// Passive ChatGPT capture: no timer, auth refresh or conversation sweep.
(function(root){
 const ORIGIN='https://chatgpt.com',COOLDOWN='chatgptSyncCooldownUntil';
 const originalFetch=globalThis.fetch.bind(globalThis),flights=new Map(),identities=new Map();
 let tail=Promise.resolve(),rateTail=Promise.resolve(),upstreamRequests=0,suppressedRequests=0,memoryCooldown=0;
 function deadline(raw){
  const now=Date.now(),s=String(raw||'').trim();
  const until=/^\d+$/.test(s)?now+Number(s)*1000:Date.parse(s);
  return Number.isFinite(until)&&until>now?until:now+60_000;
 }
 function rateLimit(raw){
  const until=deadline(raw);
  memoryCooldown=Math.max(memoryCooldown,until);
  rateTail=rateTail.catch(()=>{}).then(async()=>{
   const state=await chrome.storage.local.get({[COOLDOWN]:0});
   await chrome.storage.local.set({[COOLDOWN]:Math.max(Number(state[COOLDOWN])||0,until)});
  });return rateTail;
 }
 globalThis.fetch=root.fetch=async function(input,options){
  let url;try{url=new URL(typeof input==='string'||input instanceof URL?String(input):input.url);}catch{return originalFetch(input,options);}
  if(url.origin!==ORIGIN)return originalFetch(input,options);
  const stored=await chrome.storage.local.get({[COOLDOWN]:0});
  const until=Math.max(Number(stored[COOLDOWN])||0,memoryCooldown);
  if(Date.now()<until){suppressedRequests++;return new Response('{"error":"UserIO respects ChatGPT Retry-After"}',{status:429,headers:{'Content-Type':'application/json','Retry-After':String(Math.ceil((until-Date.now())/1000))}});}
  const key=(options?.method||input?.method||'GET').toUpperCase()+' '+url.href;
  if(key.startsWith('GET ')&&flights.has(key))return (await flights.get(key)).clone();
  upstreamRequests++;
  const flight=originalFetch(input,options).then(async response=>{if(response.status===429)await rateLimit(response.headers.get('Retry-After'));return response;});
  if(key.startsWith('GET '))flights.set(key,flight);
  try{return await flight;}finally{if(flights.get(key)===flight)flights.delete(key);}
 };
 root.ChatGPT.start=async()=>{
  await chrome.alarms.clear('userio-gpt-sync');
  await chrome.storage.local.set({chatgptPassiveReport:{mode:'passive',startedAt:Date.now()}});
  // MV3 reload does not reinject manifest scripts into existing tabs.
  if(chrome.tabs&&chrome.scripting){
   const tabs=await chrome.tabs.query({url:ORIGIN+'/*'});
   for(const tab of tabs){
    try{
     await chrome.scripting.executeScript({target:{tabId:tab.id},world:'ISOLATED',files:['content/chatgpt-passive-bridge.js']});
     await chrome.scripting.executeScript({target:{tabId:tab.id},world:'MAIN',files:['content/chatgpt-fetch-hook.js']});
    }catch{/* Next natural navigation loads the manifest scripts. */}
   }
  }
 };
 root.ChatGPT.passiveStatus=async()=>({ok:true,version:chrome.runtime.getManifest().version,mode:'passive',upstreamRequests,suppressedRequests,
  alarms:(await chrome.alarms.getAll()).map(a=>a.name),...(await chrome.storage.local.get({chatgptPassiveReport:null,[COOLDOWN]:0}))});
 async function observe(packet,sender){
  const o=packet.observation,key=sender.tab.id+':'+packet.pageKey;
  if(!o||typeof o!=='object')return {ok:false};
  if(o.kind==='rate_limit'){await rateLimit(o.retryAfter);return {ok:true};}
  if(o.kind==='identity'){
   if(typeof o.email!=='string'||o.email.length>254||!o.email.includes('@')||typeof o.accountId!=='string'||!o.accountId||o.accountId.length>160)return {ok:false};
   identities.set(key,{email:o.email,accountId:o.accountId});
   if(identities.size>30)identities.delete(identities.keys().next().value);
   return {ok:true};
  }
  const identity=identities.get(key);
  if(!identity||o.kind!=='conversation'||typeof o.chatId!=='string'||! /^[A-Za-z0-9_-]{1,128}$/.test(o.chatId)||!Array.isArray(o.messages)||o.messages.length>100)return {ok:false,reason:'identity_or_source_unconfirmed'};
  const slug=identity.email.toLowerCase().replace(/[^a-z0-9._-]+/g,'-').replace(/^[.-]+|[.-]+$/g,'').slice(0,64);
  const accounts=await root.UserIO.call('GET','/v1/chatgpt/sessions');
  if(!accounts?.accounts?.some(a=>a.slug===slug))return {ok:false,reason:'account_not_registered'};
  let count=0;
  for(const m of o.messages){
   if(!m||typeof m.id!=='string'||! /^[A-Za-z0-9_-]{1,128}$/.test(m.id)||!['user','assistant'].includes(m.role)||typeof m.text!=='string'||m.text.length>8000||!Number.isFinite(m.ts)||m.ts<0)continue;
   await root.UserIO.call('POST','/v1/messages',{route_id:'chatgpt',message:{schema:'universal.inbox.message.v1',source:'chatgpt:'+slug,message_id:o.chatId+':'+m.id,sender:o.chatId,sender_name:String(o.title||'ChatGPT').slice(0,80),body:'['+m.role+'] '+m.text,received_at:m.ts}});count++;
  }
  await chrome.storage.local.set({chatgptPassiveReport:{mode:'passive',lastCaptureAt:Date.now(),messages:count}});
  return {ok:true,messages:count};
 }
 chrome.runtime.onMessage.addListener((packet,sender,reply)=>{
  if(packet?.kind!=='userioChatGPTObserved')return false;
  let url;try{url=new URL(sender?.tab?.url);}catch{return false;}
  if(url.origin!==ORIGIN||sender.frameId>0||typeof packet.pageKey!=='string'||packet.pageKey.length>80)return false;
  if(packet.observation?.kind==='rate_limit'){
   rateLimit(packet.observation.retryAfter).then(()=>reply({ok:true}),()=>reply({ok:false}));return true;
  }
  tail=tail.catch(()=>{}).then(()=>observe(packet,sender));
  tail.then(reply,()=>reply({ok:false,error:'passive_capture_failed'}));return true;
 });
})(self);
