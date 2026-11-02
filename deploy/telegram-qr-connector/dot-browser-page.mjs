export async function pageAction(url, room, text, after, replyTo=null) {
    if (location.href !== url) return {ok:false,error:'wrong_dot_route'};
    const allRows = [...document.querySelectorAll('article.message-row[data-message-id]')];
    const rows = allRows.filter(e=>!e.closest?.('.reply-chain-panel[role="dialog"]'));
    const prefix = room + '~' + room + '~CalpicoMessage~';
    if (!rows.length || rows.some(e => !e.dataset.messageId.startsWith(prefix))) return {ok:false,error:'wrong_or_unloaded_room'};
    const messages=[];
    for(const e of rows){
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
        text:e.querySelector('.message-text')?.innerText||'',
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
    editor.focus();
    if(!document.execCommand('insertText',false,text)) return {ok:false,error:'insert_failed'};
    await new Promise(resolve=>setTimeout(resolve,100));
    const ready=[...scope.querySelectorAll('button')].filter(e=>e.getClientRects().length && ['Отправить','Send'].includes(e.getAttribute('aria-label')));
    if(editor.innerText.trim()!==text.trim() || ready.length!==1 || ready[0].disabled) return {ok:false,error:'draft_requires_review'};
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
    return {ok:true,status:'submitted_unconfirmed',cursor:messages.at(-1)?.id};
}
