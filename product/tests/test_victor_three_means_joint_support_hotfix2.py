"""Victor's hard three means constrain joint support, never prior-selected truth.

The reported high-tier arithmetic is reproduced with a synthetic low-tier body.
The original settlement export is a separate controlled reconstruction, not replay.
"""
from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import struct
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "hero_ref/src", ROOT / "hero_ref/tools"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ahmad_ref_engine as engine  # noqa: E402

NOTE = "victor_three_means_joint_support"
STATIC_SHA256 = "7543b4aa16addc73dfaaaf3c6df9bc0f6662bd83458de110a16d04ff9f2f9676"
DEFAULT_STATIC_PATH = Path(
    "C:/b34h4/external_references/AuctionAnalyzer4.13.3/_decompiled/"
    "MapBidCalculator/MapBidCalculator/Models/StaticData.cs"
)
HIGH_KEYS = ("q4", "q5", "q6")
REPORTED_ARMS = {
    (13, 2, 18), (13, 5, 15), (13, 8, 12), (13, 11, 9),
    (13, 14, 6), (13, 17, 3), (26, 4, 3),
}


def _require_static(path: Path) -> dict:
    assert path.is_file(), f"missing frozen StaticData input: {path}"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == STATIC_SHA256, (
        "wrong frozen StaticData SHA256"
    )
    data = engine.load_reference_static_data(path)
    assert 2509 in data["map_nests"], "StaticData map table missing"
    return data


@pytest.fixture(scope="module")
def static_data() -> dict:
    return _require_static(Path(os.environ.get("BIDKING_TEST_STATIC_DATA_PATH", DEFAULT_STATIC_PATH)))


def _float32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def _snapshot(
    *, high_total: int = 33,
    means: tuple[float, float, float] = (50 / 13, 7.0, 16 / 3),
    round_no: int = 3,
    phase: str = "bidding",
    hero: str = "victor",
) -> dict:
    # These low counts are synthetic, not settlement truth supplied to bidding.
    session = "synthetic-victor-three-means"
    return {
        "hero": hero, "map_id": 2509, "round": round_no,
        "phase": phase, "session_id": session, "created_at": time.time(),
        "structured_ref_inputs": {
            "source": "synthetic-hard-facts", "hero": hero,
            "total_count": None,
            "counts": {"q1": 10, "q3": 12},
            "count_sums": {"q4q5q6": high_total},
            "avg_cells": dict(zip(HIGH_KEYS, map(_float32, means))),
        },
        "ui_contract": {
            "context": {
                "hero": hero, "map_id": 2509, "round": round_no,
                "phase": phase, "session_id": session,
            },
            "constraints": {"public_info": {}},
            "actions": {"results": []},
        },
    }


def _run(snapshot: dict, static_data: dict, *, cap: int = 8000) -> dict:
    return engine.run_reference_engine(
        engine.prepare_reference_engine_snapshot(copy.deepcopy(snapshot)),
        static_data=static_data, max_combos=cap,
    ).as_dict()


def _capture_arms(monkeypatch: pytest.MonkeyPatch) -> set[tuple[int, int, int]]:
    arms: set[tuple[int, int, int]] = set()
    original = engine._q6_full_avg_combo_variants

    def capture(counts, evidence):
        variants = original(counts, evidence)
        if variants:
            arms.add(tuple(counts[key] for key in HIGH_KEYS))
        return variants

    monkeypatch.setattr(engine, "_q6_full_avg_combo_variants", capture)
    return arms


def _assert_support(result: dict, arms: set[tuple[int, int, int]]) -> None:
    assert result["status"] in {"ok", "count_prior"}
    assert result["balanced"] is not None
    assert "combo_cap_hit" not in result["notes"]
    assert NOTE in result["notes"]
    assert result["red_count_range"] == result["quality_count_ranges"]["q6"]
    assert result["red_cells_range"] == result["quality_cells_ranges"]["q6"]
    for index, key in enumerate(HIGH_KEYS):
        counts = {arm[index] for arm in arms}
        assert result["quality_lock"][key]["strength"] == (
            "probabilistic" if len(counts) > 1 else "exact"
        )
        if len(counts) > 1:
            assert {row["value"] for row in result["quality_count_candidates"][key]} == counts
        joints = result["quality_count_cells_candidates"][key]
        assert {row["count"] for row in joints} == counts
        avg = result["evidence"]["avg_cells"][key]
        evidence = engine.extract_evidence(_snapshot())
        for row in joints:
            assert row["prob"] > 0
            assert row["cells"] in engine._avg_grid_options_for_evidence(
                row["count"], avg, key, evidence
            )


def test_static_input_gate_rejects_missing_and_wrong_input(tmp_path: Path) -> None:
    with pytest.raises(AssertionError, match="missing frozen StaticData"):
        _require_static(tmp_path / "missing.cs")
    wrong = tmp_path / "wrong.cs"
    wrong.write_text("wrong", encoding="utf-8")
    with pytest.raises(AssertionError, match="wrong frozen StaticData"):
        _require_static(wrong)


def test_reported_arithmetic_keeps_all_seven_joint_arms(
    static_data: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_arms(monkeypatch)
    result = _run(_snapshot(), static_data)
    assert captured == REPORTED_ARMS
    assert result["combo_count"] == len(REPORTED_ARMS)
    assert {(13, 11, 9), (26, 4, 3)} <= captured
    _assert_support(result, captured)


def test_positive_control_detects_the_parent_soft_domain_false_lock(
    static_data: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as disabled:
        disabled.setattr(engine, "_victor_three_means_high_count_sum", lambda _ev: None)
        parent = _run(_snapshot(), static_data)
    current = _run(_snapshot(), static_data)
    assert parent["combo_count"] == 1
    assert parent["quality_count_ranges"]["q4"] == [13, 13, 13]
    assert parent["quality_lock"]["q4"] == {
        "strength": "exact", "reason": "derived_single_solution",
    }
    assert current["combo_count"] == 7
    assert current["quality_lock"]["q4"]["strength"] == "probabilistic"


def test_different_ratios_are_not_a_sample_or_map_patch(
    static_data: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot(high_total=18, means=(11 / 4, 13 / 3, 9 / 2))
    snapshot["map_id"] = snapshot["ui_contract"]["context"]["map_id"] = 2409
    captured = _capture_arms(monkeypatch)
    result = _run(snapshot, static_data)
    assert captured == {(4, 6, 8), (4, 12, 2), (8, 6, 4)}
    _assert_support(result, captured)


def test_genuine_unique_hard_solution_still_locks_at_exact_cap(static_data: dict) -> None:
    result = _run(_snapshot(high_total=7, means=(2.5, 3.5, 4 / 3)), static_data, cap=1)
    _assert_support(result, {(2, 2, 3)})
    assert result["combo_count"] == 1
    assert all(result["quality_lock"][key]["reason"] == "derived_single_solution" for key in HIGH_KEYS)


@pytest.mark.parametrize("cap", [1, 6])
def test_truncated_prefix_cannot_publish_joint_counts_or_quotes(static_data: dict, cap: int) -> None:
    result = _run(_snapshot(), static_data, cap=cap)
    assert result["status"] == "incomplete_support"
    assert NOTE + ":incomplete" in result["notes"]
    assert result["combo_count"] == 0
    assert result["quality_lock"] == {}
    assert result["quality_count_candidates"] == {}
    assert result["quality_count_cells_candidates"] == {}
    assert all(result[key] is None for key in ("conservative", "balanced", "aggressive"))


@pytest.mark.parametrize("cap", [7, 8000])
@pytest.mark.parametrize("exact_total", [False, True])
def test_exactly_full_support_is_distinct_from_a_capped_prefix(
    static_data: dict, cap: int, exact_total: bool,
) -> None:
    snapshot = _snapshot()
    if exact_total:
        snapshot["structured_ref_inputs"]["total_count"] = 55
    result = _run(snapshot, static_data, cap=cap)
    assert result["combo_count"] == 7
    _assert_support(result, REPORTED_ARMS)


def test_eighty_rows_is_not_the_eight_thousand_combo_budget(static_data: dict) -> None:
    snapshot = _snapshot(high_total=16, means=(2.0, 2.0, 2.0))
    capped = _run(snapshot, static_data, cap=80)
    full = _run(snapshot, static_data, cap=8000)
    assert capped["status"] == "incomplete_support"
    assert full["combo_count"] == 105
    assert full["status"] in {"ok", "count_prior"}
    assert "combo_cap_hit" not in full["notes"]


def test_actual_eight_thousand_cap_rejects_the_8001st_joint_state(static_data: dict) -> None:
    snapshot = _snapshot(high_total=128, means=(2.0, 2.0, 2.0))
    snapshot["structured_ref_inputs"]["total_count"] = 150
    # 128 positive high-tier counts have C(127, 2) = 8001 compositions.
    result = _run(snapshot, static_data, cap=8000)
    assert result["status"] == "incomplete_support"
    assert "combo_cap_hit" in result["notes"]
    assert result["combo_count"] == 0
    assert result["quality_lock"] == {}
    assert result["balanced"] is None


@pytest.mark.parametrize("round_no,phase", [(1, "reading"), (2, "bidding"), (5, "bidding")])
def test_early_and_late_preparation_preserve_hard_inputs(
    static_data: dict, round_no: int, phase: str,
) -> None:
    snapshot = _snapshot(round_no=round_no, phase=phase)
    if round_no == 1:
        snapshot["audit_aisha_early_round"] = True
    result = _run(snapshot, static_data)
    assert result["evidence"]["phase"] == phase
    assert result["evidence"]["count_sums"]["q4q5q6"] == 33
    _assert_support(result, REPORTED_ARMS)


@pytest.mark.parametrize("key", HIGH_KEYS)
def test_missing_one_mean_keeps_the_prior_lane_unchanged(
    static_data: dict, monkeypatch: pytest.MonkeyPatch, key: str,
) -> None:
    snapshot = _snapshot()
    snapshot["structured_ref_inputs"]["avg_cells"].pop(key)
    current = _run(snapshot, static_data)
    monkeypatch.setattr(engine, "_victor_three_means_high_count_sum", lambda _ev: None)
    assert _run(snapshot, static_data) == current
    assert NOTE not in current["notes"]


def test_public_soft_mean_is_not_promoted_by_the_other_two_hard_means(
    static_data: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    snapshot["structured_ref_inputs"]["avg_cells"].pop("q5")
    snapshot["ui_contract"]["constraints"]["public_info"] = {
        "public_numeric_facts": [{"semantic": "q5_avg_cells", "value": 7.0}],
    }
    current = _run(snapshot, static_data)
    assert "q5" in current["evidence"]["soft_avg_cell_keys"]
    assert NOTE not in current["notes"]
    monkeypatch.setattr(engine, "_victor_three_means_high_count_sum", lambda _ev: None)
    assert _run(snapshot, static_data) == current


@pytest.mark.parametrize("mutation", ["missing_sum", "manual", "settled"])
def test_incomplete_or_nonlive_evidence_stays_on_its_existing_lane(
    static_data: dict, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    snapshot = _snapshot()
    if mutation == "missing_sum":
        snapshot["structured_ref_inputs"].pop("count_sums")
    else:
        snapshot["phase"] = snapshot["ui_contract"]["context"]["phase"] = mutation
    current = _run(snapshot, static_data)
    assert NOTE not in current["notes"]
    monkeypatch.setattr(engine, "_victor_three_means_high_count_sum", lambda _ev: None)
    assert _run(snapshot, static_data) == current


@pytest.mark.parametrize("hero", ["ahmed", "aisha"])
def test_other_heroes_are_byte_stable(
    static_data: dict, monkeypatch: pytest.MonkeyPatch, hero: str,
) -> None:
    snapshot = _snapshot(hero=hero)
    current = _run(snapshot, static_data)
    monkeypatch.setattr(engine, "_victor_three_means_high_count_sum", lambda _ev: None)
    assert _run(snapshot, static_data) == current
    assert NOTE not in current["notes"]


@pytest.mark.parametrize("key", HIGH_KEYS)
def test_soft_mean_does_not_activate_the_joint_hard_lane(key: str) -> None:
    evidence = engine.extract_evidence(_snapshot())
    evidence = replace(evidence, soft_avg_cell_keys=frozenset({key}))
    assert engine._victor_three_means_high_count_sum(evidence) is None


@pytest.mark.parametrize("bad", [None, 0.0, -2.0, float("nan"), float("inf"), True])
def test_invalid_mean_cannot_authorize_complete_support(bad: object) -> None:
    evidence = engine.extract_evidence(_snapshot())
    evidence = replace(evidence, avg_cells={**evidence.avg_cells, "q5": bad})
    assert engine._victor_three_means_high_count_sum(evidence) is None


@pytest.mark.parametrize("mutation", ["zero_sum", "fixed_zero", "wrong_fixed_sum", "impossible_cells"])
def test_contradictory_hard_inputs_do_not_fabricate_a_quote(static_data: dict, mutation: str) -> None:
    snapshot = _snapshot()
    inputs = snapshot["structured_ref_inputs"]
    if mutation == "zero_sum":
        inputs["count_sums"]["q4q5q6"] = 0
    elif mutation == "fixed_zero":
        inputs["counts"]["q4"] = 0
    elif mutation == "wrong_fixed_sum":
        inputs["counts"].update({"q4": 26, "q5": 11, "q6": 9})
    else:
        inputs["quality_cells"] = {"q4": 99}
    result = _run(snapshot, static_data)
    assert result["status"] == "no_reachable_combo"
    assert result["combo_count"] == 0
    assert result["quality_lock"] == {}
    assert result["balanced"] is None


@pytest.mark.parametrize("constraint", ["fixed_count", "exact_cells", "minimum_count"])
def test_stronger_count_cells_and_lower_bounds_still_remove_impossible_arms(
    static_data: dict, constraint: str,
) -> None:
    snapshot = _snapshot()
    inputs = snapshot["structured_ref_inputs"]
    if constraint == "fixed_count":
        inputs["counts"]["q4"] = 26
    elif constraint == "exact_cells":
        inputs["quality_cells"] = {"q4": 100}
    else:
        inputs["min_counts"] = {"q4": 20}
    result = _run(snapshot, static_data)
    assert result["quality_count_ranges"]["q4"] == [26, 26, 26]
    assert result["quality_count_ranges"]["q5"] == [4, 4, 4]
    assert result["quality_count_ranges"]["q6"] == [3, 3, 3]
    assert all(result["quality_lock"][key]["strength"] == "exact" for key in HIGH_KEYS)


def _hard_grid_snapshot() -> dict:
    snapshot = _snapshot()
    inputs = snapshot["structured_ref_inputs"]
    inputs["counts"] = {}
    inputs["min_counts"] = {"q1": 5, "q3": 1}
    inputs["total_cells"] = 193
    return snapshot


def test_hard_grid_restores_a_high_arm_outside_the_soft_total_window(
    static_data: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _hard_grid_snapshot()
    evidence = engine.extract_evidence(engine.prepare_reference_engine_snapshot(copy.deepcopy(snapshot)))
    witness = {"q1": 5, "q3": 1, "q4": 13, "q5": 17, "q6": 3}
    assert engine._q6_full_avg_combo_variants(witness, evidence)
    totals, _center = engine._total_count_candidates(evidence, [])
    assert sum(witness.values()) not in totals
    with monkeypatch.context() as high_only:
        high_only.setattr(engine, "_victor_three_means_hard_grid_witnesses", lambda *a, **kw: ([], True))
        without_witness = _run(snapshot, static_data)
    assert 17 not in {row["value"] for row in without_witness["quality_count_candidates"]["q5"]}
    captured = _capture_arms(monkeypatch)
    result = _run(snapshot, static_data)
    assert captured == REPORTED_ARMS
    assert result["combo_count"] == without_witness["combo_count"] + 1
    assert "victor_three_means_hard_grid_witness" in result["notes"]
    assert result["total_grid_range"] == [193, 193, 193]
    _assert_support(result, REPORTED_ARMS)


def test_hard_grid_probe_budget_exhaustion_is_not_complete_support(
    static_data: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = engine._victor_three_means_hard_grid_witnesses

    def limited(*args, **kwargs):
        kwargs["max_probes"] = 1
        return original(*args, **kwargs)

    monkeypatch.setattr(engine, "_victor_three_means_hard_grid_witnesses", limited)
    result = _run(_hard_grid_snapshot(), static_data)
    assert result["status"] == "incomplete_support"
    assert result["balanced"] is None
    assert result["quality_lock"] == {}
    assert result["quality_count_cells_candidates"] == {}
    assert "victor_three_means_hard_grid_probe_incomplete" in result["notes"]


def test_hard_grid_and_exact_low_cells_prove_a_single_high_arm_at_exact_cap(static_data: dict) -> None:
    snapshot = _hard_grid_snapshot()
    snapshot["structured_ref_inputs"]["counts"] = {"q1": 5, "q3": 1}
    snapshot["structured_ref_inputs"]["quality_cells"] = {"q1": 6, "q3": 2}
    result = _run(snapshot, static_data, cap=1)
    assert result["combo_count"] == 1
    _assert_support(result, {(13, 17, 3)})


@pytest.mark.parametrize("model_note", [
    "total_grid_from_model", engine.TOTAL_GRID_FROM_HIGH_TIER_CELLS_NOTE,
    engine.GRID_IDENTITY_RECONCILE_NOTE_PREFIX + ":193",
])
def test_a_model_grid_target_cannot_bound_the_physical_witness_domain(
    static_data: dict, model_note: str,
) -> None:
    evidence = engine.extract_evidence(_hard_grid_snapshot())
    evidence = replace(evidence, source_notes=(model_note,))
    probs, _note = engine._quality_probabilities(2509, static_data)
    rows, complete = engine._victor_three_means_hard_grid_witnesses(
        [], evidence=evidence, probs=probs, total_prior_center=47,
        max_new=8001, max_probes=8001,
        item_values={}, round_no=3,
    )
    assert rows == [] and complete is True


@pytest.mark.parametrize("cap", [1, 8000])
def test_summary_consumes_the_same_complete_or_incomplete_joint_support(
    static_data: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cap: int,
) -> None:
    import ahmad_live_panel_server as panel
    from detail_composition_view import detail_composition_view

    original_run = engine.run_reference_engine
    monkeypatch.setattr(panel, "run_reference_engine", lambda snapshot, **kwargs: original_run(
        snapshot, static_data=static_data, **kwargs,
    ))
    monkeypatch.setattr(panel, "hero_max_combos_for_round", lambda *_args: cap)
    snapshot = _snapshot()
    snapshot["skill_reveal_rows"] = [{
        "skill_id": 100209, "hero_id": 209, "result": 33,
        "revealed_items_detail": [], "sort": 1,
    }]
    summary = panel.summarize_snapshot(snapshot, snapshot_path=tmp_path / "latest_snapshot.json")
    view = detail_composition_view(summary)
    if cap == 1:
        assert summary["ahmed_ref"]["status"] == "incomplete_support"
        assert summary["ahmed_ref"]["balanced"] is None
        assert summary["ahmed_ref"]["quality_count_cells_candidates"] == {}
        assert all(view.quality(key).status not in {"exact", "ready"} for key in HIGH_KEYS)
        return
    _assert_support(summary["ahmed_ref"], REPORTED_ARMS)
    for key in HIGH_KEYS:
        assert view.quality(key).lock_strength == "probabilistic"
        assert view.quality(key).status == "ready"


V3M_A01_TOTAL_NOTE = "victor_three_means_total_from_fixed_low"
V3M_A01_ARMS = {(2, 6, 4), (4, 6, 2)}


def _v3m_a01_snapshot(low: tuple[int, int] = (2, 3), *, explicit: bool = False) -> dict:
    snapshot = _snapshot(high_total=12, means=(2.5, 7 / 3, 1.5))
    snapshot["structured_ref_inputs"]["counts"] = dict(zip(("q1", "q3"), low))
    if explicit:
        snapshot["structured_ref_inputs"]["total_count"] = sum(low) + 12
    return snapshot


@pytest.mark.parametrize("low", [(2, 3), (3, 3), (10, 12)])
@pytest.mark.parametrize("explicit", [False, True])
def test_v3m_a01_fixed_low_and_high_sum_keep_both_hard_arms(
    static_data: dict, low: tuple[int, int], explicit: bool,
) -> None:
    result = _run(_v3m_a01_snapshot(low, explicit=explicit), static_data)
    assert result["combo_count"] == 2
    _assert_support(result, V3M_A01_ARMS)
    implied_total = sum(low) + 12
    assert result["evidence"]["total_count"] == (implied_total if explicit else None)
    assert (f"{V3M_A01_TOTAL_NOTE}:{implied_total}" in result["notes"]) is not explicit


@pytest.mark.parametrize("low", [(0, 0), (0, 3), (2, 0)])
def test_v3m_a01_fixed_zero_is_a_hard_count_not_missing(static_data: dict, low: tuple[int, int]) -> None:
    result = _run(_v3m_a01_snapshot(low), static_data)
    assert result["combo_count"] == 2
    assert f"{V3M_A01_TOTAL_NOTE}:{sum(low) + 12}" in result["notes"]
    _assert_support(result, V3M_A01_ARMS)


def test_v3m_a01_derived_candidate_does_not_rewrite_observed_evidence() -> None:
    snapshot = _v3m_a01_snapshot()
    evidence = engine.extract_evidence(engine.prepare_reference_engine_snapshot(snapshot))
    observed = copy.deepcopy(evidence)
    notes: list[str] = []
    totals, center = engine._total_count_candidates(evidence, notes)
    assert totals == [17] and center == 17
    assert evidence == observed
    assert evidence.total_count is None
    assert not {"structured_ref_bridge_total_count", "field_update_total_count"}.intersection(evidence.source_notes)
    assert notes == [f"{V3M_A01_TOTAL_NOTE}:17"]


@pytest.mark.parametrize("missing", ["q1", "q3", "both"])
def test_v3m_a01_minima_or_one_fixed_low_cannot_prove_an_exact_total(
    monkeypatch: pytest.MonkeyPatch, missing: str,
) -> None:
    snapshot = _v3m_a01_snapshot()
    inputs = snapshot["structured_ref_inputs"]
    keys = ("q1", "q3") if missing == "both" else (missing,)
    inputs["min_counts"] = {key: inputs["counts"].pop(key) for key in keys}
    evidence = engine.extract_evidence(engine.prepare_reference_engine_snapshot(snapshot))
    notes: list[str] = []
    result = engine._total_count_candidates(evidence, notes)
    assert len(result[0]) > 1
    assert not any(note.startswith(V3M_A01_TOTAL_NOTE) for note in notes)
    monkeypatch.setattr(engine, "_victor_three_means_high_count_sum", lambda _evidence: None)
    legacy_notes: list[str] = []
    assert engine._total_count_candidates(evidence, legacy_notes) == result
    assert legacy_notes == notes


@pytest.mark.parametrize("explicit_total", [16, 18])
def test_v3m_a01_conflicting_explicit_total_is_not_silently_overwritten(
    static_data: dict, explicit_total: int,
) -> None:
    snapshot = _v3m_a01_snapshot()
    snapshot["structured_ref_inputs"]["total_count"] = explicit_total
    result = _run(snapshot, static_data)
    assert result["evidence"]["total_count"] == explicit_total
    assert result["status"] == "no_reachable_combo"
    assert result["combo_count"] == 0 and result["balanced"] is None
    assert result["quality_lock"] == {}
    assert not any(note.startswith(V3M_A01_TOTAL_NOTE) for note in result["notes"])


@pytest.mark.parametrize("cap", [1, 2])
def test_v3m_a01_inferred_total_keeps_sentinel_publication_atomic(static_data: dict, cap: int) -> None:
    result = _run(_v3m_a01_snapshot(), static_data, cap=cap)
    assert result["evidence"]["total_count"] is None
    if cap == 1:
        assert result["status"] == "incomplete_support"
        assert result["combo_count"] == 0
        assert result["quality_lock"] == result["quality_count_cells_candidates"] == {}
        assert all(result[key] is None for key in ("conservative", "balanced", "aggressive"))
    else:
        assert result["combo_count"] == 2
        _assert_support(result, V3M_A01_ARMS)


@pytest.mark.parametrize("constraint", ["fixed_high", "high_cells", "high_min", "q4q5_sum", "low_cells_and_grid"])
def test_v3m_a01_stronger_facts_still_prove_a_real_unique_arm(
    static_data: dict, constraint: str,
) -> None:
    snapshot = _v3m_a01_snapshot()
    inputs = snapshot["structured_ref_inputs"]
    expected = {(2, 6, 4)}
    if constraint == "fixed_high":
        inputs["counts"]["q4"] = 2
    elif constraint == "high_cells":
        inputs["quality_cells"] = {"q4": 5}
    elif constraint == "high_min":
        inputs["min_counts"] = {"q4": 3}
        expected = {(4, 6, 2)}
    elif constraint == "q4q5_sum":
        inputs["count_sums"]["q4q5"] = 8
    else:
        inputs["quality_cells"] = {"q1": 2, "q3": 3}
        inputs["total_cells"] = 32
        expected = {(4, 6, 2)}
    result = _run(snapshot, static_data, cap=1)
    assert result["combo_count"] == 1
    _assert_support(result, expected)


@pytest.mark.parametrize("phase", ["reading", "bidding"])
def test_v3m_a01_actual_live_manual_overlay_preserves_phase_and_can_derive_total(
    static_data: dict, monkeypatch: pytest.MonkeyPatch, phase: str,
) -> None:
    import ast

    # Execute the actual pure overlay methods without creating a Tk window.
    tree = ast.parse((ROOT / "hero_ref/tools/ahmad_tk_overlay.py").read_text(encoding="utf-8"))
    names = {"_structured_input_candidates", "_merge_ref_inputs", "_merged_live_ref_inputs",
             "_manual_ref_inputs", "_snapshot_with_manual_overlay"}
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AhmadTkOverlay")
    methods = [node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in methods} == names
    bridge = ast.parse("from __future__ import annotations\nclass Bridge:\n    pass\n")
    bridge.body[1].body = methods
    scope = {"copy": copy, "time": time}
    exec(compile(ast.fix_missing_locations(bridge), "actual-victor-manual-overlay", "exec"), scope)
    live = _v3m_a01_snapshot()
    live["phase"] = live["ui_contract"]["context"]["phase"] = phase
    live["formal_mode"] = "v3_practical"
    manual = {"phase": "manual", "structured_ref_inputs": {"avg_cells": {"q4": 2.5}}}
    merged = scope["Bridge"]()._snapshot_with_manual_overlay(live, manual)
    assert merged["phase"] == phase and merged["formal_mode"] == "v3_practical"
    result = _run(merged, static_data)
    assert f"{V3M_A01_TOTAL_NOTE}:17" in result["notes"]
    _assert_support(result, V3M_A01_ARMS)
    standalone = copy.deepcopy(merged)
    standalone["phase"] = standalone["ui_contract"]["context"]["phase"] = "manual"
    independent_result = _run(standalone, static_data)
    assert not any(note.startswith(V3M_A01_TOTAL_NOTE) for note in independent_result["notes"])
    monkeypatch.setattr(engine, "_victor_three_means_high_count_sum", lambda _evidence: None)
    assert _run(standalone, static_data) == independent_result


def test_v3m_a01_summary_consumes_the_recovered_hard_total_body(
    static_data: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import ahmad_live_panel_server as panel
    from detail_composition_view import detail_composition_view

    original_run = engine.run_reference_engine
    monkeypatch.setattr(panel, "run_reference_engine", lambda snapshot, **kwargs: original_run(
        snapshot, static_data=static_data, **kwargs,
    ))
    snapshot = _v3m_a01_snapshot()
    snapshot["skill_reveal_rows"] = [{
        "skill_id": 100209, "hero_id": 209, "result": 12,
        "revealed_items_detail": [], "sort": 1,
    }]
    summary = panel.summarize_snapshot(snapshot, snapshot_path=tmp_path / "latest_snapshot.json")
    _assert_support(summary["ahmed_ref"], V3M_A01_ARMS)
    assert summary["ahmed_ref"]["evidence"]["total_count"] is None
    view = detail_composition_view(summary)
    for key in ("q4", "q6"):
        assert view.quality(key).lock_strength == "probabilistic"
        assert view.quality(key).status == "ready"
