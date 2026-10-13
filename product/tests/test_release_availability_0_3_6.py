"""Current modeled authority vs frozen historical availability contracts."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def gate():
    return load('availability_036_gate_test', 'scripts/check_hotfix3_real_drop_pool_quote_runtime.py')


@pytest.fixture(scope='module')
def audit():
    return load('availability_036_audit_test', 'scripts/audit_release_zip.py')


def model_tables(gate):
    receipt = dict(schema_version=1, status='current_rankmap_algorithm_prior',
        authority_mode='rankmap_drop_model_algorithm_prior', package_generation='76b7ca-2026-10-08-723',
        drop_or_dropmap_textasset=False, drop_or_dropmap_absence_is_not_a_blocker=True,
        claims_item_level_drop_graph=False, modeled_item_probabilities=True,
        model_distribution_authority=True, official_distribution_authority=False,
        runtime_open_claim=False, scope_map_count=112, scope='physical_map_conditioned_ordinary_and_ranked')
    drops = SimpleNamespace(package_generation=receipt['package_generation'], authority_mode=receipt['authority_mode'],
        bound_map_ids=gate.CURRENT_MODEL_MAP_IDS, bound_pool_ids=gate.CURRENT_MODEL_MAP_IDS)
    return SimpleNamespace(rankmap_distribution_receipt=receipt, rankmap_distribution_status='ok', drops=drops,
        maps={mid: SimpleNamespace(drop_pool_id=mid) for mid in gate.CURRENT_MODEL_MAP_IDS}, items={})


def test_literal_scope_identity_is_112_not_legacy_18(gate):
    assert len(gate.CURRENT_MODEL_MAP_IDS) == 112
    assert gate._ids_sha256(gate.CURRENT_MODEL_MAP_IDS) == '78bab49717c2a3884ea49e8452cc779544ce4ee8bad5dce49e513e5bbfb4c99d'
    assert {6001, 2511, 4511, 5506}.isdisjoint(gate.CURRENT_MODEL_MAP_IDS)
    assert gate._uses_current_model(model_tables(gate)) is True


@pytest.mark.parametrize('field,value', [
    ('scope_map_count', 18), ('scope_map_count', True), ('runtime_open_claim', True),
    ('model_distribution_authority', False), ('official_distribution_authority', True),
    ('drop_or_dropmap_textasset', True), ('claims_item_level_drop_graph', True),
    ('drop_or_dropmap_absence_is_not_a_blocker', False), ('authority_mode', 'official_drop'),
    ('scope', 'legacy_pool'), ('ranked_5606', 'modeled_empirically_calibrated'),
])
def test_current_receipt_cannot_borrow_old_or_official_claim(gate, field, value):
    tables = model_tables(gate)
    tables.rankmap_distribution_receipt[field] = value
    with pytest.raises(AssertionError, match='current_model_authority_contract_failed'):
        gate._uses_current_model(tables)


@pytest.mark.parametrize('mutation', ['missing_map', 'wrong_map_same_count', 'unbound', 'wrong_generation', 'unknown_generation'])
def test_bound_map_and_generation_are_not_declaration_only(gate, mutation):
    tables = model_tables(gate)
    if mutation == 'missing_map':
        tables.drops.bound_map_ids -= {5606}
    elif mutation == 'wrong_map_same_count':
        tables.drops.bound_map_ids = (tables.drops.bound_map_ids - {5606}) | {6001}
    elif mutation == 'unbound':
        tables.drops = {}
    elif mutation == 'wrong_generation':
        tables.drops.package_generation = 'b049c3-2026-09-10-597'
    else:
        tables.rankmap_distribution_receipt['package_generation'] = 'unrecognized'
    with pytest.raises(AssertionError):
        gate._uses_current_model(tables)


def fake_monitor_and_drop(gate, tables, *, bad_map=None):
    seen = []
    def flatten(pool_id, drops, items, *, map_id):
        seen.append((pool_id, map_id))
        assert pool_id == tables.maps[map_id].drop_pool_id
        return SimpleNamespace(item_ids=[1], resolution_status='conditional' if map_id == bad_map else 'complete',
            resolution_generation=tables.drops.package_generation, resolution_policy='rankmap_drop_model_algorithm_prior')
    def alias(mid, maps, drops):
        if 2531 <= mid <= 2540 or 4531 <= mid <= 4540:
            return {'source_map_id': mid, 'model_map_id': mid - 30}
        return {}
    monitor = SimpleNamespace(CURRENT_RANKMAP_MAP_IDS=set(range(5601, 5612)),
        _activity_shipwreck_alias=alias, _drop_pool_support_receipt=lambda *_args: {})
    return monitor, SimpleNamespace(flatten_pool=flatten), seen


def test_census_verifies_each_physical_map_and_pool_not_just_count(gate):
    tables = model_tables(gate)
    monitor, drop, seen = fake_monitor_and_drop(gate, tables)
    changed = {2511, 4511, 5201, 5202, 5505, 5506, *range(5601, 5611)}
    excluded = changed | {*range(2531, 2551), *range(4531, 4551), 5611, 6001} | (set(range(5501, 5511)) - {5505, 5506})
    engine = SimpleNamespace(UPDATE606_CHANGED_ROUTE_MAP_IDS=changed, _AISHA_Q5_TOTAL_CELLS_FORBIDDEN_WEIGHT_MAP_IDS=excluded)
    assert gate._policy_census(tables, monitor, engine, drop) == gate.CURRENT_MODEL_CENSUS
    assert len(seen) == 224 and {mid for _, mid in seen} == gate.CURRENT_MODEL_MAP_IDS
    _, bad, _ = fake_monitor_and_drop(gate, tables, bad_map=5606)
    with pytest.raises(AssertionError, match='current_model_route_not_sampleable'):
        gate._policy_census(tables, monitor, engine, bad)


def test_legacy_ranked_open_claim_stays_required_and_false_is_rejected(gate):
    tables = model_tables(gate)
    tables.rankmap_distribution_receipt = dict(status='current_rankmap_algorithm_prior',
        authority_mode='rankmap_drop_model_algorithm_prior', package_generation='b049c3-2026-09-10-597',
        scope_map_count=18, ranked_5606='modeled_empirically_calibrated', runtime_open_claim=True,
        chenmei_5506='pending_isolated', drop_or_dropmap_textasset=False, claims_item_level_drop_graph=False)
    tables.drops.package_generation = 'b049c3-2026-09-10-597'
    monitor, drop, seen = fake_monitor_and_drop(gate, tables)
    def summary(snapshot, **kwargs):
        support = snapshot['rankmap_distribution_support']
        return {'status': 'ok', 'ahmed_ref': {'status': 'ok', 'conservative': 1, 'balanced': 2, 'aggressive': 3},
                'flags': [], 'rankmap_distribution_support': support, 'diagnostics': {'rankmap_distribution_support': support}}
    panel = SimpleNamespace(summarize_snapshot=summary)
    assert gate._uses_current_model(tables) is False
    gate._check_current_ranked_authority(tables=tables, monitor=monitor, panel=panel, drop_engine=drop)
    assert {mid for _, mid in seen} == set(range(5601, 5612))
    tables.rankmap_distribution_receipt['runtime_open_claim'] = False
    with pytest.raises(AssertionError, match='current_ranked_authority_contract_failed'):
        gate._check_current_ranked_authority(tables=tables, monitor=monitor, panel=panel, drop_engine=drop)


def aggregate_receipt(availability):
    return {'schema': 'bidking_release_availability_receipt_v1', 'receipt_version': 1, 'status': 'pass',
        'captures': [{**row, 'verdict': 'pass'} for row in availability['captures']],
        'stage': {'identity_sha256': 'a'*64, 'verdict': 'pass'},
        'extracted_zip': {'identity_sha256': 'a'*64, 'verdict': 'pass'},
        'policy_census': {**copy.deepcopy(availability['policy_census']), 'verdict': 'pass'},
        'route_scope': {key: availability['route_scope'][key] for key in
            ('changed_route_count','new_only_count','legacy_only_count','forbidden_count','current_activity_alias_sampleable_count','ranked_production_unsupported_count')} | {'verdict': 'pass'},
        'chains': {key: 'pass' for key in ('accepted_scheduled_snapshot','summary_api_ui','current_authority_2409')}}


@pytest.mark.parametrize('legacy', [False, True])
def test_auditor_retains_explicit_old_and_new_contracts(audit, tmp_path, legacy):
    contract = json.loads((ROOT / 'scripts/release_zip_contract.json').read_text('utf-8'))
    selected = contract['release_availability']
    if legacy:
        selected['current_authority'] = {key: selected['current_authority'][key] for key in ('map_id','canonical_input_sha256')}
        selected['policy_census'] = copy.deepcopy(audit.EXPECTED_RELEASE_AVAILABILITY_CENSUS)
        selected['route_scope'] = copy.deepcopy(audit.EXPECTED_RELEASE_AVAILABILITY_ROUTE_SCOPE)
    assert audit._availability_uses_current_model(selected) is (not legacy)
    path = tmp_path / 'contract.json'
    path.write_text(json.dumps(contract), 'utf-8')
    receipt = aggregate_receipt(selected)
    assert audit._validate_release_availability_receipt(receipt, path) == receipt
    receipt['policy_census']['direct_root']['sampleable'] += 1
    with pytest.raises(ValueError, match='release_availability_census_invalid'):
        audit._validate_release_availability_receipt(receipt, path)


@pytest.mark.parametrize('mutation', ['old_census', 'old_route', 'official', 'open', 'live_claim', 'bool_count', 'unknown'])
def test_auditor_refuses_mixed_authority_and_evidence_claims(audit, tmp_path, mutation):
    contract = json.loads((ROOT / 'scripts/release_zip_contract.json').read_text('utf-8'))
    a = contract['release_availability']
    if mutation == 'old_census':
        a['policy_census'] = copy.deepcopy(audit.EXPECTED_RELEASE_AVAILABILITY_CENSUS)
    elif mutation == 'old_route':
        a['route_scope'] = copy.deepcopy(audit.EXPECTED_RELEASE_AVAILABILITY_ROUTE_SCOPE)
    elif mutation == 'official':
        a['current_authority']['distribution_authority'] = 'official_drop'
    elif mutation == 'open':
        a['current_authority']['runtime_open_claim'] = True
    elif mutation == 'live_claim':
        a['current_authority']['historical_capture_scope'] = 'current_generation_live'
    elif mutation == 'bool_count':
        a['policy_census']['direct_root']['degraded'] = False
    else:
        a['current_authority']['package_generation'] = 'unrecognized'
    path = tmp_path / 'contract.json'
    path.write_text(json.dumps(contract), 'utf-8')
    with pytest.raises(ValueError, match='release_availability_contract_invalid'):
        audit._validate_release_availability_receipt(aggregate_receipt(a), path)
