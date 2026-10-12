"""Fast unit: bounded owned sticker thumbnail, expected2s/max15s."""
import io
import base64
import pytest
from universal_userio.adapters import MaxChannelAdapter
from universal_userio.channels.core import AdapterNotSupported
from universal_userio.contracts import InboxMessage
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


def setup(tmp_path):
    store=SQLiteUserIOStore(tmp_path/'max.sqlite3')
    service=UserIOService(store,object(),object())
    cid,_=service.receive(InboxMessage('max','m','peer','',1,attachments=(
        {'kind':'sticker','src':'https://i.oneme.ru/getSmile?smileId=1&smileType=1'},)),route_id='test')
    return store,service,MaxChannelAdapter(store,service,store.default_user_id)


def test_owned_public_thumbnail_is_bounded_and_identifies_real_bytes(tmp_path,monkeypatch):
    store,service,adapter=setup(tmp_path)
    calls=[]
    class Response(io.BytesIO):
        def __enter__(self):return self
        def __exit__(self,*args):self.close()
        def read(self,n=-1):
            assert n==1025
            return super().read(n)
    def open_url(url,timeout):
        calls.append((url,timeout))
        return Response(base64.b64decode('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'))
    monkeypatch.setattr('universal_userio.adapters.urllib.request.urlopen',open_url)
    result=adapter.download_for_ai(file_ref='m',max_bytes=1024)
    assert result.content_type=='image/png' and result.data.startswith(b'\x89PNG')
    assert len(calls)==1 and calls[0][1]==10
    other,_=store.create_user('other','long-password')
    with pytest.raises(KeyError):
        MaxChannelAdapter(store,service,other.user_id).download_for_ai(file_ref='m',max_bytes=1024)
    assert len(calls)==1


def test_animated_sticker_uses_first_frame(tmp_path,monkeypatch):
    from PIL import Image
    _,_,adapter=setup(tmp_path)
    encoded=io.BytesIO()
    Image.new('RGB',(2,2),'red').save(encoded,format='GIF',save_all=True,
        append_images=[Image.new('RGB',(2,2),'blue')],duration=100,loop=0)
    class Response(io.BytesIO):
        def __enter__(self):return self
        def __exit__(self,*args):self.close()
    monkeypatch.setattr('universal_userio.adapters.urllib.request.urlopen',
        lambda *a,**k:Response(encoded.getvalue()))
    result=adapter.download_for_ai(file_ref='m',max_bytes=2048)
    with Image.open(io.BytesIO(result.data)) as frame:
        assert frame.format=='PNG' and frame.n_frames==1
        assert frame.convert('RGB').getpixel((0,0))==(255,0,0)


def test_thumbnail_refuses_oversize_before_pixels_reach_model(tmp_path,monkeypatch):
    _,_,adapter=setup(tmp_path)
    class Response(io.BytesIO):
        def __enter__(self):return self
        def __exit__(self,*args):self.close()
    monkeypatch.setattr('universal_userio.adapters.urllib.request.urlopen',
        lambda *a,**k:Response(b'GIF89a'+b'x'*1024))
    with pytest.raises(AdapterNotSupported,match='budget'):
        adapter.download_for_ai(file_ref='m',max_bytes=1024)
