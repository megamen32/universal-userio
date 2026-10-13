// Fast unit expected0.1s/max2s: no operator substitution, sender ambiguity or
// cross-user group author/own-message confusion in personal WhatsApp enrollment.
import assert from 'node:assert/strict';
import {accountRef,senderSlots,personalEnvelope} from './tenant-routing.mjs';
const owners={owners:{'account-2':'alice','account-3':'bob'},allowed:(slot,user)=>(owners.owners[slot]||'user_owner')===user};
const slots=new Map([['account-1',{status:'connected',socket:{user:{id:'1234567:0@s.whatsapp.net'}}}],
 ['account-2',{status:'connected',socket:{user:{id:'1234567:1@s.whatsapp.net'}}}],
 ['account-3',{status:'connected',socket:{user:{id:'9876543:0@s.whatsapp.net'}}}]]);
assert.equal(accountRef('account-1','1234567:0@s.whatsapp.net',owners),'whatsapp:1234567');
const ref=accountRef('account-2','1234567:1@s.whatsapp.net',owners);
assert.deepEqual(senderSlots({},slots,owners),['account-1']);
assert.deepEqual(senderSlots({user_id:'alice',account_ref:ref},slots,owners),['account-2']);
assert.throws(()=>senderSlots({user_id:'bob',account_ref:ref},slots,owners));
assert.throws(()=>senderSlots({user_id:'alice',account_ref:'whatsapp:1234567'},slots,owners));
assert.throws(()=>senderSlots({user_id:'alice',account_ref:ref,slot:'account-3'},slots,owners));
const input={key:{id:'source1',remoteJid:'group@g.us',participant:'7654321@s.whatsapp.net'},pushName:'Member',messageTimestamp:1000,message:{conversation:'hello'}};
const msg=personalEnvelope('account-2',input,{ref,own:'1234567@s.whatsapp.net',peer:'group@g.us'});
assert.equal(msg.sender,'7654321@s.whatsapp.net');assert.equal(msg.peer_id,'group@g.us');
assert.equal(msg.message_id,ref+'|group@g.us:source1');assert.equal(msg.sender_name,'Member');
const sent=personalEnvelope('account-2',{...input,key:{...input.key,fromMe:true}},{ref,own:'1234567@s.whatsapp.net',peer:'group@g.us',reconciliation:true});
assert.equal(sent.direction,'outgoing');assert.equal(sent.sender,'1234567@s.whatsapp.net');assert.equal(sent.reconciliation,true);
console.log('Personal WhatsApp source/owner/sender: PASS');
