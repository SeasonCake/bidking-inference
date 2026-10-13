"""E09: hidden-red low CPI tail is opt-in and conservative-only."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools", ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from ahmad_ref_engine import RED_CPI_LOW_TAIL_ENABLE, run_reference_engine  # noqa: E402


def _snapshot(*, q6_cells: int | None = None) -> dict:
    structured = {
        "total_count": 10,
        "fixed_counts": {"q1": 2, "q3": 2, "q4": 2, "q5": 3, "q6": 1},
    }
    if q6_cells is not None:
        structured["quality_cells"] = {"q6": q6_cells}
    return {
        "ui_contract": {
            "context": {"hero": "victor", "map_id": 2404, "phase": "bidding", "round": 3},
            "constraints": {"public_info": {}},
        },
        "structured_ref_inputs": structured,
        "public_info_rows": [],
    }


def _run(monkeypatch: pytest.MonkeyPatch, *, enabled: bool, q6_cells: int | None = None) -> dict:
    if enabled:
        monkeypatch.setenv(RED_CPI_LOW_TAIL_ENABLE, "1")
    else:
        monkeypatch.delenv(RED_CPI_LOW_TAIL_ENABLE, raising=False)
    return run_reference_engine(_snapshot(q6_cells=q6_cells), max_combos=20_000).as_dict()


def test_e09_default_off_is_head_equivalent(monkeypatch: pytest.MonkeyPatch) -> None:
    default = _run(monkeypatch, enabled=False)
    monkeypatch.setenv(RED_CPI_LOW_TAIL_ENABLE, "0")
    explicit_off = run_reference_engine(_snapshot(), max_combos=20_000).as_dict()
    assert explicit_off == default
    assert not any(str(note).startswith("red_cpi_low_tail:") for note in default["notes"])


def test_e09_hidden_red_adds_low_probability_and_lowers_only_conservative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    off = _run(monkeypatch, enabled=False)
    on = _run(monkeypatch, enabled=True)

    assert off["red_count_range"] == on["red_count_range"] == [1, 1, 1]
    assert on["red_cells_range"][0] < off["red_cells_range"][0]
    assert on["red_cells_range"][1:] == off["red_cells_range"][1:]
    assert on["red_value_range"][0] < off["red_value_range"][0]
    assert on["red_value_range"][1:] == off["red_value_range"][1:]
    assert on["conservative"] < off["conservative"]
    assert on["balanced"] == off["balanced"]
    assert on["aggressive"] == off["aggressive"]
    assert any(str(note).startswith("red_cpi_low_tail:") for note in on["notes"])

    candidates = on["quality_count_cells_candidates"]["q6"]
    one_cell = next(row for row in candidates if row["count"] == 1 and row["cells"] == 1)
    assert one_cell["prob"] == pytest.approx(0.12, abs=0.001)


def test_e09_known_red_cells_are_a_complete_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    off = _run(monkeypatch, enabled=False, q6_cells=4)
    on = _run(monkeypatch, enabled=True, q6_cells=4)
    assert on == off
