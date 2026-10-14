"""Bounded read-only NoticePlace context for the owner's incoming calls."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sqlite3
import time
from datetime import datetime
from zoneinfo import ZoneInfo


AUTOSELL_ALIASES = ('autosell', 'autofindclient', 'автопродаж', 'автоселлер')


def service_search(query):
    text = str(query or '').casefold()
    return 'autosell' if any(alias in text for alias in AUTOSELL_ALIASES) else ''


def asks_recent_repair(question):
    return bool(re.search(r'\b(?:исправ(?:ил\w*|лен\w*)|закрыва\w*|закрыл\w*|'
                          r'восстанов\w*|почини\w*|устрани\w*|прошл\w*|раньше|вчера)\b',
                          str(question or '').casefold()))


def safe_text(value, limit=1200):
    text = str(value or "")
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"\b(?:Bearer\s+\S+|sk-[A-Za-z0-9_-]{10,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)", "[redacted]", text)
    text = re.sub(r"(?i)((?:api[_-]?key|password|secret|token)\s*[=:]\s*)[\"']?[^\s,;\"']+", r"\1[redacted]", text)
    return " ".join(text.split())[:limit]


class NoticePlaceVoiceStore:
    def __init__(self, database=None, recipient='me', *, owner_audiences=False):
        selected = str(database) if database is not None else os.environ.get('NOTICEPLACE_DB', '/var/lib/notification-center/notify-center.sqlite3')
        self.database = selected if selected.startswith('mesh:') else Path(selected)
        self.recipient = recipient
        # This broader scope is explicit in the authenticated owner-call MCP.
        # Other recipients and the ordinary single-recipient reader stay separate.
        self.recipients = ('me', 'health') if owner_audiences else (recipient,)

    def _connect(self):
        if str(self.database).startswith('mesh:'):
            from notification_center.mesh_store import connect
            return connect(str(self.database), readonly=True)
        connection = sqlite3.connect(self.database.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.create_function('voice_casefold', 1, lambda value: str(value or '').casefold())
        return connection

    @staticmethod
    def _summary(row):
        return {key: safe_text(row[key], 600 if key == 'title' else 128)
                for key in ('id', 'project', 'state', 'severity', 'title')} | {
                    'updated_at': datetime.fromtimestamp(row['updated_at'], ZoneInfo('Europe/Moscow')).isoformat(),
                    'resolved_at': datetime.fromtimestamp(row['resolved_at'], ZoneInfo('Europe/Moscow')).isoformat() if row['resolved_at'] else None}

    def list_incidents(self, query='', include_resolved=False):
        # Only real incident records; bounded recent recovery lookup is explicit.
        where = 'recipient IN (' + ','.join('?' for _ in self.recipients) + ") AND kind='incident' AND event_type IS NOT 'operator.call_test'"
        params = list(self.recipients)
        if include_resolved:
            where += " AND (state!='resolved' OR resolved_at>=?)"
            params.append(time.time() - 86400)
        else:
            where += " AND state!='resolved'"
        # Apply service aliases to all audience-scoped cards before LIMIT. A
        # natural question is not an AND of "why", "notifications", etc.
        # A generic fleet card may list autosell units in its body without
        # being an autosell incident. Service identity comes from project/title.
        search_text = "voice_casefold(COALESCE(title,'')||' '||COALESCE(project,''))"
        terms = [] if service_search(query) else str(query)[:128].casefold().split()[:8]
        if service_search(query):
            where += ' AND (' + ' OR '.join(search_text + ' LIKE ?' for _ in AUTOSELL_ALIASES) + ')'
            params.extend('%' + alias + '%' for alias in AUTOSELL_ALIASES)
        for term in terms:
            where += " AND voice_casefold(COALESCE(title,'')||' '||COALESCE(body,'')||' '||project) LIKE ? ESCAPE '\\'"
            params.append('%' + term.replace('\\','\\\\').replace('%','\\%').replace('_','\\_') + '%')
        connection = self._connect()
        try:
            total = connection.execute('SELECT count(*) FROM incidents WHERE ' + where, params).fetchone()[0]
            rows = connection.execute('SELECT id,project,state,severity,title,body,updated_at,resolved_at FROM incidents WHERE '
                + where + " ORDER BY CASE severity WHEN 'emergency' THEN 6 WHEN 'critical' THEN 5 WHEN 'important' THEN 4 "
                "WHEN 'notice' THEN 3 WHEN 'info' THEN 2 ELSE 1 END DESC,updated_at DESC LIMIT 20", params).fetchall()
            items = [self._summary(row) for row in rows]
            return {'total_in_scope': total, 'returned': len(items), 'truncated': total>20,
                    'audiences': list(self.recipients),
                    'includes_recent_resolved': bool(include_resolved), 'incidents': items,
                    'observed_at': datetime.now(ZoneInfo('Europe/Moscow')).isoformat(),
                    'state_source': 'NoticePlace cards; session_read is required to confirm actual investigation progress'}
        finally:
            connection.close()

    def incident(self, incident_id):
        connection = self._connect()
        try:
            row = connection.execute("SELECT * FROM incidents WHERE id=? AND recipient IN ("
                + ','.join('?' for _ in self.recipients) + ") AND kind='incident' AND event_type IS NOT 'operator.call_test'",
                (incident_id, *self.recipients)).fetchone()
            if row is None:
                raise ValueError('Incident is not available in this incoming call')
            result = self._summary(row)
            result['body'] = safe_text(row['body'], 2000)
            binding = connection.execute("SELECT payload_json FROM events WHERE incident_id=? AND event_type IN "
                "('health.diagnosis_session_started','health.orchestrator_session_started','health.remediation_session_started') "
                "ORDER BY created_at DESC,rowid DESC LIMIT 1", (incident_id,)).fetchone()
            session = json.loads(binding['payload_json']) if binding else {}
            harness = str(session.get('harness') or '').lower()
            session_id = str(session.get('session_id') or '')[:256]
            result['has_session'] = bool(session_id and harness in {'codex','hermes','opencode','zcode'})
            result['session_harness'] = harness if result['has_session'] else ''
            result['session_id'] = session_id if result['has_session'] else ''
            return result
        finally:
            connection.close()
