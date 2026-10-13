from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ahmad_live_panel_server import _quality_composition_payload  # noqa: E402
from ahmad_ref_engine import RefEvidence, RefResult  # noqa: E402


def _evidence() -> RefEvidence:
    return RefEvidence(
        hero="ahmed",
        map_id=4528,
        phase="bidding",
        total_count=None,
        fixed_counts={},
        min_counts={},
        count_sums={},
        avg_cells={},
        quality_cells={},
        quality_cell_floors={},
        avg_values={},
        quality_values={},
        quality_value_floors={},
        quality_value_floor_item_counts={},
        split_counts={},
        split_quality_cells={},
        split_avg_cells={},
        random_value_floors=(),
        total_grid_target=None,
        soft_avg_cell_keys=frozenset(),
        soft_avg_value_keys=frozenset(),
        v3_conservative="",
        v3_balanced="",
        v3_aggressive="",
        source_notes=(),
    )


def _result(probabilities: tuple[float, ...]) -> RefResult:
    marginal = tuple((index, probability) for index, probability in enumerate(probabilities, 1))
    joint = tuple(
        (index, index + 8, probability)
        for index, probability in enumerate(probabilities, 1)
    )
    return RefResult(
        status="ok",
        source="probability-wire-test",
        conservative=1,
        balanced=2,
        aggressive=3,
        value_p25=1,
        value_p50=2,
        value_p75=3,
        combo_count=len(probabilities),
        red_count_range=(1, 4, 7),
        red_cells_range=(9, 12, 15),
        red_value_range=(1, 2, 3),
        quality_count_ranges={"q6": (1, 4, 7)},
        quality_cells_ranges={"q6": (9, 12, 15)},
        total_grid_range=(9, 12, 15),
        notes=(),
        evidence=_evidence(),
        quality_count_candidates={"q6": marginal},
        quality_cells_candidates={"q6": marginal},
        quality_count_cells_candidates={"q6": joint},
        quality_lock={
            "q6": {"strength": "probabilistic", "reason": "combo_multi_solution"}
        },
    )


def _probabilities(rows: list[dict[str, object]]) -> list[float]:
    return [float(row["prob"]) for row in rows]


def test_rounding_cannot_turn_valid_distribution_into_wire_conflict() -> None:
    # Each of the first six rows independently rounds up.  The historical
    # serializer published 1.0002 and the real composition boundary rejected
    # the otherwise valid engine result as a conflict.
    result = _result((0.14286,) * 6 + (0.14284,))

    raw = result.as_dict()

    for field in (
        "quality_count_candidates",
        "quality_cells_candidates",
        "quality_count_cells_candidates",
    ):
        probabilities = _probabilities(raw[field]["q6"])
        assert all(value > 0.0 for value in probabilities)
        assert sum(probabilities) == 1.0
    payload = _quality_composition_payload(raw)
    assert payload["status"] == "ready"
    assert payload["qualities"]["q6"]["status"] == "ready"
    assert payload["qualities"]["q6"]["other_probability"] == 0.0


def test_existing_in_budget_wire_values_remain_byte_stable() -> None:
    result = _result((0.3237, 0.3237, 0.3237))

    raw = result.as_dict()

    assert _probabilities(raw["quality_count_cells_candidates"]["q6"]) == [
        0.3237,
        0.3237,
        0.3237,
    ]
    payload = _quality_composition_payload(raw)
    assert payload["status"] == "ready"
    assert payload["qualities"]["q6"]["other_probability"] == pytest.approx(0.0289)


def test_true_internal_over_budget_distribution_still_fails_closed() -> None:
    result = _result((0.6, 0.5))

    raw = result.as_dict()

    assert sum(_probabilities(raw["quality_count_cells_candidates"]["q6"])) == 1.1
    assert _quality_composition_payload(raw)["status"] == "conflict"
