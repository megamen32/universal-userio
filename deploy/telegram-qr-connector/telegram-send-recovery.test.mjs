import assert from 'node:assert/strict';
import test from 'node:test';
import {join} from 'node:path';
import {mkdtempSync, rmSync, readFileSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {SendAttemptLedger} from './send-attempts.mjs';
import {stableTelegramRandomId, messageIdFromResponse, captureTelegramState,
  sendTelegramText, confirmTelegramUpdate, confirmTelegramReplyUpdate, reconcileTelegramDifference} from './telegram-send-recovery.mjs';

const api = {
  updates: {
    GetState: class {className='updates.GetState';},
    GetDifference: class {
      className='updates.GetDifference';
      constructor(fields) {Object.assign(this,fields);}
    },
  },
  messages: {SendMessage: class {
    className='messages.SendMessage';
    constructor(fields) {Object.assign(this,fields);}
  }},
};

function fixture(t, {state='pending', account='telegram:one', id='draft-one', peer='800'} = {}) {
  const dir=mkdtempSync(join(tmpdir(),'userio-mtproto-recovery-'));
  t.after(()=>rmSync(dir,{recursive:true,force:true}));
  const path=join(dir,'send-attempts.json');
  const ledger=new SendAttemptLedger(path);
  const randomId=stableTelegramRandomId(id,account,peer);
  ledger.record(id,{state,account_id:account,peer_id:peer,kind:'send',
    random_id:randomId,update_state:{pts:100,qts:1,date:1700000000}});
  return {ledger,randomId,id,path,peer,account};
}

test('stable signed 64-bit MTProto nonce is scoped to the exact approved attempt',()=>{
  const nonce=stableTelegramRandomId('draft1','account1','900');
  assert.equal(stableTelegramRandomId('draft1','account1','900'),nonce);
  assert.notEqual(stableTelegramRandomId('draft2','account1','900'),nonce);
  assert.notEqual(stableTelegramRandomId('draft1','account1','901'),nonce);
  assert.notEqual(stableTelegramRandomId('draft1','account2','900'),nonce);
  assert.equal(BigInt.asIntN(64,BigInt(nonce)),BigInt(nonce));
});

test('raw MTProto send sets the explicit durable nonce and trusts only its own provider ID',async()=>{
  const randomId=stableTelegramRandomId('draft1','account1','900');
  const calls=[];
  const client={
    async getInputEntity(peer){return peer;},
    async invoke(query){
      calls.push(query);
      return {className:'Updates',updates:[
        {className:'UpdateMessageID',randomId:123n,id:10},
        {className:'UpdateMessageID',randomId:BigInt(randomId),id:87},
        {className:'UpdateNewMessage',message:{id:87,date:1700,out:true}},
      ]};
    },
  };
  const sent=await sendTelegramText({client,Api:api,peer:'bound-peer',text:'offline fixture',randomId});
  assert.deepEqual(sent,{id:87,date:1700});
  assert.equal(calls.length,1);
  assert.equal(calls[0].className,'messages.SendMessage');
  assert.equal(String(calls[0].randomId),randomId);
  assert.equal(calls[0].peer,'bound-peer');
});

test('wrong response random_id cannot be misattributed to the approved draft',async()=>{
  const randomId=stableTelegramRandomId('draft1','a','1');
  const client={async getInputEntity(v){return v;},async invoke(){return {
    className:'Updates',updates:[{className:'UpdateMessageID',randomId:2n,id:100}]};}};
  await assert.rejects(sendTelegramText({client,Api:api,peer:'1',text:'fixture',randomId}),
    /no matching provider message ID/);
});

test('updateMessageID restores UNKNOWN to delivered and survives connector restart',t=>{
  const f=fixture(t,{state:'unknown'});
  const result=confirmTelegramUpdate(f.ledger,f.account,{
    className:'UpdateMessageID',randomId:BigInt(f.randomId),id:71});
  assert.equal(result.status,'delivered');
  assert.equal(f.ledger.get(f.id).message_id,'71');
  assert.equal(new SendAttemptLedger(f.path).get(f.id).state,'sent');
});

test('an update from another account cannot match a nonce',t=>{
  const f=fixture(t);
  assert.equal(confirmTelegramUpdate(f.ledger,'telegram:other',{
    className:'UpdateMessageID',randomId:BigInt(f.randomId),id:71}).status,'not_found');
  assert.equal(f.ledger.get(f.id).state,'pending');
});

test('conflicting update never overwrites an already accepted provider ID',t=>{
  const f=fixture(t,{state:'sent'});
  f.ledger.record(f.id,{state:'sent',message_id:'71'});
  assert.equal(confirmTelegramUpdate(f.ledger,f.account,{
    className:'UpdateMessageID',randomId:BigInt(f.randomId),id:72}).status,'id_conflict');
  assert.equal(f.ledger.get(f.id).message_id,'71');
  assert.equal(f.ledger.record(f.id,{state:'unknown'}).state,'sent');
});

test('read-only MTProto getDifference recovers the original nonce over multiple slices',async t=>{
  const f=fixture(t,{state:'unknown'});
  let calls=0;
  const client={async invoke(query){
    assert.equal(query.className,'updates.GetDifference');
    calls++;
    if(calls===1){
      assert.equal(query.pts,100);
      return {className:'updates.DifferenceSlice',otherUpdates:[],
        intermediateState:{pts:110,qts:1,date:1700000001}};
    }
    assert.equal(query.pts,110);
    return {className:'updates.Difference',otherUpdates:[
      {className:'UpdateMessageID',randomId:BigInt(f.randomId),id:444}]};
  }};
  const result=await reconcileTelegramDifference({client,Api:api,ledger:f.ledger,attemptId:f.id});
  assert.equal(result.status,'delivered');
  assert.equal(f.ledger.get(f.id).message_id,'444');
  assert.equal(calls,2);
});

test('differenceTooLong is UNKNOWN, not a negative proof',async t=>{
  const f=fixture(t);
  const client={async invoke(){return {className:'updates.DifferenceTooLong',pts:9999};}};
  assert.equal((await reconcileTelegramDifference({
    client,Api:api,ledger:f.ledger,attemptId:f.id})).status,'unresolved');
  assert.equal(f.ledger.get(f.id).state,'pending');
});

test('older attempt without saved nonce cannot be falsely resolved',async t=>{
  const f=fixture(t);
  f.ledger.record(f.id,{state:'unknown',random_id:null});
  let calls=0;
  const client={async invoke(){calls++;}};
  assert.equal((await reconcileTelegramDifference({
    client,Api:api,ledger:f.ledger,attemptId:f.id})).status,'not_eligible');
  assert.equal(calls,0);
});

test('corrupt saved journal must fail closed, never erase the attempt',t=>{
  const f=fixture(t);
  writeFileSync(f.path,'{malformed');
  assert.throws(()=>new SendAttemptLedger(f.path));
});

test('in-flight nonces are retained even with a tiny journal limit',t=>{
  const f=fixture(t);
  const ledger=new SendAttemptLedger(f.path,{limit:1});
  for(let n=0;n<5;n++)ledger.record('old_'+n,{state:'sent',message_id:String(n+1)});
  assert.equal(ledger.get(f.id)?.random_id,f.randomId);
});

test('MTProto update checkpoint failure is a pre-provider refusal',async()=>{
  let calls=0;
  const client={async invoke(query){calls++;throw new Error('unavailable');}};
  await assert.rejects(captureTelegramState(client,api),e=>
    e.code==='mtproto_update_checkpoint_unavailable' && e.submitted===false);
  assert.equal(calls,1);
});

test('source-bound reply needs provider readback before declaring delivery',async t=>{
  const f=fixture(t,{state:'unknown'});
  f.ledger.record(f.id,{kind:'reply',reply_to_id:'55',thread_id:'50',
    body_sha256:'f7c3bc1d808e04732adf679965ccc34ca7ae3441f2efae52c0529e7aab7e2e3a'});
  const update={className:'UpdateMessageID',randomId:BigInt(f.randomId),id:89};
  const client={async getMessages(){return [{id:89,out:true,
    message:'different',replyTo:{replyToMsgId:55,replyToTopId:50}}];}};
  assert.equal((await confirmTelegramReplyUpdate(f.ledger,f.account,update,{
    client,peer:'owned-peer'})).status,'reply_binding_unverified');
  assert.equal(f.ledger.get(f.id).state,'unknown');
});

test('a provider-verified source-bound reply retains its source in the original journal',async t=>{
  const {createHash}=await import('node:crypto');
  const f=fixture(t,{state:'unknown'});
  const body='reply body';
  const digest=createHash('sha256').update(body).digest('hex');
  f.ledger.record(f.id,{kind:'reply',reply_to_id:'55',thread_id:'50',
    body_sha256:digest});
  const update={className:'UpdateMessageID',randomId:BigInt(f.randomId),id:89};
  const client={async getMessages(_peer,{ids}) {
    assert.deepEqual(ids,[89]);
    return [{id:89,out:true,message:body,date:1700000010,
      replyTo:{replyToMsgId:55,replyToTopId:50}}];
  }};
  const result=await confirmTelegramReplyUpdate(f.ledger,f.account,update,{
    client,peer:'owned-peer'});
  assert.equal(result.status,'delivered');
  const saved=new SendAttemptLedger(f.path).get(f.id);
  assert.equal(saved.message_id,'89');
  assert.equal(saved.reply_to_id,'55');
  assert.equal(saved.thread_id,'50');
});
