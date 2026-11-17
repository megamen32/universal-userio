import base64
import hashlib

from universal_userio.store import SQLiteUserIOStore

AUDIENCE = "https://userio.dmo.airlockrun.ru"
VERIFIER = "a" * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")


def test_web_bridge_ticket_one_use_with_pkce(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite")
    uid = store.owner().user_id
    ticket = store.issue_web_session_ticket(
        user_id=uid, audience=AUDIENCE, code_challenge=CHALLENGE
    )
    assert store.redeem_web_session_ticket(
        ticket=ticket, code_verifier="b" * 64, audience=AUDIENCE
    ) is None
    assert store.redeem_web_session_ticket(
        ticket=ticket, code_verifier=VERIFIER, audience="https://other.invalid"
    ) is None
    assert store.redeem_web_session_ticket(
        ticket=ticket, code_verifier=VERIFIER, audience=AUDIENCE
    ) == uid
    assert store.redeem_web_session_ticket(
        ticket=ticket, code_verifier=VERIFIER, audience=AUDIENCE
    ) is None


def test_distinct_accounts_never_share_browser_tickets(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "userio-2.sqlite")
    first = store.register_user("bridge-a", "long-pass-first")
    second = store.register_user("bridge-b", "long-pass-second")
    a = store.issue_web_session_ticket(user_id=first.user_id, audience=AUDIENCE, code_challenge=CHALLENGE)
    b = store.issue_web_session_ticket(user_id=second.user_id, audience=AUDIENCE, code_challenge=CHALLENGE)
    assert a != b
    assert store.redeem_web_session_ticket(ticket=a,code_verifier=VERIFIER,audience=AUDIENCE) == first.user_id
    assert store.redeem_web_session_ticket(ticket=b,code_verifier=VERIFIER,audience=AUDIENCE) == second.user_id
    assert store.redeem_web_session_ticket(ticket=a,code_verifier=VERIFIER,audience=AUDIENCE) is None
