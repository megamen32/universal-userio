import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
class Element {
  constructor() { this.hidden = false; this.disabled = false; this.children = []; this.listeners = {}; this.textContent = ''; this.value = ''; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  replaceChildren(...children) { this.children = children; }
  append(...children) { this.children.push(...children); }
}
test('settings show the connected account and disconnect only the selected account', async () => {
  const ids = ['endpoint','routeId','save','status','userioStatus','userioDisconnect','userioConnect','chatgptSelect','chatgptConnect','chatgptStatus','chatgptAccounts'];
  const el = Object.fromEntries(ids.map(id => [id, new Element()]));
  let accounts = [{slug:'one',email:'one@example.test'},{slug:'two',email:'two@example.test'}];
  const calls = [];
  const chrome = {storage:{local:{get:async defaults => defaults,set:async()=>{}}},runtime:{sendMessage:(msg,cb)=>{
    calls.push(msg);
    if(msg.kind==='userioStatus') cb({ok:true,connected:true,accountLabel:'user'});
    else if(msg.kind==='gptConnectionStatus') cb({ok:true,accounts:[...accounts]});
    else if(msg.kind==='gptDisconnect') { accounts=accounts.filter(a=>a.slug!==msg.slug); cb({ok:true}); }
    else cb({ok:true});
  }}};
  vm.runInNewContext(fs.readFileSync(path.join(root,'options.js'),'utf8'),{window:{USERIO_CONFIG:{},confirm:()=>true},document:{getElementById:id=>el[id],createElement:()=>new Element()},chrome,setTimeout});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(el.chatgptSelect.hidden,false);
  assert.equal(el.chatgptAccounts.children.length,2);
  assert.equal(el.chatgptAccounts.children[0].children[1].textContent,'Отключить');
  await el.chatgptAccounts.children[0].children[1].listeners.click();
  assert.equal(calls.find(x=>x.kind==='gptDisconnect').slug,'one');
  assert.equal(el.chatgptAccounts.children.length,1);
  assert.equal(el.chatgptAccounts.children[0].children[0].textContent,'two@example.test');
});

test('another connected ChatGPT never hides explicit selection of the local profile', async () => {
  const ids = ['endpoint','routeId','save','status','userioStatus','userioDisconnect','userioConnect','chatgptSelect','chatgptConnect','chatgptStatus','chatgptAccounts'];
  const el = Object.fromEntries(ids.map(id => [id, new Element()]));
  let accounts=[{slug:'remote',email:'remote@example.test'}];const calls=[];let confirms=0;
  const chrome={storage:{local:{get:async defaults=>defaults,set:async()=>{}}},runtime:{sendMessage:(msg,cb)=>{
    calls.push(msg);
    if(msg.kind==='userioStatus')cb({ok:true,connected:true,accountLabel:'owner'});
    else if(msg.kind==='gptConnectionStatus')cb({ok:true,accounts:[...accounts]});
    else if(msg.kind==='gptIdentity')cb({ok:true,email:'local@example.test',name:'Local Profile',fingerprint:'local-fingerprint'});
    else if(msg.kind==='gptRegister'){accounts.push({slug:'local',email:'local@example.test'});cb({ok:true});}
    else cb({ok:true});
  }}};
  vm.runInNewContext(fs.readFileSync(path.join(root,'options.js'),'utf8'),{window:{USERIO_CONFIG:{},confirm:()=>{confirms++;return true}},document:{getElementById:id=>el[id],createElement:()=>new Element()},chrome,setTimeout});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(el.chatgptSelect.hidden,false,'existing remote account must not prevent adding this profile');
  assert.equal(calls.some(x=>x.kind==='gptRegister'),false);
  await el.chatgptSelect.listeners.click();
  assert.match(el.chatgptStatus.textContent,/local@example.test/);
  assert.equal(el.chatgptConnect.hidden,false);assert.equal(confirms,0);
  assert.equal(calls.some(x=>x.kind==='gptRegister'),false,'identity inspection must not upload cookies');
  await el.chatgptConnect.listeners.click();
  assert.equal(confirms,1);assert.equal(calls.find(x=>x.kind==='gptRegister').expectedFingerprint,'local-fingerprint');
  assert.equal(el.chatgptAccounts.children.length,2);
  assert.equal(el.chatgptSelect.hidden,false,'further profiles remain connectable');
});
