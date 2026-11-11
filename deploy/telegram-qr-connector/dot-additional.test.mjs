import './dot-test-policy.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {AdditionalPrivateDots,validateAdditionalBindings} from './dot-additional.mjs';
const binding={enabled:true,owner:'user_airlock',peerId:'111111111',botAccountId:'telegram:333333333',agentId:'mini',connectionId:'airlock',accountScope:'a'.repeat(64),pageId:42,room:'cccccccccccccccccccccccccccccccc',url:'https://chatgpt.com/dots/new-room'};
test('additional private bot cannot replace existing bots or target a different recipient',()=>{
 assert.throws(()=>validateAdditionalBindings([binding],{peerId:'999',reserved:[]}),/binding/);
 assert.throws(()=>validateAdditionalBindings([binding],{peerId:binding.peerId,reserved:[binding.botAccountId]}),/reserved/);
 assert.throws(()=>validateAdditionalBindings([binding,binding],{peerId:binding.peerId,reserved:[]}),/duplicate/);
 assert.equal(validateAdditionalBindings([binding],{peerId:binding.peerId,reserved:[]}).length,1);
});
test('same binding initializes once under concurrent tick and receive; foreign traffic is ignored',async()=>{
 let initialized=0,ticks=0,received=0;let release;
 const wait=new Promise(r=>release=r);
 const dots=new AdditionalPrivateDots({read:()=>[binding],create:async()=>{initialized++;await wait;return {mirror:{tick:async()=>{ticks++;}},receive:async()=>{received++;return {status:'accepted'};}};}});
 const event={accountId:binding.botAccountId,peerId:binding.peerId,senderId:binding.peerId,private:true,out:false,isEdit:false};
 const a=dots.tick();const b=dots.receive(event,{});release();await Promise.all([a,b]);
 assert.equal(initialized,1);assert.equal(ticks,1);assert.equal(received,1);
 assert.equal(await dots.receive({...event,senderId:'999'},{}),true);assert.equal(received,1);
 assert.equal(await dots.receive({...event,accountId:'telegram:other'},{}),false);
});
test('changed binding fails before using cached relay; disabling prevents subsequent sends',async()=>{
 let current=[binding],received=0;
 const dots=new AdditionalPrivateDots({read:()=>current,create:async()=>({mirror:{tick:async()=>{}},receive:async()=>{received++;}})});
 const e={accountId:binding.botAccountId,peerId:binding.peerId,senderId:binding.peerId,private:true,out:false,isEdit:false};
 await dots.receive(e,{});current=[{...binding,room:'dddddddddddddddddddddddddddddddd'}];
 await assert.rejects(()=>dots.receive(e,{}),/binding_changed/);assert.equal(received,1);
 current=[];assert.equal(await dots.receive(e,{}),false);
});
test('room collisions and disabled rows cannot start duplicate mirrors',()=>{
 assert.throws(()=>validateAdditionalBindings([binding,{...binding,botAccountId:'telegram:444444444'}],{peerId:binding.peerId}),/room/);
 assert.throws(()=>validateAdditionalBindings([binding],{peerId:binding.peerId,reservedRooms:[binding.room]}),/room/);
 assert.deepEqual(validateAdditionalBindings([{...binding,enabled:false}],{peerId:binding.peerId}),[]);
});
test('invalid additional configuration does not interrupt legacy routing',()=>{
 const dots=new AdditionalPrivateDots({read:()=>{throw Error('bad JSON');},create:()=>{throw Error('unexpected');}});
 assert.equal(dots.matches('telegram:legacy'),false);
});
