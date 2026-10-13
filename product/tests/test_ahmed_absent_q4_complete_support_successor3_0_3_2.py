"""Publish Ahmed absent-q4 widening only from globally complete support."""

from __future__ import annotations

import copy
import hashlib
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ahmad_live_panel_server as panel  # noqa: E402
import ahmad_ref_engine as engine  # noqa: E402


ROLLBACK = engine.AHMED_ABSENT_Q4_ZERO_ANTILOCK_DISABLE
NOTE = engine.AHMED_ABSENT_Q4_ZERO_ANTILOCK_NOTE
FROZEN_INPUTS = {
    "data/raw/tables/Item.txt": (
        527_604,
        "7c5901ea6fdd680118737d5e5733496f2c802c97d60b0cd70df95a6115fe6625",
    ),
    "data/raw/tables/BidMap.txt": (
        80_972,
        "6c54d421fae9e238f1c1b5afc6deea88e325805ca8df706804e8dfb893f50edd",
    ),
    "data/raw/tables/Drop.txt": (
        294_160,
        "9a8ed5748d05d774fb49f3aabd0769c5e81afe149d1ffaa8dcce92314948c307",
    ),
    "data/raw/tables/Hero.txt": (
        10_628,
        "4763011df55a3bb3f3f07ee804351055c9f058aace482a4df32057098efabed9",
    ),
    "data/processed/red_longtail_normalized_0627.json": (
        992,
        "c5cba6367d1a314533aead17488b12a319f24f21c3f8bf016d275472af21d5b0",
    ),
    "data/processed/drop_table_priors_0627.json": (
        30_211,
        "7ff3859d88a74e184074a8df58d11a284755376eeb6cced7befc697212a3d02c",
    ),
    "data/processed/quality_combo_presolve_q456.json": (
        15_710_713,
        "c2cd901aaf32fabdee3cf7f2b7a233fcd049c3f37d59f62e4dd577f95130b5df",
    ),
    (
        "external_references/AuctionAnalyzer4.13.3/_decompiled/"
        "MapBidCalculator/MapBidCalculator/Models/StaticData.cs"
    ): (
        10_408,
        "7543b4aa16addc73dfaaaf3c6df9bc0f6662bd83458de110a16d04ff9f2f9676",
    ),
}


@pytest.fixture(scope="module", autouse=True)
def _frozen_package_inputs() -> None:
    for relative, (expected_size, expected_sha256) in FROZEN_INPUTS.items():
        payload = (ROOT / relative).read_bytes()
        assert len(payload) == expected_size, relative
        assert hashlib.sha256(payload).hexdigest() == expected_sha256, relative


def _sort11_snapshot() -> dict:
    return {
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 4505,
                "phase": "bidding",
                "round": 2,
            },
        },
        "structured_ref_inputs": {
            "total_count": 58,
            "counts": {"q3": 22},
            "avg_cells": {"q1": 1.941176414489746, "q5": 6.125},
        },
        "skill_reveals": [
            {"hero_id": 204, "skill_id": 1002041, "result": 6.125},
        ],
    }


def _run(snapshot: dict, *, max_combos: int = 8_000) -> dict:
    return engine.run_reference_engine(snapshot, max_combos=max_combos).as_dict()


@pytest.mark.parametrize("max_combos", (1, 2, 3))
def test_cap_hit_blanks_every_derived_publication(max_combos: int) -> None:
    result = _run(_sort11_snapshot(), max_combos=max_combos)

    assert result["status"] == "incomplete_support"
    assert result["combo_count"] == 0
    assert result["total_count_estimate"] == 58
    assert [result[key] for key in ("conservative", "balanced", "aggressive")] == [
        None,
        None,
        None,
    ]
    assert [result[key] for key in ("value_p25", "value_p50", "value_p75")] == [
        None,
        None,
        None,
    ]
    assert result["red_count_range"] == [None, None, None]
    assert result["red_cells_range"] == [None, None, None]
    assert result["red_value_range"] == [None, None, None]
    assert result["total_grid_range"] == [None, None, None]
    assert result["quality_count_ranges"]["q3"] == [22, 22, 22]
    assert all(
        result["quality_count_ranges"][key] == [None, None, None]
        for key in ("q1", "q4", "q5", "q6")
    )
    assert all(
        result["quality_cells_ranges"][key] == [None, None, None]
        for key in engine.QUALITY_KEYS
    )
    assert result["quality_count_candidates"] == {}
    assert result["quality_cells_candidates"] == {}
    assert result["quality_count_cells_candidates"] == {}
    assert result["quality_lock"]["q3"] == {
        "strength": "exact",
        "reason": "exact_lock",
    }
    assert all(
        result["quality_lock"][key]
        == {"strength": "probabilistic", "reason": "combo_multi_solution"}
        for key in ("q1", "q4", "q5", "q6")
    )
    assert NOTE not in result["notes"]
    assert "combo_cap_hit" in result["notes"]


def _assert_complete_sort11(result: dict) -> None:
    assert result["status"] == "count_prior"
    assert result["combo_count"] == 4
    assert result["quality_count_ranges"] == {
        "q1": [17, 17, 17],
        "q3": [22, 22, 22],
        "q4": [9, 9, 11],
        "q5": [8, 8, 8],
        "q6": [0, 2, 2],
    }
    assert [result[key] for key in ("conservative", "balanced", "aggressive")] == [
        748_022,
        845_198,
        926_030,
    ]
    assert NOTE in result["notes"]
    assert "combo_cap_hit" not in result["notes"]
    q4_by_probability = {
        row["prob"]: row["value"]
        for row in result["quality_count_candidates"]["q4"]
    }
    q6_by_probability = {
        row["prob"]: row["value"]
        for row in result["quality_count_candidates"]["q6"]
    }
    assert q4_by_probability.keys() == q6_by_probability.keys()
    assert all(
        q4_by_probability[probability] + q6_by_probability[probability] == 11
        for probability in q4_by_probability
    )
    for key in ("q4", "q6"):
        assert all(
            engine._can_compose_grid_total_with_q6_catalog(
                key,
                int(row["count"]),
                int(row["cells"]),
            )
            for row in result["quality_count_cells_candidates"][key]
        )


@pytest.mark.parametrize("max_combos", (4, 8_000, 50_000))
def test_complete_global_support_repairs_absent_q4(max_combos: int) -> None:
    _assert_complete_sort11(_run(_sort11_snapshot(), max_combos=max_combos))


def test_multiple_totals_use_one_global_atomic_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results: list[tuple[dict, dict]] = []
    for order in ([57, 58], [58, 57]):
        monkeypatch.setattr(
            engine,
            "_total_count_candidates",
            lambda *args, values=order, **kwargs: (list(values), 58),
        )
        results.append(
            (
                _run(_sort11_snapshot(), max_combos=3),
                _run(_sort11_snapshot(), max_combos=8),
            )
        )

    assert results[0] == results[1]
    incomplete, complete = results[0]

    assert incomplete["status"] == "incomplete_support"
    assert incomplete["combo_count"] == 0
    assert incomplete["quality_count_ranges"]["q4"] == [None, None, None]
    assert NOTE not in incomplete["notes"]
    assert "combo_cap_hit" in incomplete["notes"]
    assert complete["status"] == "count_prior"
    assert complete["combo_count"] == 7
    assert complete["quality_count_ranges"]["q4"] == [9, 9, 10]
    assert NOTE in complete["notes"]
    assert "combo_cap_hit" not in complete["notes"]


@pytest.mark.parametrize("max_combos", (1, 2, 3, 4, 8_000, 50_000))
def test_named_rollback_keeps_parent_wire(
    max_combos: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ROLLBACK, "1")
    result = _run(_sort11_snapshot(), max_combos=max_combos)

    assert result["status"] == "count_prior"
    assert result["combo_count"] == 1
    assert result["quality_count_ranges"] == {
        "q1": [17, 17, 17],
        "q3": [22, 22, 22],
        "q4": [0, 0, 0],
        "q5": [16, 16, 16],
        "q6": [3, 3, 3],
    }
    assert result["quality_lock"]["q4"] == {
        "strength": "exact",
        "reason": "derived_single_solution",
    }
    assert [result[key] for key in ("conservative", "balanced", "aggressive")] == [
        1_413_143,
        1_493_358,
        1_573_573,
    ]
    assert NOTE not in result["notes"]
    assert ("combo_cap_hit" in result["notes"]) is (max_combos == 1)


@pytest.mark.parametrize("tier", ("q4", "q6"))
@pytest.mark.parametrize(
    ("field", "q4_value", "q6_value"),
    (
        ("min_counts", 1, 1),
        ("counts", 9, 2),
        ("quality_cells", 21, 6),
        ("quality_values", 100_000, 100_000),
        ("quality_value_floors", 10_000, 10_000),
        ("avg_cells", 2.444444417953491, 3.0),
    ),
)
def test_direct_q4_q6_evidence_is_a_rollback_noop(
    tier: str,
    field: str,
    q4_value: int | float,
    q6_value: int | float,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _sort11_snapshot()
    value = q4_value if tier == "q4" else q6_value
    snapshot["structured_ref_inputs"].setdefault(field, {})[tier] = value
    current = _run(snapshot)
    monkeypatch.setenv(ROLLBACK, "1")
    rollback = _run(snapshot)

    assert current == rollback
    assert NOTE not in current["notes"]


@pytest.mark.parametrize(
    ("field", "value"),
    (("counts", 0), ("quality_cells", 0), ("avg_cells", 0.0)),
)
def test_authoritative_q4_zero_stays_exact(
    field: str,
    value: int | float,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _sort11_snapshot()
    snapshot["structured_ref_inputs"].setdefault(field, {})["q4"] = value
    current = _run(snapshot)
    monkeypatch.setenv(ROLLBACK, "1")
    rollback = _run(snapshot)

    assert current == rollback
    assert current["quality_count_ranges"]["q4"] == [0, 0, 0]
    assert current["quality_lock"]["q4"]["strength"] == "exact"


def test_positive_q4_and_hard_zero_conflict_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _sort11_snapshot()
    snapshot["structured_ref_inputs"]["avg_cells"]["q4"] = 2.444444417953491
    snapshot["structured_ref_inputs"]["counts"]["q4"] = 0
    current = _run(snapshot)
    monkeypatch.setenv(ROLLBACK, "1")
    rollback = _run(snapshot)

    assert current == rollback
    assert current["status"] == "no_reachable_combo"
    assert current["combo_count"] == 0


@pytest.mark.parametrize(
    ("surface", "expected_q4"),
    (("sort9", [12, 13, 13]), ("sort17", [9, 9, 9])),
)
def test_real_frame_neighbors_are_not_reprocessed(
    surface: str,
    expected_q4: list[int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _sort11_snapshot()
    if surface == "sort9":
        del snapshot["structured_ref_inputs"]["avg_cells"]["q1"]
    else:
        snapshot["structured_ref_inputs"]["avg_cells"]["q4"] = 2.444444417953491
    current = _run(snapshot)
    monkeypatch.setenv(ROLLBACK, "1")
    rollback = _run(snapshot)

    assert current == rollback
    assert current["quality_count_ranges"]["q4"] == expected_q4
    assert NOTE not in current["notes"]


@pytest.mark.parametrize(("field", "value"), (("hero", "aisha"), ("phase", "settled")))
def test_non_target_hero_and_phase_are_rollback_noops(
    field: str,
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _sort11_snapshot()
    snapshot["ui_contract"]["context"][field] = value
    current = _run(snapshot)
    monkeypatch.setenv(ROLLBACK, "1")
    rollback = _run(snapshot)

    assert current == rollback
    assert NOTE not in current["notes"]


def _public_skill_4542_snapshot() -> dict:
    return {
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 4542,
                "phase": "bidding",
                "round": 2,
            },
            "constraints": {
                "public_info": {
                    "public_numeric_facts": [
                        {"semantic": "q5_avg_cells", "value": 4.125},
                    ],
                },
            },
            "actions": {
                "results": [
                    {"action_id": 100110, "result": 2.2916667461395264},
                    {"action_id": 100117, "result": 14},
                ],
            },
        },
        "structured_ref_inputs": {
            "total_count": 56,
            "fixed_counts": {"q3": 14},
            "avg_cells": {"q1": 2.2916667461395264, "q5": 4.125},
        },
        "skill_reveals": [
            {"hero_id": 104, "skill_id": 1002041, "result": 4.125},
        ],
    }


def test_4542_public_skill_joint_repair_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _public_skill_4542_snapshot()
    current = _run(snapshot, max_combos=50_000)
    monkeypatch.setenv(ROLLBACK, "1")
    rollback = _run(snapshot, max_combos=50_000)

    assert current == rollback
    assert current["combo_count"] == 4
    assert current["quality_count_ranges"]["q4"] == [8, 8, 9]
    assert current["quality_count_ranges"]["q5"] == [8, 8, 8]


def test_cap_hit_is_unavailable_to_summary_and_composition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(panel, "hero_max_combos_for_round", lambda *args, **kwargs: 3)
    summary = panel.summarize_snapshot(
        copy.deepcopy(_sort11_snapshot()),
        snapshot_path=tmp_path / "latest_snapshot.json",
    )

    assert summary["reference"]["readiness"] == "incomplete_support"
    assert summary["reference"]["decision_range"] == "-"
    assert summary["red"]["count_range"] == "-"
    assert summary["red"]["cells_range"] == "-"
    assert summary["quality_composition"]["status"] == "unavailable"
