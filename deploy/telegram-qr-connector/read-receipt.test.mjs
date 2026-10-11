// Fast unit: exact account isolation and official channel/DM routing; max5s.
import assert from 'node:assert/strict';import test from 'node:test';
import {acknowledgeRead} from './read-receipt.mjs';
class InputPeerChannel {constructor(){this.channelId=7;this.accessHash=9}}
class InputChannel {constructor(p){Object.assign(this,p)}}
class DM {constructor(p){Object.assign(this,p);this.kind='dm'}}
class Channel {constructor(p){Object.assign(this,p);this.kind='channel'}}
const Api={InputPeerChannel,InputChannel,messages:{ReadHistory:DM},channels:{ReadHistory:Channel}};
test('positive exact cursor uses only selected connected account, not another client',async()=>{
 const calls=[],client={getEntity:async()=>({}),getInputEntity:async()=>({userId:5}),invoke:async r=>calls.push(r)};
 const slots=new Map([['one',{accountId:'telegram:540',client,labelPeers:{idPeers:new Map()}}]]);
 assert.deepEqual(await acknowledgeRead({slots,Api,payload:{account_id:'telegram:540',peer_id:'381',max_id:2}}),{ok:true,account_id:'telegram:540',peer_id:'381',max_id:2});
 assert.equal(calls[0].kind,'dm');assert.equal(calls[0].maxId,2);
 await assert.rejects(acknowledgeRead({slots,Api,payload:{account_id:'other',peer_id:'381',max_id:2}}));
 await assert.rejects(acknowledgeRead({slots,Api,payload:{account_id:'telegram:540',peer_id:'381',max_id:0}}));
 assert.equal(calls.length,1);
});
test('channel cursor uses channels.ReadHistory without clearing mentions separately',async()=>{
 const calls=[],client={getEntity:async()=>({}),getInputEntity:async()=>new InputPeerChannel(),invoke:async r=>calls.push(r)};
 await acknowledgeRead({slots:new Map([['one',{accountId:'telegram:540',client,labelPeers:{idPeers:new Map()}}]]),Api,payload:{account_id:'telegram:540',peer_id:'-1007',max_id:2}});
 assert.equal(calls.length,1);assert.equal(calls[0].kind,'channel');assert.equal(calls[0].channel.channelId,7);
});
