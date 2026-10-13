"""Fast unit: official MAX Web wire, expected1/max5s; native JSON unchanged."""
import struct,json
import msgpack,lz4.block
import pytest
from universal_userio.channels.max_wire import encode,decode,MAX_PAYLOAD
from universal_userio.channels.max_client import MaxTransport

def test_official_header_compressed_response_and_native_bigint():
    payload={'profile':{'contact':{'id':msgpack.ExtType(1,msgpack.packb(9007199254740993123))}},'padding':'hello '*100}
    raw=msgpack.packb(payload,use_bin_type=True);packed=lz4.block.compress(raw,store_size=False)
    factor=(len(raw)+len(packed)-1)//len(packed)
    frame=struct.pack('>BBhhB',10,1,7,291,factor)+len(packed).to_bytes(3,'big')+packed
    result=decode(frame);assert result['payload']['profile']['contact']['id']==9007199254740993123
    assert result['seq']==7 and result['opcode']==291
    frame={'cmd':0,'seq':1,'opcode':288,'payload':{}}
    data=encode(frame);assert data[:10]==bytes([10,0,0,1,1,32,0,0,0,1]);assert decode(data)['payload']=={}

def test_web_codec_budget_and_native_transport_remain_distinct():
    class WS:
        def __init__(self):self.sent=[];self.data='';self.connected=True
        def send(self,data,**kwargs):self.sent.append((data,kwargs));self.data=data
        def settimeout(self,t):pass
        def recv(self):return self.data
    ws=WS();frame={'cmd':0,'seq':1,'opcode':6,'payload':{'deviceId':'new'}};text=json.dumps(frame)
    native=MaxTransport();native.send(ws,text);assert ws.sent==[(text,{})];assert native.recv(ws,1)==frame
    web=MaxTransport(web_auth=True);web.send(ws,text);assert isinstance(ws.sent[-1][0],bytes)
    assert web.recv(ws,1)['payload']==frame['payload']
    with pytest.raises(ValueError):decode(b'bad')
    with pytest.raises(ValueError):decode(bytes([10,1,0,0,0,6,255])+bytes([64,0,0]))
    with pytest.raises(ValueError):encode({**frame,'payload':'x'*(MAX_PAYLOAD+1)})
