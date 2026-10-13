import {EventEmitter} from 'node:events';
let count=0;
export const Browsers={ubuntu:()=>['fixture']};export const DisconnectReason={loggedOut:401};
export async function fetchLatestBaileysVersion(){return {version:[2,3000,1]};}
export const fetchLatestWaWebVersion=fetchLatestBaileysVersion;
export async function useMultiFileAuthState(){return {state:{},saveCreds:()=>{}};}
export default function makeSocket(){
 const ev=new EventEmitter(),index=++count;
 const socket={ev,user:{id:(1234560+index)+':1@s.whatsapp.net'},end:()=>ev.emit('connection.update',{connection:'close',lastDisconnect:{error:{output:{statusCode:401}}}}),
  sendMessage:async(peer,msg)=>({key:{id:'fixture-sent',remoteJid:peer,fromMe:true},message:msg})};
 setTimeout(()=>ev.emit('connection.update',{qr:'fixture-qr'}),20);
 setTimeout(()=>{ev.emit('connection.update',{connection:'open'});setTimeout(()=>ev.emit('messages.upsert',{type:'notify',messages:[{key:{id:'fixture-inbound',remoteJid:'peer@s.whatsapp.net'},message:{conversation:'source'},messageTimestamp:1000}]}),50);},500);
 return socket;
}
