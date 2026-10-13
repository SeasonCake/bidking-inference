"""F02: actual skill projectors, real catalog/mean parser and current engine."""
import copy
import json
import struct
import pytest
import ahmad_ref_engine as engine
from bidking_lab.inference import v2, size_avg_evidence as size
from bidking_lab.live import monitor
from bidking_lab.live.fatbeans import FatbeansObservedItem,FatbeansSkillReveal,FatbeansStateEvent,FatbeansCaptureEvents,_parse_action_result

SESSION='4501:918273645'
HERO_IDS={'ethan':208,'raven':301,'indiana':303}
UNSET=object()

def catalog_item(cid):
    return next(c for d in v2._canonical_map_all_footprint_domains(4501).values() for c in d.candidates if c.item_id==cid)

def physical(item,runtime):
    return {'runtime_id':runtime,'shape_code':item.width*10+item.height,'cells':item.cells}

def outline_row(items):
    return {'action_id':100100,'session_id':SESSION,'round':3,'sort':29,'revealed_items_detail':items}

def mean_row(action,value):
    def varint(x):
        result=bytearray()
        while x>127:result.append((x&127)|128);x>>=7
        result.append(x);return bytes(result)
    parsed=_parse_action_result(varint(32)+varint(action)+varint(77)+struct.pack('<f',value))
    capture=FatbeansCaptureEvents(packets=(),frames=(),sends=(),statuses=(),states=(FatbeansStateEvent(
        sort_id=31,capture_time='2026-09-07T02:00:00+08:00',message_id=0x27,session_id=SESSION,map_id=4501,round_index=3,action_results=(parsed,)),))
    return monitor._action_result_rows(capture,{},session_id=SESSION)[0]

def current_state(rows,n,hero):
    return {'hero':hero,'map_id':4501,'round':3,'phase':'bidding','session_id':SESSION,
        'warehouse_source':{'qualification':'official_random_warehouse','official_distribution_eligible':True,
                            'quote_policy':'advisory_only_no_clamp','quarantine_required':False},
        'warehouse_source_qualification':'official_random_warehouse','official_distribution_eligible':True,
        'action_result_rows':rows,'structured_ref_inputs':{'source':'monitor_pre_settlement_field_updates','total_count':n}}

def quotes(state):
    result=engine.run_reference_engine(copy.deepcopy(state),max_combos=2000).as_dict()
    return [result[k] for k in ('conservative','balanced','aggressive')],result

def projected(hero,skill_id,items,*,session=SESSION,wire=5,state_round=5,with_capture=False):
    observed=tuple(FatbeansObservedItem(local_index=p.get('local_index'),runtime_id=p['runtime_id'],item_id=p.get('item_id'),
        quality=p.get('quality'),value=p.get('value'),shape_code=p.get('shape_code'),cells=p.get('cells')) for p in items)
    state=FatbeansStateEvent(sort_id=41,capture_time='2026-09-07T02:40:00+08:00',message_id=0x27,session_id=session,
        map_id=4501,round_index=state_round,skill_reveals=(FatbeansSkillReveal(skill_id=skill_id,hero_id=HERO_IDS[hero],
        round_index=wire,observed_items=observed),))
    events=FatbeansCaptureEvents(packets=(),frames=(),sends=(),statuses=(),states=(state,))
    rows=monitor._skill_reveal_rows(events,{},session_id=SESSION)
    result=(rows,monitor._skill_reveals_for_ref(rows))
    return (*result,events) if with_capture else result

def fixture(hero,*,wire=UNSET,state_round=UNSET,session=SESSION,conflicting=False,with_capture=False):
    gold=catalog_item(1015003);red=catalog_item(1046007);low=catalog_item(1091005)
    if hero=='ethan':
        items=[physical(red,901),physical(low,902)];skill_id=1002085
        s=current_state([mean_row(100171,red.value)],None,hero=hero)
    else:
        one=next(c for c in v2.canonical_map_footprint_domain(4501,1).candidates if c.quality==6)
        items=[dict(runtime_id=901,quality=5)]
        if hero=='raven':items.append(dict(runtime_id=902,quality=6))
        skill_id=100301 if hero=='raven' else 1003033
        outline=outline_row([physical(gold,901),physical(one,902)]);outline['revealed_items']=2
        s=current_state([outline,mean_row(100171,143100 if conflicting else gold.value)],2,hero=hero)
    w=(2 if hero=='indiana' else 5) if wire is UNSET else wire
    sr=w if state_round is UNSET else state_round
    rows,refs,capture=projected(hero,skill_id,items,session=session,wire=w,state_round=sr,with_capture=True)
    # F03: these are projected rows, not the legacy explicit-round evaluator.
    # Let the actual caller derive state5 -> action6; wire5 is only a fixture.
    s.update(round=monitor._action_round(capture),observed_round=monitor._latest_round(capture),
             skill_reveal_rows=rows,skill_reveals=refs)
    return (s,capture) if with_capture else s

def test_original_ethan_projector_full_outline_exact():
    s=fixture('ethan');e=engine.extract_evidence(copy.deepcopy(s));q,r=quotes(s)
    print(json.dumps({'hero':'ethan','K':e.shape_average_subset_floor,'status':[x['count_status'] for x in e.shape_average_active_support],'quotes':q}))
    assert e.shape_average_subset_floor==143207 and e.shape_average_active_support[0]['count_status']=='exact'

@pytest.mark.parametrize('hero',['raven','indiana'])
def test_original_projector_quality_conflict_no_hard(hero):
    good=engine.extract_evidence(fixture(hero));s=fixture(hero,conflicting=True)
    bad=engine.extract_evidence(copy.deepcopy(s));q,r=quotes(s)
    print(json.dumps({'hero':hero,'positive_K':good.shape_average_subset_floor,'conflicting_K':bad.shape_average_subset_floor,'quotes':q}))
    assert good.shape_average_active_support and not bad.shape_average_active_support

@pytest.mark.parametrize('hero',['ethan','raven','indiana'])
def test_actual_projection_lineage_and_compact_only_consumer(hero):
    s=fixture(hero)
    raw=s['skill_reveal_rows'][0];compact=s['skill_reveals'][0]
    for key in ('session_id','state_round_index','round_index','round_index_state'):
        assert raw[key]==compact[key]
    assert raw['session_id']==SESSION and raw['round_index_state']=='wire'
    expected=engine.extract_evidence(copy.deepcopy(s))
    s.pop('skill_reveal_rows')
    actual=engine.extract_evidence(s)
    assert actual.shape_average_subset_floor==expected.shape_average_subset_floor
    assert actual.shape_average_active_support

@pytest.mark.parametrize('session',[None,'4501:other-session'])
@pytest.mark.parametrize('hero',['ethan','raven','indiana'])
def test_unknown_or_wrong_session_is_not_stamped_current(hero,session):
    s=fixture(hero,session=session)
    rows=s['skill_reveal_rows']
    if session is None:
        assert rows and all(r['session_id'] is None for r in rows)
        assert all(r['session_id'] is None for r in s['skill_reveals'])
    else:assert rows==[] and s['skill_reveals']==[]
    domains=v2._canonical_map_all_footprint_domains(4501)
    current=engine._shape_average_physical_context(s,session=SESSION,map_id=4501,domains=domains,total_count=2,physical_domains=())
    no_skills=copy.deepcopy(s);no_skills.update(skill_reveal_rows=[],skill_reveals=[])
    absent=engine._shape_average_physical_context(no_skills,session=SESSION,map_id=4501,domains=domains,total_count=2,physical_domains=())
    assert current==absent

@pytest.mark.parametrize('fault',['wire_missing','wire_bool','wire_string','wire_wrong','wire_future','state_future','state_before_skill','current_before_skill','observed_before_skill','conflicting_wire_copy','malformed_explicit_round'])
@pytest.mark.parametrize('hero',['ethan','raven','indiana'])
def test_projected_round_qualification_never_invents_skill_authority(hero,fault):
    wire=2 if hero=='indiana' else 5;state_round=wire
    if fault=='wire_missing':wire=None
    elif fault=='wire_bool':wire=True
    elif fault=='wire_string':wire=str(wire)
    elif fault=='wire_wrong':wire=3 if hero=='indiana' else 4
    elif fault=='wire_future':wire=6
    elif fault=='state_future':state_round=6
    elif fault=='state_before_skill':state_round=1
    s=fixture(hero,wire=wire,state_round=state_round)
    if fault=='state_future':
        valid=fixture(hero)
        s.update(round=valid['round'],observed_round=valid['observed_round'])
    if fault=='current_before_skill':s['round']=2 if hero=='indiana' else 4
    elif fault=='observed_before_skill':s['observed_round']=1 if hero=='indiana' else 4
    elif fault=='conflicting_wire_copy':s['skill_reveal_rows'][0]['round_index_wire']=99
    elif fault=='malformed_explicit_round':s['skill_reveal_rows'][0]['round']='5'
    before=copy.deepcopy(s['skill_reveal_rows'])
    e=engine.extract_evidence(s)
    if fault=='wire_wrong' and hero in ('ethan','raven'):
        # The old wire==5 assumption was withdrawn. Wire4/state5 is a
        # compatible generic projection, not proof of the real R5 packet.
        assert e.shape_average_active_support
    else:
        assert not e.shape_average_active_support and e.shape_average_subset_floor==0
    assert s['skill_reveal_rows']==before

def test_indiana_zero_based_and_generic_full_skill_caller_coordinates():
    indiana=fixture('indiana');indiana['round']=3;indiana['observed_round']=2
    assert engine.extract_evidence(indiana).shape_average_active_support
    for hero in ('ethan','raven'):
        s=fixture(hero);s['observed_round']=5
        assert engine.extract_evidence(s).shape_average_active_support

@pytest.mark.parametrize('hero',['ethan','raven','indiana'])
def test_duplicate_projected_skill_and_rollback_preserve_existing_quote_path(hero,monkeypatch):
    s=fixture(hero);expected=engine.extract_evidence(copy.deepcopy(s)).shape_average_subset_floor
    s['skill_reveal_rows'].append(copy.deepcopy(s['skill_reveal_rows'][0]))
    s['skill_reveals']=monitor._skill_reveals_for_ref(s['skill_reveal_rows'])
    assert engine.extract_evidence(copy.deepcopy(s)).shape_average_subset_floor==expected
    baseline=copy.deepcopy(s);baseline['action_result_rows']=[r for r in baseline['action_result_rows'] if r['action_id']!=100171]
    no,_=quotes(baseline)
    with monkeypatch.context() as m:
        m.setenv(size.SHAPE_AVG_ACTIVE_JOINT_DISABLE,'1')
        disabled,_=quotes(s)
    assert no==disabled

def test_partial_ethan_projector_does_not_invent_complete_n():
    rows,refs,capture=projected('ethan',1002081,[physical(catalog_item(1046007),901)],wire=1,state_round=1,with_capture=True)
    mean=mean_row(100171,143100);mean['round']=1
    s=current_state([mean],None,hero='ethan');s.update(round=monitor._action_round(capture),observed_round=monitor._latest_round(capture),skill_reveal_rows=rows,skill_reveals=refs)
    e=engine.extract_evidence(s)
    assert e.total_count is None and not e.shape_average_active_support

def test_unknown_followup_cannot_erase_actual_current_skill_lineage():
    def event(session,sort):
        return FatbeansStateEvent(sort_id=sort,capture_time='synthetic',message_id=0x27,session_id=session,map_id=4501,round_index=5,
            skill_reveals=(FatbeansSkillReveal(skill_id=100301,hero_id=301,round_index=5,
                observed_items=tuple(FatbeansObservedItem(runtime_id=r,quality=q,local_index=None,item_id=None,value=None,shape_code=None,cells=None)
                                     for r,q in ((901,5),(902,6)))),))
    events=FatbeansCaptureEvents(packets=(),frames=(),sends=(),statuses=(),states=(event(SESSION,41),event(None,42)))
    rows=monitor._skill_reveal_rows(events,{},session_id=SESSION)
    assert {r['session_id'] for r in rows}=={SESSION,None}
    s=fixture('raven',conflicting=True);s.update(skill_reveal_rows=rows,skill_reveals=monitor._skill_reveals_for_ref(rows))
    assert not engine.extract_evidence(s).shape_average_active_support
    same=FatbeansCaptureEvents(packets=(),frames=(),sends=(),statuses=(),states=(event(SESSION,41),event(SESSION,42)))
    latest=monitor._skill_reveal_rows(same,{},session_id=SESSION)
    assert len(latest)==1 and latest[0]['sort']==42

@pytest.mark.parametrize('mode',['known_same_identity','runtime_moves_local','alias_conflict'])
def test_actual_skill_quality_joins_existing_physical_identity_once(mode):
    s=fixture('raven')
    item=s['action_result_rows'][0]['revealed_items_detail'][0]
    gold=catalog_item(1015003);item.update(item_id=gold.item_id,value=gold.value,quality=5,local_index=1)
    skill_item=s['skill_reveal_rows'][0]['revealed_items_detail'][0]
    skill_item['local_index']=2 if mode=='runtime_moves_local' else 1
    if mode=='alias_conflict':skill_item['runtime_id']=999
    s['skill_reveals']=monitor._skill_reveals_for_ref(s['skill_reveal_rows'])
    e=engine.extract_evidence(s)
    if mode=='alias_conflict':assert not e.shape_average_active_support
    else:assert e.shape_average_subset_floor==82659

def test_existing_canonical_r1_is_preserved_without_fabricating_wire():
    row={'hero_id':104,'skill_id':1001041,'session_id':SESSION,'state_round_index':None,
         'round_index':0,'round_index_state':'skill_canonical'}
    assert engine._shape_average_skill_round_current(row,{'round':1},1)
    row['state_round_index']=4
    assert engine._shape_average_skill_round_current(row,{'round':5},5)
    row['round_index_state']='wire';row['state_round_index']=0
    assert engine._shape_average_skill_round_current(row,{'round':1},1)
    row['round_index_state']='wire';row['round_index']=1
    assert not engine._shape_average_skill_round_current(row,{'round':1},1)
