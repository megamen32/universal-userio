"""Finite administrator MAX QR login using the existing owned MAX transport.

Official web client: INIT6, QR288, status289, human-confirmed AuthByTrack291.
No SMS, message action, existing-session extraction or credential replacement.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import time
import uuid

from .channels.max_client import MaxClient, MaxTransport


class MaxQrLogin:
    def __init__(self, expected_user: str, *, client=None, socks_host='', socks_port=0):
        if expected_user and not re.fullmatch(r'[1-9][0-9]*', expected_user):
            raise ValueError('exact expected MAX account required')
        self.expected_user=expected_user
        self.device_id=str(uuid.uuid4())
        self.client=client or MaxClient('',self.device_id,app_version='26.10.3',
            transport=MaxTransport(socks_host=socks_host,socks_port=socks_port,web_auth=True))
        self._track='';self._ready=False;self._completed=False

    def begin(self):
        if self._track:raise ValueError('QR flow already started')
        self.client.initialize_anonymous()
        reply=self.client._roundtrip(288,{})
        if (not isinstance(reply,dict) or not isinstance(reply.get('trackId'),str) or not reply['trackId']
                or not isinstance(reply.get('qrLink'),str) or not reply['qrLink']
                or type(reply.get('ttl')) is not int or not 0<reply['ttl']<=600000
                or type(reply.get('pollingInterval')) is not int or not 100<=reply['pollingInterval']<=30000):
            raise ValueError('MAX QR response unavailable')
        self._track=reply['trackId']
        return {key:reply[key] for key in ('qrLink','ttl','pollingInterval')}

    def poll(self):
        if not self._track or self._completed:raise ValueError('active QR flow required')
        reply=self.client._roundtrip(289,{'trackId':self._track})
        status=reply.get('status') if isinstance(reply,dict) else None
        if not isinstance(status,dict) or type(status.get('loginAvailable')) is not bool:
            raise ValueError('MAX QR status unavailable')
        self._ready=status['loginAvailable']
        return self._ready

    def finish(self):
        if not self._ready:raise ValueError('human MAX confirmation required')
        if self._completed:raise ValueError('AuthByTrack outcome must not replay')
        self._completed=True
        reply=self.client._roundtrip(291,{'trackId':self._track})
        if isinstance(reply,dict) and reply.get('passwordChallenge'):
            raise ValueError('MAX additional confirmation required')
        profile=reply.get('profile',{}) if isinstance(reply,dict) else {}
        contact=profile.get('contact',{}) if isinstance(profile,dict) else {}
        actual=str(contact.get('id') or '')
        if not re.fullmatch(r'[1-9][0-9]*',actual) or self.expected_user and actual!=self.expected_user:
            raise ValueError('MAX authenticated account differs from expected account')
        attrs=reply.get('tokenAttrs',{})
        login=attrs.get('LOGIN',{}) if isinstance(attrs,dict) else {}
        token=login.get('token') if isinstance(login,dict) else None
        if (not isinstance(token,str) or not 16<=len(token)<=8192
                or any(ord(c)<32 for c in token)):
            raise ValueError('MAX login credential unavailable')
        return {'token':token,'device_id':self.device_id,'user_id':actual}

    def close(self):self.client.close()


def private_write(path, value):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as stream:json.dump(value,stream);stream.flush();os.fsync(stream.fileno())


def main():
    parser=argparse.ArgumentParser(description='Connect the same MAX account by human-scanned QR; never prints login credentials.')
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--expected-user',required=True)
    parser.add_argument('--wall',type=int,default=105)
    args=parser.parse_args()
    area=args.directory
    if (not area.is_absolute() or area.is_symlink() or area.resolve()!=area
            or not area.is_dir() or area.stat().st_uid!=os.getuid()
            or area.stat().st_mode&0o777!=0o700 or not 20<=args.wall<=110):
        parser.error('private existing directory and finite wall20..110 required')
    import qrcode
    proxy=os.environ.get('USERIO_MAX_SOCKS','').strip()
    host,_,port=proxy.rpartition(':') if proxy else ('','','0')
    flow=MaxQrLogin(args.expected_user,socks_host=host,socks_port=int(port or 1080))
    private_write(area/'intent.json',{'started_at':time.time(),'expected_user':args.expected_user,'provider_messages':0})
    qr=area/'qr.png';started=time.monotonic()
    try:
        pending=flow.begin()
        deadline=min(started+args.wall,time.monotonic()+pending['ttl']/1000)
        with qr.open('xb') as output:
            os.chmod(qr,0o600);qrcode.make(pending['qrLink']).save(output,format='PNG')
        print(json.dumps({'status':'qr_ready','image':str(qr),'expected_user':args.expected_user,
                         'expires_at':time.time()+max(0,deadline-time.monotonic()),'provider_messages':0}),flush=True)
        while time.monotonic()<deadline-5:
            if flow.poll():
                private_write(area/'auth-attempt.json',{'at':time.time(),'expected_user':args.expected_user,
                                                       'status':'AuthByTrack attempted; do not replay'})
                credential=flow.finish()
                private_write(area/'credential.json',{**credential,'verified_native_login':False})
                private_write(area/'auth-received.json',{'at':time.time(),'expected_user':args.expected_user,
                                                        'status':'received; verify SAME credential before install'})
                # Verify the credential through the normal server login before
                # staging it. This performs no message writes or read ACK.
                verify=MaxClient(credential['token'],credential['device_id'],socks_host=host,socks_port=int(port or 1080))
                try:
                    profile=verify.connect().get('profile',{}).get('contact',{})
                    if str(profile.get('id') or '')!=args.expected_user:
                        raise ValueError('MAX native account verification failed')
                finally:verify.close()
                private_write(area/'credential-verified.json',{**credential,'verified_native_login':True})
                os.replace(area/'credential-verified.json',area/'credential.json')
                private_write(area/'result.json',{'status':'verified','expected_user':args.expected_user,'provider_messages':0,
                                                  'credential_ref':str(area/'credential.json'),'finished_at':time.time()})
                print(json.dumps({'status':'verified','expected_user':args.expected_user,'credential_ref':str(area/'credential.json'),'provider_messages':0}),flush=True)
                return 0
            time.sleep(min(pending['pollingInterval']/1000,3,max(0,deadline-time.monotonic()-5)))
        private_write(area/'result.json',{'status':'expired','provider_messages':0,'finished_at':time.time()})
        print(json.dumps({'status':'expired','provider_messages':0}),flush=True)
        return 1
    except Exception as error:
        private_write(area/'result.json',{'status':'failed','error_class':type(error).__name__,'provider_messages':0,
                                          'auth_by_track_attempted':flow._completed,'finished_at':time.time()})
        print(json.dumps({'status':'failed','error_class':type(error).__name__,'provider_messages':0}),flush=True)
        return 1
    finally:
        flow.close()
        qr.unlink(missing_ok=True)


if __name__=='__main__':raise SystemExit(main())
