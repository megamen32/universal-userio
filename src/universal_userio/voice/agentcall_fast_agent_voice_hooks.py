"""FastAgent AgentCard hooks using its public StreamChunk/listener API.

Only assistant text crosses this pipe. Reasoning and tool arguments never do.
The CLI still owns its tool loop, admission, history and result export.
"""
import json
import os
import time
from pathlib import Path


def emit(record):
    record = dict(record, at=time.monotonic())
    path = Path(os.environ['AGENTCALL_STREAM_PATH'])
    if path.parent != Path('/run/agentcall/fast-agent') or not path.name.endswith('.stream.jsonl'):
        raise ValueError('invalid voice stream path')
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, 'w') as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + '\n')


async def before_llm_call(ctx):
    state = {'tool': False, 'removers': []}
    ctx.agent.agentcall_voice_stream = state
    emit({'event': 'start'})

    def text(chunk):
        if chunk.event == 'rollback':
            emit({'event': 'rollback'})
        elif chunk.event == 'delta' and not chunk.is_reasoning and not state['tool']:
            emit({'event': 'text', 'text': chunk.text})

    def tool(event, payload):
        if event in {'start', 'delta'}:
            state['tool'] = True
            emit({'event': 'tool'})

    state['removers'] = [ctx.agent.llm.add_stream_listener(text),
                         ctx.agent.llm.add_tool_stream_listener(tool)]


async def after_llm_call(ctx):
    state = getattr(ctx.agent, 'agentcall_voice_stream', {})
    for remove in state.get('removers', []):
        remove()
    # Stop reason/tool payload are not speech. Only the text tail of a normal
    # assistant completion is flushed; tool turns are discarded.
    emit({'event': 'end', 'final': ctx.is_turn_complete and not bool(ctx.message.tool_calls)})
