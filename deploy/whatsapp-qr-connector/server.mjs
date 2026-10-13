import http from "node:http";
import { mkdirSync, readdirSync, readFileSync, writeFileSync, existsSync, renameSync, chmodSync } from "node:fs";
import { join } from "node:path";
import makeWASocket, { Browsers, DisconnectReason, fetchLatestBaileysVersion, fetchLatestWaWebVersion, useMultiFileAuthState } from "@whiskeysockets/baileys";
import QRCode from "qrcode";
import { EnrollmentOwners, enrollmentPrincipal } from "./enrollment-owner.mjs";
import { accountRef, senderSlots, personalEnvelope } from "./tenant-routing.mjs";

const port = Number(process.env.PORT || 18096);
const publicPrefix = (process.env.PUBLIC_PREFIX ?? "/whatsapp-qr").replace(/\/$/, "");
const stateDir = process.env.USERIO_WHATSAPP_STATE_DIR || "/var/lib/universal-userio/whatsapp-qr";
const sessionsDir = join(stateDir, "sessions");
const userIoUrl = process.env.UNIVERSAL_USERIO_URL || "http://127.0.0.1:18093";
mkdirSync(stateDir,{recursive:true,mode:0o700});
const owners=new EnrollmentOwners(join(stateDir,"slot-owners.json"),process.env.USERIO_WHATSAPP_USER_ID||"user_owner");
const linkPath=join(stateDir,"slot-accounts.json");
const links=existsSync(linkPath)?JSON.parse(readFileSync(linkPath,"utf8")):{};
function saveLinks(){const tmp=linkPath+".next";writeFileSync(tmp,JSON.stringify(links),{mode:0o600});chmodSync(tmp,0o600);renameSync(tmp,linkPath);}
const slots = new Map();
let nextSlot = 1;
// In-memory cache of every message we have ever seen, keyed by `${chatId}:${messageId}`.
// Lets /download resolve media even after a restart that wiped Baileys' internal
// store, as long as the message was observed by this bridge process at least once.
// Limited to MAX_MESSAGE_CACHE entries per process to bound memory.
const MAX_MESSAGE_CACHE = 5000;
const messageCache = new Map();
const cacheKey = (chatId,messageId,slot="legacy") => `${slot}::${chatId}::${messageId}`;
const rememberMessage = (chatId,message,slot="legacy") => {
  if (!message || !message.key || !message.key.id) return;
  const id = String(message.key.id);
  messageCache.set(cacheKey(chatId,id,slot), message);
  if (messageCache.size > MAX_MESSAGE_CACHE) {
    const oldest = messageCache.keys().next().value;
    if (oldest) messageCache.delete(oldest);
  }
};
const lookupMessage = (chatId,messageId,slot="legacy") => messageCache.get(cacheKey(chatId,messageId,slot));
mkdirSync(sessionsDir, { recursive: true, mode: 0o700 });
const knownSlots = [];
for (const name of readdirSync(sessionsDir, { withFileTypes: true })) {
  const match = /^account-(\d+)$/.exec(name.name);
  if (name.isDirectory() && match) { slots.set(`account-${match[1]}`, { status: "idle" }); nextSlot = Math.max(nextSlot, Number(match[1]) + 1); knownSlots.push(`account-${match[1]}`); }
}
// Reconnect every persisted session at boot; without this the slots stay "idle"
// until a human opens /?slot=... and all ingestion silently stops after a restart.
for(const slot of knownSlots) {
 if(owners.owners[slot]&&(!links[slot]||links[slot].disabled)){slots.set(slot,{status:"cancelled",cancelled:true});continue;}
 void start(slot).catch(()=>slots.set(slot,{status:"error"}));
}

const escape = (value) => String(value).replace(/[&<>\"]/g, (char) => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;"}[char]));
// WhatsApp hands out anonymous @lid JIDs alongside real phone JIDs; the same
// person then splits into two conversations. Learn lid->phone mappings so
// ingested senders are normalized to one stable phone JID per person.
const lidMapPath = join(stateDir, "lid-map.json");
let lidMap = {};
try { lidMap = JSON.parse(readFileSync(lidMapPath, "utf8")) || {}; } catch { lidMap = {}; }
const learnLid = (lid, jid) => {
  if (typeof lid === "string" && lid.endsWith("@lid") && typeof jid === "string" && jid.endsWith("@s.whatsapp.net") && lidMap[lid] !== jid) {
    lidMap[lid] = jid;
    try { writeFileSync(lidMapPath, JSON.stringify(lidMap)); } catch (error) { console.error("LID map save failed", error.message); }
  }
};
const normSender = (jid) => (typeof jid === "string" && jid.endsWith("@lid") && lidMap[jid]) || jid;
const page = (content) => `<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Подключить WhatsApp</title><style>body{margin:0;font:16px system-ui;background:#111;color:#eee}main{max-width:680px;margin:40px auto;padding:24px}.card{background:#1d1d1d;border-radius:16px;padding:20px;margin:12px 0}.qr{width:min(300px,100%);background:#fff;padding:12px;border-radius:12px}button{padding:12px 16px;border:0;border-radius:10px;font-weight:700;cursor:pointer}</style><main><h1>Подключить WhatsApp</h1><p><a href="/accounts?service=whatsapp">← Аккаунты UserIO</a></p><p>Откройте WhatsApp на телефоне → Связанные устройства → Привязка устройства. Отсканируйте QR. Подключение будет сохранено в вашем UserIO.</p>${content}</main>`;

function postUserIo(path, payload, personalHeaders=null) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify(payload);
    const request = http.request(new URL(path, userIoUrl), { method: "POST", headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body), ...(personalHeaders||{Authorization: `Bearer ${process.env.USERIO_API_TOKEN || ""}`}) } }, (response) => {
      response.resume();
      response.on("end", () => response.statusCode >= 200 && response.statusCode < 300 ? resolve() : reject(new Error(`UserIO returned HTTP ${response.statusCode}`)));
    });
    request.setTimeout(8000,()=>request.destroy(new Error("UserIO timeout")));
    request.once("error", reject); request.end(body);
  });
}

async function registerAccount(slot, jid) {
  const id=accountRef(slot,jid,owners);
  if(owners.owners[slot]&&!owners.auth.has(slot)){
    if(links[slot]?.account_ref!==id||links[slot]?.owner!==owners.owner(slot)||links[slot]?.disabled)throw Error("Personal account registration required");return;
  } // Persisted personal binding; never recreate it as operator.
  const headers=owners.registrationHeaders(slot);
  await postUserIo("/v1/accounts",{id,provider:"whatsapp",display_name:`WhatsApp ${jid.split(":")[0].split("@")[0]}`,
    can_read:true,can_reply:Boolean(headers),credential_ref:`whatsapp-qr:${slot}`,enabled:true},headers);
  if(owners.owners[slot]){links[slot]={account_ref:id,owner:owners.owner(slot),disabled:false};saveLinks();}
  owners.registered(slot);
}

async function ingest(slot, message, reconciliation=false) {
  if(owners.owners[slot]) {
    const item=slots.get(slot);if(item?.status!=="connected")return;
    const own=String(item.socket?.user?.id||item.name||"").split(":")[0].split("@")[0]+"@s.whatsapp.net";
    const ref=accountRef(slot,item.socket?.user?.id||item.name,owners);
    const envelope=personalEnvelope(slot,message,{ref,own,peer:normSender(message.key?.remoteJid),reconciliation});
    if(envelope)await postUserIo("/v1/messages",{route_id:"whatsapp-reply",account_id:ref,message:envelope});
    return;
  }
  const key = message.key || {};
  const sender = normSender(key.remoteJid) || "whatsapp";
  const isGroup = typeof sender === "string" && sender.endsWith("@g.us");
  const body = message.message?.conversation || message.message?.extendedTextMessage?.text || message.message?.imageMessage?.caption || message.message?.videoMessage?.caption || (message.message?.imageMessage ? "[WhatsApp image]" : message.message?.videoMessage ? "[WhatsApp video]" : "");
  if (!body || key.fromMe) return;
  const envelope = { schema: "universal.inbox.message.v1", source: "whatsapp", message_id: `${slot}:${key.id || Date.now()}`, sender, body };
  if (!isGroup && message.pushName) envelope.sender_name = String(message.pushName).slice(0, 128);
  await postUserIo("/v1/messages", { route_id: "whatsapp-reply", message: envelope });
}

async function start(slot) {
  const current = slots.get(slot);
  if (!current || ["connecting", "waiting", "connected"].includes(current.status)) return;
  slots.set(slot, { status: "connecting" });
  const pending=slots.get(slot);
  const auth = await useMultiFileAuthState(join(sessionsDir, slot));
  if(slots.get(slot)!==pending||pending.cancelled)return;
  const fallbackVersion = [2, 3000, 1042466098];
  const versionResult = await Promise.race([
    fetchLatestWaWebVersion(),
    new Promise((resolve) => setTimeout(() => resolve({ version: fallbackVersion }), 5000)),
  ]);
  const version = versionResult?.version || (await fetchLatestBaileysVersion()).version || fallbackVersion;
  if(slots.get(slot)!==pending||pending.cancelled)return;
  const socket = makeWASocket({ version, auth: auth.state, browser: Browsers.ubuntu("Universal UserIO"), printQRInTerminal: false, syncFullHistory: true });
  slots.set(slot, { status: "waiting", socket });
  socket.ev.on("creds.update", auth.saveCreds);
  socket.ev.on("contacts.upsert", (contacts) => {
    for (const contact of contacts || []) learnLid(contact.lid, contact.jid || (typeof contact.id === "string" && contact.id.endsWith("@s.whatsapp.net") ? contact.id : undefined));
  });
  socket.ev.on("chats.phoneNumberShare", ({ lid, jid }) => learnLid(lid, jid));
  socket.ev.on("connection.update", async ({ connection, lastDisconnect, qr }) => {
    if(slots.get(slot)?.socket!==socket||slots.get(slot)?.cancelled)return;
    const item = slots.get(slot) || {};
    if (qr) {const encoded=await QRCode.toDataURL(qr);if(slots.get(slot)?.socket===socket&&!slots.get(slot)?.cancelled)slots.set(slot,{...item,status:"waiting",qr:encoded,socket});}
    if (connection === "open") {
      const jid = socket.user?.id || "whatsapp";
      try { slots.set(slot,{status:"registering",name:jid,socket});await registerAccount(slot, jid); if(slots.get(slot)?.socket===socket)slots.set(slot, { status: "connected", name: jid, socket }); }
      catch (error) { console.error("WhatsApp account registration failed", error.constructor.name); if(slots.get(slot)?.socket===socket)slots.set(slot, { status: "connected-unregistered", name: jid, socket }); }
    }
    if (connection === "close") {
      const code = lastDisconnect?.error?.output?.statusCode;
      if (code !== DisconnectReason.loggedOut) { slots.set(slot, { status: "idle" }); setTimeout(() => {if(!slots.get(slot)?.cancelled)void start(slot);}, 3000); }
      else slots.set(slot, { status: "logged-out" });
    }
  });
  socket.ev.on("messages.upsert", async ({ messages, type }) => {
    if(slots.get(slot)?.socket!==socket||slots.get(slot)?.cancelled||!["notify", "append"].includes(type)) return;
    for (const message of messages) {
      const chatJid = normSender(String((message.key && message.key.remoteJid) || ""));
      if (chatJid) rememberMessage(chatJid,message,slot);
      try { await ingest(slot, message, type!=="notify"); } catch (error) { console.error("WhatsApp ingest failed", error.message); }
    }
  });
  socket.ev.on("messaging-history.set", async ({ messages }) => {
    if(slots.get(slot)?.socket!==socket||slots.get(slot)?.cancelled)return;
    for (const message of messages || []) {
      const chatJid = normSender(String((message.key && message.key.remoteJid) || ""));
      if (chatJid) rememberMessage(chatJid,message,slot);
      try { await ingest(slot, message,true); } catch (error) { console.error("WhatsApp history ingest failed", error.message); }
    }
  });
  socket.ev.on("chats.upsert", async (chats) => {
    if(slots.get(slot)?.socket!==socket||slots.get(slot)?.cancelled)return;
    for (const chat of chats || []) {
      if (chat.lastMessage) { try { await ingest(slot, chat.lastMessage,true); } catch (error) { console.error("WhatsApp chat ingest failed", error.message); } }
    }
  });
}

function cancel(slot) {const item=slots.get(slot);if(!item||item.status==="registering")return;if(owners.owners[slot]){links[slot]={...(links[slot]||{}),owner:owners.owner(slot),disabled:true};saveLinks();}slots.set(slot,{...item,status:"cancelled",qr:"",cancelled:true});owners.registered(slot);try{item.socket?.end(new Error("UserIO login cancelled"));}catch{}}

function restore(slot) { if (!/^account-\d+$/.test(slot)) return false; if (!slots.has(slot)) slots.set(slot, { status: "idle" }); void start(slot); return true; }

http.createServer(async (request, response) => {
  const url = new URL(request.url, `http://${request.headers.host}`); const selected = url.searchParams.get("slot");
  let personal=null;
  if(["/","/state","/new","/start","/cancel"].includes(url.pathname)) {
    try {
      personal=await enrollmentPrincipal(userIoUrl,request.headers,async(url,headers)=>{
        const r=await fetch(url,{headers,signal:AbortSignal.timeout(5000)});
        if(!r.ok)throw Error("UserIO login required");return r.json();
      });
      if(!personal.principal.can_manage)throw Error("Personal UserIO login required");
      if(selected&&(!/^account-\d+$/.test(selected)||!slots.has(selected)||!owners.allowed(selected,personal.principal.user_id))) {
        response.writeHead(403,{"content-type":"text/html; charset=utf-8"});return response.end(page("<p>Это подключение принадлежит другому пользователю.</p>"));
      }
    } catch {
      response.writeHead(401,{"content-type":"text/html; charset=utf-8"});return response.end(page('<p>Войдите в свой UserIO для подключения WhatsApp.</p><a href="/login">Войти</a>'));
    }
  }
  if(url.pathname==="/state") {
    const rows=[...slots.entries()].filter(([id])=>owners.allowed(id,personal.principal.user_id))
      .map(([id,item])=>({id,status:item.status,name:item.name||"",qr:item.qr||""}));
    response.writeHead(200,{"content-type":"application/json","Cache-Control":"no-store"});return response.end(JSON.stringify(rows));
  }
  if(url.pathname==="/new") {
    const active=[...slots.values()].filter(x=>["connecting","waiting","registering","connected","connected-unregistered"].includes(x.status)).length;
    const ownPending=[...slots.entries()].some(([id,x])=>owners.allowed(id,personal.principal.user_id)&&["connecting","waiting"].includes(x.status));
    if(active>=8||ownPending){response.writeHead(409,{"content-type":"text/html; charset=utf-8"});return response.end(page("<p>Завершите или отмените текущий вход. Если подключений много, повторите позже.</p>"));}
    const slot=`account-${nextSlot++}`;owners.bind(slot,personal.principal,personal.headers);slots.set(slot,{status:"idle"});
    void start(slot).catch(()=>slots.set(slot,{status:"error"}));
    setTimeout(()=>{const item=slots.get(slot);if(item&&item.status!=="connected"&&!item.cancelled)cancel(slot);},180000).unref();
    response.writeHead(302,{Location:`${publicPrefix}/?slot=${slot}`});return response.end();
  }
  if(url.pathname==="/cancel") {
    if(slots.get(selected)?.status!=="connected")cancel(selected);
    response.writeHead(302,{Location:`${publicPrefix}/`});return response.end();
  }
  if(url.pathname==="/start") {
    if(!selected){response.writeHead(400);return response.end();}
    owners.bind(selected,personal.principal,personal.headers);
    const item=slots.get(selected);
    if(["connected","registering"].includes(item?.status)){response.writeHead(409);return response.end();}
    if(item?.status==="connected-unregistered"&&item.socket?.user?.id){
      try{await registerAccount(selected,item.socket.user.id);slots.set(selected,{...item,status:"connected"});}catch{response.writeHead(409);return response.end();}
    }else{
      slots.set(selected,{status:"idle"});try{item?.socket?.end(new Error("UserIO reconnect"));}catch{}
      if(links[selected]){links[selected].disabled=false;saveLinks();}void start(selected);
    }
    response.writeHead(302,{Location:`${publicPrefix}/?slot=${selected}`});return response.end();
  }
  if (url.pathname === "/seed-test-message" && request.method === "POST") {
    // Operator-only: produce a fake but real WAMessage that /download can
    // serve without depending on WhatsApp's media round-trip. Lets a tester
    // exercise the full UserIO → bridge → bytes chain end-to-end.
    const expected = `Bearer ${process.env.USERIO_API_TOKEN || ""}`;
    if (!process.env.USERIO_API_TOKEN || request.headers.authorization !== expected) {
      response.writeHead(401, { "content-type": "application/json" });
      return response.end(JSON.stringify({ error: "unauthorized" }));
    }
    let raw = "";
    request.on("data", (chunk) => { raw += chunk; });
    request.on("end", () => {
      try {
        const payload = JSON.parse(raw || "{}");
        const chatId = String(payload.chat_id || "").trim();
        const messageId = String(payload.message_id || `seed-${Date.now()}`).trim();
        const dataUrl = String(payload.data_url || "").trim();
        const mime = String(payload.mime || "image/png").trim();
        const filename = String(payload.filename || `seed.${mime.split("/")[1] || "bin"}`).trim();
        if (!chatId || !dataUrl) {
          response.writeHead(400, { "content-type": "application/json" });
          return response.end(JSON.stringify({ error: "chat_id and data_url required" }));
        }
        const base64 = dataUrl.replace(/^data:[^;]+;base64,/, "");
        const data = Buffer.from(base64, "base64");
        const kind = mime.startsWith("image/") ? "image" : mime.startsWith("video/") ? "video" : mime.startsWith("audio/") ? "audio" : "document";
        const message = {
          key: { remoteJid: chatId, fromMe: false, id: messageId },
          message: {
            [kind + "Message"]: {
              mimetype: mime,
              fileName: filename,
              caption: "",
              mediaKey: Buffer.alloc(32).toString("base64"),
              fileLength: data.length,
              url: "https://example.invalid/seed",
              _seeded: true,
              _seedData: data.toString("base64"),
            },
          },
          messageTimestamp: Math.floor(Date.now() / 1000),
        };
        rememberMessage(chatId, message);
        response.end(JSON.stringify({ ok: true, chat_id: chatId, message_id: messageId, mime, bytes: data.length }));
      } catch (error) {
        response.writeHead(500, { "content-type": "application/json" });
        response.end(JSON.stringify({ error: (error && error.message) || "seed failed" }));
      }
    });
    return;
  }
  if(url.pathname==="/account/disconnect"&&request.method==="POST") {
    if(!process.env.USERIO_API_TOKEN||request.headers.authorization!==`Bearer ${process.env.USERIO_API_TOKEN}`){response.writeHead(401);return response.end();}
    let raw="";request.on("data",chunk=>{raw+=chunk;if(raw.length>8192)request.destroy();});
    request.on("end",()=>{try {const p=JSON.parse(raw);const found=Object.entries(links).filter(([slot,link])=>link.account_ref===p.account_ref&&link.owner===p.user_id&&owners.allowed(slot,p.user_id));if(found.length!==1)throw Error("owned account unavailable");cancel(found[0][0]);response.end(JSON.stringify({disconnected:true}));}catch{response.writeHead(409);response.end(JSON.stringify({error:"owned account unavailable"}));}});return;
  }
  if (url.pathname === "/send-self-test" && request.method === "POST") {
    // Operator-only: send a tiny synthetic image to ourselves to populate the
    // cache and exercise the download path end-to-end. Guarded by the same
    // bearer token as the rest of the API.
    const expected = `Bearer ${process.env.USERIO_API_TOKEN || ""}`;
    if (!process.env.USERIO_API_TOKEN || request.headers.authorization !== expected) {
      response.writeHead(401, { "content-type": "application/json" });
      return response.end(JSON.stringify({ error: "unauthorized" }));
    }
    let raw = "";
    request.on("data", (chunk) => { raw += chunk; });
    request.on("end", async () => {
      try {
        const payload = JSON.parse(raw || "{}");
        const chatId = String(payload.chat_id || "").trim();
        if (!chatId) {
          response.writeHead(400, { "content-type": "application/json" });
          return response.end(JSON.stringify({ error: "chat_id required" }));
        }
        // 1x1 transparent PNG, sent as a buffer. The bridge will then
        // receive its own message via messages.upsert and remember it.
        const png = Buffer.from(
          "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=",
          "base64",
        );
        for (const [slot, item] of slots) {
          if(owners.owners[slot])continue;
          if (!item.socket || item.status !== "connected") continue;
          const sent = await item.socket.sendMessage(chatId, { image: png, caption: `userio-self-test-${Date.now()}` });
          const sentId = String(sent && sent.key && sent.key.id || "");
          // Baileys does not always round-trip messages.upsert for
          // own writes; remember the message ourselves so /download can
          // resolve it without waiting for history sync.
          if (sentId) rememberMessage(chatId,sent,slot);
          return response.end(JSON.stringify({
            ok: true, slot,
            message_id: sentId,
            chat_id: chatId,
          }));
        }
        response.writeHead(404, { "content-type": "application/json" });
        response.end(JSON.stringify({ error: "no connected account" }));
      } catch (error) {
        console.error("send-self-test error:", (error && error.message) || error);
        response.writeHead(502, { "content-type": "application/json" });
        response.end(JSON.stringify({ error: (error && error.message) || "send failed" }));
      }
    });
    return;
  }
  if (url.pathname === "/send" && request.method === "POST") {
    // Operator-only: send a text message on behalf of a connected WhatsApp slot.
    // Guarded by the same bearer token as /send-self-test, /seed-test-message
    // and /download. Slot is picked by exact match, then by which connected
    // account has the chat in its history (lidMap-aware).
    const expected = `Bearer ${process.env.USERIO_API_TOKEN || ""}`;
    if (!process.env.USERIO_API_TOKEN || request.headers.authorization !== expected) {
      response.writeHead(401, { "content-type": "application/json" });
      return response.end(JSON.stringify({ error: "unauthorized" }));
    }
    let raw = "";
    request.on("data", (chunk) => { raw += chunk; });
    request.on("end", async () => {
      try {
        const payload = JSON.parse(raw || "{}");
        const chatId = String(payload.chatId || payload.chat_id || "").trim();
        const text = String(payload.message || payload.text || "");
        const replyTo = payload.replyTo || payload.reply_to || null;
        if (!chatId || !text) {
          response.writeHead(400, { "content-type": "application/json" });
          return response.end(JSON.stringify({ error: "chatId and message are required" }));
        }
        // 1. Exact slot from request (operator knows which account to use).
        // 2. Otherwise any connected slot — useful when sender JID is in its history.
        const candidateSlots=senderSlots(payload,slots,owners);
        for (const slot of candidateSlots) {
          const item = slots.get(slot);
          if (!item || !item.socket || item.status !== "connected") continue;
          const sent = await item.socket.sendMessage(chatId, { text }, replyTo ? { quoted: lookupMessage(chatId,String(replyTo),slot) } : {});
          const sentId = String(sent && sent.key && sent.key.id || "");
          if (sentId) rememberMessage(chatId,sent,slot);
          return response.end(JSON.stringify({
            ok: true, slot,
            messageId: sentId,
            messageIds: sentId ? [sentId] : [],
            chat_id: chatId,
          }));
        }
        response.writeHead(404, { "content-type": "application/json" });
        response.end(JSON.stringify({ error: "no connected account" }));
      } catch (error) {
        console.error("send error:", (error && error.message) || error);
        response.writeHead(502, { "content-type": "application/json" });
        response.end(JSON.stringify({ error: (error && error.message) || "send failed" }));
      }
    });
    return;
  }
  if (url.pathname === "/download" && request.method === "POST") {
    const expected = `Bearer ${process.env.USERIO_API_TOKEN || ""}`;
    if (!process.env.USERIO_API_TOKEN || request.headers.authorization !== expected) {
      response.writeHead(401, { "content-type": "application/json" });
      return response.end(JSON.stringify({ error: "unauthorized" }));
    }
    let raw = "";
    request.on("data", (chunk) => { raw += chunk; });
    request.on("end", async () => {
      try {
        const payload = JSON.parse(raw || "{}");
        const chatId = String(payload.chat_id || payload.chat || "").trim();
        const messageId = String(payload.message_id || "").trim();
        if (!chatId || !messageId) {
          response.writeHead(400, { "content-type": "application/json" });
          return response.end(JSON.stringify({ error: "chat_id and message_id are required" }));
        }
        const { downloadContentFromMessage, getContentType } = await import("@whiskeysockets/baileys");
        for (const slot of senderSlots(payload,slots,owners)) {
          const item=slots.get(slot);
          if (!item.socket || item.status !== "connected") continue;
          let target = null;
          let stage = "none";
          // 1. Walk the in-memory cache populated by messages.upsert and
          //    messaging-history.set events. This is the only place older
          //    media lives after a restart since the bridge does not persist
          //    Baileys' in-memory message store.
          target=lookupMessage(chatId,messageId,slot)||(!owners.owners[slot]?lookupMessage(chatId,messageId):null);
          stage = "cache";
          // 2. Baileys' own store (when bound). Returns null for messages
          //    that have aged out of the in-memory ring buffer.
          if (!target && typeof item.socket.store?.loadMessage === "function") {
            stage = "loadMessage";
            try { target = await item.socket.store.loadMessage(chatId, messageId); } catch { target = null; }
          }
          if (!target || !target.message) continue;
          const kind = getContentType(target.message);
          if (!kind) {
            response.writeHead(404, { "content-type": "application/json" });
            return response.end(JSON.stringify({ error: "message has no media" }));
          }
          // In-process seeded messages carry their bytes inline so a tester
          // can verify the UserIO → bridge → /raw chain without round-tripping
          // through WhatsApp. The flag is set only by /seed-test-message.
          const seeded = target.message[kind];
          if (seeded && seeded._seeded && seeded._seedData) {
            const buffer = Buffer.from(seeded._seedData, "base64");
            response.writeHead(200, {
              "content-type": seeded.mimetype || "application/octet-stream",
              "content-disposition": `attachment; filename="${(seeded.fileName || `${kind}-${messageId}`).replace(/"/g, "")}"`,
              "x-filename": seeded.fileName || `${kind}-${messageId}`,
              "x-mime": seeded.mimetype || "application/octet-stream",
            });
            return response.end(buffer);
          }
          const stream = await downloadContentFromMessage(target.message, kind);
          const chunks = [];
          for await (const chunk of stream) chunks.push(chunk);
          const buffer = Buffer.concat(chunks);
          const filename = target.message[kind]?.fileName || `${kind}-${messageId}`;
          const mime = target.message[kind]?.mimetype || "application/octet-stream";
          response.writeHead(200, {
            "content-type": mime,
            "content-disposition": `attachment; filename="${filename.replace(/"/g, "")}"`,
            "x-filename": filename,
            "x-mime": mime,
          });
          return response.end(buffer);
        }
        response.writeHead(404, { "content-type": "application/json" });
        response.end(JSON.stringify({ error: `no connected account knows chat "${chatId}"` }));
      } catch (error) {
        console.error("download error:", (error && error.message) || error);
        response.writeHead(502, { "content-type": "application/json" });
        response.end(JSON.stringify({ error: (error && error.message) || "download failed" }));
      }
    });
    return;
  }
  if(url.pathname!=="/"){response.writeHead(404);return response.end();}
  if(selected&&slots.get(selected)?.status==="idle")restore(selected);
  const cards = [...slots.entries()].filter(([id])=>owners.allowed(id,personal.principal.user_id)).map(([id, item]) => `<div class=card><b>${escape(id)}</b><p>${escape(item.status)}${item.name ? `: ${escape(item.name)}` : ""}</p>${item.qr ? `<img class=qr src="${item.qr}" alt="WhatsApp login QR"><p>WhatsApp → Настройки → Связанные устройства → Привязка устройства</p>` : ""}${item.status === "error" ? `<a href="${publicPrefix}/start?slot=${encodeURIComponent(id)}"><button>Повторить вход</button></a>` : ""}${["connecting", "waiting"].includes(item.status) ? `<a href="${publicPrefix}/cancel?slot=${encodeURIComponent(id)}"><button>Отменить вход</button></a><script>setTimeout(()=>location.reload(),3000)</script>` : ""}</div>`).join("");
  response.setHeader("content-type", "text/html; charset=utf-8"); response.end(page(`<a href="${publicPrefix}/new"><button>Показать QR-код</button></a>${cards}`));
}).listen(port, "127.0.0.1");
