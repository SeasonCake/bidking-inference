"""Pin the 56xx ranked count center into a gate.

`_default_total_count(map_id)` supplies the prior total-item count used when the
real count is not yet public (early/unlocked rounds). For the 56xx ranked family
the true vault is far larger than the classic-28 default: §5 measured 637 settled
ranked games (group_0625_56xx) with **count median 37 (mean 36.9)** and per-map
medians tightly in 36-38 — so a single center of 37 is correct and per-map split
is not worth it. See `engine-no-56xx-rank-prior-red-underprimed` memory +
`data/reports/per_map_accuracy_0625.md`.

This is a pure-function unit gate (no engine DP): if someone re-touches
`_default_total_count` and drops the family-56 branch, the center silently falls
back to 28 → non-ahmed heroes (ethan/aisha/sophie/gabriela) under-count ~24% on
ranked. These asserts catch that regression. The env override is the documented
escape hatch.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))

from ahmad_ref_engine import _default_total_count  # noqa: E402

RANK56_CENTER = 37
CLASSIC_DEFAULT = 28


@pytest.mark.parametrize("map_id", [5601, 5604, 5608, 5610, 5611, 5699])
def test_rank56_count_center_is_37(map_id: int) -> None:
    assert _default_total_count(map_id) == RANK56_CENTER


@pytest.mark.parametrize(
    "map_id,expected",
    [
        (2401, 28),   # classic 24xx unchanged
        (2301, 27),   # 23xx
        (2101, 24),   # 21xx
        (2501, 33),   # 25xx
        (4501, 33),   # 45xx
        (2601, 33),   # 26xx
        (None, 28),   # unknown -> classic default
    ],
)
def test_non_rank56_families_unchanged(map_id, expected: int) -> None:
    """The family-56 branch must not perturb any other family's center."""
    assert _default_total_count(map_id) == expected


def test_rank56_env_override_reverts_to_classic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AHMAD_DISABLE_RANK56_COUNT_CENTER", "1")
    assert _default_total_count(5608) == CLASSIC_DEFAULT
    # other families remain unaffected by the override
    assert _default_total_count(2401) == 28
