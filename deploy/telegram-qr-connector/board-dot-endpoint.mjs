// Disabled integration candidate for UserIO, not mounted or deployed.
// Token verification and durable journal ownership must use UserIO's protected
// server store. This module does not create, read or persist credentials.
export function createBoardDotHandler({enabled=false, binding, authorizeToken, relay, readReceipt}) {
 return async function handle(request) {
  const json=(body,status=200)=>Response.json(body,{status,headers:{'Cache-Control':'no-store'}});
  if(!enabled) return json({error:'binding_disabled'},503);
  const auth=request.headers.get('authorization') ?? '';
  if(!auth.startsWith('Bearer ') || auth.length>4096) return json({error:'unauthorized'},401);
  let grant;
  try {grant=await authorizeToken(auth.slice(7));} catch {return json({error:'unauthorized'},401);}
  if(!grant || grant.audience!=='userio-dot-board' || grant.owner!==binding.owner
      || grant.room!==binding.room || !grant.credentialId || grant.active!==true || !Array.isArray(grant.operations))
    return json({error:'forbidden'},403);
  const safeStatus=value=>['accepted','queued','unknown','sending','blocked','not_attempted','not_accepted','queue_full_not_accepted'].includes(value)?value:'unknown';
  const path=new URL(request.url).pathname;
  const root='/telegram-qr/dot-board/requests';
  const permittedId=id=>/^board:[a-f0-9]{64}(?::answer:[a-f0-9]{64})?$/.test(id);
  if(request.method==='POST' && path===root) {
    if(!grant.operations.includes('enqueue')) return json({error:'forbidden'},403);
    // Bound streaming body before parsing rather than trusting Content-Length.
    const reader=request.body?.getReader();if(!reader)return json({error:'invalid_body'},400);
    let text='',bytes=0;const decoder=new TextDecoder();
    try {for(;;){const {done,value}=await reader.read();if(done)break;bytes+=value.byteLength;
      if(bytes>65536){await reader.cancel();return json({error:'body_too_large'},413);}
      text+=decoder.decode(value,{stream:true});}text+=decoder.decode();
    }catch{return json({error:'invalid_body'},400);}
    let body;try{body=JSON.parse(text);}catch{return json({error:'invalid_body'},400);}
    if(!body || Object.keys(body).some(k=>!['requestId','text'].includes(k))
        || typeof body.requestId!=='string' || !permittedId(body.requestId)
        || typeof body.text!=='string' || !body.text.trim() || body.text.length>16000)
      return json({error:'invalid_request'},400);
    try {
      // Adapter must bind credentialId to the persisted source before same-singleton
      // enqueue. A caller cannot read or reuse another credential's source.
      const receipt=await relay.enqueueOwned(grant.credentialId,{source:'board',requestId:body.requestId,text:body.text});
      return json({requestId:body.requestId,status:safeStatus(receipt.status)},202);
    } catch {return json({requestId:body.requestId,status:'unknown',retryable:false},202);}
  }
  if(request.method==='GET' && path.startsWith(root+'/')) {
    if(!grant.operations.includes('read_receipt'))return json({error:'forbidden'},403);
    let id;try{id=decodeURIComponent(path.slice(root.length+1));}catch{return json({error:'invalid_request'},400);}
    if(!permittedId(id) || new URL(request.url).search)return json({error:'invalid_request'},400);
    let receipt;try{receipt=await readReceipt(grant.credentialId,id);}catch{return json({error:'receipt_unavailable'},503);}
    if(!receipt)return json({error:'not_found'},404);
    // No message text, assistant reply, cursor/transcript, credential or room IDs.
    return json({requestId:id,status:safeStatus(receipt.status)});
  }
  return json({error:'not_found'},404);
 };
}
