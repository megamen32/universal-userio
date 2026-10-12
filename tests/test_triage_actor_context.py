from universal_userio.contracts import InboxMessage
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def generate(self, **kwargs):
        return []

    def triage_with_context(self, **kwargs):
        self.input = kwargs
        return dict(decision="silent", importance=0.1, confidence=1.0,
                    urgency="none", reason_codes=[], reason_ru="Обычное сообщение",
                    action_required=False, action_summary="", deadline_at=None,
                    suggested_replies=[], safety_override=False, policy_version="test")


class Outbox:
    def send_reply(self, **kwargs):
        raise AssertionError("No outbound replies")


def register(store, account_id, name, user_id=None):
    store.register_account(account_id=account_id, provider="telegram",
                           display_name=name, can_read=True, can_reply=True,
                           credential_ref="test-ref", user_id=user_id)


def test_private_incoming_resolves_distinct_accounts_without_changing_raw_label(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "state.sqlite3")
    register(store, "telegram:100", "Никита")
    register(store, "telegram:200", "Секретарь")
    generator = Generator()
    service = UserIOService(store, generator, Outbox())
    conversation_id, _ = service.receive(
        InboxMessage("telegram", "200:1", "Никита (старый контакт)", "Результат работы", 1,
                     direction="incoming", conversation_kind="direct", peer_id="200"),
        account_ref="telegram:100", route_id="telegram",
    )
    seq = store.workspace_events()["events"][0]["seq"]
    result = service.triage_workspace_event(event_seq=seq, request_id="actor-test")
    assert generator.input["actor_context"] == {
        "sender_account_id": "telegram:200", "sender_display_name": "Секретарь",
        "receiver_account_id": "telegram:100", "receiver_display_name": "Никита",
    }
    assert generator.input["latest_message"].sender == "Никита (старый контакт)"
    assert store.conversation(conversation_id)["messages"][0]["sender"] == "Никита (старый контакт)"
    assert result["triage"]["decision"] == "silent"  # Identity never forces a notification.


def test_group_and_foreign_accounts_never_supply_a_private_sender_identity(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "state.sqlite3")
    register(store, "telegram:100", "Никита")
    other, _ = store.create_user("other", "test-password")
    register(store, "telegram:200", "Foreign private name", other.user_id)
    service = UserIOService(store, Generator(), Outbox())
    event = dict(source="telegram", direction="incoming", conversation_kind="direct",
                 peer_id="200", account_ref="telegram:100")
    context = service._triage_actor_context(event, store._user(None))
    assert context['receiver_account_id'] == 'telegram:100'
    assert context['sender_display_name'] == '200'
    assert 'Foreign private name' not in str(context)
    register(store, "telegram:200", "Секретарь")
    assert service._triage_actor_context({**event, "conversation_kind": "group"}, store._user(None)) is None
    assert service._triage_actor_context({**event, "direction": "outgoing"}, store._user(None)) is None
    assert service._triage_actor_context({**event, "peer_id": "100"}, store._user(None)) is None


def test_max_automatic_triage_resolves_registered_owner_and_current_contact(tmp_path):
    store=SQLiteUserIOStore(tmp_path/'max.sqlite3')
    store.register_account(account_id='max',provider='max',display_name='Никита',can_read=True,can_reply=True,credential_ref='test-only')
    generator=Generator();service=UserIOService(store,generator,Outbox())
    cid,_=service.receive(InboxMessage('max','current','Дарья','Спасибо!',1791789000,
                                     peer_id='200',conversation_kind='direct'),route_id='test',account_ref='max')
    seq=store.workspace_events()['events'][0]['seq']
    service.triage_workspace_event(event_seq=seq,request_id='max-actor-test')
    assert generator.input['actor_context']['receiver_display_name']=='Никита'
    assert generator.input['actor_context']['sender_display_name']=='Дарья'


def test_mail_automatic_triage_resolves_exact_registered_himalaya_alias(tmp_path):
    store=SQLiteUserIOStore(tmp_path/'mail.sqlite3')
    store.register_account(account_id='gmail-owner',provider='gmail',display_name='Никита · owner@example.test',
                           can_read=True,can_reply=True,credential_ref='himalaya:owner')
    generator=Generator();service=UserIOService(store,generator,Outbox())
    cid,_=service.receive(InboxMessage('gmail:owner','mail-1','partner@example.test','Письмо',1791789000,
                                     conversation_kind='direct'),route_id='gmail:owner',account_ref='owner')
    seq=store.workspace_events()['events'][0]['seq']
    service.triage_workspace_event(event_seq=seq,request_id='mail-actor-test')
    actors=generator.input['actor_context']
    assert actors['receiver_account_id']=='gmail-owner'
    assert actors['sender_display_name']=='partner@example.test'


def test_telegram_group_triage_uses_current_author_not_group_as_person(tmp_path):
    store=SQLiteUserIOStore(tmp_path/'group-triage.sqlite3')
    register(store,'telegram:100','Никита')
    generator=Generator();service=UserIOService(store,generator,Outbox())
    import json
    routing={'kind':'telegram_routing','provider_ref':json.dumps({'group_name':'Рабочая группа','author_name':'Дарья Петрова','author_resolved':True,'author_id':'900'})}
    cid,_=service.receive(InboxMessage('telegram','group-9','900','Проверьте договор',1791789000,
                                     sender_name='Дарья Петрова',attachments=(routing,),peer_id='-1007',conversation_kind='group',
                                     conversation_title='Рабочая группа'),route_id='telegram',account_ref='telegram:100')
    store.set_workspace_chat_rule(conversation_id=cid,action='allow')
    service.receive(InboxMessage('telegram','group-10','900','Уточняю срок',1791789001,
                                 sender_name='Дарья Петрова',attachments=(routing,),peer_id='-1007',conversation_kind='group',
                                 conversation_title='Рабочая группа'),route_id='telegram',account_ref='telegram:100')
    seq=store.workspace_events()['events'][0]['seq']
    service.triage_workspace_event(event_seq=seq,request_id='group-actor-test')
    assert generator.input['actor_context']['sender_display_name']=='Дарья Петрова'
    assert generator.input['actor_context']['receiver_display_name']=='Никита'
