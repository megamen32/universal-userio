import asyncio
import json
import uuid
from unittest.mock import AsyncMock, Mock, patch

import test_voice_engine_fast_agent as fixtures


def test_drain_blocks_control_and_preserves_prepared_audio():
    asyncio.run(exercise_drain())


async def exercise_drain():
    bridge = fixtures.bridge
    prepared_id = str(uuid.uuid4())
    controller = bridge.CallController(bridge.BackendConfig.from_environment(), {prepared_id: object()})
    started, finish = asyncio.Event(), asyncio.Event()

    async def transport(*args):
        started.set()
        await finish.wait()

    def reader(action):
        value = asyncio.StreamReader()
        value.feed_data(json.dumps({'action': action}).encode() + b'\n')
        return value

    def writer():
        result = AsyncMock()
        result.write = Mock()
        result.close = Mock()
        return result

    with patch.object(controller, 'stream_matrix_transport', side_effect=transport):
        first = asyncio.create_task(controller.accept(reader('matrix_transport'), writer()))
        await started.wait()
        controller.maintenance('drain')
        for incoming, action in ((False, 'matrix_transport'), (True, 'prepare_incoming')):
            output = writer()
            await controller.accept(reader(action), output, incoming_only=incoming)
            assert json.loads(output.write.call_args.args[0])['error'] == 'voice_admission_draining'
        assert controller.maintenance('status')['safe'] is False
        finish.set()
        await first
        assert controller.maintenance('status')['prepared'] == 1

        audio = asyncio.StreamReader()
        audio.feed_data(bridge.pack_audiosocket_frame(bridge.UUID_KIND, uuid.UUID(prepared_id).bytes))
        output = writer()
        audio_started, audio_finish = asyncio.Event(), asyncio.Event()
        async def consume(reader, writer, broker, config, prepared, **kwargs):
            assert kwargs['first_frame'][1] == uuid.UUID(prepared_id).bytes
            prepared.pop(prepared_id)
            audio_started.set()
            await audio_finish.wait()
        with patch.object(bridge, 'handle_call', side_effect=consume):
            task = asyncio.create_task(controller.accept_audio(audio, output))
            await audio_started.wait()
            assert controller.maintenance('status')['audio'] == 1
            assert controller.maintenance('status')['safe'] is False
            audio_finish.set()
            await task
        assert controller.maintenance('status')['safe'] is True
        unknown = asyncio.StreamReader()
        unknown.feed_data(bridge.pack_audiosocket_frame(bridge.UUID_KIND, uuid.uuid4().bytes))
        output = writer()
        with patch.object(bridge, 'handle_call', new_callable=AsyncMock) as rejected:
            await controller.accept_audio(unknown, output)
            rejected.assert_not_awaited()
            output.close.assert_called_once()
        controller.maintenance('open')
        assert controller.maintenance('status')['draining'] is False


def test_maintenance_socket_reports_busy_and_safe(tmp_path):
    async def exercise():
        bridge = fixtures.bridge
        controller = bridge.CallController(bridge.BackendConfig.from_environment(), {})
        path = tmp_path / 'maintenance.sock'
        server = await asyncio.start_unix_server(controller.accept_maintenance, path=str(path))
        async def command(action):
            reader, writer = await asyncio.open_unix_connection(str(path))
            writer.write(json.dumps({'action': action}).encode() + b'\n')
            await writer.drain()
            result = json.loads(await reader.readline())
            writer.close()
            await writer.wait_closed()
            return result
        async with server:
            assert (await command('drain'))['safe'] is True
            assert (await command('status'))['draining'] is True
            assert (await command('open'))['safe'] is False
    asyncio.run(exercise())
