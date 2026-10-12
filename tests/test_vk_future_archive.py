"""Unavailable VK personal messages path stays inert; prototype is archived."""
from __future__ import annotations

import inspect
from pathlib import Path

from universal_userio import account_connections
from universal_userio.http_api import _vk_connect_page

ROOT = Path(__file__).resolve().parents[1]


def test_vip_prototype_is_present_but_not_imported_by_production():
    vip=ROOT / "VIP" / "vk-messages-api"
    for basename in ("README.md","prototype_vk_native.py",
                     "prototype-account-integration.md","prototype_vk_native_tests.py.txt"):
        assert (vip / basename).is_file()
    assert "Status: archived" in (vip / "README.md").read_text()
    assert not (ROOT / "src" / "universal_userio" / "channels" / "vk_native.py").exists()
    active=inspect.getsource(account_connections)
    assert "connect_vk_direct(" not in active
    assert "def poll_vk(" not in active
    assert "channels.vk_native" not in active
    # The official VK *community* reader is not the archived personal reader.
    assert "def poll_vk_community(" in active
    assert "def poll_vk(" not in active


def test_vkid_page_does_not_ask_for_unavailable_personal_messages_scope():
    page=_vk_connect_page("12345")
    assert "mode=direct" not in page
    assert "Запросить доступ к сообщениям VK" not in page
    assert "scope:''" in page
    assert "временно" not in page.lower() or "VK ID" in page
