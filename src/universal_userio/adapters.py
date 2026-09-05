"""Transport adapters; credentials and provider URLs stay outside the AI domain."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shlex
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from email.utils import parseaddr
from datetime import datetime
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

from .channels.core import AdapterNotSupported
from .contracts import ChannelFile, InboxMessage, ReplyDraft

if TYPE_CHECKING:
    from .service import UserIOService
    from .store import SQLiteUserIOStore


def inbox_message_from_envelope(payload: Mapping[str, Any], *, received_at: float) -> InboxMessage:
    if payload.get("schema") != "universal.inbox.message.v1":
        raise ValueError("unsupported inbox schema")
    source = str(payload.get("source") or "").strip().lower()
    message_id = str(payload.get("message_id") or "").strip()
    sender = str(payload.get("sender") or "").strip()
    body = str(payload.get("body") or "").strip()
    if source not in {"telegram", "matrix", "whatsapp", "vk", "phone", "sms", "email", "gmail"} and not source.startswith("gmail:"):
        raise ValueError("unsupported message source")
    if not message_id or not sender:
        raise ValueError("inbox message requires message_id and sender")
    sender_name = str(payload.get("sender_name") or "").strip()
    raw_attachments = payload.get("attachments")
    parsed_attachments: list[dict[str, Any]] = []
    if isinstance(raw_attachments, list):
        for idx, item in enumerate(raw_attachments):
            if not isinstance(item, Mapping):
                continue
            att = {"idx": idx}
            for key in ("kind", "content_type", "filename", "src", "attachment_id", "provider_ref"):
                if key in item and item[key] is not None:
                    att[key] = item[key]
            if "size" in item and item["size"] is not None:
                try:
                    att["size"] = int(item["size"])
                except (TypeError, ValueError):
                    pass
            parsed_attachments.append(att)
    return InboxMessage(
        source, message_id, sender, body, received_at,
        sender_name=sender_name,
        attachments=tuple(parsed_attachments),
    )


@dataclass(frozen=True, slots=True)
class NoticePlaceRoute:
    event_url: str
    token: str
    project: str
    recipient: str = "userio"
    severity: str = "notice"


class NoticePlaceOutboxClient:
    """Emit a policy-bound reply intent; the route owns every destination detail."""

    def __init__(self, routes: Mapping[str, NoticePlaceRoute], *, runner: Any = urllib.request.urlopen) -> None:
        self._routes = dict(routes)
        self._runner = runner

    def send_reply(self, *, route_id: str, conversation_id: str, draft_id: str, body: str) -> str:
        route = self._routes.get(route_id)
        if route is None:
            raise ValueError("unknown UserIO route")
        dedup = f"userio:{conversation_id}:{draft_id}"
        payload = {
            "schema": "notify.event.v1",
            "project": route.project,
            "recipient": route.recipient,
            "kind": "notification",
            "severity": route.severity,
            "title": "UserIO approved reply",
            "body": body,
            "dedup_key": dedup,
            "correlation_id": conversation_id,
            "event_type": "userio.reply.v1",
            "producer": "universal-userio",
            "plugin": "userio",
        }
        request = urllib.request.Request(
            route.event_url.rstrip("/") + "/v1/events",
            data=json.dumps(payload, ensure_ascii=False, sort_keys=True).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {route.token}",
                "Idempotency-Key": "userio-" + hashlib.sha256(dedup.encode()).hexdigest(),
            },
            method="POST",
        )
        try:
            with self._runner(request, timeout=8) as response:
                if int(response.status) != 202:
                    raise RuntimeError(f"NoticePlace returned HTTP {response.status}")
                result = json.loads(response.read())
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"NoticePlace returned HTTP {error.code}") from error
        event_id = result.get("event_id") if isinstance(result, dict) else None
        if not isinstance(event_id, str) or not event_id:
            raise RuntimeError("NoticePlace returned invalid acceptance receipt")
        return event_id

    def send_chatgpt_reply(self, *, chat_ref: str, draft_id: str, body: str) -> str:
        """Deliver an approved UserIO draft through the configured CDP MCP sidecar."""
        result = _configured_chatgpt_cdp_client().call("send_message", {
            "chatRef": chat_ref,
            "text": body,
            "confirmation": "SEND_MESSAGE",
            "idempotencyKey": draft_id,
        })
        message_ref = result.get("messageRef")
        if not isinstance(message_ref, str) or not message_ref:
            raise RuntimeError("chatgpt-cdp-mcp returned no sent-message receipt")
        return message_ref


class TelegramQrHttpOutbox:
    """Deliver approved Telegram drafts through the telegram-qr connector.

    The connector owns every Telegram session (login, live ingest, delivery),
    so auth keys are never shared between processes. ``send_reply`` posts the
    chat label and body to its ``POST /send`` endpoint.
    """

    def __init__(self, base_url: str, token: str, *, runner: Any = urllib.request.urlopen, timeout: int = 20) -> None:
        self._base_url, self._token, self._runner, self._timeout = base_url.rstrip("/"), token, runner, timeout

    def send_reply(self, *, chat: str, body: str, draft_id: str, chat_id: str = "", account_ref: str = "") -> str:
        if not chat or not body:
            raise ValueError("Telegram delivery requires chat and body")
        payload = json.dumps({"chat": chat, "chat_id": chat_id, "account_id": account_ref, "body": body}).encode()
        request = urllib.request.Request(  # noqa: S310
            f"{self._base_url}/send", data=payload, method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._token}"},
        )
        try:
            with self._runner(request, timeout=self._timeout) as response:
                data = json.loads(response.read().decode() or "{}")
        except urllib.error.HTTPError as error:
            detail = error.read().decode()[:200]
            raise RuntimeError(f"telegram-qr delivery failed: HTTP {error.code} {detail}") from error
        except (OSError, urllib.error.URLError) as error:
            raise RuntimeError("telegram-qr connector is unreachable") from error
        return f"telegram-qr:{data.get('slot', '?')}:{data.get('message_id', 'sent')}:{draft_id}"


class HimalayaGmailOutbox:
    """Send one explicitly approved Gmail reply through the configured Himalaya SMTP account."""

    def __init__(
        self, *, binary: str = "/home/roomhacker/.cargo/bin/himalaya", config: str = "/home/roomhacker/.config/himalaya/config.toml",
        runner: Any = subprocess.run,
    ) -> None:
        self._binary, self._config, self._runner = binary, config, runner

    def send_reply(self, *, account: str, sender: str, recipient: str, message_id: str, body: str, draft_id: str) -> str:
        address = parseaddr(recipient)[1]
        from_address = parseaddr(sender)[1]
        if not account or not from_address or not address or not message_id:
            raise ValueError("Gmail reply requires account, sender, recipient, and message id")
        reference = message_id.strip("<>")
        raw = f"From: {from_address}\nTo: {address}\nSubject: Re: UserIO reply\nIn-Reply-To: <{reference}>\nReferences: <{reference}>\n\n{body}\n"
        try:
            completed = self._runner(
                [self._binary, "--config", self._config, "--account", account, "message", "send"],
                input=raw, text=True, capture_output=True, timeout=30, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError("Himalaya Gmail delivery did not complete") from error
        if completed.returncode != 0:
            detail = completed.stderr.strip().splitlines()[-1] if completed.stderr else "unknown Himalaya error"
            raise RuntimeError(f"Himalaya Gmail delivery failed: {detail[:240]}")
        return f"himalaya:{account}:{draft_id}"


class AndroidSmsGatewayClient:
    """Bounded authenticated client for one Android SMS Gateway instance."""

    def __init__(self, url: str, token: str, *, runner: Any = urllib.request.urlopen) -> None:
        self._url, self._token, self._runner = url.rstrip("/"), token, runner

    def inbound(self) -> list[InboxMessage]:
        result = self._request("GET", "/v1/inbound")
        messages = result.get("messages")
        if not isinstance(messages, list):
            raise RuntimeError("Android SMS Gateway returned invalid inbound messages")
        converted: list[InboxMessage] = []
        for item in messages:
            if not isinstance(item, Mapping):
                continue
            message_id, sender, body = item.get("id"), item.get("from"), item.get("body")
            received_at = item.get("receivedAt")
            if not all(isinstance(value, str) and value.strip() for value in (message_id, sender, body, received_at)):
                continue
            try:
                timestamp = datetime.fromisoformat(received_at.replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            converted.append(InboxMessage("sms", message_id, sender, body, timestamp))
        return converted

    def send(self, *, to: str, body: str) -> str:
        result = self._request("POST", "/v1/messages", {"to": to, "body": body})
        receipt = result.get("id")
        if not isinstance(receipt, str) or not receipt:
            raise RuntimeError("Android SMS Gateway returned no accepted-message receipt")
        if result.get("status") not in {"accepted_by_android", "queued_for_device"}:
            raise RuntimeError("Android SMS Gateway did not accept the message")
        return receipt

    def _request(self, method: str, path: str, payload: Mapping[str, str] | None = None) -> dict[str, Any]:
        request = urllib.request.Request(
            self._url + path,
            data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}, method=method,
        )
        try:
            with self._runner(request, timeout=8) as response:
                status, raw = int(response.status), response.read()
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"Android SMS Gateway returned HTTP {error.code}") from error
        except urllib.error.URLError as error:
            raise AdapterNotSupported(f"Android SMS Gateway is unavailable: {error.reason}") from error
        if status not in {200, 202}:
            raise RuntimeError(f"Android SMS Gateway returned HTTP {status}")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise RuntimeError("Android SMS Gateway returned invalid JSON")
        return result


class StoredChannelAdapter:
    """User-bound wrapper over UserIO's canonical conversations and draft queue."""

    channel: str | None = None

    def __init__(self, store: SQLiteUserIOStore, service: UserIOService, user_id: str) -> None:
        self._store, self._service, self._user_id = store, service, user_id

    # Hooks subclasses override to plug a real channel adapter without
    # touching the canonical UserIO store. Tests use these to inject mock
    # transports; production code can keep the default factory.
    def _build_channel(self) -> Any:
        return None

    def list(self, *, limit: int = 100) -> list[dict[str, Any]]:
        records = self._store.conversations(
            source=self.channel, limit=max(1, min(limit, 100)), user_id=self._user_id
        )
        return [
            {
                "id": record["id"],
                "channel": _public_channel(str(record["source"])),
                "title": record["identity_id"] or record["sender"],
                "last_message_snippet": str(record["preview"] or "")[:500],
                "unread": int(record["unread_count"]),
            }
            for record in records
        ]

    def read(
        self, *, chat_id: str | None = None, message_id: str | None = None
    ) -> dict[str, Any]:
        if bool(chat_id) == bool(message_id):
            raise ValueError("provide exactly one of chat_id or message_id")
        if chat_id:
            record = self._store.conversation(chat_id, user_id=self._user_id, text_limit=65_536)
            if record is None or not self._matches(str(record["source"])):
                raise KeyError("chat not found")
            return {"chat": record}
        source = self.channel
        record = self._store.message(
            str(message_id), source=source, user_id=self._user_id, text_limit=65_536
        )
        if record is None or not self._matches(str(record["source"])):
            raise KeyError("message not found")
        return {"message": record}

    def download(self, *, file_ref: str) -> ChannelFile:
        del file_ref
        raise AdapterNotSupported("not supported by adapter")

    def send(
        self, *, chat_id: str, text: str, attachments: list[str] | None = None
    ) -> ReplyDraft:
        if attachments:
            raise AdapterNotSupported("attachments are not supported by adapter")
        record = self._store.conversation(chat_id, user_id=self._user_id)
        if record is None or not self._matches(str(record["source"])):
            raise KeyError("chat not found")
        return self._service.create_manual_draft(chat_id, body=text, user_id=self._user_id)

    def _matches(self, source: str) -> bool:
        if self.channel is None:
            return True
        if self.channel == "mail":
            return source in {"mail", "email", "gmail"} or source.startswith("gmail:")
        return source == self.channel


class MailChannelAdapter(StoredChannelAdapter):
    channel = "mail"

    def __init__(self, store: SQLiteUserIOStore, service: UserIOService, user_id: str, *, channel_factory=None) -> None:
        super().__init__(store, service, user_id)
        self._channel_factory = channel_factory

    def _build_channel(self) -> Any:
        if self._channel_factory is not None:
            return self._channel_factory()
        from .channels.email import EmailChannel
        return EmailChannel.from_env()

    def download(self, *, file_ref: str) -> ChannelFile:
        try:
            uid = int(file_ref)
        except (TypeError, ValueError) as exc:
            raise AdapterNotSupported(f"email download requires integer uid, got {file_ref!r}") from exc
        message = self._store.message(str(uid), user_id=self._user_id)
        if message is None or str(message.get("source") or "") not in {"mail", "email", "gmail"} \
                and not str(message.get("source") or "").startswith("gmail:"):
            raise KeyError(f"email message {uid} not found")
        peer = str(message.get("sender") or "").strip()
        if not peer:
            raise AdapterNotSupported("email message has no peer")
        try:
            channel = self._build_channel()
        except Exception as exc:
            raise AdapterNotSupported(f"email channel not configured: {exc}") from exc
        try:
            media = asyncio.run(channel.download_media(chat=peer, message=uid))
        except Exception as exc:
            raise AdapterNotSupported(f"email download failed for uid {uid}: {exc}") from exc
        return ChannelFile(
            filename=str(media.filename or f"attachment-{uid}"),
            content_type=str(media.mime_type or "application/octet-stream"),
            data=bytes(media.data or b""),
        )


class TelegramChannelAdapter(StoredChannelAdapter):
    channel = "telegram"

    def __init__(self, store: SQLiteUserIOStore, service: UserIOService, user_id: str, *, bridge_url: str | None = None, runner: Any = urllib.request.urlopen) -> None:
        super().__init__(store, service, user_id)
        self._bridge_url = (bridge_url or os.environ.get("USERIO_TELEGRAM_QR_URL", "")).rstrip("/")
        self._runner = runner

    def download(self, *, file_ref: str) -> ChannelFile:
        return _download_via_bridge(
            channel="telegram",
            message=self._store.message(file_ref, user_id=self._user_id),
            file_ref=file_ref,
            bridge_url=self._bridge_url,
            token_env="USERIO_API_TOKEN",
            chat_field="chat",
            chat_id_field="chat_id",
            message_field="message_id",
            runner=self._runner,
        )


class WhatsAppChannelAdapter(StoredChannelAdapter):
    channel = "whatsapp"

    def __init__(self, store: SQLiteUserIOStore, service: UserIOService, user_id: str, *, bridge_url: str | None = None, runner: Any = urllib.request.urlopen) -> None:
        super().__init__(store, service, user_id)
        self._bridge_url = (bridge_url or os.environ.get("USERIO_WHATSAPP_BRIDGE_URL", "")).rstrip("/")
        self._runner = runner

    def download(self, *, file_ref: str) -> ChannelFile:
        return _download_via_bridge(
            channel="whatsapp",
            message=self._store.message(file_ref, user_id=self._user_id),
            file_ref=file_ref,
            bridge_url=self._bridge_url,
            token_env="USERIO_API_TOKEN",
            chat_field="chat",
            chat_id_field="chat_id",
            message_field="message_id",
            runner=self._runner,
        )


class VKChannelAdapter(StoredChannelAdapter):
    channel = "vk"

    def __init__(
        self,
        store: SQLiteUserIOStore,
        service: UserIOService,
        user_id: str,
        *,
        gateway_url: str | None = None,
        runner: Any = urllib.request.urlopen,
    ) -> None:
        super().__init__(store, service, user_id)
        self._gateway_url = (gateway_url or os.environ.get("USERIO_VK_EXTENSION_GATEWAY_URL", "")).rstrip("/")
        self._runner = runner

    def list_attachments(self, *, message_id: str) -> list[dict[str, Any]]:
        return self._store.attachments_for_message(
            source="vk", message_id=message_id, user_id=self._user_id,
        )

    def download(self, *, file_ref: str) -> ChannelFile:
        # The bytes always live in the VK browser extension's IndexedDB. A
        # gateway URL bridges UserIO into that storage; without one there is
        # honestly nothing we can fetch. Surface that truth first so callers
        # don't see a misleading attachment-not-found when production simply
        # hasn't been pointed at the extension yet.
        if not self._gateway_url:
            raise AdapterNotSupported(
                "VK media flows through the browser extension; UserIO does not hold a VK API token. "
                "Set USERIO_VK_EXTENSION_GATEWAY_URL or open the attachment in the VK Inbox extension.",
            )
        record = self._store.attachment_by_id(file_ref, user_id=self._user_id)
        message_id = ""
        if record is None:
            # Fall back: caller may have passed "{message_id}:{idx}" if attachment_id
            # round-trip is broken.
            message_id, sep, idx = file_ref.partition(":")
            attachments = self._store.attachments_for_message(
                source="vk", message_id=message_id, user_id=self._user_id,
            )
            record = next(
                (a for a in attachments if str(a.get("idx")) == idx),
                None,
            )
            if record is None:
                raise AdapterNotSupported(f"vk attachment {file_ref!r} not found")
        else:
            message_id = str(record.get("message_id") or "")
        # Resolve peer_id from the stored message; the gateway keys blobs on it.
        message_row = (
            self._store.message(message_id, source="vk", user_id=self._user_id)
            if message_id else None
        )
        peer_id = ""
        if message_row is not None:
            # VK stores the actual peer (chat/user id) in `sender`; display_name
            # already lives in conversations.name.
            peer_id = str(message_row.get("sender") or "")
        payload = json.dumps({
            "peer_id": peer_id,
            "msg_id": str(record.get("message_id") or ""),
            "idx": int(record.get("idx") or 0),
            "attachment_id": record.get("attachment_id") or file_ref,
        }).encode()
        request = urllib.request.Request(
            f"{self._gateway_url}/vk/attachment",
            data=payload, method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {os.environ.get('USERIO_API_TOKEN', '')}",
            },
        )
        try:
            with self._runner(request, timeout=60) as response:
                content_type = response.headers.get("Content-Type", "") or ""
                data = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:200]
            raise AdapterNotSupported(
                f"vk extension gateway refused download: HTTP {error.code} {detail or error.reason}",
            ) from error
        except (OSError, urllib.error.URLError) as error:
            raise AdapterNotSupported(f"vk extension gateway is unreachable: {error}") from error
        if "application/json" in content_type.lower():
            try:
                payload_obj = json.loads(data.decode() or "{}")
            except json.JSONDecodeError:
                payload_obj = {}
            if isinstance(payload_obj, dict) and payload_obj.get("error"):
                raise AdapterNotSupported(
                    f"vk extension gateway returned error: {payload_obj['error']}"
                )
        return ChannelFile(
            filename=str(record.get("filename") or f"vk-{record.get('attachment_id') or file_ref}"),
            content_type=str(
                record.get("content_type") or content_type.split(";", 1)[0]
                or "application/octet-stream"
            ),
            data=bytes(data),
        )


class AndroidSmsChannelAdapter(StoredChannelAdapter):
    channel = "sms"

    def _sync(self) -> None:
        gateway = self._service.sms_gateway
        if gateway is None or self._service.sms_user_id != self._user_id:
            raise AdapterNotSupported("Android SMS adapter is not configured for this UserIO user")
        for message in gateway.inbound():
            self._service.receive(message, route_id=self._service.sms_route_id, user_id=self._user_id)

    def list(self, *, limit: int = 100) -> list[dict[str, Any]]:
        self._sync()
        return super().list(limit=limit)

    def download(self, *, file_ref: str) -> ChannelFile:
        raise AdapterNotSupported(
            "Android SMS adapter does not deliver attachments. The gateway only relays SMS bodies; "
            "if the message body is `[MMS]` it surfaces as text only and there are no bytes to fetch.",
        )


def _download_via_bridge(
    *, channel: str, message: dict[str, object] | None, file_ref: str,
    bridge_url: str, token_env: str,
    chat_field: str, chat_id_field: str, message_field: str,
    runner: Any = urllib.request.urlopen, timeout: float = 60.0,
) -> ChannelFile:
    """Single-shot HTTP round-trip to a media bridge.

    Both Telegram (USERIO_TELEGRAM_QR_URL) and WhatsApp (USERIO_WHATSAPP_BRIDGE_URL)
    expose `POST /download` with the same JSON contract. We POST `{chat, chat_id,
    message_id}` and expect the bridge to either return raw bytes with
    `Content-Disposition: attachment` or a JSON `{error: ...}`.
    """
    if message is None:
        raise AdapterNotSupported(
            f"{channel} message {file_ref!r} not found in the local store; "
            f"the bridge has nothing to download.",
        )
    if not bridge_url:
        raise AdapterNotSupported(
            f"{channel} bridge URL is not configured; set USERIO_TELEGRAM_QR_URL or "
            f"USERIO_WHATSAPP_BRIDGE_URL before downloading attachments.",
        )
    token = os.environ.get(token_env, "")
    sender = str(message.get("sender") or "").strip()
    if not sender:
        raise AdapterNotSupported(f"{channel} message {file_ref!r} has no peer")
    payload = json.dumps({
        chat_field: sender,
        chat_id_field: sender,
        message_field: str(file_ref),
    }).encode()
    request = urllib.request.Request(
        f"{bridge_url}/download",
        data=payload, method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with runner(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "") or ""
            data = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")[:200]
        raise AdapterNotSupported(
            f"{channel} bridge refused download: HTTP {error.code} {detail or error.reason}",
        ) from error
    except (OSError, urllib.error.URLError) as error:
        raise AdapterNotSupported(f"{channel} bridge is unreachable: {error}") from error
    if "application/json" in content_type.lower():
        try:
            payload_obj = json.loads(data.decode() or "{}")
        except json.JSONDecodeError:
            payload_obj = {}
        if isinstance(payload_obj, dict) and payload_obj.get("error"):
            raise AdapterNotSupported(f"{channel} bridge returned error: {payload_obj['error']}")
    filename = (
        data[:0].decode()
        or f"{channel}-{file_ref}"
    )
    return ChannelFile(
        filename=filename,
        content_type=content_type.split(";", 1)[0] or "application/octet-stream",
        data=bytes(data),
    )

    def read(self, *, chat_id: str | None = None, message_id: str | None = None) -> dict[str, Any]:
        self._sync()
        return super().read(chat_id=chat_id, message_id=message_id)


class ChatGPTCDPMcpClient:
    """Small persistent stdio client for the local chatgpt-cdp-mcp sidecar."""

    def __init__(self, command: str | None = None) -> None:
        command = command or os.environ.get("USERIO_CHATGPT_CDP_MCP_COMMAND", "")
        if not command.strip():
            raise AdapterNotSupported(
                "ChatGPT CDP adapter is not configured; set USERIO_CHATGPT_CDP_MCP_COMMAND"
            )
        self._command = shlex.split(command)
        self._process: subprocess.Popen[str] | None = None
        self._request_id = 0
        self._lock = threading.Lock()

    def call(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._start()
            result = self._request("tools/call", {"name": name, "arguments": dict(arguments)})
        content = result.get("content")
        if not isinstance(content, list) or not content or not isinstance(content[0], Mapping):
            raise RuntimeError("chatgpt-cdp-mcp returned an invalid tool result")
        text = content[0].get("text")
        if not isinstance(text, str):
            raise RuntimeError("chatgpt-cdp-mcp returned a non-text tool result")
        value = json.loads(text)
        if not isinstance(value, dict):
            raise RuntimeError("chatgpt-cdp-mcp returned a non-object tool result")
        return value

    def _start(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        try:
            self._process = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except OSError as error:
            raise AdapterNotSupported(f"could not start chatgpt-cdp-mcp: {error}") from error
        self._request(
            "initialize",
            {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "universal-userio", "version": "0.1.0"}},
        )
        assert self._process.stdin is not None
        self._process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}) + "\n")
        self._process.stdin.flush()

    def _request(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        process = self._process
        if process is None or process.stdin is None or process.stdout is None:
            raise RuntimeError("chatgpt-cdp-mcp is not running")
        self._request_id += 1
        request_id = self._request_id
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params)}) + "\n")
        process.stdin.flush()
        while True:
            line = process.stdout.readline()
            if not line:
                raise RuntimeError("chatgpt-cdp-mcp closed its stdio transport")
            response = json.loads(line)
            if response.get("id") != request_id:
                continue
            if "error" in response:
                message = response["error"].get("message", "unknown MCP error")
                raise AdapterNotSupported(f"chatgpt-cdp-mcp: {message}")
            result = response.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("chatgpt-cdp-mcp returned an invalid JSON-RPC result")
            return result


_chatgpt_cdp_client: ChatGPTCDPMcpClient | None = None
_chatgpt_cdp_client_lock = threading.Lock()


def _configured_chatgpt_cdp_client() -> ChatGPTCDPMcpClient:
    """Keep opaque refs valid across independent UserIO MCP requests."""
    global _chatgpt_cdp_client
    with _chatgpt_cdp_client_lock:
        if _chatgpt_cdp_client is None:
            _chatgpt_cdp_client = ChatGPTCDPMcpClient()
        return _chatgpt_cdp_client


class ChatGPTCDPChannelAdapter:
    """Read page-visible ChatGPT chats via one explicitly configured CDP MCP sidecar."""

    channel = "chatgpt"

    def __init__(
        self, store: SQLiteUserIOStore, service: UserIOService, user_id: str,
        *, client: ChatGPTCDPMcpClient | Any | None = None,
    ) -> None:
        self._store, self._service, self._user_id = store, service, user_id
        self._client = client or _configured_chatgpt_cdp_client()

    def list(self, *, limit: int = 100) -> list[dict[str, Any]]:
        result = self._client.call("list_chats", {"view": "recent", "limit": max(1, min(limit, 100))})
        chats = result.get("chats")
        if not isinstance(chats, list):
            raise RuntimeError("chatgpt-cdp-mcp returned chats in an invalid format")
        return [self._chat_summary(chat) for chat in chats if isinstance(chat, Mapping)]

    def read(self, *, chat_id: str | None = None, message_id: str | None = None) -> dict[str, Any]:
        if bool(chat_id) == bool(message_id):
            raise ValueError("provide exactly one of chat_id or message_id")
        if message_id:
            raise AdapterNotSupported("ChatGPT CDP adapter reads chats, not individual messages")
        result = self._client.call("export_chat", {"chatRef": chat_id, "format": "json"})
        content = result.get("content")
        if not isinstance(content, str):
            raise RuntimeError("chatgpt-cdp-mcp returned an export without content")
        chat = json.loads(content)
        if not isinstance(chat, dict):
            raise RuntimeError("chatgpt-cdp-mcp returned an invalid chat export")
        return {"chat": chat}

    def download(self, *, file_ref: str) -> ChannelFile:
        del file_ref
        raise AdapterNotSupported("not supported by adapter")

    def send(self, *, chat_id: str, text: str, attachments: list[str] | None = None) -> ReplyDraft:
        if attachments:
            raise AdapterNotSupported("attachments are not supported by adapter")
        exported = self.read(chat_id=chat_id)["chat"]
        messages = exported.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ValueError("ChatGPT chat has no message to anchor a UserIO draft")
        latest = messages[-1]
        if not isinstance(latest, Mapping):
            raise RuntimeError("chatgpt-cdp-mcp returned an invalid message export")
        body = str(latest.get("text") or latest.get("body") or "").strip()
        message_ref = str(latest.get("messageRef") or latest.get("id") or "").strip()
        if not body or not message_ref:
            raise RuntimeError("chatgpt-cdp-mcp returned a message without text or reference")
        conversation_id, _ = self._service.receive(
            InboxMessage("chatgpt", message_ref, chat_id, body, time.time()),
            route_id="chatgpt", user_id=self._user_id,
        )
        return self._service.create_manual_draft(conversation_id, body=text, user_id=self._user_id)

    @classmethod
    def _chat_summary(cls, chat: Mapping[str, Any]) -> dict[str, Any]:
        chat_ref = chat.get("chatRef")
        if not isinstance(chat_ref, str) or not chat_ref:
            raise RuntimeError("chatgpt-cdp-mcp returned a chat without chatRef")
        return {
            "id": chat_ref,
            "channel": cls.channel,
            "title": str(chat.get("title") or "ChatGPT"),
            "last_message_snippet": str(chat.get("preview") or "")[:500],
            "unread": bool(chat.get("unread", False)),
        }


class ChatGPTWebChannelAdapter:
    """Read ChatGPT chats headlessly from a stored session cookie.

    The ``__Secure-next-auth.session-token`` cookie (lives for months, renewed
    whenever the user is active in a browser) is exchanged for a ~10-day
    ``accessToken`` at ``/api/auth/session``, and that token drives the
    ``backend-api`` chat endpoints. Requires ``curl_cffi`` for the Chrome TLS
    fingerprint: Cloudflare rejects plain urllib clients.
    """

    channel = "chatgpt"
    SESSION_ENV = "USERIO_CHATGPT_SESSION_FILE"

    def __init__(
        self, store: SQLiteUserIOStore, service: UserIOService, user_id: str,
        *, session_file: str | None = None, client_factory: Any | None = None,
    ) -> None:
        self._store, self._service, self._user_id = store, service, user_id
        self._session_file = session_file or os.environ.get(self.SESSION_ENV, "")
        self._client_factory = client_factory
        self._access_token: str | None = None
        self._expires: float = 0.0

    @classmethod
    def configured(cls) -> bool:
        return bool(os.environ.get(cls.SESSION_ENV, "").strip())

    def _client(self) -> Any:
        path = self._session_file.strip()
        if not path:
            raise AdapterNotSupported(
                f"ChatGPT web adapter is not configured; set {self.SESSION_ENV}"
            )
        try:
            state = json.loads(open(path, encoding="utf-8").read())
        except FileNotFoundError as error:
            raise AdapterNotSupported(f"ChatGPT session file is missing: {path}") from error
        except json.JSONDecodeError as error:
            raise AdapterNotSupported(f"ChatGPT session file is not valid JSON: {path}") from error
        session_token = str(state.get("session_token") or state.get("sessionToken") or "").strip()
        if not session_token:
            raise AdapterNotSupported(f"ChatGPT session file has no session_token: {path}")
        try:
            from curl_cffi import requests as cffi_requests
        except ImportError as error:
            raise AdapterNotSupported(
                "curl-cffi is required for the ChatGPT web adapter; "
                "install with: pip install 'universal-userio[chatgpt]'"
            ) from error
        client = cffi_requests.Session(impersonate="chrome", timeout=30)
        client.cookies.set("__Secure-next-auth.session-token", session_token, domain=".chatgpt.com")
        return client

    def _request(self, url: str) -> dict[str, Any]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            **({"Authorization": f"Bearer {self._access_token}"} if self._access_token else {}),
        }
        response = None
        last_error: Exception | None = None
        for _ in range(3):  # chatgpt.com occasionally closes connections mid-transfer
            client = self._client_factory() if self._client_factory else self._client()
            try:
                response = client.get(url, headers=headers)
                break
            except Exception as error:  # curl_cffi raises its own hierarchy
                last_error = error
                time.sleep(1)
        if response is None:
            raise RuntimeError(f"ChatGPT transport failed: {last_error}")
        if response.status_code in (401, 403) and self._access_token:
            raise RuntimeError("ChatGPT rejected the access token; refresh the session cookie")
        if response.status_code != 200:
            raise RuntimeError(f"ChatGPT returned HTTP {response.status_code} for {url.split('?')[0]}")
        return json.loads(response.text)

    def _renew(self) -> None:
        session = self._request("https://chatgpt.com/api/auth/session")
        token = str(session.get("accessToken") or "")
        if not token:
            raise RuntimeError("ChatGPT session cookie was rejected: no accessToken in /api/auth/session")
        self._access_token, self._expires = token, time.time() + 600

    def _bearer(self) -> str:
        if not self._access_token or time.time() >= self._expires:
            self._renew()
        return self._access_token  # type: ignore[return-value]

    def list(self, *, limit: int = 100) -> list[dict[str, Any]]:
        self._bearer()
        query = urllib.parse.urlencode({
            "offset": 0, "limit": max(1, min(limit, 100)), "order": "updated", "is_archived": "false",
        })
        result = self._request(f"https://chatgpt.com/backend-api/conversations?{query}")
        items = result.get("items")
        if not isinstance(items, list):
            raise RuntimeError("ChatGPT returned conversations in an invalid format")
        return [self._chat_summary(chat) for chat in items if isinstance(chat, Mapping)]

    def read(self, *, chat_id: str | None = None, message_id: str | None = None) -> dict[str, Any]:
        if bool(chat_id) == bool(message_id):
            raise ValueError("provide exactly one of chat_id or message_id")
        if message_id:
            raise AdapterNotSupported("ChatGPT web adapter reads chats, not individual messages")
        self._bearer()
        chat = self._request(f"https://chatgpt.com/backend-api/conversation/{urllib.parse.quote(chat_id)}")
        if not isinstance(chat, dict):
            raise RuntimeError("ChatGPT returned an invalid conversation export")
        return {"chat": self._conversation(chat_id or "", chat)}

    @classmethod
    def _conversation(cls, chat_id: str, chat: Mapping[str, Any]) -> dict[str, Any]:
        mapping = chat.get("mapping")
        messages: list[dict[str, Any]] = []
        if isinstance(mapping, dict):
            for node in mapping.values():
                if not isinstance(node, Mapping):
                    continue
                message = node.get("message")
                if not isinstance(message, Mapping):
                    continue
                role = str((message.get("author") or {}).get("role") or "")
                if role == "system":
                    continue
                created = float(message.get("create_time") or 0)
                parts = (message.get("content") or {}).get("parts") or []
                text = " ".join(
                    part if isinstance(part, str) else f"[{part.get('content_type')}]" for part in parts
                ).strip()
                if text:
                    messages.append({"role": role, "text": text[:8000], "created_at": created})
        messages.sort(key=lambda m: m["created_at"])
        return {
            "id": chat_id or str(chat.get("conversation_id") or ""),
            "title": str(chat.get("title") or "ChatGPT"),
            "messages": messages,
        }

    def download(self, *, file_ref: str) -> ChannelFile:
        del file_ref
        raise AdapterNotSupported("not supported by adapter")

    def send(self, *, chat_id: str, text: str, attachments: list[str] | None = None) -> ReplyDraft:
        if attachments:
            raise AdapterNotSupported("attachments are not supported by adapter")
        chat = self.read(chat_id=chat_id)["chat"]
        messages = chat.get("messages") or []
        if not messages:
            raise ValueError("ChatGPT chat has no message to anchor a UserIO draft")
        latest = messages[-1]
        message_ref = f"{chat_id}:{latest.get('created_at', 0)}:{len(messages)}"
        conversation_id, _ = self._service.receive(
            InboxMessage("chatgpt", message_ref, chat_id, str(latest.get("text", ""))[:8000], time.time()),
            route_id="chatgpt", user_id=self._user_id,
        )
        return self._service.create_manual_draft(conversation_id, body=text, user_id=self._user_id)

    @classmethod
    def _chat_summary(cls, chat: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "id": str(chat.get("id") or ""),
            "channel": cls.channel,
            "title": str(chat.get("title") or "ChatGPT"),
            "last_message_snippet": "",
            "unread": 0,
        }


# Provider-facing compatibility names; all expose the same four-method contract.
GmailChannelAdapter = MailChannelAdapter


class UnifiedChannels(StoredChannelAdapter):
    """Resolve all current provider wrappers behind one four-method interface."""

    _types = {
        "mail": MailChannelAdapter,
        "gmail": MailChannelAdapter,
        "email": MailChannelAdapter,
        "telegram": TelegramChannelAdapter,
        "whatsapp": WhatsAppChannelAdapter,
        "vk": VKChannelAdapter,
        "sms": AndroidSmsChannelAdapter,
        "chatgpt": ChatGPTCDPChannelAdapter,
    }

    def adapter(self, channel: str | None) -> StoredChannelAdapter:
        if not channel:
            return self
        name = channel.strip().lower()
        if name == "chatgpt" and ChatGPTWebChannelAdapter.configured():
            return ChatGPTWebChannelAdapter(self._store, self._service, self._user_id)
        adapter_type = self._types.get(name)
        if adapter_type is None:
            raise ValueError("unknown channel")
        return adapter_type(self._store, self._service, self._user_id)

    def download(self, *, file_ref: str) -> ChannelFile:
        channel, separator, provider_ref = file_ref.partition(":")
        if separator and channel in self._types:
            return self.adapter(channel).download(file_ref=provider_ref)
        raise AdapterNotSupported("not supported by adapter")


def _public_channel(source: str) -> str:
    return "mail" if source in {"mail", "email", "gmail"} or source.startswith("gmail:") else source
