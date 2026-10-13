"""B6 quote/display coupling gate for forced-red ``count_kept`` clamps.

The B6 balanced-bid strip used the pre-clamp geometric room, while BUG-J/H later kept at least one
cell for every red item forced by the total-count identity.  That produced a contradictory result:
the panel retained red cells/value but the balanced quote priced the same arm as zero red.

The repair defers only those forced-red strips and reuses the later geometry clamp's actual retained
mid-cell ratio.  Genuine ``forced_red=0`` phantoms stay under the original B6 behavior (covered by
``test_b6_red_cells_geometry_clamp``).  Kill: BIDKING_DISABLE_B6_COUNT_KEPT_BID_COUPLING=1.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "scripts", ROOT / "src", ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402
from audit_aisha_gap import _build_snapshot, _map_id_from_events  # noqa: E402
from bidking_lab.live.fatbeans import (  # noqa: E402
    live_batches_from_fatbeans_events,
    parse_fatbeans_capture,
)

FATBEANS = ROOT / "data" / "samples" / "fatbeans"
KILL = "BIDKING_DISABLE_B6_COUNT_KEPT_BID_COUPLING"
CASES = (
    "fatbeans_valid_aisha_2503_3rounds_2503_1683352582040193_0016.json",
    "fatbeans_valid_aisha_2508_3rounds_2508_1700601007230572_0572.json",
    "fatbeans_valid_aisha_5604_5rounds_5604_1575875059422304_1857.json",
)
TRUTH_TOTALS = {
    CASES[0]: 986_721,
    CASES[1]: 1_132_801,
    CASES[2]: 656_137,
}


def _snapshot(sample: Path) -> dict:
    events = parse_fatbeans_capture(str(sample))
    pre = [batch for batch in live_batches_from_fatbeans_events(events) if batch.phase != "settled"]
    decision_round = next(
        index
        for index in range(len(pre), 0, -1)
        if not all(batch.phase == "reading" for batch in pre[:index])
    )
    return prepare_reference_engine_snapshot(
        _build_snapshot(
            hero="aisha",
            events=events,
            prefix_batches=pre[:decision_round],
            map_id=_map_id_from_events(events),
        )
    )


def _run(snapshot: dict, *, disabled: bool) -> dict:
    previous = os.environ.pop(KILL, None)
    try:
        if disabled:
            os.environ[KILL] = "1"
        return run_reference_engine(snapshot, max_combos=120_000).as_dict()
    finally:
        os.environ.pop(KILL, None)
        if previous is not None:
            os.environ[KILL] = previous


@pytest.mark.parametrize("name", CASES)
def test_count_kept_bid_uses_displayed_red_cell_ratio(name: str) -> None:
    sample = FATBEANS / name
    if not sample.exists():
        pytest.skip(f"sample {name} absent (gitignored) — local-only gate")

    snapshot = _snapshot(sample)
    old = _run(snapshot, disabled=True)
    new = _run(snapshot, disabled=False)

    old_clamp = next(note for note in old["notes"] if "red_cells_geometry_clamp" in str(note))
    new_clamp = next(note for note in new["notes"] if "red_cells_geometry_clamp" in str(note))
    assert str(old_clamp).endswith(":count_kept")
    assert new_clamp == old_clamp
    assert new["red_count_range"] == old["red_count_range"]
    assert new["red_cells_range"] == old["red_cells_range"]
    assert new["red_value_range"] == old["red_value_range"]

    retained = new["red_cells_range"][1]
    original = int(str(new_clamp).split(":", 1)[1].split("->", 1)[0])
    expected_note = f"aisha_no_room_red_bid_strip:bal{retained / original:.2f}:count_kept"
    assert expected_note in new["notes"]
    assert new["balanced"] > old["balanced"]
    truth = TRUTH_TOTALS[name]
    assert abs(new["balanced"] - truth) < abs(old["balanced"] - truth)
    assert new["aggressive"] == old["aggressive"]


def test_2508_keeps_action_100125_as_q5_value_sum() -> None:
    sample = FATBEANS / CASES[1]
    if not sample.exists():
        pytest.skip(f"sample {CASES[1]} absent (gitignored) — local-only gate")

    snapshot = _snapshot(sample)
    assert snapshot["structured_ref_inputs"]["quality_values"]["q5"] == 150_408
    result = _run(snapshot, disabled=False)
    assert "action_100125_q5_value_sum" in result["notes"]
