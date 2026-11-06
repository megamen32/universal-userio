import './dot-test-policy.mjs';
import test from 'node:test';import assert from 'node:assert/strict';import {mkdtempSync} from 'node:fs';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {DotRelay} from './dot-relay.mjs';import {DotMirror} from './dot-mirror.mjs';
import {groupDotText} from './dot-group-message.mjs';
test('group and room are isolated while delivery and echo survive restart',async()=>{
 const room='room2',peer='-1004352022764',path=join(mkdtempSync(join(tmpdir(),'dot-group-')),'state');let rows=[{id:'base',role:'assistant',text:'old'}],sent=[],now=0,delivered=0;
 const config={statePath:path,botAccountId:'telegram:777777',agentId:'airlock',tabId:2,room,peerId:peer,group:true,wait:async()=>{},browserCall:async(action,args)=>{if(action==='dot_send'){delivered++;rows.push({id:'echo',role:'user',text:args.text});return {ok:true}};const idx=args.after?rows.findIndex(x=>x.id===args.after):-1;return {ok:true,room,cursor:rows.at(-1).id,messages:rows.slice(idx+1)}},send:async(target,text)=>{sent.push({target,text});return 40}};
 let relay=new DotRelay(config),mirror=new DotMirror(relay,{now:()=>now,stableMs:1});assert.equal((await mirror.tick()).status,'baseline_initialized');
 const e={accountId:config.botAccountId,peerId:peer,senderId:'42',senderIsBot:false,private:false,messageId:'1',text:'hello'};
 assert.equal((await relay.receive(e)).status,'accepted');relay=new DotRelay(config);assert.equal((await relay.receive(e)).duplicate,true);assert.equal(delivered,1);
 mirror=new DotMirror(relay,{now:()=>now,stableMs:1});await mirror.tick();rows.push({id:'answer',role:'assistant',text:'world'});await mirror.tick();now=2;await mirror.tick();assert.equal(sent.length,1);assert.equal(sent[0].target,peer);assert.match(sent[0].text,/world/);
});
test('two group participants retain their distinct names through the existing relay and echo',async()=>{
 const room='room2',peer='-1004352022764',path=join(mkdtempSync(join(tmpdir(),'dot-group-names-')),'state');
 let rows=[{id:'base',role:'assistant',text:'old'}],delivered=[];
 const config={statePath:path,botAccountId:'telegram:777777',agentId:'airlock',tabId:2,room,peerId:peer,group:true,wait:async()=>{},send:async()=>40,
  browserCall:async(action,args)=>{if(action==='dot_send'){delivered.push(args.text);rows.push({id:'echo'+delivered.length,role:'user',text:args.text});return {ok:true}}const idx=args.after?rows.findIndex(x=>x.id===args.after):-1;return {ok:true,room,cursor:rows.at(-1).id,messages:rows.slice(idx+1)}}};
 const relay=new DotRelay(config);
 const participants=[{id:'42',firstName:'Никита',lastName:'Разанов'},{id:'43',firstName:'Оля',lastName:'Карпова'}];
 for(const sender of participants){const message={message:'Привет'};const e={accountId:config.botAccountId,peerId:peer,senderId:sender.id,senderIsBot:false,private:false,messageId:sender.id,text:groupDotText(sender,message)};assert.equal((await relay.receive(e)).status,'accepted');assert.equal((await relay.receive(e)).duplicate,true);}
 assert.deepEqual(delivered,['Никита Разанов:\nПривет','Оля Карпова:\nПривет']);
});
