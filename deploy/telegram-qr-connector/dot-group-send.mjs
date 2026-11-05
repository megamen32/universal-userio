// One Bot API request per already journalled relay delivery; never retry here.
export function createGroupBotSender({token,peerId,botId,fetcher=fetch}){
 if(!/^[0-9]{5,20}:[A-Za-z0-9_-]{30,80}$/.test(token)||token.split(':')[0]!==botId||!/^\-100[1-9][0-9]+$/.test(peerId))throw Error('invalid_group_sender');
 return async(peer,text,replyTo)=>{
  if(peer!==peerId||typeof text!=='string'||!text.trim()||text.length>4096)throw Error('invalid_group_send');
  const args={chat_id:peerId,text,link_preview_options:{is_disabled:true}};
  if(replyTo!=null){if(!/^[1-9][0-9]*$/.test(String(replyTo)))throw Error('invalid_group_reply');args.reply_parameters={message_id:Number(replyTo)}}
  let response;try{response=await fetcher('https://api.telegram.org/bot'+token+'/sendMessage',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(args),signal:AbortSignal.timeout(20000)});}catch{throw Error('group_send_unknown');}
  if(!response.ok)throw Error('group_send_unknown');
  let d;try{d=await response.json()}catch{throw Error('group_send_unknown')}
  if(d.ok!==true||String(d.result?.chat?.id)!==peerId||String(d.result?.from?.id)!==botId||!Number.isSafeInteger(d.result?.message_id)||d.result.message_id<=0)throw Error('group_send_unknown');
  return d.result.message_id;
 };
}
