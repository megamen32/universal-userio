import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash,webcrypto} from 'node:crypto';
import vm from 'node:vm';
const ref='11111111-1111-4111-8111-111111111111';
const session={user:{id:'person',name:'Person'},account:{id:'workspace'},accessToken:'fixture'};
const expected=createHash('sha256').update(JSON.stringify({provider:'chatgpt',user_id:'person',account_id:'workspace'})).digest('hex');
function setup(overrides={}){
 const data={},calls=[],provider=[],activeSession=overrides.session||session;
 const chrome={storage:{local:{get:async k=>typeof k==='string'?{[k]:data[k]}:{...k,...data},set:async p=>Object.assign(data,p)}},cookies:{getAll:async()=>[{name:'__Secure-next-auth.session-token',value:'fixture-cookie'}]},tabs:{query:async()=>[{id:1,url:'https://chatgpt.com/c/'+ref,status:'complete'}],get:async()=>({id:1,url:'https://chatgpt.com/c/'+ref,status:'complete'})},scripting:{executeScript:async ({func,args=[]})=>[{result:await func(...args)}]}};
 const fetch=async p=>{provider.push(p);return {ok:true,status:200,headers:{get:()=>null},json:async()=>p==='/api/auth/session'?activeSession:{conversation_id:ref,messages:[{id:'u1',author:{role:'user'},content:{parts:['hi']}},{id:'a1',author:{role:'assistant'},content:{parts:['hello']},status:'finished_successfully',end_turn:true}]}}};
 const self={UserIO:{call:async (...x)=>{calls.push(x);return {ok:true}}}};
 const c={self,chrome,fetch,crypto:webcrypto,TextEncoder,URL,AbortController,setTimeout,clearTimeout,location:{origin:'https://chatgpt.com',pathname:'/c/'+ref},console};vm.createContext(c);
 for(const file of ['connections','chatgpt','chatgpt-core','chatgpt-state','scoped-chatgpt','agent'])vm.runInContext(readFileSync(new URL('../lib/'+file+'.js',import.meta.url),'utf8'),c);
 return {self,data,calls,provider};
}
const ctx={connectionId:'legacy-default',principalId:'principal',agentInstanceId:'browser'};
test('scope agrees with Herder identity and is shared by list and read',async()=>{
 const f=setup(), scope=await f.self.UserIOScopedChatGPT.accountScope();
 assert.equal(scope.account_scope,expected);
 assert.notEqual((await f.self.UserIOScopedChatGPT.list({expected_account_scope:expected})).error,'account_scope_mismatch');
 const r=await f.self.UserIOScopedChatGPT.read({chat_ref:ref,expected_account_scope:expected});
 assert.equal(r.ok,true);assert.equal(r.account_scope,expected);
});
test('bounded session proof runs through real agent and ChatGPT modules',async()=>{
 const f=setup();const result=await f.self.Agent.processCommand({id:'proof',action:'gpt_session_proof',args:{nonce:'challenge'}},ctx);
 assert.equal(result.ok,true);assert.equal(result.present,true);assert.match(result.proof,/^[a-f0-9]{64}$/);
 assert.equal((await f.self.Agent.processCommand({id:'bad',action:'gpt_session_proof',args:{nonce:'!'}},ctx)).error,'invalid_challenge');
 assert.equal((await f.self.Agent.processCommand({id:'other',action:'gpt_register'},ctx)).error,'account_not_authorized');
});
test('real account binding authorizes only its exact observed scope without creating accounts',async()=>{
 const f=setup(), c=f.self.UserIOConnections;
 const a=await c.observeAccount({provider:'chatgpt',userId:'person',workspaceId:'workspace',accountScope:expected});
 await c.updateBindings(a.key,['legacy-default']);
 const before=JSON.stringify(await c.accounts());
 const r=await f.self.Agent.processCommand({id:'read',action:'gpt_read',args:{chat_ref:ref,expected_account_scope:expected}},ctx);
 assert.equal(r.ok,true);
 assert.equal((await f.self.Agent.processCommand({id:'wrong',action:'gpt_read',args:{chat_ref:ref,expected_account_scope:'a'.repeat(64)}},ctx)).error,'account_not_authorized');
 assert.equal(JSON.stringify(await c.accounts()),before);
});
test('bounded snapshots do not claim a complete history',()=>{
 const f=setup(),core=f.self.UserIOChatGPTCore;
 const messages=Array.from({length:101},(_,i)=>({id:'m'+i,role:i%2?'assistant':'user',text:'x'}));
 const r=core.transition(core.initialState(),{provider:'chatgpt',user_id:'person',workspace_id:'workspace',account_scope:expected,chat_ref:ref,coverage:'addressed',messages});
 assert.equal(r.snapshot.messages.length,100);assert.equal(r.snapshot.complete,false);
});
test('persisted snapshots require the new completeness evidence',()=>{
 const core=setup().self.UserIOChatGPTCore;
 const event={provider:'chatgpt',user_id:'person',workspace_id:'workspace',account_scope:expected,chat_ref:ref,coverage:'addressed',messages:[{id:'u',role:'user',text:'hi'},{id:'a',role:'assistant',text:'done',status:'finished_successfully',end_turn:true}]};
 const state=core.transition(core.initialState(),event).state;
 const id=Object.keys(state.records)[0];
 assert.equal(core.view(core.hydrate(core.serialize(state)).records[id]).complete,true);
 delete state.records[id].completenessVerified;delete state.records[id].tailVerified;
 const old=core.view(core.hydrate(state).records[id]);
 assert.equal(old.complete,false);assert.equal(old.ready,false);
});

test('API send rechecks the active account before any provider operation',async()=>{
 const f=setup({session:{...session,account:{id:'other-workspace'}}}),c=f.self.UserIOConnections;
 const a=await c.observeAccount({provider:'chatgpt',userId:'person',workspaceId:'workspace'});
 await c.updateBindings(a.key,['legacy-default']);
 const r=await f.self.Agent.processCommand({id:'send',action:'gpt_send',args:{chat_ref:ref,expected_account_scope:expected,mode:'api',text:'fixture'}},ctx);
 assert.equal(r.error,'account_scope_mismatch');
 assert.deepEqual(f.provider,['/api/auth/session']);
});
test('lookup rejects ambiguous persisted bindings and preserves storage',async()=>{
 const f=setup(),c=f.self.UserIOConnections;
 const a=await c.observeAccount({provider:'chatgpt',userId:'person',workspaceId:'workspace'});
 await c.updateBindings(a.key,['legacy-default']);
 f.data.userioAccountBindings.duplicate={...f.data.userioAccountBindings[a.key]};
 const before=JSON.stringify(f.data.userioAccountBindings);
 assert.equal((await f.self.Agent.processCommand({id:'read2',action:'gpt_read',args:{chat_ref:ref,expected_account_scope:expected}},ctx)).error,'account_not_authorized');
 assert.equal(JSON.stringify(f.data.userioAccountBindings),before);
});
test('empty workspace is kept as an explicit canonical identity',async()=>{
 const f=setup(),c=f.self.UserIOConnections;
 const a=await c.observeAccount({provider:'chatgpt',userId:'person',workspaceId:'',accountId:'fallback',accountScope:'a'.repeat(64)});
 assert.equal(a.workspaceId,'');await c.updateBindings(a.key,['legacy-default']);
 const scope=createHash('sha256').update(JSON.stringify({provider:'chatgpt',user_id:'person',account_id:''})).digest('hex');
 assert.equal((await c.subscriptionsForScope('chatgpt',scope)).length,1);
});

test('colliding legacy binding keys cannot acquire another identity',async()=>{
 const f=setup(),c=f.self.UserIOConnections;
 const a=await c.observeAccount({provider:'chatgpt',userId:'a:b',workspaceId:'c'});
 await c.updateBindings(a.key,['legacy-default']);
 await assert.rejects(()=>c.observeAccount({provider:'chatgpt',userId:'a',workspaceId:'b:c'}),/account_identity_collision/);
});
