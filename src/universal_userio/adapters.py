"""Transport adapters; credentials and provider URLs stay outside the AI domain."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import threading
import time
import urllib.error
import urllib.request
from email.utils import parseaddr
from datetime import datetime
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

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
    if not message_id or not sender or not body:
        raise ValueError("inbox message requires message_id, sender and body")
    return InboxMessage(source, message_id, sender, body, received_at)


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


class HimalayaGmailOutbox:
    """Send one explicitly approved Gmail reply through the configured Himalaya SMTP account."""

    def __init__(self, *, binary: str = "himalaya", runner: Any = subprocess.run) -> None:
        self._binary, self._runner = binary, runner

    def send_reply(self, *, account: str, recipient: str, message_id: str, body: str, draft_id: str) -> str:
        address = parseaddr(recipient)[1]
        if not account or not address or not message_id:
            raise ValueError("Gmail reply requires account, recipient, and message id")
        reference = message_id.strip("<>")
        raw = f"To: {address}\nSubject: Re: UserIO reply\nIn-Reply-To: <{reference}>\nReferences: <{reference}>\n\n{body}\n"
        try:
            completed = self._runner(
                [self._binary, "--account", account, "message", "send", "--save", "Sent"],
                input=raw, text=True, capture_output=True, timeout=30, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError("Himalaya Gmail delivery did not complete") from error
        if completed.returncode != 0:
            raise RuntimeError("Himalaya Gmail delivery failed")
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
        if result.get("status") != "accepted_by_android":
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


class AdapterNotSupported(ValueError):
    """The selected adapter honestly does not implement this capability."""


class StoredChannelAdapter:
    """User-bound wrapper over UserIO's canonical conversations and draft queue."""

    channel: str | None = None

    def __init__(self, store: SQLiteUserIOStore, service: UserIOService, user_id: str) -> None:
        self._store, self._service, self._user_id = store, service, user_id

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


class TelegramChannelAdapter(StoredChannelAdapter):
    channel = "telegram"


class WhatsAppChannelAdapter(StoredChannelAdapter):
    channel = "whatsapp"


class VKChannelAdapter(StoredChannelAdapter):
    channel = "vk"


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
        adapter_type = self._types.get(channel.strip().lower())
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
