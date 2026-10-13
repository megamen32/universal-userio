"""Focused file unit: scoped staging and no replay; expected2/max30 seconds."""
import base64,hashlib,json,tempfile,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from universal_userio.store import SQLiteUserIOStore
from universal_userio.max_web import MaxWeb
from universal_userio.contracts import ReplyDraft,DeliveryNotSubmittedError

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=SQLiteUserIOStore(str(Path(self.tmp.name)/"state.db"))
        self.conv={"id":"cid","account_ref":"max","peer_id":"42","source":"max","route_id":"max"}
        self.store.conversation=lambda cid,**kw:self.conv if cid=="cid" else None
        self.store.send_enabled=lambda **kw:True
        self.store.route_allowed=lambda **kw:True
        self.service=SimpleNamespace(_store=self.store,require_max_reply_account=lambda c,u:None)
        self.web=MaxWeb(self.service,"user_owner")
        blob=b"actual bytes"
        upload=self.web.stage("cid",{"filename":"actual.txt","mime_type":"text/plain",
            "data_base64":base64.b64encode(blob).decode(),"sha256":hashlib.sha256(blob).hexdigest(),"request_id":"matrix-file-test-1"})
        self.draft=ReplyDraft("draft_test","cid","caption","proposed")
        self.web.bind(self.draft,self.conv,upload["upload_id"])
        self.row=self.web.for_draft(self.draft.id,self.conv)
    def tearDown(self):
        self.store._connection.close();self.tmp.cleanup()
    def test_bytes_and_native_file_send_keep_exact_peer_and_stable_id(self):
        calls=[]
        client=SimpleNamespace(upload_file=lambda **kw:(calls.append(("upload",kw)) or {"_type":"FILE","fileId":7}),
            send_bound=lambda **kw:(calls.append(("send",kw)) or {"message_id":"99"}))
        self.assertEqual(self.web.send_file(client,self.conv,self.draft,self.row),"99")
        self.assertEqual(calls[0][1]["data"],b"actual bytes")
        self.assertEqual(calls[1][1]["chat_id"],"42")
        self.assertEqual(calls[1][1]["text"],"caption")
        self.assertEqual(calls[1][1]["attaches"],[{"_type":"FILE","fileId":7}])
        self.assertIsInstance(calls[1][1]["cid"],int)
    def test_staged_file_cannot_be_sent_to_another_peer(self):
        with self.assertRaises(ValueError):
            self.web.for_draft(self.draft.id,dict(self.conv,peer_id="foreign"))
    def test_unconfirmed_send_keeps_native_upload_without_second_upload(self):
        calls=[]
        def fail(**kw):raise DeliveryNotSubmittedError()
        client=SimpleNamespace(upload_file=lambda **kw:(calls.append(kw) or {"_type":"FILE","fileId":7}),
            send_bound=fail)
        with self.assertRaises(RuntimeError):self.web.send_file(client,self.conv,self.draft,self.row)
        current=self.web.for_draft(self.draft.id,self.conv)
        self.assertTrue(current["native_attachment"])
        self.assertEqual(len(calls),1)
    def test_same_draft_cannot_bind_a_different_file(self):
        with self.assertRaises(ValueError):
            self.web.bind(self.draft,self.conv,"upload_foreign")
if __name__=="__main__":unittest.main()

def test_max_matrix_file_uses_manual_delivery_claim_once(tmp_path):
    from tests.test_max_web_delivery import setup
    store,transport,service,cid=setup(tmp_path)
    conv=store.conversation(cid)
    upload_calls=[]
    service.max_client.upload_file=lambda **kw:(upload_calls.append(kw) or {'_type':'FILE','fileId':7})
    web=MaxWeb(service,store.default_user_id)
    blob=b'file canary'
    staged=web.stage(cid,{'filename':'canary.txt','mime_type':'text/plain',
        'data_base64':base64.b64encode(blob).decode(),'sha256':hashlib.sha256(blob).hexdigest(),
        'request_id':'matrix-file-integration-001'})
    result=service.send_manual_message(cid,body='',request_id='matrix-file-integration-001',upload_id=staged['upload_id'])
    replay=service.send_manual_message(cid,body='',request_id='matrix-file-integration-001',upload_id=staged['upload_id'])
    assert result.status=='approved' and result.receipt=='124' and replay.receipt=='124'
    assert len(upload_calls)==1 and upload_calls[0]['data']==blob
    sends=[f for f in transport.sent if f['opcode']==64]
    assert len(sends)==1 and sends[0]['payload']['message']['attaches']==[{'_type':'FILE','fileId':7}]
