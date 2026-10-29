"""Run the real fixture phrase loop without Matrix, audio hardware or model calls."""
import ast
import asyncio
import contextlib
import io
from pathlib import Path
from types import SimpleNamespace
import unittest


class FixtureBargeTests(unittest.TestCase):
    def run_loop(self, heard=True, fresh=True, tail_required=False, barge_index=0):
        tree = ast.parse((Path(__file__).parent/'manual/matrix_rtc_ai_caller.py').read_text())
        loop = next(n for n in ast.walk(tree) if isinstance(n, ast.For)
                    and ast.unparse(n.iter) == 'enumerate([FIRST_WAV, *SCRIPT_WAVS])')
        fn = ast.AsyncFunctionDef(name='run', args=ast.arguments(posonlyargs=[], args=[],
            kwonlyargs=[], kw_defaults=[], defaults=[]), body=[
                ast.Assign(targets=[ast.Name(id='utterance', ctx=ast.Store())], value=ast.Constant(0)), loop], decorator_list=[])
        module = ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[]))
        events = []
        clock = [10.0]
        def now():
            clock[0] += 0
            return clock[0]
        async def speak(pcm): events.append(('pcm', pcm))
        async def gate(stage): events.append(('gate', stage))
        async def substantive():
            events.append(('substantive_pcm_gate', heard))
            if not heard: raise TimeoutError('no substantive received audio')
        async def tail():
            events.append(('received_tail_gate', False))
            raise TimeoutError('expected final word not yet received')
        async def noop(*args): pass
        namespace = dict(FIRST_WAV='first', SCRIPT_WAVS=(['second'] if barge_index else []),
            os=SimpleNamespace(environ=({'CALLER_WAIT_FULL_ANSWER':'private-jsonl', 'CALLER_REQUIRED_FINAL_TAIL':'кедр'} if tail_required else {'CALLER_WAIT_FULL_ANSWER':'private-jsonl', 'CALLER_BARGE_WAV':'barge','CALLER_BARGE_PHRASE_INDEX':str(barge_index)})),
            _load_wav=lambda path: (path, 48000), source=SimpleNamespace(wait_for_playout=noop),
            speak=speak, completed_answer=gate, wait_reply=noop, wait_substantive_answer=substantive, wait_received_tail=tail, received_pcm=bytearray(),
            time=SimpleNamespace(monotonic=now, time=now),
            asyncio=SimpleNamespace(sleep=noop), last_voice_at=10 if fresh else 0,
            stop=SimpleNamespace(is_set=lambda: False), json=__import__('json'))
        exec(compile(module, '<real fixture phrase loop>', 'exec'), namespace)
        with contextlib.redirect_stdout(io.StringIO()):
            try: asyncio.run(namespace['run']())
            except TimeoutError: return events, True
        return events, False

    def test_barge_follows_received_speech_before_waiting_for_completed_turn(self):
        events, timed_out = self.run_loop()
        self.assertFalse(timed_out)
        self.assertEqual(events, [('pcm','first'), ('substantive_pcm_gate',True), ('pcm','barge'), ('gate','barge')])

    def test_no_received_speech_never_releases_interruption(self):
        events, timed_out = self.run_loop(heard=False)
        self.assertTrue(timed_out)
        self.assertEqual(events, [('pcm','first'), ('substantive_pcm_gate',False)])

    def test_finished_answer_does_not_release_late_barge_after_asr(self):
        events, timed_out = self.run_loop(fresh=False)
        self.assertFalse(timed_out)
        self.assertNotIn(('pcm', 'barge'), events)
        self.assertIn(('gate', 'phrase'), events)

    def test_sdk_terminal_and_silence_do_not_replace_received_final_tail(self):
        events, timed_out = self.run_loop(tail_required=True)
        self.assertTrue(timed_out)
        self.assertEqual(events, [('pcm','first'), ('gate','phrase'), ('received_tail_gate',False)])

    def test_memory_round_barges_only_on_second_original_phrase(self):
        events, timed_out = self.run_loop(barge_index=1)
        self.assertFalse(timed_out)
        self.assertEqual(events, [('pcm','first'), ('gate','phrase'), ('pcm','second'), ('substantive_pcm_gate',True), ('pcm','barge'), ('gate','barge')])

    def test_physical_window_cannot_accumulate_later_idle_prompt(self):
        tree=ast.parse((Path(__file__).parent/'manual/matrix_rtc_ai_caller.py').read_text())
        loop=next(n for n in ast.walk(tree) if isinstance(n,ast.While) and 'received_voiced_samples - baseline < required' in ast.unparse(n.test))
        fn=ast.AsyncFunctionDef(name='run',args=ast.arguments(posonlyargs=[],args=[],kwonlyargs=[],kw_defaults=[],defaults=[]),body=[loop],decorator_list=[])
        clock=[0.]
        async def tick(*args):clock[0]+=.2
        namespace=dict(received_voiced_samples=100,baseline=0,required=48000,deadline=10,last_voice_at=0,stop=SimpleNamespace(is_set=lambda:False),time=SimpleNamespace(monotonic=lambda:clock[0]),asyncio=SimpleNamespace(sleep=tick))
        exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),'<physical gate>','exec'),namespace)
        with self.assertRaises(TimeoutError):asyncio.run(namespace['run']())
        self.assertLess(clock[0],2)
