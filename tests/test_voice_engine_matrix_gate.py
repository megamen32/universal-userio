"""Focused integration: same voice engine with Matrix answer gate (1s/10s).

Detects accidental SIP/PSTN gating or loss of the existing voice transcripts.
No actual phone, provider request, recording service or network is used.
"""
import unittest
import asyncio
import json
from unittest.mock import AsyncMock, patch
import test_voice_engine_fast_agent as fixtures


class MatrixGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_control_routes_matrix_without_telephone_preparation(self):
        controller = fixtures.bridge.CallController(fixtures.bridge.BackendConfig.from_environment(), {})
        reader = asyncio.StreamReader()
        reader.feed_data(json.dumps({'action':'matrix_transport'}).encode()+b'\n')
        writer = AsyncMock()
        writer.write = unittest.mock.Mock()
        writer.close = unittest.mock.Mock()
        with patch.object(controller, 'stream_matrix_transport', new_callable=AsyncMock) as transport, \
             patch.object(controller, 'prepare', side_effect=AssertionError('must not dial telephone')):
            await controller.accept(reader, writer)
            transport.assert_awaited_once()
            transport.reset_mock()
            incoming = asyncio.StreamReader()
            incoming.feed_data(json.dumps({'action':'matrix_transport'}).encode()+b'\n')
            await controller.accept_incoming(incoming, writer)
            transport.assert_not_awaited()
            self.assertIn('incoming socket', writer.write.call_args.args[0].decode())

    async def test_matrix_answer_uses_existing_handle_call_without_sip(self):
        fixture = fixtures.CallPeerSnapshotTests()
        fixture.setUp()
        # This helper TestCase has no own asyncio runner. Transfer its cleanup
        # callbacks to the real running case; doCleanups on it hides failures.
        for cleanup,args,kwargs in fixture._cleanups:
            self.addCleanup(cleanup,*args,**kwargs)
        fixture._cleanups.clear()
        original = fixtures.bridge.handle_call
        gate = AsyncMock(return_value=True)
        async def matrix_handle(*args, **kwargs):
            with patch.object(fixtures.bridge,'wait_for_sip_answer',
                              side_effect=AssertionError('Matrix must not wait for SIP')):
                return await original(*args,**kwargs,answer_gate=gate)
        with patch.object(fixtures.bridge,'handle_call',side_effect=matrix_handle):
            events,call_id,peer = await fixture.scenario()
        gate.assert_awaited_once()
        self.assertEqual(call_id,gate.call_args.args[0])
        self.assertIn('Начало.',[e.get('text') for e in events])
        self.assertIn('Сервис работает.',[e.get('text') for e in events])
        self.assertEqual(call_id,events[-1]['callId'])

from test_voice_engine_fast_agent import isolate_voice_engine
