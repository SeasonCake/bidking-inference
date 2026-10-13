# -*- coding: utf-8 -*-
"""Audit public info 200040/200041 (ranked map type-quality reveal) engine usage.

Read-only: loads HeroRefDiag round_snapshots and replays ahmad_ref_engine with/without
public quality evidence (public_info_rows + ui_contract.constraints mirror).

Usage:
  PYTHONPATH=src;scripts;hero_ref/src;hero_ref/tools python scripts/audit_public_info_20004x.py [diag_dir ...]
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("src", "scripts", "hero_ref/src", "hero_ref/tools"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from ahmad_ref_engine import (  # noqa: E402
    _coarse_quality_reveal_floors,
    extract_evidence,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)

DEFAULT_DIAGS = [
    Path(r"C:/Users/example/Desktop/HeroRefDiag-20260628-175428-5602_1630026005433109"),
    Path(r"C:/Users/example/Desktop/HeroRefDiag-20260628-173921-5602_1630026004464767"),
]


def _quality_breakdown(pi_row: dict) -> dict[str, int]:
    return dict(
        Counter(
            "q" + str(i.get("quality"))
            for i in pi_row.get("revealed_items_detail") or []
        )
    )


def strip_public_quality_evidence(art: dict) -> dict:
    """Remove both engine ingest paths for public quality reveals."""
    a = deepcopy(art)
    a["public_info_rows"] = []
    uc = dict(a.get("ui_contract") or {})
    wh = dict(uc.get("warehouse") or {})
    wh.update(
        quality_counts={},
        quality_reveal_counts={},
        quality_reveal_sources={},
        items=[],
    )
    uc["warehouse"] = wh
    cons = dict(uc.get("constraints") or {})
    counts = dict(cons.get("counts") or {})
    counts["known_quality_counts"] = {}
    cons["counts"] = counts
    summ = dict(cons.get("summary") or {})
    for key in (
        "known_purple_item_count",
        "known_red_item_count",
        "known_gold_item_count",
        "known_grid_items",
    ):
        summ[key] = 0
    cons["summary"] = summ
    uc["constraints"] = cons
    a["ui_contract"] = uc
    a["minimap_grid_items"] = []
    return a


def analyze_diag(diag: Path) -> dict:
    out: dict = {"diag": diag.name, "rounds": []}
    snap_dir = diag / "round_snapshots"
    if not snap_dir.is_dir():
        out["error"] = f"missing {snap_dir}"
        return out

    for snap_path in sorted(snap_dir.glob("*.json")):
        art = json.loads(snap_path.read_text(encoding="utf-8"))
        pis = art.get("public_info_rows") or []
        if not pis:
            continue
        pi = pis[0]
        info_id = pi.get("info_id")
        snap = prepare_reference_engine_snapshot(art)
        notes: list[str] = []
        coarse, *_ = _coarse_quality_reveal_floors(snap, source_notes=notes)
        res = run_reference_engine(snap)
        out["rounds"].append(
            {
                "file": snap_path.name,
                "session": art.get("session_id"),
                "round": art.get("round"),
                "phase": art.get("phase"),
                "info_id": info_id,
                "value_field": pi.get("value"),
                "revealed_n": pi.get("revealed_items"),
                "quality_raw": _quality_breakdown(pi),
                "coarse_counts": coarse,
                "coarse_notes": notes,
                "balanced": res.balanced,
                "tic": (art.get("inference_input_constraints") or {})
                .get("total_item_count", {})
                .get("value"),
            }
        )

    r1 = snap_dir / "r01_reading.json"
    if r1.exists():
        art = json.loads(r1.read_text(encoding="utf-8"))
        art_no = strip_public_quality_evidence(art)
        b_with = run_reference_engine(prepare_reference_engine_snapshot(art)).balanced
        b_without = run_reference_engine(
            prepare_reference_engine_snapshot(art_no)
        ).balanced
        ev_with = extract_evidence(prepare_reference_engine_snapshot(art))
        ev_without = extract_evidence(prepare_reference_engine_snapshot(art_no))
        out["ab_r01_reading"] = {
            "with_public": b_with,
            "without_public": b_without,
            "delta": b_with - b_without,
            "min_counts_with": {k: v for k, v in (ev_with.min_counts or {}).items() if v},
            "min_counts_without": {
                k: v for k, v in (ev_without.min_counts or {}).items() if v
            },
        }

    for settled_name in ("r05_settled.json", "r04_settled.json"):
        settled = snap_dir / settled_name
        if settled.exists():
            art = json.loads(settled.read_text(encoding="utf-8"))
            out["settled"] = {
                "file": settled_name,
                "items": art.get("inventory_count"),
                "value": art.get("known_value_sum"),
                "final_qc": art.get("final_quality_counts"),
            }
            break
    return out


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    diags = [Path(p) for p in argv] if argv else DEFAULT_DIAGS
    for diag in diags:
        if diag.is_dir():
            print(json.dumps(analyze_diag(diag), ensure_ascii=False, indent=2))
        else:
            print(json.dumps({"diag": str(diag), "error": "not a directory"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
