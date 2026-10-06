from __future__ import annotations

import json

import pytest

from universal_userio.ai import OpenAICompatibleDraftGenerator
from universal_userio.contracts import InboxMessage


def _response(content: str):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def read() -> bytes:
            return json.dumps({"choices": [{"message": {"content": content}}]}).encode()

    return Response()


def _tool_response(arguments: object, *, name: str = "submit_triage"):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def read() -> bytes:
            return json.dumps({"choices": [{"message": {"content": "ignored", "tool_calls": [{
                "type": "function", "function": {
                    "name": name,
                    "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments),
                },
            }]}}]}).encode()

    return Response()


def test_variants_are_separate_calls_with_context_and_auth() -> None:
    requests = []
    replies = iter(["<think>reasoning</think>\ndraft one", "draft two"])

    def runner(request, *, timeout):
        requests.append((request, timeout))
        return _response(next(replies))

    generator = OpenAICompatibleDraftGenerator(endpoint="https://ai.example/v1", token="secret", model="business-model", runner=runner)
    drafts = generator.suggest_with_context(
        conversation_id="conv_1", latest_message=InboxMessage("vk", "2", "anna", "latest", 2.0),
        history=[{"sender": "anna", "body": "earlier"}], limit=2,
    )

    # Providers like MiniMax reject n>1, so each variant is its own call...
    assert len(requests) == 2
    payload = json.loads(requests[0][0].data)
    assert "n" not in payload
    assert "anna: earlier" in payload["messages"][1]["content"]
    assert requests[0][0].get_header("Authorization") == "Bearer secret"
    # ...and <think> blocks are stripped from reasoning-model answers.
    assert drafts == ["draft one", "draft two"]


def test_duplicate_variants_are_not_repeated() -> None:
    def runner(request, *, timeout):
        return _response("same answer")

    generator = OpenAICompatibleDraftGenerator(endpoint="https://ai.example/v1", token="secret", model="m", runner=runner)
    drafts = generator.suggest_with_context(
        conversation_id="conv_1", latest_message=InboxMessage("vk", "2", "anna", "latest", 2.0),
        history=[], limit=3,
    )
    assert drafts == ["same answer"]


def test_importance_triage_is_one_bounded_json_call_with_last_twenty_messages() -> None:
    requests = []
    content = json.dumps({
        "decision": "notify",
        "importance": 0.91,
        "urgency": "high",
        "confidence": 0.88,
        "reason_codes": ["explicit_question"],
        "reason_ru": "Нужен ответ сегодня.",
        "action_required": True,
        "action_summary": "Ответить на вопрос.",
        "deadline_at": None,
        "suggested_replies": [
            {"body": "Да, отвечу сегодня."},
            {"body": "Принял, вернусь с ответом позже."},
        ],
        "safety_override": False,
        "policy_version": "model-value-is-replaced",
    })

    def runner(request, *, timeout):
        requests.append((request, timeout))
        return _tool_response(content)

    generator = OpenAICompatibleDraftGenerator(
        endpoint="https://ai.example/v1", token="secret", model="triage-model", runner=runner,
    )
    result = generator.triage_with_context(
        conversation_id="conv_1",
        latest_message=InboxMessage("telegram", "25", "anna", "latest", 25.0),
        history=[{"sender": "anna", "body": f"message-{index}"} for index in range(25)],
        max_drafts=2,
        actor_context={
            "sender_account_id": "telegram:200", "sender_display_name": "Секретарь",
            "receiver_account_id": "telegram:100", "receiver_display_name": "Никита",
        },
    )

    assert len(requests) == 1
    payload = json.loads(requests[0][0].data)
    assert payload["tool_choice"]["function"]["name"] == "submit_triage"
    assert payload["tools"][0]["function"]["parameters"]["additionalProperties"] is False
    prompt = payload["messages"][1]["content"]
    context = json.loads(prompt.splitlines()[-1])
    assert context["sender"] == "Секретарь"
    assert context["raw_sender_label"] == "anna"
    assert context["actor_context"]["receiver_account_id"] == "telegram:100"
    assert context["direction"] == "incoming"
    assert "message-4" not in prompt
    assert "message-5" in prompt and "message-24" in prompt
    assert result["importance"] == 0.91
    assert result["suggested_replies"] == [
        {"body": "Да, отвечу сегодня."},
        {"body": "Принял, вернусь с ответом позже."},
    ]


def test_importance_triage_parser_rejects_unbounded_or_extra_output() -> None:
    invalid = {
        "decision": "notify", "importance": 0.9, "urgency": "high", "confidence": 0.9,
        "reason_codes": ["question"], "reason_ru": "reason", "action_required": True,
        "action_summary": "act", "deadline_at": None,
        "suggested_replies": [{"body": "x" * 2001}], "safety_override": False,
        "policy_version": "v1", "unexpected": True,
    }
    generator = OpenAICompatibleDraftGenerator(
        endpoint="https://ai.example/v1", token="secret", model="m",
        runner=lambda *_args, **_kwargs: _tool_response(invalid),
    )

    with pytest.raises(ValueError, match="triage JSON"):
        generator.triage_with_context(
            conversation_id="conv_1", latest_message=InboxMessage("vk", "1", "a", "b", 1.0),
            history=[], max_drafts=2,
        )


@pytest.mark.parametrize("response", [
    _response('{"messages": []}'),
    _tool_response({}, name="another_tool"),
    _tool_response("not-json"),
])
def test_importance_triage_rejects_content_wrappers_wrong_tools_and_invalid_arguments(response) -> None:
    generator = OpenAICompatibleDraftGenerator(
        endpoint="https://ai.example/v1", token="secret", model="m",
        runner=lambda *_args, **_kwargs: response,
    )

    with pytest.raises(ValueError, match="triage"):
        generator.triage_with_context(
            conversation_id="conv_1", latest_message=InboxMessage("vk", "1", "a", "b", 1.0),
            history=[], max_drafts=2,
        )
