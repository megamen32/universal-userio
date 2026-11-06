import {createHash} from 'node:crypto';
import {MAX_IMAGES,MAX_IMAGE_BYTES,MAX_ALBUM_BYTES,imageKind} from './dot-media.mjs';
import {telegramEntities} from './dot-format.mjs';

const NAME=/^dot-([1-9][0-9]*)-([a-f0-9]{16})\.(jpg|png|webp)$/;
const DIGEST=/^[a-f0-9]{64}$/;
const ID=/^[1-9][0-9]*$/;

// Transport preparation only, not a second claim or a second Telegram client.
// Caller must use the current, authorized UserIO bot client's sendFile method.
export function verifyDotTelegramMedia(media){
 if(!Array.isArray(media)||media.length<1||media.length>MAX_IMAGES)
  throw Error('invalid_dot_media_group');
 let total=0;
 media.forEach((entry,i)=>{
  if(!entry||typeof entry.filename!=='string'||!entry.filename||entry.filename.length>255||/[\/\\\u0000-\u001f]/.test(entry.filename)||!DIGEST.test(entry.sha256)
   ||!Buffer.isBuffer(entry.bytes))throw Error('dot_media_descriptor_invalid');
  const bytes=entry.bytes;
  total+=bytes.length;
  if(!bytes.length||bytes.length>MAX_IMAGE_BYTES||total>MAX_ALBUM_BYTES)
   throw Error('dot_media_size_refused');
  const digest=createHash('sha256').update(bytes).digest('hex');
  if(digest!==entry.sha256)throw Error('dot_media_identity_changed');
  if(entry.kind==='document'){
   if(typeof entry.mime!=='string'||!/^[a-zA-Z0-9!#$&^_.+-]+\/[a-zA-Z0-9!#$&^_.+-]+$/.test(entry.mime))throw Error('dot_media_identity_changed');
  }else{
   const kind=imageKind(bytes),match=entry.filename.match(NAME);
   if(!match||Number(match[1])!==i+1||entry.filename!=='dot-'+(i+1)+'-'+digest.slice(0,16)+'.'+kind.ext||entry.mime!==kind.mime)throw Error('dot_media_identity_changed');
  }
 });
 return media.length;
}
export function dotTelegramMediaParams(media,caption,replyReceipt,CustomFile,entities=[],api=null){
 verifyDotTelegramMedia(media);
 if(typeof CustomFile!=='function')throw Error('invalid_dot_media_file_adapter');
 if(typeof caption!=='string'||caption.length>900||!caption.trim())
  throw Error('invalid_dot_media_caption');
 if(replyReceipt!=null&&(!ID.test(String(replyReceipt))||!Number.isSafeInteger(Number(replyReceipt))))
  throw Error('invalid_dot_media_reply_receipt');
 const files=media.map(entry=>new CustomFile(entry.filename,entry.bytes.length,'',entry.bytes));
 return {file:files.length===1?files[0]:files,caption,formattingEntities:telegramEntities(caption,entities,api),
  forceDocument:media.some(m=>m.kind==='document'),...(replyReceipt==null?{}:{replyTo:Number(replyReceipt)})};
}

export function dotTelegramMediaReceipts(sent,expectedCount){
 const messages=Array.isArray(sent)?sent:[sent];
 if(!Number.isSafeInteger(expectedCount)||expectedCount<1||expectedCount>MAX_IMAGES
  ||messages.length!==expectedCount)throw Error('dot_media_receipt_unknown');
 const ids=messages.map(m=>m?.id);
 if(ids.some(id=>!ID.test(String(id))||!Number.isSafeInteger(Number(id)))
  ||new Set(ids.map(String)).size!==ids.length)throw Error('dot_media_receipt_unknown');
 return ids.map(String);
}
