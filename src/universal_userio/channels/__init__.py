"""Universal channel adapters library.

Core contracts are stdlib-only; per-platform adapters are imported explicitly
(``universal_userio.channels.telegram``, ...) so projects install only the
extras they need.  Spec: docs/2026-09-03-universal-adapters-spec.md.
"""

from universal_userio.channels.core import (
    AdapterCapabilities,
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
    Contact,
    ContactPort,
    ContactRef,
    DownloadedMedia,
    FilePort,
    GroupContactPort,
    MessageRef,
    Omnichannel,
    mapping_value,
)

__all__ = [
    "AdapterNotSupported",
    "AdapterCapabilities",
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
    "Contact",
    "ContactPort",
    "ContactRef",
    "DownloadedMedia",
    "FilePort",
    "GroupContactPort",
    "MessageRef",
    "Omnichannel",
    "mapping_value",
]
