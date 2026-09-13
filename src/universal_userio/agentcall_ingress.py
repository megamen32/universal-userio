"""Project safe AgentCall lifecycle events into UserIO's ``phone`` channel."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import time
import urllib.request
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any


LOG = logging.getLogger("universal_userio.agentcall_ingress")
_MAX_FRAME_BYTES = 64 * 1024
_MAX_TEXT_CHARS = 4_000
_CALL_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)


@dataclass(frozen=True, slots=True)
class AgentCallEventProjector:
    """Convert redacted AgentCall RPC events into canonical inbox envelopes."""

    sender_name: str = "AI phone call"

    def project(self, event: Mapping[str, Any]) -> dict[str, str] | None:
        kind = str(event.get("event") or "").strip().lower()
        call_id = str(event.get("callId") or "").strip()
        if (
            not call_id
            or len(call_id) > 128
            or any(char not in _CALL_ID_CHARS for char in call_id)
        ):
            return None
        body = self._body(kind, event)
        if body is None:
            return None
        canonical = json.dumps(
            event, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
        return {
            "schema": "universal.inbox.message.v1",
            "source": "phone",
            "message_id": f"agentcall-{digest}",
            "sender": call_id,
            "sender_name": self.sender_name,
            "body": body[:_MAX_TEXT_CHARS],
            "direction": self._direction(kind, event),
        }

    @staticmethod
    def _direction(kind: str, event: Mapping[str, Any]) -> str:
        if kind == "transcript_final":
            speaker = str(event.get("speaker") or "").strip().lower()
            if speaker == "agent":
                return "outgoing"
            if speaker == "remote":
                return "incoming"
        return "system"

    @staticmethod
    def _body(kind: str, event: Mapping[str, Any]) -> str | None:
        if kind == "dialing":
            return "Исходящий ИИ-звонок: набор номера"
        if kind == "incoming":
            return "Входящий звонок"
        if kind == "active":
            direction = str(event.get("direction") or "").strip().lower()
            return "Исходящий ИИ-звонок начат" if direction == "outgoing" else "Звонок принят"
        if kind == "transcript_final":
            text = str(event.get("text") or "").strip()
            if not text:
                return None
            speaker = str(event.get("speaker") or "").strip().lower()
            prefix = (
                "Собеседник"
                if speaker == "remote"
                else "ИИ"
                if speaker == "agent"
                else "Речь"
            )
            return f"{prefix}: {text}"
        if kind == "ended":
            reason = _safe_label(event.get("reason") or event.get("outcome"))
            return f"Звонок завершён ({reason})" if reason else "Звонок завершён"
        if kind in {"error", "media_failure"}:
            reason = _safe_label(event.get("reason") or event.get("code"))
            return f"Ошибка звонка ({reason})" if reason else "Ошибка звонка"
        return None


def _safe_label(value: object) -> str:
    text = str(value or "").strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._- ")
    return text if text and len(text) <= 96 and all(char in allowed for char in text) else ""


class AgentCallRpcSubscriber:
    """Read the gateway's newline-delimited, redacted event subscription."""

    def __init__(self, socket_path: str, *, timeout: float = 15.0) -> None:
        self.socket_path = socket_path
        self.timeout = timeout

    def events(self) -> Iterator[dict[str, Any]]:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(self.timeout)
            connection.connect(self.socket_path)
            request = {"id": "userio-events", "method": "events", "args": {}}
            connection.sendall(
                (json.dumps(request, separators=(",", ":")) + "\n").encode()
            )
            stream = connection.makefile("rb")
            acknowledgement = self._read_frame(stream)
            if acknowledgement != {
                "id": "userio-events",
                "result": {"subscribed": True},
            }:
                raise RuntimeError("AgentCall event subscription refused")
            connection.settimeout(None)
            while True:
                frame = self._read_frame(stream)
                event = frame.get("event")
                if isinstance(event, dict):
                    yield event

    @staticmethod
    def _read_frame(stream: Any) -> dict[str, Any]:
        line = stream.readline(_MAX_FRAME_BYTES + 1)
        if not line:
            raise ConnectionError("AgentCall event stream closed")
        if len(line) > _MAX_FRAME_BYTES or not line.endswith(b"\n"):
            raise ValueError("AgentCall event frame is invalid")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("AgentCall event frame is invalid")
        return value


class UserIOIngressClient:
    def __init__(
        self,
        base_url: str,
        bearer_token: str,
        *,
        route_id: str = "phone",
        account_id: str = "",
        timeout: float = 15.0,
    ) -> None:
        self.endpoint = base_url.rstrip("/") + "/v1/messages"
        self.bearer_token = bearer_token
        self.route_id = route_id
        self.account_id = account_id
        self.timeout = timeout

    def publish(self, message: Mapping[str, str]) -> None:
        payload: dict[str, object] = {
            "route_id": self.route_id,
            "message": dict(message),
        }
        if self.account_id:
            payload["account_id"] = self.account_id
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.bearer_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            if response.status not in {200, 201, 202}:
                raise RuntimeError(f"UserIO ingress returned HTTP {response.status}")


def run_forever(
    subscriber: AgentCallRpcSubscriber,
    publisher: UserIOIngressClient,
    projector: AgentCallEventProjector,
    *,
    reconnect_seconds: float = 3.0,
) -> None:
    while True:
        try:
            for event in subscriber.events():
                message = projector.project(event)
                if message is not None:
                    publisher.publish(message)
        except (ConnectionError, OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
            LOG.warning("AgentCall/UserIO bridge reconnecting: %s", error)
            time.sleep(max(0.1, reconnect_seconds))


def userio_url_from_environment() -> str:
    configured = os.environ.get("USERIO_URL", "").strip()
    if configured:
        return configured
    port = os.environ.get("USERIO_PORT", "18093").strip() or "18093"
    if not port.isdigit() or not 1 <= int(port) <= 65_535:
        raise SystemExit("USERIO_PORT is invalid")
    return f"http://127.0.0.1:{port}"


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    bearer_token = os.environ.get("USERIO_BEARER_TOKEN", "").strip() or os.environ.get(
        "USERIO_API_TOKEN", ""
    ).strip()
    if not bearer_token:
        raise SystemExit("USERIO_BEARER_TOKEN is required")
    run_forever(
        AgentCallRpcSubscriber(
            os.environ.get("AGENTCALL_RPC_SOCKET", "/run/agentcall/gatewayd.sock")
        ),
        UserIOIngressClient(
            userio_url_from_environment(),
            bearer_token,
            route_id=os.environ.get("USERIO_PHONE_ROUTE_ID", "phone").strip() or "phone",
            account_id=os.environ.get("USERIO_ACCOUNT_ID", "").strip(),
        ),
        AgentCallEventProjector(
            os.environ.get("USERIO_PHONE_LABEL", "S21 AI call").strip()
        ),
    )


if __name__ == "__main__":
    main()
