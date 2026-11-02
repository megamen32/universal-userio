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

test('owned board admission binds credential in same durable source before send',async()=>{
 const x=setup();const input={source:'board',requestId:'board:'+'a'.repeat(64),text:'task'};
 assert.equal((await x.relay.enqueueOwned('board-service',input)).status,'accepted');
 const stored=JSON.parse(readFileSync(x.path));assert.equal(stored.incoming[input.requestId].credential_id,'board-service');
 assert.deepEqual(new DotRelay(x.config).readOwnedReceipt('board-service',input.requestId),{status:'accepted'});
 assert.equal(x.relay.readOwnedReceipt('other-service',input.requestId),null);
 assert.equal((await new DotRelay(x.config).enqueueOwned('board-service',input)).duplicate,true);
 assert.equal(x.counts().writes,1);
});
test('changed credential or payload cannot reuse owned source',async()=>{
 const x=setup();const input={source:'board',requestId:'board:'+'a'.repeat(64),text:'task'};
 await x.relay.enqueueOwned('board-service',input);
 await assert.rejects(()=>x.relay.enqueueOwned('other-service',input),/source_conflict/);
 await assert.rejects(()=>x.relay.enqueueOwned('board-service',{...input,text:'different'}),/source_conflict/);
 assert.equal(x.counts().writes,1);
});
test('owned unknown outcome survives restart without another write',async()=>{
 const x=setup({unknown:true});const input={source:'board',requestId:'board:'+'a'.repeat(64),text:'task'};
 assert.equal((await x.relay.enqueueOwned('board-service',input)).status,'send_uncertain');
 assert.equal((await new DotRelay(x.config).enqueueOwned('board-service',input)).duplicate,true);
 assert.equal(x.counts().writes,1);
});
