"""Fictional inputs exercise actual product mathematics without a game catalogue."""
from bidking_lab.inference.display import enumerate_candidates, parse_reading, is_compatible
from bidking_lab.inference.mean_multiset_support import MeanSupportCandidate, solve_repeat_allowed_mean_support

def test_decimal_precision_is_preserved():
    reading=parse_reading('2.90')
    assert enumerate_candidates(reading,max_count=20,max_total_cells=60)==[(32,11)]
    assert not is_compatible(reading,29,10)
    assert is_compatible(parse_reading('2.9'),29,10)

def test_complete_composition_and_infeasible_total():
    items=[MeanSupportCandidate(1,3,10,1),MeanSupportCandidate(2,4,20,1)]
    result=solve_repeat_allowed_mean_support(items,total_lower=30,total_upper=30,item_count=2)
    assert result.status=='ok'
    assert len(result.signatures)==1
    assert result.signatures[0].total_value==30
    assert result.signatures[0].quality_counts==(0,0,1,1,0,0)
    impossible=solve_repeat_allowed_mean_support(items,total_lower=31,total_upper=31,item_count=2)
    assert not impossible.signatures

def test_invalid_input_does_not_create_a_plausible_result():
    bad=solve_repeat_allowed_mean_support([],total_lower=30,total_upper=30,item_count=2)
    assert bad.status=='invalid_candidates'
    assert not bad.signatures
