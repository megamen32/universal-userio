(function(root){
  const KEY="userioConnections", BIND="userioAccountBindings";
  const connectionId = (value) => typeof value === "string" && /^[A-Za-z0-9._:-]{1,128}$/.test(value);
  const validateConnection = (input) => {
    if (!input || !connectionId(input.id) || input.kind !== "userio" || typeof input.endpoint !== "string") return {ok:false,error:"invalid_connection"};
    try { const u=new URL(input.endpoint); if(!["http:","https:"].includes(u.protocol)||u.username||u.password||u.search||u.hash) return {ok:false,error:"invalid_endpoint"}; } catch (_) { return {ok:false,error:"invalid_endpoint"}; }
    return {ok:true};
  };
  const transitionConnections = (state, action) => {
    const current=Array.isArray(state)?state.map(x=>({...x})):[];
    if(action.type==="save") { const next={...action.input,kind:"userio",enabled:action.input.enabled!==false}; const check=validateConnection(next); if(!check.ok) return check; const i=current.findIndex(x=>x.id===next.id); if(i<0)current.push(next);else current[i]={...current[i],...next}; return {ok:true,state:current,value:next,effects:[{type:"storage.set",key:KEY,value:current}]}; }
    if(action.type==="remove") { if(action.id==="legacy-default") return {ok:false,error:"cannot_remove_legacy_default"}; const next=current.filter(x=>x.id!==action.id);return {ok:true,state:next,effects:[{type:"storage.set",key:KEY,value:next}]}; }
    return {ok:false,error:"unknown_transition"};
  };
  async function raw(){const s=await chrome.storage.local.get({[KEY]:null,endpoint:"http://127.0.0.1:18093",routeId:"vk-browser"}); if(Array.isArray(s[KEY]))return s[KEY]; const cs=[{id:"legacy-default",label:"UserIO",endpoint:s.endpoint,enabled:true,kind:"userio",legacy:true}]; await chrome.storage.local.set({[KEY]:cs}); return cs;}
  async function list(){return raw()}
  async function get(id){return (await raw()).find(x=>x.id===id)||null}
  async function save(input){const cs=await raw(), result=transitionConnections(cs,{type:"save",input:{...input,id:input.id||crypto.randomUUID(),label:String(input.label||"UserIO"),endpoint:String(input.endpoint||cs[0].endpoint)}}); if(!result.ok)throw new Error(result.error); await chrome.storage.local.set({[KEY]:result.state}); return result.value}
  async function remove(id){const result=transitionConnections(await raw(),{type:"remove",id}); if(!result.ok)throw new Error(result.error); await chrome.storage.local.set({[KEY]:result.state}); return {ok:true}}
  async function bindings(){return (await chrome.storage.local.get({[BIND]:{}}))[BIND]||{}}
  function key(a){return [a.provider,a.userId||"",a.workspaceId||a.accountId||a.accountScope||"legacy"].join(":")}
  async function observeAccount(a){const k=key(a),b=await bindings(); if(!b[k]){b[k]={provider:a.provider,userId:a.userId||"",workspaceId:a.workspaceId||a.accountId||a.accountScope||"",accountScope:a.accountScope||"",label:a.label||"",connectionIds:a.provider==="vk"?["legacy-default"]:[]};await chrome.storage.local.set({[BIND]:b})} return {key:k,...b[k]}}
  async function updateBindings(k,ids){const b=await bindings(); if(!b[k])throw new Error("account_not_observed"); b[k].connectionIds=[...new Set(ids)]; await chrome.storage.local.set({[BIND]:b}); return b[k]}
  async function accounts(){return Object.entries(await bindings()).map(([key,value])=>({key,...value}))}
  async function subscriptions(a){const account=await observeAccount(a),cs=await raw(); return account.connectionIds.map(id=>cs.find(c=>c.id===id)).filter(c=>c&&c.enabled)}
  const validateReceipt=(receipt, expected)=>Boolean(receipt&&expected&&receipt.id===expected.id&&receipt.connectionId===expected.connectionId&&receipt.agentInstanceId===expected.agentInstanceId);
  root.UserIOConnections={list,get,save,remove,accounts,observeAccount,updateBindings,subscriptions,accountKey:key,validateConnection,transitionConnections,validateReceipt};
})(typeof self!=="undefined"?self:this);
