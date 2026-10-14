"""Import Yandex-native identities and history through ordinary UserIO ingress."""
import json
import os
import subprocess
import re
from ..contracts import InboxMessage, DeliveryNotSubmittedError


def connect(service, *, user_id, credentials, vault=None):
    """Persist only a freshly verified, owner-provided Yandex browser session."""
    from ..account_connections import ConnectorSecrets
    verified = read(credentials, limit=1)
    user = verified['user']
    account_id = 'yandex:'+str(user['Uid'])
    record = dict(credentials,provider='yandex',user_id=user_id,account_id=account_id)
    storage = vault or ConnectorSecrets()
    reference = storage.save(record)
    try:
        service._store.register_account(account_id=account_id,provider='yandex',
            display_name='Яндекс · чаты · '+str(user.get('DisplayName') or 'Аккаунт'),
            can_read=True,can_reply=True,credential_ref=reference,user_id=user_id)
        service._store.bind_channel_route(user_id=user_id,source='yandex',route_id='manual')
    except BaseException:
        storage.delete(reference,user_id=user_id,provider='yandex')
        raise
    return account_id, record


def read(account, *, chat_id='', before=0, limit=20, operation='history',
         text='',payload_id='',target_guid=''):
    host = os.environ.get('USERIO_TELEMOST_SSH_HOST', '')
    if host not in {'192.168.2.5','192.168.2.75'}:
        raise RuntimeError('Yandex reader not configured')
    remote = '/home/roomhacker/.local/share/userio-telemost'
    command = ['runuser','-u','roomhacker','--','ssh','-o','BatchMode=yes',
        '-o','ConnectTimeout=5','roomhacker@'+host,'timeout','45','prlimit',
        '--as=268435456','--cpu=15','--nofile=64','--',
        'env','PYTHONPATH='+remote+'/source',remote+'/venv/bin/python',
        '-m','universal_userio.yandex.client']
    result = subprocess.run(command, input=json.dumps({'credentials':account,
        'chat_id':chat_id,'before':before,'limit':limit,'operation':operation,
        'text':text,'payload_id':payload_id,'target_guid':target_guid}).encode(),
        capture_output=True, timeout=50)
    if result.returncode or len(result.stdout)>4*1024*1024:
        try:details=json.loads(result.stdout)
        except ValueError:details={}
        stage=details.get('stage','unknown')
        if stage not in {'identity_read','registry_get_users_data','registry_create_private_chat','push','delivery_readback'}:
            stage='unknown'
        if operation=='send' and details.get('message_submitted') is False:
            raise DeliveryNotSubmittedError('Yandex send not submitted: '+stage)
        raise RuntimeError('Yandex operation unavailable: '+stage)
    return json.loads(result.stdout)


def project(chat, account, *, reconciliation=True):
    peer = chat['ChatId']
    private = bool(chat.get('PartnerInfo')) or bool(re.fullmatch(
        r'[a-z0-9-]{36}_[a-z0-9-]{36}',peer))
    title = str((chat.get('PartnerInfo',{}) if private else chat.get('ChatInfo',{})).get(
        'DisplayName' if private else 'Name') or '')[:200]
    kind = 'direct' if private else ('channel' if peer.startswith('1/') else 'group')
    for item in chat.get('Messages', []):
        server = item.get('ServerMessage',{})
        info = server.get('ServerMessageInfo',{})
        author = info.get('From',{})
        guid = str(author.get('Guid') or '')
        stamp = info.get('Timestamp')
        if not guid or type(stamp) is not int or stamp <= 0:
            continue
        plain = server.get('ClientMessage',{}).get('Plain',{})
        text = plain.get('Text') or ''
        body = str(text.get('MessageText') or '') if isinstance(text,dict) else str(text)
        attachments = []
        for name,attachment_kind in [('Image','image'),('MiscFile','file'),('Voice','voice')]:
            if name in plain:
                data = plain[name].get('FileInfo',{})
                attachments.append({'kind':attachment_kind,'provider_ref':json.dumps(data),
                    'idx':len(attachments)})
        if not body and not attachments:
            # System/control frames must not masquerade as a person's message.
            continue
        yield InboxMessage(source='yandex',
            message_id=account['account_id']+'|'+peer+':'+str(stamp),
            sender=guid, sender_name=str(author.get('DisplayName') or guid)[:200],
            body=body, attachments=tuple(attachments), received_at=stamp/1000000,
            edited_at=float(info.get('LastEditTimestamp') or 0)/1000000,
            direction='outgoing' if guid==account['guid'] else 'incoming',
            peer_id=peer, conversation_title=title,
            conversation_kind=kind,
            sender_is_bot=bool(author.get('IsRobot')), reconciliation=reconciliation)


def poll(service, account):
    user = account['user_id']
    key = 'yandex:sync-ready:'+account['account_id']
    first = not service._store.user_preference(key,user_id=user)
    result = read(account)
    count = 0
    for chat in result['chats']:
        for message in project(chat,account,reconciliation=first):
            _, accepted = service.receive(message,route_id='manual',user_id=user,
                account_ref=account['account_id'])
            count += int(accepted)
        page_key = 'yandex:backfill:'+account['account_id']+':'+chat['ChatId']
        if not service._store.user_preference(page_key,user_id=user):
            timestamps = [m.get('ServerMessage',{}).get('ServerMessageInfo',{}).get('Timestamp',0)
                          for m in chat.get('Messages',[])]
            timestamps = [t for t in timestamps if type(t) is int and t>0]
            service._store.set_user_preference(page_key,
                str(min(timestamps)) if timestamps else 'done',user_id=user)
    # Bounded fair backfill: one historical page per poll, never old AI dispatch.
    pending = [(chat, 'yandex:backfill:'+account['account_id']+':'+chat['ChatId'])
               for chat in result['chats']]
    pending = [(chat,k) for chat,k in pending
               if service._store.user_preference(k,user_id=user) not in (None,'done')]
    if pending:
        turn_key = 'yandex:backfill-turn:'+account['account_id']
        turn = int(service._store.user_preference(turn_key,user_id=user) or '0')
        chat, page_key = pending[turn % len(pending)]
        before = int(service._store.user_preference(page_key,user_id=user))
        page = read(account,chat_id=chat['ChatId'],before=before,limit=50)
        timestamps = []
        for old in page['chats']:
            if old['ChatId'] != chat['ChatId']:
                raise ValueError('Yandex returned another chat')
            for m in old.get('Messages',[]):
                ts = m.get('ServerMessage',{}).get('ServerMessageInfo',{}).get('Timestamp',0)
                if type(ts) is int and 0<ts<before:timestamps.append(ts)
            for message in project(old,account,reconciliation=True):
                _, accepted = service.receive(message,route_id='manual',user_id=user,
                    account_ref=account['account_id'])
                count += int(accepted)
        service._store.set_user_preference(page_key,
            str(min(timestamps)) if len(timestamps)>=50 else 'done',user_id=user)
        service._store.set_user_preference(turn_key,str(turn+1),user_id=user)
    service._store.set_user_preference(key,'true',user_id=user)
    import time
    service._store.set_user_preference('yandex:last-poll:'+account['account_id'],
                                      str(time.time()),user_id=user)
    return {'chats':len(result['chats']),'new_messages':count,'initial_history':first}
