import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {findOriginalTelegramQuestion,verifiedOriginalMessengerReceipt} from './dot-question-consumer.mjs';

const OWNER='111111111',ACCOUNT='telegram:999999';
const original={account_ref:ACCOUNT,peer_id:OWNER,source_provider_message_id:'42',echoed_dot_message_id:'echo'};
function state(status='accepted'){
 return {botAccountId:ACCOUNT,state:{
  incoming:{[ACCOUNT+':'+OWNER+':42']:{
   source:'telegram',status,echo:'echo',source_message_ids:['42']}},
  mirror:{published:{}}}};
}
const rows=[{id:'old',role:'user'}, {id:'echo',role:'user'},{id:'answer',role:'assistant'}];
const message={id:'answer',role:'assistant',text:'reply'};
const hex=text=>createHash('sha256').update(text).digest('hex');
function approved(body='dots:\nreply'){
 const action_id='action_'+'a'.repeat(64);
 return {
  status:'accepted',question_delivery_state:'sent',
  account_ref:ACCOUNT,peer_id:OWNER,source_provider_message_id:'42',
  source_message_id:ACCOUNT+'|'+OWNER+':42',conversation_id:'conv_verified',
  event_seq:7,revision:'0',epoch:2,
  action_id,request_id:'original_request_0001',
  receipt_ref:'messenger:'+action_id,
  provider_message_id:'201',message_sha256:hex(body),
 };
}
test('nearest accepted Telegram echo resolves exactly original provider ID without fabricating source_message_id',()=>{
 const v=findOriginalTelegramQuestion(state(),message,rows,OWNER);
 assert.equal(v.status,'linked');
 assert.equal(v.via,'nearest_user');
 assert.deepEqual(v.source,original);
 assert.equal(Object.hasOwn(v.source,'source_message_id'),false);
});
test('native reply to exact Telegram echo keeps account and provider message ID',()=>{
 const v=findOriginalTelegramQuestion(state(),{...message,reply_to:'echo'},rows,OWNER);
 assert.equal(v.status,'linked');
 assert.equal(v.via,'reply');
 assert.equal(v.source.source_provider_message_id,'42');
});
test('wrong source account, wrong peer, missing echo or unverified receipt never maps a question',()=>{
 const x=state();const key=Object.keys(x.state.incoming)[0];
 x.state.incoming[ACCOUNT+':wrong:42']=x.state.incoming[key];delete x.state.incoming[key];
 assert.equal(findOriginalTelegramQuestion(x,message,rows,OWNER).status,'none');
 const y=state('send_uncertain');
 assert.equal(findOriginalTelegramQuestion(y,message,rows,OWNER).status,'unverified');
 const z=state();z.state.incoming[Object.keys(z.state.incoming)[0]].source_message_ids=['43'];
 assert.equal(findOriginalTelegramQuestion(z,message,rows,OWNER).status,'unverified');
});
test('pending Telegram-origin echo with unknown dot receipt cannot bypass the original fence',()=>{
 const x=state();
 const key=Object.keys(x.state.incoming)[0];
 x.state.incoming[key]={source:'telegram',status:'send_uncertain',
  text_hash:hex('tg question'),source_message_ids:['42']};
 const r=[{id:'old',role:'user',text:'prior'},{id:'possible_echo',role:'user',text:'tg question'},
  {id:'answer',role:'assistant',text:'reply'}];
 assert.equal(findOriginalTelegramQuestion(x,r[2],r,OWNER).status,'unverified');
});
test('duplicate or missing Telegram source receipt blocks rather than guessing another account',()=>{
 const x=state();
 x.state.incoming[ACCOUNT+':'+OWNER+':43']={source:'telegram',status:'accepted',echo:'echo',source_message_ids:['43']};
 assert.equal(findOriginalTelegramQuestion(x,message,rows,OWNER).status,'unverified');
 const y=state();y.state.incoming[ACCOUNT+':'+OWNER+':42'].source_message_ids=[];
 assert.equal(findOriginalTelegramQuestion(y,message,rows,OWNER).status,'unverified');
});
test('ordinary dot message without accepted Telegram echo remains unrelated',()=>{
 assert.equal(findOriginalTelegramQuestion(state(),{id:'standalone',role:'user'},rows,OWNER).status,'none');
 assert.equal(findOriginalTelegramQuestion(state(),{...message,reply_to:'other'},rows,OWNER).status,'none');
});
test('reply to already-answered question cannot start a second provider action',()=>{
 const s=state();
 s.state.mirror.published.reply={status:'accepted',question_source:original,receipts:['201']};
 const x=findOriginalTelegramQuestion(s,{id:'later',role:'assistant',reply_to:'reply'},[...rows,{id:'reply',role:'assistant'},{id:'later',role:'assistant'}],OWNER);
 assert.equal(x.status,'already_answered');
});
test('verify exact original UserIO Messenger source, action ID, receipt and hash',()=>{
 const v=verifiedOriginalMessengerReceipt(original,'dots:\nreply',approved());
 assert.equal(v.source_message_id,ACCOUNT+'|'+OWNER+':42');
 assert.equal(v.receipt_ref,'messenger:action_'+'a'.repeat(64));
 assert.equal(v.provider_message_id,'201');
 assert.equal(Object.hasOwn(v,'lease_token'),false);
});
test('reject forged, stale or ambiguous receipt and source instead of accepting provider result',()=>{
 const base=approved();
 const changes=[
  {source_message_id:original.echoed_dot_message_id+'~CalpicoMessage~bad'},
  {source_message_id:ACCOUNT+'|'+OWNER+':999'},
  {source_message_id:'telegram:another|'+OWNER+':42'},
  {source_provider_message_id:'43'},
  {account_ref:'telegram:other'},
  {peer_id:'987'},
  {status:'sending'},
  {question_delivery_state:'unknown'},
  {question_delivery_state:'requires_review'},
  {receipt_ref:'messenger:other'},
  {action_id:'not_original'},
  {provider_message_id:'0'},
  {message_sha256:'0'.repeat(64)},
  {event_seq:0},
  {epoch:0},
  {request_id:'new'},
 ];
 for(const patch of changes)assert.throws(
  ()=>verifiedOriginalMessengerReceipt(original,'dots:\nreply',{...base,...patch}),
  /original_messenger_receipt_requires_review/);
 assert.throws(()=>verifiedOriginalMessengerReceipt(original,'different text',base));
});
