import './dot-test-policy.mjs';
import test from 'node:test';import assert from 'node:assert/strict';import {mkdtempSync} from 'node:fs';import {tmpdir} from 'node:os';import {join} from 'node:path';import {DotRelay,ROOM,OWNER,textHash} from './dot-relay.mjs';import {DotMirror,sensitiveText,mirrorParts,mirrorSendOptions} from './dot-mirror.mjs';
const msg=(id,role,text)=>({id,role,text});
function fixture(options={}){let rows=[msg('baseline','user','old history')],time=0,sends=[],edits=[],reads=0;const config={statePath:join(mkdtempSync(join(tmpdir(),'dot-mirror-')),'state.json'),botAccountId:'telegram:999999',agentId:'browser',tabId:18,browserCall:async()=>{reads++;return {ok:true,room:ROOM,cursor:rows.at(-1)?.id,messages:rows}},send:async(owner,text,replyTo=null)=>{sends.push(replyTo==null?{owner,text}:{owner,text,replyTo});if(options.sendError)throw Error('unknown');return options.numericReceipts?String(sends.length):'tg'+sends.length},edit:async(owner,receipt,text)=>{edits.push({owner,receipt,text});if(options.editError)throw Error('unknown')},wait:async()=>{}};let relay=new DotRelay(config),mirror=new DotMirror(relay,{now:()=>time,stableMs:10});return {get relay(){return relay},get mirror(){return mirror},rows:v=>{rows=v},advance:()=>{time+=11},sends,edits,reads:()=>reads,restart:()=>{relay=new DotRelay(config);mirror=new DotMirror(relay,{now:()=>time,stableMs:10})}};}
async function settle(x){await x.mirror.tick();x.advance();return x.mirror.tick();}
test('activation baseline skips all history, repeat does not send',async()=>{const x=fixture();assert.equal((await x.mirror.tick()).status,'baseline_initialized');await settle(x);assert.equal(x.sends.length,0)});
test('both new authors labelled, restart and stable IDs prevent duplicates',async()=>{const x=fixture();await x.mirror.tick();x.rows([msg('baseline','user','old'),msg('u','user','hello'),msg('a','assistant','background result')]);await settle(x);assert.deepEqual(x.sends,[{owner:OWNER,text:'Test owner:\nhello'},{owner:OWNER,text:'dots:\nbackground result'}]);x.restart();await settle(x);assert.equal(x.sends.length,2)});
test('Telegram-origin echo suppressed but assistant still mirrored',async()=>{const x=fixture();await x.mirror.tick();x.relay.state.incoming['telegram:999999:111111111:1']={source:'telegram',status:'accepted',echo:'echo'};x.rows([msg('baseline','user','old'),msg('echo','user','from TG'),msg('a','assistant','answer')]);await settle(x);assert.deepEqual(x.sends.map(s=>s.text),['dots:\nanswer'])});
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
test('dot assistant replying to an accepted Telegram echo uses original owner message ID',async()=>{
 const x=fixture({numericReceipts:true});await x.mirror.tick();
 x.relay.state.incoming['telegram:999999:'+OWNER+':42']={
  source:'telegram',status:'accepted',echo:'echo',source_message_ids:['42']};
 x.rows([msg('baseline','user','old'),msg('echo','user','tg inbound'),{...msg('assistant-reply','assistant','response'),reply_to:'echo'}]);
 await settle(x);
 assert.deepEqual(x.sends,[{owner:OWNER,text:'dots:\nresponse',replyTo:'42'}]);
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
