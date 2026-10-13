"""Design gate: the random_avg_red_anchor q6-value floor is default-OFF for ETHAN.

The anchor is a count/value-hero mechanism (aisha/ahmed): a high random-check per-item avg is taken
as proof of a jackpot red carrying ~the draw total (0.95 * N*avg), floored onto q6 value. Ethan is
CELL-centric — red value comes from red CELLS x per-cell price, not a value floor — so the anchor
conflicts with ethan's valuation and over-reaches when the random-check avg reflects the item's
CATALOG/tier value rather than a poor vault's cheap instance value.

Measured on clean_eval (2026-07-09): across ethan/aisha/ahmed decision rounds (480) the anchor fires
only twice — ethan 5605 (villa/56xx poor vault, 9*120,388=1.08M floor on a 239k vault -> +332% over-bid;
disabling it lands +6%) and ahmed 2401 (rich vault, floor < total, working). Never on any ethan 3+red
vault. So ethan gets zero benefit and one catastrophe -> default-OFF for ethan, ON for other heroes.

Env: default OFF for ethan; ETHAN_ENABLE_RANDOM_AVG_RED_ANCHOR=1 force-on (A/B). Global kill for all
heroes: BIDKING_DISABLE_RANDOM_AVG_RED_ANCHOR=1. Pure synthetic snapshot — no PII sample needed.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "hero_ref" / "src",):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from ahmad_ref_engine import run_reference_engine  # noqa: E402

ANCHOR_NOTE_PREFIX = "random_avg_red_anchor_q6_value_floor"


def _snapshot(hero: str) -> dict:
    # random_9_avg_value = 120,388 mirrors the ethan 5605 catastrophe (9 * 120,388 = 1.08M draw total,
    # 0.95x -> ~1.03M red value floor). max_random_avg 120,388 >= 100k threshold; q6 absent -> the
    # anchor's fixed_counts.get("q6", 1) default makes the gate pass, so force-on fires for any hero.
    return {
        "ui_contract": {
            "context": {"hero": hero, "map_id": 5605, "phase": "bidding"},
            "constraints": {
                "public_info": {
                    "public_numeric_facts": [
                        {
                            "semantic": "random_9_avg_value",
                            "kind": "random_avg_value",
                            "sample_count": 9,
                            "value": 120_388,
                        }
                    ],
                },
            },
        },
        "structured_ref_inputs": {"total_count": 40},
    }


def _anchor_fired(hero: str, env: dict[str, str], monkeypatch) -> bool:
    for k in ("ETHAN_ENABLE_RANDOM_AVG_RED_ANCHOR", "BIDKING_DISABLE_RANDOM_AVG_RED_ANCHOR"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    result = run_reference_engine(_snapshot(hero), max_combos=60_000).as_dict()
    return any(str(n).startswith(ANCHOR_NOTE_PREFIX) for n in result["notes"])


def test_ethan_anchor_default_off(monkeypatch) -> None:
    # Default (no env): ethan must NOT anchor red value on the random-check draw total.
    assert not _anchor_fired("ethan", {}, monkeypatch)


def test_ethan_anchor_force_on_restores(monkeypatch) -> None:
    # A/B escape hatch: force-on restores the old (firing) behaviour for ethan.
    assert _anchor_fired("ethan", {"ETHAN_ENABLE_RANDOM_AVG_RED_ANCHOR": "1"}, monkeypatch)


def test_non_ethan_anchor_still_fires(monkeypatch) -> None:
    # Zero regression for count/value heroes: ahmed still anchors on the same random check.
    assert _anchor_fired("ahmed", {}, monkeypatch)
    assert _anchor_fired("aisha", {}, monkeypatch)


def test_global_kill_disables_for_all(monkeypatch) -> None:
    # The global kill switch turns the anchor off even for the count/value heroes.
    assert not _anchor_fired("ahmed", {"BIDKING_DISABLE_RANDOM_AVG_RED_ANCHOR": "1"}, monkeypatch)
