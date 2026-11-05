import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {pageAction} from './dot-browser-page.mjs';

const url='https://example.invalid/dot',room='fixture-room';
function fixture(options={}){
 let elapsed=0,inserts=0,clicks=0;
 const editor={innerText:options.existing||'',isConnected:true,getClientRects:()=>[1],focus:()=>{}};
 const replacement={...editor};
 const row={dataset:{messageId:room+'~'+room+'~CalpicoMessage~old'},closest:()=>null,classList:{contains:()=>false},querySelector:s=>s==='.message-text'?{innerText:'old'}:null};
 const button={getClientRects:()=>[1],getAttribute:()=> 'Send',get disabled(){return options.neverReady || elapsed < (options.readyAt||0);},click:()=>{clicks++;editor.innerText='';}};
 const location={href:url};
 const document={
  querySelector:()=>null,
  querySelectorAll:s=>s.startsWith('article')?[row]:
   s.startsWith('[contenteditable')?(options.replaceAt&&elapsed>=options.replaceAt?[replacement]:[editor]):
   s==='button'?(options.absentUntil&&elapsed>0&&elapsed<options.absentUntil?[]:[button]):[],
  execCommand:(_a,_b,text)=>{inserts++;editor.innerText=options.insertMismatch?'different':text;return true;}
 };
 const context={
  location,document,
  setTimeout:(callback,ms)=>{
   elapsed+=ms;
   if(options.editAt&&elapsed>=options.editAt)editor.innerText='human changed draft';
   if(options.navigateAt&&elapsed>=options.navigateAt)location.href='https://example.invalid/elsewhere';
   if(options.wrongRoomAt&&elapsed>=options.wrongRoomAt)row.dataset.messageId='wrong-room';
   if(options.detachAt&&elapsed>=options.detachAt)editor.isConnected=false;
   callback();
  }
 };
 return {run:async(fn=pageAction,text='synthetic message')=>vm.runInNewContext('('+fn.toString()+')(...'+JSON.stringify([url,room,text,null])+')',context),get state(){return {elapsed,inserts,clicks,draft:editor.innerText};}};
}
test('delayed enabled Send receives one insert and one click',async()=>{
 const f=fixture({readyAt:400});assert.equal((await f.run()).ok,true);assert.deepEqual(f.state,{elapsed:500,inserts:1,clicks:1,draft:''});
});
test('temporary missing Send is awaited without reinserting',async()=>{
 const f=fixture({absentUntil:400});assert.equal((await f.run()).ok,true);assert.equal(f.state.inserts,1);assert.equal(f.state.clicks,1);
});
test('never ready preserves the draft and stops after five seconds',async()=>{
 const f=fixture({neverReady:true});const result=await f.run();assert.equal(result.error,'draft_requires_review');assert.equal(result.draft_issue,'send_not_ready');
 assert.deepEqual(f.state,{elapsed:5000,inserts:1,clicks:0,draft:'synthetic message'});
});
test('an existing human draft is neither changed nor submitted',async()=>{
 const f=fixture({existing:'human draft'});assert.equal((await f.run()).error,'existing_draft');assert.deepEqual(f.state,{elapsed:0,inserts:0,clicks:0,draft:'human draft'});
});
test('text mismatch stops and preserves exact observed draft',async()=>{
 const f=fixture({insertMismatch:true});const result=await f.run();assert.equal(result.draft_issue,'text_changed');assert.equal(f.state.clicks,0);assert.equal(f.state.draft,'different');
});
test('human edit during readiness wait stops before any click',async()=>{
 const f=fixture({readyAt:400,editAt:200});const result=await f.run();assert.equal(result.draft_issue,'text_changed');assert.equal(f.state.clicks,0);assert.equal(f.state.inserts,1);assert.equal(f.state.draft,'human changed draft');
});
test('composer replacement or detachment stops before click',async()=>{
 for(const options of [{readyAt:400,replaceAt:200},{readyAt:400,detachAt:200}]){const f=fixture(options);assert.equal((await f.run()).draft_issue,'composer_changed');assert.equal(f.state.clicks,0);assert.equal(f.state.inserts,1);}
});
test('navigation while waiting fails closed and preserves draft',async()=>{
 const f=fixture({readyAt:400,navigateAt:200});assert.equal((await f.run()).error,'navigation_requires_review');assert.equal(f.state.clicks,0);assert.equal(f.state.draft,'synthetic message');
});
test('room change before readiness fails closed',async()=>{
 const f=fixture({readyAt:400,wrongRoomAt:200});assert.equal((await f.run()).error,'room_changed_requires_review');assert.equal(f.state.clicks,0);assert.equal(f.state.draft,'synthetic message');
});
test('ordinary immediately ready path sends exactly once',async()=>{
 const f=fixture();assert.equal((await f.run()).ok,true);assert.equal(f.state.clicks,1);assert.equal(f.state.inserts,1);
});
