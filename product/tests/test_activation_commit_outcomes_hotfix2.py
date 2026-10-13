"""Local commit outcome semantics through real response parsing and consumers."""
from dataclasses import replace
import json
import threading
import os
import subprocess
import sys

import pytest

from test_activation_candidate_atomic_commit_0_3_1 import (
    isolated_activation_runtime, _success_response, _TOKEN_A, _TOKEN_B,
    _DEVICE, _KEY_A,
)
from bidking_lab.secure import activation
from bidking_lab.secure import client_activate as ca
from bidking_lab.secure import enforce
from test_activation_clear_preflight_hotfix2 import dialog, calls, _run_ui_job
from test_activation_clear_runtime_deny_0_3_1 import isolated_clear_runtime


def _raise(*args, **kwargs):
    raise OSError('synthetic private path/token exception must never reach output')


def _candidate(monkeypatch, *, rotating=False):
    monkeypatch.setattr(ca, '_post_activate',
                        lambda _u, _d, token, _t: _success_response(token))
    candidate = ca.activate(force_server=True, defer_commit=True,
                            ui_token=_TOKEN_B if rotating else None)
    assert isinstance(candidate, ca.ActivationCandidate)
    assert candidate.result.activated is True
    assert candidate.commit_plan.kind == 'grant'
    return candidate


def _assert_local(result, stage, recovery, reason='local_commit_failed'):
    assert result.activated is False
    assert result.source == 'client'
    assert result.reason == reason
    assert result.local_commit_stage == stage
    assert result.local_commit_recovery == recovery
    state = enforce.activation_state(result)
    assert state['run_full'] is False
    assert state['needs_token'] is False
    assert state['local_commit_stage'] == stage
    assert state['local_commit_recovery'] == recovery
    assert state['activation_availability'] == reason
    assert not enforce._is_graceable_heartbeat_failure(result)
    for text in (result.status_line(), state['text']):
        assert '网络' not in text and 'token' not in text and '写入权限' not in text
        assert 'synthetic' not in text
        assert ('验证通过' in text) == (reason == 'local_commit_failed')


@pytest.mark.parametrize('rotating', [False, True])
@pytest.mark.parametrize('stage,seam', [
    ('snapshot', '_snapshot_activation_persistence'),
    ('cache', '_save_cache'), ('provider', '_install_provider'),
    ('deny_marker', '_clear_persistent_authoritative_deny'),
    ('upload_lease', '_install_process_upload_lease'),
])
def test_validated_commit_stage(monkeypatch, isolated_activation_runtime, rotating, stage, seam):
    candidate = _candidate(monkeypatch, rotating=rotating)
    monkeypatch.setattr(ca, seam, _raise)
    outcome = ca.commit_activation_candidate(candidate)
    assert outcome.committed is False
    _assert_local(outcome.result, stage, 'not_started' if stage == 'snapshot' else 'complete')
    assert activation.is_activated() is False


def test_refresh_local_failure_preserves_transport_and_denies_grace(monkeypatch, isolated_activation_runtime):
    _candidate(monkeypatch)
    initial = enforce.startup_activate(force_server=True)
    assert initial.activated
    monkeypatch.setattr(ca, '_save_cache', _raise)
    failed = enforce.refresh_activation()
    _assert_local(failed, 'cache', 'complete')
    assert enforce.current() is failed
    assert not enforce.should_run_full()
    assert not activation.is_activated()
    assert enforce.activation_state()['run_full'] is False
    assert failed.transport_summary.attempt_count == 1


@pytest.mark.parametrize('stage,seam', [('token', '_commit_ui_token'),
                                       ('shadow_cleanup', '_cleanup_shadow_tokens')])
def test_rotating_only_stages(monkeypatch, isolated_activation_runtime, stage, seam):
    candidate = _candidate(monkeypatch, rotating=True)
    monkeypatch.setattr(ca, seam, _raise)
    outcome = ca.commit_activation_candidate(candidate)
    _assert_local(outcome.result, stage, 'complete')
    assert isolated_activation_runtime.read_text().strip() == _TOKEN_A
    assert not activation.is_activated()


@pytest.mark.parametrize('returns_none', [False, True])
def test_snapshot_has_no_transaction_writes(monkeypatch, isolated_activation_runtime, returns_none):
    candidate = _candidate(monkeypatch)
    writes = []
    monkeypatch.setattr(ca, '_snapshot_activation_persistence',
                        (lambda: None) if returns_none else _raise)
    for seam in ('_commit_ui_token', '_save_cache', '_install_provider', '_restore_activation_persistence'):
        monkeypatch.setattr(ca, seam, lambda *a, **k: writes.append(True))
    outcome = ca.commit_activation_candidate(candidate)
    _assert_local(outcome.result, 'snapshot', 'not_started')
    assert not writes
    assert ca._RUNTIME_ACTIVATION_DENIED.is_set()


@pytest.mark.parametrize('recovery_complete,runtime_fail_closed', [(True, False), (False, True), (False, False)])
def test_recovery_requires_both_flags(monkeypatch, isolated_activation_runtime, recovery_complete, runtime_fail_closed):
    candidate = _candidate(monkeypatch)
    monkeypatch.setattr(ca, '_save_cache', _raise)
    monkeypatch.setattr(ca, '_recover_failed_candidate_commit',
                        lambda *a, **k: (recovery_complete, runtime_fail_closed))
    outcome = ca.commit_activation_candidate(candidate)
    _assert_local(outcome.result, 'cache', 'incomplete')
    assert ca._RUNTIME_ACTIVATION_DENIED.is_set()


@pytest.mark.parametrize('stage', ['identity', 'clear_epoch'])
def test_final_identity_or_epoch_uses_original_recovery(monkeypatch, isolated_activation_runtime, stage):
    candidate = _candidate(monkeypatch)
    if stage == 'identity':
        snapshot = ca.activation_identity_snapshot
        calls = 0
        def changed(*args, **kwargs):
            nonlocal calls
            calls += 1
            value = snapshot(*args, **kwargs)
            return replace(value, revision=value.revision + 1) if calls > 1 else value
        monkeypatch.setattr(ca, 'activation_identity_snapshot', changed)
    else:
        monkeypatch.setattr(ca, '_complete_activation_clear_epoch', lambda *_a: False)
    restore = ca._restore_activation_persistence
    restored = []
    def counted(value):
        restored.append(True)
        return restore(value)
    monkeypatch.setattr(ca, '_restore_activation_persistence', counted)
    outcome = ca.commit_activation_candidate(candidate)
    _assert_local(outcome.result, stage, 'complete', 'local_identity_changed')
    assert restored == [True]
    assert not activation.is_activated()


def test_stale_before_writes_does_not_deny_new_grant(monkeypatch, isolated_activation_runtime):
    candidate = _candidate(monkeypatch)
    assert ca.save_token(_TOKEN_B)
    latest = enforce.startup_activate(force_server=True)
    assert latest.activated
    provider = activation._provider
    cache = ca._cache_file().read_bytes()
    outcome = ca.commit_activation_candidate(candidate)
    _assert_local(outcome.result, 'identity', 'not_started', 'local_identity_changed')
    assert activation._provider is provider
    assert ca._cache_file().read_bytes() == cache
    assert activation.is_activated()
    assert enforce.current() is latest


def test_enforce_rejects_changed_identity_before_candidate_commit(monkeypatch, isolated_activation_runtime):
    activate = ca.activate
    _candidate(monkeypatch)
    def changed(**kwargs):
        candidate = activate(**kwargs)
        assert ca.save_token(_TOKEN_B)
        return candidate
    monkeypatch.setattr(ca, 'activate', changed)
    monkeypatch.setattr(ca, 'commit_activation_candidate', _raise)
    result = enforce.refresh_activation()
    _assert_local(result, 'identity', 'not_started', 'local_identity_changed')
    assert enforce.current() is result


def test_old_deny_marker_does_not_erase_current_local_commit_failure(monkeypatch, isolated_activation_runtime):
    _candidate(monkeypatch)
    assert ca._persist_authoritative_deny('revoked')
    monkeypatch.setattr(ca, '_clear_persistent_authoritative_deny', lambda: False)
    result = enforce.refresh_activation()
    _assert_local(result, 'deny_marker', 'complete')
    assert enforce.activation_state()['reason'] == 'local_commit_failed'
    assert ca.persistent_authoritative_deny_reason() == 'revoked'
    assert enforce.persistent_authoritative_deny_state()['reason'] == 'revoked'
    assert ca._RUNTIME_ACTIVATION_DENIED.is_set()
    assert not activation.is_activated()
    assert ca.current_upload_lease(now=2_000_001) is None
    monkeypatch.setattr(ca, '_post_activate', lambda *_a: (403, {'ok': False, 'reason': 'device_revoked'}))
    denied = enforce.refresh_activation()
    assert denied.reason == 'device_revoked' and denied.source == 'server'
    assert enforce._LAST is denied
    assert enforce.current().reason == 'device_revoked'
    assert enforce.activation_state()['reason'] == 'device_revoked'


def test_commit_clear_gate_keeps_clear_class(monkeypatch, isolated_activation_runtime):
    candidate = _candidate(monkeypatch)
    monkeypatch.setattr(ca, '_activation_clear_epoch_allows_grant', lambda *_a: False)
    monkeypatch.setattr(ca, '_snapshot_activation_persistence', _raise)
    outcome = ca.commit_activation_candidate(candidate)
    assert outcome.result.reason == 'local_clear_incomplete'
    assert outcome.result.local_commit_stage == 'clear_epoch'
    assert outcome.result.local_commit_recovery == 'not_started'
    assert enforce.activation_state(outcome.result)['needs_token'] is False


@pytest.mark.parametrize('kind', ['no_plan', 'invalid_kind', 'missing_key', 'no_provenance'])
def test_inconsistent_candidates_never_claim_server_validation(monkeypatch, isolated_activation_runtime, kind):
    candidate = _candidate(monkeypatch)
    if kind == 'no_plan':
        candidate = replace(candidate, commit_plan=None)
    elif kind == 'invalid_kind':
        candidate = replace(candidate, commit_plan=replace(candidate.commit_plan, kind='invalid'))
    elif kind == 'missing_key':
        candidate = replace(candidate, commit_plan=replace(candidate.commit_plan, master_key=None))
    else:
        candidate = replace(candidate, _validated_grant=False)
        monkeypatch.setattr(ca, '_save_cache', _raise)
    outcome = ca.commit_activation_candidate(candidate)
    assert not outcome.committed
    assert outcome.result.reason == 'local_activation_state_error'
    assert '服务器已验证' not in outcome.result.status_line()
    assert not enforce.activation_state(outcome.result)['needs_token']


@pytest.mark.parametrize('boundary', ['client', 'enforce'])
def test_outer_grant_exception_reports_unknown(monkeypatch, isolated_activation_runtime, boundary):
    candidate = _candidate(monkeypatch)
    if boundary == 'client':
        monkeypatch.setattr(ca, '_commit_activation_candidate_guarded', _raise)
        result = ca.commit_activation_candidate(candidate).result
    else:
        monkeypatch.setattr(ca, 'commit_activation_candidate', _raise)
        result = enforce.refresh_activation()
    _assert_local(result, 'unexpected', 'unknown')
    assert not activation.is_activated()


@pytest.mark.parametrize('boundary', ['client', 'enforce'])
@pytest.mark.parametrize('reason', ['revoked', 'device_mismatch'])
def test_authoritative_denial_exception_preserves_server_result(monkeypatch, isolated_activation_runtime, boundary, reason):
    monkeypatch.setattr(ca, '_post_activate', lambda *_a: (403, {'ok': False, 'reason': reason}))
    if boundary == 'client':
        candidate = ca.activate(force_server=True, defer_commit=True)
        assert candidate.commit_plan.kind == 'authoritative_clear'
        monkeypatch.setattr(ca, '_clear_cache', _raise)
        result = ca.commit_activation_candidate(candidate).result
    else:
        monkeypatch.setattr(ca, 'commit_activation_candidate', _raise)
        result = enforce.refresh_activation()
    assert result.reason == reason and result.source == 'server'
    assert not result.activated
    assert '服务器已验证' not in result.status_line()
    assert not enforce.should_run_full(result)
    assert not activation.is_activated()


@pytest.mark.parametrize('response,reason', [
    ((200, {'ok': True, 'mode': 'paid', 'lease': 'malformed'}), 'offline'),
    ((503, {'ok': False, 'reason': 'state_error'}), 'state_error'),
])
def test_non_grant_response_unchanged(monkeypatch, isolated_activation_runtime, response, reason):
    monkeypatch.setattr(ca, '_post_activate', lambda *_a: response)
    result = enforce.refresh_activation()
    assert result.reason == reason and result.source == 'server'
    assert result.local_commit_stage == result.local_commit_recovery == ''
    assert '服务器已验证' not in result.status_line()


def test_safe_field_projection_and_missing_compatibility():
    result = ca.ActivationResult(False, 'none', reason='local_activation_state_error', source='client')
    result.local_commit_stage = {'private': 'path'}
    result.local_commit_recovery = 'private exception'
    state = enforce.activation_state(result)
    assert state['local_commit_stage'] == state['local_commit_recovery'] == ''
    del result.local_commit_stage
    del result.local_commit_recovery
    state = enforce.activation_state(result)
    assert state['local_commit_stage'] == state['local_commit_recovery'] == ''


def test_legacy_candidate_without_provenance_keeps_existing_grant_commit(monkeypatch, isolated_activation_runtime):
    candidate = replace(_candidate(monkeypatch), _validated_grant=False)
    outcome = ca.commit_activation_candidate(candidate)
    assert outcome.committed and outcome.result.activated
    assert outcome.result is candidate.result
    assert activation.data_key() == _KEY_A


def test_non_candidate_plugin_success_seam_unchanged(monkeypatch, isolated_activation_runtime):
    candidate = _candidate(monkeypatch)
    assert ca.commit_activation_candidate(candidate).committed
    monkeypatch.setattr(ca, 'activate', lambda **kwargs: candidate.result)
    result = enforce.refresh_activation()
    assert result is candidate.result
    assert result.feature_usage is None
    assert result.reason == candidate.result.reason
    assert result.mode == candidate.result.mode
    assert result.lease_exp == candidate.result.lease_exp
    assert result.master_key == candidate.result.master_key
    assert result.activated and enforce.should_run_full(result)


def test_no_plan_failure_keeps_original_result(monkeypatch, isolated_activation_runtime):
    candidate = _candidate(monkeypatch)
    result = ca.ActivationResult(False, 'none', reason='state_error', source='server')
    outcome = ca.commit_activation_candidate(replace(candidate, result=result, commit_plan=None))
    assert outcome.result is result and outcome.committed


def test_late_old_local_failure_cannot_replace_new_grant(monkeypatch, isolated_activation_runtime):
    _candidate(monkeypatch)
    activate = ca.activate
    started, release = threading.Event(), threading.Event()
    first = True
    def delayed(**kwargs):
        nonlocal first
        candidate = activate(**kwargs)
        if first:
            first = False
            failed = ca._candidate_commit_failure(candidate, stage='snapshot', recovery='not_started')
            started.set()
            assert release.wait(5)
            return replace(candidate, result=failed, commit_plan=None)
        return candidate
    monkeypatch.setattr(ca, 'activate', delayed)
    returned = []
    worker = threading.Thread(target=lambda: returned.append(enforce.refresh_activation()))
    worker.start()
    try:
        assert started.wait(5)
        assert ca.save_token(_TOKEN_B)
        latest = enforce.refresh_activation()
        assert latest.activated
        provider = activation._provider
        cache = ca._cache_file().read_bytes()
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert returned[0].reason == 'local_identity_changed'
    assert enforce.current() is latest
    assert activation._provider is provider and activation.is_activated()
    assert ca._cache_file().read_bytes() == cache


def test_real_dialog_worker_uses_local_failure_message(monkeypatch, dialog, calls):
    dialog.entry.value = _TOKEN_B
    monkeypatch.setattr(ca, '_save_cache', _raise)
    dialog._submit()
    _run_ui_job(dialog)
    assert len(calls) == 1
    result = enforce.current()
    _assert_local(result, 'cache', 'complete')
    assert dialog.status.options['text'] == enforce.activation_state(result)['text']
    assert dialog.status.options['fg'] == 'BAD'
    assert not dialog.callbacks  # no success/close callback


@pytest.mark.parametrize('mode', ['paid', 'trial'])
def test_success_and_same_token_retry_preserve_server_times(monkeypatch, isolated_activation_runtime, mode):
    expected_exp = 2_003_600
    paid_exp = 2_086_400
    def response(_u, _d, token, _t):
        code, body = _success_response(token)
        lease = json.loads(body['lease'].split('.')[0])
        lease['exp'] = expected_exp
        if mode == 'paid':
            lease['paid_exp'] = paid_exp
        body.update(mode=mode, lease=json.dumps(lease) + '.sig')
        if mode == 'trial':
            body['trial_exp'] = expected_exp
        return code, body
    monkeypatch.setattr(ca, '_post_activate', response)
    for _ in range(2):
        result = enforce.refresh_activation(ui_token=_TOKEN_A)
        assert result.activated and result.mode == mode
        assert result.lease_exp == expected_exp
        assert result.paid_exp == (paid_exp if mode == 'paid' else None)
        assert result.trial_exp == (expected_exp if mode == 'trial' else None)
        assert result.local_commit_stage == result.local_commit_recovery == ''
        assert activation.data_key() == _KEY_A


def test_f01_diagnostic_defaults_do_not_create_pet_state_error(monkeypatch, isolated_activation_runtime):
    candidate = _candidate(monkeypatch)
    assert candidate.result.feature_usage is None
    result = enforce.refresh_activation()
    assert result.activated
    assert result.feature_usage is None
    assert result.feature_entitlements == {}


@pytest.mark.parametrize('boundary', ['persist_raise', 'outer_raise'])
def test_f01_current_denial_survives_failure_but_reset_reads_persisted_marker(monkeypatch, isolated_activation_runtime, boundary):
    assert ca._persist_authoritative_deny('revoked')
    monkeypatch.setattr(ca, '_post_activate', lambda *_a: (403, {'ok': False, 'reason': 'device_revoked'}))
    seam = '_persist_authoritative_deny' if boundary == 'persist_raise' else 'commit_activation_candidate'
    monkeypatch.setattr(ca, seam, _raise)
    result = enforce.refresh_activation()
    assert result.reason == enforce.current().reason == 'device_revoked'
    assert enforce.activation_state()['reason'] == 'device_revoked'
    assert ca.persistent_authoritative_deny_reason() == 'revoked'
    assert not activation.is_activated() and not enforce.should_run_full()
    assert ca.current_upload_lease(now=2_000_001) is None
    enforce._LAST = enforce._LAST_HEARTBEAT = None
    assert enforce.current().reason == 'revoked'
    assert enforce.persistent_authoritative_deny_state()['reason'] == 'revoked'


@pytest.mark.parametrize('mutation', ['new_generation', 'changed_identity', 'aba_identity'])
def test_f01_rejection_projection_expires_with_request_or_identity(monkeypatch, isolated_activation_runtime, mutation):
    assert ca._persist_authoritative_deny('revoked')
    monkeypatch.setattr(ca, '_post_activate', lambda *_a: (403, {'ok': False, 'reason': 'device_mismatch'}))
    result = enforce.refresh_activation()
    assert result.reason == enforce.activation_state()['reason'] == 'device_mismatch'
    if mutation == 'new_generation':
        enforce._ACTIVATION_REQUEST_GENERATION += 1
    else:
        assert ca.save_token(_TOKEN_B)
        if mutation == 'aba_identity':
            assert ca.save_token(_TOKEN_A)
    assert enforce.activation_state()['reason'] == 'revoked'
    assert not enforce.should_run_full()


@pytest.mark.parametrize('seam', ['result', 'manual_candidate'])
def test_f01_unproven_server_result_does_not_override_persisted_marker(monkeypatch, isolated_activation_runtime, seam):
    assert ca._persist_authoritative_deny('revoked')
    monkeypatch.setattr(ca, '_post_activate', lambda *_a: (403, {'ok': False, 'reason': 'device_mismatch'}))
    candidate = ca.activate(force_server=True, defer_commit=True)
    if seam == 'manual_candidate':
        supplied = ca.ActivationCandidate(result=candidate.result, identity_snapshot=candidate.identity_snapshot,
                                          commit_plan=candidate.commit_plan)
    else:
        supplied = candidate.result
    monkeypatch.setattr(ca, 'activate', lambda **kwargs: supplied)
    ca._RUNTIME_ACTIVATION_DENIED.set()
    enforce.refresh_activation()
    assert enforce.activation_state()['reason'] == 'revoked'
    assert not enforce.should_run_full()


def test_f01_copy_is_exact_and_other_local_messages_preserved():
    assert ca._LOCAL_ACTIVATION_MESSAGES == {
        'local_commit_failed': '验证通过，本机激活未完成；请重启重试，仍异常请导出诊断',
        'local_identity_changed': '本机激活状态已变化，请重试；仍异常请重启并导出诊断',
        'local_activation_state_error': '本机激活状态异常，请重启后重试；仍异常请导出诊断',
    }


@pytest.mark.parametrize('mode', ['paid', 'trial'])
@pytest.mark.parametrize('invalid', [False, True])
def test_f01_real_pet_usage_survives_refresh_and_guard(monkeypatch, isolated_activation_runtime, mode, invalid):
    now, exp = 2_000_000, 2_003_600
    pet = dict(kind='quota' if mode == 'paid' else 'trial',
               total_seconds=43200 if mode == 'paid' else 600,
               remaining_seconds=43200 if mode == 'paid' else 600,
               status='available', server_now=now, entitlement_id='a' * 64,
               usage_revision=1, last_seq=-1, visible=False, visibility_lease_exp=now,
               entitlement_exp=now + 86400 if mode == 'paid' else exp)
    if mode == 'paid':
        pet['duration_days'] = 30
    if invalid:
        pet['remaining_seconds'] = True
    code, body = _success_response(_TOKEN_A)
    lease = json.loads(body['lease'].split('.')[0])
    lease.update(exp=exp, plan_kind='30d' if mode == 'paid' else 'trial',
                 feature_entitlements={'pet_companion': pet['kind']},
                 feature_usage={'schema_version': 1, 'pet_companion': pet})
    if mode == 'paid':
        lease['paid_exp'] = now + 86400
    else:
        body['trial_exp'] = exp
    body.update(mode=mode, lease=json.dumps(lease) + '.' + '0' * 64)
    monkeypatch.setattr(ca, '_post_activate', lambda *_a: (code, body))
    candidate = ca.activate(force_server=True, defer_commit=True)
    assert candidate.result.activated
    for _ in range(2):
        result = enforce.refresh_activation()
        state = enforce.activation_state()
        assert result.activated and state['run_full']
        assert result.feature_usage == candidate.result.feature_usage
        observed = state['feature_usage']['pet_companion']
        if invalid:
            assert observed['kind'] == 'none' and observed['status'] == 'state_error'
        else:
            assert observed == pet
            assert enforce._LAST_PET_USAGE_AUTHORITY == pet


def test_f01_projection_keeps_required_authority_schema_and_ignores_only_metadata():
    class Bomb:
        def __getattribute__(self, name):
            raise AssertionError('diagnostic value inspected')
    result = ca.ActivationResult(False, 'none')
    result.local_commit_stage = result.local_commit_recovery = Bomb()
    result._authoritative_rejection_context = Bomb()
    fields = enforce._project_activation_result_fields(result, result_type=ca.ActivationResult)
    assert len(fields) == 13
    assert set(fields) == set(enforce._ACTIVATION_RESULT_FIELD_NAMES)
    assert not (set(fields) & enforce._ACTIVATION_RESULT_DIAGNOSTIC_FIELDS)
    for name in enforce._ACTIVATION_RESULT_DIAGNOSTIC_FIELDS:
        result.__dict__.pop(name)
    assert enforce._project_activation_result_fields(result, result_type=ca.ActivationResult) == fields
    result.__dict__['unknown_field'] = None
    assert enforce._project_activation_result_fields(result, result_type=ca.ActivationResult) is None
    result.__dict__.pop('unknown_field')
    result.__dict__.pop('lease_exp')
    assert enforce._project_activation_result_fields(result, result_type=ca.ActivationResult) is None


def test_f01_fresh_process_reads_persisted_denial(monkeypatch, isolated_activation_runtime):
    assert ca._persist_authoritative_deny('revoked')
    monkeypatch.setattr(ca, '_post_activate', lambda *_a: (403, {'ok': False, 'reason': 'device_mismatch'}))
    assert enforce.refresh_activation().reason == enforce.activation_state()['reason'] == 'device_mismatch'
    command = "from bidking_lab.secure import enforce; s=enforce.persistent_authoritative_deny_state(); print(s['reason'], s['run_full'])"
    process = subprocess.run([sys.executable, '-c', command], env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
                             capture_output=True, text=True, timeout=10)
    assert process.returncode == 0, process.stderr
    assert process.stdout.strip() == 'revoked False'


@pytest.mark.parametrize('aba', [False, True])
def test_f01_late_server_rejection_cannot_replace_new_grant(monkeypatch, isolated_activation_runtime, aba):
    started, release = threading.Event(), threading.Event()
    first = True
    def post(_u, _d, token, _t):
        nonlocal first
        if first:
            first = False
            started.set()
            assert release.wait(5)
            return 403, {'ok': False, 'reason': 'device_mismatch'}
        return _success_response(token)
    monkeypatch.setattr(ca, '_post_activate', post)
    returned = []
    thread = threading.Thread(target=lambda: returned.append(enforce.refresh_activation()))
    thread.start()
    try:
        assert started.wait(5)
        assert ca.save_token(_TOKEN_B)
        latest = enforce.refresh_activation()
        if aba:
            assert ca.save_token(_TOKEN_A)
            latest = enforce.refresh_activation()
        assert latest.activated
        provider = activation._provider
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert returned[0].reason == 'local_identity_changed'
    assert returned[0]._authoritative_rejection_context is None
    assert enforce.current() is latest and enforce.should_run_full()
    assert activation._provider is provider and activation.is_activated()


def test_f01_clear_invalidates_rejection_display_provenance(monkeypatch, isolated_clear_runtime):
    assert ca._persist_authoritative_deny('revoked')
    monkeypatch.setattr(ca, '_post_activate', lambda *_a: (403, {'ok': False, 'reason': 'device_mismatch'}))
    result = enforce.refresh_activation()
    assert enforce._is_current_server_rejection(result)
    assert enforce.clear_activation()['cleared'] is True
    assert not enforce._is_current_server_rejection(result)
    assert not enforce.should_run_full()


def test_f01_rejection_context_is_not_public_metadata(monkeypatch, isolated_activation_runtime):
    assert ca._persist_authoritative_deny('revoked')
    monkeypatch.setattr(ca, '_post_activate', lambda *_a: (403, {'ok': False, 'reason': 'device_mismatch'}))
    result = enforce.refresh_activation()
    assert result._authoritative_rejection_context is not None
    assert '_authoritative_rejection_context' not in repr(result)
    state = enforce.activation_state()
    assert '_authoritative_rejection_context' not in state
    assert _TOKEN_A not in str(state) + repr(result) + result.status_line()
    result._authoritative_rejection_context = {'private': 'PRIVATE-CANARY-C:/synthetic/token'}
    assert not enforce._is_current_server_rejection(result)
    assert 'PRIVATE-CANARY' not in repr(result) + str(enforce.activation_state(result)) + result.status_line()
