import test from 'node:test';import assert from 'node:assert/strict';import {mkdtempSync,readFileSync,statSync,rmSync} from 'node:fs';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {groupSettings,chooseGroupReply,groupTopicId,readGroupSettings,writeGroupSettings,observeGroupMessage,resolveGroupTopicTitle,accessGroupSettings} from './dot-group-settings.mjs';
test('group settings default off and reject ambiguous values',()=>{
 assert.deepEqual(groupSettings({}),{showTopic:false,chainPrevious:false});
 assert.deepEqual(groupSettings({showTopic:true,chainPrevious:true}),{showTopic:true,chainPrevious:true});
 assert.throws(()=>groupSettings({showTopic:'false'}),/invalid/);
});
test('explicit replies take precedence; automatic reply stays inside the topic',()=>{
 const previous={messageId:'17',topicId:'4'};
 assert.equal(chooseGroupReply({explicit:'9',previous,topicId:'4',enabled:true}),'9');
 assert.equal(chooseGroupReply({explicit:null,previous,topicId:'4',enabled:true}),'17');
 assert.equal(chooseGroupReply({explicit:null,previous,topicId:'5',enabled:true}),null);
 assert.equal(chooseGroupReply({explicit:null,previous,topicId:'4',enabled:false}),null);
});
test('forum topic id follows the provider reply header',()=>{
 assert.equal(groupTopicId({replyTo:{forumTopic:true,replyToTopId:7,replyToMsgId:8}},true),'7');
 assert.equal(groupTopicId({replyTo:{forumTopic:true,replyToMsgId:9}},true),'9');
 assert.equal(groupTopicId({},true),'1');assert.equal(groupTopicId({},false),null);
});
test('settings are durable, bound to the exact group and protected against stale writes',()=>{
 const dir=mkdtempSync(join(tmpdir(),'dot-settings-')),path=join(dir,'settings.json'),binding={owner:'user_123',botAccountId:'telegram:123',peerId:'-10055'};
 assert.deepEqual(readGroupSettings(path,binding),{revision:0,showTopic:false,chainPrevious:false});
 assert.deepEqual(writeGroupSettings(path,binding,0,{showTopic:true,chainPrevious:false}),{revision:1,showTopic:true,chainPrevious:false});
 assert.equal(statSync(path).mode&0o777,0o600);assert.match(readFileSync(path,'utf8'),/"revision":1/);
 assert.throws(()=>writeGroupSettings(path,binding,0,{showTopic:false,chainPrevious:false}),/revision/);
 assert.throws(()=>readGroupSettings(path,{...binding,peerId:'-10066'}),/binding/);
 rmSync(dir,{recursive:true});
});
test('latest observed message is monotonic and scoped per topic',()=>{
 const state={};assert.equal(observeGroupMessage(state,{messageId:'11',topicId:'4'}),true);
 assert.equal(observeGroupMessage(state,{messageId:'13',topicId:'5'}),true);
 assert.equal(observeGroupMessage(state,{messageId:'12',topicId:'4'}),true);
 assert.equal(state.groupContext.currentTopic,'5');assert.equal(state.groupContext.lastByTopic['4'],'12');
 assert.equal(observeGroupMessage(state,{messageId:'10',topicId:'4'}),false);assert.equal(state.groupContext.lastByTopic['4'],'12');
});
test('topic names come from the exact provider topic and stay unknown when unavailable',async()=>{
 class Request{constructor(v){Object.assign(this,v)}}
 const api={channels:{GetForumTopicsByID:Request}},client={getInputEntity:async()=>({channel:'fixture'}),invoke:async req=>({topics:[{id:8,title:'Другой'},{id:7,title:'Проект А'}]})};
 assert.equal(await resolveGroupTopicTitle({client,api,peer:'group',topicId:'7',getInputChannel:x=>x}),'Проект А');
 assert.equal(await resolveGroupTopicTitle({client:{...client,invoke:async()=>{throw Error('offline')}},api,peer:'group',topicId:'7',getInputChannel:x=>x}),null);
});
test('only the personal owner can read or update group settings',()=>{
 const dir=mkdtempSync(join(tmpdir(),'dot-settings-auth-')),path=join(dir,'settings.json'),binding={owner:'user_123',botAccountId:'telegram:123',peerId:'-10055'};
 assert.throws(()=>accessGroupSettings({path,binding,principal:{user_id:'user_other',can_manage:true},method:'GET'}),/forbidden/);
 assert.throws(()=>accessGroupSettings({path,binding,principal:{user_id:'user_123',can_manage:false},method:'POST',input:{revision:0,showTopic:true,chainPrevious:false}}),/forbidden/);
 assert.equal(accessGroupSettings({path,binding,principal:{user_id:'user_123',can_manage:true},method:'POST',input:{revision:0,showTopic:true,chainPrevious:false}}).showTopic,true);
 rmSync(dir,{recursive:true});
});
