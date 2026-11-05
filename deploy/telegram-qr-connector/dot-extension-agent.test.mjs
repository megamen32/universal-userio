import test from 'node:test';import assert from 'node:assert/strict';
import {createExtensionCaller} from './dot-extension-agent.mjs';
const init={base:'http://127.0.0.1:18093',agentId:'browser',url:'https://chatgpt.com/dots/a~b',room:'room',token:'fixture',wait:async()=>{},now:()=>100000};
test('extension caller matches command receipt and refuses stale result',async()=>{
 let posts=0;let reads=0;
 const call=createExtensionCaller({...init,fetcher:async(url,opts)=>({ok:true,json:async()=>opts.method==='POST'?(posts++,{queued:true,id:'browser:1'}):(reads++,{results:[{id:'browser:1',action:'dot_action',received_at:reads===1?99:101,result:{ok:true,room:'room'}}]})})});
 assert.equal((await call('dot_snapshot',{})).room,'room');assert.equal(posts,1);assert.equal(reads,2);
});
test('uncertain send is not replayed on result timeout',async()=>{
 let posts=0;const call=createExtensionCaller({...init,fetcher:async(url,opts)=>({ok:true,json:async()=>opts.method==='POST'?(posts++,{queued:true,id:'browser:2'}):{results:[]}})});
 await assert.rejects(call('dot_send',{text:'hello'}),/extension_result_unknown/);assert.equal(posts,1);
});
