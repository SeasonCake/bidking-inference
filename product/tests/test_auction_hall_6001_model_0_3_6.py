"""Bounded deterministic owner controls; no actual game or engine replay."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str((ROOT/'hero_ref/src').resolve()))
import auction_hall_6001_quote_model as hall
from bidking_lab.extract.bid_map_table import load_bid_map_table
from bidking_lab.live.warehouse_source import classify_from_map_table


@pytest.fixture(scope='module')
def model():
    return hall.load_model(lambda p:(ROOT/p).read_bytes())


@pytest.fixture
def snapshot():
    maps=load_bid_map_table(ROOT/'data/raw/tables/BidMap.txt')
    source=classify_from_map_table(6001,maps,source_generation=hall.GENERATION,
        profile_generation=hall.GENERATION,game_version=hall.GENERATION,
        owner_evidence_status='absent').as_dict()
    value=dict(map_id=6001,model_map_id=6001,session_id='6001:123456789',
        epoch=123456789,phase='bidding',round=4,warehouse_source=source,
        warehouse_source_generation=hall.GENERATION,warehouse_game_version_session_stable=True)
    value['ui_contract']=dict(context=dict(map_id=6001,session_id=value['session_id'],phase='bidding'))
    return value


def e(n=None,**kwargs):
    return dict(map_id=6001,phase='bidding',total_count=n,**kwargs)


def obj(item_id,index=0,**kwargs):
    return dict(dict(item_id=item_id,local_index=index,session_id='6001:123456789'),**kwargs)


def run(model,snapshot,evidence,observed=(),draws=128,**kwargs):
    return hall.posterior(model,evidence,observed,snapshot=snapshot,draws=draws,**kwargs)


def test_exact_inputs_and_curve_quality_mass(model):
    assert len(model.ordinary)==675 and 1006001 not in model.ordinary
    assert not set(model.ordinary)&(set(hall.EVENT_IDS)|set(hall.SEASON_IDS))
    assert len(hall.EVENT_IDS)==18 and len(hall.SEASON_IDS)==27
    assert sum(model.ordinary_p)==pytest.approx(1,abs=2e-11)
    for j,q in enumerate(hall.QUALITY_KEYS):
        total=sum(p for i,p in zip(model.ordinary,model.ordinary_p) if hall._quality(i,model)==q)
        assert total==pytest.approx(model.quality_mass[j],abs=2e-11)
    assert sum(model.event_p)==pytest.approx(1) and sum(model.season_p)==pytest.approx(1)
    with pytest.raises(TypeError):
        model.items[hall.EVENT_IDS[0]]['value']=0


def test_real_current_classifier_exact_allowed_tuple(snapshot):
    assert hall.scope_state(snapshot)=='ok'
    for key,value in hall.SOURCE_CLASSIFICATION.items():
        assert type(snapshot['warehouse_source'][key]) is type(value)
        assert snapshot['warehouse_source'][key]==value
    assert snapshot['warehouse_source']['source_conflict'] is True
    assert snapshot['warehouse_source']['official_distribution_eligible'] is False


@pytest.mark.parametrize('field,bad',[
    ('reason','pw2_sealed_owner_block_absent'),('qualification','player_custom_warehouse'),
    ('explicit_source','player'),('owner_evidence_status','present'),('config',{}),
    ('game_version','b049c3-2026-09-10-597'),('map_table_build','25221911'),
    ('map_table_sha256','x'),('expected_map_table_sha256','x'),
    ('map_source_contract_sha256','x'),('source_conflict',False),
    ('quarantine_required',False),('official_distribution_eligible',True),
    ('auction_mode','sealed'),('map_id',5215)])
def test_non_distribution_exception_never_broadly_allows_conflict(snapshot,model,field,bad):
    snapshot['warehouse_source'][field]=bad
    assert hall.scope_state(snapshot)!='ok'
    with pytest.raises(hall.ModelInputError,match='scope:'):
        run(model,snapshot,e(125))


@pytest.mark.parametrize('field,bad',[
    ('epoch',1),('session_epoch','123456788'),('session_epoch','1'*5000),
    ('session_id','6001:0123456789'),('session_id','5215:123456789'),
    ('session_id','6001:0'),('map_id',5215),('model_map_id',2601),
    ('map_alias_mode','economic'),('warehouse_source_generation','b049c3-2026-09-10-597'),
    ('warehouse_game_version_session_stable',False),('phase','settlement'),
    ('warehouse_owner_evidence_status','present')])
def test_scope_alias_generation_session_and_epoch(snapshot,model,field,bad):
    snapshot[field]=bad
    assert hall.scope_state(snapshot)!='ok'
    with pytest.raises(hall.ModelInputError,match='scope:'):
        run(model,snapshot,e(125))


@pytest.mark.parametrize('n',[2,31,125,512])
def test_real_n_not_drop_invocation_or_global_col3(model,snapshot,n):
    result=run(model,snapshot,e(n),draws=64)
    assert result['status']=='ok' and result['total_count_range']==[n]*3
    assert result['guarantee_violations']==0
    assert result['quality_count_ranges']['q6'][0]>=2
    assert result['count_prior_used'] is False


def test_unknown_n_weak_train_only_prior_not_col3(model,snapshot):
    result=run(model,snapshot,e(),draws=512)
    assert result['status']=='ok' and result['total_count_range'][1]>100
    assert result['count_prior_used'] is True
    assert result['count_prior']['train_sessions']==7
    assert result['count_prior']['source_qualification']=='unknown_or_conflicting'
    assert result['current_training_claim'] is False
    assert result['official_distribution_authority'] is False
    assert sum(result['quality_count_means'].values())==pytest.approx(result['count_mean'])
    assert sum(result['quality_cells_means'].values())==pytest.approx(result['cells_mean'])
    assert sum(result['quality_value_means'].values())==pytest.approx(result['value_mean'])


@pytest.mark.parametrize('n',[0,1])
def test_two_guarantees_reject_n_one(model,snapshot,n):
    with pytest.raises(hall.MathematicalConflict,match='two_guarantees'):
        run(model,snapshot,e(n))


@pytest.mark.parametrize('set_name',['EVENT_IDS','SEASON_IDS'])
def test_same_loader_detects_missing_guarantee_set(monkeypatch,set_name):
    monkeypatch.setattr(hall,set_name,())
    with pytest.raises(hall.ModelInputError,match='guarantee_set_identity'):
        hall.load_model(lambda p:(ROOT/p).read_bytes())


def test_known_objects_replace_two_guarantees_and_duplicates_merge(model,snapshot):
    a,b=hall.EVENT_IDS[0],hall.SEASON_IDS[0]
    snapshot['minimap_grid_items']=[obj(a,0,runtime_id=100),obj(b,1)]
    snapshot['ui_contract']['minimap']=dict(items=[obj(a,0),obj(b,1,runtime_id=101)])
    merged=hall.observations(snapshot)
    assert len(merged)==2
    result=run(model,snapshot,e(2),observed=merged,draws=16)
    expected=model.items[a]['value']+model.items[b]['value']
    assert result['value_range']==[expected]*3
    assert result['observed_physical_count']==2
    assert result['quality_count_ranges']['q6']==[2]*3
    assert result['total_grid_range']==[model.items[a]['cells']+model.items[b]['cells']]*3


def test_revealed_extra_guarantee_replaces_base_not_another_vault(model,snapshot):
    ids=[hall.EVENT_IDS[0],hall.EVENT_IDS[1],hall.SEASON_IDS[0]]
    observations=[obj(i,j) for j,i in enumerate(ids)]
    result=run(model,snapshot,e(3),observations,draws=16)
    assert result['value_range']==[sum(model.items[i]['value'] for i in ids)]*3
    assert result['total_count_range']==[3]*3
    with pytest.raises(hall.MathematicalConflict,match='missing_guarantees_exceed'):
        run(model,snapshot,e(2),observations[:2])


def test_foldfan_only_disclosed_not_unobserved_base(model,snapshot):
    assert 1006001 not in model.ordinary
    ids=[hall.EVENT_IDS[0],hall.SEASON_IDS[0],1006001]
    result=run(model,snapshot,e(3),[obj(i,j) for j,i in enumerate(ids)],draws=8)
    assert result['value_range']==[sum(model.items[i]['value'] for i in ids)]*3


def test_hard_quality_counts_cells_values_conserve_single_particle(model,snapshot):
    base=next(i for i in model.ordinary if model.items[i]['quality']==5)
    ids=[hall.EVENT_IDS[0],hall.SEASON_IDS[0],base]
    facts=[obj(i,j) for j,i in enumerate(ids)]
    counts=dict(q1=0,q3=0,q4=0,q5=1,q6=2)
    cells=dict(q5=model.items[base]['cells'],q6=sum(model.items[i]['cells'] for i in ids[:2]))
    values=dict(q5=model.items[base]['value'],q6=sum(model.items[i]['value'] for i in ids[:2]))
    evidence=e(3,fixed_counts=counts,quality_cells=cells,quality_values=values,
        total_grid_target=sum(cells.values()),avg_cells={'q5':cells['q5'],'q6':cells['q6']/2},count_sums={'q5q6':3})
    result=run(model,snapshot,evidence,facts,draws=16)
    assert result['value_range']==[sum(values.values())]*3
    assert result['total_grid_range']==[sum(cells.values())]*3
    assert sum(result['quality_count_ranges'][q][1] for q in hall.QUALITY_KEYS)==3
    assert sum(result['quality_cells_ranges'][q][1] for q in hall.QUALITY_KEYS)==sum(cells.values())
    assert run(model,snapshot,e(3,fixed_counts=counts,quality_cells={'q5':cells['q5']+1}),facts,draws=8)['status']=='computational_insufficient'


def test_fixed_red_count_conditioning_not_rare_rejection(model,snapshot):
    result=run(model,snapshot,e(125,fixed_counts=dict(q1=0,q3=0,q4=0,q5=0,q6=125)),draws=32)
    assert result['status']=='ok' and result['accepted']==32
    assert result['quality_count_ranges']['q6']==[125]*3


@pytest.mark.parametrize('inputs',[
    dict(fixed_counts={'q6':1}),dict(fixed_counts={'q1':124,'q6':2}),
    dict(fixed_counts=dict(q1=0,q3=0,q4=0,q5=0,q6=124)),
    dict(min_counts={'q5':125,'q6':2})])
def test_proved_hard_count_contradictions_are_not_sampling_failure(model,snapshot,inputs):
    with pytest.raises(hall.MathematicalConflict):
        run(model,snapshot,e(125,**inputs))


def test_insufficient_computation_is_not_mathematical_claim(model,snapshot):
    result=run(model,snapshot,e(125,total_grid_target=125),draws=1)
    assert result['status']=='computational_insufficient'
    assert result['reason']=='bounded_sampling_exhausted_for_constraints'
    assert result['value_range'] is None
    with pytest.raises(hall.MathematicalConflict,match='total_cells_outside'):
        run(model,snapshot,e(125,total_grid_target=10**7),draws=1)


def test_future_settlement_labels_are_never_model_inputs(model,snapshot):
    before=run(model,snapshot,e(125),draws=32)
    snapshot.update(final_quality_counts={'q6':999},inventory_count=1,known_value_sum=1,
        settlement_truth=dict(total_count=1,value=1))
    after=run(model,snapshot,e(125),draws=32)
    assert before==after


@pytest.mark.parametrize('mutation',[dict(session_id='6001:2'),dict(observed_round=5),dict(item_id=9999999)])
def test_wrong_session_future_and_unknown_object_do_not_enter(model,snapshot,mutation):
    fact=obj(hall.EVENT_IDS[0],**mutation) if 'item_id' not in mutation else dict(obj(hall.EVENT_IDS[0]),**mutation)
    with pytest.raises(hall.ModelInputError):
        run(model,snapshot,e(125),[fact],draws=1)


def test_partial_physical_domain_and_contradictory_catalog_fact(model,snapshot):
    a,b=hall.EVENT_IDS[:2]
    fact=dict(local_index=0,session_id='6001:123456789',candidate_item_ids=[a,b],quality=6)
    result=run(model,snapshot,e(2),[fact],draws=32)
    assert result['status']=='ok' and result['observed_physical_count']==1
    with pytest.raises(hall.MathematicalConflict,match='no_catalog_candidate'):
        run(model,snapshot,e(2),[obj(a,value=model.items[a]['value']+1)],draws=1)


@pytest.mark.parametrize('path',list(hall.INPUTS))
def test_warm_changed_or_missing_input_is_not_cached_success(path):
    hall.load_model(lambda p:(ROOT/p).read_bytes())
    with pytest.raises(hall.ModelInputError,match='model_input_identity'):
        hall.load_model(lambda p:b'{}' if p==path else (ROOT/p).read_bytes())
    def missing(p):
        if p==path:
            raise FileNotFoundError('synthetic missing')
        return (ROOT/p).read_bytes()
    with pytest.raises(hall.ModelInputError,match='model_input_missing'):
        hall.load_model(missing)


def test_deterministic_output_and_fresh_session_no_context_cache(model,snapshot):
    before=run(model,snapshot,e(125),draws=64)
    assert before==run(model,snapshot,e(125),draws=64)
    snapshot['session_id']='6001:2'; snapshot['epoch']=2
    snapshot['ui_contract']['context']['session_id']='6001:2'
    assert hall.scope_state(snapshot)=='ok'
    assert before==run(model,snapshot,e(125),draws=64)
    snapshot['warehouse_game_version_session_stable']=False
    with pytest.raises(hall.ModelInputError):
        run(model,snapshot,e(125),draws=1)


def test_runtime_local_bridge_merges_earlier_partial_views_not_two_slots(model,snapshot):
    a,b=hall.EVENT_IDS[0],hall.SEASON_IDS[0]
    snapshot['minimap_grid_items']=[dict(runtime_id=100,item_id=a),dict(local_index=0,cells=model.items[a]['cells']),
        dict(runtime_id=100,local_index=0,quality=6),obj(b,1)]
    merged=hall.observations(snapshot)
    assert len(merged)==2
    assert merged[0]['runtime_id']==100 and merged[0]['local_index']==0 and merged[0]['item_id']==a
    result=run(model,snapshot,e(2),draws=8)
    assert result['observed_physical_count']==2
    assert result['value_range']==[model.items[a]['value']+model.items[b]['value']]*3
    snapshot['minimap_grid_items'][1]['item_id']=hall.EVENT_IDS[1]
    with pytest.raises(hall.MathematicalConflict,match='physical_observation_conflict:item_id'):
        hall.observations(snapshot)


@pytest.mark.parametrize('container',[list,tuple])
def test_correlated_pair_forms_share_same_constraint_checker(model,snapshot,container):
    a,b=hall.EVENT_IDS[0],hall.SEASON_IDS[0]
    cells=model.items[a]['cells']+model.items[b]['cells']
    value=model.items[a]['value']+model.items[b]['value']
    evidence=e(2,quality_count_cells_options={'q6':container([container([2,cells])])},
        quality_count_cells_value_options={'q6':container([container([2,cells,value])])})
    result=run(model,snapshot,evidence,[obj(a),obj(b,1)],draws=8)
    assert result['status']=='ok' and result['value_range']==[value]*3
    evidence['quality_count_cells_options']['q6']=container([container([2,cells+1])])
    assert run(model,snapshot,evidence,[obj(a),obj(b,1)],draws=8)['status']=='computational_insufficient'


@pytest.mark.parametrize('field',['quality_values','hard_public_avg_values','avg_values'])
@pytest.mark.parametrize('count_context',['exact_quality','whole_known','unknown_other_quality','inferred_quality'])
def test_complete_revealed_quality_prices_equal_raw_sum_not_public_override(model,snapshot,field,count_context):
    a,b=hall.EVENT_IDS[-1],hall.SEASON_IDS[-1]
    rows=[obj(a),obj(b,1)]
    raw=model.items[a]['value']+model.items[b]['value']
    n=3 if count_context in ('unknown_other_quality','inferred_quality') else 2
    fixed={'q6':2} if count_context!='whole_known' else {}
    if count_context=='unknown_other_quality':
        fixed.update(q1=0,q3=0,q4=0,q5=1)
    elif count_context=='inferred_quality':
        fixed=dict(q1=0,q3=0,q4=0,q5=1)
    divisor=1 if field=='quality_values' else 2
    good=run(model,snapshot,e(n,fixed_counts=fixed,**{field:{'q6':raw/divisor}}),rows,draws=8)
    assert good['status']=='ok' and good['quality_value_ranges']['q6']==[raw]*3
    for delta in (-1,1):
        with pytest.raises(hall.MathematicalConflict):
            run(model,snapshot,e(n,fixed_counts=fixed,**{field:{'q6':(raw+delta)/divisor}}),rows,draws=8)


@pytest.mark.parametrize('field',['quality_values','hard_public_avg_values','avg_values'])
def test_unknown_same_quality_keeps_legal_public_value_override(model,snapshot,field):
    a,b=hall.EVENT_IDS[-1],hall.SEASON_IDS[-1]
    unknown=next(i for i in model.ordinary if model.items[i]['quality']==6)
    raw=model.items[a]['value']+model.items[b]['value']+model.items[unknown]['value']
    amount=raw if field=='quality_values' else raw/3
    result=run(model,snapshot,e(3,fixed_counts={'q6':3},**{field:{'q6':amount}}),[obj(a),obj(b,1)],draws=16)
    assert result['status']=='ok' and result['quality_value_ranges']['q6']==pytest.approx([raw]*3)


def test_soft_average_reweights_complete_quality_without_changing_physical_price(model,snapshot):
    a,b=hall.EVENT_IDS[-1],hall.SEASON_IDS[-1]
    raw=model.items[a]['value']+model.items[b]['value']
    result=run(model,snapshot,e(2,fixed_counts={'q6':2},avg_values={'q6':raw},soft_avg_value_keys={'q6'}),
        [obj(a),obj(b,1)],draws=8)
    assert result['status']=='ok' and result['quality_value_ranges']['q6']==[raw]*3


def test_non_singleton_priced_public_domains_can_complete_a_quality(model,snapshot):
    a,b=hall.EVENT_IDS[-1],hall.SEASON_IDS[-1]
    raw=model.items[a]['value']+model.items[b]['value']
    # The same known price/quality may leave identity ambiguous. Completeness
    # is about actual slots and prices, not only singleton item IDs.
    equal_pair=next((tuple(i for i in model.ordinary if model.items[i]['quality']==6 and model.items[i]['value']==model.items[j]['value'])
                    for j in model.ordinary if model.items[j]['quality']==6
                    and sum(model.items[i]['quality']==6 and model.items[i]['value']==model.items[j]['value'] for i in model.ordinary)>1),None)
    assert equal_pair is not None
    value=model.items[equal_pair[0]]['value']
    rows=[obj(a),obj(b,1),dict(local_index=2,session_id='6001:123456789',candidate_item_ids=list(equal_pair),quality=6,value=value)]
    with pytest.raises(hall.MathematicalConflict,match='complete_quality_public_value_conflict'):
        run(model,snapshot,e(3,fixed_counts={'q6':3},quality_values={'q6':raw+value+1}),rows,draws=8)


def test_quartiles_use_same_weighted_particles_and_median_as_safety_range(model,snapshot):
    result=run(model,snapshot,e(125),draws=128)
    assert result['status']=='ok' and result['accepted']==128
    assert result['range_quantiles']==[.1,.5,.9]
    low,median,high=result['value_range']
    q25,q50,q75=result['value_quartiles']
    assert low<=q25<=q50<=q75<=high
    assert q50==median


def test_count_std_uses_joint_particle_n_and_is_zero_for_exact_n(model,snapshot):
    exact=run(model,snapshot,e(125),draws=32)
    assert exact['count_mean']==pytest.approx(125)
    assert exact['count_std']==pytest.approx(0,abs=1e-10)
    uncertain=run(model,snapshot,e(),draws=128)
    assert uncertain['status']=='ok' and uncertain['count_std']>0
    assert uncertain['total_count_range'][0]<=uncertain['count_mean']<=uncertain['total_count_range'][2]
    assert sum(uncertain['quality_count_means'].values())==pytest.approx(uncertain['count_mean'])
