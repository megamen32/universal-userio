import fs from 'node:fs';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {OWNER} from './dot-relay.mjs';
export const MAX_IMAGE_BYTES=8*1024*1024,MAX_ALBUM_BYTES=20*1024*1024,MAX_IMAGES=10;
export function imageKind(bytes){
 if(!Buffer.isBuffer(bytes)||!bytes.length||bytes.length>MAX_IMAGE_BYTES)throw Error('image_size_refused');
 if(bytes[0]===255&&bytes[1]===216&&bytes[2]===255)return {ext:'jpg',mime:'image/jpeg'};
 if(bytes.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10])))return {ext:'png',mime:'image/png'};
 if(bytes.subarray(0,4).toString()==='RIFF'&&bytes.subarray(8,12).toString()==='WEBP')return {ext:'webp',mime:'image/webp'};
 throw Error('image_type_refused');
}
export function hasSupportedImage(message){
 return !!(message.photo||message.media?.photo)||/^image\/(?:jpeg|png|webp)$/.test(message.document?.mimeType||message.media?.document?.mimeType||'');
}
export class DotMediaIngress{
 constructor({stateDir,botAccountId,download,deliver,schedule=(f,ms)=>setTimeout(f,ms),now=()=>Date.now()}){
  if(!/^telegram:\d+$/.test(botAccountId))throw Error('invalid_bot');
  Object.assign(this,{stateDir,botAccountId,download,deliver,schedule,now});
  this.file=join(stateDir,'dot-media-'+botAccountId.split(':')[1]+'.json');
  this.state=fs.existsSync(this.file)?JSON.parse(fs.readFileSync(this.file,'utf8')):{groups:{}};
  this.pending=new Map();
  for(const g of Object.values(this.state.groups))if(['collecting','preparing','prepared'].includes(g.status))g.status='preparation_interrupted_requires_review';
  this.save();
 }
 save(){fs.mkdirSync(this.stateDir,{recursive:true,mode:0o700});fs.writeFileSync(this.file+'.tmp',JSON.stringify(this.state),{mode:0o600});fs.renameSync(this.file+'.tmp',this.file);}
 receive(event,message){
  if(event.accountId!==this.botAccountId||event.peerId!==OWNER||event.senderId!==OWNER||!event.private||event.out||event.isEdit||!hasSupportedImage(message)||!/^[1-9][0-9]*$/.test(event.messageId))return {status:'ignored'};
  const album=message.groupedId?String(message.groupedId):null;
  if(album&&!/^[0-9-]{1,30}$/.test(album))return {status:'invalid_album'};
  const key=album?'album:'+album:'message:'+event.messageId;
  let g=this.state.groups[key],pending=this.pending.get(key);
  if(g&&g.status!=='collecting'){
   if(!g.ids.includes(event.messageId)){g.late_ids=[...new Set([...(g.late_ids||[]),event.messageId])];this.save();return {status:'late_album_member_requires_review'};}
   return {status:g.status,duplicate:true};
  }
  if(g&&!pending)return {status:g.status,duplicate:true};
  if(!g){
   if(Object.values(this.state.groups).filter(x=>x.status!=='accepted').length>=32)return {status:'media_queue_full_requires_review'};
   g=this.state.groups[key]={status:'collecting',created:this.now(),ids:[],album};
   pending={events:new Map(),version:0};pending.done=new Promise(resolve=>{pending.resolve=resolve;});this.pending.set(key,pending);
  }
  if(g.ids.includes(event.messageId))return {status:g.status,duplicate:true};
  if(g.ids.length>=MAX_IMAGES){g.status='album_too_large_requires_review';this.save();pending.resolve();this.pending.delete(key);return {status:g.status};}
  g.ids.push(event.messageId);pending.events.set(event.messageId,{event,message});pending.version++;this.save();
  const version=pending.version;
  this.schedule(()=>{if(pending.version===version)void this.flush(key);},album?2000:0);
  return {status:'media_collecting',key};
 }
 waitForPending(){return Promise.all([...this.pending.values()].map(p=>p.done));}
 async flush(key){
  const pending=this.pending.get(key),g=this.state.groups[key];
  if(!pending||g.status!=='collecting')return;
  g.status='preparing';this.save();
  try{
   const rows=[...pending.events.values()].sort((a,b)=>Number(a.event.messageId)-Number(b.event.messageId));
   const replies=new Set(rows.map(r=>r.event.replyTo).filter(Boolean));
   if(replies.size>1)throw Error('album_reply_ambiguous');
   const media=[];let total=0;
   for(const {event,message} of rows){
    const bytes=Buffer.from(await this.download(message));
    const kind=imageKind(bytes);total+=bytes.length;if(total>MAX_ALBUM_BYTES)throw Error('album_size_refused');
    const sha256=createHash('sha256').update(bytes).digest('hex');
    const filename='tg-'+event.messageId+'-'+sha256.slice(0,16)+'.'+kind.ext;
    const directory=join(this.stateDir,'dot-media-files');fs.mkdirSync(directory,{recursive:true,mode:0o700});
    const path=join(directory,filename);
    const stagedBytes=fs.readdirSync(directory).reduce((total,name)=>{
      if(!/^tg-[0-9]+-[a-f0-9]{16}\.(jpg|png|webp)$/.test(name))return total;
      const stat=fs.lstatSync(join(directory,name));return total+(stat.isFile()?stat.size:0);
    },0);
    if(stagedBytes+bytes.length>256*1024*1024)throw Error('media_staging_quota');
    if(fs.existsSync(path)){
     const stat=fs.lstatSync(path);
     if(!stat.isFile()||stat.isSymbolicLink()||stat.uid!==process.getuid()||(stat.mode&0o777)!==0o600)throw Error('unsafe_image_file');
     if(createHash('sha256').update(fs.readFileSync(path)).digest('hex')!==sha256)throw Error('image_file_changed');
    }else fs.writeFileSync(path,bytes,{mode:0o600,flag:'wx'});
    media.push({path,filename,sha256,size:bytes.length,mime:kind.mime,source_message_id:event.messageId});
   }
   g.media=media;g.status='prepared';this.save();
   const first=rows[0].event;
   const text=rows.map(r=>r.event.text||'').filter(Boolean).join('\n\n');
   if(text.length>16000)throw Error('album_caption_too_large');
   const result=await this.deliver({...first,text,replyTo:[...replies][0]||null,media,source_message_ids:g.ids,grouped_id:g.album});
   g.status=result.status;g.echo=result.echo||null;this.save();
   if(result.status==='accepted')for(const file of media){try{fs.unlinkSync(file.path);}catch{}}
  }catch(error){g.status='media_requires_review';g.reason=/^[a-z0-9_]+$/.test(error?.message||'')?error.message:'media_preparation_failed';this.save();}
  finally{pending.resolve();this.pending.delete(key);}
 }
}
