"""ACT-A01 fixed local recovery guidance must preserve transport evidence."""
from __future__ import annotations

import json

import pytest

from scripts import activation_transport_diagnostic as diag


@pytest.fixture
def report(monkeypatch):
    monkeypatch.setattr(diag, 'BASES', (('endpoint_1', 'https://example.invalid'),))
    monkeypatch.setattr(diag, 'ENDPOINTS', (
        ('endpoint_1', 'health', 'https://example.invalid/healthz'),
        ('endpoint_1', 'activation_readiness', 'https://example.invalid/readiness'),
    ))
    monkeypatch.setattr(diag, 'inspect_system_dns', lambda *_: {})
    monkeypatch.setattr(diag, 'inspect_trusted_dns', lambda *_: {})
    monkeypatch.setattr(diag, '_emit_progress', lambda *_: None)
    monkeypatch.setattr(diag, '_curl_version', lambda *_: 'fixture')
    for name in ('inspect_windows_network_state', 'inspect_windows_proxy_state',
                 'inspect_activation_recheck_history'):
        monkeypatch.setattr(diag, name, lambda: {})

    def build(cache_state='malformed', python_ready=True, curl_ready=True):
        monkeypatch.setattr(diag, 'inspect_local_activation', lambda: {
            'cache_state': cache_state, 'credential_available': True,
        })

        def probe(name, endpoint, _url, *args):
            ready = curl_ready if args else python_ready
            return dict(name=name, endpoint=endpoint,
                        transport='windows_curl' if args else 'packaged_python',
                        ok=ready, stage='http' if ready else 'tls',
                        failure_class='http_response' if ready else 'tls_handshake_failed')

        monkeypatch.setattr(diag, 'check_python', probe)
        monkeypatch.setattr(diag, 'check_curl', probe)
        return diag.build_report()

    return build


def test_ready_malformed_has_local_recovery_without_offline_reclassification(report):
    result = report()
    assert result['classification'] == 'PACKAGED_PYTHON_ACTIVATION_READY'
    assert result['activation_availability'] == 'online_recheck_available'
    guidance = result['support_guidance']
    assert guidance['code'] == 'local_activation_cache_malformed'
    assert '重启' in guidance['message']
    assert '诊断' in guidance['message']
    assert '反复' in guidance['message']


@pytest.mark.parametrize('state', ['missing', 'absent', 'valid', 'healthy', 'other_identity', 'expired'])
def test_nonmalformed_guidance_keeps_default(report, state):
    result = report(cache_state=state)
    assert result['support_guidance'] == {'code': 'no_fixed_action', 'message': ''}


def test_legacy_guidance_call_keeps_contract():
    assert diag.activation_support_guidance('PACKAGED_PYTHON_ACTIVATION_READY') == {
        'code': 'no_fixed_action', 'message': '',
    }


def test_malformed_and_all_network_failure_preserve_both_actions(report):
    result = report(python_ready=False, curl_ready=False)
    assert result['classification'] == 'ALL_TESTED_HTTPS_PATHS_FAILED'
    assert result['transport_failure_classes'] == ['tls_handshake_failed']
    assert result['support_guidance']['code'] == 'local_activation_cache_malformed'
    message = result['support_guidance']['message']
    assert '重启' in message and '网络诊断' in message
    assert '排查连接' in message


def test_malformed_and_tls_path_problem_preserve_existing_tls_action(report):
    result = report(python_ready=False, curl_ready=True)
    assert result['classification'] == 'PACKAGED_PYTHON_FAIL_WINDOWS_CURL_ACTIVATION_READY'
    message = result['support_guidance']['message']
    assert '重启' in message
    assert '系统代理/TLS路径不兼容' in message
    assert '可关闭非必要代理' in message and '客户端直连重试' in message


def test_malformed_guidance_never_reflects_extra_private_fields():
    canary = 'SYNTHETIC-PRIVATE-TOKEN-EXCEPTION'
    result = diag.activation_support_guidance(
        canary, {'cache_state': 'malformed', 'token': canary, 'exception': canary})
    assert canary not in json.dumps(result)
    assert result['code'] == 'local_activation_cache_malformed'


def test_malformed_input_protocols_are_not_invoked():
    class MagicDict(dict):
        def get(self, *_):
            raise AssertionError('dict subclass must not run')

    class MagicString(str):
        def __eq__(self, _):
            raise AssertionError('str subclass must not run')

    for value in (None, MagicDict(cache_state='malformed'),
                  {'cache_state': MagicString('malformed')}):
        assert diag.activation_support_guidance('PACKAGED_PYTHON_ACTIVATION_READY', value) == {
            'code': 'no_fixed_action', 'message': '',
        }
