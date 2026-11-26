import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

test('DION handoff accepts only the own popup, including a popup opened in a tab', async () => {
  const handlers = [], calls = [];
  const self = {UserIODB:{}, UserIO:{}, Collect:{start(){}}, Vault:{}, Agent:{start(){}},
    ChatGPT:{start:async()=>{}}, UserIODion:{
      importAccount:async id=>(calls.push(id), {ok:true}),
      beginReauth:async id=>(calls.push(id), {ok:true}),
    }};
  const chrome = {runtime:{id:'own', getURL:path=>'chrome-extension://own/'+path,
    onMessage:{addListener:fn=>handlers.push(fn)}},
    alarms:{create(){},onAlarm:{addListener(){}}}};
  const context = vm.createContext({self, chrome, console, setTimeout, clearTimeout, importScripts(){}});
  vm.runInContext(fs.readFileSync(new URL('../background.js',import.meta.url),'utf8'),context);
  const send = (kind,sender)=>new Promise(resolve=>handlers[0]({kind,connectionId:'backend'},sender,resolve));
  for (const kind of ['dionImport','dionReauth']) {
    assert.equal((await send(kind,{id:'own',url:chrome.runtime.getURL('popup.html'),tab:{id:1}})).ok,true);
    assert.equal((await send(kind,{id:'own',url:chrome.runtime.getURL('popup.html')})).ok,true);
    for (const sender of [{id:'own',url:'https://dion.vc/home',tab:{id:2}},
      {id:'other',url:chrome.runtime.getURL('popup.html')}, {id:'own'}]) {
      assert.match(JSON.stringify(await send(kind,sender)),/extension_ui_required/);
    }
  }
  assert.equal(calls.length,4);
});
