"""Focused integration, expected 5s/max30s; no network or credentials.

Detects new-file/reference confusion, source/user substitution, staged byte
tampering, quota/expiry drift and ambiguous upload/send replay.
"""
import base64
import hashlib
import json
from dataclasses import replace

import pytest

from test_messenger import case, dispatch
from universal_userio.messenger import Messenger
from universal_userio.messenger_uploads import MAX_BYTES


def staged(case, request='stage-new-file-123456', **changes):
    store,service,p,cid,_=case
    p.operations.add('file_upload')
    data=b'New exported document, not an existing Telegram attachment.'
    args=dict(account_ref=p.account_ref,conversation_id=cid,source_message_id=p.account_ref+'|-10042:77',
              filename='report.txt',mime_type='text/plain',data_base64=base64.b64encode(data).decode(),
              sha256=hashlib.sha256(data).hexdigest(),request_id=request)
    args.update(changes)
    return Messenger(store,service,store.owner()),args,data


def test_new_bytes_are_private_bound_and_approved_once(case):
    api,args,data=staged(case)
    result=api.upload(**args)
    assert 'data' not in result and 'data_base64' not in result
    assert api.upload(**args)==result and case[2].calls==[]
    a=api.propose(args['account_ref'],args['conversation_id'],args['source_message_id'],'file',
                  {'upload_id':result['upload_id'],'caption':'New report'},'send-new-file-123456')
    assert a['envelope']['payload']['sha256']==hashlib.sha256(data).hexdigest()
    assert a['envelope']['media_hash']=='' and 'media_message_id' not in a['envelope']['payload']
    assert data.decode() not in json.dumps(a)
    from universal_userio.messenger_uploads import MessengerUploads
    assert MessengerUploads(case[0],case[0].default_user_id).data(a['envelope'])==data
    accepted=api.approve(a['id'],a['hash'],True)
    assert accepted['status']=='accepted' and len(case[2].calls)==1
    assert api.approve(a['id'],a['hash'],True)==accepted and len(case[2].calls)==1
    assert case[0]._connection.execute('SELECT data FROM messenger_uploads').fetchone()[0] is None


@pytest.mark.parametrize('change,error',[
    ({'filename':'../report.txt'},'plain_filename_required'),
    ({'mime_type':'text/plain\r\nBad: value'},'mime_type_required'),
    ({'data_base64':'?bad'},'invalid_upload_base64'),
    ({'sha256':'0'*64},'upload_sha256_conflict'),
    ({'data_base64':base64.b64encode(b'x'*(MAX_BYTES+1)).decode()},'upload_exceeds_2MiB'),
])
def test_invalid_content_does_not_stage_or_send(case,change,error):
    api,args,_=staged(case,**change)
    with pytest.raises(ValueError,match=error):api.upload(**args)
    assert not case[2].calls


def test_scope_source_and_single_action_binding(case):
    api,args,data=staged(case);u=api.upload(**args)
    assert not dispatch(case,'upload',args,readonly=True)['ok']
    from universal_userio.messenger_uploads import MessengerUploads
    other=MessengerUploads(case[0],'different-user')
    with pytest.raises(KeyError):other.metadata(u['upload_id'],{})
    with pytest.raises(ValueError,match='invalid_upload_payload'):
        api.propose(args['account_ref'],args['conversation_id'],args['source_message_id'],'file',
            {'upload_id':u['upload_id'],'media_message_id':args['source_message_id']},'mixed-file-payload-123456')
    a=api.propose(args['account_ref'],args['conversation_id'],args['source_message_id'],'file',{'upload_id':u['upload_id']},'bound-one-action-123456')
    with pytest.raises(ValueError,match='upload_already_bound'):
        api.propose(args['account_ref'],args['conversation_id'],args['source_message_id'],'file',{'upload_id':u['upload_id']},'bound-second-action-123456')
    assert api.propose(args['account_ref'],args['conversation_id'],args['source_message_id'],'file',{'upload_id':u['upload_id']},'bound-one-action-123456')==a
    with case[0]._connection:
        case[0]._connection.execute('UPDATE messages SET body=?',('source was edited',))
    with pytest.raises(ValueError,match='source_snapshot_conflict'):api.approve(a['id'],a['hash'],True)
    assert api.get(a['id'])['status']=='proposed' and not case[2].calls


def test_expiry_quota_and_immutable_bytes(case):
    api,args,_=staged(case)
    first=api.upload(**args)
    for n in range(3):api.upload(**{**args,'request_id':f'another-staged-file-1234-{n}'})
    with pytest.raises(ValueError,match='upload_staging_limit'):api.upload(**{**args,'request_id':'fifth-staged-file-123456'})
    a=api.propose(args['account_ref'],args['conversation_id'],args['source_message_id'],'file',{'upload_id':first['upload_id']},'tampered-file-action-123456')
    with case[0]._connection:case[0]._connection.execute('UPDATE messenger_uploads SET data=? WHERE id=?',(b'tampered',first['upload_id']))
    with pytest.raises(ValueError,match='upload_bytes_conflict'):api.approve(a['id'],a['hash'],True)
    with case[0]._connection:case[0]._connection.execute('UPDATE messenger_uploads SET expires_at=0')
    with pytest.raises(ValueError,match='upload_expired_or_consumed'):api.approve(a['id'],a['hash'],True)
    api.upload(**{**args,'request_id':'fresh-after-expiry-123456'})
    assert case[0]._connection.execute('SELECT COUNT(*) FROM messenger_uploads WHERE data IS NOT NULL').fetchone()[0]==1
    assert not case[2].calls


def test_uncertain_upload_action_is_never_repeated(case):
    api,args,_=staged(case);u=api.upload(**args)
    a=api.propose(args['account_ref'],args['conversation_id'],args['source_message_id'],'file',{'upload_id':u['upload_id']},'unknown-upload-action-123456')
    case[2].fail=True
    with pytest.raises(TimeoutError):api.approve(a['id'],a['hash'],True)
    assert api.get(a['id'])['status']=='sending'
    with pytest.raises(ValueError,match='delivery_uncertain'):api.approve(a['id'],a['hash'],True)
    assert len(case[2].calls)==1


@pytest.mark.parametrize('legacy',[False,True])
def test_both_mcp_dispatchers_expose_staging_without_send(case,legacy):
    api,args,_=staged(case)
    response=dispatch(case,'upload',args,legacy=legacy)
    assert response['ok'] and response['upload']['upload_id'].startswith('upload_')
    assert not case[2].calls


def test_metadata_corruption_before_propose_is_refused(case):
    api,args,_=staged(case);u=api.upload(**args)
    row=case[0]._connection.execute('SELECT metadata FROM messenger_uploads').fetchone()
    changed=json.loads(row[0]);changed['filename']='different-document.txt'
    with case[0]._connection:case[0]._connection.execute('UPDATE messenger_uploads SET metadata=?',(json.dumps(changed),))
    with pytest.raises(ValueError,match='upload_metadata_conflict'):
        api.propose(args['account_ref'],args['conversation_id'],args['source_message_id'],'file',{'upload_id':u['upload_id']},'tampered-metadata-action-123456')
    assert not case[2].calls
