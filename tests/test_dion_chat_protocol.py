import pytest

from universal_userio.dion_chat_protocol import DionChatProtocolError, parse_page, sync_once


def test_page_uses_provider_ids_and_derives_direction():
    page = parse_page({"messages": [{"id": "m1", "sender_id": "owner", "text": "hi", "timestamp_ms": 4}], "next_cursor": "c2"}, chat_id="chat-1", own_user_id="owner")
    assert page.messages[0].provider_message_id == "m1"
    assert page.messages[0].direction == "out"
    assert page.next_cursor == "c2"


def test_rejects_display_name_only_message():
    with pytest.raises(DionChatProtocolError):
        parse_page({"messages": [{"id": "m1", "sender_name": "Owner", "text": "hi"}]}, chat_id="c", own_user_id="owner")


def test_transport_is_injected_and_cursor_is_forwarded():
    class Fixture:
        def __init__(self): self.cursor = object()
        def list_messages(self, chat_id, cursor):
            assert chat_id == "c" and cursor == "old"
            return {"messages": [], "next_cursor": "new"}
    page = sync_once(Fixture(), chat_id="c", own_user_id="owner", cursor="old")
    assert page.next_cursor == "new"
