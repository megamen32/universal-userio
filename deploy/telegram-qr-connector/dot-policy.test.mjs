import './dot-test-policy.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,writeFileSync,chmodSync,symlinkSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {readDotPolicy,DOT_POLICY} from './dot-policy.mjs';
test('protected policy accepts only the configured scope and safe transport syntax',()=>{
 const dir=mkdtempSync(join(tmpdir(),'dot-policy-verify-')),file=join(dir,'policy.json');
 try{
  const put=p=>writeFileSync(file,JSON.stringify(p),{mode:0o600});
  put(DOT_POLICY);assert.deepEqual(readDotPolicy(file),DOT_POLICY);
  // A migrated, already bound bot needs no BotFather username.
  // Enrollment remains disabled when that identity is unverified.
  const {sourceUsername,...withoutEnrollment}=DOT_POLICY;
  put(withoutEnrollment);assert.deepEqual(readDotPolicy(file),withoutEnrollment);
  put({...withoutEnrollment,sourceUsername:''});assert.equal(readDotPolicy(file),null);
  put(DOT_POLICY);
  for(const patch of [{owner:'bad'},{room:'wrong'},{sshTarget:'user@host;id'},{pythonPath:'/usr/bin/python3;id'},{mcpScriptPath:'/tmp/../private'},{mcpEndpoint:'http://example.invalid/mcp'},{browserUrl:'https://example.invalid/dots/test'},{unknown:true}]){
   put({...DOT_POLICY,...patch});assert.equal(readDotPolicy(file),null);
  }
  put(DOT_POLICY);chmodSync(file,0o644);assert.equal(readDotPolicy(file),null);
  chmodSync(file,0o600);assert.equal(readDotPolicy(file,{uid:process.getuid()+1}),null);
  symlinkSync(file,join(dir,'link'));assert.equal(readDotPolicy(join(dir,'link')),null);
  assert.equal(readDotPolicy(join(dir,'missing')),null);
 }finally{rmSync(dir,{recursive:true,force:true});}
});
