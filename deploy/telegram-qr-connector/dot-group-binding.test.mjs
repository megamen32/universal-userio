import test from 'node:test';import assert from 'node:assert/strict';import {mkdtempSync,writeFileSync,chmodSync} from 'node:fs';import {join} from 'node:path';import {tmpdir} from 'node:os';import {readGroupBinding,groupJournalPath} from './dot-group-binding.mjs';
test('binding must be private, exact and explicitly enabled',()=>{const p=join(mkdtempSync(join(tmpdir(),'binding-')),'binding.json');const b={enabled:true,botAccountId:'telegram:111111',owner:'user_123abc',peerId:'-1004352022764',url:'https://chatgpt.com/dots/a~b',room:'abc',agentId:'browser',connectionId:'airlock',accountScope:'a'.repeat(64),pageId:1};writeFileSync(p,JSON.stringify(b),{mode:0o600});assert.deepEqual(readGroupBinding(p),b);chmodSync(p,0o644);assert.throws(()=>readGroupBinding(p),/unsafe/);chmodSync(p,0o600);writeFileSync(p,JSON.stringify({...b,peerId:'-1'}));assert.throws(()=>readGroupBinding(p),/invalid/)});
test('new group route gets a distinct journal without renaming private history',()=>{
 const b={botAccountId:'telegram:111111',peerId:'-1004352022764'};
 assert.equal(groupJournalPath('/state',b),'/state/dot-relay-111111.json');
 assert.equal(groupJournalPath('/state',{...b,journalId:'group-4352022764'}),'/state/dot-relay-111111-group-4352022764.json');
 for(const journalId of ['../old','private','group-123','group-4352022764/other'])assert.throws(()=>groupJournalPath('/state',{...b,journalId}),/journal/);
});
