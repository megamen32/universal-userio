"""Persistent, user-scoped exact-first search over contacts and retained messages."""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from typing import Any
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


def search_conversations(store: Any, query: str, *, source: str|None=None, account_ref: str|None=None,
                         limit: int=100, user_id: str|None=None, scan_limit: int=50000,
                         match_type: str='all', offset: int=0) -> list[dict[str,object]]:
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
