import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import os from 'node:os';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
function setup(fetchImpl) {
 const ctx={self:{},chrome:{runtime:{getManifest:()=>({version:'0.9.3'})}},fetch:fetchImpl,DataView,Uint8Array,TextDecoder,DecompressionStream,Response,Blob};
 vm.runInNewContext(fs.readFileSync(path.join(root,'lib/update.js'),'utf8'),ctx);
 return ctx.self.UserIOUpdate;
}
test('compares the actual downloadable ZIP manifest and reports a newer release',async()=>{
 const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'userio-update-'));
 try {
  fs.writeFileSync(path.join(tmp,'manifest.json'), JSON.stringify({manifest_version:3,name:'Universal UserIO Agent',version:'0.10.0'}));
  execFileSync('zip',['-q',path.join(tmp,'update.zip'),'manifest.json'],{cwd:tmp});
  const data=fs.readFileSync(path.join(tmp,'update.zip'));
  const update=setup(async()=>({ok:true,arrayBuffer:async()=>data.buffer.slice(data.byteOffset,data.byteOffset+data.byteLength)}));
  const result=await update.check();
  assert.equal(result.ok,true); assert.equal(result.available,true); assert.equal(result.latest,'0.10.0');
  assert.equal(update.compare('0.9.4','0.10.0'),-1); assert.equal(update.compare('1.0','1.0.0'),0);
 } finally { fs.rmSync(tmp,{recursive:true,force:true}); }
});
test('reports an unavailable version check honestly',async()=>{
 const update=setup(async()=>{throw new Error('offline')});
 const result=await update.check(); assert.equal(result.ok,false); assert.equal(result.current,'0.9.3');
});
