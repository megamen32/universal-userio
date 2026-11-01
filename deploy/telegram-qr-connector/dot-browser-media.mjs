import {requireDotPolicy} from './dot-policy.mjs';
import fs from 'node:fs';
import {spawn} from 'node:child_process';
import {randomUUID,createHash} from 'node:crypto';
import {MAX_IMAGES,MAX_IMAGE_BYTES,MAX_ALBUM_BYTES,imageKind} from './dot-media.mjs';
const ROOT='/var/lib/universal-userio/telegram-qr/dot-media-files/';
export function validateMediaFiles(media){
 if(!Array.isArray(media)||media.length<1||media.length>MAX_IMAGES)throw Error('invalid_media_count');
 let total=0;
 return media.map(m=>{
  if(typeof m.path!=='string'||!m.path.startsWith(ROOT)||!/^tg-[0-9]+-[a-f0-9]{16}\.(jpg|png|webp)$/.test(m.filename)||m.path!==ROOT+m.filename||!/^[a-f0-9]{64}$/.test(m.sha256))throw Error('invalid_media_path');
  const stat=fs.lstatSync(m.path);
  if(!stat.isFile()||stat.isSymbolicLink()||stat.uid!==process.getuid()||(stat.mode&0o777)!==0o600||stat.size>MAX_IMAGE_BYTES)throw Error('unsafe_media_file');
  const data=fs.readFileSync(m.path);total+=data.length;if(total>MAX_ALBUM_BYTES)throw Error('album_size_refused');
  if(createHash('sha256').update(data).digest('hex')!==m.sha256||imageKind(data).mime!==m.mime)throw Error('media_identity_changed');
  return {filename:m.filename,data:data.toString('base64')};
 });
}
async function remotePython(code,input){
 const policy=requireDotPolicy();
 const quoted="'"+code.replaceAll("'","'\\''")+"'";
 return new Promise((resolve,reject)=>{
  const child=spawn('ssh',['-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=5',policy.sshTarget,policy.pythonPath+' -c '+quoted],{stdio:['pipe','pipe','pipe']});
  let output='',settled=false;
  const done=(err)=>{if(settled)return;settled=true;clearTimeout(timer);if(err){child.kill();reject(Error(err));}else resolve(output);};
  const timer=setTimeout(()=>done('media_transfer_timeout'),60000);
  child.stdout.setEncoding('utf8');child.stdout.on('data',s=>{output+=s;if(output.length>8192)done('media_transfer_result_invalid');});
  child.stderr.resume();child.on('error',()=>done('media_transfer_failed'));child.on('close',code=>done(code?'media_transfer_failed':null));child.stdin.on('error',()=>{});child.stdin.end(JSON.stringify(input));
 });
}
export async function stageMedia(media){
 const files=validateMediaFiles(media),directory='/tmp/userio-dot-media-'+randomUUID();
 const code="import sys,json,os,base64\np=json.load(sys.stdin)\nd=p['directory']\nassert d.startswith('/tmp/userio-dot-media-') and '/' not in d[len('/tmp/'):]\nos.umask(0o077)\nos.mkdir(d,0o700)\npaths=[]\nfor f in p['files']:\n n=f['filename']; assert '/' not in n and n.startswith('tg-')\n path=d+'/'+n\n with open(path,'xb') as out: out.write(base64.b64decode(f['data'],validate=True))\n paths.append(path)\nprint(json.dumps(paths))";
 const paths=JSON.parse(await remotePython(code,{directory,files}));
 if(!Array.isArray(paths)||paths.length!==files.length||paths.some((p,i)=>p!==directory+'/'+files[i].filename))throw Error('media_stage_unverified');
 return {directory,paths};
}
export async function cleanupStagedMedia(staged){
 if(!staged?.directory?.match(/^\/tmp\/userio-dot-media-[a-f0-9-]{36}$/))return;
 await remotePython("import sys,json,os\np=json.load(sys.stdin)\nd=p['directory']\nassert d.startswith('/tmp/userio-dot-media-') and '/' not in d[len('/tmp/'):]\nfor n in os.listdir(d):\n assert n.startswith('tg-') and '/' not in n\n os.unlink(d+'/'+n)\nos.rmdir(d)",{directory:staged.directory}).catch(()=>{});
}
export async function mediaPageAction(url,room,action,args){
 const panelSelector='.reply-chain-panel[role="dialog"]';
 const fail=error=>({ok:false,error});
 if(location.href!==url)return fail('wrong_dot_route');
 const prefix=room+'~'+room+'~CalpicoMessage~';
 const main=[...document.querySelectorAll('article.message-row[data-message-id]')].filter(e=>!e.closest(panelSelector));
 if(!main.length||main.some(e=>!e.dataset.messageId.startsWith(prefix)))return fail('wrong_or_unloaded_room');
 let scope=document,panel=document.querySelector(panelSelector);
 if(action==='prepare'){
  const editors=[...document.querySelectorAll('[contenteditable=true][data-composer-markdown]')].filter(e=>e.getClientRects().length);
  if(editors.length!==1||editors[0].innerText.trim())return fail('existing_draft');
  if(panel)return fail('existing_reply_panel');
  if(args.reply_to){
   const row=main.find(e=>e.dataset.messageId===args.reply_to);
   const button=row?.querySelector('[data-action=reply]');if(!button)return fail('reply_target_not_visible');
   button.click();
   for(let i=0;i<20;i++){await new Promise(r=>setTimeout(r,100));panel=document.querySelector(panelSelector);if(panel?.querySelector('article[data-message-id]'))break;}
  }
 }
 if(args.reply_to){
  if(!panel)return fail('reply_panel_unverified');
  const rows=[...panel.querySelectorAll('article[data-message-id]')];
  if(rows.length!==1||rows[0].dataset.messageId!==args.reply_to)return fail('reply_target_ambiguous');
  scope=panel;
 }else if(panel)return fail('existing_reply_panel');
 const editors=[...scope.querySelectorAll('[contenteditable=true][data-composer-markdown]')].filter(e=>e.getClientRects().length);
 if(editors.length!==1)return fail('ambiguous_composer');
 const editor=editors[0],composer=editor.closest('[data-role=composer]');
 if(!composer)return fail('composer_scope_missing');
 if(action==='prepare'){
  if(editor.innerText.trim()||composer.querySelectorAll('img').length)return fail('existing_composer_content');
  const add=[...scope.querySelectorAll('button')].filter(b=>b.getClientRects().length&&['Добавить файлы и другое','Add files and more'].includes(b.getAttribute('aria-label')));
  if(add.length!==1)return fail('upload_control_missing');
  add[0].click();return {ok:true,status:'chooser_ready'};
 }
 const filenames=args.filenames||[];
 const images=[...composer.querySelectorAll('img')].filter(e=>e.getClientRects().length);
 const seen=filenames.every(n=>composer.outerHTML.includes(n));
 const send=[...scope.querySelectorAll('button')].filter(b=>b.getClientRects().length&&['Отправить','Send'].includes(b.getAttribute('aria-label')));
 const uploading=!!composer.querySelector('[role=progressbar],[aria-busy=true]')||[...composer.querySelectorAll('[aria-label]')].some(e=>/Uploading|Загрузка|Загружается/i.test(e.getAttribute('aria-label')||''))||/Uploading|Загрузка|Загружается/i.test(composer.innerText);
 const failedUpload=/upload failed|failed to upload|error uploading|could not upload|couldn't upload|ошибка загрузки|не удалось загрузить/i.test(composer.innerText)||[...composer.querySelectorAll('[aria-label]')].some(e=>/retry upload|upload failed|failed to upload|повторить загрузку|ошибка загрузки|не удалось загрузить/i.test(e.getAttribute('aria-label')||''));
 if(failedUpload)return fail('media_upload_failed_requires_review');
 const ready=filenames.length>0&&images.length===filenames.length&&seen&&!uploading&&send.length===1&&!send[0].disabled;
 if(action==='inspect')return {ok:true,ready,imageCount:images.length,filenamesSeen:seen,uploading};
 if(action!=='send'||!ready)return fail('upload_not_ready');
 if(editor.innerText.trim())return fail('existing_draft');
 if(args.text){
  editor.focus();if(!document.execCommand('insertText',false,args.text))return fail('insert_failed');
  await new Promise(r=>setTimeout(r,100));
  if(editor.innerText.trim()!==args.text.trim())return fail('caption_unverified');
 }
 if(location.href!==url)return fail('navigation_requires_review');
 const current=[...scope.querySelectorAll('button')].filter(b=>b.getClientRects().length&&['Отправить','Send'].includes(b.getAttribute('aria-label')));
 if(current.length!==1||current[0].disabled)return fail('send_not_ready');
 current[0].click();
 await new Promise(r=>setTimeout(r,200));
 if(panel&&!editor.innerText.trim()&&!composer.querySelectorAll('img').length){
  [...panel.querySelectorAll('button')].find(b=>['Закрыть ветку ответов','Close reply thread','Close replies'].includes(b.getAttribute('aria-label')))?.click();
 }
 return {ok:true,status:'submitted_unconfirmed'};
}
function toolText(output){
 const line=output.split('\n').find(l=>l.trim().startsWith('{'));const j=JSON.parse(line||'null');
 if(!j||j.error||j.result?.isError)throw Error('media_gateway_failed');
 return (j.result?.content||[]).filter(c=>c.type==='text').map(c=>c.text||'').join('\n');
}
export function fileChooserUid(output){
 const lines=toolText(output).split('\n').filter(l=>/button "(?:Добавить фото и файлы|Add photos & files|Add photos and files)"/.test(l));
 if(lines.length!==1)throw Error('file_chooser_ambiguous');
 const uid=lines[0].match(/uid=([A-Za-z0-9_-]+)/)?.[1];if(!uid)throw Error('file_chooser_missing');return uid;
}
export async function sendMediaThroughBrowser(call,page,args,parse,url,room,wait=ms=>new Promise(r=>setTimeout(r,ms))){
 const staged=await stageMedia(args.media);
 const evaluate=async action=>parse(await call('evaluate_script',{pageId:page,function:'() => ('+mediaPageAction.toString()+')(...'+JSON.stringify([url,room,action,{text:args.text||'',reply_to:args.reply_to||null,filenames:args.media.map(m=>m.filename)}])+')'}));
 try{
  const prepared=await evaluate('prepare');if(!prepared.ok)return prepared;
  const uid=fileChooserUid(await call('take_snapshot',{pageId:page}));
  toolText(await call('upload_file',{pageId:page,uid,filePaths:staged.paths,includeSnapshot:false}));
  let stable=0;
  for(let i=0;i<30;i++){await wait(1000);const r=await evaluate('inspect');if(!r.ok)return r;stable=r.ready?stable+1:0;if(stable>=2)return await evaluate('send');}
  return {ok:false,error:'media_upload_timeout_requires_review'};
 }finally{await cleanupStagedMedia(staged);}
}
