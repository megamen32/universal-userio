'use strict';
(() => {
 const pages = JSON.parse(document.getElementById('pages-data').textContent);
 const pageMap = new Map(pages.map(p => [p.slug, p]));
 const $ = (q, root=document) => root.querySelector(q);
 const $$ = (q, root=document) => Array.from(root.querySelectorAll(q));
 const menu = $('#menu-toggle'), dialog=$('#search-dialog'), input=$('#search-input'), results=$('#search-results');
 let current='overview', resultIndex=0, lastFocus=null, toastTimer=null;
 function toast(text){const node=$('#toast');node.textContent=text;node.classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>node.classList.remove('show'),2600);}
 async function copy(text){
  try{ if(navigator.clipboard && window.isSecureContext){await navigator.clipboard.writeText(text);}else{const a=document.createElement('textarea');a.value=text;a.style.cssText='position:fixed;left:-9999px;top:0';document.body.appendChild(a);a.select();const ok=document.execCommand('copy');a.remove();if(!ok)throw new Error('copy unavailable');}toast('Скопировано');return true;}
  catch(e){toast('Копирование недоступно. Выделите текст вручную.');return false;}
 }
 function closeMenu(){document.body.classList.remove('menu-open');menu.setAttribute('aria-expanded','false');}
 menu.addEventListener('click',()=>{const open=document.body.classList.toggle('menu-open');menu.setAttribute('aria-expanded',String(open));if(open)$('#search-trigger').focus();});
 $('#backdrop').addEventListener('click',closeMenu);
 function showPage(){
  const hash=location.hash.slice(1);let target;
  try{target=decodeURIComponent(hash);}catch(e){target='overview';}
  if(target==='main'){const main=$('#main');main.setAttribute('tabindex','-1');main.focus();main.scrollIntoView({block:'start'});return;}
  let slug=target.split('--')[0]||'overview';if(!pageMap.has(slug))slug='overview';
  const changed=slug!==current;current=slug;
  $$('.page').forEach(p=>p.classList.toggle('active',p.id===slug));
  $$('.nav-link').forEach(a=>{if(a.getAttribute('href')==='#'+slug)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
  const page=pageMap.get(slug);$('#breadcrumb-group').textContent=page.group;$('#breadcrumb-title').textContent=page.title;
  document.title=page.title+' · User.io Docs';
  const toc=$('#toc-list');toc.replaceChildren();
  $$('h2',document.getElementById(slug)).forEach(h=>{const a=document.createElement('a');a.href='#'+h.id;a.textContent=h.textContent;toc.appendChild(a);});
  $('#copy-markdown').onclick=()=>copy(page.markdown);
  closeMenu();
  if(target.includes('--') && document.getElementById(target)){requestAnimationFrame(()=>document.getElementById(target).scrollIntoView({block:'start'}));}
  else {requestAnimationFrame(()=>window.scrollTo({top:0,behavior:'instant'}));}
  if(changed){const h=$('h1',document.getElementById(slug));h.setAttribute('tabindex','-1');h.focus({preventScroll:true});}
 }
 window.addEventListener('hashchange',showPage);showPage();
 $$('.code-copy').forEach(b=>b.addEventListener('click',()=>copy($('pre code',b.closest('.code-wrap')).textContent)));
 $$('[data-copy-endpoint]').forEach(b=>b.addEventListener('click',()=>copy('https://msg.bezrabotnyi.com/mcp')));
 const themeButton=$('#theme-toggle');
 function applyTheme(theme){document.documentElement.dataset.theme=theme;themeButton.setAttribute('aria-label',theme==='dark'?'Включить светлую тему':'Включить тёмную тему');themeButton.setAttribute('title',themeButton.getAttribute('aria-label'));}
 let stored='light';try{stored=localStorage.getItem('userio-docs-theme')||'light';}catch(e){}applyTheme(stored==='dark'?'dark':'light');
 themeButton.addEventListener('click',()=>{const theme=document.documentElement.dataset.theme==='dark'?'light':'dark';applyTheme(theme);try{localStorage.setItem('userio-docs-theme',theme);}catch(e){}});
 const norm=s=>s.toLocaleLowerCase('ru').replaceAll('ё','е');
 function textWithMarks(node,text,query){
  const words=norm(query).split(/\s+/).filter(Boolean).sort((a,b)=>b.length-a.length);
  if(!words.length){node.textContent=text;return;}
  const escaped=words.map(w=>w.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'));
  const regex=new RegExp(escaped.join('|'),'giu');let last=0,match;
  while((match=regex.exec(norm(text)))!==null){node.appendChild(document.createTextNode(text.slice(last,match.index)));const m=document.createElement('mark');m.textContent=text.slice(match.index,match.index+match[0].length);node.appendChild(m);last=match.index+match[0].length;}
  node.appendChild(document.createTextNode(text.slice(last)));
 }
 function renderSearch(){
  const q=input.value.trim(),terms=norm(q).split(/\s+/).filter(Boolean);results.replaceChildren();resultIndex=0;
  const ranked=pages.map(p=>{const title=norm(p.title+' '+p.summary),body=norm(p.text);return {p,score:terms.reduce((s,t)=>s+(title.includes(t)?10:0)+(body.includes(t)?1:0),0),match:terms.every(t=>(title+' '+body).includes(t))};}).filter(x=>x.match).sort((a,b)=>b.score-a.score).slice(0,10);
  if(!ranked.length){const empty=document.createElement('div');empty.className='empty-search';empty.textContent='Ничего не найдено. Попробуйте «Telegram», «пароль» или «MCP».';results.appendChild(empty);return;}
  for(const {p} of ranked){
   const a=document.createElement('a');a.className='result';a.href='#'+p.slug;a.setAttribute('role','option');a.id='result-'+p.slug;
   const group=document.createElement('small');group.textContent=p.group;
   const title=document.createElement('strong');textWithMarks(title,p.title,q);
   const snippet=document.createElement('p');let text=p.summary;
   if(terms.length && !terms.some(t=>norm(text).includes(t))){const at=norm(p.text).indexOf(terms[0]);if(at>=0){const start=Math.max(0,at-65);text=(start?'…':'')+p.text.slice(start,start+190)+'…';}}
   textWithMarks(snippet,text,q);a.append(group,title,snippet);a.addEventListener('click',()=>{closeSearch(false);closeMenu();if(current===p.slug)window.scrollTo(0,0);});results.appendChild(a);
  }
  highlightResult();
 }
 function highlightResult(){const links=$$('.result',results);links.forEach((a,i)=>a.setAttribute('aria-selected',String(i===resultIndex)));input.setAttribute('aria-activedescendant',links[resultIndex]?.id||'');}
 function openSearch(){closeMenu();lastFocus=document.activeElement;renderSearch();dialog.showModal();input.focus();}
 function closeSearch(restore=true){dialog.close();if(restore && lastFocus)lastFocus.focus();}
 $('#search-trigger').addEventListener('click',openSearch);$('#search-mobile').addEventListener('click',openSearch);$('#close-search').addEventListener('click',()=>closeSearch());
 input.addEventListener('input',renderSearch);
 dialog.addEventListener('click',e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)closeSearch();}});
 dialog.addEventListener('cancel',()=>{if(lastFocus)lastFocus.focus();});
 document.addEventListener('keydown',e=>{
  if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();if(dialog.open)closeSearch();else openSearch();}
  if(e.key==='Escape'){closeMenu();if(dialog.open){e.preventDefault();closeSearch();return;}}
  if(!dialog.open)return;
  const links=$$('.result',results);
  if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();if(links.length){resultIndex=(resultIndex+(e.key==='ArrowDown'?1:-1)+links.length)%links.length;highlightResult();links[resultIndex].scrollIntoView({block:'nearest'});}}
  if(e.key==='Enter'&&document.activeElement===input&&links[resultIndex]){e.preventDefault();links[resultIndex].click();}
 });
})();
