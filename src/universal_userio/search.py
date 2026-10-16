"""Persistent, user-scoped exact-first search over contacts and retained messages."""
from __future__ import annotations

import re
import sqlite3
import time
import unicodedata
from typing import Any, Mapping, Sequence

from .semantic_index import body_hash
from urllib.parse import quote
from functools import lru_cache


def normalize(value: object) -> str:
    text = unicodedata.normalize('NFKC', str(value or '')).casefold().replace('ё', 'е')
    # Telephone formatting and @ are presentation, not identity.
    text = re.sub(r'\+?\d[\d ()-]{5,}\d', lambda m: re.sub(r'\D', '', m[0]), text)
    return ' '.join(re.findall(r'\w+', text))


def _execute_schema(db: Any, script: str) -> None:
    # sqlite3.executescript commits implicitly; keep index creation/backfill atomic.
    statement = ""
    for char in script:
        statement += char
        if char == ";" and sqlite3.complete_statement(statement):
            db.execute(statement)
            statement = ""


def initialize_search(store: Any) -> None:
    db = store._connection
    db.execute("SAVEPOINT userio_search_init")
    try:
        _initialize_search(store)
    except BaseException:
        db.execute("ROLLBACK TO userio_search_init")
        db.execute("RELEASE userio_search_init")
        raise
    db.execute("RELEASE userio_search_init")


def _initialize_search(store: Any) -> None:
    db = store._connection
    db.create_function('userio_search_text', 1, normalize, deterministic=True)
    db.execute('''CREATE TABLE IF NOT EXISTS search_profiles (
        user_id TEXT NOT NULL,conversation_id TEXT NOT NULL,username TEXT NOT NULL DEFAULT '',
        first_name TEXT NOT NULL DEFAULT '',last_name TEXT NOT NULL DEFAULT '',phone TEXT NOT NULL DEFAULT '',
        PRIMARY KEY(user_id,conversation_id))''')
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='search_documents'").fetchone():
        return
    _execute_schema(db, '''
        CREATE TABLE search_documents (
            id INTEGER PRIMARY KEY,user_id TEXT NOT NULL,conversation_id TEXT NOT NULL,
            kind TEXT NOT NULL,message_rowid INTEGER UNIQUE,text TEXT NOT NULL);
        CREATE INDEX search_documents_chat ON search_documents(user_id,conversation_id,kind);
        CREATE UNIQUE INDEX search_documents_contact ON search_documents(user_id,conversation_id) WHERE kind='contact';
        CREATE VIRTUAL TABLE search_fts USING fts5(text,content='search_documents',content_rowid='id',tokenize='trigram');
        CREATE TRIGGER search_doc_insert AFTER INSERT ON search_documents BEGIN
          INSERT INTO search_fts(rowid,text) VALUES(new.id,new.text); END;
        CREATE TRIGGER search_doc_delete AFTER DELETE ON search_documents BEGIN
          INSERT INTO search_fts(search_fts,rowid,text) VALUES('delete',old.id,old.text); END;
        CREATE TRIGGER search_doc_update AFTER UPDATE OF text ON search_documents BEGIN
          INSERT INTO search_fts(search_fts,rowid,text) VALUES('delete',old.id,old.text);
          INSERT INTO search_fts(rowid,text) VALUES(new.id,new.text); END;
    ''')
    contact_select = '''SELECT c.user_id,c.id,'contact',userio_search_text(
        c.sender||' '||COALESCE(c.identity_id,'')||' '||COALESCE(n.name,'')||' '||
        COALESCE(p.username,'')||' '||COALESCE(p.first_name,'')||' '||COALESCE(p.last_name,'')||' '||COALESCE(p.phone,''))
        FROM conversations c LEFT JOIN contact_names n ON n.user_id=c.user_id AND n.source=c.source
        AND n.sender=CASE WHEN c.source IN ('max','phone','vk','yandex') AND c.account_ref!='' AND c.peer_id!=''
          THEN 'chat:'||c.account_ref||':'||c.peer_id ELSE c.sender END
        LEFT JOIN search_profiles p ON p.user_id=c.user_id AND p.conversation_id=c.id'''
    # Triggers cover ingress, edits, deletion, renames, profile refresh and direct SQL maintenance.
    for event, ref in [('INSERT','new'),('UPDATE OF sender,identity_id,account_ref,peer_id','new'),('DELETE','old')]:
        suffix = event.split()[0].lower()
        insert = '' if suffix == 'delete' else ('INSERT INTO search_documents(user_id,conversation_id,kind,text) '+contact_select+f' WHERE c.user_id={ref}.user_id AND c.id={ref}.id;')
        _execute_schema(db, f'''CREATE TRIGGER search_chat_{suffix} AFTER {event} ON conversations BEGIN
          DELETE FROM search_documents WHERE user_id={ref}.user_id AND conversation_id={ref}.id AND kind='contact';
          {insert} END;''')
    for table in ['contact_names','search_profiles']:
        for event, ref in [('INSERT','new'),('UPDATE','new'),('DELETE','old')]:
            where = f'c.user_id={ref}.user_id AND c.id={ref}.conversation_id' if table=='search_profiles' else f"c.user_id={ref}.user_id AND c.source={ref}.source AND (c.sender={ref}.sender OR 'chat:'||c.account_ref||':'||c.peer_id={ref}.sender)"
            _execute_schema(db, f'''CREATE TRIGGER search_{table}_{event.lower()} AFTER {event} ON {table} BEGIN
              DELETE FROM search_documents WHERE kind='contact' AND user_id={ref}.user_id AND conversation_id IN (SELECT c.id FROM conversations c WHERE {where});
              INSERT INTO search_documents(user_id,conversation_id,kind,text) {contact_select} WHERE {where}; END;''')
    for event, ref in [('INSERT','new'),('UPDATE OF body,sender,deleted_at,conversation_id','new'),('DELETE','old')]:
        suffix=event.split()[0].lower()
        insert='' if suffix=='delete' else '''INSERT INTO search_documents(user_id,conversation_id,kind,message_rowid,text)
          SELECT new.user_id,new.conversation_id,'message',new.rowid,userio_search_text(new.body||' '||new.sender) WHERE new.deleted_at IS NULL;'''
        _execute_schema(db, f'''CREATE TRIGGER search_message_{suffix} AFTER {event} ON messages BEGIN
          DELETE FROM search_documents WHERE message_rowid={ref}.rowid; {insert} END;''')
    db.execute('INSERT INTO search_documents(user_id,conversation_id,kind,text) '+contact_select)
    db.execute('''INSERT INTO search_documents(user_id,conversation_id,kind,message_rowid,text)
        SELECT user_id,conversation_id,'message',rowid,userio_search_text(body||' '||sender) FROM messages WHERE deleted_at IS NULL''')


def save_profile(store: Any, user_id: str, conversation_id: str, profile: dict) -> None:
    fields = tuple(str(profile.get(k) or '')[:256] for k in ('username','first_name','last_name','phone'))
    if not any(fields):
        return
    store._connection.execute('''INSERT INTO search_profiles(user_id,conversation_id,username,first_name,last_name,phone)
      VALUES (?,?,?,?,?,?) ON CONFLICT(user_id,conversation_id) DO UPDATE SET
      username=excluded.username,first_name=excluded.first_name,last_name=excluded.last_name,phone=excluded.phone
      WHERE (username,first_name,last_name,phone)!=(excluded.username,excluded.first_name,excluded.last_name,excluded.phone)''',
      (user_id,conversation_id,*fields))


def _distance(a: str, b: str, maximum: int) -> int:
    """Bounded Damerau edit distance: includes adjacent swapped characters."""
    if abs(len(a)-len(b))>maximum:
        return maximum+1
    previous=list(range(len(b)+1)); before=None
    for i,x in enumerate(a,1):
        row=[i]
        for j,y in enumerate(b,1):
            value=min(row[-1]+1,previous[j]+1,previous[j-1]+(x!=y))
            if before is not None and i>1 and j>1 and x==b[j-2] and a[i-2]==y:
                value=min(value,before[j-2]+1)
            row.append(value)
        if min(row)>maximum:
            return maximum+1
        before,previous=previous,row
    return previous[-1]


def source_clause(source: str) -> str:
    """Platform families accept their per-account variants too."""
    if source in {'mail', 'email', 'gmail'}:
        return "(c.source IN ('mail','email','gmail') OR c.source LIKE 'gmail:%' OR c.source LIKE 'email:%')"
    if source == 'matrix':
        return "(c.source='matrix' OR c.source LIKE 'matrix:%')"
    return 'c.source=?'


def conversation_filter(conversation_ids: Sequence[str]) -> tuple[str, list[object]]:
    """Server-side prefilter. An empty allow-list means the whole owner scope."""
    ids = [str(value).strip() for value in conversation_ids if str(value).strip()][:100]
    if not ids:
        return '', []
    return 'c.id IN (%s)' % ','.join('?' * len(ids)), ids


def deep_link(conversation_id: str, message_id: str = '', base_url: str = '') -> str:
    """Working link to the exact source message inside UserIO."""
    link = f"{(base_url or 'https://msg.bezrabotnyi.com').rstrip('/')}/#/chat/{quote(str(conversation_id))}"
    return f"{link}?m={quote(str(message_id))}" if message_id else link


def provider_link(row: Mapping[str, Any]) -> str:
    """Provider link only where the platform genuinely supports one."""
    if str(row.get('source') or '') != 'telegram':
        return ''
    username = str(row.get('peer_username') or '').lstrip('@')
    if username and username.replace('_', '').isalnum():
        return f"https://t.me/{username}"
    return ''


MESSAGE_SELECT = '''SELECT d.id AS document_id,c.id AS conversation_id,d.message_rowid,
      m.message_id,m.body,m.received_at,c.source,c.sender,c.account_ref,c.peer_id,
      c.conversation_kind,m.direction,'' AS sender_name,
      COALESCE(p.username,'') AS peer_username,
      COALESCE(n.name,'') AS display_name
    FROM search_documents d JOIN conversations c ON c.id=d.conversation_id AND c.user_id=d.user_id
    JOIN messages m ON m.rowid=d.message_rowid AND m.user_id=d.user_id
    LEFT JOIN search_profiles p ON p.user_id=c.user_id AND p.conversation_id=c.id
    LEFT JOIN contact_names n ON n.user_id=c.user_id AND n.source=c.source AND n.sender=
      CASE WHEN c.source IN ('max','phone','vk','yandex') AND c.account_ref!='' AND c.peer_id!=''
      THEN 'chat:'||c.account_ref||':'||c.peer_id ELSE c.sender END'''

_CHANNELS = {'gmail', 'mail', 'email', 'telegram', 'vk', 'max', 'sms', 'chatgpt', 'whatsapp', 'matrix'}


def _provider_id(account_ref: str, message_id: str) -> str:
    """Numeric provider id of a stored scoped message id, when it has one."""
    prefix = f"{account_ref}|" if account_ref else ""
    if prefix and message_id.startswith(prefix):
        remainder = message_id[len(prefix):]
        return remainder.rsplit(":", 1)[1] if ":" in remainder else remainder
    return ""


def _hit(row: Mapping[str, Any], *, kind: str, score: float, base_url: str = '') -> dict[str, Any]:
    """One search result: provenance, who wrote it, and a working link.

    ``sender`` is the chat counterpart, which is *not* the author of an outgoing
    message. ``author`` and ``direction`` come from the canonical projection, so
    a source-backed answer can never put the owner's own words in someone else's
    mouth.
    """
    from .source_projection import canonical_author, canonical_source

    body = str(row['body'] or '')
    conversation_id = str(row['conversation_id'])
    message_id = str(row['message_id'] or '')
    account_ref = str(row['account_ref'] or '')
    source = str(row['source'])
    channel = source.partition(':')[0] if source.partition(':')[0] in _CHANNELS else source
    direction = str(row['direction'] or 'incoming')
    author = canonical_author(
        channel=channel, account_ref=account_ref,
        conversation_kind=str(row['conversation_kind'] or ''), peer_id=str(row['peer_id'] or ''),
        direction=direction, sender=str(row['sender']),
        sender_name=str(row['sender_name'] or ''))
    coordinates = canonical_source(
        account_ref=account_ref, conversation_id=conversation_id, peer_id=str(row['peer_id'] or ''),
        conversation_kind=str(row['conversation_kind'] or ''), channel=channel,
        message_id=message_id, provider_message_id=_provider_id(account_ref, message_id),
        direction=direction)
    return {
        'conversation_id': conversation_id, 'message_id': message_id,
        'source': source, 'channel': channel, 'account_ref': account_ref,
        'peer_id': str(row['peer_id'] or ''), 'sender': str(row['sender']),
        'display_name': str(row['display_name'] or ''), 'received_at': row['received_at'],
        'direction': direction, 'author': author,
        'provider_message_id': coordinates['provider_message_id'],
        'thread_id': coordinates['thread_id'],
        'reply_to_provider_message_id': coordinates['reply_to_provider_message_id'],
        'snippet': body[:300], 'match_type': kind, 'score': round(float(score), 6),
        'deep_link': deep_link(conversation_id, message_id, base_url),
        'provider_link': provider_link(row),
    }


def _message_scope(store: Any, *, user_id: str, source: str | None, account_ref: str | None,
                   conversation_ids: Sequence[str]) -> tuple[list[str], list[object]]:
    where = ['d.user_id=?', 'm.user_id=?', "d.kind='message'", 'm.deleted_at IS NULL',
             'm.conversation_id=c.id',
             "NOT EXISTS (SELECT 1 FROM phone_conversation_aliases a"
             " WHERE a.user_id=c.user_id AND a.old_id=c.id)"]
    args: list[object] = [user_id, user_id]
    if source:
        where.append(source_clause(source))
        if source_clause(source) == 'c.source=?':
            args.append(source)
    if account_ref:
        where.append('c.account_ref=?')
        args.append(account_ref)
    clause, clause_args = conversation_filter(conversation_ids)
    if clause:
        where.append(clause)
        args += clause_args
    return where, args


def _exact_message_rows(store: Any, where: list[str], args: list[object],
                        tokens: Sequence[str], *, offset: int, limit: int) -> list[Any]:
    """Trigram-indexed exact pass. Paged in SQL, so no hit is silently dropped."""
    quoted = lambda token: '"' + token.replace('"', '""') + '"'
    indexed = [token for token in tokens if len(token) >= 3]
    clause = 'd.id IN (SELECT rowid FROM search_fts WHERE search_fts MATCH ?)' if indexed else '1=1'
    clause_args = [*args, *([' AND '.join(map(quoted, indexed))] if indexed else [])]
    for token in tokens:
        clause += ' AND instr(d.text,?)>0'
        clause_args.append(token)
    return store._connection.execute(
        MESSAGE_SELECT + ' WHERE ' + ' AND '.join(where) + ' AND ' + clause
        + ' ORDER BY m.received_at DESC,d.id DESC LIMIT ? OFFSET ?',
        [*clause_args, limit, offset]).fetchall()


def _fuzzy_message_rows(store: Any, where: list[str], args: list[object], tokens: Sequence[str],
                        *, offset: int, limit: int, seen: set[int]) -> list[tuple[dict[str, Any], float]]:
    """Typo-tolerant pass over the same rows, ranked and paged.

    Candidates stream from the trigram index in document order, so a long
    history is never truncated by an arbitrary scan ceiling.
    """
    quoted = lambda token: '"' + token.replace('"', '""') + '"'
    trigrams = set()
    for token in tokens:
        if len(token) >= 6:
            trigrams.update(token[index:index + 3] for index in range(len(token) - 2))
    near = lru_cache(maxsize=32768)(_distance)
    found: dict[int, tuple[tuple[int, float], dict[str, Any]]] = {}
    cursor = 0
    pages = 0
    max_pages = max(4, min(64, 2 + (offset + limit) // 50))
    while len(found) < offset + limit and pages < max_pages:
        pages += 1
        clause = ' AND '.join(where) + ' AND d.id>?' + (
            ' AND d.id IN (SELECT rowid FROM search_fts WHERE search_fts MATCH ?)' if trigrams else '')
        page_args = [*args, cursor, *([' OR '.join(map(quoted, sorted(trigrams)))] if trigrams else [])]
        rows = store._connection.execute(
            MESSAGE_SELECT + ' WHERE ' + clause + ' ORDER BY d.id LIMIT 2000', page_args).fetchall()
        if not rows:
            break
        cursor = max(int(row['document_id']) for row in rows)
        for row in rows:
            rowid = int(row['message_rowid'])
            if rowid in seen:
                continue
            body = str(row['body'] or '')
            words = [word for word in re.findall(r'\w+', body)] or [body]
            score = 0
            for token in tokens:
                if token in body:
                    continue
                maximum = 2 if len(token) >= 9 else 1
                if len(token) < 4:
                    score = 99
                    break
                best = min((near(token, word, maximum) for word in set(words)
                            if abs(len(word) - len(token)) <= maximum), default=99)
                if best > maximum:
                    score = 99
                    break
                score += best
            if score == 99 or score == 0:
                continue
            rank = (score, -float(row['received_at'] or 0))
            if rowid not in found or rank < found[rowid][0]:
                found[rowid] = (rank, dict(row))
        if len(rows) < 2000:
            break
    ranked = sorted(found.values(), key=lambda item: item[0])
    return [(row, 1.0 / (1.0 + float(rank[0]))) for rank, row in ranked[offset:offset + limit]]


def text_coverage(store: Any, *, user_id: str) -> dict[str, Any]:
    messages = store._connection.execute(
        "SELECT COUNT(*) FROM messages WHERE user_id=? AND deleted_at IS NULL"
        " AND length(trim(coalesce(body,'')))>0", (user_id,)).fetchone()[0]
    conversations = store._connection.execute(
        "SELECT COUNT(*) FROM conversations WHERE user_id=?", (user_id,)).fetchone()[0]
    return {"messages": int(messages), "conversations": int(conversations), "exact": True}


def search_messages(store: Any, query: str, *, source: str | None = None, account_ref: str | None = None,
                    limit: int = 50, user_id: str | None = None, match_type: str = 'all',
                    offset: int = 0, conversation_ids: Sequence[str] = (), mode: str = 'text',
                    base_url: str = '') -> dict[str, Any]:
    """Message-level search over the whole retained history.

    One hit per matching message, so no conversation grouping can hide a match.
    The ``conversation_ids`` allow-list is applied inside the query and every row
    is re-read from the live message table, so deleted or edited text is never
    emitted from an index.
    """
    from . import semantic_index

    text = normalize(query)[:256]
    tokens = text.split()
    uid = store._user(user_id)
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    coverage = text_coverage(store, user_id=uid)
    if not tokens or len(text) < 2:
        return {'results': [], 'coverage': coverage, 'mode_used': 'none', 'semantic': {'available': False}}

    where, args = _message_scope(store, user_id=uid, source=source, account_ref=account_ref,
                                 conversation_ids=conversation_ids)
    selected: list[tuple[dict[str, Any], str, float]] = []
    seen: set[int] = set()
    if mode in {'text', 'hybrid'}:
        with store._lock:
            rows = _exact_message_rows(store, where, args, tokens, offset=offset, limit=limit)
            selected += [(dict(row), 'exact', 1.0) for row in rows]
            seen |= {int(row['message_rowid']) for row in rows}
            if match_type != 'exact':
                for row, score in _fuzzy_message_rows(store, where, args, tokens, offset=offset,
                                                      limit=limit, seen=seen):
                    selected.append((row, 'fuzzy', score))

    semantic = {'available': False, 'reason': 'not_requested'}
    settings = semantic_index.resolve_settings(store, user_id=uid)
    if mode in {'semantic', 'hybrid'}:
        semantic = {'available': False, 'reason': 'not_configured'}
        if settings.enabled and settings.configured():
            try:
                candidates = semantic_index.search(
                    store, settings, query, user_id=uid, conversation_ids=conversation_ids,
                    limit=min(200, (offset + limit) * 3))
                # Paging applies to the merged, ranked answer, so a deep page
                # must not repeat the candidates the caller already saw.
                fresh = [candidate for candidate in candidates
                         if int(candidate['message_rowid']) not in seen]
                for candidate in fresh[offset:offset + limit]:
                    row = _live_message(store, uid, int(candidate['message_rowid']), account_ref,
                                        expect_hash=str(candidate.get('body_sha256') or ''))
                    if row is None:
                        continue
                    rowid = int(row['message_rowid'])
                    if rowid in seen:
                        continue
                    selected.append((row, 'semantic', float(candidate['score'])))
                    seen.add(rowid)
                semantic = {'available': True, 'reason': '', 'dimensions': settings.dimensions,
                            'model': settings.embedding_model, 'candidates': len(candidates)}
            except semantic_index.SemanticUnavailable as error:
                semantic = {'available': False, 'reason': error.reason}
    order = {'exact': 0, 'semantic': 1, 'fuzzy': 2}
    selected.sort(key=lambda item: (order.get(item[1], 3), -item[2]))
    results = [_hit(row, kind=kind, score=score, base_url=base_url)
               for row, kind, score in selected[:limit]]
    return {'results': results, 'coverage': coverage, 'mode_used': _mode_used(semantic, mode, selected),
            'semantic': semantic}


def _live_message(store: Any, uid: str, message_rowid: int, account_ref: str | None, *,
                  expect_hash: str = '') -> dict[str, Any] | None:
    """Re-read one candidate under the permissions that apply right now.

    The index contributes identity only. A candidate is emitted only when the
    live row still exists, is not deleted, belongs to this user and conversation,
    still carries the text that was indexed, and its account is still enabled
    for reading. Anything else is dropped and, when the text moved on, queued
    for re-indexing.
    """
    row = store._connection.execute(
        MESSAGE_SELECT + ' WHERE m.rowid=? AND m.user_id=? AND m.deleted_at IS NULL'
        + (' AND c.account_ref=?' if account_ref else ''),
        [message_rowid, uid, account_ref] if account_ref else [message_rowid, uid]).fetchone()
    if row is None:
        return None
    if expect_hash and body_hash(str(row['body'] or '')) != expect_hash:
        # The text changed after indexing: this ranking no longer describes it.
        store._connection.execute(
            "INSERT INTO search_vector_queue(message_rowid,operation,created_at) VALUES(?, 'upsert', ?)"
            " ON CONFLICT(message_rowid) DO UPDATE SET operation='upsert',created_at=excluded.created_at",
            (message_rowid, time.time()))
        store._connection.commit()
        return None
    if not _account_readable(store, uid, str(row['account_ref'] or '')):
        return None
    return dict(row)


def _account_readable(store: Any, uid: str, account_ref: str) -> bool:
    """Revoked or disabled scope never reaches a result."""
    if not account_ref:
        return True
    return any(str(item['id']) == account_ref and item['enabled']
               and 'read' in (item['capabilities'] or ())
               for item in store.accounts(user_id=uid))


def _mode_used(semantic: Mapping[str, Any], requested: str,
               selected: Sequence[tuple[Any, str, float]] = ()) -> str:
    """What actually answered this call: text, semantic, or both."""
    kinds = {kind for _, kind, _ in selected}
    if requested == 'text':
        return 'text'
    if not semantic.get('available'):
        return 'text'
    if requested == 'hybrid' and 'exact' in kinds and 'semantic' in kinds:
        return 'hybrid'
    return 'semantic' if 'semantic' in kinds else 'text'


PUBLIC_WEB_URL = "https://msg.bezrabotnyi.com"
INDEX_STEP_BUDGET_SECONDS = 240


def run_search_index(service: Any, payload: Mapping[str, Any] | None = None, *,
                     user_id: str | None = None) -> dict[str, Any]:
    """One bounded, resumable index slice: drain live edits, then advance.

    The caller decides how often to run it. Nothing here blocks longer than the
    step budget, and every slice records its own checkpoint, so a restart or a
    cancelled run continues instead of starting over.
    """
    from . import semantic_index

    store = service._store
    payload = payload or {}
    uid = store._user(user_id)
    settings = semantic_index.resolve_settings(store, user_id=uid)
    if not settings.enabled or not settings.configured():
        return {**semantic_index.status(store, user_id=uid), "ran": False,
                "reason": "not_configured", "text_only": True}
    try:
        limit = max(1, min(int(payload.get("messages", 200)), semantic_index.MAX_CHUNK_ROWS))
    except (TypeError, ValueError):
        limit = 200
    budget = max(10.0, min(float(payload.get("budget_seconds", INDEX_STEP_BUDGET_SECONDS)), 1800.0))
    deadline = time.monotonic() + budget
    drained = semantic_index.drain_queue(store, settings, user_id=uid, limit=limit, deadline=deadline)
    priority = store.user_search_settings_public(user_id=uid).get("priority_conversations") or []
    step = semantic_index.backfill_step(store, settings, user_id=uid, limit=limit,
                                        deadline=max(deadline, time.monotonic() + 1.0),
                                        priority_conversation_ids=priority)
    store._connection.commit()
    scoped = semantic_index.status(store, user_id=uid, conversation_ids=priority) if priority else {}
    return {**semantic_index.status(store, user_id=uid),
            "ran": True, "reason": "", "drained": drained, "step": step,
            "priority": scoped, "budget_seconds": budget}


def search_coverage(store: Any, *, user_id: str | None = None,
                    environment: dict[str, str] | None = None) -> dict[str, Any]:
    """Index readiness for the owner surface. Never raises, never leaks secrets."""
    from . import semantic_index

    uid = store._user(user_id)
    settings = semantic_index.resolve_settings(store, user_id=uid, environment=environment)
    coverage = text_coverage(store, user_id=uid)
    semantic = semantic_index.probe(settings) if settings.enabled else {
        'available': False, 'reason': 'not_configured'}
    vector = semantic_index.status(store, user_id=uid) if settings.enabled else {}
    priority = store.user_search_settings_public(user_id=uid).get('priority_conversations') or []
    scoped = semantic_index.status(store, user_id=uid, conversation_ids=priority) if priority else {}
    return {
        'text': coverage,
        'semantic': {**semantic,
                     'enabled': settings.enabled, 'collection': settings.qdrant_collection,
                     'model': settings.embedding_model, **vector},
        # A scoped slice is reported next to the global numbers so a partial
        # index is never presented as full coverage of the whole history.
        'scope': {'conversation_ids': priority, **(scoped or {})},
        'text_only': not bool(semantic.get('available')),
    }


def search_conversations(store: Any, query: str, *, source: str|None=None, account_ref: str|None=None,
                         limit: int=100, user_id: str|None=None,
                         match_type: str='all', offset: int=0) -> list[dict[str,object]]:
    """Conversation-collapsed search for the chat list. Message-level callers use
    :func:`search_messages`, which has no scan ceiling and no conversation grouping."""
    text=normalize(query)[:256]; tokens=text.split()
    if not tokens or len(text)<2:
        return []
    uid=store._user(user_id); limit=max(1,min(int(limit),500)); offset=max(0,int(offset))
    where=['d.user_id=?','c.user_id=?',"NOT EXISTS (SELECT 1 FROM phone_conversation_aliases a WHERE a.user_id=c.user_id AND a.old_id=c.id)"]
    args: list[object]=[uid,uid]
    if source:
        if source in {'mail','email','gmail'}:
            where.append("(c.source IN ('mail','email','gmail') OR c.source LIKE 'gmail:%' OR c.source LIKE 'email:%')")
        elif source=='matrix': where.append("(c.source='matrix' OR c.source LIKE 'matrix:%')")
        else: where.append('c.source=?'); args.append(source)
    if account_ref: where.append('c.account_ref=?'); args.append(account_ref)
    base='''SELECT d.*,c.source,c.sender,c.identity_id,c.account_ref,c.updated_at,
      m.message_id,m.body,m.received_at,
      (SELECT name FROM contact_names n WHERE n.user_id=c.user_id AND n.source=c.source AND n.sender=
        CASE WHEN c.source IN ('max','phone','vk','yandex') AND c.account_ref!='' AND c.peer_id!='' THEN 'chat:'||c.account_ref||':'||c.peer_id ELSE c.sender END) AS display_name
      FROM search_documents d JOIN conversations c ON c.id=d.conversation_id AND c.user_id=d.user_id
      LEFT JOIN messages m ON m.rowid=d.message_rowid'''
    quoted=lambda t:'"'+t.replace('"','""')+'"'
    indexed=[t for t in tokens if len(t)>=3]
    exact_clause=('d.id IN (SELECT rowid FROM search_fts WHERE search_fts MATCH ?)' if indexed else '1=1')
    exact_args=[*args,*([' AND '.join(map(quoted,indexed))] if indexed else [])]
    for token in tokens:
        exact_clause+=' AND instr(d.text,?)>0'; exact_args.append(token)
    with store._lock:
        # Collapse in SQL, before LIMIT: each page contains distinct conversations.
        exact=store._connection.execute('''WITH hits AS ('''+base+' WHERE '+' AND '.join(where)+' AND '+exact_clause+'''), ranked AS (
          SELECT *,ROW_NUMBER() OVER (PARTITION BY conversation_id ORDER BY (kind='contact') DESC,received_at DESC,id DESC) AS rn FROM hits)
          SELECT * FROM ranked WHERE rn=1 ORDER BY (kind='contact') DESC,COALESCE(received_at,updated_at) DESC,conversation_id LIMIT ? OFFSET ?''',
          [*exact_args,limit if match_type!='fuzzy' else 100000,offset if match_type!='fuzzy' else 0]).fetchall()
        selected=[(dict(r),'exact') for r in exact] if match_type!='fuzzy' else []
        if match_type!='exact':
            # Trigram index narrows long typo searches; short names inspect contacts only.
            chunks=set()
            for t in tokens:
                if len(t)>=6:
                    # All trigrams retain candidates when a typo destroys a middle chunk.
                    chunks.update(t[i:i+3] for i in range(len(t)-2))
            fuzzy_clause="d.kind='contact'"
            fuzzy_args=list(args)
            if chunks:
                fuzzy_clause="("+fuzzy_clause+" OR d.id IN (SELECT rowid FROM search_fts WHERE search_fts MATCH ?))"
                fuzzy_args.append(' OR '.join(map(quoted,sorted(chunks))))
            rows=store._connection.execute(base+' WHERE '+' AND '.join(where)+' AND '+fuzzy_clause+" ORDER BY (d.kind='contact') DESC,m.received_at DESC LIMIT 5000",fuzzy_args).fetchall()
            near = lru_cache(maxsize=32768)(_distance)
            found={}; exact_ids={str(r['conversation_id']) for r in exact}
            for r in rows:
                cid=str(r['conversation_id'])
                if cid in exact_ids: continue
                words=r['text'].split(); score=0
                for t in tokens:
                    if t in r['text']: continue
                    maximum=2 if len(t)>=9 else 1
                    if len(t)<4: score=99; break
                    dist=min((near(t,w,maximum) for w in set(words) if abs(len(w)-len(t))<=maximum),default=99)
                    if dist>maximum: score=99; break
                    score+=dist
                if score==99 or score==0: continue
                rank=(score,0 if r['kind']=='contact' else 1,-float(r['received_at'] or r['updated_at']))
                if cid not in found or rank<found[cid][0]: found[cid]=(rank,dict(r))
            fuzzy=[(r,'fuzzy') for _,r in sorted(found.values(),key=lambda x:x[0])]
            selected+=fuzzy[offset:offset+limit] if match_type=='fuzzy' else fuzzy[:max(0,limit-len(selected))]
        results=[]
        for r,kind in selected:
            cid=r['conversation_id']
            latest=store._connection.execute('''SELECT message_id,body,received_at FROM messages WHERE user_id=? AND conversation_id=? AND deleted_at IS NULL ORDER BY received_at DESC,rowid DESC LIMIT 1''',(uid,cid)).fetchone()
            message=r if r['kind']=='message' else (dict(latest) if latest else {})
            unread=store._connection.execute('''SELECT COUNT(*) FROM messages WHERE user_id=? AND conversation_id=? AND seen_at IS NULL AND deleted_at IS NULL''',(uid,cid)).fetchone()[0]
            results.append({'id':cid,'source':r['source'],'sender':r['sender'],'identity_id':r['identity_id'],
              'display_name':r['display_name'],'account_ref':r['account_ref'],'preview':str(message.get('body') or '')[:300],
              'unread_count':unread,'last_at':message.get('received_at') or r['updated_at'],
              'match_message_id':message.get('message_id') or '', 'match_body':str(message.get('body') or '')[:500],
              'match_type':kind,'match_field':'contact' if r['kind']=='contact' else 'message'})
        return results
