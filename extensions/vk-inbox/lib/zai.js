// Scoped Z.AI commands for the existing multi-profile UserIO MV3 agent.
// The site session is used in its own origin; no cookies, credentials or
// arbitrary JS expressions are returned through the command channel.
(function(root){
  const lib={};
  const ORIGIN="https://chat.z.ai";
  const REF=/^[a-f0-9-]{12,80}$/i, SCOPE=/^[a-f0-9]{64}$/;
  const stop=(e)=>!!e && e.name==="AbortError";
  const errorOf=(e,fallback)=>stop(e)?{ok:false,error:"zai_timeout"}:{ok:false,error:fallback};
  const time=async(f,ms=22000)=>{
    const controller=new AbortController();
    const timer=setTimeout(()=>controller.abort(),ms);
    try{return await f(controller.signal);}finally{clearTimeout(timer);}
  };
  const fail=(response,action)=>({
    ok:false,error:response.status===429?"zai_http_429":"zai_"+action+"_http_"+response.status,
    status:response.status,
    ...(response.status===429?{retry_after:Math.min(86400,Math.max(0,Number(response.headers.get("Retry-After"))||0))}:{})
  });
  lib.validRef=v=>typeof v==="string"&&REF.test(v)&&v.length<=80;
  lib.validScope=v=>typeof v==="string"&&SCOPE.test(v);
  const parse=(v)=>{
    try{const u=new URL(v);return u.origin===ORIGIN?u:null;}catch{return null;}
  };
  lib.findTab=async({create=true}={})=>{
    const candidates=(await chrome.tabs.query({url:"https://chat.z.ai/*"}))
      .filter(t=>parse(t.url));
    let tab=candidates.find(t=>t.status==="complete")||candidates[0];
    if(tab) await root.UserIOTabGroups?.assign("zai",tab.id);
    if(!tab && create){
      const allocated=await root.UserIOTabGroups?.acquire("zai",{slot:"background",url:ORIGIN+"/?userio_slot=background",active:false,match:t=>String(t.url||"").startsWith(ORIGIN+"/?userio_slot=background")});
      if(allocated?.ok)tab=allocated.tab;
      else if(allocated?.error && allocated.error!=="tab_groups_unavailable")return allocated;
      if(!tab)tab=await chrome.tabs.create({url:ORIGIN+"/",active:false});
    }
    if(!tab)return {ok:false,error:"zai_tab_missing"};
    await root.UserIOTabGroups?.assign("zai",tab.id);
    const deadline=Date.now()+25000;
    while(Date.now()<deadline){
      const current=await chrome.tabs.get(tab.id).catch(()=>null);
      if(!current)return {ok:false,error:"zai_tab_closed"};
      if(!parse(current.url) && current.status==="complete")
        return {ok:false,error:"zai_origin_changed"};
      if(current.status==="complete")return {ok:true,tab:current};
      await new Promise(r=>setTimeout(r,350));
    }
    return {ok:false,error:"zai_tab_timeout"};
  };
  const invoke=async(tab,func,args=[])=>{
    if(!parse(tab.url))return {ok:false,error:"zai_origin_changed"};
    try{
      const [out]=await chrome.scripting.executeScript({
        target:{tabId:tab.id,allFrames:false},world:"MAIN",func,args
      });
      return out?.result??{ok:false,error:"zai_script_no_result"};
    }catch{return {ok:false,error:"zai_script_unavailable"};}
  };
  // In the page's MAIN world, browser native fetch keeps the site's cookies.
  // No API bearer or cookie value is ever passed back to the extension worker.
  async function listPage(expectedScope){
    if(location.origin!=="https://chat.z.ai")return {ok:false,error:"zai_origin_changed"};
    const token=localStorage.getItem("token");
    if(location.pathname.startsWith("/auth")||typeof token!=="string"||!token.trim()||/\s/.test(token))return {ok:false,error:"authentication_required"};
    const headers={authorization:"Bearer "+token};
    const expected=/^[a-f0-9]{64}$/;
    if(expectedScope && !expected.test(expectedScope))return {ok:false,error:"invalid_account_scope"};
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),18000);
    try{
      // Native auth identity is available even in a brand-new account with
      // zero chat history. Never ship the raw user id out of the page.
      const authResponse=await fetch("/api/v1/auths/",{
        credentials:"same-origin",headers,signal:controller.signal});
      if(!authResponse.ok)return {ok:false,error:[401,403].includes(authResponse.status)?"authentication_required":"zai_auth_unavailable",status:authResponse.status};
      const auth=await authResponse.json();
      if(typeof auth?.role!=="string"||!auth.role||auth.role.toLowerCase()==="guest")return {ok:false,error:"authentication_required"};
      if(typeof auth?.id!=="string"||!auth.id)return {ok:false,error:"zai_auth_identity_invalid"};
      const digest=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(auth.id));
      const scope=Array.from(new Uint8Array(digest),x=>x.toString(16).padStart(2,"0")).join("");
      if(expectedScope && expectedScope!==scope)return {ok:false,error:"account_scope_mismatch"};
      const seen=new Set(),chats=[];
      for(let page=1;page<=2;page++){
        const response=await fetch("/api/v1/chats/?page="+page+"&type=all&channel=zai-web&project_id=",{
          credentials:"same-origin",headers,signal:controller.signal
        });
        if(!response.ok)return {ok:false,error:response.status===429?"zai_http_429":"zai_list_unavailable",
          status:response.status,retry_after:Math.min(86400,Math.max(0,Number(response.headers.get("Retry-After"))||0))};
        const data=await response.json();
        if(!Array.isArray(data))return {ok:false,error:"zai_list_invalid"};
        for(const item of data){
          if(!item||typeof item.id!=="string"||!/^[a-f0-9-]{12,80}$/i.test(item.id)||seen.has(item.id))continue;
          seen.add(item.id);
          chats.push({id:item.id,title:String(item.title||"Z.AI conversation").slice(0,160),
            updated_at:Number(item.updated_at)||0,
            last_run_status:typeof item.last_run?.status==="string"?item.last_run.status:"",
            unread:item.has_unread_msg===true});
        }
        if(data.length<60)break;
      }
      chats.sort((a,b)=>b.updated_at-a.updated_at);
      if(chats.length){
        const response=await fetch("/api/v1/chats/"+encodeURIComponent(chats[0].id),{
          credentials:"same-origin",headers,signal:controller.signal});
        if(!response.ok)return {ok:false,error:"zai_scope_unavailable",status:response.status};
        const detail=await response.json();
        if(detail.id!==chats[0].id||String(detail.user_id||"")!==auth.id)
          return {ok:false,error:"zai_scope_identity_mismatch"};
      }
      return {ok:true,account_scope:scope,chats:chats.slice(0,100),_native_user_id:auth.id};
    }catch(e){return {ok:false,error:e?.name==="AbortError"?"zai_timeout":"zai_list_unavailable"};}
    finally{clearTimeout(timer);}
  }
  lib.list=async(args={})=>{
    const expectedScope=args.expected_account_scope||"";
    if(expectedScope&&!lib.validScope(expectedScope))return {ok:false,error:"invalid_account_scope"};
    const tab=await lib.findTab();if(!tab.ok)return tab;
    const result=await invoke(tab.tab,listPage,[expectedScope]);
    if(result.ok){
      const userId=result._native_user_id;
      delete result._native_user_id;
      if(typeof userId!=="string"||!userId||!lib.validScope(result.account_scope))return {ok:false,error:"zai_auth_identity_invalid"};
      await root.UserIOConnections?.observeAccount({provider:"zai",userId,
        accountScope:result.account_scope,label:"Z.ai",connectionIds:[]});
    }
    return result;
  };
  lib.scope=async()=>{
    const r=await lib.list();
    return r.ok?{ok:true,account_scope:r.account_scope}:{ok:false,error:r.error,status:r.status};
  };
  async function readPage(id,scope){
    if(location.origin!=="https://chat.z.ai")return {ok:false,error:"zai_origin_changed"};
    const token=localStorage.getItem("token");
    if(location.pathname.startsWith("/auth")||typeof token!=="string"||!token.trim()||/\s/.test(token))return {ok:false,error:"authentication_required"};
    const headers={authorization:"Bearer "+token};
    if(!/^[a-f0-9-]{12,80}$/i.test(id)||!(/^[a-f0-9]{64}$/).test(scope))
      return {ok:false,error:"zai_invalid_reference"};
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),22000);
    try{
      const response=await fetch("/api/v1/chats/"+encodeURIComponent(id),{
        credentials:"same-origin",headers,signal:controller.signal});
      if(!response.ok)return {ok:false,error:response.status===429?"zai_http_429":"zai_chat_unavailable",
        status:response.status,retry_after:Math.min(86400,Math.max(0,Number(response.headers.get("Retry-After"))||0))};
      const chat=await response.json();
      if(chat.id!==id||!chat.user_id)return {ok:false,error:"zai_chat_identity_mismatch"};
      const digest=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(String(chat.user_id)));
      const actual=Array.from(new Uint8Array(digest),x=>x.toString(16).padStart(2,"0")).join("");
      if(actual!==scope)return {ok:false,error:"account_scope_mismatch"};
      const mapping=chat.chat?.history?.messages;
      if(!mapping||typeof mapping!=="object"||Array.isArray(mapping))
        return {ok:false,error:"zai_history_invalid"};
      const ids=Object.keys(mapping).slice(-180);
      if(ids.length===0)return {ok:true,chat_ref:id,messages:[],latest_user:null,latest_assistant:null,ready:false,phase:"unknown"};
      const messages=[];
      for(let i=0;i<ids.length;i+=60){
        const chunk=ids.slice(i,i+60);
        const r=await fetch("/api/v1/chats/"+encodeURIComponent(id)+"/messages/batch",{
          method:"POST",credentials:"same-origin",headers:{...headers,"content-type":"application/json"},
          body:JSON.stringify({ids:chunk}),signal:controller.signal});
        if(!r.ok)return {ok:false,error:r.status===429?"zai_http_429":"zai_batch_unavailable",
          status:r.status,retry_after:Math.min(86400,Math.max(0,Number(r.headers.get("Retry-After"))||0))};
        const batch=await r.json();
        if(batch.chat_id!==id||!batch.data||typeof batch.data!=="object")
          return {ok:false,error:"zai_batch_identity_mismatch"};
        for(const key of chunk){
          const m=batch.data[key];
          if(!m || m.id!==key)return {ok:false,error:"zai_batch_message_mismatch"};
          if(m.role!=="user"&&m.role!=="assistant")continue;
          const blocks=Array.isArray(m.content_blocks)?m.content_blocks:[];
          const text=(typeof m.content==="string"?m.content:blocks
            .filter(v=>v&&(v.type==="text"||v.type==="markdown"))
            .map(v=>typeof v.content==="string"?v.content:"").join("\n"))
            .trim().slice(0,16000);
          if(!text)continue;
          const timestamp=Number(m.created_at||m.timestamp)||0;
          messages.push({id:m.id,role:m.role,text,
            timestamp:timestamp>0?timestamp:0,
            model:typeof m.model==="string"?m.model:"",
            done:m.done===true});
        }
      }
      messages.sort((a,b)=>a.timestamp-b.timestamp);
      const tail=messages.slice(-80);
      let latest_user=null,latest_assistant=null;
      for(const m of tail){
        if(m.role==="user"){latest_user=m;latest_assistant=null;}
        else if(latest_user)latest_assistant=m;
      }
      const run=chat.last_run;
      const runStatus=typeof run?.status==="string"?run.status:"";
      const ready=!!latest_assistant && (latest_assistant.done===true
        || ["completed","done","success","succeeded"].includes(runStatus));
      return {ok:true,chat_ref:id,messages:tail,latest_user,latest_assistant,ready,
        phase:ready?"idle":runStatus==="running"?"generating":"unknown",
        last_run_status:runStatus};
    }catch(e){return {ok:false,error:e?.name==="AbortError"?"zai_timeout":"zai_history_unavailable"};}
    finally{clearTimeout(timer);}
  }
  lib.read=async(args={})=>{
    const ref=args.chat_ref,scope=args.expected_account_scope;
    if(!lib.validRef(ref)||!lib.validScope(scope))return {ok:false,error:"zai_invalid_reference"};
    const tab=await lib.findTab();if(!tab.ok)return tab;
    const result=await invoke(tab.tab,readPage,[ref,scope]);
    if(result.ok&&result.chat_ref!==ref)return {ok:false,error:"zai_read_identity_mismatch"};
    return result;
  };
  // Retain native response bodies, rather than labelling normalized display
  // messages as a full transcript. A bounded result is intentionally partial.
  async function rawPage(id,scope){
    if(location.origin!=="https://chat.z.ai")return {ok:false,error:"zai_origin_changed"};
    const token=localStorage.getItem("token");
    if(location.pathname.startsWith("/auth")||typeof token!=="string"||!token.trim()||/\s/.test(token))return {ok:false,error:"authentication_required"};
    const headers={authorization:"Bearer "+token};
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),22000);
    try{
      const url="/api/v1/chats/"+encodeURIComponent(id);
      const r=await fetch(url,{credentials:"same-origin",headers,signal:controller.signal});
      if(!r.ok)return {ok:false,error:"zai_chat_unavailable",status:r.status};
      const detail=await r.text();
      if(new TextEncoder().encode(detail).length>200000)return {ok:false,error:"zai_raw_too_large"};
      const chat=JSON.parse(detail);
      if(chat.id!==id||!chat.user_id)return {ok:false,error:"zai_chat_identity_mismatch"};
      const digest=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(String(chat.user_id)));
      const actual=Array.from(new Uint8Array(digest),x=>x.toString(16).padStart(2,"0")).join("");
      if(actual!==scope)return {ok:false,error:"account_scope_mismatch"};
      const mapping=chat.chat?.history?.messages;
      if(!mapping||typeof mapping!=="object"||Array.isArray(mapping))return {ok:false,error:"zai_history_invalid"};
      const ids=Object.keys(mapping), batches=[];
      let size=new TextEncoder().encode(detail).length;
      for(let start=0;start<ids.length;start+=60){
        const requested=ids.slice(start,start+60);
        const b=await fetch(url+"/messages/batch",{method:"POST",credentials:"same-origin",
          headers:{...headers,"content-type":"application/json"},body:JSON.stringify({ids:requested}),signal:controller.signal});
        if(!b.ok)return {ok:false,error:"zai_batch_unavailable",status:b.status};
        const body=await b.text();size+=new TextEncoder().encode(body).length;
        if(size>200000)return {ok:false,error:"zai_raw_too_large"};
        const parsed=JSON.parse(body);
        if(parsed.chat_id!==id||!parsed.data||requested.some(key=>parsed.data[key]?.id!==key))
          return {ok:false,error:"zai_batch_identity_mismatch"};
        batches.push(body);
      }
      return {ok:true,chat_ref:id,detail,batches,complete:false,
        limitations:["Native response bodies are preserved; a consistent snapshot and attachment bytes are not established."]};
    }catch(e){return {ok:false,error:e?.name==="AbortError"?"zai_timeout":"zai_raw_unavailable"};}
    finally{clearTimeout(timer);}
  }
  lib.raw=async(args={})=>{
    if(!lib.validRef(args.chat_ref)||!lib.validScope(args.expected_account_scope))return {ok:false,error:"zai_invalid_reference"};
    const tab=await lib.findTab();if(!tab.ok)return tab;
    return invoke(tab.tab,rawPage,[args.chat_ref,args.expected_account_scope]);
  };
  async function archivePage(id,scope){
    if(location.origin!=="https://chat.z.ai")return {ok:false,error:"zai_origin_changed"};
    const token=localStorage.getItem("token");
    if(location.pathname.startsWith("/auth")||typeof token!=="string"||!token.trim()||/\s/.test(token))return {ok:false,error:"authentication_required"};
    const headers={authorization:"Bearer "+token};
    const ac=new AbortController(),timer=setTimeout(()=>ac.abort(),20000);let mutation=false;
    try{
      const url="/api/v1/chats/"+encodeURIComponent(id);
      const read=async()=>{const r=await fetch(url,{credentials:"same-origin",headers,signal:ac.signal});
        if(!r.ok)throw new Error("read_failed");return r.json();};
      const before=await read();
      if(before.id!==id||!before.user_id)return {ok:false,error:"identity_mismatch"};
      const digest=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(String(before.user_id)));
      if(Array.from(new Uint8Array(digest),x=>x.toString(16).padStart(2,"0")).join("")!==scope)return {ok:false,error:"account_scope_mismatch"};
      if(before.archived===true)return {ok:true,chat_ref:id,archived:true,already:true};
      if(before.archived!==false)return {ok:false,error:"archive_state_unknown"};
      if(["running","pending","in_progress"].includes(before.last_run?.status))return {ok:false,error:"conversation_busy"};
      mutation=true;
      const response=await fetch(url+"/archive",{method:"POST",credentials:"same-origin",headers,signal:ac.signal});
      if(!response.ok)return {ok:false,error:"archive_outcome_unknown"};
      const after=await read();
      return after.id===id&&after.user_id===before.user_id&&after.archived===true?{ok:true,chat_ref:id,archived:true}:{ok:false,error:"archive_outcome_unknown"};
    }catch{return {ok:false,error:mutation?"archive_outcome_unknown":"archive_preflight_failed"};}
    finally{clearTimeout(timer);}
  }
  lib.archive=async(args={})=>{
    if(!lib.validRef(args.chat_ref)||!lib.validScope(args.expected_account_scope))return {ok:false,error:"zai_invalid_reference"};
    const tab=await lib.findTab();if(!tab.ok)return tab;
    return invoke(tab.tab,archivePage,[args.chat_ref,args.expected_account_scope]);
  };
  lib._invoke=invoke;
  root.UserIOZai=lib;
})(typeof self!=="undefined"?self:this);
