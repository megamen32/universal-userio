"""Bounded read-only NoticePlace context for the owner's incoming calls.

The context is read over the authenticated read API of NoticePlace. UserIO
never opens the NoticePlace store: a shared database file is not an API, and a
voice answer must not depend on the storage of another service.
"""
from __future__ import annotations

import json
import hashlib
import os
import math
from pathlib import Path
import stat
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
QA_BINDING = {'qa_caller':'caller', 'qa_room':'room', 'qa_account_ref':'account_ref',
              'qa_sdk_user_id':'sdk_user_id', 'voice_transport':'transport', 'incident_id':'incident_id',
              'correlation_id':'correlation_id'}


def explicit_incident_acknowledgement(text):
    """Recognize a current, unqualified receipt statement; never quoted intent."""
    value = str(text or '').casefold().replace('ё', 'е')
    if re.search(r'[?"«»“”]|\b(?:не|нет|если|когда|допустим)\b', value):
        return False
    value = ' '.join(re.sub(r'[.,!;:]', ' ', value).split())
    value = re.sub(r'^(?:да |пожалуйста )+', '', value)
    value = re.sub(r'(?: пожалуйста| спасибо)+$', '', value)
    return bool(re.fullmatch(
        r'(?:я )?(?:(?:получил|получила|получили|принял|приняла) '
        r'(?:(?:это|тестовое|ваше|данное) )*(?:уведомление|сообщение)|'
        r'(?:(?:это|тестовое|ваше|данное) )*(?:уведомление|сообщение) '
        r'(?:получил|получила|получено|принято)|'
        r'подтверждаю(?: (?:(?:этот|данный|тестовый) )?инцидент| получение(?: уведомления)?)?|'
        r'подтверди(?: (?:(?:этот|данный|тестовый) )?инцидент| получение(?: уведомления)?)?|'
        r'принято|останови эскалацию)', value))


def incident_ack_instruction(text):
    if not explicit_incident_acknowledgement(text):
        return ''
    return ('Текущая реплика явно подтверждает получение уведомления (ACK), '
            'в том числе «получил уведомление»; повторного подтверждения не спрашивай. '
            'Отметь только точный текущий инцидент существующим разрешённым ACK-инструментом. '
            'Это не ремонт и не resolve. Сообщай об отметке только по квитанции; '
            'если разрешённого инструмента нет, честно скажи, что отметка не выполнена. ')


def qa_scope_document():
    """Read only the server-owned scope; a file reference allows live revocation."""
    try:
        scope = json.loads(os.environ.get('AGENTCALL_QA_SCOPE') or '{}')
        if isinstance(scope, dict) and scope.get('scope_file'):
            path = Path(scope['scope_file'])
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor) as stream:
                metadata = os.fstat(stream.fileno())
                import pwd
                owners = {0, os.geteuid(), pwd.getpwnam('roomhacker').pw_uid}
                if (metadata.st_uid not in owners or metadata.st_mode & 0o077
                        or not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 65536):
                    raise PermissionError('QA scope must be a private server-owned file')
                scope = json.load(stream)
        if not isinstance(scope, dict):raise ValueError('invalid scope')
        return scope
    except (OSError, ValueError, TypeError):
        raise PermissionError('QA incident scope is unavailable') from None


def qa_incident_scope(binding, *, now=None):
    """Authorize this exact synthetic incident, never an owner or arbitrary session."""
    moment = time.time() if now is None else now
    entries = qa_scope_document().get('entries', [])
    if not isinstance(entries, list):raise PermissionError('Invalid QA scope entries')
    for entry in entries:
        if not isinstance(entry, dict):continue
        if any(not binding.get(key) or binding[key] != entry.get(field)
               for key, field in QA_BINDING.items()):continue
        expires = entry.get('expires_at')
        pair = entry.get('participants')
        if (entry.get('synthetic') is not True or entry.get('transport') != 'matrix'
                or not isinstance(pair, list) or len(pair) != 2
                or any(not isinstance(member,str) or not member for member in pair)
                or len(set(pair)) != 2
                or entry['caller'] not in pair
                or type(expires) not in (int, float) or not math.isfinite(expires) or expires <= moment
                or not isinstance(entry.get('project'), str) or not entry['project'].strip()
                or entry['project'] == '*'
                or entry.get('token_env') != 'NOTICEPLACE_VOICE_QA_TOKEN'):
            raise PermissionError('QA incident scope is invalid or expired')
        return entry
    raise PermissionError('No exact QA incident scope for this caller')


def qa_scope_environment():
    return {key:os.environ[key] for key in ('AGENTCALL_QA_SCOPE','NOTICEPLACE_VOICE_QA_TOKEN',
            'NOTICEPLACE_API_URL') if key in os.environ}


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


def _scoped_open(request, timeout=None):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):return None
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect).open(request, timeout=timeout)


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


class ScopedNoticePlaceVoiceStore(NoticePlaceVoiceStore):
    """QA reads one server-bound card; no global list, sessions or owner token."""
    def __init__(self, binding, *, receipt_directory='/var/lib/agentcall/qa-actions', **kwargs):
        self.binding = dict(binding)
        self.receipt_directory = Path(receipt_directory)
        scope = qa_incident_scope(self.binding)
        kwargs.setdefault('runner', _scoped_open)
        super().__init__(token=os.environ.get(scope['token_env'], ''),
                         project=scope['project'], **kwargs)

    def incident(self, incident_id):
        scope = qa_incident_scope(self.binding)
        if incident_id != scope['incident_id']:
            raise PermissionError('Incident is outside this QA call')
        self.token = os.environ.get(scope['token_env'], '')
        self.project = scope['project']
        payload = self._request('/v1/incidents/' + urllib.parse.quote(incident_id, safe=''))
        if (payload.get('id') != incident_id or payload.get('project') != self.project
                or payload.get('correlation_id') != scope['correlation_id']):
            raise PermissionError('QA incident response does not match its binding')
        result = self._summary(payload)
        result['body'] = safe_text(payload.get('body'), 2000)
        result['has_session'] = False
        return result

    def list_incidents(self, query='', include_resolved=False):
        card = self.incident(self.binding['incident_id'])
        cards = [card] if include_resolved or card['state'] != 'resolved' else []
        return {'total_in_scope':len(cards), 'returned':len(cards), 'truncated':False,
                'audiences':['synthetic'], 'includes_recent_resolved':bool(include_resolved),
                'incidents':cards, 'observed_at':datetime.now(ZoneInfo('Europe/Moscow')).isoformat()}

    def _post_ack(self, incident_id):
        request = urllib.request.Request(self.base_url+'/v1/incidents/'+
            urllib.parse.quote(incident_id,safe='')+'/ack', method='POST',
            data=json.dumps({'actor':'voice-qa:'+self.binding['qa_sdk_user_id']}).encode(),
            headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json',
                     'Idempotency-Key':'voice-qa-ack-'+hashlib.sha256((incident_id+'\0'+
                         self.binding['correlation_id']).encode()).hexdigest()})
        with self.runner(request, timeout=self.timeout) as response:
            response.read()

    def acknowledge(self, incident_id):
        card = self.incident(incident_id)  # Checks exact id, project, correlation and live TTL.
        if card['state'] in ('acknowledged','resolved'):
            return {'ok':True,'incident_id':incident_id,'state':card['state'],
                    'delivery_cancellation_verified':False}
        root = self.receipt_directory
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = root.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
            raise PermissionError('QA action receipts must be private and service-owned')
        key = hashlib.sha256((incident_id+'\0'+self.binding['correlation_id']).encode()).hexdigest()
        path = root / (key+'.ack.json')
        try:
            descriptor = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            return {'ok':False,'incident_id':incident_id,'state':'unknown','replay_allowed':False}
        with os.fdopen(descriptor,'w') as stream:
            json.dump({'incident_id':incident_id,'correlation_id':self.binding['correlation_id'],
                       'state':'attempted','replay_allowed':False},stream)
            stream.flush();os.fsync(stream.fileno())
        descriptor = os.open(root, os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(descriptor)
        finally:os.close(descriptor)
        try:
            qa_incident_scope(self.binding)
            self._post_ack(incident_id)
            card = self.incident(incident_id)
        except Exception:
            return {'ok':False,'incident_id':incident_id,'state':'unknown','replay_allowed':False}
        return {'ok':card['state'] in ('acknowledged','resolved'), 'incident_id':incident_id,
                'state':card['state'],'delivery_cancellation_verified':False}


class RecipientNoticePlaceVoiceStore(NoticePlaceVoiceStore):
    """The same narrow tools for a real delivered recipient, without QA grants.

    The opaque proof stays in the private server context and HTTP body. Only
    NoticePlace can resolve an incident; tools cannot supply identity or scope.
    """
    def __init__(self, binding, *, receipt_directory='/var/lib/agentcall/qa-actions', **kwargs):
        self.binding = dict(binding)
        if not self.binding.get('voice_recipient_proof') or not self.binding.get('secretary_voice_call_id'):
            raise PermissionError('No server recipient receipt')
        self.receipt_directory = Path(receipt_directory)
        kwargs.setdefault('runner', _scoped_open)
        super().__init__(token='', **kwargs)
        self._bound = None

    def _action(self, action, utterance=''):
        if not self.base_url:
            raise NoticePlaceContextError('NoticePlace recipient API is not configured')
        body = {'proof':self.binding['voice_recipient_proof'], 'action':action}
        if self._bound:
            body['binding_id'] = self._bound['binding_id']
        if action == 'ack':body['utterance'] = utterance
        request = urllib.request.Request(self.base_url+'/v1/voice/recipient-context', method='POST',
            data=json.dumps(body).encode(), headers={'Content-Type':'application/json'})
        try:
            with self.runner(request, timeout=self.timeout) as response:
                result = json.loads(response.read())
        except urllib.error.HTTPError:
            raise PermissionError('No unique current delivered recipient notice') from None
        if (not isinstance(result,dict) or result.get('ok') is not True
                or result.get('call_id') != self.binding['secretary_voice_call_id']
                or type(result.get('expires_at')) not in (int,float)
                or not time.time() < result['expires_at'] <= time.time()+600
                or not isinstance(result.get('incident'),dict) or not result['incident'].get('id')
                or not isinstance(result.get('binding_id'),str) or not result['binding_id']):
            raise PermissionError('Recipient response does not match this call')
        if self._bound and (result['binding_id'] != self._bound['binding_id'] or
                any(result['incident'].get(key) != self._bound['incident'].get(key)
                    for key in ('id','project','correlation_id'))):
            raise PermissionError('Recipient incident changed during call')
        self._bound = result
        return result['incident']

    def incident(self, incident_id=None):
        card = self._action('read' if self._bound else 'resolve')
        if incident_id is not None and incident_id != card['id']:
            raise PermissionError('Incident is outside this recipient call')
        return self._summary(card) | {'body':safe_text(card.get('body'),2000),'has_session':False}

    def list_incidents(self, query='', include_resolved=False):
        card = self.incident()
        return {'total_in_scope':1, 'returned':1, 'truncated':False,
                'audiences':['current recipient'], 'incidents':[card]}

    def acknowledge(self, incident_id=None, utterance=''):
        if not explicit_incident_acknowledgement(utterance):
            raise PermissionError('No explicit current receipt statement')
        card = self.incident(incident_id)
        if card['state'] in ('acknowledged','resolved'):
            return {'ok':True,'incident_id':card['id'],'state':card['state']}
        root = self.receipt_directory
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = root.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
            raise PermissionError('Action receipts must be private and service-owned')
        key = hashlib.sha256((self.binding['secretary_voice_call_id']+'\0'+self._bound['binding_id']).encode()).hexdigest()
        try:
            fd = os.open(root/(key+'.ack.json'),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        except FileExistsError:
            return {'ok':False,'state':'unknown','replay_allowed':False}
        with os.fdopen(fd,'w') as stream:
            json.dump({'state':'attempted','incident_id':card['id'],'replay_allowed':False},stream)
            stream.flush();os.fsync(stream.fileno())
        directory = os.open(root,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
        try:
            self._action('ack',utterance)
            card = self.incident(card['id'])
        except Exception:
            return {'ok':False,'state':'unknown','replay_allowed':False}
        return {'ok':card['state'] in ('acknowledged','resolved'),'incident_id':card['id'],'state':card['state']}
