import json

from universal_userio.adapters import ByokBridgeGenerator
from universal_userio.contracts import InboxMessage


def test_byok_bridge_receives_current_message_and_real_speaker_roles():
    requests = []

    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self): return b'{"text":"reply"}'

    def runner(request, *, timeout):
        requests.append(json.loads(request.data))
        return Response()

    generator = ByokBridgeGenerator(bridge_url="http://bridge.test", bridge_token="bridge-token",
                                    endpoint="https://user-model.test/v1", model="owned-model",
                                    api_key="private-key", runner=runner)
    generator.suggest_with_actor_context(
        conversation_id="owned-conversation", latest_message=InboxMessage("max", "current-9", "routing-label", "New question", 9),
        history=[{"message_id":"old-8", "sender":"routing-label", "direction":"outgoing", "body":"Owner already answered"}],
        limit=1, actor_context={"owner_name":"Никита", "interlocutor_name":"Дарья", "conversation_kind":"direct"},
    )
    payload = requests[0]
    context = json.loads(payload["prompt"].split("\n")[-1])
    assert context["latest_message"]["message_id"] == "current-9"
    assert context["latest_message"]["body"] == "New question"
    assert context["latest_message"]["sender_label"] == "Дарья"
    assert context["history"][0]["speaker_role"] == "owner"
    assert context["history"][0]["sender_label"] == "Никита"
    assert payload["model_id"] == "owned-model"
    assert payload["base_url"] == "https://user-model.test/v1"


def test_text_only_bridge_does_not_claim_image_pixels_are_available():
    captured = []
    generator = ByokBridgeGenerator(bridge_url="http://bridge.test", bridge_token="t",
                                    endpoint="https://user-model.test/v1", model="m", api_key="k")
    generator._chat = lambda prompt: captured.append(prompt) or "reply"
    message = InboxMessage("max", "sticker-1", "peer", "", 1,
                           attachments=({"kind":"sticker", "data_url":"data:image/png;base64,secret-pixels"},))
    generator.suggest_with_context(conversation_id="c", latest_message=message, history=[], limit=1)
    assert "secret-pixels" not in captured[0]
    context = json.loads(captured[0].split("\n")[-1])
    assert context["latest_message"]["message_kind"] == "sticker_reaction"
    assert context["latest_message"]["attachments"][0]["image_available"] is False
