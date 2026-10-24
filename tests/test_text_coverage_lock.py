"""Retained text search must serialize aggregate reads on shared SQLite."""
from test_messenger import case
from test_semantic_status_lock import CheckedConnection
from universal_userio.search_surface import search_tool_response


def test_exact_phone_digits_search_serializes_text_coverage(case):
    store = case[0]
    uid = store.owner().user_id
    original = store._connection
    checked = CheckedConnection(original, store._lock)
    store._connection = checked
    try:
        result = search_tool_response(store, {
            'query': '900000000000', 'scope': 'conversation', 'mode': 'text',
            'match_type': 'exact', 'limit': 100, 'offset': 0,
        }, user_id=uid)
        assert result['ok'] and result['results'] == []
        assert result['coverage']['text'] == {'messages': 1, 'conversations': 1, 'exact': True}
        assert 'status' not in result  # A no-hit search is not NOT_CONTACTED.
    finally:
        store._connection = original
