import os
import stat
from pathlib import Path

import pytest

from universal_userio import chatgpt_sessions, vault


def test_chatgpt_email_users_are_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(chatgpt_sessions, "SESSION_DIR", tmp_path)
    for user, token in [("airlock@example.com", "first"), ("other+test@example.com", "second")]:
        chatgpt_sessions.save_session({"account_ref": "same", "session_token": token}, user=user)
        assert len(chatgpt_sessions.list_sessions(user=user)) == 1
        assert chatgpt_sessions.load_session("same", user=user)["cookie_chunks"][0]["value"] == token
    chatgpt_sessions.delete_session("same", user="airlock@example.com")
    assert chatgpt_sessions.list_sessions(user="airlock@example.com") == []
    assert len(chatgpt_sessions.list_sessions(user="other+test@example.com")) == 1


@pytest.mark.parametrize("user", ["..", ".", "../other", "a/b", "a\\b", "bad\x00name", "alice\n"])
def test_chatgpt_rejects_unsafe_usernames(tmp_path, monkeypatch, user):
    monkeypatch.setattr(chatgpt_sessions, "SESSION_DIR", tmp_path)
    with pytest.raises(ValueError):
        chatgpt_sessions.list_sessions(user=user)


def test_chatgpt_sessions_are_user_scoped_and_private(tmp_path, monkeypatch):
    monkeypatch.setattr(chatgpt_sessions, "SESSION_DIR", tmp_path / "chatgpt")
    payload = {"email": "same@example.com", "session_token": "secret-a"}
    a = chatgpt_sessions.save_session(payload, user="alice")
    payload2 = {"email": "same@example.com", "session_token": "secret-b"}
    b = chatgpt_sessions.save_session(payload2, user="bob")
    assert a["slug"] == b["slug"] == "same-example.com"
    assert chatgpt_sessions.load_session(a["slug"], user="alice")["cookie_chunks"][0]["value"] == "secret-a"
    assert chatgpt_sessions.load_session(b["slug"], user="bob")["cookie_chunks"][0]["value"] == "secret-b"
    assert len(chatgpt_sessions.list_sessions(user="alice")) == 1
    assert len(chatgpt_sessions.list_sessions(user="bob")) == 1
    apath = tmp_path / "chatgpt" / "alice" / "same-example.com.json"
    assert stat.S_IMODE(apath.stat().st_mode) == 0o600
    assert stat.S_IMODE(apath.parent.stat().st_mode) == 0o700


def test_vault_is_user_scoped_and_private(tmp_path, monkeypatch):
    monkeypatch.setattr(vault, "VAULT_DIR", tmp_path / "vault")
    vault.save_session("browser", {"blob": {"secret": "alice"}}, user="alice")
    vault.save_session("browser", {"blob": {"secret": "bob"}}, user="bob")
    assert vault.load_session("browser", user="alice")["blob"]["secret"] == "alice"
    assert vault.load_session("browser", user="bob")["blob"]["secret"] == "bob"
    assert [s["name"] for s in vault.list_sessions(user="alice")] == ["browser"]
    path = tmp_path / "vault" / "alice" / "browser.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    vault.delete_session("browser", user="alice")
    with pytest.raises(KeyError):
        vault.load_session("browser", user="alice")
    assert vault.load_session("browser", user="bob")["blob"]["secret"] == "bob"
