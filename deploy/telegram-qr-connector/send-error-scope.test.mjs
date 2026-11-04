import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import vm from 'node:vm';
import test from 'node:test';
import { SendAttemptLedger, attemptState } from './send-attempts.mjs';
import { duplicateReceipt, replyAttemptFields } from './send-receipt.mjs';
import { captureTelegramState, confirmTelegramUpdate, reconcileTelegramDifference,
  sendTelegramText, stableTelegramRandomId } from './telegram-send-recovery.mjs';

// Execute the actual send branch with an isolated ledger and provider doubles.
// Never import server.mjs: importing it would start live connectors/listeners.
const source = readFileSync(process.env.USERIO_SEND_SCOPE_SOURCE || new URL('./server.mjs', import.meta.url), 'utf8');
const begin = source.indexOf('  if (url.pathname === "/send" && req.method === "POST") {');
const end = source.indexOf('\n  if (url.pathname', begin + 10);
assert.ok(begin >= 0 && end > begin, 'the exact send branch must be present');
const branch = source.slice(begin, end);
class TelegramReplyRefused extends Error {
  constructor(message = 'source changed') {
    super(message); this.code = 'reply_source_changed'; this.submitted = false;
  }
}
function fixture(t, options = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'userio-send-scope-'));
  t.after(() => rmSync(dir, {recursive: true, force: true}));
  const path = join(dir, 'attempts.json');
  const ledger = new SendAttemptLedger(path);
  const account = 'telegram:fixture', peerId = '9002', slot = 'fixture-slot';
  const token = randomUUID();
  let sendCalls = 0, replyCalls = 0;
  const item = {accountId: account,
    labelPeers: {idPeers: new Map([[peerId, {id: peerId}]]), labelPeers: new Map()},
    client: {
      async getInputEntity(peer) { return peer; },
      async invoke(request) {
        if (request.className === 'updates.GetState') {
          if (options.stateError) throw new Error('synthetic pre-send state refusal');
          return {pts: 100, qts: 0, date: 1700000000};
        }
        if (request.className === 'messages.SendMessage') {
          sendCalls++;
          if (options.sendError) throw options.sendError;
          return {className: 'UpdateShortSentMessage', id: 9101, date: 1700000001};
        }
        throw new Error('unexpected request type: ' + request.className);
      },
    },
  };
  const bindings = {
    url: {pathname: '/send'}, process: {env: {USERIO_API_TOKEN: token}},
    console: {log() {}, error() {}}, sendAttempts: ledger,
    attemptState, duplicateReceipt, replyAttemptFields, TelegramReplyRefused,
    captureTelegramState, confirmTelegramUpdate, reconcileTelegramDifference,
    sendTelegramText, stableTelegramRandomId,
    Api: {updates: {
      GetState: class {className = 'updates.GetState';},
      GetDifference: class {className = 'updates.GetDifference';},
    }, messages: {SendMessage: class {
      className = 'messages.SendMessage';
      constructor(fields) { Object.assign(this, fields); }
    }}},

    liveSlots: options.disconnected ? new Map() : new Map([[slot, item]]),
    slots: new Map([[slot, {accountId: account}]]), syncs: new Map(),
    slotProfiles: {[slot]: account},
    enrollmentOwners: {owners: {[slot]: 'fixture-owner'}, owner: () => 'fixture-owner'},
    async sendTelegramReply(args) {
      replyCalls++;
      if (options.replyError) throw options.replyError;
      return {messageId: 9102, date: 1700000000, replyToId: args.messageId,
        threadId: args.threadId || '', text: args.text, out: true};
    },
  };
  const route = new vm.Script('(async (req, res) => {\n' + branch + '\n})',
    {filename: 'actual-send-branch.mjs'}).runInNewContext(bindings);
  const payload = {account_id: account, chat_id: peerId, slot,
    body: 'synthetic fixture body', attempt_id: 'fixture-attempt'};
  async function request(body = payload, authorized = true) {
    const listeners = new Map();
    const req = {method: 'POST', headers: {authorization: authorized ? 'Bearer ' + token : ''},
      on(event, callback) { listeners.set(event, callback); }};
    const res = {status: null, result: null, ended: 0,
      writeHead(status) { this.status = status; },
      end(value) { this.result = JSON.parse(value); this.ended++; }};
    await route(req, res);
    if (listeners.has('data')) listeners.get('data')(typeof body === 'string' ? body : JSON.stringify(body));
    if (listeners.has('end')) await listeners.get('end')();
    assert.equal(res.ended, 1, 'every request ends once');
    return {status: res.status, ...res.result};
  }
  return {request, ledger, path, payload, account, peerId,
    calls: () => ({sendCalls, replyCalls}),
    restart() { bindings.sendAttempts = new SendAttemptLedger(path); }};
}

test('PEER_FLOOD returns an honest unknown receipt without ReferenceError', async t => {
  const f = fixture(t, {sendError: new Error('400: PEER_FLOOD (messages.SendMessage)')});
  const result = await f.request();
  assert.equal(result.status, 502);
  assert.equal(result.submitted, 'unknown');
  assert.match(result.error, /PEER_FLOOD/);
  const entry = f.ledger.get(f.payload.attempt_id);
  assert.equal(entry.state, 'unknown');
  assert.equal(entry.account_id, f.account);
  assert.equal(entry.peer_id, f.peerId);
  assert.equal(f.calls().sendCalls, 1);
  assert.ok(!readFileSync(f.path, 'utf8').includes(f.payload.body));
  f.restart();
  assert.equal((await f.request()).status, 502);
  assert.equal(f.calls().sendCalls, 1, 'unknown must not be resent after restart');
});
test('a generic transport exception completes HTTP 502 and keeps UNKNOWN', async t => {
  const f = fixture(t, {sendError: new Error('synthetic timeout')});
  assert.equal((await f.request()).code, 'send_outcome_unknown');
  assert.equal(f.ledger.get(f.payload.attempt_id).state, 'unknown');
  assert.equal(f.calls().sendCalls, 1);
});
test('a known reply refusal is recorded without a scope crash', async t => {
  const f = fixture(t, {replyError: new TelegramReplyRefused()});
  const result = await f.request({...f.payload, reply_to_id: '9003', expected_text: 'source fixture'});
  assert.equal(result.status, 409);
  assert.equal(result.submitted, false);
  assert.equal(result.code, 'reply_source_changed');
  assert.equal(f.ledger.get(f.payload.attempt_id).state, 'refused');
  assert.equal(f.calls().sendCalls, 0);
});
test('an explicit pre-send refusal preserves account and peer attribution', async t => {
  const error = Object.assign(new Error('synthetic refusal'), {submitted: false, code: 'fixture_refused'});
  const f = fixture(t, {replyError: error});
  const result = await f.request({...f.payload, reply_to_id: '9003', expected_text: 'source fixture'});
  assert.equal(result.status, 409);
  assert.equal(result.submitted, false);
  assert.equal(f.ledger.get(f.payload.attempt_id).account_id, f.account);
  assert.equal(f.ledger.get(f.payload.attempt_id).peer_id, f.peerId);
});
test('malformed JSON completes the existing error response', async t => {
  const f = fixture(t);
  const result = await f.request('{');
  assert.equal(result.status, 502);
  assert.equal(result.code, 'send_outcome_unknown');
  assert.equal(f.ledger.entries.size, 0);
  assert.equal(f.calls().sendCalls, 0);
});
test('missing authentication never enters the send branch', async t => {
  const f = fixture(t);
  assert.equal((await f.request(f.payload, false)).status, 401);
  assert.equal(f.calls().sendCalls, 0);
  assert.equal(f.ledger.entries.size, 0);
});
test('a disconnected pinned account is refused before provider work', async t => {
  const f = fixture(t, {disconnected: true});
  const result = await f.request();
  assert.equal(result.code, 'account_not_connected');
  assert.equal(result.submitted, false);
  assert.equal(f.calls().sendCalls, 0);
});
test('a pinned account mismatch never falls back to another account', async t => {
  const f = fixture(t);
  assert.equal((await f.request({...f.payload, account_id: 'telegram:other-fixture'})).status, 404);
  assert.equal(f.calls().sendCalls, 0);
});
for (const state of ['pending', 'unknown']) {
  test('an existing ' + state + ' attempt is never replayed', async t => {
    const f = fixture(t);
    f.ledger.record(f.payload.attempt_id, {state});
    assert.equal((await f.request()).submitted, 'unknown');
    assert.equal(f.calls().sendCalls, 0);
    assert.equal(f.ledger.get(f.payload.attempt_id).state, state);
  });
}
test('a successful send returns its original receipt on duplicate invocation', async t => {
  const f = fixture(t);
  assert.equal((await f.request()).message_id, '9101');
  const second = await f.request();
  assert.equal(second.duplicate, true);
  assert.equal(second.message_id, '9101');
  assert.equal(f.calls().sendCalls, 1);
});
test('a successful reply retains its source binding in the duplicate receipt', async t => {
  const f = fixture(t);
  const payload = {...f.payload, reply_to_id: '9003', thread_id: '9004', expected_text: 'source fixture'};
  assert.equal((await f.request(payload)).message_id, 9102);
  const second = await f.request(payload);
  assert.equal(second.reply_to_id, '9003');
  assert.equal(second.thread_id, '9004');
  assert.equal(f.calls().replyCalls, 1);
  assert.equal(f.calls().sendCalls, 0);
});

test('a failed send persists its random_id and pre-send update checkpoint', async t => {
  const f = fixture(t, {sendError: new Error('synthetic lost response')});
  assert.equal((await f.request()).status, 502);
  const row = f.ledger.get(f.payload.attempt_id);
  assert.match(row.random_id, /^-?[1-9][0-9]*$/);
  assert.deepEqual(row.update_state, {pts: 100, qts: 0, date: 1700000000});
  f.restart();
  assert.equal((await f.request()).status, 502);
  assert.equal(f.calls().sendCalls, 1);
});

test('an updateMessageID receipt survives a late failing send response', async t => {
  const f = fixture(t, {sendError: new Error('synthetic lost response')});
  const original = await f.request();
  assert.equal(original.status, 502);
  const entry = f.ledger.get(f.payload.attempt_id);
  const receipt = confirmTelegramUpdate(f.ledger, f.account, {
    className:'UpdateMessageID', randomId: BigInt(entry.random_id), id: 9103});
  assert.equal(receipt.status,'delivered');
  assert.equal(f.ledger.get(f.payload.attempt_id).state,'sent');
  assert.equal((await f.request()).duplicate,true);
  assert.equal(f.calls().sendCalls,1);
});

test('a missing stable operation id is refused before any provider call',async t=>{
  const f=fixture(t);
  const result=await f.request({...f.payload,attempt_id:''});
  assert.equal(result.status,400);
  assert.equal(result.submitted,false);
  assert.equal(f.calls().sendCalls,0);
});
test('a missing pre-send MTProto checkpoint refuses without sending',async t=>{
  const f=fixture(t,{stateError:true});
  const result=await f.request();
  assert.equal(result.status,409);
  assert.equal(result.submitted,false);
  assert.equal(result.code,'mtproto_update_checkpoint_unavailable');
  assert.equal(f.calls().sendCalls,0);
});
