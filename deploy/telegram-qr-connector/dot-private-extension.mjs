// Keep the relay's durable identity during a browser transport migration.
// A configured extension never silently falls back to the legacy CDP path.
export function createPrivateDotCaller({binding,legacyCall,loadExtension}){
 if(binding.transport===undefined||binding.transport==='cdp')return legacyCall;
 if(binding.transport!=='extension'||typeof binding.agentId!=='string'||!binding.agentId.trim())throw Error('invalid_dot_extension_binding');
 let extension;
 return async(action,args)=>{
  if(args.tab_id!==binding.pageId)throw Error('dot_extension_binding_changed');
  extension??=loadExtension();
  return extension(action,args);
 };
}
