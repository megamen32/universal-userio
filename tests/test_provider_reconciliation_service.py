"""Integration of provider-specific READ-ONLY receipts with original draft CAS."""
import pytest

from test_telegram_delivery_reconciliation import _uncertain_draft, _make_due
from universal_userio.delivery_reconciliation import TelegramDeliveryReconciliationWorker
import universal_userio.provider_readback as readback


def case(tmp_path, source):
    service,draft_id = _uncertain_draft(tmp_path,{"state":"pending"})
    conversation = service._store.draft(draft_id).conversation_id
    with service._store._connection:
        service._store._connection.execute(
            "UPDATE conversations SET source=?,account_ref=?,peer_id=? WHERE id=?",
            (source,'vk:123' if source=='vk' else 'matrix:user' if source=='matrix'
                    else 'yandex:user','42',conversation))
    return service,draft_id


@pytest.mark.parametrize('channel,receipt',[
    ('vk','12345'),('matrix','matrix:$event'),('yandex','123456789'),
])
def test_positive_original_provider_receipt_approves_and_never_resends(tmp_path,monkeypatch,
                                                                      channel,receipt):
    service,draft_id=case(tmp_path,channel)
    seen=[]
    def reader(s,draft,conv,*,user_id):
        seen.append((draft.id,conv['source'],user_id))
        return receipt
    monkeypatch.setattr(readback,'original_provider_receipt',reader)
    outbox=service.telegram_outbox
    original_sends=len(outbox.sent)
    result=service.reconcile_draft_delivery(draft_id)
    assert result['resolution']=='delivered'
    assert result['receipt']==receipt
    assert service._store.draft(draft_id).status=='approved'
    assert len(outbox.sent)==original_sends
    assert len(seen)==1 and seen[0][0]==draft_id
    service._store.close()


@pytest.mark.parametrize('channel',['vk','matrix','yandex'])
def test_unavailable_original_receipt_keeps_unknown_and_no_retry(tmp_path,monkeypatch,channel):
    service,draft_id=case(tmp_path,channel)
    monkeypatch.setattr(readback,'original_provider_receipt',
                        lambda *a,**kw:None)
    outbox=service.telegram_outbox
    previous=len(outbox.sent)
    result=service.reconcile_draft_delivery(draft_id)
    assert result['resolution']=='unknown'
    assert result['retry_allowed'] is False
    assert service._store.draft(draft_id).status in ('sending','delivery_unknown')
    assert len(outbox.sent)==previous
    service._store.close()


@pytest.mark.parametrize('channel',['vk','matrix','yandex'])
def test_generic_automatic_lease_claims_exact_original_draft(tmp_path,monkeypatch,channel):
    service,draft_id=case(tmp_path,channel)
    _make_due(service,draft_id)
    seen=[]
    monkeypatch.setattr(readback,'original_provider_receipt',
                        lambda service,draft,conversation,**kw:
                          (seen.append(draft.id) or 'receipt-native-id'))
    worker=TelegramDeliveryReconciliationWorker(service)
    assert worker.run_once()==1
    assert seen==[draft_id]
    assert service._store.draft(draft_id).status=='approved'
    assert worker.run_once()==0
    service._store.close()


def test_stale_generation_refuses_positive_original_provider_receipt(tmp_path,monkeypatch):
    service,draft_id=case(tmp_path,'vk')
    def read(service,draft,conversation,**kw):
        with service._store._connection:
            service._store._connection.execute(
                "UPDATE drafts SET delivery_attempt_generation=delivery_attempt_generation+1 WHERE id=?",
                (draft.id,))
        return '123'
    monkeypatch.setattr(readback,'original_provider_receipt',read)
    result=service.reconcile_draft_delivery(draft_id)
    assert result['resolution']=='superseded'
    assert service._store.draft(draft_id).status!='approved'
    service._store.close()
