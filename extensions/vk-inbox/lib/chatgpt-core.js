// Pure ChatGPT snapshot reducer. No I/O and no active mutable state.
(function(root){
 const scope=/^[a-f0-9]{64}$/, ref=/^[A-Za-z0-9_-]{1,128}$/, provider=/^[a-z][a-z0-9_-]{1,31}$/;
 const clone=x=>JSON.parse(JSON.stringify(x));
 const accountKey=(name,user,workspace)=>{
  if(!provider.test(String(name||''))||typeof user!=="string"||!user||user.length>160||typeof workspace!=="string"||workspace.length>160)
   throw new Error('account_identity_required');
  return JSON.stringify([name,user,workspace]);
 };
 function initialState(){return {epoch:0,records:{}};}
 function hydrate(raw){
  if(!raw||typeof raw!=="object")return initialState();
  const records={};let maxEpoch=0;
  for(const [id,value] of Object.entries(raw.records&&typeof raw.records==="object"?raw.records:{})){
   if(!value||typeof value!=="object"||typeof value.accountKey!=="string"||typeof value.scope!=="string"||!scope.test(value.scope)||!ref.test(String(value.ref||''))||!Array.isArray(value.messages))continue;
   const messages=value.messages.map(normalize).filter(Boolean).slice(-100);
   if(!messages.length)continue;
   const row={provider:typeof value.provider==="string"?value.provider:"chatgpt",userId:typeof value.userId==="string"?value.userId:"",workspaceId:typeof value.workspaceId==="string"?value.workspaceId:"",label:typeof value.label==="string"?value.label:"",email:typeof value.email==="string"?value.email:"",scope:value.scope,accountKey:value.accountKey,ref:String(value.ref),messages,coverage:value.coverage==="addressed"?"addressed":"partial",epoch:Number.isSafeInteger(value.epoch)&&value.epoch>0?value.epoch:1,revision:Number.isSafeInteger(value.revision)&&value.revision>0?value.revision:1,observedAt:Number(value.observedAt)||0,sourceVersion:Number(value.sourceVersion)||0,stale:value.stale===true};
   records[id]=row;maxEpoch=Math.max(maxEpoch,row.epoch);
  }
  return {epoch:Math.max(Number.isSafeInteger(raw.epoch)&&raw.epoch>0?raw.epoch:0,maxEpoch),records};
 }
 function serialize(state){return clone({version:1,epoch:state.epoch,records:state.records});}
 function normalize(m){
  if(!m||!ref.test(String(m.id||''))||!['user','assistant'].includes(m.role)||typeof m.text!=='string'||!m.text.trim())return null;
  return {id:String(m.id),role:m.role,text:m.text.trim().slice(0,16000),status:typeof m.status==='string'?m.status.slice(0,80):'',end_turn:m.end_turn===true,create_time:Number(m.create_time??m.ts)||0,update_time:Number(m.update_time)||0,phase:typeof m.phase==='string'?m.phase.slice(0,80):'',model:typeof m.model==='string'?m.model.slice(0,100):''};
 }
 function fromProvider(data){
  if(!data||typeof data!=='object')return [];
  let chain=Array.isArray(data.messages)?data.messages:[];
  if(!chain.length){const map=data.mapping&&typeof data.mapping==='object'?data.mapping:{};let n=typeof data.current_node==='string'?data.current_node:null,seen=new Set();for(let i=0;n&&i<10000;i++){if(seen.has(n))break;seen.add(n);const node=map[n];if(!node)break;if(node.message)chain.push(node.message);n=typeof node.parent==='string'?node.parent:null;}chain.reverse();}
  return chain.filter(m=>m&&(m.author?.role!=='assistant'||((!m.channel||['final','commentary'].includes(m.channel))&&(!m.recipient||m.recipient==='all')))).map(m=>({id:m.id,role:m.author?.role||m.role,text:Array.isArray(m.content?.parts)?m.content.parts.map(p=>typeof p==='string'?p:p?.text||p?.content||'').join('\n'):m.text,status:m.status,end_turn:m.end_turn,create_time:m.create_time,update_time:m.update_time,phase:m.channel,model:m.metadata?.model_slug}));
 }
 function view(r){const ms=r.messages,u=[...ms].reverse().find(m=>m.role==='user')||null,i=u?ms.lastIndexOf(u):-1,as=i<0?[]:ms.slice(i+1).filter(m=>m.role==='assistant'),last=as.at(-1)||null,done=[...as].reverse().find(m=>m.status==='finished_successfully'&&m.end_turn===true)||null,ready=r.coverage==='addressed'&&!!done&&last===done;return {ok:true,provider:r.provider,user_id:r.userId,workspace_id:r.workspaceId,account_label:r.label,account_email:r.email,account_key:r.accountKey,account_scope:r.scope,chat_ref:r.ref,snapshot_epoch:String(r.epoch),revision:r.revision,coverage:r.coverage,complete:r.coverage==='addressed',stale:r.stale,observed_at:r.observedAt,source_version:r.sourceVersion,conflict:r.conflict||null,limitations:r.coverage==='partial'?['partial_capture','completion_unverified']:[],messages:clone(ms),latest_user:u?clone(u):null,latest_assistant:clone(done||last),ready,phase:ready?'idle':'unknown'};}
 function transition(state,event){
  if(!state||!event||!scope.test(event.account_scope)||!ref.test(event.chat_ref)||!['partial','addressed'].includes(event.coverage))return {state:clone(state||initialState()),snapshot:{ok:false,error:'invalid_snapshot'},effects:[]};
  const name=event.provider||'chatgpt',user=event.user_id||'',workspace=event.workspace_id??event.account_id??'';
  let expected;try{expected=accountKey(name,user,workspace);}catch{return {state:clone(state),snapshot:{ok:false,error:'account_identity_required'},effects:[]};}
  if(event.account_key&&event.account_key!==expected)return {state:clone(state),snapshot:{ok:false,error:'account_key_mismatch'},effects:[]};
  const ak=expected;
  const id=ak+':'+event.chat_ref,old=state.records[id],at=Number(event.observed_at)||0,version=Number(event.source_version)||0;
  if(old&&(at<old.observedAt||(version&&old.sourceVersion&&version<old.sourceVersion))){const s=view(old);s.conflict='older_snapshot_ignored';s.limitations=[...s.limitations,'older_snapshot_ignored'];return {state:clone(state),snapshot:s,effects:[]};}
  const incoming=Array.isArray(event.messages)?event.messages:fromProvider(event.data),parsed=incoming.map(normalize).filter(Boolean).slice(-100);if(!parsed.length)return {state:clone(state),snapshot:{ok:false,error:'provider_snapshot_unreadable'},effects:[]};
  const next={provider:name,userId:user,workspaceId:workspace,label:typeof event.account_label==='string'?event.account_label.slice(0,160):'',email:typeof event.account_email==='string'?event.account_email.slice(0,254):'',scope:event.account_scope,accountKey:ak,ref:event.chat_ref,messages:parsed,coverage:event.coverage,epoch:state.epoch+1,revision:(old?.revision||0)+1,observedAt:at,sourceVersion:version,stale:false};
  const records={...state.records,[id]:next};return {state:{epoch:next.epoch,records},snapshot:view(next),effects:[{type:'publishChatSnapshot',snapshot:view(next)}]};
 }
 root.UserIOChatGPTCore={initialState,hydrate,serialize,transition,fromProvider,accountKey,view};
})(typeof self!=='undefined'?self:this);
