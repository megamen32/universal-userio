// Fast unit: a username resolves through the entity lookup, a phone number is
// imported before Telegram can resolve it, and nothing is ever sent.
import assert from 'node:assert/strict';import test from 'node:test';
import {resolveTelegramContact} from './contact-resolve.mjs';

class ImportContacts {constructor(p){Object.assign(this,p)}}
class ResolvePhone {constructor(p){Object.assign(this,p)}}
const Api={contacts:{ImportContacts,ResolvePhone}};

test('a username resolves through the entity lookup and sends nothing',async()=>{
  const calls=[];
  const client={getEntity:async v=>{calls.push(v);return{id:42,username:'vampsupport_vpn',firstName:'Vamp'}},
    invoke:async req=>{calls.push(req.constructor.name);throw Error('unexpected invoke')}};
  const entity=await resolveTelegramContact(client,'@vampsupport_vpn',Api);
  assert.equal(entity.id,42);
  assert.deepEqual(calls,['vampsupport_vpn']);
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

test('an unknown username resolves to nothing',async()=>{
  const client={getEntity:async()=>{throw Error('not found')}};
  assert.equal(await resolveTelegramContact(client,'nobody_here',Api),null);
});

test('an empty identifier resolves to nothing without touching the client',async()=>{
  const client={getEntity:async()=>{throw Error('must not be called')}};
  assert.equal(await resolveTelegramContact(client,'   ',Api),null);
});
