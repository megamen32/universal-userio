// Background service worker (MV3). Mediates between content scripts and the
// popup, holds the IndexedDB cache (lib/db.js), forwards captures to UserIO
// (lib/userio.js), and runs the long-poll command channel (lib/agent.js).
//
// lib/*.js attach themselves to `self.UserIODB` / `self.UserIO` /
// `self.Collect` / `self.Agent`; importScripts loads them in order.

importScripts("lib/config.js", "lib/db.js", "lib/userio.js", "lib/collect.js", "lib/agent.js");

const DB = self.UserIODB;
const USERIO = self.UserIO;
const COLLECT = self.Collect;
const AGENT = self.Agent;

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

async function downloadAttachmentsInBackground(peer_id, msg_id, attachments) {
  const downloaded = [];
  for (let i = 0; i < attachments.length; i += 1) {
    const att = attachments[i];
    const src = att && att.src;
    if (!src || /^data:/i.test(src) || /^blob:/i.test(src)) {
      // skip inline data and ephemeral blob URLs — we can't reach them from
      // the SW anyway, and UserIO doesn't need them.
      continue;
    }
    try {
      const res = await fetch(src, { credentials: "include" });
      if (!res.ok) {
        await DB.saveAttachment({
          peer_id, msg_id, idx: i,
          content_type: att.content_type || "application/octet-stream",
          filename: att.filename || `attachment-${i}`,
          src, status: "failed", error: `HTTP ${res.status}`,
        });
        continue;
      }
      const buf = await res.arrayBuffer();
      await DB.saveAttachment({
        peer_id, msg_id, idx: i,
        content_type: att.content_type || res.headers.get("content-type") || "application/octet-stream",
        filename: att.filename || `attachment-${i}`,
        size: buf.byteLength,
        bytes: buf,
        src, status: "ok",
      });
      downloaded.push({ idx: i, src, content_type: att.content_type, filename: att.filename, size: buf.byteLength });
    } catch (e) {
      await DB.saveAttachment({
        peer_id, msg_id, idx: i,
        content_type: att.content_type || "application/octet-stream",
        filename: att.filename || `attachment-${i}`,
        src, status: "failed", error: String(e && e.message || e),
      });
    }
  }
  return downloaded;
}

async function onCapture(payload, sender) {
  if (!payload || !payload.msg_id) return { ok: false };
  const key = `${payload.peer_id}:${payload.msg_id}`;
  if (SEEN.has(key)) return { ok: true, deduped: true };
  SEEN.add(key);

  const attachments = Array.isArray(payload.attachments) ? payload.attachments : [];
  const saved = await DB.upsertMessage({
    peer_id: String(payload.peer_id || "unknown"),
    msg_id: String(payload.msg_id),
    body: payload.body || "",
    sender: payload.sender || payload.peer_id,
    ts: payload.ts || Date.now(),
    direction: payload.direction || "in",
    status: "captured",
    attachments: attachments.map((a) => ({
      src: a.src, content_type: a.content_type, filename: a.filename, kind: a.kind,
    })),
  });

  await DB.upsertChat({
    peer_id: String(payload.peer_id || "unknown"),
    name: payload.peer_name || String(payload.peer_id || ""),
    last_message_at: payload.ts || Date.now(),
    last_preview: (payload.body || "").slice(0, 200),
    last_msg_id: String(payload.msg_id),
    unread: payload.direction === "out" ? 0 : (payload.unread_increment || 1),
  });

  // download attachments in the SW (extension storage), not the content page
  let downloadedAttachments = [];
  if (attachments.length) {
    try {
      downloadedAttachments = await downloadAttachmentsInBackground(
        String(payload.peer_id), String(payload.msg_id), attachments,
      );
    } catch (e) {
      console.warn("[userio-vk] attachment download failed", e && e.message);
    }
  }

  // forward to UserIO (async, batched, doesn't block return)
  FORWARD_QUEUE.push({
    peerId: payload.peer_id,
    peerName: payload.peer_name,
    msgId: payload.msg_id,
    body: payload.body,
    ts: payload.ts || Date.now(),
    direction: payload.direction || "in",
    attachments: downloadedAttachments,
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

async function listAttachments(peer_id, msg_id) {
  const rows = await DB.listAttachmentsForMessage(String(peer_id), String(msg_id));
  // strip raw bytes from the RPC response
  return rows.map(({ bytes, ...meta }) => ({ ...meta }));
}

async function readAttachment(id) {
  const row = await DB.getAttachment(Number(id));
  if (!row) return null;
  // return as base64 over the SW RPC since structured clone is friendlier
  const bytes = row.bytes || new ArrayBuffer(0);
  let b64 = "";
  try {
    b64 = btoa(String.fromCharCode.apply(null, new Uint8Array(bytes)));
  } catch (_) {
    b64 = "";
  }
  return {
    id: row.id,
    peer_id: row.peer_id,
    msg_id: row.msg_id,
    idx: row.idx,
    content_type: row.content_type,
    filename: row.filename,
    size: row.size,
    status: row.status,
    error: row.error,
    src: row.src,
    bytes_base64: b64,
  };
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
  // Type + send from the MAIN world: VK's React composer ignores synthetic
  // clicks and input events dispatched from the extension's isolated world.
  try {
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["lib/selectors.js"],
      world: "MAIN",
    });
    const [injection] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      world: "MAIN",
      func: async (text) => {
        const SEL = window.VKSelectors;
        if (!SEL) return { ok: false, error: "selectors missing in MAIN world" };
        const input = await SEL.waitFor(() => SEL.messageInput(), 8000).catch(() => null);
        if (!input) return { ok: false, error: "input not found" };
        SEL.setInputText(input, text || "");
        await new Promise((r) => setTimeout(r, 250));
        const sendBtn = await SEL.waitFor(() => SEL.sendButton(), 5000).catch(() => null);
        if (sendBtn) {
          sendBtn.click();
        } else {
          input.focus();
          input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true }));
        }
        return { ok: true };
      },
      args: [String(body)],
    });
    if (injection && injection.result && injection.result.ok) return injection.result;
  } catch (e) {
    console.warn("[userio-vk] main-world send failed, falling back to content bridge", e && e.message);
  }
  // Ask the content script to type + send (legacy fallback)
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
        case "listAttachments":
          sendResponse({ ok: true, attachments: await listAttachments(msg.peer_id, msg.msg_id) });
          break;
        case "readAttachment":
          sendResponse({ ok: true, attachment: await readAttachment(msg.id) });
          break;
        case "send":
          sendResponse(await composeAndApprove(msg.peer_id, msg.body));
          break;
        case "stats":
          sendResponse({ ok: true, stats: await DB.stats() });
          break;
        case "collectStatus":
          sendResponse({ ok: true, state: await COLLECT.state() });
          break;
        case "collectRun":
          sendResponse(await COLLECT.runDue());
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

COLLECT.start();
AGENT.start();
