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
  assert.equal(el.chatgptSelect.hidden,true);
  assert.equal(el.chatgptAccounts.children.length,2);
  assert.equal(el.chatgptAccounts.children[0].children[1].textContent,'Отключить');
  await el.chatgptAccounts.children[0].children[1].listeners.click();
  assert.equal(calls.find(x=>x.kind==='gptDisconnect').slug,'one');
  assert.equal(el.chatgptAccounts.children.length,1);
  assert.equal(el.chatgptAccounts.children[0].children[0].textContent,'two@example.test');
});
