import './dot-test-policy.mjs';import test from 'node:test';import assert from 'node:assert/strict';
import {routeGroupDotEvent} from './dot-group-receive.mjs';
test('group images use the existing ingress; previews remain text and other files stay recorded',async()=>{
 const events=[];const relay={prepareReceive:async e=>(events.push(['text',e.messageId]),{status:'accepted'}),holdIncoming:(e,r)=>(events.push([r,e.messageId]),{status:r})};
 const ingress={receive:(e,m)=>(events.push(['image',e.messageId]),{status:'media_collecting'}),waitForPending:async()=>{}};
 const event={messageId:'1',text:'Оля:\nтекст'};
 assert.equal((await routeGroupDotEvent({event,message:{media:{className:'MessageMediaWebPage'}},relay,ingress})).status,'accepted');
 assert.equal((await routeGroupDotEvent({event:{...event,messageId:'2'},message:{media:{className:'MessageMediaPhoto',photo:{}}},relay,ingress})).status,'media_collecting');
 assert.equal((await routeGroupDotEvent({event:{...event,messageId:'3'},message:{media:{className:'MessageMediaDocument'}},relay,ingress})).status,'media_requires_review');
 assert.deepEqual(events,[['text','1'],['image','2'],['media_requires_review','3']]);
});
