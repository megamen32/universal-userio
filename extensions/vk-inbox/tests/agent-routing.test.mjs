import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

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
