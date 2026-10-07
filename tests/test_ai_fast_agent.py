from __future__ import annotations

import json
import base64

import pytest

from universal_userio.ai import OpenAICompatibleDraftGenerator
from universal_userio.contracts import InboxMessage


class _Response:
    status = 200

    def __init__(self, message: dict[str, object]) -> None:
        self._body = json.dumps({"choices": [{"message": message}]}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return self._body


def _tool(name: str, arguments: object, *, call_id: str = "call-1") -> _Response:
    return _Response({
        "content": "",
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }],
    })


_TRIAGE = {
    "decision": "review",
    "importance": 0.5,
    "urgency": "low",
    "confidence": 0.8,
    "reason_codes": ["context_checked"],
    "reason_ru": "Контекст проверен.",
    "action_required": False,
    "action_summary": "Проверить.",
    "deadline_at": None,
    "suggested_replies": [],
    "safety_override": False,
    "policy_version": "v1",
}


def test_vision_payload_contains_bytes_without_transport_secrets() -> None:
    requests = []
    def runner(request, **_kwargs):
        requests.append(json.loads(request.data))
        return _tool("submit_triage", _TRIAGE)
    raw = b"\x89PNG\r\n\x1a\n" + b"bounded-image"
    attachment = {
        "content_type": "image/png", "image_bytes": raw,
        "src": "/private/secret-photo.png", "attachment_url": "https://provider/photo?token=SECRET_SENTINEL",
        "provider_ref": "SECRET_SENTINEL", "filename": "/private/secret-photo.png",
    }
    generator = OpenAICompatibleDraftGenerator(endpoint="https://ai.invalid", token="api", runner=runner)
    generator.triage_with_context(
        conversation_id="c", latest_message=InboxMessage("telegram", "1", "Катя", "фото", 1.0, attachments=(attachment,)),
        history=[], max_drafts=0,
    )
    payload = requests[0]
    assert payload["model"] == "MiniMax-M3"
    content = payload["messages"][1]["content"]
    assert content[1]["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(raw).decode()
    assert "SECRET_SENTINEL" not in json.dumps(payload)
    assert "/private/" not in json.dumps(payload)


def test_read_more_sanitizes_raw_attachment_fields_and_sends_images() -> None:
    requests = []
    def runner(request, **_kwargs):
        requests.append(json.loads(request.data))
        if len(requests) == 1:
            return _tool("read_more_context", {})
        return _tool("submit_triage", _TRIAGE)
    generator = OpenAICompatibleDraftGenerator(endpoint="https://ai.invalid", token="api", runner=runner)
    anchors = []
    def reader(anchor):
        anchors.append(anchor)
        return [{"body": "older", "provider_ref": "SECRET_SENTINEL", "attachments": [{
            "content_type": "image/jpeg", "image_bytes": b"\xff\xd8\xffimage",
            "src": "/private/photo", "attachment_url": "https://provider?token=SECRET_SENTINEL",
        }]}]
    generator.triage_with_context(
        conversation_id="c", latest_message=InboxMessage("telegram", "1", "Катя", "новое", 1.0),
        history=[{"message_id": "2", "anchor_id": "rowid:23", "body": "recent"}],
        history_reader=reader, max_drafts=0,
    )
    outbound = json.dumps(requests[1])
    assert anchors == ["rowid:23"]
    assert "data:image/jpeg;base64," in outbound
    assert "SECRET_SENTINEL" not in outbound and "/private/" not in outbound


@pytest.mark.parametrize("attachment", [
    {"content_type": "image/png", "image_bytes": b"not an image"},
    {"image_data_url": "https://provider?token=SECRET_SENTINEL"},
    {"content_type": "image/png", "image_bytes": b"\x89PNG\r\n\x1a\n" + b"x" * (2 * 1024 * 1024)},
])
def test_invalid_or_oversized_vision_data_is_not_forwarded(attachment) -> None:
    assert OpenAICompatibleDraftGenerator._vision_content("text", [attachment]) == "text"


def test_summary_vision_data_url_is_normalized_and_transport_fields_removed() -> None:
    requests = []
    def runner(request, **_kwargs):
        requests.append(json.loads(request.data))
        return _tool("submit_conversation_summary", {"summary": "Макет получен."})
    data_url = "data:image/gif;base64," + base64.b64encode(b"GIF89aimage").decode()
    generator = OpenAICompatibleDraftGenerator(endpoint="https://ai.invalid", token="api", runner=runner)
    generator.summarize_conversation(
        conversation_id="c", previous_summary="", token_budget=128,
        new_messages=[{"body": "макет", "attachments": [{
            "content_type": "image/gif", "image_data_url": data_url,
            "attachment_url": "https://provider?token=SECRET_SENTINEL", "filename": "/private/photo",
        }]}],
    )
    payload = requests[0]
    assert payload["messages"][1]["content"][1]["image_url"]["url"] == data_url
    assert "SECRET_SENTINEL" not in json.dumps(payload) and "/private/" not in json.dumps(payload)


def test_legacy_attachment_tuple_cannot_smuggle_url_or_token() -> None:
    requests: list[dict[str, object]] = []

    def runner(request, **_kwargs):
        requests.append(json.loads(request.data))
        return _tool("submit_conversation_summary", {"summary": "ok"})

    generator = OpenAICompatibleDraftGenerator(
        endpoint="https://ai.invalid", token="api", runner=runner,
    )
    generator.summarize_conversation(
        conversation_id="c", previous_summary="", token_budget=128,
        new_messages=[{
            "body": "legacy",
            "attachments": [("file", "https://internal/image?token=SECRET_SENTINEL")],
        }],
    )

    outbound = json.dumps(requests[0])
    assert "SECRET_SENTINEL" not in outbound
    assert "https://internal" not in outbound


def test_fast_agent_starts_with_small_context_and_cached_summary() -> None:
    requests: list[dict[str, object]] = []

    def runner(request, *, timeout):
        requests.append(json.loads(request.data))
        return _tool("submit_triage", _TRIAGE)

    generator = OpenAICompatibleDraftGenerator(
        endpoint="https://api.minimax.example/v1", token="secret", runner=runner,
    )
    result = generator.triage_with_context(
        conversation_id="conv-1",
        latest_message=InboxMessage("telegram", "7", "Катя", "Что думаешь?", 7.0),
        history=[
            {"message_id": str(index), "sender": "Катя", "body": f"message-{index}"}
            for index in range(1, 7)
        ],
        history_reader=lambda _before: [],
        cached_summary="Ранее договорились подписать официальный договор.",
    )

    assert result["decision"] == "review"
    payload = requests[0]
    assert payload["model"] == "MiniMax-M2.7"
    context = json.loads(str(payload["messages"][1]["content"]).splitlines()[-1])
    assert [item["body"] for item in context["history"]] == [
        "message-3", "message-4", "message-5", "message-6",
    ]
    assert context["cached_summary"].startswith("Ранее договорились")
    assert payload["tool_choice"] == "auto"


@pytest.mark.parametrize("attachments", [
    ({"kind": "image", "filename": "photo.bin"},),
    ({"content_type": "image/png", "filename": "photo"},),
    ({"filename": "photo.webp"},),
    ({"photo": True},),
])
def test_triage_routes_image_attachments_to_minimax_m3(attachments) -> None:
    requests: list[dict[str, object]] = []

    def runner(request, *, timeout):
        requests.append(json.loads(request.data))
        return _tool("submit_triage", _TRIAGE)

    generator = OpenAICompatibleDraftGenerator(
        endpoint="https://api.minimax.example/v1", token="secret", runner=runner,
    )
    generator.triage_with_context(
        conversation_id="conv-1",
        latest_message=InboxMessage(
            "telegram", "1", "Катя", "[image]", 1.0, attachments=attachments,
        ),
        history=[],
    )

    assert requests[0]["model"] == "MiniMax-M3"


def test_read_more_switches_to_image_model_and_honours_context_cap() -> None:
    requests: list[dict[str, object]] = []
    replies = iter([
        _tool("read_more_context", {"reason": "Нужна предыстория"}),
        _tool("submit_triage", _TRIAGE),
    ])

    def runner(request, *, timeout):
        requests.append(json.loads(request.data))
        return next(replies)

    generator = OpenAICompatibleDraftGenerator(
        endpoint="https://api.minimax.example/v1", token="secret", runner=runner,
    )
    generator.triage_with_context(
        conversation_id="conv-1",
        latest_message=InboxMessage("telegram", "10", "Катя", "Посмотри выше", 10.0),
        history=[{"message_id": "9", "sender": "Катя", "body": "выше"}],
        history_reader=lambda _before: [
            {"message_id": str(index), "sender": "Катя", "body": "old"}
            for index in range(1, 7)
        ] + [{
            "message_id": "7", "sender": "Катя", "body": "фото",
            "attachments": [{"mime_type": "image/jpeg"}],
        }],
        max_context_messages=2,
    )

    assert [payload["model"] for payload in requests] == ["MiniMax-M2.7", "MiniMax-M3"]
    tool_result = json.loads(requests[1]["messages"][-1]["content"])
    # The ceiling covers the initial message plus one older page entry.
    assert len(tool_result["older_messages"]) == 1
    assert tool_result["older_messages"][-1]["message_id"] == "7"


def test_read_more_keeps_one_vision_budget_for_complete_provider_payload() -> None:
    requests: list[dict[str, object]] = []
    replies = iter([
        _tool("read_more_context", {"reason": "page one"}),
        _tool("read_more_context", {"reason": "page two"}),
        _tool("submit_triage", _TRIAGE),
    ])

    def runner(request, *, timeout):
        requests.append(json.loads(request.data))
        return next(replies)

    raw = b"\x89PNG\r\n\x1a\n" + b"x" * (900 * 1024)
    attachment = {"content_type": "image/png", "image_bytes": raw}
    pages = iter([
        [{"message_id": "8", "body": "page one", "attachments": [attachment, attachment]}],
        [{"message_id": "7", "body": "page two", "attachments": [attachment, attachment]}],
    ])
    generator = OpenAICompatibleDraftGenerator(
        endpoint="https://api.minimax.example/v1", token="secret", runner=runner,
    )
    generator.triage_with_context(
        conversation_id="conv-1",
        latest_message=InboxMessage(
            "telegram", "10", "Катя", "Посмотри изображения", 10.0,
            attachments=(attachment, attachment),
        ),
        history=[{"message_id": "9", "body": "recent"}],
        history_reader=lambda _before: next(pages),
        max_context_messages=4,
    )

    image_urls = [
        part["image_url"]["url"]
        for message in requests[-1]["messages"]
        if isinstance(message.get("content"), list)
        for part in message["content"]
        if part.get("type") == "image_url"
    ]
    decoded = [base64.b64decode(url.split(",", 1)[1]) for url in image_urls]
    assert len(decoded) == 4
    assert sum(map(len, decoded)) <= 4 * 1024 * 1024


def test_read_more_truncates_one_large_message_to_token_budget() -> None:
    requests: list[dict[str, object]] = []
    replies = iter([
        _tool("read_more_context", {"reason": "Нужен текст выше"}),
        _tool("submit_triage", _TRIAGE),
    ])

    def runner(request, *, timeout):
        requests.append(json.loads(request.data))
        return next(replies)

    generator = OpenAICompatibleDraftGenerator(
        endpoint="https://api.minimax.example/v1", token="secret", runner=runner,
    )
    generator.triage_with_context(
        conversation_id="conv-1",
        latest_message=InboxMessage("telegram", "2", "Катя", "Что было?", 2.0),
        history=[{"message_id": "1", "sender": "Катя", "body": "выше"}],
        history_reader=lambda _before: [
            {"message_id": "0", "sender": "Катя", "body": "x" * 5000},
        ],
        max_context_token_budget=128,
    )

    tool_result = json.loads(requests[1]["messages"][-1]["content"])
    assert len(tool_result["older_messages"]) == 1
    assert 0 < len(tool_result["older_messages"][0]["body"]) < 5000


def test_read_more_charges_full_cyrillic_entry_conservatively() -> None:
    entry = {
        "message_id": "1", "sender": "Ж" * 320,
        "direction": "incoming", "body": "я" * 1500,
    }
    bounded = OpenAICompatibleDraftGenerator._bounded_context_entries(
        [entry], token_budget=512,
    )

    assert len(bounded) == 1
    assert 0 < len(bounded[0]["body"]) < 512
    assert OpenAICompatibleDraftGenerator._context_token_estimate(bounded[0]) <= 512


def test_incremental_summary_is_bounded_forced_tool_call_and_configurable_model() -> None:
    requests: list[dict[str, object]] = []

    def runner(request, *, timeout):
        requests.append(json.loads(request.data))
        return _tool("submit_conversation_summary", {
            "summary": "Катя прислала макет. Нужно проверить его сегодня.",
        })

    generator = OpenAICompatibleDraftGenerator(
        endpoint="https://ai.example/v1", token="secret", model="text-model",
        image_model="vision-model", summary_model="cheap-summary-model", runner=runner,
    )
    summary = generator.summarize_conversation(
        conversation_id="conv-1",
        previous_summary="Катя ждёт обратную связь.",
        new_messages=[{
            "message_id": "2", "sender": "Катя", "direction": "incoming",
            "body": "Вот макет", "attachments": [{"content_type": "image/png"}],
        }],
        token_budget=320,
    )

    assert summary == "Катя прислала макет. Нужно проверить его сегодня."
    payload = requests[0]
    assert payload["model"] == "vision-model"
    assert payload["max_tokens"] == 320
    assert payload["tool_choice"]["function"]["name"] == "submit_conversation_summary"
    assert payload["tools"][0]["function"]["parameters"]["additionalProperties"] is False
    context = json.loads(payload["messages"][1]["content"])
    assert context["previous_summary"] == "Катя ждёт обратную связь."
    assert context["new_messages"][0]["attachments"][0]["content_type"] == "image/png"


def test_text_summary_uses_configured_summary_model_and_validates_shape() -> None:
    requests: list[dict[str, object]] = []

    def runner(request, *, timeout):
        requests.append(json.loads(request.data))
        return _tool("submit_conversation_summary", {"summary": "ok", "extra": True})

    generator = OpenAICompatibleDraftGenerator(
        endpoint="https://ai.example/v1", token="secret", model="text-model",
        summary_model="summary-model", runner=runner,
    )
    with pytest.raises(ValueError, match="summary JSON keys"):
        generator.summarize_conversation(
            conversation_id="conv-1", previous_summary="", token_budget=128,
            new_messages=[{"message_id": "1", "body": "Только текст"}],
        )

    assert requests[0]["model"] == "summary-model"
