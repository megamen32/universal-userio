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
 async function capture(url,response){
  if(response.status===429){emit({kind:'rate_limit',retryAfter:response.headers.get('Retry-After')||''});return;}
  if(!response.ok||!String(response.headers.get('Content-Type')||'').includes('application/json'))return;
  if(url.pathname!=='/api/auth/session'&&! /^\/backend-api\/conversation\/[A-Za-z0-9_-]+$/.test(url.pathname))return;
  const data=await boundedJSON(response);if(!data)return;
  if(url.pathname==='/api/auth/session'){
   if(data.user?.id&&data.user?.email)emit({kind:'identity',accountId:String(data.user.id).slice(0,160),email:String(data.user.email).slice(0,254)});
   return;
  }
  const messages=Object.values(data.mapping||{}).map(n=>n?.message).filter(m=>m&&['user','assistant'].includes(m.author?.role)&&m.id)
   .slice(-100).map(m=>({id:String(m.id).slice(0,128),role:m.author.role,text:(m.content?.parts||[]).filter(p=>typeof p==='string').join('\n').slice(0,8000),ts:Number(m.create_time)||0})).filter(m=>m.text);
  emit({kind:'conversation',chatId:url.pathname.split('/').pop(),title:String(data.title||'ChatGPT').slice(0,80),messages});
 }
 window.fetch=function(...args){
  const promise=original.apply(this,args);
  let url;try{url=new URL(typeof args[0]==='string'||args[0] instanceof URL?String(args[0]):args[0].url,location.href);}catch{return promise;}
  if(url.origin===location.origin&&(url.pathname.startsWith('/backend-api/')||url.pathname==='/api/auth/session'))promise.then(r=>{void capture(url,r).catch(()=>{});},()=>{});
  return promise;
 };
})();
