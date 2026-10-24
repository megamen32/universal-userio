// Fast unit: a username resolves through the entity lookup, a phone number is
// imported before Telegram can resolve it, and nothing is ever sent.
import assert from 'node:assert/strict';import test from 'node:test';
import {resolveTelegramContact} from './contact-resolve.mjs';

class ImportContacts {constructor(p){Object.assign(this,p)}}
class ResolvePhone {constructor(p){Object.assign(this,p)}}
class ResolveUsername {constructor(p){Object.assign(this,p)}}
const Api={contacts:{ImportContacts,ResolvePhone,ResolveUsername}};

test('a username resolves through the entity lookup and sends nothing',async()=>{
  const calls=[];
  const client={getEntity:async()=>{throw Error('must preserve native error')},
    invoke:async req=>{calls.push([req.constructor.name,req.username]);
      return{peer:{userId:42},users:[{id:42,username:'vampsupport_vpn',firstName:'Vamp'}]}}};
  const entity=await resolveTelegramContact(client,'@vampsupport_vpn',Api);
  assert.equal(entity.id,42);
  assert.deepEqual(calls,[['ResolveUsername','vampsupport_vpn']]);
});

test('a phone number is imported before Telegram resolves it',async()=>{
  const invoked=[];
  const client={getEntity:async()=>{throw Error('a phone must not use the entity lookup')},
    invoke:async req=>{invoked.push(req.constructor.name);
      if(req.constructor.name==='ResolvePhone')return {contacts:{found:[{users:[{id:7,firstName:'Ivan'}]}]}};
      return {}}};
  const entity=await resolveTelegramContact(client,'+7 (926) 78-178-74',Api);
  assert.equal(entity.id,7);
  assert.deepEqual(invoked,['ImportContacts','ResolvePhone']);
});

test('an explicitly unoccupied username preserves native proof',async()=>{
  const client={invoke:async()=>{throw Object.assign(Error('private'),{errorMessage:'USERNAME_NOT_OCCUPIED'})}};
  await assert.rejects(()=>resolveTelegramContact(client,'nobody_here',Api),
    error=>JSON.parse(error.message).code==='USERNAME_NOT_OCCUPIED');
});

test('an empty identifier resolves to nothing without touching the client',async()=>{
  const client={getEntity:async()=>{throw Error('must not be called')}};
  assert.equal(await resolveTelegramContact(client,'   ',Api),null);
});

test('native flood error keeps its typed deadline without revealing input',async()=>{
  class FloodWaitError extends Error {constructor(){super('private username');this.errorMessage='FLOOD';this.code=420;this.seconds=900;}}
  const client={invoke:async()=>{throw new FloodWaitError()}};
  await assert.rejects(()=>resolveTelegramContact(client,'fixture_user',Api),error=>{
    const receipt=JSON.parse(error.message);
    assert.equal(receipt.schema,'userio.identity-read-error.v1');
    assert.equal(receipt.code,'FLOOD_WAIT');
    assert.equal(receipt.retry_after_seconds,900);
    assert.ok(receipt.observed_at>0);
    assert.ok(!error.message.includes('fixture_user')&&!error.message.includes('private'));
    return true;
  });
});

test('unknown native and transport failures never become unoccupied source proof',async()=>{
  for (const [error,code] of [[Error('private input'),'UNKNOWN_NATIVE_ERROR'],
    [Object.assign(Error('private input'),{code:'ETIMEDOUT'}),'ETIMEDOUT']]) {
    const client={invoke:async()=>{throw error}};
    await assert.rejects(()=>resolveTelegramContact(client,'fixture_user',Api),value=>{
      const receipt=JSON.parse(value.message);
      assert.equal(receipt.code,code);
      assert.equal(receipt.retry_after_seconds,null);
      assert.ok(!value.message.includes('private'));
      return true;
    });
  }
});
