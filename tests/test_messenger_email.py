"""Focused integration: actual RFC identity, mail scope, unknown no-replay (5s/30s)."""
from email import message_from_string
from email.header import decode_header, make_header
from types import SimpleNamespace
import pytest

from universal_userio.adapters import HimalayaGmailOutbox
from universal_userio.contracts import InboxMessage
from universal_userio.messenger import Messenger
from universal_userio.messenger_email import GmailMessengerProvider
from universal_userio.personal_tokens import PersonalTokens
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


def setup_mail(tmp_path, *, fail=False):
    store=SQLiteUserIOStore(tmp_path/'mail.db');calls=[]
    def run(argv, **kwargs):
        calls.append((argv,kwargs['input']))
        if fail:raise TimeoutError('unknown provider state')
        return SimpleNamespace(returncode=0,stdout='',stderr='')
    outbox=HimalayaGmailOutbox(runner=run)
    service=UserIOService(store,None,None,gmail_outbox=outbox)
    for ref,alias,address in [('gmail-care','care','care@example.test'),('gmail-owner','gmail','owner@example.test')]:
        store.register_account(account_id=ref,provider='gmail',display_name=address,can_read=True,
            can_reply=True,credential_ref='himalaya:'+alias)
        service.messenger_providers[ref]=GmailMessengerProvider(service,
            next(a for a in store.accounts() if a['id']==ref),store.default_user_id)
    cid,_=service.receive(InboxMessage('gmail:care','question@example.test','owner@example.test',
        'Почтовый аккаунт: care@example.test\nТема: Связь аккаунтов\n\nВспомни секретный маркер',1),
        route_id='gmail-read-only',account_ref='care')
    return store,service,Messenger(store,service,store.owner()),cid,calls


def test_stored_mail_source_history_and_exact_rfc_reply(tmp_path):
    store,service,api,cid,calls=setup_mail(tmp_path)
    cap=api.capabilities('gmail-care')
    assert cap['provider_status']=='configured' and cap['history_mode']=='stored'
    assert cap['operations']['reply']['allowed']
    assert all(not cap['operations'][op]['supported'] for op in ('edit','react','typing','sticker'))
    source=api.resolve('gmail-care','owner@example.test','question@example.test')
    assert source['mode']=='stored' and source['fresh'] is False
    assert source['message']['source']['account_ref']=='gmail-care'
    assert source['message']['source']['channel']=='gmail'
    assert source['message']['author']['id']=='owner@example.test'
    action=api.propose('gmail-care',cid,'question@example.test','reply',{'text':'Ответ с маркером'},'mail-new-intent-20261013')
    sent=api.approve(action['id'],action['hash'],True)
    assert sent['status']=='accepted' and sent['receipt']['delivery_status']=='smtp_accepted'
    wire=message_from_string(calls[0][1])
    assert wire['From']=='care@example.test' and wire['To']=='owner@example.test'
    assert wire['In-Reply-To']=='<question@example.test>'
    assert str(make_header(decode_header(wire['Subject'])))=='Re: Связь аккаунтов'
    assert wire['Message-ID'].strip('<>')==sent['receipt']['provider_message_id']
    assert 'Ответ с маркером' in wire.get_payload(decode=True).decode()
    assert api.approve(action['id'],action['hash'],True)['receipt']==sent['receipt']
    assert len(calls)==1
    history=api.history('gmail-care',cid)
    outgoing=history['messages'][-1]
    assert outgoing['author']['id']=='care@example.test' and outgoing['source']['direction']=='outgoing'
    assert outgoing['source']['reply_to_provider_message_id']=='question@example.test'


def test_unknown_mail_is_not_replayed(tmp_path):
    _,_,api,cid,calls=setup_mail(tmp_path,fail=True)
    action=api.propose('gmail-care',cid,'question@example.test','reply',{'text':'Ответ'},'mail-unknown-20261013')
    with pytest.raises(RuntimeError,match='delivery did not complete'):api.approve(action['id'],action['hash'],True)
    assert api.get(action['id'])['status']=='sending'
    with pytest.raises(ValueError,match='delivery_uncertain'):api.approve(action['id'],action['hash'],True)
    assert len(calls)==1


def test_new_only_mail_events_survive_alias_binding(tmp_path):
    store,service,api,cid,_=setup_mail(tmp_path)
    head=api.events('gmail-care')['head'];assert head>0
    store.set_conversation_account(cid,'gmail-care')
    assert api.events('gmail-care',after=head)['events']==[]
    service.receive(InboxMessage('gmail:care','fresh@example.test','owner@example.test','Новый вопрос',2,
        peer_id='owner@example.test',conversation_kind='direct',provider_thread_id='fresh@example.test'),
        route_id='gmail-read-only',account_ref='gmail-care')
    events=api.events('gmail-care',after=head)['events']
    assert len(events)==1 and events[0]['message']['source']['provider_message_id']=='fresh@example.test'
    assert events[0]['message']['source']['thread_id']=='fresh@example.test'


def test_mail_tokens_are_account_scoped_and_readonly_stays_readonly(tmp_path):
    store,service,api,cid,_=setup_mail(tmp_path)
    manager=PersonalTokens(store)
    value=manager.create(store.default_user_id,'mail sender',scope='messenger_actions',account_refs=['gmail-care'])
    principal=store.authenticate_token(value['token'])
    scoped=Messenger(store,service,principal)
    assert scoped.capabilities('gmail-care')['operations']['reply']['allowed']
    with pytest.raises(PermissionError):scoped.capabilities('gmail-owner')
    ro=manager.create(store.default_user_id,'mail readonly',scope='read_only')
    readonly=Messenger(store,service,store.authenticate_token(ro['token']))
    assert readonly.capabilities('gmail-care')['operations']['reply']['reason']=='read_only_token'
    with pytest.raises(KeyError):api.resolve('gmail-owner','owner@example.test','question@example.test')
    with pytest.raises(ValueError):api.resolve('gmail-care','different@example.test','question@example.test')
