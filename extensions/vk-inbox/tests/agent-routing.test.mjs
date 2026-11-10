import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { createHash, webcrypto } from 'node:crypto';

function fixture() {
  const data={}, calls=[], executed=[];
  const chrome={storage:{local:{async get(k){return {[k]:data[k]}},async set(p){Object.assign(data,p)}}}};
  const self={UserIOConnections:{subscriptionsForScope:async (_provider,accountScope)=>accountScope==='scope-a'?[{id:'work'}]:[{id:'personal'}]},UserIO:{call:async (...args)=>{calls.push(args);return {accepted:true}}},UserIOScopedChatGPT:{read:async args=>{executed.push(args);return {ok:true}},firstSend:async args=>{executed.push(args);return {ok:true,admission:'confirmed'}}}};
  vm.runInNewContext(fs.readFileSync(new URL('../lib/agent.js',import.meta.url),'utf8'),{self,chrome,URL,setTimeout,console});
  return {self,data,calls,executed};
}
test('real agent code blocks wrong account and arbitrary eval, and fences replay across restart',async()=>{
  const f=fixture(),ctx={connectionId:'work',principalId:'alice',agentInstanceId:'instance'};
  const cmd={id:'1',agent_id:'agent',action:'gpt_read',args:{expected_account_scope:'scope-b'}};
  assert.equal((await f.self.Agent.processCommand(cmd,ctx)).error,'account_not_authorized'); assert.equal(f.executed.length,0);
  assert.equal((await f.self.Agent.processCommand({id:'2',action:'eval',args:{}},ctx)).error,'account_not_authorized');
  const allowed={id:'3',action:'gpt_read',args:{expected_account_scope:'scope-a'}};
  assert.equal((await f.self.Agent.processCommand(allowed,ctx)).ok,true); assert.equal(f.executed.length,1);
  vm.runInNewContext(fs.readFileSync(new URL('../lib/agent.js',import.meta.url),'utf8'),{self:f.self,chrome:{storage:{local:{async get(k){return {[k]:f.data[k]}},async set(p){Object.assign(f.data,p)}}}},URL,setTimeout,console});
  assert.equal((await f.self.Agent.processCommand(allowed,ctx)).error,'command_already_seen'); assert.equal(f.executed.length,1);
  assert.ok(f.calls.every(c=>c[3]==='work'));
});
test('existing chat UI send returns the new native user receipt only',async()=>{
  const ref='11111111-1111-4111-8111-111111111111',id='22222222-2222-4222-8222-222222222222';
  const scope=createHash('sha256').update(JSON.stringify({provider:'chatgpt',user_id:'user',account_id:'account'})).digest('hex');
  let clicked=false,reads=0; const editor={textContent:'',getClientRects:()=>[1],focus(){}};
  const button={get disabled(){return !editor.textContent},getClientRects:()=>[1],click(){clicked=true}};
  const document={querySelector(){return null},
    querySelectorAll(s){return s==='[contenteditable="true"].ProseMirror'?[editor]:s==='button[aria-label="Send"]'&&editor.textContent?[button]:[]},execCommand(_c,_s,t){editor.textContent=t}};
  const tab={id:1,url:'https://chatgpt.com/c/'+ref,status:'complete'},data={};
  const chrome={storage:{local:{async get(k){return {[k]:data[k]}},async set(p){Object.assign(data,p)}}},tabs:{get:async()=>tab},
    scripting:{executeScript:async({func,args})=>[{result:await func(...args)}]}};
  const self={UserIOConnections:{subscriptionsForScope:async()=>[{id:'work'}]},UserIO:{call:async()=>({accepted:true})},
    UserIOScopedChatGPT:{validRef:r=>r===ref||r===id,exactTab:async()=>({ok:true,tab}),read:async()=>{reads++;return {ok:true,chat_ref:ref,account_scope:scope,messages:clicked?[{id,role:'user',text:'hello'}]:[]}}}};
  const location={origin:'https://chatgpt.com',pathname:'/c/'+ref,href:tab.url};
  const immediate=(f)=>{queueMicrotask(f);return 1};
  vm.runInNewContext(fs.readFileSync(new URL('../lib/agent.js',import.meta.url),'utf8'),{self,chrome,URL,setTimeout:immediate,console,document,location,crypto:webcrypto,TextEncoder,fetch:async()=>({ok:true,json:async()=>({user:{id:'user'},account:{id:'account'}})})});
  const result=await self.Agent.processCommand({id:'send-1',agent_id:'agent',action:'gpt_send',args:{text:'hello',chat_ref:ref,mode:'ui',expected_account_scope:scope}},{connectionId:'work',principalId:'alice',agentInstanceId:'instance'});
  assert.equal(result.native_user_message_id,id);assert.equal(result.chat_ref,ref);assert.equal(result.admission,'confirmed');assert.equal(reads,2);
});
test('scoped first send is fenced and never replayed',async()=>{
  const f=fixture(),ctx={connectionId:'work',principalId:'alice',agentInstanceId:'instance'};
  const cmd={id:'first-1',agent_id:'agent',action:'gpt_first_send',args:{expected_account_scope:'scope-a'}};
  assert.equal((await f.self.Agent.processCommand(cmd,ctx)).admission,'confirmed');
  assert.equal((await f.self.Agent.processCommand(cmd,ctx)).error,'command_already_seen');
  assert.equal(f.executed.length,1);
});
test('command with mismatched connection or principal never executes or posts',async()=>{
  const f=fixture(); await assert.rejects(()=>f.self.Agent.processCommand({id:'1',action:'gpt_read',connection_id:'personal'},{connectionId:'work',principalId:'alice',agentInstanceId:'i'}),/command_context_mismatch/); assert.equal(f.calls.length,0);
});
test('Z.ai lifecycle commands require a bound destination and dispatch to exact handlers',async()=>{
  const f=fixture(),ctx={connectionId:'work',principalId:'alice',agentInstanceId:'instance'}, seen=[];
  f.self.UserIOZai={raw:async args=>{seen.push(['raw',args]);return {ok:true}},archive:async args=>{seen.push(['archive',args]);return {ok:true}}};
  f.self.UserIOZaiLifecycle={firstSend:async args=>{seen.push(['first',args]);return {ok:true}}};
  for(const [action,name] of [['zai_raw','raw'],['zai_first_send','first'],['zai_archive','archive']]){
    const denied={id:action+'-no',action,args:{expected_account_scope:'scope-b'}};
    assert.equal((await f.self.Agent.processCommand(denied,ctx)).error,'account_not_authorized');
    const allowed={id:action+'-yes',action,args:{expected_account_scope:'scope-a'}};
    assert.equal((await f.self.Agent.processCommand(allowed,ctx)).ok,true);
    assert.equal(seen.at(-1)[0],name);
  }
  assert.equal(seen.length,3);
});
