#!/usr/bin/env python3
"""Keep known work forums visible; never change Telegram membership.

Run on the Matrix host. Seed only IDs read from Telegram's native topic
catalog. Future topic portal stubs made by mautrix are promoted for these
same explicitly selected forums. No new Telegram client or group is created.
"""
import argparse,hashlib,json,os,pathlib,sqlite3,subprocess,time,urllib.request,urllib.parse
import yaml

def main():
    p=argparse.ArgumentParser();p.add_argument("--root",required=True);p.add_argument("--catalog",required=True)
    a=p.parse_args();root=pathlib.Path(a.root);catalog=json.loads(pathlib.Path(a.catalog).read_text())
    if not 1<=len(catalog.get("forums",[]))<=16 or sum(len(f["topics"]) for f in catalog["forums"])>256:
        raise RuntimeError("work forum catalog exceeds reviewed bound")
    data=root/"mautrix-telegram/data";db_path=data/"mautrix-telegram.db"
    db=sqlite3.connect(str(db_path),timeout=5);db.row_factory=sqlite3.Row
    user=db.execute("SELECT user_mxid,id,space_room FROM user_login WHERE user_mxid=?",(catalog["owner"],)).fetchone()
    if not user:raise RuntimeError("selected Telegram login unavailable")
    management=db.execute("SELECT management_room FROM user WHERE mxid=?",(catalog["owner"],)).fetchone()[0]
    cfg_path=data/"config.yaml";cfg=yaml.safe_load(cfg_path.read_text())
    assert cfg["bridge"]["bridge_matrix_leave"] is False
    if cfg["bridge"]["portal_create_filter"]["mode"]!="allow":raise RuntimeError("explicit allow policy required")
    allowed=cfg["bridge"]["portal_create_filter"]["list"]
    allowed_ids={v if isinstance(v,str) else v["id"] for v in allowed}
    changed=False;created_stubs=0
    with db:
        for forum in catalog["forums"]:
            base="channel:"+forum["id"]
            template=db.execute("SELECT * FROM portal WHERE id=? AND receiver=''",(base,)).fetchone()
            parent=db.execute("SELECT * FROM portal WHERE id=? AND receiver=''",(base+":-1",)).fetchone()
            if not parent or not parent["mxid"]:raise RuntimeError("forum parent room unavailable")
            if not template:template=parent
            topics={str(x["id"]):x["title"] for x in forum["topics"]}
            # Keep future topics whose actual remote updates already produced
            # portal stubs, even if they were not present in the initial catalog.
            for r in db.execute("SELECT id FROM portal WHERE id LIKE ? AND receiver=''",(base+":%",)):
                topic=r["id"].split(":")[-1]
                if topic.isdigit() and int(topic)>1:topics.setdefault(topic,"")
            for topic,title in topics.items():
                target=base if topic=="1" else base+":"+topic
                if target not in allowed_ids:
                    allowed.append({"id":target,"receiver":""});allowed_ids.add(target);changed=True
                if db.execute("SELECT 1 FROM portal WHERE id=? AND receiver=''",(target,)).fetchone():continue
                row=dict(template);row.update(id=target,receiver="",mxid=None,parent_id=base+":-1",parent_receiver="",
                    name=title,room_type="",name_set=0,avatar_set=0,topic_set=0,in_space=0,
                    metadata=json.dumps({"is_supergroup":True,"is_forum_general":topic=="1",
                        "last_sync":0,"sponsored_message_poll_ts":0}))
                cols=list(row);db.execute("INSERT INTO portal ("+",".join(cols)+") VALUES ("+",".join("?" for _ in cols)+")",[row[k] for k in cols])
                created_stubs+=1
    if changed:
        cfg_path.write_text(yaml.safe_dump(cfg,allow_unicode=True,sort_keys=False))
    stamp=root/".tmp/userio-forums-filter-applied"
    digest=hashlib.sha256(json.dumps(cfg["bridge"]["portal_create_filter"],sort_keys=True).encode()).hexdigest()
    if changed or created_stubs or not stamp.exists() or stamp.read_text()!=digest:
        subprocess.run(["docker","restart","-t","15","mautrix-telegram"],check=True,stdout=subprocess.DEVNULL,timeout=25,
            env=dict(os.environ,GOMAXPROCS="2"))
        stamp.write_text(digest)
    app=yaml.safe_load((data/"registration.yaml").read_text())
    puppet=yaml.safe_load((root/"data/mautrix-doublepuppet-registration.yaml").read_text())
    base_url="http://192.168.2.75:8008"
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def mx(method,path,body,owner=False):
        token=puppet["as_token"] if owner else app["as_token"]
        who=catalog["owner"] if owner else "@telegrambot:chat.bezrabotnyi.com"
        q="user_id="+urllib.parse.quote(who,safe="")
        request=urllib.request.Request(base_url+"/_matrix/client/v3"+path+"?"+q,method=method,
            data=json.dumps(body).encode(),headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"})
        return json.load(opener.open(request,timeout=10))
    quote=lambda s:urllib.parse.quote(s,safe="")
    requested=0
    for forum in catalog["forums"]:
        base="channel:"+forum["id"]
        parent=db.execute("SELECT mxid FROM portal WHERE id=? AND receiver=''",(base+":-1",)).fetchone()[0]
        # Explicit personal navigation: a linked Telegram Community need not
        # have its own Matrix portal for this work forum to remain reachable.
        mx("PUT","/rooms/"+quote(user["space_room"])+"/state/m.space.child/"+quote(parent),
            {"via":["chat.bezrabotnyi.com"],"suggested":True})
        mx("PUT","/rooms/"+quote(parent)+"/state/m.space.parent/"+quote(user["space_room"]),
            {"via":["chat.bezrabotnyi.com"],"canonical":True})
        for r in db.execute("SELECT id FROM portal WHERE receiver='' AND mxid IS NULL AND (id=? OR id LIKE ?)",(base,base+":%")):
            if r["id"].endswith(":-1"):continue
            # An internal bridge command is safe to retry in a later time
            # bucket; create-portal itself checks whether the room exists.
            txn="forum_"+hashlib.sha256((r["id"]+"|"+str(int(time.time())//300)).encode()).hexdigest()
            mx("PUT","/rooms/"+quote(management)+"/send/m.room.message/"+txn,
                {"msgtype":"m.text","body":"!tg create-portal "+user["id"]+" "+r["id"]},owner=True)
            requested+=1
    print(json.dumps({"forums":len(catalog["forums"]),"new_stubs":created_stubs,"commands":requested,"filter_updated":changed}))

if __name__=="__main__":main()
