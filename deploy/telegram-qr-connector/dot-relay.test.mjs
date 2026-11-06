import './dot-test-policy.mjs';
import test from 'node:test';import assert from 'node:assert/strict';import {mkdtempSync,readFileSync} from 'node:fs';import {tmpdir} from 'node:os';import {join} from 'node:path';import {DotRelay,ROOM,OWNER,canonicalPrivateSender} from './dot-relay.mjs';
const event={accountId:'telegram:999999',peerId:OWNER,senderId:OWNER,private:true,out:false,isEdit:false,messageId:'12',text:'hello'};
function setup(options={}){let calls=0,submitted='',writes=0;const path=join(mkdtempSync(join(tmpdir(),'dot-relay-')),'state.json');const config={statePath:path,botAccountId:event.accountId,agentId:'browser',tabId:7,wait:async()=>{},send:async()=>{throw Error('inbound must not send Telegram')},browserCall:async(action,args)=>{calls++;if(action==='dot_send'){writes++;submitted=args.text;const s=JSON.parse(readFileSync(path));assert.ok(Object.values(s.incoming).some(r=>r.status==='send_uncertain'));if(options.unknown)throw Error('timeout');return {ok:true};}if(!args.after)return {ok:true,room:options.wrongRoom?'other':ROOM,cursor:'old'};return {ok:true,room:ROOM,messages:[{id:'echo',role:'user',text:submitted},...(options.ambiguous?[{id:'echo2',role:'user',text:submitted}]:[])]};}};return {relay:new DotRelay(config),config,path,counts:()=>({calls,submitted,writes})};}
test('exact owner source only',async()=>{const x=setup();for(const change of [{senderId:'2'},{peerId:'-1'},{accountId:'telegram:222222222'},{private:false},{out:true},{isEdit:true},{reconciliation:true}])assert.equal((await x.relay.receive({...event,...change})).status,'ignored');assert.equal(x.counts().calls,0)});
test('raw original text sends once, echo accepted and restart deduped',async()=>{const x=setup();assert.equal((await x.relay.receive(event)).status,'accepted');assert.equal(x.counts().submitted,'hello');assert.equal((await new DotRelay(x.config).receive(event)).duplicate,true);assert.equal(x.counts().writes,1);assert.equal(x.relay.mirrorOrigin({id:'echo',role:'user',text:'hello'}),'telegram')});
test('uncertain sends not retried and matching echo held',async()=>{const x=setup({unknown:true});assert.equal((await x.relay.receive(event)).status,'send_uncertain');assert.equal((await new DotRelay(x.config).receive(event)).duplicate,true);assert.equal(x.counts().writes,1);assert.equal(x.relay.mirrorOrigin({id:'candidate',role:'user',text:'hello'}),'uncertain')});
test('wrong room never writes',async()=>{const x=setup({wrongRoom:true});assert.equal((await x.relay.receive(event)).status,'not_accepted');assert.equal(x.counts().writes,0)});
test('ambiguous identical echo requires review',async()=>{const x=setup({ambiguous:true});assert.equal((await x.relay.receive(event)).status,'ambiguous_echo_requires_review');assert.equal(x.relay.mirrorOrigin({id:'echo',role:'user',text:'hello'}),'uncertain')});
test('binding change refused',()=>{const x=setup();x.relay.save();assert.throws(()=>new DotRelay({...x.config,tabId:8}),/binding_changed/)});
test('canonical missing sender only private incoming exactowner',()=>{const m={peerId:{userId:BigInt(OWNER)},out:false};assert.equal(canonicalPrivateSender(m,OWNER),OWNER);assert.equal(canonicalPrivateSender({...m,out:true},OWNER),'');assert.equal(canonicalPrivateSender({peerId:{chatId:1}},OWNER),'');assert.equal(canonicalPrivateSender({...m,senderId:42},OWNER),'42');assert.equal(canonicalPrivateSender({...m,fromId:{userId:42}},OWNER),'42');assert.equal(canonicalPrivateSender({peerId:{userId:42}},'42'),'')});
test('internal board namespace shares queue without pretending Telegram',async()=>{const x=setup();assert.equal((await x.relay.enqueueTrusted({source:'board',requestId:'board:execution1',text:'task'})).status,'accepted');assert.equal(x.relay.mirrorOrigin({id:'echo',role:'user',text:'task'}),null);await assert.rejects(()=>x.relay.enqueueTrusted({source:'other',requestId:'board:1',text:'bad'}));assert.equal((await x.relay.enqueueTrusted({source:'board',requestId:'board:execution1',text:'task'})).duplicate,true)});
test('old completed receipt remains duplicate',async()=>{const x=setup();x.relay.state.incoming[event.accountId+':'+OWNER+':12']={status:'replied',expectedReply:'DOT_OK_20261101_0128'};x.relay.save();assert.equal((await new DotRelay(x.config).receive(event)).duplicate,true);assert.equal(x.counts().writes,0)});
test('Telegram reply maps accepted receipts and incoming echoes only, without replay',async()=>{
 const relay=Object.create(DotRelay.prototype);relay.botAccountId='telegram:333333333';
 const id=ROOM+'~'+ROOM+'~CalpicoMessage~Sentinel_target';
 relay.state={incoming:{},mirror:{published:{[id]:{status:'accepted',receipts:['15']}}}};
 let admitted;relay.admit=async(...args)=>(admitted=args,{status:'captured'});
 const e={accountId:relay.botAccountId,peerId:OWNER,senderId:OWNER,private:true,out:false,isEdit:false,messageId:'99',text:'yes',replyTo:'15'};
 await relay.receive(e);assert.equal(admitted[1],'yes');assert.equal(admitted[3].reply_to_main,id);
 relay.state.incoming[relay.botAccountId+':'+OWNER+':12']={status:'accepted',echo:id};
 assert.equal(relay.replyTarget('12'),id);
 relay.state.mirror.published[id].status='sending';assert.equal(relay.replyTarget('15'),null);
 relay.state.mirror.published.other={status:'accepted',receipts:['15']};assert.equal(relay.replyTarget('15'),null);
 await relay.receive({...e,replyTo:'3',replyContext:'Bot-only question'});
 assert.match(admitted[1],/цитата контекста/);assert.match(admitted[1],/> Bot-only question/);
 assert.equal(admitted[3].reply_to_main,null);assert.equal(admitted[3].provider_reply_to,'3');
 assert.equal(admitted[3].reply_mode,'quoted_context');
});
test('reply context preparation cannot reorder a later plain incoming message',async()=>{
 const relay=Object.create(DotRelay.prototype);relay.allowed=()=>true;relay.replyTarget=()=>null;
 let release;const pending=new Promise(r=>{release=r;});const order=[];
 relay.receive=async e=>{order.push(e.messageId);return {status:'captured'};};
 const first=relay.prepareReceive({messageId:'1',replyTo:'3'},async()=>{await pending;return 'context';});
 const second=relay.prepareReceive({messageId:'2'},async()=>{throw Error('unexpected lookup');});
 await new Promise(r=>setTimeout(r,0));assert.deepEqual(order,[]);
 release();await Promise.all([first,second]);assert.deepEqual(order,['1','2']);
});
test('image-only owner input keeps manifest and requires image filenames in echo',async()=>{
 const relay=Object.create(DotRelay.prototype);relay.botAccountId='telegram:333333333';relay.state={incoming:{}};let admitted;
 relay.admit=async(...args)=>(admitted=args,{status:'captured'});
 const e={accountId:relay.botAccountId,peerId:OWNER,senderId:OWNER,private:true,messageId:'80',text:'',media:[{filename:'tg-80-aaaaaaaaaaaaaaaa.jpg',sha256:'a'.repeat(64)}],source_message_ids:['80','81'],grouped_id:'7'};
 assert.equal(relay.allowed(e),true);await relay.receive(e);assert.equal(admitted[1],'');assert.equal(admitted[3].media.length,1);
 assert.deepEqual(admitted[3].source_message_ids,['80','81']);
 assert.equal(relay.allowed({...e,senderId:'other'}),false);assert.equal(relay.allowed({...e,media:[]}),false);
});
test('group binding admits only verified human messages in the exact group',async()=>{
 const x=setup();const relay=new DotRelay({...x.config,statePath:x.path+'-group',room:'group-room',peerId:'-1004352022764',group:true});
 const e={...event,peerId:'-1004352022764',senderId:'123',private:false,senderIsBot:false};
 assert.equal(relay.allowed(e),true);
 for(const change of [{peerId:'-1001'},{private:true},{senderIsBot:true},{senderIsBot:undefined},{senderId:''},{out:true}]) assert.equal(relay.allowed({...e,...change}),false);
});
test('group delivery retains formatting without persisting link targets in the journal',async()=>{
 const x=setup();let observed;
 const relay=new DotRelay({...x.config,statePath:x.path+'-group-format',room:ROOM,peerId:'-1004352022764',group:true,browserCall:async(action,args)=>{
  if(action==='dot_send'){observed=args;return {ok:true}};
  return args.after?{ok:true,room:ROOM,cursor:'new',messages:[{id:'echo',role:'user',text:'Оля:\nсайт',entities:[{type:'text_url',offset:5,length:4,url:'https://example.org/private'}]}]}:{ok:true,room:ROOM,cursor:'old'};
 }});
 const e={accountId:event.accountId,peerId:'-1004352022764',senderId:'43',senderIsBot:false,private:false,messageId:'55',text:'Оля:\nсайт',entities:[{type:'text_url',offset:5,length:4,url:'https://example.org/private'}]};
 assert.equal((await relay.receive(e)).status,'accepted');assert.deepEqual(observed.entities,e.entities);
 assert.equal(readFileSync(x.path+'-group-format','utf8').includes('https://example.org/private'),false);
});
test('a plain echo cannot confirm an originally formatted delivery',async()=>{
 const x=setup();const relay=new DotRelay({...x.config,statePath:x.path+'-format-loss',room:ROOM,wait:async()=>{},browserCall:async(action,args)=>action==='dot_send'?{ok:true}:args.after?{ok:true,room:ROOM,cursor:'new',messages:[{id:'echo',role:'user',text:'сайт',entities:[]}]}:{ok:true,room:ROOM,cursor:'old'}});
 const result=await relay.receive({...event,text:'сайт',entities:[{type:'text_url',offset:0,length:4,url:'https://example.org/'}]});
 assert.equal(result.status,'echo_format_requires_review');
});
test('unsupported formatting is journalled once before any browser action',async()=>{
 const x=setup();const e={...event,entities:[{type:'unsupported',provider_type:'MessageEntitySpoiler',offset:0,length:5}]};
 const r=await x.relay.receive(e);assert.equal(r.status,'format_requires_review');assert.equal(x.counts().calls,0);
 assert.equal((await new DotRelay(x.config).receive(e)).duplicate,true);assert.equal(x.counts().calls,0);
});
test('unsupported media is recorded against the original message without a send',async()=>{
 const x=setup();const e={...event,text:'',media:[{kind:'document'}]};
 const r=x.relay.holdIncoming(e,'media_requires_review');assert.equal(r.status,'media_requires_review');assert.equal(x.counts().calls,0);
 assert.equal(new DotRelay(x.config).holdIncoming(e,'media_requires_review').duplicate,true);
});
test('verified native reply target permits a unique echo when the main list omits reply metadata',async()=>{
 let sent=0;const room=ROOM,target=room+'~'+room+'~CalpicoMessage~target';
 const path=join(mkdtempSync(join(tmpdir(),'dot-reply-')),'state.json');
 const r=new DotRelay({statePath:path,botAccountId:event.accountId,agentId:'browser',tabId:7,wait:async()=>{},send:async()=>{},browserCall:async(action,args)=>{if(action==='dot_send'){sent++;return {ok:true,verified_reply_to:target}};return args.after?{ok:true,room,cursor:'new',messages:[{id:'echo',role:'user',text:'hello',reply_to:null}]}:{ok:true,room,cursor:'old'}}});
 r.state.mirror={published:{[target]:{status:'accepted',receipts:['55']}}};const result=await r.receive({...event,replyTo:'55'});assert.equal(result.status,'accepted');assert.equal(sent,1);
});
