(function(root){
async function pageAction(url, room, text, after, replyTo=null, inputEntities=[]) {
    if (location.href !== url) return {ok:false,error:'wrong_dot_route'};
    const allRows = [...document.querySelectorAll('article.message-row[data-message-id]')];
    const rows = allRows.filter(e=>!e.closest?.('.reply-chain-panel[role="dialog"]'));
    const prefix = room + '~' + room + '~CalpicoMessage~';
    if (!rows.length || rows.some(e => !e.dataset.messageId.startsWith(prefix))) return {ok:false,error:'wrong_or_unloaded_room'};
    const messages=[];
    for(const e of rows){
      const content=e.querySelector('.message-text');
      const text=content?.innerText||content?.textContent||'';
      const entities=[];
      // DOM offsets are UTF-16, as are Telegram message entity offsets. Only
      // publish ranges when the walked text is exactly the text we transmit.
      let walked='';
      const walk=node=>{
        if(node?.nodeType===3){walked+=node.nodeValue||'';return;}
        const start=walked.length;
        for(const child of node?.childNodes||[])walk(child);
        const length=walked.length-start;
        if(!length)return;
        const tag=String(node?.tagName||'').toUpperCase();
        const type={STRONG:'bold',B:'bold',EM:'italic',I:'italic',S:'strike',DEL:'strike',U:'underline',CODE:'code',PRE:'pre',A:'text_url'}[tag];
        if(!type)return;
        if(type==='text_url'){
          try{const target=new URL(node.getAttribute('href'),location.href);if(!['http:','https:'].includes(target.protocol))return;entities.push({type,offset:start,length,url:target.href});}catch{}
        }else entities.push({type,offset:start,length});
      };
      if(content?.childNodes)walk(content);
      let mapped=entities,formatIncomplete=false;
      if(entities.length&&walked!==text){
        // innerText may insert paragraph breaks absent from textContent. Map
        // only when every non-whitespace UTF-16 code unit agrees in order.
        const positions=new Map();let i=0,j=0;
        while(i<walked.length||j<text.length){
          while(i<walked.length&&/\s/.test(walked[i]))i++;
          while(j<text.length&&/\s/.test(text[j]))j++;
          if(i===walked.length||j===text.length)break;
          if(walked[i]!==text[j]){formatIncomplete=true;break;}
          positions.set(i++,j++);
        }
        if(walked.slice(i).trim()||text.slice(j).trim())formatIncomplete=true;
        if(!formatIncomplete)mapped=entities.map(entity=>{
          let first=entity.offset,last=entity.offset+entity.length-1;
          while(first<=last&&!positions.has(first))first++;
          while(last>=first&&!positions.has(last))last--;
          if(first>last){formatIncomplete=true;return entity;}
          return {...entity,offset:positions.get(first),length:positions.get(last)-positions.get(first)+1};
        });
      }
      const images=[...(e.querySelectorAll?.('img.attachment-image')||[])];
      if(images.length>10)return {ok:false,error:'too_many_visible_attachments'};
      const attachment_fingerprints=[];
      for(const img of images){
        const src=img.currentSrc||img.src||img.getAttribute?.('src')||'';
        if(typeof src!=='string'||!src)return {ok:false,error:'media_source_unavailable'};
        // Only a digest of the DOM source is returned; signed URLs never
        // leave the authenticated page or enter the durable message journal.
        let digest;
        try{
          digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(new URL(src,location.href).href));
        }catch{return {ok:false,error:'media_fingerprint_requires_review'};}
        attachment_fingerprints.push(Array.from(new Uint8Array(digest)).map(x=>x.toString(16).padStart(2,'0')).join(''));
      }
      messages.push({id:e.dataset.messageId,role:e.classList.contains('self')?'user':'assistant',
        text,entities:formatIncomplete?[]:mapped,format_incomplete:formatIncomplete,
        attachment_names:images.map(i=>i.alt).filter(Boolean),
        attachment_count:images.length,attachment_fingerprints,
        reply_to:e.querySelector('[data-action="jump-reply"][data-message-id]')?.dataset?.messageId||null});
    }
    let editors = [...document.querySelectorAll('[contenteditable="true"][data-composer-markdown]')].filter(e => e.getClientRects().length);
    if (text === null) {
      const index = after ? messages.findIndex(m=>m.id===after) : -1;
      if(after && index<0) return {ok:false,error:'cursor_not_visible',resync_required:true};
      return {ok:true,room,messages:messages.slice(index+1),cursor:messages.at(-1)?.id};
    }
    if(editors.length!==1) return {ok:false,error:'ambiguous_composer'};
    if(editors[0].innerText.trim()) return {ok:false,error:'existing_draft'};
    const originalComposer=editors[0].closest?.('[data-role=composer]');
    if(originalComposer?.querySelectorAll('img')?.length||originalComposer?.querySelector('input[type=file]')?.files?.length)return {ok:false,error:'existing_attachments'};
    if(document.querySelector?.('.reply-chain-panel[role="dialog"]')) return {ok:false,error:'existing_reply_panel'};
    let scope=document,ownedPanel=null;
    if(replyTo){
      if(typeof replyTo!=='string'||!replyTo.startsWith(prefix))return {ok:false,error:'invalid_reply_target'};
      const targets=rows.filter(e=>e.dataset.messageId===replyTo);
      const replyButton=targets.length===1?targets[0].querySelector('[data-action="reply"]'):null;
      if(!replyButton)return {ok:false,error:'reply_target_not_visible'};
      replyButton.click();
      await new Promise(resolve=>setTimeout(resolve,100));
      const panels=[...document.querySelectorAll('.reply-chain-panel[role="dialog"]')].filter(e=>e.getClientRects().length);
      if(panels.length!==1)return {ok:false,error:'reply_panel_unverified'};
      ownedPanel=panels[0];
      const targetRows=[...ownedPanel.querySelectorAll('article.message-row[data-message-id]')];
      if(targetRows.length!==1||targetRows[0].dataset.messageId!==replyTo)return {ok:false,error:'reply_chain_target_ambiguous'};
      scope=ownedPanel;
      editors=[...scope.querySelectorAll('[contenteditable="true"][data-composer-markdown]')].filter(e=>e.getClientRects().length);
      if(editors.length!==1||editors[0].innerText.trim())return {ok:false,error:'reply_composer_unverified'};
    }
    const editor=editors[0];
    const buttons=[...scope.querySelectorAll('button')].filter(e=>e.getClientRects().length && ['Отправить','Send'].includes(e.getAttribute('aria-label')));
    if(buttons.length!==1) return {ok:false,error:'ambiguous_send'};
    if(!Array.isArray(inputEntities)||inputEntities.length>200)return {ok:false,error:'invalid_dot_format'};
    const tags={bold:'strong',italic:'em',strike:'s',underline:'u',code:'code',pre:'code',text_url:'a'};
    const entities=[];
    for(const e of inputEntities){
      if(!e||!Object.hasOwn(tags,e.type)||!Number.isSafeInteger(e.offset)||!Number.isSafeInteger(e.length)||e.offset<0||e.length<1||e.offset+e.length>text.length)return {ok:false,error:'invalid_dot_format'};
      if((e.offset>0&&/[\uD800-\uDBFF]/.test(text[e.offset-1])&&/[\uDC00-\uDFFF]/.test(text[e.offset]))||(e.offset+e.length<text.length&&/[\uD800-\uDBFF]/.test(text[e.offset+e.length-1])&&/[\uDC00-\uDFFF]/.test(text[e.offset+e.length])))return {ok:false,error:'invalid_dot_format'};
      if(e.type==='text_url'){let target;try{target=new URL(e.url)}catch{return {ok:false,error:'invalid_dot_format'}};if(!['http:','https:'].includes(target.protocol))return {ok:false,error:'invalid_dot_format'};entities.push({...e,url:target.href});}
      else entities.push(e);
    }
    const escape=value=>String(value).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
    let insertion=text,command='insertText';
    if(entities.length){
      const points=[...new Set([0,text.length,...entities.flatMap(e=>[e.offset,e.offset+e.length])])].sort((a,b)=>a-b);
      insertion='';command='insertHTML';
      for(let i=0;i<points.length-1;i++){
        const start=points[i],end=points[i+1],active=entities.filter(e=>e.offset<=start&&e.offset+e.length>=end);
        if(active.filter(e=>e.type==='text_url').length>1)return {ok:false,error:'invalid_dot_format'};
        let chunk=escape(text.slice(start,end)).replace(/\n/g,'<br>');
        for(const e of active){const tag=tags[e.type];chunk='<'+tag+(e.type==='text_url'?' href="'+escape(e.url)+'"':'')+'>'+chunk+'</'+tag+'>';}
        insertion+=chunk;
      }
    }
    editor.focus();
    if(!document.execCommand(command,false,insertion)) return {ok:false,error:'insert_failed'};
    // Only wait for the UI to enable this same, unchanged draft. Never insert
    // again, clear a draft, or retry a click. A slow render is not a send failure.
    let ready=[];
    for(let attempt=0;attempt<50;attempt++){
      await new Promise(resolve=>setTimeout(resolve,100));
      if(location.href!==url)return {ok:false,error:'navigation_requires_review'};
      const currentEditors=[...scope.querySelectorAll('[contenteditable="true"][data-composer-markdown]')].filter(e=>e.getClientRects().length);
      if(editor.isConnected===false || currentEditors.length!==1 || currentEditors[0]!==editor)return {ok:false,error:'draft_requires_review',draft_issue:'composer_changed'};
      if(editor.innerText.trim()!==text.trim())return {ok:false,error:'draft_requires_review',draft_issue:'text_changed'};
      if(entities.some(e=>e.type==='text_url'&&![...(editor.querySelectorAll?.('a')||[])].some(a=>{try{return new URL(a.getAttribute('href'),location.href).href===e.url}catch{return false}})))return {ok:false,error:'draft_requires_review',draft_issue:'link_missing'};
      ready=[...scope.querySelectorAll('button')].filter(e=>e.getClientRects().length && ['Отправить','Send'].includes(e.getAttribute('aria-label')));
      if(ready.length===1 && !ready[0].disabled)break;
      ready=[];
    }
    if(ready.length!==1)return {ok:false,error:'draft_requires_review',draft_issue:'send_not_ready'};
    if(location.href!==url) return {ok:false,error:'navigation_requires_review'};
    if([...document.querySelectorAll('article.message-row[data-message-id]')].some(e=>!e.dataset.messageId.startsWith(prefix))) return {ok:false,error:'room_changed_requires_review'};
    if(ownedPanel){
      const targetRows=[...ownedPanel.querySelectorAll('article.message-row[data-message-id]')];
      if(targetRows.length!==1||targetRows[0].dataset.messageId!==replyTo)return {ok:false,error:'reply_target_changed'};
    }
    ready[0].click();
    await new Promise(resolve=>setTimeout(resolve,100));
    if(ownedPanel&&!editor.innerText.trim()){
      const close=[...ownedPanel.querySelectorAll('button')].find(b=>['Закрыть ветку ответов','Close reply thread','Close replies'].includes(b.getAttribute('aria-label')));
      close?.click();
    }
    return {ok:true,status:'submitted_unconfirmed',cursor:messages.at(-1)?.id,verified_reply_to:ownedPanel?replyTo:null};
}

async function mediaAction(url,room,operation,args={}){
  const fail=error=>({ok:false,error});
  if(location.href!==url)return fail('wrong_dot_route');
  const rows=[...document.querySelectorAll('article.message-row[data-message-id]')].filter(e=>!e.closest?.('.reply-chain-panel[role="dialog"]'));
  const prefix=room+'~'+room+'~CalpicoMessage~';
  if(!rows.length||rows.some(e=>!e.dataset.messageId.startsWith(prefix)))return fail('wrong_or_unloaded_room');
  if(!/^[0-9a-f-]{36}$/.test(args.transfer_id||''))return fail('invalid_media_transfer');
  const key='__userioDotUpload',existing=globalThis[key];
  if(operation==='start'){
    if(existing&&existing.expires>Date.now())return fail('media_transfer_busy');
    const files=args.files;
    if(!Array.isArray(files)||files.length<1||files.length>10)return fail('invalid_media_files');
    let total=0;
    for(const f of files){
      if(!f||typeof f.filename!=='string'||!f.filename||f.filename.length>255||/[\/\\\u0000-\u001f]/.test(f.filename)||typeof f.mime!=='string'||f.mime.length>150||!Number.isSafeInteger(f.size)||f.size<1||f.size>8*1024*1024||!/^[a-f0-9]{64}$/.test(f.sha256||''))return fail('invalid_media_files');
      total+=f.size;if(total>20*1024*1024)return fail('invalid_media_files');
    }
    globalThis[key]={id:args.transfer_id,room,expires:Date.now()+600000,files:files.map(f=>({...f,bytes:new Uint8Array(f.size),received:0}))};
    return {ok:true,status:'started'};
  }
  if(!existing||existing.id!==args.transfer_id||existing.room!==room||existing.expires<=Date.now())return fail('media_transfer_unknown');
  if(operation==='finish'){for(const f of existing.files)f.bytes.fill(0);delete globalThis[key];return {ok:true,status:'cleared'};}
  if(operation==='chunk'){
    if(!Number.isSafeInteger(args.index)||args.index<0||args.index>=existing.files.length||!Number.isSafeInteger(args.offset)||typeof args.data!=='string'||args.data.length>180000)return fail('invalid_media_chunk');
    const f=existing.files[args.index];if(args.offset!==f.received)return fail('invalid_media_offset');
    let binary;try{binary=atob(args.data)}catch{return fail('invalid_media_chunk')};
    if(!binary.length||binary.length>128*1024||f.received+binary.length>f.size)return fail('invalid_media_chunk');
    for(let i=0;i<binary.length;i++)f.bytes[f.received+i]=binary.charCodeAt(i);
    f.received+=binary.length;return {ok:true,index:args.index,received:f.received};
  }
  if(operation==='upload'){
    if(existing.uploaded)return fail('media_upload_already_started');
    if(args.reply_to)return fail('media_reply_requires_review');
    if(document.querySelector?.('.reply-chain-panel[role="dialog"]'))return fail('existing_reply_panel');
    for(const f of existing.files){
      if(f.received!==f.size)return fail('media_incomplete');
      const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',f.bytes))).map(x=>x.toString(16).padStart(2,'0')).join('');
      if(digest!==f.sha256)return fail('media_digest_mismatch');
    }
    const editors=[...document.querySelectorAll('[contenteditable="true"][data-composer-markdown]')].filter(e=>e.getClientRects().length);
    if(editors.length!==1||editors[0].innerText.trim())return fail('existing_or_ambiguous_draft');
    const composer=editors[0].closest?.('[data-role=composer]');
    if(!composer||composer.querySelectorAll('img').length)return fail('existing_attachments');
    const inputs=[...composer.querySelectorAll('input[type="file"]')];
    if(inputs.length!==1||inputs[0].files?.length)return fail('file_input_unavailable');
    const transfer=new DataTransfer();
    for(const f of existing.files)transfer.items.add(new File([f.bytes],f.filename,{type:f.mime}));
    inputs[0].files=transfer.files;existing.uploaded=true;
    inputs[0].dispatchEvent(new Event('change',{bubbles:true}));
    return {ok:true,status:'upload_started'};
  }
  if(operation==='inspect'||operation==='send'){
    if(existing.submitted)return fail('media_send_already_started');
    if(!existing.uploaded)return fail('media_upload_not_started');
    if(document.querySelector?.('.reply-chain-panel[role="dialog"]'))return fail('existing_reply_panel');
    const editors=[...document.querySelectorAll('[contenteditable="true"][data-composer-markdown]')].filter(e=>e.getClientRects().length);
    if(editors.length!==1)return fail('ambiguous_composer');
    const editor=editors[0],composer=editor.closest?.('[data-role=composer]');
    if(!composer)return fail('composer_scope_missing');
    const images=[...composer.querySelectorAll('img')].filter(e=>e.getClientRects().length);
    const buttons=[...document.querySelectorAll('button')].filter(e=>e.getClientRects().length&&['Отправить','Send'].includes(e.getAttribute('aria-label')));
    const seen=existing.files.every(f=>String(composer.outerHTML||'').includes(f.filename));
    const busy=!!composer.querySelector('[role=progressbar],[aria-busy=true]')||/Uploading|Загрузка|Загружается/i.test(composer.innerText||'');
    const ready=images.length===existing.files.length&&seen&&!busy&&buttons.length===1&&!buttons[0].disabled;
    if(operation==='inspect')return {ok:true,ready};
    if(!ready)return fail('upload_not_ready');
    if(editor.innerText.trim())return fail('existing_draft');
    if(args.entities?.length)return fail('media_format_requires_review');
    const text=typeof args.text==='string'?args.text:'';
    if(text){editor.focus();if(!document.execCommand('insertText',false,text))return fail('insert_failed');await new Promise(resolve=>setTimeout(resolve,100));if(editor.innerText.trim()!==text.trim())return fail('caption_unverified');}
    if(location.href!==url||buttons[0].disabled)return fail('send_not_ready');
    existing.submitted=true;buttons[0].click();
    return {ok:true,status:'submitted_unconfirmed'};
  }
  return fail('invalid_media_operation');
}
root.UserIODot={pageAction,mediaAction};
})(self);
