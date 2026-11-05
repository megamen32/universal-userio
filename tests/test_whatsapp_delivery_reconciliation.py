"""Read-only original WhatsApp QR receipts; never replay uncertain sends."""
import io
import json
import urllib.error
import urllib.request
from types import SimpleNamespace
import pytest

from test_telegram_delivery_reconciliation import _uncertain_draft, _make_due
from universal_userio.channels.whatsapp import WhatsAppBridgeClient
from universal_userio.delivery_reconciliation import TelegramDeliveryReconciliationWorker
from universal_userio.service import UserIOService
from universal_userio.provider_readback import original_provider_receipt


REF='whatsapp:100:account-1'
PEER='123@s.whatsapp.net'
ID='3EB0WAORIGINAL12345678'


class Sender:
    def __init__(self, *, status=None):
        self.status = status
        self.calls=[]
    def send(self, **kwargs):
        self.calls.append(('send', kwargs))
        return ID
    def original_send_status(self, **kwargs):
        self.calls.append(('read', kwargs))
        return self.status


def fixture(tmp_path, status=None):
    service, draft_id = _uncertain_draft(tmp_path, {'state':'pending'})
    service._store.register_account(account_id=REF, provider='whatsapp',
        display_name='personal', can_read=True,can_reply=True, credential_ref='whatsapp-qr:account-1')
    with service._store._connection:
        service._store._connection.execute(
            "UPDATE conversations SET source='whatsapp',account_ref=?,peer_id=?"
            " WHERE id=(SELECT conversation_id FROM drafts WHERE id=?)", (REF,PEER,draft_id))
    service.whatsapp_outbox=Sender(status=status)
    return service,draft_id


def conversation(service,draft_id):
    draft=service._store.draft(draft_id)
    return service._store.conversation(draft.conversation_id)


def test_approved_personal_send_pins_exact_draft_id_to_bridge_before_network(tmp_path):
    service,draft_id=fixture(tmp_path)
    draft=service._store.draft(draft_id)
    send=service._prepare_claimed_draft(draft,user_id=service._store.default_user_id)
    assert service.whatsapp_outbox.calls==[]
    assert send()==ID
    kind,payload=service.whatsapp_outbox.calls[0]
    assert kind=='send'
    assert payload['attempt_id']==draft_id
    assert payload['user_id']==service._store.default_user_id
    assert payload['account_ref']==REF
    assert payload['chat_id']==PEER
    assert payload['text']==draft.body


def test_exact_scoped_provider_receipt_confirms_original_without_send(tmp_path):
    service,draft_id=fixture(tmp_path,status={'attempt_id':'tmp','account_ref':REF,
                                             'chat_id':PEER,'state':'sent','message_id':ID})
    service.whatsapp_outbox.status['attempt_id']=draft_id
    result=service.reconcile_draft_delivery(draft_id)
    assert result['resolution']=='delivered'
    assert result['receipt']==ID
    assert service._store.draft(draft_id).status=='approved'
    assert [x for x in service.whatsapp_outbox.calls if x[0]=='send']==[]


@pytest.mark.parametrize('state',['unknown','pending','not_submitted'])
def test_no_positive_receipt_keeps_original_send_claim_unknown(tmp_path,state):
    service,draft_id=fixture(tmp_path, status={'attempt_id':'tmp','account_ref':REF,
          'chat_id':PEER,'state':state,'message_id':''})
    service.whatsapp_outbox.status['attempt_id']=draft_id
    result=service.reconcile_draft_delivery(draft_id)
    assert result['resolution']=='unknown'
    assert result['retry_allowed'] is False
    assert service._store.draft(draft_id).status in ('sending','delivery_unknown')
    assert [x for x in service.whatsapp_outbox.calls if x[0]=='send']==[]


def test_source_binding_refuses_foreign_account_status(tmp_path):
    service,draft_id=fixture(tmp_path,status={'attempt_id':'temp',
        'state':'sent','account_ref':'whatsapp:foreign:account-1','chat_id':PEER,'message_id':ID})
    service.whatsapp_outbox.status['attempt_id']=draft_id
    with pytest.raises(RuntimeError,match='identity conflict'):
        service.reconcile_draft_delivery(draft_id)
    assert service._store.draft(draft_id).status=='sending'


def test_background_worker_claims_one_original_draft(tmp_path):
    service,draft_id=fixture(tmp_path,status={'attempt_id':'tmp','account_ref':REF,
        'chat_id':PEER,'state':'sent','message_id':ID})
    service.whatsapp_outbox.status['attempt_id']=draft_id
    _make_due(service,draft_id)
    worker=TelegramDeliveryReconciliationWorker(service)
    assert worker.run_once()==1
    assert service._store.draft(draft_id).status=='approved'
    assert worker.run_once()==0
    assert not any(k=='send' for k,_ in service.whatsapp_outbox.calls)


class FakeResponse(io.BytesIO):
    def __enter__(self):return self
    def __exit__(self,*args):self.close()


class FakeOpener:
    def __init__(self, responses):
        self.responses=list(responses);self.requests=[]
    def __call__(self, request,timeout=None):
        self.requests.append(request)
        item=self.responses.pop(0)
        if isinstance(item,Exception):raise item
        return FakeResponse(json.dumps(item).encode())


def test_bridge_read_is_get_and_exact_account_bound():
    x=FakeOpener([{'attempt_id':'draft','account_ref':REF,'chat_id':PEER,
                   'state':'sent','message_id':ID}])
    client=WhatsAppBridgeClient('http://127.0.0.1:18096',runner=x,token='test-token')
    result=client.original_send_status(chat_id=PEER,user_id='owner',
                 account_ref=REF,attempt_id='draft')
    assert result['message_id']==ID
    assert len(x.requests)==1
    assert x.requests[0].get_method()=='GET'
    assert '/send-attempt?' in x.requests[0].full_url


def test_bridge_send_with_original_id_passes_same_durable_attempt():
    x=FakeOpener([{'messageId':ID}])
    client=WhatsAppBridgeClient('http://127.0.0.1:18096',runner=x,token='test-token')
    assert client.send(chat_id=PEER,text='text',user_id='owner',
        account_ref=REF,attempt_id='draft')==ID
    payload=json.loads(x.requests[0].data)
    assert payload['attempt_id']=='draft'
    assert payload['account_ref']==REF


def test_unpinned_legacy_whatsapp_is_not_silently_assigned_a_new_id(tmp_path):
    service,draft_id=fixture(tmp_path)
    conv=conversation(service,draft_id)
    conv['account_ref']='legacy-whatsapp'
    draft=service._store.draft(draft_id)
    assert original_provider_receipt(service,draft,conv,
        user_id=service._store.default_user_id) is None
    assert service.whatsapp_outbox.calls==[]
