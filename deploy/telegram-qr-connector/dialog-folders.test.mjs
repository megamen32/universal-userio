// Focused metadata integration: exact live account + private folder rule fields, expected1s/max5s.
import assert from 'node:assert/strict';import test from 'node:test';
import {readDialogFolders} from './dialog-folders.mjs';
class GetDialogFilters {}
const Api={messages:{GetDialogFilters}};
test('reads exact existing client folder metadata without exposing access hashes',async()=>{
 const calls=[]; const client={getMe:async()=>({id:540}),getPeerId:async p=>String(p.id),invoke:async r=>{
  calls.push(r);return {filters:[{id:15,title:{text:'Работа ТГ'},includePeers:[{id:'-1007',accessHash:'PRIVATE'}],pinnedPeers:[{id:'188'}],excludePeers:[{id:'999'}],groups:false}]};}};
 const result=await readDialogFolders({slots:new Map([['one',{accountId:'telegram:540',client}]]),Api,accountId:'telegram:540',folderIds:[15]});
 assert.equal(calls.length,1);assert.ok(calls[0] instanceof GetDialogFilters);
 assert.deepEqual(result.folders[0].include_peer_ids,['-1007']);
 assert.deepEqual(result.folders[0].pinned_peer_ids,['188']);assert.deepEqual(result.folders[0].exclude_peer_ids,['999']);
 assert.equal(result.folders[0].categories.groups,false);assert.equal(result.account_id,'telegram:540');
 assert.ok(!JSON.stringify(result).includes('PRIVATE'));
});
test('missing/wrong connected identity refuses before folder RPC',async()=>{
 let called=0;const client={getMe:async()=>({id:1}),invoke:async()=>{called++;}};
 const slots=new Map([['one',{accountId:'telegram:540',client}]]);
 await assert.rejects(readDialogFolders({slots,Api,accountId:'telegram:999',folderIds:[15]}));
 await assert.rejects(readDialogFolders({slots,Api,accountId:'telegram:540',folderIds:[15]}));
 assert.equal(called,0);
});
test('resolves only explicit nonexcluded peers through same client and emits identity metadata',async()=>{
 let batch;
 const client={getMe:async()=>({id:540}),getPeerId:async p=>String(p.id),
 invoke:async()=>({filters:[{id:15,title:{text:'Work'},includePeers:[{id:'188'},{id:'999'}],pinnedPeers:[],excludePeers:[{id:'999'}]}]}),
 getEntity:async peers=>{batch=peers;return [{firstName:'Олег',lastName:'Карпов',phone:'PRIVATE',accessHash:'PRIVATE'}];}};
 const result=await readDialogFolders({slots:new Map([['one',{accountId:'telegram:540',client}]]),Api,accountId:'telegram:540',folderIds:[15],resolve:true});
 assert.deepEqual(batch,[{id:'188'}]);assert.deepEqual(result.dialogs,[{peer_id:'188',name:'Олег Карпов',conversation_kind:'direct'}]);
 assert.ok(!JSON.stringify(result).includes('PRIVATE'));
});
