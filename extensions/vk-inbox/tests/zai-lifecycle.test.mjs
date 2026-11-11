import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash, webcrypto } from 'node:crypto';
import vm from 'node:vm';

const id='11111111-1111-4111-8111-111111111111';
const msg='22222222-2222-4222-8222-222222222222';
const scope=createHash('sha256').update('fixture-owner').digest('hex');
test('first send accepts only its exact pending navigation and rejects unsafe routes or timeout',async()=>{
  for(const scenario of ['pending_then_complete','committed_loading','settled_foreign','pending_foreign','pending_timeout','unknown_save_fails']){
    let calls=0,clicks=0,removed=0,clock=0,invokedTab;
    const tabId=7, expected='https://chat.z.ai/?userio_first='+id;
    const chrome={storage:{local:{get:async()=>({}),set:async values=>{
      if((scenario==='settled_foreign'&&Object.values(values)[0].phase==='rejected') ||
         (scenario==='unknown_save_fails'&&Object.values(values)[0].phase==='unknown'))throw new Error('private error');
    }}},tabs:{create:async()=>({id:tabId}),get:async()=>{
      calls++;
      if(scenario==='settled_foreign')return {id:tabId,url:'https://elsewhere.invalid/',status:'complete'};
      if(scenario==='pending_foreign')return {id:tabId,url:'',pendingUrl:'https://elsewhere.invalid/',status:'loading'};
      if(scenario==='committed_loading'&&calls===1)return {id:tabId,url:expected,status:'loading'};
      if(scenario==='pending_timeout'||calls===1)return {id:tabId,url:'',pendingUrl:expected,status:'loading'};
      return {id:tabId,url:expected,status:'complete'};
    },remove:async()=>{removed++}}};
    const root={UserIOZai:{list:async()=>({ok:true}),_invoke:async tab=>{invokedTab=tab;clicks++;return {ok:false}}}};
    const FakeDate=class extends Date {static now(){return clock}};
    const fakeTimeout=fn=>{clock+=1000;queueMicrotask(fn);return 1};
    vm.runInNewContext(readFileSync(new URL('../lib/zai-lifecycle.js',import.meta.url),'utf8'),
      {self:root,chrome,URL,setTimeout:fakeTimeout,clearTimeout,Date:FakeDate});
    const result=await root.UserIOZaiLifecycle.firstSend({request_id:id,expected_account_scope:scope,text:'hello'});
    if(['pending_then_complete','committed_loading','unknown_save_fails'].includes(scenario)){
      assert.equal(calls,2);assert.equal(clicks,1);assert.equal(result.admission,'unknown');
      assert.equal(invokedTab.url,expected);assert.equal(invokedTab.status,'complete');
      assert.equal(result.diagnostic,undefined);assert.equal(removed,0);
    }else{
      assert.equal(clicks,0);assert.equal(result.admission,'not_submitted');assert.equal(removed,1);
      assert.equal(result.diagnostic,scenario==='pending_timeout'?'route_timeout':'route_mismatch');
      assert.equal(JSON.stringify(result).includes('private error'),false);
    }
  }
});
test('first send reports only a safe preflight stage without changing admission',async()=>{
  for(const stage of ['storage_preflight','scope_list','tab_create','route','admission_record']){
    let clicks=0,creates=0,removes=0;
    const sensitive='secret token https://example.test/private user text';
    const fail=()=>{throw new Error(sensitive)};
    const tab={id:7,url:'https://chat.z.ai/',status:'complete'};
    const chrome={storage:{local:{get:async()=>stage==='storage_preflight'?fail():{},set:async values=>{
      if(stage==='admission_record'&&Object.values(values)[0].phase==='admission_unknown')fail();
    }}},tabs:{create:async()=>{creates++;if(stage==='tab_create')fail();return tab},
      get:async()=>stage==='route'?fail():tab,remove:async()=>{removes++}}};
    const root={UserIOZai:{list:async()=>stage==='scope_list'?{ok:false,error:sensitive}:{ok:true},
      _invoke:async()=>{clicks++;return {ok:false}}}};
    vm.runInNewContext(readFileSync(new URL('../lib/zai-lifecycle.js',import.meta.url),'utf8'),
      {self:root,chrome,URL,setTimeout,clearTimeout,Date});
    const result=await root.UserIOZaiLifecycle.firstSend({request_id:id,expected_account_scope:scope,text:'hello'});
    assert.equal(result.error,'zai_first_preflight_failed');
    assert.equal(result.admission,'not_submitted');
    assert.equal(result.diagnostic,stage);
    assert.equal(JSON.stringify(result).includes(sensitive),false);
    assert.equal(clicks,0);
    assert.equal(creates,['tab_create','route','admission_record'].includes(stage)?1:0);
    assert.equal(removes,['route','admission_record'].includes(stage)?1:0);
  }
});
function fixture({before={},after={}}={}){
  let archived=false,posts=0; const bodies=[];
  const fetch=async(url,opts={})=>{
    assert.equal(opts.headers?.authorization,'Bearer fixture-token');
    if(url.endsWith('/archive')){posts++;archived=!archived;return {ok:true};}
    const body=url.endsWith('/messages/batch')
      ? ` { "chat_id":"${id}", "data":{"${msg}":{"id":"${msg}","content":"native  text"}} } `
      : JSON.stringify({id,user_id:'fixture-owner',archived,chat:{history:{messages:{[msg]:{}}}},...(posts?after:before)});
    bodies.push(body);return {ok:true,text:async()=>body,json:async()=>JSON.parse(body)};
  };
  const tab={id:1,url:'https://chat.z.ai/',status:'complete'};
  const chrome={tabs:{query:async()=>[tab],get:async()=>tab},scripting:{executeScript:async({func,args})=>[{result:await func(...args)}]}};
  const context={self:{},chrome,URL,location:{origin:'https://chat.z.ai',pathname:'/'},localStorage:{getItem:()=> 'fixture-token'},crypto:webcrypto,TextEncoder,AbortController,setTimeout,clearTimeout,fetch};
  vm.runInNewContext(readFileSync(new URL('../lib/zai.js',import.meta.url),'utf8'),context);
  return {api:context.self.UserIOZai,bodies,posts:()=>posts};
}
test('raw preserves exact native response bodies and refuses wrong scope',async()=>{
  const f=fixture();
  assert.equal((await f.api.raw({chat_ref:id,expected_account_scope:'f'.repeat(64)})).error,'account_scope_mismatch');
  const r=await f.api.raw({chat_ref:id,expected_account_scope:scope});
  assert.equal(r.ok,true);assert.equal(r.complete,false);
  assert.equal(r.detail,f.bodies.at(-2));assert.equal(r.batches[0],f.bodies.at(-1));
});
test('archive checks scope and readback and does not toggle twice',async()=>{
  const f=fixture();
  assert.equal((await f.api.archive({chat_ref:id,expected_account_scope:'f'.repeat(64)})).error,'account_scope_mismatch');
  assert.equal(f.posts(),0);
  const first=await f.api.archive({chat_ref:id,expected_account_scope:scope});
  assert.equal(first.ok,true);assert.equal(first.chat_ref,id);assert.equal(first.archived,true);
  const second=await f.api.archive({chat_ref:id,expected_account_scope:scope});
  assert.equal(second.ok,true);assert.equal(second.chat_ref,id);assert.equal(second.archived,true);assert.equal(second.already,true);
  assert.equal(f.posts(),1);
});
test('archive returns no confirmation for changed identity or unconfirmed readback',async()=>{
  for(const before of [{id:msg},{user_id:'wrong-user'},{archived:null}]){
    const f=fixture({before});const result=await f.api.archive({chat_ref:id,expected_account_scope:scope});
    assert.equal(result.ok,false);assert.equal(result.archived,undefined);assert.equal(f.posts(),0);
  }
  for(const after of [{id:msg},{user_id:'wrong-user'},{archived:false}]){
    const f=fixture({after});const result=await f.api.archive({chat_ref:id,expected_account_scope:scope});
    assert.equal(result.ok,false);assert.equal(result.error,'archive_outcome_unknown');assert.equal(result.archived,undefined);assert.equal(f.posts(),1);
  }
});
test('first send confirms native identities once and refuses replay after uncertain click',async()=>{
  const storage=new Map(),tabs=new Map(), owner='33333333-3333-4333-8333-333333333333';
  let clicks=0,fail=false;
  const chrome={storage:{local:{get:async k=>({[k]:storage.get(k)}),set:async values=>Object.entries(values).forEach(([k,v])=>storage.set(k,v))}},
    tabs:{create:async({url})=>{const t={id:tabs.size+1,url,status:'complete'};tabs.set(t.id,t);return t;},
      get:async id=>tabs.get(id),remove:async id=>tabs.delete(id)}};
  const root={UserIOZai:{list:async()=>({ok:true}),_invoke:async tab=>{
    clicks++;if(fail)return {ok:false};tab.url='https://chat.z.ai/c/'+id;return {ok:true};},
    raw:async()=>({ok:true,batches:[JSON.stringify({data:{[msg]:{id:msg,role:'user',content:'hello'}}})]})}};
  const context={self:root,chrome,URL,setTimeout,clearTimeout,Date};
  vm.runInNewContext(readFileSync(new URL('../lib/zai-lifecycle.js',import.meta.url),'utf8'),context);
  const args={request_id:owner,expected_account_scope:scope,text:'hello'};
  const result=await root.UserIOZaiLifecycle.firstSend(args);
  assert.equal(result.admission,'confirmed');assert.equal(result.chat_ref,id);assert.equal(result.native_user_message_id,msg);
  assert.equal((await root.UserIOZaiLifecycle.firstSend(args)).error,'zai_first_duplicate');
  fail=true;
  const uncertain={...args,request_id:'44444444-4444-4444-8444-444444444444'};
  assert.equal((await root.UserIOZaiLifecycle.firstSend(uncertain)).admission,'unknown');
  assert.equal((await root.UserIOZaiLifecycle.firstSend(uncertain)).error,'zai_first_duplicate');
  assert.equal(clicks,2);
});
test('first send executes the page function against a fake DOM and verifies native admission',async()=>{
  const storage=new Map(),tabs=new Map(), owner='55555555-5555-4555-8555-555555555555';
  let clicks=0,currentToken='fixture-token',currentRole='user';
  class TextArea { constructor(){this._value=''} get value(){return this._value} set value(v){this._value=v} getClientRects(){return [1]} dispatchEvent(){} }
  const editor=new TextArea(),location={origin:'https://chat.z.ai',pathname:'/'};
  const button={disabled:false,getClientRects:()=>[1],click(){clicks++;const tab=tabs.get(1);tab.url='https://chat.z.ai/c/'+id;location.pathname='/c/'+id}};
  const document={querySelector:s=>s.startsWith('textarea')?editor:s==='#send-message-button'?button:null,querySelectorAll:()=>[]};
  const chrome={storage:{local:{get:async k=>({[k]:storage.get(k)}),set:async v=>Object.entries(v).forEach(([k,x])=>storage.set(k,x))}},tabs:{
    create:async({url})=>{const t={id:1,url,status:'complete'};tabs.set(1,t);return t},get:async k=>tabs.get(k),remove:async k=>tabs.delete(k)}};
  const root={UserIOZai:{list:async()=>({ok:true}),_invoke:async(_tab,func,args)=>func(...args),raw:async()=>({ok:true,batches:[JSON.stringify({data:{[msg]:{id:msg,role:'user',content:'hello'}}})]})}};
  const context={self:root,chrome,URL,setTimeout,clearTimeout,Date,location,document,HTMLTextAreaElement:TextArea,Event:class {},crypto:webcrypto,TextEncoder,localStorage:{getItem:()=>currentToken},fetch:async(_url,opts)=>({ok:opts.headers.authorization==='Bearer fixture-token',json:async()=>({id:'fixture-owner',role:currentRole})})};
  vm.runInNewContext(readFileSync(new URL('../lib/zai-lifecycle.js',import.meta.url),'utf8'),context);
  const result=await root.UserIOZaiLifecycle.firstSend({request_id:owner,expected_account_scope:scope,text:'hello'});
  assert.equal(result.admission,'confirmed');assert.equal(result.native_user_message_id,msg);assert.equal(clicks,1);
  for(const [rid,token,role] of [['66666666-6666-4666-8666-666666666666',null,'user'],['77777777-7777-4777-8777-777777777777','fixture-token','guest']]){
    currentToken=token;currentRole=role;location.pathname='/';
    const refused=await root.UserIOZaiLifecycle.firstSend({request_id:rid,expected_account_scope:scope,text:'hello'});
    assert.equal(refused.ok,false);assert.equal(clicks,1);
  }
});
