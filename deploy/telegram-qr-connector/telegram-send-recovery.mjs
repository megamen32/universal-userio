// Durable MTProto send identity. Never invokes an outgoing provider method during recovery.
import { createHash } from 'node:crypto';

const nativeId = v => Number.isSafeInteger(Number(v)) && Number(v) > 0
  && Number(v) <= 2147483647 && String(v) === String(Number(v));

function nonceText(value) {
  try {
    const number = BigInt(String(value));
    if (!number || BigInt.asIntN(64, number) !== number) return '';
    return number.toString();
  } catch {
    return '';
  }
}

export function stableTelegramRandomId(attemptId, accountId, peerId) {
  if (![attemptId, accountId, peerId].every(v => typeof v === 'string' && v.length)) {
    throw new TypeError('A pinned attempt, account and peer are required');
  }
  const bytes = createHash('sha256').update('userio:telegram:text:v1\0')
    .update(JSON.stringify([attemptId, accountId, peerId])).digest();
  const number = bytes.readBigInt64BE(0);
  return (number === 0n ? 1n : number).toString();
}

export function shortState(raw) {
  if (!raw || !['pts', 'qts', 'date'].every(k => Number.isSafeInteger(Number(raw[k]))
      && Number(raw[k]) >= 0) || Number(raw.date) === 0) return null;
  return {pts: Number(raw.pts), qts: Number(raw.qts), date: Number(raw.date)};
}

export async function captureTelegramState(client, Api) {
  try {
    const snapshot = shortState(await client.invoke(new Api.updates.GetState({})));
    if (!snapshot) throw new Error('missing state');
    return snapshot;
  } catch {
    const error = new Error('MTProto state is unavailable before provider send');
    error.submitted = false;
    error.code = 'mtproto_update_checkpoint_unavailable';
    throw error;
  }
}

export function messageIdFromResponse(response, randomId) {
  if (!response || !nonceText(randomId)) return null;
  if (response.className === 'UpdateShortSentMessage') {
    return nativeId(response.id) ? {id: Number(response.id), date: Number(response.date || 0)} : null;
  }
  const matches = (Array.isArray(response.updates) ? response.updates : [])
    .filter(u => u?.className === 'UpdateMessageID' && nonceText(u.randomId) === randomId
      && nativeId(u.id));
  if (matches.length !== 1) return null;
  const id = Number(matches[0].id);
  const message = (response.updates || []).find(u => u?.message && Number(u.message.id) === id
    && u.message.out === true)?.message;
  return {id, date: Number(message?.date || 0)};
}

export async function sendTelegramText({client, Api, peer, text, randomId,
                                        replyToId = '', topMsgId = '', entities}) {
  const nonce = nonceText(randomId);
  if (!nonce || typeof text !== 'string' || !text.trim()) {
    throw new TypeError('A valid MTProto nonce and text are required');
  }
  const inputPeer = await client.getInputEntity(peer);
  const fields = {peer: inputPeer, message: text, randomId: BigInt(nonce)};
  if (replyToId) {
    fields.replyTo = new Api.InputReplyToMessage({replyToMsgId: Number(replyToId),
      ...(topMsgId ? {topMsgId: Number(topMsgId)} : {})});
  }
  if (entities !== undefined) fields.entities = entities;
  const result = await client.invoke(new Api.messages.SendMessage(fields));
  const receipt = messageIdFromResponse(result, nonce);
  if (!receipt) throw new Error('MTProto send returned no matching provider message ID');
  return receipt;
}

export function confirmTelegramUpdate(ledger, accountId, update, {source='updateMessageID'} = {}) {
  if (!update || update.className !== 'UpdateMessageID'
      || !nonceText(update.randomId) || !nativeId(update.id)) {
    return {status: 'ignored'};
  }
  const randomId = nonceText(update.randomId);
  const matched = [...ledger.entries.values()].filter(e => e.random_id === randomId
    && e.account_id === accountId && e.kind === 'send' && e.peer_id);
  if (matched.length !== 1) return {status: matched.length ? 'ambiguous' : 'not_found'};
  const entry = matched[0], messageId = String(update.id);
  if (entry.state === 'refused') return {status: 'refused_conflict'};
  if (entry.state === 'sent') {
    return {status: entry.message_id === messageId ? 'already_sent' : 'id_conflict'};
  }
  if (!['pending', 'unknown'].includes(entry.state)) return {status: 'ignored'};
  ledger.record(entry.attempt_id, {state: 'sent', message_id: messageId,
    code: 'mtproto_update_message_id', receipt_source: source});
  return {status: 'delivered', attempt_id: entry.attempt_id, message_id: messageId};
}

export async function reconcileTelegramDifference({client, Api, ledger, attemptId,
                                                   resolvePeer, maxSlices=4}) {
  const row = ledger.get(attemptId);
  if (!row || !['pending', 'unknown'].includes(row.state)
      || !nonceText(row.random_id) || !row.account_id || !row.peer_id) {
    return {status: 'not_eligible'};
  }
  let cursor = shortState(row.update_state);
  if (!cursor) return {status: 'no_pre_send_checkpoint'};
  const allowed = Math.min(8, Math.max(1, maxSlices));
  for (let i = 0; i < allowed; ++i) {
    // Read-only difference, strictly from the state captured before this attempt.
    const result = await client.invoke(new Api.updates.GetDifference({
      pts: cursor.pts, qts: cursor.qts, date: cursor.date, ptsTotalLimit: 500,
    }));
    const updates = Array.isArray(result?.otherUpdates) ? result.otherUpdates : [];
    for (const update of updates) {
      const applied = row.kind === 'reply'
        ? await confirmTelegramReplyUpdate(ledger, row.account_id, update, {
            client, peer: typeof resolvePeer === 'function' ? resolvePeer(row) : null})
        : confirmTelegramUpdate(ledger, row.account_id, update, {source: 'getDifference'});
      if (applied.status === 'delivered' && applied.attempt_id === attemptId) return applied;
    }
    if (ledger.get(attemptId)?.state === 'sent') return {status: 'delivered'};
    if (result?.className !== 'updates.DifferenceSlice') return {status: 'unresolved'};
    const next = shortState(result.intermediateState);
    if (!next || next.pts < cursor.pts || next.date < cursor.date
      || (next.pts === cursor.pts && next.date === cursor.date)) {
      return {status: 'invalid_difference_cursor'};
    }
    cursor = next;
  }
  return {status: 'bounded_incomplete'};
}

export async function confirmTelegramReplyUpdate(ledger, accountId, update, {client, peer} = {}) {
  if (update?.className !== 'UpdateMessageID' || !nonceText(update.randomId)
      || !nativeId(update.id)) return {status:'ignored'};
  const matching = [...ledger.entries.values()].filter(e => e.random_id === nonceText(update.randomId)
    && e.account_id === accountId && e.kind === 'reply' && e.peer_id);
  if (matching.length !== 1) return {status:matching.length ? 'ambiguous' : 'not_found'};
  const row = matching[0], messageId = String(update.id);
  if (row.state === 'sent') return {status:row.message_id === messageId ? 'already_sent' : 'id_conflict'};
  if (!['unknown','pending'].includes(row.state) || !peer || !client) return {status:'unverified'};
  let message;
  try { message = (await client.getMessages(peer, {ids:[Number(messageId)]}))?.[0]; }
  catch { return {status:'provider_read_unavailable'}; }
  const digest = typeof message?.message === 'string'
    ? createHash('sha256').update(message.message).digest('hex') : '';
  if (!message || String(message.id) !== messageId || message.out !== true
      || !row.body_sha256 || digest !== row.body_sha256
      || String(message.replyTo?.replyToMsgId || '') !== row.reply_to_id
      || row.thread_id && String(message.replyTo?.replyToTopId || '') !== row.thread_id) {
    return {status:'reply_binding_unverified'};
  }
  ledger.record(row.attempt_id, {state:'sent', message_id:messageId,
    date:Number(message.date || 0), code:'mtproto_verified_reply_update', receipt_source:'updateMessageID'});
  return {status:'delivered', attempt_id:row.attempt_id,message_id:messageId};
}
