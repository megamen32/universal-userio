import {DOT_POLICY} from './dot-policy.mjs';
import {OWNER,ROOM,textHash} from './dot-relay.mjs';
import {MAX_IMAGES} from './dot-media.mjs';
import {verifyDotTelegramMedia} from './dot-egress.mjs';
import {telegramEntities} from './dot-format.mjs';
import {chooseGroupReply,observeGroupMessage} from './dot-group-settings.mjs';
import {openSync,writeFileSync,fsyncSync,closeSync} from 'node:fs';
import {dirname,join} from 'node:path';
export function sensitiveText(text){
 const s=String(text);
 return /\b\d{6,12}:[A-Za-z0-9_-]{30,}\b/.test(s)||/\b(?:sk|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{16,}\b/.test(s)||/-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----/.test(s)||/\bBearer\s+[A-Za-z0-9._~+\/-]{12,}/i.test(s)||/(?:password|passwd|passphrase|парол[ьяь]|токен|token|api[_ -]?key|секрет|secret|session[_ -]?(?:id|key)|cookie)\s*(?::|=|\bis\b|это)/i.test(s)||/(?:парол[ьяь]|password|passphrase|api[_ -]?key|токен)/i.test(s);
}
export function safeReplyContext(message,replyId){
 if(!message||String(message.id)!==String(replyId)||message.out!==true||String(message.peerId?.userId||'')!==OWNER)return '[Контекст не подтверждён]';
 const text=String(message.message||'');
 if(!text.trim())return '[Исходное сообщение без текста]';
 if(sensitiveText(text))return '[Чувствительный контекст скрыт]';
 if(text.length>1400)return text.slice(0,1400)+'\n[Цитата сокращена]';
 return text;
}
// Only a durable native Telegram receipt may become a reply target.
const nativeTelegramMessageId=value=>typeof value==='string'&&/^[1-9][0-9]*$/.test(value)&&Number.isSafeInteger(Number(value));
export function resolveMirrorReply(relay,replyTo){
 if(replyTo==null)return {status:'none',receipt:null};
 if(typeof replyTo!=='string'||!replyTo)return {status:'unresolved',receipt:null};
 const matches=new Set();
 const published=relay.state.mirror?.published?.[replyTo];
 if((published?.peer_id||relay.peerId||OWNER)===(relay.peerId||OWNER)&&published?.status==='accepted'&&Array.isArray(published.receipts)&&published.receipts.length>0&&published.receipts.every(nativeTelegramMessageId))matches.add(published.receipts[0]);
 const prefix=relay.botAccountId+':'+(relay.peerId||OWNER)+':';
 for(const [key,record] of Object.entries(relay.state.incoming||{})){
  if(!key.startsWith(prefix)||record.source!=='telegram'||record.status!=='accepted'||record.echo!==replyTo)continue;
  const id=key.slice(prefix.length);
  if(record.source_message_ids?.length!==1||record.source_message_ids[0]!==id||!nativeTelegramMessageId(id))continue;
  matches.add(id);
 }
 return matches.size===1?{status:'resolved',receipt:[...matches][0]}:{status:'unresolved',receipt:null};
}
function mirrorTopic(relay,message){
 if(!relay.group)return {known:false};
 if(!message.reply_to){const context=relay.state.groupContext;return context&&Object.hasOwn(context,'currentTopic')?{known:true,topicId:context.currentTopic}:{known:false};}
 const topics=[];const published=relay.state.mirror?.published?.[message.reply_to];
 if(published?.status==='accepted'&&Object.hasOwn(published,'topic_id'))topics.push(published.topic_id);
 for(const r of Object.values(relay.state.incoming||{}))if(r.status==='accepted'&&r.echo===message.reply_to&&Object.hasOwn(r,'topic_id'))topics.push(r.topic_id);
 const unique=[...new Set(topics)];return unique.length===1?{known:true,topicId:unique[0]}:{known:false};
}
export function mirrorSendOptions(text,replyTo=null,entities=[],api=null){
 const options={message:text,formattingEntities:telegramEntities(text,entities,api),linkPreview:false};
 if(replyTo!=null){
  if(!nativeTelegramMessageId(String(replyTo)))throw Error('invalid_mirror_reply_receipt');
  options.replyTo=Number(replyTo);
 }
 return options;
}
export function mirrorParts(message,labels=DOT_POLICY){
 const label=message.role==='user'?(labels?.ownerLabel||'Owner'):(labels?.assistantLabel||'Assistant');const text=String(message.text||'');
 const pieces=[];let offset=0;
 while(offset<text.length){let end=Math.min(offset+3000,text.length);if(end<text.length&&/[\uD800-\uDBFF]/.test(text[end-1]))end--;pieces.push(text.slice(offset,end));offset=end;if(pieces.length>12)throw Error('message_too_large');}
 return pieces.map((body,i)=>label+(pieces.length>1?` (${i+1}/${pieces.length})`:'')+':\n'+body);
}
export function mirrorPartEntities(message,parts){
 const source=String(message.text||''),entities=message.entities||[];
 if(!Array.isArray(entities))throw Error('invalid_format_entities');
 const types=new Set(['bold','italic','strike','underline','code','pre','blockquote','text_url']);
 for(const e of entities){
  if(!types.has(e.type)||!Number.isSafeInteger(e.offset)||!Number.isSafeInteger(e.length)||e.offset<0||e.length<1||e.offset+e.length>source.length)throw Error('invalid_format_entity');
  if(e.type==='text_url'){let u;try{u=new URL(e.url)}catch{throw Error('invalid_format_url')};if(!['http:','https:'].includes(u.protocol))throw Error('invalid_format_url');}
 }
 let start=0;
 return parts.map(part=>{
  const prefix=part.indexOf('\n')+1, length=part.length-prefix,end=start+length;
  const projected=entities.filter(e=>e.offset<end&&e.offset+e.length>start).map(e=>({...e,offset:prefix+Math.max(0,e.offset-start),length:Math.min(end,e.offset+e.length)-Math.max(start,e.offset)}));
  start=end;return projected;
 });
}
// Text-only hashes remain byte-for-byte compatible with the existing journal.
// Media must be included in the source identity before any provider send.
export function mirrorSourceHash(m){
 const count=m.attachment_count??m.attachment_names?.length??0;
 if(!Number.isSafeInteger(count)||count<0||count>MAX_IMAGES)return null;
 if(!count)return textHash(m.text);
 const f=m.attachment_fingerprints;
 if(!Array.isArray(f)||f.length!==count||f.some(x=>typeof x!=='string'||! /^[a-f0-9]{64}$/.test(x)))return null;
 return textHash(JSON.stringify({text:String(m.text||''),media:f}));
}
const formatHash=m=>textHash(JSON.stringify({entities:m.entities||[],incomplete:!!m.format_incomplete}));
export class DotMirror{
 constructor(relay,{now=()=>Date.now(),stableMs=8000}={}){this.relay=relay;this.now=now;this.stableMs=stableMs;this.running=false;}
 async resync({requestId,expectedCursor,targetCursor}){
  const refused={status:'resync_refused'};
  if(this.running||this.relay.busy||! /^[a-f0-9]{32}$/.test(requestId||''))return refused;
  this.running=true;
  try{
   const state=this.relay.state,mirror=state.mirror;
   const unsafe=()=>this.relay.busy||mirror?.blocked||mirror?.status!=='cursor_not_visible'||mirror?.cursor!==expectedCursor||
    Object.values(mirror?.published||{}).some(p=>['sending','edit_uncertain'].includes(p.status))||
    Object.values(state.outgoing||{}).some(p=>p.status!=='accepted');
   if(!mirror||unsafe()||(mirror.resyncs||[]).some(r=>r.requestId===requestId))return refused;
   const before=JSON.stringify(state);
   let snap;try{snap=await this.relay.browserCall('dot_snapshot',{tab_id:this.relay.tabId});}catch{return refused;}
   if(unsafe()||before!==JSON.stringify(state)||!snap.ok||snap.room!==(this.relay.room||ROOM)||!snap.cursor||snap.cursor!==targetCursor||
      !Array.isArray(snap.messages)||!snap.messages.length||snap.messages.at(-1)?.id!==targetCursor||
      snap.messages.some(m=>m.id===expectedCursor))return refused;
   const backupPath=join(dirname(this.relay.statePath),'dot-resync-'+requestId+'.json');
   let fd,dirFd;try{
    fd=openSync(backupPath,'wx',0o600);
    writeFileSync(fd,JSON.stringify({state,snapshot:snap}));fsyncSync(fd);
    dirFd=openSync(dirname(backupPath),'r');fsyncSync(dirFd);
   }catch{return refused;}finally{if(fd!==undefined)closeSync(fd);if(dirFd!==undefined)closeSync(dirFd);}
   mirror.cursor=targetCursor;
   mirror.status='resynced';
   (mirror.resyncs||=([])).push({requestId,oldCursor:expectedCursor,newCursor:targetCursor,at:this.now(),gap:'not_delivered',backup:backupPath});
   try{this.relay.save();}catch(error){this.relay.state=JSON.parse(before);throw error;}
   return {status:'resynced',backupPath};
  }finally{this.running=false;}
 }
 async tick(){
  if(this.running||this.relay.busy)return {status:'incoming_busy'};
  this.running=true;
  try{
   let state=this.relay.state.mirror;
   if(state?.blocked)return {status:state.blocked};
   if(state&&Object.values(state.published).some(p=>['sending','edit_uncertain'].includes(p.status))){state.blocked='previous_operation_unknown';this.relay.save();return {status:state.blocked};}
   if(state?.nextReadAttemptAt>this.now())return {status:'read_backoff'};
   let snap;
   try{snap=await this.relay.browserCall('dot_snapshot',{tab_id:this.relay.tabId,...(this.relay.memberPrivateEnabled&&state?.cursor?{native_after:state.cursor}:{})});}
   catch(error){
    if(state){state.status='snapshot_read_failed';state.lastReadErrorAt=this.now();
     state.lastError=/^[a-z0-9_]{1,120}$/.test(String(error?.message||''))?error.message:'snapshot_transport_error';
     state.readFailures=(state.readFailures||0)+1;
     state.nextReadAttemptAt=this.now()+Math.min(300000,30000*2**Math.min(state.readFailures-1,4));
     this.relay.save();}
    return {status:'snapshot_read_failed'};
   }
   if(state){state.readFailures=0;delete state.nextReadAttemptAt;delete state.lastReadErrorAt;}
   if(!snap.ok||snap.room!==(this.relay.room||ROOM)||!snap.cursor)return {status:'snapshot_unavailable'};
   const rows=snap.messages||[];
   if(!state){state=this.relay.state.mirror={baselineAt:this.now(),cursor:snap.cursor,candidates:{},published:{},held:{}};this.relay.save();return {status:'baseline_initialized',cursor:state.cursor};}
   const index=rows.findIndex(m=>m.id===state.cursor);
   if(index<0){state.status='cursor_not_visible';this.relay.save();return {status:state.status};}
   const newRows=rows.slice(index+1);
   const edits=rows.filter(m=>{const p=state.published[m.id];return p&&(p.hash!==mirrorSourceHash(m)||(p.format_hash&&p.format_hash!==formatHash(m)));});
   for(const m of [...newRows,...edits]){
    if(!['user','assistant'].includes(m.role))continue;
    const hash=mirrorSourceHash(m);
    if(hash===null){state.status='media_identity_requires_review';state.blocked=state.status;this.relay.save();return {status:state.status};}
    const old=state.candidates[m.id];
    const format_hash=formatHash(m);
    if(!old||old.hash!==hash||old.format_hash!==format_hash)state.candidates[m.id]={hash,format_hash,since:this.now()};
   }
   this.relay.save();let changed=false;
   for(const m of newRows){
    if(!['user','assistant'].includes(m.role)){state.cursor=m.id;continue;}
    const origin=this.relay.mirrorOrigin(m);
    if(origin==='uncertain'){state.status='incoming_origin_uncertain';this.relay.save();return {status:state.status};}
    if(origin==='telegram'){state.cursor=m.id;delete state.candidates[m.id];changed=true;continue;}
    if(m.reply_resolution==='pending'){state.status='native_reply_pending';this.relay.save();return {status:state.status};}
    const route=this.relay.routeMessage?.(m,rows)||{peerId:this.relay.peerId||OWNER,private:false};
    if(route.hold){state.held[m.id]={reason:route.hold};state.cursor=m.id;delete state.candidates[m.id];changed=true;continue;}
    const destination=route.peerId;
    const c=state.candidates[m.id];if(!c||this.now()-c.since<this.stableMs)break;
    if(m.attachment_incomplete){state.held[m.id]={reason:'media_identity_requires_review',hash:c.hash};state.cursor=m.id;delete state.candidates[m.id];changed=true;continue;}
    if(m.format_incomplete){state.held[m.id]={reason:'format_requires_review',hash:c.hash};state.cursor=m.id;delete state.candidates[m.id];changed=true;continue;}
    const imageCount=m.attachment_count??m.attachment_names?.length??0;
    if(sensitiveText(m.text)||(!m.text.trim()&&!imageCount)){
     state.held[m.id]={reason:sensitiveText(m.text)?'sensitive_content':'no_visible_text',hash:c.hash};state.cursor=m.id;delete state.candidates[m.id];changed=true;continue;
    }
    let reply=route.private?{status:route.replyTo?'resolved':'none',receipt:route.replyTo||null}:resolveMirrorReply(this.relay,m.reply_to);
    let routingWarning=route.warning||null;
    if(reply.status==='unresolved'){
     if(this.relay.group&&typeof this.relay.routeMessage==='function'&&!route.private){
      routingWarning='telegram_reply_target_unavailable';reply={status:'none',receipt:null};
     }else{
      state.held[m.id]={reason:'reply_target_unresolved',hash:c.hash};state.cursor=m.id;delete state.candidates[m.id];changed=true;continue;
     }
    }
    const topic=route.private?{known:false}:mirrorTopic(this.relay,m);let autoChain=false;
    if(!route.private&&reply.status==='none'&&this.relay.group&&typeof this.relay.groupOptions==='function'){
     const context=this.relay.state.groupContext,topicId=context?.currentTopic??null;
     const previous=context?.lastByTopic?.[topicId??'direct'];
     autoChain=this.relay.groupOptions().chainPrevious===true;
     const receipt=chooseGroupReply({explicit:null,previous:previous?{messageId:previous,topicId}:null,topicId,enabled:autoChain});
     if(receipt)reply={status:'resolved',receipt};
    }
    let parts;try{parts=m.text.trim()?mirrorParts(m,this.relay.labels):[];}catch{state.held[m.id]={reason:'oversize_requires_review',hash:c.hash};state.cursor=m.id;delete state.candidates[m.id];changed=true;continue;}
    let formatted;try{formatted=mirrorPartEntities(m,parts);}catch{state.held[m.id]={reason:'format_requires_review',hash:c.hash};state.cursor=m.id;delete state.candidates[m.id];changed=true;continue;}
    let media=null,caption=null,captionEntities=[];
    if(imageCount){
     if(typeof this.relay.sendMedia!=='function'){state.status='media_transport_requires_review';this.relay.save();return {status:state.status};}
     // Download, validate and stage ALL images before any irreversible send.
     try{
      media=await this.relay.browserCall('dot_media_read',{tab_id:this.relay.tabId,source_id:m.id,expected_fingerprints:m.attachment_fingerprints});
      if(verifyDotTelegramMedia(media)!==imageCount)throw Error('media_count_changed');
     }catch{
      state.held[m.id]={reason:'media_source_requires_review',hash:c.hash};
      state.cursor=m.id;delete state.candidates[m.id];changed=true;continue;
     }
     const inline=parts.length===1&&parts[0].length<=900;
     const allDocuments=media.every(file=>file.kind==='document');
     const hasDocuments=media.some(file=>file.kind==='document');
     const attachmentLabel=allDocuments?(media.length===1?'[файл]':'[файлы]'):hasDocuments?'[вложения]':'[изображение]';
     caption=inline?parts[0]:mirrorParts({...m,text:attachmentLabel},this.relay.labels)[0];
     if(inline){captionEntities=formatted[0];parts=[];}
    }
    const pub={peer_id:destination,request_id:route.requestId||null,...(routingWarning?{warning:routingWarning}:{}),hash:c.hash,format_hash:c.format_hash,role:m.role,reply_to:m.reply_to||null,reply_receipt:reply.receipt,...(topic.known?{topic_id:topic.topicId}:{}),
     receipts:[],media_receipts:[],media_count:imageCount,
     media_sha256:media?.map(f=>f.sha256)||[],partHashes:parts.map(textHash),partFormats:formatted,status:'sending'};
    state.published[m.id]=pub;this.relay.save();
    if(imageCount){
     const key='mirror:'+m.id+':media:0';
     if(this.relay.state.outgoing[key]){state.blocked='delivery_requires_review';this.relay.save();return {status:state.blocked};}
     this.relay.state.outgoing[key]={status:'send_uncertain',source_id:m.id,kind:'media',
      media_sha256:pub.media_sha256,part:0};this.relay.save();
     try{
      // No competing claim: propagate the immutable original source identity.
      // Native claim fence integration belongs to the existing UserIO owner.
      const receipts=await this.relay.sendMedia(destination,media,caption,reply.receipt,{source_message_id:m.id},captionEntities);
      if(!Array.isArray(receipts)||!receipts.length||receipts.some(id=>!/^[1-9][0-9]*$/.test(String(id))))throw Error('media_provider_receipt_unknown');
      pub.media_receipts=receipts.map(String);pub.receipts.push(...pub.media_receipts);
      if(topic.known)for(const receipt of pub.media_receipts)observeGroupMessage(this.relay.state,{messageId:receipt,topicId:topic.topicId});
      this.relay.state.outgoing[key]={status:'accepted',source_id:m.id,kind:'media',part:0,receipts:pub.media_receipts};
      this.relay.save();
     }catch{state.blocked='delivery_unknown';this.relay.save();return {status:state.blocked};}
    }
    for(let n=0;n<parts.length;n++){
     const key='mirror:'+m.id+':'+n;
     if(this.relay.state.outgoing[key]){state.blocked='delivery_requires_review';this.relay.save();return {status:state.blocked};}
     this.relay.state.outgoing[key]={status:'send_uncertain',source_id:m.id,part:n};this.relay.save();
     const target=autoChain&&pub.receipts.length?pub.receipts.at(-1):reply.receipt;
     try{const receipt=await this.relay.send(destination,parts[n],target,formatted[n]);pub.receipts.push(String(receipt));if(topic.known)observeGroupMessage(this.relay.state,{messageId:String(receipt),topicId:topic.topicId});this.relay.state.outgoing[key]={status:'accepted',source_id:m.id,part:n,receipt:String(receipt)};this.relay.save();}
     catch{state.blocked='delivery_unknown';this.relay.save();return {status:state.blocked};}
    }
    pub.status='accepted';if(m.role==='assistant'&&route.requestId===this.relay.state.activeTurn)delete this.relay.state.activeTurn;state.cursor=m.id;delete state.candidates[m.id];changed=true;this.relay.save();
   }
   for(const m of edits){
    const pub=state.published[m.id],c=state.candidates[m.id];if(!c||this.now()-c.since<this.stableMs)continue;
    if(m.format_incomplete){state.held[m.id]={reason:'format_edit_requires_review',hash:c.hash};this.relay.save();continue;}
    if(pub.media_count||(m.attachment_count??m.attachment_names?.length??0)>0){state.held[m.id]={reason:'media_edit_requires_review',hash:c.hash};this.relay.save();continue;}
    if((m.reply_to||null)!==(pub.reply_to||null)){state.held[m.id]={reason:'reply_target_edited_requires_review',hash:c.hash};continue;}
    if(sensitiveText(m.text)||!m.text.trim()){state.held[m.id]={reason:'sensitive_or_empty_update',hash:c.hash};continue;}
    let parts;try{parts=mirrorParts(m,this.relay.labels);}catch{state.held[m.id]={reason:'oversize_update',hash:c.hash};continue;}
    let formatted;try{formatted=mirrorPartEntities(m,parts);}catch{state.held[m.id]={reason:'format_edit_requires_review',hash:c.hash};this.relay.save();continue;}
    if(parts.length!==pub.receipts.length){state.held[m.id]={reason:'edit_part_count_changed',hash:c.hash};continue;}
    if(!Array.isArray(pub.partFormats)||pub.partFormats.length!==parts.length){state.held[m.id]={reason:'edit_original_format_unknown',hash:c.hash};this.relay.save();continue;}
    if(typeof this.relay.edit!=='function'){state.blocked='edit_unavailable';this.relay.save();return {status:state.blocked};}
    pub.status='edit_uncertain';this.relay.save();
    try{for(let n=0;n<parts.length;n++)await this.relay.edit(pub.peer_id||this.relay.peerId||OWNER,pub.receipts[n],parts[n],pub.partHashes?.[n],formatted[n],pub.partFormats[n]);}
    catch{state.blocked='edit_unknown';this.relay.save();return {status:state.blocked};}
    pub.hash=c.hash;pub.format_hash=c.format_hash;pub.partHashes=parts.map(textHash);pub.partFormats=formatted;pub.status='accepted';delete state.held[m.id];delete state.candidates[m.id];changed=true;this.relay.save();
   }
   state.status=changed?'updated':newRows.length||edits.length?'stabilizing':'idle';state.lastObservedAt=this.now();this.relay.save();return {status:state.status,cursor:state.cursor};
  }finally{this.running=false;}
 }
}
