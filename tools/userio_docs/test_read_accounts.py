"""Local-only tests for the read-only API example; never use real credentials."""
import importlib.util
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('read_accounts',ROOT/'examples/userio/read_accounts.py')
api=importlib.util.module_from_spec(spec);spec.loader.exec_module(api)

class Tests(unittest.TestCase):
    def test_url_validation(self):
        for good in ['https://msg.bezrabotnyi.com','http://127.0.0.1:18093','http://localhost:18093/','http://[::1]:18093']:
            self.assertEqual(api.validate_base_url(good),good.rstrip('/'))
        for bad in ['http://example.com','https://name:secret@example.com','https://example.com/mcp','https://example.com/?token=test','file:///tmp/a','https://example.com/#x']:
            with self.assertRaises(ValueError):api.validate_base_url(bad)

    def test_http_read_and_redirect_refusal(self):
        events=[]
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                events.append(self.path)
                if self.path=='/redirect':
                    self.send_response(302);self.send_header('Location','/must-not-follow');self.end_headers();return
                assert self.headers.get('Authorization')=='Bearer TEST-ONLY'
                body=json.dumps({'accounts':[{'id':'example','provider':'telegram'}]}).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            base=f'http://127.0.0.1:{server.server_port}'
            self.assertEqual(api.request_json(base,'/v1/accounts',token='TEST-ONLY')['accounts'][0]['id'],'example')
            with self.assertRaises(HTTPError) as raised:
                api.request_json(base,'/redirect',token='TEST-ONLY')
            self.assertEqual(raised.exception.code,302)
            self.assertEqual(events,['/v1/accounts','/redirect'])
        finally:server.shutdown();server.server_close();thread.join(timeout=2)

if __name__=='__main__':unittest.main()
