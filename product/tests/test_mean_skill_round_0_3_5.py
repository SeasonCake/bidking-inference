"""F03 actual monitor coordinate projection; no claim of real R5 wire samples."""
import copy,json
from dataclasses import replace
import pytest
import test_mean_skill_lineage_0_3_5 as lineage
from bidking_lab.live import monitor
from bidking_lab.live.fatbeans import FatbeansCaptureEvents,FatbeansStateEvent,FatbeansSendEvent,FatbeansSkillReveal
from bidking_lab.runtime.snapshot import ui_contract_from_artifact
engine=lineage.engine

def caller_state(hero,*,wire=lineage.UNSET,state_round=lineage.UNSET,lane='raw',sent_bids=0,earlier_state=None,conflicting=False):
    wire=(2 if hero=='indiana' else 5) if wire is lineage.UNSET else wire
    state_round=wire if state_round is lineage.UNSET else state_round
    s,capture=lineage.fixture(hero,wire=wire,state_round=state_round,conflicting=conflicting,with_capture=True)
    sends=tuple(FatbeansSendEvent(sort_id=i,capture_time='synthetic',message_id=0x22,session_id=lineage.SESSION,value=100+i) for i in range(sent_bids))
    states=capture.states
    if earlier_state is not None:
        states=(FatbeansStateEvent(sort_id=1,capture_time='synthetic',message_id=0x25,session_id=lineage.SESSION,map_id=4501,round_index=earlier_state),*states)
    capture=replace(capture,sends=sends,states=states)
    s.update(round=monitor._action_round(capture),observed_round=monitor._latest_round(capture),phase=monitor._latest_phase(capture))
    for row in s['action_result_rows']:row['round']=1
    if lane=='compact':s.pop('skill_reveal_rows')
    return s,capture

@pytest.mark.parametrize('lane',['raw','compact'])
def test_original_f02_a_observed_cannot_override_actionable(lane):
    s,capture=caller_state('indiana',lane=lane)
    assert (s['round'],s['observed_round'])==(3,2)
    assert engine.extract_evidence(copy.deepcopy(s)).shape_average_active_support
    s['round']=2
    e=engine.extract_evidence(copy.deepcopy(s));quotes,_=lineage.quotes(s)
    print(json.dumps({'case':'F02-A','lane':lane,'K':e.shape_average_subset_floor,'quotes':quotes}))
    assert not e.shape_average_active_support

@pytest.mark.parametrize('hero',['ethan','raven'])
def test_original_f02_b_generic_wire5_has_actual_caller_window(hero):
    table=[]
    for state_round in range(6):
        s,_=caller_state(hero,wire=5,state_round=state_round)
        e=engine.extract_evidence(s)
        table.append({'state':state_round,'observed':s['observed_round'],'actionable':s['round'],'K':e.shape_average_subset_floor})
    print(json.dumps({'case':'F02-B-generic-not-real-wire','hero':hero,'table':table}))
    assert table[-1]['K']>0 and all(row['K']==0 for row in table[:-1])

@pytest.mark.parametrize('hero',['ethan','raven','indiana'])
@pytest.mark.parametrize('lane',['raw','compact'])
def test_original_physical_content_through_real_caller_and_ui_contract(hero,lane):
    s,_=caller_state(hero,lane=lane)
    s['action_round']=s['round'];s['ui_contract']=ui_contract_from_artifact(s)
    context=s['ui_contract']['context']
    assert context['action_round']==s['round'] and context['observed_round']==s['observed_round']
    good=engine.extract_evidence(copy.deepcopy(s));q,_=lineage.quotes(s)
    assert good.shape_average_active_support and all(type(v) is int for v in q)
    if hero=='ethan':assert good.shape_average_subset_floor==143207 and good.shape_average_active_support[0]['count_status']=='exact'
    else:
        bad,_=caller_state(hero,lane=lane,conflicting=True)
        assert not engine.extract_evidence(bad).shape_average_active_support
    # The same produced context can be consumed without redundant root coordinates.
    s.pop('round');s.pop('action_round');s.pop('observed_round')
    assert engine.extract_evidence(s).shape_average_subset_floor==good.shape_average_subset_floor

@pytest.mark.parametrize('hero',['ethan','raven'])
@pytest.mark.parametrize('wire',[4,5])
def test_generic_full_skill_wire_is_not_fixed_to_five(hero,wire):
    s,_=caller_state(hero,wire=wire,state_round=wire)
    assert engine.extract_evidence(s).shape_average_active_support
    early,_=caller_state(hero,wire=3,state_round=3)
    assert not engine.extract_evidence(early).shape_average_active_support

@pytest.mark.parametrize('lane',['raw','compact'])
@pytest.mark.parametrize('fault',['action_equal_observed','action_alias_conflict','context_observed_conflict','state_future','wire_future','current_bool','observed_string'])
def test_caller_coordinate_conflicts_never_add_hard_support(lane,fault):
    s,_=caller_state('indiana',lane=lane)
    raw=s['skill_reveals'][0] if lane=='compact' else s['skill_reveal_rows'][0]
    if fault=='action_equal_observed':s['round']=s['observed_round']
    elif fault=='action_alias_conflict':s['action_round']=s['round']+1
    elif fault=='context_observed_conflict':
        s['ui_contract']=ui_contract_from_artifact(s);s['ui_contract']['context']['observed_round']=1
    elif fault=='state_future':raw['state_round_index']=s['round']
    elif fault=='wire_future':raw['round_index']=s['round']
    elif fault=='current_bool':s['round']=True
    else:s['observed_round']=str(s['observed_round'])
    original=copy.deepcopy(s)
    assert not engine.extract_evidence(s).shape_average_active_support
    assert s==original

@pytest.mark.parametrize('lane',['raw','compact'])
@pytest.mark.parametrize('bid_count,accepted',[(1,False),(2,True),(4,True)])
def test_known_indiana_missing_state_bid_count_fallback(lane,bid_count,accepted):
    s,capture=caller_state('indiana',wire=2,state_round=None,lane=lane,sent_bids=bid_count)
    assert s['round']==bid_count+1 and s['observed_round'] is None
    assert bool(engine.extract_evidence(s).shape_average_active_support)==accepted

@pytest.mark.parametrize('hero',['ethan','raven'])
def test_unknown_full_wire_no_state_or_observed_remains_unqualified(hero):
    s,_=caller_state(hero,wire=5,state_round=None,sent_bids=5)
    assert s['round']==6 and s['observed_round'] is None
    assert not engine.extract_evidence(s).shape_average_active_support

@pytest.mark.parametrize('hero',['ethan','raven','indiana'])
def test_missing_latest_state_can_retain_earlier_observed_without_exact_plus_one(hero):
    observed=2 if hero=='indiana' else 5
    bid_count=4 if hero=='indiana' else 6
    s,_=caller_state(hero,wire=observed,state_round=None,sent_bids=bid_count,earlier_state=observed)
    assert s['round']==bid_count+1 and s['observed_round']==observed and s['round']>observed+1
    assert engine.extract_evidence(s).shape_average_active_support

def test_canonical_r1_and_legacy_evaluator_are_separate_qualified_scopes():
    capture=FatbeansCaptureEvents(packets=(),frames=(),sends=(),statuses=(),states=(FatbeansStateEvent(
        sort_id=1,capture_time='synthetic',message_id=0x27,session_id=lineage.SESSION,map_id=4501,round_index=None,
        skill_reveals=(FatbeansSkillReveal(skill_id=1001041,hero_id=104,round_index=None),)),))
    rows=monitor._skill_reveal_rows(capture,{},session_id=lineage.SESSION)
    s={'round':monitor._action_round(capture),'observed_round':monitor._latest_round(capture)}
    assert s=={'round':1,'observed_round':None} and rows[0]['round_index_state']=='skill_canonical'
    assert engine._shape_average_skill_round_current(rows[0],s,1)
    legacy={'session_id':lineage.SESSION,'round':5}
    assert engine._shape_average_skill_round_current(legacy,{'round':5},5)
    legacy.pop('round')
    assert not engine._shape_average_skill_round_current(legacy,{'round':5},5)

@pytest.mark.parametrize('message,state_round,bids,phase,current,observed',[
    (0x21,4,0,'reading',1,4),(0x27,0,0,'bidding',1,0),(0x27,5,0,'bidding',6,5),
    (0x27,None,3,'bidding',4,None),(0x2d,2,4,'settled',4,2)])
def test_actual_caller_phase_coordinates_are_not_one_global_formula(message,state_round,bids,phase,current,observed):
    capture=FatbeansCaptureEvents(packets=(),frames=(),statuses=(),
        sends=tuple(FatbeansSendEvent(sort_id=i,capture_time='synthetic',message_id=0x22,session_id=lineage.SESSION,value=100) for i in range(bids)),
        states=(FatbeansStateEvent(sort_id=10,capture_time='synthetic',message_id=message,session_id=lineage.SESSION,map_id=4501,round_index=state_round),))
    assert (monitor._latest_phase(capture),monitor._action_round(capture),monitor._latest_round(capture))==(phase,current,observed)
