(function(root){
  const KEY="userioConnections", BIND="userioAccountBindings";
  const connectionId = (value) => typeof value === "string" && /^[A-Za-z0-9._:-]{1,128}$/.test(value);
  const validateConnection = (input) => {
    if (!input || !connectionId(input.id) || input.kind !== "userio" || typeof input.endpoint !== "string") return {ok:false,error:"invalid_connection"};
    try { const u=new URL(input.endpoint); if(!["http:","https:"].includes(u.protocol)||u.username||u.password||u.search||u.hash||(u.protocol==="http:"&&!['localhost','127.0.0.1','[::1]'].includes(u.hostname))) return {ok:false,error:"invalid_endpoint"}; } catch (_) { return {ok:false,error:"invalid_endpoint"}; }
    return {ok:true};
  };
  const transitionConnections = (state, action) => {
    const current=Array.isArray(state)?state.map(x=>({...x})):[];
    if(action.type==="save") { const next={...action.input,kind:"userio",enabled:action.input.enabled!==false}; const check=validateConnection(next); if(!check.ok) return check; const i=current.findIndex(x=>x.id===next.id); if(i<0)current.push(next);else current[i]={...current[i],...next}; return {ok:true,state:current,value:next,effects:[{type:"storage.set",key:KEY,value:current}]}; }
    if(action.type==="remove") { if(action.id==="legacy-default") return {ok:false,error:"cannot_remove_legacy_default"}; const next=current.filter(x=>x.id!==action.id);return {ok:true,state:next,effects:[{type:"storage.set",key:KEY,value:next}]}; }
    return {ok:false,error:"unknown_transition"};
  };
  let queue=Promise.resolve();
  const serialize=fn=>{const result=queue.then(fn);queue=result.catch(()=>{});return result};
  function reduce(state, action){
    const next={connections:state.connections.map(c=>({...c})),bindings:Object.fromEntries(Object.entries(state.bindings).map(([k,v])=>[k,{...v,connectionIds:[...v.connectionIds]}]))};
    if(action.type==='observe'){const a=action.account;if(!a||!['vk','chatgpt','zai'].includes(a.provider))return {ok:false,error:'invalid_provider'};const k=key(a);if(!next.bindings[k])next.bindings[k]={provider:a.provider,userId:a.userId||'',workspaceId:a.workspaceId||a.accountId||a.accountScope||'',accountScope:a.accountScope||'',label:a.label||'',connectionIds:a.provider==='vk'&&!a.accountId&&!a.accountScope&&!a.userId?['legacy-default']:[]};return {ok:true,state:next,value:{key:k,...next.bindings[k]},effects:[{type:'storage.set',key:BIND,value:next.bindings}]};}
    if(action.type==='bind'){if(!next.bindings[action.key])return {ok:false,error:'account_not_observed'};if(!Array.isArray(action.ids)||action.ids.some(id=>!connectionId(id)||!next.connections.some(c=>c.id===id)))return {ok:false,error:'unknown_connection'};next.bindings[action.key].connectionIds=[...new Set(action.ids)];return {ok:true,state:next,value:next.bindings[action.key],effects:[{type:'storage.set',key:BIND,value:next.bindings}]};}
    const r=transitionConnections(next.connections,action);return r.ok?{...r,state:{...next,connections:r.state}}:r;
  }
  async function raw(){const s=await chrome.storage.local.get({[KEY]:null,endpoint:(root.USERIO_CONFIG||{}).endpoint||"http://127.0.0.1:18093"}); if(Array.isArray(s[KEY]))return s[KEY].map(c=>({...c})); const cs=[{id:"legacy-default",label:"UserIO",endpoint:s.endpoint,enabled:true,kind:"userio",legacy:true}]; await chrome.storage.local.set({[KEY]:cs}); return cs;}
  async function list(){return raw()}
  async function get(id){return (await raw()).find(x=>x.id===id)||null}
  async function save(input){return serialize(async()=>{const cs=await raw(), result=reduce({connections:cs,bindings:await bindings()},{type:"save",input:{...input,id:input.id||crypto.randomUUID(),label:String(input.label||"UserIO"),endpoint:input.endpoint}}); if(!result.ok)throw new Error(result.error); for(const e of result.effects)await chrome.storage.local.set({[e.key]:e.value}); return result.value})}
  async function remove(id){const result=transitionConnections(await raw(),{type:"remove",id}); if(!result.ok)throw new Error(result.error); await chrome.storage.local.set({[KEY]:result.state}); return {ok:true}}
  async function bindings(){return (await chrome.storage.local.get({[BIND]:{}}))[BIND]||{}}
  function key(a){return [a.provider,a.userId||"",a.workspaceId||a.accountId||a.accountScope||"legacy"].join(":")}
  async function apply(action){return serialize(async()=>{const result=reduce({connections:await raw(),bindings:await bindings()},action);if(!result.ok)throw new Error(result.error);for(const e of result.effects)await chrome.storage.local.set({[e.key]:e.value});return result.value})}
  async function observeAccount(a){return apply({type:'observe',account:a})}
  async function updateBindings(k,ids){return apply({type:'bind',key:k,ids})}
  async function accounts(){return Object.entries(await bindings()).map(([key,value])=>({key,...value}))}
  async function subscriptions(a){const account=await observeAccount(a),cs=await raw(); return account.connectionIds.map(id=>cs.find(c=>c.id===id)).filter(c=>c&&c.enabled)}
  const validateReceipt=(receipt, expected)=>Boolean(receipt&&expected&&receipt.id===expected.id&&receipt.connectionId===expected.connectionId&&receipt.agentInstanceId===expected.agentInstanceId);
  root.UserIOConnections={list,get,save,remove,accounts,observeAccount,updateBindings,subscriptions,accountKey:key,validateConnection,transitionConnections,reduce,validateReceipt};
})(typeof self!=="undefined"?self:this);
