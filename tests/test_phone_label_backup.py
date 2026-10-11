"""Focused integration: reveal an existing peer without regrouping. Max10s."""
import importlib.util
from pathlib import Path
from universal_userio.contracts import InboxMessage
from universal_userio.phone_grouping import phone_peer_ref
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


def test_label_only_backup_and_rollback_preserve_history(tmp_path):
    spec=importlib.util.spec_from_file_location('phone_cli',Path(__file__).parents[1]/'scripts/reconcile_phone_conversations.py')
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    store=SQLiteUserIOStore(tmp_path/'db')
    store.register_account(account_id='phone100',provider='phone',display_name='S21',can_read=True,can_reply=False,credential_ref='fixture')
    service=UserIOService(store,object(),object())
    cid,_=service.receive(InboxMessage('phone','one','call','ended',1),route_id='phone')
    peer=phone_peer_ref('phone100','+79991234567');peers={'call':peer}
    store.group_phone_conversations(account_ref='phone100',call_peers=peers,labels={peer:'Телефон · ••••4567'})
    before=store.conversation(cid)
    plan=store.group_phone_conversations(account_ref='phone100',call_peers=peers,dry_run=True)
    assert not plan['groups']
    state=tmp_path/'state';state.mkdir(mode=0o700)
    result=cli.apply_with_backup(store,plan,{peer:'Телефон · +79991234567'},state,peers)
    assert result['backup_consistent'] and result['labels_updated']==1
    assert store.conversation(cid)['messages']==before['messages']
    cli.rollback(store,result['receipt'])
    assert store.conversation(cid)==before


def test_initial_grouping_restores_preexisting_contact_name(tmp_path):
    spec=importlib.util.spec_from_file_location('phone_cli',Path(__file__).parents[1]/'scripts/reconcile_phone_conversations.py')
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    store=SQLiteUserIOStore(tmp_path/'db')
    store.register_account(account_id='phone100',provider='phone',display_name='S21',can_read=True,can_reply=False,credential_ref='fixture')
    service=UserIOService(store,object(),object());service.receive(InboxMessage('phone','one','call','ended',1),route_id='phone')
    peer=phone_peer_ref('phone100','+79991234567');peers={'call':peer};key='chat:phone100:'+peer
    with store._connection:store._connection.execute("INSERT INTO contact_names(user_id,source,sender,name,updated_at) VALUES (?,'phone',?,'Никита',1)",(store.default_user_id,key))
    plan=store.group_phone_conversations(account_ref='phone100',call_peers=peers,dry_run=True)
    state=tmp_path/'state';state.mkdir(mode=0o700)
    result=cli.apply_with_backup(store,plan,{peer:'Телефон · +79991234567'},state,peers)
    cli.rollback(store,result['receipt'])
    assert store._connection.execute('SELECT name FROM contact_names WHERE sender=?',(key,)).fetchone()[0]=='Никита'
