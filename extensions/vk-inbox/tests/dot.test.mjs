import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
function setup(tabs){
 let calls=[];const self={UserIODot:{pageAction:()=>{},mediaAction:()=>{}}};
 const chrome={tabs:{query:async()=>tabs},scripting:{executeScript:async x=>(calls.push(x),[{result:{ok:true,room:'r'}}])}};
 const src=fs.readFileSync(path.join(root,'lib/agent.js'),'utf8').replace('  root.Agent = lib;','  lib.testExecute = execute; root.Agent = lib;');
 vm.runInNewContext(src,{self,chrome,setTimeout,Promise});return {execute:self.Agent.testExecute,calls};
}
const url='https://chatgpt.com/dots/aaa~bbb';
test('dot actions require one exact tab and never navigate another tab',async()=>{
 for(const tabs of [[],[{id:1,url},{id:2,url}]]){const x=setup(tabs);const r=await x.execute({action:'dot_action',args:{url,room:'r',operation:'snapshot'}});assert.equal(r.error,'dot_tab_not_unique');assert.equal(x.calls.length,0)}
 const x=setup([{id:1,url}]);assert.equal((await x.execute({action:'dot_action',args:{url,room:'r',operation:'snapshot'}})).ok,true);assert.equal(x.calls[0].target.tabId,1);assert.equal(x.calls[0].args[2],null);
});
test('dot send validates payload before injection',async()=>{
 const x=setup([{id:1,url}]);for(const text of ['',null,'x'.repeat(16001)])assert.equal((await x.execute({action:'dot_action',args:{url,room:'r',operation:'send',text}})).ok,false);
 assert.equal(x.calls.length,0);assert.equal((await x.execute({action:'dot_action',args:{url,room:'r',operation:'send',text:'hi'}})).ok,true);assert.equal(x.calls.length,1);
});
test('dot action forwards formatting to the selected tab',async()=>{
 const x=setup([{id:1,url}]);const entities=[{type:'text_url',offset:0,length:4,url:'https://example.org/'}];
 assert.equal((await x.execute({action:'dot_action',args:{url,room:'r',operation:'send',text:'сайт',entities}})).ok,true);
 assert.deepEqual(JSON.parse(JSON.stringify(x.calls[0].args[5])),entities);
});
test('media commands require the exact Dot tab and use the fixed media function',async()=>{
 const x=setup([{id:1,url}]);const r=await x.execute({action:'dot_media',args:{url,room:'r',operation:'start',transfer_id:'12345678-1234-1234-1234-123456789abc',files:[]}});
 assert.equal(r.ok,true);assert.equal(x.calls[0].target.tabId,1);assert.equal(x.calls[0].args[2],'start');
});
test('backgrounded Dot tab retains message text for durable echo matching',async()=>{
 const room='fixture';const row={dataset:{messageId:room+'~'+room+'~CalpicoMessage~id'},closest:()=>null,classList:{contains:()=>true},querySelector:s=>s==='.message-text'?{innerText:'',textContent:'expected message'}:null,querySelectorAll:()=>[]};
 const self={};const document={querySelectorAll:s=>s.startsWith('article')?[row]:[],querySelector:()=>null};const location={href:url};
 vm.runInNewContext(fs.readFileSync(path.join(root,'lib/dot.js'),'utf8'),{self,document,location,URL,TextEncoder,crypto:globalThis.crypto});
 const result=await self.UserIODot.pageAction(url,room,null,null);assert.equal(result.messages[0].text,'expected message');
});
test('Dot snapshot retains the target of a labelled link',async()=>{
 const room='fixture';
 const anchor={tagName:'A',textContent:'сайт',getAttribute:n=>n==='href'?'https://example.org/path':null,childNodes:[{nodeType:3,nodeValue:'сайт'}]};
 const message={innerText:'Откройте сайт',textContent:'Откройте сайт',childNodes:[{nodeType:3,nodeValue:'Откройте '},anchor],querySelectorAll:()=>[anchor]};
 const row={dataset:{messageId:room+'~'+room+'~CalpicoMessage~id'},closest:()=>null,classList:{contains:()=>false},querySelector:s=>s==='.message-text'?message:null,querySelectorAll:()=>[]};
 const self={};const document={querySelectorAll:s=>s.startsWith('article')?[row]:[],querySelector:()=>null};const location={href:url};
 vm.runInNewContext(fs.readFileSync(path.join(root,'lib/dot.js'),'utf8'),{self,document,location,URL,TextEncoder,crypto:globalThis.crypto});
 const result=await self.UserIODot.pageAction(url,room,null,null);
 assert.deepEqual(JSON.parse(JSON.stringify(result.messages[0].entities)),[{type:'text_url',offset:9,length:4,url:'https://example.org/path'}]);
});
test('Dot snapshot preserves a link after a rendered paragraph break',async()=>{
 const room='fixture';const a={tagName:'A',getAttribute:n=>n==='href'?'https://example.org/':null,childNodes:[{nodeType:3,nodeValue:'сайт'}]};
 const content={innerText:'Первый абзац\nОткройте сайт',textContent:'Первый абзацОткройте сайт',childNodes:[{nodeType:3,nodeValue:'Первый абзацОткройте '},a]};
 const row={dataset:{messageId:room+'~'+room+'~CalpicoMessage~id'},closest:()=>null,classList:{contains:()=>false},querySelector:s=>s==='.message-text'?content:null,querySelectorAll:()=>[]};
 const self={};const document={querySelectorAll:s=>s.startsWith('article')?[row]:[],querySelector:()=>null};
 vm.runInNewContext(fs.readFileSync(path.join(root,'lib/dot.js'),'utf8'),{self,document,location:{href:url},URL,TextEncoder,crypto:globalThis.crypto});
 const result=await self.UserIODot.pageAction(url,room,null,null);
 assert.deepEqual(JSON.parse(JSON.stringify(result.messages[0].entities)),[{type:'text_url',offset:22,length:4,url:'https://example.org/'}]);
});
test('Dot composer receives a formatted hyperlink without interpreting input as raw HTML',async()=>{
 const room='fixture';let inserted='',clicks=0;
 const row={dataset:{messageId:room+'~'+room+'~CalpicoMessage~id'},closest:()=>null,classList:{contains:()=>true},querySelector:s=>s==='.message-text'?{innerText:'old'}:null,querySelectorAll:()=>[]};
 const editor={innerText:'',isConnected:true,getClientRects:()=>[1],focus(){},closest:()=>null,querySelectorAll:s=>s==='a'?[{getAttribute:()=> 'https://example.org/'}]:[]};
 const button={getClientRects:()=>[1],getAttribute:()=> 'Send',disabled:false,click:()=>{clicks++;editor.innerText='';}};
 const document={querySelector:()=>null,querySelectorAll:s=>s.startsWith('article')?[row]:s.startsWith('[contenteditable')?[editor]:s==='button'?[button]:[],execCommand:(kind,_ui,value)=>{inserted=value;editor.innerText='сайт';return kind==='insertHTML';}};
 const self={};vm.runInNewContext(fs.readFileSync(path.join(root,'lib/dot.js'),'utf8'),{self,document,location:{href:url},URL,TextEncoder,crypto:globalThis.crypto,setTimeout:(fn)=>fn()});
 const result=await self.UserIODot.pageAction(url,room,'сайт',null,null,[{type:'text_url',offset:0,length:4,url:'https://example.org/'}]);
 assert.equal(result.ok,true);assert.match(inserted,/<a\s+href="https:\/\/example\.org\/">сайт<\/a>/);assert.equal(clicks,1);
});
test('Dot extension stages media chunks only for the exact room and expected offset',async()=>{
 const room='fixture',id=room+'~'+room+'~CalpicoMessage~id';
 const row={dataset:{messageId:id},closest:()=>null};
 const document={querySelectorAll:s=>s.startsWith('article')?[row]:[],querySelector:()=>null};
 const self={};const context={self,document,location:{href:url},URL,TextEncoder,Uint8Array,crypto:globalThis.crypto,atob,Date};
 vm.runInNewContext(fs.readFileSync(path.join(root,'lib/dot.js'),'utf8'),context);
 const args={transfer_id:'12345678-1234-1234-1234-123456789abc',files:[{filename:'test.png',mime:'image/png',size:4,sha256:'a'.repeat(64)}]};
 assert.equal((await self.UserIODot.mediaAction(url,room,'start',args)).ok,true);
 assert.equal((await self.UserIODot.mediaAction(url,room,'chunk',{transfer_id:args.transfer_id,index:0,offset:1,data:'AQIDBA=='})).error,'invalid_media_offset');
 assert.equal((await self.UserIODot.mediaAction(url,room,'chunk',{transfer_id:args.transfer_id,index:0,offset:0,data:'AQIDBA=='})).ok,true);
 assert.equal((await self.UserIODot.mediaAction(url,'other','chunk',{transfer_id:args.transfer_id,index:0,offset:0,data:'AQIDBA=='})).error,'wrong_or_unloaded_room');
});
test('staged image is verified and delivered to the current Dot file input',async()=>{
 const room='fixture',row={dataset:{messageId:room+'~'+room+'~CalpicoMessage~id'},closest:()=>null};let changed=0;
 const input={files:[],dispatchEvent:()=>changed++};
 const composer={querySelectorAll:s=>s==='input[type="file"]'?[input]:[],querySelector:()=>null};
 const editor={innerText:'',getClientRects:()=>[1],closest:()=>composer};
 const document={querySelector:()=>null,querySelectorAll:s=>s.startsWith('article')?[row]:s.startsWith('[contenteditable')?[editor]:[]};
 class Transfer{constructor(){this.files=[];this.items={add:f=>this.files.push(f)}}}
 class FakeFile{constructor(parts,name,options){this.name=name;this.type=options.type;this.parts=parts}}
 const self={};const context={self,document,location:{href:url},URL,TextEncoder,Uint8Array,crypto:globalThis.crypto,atob,Date,DataTransfer:Transfer,File:FakeFile,Event:class{}};
 vm.runInNewContext(fs.readFileSync(path.join(root,'lib/dot.js'),'utf8'),context);
 const bytes=new Uint8Array([1,2,3,4]);const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(b=>b.toString(16).padStart(2,'0')).join('');
 const transfer_id='12345678-1234-1234-1234-123456789abc';
 await self.UserIODot.mediaAction(url,room,'start',{transfer_id,files:[{filename:'test.png',mime:'image/png',size:4,sha256:hash}]});
 await self.UserIODot.mediaAction(url,room,'chunk',{transfer_id,index:0,offset:0,data:'AQIDBA=='});
 const result=await self.UserIODot.mediaAction(url,room,'upload',{transfer_id});
 assert.equal(result.ok,true);assert.equal(input.files[0].name,'test.png');assert.equal(changed,1);
});
test('media send only clicks when the exact upload is ready and never clicks twice',async()=>{
 const room='fixture',row={dataset:{messageId:room+'~'+room+'~CalpicoMessage~id'},closest:()=>null};let clicks=0;
 const input={files:[],dispatchEvent:()=>{}},preview={getClientRects:()=>[1]};
 const composer={outerHTML:'test.png',innerText:'',querySelectorAll:s=>s==='input[type="file"]'?[input]:s==='img'?(input.files.length?[preview]:[]):[],querySelector:()=>null};
 const editor={innerText:'',getClientRects:()=>[1],closest:()=>composer,focus:()=>{}};
 const button={getClientRects:()=>[1],getAttribute:()=> 'Send',disabled:false,click:()=>clicks++};
 const document={querySelector:()=>null,querySelectorAll:s=>s.startsWith('article')?[row]:s.startsWith('[contenteditable')?[editor]:s==='button'?[button]:[],execCommand:(_a,_b,v)=>(editor.innerText=v,true)};
 class Transfer{constructor(){this.files=[];this.items={add:f=>this.files.push(f)}}};class FakeFile{constructor(_parts,name){this.name=name}}
 const self={};const context={self,document,location:{href:url},URL,TextEncoder,Uint8Array,crypto:globalThis.crypto,atob,Date,DataTransfer:Transfer,File:FakeFile,Event:class{},setTimeout:fn=>fn()};
 vm.runInNewContext(fs.readFileSync(path.join(root,'lib/dot.js'),'utf8'),context);
 const bytes=new Uint8Array([1,2,3,4]),hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(b=>b.toString(16).padStart(2,'0')).join(''),transfer_id='12345678-1234-1234-1234-123456789abc';
 await self.UserIODot.mediaAction(url,room,'start',{transfer_id,files:[{filename:'test.png',mime:'image/png',size:4,sha256:hash}]});
 await self.UserIODot.mediaAction(url,room,'chunk',{transfer_id,index:0,offset:0,data:'AQIDBA=='});await self.UserIODot.mediaAction(url,room,'upload',{transfer_id});
 assert.equal((await self.UserIODot.mediaAction(url,room,'send',{transfer_id,text:'Фото'})).ok,true);
 assert.equal((await self.UserIODot.mediaAction(url,room,'send',{transfer_id,text:'Фото'})).error,'media_send_already_started');assert.equal(clicks,1);
});
