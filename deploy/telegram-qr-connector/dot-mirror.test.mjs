import './dot-test-policy.mjs';
import test from 'node:test';import assert from 'node:assert/strict';import {mkdtempSync} from 'node:fs';import {tmpdir} from 'node:os';import {join} from 'node:path';import {DotRelay,ROOM,OWNER,textHash} from './dot-relay.mjs';
import {createHash} from 'node:crypto';
import {dotTelegramMediaParams,dotTelegramMediaReceipts} from './dot-egress.mjs';import {DotMirror,sensitiveText,mirrorParts,mirrorSendOptions,mirrorSourceHash} from './dot-mirror.mjs';
const msg=(id,role,text)=>({id,role,text});
function fixture(options={}){
 let rows=[msg('baseline','user','old history')],time=0,sends=[],edits=[],mediaSends=[],questionApprovals=[],reads=0;
 class FakeCustomFile{constructor(name,size,path,buffer){Object.assign(this,{name,size,path,buffer});}}
 const config={
  statePath:join(mkdtempSync(join(tmpdir(),'dot-mirror-')),'state.json'),
  botAccountId:'telegram:999999',agentId:'browser',tabId:18,
  browserCall:async(action,args)=>{
   reads++;
   if(action==='dot_media_read'){
    if(options.mediaReadError)throw Error('browser_image_unavailable');
    return options.media||[];
   }
   return {ok:true,room:ROOM,cursor:rows.at(-1)?.id,messages:rows};
  },
  send:async(owner,text,replyTo=null)=>{
   sends.push(replyTo==null?{owner,text}:{owner,text,replyTo});
   if(options.sendError)throw Error('unknown');
   return options.numericReceipts?String(sends.length):'tg'+sends.length;
  },
  sendMedia:async(owner,media,caption,replyTo,source)=>{
   const optionsParams=dotTelegramMediaParams(media,caption,replyTo,FakeCustomFile);
   mediaSends.push({owner,caption,replyTo,source,count:media.length,files:optionsParams.file,journalStatusAtCall:relay.state.outgoing['mirror:'+source.source_message_id+':media:0']?.status});
   if(options.mediaSendError)throw Error('provider_unknown');
   return dotTelegramMediaReceipts(media.map((_,i)=>({id:100+mediaSends.length*10+i})),media.length);
  },
  ...(options.withQuestionApproval?{questionApproval:async({source,dot_source_id,body,original_reply_receipt})=>{
   questionApprovals.push({source,dot_source_id,body,original_reply_receipt,
    journalStatusAtCall:relay.state.outgoing['mirror:'+dot_source_id+':0']?.status});
   if(options.questionApprovalError)throw Error('backend_original_provider_unknown');
   const action_id='action_'+'a'.repeat(64);
   return options.questionResponse||{
    status:'accepted',question_delivery_state:'sent',
    account_ref:source.account_ref,peer_id:source.peer_id,
    source_provider_message_id:source.source_provider_message_id,
    source_message_id:source.account_ref+'|'+source.peer_id+':'+source.source_provider_message_id,
    conversation_id:'conv_verified',
    event_seq:42,revision:'0',epoch:1,
    action_id,request_id:'original_request_0001',
    receipt_ref:'messenger:'+action_id,
    provider_message_id:'201',message_sha256:textHash(body),
   };
  }}:{}),
  edit:async(owner,receipt,text)=>{
   edits.push({owner,receipt,text});if(options.editError)throw Error('unknown');
  },wait:async()=>{}
 };
 let relay=new DotRelay(config),mirror=new DotMirror(relay,{now:()=>time,stableMs:10});
 return {get relay(){return relay},get mirror(){return mirror},rows:v=>{rows=v},
  advance:()=>{time+=11},sends,mediaSends,questionApprovals,edits,reads:()=>reads,
  restart:()=>{relay=new DotRelay(config);mirror=new DotMirror(relay,{now:()=>time,stableMs:10})}};
}
const imageEntry=(index,seed=index)=>{
 const bytes=Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]),Buffer.alloc(512,seed)]);
 const sha256=createHash('sha256').update(bytes).digest('hex');
 return {filename:'dot-'+index+'-'+sha256.slice(0,16)+'.png',bytes,sha256,mime:'image/png'};
};
const withImages=(id,role,text,count=1)=>({...msg(id,role,text),
 attachment_count:count,attachment_names:Array.from({length:count},(_,i)=>'photo'+i+'.png'),
 attachment_fingerprints:Array.from({length:count},(_,i)=>createHash('sha256').update('source_'+i+'_'+id).digest('hex'))});

async function settle(x){await x.mirror.tick();x.advance();return x.mirror.tick();}
test('activation baseline skips all history, repeat does not send',async()=>{const x=fixture();assert.equal((await x.mirror.tick()).status,'baseline_initialized');await settle(x);assert.equal(x.sends.length,0)});
test('both new authors labelled, restart and stable IDs prevent duplicates',async()=>{const x=fixture();await x.mirror.tick();x.rows([msg('baseline','user','old'),msg('u','user','hello'),msg('a','assistant','background result')]);await settle(x);assert.deepEqual(x.sends,[{owner:OWNER,text:'Test owner:\nhello'},{owner:OWNER,text:'dots:\nbackground result'}]);x.restart();await settle(x);assert.equal(x.sends.length,2)});
test('unverified incoming Telegram echo holds assistant instead of bypassing backend claim',async()=>{
 const x=fixture();await x.mirror.tick();
 x.relay.state.incoming['telegram:999999:'+OWNER+':1']={source:'telegram',status:'accepted',echo:'echo'};
 x.rows([msg('baseline','user','old'),msg('echo','user','from TG'),msg('a','assistant','answer')]);
 assert.equal((await settle(x)).status,'question_origin_requires_review');
 assert.deepEqual(x.sends,[]);assert.equal(x.relay.state.mirror.cursor,'echo');
});
test('uncertain incoming origin pauses instead of echo duplication',async()=>{const x=fixture();await x.mirror.tick();x.relay.state.incoming['telegram:999999:111111111:1']={source:'telegram',status:'send_uncertain',text_hash:textHash('from TG')};x.rows([msg('baseline','user','old'),msg('echo','user','from TG')]);assert.equal((await settle(x)).status,'incoming_origin_uncertain');assert.equal(x.sends.length,0)});
test('secret patterns and explicit credential labels held with no raw excerpts',async()=>{for(const text of ['123456789:'+ 'A'.repeat(35),'sk-'+ 'A'.repeat(30),'Authorization: Bearer abcdefghijklmnop','-----BEGIN RSA PRIVATE KEY-----','password: hunter2','токен: hidden','Пароль передан через SSS'])assert.equal(sensitiveText(text),true);const x=fixture();await x.mirror.tick();x.rows([msg('baseline','user','old'),msg('secret','assistant','password: hunter2'),msg('safe','assistant','ready')]);await settle(x);assert.deepEqual(x.sends.map(s=>s.text),['dots:\nready']);assert.equal(x.relay.state.mirror.held.secret.reason,'sensitive_content');assert.equal(JSON.stringify(x.relay.state).includes('hunter2'),false)});
test('long text chunks without truncation and bounded oversize held',async()=>{const text='a'.repeat(7000);const parts=mirrorParts(msg('a','assistant',text));assert.equal(parts.length,3);assert.equal(parts.map(p=>p.slice(p.indexOf('\n')+1)).join(''),text);assert.throws(()=>mirrorParts(msg('x','user','a'.repeat(40000))));const x=fixture();await x.mirror.tick();x.rows([msg('baseline','user','old'),msg('large','assistant','a'.repeat(40000))]);await settle(x);assert.equal(x.sends.length,0);assert.equal(x.relay.state.mirror.held.large.reason,'oversize_requires_review')});
test('stable source edit updates same Telegram receipt without a new send',async()=>{const x=fixture();await x.mirror.tick();x.rows([msg('baseline','user','old'),msg('a','assistant','version1')]);await settle(x);x.rows([msg('baseline','user','old'),msg('a','assistant','version2')]);await settle(x);assert.equal(x.sends.length,1);assert.deepEqual(x.edits,[{owner:OWNER,receipt:'tg1',text:'dots:\nversion2'}])});
test('missing cursor never backfills newly visible older history',async()=>{const x=fixture();await x.mirror.tick();x.rows([msg('ancient','assistant','older message')]);assert.equal((await x.mirror.tick()).status,'cursor_not_visible');assert.equal(x.sends.length,0)});
test('unknown send remains blocked across restart, no retry',async()=>{const x=fixture({sendError:true});await x.mirror.tick();x.rows([msg('baseline','user','old'),msg('a','assistant','answer')]);assert.equal((await settle(x)).status,'delivery_unknown');x.restart();await x.mirror.tick();assert.equal(x.sends.length,1)});
test('uncertain prior operation blocks before next browser read',async()=>{const x=fixture();await x.mirror.tick();x.relay.state.mirror.published.a={status:'edit_uncertain',hash:'x',receipts:['tg1']};x.relay.save();x.restart();const count=x.reads();assert.equal((await x.mirror.tick()).status,'previous_operation_unknown');assert.equal(x.reads(),count)});
test('edit chunk-count changes require review, never truncate silently',async()=>{const x=fixture();await x.mirror.tick();x.rows([msg('baseline','user','old'),msg('a','assistant','short')]);await settle(x);x.rows([msg('baseline','user','old'),msg('a','assistant','a'.repeat(7000))]);await settle(x);assert.equal(x.sends.length,1);assert.equal(x.edits.length,0);assert.equal(x.relay.state.mirror.held.a.reason,'edit_part_count_changed')});
test('mirror waits for inbound echo before inspecting new rows',async()=>{const x=fixture();x.relay.busy=true;assert.equal((await x.mirror.tick()).status,'incoming_busy');assert.equal(x.reads(),0)});
test('content must stabilize, transient partial is not sent',async()=>{const x=fixture();await x.mirror.tick();x.rows([msg('baseline','user','old'),msg('a','assistant','partial')]);await x.mirror.tick();x.advance();x.rows([msg('baseline','user','old'),msg('a','assistant','complete answer')]);await x.mirror.tick();assert.equal(x.sends.length,0);x.advance();await x.mirror.tick();assert.deepEqual(x.sends.map(s=>s.text),['dots:\ncomplete answer'])});

test('mirror sends literal text with no Markdown parsing or link previews',()=>{assert.deepEqual(mirrorSendOptions('**literal** https://example.invalid'),{message:'**literal** https://example.invalid',formattingEntities:[],linkPreview:false})});
test('read errors persist sanitized status, back off and recover without cursor reset',async()=>{
 let time=1000,fail=true,calls=0,saves=0;
 const state={mirror:{baselineAt:1,cursor:'old',published:{},candidates:{},held:{}}};
 const relay={state,tabId:18,busy:false,save:()=>{saves++;},browserCall:async()=>{calls++;if(fail)throw Error('provider raw sensitive detail');return {ok:true,room:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',cursor:'old',messages:[{id:'old',role:'assistant',text:'previous'}]};}};
 const mirror=new DotMirror(relay,{now:()=>time});
 assert.equal((await mirror.tick()).status,'snapshot_read_failed');
 assert.equal(state.mirror.lastReadErrorAt,1000);assert.equal(state.mirror.cursor,'old');
 assert.equal(JSON.stringify(state).includes('sensitive detail'),false);
 assert.equal((await mirror.tick()).status,'read_backoff');assert.equal(calls,1);
 time=32000;fail=false;assert.equal((await mirror.tick()).status,'idle');
 assert.equal(state.mirror.baselineAt,1);assert.equal(state.mirror.readFailures,0);
 assert.equal(state.mirror.lastObservedAt,32000);assert.ok(saves>0);
});
test('reply quotes accept only exact owner-directed outgoing target and hide secrets',async()=>{
 const {safeReplyContext}=await import('./dot-mirror.mjs');
 const m={id:3,out:true,peerId:{userId:'111111111'},message:'Which option?'};
 assert.equal(safeReplyContext(m,'3'),'Which option?');
 assert.match(safeReplyContext({...m,out:false},'3'),/не подтверждён/);
 assert.match(safeReplyContext({...m,peerId:{userId:'other'}},'3'),/не подтверждён/);
 assert.match(safeReplyContext(m,'4'),/не подтверждён/);
 assert.match(safeReplyContext({...m,message:'password: secret-value'},'3'),/скрыт/);
 assert.match(safeReplyContext({...m,message:'Bearer abcdefghijklmnopqrstuv'},'3'),/скрыт/);
 assert.ok(safeReplyContext({...m,message:'x'.repeat(2000)},'3').length<1600);
});


test('dot reply uses original Telegram receipt for a previously mirrored message',async()=>{
 const x=fixture({numericReceipts:true});await x.mirror.tick();
 x.rows([msg('baseline','user','old'),msg('first','assistant','initial')]);
 await settle(x);
 x.rows([msg('baseline','user','old'),msg('first','assistant','initial'),{...msg('reply','user','followup'),reply_to:'first'}]);
 await settle(x);
 assert.deepEqual(x.sends,[{owner:OWNER,text:'dots:\ninitial'},{owner:OWNER,text:'Test owner:\nfollowup',replyTo:'1'}]);
 assert.equal(x.relay.state.mirror.published.reply.reply_receipt,'1');
 x.restart();await settle(x);assert.equal(x.sends.length,2);
});
test('dot reply to Telegram echo waits for original Messenger approval, never direct send',async()=>{
 const x=fixture({numericReceipts:true});await x.mirror.tick();
 x.relay.state.incoming['telegram:999999:'+OWNER+':42']={
  source:'telegram',status:'accepted',echo:'echo',source_message_ids:['42']};
 x.rows([msg('baseline','user','old'),msg('echo','user','tg inbound'),{...msg('assistant-reply','assistant','response'),reply_to:'echo'}]);
 assert.equal((await settle(x)).status,'question_original_approval_not_wired');
 assert.deepEqual(x.sends,[]);assert.deepEqual(x.mediaSends,[]);
 assert.equal(x.relay.state.mirror.cursor,'echo');
 assert.equal(x.relay.state.outgoing['mirror:assistant-reply:0'],undefined);
});
test('unverified dot reply target is held and cannot fall back to a silent plain message',async()=>{
 const x=fixture();await x.mirror.tick();
 x.rows([msg('baseline','user','old'),{...msg('orphan','assistant','private answer'),reply_to:'not_mirrored'}]);
 await settle(x);
 assert.equal(x.sends.length,0);
 assert.equal(x.relay.state.mirror.held.orphan.reason,'reply_target_unresolved');
 x.restart();await settle(x);assert.equal(x.sends.length,0);
});
test('pending or ambiguous incoming receipt never becomes a reply target',async()=>{
 const x=fixture({numericReceipts:true});await x.mirror.tick();
 x.relay.state.incoming['telegram:999999:'+OWNER+':42']={source:'telegram',status:'send_uncertain',echo:'echo',source_message_ids:['42']};
 x.rows([msg('baseline','user','old'),{...msg('response','assistant','response'),reply_to:'echo'}]);
 await settle(x);
 assert.equal(x.sends.length,0);
 assert.equal(x.relay.state.mirror.held.response.reason,'reply_target_unresolved');
});
test('edited dot reply target does not silently relink an already delivered message',async()=>{
 const x=fixture({numericReceipts:true});await x.mirror.tick();
 x.rows([msg('baseline','user','old'),msg('first','assistant','first'),msg('second','assistant','second')]);
 await settle(x);
 x.rows([msg('baseline','user','old'),msg('first','assistant','first'),msg('second','assistant','second'),{...msg('reply','assistant','response'),reply_to:'first'}]);
 await settle(x);
 assert.equal(x.sends.length,3);
 x.rows([msg('baseline','user','old'),msg('first','assistant','first'),msg('second','assistant','second'),{...msg('reply','assistant','response modified'),reply_to:'second'}]);
 await settle(x);
 assert.equal(x.sends.length,3);assert.equal(x.edits.length,0);
 assert.equal(x.relay.state.mirror.held.reply.reason,'reply_target_edited_requires_review');
});
test('native Telegram reply send options reject non-provider receipts and preserve literal formatting',()=>{
 assert.deepEqual(mirrorSendOptions('text','42'),{message:'text',formattingEntities:[],linkPreview:false,replyTo:42});
 for(const v of ['tg1','0','-1','123abc','9007199254740992'])assert.throws(()=>mirrorSendOptions('text',v),/invalid_mirror_reply_receipt/);
});


test('each long reply chunk retains the exact target, with no fallback to a plain send',async()=>{
 const x=fixture({numericReceipts:true});await x.mirror.tick();
 x.rows([msg('baseline','user','old'),msg('first','assistant','initial')]);
 await settle(x);
 x.rows([msg('baseline','user','old'),msg('first','assistant','initial'),{...msg('reply','assistant','z'.repeat(6100)),reply_to:'first'}]);
 await settle(x);
 assert.equal(x.sends.length,4);
 assert.deepEqual(x.sends.slice(1).map(m=>m.replyTo),['1','1','1']);
});
test('ambiguous native origin mapping is held rather than guessed',async()=>{
 const x=fixture({numericReceipts:true});await x.mirror.tick();
 x.relay.state.incoming['telegram:999999:'+OWNER+':42']={source:'telegram',status:'accepted',echo:'echo',source_message_ids:['42']};
 x.relay.state.incoming['telegram:999999:'+OWNER+':43']={source:'telegram',status:'accepted',echo:'echo',source_message_ids:['43']};
 x.rows([msg('baseline','user','old'),{...msg('reply','assistant','answer'),reply_to:'echo'}]);
 await settle(x);
 assert.equal(x.sends.length,0);
 assert.equal(x.relay.state.mirror.held.reply.reason,'reply_target_unresolved');
});

test('photo-only dot message reaches one synthetic Telegram media operation, no replay after restart',async()=>{
 const id=ROOM+'~'+ROOM+'~CalpicoMessage~photo1';
 const x=fixture({media:[imageEntry(1)]});
 await x.mirror.tick();
 x.rows([msg('baseline','user','old'),withImages(id,'assistant','')]);
 assert.equal((await settle(x)).status,'updated');
 assert.equal(x.sends.length,0);
 assert.equal(x.mediaSends.length,1);
 assert.equal(x.mediaSends[0].owner,OWNER);
 assert.equal(x.mediaSends[0].source.source_message_id,id);
 assert.equal(x.mediaSends[0].journalStatusAtCall,'send_uncertain');
 assert.equal(x.mediaSends[0].count,1);
 assert.match(x.mediaSends[0].caption,/изображение/);
 assert.equal(x.relay.state.mirror.published[id].media_count,1);
 assert.deepEqual(x.relay.state.mirror.published[id].media_sha256,[imageEntry(1).sha256]);
 assert.deepEqual(x.relay.state.mirror.published[id].media_receipts,['110']);
 assert.equal(x.relay.state.outgoing['mirror:'+id+':media:0'].status,'accepted');
 x.restart();await settle(x);assert.equal(x.mediaSends.length,1);
});
test('dot image album and caption preserve original Telegram reply correlation',async()=>{
 const id=ROOM+'~'+ROOM+'~CalpicoMessage~replyWithImages';
 const x=fixture({media:[imageEntry(1),imageEntry(2)],numericReceipts:true});
 await x.mirror.tick();
 x.rows([msg('baseline','user','old'),msg('first','assistant','initial')]);await settle(x);
 x.rows([msg('baseline','user','old'),msg('first','assistant','initial'),{...withImages(id,'assistant','two images',2),reply_to:'first'}]);
 await settle(x);
 assert.equal(x.sends.length,1);
 assert.equal(x.mediaSends.length,1);
 assert.equal(x.mediaSends[0].replyTo,'1');
 assert.equal(x.mediaSends[0].source.source_message_id,id);
 assert.equal(x.mediaSends[0].count,2);
 assert.equal(x.mediaSends[0].caption,'dots:\ntwo images');
 assert.deepEqual(x.relay.state.mirror.published[id].media_receipts,['110','111']);
 x.restart();await settle(x);assert.equal(x.mediaSends.length,1);
});
test('long caption is preserved in subsequent scoped text chunks, no silent truncation',async()=>{
 const id=ROOM+'~'+ROOM+'~CalpicoMessage~largeCaption';
 const txt='x'.repeat(5000);
 const x=fixture({media:[imageEntry(1)],numericReceipts:true});
 await x.mirror.tick();
 x.rows([msg('baseline','user','old'),withImages(id,'assistant',txt)]);
 await settle(x);
 assert.equal(x.mediaSends.length,1);
 assert.equal(x.sends.length,2);
 assert.match(x.mediaSends[0].caption,/изображение/);
 assert.equal(x.sends.map(x=>x.text.slice(x.text.indexOf('\n')+1)).join(''),txt);
 assert.equal(x.relay.state.mirror.published[id].receipts.length,3);
});
test('failed browser media read blocks BEFORE any durable send attempt, preserves cursor',async()=>{
 const id=ROOM+'~'+ROOM+'~CalpicoMessage~noBrowserData';
 const x=fixture({mediaReadError:true});
 await x.mirror.tick();
 x.rows([msg('baseline','user','old'),withImages(id,'assistant','caption')]);
 assert.equal((await settle(x)).status,'media_source_requires_review');
 assert.equal(x.mediaSends.length,0);
 assert.equal(x.sends.length,0);
 assert.equal(x.relay.state.mirror.cursor,'baseline');
 assert.equal(x.relay.state.outgoing['mirror:'+id+':media:0'],undefined);
});
test('unknown provider photo send is journaled before call and never replayed',async()=>{
 const id=ROOM+'~'+ROOM+'~CalpicoMessage~unknownPhoto';
 const x=fixture({media:[imageEntry(1)],mediaSendError:true});
 await x.mirror.tick();
 x.rows([msg('baseline','user','old'),withImages(id,'assistant','caption')]);
 assert.equal((await settle(x)).status,'delivery_unknown');
 assert.equal(x.mediaSends.length,1);
 assert.equal(x.relay.state.outgoing['mirror:'+id+':media:0'].status,'send_uncertain');
 x.restart();await x.mirror.tick();
 assert.equal(x.mediaSends.length,1);
});
test('image fingerprint changes require restabilization before any send',async()=>{
 const id=ROOM+'~'+ROOM+'~CalpicoMessage~changed';
 const x=fixture({media:[imageEntry(1)]});
 const first=withImages(id,'assistant','caption');
 await x.mirror.tick();
 x.rows([msg('baseline','user','old'),first]);
 await x.mirror.tick();
 x.advance();
 const changed={...first,attachment_fingerprints:['a'.repeat(64)]};
 x.rows([msg('baseline','user','old'),changed]);
 await x.mirror.tick();
 assert.equal(x.mediaSends.length,0);
 x.advance();await x.mirror.tick();
 assert.equal(x.mediaSends.length,1);
});
test('published photo edits require review, never edit an image or resend old data',async()=>{
 const id=ROOM+'~'+ROOM+'~CalpicoMessage~editedPhoto';
 const x=fixture({media:[imageEntry(1)]});
 await x.mirror.tick();
 x.rows([msg('baseline','user','old'),withImages(id,'assistant','original')]);await settle(x);
 x.rows([msg('baseline','user','old'),withImages(id,'assistant','edited')]);await settle(x);
 assert.equal(x.mediaSends.length,1);
 assert.equal(x.sends.length,0);
 assert.equal(x.edits.length,0);
 assert.equal(x.relay.state.mirror.held[id].reason,'media_edit_requires_review');
});
test('unverified media snapshot cannot pass as text-only output',async()=>{
 const id=ROOM+'~'+ROOM+'~CalpicoMessage~noFingerprint';
 const x=fixture({media:[imageEntry(1)]});
 await x.mirror.tick();
 x.rows([msg('baseline','user','old'),{...msg(id,'assistant','caption'),attachment_count:1}]);
 assert.equal((await x.mirror.tick()).status,'media_identity_requires_review');
 assert.equal(x.sends.length,0);assert.equal(x.mediaSends.length,0);
 assert.equal(x.relay.state.mirror.cursor,'baseline');
});
test('old text-only journals retain exact previous digest semantics',()=>{
 assert.equal(mirrorSourceHash(msg('a','assistant','hello')),textHash('hello'));
 assert.equal(mirrorSourceHash({...msg('a','assistant','hello'),attachment_count:0}),textHash('hello'));
 assert.equal(mirrorSourceHash({...msg('a','assistant','hello'),attachment_count:1}),null);
});

test('corrupted image hash cannot create an uncertain provider send record',async()=>{
 const id=ROOM+'~'+ROOM+'~CalpicoMessage~corrupt';
 const damaged={...imageEntry(1),sha256:'f'.repeat(64)};
 const x=fixture({media:[damaged]});
 await x.mirror.tick();
 x.rows([msg('baseline','user','old'),withImages(id,'assistant','photo')]);
 assert.equal((await settle(x)).status,'media_source_requires_review');
 assert.equal(x.relay.state.mirror.blocked,'media_source_requires_review');
 assert.equal(x.mediaSends.length,0);
 assert.equal(x.relay.state.outgoing['mirror:'+id+':media:0'],undefined);
 x.restart();assert.equal((await x.mirror.tick()).status,'media_source_requires_review');
 assert.equal(x.mediaSends.length,0);
});

test('approved question reply delegates to original Messenger action without any direct Telegram send',async()=>{
 const x=fixture({withQuestionApproval:true});await x.mirror.tick();
 x.relay.state.incoming['telegram:999999:'+OWNER+':42']={
  source:'telegram',status:'accepted',echo:'echo',source_message_ids:['42']};
 x.rows([msg('baseline','user','old'),msg('echo','user','tg question'),
  msg('answer','assistant','response')]);
 assert.equal((await settle(x)).status,'updated');
 assert.equal(x.questionApprovals.length,1);
 const call=x.questionApprovals[0];
 assert.equal(call.source.account_ref,'telegram:999999');
 assert.equal(call.source.peer_id,OWNER);
 assert.equal(call.source.source_provider_message_id,'42');
 assert.equal(Object.hasOwn(call.source,'source_message_id'),false);
 assert.equal(call.body,'dots:\nresponse');
 assert.equal(call.journalStatusAtCall,'send_uncertain');
 assert.equal(x.sends.length,0);
 assert.equal(x.mediaSends.length,0);
 assert.equal(x.edits.length,0);
 const accepted=x.relay.state.outgoing['mirror:answer:0'];
 assert.equal(accepted.kind,'original_question');
 assert.equal(accepted.status,'accepted');
 assert.equal(accepted.source_provider_message_id,'42');
 assert.equal(accepted.source_message_id,'telegram:999999|'+OWNER+':42');
 assert.equal(accepted.receipt_ref,'messenger:action_'+'a'.repeat(64));
 assert.equal(accepted.receipt,'201');
 assert.equal(x.relay.state.mirror.cursor,'answer');
 assert.equal(x.relay.state.mirror.published.answer.question_receipt_ref,accepted.receipt_ref);
 x.restart();await settle(x);
 assert.equal(x.questionApprovals.length,1);
});
test('backend original Messenger approval uncertainty remains blocked after restart',async()=>{
 const x=fixture({withQuestionApproval:true,questionApprovalError:true});await x.mirror.tick();
 x.relay.state.incoming['telegram:999999:'+OWNER+':42']={
  source:'telegram',status:'accepted',echo:'echo',source_message_ids:['42']};
 x.rows([msg('baseline','user','old'),msg('echo','user','tg question'),msg('answer','assistant','answer')]);
 assert.equal((await settle(x)).status,'question_original_delivery_unknown');
 assert.equal(x.relay.state.outgoing['mirror:answer:0'].status,'send_uncertain');
 assert.equal(x.questionApprovals.length,1);
 assert.equal(x.sends.length,0);
 x.restart();
 assert.equal((await x.mirror.tick()).status,'question_original_delivery_unknown');
 assert.equal(x.questionApprovals.length,1);
 assert.equal(x.relay.state.mirror.cursor,'echo');
});
test('forged provider receipt fails closed and cannot trigger fallback direct send',async()=>{
 const x=fixture({withQuestionApproval:true,questionResponse:{
  status:'accepted',question_delivery_state:'sent',source_message_id:'fake',provider_message_id:'201'
 }});
 await x.mirror.tick();
 x.relay.state.incoming['telegram:999999:'+OWNER+':42']={
  source:'telegram',status:'accepted',echo:'echo',source_message_ids:['42']};
 x.rows([msg('baseline','user','old'),msg('echo','user','tg question'),msg('answer','assistant','reply')]);
 assert.equal((await settle(x)).status,'question_original_delivery_unknown');
 assert.equal(x.sends.length,0);
 assert.equal(x.mediaSends.length,0);
 assert.equal(x.relay.state.outgoing['mirror:answer:0'].status,'send_uncertain');
});
test('question image never uses direct Telegram sendFile without original backend fence',async()=>{
 const x=fixture({withQuestionApproval:true,media:[imageEntry(1)]});await x.mirror.tick();
 x.relay.state.incoming['telegram:999999:'+OWNER+':42']={
  source:'telegram',status:'accepted',echo:'echo',source_message_ids:['42']};
 x.rows([msg('baseline','user','old'),msg('echo','user','tg question'),
  withImages('image-answer','assistant','picture')]);
 assert.equal((await settle(x)).status,'question_media_original_fence_missing');
 assert.equal(x.questionApprovals.length,0);
 assert.equal(x.sends.length,0);
 assert.equal(x.mediaSends.length,0);
 assert.equal(x.relay.state.outgoing['mirror:image-answer:media:0'],undefined);
 assert.equal(x.relay.state.mirror.cursor,'echo');
});
test('multipart question reply requires exactly one existing original action',async()=>{
 const x=fixture({withQuestionApproval:true});await x.mirror.tick();
 x.relay.state.incoming['telegram:999999:'+OWNER+':42']={
  source:'telegram',status:'accepted',echo:'echo',source_message_ids:['42']};
 x.rows([msg('baseline','user','old'),msg('echo','user','tg question'),
  msg('long-answer','assistant','a'.repeat(7100))]);
 assert.equal((await settle(x)).status,'question_multipart_original_fence_missing');
 assert.equal(x.questionApprovals.length,0);
 assert.equal(x.sends.length,0);
});
test('question source chain cannot cause a second approved send',async()=>{
 const x=fixture({withQuestionApproval:true});await x.mirror.tick();
 x.relay.state.incoming['telegram:999999:'+OWNER+':42']={
  source:'telegram',status:'accepted',echo:'echo',source_message_ids:['42']};
 x.rows([msg('baseline','user','old'),msg('echo','user','tg question'),
  msg('answer','assistant','first')]);
 await settle(x);
 x.rows([msg('baseline','user','old'),msg('echo','user','tg question'),
  msg('answer','assistant','first'),{...msg('followup','assistant','second'),reply_to:'answer'}]);
 assert.equal((await settle(x)).status,'question_origin_requires_review');
 assert.equal(x.questionApprovals.length,1);
 assert.equal(x.sends.length,0);
 assert.equal(x.relay.state.mirror.cursor,'answer');
});
test('edit of approved Messenger question never invokes direct Telegram edit',async()=>{
 const x=fixture({withQuestionApproval:true});await x.mirror.tick();
 x.relay.state.incoming['telegram:999999:'+OWNER+':42']={
  source:'telegram',status:'accepted',echo:'echo',source_message_ids:['42']};
 x.rows([msg('baseline','user','old'),msg('echo','user','tg question'),
  msg('answer','assistant','first')]);
 await settle(x);
 x.rows([msg('baseline','user','old'),msg('echo','user','tg question'),
  msg('answer','assistant','modified')]);
 await settle(x);
 assert.equal(x.questionApprovals.length,1);
 assert.equal(x.edits.length,0);
 assert.equal(x.relay.state.mirror.held.answer.reason,'question_edit_requires_original_approval');
});
