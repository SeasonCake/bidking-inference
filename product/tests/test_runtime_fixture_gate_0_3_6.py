"""Execute source-extracted ABI fixture guards without Stage or compilation."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
GENERATION = '76b7ca-2026-10-08-723'
IDS = frozenset({*range(2101,2108),*range(2201,2206),*range(2301,2311),*range(2401,2411),
    *range(2501,2511),*range(2512,2531),2601,*range(4401,4411),*range(4501,4511),*range(4512,4531),*range(5601,5612)})


@pytest.fixture(scope='module')
def extracted():
    path = ROOT / 'tests/test_release_zip_audit.py'
    spec = importlib.util.spec_from_file_location('runtime_fixture_036_ps_harness', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    body = r'''
$content = [IO.File]::ReadAllText($env:BIDKING_TEST_BUILD_SCRIPT)
$Repo = Split-Path -Parent (Split-Path -Parent $env:BIDKING_TEST_BUILD_SCRIPT)
$start = $content.IndexOf('$gateFixtures = @(')
$end = $content.IndexOf('function New-RuntimeFixtureGateCode', $start)
if ($start -lt 0 -or $end -le $start) { throw 'fixture-spec extraction failed' }
. ([scriptblock]::Create($content.Substring($start, $end - $start)))
$rows = @()
foreach ($spec in $gateFixtures) {
    $rows += [ordered]@{ MapId = $spec.MapId; Spec = $spec; Code = (New-RuntimeFixtureGateCode -GateSpec $spec -GateFixture 'C:ixture.jsonl') }
}
[Console]::Out.Write(($rows | ConvertTo-Json -Depth 6 -Compress))
'''
    # This extracts only declarations/function body; it never invokes builder.
    shell, _, _ = module._powershell_runtime_hosts()[0]
    rows = module._run_runtime_gate_spec_probe(shell, body)
    probe_ast = ast.parse((ROOT / 'tests/client_protection_runtime_probe.py').read_text('utf-8'))
    smoke = next(node.value for node in ast.walk(probe_ast) if isinstance(node, ast.Constant)
                 and isinstance(node.value, str) and node.value.startswith('import hashlib, importlib, json, os, sys\n'))
    probe = ast.parse(smoke)
    return {row['MapId']: ast.parse(row['Code']) for row in rows}, probe, rows


def extract_function(tree, name, flattened=None):
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    namespace = {'hashlib': hashlib, 'json': json, 'flatten_pool': flattened}
    exec(compile(ast.Module(body=[node],type_ignores=[]), '<exact-source-fixture-guard>', 'exec'), namespace)
    return namespace[name]


def tables():
    receipt = dict(status='current_rankmap_algorithm_prior', package_generation=GENERATION,
        authority_mode='rankmap_drop_model_algorithm_prior', scope_map_count=112,
        scope='physical_map_conditioned_ordinary_and_ranked', model_distribution_authority=True,
        official_distribution_authority=False, runtime_open_claim=False, drop_or_dropmap_textasset=False,
        claims_item_level_drop_graph=False, drop_or_dropmap_absence_is_not_a_blocker=True)
    return SimpleNamespace(rankmap_distribution_status='ok',rankmap_distribution_receipt=receipt,
        drops=SimpleNamespace(package_generation=GENERATION,authority_mode='rankmap_drop_model_algorithm_prior',bound_map_ids=IDS),
        maps={2508:SimpleNamespace(drop_pool_id=2508),5604:SimpleNamespace(drop_pool_id=2704)},items={})


def flatten(pool, drops, items, *, map_id):
    assert (map_id,pool) in {(2508,2508),(5604,2704)}
    return SimpleNamespace(item_ids=[1],resolution_status='complete',resolution_policy='rankmap_drop_model_algorithm_prior',resolution_generation=GENERATION)


def tree_for(extracted, consumer):
    builders, probe, _ = extracted
    return probe if consumer=='probe' else builders[2508]


def test_factory_and_probe_have_identical_actual_authority_guard(extracted):
    builders, probe, rows = extracted
    functions = [next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='require_current_fixture_authority')
                 for tree in [builders[5604],builders[2508],probe]]
    assert len({ast.dump(node,include_attributes=False) for node in functions})==1
    assert {row['Spec']['ExpectedStatus'] for row in rows}=={'sampleable'}
    assert {row['Spec']['PackageGeneration'] for row in rows}=={GENERATION}
    assert {row['Spec']['ModelScopeCount'] for row in rows}=={112}
    for row in rows:
        function=extract_function(builders[row['MapId']],'require_current_fixture_authority',flatten)
        function(tables(),row['MapId'],row['Spec']['OriginalPoolId'],GENERATION,112)


@pytest.mark.parametrize('consumer',['factory','probe'])
@pytest.mark.parametrize('mutation',['generation','scope18','wrong_ids_same_count','official','not_model','open','drop_claim','conditional','pool_swap'])
def test_exact_authority_guard_refuses_adjacent_bad_qualification(extracted,consumer,mutation):
    value=tables()
    if mutation=='generation': value.rankmap_distribution_receipt['package_generation']='b049c3-2026-09-10-597'
    elif mutation=='scope18': value.rankmap_distribution_receipt['scope_map_count']=18
    elif mutation=='wrong_ids_same_count': value.drops.bound_map_ids=(IDS-{5606})|{6001}
    elif mutation=='official': value.rankmap_distribution_receipt['official_distribution_authority']=True
    elif mutation=='not_model': value.rankmap_distribution_receipt['model_distribution_authority']=False
    elif mutation=='open': value.rankmap_distribution_receipt['runtime_open_claim']=True
    elif mutation=='drop_claim': value.rankmap_distribution_receipt['claims_item_level_drop_graph']=True
    elif mutation=='pool_swap': value.maps[2508].drop_pool_id=2704
    def selected(*args,**kwargs):
        result=flatten(*args,**kwargs)
        if mutation=='conditional': result.resolution_status='conditional'
        return result
    function=extract_function(tree_for(extracted,consumer),'require_current_fixture_authority',selected)
    with pytest.raises(AssertionError): function(value,2508,2508,GENERATION,112)


@pytest.mark.parametrize('consumer',['factory','probe'])
@pytest.mark.parametrize('missing',[None,1086008,1096009])
def test_tail_recovered_ids_is_independent_of_sampleable_status(extracted,consumer,missing):
    tree=tree_for(extracted,consumer)
    guard=next(node for node in ast.walk(tree) if isinstance(node,ast.If)
        and any(isinstance(child,ast.Name) and child.id=='recovered_ids' for child in ast.walk(node)))
    assert 'expected_status' not in ast.unparse(guard.test)
    assert '2508' in ast.unparse(guard.test)
    ids={1086008,1096009,1}-{missing}
    namespace={'settled':SimpleNamespace(inventory_items=[SimpleNamespace(item_id=i) for i in ids]),
               'expected_map_id':2508,'expected_status':'sampleable','spec':{'map_id':2508,'expected_status':'sampleable'}}
    code=compile(ast.Module(body=[guard],type_ignores=[]),'<exact-source-tail-guard>','exec')
    if missing is None: exec(code,namespace)
    else:
        with pytest.raises(AssertionError): exec(code,namespace)


@pytest.mark.parametrize('bad',['legacy_status','old_generation','scope18','wrong_hero','wrong_n','lost_partial','extra_key'])
def test_probe_exact_current_spec_does_not_accept_old_or_arbitrary_metadata(extracted,bad):
    spec=dict(map_id=2508,original_pool_id=2508,expected_status='sampleable',package_generation=GENERATION,
        model_scope_count=112,support_status='',support_unresolved_probability_ppm=0,hero='ahmed',inventory_count=47,
        settlement_incomplete=True,declared_item_count=51)
    function=extract_function(extracted[1],'require_approved_runtime_fixture_spec')
    function(spec)
    if bad=='legacy_status': spec.update(expected_status='degraded_sampleable',support_status='legacy_incomplete_conditional_known_support',support_unresolved_probability_ppm=73)
    elif bad=='old_generation': spec['package_generation']='b049c3-2026-09-10-597'
    elif bad=='scope18': spec['model_scope_count']=18
    elif bad=='wrong_hero': spec['hero']='aisha'
    elif bad=='wrong_n': spec['inventory_count']=51
    elif bad=='lost_partial': spec['settlement_incomplete']=False
    else: spec['unexpected']='x'
    with pytest.raises(AssertionError): function(spec)
