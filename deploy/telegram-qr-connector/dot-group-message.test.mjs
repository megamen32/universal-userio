import test from 'node:test';
import assert from 'node:assert/strict';
import {groupDotText,groupDotEntities,groupMediaKind} from './dot-group-message.mjs';

test('group message carries sender first and last name without changing content',()=>{
 assert.equal(groupDotText({firstName:'Никита',lastName:'Разанов',id:42},{message:'Здравствуйте'}),'Никита Разанов:\nЗдравствуйте');
 assert.equal(groupDotText({firstName:'Оля',lastName:'Карпова',id:43},{message:'Привет'}),'Оля Карпова:\nПривет');
});
test('Telegram formatted link remains bound to the correct text after sender prefix',()=>{
 const sender={firstName:'Оля',lastName:'Карпова',id:43};
 const message={message:'Наш сайт',entities:[{className:'MessageEntityTextUrl',offset:4,length:4,url:'https://example.org/'}]};
 assert.deepEqual(groupDotEntities(sender,message),[{type:'text_url',offset:'Оля Карпова:\n'.length+4,length:4,url:'https://example.org/'}]);
});
test('unsupported provider formatting remains explicit instead of throwing away the message',()=>{
 const sender={firstName:'Оля',id:43},message={message:'секрет',entities:[{className:'MessageEntitySpoiler',offset:0,length:6}]};
 assert.deepEqual(groupDotEntities(sender,message),[{type:'unsupported',provider_type:'MessageEntitySpoiler',offset:5,length:6}]);
});
test('a link preview is ordinary text, while pictures and other files are distinct',()=>{
 assert.equal(groupMediaKind({message:'ссылка',media:{className:'MessageMediaWebPage'}}),'text');
 assert.equal(groupMediaKind({message:'',media:{className:'MessageMediaPhoto',photo:{}}}),'image');
 assert.equal(groupMediaKind({message:'',media:{className:'MessageMediaDocument',document:{mimeType:'application/pdf'}}}),'document');
});
test('forward provenance is visible and unavailable names are not invented',()=>{
 assert.equal(groupDotText({firstName:'Оля',id:43},{message:'Текст',fwdFrom:{fromName:'Иван'}}),'Оля:\n[Переслано от: Иван]\nТекст');
 assert.equal(groupDotText({id:43},{message:'Текст',fwdFrom:{}}),'Участник Telegram 43:\n[Переслано; источник недоступен]\nТекст');
});
test('saved and numeric Telegram forward sources use the existing origin contract',()=>{
 const sender={firstName:'Оля',id:43};
 assert.match(groupDotText(sender,{message:'Текст',fwdFrom:{savedFromName:'Иван'}}),/Сохранено от: Иван/);
 assert.match(groupDotText(sender,{message:'Текст',fwdFrom:{fromId:{channelId:123}}}),/Переслано от: источник 123/);
});
