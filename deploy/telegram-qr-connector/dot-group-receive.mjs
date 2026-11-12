import {groupMediaKind} from './dot-group-message.mjs';

// The provider message is already stored in UserIO before this relay seam.
export async function routeGroupDotEvent({event,message,relay,ingress}){
 const kind=groupMediaKind(message);
 const typingId=relay.typing?.start(event,['image','document'].includes(kind));
 try{
  let result;
  if(kind==='image'||kind==='document')result=await ingress.receive(event,message);
  else if(kind==='other_media')result=relay.holdIncoming({...event,media:[{kind:'provider_attachment'}]},'media_requires_review');
  else {await ingress.waitForPending();result=await relay.prepareReceive(event,async()=> '[Контекст недоступен]');}
  relay.typing?.settled(typingId,result);return result;
 }catch(error){relay.typing?.stop(typingId,'ingress_error');throw error;}
}
