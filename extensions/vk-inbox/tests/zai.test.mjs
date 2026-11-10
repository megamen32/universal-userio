import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { webcrypto, createHash } from "node:crypto";
import vm from "node:vm";
const ROOT=new URL("../",import.meta.url);
const ID="11111111-1111-4111-8111-111111111111";
const USER="22222222-2222-4222-8222-222222222222";
const ASST="33333333-3333-4333-8333-333333333333";
const SCOPE=createHash("sha256").update("zai-user-private").digest("hex");
function response(body,status=200,after=null){
 return {ok:status>=200&&status<300,status,
   headers:{get:key=>key.toLowerCase()==="retry-after"?after:null},json:async()=>body};
}
function fixture({list=[{id:ID,title:"Existing",updated_at:1800000000}],
 status=200,scope="zai-user-private",history,token="fixture-token",role="user",path="/",requireAuth=false,authStatus=200}={}){
 const fetches=[],created=[],removed=[],tabs=[{id:1,url:"https://chat.z.ai/",status:"complete"}];
 const texts={
  [USER]:{id:USER,role:"user",content:"Hello",created_at:1},
  [ASST]:{id:ASST,role:"assistant",content_blocks:[{type:"markdown",content:"World"}],
          created_at:2,done:true}
 };
 const map=history || {[USER]:{},[ASST]:{}};
 const fetch=async(path,opts={})=>{
  fetches.push({path,method:opts.method||"GET",body:opts.body||null,authorization:opts.headers?.authorization});
  if(requireAuth&&opts.headers?.authorization!=="Bearer "+token)return response({},401);
  if(path==="/api/v1/auths/")return response({id:scope,role},authStatus);
  if(path.startsWith("/api/v1/chats/?"))return response(list,status,"47");
  if(path.endsWith("/messages/batch")){
    const ids=JSON.parse(opts.body).ids;
    return response({chat_id:ID,data:Object.fromEntries(ids.map(id=>[id,texts[id]]))});
  }
  if(path==="/api/v1/chats/"+ID)return response({id:ID,user_id:scope,
    chat:{history:{messages:map}},last_run:{status:"completed"}});
  return response({},404);
 };
 const chrome={
  tabs:{
   query:async()=>tabs.slice(),get:async id=>tabs.find(t=>t.id===id),
   create:async({url})=>{const next={id:tabs.length+1,url,status:"complete"};tabs.push(next);created.push(next);return next;},
   remove:async id=>{removed.push(id);}
  },
  scripting:{executeScript:async({target,func,args=[]})=>{
    assert.ok(tabs.some(t=>t.id===target.tabId));
    return [{result:await func(...args)}];
  }},
  storage:{local:{
    items:new Map(),
    async get(k){if(typeof k==='object')return Object.fromEntries(Object.entries(k).map(([key,value])=>[key,this.items.has(key)?this.items.get(key):value]));return {[k]:this.items.get(k)};},
    async set(value){for(const [k,v] of Object.entries(value))this.items.set(k,v);}
  }}
 };
 const context={self:{},chrome,URL,
   location:{origin:"https://chat.z.ai",pathname:path,href:"https://chat.z.ai"+path},localStorage:{getItem:()=>token},
   crypto:webcrypto,TextEncoder,AbortController,setTimeout,clearTimeout,fetch};
 vm.runInNewContext(readFileSync(new URL("lib/zai.js",ROOT),"utf8"),context);
 return {api:context.self.UserIOZai,fetches,created,removed,tabs,context,chrome};
}
test("ZAI never observes anonymous identities or accounts on auth page",async()=>{
 for(const id of ["guest-one","guest-two"]){
  for(const overrides of [{token:null},{role:"guest"},{path:"/auth"},{authStatus:401}]){
   const f=fixture({scope:id,...overrides});let observed=0;
   f.context.self.UserIOConnections={observeAccount:async()=>{observed++;}};
   const result=await f.api.scope();assert.equal(result.ok,false);assert.equal(result.error,"authentication_required");assert.equal(observed,0);
  }
 }
});
test("ZAI uses authenticated native requests without exposing the token",async()=>{
 const f=fixture({requireAuth:true});
 assert.equal((await f.api.list({expected_account_scope:SCOPE})).ok,true);
 assert.equal((await f.api.read({chat_ref:ID,expected_account_scope:SCOPE})).ok,true);
 assert.ok(f.fetches.every(x=>x.authorization==='Bearer fixture-token'));
 assert.equal(JSON.stringify(await f.api.scope()).includes('fixture-token'),false);
});
test("ZAI list is scoped and returns native IDs without browser secrets",async()=>{
 const f=fixture();
 const r=await f.api.list({expected_account_scope:SCOPE});
 assert.equal(r.ok,true);assert.equal(r.chats.length,1);
 assert.equal(r.chats[0].id,ID);assert.equal(r.account_scope,SCOPE);
 assert.equal("user_id" in r,false);assert.equal("cookies" in r,false);
 assert.equal(f.fetches.length,3);
});
test("ZAI observes actual identity without granting a connection and preserves explicit binding",async()=>{
 const f=fixture();
 vm.runInNewContext(readFileSync(new URL("lib/connections.js",ROOT),"utf8"),f.context);
 assert.equal((await f.api.scope()).account_scope,SCOPE);
 const accounts=await f.context.self.UserIOConnections.accounts();
 assert.equal(accounts.length,1);
 assert.equal(accounts[0].userId,"zai-user-private");
 assert.equal(accounts[0].accountScope,SCOPE);
 assert.equal(accounts[0].connectionIds.length,0);
 assert.equal((await f.context.self.UserIOConnections.subscriptionsForScope("zai",SCOPE)).length,0);
 await f.context.self.UserIOConnections.updateBindings(accounts[0].key,["legacy-default"]);
 await f.api.scope();
 assert.equal((await f.context.self.UserIOConnections.subscriptionsForScope("zai",SCOPE)).length,1);
 assert.equal((await f.context.self.UserIOConnections.subscriptionsForScope("zai","f".repeat(64))).length,0);
});
test("ZAI read uses exact chat identity and batch, preserving the latest pair",async()=>{
 const f=fixture();
 const r=await f.api.read({chat_ref:ID,expected_account_scope:SCOPE});
 assert.equal(r.ok,true);assert.equal(r.ready,true);
 assert.equal(r.latest_user.text,"Hello");assert.equal(r.latest_assistant.text,"World");
 assert.deepEqual(Array.from(r.messages,m=>m.id),[USER,ASST]);
 assert.equal(f.fetches.length,2);
});
test("Wrong scope never gets history text or sends a batch",async()=>{
 const f=fixture();
 const r=await f.api.read({chat_ref:ID,expected_account_scope:"f".repeat(64)});
 assert.equal(r.error,"account_scope_mismatch");
 assert.equal(f.fetches.length,1);
});
test("Invalid ref rejects before any site request",async()=>{
 const f=fixture();
 const r=await f.api.read({chat_ref:"../other",expected_account_scope:SCOPE});
 assert.equal(r.error,"zai_invalid_reference");assert.equal(f.fetches.length,0);
});
test("429 respects Retry-After and does not retry",async()=>{
 const f=fixture({status:429});
 const r=await f.api.list();
 assert.equal(r.error,"zai_http_429");assert.equal(r.retry_after,47);
 assert.equal(f.fetches.length,2);
});
test("Two simulated profile contexts maintain independent auth scope",async()=>{
 const a=fixture({scope:"first"});
 const b=fixture({scope:"second"});
 const sa=createHash("sha256").update("first").digest("hex");
 const sb=createHash("sha256").update("second").digest("hex");
 assert.equal((await a.api.scope()).account_scope,sa);
 assert.equal((await b.api.scope()).account_scope,sb);
 assert.equal((await a.api.list({expected_account_scope:sb})).error,"account_scope_mismatch");
 assert.equal((await b.api.list({expected_account_scope:sa})).error,"account_scope_mismatch");
});
test("Judge refuses malformed model and never opens a tab",async()=>{
 const f=fixture();
 vm.runInNewContext(readFileSync(new URL("lib/zai-judge.js",ROOT),"utf8"),f.context);
 const r=await f.context.self.UserIOZaiJudge.infer({
   request_id:ID,expected_account_scope:SCOPE,model:"unsafe-model",prompt:"Hello"
 });
 assert.equal(r.error,"invalid_zai_judge_request");assert.equal(f.created.length,0);
});
test("Judge account mismatch is a durable no-click rejection",async()=>{
 const f=fixture();
 vm.runInNewContext(readFileSync(new URL("lib/zai-judge.js",ROOT),"utf8"),f.context);
 const input={request_id:ID,expected_account_scope:"f".repeat(64),model:"glm-5.3",prompt:"Return JSON"};
 const r=await f.context.self.UserIOZaiJudge.infer(input);
 assert.equal(r.error,"account_scope_unavailable");assert.equal(f.created.length,0);
 assert.equal((await f.context.self.UserIOZaiJudge.infer(input)).error,"zai_judge_duplicate_attempt_refused");
 const status=await f.context.self.UserIOZaiJudge.status({request_id:ID});
 assert.equal(status.phase,"rejected");
});

test("ZAI judge submits only to a new owned tab, with one native-correlated click",async()=>{
 const f=fixture();
 vm.runInNewContext(readFileSync(new URL("lib/zai-judge.js",ROOT),"utf8"),f.context);
 const steps=[];
 f.context.self.UserIOZaiJudgePageOperation=async p=>{
   steps.push(p.step);
   if(p.step==="claim")return {ok:true,path:"/",phase:"idle",draft:false};
   if(p.step==="click"){
     f.tabs.at(-1).url="https://chat.z.ai/c/"+ID;
     return {ok:true};
   }
   if(p.step==="read")return {ok:true,text:'{"done":true}',
     userMessageId:USER,assistantMessageId:ASST};
   if(p.step==="state")return {ok:true,path:f.tabs.at(-1).url.split("chat.z.ai")[1],phase:"idle",draft:false};
   return {ok:true};
 };
 const r=await f.context.self.UserIOZaiJudge.infer({
   request_id:ID,expected_account_scope:SCOPE,model:"glm-5.3",
   prompt:"Only a disposable judge question"
 });
 assert.equal(r.ok,true);assert.equal(r.kind,"answer");
 assert.equal(r.userMessageId,USER);assert.equal(r.assistantMessageId,ASST);
 assert.equal(steps.filter(x=>x==="click").length,1);
 assert.equal(f.created.length,1);assert.equal(f.removed.length,1);
 assert.equal((await f.context.self.UserIOZaiJudge.status({request_id:ID})).phase,"confirmed");
});
test("Unknown ZAI judge click cannot be repeated even with same request id",async()=>{
 const f=fixture();
 vm.runInNewContext(readFileSync(new URL("lib/zai-judge.js",ROOT),"utf8"),f.context);
 let clicks=0;
 f.context.self.UserIOZaiJudgePageOperation=async p=>{
   if(p.step==="claim")return {ok:true,path:"/",phase:"idle",draft:false};
   if(p.step==="click"){clicks++;return {ok:false,reason:"transport_lost"};}
   if(p.step==="state")return {ok:true,path:"/",phase:"idle",draft:false};
   return {ok:true};
 };
 const params={request_id:ID,expected_account_scope:SCOPE,model:"glm-5.2",prompt:"One-time"};
 const r=await f.context.self.UserIOZaiJudge.infer(params);
 assert.equal(r.error,"zai_judge_outcome_unknown");assert.equal(r.admission,"unknown");
 assert.equal(clicks,1);
 assert.equal((await f.context.self.UserIOZaiJudge.infer(params)).error,"zai_judge_duplicate_attempt_refused");
 assert.equal(clicks,1);
 assert.equal(f.removed.length,0);
});
test("ZAI existing-chat send rejects malformed identity without clicks",async()=>{
 const f=fixture();
 vm.runInNewContext(readFileSync(new URL("lib/zai-send.js",ROOT),"utf8"),f.context);
 const r=await f.context.self.UserIOZaiSend.send({
   chat_ref:"invalid",expected_account_scope:SCOPE,text:"hello",request_id:ID
 });
 assert.equal(r.error,"zai_invalid_send_request");
 assert.equal(f.created.length,0);
});
test("ZAI existing-chat send refuses incomplete last native run",async()=>{
 const f=fixture({history:{[USER]:{}}});
 vm.runInNewContext(readFileSync(new URL("lib/zai-send.js",ROOT),"utf8"),f.context);
 const result=await f.context.self.UserIOZaiSend.send({
   chat_ref:ID,expected_account_scope:SCOPE,text:"should not click",request_id:ID
 });
 assert.equal(result.admission,"not_submitted");
 assert.equal(result.error,"zai_native_chat_not_ready");
 assert.equal(f.created.length,0);
});

test("A fresh ZAI account with no chats still has a SHA-256 scope",async()=>{
 const f=fixture({list:[]});
 const result=await f.api.scope();
 assert.equal(result.ok,true);
 assert.equal(result.account_scope,SCOPE);
 assert.equal(f.fetches.length,2);
});
test("Incorrect ZAI account scope never fetches chat list",async()=>{
 const f=fixture();
 const result=await f.api.list({expected_account_scope:"e".repeat(64)});
 assert.equal(result.error,"account_scope_mismatch");
 assert.equal(f.fetches.length,1);
});


test("ZAI exact-chat sender clicks once, then confirms only a new native user ID",async()=>{
 const f=fixture();
 vm.runInNewContext(readFileSync(new URL("lib/zai-send.js",ROOT),"utf8"),f.context);
 let reads=0,clicks=0;
 const NEW="44444444-4444-4444-8444-444444444444";
 f.context.self.UserIOZai.read=async()=>{
   reads++;
   return {ok:true,ready:true,latest_assistant:{id:ASST},
     messages:[{id:USER,role:"user",text:"previous"} ,
       ...(reads>1?[{id:NEW,role:"user",text:"unique test turn"}]:[])]};
 };
 f.chrome.scripting.executeScript=async({func,args})=>{
   const name=func.name;
   if(name==="routePage")return [{result:{ok:true,path:"/c/"+ID}}];
   if(name==="checkPage"||name==="modelPage")return [{result:{ok:true}}];
   if(name==="clickPage"){clicks++;return [{result:{ok:true,clicked:true}}];}
   throw Error("unexpected browser script: "+name);
 };
 const input={chat_ref:ID,expected_account_scope:SCOPE,request_id:ID,text:"unique test turn"};
 const res=await f.context.self.UserIOZaiSend.send(input);
 assert.equal(res.ok,true);assert.equal(res.admission,"confirmed");
 assert.equal(res.native_user_message_id,NEW);
 assert.equal(clicks,1);
 assert.equal(f.removed.length,1);
 assert.equal((await f.context.self.UserIOZaiSend.send(input)).error,"zai_send_duplicate_attempt_refused");
 assert.equal(clicks,1);
});
test("ZAI exact-chat sender does not retry an uncertain click",async()=>{
 const f=fixture();
 vm.runInNewContext(readFileSync(new URL("lib/zai-send.js",ROOT),"utf8"),f.context);
 f.context.self.UserIOZai.read=async()=>({ok:true,ready:true,latest_assistant:{id:ASST},
   messages:[{id:USER,role:"user",text:"old"}]});
 let clicks=0;
 f.chrome.scripting.executeScript=async({func})=>{
   if(func.name==="routePage")return [{result:{ok:true,path:"/c/"+ID}}];
   if(func.name==="checkPage"||func.name==="modelPage")return [{result:{ok:true}}];
   if(func.name==="clickPage"){clicks++;return [{result:{ok:false,error:"receipt_lost"}}];}
   throw Error("unknown");
 };
 const input={chat_ref:ID,expected_account_scope:SCOPE,request_id:ID,text:"only once"};
 const res=await f.context.self.UserIOZaiSend.send(input);
 assert.equal(res.error,"zai_send_outcome_unknown");
 assert.equal(res.admission,"unknown");
 assert.equal(clicks,1);
 assert.equal(f.removed.length,0);
 assert.equal((await f.context.self.UserIOZaiSend.send(input)).error,"zai_send_duplicate_attempt_refused");
 assert.equal(clicks,1);
});

test("ZAI judge admits a tab while Chrome still reports loading but composer is ready",async()=>{
 const f=fixture();
 vm.runInNewContext(readFileSync(new URL("lib/zai-judge.js",ROOT),"utf8"),f.context);
 f.context.document={querySelector:()=>({value:""})};
 f.chrome.tabs.create=async({url})=>{
   const tab={id:f.tabs.length+1,url,status:"loading"};
   f.tabs.push(tab);f.created.push(tab);return tab;
 };
 const phases=[];
 f.context.self.UserIOZaiJudgePageOperation=async p=>{
   phases.push(p.step);
   if(p.step==="claim")return {ok:true,path:"/",phase:"idle",draft:false};
   if(p.step==="click"){
     const tab=f.tabs.at(-1);tab.url="https://chat.z.ai/c/"+ID;
     return {ok:true};
   }
   if(p.step==="read")return {ok:true,text:'{"kind":"ok"}',
     userMessageId:USER,assistantMessageId:ASST};
   if(p.step==="state")return {ok:true,path:"/c/"+ID,phase:"idle",draft:false};
   return {ok:true};
 };
 const r=await f.context.self.UserIOZaiJudge.infer({
   request_id:ID,expected_account_scope:SCOPE,model:"glm-5.3-flash",prompt:"one-time"
 });
 assert.equal(r.kind,"answer");
 assert.equal(phases.filter(x=>x==="click").length,1);
 assert.equal(f.created.length,1);
});
