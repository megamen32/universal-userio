import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source=fs.readFileSync(new URL('../lib/agent.js',import.meta.url),'utf8').replace('  root.Agent = lib;','  lib.compactReceiptsForTest=compactReceipts; root.Agent = lib;');
function fixture(data={}){
 const calls=[];
 const chrome={storage:{local:{get:async key=>key===null?{...data}:({[key]:data[key]}),set:async patch=>Object.assign(data,patch),remove:async keys=>{for(const key of keys)delete data[key]}}}};
 const self={UserIOConnections:{subscriptionsForScope:async()=>[{id:'work'}]},UserIO:{call:async(...args)=>{calls.push(args);return {accepted:true}}},UserIOScopedChatGPT:{read:async()=>({ok:true,messages:[{text:'snapshot'.repeat(1000)}]})}};
 vm.runInNewContext(source,{self,chrome,URL,setTimeout,console});return {agent:self.Agent,data,calls};
}
test('compact delivered snapshots but preserve write fences, unknown results and login settings',async()=>{
 const f=fixture({'userioCommandReceipt:done':{phase:'delivered',result:{messages:['x'.repeat(100000)]}},'userioCommandReceipt:unknown':{phase:'executed',result:{ok:true}},'userioCommandReceipt:started':{phase:'started'},oauthAccessToken:'fixture-session',userioConnections:[{id:'work'}]});
 const r=await f.agent.compactReceiptsForTest();
 assert.equal(r.compacted,1);assert.equal(f.data['userioCommandReceipt:done'].phase,'delivered');assert.equal(f.data['userioCommandReceipt:done'].result,undefined);
 assert.equal(f.data['userioCommandReceipt:unknown'].result.ok,true);assert.equal(f.data['userioCommandReceipt:started'].phase,'started');assert.equal(f.data.oauthAccessToken,'fixture-session');assert.equal(f.data.userioConnections[0].id,'work');
});
test('bound old read-only receipt metadata without deleting mutation replay fences',async()=>{
 const data={};for(let i=0;i<520;i++)data['userioCommandReceipt:read-'+i]={phase:'delivered',readOnly:true,completedAt:i};data['userioCommandReceipt:write']={phase:'delivered'};
 const f=fixture(data),r=await f.agent.compactReceiptsForTest();assert.equal(r.pruned,8);assert.equal(data['userioCommandReceipt:read-0'],undefined);assert.equal(data['userioCommandReceipt:read-519'].phase,'delivered');assert.equal(data['userioCommandReceipt:write'].phase,'delivered');
});
test('full read result reaches server but only compact receipt remains locally',async()=>{
 const f=fixture();const command={id:'read-1',agent_id:'browser',action:'gpt_read',args:{expected_account_scope:'scope'}};const ctx={connectionId:'work',principalId:'owner',agentInstanceId:'instance'};
 const r=await f.agent.processCommand(command,ctx);assert.equal(r.ok,true);assert.equal(f.calls[0][2].result.messages[0].text.length,8000);
 const record=Object.entries(f.data).find(([k])=>k.startsWith('userioCommandReceipt:'))[1];assert.equal(record.phase,'delivered');assert.equal(record.readOnly,true);assert.equal(record.result,undefined);
 assert.equal((await f.agent.processCommand(command,ctx)).error,'command_already_seen');assert.equal(f.calls.length,1);
});
