import test from 'node:test';
import assert from 'node:assert/strict';
import {resolveMediaPeer} from './media-peer.mjs';

test('resolves an uncached exact peer on the selected account',async()=>{
 const peer={id:'-5491867632'};let calls=0;
 const item={accountId:'telegram:540308572',labelPeers:{idPeers:new Map()},client:{getEntity:async id=>{calls++;assert.equal(id,peer.id);return peer},getPeerId:async p=>p.id}};
 assert.equal(await resolveMediaPeer(item,{accountId:item.accountId,chatKey:peer.id}),peer);assert.equal(calls,1);
});
test('refuses mismatched account and peer, without using labels as fallback',async()=>{
 let calls=0;const item={accountId:'telegram:2',labelPeers:{idPeers:new Map()},client:{getEntity:async()=>{calls++;return {id:'-2'}},getPeerId:async p=>p.id}};
 assert.equal(await resolveMediaPeer(item,{accountId:'telegram:1',chatKey:'-1'}),null);assert.equal(calls,0);
 assert.equal(await resolveMediaPeer(item,{accountId:'telegram:2',chatKey:'-1'}),null);assert.equal(calls,1);
});
