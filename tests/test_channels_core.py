"""Unit tests for the universal channels core contracts."""

import pytest

from universal_userio.channels import (
    AdapterNotSupported,
    ChatMessage,
    ChatPort,
    ChatRateLimitError,
    ChatSummary,
    DownloadedMedia,
    mapping_value,
)


def test_chat_summary_accepts_id_and_name_aliases() -> None:
    summary = ChatSummary(chat_id=7, name="Sales")
    assert summary.id == 7
    assert summary.chat_id == 7
    assert summary.title == "Sales"
    assert summary.name == "Sales"


def test_chat_message_requires_chat_and_id() -> None:
    message = ChatMessage(chat=5, message_id=9, message="hi")
    assert message.chat_id == 5
    assert message.id == 9
    assert message.text == "hi"
    with pytest.raises(TypeError):
        ChatMessage(chat_id=5)


def test_downloaded_media_aliases() -> None:
    media = DownloadedMedia(chat=1, message_id=2, content=b"x", media_type="image/png")
    assert media.data == b"x"
    assert media.bytes == b"x"
    assert media.mime_type == "image/png"


def test_error_hierarchy_and_adapter_not_supported() -> None:
    assert issubclass(ChatRateLimitError, Exception)
    assert issubclass(AdapterNotSupported, ValueError)


def test_chat_port_runtime_check_on_full_implementation() -> None:
    class FakeChannel:
        platform = "fake"
        capabilities = frozenset({"read", "send"})

        async def list_chats(self): ...
        async def read_chat(self, chat, limit=100): ...
        async def read_message(self, chat, message_id): ...
        async def acknowledge_chat(self, chat): ...
        async def download_media(self, chat, message): ...
        async def send_message(self, chat, text, *, reply_to=None): ...
        async def forward_message(self, source_chat, message, target_chat): ...
        async def delete_message(self, chat, message): ...
        async def edit_message(self, chat, message, text): ...
        async def react(self, chat, message, emoji): ...
        def typing(self, chat): ...

    fake = FakeChannel()
    assert isinstance(fake, ChatPort)
    assert fake.platform == "fake"
    assert "send" in fake.capabilities


def test_mapping_value_first_present_key() -> None:
    assert mapping_value({"b": 2, "a": 1}, "a", "b") == 1
    assert mapping_value({"b": 2}, "a", default=5) == 5
