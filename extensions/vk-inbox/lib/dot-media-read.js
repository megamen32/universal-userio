// Kept in sync with deploy/telegram-qr-connector/dot-browser-egress.mjs.
(function(root){
async function dotMediaPageAction(url,room,action,args={}){
 const fail=error=>({ok:false,error});
 const cacheKey='__userioDotMediaReadOnly';
 const clear=()=>{
  const old=globalThis[cacheKey];
  if(old?.entries)for(const entry of old.entries)entry.bytes.fill(0);
  delete globalThis[cacheKey];
 };
 if(action==='finish'){
  const old=globalThis[cacheKey];
  if(old?.nonce===args.nonce){clear();return {ok:true,status:'cleared'};}
  return fail('media_cache_unknown');
 }
 if(location.href!==url)return fail('wrong_dot_route');
 const prefix=room+'~'+room+'~CalpicoMessage~';
 const rows=[...document.querySelectorAll('article.message-row[data-message-id]')]
  .filter(e=>!e.closest?.('.reply-chain-panel[role="dialog"]'));
 if(!rows.length||rows.some(e=>!e.dataset.messageId.startsWith(prefix)))
  return fail('wrong_or_unloaded_room');
 if(typeof args.source_id!=='string'||!args.source_id.startsWith(prefix))
  return fail('invalid_media_source');
 const matches=rows.filter(e=>e.dataset.messageId===args.source_id);
 if(matches.length!==1)return fail('media_source_not_unique');
 const images=[...(matches[0].querySelectorAll?.('img.attachment-image')||[])];
 if(images.length<1||images.length>10)return fail('media_count_requires_review');
 const sha=async bytes=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)))
  .map(x=>x.toString(16).padStart(2,'0')).join('');
 const current=[];
 for(const img of images){
  const source=img.currentSrc||img.src||img.getAttribute?.('src')||'';
  if(typeof source!=='string'||!source)return fail('media_source_unavailable');
  const u=new URL(source,location.href);
  // Image URLs are taken only from this exact message's own DOM. Do not
  // transfer bearer-bearing URLs or fetch arbitrary third-party resources.
  if(!['https:','blob:'].includes(u.protocol)||u.origin!==location.origin)
   return fail('media_origin_requires_review');
  current.push({src:u.href,fingerprint:await sha(new TextEncoder().encode(u.href))});
 }
 if(action==='start'){
  const existing=globalThis[cacheKey];
  if(existing&&existing.expiresAt>Date.now())return fail('media_read_busy_requires_review');
  if(existing)clear();
  if(!Array.isArray(args.expected_fingerprints)||args.expected_fingerprints.length!==images.length
   ||current.some((v,i)=>v.fingerprint!==args.expected_fingerprints[i]))
   return fail('media_identity_changed');
  let total=0;
  const entries=[];
  try{
   for(const item of current){
    const response=await fetch(item.src,{credentials:'same-origin',redirect:'error',cache:'no-store'});
    if(!response.ok||!response.body?.getReader)return fail('media_fetch_unavailable');
    const reader=response.body.getReader(),chunks=[];let size=0;
    try{
     for(;;){
      const {done,value}=await reader.read();
      if(done)break;
      if(!(value instanceof Uint8Array))throw Error('media_chunk_invalid');
      size+=value.byteLength;
      if(size>8*1024*1024||total+size>20*1024*1024)throw Error('media_size_refused');
      chunks.push(value);
     }
    }finally{reader.releaseLock();}
    if(size===0)throw Error('media_empty');
    const bytes=new Uint8Array(size);let pos=0;
    for(const chunk of chunks){bytes.set(chunk,pos);pos+=chunk.byteLength;}
    total+=size;
    entries.push({bytes,source:item.src,fingerprint:item.fingerprint,sha256:await sha(bytes)});
   }
  }catch{for(const e of entries)e.bytes.fill(0);return fail('media_fetch_requires_review');}
  const nonce=crypto.randomUUID();
  globalThis[cacheKey]={nonce,sourceId:args.source_id,expiresAt:Date.now()+600000,entries};
  return {ok:true,nonce,count:entries.length,items:entries.map(e=>({
   size:e.bytes.byteLength,sha256:e.sha256,fingerprint:e.fingerprint
  }))};
 }
 if(action==='chunk'){
  const cache=globalThis[cacheKey];
  if(!cache||cache.nonce!==args.nonce||cache.sourceId!==args.source_id
   ||cache.expiresAt<=Date.now())return fail('media_read_expired');
  if(cache.entries.length!==current.length||cache.entries.some((e,i)=>e.fingerprint!==current[i].fingerprint))
   return fail('media_identity_changed');
  if(!Number.isSafeInteger(args.index)||args.index<0||args.index>=cache.entries.length
   ||!Number.isSafeInteger(args.offset)||args.offset<0
   ||!Number.isSafeInteger(args.length)||args.length<1||args.length>128*1024)
   return fail('invalid_media_chunk_request');
  const entry=cache.entries[args.index];
  if(args.offset>=entry.bytes.byteLength)return fail('invalid_media_offset');
  const chunk=entry.bytes.subarray(args.offset,Math.min(entry.bytes.byteLength,args.offset+args.length));
  let binary='';
  for(let i=0;i<chunk.length;i+=8192)binary+=String.fromCharCode(...chunk.subarray(i,i+8192));
  return {ok:true,nonce:args.nonce,index:args.index,offset:args.offset,size:entry.bytes.byteLength,
   sha256:entry.sha256,data:btoa(binary)};
 }
 return fail('invalid_media_operation');
}

root.UserIODotRead={pageAction:dotMediaPageAction};
})(self);
