// Ephemeral activity never blocks durable message delivery.
const WAITING=new Set(['queued','send_uncertain','submitted_unconfirmed','accepted','media_collecting','collecting','preparing','prepared']);
export class DotTyping {
 constructor({relay,send,intervalMs=4000,maxMs=300000,now=()=>Date.now(),schedule=setTimeout,cancel=clearTimeout,report=()=>{}}){
  Object.assign(this,{relay,send,intervalMs,maxMs,now,schedule,cancel,report});
  this.requests=new Map();this.chats=new Map();this.timer=null;
  this.seen=new Set(Object.entries(relay.state.mirror?.published||{}).filter(([,p])=>p.status==='accepted').map(([id])=>id));
 }
 event(type,data){try{this.report({event:type,...data});}catch{}}
 start(event,hasMedia=false){
  const checked=hasMedia&&!event.media?.length?{...event,media:[{kind:'provider_attachment'}]}:event;
  if(!this.relay.allowed(checked))return null;
  const id=event.accountId+':'+event.peerId+':'+event.messageId;
  if(this.relay.state.incoming[id]||this.requests.has(id))return null;
  const topic=/^[1-9][0-9]*$/.test(String(event.topicId||''))&&Number(event.topicId)<=0x7fffffff?String(event.topicId):null;
  const key=JSON.stringify([event.peerId,topic]);
  this.requests.set(id,{id,key,peer:event.peerId,topic,messageId:String(event.messageId),started:this.now()});
  let chat=this.chats.get(key);
  if(!chat){chat={key,peer:event.peerId,topic,ids:new Set(),busy:false,pulses:0,lastAttempt:null};this.chats.set(key,chat);}
  chat.ids.add(id);
  if(!chat.busy)void this.pulse(chat);
  this.arm();return id;
 }
 arm(){if(this.timer!==null||!this.requests.size)return;this.timer=this.schedule(()=>{this.timer=null;this.tick();},this.intervalMs);this.timer?.unref?.();}
 async bounded(chat,active){
  let timer;
  try{return await Promise.race([Promise.resolve().then(()=>this.send(chat.peer,chat.topic,active)),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('typing_timeout')),3500);timer.unref?.();})]);}
  finally{clearTimeout(timer);}
 }
 async pulse(chat){
  if(chat.busy||this.chats.get(chat.key)!==chat||(chat.lastAttempt!==null&&this.now()-chat.lastAttempt<this.intervalMs))return;
  chat.busy=true;chat.lastAttempt=this.now();
  try{
   if(await this.bounded(chat,true)!==true)throw Error('typing_not_accepted');
   chat.pulses++;
   if(chat.pulses<=2)this.event(chat.pulses===1?'started':'renewed',{peer:chat.peer,topic:chat.topic,requests:[...chat.ids]});
  }catch{this.event('warning',{peer:chat.peer,topic:chat.topic,reason:'typing_unavailable'});}
  finally{chat.busy=false;if(this.chats.get(chat.key)!==chat&&!this.chats.has(chat.key))void this.bounded(chat,false).catch(()=>{});}
 }
 stop(id,reason='finished'){
  const r=this.requests.get(id);if(!r)return;
  this.requests.delete(id);const chat=this.chats.get(r.key);chat?.ids.delete(id);
  this.event('stopped',{peer:r.peer,topic:r.topic,request:id,reason});
  if(chat&&!chat.ids.size){this.chats.delete(r.key);if(!chat.busy)void this.bounded(chat,false).catch(()=>{});}
  if(!this.requests.size&&this.timer!==null){this.cancel(this.timer);this.timer=null;}
 }
 record(r){return this.relay.state.incoming[r.id]||Object.values(this.relay.state.incoming).find(p=>p.delivery_peer_id===r.peer&&p.source_message_ids?.includes(r.messageId));}
 settled(id,result){if(id&&result?.status&&!WAITING.has(result.status))this.stop(id,'ingress_'+result.status);}
 delivered(pub){
  if(pub.role!=='assistant'||pub.status!=='accepted')return;
  const parent=pub.request_id&&this.relay.state.incoming[pub.request_id];
  for(const r of [...this.requests.values()]){
   const exact=pub.request_id===r.id||(parent?.delivery_peer_id===r.peer&&parent.source_message_ids?.includes(r.messageId));
   // Unthreaded group output never completes a private request.
   const groupAnswer=!pub.reply_to&&!pub.request_id&&pub.peer_id===this.relay.peerId&&r.peer===pub.peer_id&&
    (pub.topic_id==null||String(pub.topic_id)===r.topic)&&this.record(r)?.status==='accepted';
   if(exact||groupAnswer)this.stop(r.id,'reply_delivered');
  }
 }
 tick(){
  const mirror=this.relay.state.mirror;
  for(const [id,p] of Object.entries(mirror?.published||{}))if(p.status==='accepted'&&!this.seen.has(id)){this.seen.add(id);this.delivered(p);}
  for(const r of [...this.requests.values()]){
   const record=this.record(r);
   if(this.now()-r.started>=this.maxMs)this.stop(r.id,'response_timeout');
   else if(record&&!WAITING.has(record.status))this.stop(r.id,'ingress_'+record.status);
   else if(mirror?.blocked||mirror?.readFailures>0)this.stop(r.id,'mirror_unavailable');
  }
  for(const chat of this.chats.values())if(!chat.busy)void this.pulse(chat);
  this.arm();
 }
 close(){for(const id of [...this.requests.keys()])this.stop(id,'shutdown');}
}
