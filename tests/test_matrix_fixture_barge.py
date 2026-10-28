"""Run the real fixture phrase loop without Matrix, audio hardware or model calls."""
import ast
import asyncio
import contextlib
import io
from pathlib import Path
from types import SimpleNamespace
import unittest


class FixtureBargeTests(unittest.TestCase):
    def run_loop(self, heard=True):
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
            clock[0] += 10
            return clock[0]
        async def speak(pcm): events.append(('pcm', pcm))
        async def gate(stage): events.append(('gate', stage))
        async def substantive():
            events.append(('substantive_pcm_gate', heard))
            if not heard: raise TimeoutError('no substantive received audio')
        async def noop(*args): pass
        namespace = dict(FIRST_WAV='first', SCRIPT_WAVS=[],
            os=SimpleNamespace(environ={'CALLER_WAIT_FULL_ANSWER':'private-jsonl', 'CALLER_BARGE_WAV':'barge'}),
            _load_wav=lambda path: (path, 48000), source=SimpleNamespace(wait_for_playout=noop),
            speak=speak, completed_answer=gate, wait_reply=noop, wait_substantive_answer=substantive, received_pcm=bytearray(),
            time=SimpleNamespace(monotonic=now, time=now),
            asyncio=SimpleNamespace(sleep=noop), last_voice_at=100000 if heard else 0,
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
