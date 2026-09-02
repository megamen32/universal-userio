"""Unit tests for the Telegram channel adapter (no network access)."""

import pytest

from universal_userio.channels.telegram import (
    TelegramAPI,
    _proxy_spec,
    parse_msg_url,
    telegram_env_config,
)


class _StubClient:
    """Minimal stand-in for a Telethon client instance."""


def test_parse_msg_url_public_and_private() -> None:
    assert parse_msg_url("https://t.me/somechannel/42") == ("somechannel", 42)
    assert parse_msg_url("https://t.me/c/123456/7") == (-100123456, 7)


def test_adapter_metadata() -> None:
    adapter = TelegramAPI(_StubClient())
    assert adapter.platform == "telegram"
    assert {"read", "send", "edit", "media"} <= adapter.capabilities


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
