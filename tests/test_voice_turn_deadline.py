"""The voice turn has its own deadline and keeps the reason of an unsettled turn."""
from pathlib import Path
import subprocess
import sys


BRIDGE = Path(__file__).parents[1] / 'src/universal_userio/voice/agentcall_realtime_bridge.py'

LOAD = r'''
import ast, importlib, pathlib, sys, types
path = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(path.parent))
tree = ast.parse(path.read_text())
assert isinstance(tree.body[-1], ast.If)
assert ast.unparse(tree.body[-1].test) == "__name__ == '__main__'"
tree.body.pop()
entry = types.ModuleType('__main__')
entry.__file__ = str(path)
sys.modules['__main__'] = entry
exec(compile(tree, str(path), 'exec'), entry.__dict__)
bridge = importlib.import_module('agentcall_realtime_bridge')
'''


def _run(program):
    result = subprocess.run([sys.executable, '-c', LOAD + program, str(BRIDGE)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_voice_turn_has_its_own_deadline():
    _run(r'''
import os
os.environ['AGENTCALL_REQUEST_TIMEOUT'] = '30'
os.environ['AGENTCALL_VOICE_REQUEST_TIMEOUT'] = '120'
config = bridge.BackendConfig.from_environment()
assert config.request_timeout == 30.0, config.request_timeout
assert config.voice_request_timeout == 120.0, config.voice_request_timeout
from dataclasses import replace
voice = replace(config, request_timeout=config.voice_request_timeout)
assert voice.request_timeout == 120.0, voice.request_timeout
assert config.request_timeout == 30.0, config.request_timeout
''')


def test_voice_deadline_defaults_to_the_client_cap():
    _run(r'''
import os
os.environ.pop('AGENTCALL_REQUEST_TIMEOUT', None)
os.environ.pop('AGENTCALL_VOICE_REQUEST_TIMEOUT', None)
config = bridge.BackendConfig.from_environment()
assert config.voice_request_timeout == 120.0, config.voice_request_timeout
''')


def test_unsettled_turn_keeps_its_cause():
    _run(r'''
plain = bridge.NativeTurnUnsettled('Secretary voice outcome is unknown; inspect receipt')
assert bridge.call_error_detail(plain) == (
    'NativeTurnUnsettled: Secretary voice outcome is unknown; inspect receipt')
try:
    try:
        raise TimeoutError('timed out')
    except TimeoutError:
        raise bridge.NativeTurnUnsettled(
            'Secretary voice outcome is unknown; inspect receipt') from None
except bridge.NativeTurnUnsettled as masked:
    detail = bridge.call_error_detail(masked)
assert 'cause=TimeoutError: timed out' in detail, detail
''')
