"""The native script and SDK wrapper must share exception/progress classes."""
from pathlib import Path
import subprocess
import sys


def test_native_script_exports_one_canonical_bridge_module():
    source = Path(__file__).parents[1] / 'src/universal_userio/voice/agentcall_realtime_bridge.py'
    program = r'''
import ast, importlib, pathlib, sys, types
path = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(path.parent))
tree = ast.parse(path.read_text())
# Load the exact native script definitions without starting listeners/main.
assert isinstance(tree.body[-1], ast.If)
assert ast.unparse(tree.body[-1].test) == "__name__ == '__main__'"
tree.body.pop()
entry = types.ModuleType('__main__')
entry.__file__ = str(path)
sys.modules['__main__'] = entry
exec(compile(tree, str(path), 'exec'), entry.__dict__)
canonical = importlib.import_module('agentcall_realtime_bridge')
assert canonical is entry, 'native script and imported wrapper see different bridge modules'
assert canonical.NativeTurnUnsettled is entry.NativeTurnUnsettled
assert canonical.SpokenProgress is entry.SpokenProgress
'''
    result = subprocess.run([sys.executable, '-c', program, str(source)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
