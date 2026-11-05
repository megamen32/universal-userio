"""Read-only ChatGPT CDP original idempotency receipts, no browser or outgoing send."""
import pytest
import universal_userio.adapters as adapters
from test_telegram_delivery_reconciliation import _uncertain_draft, _make_due
from universal_userio.delivery_reconciliation import TelegramDeliveryReconciliationWorker
from universal_userio.provider_readback import original_provider_receipt

CHAT='cdpchat:v1:chat:original'
MESSAGE='cdpchat:v1:message:accepted123'

class FakeCdp:
    def __init__(self,result=None):self.result=result;self.calls=[]
    def call(self,name,args):
        self.calls.append((name,args))
        if callable(self.result):return self.result(name,args)
        return self.result

def prepare(tmp_path,monkeypatch,status='sent',evidence='page_observed'):
    service,draft_id=_uncertain_draft(tmp_path,{'state':'pending'})
    with service._store._connection:
        service._store._connection.execute(
            "UPDATE conversations SET source='chatgpt', account_ref='', peer_id=?,sender=? "
            "WHERE id=(SELECT conversation_id FROM drafts WHERE id=?)",
            (CHAT,CHAT,draft_id))
    receipt={'state':status,'chatRef':CHAT,'idempotencyKey':draft_id,
             'recipientDeliveryConfirmed':False,'evidence':evidence,'messageRef':MESSAGE}
    sidecar=FakeCdp(receipt)
    monkeypatch.setattr(adapters,'_configured_chatgpt_cdp_client',lambda:sidecar)
    monkeypatch.setenv('USERIO_CHATGPT_CDP_MCP_COMMAND','synthetic-test-only')
    return service,draft_id,sidecar

def test_original_cdp_receipt_closes_draft_without_second_send(tmp_path,monkeypatch):
    service,draft_id,sidecar=prepare(tmp_path,monkeypatch)
    before=len(service.telegram_outbox.sent)
    result=service.reconcile_draft_delivery(draft_id)
    assert result['resolution']=='delivered'
    assert result['receipt']==MESSAGE
    assert result['reason']=='cdp_page_observed'
    assert result['delivery_confirmed'] is False
    assert service._store.draft(draft_id).status=='approved'
    assert len(service.telegram_outbox.sent)==before
    assert sidecar.calls==[('get_send_status',{'chatRef':CHAT,
        'idempotencyKey':draft_id,'text':service._store.draft(draft_id).body})]

@pytest.mark.parametrize('bad', [
    {'state':'unknown'},{'state':'not_found'},{'state':'unavailable'},
    {'evidence':None},{'recipientDeliveryConfirmed':True},
    {'messageRef':'not-a-cdp-original'},{'idempotencyKey':'foreign-key'},
    {'chatRef':'foreign-chat'}
])
def test_unconfirmed_or_foreign_cdp_outcome_keeps_unknown(tmp_path,monkeypatch,bad):
    svc,draft_id,sidecar=prepare(tmp_path,monkeypatch)
    sidecar.result.update(bad)
    before=len(svc.telegram_outbox.sent)
    result=svc.reconcile_draft_delivery(draft_id)
    assert result['resolution']=='unknown'
    assert result['retry_allowed'] is False
    assert len(svc.telegram_outbox.sent)==before
    assert svc._store.draft(draft_id).status!='approved'

def test_without_configured_cdp_sidecar_does_not_start_mcp_or_send(tmp_path,monkeypatch):
    svc,draft_id,sidecar=prepare(tmp_path,monkeypatch)
    monkeypatch.delenv('USERIO_CHATGPT_CDP_MCP_COMMAND')
    result=svc.reconcile_draft_delivery(draft_id)
    assert result['resolution']=='unknown'
    assert sidecar.calls==[]

def test_other_chatgpt_account_does_not_use_unscoped_sidecar(tmp_path,monkeypatch):
    svc,draft_id,sidecar=prepare(tmp_path,monkeypatch)
    with svc._store._connection:
        svc._store._connection.execute(
            "UPDATE conversations SET account_ref='foreign-web-account' "
            "WHERE id=(SELECT conversation_id FROM drafts WHERE id=?)",
            (draft_id,))
    draft=svc._store.draft(draft_id)
    conversation=svc._store.conversation(draft.conversation_id)
    assert original_provider_receipt(svc,draft,conversation,
        user_id=svc._store.default_user_id) is None
    assert sidecar.calls==[]

def test_exact_original_pending_chatgpt_is_reconciled_by_existing_worker(tmp_path,monkeypatch):
    svc,draft_id,sidecar=prepare(tmp_path,monkeypatch)
    _make_due(svc,draft_id)
    worker=TelegramDeliveryReconciliationWorker(svc)
    assert worker.run_once()==1
    assert svc._store.draft(draft_id).status=='approved'
    assert worker.run_once()==0
    assert len(sidecar.calls)==1
