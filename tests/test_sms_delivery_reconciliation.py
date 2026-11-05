"""Original SMS key recovery, no test phone or outgoing provider messages."""
import io,json,hashlib,urllib.error
import pytest

from test_telegram_delivery_reconciliation import _uncertain_draft,_make_due
from universal_userio.channels.sms_gateway import AndroidSmsGatewayClient
from universal_userio.delivery_reconciliation import TelegramDeliveryReconciliationWorker
from universal_userio.service import UserIOService
from universal_userio.provider_readback import original_provider_receipt


NATIVE_ID='12345678-1234-1234-1234-123456789abc'

class FakeGateway:
    def __init__(self, status='sent', evidence='android_sms_sent_callback'):
        self.status=status;self.evidence=evidence;self.calls=[]
    def original_send_status(self, **kw):
        self.calls.append(('read',kw))
        return {'id':NATIVE_ID,'status':self.status,
                'evidence':self.evidence,'device_id':kw['device_id'] or 'native-serial'}
    def send(self, **kw):
        self.calls.append(('send',kw))
        raise AssertionError('real SMS send is forbidden in reconciliation tests')


def fixture(tmp_path, status='sent', evidence='android_sms_sent_callback'):
    service,draft_id=_uncertain_draft(tmp_path,{'state':'pending'})
    service.sms_user_id=service._store.default_user_id
    service.sms_gateway=FakeGateway(status,evidence)
    with service._store._connection:
        service._store._connection.execute(
            "UPDATE conversations SET source='sms',account_ref='',peer_id=?,sender=? "
            "WHERE id=(SELECT conversation_id FROM drafts WHERE id=?)",
            ('+15550000001','+15550000001',draft_id))
    return service,draft_id


def test_readback_original_key_closes_claim_without_sms(tmp_path):
    svc,draft_id=fixture(tmp_path)
    result=svc.reconcile_draft_delivery(draft_id)
    assert result['resolution']=='delivered'
    assert result['receipt']==NATIVE_ID
    assert result['delivery_confirmed'] is False
    assert result['reason']=='android_sms_sent_callback'
    assert svc._store.draft(draft_id).status=='approved'
    assert svc.sms_gateway.calls==[('read',dict(to='+15550000001',
        body=svc._store.draft(draft_id).body,idempotency_key=draft_id,device_id=''))]


@pytest.mark.parametrize('state,evidence',[
 ('queued_for_device',None),('sending',None),('unknown',None),
 ('failed',None),('sent',None),('sent','not_sent_copy')
])
def test_pending_or_unverified_sent_stays_unknown_no_retry(tmp_path,state,evidence):
    svc,draft_id=fixture(tmp_path,status=state,evidence=evidence)
    result=svc.reconcile_draft_delivery(draft_id)
    assert result['resolution']=='unknown'
    assert result['retry_allowed'] is False
    assert svc._store.draft(draft_id).status in ('sending','delivery_unknown')
    assert all(kind=='read' for kind,_ in svc.sms_gateway.calls)


def test_native_positive_evidence_can_be_processed_by_existing_worker(tmp_path):
    svc,draft_id=fixture(tmp_path)
    _make_due(svc,draft_id)
    worker=TelegramDeliveryReconciliationWorker(svc)
    assert worker.run_once()==1
    assert svc._store.draft(draft_id).status=='approved'
    assert worker.run_once()==0
    assert all(x=='read' for x,_ in svc.sms_gateway.calls)


def test_legacy_default_sms_sender_pins_draft_id_on_first_send(tmp_path):
    svc,draft_id=fixture(tmp_path)
    draft=svc._store.draft(draft_id)
    send=svc._prepare_claimed_draft(draft,user_id=svc._store.default_user_id)
    assert svc.sms_gateway.calls==[]
    with pytest.raises(AssertionError,match='forbidden'):
        send()
    assert svc.sms_gateway.calls[0][1]['idempotency_key']==draft_id


def test_forbidden_personal_sms_account_is_not_queried(tmp_path):
    svc,draft_id=fixture(tmp_path)
    with svc._store._connection:
        svc._store._connection.execute(
          "UPDATE conversations SET account_ref='sms:user-foreign-device' "
          "WHERE id=(SELECT conversation_id FROM drafts WHERE id=?)",
          (draft_id,))
    draft=svc._store.draft(draft_id)
    conversation=svc._store.conversation(draft.conversation_id)
    assert original_provider_receipt(svc,draft,conversation,
        user_id=svc._store.default_user_id) is None
    assert svc.sms_gateway.calls==[]


class Response(io.BytesIO):
    def __init__(self,body,status=200):
        super().__init__(json.dumps(body).encode());self.status=status
    def __enter__(self):return self
    def __exit__(self,*args):self.close()


def test_gateway_original_status_is_get_using_exact_key_and_hashes():
    calls=[]
    def runner(request,timeout):
        calls.append(request)
        return Response({'id':NATIVE_ID,'status':'sent',
                          'evidence':'android_sms_sent_callback','device_id':'device1'})
    client=AndroidSmsGatewayClient('http://127.0.0.1:999',token='synthetic',runner=runner)
    row=client.original_send_status(to='+15550000001',body='hello',
                                    idempotency_key='draftkey',device_id='device1')
    assert row['id']==NATIVE_ID
    req=calls[0]
    assert req.get_method()=='GET'
    assert req.get_header('Idempotency-key')=='draftkey'
    assert 'recipient_sha256='+hashlib.sha256(b'+15550000001').hexdigest() in req.full_url
    assert 'body_sha256='+hashlib.sha256(b'hello').hexdigest() in req.full_url


def test_gateway_404_is_unresolved_not_unsent():
    def runner(req,timeout):
        raise urllib.error.HTTPError(req.full_url,404,'missing',{},None)
    c=AndroidSmsGatewayClient('http://127.0.0.1:999',token='synthetic',runner=runner)
    assert c.original_send_status(to='+15550000001',body='hello',
                                   idempotency_key='draft') is None
