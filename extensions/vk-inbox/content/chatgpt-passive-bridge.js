(function(){
 if(globalThis.__userioPassiveBridge)return;
 globalThis.__userioPassiveBridge=true;
 const pageKey=crypto.randomUUID();
 let retired=false;
 const retire=()=>{
  if(retired)return;
  retired=true;
  window.removeEventListener('message',onMessage);
 };
 const handleFailure=error=>{
  if(/extension context invalidated/i.test(String(error?.message||error)))retire();
 };
 const onMessage=event=>{
  if(retired)return;
  if(event.source!==window||event.origin!=='https://chatgpt.com'||event.data?.type!=='userio-chatgpt-observed-v1')return;
  const observation=event.data.observation;
  if(!observation||!['identity','conversation','rate_limit'].includes(observation.kind))return;
  // The page projection contains only text and public identity; never headers,
  // cookies, access tokens or the raw provider response.
  if(JSON.stringify(observation).length>1024*1024)return;
  try{
   const runtime=globalThis.chrome?.runtime;
   if(!runtime?.id||typeof runtime.sendMessage!=='function'){retire();return;}
   Promise.resolve(runtime.sendMessage({kind:'userioChatGPTObserved',pageKey,observation})).catch(handleFailure);
  }catch(error){handleFailure(error);}
 };
 window.addEventListener('message',onMessage);
})();
