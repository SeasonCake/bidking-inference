from __future__ import annotations

import copy
import hashlib
import math
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from ahmad_live_panel_server import summarize_snapshot  # noqa: E402
from ahmad_ref_engine import (  # noqa: E402
    AHMED_NONOFFICIAL_Q4_ARM_RISK_CEILING_NOTE_PREFIX,
    AHMED_NONOFFICIAL_Q4_ARM_RISK_CEILING_SPLIT_DISABLE,
    _ahmed_q4_arm_probability_eligible,
    _ahmed_q4_arm_summary_eligible,
    _risk_ceiling_exceeds_ordinary,
    run_reference_engine,
)


UNKNOWN_SOURCE = {
    "qualification": "postupdate_unknown_warehouse_source",
    "reason": "postupdate_non_ranked_source_unresolved",
    "official_distribution_eligible": False,
    "quote_policy": "advisory_only_no_clamp",
}
PLAYER_SOURCE = {
    "qualification": "player_custom_warehouse",
    "reason": "confirmed_player_custom_warehouse",
    "official_distribution_eligible": False,
    "quote_policy": "advisory_only_no_clamp",
}
OFFICIAL_SOURCE = {
    "qualification": "official_distribution",
    "reason": "official_distribution_verified",
    "official_distribution_eligible": True,
    "quote_policy": "normal",
}
SOURCE_INPUT_SHA256 = {
    ROOT / "data" / "raw" / "tables" / "Item.txt": (
        "7c5901ea6fdd680118737d5e5733496f2c802c97d60b0cd70df95a6115fe6625"
    ),
    ROOT
    / "external_references"
    / "AuctionAnalyzer4.13.3"
    / "_decompiled"
    / "MapBidCalculator"
    / "MapBidCalculator"
    / "Models"
    / "StaticData.cs": (
        "7543b4aa16addc73dfaaaf3c6df9bc0f6662bd83458de110a16d04ff9f2f9676"
    ),
}


def _snapshot_2404(*, source: dict = UNKNOWN_SOURCE) -> dict:
    """Sanitized real R3 state for 2404:9000000000000037."""
    context = {
        "hero": "ahmed",
        "map_id": 2404,
        "model_map_id": 2404,
        "round": 3,
        "action_round": 3,
        "phase": "bidding",
        "session_id": "2404:9000000000000037",
        "warehouse_source": copy.deepcopy(source),
        "warehouse_source_qualification": source["qualification"],
        "official_distribution_eligible": source[
            "official_distribution_eligible"
        ],
    }
    minimap_qualities = (3, 4, 6, 3, 2, 2, 2, 3, 5)
    structured = {
        "source": "monitor_pre_settlement_field_updates",
        "hero": "ahmed",
        "total_count": 41,
        "avg_cells": {
            "q1": 1.615384578704834,
            "q4": 2.6666667461395264,
            "q5": 6.333333492279053,
        },
        "counts": {"q3": 18},
        "quality_cells": {},
        "avg_values": {},
        "quality_values": {},
        "quality_value_floors": {},
        "count_sums": {},
        "scoped_count_sums": [],
        "split_avg_cells": {},
        "split_counts": {},
        "split_quality_cells": {},
        "minimap_items": [
            {
                "quality": quality,
                "item_id": None,
                "cells": 1,
                "shape_key": None,
                "local_index": index,
                "row": None,
                "col": None,
                "width": None,
                "height": None,
                "category": None,
                "value": None,
            }
            for index, quality in enumerate(minimap_qualities)
        ],
    }
    return {
        **context,
        "warehouse_source": copy.deepcopy(source),
        "structured_ref_inputs": copy.deepcopy(structured),
        "ahmad_ref_inputs": copy.deepcopy(structured),
        "ui_contract": {
            "context": context,
            "constraints": {
                "structured_ref_inputs": copy.deepcopy(structured),
                "public_info": {},
            },
        },
    }


def _run(snapshot: dict, *, max_combos: int = 8_000) -> dict:
    return run_reference_engine(snapshot, max_combos=max_combos).as_dict()


def _without_split_outputs(result: dict) -> dict:
    stable = copy.deepcopy(result)
    stable.pop("risk_ceiling", None)
    stable.pop("notes", None)
    stable.pop("aggressive", None)
    return stable


def test_2404_exact_numeric_fixture_inputs_are_registered() -> None:
    for path, expected in SOURCE_INPUT_SHA256.items():
        assert path.is_file(), path
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected


@pytest.mark.parametrize(
    ("probability", "eligible"),
    [
        (0.009999, False),
        (0.01, True),
        (0.010001, True),
    ],
)
def test_risk_branch_probability_threshold_is_inclusive(
    probability: float,
    eligible: bool,
) -> None:
    assert _ahmed_q4_arm_probability_eligible(probability) is eligible
    assert _ahmed_q4_arm_summary_eligible((3, 8, probability, 1, 2, 3)) is eligible


@pytest.mark.parametrize(
    "probability",
    [
        True,
        False,
        "0.5",
        None,
        math.nan,
        math.inf,
        -math.inf,
        -0.000001,
        1.000001,
        10**400,
        -(10**400),
    ],
)
def test_malformed_risk_branch_probability_fails_closed(probability: object) -> None:
    assert _ahmed_q4_arm_probability_eligible(probability) is False
    assert _ahmed_q4_arm_summary_eligible((3, 8, probability, 1, 2, 3)) is False


def test_highest_value_subthreshold_branch_is_not_eligible_for_selection() -> None:
    rows = [
        (1, 2, 0.009999, 100.0, 999.0, 2_000.0),
        (2, 4, 0.01, 100.0, 200.0, 300.0),
        (3, 6, 0.010001, 100.0, 250.0, 350.0),
    ]
    eligible = [row for row in rows if _ahmed_q4_arm_summary_eligible(row)]

    assert [row[0] for row in eligible] == [2, 3]
    assert max(eligible, key=lambda row: (row[4], -row[0]))[0] == 3


@pytest.mark.parametrize(
    ("risk", "ordinary"),
    [
        (100, 100),
        (99, 100),
        (math.nan, 100),
        (math.inf, 100),
        (True, 100),
        ("101", 100),
        (10**400, 100),
        (100, 10**400),
        (-(10**400), 100),
        (100, -(10**400)),
    ],
)
def test_nonhigher_or_malformed_risk_amount_is_not_publishable(
    risk: object,
    ordinary: object,
) -> None:
    assert _risk_ceiling_exceeds_ordinary(risk, ordinary) is False


def test_higher_finite_risk_amount_is_publishable() -> None:
    assert _risk_ceiling_exceeds_ordinary(101, 100) is True


@pytest.mark.parametrize("max_combos", [8_000, 50_000])
def test_subthreshold_high_value_branch_cannot_be_selected_for_risk_ceiling(
    max_combos: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2404()
    for path in (
        snapshot["structured_ref_inputs"],
        snapshot["ahmad_ref_inputs"],
        snapshot["ui_contract"]["constraints"]["structured_ref_inputs"],
    ):
        path["avg_cells"]["q4"] = 2.0

    result = _run(snapshot, max_combos=max_combos)
    q4_joint = result["quality_count_cells_candidates"]["q4"]
    risk = result["risk_ceiling"]
    selected = risk["selected_branch"]
    published_probability = {
        (row["count"], row["cells"]): row["prob"] for row in q4_joint
    }
    subthreshold_branches = {
        (branch["count"], branch["cells"])
        for branch in risk["branches"]
        if not branch["eligible"]
    }

    assert subthreshold_branches
    assert selected["eligible"] is True
    assert selected["probability"] >= risk["minimum_branch_probability"]
    assert (selected["count"], selected["cells"]) not in subthreshold_branches
    for branch in risk["branches"]:
        key = (branch["count"], branch["cells"])
        assert key in published_probability
        assert branch["eligible"] is (
            branch["probability"] >= risk["minimum_branch_probability"]
        )
    assert selected["aggressive"] == risk["value"]
    assert risk["value"] > risk["baseline_aggressive"]

    monkeypatch.setenv(
        AHMED_NONOFFICIAL_Q4_ARM_RISK_CEILING_SPLIT_DISABLE,
        "1",
    )
    rollback = _run(snapshot, max_combos=max_combos)
    assert rollback["risk_ceiling"] == {}
    assert rollback["aggressive"] == risk["value"]
    assert _without_split_outputs(result) == _without_split_outputs(rollback)


def test_real_2404_separates_ordinary_aggressive_from_high_arm_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2404()
    default = _run(snapshot)

    monkeypatch.setenv(
        AHMED_NONOFFICIAL_Q4_ARM_RISK_CEILING_SPLIT_DISABLE,
        "1",
    )
    rollback = _run(snapshot)

    assert default["quality_count_cells_candidates"]["q4"] == [
        {"count": 6, "cells": 16, "prob": 0.9008},
        {"count": 3, "cells": 8, "prob": 0.0992},
    ]
    assert default["quality_lock"]["q4"] == {
        "strength": "probabilistic",
        "reason": "combo_multi_solution",
    }
    assert (
        default["conservative"],
        default["balanced"],
        default["aggressive"],
    ) == (384_045, 426_890, 469_734)
    assert rollback["aggressive"] == 857_072
    assert default["risk_ceiling"]["value"] == 857_072
    assert default["risk_ceiling"]["baseline_aggressive"] == 469_734
    assert default["risk_ceiling"]["warehouse_qualification"] == (
        "postupdate_unknown_warehouse_source"
    )
    assert default["risk_ceiling"]["official_distribution_eligible"] is False
    assert default["risk_ceiling"]["selected_branch"] == {
        "quality": "q4",
        "count": 3,
        "cells": 8,
        "probability": 0.09915,
        "eligible": True,
        "value_p25": 631_795,
        "value_p50": 681_469,
        "value_p75": 931_600,
        "conservative": 581_251,
        "balanced": 626_951,
        "aggressive": 857_072,
    }
    assert rollback["risk_ceiling"] == {}
    assert _without_split_outputs(default) == _without_split_outputs(rollback)


def test_real_2404_product_and_audit_caps_match() -> None:
    product = _run(_snapshot_2404(), max_combos=8_000)
    audit = _run(_snapshot_2404(), max_combos=50_000)
    assert product == audit


def test_real_2404_exact_q4_count_removes_ceiling_and_locks_six(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_2404()
    snapshot["structured_ref_inputs"]["counts"]["q4"] = 6
    snapshot["ahmad_ref_inputs"]["counts"]["q4"] = 6
    snapshot["ui_contract"]["constraints"]["structured_ref_inputs"]["counts"][
        "q4"
    ] = 6
    default = _run(snapshot)

    monkeypatch.setenv(
        AHMED_NONOFFICIAL_Q4_ARM_RISK_CEILING_SPLIT_DISABLE,
        "1",
    )
    rollback = _run(snapshot)

    assert default == rollback
    assert default["quality_count_cells_candidates"]["q4"] == [
        {"count": 6, "cells": 16, "prob": 1.0}
    ]
    assert default["quality_lock"]["q4"] == {
        "strength": "exact",
        "reason": "exact_lock",
    }
    assert (
        default["conservative"],
        default["balanced"],
        default["aggressive"],
    ) == (384_045, 426_890, 469_734)
    assert default["risk_ceiling"] == {}


@pytest.mark.parametrize(
    ("source", "qualification"),
    [
        (UNKNOWN_SOURCE, "postupdate_unknown_warehouse_source"),
        (PLAYER_SOURCE, "player_custom_warehouse"),
    ],
)
def test_real_2404_nonofficial_pools_keep_distinct_provenance(
    source: dict,
    qualification: str,
) -> None:
    result = _run(_snapshot_2404(source=source))
    assert result["risk_ceiling"]["warehouse_qualification"] == qualification
    assert result["risk_ceiling"]["official_distribution_eligible"] is False


def test_real_2404_official_and_settlement_are_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    official = _snapshot_2404(source=OFFICIAL_SOURCE)
    default_official = _run(official)
    monkeypatch.setenv(
        AHMED_NONOFFICIAL_Q4_ARM_RISK_CEILING_SPLIT_DISABLE,
        "1",
    )
    rollback_official = _run(official)
    assert default_official == rollback_official
    assert default_official["risk_ceiling"] == {}

    monkeypatch.delenv(
        AHMED_NONOFFICIAL_Q4_ARM_RISK_CEILING_SPLIT_DISABLE,
        raising=False,
    )
    settled = _snapshot_2404()
    settled["phase"] = "settled"
    settled["ui_contract"]["context"]["phase"] = "settled"
    default_settled = _run(settled)
    monkeypatch.setenv(
        AHMED_NONOFFICIAL_Q4_ARM_RISK_CEILING_SPLIT_DISABLE,
        "1",
    )
    rollback_settled = _run(settled)
    assert default_settled == rollback_settled
    assert default_settled["risk_ceiling"] == {}


def test_real_2404_product_summary_uses_ordinary_aggressive(
    tmp_path: Path,
) -> None:
    summary = summarize_snapshot(
        _snapshot_2404(),
        snapshot_path=tmp_path / "latest_snapshot.json",
    )
    assert summary["reference"]["decision_range"] == (
        "384,045 / 426,890 / 469,734"
    )
    assert AHMED_NONOFFICIAL_Q4_ARM_RISK_CEILING_NOTE_PREFIX in (
        summary["evidence"]["diagnostics"]
    )
