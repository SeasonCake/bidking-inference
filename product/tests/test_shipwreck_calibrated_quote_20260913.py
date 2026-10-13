from __future__ import annotations
import copy,hashlib,json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'hero_ref/src'),str(ROOT/'hero_ref/tools')]
import ahmad_ref_engine as engine
import ahmad_live_panel_server as panel
from bidking_lab.live.warehouse_source import classify_from_map_table
from bidking_lab.runtime.snapshot import ui_contract_from_artifact
GEN='b049c3-2026-09-10-597'
def snapshot(map_id=2511,n=38):
    maps={r['map_id']:r for r in json.loads((ROOT/'data/processed/maps.json').read_bytes())}
    source=classify_from_map_table(map_id,maps,source_generation=GEN,profile_generation=GEN,
        game_version=GEN,owner_evidence_status='absent').as_dict()
    s=dict(map_id=map_id,model_map_id=map_id,hero='ahmed',hero_id=204,session_id=f'{map_id}:9000000000000059',
        phase='bidding',round=3,action_round=3,observed_round=3,warehouse_source=source,
        warehouse_source_generation=GEN,warehouse_source_qualification=source['qualification'],
        official_distribution_eligible=source['official_distribution_eligible'],warehouse_game_version_session_stable=True,
        structured_ref_inputs={} if n is None else dict(total_count=n))
    s['ui_contract']=ui_contract_from_artifact(s)
    return s

@pytest.mark.parametrize('map_id',[2511,4511])
def test_current_both_maps_use_train_in_normal_engine(map_id):
    s=snapshot(map_id)
    out=engine.run_reference_engine(engine.prepare_reference_engine_snapshot(s)).as_dict()
    assert out['balanced'] is not None,out['notes']
    assert out['empirical_calibration']['map_id']==map_id
    assert 'provisional_quote' not in out
    assert not any('uncalibrated' in note for note in out['notes'])

@pytest.mark.parametrize('map_id',[2511,4511])
def test_valid_or_malformed_owner_never_gets_normal_prior(map_id):
    for status in ('valid','malformed_conflict'):
        s=snapshot(map_id);s['warehouse_source']['owner_evidence_status']=status
        assert engine.run_reference_engine(s).balanced is None


def test_tiny_parameters_are_exact_selected_train_values():
    data=ROOT/'data/processed/shipwreck_calibrated_b049.json'
    assert (data.stat().st_size,hashlib.sha256(data.read_bytes()).hexdigest())==(engine.SHIPWRECK_CALIBRATED_BYTES,engine.SHIPWRECK_CALIBRATED_SHA256)
    d=Path('C:/example/bidking-workspace/scratch/bidking_035_cloud_calibration_20260913_01')
    for mid in (2511,4511):
        candidate=json.loads((d/f'CANDIDATE_{mid}.json').read_bytes())
        model=engine._shipwreck_calibrated_model(mid)
        assert model['count_mean']==candidate['train']['N']['mean']
        assert model['count_sd']==candidate['train']['N']['sd']
        for key in ('quality_probs','quality_values','quality_cells'):
            assert model[key]==candidate['train'][key]
    assert engine._shipwreck_calibrated_model(2501) is None
    assert engine._shipwreck_calibrated_model(4501) is None
    assert engine._shipwreck_calibrated_model(2512) is None


@pytest.mark.parametrize('mid',[2511,4511])
def test_initial_prior_does_not_exhaust_budget_on_small_N(mid,tmp_path):
    s=snapshot(mid,None)
    out=engine.run_reference_engine(s,max_combos=5000).as_dict()
    assert out['balanced'] is not None,out['notes']
    assert 40<=out['total_count_estimate']<=49
    summary=panel._hero_single_pass_ref_result(s,hero_key='ahmed',round_no=3,public_info={})
    assert summary['balanced'] is not None
    assert not summary.get('provisional_quote')


def test_old_sidecar_never_read_and_changed_calibration_revokes(monkeypatch):
    original=engine._aisha_q5_total_cells_plaintext_bytes
    calls=[]
    def reader(path):
        calls.append(str(path))
        assert not str(path).endswith('activity_2511_provisional_b049.json')
        return original(path)
    monkeypatch.setattr(engine,'_aisha_q5_total_cells_plaintext_bytes',reader)
    s=snapshot(2511,38)
    out=engine.run_reference_engine(s,max_combos=3000).as_dict()
    assert out['balanced'] is not None
    for bad in (b'{}',None):
        def changed(path):
            if str(path).endswith('shipwreck_calibrated_b049.json'):
                if bad is None:raise FileNotFoundError('synthetic missing input')
                return bad
            return original(path)
        monkeypatch.setattr(engine,'_aisha_q5_total_cells_plaintext_bytes',changed)
        assert engine.run_reference_engine(s).balanced is None
        assert panel.activity_2511_provisional_quote_revoked(s,{'ahmed_ref':out})


@pytest.mark.parametrize('mid',[2511,4511])
@pytest.mark.parametrize('field,value',[('session_id','2511:old'),('model_map_id',2505),('warehouse_game_version_session_stable',False),('phase','settled')])
def test_current_raw_aliases_and_partial_settled_boundaries(mid,field,value):
    s=snapshot(mid);s[field]=value
    assert engine.run_reference_engine(s).balanced is None
    assert engine._SHIPWRECK_CALIBRATED_PRIOR.get() is None


def test_empirical_context_restored_after_exception_and_other_map(monkeypatch):
    from types import SimpleNamespace
    seen=[]
    @engine._shipwreck_calibrated_context
    def probe(s):
        seen.append(engine._SHIPWRECK_CALIBRATED_PRIOR.get())
        return SimpleNamespace(balanced=None)
    probe(snapshot(2511));probe(snapshot(4511));probe({'map_id':2501})
    assert [row['map_id'] if row else None for row in seen]==[2511,4511,None]
    assert engine._SHIPWRECK_CALIBRATED_PRIOR.get() is None
    def fail(*args,**kwargs):
        assert engine._SHIPWRECK_CALIBRATED_PRIOR.get()['map_id']==2511
        raise RuntimeError('synthetic engine exception')
    monkeypatch.setattr(engine,'_run_reference_engine_impl',fail)
    with pytest.raises(RuntimeError):engine.run_reference_engine(snapshot(2511))
    assert engine._SHIPWRECK_CALIBRATED_PRIOR.get() is None


def test_grid_cache_is_bound_to_calibration_and_normal_default():
    from types import SimpleNamespace
    evidence=engine.extract_evidence({'hero':'ahmed','structured_ref_inputs':{'total_count':10}})
    counts=dict(zip(engine.QUALITY_KEYS,(1,1,1,7,0)))
    @engine._shipwreck_calibrated_context
    def grid(s):
        return SimpleNamespace(balanced=None,options=engine._display_grid_options_for_quality(counts,evidence,'q5'))
    baseline=grid({'map_id':2501}).options
    one=grid(snapshot(2511)).options;two=grid(snapshot(4511)).options
    assert one!=baseline and two!=baseline
    assert grid({'map_id':2501}).options==baseline
    assert grid(snapshot(2511)).options==one


def test_real_observed_count_value_and_cells_override_parameters():
    s=snapshot(2511,3)
    s['structured_ref_inputs'].update(fixed_counts={'q1':0,'q3':0,'q4':0,'q5':1,'q6':2},
        quality_cells={'q5':4,'q6':6},quality_values={'q5':50000,'q6':300000})
    s['ui_contract']=ui_contract_from_artifact(s)
    low=engine.run_reference_engine(s).as_dict()
    assert low['balanced'] is not None,low['notes']
    assert low['evidence']['total_count']==3
    high=copy.deepcopy(s);high['structured_ref_inputs']['quality_values']['q6']=800000
    high['ui_contract']=ui_contract_from_artifact(high)
    new=engine.run_reference_engine(high).as_dict()
    assert new['value_p50']>low['value_p50']
    assert new['quality_count_ranges']['q6']==[2]*3
    assert new['quality_cells_ranges']['q6']==[6]*3


@pytest.mark.parametrize('mid',[2511,4511])
def test_actual_monitor_producer_to_normal_summary(mid,monkeypatch,tmp_path):
    from bidking_lab.live import fatbeans as fb,monitor
    from bidking_lab.extract.bid_map_table import parse_bid_map_table
    raw=Path('C:/example/bidking-workspace/scratch/bidking_035_small_hotfix_20260910_01/official_b049_static01/textassets/BidMap.txt').read_text(encoding='utf-8-sig')
    maps=parse_bid_map_table([line.split('\t') for line in raw.splitlines()])
    tables=monitor.MonitorTables(maps=maps,drops={},items={},warehouse_package_generation=GEN,
        warehouse_game_version=GEN,warehouse_live_identity_check=True)
    state=fb.FatbeansStateEvent(sort_id=1,capture_time='2026-09-13T14:00:00+08:00',message_id=0x21,
        session_id=f'{mid}:9000000000000059',map_id=mid,round_index=1,
        skill_reveals=(fb.FatbeansSkillReveal(skill_id=100204,hero_id=204,round_index=1),),
        public_infos=(fb.FatbeansPublicInfo(info_id=200017,map_id=mid,value=38,value_field=2),))
    events=fb.FatbeansCaptureEvents(packets=(),frames=(),sends=(),states=(state,),statuses=())
    monkeypatch.setattr(monitor,'_fresh_session_game_version',lambda *_:(GEN,True))
    artifact=monitor.build_monitor_artifact_from_events(events,tables=tables,n_trials=1,roi_trials=1,run_debug_shadows=False)
    assert artifact['model_map_id']==mid
    assert artifact['formal_mode_reason']=='shipwreck_calibrated_ref_only'
    summary=panel.summarize_snapshot(artifact,snapshot_path=tmp_path/f'producer-{mid}.json')
    out=summary['ahmed_ref']
    assert out['balanced'] is not None,out['notes']
    assert out['evidence']['total_count']==38
    assert out['empirical_calibration']['map_id']==mid
    assert '暂估' not in summary['reference']['action']
    assert not any('未校准' in f['label'] for f in summary['flags'])


@pytest.mark.parametrize('mid',[2511,4511])
def test_epoch_and_raw_source_alias_updates_revoke(mid):
    from bidking_lab.live.live_refresh_signatures import ref_input_signature_from_snapshot
    s=snapshot(mid);base=ref_input_signature_from_snapshot(s)
    for field,value in [('epoch',2),('session_epoch',3)]:
        changed=copy.deepcopy(s);changed[field]=value;changed['ui_contract']['context'][field]=value+1
        assert ref_input_signature_from_snapshot(changed)!=base
        assert engine.run_reference_engine(changed).balanced is None


@pytest.mark.parametrize('mid',[2511,4511])
def test_direct_and_prepared_agree_for_same_real_constraints(mid):
    s=snapshot(mid)
    s['structured_ref_inputs'].update(fixed_counts={'q1':8,'q3':9},quality_cells={'q1':9,'q3':19})
    s['ui_contract']=ui_contract_from_artifact(s)
    direct=engine.run_reference_engine(s,max_combos=3000).as_dict()
    prepared=engine.run_reference_engine(engine.prepare_reference_engine_snapshot(s),max_combos=3000).as_dict()
    assert direct['balanced'] is not None
    for key in ('conservative','balanced','aggressive','quality_count_ranges','quality_cells_ranges','empirical_calibration'):
        assert direct[key]==prepared[key]


@pytest.mark.parametrize('sid',['0c5e0d26269fa9bf','3fe7f18137019cbc'])
def test_named_settlement_aggregates_are_not_pulled_from_exact_values(sid):
    # These are post-settlement projection controls in a synthetic live envelope;
    # they are not reconstructed prebid replay or an accuracy test.
    raw=Path('C:/example/bidking-workspace/scratch/bidking_035_cloud_calibration_20260913_01/QUALIFIED_ROWS.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest()=='cbf3cd2144c446dc609d1e6e99b1f8fc989601136e28c707feb9fba1f7211e8d'
    row=next(r for r in json.loads(raw) if r['session_hash']==sid)
    s=snapshot(row['map_id'],row['N'])
    s['structured_ref_inputs'].update(fixed_counts=row['counts'],quality_cells=row['cells'],quality_values=row['values'])
    s['ui_contract']=ui_contract_from_artifact(s)
    out=engine.run_reference_engine(s).as_dict()
    assert out['balanced'] is not None,out['notes']
    assert out['evidence']['total_count']==row['N']
    assert out['value_p50']==row['V']


@pytest.mark.parametrize('mid',[2501,4501,2512,4512,5215])
def test_unselected_maps_and_5215_match_frozen_before_engine(mid):
    import importlib.util
    path=Path('C:/example/bidking-workspace/scratch/bidking_035_shipwreck_quote_source_20260913_01/before/hero_ref/src/ahmad_ref_engine.py')
    spec=importlib.util.spec_from_file_location('engine_before_shipwreck',ROOT/'hero_ref/src/ahmad_ref_engine.py')
    before=importlib.util.module_from_spec(spec);sys.modules[spec.name]=before
    exec(compile(path.read_bytes(),str(ROOT/'hero_ref/src/ahmad_ref_engine.py'),'exec'),before.__dict__)
    if mid==5215:
        from test_wuqilin_5215_quote_model_20260913 import snapshot as wu_snapshot
        s=wu_snapshot(38)
    else:
        s=snapshot(mid,38)
        s['structured_ref_inputs']['fixed_counts']=dict(zip(engine.QUALITY_KEYS,(8,9,12,6,3)))
        s['ui_contract']=ui_contract_from_artifact(s)
    assert engine.run_reference_engine(s).as_dict()==before.run_reference_engine(s).as_dict()
