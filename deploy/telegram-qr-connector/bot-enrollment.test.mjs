import './dot-test-policy.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {redactBotFatherToken, botTokenFromMessage, assertBotIdentity, botCredentialPath} from './bot-enrollment.mjs';
const token='123456789:'+'x'.repeat(35), father={id:93372553,username:'BotFather',bot:true,verified:true};
const msg={senderId:93372553,out:false,message:'Done. '+token};
test('only verified inbound BotFather yields token',()=>{assert.equal(botTokenFromMessage(msg,father,'example_dot_bot').botId,'123456789');for(const f of [{...father,verified:false},{...father,username:'FakeBotFather'}])assert.throws(()=>botTokenFromMessage(msg,f,'example_dot_bot'));assert.throws(()=>botTokenFromMessage({...msg,out:true},father,'example_dot_bot'));});
test('requires exactly one token and a matching bot',()=>{assert.throws(()=>botTokenFromMessage({...msg,message:token+' '+token},father,'example_dot_bot'));const e=botTokenFromMessage(msg,father,'example_dot_bot');assertBotIdentity({bot:true,id:123456789,username:'example_dot_bot'},e);assert.throws(()=>assertBotIdentity({bot:true,id:987654321,username:'example_dot_bot'},e));});
test('secrets redacted and credential path owner scoped',()=>{assert.equal(redactBotFatherToken(msg.message),'Done. [BOT_TOKEN_REDACTED]');assert.notEqual(botCredentialPath('/safe','owner1','123456789'),botCredentialPath('/safe','owner2','123456789'));assert.throws(()=>botCredentialPath('/safe','','123456789'));});
test('connector bot enrollment has no raw token request and skips unsupported user-only sync',()=>{const s=readFileSync(new URL('./server.mjs',import.meta.url),'utf8');assert.ok(s.includes('/bot/enroll-from-botfather'));assert.ok(s.includes('if (!me.bot)'));assert.ok(s.includes('!self.bot'));assert.ok(s.includes('redactBotFatherToken(normalized.body)'));});

test("enrollment is restricted to the explicitly authorized source",()=>{const s=readFileSync(new URL("./server.mjs",import.meta.url),"utf8");assert.ok(s.includes('payload.source_account_id !== DOT_POLICY.sourceAccountId'));assert.ok(s.includes('payload.source_username !== DOT_POLICY.sourceUsername'));assert.ok(s.includes('!DOT_POLICY?.sourceUsername'));assert.ok(s.includes("registration_incomplete"));});

test('explicit bot enrollment validates exactly one token and username before connecting',async()=>{
 const {botTokenFromInput}=await import('./bot-enrollment.mjs');
 assert.deepEqual(botTokenFromInput({token,username:'AirlockDotsBot'}),{token,botId:'123456789',username:'AirlockDotsBot'});
 for(const bad of [{token:'wrong',username:'AirlockDotsBot'},{token:token+' junk',username:'AirlockDotsBot'},{token,username:'not-a-bot'},{token,username:'AirlockDotsBot',extra:true}])assert.throws(()=>botTokenFromInput(bad));
});

test('explicit enrollment binds owner and refuses duplicates and competing owners',async()=>{
 const {enrollExplicitBot}=await import('./bot-enrollment.mjs');
 const slots=new Map(),slotProfiles={},inFlight=new Set(),bound=new Map(),calls=[];
 const owners={bind:(id,p)=>bound.set(id,p.user_id),allowed:(id,user)=>bound.get(id)===user};
 const principal={user_id:'airlock',can_manage:true};
 const deps={input:{token,username:'AirlockDotsBot'},principal,headers:{},slots,slotProfiles,inFlight,owners,
  hasSavedToken:()=>false,newClient:()=>({connect:async()=>calls.push('connect'),disconnect:async()=>{},signInBot:async()=>({bot:true,id:123456789,username:'AirlockDotsBot'})}),
  credentials:()=>({}),saveToken:(owner)=>calls.push('save:'+owner),allocateSlot:()=> 'slot1',
  finishLogin:async(slot)=>slots.set(slot,{status:'connected',accountId:'telegram:123456789'})};
 assert.equal((await enrollExplicitBot(deps)).status,'connected');assert.equal(bound.get('slot1'),'airlock');
 assert.equal((await enrollExplicitBot(deps)).status,'already_registered');
 await assert.rejects(enrollExplicitBot({...deps,principal:{user_id:'other',can_manage:true}}));
 assert.deepEqual(calls,['connect','save:airlock']);
});
test('explicit enrollment never connects when credential outcome is unknown',async()=>{
 const {enrollExplicitBot}=await import('./bot-enrollment.mjs');let connected=false;
 const deps={input:{token,username:'AirlockDotsBot'},principal:{user_id:'airlock',can_manage:true},slots:new Map(),slotProfiles:{},inFlight:new Set(),owners:{},hasSavedToken:()=>true,newClient:()=>{connected=true;}};
 await assert.rejects(enrollExplicitBot(deps),/existing_credential_requires_review/);assert.equal(connected,false);
});

test('explicit enrollment rejects non-manager and mismatched provider identity',async()=>{
 const {enrollExplicitBot}=await import('./bot-enrollment.mjs');let connected=0,saved=0;
 const base={input:{token,username:'AirlockDotsBot'},principal:{user_id:'airlock',can_manage:false},slots:new Map(),slotProfiles:{},inFlight:new Set(),owners:{},hasSavedToken:()=>false,newClient:()=>{connected++;return {connect:async()=>{},disconnect:async()=>{},signInBot:async()=>({bot:true,id:123456789,username:'differentbot'})}},credentials:()=>({}),saveToken:()=>saved++};
 await assert.rejects(enrollExplicitBot(base));assert.equal(connected,0);
 await assert.rejects(enrollExplicitBot({...base,principal:{user_id:'airlock',can_manage:true}}));assert.equal(connected,1);assert.equal(saved,0);
});
test('explicit enrollment prevents a simultaneous second authentication',async()=>{
 const {enrollExplicitBot}=await import('./bot-enrollment.mjs');let release;const waiting=new Promise(r=>{release=r});let clients=0;
 const base={input:{token,username:'AirlockDotsBot'},principal:{user_id:'airlock',can_manage:true},headers:{},slots:new Map(),slotProfiles:{},inFlight:new Set(),owners:{bind(){}},hasSavedToken:()=>false,newClient:()=>{clients++;return {connect:async()=>waiting,disconnect:async()=>{},signInBot:async()=>({bot:true,id:123456789,username:'AirlockDotsBot'})}},credentials:()=>({}),saveToken:()=>{},allocateSlot:()=> 'a',finishLogin:async()=>{}};
 const first=enrollExplicitBot(base);await assert.rejects(enrollExplicitBot(base),/bot_enrollment_in_progress/);release();await first;assert.equal(clients,1);
});
