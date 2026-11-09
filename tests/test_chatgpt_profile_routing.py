import hashlib
import hmac
import json

import pytest

from universal_userio import chatgpt_sessions


INSTANCE_A = "browser-11111111-1111-4111-8111-111111111111"
INSTANCE_B = "browser-22222222-2222-4222-8222-222222222222"


def proof(nonce, cookies):
    chunks = sorted([[x["name"], x["value"]] for x in cookies])
    key = json.dumps(chunks, ensure_ascii=False, separators=(",", ":")).encode()
    return hmac.new(key, ("userio-profile-match-v1:"+nonce).encode(), hashlib.sha256).hexdigest()


def test_matching_existing_chatgpt_profile_is_unambiguous_and_user_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(chatgpt_sessions, "SESSION_DIR", tmp_path)
    cookies = [{"name":"__Secure-next-auth.session-token.0","value":"fixture-secret"}]
    chatgpt_sessions.save_session({"account_ref":"same","cookies":cookies,"agent_id":"legacy"}, user="alice")
    chatgpt_sessions.save_session({"account_ref":"same","cookies":[{"name":"__Secure-next-auth.session-token.0","value":"different"}]}, user="bob")
    nonce = "challenge-123"
    lookup = lambda instance, challenge: {"ok":True,"proof":proof(challenge, cookies if instance == INSTANCE_A else [{"name":"__Secure-next-auth.session-token.0","value":"other"}])}
    assert chatgpt_sessions.resolve_agent_instance("same", "legacy", [INSTANCE_A,INSTANCE_B], user="alice", challenge=nonce, probe=lookup) == INSTANCE_A
    assert chatgpt_sessions.resolve_agent_instance("same", "legacy", [INSTANCE_A,INSTANCE_B], user="bob", challenge=nonce, probe=lookup) is None
    assert chatgpt_sessions.load_session("same",user="alice")["agent_id"] == "legacy"


def test_ambiguous_and_unverified_profiles_are_not_guessed(tmp_path, monkeypatch):
    monkeypatch.setattr(chatgpt_sessions, "SESSION_DIR", tmp_path)
    cookies = [{"name":"__Secure-next-auth.session-token","value":"fixture-secret"}]
    chatgpt_sessions.save_session({"account_ref":"same","cookies":cookies,"agent_id":"legacy"}, user="alice")
    nonce = "challenge-123"
    same = lambda instance, challenge: {"ok":True,"proof":proof(challenge,cookies)}
    assert chatgpt_sessions.resolve_agent_instance("same", "legacy", [INSTANCE_A,INSTANCE_B], user="alice", challenge=nonce, probe=same) is None
    unavailable = lambda instance, challenge: same(instance,challenge) if instance == INSTANCE_A else {"ok":False}
    assert chatgpt_sessions.resolve_agent_instance("same", "legacy", [INSTANCE_A,INSTANCE_B], user="alice", challenge=nonce, probe=unavailable) is None
    no_session = lambda instance, challenge: same(instance,challenge) if instance == INSTANCE_A else {"ok":True,"present":False}
    assert chatgpt_sessions.resolve_agent_instance("same", "legacy", [INSTANCE_A,INSTANCE_B], user="alice", challenge=nonce, probe=no_session) == INSTANCE_A
    with pytest.raises(ValueError):
        chatgpt_sessions.resolve_agent_instance("same", "legacy", [INSTANCE_A], user="alice", challenge="invalid nonce", probe=same)


def test_chatgpt_fallback_routes_to_proven_profile_instead_of_legacy_alias(tmp_path, monkeypatch):
    from universal_userio import adapters, agent_channel
    monkeypatch.setattr(chatgpt_sessions, "SESSION_DIR", tmp_path)
    cookies = [{"name":"__Secure-next-auth.session-token","value":"fixture-secret"}]
    chatgpt_sessions.save_session({"account_ref":"same","cookies":cookies,"agent_id":"legacy"}, user="alice")
    record=chatgpt_sessions.load_session("same",user="alice")
    monkeypatch.setattr(adapters,"_chatgpt_accounts",lambda user:{"same":cookies})
    monkeypatch.setitem(adapters._chatgpt_records,("alice","same"),record)
    monkeypatch.setattr(agent_channel,"status",lambda *,user:{"agents":[{"agent_id":"legacy","identity_conflict":True,"instances":[INSTANCE_A,INSTANCE_B]}]})
    def call(instance, action, args, *, user, timeout_sec):
        assert action == "gpt_session_proof" and user == "alice"
        return {"ok":True,"proof":proof(args["nonce"],cookies if instance==INSTANCE_A else [{"name":"__Secure-next-auth.session-token","value":"other"}])}
    monkeypatch.setattr(agent_channel,"call",call)
    outbox=adapters.ChatGPTWebOutbox()
    monkeypatch.setattr(outbox,"_send_web",lambda **kwargs: (_ for _ in ()).throw(RuntimeError("403")))
    sent=[]
    receipt=outbox.send_reply(chat_ref="chat",draft_id="draft",body="hello",account_ref="same",user="alice",agent_fallback=lambda **kwargs:sent.append(kwargs) or {"assistant_id":"result"})
    assert receipt == "result"
    assert sent[0]["agent_id"] == INSTANCE_A
    monkeypatch.setattr(agent_channel,"call",lambda instance, action, args, *, user, timeout_sec:{"ok":True,"proof":proof(args["nonce"],cookies)})
    sent.clear()
    with pytest.raises(RuntimeError,match="identity conflict"):
        outbox.send_reply(chat_ref="chat",draft_id="draft2",body="hello",account_ref="same",user="alice",agent_fallback=lambda **kwargs:sent.append(kwargs))
    assert sent == []
