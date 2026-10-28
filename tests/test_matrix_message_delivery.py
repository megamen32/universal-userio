"""Focused generic draft path; no server, credentials or external send."""
import json
from types import SimpleNamespace
import urllib.error

import pytest

from universal_userio.contact_start import ContactStartError
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


def fixture(tmp_path, monkeypatch, can_reply=True):
    store = SQLiteUserIOStore(tmp_path/'userio.sqlite3')
    service = UserIOService(store, SimpleNamespace(), SimpleNamespace())
    user = store.default_user_id
    store.register_account(account_id='matrix:qa-a',provider='matrix',display_name='@a:test',
        enabled=True,can_read=True,can_reply=can_reply,credential_ref='connector:qa-a',user_id=user)
    class Vault:
        def load(self, ref, *, user_id, provider):
            assert ref=='connector:qa-a' and user_id==user and provider=='matrix'
            return {'homeserver':'https://matrix.test','access_token':'account-a-token',
                    'matrix_user_id':'@a:test','account_id':'matrix:qa-a','user_id':user}
    import universal_userio.account_connections as connections
    monkeypatch.setattr(connections,'ConnectorSecrets',Vault)
    return store, service, user


def test_matrix_exact_account_draft_approve_has_provider_receipt_and_no_replay(tmp_path,monkeypatch):
    store, service, user = fixture(tmp_path,monkeypatch)
    prepared = service.contact_starter.prepare(user_id=user,account_ref='matrix:qa-a',
        identifier='!strict:test',text='Synthetic notice',request_id='matrix-message-qa-001')
    import universal_userio.channels.matrix as matrix
    calls=[]
    class Response:
        status=200
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def read(self,*_):return json.dumps({'event_id':'$actual-provider-id'}).encode()
    def request(req, timeout):
        if req.get_method() == 'GET':
            raise urllib.error.HTTPError(req.full_url,404,'No encryption state',{},None)
        calls.append(req)
        assert req.get_header('Authorization')=='Bearer account-a-token'
        assert '/rooms/%21strict%3Atest/send/m.room.message/' in req.full_url
        return Response()
    monkeypatch.setattr(matrix,'_matrix_open',request)
    approved=service.approve(prepared['draft_id'],user_id=user,expected_snapshot={
        'expected_text':'Synthetic notice','expected_chat_id':prepared['conversation_id'],'expected_attachments':[]})
    assert approved.receipt=='matrix:$actual-provider-id'
    assert service.approve(prepared['draft_id'],user_id=user).receipt==approved.receipt
    assert len(calls)==1
    store.close()


def test_matrix_read_only_account_is_not_promoted_by_contact_prepare(tmp_path,monkeypatch):
    store,service,user=fixture(tmp_path,monkeypatch,can_reply=False)
    with pytest.raises(ContactStartError,match='not_reply_capable'):
        service.contact_starter.prepare(user_id=user,account_ref='matrix:qa-a',
            identifier='!strict:test',text='No permission',request_id='matrix-message-qa-002')
    assert 'reply' not in store.accounts(user_id=user)[0]['capabilities']
    store.close()


def test_matrix_unknown_provider_outcome_keeps_claim_and_blocks_repeat(tmp_path,monkeypatch):
    store,service,user=fixture(tmp_path,monkeypatch)
    draft=service.contact_starter.prepare(user_id=user,account_ref='matrix:qa-a',
        identifier='!strict:test',text='One attempt',request_id='matrix-message-qa-003')
    import universal_userio.channels.matrix as matrix
    attempts=[]
    def request(req,timeout):
        if req.get_method()=='GET':
            raise urllib.error.HTTPError(req.full_url,404,'No encryption state',{},None)
        attempts.append(req)
        raise TimeoutError('provider may have accepted')
    monkeypatch.setattr(matrix,'_matrix_open',request)
    with pytest.raises(TimeoutError):
        service.approve(draft['draft_id'],user_id=user)
    from universal_userio.service import DeliveryUnavailableError
    with pytest.raises(DeliveryUnavailableError,match='Повтор заблокирован'):
        service.approve(draft['draft_id'],user_id=user)
    assert len(attempts)==1
    assert store.draft(draft['draft_id'],user_id=user).status=='sending'
    store.close()


def test_matrix_exact_chat_grant_denies_foreign_account_room_and_draft(tmp_path,monkeypatch):
    store,service,user=fixture(tmp_path,monkeypatch)
    allowed=service.contact_starter.prepare(user_id=user,account_ref='matrix:qa-a',
        identifier='!strict:test',text='Allowed',request_id='matrix-message-qa-004')
    foreign=service.contact_starter.prepare(user_id=user,account_ref='matrix:qa-a',
        identifier='!foreign:test',text='Forbidden',request_id='matrix-message-qa-005')
    from universal_userio.contracts import UserPrincipal
    from universal_userio.scoped_permissions import enforce,validate_grants
    grants=validate_grants(store,user,[{'account_ref':'matrix:qa-a','access':'read_write',
        'chats':[{'conversation_id':allowed['conversation_id'],'access':'read_write'}]}])
    principal=UserPrincipal(user,'qa','user',messenger_grants=tuple(grants))
    enforce(store,principal,'userio.messages.prepare',{'account_ref':'matrix:qa-a',
        'conversation_id':allowed['conversation_id']})
    enforce(store,principal,'userio.messages.send',{'draft_id':allowed['draft_id']})
    for args in ({'account_ref':'matrix:foreign','conversation_id':allowed['conversation_id']},
                 {'account_ref':'matrix:qa-a','conversation_id':foreign['conversation_id']},
                 {'account_ref':'matrix:qa-a','identifier':'!foreign:test'}):
        with pytest.raises(PermissionError):
            enforce(store,principal,'userio.messages.prepare',args)
    with pytest.raises(PermissionError):
        enforce(store,principal,'userio.messages.send',{'draft_id':foreign['draft_id']})
    store.close()
