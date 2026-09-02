"""Universal channel adapters library.

Core contracts are stdlib-only; per-platform adapters are imported explicitly
(``universal_userio.channels.telegram``, ...) so projects install only the
extras they need.  Spec: docs/2026-09-03-universal-adapters-spec.md.
"""

from universal_userio.channels.core import (
    AdapterNotSupported,
    Channel,
    ChatId,
    ChatInvalidPeerError,
    ChatMessage,
    ChatOperationError,
    ChatPermissionError,
    ChatPort,
    ChatRateLimitError,
    ChatRef,
    ChatSummary,
    DownloadedMedia,
    MessageRef,
    mapping_value,
)

__all__ = [
    "AdapterNotSupported",
    "Channel",
    "ChatId",
    "ChatInvalidPeerError",
    "ChatMessage",
    "ChatOperationError",
    "ChatPermissionError",
    "ChatPort",
    "ChatRateLimitError",
    "ChatRef",
    "ChatSummary",
    "DownloadedMedia",
    "MessageRef",
    "mapping_value",
]
