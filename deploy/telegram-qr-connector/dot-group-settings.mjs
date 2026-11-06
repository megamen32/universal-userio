import {lstatSync,readFileSync,openSync,closeSync,writeFileSync,fsyncSync,renameSync,unlinkSync} from 'node:fs';
import {dirname} from 'node:path';import {randomUUID} from 'node:crypto';
const id=value=>typeof value==='string'&&/^[1-9][0-9]*$/.test(value)&&Number.isSafeInteger(Number(value));
export function groupSettings(value){
 if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).some(k=>!['showTopic','chainPrevious'].includes(k)))throw Error('invalid_group_settings');
 for(const key of ['showTopic','chainPrevious'])if(value[key]!==undefined&&typeof value[key]!=='boolean')throw Error('invalid_group_settings');
 return {showTopic:value.showTopic??false,chainPrevious:value.chainPrevious??false};
}
export function chooseGroupReply({explicit,previous,topicId,enabled}){
 if(explicit!=null){if(!id(String(explicit)))throw Error('invalid_group_reply');return String(explicit);}
 if(enabled!==true||!previous||!id(previous.messageId)||previous.topicId!==(topicId??null))return null;
 return previous.messageId;
}
// Match the official Telegram forum topic rules: absent forum header means
// General only when the chat was independently confirmed to be a forum.
export function groupTopicId(message,isForum){
 if(isForum!==true)return null;
 const reply=message?.replyTo;
 if(!reply?.forumTopic)return '1';
 const candidate=String(reply.replyToTopId||reply.replyToMsgId||'');
 return id(candidate)?candidate:null;
}
export function observeGroupMessage(state,{messageId,topicId}){
 if(!id(messageId)||(topicId!==null&&!id(topicId)))throw Error('invalid_group_observation');
 const context=state.groupContext||={currentTopic:null,currentMessageId:null,lastByTopic:{}};
 const key=topicId??'direct',old=context.lastByTopic[key];
 if(old&&Number(old)>=Number(messageId))return false;
 context.lastByTopic[key]=messageId;
 if(!context.currentMessageId||Number(context.currentMessageId)<Number(messageId)){
  context.currentMessageId=messageId;context.currentTopic=topicId;
 }
 return true;
}
export async function resolveGroupTopicTitle({client,api,peer,topicId,getInputChannel}){
 if(!id(topicId)||!client||!api?.channels?.GetForumTopicsByID||typeof getInputChannel!=='function')return null;
 try{
  const channel=getInputChannel(await client.getInputEntity(peer));
  const response=await client.invoke(new api.channels.GetForumTopicsByID({channel,topics:[Number(topicId)]}));
  const matches=(response?.topics||[]).filter(t=>String(t.id)===topicId&&typeof t.title==='string'&&t.title.trim());
  return matches.length===1?matches[0].title.trim():null;
 }catch{return null;}
}
const bindingKey=b=>({owner:b.owner,botAccountId:b.botAccountId,peerId:b.peerId});
export function readGroupSettings(path,binding){
 let stat;try{stat=lstatSync(path)}catch(e){if(e.code==='ENOENT')return {revision:0,...groupSettings({})};throw e;}
 if(!stat.isFile()||stat.isSymbolicLink()||stat.uid!==process.getuid()||(stat.mode&0o777)!==0o600)throw Error('unsafe_group_settings');
 const data=JSON.parse(readFileSync(path,'utf8'));
 if(JSON.stringify(data.binding)!==JSON.stringify(bindingKey(binding)))throw Error('group_settings_binding_changed');
 if(!Number.isSafeInteger(data.revision)||data.revision<1)throw Error('invalid_group_settings_revision');
 return {revision:data.revision,...groupSettings(data.settings)};
}
export function writeGroupSettings(path,binding,expectedRevision,settings){
 const value=groupSettings(settings),current=readGroupSettings(path,binding);
 if(!Number.isSafeInteger(expectedRevision)||expectedRevision!==current.revision)throw Error('group_settings_revision_changed');
 const revision=current.revision+1,temp=path+'.'+randomUUID()+'.tmp';let fd;
 try{
  fd=openSync(temp,'wx',0o600);writeFileSync(fd,JSON.stringify({binding:bindingKey(binding),revision,settings:value}));fsyncSync(fd);closeSync(fd);fd=undefined;
  renameSync(temp,path);const dir=openSync(dirname(path),'r');try{fsyncSync(dir)}finally{closeSync(dir)}
 }finally{if(fd!==undefined)closeSync(fd);try{unlinkSync(temp)}catch{}}
 return {revision,...value};
}
export function accessGroupSettings({path,binding,principal,method,input}){
 if(!binding||principal?.user_id!==binding.owner||principal.can_manage!==true)throw Error('group_settings_forbidden');
 if(method==='GET')return readGroupSettings(path,binding);
 if(method!=='POST'||!input||Object.keys(input).some(k=>!['revision','showTopic','chainPrevious'].includes(k))||typeof input.showTopic!=='boolean'||typeof input.chainPrevious!=='boolean')throw Error('invalid_group_settings');
 return writeGroupSettings(path,binding,input.revision,{showTopic:input.showTopic,chainPrevious:input.chainPrevious});
}
