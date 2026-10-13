import {pathToFileURL} from 'node:url';
export async function resolve(specifier,context,next){
 if(specifier==='@whiskeysockets/baileys')return {url:new URL('./wa-mock-sdk.mjs',import.meta.url).href,shortCircuit:true};
 if(specifier==='qrcode')return {url:new URL('./wa-mock-qr.mjs',import.meta.url).href,shortCircuit:true};
 return next(specifier,context);
}
