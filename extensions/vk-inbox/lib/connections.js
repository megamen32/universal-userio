(function(root){
  const KEY="userioConnections", BIND="userioAccountBindings";
  async function raw(){const s=await chrome.storage.local.get({[KEY]:null,endpoint:"http://127.0.0.1:18093",routeId:"vk-browser"}); if(Array.isArray(s[KEY]))return s[KEY]; const cs=[{id:"legacy-default",label:"UserIO",endpoint:s.endpoint,enabled:true,kind:"userio",legacy:true}]; await chrome.storage.local.set({[KEY]:cs}); return cs;}
  async function list(){return raw()}
  async function get(id){return (await raw()).find(x=>x.id===id)||null}
  async function save(input){const cs=await raw(); const c={id:input.id||crypto.randomUUID(),label:String(input.label||"UserIO"),endpoint:String(input.endpoint||cs[0].endpoint),enabled:input.enabled!==false,kind:"userio"}; const i=cs.findIndex(x=>x.id===c.id); if(i<0)cs.push(c);else cs[i]={...cs[i],...c}; await chrome.storage.local.set({[KEY]:cs}); return c}
  async function remove(id){if(id==="legacy-default")throw new Error("cannot_remove_legacy_default"); await chrome.storage.local.set({[KEY]:(await raw()).filter(x=>x.id!==id)}); return {ok:true}}
  async function bindings(){return (await chrome.storage.local.get({[BIND]:{}}))[BIND]||{}}
  function key(a){return [a.provider,a.userId||"",a.workspaceId||a.accountId||a.accountScope||"legacy"].join(":")}
  async function observeAccount(a){const k=key(a),b=await bindings(); if(!b[k]){b[k]={provider:a.provider,userId:a.userId||"",workspaceId:a.workspaceId||a.accountId||a.accountScope||"",accountScope:a.accountScope||"",label:a.label||"",connectionIds:a.provider==="vk"?["legacy-default"]:[]};await chrome.storage.local.set({[BIND]:b})} return {key:k,...b[k]}}
  async function updateBindings(k,ids){const b=await bindings(); if(!b[k])throw new Error("account_not_observed"); b[k].connectionIds=[...new Set(ids)]; await chrome.storage.local.set({[BIND]:b}); return b[k]}
  async function accounts(){return Object.entries(await bindings()).map(([key,value])=>({key,...value}))}
  async function subscriptions(a){const account=await observeAccount(a),cs=await raw(); return account.connectionIds.map(id=>cs.find(c=>c.id===id)).filter(c=>c&&c.enabled)}
  root.UserIOConnections={list,get,save,remove,accounts,observeAccount,updateBindings,subscriptions,accountKey:key};
})(typeof self!=="undefined"?self:this);
