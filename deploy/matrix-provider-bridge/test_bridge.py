"""Fast unit: delivery isolation, replay and leave safety (expected 2s, maximum 30s)."""
import json
import tempfile
import unittest
from pathlib import Path
from bridge import Bridge, BRIDGE_FIELD, HASH

class FakeHttp:
    def __init__(self):
        self.calls=[]
        self.conv={"id":"conv_vk","source":"vk","account_ref":"vk:1","peer_id":"2","conversation_kind":"direct","sender":"Peer","messages":[]}
    def request(self, method, path, payload=None, **kwargs):
        self.calls.append((method,path,payload,kwargs))
        if path.endswith("/send"):
            return {"status":"approved","receipt":"vk-personal:99"}
        if "/v1/conversations/" in path:
            return self.conv
        if "/send/m.room.message/" in path:
            return {"event_id":"$projected"}
        return {}

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.mx,self.uio=FakeHttp(),FakeHttp()
        self.b=Bridge({"owner_mxid":"@owner:example.org","server_name":"example.org","database":str(Path(self.tmp.name)/"bridge.db"),
            "accounts":{"vk:1":{"provider":"vk"},"max":{"provider":"max"}},"as_token":"as","hs_token":"hs"},
            mx=self.mx,userio=self.uio)
        self.b.db.execute("INSERT INTO portals VALUES ('conv_vk','!vk:example.org','vk','vk:1','2',1)")
        self.b.db.commit()
    def tearDown(self):
        self.b.db.close();self.tmp.cleanup()
    def evt(self, **changes):
        e={"event_id":"$human","room_id":"!vk:example.org","sender":"@owner:example.org",
            "type":"m.room.message","content":{"msgtype":"m.text","body":"hello"}}
        return dict(e,**changes)
    def job(self, event):
        return {"payload":json.dumps(event)}
    def test_poll_uses_detail_kind_when_summary_omits_it(self):
        original=self.uio.request
        def request(method,path,payload=None,**kwargs):
            if path.startswith("/v1/conversations?"):
                return {"conversations":[{"id":"conv_vk","source":"vk","account_ref":"vk:1"}]} if "vk" in path else {"conversations":[]}
            return original(method,path,payload,**kwargs)
        self.uio.request=request
        self.b.poll()
        self.assertTrue(any(p=="/v1/conversations/conv_vk" for _,p,_,_ in self.uio.calls))
        self.assertGreater(self.b.last_poll,0)
    def test_same_ghost_not_invited_again_for_each_message(self):
        portal=dict(self.b.db.execute("SELECT * FROM portals").fetchone())
        for mid in ("7","8"):
            self.b.import_message(portal,self.uio.conv,{"message_id":"vk:1|2:"+mid,"body":"hello","sender":"2","direction":"incoming"})
        self.assertEqual(sum("/invite" in p for _,p,_,_ in self.mx.calls),1)
    def test_native_userio_approved_with_receipt_is_sent(self):
        self.b.accept_transaction("txn",{"events":[self.evt()]})
        self.b.drain()
        self.assertEqual(self.b.db.execute("SELECT status,receipt FROM jobs").fetchone()[:],("sent","vk-personal:99"))
    def test_lost_response_reconciled_from_existing_draft_without_send(self):
        self.b.config["userio_user_id"]="user_owner"
        self.b.db.execute("INSERT INTO jobs(event_id,payload,status) VALUES (?,?,?)",
            ("$human",json.dumps(self.evt()),"failed"))
        self.b.db.commit()
        conv=dict(self.uio.conv,drafts=[{"id":"draft_web_"+HASH("user_owner:mx_"+HASH("$human")),
            "status":"approved","outbox_receipt":"99"}])
        self.b.reconcile_approved({"room":"!vk:example.org"},conv)
        self.assertEqual(self.b.db.execute("SELECT status,receipt FROM jobs").fetchone()[:],("sent","99"))
        self.assertFalse(self.uio.calls)
    def test_confirmed_delivery_replaces_prior_projected_echo(self):
        self.b.db.execute("INSERT INTO jobs(event_id,payload,status,receipt) VALUES (?,?,?,?)",
            ("$human",json.dumps(self.evt()),"sent","99"))
        self.b.db.execute("INSERT INTO mappings VALUES (?,?,?,?,?)",("conv_vk","vk:1|2:99","$echo",self.b.owner,"old"))
        self.b.db.commit()
        self.b.import_message(dict(self.b.db.execute("SELECT * FROM portals").fetchone()),self.uio.conv,
            {"message_id":"vk:1|2:99","body":"hello","direction":"outgoing"})
        self.assertEqual(self.b.db.execute("SELECT event FROM mappings").fetchone()[0],"$human")
        self.assertTrue(any("/redact/" in p for _,p,_,_ in self.mx.calls))
    def test_transaction_replay_exactly_once(self):
        p={"events":[self.evt()]}
        self.b.accept_transaction("txn",p);self.b.accept_transaction("txn",p)
        self.assertEqual(self.b.db.execute("SELECT count(*) FROM jobs").fetchone()[0],1)
        self.b.drain();self.b.drain()
        self.assertEqual(sum(p.endswith("/send") for _,p,_,_ in self.uio.calls),1)
    def test_transaction_conflicting_reuse_rejected(self):
        self.b.accept_transaction("txn",{"events":[self.evt()]})
        with self.assertRaises(ValueError):self.b.accept_transaction("txn",{"events":[]})
    def test_foreign_author_not_forwarded(self):
        self.b.accept_transaction("txn",{"events":[self.evt(sender="@other:example.org")]})
        self.assertEqual(self.b.db.execute("SELECT count(*) FROM jobs").fetchone()[0],0)
    def test_leave_stops_local_portal_without_remote_operation(self):
        e=self.evt(type="m.room.member",state_key=self.b.owner,content={"membership":"leave"})
        self.assertEqual(self.b.process(self.job(e)),("ignored",""))
        self.assertEqual(self.b.db.execute("SELECT active FROM portals").fetchone()[0],0)
        self.assertFalse(self.uio.calls)
        self.assertEqual(self.b.process(self.job(self.evt())),("ignored",""))
        self.assertFalse(self.uio.calls)
    def test_rejoin_resumes_same_portal(self):
        for value in ("leave","join"):
            self.b.process(self.job(self.evt(type="m.room.member",state_key=self.b.owner,content={"membership":value})))
        self.assertEqual(self.b.db.execute("SELECT active FROM portals").fetchone()[0],1)
    def test_channels_and_unowned_accounts_excluded(self):
        self.assertTrue(self.b.selected(self.uio.conv))
        self.assertFalse(self.b.selected(dict(self.uio.conv,conversation_kind="channel")))
        self.assertFalse(self.b.selected(dict(self.uio.conv,account_ref="vk:foreign")))
        self.assertFalse(self.b.selected(dict(self.uio.conv,source="max")))
    def test_destination_revalidated_before_submission(self):
        self.uio.conv["peer_id"]="wrong"
        with self.assertRaises(ValueError):self.b.process(self.job(self.evt()))
        self.assertFalse(any(p.endswith("/send") for _,p,_,_ in self.uio.calls))
    def test_unknown_delivery_not_replayed(self):
        original=self.uio.request
        def fail(method,path,payload=None,**kw):
            if path.endswith("/send"):
                self.uio.calls.append((method,path,payload,kw));raise TimeoutError()
            return original(method,path,payload,**kw)
        self.uio.request=fail
        self.b.accept_transaction("txn",{"events":[self.evt()]})
        self.b.drain();self.b.drain()
        self.assertEqual(sum(p.endswith("/send") for _,p,_,_ in self.uio.calls),1)
        self.assertEqual(self.b.db.execute("SELECT status FROM jobs").fetchone()[0],"failed")
    def test_bridge_echo_is_not_sent_to_provider(self):
        e=self.evt(content={"msgtype":"m.text","body":"echo",BRIDGE_FIELD:{}})
        self.assertEqual(self.b.process(self.job(e)),("ignored",""))
        self.assertFalse(self.uio.calls)
    def test_same_receipt_in_other_portal_does_not_suppress_message(self):
        self.b.db.execute("INSERT INTO jobs(event_id,payload,status,receipt) VALUES (?,?,?,?)",
            ("$other",json.dumps(self.evt(room_id="!other:example.org")),"sent","vk-personal:99"))
        self.b.db.commit()
        portal=dict(self.b.db.execute("SELECT * FROM portals").fetchone())
        msg={"message_id":"vk:1|2:99","body":"hello","direction":"outgoing"}
        self.b.import_message(portal,self.uio.conv,msg)
        self.assertTrue(any("/send/m.room.message/" in p for _,p,_,_ in self.mx.calls))
    def test_own_send_echo_maps_to_original_human_event(self):
        self.b.db.execute("INSERT INTO jobs(event_id,payload,status,receipt) VALUES (?,?,?,?)",
            ("$human",json.dumps(self.evt()),"sent","vk-personal:99"))
        self.b.db.commit()
        self.b.import_message(dict(self.b.db.execute("SELECT * FROM portals").fetchone()),self.uio.conv,
            {"message_id":"vk:1|2:99","body":"hello","direction":"outgoing"})
        self.assertFalse(self.mx.calls)
        self.assertEqual(self.b.db.execute("SELECT event,sender FROM mappings").fetchone()[:],("$human",self.b.owner))
    def test_provider_edit_is_matrix_replace_and_is_deduplicated(self):
        portal=dict(self.b.db.execute("SELECT * FROM portals").fetchone())
        msg={"message_id":"vk:1|2:7","body":"before","sender":"2","direction":"incoming"}
        self.b.import_message(portal,self.uio.conv,msg)
        self.mx.calls.clear()
        msg["body"]="after"
        self.b.import_message(portal,self.uio.conv,msg)
        self.b.import_message(portal,self.uio.conv,msg)
        sends=[c for _,p,c,_ in self.mx.calls if "/send/m.room.message/" in p]
        self.assertEqual(len(sends),1)
        self.assertEqual(sends[0]["m.relates_to"],{"rel_type":"m.replace","event_id":"$projected"})
        self.assertEqual(sends[0]["m.new_content"]["body"],"after")
    def test_edit_from_matrix_only_updates_own_source(self):
        self.b.db.execute("INSERT INTO mappings VALUES (?,?,?,?,?)",("conv_vk","vk:1|2:99","$human",self.b.owner,"x"))
        self.b.db.commit()
        self.uio.conv["messages"]=[{"message_id":"vk:1|2:99","direction":"outgoing","body":"before"}]
        event=self.evt(event_id="$edit",content={"msgtype":"m.text","body":"* after",
            "m.relates_to":{"rel_type":"m.replace","event_id":"$human"},"m.new_content":{"msgtype":"m.text","body":"after"}})
        self.assertEqual(self.b.process(self.job(event)),("edited",""))
        patch=[c for m,p,c,_ in self.uio.calls if m=="PATCH"][0]
        self.assertEqual(patch,{"body":"after","expected_body":"before"})
    def test_direct_reply_fallback_not_sent_as_markup(self):
        e=self.evt(content={"msgtype":"m.text","body":"> <peer> quoted\n\nreply",
            "m.relates_to":{"m.in_reply_to":{"event_id":"$original"}}})
        self.b.process(self.job(e))
        send=next(c for m,p,c,_ in self.uio.calls if p.endswith("/send"))
        self.assertEqual(send["body"],"reply")
    def test_failed_matrix_receipt_keeps_confirmed_provider_send(self):
        original=self.mx.request
        def fail(m,p,c=None,**kw):
            if "/receipt/" in p:raise TimeoutError()
            return original(m,p,c,**kw)
        self.mx.request=fail
        self.b.accept_transaction("txn",{"events":[self.evt()]})
        self.b.drain()
        self.assertEqual(self.b.db.execute("SELECT status FROM jobs").fetchone()[0],"sent")

    def media_fixture(self, mime="application/octet-stream", body=""):
        self.media_calls=[]
        def uio(method,path,payload=None,**kwargs):
            self.media_calls.append(path)
            return b"\x89PNG\r\n\x1a\nactual image bytes",mime
        previous=self.mx.request
        def mx(method,path,payload=None,**kwargs):
            if "/media/v3/upload" in path:
                self.mx.calls.append((method,path,payload,kwargs))
                return {"content_uri":"mxc://example.org/actual"}
            return previous(method,path,payload,**kwargs)
        self.uio.request=uio;self.mx.request=mx
        self.portal=dict(self.b.db.execute("SELECT * FROM portals").fetchone())
        self.message={"body":body,"attachments":[{"kind":"image","filename":"photo.png"}]}
        self.b.db.execute("INSERT INTO mappings VALUES (?,?,?,?,?)",("conv_vk","mid","$placeholder",self.b.owner,"hash"))
        self.b.db.commit()
    def test_native_generic_mime_png_becomes_matrix_image_and_hides_empty_placeholder_once(self):
        self.media_fixture()
        self.b.import_media(self.portal,"conv_vk","mid",self.message,self.b.owner)
        self.b.import_media(self.portal,"conv_vk","mid",self.message,self.b.owner)
        sends=[c for m,p,c,k in self.mx.calls if "/send/m.room.message/" in p]
        self.assertEqual(sends[0]["msgtype"],"m.image")
        self.assertEqual(sends[0]["url"],"mxc://example.org/actual")
        self.assertEqual(sends[0]["info"]["mimetype"],"image/png")
        self.assertEqual(len(self.media_calls),1)
        self.assertEqual(sum("/redact/" in p for m,p,c,k in self.mx.calls),1)
    def test_image_caption_is_preserved(self):
        self.media_fixture(body="Meaningful caption")
        self.b.import_media(self.portal,"conv_vk","mid",self.message,self.b.owner)
        self.assertFalse(any("/redact/" in p for m,p,c,k in self.mx.calls))
    def test_failed_media_backoff_survives_restart(self):
        self.media_fixture();attempts=[]
        def fail(*args,**kwargs):attempts.append(args);raise ValueError("unsupported native media")
        self.uio.request=fail
        self.b.import_media(self.portal,"conv_vk","mid",self.message,self.b.owner)
        config=self.b.config;self.b.db.close();self.b=Bridge(config,mx=self.mx,userio=self.uio)
        self.b.import_media(self.portal,"conv_vk","mid",self.message,self.b.owner)
        self.assertEqual(len(attempts),1)
    def test_changed_attachment_retries_immediately_and_success_clears_backoff(self):
        self.media_fixture()
        original=self.uio.request
        self.uio.request=lambda *a,**k:(_ for _ in ()).throw(ValueError("not ready"))
        self.b.import_media(self.portal,"conv_vk","mid",self.message,self.b.owner)
        self.uio.request=original
        self.message["attachments"][0]["filename"]="available.png"
        self.b.import_media(self.portal,"conv_vk","mid",self.message,self.b.owner)
        self.assertEqual(self.b.db.execute("SELECT count(*) FROM media_retries").fetchone()[0],0)
        self.assertEqual(self.b.db.execute("SELECT count(*) FROM media_links").fetchone()[0],1)

if __name__=="__main__":unittest.main()
