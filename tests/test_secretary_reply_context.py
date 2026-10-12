"""Fast unit shield: current inbound, roles and preserved followups; max10s."""
import json
from universal_userio.ai import OpenAICompatibleDraftGenerator
from universal_userio.contracts import InboxMessage
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Probe(OpenAICompatibleDraftGenerator):
    def __init__(self):
        super().__init__(endpoint='http://localhost/test', token='test-only')
    def _one_draft(self, prompt, **kwargs):
        self.prompt = prompt
        return 'reply'


def test_reply_prompt_uses_current_message_and_direction_not_contact_label():
    probe = Probe()
    probe.suggest_with_context(conversation_id='conv', latest_message=InboxMessage(
        'telegram','latest','Никита','Собеседник уточнил срок',3), history=[
        {'message_id':'old','sender':'Никита','direction':'incoming','body':'старый вопрос'},
        {'message_id':'reply','sender':'Никита','direction':'outgoing','body':'мой ответ'},
    ], limit=1)
    assert 'Собеседник уточнил срок' in probe.prompt
    assert 'interlocutor' in probe.prompt and 'owner' in probe.prompt
    assert 'latest' in probe.prompt


class Capture:
    def suggest_with_context(self, **kwargs):
        self.context=kwargs
        return ['suggestion']


def test_opt_in_reply_targets_latest_inbound_preserving_owner_reply(tmp_path):
    store=SQLiteUserIOStore(tmp_path/'db.sqlite3');gen=Capture()
    service=UserIOService(store,gen,object())
    cid,_=service.receive(InboxMessage('telegram','question','same-peer','когда?',1),route_id='test')
    service.receive(InboxMessage('telegram','answer','same-peer','уже ответил',2,direction='outgoing'),route_id='test')
    service.propose_from_conversation(cid,limit=1)
    assert gen.context['latest_message'].message_id=='question'
    assert gen.context['latest_message'].direction=='incoming'
    assert any(m['body']=='уже ответил' and m['direction']=='outgoing' for m in gen.context['history'])


def test_external_peer_keeps_receiver_owner_identity(tmp_path):
    store=SQLiteUserIOStore(tmp_path/'db.sqlite3');service=UserIOService(store,Capture(),object())
    store.register_account(account_id='telegram:100',provider='telegram',display_name='Никита',can_read=True,can_reply=True,credential_ref='test-only')
    context=service._triage_actor_context({'source':'telegram','direction':'incoming','conversation_kind':'direct','peer_id':'200','account_ref':'telegram:100','sender':'Дарья'},store.default_user_id)
    assert context is not None and context['receiver_account_id']=='telegram:100'
    assert context['sender_account_id']=='telegram:200'
    assert context['sender_display_name']=='Дарья'


def test_draft_keeps_source_timestamp_and_marks_later_owner_reply_stale(tmp_path):
    store=SQLiteUserIOStore(tmp_path/'db.sqlite3');service=UserIOService(store,Capture(),object())
    cid,_=service.receive(InboxMessage('telegram','question','peer','исходный вопрос',1),route_id='test')
    service.propose_from_conversation(cid,limit=1)
    draft=store.conversation(cid)['drafts'][0]
    assert draft['created_at']>0 and draft['reply_to']['message_id']=='question'
    assert draft['reply_to']['body']=='исходный вопрос' and draft['context_status']=='current'
    service.receive(InboxMessage('telegram','answer','peer','я уже ответил',2,direction='outgoing'),route_id='test')
    after=store.conversation(cid)['drafts'][0]
    assert after['created_at']==draft['created_at'] and after['reply_to']==draft['reply_to']
    assert after['context_status']=='stale'


def test_context_race_during_generation_does_not_bind_new_incoming(tmp_path):
    store=SQLiteUserIOStore(tmp_path/'db.sqlite3')
    class Racing(Capture):
        def suggest_with_context(self, **kwargs):
            service.receive(InboxMessage('telegram','new','peer','новый вопрос',2),route_id='test')
            return ['old-source reply']
    service=UserIOService(store,Racing(),object())
    cid,_=service.receive(InboxMessage('telegram','old','peer','старый вопрос',1),route_id='test')
    service.propose_from_conversation(cid,limit=1)
    draft=store.conversation(cid)['drafts'][0]
    assert draft['reply_to']['message_id']=='old' and draft['context_status']=='stale'


def test_legacy_draft_is_not_assigned_guessed_source(tmp_path):
    from universal_userio.contracts import ReplyDraft
    store=SQLiteUserIOStore(tmp_path/'db.sqlite3');service=UserIOService(store,Capture(),object())
    cid,_=service.receive(InboxMessage('telegram','question','peer','question',1),route_id='test')
    store.add_draft(ReplyDraft('legacy',cid,'old proposal','proposed'))
    draft=store.conversation(cid)['drafts'][0]
    assert draft['created_at']>0 and draft['reply_to'] is None and draft['context_status']=='unknown'


def test_reply_source_is_not_lost_after_more_than_one_ui_page(tmp_path):
    store=SQLiteUserIOStore(tmp_path/'db.sqlite3');gen=Capture()
    service=UserIOService(store,gen,object())
    cid,_=service.receive(InboxMessage('telegram','question','peer','первый вопрос',1),route_id='test')
    for index in range(205):
        service.receive(InboxMessage('telegram',f'reply-{index}','peer','мой ответ',index+2,
                                     direction='outgoing'),route_id='test')
    service.propose_from_conversation(cid,limit=1)
    assert gen.context['latest_message'].message_id=='question'


def test_source_edit_between_selection_and_generation_uses_same_saved_version(tmp_path, monkeypatch):
    store=SQLiteUserIOStore(tmp_path/'db.sqlite3');gen=Capture()
    service=UserIOService(store,gen,object())
    cid,_=service.receive(InboxMessage('telegram','question','peer','старый вопрос',1),route_id='test')
    select=store.latest_incoming_message
    def edit_after_selection(*args, **kwargs):
        old=select(*args,**kwargs)
        service.receive(InboxMessage('telegram','question','peer','исправленный вопрос',1,
            edited_at=2,reconciliation=True),route_id='test')
        return old
    monkeypatch.setattr(store,'latest_incoming_message',edit_after_selection)
    service.propose_from_conversation(cid,limit=1)
    draft=store.conversation(cid)['drafts'][0]
    assert gen.context['latest_message'].body=='исправленный вопрос'
    assert draft['reply_to']['body']==gen.context['latest_message'].body
    assert draft['context_status']=='current'


def test_triage_draft_binds_pre_generation_source_even_if_new_message_arrives(tmp_path):
    store=SQLiteUserIOStore(tmp_path/'db.sqlite3')
    store.register_account(account_id='telegram:100',provider='telegram',display_name='Владелец',
        can_read=True,can_reply=True,credential_ref='test-only')
    class Triage:
        def triage_with_context(self, **kwargs):
            service.receive(InboxMessage('telegram','new','peer','новый вопрос',2,
                conversation_kind='direct',peer_id='200'),route_id='test',account_ref='telegram:100')
            return dict(importance=1.0,confidence=1.0,urgency='low',reason_codes=[],
                reason_ru='Ответить',action_required=True,action_summary='Дать ответ',deadline_at=None,
                suggested_replies=[{'body':'ответ на старый вопрос'}])
    service=UserIOService(store,Triage(),object())
    cid,_=service.receive(InboxMessage('telegram','old','peer','старый вопрос',1,
        conversation_kind='direct',peer_id='200'),route_id='test',account_ref='telegram:100')
    seq=store.workspace_events()['events'][0]['seq']
    source_key=store.workspace_event(seq)['message_id']
    service.triage_workspace_event(event_seq=seq,request_id='source-triage')
    draft=store.conversation(cid)['drafts'][0]
    assert draft['reply_to']['message_id']==source_key
    assert draft['context_status']=='stale'


def test_owner_reply_during_triage_context_preparation_is_not_current(tmp_path, monkeypatch):
    store=SQLiteUserIOStore(tmp_path/'db.sqlite3')
    store.register_account(account_id='telegram:100',provider='telegram',display_name='Владелец',
        can_read=True,can_reply=True,credential_ref='test-only')
    class Triage:
        def triage_with_context(self, **kwargs):
            return dict(importance=1.0,confidence=1.0,urgency='low',reason_codes=[],
                reason_ru='Ответить',action_required=True,action_summary='Дать ответ',deadline_at=None,
                suggested_replies=[{'body':'устаревающее предложение'}])
    service=UserIOService(store,Triage(),object())
    cid,_=service.receive(InboxMessage('telegram','old','peer','вопрос',1,
        conversation_kind='direct',peer_id='200'),route_id='test',account_ref='telegram:100')
    read_followups=store.owner_messages_after
    changed=False
    def respond_after_read(*args, **kwargs):
        nonlocal changed
        old=read_followups(*args,**kwargs)
        if not changed:
            changed=True
            service.receive(InboxMessage('telegram','reply','peer','владелец уже ответил',2,
                direction='outgoing',conversation_kind='direct',peer_id='200'),route_id='test',account_ref='telegram:100')
        return old
    monkeypatch.setattr(store,'owner_messages_after',respond_after_read)
    seq=store.workspace_events()['events'][0]['seq']
    service.triage_workspace_event(event_seq=seq,request_id='followup-race')
    assert store.conversation(cid)['drafts'][0]['context_status']=='stale'


def test_reply_resolves_owner_account_separately_from_female_interlocutor(tmp_path):
    """Fast unit: owner/interlocutor identity regression, expected1s/max10s."""
    store=SQLiteUserIOStore(tmp_path/'actors.sqlite3');probe=Probe()
    store.register_account(account_id='max',provider='max',display_name='Никита',can_read=True,can_reply=True,credential_ref='test-only')
    service=UserIOService(store,probe,object())
    cid,_=service.receive(InboxMessage('max','last','Дарья','Спасибо!',1,peer_id='200',conversation_kind='direct'),route_id='test',account_ref='max')
    service.propose_from_conversation(cid,limit=1)
    context=json.loads(probe.prompt[probe.prompt.index('{"conversation_id"'):])
    assert context['actors']['owner_name']=='Никита'
    assert context['actors']['interlocutor_name']=='Дарья'
    assert context['actors']['owner_account_ref']=='max'
    assert context['latest_message']['speaker_role']=='interlocutor'


def test_group_reply_addressee_is_current_author_not_group_title(tmp_path):
    """Fast unit: group title is not the interlocutor, expected1s/max10s."""
    store=SQLiteUserIOStore(tmp_path/'group-actors.sqlite3');probe=Probe()
    store.register_account(account_id='max',provider='max',display_name='Никита',can_read=True,can_reply=True,credential_ref='test-only')
    service=UserIOService(store,probe,object())
    cid,_=service.receive(InboxMessage('max','group-last','200','Встреча завтра',1,sender_name='Дарья Петрова',peer_id='300',conversation_kind='group',conversation_title='Рабочая группа'),route_id='test',account_ref='max')
    service.propose_from_conversation(cid,limit=1)
    context=json.loads(probe.prompt[probe.prompt.index('{"conversation_id"'):])
    assert context['actors']['interlocutor_name']=='Дарья Петрова'
    assert context['actors']['conversation_name']=='Рабочая группа'
    assert context['actors']['owner_name']=='Никита'


def test_sticker_reaction_keeps_real_speakers_despite_shared_routing_label():
    """Fast unit: old owner problem is not the interlocutor's, 1s/max10s."""
    probe=Probe()
    probe.suggest_with_actor_context(conversation_id='c',latest_message=InboxMessage('max','sticker','same-routing-ref','',3,attachments=({'kind':'sticker'},)),
        history=[{'message_id':'owner-complaint','sender':'same-routing-ref','direction':'outgoing','body':'У меня не отображаются стикеры'}],limit=1,
        actor_context={'owner_name':'Никита','interlocutor_name':'Дарья','conversation_kind':'direct'})
    context=json.loads(probe.prompt[probe.prompt.index('{"conversation_id"'):])
    assert context['history'][0]['sender_label']=='Никита'
    assert context['latest_message']['sender_label']=='Дарья'
    assert context['latest_message']['message_kind']=='sticker_reaction'
    assert context['history'][0]['speaker_role']=='owner'
