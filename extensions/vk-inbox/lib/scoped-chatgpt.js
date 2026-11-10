// Strict ChatGPT scoped read/inventory. No site credentials ever leave the page.
(function (root) {
  const ORIGIN="https://chatgpt.com";
  const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const lib={};
  lib.observeIdentity=async(userId,workspaceId,label="")=>{
    if(typeof userId!=="string"||!userId||typeof workspaceId!=="string")return;
    const bytes=new TextEncoder().encode(JSON.stringify({provider:"chatgpt",user_id:userId,account_id:workspaceId}));
    const digest=await crypto.subtle.digest("SHA-256",bytes);
    const accountScope=Array.from(new Uint8Array(digest),v=>v.toString(16).padStart(2,"0")).join("");
    await root.UserIOConnections?.observeAccount({provider:"chatgpt",userId,workspaceId,accountScope,label,connectionIds:[]});
  };
  lib.validRef=v=>typeof v==="string"&&UUID.test(v);
  // The agent persists its command receipt before invoking this one-shot action.
  lib.firstSend=async(args={})=>{
    const scope=args.expected_account_scope,text=args.text;
    if(!/^[0-9a-f]{64}$/.test(scope||"")||typeof text!=="string"||!text.trim()||text.length>16000)
      return {ok:false,error:"invalid_first_send"};
    let tab;
    try {
      tab=await chrome.tabs.create({url:ORIGIN+"/",active:false});
      await root.UserIOTabGroups?.assign("chatgpt",tab.id);
      const until=Date.now()+25000;
      while(Date.now()<until){
        tab=await chrome.tabs.get(tab.id);
        if(tab.status==="complete")break;
        await new Promise(resolve=>setTimeout(resolve,350));
      }
      if(tab.status!=="complete")return {ok:false,error:"tab_load_timeout"};
      const [entry]=await chrome.scripting.executeScript({target:{tabId:tab.id},world:"MAIN",func:async(scope,text)=>{
        const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
        let clicked=false;
        try {
          if(location.origin!=="https://chatgpt.com"||location.pathname!=="/")return {ok:false,error:"not_empty_route"};
          const response=await fetch("/api/auth/session",{credentials:"include"});
          if(!response.ok)return {ok:false,error:"authentication_required"};
          const s=await response.json();
          if(typeof s?.user?.id!=="string"||!s.user.id)return {ok:false,error:"account_scope_unavailable"};
          const hash=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(JSON.stringify({provider:"chatgpt",user_id:s.user.id,account_id:String(s.account?.id||"")})));
          if(Array.from(new Uint8Array(hash),v=>v.toString(16).padStart(2,"0")).join("")!==scope)return {ok:false,error:"account_scope_mismatch"};
          const editor=document.querySelector("#prompt-textarea"),send=document.querySelector('[data-testid="send-button"]');
          if(!editor||!send||editor.textContent.trim()||document.querySelector('[data-message-author-role]'))return {ok:false,error:"composer_not_empty_or_ready"};
          editor.focus(); document.execCommand("insertText",false,text);
          await new Promise(resolve=>setTimeout(resolve,100));
          if(editor.textContent.trim()!==text.trim()||send.disabled)return {ok:false,error:"composer_changed"};
          const current=await fetch("/api/auth/session",{credentials:"include"});
          if(!current.ok)return {ok:false,error:"authentication_required"};
          const now=await current.json();
          if(typeof now?.user?.id!=="string"||!now.user.id)return {ok:false,error:"account_scope_unavailable"};
          const currentHash=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(JSON.stringify({provider:"chatgpt",user_id:now.user.id,account_id:String(now.account?.id||"")})));
          if(Array.from(new Uint8Array(currentHash),v=>v.toString(16).padStart(2,"0")).join("")!==scope)return {ok:false,error:"account_scope_mismatch"};
          if(location.origin!=="https://chatgpt.com"||location.pathname!=="/"||editor.textContent.trim()!==text.trim()||send.disabled||document.querySelector('[data-message-author-role]'))return {ok:false,error:"composer_changed"};
          clicked=true; send.click();
          const until=Date.now()+120000;
          while(Date.now()<until){
            const match=location.pathname.match(/^\/c\/([^/]+)$/);
            const nodes=[...document.querySelectorAll('[data-message-author-role="user"]')];
            const matching=nodes.filter(n=>n.textContent.trim()===text.trim());
            const id=matching[0]?.getAttribute("data-message-id")||matching[0]?.closest("[data-message-id]")?.getAttribute("data-message-id");
            if(match&&uuid.test(match[1])&&matching.length===1&&uuid.test(id||""))
              return {ok:true,admission:"confirmed",chat_ref:match[1],native_user_message_id:id};
            await new Promise(resolve=>setTimeout(resolve,500));
          }
          return {ok:false,error:"first_send_outcome_unknown"};
        }catch{return {ok:false,error:clicked?"first_send_outcome_unknown":"first_send_preflight_failed"};}
      },args:[scope,text]});
      return entry?.result??{ok:false,error:"first_send_outcome_unknown"};
    }catch{return {ok:false,error:"first_send_outcome_unknown"};}
  };
  lib.archive=async(args={})=>{
    const ref=args.chat_ref,scope=args.expected_account_scope;
    if(!lib.validRef(ref)||!/^[0-9a-f]{64}$/.test(scope||""))return {ok:false,error:"invalid_archive_request"};
    const exact=await lib.exactTab(ref); if(!exact.ok)return exact;
    try {
      const [entry]=await chrome.scripting.executeScript({target:{tabId:exact.tab.id},world:"MAIN",func:async(ref,scope)=>{
        let mutation=false;
        const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),20000);
        try {
          if(location.origin!=="https://chatgpt.com"||location.pathname!=="/c/"+ref)return {ok:false,error:"chat_ref_mismatch"};
          const auth=await fetch("/api/auth/session",{credentials:"include",signal:controller.signal});
          if(!auth.ok)return {ok:false,error:"authentication_required"};
          const s=await auth.json(); if(typeof s?.user?.id!=="string"||!s.user.id||!s.accessToken)return {ok:false,error:"account_scope_unavailable"};
          const hash=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(JSON.stringify({provider:"chatgpt",user_id:s.user.id,account_id:String(s.account?.id||"")})));
          if(Array.from(new Uint8Array(hash),v=>v.toString(16).padStart(2,"0")).join("")!==scope)return {ok:false,error:"account_scope_mismatch"};
          const headers={authorization:"Bearer "+s.accessToken,"content-type":"application/json",...(s.account?.id?{"chatgpt-account-id":s.account.id}:{})};
          const url="/backend-api/conversation/"+encodeURIComponent(ref);
          const read=async()=>{const r=await fetch(url,{headers,credentials:"same-origin",signal:controller.signal});if(!r.ok)throw new Error("read");return r.json()};
          const before=await read(); if(before.conversation_id!==ref)return {ok:false,error:"chat_ref_mismatch"};
          if(before.is_archived===true)return {ok:true,chat_ref:ref,archived:true,already:true};
          if(before.async_status==="running"||before.is_generating===true)return {ok:false,error:"conversation_busy"};
          mutation=true; const result=await fetch(url,{method:"PATCH",headers,credentials:"same-origin",signal:controller.signal,body:JSON.stringify({is_archived:true})});
          if(!result.ok)return {ok:false,error:"archive_outcome_unknown"};
          const after=await read(); return after.conversation_id===ref&&after.is_archived===true?{ok:true,chat_ref:ref,archived:true}:{ok:false,error:"archive_outcome_unknown"};
        }catch{return {ok:false,error:mutation?"archive_outcome_unknown":"archive_preflight_failed"};}finally{clearTimeout(timer);}
      },args:[ref,scope]});
      return entry?.result??{ok:false,error:"archive_outcome_unknown"};
    }catch{return {ok:false,error:"archive_outcome_unknown"};}
  };
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
            const digest=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(JSON.stringify({provider:"chatgpt",user_id:accountId,account_id:String(session.account?.id||"")})));
            const workspace_id=String(session.account?.id||"");
            return {ok:true,provider:"chatgpt",user_id:String(accountId),account_id:workspace_id,workspace_id,
              account_scope:Array.from(new Uint8Array(digest),
              value=>value.toString(16).padStart(2,"0")).join("")};
          }catch(e){return {ok:false,error:e?.name==="AbortError"?"account_scope_timeout":"account_scope_unavailable"};}
          finally{clearTimeout(timer);}
        }});
      const result=entry?.result??{ok:false,error:"account_scope_no_result"};
      if(result.ok)await lib.observeIdentity(result.user_id,result.workspace_id);
      return result;
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
              const bytes=new TextEncoder().encode(JSON.stringify({provider:"chatgpt",user_id:s.user.id,account_id:String(s.account?.id||"")}));
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
    const requestStarted=Date.now();
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
              const digest=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(JSON.stringify({provider:"chatgpt",user_id:s.user.id,account_id:String(s.account?.id||"")})));
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
            let chain=[], complete=false, tailVerified=false;
            const more=data?.has_more===true||data?.hasMore===true||!!data?.next_cursor||!!data?.nextCursor;
            if(!legacy && Array.isArray(data?.messages)){
              if(data.conversation_id!==ref)return {ok:false,error:"history_identity_mismatch"};
              chain=data.messages;
              tailVerified=true;
              complete=data.has_more===false&&!more;
            }else if(data?.mapping&&typeof data.mapping==="object"){
              const mapping=data.mapping;
              let current=typeof data.current_node==="string"?data.current_node:null;
              const seen=new Set();
              for(let n=0;current&&n<10000;n++){
                if(seen.has(current))break;
                seen.add(current);const node=mapping[current];
                if(!node)break;
                if(node.message)chain.push(node.message);
                if(node.parent===null){complete=!more;current=null;break;}
                current=typeof node.parent==="string"?node.parent:null;
              }
              chain.reverse();
              tailVerified=complete;
            }else return {ok:false,error:"history_invalid"};
            if(!chain.length)return {ok:false,error:"history_empty"};
            const workspace_id=String(s.account?.id||"");
            const bytes=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(JSON.stringify({provider:"chatgpt",user_id:s.user.id,account_id:workspace_id})));
            const account_scope=Array.from(new Uint8Array(bytes),v=>v.toString(16).padStart(2,"0")).join("");
            return {ok:true,chat_ref:ref,account_scope,provider:"chatgpt",user_id:String(s.user.id),account_id:workspace_id,workspace_id,account_label:String(s.user.name||s.user.email||s.user.id),account_email:String(s.user.email||""),complete,tailVerified,data:{conversation_id:data.conversation_id,messages:data.messages,mapping:data.mapping,current_node:data.current_node}};
          }catch(e){return {ok:false,error:e?.name==="AbortError"?"read_timeout":"history_unavailable"};}
          finally{clearTimeout(timer);}
        },args:[ref,16000,scope]});
      const result=entry?.result??{ok:false,error:"read_no_result"};
      if(result.ok&&result.chat_ref!==ref)return {ok:false,error:"read_ref_mismatch"};
      if(!result.ok)return result;
      if(!root.UserIOChatGPTCore)return {ok:false,error:"chatgpt_core_unavailable"};
      if(!root.UserIOChatGPTStore)return {ok:false,error:"chatgpt_core_unavailable"};
      const update=await root.UserIOChatGPTStore.transition({provider:result.provider||"chatgpt",user_id:result.user_id,workspace_id:result.workspace_id??result.account_id??"",account_label:result.account_label,account_email:result.account_email,account_scope:result.account_scope,chat_ref:ref,data:result.data,coverage:result.complete?"addressed":"partial",tail_verified:result.tailVerified,observed_at:requestStarted});
      const snapshot=update.snapshot;
      if(snapshot.ok&&!snapshot.latest_user)return {ok:false,error:"latest_user_unavailable"};
      return {...snapshot,effect_results:update.effect_results||[]};
    }catch{return {ok:false,error:"read_transport_unavailable"};}
  };
  root.UserIOScopedChatGPT=lib;
})(typeof self!=="undefined"?self:this);
