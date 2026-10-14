"""Fast Agent phone turns must stay bound to one exact Herder session."""

import pytest
import importlib
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1] / "src/universal_userio/voice"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bridge = load_module("agentcall_fast_agent_bridge", ROOT / "agentcall_realtime_bridge.py")


class DummyMCPServer:
    def __init__(self, *_args, **_kwargs):
        pass

    def tool(self, **_kwargs):
        return lambda function: function


mcp_module = types.ModuleType("mcp")
mcp_server_module = types.ModuleType("mcp.server")
mcp_mcpserver_module = types.ModuleType("mcp.server.mcpserver")
mcp_mcpserver_module.MCPServer = DummyMCPServer
with patch.dict(
    sys.modules,
    {
        "mcp": mcp_module,
        "mcp.server": mcp_server_module,
        "mcp.server.mcpserver": mcp_mcpserver_module,
    },
):
    session_mcp = load_module("agentcall_session_mcp_test", ROOT / "agentcall_session_mcp.py")


class FastAgentBridgeTests(unittest.TestCase):
    def test_guest_message_subjects_are_not_promoted_to_caller_identity(self):
        # Fast unit, expected <1s/max2s. Capture the real request/history seam;
        # detects unconditional introductions/name pressure and role inference.
        config=replace(bridge.BackendConfig.from_environment(),system_prompt=bridge.GUEST_SYSTEM_PROMPT,
            llm_provider='openai-compatible',fast_agent_fallback_enabled=False)
        pipeline=bridge.LocalSpeechPipeline(config)
        pipeline.history.append({'role':'assistant','content':bridge.GUEST_GREETING})
        received='Анна встретит Никиту завтра в 10:00 утра. Номер заявки 742.'
        caller='Я ИИ-консьерж. Подтверждаю сообщение.'
        requests=[]
        def respond(url,**kwargs):
            requests.append(json.loads(kwargs['data']))
            return ('{"choices":[{"message":{"content":"Сообщение услышал."}}]}'.encode(), 'application/json')
        with patch.object(bridge,'_request',side_effect=respond):
            pipeline.reply(received);pipeline.reply(caller)
        for request in requests:
            prompt=request['messages'][0]['content']
            self.assertIn('не имя звонящего',prompt)
            self.assertIn('явно представился',prompt)
            self.assertIn('кто что сделает, с кем, когда',prompt)
            self.assertIn('не представляйся повторно',prompt)
            self.assertIn('не требуй имя',prompt)
            self.assertNotIn('Узнай имя собеседника',prompt)
        self.assertEqual(received,requests[0]['messages'][-1]['content'])
        self.assertEqual(['system','assistant','user','assistant','user'],[m['role'] for m in requests[1]['messages']])
        self.assertEqual(received,requests[1]['messages'][2]['content'])
        self.assertEqual(caller,requests[1]['messages'][-1]['content'])
        self.assertFalse(any('tools' in request for request in requests))

    def test_agent_context_is_bounded_and_requires_supported_exact_session(self):
        context = bridge.sanitize_agent_context({
            "session_id": "s" * 400,
            "session_harness": "CODEX",
            "title": "x" * 800,
            "unknown": "not forwarded",
        })
        self.assertEqual("codex", context["session_harness"])
        self.assertEqual(256, len(context["session_id"]))
        self.assertEqual(512, len(context["title"]))
        self.assertNotIn("unknown", context)

        invalid = bridge.sanitize_agent_context({
            "session_id": "session-1",
            "session_harness": "shell",
        })
        self.assertEqual("", invalid["session_id"])
        self.assertEqual("", invalid["session_harness"])

    def test_fast_agent_command_is_restricted_and_context_is_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "fast-agent"
            python = root / "python"
            helper = root / "helper.py"
            for path in (binary, python, helper):
                path.touch()
            (root / "fastagent.config.yaml").write_text("mcp: {servers: {NoticePlace: {command: test-only}}}")
            turn = root / "turn.json"
            turn.write_text("{}", encoding="utf-8")
            calls = []

            def runner(command, **kwargs):
                calls.append((command, kwargs))
                return subprocess.CompletedProcess(
                    command, 0, stdout="<think>hidden</think>Готово.", stderr=""
                )

            config = replace(
                bridge.BackendConfig.from_environment(),
                llm_api_key="test-only",
                fast_agent_bin=str(binary),
                fast_agent_python=str(python),
                fast_agent_mcp_script=str(helper),
                fast_agent_home=str(root),
            )
            brain = bridge.FastAgentIncidentBrain(
                config,
                {"session_id": "session-1", "session_harness": "codex"},
                "Инцидент тестовый",
                runner=runner,
            )
            with patch.object(brain, "_write_context", return_value=turn):
                self.assertEqual("Готово.", brain.reply("Какой статус?"))

            command, kwargs = calls[0]
            self.assertIn("--home", command)
            self.assertEqual(config.fast_agent_home, command[command.index("--home") + 1])
            self.assertEqual(config.fast_agent_home, kwargs["cwd"])
            self.assertEqual(str(root / "fastagent.config.yaml"), command[command.index("--config-path") + 1])
            self.assertEqual("NoticePlace", command[command.index("--servers") + 1])
            self.assertIn("?reasoning=low", command[command.index("--model") + 1])
            self.assertEqual('http://127.0.0.1:18787/mcp',command[command.index('--url')+1])
            self.assertIn("--no-shell", command)
            self.assertIn("--no-subagents", command)
            self.assertIn("--stdio", command)
            self.assertNotIn("AGENTCALL_LLM_API_KEY", kwargs["env"])
            self.assertEqual("test-only", kwargs["env"]["GENERIC_API_KEY"])
            self.assertFalse(turn.exists())

    def test_answer_cleanup_rejects_empty_model_output(self):
        self.assertEqual("Ответ", bridge.clean_fast_agent_answer("<think>x</think> Ответ "))
        with self.assertRaises(RuntimeError):
            bridge.clean_fast_agent_answer("<think>x</think>")

    def test_tool_receipt_survives_next_turn_without_cross_call_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("fast-agent", "python", "helper.py"):
                (root / name).touch()
            config = replace(bridge.BackendConfig.from_environment(), llm_api_key="test-only",
                fast_agent_bin=str(root / "fast-agent"), fast_agent_python=str(root / "python"),
                fast_agent_mcp_script=str(root / "helper.py"), fast_agent_home=str(root))
            prompts = []
            receipt = {"role": "user", "content": [], "tool_results": {
                "read-1": {"content": [{"type": "text", "text":
                    '{"session_id":"exact-target","harness":"opencode"}'}]}}}

            def runner(command, **_kwargs):
                prompt = json.loads(Path(command[command.index("--prompt-file") + 1]).read_text())
                prompts.append(prompt["messages"])
                Path(command[command.index("--results") + 1]).write_text(json.dumps({"messages":
                    prompt["messages"] + [receipt, {"role": "assistant", "content":
                        [{"type": "text", "text": "Сессия прочитана."}]}]}))
                return subprocess.CompletedProcess(command, 0, stdout="Сессия прочитана.", stderr="")

            brain = bridge.FastAgentIncidentBrain(config, {"call_mode": "incoming"}, "", runner=runner)
            brain.history.append(("ассистент", "Алло, слушаю."))
            for index, question in enumerate(("Прочитай сессию", "Передай туда мой выбор")):
                turn = root / f"turn-{index}.json"
                turn.write_text("{}")
                with patch.object(brain, "_write_context", return_value=turn):
                    brain.reply(question)
                self.assertFalse(turn.with_suffix(".prompt.json").exists())
                self.assertFalse(turn.with_suffix(".history.json").exists())
            self.assertEqual("assistant", prompts[0][0]["role"])
            self.assertIn(receipt, prompts[1])
            other = bridge.FastAgentIncidentBrain(config, {"call_mode": "incoming"}, "", runner=runner)
            turn = root / "turn-other.json"
            turn.write_text("{}")
            with patch.object(other, "_write_context", return_value=turn):
                other.reply("Какие инциденты?")
            self.assertNotIn(receipt, prompts[2])


class VoiceFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_configured_voice_is_primary_and_phone_is_not_called(self):
        pcm = b"\xe8\x03" * 20_000
        config = bridge.BackendConfig.from_environment()
        controller = bridge.CallController(config, {}, synthesize=lambda _text: pcm)
        with patch.dict("os.environ", {"AGENTCALL_TTS_PHONE_FALLBACK": "0"}), \
             patch.object(controller, "_synthesize_phone_minimax") as phone:
            result = await controller._synthesize_with_fallback("Проверка")
        self.assertEqual(pcm, result)
        phone.assert_not_called()

    async def test_phone_minimax_is_only_an_opt_in_failure_fallback(self):
        pcm = b"\xe8\x03" * 20_000

        def failed_primary(_text):
            raise RuntimeError("local unavailable")

        config = bridge.BackendConfig.from_environment()
        controller = bridge.CallController(config, {}, synthesize=failed_primary)
        with patch.dict("os.environ", {"AGENTCALL_TTS_PHONE_FALLBACK": "1"}), \
             patch.object(controller, "_synthesize_phone_minimax", return_value=pcm) as phone:
            result = await controller._synthesize_with_fallback("Проверка")
        self.assertEqual(pcm, result)
        phone.assert_called_once_with("Проверка")


class SessionMCPTests(unittest.TestCase):
    def context(self, utterance: str) -> dict[str, str]:
        return {
            "incident_id": "incident-1",
            "event_type": "health.degraded",
            "severity": "critical",
            "title": "Сервер перегружен",
            "body": "Загрузка достигла ста процентов.",
            "session_id": "session/one",
            "session_harness": "codex",
            "source_id": "host:100",
            "host_id": "server-100",
            "signal_type": "cpu",
            "correlation_id": "episode-1",
            "current_utterance": utterance,
            "previous_assistant": "",
        }

    def test_exact_url_encodes_only_bound_pair(self):
        url = session_mcp.exact_session_url(self.context("статус"), "progress?limit=3")
        self.assertEqual(
            "http://127.0.0.1:18787/api/sessions/codex/session%2Fone/progress?limit=3",
            url,
        )

    def test_continue_refuses_non_explicit_status_question(self):
        with patch.object(session_mcp, "_request_json") as request:
            result = session_mcp.continue_session_for(self.context("Как там идет процесс?"))
        self.assertFalse(result["queued"])
        request.assert_not_called()

    def test_natural_explicit_continue_phrases_are_accepted(self):
        for phrase in (
            "Разбери сессию",
            "Короче, давайте заново пилите",
            "Перезапусти восстановление",
        ):
            with self.subTest(phrase=phrase):
                self.assertTrue(session_mcp.explicit_continue_requested(phrase))

    def test_affirmation_requires_the_assistants_immediate_offer(self):
        self.assertTrue(session_mcp.explicit_continue_requested(
            "Да, хочу",
            "Хотите, чтобы я запустил выполнение плана?",
        ))
        self.assertFalse(session_mcp.explicit_continue_requested(
            "Да, хочу",
            "Текущий статус: работа остановлена.",
        ))

    def test_new_session_request_is_rejected_without_delivery(self):
        context = self.context("Запусти дополнительную сессию")
        with patch.object(session_mcp, "_request_json") as request:
            result = session_mcp.continue_session_for(context)
        self.assertFalse(result["queued"])
        self.assertIn("не создаёт", result["reason"])
        request.assert_not_called()

    def test_continue_queues_exact_session_for_explicit_human_request(self):
        with patch.object(
            session_mcp,
            "_request_json",
            return_value={"ok": True, "delivery": "accepted", "sessionStatus": "running"},
        ) as request:
            result = session_mcp.continue_session_for(
                self.context("Продолжи разбираться и исправь проблему")
            )
        self.assertTrue(result["queued"])
        url = request.call_args.args[0]
        payload = request.call_args.kwargs["payload"]
        self.assertIn("/codex/session%2Fone/message", url)
        self.assertEqual("queue", payload["mode"])
        self.assertTrue(payload["humanRequested"])
        self.assertIn("Продолжи разбираться", payload["message"])

    def test_status_does_not_expose_unbounded_last_message(self):
        response = {
            "session": {
                "status": "running",
                "title": "Диагностика",
                "lastMessage": "secret " * 1000,
            },
            "activity": {"hasMessageActivity": True, "hasToolActivity": False},
            "evidence": [
                {"kind": "message", "role": "assistant", "preview": "x" * 500}
            ],
        }
        with patch.object(session_mcp, "_request_json", return_value=response):
            result = session_mcp.session_status_for(self.context("статус"))
        self.assertNotIn("lastMessage", result)
        self.assertNotIn("session_id", result)
        self.assertNotIn("harness", result)
        self.assertEqual(320, len(result["recent_evidence"][0]["preview"]))

    def test_context_file_must_be_private(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "context.json"
            path.write_text(json.dumps(self.context("статус")), encoding="utf-8")
            path.chmod(0o644)
            with self.assertRaises(PermissionError):
                session_mcp.load_context(path, enforce_runtime_path=False)
            path.chmod(0o600)
            loaded = session_mcp.load_context(path, enforce_runtime_path=False)
            self.assertEqual("session/one", loaded["session_id"])


class SessionConversationRegressionTests(unittest.TestCase):
    def test_natural_request_to_return_work_is_recognized(self):
        self.assertTrue(session_mcp.explicit_continue_requested('Передай запрос и вернуть его в работу.'))
        self.assertTrue(session_mcp.explicit_continue_requested('Почини сервис.'))
        self.assertTrue(session_mcp.explicit_continue_requested('Восстанови сервис.'))
        self.assertFalse(session_mcp.explicit_continue_requested('Не почини сервис.'))
        self.assertFalse(session_mcp.explicit_continue_requested('Не нужно исправлять сервис.'))
        self.assertFalse(session_mcp.explicit_continue_requested('Что означает передай запрос?'))

    def test_reader_gets_message_text_instead_of_short_progress_preview(self):
        context=SessionMCPTests().context('Какой адрес отказал?')
        response={'session':{'status':'idle','title':'Диагностика'},'messages':[
            {'role':'assistant','timestamp':'2026-10-11T14:00:00Z','parts':[
                {'type':'thinking','text':'private reasoning'},
                {'type':'text','text':'Подробности. '*50+'Endpoint 127.0.0.1:30802; connection refused.'},
                {'type':'tool_result','name':'bash','output':'curl: connection refused; Bearer abcsecretvalue'}]}]}
        with patch.object(session_mcp,'_request_json',return_value=response) as request:
            result=session_mcp.session_read_for(context)
        self.assertIn('/codex/session%2Fone/details?limit=50',request.call_args.args[0])
        self.assertIn('127.0.0.1:30802',result['recent_messages'][0]['text'])
        self.assertNotIn('private reasoning',json.dumps(result))
        self.assertNotIn('abcsecretvalue',json.dumps(result))

    def test_reader_retains_earlier_cause_report_and_later_repair_in_order(self):
        """Fast unit: lost initial cause under tool-heavy tail; expected <1s/max 10s."""
        initial='Готово.\n## Что сломано\n'+('Correlation пересчитывался из текущего часа; 20:00 episode -> 21:00 recovery. '*15)
        delta='Регрессия закрыта.\n## Что я сломал\n'+('Поздняя регрессия ремонта: отсутствующий state считался успехом. '*15)+'\n50 passed'
        messages=[{'role':'assistant','timestamp':str(i).zfill(2),'parts':[
            {'type':'tool_result','name':'bash','output':'noise output'}]} for i in range(50)]
        messages[10]={'role':'assistant','timestamp':'10','parts':[{'type':'text','text':initial}]}
        messages[49]={'role':'assistant','timestamp':'49','parts':[{'type':'text','text':delta}]}
        response={'session':{'status':'idle'},'history':{'source':'acp-load','complete':False},'messages':messages}
        with patch.object(session_mcp,'_request_json',return_value=response):
            result=session_mcp.session_read_for(SessionMCPTests().context('Почему не закрывались уведомления?'))
        self.assertEqual(['10','49'],[m['at'] for m in result['recent_messages']])
        self.assertIn('20:00 episode',result['recent_messages'][0]['text'])
        self.assertIn('Поздняя регрессия',result['recent_messages'][1]['text'])
        self.assertIn('50 passed',result['recent_messages'][1]['text'])
        self.assertEqual(50,result['coverage']['messages_observed'])
        self.assertFalse(result['coverage']['upstream_complete'])
        self.assertTrue(result['coverage']['earlier_history_may_be_missing'])
        self.assertIn('data',result['context_policy'])

    def test_long_reasoning_prefix_does_not_displace_factual_report_and_truncation_is_visible(self):
        """Fast unit: bounded report excerpt, secrets/reasoning omitted; <1s/max 10s."""
        text=('English planning before final report. '*300)+'\n## Что сломано\n'+'correlation hour-boundary root cause\n'+('факт '*1800)+'\n## Проверено\n50 passed'
        response={'session':{},'messages':[{'role':'assistant','timestamp':'t','parts':[
            {'type':'thinking','text':'private chain'}, {'type':'text','text':text}]}]}
        with patch.object(session_mcp,'_request_json',return_value=response):
            result=session_mcp.session_read_for(SessionMCPTests().context('Почему?'))
        report=result['recent_messages'][0]
        self.assertIn('correlation hour-boundary',report['text'])
        self.assertIn('50 passed',report['text'])
        self.assertNotIn('English planning',report['text'])
        self.assertNotIn('private chain',json.dumps(result))
        self.assertTrue(report['text_truncated'])
        self.assertTrue(report['prefix_omitted'])
        self.assertLessEqual(len(report['text']),6000)

    def test_continuation_uses_one_stable_turn_input_id(self):
        context=SessionMCPTests().context('Почини сервис.')
        context['turn_id']='a'*32
        with patch.object(session_mcp,'_request_json',return_value={'ok':True}) as request:
            result=session_mcp.continue_session_for(context)
        self.assertTrue(result['queued'])
        self.assertEqual('agentcall-'+'a'*32,request.call_args.kwargs['payload']['inputId'])


class ProviderFallbackRegressionTests(unittest.TestCase):
    """Fast unit: quota routing and no replay; expected <1s, maximum 10s."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path.cwd() / '.tmp')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for name in ('fast-agent', 'python', 'helper.py'):
            (self.root / name).touch()
        self.key = self.root / 'provider-key'
        self.key.write_text('INDEPENDENT-SECRET\n')
        self.key.chmod(0o600)
        (self.root / 'fastagent.config.yaml').write_text('llm_retries: 2\nmcp: {servers: {NoticePlace: {command: test-only}}}\n')
        self.config = replace(bridge.BackendConfig.from_environment(),
            llm_api_key='PRIMARY-SECRET', fast_agent_bin=str(self.root / 'fast-agent'),
            fast_agent_python=str(self.root / 'python'), fast_agent_mcp_script=str(self.root / 'helper.py'),
            fast_agent_home=str(self.root), fast_agent_fallback_enabled=True,
            fast_agent_fallback_base_url='https://api.z.ai/api/coding/paas/v4',
            fast_agent_fallback_model='voice-zai', fast_agent_fallback_key_file=str(self.key))
        self.calls = []
        bridge.PRIMARY_PROVIDER_CACHE.clear()

    def brain(self, runner=None):
        def success(command, **kwargs):
            self.calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, stdout='Ответ.', stderr='')
        result = bridge.FastAgentIncidentBrain(self.config,
            {'session_id': 'exact-session', 'session_harness': 'codex'}, '', runner=runner or success)
        def context(text):
            turn = self.root / f'turn-{len(self.calls)}.json'
            turn.write_text(json.dumps({'session_id': 'exact-session', 'turn_id': turn.stem}))
            return turn
        result._write_context = context
        return result

    def test_quota_selects_independent_auth_base_model_and_keeps_call_history(self):
        brain = self.brain()
        receipt = {'role': 'user', 'content': [], 'tool_results': {'accepted': {'content': [
            {'type': 'text', 'text': '{"inputId":"original-input","delivery":"accepted"}'}]}}}
        brain.messages = [receipt]
        def run(command, **kwargs):
            self.calls.append((command, kwargs))
            config_path = Path(command[command.index('--config-path') + 1])
            config_text = config_path.read_text()
            self.assertIn('llm_retries: 0', config_text)
            self.assertNotIn('llm_retries: 2', config_text)
            self.assertIn('NoticePlace', config_text)
            self.assertEqual('NoticePlace', command[command.index('--servers') + 1])
            prompt = json.loads(Path(command[command.index('--prompt-file') + 1]).read_text())
            self.assertIn(receipt, prompt['messages'])
            self.assertIn('exact-session', prompt['messages'][-1]['content'][0]['text'])
            return subprocess.CompletedProcess(command, 0, stdout='Ответ.', stderr='')
        brain.runner = run
        with patch.object(brain, '_probe_primary', return_value=False):
            self.assertEqual('Ответ.', brain.reply('Какой статус?'))
        command, kwargs = self.calls[0]
        self.assertEqual('voice-zai?reasoning=low', command[command.index('--model') + 1])
        self.assertEqual(self.config.fast_agent_fallback_base_url, command[command.index('--base-url') + 1])
        self.assertEqual('INDEPENDENT-SECRET', kwargs['env']['GENERIC_API_KEY'])
        self.assertNotIn('SECRET', ' '.join(command))
        self.assertIn('--url', command)
        self.assertEqual([receipt], brain.messages)
        self.assertEqual([], list(self.root.glob('turn-*')))

    def test_normal_primary_and_disabled_default_are_unchanged(self):
        self.assertFalse(bridge.BackendConfig.from_environment().fast_agent_fallback_enabled)
        brain = self.brain()
        with patch.object(brain, '_probe_primary', return_value=True):
            brain.reply('Статус')
        command, kwargs = self.calls[0]
        self.assertEqual('voice-minimax?reasoning=low', command[command.index('--model') + 1])
        self.assertEqual('PRIMARY-SECRET', kwargs['env']['GENERIC_API_KEY'])
        brain.config = replace(self.config, fast_agent_fallback_enabled=False)
        with patch.object(brain, '_probe_primary') as probe:
            brain.reply('Еще статус')
        probe.assert_not_called()
        self.assertNotIn('FAST_AGENT_RETRIES', self.calls[-1][1]['env'])

    def test_provider_quota_preflight_is_bounded_tool_free_and_cached_between_calls(self):
        import urllib.error
        brain = self.brain()
        refusal = urllib.error.HTTPError('https://api.minimax.io/v1/chat/completions', 429,
                                         'quota', {}, None)
        with patch.object(bridge.urllib.request, 'urlopen', side_effect=refusal) as request:
            brain.reply('Статус')
            other = self.brain()
            other.reply('Другой звонок')
        self.assertEqual(1, request.call_count)
        self.assertEqual('GET',request.call_args.args[0].get_method())
        self.assertIsNone(request.call_args.args[0].data)
        self.assertEqual('https://www.minimax.io/v1/token_plan/remains',request.call_args.args[0].full_url)
        self.assertLessEqual(request.call_args.kwargs['timeout'], 5)
        self.assertTrue(all(c[c.index('--model') + 1].startswith('voice-zai') for c, _ in self.calls))

    def test_quota_get_uses_general_status_percent_not_deprecated_counts(self):
        import io
        row={'model_name':'general','current_interval_status':2,'current_interval_remaining_percent':0,
             'current_weekly_status':1,'current_weekly_remaining_percent':63,'end_time':(bridge.time.time()+3600)*1000,
             'total_count':0,'usage_count':0}
        data={'base_resp':{'status_code':0},'model_remains':[row]}
        with patch.object(bridge.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(data).encode())) as probe:
            self.brain().reply('Статус')
        self.assertEqual('INDEPENDENT-SECRET',self.calls[-1][1]['env']['GENERIC_API_KEY'])
        self.assertEqual('GET',probe.call_args.args[0].get_method())
        bridge.PRIMARY_PROVIDER_CACHE.clear()
        row.update(current_interval_status=1,current_interval_remaining_percent=12)
        with patch.object(bridge.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(data).encode())):
            self.brain().reply('Статус')
        self.assertEqual('PRIMARY-SECRET',self.calls[-1][1]['env']['GENERIC_API_KEY'])

    def test_invalid_quota_metadata_is_not_proof_of_exhaustion(self):
        import io
        for payload in ({'base_resp':{'status_code':1}}, {'base_resp':{'status_code':0},'model_remains':[]},
                        {'base_resp':{'status_code':0},'model_remains':[{'model_name':'general','current_interval_remaining_percent':0}]}):
            bridge.PRIMARY_PROVIDER_CACHE.clear()
            with patch.object(bridge.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(payload).encode())):
                self.brain().reply('Статус')
            self.assertEqual('PRIMARY-SECRET',self.calls[-1][1]['env']['GENERIC_API_KEY'])

    def test_ambiguous_preflight_failure_does_not_select_fallback(self):
        brain = self.brain()
        with patch.object(bridge.urllib.request, 'urlopen', side_effect=TimeoutError('unknown')):
            brain.reply('Статус')
        self.assertEqual('PRIMARY-SECRET', self.calls[0][1]['env']['GENERIC_API_KEY'])

    def test_backoff_expires_and_restored_primary_is_preferred_again(self):
        import urllib.error
        brain = self.brain()
        refusal = urllib.error.HTTPError('https://api.minimax.io/v1/chat/completions', 429,
                                         'quota', {}, None)
        with patch.object(bridge.time, 'monotonic', return_value=100), \
             patch.object(bridge.urllib.request, 'urlopen', side_effect=refusal):
            brain.reply('Первый статус')
        with patch.object(bridge.time, 'monotonic', return_value=120), \
             patch.object(bridge.urllib.request, 'urlopen') as probe:
            brain.reply('Второй статус')
        probe.assert_not_called()
        with patch.object(bridge.time, 'monotonic', return_value=161), \
             patch.object(bridge.urllib.request, 'urlopen') as restored:
            brain.reply('Третий статус')
        restored.assert_called_once()
        self.assertEqual(['INDEPENDENT-SECRET', 'INDEPENDENT-SECRET', 'PRIMARY-SECRET'],
                         [kwargs['env']['GENERIC_API_KEY'] for _, kwargs in self.calls])

    def test_failed_tool_turn_is_not_retried_and_receipt_survives_next_provider(self):
        receipt = {'role': 'user', 'content': [], 'tool_results': {'send': {'content': [
            {'type': 'text', 'text': '{"inputId":"stable-input","delivery":"unknown"}'}]}}}
        def failed(command, **kwargs):
            self.calls.append((command, kwargs))
            prompt = json.loads(Path(command[command.index('--prompt-file') + 1]).read_text())
            Path(command[command.index('--results') + 1]).write_text(json.dumps({'messages': prompt['messages'] + [receipt]}))
            return subprocess.CompletedProcess(command, 1, stdout='Error code: 429 PRIMARY-SECRET', stderr='')
        brain = self.brain(failed)
        with patch.object(brain, '_probe_primary', return_value=True), patch.object(bridge, '_log') as log:
            with self.assertRaises(RuntimeError) as failure:
                brain.reply('Передай мой ответ')
        self.assertEqual(1, len(self.calls))
        self.assertIn(receipt, brain.messages)
        self.assertNotIn('SECRET', str(failure.exception))
        self.assertNotIn('SECRET', str(log.call_args_list))
        def next_turn(command, **kwargs):
            self.calls.append((command, kwargs))
            prompt = json.loads(Path(command[command.index('--prompt-file') + 1]).read_text())
            self.assertIn(receipt, prompt['messages'])
            self.assertIn('не повторяй', prompt['messages'][-1]['content'][0]['text'].lower())
            return subprocess.CompletedProcess(command, 0, stdout='Проверяю статус.', stderr='')
        brain.runner = next_turn
        with patch.object(bridge.urllib.request, 'urlopen') as probe:
            brain.reply('Какой статус доставки?')
        probe.assert_not_called()
        self.assertEqual(2, len(self.calls))
        self.assertEqual('INDEPENDENT-SECRET', self.calls[-1][1]['env']['GENERIC_API_KEY'])

    def test_fallback_auth_is_private_and_missing_auth_never_reuses_primary(self):
        for mode in (0o644, None):
            if mode is None:
                self.key.unlink()
            else:
                self.key.chmod(mode)
            brain = self.brain()
            with patch.object(brain, '_probe_primary', return_value=False):
                with self.assertRaisesRegex(RuntimeError, 'fallback.*credential'):
                    brain.reply('Статус')
        self.assertEqual([], self.calls)

    def test_fallback_failure_redacts_auth_and_never_retries(self):
        def failed(command, **kwargs):
            self.calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 1, stdout='', stderr='INDEPENDENT-SECRET PRIMARY-SECRET')
        brain = self.brain(failed)
        with patch.object(brain, '_probe_primary', return_value=False), patch.object(bridge, '_log') as log:
            with self.assertRaises(RuntimeError) as failure:
                brain.reply('Статус')
        self.assertEqual(1, len(self.calls))
        self.assertNotIn('SECRET', str(failure.exception))
        self.assertNotIn('SECRET', str(log.call_args_list))

    def test_unknown_runner_completion_is_not_replayed(self):
        def unknown(command, **kwargs):
            self.calls.append((command, kwargs))
            raise subprocess.TimeoutExpired(command, 1, stderr='PRIMARY-SECRET')
        brain = self.brain(unknown)
        with patch.object(brain, '_probe_primary', return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'no action replay') as failure:
                brain.reply('Передай ответ')
        self.assertEqual(1, len(self.calls))
        self.assertTrue(brain.turn_interrupted)
        self.assertNotIn('SECRET', str(failure.exception))
        self.assertEqual([], list(self.root.glob('turn-*')))

    def test_low_high_are_explicit_supported_efforts_without_disabling_thinking(self):
        brain = self.brain()
        for effort in ('low', 'high'):
            brain.config = replace(self.config, llm_reasoning_effort=effort)
            with patch.object(brain, '_probe_primary', return_value=False):
                brain.reply('Статус')
            command = self.calls[-1][0]
            self.assertEqual('voice-zai?reasoning=' + effort, command[command.index('--model') + 1])
            self.assertNotIn('disabled', ' '.join(command))



class RawOpeningFallbackTests(unittest.IsolatedAsyncioTestCase):
    """Fast unit: generated opening and audible assistant history; max 10s."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path.cwd() / '.tmp')
        self.addCleanup(self.directory.cleanup)
        environment=patch.dict('os.environ',{'AGENTCALL_RECORDING_DIRECTORY':str(Path(self.directory.name)/'recordings')})
        environment.start();self.addCleanup(environment.stop)
        adapter=patch.dict(sys.modules,{'agentcall_recording_transcribe':types.SimpleNamespace(submit_transcription=lambda manifest:{'status':'QUEUED'})})
        adapter.start();self.addCleanup(adapter.stop)
        self.key = Path(self.directory.name) / 'key'
        self.key.write_text('INDEPENDENT-SECRET')
        self.key.chmod(0o600)
        self.config = replace(bridge.BackendConfig.from_environment(),
            llm_provider='openai-compatible', llm_url='https://api.minimax.io/v1/chat/completions',
            llm_model='MiniMax-M3.1-Flash-Preview', llm_api_key='PRIMARY-SECRET',
            fast_agent_fallback_enabled=True, fast_agent_fallback_key_file=str(self.key),
            fast_agent_fallback_base_url='https://api.z.ai/api/coding/paas/v4',
            llm_fallback_model='glm-5.3-flash')
        bridge.PRIMARY_PROVIDER_CACHE.clear()
        self.generated = 'Сервис восстановлен. Слышно ли меня?'

    def response(self, url, **kwargs):
        payload = json.loads(kwargs['data'])
        self.assertEqual('https://api.z.ai/api/coding/paas/v4/chat/completions', url)
        self.assertEqual('glm-5.3-flash', payload['model'])
        self.assertNotEqual('voice-zai', payload['model'])
        self.assertEqual('Bearer INDEPENDENT-SECRET', kwargs['headers']['Authorization'])
        self.assertNotIn('tools', payload)
        self.assertNotIn('max_completion_tokens', payload)
        self.assertEqual(self.config.llm_max_completion_tokens, payload['max_tokens'])
        self.assertEqual('low', payload['reasoning_effort'])
        self.assertIn('Контекст звонка:', payload['messages'][-1]['content'])
        return json.dumps({'choices': [{'message': {'content': self.generated}}]}).encode(), 'application/json'

    async def test_quota_generates_opening_and_audible_first_message_is_assistant(self):
        import asyncio
        import urllib.error
        import uuid
        config = self.config
        pipeline = bridge.LocalSpeechPipeline(config)
        refusal = urllib.error.HTTPError(config.llm_url, 429, 'quota', {}, None)
        with patch.object(bridge.urllib.request, 'urlopen', side_effect=refusal) as probe, \
             patch.object(bridge, '_request', side_effect=self.response) as request:
            opening = pipeline.opening('Старый текст инцидента', 'Проверка голоса')
        self.assertEqual(self.generated, opening)
        self.assertEqual(1, request.call_count)
        probe.assert_called_once()
        other = bridge.FastAgentIncidentBrain(config, {'call_mode': 'incoming'}, '')
        with patch.object(bridge.urllib.request, 'urlopen') as shared_probe:
            self.assertEqual('INDEPENDENT-SECRET', other._provider_route()[2])
        shared_probe.assert_not_called()
        self.assertEqual('PRIMARY-SECRET', pipeline.config.llm_api_key)
        self.assertEqual(config.tts_api_key, pipeline.config.tts_api_key)

        # Run the actual greeting consumer in memory; no phone, SIP or TTS calls.
        call_id = uuid.uuid4()
        prepared = bridge.PreparedCall(greeting_text=opening, greeting_pcm=b'pcm',
            attention_text='Алло', attention_pcm=b'pcm', context='Проверка голоса',
            agent_context={'session_id': 'exact', 'session_harness': 'codex'}, created_at=0)
        pipelines, brains = [], []
        real_pipeline, real_brain = bridge.LocalSpeechPipeline, bridge.FastAgentIncidentBrain
        def make_pipeline(value):
            result = real_pipeline(value); pipelines.append(result); return result
        def make_brain(*args):
            result = real_brain(*args); brains.append(result); return result
        async def parked(*args,**kwargs):
            await asyncio.Future()
        async def yes(*args, **kwargs):
            return True
        played = []
        async def play(writer, chunks, hung_up, **kwargs):
            played.append(True)
            if len(played) == 2:
                hung_up.set()
        async def noop(*args, **kwargs):
            pass
        class Writer:
            def close(self): pass
            async def wait_closed(self): pass
        class Broker:
            async def emit(self, event): pass
        with patch.object(bridge, 'LocalSpeechPipeline', side_effect=make_pipeline), \
             patch.object(bridge, 'FastAgentIncidentBrain', side_effect=make_brain), \
             patch.object(bridge, 'read_audiosocket_frame', return_value=(bridge.UUID_KIND, call_id.bytes)), \
             patch.object(bridge, 'pump_audio', side_effect=parked), \
             patch.object(bridge, 'wait_for_sip_answer', side_effect=yes), \
             patch.object(bridge, 'play_attention_until_remote_audio', side_effect=yes), \
             patch.object(bridge, 'send_pcm_stream', side_effect=play):
            await bridge.handle_call(object(), Writer(), Broker(), config, {str(call_id): prepared}, hangup=noop)
        self.assertEqual({'role': 'assistant', 'content': opening}, pipelines[0].history[1])
        self.assertEqual([('ассистент', opening)], brains[0].history)

    async def test_raw_quota_after_available_probe_retries_only_tool_free_generation(self):
        import urllib.error
        primary_refusal = urllib.error.HTTPError(self.config.llm_url, 429, 'quota', {}, None)
        def request(url, **kwargs):
            if url == self.config.llm_url:
                raise primary_refusal
            return self.response(url, **kwargs)
        pipeline = bridge.LocalSpeechPipeline(self.config)
        with patch.object(bridge.VoiceProviderRouter, '_probe_primary', return_value=True), \
             patch.object(bridge, '_request', side_effect=request) as calls:
            self.assertEqual(self.generated, pipeline.opening('Инцидент', 'Проверка'))
        self.assertEqual(2, calls.call_count)
        self.assertFalse(next(iter(bridge.PRIMARY_PROVIDER_CACHE.values()))[1])

    async def test_disabled_fallback_preserves_primary_request_and_constructor(self):
        config = replace(self.config, fast_agent_fallback_enabled=False)
        self.key.unlink()
        pipeline = bridge.LocalSpeechPipeline(config)
        def primary(url, **kwargs):
            self.assertEqual(config.llm_url, url)
            payload = json.loads(kwargs['data'])
            self.assertEqual(config.llm_model, payload['model'])
            self.assertIn('max_completion_tokens', payload)
            self.assertEqual('Bearer PRIMARY-SECRET', kwargs['headers']['Authorization'])
            return json.dumps({'choices': [{'message': {'content': 'Основной ответ.'}}]}).encode(), 'application/json'
        with patch.object(bridge.urllib.request, 'urlopen') as probe, \
             patch.object(bridge, '_request', side_effect=primary):
            self.assertEqual('Основной ответ.', pipeline.reply('Привет'))
        probe.assert_not_called()
        self.assertIs(config, pipeline.config)
        self.assertEqual('assistant', pipeline.history[-1]['role'])



class CallPeerSnapshotTests(unittest.IsolatedAsyncioTestCase):
    """Fast unit: call-scoped peer identity, no device/LLM/TTS; max 10s."""

    def setUp(self):
        directory=tempfile.TemporaryDirectory(dir=Path.cwd()/'.tmp');self.addCleanup(directory.cleanup)
        environment=patch.dict('os.environ',{'AGENTCALL_RECORDING_DIRECTORY':directory.name})
        environment.start();self.addCleanup(environment.stop)
        adapter=patch.dict(sys.modules,{'agentcall_recording_transcribe':types.SimpleNamespace(submit_transcription=lambda manifest:{'status':'QUEUED'})})
        adapter.start();self.addCleanup(adapter.stop)
        self.target = '+79990000001'
        self.config = replace(bridge.BackendConfig.from_environment(),
            outbound_target=self.target, peer_account_ref='phone100')

    async def test_v1_identity_is_direction_independent_and_account_scoped(self):
        import hashlib
        with patch.dict('os.environ', {'AGENTCALL_PEER_ACCOUNT_REF': 'phone100'}):
            self.assertEqual('phone100', bridge.BackendConfig.from_environment().peer_account_ref)
        with patch.dict('os.environ', {'AGENTCALL_PEER_ACCOUNT_REF': ''}):
            self.assertEqual('', bridge.BackendConfig.from_environment().peer_account_ref)
        expected = 'phone:v1:' + hashlib.sha256(b'phone100\x0079990000001').hexdigest()
        self.assertEqual(('phone100', expected), bridge.call_peer_snapshot('phone100', self.target))
        self.assertEqual(('phone100', expected), bridge.call_peer_snapshot('phone100', '8 (999) 000-00-01'))
        self.assertNotEqual(expected, bridge.call_peer_snapshot('phone-other', self.target)[1])
        self.assertEqual(('', ''), bridge.call_peer_snapshot('', self.target))
        self.assertEqual(('', ''), bridge.call_peer_snapshot('phone100', 'hidden'))
        event = bridge.safe_event('active', 'call-uuid', accountRef='phone100', peerRef=expected)
        self.assertNotIn(self.target, json.dumps(event))
        with self.assertRaises(ValueError):
            bridge.safe_event('active', 'call-uuid', phoneNumber=self.target)

    async def test_outbound_snapshot_precedes_generation_and_controls_actual_originate(self):
        import asyncio
        from unittest.mock import AsyncMock
        prepared, snapshots, commands = {}, [], []
        pcm = b'\xe8\x03' * 30_000
        def opening(message, context):
            controller.config = replace(controller.config, outbound_target='+79990000002', peer_account_ref='other')
            return 'Проверяю сервис.'
        async def originate(call_id):
            snapshots.append(prepared[call_id])
            await controller._originate(call_id)
            prepared.pop(call_id)
        async def process(*args, **kwargs):
            commands.append(args)
            return types.SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b'', b'')))
        controller = bridge.CallController(self.config, prepared, generate_opening=opening, originate=originate)
        with patch.object(bridge, 'load_wait_prompts', return_value=(('Жду.', pcm),)), \
             patch.object(controller, '_synthesize_with_fallback', new=AsyncMock(return_value=pcm)), \
             patch.object(controller, '_ensure_s21_registered'), \
             patch.object(bridge.asyncio, 'create_subprocess_exec', side_effect=process):
            receipt = await controller.prepare('Проверка', repeat=1)
        snapshot = snapshots[0]
        self.assertEqual('phone100', snapshot.account_ref)
        self.assertEqual(bridge.call_peer_snapshot('phone100', self.target)[1], snapshot.peer_ref)
        self.assertEqual(self.target, snapshot.outbound_target)
        self.assertEqual(self.target, snapshot.peer_label)
        self.assertIn('Local/' + self.target + '@gateway-outbound', commands[0][-1])
        self.assertNotIn('+79990000002', commands[0][-1])
        self.assertIn(receipt['call_id'], commands[0][-1])

    async def test_incoming_snapshots_actual_normalized_caller(self):
        pcm = b'\xe8\x03' * 30_000
        prepared = {}
        controller = bridge.CallController(self.config, prepared)
        with patch.object(Path, 'read_bytes', return_value=b'fixture-wave'), \
             patch.object(bridge, 'wav_to_native_pcm', return_value=bridge.SpeechPCM(pcm)), \
             patch.object(bridge, 'load_wait_prompts', return_value=(('Жду.', pcm),)):
            receipt = controller.prepare_incoming('8 (999) 000-00-01')
        call = prepared[receipt['call_id']]
        self.assertEqual(('phone100', bridge.call_peer_snapshot('phone100', self.target)[1]),
                         (call.account_ref, call.peer_ref))
        self.assertEqual('incoming', call.agent_context['call_mode'])
        self.assertEqual(self.target, call.peer_label)

    async def scenario(self, *, known=True, incoming=False, fail=False):
        import asyncio, uuid
        pcm = b'\xe8\x03' * 30_000
        call_id = uuid.uuid4()
        account, peer = bridge.call_peer_snapshot('phone100', self.target)
        prepared = bridge.PreparedCall('Начало.', pcm, 'Алло.', pcm, '',
            {'call_mode': 'incoming'} if incoming else {}, 0,
            wait_prompts=(('Подождите.', pcm),), account_ref=account, peer_ref=peer,
            outbound_target=self.target, peer_label=self.target)
        events = []
        class Pipeline:
            def __init__(self, config): self.config = config; self.history = []
            def opening(self, *_args): raise AssertionError('opening already prepared')
            def transcribe(self, pcm): return 'Как дела?'
            def reply(self, text):
                if fail: raise RuntimeError('fixture unavailable')
                return 'Сервис работает.'
            def synthesize(self, text): return pcm
        class Writer:
            def close(self): pass
            async def wait_closed(self): pass
        class Broker:
            async def emit(self, event): events.append(event)
        async def pump(*args,**kwargs): await asyncio.Future()
        async def capture(audio_queue, utterances, detector):
            await utterances.put(pcm)
            await utterances.put(None)
        async def ready(*args, **kwargs): return True
        async def send(*args, **kwargs): pass
        async def play(writer, chunks, queue, detector, hung_up, **kwargs):
            hung_up.set()
        async def hangup(*args): pass
        from unittest.mock import AsyncMock
        newer = replace(self.config, outbound_target='+79990000002', greeting='Начало.', greeting_wav='', peer_account_ref='other')
        reader = bridge.BytesReader(bridge.pack_audiosocket_frame(bridge.UUID_KIND, call_id.bytes))
        with patch.object(bridge, 'LocalSpeechPipeline', Pipeline), \
             patch.object(bridge, 'FastAgentIncidentBrain', return_value=types.SimpleNamespace(history=[], reply=lambda _: 'Сервис работает.')), \
             patch.object(bridge, 'pump_audio', side_effect=pump), \
             patch.object(bridge, 'capture_remote_audio', side_effect=capture), \
             patch.object(bridge, 'wait_for_sip_answer', side_effect=ready), \
             patch.object(bridge, 'play_attention_until_remote_audio', side_effect=ready), \
             patch.object(bridge, 'send_pcm_stream', side_effect=send), \
             patch.object(bridge, 'play_with_barge_in', side_effect=play), \
             patch.object(bridge, 'load_wait_prompts', return_value=(('Подождите.', pcm),)), \
             patch.object(bridge.CallController, '_synthesize_with_fallback', new=AsyncMock(return_value=pcm)):
            await bridge.handle_call(reader, Writer(), Broker(), newer,
                {str(call_id): prepared} if known else {}, hangup=hangup)
        return events, str(call_id), peer

    async def test_every_call_and_ack_event_keeps_snapshot_and_original_uuid(self):
        for incoming, fail in ((False, False), (True, False), (False, True)):
            with self.subTest(incoming=incoming, fail=fail):
                events, call_id, peer = await self.scenario(incoming=incoming, fail=fail)
                self.assertIn('Подождите.', [e.get('text') for e in events])
                self.assertEqual('ended', events[-1]['event'])
                if fail: self.assertIn('error', [e['event'] for e in events])
                for event in events:
                    self.assertEqual(call_id, event['callId'])
                    self.assertEqual('phone100', event['accountRef'])
                    self.assertEqual(peer, event['peerRef'])
                    self.assertIn('at', event)
                    self.assertEqual(self.target, event['peerLabel'])
                    self.assertNotIn(self.target, json.dumps({k:v for k,v in event.items() if k!='peerLabel'}))
                    self.assertNotIn('phoneNumber', event)

    async def test_unknown_uuid_never_infers_peer_from_current_target(self):
        events, call_id, peer = await self.scenario(known=False)
        self.assertTrue(events)
        for event in events:
            self.assertEqual(call_id, event['callId'])
            self.assertNotIn('peerRef', event)
            self.assertNotIn('accountRef', event)


if __name__ == "__main__":
    unittest.main()


@pytest.fixture(autouse=True)
def isolate_voice_engine(monkeypatch, tmp_path):
    """Keep migrated engine tests away from operator identities/archive/STT."""
    monkeypatch.setenv('EXMANAGER_VOICE_CONFIG_FILE',str(tmp_path/'absent-config'))
    monkeypatch.delenv('EXMANAGER_VOICE_URL',raising=False)
    monkeypatch.setenv('AGENTCALL_RECORDING_DIRECTORY',str(tmp_path/'recordings'))
    sys.path.insert(0,str(ROOT))
    transcribe=importlib.import_module('agentcall_recording_transcribe')
    monkeypatch.setattr(transcribe,'submit_transcription',lambda *a:{'status':'QUEUED'})
    yield
    sys.path.remove(str(ROOT))
