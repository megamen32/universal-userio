"""Capabilities of built-in adapters, read from their actual implementations."""
from __future__ import annotations

from importlib import import_module

_ADAPTERS = {
    'telegram': ('telegram', 'TelegramAPI'),
    'email': ('email', 'EmailChannel'),
    'sms': ('sms', 'AndroidSmsChannel'),
    'whatsapp': ('whatsapp', 'WhatsAppChannel'),
    'vk': ('vk', 'VkChannel'),
    'max': ('max', 'MaxChannel'),
}


def channel_capabilities(platform: str) -> frozenset[str]:
    """Return implemented capabilities without opening an account or network connection."""
    target = _ADAPTERS.get(str(platform).strip().lower())
    if target is None:
        return frozenset()
    try:
        adapter = getattr(import_module(f'universal_userio.channels.{target[0]}'), target[1])
    except ImportError:
        return frozenset()  # The optional provider dependency is not installed.
    return frozenset(adapter.capabilities)
