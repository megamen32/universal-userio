import './dot-test-policy.mjs';
import test from 'node:test';import assert from 'node:assert/strict';
import {createPrivateDotCaller} from './dot-private-extension.mjs';
test('private extension transport never calls CDP and retains durable relay identity',async()=>{
 let cdp=0,loads=0;const binding={transport:'extension',agentId:'mini-userio',pageId:18};
 const call=createPrivateDotCaller({binding,legacyCall:()=>{cdp++;throw Error('CDP forbidden')},loadExtension:()=>{loads++;return async(action,args)=>({ok:true,action,after:args.after})}});
 assert.deepEqual(await call('dot_snapshot',{tab_id:18,after:'cursor'}),{ok:true,action:'dot_snapshot',after:'cursor'});
 await call('dot_snapshot',{tab_id:18});assert.equal(loads,1);assert.equal(cdp,0);
 await assert.rejects(()=>call('dot_snapshot',{tab_id:19}),/binding/);
});
test('configured extension failures never fall back to CDP',async()=>{
 let cdp=0;const call=createPrivateDotCaller({binding:{transport:'extension',agentId:'mini',pageId:18},legacyCall:()=>cdp++,loadExtension:()=>{throw Error('credential unavailable')}});
 await assert.rejects(()=>call('dot_snapshot',{tab_id:18}),/credential unavailable/);assert.equal(cdp,0);
});
test('existing CDP bindings retain their behavior until explicitly switched',async()=>{
 const call=createPrivateDotCaller({binding:{pageId:18},legacyCall:async()=>({ok:true}),loadExtension:()=>{throw Error('unexpected')}});
 assert.deepEqual(await call('dot_snapshot',{tab_id:18}),{ok:true});
});
