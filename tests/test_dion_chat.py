from __future__ import annotations

import base64
import json
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from universal_userio.dion_chat import DionCryptoSession, DionChatClient, dion_message


class Response:
    def __init__(self, payload, status=200, headers=None):
        self.payload = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.status = status
        self.headers = headers or {}
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, limit=-1):
        return self.payload[:limit] if limit >= 0 else self.payload


def test_crypto_session_matches_dion_webcrypto_wire():
    server_private = ec.generate_private_key(ec.SECP256R1())
    server_public_raw = server_private.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    server_aes = {}

    def opener(request, timeout=0):
        assert request.full_url.endswith("/v1/crypto/sessions")
        assert request.get_header("Authorization") == "Bearer access-token"
        payload = json.loads(request.data)
        client_raw = base64.b64decode(payload["client_public_key"])
        client_public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), client_raw)
        shared = server_private.exchange(ec.ECDH(), client_public)
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"", info=b"").derive(shared)
        server_aes["value"] = AESGCM(key)
        return Response({
            "server_public_key": base64.b64encode(server_public_raw).decode(),
            "session_id": "session-1",
        })

    crypto = DionCryptoSession()
    crypto.establish(access_token="access-token", opener=opener)
    assert crypto.session_id == "session-1"

    encrypted = base64.b64decode(crypto.encrypt_json({"hello": "мир"}))
    decoded = server_aes["value"].decrypt(encrypted[:12], encrypted[12:], None)
    assert json.loads(decoded) == {"hello": "мир"}

    iv = os.urandom(12)
    reply = iv + server_aes["value"].encrypt(iv, json.dumps({"ok": True}).encode(), None)
    assert crypto.decrypt_json(base64.b64encode(reply).decode()) == {"ok": True}


def test_intermediate_id_is_stable_per_draft_and_destination():
    a = DionChatClient.intermediate_id("dion:o:u", "room-a", "draft-1")
    b = DionChatClient.intermediate_id("dion:o:u", "room-a", "draft-1")
    c = DionChatClient.intermediate_id("dion:o:u", "room-b", "draft-1")
    assert a == b
    assert a != c


def test_message_uses_conversation_uuid_and_provider_message_id():
    row = {
        "message_id": "m-42",
        "conversation_id": "conv-abc",
        "creator_id": "peer-7",
        "creator": {"user_id": "peer-7", "name": "Peer"},
        "content": "hello",
        "created_at": "2026-11-26T12:00:00Z",
        "updated_at": "2026-11-26T12:00:00Z",
        "attachments": [],
    }
    message = dion_message(
        row, account_id="dion:org:user", own_user_id="user",
        title="Team", conversation_type="GROUP",
    )
    assert message is not None
    assert message.source == "dion"
    assert message.peer_id == "conv-abc"
    assert message.message_id == "dion:org:user|conv-abc:m-42"
    assert message.sender == "peer-7"
    assert message.conversation_kind == "group"


def test_auth_rejection_refresh_is_bound_to_the_rejected_access_token():
    import urllib.error

    class Broker:
        def __init__(self):
            self.token = "old"
            self.rejected = []
        def access_token(self, account_id):
            return self.token
        def refresh(self, account_id, *, rejected_access_token=None):
            self.rejected.append(rejected_access_token)
            self.token = "new"
            return {"access_token": self.token}

    class Crypto:
        ready = True
        session_id = "fixture"
        def reset(self):
            pass

    attempts = []
    def opener(request, timeout):
        attempts.append(request.get_header("Authorization"))
        if len(attempts) == 1:
            raise urllib.error.HTTPError(request.full_url, 401, "expired", {}, None)
        return Response({})

    broker = Broker()
    client = DionChatClient(broker, "fixture", opener=opener)
    client.crypto = Crypto()
    assert client.list_conversations() == {}
    assert attempts == ["Bearer old", "Bearer new"]
    assert broker.rejected == ["old"]


class FakeClient(DionChatClient):
    def __init__(self):
        self.account_id = "dion:org:user"
        self.posts = []
        self.lookup = None
    def by_intermediate(self, conversation_id, intermediate_id):
        return self.lookup
    def conversation(self, conversation_id):
        return {"permissions": ["conversation.messages.create"]}
    def _request(self, method, path, *, query=None, body=None, side_effect=False):
        self.posts.append((method, path, body, side_effect))
        return {"messages": [{"intermediate_id": body["messages"][0]["intermediate_id"]}]}


def test_send_uses_batch_and_reconciles_by_intermediate_id():
    client = FakeClient()
    receipt = client.send_text("room-1", "hello", request_id="draft-x")
    assert receipt.startswith("dion:room-1:")
    assert len(client.posts) == 1
    method, path, body, side_effect = client.posts[0]
    assert method == "POST" and path == "/v1/messages/batch" and side_effect is True
    assert body["conversation_id"] == "room-1"
    assert body["messages"][0]["formatted_content"] == {
        "type": "rich_text",
        "elements": [{"type": "text", "text": "hello"}],
    }

    intermediate = body["messages"][0]["intermediate_id"]
    client.lookup = {
        "intermediate_id": intermediate,
        "message_id": "provider-99",
    }
    receipt2 = client.send_text("room-1", "hello", request_id="draft-x")
    assert receipt2.endswith(":provider-99")
    assert len(client.posts) == 1


def test_unified_channels_reads_stored_dion_conversation(tmp_path):
    from universal_userio.adapters import UnifiedChannels
    from universal_userio.contracts import InboxMessage
    from universal_userio.service import UserIOService
    from universal_userio.store import SQLiteUserIOStore

    store = SQLiteUserIOStore(tmp_path / "db.sqlite3")
    try:
        service = UserIOService(store, object(), object())
        account_id = "dion:org:user"
        store.register_account(
            account_id=account_id, provider="dion", display_name="DION",
            can_read=True, can_reply=True, credential_ref="test", enabled=True,
            user_id=store.default_user_id,
        )
        store.bind_channel_route(
            user_id=store.default_user_id, source="dion", route_id="dion",
        )
        conversation_id, accepted = service.receive(
            InboxMessage(
                source="dion", message_id=f"{account_id}|room-1:m1",
                sender="peer", sender_name="Peer", body="hello",
                received_at=1_795_694_000, peer_id="room-1",
                conversation_kind="direct", conversation_title="DION chat",
            ),
            route_id="dion", user_id=store.default_user_id, account_ref=account_id,
        )
        assert accepted
        channels = UnifiedChannels(store, service, store.default_user_id)
        rows = channels.adapter("dion").list(limit=10)
        assert [row["id"] for row in rows] == [conversation_id]
        chat = channels.adapter("dion").read(chat_id=conversation_id)["chat"]
        assert chat["messages"][-1]["body"] == "hello"
    finally:
        store.close()
