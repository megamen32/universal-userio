from universal_userio import adapters


def test_legacy_media_download_uses_owned_conversation_account(monkeypatch):
    message = {"message_id": "-5491867632:123", "conversation_id": "c", "sender": "chat"}
    class Store:
        def message(self, message_id, **kwargs):
            return message if message_id == message["message_id"] else None
        def attachments_for_message(self, **kwargs):
            return [{"kind": "voice", "idx": 0}]
        def conversation(self, conversation_id, **kwargs):
            assert conversation_id == "c"
            return {"account_ref": "telegram:540308572", "peer_id": "-5491867632"}
    captured = {}
    def download(**kwargs):
        captured.update(kwargs)
        return b"ok"
    monkeypatch.setattr(adapters, "_download_via_bridge", download)
    adapter = adapters.TelegramChannelAdapter(Store(), object(), "owner", bridge_url="http://localhost")
    assert adapter.download(file_ref=message["message_id"]) == b"ok"
    assert captured["bridge_context"] == {"account_id": "telegram:540308572"}
