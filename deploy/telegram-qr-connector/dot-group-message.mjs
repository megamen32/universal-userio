import {hasSupportedImage,hasSupportedDocument} from './dot-media.mjs';
import {forwardOrigin,forwardLine} from './forward-origin.mjs';
// Preserve provider-supplied identity without treating it as trusted markup.
const line=value=>String(value||'').replace(/[\r\n\t\u0000-\u001f]+/g,' ').replace(/\s+/g,' ').trim().slice(0,200);
export function groupMediaKind(message){
 if(hasSupportedDocument(message))return 'document';
 if(hasSupportedImage(message))return 'image';
 if(!message?.media||['MessageMediaEmpty','MessageMediaWebPage'].includes(message.media.className))return 'text';
 return 'other_media';
}
export function groupDotText(sender,message,options={}){
 const id=String(sender?.id||'');
 if(!/^[1-9][0-9]*$/.test(id))throw Error('invalid_group_sender');
 const name=[line(sender.firstName),line(sender.lastName)].filter(Boolean).join(' ')
  ||(line(sender.username)?'@'+line(sender.username):'Участник Telegram '+id);
 const forwarded=message?.fwdFrom;
 const origin=forwarded&&forwardOrigin(message);
 const originText=origin?line(forwardLine(origin)):(forwarded&&line(forwarded.postAuthor)?'Переслано от: '+line(forwarded.postAuthor):'');
 const prefix=forwarded?(originText?'['+originText+']\n':'[Переслано; источник недоступен]\n'):'';
 const topic=options.showTopic===true&&options.topicId?'[Топик: '+(line(options.topicTitle)||'название недоступно')+']\n':'';
 return topic+name+':\n'+prefix+String(message?.message||'');
}
export function groupDotEntities(sender,message,options={}){
 const raw=String(message?.message||''),text=groupDotText(sender,message,options),prefix=text.length-raw.length;
 const kinds={MessageEntityBold:'bold',MessageEntityItalic:'italic',MessageEntityStrike:'strike',MessageEntityUnderline:'underline',MessageEntityCode:'code',MessageEntityPre:'pre',MessageEntityBlockquote:'blockquote',MessageEntityTextUrl:'text_url'};
 const plain=new Set(['MessageEntityUrl','MessageEntityEmail','MessageEntityMention','MessageEntityHashtag','MessageEntityBotCommand','MessageEntityCashtag','MessageEntityPhone']);
 return (message.entities||[]).flatMap(e=>{
  const type=e.className==='MessageEntityBlockquote'&&e.collapsed?null:kinds[e.className];if(!type&&plain.has(e.className))return [];
  // A range or link the Dot composer cannot represent is held for review as
  // an explicit unsupported entity, never thrown away with the message.
  if(!Number.isSafeInteger(e.offset)||!Number.isSafeInteger(e.length)||e.offset<0||e.length<1||e.offset+e.length>raw.length)
   return [{type:'unsupported',provider_type:line(e.className)||'unknown',offset:prefix+e.offset,length:e.length}];
  if(!type)return [{type:'unsupported',provider_type:line(e.className)||'unknown',offset:prefix+e.offset,length:e.length}];
  const item={type,offset:prefix+e.offset,length:e.length};
  if(type==='text_url'){let u;try{u=new URL(e.url)}catch{return [{type:'unsupported',provider_type:line(e.className)||'unknown',offset:prefix+e.offset,length:e.length}];}if(!['http:','https:'].includes(u.protocol))return [{type:'unsupported',provider_type:line(e.className)||'unknown',offset:prefix+e.offset,length:e.length}];item.url=u.href;}
  return [item];
 });
}
