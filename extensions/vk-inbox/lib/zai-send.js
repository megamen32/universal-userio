// Existing Z.AI chat sending through an extension-owned background tab.
// A click is never retried: UNKNOWN admission must be reconciled by native ID.
(function(root){
  const lib={},REF=/^[a-f0-9-]{12,80}$/i,SCOPE=/^[a-f0-9]{64}$/,UUID=/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i;
  const MODELS={"glm-5.3":"GLM-5.3","glm-5.2":"GLM-5.2","glm-5.3-flash":"GLM-5.3-Flash"};
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const stateKey=id=>"userio.zai.send."+id;
  let running=false;
  async function routePage(id){
    if(location.origin!=="https://chat.z.ai"||location.pathname!=="/")
      return {ok:false,error:"zai_route_origin_mismatch"};
    const editor=document.querySelector('textarea[placeholder="Send a Message"]');
    const send=document.querySelector("#send-message-button");
    const visible=e=>!!e&&e.getClientRects().length>0;
    const stop=[...document.querySelectorAll("button")].some(b=>visible(b)
      &&!!b.querySelector(":scope > span.block.size-3.rounded-xs.bg-text-inverted"));
    if(!visible(editor)||!visible(send)||editor.value||stop)
      return {ok:false,error:"zai_route_not_idle"};
    const route="/c/"+id;
    history.pushState({},"",route);
    dispatchEvent(new PopStateEvent("popstate"));
    return {ok:true,path:location.pathname};
  }
  async function checkPage(id,scope){
    if(location.origin!=="https://chat.z.ai"||location.pathname!=="/c/"+id)
      return {ok:false,error:"zai_wrong_chat"};
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
    try{
      const response=await fetch("/api/v1/chats/"+encodeURIComponent(id),{
        credentials:"same-origin",signal:controller.signal});
      if(!response.ok)return {ok:false,error:"zai_chat_unavailable",status:response.status};
      const chat=await response.json();
      if(chat.id!==id||!chat.user_id)return {ok:false,error:"zai_chat_identity_mismatch"};
      const digest=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(String(chat.user_id)));
      const actual=Array.from(new Uint8Array(digest),n=>n.toString(16).padStart(2,"0")).join("");
      return actual===scope?{ok:true}:{ok:false,error:"account_scope_mismatch"};
    }catch{return {ok:false,error:"zai_scope_unavailable"};}
    finally{clearTimeout(timer);}
  }
  async function modelPage(id,scope,model){
    if(location.origin!=="https://chat.z.ai"||location.pathname!=="/c/"+id)
      return {ok:false,error:"zai_wrong_chat"};
    if(!model)return {ok:true};
    const visible=e=>!!e&&e.getClientRects().length>0;
    const editor=document.querySelector('textarea[placeholder="Send a Message"]');
    const send=document.querySelector("#send-message-button");
    const stop=()=>[...document.querySelectorAll("button")].some(b=>visible(b)
      &&!!b.querySelector(":scope > span.block.size-3.rounded-xs.bg-text-inverted"));
    if(!visible(editor)||!visible(send)||editor.value||stop())return {ok:false,error:"zai_busy"};
    const picker=[...document.querySelectorAll('button[aria-label="Select a model"]')].filter(visible);
    if(picker.length!==1)return {ok:false,error:"zai_model_picker_missing"};
    const button=picker[0],wasOpen=button.getAttribute("data-state")==="open";
    if(!wasOpen)button.click();
    try{
      await new Promise(r=>setTimeout(r,200));
      if(location.pathname!=="/c/"+id||stop())return {ok:false,error:"zai_busy"};
      const matches=[...document.querySelectorAll('[role="menu"][data-state="open"] button')]
        .filter(visible).filter(b=>(b.textContent||"").trim()===model);
      if(matches.length!==1)return {ok:false,error:"zai_model_unavailable"};
      if((button.textContent||"").trim()!==model)matches[0].click();
      await new Promise(r=>setTimeout(r,200));
      return (button.textContent||"").trim()===model&&editor.value===""&&!stop()
        ?{ok:true}:{ok:false,error:"zai_model_readback_unconfirmed"};
    }finally{
      if(!wasOpen&&button.getAttribute("data-state")==="open")button.click();
    }
  }
  async function clickPage(id,scope,text,model){
    // Check account, target, Stop, exact draft, model, and Send before one
    // action. The page itself owns the UI, not a CDP/Vibe session.
    if(location.origin!=="https://chat.z.ai"||location.pathname!=="/c/"+id)
      return {ok:false,error:"zai_wrong_chat"};
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
    try{
      const response=await fetch("/api/v1/chats/"+encodeURIComponent(id),{
        credentials:"same-origin",signal:controller.signal});
      if(!response.ok)return {ok:false,error:"zai_chat_unavailable"};
      const chat=await response.json();
      if(chat.id!==id||!chat.user_id)return {ok:false,error:"zai_chat_identity_mismatch"};
      const digest=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(String(chat.user_id)));
      const actual=Array.from(new Uint8Array(digest),n=>n.toString(16).padStart(2,"0")).join("");
      if(actual!==scope)return {ok:false,error:"account_scope_mismatch"};
    }catch{return {ok:false,error:"zai_scope_unavailable"};}
    finally{clearTimeout(timer);}
    const editor=document.querySelector('textarea[placeholder="Send a Message"]');
    const send=document.querySelector("#send-message-button");
    const visible=e=>!!e&&e.getClientRects().length>0;
    const stop=()=>[...document.querySelectorAll("button")].some(b=>visible(b)
      &&!!b.querySelector(":scope > span.block.size-3.rounded-xs.bg-text-inverted"));
    if(!visible(editor)||!visible(send)||editor.value||stop())
      return {ok:false,error:"zai_busy"};
    if(model){
      const picker=[...document.querySelectorAll('button[aria-label="Select a model"]')].filter(visible);
      if(picker.length!==1||(picker[0].textContent||"").trim()!==model)
        return {ok:false,error:"zai_model_changed"};
    }
    const setter=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,"value")?.set;
    if(!setter)return {ok:false,error:"zai_composer_unavailable"};
    setter.call(editor,text);
    editor.dispatchEvent(new Event("input",{bubbles:true}));
    editor.dispatchEvent(new Event("change",{bubbles:true}));
    await new Promise(r=>setTimeout(r,170));
    if(location.origin!=="https://chat.z.ai"||location.pathname!=="/c/"+id
       ||editor.value!==text||stop()||!visible(send)||send.disabled)
      return {ok:false,error:"zai_preclick_changed"};
    send.click();
    return {ok:true,clicked:true};
  }
  async function openExact(ref){
    const tab=await chrome.tabs.create({url:"https://chat.z.ai/",active:false});
    const deadline=Date.now()+25000;
    while(Date.now()<deadline){
      const now=await chrome.tabs.get(tab.id).catch(()=>null);
      if(!now)return {ok:false,error:"zai_tab_lost"};
      if(!now.url?.startsWith("https://chat.z.ai/")&&now.status==="complete")
        return {ok:false,error:"zai_wrong_origin"};
      if(now.status==="complete")return {ok:true,tab:now};
      await sleep(400);
    }
    return {ok:false,error:"zai_tab_timeout"};
  }
  const script=async(tab,fn,args)=>{
    try{const [v]=await chrome.scripting.executeScript({
      target:{tabId:tab.id,allFrames:false},world:"MAIN",func:fn,args});
      return v?.result??{ok:false,error:"zai_script_empty"};
    }catch{return {ok:false,error:"zai_script_unavailable"};}
  };
  const prepare=async(tab,id,scope,model)=>{
    const nav=await script(tab,routePage,[id]);
    if(!nav.ok||nav.path!=="/c/"+id)return {ok:false,error:nav.error||"zai_route_unconfirmed"};
    // The application loads chats asynchronously after popstate.
    const deadline=Date.now()+15000;
    for(;;){
      const verified=await script(tab,checkPage,[id,scope]);
      if(verified.ok)break;
      if(verified.error==="account_scope_mismatch"||Date.now()>deadline)
        return {ok:false,error:verified.error||"zai_chat_not_ready"};
      await sleep(500);
    }
    const selected=await script(tab,modelPage,[id,scope,model]);
    return selected.ok?{ok:true}:{ok:false,error:selected.error||"zai_model_unavailable"};
  };
  lib.send=async(args={})=>{
    const ref=args.chat_ref,scope=args.expected_account_scope,text=args.text,model=MODELS[args.model]||"";
    const rid=args.request_id;
    if(typeof ref!=="string"||!REF.test(ref)||typeof scope!=="string"||!SCOPE.test(scope)
      ||typeof text!=="string"||!text.trim()||text.length>16000
      ||typeof rid!=="string"||!UUID.test(rid) || args.model && !MODELS[args.model])
      return {ok:false,error:"zai_invalid_send_request"};
    if(running)return {ok:false,error:"zai_send_busy",admission:"not_submitted"};
    const id=stateKey(rid);
    if((await chrome.storage.local.get(id))[id])
      return {ok:false,error:"zai_send_duplicate_attempt_refused",admission:"unknown"};
    running=true;
    let clicked=false,tab;
    const save=async phase=>chrome.storage.local.set({[id]:{
      phase,chat_ref:ref,at:Date.now()
    }});
    try{
      await save("preflight");
      const before=await root.UserIOZai.read({chat_ref:ref,expected_account_scope:scope});
      if(!before.ok||!before.ready||!before.latest_assistant?.id){
        await save("rejected");
        return {ok:false,error:"zai_native_chat_not_ready",admission:"not_submitted"};
      }
      const baseline=new Set(before.messages.map(m=>m.id));
      const allocated=await openExact(ref);
      if(!allocated.ok)throw new Error("zai_tab_allocate_unavailable");
      tab=allocated.tab;
      const ready=await prepare(tab,ref,scope,model);
      if(!ready.ok)throw new Error(ready.error);
      // Mark UNKNOWN before calling the click script: the extension may die
      // after the browser receives the click but before it returns a receipt.
      await save("admission_unknown");
      clicked=true;
      const result=await script(tab,clickPage,[ref,scope,text,model]);
      if(!result.ok||!result.clicked)throw new Error("zai_click_unconfirmed");
      const deadline=Date.now()+120000;
      while(Date.now()<deadline){
        const after=await root.UserIOZai.read({chat_ref:ref,expected_account_scope:scope});
        if(after.ok){
          const matches=after.messages.filter(m=>m.role==="user"&&!baseline.has(m.id)
            &&m.text===text);
          if(matches.length===1){
            await save("confirmed");
            await chrome.tabs.remove(tab.id).catch(()=>undefined);
            return {ok:true,admission:"confirmed",chat_ref:ref,
              native_user_message_id:matches[0].id,chat_url:"https://chat.z.ai/c/"+ref};
          }
          if(matches.length>1)break;
        }
        await sleep(1800);
      }
      throw new Error("zai_readback_unconfirmed");
    }catch{
      await save(clicked?"unknown":"rejected");
      if(!clicked&&tab)await chrome.tabs.remove(tab.id).catch(()=>undefined);
      return {ok:false,error:clicked?"zai_send_outcome_unknown":"zai_send_preflight_unavailable",
        admission:clicked?"unknown":"not_submitted"};
    }finally{running=false;}
  };
  root.UserIOZaiSend=lib;
})(typeof self!=="undefined"?self:this);
