"""Coverage reads must serialize the store's shared SQLite connection."""
import pytest

from test_messenger import case
from universal_userio.semantic_index import status


class CheckedConnection:
    def __init__(self, connection, lock):
        self.connection, self.lock = connection, lock
        self.executions = 0

    def execute(self, *args, **kwargs):
        # Python SQLite callbacks and a concurrent writer must not acquire
        # the connection mutex and store RLock in opposite orders.
        assert self.lock._is_owned(), 'semantic coverage used shared SQLite without store lock'
        self.executions += 1
        return self.connection.execute(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.connection, name)


@pytest.mark.parametrize('scope,expected', [('all', 1), ('exact', 1), ('foreign', 0)])
def test_semantic_coverage_serializes_all_connection_access(case, scope, expected):
    store, _, _, cid, _ = case
    uid = store.owner().user_id
    original = store._connection
    checked = CheckedConnection(original, store._lock)
    store._connection = checked
    try:
        scopes = () if scope == 'all' else (cid,) if scope == 'exact' else ('missing-conversation',)
        result = status(store, user_id=uid, conversation_ids=scopes)
        assert result['live_messages'] == expected
        assert result['scope_conversations'] == len(scopes)
        assert checked.executions >= 8
        # Existing callers may already hold this reentrant store lock.
        with store._lock:
            assert status(store, user_id=uid, conversation_ids=scopes) == result
    finally:
        store._connection = original
