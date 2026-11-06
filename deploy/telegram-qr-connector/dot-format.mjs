// Convert normalized, UTF-16 source ranges to the installed GramJS types.
export function telegramEntities(text,entities=[],api){
 if(!Array.isArray(entities))throw Error('invalid_format_entities');
 const kinds={bold:'MessageEntityBold',italic:'MessageEntityItalic',strike:'MessageEntityStrike',underline:'MessageEntityUnderline',code:'MessageEntityCode',pre:'MessageEntityPre',text_url:'MessageEntityTextUrl'};
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
 return expected.every(e=>actual.some(a=>a.className===e.className&&a.offset===e.offset&&a.length===e.length&&(!e.url||a.url===e.url)));
}
