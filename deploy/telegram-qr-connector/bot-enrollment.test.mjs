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
