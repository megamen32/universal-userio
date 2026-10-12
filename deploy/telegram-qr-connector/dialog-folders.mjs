// Official TL messages.getDialogFilters; Android MessagesController/tweb filters;
// installed GramJS2.26.22/layer198. Metadata only on the existing account client.
export async function readDialogFolders({slots,Api,accountId,folderIds,resolve=false}) {
  if(typeof accountId!=='string'||!/^telegram:[1-9][0-9]*$/.test(accountId)||
     !Array.isArray(folderIds)||!folderIds.length||folderIds.length>10||
     folderIds.some(id=>!Number.isInteger(id)||id<2)) throw Error('exact account and bounded folder IDs required');
  const item=[...slots.values()].find(value=>value.accountId===accountId);
  if(!item||!item.client)throw Error('requested account is not connected');
  const me=await item.client.getMe();
  if(!me||'telegram:'+String(me.id)!==accountId)throw Error('connected account identity mismatch');
  const response=await item.client.invoke(new Api.messages.GetDialogFilters());
  const folders=[];
  const explicitPeers=new Map();
  for(const filter of response.filters||[]) {
    if(!folderIds.includes(filter.id))continue;
    const ids=async peers=>{
      const result=[];
      for(const peer of peers||[])result.push(String(await item.client.getPeerId(peer,true)));
      return [...new Set(result)];
    };
    const categories={};
    for(const flag of ['contacts','nonContacts','groups','broadcasts','bots'])categories[flag]=filter[flag]===true;
    folders.push({id:filter.id,title:String(typeof filter.title==='string'?filter.title:filter.title.text),
      include_peer_ids:await ids(filter.includePeers),pinned_peer_ids:await ids(filter.pinnedPeers),
      exclude_peer_ids:await ids(filter.excludePeers),categories,
      exclude_muted:filter.excludeMuted===true,exclude_read:filter.excludeRead===true,
      exclude_archived:filter.excludeArchived===true});
    const excluded=new Set(await ids(filter.excludePeers));
    for(const peer of [...(filter.includePeers||[]),...(filter.pinnedPeers||[])]) {
      const id=String(await item.client.getPeerId(peer,true));
      if(!excluded.has(id))explicitPeers.set(id,peer);
    }
  }
  const result={account_id:accountId,folders};
  if(resolve&&explicitPeers.size) {
    // Installed GramJS batches InputPeers into GetUsers/GetChats/GetChannels.
    // Keep the same connected client; emit only required identity metadata.
    const peers=[...explicitPeers.values()];
    const entities=await item.client.getEntity(peers);
    result.dialogs=[];
    for(let i=0;i<peers.length;i++) {
      const entity=entities[i];
      if(!entity||entity.deleted)continue;
      const id=String(await item.client.getPeerId(peers[i],true));
      const name=entity.title||[entity.firstName,entity.lastName].filter(Boolean).join(' ')||entity.username||id;
      const kind=entity.bot?'telegram_bot':entity.broadcast?'channel':entity.megagroup||entity.className==='Chat'?'group':'direct';
      result.dialogs.push({peer_id:id,name:String(name),conversation_kind:kind});
      if(item.labelPeers) {
        item.labelPeers.idPeers.set(id,entity);
        item.labelPeers.labelPeers.set(String(name),entity);
      }
    }
  }
  return result;
}
