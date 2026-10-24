"""messages.prepare must not produce an outbound provider action.

The public compose contract is "Nothing is sent": prepare stores exactly one
durable draft that the caller reviews and then approves with messages.send.
A Telegram draft-approval notice is itself an outbound provider message, so
prepare must not trigger one - neither for the prepared draft nor through the
owner's approval-notification channel.
"""
from universal_userio.contracts import InboxMessage
from universal_userio.draft_notifications import TelegramDraftApprovalNotifier
from universal_userio.mcp_surface import UserIOMcpSurface
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore

TEXT = "Канарейка: prepare не должен ничего отправлять"


class Generator:
    def propose(self, *_args, **_kwargs):
        return ""


class DirectOutbox:
    def send(self, *_args, **_kwargs):
        return "noop"


class FakeTelegramOutbox:
    """The provider transport. Every outbound call is recorded here."""

    def __init__(self):
        self.sent = []

    def send_reply(self, **kwargs):
        self.sent.append(dict(kwargs))
        return f"telegram-qr:account-6:{1000 + len(self.sent)}:{kwargs.get('draft_id')}"


def build(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    owner = store.owner()
    telegram = FakeTelegramOutbox()
    # The production wiring from runtime.py: one Telegram account, and the
    # real approval notifier bound to the owner's Telegram DM.
    notifier = TelegramDraftApprovalNotifier(
        telegram, owner_user_id=owner.user_id, chat="Никита Розанов",
        chat_id="540308572", account_ref="telegram:8810909089",
    )
    service = UserIOService(
        store, Generator(), DirectOutbox(), telegram_outbox=telegram,
        draft_notifier=notifier, draft_notification_delay_seconds=0,
    )
    store.register_account(
        account_id="telegram:8810909089", provider="telegram", display_name="Секретарь",
        enabled=True, can_read=True, can_reply=True,
        credential_ref="telegram-qr:account-6", user_id=owner.user_id,
    )
    return store, service, telegram, owner


def telegram_conversation(service, owner):
    message = InboxMessage(
        "telegram", "telegram:8810909089|540308572:1", "Никита Розанов", "привет",
        10.0, peer_id="540308572",
    )
    conversation_id, accepted = service.receive(
        message, route_id="telegram", user_id=owner.user_id,
        account_ref="telegram:8810909089",
    )
    assert accepted is True
    return conversation_id


def test_messages_prepare_sends_nothing_to_the_provider(tmp_path):
    store, service, telegram, owner = build(tmp_path)
    conversation_id = telegram_conversation(service, owner)
    surface = UserIOMcpSurface(store, service, owner)

    result = surface.dispatch("userio.messages.prepare", {
        "conversation_id": conversation_id,
        "text": TEXT,
        "request_id": "prepare-no-send-0001",
    })

    assert result["ok"] is True
    assert result["status"] == "proposed"
    assert telegram.sent == [], "prepare must not produce an outbound provider message"
    draft = store.draft(result["draft_id"], user_id=owner.user_id)
    assert draft.body == TEXT
    assert draft.status == "proposed"


def test_messages_send_still_delivers_the_prepared_body_exactly_once(tmp_path):
    store, service, telegram, owner = build(tmp_path)
    conversation_id = telegram_conversation(service, owner)
    surface = UserIOMcpSurface(store, service, owner)

    prepared = surface.dispatch("userio.messages.prepare", {
        "conversation_id": conversation_id,
        "text": TEXT,
        "request_id": "prepare-no-send-0002",
    })
    assert telegram.sent == []

    snapshot = {
        "draft_id": prepared["draft_id"],
        "confirm": True,
        "expected_text": prepared["body"],
        "expected_chat_id": prepared["conversation_id"],
        "expected_attachments": [],
    }
    sent = surface.dispatch("userio.messages.send", snapshot)

    assert sent["ok"] is True
    assert sent["sent"] is True
    assert len(telegram.sent) == 1
    assert telegram.sent[0]["body"] == TEXT

    replay = surface.dispatch("userio.messages.send", snapshot)

    assert replay["ok"] is True
    assert len(telegram.sent) == 1, "a repeated send must replay the stored receipt"
