(function(){
 if(globalThis.__userioPassiveBridge)return;
 globalThis.__userioPassiveBridge=true;
 const pageKey=crypto.randomUUID();
 window.addEventListener('message',event=>{
  if(event.source!==window||event.origin!=='https://chatgpt.com'||event.data?.type!=='userio-chatgpt-observed-v1')return;
  const observation=event.data.observation;
  if(!observation||!['identity','conversation','rate_limit'].includes(observation.kind))return;
  // The page projection contains only text and public identity; never headers,
  // cookies, access tokens or the raw provider response.
  if(JSON.stringify(observation).length>1024*1024)return;
  chrome.runtime.sendMessage({kind:'userioChatGPTObserved',pageKey,observation}).catch(()=>{});
 });
})();
