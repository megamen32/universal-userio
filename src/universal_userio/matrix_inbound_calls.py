"""Inbound Matrix call intake inside the existing UserIO runtime.

The connected-account lifecycle already owns the managed Matrix credentials
and the voice transport to the voice host. This module adds the missing half:
it watches those accounts for an incoming MatrixRTC call addressed to the
account and hands the call to the existing control transport, which answers it
on the voice host with the existing Secretary SDK.

Admission is narrow and evidence-based: the caller is the sender of the call
notification, the room must hold exactly the called account and that caller,
and the account must be one the deployment allows. Every call is keyed by the
caller's own call event and recorded in the existing durable call store, so a
keepalive, a repeated sync or a restart never answers the same call twice.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import threading
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

log = logging.getLogger("userio.matrix_inbound_calls")

MEMBER = "org.matrix.msc3401.call.member"
RING = "org.matrix.msc4075.rtc.notification"
CALLEE_ACTION = "matrix_answer_transport"
DEFAULT_POLL_SECONDS = 5.0
DEFAULT_LIMIT = 50
DEFAULT_CALL_ROOT = "/var/lib/universal-userio/matrix-calls"


class _RejectRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Matrix redirects may not forward bearer tokens")


def _matrix_open(request, timeout):
    return urllib.request.build_opener(_RejectRedirect).open(request, timeout=timeout)


@dataclass(frozen=True, slots=True)
class InboundMatrixCall:
    """One incoming MatrixRTC call addressed to the polled account."""

    room_id: str
    event_id: str
    caller: str
    call_id: str
    focus: Mapping[str, Any]
    expires_ms: int
    origin_server_ts: int = 0
    joined: tuple[str, ...] = ()
    caller_device: str = ""
    membership_id: str = ""

    @property
    def key(self) -> str:
        """Stable identity of this call event; the same event is never answered twice."""
        digest = hashlib.sha256(
            json.dumps([self.room_id, self.event_id, self.call_id], sort_keys=True).encode()
        ).hexdigest()
        return digest

    def receipt_id(self, account_ref: str) -> str:
        """Durable receipt identity of this call for one account.

        The identity is the call itself, not the notification: the same call may
        be announced again, and a restart must still find the same receipt.
        """
        # The membership id is the session identity of this very call: it is
        # stable for its keepalives and notifications, and a new call on the
        # same device publishes a new one.
        return str(uuid.uuid5(uuid.NAMESPACE_URL, "\0".join(
            [account_ref, self.room_id, self.caller,
             self.membership_id or self.call_id or self.event_id])))


class MatrixCallReader:
    """Read MatrixRTC call events of one account; messages are never requested."""

    def __init__(self, homeserver: str, token: str, own_user_id: str,
                 runner: Any = _matrix_open) -> None:
        self.homeserver = homeserver.rstrip("/")
        self.token = token
        self.own_user_id = own_user_id
        self.runner = runner
        if not self.homeserver or not self.token or not self.own_user_id:
            raise ValueError("matrix call reader config incomplete")

    def _joined_members(self, room_id: str, room: Mapping[str, Any]) -> tuple[str, ...]:
        """The room's current members: the sync state first, the room view after."""
        joined = {str(event.get("state_key") or "")
                  for event in ((room.get("state") or {}).get("events") or [])
                  if isinstance(event, dict) and event.get("type") == "m.room.member"
                  and (event.get("content") or {}).get("membership") == "join"}
        if joined:
            return tuple(sorted(joined))
        # A call usually arrives without the members in this delta, so the room
        # view is the persistent source of the member set.
        request = urllib.request.Request(
            self.homeserver + "/_matrix/client/v3/rooms/" + urllib.parse.quote(room_id, safe="")
            + "/joined_members", headers={"Authorization": "Bearer " + self.token})
        with self.runner(request, timeout=20) as response:
            data = json.loads(response.read())
        members = data.get("joined")
        if not isinstance(members, dict):
            raise RuntimeError("invalid Matrix joined members response")
        return tuple(sorted(str(key) for key in members))

    def _event(self, room_id: str, event_id: str) -> Mapping[str, Any]:
        """Read one room event by its identifier."""
        request = urllib.request.Request(
            self.homeserver + "/_matrix/client/v3/rooms/" + urllib.parse.quote(room_id, safe="")
            + "/event/" + urllib.parse.quote(event_id, safe=""),
            headers={"Authorization": "Bearer " + self.token})
        with self.runner(request, timeout=20) as response:
            data = json.loads(response.read())
        return data if isinstance(data, dict) else {}

    def _call_members(self, room_id: str, room: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
        """The call memberships of the room: the delta plus the room state.

        A call membership is a state event and is published before the ring, so
        it is usually not part of the delta that carries the ring; the room
        state is the persistent source of the member set.
        """
        members: dict[str, Mapping[str, Any]] = {}
        events = list(((room.get("state") or {}).get("events") or []))
        events += list((room.get("timeline") or {}).get("events") or [])
        for event in events:
            if isinstance(event, dict) and event.get("type") == MEMBER:
                members[str(event.get("state_key") or "")] = event
        request = urllib.request.Request(
            self.homeserver + "/_matrix/client/v3/rooms/" + urllib.parse.quote(room_id, safe="")
            + "/state", headers={"Authorization": "Bearer " + self.token})
        with self.runner(request, timeout=20) as response:
            data = json.loads(response.read())
        for event in data if isinstance(data, list) else []:
            if isinstance(event, dict) and event.get("type") == MEMBER:
                members[str(event.get("state_key") or "")] = event
        return members

    def _call_member(self, room_id: str, room: Mapping[str, Any],
                     ring: Mapping[str, Any], caller: str) -> Mapping[str, Any] | None:
        """The call membership of this ring.

        The ring references its own membership event, and that exact event is
        the call of this ring. The room state is the fallback for a ring
        without a reference; the newest membership of the caller wins.
        """
        relates = (ring.get("content") or {}).get("m.relates_to") or {}
        event_id = str(relates.get("event_id") or "")
        if event_id:
            event = self._event(room_id, event_id)
            if event.get("type") == MEMBER and str(event.get("sender") or "") == caller:
                return event
        members = self._call_members(room_id, room)
        return next((value for key, value in sorted(
            members.items(),
            key=lambda item: int(item[1].get("origin_server_ts") or 0),
            reverse=True)
            if key.startswith("_" + caller + "_")
            and str(value.get("sender") or "") == caller), None)

    def poll(self, cursor: str | None, *, limit: int = DEFAULT_LIMIT) -> tuple[list[InboundMatrixCall], str]:
        query = {"timeout": "0", "filter": json.dumps({"types": [MEMBER, RING]}, separators=(",", ":"))}
        if cursor:
            query["since"] = cursor
        request = urllib.request.Request(
            self.homeserver + "/_matrix/client/v3/sync?" + urllib.parse.urlencode(query),
            headers={"Authorization": "Bearer " + self.token})
        with self.runner(request, timeout=30) as response:
            data = json.loads(response.read())
        next_batch = data.get("next_batch")
        rooms = data.get("rooms", {}).get("join", {})
        if not isinstance(next_batch, str) or not isinstance(rooms, dict):
            raise RuntimeError("invalid Matrix sync response")
        if cursor is None:
            return [], next_batch
        calls: list[InboundMatrixCall] = []
        for room_id, room in rooms.items():
            if not isinstance(room, dict):
                continue
            rings: list[Mapping[str, Any]] = []
            for event in room.get("timeline", {}).get("events", []):
                if not isinstance(event, dict) or event.get("type") != RING:
                    continue
                mentions = (event.get("content") or {}).get("m.mentions") or {}
                if self.own_user_id in (mentions.get("user_ids") or []):
                    rings.append(event)
            if not rings:
                continue
            joined = self._joined_members(room_id, room)
            for ring in rings:
                # The call notification names the caller; the call membership
                # belongs to that same caller and to no one else.
                caller = str(ring.get("sender") or "")
                member = self._call_member(room_id, room, ring, caller)
                if member is None:
                    continue
                content = member.get("content") or {}
                focus = next((item for item in content.get("foci_preferred", [])
                              if isinstance(item, dict) and item.get("type") == "livekit"), None)
                if focus is None:
                    continue
                calls.append(InboundMatrixCall(
                    room_id=str(room_id),
                    event_id=str(ring.get("event_id") or ""),
                    caller=caller,
                    call_id=str(content.get("call_id") or ""),
                    focus=focus,
                    expires_ms=int(content.get("expires") or 0),
                    origin_server_ts=int(ring.get("origin_server_ts") or 0),
                    joined=joined,
                    caller_device=_caller_device(str(member.get("state_key") or ""), caller),
                    membership_id=str(content.get("membershipID") or ""),
                ))
                if len(calls) >= limit:
                    break
            if len(calls) >= limit:
                break
        return [call for call in calls if call.event_id and call.caller], next_batch


def _caller_device(state_key: str, caller: str) -> str:
    """The caller's device from its RTC membership key."""
    prefix = "_" + caller + "_"
    if not state_key.startswith(prefix) or not state_key.endswith("_m.call"):
        return ""
    return state_key[len(prefix):-len("_m.call")]


def admit_call(call: InboundMatrixCall, *, own_user_id: str, allowed_accounts: Sequence[str],
               now: float | None = None) -> str | None:
    """Return the reason a call is refused, or None when it may be answered.

    The trusted caller is the sender of the call notification and the room must
    hold exactly the called account and that caller. An expired call, an
    unknown account or a room with other members is never answered.
    """
    if own_user_id not in allowed_accounts:
        return "account_not_allowed"
    if not call.caller or call.caller == own_user_id:
        return "caller_not_trusted"
    if set(call.joined) != {own_user_id, call.caller}:
        return "room_not_one_to_one"
    if call.expires_ms and call.origin_server_ts:
        expires_at = call.origin_server_ts / 1000 + call.expires_ms / 1000
        if (now if now is not None else time.time()) > expires_at:
            return "call_expired"
    return None


def control_socket_path(config_path: str | Path = "/etc/userio-containers/matrix-call-control.json") -> str:
    """Resolve the existing private control route of the voice host."""
    path = Path(config_path)
    metadata = path.stat()
    if metadata.st_uid not in {0, os.geteuid()} or metadata.st_mode & 0o077:
        raise PermissionError("matrix control configuration must be service owned")
    value = json.loads(path.read_text())
    if value.get("execution_host") != "roomhacker-server-88":
        raise ValueError("matrix control route does not name the voice host")
    socket_path = str(value.get("socket_path") or "")
    if not socket_path.startswith("/run/agentcall/"):
        raise ValueError("matrix control route is not a private runtime socket")
    return socket_path


def build_answer_request(call: InboundMatrixCall, *, account: Mapping[str, str],
                         secret: Mapping[str, str], identity: Mapping[str, str],
                         rtc_auth_url: str, purpose: str = "") -> dict[str, Any]:
    """Build the control request that answers one admitted call on the voice host.

    The request carries the trusted caller and the room; the person identity is
    resolved by the voice host from its own verified identity registry.
    """
    if not call.caller or call.caller == account.get("matrix_user_id"):
        raise ValueError("matrix call is not addressed to the polled account")
    return {
        "action": CALLEE_ACTION,
        "call": {
            # The wire call id stays room-scoped like the working caller, and
            # the internal engine uuid is a separate field.
            "call_id": "",
            "engine_call_id": str(uuid.uuid5(uuid.NAMESPACE_URL, call.key)),
            "room_id": call.room_id,
            "caller": call.caller,
            "purpose": purpose or f"Входящий Matrix-звонок от {call.caller}",
            "max_seconds": 120,
            "focus": dict(call.focus),
            "expires_ms": call.expires_ms,
        },
        "account": dict(secret),
        "identity": dict(identity),
        "rtc_auth_url": rtc_auth_url,
    }


def answer_call(request: Mapping[str, Any], socket_path: str, *, timeout: float = 240.0) -> dict[str, Any]:
    """Send the answer request over the existing control transport."""
    payload = (json.dumps(request, ensure_ascii=False) + "\n").encode()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(socket_path)
        client.sendall(payload)
        stream = client.makefile("rb")
        while True:
            line = stream.readline()
            if not line:
                break
            event = json.loads(line)
            if event.get("type") in {"receipt", "error"}:
                return event
    return {"type": "error", "error": "no_receipt"}


def receipt_path(root: str | Path, receipt_id: str) -> Path:
    return Path(root) / receipt_id / "call.json"


def load_receipt(root: str | Path, receipt_id: str) -> dict[str, Any] | None:
    """Read an existing durable receipt; its presence means the call was handled."""
    path = receipt_path(root, receipt_id)
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def write_receipt(root: str | Path, receipt_id: str, record: Mapping[str, Any]) -> Path:
    """Store the durable receipt of one handled call in the existing call store."""
    folder = Path(root) / receipt_id
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = folder / "call.json"
    temporary = folder / (".call-" + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(dict(record), handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return path


def claim_receipt(root: str | Path, receipt_id: str, record: Mapping[str, Any]) -> Path:
    """Claim one call durably before the external answer is attempted.

    The claim is written atomically and is never removed: a restart or a
    repeated sync must find it and answer nothing a second time.
    """
    return write_receipt(root, receipt_id, dict(record, status="accepted", claimed_at=time.time()))


class MatrixInboundCallWorker:
    """Watch the managed accounts and answer an incoming call once."""

    def __init__(self, service: Any, *, allowed_accounts: Sequence[str], rtc_auth_url: str,
                 call_root: str | Path = DEFAULT_CALL_ROOT,
                 control_config: str | Path = "/etc/userio-containers/matrix-call-control.json",
                 reader_factory: Any = None, answers: Any = None, vault: Any = None,
                 poll_seconds: float = DEFAULT_POLL_SECONDS) -> None:
        self.service = service
        self.allowed_accounts = tuple(allowed_accounts)
        self.rtc_auth_url = rtc_auth_url
        self.call_root = Path(call_root)
        self.control_config = Path(control_config)
        self.reader_factory = reader_factory or self._reader
        self.answers = answers or self._answer
        self.vault = vault
        self.poll_seconds = max(1.0, float(poll_seconds))
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self._cursors: dict[str, str] = {}

    def start(self) -> None:
        self.thread = threading.Thread(target=self.run, name="userio-matrix-inbound-calls", daemon=True)
        self.thread.start()

    def _reader(self, account: Mapping[str, str], secret: Mapping[str, str]) -> MatrixCallReader:
        return MatrixCallReader(secret["homeserver"], secret["access_token"], secret["matrix_user_id"])

    def _accounts(self) -> list[Mapping[str, str]]:
        with self.service._store._lock:
            rows = [dict(row) for row in self.service._store._connection.execute(
                """SELECT user_id,id,provider,credential_ref FROM provider_accounts
                   WHERE enabled=1 AND provider='matrix' AND credential_ref LIKE 'connector:%'
                   ORDER BY user_id,id""")]
        return rows

    def _answer(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return answer_call(request, control_socket_path(self.control_config))

    @staticmethod
    def _default_vault():
        from .account_connections import ConnectorSecrets
        return ConnectorSecrets()

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                self.poll_once()
            except Exception as error:
                log.warning("matrix inbound call poll failed: %s", type(error).__name__)
            self.stop.wait(self.poll_seconds)

    def poll_once(self) -> int:
        vault = self.vault or self._default_vault()
        answered = 0
        for account in self._accounts():
            if self.stop.is_set():
                break
            try:
                secret = vault.load(account["credential_ref"], user_id=account["user_id"], provider="matrix")
                own_user_id = str(secret.get("matrix_user_id") or "")
                if own_user_id not in self.allowed_accounts:
                    continue
                reader = self.reader_factory(account, secret)
                calls, nxt = reader.poll(self._cursors.get(account["id"]))
                self._cursors[account["id"]] = nxt
                for call in calls:
                    reason = admit_call(call, own_user_id=own_user_id,
                                       allowed_accounts=self.allowed_accounts)
                    if reason is not None:
                        log.info("matrix inbound call refused room=%s reason=%s", call.room_id, reason)
                        continue
                    receipt_id = call.receipt_id(account["id"])
                    if load_receipt(self.call_root, receipt_id) is not None:
                        # A keepalive, a repeated sync or a restart must never
                        # answer the same call a second time.
                        continue
                    request = build_answer_request(
                        call,
                        account={"id": account["id"], "matrix_user_id": own_user_id},
                        secret=secret, identity={"device_id": secret.get("device_id", "")},
                        rtc_auth_url=self.rtc_auth_url)
                    base = {
                        "call_id": receipt_id,
                        "user_id": account["user_id"],
                        "account_ref": account["id"],
                        "room_id": call.room_id,
                        "target": call.caller,
                        "direction": "incoming",
                        "created_at": time.time(),
                    }
                    # The claim lands before the external answer, so a crash in
                    # the middle of the call can never replay it.
                    claim_receipt(self.call_root, receipt_id, base)
                    try:
                        receipt = self.answers(request)
                    except Exception as error:
                        write_receipt(self.call_root, receipt_id, dict(
                            base, status="unknown", answered=False,
                            error=type(error).__name__, claimed_at=time.time()))
                        log.warning("matrix inbound call answer failed room=%s error=%s",
                                   call.room_id, type(error).__name__)
                        continue
                    write_receipt(self.call_root, receipt_id, dict(
                        base, status="sent" if receipt.get("type") == "receipt" else "unknown",
                        answered=receipt.get("type") == "receipt", result=receipt))
                    answered += 1
                    log.info("matrix inbound call handled room=%s caller=%s receipt=%s",
                             call.room_id, call.caller, receipt.get("type"))
            except Exception as error:
                log.warning("matrix inbound call account %s failed: %s", account.get("id"), type(error).__name__)
        return answered
