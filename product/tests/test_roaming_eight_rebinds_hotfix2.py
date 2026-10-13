"""ROAM-B1 service/storage/wire contract; synthetic authority only."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import activation_core as core
import activation_server as server

SECRET = b'ROAM-B1-SYNTHETIC-SECRET'
KEY = base64.b64encode(b'R' * 32).decode('ascii')
NOW = 2_000_000_000
REAL_TIME = time.time


@pytest.fixture(autouse=True)
def frozen_time(monkeypatch):
    monkeypatch.setattr(server.time, 'time', lambda: NOW)


def service(root):
    root.mkdir(parents=True, exist_ok=True)
    database = root / 'pet_usage.sqlite3'
    if not database.exists():
        server.initialize_pet_usage_database(database, secret=SECRET,
            legacy_paths=(root / 'pet_usage.json', root / 'pet_usage.anchor.jsonl'))
    return server.ActivationService(secret=SECRET, master_key_b64=KEY,
        bindings_path=root / 'bindings.json', revoked_path=root / 'revoked.txt',
        telemetry_path=root / 'telemetry.jsonl', trials_path=root / 'trials.json',
        paid_starts_path=root / 'paid_starts.json', pet_usage_db_path=database,
        pet_usage_path=root / 'pet_usage.json', device_block_key=b'D' * 32,
        upload_security_epoch=1)


def token(uid='roam-card', *, days=30, iat=NOW-60, roaming=True):
    return core.mint_token(uid, SECRET, iat=iat,
                           exp=None if days is None else iat + days * 86400, roaming=roaming)


def request(svc, device, signed=None, *, upload=False):
    body = {'token': signed or token(), 'device': device}
    if upload:
        body['upload_lease_schema'] = 1
    return svc.handle_activate(body)


def lease(response):
    assert response[0] == 200 and response[1]['ok'] is True
    return json.loads(response[1]['lease'].rsplit('.', 1)[0])


def test_ninth_rebind_is_denied(tmp_path):
    svc = service(tmp_path)
    for index in range(9):
        assert request(svc, f'DEVICE-{index}')[0] == 200
    denied = request(svc, 'DEVICE-9', upload=True)
    assert denied == (403, {'ok': False, 'mode': 'paid', 'reason': 'device_mismatch',
                           'detail': 'roaming_rebind_limit',
                           'roaming_rebinds': {'schema_version': 1, 'policy_epoch': 1,
                                               'limit': 8, 'used': 8, 'remaining': 0}})
    assert request(svc, 'DEVICE-8')[0] == 200


def test_same_device_and_bound_card_positive_control(tmp_path):
    svc = service(tmp_path)
    signed = token(roaming=False)
    assert request(svc, 'DEVICE-A', signed)[0] == 200
    assert request(svc, 'DEVICE-A', signed)[0] == 200
    denied = request(svc, 'DEVICE-B', signed)
    assert denied == (403, {'ok': False, 'mode': 'paid', 'reason': 'device_mismatch'})


def record(device, used):
    return {'schema_version': 1, 'device': device, 'roaming_rebinds_used': used}


def read_bindings(svc):
    return json.loads(svc.bindings_path.read_text(encoding='utf-8'))


@pytest.mark.parametrize('days', [30, 90, 365])
@pytest.mark.parametrize('legacy', [False, True])
def test_all_terms_legacy_and_new_cards_share_eight(tmp_path, days, legacy):
    svc = service(tmp_path)
    signed = token(days=days)
    if legacy:
        server._atomic_write_json(svc.bindings_path, {'roam-card': 'DEVICE-A'})
        server._atomic_write_json(svc.paid_starts_path, {'roam-card': NOW-30})
    first = lease(request(svc, 'DEVICE-A', signed))
    assert first['roaming_rebinds'] == core.roaming_rebinds_state(0)
    expected_start = NOW-30 if legacy else NOW
    assert first['paid_exp'] == expected_start + days * 86400
    assert read_bindings(svc)['roam-card'] == record('DEVICE-A', 0)
    second = lease(request(svc, 'DEVICE-B', signed))
    third = lease(request(svc, 'DEVICE-A', signed))
    assert second['roaming_rebinds']['used'] == 1
    assert third['roaming_rebinds']['used'] == 2
    assert third['paid_exp'] == first['paid_exp']
    assert third['feature_usage']['pet_companion']['kind'] == ('quota' if days == 30 else 'full')


def test_same_uid_resign_term_change_and_nonroaming_do_not_reset(tmp_path):
    svc = service(tmp_path)
    for index in range(8):
        request(svc, f'DEVICE-{index}')
    resign = token(days=365, iat=NOW-500)
    assert lease(request(svc, 'DEVICE-7', resign))['roaming_rebinds']['used'] == 7
    bound = token(days=90, iat=NOW-300, roaming=False)
    assert 'roaming_rebinds' not in lease(request(svc, 'DEVICE-7', bound))
    assert read_bindings(svc)['roam-card'] == record('DEVICE-7', 7)
    assert lease(request(svc, 'DEVICE-8', resign))['roaming_rebinds']['used'] == 8
    assert request(svc, 'DEVICE-9', resign)[0] == 403
    assert lease(request(svc, 'DEVICE-9', token('new-card')))['roaming_rebinds']['used'] == 0
    assert read_bindings(svc)['roam-card'] == record('DEVICE-8', 8)


@pytest.mark.parametrize('used', [0, 7, 8])
def test_restart_preserves_counter_and_current_device(tmp_path, used):
    svc = service(tmp_path)
    server._atomic_write_json(svc.bindings_path, {'roam-card': record('DEVICE-A', used)})
    server._atomic_write_json(svc.paid_starts_path, {'roam-card': NOW-60})
    restarted = service(tmp_path)
    assert lease(request(restarted, 'DEVICE-A'))['roaming_rebinds']['used'] == used
    moved = request(restarted, 'DEVICE-B')
    assert moved[0] == (403 if used == 8 else 200)
    assert read_bindings(restarted)['roam-card']['roaming_rebinds_used'] == min(8, used+1)


@pytest.mark.parametrize('bad', [None, '', 2, [], {}, {'device': 'DEVICE-A'},
    {'schema_version': 1, 'device': 'DEVICE-A', 'roaming_rebinds_used': 0, 'unknown': 1},
    record('DEVICE-A', True), record('DEVICE-A', 1.0), record('DEVICE-A', '1'),
    record('DEVICE-A', -1), record('DEVICE-A', 9), record('', 0),
    {'schema_version': True, 'device': 'DEVICE-A', 'roaming_rebinds_used': 0},
    {'schema_version': 2, 'device': 'DEVICE-A', 'roaming_rebinds_used': 0}])
def test_invalid_binding_never_resets_or_reports_quota(tmp_path, bad):
    svc = service(tmp_path)
    server._atomic_write_json(svc.bindings_path, {'roam-card': bad})
    before = svc.bindings_path.read_bytes()
    code, payload = request(svc, 'DEVICE-B')
    assert (code, payload) == (503, {'ok': False, 'mode': 'paid', 'reason': 'state_error'})
    assert svc.bindings_path.read_bytes() == before
    assert not svc.paid_starts_path.exists()
    assert core.activate(token(), SECRET, 'DEVICE-B', bindings={'roam-card': bad}, revoked=set(), now=NOW) == (False, 'state_error', None)


@pytest.mark.parametrize('failure', ['bad_token', 'revoked', 'expired', 'blocked'])
def test_denial_precedence_cannot_claim_exhaustion(tmp_path, failure):
    svc = service(tmp_path)
    server._atomic_write_json(svc.bindings_path, {'roam-card': record('DEVICE-A', 8)})
    server._atomic_write_json(svc.paid_starts_path, {'roam-card': NOW-60})
    signed = token()
    expected = failure
    if failure == 'bad_token':
        signed += 'x'
    elif failure == 'revoked':
        svc.revoked_path.write_text('roam-card\n', encoding='utf-8')
    elif failure == 'expired':
        server._atomic_write_json(svc.paid_starts_path, {'roam-card': NOW-31*86400})
    else:
        expected = 'device_revoked'
        server._atomic_write_json(svc.device_blocks_path, {'schema_version': 1, 'blocked': {
            server.device_block_id(b'D'*32, 'DEVICE-B'): {'blocked_at': NOW, 'reason': 'confirmed_abuse',
                                                       'evidence_ref': 'INC-123456789012', 'actor': 'author'}}})
    response = request(svc, 'DEVICE-B', signed)
    assert response[1]['reason'] == expected
    assert 'roaming_rebinds' not in response[1] and 'detail' not in response[1]
    assert read_bindings(svc)['roam-card'] == record('DEVICE-A', 8)


@pytest.mark.parametrize('failure', ['before_replace', 'after_replace', 'readback'])
def test_commit_failure_retry_is_atomic_and_nested_cache_is_unmodified(tmp_path, monkeypatch, failure):
    svc = service(tmp_path)
    request(svc, 'DEVICE-A')
    cached = svc._paid_authority_cache[svc.bindings_path][1]
    cached_row = cached['roam-card']
    starts_before = svc.paid_starts_path.read_bytes()
    real_replace = server.os.replace
    real_load = svc._load_paid_authority_snapshot
    reads = 0
    def replace(source, target):
        if Path(target) == svc.bindings_path:
            if failure == 'after_replace':
                real_replace(source, target)
            raise OSError('synthetic atomic replace failure')
        real_replace(source, target)
    def load(path, *, now):
        nonlocal reads
        if path == svc.bindings_path:
            reads += 1
            if reads == 2:
                raise server.StateFileError('synthetic post-commit readback failure')
        return real_load(path, now=now)
    with monkeypatch.context() as faults:
        if failure == 'readback':
            faults.setattr(svc, '_load_paid_authority_snapshot', load)
        else:
            faults.setattr(server.os, 'replace', replace)
        assert request(svc, 'DEVICE-B') == (503, {'ok': False, 'mode': 'paid', 'reason': 'state_error'})
    expected = record('DEVICE-A', 0) if failure == 'before_replace' else record('DEVICE-B', 1)
    assert read_bindings(svc)['roam-card'] == expected
    assert cached['roam-card'] is cached_row and cached_row == record('DEVICE-A', 0)
    assert svc.paid_starts_path.read_bytes() == starts_before
    assert lease(request(svc, 'DEVICE-B'))['roaming_rebinds']['used'] == 1
    assert lease(request(service(tmp_path), 'DEVICE-B'))['roaming_rebinds']['used'] == 1


def test_same_device_concurrency_and_distinct_device_limit_are_serial(tmp_path):
    svc = service(tmp_path)
    request(svc, 'DEVICE-A')
    with ThreadPoolExecutor(max_workers=4) as pool:
        same = list(pool.map(lambda _: request(svc, 'DEVICE-B'), range(8)))
    assert all(response[0] == 200 for response in same)
    assert read_bindings(svc)['roam-card'] == record('DEVICE-B', 1)
    with ThreadPoolExecutor(max_workers=4) as pool:
        distinct = list(pool.map(lambda index: request(svc, f'DEVICE-{index}'), range(10)))
    assert sum(response[0] == 200 for response in distinct) == 7
    assert sum(response[0] == 403 for response in distinct) == 3
    assert read_bindings(svc)['roam-card']['roaming_rebinds_used'] == 8


def test_unchanged_heartbeat_is_write_free_and_uses_same_complete_cache(tmp_path, monkeypatch):
    svc = service(tmp_path)
    server._atomic_write_json(svc.bindings_path, {'other-card': record('OTHER', 5)})
    request(svc, 'DEVICE-A')
    current = svc._paid_authority_cache[svc.bindings_path][1]
    other = current['other-card']
    before = svc.bindings_path.read_bytes()
    def no_write(*args):
        raise AssertionError('unchanged heartbeat attempted authority write')
    monkeypatch.setattr(server, '_atomic_write_json', no_write)
    for _ in range(4):
        assert lease(request(svc, 'DEVICE-A'))['roaming_rebinds']['used'] == 0
        assert svc._paid_authority_cache[svc.bindings_path][1] is current
    assert current['other-card'] is other and other == record('OTHER', 5)
    assert svc.bindings_path.read_bytes() == before


def test_upload_lease_tracks_device_from_versioned_record(tmp_path):
    svc = service(tmp_path)
    initial = request(svc, 'DEVICE-A', upload=True)
    old_upload = initial[1]['upload_lease']
    assert svc.authorize_upload_lease(old_upload, now=NOW+1)[0]
    moved = request(svc, 'DEVICE-B', upload=True)
    assert svc.authorize_upload_lease(old_upload, now=NOW+1)[:2] == (False, 'identity_device_mismatch')
    assert svc.authorize_upload_lease(moved[1]['upload_lease'], now=NOW+1)[0]


def test_device_admin_projects_devices_without_mutating_records(tmp_path, capsys):
    import activation_device_admin as admin
    svc = service(tmp_path)
    request(svc, 'DEVICE-A')
    bindings = read_bindings(svc)
    bindings['legacy-card'] = 'DEVICE-A'
    server._atomic_write_json(svc.bindings_path, bindings)
    (tmp_path / 'device-key.txt').write_bytes(b'D'*32)
    args = SimpleNamespace(state_dir=str(tmp_path), device_block_key_file='device-key.txt',
        bindings_file='bindings.json', paid_starts_file='paid_starts.json', revoked_file='revoked.txt',
        device_blocks_file='device_blocks.json')
    before = svc.bindings_path.read_bytes()
    context = admin._load_context(args)
    assert context[2] == {'roam-card': 'DEVICE-A', 'legacy-card': 'DEVICE-A'}
    args.uid = ['roam-card', 'legacy-card']
    assert admin._inspect(args) == 0
    output = capsys.readouterr().out
    rows = json.loads(output)['candidates']
    assert all(row['bound'] and row['same_device_uid_count'] == 2 for row in rows)
    assert rows[0]['device_alias'] == rows[1]['device_alias']
    assert 'DEVICE-A' not in output and 'roam-card' not in output
    assert svc.bindings_path.read_bytes() == before


def test_permanent_and_trial_do_not_advertise_roaming_quota(tmp_path):
    svc = service(tmp_path)
    assert 'roaming_rebinds' not in lease(request(svc, 'DEVICE-A', token(days=None)))
    assert request(svc, 'DEVICE-B', token(days=None))[0] == 403
    assert 'roaming_rebinds' not in lease(svc.handle_activate({'device': 'TRIAL-DEVICE'}))


def test_api_mixed_version_and_missing_reader_are_rejected(tmp_path, monkeypatch):
    assert server.activation_core_compatibility_error() is None
    with monkeypatch.context() as old:
        old.setattr(core, 'ACTIVATION_CORE_API_VERSION', 5)
        assert server.activation_core_compatibility_error() == 'activation_core_api_mismatch'
        with pytest.raises(RuntimeError, match='activation_core_api_mismatch'):
            service(tmp_path)
    monkeypatch.setattr(core, 'binding_state', None)
    assert server.activation_core_compatibility_error() == 'activation_core_symbol_missing'


def test_complete_candidate_gate_rejects_old_core_api(tmp_path):
    from test_activation_deploy_contract import _copy_candidate
    from activation_deploy_gate import validate_candidate_dir
    candidate = _copy_candidate(tmp_path / 'candidate')
    path = candidate / 'activation_core.py'
    text = path.read_text(encoding='utf-8')
    assert 'ACTIVATION_CORE_API_VERSION = 6' in text
    path.write_text(text.replace('ACTIVATION_CORE_API_VERSION = 6', 'ACTIVATION_CORE_API_VERSION = 5', 1), encoding='utf-8')
    with pytest.raises(RuntimeError, match='activation_core_api_mismatch'):
        validate_candidate_dir(candidate)


def test_state_aware_existing_api_gate_accepts_new_core_and_rejects_old(monkeypatch):
    import activation_state_aware_deploy as deploy
    deploy._require_core_accepts_v2(core, {}, label='synthetic-current')
    monkeypatch.setattr(core, 'ACTIVATION_CORE_API_VERSION', 5)
    with pytest.raises(deploy.DeploymentError, match='runtime does not support trial v2'):
        deploy._require_core_accepts_v2(core, {}, label='synthetic-old')


def test_existing_client_treats_quota_denial_as_authoritative_not_transient(tmp_path, monkeypatch):
    from bidking_lab.secure import client_activate as client, enforce
    svc = service(tmp_path / 'server')
    signed = token()
    for index in range(9):
        assert request(svc, f'DEVICE-{index}', signed)[0] == 200
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'client'))
    monkeypatch.delenv('BIDKING_ACTIVATION_TOKEN', raising=False)
    monkeypatch.delenv('BIDKING_ACTIVATION_FILE', raising=False)
    monkeypatch.setattr(client, '_package_dir', lambda: tmp_path / 'package')
    monkeypatch.setattr(client, 'device_fingerprint', lambda: 'DEVICE-9')
    monkeypatch.setattr(client, '_ACTIVATION_CLEAR_EPOCH', 0)
    monkeypatch.setattr(client, '_ACTIVATION_CLEAR_STATE', None)
    monkeypatch.setattr(client, '_activate_urls', lambda _: ['https://activation.invalid/activate'])
    observed = []
    def post(_url, device, value, _timeout):
        response = request(svc, device, value)
        observed.append(response)
        return response
    monkeypatch.setattr(client, '_post_activate', post)
    result = client.activate(ui_token=signed, force_server=True)
    assert len(observed) == 1 and observed[0][0] == 403
    assert observed[0][1]['detail'] == 'roaming_rebind_limit'
    assert result.activated is False and result.reason == 'device_mismatch' and result.source == 'server'
    assert not enforce._is_graceable_heartbeat_failure(result)
    assert enforce.activation_state(result)['run_full'] is False


def test_core_only_cow_preserves_other_records_and_expired_start(tmp_path):
    old = record('DEVICE-A', 7)
    other = record('OTHER', 3)
    bindings = {'roam-card': old, 'other': other}
    starts = {'roam-card': NOW-100}
    ok, _, signed_lease = core.activate(token(), SECRET, 'DEVICE-B', bindings=bindings,
                                         revoked=set(), paid_starts=starts, now=NOW)
    assert ok and signed_lease['roaming_rebinds']['used'] == 8
    assert bindings['roam-card'] is not old and old == record('DEVICE-A', 7)
    assert bindings['other'] is other and starts['roam-card'] == NOW-100
    no_clock = {}
    assert core.activate(token(), SECRET, 'DEVICE-C', bindings=bindings,
                         revoked=set(), paid_starts=no_clock, now=NOW) == (False, 'roaming_rebind_limit', None)
    assert no_clock == {} and bindings['roam-card'] == record('DEVICE-B', 8)


def test_post_replace_error_invalidates_same_stamp_cache_before_next_rebind(tmp_path, monkeypatch):
    svc = service(tmp_path)
    request(svc, 'DEVICE-A')
    original_stamp = server._authority_file_stamp
    fixed = original_stamp(svc.bindings_path)
    monkeypatch.setattr(server, '_authority_file_stamp',
        lambda path: fixed if path == svc.bindings_path else original_stamp(path))
    write = server._atomic_write_json
    def committed_then_raises(path, value):
        write(path, value)
        if path == svc.bindings_path:
            raise OSError('synthetic failure after replace')
    with monkeypatch.context() as fault:
        fault.setattr(server, '_atomic_write_json', committed_then_raises)
        assert request(svc, 'DEVICE-B')[0] == 503
    assert read_bindings(svc)['roam-card'] == record('DEVICE-B', 1)
    assert lease(request(svc, 'DEVICE-C'))['roaming_rebinds']['used'] == 2
    assert read_bindings(svc)['roam-card'] == record('DEVICE-C', 2)


def test_complete_candidate_gate_detects_ninth_rebind_positive_control(tmp_path, monkeypatch):
    from test_activation_deploy_contract import _copy_candidate
    from activation_deploy_gate import CandidateError, validate_candidate_dir
    monkeypatch.setattr(server.time, 'time', REAL_TIME)
    candidate = _copy_candidate(tmp_path / 'candidate')
    path = candidate / 'activation_core.py'
    text = path.read_text(encoding='utf-8')
    assert 'ROAMING_REBINDS_LIMIT = 8' in text
    path.write_text(text.replace('ROAMING_REBINDS_LIMIT = 8', 'ROAMING_REBINDS_LIMIT = 9', 1), encoding='utf-8')
    with pytest.raises(CandidateError, match='unexpected paid contract'):
        validate_candidate_dir(candidate)
