from __future__ import annotations

import json
import subprocess
import sys
import threading
from dataclasses import dataclass, replace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))
import ahmad_ref_engine as engine  # noqa: E402


@dataclass(frozen=True)
class _DummyResult:
    evidence: engine.RefEvidence
    payload: tuple[int, ...]
    red_outline_value_summary: dict | None = None


def _evidence():
    return engine.extract_evidence(
        {
            "ui_contract": {
                "context": {
                    "hero": "ethan",
                    "map_id": 2505,
                    "phase": "bidding",
                    "round": 5,
                }
            },
            "structured_ref_inputs": {"total_count": 58},
        }
    )


def _with_run_cache(callback):
    token = engine._RUN_CACHE.set({"env": {}, "memo": {}})
    try:
        return callback()
    finally:
        engine._RUN_CACHE.reset(token)


def test_avg_grid_run_cache_reuses_immutable_result_and_returns_fresh_list(
    monkeypatch,
) -> None:
    evidence = _evidence()
    original = engine._avg_grid_options
    calls = 0

    def counted(count, avg, quality_key=None):
        nonlocal calls
        calls += 1
        return original(count, avg, quality_key)

    monkeypatch.setattr(engine, "_avg_grid_options", counted)

    def exercise() -> tuple[list[int], list[int]]:
        first = engine._avg_grid_options_for_evidence(5, 2.9, "q4", evidence)
        first.append(999)
        second = engine._avg_grid_options_for_evidence(5, 2.9, "q4", evidence)
        return first, second

    first, second = _with_run_cache(exercise)

    assert calls == 1
    assert 999 in first
    assert 999 not in second


def test_avg_grid_run_cache_key_tracks_mutated_evidence_content() -> None:
    evidence = replace(
        _evidence(),
        quality_cell_floor_item_counts={"q5": 1},
        quality_cell_floors={"q5": 5.0},
    )

    def exercise() -> tuple[list[int], list[int]]:
        before = engine._avg_grid_options_for_evidence(2, 5.0, "q5", evidence)
        evidence.quality_cell_floors["q5"] = 6.0
        after = engine._avg_grid_options_for_evidence(2, 5.0, "q5", evidence)
        return before, after

    before, after = _with_run_cache(exercise)

    assert before == []
    assert after == [10]


def test_avg_grid_run_cache_key_tracks_catalog_domain_aba_in_place() -> None:
    evidence = _evidence()
    catalog = engine._catalog_cell_sizes_by_table_quality()
    original_q6 = catalog[6]
    assert 20 in original_q6

    def exercise() -> tuple[list[int], list[int], list[int]]:
        first = engine._avg_grid_options_for_evidence(1, 20.0, "q6", evidence)
        catalog[6] = tuple(size for size in original_q6 if size != 20)
        engine._catalog_quality_composable_grid_options.cache_clear()
        second = engine._avg_grid_options_for_evidence(1, 20.0, "q6", evidence)
        catalog[6] = original_q6
        engine._catalog_quality_composable_grid_options.cache_clear()
        third = engine._avg_grid_options_for_evidence(1, 20.0, "q6", evidence)
        return first, second, third

    try:
        first, second, third = _with_run_cache(exercise)
    finally:
        catalog[6] = original_q6
        engine._catalog_quality_composable_grid_options.cache_clear()

    assert first == [20]
    assert second == []
    assert third == [20]


def test_avg_grid_run_cache_catalog_key_uses_content_not_identity(monkeypatch) -> None:
    evidence = _evidence()
    catalog = engine._catalog_cell_sizes_by_table_quality()
    original_q6 = catalog[6]
    original = engine._avg_grid_options
    calls = 0

    def counted(count, avg, quality_key=None):
        nonlocal calls
        calls += 1
        return original(count, avg, quality_key)

    monkeypatch.setattr(engine, "_avg_grid_options", counted)

    def exercise() -> None:
        first = engine._avg_grid_options_for_evidence(1, 20.0, "q6", evidence)
        equal_but_distinct = tuple(list(original_q6))
        assert equal_but_distinct == original_q6
        assert equal_but_distinct is not original_q6
        catalog[6] = equal_but_distinct
        second = engine._avg_grid_options_for_evidence(1, 20.0, "q6", evidence)
        assert first == second == [20]

    try:
        _with_run_cache(exercise)
    finally:
        catalog[6] = original_q6
        engine._catalog_quality_composable_grid_options.cache_clear()

    assert calls == 1


def test_avg_grid_run_cache_bypasses_unstable_catalog_shape_without_magic(
    monkeypatch,
) -> None:
    evidence = _evidence()
    calls = 0

    class PoisonInt(int):
        def __hash__(self):
            raise AssertionError("custom hash must not run")

        def __eq__(self, other):
            raise AssertionError("custom equality must not run")

    def counted(_count, _avg, _quality_key=None):
        nonlocal calls
        calls += 1
        return [20]

    monkeypatch.setattr(
        engine,
        "_catalog_cell_sizes_by_table_quality",
        lambda: {6: (PoisonInt(20),)},
    )
    monkeypatch.setattr(engine, "_avg_grid_options", counted)

    def exercise() -> None:
        assert engine._avg_grid_options_for_evidence(1, 20.0, "q6", evidence) == [20]
        assert engine._avg_grid_options_for_evidence(1, 20.0, "q6", evidence) == [20]

    _with_run_cache(exercise)

    assert calls == 2


def test_avg_grid_run_cache_rollback_restores_uncached_call_count(monkeypatch) -> None:
    evidence = _evidence()
    original = engine._avg_grid_options
    calls = 0

    def counted(count, avg, quality_key=None):
        nonlocal calls
        calls += 1
        return original(count, avg, quality_key)

    monkeypatch.setenv(engine.AVG_GRID_OPTIONS_RUN_CACHE_DISABLE, "1")
    monkeypatch.setattr(engine, "_avg_grid_options", counted)

    def exercise() -> None:
        engine._avg_grid_options_for_evidence(5, 2.9, "q4", evidence)
        engine._avg_grid_options_for_evidence(5, 2.9, "q4", evidence)

    _with_run_cache(exercise)

    assert calls == 2


@pytest.mark.parametrize("hero", ["aisha", "ahmed"])
def test_avg_grid_run_cache_is_noop_for_other_heroes(monkeypatch, hero: str) -> None:
    evidence = replace(_evidence(), hero=hero)
    original = engine._avg_grid_options
    calls = 0

    def counted(count, avg, quality_key=None):
        nonlocal calls
        calls += 1
        return original(count, avg, quality_key)

    monkeypatch.setattr(engine, "_avg_grid_options", counted)

    def exercise() -> dict:
        run_cache = engine._RUN_CACHE.get()
        assert run_cache is not None
        before = {name: dict(values) for name, values in run_cache.items()}
        engine._avg_grid_options_for_evidence(5, 2.9, "q4", evidence)
        engine._avg_grid_options_for_evidence(5, 2.9, "q4", evidence)
        assert run_cache == before
        return run_cache

    run_cache = _with_run_cache(exercise)

    assert calls == 2
    assert run_cache == {"env": {}, "memo": {}}


@pytest.mark.parametrize(
    ("qualification", "official"),
    [
        ("official_random_warehouse", True),
        ("player_custom_warehouse", False),
        ("postupdate_unknown_warehouse_source", False),
    ],
)
def test_avg_grid_run_cache_is_value_equivalent_for_three_source_labels(
    monkeypatch,
    qualification: str,
    official: bool,
) -> None:
    snapshot = {
        "ui_contract": {
            "context": {
                "hero": "ethan",
                "map_id": 2505,
                "phase": "bidding",
                "round": 3,
                "warehouse_source": {
                    "qualification": qualification,
                    "official_distribution_eligible": official,
                },
                "warehouse_source_qualification": qualification,
                "official_distribution_eligible": official,
            }
        },
        "structured_ref_inputs": {
            "total_count": 24,
            "min_counts": {"q1": 2, "q3": 2, "q4": 1},
            "avg_cells": {"q4": 2.9},
        },
    }

    monkeypatch.setenv(engine.AVG_GRID_OPTIONS_RUN_CACHE_DISABLE, "1")
    rollback = engine.run_reference_engine(snapshot, max_combos=500).as_dict()
    monkeypatch.delenv(engine.AVG_GRID_OPTIONS_RUN_CACHE_DISABLE, raising=False)
    cached = engine.run_reference_engine(snapshot, max_combos=500).as_dict()

    assert cached == rollback
    assert (
        snapshot["ui_contract"]["context"]["warehouse_source_qualification"]
        == qualification
    )
    assert snapshot["ui_contract"]["context"]["official_distribution_eligible"] is official


def test_avg_grid_run_cache_resets_for_public_aba_calls(monkeypatch) -> None:
    evidence_a = _evidence()
    evidence_b = replace(evidence_a, avg_cells={"q4": 3.0})
    original = engine._avg_grid_options
    calls: list[tuple[int, float | None, str | None]] = []

    def counted(count, avg, quality_key=None):
        calls.append((count, avg, quality_key))
        return original(count, avg, quality_key)

    def fake_impl(snapshot, **_kwargs):
        evidence = evidence_a if snapshot["case"] == "A" else evidence_b
        avg = 2.9 if snapshot["case"] == "A" else 3.0
        first = engine._avg_grid_options_for_evidence(5, avg, "q4", evidence)
        second = engine._avg_grid_options_for_evidence(5, avg, "q4", evidence)
        assert first == second
        return _DummyResult(evidence=evidence, payload=tuple(first))

    monkeypatch.setattr(engine, "_avg_grid_options", counted)
    monkeypatch.setattr(engine, "_run_reference_engine_impl", fake_impl)

    a1 = engine.run_reference_engine({"case": "A"})
    b = engine.run_reference_engine({"case": "B"})
    a2 = engine.run_reference_engine({"case": "A"})

    assert a1.payload == a2.payload
    assert a1.payload != b.payload
    assert calls == [(5, 2.9, "q4"), (5, 3.0, "q4"), (5, 2.9, "q4")]


def test_avg_grid_run_cache_is_context_local_between_threads(monkeypatch) -> None:
    evidence = _evidence()
    original = engine._avg_grid_options
    calls: list[int] = []
    barrier = threading.Barrier(2)

    def counted(count, avg, quality_key=None):
        calls.append(threading.get_ident())
        return original(count, avg, quality_key)

    def fake_impl(_snapshot, **_kwargs):
        barrier.wait(timeout=5)
        first = engine._avg_grid_options_for_evidence(5, 2.9, "q4", evidence)
        second = engine._avg_grid_options_for_evidence(5, 2.9, "q4", evidence)
        assert first == second
        return _DummyResult(evidence=evidence, payload=tuple(first))

    monkeypatch.setattr(engine, "_avg_grid_options", counted)
    monkeypatch.setattr(engine, "_run_reference_engine_impl", fake_impl)

    results: list[_DummyResult] = []
    threads = [
        threading.Thread(target=lambda: results.append(engine.run_reference_engine({})))
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == 2
    assert results[0].payload == results[1].payload
    assert len(calls) == 2
    assert len(set(calls)) == 2


def test_avg_grid_run_cache_survives_nested_public_call_and_resets_once(
    monkeypatch,
) -> None:
    evidence = _evidence()
    original = engine._avg_grid_options
    calls = 0
    cache_ids: list[int] = []

    def counted(count, avg, quality_key=None):
        nonlocal calls
        calls += 1
        return original(count, avg, quality_key)

    def fake_impl(snapshot, **_kwargs):
        run_cache = engine._RUN_CACHE.get()
        assert run_cache is not None
        cache_ids.append(id(run_cache))
        payload = engine._avg_grid_options_for_evidence(5, 2.9, "q4", evidence)
        if snapshot["case"] == "outer":
            nested = engine.run_reference_engine({"case": "inner"})
            assert nested.payload == tuple(payload)
            assert engine._avg_grid_options_for_evidence(5, 2.9, "q4", evidence) == payload
        return _DummyResult(evidence=evidence, payload=tuple(payload))

    monkeypatch.setattr(engine, "_avg_grid_options", counted)
    monkeypatch.setattr(engine, "_run_reference_engine_impl", fake_impl)

    result = engine.run_reference_engine({"case": "outer"})

    assert result.evidence is evidence
    assert calls == 1
    assert len(cache_ids) == 2
    assert len(set(cache_ids)) == 1
    assert engine._RUN_CACHE.get() is None
    assert engine._RUN_CALL_DEPTH.get() == 0


def test_avg_grid_run_cache_resets_after_public_exception(monkeypatch) -> None:
    evidence = _evidence()
    original = engine._avg_grid_options
    calls = 0

    def counted(count, avg, quality_key=None):
        nonlocal calls
        calls += 1
        return original(count, avg, quality_key)

    def raising_impl(_snapshot, **_kwargs):
        engine._avg_grid_options_for_evidence(5, 2.9, "q4", evidence)
        raise RuntimeError("expected failure")

    monkeypatch.setattr(engine, "_avg_grid_options", counted)
    monkeypatch.setattr(engine, "_run_reference_engine_impl", raising_impl)

    with pytest.raises(RuntimeError, match="expected failure"):
        engine.run_reference_engine({})

    assert engine._RUN_CACHE.get() is None
    assert engine._RUN_CALL_DEPTH.get() == 0

    def succeeding_impl(_snapshot, **_kwargs):
        first = engine._avg_grid_options_for_evidence(5, 2.9, "q4", evidence)
        second = engine._avg_grid_options_for_evidence(5, 2.9, "q4", evidence)
        assert first == second
        return _DummyResult(evidence=evidence, payload=tuple(first))

    monkeypatch.setattr(engine, "_run_reference_engine_impl", succeeding_impl)
    engine.run_reference_engine({})

    assert calls == 2
    assert engine._RUN_CACHE.get() is None


def test_avg_grid_run_cache_has_bounded_dedicated_bucket(monkeypatch) -> None:
    evidence = _evidence()
    original = engine._avg_grid_options
    calls = 0

    def counted(count, avg, quality_key=None):
        nonlocal calls
        calls += 1
        return original(count, avg, quality_key)

    monkeypatch.setattr(engine, "_avg_grid_options", counted)
    monkeypatch.setattr(engine, "AVG_GRID_OPTIONS_RUN_CACHE_MAX_ENTRIES", 2)

    def exercise() -> tuple[int, dict]:
        for count in (1, 2, 3):
            engine._avg_grid_options_for_evidence(count, None, "q4", evidence)
            engine._avg_grid_options_for_evidence(count, None, "q4", evidence)
        run_cache = engine._RUN_CACHE.get()
        assert run_cache is not None
        return calls, run_cache

    total_calls, run_cache = _with_run_cache(exercise)

    assert total_calls == 4
    assert len(run_cache["avg_grid_options_for_evidence"]) == 2


def test_avg_grid_run_cache_is_process_local() -> None:
    code = """
import json
import sys
from pathlib import Path
root = Path.cwd()
sys.path.insert(0, str(root / 'hero_ref' / 'src'))
import ahmad_ref_engine as engine
evidence = engine.extract_evidence({
    'ui_contract': {'context': {'hero': 'ethan', 'map_id': 2505, 'phase': 'bidding', 'round': 5}},
    'structured_ref_inputs': {'total_count': 58},
})
token = engine._RUN_CACHE.set({'env': {}, 'memo': {}})
try:
    first = engine._avg_grid_options_for_evidence(5, 2.9, 'q4', evidence)
    second = engine._avg_grid_options_for_evidence(5, 2.9, 'q4', evidence)
    bucket = engine._RUN_CACHE.get()['avg_grid_options_for_evidence']
    print(json.dumps({'equal': first == second, 'entries': len(bucket)}))
finally:
    engine._RUN_CACHE.reset(token)
"""
    outputs = [
        subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout.strip()
        for _ in range(2)
    ]

    assert [json.loads(output) for output in outputs] == [
        {"equal": True, "entries": 1},
        {"equal": True, "entries": 1},
    ]
