import './dot-test-policy.mjs';
import test from 'node:test';import assert from 'node:assert/strict';
import {DotTyping} from './dot-typing.mjs';
import {routeGroupDotEvent} from './dot-group-receive.mjs';
const group='-100123456',bot='telegram:999999';
const event=(peer=group,messageId='1',topicId=null)=>({accountId:bot,peerId:peer,senderId:'111',senderIsBot:false,private:peer!==group,membershipVerified:true,messageId,topicId,text:'hello'});
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function fixture({send,maxMs=300000}={}){
 let time=0;const calls=[],reports=[],timers=new Map();let tid=0;
 const relay={peerId:group,state:{incoming:{},mirror:{published:{},readFailures:0}},allowed:e=>e.accountId===bot&&!e.out&&!e.isEdit&&e.senderIsBot!==true&&(!e.private||e.membershipVerified)};
 const typing=new DotTyping({relay,send:send||((peer,topic,active)=>{calls.push({peer,topic,active});return true;}),now:()=>time,maxMs,
  schedule:(fn,ms)=>{const id=++tid;timers.set(id,{fn,ms});return id;},cancel:id=>timers.delete(id),report:e=>reports.push(e)});
 relay.typing=typing;
 return {typing,relay,calls,reports,timers,advance:ms=>{time+=ms;},record:(e,status='accepted')=>{const id=e.accountId+':'+e.peerId+':'+e.messageId;relay.state.incoming[id]={status,delivery_peer_id:e.peerId,source_message_ids:[e.messageId]};return id;}};
}
test('typing starts before browser or queued-media work and survives slow delivery',async()=>{
 const f=fixture();let resolve,entered=false;
 f.relay.prepareReceive=async e=>{entered=true;return new Promise(r=>{resolve=()=>{f.record(e);r({status:'accepted'});};});};
 const pending=routeGroupDotEvent({event:event(),message:{},relay:f.relay,ingress:{waitForPending:async()=>{}}});
 await flush();assert.equal(entered,true);assert.deepEqual(f.calls,[{peer:group,topic:null,active:true}]);
 assert.equal([...f.timers.values()][0].ms,4000);f.advance(4000);f.typing.tick();await flush();assert.equal(f.calls.length,2);
 resolve();await pending;assert.equal(f.typing.requests.size,1);f.typing.close();await flush();
});
test('typing RPC errors are warnings, never a failed or delayed message',async()=>{
 const f=fixture({send:async()=>{throw Error('network unavailable');}});f.relay.prepareReceive=async()=>({status:'accepted'});
 const result=await routeGroupDotEvent({event:event(),message:{},relay:f.relay,ingress:{waitForPending:async()=>{}}});
 await flush();assert.equal(result.status,'accepted');assert.ok(f.reports.some(r=>r.event==='warning'));f.typing.close();
});
test('private and group topics renew independently; one reply cannot cancel another chat',async()=>{
 const f=fixture(),a=event('111','7'),b=event(group,'8','4');const ai=f.typing.start(a),bi=f.typing.start(b);f.record(a);f.record(b);
 await flush();f.typing.delivered({role:'assistant',status:'accepted',request_id:ai,peer_id:'111'});await flush();
 assert.equal(f.typing.requests.has(ai),false);assert.equal(f.typing.requests.has(bi),true);
 assert.ok(f.calls.some(c=>c.peer==='111'&&c.active===false));assert.ok(!f.calls.some(c=>c.peer===group&&c.active===false));
 f.typing.close();await flush();
});
test('one peer remains typing until every separate request is answered',async()=>{
 const f=fixture(),a=event('111','7'),b=event('111','8');const ai=f.typing.start(a),bi=f.typing.start(b);f.record(a);f.record(b);await flush();
 f.typing.delivered({role:'assistant',status:'accepted',request_id:ai,peer_id:'111'});await flush();
 assert.equal(f.typing.requests.size,1);assert.ok(!f.calls.some(c=>!c.active));
 f.typing.delivered({role:'assistant',status:'accepted',request_id:bi,peer_id:'111'});await flush();assert.equal(f.calls.filter(c=>!c.active).length,1);
});
test('unthreaded group publication stops accepted group work, not private/queued/other topics',async()=>{
 const f=fixture(),a=event(group,'1','4'),b=event('111','2'),c=event(group,'3','5'),d=event(group,'4','4');
 const ids=[a,b,c,d].map(e=>f.typing.start(e));[a,b,c].forEach(e=>f.record(e));f.record(d,'queued');await flush();
 f.typing.delivered({role:'assistant',status:'accepted',peer_id:group,topic_id:'4',request_id:null,reply_to:null});
 assert.equal(f.typing.requests.has(ids[0]),false);for(const id of ids.slice(1))assert.equal(f.typing.requests.has(id),true);f.typing.close();await flush();
});
test('fresh published receipt stops renewal and cancels Telegram typing',async()=>{
 const f=fixture(),e=event('111');const id=f.typing.start(e);f.record(e);await flush();
 f.relay.state.mirror.published.answer={role:'assistant',status:'accepted',request_id:id,peer_id:'111'};
 f.advance(4000);f.typing.tick();await flush();assert.equal(f.typing.requests.size,0);assert.deepEqual(f.calls.map(c=>c.active),[true,false]);
});
test('failure and mirror outage terminate typing without changing durable state',async()=>{
 for(const failure of ['not_accepted','requires_review','mirror']){
  const f=fixture(),e=event();f.typing.start(e);f.record(e,failure==='mirror'?'accepted':failure);if(failure==='mirror')f.relay.state.mirror.readFailures=1;
  const before=JSON.stringify(f.relay.state);await flush();f.advance(4000);f.typing.tick();await flush();
  assert.equal(f.typing.requests.size,0);assert.equal(JSON.stringify(f.relay.state),before);
 }
});
test('bounded timeout prevents endless fake typing',async()=>{
 const f=fixture({maxMs:8000}),e=event();f.typing.start(e);f.record(e);await flush();f.advance(8000);f.typing.tick();await flush();
 assert.equal(f.typing.requests.size,0);assert.ok(f.reports.some(r=>r.reason==='response_timeout'));assert.equal(f.timers.size,0);
});
test('duplicate, unauthorised and outgoing events do not rearm typing',async()=>{
 const f=fixture(),e=event();const id=f.typing.start(e);assert.ok(id);assert.equal(f.typing.start(e),null);await flush();assert.equal(f.calls.length,1);
 f.record(e);f.typing.stop(id);assert.equal(f.typing.start(e),null);
 for(const bad of [{...event('111','9'),membershipVerified:false},{...event(group,'10'),out:true},{...event(group,'11'),isEdit:true}])assert.equal(f.typing.start(bad),null);
 f.typing.close();await flush();
});
test('ingress exception cancels typing and preserves original error',async()=>{
 const f=fixture();f.relay.prepareReceive=async()=>{throw Error('existing_failure');};
 await assert.rejects(routeGroupDotEvent({event:event(),message:{},relay:f.relay,ingress:{waitForPending:async()=>{}}}),/existing_failure/);
 await flush();assert.equal(f.typing.requests.size,0);
});
test('media collection keeps typing, rejected formatting stops it',async()=>{
 const f=fixture(),e=event();f.relay.holdIncoming=()=>({status:'media_requires_review'});
 const result=await routeGroupDotEvent({event:e,message:{media:{className:'MessageMediaPhoto',photo:{}}},relay:f.relay,ingress:{receive:async()=>({status:'media_collecting'})}});
 assert.equal(result.status,'media_collecting');assert.equal(f.typing.requests.size,1);f.typing.close();await flush();
});
