import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
function setup(tabs){
 let calls=[];const self={UserIODot:{pageAction:()=>{}}};
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
