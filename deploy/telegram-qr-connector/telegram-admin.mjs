import { closeSync, existsSync, fsyncSync, openSync, readFileSync, writeSync } from "node:fs";
import { createHash } from "node:crypto";

const operations = new Set(["group_create", "group_invite", "group_remove", "channel_create", "channel_delete"]);
const normalize = (value) => Array.isArray(value) ? value.map(normalize)
  : value && typeof value === "object" ? Object.fromEntries(Object.keys(value).sort().map((key) => [key, normalize(value[key])])) : value;
const canonical = (value) => JSON.stringify(normalize(value));
const hash = (value) => createHash("sha256").update(canonical(value)).digest("hex");

export class TelegramAdminJournal {
  constructor(path) {
    this.path = path;
    this.rows = new Map();
    this.inFlight = new Map();
    if (existsSync(path)) for (const line of readFileSync(path, "utf8").split("\n")) {
      if (!line) continue;
      try { const row = JSON.parse(line); this.rows.set(row.request_id, row); } catch { /* ignore a torn final row */ }
    }
  }
  put(row) {
    const fd=openSync(this.path,"a",0o600);
    try { writeSync(fd,JSON.stringify(row)+"\n"); fsyncSync(fd); } finally { closeSync(fd); }
    this.rows.set(row.request_id, row);
  }
}

function assertPeerId(value) {
  if (typeof value !== "string" || !/^-?[1-9][0-9]*$/.test(value)) throw new Error("exact_peer_id_required");
  return value;
}

async function preflight(client, Api, operation, payload) {
  if (!operations.has(operation) || !payload || typeof payload !== "object" || Array.isArray(payload)) throw new Error("invalid_telegram_admin_operation");
  const actor=await client.getMe();
  if (!actor || actor.className!=="User" || !actor.id) throw new Error("telegram_account_identity_unavailable");
  if (operation === "group_create") {
    const title=String(payload.title || "").trim();
    if (!title || title.length>128) throw new Error("invalid_group_title");
    if (!Array.isArray(payload.participant_ids) || payload.participant_ids.length < 1 || payload.participant_ids.length > 50) throw new Error("participants_required");
    if (new Set(payload.participant_ids).size!==payload.participant_ids.length) throw new Error("duplicate_participant");
    if (new Set(payload.participant_ids).size!==payload.participant_ids.length) throw new Error("duplicate_participant");
    const participants = [];
    for (const id of payload.participant_ids) {
      if (typeof id !== "string" || !/^[1-9][0-9]*$/.test(id) || id === String(actor.id)) throw new Error("invalid_or_self_participant");
      const entity = await client.getEntity(id);
      if (entity.className !== "User") throw new Error("participant_must_be_user");
      participants.push({ id: String(entity.id), name: [entity.firstName, entity.lastName].filter(Boolean).join(" ") || entity.username || String(entity.id) });
    }
    return { operation, actor_id:String(actor.id), conversation_kind: "group", participants };
  }
  if (operation === "channel_create") {
    const title = String(payload.title || "").trim();
    if (!title || title.length > 128 || String(payload.about || "").length > 255) throw new Error("invalid_channel_title_or_about");
    return { operation, actor_id:String(actor.id), conversation_kind: "channel", title, broadcast: true, megagroup: false, private: true };
  }
  const peerId = assertPeerId(String(payload.peer_id || ""));
  const peer = await client.getEntity(peerId);
  if (operation === "channel_delete") {
    if (peer.className !== "Channel" || peer.broadcast !== true || peer.megagroup === true || peer.creator !== true) throw new Error("owned_broadcast_channel_required");
    if (payload.confirmation_phrase !== `DELETE CHANNEL ${peerId}`) throw new Error("channel_delete_phrase_mismatch");
    return { operation, actor_id:String(actor.id), peer_id: peerId, conversation_kind: "channel", title: String(peer.title || ""), creator: true };
  }
  const isBasic = peer.className === "Chat";
  const isSupergroup = peer.className === "Channel" && peer.megagroup === true;
  if (!isBasic && !isSupergroup) throw new Error("group_or_supergroup_required");
  const id = String(payload.participant_id || "");
  if (!/^[1-9][0-9]*$/.test(id)) throw new Error("exact_telegram_user_id_required");
  if (id === String(actor.id)) throw new Error("cannot_target_own_account");
  const user = await client.getEntity(id);
  if (user.className !== "User") throw new Error("participant_must_be_user");
  if (operation === "group_remove" && isSupergroup && payload.ban_on_supergroup !== true) throw new Error("supergroup_removal_is_ban_requires_explicit_flag");
  return { operation, actor_id:String(actor.id), peer_id: peerId, conversation_kind: isBasic ? "group" : "channel",
    participant: { id: String(user.id), name: [user.firstName, user.lastName].filter(Boolean).join(" ") || user.username || id },
    ...(operation === "group_remove" && isSupergroup ? { effect: "ban" } : {}) };
}

export async function prepareTelegramAdmin(client, Api, operation, payload) {
  return preflight(client, Api, operation, payload);
}

export function invokeTelegramAdminOnce(client, request) {
  const view=Object.create(client);
  Object.defineProperties(view,{
    _requestRetries:{value:1},
    disconnect:{value:async function(){throw new Error("admin_mutation_will_not_reconnect_shared_client");}},
    connect:{value:async function(){throw new Error("admin_mutation_will_not_reconnect_shared_client");}},
    _switchDC:{value:async function(){throw new Error("admin_mutation_will_not_switch_shared_client_dc");}},
  });
  return view.invoke(request);
}

export async function executeTelegramAdmin(client, Api, journal, { accountId, operation, payload, requestId }) {
  if (typeof requestId !== "string" || !/^action_[0-9a-f]{64}$/.test(requestId)) throw new Error("valid_request_id_required");
  const fingerprint = hash({ accountId, operation, payload });
  const prior = journal.rows.get(requestId);
  if (prior) {
    if (prior.fingerprint !== fingerprint) throw new Error("telegram_admin_idempotency_conflict");
    if (prior.status === "complete" || prior.status === "partial") return prior.result;
    return { ok: true, status: "unknown", account_id: accountId, operation,
      peer_id: prior.peer_id || "", receipt: prior.receipt, error: "no_replay_after_uncertain_outcome" };
  }
  if (journal.inFlight.has(requestId)) {
    if (journal.inFlight.get(requestId)!==fingerprint) throw new Error("telegram_admin_idempotency_conflict");
    return { ok: true, status: "unknown", account_id: accountId,
      operation, peer_id: String(payload.peer_id || ""), receipt: `telegram-admin:${requestId}`,
      error: "duplicate_request_in_flight" };
  }
  journal.inFlight.set(requestId,fingerprint);
  let prepared;
  try { prepared = await preflight(client, Api, operation, payload); }
  catch (error) { journal.inFlight.delete(requestId); throw error; }
  if (payload.prepared && hash(payload.prepared) !== hash(prepared)) {
    journal.inFlight.delete(requestId);
    throw new Error("telegram_admin_target_changed");
  }
  const receipt = `telegram-admin:${requestId}`;
  const claimed = { request_id: requestId, fingerprint, status: "sending", account_id: accountId,
    operation, peer_id: prepared.peer_id || "", receipt, updated_at: Date.now() };
  try { journal.put(claimed); } catch (error) { journal.inFlight.delete(requestId); throw error; }
  try {
    let result;
    if (operation === "group_create") {
      const users = [];
      for (const participant of prepared.participants) users.push(await client.getInputEntity(participant.id));
      const updates = await invokeTelegramAdminOnce(client,new Api.messages.CreateChat({ users, title: String(payload.title).trim() }));
      const chat = (updates.chats || []).find((item) => item.className === "Chat");
      if (!chat) throw new Error("group_creation_result_unverifiable");
      const missing = (updates.missingInvitees || []).map((user) => String(user.userId || user.id || ""));
      result = { ok: true, status: missing.length ? "partial" : "complete", account_id: accountId,
        operation, peer_id: String(-chat.id), conversation_kind: "group", title: String(payload.title).trim(),
        missing_invitees: missing, receipt };
    } else if (operation === "channel_create") {
      const updates = await invokeTelegramAdminOnce(client,new Api.channels.CreateChannel({ broadcast: true, megagroup: false,
        title: prepared.title, about: String(payload.about || "") }));
      const channel = (updates.chats || []).find((item) => item.className === "Channel" && item.broadcast === true);
      if (!channel) throw new Error("channel_creation_result_unverifiable");
      result = { ok: true, status: "complete", account_id: accountId, operation,
        peer_id: `-100${channel.id}`, conversation_kind: "channel", title: channel.title || prepared.title, receipt };
    } else if (operation === "group_invite") {
      const peer = await client.getEntity(prepared.peer_id);
      const user = await client.getInputEntity(prepared.participant.id);
      let response;
      if (prepared.conversation_kind === "group") response = await invokeTelegramAdminOnce(client,new Api.messages.AddChatUser({
        chatId: peer.id, userId: user, fwdLimit: 0 }));
      else response = await invokeTelegramAdminOnce(client,new Api.channels.InviteToChannel({ channel: peer, users: [user] }));
      result = { ok: true, status: "complete", account_id: accountId, operation,
        peer_id: prepared.peer_id, participant_id: prepared.participant.id, conversation_kind: prepared.conversation_kind,
        updates: response.className, receipt };
    } else if (operation === "group_remove") {
      const peer = await client.getEntity(prepared.peer_id);
      const user = await client.getInputEntity(prepared.participant.id);
      if (prepared.conversation_kind === "group") await invokeTelegramAdminOnce(client,new Api.messages.DeleteChatUser({
        chatId: peer.id, userId: user, revokeHistory: false }));
      else await invokeTelegramAdminOnce(client,new Api.channels.EditBanned({ channel: peer, participant: user,
        bannedRights: new Api.ChatBannedRights({ viewMessages: true, untilDate: 0 }) }));
      result = { ok: true, status: "complete", account_id: accountId, operation,
        peer_id: prepared.peer_id, participant_id: prepared.participant.id,
        conversation_kind: prepared.conversation_kind, ...(prepared.effect ? { effect: prepared.effect } : {}), receipt };
    } else {
      const peer = await client.getEntity(prepared.peer_id);
      if (peer.className !== "Channel" || peer.broadcast !== true || peer.megagroup === true || peer.creator !== true) throw new Error("channel_ownership_changed");
      await invokeTelegramAdminOnce(client,new Api.channels.DeleteChannel({ channel: peer }));
      result = { ok: true, status: "complete", account_id: accountId, operation,
        peer_id: prepared.peer_id, conversation_kind: "channel", title: prepared.title, receipt };
    }
    journal.put({ ...claimed, status: result.status, result, updated_at: Date.now() });
    journal.inFlight.delete(requestId);
    return result;
  } catch (error) {
    try {
      journal.put({ ...claimed, status: "unknown", error: String((error && (error.errorMessage || error.message)) || "provider_error").slice(0, 100), updated_at: Date.now() });
    } catch { /* the already-fsynced sending claim still prevents a replay */ }
    journal.inFlight.delete(requestId);
    return { ok: true, status: "unknown", account_id: accountId, operation,
      peer_id: claimed.peer_id, receipt, error: "provider_outcome_unknown" };
  }
}
