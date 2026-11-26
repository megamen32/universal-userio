from universal_userio.store import SQLiteUserIOStore


def test_account_freshness_is_scoped_and_uses_bounded_message_work(tmp_path):
    store = SQLiteUserIOStore(tmp_path / 'userio.sqlite3')
    db = store._connection
    db.executemany(
        'INSERT INTO conversations(user_id,id,conversation_key,route_id,source,sender,updated_at,account_ref) VALUES (?,?,?,?,?,?,?,?)',
        [
            ('owner', 'one', 'key1', 'route', 'telegram', 'p', 1, 'telegram:one'),
            ('owner', 'two', 'key2', 'route', 'telegram', 'p', 1, 'telegram:two'),
            ('other', 'one', 'key1', 'route', 'telegram', 'p', 1, 'telegram:one'),
        ],
    )
    db.executemany(
        'INSERT INTO messages(user_id,source,message_id,conversation_id,sender,body,received_at) VALUES (?,?,?,?,?,?,?)',
        [('owner', 'telegram', str(i), 'one', 'p', '', i) for i in range(1, 8001)]
        + [('owner', 'telegram', 'other-account', 'two', 'p', '', 9000),
           ('other', 'telegram', 'other-user', 'one', 'p', '', 10000),
           ('owner', 'sms', 'other-source', 'one', 'p', '', 11000)],
    )
    db.commit()
    calls = 0

    def limit_work():
        nonlocal calls
        calls += 1
        return calls > 100

    db.set_progress_handler(limit_work, 1000)
    try:
        assert store.latest_message_at('telegram', user_id='owner', account_ref='telegram:one') == 8000
    finally:
        db.set_progress_handler(None, 0)
    assert store.latest_message_at('telegram', user_id='owner') == 9000
    assert store.latest_message_at('telegram', user_id='other', account_ref='telegram:one') == 10000
    assert store.latest_message_at('sms', user_id='owner', account_ref='telegram:one') == 11000
    assert store.latest_message_at('telegram', user_id='owner', account_ref='missing') is None
