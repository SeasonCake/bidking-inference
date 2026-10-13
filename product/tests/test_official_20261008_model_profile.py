"""Exact source/model regeneration, with independent qualification and conservation checks."""
from __future__ import annotations
import base64
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import pytest
from bidking_lab.extract import official_20261008_model_profile as profile
from bidking_lab.extract.item_table import load_item_table, parse_item_table
from bidking_lab.data.item_category_authority import validate_authority_document, ItemCategoryAuthorityError

PRODUCT=Path(__file__).resolve().parents[1]
R=Path(os.environ.get('BIDKING_20261008_CONTENT_ROOT','C:/example/bidking-workspace/scratch/bidking_036_intake_20261011_01/content'))

@pytest.fixture(scope='module')
def inputs():
    texts={n:(R/'textassets'/f'{n}.txt').read_bytes() for n in profile.TEXT_ASSET_IDENTITIES}
    baseline={p:(R/'src2a_baseline'/p).read_bytes() for p in profile.BASELINE_IDENTITIES}
    return texts,baseline

@pytest.fixture(scope='module')
def built(inputs):
    texts,baseline=inputs
    return profile.build_inputs(text_assets=texts,baseline_inputs=baseline)

def value(outputs,name):
    return json.loads(outputs[f'data/processed/{name}.json'])

def test_explicit_profile_selection_and_exact_full_regeneration(built):
    outputs,receipt=built
    assert profile.select_model_profile('76b7ca-2026-10-08-723') is profile.PROFILE
    for invalid in ('b049c3-2026-09-10-597','76b7ca-2026-10-08-723-extra',None,False):
        assert profile.select_model_profile(invalid) is None
    assert profile.PACKAGE_BASELINE_ID=='package-data-2026-10-08-76b7ca-0.3.6'
    assert profile.CURRENT_RANKMAP_MAP_IDS==(*range(2101,2108),*range(5601,5612))
    assert len(outputs)==12
    # Independent initial reconstruction had these two catalog/data pins.
    assert profile.identity(outputs['data/processed/items.json'])=={'bytes':737910,'sha256':'7be0cb441b7b63e87e08d3f15ede1d3f4e2435e299715b13ff02fcc629287bab'}
    assert profile.identity(outputs['data/processed/items_droppable.json'])=={'bytes':322888,'sha256':'68e944020749968d301dca61ff2b12fccd21b32d93c22b72d394285cd9658f91'}
    assert receipt['lifecycle']=='qualified_model_inputs_not_built_or_runtime_verified'
    for path,raw in outputs.items():
        assert raw==(R/'src2a_discovery'/path).read_bytes()

@pytest.mark.parametrize('section,name',[('text','Item'),('text','RankMap'),('baseline','data/processed/items_droppable.json'),('baseline','data/processed/drop_weights_all_maps_0627.json')])
@pytest.mark.parametrize('mutation',['missing','wrong-byte'])
def test_inputs_fail_before_any_model_work(inputs,monkeypatch,section,name,mutation):
    texts,baseline=map(dict,inputs)
    selected=texts if section=='text' else baseline
    if mutation=='missing': selected.pop(name)
    else: selected[name]=selected[name]+b'!'
    def forbidden(*args,**kwargs): pytest.fail('invalid input entered fitting')
    monkeypatch.setattr(profile.legacy,'_current_item_fitted_values',forbidden)
    with pytest.raises(profile.OfficialModelInputError): profile.build_inputs(text_assets=texts,baseline_inputs=baseline)

def test_lookup_preserves_real_qualities_names_and_prices(built,inputs):
    outputs,_=built
    catalog={v['item_id']:v for v in value(outputs,'items')}
    old=json.loads(inputs[1]['data/processed/items.json'])
    raw={i:v for i,v in parse_item_table([l.split('\t') for l in inputs[0]['Item'].decode('utf-8-sig').splitlines()]).items()}
    assert len(catalog)==1506 and len(value(outputs,'maps'))==246
    assert all(catalog[r['item_id']]['name']==r['name'] for r in old)
    assert all(catalog[i]['quality']==v.quality and catalog[i]['value']==v.value and (catalog[i]['shape_w'],catalog[i]['shape_h'])==(v.shape_w,v.shape_h) for i,v in raw.items())
    guild=[v for v in catalog.values() if 1401001<=v['item_id']<=1404012 and v['tags']==[111]]
    assert len(guild)==48 and {v['quality'] for v in guild}=={3}

def test_qualified_lifecycle_and_real_model_scope(built):
    outputs,_=built
    union={v['item_id'] for v in value(outputs,'items_droppable')}
    assert len(union)==690
    assert union.isdisjoint(profile.EXCLUDED_MODEL_IDS)
    assert set(profile.ADDED_HALLOWEEN_IDS)<=union
    assert not union&set(range(1401001,1404013))
    assert 1306019 not in union
    ordinary=value(outputs,'drop_weights_all_maps_0627')
    ranked=value(outputs,'rankmap_drop_weights_current')
    assert len(ordinary)==101 and len(ranked)==18
    assert set(ranked)=={str(i) for i in profile.CURRENT_RANKMAP_MAP_IDS}
    assert '6001' not in ordinary and '6001' not in ranked
    assert not {'2511','4511'}&ordinary.keys()
    for mid,entry in {**ordinary,**ranked}.items():
        ids={int(i) for i in entry['items']}
        s2=5601<=int(mid)<=5611
        assert len(ids)==entry['n_items']==(684 if s2 else 675)
        assert ids.isdisjoint(profile.EXCLUDED_MODEL_IDS)
        assert bool(ids&set(profile.ADDED_HALLOWEEN_IDS)) is (not s2)

def test_all_probabilities_priors_and_unchanged_lower_qualities(built,inputs):
    outputs,_=built
    catalog={v['item_id']:v for v in value(outputs,'items')}
    for wn,pn in [('drop_weights_all_maps_0627','drop_table_priors_0627'),('rankmap_drop_weights_current','rankmap_drop_priors_current')]:
        before=json.loads(inputs[1][f'data/processed/{wn}.json'])
        priors_before=json.loads(inputs[1][f'data/processed/{pn}.json'])
        weights=value(outputs,wn);priors=value(outputs,pn)
        assert len(priors)==(72 if wn.startswith('drop_weights') else 18)
        for mid,entry in weights.items():
            assert math.isclose(sum(x[0] for x in entry['items'].values()),1,abs_tol=2e-11)
            assert entry['count']==before[mid]['count']==16.0
            for i,triple in entry['items'].items():
                assert triple[1]==catalog[int(i)]['value']
                if catalog[int(i)]['quality']<6:
                    previous=before[mid]['items'][i]
                    assert triple[1:]==previous[1:]
                    # Maps without a stored prior infer bucket mass from the
                    # old rounded triples. One final 12-decimal quantum can move.
                    assert abs(triple[0]-previous[0])<=1.01e-12
            if mid in priors:
                assert priors[mid]['quality_probs']==priors_before[mid]['quality_probs']
                assert all(priors[mid]['quality_values'][q]==priors_before[mid]['quality_values'][q] for q in ('q1','q3','q4','q5'))
                for q,mass in priors[mid]['quality_probs'].items():
                    observed=sum(x[0] for i,x in entry['items'].items() if ('q1' if catalog[int(i)]['quality']<=2 else f"q{catalog[int(i)]['quality']}")==q)
                    # Existing seven-decimal empirical masses can sum to
                    # 0.9999999. The established sampler normalizes that sum.
                    expected_mass=mass/sum(priors[mid]['quality_probs'].values())
                    assert math.isclose(observed,expected_mass,abs_tol=2e-11)
    assert value(outputs,'drop_table_priors_0627')['2501']['quality_values']['q6']==162779
    assert value(outputs,'rankmap_drop_priors_current')['5601']['quality_values']['q6']==144891

def test_model_authority_and_category_are_not_server_open_claims(built):
    outputs,_=built
    authority=value(outputs,'rankmap_distribution_authority_current')
    assert authority['schema']=='bidking_official_20261008_model_authority_v1'
    assert authority['model_distribution_authority'] is True and authority['official_distribution_authority'] is False
    assert authority['runtime_open_claim'] is False
    document=value(outputs,'item_category_authority_0_3_2')
    validate_authority_document(document,expected_item_table_sha256='125550adca27edc14378886d7ab04c7b0b537015a1c59906e374c0659c1d4372')
    assert document['authority_identity']=='36600b5116701a8152af992e51e1c7d6a0492fed0e841d36c79e0de285f89dde'
    rows={r['item_id']:r for r in document['records']}
    assert len(rows)==1506 and sum(r['current_random_drop_eligible'] for r in rows.values())==690
    assert rows[1401001]['quality']==3 and rows[1401001]['current_random_drop_eligible'] is False
    assert rows[1016015]['current_random_drop_eligible'] is True
    assert rows[1916001]['current_random_drop_eligible'] is False
    bad=json.loads(json.dumps(document));bad['sources']['item_table']['sha256']='0'*64
    with pytest.raises(ItemCategoryAuthorityError,match='exact 76b7ca'): validate_authority_document(bad)

def test_new_display_overlay_requires_the_new_full_catalog(built,inputs,tmp_path):
    outputs,_=built
    root=tmp_path/'product'
    for path in ('data/raw/tables/Item.txt','data/processed/items.json'):
        p=root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(outputs[path])
    loaded=load_item_table(root/'data/raw/tables/Item.txt')
    expected={v['item_id']:v['name'] for v in value(outputs,'items')}
    assert {i:v.name for i,v in loaded.items()}==expected
    (root/'data/processed/items.json').write_bytes(inputs[1]['data/processed/items.json'])
    with pytest.raises(ValueError,match='identity'): load_item_table(root/'data/raw/tables/Item.txt')

def _script(relative,name):
    spec=importlib.util.spec_from_file_location(name,PRODUCT/relative)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def test_legacy_regeneration_clis_refuse_new_support(built,inputs,monkeypatch,tmp_path):
    rates=_script('scripts/calibration/rankmap_drop_model.py','new_guard_rates')
    rates.ROOT=tmp_path
    p=tmp_path/'data/raw/tables/Item.txt';p.parent.mkdir(parents=True);p.write_bytes(built[0]['data/raw/tables/Item.txt'])
    with pytest.raises(ValueError,match='76b7ca'): rates.drop_rates(2501)
    rebuild=_script('scripts/calibration/rebuild_items_droppable.py','new_guard_rebuild')
    rows=[l.split('\t') for l in inputs[0]['Item'].decode('utf-8-sig').splitlines()]
    monkeypatch.setattr(rebuild,'load_item_table',lambda path:parse_item_table(rows))
    rebuild.OUT=tmp_path/'not-written.json'
    with pytest.raises(ValueError,match='76b7ca'): rebuild.main()
    assert not rebuild.OUT.exists()

def test_runtime_member_validation_rejects_wrong_or_partial_outputs(built):
    outputs,_=built
    bad=dict(outputs);bad.pop('data/processed/items_droppable.json')
    with pytest.raises(profile.OfficialModelInputError): profile.validate_profile_outputs(bad,generation=profile.GENERATION)
    bad=dict(outputs);bad['data/processed/drop_table_priors_0627.json']+=b'!'
    with pytest.raises(profile.OfficialModelInputError): profile.validate_profile_outputs(bad,generation=profile.GENERATION)
    with pytest.raises(profile.OfficialModelInputError): profile.validate_profile_outputs(outputs,generation='b049c3-2026-09-10-597')

def test_copy_only_cli_build_check_and_missing_input_failure(tmp_path):
    script=PRODUCT/'scripts/calibration/build_official_20261008_inputs.py'
    out=tmp_path/'candidate';receipt=tmp_path/'receipt.json'
    command=[sys.executable,'-B',str(script),'--textassets-root',str(R/'textassets'),'--baseline-root',str(R/'src2a_baseline'),'--output-root',str(out),'--receipt-out',str(receipt)]
    first=subprocess.run(command,capture_output=True,text=True,timeout=20)
    assert first.returncode==0,first.stderr
    check=subprocess.run([*command,'--check'],capture_output=True,text=True,timeout=20)
    assert check.returncode==0,check.stderr
    absent=tmp_path/'absent';absent.mkdir()
    failed_command=[sys.executable,'-B',str(script),'--textassets-root',str(absent),'--baseline-root',str(R/'src2a_baseline'),'--output-root',str(tmp_path/'must-not-exist'),'--receipt-out',str(tmp_path/'bad-receipt.json')]
    failed=subprocess.run(failed_command,capture_output=True,text=True,timeout=20)
    assert failed.returncode==2 and 'input-assembly-not-ready' in failed.stderr
    assert not (tmp_path/'must-not-exist').exists() and not (tmp_path/'bad-receipt.json').exists()
