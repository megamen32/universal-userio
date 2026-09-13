from __future__ import annotations

import asyncio

import pytest

from userio_adapter_sdk import ChatMessage, Omnichannel


class ChannelStub:
    capabilities = frozenset({"read", "send"})

    def __init__(self, platform: str) -> None:
        self.platform = platform
        self.sent: list[tuple[object, str, int | None]] = []

    async def send_message(self, chat, text: str, *, reply_to: int | None = None):
        self.sent.append((chat, text, reply_to))
        return ChatMessage(chat_id=chat, id=len(self.sent), text=text, out=True)


def test_omnichannel_routes_by_platform_and_account_without_provider_objects() -> None:
    channels = Omnichannel()
    work = ChannelStub("telegram")
    sales = ChannelStub("telegram")
    sms = ChannelStub("sms")

    channels.register(work, account_id="work")
    channels.register(sales, account_id="sales")
    channels.register(sms)

    sent = asyncio.run(
        channels.send_message(
            "TELEGRAM", "buyer", "Hello", account_id="sales", reply_to=41
        )
    )

    assert sent.text == "Hello"
    assert sales.sent == [("buyer", "Hello", 41)]
    assert work.sent == []
    assert channels.platforms == ("sms", "telegram")
    assert [(item.platform, item.account_id) for item in channels.bindings] == [
        ("sms", "default"),
        ("telegram", "sales"),
        ("telegram", "work"),
    ]


def test_omnichannel_requires_account_for_ambiguous_platform() -> None:
    channels = Omnichannel()
    channels.register(ChannelStub("email"), account_id="one")
    channels.register(ChannelStub("email"), account_id="two")

    with pytest.raises(ValueError, match="account_id is required"):
        channels.channel("email")


def test_omnichannel_refuses_accidental_account_replacement() -> None:
    channels = Omnichannel()
    channels.register(ChannelStub("vk"), account_id="sales")

    with pytest.raises(ValueError, match="already registered"):
        channels.register(ChannelStub("vk"), account_id="sales")
