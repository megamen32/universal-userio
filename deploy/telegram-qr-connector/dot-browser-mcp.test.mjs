import './dot-test-policy.mjs';
import test from 'node:test';import assert from 'node:assert/strict';import vm from 'node:vm';import {fixedEvaluation,parseEvaluation} from './dot-browser-mcp.mjs';
test('only fixed operations and page IDs allowed',()=>{assert.throws(()=>fixedEvaluation('eval',{tab_id:7}));assert.throws(()=>fixedEvaluation('dot_send',{tab_id:'7',text:'hello'}));assert.equal(fixedEvaluation('dot_snapshot',{tab_id:9}).pageId,9)});
test('malicious message stays data, wrong URL fails closed',async()=>{const e=fixedEvaluation('dot_send',{tab_id:9,text:'\");globalThis.pwned=true;//'});const context={location:{href:'https://chatgpt.com/c/wrong'}};assert.equal((await vm.runInNewContext('('+e.function+')()',context)).error,'wrong_dot_route');assert.equal(context.pwned,undefined)});
test('fenced JSON parses embedded fences safely',()=>{const value={ok:true,text:'code ``` block'};const result={result:{content:[{type:'text',text:'Script\n```json\n'+JSON.stringify(value)+'\n```'}]}};assert.deepEqual(parseEvaluation(JSON.stringify(result)),value);assert.throws(()=>parseEvaluation('{"error":{}}'))});

test('page contract rejects wrong room, existing draft, ambiguous composer',async()=>{
 const {pageAction}=await import('./dot-browser-page.mjs');
 const url='https://chatgpt.com/dots/11111111-1111-4111-8111-111111111111~bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
 const room='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
 globalThis.location={href:url};
 const row={dataset:{messageId:room+'~'+room+'~CalpicoMessage~one'},classList:{contains:()=>false},querySelector:()=>({innerText:'old'})};
 let editors=[];
 globalThis.document={querySelectorAll:s=>s.startsWith('article')?[row]:editors};
 assert.equal((await pageAction(url,room,null,'missing')).error,'cursor_not_visible');
 assert.equal((await pageAction(url,room,'new',null)).error,'ambiguous_composer');
 editors=[{getClientRects:()=>[1],innerText:'user draft'}];
 assert.equal((await pageAction(url,room,'new',null)).error,'existing_draft');
 row.dataset.messageId='other';
 assert.equal((await pageAction(url,room,null,null)).error,'wrong_or_unloaded_room');
 delete globalThis.location;delete globalThis.document;
});
test('page reassignment uses unique exact URL and verified room, never retries sends',async()=>{
 const {createDotBrowserCaller,discoverDotPage}=await import('./dot-browser-mcp.mjs');
 const url='https://chatgpt.com/dots/11111111-1111-4111-8111-111111111111~bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
 const room='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
 const list=text=>JSON.stringify({result:{content:[{type:'text',text}]}});
 const fence=String.fromCharCode(96).repeat(3);
 const evalResult=value=>list(fence+'json\n'+JSON.stringify(value)+'\n'+fence);
 assert.equal(discoverDotPage(list('1: Your dot ('+url+') [selected]')),1);
 assert.throws(()=>discoverDotPage(list('1: A ('+url+')\n2: B ('+url+')')));
 assert.throws(()=>discoverDotPage(list('1: wrong (https://chatgpt.com/c/other)')));
 let calls=[],failSend=false;
 const caller=createDotBrowserCaller(async(name,args)=>{
  calls.push([name,args.pageId]);
  if(name==='list_pages')return list('1: dot ('+url+')');
  if(args.pageId===18)throw Error('gone');
  if(failSend)throw Error('uncertain');
  return evalResult({ok:true,room,cursor:'saved',messages:[]});
 });
 assert.equal((await caller('dot_snapshot',{tab_id:18})).ok,true);
 assert.deepEqual(calls,[['evaluate_script',18],['list_pages',undefined],['evaluate_script',1]]);
 calls=[];await caller('dot_snapshot',{tab_id:18});assert.deepEqual(calls,[['evaluate_script',1]]);
 calls=[];failSend=true;await assert.rejects(caller('dot_send',{tab_id:18,text:'x'}));
 assert.deepEqual(calls,[['evaluate_script',1]]);
});
test('rediscovery backs off repeated errors',async()=>{
 const {createDotBrowserCaller}=await import('./dot-browser-mcp.mjs');
 let lists=0;
 const caller=createDotBrowserCaller(async(name)=>{if(name==='list_pages'){lists++;return JSON.stringify({result:{content:[{type:'text',text:'no matching page'}]}});}throw Error('gone');},()=>1000);
 await assert.rejects(caller('dot_snapshot',{tab_id:18}));
 await assert.rejects(caller('dot_snapshot',{tab_id:18}));
 assert.equal(lists,1);
});
test('rediscovery wrong room cannot establish send mapping',async()=>{
 const {createDotBrowserCaller}=await import('./dot-browser-mcp.mjs');
 const fence=String.fromCharCode(96).repeat(3),pages=[];
 const pack=text=>JSON.stringify({result:{content:[{type:'text',text}]}});
 const caller=createDotBrowserCaller(async(name,args)=>{
  if(name==='list_pages')return pack('1: dot (https://chatgpt.com/dots/11111111-1111-4111-8111-111111111111~bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb)');
  pages.push(args.pageId);
  if(args.pageId===18)throw Error('gone');
  return pack(fence+'json\n'+JSON.stringify({ok:true,room:'other'})+'\n'+fence);
 });
 await assert.rejects(caller('dot_snapshot',{tab_id:18}),/room_unverified/);
 await assert.rejects(caller('dot_send',{tab_id:18,text:'x'}));
 assert.deepEqual(pages,[18,1,18]);
});
test('native reply uses exact target panel composer and closes only its cleared panel',async()=>{
 const {pageAction}=await import('./dot-browser-page.mjs');
 const url='https://chatgpt.com/dots/11111111-1111-4111-8111-111111111111~bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
 const room='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',id=room+'~'+room+'~CalpicoMessage~target';
 let opened=false,sent=0,closed=0;
 const editor={innerText:'',getClientRects:()=>[1],focus:()=>{}};
 const send={getClientRects:()=>[1],getAttribute:()=> 'Отправить',disabled:false,click:()=>{sent++;editor.innerText='';}};
 const close={getClientRects:()=>[1],getAttribute:()=> 'Закрыть ветку ответов',click:()=>{opened=false;closed++;}};
 const row={dataset:{messageId:id},closest:()=>null,classList:{contains:()=>false},querySelector:s=>s==='[data-action="reply"]'?{click:()=>{opened=true;}}:s==='.message-text'?{innerText:'target'}:null};
 const panel={getClientRects:()=>[1],querySelectorAll:s=>s.startsWith('article')?[row]:s.startsWith('[contenteditable')?[editor]:s==='button'?[send,close]:[]};
 globalThis.location={href:url};
 globalThis.document={querySelector:s=>s==='.reply-chain-panel[role="dialog"]'&&opened?panel:null,querySelectorAll:s=>s.startsWith('article')?[row]:s==='.reply-chain-panel[role="dialog"]'?(opened?[panel]:[]):s.startsWith('[contenteditable')?[editor]:s==='button'?[send]:[],execCommand:(_a,_b,text)=>{editor.innerText=text;return true;}};
 assert.equal((await pageAction(url,room,'reply',null,id)).ok,true);
 assert.equal(sent,1);assert.equal(closed,1);
 opened=true;assert.equal((await pageAction(url,room,'plain',null)).error,'existing_reply_panel');assert.equal(sent,1);
 delete globalThis.location;delete globalThis.document;
});
test('fixed browser operation rejects reply IDs outside this room',()=>{
 assert.throws(()=>fixedEvaluation('dot_send',{tab_id:1,text:'reply',reply_to:'other-room'}));
});
test('reply panel duplicate cannot rewind main mirror cursor or duplicate a source',async()=>{
 const {pageAction}=await import('./dot-browser-page.mjs');
 const room='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',url='https://chatgpt.com/dots/11111111-1111-4111-8111-111111111111~bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
 const row=(id,panel=false)=>({dataset:{messageId:room+'~'+room+'~CalpicoMessage~'+id},closest:()=>panel?{}:null,classList:{contains:()=>false},querySelector:s=>s==='.message-text'?{innerText:id}:null});
 const rows=[row('old'),row('new'),row('old',true)];
 globalThis.location={href:url};globalThis.document={querySelectorAll:s=>s.startsWith('article')?rows:[]};
 const result=await pageAction(url,room,null,null);
 assert.equal(result.messages.length,2);assert.ok(result.cursor.endsWith('~new'));
 delete globalThis.location;delete globalThis.document;
});
test('ordinary composer reply-chain-container is not a reply dialog and cannot block plain send',async()=>{
 const {pageAction}=await import('./dot-browser-page.mjs');
 const room='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',url='https://chatgpt.com/dots/11111111-1111-4111-8111-111111111111~bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
 const container={className:'reply-chain-container'};let sends=0;
 const editor={innerText:'',getClientRects:()=>[1],focus:()=>{}};
 const row={dataset:{messageId:room+'~'+room+'~CalpicoMessage~old'},closest:()=>null,classList:{contains:()=>false},querySelector:s=>s==='.message-text'?{innerText:'old'}:null};
 const button={getClientRects:()=>[1],getAttribute:()=> 'Отправить',disabled:false,click:()=>{sends++;editor.innerText='';}};
 globalThis.location={href:url};globalThis.document={querySelector:s=>s==='.reply-chain-container'?container:null,querySelectorAll:s=>s.startsWith('article')?[row]:s.startsWith('[contenteditable')?[editor]:s==='button'?[button]:[],execCommand:(_a,_b,text)=>{editor.innerText=text;return true;}};
 assert.equal((await pageAction(url,room,'normal',null)).ok,true);assert.equal(sends,1);
 delete globalThis.location;delete globalThis.document;
});
