// One-time first message for an extension-owned blank Z.ai tab.
(function(root){
  const lib={}, UUID=/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i, SCOPE=/^[a-f0-9]{64}$/;
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const key=id=>'userio.zai.first.'+id;
  let running=false;
  async function firstClick(scope,text){
    if(location.origin!=='https://chat.z.ai'||location.pathname!=='/')return {ok:false};
    try{
      const token=localStorage.getItem('token');
      if(typeof token!=='string'||!token.trim()||/\s/.test(token))return {ok:false};
      const auth=await fetch('/api/v1/auths/',{credentials:'same-origin',headers:{authorization:'Bearer '+token}});
      if(!auth.ok)return {ok:false};
      const identity=await auth.json();
      if(typeof identity.id!=='string'||!identity.id||typeof identity.role!=='string'||!identity.role||identity.role.toLowerCase()==='guest')return {ok:false};
      const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(identity.id));
      if(Array.from(new Uint8Array(digest),n=>n.toString(16).padStart(2,'0')).join('')!==scope)return {ok:false};
      const editor=document.querySelector('textarea[placeholder="Send a Message"]');
      const send=document.querySelector('#send-message-button');
      const visible=e=>!!e&&e.getClientRects().length>0;
      const busy=()=>[...document.querySelectorAll('button')].some(b=>visible(b)&&b.querySelector(':scope > span.block.size-3.rounded-xs.bg-text-inverted'));
      if(location.pathname!=='/'||!visible(editor)||!visible(send)||editor.value||busy())return {ok:false};
      const setter=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value')?.set;
      if(!setter)return {ok:false};
      setter.call(editor,text);editor.dispatchEvent(new Event('input',{bubbles:true}));
      editor.dispatchEvent(new Event('change',{bubbles:true}));
      await new Promise(r=>setTimeout(r,170));
      if(location.origin!=='https://chat.z.ai'||location.pathname!=='/'||editor.value!==text||!visible(send)||send.disabled||busy())return {ok:false};
      send.click();return {ok:true};
    }catch{return {ok:false};}
  }
  lib.firstSend=async(args={})=>{
    const {request_id:rid,expected_account_scope:scope,text}=args;
    if(typeof rid!=='string'||!UUID.test(rid)||typeof scope!=='string'||!SCOPE.test(scope)
      ||typeof text!=='string'||!text.trim()||text.length>16000)return {ok:false,error:'zai_invalid_first_send'};
    if(running)return {ok:false,error:'zai_first_busy',admission:'not_submitted'};
    running=true;
    const slot=key(rid);
    let tab,started=false;
    const save=async(phase,extra={})=>chrome.storage.local.set({[slot]:{phase,...extra,at:Date.now()}});
    try{
      if((await chrome.storage.local.get(slot))[slot])return {ok:false,error:'zai_first_duplicate',admission:'unknown'};
      await save('preflight');
      const verify=await root.UserIOZai.list({expected_account_scope:scope});
      if(!verify.ok)throw new Error('scope');
      tab=await chrome.tabs.create({url:'https://chat.z.ai/?userio_first='+rid,active:false});
      const deadline=Date.now()+25000;
      while(Date.now()<deadline){
        const now=await chrome.tabs.get(tab.id);
        const url=new URL(now.url);
        if(url.origin!=='https://chat.z.ai'||url.pathname!=='/')throw new Error('target');
        if(now.status==='complete')break;
        await sleep(350);
      }
      await save('admission_unknown',{tab_id:tab.id});started=true;
      const result=await root.UserIOZai._invoke(tab,firstClick,[scope,text]);
      if(!result.ok)throw new Error('click');
      const until=Date.now()+120000;
      while(Date.now()<until){
        const current=await chrome.tabs.get(tab.id);
        const url=new URL(current.url);
        if(url.origin!=='https://chat.z.ai')throw new Error('target');
        const match=url.pathname.match(/^\/c\/([a-f0-9-]{12,80})$/i);
        if(match){
          const read=await root.UserIOZai.raw({chat_ref:match[1],expected_account_scope:scope});
          if(read.ok){
            const messages=read.batches.flatMap(body=>Object.values(JSON.parse(body).data));
            const users=messages.filter(m=>m&&m.role==='user');
            if(users.length===1&&users[0].content===text&&UUID.test(users[0].id||'')){
              const receipt={chat_ref:match[1],native_user_message_id:users[0].id};
              await save('confirmed',receipt);
              return {ok:true,admission:'confirmed',...receipt};
            }
          }
        }
        await sleep(1800);
      }
      throw new Error('timeout');
    }catch{
      await save(started?'unknown':'rejected',tab?{tab_id:tab.id}:{});
      if(!started&&tab)await chrome.tabs.remove(tab.id).catch(()=>undefined);
      return {ok:false,error:started?'zai_first_outcome_unknown':'zai_first_preflight_failed',admission:started?'unknown':'not_submitted'};
    }finally{running=false;}
  };
  root.UserIOZaiLifecycle=lib;
})(typeof self!=='undefined'?self:this);
