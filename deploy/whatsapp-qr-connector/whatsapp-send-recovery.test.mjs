import assert from 'node:assert/strict';
import test from 'node:test';
import {mkdtempSync,rmSync,readFileSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {WhatsAppSendJournal} from './whatsapp-send-recovery.mjs';

function fx(t) {
  const dir=mkdtempSync(join(tmpdir(),'userio-wa-recovery-'));
  t.after(()=>rmSync(dir,{recursive:true,force:true}));
  const path=join(dir,'send-attempts.json'), ledger=new WhatsAppSendJournal(path);
  const options={attempt_id:'draft-one',user_id:'owner',account_ref:'whatsapp:account-1',
    slot:'account-1',chat_id:'123@s.whatsapp.net',body:'fixture text',reply_id:'',
    message_id:'3EB0FIRSTMESSAGE12345'};
  return {ledger,path,options};
}

function event(f,{fromMe=true,remoteJid,id,body,reply}={}) {
 return {key:{fromMe,remoteJid:remoteJid??f.options.chat_id,id:id??f.options.message_id},
  message:{conversation:body??f.options.body,
    ...(reply?{extendedTextMessage:{text:f.options.body,contextInfo:{stanzaId:reply}}}:{})}};
}

test('journal persists original provider key before any send operation',t=>{
 const f=fx(t),row=f.ledger.prepare(f.options);
 assert.equal(row.state,'pending');
 assert.equal(JSON.parse(readFileSync(f.path)).rows[0].message_id,f.options.message_id);
 assert.equal(new WhatsAppSendJournal(f.path).get(row.attempt_id).state,'pending');
});

test('same attempt cannot issue a second provider write while UNKNOWN',t=>{
 const f=fx(t);f.ledger.prepare(f.options);
 f.ledger.update(f.options.attempt_id,'unknown');
 const repeated=f.ledger.prepare(f.options);
 assert.equal(repeated.state,'unknown');
 assert.equal(f.ledger.rows.size,1);
});

test('another body, peer, account, slot or user cannot hijack existing attempt',t=>{
 const f=fx(t);f.ledger.prepare(f.options);
 for(const changes of [{body:'changed'},{chat_id:'other@s.whatsapp.net'},
   {user_id:'other'},{account_ref:'another'},{slot:'account-2'},
   {message_id:'NEWKEY123456789012'}]){
   assert.throws(()=>f.ledger.prepare({...f.options,...changes}),/conflict/);
 }
});

test('outbound provider update with exact preassigned ID confirms UNKNOWN',t=>{
 const f=fx(t);f.ledger.prepare(f.options);f.ledger.update(f.options.attempt_id,'unknown');
 assert.equal(f.ledger.acceptEvent(f.options.slot,event(f)).status,'sent');
 assert.equal(new WhatsAppSendJournal(f.path).get(f.options.attempt_id).state,'sent');
 assert.equal(f.ledger.scopedStatus({attempt_id:f.options.attempt_id,
  user_id:f.options.user_id,account_ref:f.options.account_ref,chat_id:f.options.chat_id}).message_id,
  f.options.message_id);
});

test('incorrect account slot, ID, peer, body or inbound sender never confirms',t=>{
 const f=fx(t);f.ledger.prepare(f.options);
 assert.notEqual(f.ledger.acceptEvent('account-2',event(f)).status,'sent');
 for(const bad of [{fromMe:false},{remoteJid:'wrong@s.whatsapp.net'},
   {id:'3EB0OTHER1234567'},{body:'wrong'}]){
  assert.notEqual(f.ledger.acceptEvent(f.options.slot,event(f,bad)).status,'sent');
 }
 assert.equal(f.ledger.get(f.options.attempt_id).state,'pending');
});

test('scoped lookup does not expose foreign user or chat',t=>{
 const f=fx(t);f.ledger.prepare(f.options);
 assert.equal(f.ledger.scopedStatus({...f.options,account_ref:'other'}),null);
 assert.equal(f.ledger.scopedStatus({...f.options,user_id:'other'}),null);
 assert.equal(f.ledger.scopedStatus({...f.options,chat_id:'other'}),null);
});

test('late error cannot downgrade a confirmed native message ID',t=>{
 const f=fx(t);f.ledger.prepare(f.options);
 f.ledger.acceptEvent(f.options.slot,event(f));
 assert.equal(f.ledger.update(f.options.attempt_id,'unknown').state,'sent');
 assert.equal(f.ledger.get(f.options.attempt_id).state,'sent');
});

test('journal corruption fails closed rather than clearing historical nonces',t=>{
 const f=fx(t);f.ledger.prepare(f.options);
 writeFileSync(f.path,'{invalid');
 assert.throws(()=>new WhatsAppSendJournal(f.path));
});

test('legacy UNKNOWN with no pre-send journal has no positive receipt',t=>{
 const f=fx(t);
 assert.equal(f.ledger.scopedStatus(f.options),null);
 assert.equal(f.ledger.acceptEvent(f.options.slot,event(f)).status,'not_found_or_ambiguous');
});

test('plain provider success is persisted with the same predetermined ID',t=>{
 const f=fx(t);
 f.ledger.prepare(f.options);
 assert.equal(f.ledger.update(f.options.attempt_id,'sent',f.options.message_id).message_id,
  f.options.message_id);
 assert.throws(()=>f.ledger.update(f.options.attempt_id,'sent','DIFFERENT'),/conflict/);
});
