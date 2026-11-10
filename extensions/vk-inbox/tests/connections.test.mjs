import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { randomUUID } from 'node:crypto';
test('explicit account bindings and idempotent legacy migration', async () => {
  const data={endpoint:'https://same.example',oauthAccessToken:'legacy'};
  const chrome={storage:{local:{async get(k){if(typeof k==='string')return {[k]:data[k]}; return {...k,...data}}, async set(p){Object.assign(data,p)}}}};
  const self={}; vm.runInNewContext(fs.readFileSync(new URL('../lib/connections.js',import.meta.url),'utf8'),{self,chrome,crypto:{randomUUID},URL});
  const c=self.UserIOConnections, first=await c.list(), legacy=first[0].id;
  const personal=await c.save({label:'Personal',endpoint:'https://same.example',enabled:true});
  const a=await c.observeAccount({provider:'chatgpt',accountId:'A'}), b=await c.observeAccount({provider:'chatgpt',accountId:'B'});
  await c.updateBindings(a.key,[legacy,personal.id]); await c.updateBindings(b.key,[personal.id]);
  assert.deepEqual(Array.from(await c.subscriptions({provider:'chatgpt',accountId:'A'}),x=>x.id),[legacy,personal.id]);
  assert.deepEqual(Array.from(await c.subscriptions({provider:'chatgpt',accountId:'B'}),x=>x.id),[personal.id]);
  assert.deepEqual(Array.from(await c.subscriptions({provider:'chatgpt',accountId:'C'})),[]);
  assert.equal((await c.list()).length,2); assert.equal(data.oauthAccessToken,'legacy');
});
test('logout one connection preserves the other persisted binding', async()=>{
  const data={endpoint:'https://e'}; const chrome={storage:{local:{async get(k){if(typeof k==='string')return {[k]:data[k]};return {...k,...data}},async set(p){Object.assign(data,p)},async remove(k){for(const x of k)delete data[x]}}}};
  const self={}; vm.runInNewContext(fs.readFileSync(new URL('../lib/connections.js',import.meta.url),'utf8'),{self,chrome,crypto:{randomUUID},URL});
  const a=await self.UserIOConnections.save({label:'work'}),b=await self.UserIOConnections.save({label:'personal'});
  assert.equal((await self.UserIOConnections.list()).length,3); await chrome.storage.local.remove(['oauthAccessToken:'+a.id]); assert.ok((await self.UserIOConnections.get(b.id)));
});
test('pure transition preserves input and describes the storage effect', async()=>{
  const self={}; vm.runInNewContext(fs.readFileSync(new URL('../lib/connections.js',import.meta.url),'utf8'),{self,URL});
  const initial=[{id:'work',kind:'userio',endpoint:'https://same.example',enabled:true}];
  Object.freeze(initial[0]); Object.freeze(initial);
  const next=self.UserIOConnections.transitionConnections(initial,{type:'save',input:{id:'personal',endpoint:'https://same.example'}});
  assert.equal(initial.length,1); assert.equal(next.state.length,2); assert.equal(next.effects[0].type,'storage.set');
  assert.equal(self.UserIOConnections.transitionConnections(initial,{type:'save',input:{id:'x',endpoint:'javascript:alert(1)'}}).ok,false);
});
test('receipt requires exact connection and browser identity', async()=>{
  const self={}; vm.runInNewContext(fs.readFileSync(new URL('../lib/connections.js',import.meta.url),'utf8'),{self,URL});
  const expected={id:'cmd',connectionId:'work',agentInstanceId:'browser-a'};
  assert.equal(self.UserIOConnections.validateReceipt(expected,expected),true);
  assert.equal(self.UserIOConnections.validateReceipt({...expected,connectionId:'personal'},expected),false);
  assert.equal(self.UserIOConnections.validateReceipt({...expected,agentInstanceId:'browser-b'},expected),false);
});
