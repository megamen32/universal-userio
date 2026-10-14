"""Focused integration<1s/max10s: Telegram routes never originate telephone."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch
import test_voice_engine_fast_agent as fixtures


class TelegramControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_control_routes_fixed_worker_and_incoming_socket_refuses_it(self):
        controller=fixtures.bridge.CallController(fixtures.bridge.BackendConfig.from_environment(), {})
        reader=asyncio.StreamReader()
        reader.feed_data(json.dumps({'action':'telegram_transport'}).encode()+b'\n')
        writer=AsyncMock(); writer.write=Mock(); writer.close=Mock()
        with patch.object(controller,'stream_telegram_transport',new_callable=AsyncMock) as transport, \
             patch.object(controller,'prepare',side_effect=AssertionError('must not dial GSM')):
            await controller.accept(reader,writer)
            transport.assert_awaited_once()
            transport.reset_mock()
            incoming=asyncio.StreamReader()
            incoming.feed_data(json.dumps({'action':'telegram_transport'}).encode()+b'\n')
            await controller.accept_incoming(incoming,writer)
            transport.assert_not_awaited()
            self.assertIn('incoming socket',writer.write.call_args.args[0].decode())

    async def test_only_one_native_transport_uses_the_existing_voice_budget(self):
        controller=fixtures.bridge.CallController(fixtures.bridge.BackendConfig.from_environment(), {})
        controller._matrix_transport_busy=True
        with patch.object(fixtures.bridge.asyncio,'create_subprocess_exec',new_callable=AsyncMock) as spawn:
            with self.assertRaises(RuntimeError):
                await controller.stream_telegram_transport({},None,None)
            spawn.assert_not_awaited()
        controller._matrix_transport_busy=False; controller._telegram_transport_busy=True
        with patch.object(fixtures.bridge.asyncio,'create_subprocess_exec',new_callable=AsyncMock) as spawn:
            with self.assertRaises(RuntimeError):
                await controller.stream_matrix_transport({},None,None)
            spawn.assert_not_awaited()

from test_voice_engine_fast_agent import isolate_voice_engine
