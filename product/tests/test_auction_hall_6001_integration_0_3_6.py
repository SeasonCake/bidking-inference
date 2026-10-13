"""Current 6001 producer -> engine -> summary controls, not game replay."""
import copy
from pathlib import Path

import pytest

from test_activity_generation_0_3_6 import ROOT, GENERATION, engine, panel, snapshot
import auction_hall_6001_quote_model as hall
from bidking_lab.runtime.snapshot import ui_contract_from_artifact


def current(hero='ahmed', inputs=None):
    value = snapshot(hero, 6001, inputs)
    value.update(round=4, action_round=4, observed_round=4)
    value['ui_contract'] = ui_contract_from_artifact(value)
    return value


@pytest.mark.parametrize('hero', ['aisha', 'ahmed'])
def test_real_evidence_and_joint_total_are_preserved(hero):
    value = current(hero, {'total_count': 125})
    before = copy.deepcopy(value)
    result = engine.run_reference_engine(value).as_dict()
    assert result['source'] == hall.MODEL_VERSION, result['notes']
    assert 0 < result['conservative'] <= result['balanced'] <= result['aggressive']
    assert result['evidence']['total_count'] == result['total_count_estimate'] == 125
    assert result['total_count_error_band'] == [125, 125]
    assert result['total_count_error_std'] == 0
    meta = result['provisional_quote']
    assert meta['attempts'] == 512 and meta['guarantee_violations'] == 0
    assert meta['generation'] == GENERATION and meta['count_prior_used'] is False
    assert meta['current_training_claim'] is False and meta['official_distribution_authority'] is False
    assert not panel.activity_2511_provisional_quote_revoked(value, {'ahmed_ref': result})
    assert value == before


def test_unknown_n_uses_joint_range_and_explicit_weak_origin():
    result = engine.run_reference_engine(current()).as_dict()
    meta = result['provisional_quote']
    assert result['total_count_estimate'] == meta['total_count_range'][1] > 100
    assert result['total_count_error_band'] == [meta['total_count_range'][0], meta['total_count_range'][2]]
    assert result['total_count_error_std'] == meta['count_std'] > 0
    assert result['total_count_confidence'] == 'low'
    assert meta['count_prior_used'] and meta['count_prior']['train_sessions'] == 7


def fully_known():
    value = current(inputs={'total_count': 2, 'fixed_counts': {'q1': 0, 'q3': 0, 'q4': 0, 'q5': 0, 'q6': 2}})
    value['minimap_grid_items'] = [
        {'item_id': item, 'local_index': index, 'session_id': value['session_id']}
        for index, item in enumerate((1096015, 1916038))]
    value['ui_contract'] = ui_contract_from_artifact(value)
    return value


def test_actual_known_items_not_an_extra_warehouse_and_public_conflict_rejected():
    value = fully_known()
    result = engine.run_reference_engine(value).as_dict()
    assert result['value_p25'] == result['value_p50'] == result['value_p75'] == 7386410
    assert result['red_count_range'] == [2, 2, 2]
    assert result['provisional_quote']['observed_physical_count'] == 2
    assert result['quality_lock']['q6']['strength'] == 'exact'
    value['structured_ref_inputs']['quality_values'] = {'q6': 7386411}
    value['ui_contract'] = ui_contract_from_artifact(value)
    conflict = engine.run_reference_engine(value).as_dict()
    assert conflict['balanced'] is None
    assert panel._ref_adaptation_pending_text(conflict) == '公开信息存在冲突'


@pytest.mark.parametrize('field,bad', [('session_epoch', 1), ('phase', 'settled'),
                                     ('warehouse_game_version_session_stable', False), ('model_map_id', 2601)])
def test_new_source_state_revokes_warm_result(field, bad):
    value = current(inputs={'total_count': 125})
    result = engine.run_reference_engine(value).as_dict()
    value[field] = bad
    assert engine.run_reference_engine(value).balanced is None
    assert panel.activity_2511_provisional_quote_revoked(value, {'ahmed_ref': result})


def test_missing_warm_input_revokes_then_recovers_without_manual_clear(monkeypatch):
    value = current(inputs={'total_count': 125})
    result = engine.run_reference_engine(value).as_dict()
    original = engine._aisha_q5_total_cells_plaintext_bytes
    def missing(path):
        if Path(path).name == 'Item.txt':
            raise FileNotFoundError('synthetic unavailable model input')
        return original(path)
    monkeypatch.setattr(engine, '_aisha_q5_total_cells_plaintext_bytes', missing)
    assert engine.run_reference_engine(value).balanced is None
    assert panel.activity_2511_provisional_quote_revoked(value, {'ahmed_ref': result})
    monkeypatch.setattr(engine, '_aisha_q5_total_cells_plaintext_bytes', original)
    assert engine.run_reference_engine(value).balanced is not None


@pytest.mark.parametrize('guard', [engine._CURRENT_ACTIVITY_RAW_MAP_BLOCKED, True, [], None, 'unknown-marker'])
def test_raw_identity_guard_is_not_repaired_by_a_valid_projected_map(guard):
    value = current(inputs={'total_count': 125})
    result = engine.run_reference_engine(value).as_dict()
    value[engine._CURRENT_ACTIVITY_RAW_MAP_GUARD_KEY] = guard
    assert engine.run_reference_engine(value).balanced is None
    assert panel.activity_2511_provisional_quote_revoked(value, {'ahmed_ref': result})


@pytest.mark.parametrize('context', [[], 'invalid-context', True])
def test_real_summary_normalization_keeps_negative_raw_identity(context, tmp_path):
    value = current(inputs={'total_count': 125})
    old = engine.run_reference_engine(value).as_dict()
    value['ui_contract']['context'] = context
    prepared = engine.stabilize_reference_engine_external_snapshot(value)
    assert prepared[engine._CURRENT_ACTIVITY_RAW_MAP_GUARD_KEY] == engine._CURRENT_ACTIVITY_RAW_MAP_BLOCKED
    assert panel.summarize_snapshot(value, snapshot_path=tmp_path / 'synthetic-invalid.json')['ahmed_ref']['balanced'] is None
    assert panel.activity_2511_provisional_quote_revoked(prepared, {'ahmed_ref': old})


@pytest.mark.parametrize('key,bad', [('schema', 'old'), ('generation', '4ae819'), ('model_map_id', 2601), ('input_sha256', {})])
def test_displayed_metadata_cannot_claim_new_scope(key, bad):
    value = current(inputs={'total_count': 125})
    result = engine.run_reference_engine(value).as_dict()
    result['provisional_quote'][key] = bad
    assert panel.activity_2511_provisional_quote_revoked(value, {'ahmed_ref': result})


def test_bounded_exhaustion_is_not_mathematical_conflict(monkeypatch):
    value = current(inputs={'total_count': 126})
    engine._auction_hall_6001_current_cached_quote.cache_clear()
    monkeypatch.setattr(hall, 'posterior', lambda *_args, **_kwargs: {
        'status': 'computational_insufficient', 'reason': 'bounded_sampling_exhausted_for_constraints'})
    try:
        result = engine.run_reference_engine(value).as_dict()
        assert result['balanced'] is None and result['status'] == 'computational_insufficient'
        assert panel._ref_adaptation_pending_text(result) == '本局约束较强，暂未收敛'
    finally:
        engine._auction_hall_6001_current_cached_quote.cache_clear()


@pytest.mark.parametrize('hero', ['aisha', 'ahmed'])
def test_summary_uses_current_model_copy_not_retired_673_support(tmp_path, hero):
    value = current(hero, inputs={'total_count': 125})
    if hero == 'aisha':
        model = hall.load_model(lambda path: (ROOT / path).read_bytes())
        item_id = next(i for i in model.ordinary if model.items[i]['quality'] == 4)
        item = model.items[item_id]
        value['skill_reveal_rows'] = [{
            'hero_id': 103, 'skill_id': 1001031, 'round': 4, 'sort_id': 1, 'revealed_items': 1,
            'observed_items': [{'item_id': item_id, 'local_index': 0, 'runtime_id': 101,
                                'quality': 4, 'value': item['value'], 'cells': item['cells'],
                                'shape_code': item['shape_code'], 'session_id': value['session_id']}]}]
        value['ui_contract'] = ui_contract_from_artifact(value)
    summary = panel.summarize_snapshot(value, snapshot_path=tmp_path / 'synthetic-6001.json')
    assert summary['ahmed_ref'].get('source') == hall.MODEL_VERSION, summary['ahmed_ref']
    assert summary['ahmed_ref']['balanced'] is not None
    assert summary['quality_composition']['status'] != 'conflict'
    assert '673' not in str(summary)
    assert '当前藏品与两类保底' in str(summary)


def test_aisha_missing_skill_keeps_existing_readiness_rule(tmp_path):
    value = current('aisha', inputs={'total_count': 125})
    summary = panel.summarize_snapshot(value, snapshot_path=tmp_path / 'synthetic-pending-6001.json')
    assert summary['ahmed_ref']['balanced'] is None
    assert 'aisha_early_round_waiting' in summary['ahmed_ref']['notes']


def test_actual_monitor_producer_reaches_6001_model(monkeypatch):
    from bidking_lab.extract.bid_map_table import load_bid_map_table
    from bidking_lab.live import fatbeans as fb, monitor
    tables = monitor.MonitorTables(maps=load_bid_map_table(ROOT / 'data/raw/tables/BidMap.txt'), drops={}, items={},
        warehouse_package_generation=GENERATION, warehouse_game_version=GENERATION, warehouse_live_identity_check=True)
    state = fb.FatbeansStateEvent(sort_id=1, capture_time='2026-10-11T15:00:00+00:00', message_id=0x21,
        session_id='6001:123456789', map_id=6001, round_index=1,
        skill_reveals=(fb.FatbeansSkillReveal(skill_id=100204, hero_id=204, round_index=1),),
        public_infos=(fb.FatbeansPublicInfo(info_id=200017, map_id=6001, value=125, value_field=2),))
    monkeypatch.setattr(monitor, '_fresh_session_game_version', lambda *_: (GENERATION, True))
    artifact = monitor.build_monitor_artifact_from_events(
        fb.FatbeansCaptureEvents(packets=(), frames=(), sends=(), states=(state,), statuses=()),
        tables=tables, n_trials=1, roi_trials=1, run_debug_shadows=False)
    result = engine.run_reference_engine(artifact).as_dict()
    assert result['source'] == hall.MODEL_VERSION, result['notes']
    assert result['balanced'] is not None and result['total_count_estimate'] == 125
