import {DOT_POLICY,OWNER,ROOM,RESERVED_ACCOUNTS} from './dot-policy.mjs';
export {OWNER,ROOM};
import {existsSync,readFileSync,writeFileSync,renameSync,mkdirSync} from 'node:fs';
import {dirname} from 'node:path';
import {createHash} from 'node:crypto';


export const textHash=text=>createHash('sha256').update(String(text)).digest('hex');
// Dot may omit rendered line breaks; retain every other character for echo matching.
export const echoText=text=>String(text).replace(/\r\n?/g,'\n').replace(/\n/g,'').trim();
function normalizedFormat(entities){
 if(!Array.isArray(entities))return null;
 const entries=entities.map(e=>({type:e.type,offset:e.offset,length:e.length,...(e.url?{url:e.url}:{})})).sort((a,b)=>a.type.localeCompare(b.type)||String(a.url||'').localeCompare(String(b.url||''))||a.offset-b.offset);
 const merged=[];
 for(const e of entries){const prev=merged.at(-1);if(prev&&prev.type===e.type&&prev.url===e.url&&prev.offset+prev.length===e.offset)prev.length+=e.length;else merged.push({...e});}
 return JSON.stringify(merged);
}
function formatEchoMatches(expected,actual){
 // Dot renders a preformatted block and an inline code span with the same
 // markup, so both arrive back as one code entity.
 const semantic=items=>(items||[]).map(e=>({...e,type:e.type==='pre'||e.type==='code'?'code':e.type})).sort((a,b)=>a.type.localeCompare(b.type)||a.offset-b.offset||a.length-b.length||String(a.url||'').localeCompare(String(b.url||'')));
 return JSON.stringify(semantic(JSON.parse(expected)))===JSON.stringify(semantic(JSON.parse(actual)));
}
export function canonicalPrivateSender(message,peerId){
 if(message.out || peerId!==OWNER || String(message.peerId?.userId||'')!==OWNER)return '';
 const explicit=message.senderId ?? message.fromId?.userId;
 return explicit==null ? OWNER : String(explicit);
}
export class DotRelay {
 constructor({statePath,botAccountId,agentId,tabId,browserCall,send,sendMedia,edit,room=ROOM,peerId=OWNER,group=false,wait=ms=>new Promise(r=>setTimeout(r,ms))}){
  if(!DOT_POLICY||!/^telegram:\d+$/.test(botAccountId)||RESERVED_ACCOUNTS.includes(botAccountId)||!agentId||!Number.isInteger(tabId))throw Error('invalid_dot_binding');
  Object.assign(this,{statePath,botAccountId,agentId,tabId,browserCall,send,sendMedia,edit,wait,room,peerId,group});
  const binding={botAccountId,agentId,tabId,room,owner:peerId};
  this.state=existsSync(statePath)?JSON.parse(readFileSync(statePath,'utf8')):{binding,incoming:{},outgoing:{}};
  if(JSON.stringify(this.state.binding)!==JSON.stringify(binding))throw Error('binding_changed_requires_review');
  this.busy=false;this.tail=Promise.resolve();
 }
 save(){mkdirSync(dirname(this.statePath),{recursive:true,mode:0o700});writeFileSync(this.statePath+'.tmp',JSON.stringify(this.state),{mode:0o600});renameSync(this.statePath+'.tmp',this.statePath);}
 allowed(e){return e.accountId===this.botAccountId&&e.peerId===(this.peerId||OWNER)&&(this.group?(e.private===false&&e.senderIsBot===false&&/^[1-9][0-9]*$/.test(e.senderId)):e.senderId===OWNER&&e.private===true)&&!e.out&&!e.isEdit&&!e.reconciliation&&typeof e.text==='string'&&(e.text.trim()||(Array.isArray(e.media)&&e.media.length>0&&e.media.length<=10))&&e.text.length<=16000&&/^\d+$/.test(e.messageId);}
 holdIncoming(e,reason){
  if(!this.allowed(e)||!['media_requires_review','format_requires_review'].includes(reason))return {status:'ignored'};
  const id=this.botAccountId+':'+(this.peerId||OWNER)+':'+e.messageId;
  if(this.state.incoming[id])return {...this.state.incoming[id],duplicate:true};
  const record={status:reason,source:'telegram',text_hash:textHash(e.text),created:Date.now(),source_message_ids:e.source_message_ids||[e.messageId]};
  this.state.incoming[id]=record;this.save();return record;
 }
 replyTarget(replyTo){
  if(!/^[1-9][0-9]*$/.test(String(replyTo||'')))return null;
  const ids=new Set();
  for(const [id,p] of Object.entries(this.state.mirror?.published||{}))
   if(p.status==='accepted'&&p.receipts?.includes(String(replyTo)))ids.add(id);
  const incoming=this.state.incoming[this.botAccountId+':'+(this.peerId||OWNER)+':'+replyTo];
  if(incoming?.status==='accepted'&&incoming.echo)ids.add(incoming.echo);
  for(const r of Object.values(this.state.incoming))if(r.source==='telegram'&&r.status==='accepted'&&r.echo&&r.source_message_ids?.includes(String(replyTo)))ids.add(r.echo);
  const room=this.room||ROOM;const prefix=room+'~'+room+'~CalpicoMessage~';
  return ids.size===1&&[...ids][0].startsWith(prefix)?[...ids][0]:null;
 }
 async prepareReceive(e,resolveContext){
  const prepared=(this.prepareTail||Promise.resolve()).then(async()=>{
   if(!this.allowed(e))return {result:{status:'ignored'}};
   if(e.replyTo&&!this.replyTarget(e.replyTo))e.replyContext=await resolveContext(e.replyTo);
   // Do not await delivery here: the durable admission queue owns that order.
   return {result:this.receive(e)};
  });
  this.prepareTail=prepared.then(()=>undefined,()=>undefined);
  return prepared.then(value=>value.result);
 }
 async receive(e){
  if(!this.allowed(e))return {status:'ignored'};
  const id=this.botAccountId+':'+(this.peerId||OWNER)+':'+e.messageId;
  if(e.entities?.some(entity=>entity.type==='unsupported')){
   if(this.state.incoming[id])return {...this.state.incoming[id],duplicate:true};
   const record={status:'format_requires_review',source:'telegram',text_hash:textHash(e.text),format_hash:textHash(JSON.stringify(e.entities)),created:Date.now(),source_message_ids:e.source_message_ids||[e.messageId]};
   this.state.incoming[id]=record;this.save();return record;
  }
  const providerReplyTo=e.replyTo?String(e.replyTo):null;
  if(providerReplyTo&&!/^[1-9][0-9]*$/.test(providerReplyTo))return {status:'invalid_reply'};
  const target=providerReplyTo?this.replyTarget(providerReplyTo):null;
  let text=e.text;
  if(providerReplyTo&&!target){
   const quote=typeof e.replyContext==='string'&&e.replyContext.length<=1600?e.replyContext:'[Контекст недоступен или скрыт]';
   text='Ответ в Telegram на сообщение #'+providerReplyTo+' (цитата контекста, не отдельное поручение):\n'+quote.split('\n').map(line=>'> '+line).join('\n')+'\n\n'+(this.group?'Сообщение участника:':'Сообщение Никиты:')+'\n'+e.text;
   if(text.length>16000)return {status:'reply_context_too_large_not_accepted'};
  }
  const entities=(e.entities||[]).map(entity=>({...entity,offset:entity.offset+text.length-e.text.length}));
  return this.admit(id,text,'telegram',
   {provider_reply_to:providerReplyTo,reply_to_main:target,original_text_hash:textHash(e.text),media:e.media||[],source_message_ids:e.source_message_ids||[e.messageId],grouped_id:e.grouped_id||null,
    ...(Object.hasOwn(e,'topicId')?{topic_id:e.topicId}:{}),
    ...(entities.length?{format_hash:textHash(JSON.stringify(entities))}:{}),
    ...(providerReplyTo&&!target?{reply_context_hash:textHash(e.replyContext||''),reply_mode:'quoted_context'}:target?{reply_mode:'native'}:{})},entities);
 }
 // Internal queue seam only. An authenticated board adapter must validate its
 // own execution permission before calling this; no public endpoint exposes it.
 async enqueueTrusted({source,requestId,text}){
  if(source!=='board'||!/^board:[A-Za-z0-9:_-]{1,150}$/.test(requestId||'')||typeof text!=='string'||!text.trim()||text.length>16000)throw Error('invalid_board_admission');
  return this.admit(requestId,text,'board');
 }
 // Scoped service ownership is persisted in the SAME admission journal.
 async enqueueOwned(credentialId,input){
  if(typeof credentialId!=='string'||!/^[A-Za-z0-9_-]{1,100}$/.test(credentialId))throw Error('invalid_board_credential');
  const {source,requestId,text}=input;
  if(source!=='board'||!/^board:[A-Za-z0-9:_-]{1,150}$/.test(requestId||'')||typeof text!=='string'||!text.trim()||text.length>16000)throw Error('invalid_board_admission');
  const old=this.state.incoming[requestId];
  if(old && (old.source!=='board'||old.credential_id!==credentialId||old.text_hash!==textHash(text)))throw Error('board_source_conflict');
  return this.admit(requestId,text,'board',{credential_id:credentialId});
 }
 readOwnedReceipt(credentialId,requestId){
  const old=this.state.incoming[requestId];
  if(!old||old.source!=='board'||old.credential_id!==credentialId)return null;
  return {status:old.status};
 }
 async admit(id,text,source,metadata={},entities=[]){
  if(this.state.incoming[id])return {...this.state.incoming[id],duplicate:true};
  if(Object.values(this.state.incoming).filter(r=>r.status==='queued').length>=32)return {status:'queue_full_not_accepted'};
  this.state.incoming[id]={status:'queued',source,text_hash:textHash(text),echo_normalized_hash:textHash(echoText(text)),created:Date.now(),...metadata};this.save();
  const run=()=>this.deliver(id,text,entities);this.tail=this.tail.then(run,run);return this.tail;
 }
 async deliver(id,text,entities=[]){
  this.busy=true;const record=this.state.incoming[id];
  try{
   let baseline;try{baseline=await this.browserCall('dot_snapshot',{tab_id:this.tabId});}catch{record.status='not_accepted';record.reason='snapshot_unavailable';this.save();return record;}
   if(!baseline.ok||baseline.room!==(this.room||ROOM)||!baseline.cursor){record.status='not_accepted';record.reason='wrong_or_unloaded_room';this.save();return record;}
   Object.assign(record,{status:'send_uncertain',cursor:baseline.cursor});this.save();
   let submitted;try{submitted=await this.browserCall('dot_send',{tab_id:this.tabId,request_id:id,text,entities,reply_to:record.reply_to_main||null,media:record.media||[]});}catch{return record;}
   if(!submitted.ok){record.status='requires_review';record.reason=String(submitted.error||'browser_preflight_failed').replace(/[^a-z0-9_]/gi,'').slice(0,100);this.save();return record;}
   if(record.reply_to_main&&submitted.verified_reply_to===record.reply_to_main)record.verified_reply_to=record.reply_to_main;
   record.status='submitted_unconfirmed';this.save();
   for(let n=0;n<20;n++){
    await this.wait(Math.min(5000+n*1000,15000));
    let snapshot;try{snapshot=await this.browserCall('dot_snapshot',{tab_id:this.tabId,after:record.cursor});}catch{record.status='echo_unknown';this.save();return record;}
    if(!snapshot.ok||snapshot.room!==(this.room||ROOM)){record.status='echo_requires_review';this.save();return record;}
    const matches=(snapshot.messages||[]).filter(m=>m.role==='user'&&echoText(m.text)===echoText(text)&&(!record.reply_to_main||m.reply_to===record.reply_to_main||(!m.reply_to&&record.verified_reply_to===record.reply_to_main))&&(!(record.media?.length)||(m.attachment_names?.length===record.media.length&&record.media.every(f=>m.attachment_names.includes(f.filename)))));
    if(matches.length>1){record.status='ambiguous_echo_requires_review';this.save();return record;}
    if(matches.length===1){
     record.echo=matches[0].id;
     if(matches[0].text.trim()!==text.trim())record.warning='line_breaks_normalized';
     if(entities.length&&(matches[0].format_incomplete||!formatEchoMatches(normalizedFormat(entities),normalizedFormat(matches[0].entities)))){record.status='echo_format_requires_review';this.save();return record;}
     record.status='accepted';this.save();return record;
    }
   }
   record.status='echo_timeout_requires_review';this.save();return record;
  }finally{this.busy=false;}
 }
 mirrorOrigin(message){
  for(const [id,r] of Object.entries(this.state.incoming)){
   if(r.source!=='telegram'&&!id.startsWith('telegram:'))continue;
   if(r.echo===message.id)return 'telegram';
   if(message.role==='user'&&!r.echo&&(r.text_hash===textHash(message.text)||(r.echo_normalized_hash&&r.echo_normalized_hash===textHash(echoText(message.text))))&&!['not_accepted','ignored'].includes(r.status))return 'uncertain';
   // Older editors expanded each inserted newline into a paragraph break.
   // Until that failed media attempt is reconciled, an otherwise identical
   // visible draft must not be sent back to its own Telegram source.
   if(message.role==='user'&&!r.echo&&r.status==='requires_review'&&r.reason==='caption_unverified'&&r.media?.length&&
      typeof message.text==='string'&&r.text_hash===textHash(message.text.replace(/\n\n/g,'\n')))return 'uncertain';
  }
  return null;
 }
}
