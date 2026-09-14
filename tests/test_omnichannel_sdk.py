from __future__ import annotations

import asyncio

import pytest

from userio_adapter_sdk import (
    AdapterCapabilities,
    AdapterNotSupported,
    ChatMessage,
    Contact,
    DownloadedMedia,
    FilePort,
    ContactPort,
    GroupContactPort,
    Omnichannel,
)


class ChannelStub:
    capabilities = frozenset({"read", "send"})

    def __init__(self, platform: str) -> None:
        self.platform = platform
        self.sent: list[tuple[object, str, int | None]] = []

    async def send_message(self, chat, text: str, *, reply_to: int | None = None):
        self.sent.append((chat, text, reply_to))
        return ChatMessage(chat_id=chat, id=len(self.sent), text=text, out=True)


class RichChannelStub(ChannelStub):
    capabilities = frozenset(AdapterCapabilities.ALL)

    def __init__(self, platform: str) -> None:
        super().__init__(platform)
        self.calls: list[tuple[object, ...]] = []

    async def download(self, chat, message):
        self.calls.append(("download", chat, message))
        return DownloadedMedia(chat_id=chat, message_id=7, data=b"file")

    async def upload(self, chat, data, *, filename, mime_type=None, caption=None):
        self.calls.append(("upload", chat, data, filename, mime_type, caption))
        return ChatMessage(chat_id=chat, id=8, text=caption or "", filename=filename, out=True)

    async def get_contact(self, contact):
        self.calls.append(("get_contact", contact))
        return Contact(id=contact, display_name="Ada")

    async def add_contact(self, contact):
        self.calls.append(("add_contact", contact))
        return Contact(id=42, display_name=contact.display_name, phone=contact.phone)

    async def edit_contact(self, contact, **changes):
        self.calls.append(("edit_contact", contact, changes))
        return Contact(id=contact, display_name=changes["display_name"])

    async def remove_contact(self, contact):
        self.calls.append(("remove_contact", contact))
        return True

    async def add_contact_to_group(self, contact, group):
        self.calls.append(("add_contact_to_group", contact, group))
        return True

    async def remove_contact_from_group(self, contact, group):
        self.calls.append(("remove_contact_from_group", contact, group))
        return True


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


def test_omnichannel_delegates_file_contact_and_group_operations() -> None:
    channels = Omnichannel()
    adapter = RichChannelStub("telegram")
    channels.register(adapter)
    draft = Contact(display_name="Ada Lovelace", phone="+15550001")

    async def exercise():
        downloaded = await channels.download("telegram", "chat", 7)
        uploaded = await channels.upload(
            "telegram", "chat", b"abc", filename="a.txt", mime_type="text/plain", caption="A"
        )
        found = await channels.get_contact("telegram", 42)
        added = await channels.add_contact("telegram", draft)
        edited = await channels.edit_contact("telegram", 42, display_name="Ada Byron")
        removed = await channels.remove_contact("telegram", 42)
        joined = await channels.add_contact_to_group("telegram", 42, "engineers")
        left = await channels.remove_contact_from_group("telegram", 42, "engineers")
        return downloaded, uploaded, found, added, edited, removed, joined, left

    downloaded, uploaded, found, added, edited, removed, joined, left = asyncio.run(exercise())
    assert downloaded.data == b"file"
    assert uploaded.filename == "a.txt"
    assert found == Contact(id=42, display_name="Ada")
    assert added.id == 42
    assert edited.display_name == "Ada Byron"
    assert (removed, joined, left) == (True, True, True)
    assert [call[0] for call in adapter.calls] == [
        "download", "upload", "get_contact", "add_contact", "edit_contact",
        "remove_contact", "add_contact_to_group", "remove_contact_from_group",
    ]


def test_omnichannel_enforces_adapter_capability_before_calling_missing_method() -> None:
    channels = Omnichannel()
    channels.register(ChannelStub("sms"))

    with pytest.raises(AdapterNotSupported, match="sms.*get_contact"):
        asyncio.run(channels.get_contact("sms", "+15550001"))


def test_adapter_capability_names_are_stable_and_complete() -> None:
    assert AdapterCapabilities.DOWNLOAD == "download"
    assert AdapterCapabilities.UPLOAD == "upload"
    assert {
        "get_contact", "add_contact", "edit_contact", "remove_contact",
        "add_contact_to_group", "remove_contact_from_group",
    } <= AdapterCapabilities.ALL
    rich = RichChannelStub("telegram")
    assert isinstance(rich, FilePort)
    assert isinstance(rich, ContactPort)
    assert isinstance(rich, GroupContactPort)


def test_registration_exposes_declared_caps_and_preserves_provider_extensions() -> None:
    channels = Omnichannel()
    rich = RichChannelStub("telegram")
    channels.register(rich)
    assert channels.capabilities("telegram") == AdapterCapabilities.ALL

    extended = ChannelStub("extended")
    extended.capabilities = frozenset({"send", "teleport"})
    channels.register(extended)
    assert channels.capabilities("extended") == frozenset({"send", "teleport"})
