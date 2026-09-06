"""Matrix read ingress owned by Universal UserIO."""
from __future__ import annotations
import os, signal, threading
from .gmail_ingress import UserIOIngressClient
from .channels.matrix import MatrixMessage, MatrixReader

def poll_once(sink:UserIOIngressClient,reader:MatrixReader,*,limit:int=100)->int:
    cursor=sink.cursor('matrix'); messages,nxt=reader.poll(cursor,limit=limit)
    for m in messages:
        sink.send_message(source='matrix', account_id='matrix', route_id='userio-reply', message_id=f'{m.room_id}:{m.event_id}', sender=m.sender, body=m.body)
    sink.set_cursor('matrix',nxt)
    return len(messages)

def main()->int:
    token=os.environ['USERIO_API_TOKEN']; sink=UserIOIngressClient(os.environ.get('USERIO_INGRESS_URL','http://127.0.0.1:18093'),token)
    reader=MatrixReader(os.environ['USERIO_MATRIX_HOMESERVER'],os.environ['USERIO_MATRIX_ACCESS_TOKEN'],tuple(x.strip() for x in os.environ['USERIO_MATRIX_ROOM_IDS'].split(',') if x.strip()),os.environ['USERIO_MATRIX_USER_ID'])
    interval=float(os.environ.get('USERIO_MATRIX_POLL_INTERVAL_SECONDS','2')); limit=int(os.environ.get('USERIO_MATRIX_POLL_LIMIT','100')); stop=threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM): signal.signal(sig,lambda *_:stop.set())
    while not stop.is_set():
        poll_once(sink,reader,limit=limit); stop.wait(interval)
    return 0
if __name__=='__main__': raise SystemExit(main())
