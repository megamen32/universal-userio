"""Offline, credential-free attribution and privacy checks for ChatGPT transport."""
import logging
import json
from datetime import datetime, timezone
from threading import Lock

import pytest

from universal_userio import adapters
from universal_userio.chatgpt_request_trace import (
    ChatGPTRequestTrace, classify_endpoint, retry_after_seconds,
)


def test_endpoint_allowlist_strips_private_paths_queries_and_accounts():
    secret = "SUPER_PRIVATE_QUERY=SHOULD_NOT_ESCAPE"
    assert classify_endpoint("https://chatgpt.com/backend-api/conversation/a123?"+secret) == (
        "conversation_read", "/backend-api/conversation/{id}")
    assert classify_endpoint("https://chatgpt.com/backend-api/conversations?"+secret) == (
        "conversation_list", "/backend-api/conversations")
    assert classify_endpoint("https://chatgpt.com/backend-api/conversation", method="POST") == (
        "message_send", "/backend-api/conversation")
    assert classify_endpoint("https://chatgpt.com/private/"+secret) == ("other", "other")


def test_retention_cap_and_minute_aggregates_are_upstream_only(caplog):
    t = ChatGPTRequestTrace()
    with caplog.at_level(logging.INFO, logger="universal_userio.chatgpt_request_trace"):
        t.record(initiator="userio_cdp", executor="cdp_sidecar", kind="delegated",
                 operation="send_message", endpoint="cdp_sidecar", timestamp=70000)
        t.record(initiator="userio_web", executor="curl_cffi", kind="upstream_http",
                 operation="conversation_read", endpoint="/backend-api/conversation/{id}",
                 status=429, latency_ms=20, retry_after_s=120, timestamp=70000)
        for _ in range(520):
            t.record(initiator="bogus_access_token", executor="secret_cookie",
                     kind="dom_cache_read", operation="chat_id_private",
                     endpoint="/private/secret?auth=PASSWORD", cache_hit=True, timestamp=70000)
    snapshot = t.minute_snapshot(timestamp=70000)
    assert sum(e["upstream_http"] for e in snapshot) == 1
    assert sum(e["http_429"] for e in snapshot) == 1
    assert sum(e["cache_hits"] for e in snapshot) == 520
    assert t.retained_event_count(timestamp=70000) == 512
    assert caplog.text.count("chatgpt_request ") <= t.MAX_LOG_EVENTS_PER_MINUTE
    assert t.retained_event_count(timestamp=70000+3601) == 0
    assert not t.minute_snapshot(timestamp=70000+3601)
    for value in ("PASSWORD", "secret_cookie", "bogus_access_token", "chat_id_private"):
        assert value not in caplog.text
    assert '"retry_after_s":120' in caplog.text


def test_retry_after_numeric_and_http_date():
    assert retry_after_seconds("120") == 120
    assert retry_after_seconds("9999999") == 86400
    assert retry_after_seconds("Authorization: Bearer secret") is None
    at = datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc).timestamp()
    assert retry_after_seconds("Mon, 02 Nov 2026 00:02:00 GMT", at=at) == 120


def test_actual_http_429_is_attributed_without_new_headers_or_remote_calls(monkeypatch, caplog):
    trace = ChatGPTRequestTrace()
    monkeypatch.setattr(adapters, "CHATGPT_REQUEST_TRACE", trace)
    monkeypatch.setattr(adapters, "_chatgpt_accounts", lambda *_: {"fixture": []})
    monkeypatch.setattr(adapters, "_chatgpt_user_agent", lambda *_: "test-agent")

    class Response:
        status_code = 429
        headers = {"Retry-After": "17"}
        text = "not persisted"

    class Client:
        def __init__(self):
            self.calls = []
        def get(self, url, headers):
            self.calls.append((url, headers))
            return Response()
    client = Client()
    monkeypatch.setattr(adapters, "_chatgpt_client", lambda *_: client)
    secret = "AUTH_KEY_NOT_ALLOWED_IN_TRACE"
    with caplog.at_level(logging.INFO, logger="universal_userio.chatgpt_request_trace"):
        with pytest.raises(RuntimeError, match="HTTP 429") as failure:
            adapters._chatgpt_raw("fixture_user", "fixture",
                                 "https://chatgpt.com/backend-api/conversation/"+secret+"?token=NEVER_LOG",
                                 bearer_needed=False)
    assert secret not in str(failure.value)
    assert "NEVER_LOG" not in str(failure.value)
    assert len(client.calls) == 1
    assert all(not k.lower().startswith(("x-trace", "x-correlation", "x-request-id"))
               for k in client.calls[0][1])
    assert sum(e["upstream_http"] for e in trace.minute_snapshot()) == 1
    assert "NEVER_LOG" not in caplog.text
    assert secret not in caplog.text
    assert '"http_status":429' in caplog.text
    assert '"retry_after_s":17' in caplog.text


def test_cdp_delegate_only_not_upstream(monkeypatch):
    trace = ChatGPTRequestTrace()
    monkeypatch.setattr(adapters, "CHATGPT_REQUEST_TRACE", trace)
    client = object.__new__(adapters.ChatGPTCDPMcpClient)
    client._lock = Lock()
    monkeypatch.setattr(client, "_start", lambda: None)
    monkeypatch.setattr(client, "_request", lambda *a: {
        "content": [{"text": '{"messageRef":"offline-result"}'}]})
    assert client.call("send_message", {"text": "PRIVATE MESSAGE", "chatRef": "secret"}) == {
        "messageRef": "offline-result"}
    rows = trace.minute_snapshot()
    assert sum(x["events"] for x in rows) == 1
    assert sum(x["upstream_http"] for x in rows) == 0
    assert rows[0]["kind"] == "delegated"


def test_minute_rollup_emits_without_timer_or_background_traffic(caplog):
    t = ChatGPTRequestTrace()
    with caplog.at_level(logging.WARNING, logger="universal_userio.chatgpt_request_trace"):
        t.record(initiator="userio_web", executor="curl_cffi", kind="upstream_http",
                 operation="auth_session", endpoint="/api/auth/session",
                 status=200, timestamp=120000)
        t.record(initiator="userio_cdp", executor="cdp_sidecar", kind="delegated",
                 operation="other", endpoint="cdp_sidecar", timestamp=120001)
        t.record(initiator="userio_web", executor="curl_cffi", kind="upstream_http",
                 operation="conversation_list", endpoint="/backend-api/conversations",
                 status=429, timestamp=120060)
    records = [r.message for r in caplog.records if r.message.startswith("chatgpt_minute ")]
    assert len(records) == 1
    summary = json.loads(records[0].split(" ", 1)[1])
    assert summary["events"] == 2
    assert summary["upstream_http"] == 1
    assert summary["http_429"] == 0


def test_active_web_transport_auth_cache_and_429_are_not_double_counted(monkeypatch, caplog):
    """One nested auth mint plus 3 explicit HTTP calls means exactly four upstream attempts."""
    trace = ChatGPTRequestTrace()
    monkeypatch.setattr(adapters, "CHATGPT_REQUEST_TRACE", trace)
    monkeypatch.setattr(adapters, "_chatgpt_accounts", lambda *_: {"fixture": []})
    monkeypatch.setattr(adapters, "_chatgpt_user_agent", lambda *_: "fixture-agent")
    monkeypatch.setattr(adapters, "_chatgpt_tokens", {})

    private = "MOCK_SECRET_NOT_FOR_LOGS"
    class Response:
        def __init__(self, status, text, headers=None):
            self.status_code, self.text, self.headers = status, text, headers or {}

    class Client:
        def __init__(self):
            self.calls = []
        def get(self, url, headers):
            self.calls.append((url, tuple(headers)))
            if "/api/auth/session" in url:
                return Response(200, json.dumps({"accessToken": private}))
            if "/backend-api/conversation/" in url:
                return Response(429, "limited", {"Retry-After": "17"})
            return Response(200, '{"items":[]}')
    client=Client()
    monkeypatch.setattr(adapters, "_chatgpt_client", lambda *_: client)
    with caplog.at_level(logging.WARNING, logger="universal_userio.chatgpt_request_trace"):
        for _ in range(2):
            assert adapters._chatgpt_request(
                "fixture_user", "fixture",
                "https://chatgpt.com/backend-api/conversations?state=OAUTH_STATE_PRIVATE"
            ) == {"items": []}
        with pytest.raises(RuntimeError, match="HTTP 429"):
            adapters._chatgpt_request(
                "fixture_user", "fixture",
                "https://chatgpt.com/backend-api/conversation/PRIVATE_CHAT_ID?code=PRIVATE_CODE"
            )
    events=[json.loads(x.getMessage().split(" ", 1)[1]) for x in caplog.records
            if x.getMessage().startswith("chatgpt_request ")]
    assert [x["operation"] for x in events] == [
        "auth_session", "conversation_list", "conversation_list", "conversation_read"
    ]
    assert len(client.calls)==4
    assert [x["upstream_count"] for x in events]==[1,1,1,1]
    assert [x["cache_hit"] for x in events]==[False,False,True,True]
    assert sum(x["http_429"] for x in trace.minute_snapshot())==1
    assert sum(x["upstream_http"] for x in trace.minute_snapshot())==4
    assert sum(x["cache_hits"] for x in trace.minute_snapshot())==2
    for forbidden in (private, "OAUTH_STATE_PRIVATE", "PRIVATE_CODE", "PRIVATE_CHAT_ID",
                      "fixture_user"):
        assert forbidden not in caplog.text


def test_retry_error_strips_url_query_and_counts_all_physical_attempts(monkeypatch, caplog):
    trace = ChatGPTRequestTrace()
    monkeypatch.setattr(adapters, "CHATGPT_REQUEST_TRACE", trace)
    monkeypatch.setattr(adapters, "_chatgpt_accounts", lambda *_: {"fixture":[]})
    monkeypatch.setattr(adapters, "_chatgpt_user_agent", lambda *_: "")
    slept=[]
    monkeypatch.setattr(adapters.time, "sleep", lambda delay: slept.append(delay))

    class Broken:
        calls=0
        def get(self, url, headers):
            self.calls += 1
            raise RuntimeError("private request ?access_token=SHOULD_NEVER_ESCAPE")
    fake=Broken()
    monkeypatch.setattr(adapters, "_chatgpt_client", lambda *_: fake)
    with caplog.at_level(logging.WARNING, logger="universal_userio.chatgpt_request_trace"):
        with pytest.raises(RuntimeError, match="ChatGPT transport failed: RuntimeError") as result:
            adapters._chatgpt_raw("fixture_user", "fixture",
                    "https://chatgpt.com/backend-api/conversations?code=HIDDEN_CODE",
                    bearer_needed=False)
    assert fake.calls==3
    assert slept==[1,1,1]  # existing retry pacing retained
    assert "SHOULD_NEVER_ESCAPE" not in str(result.value)
    assert "HIDDEN_CODE" not in str(result.value)
    events=[json.loads(x.getMessage().split(" ",1)[1]) for x in caplog.records
            if x.getMessage().startswith("chatgpt_request ")]
    assert [x["retry_attempt"] for x in events]==[0,1,2]
    assert [x["backoff_ms"] for x in events]==[1000,1000,1000]
    assert sum(x["upstream_http"] for x in trace.minute_snapshot())==3
    assert sum(x["http_429"] for x in trace.minute_snapshot())==0
    assert "SHOULD_NEVER_ESCAPE" not in caplog.text
    assert "HIDDEN_CODE" not in caplog.text


def test_web_send_post_operation_and_status_redacted(monkeypatch, caplog):
    trace=ChatGPTRequestTrace()
    monkeypatch.setattr(adapters, "CHATGPT_REQUEST_TRACE", trace)
    monkeypatch.setattr(adapters, "_chatgpt_accounts", lambda *_: {"fixture":[]})
    monkeypatch.setattr(adapters, "_chatgpt_user_agent", lambda *_: "")
    class Response:
        status_code=429
        headers={"Retry-After":"42"}
        text="not used"
    class Client:
        def __init__(self):self.calls=[]
        def post(self,url,headers,json):
            self.calls.append((url,set(headers),json))
            return Response()
    client=Client()
    monkeypatch.setattr(adapters, "_chatgpt_client", lambda *_: client)
    with caplog.at_level(logging.WARNING, logger="universal_userio.chatgpt_request_trace"):
        with pytest.raises(RuntimeError, match="HTTP 429"):
            adapters._chatgpt_raw("fixture_user","fixture",
                                 "https://chatgpt.com/backend-api/conversation?code=OAUTH_CODE_PRIVATE",
                                 bearer_needed=False, json_body={"prompt":"MOCK_PRIVATE_MESSAGE"})
    assert len(client.calls)==1
    events=[json.loads(x.getMessage().split(" ",1)[1]) for x in caplog.records
            if x.getMessage().startswith("chatgpt_request ")]
    assert len(events)==1
    assert events[0]["operation"]=="message_send"
    assert events[0]["endpoint"]=="/backend-api/conversation"
    assert events[0]["retry_after_s"]==42
    assert events[0]["upstream_count"]==1
    assert "OAUTH_CODE_PRIVATE" not in caplog.text
    assert "MOCK_PRIVATE_MESSAGE" not in caplog.text
