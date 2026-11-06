import './dot-test-policy.mjs';
import test from 'node:test';import assert from 'node:assert/strict';
import fs from 'node:fs';import os from 'node:os';import path from 'node:path';import {createHash} from 'node:crypto';
import {DotMediaIngress,imageKind,MAX_IMAGE_BYTES} from './dot-media.mjs';
import {fileChooserUid,validateMediaFiles,mediaPageAction} from './dot-browser-media.mjs';
const OWNER='111111111',BOT='telegram:333333333';
const event=id=>({accountId:BOT,peerId:OWNER,senderId:OWNER,private:true,out:false,isEdit:false,messageId:String(id),text:''});
const jpeg=()=>Buffer.from([255,216,255,224,1,2,3,4]);
test('only bounded JPEG PNG WEBP images accepted, never arbitrary files',()=>{
 assert.equal(imageKind(jpeg()).mime,'image/jpeg');
 assert.equal(imageKind(Buffer.from([137,80,78,71,13,10,26,10])).ext,'png');
 assert.throws(()=>imageKind(Buffer.from('shell command')));
 assert.throws(()=>imageKind(Buffer.alloc(MAX_IMAGE_BYTES+1)));
 assert.throws(()=>validateMediaFiles([{path:'/etc/passwd',filename:'passwd',sha256:'a'.repeat(64)}]));
});
test('owner-only album batches images with captions/reply and deduplicates known delivery',async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'dot-media-test-')),timers=[],delivered=[];
 const ingress=new DotMediaIngress({stateDir:dir,botAccountId:BOT,download:async()=>jpeg(),deliver:async e=>{delivered.push(e);return {status:'accepted',echo:'echo'};},schedule:f=>timers.push(f)});
 const photo={photo:{},groupedId:'777'};
 assert.equal(ingress.receive({...event(1),senderId:'other'},photo).status,'ignored');
 ingress.receive({...event(1),text:'caption',replyTo:'15'},photo);ingress.receive(event(2),photo);
 assert.equal(delivered.length,0);assert.equal(ingress.receive(event(1),photo).duplicate,true);
 let idle=false;const pending=ingress.waitForPending().then(()=>{idle=true;});
 assert.equal(idle,false);await ingress.flush('album:777');await pending;
 assert.equal(delivered.length,1);assert.equal(delivered[0].media.length,2);assert.equal(delivered[0].text,'caption');
 assert.equal(delivered[0].replyTo,'15');assert.deepEqual(delivered[0].source_message_ids,['1','2']);
 assert.ok(delivered[0].media.every(m=>!fs.existsSync(m.path)));
 assert.equal(ingress.receive(event(2),photo).duplicate,true);assert.equal(delivered.length,1);
 fs.rmSync(dir,{recursive:true});
});
test('the existing media ingress accepts only the exact authorised group and human sender',async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'dot-group-media-')),delivered=[];
 const peer='-1004352022764';
 const ingress=new DotMediaIngress({stateDir:dir,botAccountId:BOT,allowEvent:e=>e.peerId===peer&&e.private===false&&e.senderIsBot===false&&/^\d+$/.test(e.senderId),download:async()=>jpeg(),deliver:async e=>(delivered.push(e),{status:'accepted'}),schedule:()=>{}});
 const e={...event(81),peerId:peer,senderId:'43',senderIsBot:false,private:false,text:'Оля:\nФото'};
 assert.equal(ingress.receive({...e,peerId:'-1009'},{photo:{}}).status,'ignored');
 assert.equal(ingress.receive(e,{photo:{}}).status,'media_collecting');await ingress.flush('message:81');
 assert.equal(delivered.length,1);assert.equal(delivered[0].text,'Оля:\nФото');assert.equal(delivered[0].media.length,1);
 fs.rmSync(dir,{recursive:true});
});
test('album captions retain their separate formatted ranges when joined',async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'dot-album-format-'));let delivered;
 const ingress=new DotMediaIngress({stateDir:dir,botAccountId:BOT,download:async()=>jpeg(),deliver:async e=>(delivered=e,{status:'accepted'}),schedule:()=>{}});
 ingress.receive({...event(1),text:'первая',entities:[{type:'bold',offset:0,length:6}]},{photo:{},groupedId:'7'});
 ingress.receive({...event(2),text:'сайт',entities:[{type:'text_url',offset:0,length:4,url:'https://example.org/'}]},{photo:{},groupedId:'7'});
 await ingress.flush('album:7');
 assert.equal(delivered.text,'первая\n\nсайт');
 assert.deepEqual(delivered.entities,[{type:'bold',offset:0,length:6},{type:'text_url',offset:8,length:4,url:'https://example.org/'}]);
 fs.rmSync(dir,{recursive:true});
});
test('authorised group documents preserve their file name and bytes',async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'dot-group-doc-')),peer='-1004352022764';let delivered;
 const ingress=new DotMediaIngress({stateDir:dir,botAccountId:BOT,allowDocuments:true,allowEvent:e=>e.peerId===peer&&e.private===false&&e.senderIsBot===false,download:async()=>Buffer.from('%PDF-fixture'),deliver:async e=>(delivered=e,{status:'accepted'}),schedule:()=>{}});
 const e={...event(82),peerId:peer,senderId:'43',senderIsBot:false,private:false,text:'Оля:\nДокумент'};
 const message={media:{className:'MessageMediaDocument',document:{mimeType:'application/pdf',attributes:[{fileName:'договор.pdf'}]}}};
 assert.equal(ingress.receive(e,message).status,'media_collecting');await ingress.flush('message:82');
 assert.equal(delivered.media[0].filename,'договор.pdf');assert.equal(delivered.media[0].mime,'application/pdf');assert.match(delivered.media[0].path,/\.bin$/);
 fs.rmSync(dir,{recursive:true});
});
test('document staging validates a protected digest while retaining the original file name',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'dot-doc-validation-')),bytes=Buffer.from('%PDF-fixture'),sha256=createHash('sha256').update(bytes).digest('hex'),file=path.join(dir,'tg-82-'+sha256.slice(0,16)+'.bin');
 fs.writeFileSync(file,bytes,{mode:0o600});
 const result=validateMediaFiles([{path:file,filename:'договор.pdf',mime:'application/pdf',size:bytes.length,sha256}],{root:dir+'/'});
 assert.equal(result[0].filename,'договор.pdf');assert.deepEqual(Buffer.from(result[0].data,'base64'),bytes);
 fs.rmSync(dir,{recursive:true});
});
test('group album never combines messages from different senders',async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'dot-album-sender-')),peer='-1004352022764';let sent=0;
 const ingress=new DotMediaIngress({stateDir:dir,botAccountId:BOT,allowEvent:e=>e.peerId===peer&&e.private===false&&e.senderIsBot===false,download:async()=>jpeg(),deliver:async()=>{sent++;return {status:'accepted'}},schedule:()=>{}});
 const e={...event(81),peerId:peer,senderId:'43',senderIsBot:false,private:false,text:'Фото'};
 ingress.receive(e,{photo:{},groupedId:'7'});ingress.receive({...e,messageId:'82',senderId:'44'},{photo:{},groupedId:'7'});await ingress.flush('album:7');
 assert.equal(sent,0);assert.equal(ingress.state.groups['album:7'].reason,'album_sender_ambiguous');fs.rmSync(dir,{recursive:true});
});
test('uncertain album delivery is durable and never replayed after restart',async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'dot-media-unknown-'));
 let count=0;const options={stateDir:dir,botAccountId:BOT,download:async()=>jpeg(),deliver:async()=>{count++;return {status:'send_uncertain'};},schedule:()=>{}};
 const a=new DotMediaIngress(options);a.receive(event(4),{photo:{}});await a.flush('message:4');
 const b=new DotMediaIngress(options);assert.equal(b.receive(event(4),{photo:{}}).duplicate,true);assert.equal(count,1);
 assert.equal(b.state.groups['message:4'].status,'send_uncertain');
 fs.rmSync(dir,{recursive:true});
});
test('interrupted preparation and ambiguous album replies fail closed',async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'dot-media-stop-'));let count=0;
 const opts={stateDir:dir,botAccountId:BOT,download:async()=>jpeg(),deliver:async()=>{count++;},schedule:()=>{}};
 const a=new DotMediaIngress(opts);a.receive(event(1),{photo:{}});
 const b=new DotMediaIngress(opts);assert.equal(b.state.groups['message:1'].status,'preparation_interrupted_requires_review');
 b.receive({...event(2),replyTo:'8'},{photo:{},groupedId:'9'});b.receive({...event(3),replyTo:'10'},{photo:{},groupedId:'9'});
 await b.flush('album:9');assert.equal(b.state.groups['album:9'].reason,'album_reply_ambiguous');assert.equal(count,0);
 fs.rmSync(dir,{recursive:true});
});
test('chooser UID must be unique official upload button, not generic document text',()=>{
 const output=text=>JSON.stringify({result:{content:[{type:'text',text}]}});
 assert.equal(fileChooserUid(output('uid=1_851 button "Добавить фото и файлы"')),'1_851');
 assert.throws(()=>fileChooserUid(output('text "Добавить фото и файлы"')));
 assert.throws(()=>fileChooserUid(output('uid=1_1 button "Добавить фото и файлы"\nuid=1_2 button "Добавить фото и файлы"')));
});
test('media UI rejects wrong route before touching composer',async()=>{
 globalThis.location={href:'https://chatgpt.com/c/other'};
 assert.equal((await mediaPageAction('https://chatgpt.com/dots/fixed','room','prepare',{})).error,'wrong_dot_route');
 delete globalThis.location;
});
test('late album member cannot be silently claimed as part of already preparing delivery',async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'dot-media-late-'));
 let release;const gate=new Promise(r=>{release=r;});let delivered;
 const ingress=new DotMediaIngress({stateDir:dir,botAccountId:BOT,download:async()=>{await gate;return jpeg();},deliver:async e=>(delivered=e,{status:'accepted'}),schedule:()=>{}});
 ingress.receive(event(1),{photo:{},groupedId:'9'});const run=ingress.flush('album:9');
 assert.equal(ingress.receive(event(2),{photo:{},groupedId:'9'}).status,'late_album_member_requires_review');
 release();await run;assert.deepEqual(delivered.source_message_ids,['1']);assert.deepEqual(ingress.state.groups['album:9'].late_ids,['2']);
 fs.rmSync(dir,{recursive:true});
});
test('enabled Send is insufficient while attachment accessibility label says uploading',async()=>{
 const room='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',url='https://chatgpt.com/dots/fixed',filename='tg-55-640b55ed06175cf6.jpg';
 let uploading=true;
 const img={getClientRects:()=>[1]};
 const composer={outerHTML:'<img alt="'+filename+'">',innerText:'',querySelector:()=>null,querySelectorAll:s=>s==='img'?[img]:s==='[aria-label]'&&uploading?[{getAttribute:()=> 'Uploading '+filename}]:[]};
 const editor={getClientRects:()=>[1],innerText:'',closest:()=>composer};
 const row={dataset:{messageId:room+'~'+room+'~CalpicoMessage~one'},closest:()=>null};
 const button={getClientRects:()=>[1],getAttribute:()=> 'Send',disabled:false};
 globalThis.location={href:url};globalThis.document={querySelector:()=>null,querySelectorAll:s=>s.startsWith('article')?[row]:s.startsWith('[contenteditable')?[editor]:s==='button'?[button]:[]};
 assert.equal((await mediaPageAction(url,room,'inspect',{filenames:[filename]})).ready,false);
 uploading=false;assert.equal((await mediaPageAction(url,room,'inspect',{filenames:[filename]})).ready,true);
 composer.innerText='Failed to upload';
 assert.equal((await mediaPageAction(url,room,'inspect',{filenames:[filename]})).error,'media_upload_failed_requires_review');
 delete globalThis.location;delete globalThis.document;
});
