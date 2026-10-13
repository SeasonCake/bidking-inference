"""REF-P1 owner checks: actual worker/parser/child; explicit numeric seams."""
from dataclasses import replace
import json
import hashlib
import copy
import re
from pathlib import Path
import subprocess
import shutil
import sys
import time
import threading
from types import SimpleNamespace

import pytest
import psutil

from test_fatbeans_webhook_monitor import _module
from test_refresh_semantic_deadline_hotfix2 import _state, _action
from bidking_lab.live import monitor as engine


def roundtrip(value):
    result = None
    for result in engine._full_wire_decode(engine._full_wire_tokens(value)):
        pass
    return result


def test_wire_preserves_verified_unverified_nested_policy_and_conflicts():
    from bidking_lab.simulation.basic_mc import (
        DropPoolResolutionPolicy, PolicyBoundDropPools, RankMapAlgorithmPriorBoundDropPools,
        _bind_verified_drop_pool_resolution_policy, _verified_resolution_policy, FlattenedPool,
    )
    policy = DropPoolResolutionPolicy('test-policy', 'test-generation', frozenset({1}), frozenset({(1, 2)}))
    for verified in (True, False):
        pools = (_bind_verified_drop_pool_resolution_policy if verified else PolicyBoundDropPools)({}, policy)
        for wrapped in (False, True):
            drops = (RankMapAlgorithmPriorBoundDropPools(pools, {1: FlattenedPool(
                item_ids=[2], probabilities=[1.0], n_min=[1], n_max=[1], values=[123])},
                package_generation='test-generation', authority_mode='test') if wrapped else pools)
            tables = engine.MonitorTables({}, drops, {}, warehouse_game_version='v1')
            tables.warehouse_session_versions.resolve('s', 'v1')
            tables.warehouse_session_versions.resolve('s', 'v2')
            rebuilt = engine._full_tables_restore(roundtrip(engine._full_tables_snapshot(tables, 's')))
            assert rebuilt.warehouse_session_versions.first_by_session == {'s': 'v1'}
            assert rebuilt.warehouse_session_versions.conflicted_sessions == {'s'}
            assert rebuilt.warehouse_session_versions.lock is not tables.warehouse_session_versions.lock
            assert type(rebuilt.drops) is type(drops)
            inner = rebuilt.drops._pools if wrapped else rebuilt.drops
            assert (_verified_resolution_policy(inner) is not None) is verified
            if wrapped:
                assert rebuilt.drops.flattened_pool(1).values == [123]
                assert (rebuilt.drops.resolution_policy is not None) is verified


def test_wire_rejects_hooks_and_oversized_depth():
    class Unserializable:
        def __reduce__(self):
            raise AssertionError('must not call a pickle hook')
    with pytest.raises(ValueError, match='full_input_type'):
        list(engine._full_wire_tokens(Unserializable()))
    value = []
    for _ in range(66):
        value = [value]
    with pytest.raises(ValueError, match='full_input_depth'):
        list(engine._full_wire_tokens(value))


def test_wire_chunked_text_and_identity_types():
    value = {'tuple': ('s', 2, frozenset({4})), 'text': '你好' * 100000}
    encoded = list(engine._full_wire_tokens(value))
    assert max(map(len, encoded)) <= engine._FULL_WIRE_TOKEN_LIMIT
    assert roundtrip(value) == value


@pytest.fixture
def rig(tmp_path, monkeypatch):
    module = _module()
    tables = engine.MonitorTables({}, {}, {}, warehouse_game_version='test-version')
    monitor = module.FatbeansWebhookMonitor(config=module.WebhookMonitorConfig(
        log_dir=tmp_path/'logs', raw_dir=tmp_path/'raw', process_name='BidKing.exe',
        server_ports=(10000,), n_trials=500, roi_trials=0, shadow_trials=20,
        full_shadow_trials=20, run_debug_shadows=False, seed=1,
        archive_round_snapshots=False), tables=tables)
    builds = []
    def fast(payload, **kwargs):
        assert kwargs['n_trials'] == 20
        signature = module._semantic_signature_from_payload(payload)
        builds.append(signature)
        return {'session_id':signature[0], 'phase':'bidding', 'round':1,
                'created_at':time.time(), 'processor':'fast-seam'}
    monkeypatch.setattr(module, 'build_monitor_artifact_from_payload', fast)
    marker = tmp_path/'child-entered.txt'
    state = SimpleNamespace(module=module, monitor=monitor, marker=marker, builds=builds,
                            behavior='normal', process=None, path=tmp_path)
    def start(job):
        code = ('from test_refresh_full_budget_0_3_5_child import run;run(' +
                repr(job['channel_name'])+','+repr(state.behavior)+','+repr(str(marker))+')')
        state.process = subprocess.Popen([sys.executable, '-B', '-c', code],
                                         creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return state.process
    monkeypatch.setattr(monitor, '_start_full_process', start)
    yield state
    monitor._stop.set()
    if monitor._worker.is_alive():
        monitor._queue.put_nowait(object())
        monitor._worker.join(3)
    if monitor._full_job is not None:
        assert monitor._end_full_job(monitor._full_job, 'test_cleanup')
    monitor._fact_receipts.close()
    assert state.process is None or state.process.poll() is not None
    if marker.exists():
        assert not psutil.pid_exists(int(marker.read_text(encoding='ascii')))


def prepare(rig):
    monitor = rig.monitor
    monitor.accept_row(_state())
    monitor.accept_row(_action())
    monitor._process_snapshot(force=False)
    monitor._last_packet_at = time.monotonic() - 2
    monitor._maybe_run_idle_full_upgrade()
    assert monitor._full_job is not None


def drain(rig, timeout=8):
    until = time.monotonic() + timeout
    while rig.monitor._full_job is not None and time.monotonic() < until:
        rig.monitor._supervise_full_job()
        time.sleep(.01)
    assert rig.monitor._full_job is None


def entered(rig):
    until = time.monotonic() + 4
    while not rig.marker.exists() and time.monotonic() < until:
        rig.monitor._supervise_full_job()
        time.sleep(.01)
    assert rig.marker.exists()
    assert rig.process.poll() is None


def test_real_child_success_and_single_attempt(rig):
    prepare(rig)
    old_job = rig.monitor._full_job
    drain(rig)
    assert rig.monitor._full_status['stage'] == 'full_completed'
    assert rig.monitor._full_status['reaped']
    snapshot = json.loads((rig.path/'logs/latest_snapshot.json').read_text(encoding='utf-8'))
    assert snapshot['snapshot_mode'] == 'full' and snapshot['received_trials'] == 500
    assert rig.monitor._last_full_processed_count == 2
    rig.monitor._maybe_run_idle_full_upgrade()
    assert rig.monitor._full_job is None
    seq = rig.monitor._snapshot_publish_seq
    rig.monitor._commit_full_job(old_job)
    assert rig.monitor._snapshot_publish_seq == seq


@pytest.mark.parametrize('reason', ['semantic', 'reset', 'stop', 'bid'])
def test_entered_child_cancel_preserves_success_side_effects(rig, reason):
    rig.behavior = 'slow'
    prepare(rig)
    entered(rig)
    monitor = rig.monitor
    inference_at = monitor._last_inference_at
    raw_count = monitor._raw_persisted_count
    snapshot = (rig.path/'logs/latest_snapshot.json').read_bytes()
    old_signature = monitor._last_bid_signature
    if reason == 'semantic':
        monitor.accept_row(_action(13))
    elif reason == 'reset':
        monitor.reset_rows()
    elif reason == 'stop':
        monitor._stop.set()
    else:
        monitor.accept_row({'SortID':3}, schedule_process=False)
    successor_anchor = monitor._pending_semantic_anchor
    successor_trace = monitor._active_compute_trace
    monitor._supervise_full_job()
    assert monitor._full_job is None and rig.process.poll() is not None
    assert monitor._full_status['stage'] == 'full_cancelled'
    assert (rig.path/'logs/latest_snapshot.json').read_bytes() == snapshot
    assert not monitor.raw_path.exists()
    assert monitor._last_full_processed_count == 0
    assert monitor._pending_semantic_anchor == successor_anchor
    assert monitor._active_compute_trace == successor_trace
    if reason != 'reset':
        assert monitor._last_inference_at == inference_at
        assert monitor._raw_persisted_count == raw_count
        assert monitor._last_bid_signature == old_signature


def test_actual_six_second_timeout_and_new_fast_epoch_recovers(rig):
    rig.behavior = 'slow'
    prepare(rig)
    started = rig.monitor._full_job['started']
    entered(rig)
    drain(rig)
    assert 6 <= time.monotonic() - started < 7.5
    assert rig.monitor._full_status['stage'] == 'full_timeout'
    rig.monitor._maybe_run_idle_full_upgrade()
    assert rig.monitor._full_job is None
    rig.behavior = 'normal'
    rig.monitor.accept_row(_action(17))
    rig.monitor._process_snapshot(force=False)
    rig.monitor._last_packet_at = time.monotonic()-2
    rig.monitor._maybe_run_idle_full_upgrade()
    drain(rig)
    assert rig.monitor._full_status['stage'] == 'full_completed'


@pytest.mark.parametrize('behavior,expected', [('crash','full_child_failed'), ('wrong-session','full_result_invalid'),
                                             ('partial','full_partial'), ('error','full_compute_error'),
                                             ('large-result','full_child_failed')])
def test_child_failure_never_commits(rig, behavior, expected):
    rig.behavior = behavior
    prepare(rig)
    drain(rig)
    assert rig.monitor._full_status['stage'] == expected
    assert rig.monitor._last_full_processed_count == 0
    assert not rig.monitor.raw_path.exists()


def test_real_worker_preempts_entered_full_then_obeys_p2(rig):
    rig.behavior = 'slow'
    prepare(rig)
    entered(rig)
    rig.monitor._worker.start()
    before = time.monotonic()
    rig.monitor.accept_row(_action(19))
    until = before + 4
    while len(rig.builds) < 2 and time.monotonic() < until:
        time.sleep(.01)
    assert len(rig.builds) == 2
    assert time.monotonic() - before >= 1.0
    assert rig.process.poll() is not None
    assert rig.monitor._last_full_processed_count == 0
    assert rig.monitor._full_status['reaped']


@pytest.mark.parametrize('delta,expected', [(-.001, 'full_completed'), (0, 'full_stale'), (.001, 'full_stale')])
def test_commit_deadline_boundary(rig, monkeypatch, delta, expected):
    prepare(rig)
    job = rig.monitor._full_job
    job['artifact'] = {'session_id':job['signature'][0], 'phase':'bidding', 'round':1}
    monkeypatch.setattr(rig.module, 'time', SimpleNamespace(
        **(vars(time) | {'monotonic':lambda:job['deadline']+delta})))
    rig.monitor._commit_full_job(job)
    assert rig.monitor._full_status['stage'] == expected
    assert rig.monitor._last_full_processed_count == (2 if delta < 0 else 0)


@pytest.mark.parametrize('bad', ['identity', 'digest', 'session', 'duplicate', 'channel'])
def test_late_or_wrong_response_has_no_commit(rig, bad):
    prepare(rig)
    job = rig.monitor._full_job
    job['input_digest'] = 'bound-input'
    response = {'identity':job['identity'], 'input_digest':'bound-input',
                'artifact':{'session_id':job['signature'][0], 'phase':'bidding', 'round':1}}
    if bad == 'identity':
        response['identity'] = ('wrong-generation',)
    elif bad == 'digest':
        response['input_digest'] = 'wrong'
    elif bad == 'session':
        response['artifact']['session_id'] = 'wrong'
    elif bad == 'duplicate':
        job['invalid'] = 'full_cancelled'
    else:
        response = None
    job['phase'] = 'receiving'
    job['decoder'] = iter([response])
    rig.monitor._supervise_full_job()
    assert rig.monitor._full_job is None
    assert rig.monitor._last_full_processed_count == 0
    assert not rig.monitor.raw_path.exists()


def test_start_failure_does_not_retry_or_mark_full(rig, monkeypatch):
    def failed(job):
        raise OSError('test start failure')
    monkeypatch.setattr(rig.monitor, '_start_full_process', failed)
    prepare(rig)
    drain(rig)
    assert rig.monitor._full_status['stage'] == 'full_start_failed'
    rig.monitor._maybe_run_idle_full_upgrade()
    assert rig.monitor._full_job is None
    assert rig.monitor._last_full_processed_count == 0


@pytest.mark.parametrize('kill_succeeds', [False, True])
def test_terminate_kill_reap_failure_retains_owner(rig, kill_succeeds):
    prepare(rig)
    class Owned:
        pid = 123
        returncode = None
        calls = []
        def poll(self): return self.returncode
        def terminate(self): self.calls.append('terminate')
        def wait(self, timeout):
            self.calls.append('wait')
            if self.returncode is None:
                raise subprocess.TimeoutExpired('owned-test', timeout)
        def kill(self):
            self.calls.append('kill')
            if kill_succeeds:
                self.returncode = -9
    job = rig.monitor._full_job
    child = Owned()
    job['process'] = child
    assert rig.monitor._end_full_job(job, 'full_timeout') is kill_succeeds
    assert child.calls == ['terminate','wait','kill','wait']
    if not kill_succeeds:
        assert rig.monitor._full_job is job
        before = len(rig.builds)
        rig.monitor.accept_row(_action(33))
        rig.monitor._process_if_due()
        rig.monitor._process_snapshot(force=False)
        assert len(rig.builds) == before
        assert rig.monitor._full_status['stage'] == 'full_reap_failed'
        child.returncode = -9
        rig.monitor._supervise_full_job()
        assert rig.monitor._full_job is None


def test_preparation_and_channel_sizes_are_bounded(rig, monkeypatch):
    prepare(rig)
    job = rig.monitor._full_job
    job['encoder'] = iter([b'x' * 100])
    job['offset'] = engine._FULL_WIRE_CAPACITY - 10
    rig.monitor._supervise_full_job()
    assert rig.monitor._full_status['stage'] == 'full_input_size'
    assert rig.process is None
    with pytest.raises(ValueError, match='full_channel_size'):
        list(engine._full_mmap_tokens(None, engine._FULL_WIRE_CAPACITY))


def test_preparation_deadline_is_not_reset_by_progress(rig, monkeypatch):
    prepare(rig)
    job = rig.monitor._full_job
    clock = SimpleNamespace(value=job['started'])
    original_deadline = job['deadline']
    monkeypatch.setattr(rig.module, 'time', SimpleNamespace(**(vars(time) | {'monotonic':lambda:clock.value})))
    def preparation():
        while True:
            clock.value += 1
            yield b'["value",1]\n'
    job['encoder'] = preparation()
    for _ in range(8):
        if rig.monitor._full_job is not None:
            rig.monitor._supervise_full_job()
    assert job['deadline'] == original_deadline
    assert rig.monitor._full_status['stage'] == 'full_timeout'
    assert rig.process is None


def test_child_bound_version_never_reads_disk_and_keeps_conflict(monkeypatch):
    from bidking_lab.live import version_check
    tables = engine.MonitorTables({}, {}, {}, warehouse_live_identity_check=True,
                                  warehouse_game_version='v1')
    tables.warehouse_session_versions.resolve('s', 'v1')
    monkeypatch.setattr(version_check, 'detect_game_version_strict_disk_snapshot',
                        lambda *args: SimpleNamespace(value='v2'))
    snapshot = engine._full_tables_snapshot(tables, 's')
    rebuilt = engine._full_tables_restore(roundtrip(snapshot))
    def forbidden(*args):
        raise AssertionError('child attempted version discovery')
    monkeypatch.setattr(version_check, 'detect_game_version_strict_disk_snapshot', forbidden)
    assert engine._fresh_session_game_version(rebuilt, 's') == ('v2', False)
    assert engine._fresh_session_game_version(rebuilt, 'other') == ('', False)
    assert rebuilt.warehouse_session_versions.first_by_session == {'s':'v1'}
    assert rebuilt.warehouse_session_versions.conflicted_sessions == {'s'}


def test_changed_tables_and_version_reject_commit(rig):
    prepare(rig)
    job = rig.monitor._full_job
    job['artifact'] = {'session_id':job['signature'][0], 'phase':'bidding', 'round':1}
    rig.monitor.tables = replace(rig.monitor.tables, warehouse_game_version='changed')
    rig.monitor._commit_full_job(job)
    assert rig.monitor._full_status['stage'] == 'full_stale'
    assert not rig.monitor.raw_path.exists()


def test_reservation_rechecks_new_pending_in_gate_gap(rig, monkeypatch):
    prepare(rig)
    rig.monitor._end_full_job(rig.monitor._full_job, 'test_initial_attempt')
    rig.monitor._full_attempted_epoch = None
    rig.monitor.accept_row(_action(31))
    anchor = rig.monitor._pending_semantic_anchor
    rig.monitor._dispatch_idle_full()
    assert rig.monitor._full_job is None
    assert rig.monitor._pending_semantic_anchor == anchor


@pytest.mark.parametrize('stage', ['dispatch', 'commit'])
def test_parent_authority_gate_is_kept(rig, monkeypatch, stage):
    from bidking_lab.secure import enforce
    if stage == 'commit':
        prepare(rig)
    monkeypatch.setattr(enforce, 'current', lambda: object())
    monkeypatch.setattr(enforce, 'should_run_full', lambda: False)
    if stage == 'dispatch':
        rig.monitor.accept_row(_state())
        rig.monitor.accept_row(_action())
        # An existing valid fast epoch is the fixture input to the dispatch gate.
        rig.monitor._last_processed_count = 2
        rig.monitor._last_semantic_signature = rig.monitor._last_probed_semantic_signature
        rig.monitor._pending_semantic_anchor = None
        rig.monitor._last_packet_at = time.monotonic()-2
        rig.monitor._dispatch_idle_full()
    else:
        rig.monitor._commit_full_job(rig.monitor._full_job)
    assert rig.monitor._full_status['stage'] == 'full_gated'
    assert rig.monitor._last_full_processed_count == 0
    assert rig.process is None


def test_stop_tail_keeps_fast_settlement_and_never_reopens_full(rig, monkeypatch):
    rig.behavior = 'slow'
    prepare(rig)
    entered(rig)
    rig.monitor.accept_row(_action(34))
    calls = []
    def build(payload, **kwargs):
        assert kwargs['n_trials'] == 20
        signature = rig.module._semantic_signature_from_payload(payload)
        return {'session_id':signature[0], 'phase':'settled', 'round':1,
                'truth_from_parser_seam':123}
    def write(artifact, **kwargs):
        calls.append((artifact['snapshot_mode'], artifact['phase'], kwargs['append_logs']))
    monkeypatch.setattr(rig.module, 'build_monitor_artifact_from_payload', build)
    monkeypatch.setattr(rig.module, 'write_monitor_logs', write)
    rig.monitor._stop.set()
    rig.monitor._run_worker()
    assert calls == [('fast', 'settled', True)]
    assert rig.monitor._last_processed_count == 3
    assert rig.monitor._raw_persisted_count == 3
    assert rig.monitor._last_full_processed_count == 0
    assert rig.process.poll() is not None
    assert rig.monitor._full_job is None


@pytest.mark.parametrize('main', ['run_wss_live_monitor.py', 'run_npcap_live_monitor.py',
                                 'run_windivert_live_monitor.py', 'local_compat_monitor.py'])
def test_launcher_argv_does_not_replay_main(rig, monkeypatch, main):
    seen = []
    monkeypatch.setattr(sys, 'argv', [main, '--must-not-run-capture'])
    monkeypatch.setattr(rig.module.subprocess, 'Popen', lambda command, **kwargs: seen.append((command, kwargs)))
    rig.module.FatbeansWebhookMonitor._start_full_process(rig.monitor, {'channel_name':'owned-name'})
    command, kwargs = seen[0]
    assert command[1:3] == ['-B','-c']
    assert main not in command[3]
    assert '_full_artifact_child' in command[3]
    assert command[-1] == 'owned-name'
    assert kwargs['creationflags'] & subprocess.CREATE_NO_WINDOW


@pytest.mark.parametrize('main_name,expected', [('__main__', True), ('__mp_main__', False)])
def test_generated_monitor_shell_has_one_authority(tmp_path, main_name, expected):
    builder = Path(__file__).resolve().parents[1]/'scripts/build_live_portable.ps1'
    text = builder.read_text(encoding='utf-8-sig')
    match = re.search(r"\$monitorBootstrap = @'\r?\n(.*?)\r?\n'@", text, re.S)
    assert match
    shell = match.group(1)+'\n'
    assert len(shell.encode()) <= 256
    (tmp_path/'local_compat_monitor.py').write_text(shell, encoding='utf-8')
    marker = tmp_path/'authority-count.txt'
    (tmp_path/'_local_compat_monitor.py').write_text(
        'from pathlib import Path\ndef main():\n    Path('+repr(str(marker))+').write_text("one")\n    return 0\n',
        encoding='utf-8')
    code = 'import runpy;runpy.run_path("local_compat_monitor.py", run_name='+repr(main_name)+')'
    result = subprocess.run([sys.executable,'-B','-c',code], cwd=tmp_path,
                            timeout=5, capture_output=True,
                            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    assert result.returncode == 0, result.stderr
    assert marker.exists() is expected


def test_source_launcher_runs_protected_child_without_capture_main(rig, monkeypatch):
    # Malformed wire input exercises the real launcher/protected entry up to the
    # parser's error, without invoking numerical fallback or any capture main.
    real = rig.module.FatbeansWebhookMonitor._start_full_process
    def start(job):
        rig.process = real(rig.monitor, job)
        return rig.process
    monkeypatch.setattr(rig.monitor, '_start_full_process', start)
    prepare(rig)
    rig.monitor._full_job['rows'][:] = [{'Data': '!', 'Direct':'REV', 'SortID':1}]
    drain(rig)
    assert rig.process.poll() is not None
    assert rig.monitor._full_status['stage'] in ('full_input_error', 'full_partial', 'full_result_invalid')
    assert not rig.marker.exists()
    assert not rig.monitor.raw_path.exists()


def assembled_tables(tmp_path, monkeypatch):
    from bidking_lab.live import version_check
    source = Path(__file__).resolve().parents[1]
    receipt = json.loads(Path('C:/example/bidking-workspace/scratch/bidking_035_refresh_full_budget_source_20260906_01/CENTRAL_INPUTS.json').read_text())
    for suffix in ('01','02'):
        addendum = json.loads(Path('C:/example/bidking-workspace/scratch/bidking_035_refresh_full_budget_source_20260906_01',
                                 'CENTRAL_INPUTS_ADDENDUM_' + suffix + '.json').read_text())
        receipt['files'].update(addendum['files'])
    def require(path, identity):
        raw = path.read_bytes()
        if len(raw) != identity['bytes'] or hashlib.sha256(raw).hexdigest() != identity['sha256']:
            raise ValueError('input assembly failure')
    for relative, identity in receipt['files'].items():
        require(source/relative, identity)
        destination = tmp_path/relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source/relative, destination)
    monkeypatch.setattr(version_check, 'detect_game_version_strict_disk_snapshot',
                        lambda *args: SimpleNamespace(value='synthetic-v', path=tmp_path/'version'))
    tables = engine.load_monitor_tables(tables_dir=tmp_path/'data/raw/tables')
    assert tables.rankmap_distribution_status == 'ok'
    return tables, receipt, require


def test_real_tables_roundtrip_with_exact_named_input_gate(tmp_path, monkeypatch):
    tables, receipt, require = assembled_tables(tmp_path, monkeypatch)
    assert len(tables.maps) > 200 and len(tables.items) > 1400
    snapshot = engine._full_tables_snapshot(tables, 'real-tables-test')
    started = time.monotonic()
    tokens = list(engine._full_wire_tokens(snapshot))
    assert sum(map(len, tokens)) < engine._FULL_WIRE_CAPACITY
    rebuilt = engine._full_tables_restore(roundtrip(snapshot))
    assert type(rebuilt.drops) is type(tables.drops)
    assert rebuilt.maps == tables.maps and rebuilt.items == tables.items
    assert rebuilt.rankmap_distribution_receipt == tables.rankmap_distribution_receipt
    assert rebuilt.warehouse_package_generation == tables.warehouse_package_generation
    print(json.dumps({'table_wire_bytes':sum(map(len,tokens)), 'table_tokens':len(tokens),
                      'roundtrip_seconds':time.monotonic()-started,
                      'rankmap_status':tables.rankmap_distribution_status}))
    first, identity = next(iter(receipt['files'].items()))
    with (tmp_path/first).open('ab') as stream:
        stream.write(b'wrong-input-positive-control')
    with pytest.raises(ValueError, match='input assembly failure'):
        require(tmp_path/first, identity)
    with pytest.raises(FileNotFoundError):
        require(tmp_path/'missing', identity)


def test_real_tables_actual_child_numeric_equivalence(rig, monkeypatch):
    tables, _, _ = assembled_tables(rig.path/'package', monkeypatch)
    # Explicit synthetic valid-generation identity, not a Steam/runtime claim.
    # The separate table test retains live conflict-state transport coverage.
    tables = replace(tables, warehouse_game_version=tables.warehouse_package_generation,
                     warehouse_live_identity_check=False)
    monkeypatch.setenv('BIDKING_PROJECT_ROOT', str(rig.path/'package'))
    rig.monitor.tables = tables
    real = rig.module.FatbeansWebhookMonitor._start_full_process
    def start(job):
        rig.process = real(rig.monitor, job)
        return rig.process
    monkeypatch.setattr(rig.monitor, '_start_full_process', start)
    from test_live_fatbeans import _fatbeans_row, _rev_state_with_bids_frame, _player_bid_block
    # 2101 belongs to CURRENT_RANKMAP_MAP_IDS; 2501 uses the legacy Drop
    # traversal and its pre-existing missing leaf is not a qualified positive.
    session = '2101:ref-p1-numeric'
    row = _fatbeans_row('REV', _rev_state_with_bids_frame(
        session=session, map_id=2101, round_no=1,
        bids=(_player_bid_block(player_id=9, name='Synthetic', hero_id=104, value=200000),)), sort_id=1)
    row['CaptureTime'] = '2026-09-06 12:00:00.000'
    rig.monitor._known_local_player_id = 9
    rig.monitor.accept_row(row)
    rig.monitor._process_snapshot(force=False)
    params = dict(file=rig.monitor.raw_path.name, n_trials=500, roi_trials=0,
                  shadow_trials=20, run_debug_shadows=False, seed=1,
                  formal_mode='v3_practical', local_player_id_hint=rig.monitor._known_local_player_id)
    reference = engine.build_monitor_artifact_from_payload(
        json.dumps(rig.monitor._rows), tables=tables, **params)
    assert reference['formal_mode_reason'] != 'no_inference_session'
    assert reference['model_map_id'] == 2101
    assert reference['rankmap_distribution_support']['model_distribution_authority']
    assert reference['rankmap_distribution_support']['official_distribution_authority'] is False
    assert reference['v2_posterior_rows'] and reference['map_rows']
    rig.monitor._last_packet_at = time.monotonic()-2
    rig.monitor._maybe_run_idle_full_upgrade()
    job = rig.monitor._full_job
    assert job is not None
    drain(rig)
    assert rig.monitor._full_status['stage'] == 'full_completed', rig.monitor._full_status
    actual = job['artifact']
    # Publication metadata is added by the unchanged parent success tail.
    excluded_clock_paths = [('created_at',), ('processing_seconds',),
                            ('ui_contract','source','created_at'),
                            ('ui_contract','source','processing_seconds'),
                            ('ui_contract','diagnostics','sampling','processing_seconds')]
    def without_clocks(value):
        value = copy.deepcopy(value)
        for path in excluded_clock_paths:
            target = value
            for key in path[:-1]:
                target = target[key]
            assert path[-1] in target
            del target[path[-1]]
        return value
    common = without_clocks({key:value for key,value in actual.items() if key in reference})
    expected = without_clocks(reference)
    assert common == expected
    assert reference.get('n_trials') == 500
    print(json.dumps({'actual_full_phases':rig.monitor._full_status,
                      'reference_keys':len(reference),
                      'excluded_generated_clock_paths':excluded_clock_paths,
                      'numerical_population':'synthetic state and valid generation identity, real tables/algorithm'}))


def _f01_entered_settlement(rig, monkeypatch):
    rig.behavior = 'slow'
    prepare(rig)
    entered(rig)
    monitor = rig.monitor
    monitor.accept_row(_action(41))
    order = []
    close = monitor._fact_receipts.close
    finish = monitor._finish_compute_trace
    def build(payload, **kwargs):
        assert kwargs['n_trials'] == 20
        return {'session_id':rig.module._semantic_signature_from_payload(payload)[0],
                'phase':'settled','round':2,'synthetic_settlement_truth':314159}
    def write(artifact, **kwargs):
        assert rig.process.poll() is not None
        assert kwargs['append_logs'] is True
        assert artifact['snapshot_mode'] == 'fast'
        order.append('settlement')
        rig.module._atomic_write_json(rig.path/'logs/latest_snapshot.json', artifact)
    def closed():
        order.append('writer_close')
        close()
    def finished(event, **kwargs):
        if event == 'stopped':
            order.append('stopped')
        finish(event, **kwargs)
    monkeypatch.setattr(rig.module,'build_monitor_artifact_from_payload',build)
    monkeypatch.setattr(rig.module,'write_monitor_logs',write)
    monkeypatch.setattr(monitor._fact_receipts,'close',closed)
    monkeypatch.setattr(monitor,'_finish_compute_trace',finished)
    return order


def _f01_cleanup(rig, original_reap):
    rig.monitor._reap_full_process = original_reap
    if rig.monitor._full_job is not None:
        original_reap(rig.monitor._full_job)
    wake = getattr(rig.monitor,'_shutdown_wake',None)
    if wake is not None:
        wake.set()
    if rig.monitor._worker.is_alive():
        rig.monitor._worker.join(3)


@pytest.mark.parametrize('failures',[0,1,3])
def test_f01_real_child_transient_stop_reap_keeps_tail_and_writer(rig,monkeypatch,failures):
    order = _f01_entered_settlement(rig,monkeypatch)
    original = rig.monitor._reap_full_process
    calls = []
    def transient(job):
        calls.append(time.monotonic())
        if len(calls) <= failures:
            return False
        return original(job)
    monkeypatch.setattr(rig.monitor,'_reap_full_process',transient)
    rig.monitor._stop.set()
    rig.monitor._worker.start()
    try:
        started=time.monotonic()
        rig.monitor.stop()
        print(json.dumps({'failures':failures,'calls':len(calls),'seconds':time.monotonic()-started,
                          'child_alive':rig.process.poll() is None,'worker_alive':rig.monitor._worker.is_alive(),
                          'order':order}))
        assert rig.process.poll() is not None
        assert order == ['settlement','writer_close','stopped']
        assert rig.monitor._last_processed_count == 3
        assert json.loads((rig.path/'logs/latest_snapshot.json').read_text())['synthetic_settlement_truth']==314159
        rig.monitor.stop()
        assert order == ['settlement','writer_close','stopped']
    finally:
        _f01_cleanup(rig,original)


@pytest.mark.parametrize('reset',[False,True])
def test_f01_persistent_failure_returns_pending_and_recovers(rig,monkeypatch,reset):
    order=_f01_entered_settlement(rig,monkeypatch)
    monitor=rig.monitor
    original=monitor._reap_full_process
    calls=[]
    def unreaped(job):
        calls.append(time.monotonic())
        return False
    monkeypatch.setattr(monitor,'_reap_full_process',unreaped)
    monitor._stop.set();monitor._worker.start()
    try:
        started=time.monotonic()
        assert monitor.stop() is False
        elapsed=time.monotonic()-started
        assert 4.8 <= elapsed < 5.8
        assert monitor._worker.is_alive() and monitor._worker.daemon is False
        assert rig.process.poll() is None and monitor._full_job is not None
        assert monitor.status()['shutdown']=={'stage':'reap_pending','complete':False}
        assert order==[]
        assert 10 <= len(calls) <= 30
        assert all(b-a >= .15 for a,b in zip(calls,calls[1:]))
        if reset:
            monitor.reset_rows()
        monkeypatch.setattr(monitor,'_reap_full_process',original)
        assert monitor.stop() is True
        assert not monitor._worker.is_alive() and rig.process.poll() is not None
        assert order==(['writer_close','stopped'] if reset else ['settlement','writer_close','stopped'])
        print(json.dumps({'persistent_stop_seconds':elapsed,'attempts':len(calls),'reset':reset,'final_order':order}))
    finally:
        _f01_cleanup(rig,original)


@pytest.mark.parametrize('stopping',[False,True])
def test_f01_real_handle_terminate_oserror_recovery(rig,monkeypatch,stopping):
    order=_f01_entered_settlement(rig,monkeypatch)
    monitor=rig.monitor;job=monitor._full_job;real=rig.process
    original=monitor._reap_full_process
    class Proxy:
        pid=real.pid
        @property
        def returncode(self):return real.returncode
        def poll(self):return real.poll()
        def terminate(self):raise OSError('injected temporary termination failure')
        def wait(self,timeout):return real.wait(timeout=timeout)
        def kill(self):return real.kill()
    job['process']=Proxy()
    try:
        if stopping:
            monitor._stop.set();monitor._worker.start()
            assert monitor.stop() is False
        else:
            monitor._supervise_full_job()
            monitor._process_if_due()
        assert real.poll() is None and monitor._full_job is job and order==[]
        job['process']=real
        if not stopping:
            monitor._supervise_full_job()
            monitor._process_snapshot(force=False)
            assert order==['settlement']
        assert monitor.stop() is True
        assert order==['settlement','writer_close','stopped']
        assert real.poll() is not None and not monitor._worker.is_alive()
    finally:
        job['process']=real
        _f01_cleanup(rig,original)


def test_f01_concurrent_stop_finalizes_once(rig,monkeypatch):
    order=_f01_entered_settlement(rig,monkeypatch)
    monitor=rig.monitor;original=monitor._reap_full_process
    attempts=[]
    def transient(job):
        attempts.append(threading.get_ident())
        return False if len(attempts)<=3 else original(job)
    monkeypatch.setattr(monitor,'_reap_full_process',transient)
    barrier=threading.Barrier(5);results=[]
    def stop():
        barrier.wait()
        results.append(monitor.stop())
    threads=[threading.Thread(target=stop) for _ in range(4)]
    try:
        for thread in threads:thread.start()
        barrier.wait()
        for thread in threads:thread.join(6)
        assert all(not thread.is_alive() for thread in threads)
        assert results==[True]*4
        assert len(set(attempts))==1
        assert order==['settlement','writer_close','stopped']
        assert monitor._shutdown_complete.is_set()
    finally:
        _f01_cleanup(rig,original)
        for thread in threads:thread.join(3)


def test_f01_writer_stays_open_while_fast_tail_is_still_running(rig,monkeypatch):
    order=_f01_entered_settlement(rig,monkeypatch)
    monitor=rig.monitor;original=monitor._reap_full_process
    build=rig.module.build_monitor_artifact_from_payload
    entered_tail=threading.Event();release=threading.Event()
    def delayed(*args,**kwargs):
        entered_tail.set()
        assert release.wait(8)
        return build(*args,**kwargs)
    monkeypatch.setattr(rig.module,'build_monitor_artifact_from_payload',delayed)
    monitor._stop.set();monitor._worker.start()
    try:
        assert monitor.stop() is False
        assert entered_tail.is_set() and monitor._worker.is_alive()
        assert rig.process.poll() is not None
        assert order==[]
        assert monitor.status()['shutdown']['stage']=='flushing_fast'
        release.set()
        assert monitor.stop() is True
        assert order==['settlement','writer_close','stopped']
    finally:
        release.set();_f01_cleanup(rig,original)


def test_f01_caller_return_does_not_abandon_nondaemon_owner(tmp_path):
    marker=tmp_path/'caller-returned.json'
    command=[sys.executable,'-B','-c',
             'from test_refresh_full_budget_0_3_5_child import run_f01_caller;run_f01_caller('+repr(str(tmp_path))+')']
    with (tmp_path/'caller.stdout').open('w') as stdout, (tmp_path/'caller.stderr').open('w') as stderr:
        process=subprocess.Popen(command,stdout=stdout,stderr=stderr,
                                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            deadline=time.monotonic()+9
            while not marker.exists() and process.poll() is None and time.monotonic()<deadline:
                time.sleep(.025)
            assert marker.exists(), (tmp_path/'caller.stderr').read_text()
            receipt=json.loads(marker.read_text())
            assert receipt['stop_result'] is False and receipt['worker_daemon'] is False
            assert process.poll() is None
            assert psutil.pid_exists(receipt['compute_pid'])
            assert not (tmp_path/'shutdown-order.json').exists()
            (tmp_path/'release-reap').write_text('recover')
            assert process.wait(timeout=5)==0
            assert not psutil.pid_exists(receipt['compute_pid'])
            assert json.loads((tmp_path/'shutdown-order.json').read_text())==['settlement','writer_close','stopped']
        finally:
            (tmp_path/'release-reap').write_text('recover')
            process.wait(timeout=6)
