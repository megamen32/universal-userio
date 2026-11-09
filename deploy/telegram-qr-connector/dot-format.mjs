// Convert normalized, UTF-16 source ranges to the installed GramJS types.
export function telegramEntities(text,entities=[],api){
 if(!Array.isArray(entities))throw Error('invalid_format_entities');
 const kinds={bold:'MessageEntityBold',italic:'MessageEntityItalic',strike:'MessageEntityStrike',underline:'MessageEntityUnderline',code:'MessageEntityCode',pre:'MessageEntityPre',blockquote:'MessageEntityBlockquote',text_url:'MessageEntityTextUrl'};
 return entities.map(e=>{
  if(!e||!Object.hasOwn(kinds,e.type)||!Number.isSafeInteger(e.offset)||!Number.isSafeInteger(e.length)||e.offset<0||e.length<1||e.offset+e.length>text.length||typeof api?.[kinds[e.type]]!=='function')throw Error('invalid_format_entity');
  const value={offset:e.offset,length:e.length};
  if(e.type==='text_url'){let u;try{u=new URL(e.url)}catch{throw Error('invalid_format_url')};if(!['http:','https:'].includes(u.protocol))throw Error('invalid_format_url');value.url=u.href;}
  if(e.type==='pre')value.language='';
  return new api[kinds[e.type]](value);
 });
}
export function telegramFormatMatches(expected,actual){
 if(!Array.isArray(expected)||!Array.isArray(actual))return !expected?.length;
 const automatic=new Set(['MessageEntityUrl','MessageEntityEmail','MessageEntityMention','MessageEntityHashtag','MessageEntityBotCommand','MessageEntityCashtag','MessageEntityPhone']);
 const identity=e=>JSON.stringify({type:e.className,offset:e.offset,length:e.length,...(e.url?{url:e.url}:{}),...(e.language?{language:e.language}:{})});
 const want=expected.map(identity).sort(),got=actual.filter(e=>!automatic.has(e.className)).map(identity).sort();
 return want.length===got.length&&want.every((value,index)=>value===got[index]);
}
