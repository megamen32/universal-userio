"""Exercise the old accept path through real local sockets, with no providers."""
import asyncio
from contextlib import AsyncExitStack, ExitStack
from dataclasses import replace
import json
import socket
import threading
import uuid
import wave

import pytest

from test_portable_voice_release import load
from test_voice_engine_fast_agent import bridge


def test_legacy_handoff_preserves_old_connections_and_prepared_uuid(tmp_path, monkeypatch):
    module = load()
    prepared = {}
    preparations = []
    matrix_started = asyncio.Event()
    finish_matrix = asyncio.Event()
    audio_started = asyncio.Event()
    finish_audio = asyncio.Event()
    greeting = tmp_path / 'incoming-guest-greeting.wav'
    with wave.open(str(greeting), 'wb') as wav:
        wav.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\x00\x04\x00\xfc' * 16000)
    config = replace(bridge.BackendConfig.from_environment(),
                     incoming_greeting_wav=str(greeting), outbound_target='', peer_account_ref='')
    controller = bridge.CallController(config, prepared)
    prepare_incoming = controller.prepare_incoming
    release_probe = threading.Event()
    probe_entered = threading.Event()
    docker_reads = []
    read_handoff_status = module.read_handoff_status

    def inspect(method, path):
        assert method == 'GET'
        docker_reads.append(path)
        return {'Id': 'old-primary',
                'Config': {'Labels': {module.LABEL: '1', 'com.userio.role': 'calls-primary'}},
                'State': {'Running': True, 'StartedAt': 'old-started-at'},
                'Mounts': [{'Destination': str(tmp_path), 'Source': str(tmp_path), 'RW': True}]}

    def check_legacy_runtime(runtime_dir):
        probe_entered.set()
        assert release_probe.wait(timeout=3)
        return read_handoff_status(runtime_dir)

    def prepare(caller):
        preparations.append(caller)
        return prepare_incoming(caller)

    async def transport(_request, _reader, writer):
        matrix_started.set()
        await finish_matrix.wait()
        writer.write(b'remote media finished\n')
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def audio(reader, writer):
        try:
            kind, payload = await bridge.read_audiosocket_frame(reader)
            assert kind == bridge.UUID_KIND
            assert prepared.pop(str(uuid.UUID(bytes=payload))).host_answered_incoming
            audio_started.set()
            await finish_audio.wait()
            writer.write(b'audio finished')
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def send(writer, action, **fields):
        writer.write(json.dumps({'action': action, **fields}).encode() + b'\n')
        await writer.drain()

    async def exercise():
        control = tmp_path / 'control.sock'
        incoming = tmp_path / 'incoming.sock'
        control_server = await asyncio.start_unix_server(controller._accept_admitted, path=control)
        incoming_server = await asyncio.start_unix_server(
            lambda r, w: controller._accept_admitted(r, w, incoming_only=True), path=incoming)
        audio_server = await asyncio.start_server(audio, '127.0.0.1', 0)
        async with control_server, incoming_server, audio_server, AsyncExitStack() as stack:
            async def connect(opener, *args):
                reader, writer = await opener(*args)
                stack.push_async_callback(writer.wait_closed)
                stack.callback(writer.close)
                return reader, writer

            # These connects succeed before the barrier. Both requests are
            # withheld until after it closes to cover the accept/read race.
            matrix_read, matrix_write = await connect(asyncio.open_unix_connection, control)
            agi_read, agi_write = await connect(asyncio.open_unix_connection, incoming)
            original_inodes = [path.stat().st_ino for path in (control, incoming)]
            check = asyncio.create_task(asyncio.to_thread(
                module.check_primary_handoff, 'userio-voice', 'old-primary'))
            try:
                assert await asyncio.to_thread(probe_entered.wait, 2)
                for path in (control, incoming):
                    with pytest.raises(FileNotFoundError):
                        await connect(asyncio.open_unix_connection, path)
                assert preparations == []
                await send(matrix_write, 'matrix_transport')
                await matrix_started.wait()
                await send(agi_write, 'prepare_incoming', caller_number='isolated-fixture')
                call_id = json.loads(await agi_read.readline())['call_id']
                assert list(prepared) == [call_id]

                audio_read, audio_write = await connect(
                    asyncio.open_connection, *audio_server.sockets[0].getsockname())
                audio_write.write(bridge.pack_audiosocket_frame(
                    bridge.UUID_KIND, uuid.UUID(call_id).bytes))
                await audio_write.drain()
                await audio_started.wait()
                assert not prepared
                assert not check.done()
                for path in (control, incoming):
                    with pytest.raises(FileNotFoundError):
                        await connect(asyncio.open_unix_connection, path)
                assert preparations == ['isolated-fixture']
            finally:
                finish_matrix.set()
                finish_audio.set()
                release_probe.set()
                with pytest.raises(RuntimeError, match='legacy voice cannot prove drained'):
                    await check

            assert await matrix_read.readline() == b'remote media finished\n'
            assert await audio_read.read() == b'audio finished'
            assert docker_reads == ['/containers/userio-voice/json']
            assert [path.stat().st_ino for path in (control, incoming)] == original_inodes
            readback, readback_write = await connect(asyncio.open_unix_connection, incoming)
            await send(readback_write, 'prepare_incoming', caller_number='reopened')
            reopened = json.loads(await readback.readline())['call_id']
            assert list(prepared) == [reopened]
            assert preparations == ['isolated-fixture', 'reopened']

    monkeypatch.setattr(controller, 'prepare_incoming', prepare)
    monkeypatch.setattr(controller, 'stream_matrix_transport', transport)
    monkeypatch.setattr(bridge, 'load_wait_prompts', lambda: ())
    monkeypatch.setattr(module, 'RUNTIME_DIR', tmp_path)
    monkeypatch.setattr(module, 'api', inspect)
    monkeypatch.setattr(module, 'read_handoff_status', check_legacy_runtime)
    asyncio.run(asyncio.wait_for(exercise(), timeout=5))


def test_fence_rollback_preserves_a_replaced_ingress(tmp_path):
    module = load()
    control = tmp_path / 'control.sock'
    incoming = tmp_path / 'incoming.sock'
    with ExitStack() as stack:
        for path in (control, incoming):
            listener = stack.enter_context(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
            listener.bind(str(path))
            listener.listen()
        old_control = control.stat().st_ino
        old_incoming = incoming.stat().st_ino
        replacement = stack.enter_context(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
        with pytest.raises(RuntimeError, match='admission changed during handoff: control.sock'):
            with module.fence_primary_admission(tmp_path):
                replacement.bind(str(control))
                new_control = control.stat().st_ino

        assert control.stat().st_ino == new_control
        assert incoming.stat().st_ino == old_incoming
        assert (tmp_path / '.primary-handoff' / 'control.sock').stat().st_ino == old_control
