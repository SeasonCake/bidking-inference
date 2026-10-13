"""Map-conditioned models must not overwrite one another at a shared pool id."""
from __future__ import annotations

import pytest

from bidking_lab.simulation.basic_mc import (
    DropPoolResolutionError, FlattenedPool, bind_rankmap_algorithm_prior, flatten_pool,
)

GEN = "76b7ca-2026-10-08-723"
MODE = "rankmap_drop_model_algorithm_prior"


def modeled(probability, *, generation=GEN):
    return FlattenedPool(item_ids=[1, 2], probabilities=[probability, 1 - probability],
                         n_min=[1, 1], n_max=[1, 1], values=[100, 200],
                         resolution_policy=MODE, resolution_generation=generation)


def bound():
    return bind_rankmap_algorithm_prior({}, {}, package_generation=GEN, authority_mode=MODE,
                                        flattened_by_map_id={2401: (900, modeled(.9)), 2402: (900, modeled(.2))})


def test_same_pool_two_maps_keep_their_own_distribution():
    pools = bound()
    first = flatten_pool(900, pools, {}, map_id=2401)
    second = flatten_pool(900, pools, {}, map_id=2402)
    assert first.probabilities == [.9, pytest.approx(.1)]
    assert second.probabilities == [.2, .8]
    assert flatten_pool(900, pools, {}, map_id=2401) == first
    assert pools.bound_pool_ids == frozenset({900})
    assert pools.bound_map_ids == frozenset({2401, 2402})


def test_ambiguous_bare_pool_never_chooses_first_or_last_map():
    with pytest.raises(DropPoolResolutionError, match="map_context_required"):
        flatten_pool(900, bound(), {})


def test_wrong_map_or_pool_cannot_borrow_another_model():
    with pytest.raises(DropPoolResolutionError, match="map_pool_mismatch"):
        flatten_pool(901, bound(), {}, map_id=2401)
    with pytest.raises(DropPoolResolutionError, match="map_context_unknown"):
        flatten_pool(900, bound(), {}, map_id=2501)


def test_old_pool_only_api_stays_compatible():
    old = bind_rankmap_algorithm_prior({}, {900: modeled(.7)}, package_generation=GEN, authority_mode=MODE)
    assert flatten_pool(900, old, {}).probabilities == [.7, pytest.approx(.3)]
    assert flatten_pool(900, old, {}, map_id=2401) == flatten_pool(900, old, {})
    assert old.bound_map_ids == frozenset()


@pytest.mark.parametrize("same_pool", [False, True])
def test_identical_or_unique_bare_pool_is_unambiguous(same_pool):
    maps = {2401: (900, modeled(.4)), 2402: (900 if same_pool else 901, modeled(.4))}
    pools = bind_rankmap_algorithm_prior({}, {}, package_generation=GEN, authority_mode=MODE, flattened_by_map_id=maps)
    assert flatten_pool(900, pools, {}).probabilities == [.4, .6]


def test_returned_or_input_model_mutation_cannot_cross_maps():
    original = modeled(.9)
    pools = bind_rankmap_algorithm_prior({}, {}, package_generation=GEN, authority_mode=MODE,
                                        flattened_by_map_id={2401: (900, original), 2402: (900, modeled(.2))})
    original.probabilities[0] = 0
    first = flatten_pool(900, pools, {}, map_id=2401)
    assert first.probabilities[0] == .9
    first.probabilities[0] = .5
    assert flatten_pool(900, pools, {}, map_id=2401).probabilities[0] == .9
    assert flatten_pool(900, pools, {}, map_id=2402).probabilities[0] == .2


@pytest.mark.parametrize("map_id,pool_id,generation", [
    (True, 900, GEN), ("2401", 900, GEN), (0, 900, GEN),
    (2401, True, GEN), (2401, -1, GEN), (2401, 900, "b049c3-2026-09-10-597"),
])
def test_invalid_map_scope_or_mixed_generation_rejected(map_id, pool_id, generation):
    with pytest.raises(ValueError):
        bind_rankmap_algorithm_prior({}, {}, package_generation=GEN, authority_mode=MODE,
                                     flattened_by_map_id={map_id: (pool_id, modeled(.9, generation=generation))})


def test_real_simulation_and_live_consumers_select_physical_map():
    import numpy as np
    from test_basic_mc import _make_item, _make_map
    from bidking_lab.inference.ground_truth import prepare_session_sampler, _resolve_sub_pool
    from bidking_lab.live.monitor import _effective_item_weights_for_map, _drop_pool_support_receipt, MonitorTables
    from bidking_lab.simulation.basic_mc import simulate_map
    from bidking_lab.simulation.bidding import _build_flat_arrays
    from bidking_lab.simulation.hero_value import simulate_hero_value

    items = {1: _make_item(1, 100), 2: _make_item(2, 200)}
    maps = {mid: _make_map(mid, 900, 1, 1) for mid in (2401, 2402)}
    drops = bind_rankmap_algorithm_prior({}, {}, package_generation=GEN, authority_mode=MODE,
                                        flattened_by_map_id={2401: (900, modeled(1.0)), 2402: (900, modeled(0.0))})
    for mid, probabilities, expected in [(2401, [1.0, 0.0], 100), (2402, [0.0, 1.0], 200)]:
        sampler = prepare_session_sampler(mid, maps=maps, drops=drops, items=items)
        assert sampler.pools[0].probabilities.tolist() == probabilities
        assert _resolve_sub_pool(maps[mid], maps, drops, items, np.random.default_rng(1)).probabilities == probabilities
        arrays, _ = _build_flat_arrays(maps[mid], maps, drops, items)
        assert arrays[0][0].tolist() == probabilities
        assert simulate_map(mid, maps=maps, drops=drops, items=items, n_trials=3).mean == expected
        assert simulate_hero_value(103, mid, maps=maps, drops=drops, items=items, n_trials=3).baseline_mean == expected
        weights = _effective_item_weights_for_map(mid, maps=maps, drops=drops, items=items)
        assert weights == dict(zip((1, 2), probabilities))
        assert _drop_pool_support_receipt(mid, MonitorTables(maps=maps, drops=drops, items=items)) == {}

    # Anthology routing must pass each sub-map, never the parent or bare pool.
    maps[2499] = _make_map(2499, 999, 1, 1, sub_weights=[(2401, 1), (2402, 1)])
    mixed = prepare_session_sampler(2499, maps=maps, drops=drops, items=items)
    assert [pool.probabilities.tolist() for pool in mixed.pools] == [[1.0, 0.0], [0.0, 1.0]]
    arrays, _ = _build_flat_arrays(maps[2499], maps, drops, items)
    assert [array[0].tolist() for array in arrays] == [[1.0, 0.0], [0.0, 1.0]]
    assert _effective_item_weights_for_map(2499, maps=maps, drops=drops, items=items) == {1: .5, 2: .5}
