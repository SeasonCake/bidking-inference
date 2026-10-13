from __future__ import annotations
import ast,json,math,os,sys,textwrap
from functools import lru_cache
from dataclasses import replace
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'hero_ref/tools'))
import detail_composition_view as view

@lru_cache(maxsize=1)
def formatter():
    source=(ROOT/'hero_ref/tools/ahmad_tk_overlay.py').read_text(encoding='utf-8');tree=ast.parse(source)
    ns={**vars(view),'Any':object};methods={}
    for name in ('_text','_hover_candidate_block','_hover_probability_blocks','_structured_quality_candidate_block','_joint_quality_is_exact','_lock_triple','_red_hover'):
        matches=[n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name==name];assert len(matches)==1
        exec(compile('from __future__ import annotations\n'+textwrap.dedent(ast.get_source_segment(source,matches[0])),name,'exec'),ns)
        if name in ('_joint_quality_is_exact','_lock_triple','_red_hover'):
            methods[name]=staticmethod(ns[name]) if name=='_joint_quality_is_exact' else classmethod(ns[name]) if name=='_lock_triple' else ns[name]
    return ns,type('ActualHoverFormatter',(),methods)()

@pytest.fixture(scope='module')
def inputs():
    locator=os.environ.get('BIDKING_QUALITY_HOVER_FIXTURE_JSON')
    if locator:return json.loads(Path(locator).read_bytes())
    # Standalone regression input; the owner run separately supplies exact N38.
    qualities={key:dict(status='ready',source='pricing_posterior',lock_strength='probabilistic',lock_reason='sampled_weighted_posterior',
        candidates=[dict(count=c,cells=s,probability=p) for c,s,p in [(3,6,.2),(5,19,.3),(7,25,.2)]],
        count_candidates=[dict(value=v,probability=p) for v,p in [(3,.2),(5,.5),(7,.3)]],
        cells_candidates=[dict(value=v,probability=p) for v,p in [(6,.2),(19,.5),(25,.3)]],
        shown_probability=.7,other_probability=.3,truncated=True,cap_hit=False,completion_flags_complete=True,pricing_posterior=True,display_reconciled=False,reason='',count_correlation_state='independent',count_correlation_note='',compact_count_candidates=[],compact_count_triplet=[]) for key in ('q1','q3','q4','q5','q6')}
    return dict(summary=dict(quality_composition=dict(schema_version=1,status='ready',reason='',qualities=qualities),red=dict(count_range='3 / 4 / 5',cells_range='11 / 17 / 24'),reference={}))

@pytest.mark.parametrize('key',['q6','q3','q1'])
@pytest.mark.parametrize('dimension,unit',[('count','件'),('cells','格')])
def test_full_marginal_hover_does_not_add_joint_other_mass(inputs,key,dimension,unit):
    q=view.detail_composition_view(inputs['summary']).quality(key)
    rows=getattr(q,dimension+'_candidates');assert math.fsum(x.probability for x in rows)==pytest.approx(1,abs=1e-12)
    assert q.other_probability>0
    ns,_=formatter();block=ns['_structured_quality_candidate_block'](q,dimension=dimension,heading='candidate',unit=unit)
    assert '其他 ' not in block

@pytest.mark.parametrize('dimension,unit,displayed',[('count','件',[3,5,7]),('cells','格',[6,19,25])])
def test_displayed_red_triplet_matches_hover_labeled_triplet(inputs,dimension,unit,displayed):
    q=view.detail_composition_view(inputs['summary']).quality('q6');_,obj=formatter()
    text=obj._red_hover('红件' if dimension=='count' else '红格',inputs['summary']['red'],inputs['summary']['reference'],quality=q)
    assert [getattr(row,dimension) for row in view.quality_paired_candidate_triplet(q)]==displayed
    assert f'保守 {displayed[0]} / 中位 {displayed[1]} / 激进 {displayed[2]}{unit}' in text
    assert '实际范围' not in text

def test_truncated_joint_fallback_positive_control(inputs):
    q=view.detail_composition_view(inputs['summary']).quality('q6');q=replace(q,count_candidates=(),cells_candidates=())
    ns,_=formatter();text=ns['_structured_quality_candidate_block'](q,dimension='count',heading='candidate',unit='件')
    assert '其他 ' in text
    assert math.fsum(x.probability for x in q.candidates)+q.other_probability==pytest.approx(1,abs=1e-12)

def test_exact_zero_marginal_positive_control(inputs):
    q=view.detail_composition_view(inputs['summary']).quality('q5')
    q=replace(q,status='exact',lock_strength='exact',shown_probability=1.0,truncated=False,cap_hit=False,completion_flags_complete=True,candidates=(view.CompositionCandidate(0,0,1.0),),count_candidates=(view.MarginalCandidate(0,1.0),),cells_candidates=(view.MarginalCandidate(0,1.0),),other_probability=0.0)
    ns,_=formatter()
    for dimension,unit in [('count','件'),('cells','格')]:
        text=ns['_structured_quality_candidate_block'](q,dimension=dimension,heading='candidate',unit=unit)
        assert f'0{unit} 100%' in text and '其他 ' not in text

@pytest.mark.parametrize('key',['q6','q3','q1'])
@pytest.mark.parametrize('dimension,unit',[('count','件'),('cells','格')])
def test_compact_rows_preserve_every_probability_unit_and_order(inputs,key,dimension,unit):
    q=view.detail_composition_view(inputs['summary']).quality(key);rows=getattr(q,dimension+'_candidates')
    ns,_=formatter();text=ns['_structured_quality_candidate_block'](q,dimension=dimension,heading='candidate',unit=unit)
    actual=[entry.strip() for line in text.splitlines()[1:] for entry in line.split(' · ')]
    expected=[f'{row.value}{unit} {view.format_quality_probability(q,row.probability)}' for row in rows]
    assert actual==expected
    assert len(text.splitlines())==1+math.ceil(len(rows)/4)
    assert getattr(q,dimension+'_candidates')==rows

def test_ordinary_6001_joint_fallback_retains_single_entry_lines(inputs):
    q=replace(view.detail_composition_view(inputs['summary']).quality('q6'),lock_reason='legacy',count_candidates=(),cells_candidates=())
    ns,obj=formatter();text=ns['_structured_quality_candidate_block'](q,dimension='count',heading='candidate',unit='件')
    assert ' · ' not in text and '其他 ' in text
    hover=obj._red_hover('红件',dict(count_range='1/2/3'),{},quality=q)
    assert '保守 1 / 中位 2 / 激进 3件' in hover
    assert '概率估计：按中位展示，实际范围 1–3件' in hover

def test_gold_purple_joint_tooltip_copy_remains_intact():
    _,obj=formatter();red=dict(quality_count_summary='紫件 1/2/3 · 金件 2/3/4',purple_gold_probability_hover='紫品：2件/8格 60% · 其他 40%；金品：3件/12格 70% · 其他 30%')
    text=obj._red_hover('紫金件',red,{})
    assert text=='紫金件/格\n  紫件 1/2/3\n  金件 2/3/4\n\n紫品\n  2件/8格 60%\n  其他 40%\n\n金品\n  3件/12格 70%\n  其他 30%\n\n每档顺序：保守 / 中位 / 激进；件数与格数并列'

def test_partial_and_exact_zero_and_missing_do_not_infer_joint_lock(inputs):
    q=view.detail_composition_view(inputs['summary']).quality('q6');_,obj=formatter()
    partial=replace(q,candidates=(view.CompositionCandidate(3,6,.6),view.CompositionCandidate(3,9,.25),view.CompositionCandidate(3,12,.15)),compact_count_triplet=(),count_lock_value=3,cells_lock_value=None,dimension_state='count_locked',other_probability=0.0)
    count=obj._red_hover('红件',dict(count_range='1/2/3'),{},quality=partial)
    cells=obj._red_hover('红格',dict(cells_range='1/2/3'),{},quality=partial)
    assert '3件' in count and '该维度已锁定' in count and '唯一锁定' not in count
    assert '主行联合代表' in cells and '已锁定' not in cells
    zero=replace(q,status='exact',lock_strength='exact',candidates=(view.CompositionCandidate(0,0,1.0),),count_candidates=(view.MarginalCandidate(0,1.0),),cells_candidates=(view.MarginalCandidate(0,1.0),),compact_count_candidates=(),compact_count_triplet=(),count_lock_value=0,cells_lock_value=0,shown_probability=1.0,other_probability=0.0,truncated=False,cap_hit=False,completion_flags_complete=True)
    assert '0件' in obj._red_hover('红件',{}, {},quality=zero)
    assert '唯一锁定' in obj._red_hover('红件',{}, {},quality=zero)
    missing=replace(q,status='unavailable',candidates=(),count_candidates=(),cells_candidates=(),compact_count_candidates=(),compact_count_triplet=(),reason='等待数据')
    text=obj._red_hover('红件',dict(count_range='0/0/0'),{},quality=missing)
    assert '等待可用的联合候选' in text and '0件' not in text and '唯一锁定' not in text

@pytest.mark.parametrize('dimension,unit',[('count','件'),('cells','格')])
def test_ordinary_truncated_supplied_marginal_keeps_true_tail(inputs,dimension,unit):
    q=replace(view.detail_composition_view(inputs['summary']).quality('q6'),lock_reason='ordinary_posterior',
        candidates=(view.CompositionCandidate(2,6,.4),view.CompositionCandidate(3,9,.3)),
        count_candidates=(view.MarginalCandidate(2,.4),view.MarginalCandidate(3,.3)),
        cells_candidates=(view.MarginalCandidate(6,.4),view.MarginalCandidate(9,.3)),
        shown_probability=.7,other_probability=.3,truncated=True,compact_count_candidates=(),compact_count_triplet=(),count_correlation_state='independent')
    assert math.fsum(x.probability for x in getattr(q,dimension+'_candidates'))==pytest.approx(.7)
    ns,_=formatter();text=ns['_structured_quality_candidate_block'](q,dimension=dimension,heading='candidate',unit=unit)
    assert ' · ' not in text
    assert text.splitlines()[-1]=='  其他 30.0%'
    assert [line.strip() for line in text.splitlines()[1:-1]]==[f'{x.value}{unit} {view.format_quality_probability(q,x.probability)}' for x in getattr(q,dimension+'_candidates')]

@pytest.mark.parametrize('dimension,unit',[('count','件'),('cells','格')])
def test_sampled_partial_supplied_marginal_does_not_lose_small_true_tail(inputs,dimension,unit):
    q=replace(view.detail_composition_view(inputs['summary']).quality('q6'),
        candidates=(view.CompositionCandidate(2,6,.7),view.CompositionCandidate(3,9,.29999999)),
        count_candidates=(view.MarginalCandidate(2,.7),view.MarginalCandidate(3,.29999999)),
        cells_candidates=(view.MarginalCandidate(6,.7),view.MarginalCandidate(9,.29999999)),shown_probability=.99999999,other_probability=.00000001,compact_count_candidates=(),compact_count_triplet=(),count_correlation_state='independent')
    ns,_=formatter();text=ns['_structured_quality_candidate_block'](q,dimension=dimension,heading='candidate',unit=unit)
    assert text.splitlines()[-1]=='  其他 <0.1%'
