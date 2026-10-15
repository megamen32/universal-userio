"""Account-scoped Telegram chat list/search regression tests."""
from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

from universal_userio.contracts import InboxMessage
from universal_userio.http_api import handler
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


def test_switching_telegram_accounts_filters_chats_and_search(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, object(), object())
    nikita, _ = service.receive(
        InboxMessage("telegram", "900:10", "Shared peer", "unique-needle main",
                     100.0, peer_id="900", conversation_kind="direct"),
        route_id="telegram", account_ref="telegram:111",
    )
    secretary, _ = service.receive(
        InboxMessage("telegram", "900:11", "Shared peer", "unique-needle other",
                     110.0, peer_id="900", conversation_kind="direct"),
        route_id="telegram", account_ref="telegram:222",
    )
    assert nikita != secretary
    assert {row["id"] for row in store.conversations(source="telegram")} == {
        nikita, secretary,
    }

    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="test-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    headers = {"Authorization": "Bearer test-token"}
    store.register_account(account_id="telegram:111", provider="telegram", display_name="main", can_read=True, can_reply=True, credential_ref="telegram-qr:test")
    try:
        with urlopen(Request(base + "/v1/contact-profiles", method="POST", headers={**headers,"Content-Type":"application/json"}, data=json.dumps({"account_id":"telegram:111","profiles":[{"peer_id":"900","username":"semerindima","first_name":"Дмитрий","last_name":"Семерин","phone":"79841303184"}]}).encode())) as response:
            assert json.loads(response.read())["updated"] == 1
        with urlopen(Request(base + "/v1/search?q=semerindima&match_type=exact&account_ref=telegram%3A111", headers=headers)) as response:
            assert json.loads(response.read())["results"][0]["id"] == nikita
        with urlopen(Request(base + "/v1/search?q=semerindima&match_type=exact&account_ref=telegram%3A222", headers=headers)) as response:
            assert json.loads(response.read())["results"] == []
        for ref, expected in (("telegram%3A111", nikita), ("telegram%3A222", secretary)):
            with urlopen(Request(
                base + f"/v1/conversations?source=telegram&exact_source=1&account_ref={ref}",
                headers=headers,
            )) as response:
                rows = json.loads(response.read())["conversations"]
            assert len(rows) == 1 and rows[0]["id"] == expected
            with urlopen(Request(
                base + f"/v1/search?q=unique-needle&source=telegram&account_ref={ref}",
                headers=headers,
            )) as response:
                found = json.loads(response.read())["results"]
            assert len(found) == 1 and found[0]["id"] == expected
        with urlopen(Request(base + "/v1/conversations?source=telegram", headers=headers)) as response:
            all_rows = json.loads(response.read())["conversations"]
        assert {row["id"] for row in all_rows} == {nikita, secretary}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        store.close()
