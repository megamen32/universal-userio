// Personal connections use provider identity AND durable UserIO slot ownership.
export function accountRef(slot,jid,owners) {
 const own=String(jid||'').split(':')[0].split('@')[0];
 if(!/^[1-9][0-9]{5,19}$/.test(own))throw Error('Actual WhatsApp identity unavailable');
 return `whatsapp:${own}`+(owners.owners[slot]?`:${slot}`:'');
}
export function senderSlots(payload,slots,owners) {
 const user=String(payload.user_id||''),ref=String(payload.account_ref||'');
 const result=[];
 for(const [slot,item] of slots) {
  if(!item.socket||item.status!=='connected')continue;
  if(user||ref) {
   if(!user||!ref||!owners.allowed(slot,user))continue;
   if(accountRef(slot,item.socket.user?.id||item.name,owners)!==ref)continue;
  } else if(owners.owners[slot])continue; // Legacy APIs never select a personal session.
  if(payload.slot&&payload.slot!==slot)continue;
  result.push(slot);
 }
 if((user||ref)&&result.length!==1)throw Error('Exact owned WhatsApp account unavailable');
 return result;
}
export function personalEnvelope(slot,message,{ref,own,peer,reconciliation=false}) {
 const key=message.key||{},part=message.message||{};
 if(!key.id||!peer||peer==='status@broadcast')return null;
 const body=part.conversation||part.extendedTextMessage?.text||part.imageMessage?.caption||part.videoMessage?.caption||part.documentMessage?.caption||
  (part.imageMessage?'[Фото]':part.videoMessage?'[Видео]':part.documentMessage?'[Документ]':part.audioMessage?'[Голосовое сообщение]':part.stickerMessage?'[Стикер]':'');
 if(!body)return null;
 const group=peer.endsWith('@g.us'),out=key.fromMe===true;
 const author=out?own:group?String(key.participant||message.participant||''):peer;
 return {schema:'universal.inbox.message.v1',source:'whatsapp',message_id:`${ref}|${peer}:${key.id}`,
  sender:author||peer,peer_id:peer,conversation_kind:group?'group':'direct',
  direction:out?'outgoing':'incoming',body:String(body).slice(0,50000),
  ...(message.pushName&&!out?{sender_name:String(message.pushName).slice(0,128)}:{}),
  received_at:Number(message.messageTimestamp)||Date.now()/1000,reconciliation,
  attachments:[{kind:'whatsapp_routing',bridge_slot:slot,provider_ref:{chat_id:peer,message_id:String(key.id)}}]};
}
