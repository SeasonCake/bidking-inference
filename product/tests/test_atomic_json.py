"""Shared live JSON writer contract, exercised through all three real consumers."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bidking_lab.live import monitor
from bidking_lab.live import live_refresh_signatures
from test_fatbeans_webhook_monitor import _module as webhook_module


@pytest.fixture(scope='module', params=('monitor', 'bid_refresh', 'webhook'))
def writer_module(request):
    return {'monitor': lambda: monitor, 'bid_refresh': lambda: live_refresh_signatures,
            'webhook': webhook_module}[request.param]()


def test_atomic_json_preserves_pretty_utf8_lf_bytes_and_overwrites(writer_module, tmp_path):
    target = tmp_path/'nested'/'state.json'
    writer_module._atomic_write_json(target, {'中文': '值', 'list': [1, None]})
    assert target.read_bytes() == '{\n  "中文": "值",\n  "list": [\n    1,\n    null\n  ]\n}\n'.encode('utf-8')
    writer_module._atomic_write_json(target, {'replacement': True})
    assert target.read_bytes() == b'{\n  "replacement": true\n}\n'
    assert list(target.parent.iterdir()) == [target]


def test_atomic_json_retries_only_permission_error_before_replacing(writer_module, tmp_path, monkeypatch):
    target = tmp_path/'state.json'
    target.write_bytes(b'previous')
    replace = Path.replace
    attempts, delays = [], []

    def transient(source, destination):
        assert target.read_bytes() == b'previous'
        attempts.append(source)
        if len(attempts) < 3:
            raise PermissionError('synthetic transient reader lock')
        return replace(source, destination)

    monkeypatch.setattr(Path, 'replace', transient)
    monkeypatch.setattr(writer_module.time, 'sleep', delays.append)
    writer_module._atomic_write_json(target, {'ok': 1})
    assert len(attempts) == 3 and delays == [0.05, 0.1]
    assert json.loads(target.read_text('utf-8')) == {'ok': 1}
    assert list(tmp_path.iterdir()) == [target]


def test_atomic_json_exhausted_retry_keeps_old_target_and_cleans_temp(writer_module, tmp_path, monkeypatch):
    target = tmp_path/'state.json'
    target.write_bytes(b'previous')
    attempts, delays = [], []

    def locked(source, destination):
        attempts.append(source)
        raise PermissionError('synthetic persistent reader lock')

    monkeypatch.setattr(Path, 'replace', locked)
    monkeypatch.setattr(writer_module.time, 'sleep', delays.append)
    with pytest.raises(PermissionError, match='persistent reader lock'):
        writer_module._atomic_write_json(target, {'ok': 1})
    assert len(attempts) == 5
    assert delays == pytest.approx([0.05, 0.1, 0.15, 0.2])
    assert target.read_bytes() == b'previous'
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize('kind', ('object', 'cycle'))
def test_atomic_json_encoding_failure_cleans_only_its_temp(writer_module, tmp_path, kind):
    target = tmp_path/'state.json'
    sibling = tmp_path/'keep.txt'
    target.write_bytes(b'previous')
    sibling.write_bytes(b'keep')
    payload = {'bad': object()} if kind == 'object' else {}
    if kind == 'cycle':
        payload['self'] = payload
    error = TypeError if kind == 'object' else ValueError
    with pytest.raises(error):
        writer_module._atomic_write_json(target, payload)
    assert target.read_bytes() == b'previous' and sibling.read_bytes() == b'keep'
    assert set(tmp_path.iterdir()) == {target, sibling}


def test_atomic_json_non_permission_replace_error_is_not_retried(writer_module, tmp_path, monkeypatch):
    target = tmp_path/'state.json'
    target.write_bytes(b'previous')
    attempts, delays = [], []

    def fail(source, destination):
        attempts.append(source)
        raise OSError('synthetic replace failure')

    monkeypatch.setattr(Path, 'replace', fail)
    monkeypatch.setattr(writer_module.time, 'sleep', delays.append)
    with pytest.raises(OSError, match='replace failure'):
        writer_module._atomic_write_json(target, {'ok': 1})
    assert len(attempts) == 1 and delays == []
    assert target.read_bytes() == b'previous'
    assert list(tmp_path.iterdir()) == [target]
