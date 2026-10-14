"""Bounded Xiva client, matching Telemost web213.7.0's wire codec.

Credentials enter via stdin. History never sends seen markers or typing. Text
delivery is a separate explicit operation after UserIO's durable send claim.
"""
from __future__ import annotations
import asyncio
import json
import struct
import sys
import uuid
from urllib.parse import urlencode

MAX_BYTES = 4 * 1024 * 1024


def _pack(value):
    if isinstance(value, int):
        if 0 <= value < 128:
            return bytes([value])
        if 0 <= value < 65536:
            return b'\xcd' + struct.pack('!H', value)
        raise ValueError('invalid request number')
    raw = value.encode('ascii')
    if len(raw) > 31:
        raise ValueError('invalid operation')
    return bytes([160 + len(raw)]) + raw


def packet(request_id, operation, payload):
    return (b'\x01\x93' + _pack(0) + _pack(request_id) + _pack(operation)
            + b'\x05' + b'\0' * 11 + json.dumps(payload).encode())


def decode(raw):
    """Decode the exact Xiva type/header plus Messenger's12-byte frame."""
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError('invalid provider packet size')
    kind, offset = raw[0], 1
    count = 3 if kind == 1 else 2 if kind == 2 else 4 if kind == 3 else 0
    if not count or raw[offset] != 0x90 + count:
        raise ValueError('invalid provider packet header')
    offset += 1
    header = []
    for _ in range(count):
        tag = raw[offset]
        offset += 1
        if tag < 128:
            value = tag
        elif tag in (204, 205, 206):
            size = {204:1, 205:2, 206:4}[tag]
            value = int.from_bytes(raw[offset:offset+size], 'big')
            offset += size
        elif 160 <= tag < 192 or tag in (217,218,219):
            if tag < 192:
                size = tag - 160
            else:
                length_bytes = {217:1,218:2,219:4}[tag]
                size = int.from_bytes(raw[offset:offset+length_bytes], 'big')
                offset += length_bytes
            value = raw[offset:offset+size].decode()
            offset += size
        else:
            raise ValueError('unsupported provider header')
        if offset > len(raw):
            raise ValueError('truncated provider header')
        header.append(value)
    if kind != 1:
        return kind, header, None
    if raw[offset:offset+1] != b'\x05' or len(raw) < offset+12:
        raise ValueError('invalid Messenger frame')
    return kind, header, json.loads(raw[offset+12:])


async def history(credentials, *, chat_id='', before=0, limit=20,
                  operation='history', text='', payload_id='', target_guid='', trace=None):
    import aiohttp
    trace = {} if trace is None else trace
    trace.update(stage='identity_read',message_submitted=False)
    uid = str(credentials['uid'])
    if not uid.isascii() or not uid.isdigit():
        raise ValueError('invalid Yandex account')
    if not 1 <= limit <= 50 or type(before) is not int or before < 0:
        raise ValueError('invalid history bounds')
    if len(chat_id) > 200:
        raise ValueError('invalid chat identity')
    if operation not in {'history','resolve','send'}:
        raise ValueError('unsupported Yandex operation')
    if operation=='send' and (not text.strip() or len(text)>4096 or
                             str(uuid.UUID(payload_id))!=payload_id):
        raise ValueError('invalid Yandex message')
    if operation=='resolve' and str(uuid.UUID(target_guid))!=target_guid:
        raise ValueError('invalid Yandex recipient')
    cookie = '; '.join(k+'='+v for k,v in credentials['cookies'].items())
    if '\r' in cookie or '\n' in cookie or len(cookie) > 32000:
        raise ValueError('invalid session')
    query = urlencode({'service':'messenger-prod:version5*common+version5*main',
                       'session':uuid.uuid4().hex, 'client':'web_main', 'user':uid})
    timeout = aiohttp.ClientTimeout(total=35, connect=5)
    async with aiohttp.ClientSession(timeout=timeout, headers={
        'Cookie':cookie, 'Origin':'https://telemost.yandex.ru'}) as http:
        async with http.ws_connect('wss://push.yandex.ru/v2/subscribe/websocket?'+query,
                                   max_msg_size=MAX_BYTES) as ws:
            subscribed = False
            for _ in range(16):
                msg = await ws.receive(timeout=8)
                if msg.type != aiohttp.WSMsgType.TEXT:
                    raise RuntimeError('Yandex subscription refused')
                event_operation = json.loads(msg.data).get('operation')
                if event_operation == 'subscribed':
                    subscribed = True
                    break
                if event_operation == 'xivaws-error':
                    raise RuntimeError('Yandex authentication required')
            if not subscribed:
                raise RuntimeError('Yandex subscription unavailable')
            async def request(number, name, params):
                await ws.send_bytes(packet(number, name,
                    dict(params, RequestId=uuid.uuid4().hex)))
                for _ in range(64):
                    msg = await ws.receive(timeout=8)
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        if json.loads(msg.data).get('operation') == 'xivaws-error':
                            raise RuntimeError('Yandex authentication required')
                        continue
                    if msg.type != aiohttp.WSMsgType.BINARY:
                        raise RuntimeError('Yandex connection closed')
                    kind, header, body = decode(msg.data)
                    if kind == 2 and header[0] == number:
                        raise RuntimeError('Yandex history refused')
                    if kind == 1 and header[1] == number:
                        return body
                raise RuntimeError('Yandex history missing')
            who = await request(1, 'whoami', {})
            user = who.get('UserInfo', {})
            if str(user.get('Uid')) != uid or str(user.get('Guid')) != credentials['guid']:
                raise PermissionError('Yandex session identity changed')
            async def registry(method, params):
                trace['stage']='registry_'+method
                url='https://yandex.ru/messenger/api/registry/'
                async with http.post(url+'csrf-token/',data=b'') as response:
                    if response.status!=200:raise RuntimeError('Yandex authentication required')
                    token=(await response.json())['token']
                form=aiohttp.FormData()
                form.add_field('request',json.dumps({'method':method,'params':params}),
                               content_type='application/json')
                async with http.post(url+'api/',data=form,headers={
                    'X-CSRF-TOKEN':token,'X-Origin-Service-ID':'27','X-Uid':uid}) as response:
                    if response.status!=200:raise RuntimeError('Yandex recipient unavailable')
                    return (await response.json())['data']
            if operation=='resolve':
                people=(await registry('get_users_data',{'guids':[target_guid]})).get('users',[])
                person=next((p for p in people if p.get('guid')==target_guid),None)
                if not person:raise RuntimeError('Yandex recipient not found')
                return {'person':person,'peer_id':'_'.join(sorted([credentials['guid'],target_guid]))}
            if operation=='send':
                parts=chat_id.split('_')
                if len(parts)==2:
                    if credentials['guid'] not in parts:
                        raise PermissionError('private chat belongs to another sender')
                    target=parts[0] if parts[1]==credentials['guid'] else parts[1]
                    created=await registry('create_private_chat',{'guid':target,'wait_for_update':True})
                    if created.get('chat',{}).get('chat_id')!=chat_id:
                        raise PermissionError('Yandex private destination changed')
                trace.update(stage='push',message_submitted=True)
                ack=await request(2,'push',{'ClientMessage':{'Plain':{
                    'ChatId':chat_id,'PayloadId':payload_id,'Text':{'MessageText':text}}},
                    'Meta':{'Origin':27}})
                timestamp=ack.get('MessageInfo',{}).get('TimestampMcs')
                if ack.get('Status') not in (1,2) or type(timestamp) is not int or timestamp<=0:
                    raise RuntimeError('Yandex send result unknown')
                trace['stage']='delivery_readback'
                page=await request(3,'history',{'ChatId':chat_id,
                    'MaxTimestamp':timestamp+1,'Limit':1,'ChatDataFilter':{}})
                for chat in page.get('Chats',[]):
                    if chat['ChatId']!=chat_id:continue
                    for entry in chat.get('Messages',[]):
                        server=entry.get('ServerMessage',{});info=server.get('ServerMessageInfo',{})
                        plain=server.get('ClientMessage',{}).get('Plain',{})
                        if (info.get('Timestamp')==timestamp and info.get('From',{}).get('Guid')==credentials['guid']
                            and plain.get('PayloadId')==payload_id and plain.get('Text',{}).get('MessageText')==text):
                            return {'receipt':str(timestamp),'chat':chat}
                raise RuntimeError('Yandex delivery readback unavailable')
            params = {'Limit':limit, 'ChatDataFilter':{}}
            if chat_id:
                params['ChatId'] = chat_id
            if before:
                params['MaxTimestamp'] = before
            result = await request(2, 'history', params)
            return {'user':user, 'chats':result.get('Chats', [])}


def main():
    trace={}
    try:
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise ValueError('request too large')
        request = json.loads(raw)
        result = asyncio.run(asyncio.wait_for(history(request['credentials'],
            chat_id=request.get('chat_id',''), before=request.get('before',0),
            limit=request.get('limit',20),operation=request.get('operation','history'),
            text=request.get('text',''),payload_id=request.get('payload_id',''),
            target_guid=request.get('target_guid',''),trace=trace), 40))
        output = json.dumps(result, ensure_ascii=False).encode()
        if len(output) > MAX_BYTES:
            raise ValueError('history too large')
        sys.stdout.buffer.write(output)
    except Exception as error:
        print(json.dumps({'error':type(error).__name__,**trace}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
