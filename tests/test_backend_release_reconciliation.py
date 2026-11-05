"""Fail-closed recovery of an interrupted backend release, without provider sends."""
from copy import deepcopy
import json
import pytest

from scripts.container_release import (
    _backend_resume_snapshot_proof, _backend_replacement_config,
)


def fixture():
    revision='a'*40
    old={'Id':'old-container','Image':'sha256:old',
         'Config':{'Image':'backend:old','Labels':{'org.opencontainers.image.revision':'old'}},
         'HostConfig':{'NetworkMode':'host','OomKillDisable':None},
         'Mounts':[{'Destination':'/data','Name':'data-volume'}]}
    new_config=_backend_replacement_config(old['Config'],image='universal-userio-backend:sha-next',
                                            revision=revision)
    current={'Id':'new-container','Image':'sha256:new','Config':new_config,
             'HostConfig':{'NetworkMode':'host','OomKillDisable':False},
             'Mounts':deepcopy(old['Mounts']),'State':{'Running':True}}
    others={'userio-voice':{'Id':'voice','Image':'sha256:voice',
                            'Config':{'Name':'voice'},'HostConfig':{'NetworkMode':'host'}},
            'userio-frontend':{'Id':'frontend','Image':'sha256:front',
                              'Config':{'Name':'frontend'},'HostConfig':{'NetworkMode':'host'}}}
    original=json.dumps({'services':{'backend':{'image':'backend:old'}}},indent=2).encode()
    return {'baseline':{'backend':old,'others':others,'account_ids':['acct-1'],
                        'image_id':'sha256:new'},
            'intent':{'revision':revision,'image_id':'sha256:new','expected_container':'old-container'},
            'candidate':{'Id':'new-container','Image':'sha256:new','Config':new_config},
            'current':current,'revision':revision,'expected_container':'new-container',
            'current_accounts':['acct-1'],'current_others':deepcopy(others),
            'original_compose':original,'current_compose':original}


def test_interrupted_release_requires_exact_candidate_and_preserves_source_bytes():
    x=fixture()
    baseline=deepcopy(x)
    receipt=_backend_resume_snapshot_proof(**x)
    assert receipt['compose_update_needed'] is True
    assert receipt['image']=='universal-userio-backend:sha-next'
    assert json.loads(receipt['installed_compose'])['services']['backend']['image']==receipt['image']
    assert x==baseline


def test_recovery_is_idempotent_after_config_cas_but_before_receipt():
    x=fixture()
    out=_backend_resume_snapshot_proof(**x)
    x['current_compose']=out['installed_compose']
    assert _backend_resume_snapshot_proof(**x)['compose_update_needed'] is False


@pytest.mark.parametrize('field,change',[
    ('intent',lambda x:x['intent'].update(revision='other')),
    ('intent',lambda x:x['intent'].update(expected_container='wrong')),
    ('intent',lambda x:x['intent'].update(image_id='sha256:foreign')),
    ('candidate',lambda x:x['candidate'].update(Id='foreign')),
    ('current',lambda x:x['current'].update(Id='foreign')),
    ('current',lambda x:x['current']['State'].update(Running=False)),
    ('current',lambda x:x['current'].update(Image='sha256:foreign')),
    ('current',lambda x:x['current']['HostConfig'].update(NetworkMode='bridge')),
    ('current',lambda x:x['current'].update(Mounts=[])),
    ('current',lambda x:x['current']['Config'].update(Env=['unexpected'])),
    ('current_accounts',lambda x:x['current_accounts'].append('foreign-account')),
    ('current_others',lambda x:x['current_others']['userio-voice'].update(Image='sha256:changed')),
    ('current_compose',lambda x:x.update(current_compose=b'{}')),
])
def test_any_identity_or_config_change_blocks_recovery(field,change):
    x=fixture()
    change(x)
    with pytest.raises(RuntimeError):
        _backend_resume_snapshot_proof(**x)
