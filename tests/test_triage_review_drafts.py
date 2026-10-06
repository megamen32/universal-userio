from __future__ import annotations

import json

from universal_userio.adapters import TelegramQrHttpOutbox
from universal_userio.ai import OpenAICompatibleDraftGenerator
from universal_userio.contracts import InboxMessage
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class _Response:
    def __init__(self, body: dict) -> None:
        self.status = 200
        self._body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return self._body


def _tool_response(name: str, arguments: object, *, call_id: str = "call-1") -> _Response:
    return _Response({"choices": [{"message": {"content": "ignored", "tool_calls": [{
        "id": call_id, "type": "function",
        "function": {
            "name": name,
            "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments),
        },
    }]}}]})


def _text_response(content: str) -> _Response:
    return _Response({"choices": [{"message": {"content": content}}]})


_TRIAGE_ARGUMENTS = {
    "decision": "review", "importance": 0.4, "urgency": "low", "confidence": 0.6,
    "reason_codes": ["needs_context"], "reason_ru": "Нужно больше контекста.",
    "action_required": False, "action_summary": "Проверить ветку.", "deadline_at": None,
    "suggested_replies": [{"body": "Первый вариант"}, {"body": "Второй вариант"}],
    "safety_override": False, "policy_version": "model-v1",
}


def _generator(runner) -> OpenAICompatibleDraftGenerator:
    return OpenAICompatibleDraftGenerator(
        endpoint="https://ai.example/v1", token="secret", model="m", runner=runner,
    )


def test_read_more_loop_pages_history_then_submits() -> None:
    requests = []
    replies = iter([
        _tool_response("read_more_context", {"reason": "нужна история"}),
        _tool_response("submit_triage", _TRIAGE_ARGUMENTS),
    ])
    reads = []

    def runner(request, *, timeout):
        requests.append(json.loads(request.data))
        return next(replies)

    def reader(before_message_id: str):
        reads.append(before_message_id)
        return [{"message_id": "1", "sender": "anna", "direction": "incoming", "body": "старое"}]

    result = _generator(runner).triage_with_context(
        conversation_id="conv_1",
        latest_message=InboxMessage("telegram", "3", "anna", "какой вариант?", 3.0),
        history=[{"message_id": "2", "sender": "anna", "direction": "incoming", "body": "вопрос"}],
        history_reader=reader,
    )

    assert reads == ["2"]
    assert result["decision"] == "review"
    assert [item["body"] for item in result["suggested_replies"]] == ["Первый вариант", "Второй вариант"]
    first_payload = requests[0]
    assert first_payload["tool_choice"] == "auto"
    assert [tool["function"]["name"] for tool in first_payload["tools"]] == [
        "submit_triage", "read_more_context",
    ]
    follow_up = requests[1]
    assert [message["role"] for message in follow_up["messages"]][-2:] == ["assistant", "tool"]
    tool_note = json.loads(follow_up["messages"][-1]["content"])
    assert tool_note["older_messages"][0]["message_id"] == "1"


def test_read_more_loop_stops_when_history_is_exhausted() -> None:
    replies = iter([
        _tool_response("read_more_context", {"reason": "хочу ещё"}),
        _tool_response("read_more_context", {"reason": "точно всё?"}),
        _tool_response("submit_triage", _TRIAGE_ARGUMENTS),
    ])

    def runner(request, *, timeout):
        return next(replies)

    def reader(before_message_id: str):
        if before_message_id == "0":
            return []
        return [{"message_id": "0", "sender": "anna", "direction": "incoming", "body": "самое старое"}]

    result = _generator(runner).triage_with_context(
        conversation_id="conv_1",
        latest_message=InboxMessage("telegram", "2", "anna", "вопрос", 2.0),
        history=[{"message_id": "1", "sender": "anna", "direction": "incoming", "body": "потом"}],
        history_reader=reader,
    )

    assert result["decision"] == "review"


def test_read_more_loop_nudges_plain_answers_toward_submit() -> None:
    replies = iter([
        _text_response("думаю..."),
        _tool_response("submit_triage", _TRIAGE_ARGUMENTS),
    ])

    def runner(request, *, timeout):
        return next(replies)

    result = _generator(runner).triage_with_context(
        conversation_id="conv_1",
        latest_message=InboxMessage("telegram", "2", "anna", "вопрос", 2.0),
        history=[],
        history_reader=lambda _before: [],
    )

    assert result["decision"] == "review"


def test_legacy_triage_without_reader_keeps_forced_tool_choice() -> None:
    requests = []

    def runner(request, *, timeout):
        requests.append(json.loads(request.data))
        return _tool_response("submit_triage", _TRIAGE_ARGUMENTS)

    result = _generator(runner).triage_with_context(
        conversation_id="conv_1",
        latest_message=InboxMessage("telegram", "2", "anna", "вопрос", 2.0),
        history=[],
    )

    assert result["decision"] == "review"
    assert requests[0]["tool_choice"] == {
        "type": "function", "function": {"name": "submit_triage"},
    }


class _TriageGenerator:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls: list[dict] = []

    def suggest(self, **_kwargs: object) -> str:
        return "draft"

    def triage_with_context(self, **kwargs):
        self.calls.append(kwargs)
        return dict(self.result)


class _Outbox:
    def send_reply(self, **_kwargs: object) -> str:
        return "receipt-1"


class _ReactOutbox:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send_reply(self, **_kwargs: object) -> str:
        return "receipt-1"

    def react(self, *, chat, chat_id, account_ref, message_id, emoji):
        self.calls.append({
            "chat": chat, "chat_id": chat_id, "account_ref": account_ref,
            "message_id": message_id, "emoji": emoji,
        })
        return {"ok": True, "slot": "slot-1", "reacted": True}


def _review_result() -> dict:
    return {
        "decision": "review", "importance": 0.55, "urgency": "low", "confidence": 0.6,
        "reason_codes": ["ambiguous"], "reason_ru": "Контекст неполный.",
        "action_required": False, "action_summary": "Проверить.", "deadline_at": None,
        "suggested_replies": [{"body": "Первый вариант"}, {"body": "Второй вариант"}],
        "safety_override": False, "policy_version": "model-v1",
    }


def _service(tmp_path, generator, *, telegram_outbox=None) -> UserIOService:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    return UserIOService(store, generator, _Outbox(), telegram_outbox=telegram_outbox)


def test_review_triage_keeps_draft_variants(tmp_path) -> None:
    generator = _TriageGenerator(_review_result())
    service = _service(tmp_path, generator)
    service.receive(InboxMessage("telegram", "c1:12", "Печкин", "вопрос", 1.0, conversation_kind="direct"), route_id="review-drafts")
    seq = int(service._store.workspace_events()["events"][-1]["seq"])

    result = service.triage_workspace_event(event_seq=seq, request_id="req-review")

    assert result["triage"]["decision"] == "review"
    assert [draft["body"] for draft in result["drafts"]] == ["Первый вариант", "Второй вариант"]
    assert all(draft["id"] and draft["status"] == "proposed" for draft in result["drafts"])
    assert callable(generator.calls[0]["history_reader"])


def test_review_triage_draft_can_be_sent(tmp_path) -> None:
    generator = _TriageGenerator(_review_result())
    service = _service(tmp_path, generator)
    service.receive(InboxMessage("telegram", "c1:12", "Печкин", "вопрос", 1.0, conversation_kind="direct"), route_id="review-drafts")
    seq = int(service._store.workspace_events()["events"][-1]["seq"])
    result = service.triage_workspace_event(event_seq=seq, request_id="req-review")
    draft_id = str(result["drafts"][0]["id"])

    sent = service.send_workspace_triage(
        event_seq=seq, request_id="req-review", draft_id=draft_id,
        actor="telegram:1", confirm=True,
    )

    assert sent["sent"] is True


def test_silent_triage_still_has_no_drafts(tmp_path) -> None:
    generator = _TriageGenerator({
        "decision": "silent", "importance": 0.1, "urgency": "none", "confidence": 0.95,
        "reason_codes": ["trivial"], "reason_ru": "Мур мур.", "action_required": False,
        "action_summary": "Ничего не делать.", "deadline_at": None,
        "suggested_replies": [{"body": "Мур"}], "safety_override": False,
        "policy_version": "model-v1",
    })
    service = _service(tmp_path, generator)
    service.receive(InboxMessage("telegram", "c1:12", "Печкин", "вопрос", 1.0, conversation_kind="direct"), route_id="review-drafts")
    seq = int(service._store.workspace_events()["events"][-1]["seq"])

    result = service.triage_workspace_event(event_seq=seq, request_id="req-silent")

    assert result["triage"]["decision"] == "silent"
    assert result["drafts"] == []


def test_failed_triage_keeps_error_detail(tmp_path) -> None:
    class FailingGenerator(_TriageGenerator):
        def triage_with_context(self, **kwargs):
            self.calls.append(kwargs)
            raise ValueError("AI triage JSON enum")

    service = _service(tmp_path, FailingGenerator(_review_result()))
    service.receive(InboxMessage("telegram", "c1:12", "Печкин", "вопрос", 1.0, conversation_kind="direct"), route_id="review-drafts")
    seq = int(service._store.workspace_events()["events"][-1]["seq"])

    result = service.triage_workspace_event(event_seq=seq, request_id="req-fail")

    assert "AI triage JSON enum" in str(result.get("last_error"))


def test_react_to_message_routes_telegram_conversation(tmp_path) -> None:
    outbox = _ReactOutbox()
    service = _service(tmp_path, _TriageGenerator(_review_result()), telegram_outbox=outbox)
    service.receive(InboxMessage("telegram", "c1:12", "Печкин", "вопрос", 1.0, conversation_kind="direct"), route_id="react-test")
    conversation_id = service._store.workspace_events()["events"][-1]["conversation_id"]

    result = service.react_to_message(
        conversation_id=conversation_id, message_id="c1:12", emoji="🐾", confirm=True,
    )

    assert result["ok"] is True
    assert outbox.calls[0]["message_id"] == "c1:12"
    assert outbox.calls[0]["emoji"] == "🐾"
    assert outbox.calls[0]["chat"] == "Печкин"


def test_react_to_message_requires_exact_confirmation(tmp_path) -> None:
    outbox = _ReactOutbox()
    service = _service(tmp_path, _TriageGenerator(_review_result()), telegram_outbox=outbox)
    service.receive(InboxMessage("telegram", "c1:12", "Печкин", "вопрос", 1.0, conversation_kind="direct"), route_id="react-test")
    conversation_id = service._store.workspace_events()["events"][-1]["conversation_id"]

    result = service.react_to_message(
        conversation_id=conversation_id, message_id="c1:12", emoji="🐾", confirm=False,
    )

    assert result == {"ok": False, "error": "exact_confirmation_required"}
    assert outbox.calls == []


def test_react_to_message_rejects_unsupported_source(tmp_path) -> None:
    service = _service(tmp_path, _TriageGenerator(_review_result()))
    service.receive(InboxMessage("gmail:self", "m-1", "a@b.c", "hi", 1.0), route_id="react-test")
    conversation_id = service._store.workspace_events()["events"][-1]["conversation_id"]

    result = service.react_to_message(
        conversation_id=conversation_id, message_id="m-1", emoji="🐾", confirm=True,
    )

    assert result == {"ok": False, "error": "reaction_not_supported_for_source"}


def test_telegram_outbox_react_posts_connector_contract() -> None:
    requests = []

    def runner(request, **_kwargs):
        requests.append(request)
        return _Response({"ok": True, "slot": "slot-1", "reacted": True})

    outbox = TelegramQrHttpOutbox("https://connector.example", "secret", runner=runner)

    receipt = outbox.react(
        chat="Печкин", chat_id="c1", account_ref="telegram:1",
        message_id="acc|c1:12", emoji="🐾",
    )

    assert receipt == {"ok": True, "slot": "slot-1", "reacted": True}
    request = requests[0]
    assert request.full_url == "https://connector.example/react"
    payload = json.loads(request.data)
    assert payload == {
        "chat": "Печкин", "chat_id": "c1", "account_id": "telegram:1",
        "message_id": "acc|c1:12", "emoji": "🐾",
    }
    assert request.get_header("Authorization") == "Bearer secret"


def test_read_more_loop_answers_every_parallel_tool_call() -> None:
    def _calls_response() -> _Response:
        return _Response({"choices": [{"message": {"content": "ignored", "tool_calls": [
            {"id": "call-a", "type": "function", "function": {
                "name": "read_more_context", "arguments": json.dumps({"reason": "первый"}),
            }},
            {"id": "call-b", "type": "function", "function": {
                "name": "read_more_context", "arguments": json.dumps({"reason": "второй"}),
            }},
        ]}}]})

    requests = []
    replies = iter([_calls_response(), _tool_response("submit_triage", _TRIAGE_ARGUMENTS)])

    def runner(request, *, timeout):
        requests.append(json.loads(request.data))
        return next(replies)

    def reader(before_message_id: str):
        return [] if before_message_id == "0" else [
            {"message_id": "0", "sender": "anna", "direction": "incoming", "body": "старое"},
        ]

    result = _generator(runner).triage_with_context(
        conversation_id="conv_1",
        latest_message=InboxMessage("telegram", "2", "anna", "вопрос", 2.0),
        history=[{"message_id": "1", "sender": "anna", "direction": "incoming", "body": "потом"}],
        history_reader=reader,
    )

    assert result["decision"] == "review"
    follow_up = requests[1]
    echo = follow_up["messages"][-3]
    assert echo["role"] == "assistant" and len(echo["tool_calls"]) == 2
    answers = follow_up["messages"][-2:]
    assert [answer["role"] for answer in answers] == ["tool", "tool"]
    assert [answer["tool_call_id"] for answer in answers] == ["call-a", "call-b"]


def test_before_message_id_outside_conversation_returns_empty(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, _TriageGenerator(_review_result()), _Outbox())
    conversation_id = ""
    for index in range(1, 6):
        conversation_id, _ = service.receive(
            InboxMessage("telegram", str(index), "contact", f"message-{index}", float(index)),
            route_id="telegram",
        )

    assert store.bounded_conversation_context(conversation_id, before_message_id="999") == []
    assert [
        item["message_id"] for item in
        store.bounded_conversation_context(conversation_id, before_message_id="3")
    ] == ["1", "2"]
