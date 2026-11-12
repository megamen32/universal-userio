import './dot-test-policy.mjs';import test from 'node:test';import assert from 'node:assert/strict';import {verifiedIncomingEcho} from './dot-incoming-reconcile.mjs';import {textHash} from './dot-relay.mjs';
const room='room',hash=textHash('message'),row={role:'user',text:'message',id:'room~room~CalpicoMessage~123'},record={status:'echo_timeout_requires_review',text_hash:hash,cursor:'baseline'};
test('reconciliation needs one exact echo after the saved cursor',()=>{const snap={ok:true,room,messages:[row]};assert.equal(verifiedIncomingEcho(record,snap,room,hash),row.id);for(const s of [{...snap,room:'other'},{...snap,messages:[]},{...snap,messages:[row,row]}])assert.throws(()=>verifiedIncomingEcho(record,s,room,hash));assert.throws(()=>verifiedIncomingEcho({...record,status:'not_accepted'},snap,room,hash))});
test('caption recovery verifies the document card and requires an explicit audited recovery flag',()=>{
 const r={...record,status:'requires_review',reason:'caption_unverified',media:[{filename:'План.docx'}]};
 const item={...row,attachment_names:['План.docx'],attachment_count:1};const snap={ok:true,room,messages:[item]};
 assert.throws(()=>verifiedIncomingEcho(r,snap,room,hash));
 assert.equal(verifiedIncomingEcho(r,snap,room,hash,{captionRecovery:true}),row.id);
 assert.throws(()=>verifiedIncomingEcho(r,{...snap,messages:[{...item,attachment_names:[],attachment_count:0}]},room,hash,{captionRecovery:true}));
 assert.throws(()=>verifiedIncomingEcho(r,{...snap,messages:[item,item]},room,hash,{captionRecovery:true}));
 assert.throws(()=>verifiedIncomingEcho({...r,reason:'other'},snap,room,hash,{captionRecovery:true}));
});
test('caption recovery requires a matching preflight, one-time intent, and submitted echo audit',async()=>{
 const {verifiedCaptionRecoveryAudit}=await import('./dot-incoming-reconcile.mjs');
 const original={status:'requires_review',reason:'caption_unverified',source:'telegram',text_hash:hash,cursor:'baseline',media:[{filename:'План.docx',sha256:'a'.repeat(64),size:42}]};
 const id='telegram:123:-100123:213',requestId='a'.repeat(32),echo=row.id;
 const proof={request_id:requestId,status:'preflight_complete',operation:'submit_existing_verified_draft_once',source_key:id,source_sha256:'a'.repeat(64),source_size:42,before_relay:{incoming:{[id]:original}},before_media:{groups:{'message:213':{status:'requires_review',echo:null,media:original.media}}}};
 const intent={request_id:requestId,status:'submit_started'},result={status:'submitted_echo_found',echo,echo_text_hash:hash};
 assert.equal(verifiedCaptionRecoveryAudit({proof,intent,result,record:original,id,requestId,echo,hash,messageId:213}),true);
 for(const change of [{result:{...result,echo:'other'}},{intent:{...intent,status:'other'}},{record:{...original,reason:'other'}},{proof:{...proof,source_sha256:'b'.repeat(64)}}]){
  assert.throws(()=>verifiedCaptionRecoveryAudit({proof,intent,result,record:original,id,requestId,echo,hash,messageId:213,...change}));
 }
});

import {echoText} from './dot-relay.mjs';
test('line-break-only echo normalization keeps exact source hash and unique room match',()=>{
 const nl=String.fromCharCode(10),original='Name:'+nl+'message',h=textHash(original),r={status:'submitted_unconfirmed',text_hash:h,echo_normalized_hash:textHash(echoText(original)),cursor:'baseline'};
 const row={role:'user',text:'Name:message',id:'room~room~CalpicoMessage~normalized'},snap={ok:true,room:'room',messages:[row]};
 assert.equal(verifiedIncomingEcho(r,snap,'room',h),row.id);
 assert.throws(()=>verifiedIncomingEcho(r,{...snap,messages:[{...row,text:'Name:different message'}]},'room',h));
 assert.throws(()=>verifiedIncomingEcho(r,{...snap,messages:[row,row]},'room',h));
 assert.throws(()=>verifiedIncomingEcho(r,snap,'wrong-room',h));
 assert.equal(echoText('a'+nl+'b'),'ab');assert.notEqual(echoText('a b'),echoText('ab'));
});
