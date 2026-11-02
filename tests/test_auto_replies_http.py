"""Live HTTP boundary test with synthetic accounts and no real sends."""
import json
import threading
import time
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from universal_userio.contracts import InboxMessage
from universal_userio.http_api import handler
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class StubGenerator:
    def suggest(self, **_): return "unused"


class StubOutbox:
    def __init__(self): self.calls = []
    def send_reply(self, **kwargs):
        self.calls.append(kwargs)
        return "synthetic-receipt"


def test_auto_reply_http_tenant_scope_preview_and_persistence(tmp_path):
    db_path = tmp_path / "userio.sqlite3"
    store = SQLiteUserIOStore(db_path)
    outbox = StubOutbox()
    service = UserIOService(store, StubGenerator(), outbox)
    owner, owner_token = store.create_user("test-primary-auto-reply", "synthetic test password for primary")
    other, other_token = store.create_user("test-other-auto-reply", "a synthetic test password only")
    for principal in (owner, other):
        store.register_account(
            account_id="telegram:" + principal.user_id,
            provider="telegram", display_name="Synthetic",
            can_read=True, can_reply=True, credential_ref="test:synthetic",
            user_id=principal.user_id,
        )
        store.bind_channel_route(user_id=principal.user_id, source="telegram", route_id="telegram")
    def make_chat(principal):
        conversation_id, _ = service.receive(
            InboxMessage("telegram", "peer:1", "sender", "historical", time.time() - 600,
                         peer_id="peer"),
            route_id="telegram", account_ref="telegram:" + principal.user_id,
            user_id=principal.user_id,
        )
        return conversation_id
    owner_chat, other_chat = make_chat(owner), make_chat(other)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="test-service-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:" + str(server.server_port)
    def request(path, method="GET", token=owner_token, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = Request(base + path, data=data, method=method, headers={
            "Authorization": "Bearer " + token, "Content-Type": "application/json",
        })
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())
    rule = {
        "conversation_id": owner_chat, "match_type": "regex", "pattern": "^hello",
        "flags": "i", "reply_text": "Hi", "enabled": True,
    }
    try:
        assert request("/v1/auto-replies", token="invalid")[0] == 401
        assert request("/v1/auto-replies", token="test-service-token")[0] == 403
        assert request("/v1/auto-replies")[1] == {"rules": []}
        assert request("/v1/auto-replies", "POST", owner_token, {**rule, "action": "create"})[0] == 200
        assert request("/v1/auto-replies", token=other_token)[1] == {"rules": []}
        assert request("/v1/auto-replies", "POST", other_token, {**rule, "action": "create"})[0] == 404
        assert request("/v1/auto-replies", "POST", owner_token, {**rule, "conversation_id": other_chat, "action": "create"})[0] == 404
        assert request("/v1/auto-replies/preview", "POST", owner_token, {
            "conversation_id": owner_chat, "sample_text": "HELLO!",
        })[1]["matched"] is True
        assert request("/v1/auto-replies/preview", "POST", other_token, {
            "conversation_id": owner_chat, "sample_text": "HELLO!",
        })[0] == 404
        assert request("/v1/auto-replies", "POST", owner_token, {
            **rule, "pattern": "(", "action": "create",
        })[0] == 400
        assert outbox.calls == []
        only_rule = request("/v1/auto-replies")[1]["rules"][0]
        assert request("/v1/auto-replies", "POST", owner_token, {
            "action": "toggle", "id": only_rule["id"], "enabled": False,
        })[1]["rule"]["enabled"] is False
        assert request("/v1/auto-replies", "POST", owner_token, {
            "action": "delete", "id": only_rule["id"],
        })[1]["deleted"] is True
        assert request("/v1/auto-replies")[1]["rules"] == []
    finally:
        server.shutdown()
        server.server_close()

    reopened = SQLiteUserIOStore(db_path)
    assert reopened._connection.execute("SELECT count(*) FROM auto_reply_rules").fetchone()[0] == 0


def test_synthetic_live_ingress_durable_dedupe_and_restart(tmp_path):
    db = tmp_path / "userio.sqlite3"
    store = SQLiteUserIOStore(db)
    owner = store.owner()
    store.register_account(
        account_id="telegram:synthetic", provider="telegram",
        display_name="Synthetic", can_read=True, can_reply=True,
        credential_ref="test:synthetic", user_id=owner.user_id,
    )
    outbox = StubOutbox()
    service = UserIOService(store, StubGenerator(), outbox)
    previous = InboxMessage(
        "telegram", "peer:1", "sender", "history", time.time() - 1000, peer_id="peer",
    )
    chat, accepted = service.receive(
        previous, route_id="telegram", account_ref="telegram:synthetic",
        user_id=owner.user_id,
    )
    assert accepted is True
    rule = service.auto_reply_rules.create(owner.user_id, {
        "conversation_id": chat, "match_type": "keyword",
        "pattern": "hello", "flags": "i", "reply_text": "Hi",
        "enabled": True,
    })
    event = InboxMessage(
        "telegram", "peer:2", "sender", "hello", time.time(), peer_id="peer",
    )
    service.receive(event, route_id="telegram", account_ref="telegram:synthetic",
                    user_id=owner.user_id)
    service.receive(event, route_id="telegram", account_ref="telegram:synthetic",
                    user_id=owner.user_id)
    assert len(outbox.calls) == 1
    assert outbox.calls[0]["body"] == "Hi"
    reopened = SQLiteUserIOStore(db)
    service2 = UserIOService(reopened, StubGenerator(), StubOutbox())
    assert service2.auto_reply_rules.get(owner.user_id, rule["id"])["enabled"] is True
    service2.receive(event, route_id="telegram", account_ref="telegram:synthetic",
                     user_id=owner.user_id)
    assert reopened._connection.execute(
        "SELECT state FROM auto_reply_deliveries"
    ).fetchone()[0] == "sent"
    assert len(service2._outbox.calls) == 0
