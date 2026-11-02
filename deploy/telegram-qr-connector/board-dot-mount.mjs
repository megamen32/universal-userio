// Disabled source-only candidate. No grant/config file is created by this module.
import {openSync,closeSync,fstatSync,readFileSync,constants} from 'node:fs';
import {join} from 'node:path';
import {Readable} from 'node:stream';
import {createHash,timingSafeEqual} from 'node:crypto';
import {createBoardDotHandler} from './board-dot-endpoint.mjs';
export function readBoardDotGrant(file,binding,{uid=process.getuid(),now=Date.now()}={}) {
 let fd;
 try {
  fd=openSync(file,constants.O_RDONLY|constants.O_NOFOLLOW);
  const stat=fstatSync(fd);
  if(!stat.isFile() || stat.uid!==uid || (stat.mode&0o077)!==0 || stat.size>16384)return null;
  const value=JSON.parse(readFileSync(fd,'utf8'));
  if(!value || value.enabled!==true || Object.keys(value).some(k=>!['enabled','credential_id','token_hash','owner','room','operations','expires_at'].includes(k))
    || !/^[A-Za-z0-9_-]{1,100}$/.test(value.credential_id??'') || !/^[a-f0-9]{64}$/.test(value.token_hash??'')
    || value.owner!==binding.owner || value.room!==binding.room || !Array.isArray(value.operations)
    || value.operations.length===0 || value.operations.some(x=>!['enqueue','read_receipt'].includes(x))
    || !Number.isFinite(Date.parse(value.expires_at)) || Date.parse(value.expires_at)<=now)return null;
  return value;
 }catch{return null;}finally{if(fd!==undefined)closeSync(fd);}
}
export function createBoardDotNodeMount({stateDir,binding,resolveRelay,loadGrant=readBoardDotGrant}) {
 const file=join(stateDir,'dot-board-service.json');
 return async(req,res)=>{
  const raw=req.url??'';
  if(!/^\/dot-board\/requests(?:[/?]|$)/.test(raw))return false;
  const initial=loadGrant(file,binding);
  const handler=createBoardDotHandler({enabled:!!initial,binding,
    authorizeToken:async token=>{
      const current=loadGrant(file,binding);
      if(!current)return null;
      const actual=createHash('sha256').update(token).digest();
      if(!timingSafeEqual(actual,Buffer.from(current.token_hash,'hex')))return null;
      return {audience:'userio-dot-board',owner:current.owner,room:current.room,active:true,
        credentialId:current.credential_id,operations:current.operations};
    },
    relay:{enqueueOwned:async(id,input)=>resolveRelay().enqueueOwned(id,input)},
    readReceipt:async(id,requestId)=>resolveRelay().readOwnedReceipt(id,requestId),
  });
  try {
    const method=req.method??'GET';
    const headers=new Headers();
    if(typeof req.headers.authorization==='string')headers.set('authorization',req.headers.authorization);
    const request=new Request('https://userio.invalid/telegram-qr'+raw,{method,headers,
      ...(!['GET','HEAD'].includes(method)?{body:Readable.toWeb(req),duplex:'half'}:{})});
    const response=await handler(request);
    res.writeHead(response.status,Object.fromEntries(response.headers));res.end(await response.text());
  }catch{res.writeHead(503,{'content-type':'application/json','cache-control':'no-store'});res.end('{"error":"dot_board_unavailable"}');}
  return true;
 };
}
