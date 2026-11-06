import {telegramEntities} from './dot-format.mjs';
import {textHash} from './dot-relay.mjs';

const automatic=new Set(['MessageEntityUrl','MessageEntityEmail','MessageEntityMention','MessageEntityHashtag','MessageEntityBotCommand','MessageEntityCashtag','MessageEntityPhone']);
function entityIdentity(items){
 return JSON.stringify((items||[]).filter(e=>!automatic.has(e.className)).map(e=>JSON.stringify({type:e.className,offset:e.offset,length:e.length,...(e.url?{url:e.url}:{}),...(e.language?{language:e.language}:{})})).sort());
}
export async function editDotText({client,api,peer,messageId,expectedHash,text,entities,previousEntities}){
 if(!Number.isSafeInteger(messageId)||messageId<=0||typeof expectedHash!=='string'||!/^[a-f0-9]{64}$/.test(expectedHash)||typeof text!=='string'||!text.trim()||!Array.isArray(entities)||!Array.isArray(previousEntities))throw Error('dot_edit_invalid');
 const next=telegramEntities(text,entities,api);
 const before=(await client.getMessages(peer,{ids:[messageId]}))?.[0];
 const expected=telegramEntities(String(before?.message||''),previousEntities,api);
 if(!before||before.out!==true||Number(before.id)!==messageId||textHash(String(before.message||''))!==expectedHash||entityIdentity(before.entities)!==entityIdentity(expected)||before.media&&before.media.className!=='MessageMediaWebPage')throw Error('dot_edit_source_changed');
 await client.invoke(new api.messages.EditMessage({peer,id:messageId,message:text,entities:next,noWebpage:true}));
 const after=(await client.getMessages(peer,{ids:[messageId]}))?.[0];
 if(!after||after.out!==true||Number(after.id)!==messageId||String(after.message||'')!==text||entityIdentity(after.entities)!==entityIdentity(next))throw Error('dot_edit_unknown');
 return {messageId:String(messageId)};
}
