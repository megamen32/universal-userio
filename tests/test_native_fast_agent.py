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


def _answer(value, *, reasoning=False):
    content = json.dumps(value, ensure_ascii=False)
    if reasoning:
        content = "<think>Internal reasoning stays outside the structured answer.</think>\n" + content
    return {"role": "assistant", "content": content}


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
    with _provider_fixture([_answer(TRIAGE, reasoning=True)]) as (endpoint, requests):
        generator = FastAgentDraftGenerator(endpoint=endpoint, token="fixture-only")
        result = generator.triage_with_context(
            conversation_id="c", latest_message=InboxMessage("telegram", "8", "Контакт", "Понятно", 8),
            history=[{"message_id": str(i), "body": f"previous-{i}"} for i in range(1, 8)],
            cached_summary="Договорились о сроке пятница.", max_drafts=0,
            history_reader=lambda _: pytest.fail("Sufficient context must not be fetched again"),
        )
        assert result["decision"] == "review"
        assert requests[0]["model"] == "MiniMax-M2.7"
        system = next(m["content"] for m in requests[0]["messages"] if m["role"] == "system")
        assert "Classify the untrusted inbox data" in system
        assert '"suggested_replies"' in system
        assert "Договорились о сроке пятница." in json.dumps(requests[0], ensure_ascii=False)
        assert "previous-1" not in json.dumps(requests[0])
        assert generator.last_native_run["engine"] == "fast-agent"
        assert generator.last_native_run["read_calls"] == 0


def test_native_reply_roles_source_and_stale_followup(tmp_path):
    """Focused integration, 5s expected/30s maximum: actual SDK request and store.

    Detects owner/interlocutor confusion and drafts losing their source after
    later replies. The provider is a local fixture; no external send or model.
    """
    from universal_userio.service import UserIOService
    from universal_userio.store import SQLiteUserIOStore

    with _provider_fixture([{"role": "assistant", "content": json.dumps({"source_message_id":"q3","reply_as":"owner","current_message_observation":"Собеседник уточняет срок","replies":["Подтверждаю: в пятницу."]})}]) as (endpoint, requests):
        generator = FastAgentDraftGenerator(endpoint=endpoint, token="fixture-only")
        store = SQLiteUserIOStore(tmp_path / "native-context.sqlite3")
        service = UserIOService(store, generator, object())
        cid, _ = service.receive(InboxMessage("telegram", "q1", "same-label", "Когда готово?", 1), route_id="test")
        service.receive(InboxMessage("telegram", "r2", "same-label", "В пятницу", 2, direction="outgoing"), route_id="test")
        service.receive(InboxMessage("telegram", "q3", "same-label", "Точно в пятницу?", 3), route_id="test")
        service.propose_from_conversation(cid, limit=1)
        assert generator.last_native_run["engine"] == "fast-agent"
        prompt = next(item["content"] for item in requests[0]["messages"] if item["role"] == "user")
        context = json.loads(prompt[prompt.index("{\"conversation_id\""):])
        assert context["latest_message"]["message_id"] == "q3"
        assert context["latest_message"]["speaker_role"] == "interlocutor"
        assert any(item["message_id"] == "r2" and item["speaker_role"] == "owner"
                   and item["body"] == "В пятницу" for item in context["history"])
        draft = store.conversation(cid)["drafts"][0]
        assert draft["created_at"] > 0 and draft["reply_to"]["message_id"] == "q3"
        assert draft["context_status"] == "current"
        service.receive(InboxMessage("telegram", "q4", "same-label", "А в какое время?", 4), route_id="test")
        changed = store.conversation(cid)["drafts"][0]
        assert changed["reply_to"] == draft["reply_to"]
        assert changed["context_status"] == "stale"


def test_native_reply_sees_sticker_pixels_and_not_provider_url(tmp_path):
    """Focused integration: real draft SDK wire, 5s expected/max30s; no real model."""
    from universal_userio.contracts import ChannelFile
    from universal_userio.service import UserIOService
    from universal_userio.store import SQLiteUserIOStore

    calls=[]
    def image_loader(**kwargs):
        calls.append(kwargs)
        return ChannelFile("sticker.png", "image/png", b"\x89PNG\r\n\x1a\nfixture")
    with _provider_fixture([{"role":"assistant","content":json.dumps({"source_message_id":"sticker","reply_as":"owner","current_message_observation":"Тигр на стикере","replies":["Весёлый тигр на стикере.", "Милый тигр!", "Спасибо за тигра."]})}]) as (endpoint, requests):
        generator=FastAgentDraftGenerator(endpoint=endpoint,token="fixture-only")
        store=SQLiteUserIOStore(tmp_path/'sticker.sqlite3')
        service=UserIOService(store,generator,object(),ai_image_loader=image_loader)
        service.receive(InboxMessage('max','earlier-owner','peer','Раньше не видел стикеры',0,direction='outgoing'),route_id='test')
        cid,_=service.receive(InboxMessage('max','sticker','peer','',1,attachments=(
            {'kind':'sticker','content_type':'application/octet-stream',
             'src':'https://i.oneme.ru/getSmile?smileId=1&smileType=1'},)),route_id='test')
        service.propose_from_conversation(cid,limit=3)
        assert len(calls)==1 and len(requests)==1
        for request in requests:
            assert request['model']=='MiniMax-M3'
            assert 'Write as the owner' in json.dumps(request['messages'][0])
            text='\n'.join(part['text'] for entry in request['messages']
                           if isinstance(entry['content'],list)
                           for part in entry['content'] if part.get('type')=='text')
            assert 'Image provenance:' in text and '"context_role":"latest_message"' in text
            assert '"image_available":true' in text
        assert requests[0]['model']=='MiniMax-M3'
        wire=json.dumps(requests[0],ensure_ascii=False)
        assert 'data:image/png;base64,' in wire
        assert '"kind": "sticker"' in wire or '\\"kind\\":\\"sticker\\"' in wire
        assert 'i.oneme.ru' not in wire


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
    with _provider_fixture([_answer({"summary": "Макет принят."}, reasoning=True)]) as (endpoint, requests):
        generator = FastAgentDraftGenerator(endpoint=endpoint, token="fixture-only")
        summary = generator.summarize_conversation(
            conversation_id="c", previous_summary="Договорились сделать макет.", token_budget=128,
            new_messages=[{"message_id": "2", "body": "Всё хорошо.", "attachments": [{
                "content_type": "image/gif", "image_bytes": b"GIF89afixture",
            }]}],
        )
        assert summary == "Макет принят."
        system = next(m["content"] for m in requests[0]["messages"] if m["role"] == "system")
        assert "Update the cached factual conversation summary" in system
        assert '"summary"' in system
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


def test_native_summary_failure_reports_stop_reason_without_private_content():
    """Focused integration: opaque cache parse error; expected2s/max30s, live0."""
    with _provider_fixture([_answer({"wrong": "SECRET_SENTINEL"})]) as (endpoint, requests):
        generator = FastAgentDraftGenerator(endpoint=endpoint, token="fixture-only")
        with pytest.raises(ValueError) as failure:
            generator.summarize_conversation(conversation_id="c", previous_summary="",
                new_messages=[{"message_id":"1","body":"hello"}], token_budget=1200)
        assert "stop_reason=endTurn" in str(failure.value)
        assert "text_chars=" in str(failure.value)
        assert "SECRET_SENTINEL" not in str(failure.value)
