import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash, webcrypto } from 'node:crypto';
import vm from 'node:vm';

const id='11111111-1111-4111-8111-111111111111';
const msg='22222222-2222-4222-8222-222222222222';
const scope=createHash('sha256').update('fixture-owner').digest('hex');
function fixture(){
  let archived=false,posts=0; const bodies=[];
  const fetch=async(url,opts={})=>{
    if(url.endsWith('/archive')){posts++;archived=!archived;return {ok:true};}
    const body=url.endsWith('/messages/batch')
      ? ` { "chat_id":"${id}", "data":{"${msg}":{"id":"${msg}","content":"native  text"}} } `
      : JSON.stringify({id,user_id:'fixture-owner',archived,chat:{history:{messages:{[msg]:{}}}}});
    bodies.push(body);return {ok:true,text:async()=>body,json:async()=>JSON.parse(body)};
  };
  const tab={id:1,url:'https://chat.z.ai/',status:'complete'};
  const chrome={tabs:{query:async()=>[tab],get:async()=>tab},scripting:{executeScript:async({func,args})=>[{result:await func(...args)}]}};
  const context={self:{},chrome,URL,location:{origin:'https://chat.z.ai'},crypto:webcrypto,TextEncoder,AbortController,setTimeout,clearTimeout,fetch};
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
  assert.equal((await f.api.archive({chat_ref:id,expected_account_scope:scope})).ok,true);
  assert.equal((await f.api.archive({chat_ref:id,expected_account_scope:scope})).already,true);
  assert.equal(f.posts(),1);
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
  let clicks=0;
  class TextArea { constructor(){this._value=''} get value(){return this._value} set value(v){this._value=v} getClientRects(){return [1]} dispatchEvent(){} }
  const editor=new TextArea(),location={origin:'https://chat.z.ai',pathname:'/'};
  const button={disabled:false,getClientRects:()=>[1],click(){clicks++;const tab=tabs.get(1);tab.url='https://chat.z.ai/c/'+id;location.pathname='/c/'+id}};
  const document={querySelector:s=>s.startsWith('textarea')?editor:s==='#send-message-button'?button:null,querySelectorAll:()=>[]};
  const chrome={storage:{local:{get:async k=>({[k]:storage.get(k)}),set:async v=>Object.entries(v).forEach(([k,x])=>storage.set(k,x))}},tabs:{
    create:async({url})=>{const t={id:1,url,status:'complete'};tabs.set(1,t);return t},get:async k=>tabs.get(k),remove:async k=>tabs.delete(k)}};
  const root={UserIOZai:{list:async()=>({ok:true}),_invoke:async(_tab,func,args)=>func(...args),raw:async()=>({ok:true,batches:[JSON.stringify({data:{[msg]:{id:msg,role:'user',content:'hello'}}})]})}};
  const context={self:root,chrome,URL,setTimeout,clearTimeout,Date,location,document,HTMLTextAreaElement:TextArea,Event:class {},crypto:webcrypto,TextEncoder,fetch:async()=>({ok:true,json:async()=>({id:'fixture-owner'})})};
  vm.runInNewContext(readFileSync(new URL('../lib/zai-lifecycle.js',import.meta.url),'utf8'),context);
  const result=await root.UserIOZaiLifecycle.firstSend({request_id:owner,expected_account_scope:scope,text:'hello'});
  assert.equal(result.admission,'confirmed');assert.equal(result.native_user_message_id,msg);assert.equal(clicks,1);
});
