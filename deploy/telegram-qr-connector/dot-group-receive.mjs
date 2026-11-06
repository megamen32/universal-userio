import {groupMediaKind} from './dot-group-message.mjs';

// The provider message is already stored in UserIO before this relay seam.
// Unknown attachment types remain associated with their original message id.
export async function routeGroupDotEvent({event,message,relay,ingress}){
 const kind=groupMediaKind(message);
 if(kind==='image')return ingress.receive(event,message);
 if(kind==='other_media')return relay.holdIncoming({...event,media:[{kind:'provider_attachment'}]},'media_requires_review');
 await ingress.waitForPending();
 return relay.prepareReceive(event,async()=> '[Контекст недоступен]');
}
