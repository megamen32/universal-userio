import {lstatSync,readFileSync} from 'node:fs';
export function readGroupBinding(path){
 let st;try{st=lstatSync(path)}catch(e){if(e.code==='ENOENT')return null;throw e}
 if(!st.isFile()||(st.mode&0o777)!==0o600||st.uid!==process.getuid())throw Error('unsafe_group_binding');
 const b=JSON.parse(readFileSync(path,'utf8'));
 if(b.enabled!==true)return null;
 if(!/^telegram:[0-9]+$/.test(b.botAccountId||'')||!/^user_[a-z0-9]+$/.test(b.owner||'')||!/^\-100[1-9][0-9]+$/.test(b.peerId||'')||!/^https:\/\/chatgpt\.com\/dots\/[A-Za-z0-9~_-]+$/.test(b.url||'')||!/^[A-Za-z0-9_-]+$/.test(b.room||'')||!/^[A-Za-z0-9_-]+$/.test(b.agentId||'')||!Number.isSafeInteger(b.pageId)||b.pageId<=0)throw Error('invalid_group_binding');
 return b;
}
