"""Conservative read-only provider reconciliation regression tests."""
import hashlib, io, json, uuid
import pytest
from universal_userio.provider_readback import (stable_vk_random_id,
 stable_yandex_payload_id, vk_random_id_readback, yandex_payload_readback,
 matrix_transaction_readback)

class VK:
    def __init__(self, rows):self.rows=rows;self.calls=0
    def history(self, peer, *, count, offset):
        self.calls+=1
        return self.rows[offset:offset+count]

def test_stable_provider_identities():
    key=['u','vk:123','456','d']
    original=(int.from_bytes(hashlib.sha256((chr(0).join(key)).encode()).digest()[:4],'big') & 0x7fffffff) or 1
    assert stable_vk_random_id(*key)==original
    assert stable_yandex_payload_id('u','d')==str(uuid.uuid5(uuid.NAMESPACE_URL,('u'+chr(0)+'d')))

@pytest.mark.parametrize('change',[
 {'peer_id':9},{'from_id':999},{'random_id':8},{'text':'other'},
 {'out':0},{'attachments':[1]},{'reply_message':{'id':5}}
])
def test_vk_exact_identity_and_plain_payload(change):
    row={'peer_id':456,'from_id':123,'random_id':7,'text':'hello','id':15,'out':1}
    assert vk_random_id_readback(VK([row]),own_id=123,peer_id=456,random_id=7,body='hello')=='15'
    assert vk_random_id_readback(VK([{**row,**change}]),own_id=123,
        peer_id=456,random_id=7,body='hello') is None

def test_vk_duplicate_is_unknown():
    row={'peer_id':456,'from_id':123,'random_id':7,'text':'hello','id':15,'out':1}
    assert vk_random_id_readback(VK([row,{**row,'id':16}]),own_id=123,
        peer_id=456,random_id=7,body='hello') is None

def yandex_row(*, peer='c',own='g',nonce='n',body='hello',edit=0):
    return {'ChatId':peer,'Messages':[{'ServerMessage':{
     'ServerMessageInfo':{'Timestamp':123,'From':{'Guid':own},'LastEditTimestamp':edit},
     'ClientMessage':{'Plain':{'PayloadId':nonce,'Text':{'MessageText':body}}}}}]}

@pytest.mark.parametrize('bad',[
 {'peer':'other'},{'own':'other'},{'nonce':'other'},{'body':'other'},{'edit':2}
])
def test_yandex_exact_original_payload(bad):
    reader=lambda *args,**kwargs:{'chats':[yandex_row(**bad)]}
    assert yandex_payload_readback(reader,{'guid':'g'},peer_id='c',
                                   payload_id='n',body='hello') is None
    reader=lambda *args,**kwargs:{'chats':[yandex_row()]}
    assert yandex_payload_readback(reader,{'guid':'g'},peer_id='c',
                                   payload_id='n',body='hello')=='123'

def test_empty_history_never_proves_failure():
    assert yandex_payload_readback(lambda *args,**kw:{'chats':[]},
             {'guid':'g'},peer_id='c',payload_id='n',body='hello') is None

def test_matrix_strict_transaction_and_get_only():
    seen=[]
    def run(req,timeout):
        seen.append(req.get_method())
        if req.full_url.endswith('account/whoami'):v={'user_id':'@a:host'}
        elif '/sync?' in req.full_url:v={'next_batch':'cursor'}
        else:v={'chunk':[{'type':'m.room.message','sender':'@a:host',
         'unsigned':{'transaction_id':'txn'},'content':{'msgtype':'m.text','body':'hello'},
         'event_id':'$e1'}]}
        return io.BytesIO(json.dumps(v).encode())
    assert matrix_transaction_readback('https://host','token',room_id='!room:host',
        transaction_id='txn',body='hello',runner=run)=='matrix:$e1'
    assert matrix_transaction_readback('https://host','token',room_id='!room:host',
        transaction_id='wrong',body='hello',runner=run) is None
    assert set(seen)=={'GET'}

def test_matrix_refuses_insecure_bearer_transport():
    with pytest.raises(ValueError):
        matrix_transaction_readback('http://remote.example','token',
           room_id='!room:host',transaction_id='t',body='hello')


def test_vk_identity_mismatch_refuses_original_receipt(monkeypatch):
    import universal_userio.account_connections as accounts
    from universal_userio.provider_readback import original_provider_receipt
    class Client:
        def identity(self): return 999
        def history(self, *args, **kwargs):
            raise AssertionError('must not read another account history')
    monkeypatch.setattr(accounts, '_vk_personal_client',
                        lambda *args, **kwargs: (Client(), 123))
    draft=type('Draft',(),{'id':'draft','body':'hello'})()
    conversation={'source':'vk','account_ref':'vk:123','peer_id':'456'}
    with pytest.raises(ValueError,match='identity mismatch'):
        original_provider_receipt(object(),draft,conversation,user_id='owner')
