from __future__ import annotations

import copy
import hashlib
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import ahmad_live_panel_server as panel  # noqa: E402
from ahmad_live_panel_server import (  # noqa: E402
    ServerConfig,
    make_handler,
    summarize_snapshot,
)
from ahmad_ref_engine import (  # noqa: E402
    AHMED_Q1_Q6_POSITIVE_RESIDUAL_ANTILOCK_DISABLE,
    AHMED_Q1_Q6_POSITIVE_RESIDUAL_ANTILOCK_NOTE,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)
from ahmad_tk_overlay import _summary_diagnostic_row  # noqa: E402


_EARLY_QUOTES = (642_142, 702_524, 762_906)
_Q1_CELLS_QUOTES = (2_051_737, 2_184_426, 2_317_116)
_MODEL_INPUT_SHA256 = {
    Path(
        "external_references/AuctionAnalyzer4.13.3/_decompiled/"
        "MapBidCalculator/MapBidCalculator/Models/StaticData.cs"
    ): "7543b4aa16addc73dfaaaf3c6df9bc0f6662bd83458de110a16d04ff9f2f9676",
    Path("data/processed/drop_table_priors_0627.json"): (
        "7ff3859d88a74e184074a8df58d11a284755376eeb6cced7befc697212a3d02c"
    ),
    Path("data/processed/items.json"): (
        "164b5a5a667161447fde86fd306c65a38bb0d5ef2070be7eb781482bd21e84e9"
    ),
}


@pytest.fixture(scope="module", autouse=True)
def _require_approved_model_inputs() -> None:
    """Fail explicitly when a source/audit tree forgot copy-only inputs."""

    for relative, expected_sha256 in _MODEL_INPUT_SHA256.items():
        path = ROOT / relative
        if not path.is_file():
            pytest.fail(f"required package input missing: {relative.as_posix()}")
        actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual_sha256 == expected_sha256, relative.as_posix()


def _ahmed_2502_r3_field_cut(*, include_q1_cells: bool = False) -> dict:
    """Qualified field cut from HeroRefDiag 2502:1902361467323411.

    The export contains the final support-safe snapshot, not the original WSS
    timeline. These are the exact structured fields present by sequence 17;
    ``include_q1_cells`` adds the action-100104 field that arrived at sequence
    19. The fixture therefore proves the engine contract, not a byte-for-byte
    replay of the author's first live red-count display.
    """

    structured = {
        "hero": "ahmed",
        "source": "structured_ref_bridge",
        "total_count": 57,
        "counts": {"q3": 15, "q5": 7},
        "avg_cells": {
            "q1": 1.125,
            "q4": 2.8235294818878174,
            "q5": 3.2857143878936768,
        },
    }
    if include_q1_cells:
        structured["quality_cells"] = {"q1": 9}
    return {
        "hero": "ahmed",
        "hero_id": 204,
        "map_id": 2502,
        "round": 3,
        "phase": "bidding",
        "structured_ref_inputs": structured,
        "ui_contract": {
            "context": {
                "hero": "ahmed",
                "map_id": 2502,
                "phase": "bidding",
                "round": 3,
            },
            "constraints": {"public_info": {}},
        },
        "inference_input_constraints": {
            "mode": "pre_settlement_trusted_totals",
            "total_item_count": {
                "value": 57,
                "source": "packet",
                "confidence": "exact",
            },
        },
        "public_info_rows": [],
        "skill_reveals": [],
        "skill_reveal_rows": [],
    }


def _positive_residual_prefix_counterexample() -> dict:
    """A generic support whose first cap+1 prefix still shares q1."""

    snapshot = _ahmed_2502_r3_field_cut()
    snapshot["map_id"] = 2501
    snapshot["ui_contract"]["context"]["map_id"] = 2501
    snapshot["structured_ref_inputs"].update(
        {
            "total_count": 40,
            "counts": {"q3": 10, "q5": 5},
            "avg_cells": {
                "q1": 9 / 8,
                "q4": 12 / 5,
                "q5": 18 / 5,
            },
        }
    )
    snapshot["inference_input_constraints"]["total_item_count"]["value"] = 40
    return snapshot


@pytest.mark.parametrize("prepared", [False, True], ids=("direct", "prepared"))
@pytest.mark.parametrize("max_combos", [2, 3, 4, 8_000, 50_000])
def test_ahmed_2502_positive_q6_residual_arm_stays_visible_and_probabilistic(
    prepared: bool,
    max_combos: int,
) -> None:
    snapshot = _ahmed_2502_r3_field_cut()
    if prepared:
        snapshot = prepare_reference_engine_snapshot(copy.deepcopy(snapshot))

    result = run_reference_engine(snapshot, max_combos=max_combos).as_dict()

    assert result["status"] == "ok"
    assert result["combo_count"] == 2
    assert result["quality_count_ranges"]["q1"] == [8, 16, 16]
    assert result["red_count_range"] == [2, 2, 10]
    assert result["quality_lock"]["q1"] == {
        "strength": "probabilistic",
        "reason": "combo_multi_solution",
    }
    assert result["quality_lock"]["q6"] == {
        "strength": "probabilistic",
        "reason": "combo_multi_solution",
    }
    assert result["quality_count_candidates"]["q1"] == [
        {"value": 16, "prob": 0.9957},
        {"value": 8, "prob": 0.0043},
    ]
    assert result["quality_count_candidates"]["q6"] == [
        {"value": 2, "prob": 0.9957},
        {"value": 10, "prob": 0.0043},
    ]
    assert {
        row["count"]
        for row in result["quality_count_cells_candidates"]["q1"]
    } == {8, 16}
    assert {
        row["count"]
        for row in result["quality_count_cells_candidates"]["q6"]
    } == {2, 10}
    assert AHMED_Q1_Q6_POSITIVE_RESIDUAL_ANTILOCK_NOTE in result["notes"]
    # Do not calibrate one live truth into the map prior. This change repairs
    # exact/support semantics while leaving the established quote posterior.
    assert (
        result["conservative"],
        result["balanced"],
        result["aggressive"],
    ) == _EARLY_QUOTES


@pytest.mark.parametrize("prepared", [False, True], ids=("direct", "prepared"))
@pytest.mark.parametrize("max_combos", [1, 2, 3])
def test_positive_residual_truncated_prefix_fails_closed_before_publication(
    prepared: bool,
    max_combos: int,
) -> None:
    snapshot = _positive_residual_prefix_counterexample()
    if prepared:
        snapshot = prepare_reference_engine_snapshot(copy.deepcopy(snapshot))

    result = run_reference_engine(snapshot, max_combos=max_combos).as_dict()

    assert result["status"] == "incomplete_support"
    assert result["combo_count"] == 0
    assert (result["conservative"], result["balanced"], result["aggressive"]) == (
        None,
        None,
        None,
    )
    assert result["quality_count_ranges"] == {}
    assert result["quality_cells_ranges"] == {}
    assert result["quality_count_candidates"] == {}
    assert result["quality_cells_candidates"] == {}
    assert result["quality_count_cells_candidates"] == {}
    assert result["quality_lock"] == {}
    assert result["red_count_range"] == [None, None, None]
    assert result["red_cells_range"] == [None, None, None]
    assert result["red_value_range"] == [None, None, None]
    assert result["total_grid_range"] == [None, None, None]
    assert (
        f"{AHMED_Q1_Q6_POSITIVE_RESIDUAL_ANTILOCK_NOTE}:support_incomplete"
        in result["notes"]
    )


@pytest.mark.parametrize("prepared", [False, True], ids=("direct", "prepared"))
@pytest.mark.parametrize("max_combos", [4, 8_000, 50_000])
def test_positive_residual_complete_support_publishes_all_four_solutions(
    prepared: bool,
    max_combos: int,
) -> None:
    snapshot = _positive_residual_prefix_counterexample()
    if prepared:
        snapshot = prepare_reference_engine_snapshot(copy.deepcopy(snapshot))

    result = run_reference_engine(snapshot, max_combos=max_combos).as_dict()

    assert result["status"] == "ok"
    assert result["combo_count"] == 4
    assert {
        row["value"] for row in result["quality_count_candidates"]["q1"]
    } == {8, 16}
    assert {
        row["value"] for row in result["quality_count_candidates"]["q6"]
    } == {2, 4, 7, 12}
    assert result["quality_lock"]["q1"] == {
        "strength": "probabilistic",
        "reason": "combo_multi_solution",
    }
    assert result["quality_lock"]["q6"] == {
        "strength": "probabilistic",
        "reason": "combo_multi_solution",
    }


def test_ahmed_2502_positive_residual_support_prefix_fails_closed() -> None:
    result = run_reference_engine(
        _ahmed_2502_r3_field_cut(),
        max_combos=1,
    ).as_dict()

    assert result["status"] == "incomplete_support"
    assert result["combo_count"] == 0
    assert result["conservative"] is None
    assert result["balanced"] is None
    assert result["aggressive"] is None
    assert result["quality_count_ranges"] == {}
    assert result["red_count_range"] == [None, None, None]
    assert (
        f"{AHMED_Q1_Q6_POSITIVE_RESIDUAL_ANTILOCK_NOTE}:support_incomplete"
        in result["notes"]
    )


def test_positive_residual_antilock_is_not_map_or_sample_value_hardcoded() -> None:
    snapshot = _ahmed_2502_r3_field_cut()
    snapshot["map_id"] = 2501
    snapshot["ui_contract"]["context"]["map_id"] = 2501
    snapshot["structured_ref_inputs"].update(
        {
            "total_count": 53,
            "counts": {"q3": 18, "q5": 5},
            "avg_cells": {
                "q1": 11 / 8,
                "q4": 14 / 5,
                "q5": 18 / 5,
            },
        }
    )
    snapshot["inference_input_constraints"]["total_item_count"]["value"] = 53

    result = run_reference_engine(snapshot, max_combos=50_000).as_dict()

    assert result["quality_count_ranges"]["q1"] == [8, 16, 24]
    assert result["red_count_range"] == [1, 4, 12]
    assert result["quality_lock"]["q1"]["strength"] == "probabilistic"
    assert result["quality_lock"]["q6"]["strength"] == "probabilistic"
    assert AHMED_Q1_Q6_POSITIVE_RESIDUAL_ANTILOCK_NOTE in result["notes"]


def test_ahmed_2502_positive_residual_antilock_rollback_is_exact_parent_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(AHMED_Q1_Q6_POSITIVE_RESIDUAL_ANTILOCK_DISABLE, "1")

    result = run_reference_engine(
        _ahmed_2502_r3_field_cut(),
        max_combos=50_000,
    ).as_dict()

    assert result["quality_count_ranges"]["q1"] == [16, 16, 16]
    assert result["red_count_range"] == [2, 2, 2]
    assert result["quality_lock"]["q1"]["strength"] == "exact"
    assert result["quality_lock"]["q6"]["strength"] == "exact"
    assert AHMED_Q1_Q6_POSITIVE_RESIDUAL_ANTILOCK_NOTE not in result["notes"]
    assert (
        result["conservative"],
        result["balanced"],
        result["aggressive"],
    ) == _EARLY_QUOTES


@pytest.mark.parametrize("prepared", [False, True], ids=("direct", "prepared"))
def test_ahmed_2502_q1_total_cells_keeps_the_true_q6_exact_lock(
    prepared: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _ahmed_2502_r3_field_cut(include_q1_cells=True)
    if prepared:
        snapshot = prepare_reference_engine_snapshot(copy.deepcopy(snapshot))

    result = run_reference_engine(snapshot, max_combos=50_000).as_dict()
    monkeypatch.setenv(AHMED_Q1_Q6_POSITIVE_RESIDUAL_ANTILOCK_DISABLE, "1")
    rollback = run_reference_engine(snapshot, max_combos=50_000).as_dict()

    assert result == rollback
    assert result["combo_count"] == 1
    assert result["quality_count_ranges"]["q1"] == [8, 8, 8]
    assert result["red_count_range"] == [10, 10, 10]
    assert result["quality_lock"]["q1"] == {
        "strength": "exact",
        "reason": "exact_lock",
    }
    assert result["quality_lock"]["q6"] == {
        "strength": "exact",
        "reason": "derived_single_solution",
    }
    assert AHMED_Q1_Q6_POSITIVE_RESIDUAL_ANTILOCK_NOTE not in result["notes"]
    assert (
        result["conservative"],
        result["balanced"],
        result["aggressive"],
    ) == _Q1_CELLS_QUOTES


@pytest.mark.parametrize(
    ("mutation", "expected_note"),
    [
        ({"hero": "aisha", "hero_id": 200}, False),
        ({"phase": "settled"}, False),
    ],
)
def test_ahmed_2502_positive_residual_antilock_is_not_cross_hero_or_settlement(
    mutation: dict,
    expected_note: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _ahmed_2502_r3_field_cut()
    snapshot.update(mutation)
    snapshot["ui_contract"]["context"].update(mutation)

    result = run_reference_engine(snapshot, max_combos=50_000).as_dict()
    monkeypatch.setenv(AHMED_Q1_Q6_POSITIVE_RESIDUAL_ANTILOCK_DISABLE, "1")
    rollback = run_reference_engine(snapshot, max_combos=50_000).as_dict()

    assert result == rollback
    assert (
        AHMED_Q1_Q6_POSITIVE_RESIDUAL_ANTILOCK_NOTE in result["notes"]
    ) is expected_note


@pytest.mark.parametrize("case", ["missing-q4-avg", "conflicting-q4-cells"])
def test_positive_residual_antilock_ignores_missing_or_conflicting_evidence(
    case: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _ahmed_2502_r3_field_cut()
    if case == "missing-q4-avg":
        snapshot["structured_ref_inputs"]["avg_cells"].pop("q4")
    else:
        snapshot["structured_ref_inputs"]["quality_cells"] = {"q4": 13}

    result = run_reference_engine(snapshot, max_combos=50_000).as_dict()
    monkeypatch.setenv(AHMED_Q1_Q6_POSITIVE_RESIDUAL_ANTILOCK_DISABLE, "1")
    rollback = run_reference_engine(snapshot, max_combos=50_000).as_dict()

    assert result == rollback
    assert AHMED_Q1_Q6_POSITIVE_RESIDUAL_ANTILOCK_NOTE not in result["notes"]


def test_ahmed_2502_package_shaped_summary_api_panel_and_tk_share_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _ahmed_2502_r3_field_cut()
    snapshot_path = tmp_path / "latest_snapshot.json"
    snapshot_path.write_text(
        json.dumps(snapshot, ensure_ascii=False),
        encoding="utf-8",
    )

    summary = summarize_snapshot(copy.deepcopy(snapshot), snapshot_path=snapshot_path)
    monkeypatch.setattr(panel, "_read_json", lambda _path: copy.deepcopy(snapshot))
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(
            ServerConfig(project_root=tmp_path, snapshot=Path("latest_snapshot.json"))
        ),
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with urlopen(  # noqa: S310 - self-created loopback product contract
            f"http://127.0.0.1:{server.server_port}/api/latest",
            timeout=5,
        ) as response:
            assert response.status == 200
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)

    tk_row = _summary_diagnostic_row(
        payload,
        snapshot_path=snapshot_path,
        render_mode="live",
        manual_active=False,
        settlement_values_hidden=False,
    )
    for consumer in (summary, payload):
        assert consumer["ahmed_ref"]["red_count_range"] == [2, 2, 10]
        assert consumer["ahmed_ref"]["quality_lock"]["q6"] == {
            "strength": "probabilistic",
            "reason": "combo_multi_solution",
        }
        assert consumer["red"]["count_range"] == "2 / 2 / 10"
        assert "10件 0.4%" in consumer["red"]["count_probability_hover"]
        assert consumer["reference"]["decision_range"] == (
            "642,142 / 702,524 / 762,906"
        )
    assert tk_row["red"]["count_range"] == "2 / 2 / 10"
    assert tk_row["reference"]["decision_range"] == (
        "642,142 / 702,524 / 762,906"
    )
