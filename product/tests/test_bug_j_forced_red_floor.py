"""BUG-J gate (族A): a red the ARITHMETIC forces into existence can never be clamped to 0 cells.

群友 aisha 5611 R3 截图: 红件 4/3/1 · 红格 **0/0/14** · 红值 **0/0/…**. The engine simultaneously carried
3-4 reds and claimed they occupy zero cells — every item occupies at least one cell, so that
composition cannot exist. Cause: the B6 red-cells geometry clamp drove the cells to the geometric room
(83 − 17 − 32 − 29 − 5 = 0) and then scaled the red count/value by the same factor.

But zeroing is CORRECT for the clamp's original target: a PHANTOM red (pure prior, truth 0) with no
room must die — that is B6's whole purpose (2401 R3). `_is_pure_prior_red` (= no red-SPECIFIC evidence)
cannot separate the two, because an invisible red can still be FORCED by the item identity:

    forced_red = total_count − Σ(other tiers' count highs)        5611: 35 − 10 − 12 − 7 − 3 = 3

A forced red is not a phantom, however invisible. Its cells are floored at its count (1 cell/item) and
its count is never scaled by a cells ratio. A phantom (forced == 0) keeps the old kill-the-phantom path,
so B6's design gate (test_b6_red_cells_geometry_clamp) still passes unchanged.
Kill: BIDKING_DISABLE_RED_COUNT_CLAMP_GUARD=1 (restores the pre-fix clamp entirely).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "hero_ref" / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "hero_ref" / "src"))

from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402

KILL = "BIDKING_DISABLE_RED_COUNT_CLAMP_GUARD"
KILL_B6_COUPLING = "BIDKING_DISABLE_B6_COUNT_KEPT_BID_COUPLING"
# 族D (格数恒等式对账) independently prevents this bug: the clamp's room is `total grid − Σ non-red
# cells`, and before 族D those non-red cells were the un-reconciled count×prior ones, so the room came
# out at 0 and crushed the forced red. With the tiers reconciled the room is honest and the red keeps
# its cells even with BUG-J's own guard off. To still REPRODUCE the screenshot in the OFF arm (the
# baseline that proves the bug was real), both fixes must be disabled — otherwise the OFF arm silently
# stops being a reproduction and the gate degrades into asserting nothing.
KILL_FAMD = "BIDKING_DISABLE_GRID_IDENTITY_RECONCILE"
FIXTURE = ROOT / "data" / "samples" / "recordings_inbox_20260711_desktop_diag5611_zerocells"


def _run(
    monkeypatch,
    off: bool,
    famd: bool | None = None,
    *,
    b6_coupling: bool = True,
) -> dict:
    """`off` toggles BUG-J's guard. `famd` overrides 族D independently (None = follow `off`), which is
    what lets the quote-neutrality test hold 族D constant and A/B BUG-J alone."""
    if off:
        monkeypatch.setenv(KILL, "1")
    else:
        monkeypatch.delenv(KILL, raising=False)
    if (off if famd is None else not famd):
        monkeypatch.setenv(KILL_FAMD, "1")
    else:
        monkeypatch.delenv(KILL_FAMD, raising=False)
    if b6_coupling:
        monkeypatch.delenv(KILL_B6_COUPLING, raising=False)
    else:
        monkeypatch.setenv(KILL_B6_COUPLING, "1")
    snap = json.loads((FIXTURE / "r03_bidding.json").read_text(encoding="utf-8"))
    return run_reference_engine(prepare_reference_engine_snapshot(snap), max_combos=12000).as_dict()


@pytest.mark.skipif(
    not (FIXTURE / "r03_bidding.json").exists(),
    reason="5611 zero-cells snapshot absent (gitignored) — local-only gate",
)
def test_5611_forced_red_keeps_at_least_one_cell_per_item(monkeypatch) -> None:
    off = _run(monkeypatch, off=True)
    on = _run(monkeypatch, off=False)

    # OFF reproduces the defect: arithmetic still forces reds into the quality posterior, but the
    # geometry display scales their count/cells/value away.  Later catalog constraints may legally
    # narrow the old [3,4,4] posterior (currently [3,3,3]), so pin the cross-arm invariant instead of
    # freezing an unrelated posterior shape.
    quality_counts = off["quality_count_ranges"]["q6"]
    assert quality_counts == on["quality_count_ranges"]["q6"]
    assert min(quality_counts) >= 3
    assert off["red_cells_range"][0] == 0 and off["red_cells_range"][1] == 0
    assert off["red_value_range"][0] == 0 and off["red_value_range"][1] == 0
    assert off["red_count_range"] != quality_counts

    # ON: cells >= count at every tier of the triple, count coherent, value no longer zero
    counts = quality_counts
    cells = on["red_cells_range"]
    assert on["red_count_range"] == counts  # no cells-ratio scaling of a forced red
    for i in range(3):
        assert cells[i] >= counts[i], (cells, counts)
    assert on["red_value_range"][0] > 0 and on["red_value_range"][1] > 0


@pytest.mark.skipif(
    not (FIXTURE / "r03_bidding.json").exists(),
    reason="5611 zero-cells snapshot absent (gitignored) — local-only gate",
)
def test_5611_balanced_quote_is_unchanged(monkeypatch) -> None:
    # The repair is a coherence repair: the balanced quote (driven by the combo posterior) does not move
    # on this sample; only the red cells/value LOW end stops being an impossible zero.
    # 族D is held OFF in BOTH arms: it IS quote-changing by design (it re-deals the cells and the red is
    # priced off them), so leaving it on would make this assert 族D's neutrality, not BUG-J's.
    off = _run(monkeypatch, off=True, famd=False, b6_coupling=False)
    on = _run(monkeypatch, off=False, famd=False, b6_coupling=False)
    assert on["balanced"] == off["balanced"]


@pytest.mark.skipif(
    not (FIXTURE / "r03_bidding.json").exists(),
    reason="5611 zero-cells snapshot absent (gitignored) — local-only gate",
)
def test_5611_default_b6_quote_follows_count_kept_red_value(monkeypatch) -> None:
    """The later B6 coupling intentionally changes pricing; BUG-J alone remains quote-neutral above."""
    old = _run(monkeypatch, off=False, famd=False, b6_coupling=False)
    new = _run(monkeypatch, off=False, famd=False, b6_coupling=True)
    assert new["balanced"] > old["balanced"]
    assert any(str(note).endswith(":count_kept") for note in new["notes"])
