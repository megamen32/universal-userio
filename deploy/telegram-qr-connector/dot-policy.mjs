import {openSync,closeSync,fstatSync,readFileSync,constants} from 'node:fs';
export const POLICY_PATH=process.env.USERIO_DOT_POLICY_PATH||'/var/lib/universal-userio/telegram-qr/dot-policy.json';
export function readDotPolicy(path=POLICY_PATH,{uid=process.getuid()}={}){
 let fd;
 try{
  fd=openSync(path,constants.O_RDONLY|constants.O_NOFOLLOW);
  const s=fstatSync(fd);
  if(!s.isFile()||s.uid!==uid||(s.mode&0o777)!==0o600||s.size>8192)return null;
  const p=JSON.parse(readFileSync(fd,'utf8'));
  const keys=['owner','room','sourceAccountId','sourceUsername','browserUrl','sshTarget','pythonPath','mcpScriptPath','mcpEndpoint','ownerLabel','assistantLabel'];
  if(!p||Object.keys(p).some(k=>!keys.includes(k))||!/^\d{1,20}$/.test(p.owner)||! /^[a-f0-9]{32}$/.test(p.room)
   ||!/^telegram:\d{1,20}$/.test(p.sourceAccountId)||! /^[A-Za-z0-9_]{3,64}$/.test(p.sourceUsername)
   ||!/^https:\/\/chatgpt\.com\/dots\/[a-zA-Z0-9~-]+$/.test(p.browserUrl)
   ||! /^[a-zA-Z0-9_-]+@[a-zA-Z0-9.-]+$/.test(p.sshTarget)
   ||![p.pythonPath,p.mcpScriptPath].every(x=>typeof x==='string'&&/^\/[a-zA-Z0-9_./-]+$/.test(x)&&!x.includes('..'))
   ||!/^http:\/\/127\.0\.0\.1:\d{1,5}\/mcp$/.test(p.mcpEndpoint)
   ||![p.ownerLabel,p.assistantLabel].every(x=>typeof x==='string'&&x.length>0&&x.length<=80&&!/[\r\n]/.test(x)))return null;
  return Object.freeze(p);
 }catch{return null;}finally{if(fd!==undefined)closeSync(fd);}
}
export const DOT_POLICY=readDotPolicy();
export const OWNER=DOT_POLICY?.owner||'';
export const ROOM=DOT_POLICY?.room||'';
export const RESERVED_ACCOUNTS=Object.freeze(DOT_POLICY?['telegram:'+OWNER,DOT_POLICY.sourceAccountId]:[]);
export function requireDotPolicy(){if(!DOT_POLICY)throw Error('dot_policy_unavailable');return DOT_POLICY;}
