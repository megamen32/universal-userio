// Fast unit: exact caller/recipient, persisted one-shot receipt, current GramJS
// constructors, early updates and native IPC fields. Expected5s/emergency50s.
// No Telegram login, client connection, provider request, media or browser.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import path from 'node:path';
import test from 'node:test';
import {createCallIPC} from './call-ipc.mjs';

const C = () => class { constructor(params = {}) { Object.assign(this, params); } };
const Api = {};
for (const name of ['InputPeerUser', 'InputPeerChannel', 'InputUser', 'InputPhoneCall',
  'PhoneCallProtocol', 'PhoneCallWaiting', 'PhoneCallAccepted', 'PhoneCall', 'PhoneCallDiscarded',
  'PhoneCallRequested', 'PhoneCallEmpty', 'UpdatePhoneCall', 'UpdatePhoneCallSignalingData',
  'PhoneConnection', 'PhoneConnectionWebrtc', 'PhoneCallDiscardReasonHangup', 'PhoneCallDiscardReasonMissed']) Api[name] = C();
Api.phone = Object.fromEntries(['RequestCall', 'ConfirmCall', 'SendSignalingData', 'DiscardCall'].map(n => [n, C()]));
Api.messages = Object.fromEntries(['GetDhConfig', 'DhConfig', 'DhConfigNotModified'].map(n => [n, C()]));
const callId = '9007199254740993', accessHash = '-9223372036854775807';
const protocol = {min_layer: 92, max_layer: 92, udp_p2p: true,
  udp_reflector: true, library_versions: ['13.0.0']};
const requestParams = {user_id: '222', random_id: 19, g_a_hash: Buffer.alloc(32, 1).toString('base64'), protocol, video: false};
const peerParams = {id: callId, access_hash: accessHash};
const nativeProtocol = sdk => new sdk.PhoneCallProtocol({minLayer: 92, maxLayer: 92,
  udpP2p: true, udpReflector: true, libraryVersions: ['13.0.0']});
const waiting = (sdk, extra = {}) => new sdk.PhoneCallWaiting({id: BigInt(callId), accessHash: BigInt(accessHash),
  adminId: 111n, participantId: 222n, protocol: nativeProtocol(sdk), date: 1, ...extra});

async function setup({sdk = Api, invoke, onReceipt, beforeRequest, input, accountId = 'telegram:111'} = {}) {
  const invoked = [], events = [], timeline = [], receipts = [];
  const client = {connected: true, getEntity: async () => { throw Error('no fallback peer resolution'); },
    getInputEntity: async peer => peer,
    invoke: async request => {
      invoked.push(request); timeline.push('invoke');
      if (invoke) return invoke(request);
      if (request instanceof sdk.messages.GetDhConfig) return new sdk.messages.DhConfig({g: 3,
        p: Buffer.alloc(256, 2), random: Buffer.alloc(256, 3), version: 9});
      if (request instanceof sdk.phone.RequestCall) return {phoneCall: waiting(sdk)};
      if (request instanceof sdk.phone.SendSignalingData) return true;
      if (request instanceof sdk.phone.DiscardCall) return {updates: [new sdk.UpdatePhoneCall({phoneCall:
        new sdk.PhoneCallDiscarded({id: BigInt(callId)})})]};
      if (request instanceof sdk.phone.ConfirmCall) return {phoneCall: new sdk.PhoneCall({
        id: BigInt(callId), accessHash: BigInt(accessHash), adminId: 111n, participantId: 222n,
        protocol: nativeProtocol(sdk), gAOrB: Buffer.alloc(256, 4), keyFingerprint: -4n,
        date: 1, startDate: 1, p2pAllowed: false, customParameters: {data: '{"network_use_default_route":true}'},
        connections: [new sdk.PhoneConnection({id: 5n, ip: '127.0.0.1', ipv6: '', port: 9000,
          peerTag: Buffer.alloc(16, 5), tcp: true}), new sdk.PhoneConnectionWebrtc({id: 6n,
          ip: '127.0.0.2', ipv6: '::1', port: 9001, username: 'relay', password: 'ephemeral', turn: true, stun: true})]})};
      throw Error('unexpected test RPC');
    }};
  const item = {accountId, client, labelPeers: {idPeers: new Map([['222', input ?? new sdk.InputPeerUser({userId: 222n, accessHash: 333n})]])}};
  const foreignClient = {getInputEntity() { throw Error('foreign caller accessed'); }};
  const ipc = await createCallIPC({slots: new Map([['foreign', {accountId: 'telegram:999', client: foreignClient}], ['owned', item]]), sdk,
    Api: sdk, accountId, recipientId: '222', admissionId: 'protected-attempt',
    beforeRequest: async info => { timeline.push('claim'); if (beforeRequest) await beforeRequest(info); },
    onReceipt: async receipt => { timeline.push('receipt'); receipts.push(receipt); if (onReceipt) await onReceipt(receipt); },
    onUpdate: async update => { events.push(update); timeline.push('update'); }});
  return {ipc, invoked, events, timeline, receipts, client};
}

test('all five RPCs use the fixed client and normalized long/Buffer/protocol/relay constructors', async () => {
  const {ipc, invoked, events, timeline, receipts} = await setup();
  const dh = await ipc.rpc('messages.getDhConfig', {version: 0, random_length: 256});
  assert.equal(dh.g, 3); assert.equal(Buffer.from(dh.p, 'base64').length, 256);
  const call = await ipc.rpc('phone.requestCall', requestParams);
  assert.equal(call.id, callId); assert.equal(call.access_hash, accessHash);
  assert.deepEqual(timeline.slice(1), ['claim', 'invoke', 'receipt']);
  assert.equal(receipts[0].account_id, 'telegram:111');
  const request = invoked[1];
  assert.ok(request.userId instanceof Api.InputUser); assert.equal(request.userId.userId, 222n);
  assert.ok(Buffer.isBuffer(request.gAHash)); assert.ok(request.protocol instanceof Api.PhoneCallProtocol);
  const confirmed = await ipc.rpc('phone.confirmCall', {peer: peerParams,
    g_a: Buffer.alloc(256).toString('base64'), key_fingerprint: '-4', protocol});
  assert.equal(confirmed.key_fingerprint, '-4');
  assert.equal(confirmed.connections[0].peer_tag, Buffer.alloc(16, 5).toString('base64'));
  assert.equal(confirmed.connections[0].tcp, true);
  assert.equal(confirmed.connections[1].password, 'ephemeral'); assert.equal(confirmed.connections[1].turn, true);
  assert.equal(await ipc.rpc('phone.sendSignalingData', {peer: peerParams, data: 'AQID'}), true);
  assert.equal(await ipc.rpc('phone.discardCall', {peer: peerParams, duration: 2,
    reason: 'hangup', connection_id: '0', video: false}), true);
  assert.equal(events[0].type, 'phoneCallDiscarded');
  assert.ok(invoked[4].reason instanceof Api.PhoneCallDiscardReasonHangup);
  assert.equal(invoked[4].peer.id, BigInt(callId)); assert.equal(invoked[4].peer.accessHash, BigInt(accessHash));
  await ipc.close();
});

test('timeout and admission refusal never permit another requestCall', async () => {
  for (const refuseClaim of [false, true]) {
    const {ipc, invoked} = await setup({invoke: async () => { throw Error('provider outcome unknown'); },
      beforeRequest: async () => { if (refuseClaim) throw Error('claim refused'); }});
    await assert.rejects(ipc.rpc('phone.requestCall', requestParams));
    await assert.rejects(ipc.rpc('phone.requestCall', requestParams), /already attempted/);
    assert.equal(invoked.length, refuseClaim ? 0 : 1);
    await ipc.close();
  }
});

test('recipient/peer mismatch and unsupported RPC never invoke a foreign action', async () => {
  const {ipc, invoked} = await setup();
  await assert.rejects(ipc.rpc('phone.requestCall', {...requestParams, user_id: '999'}));
  await assert.rejects(ipc.rpc('phone.requestCall', {...requestParams, g_a_hash: 'not base64'}));
  await assert.rejects(ipc.rpc('phone.sendSignalingData', {peer: peerParams, data: 'AQID'}));
  await assert.rejects(ipc.rpc('phone.acceptCall', {}));
  assert.equal(invoked.length, 0);
  await ipc.rpc('phone.requestCall', requestParams);
  await assert.rejects(ipc.rpc('phone.discardCall', {peer: {...peerParams, id: '12'}, duration: 1,
    reason: 'hangup', connection_id: '0', video: false}));
  assert.equal(invoked.length, 1);
  await ipc.close();
});

test('early accepted update waits for persisted exact receipt; unrelated and incoming calls are ignored', async () => {
  let ipc;
  const fixture = await setup({invoke: async request => {
    await ipc.forwardUpdate(new Api.UpdatePhoneCall({phoneCall: new Api.PhoneCallAccepted({
      ...waiting(Api), gB: Buffer.alloc(256, 7)})}));
    await ipc.forwardUpdate(new Api.UpdatePhoneCallSignalingData({phoneCallId: 12n, data: Buffer.from('foreign')}));
    return {phoneCall: waiting(Api)};
  }, onReceipt: async () => { assert.equal(fixture.events.length, 0); }});
  ipc = fixture.ipc;
  assert.equal(await ipc.forwardUpdate(new Api.UpdatePhoneCall({phoneCall: waiting(Api)})), false);
  await ipc.rpc('phone.requestCall', requestParams);
  assert.equal(fixture.events.length, 1); assert.equal(fixture.events[0].type, 'phoneCallAccepted');
  assert.equal(fixture.events[0].g_b, Buffer.alloc(256, 7).toString('base64'));
  assert.equal(await ipc.forwardUpdate(new Api.UpdatePhoneCall({phoneCall: new Api.PhoneCallRequested()})), false);
  assert.equal(await ipc.forwardUpdate(new Api.UpdatePhoneCall({phoneCall: new Api.PhoneCallEmpty({id: 12n})})), false);
  assert.equal(await ipc.forwardUpdate(new Api.UpdatePhoneCall({phoneCall: waiting(Api, {id: 12n})})), false);
  assert.equal(await ipc.forwardUpdate(new Api.UpdatePhoneCallSignalingData({phoneCallId: BigInt(callId), data: Buffer.from([1, 2])})), true);
  assert.equal(fixture.events[1].data, 'AQI=');
  await ipc.close();
  assert.equal(await ipc.forwardUpdate(new Api.UpdatePhoneCallSignalingData({phoneCallId: BigInt(callId), data: Buffer.from([1])})), false);
});

test('wrong recipient provider receipt cannot authorize confirm or discard', async () => {
  const {ipc, invoked} = await setup({invoke: async () => ({phoneCall: waiting(Api, {participantId: 999n})})});
  await assert.rejects(ipc.rpc('phone.requestCall', requestParams), /another call participant/);
  await assert.rejects(ipc.rpc('phone.discardCall', {peer: peerParams, duration: 0,
    reason: 'missed', connection_id: '0', video: false}));
  assert.equal(invoked.length, 1);
  await ipc.close();
});

test('private exact connected caller required; groups never enter voice RPC', async () => {
  await assert.rejects(setup({input: new Api.InputPeerChannel({channelId: 222n, accessHash: 333n})}), /private recipient/);
  await assert.rejects(setup({input: new Api.InputPeerUser({userId: 223n, accessHash: 333n})}), /private recipient/);
  await assert.rejects(createCallIPC({slots: new Map(), Api, accountId: 'telegram:111',
    recipientId: '222', admissionId: 'protected', beforeRequest: async () => {},
    onReceipt: async () => {}, onUpdate: async () => {}}), /not connected/);
});

test('actual installed GramJS constructor serialization remains network-free',
  {skip: !process.env.USERIO_GRAMJS_TEST_MODULE_ROOT}, async () => {
    const require = createRequire(path.resolve(process.env.USERIO_GRAMJS_TEST_MODULE_ROOT, 'package.json'));
    const sdk = require('telegram').Api;
    const {ipc, invoked} = await setup({sdk});
    await ipc.rpc('messages.getDhConfig', {version: 0, random_length: 256});
    await ipc.rpc('phone.requestCall', requestParams);
    await ipc.rpc('phone.confirmCall', {peer: peerParams, g_a: Buffer.alloc(256).toString('base64'),
      key_fingerprint: '-4', protocol});
    await ipc.rpc('phone.sendSignalingData', {peer: peerParams, data: 'AQID'});
    await ipc.rpc('phone.discardCall', {peer: peerParams, duration: 1,
      reason: 'missed', connection_id: '0', video: false});
    for (const request of invoked) assert.ok(request.getBytes().length >= 4);
    await ipc.close();
  });
