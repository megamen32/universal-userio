"""No-credential contract tests of VK personal Messenger silent-token exchange."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import stat
import time
from urllib.parse import quote, parse_qs

import pytest


SPEC = importlib.util.spec_from_file_location(
    "vk_proof", Path(__file__).with_name("verify_transport.py"),
)
proof = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(proof)


def sample_url(user=46887791, *, token="test-silent-token-long-value", ttl=600):
    payload = {
        "type": "silent_token", "auth": 1,
        "user": {"id": user, "first_name": "VK User"},
        "token": token, "ttl": ttl, "uuid": "",
    }
    return ("https://oauth.vk.ru/blank.html#payload=" +
            quote(json.dumps(payload), safe=""))


def test_parses_real_world_silent_payload_format():
    data = proof.parse_login_result(sample_url())
    assert data == {
        "kind": "silent_token", "token": "test-silent-token-long-value",
        "uuid": "", "vk_id": "46887791", "ttl": "600",
    }


def test_parses_implicit_access_token_only_from_vk_owned_redirect():
    value = proof.parse_login_result(
        "https://oauth.vk.ru/blank.html#access_token=valid-user-token-long&user_id=42"
    )
    assert value["kind"] == "access_token" and value["vk_id"] == "42"
    with pytest.raises(ValueError):
        proof.parse_login_result(
            "https://attacker.example/blank.html#access_token=valid-user-token-long"
        )
    with pytest.raises(ValueError):
        proof.parse_login_result(
            "https://oauth.vk.ru/authorize#access_token=valid-user-token-long"
        )


def test_wrong_or_partial_payloads_rejected():
    with pytest.raises(ValueError):
        proof.parse_login_result(sample_url(ttl=0))
    with pytest.raises(ValueError):
        proof.parse_login_result(sample_url(user=-1))
    with pytest.raises(ValueError):
        proof.parse_login_result("https://oauth.vk.ru/blank.html#payload=NOT_JSON")


def test_silent_exchange_does_not_leak_or_save_user_token():
    calls = []
    def call(method, **kwargs):
        calls.append((method, kwargs))
        if method == "auth.getAnonymToken":
            return {"token": "anonymous-issuer-valid-test"}
        if method == "auth.exchangeSilentAuthToken":
            assert kwargs["access_token"] == "anonymous-issuer-valid-test"
            assert kwargs["token"] == "test-silent-token-long-value"
            assert kwargs["uuid"] == ""
            return {"access_token": "personal-access-token-value", "user_id": 46887791,
                    "expires_in": 3600, "is_partial": False}
        raise AssertionError("Unexpected VK method")
    token, user, ttl = proof.resolve_user_access(proof.parse_login_result(sample_url()), call=call)
    assert (token, user, ttl) == ("personal-access-token-value", "46887791", 3600)
    assert [name for name, _ in calls] == ["auth.getAnonymToken", "auth.exchangeSilentAuthToken"]


def test_reject_mismatching_or_partial_vk_account():
    def callback(method, **kwargs):
        if method == "auth.getAnonymToken":
            return {"token":"anonymous-issuer-valid-test"}
        return {"access_token":"personal-access-token-value", "user_id":2, "expires_in":3600}
    with pytest.raises(RuntimeError, match="another user"):
        proof.resolve_user_access(proof.parse_login_result(sample_url()), call=callback)

    def partial(method, **kwargs):
        if method == "auth.getAnonymToken":
            return {"token":"anonymous-issuer-valid-test"}
        return {"access_token":"personal-access-token-value", "user_id":46887791,
                "is_partial":True}
    with pytest.raises(RuntimeError, match="partial"):
        proof.resolve_user_access(proof.parse_login_result(sample_url()), call=partial)


def test_exact_personal_chat_and_history_proof_readonly():
    methods = []
    def callback(method, **kwargs):
        methods.append(method)
        assert kwargs.get("access_token") == "personal-access-token-value"
        if method == "users.get":
            return [{"id":46887791}]
        if method == "messages.getConversations":
            return {"count":9,"items":[{"conversation":{"peer":{"id":123}}}]}
        if method == "messages.getHistory":
            assert kwargs["peer_id"] == 123
            return {"count":10, "items":[{"id":i} for i in range(5)]}
        raise AssertionError("Unexpected operation")
    d=proof.verify_personal_reads("personal-access-token-value","46887791",call=callback)
    assert d == {"identity_match":True,"confirmed_vk_id":"46887791",
                 "dialog_count":9,"sampled_chats":1,
                 "sampled_history_messages":5,"history_requested":True}
    assert methods == ["users.get","messages.getConversations","messages.getHistory"]
    assert not any("markAsRead" in name or "send" in name for name in methods)


def test_vault_requires_explicit_save_private_dir_and_no_overwrite(tmp_path):
    p=tmp_path/"secret"/"session.json"
    proof.save_temporary_session("personal-access-token-value","46887791",3600,p)
    assert stat.S_IMODE(p.parent.stat().st_mode)==0o700
    assert stat.S_IMODE(p.stat().st_mode)==0o600
    val=json.loads(p.read_text())
    assert val["user_id"]=="46887791" and val["expires_at"]>time.time()
    with pytest.raises(FileExistsError):
        proof.save_temporary_session("another-token-long-enough","46887791",60,p)


def test_vk_api_method_whitelist_rejects_any_send_or_read_change():
    for action in ("messages.send","messages.markAsRead","messages.delete","auth.logout"):
        with pytest.raises(ValueError, match="read-only"):
            proof.vk_call(action)


def test_vk_silent_exchange_preserves_nonempty_uuid_and_reports_stages(monkeypatch):
    from urllib.parse import quote
    payload = {
        "type": "silent_token",
        "user": {"id": 46887791},
        "token": "silently-issued-token-value",
        "uuid": "same-auth-session-uuid",
        "ttl": 600,
    }
    parsed = proof.parse_login_result(
        "https://oauth.vk.ru/blank.html#payload=" +
        quote(json.dumps(payload), safe="")
    )
    monkeypatch.delenv("VK_MESSENGER_CLIENT_SECRET", raising=False)
    stages = []
    calls = []
    def api(method, **kwargs):
        calls.append((method, kwargs))
        if method == "auth.getAnonymToken":
            assert kwargs == {"client_id": "51453752"}
            return {"token": "anonymous-for-client-id"}
        assert method == "auth.exchangeSilentAuthToken"
        assert kwargs == {
            "access_token": "anonymous-for-client-id",
            "token": "silently-issued-token-value",
            "uuid": "same-auth-session-uuid",
        }
        return {"access_token": "vk-personal-token-value", "user_id": 46887791,
                "expires_in": 7200}
    token, user, ttl = proof.resolve_user_access(
        parsed, call=api, progress=stages.append,
    )
    assert token == "vk-personal-token-value"
    assert user == "46887791"
    assert ttl == 7200  # Distinct from original silent_token TTL 600.
    assert stages == ["VK_ANONYMOUS_TOKEN_OK", "VK_PERSONAL_TOKEN_EXCHANGE_OK"]


def test_vk_common_token_is_not_refresh_token_and_never_in_returned_data():
    calls = []
    def api(method, **kwargs):
        calls.append(method)
        assert method == "auth.getExchangeToken"
        return {"users_exchange_tokens": [
            {"user_id": 46887791, "common_token": "opaque-common-token-value"},
            {"user_id": 88888888, "common_token": "different-account-token"},
        ]}
    result = proof.inspect_common_token(
        "vk-personal-token-value", "46887791", call=api,
    )
    assert result == {
        "matching_account_found": True,
        "common_token_present": True,
        "is_refresh_token_verified": False,
    }
    assert "opaque-common-token-value" not in json.dumps(result)
    assert calls == ["auth.getExchangeToken"]


def test_vk_exchange_denial_reports_method_without_sensitive_tokens():
    class HTTPReply:
        def __enter__(self):return self
        def __exit__(self, *args):return False
        def read(self, *_):return json.dumps({
            "error": {"error_code": 100, "error_msg": "bad-token-should-never-echo"}
        }).encode()
    def opener(request, *, timeout):
        return HTTPReply()
    with pytest.raises(proof.VKRejected) as caught:
        proof.vk_call(
            "auth.exchangeSilentAuthToken",
            opener=opener,
            access_token="anonymous-for-client-id",
            token="silently-issued-token-value",
            uuid="same-auth-session-uuid",
        )
    assert caught.value.method == "auth.exchangeSilentAuthToken"
    assert caught.value.code == 100
    assert "silently-issued-token-value" not in str(caught.value)
    assert "bad-token" not in str(caught.value)


def test_no_personal_history_must_not_count_as_connected():
    def api(method, **kwargs):
        if method == "users.get":
            return [{"id": 46887791}]
        if method == "messages.getConversations":
            return {"count": 0, "items": []}
        raise AssertionError(method)
    with pytest.raises(RuntimeError, match="No personal conversation"):
        proof.verify_personal_reads("vk-personal-token-value", "46887791", call=api)


def test_user_selected_dormant_peer_used_for_history():
    seen=[]
    def api(method, **kwargs):
        seen.append(method)
        if method == "users.get":
            return [{"id": 46887791}]
        if method == "messages.getConversations":
            return {"count": 2, "items": [{"conversation": {"peer": {"id": 12}}}]}
        if method == "messages.getHistory":
            assert kwargs["peer_id"] == 321
            return {"count": 1, "items": [{"id": 1, "date": 1500000000}]}
        raise AssertionError(method)
    result=proof.verify_personal_reads(
        "vk-personal-token-value", "46887791", call=api, test_peer_id=321,
    )
    assert result["history_requested"] is True
    assert result["sampled_history_messages"] == 1
    assert seen == ["users.get", "messages.getConversations", "messages.getHistory"]


def test_empty_personal_history_rejected_after_dialog_list():
    def api(method, **kwargs):
        if method == "users.get":
            return [{"id": 46887791}]
        if method == "messages.getConversations":
            return {"count": 1, "items": [{"conversation": {"peer": {"id": 12}}}]}
        if method == "messages.getHistory":
            return {"count": 0, "items": []}
        raise AssertionError(method)
    with pytest.raises(RuntimeError, match="no history"):
        proof.verify_personal_reads("vk-personal-token-value", "46887791", call=api)


def test_secret_env_is_consumed_without_exposing_raw_authorization(monkeypatch, capsys):
    fake = "https://oauth.vk.ru/blank.html#access_token=synthetic-login-value-long&user_id=42"
    monkeypatch.setenv("USERIO_VK_LOGIN_RESULT", fake)
    assert proof.read_fresh_vk_authorization(from_secret_env=True) == fake
    assert "USERIO_VK_LOGIN_RESULT" not in proof.os.environ
    assert fake not in capsys.readouterr().out
    with pytest.raises(RuntimeError, match="No one-time"):
        proof.read_fresh_vk_authorization(from_secret_env=True)


def test_getpass_plaintext_fallback_is_blocked(monkeypatch, capsys):
    import warnings
    def forced_fallback(*_args, **_kwargs):
        warnings.warn("Cannot disable echo", proof.getpass.GetPassWarning)
        raise AssertionError("unsafe plaintext read must not be reached")
    monkeypatch.setattr(proof.getpass, "getpass", forced_fallback)
    with pytest.raises(RuntimeError, match="secure terminal is unavailable"):
        proof.read_fresh_vk_authorization(from_secret_env=False)
    assert not capsys.readouterr().out


def test_proof_persists_confirmed_identity_not_untrusted_or_empty_callback_id(
    tmp_path, monkeypatch, capsys,
):
    from pathlib import Path
    import sys
    session = tmp_path / "private" / "session.json"
    secret = "synthetic-user-bearer-value"
    monkeypatch.setenv("USERIO_VK_LOGIN_RESULT", secret)
    monkeypatch.setattr(sys, "argv", [
        "verify_transport.py", "--from-secret-env", "--save", str(session),
    ])
    original = proof.verify_personal_reads
    def mock_via_actual_identity_query(token, expected, **kwargs):
        assert token == secret
        def vk(method, **_params):
            if method == "users.get":
                return [{"id": 46887791}]
            if method == "messages.getConversations":
                return {"count": 1, "items": [{
                    "conversation": {"peer": {"id": 123}}
                }]}
            if method == "messages.getHistory":
                return {"count": 1, "items": [{"id": 11, "text": "synthetic"}]}
            raise AssertionError("unexpected action")
        return original(token, expected, call=vk, **kwargs)
    monkeypatch.setattr(proof, "verify_personal_reads", mock_via_actual_identity_query)
    assert proof.main() == 0
    stored = json.loads(session.read_text())
    assert stored["user_id"] == "46887791"  # no ID in supplied bearer
    assert stored["access_token"] == secret
    output = capsys.readouterr().out
    assert "SESSION_SAVED_PRIVATELY" in output
    assert secret not in output
    assert "USERIO_VK_LOGIN_RESULT" not in proof.os.environ

    # New process-style resume, without --expected-vk-id: must still verify
    # the saved, server-confirmed identity against users.get.
    monkeypatch.setattr(sys, "argv", ["verify_transport.py", "--resume", str(session)])
    assert proof.main() == 0
    assert secret not in capsys.readouterr().out

    # Revoked/switched account: stored owner remains binding even if user
    # omitted the --expected-vk-id flag on resume.
    def wrong_identity(token, expected, **kwargs):
        assert expected == "46887791"
        raise RuntimeError("VK session belongs to a different user")
    monkeypatch.setattr(proof, "verify_personal_reads", wrong_identity)
    with pytest.raises(RuntimeError, match="different user"):
        proof.main()


def test_resume_rejects_empty_saved_owner_without_network_call(tmp_path, monkeypatch):
    import sys
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    path = private / "session.json"
    path.write_text(json.dumps({
        "access_token": "synthetic-user-bearer-value",
        "user_id": "", "expires_at": time.time() + 1800,
    }))
    path.chmod(0o600)
    monkeypatch.setattr(sys, "argv", ["verify_transport.py", "--resume", str(path)])
    with pytest.raises(RuntimeError, match="no verified VK"):
        proof.main()


def test_expired_proof_deletes_local_file_but_never_calls_vk_logout(
    tmp_path, monkeypatch,
):
    import sys
    private = tmp_path / "private"
    path = private / "session.json"
    proof.save_temporary_session("synthetic-user-bearer-value", "46887791", 60, path)
    data = json.loads(path.read_text())
    data["expires_at"] = time.time() - 1
    path.write_text(json.dumps(data))
    monkeypatch.setattr(sys, "argv", ["verify_transport.py", "--resume", str(path)])
    with pytest.raises(RuntimeError, match="VK access not revoked"):
        proof.main()
    assert not path.exists()
