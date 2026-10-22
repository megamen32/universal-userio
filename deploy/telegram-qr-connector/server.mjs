import http from "node:http";
import {EnrollmentOwners,enrollmentPrincipal} from "./enrollment-owner.mjs";
import { spawnSync } from "node:child_process";
import { appendFileSync, chmodSync, createWriteStream, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, renameSync, rmdirSync, statSync, unlinkSync, writeFileSync } from "node:fs";
import { Api, TelegramClient } from "telegram";
import { StringSession } from "telegram/sessions/index.js";
import { NewMessage, Raw } from "telegram/events/index.js";
// GramJS 2.26 events/index.js exports only Raw/NewMessage(NewEvent); the
// EditedMessage builder must come straight from its own CommonJS module.
import { EditedMessage } from "telegram/events/EditedMessage.js";
import { createHash, createHmac, timingSafeEqual } from "node:crypto";
import QRCode from "qrcode";
import { bodyAndAttachments, loadWhisperApiKey, telegramAudioDescriptor, transcribeTelegramAudio } from "./transcription.mjs";
import { telegramConversationKind, telegramGroupRoutingAttachment } from "./group-routing.mjs";
import { buildAgentDeliverEvent, isNumericTelegramPeerAllowed } from "./agent-deliver.mjs";
import { publicIngressState } from "./ingress-state.mjs";
import { syncContactProfiles } from "./contact-index.mjs";
import { resolveTelegramContact, peerProfile } from "./contact-resolve.mjs";
import { mirrorMetadata, confirmedDeletedIds, providerUnreadCount } from "./mirror-metadata.mjs";
import { loginAuthorized, normalizeLoginCode, normalizeLoginPhone } from "./login-api.mjs";
import { acknowledgeRead } from './read-receipt.mjs';
import { readDialogFolders } from './dialog-folders.mjs';
import { editOwnedTelegramText, TelegramEditRefused } from './message-edit.mjs';
import { sendTelegramReply, TelegramReplyRefused } from './message-reply.mjs';
import { authRetryDelaySeconds, probeAuthorization } from './session-auth.mjs';
import { PromisedNetSockets } from 'telegram/extensions/PromisedNetSockets.js';
import { httpConnectOptions } from './http-connect-socket.mjs';
import { logoutTelegramClient } from './account-disconnect.mjs';
import { entityLabel, entityName, resolveDialogLabel } from "./dialog-label.mjs";
import { TelegramAdminJournal, executeTelegramAdmin, prepareTelegramAdmin } from "./telegram-admin.mjs";
import { assertDeclaredMediaWithinLimit, boundedMediaLimit } from "./media-download-limit.mjs";
import { SendAttemptLedger, attemptState } from "./send-attempts.mjs";
import { attachmentResponseHeaders } from "./attachment-response.mjs";
import { tmpdir } from "node:os";
import { join as joinPath } from "node:path";
import { Transform, pipeline } from "node:stream";

const port = Number(process.env.PORT || 18095);
const publicPrefix = (process.env.PUBLIC_PREFIX || "").replace(/\/$/, "");
const stateDir = "/var/lib/universal-userio/telegram-qr";
const keyPath = "/var/lib/universal-userio/secrets/telegram-qr.agekey";
const sessionsDir = `${stateDir}/sessions`;
const userIoUrl = process.env.UNIVERSAL_USERIO_URL || "http://127.0.0.1:18093";
// Optional non-interactive 2FA password (overpod-style): answered locally via
// SRP, never persisted. The interactive page form is the default path.
const env2faPassword = (process.env.TELEGRAM_2FA_PASSWORD || process.env.USERIO_TELEGRAM_2FA_PASSWORD || "").trim();
// Live ingestion leg: backfill recent dialogs and keep a connected client per
// saved session, pushing incoming messages into the UserIO inbox.
const routeId = (process.env.USERIO_TELEGRAM_ROUTE_ID || "telegram").trim();
const syncDialogs = Number(process.env.TG_SYNC_DIALOGS || 20);
const syncPerDialog = Number(process.env.TG_SYNC_PER_DIALOG || 5);
const unreadDialogLimit = Math.min(200, Math.max(20, Number(process.env.TG_UNREAD_DIALOGS || 100)));
const syncRetrySeconds = Number(process.env.TG_SYNC_RETRY_SECONDS || 30);
const slots = new Map();
const syncs = new Map();
const unreadRefreshTimers = new Map();
const unreadRefreshLast = new Map();
// Live delivery registry: slot -> { client, labelPeers: Map<chat label, entity>,
// idPeers: Map<chat id, entity> } filled during dialog sync so POST /send can
// route approved replies through the one process that owns each Telegram
// session (auth keys are never shared).
const liveSlots = new Map();
const disconnectedSlots = new Set();
let nextSlot = 1;
let promptSeq = 0;
const accountRegistrations = new Map();
const agentDeliverUrl = String(process.env.USERIO_AGENT_DELIVER_URL || "").trim();
const agentDeliverSecret = String(process.env.USERIO_AGENT_DELIVER_HMAC_SECRET || "").trim();
const agentDeliverChatId = String(process.env.USERIO_AGENT_DELIVER_CHAT_ID || "").trim();
const agentDeliverChats = String(process.env.USERIO_AGENT_DELIVER_CHATS || agentDeliverChatId || "")
  .split(",").map((value) => value.trim()).filter(Boolean);
const agentDeliverDebounceMs = Math.max(1_000, Number(process.env.USERIO_AGENT_DELIVER_DEBOUNCE_SECONDS || 300) * 1000);
const agentDeliverName = String(process.env.USERIO_AGENT_DELIVER_NAME || "secretary-excode").trim();
const agentDeliverCwd = String(process.env.USERIO_AGENT_DELIVER_CWD || "/home/roomhacker/excode").trim();
const agentDeliverHermesSessionId = String(process.env.USERIO_AGENT_DELIVER_HERMES_SESSION_ID || "").trim();
const agentDeliverIgnoredChats = String(process.env.USERIO_AGENT_IGNORED_CHATS || "")
  .split(",").map((value) => value.trim()).filter(Boolean);
const agentDeliverCallbackSecret = String(process.env.USERIO_AGENT_DELIVER_CALLBACK_HMAC_SECRET || "").trim();
const agentDeliverReceiptPath = `${stateDir}/agent-deliver-callbacks.jsonl`;
// Durable journal of approved send attempts. It binds each attempt id to the
// provider message id, to a refusal before provider IO, or to the honest
// in-flight state, so an uncertain outcome is resolvable without a resend.
const sendAttempts = new SendAttemptLedger(`${stateDir}/send-attempts.json`);
const telegramAdminJournal = new TelegramAdminJournal(`${stateDir}/admin-actions.jsonl`);
const agentDeliverDebouncePath = `${stateDir}/agent-deliver-debounce.json`;
const agentDeliverDebounce = new Map();
const agentDeliverTimers = new Map();

const whisperApiKey = loadWhisperApiKey();
if (!whisperApiKey) console.warn("Telegram auto-transcription disabled: Whisper secret unavailable");

mkdirSync(sessionsDir, { recursive: true, mode: 0o700 });
restoreAgentDeliverDebounce();
const profilePath = `${stateDir}/slot-profiles.json`;
const slotProfiles = existsSync(profilePath) ? JSON.parse(readFileSync(profilePath, "utf8")) : {};
function saveSlotProfile(slot, accountId) {
  if (accountId) slotProfiles[slot] = accountId; else delete slotProfiles[slot];
  const next = `${profilePath}.next`;
  writeFileSync(next, JSON.stringify(slotProfiles), { mode: 0o600 });
  chmodSync(next, 0o600); renameSync(next, profilePath);
}
const enrollmentOwners=new EnrollmentOwners(`${stateDir}/slot-owners.json`,process.env.USERIO_TELEGRAM_USER_ID||"user_owner");
for (const file of readdirSync(sessionsDir)) {
  const match = /^account-(\d+)\.session$/.exec(file);
  if (!match) continue;
  const id = `account-${match[1]}`;
  slots.set(id, { status: "connected", accountId: slotProfiles[id] || "" });
  nextSlot = Math.max(nextSlot, Number(match[1]) + 1);
  void syncAccount(id);
}

function secretPath(name) {
  return `${stateDir}/credentials/${name === "telegram_qr_api_id" ? "api_id" : "api_hash"}.age`;
}

function decrypt(name) {
  const result = spawnSync("age", ["--decrypt", "-i", keyPath, secretPath(name)], { encoding: "utf-8" });
  if (result.status !== 0) throw new Error("secure Telegram credential is unavailable");
  return result.stdout.trim();
}

function credentials() {
  return { apiId: Number(decrypt("telegram_qr_api_id")), apiHash: decrypt("telegram_qr_api_hash") };
}

function registerAccount(slot, user) {
  const personalHeaders=enrollmentOwners.registrationHeaders(slot);
  const displayName = [user.firstName, user.lastName].filter(Boolean).join(" ") || user.username || `Telegram ${user.id}`;
  const body = JSON.stringify({
    id: `telegram:${user.id}`,
    provider: "telegram",
    display_name: displayName,
    can_read: true,
    can_reply: Boolean(personalHeaders),
    credential_ref: `telegram-qr:${slot}`,
    enabled: true,
  });
  return new Promise((resolve, reject) => {
    const target = new URL("/v1/accounts", userIoUrl);
    const request = http.request(target, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(body),
        ...(personalHeaders||{Authorization:`Bearer ${process.env.USERIO_API_TOKEN || ""}`}),
      },
    }, (response) => {
      response.resume();
      response.on("end", () => response.statusCode === 202 ? resolve(displayName) : reject(new Error(`UserIO returned HTTP ${response.statusCode}`)));
    });
    request.setTimeout(10000, () => request.destroy(new Error("Account registration timeout; reconcile before retry")));
    request.once("error", reject);
    request.end(body);
  });
}

// Read through the same owner-scoped API used by the browser. Never infer
// ownership from a Telegram identity observed in another user's live slot.
function registeredAccounts(headers) {
  return new Promise((resolve, reject) => {
    const request = http.get(new URL("/v1/accounts", userIoUrl), { headers }, response => {
      let body = "";
      response.on("data", chunk => { body += chunk; if (body.length > 1024 * 1024) request.destroy(new Error("Account list too large")); });
      response.on("end", () => {
        try {
          if (response.statusCode !== 200) throw new Error(`UserIO returned HTTP ${response.statusCode}`);
          resolve(JSON.parse(body).accounts || []);
        } catch (error) { reject(error); }
      });
    });
    request.setTimeout(5000, () => request.destroy(new Error("Account lookup timeout")));
    request.on("error", reject);
  });
}

function normalizeTelegramChatId(value) {
  const raw = String(value || "").trim();
  if (raw.startsWith("-100")) return raw.slice(4);
  return raw.startsWith("-") ? raw.slice(1) : raw;
}

function normalizeChatLabel(value) {
  return String(value || "").trim().toLocaleLowerCase("ru-RU");
}

function agentDeliverEnabledFor(chatKey, label) {
  if (!agentDeliverUrl || !agentDeliverSecret || agentDeliverChats.length === 0) return false;
  return isNumericTelegramPeerAllowed(chatKey, agentDeliverChats);
}

function userIoEventAllowed({ accountId, chatKey, conversationId, envelope }) {
  const body = JSON.stringify({ source: "telegram", account_id: accountId,
    peer_id: chatKey, conversation_id: conversationId, message_id: envelope.message_id });
  return new Promise((resolve, reject) => {
    const target = new URL("/v1/workspace/policy/evaluate", userIoUrl);
    const request = http.request(target, { method: "POST", headers: {
      "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body),
      Authorization: `Bearer ${process.env.USERIO_API_TOKEN || ""}`,
    }}, (response) => {
      let data = ""; response.on("data", (chunk) => { data += chunk; });
      response.on("end", () => {
        if (response.statusCode !== 200) return reject(new Error(`UserIO policy HTTP ${response.statusCode}`));
        try { resolve(JSON.parse(data).allowed === true); } catch (error) { reject(error); }
      });
    });
    request.once("error", reject); request.setTimeout(10000, () => request.destroy(new Error("UserIO policy timeout"))); request.end(body);
  });
}

function hmacV2(secret, method, path, timestamp, idempotencyKey, body) {
  const digest = createHash("sha256").update(body).digest("hex");
  const canonical = [method.toUpperCase(), path, timestamp, idempotencyKey, digest].join("\n");
  return `sha256=${createHmac("sha256", secret).update(canonical).digest("hex")}`;
}

function postAgentDeliver(chatKey, label, envelope, messageCount = 1) {
  if (!agentDeliverEnabledFor(chatKey, label)) return Promise.resolve(null);
  const normalizedChatId = normalizeTelegramChatId(chatKey);
  const event = buildAgentDeliverEvent({
    normalizedChatId, peerId: chatKey, label, envelope, messageCount,
    accountId: envelope.account_id || "",
    quietSeconds: Math.round(agentDeliverDebounceMs / 1000),
    agentName: agentDeliverName, agentCwd: agentDeliverCwd,
    hermesSessionId: agentDeliverHermesSessionId,
    ignoredChats: agentDeliverIgnoredChats,
  });
  const eventId = event.event_id;
  const body = JSON.stringify(event);
  const target = new URL(agentDeliverUrl);
  const timestamp = String(Math.floor(Date.now() / 1000));
  const signature = hmacV2(agentDeliverSecret, "POST", target.pathname, timestamp, eventId, body);
  return new Promise((resolve, reject) => {
    const request = http.request(target, { method: "POST", headers: {
      "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body),
      "X-Webhook-Timestamp": timestamp, "X-Webhook-Signature": signature, "Idempotency-Key": eventId,
    }}, (response) => {
      let data = ""; response.on("data", (chunk) => { data += chunk; });
      response.on("end", () => response.statusCode === 202 ? resolve(data ? JSON.parse(data) : true) : reject(new Error(`agent-deliver HTTP ${response.statusCode}: ${data.slice(0,200)}`)));
    });
    request.once("error", reject); request.setTimeout(10000, () => request.destroy(new Error("agent-deliver request timeout"))); request.end(body);
  });
}

function persistAgentDeliverDebounce() {
  const state = Object.fromEntries(agentDeliverDebounce.entries());
  writeFileSync(agentDeliverDebouncePath, JSON.stringify(state, null, 2) + "\n", { mode: 0o600 });
}

function scheduleAgentDeliverTimer(key) {
  const existing = agentDeliverTimers.get(key);
  if (existing) clearTimeout(existing);
  const entry = agentDeliverDebounce.get(key);
  if (!entry) return;
  const waitMs = Math.max(0, entry.updatedAt + agentDeliverDebounceMs - Date.now());
  const timer = setTimeout(async () => {
    agentDeliverTimers.delete(key);
    const current = agentDeliverDebounce.get(key);
    if (!current) return;
    if (Date.now() < current.updatedAt + agentDeliverDebounceMs) {
      scheduleAgentDeliverTimer(key);
      return;
    }
    try {
      if (await userIoEventAllowed(current)) {
        await postAgentDeliver(current.chatKey, current.label, current.envelope, current.messageCount);
      }
      agentDeliverDebounce.delete(key);
      persistAgentDeliverDebounce();
      console.log(`agent-deliver quiet ${current.label}: ${current.messageCount} message(s), last ${current.envelope.message_id}`);
    } catch (error) {
      console.error(`agent-deliver quiet ${current.label}:`, (error && error.message) || error);
      current.updatedAt = Date.now();
      persistAgentDeliverDebounce();
      scheduleAgentDeliverTimer(key);
    }
  }, waitMs);
  if (typeof timer.unref === "function") timer.unref();
  agentDeliverTimers.set(key, timer);
}

function debounceAgentDeliver(chatKey, label, envelope, accountId, conversationId) {
  if (!agentDeliverEnabledFor(chatKey, label)) return;
  const key = `${accountId}:${chatKey}`;
  const previous = agentDeliverDebounce.get(key);
  agentDeliverDebounce.set(key, {
    chatKey,
    label,
    accountId,
    conversationId,
    envelope: {
      message_id: envelope.message_id,
      account_id: accountId,
      attachments: (envelope.attachments || []).filter((item) => item.kind === "telegram_routing"),
    },
    messageCount: ((previous && previous.messageCount) || 0) + 1,
    updatedAt: Date.now(),
  });
  persistAgentDeliverDebounce();
  scheduleAgentDeliverTimer(key);
}

function restoreAgentDeliverDebounce() {
  try {
    const raw = JSON.parse(readFileSync(agentDeliverDebouncePath, "utf8"));
    for (const [key, value] of Object.entries(raw || {})) {
      if (!value || !value.chatKey || !value.accountId || !value.conversationId ||
          !(value.envelope && value.envelope.message_id)) continue;
      agentDeliverDebounce.set(key, value);
      scheduleAgentDeliverTimer(key);
    }
  } catch (error) {
    if (error && error.code !== "ENOENT") console.error("agent-deliver debounce restore:", error.message || error);
  }
}

function verifyCallbackSignature(req, body) {
  if (!agentDeliverCallbackSecret) return false;
  const timestamp = String(req.headers["x-webhook-timestamp"] || "").trim();
  const supplied = String(req.headers["x-webhook-signature"] || "").trim();
  if (!timestamp || !supplied) return false;
  const expected = `sha256=${createHmac("sha256", agentDeliverCallbackSecret).update(`${timestamp}.${body}`).digest("hex")}`;
  const a = Buffer.from(supplied); const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

function postInbox(accountId, envelope) {
  return postUserIO("/v1/messages", { route_id: routeId, account_id: accountId, message: envelope });
}
function postUserIO(path, payload) {
  const body = JSON.stringify(payload);
  return new Promise((resolve, reject) => {
    const target = new URL(path, userIoUrl);
    const request = http.request(target, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(body),
        Authorization: `Bearer ${process.env.USERIO_API_TOKEN || ""}`,
      },
    }, (response) => {
      let data = "";
      response.on("data", (chunk) => { data += chunk; });
      response.on("end", () => response.statusCode === 202
        ? resolve(JSON.parse(data))
        : reject(new Error(`UserIO returned HTTP ${response.statusCode}: ${data.slice(0, 200)}`)));
    });
    request.once("error", reject);
    request.setTimeout(20000, () => request.destroy(new Error("UserIO request timeout")));
    request.end(body);
  });
}

function telegramProxy() {
  const ip = String(process.env.TELEGRAM_PROXY_IP || "").trim();
  const port = Number(process.env.TELEGRAM_PROXY_PORT || 0);
  if (!ip || !Number.isSafeInteger(port) || port <= 0) return null;
  const secret = String(process.env.TELEGRAM_PROXY_SECRET || "").trim();
  if (secret) return { ip, port, secret, MTProxy: true };
  const proxy = { ip, port, socksType: Number(process.env.TELEGRAM_PROXY_SOCKS_TYPE || 5) };
  if (process.env.TELEGRAM_PROXY_USERNAME) proxy.username = process.env.TELEGRAM_PROXY_USERNAME;
  if (process.env.TELEGRAM_PROXY_PASSWORD) proxy.password = process.env.TELEGRAM_PROXY_PASSWORD;
  return proxy;
}

function telegramClientOptions(connectionRetries, retryDelay) {
  const options = { connectionRetries };
  if (retryDelay) options.retryDelay = retryDelay;
  const httpProxy = String(process.env.TELEGRAM_HTTP_PROXY_URL || '').trim();
  if (httpProxy) return { ...options, ...httpConnectOptions(httpProxy, PromisedNetSockets) };
  const proxy = telegramProxy();
  if (proxy) options.proxy = proxy;
  return options;
}

function newClient() {
  const { apiId, apiHash } = credentials();
  return new TelegramClient(new StringSession(""), apiId, apiHash, telegramClientOptions(3, 0));
}

function newSyncClient(sessionString) {
  const { apiId, apiHash } = credentials();
  return new TelegramClient(new StringSession(sessionString), apiId, apiHash, telegramClientOptions(5, 2000));
}

// Shared 2FA password prompt: env password (non-interactive) or the page form.
// Never dead-ends: the promise resolves when the form is submitted.
function passwordCallback(slot) {
  return async (hint) => {
    if (env2faPassword && !enrollmentOwners.owners[slot]) return env2faPassword;
    const state = slots.get(slot) || {};
    promptSeq += 1;
    return await new Promise((resolve) => {
      slots.set(slot, { ...state, status: "password-required", passwordHint: hint || "", passwordResolve: resolve, promptSeq });
    });
  };
}

// GramJS semantics: true stops the attempt, false retries.
function errorLogger() {
  let authErrors = 0;
  return async (error) => {
    const message = (error && (error.errorMessage || error.message)) || "unknown";
    console.error("Telegram auth error:", message);
    authErrors += 1;
    return authErrors >= 5;
  };
}

async function finishLogin(slot, client, user) {
  const accountId = `telegram:${user.id}`;
  const owner = enrollmentOwners.owner(slot);
  const key = `${owner}:${accountId}`;
  const previousRegistration = accountRegistrations.get(key) || Promise.resolve();
  const pending = previousRegistration.catch(() => {}).then(async () => {
    const displayName = [user.firstName, user.lastName].filter(Boolean).join(" ") || user.username || accountId;
    const file = `${sessionsDir}/${slot}.session`;
    // Publish complete bytes before the account reference can point here.
    const temporary = `${file}.next`;
    writeFileSync(temporary, client.session.save(), { mode: 0o600 });
    chmodSync(temporary, 0o600);
    renameSync(temporary, file);
    saveSlotProfile(slot, accountId);
    try {
      const headers = enrollmentOwners.registrationHeaders(slot)
        || { Authorization: `Bearer ${process.env.USERIO_API_TOKEN || ""}` };
      const accounts = await registeredAccounts(headers);
      // Public account responses intentionally omit credential references.
      // The connector persists its own verified slot-to-profile identities.
      const previous = accounts.some(account => account.provider === "telegram" && account.id === accountId);
      const retire = new Set();
      for (const [id, state] of slots) {
        if (previous && id !== slot && enrollmentOwners.allowed(id, owner)
            && (liveSlots.get(id)?.accountId || state.accountId) === accountId) retire.add(id);
      }
      await registerAccount(slot, user);
      enrollmentOwners.registered(slot);
      slots.set(slot, { status: "connected", name: displayName, accountId });
      // Registration is the commit point. A failed/unknown request keeps both
      // sessions and the previous binding available for reconciliation.
      for (const id of retire) {
        try {
          const oldFile = `${sessionsDir}/${id}.session`;
          if (existsSync(oldFile)) renameSync(oldFile, `${oldFile}.replaced-${Date.now()}`);
          const old = clearSlot(id, { preserveProfile: true });
          if (old?.client) await old.client.disconnect().catch(() => {});
        } catch (error) {
          console.error(`Telegram old session retirement failed for ${id}:`, error.message || "unknown");
        }
      }
    } catch (error) {
      console.error("Telegram account registration error:", error?.message || "unknown");
      slots.set(slot, { status: "connected-unregistered", name: displayName, accountId });
      return;
    }
    // The login client releases the key before ingestion takes ownership.
    await client.disconnect();
    void syncAccount(slot);
  });
  accountRegistrations.set(key, pending);
  try { await pending; }
  finally { if (accountRegistrations.get(key) === pending) accountRegistrations.delete(key); }
}

function failSlot(slot, error) {
  console.error("Telegram login error:", (error && (error.errorMessage || error.message)) || "unknown");
  const state = slots.get(slot) || {};
  const { passwordResolve, codeResolve, ...rest } = state;
  if (passwordResolve) passwordResolve("");
  if (codeResolve) codeResolve("");
  slots.set(slot, { ...rest, status: "error" });
}

async function startQr(slot) {
  const current = slots.get(slot);
  if (!current || ["connecting", "waiting", "connected"].includes(current.status)) return;
  slots.set(slot, { ...current, status: "connecting", mode: "qr" });
  let client;
  try {
    client = newClient();
    await client.connect();
    slots.set(slot, { ...slots.get(slot), status: "waiting", client });
    const user = await client.signInUserWithQrCode(credentials(), {
      qrCode: async ({ token }) => {
        const encoded = token.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
        const uri = `tg://login?token=${encoded}`;
        const state = slots.get(slot) || {};
        slots.set(slot, { ...state, qr: await QRCode.toDataURL(uri) });
      },
      password: passwordCallback(slot),
      onError: errorLogger(),
    });
    await finishLogin(slot, client, user);
  } catch (error) {
    failSlot(slot, error);
  } finally {
    if (client) await client.disconnect().catch(() => {});
  }
}

async function startPhone(slot) {
  const current = slots.get(slot);
  const phone = (current || {}).phone || "";
  if (!phone || ["connecting", "waiting", "code-required", "connected"].includes(current.status)) return;
  slots.set(slot, { ...current, status: "connecting", mode: "phone" });
  let client;
  try {
    client = newClient();
    await client.connect();
    const user = await client.signInUser(credentials(), {
      phoneNumber: async () => phone,
      phoneCode: async (isCodeViaApp) => {
        const state = slots.get(slot) || {};
        promptSeq += 1;
        return await new Promise((resolve) => {
          slots.set(slot, { ...state, status: "code-required", codeViaApp: !!isCodeViaApp, codeResolve: resolve, promptSeq });
        });
      },
      password: passwordCallback(slot),
      onError: errorLogger(),
    });
    await finishLogin(slot, client, user);
  } catch (error) {
    failSlot(slot, error);
  } finally {
    if (client) await client.disconnect().catch(() => {});
  }
}

function restoreSlot(slot) {
  const match = /^account-(\d+)$/.exec(slot || "");
  if (!match) return false;
  nextSlot = Math.max(nextSlot, Number(match[1]) + 1);
  if (!slots.has(slot)) {
    slots.set(slot, { status: "idle", mode: "qr" });
    void startQr(slot);
  }
  return true;
}

// --- live ingestion ---------------------------------------------------------

function setSync(slot, patch) {
  syncs.set(slot, { ...(syncs.get(slot) || { running: false, chats: 0, lastError: "", lastSyncAt: 0 }), ...patch });
}

// Lifted from megamen32/mcp-telegram@codex/read-webpage-preview-messages
// (extractMessageText): text first, then visible webpage-preview fields.
function joinVisibleText(parts, separator = "\n\n") {
  return parts
    .map((part) => (typeof part === "string" ? part.trim() : ""))
    .filter(Boolean)
    .join(separator);
}

function messageBody(message) {
  const text = message.message || "";
  if (text.length > 0) return text;
  const media = message.media;
  if (media instanceof Api.MessageMediaWebPage && media.webpage instanceof Api.WebPage) {
    return joinVisibleText([media.webpage.title, media.webpage.description]);
  }
  return "";
}

async function envelope(chatKey, label, message, client, self, options) {
  let senderEntity = message.sender || null;
  const shouldTranscribe = !options || options.transcribeAudio !== false;
  const audio = shouldTranscribe
    ? await transcribeTelegramAudio(client, message, { apiKey: whisperApiKey })
    : null;
  const normalized = bodyAndAttachments(
    { ...message, message: messageBody(message) },
    audio,
  );
  const routing = await telegramGroupRoutingAttachment({
    chatKey, groupName: label, message,
    selfId: self && self.id, selfUsername: self && self.username,
    resolveSender: async () => {
      if (!senderEntity && typeof message.getSender === "function") senderEntity = await message.getSender();
      let id = "";
      try { id = String(await client.getPeerId(senderEntity || message.fromId, true)); } catch (_error) {}
      return { id, name: senderEntity ? entityLabel(senderEntity) : String(message.postAuthor || ""),
        firstName: senderEntity && senderEntity.firstName,
        lastName: senderEntity && senderEntity.lastName,
        username: senderEntity && senderEntity.username };
    },
    resolveReply: async (replyTo) => {
      const values = await client.getMessages(message.peerId, { ids: [Number(replyTo)] });
      const parent = Array.isArray(values) ? values[0] : values;
      return { out: !!(parent && parent.out) };
    },
  });
  const attachments = normalized.attachments.slice();
  if (routing) attachments.push(routing);
  if (audio && audio.error) console.warn(`telegram audio ${chatKey}:${message.id}: ${audio.error}`);
  let chatEntity = (options && options.chatEntity) || message.chat || null;
  if (!chatEntity && typeof message.getChat === "function") {
    try { chatEntity = await message.getChat(); } catch (_error) { /* unknown stays disabled */ }
  }
  const conversationKind = telegramConversationKind(chatEntity, chatKey);
  const senderIsBot = conversationKind === "telegram_bot" || !!(senderEntity && senderEntity.bot === true);
  return {
    schema: "universal.inbox.message.v1",
    source: "telegram",
    message_id: options && options.accountId
      ? `${options.accountId}|${chatKey}:${message.id}` : `${chatKey}:${message.id}`,
    sender: label,
    peer_id: chatKey,
    provider_reply_to: String((message.replyTo || {}).replyToMsgId || ''),
    provider_thread_id: String((message.replyTo || {}).replyToTopId || ''),
    conversation_kind: conversationKind,
    sender_is_bot: senderIsBot,
    ...mirrorMetadata(message, options && options.dialog),
    ...(message.editDate ? { edited_at: Number(message.editDate) } : {}),
    ...(options && options.reconciliation ? { reconciliation: true } : {}),
    ...(entityName(chatEntity) ? { sender_name: entityName(chatEntity) } : {}),
    body: normalized.body.slice(0, 8000),
    ...(attachments.length ? { attachments: attachments.map((attachment, idx) => ({ ...attachment, idx })) } : {}),
  };
}

async function reconcileDialog(slot, client, accountId, labels, self, peer, chatKey, label, dialog, limit, beforeId = 0, scan = false) {
  const pageLimit = Math.min(200, Math.max(1, limit));
  // offsetId is exclusive; GetHistory never modifies Telegram read receipts.
  const messages = await client.getMessages(peer, {
    limit: pageLimit, ...(beforeId ? { offsetId: beforeId } : {}),
  });
  const unreadObservedAt = Date.now() / 1000;
  const ids = messages.filter(m => m && Number.isSafeInteger(Number(m.id)) && Number(m.id) > 0)
    .map(m => Number(m.id));
  const oldestId = ids.length ? Math.min(...ids) : 0;
  const hasMore = messages.length === pageLimit && oldestId > 0;
  let posted = 0;
  const postedIds = [];
  for (const message of messages) {
    if (!message || !message.date) continue;
    const value = await envelope(chatKey, label, message, client, self,
      { transcribeAudio: false, accountId, reconciliation: true, chatEntity: peer, dialog });
    if (!value.body && !(value.attachments && value.attachments.length)) continue;
    await postInbox(accountId, value);
    posted += 1;
    postedIds.push(Number(message.id));
  }
  const unread = providerUnreadCount(dialog);
  const state = await postUserIO("/v1/telegram/mirror-state", {
    account_id: accountId, peer_id: chatKey,
    read_inbox_max_id: Number(dialog && dialog.readInboxMaxId || 0),
    read_outbox_max_id: Number(dialog && dialog.readOutboxMaxId || 0),
    ...(unread === null ? {} : { unread_count: unread, unread_observed_at: unreadObservedAt }),
    history_ids: messages.filter(m => m && m.date).map(m => Number(m.id)),
    ...(scan ? {
      scan_before_id: beforeId, scan_oldest_id: oldestId,
      scan_has_more: hasMore, scan_fetched: messages.length,
    } : {}),
  });
  if (state.missing_ids && state.missing_ids.length) {
    const requested = state.missing_ids;
    const inputPeer = await client.getInputEntity(peer);
    const ids = requested.map(id => new Api.InputMessageID({id}));
    const response = inputPeer.className === "InputPeerChannel"
      ? await client.invoke(new Api.channels.GetMessages({
          channel:new Api.InputChannel({channelId:inputPeer.channelId,accessHash:inputPeer.accessHash}), id:ids}))
      : await client.invoke(new Api.messages.GetMessages({id:ids}));
    const exact = response.messages;
    const deleted = confirmedDeletedIds(requested, exact);
    if (deleted.length) await postUserIO("/v1/telegram/mirror-state",
      {account_id:accountId, peer_id:chatKey, deleted_ids:deleted});
  }
  return { posted, fetched: messages.length, oldest_id: oldestId, has_more: hasMore,
    message_ids: postedIds };
}

async function ingestProviderState(slot, client, accountId, update) {
  const name = update.className;
  if (name === "UpdateUserName") {
    const live = liveSlots.get(slot);
    if (live) {
      try {
        const user = await client.getEntity(update.userId);
        const peerKey = String(await client.getPeerId(user, true));
        const label = resolveDialogLabel(user, "");
        for (const [chatKey, peer] of live.labelPeers.idPeers) {
          if (chatKey !== peerKey || !peer || peer.className !== "User" || !label || label === peerKey) continue;
          live.dialogLabels.set(chatKey, label);
          live.labelPeers.labelPeers.set(label, user);
        }
        if (label && label !== peerKey) {
          await postUserIO("/v1/contact-profiles", { account_id: accountId, profiles: [{
            peer_id: peerKey, username: String(user.username || ""),
            first_name: String(user.firstName || ""), last_name: String(user.lastName || ""),
            phone: String(user.phone || ""),
          }] });
        }
      } catch (error) {
        console.warn(`telegram name update ${slot}:`, (error && error.message) || error);
      }
    }
    return;
  }
  const inbox = name === "UpdateReadHistoryInbox" || name === "UpdateReadChannelInbox";
  const outbox = name === "UpdateReadHistoryOutbox" || name === "UpdateReadChannelOutbox";
  if (inbox || outbox) {
    const peer = update.peer || new Api.PeerChannel({channelId:update.channelId});
    const chatKey = String(await client.getPeerId(peer, true));
      await postUserIO("/v1/telegram/mirror-state", {account_id:accountId, peer_id:chatKey,
        [inbox ? "read_inbox_max_id" : "read_outbox_max_id"]:Number(update.maxId)});
      scheduleUnreadCountRefresh(slot, client, accountId, chatKey, peer);
  } else if (name === "UpdateDeleteChannelMessages") {
    const peer = new Api.PeerChannel({channelId:update.channelId});
    await postUserIO("/v1/telegram/mirror-state", {account_id:accountId,
      peer_id:String(await client.getPeerId(peer,true)), deleted_ids:update.messages.slice(0,200)});
  } else if (name === "UpdateDeleteMessages") {
    // Non-channel IDs are account-global; apply only to known non-channel
    // peers on this account. Unknown peers are repaired by reconciliation.
    const live = liveSlots.get(slot);
    if (live) for (const [chatKey, entity] of live.labelPeers.idPeers) {
      if (entity && entity.className === "Channel") continue;
      await postUserIO("/v1/telegram/mirror-state", {account_id:accountId,
        peer_id:chatKey, deleted_ids:update.messages.slice(0,200)});
    }
  }
}

async function refreshOneUnreadCount(client, accountId, chatKey, peer) {
  const input = await client.getInputEntity(peer);
  const detail = await client.invoke(new Api.messages.GetPeerDialogs({
    peers: [new Api.InputDialogPeer({ peer: input })],
  }));
  const dialog = detail.dialogs && detail.dialogs[0];
  if (!dialog) return;
  const unread = providerUnreadCount(dialog);
  if (unread === null) return;
  await postUserIO("/v1/telegram/mirror-state", {
    account_id: accountId, peer_id: chatKey, unread_count: unread,
    unread_observed_at: Date.now() / 1000,
    read_inbox_max_id: Number(dialog.readInboxMaxId || 0),
    read_outbox_max_id: Number(dialog.readOutboxMaxId || 0),
  });
}

function scheduleUnreadCountRefresh(slot, client, accountId, chatKey, peer) {
  if (!peer || !chatKey) return;
  const key = accountId + "|" + chatKey;
  if (unreadRefreshTimers.has(key)) return;
  const nextAllowed = (unreadRefreshLast.get(key) || 0) + 15000;
  const delay = Math.max(2500, nextAllowed - Date.now());
  const timer = setTimeout(async () => {
    unreadRefreshTimers.delete(key);
    unreadRefreshLast.set(key, Date.now());
    if (unreadRefreshLast.size > 500) {
      unreadRefreshLast.delete(unreadRefreshLast.keys().next().value);
    }
    try {
      await refreshOneUnreadCount(client, accountId, chatKey, peer);
    } catch (error) {
      console.error("Telegram unread refresh:", (error && error.message) || error);
    }
  }, delay);
  if (typeof timer.unref === "function") timer.unref();
  unreadRefreshTimers.set(key, timer);
}

async function refreshUnreadSnapshot(client, accountId) {
  const dialogs = await client.getDialogs({ limit: unreadDialogLimit });
  const observedAt = Date.now() / 1000;
  const rows = dialogs.flatMap(dialog => {
    const raw = dialog.dialog || dialog;
    const unread = providerUnreadCount(raw);
    if (unread === null || !String(dialog.id || "").replace(/^-/, "").match(/^\d+$/)) return [];
    return [{
      peer_id: String(dialog.id), unread_count: unread,
      read_inbox_max_id: Number(raw.readInboxMaxId || 0),
      read_outbox_max_id: Number(raw.readOutboxMaxId || 0),
    }];
  });
  return postUserIO("/v1/telegram/unread-snapshot", {
    account_id: accountId, dialogs: rows, observed_at: observedAt,
  });
}

async function backfillDialogs(slot, client, accountId, dialogLabels, labelPeers, self, limit) {
  const dialogs = await client.getDialogs({ limit: limit || syncDialogs });
  let chats = 0;
  for (const dialog of dialogs) {
    try {
      const chatKey = String(dialog.id);
      const label = resolveDialogLabel(dialog.entity, dialogLabels.get(chatKey));
      dialogLabels.set(chatKey, label);
      if (labelPeers) {
        if (!labelPeers.idPeers.has(chatKey)) labelPeers.idPeers.set(chatKey, dialog.entity);
        if (dialog.entity && !labelPeers.labelPeers.has(label)) labelPeers.labelPeers.set(label, dialog.entity);
      }
          const page = await reconcileDialog(slot, client, accountId, dialogLabels, self,
        dialog.entity, chatKey, label, dialog.dialog, syncPerDialog);
      chats += 1;
      setSync(slot, { status: `backfill ${chats}/${dialogs.length}`, chats, lastSyncAt: Date.now() });
      if (page.posted) console.log(`sync ${slot}: ${label} +${page.posted} messages`);
      await new Promise((resolve) => setTimeout(resolve, 250));
    } catch (error) {
      console.error(`sync ${slot} dialog error:`, (error && error.message) || error);
    }
  }
  return chats;
}

async function ingestLive(slot, client, accountId, dialogLabels, self, event, isEdit = false) {
  const message = event.message;
  if (!message) return;
  let chatKey = "";
  try {
    chatKey = String(await client.getPeerId(message.peerId, true));
  } catch (error) { /* fall back to the event chat id below */ }
  if (!chatKey || chatKey === "undefined") chatKey = String(event.chatId != null ? event.chatId : "");
  if (!chatKey) return;
  let chatEntity = message.chat || null;
  if (!chatEntity && typeof message.getChat === "function") chatEntity = await message.getChat();
  const label = resolveDialogLabel(chatEntity, dialogLabels.get(chatKey));
  dialogLabels.set(chatKey, label);
  const live = liveSlots.get(slot);
  if (live && chatEntity && label && label !== "telegram") {
    live.labelPeers.labelPeers.set(label, chatEntity);
    live.labelPeers.idPeers.set(chatKey, chatEntity);
  }
  const inboxMessage = await envelope(chatKey, label, message, client, self, { accountId, chatEntity });
  if (!inboxMessage.body) return;
  const posted = await postInbox(accountId, inboxMessage);
  if (!isEdit && !message.out) {
    const peer = chatEntity || (live && live.labelPeers.idPeers.get(chatKey));
    scheduleUnreadCountRefresh(slot, client, accountId, chatKey, peer);
  }
  // An edit rewrites an existing mirror message; it is not a new arrival and
  // must not reschedule agent delivery for the chat.
  if (!isEdit && !message.out) debounceAgentDeliver(chatKey, label, inboxMessage, accountId, posted.conversation_id);
  setSync(slot, { lastSyncAt: Date.now() });
  console.log(`sync ${slot}: ${isEdit ? "live edit" : "live"} ${label} msg ${message.id}`);
}

async function syncAccount(slot) {
  const running = syncs.get(slot);
  if (running && running.running) return;
  setSync(slot, { running: true, status: "starting" });
  const file = `${sessionsDir}/${slot}.session`;
  while (!disconnectedSlots.has(slot)) {
    let client;
    let authTerminal = false;
    try {
      client = newSyncClient(readFileSync(file, "utf8"));
      setSync(slot, { status: "connecting" });
      await client.connect();
      // A revoked auth key and a slow network both make the GramJS boolean
      // false. Classify the real provider answer so the owner gets a relogin
      // instruction instead of an endless "not connected" retry loop.
      const auth = await probeAuthorization(client, Api);
      if (!auth.authorized) {
        setSync(slot, { status: auth.terminal ? "relogin-required" : "connecting",
          lastError: `auth ${auth.code}`, authCode: auth.code });
        const failure = new Error(`session is not authorized (${auth.provider_code || auth.code})`);
        failure.authTerminal = auth.terminal;
        failure.authCode = auth.code;
        authTerminal = auth.terminal;
        throw failure;
      }
      const me = await client.getMe();
      if (disconnectedSlots.has(slot)) break;
      const accountId = `telegram:${me.id}`;
      saveSlotProfile(slot, accountId);
      const displayName = [me.firstName, me.lastName].filter(Boolean).join(" ") || me.username || accountId;
      const dialogLabels = new Map();
      const labelPeers = { labelPeers: new Map(), idPeers: new Map() };
      liveSlots.set(slot, { client, labelPeers, accountId, dialogLabels });
      slots.set(slot, { ...(slots.get(slot) || {}), status: "connected", name: displayName, accountId });
      // Attach push ingestion before reconciliation. A slow historical media
      // item must not postpone delivery of genuinely new messages.
      client.addEventHandler(
        (event) => { ingestLive(slot, client, accountId, dialogLabels, me, event).catch((error) => console.error(`sync ${slot} live error:`, (error && error.message) || error)); },
        new NewMessage({}),
      );
      // Edited messages never fire NewMessage; without this handler a bot
      // editing its own status text stays stale in the mirror forever.
      client.addEventHandler(
        (event) => { ingestLive(slot, client, accountId, dialogLabels, me, event, true).catch((error) => console.error(`sync ${slot} live edit error:`, (error && error.message) || error)); },
        new EditedMessage({}),
      );
      client.addEventHandler((update) => {
        ingestProviderState(slot, client, accountId, update).catch(error =>
          console.error(`sync ${slot} state error:`, error.message || error));
      }, new Raw({}));
      await syncContactProfiles(client, accountId, postUserIO).catch(error => console.error(`contact index: ${error.message || error}`));
          const chats = await backfillDialogs(slot, client, accountId, dialogLabels, labelPeers, me);
          await refreshUnreadSnapshot(client, accountId).catch(error =>
            console.error(`unread snapshot startup: ${error.message || error}`));
      if (disconnectedSlots.has(slot)) break;
      setSync(slot, { status: "live", chats, lastError: "", lastSyncAt: Date.now() });
      console.log(`sync ${slot}: live as ${accountId}, ${chats} chats backfilled`);
      // GramJS 2.26 has no runUntilDisconnected; the client keeps itself
      // connected (auto-reconnect) and the server event loop keeps the
      // process alive. Watch liveness with a light periodic call and
      // reconcile recent messages every few minutes: push updates can be
      // lost when another client shares the session auth key.
      for (let beat = 1; ; beat += 1) {
        await new Promise((resolve) => setTimeout(resolve, 60000));
        if (disconnectedSlots.has(slot)) break;
        await client.getMe();
        if (beat % 60 === 0) await syncContactProfiles(client, accountId, postUserIO).catch(error => console.error(`contact index: ${error.message || error}`));
            if (beat % 5 === 0) {
              await refreshUnreadSnapshot(client, accountId).catch(error =>
                console.error(`unread snapshot periodic: ${error.message || error}`));
              await backfillDialogs(slot, client, accountId, dialogLabels, (liveSlots.get(slot) || {}).labelPeers || null, me, 20);
            }
        setSync(slot, { status: "live", lastError: "", lastSyncAt: Date.now() });
      }
    } catch (error) {
      if (disconnectedSlots.has(slot)) break;
      setSync(slot, { status: authTerminal ? "relogin-required" : "retrying",
        lastError: String((error && error.message) || error),
        authCode: (error && error.authCode) || "" });
      console.error(`sync ${slot} error:`, (error && error.message) || error);
      if (authTerminal) console.error(`sync ${slot}: saved session needs a fresh login; no send can use it`);
    } finally {
      liveSlots.delete(slot);
      if (client) await client.disconnect().catch(() => {});
    }
    // A revoked session never heals by reconnecting: only a new login can
    // replace it, so wait long instead of repeating the same refusal.
    await new Promise((resolve) => setTimeout(resolve, authRetryDelaySeconds(
      syncRetrySeconds, { terminal: authTerminal }) * 1000));
  }
}

// --- operator surface -------------------------------------------------------

function readBody(req) {
  return new Promise((resolve) => {
    let body = "";
    req.on("data", (chunk) => { body += chunk; });
    req.on("end", () => resolve(new URLSearchParams(body)));
  });
}

function safeFilename(value, fallback = "telegram-media") {
  const name = String(value || "").trim().replace(/[\\/\r\n\0"]/g, "_");
  return name || fallback;
}

function mediaDownloadDescriptor(message) {
  const audio = telegramAudioDescriptor(message);
  if (audio) return { contentType: audio.contentType, filename: audio.filename };
  const document = message && message.media ? message.media.document : null;
  if (document) {
    const attributes = Array.isArray(document.attributes) ? document.attributes : [];
    const filenameAttribute = attributes.find((attr) =>
      String((attr && attr.className) || (attr && attr.constructor && attr.constructor.name) || "").includes("DocumentAttributeFilename")
    );
    return {
      contentType: String(document.mimeType || document.mime_type || "application/octet-stream"),
      filename: safeFilename((filenameAttribute && (filenameAttribute.fileName || filenameAttribute.file_name)) || "", `telegram-${(message && message.id) || "document"}`),
    };
  }
  if (message && message.media && message.media.photo) {
    return { contentType: "image/jpeg", filename: `telegram-${(message && message.id) || "photo"}.jpg` };
  }
  return { contentType: "application/octet-stream", filename: `telegram-${(message && message.id) || "media"}` };
}

function readJsonBody(req, maxBytes = 64 * 1024) {
  return new Promise((resolve, reject) => {
    let raw = "";
    req.on("data", (chunk) => {
      raw += chunk;
      if (raw.length > maxBytes) req.destroy(new Error("request body too large"));
    });
    req.once("error", reject);
    req.on("end", () => {
      try { resolve(JSON.parse(raw || "{}")); }
      catch (error) { reject(error); }
    });
  });
}

function appConfigNumber(node, key) {
  if (!node || typeof node !== "object") return null;
  if (node.key === key && node.value && node.value.className === "JsonNumber") {
    const value = Number(node.value.value);
    return Number.isSafeInteger(value) && value > 0 ? value : null;
  }
  const values = Array.isArray(node.value) ? node.value : [];
  for (const item of values) {
    const found = appConfigNumber(item, key);
    if (found) return found;
  }
  if (node.config) return appConfigNumber(node.config, key);
  return null;
}

async function telegramUploadLimit(client) {
  const [me, result] = await Promise.all([
    client.getMe(), client.invoke(new Api.help.GetAppConfig({ hash: 0 })),
  ]);
  const key = me && me.premium ? "upload_max_fileparts_premium" : "upload_max_fileparts_default";
  const parts = appConfigNumber(result && result.config, key);
  const captionKey = me && me.premium ? "caption_length_limit_premium" : "caption_length_limit_default";
  const captionLength = appConfigNumber(result && result.config, captionKey);
  if (!parts || !captionLength) throw new Error("Telegram account media limits are unavailable");
  return { maxBytes: parts * 524288, captionLength };
}

function telegramMessageLocator(payload) {
  const ref = String((payload && payload.message_id) || "").trim();
  let chatKey = "";
  let messageId = 0;
  const split = ref.lastIndexOf(":");
  if (split > 0) {
    chatKey = ref.slice(0, split);
    messageId = Number(ref.slice(split + 1));
  } else {
    messageId = Number(ref);
  }
  if (!Number.isSafeInteger(messageId) || messageId <= 0) throw new Error("valid message_id is required");
  const accountSplit = chatKey.lastIndexOf("|");
  const scopedAccountId = accountSplit >= 0 ? chatKey.slice(0, accountSplit) : "";
  if (accountSplit >= 0) chatKey = chatKey.slice(accountSplit + 1);
  return {
    chatKey,
    messageId,
    chat: String((payload && payload.chat) || "").trim(),
    chatId: String((payload && payload.chat_id) || "").trim(),
    accountId: scopedAccountId || String((payload && payload.account_id) || "").trim(),
  };
}

async function downloadTelegramMedia(payload) {
  const locator = telegramMessageLocator(payload);
  const maxBytes = boundedMediaLimit(payload?.max_bytes);
  const candidates = [...liveSlots].filter(([, item]) =>
    !locator.accountId || item.accountId === locator.accountId
  );
  for (const [slot, item] of candidates) {
    if (!item.client) continue;
    const peers = item.labelPeers;
    const peer = (locator.chatKey && peers.idPeers.get(locator.chatKey))
      || (locator.chatId && peers.idPeers.get(locator.chatId))
      || (locator.chat && peers.labelPeers.get(locator.chat))
      || null;
    if (!peer) continue;
    const messages = await item.client.getMessages(peer, { ids: [locator.messageId] });
    const message = messages && messages[0];
    if (!message) continue;
    if (!message.media) throw new Error(`message ${locator.messageId} has no media`);
    assertDeclaredMediaWithinLimit(message, maxBytes);
    const bytes = await item.client.downloadMedia(message);
    if (!bytes || !bytes.length) throw new Error(`message ${locator.messageId} media download returned empty data`);
    if (maxBytes !== null && bytes.length > maxBytes) throw new Error("Telegram media exceeds the bounded download limit");
    return { slot, bytes: Buffer.from(bytes), ...mediaDownloadDescriptor(message) };
  }
  throw new Error(`no connected Telegram account can resolve message ${locator.messageId}`);
}

function publicState() {
  return publicIngressState(slots, syncs, liveSlots);
}

function clearSlot(slotId, { preserveProfile = false } = {}) {
  disconnectedSlots.add(slotId);
  const timer = unreadRefreshTimers.get(slotId);
  if (timer) clearTimeout(timer);
  unreadRefreshTimers.delete(slotId);
  const live = liveSlots.get(slotId);
  liveSlots.delete(slotId);
  slots.delete(slotId);
  syncs.delete(slotId);
  if (!preserveProfile) {
    enrollmentOwners.remove(slotId);
    saveSlotProfile(slotId, "");
  }
  try { unlinkSync(`${sessionsDir}/${slotId}.session`); }
  catch (error) { if (error?.code !== "ENOENT") throw error; }
  return live;
}

const pageShell = `<!doctype html><html lang="ru"><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Аккаунты Telegram · UserIO</title><link rel="stylesheet" href="/assets/userio-onboarding.css"><script src="/assets/userio-onboarding-theme.js"></script></head><body class="userio-setup-page"><div class="userio-setup-shell"><header class="userio-setup-header"><a class="userio-brand" href="/accounts?service=telegram"><span class="userio-brand-mark" aria-hidden="true">U</span><span>UserIO</span></a><nav class="userio-setup-nav"><a href="/accounts?service=telegram">&#8592; Все аккаунты</a></nav></header><main class="userio-setup-main"><h1 class="userio-setup-title">Аккаунты и сеансы Telegram</h1><p class="userio-setup-lead">Подключённые аккаунты и сохранённые входы. Старые сеансы без имени отмечены отдельно.</p><p class="userio-note" id="account-summary" aria-live="polite"></p><section class="userio-setup-panel"><h2>Добавить аккаунт</h2><form method=post action="__PREFIX__/phone" data-async=1><input name=phone type=tel placeholder="+79990001111" autocomplete=off aria-label="Номер телефона"><button class="userio-setup-button" type=submit>Войти по номеру</button></form><p><a class="userio-setup-button userio-setup-secondary" href="__PREFIX__/new">Показать QR-код</a></p><details><summary>Импортировать существующий сеанс</summary><p class="userio-note">Локально преобразуйте файл через <code>tg-convert путь-к-файлу --to gramjs-string --print</code> и вставьте результат. Остановите другие клиенты этого аккаунта перед импортом: одну Telegram-сессию должен обслуживать один живой владелец.</p><form id="import-session-form"><textarea name="session" required autocomplete="off" spellcheck="false" aria-label="GramJS session string" rows="4"></textarea><button class="userio-setup-button" type="submit">Проверить и сохранить</button><p class="userio-note" id="import-session-status" aria-live="polite"></p></form></details></section><div id=cards aria-live="polite"></div><details id="old-sessions" hidden><summary>Старые отключённые сеансы</summary><p class="userio-note">Эти входы уже отозваны Telegram. Привязанные профили показаны выше; история сохранена.</p><div id="old-cards"></div></details></main></div><script type="module">
import { telegramAccountCard } from "__PREFIX__/account-card.mjs";
var prefix = location.pathname.replace(/\\/$/, "");
var seen = new Map();
var accounts = new Map();
var accountsRefreshAt = 0;
function esc(v){return String(v==null?"":v).replace(/[&<>"]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c];});}
function card(s){
  var a = function(p){ return prefix + p; };
  var view = telegramAccountCard(s, accounts.get(s.accountId));
  var html = '<section class="userio-setup-panel" id="card-'+esc(s.id)+'"><h2>'+esc(view.title)+'</h2><p class="userio-status" role="status">'+esc(view.status)+'</p>';
  if (view.sessionLabel) html += '<p class="userio-note">'+esc(view.sessionLabel)+'</p>';
  if (view.note) html += '<p class="userio-note">'+esc(view.note)+'</p>';
  if (view.reconnect) html += '<a class="userio-setup-button" href="'+a('/new')+'">Переподключить Telegram</a>';
  if (view.action) html += '<div class="userio-setup-actions"><button class="userio-setup-button userio-setup-secondary" type="button" data-account-action="'+esc(view.action.kind)+'" data-account-id="'+esc(view.action.accountId||'')+'" data-slot="'+esc(view.action.slot||'')+'" data-label="'+esc(view.title)+'">'+esc(view.action.label)+'</button><p class="userio-note" data-action-status aria-live="polite"></p></div>';
  if (s.qr) html += '<img class="userio-qr" src="'+s.qr+'" alt="QR-код входа в Telegram"><p class="userio-note">Telegram \\u2192 Настройки → Устройства → Подключить устройство</p>';
  if (s.status === "code-required") html += '<form method=post action="'+a('/code?slot='+encodeURIComponent(s.id))+'" data-async=1><p>Введите код входа'+(s.codeViaApp?" (пришёл в Telegram)":" (SMS)")+'</p><input name=code inputmode=numeric autocomplete=one-time-code autofocus><button class="userio-setup-button" type=submit>Подтвердить код</button></form>';
  if (s.status === "password-required") html += '<form method=post action="'+a('/password?slot='+encodeURIComponent(s.id))+'" data-async=1><p>Облачный пароль'+(s.passwordHint?" (подсказка: "+esc(s.passwordHint)+")":"")+'</p><input type=password name=password autocomplete=off><button class="userio-setup-button" type=submit>Подтвердить</button></form>';
  if (s.status === "error") html += '<a class="userio-setup-button userio-setup-secondary" href="'+a('/start?slot='+encodeURIComponent(s.id))+'">Начать заново</a>';
  html += '</section>';
  return html;
}
async function refreshAccounts(force){
  if(!force&&Date.now()<accountsRefreshAt)return;
  accountsRefreshAt=Date.now()+10000;
  try{const response=await fetch('/v1/accounts',{cache:'no-store'});if(!response.ok)return;const data=await response.json();accounts=new Map((data.accounts||[]).filter(function(row){return row.provider==='telegram';}).map(function(row){return [row.id,row];}));}
  catch{}
}
function render(list){
  var box = document.getElementById("cards");
  var oldBox = document.getElementById("old-cards");
  accounts.forEach(function(account){
    var session=list.find(function(s){return s.accountId===account.id;});
    if(session){session.accountId=account.id;return;}
    list.push({id:'missing-'+account.id.replace(/[^a-zA-Z0-9]/g,'-'),accountId:account.id,status:'reconnect-required',sync:'missing-session',qr:''});
  });
  var connected = list.filter(function(s){return s.status==='connected'&&s.sync!=='relogin-required';}).length;
  var revoked = list.filter(function(s){return accounts.has(s.accountId)&&(s.sync==='relogin-required'||s.status==='reconnect-required');}).length;
  document.getElementById('old-sessions').hidden=!list.some(function(s){return s.sync==='relogin-required'&&!accounts.has(s.accountId);});
  document.getElementById('account-summary').textContent='Подключено аккаунтов: '+connected+' · требуют переподключения: '+revoked;
  var live = new Set(list.map(function(s){return s.id;}));
  list.forEach(function(s){
    var account=accounts.get(s.accountId)||{};
    var target=s.sync==='relogin-required'&&!accounts.has(s.accountId)?oldBox:box;
    var sig = [s.status,s.name,s.accountId,account.display_name,account.can_disconnect,(s.qr||" ").slice(0,80),s.passwordHint,s.phone,s.codeViaApp?1:0,s.promptSeq,s.mode,s.sync,s.syncChats,s.syncError].join("|");
    if (seen.get(s.id) === sig) return;
    seen.set(s.id, sig);
    var el = document.getElementById("card-"+s.id);
    var html = card(s);
    if (el) el.remove(); target.insertAdjacentHTML("beforeend", html);
  });
  [...seen.keys()].forEach(function(id){
    if (!live.has(id)) { var el = document.getElementById("card-"+id); if (el) el.remove(); seen.delete(id); }
  });
}
async function poll(){ await refreshAccounts(false); fetch(prefix+"/state").then(function(r){if(!r.ok)throw Error('state unavailable');return r.json();}).then(render).catch(function(){}); }
setInterval(poll, 1500);
document.addEventListener("submit", function(e){
  var f = e.target;
  if (f.dataset.async !== "1") return;
  e.preventDefault();
  fetch(f.getAttribute("action"), {method:"POST", body:new URLSearchParams(new FormData(f))}).catch(function(){}).then(poll);
});
document.getElementById('import-session-form').addEventListener('submit',async function(event){
  event.preventDefault();var form=event.currentTarget,field=form.elements.session,status=document.getElementById('import-session-status');
  var session=String(field.value||'').trim();if(!session)return;status.textContent='Проверяю сеанс…';
  try{
    var response=await fetch(prefix+'/import-session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session:session})});
    var result=await response.json().catch(function(){return {};});
    if(!response.ok)throw Error(result.error||'Сеанс не подключён.');
    status.textContent='Сеанс проверен и сохранён. Аккаунт: '+result.account_id;
    field.value='';await poll();
  }catch(error){status.textContent=error.message||'Сеанс не подключён.';field.value='';}
});
document.addEventListener('click',async function(event){
  var button=event.target.closest('[data-account-action]');if(!button)return;
  var kind=button.dataset.accountAction,label=button.dataset.label||'этот аккаунт';
  var question=kind==='account'
    ? (button.textContent==='Отключить аккаунт'?'Отключить «'+label+'»? Сохранённая история останется в UserIO.':'Убрать отключённый аккаунт «'+label+'» из UserIO? История останется.')
    : 'Убрать отключённую сессию «'+label+'» из UserIO? Сохранённая история останется.';
  if(!confirm(question))return;
  button.disabled=true;var status=button.parentElement.querySelector('[data-action-status]');status.textContent='Выполняем…';
  try{
    var response=kind==='account'
      ? await fetch('/v1/accounts/'+encodeURIComponent(button.dataset.accountId),{method:'DELETE'})
      : await fetch(prefix+'/remove',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({slot:button.dataset.slot})});
    var data=await response.json().catch(function(){return {};});
    if(!response.ok)throw Error(data.error||'Не удалось убрать аккаунт. Запись сохранена.');
    await refreshAccounts(true);await poll();
  }catch(error){status.textContent=error.message||'Не удалось убрать аккаунт.';button.disabled=false;}
});
poll();
</script>`;

http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const slot = url.searchParams.get("slot");
  let browserOwner=null;
  if(['/','/state','/new','/phone','/code','/password','/start','/remove','/import-session'].includes(url.pathname)){
    try{
      browserOwner=await enrollmentPrincipal(userIoUrl,req.headers,(target,auth)=>new Promise((resolve,reject)=>{
        const check=http.get(target,{headers:auth},response=>{
          let data='';response.on('data',chunk=>{data+=chunk;if(data.length>8192)check.destroy();});
          response.on('end',()=>{try{if(response.statusCode!==200)throw Error('login');resolve(JSON.parse(data));}catch(error){reject(error);}});
        });check.setTimeout(4000,()=>check.destroy(Error('login timeout')));check.on('error',reject);
      }));
      if(slot&&!enrollmentOwners.allowed(slot,browserOwner.principal.user_id))throw Error('Not your enrollment');
      if(!['/','/state'].includes(url.pathname)&&!browserOwner.principal.can_manage)throw Error('Personal login required');
    }catch(error){res.writeHead(403,{'content-type':'application/json'});return res.end(JSON.stringify({error:'Войдите в свой UserIO для подключения аккаунта'}));}
  }

  if (url.pathname === "/agent-deliver-callback" && req.method === "POST") {
    let raw = ""; req.on("data", (chunk) => { raw += chunk; });
    req.on("end", () => {
      if (!verifyCallbackSignature(req, raw)) { res.writeHead(401, { "content-type": "application/json" }); return res.end(JSON.stringify({ error: "invalid callback signature" })); }
      try { const payload = JSON.parse(raw || "{}"); appendFileSync(agentDeliverReceiptPath, JSON.stringify({ received_at: new Date().toISOString(), ...payload }) + "\n", { mode: 0o600 }); res.writeHead(202, { "content-type": "application/json" }); return res.end(JSON.stringify({ ok: true })); }
      catch (error) { res.writeHead(400, { "content-type": "application/json" }); return res.end(JSON.stringify({ error: String((error && error.message) || error) })); }
    });
    return;
  }
  if (url.pathname === "/state") {
    res.setHeader("content-type", "application/json");
    return res.end(JSON.stringify(enrollmentOwners.filter(publicState(),browserOwner.principal.user_id)));
  }
  if (url.pathname === "/import-session" && req.method === "POST") {
    let importedClient=null;
    try {
      const payload=await readJsonBody(req,16*1024);
      const session=String(payload.session||"").trim();
      if (session.length<100 || session.length>8192 || /[\r\n\0]/.test(session)) {
        throw new Error("GramJS session string is invalid");
      }
      importedClient=newSyncClient(session);
      await importedClient.connect();
      const auth=await probeAuthorization(importedClient,Api);
      if (!auth.authorized) throw new Error("Telegram session is not authorized");
      const user=await importedClient.getMe();
      const accountId=`telegram:${user.id}`;
      const savedSession=importedClient.session.save();
      const sameSession=readdirSync(sessionsDir).some(file=>/^account-\d+\.session$/.test(file)
        && readFileSync(`${sessionsDir}/${file}`,"utf8").trim()===savedSession);
      if (sameSession) throw new Error("Telegram account or session is already connected in UserIO");
      await importedClient.disconnect();
      const slotId=`account-${nextSlot++}`;
      enrollmentOwners.bind(slotId,browserOwner.principal,browserOwner.headers);
      slots.set(slotId,{status:"connected",mode:"imported"});
      await finishLogin(slotId,importedClient,user);
      importedClient=null;
      const state=slots.get(slotId)||{};
      if (state.status==="connected-unregistered") {
        res.writeHead(502,{"content-type":"application/json"});
        return res.end(JSON.stringify({error:"Сеанс сохранён, но UserIO не подтвердил регистрацию аккаунта. Перепроверьте список аккаунтов."}));
      }
      res.writeHead(201,{"content-type":"application/json"});
      return res.end(JSON.stringify({ok:true,account_id:state.accountId||accountId,slot:slotId}));
    } catch (error) {
      if (importedClient) await importedClient.disconnect().catch(()=>{});
      const message=String(error&&error.message||"");
      const duplicate=message.includes("already connected in UserIO");
      res.writeHead(duplicate?409:400,{"content-type":"application/json"});
      return res.end(JSON.stringify({error:duplicate?"Этот Telegram-аккаунт или сеанс уже подключён в UserIO.":"Не удалось проверить сеанс Telegram. Проверьте, что он авторизован и преобразован в формат GramJS."}));
    }
  }
  if (url.pathname === "/account-card.mjs" && req.method === "GET") {
    res.setHeader("content-type", "text/javascript; charset=utf-8");
    res.setHeader("cache-control", "public, max-age=300");
    return res.end(readFileSync(new URL("./account-card.mjs", import.meta.url)));
  }
  if (url.pathname === "/remove" && req.method === "POST") {
    if (!browserOwner.principal.can_manage) {
      res.writeHead(403, { "content-type": "application/json" });
      return res.end(JSON.stringify({ error: "Нет права управлять аккаунтами" }));
    }
    try {
      const payload = await readJsonBody(req);
      const slotId = String(payload.slot || "");
      if (!/^account-\d+$/.test(slotId)) throw new Error("invalid session");
      if (!enrollmentOwners.allowed(slotId, browserOwner.principal.user_id)) {
        res.writeHead(403, { "content-type": "application/json" });
        return res.end(JSON.stringify({ error: "Сессия принадлежит другому пользователю" }));
      }
      if (syncs.get(slotId)?.status !== "relogin-required") {
        res.writeHead(409, { "content-type": "application/json" });
        return res.end(JSON.stringify({ error: "Сессию можно убрать только после подтверждённого отзыва входа" }));
      }
      const live = clearSlot(slotId);
      if (live?.client) await live.client.disconnect().catch(() => {});
      res.writeHead(200, { "content-type": "application/json" });
      return res.end(JSON.stringify({ removed: true, provider_already_revoked: true }));
    } catch {
      res.writeHead(400, { "content-type": "application/json" });
      return res.end(JSON.stringify({ error: "Не удалось убрать отключённую сессию" }));
    }
  }
  if (url.pathname === "/disconnect" && req.method === "POST") {
    const expected = `Bearer ${process.env.USERIO_API_TOKEN || ""}`;
    if (!process.env.USERIO_API_TOKEN || req.headers.authorization !== expected) {
      res.writeHead(401, { "content-type": "application/json" });
      return res.end(JSON.stringify({ error: "unauthorized" }));
    }
    try {
      const payload = await readJsonBody(req);
      const slotId = String(payload.slot || "");
      const accountId = String(payload.account_id || "");
      const userId = String(payload.user_id || "");
      if (!/^account-\d+$/.test(slotId) || !/^telegram:\d+$/.test(accountId) || !userId) {
        res.writeHead(400, { "content-type": "application/json" });
        return res.end(JSON.stringify({ error: "invalid Telegram account reference" }));
      }
      if (!enrollmentOwners.allowed(slotId, userId) && enrollmentOwners.owner(slotId) !== enrollmentOwners.legacyUser) {
        res.writeHead(403, { "content-type": "application/json" });
        return res.end(JSON.stringify({ error: "Telegram account belongs to another UserIO user" }));
      }
      const sessionFile = `${sessionsDir}/${slotId}.session`;
      const live = liveSlots.get(slotId);
      const state = slots.get(slotId);
      const knownAccount = live?.accountId || state?.accountId || "";
      if (knownAccount && knownAccount !== accountId) {
        res.writeHead(409, { "content-type": "application/json" });
        return res.end(JSON.stringify({ error: "Telegram account reference does not match this session" }));
      }
      if (!existsSync(sessionFile) && !live?.client) {
        clearSlot(slotId);
        res.writeHead(200, { "content-type": "application/json" });
        return res.end(JSON.stringify({ disconnected: true, already_disconnected: true }));
      }
      if (syncs.get(slotId)?.status === "relogin-required") {
        const removed = clearSlot(slotId);
        if (removed?.client) await removed.client.disconnect().catch(() => {});
        res.writeHead(200, { "content-type": "application/json" });
        return res.end(JSON.stringify({ disconnected: true, already_disconnected: true }));
      }
      if (!live?.client || knownAccount !== accountId) {
        res.writeHead(503, { "content-type": "application/json" });
        return res.end(JSON.stringify({ error: "Telegram session is offline; account was kept" }));
      }
      try {
        await logoutTelegramClient(live.client, Api);
      } catch (error) {
        console.error(`Telegram logout not confirmed for ${slotId}:`, error?.errorMessage || error?.message || "unknown");
        res.writeHead(503, { "content-type": "application/json" });
        return res.end(JSON.stringify({ error: "Telegram did not confirm logout; account was kept" }));
      }
      const removed = clearSlot(slotId);
      await removed?.client?.disconnect().catch(() => {});
      res.writeHead(200, { "content-type": "application/json" });
      return res.end(JSON.stringify({ disconnected: true }));
    } catch {
      res.writeHead(400, { "content-type": "application/json" });
      return res.end(JSON.stringify({ error: "invalid disconnect request" }));
    }
  }
  if (url.pathname === "/new") {
    const created = `account-${nextSlot++}`;
    enrollmentOwners.bind(created,browserOwner.principal,browserOwner.headers);
    slots.set(created, { status: "idle", mode: "qr" });
    void startQr(created);
    res.writeHead(302, { Location: `${publicPrefix}/?slot=${created}` });
    return res.end();
  }
  if(url.pathname==='/read'&&req.method==='POST') {
    if(!process.env.USERIO_API_TOKEN||req.headers.authorization!==`Bearer ${process.env.USERIO_API_TOKEN}`){res.writeHead(401,{'content-type':'application/json'});return res.end(JSON.stringify({error:'unauthorized'}));}
    try {
      const result=await acknowledgeRead({slots:liveSlots,Api,payload:await readJsonBody(req)});
      res.writeHead(200,{'content-type':'application/json'});return res.end(JSON.stringify(result));
    }catch(error){res.writeHead(400,{'content-type':'application/json'});return res.end(JSON.stringify({error:String(error.message||error)}));}
  }
  if (url.pathname === "/folders" && req.method === "GET") {
    if (!process.env.USERIO_API_TOKEN || req.headers.authorization !== `Bearer ${process.env.USERIO_API_TOKEN}`) {
      res.writeHead(401, {"content-type":"application/json"});
      return res.end(JSON.stringify({error:"unauthorized"}));
    }
    try {
      const result=await readDialogFolders({slots:liveSlots,Api,
        accountId:url.searchParams.get("account_id"),
        folderIds:(url.searchParams.get("folder_ids")||"").split(",").map(Number),
        resolve:url.searchParams.get("resolve")==="1"});
      res.writeHead(200,{"content-type":"application/json"});
      return res.end(JSON.stringify(result));
    } catch(error) {
      res.writeHead(400,{"content-type":"application/json"});
      return res.end(JSON.stringify({error:String(error.message||"folder metadata unavailable")}));
    }
  }
  if (url.pathname === "/reconcile" && req.method === "POST") {
    const expected = `Bearer ${process.env.USERIO_API_TOKEN || ""}`;
    if (!process.env.USERIO_API_TOKEN || req.headers.authorization !== expected) {
      res.writeHead(401, {"content-type":"application/json"});
      return res.end(JSON.stringify({error:"unauthorized"}));
    }
    try {
      const payload = await readJsonBody(req);
      const item = [...liveSlots.values()].find(v => v.accountId === payload.account_id);
      if (!item) throw new Error("requested account is not connected");
      const chatKey = String(payload.peer_id || "");
      if (!/^-?[0-9]+$/.test(chatKey)) throw new Error("exact peer_id required");
      const peer = item.labelPeers.idPeers.get(chatKey) || await item.client.getEntity(chatKey);
      const input = await item.client.getInputEntity(peer);
      const detail = await item.client.invoke(new Api.messages.GetPeerDialogs({
        peers:[new Api.InputDialogPeer({peer:input})],
      }));
      if (!detail.dialogs || !detail.dialogs.length) throw new Error("provider dialog is unavailable");
      const me = await item.client.getMe();
      const requestedLimit = payload.limit == null ? 200 : payload.limit;
      if (!Number.isInteger(requestedLimit) || requestedLimit < 1 || requestedLimit > 200) {
        throw new Error("limit must be an integer from 1 to 200");
      }
          const beforeId = payload.before_id == null ? 0 : payload.before_id;
          if (!Number.isSafeInteger(beforeId) || beforeId < 0 || beforeId > 2147483647) {
            throw new Error("before_id must be a nonnegative Telegram message ID");
          }
          const scan = payload.scan === true;
          if (payload.scan != null && typeof payload.scan !== "boolean") {
            throw new Error("scan must be a boolean");
          }
          const page = await reconcileDialog("operator", item.client, item.accountId, null, me,
            peer, chatKey, entityLabel(peer), detail.dialogs[0], requestedLimit, beforeId, scan);
      res.writeHead(200, {"content-type":"application/json"});
      return res.end(JSON.stringify({account_id:item.accountId, peer_id:chatKey,
            mirrored:page.posted, fetched:page.fetched, oldest_id:page.oldest_id,
            has_more:page.has_more, message_ids:page.message_ids,
            provider_read_acknowledged:false, scan}));
    } catch (error) {
      res.writeHead(400, {"content-type":"application/json"});
      return res.end(JSON.stringify({error:String(error.message || error)}));
    }
  }
  if ((url.pathname === "/admin/prepare" || url.pathname === "/admin/execute") && req.method === "POST") {
    const expected = `Bearer ${process.env.USERIO_API_TOKEN || ""}`;
    if (!process.env.USERIO_API_TOKEN || req.headers.authorization !== expected) {
      res.writeHead(401, { "content-type": "application/json" });
      return res.end(JSON.stringify({ error: "unauthorized" }));
    }
    try {
      const payload = await readJsonBody(req);
      const accountId = String(payload.account_id || "");
      const item = [...liveSlots.values()].find((entry) => entry.accountId === accountId);
      if (!item) throw new Error("telegram_account_not_connected");
      const operation = String(payload.operation || "");
      let result;
      if (url.pathname === "/admin/prepare") {
        const resolved = await prepareTelegramAdmin(item.client, Api, operation, payload.payload);
        result = { ok: true, account_id: accountId, operation, resolved };
      } else {
        result = await executeTelegramAdmin(item.client, Api, telegramAdminJournal, {
          accountId, operation, payload: payload.payload, requestId: payload.request_id,
        });
      }
      res.writeHead(200, { "content-type": "application/json" });
      return res.end(JSON.stringify(result));
    } catch (error) {
      res.writeHead(400, { "content-type": "application/json" });
      return res.end(JSON.stringify({ error: String(error.message || "telegram_admin_failed") }));
    }
  }
  if (url.pathname === "/download" && req.method === "POST") {
    const expected = `Bearer ${process.env.USERIO_API_TOKEN || ""}`;
    if (!process.env.USERIO_API_TOKEN || req.headers.authorization !== expected) {
      res.writeHead(401, { "content-type": "application/json" });
      return res.end(JSON.stringify({ error: "unauthorized" }));
    }
    try {
      const payload = await readJsonBody(req);
      const media = await downloadTelegramMedia(payload);
      const filename = safeFilename(media.filename);
      res.writeHead(200, {
        "content-type": media.contentType || "application/octet-stream",
        "content-length": String(media.bytes.length),
        ...attachmentResponseHeaders(filename),
        "x-telegram-slot": media.slot,
      });
      return res.end(media.bytes);
    } catch (error) {
      console.error("download error:", (error && error.message) || error);
      res.writeHead(404, { "content-type": "application/json" });
      return res.end(JSON.stringify({ error: (error && error.message) || "download failed" }));
    }
  }
  if (url.pathname === "/resolve" && req.method === "POST") {
    const expected = `Bearer ${process.env.USERIO_API_TOKEN || ""}`;
    if (!process.env.USERIO_API_TOKEN || req.headers.authorization !== expected) {
      res.writeHead(401, { "content-type": "application/json" });
      return res.end(JSON.stringify({ error: "unauthorized" }));
    }
    let raw = "";
    req.on("data", (chunk) => { raw += chunk; });
    req.on("end", async () => {
      try {
        const payload = JSON.parse(raw || "{}");
        const accountId = String(payload.account_id || "").trim();
        const identifier = String(payload.identifier || "").trim();
        if (!accountId || !identifier || identifier.length > 64) {
          res.writeHead(400, { "content-type": "application/json" });
          return res.end(JSON.stringify({ error: "account and identifier are required",
            code: "invalid_contact_scope" }));
        }
        const chosen = [...liveSlots].find(([, item]) => item.accountId === accountId);
        if (!chosen) {
          res.writeHead(404, { "content-type": "application/json" });
          return res.end(JSON.stringify({ error: "Telegram account is not connected",
            code: "account_not_connected" }));
        }
        const [slot, item] = chosen;
        const entity = await resolveTelegramContact(item.client, identifier, Api);
        if (!entity) {
          res.writeHead(404, { "content-type": "application/json" });
          return res.end(JSON.stringify({ error: `Telegram did not resolve "${identifier}"`,
            code: "contact_not_found" }));
        }
        const peerId = String(await item.client.getPeerId(entity, true));
        const profile = peerProfile(entity);
        // Register the peer so the approved send can address it by its id.
        item.labelPeers.idPeers.set(peerId, entity);
        if (profile.username) item.labelPeers.labelPeers.set(profile.username, entity);
        if (profile.name) item.labelPeers.labelPeers.set(profile.name, entity);
        res.writeHead(200, { "content-type": "application/json" });
        return res.end(JSON.stringify({ ok: true, slot, account_id: accountId, peer_id: peerId,
          username: profile.username, name: profile.name, phone: profile.phone }));
      } catch (error) {
        console.error("resolve error:", (error && error.message) || error);
        res.writeHead(502, { "content-type": "application/json" });
        return res.end(JSON.stringify({ error: (error && error.message) || "resolve failed",
          code: "resolve_failed" }));
      }
    });
    return;
  }
  if (url.pathname === "/send" && req.method === "POST") {
    const expected = `Bearer ${process.env.USERIO_API_TOKEN || ""}`;
    if (!process.env.USERIO_API_TOKEN || req.headers.authorization !== expected) {
      res.writeHead(401, { "content-type": "application/json" });
      return res.end(JSON.stringify({ error: "unauthorized" }));
    }
    let raw = "";
    req.on("data", (chunk) => { raw += chunk; });
    req.on("end", async () => {
      try {
        const payload = JSON.parse(raw || "{}");
        const chat = String(payload.chat || "").trim();
        const chatId = String(payload.chat_id || "").trim();
        const wantAccount = String(payload.account_id || "").trim();
        const wantSlot = String(payload.slot || "").trim();
        const attemptId = String(payload.attempt_id || payload.request_id || "").trim();
        // An approved send is idempotent per attempt id. A recorded delivery
        // returns its receipt without sending again, and an attempt that may
        // already be on the wire is never replayed. The unknown answer is a
        // 502 on purpose: UserIO treats 4xx as a refusal that releases the
        // claim, and a release here could duplicate a delivered message.
        if (attemptId) {
          const recorded = sendAttempts.get(attemptId);
          const state = attemptState(recorded);
          if (state === "sent") {
            res.writeHead(200, { "content-type": "application/json" });
            return res.end(JSON.stringify({ ok: true, duplicate: true, slot: recorded.slot,
              account_id: recorded.account_id, peer_id: recorded.peer_id,
              message_id: String(recorded.message_id), date: recorded.date }));
          }
          if (state === "pending" || state === "unknown") {
            res.writeHead(502, { "content-type": "application/json" });
            return res.end(JSON.stringify({ error: "this approved send may already be on the wire",
              code: "send_outcome_unknown", submitted: "unknown" }));
          }
        }
        const rawText = String(payload.body || "");
        const replyToId = String(payload.reply_to_id || "").trim();
        const threadId = String(payload.thread_id || "").trim();
        const expectedText = payload.expected_text;
        const text = replyToId ? rawText : rawText.trim();
        if ((replyToId || threadId || expectedText !== undefined) && (!replyToId || !wantAccount || !chatId || !/^[1-9][0-9]*$/.test(replyToId)
            || Number(replyToId) > 0x7fffffff || threadId && (!/^[1-9][0-9]*$/.test(threadId)
            || Number(threadId) > 0x7fffffff) || typeof expectedText !== "string")) {
          res.writeHead(400, { "content-type": "application/json" });
          return res.end(JSON.stringify({ error: "account, exact chat, source message and source text are required for replies",
            code: "invalid_reply_scope", submitted: false }));
        }
        if ((!chat && !chatId) || !text) {
          res.writeHead(400, { "content-type": "application/json" });
          return res.end(JSON.stringify({ error: "chat (or chat_id) and body are required" }));
        }
        // The stable account identity is authoritative after reconnect. An
        // obsolete same-owner slot may resolve to its new session, while a
        // live mismatched slot or a different owner must never be substituted.
        const candidates = wantAccount || wantSlot ? [] : [...liveSlots];
        let chosen = null;
        if (wantSlot) {
          const live = liveSlots.get(wantSlot);
          if (live && (!wantAccount || live.accountId === wantAccount)) chosen = [wantSlot, live];
          if (!live && wantAccount && (slots.has(wantSlot) || Object.hasOwn(enrollmentOwners.owners, wantSlot))) {
            chosen = [...liveSlots].find(([id, item]) => item.accountId === wantAccount
              && enrollmentOwners.owner(id) === enrollmentOwners.owner(wantSlot)) || null;
          }
        } else if (wantAccount) {
          chosen = [...liveSlots].find(([, item]) => item.accountId === wantAccount) || null;
        }
        const trySend = async (slot, item) => {
          const peers = item.labelPeers;
          const peer = replyToId
            ? peers.idPeers.get(chatId)
            : (chat && peers.labelPeers.get(chat)) || (chatId && peers.idPeers.get(chatId)) || null;
          if (!peer || !item.client) return false;
          const peerId = chatId || String(await item.client.getPeerId(peer, true));
          if (replyToId) {
            const sent = await sendTelegramReply({ client: item.client, peer, messageId: replyToId,
              threadId, expectedText, text });
            console.log(`reply ${slot}: ${chatId} msg ${sent.messageId} to ${replyToId}`);
            sendAttempts.record(attemptId, { state: "sent", slot, account_id: item.accountId,
              peer_id: peerId, message_id: String(sent.messageId), date: sent.date, kind: "reply" });
            res.writeHead(200, { "content-type": "application/json" });
            res.end(JSON.stringify({ ok: true, slot, account_id: item.accountId, peer_id: chatId,
              message_id: sent.messageId, reply_to_id: sent.replyToId, thread_id: sent.threadId,
              text: sent.text, date: sent.date, out: sent.out }));
            return true;
          }
          // Record the attempt before the provider call, so an outcome that
          // never comes back stays honestly in flight instead of unknown.
          sendAttempts.record(attemptId, { state: "pending", slot, account_id: item.accountId,
            peer_id: peerId, kind: "send" });
          const sent = await item.client.sendMessage(peer, { message: text });
          console.log(`send ${slot}: "${chat || chatId}" msg ${sent && sent.id}`);
          sendAttempts.record(attemptId, { state: "sent", slot, account_id: item.accountId,
            peer_id: peerId, message_id: String(sent && sent.id), date: sent && sent.date });
          res.writeHead(200, { "content-type": "application/json" });
          res.end(JSON.stringify({ ok: true, slot, account_id: item.accountId, message_id: String(sent && sent.id) }));
          return true;
        };
        if (chosen) {
          if (await trySend(chosen[0], chosen[1])) return;
          sendAttempts.record(attemptId, { state: "refused", code: "chat_not_known",
            account_id: wantAccount, peer_id: chatId });
          res.writeHead(404, { "content-type": "application/json" });
          return res.end(JSON.stringify({ error: `account ${wantAccount} does not know chat "${chat || chatId}"`,
            code: "chat_not_known", submitted: false }));
        }
        if (wantSlot || wantAccount) {
          // Name the real reason so the caller can tell the owner what to do:
          // a revoked saved session needs a fresh login, an offline one is
          // worth retrying. Both answers happen before any provider call, so
          // nothing was submitted either way.
          const pinned = wantSlot ? wantSlot
            : [...slots.entries()].find(([, item]) => String(item.accountId || "") === wantAccount);
          const sync = pinned ? syncs.get(pinned) : null;
          const relogin = Boolean(sync && (sync.status === "relogin-required" || sync.authCode));
          sendAttempts.record(attemptId, { state: "refused",
            code: relogin ? "account_relogin_required" : "account_not_connected",
            account_id: wantAccount, peer_id: chatId });
          res.writeHead(404, { "content-type": "application/json" });
          return res.end(JSON.stringify({
            error: `account ${wantAccount || wantSlot} is not connected`, submitted: false,
            code: relogin ? "account_relogin_required" : "account_not_connected",
          }));
        }
        for (const [slot, item] of candidates) {
          if (await trySend(slot, item)) return;
        }
        sendAttempts.record(attemptId, { state: "refused", code: "chat_not_known", peer_id: chatId });
        res.writeHead(404, { "content-type": "application/json" });
        res.end(JSON.stringify({ error: `no connected account knows chat "${chat || chatId}"`,
          code: "chat_not_known", submitted: false }));
      } catch (error) {
        if (error instanceof TelegramReplyRefused || error?.submitted === false) {
          sendAttempts.record(attemptId, { state: "refused", code: error.code || "reply_refused",
            account_id: wantAccount, peer_id: chatId });
          res.writeHead(409, { "content-type": "application/json" });
          return res.end(JSON.stringify({ error: error.message || "Telegram reply refused before send",
            code: error.code || "reply_refused", submitted: false }));
        }
        console.error("send error:", (error && error.message) || error);
        sendAttempts.record(attemptId, { state: "unknown", code: "send_outcome_unknown",
          account_id: wantAccount, peer_id: chatId });
        res.writeHead(502, { "content-type": "application/json" });
        // An exception can arrive with a message already on the wire, so the
        // caller must keep its durable claim instead of assuming non-delivery.
        res.end(JSON.stringify({ error: (error && error.message) || "send failed",
          code: "send_outcome_unknown", submitted: "unknown" }));
      }
    });
    return;
  }
  if (url.pathname === "/upload-limit" && req.method === "GET") {
    const expected = `Bearer ${process.env.USERIO_API_TOKEN || ""}`;
    if (!process.env.USERIO_API_TOKEN || req.headers.authorization !== expected) {
      res.writeHead(401, { "content-type": "application/json" });
      return res.end(JSON.stringify({ error: "unauthorized" }));
    }
    try {
      const accountId=String(url.searchParams.get("account_id")||"");
      const liveEntry=[...liveSlots.entries()].find(([,value])=>value.accountId===accountId);
      const item=liveEntry&&liveEntry[1];
      if (!item) {
        res.writeHead(409,{"content-type":"application/json"});
        return res.end(JSON.stringify({error:"account_not_connected",submitted:false}));
      }
      const limits=await telegramUploadLimit(item.client);
      res.writeHead(200,{"content-type":"application/json"});
      return res.end(JSON.stringify({account_id:accountId,max_bytes:limits.maxBytes,
        caption_max_bytes:limits.captionLength}));
    } catch (error) {
      res.writeHead(503,{"content-type":"application/json"});
      return res.end(JSON.stringify({error:"account_upload_limits_unavailable"}));
    }
  }
  if (url.pathname === "/send-file" && req.method === "POST") {
    const expected=`Bearer ${process.env.USERIO_API_TOKEN||""}`;
    if (!process.env.USERIO_API_TOKEN || req.headers.authorization!==expected) {
      res.writeHead(401,{"content-type":"application/json"});
      return res.end(JSON.stringify({error:"unauthorized",submitted:false}));
    }
    let metadata;
    try {
      const encoded=String(req.headers["x-userio-file-metadata"]||"");
      if (!encoded || encoded.length>12000) throw new Error("invalid file metadata");
      const normalized=encoded.replace(/-/g,"+").replace(/_/g,"/");
      metadata=JSON.parse(Buffer.from(normalized,"base64").toString("utf8"));
      if (!metadata || !/^telegram:[1-9][0-9]*$/.test(String(metadata.account_id||""))
          || !/^-?[1-9][0-9]*$/.test(String(metadata.peer_id||""))
          || !/^action_[a-f0-9]{64}$/.test(String(metadata.attempt_id||""))
          || typeof metadata.filename!=="string" || metadata.filename.length<1 || metadata.filename.length>128
          || metadata.filename==="." || metadata.filename===".."
          || /[\\/\r\n\0\x00-\x1f\x7f]/.test(metadata.filename)
          || typeof metadata.mime_type!=="string"
          || !/^[A-Za-z0-9!#$&^_.+-]+\/[A-Za-z0-9!#$&^_.+-]+$/.test(metadata.mime_type)
          || typeof metadata.caption!=="string" || typeof metadata.force_document!=="boolean"
          || !Number.isSafeInteger(metadata.size) || metadata.size<1
          || !/^[a-f0-9]{64}$/.test(String(metadata.sha256||""))) throw new Error("invalid file metadata");
      const declared=Number(req.headers["content-length"]||0);
      if (declared!==metadata.size) throw new Error("file size declaration mismatch");
    } catch (error) {
      res.writeHead(400,{"content-type":"application/json"});
      return res.end(JSON.stringify({error:"invalid_file_upload",submitted:false}));
    }
    const prior=sendAttempts.get(metadata.attempt_id);
    const state=attemptState(prior);
    if (state==="sent") {
      if (prior.kind!=="file" || prior.account_id!==metadata.account_id
          || prior.peer_id!==metadata.peer_id || prior.sha256!==metadata.sha256
          || prior.size!==metadata.size || prior.force_document!==metadata.force_document) {
        res.writeHead(409,{"content-type":"application/json"});
        return res.end(JSON.stringify({error:"send_attempt_identity_conflict",submitted:false}));
      }
      res.writeHead(200,{"content-type":"application/json"});
      return res.end(JSON.stringify({ok:true,duplicate:true,slot:prior.slot,account_id:prior.account_id,
        peer_id:prior.peer_id,message_id:prior.message_id,date:prior.date,size:prior.size,
        sha256:prior.sha256,filename:prior.filename,mime_type:prior.mime_type}));
    }
    if (state==="pending" || state==="unknown") {
      res.writeHead(502,{"content-type":"application/json"});
      return res.end(JSON.stringify({error:"this approved file upload may already be on the wire",
        code:"send_outcome_unknown",submitted:"unknown"}));
    }
    if (state==="refused") {
      res.writeHead(409,{"content-type":"application/json"});
      return res.end(JSON.stringify({error:"this approved file upload was already refused",
        code:"send_was_refused",submitted:false}));
    }
    const liveEntry=[...liveSlots.entries()].find(([,value])=>value.accountId===metadata.account_id);
    const item=liveEntry&&liveEntry[1];
    if (!item) {
      res.writeHead(409,{"content-type":"application/json"});
      return res.end(JSON.stringify({error:"account_not_connected",submitted:false}));
    }
    let limits;
    try { limits=await telegramUploadLimit(item.client); }
    catch {
      res.writeHead(503,{"content-type":"application/json"});
      return res.end(JSON.stringify({error:"account_upload_limits_unavailable",submitted:false}));
    }
    if (metadata.size>limits.maxBytes
        || Buffer.byteLength(metadata.caption,"utf8")>limits.captionLength) {
      res.writeHead(413,{"content-type":"application/json"});
      return res.end(JSON.stringify({error:"telegram_account_file_or_caption_limit",submitted:false}));
    }
    let peer=item.labelPeers.idPeers.get(metadata.peer_id);
    if (!peer) {
      try { peer=await item.client.getEntity(metadata.peer_id); }
      catch { peer=null; }
    }
    if (!peer) {
      res.writeHead(409,{"content-type":"application/json"});
      return res.end(JSON.stringify({error:"chat_not_known",submitted:false}));
    }
    const directory=mkdtempSync(joinPath(tmpdir(),"userio-telegram-upload-"));
    const filePath=joinPath(directory,metadata.filename);
    const hasher=createHash("sha256");
    let received=0;
    const meter=new Transform({transform(chunk,encoding,callback){
      received+=chunk.length;
      if (received>metadata.size || received>limits.maxBytes) return callback(new Error("upload size exceeded"));
      hasher.update(chunk);callback(null,chunk);
    }});
    try {
      await new Promise((resolve,reject)=>pipeline(req,meter,
        createWriteStream(filePath,{flags:"wx",mode:0o600}),error=>error?reject(error):resolve()));
      const digest=hasher.digest("hex");
      if (received!==metadata.size || digest!==metadata.sha256 || statSync(filePath).size!==metadata.size) {
        throw new Error("uploaded file digest or size conflict");
      }
      sendAttempts.record(metadata.attempt_id,{state:"pending",kind:"file",account_id:metadata.account_id,
        peer_id:metadata.peer_id,size:metadata.size,sha256:metadata.sha256,filename:metadata.filename,
        mime_type:metadata.mime_type,force_document:metadata.force_document});
      const sendOptions={file:filePath,caption:metadata.caption,fileSize:metadata.size};
      if (metadata.force_document) sendOptions.forceDocument=true;
      const sent=await item.client.sendFile(peer,sendOptions);
      const messageId=String(sent&&sent.id||"");
      const date=Number(sent&&sent.date||0);
      if (!/^[1-9][0-9]*$/.test(messageId) || Number(messageId)>0x7fffffff || !sent.out || !date) {
        throw new Error("Telegram file response has no confirmed message id");
      }
      sendAttempts.record(metadata.attempt_id,{state:"sent",kind:"file",slot:liveEntry[0],
        account_id:metadata.account_id,peer_id:metadata.peer_id,message_id:messageId,date,
        size:metadata.size,sha256:metadata.sha256,filename:metadata.filename,mime_type:metadata.mime_type,
        force_document:metadata.force_document});
      res.writeHead(200,{"content-type":"application/json"});
      return res.end(JSON.stringify({ok:true,slot:liveEntry[0],account_id:metadata.account_id,
        peer_id:metadata.peer_id,message_id:messageId,date,size:metadata.size,sha256:metadata.sha256,
        filename:metadata.filename,mime_type:metadata.mime_type,out:true}));
    } catch (error) {
      const priorState=attemptState(sendAttempts.get(metadata.attempt_id));
      if (priorState==="pending") sendAttempts.record(metadata.attempt_id,{state:"unknown",code:"send_outcome_unknown"});
      console.error("Telegram file upload error:",error&&error.message||"unknown");
      if (priorState==="absent") {
        res.writeHead(400,{"content-type":"application/json"});
        return res.end(JSON.stringify({error:"Telegram file upload refused before submission",submitted:false}));
      }
      res.writeHead(502,{"content-type":"application/json"});
      return res.end(JSON.stringify({error:"Telegram file upload outcome is unknown",
        code:"send_outcome_unknown",submitted:"unknown"}));
    } finally {
      try { if (existsSync(filePath)) unlinkSync(filePath); } catch {}
      try { rmdirSync(directory); } catch {}
    }
  }
  if (url.pathname === "/send-attempt" && req.method === "POST") {
    // Read-only reconciliation of one approved send attempt. It never sends
    // anything: it only reports what the connector durably recorded for that
    // exact attempt id.
    const expected = `Bearer ${process.env.USERIO_API_TOKEN || ""}`;
    if (!process.env.USERIO_API_TOKEN || req.headers.authorization !== expected) {
      res.writeHead(401, { "content-type": "application/json" });
      return res.end(JSON.stringify({ error: "unauthorized" }));
    }
    try {
      const payload = await readJsonBody(req);
      const attemptId = String(payload.attempt_id || "").trim();
      if (!attemptId) {
        res.writeHead(400, { "content-type": "application/json" });
        return res.end(JSON.stringify({ error: "attempt_id is required" }));
      }
      const entry = sendAttempts.get(attemptId);
      if (!entry) {
        res.writeHead(404, { "content-type": "application/json" });
        return res.end(JSON.stringify({ state: "absent", attempt_id: attemptId,
          epoch: sendAttempts.startedAt }));
      }
      res.writeHead(200, { "content-type": "application/json" });
      return res.end(JSON.stringify({
        state: attemptState(entry), attempt_id: attemptId, epoch: sendAttempts.startedAt,
        slot: entry.slot || "", account_id: entry.account_id || "", peer_id: entry.peer_id || "",
        message_id: entry.message_id ? String(entry.message_id) : "",
        date: entry.date || 0, code: entry.code || "", kind: entry.kind || "send",
        recorded_at: entry.updated_at || 0,
      }));
    } catch (error) {
      res.writeHead(400, { "content-type": "application/json" });
      return res.end(JSON.stringify({ error: String(error.message || error) }));
    }
  }
  if (url.pathname === "/edit" && req.method === "POST") {
    const expected = `Bearer ${process.env.USERIO_API_TOKEN || ""}`;
    if (!process.env.USERIO_API_TOKEN || req.headers.authorization !== expected) {
      res.writeHead(401, { "content-type": "application/json" });
      return res.end(JSON.stringify({ error: "unauthorized", submitted: false }));
    }
    try {
      const payload = await readJsonBody(req);
      const accountId = String(payload.account_id || "").trim();
      const messageRef = String(payload.message_id || "").trim();
      const chatId = String(payload.chat_id || "").trim();
      if (!accountId || !chatId || !messageRef || typeof payload.expected_text !== "string"
          || typeof payload.text !== "string" || !payload.text.trim() || payload.text.length > 4096) {
        throw new TelegramEditRefused("invalid_edit", "Account, chat, message and text are required");
      }
      const separator = messageRef.indexOf("|");
      if (separator <= 0 || messageRef.slice(0, separator) !== accountId) {
        throw new TelegramEditRefused("account_scope_conflict", "Message does not belong to this Telegram account");
      }
      const scoped = messageRef.slice(separator + 1);
      const lastColon = scoped.lastIndexOf(":");
      const peerId = lastColon > 0 ? scoped.slice(0, lastColon) : "";
      const messageId = Number(lastColon > 0 ? scoped.slice(lastColon + 1) : "");
      if (peerId !== chatId || !Number.isSafeInteger(messageId) || messageId <= 0) {
        throw new TelegramEditRefused("message_scope_conflict", "Message does not belong to this Telegram chat");
      }
      const chosen = [...liveSlots].find(([, item]) => item.accountId === accountId);
      if (!chosen) {
        res.writeHead(404, { "content-type": "application/json" });
        return res.end(JSON.stringify({ error: "Telegram account is not connected", code: "account_not_connected", submitted: false }));
      }
      const [slot, item] = chosen;
      const peer = item.labelPeers.idPeers.get(chatId);
      if (!peer || !item.client) {
        res.writeHead(404, { "content-type": "application/json" });
        return res.end(JSON.stringify({ error: "Telegram chat is not connected", code: "chat_not_known", submitted: false }));
      }
      const edited = await editOwnedTelegramText({
        client: item.client, api: Api, peer, messageId,
        expectedText: payload.expected_text, text: payload.text,
      });
      res.writeHead(200, { "content-type": "application/json" });
      return res.end(JSON.stringify({ ok: true, slot, account_id: accountId, peer_id: chatId,
        message_id: edited.messageId, text: edited.text, edit_date: edited.editDate, out: edited.out }));
    } catch (error) {
      if (error?.submitted === false) {
        res.writeHead(409, { "content-type": "application/json" });
        return res.end(JSON.stringify({ error: error.message, code: error.code || "edit_refused", submitted: false }));
      }
      // The API call may have reached Telegram; leave the durable UserIO action
      // uncertain instead of suggesting a retry that could overwrite newer text.
      console.error("Telegram edit outcome is unknown:", error?.name || "Error");
      res.writeHead(502, { "content-type": "application/json" });
      return res.end(JSON.stringify({ error: "Telegram edit outcome is unknown", submitted: "unknown" }));
    }
  }
  if (url.pathname === "/react" && req.method === "POST") {
    const expected = `Bearer ${process.env.USERIO_API_TOKEN || ""}`;
    if (!process.env.USERIO_API_TOKEN || req.headers.authorization !== expected) {
      res.writeHead(401, { "content-type": "application/json" });
      return res.end(JSON.stringify({ error: "unauthorized" }));
    }
    let raw = "";
    req.on("data", (chunk) => { raw += chunk; });
    req.on("end", async () => {
      try {
        const payload = JSON.parse(raw || "{}");
        const chat = String(payload.chat || "").trim();
        const chatId = String(payload.chat_id || "").trim();
        const wantAccount = String(payload.account_id || "").trim();
        // Stored UserIO telegram ids look like "account|chatKey:msgId" or
        // "chatKey:msgId"; a bare numeric id is accepted beside chat/chat_id.
        const scoped = String(payload.message_id || "").includes("|")
          ? String(payload.message_id).slice(String(payload.message_id).indexOf("|") + 1)
          : String(payload.message_id || "");
        let targetChatId = chatId;
        let messageId = scoped.trim();
        if (messageId.includes(":")) {
          const cut = messageId.lastIndexOf(":");
          targetChatId = messageId.slice(0, cut) || targetChatId;
          messageId = messageId.slice(cut + 1);
        }
        const emoji = String(payload.emoji || "").trim();
        const msgId = Number(messageId);
        if ((!chat && !targetChatId) || !Number.isSafeInteger(msgId) || msgId <= 0 || !emoji || emoji.length > 16) {
          res.writeHead(400, { "content-type": "application/json" });
          return res.end(JSON.stringify({ error: "chat (or chat_id), valid message_id and emoji are required" }));
        }
        // Account-bound reactions never fall back to another slot, matching /send.
        const tryReact = async (slot, item) => {
          const peers = item.labelPeers;
          const peer = (chat && peers.labelPeers.get(chat)) || (targetChatId && peers.idPeers.get(targetChatId)) || null;
          if (!peer || !item.client) return false;
          await item.client.invoke(
            new Api.messages.SendReaction({ peer, msgId, reaction: [new Api.ReactionEmoji({ emoticon: emoji })] }),
          );
          console.log(`react ${slot}: "${chat || targetChatId}" msg ${msgId} ${emoji}`);
          res.writeHead(200, { "content-type": "application/json" });
          res.end(JSON.stringify({ ok: true, slot, account_id: item.accountId, reacted: true }));
          return true;
        };
        if (wantAccount) {
          const chosen = [...liveSlots].find(([, item]) => item.accountId === wantAccount);
          if (chosen && await tryReact(chosen[0], chosen[1])) return;
          res.writeHead(404, { "content-type": "application/json" });
          return res.end(JSON.stringify({
            error: `account ${wantAccount} does not know chat "${chat || targetChatId}"`,
          }));
        }
        for (const [slot, item] of [...liveSlots]) {
          if (await tryReact(slot, item)) return;
        }
        res.writeHead(404, { "content-type": "application/json" });
        res.end(JSON.stringify({ error: `no connected account knows chat "${chat || targetChatId}"` }));
      } catch (error) {
        console.error("react error:", (error && error.message) || error);
        res.writeHead(502, { "content-type": "application/json" });
        res.end(JSON.stringify({ error: (error && error.message) || "react failed" }));
      }
    });
    return;
  }
  if (url.pathname === "/login/phone" && req.method === "POST") {
    if (!loginAuthorized(req.headers, process.env.USERIO_API_TOKEN)) {
      res.writeHead(401, { "content-type": "application/json" });
      return res.end(JSON.stringify({ error: "unauthorized" }));
    }
    try {
      const payload = await readJsonBody(req);
      const phone = normalizeLoginPhone(payload.phone);
      const created = `account-${nextSlot++}`;
      slots.set(created, { status: "idle", mode: "phone", phone });
      void startPhone(created);
      res.writeHead(202, { "content-type": "application/json" });
      return res.end(JSON.stringify({ ok: true, slot: created, status: "connecting" }));
    } catch (error) {
      res.writeHead(400, { "content-type": "application/json" });
      return res.end(JSON.stringify({ error: (error && error.message) || "invalid phone" }));
    }
  }
  if (url.pathname === "/login/code" && req.method === "POST") {
    if (!loginAuthorized(req.headers, process.env.USERIO_API_TOKEN)) {
      res.writeHead(401, { "content-type": "application/json" });
      return res.end(JSON.stringify({ error: "unauthorized" }));
    }
    try {
      const payload = await readJsonBody(req);
      const slot = String(payload.slot || "").trim();
      const code = normalizeLoginCode(payload.code);
      const state = slots.get(slot);
      if (!state || !state.codeResolve) throw new Error("slot is not waiting for a code");
      const resolve = state.codeResolve;
      const { codeResolve, codeViaApp, promptSeq: _promptSeq, ...rest } = state;
      slots.set(slot, { ...rest, status: "connecting" });
      resolve(code);
      res.writeHead(202, { "content-type": "application/json" });
      return res.end(JSON.stringify({ ok: true, slot, status: "connecting" }));
    } catch (error) {
      res.writeHead(409, { "content-type": "application/json" });
      return res.end(JSON.stringify({ error: (error && error.message) || "code rejected" }));
    }
  }
  if (url.pathname === "/phone" && req.method === "POST") {
    const params = await readBody(req);
    const phone = (params.get("phone") || "").trim();
    if (phone) {
      const created = `account-${nextSlot++}`;
      enrollmentOwners.bind(created,browserOwner.principal,browserOwner.headers);
      slots.set(created, { status: "idle", mode: "phone", phone });
      void startPhone(created);
      res.writeHead(302, { Location: `${publicPrefix}/?slot=${created}` });
      return res.end();
    }
    res.writeHead(302, { Location: `${publicPrefix}/` });
    return res.end();
  }
  if (url.pathname === "/code" && req.method === "POST") {
    const params = await readBody(req);
    const code = (params.get("code") || "").trim();
    const state = slots.get(slot);
    if (state && state.codeResolve) {
      const resolve = state.codeResolve;
      const { codeResolve, codeViaApp, promptSeq, ...rest } = state;
      slots.set(slot, { ...rest, status: "connecting" });
      resolve(code);
    }
    res.writeHead(302, { Location: `${publicPrefix}/?slot=${encodeURIComponent(slot || "")}` });
    return res.end();
  }
  if (url.pathname === "/password" && req.method === "POST") {
    const params = await readBody(req);
    const password = (params.get("password") || "").trim();
    const state = slots.get(slot);
    if (state && state.passwordResolve) {
      const resolve = state.passwordResolve;
      const { passwordResolve, passwordHint, promptSeq, ...rest } = state;
      slots.set(slot, { ...rest, status: "connecting" });
      resolve(password);
    }
    res.writeHead(302, { Location: `${publicPrefix}/?slot=${encodeURIComponent(slot || "")}` });
    return res.end();
  }
  if (url.pathname === "/start" && slots.has(slot)) {
    const state = slots.get(slot);
    if (state.mode === "phone" && state.phone) void startPhone(slot);
    else void startQr(slot);
    res.writeHead(302, { Location: `${publicPrefix}/?slot=${slot}` });
    return res.end();
  }
  if (url.pathname === "/" && slot) restoreSlot(slot);
  res.setHeader("content-type", "text/html; charset=utf-8");
  return res.end(pageShell.split("__PREFIX__").join(publicPrefix));
}).listen(port, "127.0.0.1");
