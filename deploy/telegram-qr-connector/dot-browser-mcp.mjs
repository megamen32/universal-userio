import {DOT_POLICY,ROOM,requireDotPolicy} from './dot-policy.mjs';
import {sendMediaThroughBrowser} from './dot-browser-media.mjs';
import {readDotMediaThroughBrowser} from './dot-browser-egress.mjs';
import {spawn} from 'node:child_process';
import {pageAction} from './dot-browser-page.mjs';
const URL=DOT_POLICY?.browserUrl||'';

export function fixedEvaluation(action,args){
 if(!['dot_snapshot','dot_send'].includes(action)||!Number.isSafeInteger(args.tab_id)||args.tab_id<=0)throw Error('invalid_dot_operation');
 if(action==='dot_send'&&(typeof args.text!=='string'||(!args.text.trim()&&!args.media?.length)||args.text.length>16000))throw Error('invalid_dot_text');
 if(args.reply_to!=null&&(typeof args.reply_to!=='string'||!args.reply_to.startsWith(ROOM+'~'+ROOM+'~CalpicoMessage~')))throw Error('invalid_dot_reply');
 const values=[URL,ROOM,action==='dot_send'?args.text:null,action==='dot_snapshot'?(args.after||null):null,args.reply_to||null];
 return {pageId:args.tab_id,function:`() => (${pageAction.toString()})(...${JSON.stringify(values)})`};
}
export function parseEvaluation(output){
 const line=output.split('\n').find(s=>s.trim().startsWith('{'));if(!line)throw Error('missing_gateway_response');
 const response=JSON.parse(line);if(response.error||response.result?.isError)throw Error('gateway_failed');
 const text=(response.result?.content||[]).filter(c=>c.type==='text').map(c=>c.text||'').join('\n');
 const start=text.indexOf('```json'),end=text.lastIndexOf('```');if(start<0||end<=start+7)throw Error('invalid_gateway_result');
 return JSON.parse(text.slice(start+7,end).trim());
}
async function gatewayCall(name,args){
 const policy=requireDotPolicy();
 const payload=JSON.stringify({jsonrpc:'2.0',id:1,method:'tools/call',params:{name,arguments:args}})+'\n';
 return new Promise((resolve,reject)=>{
  const child=spawn('ssh',['-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=5',policy.sshTarget,policy.pythonPath,policy.mcpScriptPath,'--url',policy.mcpEndpoint],{stdio:['pipe','pipe','pipe']});
  let output='',settled=false;const finish=(err,value)=>{if(settled)return;settled=true;clearTimeout(timer);if(err){child.kill();reject(Error(err));}else resolve(value);};
  const timer=setTimeout(()=>finish('dot_browser_timeout_requires_review'),30000);
  child.stdout.setEncoding('utf8');child.stdout.on('data',s=>{output+=s;if(output.length>512*1024)finish('dot_browser_result_too_large');});
  // Never log provider stderr or user message payload.
  child.stderr.resume();child.on('error',()=>finish('dot_browser_transport_unavailable'));
  child.on('close',code=>{if(code!==0)return finish('dot_browser_transport_failed');try{finish(null,output);}catch{finish('dot_browser_result_unknown');}});
  child.stdin.on('error',()=>{});child.stdin.end(payload);
 });
}

export function discoverDotPage(output){
 const line=output.split('\n').find(s=>s.trim().startsWith('{'));
 const response=JSON.parse(line||'null');
 if(!response||response.error||response.result?.isError)throw Error('dot_discovery_unavailable');
 const text=(response.result?.content||[]).filter(c=>c.type==='text').map(c=>c.text||'').join('\n');
 const matches=text.split('\n').map(line=>{const legacy=line.match(/^(\d+): .*\((https:\/\/[^\s)]+)\)(?: \[selected\])?$/);if(legacy)return legacy;const modern=line.match(/^Page (\d+)(?: \[[^\]]+\])?: .* - (https:\/\/\S+)$/);return modern&&[modern[0],modern[1],modern[2]];}).filter(m=>m&&m[2]===URL);
 if(matches.length!==1)throw Error('dot_discovery_not_unique');
 return Number(matches[0][1]);
}
export function createDotBrowserCaller(call=gatewayCall,now=()=>Date.now()){
 const pages=new Map();let lastDiscovery=-Infinity;
 return async(action,args)=>{
  // Read-only media extraction is bound to a previously observed native
  // source message; it never discovers a different page or starts a send.
  if(action==='dot_media_read'){
   const page=pages.get(args?.tab_id)||args?.tab_id;
   return readDotMediaThroughBrowser(call,page,args,parseEvaluation,URL,ROOM);
  }
  // Validate operation before any discovery. A send is NEVER retried.
  fixedEvaluation(action,args);
  const evaluate=async(page)=>parseEvaluation(await call('evaluate_script',fixedEvaluation(action,{...args,tab_id:page})));
  const page=pages.get(args.tab_id)||args.tab_id;
  if(action==='dot_send')return args.media?.length?sendMediaThroughBrowser(call,page,args,parseEvaluation,URL,ROOM):evaluate(page);
  try{const result=await evaluate(page);if(result.ok)return result;
   if(!['wrong_dot_route','wrong_or_unloaded_room'].includes(result.error))return result;
  }catch{}
  if(now()-lastDiscovery<60000)throw Error('dot_discovery_backoff');
  lastDiscovery=now();
  const found=discoverDotPage(await call('list_pages',{}));
  const result=await evaluate(found);
  if(!result.ok||result.room!==ROOM)throw Error('dot_discovery_room_unverified');
  pages.set(args.tab_id,found);
  return result;
 };
}
export const dotBrowserCall=createDotBrowserCaller();
