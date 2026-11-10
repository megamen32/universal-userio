import {webcrypto} from 'node:crypto';import test from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';import vm from 'node:vm';
const script=fs.readFileSync(new URL('../lib/chatgpt-passive.js',import.meta.url),'utf8');
const stateScript=fs.readFileSync(new URL('../lib/chatgpt-state.js',import.meta.url),'utf8');
function fixture(status=200){
 const values={},cleared=[],calls=[],listeners=[];
 const self={ChatGPT:{start:()=>{throw Error('legacy poller started');}},UserIO:{call:async()=>({accounts:[]})}};
 const fetch=async(url)=>{calls.push(String(url));return new Response('{}',{status,headers:{'Retry-After':'120'}});};
 const chrome={alarms:{clear:async name=>cleared.push(name)},storage:{local:{get:async defaults=>({...defaults,...values}),set:async next=>Object.assign(values,next)}},runtime:{onMessage:{addListener:f=>listeners.push(f)}}};
 const context={self,chrome,fetch,Response,URL,Date,Map,Set,console,setTimeout,clearTimeout,crypto:webcrypto,TextEncoder,Uint8Array};vm.createContext(context);vm.runInContext(fs.readFileSync(new URL('../lib/chatgpt-core.js',import.meta.url),'utf8'),context);vm.runInContext(stateScript,context);vm.runInContext(script,context);
 return {context,self,values,cleared,calls,listeners};
}
test('startup removes legacy sweep and makes no ChatGPT request',async()=>{
 const f=fixture();await f.self.ChatGPT.start();assert.deepEqual(f.cleared,['userio-gpt-sync']);assert.equal(f.calls.length,0);
});
test('429 persists cooldown and later extension requests do not reach ChatGPT',async()=>{
 const f=fixture(429);await f.context.fetch('https://chatgpt.com/api/auth/session');
 assert.ok(f.values.chatgptSyncCooldownUntil>Date.now()+100_000);
 assert.equal((await f.context.fetch('https://chatgpt.com/backend-api/conversations')).status,429);assert.equal(f.calls.length,1);
 await f.context.fetch('https://msg.bezrabotnyi.com/v1/agent/poll');assert.equal(f.calls.length,2);
});
test('observations from other origins cannot publish messages or change cooldown',async()=>{
 const f=fixture();let reply;
 const listener=f.listeners[0];
 assert.equal(listener({kind:'userioChatGPTObserved',observation:{kind:'rate_limit',retryAfter:'999'}},{tab:{url:'https://evil.example/'}},v=>reply=v),false);
 assert.equal(f.values.chatgptSyncCooldownUntil,undefined);assert.equal(reply,undefined);
});
test('cooldown survives worker reload and covers URL inputs',async()=>{
 const f=fixture();f.values.chatgptSyncCooldownUntil=Date.now()+60_000;
 vm.runInContext(script,f.context);
 assert.equal((await f.context.fetch(new URL('https://chatgpt.com/api/auth/session'))).status,429);assert.equal(f.calls.length,0);
});
test('native fetch is not repeated; auth projection excludes all credentials',async()=>{
 const events=[];let requests=0;
 const response=new Response(JSON.stringify({user:{id:'account',email:'owner@example.test'},accessToken:'DO_NOT_EXPORT',cookies:['PRIVATE_COOKIE']}),{headers:{'Content-Type':'application/json'}});
 const promise=Promise.resolve(response);
 const window={fetch:()=>{requests++;return promise;},postMessage:e=>events.push(e)};
 const context={window,location:{origin:'https://chatgpt.com',href:'https://chatgpt.com/'},URL,TextDecoder,Uint8Array,console};vm.createContext(context);
 vm.runInContext(fs.readFileSync(new URL('../content/chatgpt-fetch-hook.js',import.meta.url),'utf8'),context);
 const actual=window.fetch('/api/auth/session');assert.equal(actual,promise);assert.equal(await actual,response);
 for(let i=0;i<30&&!events.length;i++)await new Promise(r=>setImmediate(r));
 assert.equal(requests,1);assert.equal(events.length,1);assert.equal(events[0].observation.email,'owner@example.test');
 assert.ok(!JSON.stringify(events).includes('DO_NOT_EXPORT'));assert.ok(!JSON.stringify(events).includes('PRIVATE_COOKIE'));
 assert.ok((await response.json()).accessToken); // original body remains readable by ChatGPT
});
test('observed 429 takes effect even when UserIO history forwarding is blocked',async()=>{
 const f=fixture();let unblock;
 const blocked=new Promise(r=>unblock=r);
 f.self.UserIO.call=async()=>{await blocked;return {accounts:[]};};
 const sender={tab:{id:1,url:'https://chatgpt.com/'},frameId:0};
 const post=o=>new Promise(resolve=>f.listeners[0]({kind:'userioChatGPTObserved',pageKey:'page',observation:o},sender,resolve));
 await post({kind:'identity',email:'owner@example.test',accountId:'account'});
 const conversation=post({kind:'conversation',chatId:'chat',messages:[]});
 await new Promise(r=>setImmediate(r));
 const rate=post({kind:'rate_limit',retryAfter:'120'});
 await new Promise(r=>setImmediate(r));
 assert.equal((await f.context.fetch('https://chatgpt.com/api/auth/session')).status,429);assert.equal(f.calls.length,0);
 unblock();await Promise.all([conversation,rate]);
});
test('passive projection and addressed consumer share native snapshot',async()=>{
 const f=fixture(),sent=[];f.self.UserIO.call=async(method,path,body)=>{if(method==='GET')return {accounts:[{slug:'owner-example.test'}]};sent.push(body);return {ok:true};};
 f.self.UserIO.publishChatSnapshot=async snapshot=>{for(const m of snapshot.messages)await f.self.UserIO.call('POST','/v1/messages',{route_id:'chatgpt',message:{message_id:snapshot.chat_ref+':'+m.id,body:'['+m.role+'] '+m.text}});return {ok:true,results:[{ok:true,connectionId:'fixture'}]};};
 const sender={tab:{id:1,url:'https://chatgpt.com/'},frameId:0};
 const post=o=>new Promise(resolve=>f.listeners[0]({kind:'userioChatGPTObserved',pageKey:'page',observation:o},sender,resolve));
 await post({kind:'identity',email:'owner@example.test',accountLabel:'owner@example.test',workspaceId:'workspace',accountId:'account'});
 await post({kind:'conversation',chatId:'chat1',messages:[{id:'m1',role:'user',text:'hello',ts:12}]});
 const digest=await webcrypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify({provider:'chatgpt',user_id:'account',workspace_id:'workspace'})));
 const scope=Buffer.from(digest).toString('hex'),key=f.self.UserIOChatGPTCore.accountKey('chatgpt','account','workspace'),view=f.self.UserIOChatGPTCore.view(f.self.UserIOChatGPTState.records[key+':chat1']);
 assert.equal(view.messages[0].id,'m1');assert.equal(view.coverage,'partial');assert.equal(view.ready,false);
 assert.equal(sent[0].message.message_id,'chat1:m1');assert.equal(sent[0].message.body,'[user] hello');
});
