from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))

import ahmad_ref_engine as engine  # noqa: E402


RAW_SOFT_RANGE = (144, 76, 82)


def _raw_result(*, hard_target: bool) -> engine.RefResult:
    evidence = engine.extract_evidence(
        {"structured_ref_inputs": {"hero": "aisha"}}
    )
    if hard_target:
        evidence = replace(
            evidence,
            total_grid_target=144.0,
            source_notes=("action_100103_total_cells",),
        )
    assert engine._hard_total_grid_target_int(evidence) == (144 if hard_target else None)
    return engine.RefResult(
        status="count_prior",
        source="reference_v0",
        conservative=1,
        balanced=2,
        aggressive=3,
        value_p25=1,
        value_p50=2,
        value_p75=3,
        combo_count=1,
        red_count_range=(1, 1, 1),
        red_cells_range=(1, 1, 1),
        red_value_range=(1, 1, 1),
        quality_count_ranges={},
        quality_cells_ranges={},
        total_grid_range=RAW_SOFT_RANGE,
        notes=(),
        evidence=evidence,
    )


def _run_recursive_boundary_case(
    monkeypatch: pytest.MonkeyPatch,
    *,
    disable_cache: bool,
    hard_target: bool,
) -> tuple[engine.RefResult, list[tuple[int, int, int]], list[bool], list[tuple[int, int, int]]]:
    if disable_cache:
        monkeypatch.setenv("AHMAD_DISABLE_RUN_INVARIANT_CACHE", "1")
    else:
        monkeypatch.delenv("AHMAD_DISABLE_RUN_INVARIANT_CACHE", raising=False)

    internal_ranges: list[tuple[int, int, int]] = []
    cache_present: list[bool] = []
    finalizer_inputs: list[tuple[int, int, int]] = []
    raw_result = _raw_result(hard_target=hard_target)

    def fake_impl(snapshot: dict, **_kwargs: object) -> engine.RefResult:
        cache_present.append(engine._RUN_CACHE.get() is not None)
        if snapshot["level"] == "outer":
            internal = engine.run_reference_engine({"level": "inner"})
            internal_ranges.append(internal.total_grid_range)
            return internal
        return raw_result

    real_finalize = engine._finalize_published_ref_result

    def counted_finalize(result: engine.RefResult) -> engine.RefResult:
        finalizer_inputs.append(result.total_grid_range)
        return real_finalize(result)

    monkeypatch.setattr(engine, "_run_reference_engine_impl", fake_impl)
    monkeypatch.setattr(engine, "_finalize_published_ref_result", counted_finalize)

    result = engine.run_reference_engine({"level": "outer"})
    assert engine._RUN_CALL_DEPTH.get() == 0
    assert engine._RUN_CACHE.get() is None
    return result, internal_ranges, cache_present, finalizer_inputs


@pytest.mark.parametrize("disable_cache", [False, True])
def test_recursive_soft_range_is_finalized_only_at_public_boundary(
    monkeypatch: pytest.MonkeyPatch,
    disable_cache: bool,
) -> None:
    result, internal_ranges, cache_present, finalizer_inputs = _run_recursive_boundary_case(
        monkeypatch,
        disable_cache=disable_cache,
        hard_target=False,
    )

    assert internal_ranges == [RAW_SOFT_RANGE]
    assert cache_present == ([False, False] if disable_cache else [True, True])
    assert finalizer_inputs == [RAW_SOFT_RANGE]
    assert result.total_grid_range == (76, 82, 144)


@pytest.mark.parametrize("disable_cache", [False, True])
def test_recursive_hard_total_is_collapsed_only_at_public_boundary(
    monkeypatch: pytest.MonkeyPatch,
    disable_cache: bool,
) -> None:
    result, internal_ranges, cache_present, finalizer_inputs = _run_recursive_boundary_case(
        monkeypatch,
        disable_cache=disable_cache,
        hard_target=True,
    )

    assert internal_ranges == [RAW_SOFT_RANGE]
    assert cache_present == ([False, False] if disable_cache else [True, True])
    assert finalizer_inputs == [RAW_SOFT_RANGE]
    assert result.total_grid_range == (144, 144, 144)
