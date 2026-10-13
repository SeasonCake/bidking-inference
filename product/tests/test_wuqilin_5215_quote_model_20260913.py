"""5215 current producer/model consumer tests; synthetic, never first-live proof."""
from __future__ import annotations
import copy
import json
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'hero_ref/src'))
sys.path.insert(0, str(ROOT / 'hero_ref/tools'))
import ahmad_ref_engine as engine

GEN='b049c3-2026-09-10-597'
SESSION='5215:9000000000000060'
def snapshot(n=None):
    from bidking_lab.live.warehouse_source import classify_from_map_table
    maps={r['map_id']:r for r in json.loads((ROOT/'data/processed/maps.json').read_bytes())}
    source=classify_from_map_table(5215,maps,source_generation=GEN,profile_generation=GEN,
        game_version=GEN,owner_evidence_status='absent').as_dict()
    value=dict(map_id=5215,model_map_id=5215,hero='wuqilin',hero_id=207,
               session_id=SESSION,phase='bidding',round=1,
               warehouse_game_version_session_stable=True,warehouse_source=source,
               ui_contract=dict(context=dict(map_id=5215,hero='wuqilin',hero_id=207,
                   session_id=SESSION,phase='bidding',round=1),constraints={}))
    if n is not None:
        value['total_item_count']=n
        value['structured_ref_inputs']={'total_count':n}
        value['ui_contract']['constraints']['public_info']={'input_constraints':{
            'total_item_count':dict(value=n,confidence='exact')}}
    return value

def reveal(s, skill, items=(), result=None, round_no=1):
    s['round']=s['ui_contract']['context']['round']=round_no
    s.setdefault('skill_reveal_rows',[]).append(dict(hero_id=207,skill_id=skill,
        session_id=SESSION,state_round_index=round_no,round_index=round_no,
        observed_items=list(items),result=result))

@pytest.mark.parametrize('n',[None,38])
def test_current_direct_prepared_and_impl_quote(n):
    s=snapshot(n)
    original=copy.deepcopy(s)
    results=[run(value).as_dict() for value in (s,engine.prepare_reference_engine_snapshot(s))
             for run in (engine.run_reference_engine,engine._run_reference_engine_impl)]
    for out in results:
        assert out['status']=='provisional_estimated'
        assert 0 < out['conservative'] <= out['balanced'] <= out['aggressive']
        assert out['provisional_quote']['model_map_id']==5215
        assert out['provisional_quote']['official_distribution_authority'] is False
        assert out['provisional_quote']['guarantee_violations']==0
    assert all(out==results[0] for out in results)
    assert s==original

def test_real_antique_count_is_separate_from_total_and_conflict_rejected():
    s=snapshot(38)
    reveal(s,100207,result=20)
    out=engine.run_reference_engine(s).as_dict()
    assert out['evidence']['total_count']==38
    assert out['provisional_quote']['antique_count_range']==[20,20,20]
    s['skill_reveal_rows'][0]['result']=19
    out=engine.run_reference_engine(s).as_dict()
    assert out['balanced'] is None
    assert any('guarantee' in note for note in out['notes'])

@pytest.mark.parametrize('change',['generation','session','hero','map','settled'])
def test_bad_current_identity_never_quotes(change):
    s=snapshot(38)
    if change=='generation': s['warehouse_source']['game_version']='old'
    if change=='session': s['ui_contract']['context']['session_id']='5215:999'
    if change=='hero': s['hero_id']=206
    if change=='map': s['ui_contract']['context']['map_id']=6001
    if change=='settled': s['phase']='settled'
    assert engine.run_reference_engine(s).balanced is None


def item_rows():
    catalog=json.loads((ROOT/'data/processed/items_droppable.json').read_bytes())
    red=[x for x in catalog if 106 in x['tags'] and x['quality']==6]
    other=[x for x in catalog if 106 in x['tags'] and x['quality']==4]
    non=[x for x in catalog if 106 not in x['tags'] and 107 in x['tags'] and x['quality']==3]
    def obs(row, runtime):
        return dict(runtime_id=runtime,item_id=row['item_id'],quality=row['quality'],
                    value=row['value'],cells=row['shape_w']*row['shape_h'],
                    shape_code=row['shape_w']*10+row['shape_h'])
    return [obs(red[0],1),obs(red[1],2)], [obs(other[i%len(other)],i+3) for i in range(18)], obs(non[0],30)


def test_partial_full_and_duplicate_actual_values_replace_slots():
    reds,others,_=item_rows()
    s=snapshot(38)
    reveal(s,100207,result=20)
    reveal(s,4022,reds+others,round_no=4)
    full=engine.run_reference_engine(s).as_dict()
    assert full['balanced'] is not None,full['notes']
    duplicate=copy.deepcopy(s)
    duplicate['skill_reveal_rows'][-1]['observed_items'] *= 2
    dup=engine.run_reference_engine(duplicate).as_dict()
    assert dup['value_p50']==full['value_p50']
    assert dup['provisional_quote']['observed_physical_count']==20
    high=copy.deepcopy(s)
    high['skill_reveal_rows'][-1]['observed_items'][0]['value'] += 500000
    high_out=engine.run_reference_engine(high).as_dict()
    assert high_out['value_p50']-full['value_p50']==500000
    partial=copy.deepcopy(s)
    partial['skill_reveal_rows'][-1]['observed_items']=reds[:1]
    part=engine.run_reference_engine(partial).as_dict()
    assert part['balanced'] is not None
    assert part['provisional_quote']['observed_physical_count']==1
    assert part['value_p50'] != full['value_p50']


def test_full_warehouse_truth_is_exact_not_prior_plus_truth():
    reds,_,non=item_rows()
    s=snapshot(3)
    reveal(s,100207,result=2)
    reveal(s,4022,reds,round_no=4)
    s['minimap_grid_items']=[dict(non,category=107)]
    out=engine.run_reference_engine(s).as_dict()
    expected=sum(row['value'] for row in reds+[non])
    assert [out[k] for k in ('value_p25','value_p50','value_p75')]==[expected]*3,out['notes']


@pytest.mark.parametrize('field,value', [('fixed_counts',{'q6':2}),('quality_cells',{'q6':6}),
    ('quality_values',{'q6':1000000}),('avg_cells',{'q6':3.0}),('avg_values',{'q6':500000.0})])
def test_public_fields_reach_model(field,value):
    s=snapshot(38)
    s['structured_ref_inputs'][field]=value
    evidence=engine.extract_evidence(s)
    assert getattr(evidence,field)==value
    out=engine.run_reference_engine(s).as_dict()
    assert out['balanced'] is not None,out['notes']
    base=engine.run_reference_engine(snapshot(38)).as_dict()
    assert out['value_p50'] != base['value_p50']
    if field=='fixed_counts': assert out['quality_count_ranges']['q6']==[2]*3
    if field=='quality_cells': assert out['quality_cells_ranges']['q6']==[6]*3
    if field=='quality_values': assert out['red_value_range']==[1000000]*3


def test_missing_and_wrong_model_inputs_revoke_warm_quote(monkeypatch):
    s=snapshot(38)
    assert engine.run_reference_engine(s).balanced is not None
    original=engine._aisha_q5_total_cells_plaintext_bytes
    monkeypatch.setattr(engine,'_aisha_q5_total_cells_plaintext_bytes',lambda p: b'wrong' if str(p).endswith('drop_table_priors_0627.json') else original(p))
    out=engine.run_reference_engine(s)
    assert out.balanced is None and any('model_input_identity' in n for n in out.notes)
    def missing(_p): raise FileNotFoundError('synthetic missing input')
    monkeypatch.setattr(engine,'_aisha_q5_total_cells_plaintext_bytes',missing)
    assert any('model_input_missing' in n for n in engine.run_reference_engine(s).notes)


def test_summary_has_original_three_quotes_and_algorithm_notice(tmp_path):
    import ahmad_live_panel_server as panel
    s=snapshot(38)
    summary=panel.summarize_snapshot(s,snapshot_path=tmp_path/'synthetic.json')
    assert summary['reference']['action']=='算法暂估'
    assert summary['reference']['price_titles']==dict(conservative='保守',balanced='参考',aggressive='激进')
    assert all(summary['reference'][key]!='-' for key in ('conservative','balanced','aggressive'))
    assert summary['evidence']['next_info_hint']=='随信息更新'
    assert '6001' not in json.dumps(summary['reference'],ensure_ascii=False)
    stale=copy.deepcopy(s);stale['hero_id']=206
    assert panel.activity_2511_provisional_quote_revoked(stale,summary)


def test_actual_monitor_event_to_three_card_summary(monkeypatch,tmp_path):
    from test_wuqilin_5215_preopen_intake_b049 import fb,monitor,capture,event
    from dataclasses import replace
    import ahmad_live_panel_server as panel
    from bidking_lab.extract.bid_map_table import parse_bid_map_table
    from test_official_b049_minimal_compat_20260910 import STATIC
    raw=(STATIC/'textassets/BidMap.txt').read_text(encoding='utf-8-sig')
    maps=parse_bid_map_table([line.split('\t') for line in raw.splitlines()])
    monkeypatch.setattr(monitor,'_fresh_session_game_version',lambda *_:(GEN,True))
    tables=monitor.MonitorTables(maps=maps,drops={},items={},warehouse_package_generation=GEN,
        warehouse_game_version=GEN,warehouse_live_identity_check=True)
    reds,_,_=item_rows()
    item=fb.FatbeansObservedItem(local_index=20,**reds[0])
    state=event(skill_reveals=(fb.FatbeansSkillReveal(skill_id=4022,hero_id=207,round_index=4,observed_items=(item,)),))
    artifact=monitor.build_monitor_artifact_from_events(capture(state),tables=tables,
        n_trials=1,roi_trials=1,run_debug_shadows=False)
    result=engine.run_reference_engine(artifact).as_dict()
    assert result['balanced'] is not None,result['notes']
    assert result['provisional_quote']['observed_physical_count']==1
    summary=panel.summarize_snapshot(artifact,snapshot_path=tmp_path/'producer.json')
    assert summary['reference']['action']=='算法暂估'
    assert all(summary['reference'][k]!='-' for k in ('conservative','balanced','aggressive'))


def test_scope_changes_invalidate_live_signature():
    from bidking_lab.live.live_refresh_signatures import ref_input_signature_from_snapshot
    s=snapshot(38)
    key=ref_input_signature_from_snapshot(s)
    for field,value in [('hero_id',206),('epoch','new'),('session_epoch',2),('map_id',6001)]:
        changed=copy.deepcopy(s);changed[field]=value
        assert ref_input_signature_from_snapshot(changed)!=key
    changed=copy.deepcopy(s)
    changed['minimap_grid_items']=[dict(runtime_id=1,quality=6,value=1)]
    value_key=ref_input_signature_from_snapshot(changed)
    changed['minimap_grid_items'][0]['value']=2
    assert ref_input_signature_from_snapshot(changed)!=value_key


@pytest.mark.parametrize('round_no',[0,1,2,3,4])
def test_each_round_quotes_real_facts_without_fabricating_items(round_no):
    reds,_,_=item_rows()
    s=snapshot(38)
    s['round']=s['ui_contract']['context']['round']=round_no
    if round_no>=1: reveal(s,100207,result=20)
    if round_no>=2:
        outline={k:v for k,v in reds[0].items() if k in ('runtime_id','cells','shape_code')}
        reveal(s,10002071,[outline],round_no=2)
    if round_no>=3:
        reveal(s,10002072,[dict(runtime_id=reds[0]['runtime_id'],quality=6)],round_no=3)
    if round_no>=4: reveal(s,4022,reds,round_no=4)
    out=engine.run_reference_engine(s).as_dict()
    assert out['balanced'] is not None,out['notes']
    assert out['provisional_quote']['observed_physical_count']==(2 if round_no==4 else 1 if round_no>=2 else 0)
    assert out['provisional_quote']['guarantee_violations']==0
    assert all(out['quality_count_cells_candidates'][q] for q in engine.QUALITY_KEYS)


@pytest.mark.parametrize('field,value',[('map_table_sha256','bad'),('owner_evidence_status','valid'),
    ('qualification','player_owned_warehouse'),('config',{'custom':True}),('map_source_contract_sha256','bad')])
def test_source_aliases_revoke(field,value):
    s=snapshot(38);s['warehouse_source'][field]=value
    assert engine.run_reference_engine(s).balanced is None


def test_rare_exact_count_uses_conditioning_not_random_hit():
    s=snapshot(38);s['structured_ref_inputs']['fixed_counts']={'q6':20}
    out=engine.run_reference_engine(s).as_dict()
    assert out['balanced'] is not None,out['notes']
    assert out['quality_count_ranges']['q6']==[20]*3


def test_public_negative_and_true_value_conflict_not_ignored():
    s=snapshot(38);s['structured_ref_inputs']['fixed_counts']={'q6':0}
    assert engine.run_reference_engine(s).balanced is None
    reds,_,_=item_rows()
    s=snapshot(3);reveal(s,100207,result=2);reveal(s,4022,reds,round_no=4)
    s['structured_ref_inputs']['quality_values']={'q6':1}
    assert engine.run_reference_engine(s).balanced is None


def test_existing_hard_physical_domain_is_consumed():
    reds,_,_=item_rows()
    row=reds[0]
    s=snapshot(38)
    s['physical_candidate_domains']=[dict(runtime_id=row['runtime_id'],local_index=0,cells=row['cells'],
        shape_key=str(row['shape_code']),required_categories=[106],excluded_categories=[],excluded_qualities=[],
        candidate_item_ids=[row['item_id']],candidate_qualities=[6],candidate_values=[row['value']],
        hard_eligible=True,catalog_status='ok',catalog_generation='physical-category-v1:fixture',
        observed_quality=6,quality=6,quality_source='observed',exact_item_id=row['item_id'],exact_value=row['value'])]
    assert engine.extract_evidence(s).physical_candidate_domains
    out=engine.run_reference_engine(s).as_dict()
    assert out['balanced'] is not None,out['notes']
    assert out['provisional_quote']['observed_physical_count']==1


def test_local_outline_to_runtime_identity_is_one_physical_slot():
    reds,_,_=item_rows()
    s=snapshot(38)
    reveal(s,10002071,[dict(local_index=8,cells=reds[0]['cells'],shape_code=reds[0]['shape_code'])],round_no=2)
    reveal(s,4022,[dict(reds[0],local_index=8)],round_no=4)
    out=engine.run_reference_engine(s).as_dict()
    assert out['balanced'] is not None,out['notes']
    assert out['provisional_quote']['observed_physical_count']==1


def test_true_antique_count_beyond_weak_prior_range_is_supported():
    s=snapshot();reveal(s,100207,result=101)
    out=engine.run_reference_engine(s).as_dict()
    assert out['balanced'] is not None,out['notes']
    assert out['provisional_quote']['antique_count_range']==[101]*3
    assert out['provisional_quote']['total_count_range'][0]>=101


@pytest.mark.parametrize('n',[None,38])
def test_quality_successor_real_joint_reaches_detail_consumer(n,tmp_path):
    import ahmad_live_panel_server as panel
    import detail_composition_view as view
    out=engine.run_reference_engine(snapshot(n)).as_dict()
    assert all(out['quality_count_cells_candidates'].get(q) for q in engine.QUALITY_KEYS)
    summary=panel.summarize_snapshot(snapshot(n),snapshot_path=tmp_path/'quality.json')
    assert summary['quality_composition']['status']=='ready'
    actual=view.detail_composition_view(summary)
    assert actual.status=='ready'
    assert all(q.status=='ready' and q.candidates for q in actual.qualities)
    assert all(q.lock_strength=='probabilistic' for q in actual.qualities)
    assert all(view.quality_count_triplet(q) and view.quality_dimension_triplet(q,'cells') for q in actual.qualities)


@pytest.mark.parametrize('scenario',['r0','n38','r1','r4partial','r4full'])
def test_quality_successor_projection_preserves_frozen_price_posterior(scenario):
    import importlib.util
    import wuqilin_5215_quote_model as model
    path=Path('C:/example/bidking-workspace/scratch/bidking_035_5215_quote_source_20260913_01/successor_quality01/before/hero_ref/src/wuqilin_5215_quote_model.py')
    spec=importlib.util.spec_from_file_location('quality_before_model',path)
    before=importlib.util.module_from_spec(spec);spec.loader.exec_module(before)
    s=snapshot(None if scenario=='r0' else 38)
    if scenario in ('r1','r4partial','r4full'): reveal(s,100207,result=20)
    if scenario in ('r4partial','r4full'):
        reds,others,_=item_rows()
        reveal(s,4022,reds[:1] if scenario=='r4partial' else reds+others,round_no=4)
    ev=engine.extract_evidence(s)
    peers=model.load_model(lambda p:(ROOT/p).read_bytes())
    a,obs=model.observations(s,engine._iter_skill_reveal_dict_rows(s),ev.physical_candidate_domains)
    old=before.posterior(peers,ev,a,obs)
    new=model.posterior(peers,ev,a,obs)
    assert all(new[key]==value for key,value in old.items())


def test_quality_successor_joint_aggregation_does_not_cross_pair_or_false_lock():
    import numpy as np
    import wuqilin_5215_quote_model as model
    import ahmad_live_panel_server as panel
    count=np.array([[3,0,1,0,2],[0,3,1,0,2]])
    cell=np.array([[9,0,2,0,6],[0,12,2,0,8]])
    block=model._quality_display_posterior(count,cell,np.array([100,200]),np.array([.75,.25]),engine.extract_evidence(snapshot(6)),())
    assert block['quality_count_cells_candidates']['q1']==((3,9,.75),(0,0,.25))
    assert block['quality_count_cells_candidates']['q6']==((2,6,.75),(2,8,.25))
    assert block['quality_lock']['q6']['strength']=='probabilistic'
    assert not block['quality_display']['exact_counts']
    assert all(sum(arm)==6 for arm in block['quality_count_arms'])


@pytest.mark.parametrize('fixed,cells,expected',[({'q6':2},{'q6':6},('q6',2,6)),({'q5':0},{},('q5',0,0))])
def test_quality_successor_actual_zero_and_exact_dimensions(fixed,cells,expected,tmp_path):
    import ahmad_live_panel_server as panel
    import detail_composition_view as view
    s=snapshot(38);s['structured_ref_inputs'].update(fixed_counts=fixed,quality_cells=cells)
    summary=panel.summarize_snapshot(s,snapshot_path=tmp_path/'exact-quality.json')
    block=summary['quality_composition']['qualities'][expected[0]]
    assert block['status']=='exact'
    assert (block['count_lock_value'],block['cells_lock_value'])==expected[1:]
    quality=next(q for q in view.detail_composition_view(summary).qualities if q.key==expected[0])
    assert view.quality_is_strict_exact(quality)


def test_quality_successor_wrong_model_does_not_open_other_provisional():
    import ahmad_live_panel_server as panel
    out=engine.run_reference_engine(snapshot(38)).as_dict()
    out['source']='auction_hall_6001_shadow'
    out['provisional_quote']['map_id']=6001
    assert panel._quality_composition_payload(out)['status']=='unavailable'


def test_quality_successor_real_tk_rows_and_probability_consumer(monkeypatch,tmp_path):
    import tkinter as tk
    import ahmad_tk_overlay as overlay
    import ahmad_live_panel_server as panel
    import detail_composition_view as view
    for name in ('_start_activation_worker','_poll_startup_update_notice','refresh'):
        monkeypatch.setattr(overlay.AhmadTkOverlay,name,lambda self:None)
    monkeypatch.setattr(overlay.AhmadTkOverlay,'_activation_product_denied',lambda self:False)
    root=tk.Tk()
    try:
        ov=overlay.AhmadTkOverlay(root,snapshot_path=tmp_path/'ui.json',interval_ms=100000,ui_prefs_enabled=False)
        s=snapshot(38)
        summary=panel.summarize_snapshot(s,snapshot_path=tmp_path/'ui.json')
        ov._apply_live_summary(s,summary,force=True);root.update_idletasks();root.update()
        assert ov._detail_composition_view.status=='ready'
        assert ov.red_count_triplet_frame.winfo_ismapped()
        assert all(label.winfo_ismapped() and '—' not in str(label.cget('text')) for label in ov.red_count_triplet_labels.values())
        for key in ('红格','金品','紫品','蓝品','白绿'):
            assert ov.red_rows[key].winfo_ismapped()
            assert str(ov.red_rows[key].cget('text')) not in ('-','—','—格','—件 —格')
        assert all(q.candidates and any(candidate.probability>0 for candidate in q.candidates)
                   for q in ov._detail_composition_view.qualities)
        assert '%' in ov.red_count_triplet_labels['middle']._hover_tip.text
        assert '%' in ov.red_rows['金品']._hover_tip.text
        assert ov.quality_state_badges['红件'].cget('text')=='估'
        assert ov.quality_state_badges['红格'].cget('text')=='估'
        ov.toggle_details();root.update_idletasks();root.update()
        assert ov._detail_composition_view.status=='ready'
        for row in ov.detail_composition_rows[:5]:
            assert row['count'].cget('text')!='-'
            assert row['cells'].cget('text')!='-'
            assert '%' in row['probability'].cget('text')
        ov._select_detail_composition_filter('q6');root.update_idletasks();root.update()
        footer=str(ov.detail_composition_footer.cget('text'))
        assert '候选覆盖' in footer and '其他' in footer and '本页8项' in footer
        import tkinter.font as tkfont
        assert tkfont.Font(font=ov.detail_composition_footer.cget('font')).measure(footer)<=ov.detail_composition_footer.winfo_width()
        exact=snapshot(38)
        exact['structured_ref_inputs'].update(fixed_counts={'q5':0,'q6':2},quality_cells={'q6':6})
        exact_summary=panel.summarize_snapshot(exact,snapshot_path=tmp_path/'ui-exact.json')
        ov._apply_live_summary(exact,exact_summary,force=True);root.update_idletasks();root.update()
        assert '0' in str(ov.red_rows['金品'].cget('text'))
        assert ov.quality_state_badges['红件'].cget('text')=='锁'
        assert ov.quality_state_badges['红格'].cget('text')=='锁'
    finally:
        root.destroy()
