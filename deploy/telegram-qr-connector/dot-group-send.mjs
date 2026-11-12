import {verifyDotTelegramMedia} from './dot-egress.mjs';
function formatEntities(text,entities){
 if(!Array.isArray(entities))throw Error('invalid_group_entities');
 return entities.map(e=>{
  const type=e.type==='text_url'?'text_link':e.type;
  if(!['text_link','bold','italic','strike','underline','code','pre','blockquote'].includes(type)||!Number.isSafeInteger(e.offset)||!Number.isSafeInteger(e.length)||e.offset<0||e.length<1||e.offset+e.length>text.length)throw Error('invalid_group_entity');
  if(type==='text_link'){let u;try{u=new URL(e.url)}catch{throw Error('invalid_group_entity')};if(!['http:','https:'].includes(u.protocol))throw Error('invalid_group_entity');return {type,offset:e.offset,length:e.length,url:u.href};}
  return {type,offset:e.offset,length:e.length};
 });
}
const hasEntities=(expected,actual)=>expected.every(e=>Array.isArray(actual)&&actual.some(a=>a.type===e.type&&a.offset===e.offset&&a.length===e.length&&(e.type!=='text_link'||a.url===e.url)));
// One Bot API request per already journalled relay delivery; never retry here.
export function createGroupBotSender({token,peerId,botId,fetcher=fetch,allowPrivate=false}){
 if(!/^[0-9]{5,20}:[A-Za-z0-9_-]{30,80}$/.test(token)||token.split(':')[0]!==botId||!(/^\-100[1-9][0-9]+$/.test(peerId)||(allowPrivate&&/^[1-9][0-9]{0,19}$/.test(peerId))))throw Error('invalid_group_sender');
 return async(peer,text,replyTo,entities=[])=>{
  if(peer!==peerId||typeof text!=='string'||!text.trim()||text.length>4096)throw Error('invalid_group_send');
  const args={chat_id:peerId,text,link_preview_options:{is_disabled:true}};
  const formatted=formatEntities(text,entities);if(formatted.length)args.entities=formatted;
  if(replyTo!=null){if(!/^[1-9][0-9]*$/.test(String(replyTo)))throw Error('invalid_group_reply');args.reply_parameters={message_id:Number(replyTo)}}
  let response;try{response=await fetcher('https://api.telegram.org/bot'+token+'/sendMessage',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(args),signal:AbortSignal.timeout(20000)});}catch{throw Error('group_send_unknown');}
  if(!response.ok)throw Error('group_send_unknown');
  let d;try{d=await response.json()}catch{throw Error('group_send_unknown')}
  if(d.ok!==true||String(d.result?.chat?.id)!==peerId||String(d.result?.from?.id)!==botId||!Number.isSafeInteger(d.result?.message_id)||d.result.message_id<=0)throw Error('group_send_unknown');
  if(!hasEntities(formatted,d.result.entities))throw Error('group_send_format_unknown');
  return d.result.message_id;
 };
}
export function createGroupBotMediaSender({token,peerId,botId,fetcher=fetch,allowPrivate=false}){
 if(!/^[0-9]{5,20}:[A-Za-z0-9_-]{30,80}$/.test(token)||token.split(':')[0]!==botId||!(/^\-100[1-9][0-9]+$/.test(peerId)||(allowPrivate&&/^[1-9][0-9]{0,19}$/.test(peerId))))throw Error('invalid_group_sender');
 return async(peer,media,caption,replyTo=null,entities=[])=>{
  if(peer!==peerId||typeof caption!=='string'||!caption.trim()||caption.length>900)throw Error('invalid_group_media');
  if(replyTo!=null&&(!/^[1-9][0-9]*$/.test(String(replyTo))||!Number.isSafeInteger(Number(replyTo))))throw Error('invalid_group_reply');
  verifyDotTelegramMedia(media);const formatted=formatEntities(caption,entities);
  const form=new FormData();form.set('chat_id',peerId);
  if(replyTo!=null)form.set('reply_parameters',JSON.stringify({message_id:Number(replyTo)}));
  let method;const documents=media.some(m=>m.kind==='document');
  if(media.length===1){method=documents?'sendDocument':'sendPhoto';form.set(documents?'document':'photo',new Blob([media[0].bytes],{type:media[0].mime}),media[0].filename);form.set('caption',caption);if(formatted.length)form.set('caption_entities',JSON.stringify(formatted));}
  else{
   method='sendMediaGroup';
   form.set('media',JSON.stringify(media.map((file,i)=>({type:documents?'document':'photo',media:'attach://file'+i,...(i===0?{caption,...(formatted.length?{caption_entities:formatted}:{})}:{})}))));
   media.forEach((file,i)=>form.set('file'+i,new Blob([file.bytes],{type:file.mime}),file.filename));
  }
  let response;try{response=await fetcher('https://api.telegram.org/bot'+token+'/'+method,{method:'POST',body:form,signal:AbortSignal.timeout(60000)});}catch{throw Error('group_media_unknown');}
  if(!response.ok)throw Error('group_media_unknown');
  let d;try{d=await response.json()}catch{throw Error('group_media_unknown')}
  const messages=Array.isArray(d.result)?d.result:[d.result];
  if(d.ok!==true||messages.length!==media.length||messages.some(m=>String(m?.chat?.id)!==peerId||String(m?.from?.id)!==botId||!Number.isSafeInteger(m?.message_id)||m.message_id<=0)||new Set(messages.map(m=>m.message_id)).size!==messages.length)throw Error('group_media_unknown');
  if(!hasEntities(formatted,messages[0].caption_entities))throw Error('group_media_format_unknown');
  return messages.map(m=>String(m.message_id));
 };
}
