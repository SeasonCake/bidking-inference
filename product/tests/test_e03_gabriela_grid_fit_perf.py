from __future__ import annotations

from dataclasses import replace
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))

import ahmad_ref_engine as engine


def test_fit_option_domain_cache_hits_and_constraint_change_misses(monkeypatch) -> None:
    monkeypatch.delenv("AHMAD_DISABLE_FIT_OPTION_DOMAIN_CACHE", raising=False)
    engine._fit_grid_option_domain_cached.cache_clear()

    first = engine._fit_grid_option_domain(
        4, 40, respect_cell_floor=True, cell_floor=4.0
    )
    after_first = engine._fit_grid_option_domain_cached.cache_info()
    second = engine._fit_grid_option_domain(
        4, 40, respect_cell_floor=True, cell_floor=4.0
    )
    after_hit = engine._fit_grid_option_domain_cached.cache_info()
    constrained = engine._fit_grid_option_domain(
        4, 40, respect_cell_floor=True, cell_floor=12.0
    )
    after_constraint_change = engine._fit_grid_option_domain_cached.cache_info()

    assert second == first
    assert after_first.misses == 1
    assert after_hit.hits == after_first.hits + 1
    assert after_constraint_change.misses == after_hit.misses + 1
    assert constrained == tuple(option for option in first if option >= 12)


def test_fit_option_domain_cache_preserves_exact_fit(monkeypatch) -> None:
    counts = {"q1": 20, "q3": 10, "q4": 8, "q5": 4, "q6": 2}
    grids = {"q1": 30.0, "q3": 22.0, "q4": 19.2, "q5": 11.2, "q6": 6.4}
    floors = {"q1": 20.0, "q3": 13.0, "q4": 25.0, "q5": 4.0, "q6": 2.0}
    kwargs = {
        "avg_cells": {},
        "target": 79.0,
        "fixed_grid_keys": set(),
        "soft_avg_cell_keys": frozenset(),
        "cell_floors": floors,
        "exact_dp": True,
    }

    monkeypatch.setenv("AHMAD_DISABLE_FIT_OPTION_DOMAIN_CACHE", "1")
    monkeypatch.setenv("AHMAD_DISABLE_FITGRIDS_ARRAY_DP", "1")
    uncached = engine._fit_grids_to_total_target(dict(grids), dict(counts), **kwargs)
    monkeypatch.delenv("AHMAD_DISABLE_FIT_OPTION_DOMAIN_CACHE")
    monkeypatch.delenv("AHMAD_DISABLE_FITGRIDS_ARRAY_DP")
    cached = engine._fit_grids_to_total_target(dict(grids), dict(counts), **kwargs)

    assert cached == uncached
    assert sum(cached.values()) == 79.0


def test_hidden_hightier_option_cache_hits_and_center_change_misses() -> None:
    engine._hidden_hightier_cell_options_cached.cache_clear()
    first = engine._hidden_hightier_cell_options_cached("q5", 3, 0, 8.0, 0.0)
    after_first = engine._hidden_hightier_cell_options_cached.cache_info()
    second = engine._hidden_hightier_cell_options_cached("q5", 3, 0, 8.0, 0.0)
    after_hit = engine._hidden_hightier_cell_options_cached.cache_info()
    shifted = engine._hidden_hightier_cell_options_cached("q5", 3, 0, 12.0, 0.0)
    after_shift = engine._hidden_hightier_cell_options_cached.cache_info()

    assert second == first
    assert after_first.misses == 1
    assert after_hit.hits == after_first.hits + 1
    assert after_shift.misses == after_hit.misses + 1
    assert shifted != first


def test_run_environment_cache_is_stable_then_refreshes(monkeypatch) -> None:
    seen: list[tuple[str | None, str | None]] = []

    def fake_impl(_snapshot, **_kwargs):
        first = engine._run_env_get("E03_TEST_SWITCH")
        monkeypatch.setenv("E03_TEST_SWITCH", "changed_inside_run")
        second = engine._run_env_get("E03_TEST_SWITCH")
        seen.append((first, second))
        return first

    monkeypatch.setattr(engine, "_run_reference_engine_impl", fake_impl)
    monkeypatch.setenv("E03_TEST_SWITCH", "first_run")
    assert engine.run_reference_engine({}) == "first_run"
    assert engine.run_reference_engine({}) == "changed_inside_run"
    assert seen == [
        ("first_run", "first_run"),
        ("changed_inside_run", "changed_inside_run"),
    ]


@pytest.mark.parametrize("first_disabled", [False, True])
def test_catalog_option_caches_key_default_and_kill_switch_in_both_orders(
    monkeypatch: pytest.MonkeyPatch,
    first_disabled: bool,
) -> None:
    for cached in (
        engine._fit_grid_option_domain_cached,
        engine._hidden_hightier_cell_options_cached,
        engine._display_grid_options_no_residual,
        engine._display_grid_options_cached,
    ):
        cached.cache_clear()

    evidence = engine.extract_evidence(
        {
            "ui_contract": {
                "context": {
                    "hero": "ahmed",
                    "map_id": 4542,
                    "phase": "bidding",
                    "round": 2,
                }
            }
        }
    )

    def set_catalog_mode(disabled: bool) -> None:
        for name in (
            engine.Q5_CATALOG_CELL_COMPOSE_DISABLE,
            engine.Q6_CATALOG_CELL_COMPOSE_DISABLE,
        ):
            if disabled:
                monkeypatch.setenv(name, "1")
            else:
                monkeypatch.delenv(name, raising=False)

    def arm() -> dict[str, object]:
        fit = engine._fit_grid_option_domain(
            1,
            25,
            key="q6",
            respect_cell_floor=False,
            cell_floor=0.0,
        )
        hidden = engine._hidden_hightier_cell_options(
            "q6",
            1,
            evidence,
            center_cells=18.0,
        )
        counts_q5 = {"q1": 0, "q3": 0, "q4": 0, "q5": 1, "q6": 0}
        counts_q6 = {"q1": 0, "q3": 0, "q4": 0, "q5": 0, "q6": 1}
        q5_evidence = replace(evidence, avg_cells={"q5": 5.0})
        q6_evidence = replace(evidence, avg_cells={"q6": 20.0})
        return {
            "fit18": 18 in fit,
            "fit20": 20 in fit,
            "hidden": tuple(hidden),
            "q5avg5": engine._display_grid_options_for_quality(
                counts_q5, q5_evidence, "q5"
            ),
            "q6avg20": engine._display_grid_options_for_quality(
                counts_q6, q6_evidence, "q6"
            ),
        }

    set_catalog_mode(first_disabled)
    first = arm()
    set_catalog_mode(not first_disabled)
    second = arm()
    by_mode = {first_disabled: first, not first_disabled: second}

    default = by_mode[False]
    disabled = by_mode[True]
    assert default["fit18"] is False
    assert default["fit20"] is True
    assert (20.0, 0.42) in default["hidden"]
    assert default["q5avg5"] == ()
    assert default["q6avg20"] == (20,)
    assert disabled["fit18"] is True
    assert disabled["fit20"] is False
    assert (18.0, 1.0) in disabled["hidden"]
    assert all(cells != 20.0 for cells, _weight in disabled["hidden"])
    assert disabled["q5avg5"] == (5,)
    assert disabled["q6avg20"] == ()
