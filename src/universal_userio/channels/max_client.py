"""MAX (oneme.ru) WebSocket transport client for the ``max`` channel.

Speaks the OneMe JSON WebSocket protocol (ver 11) that web.max.ru itself uses:
``wss://ws-api.oneme.ru/websocket`` with envelope frames
``{"ver": 11, "cmd": 0|1|3, "seq": n, "opcode": op, "payload": {...}}``.

Session lifecycle: INIT (opcode 6, deviceId + userAgent) -> LOGIN (opcode 19,
access token) -> normal requests. Heartbeat PING (opcode 1) keeps the socket
alive; DISPATCH (opcode 128) frames carry incoming events for the ingress.

Protocol references: https://github.com/MaxApiTeam/PyMax and
https://github.com/pr0bel1230/max-api-docs. Reuse an authorized session and its
stable device ID. Android authorization was verified on this transport; never
assume that a token from another account or device is interchangeable.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from collections import deque
from typing import Any

import websocket
from ..contracts import DeliveryNotSubmittedError, ProviderAuthenticationRequiredError

WS_URL = "wss://ws-api.oneme.ru/websocket"

OP_PING = 1
OP_INIT = 6
OP_LOGIN = 19
OP_GET_CHATS = 53
OP_CHAT_INFO = 48
OP_GET_HISTORY = 49
OP_SEND = 64
OP_EDIT = 67
OP_GET_MESSAGE = 71
OP_DISPATCH = 128
OP_REACTION = 178
OP_CANCEL_REACTION = 179
OP_GET_REACTIONS = 180
OP_TYPING = 65
OP_FILE_UPLOAD = 87
OP_FILE_READY = 136

_HEARTBEAT_INTERVAL = 25.0


class MaxSendNotSubmittedError(DeliveryNotSubmittedError):
    """Connection/authentication failed before any outgoing message command."""


class MaxTransport:
    """Thin wrapper over a websocket-client connection (overridable in tests)."""

    def __init__(self, *, socks_host: str = "", socks_port: int = 0, web_auth: bool = False) -> None:
        self._socks_host, self._socks_port = socks_host, socks_port
        self._web_auth = web_auth
        self._host = 'api.oneme.ru' if web_auth else 'ws-api.oneme.ru'
        self._url = 'wss://' + self._host + '/websocket'
        self._options = {'origin':'https://web.max.ru'} if web_auth else {}

    def connect(self, timeout: float) -> Any:
        if self._socks_host:
            import socket as pysocket
            import ssl

            import socks

            raw = socks.socksocket(pysocket.AF_INET, pysocket.SOCK_STREAM)
            raw.set_proxy(socks.SOCKS5, self._socks_host, self._socks_port)
            raw.settimeout(timeout)
            raw.connect((self._host, 443))
            tls = ssl.create_default_context().wrap_socket(
                raw, server_hostname=self._host
            )
            ws = websocket.WebSocket()
            ws.connect(self._url, socket=tls, timeout=timeout, **self._options)
            return ws
        return websocket.create_connection(self._url, timeout=timeout, **self._options)

    def send(self, ws: Any, text: str) -> None:
        if self._web_auth:
            from .max_wire import encode
            ws.send(encode(json.loads(text)),opcode=websocket.ABNF.OPCODE_BINARY)
        else:
            ws.send(text)

    def recv(self, ws: Any, timeout: float) -> dict[str, Any] | None:
        ws.settimeout(timeout)
        try:
            raw = ws.recv()
        except websocket.WebSocketTimeoutException:
            return None
        if not raw:
            raise ConnectionError("MAX socket closed")
        if self._web_auth:
            from .max_wire import decode
            return decode(raw)
        return json.loads(raw)

    @staticmethod
    def close(ws: Any) -> None:
        try:
            ws.close()
        except Exception:  # noqa: BLE001 - best-effort teardown
            pass


class MaxClient:
    """Synchronous MAX session client: one command = one locked round-trip."""

    def __init__(
        self,
        token: str,
        device_id: str,
        *,
        socks_host: str = "",
        socks_port: int = 0,
        app_version: str = "26.6.17",
        transport: MaxTransport | None = None,
    ) -> None:
        self._token = token
        self._device_id = device_id or str(uuid.uuid4())
        self._app_version = app_version
        self._transport = transport or MaxTransport(socks_host=socks_host, socks_port=socks_port)
        self._ws: Any = None
        self._lock = threading.RLock()
        self._pending: deque[dict[str, Any]] = deque()
        self._ready_files: deque[str] = deque(maxlen=64)
        self._seq = 0
        self._profile: dict[str, Any] | None = None
        self._last_heartbeat = 0.0
        self.authentication_required = False
        self._anonymous = False

    # -- session -----------------------------------------------------------

    @property
    def profile(self) -> dict[str, Any] | None:
        return self._profile

    def connected(self) -> bool:
        ws = self._ws
        return bool(ws) and getattr(ws, "connected", False)

    def connect(self) -> dict[str, Any]:
        with self._lock:
            try:
                return self._connect()
            except Exception:
                self.close()
                raise

    def _connect(self) -> dict[str, Any]:
        """INIT + LOGIN; returns the LOGIN payload (profile, chats, users)."""
        if self._anonymous:
            raise ValueError('anonymous QR session cannot perform sender LOGIN')
        if self.authentication_required:
            raise ProviderAuthenticationRequiredError('max')
        if self.connected():
            return self._profile or {}
        self._initialize_transport()
        login = self._roundtrip(
            OP_LOGIN,
            {
                "interactive": True,
                "token": self._token,
                "chatsCount": 100,
                "chatsSync": 100,
                "contactsSync": 0,
                "presenceSync": 0,
                "draftsSync": 0,
            },
        )
        if not isinstance(login, dict) or not login.get("profile"):
            raise RuntimeError("MAX LOGIN rejected the session token")
        expected=str(getattr(self,'expected_user_id',''))
        if expected and str((login['profile'].get('contact') or {}).get('id') or '')!=expected:
            raise RuntimeError('MAX authenticated personal identity mismatch')
        self._profile = login
        self.authentication_required = False
        return login

    def initialize_anonymous(self) -> None:
        """Initialize an isolated QR login client, never an existing sender."""
        with self._lock:
            if self._token or self.connected():
                raise ValueError('isolated anonymous MAX client required')
            self._anonymous = True
            try:
                self._initialize_transport()
            except Exception:
                self.close()
                raise

    def _initialize_transport(self) -> None:
        ws = self._transport.connect(timeout=20)
        self._ws, self._seq, self._last_heartbeat = ws, 0, time.monotonic()
        init = self._roundtrip(
            OP_INIT,
            {
                "deviceId": self._device_id,
                "userAgent": {
                    "deviceType": "WEB",
                    "pushDeviceType": "WEBPUSH",
                    "locale": "ru",
                    "deviceLocale": "ru",
                    "osVersion": "Linux",
                    "deviceName": "Firefox",
                    "headerUserAgent": (
                        "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"
                    ),
                    "appVersion": self._app_version,
                    "screen": "1366x900 1.0x",
                    "timezone": "Europe/Moscow",
                },
            },
        )
        if not init:
            raise RuntimeError("MAX INIT returned no payload")

    def close(self) -> None:
        with self._lock:
            if self._ws is not None:
                self._transport.close(self._ws)
                self._ws = None

    def _ensure(self) -> None:
        if not self.connected():
            if self._anonymous:
                raise RuntimeError('isolated MAX QR session disconnected')
            self.connect()

    # -- protocol core ------------------------------------------------------

    def _send_envelope(self, opcode: int, payload: dict[str, Any]) -> int:
        self._seq += 1
        frame = {"ver": 11, "cmd": 0, "seq": self._seq, "opcode": opcode, "payload": payload}
        self._transport.send(self._ws, json.dumps(frame, ensure_ascii=False))
        return self._seq

    def _heartbeat_if_due(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_heartbeat < _HEARTBEAT_INTERVAL:
            return
        if self._ws is not None:
            self._send_envelope(OP_PING, {"interactive": False})
        self._last_heartbeat = now

    def _roundtrip(
        self, opcode: int, payload: dict[str, Any], *, timeout: float = 20.0
    ) -> Any:
        # A remote idle close can leave websocket.connected locally true.
        # Only these side-effect-free reads may reconnect and repeat once;
        # a lost mutation acknowledgement must remain UNKNOWN.
        read_only = opcode in {OP_GET_CHATS, OP_CHAT_INFO, OP_GET_HISTORY, OP_GET_MESSAGE, OP_GET_REACTIONS, 32}
        with self._lock:
            for attempt in range(2 if read_only else 1):
                try:
                    return self._roundtrip_once(opcode, payload, timeout=timeout)
                except (OSError, websocket.WebSocketConnectionClosedException):
                    self.close()
                    if not read_only or attempt:
                        raise
        raise AssertionError('unreachable MAX request state')

    def _roundtrip_once(
        self, opcode: int, payload: dict[str, Any], *, timeout: float = 20.0
    ) -> Any:
        """Send one request and read frames until its ACK (cmd=1) or error (cmd=3)."""
        with self._lock:
            self._ensure()
            seq = self._send_envelope(opcode, payload)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                frame = self._transport.recv(self._ws, max(1.0, deadline - time.monotonic()))
                if frame is None:
                    continue
                if frame.get('opcode')==OP_FILE_READY and frame.get('cmd')==0:
                    self._remember_file_ready(frame)
                if frame.get("opcode") == OP_DISPATCH:
                    if len(self._pending) >= 1000:
                        raise RuntimeError("MAX event buffer full; reconnect/catch up required")
                    self._pending.append(frame.get("payload") or {})
                if (frame.get("seq") == seq and frame.get("opcode") == opcode
                        and frame.get("cmd") in (1, 3)):
                    if frame.get("cmd") == 3:
                        error = frame.get("payload") or {}
                        # Native error bodies can include private diagnostics.
                        # Log only a dotted alphabetic protocol code and opcode.
                        code = error.get('error')
                        safe_code = code if isinstance(code, str) and re.fullmatch(r'[a-z_]+(?:\.[a-z_]+){1,5}', code) else 'unclassified'
                        logging.getLogger(__name__).warning('MAX protocol refusal opcode=%s code=%s', opcode, safe_code)
                        if opcode == OP_LOGIN and code == 'login.token':
                            self.authentication_required = True
                            raise ProviderAuthenticationRequiredError('max', provider_code='login.token')
                        raise RuntimeError(
                            f"MAX opcode {opcode} failed: {error.get('error', 'provider-error')}"
                        )
                    return frame.get("payload")
            raise TimeoutError(f"MAX opcode {opcode} timed out after {timeout}s")

    # -- public commands ----------------------------------------------------

    def _remember_file_ready(self, frame):
        value=(frame.get('payload') or {}).get('fileId')
        if str(value).isdigit() and int(value)>0:
            self._ready_files.append(str(value))

    def upload_file(self, *, data: bytes, filename: str, mime_type: str) -> dict[str, Any]:
        """Upload new bounded bytes and wait for the same-client FILE_READY.

        PyMax uploads/service.py: FILE_UPLOAD87 -> HTTP POST -> NOTIF_ATTACH136.
        No automatic retry after upload begins. Caller owns durable uncertainty.
        """
        import urllib.request
        from urllib.parse import urlsplit, quote
        from ..messenger_max_media import NoRedirect
        if not isinstance(data,bytes) or not 1<=len(data)<=2*1024*1024:
            raise ValueError('MAX upload exceeds file budget')
        with self._lock:
            response=self._roundtrip(OP_FILE_UPLOAD,{'count':1,'type':0,'uploaderType':0,'profile':False})
            info=(response or {}).get('info')
            if not isinstance(info,list) or len(info)!=1 or not isinstance(info[0],dict):
                raise RuntimeError('MAX upload metadata unavailable')
            fid=str(info[0].get('fileId') or '')
            url=info[0].get('url');parsed=urlsplit(str(url));host=parsed.hostname or ''
            if (not fid.isdigit() or int(fid)<=0 or parsed.scheme!='https' or parsed.username or parsed.password
                    or parsed.port not in {None,443} or not any(host==d or host.endswith('.'+d) for d in ('oneme.ru','mycdn.me','okcdn.ru'))):
                raise RuntimeError('MAX native upload route unavailable')
            req=urllib.request.Request(url,data=data,method='POST',headers={
                'Content-Type':mime_type,'Content-Disposition':'attachment; filename='+quote(filename),
                'Content-Length':str(len(data)),'Content-Range':f'0-{len(data)-1}/{len(data)}'})
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open
            with opener(req,timeout=20) as uploaded:
                if uploaded.status!=200: raise RuntimeError('MAX upload not confirmed')
                uploaded.read(4096)
            deadline=time.monotonic()+30
            while fid not in self._ready_files and time.monotonic()<deadline:
                frame=self._transport.recv(self._ws,max(0.1,deadline-time.monotonic()))
                if frame is None: continue
                if frame.get('opcode')==OP_FILE_READY and frame.get('cmd')==0:
                    self._remember_file_ready(frame)
                elif frame.get('opcode')==OP_DISPATCH:
                    if len(self._pending)>=1000:raise RuntimeError('MAX event buffer full')
                    self._pending.append(frame.get('payload') or {})
            if fid not in self._ready_files: raise TimeoutError('MAX upload readiness unknown; do not retry')
            self._ready_files.remove(fid)
            return {'_type':'FILE','fileId':int(fid)}

    def chats(self, *, count: int = 50) -> list[dict[str, Any]]:
        payload = self._roundtrip(OP_GET_CHATS, {"count": count, "marker": int(time.time() * 1000)})
        chats = (payload or {}).get("chats")
        return list(chats) if isinstance(chats, list) else []

    def history(self, chat_id: int | str, *, backward: int = 30, before: int | None = None) -> list[dict[str, Any]]:
        payload = self._roundtrip(
            OP_GET_HISTORY,
            {"chatId": int(chat_id), "from": before if before is not None else int(time.time() * 1000),
             "backward": max(1, min(int(backward), 100)), "forward": 0},
        )
        messages = (payload or {}).get("messages")
        return list(messages) if isinstance(messages, list) else []

    def get_chat(self, chat_id: int | str) -> dict[str, Any]:
        payload = self._roundtrip(OP_CHAT_INFO, {"chatIds": [int(chat_id)]})
        return next((row for row in (payload or {}).get("chats", [])
                     if str(row.get("id")) == str(chat_id)), {})

    def contacts(self, ids: list[int]) -> list[dict[str, Any]]:
        if not ids:
            return []
        payload = self._roundtrip(32, {"contactIds": ids[:100]})
        return list((payload or {}).get("contacts") or [])

    def _fresh_write_connection(self) -> None:
        # websocket.connected is local state; an idle remote close does not
        # necessarily clear it. Never reuse that socket for a user write.
        self.close()
        try:
            self.connect()
        except Exception as error:
            raise MaxSendNotSubmittedError(
                "Не удалось подключиться к MAX. Сообщение не отправлено; попробуйте ещё раз."
            ) from error

    def send(self, *, chat_id: int | str, text: str) -> dict[str, Any]:
        with self._lock:
            self._fresh_write_connection()
            return self._send_message(chat_id=chat_id, text=text)

    def _send_message(self, *, chat_id: int | str, text: str,
                      reply_to: str = '', cid: int | None = None,
                      sticker: dict | None = None, attaches: list | None = None) -> dict[str, Any]:
        message = {'text':text, 'cid':cid if cid is not None else int(time.time()*1000),
                   'elements':[], 'attaches':list(attaches or [])}
        if reply_to: message['link']={'type':'REPLY','messageId':int(reply_to)}
        if sticker is not None:
            # Received stickerId is the opaque server attachment ID. The
            # top-level shortcut instead addresses a sticker within a set.
            # Reuse the actual attachment form, not the local-index shortcut.
            item={'_type':'STICKER','stickerId':int(sticker['stickerId']),
                  'setId':int(sticker['setId'])}
            if sticker.get('stickerType'): item['stickerType']=sticker['stickerType']
            message['attaches'].append(item)
        payload = self._roundtrip(
            OP_SEND,
            {
                "chatId": int(chat_id),
                "message": message,
                "notify": True,
            },
        )
        message_id = ((payload or {}).get("message") or {}).get("id")
        if message_id is None or str(message_id) == "":
            raise RuntimeError("MAX send returned no message receipt; do not retry")
        if str((payload or {}).get("chatId", chat_id)) != str(chat_id):
            raise RuntimeError("MAX send receipt belongs to another chat; do not retry")
        return {
            "chat_id": str((payload or {}).get("chatId") or chat_id),
            "message_id": str(message_id),
            "receipt": "max-ws-accepted",
        }

    def send_bound(self, *, chat_id: str, text: str, reply_to: str, cid: int,
                   sticker: dict | None = None, attaches: list | None = None) -> dict:
        """Already approved operation; same transport and authorization.

        ReplyLink follows PyMax MessageService/ReplyLink. Sticker fields follow
        the documented observed MSG_SEND frame; no URL/session copied.
        """
        with self._lock:
            self._fresh_write_connection()
            return self._send_message(chat_id=chat_id,text=text,reply_to=reply_to,cid=cid,sticker=sticker,attaches=attaches)

    def media_reference(self, *, chat_id: str, message_id: str, attachment: dict) -> dict:
        """Fetch download metadata for the exact native source; never copy a token."""
        typ=str(attachment.get('_type') or '')
        if typ=='FILE':
            return self._roundtrip(88,{'chatId':int(chat_id),'messageId':int(message_id),
                                     'fileId':attachment['fileId']}) or {}
        if typ in {'AUDIO','VIDEO'}:
            value=attachment.get('videoId') or attachment.get('audioId')
            if value is None: raise ValueError('native media id unavailable')
            return self._roundtrip(83,{'chatId':int(chat_id),'messageId':int(message_id),'videoId':value,
                                     **({'token':attachment['token']} if attachment.get('token') else {})}) or {}
        raise ValueError('native media reference unsupported')

    def react(self, *, chat_id: str, message_id: str, emoji: str) -> dict:
        # PyMax AddReactionPayload / Opcode.MSG_REACTION =178.
        with self._lock:
            self._fresh_write_connection()
            payload={'chatId':int(chat_id),'messageId':int(message_id)}
            if emoji: payload['reaction']={'reactionType':'EMOJI','id':emoji}
            result=self._roundtrip(OP_REACTION if emoji else OP_CANCEL_REACTION,payload)
            if not isinstance(result,dict) or not isinstance(result.get('reactionInfo'),dict):
                raise RuntimeError('MAX did not return a reaction receipt; do not retry')
            return result

    def reactions(self, *, chat_id: str, message_id: str) -> dict:
        # Side-effect-free native read; IDs must be numbers on the wire.
        result=self._roundtrip(OP_GET_REACTIONS,{'chatId':int(chat_id),
                                              'messageIds':[int(message_id)]})
        rows=result.get('messagesReactions') if isinstance(result,dict) else None
        if not isinstance(rows,dict): raise ValueError('MAX reaction readback unavailable')
        current=rows.get(str(message_id),{})
        if not isinstance(current,dict): raise ValueError('invalid MAX reaction readback')
        return current

    def typing(self, *, chat_id: str, active: bool) -> dict:
        # MSG_TYPING only starts an expiring indicator. A stop must not
        # accidentally issue CHAT_ACTIVITY (which marks messages read).
        if not active: return {'status':'expires_automatically'}
        with self._lock:
            self._ensure()
            self._roundtrip(OP_TYPING,{'chatId':int(chat_id)})
            return {'status':'provider_acknowledged'}

    def get_message(self, *, chat_id: int | str, message_id: int | str) -> dict[str, Any]:
        payload = self._roundtrip(OP_GET_MESSAGE, {
            "chatId": int(chat_id), "messageIds": [int(message_id)],
        })
        return next((row for row in (payload or {}).get("messages", [])
                     if str(row.get("id")) == str(message_id)), {})

    def edit(self, *, chat_id: int | str, message_id: int | str, text: str,
             expected_text: str) -> dict[str, Any]:
        with self._lock:
            self._fresh_write_connection()
            original = self.get_message(chat_id=chat_id, message_id=message_id)
            own = str(((self.profile or {}).get("profile") or {}).get("contact", {}).get("id"))
            if not original or str(original.get("sender")) != own:
                raise ValueError("Можно изменять только свои сообщения MAX")
            if original.get("text", "") != expected_text:
                raise ValueError("Сообщение уже изменилось. Обновите диалог.")
            # SHARE is an automatically generated URL preview, not an uploaded
            # file. The replacement text regenerates its links/formatting;
            # copying old offsets or previews would retain the previous URL.
            if any(not isinstance(item, dict) or item.get("_type") != "SHARE"
                   for item in original.get("attaches") or []):
                raise ValueError("Редактирование сообщений с вложениями пока недоступно")
            payload = self._roundtrip(OP_EDIT, {
                "chatId": int(chat_id), "messageId": int(message_id), "text": text,
                "elements": [], "attachments": [],
            })
            result = (payload or {}).get("message") or {}
            if str(result.get("id")) != str(message_id) or result.get("text") != text:
                raise RuntimeError("MAX не подтвердил изменение. Обновите диалог; не повторяйте автоматически.")
            # Some ACKs omit immutable origin fields. Fetch the authoritative
            # provider row before projecting the edit into the local mirror.
            row = self.get_message(chat_id=chat_id, message_id=message_id)
            if not row or row.get("text") != text or str(row.get("sender")) != own:
                raise RuntimeError("Не удалось проверить изменение в MAX. Обновите диалог.")
            return row

    def dispatch(self, *, timeout: float = 25.0) -> dict[str, Any] | None:
        """Read one DISPATCH frame if it arrives before the timeout (and keep the heartbeat due)."""
        with self._lock:
            if self._pending:
                return self._pending.popleft()
            self._ensure()
            self._heartbeat_if_due()
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                frame = self._transport.recv(self._ws, max(1.0, deadline - time.monotonic()))
                if frame is None:
                    continue
                if frame.get('opcode')==OP_FILE_READY and frame.get('cmd')==0:
                    self._remember_file_ready(frame)
                if frame.get("opcode") == OP_DISPATCH:
                    return frame.get("payload") or {}
                # A PING ACK is not a new server request: replying to it would
                # create an endless heartbeat feedback loop.
            return None


def dispatch_to_message(payload: Any) -> dict[str, Any] | None:
    """Best-effort extraction of a chat message from a DISPATCH payload.

    Returns ``{chat_id, message_id, sender, text, time, out}`` or ``None`` when
    the event carries no user-visible message (typing, presence, edits...).
    """
    if not isinstance(payload, dict):
        return None
    message = payload.get("message") if isinstance(payload.get("message"), dict) else payload
    chat_id = next((value for value in (
        payload.get("chatId"), message.get("chatId"), message.get("chat_id")
    ) if value is not None), None)
    text = message.get("text")
    message_id = message.get("id")
    if chat_id is None or message_id is None or (
        not str(text or "").strip() and not message.get("attaches")
    ):
        return None
    sender = message.get("sender")
    result = {
        "chat_id": str(chat_id),
        "message_id": str(message_id),
        "sender": "" if sender is None else str(sender),
        "text": str(text or ""),
        "time": message.get("time"),
        "out": bool(message.get("out")),
    }
    if message.get("attaches"):
        result["attaches"] = message["attaches"]
    return result
