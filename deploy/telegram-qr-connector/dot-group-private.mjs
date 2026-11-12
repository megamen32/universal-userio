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
// Recipient selection is derived from persisted ingress identity, never model text.
export function privateReplyRoute(relay,message,rows){
 const group=relay.peerId, incoming=Object.entries(relay.state.incoming||{});
 const byEcho=id=>{const found=incoming.filter(([,r])=>r.echo===id);return found.length===1?found[0]:null;};
 const fromRecord=([key,r])=>({peerId:r.delivery_peer_id||group,private:r.private_member===true,requestId:key,replyTo:r.private_member?String(r.source_message_ids?.[0]||''):null});
 const previous=relay.state.mirror?.published?.[message.id];
 if(previous?.peer_id)return {peerId:previous.peer_id,private:previous.peer_id!==group,requestId:previous.request_id||null,replyTo:previous.reply_receipt||null};
 if(message.reply_to){
  const direct=byEcho(message.reply_to);if(direct)return fromRecord(direct);
  const pub=relay.state.mirror?.published?.[message.reply_to];
  if(pub?.peer_id)return {peerId:pub.peer_id,private:pub.peer_id!==group,requestId:pub.request_id||null,replyTo:pub.receipts?.[0]||null};
 }
 const index=rows.findIndex(r=>r.id===message.id);
 const users=rows.slice(0,index+1).filter(r=>r.role==='user');
 const nearest=users.at(-1),match=nearest&&byEcho(nearest.id);
 const active=relay.state.activeTurn,activeRecord=active&&relay.state.incoming[active];
 if(activeRecord?.private_member&&activeRecord.echo&&nearest&&nearest.id!==activeRecord.echo&&!message.reply_to)
  return {hold:'private_turn_interrupted'};
 if(match)return fromRecord(match);
 if(!nearest&&activeRecord?.private_member)return {hold:'private_anchor_not_visible'};
 return {peerId:group,private:false,requestId:null,replyTo:null};
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
 // Do not change the destination while a preceding response is pending.
 relay.beforeDeliver=async()=>{
  for(let attempt=0;attempt<150;attempt++){
   const active=relay.state.activeTurn;if(!active)return true;
   const record=relay.state.incoming[active];
   if(!record||['not_accepted','ignored','requires_review'].includes(record.status)){
    delete relay.state.activeTurn;relay.save();return true;
   }
   const complete=Object.values(relay.state.mirror?.published||{}).some(p=>p.request_id===active&&p.role==='assistant'&&p.status==='accepted');
   if(complete){delete relay.state.activeTurn;relay.save();return true;}
   await mirror.tick();await relay.wait(2000);
  }
  return false;
 };
 return relay;
}
