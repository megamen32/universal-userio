// Fast unit, expected0.1s/max2s: a lost cancel response must not be displayed
// as a confirmed cancellation after a concurrent login may have completed.
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const page=readFileSync(new URL('../src/universal_userio/static/connect-accounts.html',import.meta.url),'utf8');
const start=page.indexOf('async function cancelMax()');
const end=page.indexOf("document.getElementById('max-begin').onclick",start);
assert.ok(start>0 && end>start);
for (const refused of [false,true]) {
 const elements={'max-cancel':{},'max-status':{}};
 let refreshed=0,stopped=0;
 const context=vm.createContext({maxLogin:'fixture-own-login',stopMax:()=>stopped++,
  maxRequest:async(action,payload)=>{assert.equal(action,'cancel');assert.equal(payload.login_id,'fixture-own-login');if(refused)throw Error('fixture lost reply');},
  connectedAccounts:()=>refreshed++,document:{getElementById:id=>elements[id]}});
 vm.runInContext(page.slice(start,end),context);
 await elements['max-cancel'].onclick();
 assert.equal(stopped,1);assert.equal(context.maxLogin,'');
 assert.equal(refreshed,refused?1:0);
 assert.equal(elements['max-status'].textContent.includes('Вход отменён.'),!refused);
}
console.log('MAX cancellation confirmation/lost-response: PASS');
