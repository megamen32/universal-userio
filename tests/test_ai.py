from __future__ import annotations

import json

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
