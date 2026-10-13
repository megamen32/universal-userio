#!/usr/bin/env python3
"""Resumable owner-authorized Telegram portal migration; never call Telegram APIs."""
import argparse,hashlib,json,os,pathlib,shutil,sqlite3,subprocess,time,urllib.request,urllib.parse,urllib.error
import yaml
MAX_ROOMS=256
LIMIT=4*1024*1024
Q=lambda x:urllib.parse.quote(str(x),safe="")
class Migration:
 def __init__(self,root,work):
  self.root=pathlib.Path(root);self.work=pathlib.Path(work);self.work.mkdir(parents=True,exist_ok=True,mode=0o700)
  os.chmod(self.work,0o700);self.path=self.work/"manifest.json"
  self.data=self.root/"mautrix-telegram/data";self.dbpath=self.data/"mautrix-telegram.db"
  self.cfg=yaml.safe_load((self.data/"config.yaml").read_text())
  self.app=yaml.safe_load((self.data/"registration.yaml").read_text())
  self.puppet=yaml.safe_load((self.root/"data/mautrix-doublepuppet-registration.yaml").read_text())
  d=sqlite3.connect(self.dbpath);d.row_factory=sqlite3.Row
  owners=[r[0] for r in d.execute("select distinct user_mxid from user_login")]
  if owners!=["@bezrabotnyi:chat.bezrabotnyi.com"]:raise RuntimeError("unexpected Telegram account owner")
  self.owner=owners[0]
  self.personal=d.execute("select space_room from user_login where user_mxid=?",(self.owner,)).fetchone()[0];d.close()
  self.bot="@telegrambot:chat.bezrabotnyi.com";self.domain=self.owner.split(":",1)[1]
  self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
  self.m=json.loads(self.path.read_text()) if self.path.exists() else None
  if self.m and self.m["owner"]!=self.owner:raise RuntimeError("manifest owner changed")
 def mx(self,method,path,body=None,user=None,missing=False):
  user=user or self.bot;token=self.puppet["as_token"] if user==self.owner else self.app["as_token"]
  req=urllib.request.Request("http://192.168.2.75:8008/_matrix/client/v3"+path+("&" if "?" in path else "?")+"user_id="+Q(user),
   method=method,data=None if body is None else json.dumps(body).encode(),
   headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"})
  try:
   with self.opener.open(req,timeout=15) as r:
    data=r.read(LIMIT+1)
    if len(data)>LIMIT:raise RuntimeError("Matrix response exceeds bound")
    return json.loads(data)
  except urllib.error.HTTPError as e:
   if missing and e.code==404:return None
   raise
 def state(self,room,t,key="",body=None,user=None):
  return self.mx("GET" if body is None else "PUT","/rooms/"+Q(room)+"/state/"+Q(t)+"/"+Q(key),body,user,True)
 def save(self):
  text=json.dumps(self.m,ensure_ascii=False,indent=2)
  if len(text.encode())>32*1024*1024:raise RuntimeError("manifest exceeds reviewed disk bound")
  temp=self.path.with_suffix(".new");temp.write_text(text);os.chmod(temp,0o600);temp.replace(self.path)
 def backup(self,name):
  if self.dbpath.stat().st_size>256*1024*1024:raise RuntimeError("bridge backup exceeds reviewed bound")
  dest=self.work/name
  if not dest.exists():
   src=sqlite3.connect(self.dbpath);out=sqlite3.connect(dest);src.backup(out);out.close();src.close();os.chmod(dest,0o600)
 def inventory(self):
  if self.m:raise RuntimeError("inventory already exists; reuse manifest")
  assert self.cfg["bridge"]["bridge_matrix_leave"] is False
  assert self.cfg["backfill"]["enabled"] is False
  d=sqlite3.connect(self.dbpath);d.row_factory=sqlite3.Row
  joined=self.mx("GET","/joined_rooms",user=self.owner)["joined_rooms"]
  portals=[dict(r) for r in d.execute("select * from portal where mxid is not null")]
  if len(portals)>MAX_ROOMS:raise RuntimeError("portal inventory exceeds bound")
  tg={r["mxid"] for r in portals};rows=[];spaces=[]
  for row in portals:
   if row["room_type"]=="space":
    spaces.append({"id":row["id"],"receiver":row["receiver"],"room":row["mxid"],"name":row["name"]});continue
   if row["mxid"] not in joined:continue
   state=self.mx("GET","/rooms/"+Q(row["mxid"])+"/state")
   enc=next((x["content"] for x in state if x["type"]=="m.room.encryption" and x["state_key"]==""),None)
   if not enc:continue
   rows.append({"portal":row,"old":row["mxid"],"state":state,"new":None,"finished":False})
   if len(rows)%20==0:print(json.dumps({"stage":"inventory","rooms":len(rows)}),flush=True)
  ordinary={}
  management=d.execute("select management_room from user where mxid=?",(self.owner,)).fetchone()[0]
  for room in joined:
   if room not in tg and room not in {management,self.personal}:
    ordinary[room]=self.state(room,"m.room.encryption",user=self.owner)
  d.close();self.backup("bridge-before.sqlite3");shutil.copy2(self.data/"config.yaml",self.work/"config-before.yaml")
  os.chmod(self.work/"config-before.yaml",0o600)
  self.m={"owner":self.owner,"personal":self.personal,"rows":rows,"spaces":spaces,"ordinary_encryption":ordinary,
   "stage":"inventoried","archive":None,"archive_spaces":{}}
  self.save();print(json.dumps({"stage":"inventoried","encrypted_rooms":len(rows),"spaces_preserved":len(spaces),"ordinary_rooms":len(ordinary)}),flush=True)
 def create(self):
  if self.m["stage"] not in {"inventoried","created"}:raise RuntimeError("invalid create stage")
  retained={"m.room.name","m.room.topic","m.room.avatar","m.room.history_visibility","m.room.join_rules","m.room.guest_access",
   "m.bridge","uk.half-shot.bridge","io.element.functional_members","com.beeper.room_features","m.space.parent"}
  for i,r in enumerate(self.m["rows"]):
   if not r["new"]:
    state=[{"type":e["type"],"state_key":e["state_key"],"content":e["content"]} for e in r["state"] if e["type"] in retained]
    power=next(e["content"] for e in r["state"] if e["type"]=="m.room.power_levels")
    power=json.loads(json.dumps(power));power.setdefault("users",{})[self.bot]=9001
    power.setdefault("events",{})["m.room.encryption"]=9001
    room=self.ensure_room("room:"+r["old"],{"visibility":"private","preset":"private_chat",
      "is_direct":r["portal"]["room_type"]=="dm","invite":[self.owner],
      "creation_content":{"m.federate":False},"initial_state":state,"power_level_content_override":power})
    r["new"]=room;self.save()
   self.mx("POST","/rooms/"+Q(r["new"])+"/join",{},self.owner)
   if not r.get("ghosts_joined"):
    members=[e["state_key"] for e in r["state"] if e["type"]=="m.room.member" and e["content"].get("membership")=="join"
      and e["state_key"].startswith("@telegram_") and e["state_key"].endswith(":"+self.domain)]
    if len(members)>512:raise RuntimeError("ghost membership exceeds reviewed bound")
    for user in members:
     membership=self.state(r["new"],"m.room.member",user)
     if not membership or membership.get("membership")!="join":
      self.mx("POST","/rooms/"+Q(r["new"])+"/invite",{"user_id":user})
      self.mx("POST","/rooms/"+Q(r["new"])+"/join",{},user)
    r["ghosts_joined"]=True;self.save()
   if (i+1)%10==0:print(json.dumps({"stage":"create","rooms":i+1,"total":len(self.m["rows"])}),flush=True)
  self.m["stage"]="created";self.save()
 def docker(self,*args):
  subprocess.run(["docker",*args,"mautrix-telegram"],check=True,timeout=35,env=dict(os.environ,GOMAXPROCS="2"),stdout=subprocess.DEVNULL)
 def cutover(self):
  if self.m["stage"]=="cutover":return
  if self.m["stage"] not in {"created","detached"}:raise RuntimeError("new rooms must be created first")
  if self.m["stage"]=="created":
   for r in self.m["rows"]:
    if self.state(r["new"],"m.room.encryption"):raise RuntimeError("new room became encrypted")
   self.docker("stop","-t","20")
   try:
    self.backup("bridge-at-cutover.sqlite3")
    d=sqlite3.connect(self.dbpath);d.execute("pragma foreign_keys=on")
    with d:
     for r in self.m["rows"]:
      p=r["portal"];key=(p["bridge_id"],p["id"],p["receiver"])
      current=d.execute("select mxid from portal where bridge_id=? and id=? and receiver=?",key).fetchone()
      if not current or current[0] not in {r["old"],r["new"]}:raise RuntimeError("portal changed during preparation")
     for r in self.m["rows"]:
      p=r["portal"];key=(p["bridge_id"],p["id"],p["receiver"])
      d.execute("delete from reaction where bridge_id=? and room_id=? and room_receiver=?",key)
      d.execute("delete from message where bridge_id=? and room_id=? and room_receiver=?",key)
      d.execute("delete from backfill_task where bridge_id=? and portal_id=? and portal_receiver=?",key)
      d.execute("update portal set mxid=?,in_space=0 where bridge_id=? and id=? and receiver=?",(r["new"],*key))
      d.execute("update user_portal set in_space=0,last_read=null where bridge_id=? and portal_id=? and portal_receiver=?",key)
    d.close()
    cfg=yaml.safe_load((self.data/"config.yaml").read_text())
    cfg["encryption"]["default"]=False;cfg["encryption"]["require"]=False
    # Existing encrypted management/history rooms keep their crypto support.
    cfg["encryption"]["allow"]=True
    (self.data/"config.yaml").write_text(yaml.safe_dump(cfg,allow_unicode=True,sort_keys=False))
    self.m["stage"]="detached";self.save()
   finally:self.docker("start")
  else:self.docker("start")
  self.m["stage"]="cutover";self.save();print(json.dumps({"stage":"cutover","rooms":len(self.m["rows"])}),flush=True)
 def ensure_room(self,key,payload):
  marker=hashlib.sha256(str(self.work).encode()).hexdigest()
  if not hasattr(self,"existing"):
   self.existing={}
   joined=self.mx("GET","/joined_rooms")["joined_rooms"]
   if len(joined)>1024:raise RuntimeError("bot joined room recovery exceeds bound")
   for room in joined:
    identity=self.state(room,"io.userio.telegram_migration")
    if identity and identity.get("migration")==marker:self.existing[identity["key"]]=room
  if key in self.existing:return self.existing[key]
  initial=list(payload.get("initial_state",[]))
  initial.append({"type":"io.userio.telegram_migration","state_key":"","content":{"migration":marker,"key":key}})
  room=self.mx("POST","/createRoom",dict(payload,initial_state=initial))["room_id"]
  self.existing[key]=room
  return room
 def ensure_space(self,key,name):
  room=self.ensure_room("space:"+key,{"name":name,"visibility":"private","preset":"private_chat",
   "creation_content":{"type":"m.space","m.federate":False},"invite":[self.owner]})
  self.mx("POST","/rooms/"+Q(room)+"/join",{},self.owner);return room
 def link(self,parent,child,order=None):
  content={"via":[self.domain],"suggested":True}
  if order:content["order"]=order
  self.state(parent,"m.space.child",child,content)
  self.state(child,"m.space.parent",parent,{"via":[self.domain],"canonical":True})
 def finish(self):
  if self.m["stage"] not in {"cutover","finished"}:raise RuntimeError("cutover must finish first")
  if not self.m["archive"]:
   self.m["archive"]=self.ensure_space("root","Telegram · история до 14.10.2026");self.save()
  self.link(self.personal,self.m["archive"],"zz-history")
  spaces={s["room"]:s for s in self.m["spaces"]}
  for i,r in enumerate(self.m["rows"]):
   if r["finished"]:continue
   parents=[e["state_key"] for e in r["state"] if e["type"]=="m.space.parent" and e["content"].get("via")]
   expected=(r["portal"]["parent_id"],r["portal"]["parent_receiver"])
   actual=next((s["room"] for s in self.m["spaces"] if (s["id"],s["receiver"])==expected),None)
   if actual and actual not in parents:parents.append(actual)
   if not parents:parents=[self.personal]
   for parent in parents:
    if parent!=self.personal and parent not in spaces:
     continue
    self.state(parent,"m.space.child",r["old"],{})
    self.state(r["old"],"m.space.parent",parent,{})
    self.link(parent,r["new"])
   parent=actual or self.personal
   if parent==self.personal:history=self.m["archive"]
   else:
    if parent not in self.m["archive_spaces"]:
     archive=self.ensure_space(parent,spaces[parent]["name"]+" · история")
     self.m["archive_spaces"][parent]=archive;self.save();self.link(self.m["archive"],archive)
    history=self.m["archive_spaces"][parent]
   self.link(history,r["old"])
   title=next((e["content"].get("name") for e in r["state"] if e["type"]=="m.room.name"),r["portal"]["name"])
   self.state(r["old"],"m.room.name","",{"name":title+" · история"})
   self.state(r["old"],"m.room.topic","",{"topic":"История Telegram до 14.10.2026. Продолжение: https://matrix.to/#/"+r["new"]})
   for e in r["state"]:
    if e["type"] in {"m.bridge","uk.half-shot.bridge"}:self.state(r["old"],e["type"],e["state_key"],{})
   power=next(e["content"] for e in r["state"] if e["type"]=="m.room.power_levels")
   power=json.loads(json.dumps(power))
   power.update(events_default=100,state_default=100,users_default=0,invite=100,kick=100,ban=100,redact=100)
   power["users"]={u:(9001 if u==self.bot else 0) for u in power.get("users",{})};power["users"][self.bot]=9001
   power["events"]={k:max(v,100) for k,v in power.get("events",{}).items()}
   self.state(r["old"],"m.room.power_levels","",power)
   self.state(r["old"],"m.room.tombstone","",{"body":"Переписка Telegram продолжается в новой комнате без шифрования. Эта комната сохранена для истории.","replacement_room":r["new"]})
   r["finished"]=True;self.save()
   if (i+1)%10==0:print(json.dumps({"stage":"archive","rooms":i+1,"total":len(self.m["rows"])}),flush=True)
  direct=self.mx("GET","/user/"+Q(self.owner)+"/account_data/m.direct",user=self.owner,missing=True) or {}
  replace={r["old"]:r["new"] for r in self.m["rows"]}
  new={k:list(dict.fromkeys(replace.get(v,v) for v in values)) for k,values in direct.items()}
  self.mx("PUT","/user/"+Q(self.owner)+"/account_data/m.direct",new,self.owner)
  self.m["stage"]="finished";self.save()
 def verify(self):
  d=sqlite3.connect(self.dbpath);errors=[]
  for r in self.m["rows"]:
   p=r["portal"];actual=d.execute("select mxid,parent_id,parent_receiver from portal where bridge_id=? and id=? and receiver=?",(p["bridge_id"],p["id"],p["receiver"])).fetchone()
   if actual!=(r["new"],p["parent_id"],p["parent_receiver"]):errors.append("portal mismatch")
   if self.state(r["new"],"m.room.encryption"):errors.append("new encryption")
   oldenc=next(e["content"] for e in r["state"] if e["type"]=="m.room.encryption")
   if self.state(r["old"],"m.room.encryption")!=oldenc:errors.append("old encryption changed")
   for room in [r["new"],r["old"]]:
    if self.state(room,"m.room.member",self.owner).get("membership")!="join":errors.append("owner missing")
   if (self.state(r["old"],"m.room.tombstone") or {}).get("replacement_room")!=r["new"]:errors.append("history link missing")
   parent=next((s["room"] for s in self.m["spaces"] if (s["id"],s["receiver"])==(p["parent_id"],p["parent_receiver"])),self.personal)
   if not (self.state(parent,"m.space.child",r["new"]) or {}).get("via"):errors.append("topic child missing")
   archive=self.m["archive_spaces"].get(parent,self.m["archive"])
   if not (self.state(archive,"m.space.child",r["old"]) or {}).get("via"):errors.append("history child missing")
  for s in self.m["spaces"]:
   row=d.execute("select mxid from portal where id=? and receiver=?",(s["id"],s["receiver"])).fetchone()
   if not row or row[0]!=s["room"]:errors.append("forum space changed")
  for room,encryption in self.m["ordinary_encryption"].items():
   if self.state(room,"m.room.encryption",user=self.owner)!=encryption:errors.append("ordinary encryption changed")
  d.close()
  report={"stage":"verified" if not errors else "failed","new_unencrypted":len(self.m["rows"]),"history_preserved":len(self.m["rows"]),
   "spaces_unchanged":len(self.m["spaces"]),"history_subspaces":len(self.m["archive_spaces"]),"ordinary_unchanged":len(self.m["ordinary_encryption"]),"errors":errors}
  (self.work/"verification.json").write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
  if errors:raise RuntimeError("migration consumer verification failed")
def main():
 p=argparse.ArgumentParser();p.add_argument("--root",required=True);p.add_argument("--work",required=True);p.add_argument("stage",choices=["inventory","create","cutover","finish","verify"]);a=p.parse_args()
 getattr(Migration(a.root,a.work),a.stage)()
if __name__=="__main__":main()

