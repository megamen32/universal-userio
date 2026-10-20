"""Personal Telegram write route: a pre-submission refusal must not wedge a draft.

The 16.10.2026 incident: `userio_draft_approve_send` for conversation
conv_c7b6300e97fa96bb0e4d789e answered HTTP 404 from the telegram-qr connector
(`account telegram:540308572 is not connected`) and left the draft in
`sending` forever, because the refusal was reported as an uncertain error.
"""

from __future__ import annotations

import io
import json
import urllib.error

from connector_probe import reachable_probe
from universal_userio.adapters import TelegramConnectorRefused, TelegramQrHttpOutbox
from universal_userio.contracts import InboxMessage
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def suggest(self, *, conversation_id: str, latest_message: InboxMessage) -> str:
        return "ответ"


class RecordingOutbox:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def send_reply(self, *, route_id, conversation_id, draft_id, body) -> str:
        self.calls.append({"draft_id": draft_id, "body": body})
        return "notice-receipt"


def _http_error(status: int, body: dict):
    def runner(request, **_kwargs):
        raise urllib.error.HTTPError(request.full_url, status, "refused", {},
                                     io.BytesIO(json.dumps(body).encode()))
    return TelegramQrHttpOutbox("http://127.0.0.1:18095", "token", runner=runner, probe=reachable_probe)


def _service(tmp_path, outbox) -> tuple[UserIOService, str]:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), RecordingOutbox(), telegram_outbox=outbox)
    message = InboxMessage("telegram", "telegram:540308572|8810909089:7", "Менеджер",
                           "встреча закончилась", 1.0, peer_id="8810909089")
    conversation_id, _ = service.receive(message, route_id="telegram", account_ref="telegram:540308572")
    draft = service.propose(conversation_id, message)
    return service, draft.id


def test_offline_personal_account_releases_the_draft_for_an_exact_retry(tmp_path):
    service, draft_id = _service(tmp_path, _http_error(404, {
        "error": "account telegram:540308572 is not connected",
        "submitted": False, "code": "account_not_connected"}))
    try:
        service.approve(draft_id)
    except TelegramConnectorRefused as error:
        assert "не отправлено" in str(error)
    else:
        raise AssertionError("the connector refusal must reach the caller")
    # The claim is released, so the same exact draft can be approved again.
    assert service._store.draft(draft_id).status == "proposed"


def test_uncertain_provider_error_keeps_the_claim(tmp_path):
    service, draft_id = _service(tmp_path, _http_error(502, {
        "error": "send failed", "submitted": "unknown", "code": "send_outcome_unknown"}))
    try:
        service.approve(draft_id)
    except RuntimeError:
        pass
    else:
        raise AssertionError("an uncertain outcome must not be reported as a refusal")
    assert service._store.draft(draft_id).status == "sending"
    # A later approval of the same uncertain draft is refused, never replayed.
    try:
        service.approve(draft_id)
    except Exception as error:
        assert "неизвестен" in str(error)
        # A consumer must be able to branch on the stable reason.
        assert getattr(error, "code", "") == "delivery_outcome_unknown"
    else:
        raise AssertionError("an uncertain draft must not be sent twice")


def test_relogin_refusal_names_the_owner_action(tmp_path):
    service, draft_id = _service(tmp_path, _http_error(404, {
        "error": "account telegram:540308572 is not connected",
        "submitted": False, "code": "account_relogin_required"}))
    try:
        service.approve(draft_id)
    except TelegramConnectorRefused as error:
        assert error.connector_code == "account_relogin_required"
        assert "Переподключите" in str(error)
    else:
        raise AssertionError("a revoked session must not be reported as sent")
    assert service._store.draft(draft_id).status == "proposed"