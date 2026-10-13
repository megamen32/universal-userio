// Account-bound native-call IPC for the EXISTING GramJS client. No login,
// sessions, new client, HTTP auth, durable storage, worker spawning or retries.
// Root authenticates the private route and owns durable admission and delivery.
// Official TL -> Android VoIPService -> Desktop calls_call.cpp checked first;
// installed GramJS2.26.22 constructors/long serialization checked on2026-10-13.

const METHODS = new Set(['messages.getDhConfig', 'phone.requestCall',
  'phone.confirmCall', 'phone.sendSignalingData', 'phone.discardCall']);

function integer(value, minimum, maximum, label) {
  if (!Number.isInteger(value) || value < minimum || value > maximum) throw Error(`invalid ${label}`);
  return value;
}

function decimal(value, {positive = false} = {}) {
  if (typeof value !== 'string' || !/^-?(0|[1-9][0-9]*)$/.test(value)) throw Error('exact decimal long required');
  const result = BigInt(value);
  if (result < -(1n << 63n) || result >= 1n << 63n || (positive && result <= 0n)) throw Error('long outside bounds');
  return result;
}

function providerLong(value, options) {
  if (typeof value === 'number' && !Number.isSafeInteger(value)) throw Error('provider long lost precision');
  return decimal(String(value), options).toString();
}

function bytes(value, minimum = 1, maximum = 32768) {
  if (typeof value !== 'string' || value.length > Math.ceil(maximum / 3) * 4
      || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value)) {
    throw Error('invalid base64 bytes');
  }
  const result = Buffer.from(value, 'base64');
  if (result.length < minimum || result.length > maximum || result.toString('base64') !== value) throw Error('invalid byte bounds');
  return result;
}

function encoded(value, minimum = 1, maximum = 32768) {
  if (!Buffer.isBuffer(value) && !(value instanceof Uint8Array)) throw Error('provider bytes required');
  return bytes(Buffer.from(value).toString('base64'), minimum, maximum).toString('base64');
}

function text(value, maximum, label, optional = false) {
  if (optional && value == null) return null;
  if (typeof value !== 'string' || value.length > maximum) throw Error(`invalid ${label}`);
  return value;
}

function protocol(value) {
  if (!value || typeof value !== 'object') throw Error('native protocol required');
  const min = integer(value.min_layer, 1, 2147483647, 'min layer');
  const max = integer(value.max_layer, min, 2147483647, 'max layer');
  const versions = value.library_versions;
  if (!Array.isArray(versions) || versions.length < 1 || versions.length > 16
      || versions.some(v => typeof v !== 'string' || !v || v.length > 64)
      || typeof value.udp_p2p !== 'boolean' || typeof value.udp_reflector !== 'boolean') throw Error('invalid native protocol');
  return {min_layer: min, max_layer: max, udp_p2p: value.udp_p2p,
    udp_reflector: value.udp_reflector, library_versions: [...versions]};
}

function normalizeProtocol(value) {
  return protocol({min_layer: value.minLayer, max_layer: value.maxLayer,
    udp_p2p: Boolean(value.udpP2p), udp_reflector: Boolean(value.udpReflector),
    library_versions: value.libraryVersions});
}

function tlProtocol(Api, value) {
  const p = protocol(value);
  return new Api.PhoneCallProtocol({minLayer: p.min_layer, maxLayer: p.max_layer,
    udpP2p: p.udp_p2p, udpReflector: p.udp_reflector, libraryVersions: p.library_versions});
}

function normalizeConnection(Api, value) {
  const web = value instanceof Api.PhoneConnectionWebrtc;
  if (!web && !(value instanceof Api.PhoneConnection)) throw Error('unsupported Telegram relay constructor');
  return {id: providerLong(value.id), ip: text(value.ip, 64, 'relay IP'),
    ipv6: text(value.ipv6 ?? '', 64, 'relay IPv6'), port: integer(value.port, 1, 65535, 'relay port'),
    peer_tag: web ? null : encoded(value.peerTag, 16, 16),
    username: web ? text(value.username, 1024, 'relay username') : null,
    password: web ? text(value.password, 1024, 'relay password') : null,
    turn: web && Boolean(value.turn), stun: web && Boolean(value.stun), tcp: Boolean(value.tcp)};
}

function normalizeCall(Api, value) {
  const id = providerLong(value.id, {positive: true});
  if (value instanceof Api.PhoneCallDiscarded) return {type: 'phoneCallDiscarded', id};
  const kind = value instanceof Api.PhoneCallWaiting ? 'phoneCallWaiting'
    : value instanceof Api.PhoneCallAccepted ? 'phoneCallAccepted'
    : value instanceof Api.PhoneCall ? 'phoneCall' : null;
  if (!kind) throw Error('unsupported outgoing phone call constructor');
  const call = {type: kind, id, access_hash: providerLong(value.accessHash),
    admin_id: providerLong(value.adminId, {positive: true}),
    participant_id: providerLong(value.participantId, {positive: true}),
    protocol: normalizeProtocol(value.protocol)};
  if (kind === 'phoneCallAccepted') call.g_b = encoded(value.gB, 128, 256);
  if (kind === 'phoneCall') {
    if (!Array.isArray(value.connections) || value.connections.length > 64) throw Error('invalid relay list');
    Object.assign(call, {key_fingerprint: providerLong(value.keyFingerprint),
      g_a_or_b: encoded(value.gAOrB, 128, 256), p2p_allowed: Boolean(value.p2pAllowed),
      connections: value.connections.map(item => normalizeConnection(Api, item)),
      custom_parameters: value.customParameters == null ? null
        : text(value.customParameters.data, 8192, 'call parameters')});
  }
  return call;
}

/**
 * await createCallIPC({slots:liveSlots, Api, accountId, recipientId, admissionId,
 *   beforeRequest: async metadata => persistClaimAndNonce(metadata),
 *   onReceipt: async receipt => persistProviderReceipt(receipt),
 *   onUpdate: async update => writeWorkerJSONL({type:'telegram_update',update})})
 *
 * rpc(method,params) -> normalized worker result; errors are caught by root
 * and delivered as {type:'rpc_result',request_id,error:true}, never raw logs.
 * Forward that exact client's Raw updates with await ipc.forwardUpdate(update).
 * Call close() AFTER worker teardown; it does not discard any call itself.
 */
export async function createCallIPC({slots, Api, accountId, recipientId, admissionId,
  beforeRequest, onReceipt, onUpdate}) {
  if (typeof accountId !== 'string' || !/^telegram:[1-9][0-9]*$/.test(accountId)) throw Error('exact caller account required');
  const callerId = decimal(accountId.slice('telegram:'.length), {positive: true}).toString();
  const targetId = decimal(recipientId, {positive: true}).toString();
  if (callerId === targetId) throw Error('caller and recipient must differ');
  if (typeof admissionId !== 'string' || !admissionId || admissionId.length > 128
      || ![beforeRequest, onReceipt, onUpdate].every(fn => typeof fn === 'function')) throw Error('protected admission/receipt/update hooks required');
  const item = [...slots.values()].find(value => value.accountId === accountId);
  if (!item?.client || item.client.connected === false) throw Error('requested caller is not connected');
  const peer = item.labelPeers?.idPeers?.get(targetId) ?? await item.client.getEntity(targetId);
  const input = await item.client.getInputEntity(peer);
  if (!(input instanceof Api.InputPeerUser) || providerLong(input.userId, {positive: true}) !== targetId) {
    throw Error('verified private recipient required');
  }
  const user = new Api.InputUser({userId: decimal(targetId), accessHash: decimal(providerLong(input.accessHash))});
  let attempted = false, closed = false, call = null, receiptReady = false, dhCache = null;
  let delivery = Promise.resolve(), delivering = 0;
  const early = [];
  const identity = {account_id: accountId, recipient_id: targetId, admission_id: admissionId};
  const boundCall = value => value.admin_id === callerId && value.participant_id === targetId;
  const guardPeer = value => {
    if (!call || !receiptReady || !value || value.id !== call.id || value.access_hash !== call.access_hash) throw Error('call peer does not match its persisted receipt');
    return new Api.InputPhoneCall({id: decimal(call.id, {positive: true}), accessHash: decimal(call.access_hash)});
  };
  const publish = async update => {
    if (closed) return false;
    if (++delivering > 32) { delivering--; throw Error('call update queue exceeded'); }
    const next = delivery.then(() => closed ? undefined : onUpdate(update));
    delivery = next.catch(() => {});
    try { await next; return true; } finally { delivering--; }
  };
  const queueEarly = update => {
    if (early.length >= 32) throw Error('early phone update queue exceeded');
    early.push(update);
    return true;
  };
  async function forwardUpdate(update) {
    if (closed || !attempted) return false;
    let value;
    if (update instanceof Api.UpdatePhoneCall) {
      if (!(update.phoneCall instanceof Api.PhoneCallWaiting)
          && !(update.phoneCall instanceof Api.PhoneCallAccepted)
          && !(update.phoneCall instanceof Api.PhoneCall)
          && !(update.phoneCall instanceof Api.PhoneCallDiscarded)) return false;
      value = normalizeCall(Api, update.phoneCall);
      if (value.type !== 'phoneCallDiscarded' && !boundCall(value)) return false;
    } else if (update instanceof Api.UpdatePhoneCallSignalingData) {
      value = {type: 'signaling', id: providerLong(update.phoneCallId, {positive: true}), data: encoded(update.data)};
    } else return false;
    if (!receiptReady) return queueEarly(value);
    if (value.id !== call.id) return false;
    return publish(value);
  }
  async function rpc(method, params = {}) {
    if (closed || !METHODS.has(method)) throw Error('call RPC is unavailable');
    if (!params || typeof params !== 'object' || Array.isArray(params)) throw Error('call RPC params required');
    if (method === 'messages.getDhConfig') {
      if (params.version !== 0 || params.random_length !== 256) throw Error('bounded full DH config required');
      const result = await item.client.invoke(new Api.messages.GetDhConfig({version: 0, randomLength: 256}));
      if (result instanceof Api.messages.DhConfig) {
        dhCache = {g: integer(result.g, 2, 7, 'DH generator'), p: encoded(result.p, 256, 256)};
      } else if (!(result instanceof Api.messages.DhConfigNotModified) || !dhCache) throw Error('full provider DH config unavailable');
      return {...dhCache, random: encoded(result.random, 256, 256)};
    }
    if (method === 'phone.requestCall') {
      if (attempted) throw Error('call was already attempted; reconcile its receipt');
      if (params.user_id !== targetId || params.video !== false) throw Error('fixed voice recipient required');
      const request = new Api.phone.RequestCall({userId: user,
        randomId: integer(params.random_id, 0, 2147483647, 'request nonce'),
        gAHash: bytes(params.g_a_hash, 32, 32), protocol: tlProtocol(Api, params.protocol), video: false});
      attempted = true; // Failure/timeout after this point never permits another attempt.
      await beforeRequest({...identity, random_id: params.random_id});
      const result = normalizeCall(Api, (await item.client.invoke(request)).phoneCall);
      if (!boundCall(result)) throw Error('provider receipt belongs to another call participant');
      call = result;
      await onReceipt({...identity, call: result});
      receiptReady = true;
      for (const update of early.splice(0)) if (update.id === call.id) await publish(update);
      if (result.type === 'phoneCallAccepted') await publish(result);
      return result;
    }
    if (method === 'phone.confirmCall') {
      const request = new Api.phone.ConfirmCall({peer: guardPeer(params.peer),
        gA: bytes(params.g_a, 128, 256), keyFingerprint: decimal(params.key_fingerprint),
        protocol: tlProtocol(Api, params.protocol)});
      const result = normalizeCall(Api, (await item.client.invoke(request)).phoneCall);
      if (result.type !== 'phoneCall' || result.id !== call.id || !boundCall(result)
          || result.key_fingerprint !== params.key_fingerprint) throw Error('provider confirmation does not match the call');
      return result;
    }
    if (method === 'phone.sendSignalingData') {
      return await item.client.invoke(new Api.phone.SendSignalingData({peer: guardPeer(params.peer), data: bytes(params.data)})) === true;
    }
    const request = new Api.phone.DiscardCall({peer: guardPeer(params.peer),
      duration: integer(params.duration, 0, 600, 'call duration'),
      connectionId: params.connection_id === '0' ? 0n : (() => { throw Error('invalid connection ID'); })(),
      video: params.video === false ? false : (() => { throw Error('voice discard required'); })(),
      reason: params.reason === 'hangup' ? new Api.PhoneCallDiscardReasonHangup()
        : params.reason === 'missed' ? new Api.PhoneCallDiscardReasonMissed()
        : (() => { throw Error('invalid discard reason'); })()});
    const result = await item.client.invoke(request);
    for (const update of result?.updates ?? []) await forwardUpdate(update);
    return true;
  }
  return {rpc, forwardUpdate, identity: Object.freeze({...identity}),
    async close() { closed = true; early.length = 0; await delivery; }};
}
