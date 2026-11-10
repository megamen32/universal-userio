// Effect adapter for the pure ChatGPT snapshot reducer. The reducer itself
// never touches storage or HTTP; this module owns durable worker state and
// interprets its explicit publish effects.
(function(root){
 const KEY='chatgptCanonicalStateV1';
 let state;
 let loading;
 let queue=Promise.resolve();
 async function load(){
  if(state)return state;
  if(!loading)loading=(async()=>{
   const stored=await chrome.storage.local.get({[KEY]:null});
   state=root.UserIOChatGPTCore.hydrate(stored[KEY]);
   root.UserIOChatGPTState=state;
   return state;
  })().finally(()=>{loading=undefined;});
  return loading;
 }
 async function persist(next){
  state=next;root.UserIOChatGPTState=next;
  await chrome.storage.local.set({[KEY]:root.UserIOChatGPTCore.serialize(next)});
 }
 async function reduce(event){
  const job=queue.catch(()=>{}).then(async()=>{
   const update=root.UserIOChatGPTCore.transition(await load(),event);
   if(update.snapshot?.ok)await persist(update.state);
   return update;
  });
  queue=job.catch(()=>{});
  return job;
 }
 async function effects(update){
  const results=[];
  for(const effect of update.effects||[]){
   if(effect.type!=='publishChatSnapshot')continue;
   if(typeof root.UserIO?.publishChatSnapshot!=='function'){results.push({ok:false,error:'userio_publish_unavailable'});continue;}
   try{results.push(await root.UserIO.publishChatSnapshot(effect.snapshot));}
   catch(error){results.push({ok:false,error:String(error?.message||error)});}
  }
  return results;
 }
 async function transition(event){
  const update=await reduce(event);
  return {...update,effect_results:await effects(update)};
 }
 root.UserIOChatGPTStore={load,transition,serialize:()=>root.UserIOChatGPTCore.serialize(state||root.UserIOChatGPTCore.initialState())};
})(typeof self!=='undefined'?self:this);
