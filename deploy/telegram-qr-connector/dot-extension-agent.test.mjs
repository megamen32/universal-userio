import test from 'node:test';import assert from 'node:assert/strict';import {createHash} from 'node:crypto';
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
test('extension transport carries formatted links with the original request',async()=>{
 let payload;const entities=[{type:'text_url',offset:0,length:4,url:'https://example.org/'}];
 const call=createExtensionCaller({...init,fetcher:async(url,opts)=>({ok:true,json:async()=>opts.method==='POST'?(payload=JSON.parse(opts.body),{queued:true,id:'browser:3'}):{results:[{id:'browser:3',action:'dot_action',received_at:101,result:{ok:true}}]}})});
 assert.equal((await call('dot_send',{text:'сайт',entities})).ok,true);assert.deepEqual(payload.args.entities,entities);
});
test('media is sent once over the existing extension channel in bounded chunks',async()=>{
 const posts=[];let last;
 const call=createExtensionCaller({...init,loadMedia:()=>[{filename:'test.png',data:Buffer.from('abcd').toString('base64')}],transferId:()=> '12345678-1234-1234-1234-123456789abc',fetcher:async(url,opts)=>({ok:true,json:async()=>{
  if(opts.method==='POST'){last=JSON.parse(opts.body);posts.push(last);return {queued:true,id:'browser:'+posts.length};}
  return {results:[{id:'browser:'+posts.length,action:last.action,received_at:101,result:{ok:true,ready:true,...(last.args.operation==='chunk'?{index:0,received:4}:{})}}]};
 }})});
 const result=await call('dot_send',{text:'Фото',media:[{filename:'test.png',mime:'image/png',size:4,sha256:'a'.repeat(64)}]});
 assert.equal(result.ok,true);assert.deepEqual(posts.map(x=>x.args.operation),['start','chunk','upload','inspect','inspect','send','finish']);
 assert.equal(posts.filter(x=>x.args.operation==='send').length,1);
});
test('a Dot image is read through the same extension channel and verified by digest',async()=>{
 const bytes=Buffer.from([137,80,78,71,13,10,26,10,1]),sha256=createHash('sha256').update(bytes).digest('hex'),fp='a'.repeat(64),room='a'.repeat(32),source_id=room+'~'+room+'~CalpicoMessage~id';let last,count=0;
 const call=createExtensionCaller({...init,room,fetcher:async(_url,opts)=>({ok:true,json:async()=>{
  if(opts.method==='POST'){last=JSON.parse(opts.body);count++;return {queued:true,id:'browser:'+count};}
  const op=last.args.operation,base={ok:true};
  const result=op==='start'?{...base,nonce:'12345678-1234-1234-1234-123456789abc',count:1,items:[{size:bytes.length,sha256,fingerprint:fp}]}:op==='chunk'?{...base,nonce:last.args.nonce,index:0,offset:0,size:bytes.length,sha256,data:bytes.toString('base64')}:base;
  return {results:[{id:'browser:'+count,action:last.action,received_at:101,result}]};
 }})});
 const files=await call('dot_media_read',{source_id,expected_fingerprints:[fp]});assert.equal(files.length,1);assert.deepEqual(files[0].bytes,bytes);assert.equal(count,3);
});
test('media reply carries its exact target and formatted caption through staging',async()=>{
 const posts=[];let last;const reply='room~room~CalpicoMessage~target',entities=[{type:'bold',offset:0,length:4}];
 const call=createExtensionCaller({...init,loadMedia:()=>[{filename:'test.png',data:Buffer.from('abcd').toString('base64')}],transferId:()=> '12345678-1234-1234-1234-123456789abc',fetcher:async(_url,opts)=>({ok:true,json:async()=>{
  if(opts.method==='POST'){last=JSON.parse(opts.body);posts.push(last);return {queued:true,id:'browser:'+posts.length};}
  return {results:[{id:'browser:'+posts.length,action:last.action,received_at:101,result:{ok:true,ready:true,verified_reply_to:reply,...(last.args.operation==='chunk'?{index:0,received:4}:{})}}]};
 }})});
 assert.equal((await call('dot_send',{text:'тест',reply_to:reply,entities,media:[{filename:'test.png',mime:'image/png',size:4,sha256:'a'.repeat(64)}]})).ok,true);
 assert.equal(posts.find(x=>x.args.operation==='start').args.reply_to,reply);assert.deepEqual(posts.find(x=>x.args.operation==='start').args.entities,entities);
});
