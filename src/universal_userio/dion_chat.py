"""Native personal DION chats for UserIO.

The transport mirrors DION's production web client protocol:
P-256 ECDH handshake, HKDF-SHA256, AES-GCM JSON envelopes, and stable
intermediate message IDs for reconciliation. Browser automation is not used.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
from typing import Any, Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .account_connections import ConnectorSecrets
from .contracts import ChannelFile, DeliveryNotSubmittedError, InboxMessage
from .dion_auth import DionAuthBroker, DionAuthError, DionReauthRequired

DION_CHATS_API = "https://chats-api.dion.vc"
DION_FILES_API = "https://dc-data-storage.dion.vc"
_MAX_FILE = 20 * 1024 * 1024
_PROVIDER_ID = re.compile(r"^[A-Za-z0-9_-]{1,160}$")
_MAX_JSON = 4_000_000


class DionChatError(RuntimeError):
    pass


class DionChatRejected(DeliveryNotSubmittedError):
    pass


class DionChatUncertain(DionChatError):
    """Provider may have received a side effect; caller must reconcile."""


def _time(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0




def _overlap_cursor(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    dt = dt - timedelta(milliseconds=1)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

def _body(message: Mapping[str, Any]) -> str:
    content = str(message.get("content") or "").strip()
    if content:
        return content
    formatted = message.get("formatted_content")
    if isinstance(formatted, Mapping):
        parts = []
        for element in formatted.get("elements") or []:
            if isinstance(element, Mapping) and element.get("text"):
                parts.append(str(element["text"]))
        text = "".join(parts).strip()
        if text:
            return text
    if message.get("attachments"):
        return "[Вложение DION]"
    return ""


def _attachments(row: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Project the provider DTO, never a client-supplied download URL."""
    result = []
    items = row.get("attachments")
    if items is None:
        return ()
    if not isinstance(items, list):
        raise DionChatError("DION attachments have an invalid shape")
    for item in items:
        if not isinstance(item, Mapping):
            raise DionChatError("DION attachment has an invalid shape")
        aid = str(item.get("id") or "")
        if not _PROVIDER_ID.fullmatch(aid):
            raise DionChatError("DION attachment identity is missing")
        name = str(item.get("name") or "attachment").replace("\\", "/").rsplit("/", 1)[-1]
        name = "".join(c for c in name if c.isprintable())[:255] or "attachment"
        mime = str(item.get("mime_type") or "application/octet-stream").split(";", 1)[0].strip().lower()
        if not re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", mime):
            mime = "application/octet-stream"
        size = item.get("size")
        if type(size) is not int or size < 0:
            size = None
        kind = mime.split("/", 1)[0]
        if kind not in {"image", "video", "audio"}:
            kind = "file"
        result.append({"idx": len(result), "kind": kind, "filename": name, "content_type": mime,
                       "size": size, "attachment_id": aid, "provider_ref": aid})
    return tuple(result)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "DION redirect refused", headers, fp)


class DionCryptoSession:
    def __init__(self) -> None:
        self.session_id = ""
        self._aes: AESGCM | None = None

    @property
    def ready(self) -> bool:
        return bool(self.session_id and self._aes)

    def reset(self) -> None:
        self.session_id = ""
        self._aes = None

    def establish(self, *, access_token: str, opener: Any) -> None:
        private = ec.generate_private_key(ec.SECP256R1())
        public = private.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
        request = urllib.request.Request(
            DION_CHATS_API + "/v1/crypto/sessions",
            method="POST",
            data=json.dumps({
                "client_public_key": base64.b64encode(public).decode("ascii"),
            }, separators=(",", ":")).encode(),
            headers={
                "Authorization": "Bearer " + access_token,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Dc-Encode-Codec-Type": "b64",
                "Origin": "https://dion.vc",
                "Referer": "https://dion.vc/",
                "User-Agent": "Universal-UserIO/1",
            },
        )
        try:
            with opener(request, timeout=12) as response:
                raw = response.read(_MAX_JSON + 1)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise DionReauthRequired("DION crypto session rejected authentication") from exc
            raise DionChatError(f"DION crypto handshake failed ({exc.code})") from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise DionChatError("DION crypto handshake is unavailable") from exc
        if len(raw) > _MAX_JSON:
            raise DionChatError("DION crypto handshake response is too large")
        try:
            payload = json.loads(raw)
            session_id = str(payload["session_id"])
            server_raw = base64.b64decode(str(payload["server_public_key"]), validate=True)
            server_public = ec.EllipticCurvePublicKey.from_encoded_point(
                ec.SECP256R1(), server_raw,
            )
        except (KeyError, ValueError, TypeError, binascii.Error, json.JSONDecodeError) as exc:
            raise DionChatError("DION returned an invalid crypto handshake") from exc
        shared = private.exchange(ec.ECDH(), server_public)
        key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"",
            info=b"",
        ).derive(shared)
        self.session_id = session_id
        self._aes = AESGCM(key)

    def encrypt_file(self, data: bytes) -> bytes:
        if self._aes is None:
            raise DionChatError("DION crypto session is not initialized")
        iv = os.urandom(12)
        return iv + self._aes.encrypt(iv, data, None)

    def decrypt_file(self, data: bytes) -> bytes:
        if self._aes is None:
            raise DionChatError("DION crypto session is not initialized")
        if len(data) < 28:
            raise DionChatError("DION encrypted file is truncated")
        try:
            return self._aes.decrypt(data[:12], data[12:], None)
        except (InvalidTag, ValueError) as exc:
            raise DionChatError("DION encrypted file authentication failed") from exc

    def encrypt_json(self, value: Any) -> str:
        if self._aes is None:
            raise DionChatError("DION crypto session is not initialized")
        iv = os.urandom(12)
        plain = json.dumps(
            value, ensure_ascii=False, separators=(",", ":"),
        ).encode()
        encrypted = self._aes.encrypt(iv, plain, None)
        return base64.b64encode(iv + encrypted).decode("ascii")

    def decrypt_json(self, value: str) -> Any:
        if self._aes is None:
            raise DionChatError("DION crypto session is not initialized")
        try:
            raw = base64.b64decode(value, validate=True)
            if len(raw) <= 12:
                raise ValueError("short encrypted payload")
            plain = self._aes.decrypt(raw[:12], raw[12:], None)
            return json.loads(plain)
        except (ValueError, TypeError, binascii.Error, InvalidTag, json.JSONDecodeError) as exc:
            raise DionChatError("DION returned an invalid encrypted payload") from exc


class DionChatClient:
    def __init__(
        self,
        broker: DionAuthBroker,
        account_id: str,
        *,
        opener: Any = None,
    ) -> None:
        self.broker = broker
        self.account_id = account_id
        self.cookies = CookieJar()
        self.opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirect(),
            urllib.request.HTTPCookieProcessor(self.cookies),
        ).open
        self.crypto = DionCryptoSession()

    def _ensure_crypto(self, *, refresh: bool = False) -> str:
        if refresh:
            self.crypto.reset()
        token = self.broker.access_token(self.account_id)
        if not self.crypto.ready:
            try:
                self.crypto.establish(access_token=token, opener=self.opener)
            except DionReauthRequired:
                token = str(self.broker.refresh(
                    self.account_id, rejected_access_token=token,
                )["access_token"])
                self.crypto.establish(access_token=token, opener=self.opener)
        return token

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
        side_effect: bool = False,
    ) -> dict[str, Any]:
        if not path.startswith("/"):
            raise ValueError("DION chat path must be absolute")
        query_pairs: list[tuple[str, str]] = []
        for key, value in (query or {}).items():
            if value is None or value == "":
                continue
            if isinstance(value, (list, tuple)):
                query_pairs.extend((key, str(item)) for item in value)
            else:
                query_pairs.append((key, str(value)))
        url = DION_CHATS_API + path
        if query_pairs:
            url += "?" + urllib.parse.urlencode(query_pairs)

        for attempt in range(2):
            token = self._ensure_crypto(refresh=attempt > 0)
            headers = {
                "Authorization": "Bearer " + token,
                "Accept": "application/json",
                "Dc-Crypto-Session-Id": self.crypto.session_id,
                "Dc-Encoded-Data-Over-Json": "true",
                "Dc-Encode-Codec-Type": "b64",
                "Origin": "https://dion.vc",
                "Referer": "https://dion.vc/",
                "User-Agent": "Universal-UserIO/1",
            }
            raw_body = None
            if body is not None:
                headers["Content-Type"] = "application/json"
                raw_body = json.dumps({
                    "encrypted": self.crypto.encrypt_json(dict(body)),
                }, separators=(",", ":")).encode()
            request = urllib.request.Request(
                url, method=method, data=raw_body, headers=headers,
            )
            try:
                with self.opener(request, timeout=15) as response:
                    raw = response.read(_MAX_JSON + 1)
                    status = response.status
            except urllib.error.HTTPError as exc:
                status = exc.code
                raw = exc.read(_MAX_JSON + 1)
                if status == 428 and attempt == 0:
                    self.crypto.reset()
                    continue
                if status in (401, 403) and attempt == 0:
                    self.broker.refresh(self.account_id, rejected_access_token=token)
                    self.crypto.reset()
                    continue
                if side_effect and status in (400, 401, 403, 404, 409, 422):
                    raise DionChatRejected(f"DION rejected message ({status})") from exc
                raise DionChatError(f"DION chat request failed ({status})") from exc
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                if side_effect:
                    raise DionChatUncertain("DION send outcome is unknown") from exc
                raise DionChatError("DION chats are unavailable") from exc

            if len(raw) > _MAX_JSON:
                raise DionChatError("DION chat response is too large")
            if not raw:
                return {}
            try:
                envelope = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise DionChatError("DION returned invalid JSON") from exc
            if envelope == {}:
                return {}
            if not isinstance(envelope, Mapping):
                raise DionChatError("DION returned invalid response shape")
            encrypted = envelope.get("encrypted")
            if not isinstance(encrypted, str):
                if 200 <= status < 300:
                    raise DionChatError("DION returned an unencrypted success payload")
                raise DionChatError("DION returned an error")
            result = self.crypto.decrypt_json(encrypted)
            if not isinstance(result, dict):
                raise DionChatError("DION decrypted response is invalid")
            return result
        raise DionChatError("DION crypto session could not be established")

    def list_conversations(self, *, cursor: str = "", limit: int = 50) -> dict[str, Any]:
        return self._request(
            "GET", "/v2/conversations",
            query={"limit": max(1, min(int(limit), 100)), "cursor": cursor or None},
        )

    def history(
        self, conversation_id: str, *, after: str = "", before: str = "", limit: int = 100,
    ) -> dict[str, Any]:
        conversation_id = urllib.parse.quote(str(conversation_id), safe="")
        return self._request(
            "GET", f"/v1/conversations/{conversation_id}/history",
            query={
                "after": after or None,
                "before": before or None,
                "limit": max(1, min(int(limit), 100)),
            },
        )

    def message(self, message_id: str) -> dict[str, Any]:
        if not _PROVIDER_ID.fullmatch(message_id):
            raise ValueError("invalid DION message ID")
        return self._request("GET", "/v1/messages/" + message_id)

    def _file_request(self, attachment_id: str = "", *, limit: int = _MAX_FILE,
                      data: bytes | None = None, filename: str = "", mime_type: str = ""):
        """File API is fixed by the public platform configuration, not input URLs."""
        upload = data is not None
        if not upload and not _PROVIDER_ID.fullmatch(attachment_id):
            raise ValueError("invalid DION attachment ID")
        if type(limit) is not int or not 0 < limit <= _MAX_FILE:
            raise ValueError("invalid DION file size limit")
        if upload and (not data or len(data) > limit):
            raise DionChatRejected("DION upload size is outside the supported range")
        url = DION_FILES_API + "/v1/attachments" + ("" if upload else "/" + attachment_id)
        for attempt in range(2):
            token = self._ensure_crypto(refresh=attempt > 0)
            headers = {
                "Authorization": "Bearer " + token,
                "Dc-Crypto-Session-Id": self.crypto.session_id,
                "Dc-Encoded-Data-Over-Json": "true",
                "Dc-Encode-Codec-Type": "b64",
                "Origin": "https://dion.vc",
                "Referer": "https://dion.vc/",
                "Accept": "application/json" if upload else "*/*",
            }
            body = None
            if upload:
                # RFC 7578: a single encrypted file part; strip header injection.
                safe_name = "".join(c for c in filename if c.isprintable() and c not in '\"\r\n')[:255] or "attachment"
                if not re.fullmatch(r"[a-zA-Z0-9!#$&^_.+-]+/[a-zA-Z0-9!#$&^_.+-]+", mime_type):
                    mime_type = "application/octet-stream"
                boundary = "userio-" + uuid.uuid4().hex
                headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
                prefix = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{safe_name}\"\r\n"
                          f"Content-Type: {mime_type}\r\n\r\n").encode("utf-8")
                body = prefix + self.crypto.encrypt_file(data) + f"\r\n--{boundary}--\r\n".encode()
            req = urllib.request.Request(url, method="POST" if upload else "GET", headers=headers, data=body)
            maximum = _MAX_JSON if upload else limit + 28
            try:
                with self.opener(req, timeout=30) as response:
                    raw = response.read(maximum + 1)
                    status = response.status
            except urllib.error.HTTPError as exc:
                if attempt == 0 and exc.code in (401, 428):
                    if exc.code == 401:
                        self.broker.refresh(self.account_id, rejected_access_token=token)
                    self.crypto.reset()
                    continue
                if upload:
                    raise DionChatUncertain("DION file upload was not confirmed") from exc
                raise DionChatError(f"DION file download failed ({exc.code})") from exc
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                if upload:
                    raise DionChatUncertain("DION file upload outcome is unknown") from exc
                raise DionChatError("DION file download unavailable") from exc
            if not 200 <= status < 300 or len(raw) > maximum:
                if upload:
                    raise DionChatUncertain("DION file upload response invalid")
                raise DionChatError("DION file response invalid or exceeds limit")
            if not upload:
                plain = self.crypto.decrypt_file(raw)
                if len(plain) > limit:
                    raise DionChatError("DION file exceeds size limit")
                return plain
            try:
                result = self.crypto.decrypt_json(json.loads(raw)["encrypted"])
                if not isinstance(result, Mapping) or not _PROVIDER_ID.fullmatch(str(result.get("id") or "")):
                    raise ValueError("invalid attachment ID")
                return dict(result)
            except (ValueError, KeyError, TypeError, DionChatError) as exc:
                raise DionChatUncertain("DION file upload receipt unavailable") from exc
        raise DionChatError("DION file session could not be established")

    def download_attachment(self, attachment_id: str, *, max_bytes: int = _MAX_FILE) -> bytes:
        return self._file_request(attachment_id, limit=max_bytes)

    def upload_attachment(self, data: bytes, *, filename: str, mime_type: str) -> dict[str, Any]:
        return self._file_request(data=data, filename=filename, mime_type=mime_type)

    def create_conversation(self, *, title: str, kind: str,
                            participant_ids: tuple[str, ...] = ()) -> dict[str, Any]:
        """Create one native DION conversation; creation cannot be blindly replayed."""
        if kind not in {"GROUP", "CHANNEL"} or not isinstance(title, str) or not title.strip() or len(title.strip()) > 128:
            raise DionChatRejected("DION invalid conversation title or type")
        if len(participant_ids) > 50 or len(set(participant_ids)) != len(participant_ids) or any(
                not isinstance(value, str) or not _PROVIDER_ID.fullmatch(value) for value in participant_ids):
            raise DionChatRejected("DION invalid conversation member IDs")
        result = self._request("POST", "/v1/conversations",
            body={"name": title.strip(), "conversation_type": kind,
                  "members_id": list(participant_ids)}, side_effect=True)
        peer = str(result.get("conversation_id") or "")
        if not _PROVIDER_ID.fullmatch(peer) or result.get("conversation_type") != kind:
            raise DionChatUncertain("DION conversation creation receipt is ambiguous")
        return result

    def conversation(self, conversation_id: str) -> dict[str, Any]:
        cid = urllib.parse.quote(str(conversation_id), safe="")
        return self._request("GET", f"/v1/conversations/{cid}")

    def by_intermediate(self, conversation_id: str, intermediate_id: str) -> dict[str, Any] | None:
        cid = urllib.parse.quote(str(conversation_id), safe="")
        result = self._request(
            "GET", f"/v1/conversations/{cid}/intermediate",
            query={"ids": [intermediate_id]},
        )
        rows = result.get("messages") or []
        for row in rows:
            if isinstance(row, Mapping) and str(row.get("intermediate_id") or "") == intermediate_id:
                return dict(row)
        return None

    @staticmethod
    def intermediate_id(account_id: str, conversation_id: str, request_id: str) -> str:
        return str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"userio:dion:{account_id}:{conversation_id}:{request_id}",
        ))

    def send_text(self, conversation_id: str, text: str, *, request_id: str,
                  attachment_ids: tuple[str, ...] = (), reply_to: str = "") -> str:
        text = str(text)
        if (not text.strip() and not attachment_ids) or len(text) > 50_000:
            raise DionChatRejected("DION message must contain text")
        if len(attachment_ids) > 8 or any(not _PROVIDER_ID.fullmatch(a) for a in attachment_ids):
            raise DionChatRejected("DION attachment IDs are invalid")
        if reply_to and not _PROVIDER_ID.fullmatch(reply_to):
            raise DionChatRejected("DION reply target is invalid")
        intermediate = self.intermediate_id(self.account_id, conversation_id, request_id)
        existing = self.by_intermediate(conversation_id, intermediate)
        if existing and existing.get("message_id"):
            return f"dion:{conversation_id}:{intermediate}:{existing['message_id']}"

        info = self.conversation(conversation_id)
        permissions = set(info.get("permissions") or [])
        if "conversation.messages.create" not in permissions:
            raise DionChatRejected("DION conversation does not allow messages")

        response = self._request(
            "POST", "/v1/messages/batch",
            body={
                "conversation_id": conversation_id,
                "messages": [{
                    "formatted_content": {
                        "type": "rich_text",
                        "elements": [{"type": "text", "text": text}],
                    },
                    "intermediate_id": intermediate,
                    **({"attachments_ids": list(attachment_ids)} if attachment_ids else {}),
                    **({"reply_to_message_id": reply_to} if reply_to else {}),
                }],
            },
            side_effect=True,
        )
        results = response.get("messages") or []
        matching = next(
            (row for row in results if isinstance(row, Mapping)
             and str(row.get("intermediate_id") or "") == intermediate),
            None,
        )
        if not matching:
            raise DionChatUncertain("DION accepted no durable intermediate receipt")
        if matching.get("error"):
            raise DionChatRejected("DION rejected the message")
        actual = self.by_intermediate(conversation_id, intermediate)
        provider_id = str((actual or {}).get("message_id") or "")
        return f"dion:{conversation_id}:{intermediate}:{provider_id}"

    @staticmethod
    def parse_receipt(receipt: str) -> tuple[str, str, str]:
        if not receipt.startswith("dion:"):
            raise ValueError("not a DION receipt")
        body = receipt[5:]
        conversation_id, sep, rest = body.partition(":")
        if not sep:
            raise ValueError("invalid DION receipt")
        intermediate, sep, provider_id = rest.partition(":")
        if not intermediate:
            raise ValueError("invalid DION receipt")
        return conversation_id, intermediate, provider_id


def dion_message(
    row: Mapping[str, Any], *, account_id: str, own_user_id: str,
    title: str = "", conversation_type: str = "",
    reconciled: bool = False,
) -> InboxMessage | None:
    message_id = str(row.get("message_id") or "")
    conversation_id = str(row.get("conversation_id") or "")
    created = _time(row.get("created_at"))
    body = _body(row)
    if not message_id or not conversation_id or not created or not body:
        return None
    creator = row.get("creator") if isinstance(row.get("creator"), Mapping) else {}
    creator_id = str(row.get("creator_id") or creator.get("user_id") or "")
    creator_name = str(creator.get("name") or creator.get("email") or creator_id)[:160]
    outgoing = bool(creator_id and creator_id == own_user_id)
    return InboxMessage(
        source="dion",
        message_id=f"{account_id}|{conversation_id}:{message_id}",
        sender=creator_id or conversation_id,
        sender_name=creator_name,
        body=body[:50_000],
        attachments=_attachments(row),
        received_at=created,
        direction="outgoing" if outgoing else "incoming",
        conversation_kind="direct" if conversation_type == "DM" else (
            "channel" if conversation_type == "CHANNEL" else "group"
        ),
        peer_id=conversation_id,
        conversation_title=title[:320],
        reconciliation=reconciled,
        edited_at=_time(row.get("updated_at")) if row.get("updated_at") != row.get("created_at") else 0.0,
        provider_reply_to=str(row.get("reply_to_message_id") or ""),
    )


def _client(service: Any, user_id: str, account_id: str) -> tuple[DionChatClient, dict[str, Any]]:
    account = next(
        (item for item in service._store.accounts(user_id=user_id)
         if item["id"] == account_id and item["provider"] == "dion"
         and item["enabled"]),
        None,
    )
    if account is None:
        raise DionAuthError("DION account is unavailable")
    vault = ConnectorSecrets()
    broker = DionAuthBroker(service, user_id, vault=vault)
    secret = broker.load(account_id)
    return DionChatClient(broker, account_id), secret


def project_dion_conversation(service, user_id: str, account_id: str, row: Mapping[str, Any]):
    peer = str(row.get("conversation_id") or "")
    kind = {"GROUP": "group", "CHANNEL": "channel"}.get(row.get("conversation_type"))
    if kind is None:
        return None
    if not _PROVIDER_ID.fullmatch(peer):
        raise DionChatError("DION conversation identity invalid")
    title = str(row.get("name") or "").strip()[:320]
    store = service._store
    record = store.ensure_contact_conversation("dion", peer, user_id=user_id,
        account_ref=account_id, peer_id=peer, conversation_kind=kind, route_id="dion")
    if title:
        with store._lock, store._connection:
            store._connection.execute(
                "INSERT OR REPLACE INTO contact_names (user_id,source,sender,name,updated_at) VALUES (?,?,?,?,?)",
                (user_id, "dion", f"chat:{account_id}:{peer}", title, time.time()))
    return record


def poll_dion(service: Any, account: Mapping[str, Any]) -> None:
    user_id = str(account["user_id"])
    account_id = str(account["account_id"])
    client, secret = _client(service, user_id, account_id)
    own_user_id = str(secret.get("provider_user_id") or "")

    page_key = f"dion:conversation-page:{account_id}"
    page_cursor = str(service._store.user_preference(page_key, user_id=user_id) or "")
    page = client.list_conversations(cursor=page_cursor, limit=50)
    conversations = page.get("conversations")
    if not isinstance(conversations, list):
        # The web converter tolerates an empty object, but it does not prove
        # that an authenticated conversation list was returned. Do not claim
        # success or advance an existing cursor on that evidence.
        if page == {} or ("conversations" in page and conversations is None):
            service._store.set_user_preference(
                f"dion:poll-summary:{account_id}",
                json.dumps({"status": "empty_response_unverified", "conversations": None,
                            "observed_messages": None}), user_id=user_id,
            )
            return
        raise DionChatError("DION conversation list is invalid")

    imported = 0
    for conversation in conversations:
        if not isinstance(conversation, Mapping):
            raise DionChatError("DION conversation has invalid shape")
        conversation_id = str(conversation.get("conversation_id") or "")
        if not conversation_id:
            raise DionChatError("DION conversation ID missing")
        project_dion_conversation(service, user_id, account_id, conversation)
        cursor_key = f"dion:history:{account_id}:{conversation_id}"
        stored_after = str(service._store.user_preference(cursor_key, user_id=user_id) or "")
        after = stored_after
        newest = stored_after
        for _ in range(4):
            history = client.history(conversation_id, after=after, limit=100)
            rows = history.get("messages")
            if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
                raise DionChatError("DION history response has invalid shape")
            if any(str(row.get("conversation_id") or "") != conversation_id for row in rows):
                raise DionChatError("DION history conversation mismatch")
            rows.sort(key=lambda row: (_time(row.get("created_at")), str(row.get("message_id") or "")))
            if not rows:
                break
            page_newest = after
            for row in rows:
                message = dion_message(
                    row, account_id=account_id, own_user_id=own_user_id,
                    title=str(conversation.get("name") or ""),
                    conversation_type=str(conversation.get("conversation_type") or ""),
                )
                if message is not None:
                    service.receive(
                        message, route_id="dion", user_id=user_id, account_ref=account_id,
                    )
                    imported += 1
                created_raw = str(row.get("created_at") or "")
                if created_raw and _time(created_raw) >= _time(page_newest):
                    page_newest = created_raw
                if created_raw and _time(created_raw) >= _time(newest):
                    newest = created_raw
            if len(rows) < 100 or not page_newest or _time(page_newest) <= _time(after):
                break
            # Deliberate one-millisecond overlap: duplicates are idempotent,
            # whereas a hard timestamp boundary can lose simultaneous messages.
            after = _overlap_cursor(page_newest)

        if newest and newest != stored_after:
            service._store.set_user_preference(
                cursor_key, _overlap_cursor(newest), user_id=user_id,
            )

    next_cursor = str(page.get("next_cursor") or "")
    # Walk older conversation pages across polling cycles, then return to the
    # first/recent page. This bounds each provider poll without starving chats.
    service._store.set_user_preference(
        page_key, next_cursor, user_id=user_id,
    )
    service._store.set_user_preference(
        f"dion:poll-summary:{account_id}",
        json.dumps({"status": "complete_page", "conversations": len(conversations), "observed_messages": imported}), user_id=user_id,
    )


def prepare_dion_reply(service: Any, conversation: Mapping[str, Any], draft: Any, *, user_id: str):
    account_id = str(conversation.get("account_ref") or "")
    peer_id = str(conversation.get("peer_id") or "")
    if not account_id.startswith("dion:") or not peer_id:
        raise DionChatRejected("DION conversation is not addressable")
    client, _ = _client(service, user_id, account_id)
    return lambda: client.send_text(peer_id, draft.body, request_id=draft.id)


def reconcile_dion_receipt(
    service: Any, conversation: Mapping[str, Any], receipt: str, *, user_id: str,
) -> None:
    account_id = str(conversation.get("account_ref") or "")
    peer_id = str(conversation.get("peer_id") or "")
    receipt_peer, intermediate, provider_id = DionChatClient.parse_receipt(receipt)
    if receipt_peer != peer_id:
        raise ValueError("DION receipt conversation mismatch")
    client, secret = _client(service, user_id, account_id)
    row = client.by_intermediate(peer_id, intermediate)
    if row is None:
        raise DionChatError("DION accepted message is not visible yet")
    if provider_id and str(row.get("message_id") or "") != provider_id:
        raise ValueError("DION receipt message mismatch")
    info = client.conversation(peer_id)
    message = dion_message(
        row, account_id=account_id,
        own_user_id=str(secret.get("provider_user_id") or ""),
        title=str(info.get("name") or ""),
        conversation_type=str(info.get("conversation_type") or ""),
        reconciled=True,
    )
    if message is None or message.direction != "outgoing":
        raise ValueError("DION receipt did not resolve to own message")
    service.receive(
        message, route_id=str(conversation["route_id"]),
        user_id=user_id, account_ref=account_id,
    )

def _owned_message(service, user_id, message_id, *, account_id=None):
    message = service._store.message(message_id, source="dion", user_id=user_id)
    if not message:
        raise KeyError("DION message not found")
    conversation = service._store.conversation(str(message["conversation_id"]), user_id=user_id)
    if not conversation or conversation["source"] != "dion":
        raise KeyError("DION conversation not found")
    actual_account = str(conversation["account_ref"])
    peer = str(conversation["peer_id"])
    if account_id is not None and actual_account != account_id:
        raise PermissionError("DION account mismatch")
    prefix = actual_account + "|" + peer + ":"
    if not message_id.startswith(prefix) or not _PROVIDER_ID.fullmatch(message_id[len(prefix):]):
        raise ValueError("DION message reference is invalid")
    account = next((a for a in service._store.accounts(user_id=user_id)
                    if a["id"] == actual_account and a["provider"] == "dion"), None)
    if not account or not account["enabled"] or "read" not in account["capabilities"]:
        raise PermissionError("DION account read capability is unavailable")
    return message, conversation, message_id[len(prefix):]


def download_owned_attachment(service, user_id, message_id, *, attachment_index=0,
                              max_bytes=None, account_id=None):
    if type(attachment_index) is not int or not 0 <= attachment_index < 8:
        raise ValueError("invalid_attachment_index")
    if max_bytes is None:
        max_bytes = _MAX_FILE
    if type(max_bytes) is not int or not 0 < max_bytes <= _MAX_FILE:
        raise ValueError("invalid DION file size limit")
    message, conversation, provider_id = _owned_message(
        service, user_id, message_id, account_id=account_id)
    stored = next((a for a in message.get("attachments", [])
                   if a["idx"] == attachment_index), None)
    if not stored or not stored.get("attachment_id"):
        raise KeyError("DION attachment not found")
    client, _ = _client(service, user_id, conversation["account_ref"])
    row = client.message(provider_id)
    if (str(row.get("message_id") or "") != provider_id
            or str(row.get("conversation_id") or "") != conversation["peer_id"]):
        raise DionChatError("DION attachment source mismatch")
    attachments = _attachments(row)
    if attachment_index >= len(attachments):
        raise DionChatError("DION attachment no longer exists")
    selected = attachments[attachment_index]
    if selected["attachment_id"] != stored["attachment_id"]:
        raise DionChatError("DION attachment changed")
    size = selected.get("size")
    if size is not None and size > max_bytes:
        raise DionChatError("DION file exceeds size limit")
    data = client.download_attachment(selected["attachment_id"], max_bytes=max_bytes)
    if size is not None and size != len(data):
        raise DionChatError("DION file length mismatch")
    return ChannelFile(filename=selected["filename"], content_type=selected["content_type"], data=data)
