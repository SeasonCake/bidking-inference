"""Red accuracy eval SPLIT by knowability (per user 2026-06-19).

Reframe: an under-estimate of red caused by a BIG invisible red/gold the player only got by luck
(drawn from deep unrevealed space) is NOT a real error -- players don't price hidden big items either,
and the cell estimate is meant to carry margin, not perfectly fit every settlement. So red bias/MAE
should only be judged on samples where little red/gold was hidden below the known cells. Where lots is
hidden (low reveal-completeness / large unaccounted room), the error is ACCEPTABLE and must not drive
tuning.

For every aisha 4round sample at R4: compute reveal_ratio = known_minimap_cells / settled_total_cells,
and split into KNOWABLE (reveal high -> warehouse mostly seen, little hidden) vs HIDDEN-HEAVY (reveal
low -> big hidden red/gold plausible). Report red count/cells bias+MAE per group x red bucket, plus the
settled red+gold cells per group (to show hidden-heavy == big-red games). Knowable group = the real
accuracy signal.

Usage: python scripts/eval_aisha_red_knowability.py [--limit N] [--reveal 0.80]
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "scripts", ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools", ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from audit_aisha_gap import _map_id_from_events, _settlement_breakdown  # noqa: E402
from bidking_lab.live.fatbeans import live_batches_from_fatbeans_events, parse_fatbeans_capture  # noqa: E402
import ahmad_ref_engine as eng  # noqa: E402
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402
from hero_ref_live_schedule import hero_max_combos_for_round  # noqa: E402
from regenerate_checkpoint_matrices import AISHA_SKILL_ROUNDS, _skill_ids, _snapshot_at_batch  # noqa: E402

FATBEANS_DIR = ROOT / "data/samples/fatbeans"


def _p50(t) -> int:
    return int(t[1]) if isinstance(t, (list, tuple)) and len(t) > 1 and t[1] is not None else 0


def _redbucket(c: int) -> str:
    return "low" if c <= 1 else "mid" if c <= 3 else "high"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--reveal", type=float, default=0.80, help="reveal_ratio split threshold")
    args = ap.parse_args()
    skill_round, skill_id, _ = AISHA_SKILL_ROUNDS[3]  # R4

    # 排除隐秘(26xx)超富图：入场高、爆率超高(高品~46%)、误差天生大且非典型,会虚抬 hidden-heavy 组
    # 与整体口径(用户 2026-06-19)。正常分析只用 villa/shipwreck。
    samples = [s for s in sorted(FATBEANS_DIR.glob("*aisha*4rounds*.json")) if "_aisha_26" not in s.name]
    if args.limit:
        samples = samples[: args.limit]
    total = len(samples)
    print(f"red knowability eval: {total} aisha 4round @ R{skill_round}  reveal_split={args.reveal}", flush=True)

    rows: list[dict] = []
    start = time.time()
    for k, sample in enumerate(samples, 1):
        try:
            events = parse_fatbeans_capture(sample)
            truth = _settlement_breakdown(events) or {}
            t_total = int(truth.get("total_cells") or 0)
            t_red_cnt = int((truth.get("counts") or {}).get(6, 0))
            t_red_cells = int((truth.get("cells") or {}).get(6, 0))
            t_gold_cells = int((truth.get("cells") or {}).get(5, 0))
            pre = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
            map_id = _map_id_from_events(events)
            snaps = [
                _snapshot_at_batch(events=events, pre_batches=pre, batch_no=i, map_id=map_id)
                for i in range(1, len(pre) + 1)
            ]
        except Exception:  # noqa: BLE001
            continue
        snap = next((s for s in snaps if skill_id in _skill_ids(s)), None)
        if snap is None or t_total <= 0:
            continue
        snap.setdefault("ui_contract", {}).setdefault("context", {})["round"] = skill_round
        prepared = prepare_reference_engine_snapshot(snap)
        bud = hero_max_combos_for_round("aisha", skill_round)
        os.environ.pop("BIDKING_DISABLE_AISHA_RED_ROOM", None)
        res = run_reference_engine(prepared, max_combos=bud).as_dict()
        os.environ["BIDKING_DISABLE_AISHA_RED_ROOM"] = "1"
        res_off = run_reference_engine(prepared, max_combos=bud).as_dict()
        os.environ.pop("BIDKING_DISABLE_AISHA_RED_ROOM", None)
        known = eng._known_minimap_cells(eng._minimap_items_from_snapshot(prepared))
        reveal = known / t_total if t_total else 1.0
        e_red_cnt = _p50(res.get("red_count_range"))
        e_red_cells = _p50((res.get("quality_cells_ranges") or {}).get("q6"))
        e_red_cells_off = _p50((res_off.get("quality_cells_ranges") or {}).get("q6"))
        e_red_cnt_off = _p50(res_off.get("red_count_range"))
        rows.append({
            "name": sample.name, "reveal": reveal, "redbucket": _redbucket(t_red_cnt),
            "t_red_cnt": t_red_cnt, "e_red_cnt": e_red_cnt,
            "t_red_cells": t_red_cells, "e_red_cells": e_red_cells,
            "hidden_redgold": (t_red_cells + t_gold_cells) * max(0.0, 1.0 - reveal),
            "e_red_cells_off": e_red_cells_off, "e_red_cnt_off": e_red_cnt_off,
        })
        if k % 20 == 0 or k == total:
            el = time.time() - start
            print(f"  [progress] {k}/{total} elapsed={el:.0f}s eta~{el / k * (total - k):.0f}s", flush=True)

    def report(label: str, rs: list[dict]) -> None:
        if not rs:
            print(f"\n{label}: (none)")
            return
        n = len(rs)
        cb = sum(r["e_red_cnt"] - r["t_red_cnt"] for r in rs) / n
        xb = sum(r["e_red_cells"] - r["t_red_cells"] for r in rs) / n
        xm = sum(abs(r["e_red_cells"] - r["t_red_cells"]) for r in rs) / n
        avg_rev = sum(r["reveal"] for r in rs) / n
        avg_truthred = sum(r["t_red_cells"] for r in rs) / n
        avg_hidden = sum(r["hidden_redgold"] for r in rs) / n
        cb_off = sum(r["e_red_cnt_off"] - r["t_red_cnt"] for r in rs) / n
        xb_off = sum(r["e_red_cells_off"] - r["t_red_cells"] for r in rs) / n
        xm_off = sum(abs(r["e_red_cells_off"] - r["t_red_cells"]) for r in rs) / n
        print(f"\n{label}  n={n}  avgReveal={avg_rev:.2f}  truthRedCells~{avg_truthred:.1f}  hiddenR+G~{avg_hidden:.1f}")
        print(f"   重加权ON : redCntBias={cb:+.2f}  redCellBias={xb:+.1f}  redCellMAE={xm:.1f}")
        print(f"   重加权OFF: redCntBias={cb_off:+.2f}  redCellBias={xb_off:+.1f}  redCellMAE={xm_off:.1f}")

    knowable = [r for r in rows if r["reveal"] >= args.reveal]
    hidden = [r for r in rows if r["reveal"] < args.reveal]
    print("\n========== SPLIT BY REVEAL-COMPLETENESS ==========")
    report(f"KNOWABLE (reveal>={args.reveal}) -- the REAL accuracy signal", knowable)
    report(f"HIDDEN-HEAVY (reveal<{args.reveal}) -- error largely ACCEPTABLE", hidden)
    print("\n--- KNOWABLE split by red bucket (where tuning should focus) ---")
    for b in ("low", "mid", "high"):
        report(f"  knowable/{b}", [r for r in knowable if r["redbucket"] == b])
    print(f"\ntotal used={len(rows)}  knowable={len(knowable)}  hidden={len(hidden)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
