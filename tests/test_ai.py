from __future__ import annotations

import json

from universal_userio.ai import OpenAICompatibleDraftGenerator
from universal_userio.contracts import InboxMessage


def test_openai_compatible_adapter_receives_context_and_returns_choices() -> None:
    requests = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def read() -> bytes:
            return b'{"choices":[{"message":{"content":"draft one"}},{"message":{"content":"draft two"}}]}'

    def runner(request, *, timeout):
        requests.append((request, timeout))
        return Response()

    generator = OpenAICompatibleDraftGenerator(endpoint="https://ai.example/v1", token="secret", model="business-model", runner=runner)
    drafts = generator.suggest_with_context(
        conversation_id="conv_1", latest_message=InboxMessage("vk", "2", "anna", "latest", 2.0),
        history=[{"sender": "anna", "body": "earlier"}], limit=2,
    )

    payload = json.loads(requests[0][0].data)
    assert "anna: earlier" in payload["messages"][1]["content"]
    assert requests[0][0].get_header("Authorization") == "Bearer secret"
    assert drafts == ["draft one", "draft two"]
