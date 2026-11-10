import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source=readFileSync(new URL('../content/chatgpt-fetch-hook.js',import.meta.url),'utf8');
async function observe(path,body){
 const events=[],response=new Response(JSON.stringify(body),{headers:{'Content-Type':'application/json'}});
 let count=0;const promise=Promise.resolve(response);
 const window={fetch:()=>{count++;return promise;},postMessage:e=>events.push(e.observation)};
 const context={window,location:{origin:'https://chatgpt.com',href:'https://chatgpt.com/'},URL,TextDecoder,Uint8Array};
 vm.createContext(context);vm.runInContext(source,context);
 assert.equal(window.fetch(path),promise);assert.equal(await promise,response);
 for(let i=0;i<30;i++)await new Promise(r=>setImmediate(r));
 assert.equal(count,1);assert.deepEqual(await response.json(),body);return events;
}
test('plural and singular exact history paths capture requested id and strip secrets',async()=>{
 for(const path of ['/backend-api/conversations/chat-1?num_turns=60&include_has_versions=true','/backend-api/conversation/chat-1?x=1']){
  const events=await observe(path,{conversation_id:'chat-1',messages:[{id:'m1',author:{role:'user'},content:{parts:['hello']},metadata:{accessToken:'secret-value'}}],cookie:'secret-cookie'});
  assert.equal(events.length,1);assert.equal(events[0].chatId,'chat-1');assert.equal(events[0].data.messages[0].id,'m1');
  assert.doesNotMatch(JSON.stringify(events),/secret-value|secret-cookie/);
 }
});
test('rejects mismatched identity and non-history paths',async()=>{
 for(const path of ['/backend-api/conversations/chat-1','/backend-api/conversation/chat-1'])
  assert.equal((await observe(path,{conversation_id:'other',messages:[]})).length,0);
 for(const path of ['/backend-api/conversations','/backend-api/conversations/chat-1/extra','/backend-api/conversations/chat%2Fother','https://evil.example/backend-api/conversations/chat-1'])
  assert.equal((await observe(path,{conversation_id:'chat-1',messages:[]})).length,0);
});
