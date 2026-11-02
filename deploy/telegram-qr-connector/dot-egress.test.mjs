import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {dotTelegramMediaParams,dotTelegramMediaReceipts} from './dot-egress.mjs';

class FakeCustomFile{
 constructor(name,size,path,buffer){Object.assign(this,{name,size,path,buffer});}
}
const png=i=>{
 const bytes=Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]),Buffer.alloc(256,i)]);
 const sha256=createHash('sha256').update(bytes).digest('hex');
 return {filename:'dot-'+i+'-'+sha256.slice(0,16)+'.png',bytes,sha256,mime:'image/png'};
};
test('Telegram photo uses verified CustomFile, exact caption, literal entities and native reply',()=>{
 const m=png(1);
 const p=dotTelegramMediaParams([m],'dots:\nphoto','42',FakeCustomFile);
 assert.equal(p.file.name,m.filename);
 assert.equal(p.file.size,m.bytes.length);
 assert.equal(p.file.path,'');
 assert.ok(p.file.buffer.equals(m.bytes));
 assert.equal(p.caption,'dots:\nphoto');
 assert.equal(p.replyTo,42);
 assert.deepEqual(p.formattingEntities,[]);
 assert.equal(p.forceDocument,false);
});
test('album is sent as one scoped file group without alternate client',()=>{
 const p=dotTelegramMediaParams([png(1),png(2)],'dots:\n[изображение]',null,FakeCustomFile);
 assert.equal(p.file.length,2);
 assert.equal(p.replyTo,undefined);
 assert.deepEqual(p.file.map(x=>x.name),[png(1).filename,png(2).filename]);
});
test('reject tampering, missing bytes, wrong image type, mismatched digest or filename',()=>{
 const m=png(1);
 for(const bad of [
  {...m,bytes:Buffer.from('not an image')},
  {...m,sha256:'0'.repeat(64)},
  {...m,filename:'../../evil.png'},
  {...m,filename:'dot-1-'+m.sha256.slice(0,16)+'.jpg'},
  {...m,bytes:'not Buffer'},
  {...m,mime:'image/jpeg'}
 ])assert.throws(()=>dotTelegramMediaParams([bad],'caption',null,FakeCustomFile));
 assert.throws(()=>dotTelegramMediaParams([m],'caption','0',FakeCustomFile));
 assert.throws(()=>dotTelegramMediaParams([m],'caption','9007199254740992',FakeCustomFile));
 assert.throws(()=>dotTelegramMediaParams([m],'x'.repeat(901),null,FakeCustomFile));
});
test('bounds reject 11 photos, oversize single image and oversized album before provider call',()=>{
 const files=Array.from({length:11},(_,i)=>png(i+1));
 assert.throws(()=>dotTelegramMediaParams(files,'caption',null,FakeCustomFile));
 const large=png(1),b=Buffer.concat([large.bytes,Buffer.alloc(8*1024*1024)]);
 const sha256=createHash('sha256').update(b).digest('hex');
 assert.throws(()=>dotTelegramMediaParams([{...large,bytes:b,sha256,filename:'dot-1-'+sha256.slice(0,16)+'.png'}],'caption',null,FakeCustomFile));
});
test('provider receipt extraction accepts one photo or exact album IDs, never invents delivery',()=>{
 assert.deepEqual(dotTelegramMediaReceipts({id:81},1),['81']);
 assert.deepEqual(dotTelegramMediaReceipts([{id:81},{id:82}],2),['81','82']);
 for(const x of [null,[],[{}],[{id:'0'}],[{id:1},{id:1}],{id:'not-id'},[{id:1},{id:2},{id:3}]])
  assert.throws(()=>dotTelegramMediaReceipts(x,2));
});
