"""Original Gmail Sent-copy verification: no mail is sent by the reader."""
from email.message import EmailMessage
import io
import json
import subprocess
import pytest

from universal_userio.gmail_sent_reconciliation import reconcile_original_gmail_sent
from universal_userio.adapters import HimalayaGmailOutbox
from universal_userio.delivery_receipt import read_receipt


def raw_mail(message_id, sender='from@example.test', to='to@example.test',
             body='fixture body', subject='Re: UserIO reply', attachment=False):
    mail=EmailMessage()
    mail['From']=sender
    mail['To']=to
    mail['Subject']=subject
    mail['Message-ID']='<'+message_id+'>'
    mail.set_content(body)
    if attachment:
        mail.add_attachment(b'file',maintype='application',subtype='octet-stream',filename='x.bin')
    return mail.as_bytes()


class FakeReader:
    def __init__(self, *, sent=None, envelopes=None, exit_code=0, mailboxes=None):
        self.sent=sent
        self.envelopes=[{'id':'123'}] if envelopes is None else envelopes
        self.mailboxes=[{'name':'[Gmail]/Отправленные','id':'[Gmail]/Отправленные'}] if mailboxes is None else mailboxes
        self.exit_code=exit_code
        self.actions=[]
    def __call__(self, argv, **kwargs):
        self.actions.append(list(argv))
        assert 'send' not in argv and 'add' not in argv
        if 'mailbox' in argv and 'list' in argv:
            return subprocess.CompletedProcess(argv,self.exit_code,
                stdout=json.dumps({'mailboxes':self.mailboxes}).encode(),stderr=b'')
        if 'envelope' in argv:
            return subprocess.CompletedProcess(argv,self.exit_code,
                stdout=json.dumps(self.envelopes).encode(),stderr=b'')
        assert 'message' in argv and 'read' in argv and '--raw' in argv
        return subprocess.CompletedProcess(argv,self.exit_code,
            stdout=self.sent or b'',stderr=b'')


def pair(*, sender='from@example.test', to='to@example.test', body='fixture body',
         raw=None, envelopes=None, exit_code=0, mailboxes=None):
    outbound=HimalayaGmailOutbox.outgoing_message_id('work','draft-one')
    reader=FakeReader(sent=raw if raw is not None else raw_mail(outbound, sender,to,body),
                      envelopes=envelopes,exit_code=exit_code,mailboxes=mailboxes)
    outbox=HimalayaGmailOutbox(binary='/usr/bin/fake-mail',config='/fixture/config',
                            runner=reader,sent_mailbox='sent')
    return outbox,reader


def reconcile(outbox, **updates):
    options={'account':'work','sender':'from@example.test',
             'recipient':'to@example.test','draft_id':'draft-one','body':'fixture body'}
    options.update(updates)
    return reconcile_original_gmail_sent(outbox,**options)


def test_unique_exact_original_sent_copy_is_smtp_accepted_not_recipient_delivered():
    outbox,runner=pair()
    receipt=reconcile(outbox)
    assert receipt and receipt.startswith('himalaya:work:draft-one:')
    readback=read_receipt(receipt)
    assert readback['provider']=='gmail'
    assert readback['delivery_status']=='smtp_accepted'
    assert readback['delivery_confirmed'] is False
    assert readback['sent_copy']['saved'] == 'true'
    assert len(runner.actions)==3
    assert runner.actions[1][-2:]==['to','to@example.test']
    assert all('send' not in args and 'add' not in args for args in runner.actions)


@pytest.mark.parametrize('change',[
    {'sender':'wrong@example.test'}, {'to':'wrong@example.test'},
    {'body':'wrong'}, {'attachment':True}, {'message_id':'wrong@userio.local'},
])
def test_wrong_provider_payload_never_resolves_unknown(change):
    original=HimalayaGmailOutbox.outgoing_message_id('work','draft-one')
    msg=raw_mail(change.get('message_id',original),
                 change.get('sender','from@example.test'),
                 change.get('to','to@example.test'),
                 change.get('body','fixture body'),
                 attachment=change.get('attachment',False))
    outbox,_=pair(raw=msg)
    assert reconcile(outbox) is None


def test_sent_copy_absence_does_not_authorize_retry():
    outbox,reader=pair(envelopes=[])
    assert reconcile(outbox) is None
    assert len(reader.actions)==2


def test_duplicate_matching_message_ids_are_not_trusted():
    outbox,reader=pair(envelopes=[{'id':'123'},{'id':'124'}])
    assert reconcile(outbox) is None
    assert len(reader.actions)==4


def test_malformed_search_or_permission_error_keeps_unknown():
    outbox,_=pair(exit_code=1)
    with pytest.raises(RuntimeError):
        reconcile(outbox)


@pytest.mark.parametrize('uid',['--attack','/tmp/path','0','-8',''])
def test_untrusted_mailbox_id_cannot_become_cli_argument(uid):
    outbox,reader=pair(envelopes=[{'id':uid}])
    assert reconcile(outbox) is None
    assert len(reader.actions)==2


def test_no_exact_account_sender_or_recipient_is_allowed():
    outbox,_=pair()
    with pytest.raises(ValueError):
        reconcile(outbox,recipient='')
    with pytest.raises(ValueError):
        reconcile(outbox,account='')


def test_cli_uses_single_pinned_account_and_only_read_operations():
    outbox,reader=pair()
    assert reconcile(outbox)
    for cmd in reader.actions:
        assert cmd[:5]==['/usr/bin/fake-mail','--config','/fixture/config','--account','work']
        if 'mailbox' not in cmd or 'list' not in cmd:
            assert '--mailbox' in cmd
            assert cmd[cmd.index('--mailbox')+1]=='[Gmail]/Отправленные'


def test_userio_gmail_provider_path_requires_scoped_connected_account():
    from types import SimpleNamespace
    from universal_userio.provider_readback import original_provider_receipt
    outbox,runner=pair()
    class Store:
        def accounts(self,*,user_id):
            return [{'id':'gmail:work','provider':'gmail','credential_ref':'himalaya:work',
                     'enabled':True,'capabilities':{'read','reply'},
                     'display_name':'from@example.test'}]
    svc=SimpleNamespace(_store=Store(),gmail_outbox=outbox)
    draft=SimpleNamespace(id='draft-one',body='fixture body')
    conversation={'source':'gmail:work','account_ref':'gmail:work',
                  'peer_id':'to@example.test','sender':'to@example.test'}
    receipt=original_provider_receipt(svc,draft,conversation,user_id='owner')
    assert receipt and receipt.startswith('himalaya:work:draft-one:')
    before=len(runner.actions)
    assert original_provider_receipt(svc,draft,{**conversation,'account_ref':'gmail:foreign'},
                                     user_id='owner') is None
    assert len(runner.actions)==before


def test_native_sent_folder_preferred_over_ambiguous_user_folders():
    from universal_userio.gmail_sent_reconciliation import resolve_native_sent_mailbox
    outbox,reader=pair(mailboxes=[
        {'name':'My/Отправленные'},{'name':'[Gmail]/Отправленные'},
        {'name':'Archive/Отправленные'}])
    assert resolve_native_sent_mailbox(outbox,'work')=='[Gmail]/Отправленные'
    assert len(reader.actions)==1


def test_ambiguous_sent_folders_cannot_be_selected():
    outbox,reader=pair(mailboxes=[{'name':'A/Отправленные'},
                                  {'name':'B/Отправленные'}])
    assert reconcile(outbox) is None
    assert len(reader.actions)==1


def test_existing_sent_copy_writer_uses_native_provider_mailbox_without_resending():
    from universal_userio.gmail_sent_reconciliation import resolve_native_sent_mailbox
    actions=[]
    def runner(argv,**kwargs):
        actions.append(list(argv))
        if 'mailbox' in argv and 'list' in argv:
            return subprocess.CompletedProcess(argv,0,
                stdout=json.dumps({'mailboxes':[{'name':'[Gmail]/Отправленные'}]}).encode(),stderr=b'')
        assert 'message' in argv and 'add' in argv
        assert '--mailbox' in argv and argv[argv.index('--mailbox')+1]=='[Gmail]/Отправленные'
        return subprocess.CompletedProcess(argv,0,stdout=b'',stderr=b'')
    outbox=HimalayaGmailOutbox(binary='/usr/bin/fake-mail',config='/fixture/config',runner=runner)
    assert outbox._save_sent_copy(account='work',raw='fixture original RFC5322')=='saved'
    assert len(actions)==2 and all('send' not in call for call in actions)
