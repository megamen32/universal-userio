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
    let tab,postClickStage="before_click",lastReason="unknown";
    const unknown=(stage,reason)=>({ok:false,error:"first_send_outcome_unknown",diagnostic:{stage,reason}});
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
      let entry;
      const attemptStarted=Date.now();
      try { [entry]=await chrome.scripting.executeScript({target:{tabId:tab.id},world:"MAIN",func:async(scope,text)=>{
        let clicked=false;
        const getEditor=()=>{
          const exact=document.querySelector("#prompt-textarea");
          if(exact)return exact;
          const matches=[...document.querySelectorAll('[contenteditable="true"].ProseMirror')]
            .filter(node=>node.getClientRects().length>0);
          return matches.length===1?matches[0]:null;
        };
        const getSend=()=>{
          const exact=document.querySelector('[data-testid="send-button"]');
          if(exact)return exact;
          const matches=[...document.querySelectorAll('button[aria-label="Send"]')]
            .filter(node=>node.getClientRects().length>0);
          return matches.length===1?matches[0]:null;
        };
        // Return only bounded structural metadata, never page text or HTML.
        const preflight=(error)=>{
          const editor=getEditor();
          const send=getSend();
          const nodes=[...document.querySelectorAll('textarea,[contenteditable="true"],[role="textbox"]')].slice(0,12);
          const candidates=nodes.map(node=>({tag:/^[A-Z]{1,16}$/.test(node.tagName||"")?node.tagName.toLowerCase():"unknown",
            id:typeof node.id==="string"&&/^[A-Za-z][A-Za-z0-9_.:-]{0,63}$/.test(node.id)?node.id:""}));
          return {ok:false,error,effect:"none",admission:"not_submitted",diagnostic:{
            route:location.origin==="https://chatgpt.com"&&location.pathname==="/"?"home":"other",
            hasEditor:!!editor,editorEmpty:editor?!String(editor.textContent||"").trim():null,
            hasIdEditor:!!document.querySelector("#prompt-textarea"),hasSendButton:!!send,sendEnabled:send?!send.disabled:null,
            messageCount:Math.min(100,document.querySelectorAll('[data-message-author-role]').length),candidates}};
        };
        try {
          if(location.origin!=="https://chatgpt.com"||location.pathname!=="/")return preflight("not_empty_route");
          const response=await fetch("/api/auth/session",{credentials:"include"});
          if(!response.ok)return preflight("authentication_required");
          const s=await response.json();
          if(typeof s?.user?.id!=="string"||!s.user.id)return preflight("account_scope_unavailable");
          const hash=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(JSON.stringify({provider:"chatgpt",user_id:s.user.id,account_id:String(s.account?.id||"")})));
          if(Array.from(new Uint8Array(hash),v=>v.toString(16).padStart(2,"0")).join("")!==scope)return preflight("account_scope_mismatch");
          let editor;
          const editorDeadline=Date.now()+10000;
          while(Date.now()<editorDeadline){
            if(location.origin!=="https://chatgpt.com"||location.pathname!=="/"||document.querySelector('[data-message-author-role]'))return preflight("composer_not_empty_or_ready");
            editor=getEditor();
            if(editor){
              if(editor.textContent.trim())return preflight("composer_not_empty_or_ready");
              if(editor.isContentEditable!==false&&!editor.hasAttribute?.("disabled"))break;
            }
            editor=null;await new Promise(resolve=>setTimeout(resolve,100));
          }
          if(!editor)return preflight("composer_not_empty_or_ready");
          editor.focus();
          if(getEditor()!==editor||editor.textContent.trim()||location.pathname!=="/"||document.querySelector('[data-message-author-role]'))return preflight("composer_changed");
          if(!document.execCommand("insertText",false,text))return preflight("composer_changed");
          let send;
          const sendDeadline=Date.now()+10000;
          while(Date.now()<sendDeadline){
            if(location.origin!=="https://chatgpt.com"||location.pathname!=="/"||getEditor()!==editor||editor.textContent.trim()!==text.trim()||document.querySelector('[data-message-author-role]'))return preflight("composer_changed");
            send=getSend();
            if(send&&!send.disabled)break;
            send=null;await new Promise(resolve=>setTimeout(resolve,100));
          }
          if(!send)return preflight("composer_changed");
          const current=await fetch("/api/auth/session",{credentials:"include"});
          if(!current.ok)return preflight("authentication_required");
          const now=await current.json();
          if(typeof now?.user?.id!=="string"||!now.user.id)return preflight("account_scope_unavailable");
          const currentHash=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(JSON.stringify({provider:"chatgpt",user_id:now.user.id,account_id:String(now.account?.id||"")})));
          if(Array.from(new Uint8Array(currentHash),v=>v.toString(16).padStart(2,"0")).join("")!==scope)return preflight("account_scope_mismatch");
          send=getSend();
          if(location.origin!=="https://chatgpt.com"||location.pathname!=="/"||getEditor()!==editor||editor.textContent.trim()!==text.trim()||!send||send.disabled||document.querySelector('[data-message-author-role]'))return preflight("composer_changed");
          clicked=true; send.click();
          return {ok:true,clicked:true};
        }catch{return clicked?{ok:false,error:"first_send_outcome_unknown"}:preflight("first_send_preflight_failed");}
      },args:[scope,text]}); } catch { /* A navigation may destroy the frame after the click. */ }
      if(entry?.result && !entry.result.clicked && entry.result.error!=="first_send_outcome_unknown")return entry.result;
      const uncertainClick=!entry?.result?.clicked;
      postClickStage=uncertainClick?"before_click":"wait_route";
      lastReason=uncertainClick?"script_outcome_unknown":"route_not_ready";
      const deadline=Date.now()+60000;
      let pinnedRef=null;
      while(Date.now()<deadline){
        const current=await chrome.tabs.get(tab.id);
        let url;try{url=new URL(current.url);}catch{return unknown("wait_route","invalid_url");}
        if(url.origin!==ORIGIN)return unknown("wait_route","wrong_origin");
        const match=url.pathname.match(/^\/c\/([0-9a-f-]+)$/i);
        if(match&&lib.validRef(match[1])){
          if(pinnedRef&&pinnedRef!==match[1])return unknown("wait_route","wrong_native");
          pinnedRef=match[1];
          postClickStage="read_snapshot";
          const ref=match[1],snapshot=await lib.read({chat_ref:ref,expected_account_scope:scope});
          postClickStage="recheck_route";
          const again=await chrome.tabs.get(tab.id);
          let after;try{after=new URL(again.url);}catch{return unknown("recheck_route","invalid_url");}
          if(after.origin!==ORIGIN||after.pathname!=="/c/"+ref)return unknown("recheck_route","wrong_native");
          postClickStage="read_snapshot";
          if(snapshot.ok&&snapshot.chat_ref===ref&&snapshot.account_scope===scope&&Array.isArray(snapshot.messages)){
            const matches=snapshot.messages.filter(m=>m?.role==="user"&&typeof m.text==="string"&&m.text.trim()===text.trim()&&lib.validRef(m.id));
            if(matches.length===1){
              const created=Number(matches[0].create_time)*1000;
              if(Number.isFinite(created)&&created>0&&created>=attemptStarted-10000&&created<=Date.now()+60000)
                return {ok:true,admission:"confirmed",chat_ref:ref,native_user_message_id:matches[0].id};
              return unknown("read_snapshot","message_not_fresh");
            }
            if(matches.length>1)return unknown("read_snapshot","multiple_matches");
            lastReason="message_not_found";
          }else lastReason="snapshot_unverified";
        }else if(pinnedRef)return unknown("wait_route","wrong_native");
        else if(url.pathname!=="/"){postClickStage="wait_route";lastReason="transient_route";}
        await new Promise(resolve=>setTimeout(resolve,500));
      }
      return unknown(postClickStage,lastReason);
    }catch{return unknown(postClickStage,"exception");}
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
