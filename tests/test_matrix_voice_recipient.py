"""Server-owned incoming Matrix identity reaches normal Notice Place resolution."""
import base64
import hashlib
import hmac
import json
import os
import time
from unittest.mock import patch
import unittest

from universal_userio.matrix_inbound_calls import InboundMatrixCall, build_answer_request
from universal_userio.voice import matrix_call_answer_worker as worker


class VoiceRecipientTests(unittest.TestCase):
    def build(self):
        event = InboundMatrixCall('!qa:matrix.example', '$fresh', '@recipient:matrix.example',
                                  'rtc-call', {}, 240000)
        with patch.dict(os.environ, USERIO_MATRIX_NOTICE_TOKEN='fixture-private-key'):
            request = build_answer_request(event, account={'id':'matrix:service','matrix_user_id':'@service:matrix.example'},
                secret={}, identity={}, rtc_auth_url='https://rtc.example')
        return event, request['call']

    def test_signed_proof_is_bound_to_the_actual_admitted_call(self):
        event, call = self.build()
        self.assertEqual(call['voice_receipt_id'], event.receipt_id('matrix:service'))
        encoded, signature = call['voice_recipient_proof'].split('.')
        raw = base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4))
        self.assertEqual(signature, hmac.new(b'fixture-private-key',raw,hashlib.sha256).hexdigest())
        payload=json.loads(raw)
        self.assertEqual(payload, dict(v=1,call_id=call['engine_call_id'],receipt_id=call['voice_receipt_id'],
            caller=event.caller,account_ref='matrix:service',room_id=event.room_id,expires_at=payload['expires_at']))
        self.assertLessEqual(time.time()+238,payload['expires_at'])
        context=worker.recipient_context(call,caller=event.caller,account_ref='matrix:service',room_id=event.room_id)
        self.assertEqual(context, {k:call[k] for k in ['voice_receipt_id','voice_recipient_proof']})
        self.assertNotIn('fixture-private-key', json.dumps(context))

    def test_old_calls_still_work_without_proof_and_foreign_fields_do_not_gain_it(self):
        event, call=self.build()
        self.assertEqual(worker.recipient_context({},caller=event.caller,account_ref='matrix:service',room_id=event.room_id),{})
        self.assertEqual(worker.recipient_context(call,caller='@other:matrix.example',account_ref='matrix:service',room_id=event.room_id),{})
        self.assertEqual(worker.recipient_context(call,caller=event.caller,account_ref='matrix:other',room_id=event.room_id),{})
        self.assertEqual(worker.recipient_context(call,caller=event.caller,account_ref='matrix:service',room_id=event.room_id,now=time.time()+1000),{})
