// Browser-private fixed-model Z.AI judge. One background tab per inference;
// never touches an existing user conversation or executes operator JS strings.
(function(root){
  const lib={}, ORIGIN="https://chat.z.ai";
  const MODELS={"glm-5.3":"GLM-5.3","glm-5.2":"GLM-5.2","glm-5.3-flash":"GLM-5.3-Flash"};
  const SCOPE=/^[0-9a-f]{64}$/, UUID=/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
  const MAX_BYTES=512*1024;
  let running=false;
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const key=id=>"userio.zai.judge."+id;
  const hash=async text=>Array.from(new Uint8Array(
    await crypto.subtle.digest("SHA-256",new TextEncoder().encode(text))),
    n=>n.toString(16).padStart(2,"0")).join("");
  const sameOrigin=url=>{
    try{return new URL(url).origin===ORIGIN;}catch{return false;}
  };
  async function createdTab(owner){
    const url=ORIGIN+"/?agent-herder-zai-judge="+owner;
    const managed=await root.UserIOTabGroups?.acquire("zai",{slot:"judge",url,active:false,retarget:true,match:t=>String(t.url||"").startsWith(ORIGIN+"/?agent-herder-zai-judge=")});
    let tab=managed?.ok?managed.tab:null;
    if(!tab && managed?.error && managed.error!=="tab_groups_unavailable")return managed;
    if(!tab)tab=await chrome.tabs.create({url,active:false});
    await root.UserIOTabGroups?.assign("zai",tab.id);
    const deadline=Date.now()+65000;
    while(Date.now()<deadline){
      const fresh=await chrome.tabs.get(tab.id).catch(()=>null);
      if(!fresh)return {ok:false,error:"judge_tab_closed"};
      if(!sameOrigin(fresh.url)&&fresh.status==="complete")
        return {ok:false,error:"judge_origin_changed"};
      if(sameOrigin(fresh.url)){
        const loc=new URL(fresh.url);
        if(loc.pathname!=="/")return {ok:false,error:"judge_tab_not_blank",tabId:tab.id};
        if(fresh.status==="complete")return {ok:true,tab:fresh};
        // Chrome can report "loading" long after Z.AI's composer is ready.
        // Check actual DOM readiness without navigating or touching an
        // existing chat, and retain the exact created-tab identity.
        try{
          const [probe]=await chrome.scripting.executeScript({
            target:{tabId:tab.id,allFrames:false},world:"MAIN",
            func:()=>location.origin==="https://chat.z.ai"
              &&location.pathname==="/"
              &&!!document.querySelector('textarea[placeholder="Send a Message"]')
              &&!!document.querySelector("#send-message-button")
          });
          if(probe?.result===true)return {ok:true,tab:fresh};
        }catch{ /* page is not yet ready */ }
      }
      await sleep(400);
    }
    return {ok:false,error:"judge_tab_load_timeout",tabId:tab.id};
  }
  async function discardUnclaimed(tabId,owner){
    const tab=await chrome.tabs.get(tabId).catch(()=>null);
    if(!tab||!sameOrigin(tab.url)||new URL(tab.url).pathname!=="/")return;
    try{
      const [probe]=await chrome.scripting.executeScript({
        target:{tabId,allFrames:false},world:"MAIN",
        func:(name)=>{
          if(location.origin!=="https://chat.z.ai"||location.pathname!=="/"
            ||(window.name&&window.name!=="agent-herder-zai-judge:"+name))
            return false;
          const editor=document.querySelector('textarea[placeholder="Send a Message"]');
          const active=[...document.querySelectorAll("button")].some(button=>
            button.getClientRects().length>0
            &&!!button.querySelector(":scope > span.block.size-3.rounded-xs.bg-text-inverted"));
          return !!editor && editor.value==="" && !active;
        },args:[owner]
      });
      if(probe?.result===true)await chrome.tabs.remove(tabId).catch(()=>undefined);
    }catch{ /* unknown page state: do not close */ }
  }
  const page=async(tabId,step,owner,path,scope,model,extra={})=>{
    const tab=await chrome.tabs.get(tabId).catch(()=>null);
    if(!tab||!sameOrigin(tab.url))return {ok:false,reason:"owned_tab_missing"};
    const params={step,owner,path,scope,model,acknowledgedNewPage:true,...extra};
    try{
      const [result]=await chrome.scripting.executeScript({
        target:{tabId,allFrames:false},world:"MAIN",
        func:root.UserIOZaiJudgePageOperation,args:[params]});
      return result?.result??{ok:false,reason:"script_no_result"};
    }catch{return {ok:false,reason:"script_unavailable"};}
  };
  async function releaseTab(id,owner,path,scope){
    if(!id)return;
    // Never close a user's tab: verify both the original ID and window.name.
    const v=await page(id,"state",owner,path,scope,"").catch(()=>null);
    if(v?.ok===true&&v.phase==="idle"&&!v.draft){
      await chrome.tabs.remove(id).catch(()=>undefined);
    }
  }
  lib.status=async(args={})=>{
    const id=args.request_id;
    if(typeof id!=="string"||!UUID.test(id))return {ok:false,error:"invalid_request_id"};
    const state=(await chrome.storage.local.get(key(id)))[key(id)];
    if(!state)return {ok:false,error:"unknown_request_id"};
    return {ok:true,request_id:id,phase:state.phase||"unknown",model:state.model||"",
      ...(state.chat_ref?{chat_ref:state.chat_ref}:{})};
  };
  lib.infer=async(args={})=>{
    const model=MODELS[args.model],scope=args.expected_account_scope;
    const prompt=args.prompt,requestId=args.request_id;
    if(!model || !SCOPE.test(scope||"")||typeof prompt!=="string"
      ||new TextEncoder().encode(prompt).length<1
      ||new TextEncoder().encode(prompt).length>MAX_BYTES
      ||typeof requestId!=="string"||!UUID.test(requestId))
      return {ok:false,error:"invalid_zai_judge_request"};
    if(running)return {ok:true,kind:"busy"};
    const slotKey=key(requestId);
    const prev=(await chrome.storage.local.get(slotKey))[slotKey];
    if(prev)return {ok:false,error:"zai_judge_duplicate_attempt_refused"};
    // Persist the one-use request BEFORE allocating any UI state; the
    // extension service worker might be terminated during an operation.
    await chrome.storage.local.set({[slotKey]:{phase:"preparing",model:args.model,at:Date.now()}});
    running=true;
    let tabId,owner=requestId,path="/",clicked=false,filled=false,quarantined=false;
    const checkpoint=async(phase,extra={})=>
      chrome.storage.local.set({[slotKey]:{phase,model:args.model,at:Date.now(),...extra}});
    const verify=async()=>{
      const result=await root.UserIOZai.list({expected_account_scope:scope});
      return result.ok;
    };
    try{
      if(!await verify()){
        await checkpoint("rejected");
        return {ok:false,error:"account_scope_unavailable"};
      }
      const allocated=await createdTab(owner);
      if(!allocated.ok){
        await checkpoint("rejected");
        if(Number.isSafeInteger(allocated.tabId))
          await discardUnclaimed(allocated.tabId,owner);
        return {ok:false,error:allocated.error};
      }
      tabId=allocated.tab.id;
      let result=await page(tabId,"claim",owner,path,scope,model);
      if(!result.ok || result.phase!=="idle" || result.draft)
        throw new Error("zai_judge_claim_unavailable");
      result=await page(tabId,"model",owner,path,scope,model);
      if(result.kind==="busy"||result.kind==="model_unavailable"||result.kind==="quota"){
        await checkpoint("rejected");
        await releaseTab(tabId,owner,path,scope);
        return {ok:true,kind:result.kind};
      }
      if(!result.ok)throw new Error("zai_judge_model_unavailable");
      result=await page(tabId,"fill",owner,path,scope,model,{prompt});
      if(result.kind==="busy"){
        await checkpoint("rejected");
        await releaseTab(tabId,owner,path,scope);
        return {ok:true,kind:"busy"};
      }
      if(!result.ok)throw new Error("zai_judge_fill_unavailable");
      filled=true;
      // One final identity check immediately before the only Send click.
      if(!await verify())throw new Error("zai_judge_account_changed");
      await checkpoint("admission_unknown");
      clicked=true;
      result=await page(tabId,"click",owner,path,scope,model,{prompt});
      if(!result.ok)throw new Error("zai_judge_click_outcome_unknown");
      const promptHash=await hash(prompt);
      const deadline=Date.now()+240000;
      while(Date.now()<deadline){
        const tab=await chrome.tabs.get(tabId).catch(()=>null);
        if(!tab||!sameOrigin(tab.url))throw new Error("zai_judge_target_changed");
        const now=new URL(tab.url);
        if(path==="/"&&/^\/c\/[a-f0-9-]{12,80}$/i.test(now.pathname))path=now.pathname;
        if(now.pathname!==path)throw new Error("zai_judge_target_changed");
        if(path!=="/"){
          result=await page(tabId,"read",owner,path,scope,model,{promptHash});
          if(result.ok){
            if(typeof result.text!=="string"||!result.text.trim()
              ||!result.userMessageId||!result.assistantMessageId
              ||result.userMessageId===result.assistantMessageId)
              throw new Error("zai_judge_native_receipt_invalid");
            await checkpoint("confirmed",{chat_ref:path.slice(3)});
            await releaseTab(tabId,owner,path,scope);
            return {ok:true,kind:"answer",text:result.text,
              userMessageId:result.userMessageId,assistantMessageId:result.assistantMessageId};
          }
          if(["busy","model_unavailable","quota"].includes(result.kind)
            &&typeof result.userMessageId==="string"&&result.userMessageId){
            await checkpoint("native_rejected",{chat_ref:path.slice(3)});
            // Keep the native failed chat for reconciliation, but permit the
            // outer GLM model cycle to try a different model.
            return {ok:true,kind:result.kind};
          }
          if(!["response_pending","route_pending","history_pending","history_unavailable"].includes(result.reason||""))
            throw new Error("zai_judge_readback_unknown");
        }
        await sleep(1800);
      }
      throw new Error("zai_judge_readback_timeout");
    }catch(e){
      quarantined=clicked||filled;
      await checkpoint(quarantined?"unknown":"rejected",tabId?{chat_ref:path.startsWith("/c/")?path.slice(3):""}:{});
      if(!quarantined && tabId)await releaseTab(tabId,owner,path,scope);
      return {ok:false,error:quarantined?"zai_judge_outcome_unknown":"zai_judge_preflight_unavailable",
        admission:quarantined?"unknown":"not_submitted"};
    }finally{running=false;}
  };
  root.UserIOZaiJudge=lib;
})(typeof self!=="undefined"?self:this);
