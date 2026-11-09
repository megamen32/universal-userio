import test from 'node:test';import assert from 'node:assert/strict';import {createHash} from 'node:crypto';
import {createExtensionCaller} from './dot-extension-agent.mjs';
const init={base:'http://127.0.0.1:18093',agentId:'browser',url:'https://chatgpt.com/dots/a~b',room:'room',token:'fixture',wait:async()=>{},now:()=>100000,preserveDraft:false,getAgentStatus:async()=>({agents:[]})};
const instanceA='browser-11111111-1111-4111-8111-111111111111',instanceB='browser-22222222-2222-4222-8222-222222222222';
function conflictChannel({matches=[instanceA],legacy=false,unverified=[]}={}){
 const posts=[],results=[];
 const fetcher=async(url,opts)=>({ok:true,json:async()=>{
  if(url.pathname==='/v1/agent/status')return {agents:[{agent_id:'browser',identity_conflict:true,instances:[instanceA,instanceB],legacy_unaddressable:legacy}]};
  if(opts.method==='POST'){
   const body=JSON.parse(opts.body),id=body.agent_id+':'+(posts.length+1);posts.push(body);
   const isProbe=body.action==='dot_profile_probe';
   const result=isProbe?(unverified.includes(body.agent_id)?{ok:false,error:'dot_probe_unverified'}:{ok:true,matches:matches.includes(body.agent_id)}):{ok:true};
   results.push({id,action:body.action,received_at:101,result});return {queued:true,id};
  }
  return {results};
 }});
 return {fetcher,posts};
}
test('conflicting legacy profiles route a Dot send only to the profile holding the room',async()=>{
 const channel=conflictChannel(),call=createExtensionCaller({...init,fetcher:channel.fetcher,getAgentStatus:undefined});
 assert.equal((await call('dot_send',{text:'hello'})).ok,true);
 const probes=channel.posts.filter(x=>x.action==='dot_profile_probe');
 assert.deepEqual(probes.map(x=>x.agent_id).sort(),[instanceA,instanceB]);
 const sends=channel.posts.filter(x=>x.action==='dot_action'&&x.args.operation==='send');
 assert.equal(sends.length,1);assert.equal(sends[0].agent_id,instanceA);
});
test('conflicting legacy profiles do not send if room ownership is ambiguous',async()=>{
 const channel=conflictChannel({matches:[instanceA,instanceB]}),call=createExtensionCaller({...init,fetcher:channel.fetcher,getAgentStatus:undefined});
 await assert.rejects(call('dot_send',{text:'hello'}),/identity_conflict/);
 assert.equal(channel.posts.filter(x=>x.args.operation==='send').length,0);
});
test('an active legacy profile that cannot be probed prevents guessing the target',async()=>{
 const channel=conflictChannel({legacy:true}),call=createExtensionCaller({...init,fetcher:channel.fetcher,getAgentStatus:undefined});
 await assert.rejects(call('dot_send',{text:'hello'}),/identity_conflict/);
 assert.equal(channel.posts.filter(x=>x.args.operation==='send').length,0);
});
test('an unverified profile prevents a send even when another profile matches',async()=>{
 const channel=conflictChannel({unverified:[instanceB]}),call=createExtensionCaller({...init,fetcher:channel.fetcher,getAgentStatus:undefined});
 await assert.rejects(call('dot_send',{text:'hello'}),/identity_conflict/);
 assert.equal(channel.posts.filter(x=>x.args.operation==='send').length,0);
});
test('draft preservation and send stay pinned to the same verified profile',async()=>{
 const channel=conflictChannel(),call=createExtensionCaller({...init,fetcher:channel.fetcher,getAgentStatus:undefined,preserveDraft:true,transferId:()=> '12345678-1234-1234-1234-123456789abc'});
 assert.equal((await call('dot_send',{text:'hello'})).ok,true);
 const actions=channel.posts.filter(x=>x.action!=='dot_profile_probe');
 assert.deepEqual(actions.map(x=>x.action),['dot_draft_backup','dot_draft_recover','dot_action','dot_draft_recover']);
 assert.ok(actions.every(x=>x.agent_id===instanceA));
});
test('disable during draft backup blocks the subsequent send admission',async()=>{
 let enabled=true,last;const posts=[];
 const call=createExtensionCaller({...init,preserveDraft:true,assertAllowed:()=>{if(!enabled)throw Error('disabled');},fetcher:async(_url,opts)=>({ok:true,json:async()=>{
  if(opts.method==='POST'){last=JSON.parse(opts.body);posts.push(last);return {queued:true,id:'browser:'+posts.length};}
  enabled=false;return {results:[{id:'browser:'+posts.length,action:last.action,received_at:101,result:{ok:true}}]};
 }})});
 await assert.rejects(call('dot_send',{text:'hello'}),/disabled/);
 assert.equal(posts.some(p=>p.action==='dot_action'&&p.args.operation==='send'),false);
});
test('preserves an existing draft around exactly one send',async()=>{
 const posts=[];let last;
 const caller=createExtensionCaller({...init,preserveDraft:true,transferId:()=> '12345678-1234-1234-1234-123456789abc',fetcher:async(_url,opts)=>({ok:true,json:async()=>{
  if(opts.method==='POST'){last=JSON.parse(opts.body);posts.push(last);return {queued:true,id:'browser:'+posts.length};}
  return {results:[{id:'browser:'+posts.length,action:last.action,received_at:101,result:{ok:true,status:'submitted_unconfirmed'}}]};
 }})});
 assert.equal((await caller('dot_send',{text:'hello'})).ok,true);
 assert.deepEqual(posts.map(p=>[p.action,p.args.operation||'']),[['dot_draft_backup',''],['dot_draft_recover','clear'],['dot_action','send'],['dot_draft_recover','restore']]);
});
test('does not restore or replay after an unknown send outcome',async()=>{
 const posts=[];let last;
 const caller=createExtensionCaller({...init,preserveDraft:true,transferId:()=> '12345678-1234-1234-1234-123456789abc',fetcher:async(_url,opts)=>({ok:true,json:async()=>{
  if(opts.method==='POST'){last=JSON.parse(opts.body);posts.push(last);return {queued:true,id:'browser:'+posts.length};}
  return {results:last.action==='dot_action'?[]:[{id:'browser:'+posts.length,action:last.action,received_at:101,result:{ok:true}}]};
 }})});
 await assert.rejects(caller('dot_send',{text:'hello'}),/extension_result_unknown/);
 assert.deepEqual(posts.map(p=>p.action),['dot_draft_backup','dot_draft_recover','dot_action']);
});
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
