"""Unit tests for the Telegram channel adapter (no network access)."""

import asyncio
from types import SimpleNamespace

import pytest

from userio_adapter_sdk import Contact

from universal_userio.channels.telegram import (
    TelegramAPI,
    _proxy_spec,
    parse_msg_url,
    telegram_env_config,
)


class _StubClient:
    """Minimal stand-in for a Telethon client instance."""


class _RichStubClient:
    def __init__(self) -> None:
        self.requests = []
        self.uploads = []
        self.kicks = []

    async def get_entity(self, target):
        if target == "engineers":
            return SimpleNamespace(id=900, title="Engineers")
        return SimpleNamespace(
            id=int(target) if isinstance(target, int) else 42,
            first_name="Ada",
            last_name="Lovelace",
            username="ada",
            phone="15550001",
            contact=True,
        )

    async def send_file(self, target, file, **kwargs):
        self.uploads.append((target, file.read(), file.name, kwargs))
        return SimpleNamespace(
            id=81, message=kwargs.get("caption", ""), out=True,
            file=SimpleNamespace(name=file.name, mime_type=kwargs.get("mime_type")),
        )

    async def kick_participant(self, group, contact):
        self.kicks.append((group, contact))

    async def __call__(self, request):
        self.requests.append(request)
        if request.__class__.__name__ == "ImportContactsRequest":
            imported = request.contacts[0]
            return SimpleNamespace(users=[SimpleNamespace(
                id=42, first_name=imported.first_name, last_name=imported.last_name,
                username="ada", phone=imported.phone, contact=True,
            )])
        return SimpleNamespace()


def test_parse_msg_url_public_and_private() -> None:
    assert parse_msg_url("https://t.me/somechannel/42") == ("somechannel", 42)
    assert parse_msg_url("https://t.me/c/123456/7") == (-100123456, 7)


def test_adapter_metadata() -> None:
    adapter = TelegramAPI(_StubClient())
    assert adapter.platform == "telegram"
    assert {
        "read", "send", "edit", "media", "download", "upload",
        "get_contact", "add_contact", "edit_contact", "remove_contact",
        "add_contact_to_group", "remove_contact_from_group",
    } <= adapter.capabilities


def test_file_contact_and_group_operations_are_provider_neutral() -> None:
    client = _RichStubClient()
    adapter = TelegramAPI(client)

    async def exercise():
        uploaded = await adapter.upload(
            "chat", b"hello", filename="hello.txt", mime_type="text/plain", caption="Hi"
        )
        found = await adapter.get_contact(42)
        added = await adapter.add_contact(Contact(display_name="Ada Byron", phone="+15550001"))
        edited = await adapter.edit_contact(42, display_name="Ada King")
        removed = await adapter.remove_contact(42)
        joined = await adapter.add_contact_to_group(42, "engineers")
        left = await adapter.remove_contact_from_group(42, "engineers")
        return uploaded, found, added, edited, removed, joined, left

    uploaded, found, added, edited, removed, joined, left = asyncio.run(exercise())
    assert (uploaded.filename, uploaded.text) == ("hello.txt", "Hi")
    assert client.uploads[0][1:3] == (b"hello", "hello.txt")
    assert found == Contact(
        id=42, display_name="Ada Lovelace", first_name="Ada", last_name="Lovelace",
        username="ada", phone="15550001", is_contact=True,
    )
    assert (added.display_name, edited.display_name) == ("Ada Byron", "Ada King")
    assert (removed, joined, left) == (True, True, True)
    assert [item.__class__.__name__ for item in client.requests] == [
        "ImportContactsRequest", "ImportContactsRequest", "DeleteContactsRequest",
        "InviteToChannelRequest",
    ]
    assert len(client.kicks) == 1


def test_env_config_resolution_and_fallbacks() -> None:
    config = telegram_env_config(
        {
            "TG_API_ID": "12345",
            "TG_API_HASH": "hash",
            "TG_SESSION": "session-string",
            "TG_PROXY": "socks5://127.0.0.1:1080",
        }
    )
    assert config.api_id == 12345
    assert config.proxy == {
        "proxy_type": "socks5",
        "addr": "127.0.0.1",
        "port": 1080,
        "rdns": True,
    }

    preferred = telegram_env_config(
        {
            "USERIO_TELEGRAM_API_ID": "9",
            "USERIO_TELEGRAM_API_HASH": "h",
            "USERIO_TELEGRAM_SESSION": "s",
        }
    )
    assert preferred.api_id == 9
    assert preferred.proxy is None


def test_env_config_missing_raises() -> None:
    with pytest.raises(ValueError, match="api_hash"):
        telegram_env_config({"TG_API_ID": "1", "TG_SESSION": "s"})


def test_proxy_spec_variants() -> None:
    assert _proxy_spec(None) is None
    assert _proxy_spec("http://proxy.local") is None
    authenticated = _proxy_spec("socks5h://user:pass@proxy.local:9050")
    assert authenticated == {
        "proxy_type": "socks5",
        "addr": "proxy.local",
        "port": 9050,
        "rdns": True,
        "username": "user",
        "password": "pass",
    }
