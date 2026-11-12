import {randomUUID} from 'node:crypto';
import {validateMediaFiles} from './dot-browser-media.mjs';
import {readDotMediaUsing} from './dot-browser-egress.mjs';
// Scoped caller for the existing UserIO extension command channel. Delivery
// requests are admitted once: timeout means UNKNOWN and is never retried.
const BROWSER_INSTANCE=/^browser-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
export function createExtensionCaller({base,agentId,connectionId,accountScope,agentInstanceId='',url,room,token,fetcher=fetch,wait=ms=>new Promise(r=>setTimeout(r,ms)),now=()=>Date.now(),loadMedia=validateMediaFiles,transferId=randomUUID,preserveDraft=true,discardDrafts=false,assertAllowed=()=>{},getAgentStatus=null}){
 if(!/^https:\/\/chatgpt\.com\/dots\/[A-Za-z0-9~_-]+$/.test(url)||!room||!agentId||!connectionId||!/^[A-Za-z0-9._:-]{1,128}$/.test(connectionId)||!/^[a-f0-9]{64}$/.test(accountScope||'')||!token)throw Error('invalid_extension_binding');
 const request=async(path,body)=>{
  const r=await fetcher(new URL(path,base),{method:body?'POST':'GET',headers:{Authorization:'Bearer '+token,'content-type':'application/json'},...(body?{body:JSON.stringify(body)}:{}),signal:AbortSignal.timeout(10000)});
  if(!r.ok)throw Error('extension_channel_http_'+r.status);
  return r.json();
 };
 const command=async(target,action,args)=>{
  const queuedAt=now()/1000;
  const payload={agent_id:target,connection_id:connectionId,...(agentInstanceId?{agent_instance_id:agentInstanceId}:(BROWSER_INSTANCE.test(target)?{agent_instance_id:target}:{})),action,args:{url,room,...args,expected_account_scope:accountScope}};
  assertAllowed();
  const q=await request('/v1/agent/commands',payload);
  if(!q.queued||typeof q.id!=='string')throw Error('extension_enqueue_unknown');
  for(let i=0;i<45;i++){
   const d=await request('/v1/agent/results?agent_id='+encodeURIComponent(target)+'&connection_id='+encodeURIComponent(connectionId)+'&limit=100');
   const expectedInstance=agentInstanceId||(target!==agentId&&BROWSER_INSTANCE.test(target)?target:'');
   const x=(d.results||[]).find(r=>r.id===q.id&&r.action===action&&r.connection_id===connectionId&&(!expectedInstance||r.agent_instance_id===expectedInstance)&&r.received_at>=queuedAt);
   if(x)return x.result;
   await wait(1000);
  }
  throw Error('extension_result_unknown');
 };
 const targetForRoom=async()=>{
  assertAllowed();
  if(agentInstanceId)return agentId;
  const status=getAgentStatus?await getAgentStatus():await request('/v1/agent/status');
  if(!status||!Array.isArray(status.agents))throw Error('extension_agent_status_unavailable');
  const entry=status.agents.find(item=>item.agent_id===agentId);
  // Before the first profile poll, a legacy alias cannot identify any room.
  // Do not queue a send that whichever browser appears first might consume.
  if(!entry)throw Error('extension_agent_status_unavailable');
  if(!entry.identity_conflict)return agentId;
  const instances=entry.instances;
  if(entry.legacy_unaddressable||!Array.isArray(instances)||!instances.length||instances.length>32||
     new Set(instances).size!==instances.length||instances.some(id=>typeof id!=='string'||!/^browser-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(id)))
   throw Error('extension_identity_conflict_unresolved');
  const probes=await Promise.all(instances.map(async id=>{
   const result=await command(id,'dot_profile_probe',{});
   if(!result?.ok||typeof result.matches!=='boolean')throw Error('extension_identity_conflict_unresolved');
   return result.matches?id:null;
  }));
  const matches=probes.filter(Boolean);
  if(matches.length!==1)throw Error('extension_identity_conflict_unresolved');
  return matches[0];
 };
 const run=async(action,args,target)=>{
  if(!['dot_snapshot','dot_send','dot_media_read'].includes(action))throw Error('unsupported_extension_dot_action');
  if(action==='dot_media_read')return readDotMediaUsing((operation,extra)=>command(target,'dot_media_read',{operation,source_id:args.source_id,...extra}),args,room);
  if(action==='dot_send'&&args.media?.length){
   const files=loadMedia(args.media),id=transferId();
   const meta=args.media.map(m=>({filename:m.filename,mime:m.mime,size:m.size,sha256:m.sha256}));
   const call=async(operation,extra={})=>command(target,'dot_media',{operation,transfer_id:id,...extra});
   let started=false;
   try{
    let result=await call('start',{files:meta,text:args.text||'',reply_to:args.reply_to||null,entities:args.entities||[]});if(!result.ok)return result;started=true;
    for(let index=0;index<files.length;index++){
     const bytes=Buffer.from(files[index].data,'base64');
     for(let offset=0;offset<bytes.length;offset+=128*1024){
      const chunk=bytes.subarray(offset,offset+128*1024);
      result=await call('chunk',{index,offset,data:chunk.toString('base64')});
      if(!result.ok||result.index!==index||result.received!==offset+chunk.length)throw Error('media_stage_unknown');
     }
    }
    result=await call('upload');if(!result.ok)return result;
    let stable=0;for(let i=0;i<30;i++){
     await wait(1000);result=await call('inspect');if(!result.ok)return result;
     stable=result.ready?stable+1:0;
     if(stable>=2)return await call('send',{text:args.text||''});
    }
    return {ok:false,error:'media_upload_timeout_requires_review'};
   }finally{if(started)try{await call('finish')}catch{}}
  }
  if(action==='dot_send'&&(typeof args.text!=='string'||!args.text.trim()))throw Error('invalid_message');
  return command(target,'dot_action',{operation:action==='dot_snapshot'?'snapshot':'send',after:args.after||null,reply_to:args.reply_to||null,...(action==='dot_send'?{text:args.text,...(args.entities?.length?{entities:args.entities}:{})}:{})});
 };
 return async(action,args)=>{
  if(!['dot_snapshot','dot_send','dot_media_read'].includes(action))throw Error('unsupported_extension_dot_action');
  const target=await targetForRoom();
  if(action==='dot_send'&&discardDrafts){
   if(!args.media?.length&&(typeof args.text!=='string'||!args.text.trim()))throw Error('invalid_message');
   const cleared=await command(target,'dot_draft_recover',{operation:'discard',confirm:'discard_room_drafts'});
   if(!cleared.ok)return cleared;
   const result=await run(action,args,target);
   return {...result,warnings:[...(cleared.warnings||[]),...(result.warnings||[])]};
  }
  if(action!=='dot_send'||!preserveDraft)return run(action,args,target);
  if(!args.media?.length&&(typeof args.text!=='string'||!args.text.trim()))throw Error('invalid_message');
  const backup_id=transferId();
  const backup=await command(target,'dot_draft_backup',{backup_id,purpose:'automatic'});
  if(!backup.ok){if(backup.error==='empty_draft')return run(action,args,target);return backup;}
  const cleared=await command(target,'dot_draft_recover',{backup_id,operation:'clear',confirm:'preserve_then_clear'});
  if(!cleared.ok)return cleared;
  // If the send times out, its result is UNKNOWN. Do not touch the composer
  // while the original browser operation may still be running.
  const result=await run(action,args,target);
  try{
   if(result.ok)await wait(1000); // Let Dot clear the submitted composer first.
   const restored=await command(target,'dot_draft_recover',{backup_id,operation:'restore',confirm:'restore_preserved_draft'});
   if(!restored.ok)return {...result,draft_restore:'requires_review',draft_backup_id:backup_id};
  }catch{return {...result,draft_restore:'outcome_unknown',draft_backup_id:backup_id};}
  return {...result,draft_restore:'restored'};
 };
}
