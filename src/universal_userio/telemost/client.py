"""Local SSH-side client; one private Unix request supplied through stdin."""
import http.client
import json
import os
from pathlib import Path
import socket
import sys
from urllib.parse import quote

class UnixConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.settimeout(20)
        root=Path(os.environ.get('USERIO_TELEMOST_ROOT',str(Path.home()/'.local/state/userio-telemost')))
        self.sock.connect(str(root/'worker.sock'))


def main():
    payload=json.loads(sys.stdin.buffer.read(8193))
    connection=UnixConnection('localhost',timeout=20)
    op=payload.get('op')
    if op=='audio':
        path='/audio/'+quote(payload['id'],safe='')+'/'+str(int(payload['index']))+'?owner='+quote(payload['owner'],safe='')
        connection.request('GET',path)
    elif op=='pending':
        path=('/pending/'+quote(payload['id'],safe='')+'/'+str(int(payload['index']))
              +'?owner='+quote(payload['owner'],safe='')+'&offset='+str(int(payload.get('offset',0))))
        connection.request('GET',path)
    else:connection.request('POST','/execute',json.dumps(payload),{'Content-Type':'application/json'})
    response=connection.getresponse()
    # 204 means "nothing new yet" for a live poll; it is not a failure.
    if response.status not in {200,204,206}:raise SystemExit('Telemost worker refused operation')
    while data:=response.read(65536):sys.stdout.buffer.write(data)
    connection.close()

if __name__=='__main__':main()
