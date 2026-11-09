import test from 'node:test';
import assert from 'node:assert/strict';
import {editDotText} from './dot-edit.mjs';
import {textHash} from './dot-relay.mjs';

class TextUrl {constructor(v){Object.assign(this,v);this.className='MessageEntityTextUrl'}}
class EditMessage {constructor(v){Object.assign(this,v)}}
const api={MessageEntityTextUrl:TextUrl,messages:{EditMessage}};
function fixture(){
 let message={id:42,out:true,message:'Dot:\nсайт',entities:[]},calls=0;
 const client={getMessages:async()=>[{...message}],invoke:async r=>{calls++;message={...message,message:r.message,entities:r.entities};}};
 return {client,get calls(){return calls},setMessage:m=>{message={...message,...m}}};
}
const link=[{type:'text_url',offset:5,length:4,url:'https://example.org/'}];
test('edit preserves a new hyperlink and confirms exact provider readback',async()=>{
 const f=fixture();await editDotText({client:f.client,api,peer:'p',messageId:42,expectedHash:textHash('Dot:\nсайт'),text:'Dot:\nсайт',entities:link,previousEntities:[]});assert.equal(f.calls,1);
});
test('edit refuses an external formatting change before submission',async()=>{
 const f=fixture();f.setMessage({entities:[new TextUrl({offset:5,length:4,url:'https://elsewhere.example/'})]});
 await assert.rejects(editDotText({client:f.client,api,peer:'p',messageId:42,expectedHash:textHash('Dot:\nсайт'),text:'Dot:\nсайт',entities:link,previousEntities:[]}),/source_changed/);assert.equal(f.calls,0);
});
test('edit does not claim success if the provider readback loses the link',async()=>{
 const f=fixture(),read=f.client.getMessages;
 f.client.getMessages=async(...args)=>{const rows=await read(...args);if(f.calls)rows[0].entities=[];return rows};
 await assert.rejects(editDotText({client:f.client,api,peer:'p',messageId:42,expectedHash:textHash('Dot:\nсайт'),text:'Dot:\nсайт',entities:link,previousEntities:[]}),/unknown/);
 assert.equal(f.calls,1);
});
test('edit refuses provider readback with a duplicated formatting entity',async()=>{
 const f=fixture(),invoke=f.client.invoke;
 f.client.invoke=async request=>{
  await invoke(request);
  f.setMessage({entities:[...request.entities,...request.entities]});
 };
 await assert.rejects(editDotText({client:f.client,api,peer:'p',messageId:42,expectedHash:textHash('Dot:\nсайт'),text:'Dot:\nсайт',entities:link,previousEntities:[]}),/unknown/);
 assert.equal(f.calls,1);
});
