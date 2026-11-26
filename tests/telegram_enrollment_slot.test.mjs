import assert from 'node:assert/strict';
import {mkdtempSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import test from 'node:test';
import vm from 'node:vm';
import {EnrollmentOwners} from '../deploy/telegram-qr-connector/enrollment-owner.mjs';

test('enrollment never reuses a persisted reservation, even after worker restart', () => {
  const dir=mkdtempSync(join(tmpdir(),'userio-qr-reservations-'));
  try {
    const path=join(dir,'owners.json');
    writeFileSync(path,JSON.stringify({'account-1':'other-user','account-2':'current-user'}));
    const owners=new EnrollmentOwners(path);
    const active=new Map([['account-3',{}]]);
    const src=readFileSync(new URL('../deploy/telegram-qr-connector/server.mjs',import.meta.url),'utf8');
    const helper=src.match(/function allocateEnrollmentSlot\(\) \{[\s\S]*?\n\}/)?.[0];
    assert.ok(helper,'server must use the reservation-aware allocator');
    const allocate=vm.runInNewContext(`let nextSlot=1; ${helper}; allocateEnrollmentSlot`,{enrollmentOwners:owners,slots:active});
    assert.throws(()=>owners.bind('account-1',{user_id:'current-user',can_manage:true},{}),/another user/);
    const id=allocate();
    assert.equal(id,'account-4');
    owners.bind(id,{user_id:'current-user',can_manage:true},{});
    assert.equal(owners.owner('account-1'),'other-user');
    assert.equal(allocate(),'account-5');
  } finally {rmSync(dir,{recursive:true,force:true});}
});
