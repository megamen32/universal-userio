"""Bounded read-only NoticePlace context for the owner's incoming calls.

The context is read over the authenticated read API of NoticePlace. UserIO
never opens the NoticePlace store: a shared database file is not an API, and a
voice answer must not depend on the storage of another service.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo


AUTOSELL_ALIASES = ('autosell', 'autofindclient', 'автопродаж', 'автоселлер')
API_TIMEOUT_SECONDS = 6.0
OWNER_AUDIENCES = ('me', 'health')


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
    text = re.sub(r"(?i)((?:api[_-]?key|password|secret|token)\s*[=:]\s*)\"?'?[^\\s,;\"']+", r"\1[redacted]", text)
    return " ".join(text.split())[:limit]


class NoticePlaceContextError(RuntimeError):
    """The NoticePlace context could not be read over the authenticated API."""


def _direct_open(request, timeout=None):
    """Open one request without a proxy: the API address is explicit."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout)


class NoticePlaceVoiceStore:
    """Read the incident context of NoticePlace over its authenticated API."""

    def __init__(self, base_url=None, token=None, project=None, *, owner_audiences=False,
                 timeout=API_TIMEOUT_SECONDS, runner=None):
        self.base_url = str(base_url or os.environ.get('NOTICEPLACE_API_URL', '')).strip().rstrip('/')
        self.token = str(token if token is not None else os.environ.get('NOTICEPLACE_API_TOKEN', '')).strip()
        self.project = str(project if project is not None else os.environ.get('NOTICEPLACE_API_PROJECT', '*')).strip() or '*'
        self.recipient = 'me'
        # This broader scope is explicit in the authenticated owner-call MCP.
        # Other recipients and the ordinary single-recipient reader stay separate.
        self.recipients = OWNER_AUDIENCES if owner_audiences else ('me',)
        self.timeout = max(1.0, min(float(timeout), 20.0))
        self.runner = runner or _direct_open

    def _request(self, path, params=None):
        if not self.base_url or not self.token:
            raise NoticePlaceContextError('NoticePlace context API is not configured')
        query = urllib.parse.urlencode(params or {})
        url = self.base_url + path + (('?' + query) if query else '')
        request = urllib.request.Request(url, headers={
            'Authorization': 'Bearer ' + self.token, 'Accept': 'application/json'})
        try:
            with self.runner(request, timeout=self.timeout) as response:
                payload = json.loads(response.read() or b'{}')
        except urllib.error.HTTPError as error:
            raise NoticePlaceContextError(
                f'NoticePlace context API returned HTTP {error.code}') from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise NoticePlaceContextError(
                f'NoticePlace context API is unreachable ({type(error).__name__})') from None
        except (UnicodeDecodeError, ValueError):
            raise NoticePlaceContextError('NoticePlace context API returned invalid JSON') from None
        if not isinstance(payload, dict):
            raise NoticePlaceContextError('NoticePlace context API returned an invalid object')
        return payload

    @staticmethod
    def _summary(item):
        updated = item.get('updated_at')
        resolved = item.get('resolved_at')
        return {key: safe_text(item.get(key), 600 if key == 'title' else 128)
                for key in ('id', 'project', 'state', 'severity', 'title')} | {
                    'updated_at': datetime.fromtimestamp(float(updated), ZoneInfo('Europe/Moscow')).isoformat()
                    if updated else None,
                    'resolved_at': datetime.fromtimestamp(float(resolved), ZoneInfo('Europe/Moscow')).isoformat()
                    if resolved else None}

    def list_incidents(self, query='', include_resolved=False):
        # Only real incident records; bounded recent recovery lookup is explicit.
        terms = [] if service_search(query) else str(query)[:128].casefold().split()[:8]
        if service_search(query):
            terms = list(AUTOSELL_ALIASES)
        payload = self._request('/v1/incidents', {
            'project': self.project,
            'query': ' '.join(terms),
            'include_resolved': '1' if include_resolved else '0',
            'limit': '20',
        })
        items = [self._summary(item) for item in payload.get('incidents') or []
                 if isinstance(item, dict)]
        if self.recipients != OWNER_AUDIENCES:
            allowed = set(self.recipients)
            items = [item for item in items if item.get('recipient') in allowed]
        return {
            'total_in_scope': int(payload.get('total_in_scope') or len(items)),
            'returned': len(items),
            'truncated': bool(payload.get('truncated')),
            'audiences': list(self.recipients),
            'includes_recent_resolved': bool(include_resolved),
            'incidents': items,
            'observed_at': datetime.now(ZoneInfo('Europe/Moscow')).isoformat(),
            'state_source': 'NoticePlace cards; session_read is required to confirm actual investigation progress',
        }

    def incident(self, incident_id):
        payload = self._request('/v1/incidents/' + urllib.parse.quote(str(incident_id), safe=''))
        if not payload.get('id'):
            raise NoticePlaceContextError('Incident is not available in this incoming call')
        result = self._summary(payload)
        result['body'] = safe_text(payload.get('body'), 2000)
        result['has_session'] = bool(payload.get('has_session'))
        result['session_harness'] = str(payload.get('session_harness') or '')
        result['session_id'] = str(payload.get('session_id') or '')
        return result
