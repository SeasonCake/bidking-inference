"""A/B harness for the per-family phantom-red keep (aisha red Phase1 #1).

Runs the engine over the aisha 4-round library twice per sample -- baseline (flat
AISHA_PURE_PRIOR_RED_KEEP=0.30) vs a candidate `AISHA_PURE_PRIOR_RED_KEEP_BY_FAMILY`
override -- and reports, per truth-red bucket and per map family, the displayed 参考
(balanced) tier bias vs settlement truth, so a candidate's over-value reduction can be
checked WITHOUT regressing the under-estimating red-bearing buckets (knowability口径:
high-red under = acceptable hidden red, must NOT worsen).

Usage:
  python scripts/ab_aisha_phantom_red_keep.py --family-keep "24:0.05,25:0.30,45:0.30" [--limit N]
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
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402
from hero_ref_live_schedule import hero_max_combos_for_round  # noqa: E402
from regenerate_checkpoint_matrices import AISHA_SKILL_ROUNDS, _skill_ids, _snapshot_at_batch  # noqa: E402

FATBEANS_DIR = ROOT / "data/samples/fatbeans"
ENV_FAMILY = "AISHA_PURE_PRIOR_RED_KEEP_BY_FAMILY"


def _bucket(t: int) -> str:
    if t <= 0:
        return "0"
    if t <= 1:
        return "1"
    if t <= 3:
        return "2-3"
    return "4+"


def _balanced(snap, round_no, skill_round) -> tuple[int, str]:
    res = run_reference_engine(
        prepare_reference_engine_snapshot(snap),
        max_combos=hero_max_combos_for_round("aisha", skill_round),
    ).as_dict()
    notes = res.get("notes") or []
    keep_note = next((n for n in notes if "pure_prior_red_keep" in n), "")
    return int(res.get("balanced") or 0), keep_note


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--family-keep", required=True, help='e.g. "24:0.05,25:0.30,45:0.30"')
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--round", type=int, default=4)
    args = ap.parse_args()
    skill_idx = args.round - 1
    skill_round, skill_id, _ = AISHA_SKILL_ROUNDS[skill_idx]

    samples = sorted(FATBEANS_DIR.glob("*aisha*4rounds*.json"))
    if args.limit:
        samples = samples[: args.limit]
    total = len(samples)
    print(f"A/B phantom-red keep: {total} samples @ R{skill_round}  candidate={args.family_keep}", flush=True)

    rows: list[dict] = []
    skipped = 0
    start = time.time()
    for k, sample in enumerate(samples, 1):
        try:
            events = parse_fatbeans_capture(sample)
            truth = _settlement_breakdown(events) or {}
            t_cnt = int((truth.get("counts") or {}).get(6, 0))
            t_total = int(sum((truth.get("values") or {}).values()))
            pre = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
            map_id = _map_id_from_events(events)
            snaps = [
                _snapshot_at_batch(events=events, pre_batches=pre, batch_no=i, map_id=map_id)
                for i in range(1, len(pre) + 1)
            ]
        except Exception:  # noqa: BLE001
            skipped += 1
            continue
        snap = next((s for s in snaps if skill_id in _skill_ids(s)), None)
        if snap is None:
            skipped += 1
            continue
        snap.setdefault("ui_contract", {}).setdefault("context", {})["round"] = skill_round
        # baseline: flat keep (no family override)
        os.environ.pop(ENV_FAMILY, None)
        base_bal, base_note = _balanced(snap, skill_round, skill_round)
        # candidate: per-family override
        os.environ[ENV_FAMILY] = args.family_keep
        cand_bal, cand_note = _balanced(snap, skill_round, skill_round)
        os.environ.pop(ENV_FAMILY, None)
        fam = str(int(map_id) // 100) if map_id is not None else "?"
        rows.append({
            "fam": fam, "t_cnt": t_cnt, "t_total": t_total,
            "base": base_bal, "cand": cand_bal,
            "changed": base_bal != cand_bal,
        })
        if k % 20 == 0 or k == total:
            el = time.time() - start
            print(f"  [progress] {k}/{total} elapsed={el:.0f}s eta~{el / k * (total - k):.0f}s", flush=True)

    def stat(rs):
        n = len(rs)
        if not n:
            return None
        bb = sum(r["base"] - r["t_total"] for r in rs) / n / 1000.0
        cb = sum(r["cand"] - r["t_total"] for r in rs) / n / 1000.0
        bmae = sum(abs(r["base"] - r["t_total"]) for r in rs) / n / 1000.0
        cmae = sum(abs(r["cand"] - r["t_total"]) for r in rs) / n / 1000.0
        bover = sum(1 for r in rs if r["t_total"] > 0 and r["base"] > r["t_total"] * 1.10) / n
        cover = sum(1 for r in rs if r["t_total"] > 0 and r["cand"] > r["t_total"] * 1.10) / n
        return n, bb, cb, bmae, cmae, bover, cover

    print("\n=== by truth-red bucket: baseline -> candidate (displayed 参考 total, k) ===")
    print(f"{'bucket':6s} {'n':>3s} | {'baseBias':>8s}->{'candBias':>8s} | {'baseMAE':>7s}->{'candMAE':>7s} | over% {'base':>4s}->{'cand':>4s}")
    grp: dict[str, list] = defaultdict(list)
    for r in rows:
        grp[_bucket(r["t_cnt"])].append(r)
    for b in ("0", "1", "2-3", "4+"):
        s = stat(grp.get(b) or [])
        if not s:
            continue
        n, bb, cb, bmae, cmae, bover, cover = s
        print(f"{b:6s} {n:>3d} | {bb:>+8.0f}->{cb:>+8.0f} | {bmae:>7.0f}->{cmae:>7.0f} | {bover:>5.0%}->{cover:>4.0%}")

    print("\n=== by family ===")
    fgrp: dict[str, list] = defaultdict(list)
    for r in rows:
        fgrp[r["fam"]].append(r)
    for fam in sorted(fgrp):
        s = stat(fgrp[fam])
        if not s:
            continue
        n, bb, cb, bmae, cmae, bover, cover = s
        nchg = sum(1 for r in fgrp[fam] if r["changed"])
        print(f"fam {fam}: n={n:>3d} changed={nchg:>3d} | bias {bb:>+7.0f}->{cb:>+7.0f} | MAE {bmae:>6.0f}->{cmae:>6.0f} | over {bover:>4.0%}->{cover:>4.0%}")

    print(f"\nskipped={skipped} used={len(rows)} changed={sum(1 for r in rows if r['changed'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
