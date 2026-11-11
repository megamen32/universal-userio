import './dot-test-policy.mjs';
import test from 'node:test';import assert from 'node:assert/strict';import {mkdtempSync,writeFileSync,chmodSync,symlinkSync} from 'node:fs';import {tmpdir} from 'node:os';import {join} from 'node:path';import {readDotBinding,isDedicatedDotBot,reserveDotBotBinding} from './dot-binding.mjs';
test('binding failclosed missing permissions owner and symlink',()=>{const d=mkdtempSync(join(tmpdir(),'dotbinding-'));const p=join(d,'binding');assert.equal(readDotBinding(p),null);const b={enabled:true,owner:'111111111',room:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',botAccountId:'telegram:999999',pageId:7};writeFileSync(p,JSON.stringify(b),{mode:0o600});assert.deepEqual(readDotBinding(p),b);chmodSync(p,0o644);assert.throws(()=>readDotBinding(p));chmodSync(p,0o600);writeFileSync(p,JSON.stringify({...b,owner:'1'}));assert.throws(()=>readDotBinding(p));symlinkSync(p,join(d,'link'));assert.throws(()=>readDotBinding(join(d,'link')));});

test('only dedicated new dot bot bypasses generic delivery',()=>{
 const b={enabled:false,botAccountId:'telegram:999999'};
 assert.equal(isDedicatedDotBot('telegram:999999',b),true);
 for(const account of ['telegram:111111111','telegram:222222222','telegram:123456'])assert.equal(isDedicatedDotBot(account,b),false);
 assert.equal(isDedicatedDotBot('telegram:999999',null),false);
});

test('enrollment reserves only disabled exact bot, no overwrite',()=>{
 const p=join(mkdtempSync(join(tmpdir(),'dotreserve-')),'binding');
 const b=reserveDotBotBinding('telegram:999999',p);
 assert.equal(b.enabled,false);assert.equal(readDotBinding(p),null);
 assert.equal(readDotBinding(p,true).botAccountId,'telegram:999999');
 assert.deepEqual(reserveDotBotBinding('telegram:999999',p),b);
 assert.throws(()=>reserveDotBotBinding('telegram:222222',p));
});

test('extension binding requires an explicit valid agent and rejects unknown transports',()=>{
 const p=join(mkdtempSync(join(tmpdir(),'dotextension-')),'binding');
 const b={enabled:true,owner:'111111111',room:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',botAccountId:'telegram:999999',pageId:7,transport:'extension',agentId:'mac-mini-dot',connectionId:'airlock',accountScope:'a'.repeat(64)};
 writeFileSync(p,JSON.stringify(b),{mode:0o600});assert.equal(readDotBinding(p).transport,'extension');
 for(const patch of [{agentId:''},{agentId:'../other'},{transport:'unknown'}]){writeFileSync(p,JSON.stringify({...b,...patch}));assert.throws(()=>readDotBinding(p));}
});
