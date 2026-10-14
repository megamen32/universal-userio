#!/usr/bin/env python3
"""Typed Secretary call intake over NoticePlace's existing durable queue.

No dialing, worker, scheduler or new storage. Only the canonical dispatcher
crosses the provider boundary. Sending/uncertain receipts are never replayed.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import unquote, urlsplit

ID = re.compile(r'[A-Za-z0-9._-]{1,128}\Z')
E164 = re.compile(r'\+[1-9][0-9]{6,14}\Z')
MAX_BODY = 16384


class ApiError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(code)


@dataclass(frozen=True)
class Config:
    database: str
    api_token: str
    owner_user_id: str
    recipient_e164: str
    consumer_token: str
    consumer_id: str
    project: str = 'secretary-phone'

    @classmethod
    def from_env(cls):
        names = {'database': 'NOTICEPLACE_DB', 'api_token': 'SECRETARY_API_TOKEN',
                 'owner_user_id': 'OWNER_USER_ID', 'recipient_e164': 'RECIPIENT_E164',
                 'consumer_token': 'NOTICEPLACE_CONSUMER_TOKEN', 'consumer_id': 'NOTICEPLACE_CONSUMER_ID'}
        values = {field: os.environ.get(name, '') for field, name in names.items()}
        if any(not value for value in values.values()) or not E164.fullmatch(values['recipient_e164']):
            raise RuntimeError('Secretary call API configuration is incomplete')
        return cls(**values, project=os.environ.get('NOTICEPLACE_PROJECT', 'secretary-phone'))


class SecretaryCallService:
    def __init__(self, config, center):
        self.config, self.center = config, center

    def _read(self):
        if self.config.database.startswith('mesh:'):
            from notification_center.mesh_store import connect
            return connect(self.config.database, readonly=True)
        connection = sqlite3.connect(Path(self.config.database).resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _key(self, request_id):
        # Stable within the exact project/owner/consumer namespace.
        scope = json.dumps([self.config.project, self.config.owner_user_id, self.config.consumer_id, request_id], separators=(',', ':'))
        return 'secretary-call:' + hashlib.sha256(scope.encode()).hexdigest()

    def _request(self, data):
        fields = {'requestId', 'ownerUserId', 'sourceRef', 'recipientE164', 'message', 'context'}
        if not isinstance(data, dict) or set(data) != fields:
            raise ApiError(400, 'INVALID_REQUEST')
        limits = {'requestId': 128, 'ownerUserId': 128, 'sourceRef': 512, 'recipientE164': 16, 'message': 1200, 'context': 500}
        for key, limit in limits.items():
            value = data[key]
            if not isinstance(value, str) or (key != 'context' and not value.strip()) or len(value) > limit or '\x00' in value:
                raise ApiError(400, 'INVALID_REQUEST')
        if not ID.fullmatch(data['requestId']) or not E164.fullmatch(data['recipientE164']):
            raise ApiError(400, 'INVALID_REQUEST')
        if data['ownerUserId'] != self.config.owner_user_id:
            raise ApiError(403, 'OWNER_NOT_AUTHORIZED')
        if data['recipientE164'] != self.config.recipient_e164:
            raise ApiError(422, 'RECIPIENT_UNSUPPORTED')
        return dict(data)

    def _binding(self):
        # Require the provisioned token and exactly one ordinary phone step.
        connection = self._read()
        try:
            consumer = connection.execute('SELECT project, token_hash FROM consumers WHERE id=?', (self.config.consumer_id,)).fetchone()
            steps = connection.execute('SELECT * FROM consumer_policy_stages WHERE consumer_id=?', (self.config.consumer_id,)).fetchall()
        finally:
            connection.close()
        expected_hash = hashlib.sha256(self.config.consumer_token.encode()).hexdigest()
        if consumer is None or consumer['project'] != self.config.project or not hmac.compare_digest(consumer['token_hash'], expected_hash):
            raise ApiError(503, 'CONSUMER_BINDING_INVALID')
        if len(steps) != 1:
            raise ApiError(503, 'CONSUMER_POLICY_INVALID')
        step = steps[0]
        try:
            target = json.loads(step['target_json'])
        except (ValueError, TypeError):
            raise ApiError(503, 'CONSUMER_POLICY_INVALID')
        if (not step['enabled'] or step['platform'] != 'phone' or step['action'] != 'call'
                or step['max_repeats'] != 1 or step['previous_step_id'] is not None
                or not isinstance(target, dict) or target.get('recipient_e164') != self.config.recipient_e164):
            raise ApiError(503, 'CONSUMER_POLICY_INVALID')

    def _record(self, request_id):
        if not isinstance(request_id, str) or not ID.fullmatch(request_id):
            raise ApiError(400, 'INVALID_REQUEST_ID')
        connection = self._read()
        try:
            row = connection.execute('SELECT e.*, i.consumer_id, i.project FROM events e JOIN incidents i ON i.id=e.incident_id WHERE e.idempotency_key=?', (self._key(request_id),)).fetchone()
            if row is None:
                return None, []
            if row['consumer_id'] != self.config.consumer_id or row['project'] != self.config.project:
                raise ApiError(503, 'RECORD_BINDING_INVALID')
            deliveries = connection.execute('SELECT id,channel,status,result_json FROM deliveries WHERE incident_id=? ORDER BY created_at,id', (row['incident_id'],)).fetchall()
            return dict(row), [dict(value) for value in deliveries]
        finally:
            connection.close()

    def get(self, request_id):
        row, deliveries = self._record(request_id)
        result = {'requestId': request_id, 'callId': None, 'recipientE164': self.config.recipient_e164,
                  'state': 'NOT_FOUND', 'receipt': None, 'ownerUserId': self.config.owner_user_id, 'sourceRef': None}
        if row is None:
            return result
        saved = json.loads(row['payload_json']).get('secretary_call')
        if not isinstance(saved, dict) or saved.get('requestId') != request_id or saved.get('ownerUserId') != self.config.owner_user_id or saved.get('recipientE164') != self.config.recipient_e164:
            raise ApiError(503, 'RECORD_BINDING_INVALID')
        if len(deliveries) > 1 or any(delivery['channel'] != 'phone.call' for delivery in deliveries):
            raise ApiError(503, 'DELIVERY_BINDING_INVALID')
        delivery = deliveries[0] if deliveries else None
        states = {'queued': 'QUEUED', 'claimed': 'DISPATCHING', 'sending': 'UNKNOWN', 'uncertain': 'UNKNOWN',
                  'sent': 'INITIATED', 'failed': 'FAILED', 'cancelled': 'CANCELLED', 'superseded': 'CANCELLED'}
        state = states.get(delivery['status'], 'UNKNOWN') if delivery else 'SUPPRESSED'
        try:
            provider = json.loads(delivery['result_json'] or '{}') if delivery else {}
        except (ValueError, TypeError):
            provider = {}
        call_id = provider.get('call_id') or provider.get('callId') if isinstance(provider, dict) else None
        call_id = call_id if isinstance(call_id, str) and 0 < len(call_id) <= 256 else None
        # A sent row without the actual provider identity does not prove dialing.
        if state == 'INITIATED' and call_id is None:
            state = 'UNKNOWN'
        result.update(callId=call_id, state=state, sourceRef=saved['sourceRef'], receipt={
            'eventId': row['event_id'], 'incidentId': row['incident_id'],
            'deliveryId': delivery['id'] if delivery else None,
            'deliveryState': delivery['status'] if delivery else 'suppressed', 'callId': call_id})
        return result

    def originate(self, data):
        request = self._request(data)
        existing, _ = self._record(request['requestId'])
        if existing is not None:
            if json.loads(existing['payload_json']).get('secretary_call') != request:
                raise ApiError(409, 'REQUEST_ID_CONFLICT')
            return self.get(request['requestId'])
        self._binding()
        key = self._key(request['requestId'])
        event = {'schema': 'notify.event.v1', 'project': self.config.project,
                 'recipient': self.config.owner_user_id, 'kind': 'notification', 'severity': 'critical',
                 'event_type': 'secretary.call', 'producer': 'secretary-phone-api',
                 'title': 'Сообщение от секретаря Никиты', 'body': request['message'], 'operator_note': request['context'],
                 'dedup_key': key, 'correlation_id': key, 'secretary_call': request}
        from notification_center.core import IdempotencyConflict
        try:
            self.center.create_event(self.config.consumer_token, key, event)
        except IdempotencyConflict:
            raise ApiError(409, 'REQUEST_ID_CONFLICT')
        return self.get(request['requestId'])


def make_handler(service):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # Do not log phone numbers, request bodies or credentials.
        def _reply(self, status, payload):
            encoded = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
            try:
                self.send_response(status)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
            except OSError:
                # Response transport loss cannot undo a durable acceptance.
                # Never reinterpret it as storage failure or send another reply.
                self.close_connection = True
        def _handle(self, post):
            try:
                expected = 'Bearer ' + service.config.api_token
                if not hmac.compare_digest(self.headers.get('Authorization', ''), expected):
                    raise ApiError(401, 'UNAUTHORIZED')
                path = urlsplit(self.path).path
                if post:
                    if path != '/v1/calls':
                        raise ApiError(404, 'NOT_FOUND')
                    try:
                        size = int(self.headers.get('Content-Length', '0'))
                    except ValueError:
                        raise ApiError(400, 'INVALID_REQUEST')
                    if not 0 < size <= MAX_BODY:
                        raise ApiError(413, 'BODY_TOO_LARGE')
                    try:
                        data = json.loads(self.rfile.read(size))
                    except (ValueError, UnicodeError):
                        raise ApiError(400, 'INVALID_JSON')
                    self._reply(202, service.originate(data))
                else:
                    if not path.startswith('/v1/calls/'):
                        raise ApiError(404, 'NOT_FOUND')
                    result = service.get(unquote(path[len('/v1/calls/'):]))
                    self._reply(404 if result['state'] == 'NOT_FOUND' else 200, result)
            except ApiError as error:
                self._reply(error.status, {'error': error.code})
            except (sqlite3.Error, OSError):
                self._reply(503, {'error': 'STORAGE_UNAVAILABLE', 'reconcile': 'GET_BY_REQUEST_ID'})
            except Exception:
                self._reply(503, {'error': 'REQUEST_OUTCOME_UNKNOWN', 'reconcile': 'GET_BY_REQUEST_ID'})
        def setup(self):
            super().setup()
            self.connection.settimeout(10)
        def do_POST(self):
            self._handle(True)
        def do_GET(self):
            self._handle(False)
    return Handler


def main():
    config = Config.from_env()
    if not config.database.startswith('mesh:') and not Path(config.database).is_file():
        raise RuntimeError('Existing NoticePlace database is required')
    sys.path.insert(0, os.environ.get('NOTICEPLACE_SOURCE', '/opt/noticeplace'))
    from notification_center.core import NotificationCenter
    center = NotificationCenter(config.database, {})
    service = SecretaryCallService(config, center)
    service._binding()
    host = os.environ.get('SECRETARY_API_HOST', '192.168.2.100')
    port = int(os.environ.get('SECRETARY_API_PORT', '19092'))
    HTTPServer((host, port), make_handler(service)).serve_forever()


if __name__ == '__main__':
    main()
