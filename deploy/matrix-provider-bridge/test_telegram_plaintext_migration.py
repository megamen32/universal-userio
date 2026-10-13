import hashlib,json,pathlib,sqlite3,tempfile,unittest
from unittest.mock import Mock
import yaml
from telegram_plaintext_migration import Migration
class MigrationTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  root=pathlib.Path(self.temp.name)
  self.a=Migration.__new__(Migration);a=self.a;a.work=root;a.data=root;a.path=root/"manifest.json";a.dbpath=root/"bridge.sqlite3"
  a.owner="@bezrabotnyi:chat.bezrabotnyi.com";a.bot="@telegrambot:chat.bezrabotnyi.com";a.domain="chat.bezrabotnyi.com"
  a.m={"stage":"created","rows":[{"portal":{"bridge_id":"","id":"user:1","receiver":"10"},"old":"!old","new":"!new"}]}
  a.docker=Mock();a.backup=Mock();a.state=Mock(return_value=None)
  (root/"config.yaml").write_text(yaml.safe_dump({"encryption":{"default":True,"require":False,"allow":True}}))
  d=sqlite3.connect(a.dbpath)
  d.executescript("""CREATE TABLE portal(bridge_id,id,receiver,mxid,in_space);
CREATE TABLE message(bridge_id,room_id,room_receiver,mxid);
CREATE TABLE reaction(bridge_id,room_id,room_receiver);
CREATE TABLE backfill_task(bridge_id,portal_id,portal_receiver);
CREATE TABLE user_portal(bridge_id,portal_id,portal_receiver,in_space,last_read);
INSERT INTO portal VALUES('','user:1','10','!old',1),('','user:1','20','!foreign',1);
INSERT INTO message VALUES('','user:1','10','$old'),('','user:1','20','$foreign');
INSERT INTO reaction VALUES('','user:1','10'),('','user:1','20');
INSERT INTO backfill_task VALUES('','user:1','10'),('','user:1','20');
INSERT INTO user_portal VALUES('','user:1','10',1,123),('','user:1','20',1,456);""")
  d.close()
 def test_cutover_isolates_receiver_and_preserves_foreign_mappings(self):
  a=self.a;a.cutover();d=sqlite3.connect(a.dbpath)
  self.assertEqual(d.execute("select mxid from portal where receiver='10'").fetchone()[0],"!new")
  self.assertEqual(d.execute("select * from message").fetchall(),[('','user:1','20','$foreign')])
  self.assertEqual(d.execute("select in_space,last_read from user_portal where portal_receiver='20'").fetchone(),(1,456))
  cfg=yaml.safe_load((a.data/"config.yaml").read_text());self.assertFalse(cfg["encryption"]["default"]);self.assertTrue(cfg["encryption"]["allow"])
  self.assertEqual(a.m["stage"],"cutover");d.close()
 def test_changed_portal_aborts_before_any_delete_and_restarts(self):
  a=self.a;d=sqlite3.connect(a.dbpath);d.execute("update portal set mxid='!unrelated' where receiver='10'");d.commit();d.close()
  with self.assertRaisesRegex(RuntimeError,"portal changed"):a.cutover()
  d=sqlite3.connect(a.dbpath);self.assertEqual(d.execute("select count(*) from message").fetchone()[0],2);d.close()
  self.assertEqual(a.docker.call_args.args,("start",))
 def test_cutover_recovers_committed_mapping_after_lost_checkpoint(self):
  a=self.a;d=sqlite3.connect(a.dbpath);d.execute("update portal set mxid='!new' where receiver='10'");d.commit();d.close()
  a.cutover();self.assertEqual(a.m["stage"],"cutover")
 def test_direct_list_preserves_unrelated_entries_exactly(self):
  a=self.a;a.personal="!personal";a.m.update(stage="cutover",archive="!archive",spaces=[],archive_spaces={})
  a.m["rows"][0]["finished"]=True
  direct={"@contact":["!old"],"@unrelated":["!keep","!keep"]}
  a.mx=Mock(side_effect=[direct,{}]);a.link=Mock();a.finish()
  saved=a.mx.call_args_list[-1].args[2]
  self.assertEqual(saved["@contact"],["!new"]);self.assertEqual(saved["@unrelated"],["!keep","!keep"])
  a.state.assert_called_once_with(a.personal,"m.space.child","!old",{})
 def prepare_create(self):
  a=self.a
  state=[{"type":"m.room.encryption","state_key":"","content":{"algorithm":"m.megolm.v1.aes-sha2"}},
   {"type":"m.room.power_levels","state_key":"","content":{"users":{a.owner:50}}},
   {"type":"m.room.name","state_key":"","content":{"name":"Person"}},
   {"type":"m.space.parent","state_key":"!forum","content":{"via":[a.domain]}}]
  a.m={"stage":"inventoried","rows":[{"old":"!old","new":None,"portal":{"room_type":"dm"},"state":state}]}
  return a
 def test_created_room_excludes_encryption_but_keeps_parent_and_owner(self):
  a=self.prepare_create();requests=[]
  def mx(method,path,body=None,user=None,missing=False):
   requests.append((method,path,body,user))
   if path=="/joined_rooms":return {"joined_rooms":[]}
   if path=="/createRoom":return {"room_id":"!new"}
   return {}
  a.mx=mx;a.create()
  payload=next(x[2] for x in requests if x[1]=="/createRoom")
  self.assertNotIn("m.room.encryption",[x["type"] for x in payload["initial_state"]])
  self.assertIn("m.space.parent",[x["type"] for x in payload["initial_state"]])
  self.assertEqual(payload["invite"],[a.owner]);self.assertEqual(payload["power_level_content_override"]["events"]["m.room.encryption"],9001)
 def test_marker_recovers_created_room_without_second_creation(self):
  a=self.prepare_create();a.mx=Mock(side_effect=[{"joined_rooms":["!already"]},{}])
  a.state=Mock(return_value={"migration":hashlib.sha256(str(a.work).encode()).hexdigest(),"key":"room:!old"})
  a.create()
  self.assertEqual(a.m["rows"][0]["new"],"!already")
  self.assertFalse(any(c.args[1]=="/createRoom" for c in a.mx.call_args_list))
if __name__=="__main__":unittest.main()

