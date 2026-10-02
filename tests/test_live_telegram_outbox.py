"""Phase 2 tests: live Telegram delivery through the sync channel runner."""

from __future__ import annotations

import pytest

from universal_userio.channels.live_telegram import (
    LiveTelegramOutbox,
    live_telegram_outbox_from_env,
)
from universal_userio.channels.sync_runner import SyncChannelRunner
from universal_userio.contracts import InboxMessage
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def suggest(self, *, conversation_id: str, latest_message: InboxMessage) -> str:
        return "ok"


class SentMessage:
    id = 4242


class FakeAdapter:
    platform = "telegram"

    def __init__(self) -> None:
        self.calls: list[tuple[object, str]] = []

    async def send_message(self, chat, text, *, reply_to=None):
        self.calls.append((chat, text))
        return SentMessage()


class RecordingOutbox:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def send_reply(self, *, route_id, conversation_id, draft_id, body) -> str:
        self.calls.append(("noticeplace", body))
        return "notice-receipt"


def test_sync_runner_delivers_async_adapter_from_sync_code():
    adapter = FakeAdapter()
    with SyncChannelRunner() as runner:
        outbox = LiveTelegramOutbox(adapter, runner=runner)
        receipt = outbox.send_reply(chat="12345", body="hi", draft_id="draft_1")
    assert adapter.calls == [(12345, "hi")]
    assert receipt == "telegram:4242:draft_1"


def test_live_outbox_keeps_username_peers_as_strings():
    adapter = FakeAdapter()
    with SyncChannelRunner() as runner:
        LiveTelegramOutbox(adapter, runner=runner).send_reply(
            chat="@sales_lead", body="hi", draft_id="d"
        )
    assert adapter.calls == [("@sales_lead", "hi")]


def test_factory_respects_flag_and_credentials():
    assert live_telegram_outbox_from_env({}) is None
    assert live_telegram_outbox_from_env({"USERIO_LIVE_TELEGRAM": "0"}) is None
    with pytest.raises(ValueError, match="api_hash"):
        live_telegram_outbox_from_env(
            {"USERIO_LIVE_TELEGRAM": "1", "TG_API_ID": "10", "TG_SESSION": "s"}
        )


def test_factory_builds_outbox_from_env(tmp_path):
    outbox = live_telegram_outbox_from_env(
        {
            "USERIO_LIVE_TELEGRAM": "yes",
            "USERIO_TELEGRAM_API_ID": "10",
            "USERIO_TELEGRAM_API_HASH": "hash",
            "USERIO_TELEGRAM_SESSION": str(tmp_path / "live-session"),
        }
    )
    assert isinstance(outbox, LiveTelegramOutbox)
    outbox.close()


def test_telegram_source_approves_through_live_outbox(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    adapter = FakeAdapter()
    notice = RecordingOutbox()
    with SyncChannelRunner() as runner:
        live = LiveTelegramOutbox(adapter, runner=runner)
        service = UserIOService(store, Generator(), notice, telegram_outbox=live)
        message = InboxMessage("telegram", "m-1", "12345", "hello", 1.0)
        conversation_id, accepted = service.receive(message, route_id="telegram-reply")
        draft = service.propose(conversation_id, message)
        approved = service.approve(draft.id)

    assert accepted is True
    assert approved.status == "approved"
    assert adapter.calls == [(12345, "ok")]
    assert notice.calls == []


def test_telegram_source_falls_back_to_notice_place_without_live_outbox(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    notice = RecordingOutbox()
    service = UserIOService(store, Generator(), notice)
    message = InboxMessage("telegram", "m-1", "12345", "hello", 1.0)

    conversation_id, _ = service.receive(message, route_id="telegram-reply")
    draft = service.propose(conversation_id, message)
    approved = service.approve(draft.id)

    assert approved.status == "approved"
    assert notice.calls == [("noticeplace", "ok")]
