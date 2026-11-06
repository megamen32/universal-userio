import {hasSupportedImage} from './dot-media.mjs';
// Preserve provider-supplied identity without treating it as trusted markup.
const line=value=>String(value||'').replace(/[\r\n\t\u0000-\u001f]+/g,' ').replace(/\s+/g,' ').trim().slice(0,200);
export function groupMediaKind(message){
 if(hasSupportedImage(message))return 'image';
 if(!message?.media||['MessageMediaEmpty','MessageMediaWebPage'].includes(message.media.className))return 'text';
 return 'other_media';
}
export function groupDotText(sender,message){
 const id=String(sender?.id||'');
 if(!/^[1-9][0-9]*$/.test(id))throw Error('invalid_group_sender');
 const name=[line(sender.firstName),line(sender.lastName)].filter(Boolean).join(' ')
  ||(line(sender.username)?'@'+line(sender.username):'Участник Telegram '+id);
 const forwarded=message?.fwdFrom;
 const origin=forwarded&&(line(forwarded.fromName)||line(forwarded.postAuthor));
 const prefix=forwarded?(origin?'[Переслано от '+origin+']\n':'[Переслано; источник недоступен]\n'):'';
 return name+':\n'+prefix+String(message?.message||'');
}
export function groupDotEntities(sender,message){
 const raw=String(message?.message||''),text=groupDotText(sender,message),prefix=text.length-raw.length;
 const kinds={MessageEntityBold:'bold',MessageEntityItalic:'italic',MessageEntityStrike:'strike',MessageEntityUnderline:'underline',MessageEntityCode:'code',MessageEntityPre:'pre',MessageEntityTextUrl:'text_url'};
 const plain=new Set(['MessageEntityUrl','MessageEntityEmail','MessageEntityMention','MessageEntityHashtag','MessageEntityBotCommand','MessageEntityCashtag','MessageEntityPhone']);
 return (message.entities||[]).flatMap(e=>{
  const type=kinds[e.className];if(!type&&plain.has(e.className))return [];
  if(!Number.isSafeInteger(e.offset)||!Number.isSafeInteger(e.length)||e.offset<0||e.length<1||e.offset+e.length>raw.length)throw Error('invalid_telegram_format');
  if(!type)return [{type:'unsupported',provider_type:line(e.className)||'unknown',offset:prefix+e.offset,length:e.length}];
  const item={type,offset:prefix+e.offset,length:e.length};
  if(type==='text_url'){let u;try{u=new URL(e.url)}catch{throw Error('invalid_telegram_link')};if(!['http:','https:'].includes(u.protocol))throw Error('invalid_telegram_link');item.url=u.href;}
  return [item];
 });
}
