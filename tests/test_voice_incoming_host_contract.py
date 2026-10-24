"""Host dialplan Answer→AudioSocket→Hangup: no container Asterisk executable.

Actual voice turn loop with isolated existing fixture; no sockets/providers/device.
"""
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, patch
import test_voice_engine_fast_agent as fixtures


class IncomingHostContract(unittest.IsolatedAsyncioTestCase):
    async def scenario(self, mode, *, explicit=False, host_prepared=True):
        fixture=fixtures.CallPeerSnapshotTests();fixture.setUp()
        for action,args,kwargs in fixture._cleanups:self.addCleanup(action,*args,**kwargs)
        fixture._cleanups.clear()
        original=fixtures.bridge.handle_call
        cli_answer=AsyncMock(side_effect=FileNotFoundError('/usr/sbin/asterisk')) if host_prepared else AsyncMock(return_value=True)
        cli_hangup=AsyncMock(side_effect=FileNotFoundError('/usr/sbin/asterisk')) if host_prepared else AsyncMock()
        answer=AsyncMock(return_value=True);hangup=AsyncMock();closed=[]
        async def incoming_handle(reader,writer,broker,config,prepared,**kwargs):
            prepared={k:replace(v,agent_context={'call_mode':mode},host_answered_incoming=host_prepared) for k,v in prepared.items()}
            class Writer:
                def close(self):closed.append(True);writer.close()
                async def wait_closed(self):await writer.wait_closed()
            with patch.object(fixtures.bridge,'wait_for_sip_answer',cli_answer),patch.object(fixtures.bridge,'request_asterisk_hangup',cli_hangup):
                return await original(reader,Writer(),broker,config,prepared,
                    answer_gate=answer if explicit else None,hangup=hangup if explicit else None)
        with patch.object(fixtures.bridge,'handle_call',side_effect=incoming_handle):
            events,_,_=await fixture.scenario(incoming=True)
        return events,cli_answer,cli_hangup,answer,hangup,closed

    async def test_incoming_owner_uses_already_answered_host_transport(self):
        result=await self.scenario('incoming')
        events,cli_answer,cli_hangup,_,_,closed=result
        self.assertIn('Начало.',[e.get('text') for e in events])
        self.assertIn('Сервис работает.',[e.get('text') for e in events])
        self.assertEqual(events[-1]['event'],'ended');self.assertTrue(closed)
        cli_answer.assert_not_awaited();cli_hangup.assert_not_awaited()

    async def test_incoming_guest_also_avoids_container_cli(self):
        events,cli_answer,cli_hangup,_,_,closed=await self.scenario('incoming_guest')
        self.assertIn('Начало.',[e.get('text') for e in events]);self.assertTrue(closed)
        cli_answer.assert_not_awaited();cli_hangup.assert_not_awaited()

    async def test_explicit_provider_gate_and_hangup_still_run(self):
        _,cli_answer,cli_hangup,answer,hangup,_=await self.scenario('incoming',explicit=True)
        answer.assert_awaited_once();hangup.assert_awaited_once()
        cli_answer.assert_not_awaited();cli_hangup.assert_not_awaited()

    async def test_body_incoming_mode_without_host_provenance_keeps_sip_gate(self):
        _,cli_answer,cli_hangup,_,_,_=await self.scenario('incoming',host_prepared=False)
        cli_answer.assert_awaited_once();cli_hangup.assert_awaited_once()

    def test_only_incoming_server_preparation_stamps_provenance(self):
        bridge=fixtures.bridge
        config=bridge.BackendConfig.from_environment()
        controller=bridge.CallController(config,{})
        pcm=b'\xe8\x03'*30000
        with patch.object(bridge.Path,'read_bytes',return_value=b'fixture-wav'),patch.object(bridge,'wav_to_native_pcm',return_value=pcm),patch.object(bridge,'load_wait_prompts',return_value=(('Жду.',pcm),)):
            result=controller.prepare_incoming('')
        self.assertTrue(controller.prepared_calls[result['call_id']].host_answered_incoming)

from test_voice_engine_fast_agent import isolate_voice_engine
