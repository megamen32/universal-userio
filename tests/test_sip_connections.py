"""Fast unit expected0.2/max2s: per-owner SIP identity, privilege and config isolation."""
import json, re, stat
from types import SimpleNamespace
import pytest
from universal_userio.sip_connections import Registry

def registry(tmp_path):
    calls=[]
    def runner(argv,**kwargs):
        calls.append(argv)
        return SimpleNamespace(stdout='Status : UNKNOWN\n',returncode=0)
    include=tmp_path/'sip-userio.conf';include.touch()
    return Registry(tmp_path/'private',include,runner=runner),calls,include

def test_hosted_peer_is_stable_owned_and_not_root_gateway(tmp_path):
    reg,calls,include=registry(tmp_path)
    one=reg.handle(dict(action='create',user_id='alice',device_id='my-s21',display_name='My phone'))
    other=reg.handle(dict(action='create',user_id='bob',device_id='my-s21',display_name='Other'))
    assert one['id']!=other['id'] and one['sip_password']!=other['sip_password']
    assert one['id'].startswith('phone:sip:uio-') and not one['call_ready']
    assert one==reg.handle(dict(action='create',user_id='alice',device_id='my-s21',display_name='My phone'))
    own=reg.handle(dict(action='list',user_id='alice'))['connections']
    assert len(own)==1 and own[0]['id']==one['id'] and 'sip_password' not in own[0]
    with pytest.raises(ValueError):reg.handle(dict(action='settings',user_id='bob',account_id=one['id']))
    with pytest.raises(ValueError):reg.handle(dict(action='delete',user_id='bob',account_id=one['id']))
    assert 'gateway-s21' not in include.read_text() and 'context=userio-sim-incoming' in include.read_text()
    assert stat.S_IMODE(reg.path.stat().st_mode)==0o600
    reg.handle(dict(action='delete',user_id='alice',account_id=one['id']))
    assert one['sip_username'] not in include.read_text() and other['sip_username'] in include.read_text()
    assert not any('originate' in ' '.join(x) for x in calls)

def test_validation_and_reload_registration_status(tmp_path):
    reg,_,_=registry(tmp_path)
    for device in ['','a','new\nsecret=bad','x;include=evil']:
        with pytest.raises(ValueError):reg.handle(dict(action='create',user_id='alice',device_id=device,display_name='My phone'))
    item=reg.handle(dict(action='create',user_id='alice',device_id='phone-1',display_name='My phone'))
    reg.runner=lambda *a,**k: SimpleNamespace(stdout='Status       : OK (32 ms)')
    out=reg.handle(dict(action='settings',user_id='alice',account_id=item['id']))
    assert out['status']=='registered' and not out['call_ready']
