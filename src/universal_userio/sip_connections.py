"""Owned hosted SIM/SIP enrollment. No registration is mistaken for audio proof."""
from __future__ import annotations
import hashlib, json, os, re, secrets, socket, subprocess
from pathlib import Path

SOCKET = '/var/lib/universal-userio/sip/provision.sock'

def request(action: str, user_id: str, **fields) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(25);client.connect(os.environ.get('USERIO_SIP_SOCKET',SOCKET))
        client.sendall(json.dumps(dict(action=action,user_id=user_id,**fields)).encode()+b'\n')
        data=b''
        while not data.endswith(b'\n') and len(data)<65536:
            chunk=client.recv(4096)
            if not chunk:break
            data+=chunk
    result=json.loads(data)
    if result.get('error'):raise ValueError(result['error'])
    return result

class Registry:
    """Single local provisioner owns only its include and private peer registry."""
    def __init__(self, root:Path, include:Path, *, runner=subprocess.run):
        self.root=root;self.include=include;self.runner=runner
        root.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.path=root/'peers.json'
        self.rows=json.loads(self.path.read_text()) if self.path.exists() else {}
    def _save(self):
        content='; Managed UserIO personal SIM peers; do not edit.\n'
        for peer,row in sorted(self.rows.items()):
            content+=f"\n[{peer}]\ntype=friend\nhost=dynamic\ndefaultuser={peer}\nsecret={row['password']}\ncontext=userio-sim-incoming\ninsecure=no\ndirectmedia=no\nnat=force_rport,comedia\nallowtransfer=no\ndisallow=all\nallow=ulaw\nallow=alaw\nqualify=yes\ncall-limit=1\n"
        # Preserve both files if a write fails before reload. Only this include is managed.
        for path,text,mode in [(self.path,json.dumps(self.rows),0o600),(self.include,content,0o640)]:
            tmp=path.with_name(path.name+'.next');tmp.write_text(text);tmp.chmod(mode)
            if path==self.include and path.exists():os.chown(tmp,path.stat().st_uid,path.stat().st_gid)
            tmp.replace(path)
        self.runner(['/usr/sbin/asterisk','-rx','sip reload'],capture_output=True,timeout=5,check=True)
    def handle(self,body:dict)->dict:
        uid=str(body.get('user_id') or '')
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}',uid):raise ValueError('Нужен вход в UserIO')
        action=body.get('action')
        if action=='create':
            device=str(body.get('device_id') or '').strip()
            name=str(body.get('display_name') or '').strip()
            if not re.fullmatch(r'[a-zA-Z0-9_-]{3,64}',device):raise ValueError('ID телефона: от 3 до 64 латинских букв, цифр, дефисов')
            if not name or len(name)>80:raise ValueError('Укажите название телефона, до 80 символов')
            peer='uio-'+hashlib.sha256((uid+'\0'+device).encode()).hexdigest()[:24]
            existing=self.rows.get(peer)
            if not existing:
                if len(self.rows)>=64 or sum(x['user_id']==uid for x in self.rows.values())>=3:raise ValueError('Лимит телефонов достигнут; отключите ненужный телефон')
                self.rows[peer]=dict(user_id=uid,device_id=device,display_name=name,password=secrets.token_urlsafe(32))
                try:self._save()
                except Exception:
                    self.rows.pop(peer,None);self._save();raise
            return self._public(peer,credentials=True)
        if action=='list':return {'connections':[self._public(p) for p,r in self.rows.items() if r['user_id']==uid]}
        ref=str(body.get('account_id') or '')
        peer=ref.removeprefix('phone:sip:')
        if not re.fullmatch(r'uio-[0-9a-f]{24}',peer) or self.rows.get(peer,{}).get('user_id')!=uid:raise ValueError('Подключение не найдено')
        if action=='settings':return self._public(peer,credentials=True)
        if action=='delete':
            row=self.rows.pop(peer)
            try:self._save()
            except Exception:self.rows[peer]=row;self._save();raise
            return {'deleted':True}
        raise ValueError('Неизвестное действие')
    def _public(self,peer,credentials=False):
        row=self.rows[peer]
        result=self.runner(['/usr/sbin/asterisk','-rx','sip show peer '+peer],capture_output=True,text=True,timeout=5,check=True)
        registered=bool(re.search(r'Status\s*:\s*OK\s*\(',result.stdout))
        output=dict(id='phone:sip:'+peer,display_name=row['display_name'],device_id=row['device_id'],
                    status='registered' if registered else 'waiting_phone',call_ready=False,
                    status_text='SIP зарегистрирован. Проверка маршрута и звука ещё не выполнена.' if registered else 'Настройки выданы. Ждём подключения GSM2SIP на телефоне.')
        if credentials:output.update(sip_server='bezrabotnyi.com',sip_port=5060,sip_transport='UDP',sip_username=peer,sip_password=row['password'],codecs=['PCMU','PCMA'])
        return output


def serve():
    import socketserver
    root=Path('/var/lib/universal-userio/sip');registry=Registry(root,Path('/etc/asterisk/userio-peers/peers.conf'))
    path=root/'provision.sock';path.unlink(missing_ok=True)
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(25)
            try:
                data=self.rfile.readline(8193)
                if len(data)>8192:raise ValueError('Запрос слишком большой')
                result=registry.handle(json.loads(data))
            except ValueError as exc:result={'error':str(exc)}
            except Exception:result={'error':'SIP-сервер временно недоступен. Повторите позже.'}
            self.wfile.write(json.dumps(result,ensure_ascii=False).encode()+b'\n')
    with socketserver.UnixStreamServer(str(path),Handler) as server:
        path.chmod(0o600);server.serve_forever()

if __name__=='__main__':serve()
