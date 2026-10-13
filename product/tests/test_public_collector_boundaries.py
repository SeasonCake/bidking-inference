"""Offline checks for the public self-hosted collector; no production requests."""
from __future__ import annotations
import io
import sys
from email.message import Message
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'scripts')]
import sample_collector_server as collector

@pytest.mark.parametrize('value', ['-1', '-99', 'invalid', '1.5', '+4', '4,4', '\uff14'])
@pytest.mark.parametrize('route', ['/ingest', '/private', '/my', '/activate', '/telemetry', 'e2ee'])
def test_invalid_length_is_rejected_before_read(monkeypatch, route, value):
    handler, calls, responses = make_handler(monkeypatch, route, value)
    handler.do_POST()
    assert calls == []
    assert responses[0] == 400

@pytest.mark.parametrize('route', ['/ingest', '/private', '/my', '/activate', '/telemetry', 'e2ee'])
def test_large_body_is_rejected_before_read(monkeypatch, route):
    handler, calls, responses = make_handler(monkeypatch, route, '999999999')
    handler.do_POST()
    assert calls == []
    assert responses[0] == (400 if route == 'e2ee' else 413)

@pytest.mark.parametrize('route', ['/ingest', '/private', '/my', '/activate', '/telemetry', 'e2ee'])
def test_small_body_reads_exact_declared_count(monkeypatch, route):
    handler, calls, responses = make_handler(monkeypatch, route, '4')
    handler.do_POST()
    assert calls == [4]
    assert responses

@pytest.mark.parametrize('route', ['/ingest', '/private', '/my', '/activate', '/telemetry', 'e2ee'])
def test_zero_keeps_route_semantics_without_read(monkeypatch, route):
    handler, calls, responses = make_handler(monkeypatch, route, '0')
    handler.do_POST()
    assert calls == []
    assert responses

def make_handler(monkeypatch, route, value):
    calls, responses = [], []
    class Input(io.BytesIO):
        def read(self, n=-1):
            calls.append(n)
            if n < 0: raise AssertionError('unbounded read')
            return super().read(n)
    class Envelope:
        def open_request(self, raw):
            raise collector.activation_http_e2ee.ActivationHttpE2EEError('synthetic invalid envelope')
    h = object.__new__(collector.Handler)
    h.path = collector.activation_http_e2ee.ROUTE if route == 'e2ee' else route
    h.headers = Message(); h.headers['Content-Length'] = value
    h.rfile = Input(b'abcd' * 4)
    h._client_ip = lambda: '127.0.0.1'
    h._send = lambda code, *_a, **_k: responses.append(code)
    h._send_html = lambda *_a, **_k: responses.append(200)
    h._send_upload_response = lambda code, *_a, **_k: responses.append(code)
    h._send_activation_response = lambda code, *_a, **_k: responses.append(code)
    monkeypatch.setattr(collector, '_rate_ok', lambda _ip: True)
    monkeypatch.setattr(collector, '_auth_ok', lambda *_a: False)
    monkeypatch.setattr(collector, '_ACT', object())
    monkeypatch.setattr(collector, '_ACTIVATION_HTTP_E2EE', Envelope())
    monkeypatch.setattr(collector, '_MAX_BODY', 8)
    monkeypatch.setattr(collector, '_dashboard_uid_hash_from_token', lambda *_a, **_k: (None, 'synthetic', None))
    monkeypatch.setattr(collector, '_my_uid_hash_from_token', lambda *_a, **_k: (None, 'synthetic', None))
    monkeypatch.setattr(collector, '_handle_activation_body', lambda *_a: (400, {'error': 'synthetic'}))
    if route == '/telemetry' and value == '0':
        class Authority:
            def handle_telemetry(self,*_a):return (400,{'error':'synthetic'})
        monkeypatch.setattr(collector,'_ACT',Authority())
    return h, calls, responses

def test_duplicate_length_rejected_and_missing_means_zero():
    headers=Message(); assert collector._request_content_length(headers)==0
    headers['Content-Length']='4';headers['Content-Length']='4'
    with pytest.raises(ValueError):collector._request_content_length(headers)

@pytest.mark.parametrize('host', ['0.0.0.0', '::', '192.0.2.5', 'example.invalid', ''])
@pytest.mark.parametrize('key', ['', ' ', '\t'])
def test_remote_empty_key_exits_before_runtime_initialization(monkeypatch, host, key):
    monkeypatch.setattr(collector, 'SafeThreadingHTTPServer', lambda *_a, **_k: pytest.fail('must not bind'))
    with pytest.raises(SystemExit) as exc:
        collector.main(['collector','--host',host,'--key',key])
    assert exc.value.code == 2

@pytest.mark.parametrize('host,key', [('127.0.0.1',''), ('127.0.0.2',''), ('::1',''), ('localhost',''), ('0.0.0.0','synthetic-ingest-key')])
def test_selected_bindings_keep_auth_separate(monkeypatch,tmp_path,host,key):
    monkeypatch.chdir(tmp_path)
    bound=[]
    class Server:
        def __init__(self,address,_handler):bound.append(address)
        def serve_forever(self):pass
    monkeypatch.setattr(collector,'SafeThreadingHTTPServer',Server)
    monkeypatch.setattr(collector,'_start_history_index_load',lambda:None)
    monkeypatch.setattr(collector,'_version_manifest',lambda:{})
    monkeypatch.setattr(collector,'_ACT',None)
    monkeypatch.setattr(collector,'_DASH_PASS','')
    monkeypatch.setattr(collector,'_EXPORT_KEY','')
    assert collector.main(['collector','--host',host,'--key',key]) == 0
    assert bound==[(host,18900)]
    assert collector._DASH_ALLOW_ANON is (not key)
    assert collector._EXPORT_KEY==''
    if key:
        assert not collector._auth_ok({},b'{}')
        assert not collector._auth_ok({'X-Api-Key':'wrong'},b'{}')
        assert collector._auth_ok({'X-Api-Key':key},b'{}')
    else: assert collector._auth_ok({},b'{}')
