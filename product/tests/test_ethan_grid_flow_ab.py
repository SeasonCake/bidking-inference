"""Instrumentation gates for the cache-isolated Ethan grid-flow A/B tool."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CALIBRATION = ROOT / "scripts" / "calibration"
if str(CALIBRATION) not in sys.path:
    sys.path.insert(0, str(CALIBRATION))

import ab_ethan_grid_flow as A  # noqa: E402


def _row(
    file_name: str,
    *,
    balanced: int,
    error_pct: float,
    red_count: int = 4,
    cap_note: bool = False,
) -> dict:
    return {
        "file": file_name,
        "map_id": 5601,
        "status": "ok",
        "combo_count": 1,
        "balanced": balanced,
        "error_pct": error_pct,
        "truth_red_count": red_count,
        "grid_mid_error": 0,
        "q4_mid_error": 0,
        "q5_mid_error": 0,
        "q6_mid_error": 0,
        "path_flags": {
            "occupancy": False,
            "full_outline": True,
            "value_cell_floor": False,
            "red_resid_lift": True,
            "red_residual_revalue": False,
            "q4_mid_resid": False,
            "q5_scan_lift_cap": cap_note,
        },
    }


def test_complete_grid_flow_positive_and_negative_controls() -> None:
    positive = frozenset(
        {
            A.ACTION_IDS["q1_scan"],
            A.ACTION_IDS["q3_scan"],
            A.ACTION_IDS["q4_avg"],
            A.ACTION_IDS["q5_scan"],
        }
    )
    assert A._complete_grid_flow(positive)
    assert not A._complete_grid_flow(positive - {A.ACTION_IDS["q3_scan"]})
    assert not A._complete_grid_flow(positive - {A.ACTION_IDS["q4_avg"]})
    assert not A._complete_grid_flow(positive - {A.ACTION_IDS["q5_scan"]})


def test_paired_summary_distinguishes_improvement_from_no_change() -> None:
    base = [
        _row("changed.json", balanced=150, error_pct=50.0),
        _row("same.json", balanced=100, error_pct=0.0),
    ]
    arm = [
        _row("changed.json", balanced=110, error_pct=10.0, cap_note=True),
        _row("same.json", balanced=100, error_pct=0.0),
    ]
    paired = A._paired_summary(base, arm)
    assert paired["changed_any"] == 1
    assert paired["changed_quote"] == 1
    assert paired["paired_improved"] == 1
    assert paired["paired_worsened"] == 0


def test_legacy_arm_instrumentation_has_known_good_and_known_fail() -> None:
    base = [_row("sample.json", balanced=110, error_pct=10.0, cap_note=True)]
    legacy = [_row("sample.json", balanced=150, error_pct=50.0, cap_note=False)]
    paired = A._paired_summary(base, legacy)
    ok, reasons = A._instrumentation_check("q5_scan_cap_legacy", paired)
    assert ok
    assert reasons == []

    bad_paired = A._paired_summary(base, base)
    ok, reasons = A._instrumentation_check("q5_scan_cap_legacy", bad_paired)
    assert not ok
    assert "kill_switch_failed:q5_scan_lift_cap_still_applied" in reasons
    assert "instrumentation_alarm:all_outputs_identical" in reasons


def test_arm_environment_clears_conflicting_parent_values(monkeypatch) -> None:
    monkeypatch.setenv("ETHAN_RED_RESID_LIFT_Q5_SCAN_CAP", "1.99")
    monkeypatch.setenv("ETHAN_DISABLE_RED_RESID_LIFT_Q5_SCAN_CAP", "0")
    env = A._arm_environment("q5_scan_cap_legacy")
    assert "ETHAN_RED_RESID_LIFT_Q5_SCAN_CAP" not in env
    assert env["ETHAN_DISABLE_RED_RESID_LIFT_Q5_SCAN_CAP"] == "1"
