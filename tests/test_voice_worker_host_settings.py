"""Native hosts keep protected MCP auth/config when a call is bound."""
import asyncio
import importlib.util
import io
import json
from pathlib import Path
import sys
import types

import pytest
import yaml

PATH = Path(__file__).resolve().parents[1] / 'src/universal_userio/voice/agentcall_fast_agent_worker.py'
spec = importlib.util.spec_from_file_location('host_settings_worker', PATH)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


def test_call_boot_preserves_protected_host_mcp_settings(tmp_path, monkeypatch):
    config = tmp_path / 'config.yaml'
    card = tmp_path / 'card.json'
    protected = {'transport': 'http', 'url': 'http://host-local.test/mcp',
                 'headers': {'Authorization': 'fixture-only'}, 'timeout': 12}
    config.write_text(yaml.safe_dump({'mcp': {'servers': {
        'AgentHerder': protected, 'agentcall': {'env': {'HOST_SCOPE': 'fixture'}}}}}))
    card.write_text(json.dumps({'name': 'voice'}))
    boot = {'config': str(config), 'card': str(card), 'base_url': 'http://provider.test/v1',
            'herder_url': 'http://wrong-default.test/mcp', 'mcp_python': '/host/python',
            'mcp_script': '/host/bridge.py', 'context': '/call/context.json',
            'model': 'fixture', 'home': str(tmp_path)}
    class SetupComplete(Exception):
        pass
    def ready(*args, **kwargs):
        raise SetupComplete
    for name, values in {
        'fast_agent.core.fastagent': {'FastAgent': ready},
        'fast_agent.mcp.prompt_serialization': {'load_messages': None, 'to_json_bytes': None},
        'fast_agent.types': {'RequestParams': None},
        'fast_agent.core.exceptions': {'walk_exception_chain': None},
    }.items():
        module = types.ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(worker, 'private_path', Path)
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(boot) + '\n'))
    with pytest.raises(SetupComplete):
        asyncio.run(worker.serve(io.StringIO()))
    servers = yaml.safe_load(config.read_text())['mcp']['servers']
    assert servers['AgentHerder'] == protected
    assert servers['agentcall']['env'] == {'HOST_SCOPE': 'fixture'}
    assert servers['agentcall']['args'][-1] == '--live-context'


def test_spoken_policies_keep_exact_diagnostic_domain_and_error_code():
    bridge_spec = importlib.util.spec_from_file_location('diagnostic_policy_bridge', PATH.with_name('agentcall_realtime_bridge.py'))
    bridge = importlib.util.module_from_spec(bridge_spec)
    sys.modules[bridge_spec.name] = bridge
    bridge_spec.loader.exec_module(bridge)
    for policy in (worker.VOICE_LANGUAGE_POLICY, bridge.VOICE_INSTRUCTION):
        assert 'Домен и код ошибки сохраняй точно' in policy
        assert 'notify.bezrabotnyi.com' in policy and 'HTTP 502' in policy
        assert 'Не произноси адреса' not in policy


def test_status_incoming_and_opening_prompts_keep_diagnostic_address(monkeypatch):
    bridge_spec = importlib.util.spec_from_file_location('all_diagnostic_policy_bridge', PATH.with_name('agentcall_realtime_bridge.py'))
    bridge = importlib.util.module_from_spec(bridge_spec)
    sys.modules[bridge_spec.name] = bridge
    bridge_spec.loader.exec_module(bridge)
    config = bridge.BackendConfig.from_environment()
    brain = bridge.FastAgentIncidentBrain(config, {'call_mode': 'incoming'}, '')
    prompts = [brain._status_prompt('Как дела?', {'incidents': [], 'total_in_scope': 0}),
               brain._prompt('Какой сайт вернул 502?')]
    monkeypatch.setattr(bridge.LocalSpeechPipeline, 'reply', lambda self, _: prompts.append(self.config.system_prompt) or 'Ответ.')
    bridge.LocalSpeechPipeline(config).opening('На notify.bezrabotnyi.com HTTP 502', '')
    for prompt in prompts:
        assert 'Домен и код ошибки сохраняй точно' in prompt
