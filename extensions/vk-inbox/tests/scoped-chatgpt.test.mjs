import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { webcrypto } from "node:crypto";
import vm from "node:vm";

const SRC=new URL("../lib/scoped-chatgpt.js", import.meta.url);
const STATE_SRC=new URL("../lib/chatgpt-state.js", import.meta.url);
const REF="11111111-1111-4111-8111-111111111111";
const USER="22222222-2222-4222-8222-222222222222";
const FINAL="33333333-3333-4333-8333-333333333333";
const SCOPE="f".repeat(64);
function ok(body){return {ok:true,status:200,headers:{get:()=>null},json:async()=>body};}
function fail(status,retryAfter="0"){return {ok:false,status,headers:{get:()=>retryAfter},json:async()=>({})};}
function fixture({history,scopeId="account-test",historyResponse,existingUrl="https://chatgpt.com/c/"+REF}={}){
  const tabs=[{id:1,url:existingUrl,status:"complete"}],events=[];
  const session={accessToken:"not-disclosed",user:{id:scopeId},account:{id:"account-workspace"}};
  const expectedScope=""; // no exposed token or cookies in assertions
  const fetch=async(path)=>{
    events.push(path);
    if(path==="/api/auth/session")return ok(session);
    if(path.startsWith("/backend-api/conversations/"))return historyResponse||ok(history);
    if(path.startsWith("/backend-api/conversation/"))return ok(history);
    if(path.startsWith("/backend-api/conversations?"))return ok({items:[{id:REF,title:"Example",update_time:10}]});
    return fail(404);
  };
  const chrome={
    tabs:{
      query:async()=>tabs.slice(),
      create:async({url})=>{const tab={id:tabs.length+1,url,status:"complete"};tabs.push(tab);return tab;},
      get:async id=>tabs.find(t=>t.id===id)||null
    },
    scripting:{
      executeScript:async({target,func,args=[]})=>{
        assert.ok(tabs.some(t=>t.id===target.tabId));
        return [{result:await func(...args)}];
      }
    }
  };
  const values={};
  chrome.storage={local:{get:async defaults=>({...defaults,...values}),set:async next=>Object.assign(values,next)}};
  const context={self:{},chrome,URL,location:{origin:"https://chatgpt.com",pathname:"/c/"+REF},
    crypto:webcrypto,TextEncoder,AbortController,setTimeout,clearTimeout,fetch};
  vm.createContext(context);
  vm.runInContext(readFileSync(new URL("../lib/chatgpt-core.js",import.meta.url),"utf8"),context);
  vm.runInContext(readFileSync(STATE_SRC,"utf8"),context);
  vm.runInContext(readFileSync(SRC,"utf8"),context);
  return {api:context.self.UserIOScopedChatGPT,tabs,events,expectedScope};
}
const message=(id,role,text,status="finished_successfully",end_turn=true,channel="final")=>({
 id,author:{role},recipient:"all",channel,status,end_turn,create_time:1,
 content:{parts:[{text}]}
});
test("rejects untrusted references and never navigates on invalid input",async()=>{
  const f=fixture();
  assert.equal((await f.api.read({chat_ref:"../not-a-chat"})).error,"invalid_chat_ref");
  assert.equal(f.tabs.length,1);
  assert.equal(f.events.length,0);
});
test("matches a full exact ChatGPT route, never a colliding prefix",async()=>{
  const f=fixture({existingUrl:"https://chatgpt.com/c/"+REF+"-extra"});
  const result=await f.api.exactTab(REF);
  assert.equal(result.ok,true);
  assert.equal(result.tab.url,"https://chatgpt.com/c/"+REF);
  assert.equal(f.tabs.length,2);
});
test("latest complete final assistant skips analysis and empty nodes",async()=>{
  const f=fixture({history:{conversation_id:REF,messages:[
    message(USER,"user","task"),
    message("44444444-4444-4444-8444-444444444444","assistant","internal","finished_successfully",false,"analysis"),
    message(FINAL,"assistant","completed answer"),
    message("55555555-5555-4555-8555-555555555555","assistant","","finished_successfully",false)
  ]}});
  const r=await f.api.read({chat_ref:REF});
  assert.equal(r.ok,true);
  assert.equal(r.latest_user.text,"task");
  assert.equal(r.latest_assistant.text,"completed answer");
  assert.equal(r.latest_assistant.id,FINAL);
  assert.equal(r.latest_assistant.end_turn,true);
  assert.equal(r.ready,true);
  assert.equal(r.phase,"idle");
});
test("in-progress assistant after final fails closed",async()=>{
  const f=fixture({history:{conversation_id:REF,messages:[
    message(USER,"user","task"),message(FINAL,"assistant","old answer"),
    message("66666666-6666-4666-8666-666666666666","assistant","still generating","in_progress",false)
  ]}});
  const r=await f.api.read({chat_ref:REF});
  assert.equal(r.ok,true);
  assert.equal(r.ready,false);
});
test("falls back to legacy mapping only on 404",async()=>{
  const a=message(USER,"user","request");
  const b=message(FINAL,"assistant","response");
  const history={current_node:FINAL,mapping:{
    [USER]:{parent:null,message:a},[FINAL]:{parent:USER,message:b}}};
  const f=fixture({history,historyResponse:fail(404)});
  const r=await f.api.read({chat_ref:REF});
  assert.equal(r.ok,true);assert.equal(r.ready,true);
  assert.equal(r.latest_assistant.text,"response");
  assert.equal(f.events.filter(p=>p.startsWith("/backend-api/")).length,2);
});
test("429 is surfaced with Retry-After and no fallback",async()=>{
  const f=fixture({historyResponse:fail(429,"46")});
  const r=await f.api.read({chat_ref:REF});
  assert.equal(r.error,"history_http_429");
  assert.equal(r.retry_after,46);
  assert.equal(f.events.filter(p=>p.startsWith("/backend-api/")).length,1);
});
test("list with no configured scope passes serializable arguments",async()=>{
  const f=fixture();
  const r=await f.api.list();
  assert.equal(r.ok,true);assert.equal(r.chats.length,1);
});
