"""Real SDK crypto proofs; no homeserver, call or messaging side effects."""
import asyncio
import os
import sys
import types

import pytest

pytest.importorskip("nio")
from nio import Event, MatrixRoom

from universal_userio.matrix_crypto import AccountCrypto, CryptoIdentityMissing


def account():
    return dict(user_id="test-principal", account_id="matrix:test", homeserver="https://example.org",
                matrix_user_id="@a:example.org", access_token="test-token", device_id="A")


def server_without_keys(method, path, payload=None):
    if path.endswith("/whoami"):
        return {"user_id": "@a:example.org", "device_id": "A"}
    if path.endswith("/keys/query"):
        return {"device_keys": {"@a:example.org": {}}}
    return {}


def test_sdk_bootstrap_store_survives_restart_and_decrypts_megolm(tmp_path):
    crypto = AccountCrypto(account(), root=tmp_path, api=server_without_keys)

    async def encrypt(client, db):
        room = MatrixRoom("!qa:example.org", client.user_id)
        room.encrypted = room.members_synced = True
        client.rooms[room.room_id] = room
        client.olm.create_outbound_group_session(room.room_id)
        client.olm.outbound_group_sessions[room.room_id].shared = True
        kind, content = client.encrypt(room.room_id, "io.element.call.encryption_keys",
                                       {"device_id": "A", "call_id": "", "keys": [{"index": 0, "key": "test"}]})
        return dict(type=kind, content=content, sender=client.user_id, event_id="$encrypted",
                    origin_server_ts=1, room_id=room.room_id), client.olm.account.identity_keys

    encrypted, identity = crypto.transaction(encrypt)

    async def decrypt(client, db):
        assert client.olm.account.identity_keys == identity
        clear = client.decrypt_event(Event.parse_encrypted_event(encrypted))
        assert clear.decrypted
        assert clear.source["type"] == "io.element.call.encryption_keys"
        assert clear.source["content"]["keys"][0]["key"] == "test"

    crypto.transaction(decrypt)
    assert all(not path.stat().st_mode & 0o077 for path in tmp_path.rglob("*") if path.is_file())


def test_remote_device_keys_without_local_identity_are_never_replaced(tmp_path):
    calls = []

    def existing(method, path, payload=None):
        calls.append(path)
        if path.endswith("/keys/query"):
            return {"device_keys": {"@a:example.org": {"A": {"keys": {"ed25519:A": "foreign"}}}}}
        return server_without_keys(method, path, payload)

    crypto = AccountCrypto(account(), root=tmp_path, api=existing)
    with pytest.raises(CryptoIdentityMissing):
        crypto.bootstrap()
    assert not any(path.endswith("/keys/upload") for path in calls)


def test_a_device_has_only_one_principal_sync_owner(tmp_path):
    AccountCrypto(account(), root=tmp_path, api=server_without_keys).bootstrap()
    other = dict(account(), user_id="another-principal", account_id="matrix:other")
    with pytest.raises(PermissionError, match="another account crypto lifecycle"):
        AccountCrypto(other, root=tmp_path, api=server_without_keys).bootstrap()


def test_crypto_activation_is_explicit_and_does_not_touch_other_accounts(monkeypatch):
    from universal_userio.matrix_crypto import enabled_for, for_account
    monkeypatch.delenv("USERIO_MATRIX_CRYPTO_ACCOUNTS", raising=False)
    assert not enabled_for(account())
    with pytest.raises(PermissionError, match="not explicitly enabled"):
        for_account(account())
    monkeypatch.setenv("USERIO_MATRIX_CRYPTO_ACCOUNTS", "matrix:test")
    assert enabled_for(account())
    assert not enabled_for(dict(account(), account_id="matrix:personal"))


def test_two_consumers_share_the_sdk_sync_cursor_without_losing_events(tmp_path, monkeypatch):
    from nio import AsyncClient
    from urllib.parse import parse_qs, urlsplit
    since = []

    async def no_network(*args, **kwargs):
        return types.SimpleNamespace()

    monkeypatch.setattr(AsyncClient, "keys_upload", no_network)
    monkeypatch.setattr(AsyncClient, "keys_query", no_network)
    monkeypatch.setattr(AsyncClient, "send_to_device_messages", no_network)

    def api(method, path, payload=None):
        if "/sync?" not in path:
            return server_without_keys(method, path, payload)
        since.append(parse_qs(urlsplit(path).query).get("since", [None])[0])
        events = [] if len(since) > 1 else [dict(type="m.room.message", event_id="$one", sender="@a:example.org",
            origin_server_ts=1, content={"msgtype": "m.text", "body": "fixture"})]
        return {"next_batch": "s"+str(len(since)), "rooms": {"join": {
            "!qa:example.org": {"state": {"events": []}, "timeline": {"events": events}}}}}

    crypto = AccountCrypto(account(), root=tmp_path, api=api)
    messages = crypto.poll("")
    calls = crypto.poll(None)
    assert since == [None, "s1"]
    assert messages == calls
    assert calls["rooms"]["join"]["!qa:example.org"]["timeline"]["events"][0]["event_id"] == "$one"
    assert crypto.poll(messages["next_batch"])["rooms"]["join"] == {}
