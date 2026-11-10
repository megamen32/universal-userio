import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import http from 'node:http';
import { randomUUID } from 'node:crypto';

test('actual bridge sends canonical messages to each selected HTTP destination and VK keeps envelope',async()=>{
  const requests=[];
  const server=http.createServer(async(req,res)=>{let raw='';for await(const chunk of req)raw+=chunk;const body=raw?JSON.parse(raw):undefined;requests.push({url:req.url,auth:req.headers.authorization,body});if(req.method==='GET'&&req.url==='/v1/chatgpt/sessions'){res.writeHead(req.headers.authorization==='Bearer work'?500:200,{'content-type':'application/json'});res.end(JSON.stringify({accounts:[{slug:'a'.repeat(64)}]}));return;}res.writeHead(req.headers.authorization==='Bearer work'?500:202,{'content-type':'application/json'});res.end(JSON.stringify({accepted:true}));});
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  try {
    const endpoint='http://127.0.0.1:'+server.address().port;
    const data={agentId:'agent',agentInstanceId:'instance',routeId:'vk-browser'};
    const chrome={storage:{local:{async get(defaults){return {...defaults,...data}},async set(p){Object.assign(data,p)}}}};
    const self={UserIOConnections:{get:async id=>({id,endpoint,routeId:'vk-browser'}),subscriptions:async ({provider})=>provider==='vk'?[{id:'personal'}]:[{id:'work'},{id:'personal'}]},UserIOOAuth:{accessToken:async id=>id}};
    vm.runInNewContext(fs.readFileSync(new URL('../lib/userio.js',import.meta.url),'utf8'),{self,chrome,crypto:{randomUUID},fetch,URL});
    const result=await self.UserIO.publishChatSnapshot({account_scope:'a'.repeat(64),chat_ref:'chat',messages:[{id:'native-1',role:'user',text:'hello',create_time:100}]});
    assert.equal(result.results[0].ok,false);assert.equal(result.results[1].ok,true);
    await self.UserIO.forwardCapture({peerId:'peer',msgId:'vk-1',body:'vk text',ts:1000});
    assert.deepEqual(requests.map(r=>r.url),['/v1/chatgpt/sessions','/v1/chatgpt/sessions','/v1/messages','/v1/messages']);
    assert.equal(requests[2].body.route_id,'chatgpt');assert.equal(requests[2].body.message.message_id,'chat:native-1');assert.equal(requests[2].body.message.source,'chatgpt:'+'a'.repeat(64));
    assert.equal(requests[3].body.route_id,'vk-browser');assert.equal(requests[3].body.message.source,'vk');assert.equal(requests[3].body.message.message_id,'vk-1');
  } finally {await new Promise(r=>server.close(r));}
});
