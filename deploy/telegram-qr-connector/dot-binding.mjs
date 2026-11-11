import {DOT_POLICY,OWNER,ROOM,RESERVED_ACCOUNTS} from './dot-policy.mjs';
import {randomUUID} from 'node:crypto';
import {lstatSync,readFileSync,writeFileSync,linkSync,unlinkSync} from 'node:fs';
export const BINDING_PATH='/var/lib/universal-userio/telegram-qr/dot-binding.json';
export function readDotBinding(path=BINDING_PATH,includeDisabled=false){
  let info;try{info=lstatSync(path);}catch(e){if(e.code==='ENOENT')return null;throw e;}
  if(!info.isFile() || (info.mode&0o777)!==0o600 || info.uid!==process.getuid())throw Error('unsafe_dot_binding_permissions');
  const b=JSON.parse(readFileSync(path,'utf8'));
  if(!DOT_POLICY||b.owner!==OWNER||b.room!==ROOM||!/^telegram:\d+$/.test(b.botAccountId)||RESERVED_ACCOUNTS.includes(b.botAccountId))throw Error('invalid_dot_binding');
  if(b.enabled!==true)return includeDisabled ? b : null;
  if(b.transport!==undefined&&!['cdp','extension'].includes(b.transport))throw Error('invalid_dot_transport');
  if(b.transport==='extension'&&(typeof b.agentId!=='string'||! /^[A-Za-z0-9_-]{1,100}$/.test(b.agentId)||! /^[A-Za-z0-9._:-]{1,128}$/.test(b.connectionId||'')||! /^[a-f0-9]{64}$/.test(b.accountScope||'')))throw Error('invalid_dot_extension_binding');
  if(!Number.isSafeInteger(b.pageId)||b.pageId<=0)throw Error('invalid_dot_browser_binding');
  return b;
}

export function isDedicatedDotBot(accountId,binding){return !!binding && accountId===binding.botAccountId;}

export function reserveDotBotBinding(botAccountId,path=BINDING_PATH){
  if(!DOT_POLICY||!/^telegram:\d+$/.test(botAccountId)||RESERVED_ACCOUNTS.includes(botAccountId))throw Error('invalid_dot_bot');
  const existing=readDotBinding(path,true);
  if(existing){if(existing.botAccountId!==botAccountId)throw Error('dot_binding_already_reserved');return existing;}
  const binding={enabled:false,owner:OWNER,room:ROOM,botAccountId};
  const temp=path+'.'+randomUUID()+'.tmp';
  writeFileSync(temp,JSON.stringify(binding),{mode:0o600,flag:'wx'});
  try{linkSync(temp,path);}finally{unlinkSync(temp);}
  return binding;
}
