import './dot-test-policy.mjs';
import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtempSync} from 'node:fs';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {memberAllowed,createGroupMemberAccess,privateReplyRoute} from './dot-group-private.mjs';
import {DotRelay,ROOM} from './dot-relay.mjs';import {DotMirror} from './dot-mirror.mjs';
import {createGroupBotSender} from './dot-group-send.mjs';
const group='-100123456',room=ROOM;
const row=(id,role,text)=>({id:room+'~'+room+'~CalpicoMessage~'+id,role,text});
const member=status=>({status,user:{id:111,is_bot:false}});
test('only current human group members may use private chat',()=>{
 for(const status of ['creator','administrator','member'])assert.equal(memberAllowed(member(status),'111'),true);
 for(const status of ['left','kicked','restricted'])assert.equal(memberAllowed(member(status),'111'),false);
 assert.equal(memberAllowed({...member('restricted'),is_member:true},'111'),true);
 assert.equal(memberAllowed({...member('member'),user:{id:111,is_bot:true}},'111'),false);
 assert.equal(memberAllowed(member('member'),'222'),false);
});
test('member lookup is group scoped and errors do not grant access',async()=>{
 let body;const check=createGroupMemberAccess({token:'fixture',groupId:group,fetcher:async(url,options)=>{body=JSON.parse(options.body);return {ok:true,json:async()=>({ok:true,result:member('member')})};}});
 assert.equal(await check('111'),true);assert.deepEqual(body,{chat_id:group,user_id:'111'});assert.equal(await check(group),false);
 const failed=createGroupMemberAccess({token:'fixture',groupId:group,fetcher:async()=>({ok:false,json:async()=>({ok:false})})});
 await assert.rejects(failed('111'),/membership_unavailable/);
});
function fixture(){
 let rows=[row('base','user','baseline')],sends=[],edits=[];
 const relay=new DotRelay({statePath:join(mkdtempSync(join(tmpdir(),'group-dm-')),'journal.json'),botAccountId:'telegram:999999',agentId:'browser',tabId:18,room,peerId:group,group:true,
  browserCall:async()=>({ok:true,room,cursor:rows.at(-1).id,messages:rows}),
  send:async(peer,text,reply)=>{sends.push({peer,text,reply});return String(sends.length+100);},
  edit:async(peer,id,text)=>{edits.push({peer,id,text});},wait:async()=>{}});
 relay.memberPrivateEnabled=true;relay.routeMessage=(m,rs)=>privateReplyRoute(relay,m,rs);
 const mirror=new DotMirror(relay,{stableMs:0});
 const record=(id,peer,echo,msgId)=>{relay.state.incoming[id]={status:'accepted',source:'telegram',delivery_peer_id:peer,private_member:peer!==group,echo,source_message_ids:[msgId]};};
 return {relay,mirror,rows:x=>{rows=x;},sends,edits,record};
}
test('private answer goes only to its sender, then group replies still go to the group',async()=>{
 const x=fixture();await x.mirror.tick();
 const u=row('private','user','private question'),a=row('answer','assistant','private answer');
 x.record('dm1','111',u.id,'5');x.rows([row('base','user','baseline'),u,a]);await x.mirror.tick();
 assert.deepEqual(x.sends,[{peer:'111',text:'dots:\nprivate answer',reply:'5'}]);
 assert.equal(x.sends.length,1);assert.equal(x.sends[0].peer,'111');assert.match(x.sends[0].text,/private answer/);
 assert.equal(x.relay.state.mirror.published[a.id].peer_id,'111');
 const g=row('group','user','group question'),ga=row('ga','assistant','group answer');x.record('g1',group,g.id,'6');
 x.rows([u,a,g,ga]);await x.mirror.tick();assert.equal(x.sends.length,2);assert.equal(x.sends[1].peer,group);
});
test('two participants with the same Telegram message id cannot share reply routes',async()=>{
 const x=fixture();const a=row('a','user','a'),b=row('b','user','b');
 x.record('dm1','111',a.id,'7');x.record('dm2','222',b.id,'7');
 assert.equal(x.relay.replyTarget('7','111'),a.id);assert.equal(x.relay.replyTarget('7','222'),b.id);assert.equal(x.relay.replyTarget('7',group),null);
 const answer=row('reply','assistant','b reply');assert.equal(privateReplyRoute(x.relay,answer,[a,b,answer]).peerId,'222');
});
test('private edits remain on their original peer',async()=>{
 const x=fixture();await x.mirror.tick();const u=row('u','user','private'),a=row('a','assistant','first');
 x.record('dm','111',u.id,'4');x.rows([row('base','user','baseline'),u,a]);await x.mirror.tick();
 x.rows([u,{...a,text:'edited'}]);await x.mirror.tick();assert.equal(x.edits[0].peer,'111');
});
test('interrupted private response cannot fall through to the public group',()=>{
 const x=fixture(),u=row('u','user','private'),g=row('g','user','manual'),a=row('a','assistant','uncertain');x.record('dm','111',u.id,'1');x.relay.state.activeTurn='dm';
 assert.equal(privateReplyRoute(x.relay,a,[u,g,a]).hold,'private_turn_interrupted');
 assert.equal(privateReplyRoute(x.relay,{...a,reply_to:u.id},[u,g,a]).peerId,'111');
});
test('DM sender must be the verified private peer, and group ingress stays intact',()=>{
 const x=fixture(),e={accountId:'telegram:999999',peerId:'111',senderId:'111',senderIsBot:false,private:true,membershipVerified:true,messageId:'7',text:'hello'};
 assert.equal(x.relay.allowed(e),true);assert.equal(x.relay.allowed({...e,membershipVerified:false}),false);assert.equal(x.relay.allowed({...e,senderId:'222'}),false);
 assert.equal(x.relay.allowed({...e,peerId:group,private:false,membershipVerified:false}),true);
});
test('peer-locked private sender cannot publish to a different user or group',async()=>{
 let sent=0;const token='999999:'+ 'a'.repeat(35);const sender=createGroupBotSender({token,botId:'999999',peerId:'111',allowPrivate:true,fetcher:async(url,options)=>{sent++;const b=JSON.parse(options.body);return {ok:true,json:async()=>({ok:true,result:{chat:{id:Number(b.chat_id)},from:{id:999999},message_id:1}})};}});
 assert.equal(await sender('111','hello'),1);await assert.rejects(sender('222','hello'),/invalid_group_send/);await assert.rejects(sender(group,'hello'),/invalid_group_send/);assert.equal(sent,1);
});
