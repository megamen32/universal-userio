// Telegram TL ReadHistory + official Android/tweb route; GramJS2.26.22.
export async function acknowledgeRead({slots,Api,payload}) {
  const {account_id,peer_id,max_id}=payload;
  if(typeof account_id!=='string'||!account_id||typeof peer_id!=='string'||!/^\-?\d+$/.test(peer_id)||!Number.isInteger(max_id)||max_id<1||max_id>2147483647)throw Error('exact account/peer/positive read cursor required');
  const item=[...slots.values()].find(v=>v.accountId===account_id);
  if(!item)throw Error('requested account is not connected');
  const peer=item.labelPeers.idPeers.get(peer_id)||await item.client.getEntity(peer_id);
  const input=await item.client.getInputEntity(peer);
  if(input instanceof Api.InputPeerChannel) await item.client.invoke(new Api.channels.ReadHistory({channel:new Api.InputChannel({channelId:input.channelId,accessHash:input.accessHash}),maxId:max_id}));
  else await item.client.invoke(new Api.messages.ReadHistory({peer:input,maxId:max_id}));
  return {ok:true,account_id,peer_id,max_id};
}
