import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
import {webcrypto,createHash} from 'node:crypto';
import {parseEvaluation} from './dot-browser-mcp.mjs';
import {dotMediaPageAction,readDotMediaThroughBrowser,readDotMediaUsing} from './dot-browser-egress.mjs';
import {pageAction} from './dot-browser-page.mjs';

const room='a'.repeat(32),url='https://chatgpt.com/dots/11111111-1111-4111-8111-111111111111~bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
const id=room+'~'+room+'~CalpicoMessage~photo1',remote='https://chatgpt.com/backend-api/files/synthetic-image.png';
const file=Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]),Buffer.alloc(350*1024,23)]);
const fingerprint=s=>createHash('sha256').update(s).digest('hex');
const pack=x=>JSON.stringify({result:{content:[{type:'text',text:String.fromCharCode(96).repeat(3)+'json\n'+JSON.stringify(x)+'\n'+String.fromCharCode(96).repeat(3)}]}});
test('bounded document read preserves its name and verifies bytes',async()=>{
 const bytes=Buffer.from('%PDF-fixture'),sha256=createHash('sha256').update(bytes).digest('hex'),fp='a'.repeat(64),nonce='12345678-1234-1234-1234-123456789abc';
 const evaluate=async(action,args)=>action==='start'?{ok:true,nonce,count:1,items:[{kind:'document',filename:'договор.pdf',mime:'application/pdf',size:bytes.length,sha256,fingerprint:fp}]}:action==='chunk'?{ok:true,nonce,index:0,offset:0,size:bytes.length,sha256,data:bytes.toString('base64')}:{ok:true};
 const result=await readDotMediaUsing(evaluate,{source_id:id,expected_fingerprints:[fp]},room);
 assert.equal(result[0].filename,'договор.pdf');assert.equal(result[0].kind,'document');assert.deepEqual(result[0].bytes,bytes);
});
test('extension and existing browser use the same fixed media reader',()=>{
 const self={};vm.runInNewContext(fs.readFileSync(new URL('../../extensions/vk-inbox/lib/dot-media-read.js',import.meta.url),'utf8'),{self});
 assert.equal(self.UserIODotRead.pageAction.toString(),dotMediaPageAction.toString());
});
function fixture(options={}){
 const image={currentSrc:options.src??remote,alt:'synthetic.png'};
 const row={dataset:{messageId:id},closest:()=>null,classList:{contains:()=>false},
  querySelector:s=>s==='.message-text'?{innerText:options.text||''}:null,
  querySelectorAll:s=>s==='img.attachment-image'?[image]:[]};
 let fetchCount=0, calls=[],chunkCount=0;
 const ctx=vm.createContext({location:{href:options.location??url,origin:'https://chatgpt.com'},
  document:{querySelectorAll:s=>s.startsWith('article')?[row]:[]},
  crypto:webcrypto,TextEncoder,URL,Uint8Array,Date,btoa,
  fetch:async src=>{fetchCount++;if(options.fetchError)throw Error('private fetch failure');if(src!==image.currentSrc)throw Error('mismatched requested source');return new Response(file,{status:200});}});
 const call=async(name,args)=>{
  assert.equal(name,'evaluate_script');calls.push(args);
  const result=await vm.runInContext('('+args.function+')()',ctx);
  if(args.function.includes('"chunk"')){chunkCount++;if(options.mutateDuringRead&&chunkCount===1)image.currentSrc='https://chatgpt.com/backend-api/files/changed.png';}
  return pack(result);
 };
 return {image,row,ctx,call,calls,get fetchCount(){return fetchCount},get chunkCount(){return chunkCount}};
}
test('existing page exposes image count and hashed identity without URL leakage',async()=>{
 const x=fixture();
 const page=vm.runInContext('('+pageAction.toString()+')',x.ctx);
 const result=await page(url,room,null,null);
 assert.equal(result.ok,true);
 assert.equal(result.messages.length,1);
 assert.equal(result.messages[0].attachment_count,1);
 assert.deepEqual([...result.messages[0].attachment_fingerprints],[fingerprint(remote)]);
 assert.equal(JSON.stringify(result).includes(remote),false);
});
test('read-only browser extraction chunks an image and wipes temporary cache',async()=>{
 const x=fixture();
 const media=await readDotMediaThroughBrowser(x.call,18,{source_id:id,expected_fingerprints:[fingerprint(remote)]},parseEvaluation,url,room);
 assert.equal(x.fetchCount,1);
 assert.ok(x.chunkCount>=3);
 assert.equal(media.length,1);
 assert.ok(media[0].bytes.equals(file));
 assert.equal(media[0].sha256,createHash('sha256').update(file).digest('hex'));
 assert.equal(media[0].mime,'image/png');
 assert.match(media[0].filename,/^dot-1-[a-f0-9]{16}\.png$/);
 assert.equal(Object.hasOwn(x.ctx,'__userioDotMediaReadOnly'),false);
 assert.ok(x.calls.every(v=>v.pageId===18));
});
test('cross-origin media is blocked without fetch or network activity',async()=>{
 const x=fixture({src:'https://evil.invalid/private.png'});
 await assert.rejects(
  readDotMediaThroughBrowser(x.call,18,{source_id:id,expected_fingerprints:[fingerprint(x.image.currentSrc)]},parseEvaluation,url,room),
  /dot_media_source_requires_review/);
 assert.equal(x.fetchCount,0);
});
test('changing source while reading prevents delivery, clears temporary page cache',async()=>{
 const x=fixture({mutateDuringRead:true});
 await assert.rejects(
  readDotMediaThroughBrowser(x.call,18,{source_id:id,expected_fingerprints:[fingerprint(remote)]},parseEvaluation,url,room),
  /dot_media_chunk_unverified/);
 assert.equal(Object.hasOwn(x.ctx,'__userioDotMediaReadOnly'),false);
});
test('wrong room, tab, fingerprint and URL fail before media reaches provider',async()=>{
 const x=fixture();
 await assert.rejects(readDotMediaThroughBrowser(x.call,0,{source_id:id,expected_fingerprints:[fingerprint(remote)]},parseEvaluation,url,room),/invalid_dot_media_read/);
 await assert.rejects(readDotMediaThroughBrowser(x.call,18,{source_id:'other',expected_fingerprints:[fingerprint(remote)]},parseEvaluation,url,room),/invalid_dot_media_read/);
 await assert.rejects(readDotMediaThroughBrowser(x.call,18,{source_id:id,expected_fingerprints:['0'.repeat(64)]},parseEvaluation,url,room),/dot_media_source_requires_review/);
 x.ctx.location.href='https://chatgpt.com/dots/wrong';
 await assert.rejects(readDotMediaThroughBrowser(x.call,18,{source_id:id,expected_fingerprints:[fingerprint(remote)]},parseEvaluation,url,room),/dot_media_source_requires_review/);
 assert.equal(x.fetchCount,0);
});
