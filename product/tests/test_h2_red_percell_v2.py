"""H2 gate: ethan red-residual $/cell v2 — ARCHIVED default OFF, re-evaluate with H4a.

v2 = median of per-vault red $/cell (long-tail-ruled truth; 56xx 51102 n=559 / ship 47869 n=584 /
villa 53218 n=239 / hidden 48348 n=84); the shipped pooled table under-prices typical red cells
3-14% (giant cheap vaults drag the pooled ratio). Dual-pool sweep verdict (scope clean on both):
the LIFT-target channel alone is marginal — real 3+红 med flat, synth 3+红 −27.3→−25.9 — while both
author guards were touched (real 1-2红 +34.5→+36.1; synth over10 27→29). Archived per the H5/H7p2
precedent; re-sweep TOGETHER with H4a (族先验兜底) when the LIFT fires on the coverage gap.
Opt-in: ETHAN_ENABLE_RED_PERCELL_V2=1. These tests pin: default byte-identical to the pooled table,
opt-in prices the LIFT residual at the v2 family value, factor-cap rows invariant either way.

Local-only replay gates use the gitignored 500-sample synth pool (skip when absent).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
for p in (AHMAD_SRC, ROOT / "hero_ref" / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from ahmad_ref_engine import (  # noqa: E402
    ETHAN_RESID_RED_PERCELL_BY_FAMILY,
    ETHAN_RESID_RED_PERCELL_BY_FAMILY_V2,
    _ethan_resid_red_percell,
    run_reference_engine,
)

OPT_IN = "ETHAN_ENABLE_RED_PERCELL_V2"
POOL = ROOT / "data" / "samples" / "synthetic_draw" / "ethan"


def test_helper_defaults_to_pooled_table_opt_in_v2(monkeypatch) -> None:
    monkeypatch.delenv(OPT_IN, raising=False)
    assert _ethan_resid_red_percell("map_56xx") == 46864.0
    assert _ethan_resid_red_percell("map_21xx") == 46000.0  # unknown family -> v1 global
    monkeypatch.setenv(OPT_IN, "1")
    assert _ethan_resid_red_percell("map_56xx") == 51102.0
    assert _ethan_resid_red_percell("villa") == 53218.0
    assert _ethan_resid_red_percell("map_21xx") == 50000.0
    assert set(ETHAN_RESID_RED_PERCELL_BY_FAMILY) == set(ETHAN_RESID_RED_PERCELL_BY_FAMILY_V2)


def _run_pool_sample(name: str, monkeypatch, opt_in: bool):
    from hero_ref_live_schedule import hero_max_combos_for_round

    rec = json.loads((POOL / name).read_text(encoding="utf-8"))
    if opt_in:
        monkeypatch.setenv(OPT_IN, "1")
    else:
        monkeypatch.delenv(OPT_IN, raising=False)
    rnd = int(rec.get("round") or 5)
    mc = min(15000, hero_max_combos_for_round("ethan", min(rnd, 5)))
    res = run_reference_engine(rec["snapshot"], max_combos=mc).as_dict()
    note = next(
        (str(n) for n in res.get("notes") or [] if str(n).startswith("ethan_red_resid_lift")),
        None,
    )
    return res, note


def _note_parts(note: str) -> tuple[float, float]:
    m = re.match(r"ethan_red_resid_lift:resid([\d.]+)c:target(\d+):", note)
    assert m, note
    return float(m.group(1)), float(m.group(2))


@pytest.mark.parametrize(
    "name,family,v1,v2",
    [
        ("synth_ethan_0000.json", "map_56xx", 46864.0, 51102.0),
        ("synth_ethan_0007.json", "villa", 46627.0, 53218.0),
    ],
)
def test_lift_target_prices_residual_at_family_table(name, family, v1, v2, monkeypatch) -> None:
    if not (POOL / name).exists():
        pytest.skip("synth pool absent (gitignored) — local-only gate")
    off_res, off_note = _run_pool_sample(name, monkeypatch, opt_in=False)
    on_res, on_note = _run_pool_sample(name, monkeypatch, opt_in=True)
    assert off_note and on_note
    resid_off, target_off = _note_parts(off_note)
    resid_on, target_on = _note_parts(on_note)
    assert resid_on == resid_off  # residual cells untouched; only the $/cell moves
    assert target_off / resid_off == pytest.approx(v1, rel=0.01)
    assert target_on / resid_on == pytest.approx(v2, rel=0.01)
    assert on_res["balanced"] >= off_res["balanced"]  # LIFT is lift-only


def test_factor_cap_row_is_invariant(monkeypatch) -> None:
    # 0030: factor already capped at 2.0 under v1 — v2 must not push past the cap (bal identical).
    name = "synth_ethan_0030.json"
    if not (POOL / name).exists():
        pytest.skip("synth pool absent (gitignored) — local-only gate")
    off_res, off_note = _run_pool_sample(name, monkeypatch, opt_in=False)
    on_res, on_note = _run_pool_sample(name, monkeypatch, opt_in=True)
    assert off_note and off_note.endswith("f2.000") and on_note.endswith("f2.000")
    assert on_res["balanced"] == off_res["balanced"]
