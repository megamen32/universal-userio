"""Focused integration: real loopback WS handshake ordering and slow HTTP keepalive.

Expected18/max180seconds. Detects premature ping and blocking status polls.
Run on the native RTC dependency artifact; no provider join.
"""
import asyncio,json,time,os
from pathlib import Path
from aiohttp import web
from universal_userio.telemost import transport
async def main():
    pings=[];disconnected=[];root=Path(os.environ["TELEMOST_TEST_TEMP"]);root.mkdir(parents=True,exist_ok=True)
    app=web.Application()
    async def connection(request):
        return web.json_response({'peer_id':'test-peer','room_id':'test-room','credentials':'test-only',
            'client_configuration':{'media_server_url':url+'/ws','service_name':'test','ice_servers':[]}})
    async def states(request):
        await asyncio.sleep(6)
        return web.json_response({})
    async def ws(request):
        socket=web.WebSocketResponse();await socket.prepare(request)
        hello_acknowledged=False
        try:
            async for message in socket:
                body=json.loads(message.data)
                if 'ping' in body:
                    assert hello_acknowledged, 'ping before serverHello ACK'
                    pings.append(time.monotonic())
                if 'ack' in body and body['uid']=='fixture-hello':hello_acknowledged=True
                if 'hello' in body:
                    assert body['hello']['disablePublisher'] is False
                    await socket.send_json({'uid':'fixture-hello','serverHello':{}})
        finally:disconnected.append(time.monotonic())
        return socket
    app.router.add_get('/conferences/{path}/connection',connection)
    app.router.add_post('/conferences/{path}/request-states',states)
    app.router.add_get('/ws',ws)
    runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    port=site._server.sockets[0].getsockname()[1];url=f'http://127.0.0.1:{port}'
    transport.API=url;transport.safe_media_url=lambda v:v.replace('http:','ws:')
    observed=[]
    def save():
        if record.get('server_hello_fields') is not None and not observed:observed.append(time.monotonic())
    record={'url':'https://telemost.yandex.ru/j/12345678901234','max_seconds':18,'tracks':[]}
    receiver=transport.Receiver(record,root,save,asyncio.Event());started=time.monotonic()
    try:await receiver.run()
    finally:await receiver.close();await runner.cleanup()
    elapsed=time.monotonic()-started
    assert elapsed>=18,(elapsed,record)
    assert observed and observed[0]-started<3,(observed,started,record)
    assert len(pings)>=3,(len(pings),record)
    assert record['status']=='no_audio'
    print('Websocket beyond15s PASS; muted hello/ping/lifetime/cleanup',round(elapsed,2))
def test_signaling_handshake_and_delayed_http():
    asyncio.run(main())

if __name__=="__main__":test_signaling_handshake_and_delayed_http()
