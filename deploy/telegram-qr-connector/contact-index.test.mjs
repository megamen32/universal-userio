import test from 'node:test';
import assert from 'node:assert/strict';
import { syncContactProfiles, peerProfile } from './contact-index.mjs';
test('all dialog metadata streams in bounded account-scoped pages without messages', async () => {
  const calls=[];
  const client={async *iterDialogs(options) {
    assert.equal(options.limit,10000);
    for(let id=1;id<=205;id++) yield {id,entity:{firstName:'Дмитрий',lastName:'Семерин',username:'semerindima'}};
  }};
  assert.equal(await syncContactProfiles(client,'telegram:123',async (path,body)=>calls.push([path,body])),205);
  assert.deepEqual(calls.map(c=>c[1].profiles.length),[100,100,5]);
  assert.ok(calls.every(c=>c[0]==='/v1/contact-profiles' && c[1].account_id==='telegram:123'));
  assert.equal(peerProfile({min:true,username:'partial'}),null);
});
