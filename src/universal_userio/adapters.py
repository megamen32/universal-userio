"""Transport adapters; credentials and provider URLs stay outside the AI domain."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shlex
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from email.utils import parseaddr
from pathlib import Path
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

from . import chatgpt_sessions
from .channels.core import AdapterNotSupported
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)
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
    if (
        source not in {"telegram", "matrix", "whatsapp", "vk", "phone", "sms", "email", "gmail", "chatgpt"}
        and not source.startswith("gmail:")
        and not source.startswith("chatgpt:")
    ):
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
            for key in (
                "kind", "content_type", "filename", "src", "attachment_id", "provider_ref",
                "transcript", "transcription_status", "transcription_model",
            ):
                if key in item and item[key] is not None:
                    att[key] = item[key]
            if "size" in item and item["size"] is not None:
                try:
                    att["size"] = int(item["size"])
                except (TypeError, ValueError):
                    pass
            parsed_attachments.append(att)

    # Audio transcription is an ingress enrichment, not an agent tool.  The
    # provider adapter preserves the original media attachment and puts the STT
    # result beside it.  Promote that text into the canonical message body so
    # every consumer (UI, MCP, agents, search) sees speech as normal text.
    transcripts = [
        str(att.get("transcript") or "").strip()
        for att in parsed_attachments
        if str(att.get("kind") or "").lower() in {"audio", "voice"}
        and str(att.get("transcript") or "").strip()
    ]
    if transcripts:
        transcript = "\n\n".join(dict.fromkeys(transcripts))
        placeholder = bool(re.fullmatch(
            r"\[\s*(?:(?:Telegram|WhatsApp)\s+)?(?:audio|voice|аудио|голосовое)\s*\]",
            body, flags=re.IGNORECASE,
        ))
        if not body or placeholder:
            body = transcript[:65_536]
        elif transcript.startswith(body):
            # Telegram ingress historically caps the visible body at 8k. If it
            # already contains the beginning of a long transcript, replace the
            # prefix with the canonical transcript instead of duplicating it.
            body = transcript[:65_536]
        elif "\n\n" in body and transcript.startswith(body.split("\n\n", 1)[1]):
            caption = body.split("\n\n", 1)[0]
            body = (caption + "\n\n[Транскрипция]\n" + transcript)[:65_536]
        elif transcript not in body:
            body = (body + "\n\n[Транскрипция]\n" + transcript)[:65_536]

    return InboxMessage(
        source, message_id, sender, body, received_at,
        sender_name=sender_name,
        attachments=tuple(parsed_attachments),
    )


class DirectProviderOutbox:
    """Production fallback: only provider-owned transports may deliver human replies.

    ChatGPT is a direct provider sidecar; unsupported providers fail closed instead
    of routing ordinary conversation traffic through NoticePlace.
    """
    def send_reply(self, *, route_id: str, conversation_id: str, draft_id: str, body: str) -> str:
        raise RuntimeError(f"no direct provider outbox for route {route_id!r}")

    def send_chatgpt_reply(self, *, chat_ref: str, draft_id: str, body: str) -> str:
        result = _configured_chatgpt_cdp_client().call("send_message", {
            "chatRef": chat_ref, "text": body, "confirmation": "SEND_MESSAGE", "idempotencyKey": draft_id,
        })
        message_ref = result.get("messageRef")
        if not isinstance(message_ref, str) or not message_ref:
            raise RuntimeError("chatgpt-cdp-mcp returned no sent-message receipt")
        return message_ref


class ByokBridgeGenerator:
    """Draft generator that runs a user's own endpoint through the byok-bridge.

    The bridge wraps @bezrabotnyi/byok (SSRF-safe: HTTPS-only, public IPs),
    so UserIO never calls arbitrary user URLs itself.
    """

    def __init__(self, *, bridge_url: str, bridge_token: str, endpoint: str, model: str, api_key: str,
                 runner: Any = urllib.request.urlopen, timeout: int = 90) -> None:
        self._bridge_url, self._bridge_token = bridge_url.rstrip("/"), bridge_token
        self._endpoint, self._model, self._api_key = endpoint, model, api_key
        self._runner, self._timeout = runner, timeout

    def suggest_with_context(self, *, conversation_id: str, latest_message: InboxMessage,
                             history: Sequence[dict[str, object]], limit: int) -> list[str]:
        transcript = "\n".join(f"{entry.get('sender', 'contact')}: {entry.get('body', '')}" for entry in history[-20:])
        prompt = (
            "You write concise reply drafts for a human operator. Return only the proposed reply text. "
            f"Conversation {conversation_id}; source={latest_message.source}.\nHistory:\n{transcript}"
        )
        drafts: list[str] = []
        for _ in range(max(1, limit)):
            text = self._chat(prompt)
            if text and text not in drafts:
                drafts.append(text)
            if len(drafts) >= limit:
                break
        return drafts

    def _chat(self, prompt: str) -> str:
        payload = {
            "base_url": self._endpoint, "api_key": self._api_key, "model_id": self._model,
            "prompt": prompt, "max_output_tokens": 2000,
        }
        request = urllib.request.Request(  # noqa: S310
            self._bridge_url + "/chat",
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._bridge_token}"},
            method="POST",
        )
        try:
            with self._runner(request, timeout=self._timeout) as response:
                if int(response.status) != 200:
                    raise RuntimeError(f"BYOK bridge returned HTTP {response.status}")
                data = json.loads(response.read())
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:200]
            raise RuntimeError(f"BYOK bridge returned HTTP {error.code}: {detail}") from error
        except (OSError, urllib.error.URLError) as error:
            raise RuntimeError("BYOK bridge is unreachable") from error
        return _THINK_BLOCK.sub("", str(data.get("text", ""))).strip()


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


class MatrixChannelAdapter(StoredChannelAdapter):
    """Canonical Matrix conversations. Ingress is handled by MatrixReader; writes are draft-only here."""

    channel = "matrix"


class VKChannelAdapter(StoredChannelAdapter):
    channel = "vk"

    def __init__(self, store: SQLiteUserIOStore, service: UserIOService, user_id: str) -> None:
        super().__init__(store, service, user_id)

    def list_attachments(self, *, message_id: str) -> list[dict[str, Any]]:
        return self._store.attachments_for_message(
            source="vk", message_id=message_id, user_id=self._user_id,
        )

    def download(self, *, file_ref: str) -> ChannelFile:
        # The bytes always live in the VK browser extension's IndexedDB. The
        # command channel asks every live extension agent for the blob (the
        # profile that captured the message has it) and streams the winner back.
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
                raise AdapterNotSupported(
                    f"vk attachment {file_ref!r} not found in the local store; "
                    "VK bytes live in the browser extension's IndexedDB, and this "
                    "message was never captured",
                )
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
        locator = {
            "peer_id": peer_id,
            "msg_id": str(record.get("message_id") or ""),
            "idx": int(record.get("idx") or 0),
        }
        result = self._fetch_via_agents(locator)
        data = base64.b64decode(result.get("bytes_base64") or "")
        if not data:
            raise AdapterNotSupported(
                f"vk agent {result.get('agent')!r} returned empty bytes for {file_ref!r}",
            )
        return ChannelFile(
            filename=str(
                record.get("filename") or result.get("filename")
                or f"vk-{record.get('attachment_id') or file_ref}",
            ),
            content_type=str(
                record.get("content_type") or result.get("content_type")
                or "application/octet-stream",
            ),
            data=data,
        )

    def _fetch_via_agents(self, locator: dict[str, Any]) -> dict[str, Any]:
        """Ask every live extension agent for the blob; the capturing profile wins."""
        from . import agent_channel

        username = self._service._store.owner().username
        try:
            live = [a["agent_id"] for a in agent_channel.status(user=username).get("agents", [])]
        except Exception as error:
            raise AdapterNotSupported(f"vk agent channel unavailable: {error}") from error
        if not live:
            raise AdapterNotSupported(
                "no VK extension agent is online; open the browser whose extension "
                "captured this message and try again",
            )
        errors: list[str] = []
        for agent_id in live:
            try:
                result = agent_channel.call(
                    agent_id, "vk_attachment_bytes", locator, user=username, timeout_sec=45.0,
                )
            except RuntimeError as error:
                errors.append(f"{agent_id}: {error}")
                continue
            result["agent"] = agent_id
            return result
        raise AdapterNotSupported(
            f"no online VK agent holds attachment {locator['peer_id']}:{locator['msg_id']}"
            f"#{locator['idx']}; tried: {'; '.join(errors)}",
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
            filename_header = response.headers.get("X-Filename", "") or ""
            disposition = response.headers.get("Content-Disposition", "") or ""
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
    filename = str(filename_header).strip()
    if not filename and disposition:
        match = re.search(r'filename="?([^";]+)', disposition, flags=re.IGNORECASE)
        if match:
            filename = match.group(1).strip()
    if not filename:
        filename = f"{channel}-{file_ref}"
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


class ChatGPTWebOutbox:
    """Deliver approved replies into ChatGPT via backend-api (server-side).

    Uses the same per-account session store as the web adapter: the session
    cookie is exchanged for an access token, then POST /backend-api/conversation
    streams the assistant response. Works without the user's browser being
    online; Cloudflare is satisfied by the Chrome TLS impersonation.
    """

    def send_reply(self, *, chat_ref: str, draft_id: str, body: str, account_ref: str = "",
                   user: str, agent_fallback: Any | None = None) -> str:
        try:
            return self._send_web(chat_ref=chat_ref, draft_id=draft_id, body=body, account_ref=account_ref, user=user)
        except RuntimeError as error:
            if agent_fallback is None or "403" not in str(error):
                raise
            accounts = _chatgpt_accounts(user)
            slug = account_ref if account_ref in accounts else next(
                (s for s in accounts if (_chatgpt_records.get((user, s)) or {}).get("agent_id")),
                next(iter(accounts)),
            )
            record = _chatgpt_records.get((user, slug)) or {}
            agent_id = str(record.get("agent_id") or "")
            if not agent_id:
                raise RuntimeError(f"account {slug!r} has no browser agent registered for delivery") from error
            result = agent_fallback(agent_id=agent_id, text=body, chat_ref=chat_ref)
            return str(result.get("assistant_id") or f"chatgpt-agent-sent:{draft_id}")

    def _send_web(self, *, chat_ref: str, draft_id: str, body: str, account_ref: str = "", user: str) -> str:
        _chatgpt_require_configured(user)
        accounts = _chatgpt_accounts(user)
        if account_ref in accounts:
            slug = account_ref
        else:
            # Unknown chat refs (fresh UserIO conversations) ride the first
            # real account; _send tolerates the stale ref and starts anew.
            slug = next(iter(accounts))
        parent = str(uuid.uuid4())
        conversation_payload: dict[str, Any] = {
            "action": "next",
            "messages": [{
                "id": str(uuid.uuid4()),
                "author": {"role": "user"},
                "content": {"content_type": "text", "parts": [body]},
            }],
            "model": "auto",
            "parent_message_id": parent,
        }
        if chat_ref:
            try:
                chat = _chatgpt_conversation(user, slug, chat_ref)
                last = _chatgpt_last_message(chat.get("mapping") or {})
                if last:
                    conversation_payload["parent_message_id"] = str(last.get("node_id") or parent)
                conversation_payload["conversation_id"] = chat_ref
            except (RuntimeError, KeyError):
                pass  # unknown/stale chat ref: start a fresh conversation
        stream = _chatgpt_raw(user, slug, "https://chatgpt.com/backend-api/conversation", bearer_needed=True, json_body=conversation_payload)
        assistant_id = ""
        for line in stream.splitlines():
            line = line.strip()
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            try:
                event = json.loads(line[len("data: "):])
            except json.JSONDecodeError:
                continue
            message = event.get("message") if isinstance(event, dict) else None
            if not isinstance(message, Mapping):
                continue
            if (message.get("author") or {}).get("role") == "assistant":
                assistant_id = str(message.get("id") or assistant_id)
        return assistant_id or f"chatgpt-sent:{draft_id}"


class ChatGPTWebChannelAdapter:
    """Read ChatGPT chats headlessly for every registered account.

    Account sessions (``__Secure-next-auth.session-token`` cookies exported by
    the browser extension) live in the chatgpt_sessions store; the env var
    USERIO_CHATGPT_SESSION_FILE keeps working as a legacy single account. The
    session cookie is exchanged for a ~10-day ``accessToken`` at
    ``/api/auth/session``; that token drives ``backend-api``. Requires
    ``curl_cffi`` for the Chrome TLS fingerprint: Cloudflare rejects plain
    urllib clients.
    """

    channel = "chatgpt"
    SESSION_ENV = "USERIO_CHATGPT_SESSION_FILE"

    def __init__(
        self, store: SQLiteUserIOStore, service: UserIOService, user_id: str,
        *, session_file: str | None = None, client_factory: Any | None = None,
    ) -> None:
        self._store, self._service, self._user_id = store, service, user_id
        principal = store.user(user_id)
        if principal is None:
            raise ValueError(f"unknown UserIO user: {user_id}")
        self._username = principal.username
        self._session_file = session_file or os.environ.get(self.SESSION_ENV, "")
        self._client_factory = client_factory
        if client_factory is not None:
            _chatgpt_client_factories[self._username] = client_factory
            for key in [key for key in _chatgpt_clients if key[0] == self._username]:
                _chatgpt_clients.pop(key, None)
                _chatgpt_client_fingerprints.pop(key, None)
                _chatgpt_tokens.pop(key, None)

    @classmethod
    def configured(cls) -> bool:
        return bool(os.environ.get(cls.SESSION_ENV, "").strip()) or any(chatgpt_sessions.SESSION_DIR.glob("*/*.json"))

    def list(self, *, limit: int = 100) -> list[dict[str, Any]]:
        _chatgpt_require_configured(self._username)
        limit = max(1, min(limit, 100))
        chats: list[dict[str, Any]] = []
        for slug in _chatgpt_accounts(self._username):
            query = urllib.parse.urlencode({
                "offset": 0, "limit": limit, "order": "updated", "is_archived": "false",
            })
            result = _chatgpt_request(self._username, slug, f"https://chatgpt.com/backend-api/conversations?{query}")
            items = result.get("items")
            if not isinstance(items, list):
                raise RuntimeError("ChatGPT returned conversations in an invalid format")
            for chat in items:
                if isinstance(chat, Mapping):
                    summary = self._chat_summary(chat)
                    summary["account"] = slug
                    _chatgpt_chat_accounts[(self._username, summary["id"])] = slug
                    chats.append(summary)
        return chats[:limit]

    def read(self, *, chat_id: str | None = None, message_id: str | None = None) -> dict[str, Any]:
        if bool(chat_id) == bool(message_id):
            raise ValueError("provide exactly one of chat_id or message_id")
        if message_id:
            raise AdapterNotSupported("ChatGPT web adapter reads chats, not individual messages")
        _chatgpt_require_configured(self._username)
        slug = _chatgpt_resolve_account(self._username, chat_id or "")
        return {"chat": _chatgpt_conversation(self._username, slug, chat_id or ""), "account": slug}

    def download(self, *, file_ref: str) -> ChannelFile:
        del file_ref
        raise AdapterNotSupported("not supported by adapter")

    def send(self, *, chat_id: str, text: str, attachments: list[str] | None = None) -> ReplyDraft:
        if attachments:
            raise AdapterNotSupported("attachments are not supported by adapter")
        slug = _chatgpt_resolve_account(self._username, chat_id)
        chat = _chatgpt_conversation(self._username, slug, chat_id)
        messages = chat.get("messages") or []
        if not messages:
            raise ValueError("ChatGPT chat has no message to anchor a UserIO draft")
        latest = messages[-1]
        message_ref = f"{chat_id}:{latest.get('created_at', 0)}:{len(messages)}"
        conversation_id, _ = self._service.receive(
            InboxMessage("chatgpt", message_ref, chat_id, str(latest.get("text", ""))[:8000], time.time()),
            route_id="chatgpt", user_id=self._user_id,
        )
        self._store.set_conversation_account(conversation_id, slug, user_id=self._user_id)
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


# --- shared multi-account plumbing (adapter + outbox) ------------------------

def _chatgpt_require_configured(user: str, soft: bool = False) -> bool:
    if _chatgpt_accounts(user):
        return True
    if soft:
        return False
    raise AdapterNotSupported(
        "ChatGPT web adapter is not configured: no account sessions. "
        "Register one via the browser extension (gpt_register) or set "
        f"{ChatGPTWebChannelAdapter.SESSION_ENV}"
    )


def _chatgpt_user_agent(user: str, slug: str) -> str:
    """Exported browser UA when available: cf_clearance is UA-bound."""
    record = _chatgpt_records.get((user, slug))
    return str(record.get("user_agent") or "") if record else ""


def _chatgpt_accounts(user: str) -> dict[str, list[dict]]:
    """slug -> ordered session cookie chunks; env legacy account first."""
    accounts: dict[str, list[dict]] = {}
    legacy = os.environ.get(ChatGPTWebChannelAdapter.SESSION_ENV, "").strip()
    if legacy:
        try:
            state = json.loads(Path(legacy).read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise AdapterNotSupported(f"ChatGPT session file is missing: {legacy}") from error
        except json.JSONDecodeError as error:
            raise AdapterNotSupported(f"ChatGPT session file is not valid JSON: {legacy}") from error
        except OSError as error:
            raise AdapterNotSupported(f"ChatGPT session file cannot be read: {legacy}") from error
        token = str(state.get("session_token") or state.get("sessionToken") or "").strip()
        if token:
            accounts["default"] = [
                {"name": "__Secure-next-auth.session-token", "value": token},
            ]
    for record in chatgpt_sessions.list_sessions(user=user):
        try:
            full = chatgpt_sessions.load_session(record["slug"], user=user)
            _chatgpt_records[(user, record["slug"])] = full
            accounts[record["slug"]] = full["cookie_chunks"]
        except (KeyError, RuntimeError):
            continue
    return accounts


def _chatgpt_client(user: str, slug: str) -> Any:
    key = (user, slug)
    chunks = _chatgpt_accounts(user)[slug]
    fingerprint = tuple((str(c.get("name") or ""), str(c.get("value") or "")) for c in chunks)
    client = _chatgpt_clients.get(key)
    if client is not None and _chatgpt_client_fingerprints.get(key) == fingerprint:
        return client
    if client is not None:
        _chatgpt_clients.pop(key, None)
        _chatgpt_tokens.pop(key, None)
    factory = _chatgpt_client_factories.get(user)
    if factory is not None:
        client = factory() if callable(factory) else factory
    else:
        try:
            from curl_cffi import requests as cffi_requests
        except ImportError as error:
            raise AdapterNotSupported(
                "curl-cffi is required for the ChatGPT web adapter; "
                "install with: pip install 'universal-userio[chatgpt]'"
            ) from error
        client = cffi_requests.Session(impersonate="chrome", timeout=60)
    for chunk in chunks:
        client.cookies.set(chunk["name"], chunk["value"], domain=".chatgpt.com")
    _chatgpt_clients[key] = client
    _chatgpt_client_fingerprints[key] = fingerprint
    return client


def _chatgpt_bearer(user: str, slug: str) -> str:
    key = (user, slug)
    token, expires = _chatgpt_tokens.get(key, ("", 0.0))
    if token and time.time() < expires:
        return token
    session = _chatgpt_request(user, slug, "https://chatgpt.com/api/auth/session", bearer_needed=False)
    token = str(session.get("accessToken") or "")
    if not token:
        raise RuntimeError(
            f"ChatGPT session cookie rejected for account {slug!r}: no accessToken; re-register the session"
        )
    _chatgpt_tokens[key] = (token, time.time() + 600)
    return token


def _chatgpt_request(
    user: str, slug: str, url: str, *, bearer_needed: bool = True, json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    text = _chatgpt_raw(user, slug, url, bearer_needed=bearer_needed, json_body=json_body)
    return json.loads(text)


def _chatgpt_raw(
    user: str, slug: str, url: str, *, bearer_needed: bool = True, json_body: dict[str, Any] | None = None,
) -> str:
    ua = _chatgpt_user_agent(user, slug) or (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    )
    headers = {"User-Agent": ua, "Accept": "application/json", "OAI-Language": "en-US"}
    for chunk in _chatgpt_accounts(user).get(slug, []):
        if chunk["name"] == "oai-did":
            headers["OAI-Device-Id"] = chunk["value"]
    if bearer_needed:
        headers["Authorization"] = f"Bearer {_chatgpt_bearer(user, slug)}"
    if json_body is not None:
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "text/event-stream"
    response = None
    last_error: Exception | None = None
    for _ in range(3):  # chatgpt.com occasionally closes connections mid-transfer
        client = _chatgpt_client(user, slug)
        try:
            response = client.post(url, headers=headers, json=json_body) if json_body is not None \
                else client.get(url, headers=headers)
            break
        except Exception as error:  # curl_cffi raises its own hierarchy
            last_error = error
            time.sleep(1)
    if response is None:
        raise RuntimeError(f"ChatGPT transport failed: {last_error}")
    if response.status_code in (401, 403):
        _chatgpt_tokens.pop((user, slug), None)
        raise RuntimeError(
            f"ChatGPT rejected the request for account {slug!r} "
            f"(HTTP {response.status_code}); the session cookie likely expired"
        )
    if response.status_code != 200:
        raise RuntimeError(f"ChatGPT returned HTTP {response.status_code} for {url.split('?')[0]}")
    return response.text


def _chatgpt_conversation(user: str, slug: str, chat_id: str) -> dict[str, Any]:
    raw = _chatgpt_request(user, slug, f"https://chatgpt.com/backend-api/conversation/{urllib.parse.quote(chat_id)}")
    if not isinstance(raw, dict):
        raise RuntimeError("ChatGPT returned an invalid conversation export")
    mapping = raw.get("mapping")
    messages: list[dict[str, Any]] = []
    if isinstance(mapping, dict):
        for node_id, node in mapping.items():
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
                messages.append({
                    "role": role, "text": text[:8000], "created_at": created, "node_id": str(node_id),
                })
    messages.sort(key=lambda m: m["created_at"])
    return {
        "id": chat_id or str(raw.get("conversation_id") or ""),
        "title": str(raw.get("title") or "ChatGPT"),
        "messages": messages,
        "mapping": mapping if isinstance(mapping, dict) else {},
    }


def _chatgpt_last_message(mapping: Mapping[str, Any]) -> dict[str, Any]:
    """Latest non-system node with a node id, for parent_message_id anchoring."""
    best: dict[str, Any] = {}
    best_created = -1.0
    for node_id, node in mapping.items():
        if not isinstance(node, Mapping):
            continue
        message = node.get("message")
        if not isinstance(message, Mapping):
            continue
        role = str((message.get("author") or {}).get("role") or "")
        if role == "system":
            continue
        created = float(message.get("create_time") or 0)
        if created >= best_created:
            best_created = created
            best = {"node_id": str(node_id), "role": role, "created_at": created}
    return best


def _chatgpt_resolve_account(user: str, chat_id: str) -> str:
    """Pick the account holding a chat: cache first, then probe each account."""
    cached = _chatgpt_chat_accounts.get((user, chat_id))
    if cached and cached in _chatgpt_accounts(user):
        return cached
    errors: list[str] = []
    for slug in _chatgpt_accounts(user):
        try:
            _chatgpt_request(user, slug, f"https://chatgpt.com/backend-api/conversation/{urllib.parse.quote(chat_id)}")
            _chatgpt_chat_accounts[(user, chat_id)] = slug
            return slug
        except RuntimeError as error:
            errors.append(f"{slug}: {error}")
    raise KeyError(f"no ChatGPT account holds chat {chat_id!r}; tried: {'; '.join(errors)}")


_chatgpt_clients: dict[tuple[str, str], Any] = {}
_chatgpt_client_fingerprints: dict[tuple[str, str], tuple[tuple[str, str], ...]] = {}
_chatgpt_client_factories: dict[str, Any] = {}
_chatgpt_records: dict[tuple[str, str], dict] = {}
_chatgpt_tokens: dict[tuple[str, str], tuple[str, float]] = {}
_chatgpt_chat_accounts: dict[tuple[str, str], str] = {}

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
        "matrix": MatrixChannelAdapter,
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
