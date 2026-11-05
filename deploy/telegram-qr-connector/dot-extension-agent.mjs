// Scoped caller for the existing UserIO extension command channel. Delivery
// requests are admitted once: timeout means UNKNOWN and is never retried.
export function createExtensionCaller({base,agentId,url,room,token,fetcher=fetch,wait=ms=>new Promise(r=>setTimeout(r,ms)),now=()=>Date.now()}){
 if(!/^https:\/\/chatgpt\.com\/dots\/[A-Za-z0-9~_-]+$/.test(url)||!room||!agentId||!token)throw Error('invalid_extension_binding');
 const request=async(path,body)=>{
  const r=await fetcher(new URL(path,base),{method:body?'POST':'GET',headers:{Authorization:'Bearer '+token,'content-type':'application/json'},...(body?{body:JSON.stringify(body)}:{}),signal:AbortSignal.timeout(10000)});
  if(!r.ok)throw Error('extension_channel_http_'+r.status);
  return r.json();
 };
 return async(action,args)=>{
  if(!['dot_snapshot','dot_send'].includes(action))throw Error('unsupported_extension_dot_action');
  const queuedAt=now()/1000;
  const payload={agent_id:agentId,action:'dot_action',args:{url,room,operation:action==='dot_snapshot'?'snapshot':'send',after:args.after||null,reply_to:args.reply_to||null}};
  if(action==='dot_send'){
   if(args.media?.length)throw Error('extension_media_not_supported');
   if(typeof args.text!=='string'||!args.text.trim())throw Error('invalid_message');
   payload.args.text=args.text;
  }
  const q=await request('/v1/agent/commands',payload);
  if(!q.queued||typeof q.id!=='string')throw Error('extension_enqueue_unknown');
  for(let i=0;i<45;i++){
   const d=await request('/v1/agent/results?agent_id='+encodeURIComponent(agentId)+'&limit=100');
   const x=(d.results||[]).find(r=>r.id===q.id&&r.action==='dot_action'&&r.received_at>=queuedAt);
   if(x)return x.result;
   await wait(1000);
  }
  throw Error('extension_result_unknown');
 };
}
