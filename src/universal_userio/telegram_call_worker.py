"""One call worker: existing AgentCall engine + native Telegram media + JSONL RPC.

No Telegram credentials are accepted here. The parent connector remains the
only MTProto session owner. Stdout is a private IPC pipe, never a log stream.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import signal
import struct
import sys
import traceback
import uuid

from .telegram_call_transport import TelegramCallTransport, RecipientUnanswered
from .matrix_call_worker import server_voice_config


def emit(event):
    print(json.dumps(event, ensure_ascii=False, separators=(",", ":")), flush=True)


class ConnectorRPC:
    def __init__(self, input_stream):
        self.input_stream = input_stream
        self.pending = {}
        self.counter = 0
        self.transport = None
        self.update_task = None

    async def admit_update(self, message):
        await self.transport.admit_update(message['update'])
        emit({'event':'telegram_update_admitted', 'update_id':message['update_id']})

    async def invoke(self, method, params):
        if len(self.pending) >= 64:
            raise BufferError("connector RPC queue exceeded")
        self.counter += 1
        request_id = str(self.counter)
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        emit({"event": "telegram_rpc", "request_id": request_id,
              "method": method, "params": params})
        try:
            return await asyncio.wait_for(future, 20)
        finally:
            self.pending.pop(request_id, None)

    async def read(self):
        try:
            while True:
                line = await self.input_stream.readline()
                if not line:
                    raise ConnectionError("connector IPC closed")
                if len(line) > 65536 or not line.endswith(b"\n"):
                    raise ValueError("connector IPC frame exceeded")
                message = json.loads(line)
                if message.get("type") == "rpc_result":
                    future = self.pending.get(str(message.get("request_id")))
                    if future and not future.done():
                        if message.get("error"):
                            # Do not echo external diagnostics containing session data.
                            future.set_exception(RuntimeError("connector RPC refused"))
                        else:
                            future.set_result(message.get("result"))
                elif message.get("type") == "telegram_update":
                    if 'update_id' in message:
                        if self.update_task and not self.update_task.done():
                            raise BufferError('connector update admission queue exceeded')
                        if self.update_task: self.update_task.result()
                        self.update_task = asyncio.create_task(self.admit_update(message))
                        def admitted(task):
                            if not task.cancelled() and task.exception():
                                self.transport._fail(task.exception())
                        self.update_task.add_done_callback(admitted)
                    else:
                        self.transport.update(message["update"])
                elif message.get("type") == "hangup":
                    self.transport.ended.set()
                else:
                    raise ValueError("unsupported connector IPC message")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if self.transport:
                self.transport._fail(error)
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(error)
        finally:
            if self.update_task:
                self.update_task.cancel()
                await asyncio.gather(self.update_task,return_exceptions=True)


class AudioSocketWriter:
    sample_rate = TelegramCallTransport.sample_rate

    def __init__(self, transport, audio_kind, hangup_kind):
        self.transport, self.audio_kind, self.hangup_kind = transport, audio_kind, hangup_kind
        self.pending = bytearray()
        self.closed = False

    def write(self, data):
        if len(self.pending) + len(data) > 65536:
            raise BufferError("outbound AudioSocket queue exceeded")
        self.pending.extend(data)

    async def drain(self):
        while len(self.pending) >= 3:
            size = struct.unpack("!H", self.pending[1:3])[0]
            if len(self.pending) < size + 3:
                break
            kind, pcm = self.pending[0], bytes(self.pending[3:3 + size])
            del self.pending[:3 + size]
            if kind == self.audio_kind:
                await self.transport.send_pcm(pcm)
            elif kind == self.hangup_kind:
                self.transport.ended.set()

    def close(self):
        self.closed = True
        self.transport.ended.set()

    def is_closing(self):
        return self.closed

    async def wait_closed(self):
        pass

    def get_extra_info(self, name, default=None):
        return default


async def run(request, input_stream):
    record = request["call"]
    if not record.get("admission_id") or not record.get("history_account_ref"):
        raise ValueError("account-bound durable call admission is required")
    max_seconds = int(record["max_seconds"])
    if not 1 <= max_seconds <= 600:
        raise ValueError("invalid call time budget")
    sys.path.insert(0, '/usr/local/lib/agentcall')  # Installed Exmanager brain adapter.
    sys.path.insert(0, str(Path(__file__).with_name('voice')))
    import agentcall_realtime_bridge as voice
    config = server_voice_config(voice.BackendConfig.from_environment())
    os.environ['AGENTCALL_TTS_PHONE_FALLBACK']='0'
    rpc = ConnectorRPC(input_stream)
    audio_kind = 0x16  # AgentCall AudioSocket: 48kHz mono PCM.
    reader = asyncio.StreamReader(limit=TelegramCallTransport.queue_bytes + 600)
    prepared = {}
    engine_task = None
    accepting_audio = False
    def receive(pcm):
        if accepting_audio:
            if len(reader._buffer) + len(pcm) + 3 > TelegramCallTransport.queue_bytes + 600:
                raise BufferError("AgentCall inbound AudioSocket queue exceeded")
            reader.feed_data(voice.pack_audiosocket_frame(audio_kind, pcm))
    transport = TelegramCallTransport(int(record["user_id"]), rpc.invoke, receive)
    rpc.transport = transport
    ingest = asyncio.create_task(rpc.read())
    writer = AudioSocketWriter(transport, audio_kind, voice.HANGUP_KIND)
    class Broker:
        async def emit(self, event):
            emit(event)
    broker = Broker()
    async def answer(call_id, hung_up):
        return transport.connected.is_set() and not transport.ended.is_set()
    async def hangup(call_id):
        transport.ended.set()
    async def originate(call_id):
        nonlocal engine_task, accepting_audio
        emit({"event": "prepared", "callId": call_id})
        prepared[call_id] = replace(prepared[call_id],
            account_ref=record['history_account_ref'], peer_ref=str(record['user_id']),
            peer_label=record.get('peer_label',str(record['user_id'])))
        await transport.dial()
        reader.feed_data(voice.pack_audiosocket_frame(voice.UUID_KIND, uuid.UUID(call_id).bytes))
        accepting_audio = True
        emit({"event": "connected", "callId": call_id})
        engine_task = asyncio.create_task(voice.handle_call(reader, writer, broker, config,
            prepared, hangup=hangup, answer_gate=answer))
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, transport.ended.set)
    watcher = asyncio.create_task(transport.ended.wait())
    controller = voice.CallController(config, prepared, originate=originate)
    try:
        await controller.prepare(record.get("greeting") or record["purpose"],
                                 context=record["purpose"], repeat=1,
                                 agent_context=dict(record.get('agent_context') or {},
                                     voice_transport='telegram', voice_target=str(record['user_id'])))
        if engine_task:
            await asyncio.wait((engine_task, watcher), timeout=max_seconds,
                               return_when=asyncio.FIRST_COMPLETED)
            transport.ended.set()
            if not reader.at_eof():
                reader.feed_eof()
            await asyncio.wait_for(engine_task, config.request_timeout + 10)
    finally:
        accepting_audio = False
        transport.ended.set()
        if not reader.at_eof():
            reader.feed_eof()
        if engine_task and not engine_task.done():
            engine_task.cancel()
            await asyncio.gather(engine_task, return_exceptions=True)
        try:
            await transport.close()
        finally:
            ingest.cancel(); watcher.cancel()
            await asyncio.gather(ingest, watcher, return_exceptions=True)


async def _main():
    reader = asyncio.StreamReader(limit=65536)
    pipe, _ = await asyncio.get_running_loop().connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer)
    try:
        line = await reader.readline()
        if len(line) > 65536 or not line.endswith(b"\n"):
            raise ValueError("initial IPC frame exceeded")
        await run(json.loads(line), reader)
    finally:
        pipe.close()


def main():
    try:
        asyncio.run(_main())
    except RecipientUnanswered:
        emit({"event": "ended", "reason": "unanswered"})
    except Exception as error:
        # Stack locations diagnose native/pre-answer failures without logging
        # exception messages or provider/session payloads.
        for frame in traceback.extract_tb(error.__traceback__):
            print(f"Telegram worker {type(error).__name__}: {frame.filename}:{frame.lineno} {frame.name}",
                  file=sys.stderr, flush=True)
        emit({"event": "error", "reason": type(error).__name__})
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
