// Observe existing same-origin requests. Never issue, repeat or block a request.
(function(){
 if(globalThis.__userioFetchObserved)return;
 globalThis.__userioFetchObserved=true;
 const original=window.fetch;
 const emit=observation=>window.postMessage({type:'userio-chatgpt-observed-v1',observation},location.origin);
 async function boundedJSON(response){
  const reader=response.clone().body?.getReader();if(!reader)return null;
  const chunks=[];let size=0;
  try{
   while(true){const {done,value}=await reader.read();if(done)break;size+=value.byteLength;if(size>2*1024*1024){void reader.cancel();return null;}chunks.push(value);}
   const bytes=new Uint8Array(size);let offset=0;for(const c of chunks){bytes.set(c,offset);offset+=c.byteLength;}
   return JSON.parse(new TextDecoder().decode(bytes));
  }catch{return null;}
 }
 function safeData(value,depth=0){
  if(depth>8)return null;
  if(value===null||typeof value==='string'||typeof value==='number'||typeof value==='boolean')return value;
  if(Array.isArray(value))return value.slice(0,100).map(v=>safeData(v,depth+1));
  if(typeof value!=='object')return null;
  const out={};for(const [k,v] of Object.entries(value)){
   if(/token|cookie|authorization|secret|session|accesskey|bearer/i.test(k))continue;
   out[k]=safeData(v,depth+1);
  }return out;
 }
 async function capture(url,response){
  if(response.status===429){emit({kind:'rate_limit',retryAfter:response.headers.get('Retry-After')||''});return;}
  if(!response.ok||!String(response.headers.get('Content-Type')||'').includes('application/json'))return;
  const history=url.pathname.match(/^\/backend-api\/conversations?\/([A-Za-z0-9_-]{1,128})$/);
  if(url.pathname!=='/api/auth/session'&&!history)return;
  const data=await boundedJSON(response);if(!data)return;
  if(url.pathname==='/api/auth/session'){
   if(data.user?.id)emit({kind:'identity',accountId:String(data.user.id).slice(0,160),workspaceId:String(data.account?.id||'').slice(0,160),accountLabel:String(data.user.name||data.user.email||data.user.id).slice(0,80),email:typeof data.user.email==='string'?data.user.email.slice(0,254):undefined});
   return;
  }
  if(Object.hasOwn(data,'conversation_id')&&data.conversation_id!==history[1])return;
  const bounded=safeData(data);if(!bounded)return;
  emit({kind:'conversation',chatId:history[1],title:String(data.title||'ChatGPT').slice(0,80),data:bounded});
 }
 window.fetch=function(...args){
  const promise=original.apply(this,args);
  let url;try{url=new URL(typeof args[0]==='string'||args[0] instanceof URL?String(args[0]):args[0].url,location.href);}catch{return promise;}
  if(url.origin===location.origin&&(url.pathname.startsWith('/backend-api/')||url.pathname==='/api/auth/session'))promise.then(r=>{void capture(url,r).catch(()=>{});},()=>{});
  return promise;
 };
})();
