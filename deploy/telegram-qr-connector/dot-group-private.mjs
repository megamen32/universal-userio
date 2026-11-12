import {createGroupBotSender,createGroupBotMediaSender} from './dot-group-send.mjs';
const userId=id=>typeof id==='string'&&/^[1-9][0-9]{0,19}$/.test(id);
export function memberAllowed(member,id){
 return !!member&&String(member.user?.id)===id&&member.user?.is_bot!==true&&
  (['creator','administrator','member'].includes(member.status)||(member.status==='restricted'&&member.is_member===true));
}
export function createGroupMemberAccess({token,groupId,fetcher=fetch}){
 if(!/^-100[1-9][0-9]+$/.test(groupId))throw Error('invalid_member_group');
 return async id=>{
  if(!userId(id))return false;
  const response=await fetcher('https://api.telegram.org/bot'+token+'/getChatMember',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({chat_id:groupId,user_id:id}),signal:AbortSignal.timeout(7000)});
  const result=await response.json();
  if(!response.ok||result.ok!==true)throw Error('group_membership_unavailable');
  return memberAllowed(result.result,id);
 };
}
// Only native Dot reply links choose a private recipient. Chronology, active
// turns and text labels never establish a destination. Unthreaded posts are public.
export function privateReplyRoute(relay,message,rows=[]){
 const group=relay.peerId, fallback={peerId:group,private:false,requestId:null,replyTo:null};
 if(!message.reply_to)return fallback;
 const incoming=Object.entries(relay.state.incoming||{}),published=relay.state.mirror?.published||{};
 const seen=new Set([message.id]);let target=message.reply_to;
 const immediate=published[target];
 for(let depth=0;depth<64&&target;depth++){
  if(typeof target!=='string'||seen.has(target))return {hold:'invalid_native_reply_chain'};
  seen.add(target);
  const matches=incoming.filter(([,r])=>r.status==='accepted'&&r.echo===target);
  if(matches.length>1)return {hold:'ambiguous_native_reply_origin'};
  if(matches.length===1){
   const [key,record]=matches[0],peer=record.delivery_peer_id||group;
   if(record.private_member!==true)return {...fallback,requestId:key};
   if(!userId(peer))return {hold:'invalid_private_reply_peer'};
   const receipt=immediate?.status==='accepted'&&immediate.peer_id===peer?immediate.receipts?.[0]:record.source_message_ids?.[0];
   return {peerId:peer,private:true,requestId:key,replyTo:receipt&&/^[1-9][0-9]*$/.test(String(receipt))?String(receipt):null};
  }
  const visible=rows.find(row=>row.id===target),stored=published[target];
  // Follow the parent's actual reply link, not a legacy guessed peer_id.
  if(visible){target=visible.reply_to||null;if(!target)return fallback;}
  else if(stored){target=stored.reply_to||null;if(!target)return fallback;}
  else return {...fallback,warning:'native_reply_origin_unavailable'};
 }
 return {hold:'invalid_native_reply_chain'};
}
export function installGroupMemberPrivate({relay,mirror,binding,token,boundLive}){
 const isMember=createGroupMemberAccess({token,groupId:binding.peerId});
 const senders=new Map(),mediaSenders=new Map();
 const botId=binding.botAccountId.split(':')[1];
 const ensure=async peer=>{boundLive();if(!userId(peer)||!await isMember(peer))throw Error('private_group_member_required');};
 relay.memberPrivateEnabled=true;
 relay.routeMessage=(message,rows)=>privateReplyRoute(relay,message,rows);
 relay.checkPrivateMember=isMember;
 relay.sendPrivate=async(peer,text,replyTo,entities=[])=>{
  await ensure(peer);
  if(!senders.has(peer))senders.set(peer,createGroupBotSender({token,peerId:peer,botId,allowPrivate:true}));
  return senders.get(peer)(peer,text,replyTo,entities);
 };
 relay.sendPrivateMedia=async(peer,media,caption,replyTo,origin,entities=[])=>{
  await ensure(peer);
  if(!mediaSenders.has(peer))mediaSenders.set(peer,createGroupBotMediaSender({token,peerId:peer,botId,allowPrivate:true}));
  return mediaSenders.get(peer)(peer,media,caption,replyTo,entities);
 };
 // Replies carry their own destination; generation must not block later ingress.
 if(Object.hasOwn(relay.state,'activeTurn')){delete relay.state.activeTurn;relay.save();}
 return relay;
}
