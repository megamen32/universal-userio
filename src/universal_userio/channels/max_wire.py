"""Current official MAX Web binary frames (web26.10.3 ere/$ne).

Web auth uses a10-byte header, MessagePack payload and optional raw LZ4 block.
The established ws-api JSON transport is independent of this codec.
"""
import struct

MAX_PAYLOAD=4*1024*1024


def encode(frame):
    import msgpack
    payload=frame.get('payload')
    raw=msgpack.packb(payload,use_bin_type=True) if payload is not None else b''
    if len(raw)>MAX_PAYLOAD:raise ValueError('MAX web frame exceeds payload budget')
    seq,opcode,cmd=frame['seq'],frame['opcode'],frame['cmd']
    if any(type(x) is not int for x in (seq,opcode,cmd)) or not -32768<=seq<=32767 or not -32768<=opcode<=32767 or not 0<=cmd<=255:
        raise ValueError('MAX web frame header invalid')
    return struct.pack('>BBhhB',10,cmd,seq,opcode,0)+len(raw).to_bytes(3,'big')+raw


def decode(raw):
    import msgpack
    if not isinstance(raw,bytes) or len(raw)<10:raise ValueError('MAX binary web frame required')
    version,cmd,seq,opcode,factor=struct.unpack('>BBhhB',raw[:7]);size=int.from_bytes(raw[7:10],'big')
    if version!=10 or size!=len(raw)-10 or size>MAX_PAYLOAD or size*max(factor,1)>MAX_PAYLOAD:
        raise ValueError('MAX web frame length/version invalid')
    data=raw[10:]
    if factor:
        import lz4.block
        data=lz4.block.decompress(data,uncompressed_size=size*factor)
    def extension(code,value):
        if code!=1 or len(value)>16:raise ValueError('MAX web numeric extension unsupported')
        number=msgpack.unpackb(value,raw=False)
        if type(number) is not int:raise ValueError('MAX web numeric extension invalid')
        return number
    payload=msgpack.unpackb(data,raw=False,strict_map_key=False,ext_hook=extension,
        max_str_len=MAX_PAYLOAD,max_bin_len=MAX_PAYLOAD,max_ext_len=16,
        max_array_len=100000,max_map_len=100000) if data else {}
    return {'ver':version,'cmd':cmd,'seq':seq,'opcode':opcode,'payload':payload}
