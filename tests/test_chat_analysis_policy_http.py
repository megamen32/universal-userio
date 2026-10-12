"""Focused integration (expected 3s, max 20s): real HTTP policy and future claims."""
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from universal_userio.contracts import InboxMessage
from universal_userio.http_api import handler
from universal_userio.personal_tokens import PersonalTokens
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class NoAI:
    def suggest(self, **kwargs):
        raise AssertionError("policy must not generate a reply")


class NoSend:
    def send_reply(self, **kwargs):
        raise AssertionError("policy must not send")


@pytest.fixture
def policy_http(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "inbox.sqlite3")
    service = UserIOService(store, NoAI(), NoSend())
    other, other_token = store.create_user("other-policy", "correct horse battery staple")
    ids = {}
    for source, kind in [("telegram", "group"), ("max", "group"), ("gmail", "direct")]:
        ids[source], _ = service.receive(
            InboxMessage(source, source + "-old", source + "-peer", "retained", 1., conversation_kind=kind),
            route_id="read-only",
        )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="owner-token"))
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def request(method, path, body=None, token="owner-token"):
        req = Request(f"http://127.0.0.1:{server.server_port}" + path,
                      data=None if body is None else json.dumps(body).encode(), method=method,
                      headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=3) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    yield store, service, ids, other, other_token, request
    server.shutdown()
    server.server_close()


def test_policy_switch_preserves_history_and_does_not_replay_old_events(policy_http):
    store, service, ids, _, _, request = policy_http
    path = f"/v1/conversations/{ids['telegram']}/analysis-policy"
    assert request("GET", path)[1]["analysis_policy"]["allowed"] is False
    assert request("POST", path, {"enabled": True})[1]["analysis_policy"]["allowed"] is True
    old = store.evaluate_workspace_event(conversation_id=ids["telegram"], source="telegram", message_id="telegram-old")
    assert old["allowed"] is False
    cid, _ = service.receive(InboxMessage("telegram", "future", "telegram-peer", "new", 2., conversation_kind="group"), route_id="read-only")
    assert cid == ids["telegram"]
    assert store.evaluate_workspace_event(conversation_id=cid, source="telegram", message_id="future")["allowed"] is True
    assert request("POST", path, {"enabled": False})[1]["analysis_policy"]["allowed"] is False
    assert store.evaluate_workspace_event(conversation_id=cid, source="telegram", message_id="future")["allowed"] is False
    assert len(store.conversation(cid)["messages"]) == 2
    assert request("GET", f"/v1/conversations/{ids['gmail']}/analysis-policy")[1]["analysis_policy"]["allowed"] is True
    assert request("GET", f"/v1/conversations/{ids['max']}/analysis-policy")[1]["analysis_policy"]["allowed"] is False


def test_policy_owner_binding_validation_and_readonly_token(policy_http):
    store, _, ids, _, other_token, request = policy_http
    path = f"/v1/conversations/{ids['telegram']}/analysis-policy"
    assert request("GET", path, token="invalid")[0] == 401
    assert request("GET", path, token=other_token)[0] == 404
    assert request("POST", path, {"enabled": True}, token=other_token)[0] == 400
    for body in [{"enabled": "false"}, {"enabled": 1}, {}, {"enabled": True, "user_id": store.owner().user_id}]:
        assert request("POST", path, body)[0] == 400
    readonly = PersonalTokens(store).create(store.owner().user_id, "policy readonly", scope="read_only")
    assert request("GET", path, token=readonly["token"])[0] == 200
    assert request("POST", path, {"enabled": True}, token=readonly["token"])[0] == 403
    assert request("GET", path)[1]["analysis_policy"]["allowed"] is False


def test_sidebar_policy_metadata_matches_effective_chat_rules(policy_http):
    _, _, ids, _, _, request = policy_http
    path = f"/v1/conversations/{ids['max']}/analysis-policy"
    assert request("POST", path, {"enabled": True})[0] == 200
    status, result = request("GET", "/v1/conversations")
    assert status == 200
    policies = {row["id"]: row["analysis_policy"] for row in result["conversations"]}
    assert policies[ids["max"]]["allowed"] is True
    assert policies[ids["max"]]["conversation_kind"] == "group"
    assert policies[ids["telegram"]]["allowed"] is False
    assert policies[ids["gmail"]]["conversation_kind"] == "direct"
