"""MEAN-SIZE/JOIN: exact named catalogs, explicit synthetic physical facts."""
import copy
import itertools
import json
import struct
import hashlib
from pathlib import Path
from dataclasses import replace

import pytest

from bidking_lab.inference import v2
from bidking_lab.inference import size_avg_evidence as size
from bidking_lab.live.fatbeans import FatbeansCaptureEvents,FatbeansStateEvent,_parse_action_result
from bidking_lab.live.monitor import _action_result_rows

SESSION='4501:314159265'
COUNTS={'q1':0,'q3':0,'q4':0,'q5':0,'q6':3}
CELLS={'q1':0,'q3':0,'q4':0,'q5':0,'q6':14}

def raw_row(action,value):
    def varint(n):
        out=bytearray()
        while n>=128:out.append((n&127)|128);n>>=7
        out.append(n);return bytes(out)
    parsed=_parse_action_result(varint(4<<3)+varint(action)+varint((9<<3)|5)+struct.pack('<f',value))
    events=FatbeansCaptureEvents(packets=(),frames=(),sends=(),statuses=(),states=(FatbeansStateEvent(
        sort_id=20,capture_time='2026-09-07T01:00:00+08:00',message_id=0x27,session_id=SESSION,
        map_id=4501,round_index=3,action_results=(parsed,)),))
    return _action_result_rows(events,{},session_id=SESSION)[0]

def snapshot(rows=None,hero='ahmed'):
    return {'hero':hero,'map_id':4501,'round':3,'phase':'bidding','session_id':SESSION,
            'warehouse_source':{'qualification':'official_random_warehouse','official_distribution_eligible':True,
                                'quote_policy':'advisory_only_no_clamp','quarantine_required':False},
            'warehouse_source_qualification':'official_random_warehouse','official_distribution_eligible':True,
            'action_result_rows':rows if rows is not None else [raw_row(100172,7402320)],
            'structured_ref_inputs':{'source':'monitor_pre_settlement_field_updates','total_count':3,
                'fixed_counts':dict(COUNTS),'quality_cells':dict(CELLS)}}

def actual_catalog():
    rows,maps,status,generation=v2._validated_physical_category_catalog()
    assert status=='ok' and generation.startswith('physical-category-v1:')
    assert {1016004,1096001,1066009}.issubset(maps[4501])
    assert [(rows[i].quality,rows[i].width*rows[i].height,rows[i].value) for i in (1016004,1096001,1066009)]==[
        (6,4,7402320),(6,2,92880),(6,8,281600)]
    return rows,maps,generation

@pytest.mark.parametrize('count,total,expected',[(1,4,{1}),(3,14,{0,1,2})])
def test_area_universe_matches_real_catalog_oracle(count,total,expected):
    rows,maps,_=actual_catalog()
    areas={rows[i].width*rows[i].height for i in maps[4501] if rows[i].quality==6}
    oracle={sum(c==4 for c in arm) for arm in itertools.product(areas,repeat=count) if sum(arm)==total}
    assert oracle==expected
    counts=dict(COUNTS,q6=count);cells=dict(CELLS,q6=total)
    domains=v2._canonical_map_all_footprint_domains(4501)
    options=size.exact_quality_footprint_count_options(footprint_cells=4,exact_quality_counts=counts,
        exact_quality_cells=cells,candidate_domains_by_cells=domains,exact_total_item_count=count,
        exact_warehouse_total_cells=total)
    print(json.dumps({'areas':sorted(areas),'oracle':sorted(oracle),'actual':options[6]}))
    assert set(options[6])==oracle

def test_current_full_engine_cannot_publish_floor_above_real_4_2_8_warehouse():
    actual_catalog()
    import ahmad_ref_engine as engine
    prepared=snapshot()
    evidence=engine.extract_evidence(copy.deepcopy(prepared))
    result=engine.run_reference_engine(copy.deepcopy(prepared),max_combos=2000).as_dict()
    print(json.dumps({'quality_cells':evidence.quality_cells,'source_notes':evidence.source_notes,
        'shape_floor':evidence.shape_average_value_floors,'quotes':[result[k] for k in ('conservative','balanced','aggressive')]}))
    # Decisive hard-evidence comparison, not a requirement that probabilistic
    # quotes equal one constructed warehouse realization.
    assert evidence.shape_average_value_floors.get('q6',0)<=7776800


@pytest.mark.parametrize('cells,action',[(1,100169),(2,100170),(3,100171),(4,100172),(6,100173)])
@pytest.mark.parametrize('count',[1,2])
def test_five_buckets_small_exact_physical_totals_reach_quote_floor(cells,action,count):
    import ahmad_ref_engine as engine
    domain=v2.canonical_map_footprint_domain(4501,cells)
    item=min((c for c in domain.candidates if c.quality==1),key=lambda c:c.value)
    state=snapshot([raw_row(action,item.value)])
    state['structured_ref_inputs'].update(total_count=count,
        fixed_counts={'q1':count,'q3':0,'q4':0,'q5':0,'q6':0},
        quality_cells={'q1':cells*count,'q3':0,'q4':0,'q5':0,'q6':0})
    evidence=engine.extract_evidence(copy.deepcopy(state))
    assert evidence.shape_average_subset_floor==item.value*count
    assert evidence.shape_average_value_floors['q1']==item.value*count
    result=engine.run_reference_engine(state,max_combos=2000).as_dict()
    assert result['evidence']['shape_average_subset_floor']==item.value*count
    assert all(result[k]>=item.value*count for k in ('conservative','balanced','aggressive'))


def outline(items):
    return {'action_id':100100,'session_id':SESSION,'round':3,'sort':19,
            'revealed_items_detail':items}


def physical(item,runtime,*,known=False,local=None):
    return {'runtime_id':runtime,'local_index':local,'shape_code':int(str(item.width)+str(item.height)),
            'cells':item.cells,'item_id':item.item_id if known else None,
            'quality':item.quality if known else None,'value':item.value if known else None}


def test_full_outline_n_without_quality_assignment_keeps_same_price_cids():
    import ahmad_ref_engine as engine
    item=next(c for c in v2.canonical_map_footprint_domain(4501,3).candidates if c.item_id==1046007)
    state=snapshot([outline([physical(item,101)]),raw_row(100171,143100)],hero='aisha')
    state['structured_ref_inputs']={'source':'monitor_pre_settlement_field_updates','total_count':1}
    evidence=engine.extract_evidence(state)
    assert evidence.shape_average_subset_floor==143100
    support=evidence.shape_average_active_support[0]
    assert {1046007,1116028}.issubset(support['count_one']['candidate_item_ids'])
    assert support['count_one']['unique_item_id'] is None
    assert support['item_multiset_uniqueness']=='not_computed'


def test_known_anchor_inside_bucket_is_seeded_once_with_unknown_remainder():
    import ahmad_ref_engine as engine
    domain=v2.canonical_map_footprint_domain(4501,3)
    a=next(c for c in domain.candidates if c.item_id==1046007)
    b=next(c for c in domain.candidates if c.item_id==1116028)
    state=snapshot([outline([physical(a,101,known=True),physical(b,102)]),raw_row(100171,143100)],hero='ahmed')
    state['structured_ref_inputs'].update(total_count=2,fixed_counts={'q1':0,'q3':0,'q4':0,'q5':0,'q6':2},
                                         quality_cells={'q1':0,'q3':0,'q4':0,'q5':0,'q6':6})
    evidence=engine.extract_evidence(state)
    assert evidence.shape_average_subset_floor==286200
    assert evidence.shape_average_value_floors['q6']==286200
    assert evidence.shape_average_active_support[0]['count_value']==2


def joint_state(*,known_outside=False,hero='ahmed'):
    a=next(c for c in v2.canonical_map_footprint_domain(4501,3).candidates if c.item_id==1046007)
    b=next(c for c in v2.canonical_map_footprint_domain(4501,4).candidates if c.item_id==1016004)
    rows=[outline([physical(a,101),physical(b,102,known=known_outside)]),raw_row(100171,a.value)]
    if not known_outside:
        rows.append(raw_row(100172,b.value))
    state=snapshot(rows,hero=hero)
    state['structured_ref_inputs'].update(total_count=2,fixed_counts={'q1':0,'q3':0,'q4':0,'q5':0,'q6':2},
                                         quality_cells={'q1':0,'q3':0,'q4':0,'q5':0,'q6':7})
    return state,a,b


@pytest.mark.parametrize('known_outside',[False,True])
@pytest.mark.parametrize('hero',['aisha','ahmed','gabriela'])
def test_disjoint_buckets_or_known_outside_add_once_through_three_quotes(known_outside,hero):
    import ahmad_ref_engine as engine
    state,a,b=joint_state(known_outside=known_outside,hero=hero)
    before=engine.extract_evidence(copy.deepcopy(state))
    expected=a.value+b.value
    assert before.shape_average_subset_floor==expected
    # Replayed mean and outline packets cannot add another copy of the subset.
    duplicate=copy.deepcopy(state['action_result_rows'][-1]);duplicate['sort']=23
    state['action_result_rows'].append(duplicate)
    state['action_result_rows'].append(copy.deepcopy(state['action_result_rows'][0]))
    result=engine.run_reference_engine(state,max_combos=2000).as_dict()
    assert result['evidence']['shape_average_subset_floor']==expected
    quotes=[result[k] for k in ('conservative','balanced','aggressive')]
    assert quotes==sorted(quotes) and min(quotes)>=expected


def test_cross_bucket_empty_joint_never_publishes_independent_marginals():
    import ahmad_ref_engine as engine
    state=snapshot([raw_row(100169,107),raw_row(100170,107)])
    state['structured_ref_inputs']={'source':'monitor_pre_settlement_field_updates','total_count':1}
    evidence=engine.extract_evidence(state)
    assert evidence.shape_average_subset_floor==0 and evidence.shape_average_active_support==()


@pytest.mark.parametrize('fault',['alias','negative_remainder','wrong_value','wrong_generation','future','zero_bucket'])
def test_physical_joint_conflicts_are_atomic_no_hard_change(fault):
    import ahmad_ref_engine as engine
    state,a,b=joint_state(known_outside=True)
    physical_rows=state['action_result_rows'][0]['revealed_items_detail']
    if fault=='alias':
        for item in physical_rows:item['local_index']=0
    elif fault=='negative_remainder':
        state['structured_ref_inputs']['total_count']=1
    elif fault=='wrong_value':
        physical_rows[1]['value']=b.value+1
    elif fault=='wrong_generation':
        state['physical_candidate_domains']=[{'catalog_generation':'old'}]
    elif fault=='future':
        state['action_result_rows'][-1]['round']=4
    else:
        state['action_result_rows'][-1]=raw_row(100169,107)
    evidence=engine.extract_evidence(state)
    assert evidence.shape_average_subset_floor==0
    assert evidence.shape_average_active_support==()


def test_partial_outline_and_red_plus_empty_do_not_invent_exact_bucket_n():
    import ahmad_ref_engine as engine
    a=next(c for c in v2.canonical_map_footprint_domain(4501,4).candidates if c.item_id==1016004)
    row=outline([physical(a,101)]);row['action_id']=100001
    state=snapshot([row,raw_row(100172,a.value)])
    evidence=engine.extract_evidence(state)
    mean=evidence.shape_average_active_support[0]
    assert mean['count_status']=='lower_bound'
    assert [arm['item_count'] for arm in mean['count_arms']]==[1,2]
    _,support=engine._shape_average_active_joint_facts(snapshot(),phase='bidding',current_session=SESSION,
        map_id=4501,total_count=3,fixed_counts=COUNTS,quality_cells=CELLS,
        physical_candidate_domains=(),source_notes=('quality_cells_q6_from_total_grid_residual',))
    assert [arm['item_count'] for arm in support[0]['count_arms']]==[1,2,3]


def test_same_cid_two_physical_items_remains_two_mandatory_units():
    import ahmad_ref_engine as engine
    item=next(c for c in v2.canonical_map_footprint_domain(4501,3).candidates if c.item_id==1046007)
    state=snapshot([outline([physical(item,101,known=True),physical(item,102,known=True)]),raw_row(100171,item.value)])
    state['structured_ref_inputs'].update(total_count=2,fixed_counts={'q1':0,'q3':0,'q4':0,'q5':0,'q6':2},
                                         quality_cells={'q1':0,'q3':0,'q4':0,'q5':0,'q6':6})
    evidence=engine.extract_evidence(state)
    assert evidence.shape_average_subset_floor==item.value*2
    assert evidence.shape_average_active_support[0]['common_facts']['item_count_range']==[2,2]


def test_actual_physical_domain_and_stronger_anchor_share_one_identity():
    import ahmad_ref_engine as engine
    from bidking_lab.extract.item_table import load_item_table
    from bidking_lab.config import project_root
    catalog,_,_=actual_catalog()
    item=next(c for c in v2.canonical_map_footprint_domain(4501,3).candidates if c.item_id==1046007)
    category=next(iter(catalog[item.item_id].category_yes))
    builder=v2.EvidenceStoreBuilder()
    builder.add_item(v2.RuntimeEvidence(runtime_id=101,local_index=1,quality=6,shape_key='31',cells=3,
                                        categories=(category,),sources=('action:100100',)))
    build=v2.physical_candidate_domains_from_store(4501,builder.build(),
        items=load_item_table(project_root()/'data/raw/tables/Item.txt'))
    assert build.catalog_status=='ok' and len(build.domains)==1
    domain=build.domains[0].as_dict()
    assert item.item_id in domain['candidate_item_ids']
    state=snapshot([outline([physical(item,101,known=True,local=1)]),raw_row(100171,item.value)])
    state['physical_candidate_domains']=[domain]
    state['structured_ref_inputs'].update(total_count=1,fixed_counts={'q1':0,'q3':0,'q4':0,'q5':0,'q6':1},
                                         quality_cells={'q1':0,'q3':0,'q4':0,'q5':0,'q6':3})
    evidence=engine.extract_evidence(state)
    assert evidence.shape_average_subset_floor==item.value
    support=evidence.shape_average_active_support[0]
    assert support['common_facts']['item_count_range']==[1,1]
    assert support['count_one']['unique_item_id']==item.item_id


def test_mandatory_quality_filter_checks_each_quality_not_only_n():
    from bidking_lab.inference.mean_multiset_support import MeanSupportCandidate,solve_repeat_allowed_mean_support
    candidates=[MeanSupportCandidate(1,1,100,3),MeanSupportCandidate(2,6,200,3)]
    opts={q:(0,) for q in range(1,7)};opts[6]=(2,)
    bad=solve_repeat_allowed_mean_support(candidates,total_lower=300,total_upper=300,item_count=2,
        mandatory_units=[{'identity':'runtime:1','candidate_item_ids':[1]}],quality_count_options=opts)
    assert bad.status=='ok' and bad.signatures==()
    opts[1]=(1,);opts[6]=(1,)
    good=solve_repeat_allowed_mean_support(candidates,total_lower=300,total_upper=300,item_count=2,
        mandatory_units=[{'identity':'runtime:1','candidate_item_ids':[1]}],quality_count_options=opts)
    assert good.status=='ok' and len(good.signatures)==1
    assert good.signatures[0].quality_counts==(1,0,0,0,0,1)


def test_joint_budget_failure_never_publishes_prefix():
    import ahmad_ref_engine as engine
    state,_,_=joint_state()
    saved=size._join_shape_average_support
    def bounded(*args,**kwargs):
        return saved(*args,**kwargs,transition_budget=1)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(size,'_join_shape_average_support',bounded)
        evidence=engine.extract_evidence(state)
    assert evidence.shape_average_subset_floor==0 and evidence.shape_average_active_support==()


@pytest.mark.parametrize('fault',['bool','string','varint','bits','session','settled','duplicate'])
def test_mean_typed_wire_and_epoch_guards_keep_raw_without_hard_change(fault):
    import ahmad_ref_engine as engine
    state=snapshot();row=state['action_result_rows'][0]
    if fault=='bool':row['result']=True
    elif fault=='string':row['result']=str(row['result'])
    elif fault=='varint':row['result_wire_type']='varint';row['result_field']=14
    elif fault=='bits':row['result_raw_hex']='0000803f'
    elif fault=='session':row['session_id']='4501:999'
    elif fault=='settled':state['phase']='settled'
    else:state['action_result_rows'].append(raw_row(100172,1000))
    raw=copy.deepcopy(state['action_result_rows'])
    evidence=engine.extract_evidence(state)
    assert evidence.shape_average_active_support==() and evidence.shape_average_subset_floor==0
    assert state['action_result_rows']==raw


def test_existing_rollback_restores_no_mean_quote_path(monkeypatch):
    import ahmad_ref_engine as engine
    state,_,_=joint_state()
    no_mean=copy.deepcopy(state)
    no_mean['action_result_rows']=[row for row in state['action_result_rows'] if row['action_id']==100100]
    baseline=engine.run_reference_engine(no_mean,max_combos=2000).as_dict()
    monkeypatch.setenv(size.SHAPE_AVG_ACTIVE_JOINT_DISABLE,'1')
    rollback=engine.run_reference_engine(state,max_combos=2000).as_dict()
    assert [rollback[k] for k in ('conservative','balanced','aggressive')]==[
        baseline[k] for k in ('conservative','balanced','aggressive')]
    assert 'shape_average_active_support' not in rollback['evidence']


def test_same_runtime_can_move_local_position_without_new_item():
    import ahmad_ref_engine as engine
    item=next(c for c in v2.canonical_map_footprint_domain(4501,3).candidates if c.item_id==1046007)
    state=snapshot([outline([physical(item,101,known=True,local=0)]),
                    {'action_id':100001,'session_id':SESSION,'round':3,'sort':21,
                     'revealed_items_detail':[physical(item,101,known=True,local=25)]},raw_row(100171,item.value)])
    state['structured_ref_inputs']={'source':'monitor_pre_settlement_field_updates','total_count':1}
    evidence=engine.extract_evidence(state)
    assert evidence.shape_average_subset_floor==item.value
    assert evidence.shape_average_active_support[0]['common_facts']['item_count_range']==[1,1]


def test_full_outline_semantic_id_cannot_cross_public_info_namespace():
    import ahmad_ref_engine as engine
    item=v2.canonical_map_footprint_domain(4501,3).candidates[0]
    state=snapshot()
    state['public_info_rows']=[{'info_id':100100,'session_id':SESSION,'round':3,
                               'revealed_items_detail':[physical(item,101)]}]
    context=engine._shape_average_physical_context(state,session=SESSION,map_id=4501,
        domains=v2._canonical_map_all_footprint_domains(4501),total_count=2,physical_domains=())
    assert context is not None
    assert all('count_exact' not in target for target in context[0])


def test_quality_category_random_overlap_and_quote_preferences_stay_independent():
    import ahmad_ref_engine as engine
    from bidking_lab.live import quote_preferences as prefs
    state,a,b=joint_state()
    low=min((c for c in v2.canonical_map_footprint_domain(4501,1).candidates if c.quality==1),key=lambda c:c.value)
    state['action_result_rows'][0]['revealed_items_detail'].extend(physical(low,r,known=True) for r in (103,104,105))
    state['structured_ref_inputs'].update(total_count=5,fixed_counts={'q1':3,'q3':0,'q4':0,'q5':0,'q6':2},
                                         quality_cells={'q1':3,'q3':0,'q4':0,'q5':0,'q6':7})
    base=engine.run_reference_engine(copy.deepcopy(state),max_combos=2000).as_dict()
    state['public_info_rows']=[{'info_id':200044,'value':a.value,'session_id':SESSION,'round':3}]
    state['ui_contract']={'constraints':{'public_info':{'public_random_avg_values':[
        {'kind':'random_avg_value','sample_count':3,'value':low.value}]}}}
    observed=engine.extract_evidence(copy.deepcopy(state))
    assert observed.public_category_total_value_floor==a.value
    assert engine._random_value_floor(observed)==3*low.value
    result=engine.run_reference_engine(state,max_combos=2000).as_dict()
    expected=a.value+b.value+3*low.value
    assert result['evidence']['shape_average_subset_floor']==expected
    raw={key:result[key] for key in prefs.TIERS}
    assert raw=={key:base[key] for key in prefs.TIERS}
    assert prefs.apply_policy(raw,prefs.default_policy())==raw
    custom=prefs.default_policy();custom['ratios_bps']={key:5000 for key in prefs.TIERS};custom['cap']=1000
    assert prefs.apply_policy(raw,custom)==dict.fromkeys(prefs.TIERS,1000)


def test_map_area_identity_mismatch_cannot_cross_mix_same_catalog_generation():
    domains=v2._canonical_map_all_footprint_domains(4501)
    domains[8]=replace(domains[8],map_id=2601)
    assert size.exact_quality_footprint_count_options(footprint_cells=4,exact_quality_counts=COUNTS,
        exact_quality_cells=CELLS,candidate_domains_by_cells=domains,exact_total_item_count=3) is None


def test_wide_single_partition_envelope_is_not_an_exact_cross_joint():
    row={'footprint_cells':3,'joint_support':[{'item_count':1,'total_value':100,
        'quality_counts':{},'quality_values':{},'quality_count_ranges':{'q1':[0,1],'q6':[0,1]},
        'quality_value_ranges':{'q1':[0,100],'q6':[0,100]}}]}
    outcome=size._join_shape_average_support([row],domains=v2._canonical_map_all_footprint_domains(4501),
        exact_counts={},exact_cells={},total_count=1,total_cells=3,mandatory_units_by_cells={})
    assert outcome=={'status':'joint_envelope_not_exact'}


@pytest.mark.parametrize('relative',[
    'external_references/AuctionAnalyzer4.13.3/_decompiled/MapBidCalculator/MapBidCalculator/Models/StaticData.cs',
    'data/processed/map_category_calibration_0_3_2.json'])
def test_numeric_input_gate_rejects_missing_and_wrong_bytes(tmp_path,relative):
    from bidking_lab.config import project_root
    root=project_root()
    manifest=json.loads((root.parent/'ASSEMBLED_INPUTS.json').read_text())['files']
    expected=manifest[relative]
    def require(path):
        raw=path.read_bytes()
        if len(raw)!=expected['bytes'] or hashlib.sha256(raw).hexdigest()!=expected['sha256']:
            raise ValueError('input assembly mismatch')
    require(root/relative)
    with pytest.raises(FileNotFoundError):require(tmp_path/'missing')
    wrong=tmp_path/'wrong';wrong.write_bytes(b'wrong-input')
    with pytest.raises(ValueError,match='input assembly mismatch'):require(wrong)


def test_large_count_guard_precedes_area_enumeration_and_keeps_raw():
    import ahmad_ref_engine as engine
    state=snapshot();state['structured_ref_inputs']['total_count']=100000
    state['structured_ref_inputs']['fixed_counts']['q6']=100000
    state['structured_ref_inputs']['quality_cells']['q6']=400000
    before=copy.deepcopy(state['action_result_rows'])
    result=engine.extract_evidence(state)
    assert result.shape_average_subset_floor==0 and result.shape_average_active_support==()
    assert state['action_result_rows']==before
