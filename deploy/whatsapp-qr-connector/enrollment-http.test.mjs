// Focused integration expected2s/max10s: actual wrapper HTTP/cookie→slot→
// registration/ingress/sender chain. SDK is a deterministic local provider.
import http from 'node:http';
import {spawn} from 'node:child_process';
import {mkdirSync,mkdtempSync,rmSync,readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import assert from 'node:assert/strict';
const tmp=mkdtempSync(join(process.cwd(),'.tmp/wa-http-'));
mkdirSync(join(tmp,'sessions/account-1'),{recursive:true});
const accounts=[],messages=[];
const core=http.createServer((req,res)=>{
 const cookie=req.headers.cookie||'';const uid=cookie.includes('alice')?'alice':cookie.includes('bob')?'bob':null;
 if(req.url==='/v1/connection-principal'){
  if(!uid){res.writeHead(401);return res.end('{}');}
  res.setHeader('Content-Type','application/json');return res.end(JSON.stringify({user_id:uid,can_manage:true}));
 }
 let body='';req.on('data',c=>body+=c);req.on('end',()=>{
  const payload=JSON.parse(body);
  if(req.url==='/v1/accounts')accounts.push({...payload,owner:uid||'user_owner'});
  else if(req.url==='/v1/messages')messages.push(payload);
  res.writeHead(202);res.end('{}');
 });
});
await new Promise(r=>core.listen(0,'127.0.0.1',r));
const reserve=http.createServer();await new Promise(r=>reserve.listen(0,'127.0.0.1',r));
const port=reserve.address().port;await new Promise(r=>reserve.close(r));
const child=spawn(process.execPath,['--loader',fileURLToPath(new URL('./fixtures/mock-loader.mjs',import.meta.url)),fileURLToPath(new URL('./server.mjs',import.meta.url))],{
 env:{...process.env,PORT:String(port),PUBLIC_PREFIX:'',USERIO_WHATSAPP_STATE_DIR:tmp,
  UNIVERSAL_USERIO_URL:'http://127.0.0.1:'+core.address().port,USERIO_API_TOKEN:'fixture-service'},stdio:['ignore','pipe','pipe']});
let output='';child.stdout.on('data',c=>output+=c);child.stderr.on('data',c=>output+=c);
const base='http://127.0.0.1:'+port;
async function get(path,user){return fetch(base+path,{headers:user?{Cookie:'userio_web_session='+user}:{},redirect:'manual'});}
async function until(check){const deadline=Date.now()+7000;while(Date.now()<deadline){try{if(await check())return;}catch{}await new Promise(r=>setTimeout(r,30));}throw Error('Expected wrapper state missing');}
try{
 await until(async()=> (await get('/',null)).status===401);
 assert.deepEqual(await (await get('/state','bob')).json(),[]);
 const created=await get('/new','alice');assert.equal(created.status,302);
 const slot=new URL(created.headers.get('location'),base).searchParams.get('slot');assert.equal(slot,'account-2');
 assert.equal((await get('/cancel?slot='+slot,'bob')).status,403);
 assert.equal((await get('/?slot='+slot,'bob')).status,403);
 await until(async()=> (await (await get('/state','alice')).json()).some(x=>x.id===slot&&x.status==='connected'));
 const account=accounts.find(x=>x.owner==='alice');assert.equal(account.id,'whatsapp:1234562:account-2');assert.equal(account.can_reply,true);
 assert.deepEqual(await (await get('/state','bob')).json(),[]);
 await until(()=>messages.some(x=>x.account_id===account.id));
 assert.ok(messages.filter(x=>x.account_id).every(x=>x.message.message_id.startsWith(x.account_id+'|')));
 const sent=await fetch(base+'/send',{method:'POST',headers:{Authorization:'Bearer fixture-service','Content-Type':'application/json'},body:JSON.stringify({user_id:'alice',account_ref:account.id,chatId:'target@s.whatsapp.net',message:'fixture'})});
 assert.equal((await sent.json()).slot,slot);
 const wrong=await fetch(base+'/send',{method:'POST',headers:{Authorization:'Bearer fixture-service','Content-Type':'application/json'},body:JSON.stringify({user_id:'bob',account_ref:account.id,chatId:'target@s.whatsapp.net',message:'unsent'})});
 assert.ok(wrong.status>=400);
 const links=JSON.parse(readFileSync(join(tmp,'slot-accounts.json')));assert.equal(links[slot].owner,'alice');assert.equal(links[slot].account_ref,account.id);
 console.log('WhatsApp personal HTTP registration/source/send and foreign rejection: PASS');
}catch(e){console.error(output.slice(-1200));throw e;}
finally{
 child.kill('SIGTERM');await new Promise(resolve=>{if(child.exitCode!==null)resolve();else child.once('exit',resolve);});
 await new Promise(r=>core.close(r));rmSync(tmp,{recursive:true,force:true});
}
