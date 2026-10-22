"""The private transport never adopts another app or a stale credential."""
import base64
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('sdk_tunnel', Path(__file__).resolve().parents[1] / 'scripts/secretary_sdk_tunnel.py')
tunnel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tunnel)


def snapshot(**claims):
    body = {'agent_id': tunnel.APP_ID, 'sub': tunnel.APP_ID, 'exp': 2000, 'token_version': 7, **claims}
    encoded = base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip('=')
    return {'address': '192.168.128.7', 'token': 'fixture.' + encoded + '.fixture',
            'containerId': 'fixed-container', 'imageId': 'fixed-image'}


def test_exact_app_upstream_and_fresh_token_are_accepted():
    value = snapshot()
    assert tunnel.validate(value) == ('192.168.128.7', value['token'], 7)


@pytest.mark.parametrize('claims', [{'agent_id': 'another-app'}, {'sub': 'another-app'}])
def test_other_app_token_is_denied_before_forwarding(claims):
    with pytest.raises(ValueError):
        tunnel.validate(snapshot(**claims))


def test_public_or_loopback_upstream_is_denied():
    for address in ('8.8.8.8', '127.0.0.1'):
        value = dict(snapshot(), address=address)
        with pytest.raises(ValueError):
            tunnel.validate(value)


def test_native_opaque_bearer_does_not_invent_a_jwt_expiry_gate():
    value = snapshot(exp=1)
    assert tunnel.validate(value)[1] == value["token"]
