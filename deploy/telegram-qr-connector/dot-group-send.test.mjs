import './dot-test-policy.mjs';import test from 'node:test';import assert from 'node:assert/strict';import {createHash} from 'node:crypto';import {createGroupBotSender,createGroupBotMediaSender} from './dot-group-send.mjs';
const peerId='-1004352022764',botId='123456',token=botId+':'+('x'.repeat(35));
test('send pins recipient and requires matching provider receipt',async()=>{let count=0;const send=createGroupBotSender({token,peerId,botId,fetcher:async(_u,o)=>{count++;assert.equal(JSON.parse(o.body).chat_id,peerId);return {ok:true,json:async()=>({ok:true,result:{message_id:22,chat:{id:Number(peerId)},from:{id:Number(botId)}}})}}});assert.equal(await send(peerId,'hi'),22);await assert.rejects(send('-1009','hi'),/invalid_group_send/);assert.equal(count,1)});
test('unknown outcome is never retried by sender',async()=>{let count=0;const send=createGroupBotSender({token,peerId,botId,fetcher:async()=>{count++;throw Error('network')}});await assert.rejects(send(peerId,'hi'),/unknown/);assert.equal(count,1)});
test('group delivery preserves explicit hyperlink entities',async()=>{let args;const send=createGroupBotSender({token,peerId,botId,fetcher:async(_u,o)=>{args=JSON.parse(o.body);return {ok:true,json:async()=>({ok:true,result:{message_id:22,chat:{id:Number(peerId)},from:{id:Number(botId)},entities:args.entities}})}}});await send(peerId,'Сайт',null,[{type:'text_url',offset:0,length:4,url:'https://example.org/'}]);assert.deepEqual(args.entities,[{type:'text_link',offset:0,length:4,url:'https://example.org/'}]);});
test('a provider receipt without the promised link stays unknown and is not retried',async()=>{let count=0;const send=createGroupBotSender({token,peerId,botId,fetcher:async()=>{count++;return {ok:true,json:async()=>({ok:true,result:{message_id:22,chat:{id:Number(peerId)},from:{id:Number(botId)},entities:[]}})}}});await assert.rejects(send(peerId,'Сайт',null,[{type:'text_url',offset:0,length:4,url:'https://example.org/'}]),/unknown/);assert.equal(count,1)});
test('group image uses one provider request and returns its verified receipt',async()=>{
 const bytes=Buffer.from([137,80,78,71,13,10,26,10,1]);const sha256=createHash('sha256').update(bytes).digest('hex');const media=[{filename:'dot-1-'+sha256.slice(0,16)+'.png',bytes,sha256,mime:'image/png'}];
 let count=0;const send=createGroupBotMediaSender({token,peerId,botId,fetcher:async(url,opts)=>{count++;assert.match(url,/\/sendPhoto$/);assert.equal(opts.body.get('chat_id'),peerId);assert.equal(opts.body.get('caption'),'Фото');return {ok:true,json:async()=>({ok:true,result:{message_id:22,chat:{id:Number(peerId)},from:{id:Number(botId)}}})}}});
 assert.deepEqual(await send(peerId,media,'Фото',null),['22']);assert.equal(count,1);
});
test('album delivery verifies every receipt and never retries an ambiguous outcome',async()=>{
 const bytes=Buffer.from([137,80,78,71,13,10,26,10,1]);const sha256=createHash('sha256').update(bytes).digest('hex');
 const media=[1,2].map(i=>({filename:'dot-'+i+'-'+sha256.slice(0,16)+'.png',bytes,sha256,mime:'image/png'}));let count=0;
 const send=createGroupBotMediaSender({token,peerId,botId,fetcher:async(url,opts)=>{count++;assert.match(url,/\/sendMediaGroup$/);assert.equal(JSON.parse(opts.body.get('media')).length,2);return {ok:true,json:async()=>({ok:true,result:[{message_id:22,chat:{id:Number(peerId)},from:{id:Number(botId)}}]})}}});
 await assert.rejects(send(peerId,media,'Фото'),/unknown/);assert.equal(count,1);
 await assert.rejects(send('-1009',media,'Фото'),/invalid/);assert.equal(count,1);
});
test('image caption keeps clickable link and verifies Telegram returned it',async()=>{
 const bytes=Buffer.from([137,80,78,71,13,10,26,10,1]),sha256=createHash('sha256').update(bytes).digest('hex');const media=[{filename:'dot-1-'+sha256.slice(0,16)+'.png',bytes,sha256,mime:'image/png'}];let count=0;
 const send=createGroupBotMediaSender({token,peerId,botId,fetcher:async(_url,opts)=>{count++;const entities=JSON.parse(opts.body.get('caption_entities'));assert.deepEqual(entities,[{type:'text_link',offset:0,length:4,url:'https://example.org/'}]);return {ok:true,json:async()=>({ok:true,result:{message_id:22,chat:{id:Number(peerId)},from:{id:Number(botId)},caption_entities:entities}})}}});
 assert.deepEqual(await send(peerId,media,'Сайт',null,[{type:'text_url',offset:0,length:4,url:'https://example.org/'}]),['22']);assert.equal(count,1);
});
