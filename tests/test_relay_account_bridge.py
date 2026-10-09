"""Fast unit: exact real-origin/HMAC path; expected2s, maximum30s, models0."""
import hashlib
import hmac
import io
import json
import time
from datetime import datetime, timezone

import pytest

from universal_userio.contracts import InboxMessage
from universal_userio.relay_account_bridge import RelayAccountBridge, DOMAIN, canonical_approval
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Response(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *_args): self.close()


@pytest.fixture
def case(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "store.sqlite3")
    store.register_account(account_id="telegram:8810909089", provider="telegram",
        display_name="owned test", can_read=True, can_reply=True, credential_ref="relay:fixture")
    now = time.time()
    guard = tmp_path / "guard.json"
    guard.write_text(json.dumps({"source": "telegram", "account_ref": "telegram:8810909089",
        "peer_id": "8634588930", "issued_at": now-1, "expires_at": now+3600, "purpose": "Pilot9"}))
    token = tmp_path / "token"; token.write_text("fixture-relay-auth")
    sent = []
    health = {"accountId": "8810909089", "inboundExclusions": [{"accountId": "8810909089",
        "peerId": "8634588930", "purpose": "Pilot9", "expiresAt": datetime.fromtimestamp(now+3600, timezone.utc).isoformat().replace("+00:00", "Z")}],
        "userioBridge": {"active": True,"account_id":"telegram:8810909089","peer_id":"8634588930","purpose":"Pilot9",
            "expires_at":datetime.fromtimestamp(now+3600,timezone.utc).isoformat().replace("+00:00","Z")}}
    events = [{"seq": 1,"provider_message_id": "77","sender_id":"8634588930",
        "provider_at":"2026-10-09T20:00:00Z","direction":"incoming","text":"real fixture source"}]
    def opener(request, **_kwargs):
        path = request.full_url.removeprefix("http://relay")
        if path == "/health": result = health
        elif path.startswith("/userio/telegram/events"):
            after=int(path.split("=")[-1]); result={"account_id":"telegram:8810909089","peer_id":"8634588930",
                "cursor":after,"events":[x for x in events if x["seq"]>after],"next_cursor":max([after,*[x['seq'] for x in events]])}
        else:
            assert path == "/userio/telegram/reply"
            sent.append(json.loads(request.data)); result={"providerMessageId":"91","duplicate":False}
        return Response(json.dumps(result).encode())
    class Fallback:
        calls=[]
        def send_reply(self, **kwargs): self.calls.append(kwargs);return "qr:unchanged"
    fallback=Fallback()
    bridge=RelayAccountBridge(fallback=fallback,store=store,base_url="http://relay",token_file=str(token),
        approval_key="fixture-userio-service-key",account_id="telegram:8810909089",peer_id="8634588930",
        guard_file=str(guard),opener=opener)
    service=UserIOService(store,object(),object(),telegram_outbox=bridge)
    yield store,service,bridge,events,sent,health,guard,fallback
    store.close()


def test_real_projection_import_dedup_and_explicit_source_bound_approval(case):
    store,service,bridge,events,sent,*_=case;uid=store.default_user_id
    first=service.sync_relay_account(user_id=uid);cid=first['conversation_ids'][0]
    assert service.sync_relay_account(user_id=uid)['imported']==0
    draft=service.create_manual_draft(cid,body="Свежий ответ",user_id=uid)
    assert not sent and draft.status=='proposed'
    assert store.draft_reply_origin(draft.id,user_id=uid)['provider_message_id']=='77'
    # Later real incoming must not move the already-created reply's origin.
    events.append({**events[0],"seq":2,"provider_message_id":"78","text":"later source"})
    service.sync_relay_account(user_id=uid)
    approved=service.approve(draft.id,user_id=uid,expected_snapshot={
        'expected_text':draft.body,'expected_chat_id':cid,'expected_attachments':[]})
    assert approved.status=='approved' and ':91:' in approved.receipt
    packet=sent[0];a=packet['approval'];assert a['source_message_id']=='77'
    assert a['body_sha256']==hashlib.sha256(draft.body.encode()).hexdigest()
    assert packet['proof']==hmac.new(b'fixture-userio-service-key',DOMAIN.encode()+canonical_approval(a),hashlib.sha256).hexdigest()
    assert service.approve(draft.id,user_id=uid).receipt==approved.receipt and len(sent)==1


def test_wrong_sender_projection_never_imports_or_advances_cursor(case):
    store,service,bridge,events,sent,*_=case;events[0]['sender_id']='42'
    with pytest.raises(ValueError):service.sync_relay_account(user_id=store.default_user_id)
    assert store.conversations()==[] and not sent


def test_other_account_uses_original_qr_outbox(case):
    store,service,bridge,events,sent,health,guard,fallback=case
    cb=bridge.prepare_reply(chat='other',chat_id='42',account_ref='telegram:540308572',body='x',
        draft_id='old',user_id=store.default_user_id,conversation_id='oldconv')
    assert cb()=='qr:unchanged' and not sent


def test_bare_draft_id_and_unclaimed_origin_cannot_send(case):
    store,service,bridge,events,sent,*_=case;uid=store.default_user_id
    cid=service.sync_relay_account(user_id=uid)['conversation_ids'][0]
    draft=service.create_manual_draft(cid,body='x',user_id=uid)
    with pytest.raises(ValueError):bridge.send_reply(account_ref=bridge.account_id,chat_id=bridge.peer_id,body='x',draft_id=draft.id)
    with pytest.raises(ValueError):bridge.prepare_reply(chat='x',chat_id=bridge.peer_id,account_ref=bridge.account_id,
        body='x',draft_id=draft.id,user_id=uid,conversation_id=cid)
    assert not sent


def test_edited_source_invalidates_approval_before_provider(case):
    store,service,bridge,events,sent,*_=case;uid=store.default_user_id
    cid=service.sync_relay_account(user_id=uid)['conversation_ids'][0]
    draft=service.create_manual_draft(cid,body='x',user_id=uid)
    service.receive(InboxMessage(source='telegram',message_id=f'{bridge.peer_id}:77',sender=bridge.peer_id,
        body='edited',received_at=time.time(),peer_id=bridge.peer_id,conversation_kind='direct',edited_at=time.time()),
        route_id='telegram',user_id=uid,account_ref=bridge.account_id)
    with pytest.raises(ValueError):service.approve(draft.id,user_id=uid)
    assert store.draft(draft.id,user_id=uid).status=='proposed' and not sent


@pytest.mark.parametrize('wrong',['identity','expired','other_owner','bridge_expired','bridge_inactive'])
def test_identity_expiry_owner_refuse(case,wrong):
    store,service,bridge,events,sent,health,guard,*_=case;uid=store.default_user_id
    if wrong=='identity':health['accountId']='42'
    elif wrong=='expired':
        rule=json.loads(guard.read_text());rule['expires_at']=time.time()-1;guard.write_text(json.dumps(rule))
    elif wrong=='bridge_expired':health['userioBridge']['expires_at']='2000-01-01T00:00:00Z'
    elif wrong=='bridge_inactive':health['userioBridge']['active']=False
    else:uid='other_owner'
    with pytest.raises((ValueError,PermissionError)):service.sync_relay_account(user_id=uid)
    assert not sent
