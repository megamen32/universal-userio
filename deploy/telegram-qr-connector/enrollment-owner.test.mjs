// Fast unit: user-specific enrollment/restart, expected1s/max5s, no Telegram RPC.
import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtempSync,rmSync,readFileSync} from 'node:fs';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {EnrollmentOwners,enrollmentPrincipal} from './enrollment-owner.mjs';
test('new users only see/complete their own slots; transient cookies not persisted',async()=>{
 const dir=mkdtempSync(join(process.env.TMPDIR||tmpdir(),'enrollment-'));
 try{
  const path=join(dir,'owners.json');const owners=new EnrollmentOwners(path);
  owners.bind('account-10',{user_id:'alice',can_manage:true},{Cookie:'fixture-alice'});
  owners.bind('account-11',{user_id:'bob',can_manage:true},{Cookie:'fixture-bob'});
  assert.equal(owners.allowed('account-10','bob'),false);
  assert.deepEqual(owners.filter([{id:'account-1'},{id:'account-10'},{id:'account-11'}],'alice'),[{id:'account-10'}]);
  assert.equal(owners.allowed('account-1','user_owner'),true);
  assert.doesNotMatch(readFileSync(path,'utf8'),/fixture-/);
  const restarted=new EnrollmentOwners(path);assert.equal(restarted.allowed('account-11','bob'),true);
  assert.throws(()=>restarted.registrationHeaders('account-11'),/expired/);
  assert.throws(()=>owners.bind('account-10',{user_id:'bob',can_manage:true},{}),/another/);
  assert.throws(()=>owners.bind('account-12',{user_id:'bob',can_manage:false},{}),/Personal/);
  const r=await enrollmentPrincipal('http://normal-userio',{cookie:'fixture-alice'},async(url,headers)=>{
   assert.equal(url.pathname,'/v1/connection-principal');assert.deepEqual(headers,{Cookie:'fixture-alice'});
   return {user_id:'alice',can_manage:true};});assert.equal(r.principal.user_id,'alice');
  owners.registered('account-10');assert.equal(owners.auth.size,1);
 }finally{rmSync(dir,{recursive:true,force:true});}
});
