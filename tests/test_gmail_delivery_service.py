"""Canonical Gmail unknown-send recovery never replays SMTP."""
import hashlib
import pytest

from test_telegram_delivery_reconciliation import _uncertain_draft, _make_due
from universal_userio.delivery_reconciliation import TelegramDeliveryReconciliationWorker
from universal_userio.adapters import HimalayaGmailOutbox
from universal_userio.delivery_receipt import read_receipt
import universal_userio.provider_readback as readback


def setup(tmp_path):
    service,draft_id=_uncertain_draft(tmp_path,{'state':'pending'})
    account='gmail:work'
    service._store.register_account(
        account_id=account,provider='gmail',display_name='from@example.test',
        can_read=True,can_reply=True,credential_ref='himalaya:work')
    with service._store._connection:
        service._store._connection.execute(
            "UPDATE conversations SET source='gmail:work', account_ref=?,peer_id=?,sender=? "
            "WHERE id=(SELECT conversation_id FROM drafts WHERE id=?)",
            (account,'to@example.test','to@example.test',draft_id))
    message=HimalayaGmailOutbox.outgoing_message_id('work',draft_id)
    receipt=f'himalaya:work:{draft_id}:{message}:saved'
    return service,draft_id,receipt


def test_provider_readback_can_approve_original_smtp_claim_without_resend(tmp_path,monkeypatch):
    service,draft_id,receipt=setup(tmp_path)
    calls=[]
    def read(service,draft,conversation,*,user_id):
        calls.append((draft.id,conversation['source']))
        return receipt
    monkeypatch.setattr(readback,'original_provider_receipt',read)
    previous=len(service.telegram_outbox.sent)
    result=service.reconcile_draft_delivery(draft_id)
    assert result['resolution']=='delivered'
    assert result['reason']=='smtp_accepted_sent_copy_verified'
    assert result['delivery_confirmed'] is False
    assert result['receipt']==receipt
    assert service._store.draft(draft_id).status=='approved'
    assert read_receipt(receipt)['delivery_confirmed'] is False
    assert len(service.telegram_outbox.sent)==previous
    assert calls==[(draft_id,'gmail:work')]
    service._store.close()


def test_gmail_sent_absence_stays_unknown_without_smtp_replay(tmp_path,monkeypatch):
    service,draft_id,_=setup(tmp_path)
    monkeypatch.setattr(readback,'original_provider_receipt',lambda *args,**kwargs:None)
    prev=len(service.telegram_outbox.sent)
    result=service.reconcile_draft_delivery(draft_id)
    assert result['resolution']=='unknown'
    assert result['retry_allowed'] is False
    assert service._store.draft(draft_id).status=='sending'
    assert len(service.telegram_outbox.sent)==prev
    service._store.close()


def test_gmail_original_claim_is_claimed_by_existing_worker(tmp_path,monkeypatch):
    service,draft_id,receipt=setup(tmp_path)
    _make_due(service,draft_id)
    observed=[]
    def read(service,draft,conversation,**kwargs):
        observed.append(draft.id)
        return receipt
    monkeypatch.setattr(readback,'original_provider_receipt',read)
    worker=TelegramDeliveryReconciliationWorker(service)
    assert worker.run_once()==1
    assert observed==[draft_id]
    assert service._store.draft(draft_id).status=='approved'
    assert worker.run_once()==0
    service._store.close()


def test_concurrent_changed_generation_refuses_gmail_receipt(tmp_path,monkeypatch):
    service,draft_id,receipt=setup(tmp_path)
    def read(service,draft,conversation,**kwargs):
        with service._store._connection:
            service._store._connection.execute(
                'UPDATE drafts SET delivery_attempt_generation=delivery_attempt_generation+1 WHERE id=?',
                (draft.id,))
        return receipt
    monkeypatch.setattr(readback,'original_provider_receipt',read)
    result=service.reconcile_draft_delivery(draft_id)
    assert result['resolution']=='superseded'
    assert service._store.draft(draft_id).status!='approved'
    service._store.close()
