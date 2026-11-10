import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { webcrypto, createHash } from "node:crypto";
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
test("first send rejects missing scope or empty text before opening a tab",async()=>{
  const f=fixture();
  assert.equal((await f.api.firstSend({text:'hello',expected_account_scope:'bad'})).error,'invalid_first_send');
  assert.equal((await f.api.firstSend({text:' ',expected_account_scope:SCOPE})).error,'invalid_first_send');
  assert.equal(f.tabs.length,1);
  assert.equal(f.events.length,0);
});
test("archive rejects untrusted target before opening a tab",async()=>{
  const f=fixture();
  assert.equal((await f.api.archive({chat_ref:'../bad',expected_account_scope:SCOPE})).error,'invalid_archive_request');
  assert.equal(f.tabs.length,1);
});
test("first send checks an empty owned tab and returns exact route and user message",async()=>{
  const session={user:{id:'user'},account:{id:'account'}};
  const scope=createHash('sha256').update(JSON.stringify({provider:'chatgpt',user_id:'user',account_id:'account'})).digest('hex');
  const location={origin:'https://chatgpt.com',pathname:'/'};
  const editor={textContent:'',focus(){}}; let clicks=0;
  const msg={textContent:'hello',getAttribute:()=>USER};
  const button={disabled:false,click(){clicks++;location.pathname='/c/'+REF;}};
  const document={querySelector(selector){
    if(selector==='#prompt-textarea')return editor;
    if(selector==='[data-testid="send-button"]')return button;
    if(selector==='[data-message-author-role]')return null;
    return null;
  },querySelectorAll(){return clicks?[msg]:[]},execCommand(_cmd,_show,text){editor.textContent=text}};
  const chrome={tabs:{create:async()=>({id:1,status:'complete'}),get:async()=>({id:1,status:'complete'})},
    scripting:{executeScript:async({func,args})=>[{result:await func(...args)}]}};
  const context={self:{},chrome,URL,location,document,crypto:webcrypto,TextEncoder,AbortController,setTimeout,clearTimeout,fetch:async()=>ok(session)};
  vm.createContext(context); vm.runInContext(readFileSync(SRC,'utf8'),context);
  const result=await context.self.UserIOScopedChatGPT.firstSend({text:'hello',expected_account_scope:scope});
  assert.equal(result.ok,true);assert.equal(result.chat_ref,REF);assert.equal(result.native_user_message_id,USER);assert.equal(clicks,1);
});
test("archive uses exact scope and confirms the same conversation after PATCH",async()=>{
  const session={accessToken:'test-only',user:{id:'user'},account:{id:'account'}};
  const scope=createHash('sha256').update(JSON.stringify({provider:'chatgpt',user_id:'user',account_id:'account'})).digest('hex');
  let archived=false,patches=0;
  const fetch=async(path,options={})=>{
    if(path==='/api/auth/session')return ok(session);
    if(options.method==='PATCH'){patches++;archived=true;return ok({});}
    return ok({conversation_id:REF,is_archived:archived});
  };
  const tab={id:1,url:'https://chatgpt.com/c/'+REF,status:'complete'};
  const chrome={tabs:{query:async()=>[tab],get:async()=>tab},scripting:{executeScript:async({func,args})=>[{result:await func(...args)}]}};
  const context={self:{},chrome,URL,location:{origin:'https://chatgpt.com',pathname:'/c/'+REF},crypto:webcrypto,TextEncoder,AbortController,setTimeout,clearTimeout,fetch};
  vm.createContext(context);vm.runInContext(readFileSync(SRC,'utf8'),context);
  const r=await context.self.UserIOScopedChatGPT.archive({chat_ref:REF,expected_account_scope:scope});
  assert.equal(r.ok,true);assert.equal(r.archived,true);assert.equal(r.chat_ref,REF);assert.equal(patches,1);
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
test("provider pagination and absent terminal proof never claim full history",async()=>{
  for(const markers of [{has_more:true},{next_cursor:'older'},{has_more:false,next_cursor:'older'},{has_more:false,nextCursor:'older'},{}]){
    const f=fixture({history:{conversation_id:REF,messages:[message(USER,'user','task'),message(FINAL,'assistant','done')],...markers}});
    const r=await f.api.read({chat_ref:REF});
    assert.equal(r.complete,false);assert.equal(r.coverage,'partial');assert.equal(r.ready,true);
  }
  const f=fixture({history:{conversation_id:REF,has_more:false,messages:[message(USER,'user','task'),message(FINAL,'assistant','done')]}});
  assert.equal((await f.api.read({chat_ref:REF})).complete,true);
  const busy=fixture({history:{conversation_id:REF,has_more:true,messages:[message(USER,'user','task'),message(FINAL,'assistant','working','in_progress',false)]}});
  assert.equal((await busy.api.read({chat_ref:REF})).ready,false);
});
test("legacy branch must reach an explicit root; missing parents and cycles stay partial",async()=>{
  for(const parent of ['missing',FINAL]){
    const history={current_node:FINAL,mapping:{[FINAL]:{parent:USER,message:message(FINAL,'assistant','done')},[USER]:{parent,message:message(USER,'user','task')}}};
    const r=await fixture({history,historyResponse:fail(404)}).api.read({chat_ref:REF});
    assert.equal(r.ok,true);assert.equal(r.complete,false);assert.equal(r.coverage,'partial');assert.equal(r.ready,false);
  }
});
test("falls back to legacy mapping only on 404",async()=>{
  const a=message(USER,"user","request");
  const b=message(FINAL,"assistant","response");
  const history={current_node:FINAL,mapping:{
    [USER]:{parent:null,message:a},[FINAL]:{parent:USER,message:b}}};
  const f=fixture({history,historyResponse:fail(404)});
  const r=await f.api.read({chat_ref:REF});
  assert.equal(r.ok,true);assert.equal(r.ready,true);assert.equal(r.complete,true);
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
