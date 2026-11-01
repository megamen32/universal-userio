import {DOT_POLICY} from './dot-policy.mjs';
import {OWNER,ROOM,textHash} from './dot-relay.mjs';
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
export const mirrorSendOptions=text=>({message:text,formattingEntities:[],linkPreview:false});
export function mirrorParts(message){
 const label=message.role==='user'?(DOT_POLICY?.ownerLabel||'Owner'):(DOT_POLICY?.assistantLabel||'Assistant');const text=String(message.text||'');
 const pieces=[];let offset=0;
 while(offset<text.length){let end=Math.min(offset+3000,text.length);if(end<text.length&&/[\uD800-\uDBFF]/.test(text[end-1]))end--;pieces.push(text.slice(offset,end));offset=end;if(pieces.length>12)throw Error('message_too_large');}
 return pieces.map((body,i)=>label+(pieces.length>1?` (${i+1}/${pieces.length})`:'')+':\n'+body);
}
export class DotMirror{
 constructor(relay,{now=()=>Date.now(),stableMs=8000}={}){this.relay=relay;this.now=now;this.stableMs=stableMs;this.running=false;}
 async tick(){
  if(this.running||this.relay.busy)return {status:'incoming_busy'};
  this.running=true;
  try{
   let state=this.relay.state.mirror;
   if(state?.blocked)return {status:state.blocked};
   if(state&&Object.values(state.published).some(p=>['sending','edit_uncertain'].includes(p.status))){state.blocked='previous_operation_unknown';this.relay.save();return {status:state.blocked};}
   if(state?.nextReadAttemptAt>this.now())return {status:'read_backoff'};
   let snap;
   try{snap=await this.relay.browserCall('dot_snapshot',{tab_id:this.relay.tabId});}
   catch{
    if(state){state.status='snapshot_read_failed';state.lastReadErrorAt=this.now();
     state.readFailures=(state.readFailures||0)+1;
     state.nextReadAttemptAt=this.now()+Math.min(300000,30000*2**Math.min(state.readFailures-1,4));
     this.relay.save();}
    return {status:'snapshot_read_failed'};
   }
   if(state){state.readFailures=0;delete state.nextReadAttemptAt;delete state.lastReadErrorAt;}
   if(!snap.ok||snap.room!==ROOM||!snap.cursor)return {status:'snapshot_unavailable'};
   const rows=snap.messages||[];
   if(!state){state=this.relay.state.mirror={baselineAt:this.now(),cursor:snap.cursor,candidates:{},published:{},held:{}};this.relay.save();return {status:'baseline_initialized',cursor:state.cursor};}
   const index=rows.findIndex(m=>m.id===state.cursor);
   if(index<0){state.status='cursor_not_visible';this.relay.save();return {status:state.status};}
   const newRows=rows.slice(index+1);
   const edits=rows.filter(m=>state.published[m.id]&&state.published[m.id].hash!==textHash(m.text));
   for(const m of [...newRows,...edits]){
    if(!['user','assistant'].includes(m.role))continue;
    const hash=textHash(m.text);const old=state.candidates[m.id];
    if(!old||old.hash!==hash)state.candidates[m.id]={hash,since:this.now()};
   }
   this.relay.save();let changed=false;
   for(const m of newRows){
    if(!['user','assistant'].includes(m.role)){state.cursor=m.id;continue;}
    const origin=this.relay.mirrorOrigin(m);
    if(origin==='uncertain'){state.status='incoming_origin_uncertain';this.relay.save();return {status:state.status};}
    if(origin==='telegram'){state.cursor=m.id;delete state.candidates[m.id];changed=true;continue;}
    const c=state.candidates[m.id];if(!c||this.now()-c.since<this.stableMs)break;
    if(sensitiveText(m.text)||!m.text.trim()){
     state.held[m.id]={reason:sensitiveText(m.text)?'sensitive_content':'no_visible_text',hash:c.hash};state.cursor=m.id;delete state.candidates[m.id];changed=true;continue;
    }
    let parts;try{parts=mirrorParts(m);}catch{state.held[m.id]={reason:'oversize_requires_review',hash:c.hash};state.cursor=m.id;delete state.candidates[m.id];changed=true;continue;}
    const pub={hash:c.hash,role:m.role,receipts:[],partHashes:parts.map(textHash),status:'sending'};state.published[m.id]=pub;this.relay.save();
    for(let n=0;n<parts.length;n++){
     const key='mirror:'+m.id+':'+n;
     if(this.relay.state.outgoing[key]){state.blocked='delivery_requires_review';this.relay.save();return {status:state.blocked};}
     this.relay.state.outgoing[key]={status:'send_uncertain',source_id:m.id,part:n};this.relay.save();
     try{const receipt=await this.relay.send(OWNER,parts[n]);pub.receipts.push(String(receipt));this.relay.state.outgoing[key]={status:'accepted',source_id:m.id,part:n,receipt:String(receipt)};this.relay.save();}
     catch{state.blocked='delivery_unknown';this.relay.save();return {status:state.blocked};}
    }
    pub.status='accepted';state.cursor=m.id;delete state.candidates[m.id];changed=true;this.relay.save();
   }
   for(const m of edits){
    const pub=state.published[m.id],c=state.candidates[m.id];if(!c||this.now()-c.since<this.stableMs)continue;
    if(sensitiveText(m.text)||!m.text.trim()){state.held[m.id]={reason:'sensitive_or_empty_update',hash:c.hash};continue;}
    let parts;try{parts=mirrorParts(m);}catch{state.held[m.id]={reason:'oversize_update',hash:c.hash};continue;}
    if(parts.length!==pub.receipts.length){state.held[m.id]={reason:'edit_part_count_changed',hash:c.hash};continue;}
    if(typeof this.relay.edit!=='function'){state.blocked='edit_unavailable';this.relay.save();return {status:state.blocked};}
    pub.status='edit_uncertain';this.relay.save();
    try{for(let n=0;n<parts.length;n++)await this.relay.edit(OWNER,pub.receipts[n],parts[n],pub.partHashes?.[n]);}
    catch{state.blocked='edit_unknown';this.relay.save();return {status:state.blocked};}
    pub.hash=c.hash;pub.partHashes=parts.map(textHash);pub.status='accepted';delete state.candidates[m.id];changed=true;this.relay.save();
   }
   state.status=changed?'updated':newRows.length||edits.length?'stabilizing':'idle';state.lastObservedAt=this.now();this.relay.save();return {status:state.status,cursor:state.cursor};
  }finally{this.running=false;}
 }
}
