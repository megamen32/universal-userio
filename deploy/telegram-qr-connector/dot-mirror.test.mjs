import './dot-test-policy.mjs';
import test from 'node:test';import assert from 'node:assert/strict';import {mkdtempSync} from 'node:fs';import {tmpdir} from 'node:os';import {join} from 'node:path';import {DotRelay,ROOM,OWNER,textHash} from './dot-relay.mjs';
import {createHash} from 'node:crypto';
import {dotTelegramMediaParams,dotTelegramMediaReceipts} from './dot-egress.mjs';import {DotMirror,sensitiveText,mirrorParts,mirrorSendOptions,mirrorSourceHash} from './dot-mirror.mjs';
const msg=(id,role,text)=>({id,role,text});
function fixture(options={}){
 let rows=[msg('baseline','user','old history')],time=0,sends=[],edits=[],mediaSends=[],reads=0;
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
  send:async(owner,text,replyTo=null,entities=[])=>{
   sends.push({...replyTo==null?{owner,text}:{owner,text,replyTo},...(entities.length?{entities}:{})});
   if(options.sendError)throw Error('unknown');
   return options.numericReceipts?String((options.receiptBase||0)+sends.length):'tg'+sends.length;
  },
  sendMedia:async(owner,media,caption,replyTo,source,entities=[])=>{
   const optionsParams=dotTelegramMediaParams(media,caption,replyTo,FakeCustomFile);
   mediaSends.push({owner,caption,replyTo,source,count:media.length,files:optionsParams.file,journalStatusAtCall:relay.state.outgoing['mirror:'+source.source_message_id+':media:0']?.status,...(entities.length?{entities}:{})});
   if(options.mediaSendError)throw Error('provider_unknown');
   return dotTelegramMediaReceipts(media.map((_,i)=>({id:100+mediaSends.length*10+i})),media.length);
  },
  edit:async(owner,receipt,text,expectedHash,entities,previousEntities)=>{
   edits.push({owner,receipt,text,...(entities?.length?{entities,previousEntities}:{})});if(options.editError)throw Error('unknown');
  },wait:async()=>{}
 };
 let relay=new DotRelay(config),mirror=new DotMirror(relay,{now:()=>time,stableMs:10});
 return {get relay(){return relay},get mirror(){return mirror},rows:v=>{rows=v},
  advance:()=>{time+=11},sends,mediaSends,edits,reads:()=>reads,
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

test('media caption carries the original link with the speaker offset',async()=>{
 const x=fixture({media:[imageEntry(1)]});await x.mirror.tick();
 x.rows([msg('baseline','user','old'),{...withImages('photo','assistant','сайт'),entities:[{type:'text_url',offset:0,length:4,url:'https://example.org/'}]}]);
 await settle(x);
 assert.deepEqual(x.mediaSends[0].entities,[{type:'text_url',offset:6,length:4,url:'https://example.org/'}]);
});

async function settle(x){await x.mirror.tick();x.advance();return x.mirror.tick();}
test('activation baseline skips all history, repeat does not send',async()=>{const x=fixture();assert.equal((await x.mirror.tick()).status,'baseline_initialized');await settle(x);assert.equal(x.sends.length,0)});
test('both new authors labelled, restart and stable IDs prevent duplicates',async()=>{const x=fixture();await x.mirror.tick();x.rows([msg('baseline','user','old'),msg('u','user','hello'),msg('a','assistant','background result')]);await settle(x);assert.deepEqual(x.sends,[{owner:OWNER,text:'Test owner:\nhello'},{owner:OWNER,text:'dots:\nbackground result'}]);x.restart();await settle(x);assert.equal(x.sends.length,2)});
test('optional automatic group reply uses the last message in the current topic',async()=>{
 const x=fixture({numericReceipts:true});x.relay.group=true;x.relay.groupOptions=()=>({chainPrevious:true});
 x.relay.state.groupContext={currentTopic:'4',currentMessageId:'20',lastByTopic:{'4':'20','5':'19'}};
 await x.mirror.tick();x.rows([msg('baseline','user','old'),msg('a','assistant','answer')]);await settle(x);
 assert.equal(x.sends[0].replyTo,'20');
});
test('split group messages form a chain without overriding an explicit reply',async()=>{
 const x=fixture({numericReceipts:true});x.relay.group=true;x.relay.groupOptions=()=>({chainPrevious:true});
 x.relay.state.groupContext={currentTopic:'4',currentMessageId:'20',lastByTopic:{'4':'20'}};
 await x.mirror.tick();x.rows([msg('baseline','user','old'),msg('long','assistant','a'.repeat(7000))]);await settle(x);
 assert.deepEqual(x.sends.map(s=>s.replyTo),['20','1','2']);
});
test('an explicit Dot reply overrides the automatic previous message',async()=>{
 const x=fixture({numericReceipts:true});x.relay.group=true;x.relay.groupOptions=()=>({chainPrevious:true});
 x.relay.state.groupContext={currentTopic:'4',currentMessageId:'20',lastByTopic:{'4':'20'}};
 await x.mirror.tick();x.relay.state.mirror.published.parent={status:'accepted',receipts:['15'],hash:textHash('old')};
 x.rows([msg('baseline','user','old'),{...msg('answer','assistant','ответ'),reply_to:'parent'}]);await settle(x);
 assert.equal(x.sends[0].replyTo,'15');
});
test('automatic chain advances from confirmed bot receipts even without a provider update',async()=>{
 const x=fixture({numericReceipts:true,receiptBase:20});x.relay.group=true;x.relay.groupOptions=()=>({chainPrevious:true});
 x.relay.state.groupContext={currentTopic:'4',currentMessageId:'20',lastByTopic:{'4':'20'}};
 await x.mirror.tick();x.rows([msg('baseline','user','old'),msg('one','assistant','первое'),msg('two','assistant','второе')]);await settle(x);
 assert.deepEqual(x.sends.map(s=>s.replyTo),['20','21']);assert.equal(x.relay.state.groupContext.lastByTopic['4'],'22');
});
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
test('private mirror encodes a text link for the existing Telegram client',()=>{
 class Link{constructor(v){Object.assign(this,v);this.className='MessageEntityTextUrl'}}
 const result=mirrorSendOptions('сайт',null,[{type:'text_url',offset:0,length:4,url:'https://example.org/'}],{MessageEntityTextUrl:Link});
 assert.equal(result.formattingEntities[0].className,'MessageEntityTextUrl');assert.equal(result.formattingEntities[0].url,'https://example.org/');
});
test('private image caption carries a Telegram link entity',()=>{
 class Link{constructor(v){Object.assign(this,v);this.className='MessageEntityTextUrl'}}
 class File{constructor(name,size,path,buffer){Object.assign(this,{name,size,path,buffer})}}
 const params=dotTelegramMediaParams([imageEntry(1)],'сайт',null,File,[{type:'text_url',offset:0,length:4,url:'https://example.org/'}],{MessageEntityTextUrl:Link});
 assert.equal(params.formattingEntities[0].url,'https://example.org/');
});
test('mirror sends labelled links as entities offset after the speaker label',async()=>{
 const x=fixture();await x.mirror.tick();
 x.rows([msg('baseline','user','old'),{...msg('linked','assistant','Откройте сайт'),entities:[{type:'text_url',offset:9,length:4,url:'https://example.org/path'}]}]);
 await settle(x);
 assert.deepEqual(x.sends[0].entities,[{type:'text_url',offset:15,length:4,url:'https://example.org/path'}]);
});
test('mirror holds a message when its displayed formatting cannot be mapped',async()=>{
 const x=fixture();await x.mirror.tick();
 x.rows([msg('baseline','user','old'),{...msg('bad','assistant','Откройте сайт'),format_incomplete:true}]);
 await settle(x);
 assert.equal(x.sends.length,0);
 assert.equal(x.relay.state.mirror.held.bad.reason,'format_requires_review');
});
test('format-only changes update the original message without replaying it',async()=>{
 const x=fixture();await x.mirror.tick();
 x.rows([msg('baseline','user','old'),msg('linked','assistant','сайт')]);await settle(x);
 x.rows([msg('baseline','user','old'),{...msg('linked','assistant','сайт'),entities:[{type:'text_url',offset:0,length:4,url:'https://example.org/'}]}]);await settle(x);
 assert.equal(x.sends.length,1);
 assert.equal(x.edits.length,1);assert.equal(x.edits[0].receipt,'tg1');
 assert.deepEqual(x.edits[0].previousEntities,[]);
 assert.deepEqual(x.edits[0].entities,[{type:'text_url',offset:6,length:4,url:'https://example.org/'}]);
 assert.equal(x.relay.state.mirror.published.linked.status,'accepted');
 await settle(x);assert.equal(x.edits.length,1);
});
test('an older receipt without saved formatting is held before editing',async()=>{
 const x=fixture();await x.mirror.tick();x.rows([msg('baseline','user','old'),msg('legacy','assistant','сайт')]);await settle(x);
 delete x.relay.state.mirror.published.legacy.partFormats;
 x.rows([msg('baseline','user','old'),{...msg('legacy','assistant','сайт'),entities:[{type:'text_url',offset:0,length:4,url:'https://example.org/'}]}]);await settle(x);
 assert.equal(x.edits.length,0);assert.equal(x.relay.state.mirror.held.legacy.reason,'edit_original_format_unknown');
});
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

test('resync requires exact cursor and current room head; preserves journal and backup',async()=>{
 const x=fixture();await x.mirror.tick();x.relay.state.incoming.unknown={status:'echo_timeout_requires_review',text_hash:'keep'};x.relay.state.outgoing.old={status:'accepted'};x.relay.save();
 x.rows([msg('new-head','assistant','current')]);await x.mirror.tick();
 assert.equal((await x.mirror.resync({requestId:'0123456789abcdef0123456789abcdef',expectedCursor:'wrong',targetCursor:'new-head'})).status,'resync_refused');
 const r=await x.mirror.resync({requestId:'0123456789abcdef0123456789abcdef',expectedCursor:'baseline',targetCursor:'new-head'});
 assert.equal(r.status,'resynced');assert.equal(x.relay.state.mirror.cursor,'new-head');assert.equal(x.relay.state.incoming.unknown.status,'echo_timeout_requires_review');assert.equal(x.relay.state.outgoing.old.status,'accepted');
 const {readFileSync}=await import('node:fs');const backup=JSON.parse(readFileSync(r.backupPath,'utf8'));assert.equal(backup.state.mirror.cursor,'baseline');assert.equal(backup.snapshot.messages[0].id,'new-head');
 await settle(x);assert.equal(x.sends.length,0);x.restart();assert.equal(x.relay.state.mirror.cursor,'new-head');
});
test('resync fails closed on unknown sends and changed head',async()=>{
 const x=fixture();await x.mirror.tick();x.rows([msg('head','assistant','new')]);await x.mirror.tick();x.relay.state.outgoing.unknown={status:'send_uncertain'};
 assert.equal((await x.mirror.resync({requestId:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',expectedCursor:'baseline',targetCursor:'head'})).status,'resync_refused');
 delete x.relay.state.outgoing.unknown;
 assert.equal((await x.mirror.resync({requestId:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',expectedCursor:'baseline',targetCursor:'stale'})).status,'resync_refused');
 assert.equal(x.relay.state.mirror.cursor,'baseline');
});
test('resync rejects concurrent admission while reading the room',async()=>{
 const x=fixture();await x.mirror.tick();x.rows([msg('head','assistant','new')]);await x.mirror.tick();
 const original=x.relay.browserCall;x.relay.browserCall=async(...args)=>{const snap=await original(...args);x.relay.state.incoming.concurrent={status:'queued'};return snap;};
 const r=await x.mirror.resync({requestId:'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',expectedCursor:'baseline',targetCursor:'head'});
 assert.equal(r.status,'resync_refused');assert.equal(x.relay.state.mirror.cursor,'baseline');assert.equal(x.relay.state.incoming.concurrent.status,'queued');
});
