"""Live Telegram delivery for the sync UserIO service (spec Phase 2).

Enabled with ``USERIO_LIVE_TELEGRAM=1`` plus adapter credentials from
``telegram_env_config``; approved drafts are then delivered in-process through
the universal Telegram channel adapter instead of a NoticePlace route.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from universal_userio.channels.sync_runner import SyncChannelRunner


class LiveTelegramOutbox:
    """Deliver approved drafts through an in-process Telegram channel adapter."""

    platform = "telegram"

    def __init__(self, adapter: object, *, runner: SyncChannelRunner | None = None) -> None:
        self._adapter = adapter
        self._runner = runner or SyncChannelRunner()

    def send_reply(self, *, chat: str, body: str, draft_id: str, chat_id: str = "") -> str:
        peer: int | str = int(chat) if chat.lstrip("-").isdigit() else chat
        message = self._runner.run(lambda: self._adapter.send_message(peer, body))
        return f"telegram:{getattr(message, 'id', 'sent')}:{draft_id}"

    def close(self) -> None:
        self._runner.close()

    def __enter__(self) -> "LiveTelegramOutbox":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def live_telegram_outbox_from_env(
    environment: Mapping[str, str] | None = None,
) -> LiveTelegramOutbox | None:
    """Build the live outbox when ``USERIO_LIVE_TELEGRAM`` enables it.

    The Telegram session comes from ``USERIO_TELEGRAM_SESSION``/``TG_SESSION``
    (a Telethon StringSession or a session file path).  Nothing connects to
    Telegram until the first approved draft is delivered.
    """

    source: Mapping[str, str] = os.environ if environment is None else environment
    flag = str(source.get("USERIO_LIVE_TELEGRAM", "")).strip().lower()
    if flag not in {"1", "true", "yes", "on"}:
        return None
    try:
        from universal_userio.channels.telegram import TelegramAPI
    except ImportError as error:  # pragma: no cover - depends on extras
        raise ImportError(
            "USERIO_LIVE_TELEGRAM=1 requires the telegram extra: "
            "pip install universal-userio[telegram]"
        ) from error
    return LiveTelegramOutbox(TelegramAPI.from_env(source))
