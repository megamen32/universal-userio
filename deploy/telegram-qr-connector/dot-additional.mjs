import {openSync,closeSync,fstatSync,readFileSync,constants} from 'node:fs';

// Additional bindings reuse the existing connector, command channel and journals.
// The two legacy bindings retain their paths and behavior.
export function validateAdditionalBindings(rows,{peerId,reserved=[],reservedRooms=[]}){
 if(!Array.isArray(rows)||rows.length>20)throw Error('invalid_additional_binding');
 const ids=new Set(),rooms=new Set(reservedRooms);
 for(const b of rows){
  if(b?.enabled===false)continue;
  if(!b||typeof b!=='object'||b.enabled!==true||!/^user_[a-z0-9]+$/.test(b.owner||'')||b.peerId!==peerId||
   !/^telegram:[1-9][0-9]+$/.test(b.botAccountId||'')||! /^[A-Za-z0-9_-]{1,100}$/.test(b.agentId||'')||! /^[A-Za-z0-9._:-]{1,128}$/.test(b.connectionId||'')||! /^[a-f0-9]{64}$/.test(b.accountScope||'')||
   !Number.isSafeInteger(b.pageId)||b.pageId<=0||! /^[a-f0-9]{32}$/.test(b.room||'')||
   !/^https:\/\/chatgpt\.com\/dots\/[A-Za-z0-9~_-]+$/.test(b.url||''))throw Error('invalid_additional_binding');
  if(reserved.includes(b.botAccountId))throw Error('reserved_additional_bot');
  if(ids.has(b.botAccountId))throw Error('duplicate_additional_binding');
  if(rooms.has(b.room))throw Error('duplicate_additional_room');
  rooms.add(b.room);
  ids.add(b.botAccountId);
 }
 return rows.filter(b=>b.enabled===true);
}
export function readAdditionalBindings(path,policy){
 let fd;
 try{
  fd=openSync(path,constants.O_RDONLY|constants.O_NOFOLLOW);
  const st=fstatSync(fd);
  if(!st.isFile()||st.uid!==process.getuid()||(st.mode&0o777)!==0o600||st.size>32768)throw Error('unsafe_additional_bindings');
  return validateAdditionalBindings(JSON.parse(readFileSync(fd,'utf8')),policy);
 }catch(e){if(e.code==='ENOENT')return [];throw e;}
 finally{if(fd!==undefined)closeSync(fd);}
}
export class AdditionalPrivateDots{
 constructor({read,create}){this.read=read;this.create=create;this.contexts=new Map();this.running=false;}
 matches(accountId){
  try{return this.read().some(b=>b.botAccountId===accountId);}
  catch{return this.contexts.has(accountId);}
 }
 assertCurrent(binding){
  const current=this.read().find(b=>b.botAccountId===binding.botAccountId);
  if(JSON.stringify(current)!==JSON.stringify(binding))throw Error('additional_binding_changed');
 }
 async context(binding){
  const id=binding.botAccountId,identity=JSON.stringify(binding);
  let entry=this.contexts.get(id);
  if(entry&&entry.identity!==identity)throw Error('additional_binding_changed');
  if(!entry){
   entry={identity,promise:Promise.resolve().then(()=>this.create(binding,()=>this.assertCurrent(binding)))};
   this.contexts.set(id,entry);
   entry.promise.catch(()=>{if(this.contexts.get(id)===entry)this.contexts.delete(id);});
  }
  const context=await entry.promise;this.assertCurrent(binding);return context;
 }
 async receive(event,message){
  const b=this.read().find(b=>b.botAccountId===event.accountId);
  if(!b)return false;
  if(event.peerId!==b.peerId||event.senderId!==b.peerId||!event.private||event.out||event.isEdit)return true;
  const c=await this.context(b);await c.receive(event,message);return true;
 }
 async tick(){
  if(this.running)return;this.running=true;
  try{
   const results=await Promise.allSettled(this.read().map(async b=>{const c=await this.context(b);return c.mirror.tick();}));
   if(results.some(r=>r.status==='rejected'))throw Error('additional_dot_requires_review');
   return results;
  }finally{this.running=false;}
 }
}
