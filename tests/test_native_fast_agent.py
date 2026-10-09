"""Integration tests: real native ToolRunner, local provider fixture only."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from universal_userio.contracts import InboxMessage
from universal_userio.native_fast_agent import FastAgentDraftGenerator


TRIAGE = {
    "decision": "review", "importance": 0.5, "urgency": "low", "confidence": 0.8,
    "reason_codes": ["context_checked"], "reason_ru": "Контекст проверен.",
    "action_required": False, "action_summary": "", "deadline_at": None,
    "suggested_replies": [], "safety_override": False, "policy_version": "v1",
}


def _answer(value):
    return {"role": "assistant", "content": json.dumps(value, ensure_ascii=False)}


def _read_more():
    return {"role": "assistant", "content": None, "tool_calls": [{
        "id": "read-older", "type": "function",
        "function": {"name": "read_more_context", "arguments": '{"reason":"Нужна предыстория"}'},
    }]}


@contextmanager
def _provider_fixture(replies):
    """Serve a fixed finite number of native SDK requests without external traffic."""
    requests = []
    failures = []
    answers = iter(replies)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            try:
                length = int(self.headers["Content-Length"])
                assert 0 < length <= 7 * 1024 * 1024
                payload = json.loads(self.rfile.read(length))
                assert self.path == "/v1/chat/completions"
                assert len(requests) < len(replies)
                requests.append(payload)
                message = next(answers)
                base = {"id": "fixture", "object": "chat.completion", "created": 1,
                        "model": payload["model"]}
                finish = "tool_calls" if message.get("tool_calls") else "stop"
                if payload.get("stream"):
                    delta = dict(message)
                    if delta.get("tool_calls"):
                        delta["tool_calls"] = [dict(call, index=index)
                                               for index, call in enumerate(delta["tool_calls"])]
                    chunks = [
                        dict(base, object="chat.completion.chunk", choices=[
                            {"index": 0, "delta": delta, "finish_reason": None}]),
                        dict(base, object="chat.completion.chunk", choices=[
                            {"index": 0, "delta": {}, "finish_reason": finish}]),
                    ]
                    body = ("".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
                            + "data: [DONE]\n\n").encode()
                    mime = "text/event-stream"
                else:
                    body = json.dumps(dict(base, choices=[
                        {"index": 0, "message": message, "finish_reason": finish}],
                        usage={"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20})).encode()
                    mime = "application/json"
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as error:
                failures.append(error)
                self.send_error(500)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.socket.settimeout(5)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
        assert not failures
        assert len(requests) == len(replies)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
        assert not worker.is_alive()


def test_native_triage_reuses_summary_without_unnecessary_history_read():
    with _provider_fixture([_answer(TRIAGE)]) as (endpoint, requests):
        generator = FastAgentDraftGenerator(endpoint=endpoint, token="fixture-only")
        result = generator.triage_with_context(
            conversation_id="c", latest_message=InboxMessage("telegram", "8", "Контакт", "Понятно", 8),
            history=[{"message_id": str(i), "body": f"previous-{i}"} for i in range(1, 8)],
            cached_summary="Договорились о сроке пятница.", max_drafts=0,
            history_reader=lambda _: pytest.fail("Sufficient context must not be fetched again"),
        )
        assert result["decision"] == "review"
        assert requests[0]["model"] == "MiniMax-M2.7"
        assert "Договорились о сроке пятница." in json.dumps(requests[0], ensure_ascii=False)
        assert "previous-1" not in json.dumps(requests[0])
        assert generator.last_native_run["engine"] == "fast-agent"
        assert generator.last_native_run["read_calls"] == 0


def test_native_read_more_switches_to_m3_before_sending_new_image():
    anchors = []

    def read_page(anchor):
        anchors.append(anchor)
        return [{"message_id": "2", "body": "Предыдущий макет", "attachments": [{
            "content_type": "image/png", "image_bytes": b"\x89PNG\r\n\x1a\nfixture",
            "attachment_url": "https://private.invalid?token=SECRET_SENTINEL",
        }]}]

    with _provider_fixture([_read_more(), _answer(TRIAGE)]) as (endpoint, requests):
        generator = FastAgentDraftGenerator(endpoint=endpoint, token="fixture-only")
        result = generator.triage_with_context(
            conversation_id="c", latest_message=InboxMessage("telegram", "8", "Контакт", "Как макет?", 8),
            history=[{"message_id": "7", "anchor_id": "rowid:7", "body": "новое"}],
            history_reader=read_page, max_drafts=0,
        )
        assert result == TRIAGE
        assert anchors == ["rowid:7"]
        assert [request["model"] for request in requests] == ["MiniMax-M2.7", "MiniMax-M3"]
        assert "data:image/png;base64," in json.dumps(requests[1])
        assert "SECRET_SENTINEL" not in json.dumps(requests)
        assert generator.last_native_run["read_calls"] == 1
        assert generator.last_native_run["vision_images"] == 1


def test_native_schema_failure_is_not_a_successful_triage():
    with _provider_fixture([_answer({"decision": "silent"})]) as (endpoint, _):
        generator = FastAgentDraftGenerator(endpoint=endpoint, token="fixture-only")
        with pytest.raises(ValueError, match="structured result"):
            generator.triage_with_context(
                conversation_id="c", latest_message=InboxMessage("telegram", "1", "Контакт", "текст", 1),
                history=[], max_drafts=0,
            )


def test_native_summary_uses_exact_bounded_schema_and_image_model():
    with _provider_fixture([_answer({"summary": "Макет принят."})]) as (endpoint, requests):
        generator = FastAgentDraftGenerator(endpoint=endpoint, token="fixture-only")
        summary = generator.summarize_conversation(
            conversation_id="c", previous_summary="Договорились сделать макет.", token_budget=128,
            new_messages=[{"message_id": "2", "body": "Всё хорошо.", "attachments": [{
                "content_type": "image/gif", "image_bytes": b"GIF89afixture",
            }]}],
        )
        assert summary == "Макет принят."
        assert requests[0]["model"] == "MiniMax-M3"
        assert "data:image/gif;base64," in json.dumps(requests[0])
        assert "Договорились сделать макет." in json.dumps(requests[0], ensure_ascii=False)


def test_native_read_more_budget_blocks_repeated_empty_fetch():
    anchors = []

    def read_page(anchor):
        anchors.append(anchor)
        return []

    with _provider_fixture([_read_more(), _read_more(), _answer(TRIAGE)]) as (endpoint, requests):
        generator = FastAgentDraftGenerator(endpoint=endpoint, token="fixture-only")
        generator.triage_with_context(
            conversation_id="c", latest_message=InboxMessage("telegram", "1", "Контакт", "текст", 1),
            history=[], history_reader=read_page, max_drafts=0,
        )
        assert anchors == [""]
        assert len(requests) == 3
        assert generator.last_native_run["read_calls"] == 1
