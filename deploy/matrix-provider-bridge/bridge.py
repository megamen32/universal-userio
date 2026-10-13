"""Matrix application service for owner-linked MAX and VK accounts.

UserIO owns provider credentials and delivery claims. This process owns Matrix
portals, ghosts and durable transaction/event mapping. It never changes remote
membership when a person leaves Matrix.
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import logging
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LOG = logging.getLogger("userio.matrix.bridge")
MAX_FILE = 2 * 1024 * 1024
Q = lambda s: urllib.parse.quote(str(s), safe="")
HASH = lambda s: hashlib.sha256(str(s).encode()).hexdigest()
BRIDGE_FIELD = "io.userio.bridge"

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("redirect refused")

class Http:
    def __init__(self, base, token):
        self.base, self.token = base.rstrip("/"), token
        self.open = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open

    def request(self, method, path, payload=None, *, raw=None, mime="application/json", limit=4*1024*1024):
        data = raw if raw is not None else None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method,
            headers={"Authorization": "Bearer " + self.token, "Content-Type": mime})
        with self.open(req, timeout=20) as response:
            body = response.read(limit+1)
            if len(body)>limit:
                raise ValueError("response exceeds bound")
            if raw is not None or response.headers.get_content_type() != "application/json":
                return body, response.headers.get_content_type()
            return json.loads(body)

class Bridge:
    def __init__(self, config, *, mx=None, userio=None):
        self.config = config
        self.owner = config["owner_mxid"]
        self.domain = config["server_name"]
        self.bot = "@_userio_bridgebot:" + self.domain
        self.mx = mx or Http(config["homeserver"], config["as_token"])
        self.uio = userio or Http(config["userio_url"], config["userio_token"])
        self.owner_http = Http(config["homeserver"], config["owner_token"]) if config.get("owner_token") else None
        self.db = sqlite3.connect(config["database"], check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA max_page_count=131072")
        self.db.execute("PRAGMA journal_size_limit=8388608")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS portals (
                cid TEXT PRIMARY KEY, room TEXT UNIQUE NOT NULL, source TEXT NOT NULL,
                account TEXT NOT NULL, peer TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS mappings (
                cid TEXT NOT NULL, mid TEXT NOT NULL, event TEXT NOT NULL,
                sender TEXT NOT NULL, body_hash TEXT NOT NULL, PRIMARY KEY(cid,mid));
            CREATE TABLE IF NOT EXISTS jobs (
                event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                receipt TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS transactions (id TEXT PRIMARY KEY, digest TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS values_store (key TEXT PRIMARY KEY,value TEXT NOT NULL);
        """)
        self.registered = set()
        self.joined = set()
        self.last_poll = 0.0
        self.last_error = ""

    def matrix(self, method, path, payload=None, *, user=None, **kwargs):
        suffix = ("&" if "?" in path else "?")+"user_id="+Q(user or self.bot)
        transport = self.owner_http if user == self.owner and self.owner_http else self.mx
        return transport.request(method, path+suffix, payload, **kwargs)

    def client(self, method, path, payload=None, **kwargs):
        return self.matrix(method, "/_matrix/client/v3"+path, payload, **kwargs)

    def register(self, user, name):
        if user == self.owner or user in self.registered:
            return
        local = user.split(":",1)[0][1:]
        try:
            self.mx.request("POST", "/_matrix/client/v3/register",
                {"type":"m.login.application_service","username":local,"inhibit_login":True})
        except urllib.error.HTTPError as error:
            if error.code != 400:
                raise
            result = json.loads(error.read(65536))
            if result.get("errcode") != "M_USER_IN_USE":
                raise ValueError("Matrix user registration refused")
        self.client("PUT", "/profile/"+Q(user)+"/displayname", {"displayname": name}, user=user)
        self.registered.add(user)

    def ghost(self, source, account, sender, name):
        user = "@_userio_"+source+"_"+HASH(account+"|"+sender)[:24]+":"+self.domain
        self.register(user, name or sender or source.upper())
        return user

    def state(self, key, value=None):
        with self.lock, self.db:
            if value is not None:
                self.db.execute("INSERT OR REPLACE INTO values_store VALUES (?,?)", (key, str(value)))
            row = self.db.execute("SELECT value FROM values_store WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def space(self, source):
        existing = self.state("space:"+source)
        if existing:
            return existing
        room = self.ensure_room("_userio_"+source+"_space", {"name":source.upper(),
            "creation_content":{"type":"m.space","m.federate":False},"preset":"private_chat",
            "invite":[self.owner],"initial_state":[{"type":"m.room.history_visibility","state_key":"",
            "content":{"history_visibility":"joined"}}]})
        self.state("space:"+source, room)
        return room

    def ensure_room(self, alias, payload):
        full = "#"+alias+":"+self.domain
        try:
            return self.client("GET", "/directory/room/"+Q(full))["room_id"]
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
        return self.client("POST", "/createRoom", dict(payload, room_alias_name=alias))["room_id"]

    def selected(self, conv):
        account = self.config["accounts"].get(conv.get("account_ref"))
        return bool(account and conv.get("source")==account["provider"]
            and conv.get("conversation_kind") in {"direct","group"}
            and str(conv.get("peer_id")) not in account.get("excluded_peers", []))

    def portal(self, conv):
        cid = conv["id"]
        row = self.db.execute("SELECT * FROM portals WHERE cid=?", (cid,)).fetchone()
        if row:
            if (row["source"],row["account"],row["peer"]) != (conv["source"],conv["account_ref"],conv["peer_id"]):
                raise ValueError("portal routing changed")
            return dict(row)
        source, account = conv["source"], conv["account_ref"]
        space = self.space(source)
        title = conv.get("display_name") or conv.get("sender") or conv["peer_id"]
        room = self.ensure_room("_userio_"+source+"_"+HASH(account+"|"+conv["peer_id"])[:24], {
            "name":source.upper()+" · "+title,
            "topic":"Переписка "+source.upper()+". Сообщения уходят через ваш подключённый аккаунт. Выход из Matrix не меняет членство в "+source.upper()+".",
            "preset":"private_chat", "invite":[self.owner], "creation_content":{"m.federate":False},
            "initial_state":[
                {"type":"m.bridge","state_key":"io.userio."+source,"content":{
                    "bridgebot":self.bot,"creator":self.bot,
                    "protocol":{"id":source,"displayname":source.upper()},
                    "network":{"id":account,"displayname":source.upper()},
                    "channel":{"id":conv["peer_id"],"displayname":title},
                    BRIDGE_FIELD:{"conversation_id":cid,"account_ref":account}}},
                {"type":"m.room.history_visibility","state_key":"","content":{"history_visibility":"joined"}},
                {"type":"m.space.parent","state_key":space,"content":{"via":[self.domain],"canonical":True}}
            ]})
        self.client("PUT", "/rooms/"+Q(space)+"/state/m.space.child/"+Q(room),
            {"via":[self.domain],"suggested":True})
        if self.owner_http:
            self.client("POST", "/rooms/"+Q(room)+"/join", {}, user=self.owner)
        with self.lock,self.db:
            self.db.execute("INSERT INTO portals(cid,room,source,account,peer) VALUES (?,?,?,?,?)",
                (cid,room,source,account,conv["peer_id"]))
        LOG.info("Created %s portal %s", source, room)
        return dict(self.db.execute("SELECT * FROM portals WHERE cid=?", (cid,)).fetchone())

    def send_matrix(self, room, content, txn, user=None):
        return self.client("PUT", "/rooms/"+Q(room)+"/send/m.room.message/"+Q(txn),
            content, user=user)["event_id"]

    def notice(self, room, text, txn):
        return self.send_matrix(room, {"msgtype":"m.notice","body":text}, txn)

    def import_message(self, portal, conv, message):
        cid, mid = conv["id"], message["message_id"]
        body = str(message.get("body") or "")
        current = HASH(json.dumps([body,message.get("attachments",[])], sort_keys=True))
        old = self.db.execute("SELECT * FROM mappings WHERE cid=? AND mid=?", (cid,mid)).fetchone()
        # UserIO sends are projected back with their provider ID; map them to
        # the human Matrix event instead of producing a second visible bubble.
        if message.get("direction") == "outgoing":
            suffix = mid.rsplit(":",1)[-1].rsplit("-",1)[-1]
            sent = self.db.execute("SELECT event_id FROM jobs WHERE status='sent' AND "
                "json_extract(payload,'$.room_id')=? AND (receipt=? OR receipt LIKE ?) LIMIT 1", (portal["room"],suffix, "%:"+suffix)).fetchone()
            if sent:
                if old and old["event"] != sent["event_id"]:
                    self.client("PUT", "/rooms/"+Q(portal["room"])+"/redact/"+Q(old["event"])+"/"+Q("echo_"+HASH(cid+"|"+mid)),
                        {"reason":"Убран повтор подтверждённого сообщения моста"},user=old["sender"])
                with self.lock,self.db:
                    self.db.execute("INSERT INTO mappings VALUES (?,?,?,?,?)",
                        (cid,mid,sent["event_id"],self.owner,current))
                return
        if old and old["body_hash"] == current:
            return
        sender = old["sender"] if old else self.owner if message.get("direction") == "outgoing" and self.owner_http else self.ghost(portal["source"], portal["account"],
            "owner" if message.get("direction")=="outgoing" else str(message.get("sender") or portal["peer"]),
            "Вы · "+portal["source"].upper() if message.get("direction")=="outgoing"
            else message.get("sender_name") or conv.get("display_name") or str(message.get("sender") or conv["sender"]))
        if not old and sender != self.owner and (portal["room"],sender) not in self.joined:
            try:
                member=self.client("GET","/rooms/"+Q(portal["room"])+"/state/m.room.member/"+Q(sender))
            except urllib.error.HTTPError as error:
                if error.code!=404:raise
                member={}
            if member.get("membership")!="join":
                self.client("POST","/rooms/"+Q(portal["room"])+"/invite",{"user_id":sender})
                self.client("POST","/rooms/"+Q(portal["room"])+"/join",{},user=sender)
            self.joined.add((portal["room"],sender))
        content = {"msgtype":"m.text","body":body or "[Вложение]",
            BRIDGE_FIELD:{"account_ref":portal["account"],"message_id":mid}}
        if old:
            content = dict(content, body="* "+content["body"],
                **{"m.new_content":content.copy(),
                "m.relates_to":{"rel_type":"m.replace","event_id":old["event"]}})
        event = self.send_matrix(portal["room"],content,"in_"+HASH(cid+"|"+mid+"|"+current),sender)
        with self.lock,self.db:
            self.db.execute("INSERT OR REPLACE INTO mappings VALUES (?,?,?,?,?)",
                (cid,mid,old["event"] if old else event,sender,current))
        if message.get("attachments") and not old:
            self.import_media(portal, cid, mid, message, sender)

    def import_media(self, portal, cid, mid, message, sender):
        # Current UserIO's HTTP binary seam supports native VK photo/document.
        # MAX binary transfer is kept explicitly unavailable, never a fake mxc.
        if portal["source"] != "vk":
            return
        try:
            result = self.uio.request("GET","/v1/conversations/"+Q(cid)+"/media/"+Q(mid)+"/raw",limit=MAX_FILE)
            data,mime=result
            uploaded = self.matrix("POST","/_matrix/media/v3/upload?filename="+Q("attachment"),
                raw=data,mime=mime)
            # Upload is JSON even when request bytes are raw.
            if isinstance(uploaded,tuple):
                uploaded=json.loads(uploaded[0])
            kind = "m.image" if mime.startswith("image/") else "m.file"
            self.send_matrix(portal["room"], {"msgtype":kind,"body":message.get("attachments",[{}])[0].get("filename") or "Вложение VK",
                "url":uploaded["content_uri"],"info":{"mimetype":mime,"size":len(data)}},
                "media_"+HASH(cid+"|"+mid),sender)
        except Exception:
            LOG.warning("VK media unavailable for portal %s", portal["room"])

    def reconcile_approved(self, portal, conv):
        """Restore confirmed outcomes after a lost HTTP response, without sending."""
        uid=self.config.get("userio_user_id")
        if not uid:return
        drafts={d["id"]:d for d in conv.get("drafts",[]) if d.get("status")=="approved" and d.get("outbox_receipt")}
        if not drafts:return
        jobs=self.db.execute("SELECT event_id FROM jobs WHERE status='failed' AND json_extract(payload,'$.room_id')=?",
            (portal["room"],)).fetchall()
        with self.lock,self.db:
            for job in jobs:
                draft_id="draft_web_"+HASH(uid+":mx_"+HASH(job["event_id"]))
                draft=drafts.get(draft_id)
                if draft:
                    self.db.execute("UPDATE jobs SET status='sent',receipt=?,error='' WHERE event_id=?",
                        (draft["outbox_receipt"],job["event_id"]))

    def poll(self):
        self.register(self.bot,"MAX и VK · UserIO")
        for account,settings in self.config["accounts"].items():
            listing = self.uio.request("GET","/v1/conversations?account_ref="+Q(account))["conversations"]
            if len(listing)>200:
                raise ValueError("account portal limit exceeded")
            for summary in listing:
                if summary.get("account_ref") != account or summary.get("source") != settings["provider"]:
                    continue
                conv = self.uio.request("GET","/v1/conversations/"+Q(summary["id"]))
                if not self.selected(conv):
                    continue
                portal = self.portal(conv)
                if not portal["active"]:
                    continue
                self.reconcile_approved(portal,conv)
                messages = conv.get("messages", [])
                known = self.db.execute("SELECT 1 FROM mappings WHERE cid=? LIMIT 1",(conv["id"],)).fetchone()
                selected = messages if known else messages[-self.config.get("initial_messages",10):]
                for message in selected:
                    self.import_message(portal,conv,message)
        self.last_poll=time.time()
        self.last_error=""

    def accept_transaction(self, txn, payload):
        if not isinstance(payload,dict) or not isinstance(payload.get("events",[]),list) or len(payload.get("events",[]))>500:
            raise ValueError("invalid transaction")
        digest=HASH(json.dumps(payload,sort_keys=True))
        with self.lock,self.db:
            prior=self.db.execute("SELECT digest FROM transactions WHERE id=?",(txn,)).fetchone()
            if prior:
                if prior[0]!=digest:raise ValueError("transaction conflict")
                return
            if self.db.execute("SELECT count(*) FROM jobs WHERE status='pending'").fetchone()[0]>1000:
                raise ValueError("queue full")
            for event in payload.get("events",[]):
                if not isinstance(event,dict) or event.get("sender")!=self.owner:
                    continue
                if event.get("type") not in {"m.room.member","m.room.message","m.room.encrypted","m.room.encryption"}:
                    continue
                if not isinstance(event.get("event_id"),str) or not isinstance(event.get("room_id"),str):
                    raise ValueError("event identity required")
                self.db.execute("INSERT OR IGNORE INTO jobs(event_id,payload) VALUES (?,?)",
                    (event["event_id"],json.dumps(event)))
            self.db.execute("INSERT INTO transactions VALUES (?,?)",(txn,digest))

    def process(self, job):
        event=json.loads(job["payload"])
        portal=self.db.execute("SELECT * FROM portals WHERE room=?",(event["room_id"],)).fetchone()
        if not portal:
            return "ignored",""
        portal=dict(portal)
        content=event.get("content") or {}
        if event["type"]=="m.room.encryption":
            with self.lock,self.db:
                self.db.execute("UPDATE portals SET active=0 WHERE room=?",(portal["room"],))
            self.notice(portal["room"],"Мост приостановлен: сквозное шифрование этой комнаты пока не поддерживается.","e2ee_"+HASH(event["event_id"]))
            return "ignored",""
        if event["type"]=="m.room.member":
            if event.get("state_key")==self.owner:
                membership=content.get("membership")
                if membership in {"leave","ban","join"}:
                    with self.lock,self.db:
                        self.db.execute("UPDATE portals SET active=? WHERE room=?",
                            (int(membership=="join"),portal["room"]))
            return "ignored",""
        if not portal["active"]:
            return "ignored",""
        if event["type"]=="m.room.encrypted":
            raise ValueError("Эта комната моста поддерживает сообщения без сквозного шифрования.")
        if "state_key" in event:
            return "ignored",""
        if BRIDGE_FIELD in content:
            return "ignored",""
        msgtype=content.get("msgtype")
        relates=content.get("m.relates_to",{})
        if relates.get("rel_type")=="m.replace":
            original=self.db.execute("SELECT * FROM mappings WHERE cid=? AND event=? AND sender=?",
                (portal["cid"],relates.get("event_id"),self.owner)).fetchone()
            if not original:raise ValueError("Исходное сообщение ещё не подтверждено в мессенджере; повторите изменение позже.")
            conv=self.uio.request("GET","/v1/conversations/"+Q(portal["cid"]))
            if not self.selected(conv):raise ValueError("Аккаунт больше не разрешён для этой комнаты.")
            native=next((m for m in conv["messages"] if m["message_id"]==original["mid"] and m["direction"]=="outgoing"),None)
            if not native:raise ValueError("Свое исходное сообщение не найдено.")
            replacement=content.get("m.new_content") or {}
            if replacement.get("msgtype")!="m.text":raise ValueError("Можно изменить только текст.")
            body=str(replacement.get("body") or "")
            # Recovery after an interrupted edit: native readback is already exact.
            if native["body"]!=body:
                self.uio.request("PATCH","/v1/conversations/"+Q(portal["cid"])+"/messages/"+Q(original["mid"]),
                    {"body":body,"expected_body":native["body"]})
            return "edited",""
        body=str(content.get("body") or "")
        request_id="mx_"+HASH(event["event_id"])
        send={"body":body,"request_id":request_id}
        if msgtype=="m.notice":
            return "ignored",""
        if relates.get("m.in_reply_to") and body.startswith(">") and "\n\n" in body:
            body=body.split("\n\n",1)[1]
            send["body"]=body
        if msgtype in {"m.image","m.file"}:
            if portal["source"]!="vk":
                raise ValueError("Передача вложений из Matrix в MAX пока не подключена; отправьте файл в MAX.")
            url=content.get("url","")
            if not re.fullmatch(r"mxc://[^/]+/[^/?#]+",url) or content.get("file"):
                raise ValueError("Нужно незашифрованное вложение Matrix.")
            server,media=url[6:].split("/",1)
            data,mime=self.matrix("GET","/_matrix/client/v1/media/download/"+Q(server)+"/"+Q(media),limit=MAX_FILE)
            filename=Path(body).name[:128] or "attachment"
            upload=self.uio.request("POST","/v1/conversations/"+Q(portal["cid"])+"/uploads",
                {"filename":filename,"mime_type":mime,"data_base64":base64.b64encode(data).decode(),
                 "sha256":hashlib.sha256(data).hexdigest(),"request_id":request_id})
            send["upload_id"]=upload["upload_id"]
            send["body"]=str(content.get("caption") or "")
        elif msgtype not in {"m.text","m.emote"}:
            raise ValueError("Этот тип сообщения пока не передаётся мостом.")
        # Verify the configured account still owns the exact original dialog.
        conv=self.uio.request("GET","/v1/conversations/"+Q(portal["cid"]))
        if not self.selected(conv) or (conv["account_ref"],conv["peer_id"])!=(portal["account"],portal["peer"]):
            raise ValueError("Привязка аккаунта изменилась; отправка остановлена.")
        result=self.uio.request("POST","/v1/conversations/"+Q(portal["cid"])+"/send",send)
        if result.get("status") not in {"sent","approved"} or not result.get("receipt"):
            raise ValueError("UserIO не подтвердил отправку. Проверьте исходный мессенджер перед повтором.")
        return "sent",str(result["receipt"])

    def drain(self):
        jobs=self.db.execute("SELECT * FROM jobs WHERE status='pending' ORDER BY rowid LIMIT 50").fetchall()
        for job in jobs:
            try:
                status,receipt=self.process(job)
                with self.lock,self.db:
                    self.db.execute("UPDATE jobs SET status=?,receipt=? WHERE event_id=?",
                        (status,receipt,job["event_id"]))
                if status=="sent":
                    event=json.loads(job["payload"])
                    try:self.client("POST","/rooms/"+Q(event["room_id"])+"/receipt/m.read/"+Q(job["event_id"]),{})
                    except Exception:LOG.warning("Delivery receipt could not be published")
            except Exception as error:
                # UserIO's durable request ID owns uncertain-delivery handling.
                # Keep failed work; never silently replay a possibly sent mutation.
                with self.lock,self.db:
                    self.db.execute("UPDATE jobs SET status='failed',error=? WHERE event_id=?",
                        (type(error).__name__,job["event_id"]))
                event=json.loads(job["payload"])
                public=str(error) if isinstance(error,ValueError) else "Отправка не подтверждена. Проверьте исходный мессенджер перед повтором."
                try:self.notice(event["room_id"],public,"error_"+HASH(job["event_id"]))
                except Exception:LOG.warning("Cannot report failed event %s",job["event_id"])
                LOG.warning("Bridge event failed: %s",type(error).__name__)

    def health(self):
        return {"ok":time.time()-self.last_poll<120 and not self.last_error,
            "last_poll":self.last_poll,"error":self.last_error,
            "portals":self.db.execute("SELECT count(*) FROM portals").fetchone()[0],
            "pending":self.db.execute("SELECT count(*) FROM jobs WHERE status='pending'").fetchone()[0],
            "failed":self.db.execute("SELECT count(*) FROM jobs WHERE status='failed'").fetchone()[0]}

def handler_for(bridge):
    class Handler(BaseHTTPRequestHandler):
        def reply(self,status,payload):
            body=json.dumps(payload).encode()
            self.send_response(status);self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body)
        def authenticated(self):
            parsed=urllib.parse.urlsplit(self.path)
            query=urllib.parse.parse_qs(parsed.query)
            bearer=self.headers.get("Authorization","").removeprefix("Bearer ")
            old=query.get("access_token",[""])[0]
            supplied=bearer or old
            return bool(supplied and hmac.compare_digest(supplied,bridge.config["hs_token"])
                and (not old or hmac.compare_digest(old,bridge.config["hs_token"])))
        def do_GET(self):
            if self.path=="/health":
                return self.reply(200,bridge.health())
            if not self.authenticated():return self.reply(403,{"errcode":"M_FORBIDDEN"})
            self.reply(404,{"errcode":"M_UNRECOGNIZED"})
        def do_POST(self):
            if not self.authenticated():return self.reply(403,{"errcode":"M_FORBIDDEN"})
            path=urllib.parse.urlsplit(self.path).path
            if path=="/_matrix/app/v1/ping":return self.reply(200,{})
            self.reply(404,{"errcode":"M_UNRECOGNIZED"})
        def do_PUT(self):
            if not self.authenticated():return self.reply(403,{"errcode":"M_FORBIDDEN"})
            path=urllib.parse.urlsplit(self.path).path
            if not path.startswith("/_matrix/app/v1/transactions/"):
                return self.reply(404,{"errcode":"M_UNRECOGNIZED"})
            try:
                size=int(self.headers.get("Content-Length","0"))
                if not 0<size<=2*1024*1024:raise ValueError("transaction exceeds bound")
                payload=json.loads(self.rfile.read(size))
                bridge.accept_transaction(path.rsplit("/",1)[1],payload)
                self.reply(200,{})
            except ValueError:self.reply(400,{"errcode":"M_BAD_JSON"})
            except sqlite3.Error:self.reply(503,{"errcode":"M_UNKNOWN"})
        def log_message(self,*args):pass
    return Handler

def main():
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",required=True)
    args=parser.parse_args()
    config=json.loads(Path(args.config).read_text())
    logging.basicConfig(level=logging.INFO)
    bridge=Bridge(config)
    server=ThreadingHTTPServer((config.get("bind","192.168.2.100"),config.get("port",18097)),handler_for(bridge))
    server.daemon_threads=True
    threading.Thread(target=server.serve_forever,daemon=True).start()
    next_poll=0.0
    while True:
        with bridge.lock:
            bridge.drain()
            if time.monotonic()>=next_poll:
                next_poll=time.monotonic()+config.get("poll_seconds",10)
                try:bridge.poll()
                except Exception as error:
                    bridge.last_error=type(error).__name__
                    next_poll=time.monotonic()+30
                    LOG.warning("Provider poll failed: %s",type(error).__name__)
        time.sleep(1)

if __name__=="__main__":main()
