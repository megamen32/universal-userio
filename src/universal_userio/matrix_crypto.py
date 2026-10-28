"""Account/device Matrix crypto and one durable sync stream on the API host.

The existing message and call pollers consume this stream. Voice workers receive
only call-bound media keys over their existing private control connection; they
never open a second sync or a remote crypto database.
"""
from __future__ import annotations

import asyncio
import base64
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import sqlite3
import time
from urllib.parse import quote, urlencode

KEY_EVENT = "io.element.call.encryption_keys"
MEMBER = "org.matrix.msc3401.call.member"


class CryptoIdentityMissing(ValueError):
    """The server device has keys, but their private identity is unavailable."""


def _private_directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise PermissionError("Matrix crypto state must be private")


class AccountCrypto:
    def __init__(self, account, *, root=None, api=None):
        self.account = dict(account)
        self.api = api or self._api
        root = Path(root or os.environ.get("USERIO_CONNECTION_SECRETS_DIR",
                                          "/var/lib/universal-userio/connected-secrets"))
        scope = [self.account.get(k, "") for k in ("user_id", "account_id", "homeserver", "matrix_user_id")]
        if not all(scope):
            raise ValueError("Matrix crypto account ownership is incomplete")
        self.root = root / "matrix-crypto" / hashlib.sha256(json.dumps(scope).encode()).hexdigest()

    def _api(self, method, path, payload=None):
        from .matrix_calls import request_json
        return request_json(self.account["homeserver"] + path,
                            self.account["access_token"], payload, method)

    def transaction(self, action):
        """Hold the exclusive account lifecycle through SDK state and sync commit."""
        _private_directory(self.root)
        fd = os.open(self.root / "owner.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(self._transaction(action))
            finally:
                loop.close()
        finally:
            os.close(fd)

    async def _transaction(self, action):
        from nio import AsyncClient, AsyncClientConfig, SyncResponse
        # SDK debug HTTP logs can contain encrypted key envelopes. Never enable
        # them on the call/control path.
        logging.getLogger("nio").setLevel(logging.WARNING)
        identity = self.api("GET", "/_matrix/client/v3/account/whoami")
        user, device = identity.get("user_id"), identity.get("device_id")
        if user != self.account["matrix_user_id"] or not device:
            raise ValueError("Matrix crypto requires the authenticated device identity")
        if self.account.get("device_id") and device != self.account["device_id"]:
            raise ValueError("Matrix crypto device changed")
        owners = self.root.parent / "devices"
        _private_directory(owners)
        owner_file = owners / hashlib.sha256(json.dumps([self.account["homeserver"], user, device]).encode()).hexdigest()
        try:
            fd = os.open(owner_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            if owner_file.is_symlink() or owner_file.read_text() != self.root.name:
                raise PermissionError("Matrix device already belongs to another account crypto lifecycle")
        else:
            with os.fdopen(fd, "w") as output:
                output.write(self.root.name)
        folder = self.root / hashlib.sha256(device.encode()).hexdigest()
        _private_directory(folder)
        published = self.api("POST", "/_matrix/client/v3/keys/query", {"device_keys": {user: [device]}})
        remote = published.get("device_keys", {}).get(user, {}).get(device, {}).get("keys", {})
        if remote and not (folder / "crypto.db").exists():
            raise CryptoIdentityMissing("Matrix device private identity missing; reconnect as a new device")
        pickle_file = folder / "pickle-key"
        if (folder / "crypto.db").exists() and not pickle_file.exists():
            raise CryptoIdentityMissing("Matrix crypto store pickle key is missing")
        if any((folder / name).is_symlink() for name in ("crypto.db", "stream.db")):
            raise PermissionError("Matrix crypto store must not be a symbolic link")
        if not pickle_file.exists():
            fd = os.open(pickle_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w") as output:
                output.write(secrets.token_urlsafe(48))
        if pickle_file.is_symlink() or pickle_file.stat().st_mode & 0o077:
            raise PermissionError("Matrix pickle key must be private")
        client = AsyncClient(self.account["homeserver"], user, device_id=device, store_path=str(folder),
                             config=AsyncClientConfig(encryption_enabled=True, store_name="crypto.db",
                                                      pickle_key=pickle_file.read_text(),
                                                      request_timeout=15, max_timeouts=1, max_limit_exceeded=1))
        client.restore_login(user, device, self.account["access_token"])
        db = sqlite3.connect(folder / "stream.db")
        try:
            for algorithm, key in (client.olm.account.identity_keys.items() if client.olm else []):
                if remote and remote.get(algorithm + ":" + device) != key:
                    raise CryptoIdentityMissing("Matrix device identity differs from protected store")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS state (room TEXT, type TEXT, skey TEXT, event TEXT,
                    PRIMARY KEY(room,type,skey));
                CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    room TEXT, event_id TEXT UNIQUE, event TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS rtc_keys (room TEXT, sender TEXT, device TEXT, idx INTEGER,
                    key TEXT, ts INTEGER, sender_key TEXT, PRIMARY KEY(room,sender,device,idx));
                CREATE TABLE IF NOT EXISTS pending (event_id TEXT PRIMARY KEY, room TEXT, event TEXT, created REAL);
            """)
            # nio persists crypto, not room membership. Restore only the state
            # needed by SDK encrypted sending, without replaying old timelines.
            rooms = {}
            for room, event in db.execute("SELECT room,event FROM state"):
                rooms.setdefault(room, {"state": {"events": []}, "timeline": {"events": []}})["state"]["events"].append(json.loads(event))
            await client.receive_response(SyncResponse.from_dict({"next_batch": "restore", "rooms": {"join": rooms}}))
            return await action(client, db)
        finally:
            db.close()
            await client.close()
            if client.store:
                client.store.database.close()
            for path in folder.iterdir():
                if path.is_file() and not path.is_symlink():
                    path.chmod(0o600)

    def bootstrap(self):
        async def ready(client, db):
            return {"device_id": client.device_id, "sdk": "matrix-nio"}
        return self.transaction(ready)

    async def _sync(self, client, db):
        from nio import SyncResponse, MegolmEvent
        row = db.execute("SELECT value FROM meta WHERE name='since'").fetchone()
        query = {"timeout": "0", "filter": json.dumps({"room": {"timeline": {"limit": 100}}})}
        if row:
            query["since"] = row[0]
        raw = self.api("GET", "/_matrix/client/v3/sync?" + urlencode(query))
        if not isinstance(raw.get("next_batch"), str):
            raise ValueError("invalid Matrix crypto sync response")
        for room, info in raw.get("rooms", {}).get("join", {}).items():
            timeline = info.get("timeline", {})
            if timeline.get("limited") and row:
                start, history, seen = timeline.get("prev_batch"), [], set()
                while start and start != row[0]:
                    if start in seen or len(seen) >= 16 or len(history) > 1000:
                        raise RuntimeError("Matrix crypto timeline gap exceeds catch-up bound")
                    seen.add(start)
                    page = self.api("GET", "/_matrix/client/v3/rooms/" + quote(room, safe="") +
                                    "/messages?" + urlencode({"from": start, "to": row[0], "dir": "b", "limit": 100}))
                    history.extend(page.get("chunk", []))
                    start = page.get("end")
                timeline["events"] = list(reversed(history)) + timeline.get("events", [])
                timeline["limited"] = False
        response = SyncResponse.from_dict(raw)
        if not isinstance(response, SyncResponse):
            raise ValueError("invalid Matrix crypto sync payload")
        await client.receive_response(response)
        # Publish identity/one-time keys only after matching the private store.
        if client.should_upload_keys:
            result = await client.keys_upload()
            if hasattr(result, "status_code"):
                raise RuntimeError("Matrix crypto key upload failed")
        if client.should_query_keys:
            result = await client.keys_query()
            if hasattr(result, "status_code"):
                raise RuntimeError("Matrix crypto device query failed")
        if client.outgoing_to_device_messages:
            await client.send_to_device_messages()
        for room, info in raw.get("rooms", {}).get("join", {}).items():
            if info.get("timeline", {}).get("limited") and row:
                # Never acknowledge a sync gap: pollers need the omitted events.
                raise RuntimeError("Matrix crypto timeline gap requires catch-up")
            for ev in info.get("state", {}).get("events", []) + info.get("timeline", {}).get("events", []):
                if "state_key" in ev and ev.get("type") in {"m.room.member", "m.room.encryption", MEMBER}:
                    db.execute("INSERT OR REPLACE INTO state VALUES (?,?,?,?)",
                               (room, ev["type"], ev["state_key"], json.dumps(ev)))
        # receive_response replaced decryptable Megolm events in the response.
        for room, info in response.rooms.join.items():
            for event in info.timeline.events:
                if isinstance(event, MegolmEvent):
                    db.execute("INSERT OR REPLACE INTO pending VALUES (?,?,?,?)",
                               (event.event_id, room, json.dumps(event.source), time.time()))
                else:
                    self._record(db, room, event)
        # A room key may follow its encrypted event in a later sync.
        from nio import Event
        for event_id, room, event in db.execute("SELECT event_id,room,event FROM pending").fetchall():
            try:
                parsed = Event.parse_encrypted_event(dict(json.loads(event), room_id=room))
                clear = client.decrypt_event(parsed)
            except Exception:
                continue
            self._record(db, room, clear)
            db.execute("DELETE FROM pending WHERE event_id=?", (event_id,))
        db.execute("INSERT OR REPLACE INTO meta VALUES ('since',?)", (raw["next_batch"],))
        db.execute("DELETE FROM rtc_keys WHERE ts<?", (int(time.time()*1000)-600_000,))
        db.execute("DELETE FROM events WHERE created<?", (time.time()-86400,))
        db.execute("DELETE FROM pending WHERE created<?", (time.time()-86400,))
        db.commit()

    def _record(self, db, room, event):
        if not hasattr(event, "source"):
            return
        raw = event.source
        if raw.get("type") == KEY_EVENT:
            # Authenticated Megolm only. Keys sent in clear are never accepted.
            content = raw.get("content", {})
            device = content.get("device_id")
            if not event.decrypted or content.get("call_id") != "" or not isinstance(device, str):
                return
            for item in content.get("keys", []):
                index, key = item.get("index"), item.get("key")
                if type(index) is not int or not 0 <= index < 256 or not isinstance(key, str):
                    continue
                try:
                    decoded = base64.b64decode(key + "=" * (-len(key) % 4), altchars=b"-_", validate=True)
                except ValueError:
                    continue
                if len(decoded) != 16:
                    continue
                db.execute("INSERT INTO rtc_keys VALUES (?,?,?,?,?,?,?) ON CONFLICT(room,sender,device,idx) "
                           "DO UPDATE SET key=excluded.key,ts=excluded.ts,sender_key=excluded.sender_key WHERE excluded.ts>rtc_keys.ts",
                           (room, event.sender, device, index, key, event.server_timestamp, event.sender_key))
        elif raw.get("type") in {"m.room.message", MEMBER, "org.matrix.msc4075.rtc.notification"}:
            db.execute("INSERT OR IGNORE INTO events(room,event_id,event,created) VALUES (?,?,?,?)",
                       (room, event.event_id, json.dumps(raw), time.time()))

    def poll(self, cursor=None):
        async def poll(client, db):
            await self._sync(client, db)
            seq = int(cursor[7:]) if isinstance(cursor, str) and cursor.startswith("crypto:") else 0
            rooms = {}
            for room, event in db.execute("SELECT room,event FROM events WHERE seq>? ORDER BY seq", (seq,)):
                rooms.setdefault(room, {"timeline": {"events": []}, "state": {"events": []}})["timeline"]["events"].append(json.loads(event))
            for room, event in db.execute("SELECT room,event FROM state"):
                if room in rooms:
                    rooms[room]["state"]["events"].append(json.loads(event))
            maximum = db.execute("SELECT COALESCE(MAX(seq),0) FROM sqlite_sequence WHERE name='events'").fetchone()[0]
            return {"next_batch": "crypto:" + str(maximum), "rooms": {"join": rooms}}
        return self.transaction(poll)

    def exchange(self, room, peer, *, key=None, index=None, transaction_id=None):
        """Only the already bound control stream can invoke this account operation."""
        async def exchange(client, db):
            await self._sync(client, db)
            state = self.api("GET", "/_matrix/client/v3/rooms/" + quote(room, safe="") + "/state")
            joined = {ev.get("state_key") for ev in state if ev.get("type") == "m.room.member"
                      and ev.get("content", {}).get("membership") == "join"}
            if joined != {client.user_id, peer}:
                raise PermissionError("Matrix crypto requires the exact caller and callee")
            if not any(ev.get("type") == "m.room.encryption" for ev in state):
                raise ValueError("Matrix crypto requires an encrypted room")
            from nio import SyncResponse
            await client.receive_response(SyncResponse.from_dict({"next_batch": "state:"+secrets.token_hex(8),
                "rooms": {"join": {room: {"state": {"events": state}, "timeline": {"events": []}}}}}))
            if key is not None:
                decoded = base64.b64decode(key + "=" * (-len(key) % 4), altchars=b"-_", validate=True)
                if len(decoded) != 16 or type(index) is not int or not 0 <= index < 256 or not transaction_id:
                    raise ValueError("invalid Matrix media sender key")
                result = await client.room_send(room, KEY_EVENT,
                    {"keys": [{"index": index, "key": key}], "device_id": client.device_id,
                     "call_id": "", "sent_ts": int(time.time()*1000)}, tx_id=transaction_id,
                    ignore_unverified_devices=True)
                if not getattr(result, "event_id", None):
                    # The worker never resends this key under another transaction.
                    raise RuntimeError("Matrix media key delivery not confirmed")
            members = {}
            now = int(time.time()*1000)
            for ev in state:
                c = ev.get("content", {})
                if ev.get("type") != MEMBER or ev.get("sender") != peer or not c.get("device_id"):
                    continue
                ts = int(c.get("created_ts") or ev.get("origin_server_ts") or 0)
                if c.get("application") == "m.call" and c.get("call_id") == "" and ts + int(c.get("expires") or 0) > now:
                    members[c["device_id"]] = (ts, c.get("membershipID", ""))
            keys = []
            for sender, device, idx, material, ts, sender_key in db.execute(
                    "SELECT sender,device,idx,key,ts,sender_key FROM rtc_keys WHERE room=? AND sender=?", (room, peer)):
                member = members.get(device)
                sdk_device = client.device_store[peer].get(device)
                if member and ts >= member[0] and sdk_device and sdk_device.curve25519 == sender_key:
                    keys.append({"identity": peer+":"+device, "device_id": device, "index": idx,
                                 "key": material, "membership_id": member[1]})
            return {"device_id": client.device_id, "keys": keys,
                    "members": [{"identity": peer+":"+d, "membership_id": m[1]} for d,m in sorted(members.items())]}
        return self.transaction(exchange)


def for_account(account):
    if not enabled_for(account):
        raise PermissionError("Matrix crypto account is not explicitly enabled")
    return AccountCrypto(account)


def enabled_for(account):
    allowed = {value.strip() for value in os.environ.get("USERIO_MATRIX_CRYPTO_ACCOUNTS", "").split(",") if value.strip()}
    return bool(account.get("account_id") and account["account_id"] in allowed)
