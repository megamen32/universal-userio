import {createHash} from 'node:crypto';

const numeric=value=>typeof value==='string'&&/^[1-9][0-9]*$/.test(value)
 && Number.isSafeInteger(Number(value));
const digest=text=>createHash('sha256').update(String(text)).digest('hex');

// The dot/browser ID is never the canonical UserIO source_message_id.
// Only a verified incoming Telegram echo supplies the ORIGINAL provider ID.
export function findOriginalTelegramQuestion(relay,message,rows,owner){
 const incoming=relay?.state?.incoming;
 if(!incoming||typeof incoming!=='object'||!Array.isArray(rows)
  ||!message||typeof message.id!=='string')return {status:'unverified'};
 let target=message.reply_to||null;
 let via='reply';
 if(target==null&&message.role==='assistant'){
  const pos=rows.findIndex(r=>r.id===message.id);
  const previous=pos<0?null:rows.slice(0,pos).reverse().find(r=>r.role==='user');
  target=previous?.id||null;
  via='nearest_user';
 }
 if(target==null)return {status:'none'};
 if(typeof target!=='string'||!target)return {status:'unverified'};
 const prior=relay.state.mirror?.published?.[target];
 if(prior?.question_source){
  // One question has ONE canonical outbound action. A second result needs
  // explicit human review, never a fresh provider message.
  return {status:'already_answered',source:prior.question_source};
 }
 const prefix=relay.botAccountId+':'+owner+':';
 const matching=Object.entries(incoming).filter(([k,v])=>
  k.startsWith(prefix)&&v?.source==='telegram'&&v.echo===target);
 if(matching.length===0){
  // An inbound Telegram operation can be submitted but its dot echo still
  // UNKNOWN. A visually matching question must not then escape the same
  // original provider fence via ordinary assistant mirroring.
  const original=rows.find(r=>r.id===target&&r.role==='user');
  if(typeof original?.text==='string'&&Object.entries(incoming).some(([k,v])=>
   k.startsWith(prefix)&&v?.source==='telegram'
   &&v.status!=='ignored'&&v.status!=='not_accepted'
   &&v.text_hash===digest(original.text)))
    return {status:'unverified'};
  return {status:'none'};
 }
 if(matching.length!==1)return {status:'unverified'};
 const [key,entry]=matching[0],providerId=key.slice(prefix.length);
 if(entry.status!=='accepted'||!numeric(providerId)
  ||!Array.isArray(entry.source_message_ids)
  ||entry.source_message_ids.length!==1||entry.source_message_ids[0]!==providerId)
  return {status:'unverified'};
 if(!rows.some(r=>r.id===target&&r.role==='user'))return {status:'unverified'};
 return {status:'linked',via,source:{
  account_ref:relay.botAccountId,
  peer_id:owner,
  source_provider_message_id:providerId,
  echoed_dot_message_id:target,
 }};
}

// The trusted adapter MUST wrap the single existing Messenger.approve action;
// it may not call a second Telegram provider sender or mint its own lease.
// No opaque lease_token is persisted in the mirror journal.
export function verifiedOriginalMessengerReceipt(source,body,result){
 if(!source||!result||typeof result!=='object'
  ||result.status!=='accepted'||result.question_delivery_state!=='sent'
  ||result.account_ref!==source.account_ref
  ||result.peer_id!==source.peer_id
  ||result.source_provider_message_id!==source.source_provider_message_id
  ||!numeric(source.source_provider_message_id)
  ||typeof result.source_message_id!=='string'
  ||result.source_message_id.length<8||result.source_message_id.length>250
  ||result.source_message_id.includes('CalpicoMessage')
  // Check the persisted UserIO Telegram source shape without substituting
  // this calculated value for the backend's canonical source_message_id.
  ||result.source_message_id!==source.account_ref+'|'+source.peer_id+':'+source.source_provider_message_id
  ||typeof result.conversation_id!=='string'||!result.conversation_id
  ||!Number.isSafeInteger(result.event_seq)||result.event_seq<=0
  ||typeof result.revision!=='string'||result.revision.length>128
  ||!Number.isSafeInteger(result.epoch)||result.epoch<=0
  ||typeof result.request_id!=='string'
  ||!/^[a-zA-Z0-9:_-]{16,128}$/.test(result.request_id)
  ||typeof result.action_id!=='string'
  ||!/^action_[a-f0-9]{64}$/.test(result.action_id)
  ||result.receipt_ref!=='messenger:'+result.action_id
  ||result.message_sha256!==digest(body)
  ||!numeric(result.provider_message_id))
  throw Error('original_messenger_receipt_requires_review');
 // This normalizes a verified original Messenger action, not a new send.
 return {
  account_ref:source.account_ref,peer_id:source.peer_id,
  source_provider_message_id:source.source_provider_message_id,
  source_message_id:result.source_message_id,
  conversation_id:result.conversation_id,
  action_id:result.action_id,request_id:result.request_id,
  receipt_ref:result.receipt_ref,provider_message_id:result.provider_message_id,
  event_seq:result.event_seq,revision:result.revision,epoch:result.epoch,
 };
}
