// Background service worker. Mediates between content scripts and the popup.
// Holds the IndexedDB cache (lib/db.js), forwards captures to UserIO
// (lib/userio.js), and exposes search/send RPCs.

importScripts("lib/db.js", "lib/userio.js");

const DB = self.UserIODB;
const USERIO = self.UserIO;

const SEEN = new Set(); // dedupe msg ids in-memory; persistent dedupe lives in IDB
const FORWARD_QUEUE = [];
let FORWARD_TIMER = null;

async function flushQueue() {
  if (FORWARD_TIMER) return;
  FORWARD_TIMER = setTimeout(async () => {
    FORWARD_TIMER = null;
    const batch = FORWARD_QUEUE.splice(0, FORWARD_QUEUE.length);
    for (const item of batch) {
      try {
        await USERIO.forwardCapture(item);
      } catch (e) {
        console.warn("[userio-vk] forward failed", e.message);
        // re-queue on transient failure
        FORWARD_QUEUE.push(item);
      }
    }
    if (FORWARD_QUEUE.length) flushQueue();
  }, 500);
}

async function onCapture(payload, sender) {
  if (!payload || !payload.msg_id) return { ok: false };
  const key = `${payload.peer_id}:${payload.msg_id}`;
  if (SEEN.has(key)) return { ok: true, deduped: true };
  SEEN.add(key);

  const saved = await DB.upsertMessage({
    peer_id: String(payload.peer_id || "unknown"),
    msg_id: String(payload.msg_id),
    body: payload.body || "",
    sender: payload.sender || payload.peer_id,
    ts: payload.ts || Date.now(),
    direction: payload.direction || "in",
    status: "captured",
  });

  await DB.upsertChat({
    peer_id: String(payload.peer_id || "unknown"),
    name: payload.peer_name || String(payload.peer_id || ""),
    last_message_at: payload.ts || Date.now(),
    last_preview: (payload.body || "").slice(0, 200),
    last_msg_id: String(payload.msg_id),
    unread: payload.direction === "out" ? 0 : (payload.unread_increment || 1),
  });

  // forward to UserIO (async, batched, doesn't block return)
  FORWARD_QUEUE.push({
    peerId: payload.peer_id,
    peerName: payload.peer_name,
    msgId: payload.msg_id,
    body: payload.body,
    ts: payload.ts || Date.now(),
    direction: payload.direction || "in",
  });
  flushQueue();

  // notify popup if open
  try {
    await chrome.runtime.sendMessage({ kind: "captured", message: saved });
  } catch (_) {}

  return { ok: true, id: saved.id };
}

async function searchMessages(query) {
  return DB.searchMessages(query, 100);
}

async function listChats() {
  return DB.listChats();
}

async function listMessages(peer_id) {
  return DB.listMessages(peer_id);
}

async function sendViaVK(peer_id, body) {
  // Find a VK tab whose URL matches the peer (sel=, /im/convo/, /im/, or already on /im).
  const tabs = await chrome.tabs.query({ url: ["https://vk.com/*", "https://vk.ru/*"] });
  let tab = null;
  const norm = String(peer_id);
  const matchers = [
    (u) => u.includes(`sel=${encodeURIComponent(norm)}`),
    (u) => u.includes(`sel=${norm}`),
    (u) => u.includes(`/im/convo/${encodeURIComponent(norm)}`),
    (u) => u.includes(`/im/convo/${norm}`),
    (u) => u.includes(`/im/${encodeURIComponent(norm)}`),
    (u) => u.includes(`/im/${norm}`),
  ];
  for (const t of tabs) {
    if (!t.url) continue;
    if (matchers.some((m) => m(t.url))) { tab = t; break; }
  }
  if (!tab) {
    tab = await chrome.tabs.create({
      url: `https://vk.ru/im?sel=${encodeURIComponent(norm)}`,
      active: true,
    });
    await new Promise((r) => setTimeout(r, 1500));
  } else if (!tab.url.includes(`sel=${norm}`) && !tab.url.includes(`/im/convo/${norm}`)) {
    await chrome.tabs.update(tab.id, { url: `https://vk.ru/im?sel=${encodeURIComponent(norm)}`, active: true });
    await new Promise((r) => setTimeout(r, 1500));
  } else {
    await chrome.tabs.update(tab.id, { active: true });
  }
  // Ask the content script to type + send
  return chrome.tabs.sendMessage(tab.id, { kind: "sendText", body: String(body || ""), peer_id: norm });
}

async function composeAndApprove(peer_id, body) {
  // 1. Find or build a UserIO conversation_id for (vk, peer_id).
  // We rely on /v1/conversations?source=vk returning rows whose peer is in
  // display_name / external id mapping; the simplest path is to forward an
  // inbound message first (which materializes a conversation), then we look
  // it up. For YAGNI MVP we just log a draft locally and use the VK DOM
  // directly — UserIO draft/approve is wired but optional.
  const draftId = await DB.addDraft({ peer_id: String(peer_id), body: String(body || "") });
  try {
    await sendViaVK(peer_id, body);
    await DB.updateDraft(draftId, { status: "sent", sent_at: Date.now() });
    // also log outbound to local DB so search finds it
    const msgId = `local-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    await DB.upsertMessage({
      peer_id: String(peer_id),
      msg_id: msgId,
      body: String(body || ""),
      sender: "self",
      ts: Date.now(),
      direction: "out",
      status: "sent",
    });
    return { ok: true, draft_id: draftId, msg_id: msgId };
  } catch (e) {
    await DB.updateDraft(draftId, { status: "failed", error: e.message });
    return { ok: false, draft_id: draftId, error: e.message };
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    try {
      switch (msg?.kind) {
        case "capture":
          sendResponse(await onCapture(msg.payload, sender));
          break;
        case "search":
          sendResponse({ ok: true, results: await searchMessages(msg.query) });
          break;
        case "listChats":
          sendResponse({ ok: true, chats: await listChats() });
          break;
        case "listMessages":
          sendResponse({ ok: true, messages: await listMessages(msg.peer_id) });
          break;
        case "send":
          sendResponse(await composeAndApprove(msg.peer_id, msg.body));
          break;
        case "stats":
          sendResponse({ ok: true, stats: await DB.stats() });
          break;
        case "purge":
          await DB.purgeAll();
          SEEN.clear();
          sendResponse({ ok: true });
          break;
        default:
          sendResponse({ ok: false, error: "unknown kind: " + (msg && msg.kind) });
      }
    } catch (e) {
      sendResponse({ ok: false, error: String(e && e.message || e) });
    }
  })();
  return true; // keep channel open for async response
});

// keepalive: chrome may stop the SW when idle. A short alarm keeps it warm
// enough to receive captures.
chrome.alarms.create("userio-vk-keepalive", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === "userio-vk-keepalive") {
    flushQueue();
  }
});
