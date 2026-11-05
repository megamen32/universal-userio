"""MAX original native CID reconciliation tests. No real send/provider IO."""
import hashlib
from types import SimpleNamespace
import pytest

from universal_userio.provider_readback import max_client_id_readback,original_provider_receipt
from test_telegram_delivery_reconciliation import _uncertain_draft, _make_due
from universal_userio.delivery_reconciliation import TelegramDeliveryReconciliationWorker
from universal_userio.service import UserIOService
from universal_userio.max_web import MaxWeb
import universal_userio.provider_readback as readback


class FakeMax:
    def __init__(self, *, rows=None, detail=None,own='77'):
        self.rows=rows or []
        self.detail=detail or {}
        self.profile={'profile':{'contact':{'id':own}}}
        self.expected_user_id=own
        self.reads=[]
        self.sends=[]
    def history(self, chat_id, *, backward, before=None):
        self.reads.append(('history',str(chat_id),backward,before))
        return list(self.rows)
    def get_message(self, *, chat_id, message_id):
        self.reads.append(('get_message',str(chat_id),str(message_id)))
        return self.detail.get(str(message_id),{})
    def send(self, *, chat_id, text, cid=None):
        self.sends.append((str(chat_id),text,cid))
        return {'message_id':'9001'}


def sample(cid=123, **changes):
    row={'id':9001,'time':1700000000000,'sender':77,'type':'USER','text':'answer',
         'cid':cid,'attaches':[]}
    return {**row,**changes}


def test_max_history_candidates_require_exact_get_message_cid_and_owner():
    client=FakeMax(rows=[sample(cid=None)],detail={'9001':sample()})
    assert max_client_id_readback(client,peer_id='42',own_id='77',client_id=123,body='answer')=='9001'
    assert client.reads==[('history','42',100,None),('get_message','42','9001')]


@pytest.mark.parametrize('change',[
    {'sender':999},{'cid':45},{'text':'other'},{'attaches':[{'_type':'FILE'}]},
    {'link':{'type':'REPLY','messageId':5}},{'updateTime':1700000000001},
    {'id':9910},{'type':'SYSTEM'}
])
def test_max_provider_readback_rejects_conflicts(change):
    client=FakeMax(rows=[sample()],detail={'9001':sample(**change)})
    assert max_client_id_readback(client,peer_id='42',own_id='77',client_id=123,body='answer') is None


def test_max_two_exact_matches_are_ambiguous():
    client=FakeMax(rows=[sample(),sample(id=9002)],
        detail={'9001':sample(),'9002':sample(id=9002)})
    assert max_client_id_readback(client,peer_id='42',own_id='77',client_id=123,body='answer') is None


def setup(tmp_path):
    svc,draft_id=_uncertain_draft(tmp_path,{'state':'pending'})
    with svc._store._connection:
        svc._store._connection.execute(
         "UPDATE conversations SET source='max',account_ref='max',peer_id='42' WHERE id=(SELECT conversation_id FROM drafts WHERE id=?)",
         (draft_id,))
    return svc,draft_id


def pin(service,draft_id):
    cid=int(hashlib.sha256(draft_id.encode()).hexdigest()[:13],16)
    service._store.pin_native_delivery_request(draft_id,user_id=service._store.default_user_id,
        source='max',account_ref='max',peer_id='42',native_id=str(cid))
    return cid


def test_max_origin_is_durably_pinned_before_provider_io(tmp_path,monkeypatch):
    service,draft_id=setup(tmp_path)
    client=FakeMax()
    monkeypatch.setattr(UserIOService,'require_max_reply_account',lambda *a,**k:None)
    monkeypatch.setattr(UserIOService,'max_client_for',lambda *a,**k:client)
    monkeypatch.setattr(MaxWeb,'for_draft',lambda *a,**k:None)
    monkeypatch.setattr(service._store,'route_allowed',lambda **kw: True)
    original=service._store.draft(draft_id)
    send=service._prepare_claimed_draft(original,user_id=service._store.default_user_id)
    evidence=service._store.draft_delivery_state(draft_id)['evidence']['native_claim']
    assert evidence['source']=='max'
    assert evidence['peer_id']=='42'
    assert evidence['native_id']==str(int(hashlib.sha256(draft_id.encode()).hexdigest()[:13],16))
    assert client.sends==[]
    assert send()=='9001'
    assert client.sends==[('42',original.body,int(evidence['native_id']))]


def test_old_unknown_without_persisted_nonce_is_never_inferred(tmp_path,monkeypatch):
    service,draft_id=setup(tmp_path)
    client=FakeMax(rows=[sample()],detail={'9001':sample()})
    monkeypatch.setattr(UserIOService,'max_client_for',lambda *a,**k:client)
    assert original_provider_receipt(service,service._store.draft(draft_id),
        service._store.conversation(service._store.draft(draft_id).conversation_id),
        user_id=service._store.default_user_id) is None
    assert client.reads==[]


def test_existing_native_claim_survives_every_unknown_reconciliation(tmp_path):
    service,draft_id=setup(tmp_path)
    cid=pin(service,draft_id)
    state=service._store.draft_delivery_state(draft_id)
    service._store.record_draft_delivery_reconciliation(draft_id,
        user_id=service._store.default_user_id,
        result={'resolution':'unknown','reason':'provider_history_no_match'},
        evidence={'channel':'max'},
        expected_generation=state['generation'])
    evidence=service._store.draft_delivery_state(draft_id)['evidence']
    assert evidence['native_claim']['native_id']==str(cid)
    assert evidence['channel']=='max'


def test_pinned_native_cid_recovery_closes_original_claim_without_second_send(tmp_path,monkeypatch):
    service,draft_id=setup(tmp_path)
    cid=pin(service,draft_id)
    client=FakeMax(rows=[sample(cid=None,text=service._store.draft(draft_id).body)],
        detail={'9001':sample(cid=cid,text=service._store.draft(draft_id).body)})
    monkeypatch.setattr(UserIOService,'max_client_for',lambda *a,**k:client)
    draft=service._store.draft(draft_id)
    conv=service._store.conversation(draft.conversation_id)
    receipt=original_provider_receipt(service,draft,conv,
        user_id=service._store.default_user_id)
    assert receipt=='9001'
    before=len(service.telegram_outbox.sent)
    result=service.reconcile_draft_delivery(draft_id)
    assert result['resolution']=='delivered'
    assert service._store.draft(draft_id).status=='approved'
    assert len(service.telegram_outbox.sent)==before


def test_pinned_native_cid_recovery_background_worker_uses_same_attempt(tmp_path,monkeypatch):
    service,draft_id=setup(tmp_path)
    cid=pin(service,draft_id)
    draft=service._store.draft(draft_id)
    client=FakeMax(rows=[sample(cid=None,text=draft.body)],
       detail={'9001':sample(cid=cid,text=draft.body)})
    monkeypatch.setattr(UserIOService,'max_client_for',lambda *a,**k:client)
    _make_due(service,draft_id)
    assert TelegramDeliveryReconciliationWorker(service).run_once()==1
    assert service._store.draft(draft_id).status=='approved'
    assert TelegramDeliveryReconciliationWorker(service).run_once()==0
