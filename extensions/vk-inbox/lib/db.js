// Tiny IndexedDB wrapper for the extension. Schema:
//   chats:     { peer_id (key), name, last_message_at, unread, last_preview, last_msg_id, updated_at }
//   messages:  auto-key, indexes on [peer_id+ts], [body_lc] for substring search
//   drafts:    auto-key, { peer_id, body, created_at, status: 'pending'|'sent'|'failed' }
//
// Used by the service worker. Content scripts ask the SW to read/write via
// chrome.runtime messages.

(function (root) {
  const DB_NAME = "userio_vk";
  const DB_VERSION = 1;

  let _dbPromise = null;
  function openDB() {
    if (_dbPromise) return _dbPromise;
    _dbPromise = new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onerror = () => reject(req.error);
      req.onupgradeneeded = (e) => {
        const db = e.target.result;
        if (!db.objectStoreNames.contains("chats")) {
          db.createObjectStore("chats", { keyPath: "peer_id" });
        }
        if (!db.objectStoreNames.contains("messages")) {
          const s = db.createObjectStore("messages", { keyPath: "id", autoIncrement: true });
          s.createIndex("peer_ts", ["peer_id", "ts"], { unique: false });
          s.createIndex("body_lc", "body_lc", { unique: false });
          s.createIndex("peer_id", "peer_id", { unique: false });
        }
        if (!db.objectStoreNames.contains("drafts")) {
          db.createObjectStore("drafts", { keyPath: "id", autoIncrement: true });
        }
      };
      req.onsuccess = () => resolve(req.result);
    });
    return _dbPromise;
  }

  function tx(storeNames, mode = "readonly") {
    return openDB().then((db) => db.transaction(storeNames, mode));
  }

  function req(r) {
    return new Promise((res, rej) => {
      r.onsuccess = () => res(r.result);
      r.onerror = () => rej(r.error);
    });
  }

  const lib = {};

  lib.upsertChat = async (chat) => {
    const t = await tx(["chats"], "readwrite");
    const store = t.objectStore("chats");
    const existing = await req(store.get(chat.peer_id));
    const merged = { ...(existing || {}), ...chat, updated_at: Date.now() };
    await req(store.put(merged));
    return merged;
  };

  lib.listChats = async () => {
    const t = await tx(["chats"]);
    const all = await req(t.objectStore("chats").getAll());
    return all.sort((a, b) => (b.last_message_at || 0) - (a.last_message_at || 0));
  };

  lib.getChat = async (peer_id) => {
    const t = await tx(["chats"]);
    return req(t.objectStore("chats").get(peer_id));
  };

  lib.upsertMessage = async (m) => {
    const t = await tx(["messages"], "readwrite");
    const store = t.objectStore("messages");
    const idx = store.index("peer_ts");
    const range = IDBKeyRange.bound([m.peer_id, 0], [m.peer_id, Number.MAX_SAFE_INTEGER]);
    const sameKey = await req(idx.getAll(range));
    const dup = sameKey.find((x) => String(x.msg_id) === String(m.msg_id));
    if (dup) return dup;
    const row = { ...m, body_lc: (m.body || "").toLowerCase() };
    const id = await req(store.add(row));
    return { ...row, id };
  };

  lib.listMessages = async (peer_id, limit = 200) => {
    const t = await tx(["messages"]);
    const idx = t.objectStore("messages").index("peer_ts");
    const range = IDBKeyRange.bound([peer_id, 0], [peer_id, Number.MAX_SAFE_INTEGER]);
    const rows = await req(idx.getAll(range, limit));
    return rows.sort((a, b) => a.ts - b.ts);
  };

  lib.searchMessages = async (query, limit = 50) => {
    const q = String(query || "").trim().toLowerCase();
    if (!q) return [];
    const t = await tx(["messages"]);
    const idx = t.objectStore("messages").index("body_lc");
    // IDBKeyRange.only does exact match; fall back to full scan + substring filter
    const all = await req(idx.getAll());
    const hits = all.filter((m) => (m.body_lc || "").includes(q));
    hits.sort((a, b) => b.ts - a.ts);
    return hits.slice(0, limit);
  };

  lib.addDraft = async (draft) => {
    const t = await tx(["drafts"], "readwrite");
    const id = await req(t.objectStore("drafts").add({ ...draft, created_at: Date.now(), status: draft.status || "pending" }));
    return id;
  };

  lib.updateDraft = async (id, patch) => {
    const t = await tx(["drafts"], "readwrite");
    const store = t.objectStore("drafts");
    const cur = await req(store.get(id));
    if (!cur) return null;
    const next = { ...cur, ...patch };
    await req(store.put(next));
    return next;
  };

  lib.listDrafts = async (peer_id) => {
    const t = await tx(["drafts"]);
    const all = await req(t.objectStore("drafts").getAll());
    return peer_id ? all.filter((d) => d.peer_id === peer_id) : all;
  };

  lib.stats = async () => {
    const t = await tx(["chats", "messages", "drafts"]);
    const chats = await req(t.objectStore("chats").count());
    const messages = await req(t.objectStore("messages").count());
    const drafts = await req(t.objectStore("drafts").count());
    return { chats, messages, drafts };
  };

  lib.purgeAll = async () => {
    const t = await tx(["chats", "messages", "drafts"], "readwrite");
    await req(t.objectStore("chats").clear());
    await req(t.objectStore("messages").clear());
    await req(t.objectStore("drafts").clear());
    return true;
  };

  root.UserIODB = lib;
})(typeof self !== "undefined" ? self : this);
