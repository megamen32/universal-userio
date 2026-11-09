// Strict ChatGPT scoped read/inventory. No site credentials ever leave the page.
(function (root) {
  const ORIGIN="https://chatgpt.com";
  const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const lib={};
  lib.validRef=v=>typeof v==="string"&&UUID.test(v);
  lib.exactTab=async(ref)=>{
    if(!lib.validRef(ref))return {ok:false,error:"invalid_chat_ref"};
    const url=ORIGIN+"/c/"+ref;
    const tabs=await chrome.tabs.query({url:"https://chatgpt.com/*"});
    const matches=tabs.filter(t=>{
      try {const u=new URL(t.url);return u.origin===ORIGIN&&u.pathname==="/c/"+ref;}catch{return false;}
    });
    if(matches.length>1)return {ok:false,error:"ambiguous_exact_tab"};
    let tab=matches[0];
    if(tab) await root.UserIOTabGroups?.assign("chatgpt",tab.id);
    if(!tab){
      const allocated=await root.UserIOTabGroups?.acquire("chatgpt",{slot:"chat:"+ref,url,active:false,match:t=>{try{const u=new URL(t.url);return u.origin===ORIGIN&&u.pathname==="/c/"+ref}catch{return false}}});
      if(allocated?.ok)tab=allocated.tab;
      else if(allocated?.error && allocated.error!=="tab_groups_unavailable")return allocated;
    }
    if(!tab)tab=await chrome.tabs.create({url,active:false});
    if(tab) await root.UserIOTabGroups?.assign("chatgpt",tab.id);
    const started=Date.now();
    while(Date.now()-started<25000){
      const current=await chrome.tabs.get(tab.id).catch(()=>null);
      if(!current)return {ok:false,error:"tab_closed"};
      if(current.status==="complete"){
        try{const u=new URL(current.url);if(u.origin!==ORIGIN||u.pathname!=="/c/"+ref)
          return {ok:false,error:"tab_navigation_mismatch"};}
        catch{return {ok:false,error:"tab_navigation_mismatch"};}
        return {ok:true,tab:current};
      }
      await new Promise(resolve=>setTimeout(resolve,350));
    }
    return {ok:false,error:"tab_load_timeout"};
  };
  lib.accountScope=async()=>{
    try{
      const tabs=await chrome.tabs.query({url:"https://chatgpt.com/*"});
      let tab=tabs.find(t=>{try{return new URL(t.url).origin===ORIGIN;}catch{return false;}});
      if(tab) await root.UserIOTabGroups?.assign("chatgpt",tab.id);
      if(!tab){
        const allocated=await root.UserIOTabGroups?.acquire("chatgpt",{slot:"identity",url:ORIGIN+"/",active:false,match:t=>{try{return new URL(t.url).origin===ORIGIN}catch{return false}}});
        if(allocated?.ok)tab=allocated.tab;
        else if(allocated?.error && allocated.error!=="tab_groups_unavailable")return allocated;
      }
      if(!tab)tab=await chrome.tabs.create({url:ORIGIN+"/",active:false});
      const deadline=Date.now()+25000;
      while(tab.status!=="complete" && Date.now()<deadline){
        await new Promise(resolve=>setTimeout(resolve,300));
        tab=await chrome.tabs.get(tab.id);
      }
      if(tab.status!=="complete")return {ok:false,error:"tab_load_timeout"};
      const [entry]=await chrome.scripting.executeScript({target:{tabId:tab.id},world:"MAIN",
        func:async()=>{
          if(location.origin!=="https://chatgpt.com")
            return {ok:false,error:"origin_mismatch"};
          const controller=new AbortController();
          const timer=setTimeout(()=>controller.abort(),20000);
          try{
            const response=await fetch("/api/auth/session",{credentials:"include",signal:controller.signal});
            if(!response.ok)return {ok:false,error:"authentication_required",status:response.status};
            const session=await response.json();
            const accountId=session?.user?.id;
            if(typeof accountId!=="string"||!accountId)
              return {ok:false,error:"account_scope_unavailable"};
            const digest=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(accountId));
            return {ok:true,account_scope:Array.from(new Uint8Array(digest),
              value=>value.toString(16).padStart(2,"0")).join("")};
          }catch(e){return {ok:false,error:e?.name==="AbortError"?"account_scope_timeout":"account_scope_unavailable"};}
          finally{clearTimeout(timer);}
        }});
      return entry?.result??{ok:false,error:"account_scope_no_result"};
    }catch{return {ok:false,error:"account_scope_transport_unavailable"};}
  };
  lib.list=async(args={})=>{
    const scope=args.expected_account_scope||"";
    if(scope && !/^[0-9a-f]{64}$/.test(scope))return {ok:false,error:"invalid_account_scope"};
    try{
      const tabs=await chrome.tabs.query({url:"https://chatgpt.com/*"});
      let tab=tabs.find(t=>{try{return new URL(t.url).origin===ORIGIN;}catch{return false;}});
      if(tab) await root.UserIOTabGroups?.assign("chatgpt",tab.id);
      if(!tab){
        const allocated=await root.UserIOTabGroups?.acquire("chatgpt",{slot:"identity",url:ORIGIN+"/",active:false,match:t=>{try{return new URL(t.url).origin===ORIGIN}catch{return false}}});
        if(allocated?.ok)tab=allocated.tab;
        else if(allocated?.error && allocated.error!=="tab_groups_unavailable")return allocated;
      }
      if(!tab)tab=await chrome.tabs.create({url:ORIGIN+"/",active:false});
      const [entry]=await chrome.scripting.executeScript({target:{tabId:tab.id},world:"MAIN",
        func:async(scope)=>{
          if(location.origin!=="https://chatgpt.com")return {ok:false,error:"origin_mismatch"};
          const c=new AbortController(),timer=setTimeout(()=>c.abort(),20000);
          try{
            const a=await fetch("/api/auth/session",{credentials:"include",signal:c.signal});
            if(!a.ok)return {ok:false,error:"authentication_required",status:a.status};
            const s=await a.json(),token=typeof s?.accessToken==="string"?s.accessToken:"";
            if(!token)return {ok:false,error:"authentication_required"};
            if(scope){
              if(typeof s?.user?.id!=="string")return {ok:false,error:"account_scope_unavailable"};
              const bytes=new TextEncoder().encode(s.user.id);
              const digest=await crypto.subtle.digest("SHA-256",bytes);
              const actual=Array.from(new Uint8Array(digest),v=>v.toString(16).padStart(2,"0")).join("");
              if(actual!==scope)return {ok:false,error:"account_scope_mismatch"};
            }
            const r=await fetch("/backend-api/conversations?offset=0&limit=30&order=updated&is_archived=false",{
              credentials:"include",headers:{Authorization:"Bearer "+token},signal:c.signal
            });
            if(!r.ok)return {ok:false,error:r.status===429?"history_http_429":"list_unavailable",
              status:r.status,retry_after:Math.min(3600,Math.max(0,Number(r.headers.get("Retry-After"))||0))};
            const j=await r.json();
            if(!Array.isArray(j.items))return {ok:false,error:"list_invalid"};
            const re=/^[0-9a-f]{8}-[0-9a-f-]{27,72}$/i;
            return {ok:true,chats:j.items.filter(v=>v&&typeof v.id==="string"&&re.test(v.id))
              .slice(0,30).map(v=>({id:v.id,title:String(v.title||"ChatGPT").slice(0,160),
                update_time:typeof v.update_time==="number"?v.update_time:0}))};
          }catch(e){return {ok:false,error:e?.name==="AbortError"?"list_timeout":"list_unavailable"};}
          finally{clearTimeout(timer);}
        },args:[scope]});
      return entry?.result??{ok:false,error:"list_no_result"};
    }catch{return {ok:false,error:"list_transport_unavailable"};}
  };
  lib.read=async(args={})=>{
    const ref=args.chat_ref,scope=args.expected_account_scope||"";
    if(!lib.validRef(ref))return {ok:false,error:"invalid_chat_ref"};
    if(scope && !/^[0-9a-f]{64}$/.test(scope))return {ok:false,error:"invalid_account_scope"};
    try{
      const located=await lib.exactTab(ref);
      if(!located.ok)return located;
      const [entry]=await chrome.scripting.executeScript({target:{tabId:located.tab.id},world:"MAIN",
        func:async(ref,maxText,scope)=>{
          if(location.origin!=="https://chatgpt.com"||location.pathname!=="/c/"+ref)
            return {ok:false,error:"chat_ref_mismatch"};
          const c=new AbortController(),timer=setTimeout(()=>c.abort(),22000);
          try{
            const a=await fetch("/api/auth/session",{credentials:"include",signal:c.signal});
            if(!a.ok)return {ok:false,error:"authentication_required",status:a.status};
            const s=await a.json(),token=typeof s?.accessToken==="string"?s.accessToken:"";
            if(!token)return {ok:false,error:"authentication_required"};
            if(scope){
              if(typeof s?.user?.id!=="string")return {ok:false,error:"account_scope_unavailable"};
              const digest=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(s.user.id));
              const actual=Array.from(new Uint8Array(digest),v=>v.toString(16).padStart(2,"0")).join("");
              if(actual!==scope)return {ok:false,error:"account_scope_mismatch"};
            }
            // The current ChatGPT history endpoint returns the active branch as
            // ordered messages, including final-channel and native end_turn.
            // The older singular mapping endpoint is retained only for a 404.
            const headers={Authorization:"Bearer "+token};
            if(typeof s?.account?.id==="string"&&s.account.id)
              headers["chatgpt-account-id"]=s.account.id;
            let response=await fetch("/backend-api/conversations/"+ref+
              "?num_turns=60&include_has_versions=true",{
                credentials:"include",headers,signal:c.signal
              });
            let legacy=false;
            if(response.status===404){
              legacy=true;
              response=await fetch("/backend-api/conversation/"+ref,{
                credentials:"include",headers,signal:c.signal
              });
            }
            if(!response.ok)return {ok:false,error:response.status===429?"history_http_429":"history_unavailable",
              status:response.status,retry_after:Math.min(3600,Math.max(0,Number(response.headers.get("Retry-After"))||0))};
            const data=await response.json();
            let chain=[];
            if(!legacy && Array.isArray(data?.messages)){
              if(data.conversation_id!==ref)return {ok:false,error:"history_identity_mismatch"};
              chain=data.messages;
            }else if(data?.mapping&&typeof data.mapping==="object"){
              const mapping=data.mapping;
              let current=typeof data.current_node==="string"?data.current_node:null;
              const seen=new Set();
              for(let n=0;current&&n<10000;n++){
                if(seen.has(current))return {ok:false,error:"history_cycle"};
                seen.add(current);const node=mapping[current];
                if(!node)break;
                if(node.message)chain.push(node.message);
                current=typeof node.parent==="string"?node.parent:null;
              }
              chain.reverse();
            }else return {ok:false,error:"history_invalid"};
            if(!chain.length)return {ok:false,error:"history_empty"};
            const readPart=part=>{
              if(typeof part==="string")return part;
              if(!part||typeof part!=="object")return "";
              if(typeof part.text==="string")return part.text;
              if(typeof part.content==="string")return part.content;
              if(part.content_type==="image_asset_pointer"||part.content_type==="image")return "[Изображение]";
              return "";
            };
            const shape=m=>{
              const role=m?.author?.role;
              if(role!=="user"&&role!=="assistant")return null;
              const channel=typeof m.channel==="string"?m.channel:"";
              if(role==="assistant"&&((channel&&channel!=="final"&&channel!=="commentary")
                ||(m.recipient&&m.recipient!=="all")))return null;
              const parts=Array.isArray(m.content?.parts)?m.content.parts:[];
              const text=(parts.length?parts.map(readPart).join("\n")
                :(typeof m.content?.text==="string"?m.content.text:"")).trim().slice(0,maxText);
              if(!text)return null;
              return {id:String(m.id||"").slice(0,128),role,text,
                status:typeof m.status==="string"?m.status:"",
                end_turn:m.end_turn===true,create_time:Number(m.create_time)||0,
                update_time:Number(m.update_time)||0,
                phase:channel||((typeof m.metadata?.phase==="string")?m.metadata.phase:""),
                model:typeof m.metadata?.model_slug==="string"?m.metadata.model_slug:""};
            };
            let latest_user=null,latest_assistant=null;
            let lastAssistant=null,latestCompleted=null;
            for(const m of chain){
              const v=shape(m);if(!v)continue;
              if(v.role==="user"){
                latest_user=v;lastAssistant=null;latestCompleted=null;
              }else if(latest_user){
                lastAssistant=v;
                if(v.status==="finished_successfully"&&v.end_turn===true)
                  latestCompleted=v;
              }
            }
            if(!latest_user)return {ok:false,error:"latest_user_unavailable"};
            latest_assistant=latestCompleted||lastAssistant;
            // A later native in-progress assistant invalidates the completed
            // receipt. Never infer completion from text, DOM stability, or age.
            const ready=!!latestCompleted&&lastAssistant===latestCompleted;
            return {ok:true,chat_ref:ref,latest_user,latest_assistant,ready,
              phase:ready?"idle":"unknown"};
          }catch(e){return {ok:false,error:e?.name==="AbortError"?"read_timeout":"history_unavailable"};}
          finally{clearTimeout(timer);}
        },args:[ref,16000,scope]});
      const result=entry?.result??{ok:false,error:"read_no_result"};
      if(result.ok&&result.chat_ref!==ref)return {ok:false,error:"read_ref_mismatch"};
      return result;
    }catch{return {ok:false,error:"read_transport_unavailable"};}
  };
  root.UserIOScopedChatGPT=lib;
})(typeof self!=="undefined"?self:this);
