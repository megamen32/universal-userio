import test from "node:test";
import assert from "node:assert/strict";
import { withTelegramAuthorClassification } from "./author-classification.mjs";
test("preserve actual human group author despite forwarded bot origin",()=>{
 const r={kind:"telegram_routing",provider_ref:JSON.stringify({author_id:"42",forward_author_id:"93372553",mention:true})};
 const m=JSON.parse(withTelegramAuthorClassification(r,{id:"42",is_bot:false}).provider_ref);
 assert.equal(m.author_is_bot,false);assert.equal(m.mention,true);
});
test("verified direct bot and human metadata",()=>{
 for(const b of [true,false])assert.equal(JSON.parse(withTelegramAuthorClassification(null,{id:"42",is_bot:b}).provider_ref).author_is_bot,b);
});
test("unknown or different author cannot acquire human classification",()=>{
 assert.throws(()=>withTelegramAuthorClassification(null,{id:"42"}));
 assert.throws(()=>withTelegramAuthorClassification({provider_ref:'{"author_id":"99"}'},{id:"42",is_bot:false}));
});
