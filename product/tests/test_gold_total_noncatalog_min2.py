"""Design gate: a gold(q5) TOTAL value that matches no single-item catalog price forces count >= 2.

The game's per-item gold prices are exact catalog values, so a single gold item's value must equal some
single-item catalog price (author domain). The avg path already excludes count=1 for a non-catalog avg;
the total-value path used to let count=1 through unchecked (_quality_count_matches_value_inputs returns
exact_value>0 for any count). Symmetric fix in _quality_count_matches_value_evidence. Gold only.

env AHMAD_DISABLE_GOLD_TOTAL_NONCATALOG_MIN2=1 reverts. Pure synthetic snapshot (no PII).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "hero_ref" / "src", ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import ahmad_ref_engine as A  # noqa: E402
from ahmad_ref_engine import run_reference_engine  # noqa: E402

ENV = "AHMAD_DISABLE_GOLD_TOTAL_NONCATALOG_MIN2"


def _catalog_gold_price() -> int:
    tq = A._quality_key_item_table_quality("q5")
    vals = A._exact_item_values_by_table_quality().get(int(tq)) if tq is not None else None
    assert vals, "gold catalog must be populated (Item.txt + items.json backfill)"
    return sorted(vals)[len(vals) // 2]


def _non_catalog_value(base: int) -> int:
    v = base + 123
    while A._exact_catalog_contains_value("q5", v):
        v += 1
    return v


def _snapshot(total: float) -> dict:
    return {
        "ui_contract": {"context": {"hero": "ahmed", "map_id": 4402, "phase": "bidding"}},
        "structured_ref_inputs": {"total_count": 8, "quality_values": {"q5": float(total)}},
    }


def _q5_range(total: float, disable: bool, monkeypatch) -> list:
    monkeypatch.delenv(ENV, raising=False)
    if disable:
        monkeypatch.setenv(ENV, "1")
    return list(run_reference_engine(_snapshot(total), max_combos=60_000).as_dict()["quality_count_ranges"]["q5"])


def test_noncatalog_gold_total_forces_min_two(monkeypatch) -> None:
    cat = _catalog_gold_price()
    non = _non_catalog_value(cat)
    on = _q5_range(non, disable=False, monkeypatch=monkeypatch)
    off = _q5_range(non, disable=True, monkeypatch=monkeypatch)
    assert on[0] >= 2, f"non-catalog gold total must exclude count=1, got {on}"
    assert off[0] == 1, f"env revert should restore count=1, got {off}"


def test_catalog_gold_price_still_allows_one(monkeypatch) -> None:
    cat = _catalog_gold_price()
    on = _q5_range(cat, disable=False, monkeypatch=monkeypatch)
    # a total equal to an exact single-item catalog price is a legitimate 1-item vault — never false-lock it
    assert on[0] == 1, f"catalog single-item gold price must still allow count=1, got {on}"
