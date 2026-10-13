"""Fast unit: original MAX binary source identity; expected 1s/max30s."""
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from universal_userio.matrix_max_media import download

class Tests(unittest.TestCase):
    def setUp(self):
        self.calls=[]
        def get_message(**kwargs):
            self.calls.append(kwargs)
            return {"id":"99","attaches":[{"_type":"PHOTO","baseUrl":"https://i.oneme.ru/photo"}]}
        self.client=SimpleNamespace(get_message=get_message)
        self.service=SimpleNamespace(max_client_for=lambda c,u:self.client)
        self.conv={"peer_id":"507901398","account_ref":"max"}
        self.msg={"message_id":"max-507901398-99"}
    def test_image_bytes_come_from_native_exact_message(self):
        with patch("universal_userio.matrix_max_media.fetch_native_url",return_value=(b"image","image/jpeg")) as fetch:
            result=download(self.service,"owner",self.conv,self.msg)
        self.assertEqual(self.calls,[{"chat_id":"507901398","message_id":"99"}])
        fetch.assert_called_once_with("https://i.oneme.ru/photo",maximum=2*1024*1024)
        self.assertEqual((result.data,result.content_type),(b"image","image/jpeg"))
    def test_different_peer_message_is_refused_before_provider_call(self):
        with self.assertRaises(ValueError):
            download(self.service,"owner",self.conv,{"message_id":"max-other-99"})
        self.assertFalse(self.calls)
    def test_different_native_message_identity_is_refused(self):
        self.client.get_message=lambda **kw:{"id":"100","attaches":[{"_type":"PHOTO"}]}
        with self.assertRaises(ValueError):
            download(self.service,"owner",self.conv,self.msg)
    def test_file_uses_native_download_metadata(self):
        attachment={"_type":"FILE","name":"original.txt","fileId":"actual"}
        self.client.get_message=lambda **kw:{"id":"99","attaches":[attachment]}
        self.client.media_reference=lambda **kw:{"url":"https://file.mycdn.me/native"}
        with patch("universal_userio.matrix_max_media.fetch_native_url",return_value=(b"original","text/plain")):
            result=download(self.service,"owner",self.conv,self.msg)
        self.assertEqual((result.filename,result.data),("original.txt",b"original"))

if __name__=="__main__":unittest.main()
