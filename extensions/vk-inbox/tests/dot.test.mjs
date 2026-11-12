import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {webcrypto,createHash} from 'node:crypto';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
function setup(tabs,snapshot={ok:true,room:'r',cursor:'verified'}){
 let calls=[];const self={UserIODot:{pageAction:()=>{},mediaAction:()=>{},inspectDraft:()=>{}},UserIODotRead:{pageAction:()=>{}}};
 const chrome={tabs:{query:async()=>tabs},scripting:{executeScript:async x=>(calls.push(x),[{result:snapshot}])}};
 const src=fs.readFileSync(path.join(root,'lib/agent.js'),'utf8').replace('  root.Agent = lib;','  lib.testExecute = execute; root.Agent = lib;');
 vm.runInNewContext(src,{self,chrome,setTimeout,Promise});return {execute:self.Agent.testExecute,calls};
}
const url='https://chatgpt.com/dots/aaa~bbb';
test('profile probe confirms the exact Dot room without exposing its messages',async()=>{
 const absent=setup([]),none=await absent.execute({action:'dot_profile_probe',args:{url,room:'r'}});
 assert.equal(none.ok,true);assert.equal(none.matches,false);assert.equal(absent.calls.length,0);
 const one=setup([{id:7,url}]),result=await one.execute({action:'dot_profile_probe',args:{url,room:'r'}});
 assert.equal(result.ok,true);assert.equal(result.matches,true);assert.equal(one.calls.length,1);
 assert.deepEqual(Object.keys(result).sort(),['matches','ok']);
 const duplicate=setup([{id:1,url},{id:2,url}]);
 assert.equal((await duplicate.execute({action:'dot_profile_probe',args:{url,room:'r'}})).error,'dot_tab_not_unique');
 assert.equal(duplicate.calls.length,0);
 const wrong=setup([{id:1,url}],{ok:true,room:'other',cursor:'verified'});
 assert.equal((await wrong.execute({action:'dot_profile_probe',args:{url,room:'r'}})).error,'dot_probe_unverified');
});
test('draft backup stores sensitive content locally and returns only verification metadata',async()=>{
 const draft='private draft text',saved={};
 const self={UserIODot:{captureDraft:()=>{}}};
 const chrome={tabs:{query:async()=>[{id:7,url}]},scripting:{executeScript:async()=>[{result:{ok:true,text:draft,html:'<p>'+draft+'</p>',hash:'a'.repeat(64)}}]},storage:{local:{get:async key=>key===null?{...saved}:({[key]:saved[key]}),set:async obj=>Object.assign(saved,obj)}}};
 const src=fs.readFileSync(path.join(root,'lib/agent.js'),'utf8').replace('  root.Agent = lib;','  lib.testExecute = execute; root.Agent = lib;');
 vm.runInNewContext(src,{self,chrome,setTimeout,Promise,JSON});
 const args={url,room:'r',backup_id:'12345678-1234-1234-1234-123456789abc'};
 const result=await self.Agent.testExecute({action:'dot_draft_backup',args});
 assert.equal(result.ok,true);assert.ok(!JSON.stringify(result).includes(draft));
 assert.equal(Object.values(saved)[0].text,draft);
 const settings=await self.Agent.testExecute({action:'settings_get',args:{}});
 assert.ok(!JSON.stringify(settings).includes(draft));
 assert.equal((await self.Agent.testExecute({action:'settings_set',args:{patch:{['dot_draft_backup:'+args.backup_id]:null}}})).error,'reserved_setting');
 assert.equal((await self.Agent.testExecute({action:'dot_draft_backup',args})).error,'backup_exists');
});
test('composer diagnostics only addresses the exact Dot tab',async()=>{
 let calls=0;const self={UserIODot:{inspectComposer:()=>{}}};
 const chrome={tabs:{query:async()=>[{id:7,url}]},scripting:{executeScript:async()=>{calls++;return [{result:{ok:true,editor_count:1}}]}}};
 const src=fs.readFileSync(path.join(root,'lib/agent.js'),'utf8').replace('  root.Agent = lib;','  lib.testExecute = execute; root.Agent = lib;');vm.runInNewContext(src,{self,chrome,setTimeout,Promise,JSON});
 assert.equal((await self.Agent.testExecute({action:'dot_composer_inspect',args:{url,room:'r'}})).ok,true);assert.equal(calls,1);
 assert.equal((await self.Agent.testExecute({action:'dot_composer_inspect',args:{url:'https://evil.example',room:'r'}})).error,'invalid_dot_request');assert.equal(calls,1);
});
test('composer diagnostics refuses a different room and never returns draft content',()=>{
 const room='fixture',secret='private draft';
 const row={dataset:{messageId:room+'~'+room+'~CalpicoMessage~id'},closest:()=>null};
 const parent={tagName:'DIV',getAttribute:()=>null,querySelectorAll:s=>s==='img'?[{}]:[{}],parentElement:null};
 const editor={tagName:'DIV',textContent:secret,getClientRects:()=>[1],getAttribute:()=>null,querySelectorAll:()=>[],parentElement:parent};
 const document={querySelectorAll:s=>s.startsWith('article')?[row]:[editor]},self={};
 vm.runInNewContext(fs.readFileSync(path.join(root,'lib/dot.js'),'utf8'),{self,document,location:{href:url}});
 const result=self.UserIODot.inspectComposer(url,room);
 assert.equal(result.ok,true);assert.equal(result.ancestors[1].file_inputs,1);assert.equal(result.ancestors[1].images,1);
 assert.ok(!JSON.stringify(result).includes(secret));
 assert.equal(self.UserIODot.inspectComposer(url,'wrong').error,'wrong_or_unloaded_room');
});
test('draft recovery requires an explicit action and retains unknown outcomes',async()=>{
 const saved={},draft='private draft text';let mutate=0;
 const self={UserIODot:{captureDraft:()=>{},recoverDraft:()=>{}}};
 const chrome={tabs:{query:async()=>[{id:7,url}]},scripting:{executeScript:async ({func})=>[{result:func===self.UserIODot.captureDraft?{ok:true,text:draft,html:'<p>text</p>',hash:'a'.repeat(64)}:(mutate++,{ok:true})}]},storage:{local:{get:async key=>({[key]:saved[key]}),set:async obj=>Object.assign(saved,obj)}}};
 const src=fs.readFileSync(path.join(root,'lib/agent.js'),'utf8').replace('  root.Agent = lib;','  lib.testExecute = execute; root.Agent = lib;');vm.runInNewContext(src,{self,chrome,setTimeout,Promise,JSON});
 const args={url,room:'r',backup_id:'12345678-1234-1234-1234-123456789abc'};
 await self.Agent.testExecute({action:'dot_draft_backup',args});
 assert.equal((await self.Agent.testExecute({action:'dot_draft_recover',args:{...args,operation:'clear'}})).error,'confirmation_required');
 assert.equal(mutate,0);
 assert.equal((await self.Agent.testExecute({action:'dot_draft_recover',args:{...args,operation:'clear',confirm:'preserve_then_clear'}})).ok,true);
 assert.equal(mutate,1);
 assert.equal((await self.Agent.testExecute({action:'dot_draft_recover',args:{...args,operation:'clear',confirm:'preserve_then_clear'}})).error,'recovery_requires_review');
 assert.equal(mutate,1);
});
test('an unresolved automatic backup blocks later sends without exposing its text',async()=>{
 const id='12345678-1234-1234-1234-123456789abc';
 const saved={'dot_draft_backup:prior':{purpose:'automatic',url,room:'r',phase:'cleared',text:'private'}};
 let calls=0;const self={UserIODot:{captureDraft:()=>{}}};
 const chrome={tabs:{query:async()=>[{id:7,url}]},scripting:{executeScript:async()=>{calls++;return []}},storage:{local:{get:async key=>key===null?{...saved}:({[key]:saved[key]}),set:async obj=>Object.assign(saved,obj)}}};
 const src=fs.readFileSync(path.join(root,'lib/agent.js'),'utf8').replace('  root.Agent = lib;','  lib.testExecute = execute; root.Agent = lib;');vm.runInNewContext(src,{self,chrome,setTimeout,Promise,JSON});
 const result=await self.Agent.testExecute({action:'dot_draft_backup',args:{url,room:'r',backup_id:id,purpose:'automatic'}});
 assert.equal(result.error,'unresolved_draft_backup');assert.equal(calls,0);assert.ok(!JSON.stringify(result).includes('private'));
});
test('draft inspection command only addresses one exact Dot tab with a valid hash',async()=>{
 const x=setup([{id:1,url}]);
 assert.equal((await x.execute({action:'dot_draft_inspect',args:{url,room:'r',expected_hash:'bad'}})).error,'invalid_dot_request');
 assert.equal(x.calls.length,0);
 assert.equal((await x.execute({action:'dot_draft_inspect',args:{url,room:'r',expected_hash:'a'.repeat(64)}})).ok,true);
 assert.equal(x.calls[0].target.tabId,1);assert.equal(x.calls[0].args[2],'a'.repeat(64));
 const duplicate=setup([{id:1,url},{id:2,url}]);
 assert.equal((await duplicate.execute({action:'dot_draft_inspect',args:{url,room:'r',expected_hash:'a'.repeat(64)}})).error,'dot_tab_not_unique');
 assert.equal(duplicate.calls.length,0);
});
test('draft inspection matches only the exact room and does not return draft text',async()=>{
 const room='fixture',text='Сохранённый черновик';
 const row={dataset:{messageId:room+'~'+room+'~CalpicoMessage~id'},closest:()=>null};
 const editor={innerText:text,textContent:text,getClientRects:()=>[1],closest:()=>null};
 const document={querySelectorAll:s=>s.startsWith('article')?[row]:s.startsWith('[contenteditable')?[editor]:[],querySelector:()=>null};
 const self={};vm.runInNewContext(fs.readFileSync(path.join(root,'lib/dot.js'),'utf8'),{self,document,location:{href:url},TextEncoder,crypto:webcrypto});
 const hash=createHash('sha256').update(text).digest('hex');
 const result=await self.UserIODot.inspectDraft(url,room,hash);
 assert.equal(result.ok,true);assert.equal(result.matches,true);assert.ok(!JSON.stringify(result).includes(text));
 assert.equal((await self.UserIODot.inspectDraft(url,'wrong',hash)).error,'wrong_or_unloaded_room');
 assert.equal((await self.UserIODot.inspectDraft(url,room,'bad')).error,'invalid_expected_hash');
});
test('draft capture checks exact room and captures text and markup without mutation',async()=>{
 const room='fixture',text='line one\nline two',html='<p>line one</p><p>line two</p>';
 const row={dataset:{messageId:room+'~'+room+'~CalpicoMessage~id'},closest:()=>null};
 const editor={innerText:text,textContent:text,innerHTML:html,getClientRects:()=>[1]};
 const document={querySelectorAll:s=>s.startsWith('article')?[row]:s.startsWith('[contenteditable')?[editor]:[],querySelector:()=>null};
 const self={};vm.runInNewContext(fs.readFileSync(path.join(root,'lib/dot.js'),'utf8'),{self,document,location:{href:url},TextEncoder,crypto:webcrypto});
 const result=await self.UserIODot.captureDraft(url,room);
 assert.equal(result.text,text);assert.equal(result.html,html);assert.equal(result.hash,createHash('sha256').update(text).digest('hex'));
 assert.equal((await self.UserIODot.captureDraft(url,'wrong')).error,'wrong_or_unloaded_room');
 assert.equal(editor.innerHTML,html);
});
test('draft recovery refuses changes and restores only into an empty composer',async()=>{
 const room='fixture',text='saved text',html='<p>saved text</p>';
 const row={dataset:{messageId:room+'~'+room+'~CalpicoMessage~id'},closest:()=>null};
 const editor={innerText:text,textContent:text,innerHTML:html,getClientRects:()=>[1],focus:()=>{}};let calls=0;
 const document={querySelectorAll:s=>s.startsWith('article')?[row]:s.startsWith('[contenteditable')?[editor]:[],querySelector:()=>null,createRange:()=>({selectNodeContents:()=>{}}),execCommand:(cmd)=>{calls++;if(cmd==='delete'){editor.innerText=editor.textContent=editor.innerHTML='';}else{editor.innerText=editor.textContent=text;editor.innerHTML=html;}return true;}};
 const window={getSelection:()=>({removeAllRanges:()=>{},addRange:()=>{}})};const self={};
 vm.runInNewContext(fs.readFileSync(path.join(root,'lib/dot.js'),'utf8'),{self,document,window,location:{href:url},TextEncoder,crypto:webcrypto});
 assert.equal((await self.UserIODot.recoverDraft(url,room,'clear','different',html)).error,'draft_changed');assert.equal(calls,0);
 assert.equal((await self.UserIODot.recoverDraft(url,room,'restore',text,html)).error,'existing_draft');assert.equal(calls,0);
 assert.equal((await self.UserIODot.recoverDraft(url,room,'clear',text,html)).ok,true);
 assert.equal((await self.UserIODot.recoverDraft(url,room,'restore',text,html)).ok,true);
 assert.equal(editor.innerText,text);
});
test('draft inspection refuses ambiguous editor and existing reply panel',async()=>{
 const room='fixture',row={dataset:{messageId:room+'~'+room+'~CalpicoMessage~id'},closest:()=>null};
 const editor={innerText:'text',getClientRects:()=>[1]};let panel=false;
 const document={querySelectorAll:s=>s.startsWith('article')?[row]:s.startsWith('[contenteditable')?[editor,editor]:[],querySelector:()=>panel?{}:null};
 const self={};vm.runInNewContext(fs.readFileSync(path.join(root,'lib/dot.js'),'utf8'),{self,document,location:{href:url},TextEncoder,crypto:webcrypto});
 assert.equal((await self.UserIODot.inspectDraft(url,room,'a'.repeat(64))).error,'ambiguous_composer');
 panel=true;assert.equal((await self.UserIODot.inspectDraft(url,room,'a'.repeat(64))).error,'existing_reply_panel');
});
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
test('media read commands use the same exact tab and fixed reader',async()=>{
 const x=setup([{id:1,url}]);const r=await x.execute({action:'dot_media_read',args:{url,room:'r',operation:'start',source_id:'r~r~CalpicoMessage~1'}});
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
test('Dot snapshot preserves a blockquote range',async()=>{
 const room='fixture',quote={tagName:'BLOCKQUOTE',childNodes:[{nodeType:3,nodeValue:'цитата'}]},content={innerText:'цитата',textContent:'цитата',childNodes:[quote]};
 const row={dataset:{messageId:room+'~'+room+'~CalpicoMessage~id'},closest:()=>null,classList:{contains:()=>false},querySelector:s=>s==='.message-text'?content:null,querySelectorAll:()=>[]};
 const self={};vm.runInNewContext(fs.readFileSync(path.join(root,'lib/dot.js'),'utf8'),{self,document:{querySelectorAll:s=>s.startsWith('article')?[row]:[],querySelector:()=>null},location:{href:url},URL,TextEncoder,crypto:globalThis.crypto});
 const result=await self.UserIODot.pageAction(url,room,null,null);assert.deepEqual(JSON.parse(JSON.stringify(result.messages[0].entities)),[{type:'blockquote',offset:0,length:6}]);
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
test('plain multiline text preserves hard breaks instead of adding a paragraph gap',async()=>{
 const room='fixture',text='Никита Розанов:\nТест';let clicks=0;
 const row={dataset:{messageId:room+'~'+room+'~CalpicoMessage~id'},closest:()=>null,classList:{contains:()=>true},querySelector:s=>s==='.message-text'?{innerText:'old'}:null,querySelectorAll:()=>[]};
 const editor={innerText:'',getClientRects:()=>[1],focus:()=>{},querySelectorAll:()=>[]};
 const button={disabled:false,getClientRects:()=>[1],getAttribute:()=> 'Send',click:()=>clicks++};
 const document={querySelector:()=>null,querySelectorAll:s=>s.startsWith('article')?[row]:s.startsWith('[contenteditable')?[editor]:s==='button'?[button]:[],execCommand:(command,_ui,value)=>{editor.innerText=command==='insertText'?value.replace(/\n/g,'\n\n'):value.replace(/<br>/g,'\n');return true;}};
 const self={};vm.runInNewContext(fs.readFileSync(path.join(root,'lib/dot.js'),'utf8'),{self,document,location:{href:url},URL,TextEncoder,crypto:globalThis.crypto,setTimeout:fn=>fn()});
 const result=await self.UserIODot.pageAction(url,room,text,null);
 assert.equal(result.ok,true);assert.equal(editor.innerText,text);assert.equal(clicks,1);
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
test('media reply uploads and submits in the verified reply panel',async()=>{
 const room='fixture',target=room+'~'+room+'~CalpicoMessage~target';let opened=false,clicks=0;
 const row={dataset:{messageId:target},closest:()=>null,querySelector:s=>s==='[data-action="reply"]'?{click:()=>opened=true}:null};
 const input={files:[],dispatchEvent:()=>{}},preview={getClientRects:()=>[1]};
 const composer={outerHTML:'test.png',innerText:'',querySelectorAll:s=>s==='input[type="file"]'?[input]:s==='img'?(input.files.length?[preview]:[]):[],querySelector:()=>null};
 const editor={innerText:'',getClientRects:()=>[1],closest:()=>composer,focus:()=>{}};
 const button={getClientRects:()=>[1],getAttribute:()=> 'Send',disabled:false,click:()=>clicks++};
 const panel={getClientRects:()=>[1],querySelectorAll:s=>s.startsWith('article')?[row]:s.startsWith('[contenteditable')?[editor]:s==='button'?[button]:[]};
 const document={querySelector:s=>s==='.reply-chain-panel[role="dialog"]'&&opened?panel:null,querySelectorAll:s=>s.startsWith('article')?[row]:s==='.reply-chain-panel[role="dialog"]'?(opened?[panel]:[]):[],execCommand:(_a,_b,v)=>(editor.innerText=v,true)};
 class Transfer{constructor(){this.files=[];this.items={add:f=>this.files.push(f)}}};class FakeFile{constructor(_parts,name){this.name=name}}
 const self={};const context={self,document,location:{href:url},URL,TextEncoder,Uint8Array,crypto:globalThis.crypto,atob,Date,DataTransfer:Transfer,File:FakeFile,Event:class{},setTimeout:fn=>fn()};
 vm.runInNewContext(fs.readFileSync(path.join(root,'lib/dot.js'),'utf8'),context);
 const bytes=new Uint8Array([1,2,3,4]),hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(b=>b.toString(16).padStart(2,'0')).join(''),transfer_id='12345678-1234-1234-1234-123456789abc';
 await self.UserIODot.mediaAction(url,room,'start',{transfer_id,reply_to:target,files:[{filename:'test.png',mime:'image/png',size:4,sha256:hash}]});
 await self.UserIODot.mediaAction(url,room,'chunk',{transfer_id,index:0,offset:0,data:'AQIDBA=='});
 assert.equal((await self.UserIODot.mediaAction(url,room,'upload',{transfer_id})).ok,true);
 const result=await self.UserIODot.mediaAction(url,room,'send',{transfer_id,text:'Фото'});assert.equal(result.ok,true);assert.equal(result.verified_reply_to,target);assert.equal(clicks,1);
});
test('media caption keeps its clickable link in the Dot composer',async()=>{
 const room='fixture',row={dataset:{messageId:room+'~'+room+'~CalpicoMessage~id'},closest:()=>null},input={files:[],dispatchEvent:()=>{}};let html='';
 const composer={outerHTML:'test.png',innerText:'',querySelectorAll:s=>s==='input[type="file"]'?[input]:s==='img'?(input.files.length?[{getClientRects:()=>[1]}]:[]):[],querySelector:()=>null};
 const editor={innerText:'',getClientRects:()=>[1],closest:()=>composer,focus:()=>{},querySelectorAll:s=>s==='a'?[{getAttribute:()=> 'https://example.org/'}]:[]};
 const button={getClientRects:()=>[1],getAttribute:()=> 'Send',disabled:false,click:()=>{}};
 const document={querySelector:()=>null,querySelectorAll:s=>s.startsWith('article')?[row]:s.startsWith('[contenteditable')?[editor]:s==='button'?[button]:[],execCommand:(kind,_ui,value)=>{html=value;editor.innerText='сайт';return kind==='insertHTML'}};
 class Transfer{constructor(){this.files=[];this.items={add:f=>this.files.push(f)}}};class FakeFile{constructor(_parts,name){this.name=name}}
 const self={};vm.runInNewContext(fs.readFileSync(path.join(root,'lib/dot.js'),'utf8'),{self,document,location:{href:url},URL,TextEncoder,Uint8Array,crypto:globalThis.crypto,atob,Date,DataTransfer:Transfer,File:FakeFile,Event:class{},setTimeout:fn=>fn()});
 const bytes=new Uint8Array([1,2,3,4]),hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(b=>b.toString(16).padStart(2,'0')).join(''),transfer_id='12345678-1234-1234-1234-123456789abc';
 await self.UserIODot.mediaAction(url,room,'start',{transfer_id,text:'сайт',entities:[{type:'text_url',offset:0,length:4,url:'https://example.org/'}],files:[{filename:'test.png',mime:'image/png',size:4,sha256:hash}]});
 await self.UserIODot.mediaAction(url,room,'chunk',{transfer_id,index:0,offset:0,data:'AQIDBA=='});await self.UserIODot.mediaAction(url,room,'upload',{transfer_id});
 const result=await self.UserIODot.mediaAction(url,room,'send',{transfer_id,text:'сайт'});assert.equal(result.ok,true);assert.match(html,/<a href="https:\/\/example\.org\/">сайт<\/a>/);
});
test('document caption keeps a single newline when the editor renders paragraphs differently',async()=>{
 const room='fixture',text='Никита Розанов:\nВот план на завтра.',row={dataset:{messageId:room+'~'+room+'~CalpicoMessage~id'},closest:()=>null},input={files:[],dispatchEvent:()=>{}};let clicks=0;
 const composer={outerHTML:'plan.docx',innerText:'',querySelectorAll:s=>s==='input[type="file"]'?[input]:[],querySelector:()=>null};
 const editor={innerText:'',getClientRects:()=>[1],closest:()=>composer,focus:()=>{},querySelectorAll:()=>[]};
 const button={getClientRects:()=>[1],getAttribute:()=> 'Send',disabled:false,click:()=>clicks++};
 const document={querySelector:()=>null,querySelectorAll:s=>s.startsWith('article')?[row]:s.startsWith('[contenteditable')?[editor]:s==='button'?[button]:[],execCommand:(kind,_ui,value)=>{editor.innerText=kind==='insertText'?value.replace(/\n/g,'\n\n'):value.replace(/<br>/g,'\n');return true}};
 class Transfer{constructor(){this.files=[];this.items={add:f=>this.files.push(f)}}};class FakeFile{constructor(_parts,name){this.name=name}}
 const self={};vm.runInNewContext(fs.readFileSync(path.join(root,'lib/dot.js'),'utf8'),{self,document,location:{href:url},URL,TextEncoder,Uint8Array,crypto:globalThis.crypto,atob,Date,DataTransfer:Transfer,File:FakeFile,Event:class{},setTimeout:fn=>fn()});
 const bytes=new Uint8Array([1,2,3,4]),hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(b=>b.toString(16).padStart(2,'0')).join(''),transfer_id='12345678-1234-1234-1234-123456789abc';
 await self.UserIODot.mediaAction(url,room,'start',{transfer_id,text,files:[{filename:'plan.docx',mime:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',size:4,sha256:hash}]});
 await self.UserIODot.mediaAction(url,room,'chunk',{transfer_id,index:0,offset:0,data:'AQIDBA=='});await self.UserIODot.mediaAction(url,room,'upload',{transfer_id});
 const result=await self.UserIODot.mediaAction(url,room,'send',{transfer_id,text});assert.equal(result.ok,true);assert.equal(clicks,1);
});
test('document upload readiness uses its file name rather than an image preview',async()=>{
 const room='fixture',row={dataset:{messageId:room+'~'+room+'~CalpicoMessage~id'},closest:()=>null},input={files:[],dispatchEvent:()=>{}};
 const composer={outerHTML:'договор.pdf',innerText:'',querySelectorAll:s=>s==='input[type="file"]'?[input]:[],querySelector:()=>null};
 const editor={innerText:'',getClientRects:()=>[1],closest:()=>composer};const button={getClientRects:()=>[1],getAttribute:()=> 'Send',disabled:false};
 const document={querySelector:()=>null,querySelectorAll:s=>s.startsWith('article')?[row]:s.startsWith('[contenteditable')?[editor]:s==='button'?[button]:[]};
 class Transfer{constructor(){this.files=[];this.items={add:f=>this.files.push(f)}}};class FakeFile{constructor(_parts,name){this.name=name}}
 const self={};vm.runInNewContext(fs.readFileSync(path.join(root,'lib/dot.js'),'utf8'),{self,document,location:{href:url},URL,TextEncoder,Uint8Array,crypto:globalThis.crypto,atob,Date,DataTransfer:Transfer,File:FakeFile,Event:class{}});
 const bytes=new Uint8Array([1,2,3,4]),hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(b=>b.toString(16).padStart(2,'0')).join(''),transfer_id='12345678-1234-1234-1234-123456789abc';
 await self.UserIODot.mediaAction(url,room,'start',{transfer_id,files:[{filename:'договор.pdf',mime:'application/pdf',size:4,sha256:hash}]});
 await self.UserIODot.mediaAction(url,room,'chunk',{transfer_id,index:0,offset:0,data:'AQIDBA=='});await self.UserIODot.mediaAction(url,room,'upload',{transfer_id});
 assert.equal((await self.UserIODot.mediaAction(url,room,'inspect',{transfer_id})).ready,true);
});
test('a backgrounded Dot composer verifies exact text via textContent before sending',async()=>{
 const room='fixture',row={dataset:{messageId:room+'~'+room+'~CalpicoMessage~id'},closest:()=>null,classList:{contains:()=>true},querySelector:s=>s==='.message-text'?{innerText:'old'}:null,querySelectorAll:()=>[]};let clicks=0;
 const editor={innerText:'',textContent:'',isConnected:true,getClientRects:()=>[1],focus:()=>{},closest:()=>null};
 const button={getClientRects:()=>[1],getAttribute:()=> 'Send',disabled:false,click:()=>clicks++};
 const document={querySelector:()=>null,querySelectorAll:s=>s.startsWith('article')?[row]:s.startsWith('[contenteditable')?[editor]:s==='button'?[button]:[],execCommand:(_kind,_ui,value)=>(editor.textContent=value,true)};
 const self={};vm.runInNewContext(fs.readFileSync(path.join(root,'lib/dot.js'),'utf8'),{self,document,location:{href:url},URL,TextEncoder,crypto:globalThis.crypto,setTimeout:fn=>fn()});
 assert.equal((await self.UserIODot.pageAction(url,room,'точный текст',null)).ok,true);assert.equal(clicks,1);
});
test('a backgrounded existing draft is never overwritten',async()=>{
 const room='fixture',row={dataset:{messageId:room+'~'+room+'~CalpicoMessage~id'},closest:()=>null,classList:{contains:()=>true},querySelector:s=>s==='.message-text'?{innerText:'old'}:null,querySelectorAll:()=>[]};let writes=0;
 const editor={innerText:'',textContent:'черновик',getClientRects:()=>[1]};
 const document={querySelector:()=>null,querySelectorAll:s=>s.startsWith('article')?[row]:s.startsWith('[contenteditable')?[editor]:[],execCommand:()=>{writes++;return true}};
 const self={};vm.runInNewContext(fs.readFileSync(path.join(root,'lib/dot.js'),'utf8'),{self,document,location:{href:url},URL,TextEncoder,crypto:globalThis.crypto});
 assert.equal((await self.UserIODot.pageAction(url,room,'новый текст',null)).error,'existing_draft');assert.equal(writes,0);
});
test('snapshot reports a document resource card as an attachment of its message',async()=>{
 const room='fixture',id=room+'~'+room+'~CalpicoMessage~document';
 const card={innerText:'План.docx',textContent:'План.docx'};
 const row={dataset:{messageId:id},closest:()=>null,classList:{contains:()=>true},querySelector:()=>null,
  querySelectorAll:s=>s==='img.attachment-image'?[]:s==='.attachment-list [class~="group/resource-card"]'?[card]:[]};
 const self={};const document={querySelectorAll:s=>s.startsWith('article')?[row]:[],querySelector:()=>null};
 vm.runInNewContext(fs.readFileSync(path.join(root,'lib/dot.js'),'utf8'),{self,document,location:{href:url},URL,TextEncoder,crypto:webcrypto});
 const result=await self.UserIODot.pageAction(url,room,null,null);
 assert.equal(result.ok,true);assert.equal(result.messages[0].attachment_count,1);
 assert.deepEqual(Array.from(result.messages[0].attachment_names),['План.docx']);
 assert.match(result.messages[0].attachment_fingerprints[0],/^[a-f0-9]{64}$/);
});
test('an image inside a resource card is counted only once',async()=>{
 const room='fixture',id=room+'~'+room+'~CalpicoMessage~image';
 const image={alt:'photo.png',src:'https://chatgpt.com/files/photo.png'};
 const card={querySelector:s=>s==='img.attachment-image'?image:null,innerText:'photo.png'};
 const row={dataset:{messageId:id},closest:()=>null,classList:{contains:()=>true},querySelector:()=>null,
  querySelectorAll:s=>s==='img.attachment-image'?[image]:s==='.attachment-list [class~="group/resource-card"]'?[card]:[]};
 const self={};const document={querySelectorAll:s=>s.startsWith('article')?[row]:[],querySelector:()=>null};
 vm.runInNewContext(fs.readFileSync(path.join(root,'lib/dot.js'),'utf8'),{self,document,location:{href:url},URL,TextEncoder,crypto:webcrypto});
 const result=await self.UserIODot.pageAction(url,room,null,null);
 assert.equal(result.ok,true);assert.equal(result.messages[0].attachment_count,1);
 assert.deepEqual(Array.from(result.messages[0].attachment_names),['photo.png']);
});
test('an unreadable resource card does not make the whole room unreadable',async()=>{
 const room='fixture',id=room+'~'+room+'~CalpicoMessage~bad-card';
 const card={innerText:'bad/name\nmetadata',textContent:'bad/name\nmetadata'};
 const row={dataset:{messageId:id},closest:()=>null,classList:{contains:()=>false},querySelector:()=>null,
  querySelectorAll:s=>s==='img.attachment-image'?[]:s==='.attachment-list [class~="group/resource-card"]'?[card]:[]};
 const self={};const document={querySelectorAll:s=>s.startsWith('article')?[row]:[],querySelector:()=>null};
 vm.runInNewContext(fs.readFileSync(path.join(root,'lib/dot.js'),'utf8'),{self,document,location:{href:url},URL,TextEncoder,crypto:webcrypto});
 const result=await self.UserIODot.pageAction(url,room,null,null);
 assert.equal(result.ok,true);assert.equal(result.messages[0].attachment_incomplete,true);
 assert.equal(result.messages[0].attachment_count,1);assert.equal(result.messages[0].attachment_names.length,0);
});

test('owner-authorized draft discard is room-scoped and metadata failures only warn',async()=>{
 for(const failCleanup of [false,true]){
  const saved={'dot_draft_backup:old':{url,room:'r',text:'old'},'dot_draft_backup:other':{url,room:'other',text:'keep'}};
  const self={UserIODot:{captureDraft:()=>{},recoverDraft:()=>{}}};
  const chrome={tabs:{query:async()=>[{id:7,url}]},scripting:{executeScript:async()=>[{result:{ok:false,error:'empty_draft'}}]},storage:{local:{get:async()=>{if(failCleanup)throw Error('storage unavailable');return {...saved}},remove:async keys=>{for(const key of keys)delete saved[key]}}}};
  const src=fs.readFileSync(path.join(root,'lib/agent.js'),'utf8').replace('  root.Agent = lib;','  lib.testExecute = execute; root.Agent = lib;');
  vm.runInNewContext(src,{self,chrome,setTimeout,Promise,JSON});
  const result=await self.Agent.testExecute({action:'dot_draft_recover',args:{url,room:'r',operation:'discard',confirm:'discard_room_drafts'}});
  assert.equal(result.ok,true);assert.ok(saved['dot_draft_backup:other']);
  if(failCleanup)assert.ok(result.warnings.includes('draft_metadata_cleanup_failed'));
  else{assert.equal(result.discarded,1);assert.equal(saved['dot_draft_backup:old'],undefined);}
 }
});
