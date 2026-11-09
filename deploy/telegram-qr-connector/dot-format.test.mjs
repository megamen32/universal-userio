import './dot-test-policy.mjs';
import test from 'node:test';import assert from 'node:assert/strict';
import {telegramFormatMatches} from './dot-format.mjs';

class Entity{constructor(v){Object.assign(this,v);this.className=v.className}}
const bold=()=>({className:'MessageEntityBold',offset:0,length:4});
test('an exact entity set confirms the delivered formatting',()=>{
 assert.equal(telegramFormatMatches([bold()],[bold()]),true);
});
test('a duplicated provider entity does not confirm the delivered formatting',()=>{
 assert.equal(telegramFormatMatches([bold()],[bold(),bold()]),false);
});
test('an unexpected provider formatting entity does not confirm the delivered formatting',()=>{
 assert.equal(telegramFormatMatches([bold()],[bold(),{className:'MessageEntityItalic',offset:5,length:3}]),false);
});
test('provider automatic entities do not block the confirmation',()=>{
 assert.equal(telegramFormatMatches([bold()],[bold(),{className:'MessageEntityUrl',offset:5,length:7}]),true);
});
