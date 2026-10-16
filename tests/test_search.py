from universal_userio.contracts import InboxMessage
from universal_userio.search import search_conversations
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def propose(self, *_args, **_kwargs):
        return ""


class Outbox:
    def send(self, *_args, **_kwargs):
        return "noop"


def test_global_search_finds_old_message_across_sources(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    service.receive(InboxMessage("telegram", "tg-old", "Alice", "старое редкое слово архипелаг", 1.0), route_id="telegram")
    service.receive(InboxMessage("telegram", "tg-new", "Alice", "новый обычный текст", 2.0), route_id="telegram")
    service.receive(InboxMessage("sms", "sms-1", "+100", "архипелаг в смс", 3.0), route_id="sms")

    results = search_conversations(store, "архипелаг")
    assert {row["source"] for row in results} == {"telegram", "sms"}
    telegram = next(row for row in results if row["source"] == "telegram")
    assert telegram["match_message_id"] == "tg-old"
    assert "архипелаг" in str(telegram["preview"])


def test_search_source_filter(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    service.receive(InboxMessage("telegram", "tg", "Alice", "needle", 1.0), route_id="telegram")
    service.receive(InboxMessage("sms", "sms", "+100", "needle", 2.0), route_id="sms")
    results = search_conversations(store, "needle", source="telegram")
    assert len(results) == 1
    assert results[0]["source"] == "telegram"


def test_search_unread_aggregation_has_linear_sql_work(tmp_path):
    """Fast unit, expected <1s/max5s: prevent CPU/HTTP thread exhaustion."""
    store = SQLiteUserIOStore(tmp_path / "search.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    cid, _ = service.receive(InboxMessage("telegram", "seed", "Group", "needle", 1.0), route_id="telegram")
    row = dict(store._connection.execute("SELECT * FROM messages WHERE message_id='seed'").fetchone())
    columns = list(row)
    rows = []
    for i in range(2000):
        item = dict(row, message_id=f"search-{i}", received_at=i+2., seen_at=None if i % 2 else 1.)
        rows.append(tuple(item[name] for name in columns))
    with store._connection:
        store._connection.executemany(f"INSERT INTO messages ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", rows)
    steps = 0
    def bounded_query():
        nonlocal steps
        steps += 1
        return int(steps > 100)
    store._connection.set_progress_handler(bounded_query, 10000)
    try:
        result = search_conversations(store, "needle", source="telegram")
    finally:
        store._connection.set_progress_handler(None, 0)
    assert len(result) == 1 and result[0]["id"] == cid
    assert result[0]["unread_count"] == 1001
    assert steps <= 100


def test_index_covers_old_messages_and_live_edits_deletion(tmp_path):
    """Fast unit expected2s/max20s: history cutoff, stale edit/delete index."""
    store=SQLiteUserIOStore(tmp_path/'index.sqlite3'); service=UserIOService(store,Generator(),Outbox())
    cid,_=service.receive(InboxMessage('telegram','old','Person','уникальный договор',1),route_id='telegram')
    for i in range(110):
        service.receive(InboxMessage('sms',str(i),str(i),'recent ordinary message',i+2),route_id='sms')
    # No hidden scan ceiling: the old hit is found regardless of how many newer
    # messages were indexed after it.
    assert search_conversations(store,'договор')[0]['id']==cid
    with store._connection:
        store._connection.execute("UPDATE messages SET body='другая фраза' WHERE message_id='old'")
    assert not search_conversations(store,'договор',match_type='exact')
    assert search_conversations(store,'другая фраза',match_type='exact')[0]['id']==cid
    with store._connection:
        store._connection.execute("UPDATE messages SET deleted_at=123 WHERE message_id='old'")
    assert not search_conversations(store,'другая фраза',match_type='exact')


def test_profile_exact_priority_typo_phone_and_tenant(tmp_path):
    """Fast unit expected2s/max20s: real contact fields, typos and isolation."""
    from dataclasses import replace
    from universal_userio.adapters import inbox_message_from_envelope
    store=SQLiteUserIOStore(tmp_path/'profiles.sqlite3');service=UserIOService(store,Generator(),Outbox())
    profile={'username':'semerindima','first_name':'Дмитрий','last_name':'Семерин','phone':'79841303184'}
    msg=inbox_message_from_envelope({'schema':'universal.inbox.message.v1','source':'telegram',
        'message_id':'target','sender':'Дмитрий Семерин','body':'hello','peer_id':'381703703','peer_profile':profile},received_at=1)
    cid,_=service.receive(msg,route_id='telegram')
    service.receive(InboxMessage('sms','mention','other','@semerindima',999),route_id='sms')
    for q in ['@semerindima','SEMERINDIMA','Дмитрий','Семерин','+7 984 130-31-84','79841303184']:
        results=search_conversations(store,q,match_type='exact')
        assert results[0]['id']==cid, q
        assert results[0]['match_field']=='contact'
    for q in ['@semeridima','@semerindmia','Семерни','Дмиртий']:
        results=search_conversations(store,q,match_type='fuzzy')
        assert cid in [r['id'] for r in results],q
        assert all(r['match_type']=='fuzzy' for r in results)
    other,_=store.create_user('other-user','sufficient-password')
    assert not search_conversations(store,'semerindima',user_id=other.user_id)
    service.receive(replace(msg, peer_profile={**profile,'username':'newhandle'}),route_id='telegram')
    assert cid not in [r['id'] for r in search_conversations(store,'semerindima',match_type='exact')]
    assert search_conversations(store,'newhandle',match_type='exact')[0]['id']==cid


def test_exact_and_fuzzy_pages_are_independent(tmp_path):
    """Fast unit expected2s/max20s: one group cannot hide the other or repeat pages."""
    store=SQLiteUserIOStore(tmp_path/'pages.sqlite3');service=UserIOService(store,Generator(),Outbox())
    for i in range(6):
        service.receive(InboxMessage('sms',str(i),'exact-'+str(i),'архипелаг',i+1),route_id='sms')
    fuzzy,_=service.receive(InboxMessage('sms','fuzzy','fuzzy','архипилаг',100),route_id='sms')
    a=search_conversations(store,'архипелаг',match_type='exact',limit=3)
    b=search_conversations(store,'архипелаг',match_type='exact',limit=3,offset=3)
    assert len(a)==len(b)==3
    assert not {r['id'] for r in a}&{r['id'] for r in b}
    assert search_conversations(store,'архипелаг',match_type='fuzzy',limit=3)[0]['id']==fuzzy
    assert not search_conversations(store,'архипелаг',match_type='exact',source='telegram')


def test_interrupted_initial_index_rolls_back_and_retries(tmp_path):
    """Fast unit expected2s/max20s: interrupted migration must never leave an empty index marked ready."""
    import pytest
    from universal_userio.search import initialize_search
    store=SQLiteUserIOStore(tmp_path/'atomic.sqlite3');service=UserIOService(store,Generator(),Outbox())
    service.receive(InboxMessage('sms','old','person','архипелаг',1),route_id='sms')
    db=store._connection
    with db:
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'search_%'").fetchall():
            db.execute('DROP TRIGGER '+name)
        db.execute('DROP TABLE search_fts');db.execute('DROP TABLE search_documents');db.execute('DROP TABLE search_profiles')
    class Interrupted:
        def create_function(self,*args,**kwargs):return db.create_function(*args,**kwargs)
        def execute(self,sql,*args):
            if sql.startswith('INSERT INTO search_documents(user_id,conversation_id,kind,message_rowid,text)'):
                raise RuntimeError('simulated interruption')
            return db.execute(sql,*args)
    store._connection=Interrupted()
    try:
        with pytest.raises(RuntimeError,match='simulated interruption'):initialize_search(store)
    finally:store._connection=db
    assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='search_documents'").fetchone()
    initialize_search(store)
    assert search_conversations(store,'архипелаг',match_type='exact')
    store.close()
    reopened=SQLiteUserIOStore(tmp_path/'atomic.sqlite3')
    assert search_conversations(reopened,'архипелаг',match_type='exact')
    reopened.close()
