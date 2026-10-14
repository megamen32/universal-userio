"""Bounded read-only Xiva client, matching Telemost web213.7.0's wire codec.

Credentials enter via stdin. This client never sends seen markers, subscriptions
to individual messages, typing, or provider mutations. History is read-only.
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


async def history(credentials, *, chat_id='', before=0, limit=20):
    import aiohttp
    uid = str(credentials['uid'])
    if not uid.isascii() or not uid.isdigit():
        raise ValueError('invalid Yandex account')
    if not 1 <= limit <= 50 or type(before) is not int or before < 0:
        raise ValueError('invalid history bounds')
    if len(chat_id) > 200:
        raise ValueError('invalid chat identity')
    cookie = '; '.join(k+'='+v for k,v in credentials['cookies'].items())
    if '\r' in cookie or '\n' in cookie or len(cookie) > 32000:
        raise ValueError('invalid session')
    query = urlencode({'service':'messenger-prod:version5*common+version5*main',
                       'session':uuid.uuid4().hex, 'client':'web_main', 'user':uid})
    timeout = aiohttp.ClientTimeout(total=25, connect=5)
    async with aiohttp.ClientSession(timeout=timeout, headers={
        'Cookie':cookie, 'Origin':'https://telemost.yandex.ru'}) as http:
        async with http.ws_connect('wss://push.yandex.ru/v2/subscribe/websocket?'+query,
                                   max_msg_size=MAX_BYTES) as ws:
            subscribed = False
            for _ in range(16):
                msg = await ws.receive(timeout=8)
                if msg.type != aiohttp.WSMsgType.TEXT:
                    raise RuntimeError('Yandex subscription refused')
                operation = json.loads(msg.data).get('operation')
                if operation == 'subscribed':
                    subscribed = True
                    break
                if operation == 'xivaws-error':
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
            params = {'Limit':limit, 'ChatDataFilter':{}}
            if chat_id:
                params['ChatId'] = chat_id
            if before:
                params['MaxTimestamp'] = before
            result = await request(2, 'history', params)
            return {'user':user, 'chats':result.get('Chats', [])}


def main():
    try:
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise ValueError('request too large')
        request = json.loads(raw)
        result = asyncio.run(asyncio.wait_for(history(request['credentials'],
            chat_id=request.get('chat_id',''), before=request.get('before',0),
            limit=request.get('limit',20)), 30))
        output = json.dumps(result, ensure_ascii=False).encode()
        if len(output) > MAX_BYTES:
            raise ValueError('history too large')
        sys.stdout.buffer.write(output)
    except Exception as error:
        print(json.dumps({'error':type(error).__name__}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
