import {mkdtempSync,writeFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
const directory=mkdtempSync(join(tmpdir(),'dot-policy-test-'));
const file=join(directory,'policy.json');
writeFileSync(file,JSON.stringify({
 owner:'111111111',room:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
 sourceAccountId:'telegram:222222222',sourceUsername:'test_operator',
 browserUrl:'https://chatgpt.com/dots/11111111-1111-4111-8111-111111111111~bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
 sshTarget:'testuser@example.invalid',pythonPath:'/usr/bin/python3',mcpScriptPath:'/home/testuser/mcp/http_stdio.py',
 mcpEndpoint:'http://127.0.0.1:50260/mcp',ownerLabel:'Test owner',assistantLabel:'dots'
}),{mode:0o600});
process.env.USERIO_DOT_POLICY_PATH=file;
process.on('exit',()=>rmSync(directory,{recursive:true,force:true}));
