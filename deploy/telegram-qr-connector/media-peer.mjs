// Resolve only an account-bound numeric peer. Never try a different account or
// a similarly named chat when the exact peer is absent from the dialog cache.
export async function resolveMediaPeer(item,locator){
 if(!locator.accountId||item.accountId!==locator.accountId||!/^\-?[0-9]+$/.test(locator.chatKey||''))return null;
 let peer=item.labelPeers.idPeers.get(locator.chatKey);
 if(!peer){try{peer=await item.client.getEntity(locator.chatKey)}catch{return null}}
 if(!peer)return null;
 try{if(String(await item.client.getPeerId(peer,true))!==locator.chatKey)return null}catch{return null}
 return peer;
}
