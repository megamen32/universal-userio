import test from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';import vm from 'node:vm';import {randomUUID} from 'node:crypto';
test('background RPC uses real connection registry and passes selected connection to OAuth',async()=>{
 const values={},handlers=[],oauth=[];const self={UserIODB:{},UserIO:{},Collect:{start(){}},Vault:{},Agent:{start(){}},ChatGPT:{start:async()=>{}},UserIOOAuth:{status:async id=>(oauth.push(id),{connected:true})}};
 const chrome={storage:{local:{async get(arg){if(Array.isArray(arg))return Object.fromEntries(arg.map(k=>[k,values[k]]));return {...arg,...values}},async set(v){Object.assign(values,v)}}},runtime:{onMessage:{addListener:f=>handlers.push(f)},getURL:x=>'ext://'+x},alarms:{create(){},onAlarm:{addListener(){}}}};
 const c={self,chrome,URL,crypto:{randomUUID},console,setTimeout,clearTimeout,importScripts:(...paths)=>{for(const p of paths)if(p==='lib/connections.js')vm.runInContext(fs.readFileSync(new URL('../'+p,import.meta.url),'utf8'),c)}};vm.createContext(c);vm.runInContext(fs.readFileSync(new URL('../background.js',import.meta.url),'utf8'),c);
 const send=msg=>new Promise(resolve=>handlers[0](msg,{url:'ext://options.html'},resolve));
 const saved=await send({kind:'connectionsSave',input:{id:'work',endpoint:'https://example.test',label:'Work'}});assert.equal(saved.connection.id,'work');
 assert.equal((await send({kind:'connectionsList'})).connections.some(x=>x.id==='work'),true);
 await self.UserIOConnections.observeAccount({provider:'chatgpt',userId:'u',workspaceId:'w'});const accounts=await send({kind:'accountsList'});const key=accounts.accounts[0].accountKey;
 await send({kind:'accountBindingsSave',accountKey:key,connectionIds:['work']});assert.equal((await send({kind:'accountBindingsGet'})).bindings[key][0],'work');
 await send({kind:'userioStatus',connectionId:'work'});assert.deepEqual(oauth,['work']);
});
