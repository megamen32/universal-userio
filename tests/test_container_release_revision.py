"""A revision label is intentional; secret runtime settings must stay exact."""
import copy
import importlib.util
from pathlib import Path


def test_reviewed_revision_label_does_not_fail_an_otherwise_identical_cutover():
    path = Path(__file__).resolve().parents[1] / 'scripts/container_release.py'
    spec = importlib.util.spec_from_file_location('revision_release', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    previous = {'Image': 'old', 'Labels': {}, 'Env': ['SENTINEL=preserve'],
                'Cmd': ['original-worker'], 'WorkingDir': '/opt/application'}
    frozen = copy.deepcopy(previous)
    expected = module._backend_replacement_config(previous, image='new', revision='reviewed-pin')
    observed = dict(previous, Image='new', Labels={'org.opencontainers.image.revision': 'reviewed-pin'})
    assert observed == expected
    assert previous == frozen
    assert dict(observed, Env=['SENTINEL=changed']) != expected
    assert dict(observed, Cmd=['different-worker']) != expected
    previous['Labels'] = {'owner': 'preserve', 'org.opencontainers.image.revision': 'old-pin'}
    revised = module._backend_replacement_config(previous, image='new', revision='reviewed-pin')
    assert revised['Labels'] == {'owner': 'preserve', 'org.opencontainers.image.revision': 'reviewed-pin'}
