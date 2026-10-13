"""⚠ 归档实验脚本(早期阶段)。校准请优先用 scripts/calibration/ 工具包(核心 aisha_calib.py)。
后续窗口注意旧口径:
  - **本脚本 glob `*aisha*4rounds*` 会匹配 mixed 坏样本**(现 mixed 已隔离故暂只命中 valid，
    但若 mixed 回流即被污染)→ 改用 aisha_calib.valid_aisha_samples()。
  - 量红 count base-rate(非价值)，长尾规范化不涉及；若改评估 VALUE 用 aisha_calib.normalized_truth。

TASK B: per-family truth-red base rate among pure-prior-red aisha samples + the
keep that is actually justified.

For every aisha 4-round sample (R4 decision round) with settlement truth, capture:
  - is_pure_prior: _is_pure_prior_red(evidence) at the F1 call site
  - rv50: the red-value contribution to p50 that F1 damps (keep applies to rv50)
  - non_red: p50 - rv50 (the part keep does NOT touch)
  - truth red count (settlement)
  - family = map_id // 100

Then, per family (24 villa, 25 sealed-cabin, 45 deep-sea; EXCLUDE 26 hidden):
  - n_pure_prior, frac_truth_red0 (base rate of genuinely-empty red among pure-prior)
  - mean rv50 on the truth-red=0 subset (the phantom value a low keep removes)
  - mean rv50 on the truth-red>0 subset (real red that a low keep would wrongly cut)
  - justified_keep: the keep that neutralizes bucket-0 displayed over WITHOUT pushing
    the truth-red>0 pure-prior subset further under. Computed by sweeping keep over a
    grid and scoring displayed-tier bias on truth-red0 vs truth-red>0 subsets.
  - residual_that_keep_cannot_fix: bucket-0 over-value that remains at justified_keep
    (i.e. over-value NOT flowing through the F1 rv50 path).

We capture rv50/non_red by wrapping _apply_aisha_d1_bid_adjustment (records args), and
we recompute the displayed balanced tier at several keeps via the family-keep env knob
in ONE pass by replaying the captured (non_red, rv50, truth) per sample analytically:
  damped_p50(keep) = non_red + rv50 * keep
This is exact for the F1 contribution; the displayed balanced tier is a downstream
transform of p50, so we ALSO run the engine at a few discrete keeps per family to get
the true displayed bias (analytic p50 shift is the lever; engine run is the truth).

Usage: python scripts/task_b_pure_prior_base_rate.py [--limit N]
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
import ahmad_ref_engine as E  # noqa: E402
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402
from hero_ref_live_schedule import hero_max_combos_for_round  # noqa: E402
from regenerate_checkpoint_matrices import AISHA_SKILL_ROUNDS, _skill_ids, _snapshot_at_batch  # noqa: E402

FATBEANS_DIR = ROOT / "data/samples/fatbeans"

# Capture box: filled by the wrapper on each F1 call. We keep the LAST aisha call's
# values for the run (there is one aisha hero per snapshot).
_CAP: dict = {}
_orig_f1 = E._apply_aisha_d1_bid_adjustment


def _wrapped_f1(notes, *, snapshot, hero_key, quality_count_ranges, total_grid_range,
                evidence, p50, rv50):
    if hero_key == "aisha":
        try:
            pp = E._is_pure_prior_red(evidence)
        except Exception:  # noqa: BLE001
            pp = None
        _CAP["pure_prior"] = pp
        _CAP["rv50"] = float(rv50) if rv50 is not None else None
        _CAP["p50"] = float(p50) if p50 is not None else None
        _CAP["min_red"] = int(evidence.min_counts.get("q6", 0) or 0)
    return _orig_f1(
        notes, snapshot=snapshot, hero_key=hero_key,
        quality_count_ranges=quality_count_ranges, total_grid_range=total_grid_range,
        evidence=evidence, p50=p50, rv50=rv50,
    )


E._apply_aisha_d1_bid_adjustment = _wrapped_f1


def _family(map_id) -> str:
    try:
        return str(int(map_id) // 100)
    except Exception:  # noqa: BLE001
        return "?"


def _run_one(snap, skill_round, keep_env: str | None):
    """Run engine once; return (balanced, captured dict copy)."""
    if keep_env is None:
        os.environ.pop("AISHA_PURE_PRIOR_RED_KEEP_BY_FAMILY", None)
        os.environ.pop("AISHA_PURE_PRIOR_RED_KEEP", None)
    else:
        os.environ["AISHA_PURE_PRIOR_RED_KEEP"] = keep_env
        os.environ.pop("AISHA_PURE_PRIOR_RED_KEEP_BY_FAMILY", None)
    _CAP.clear()
    res = run_reference_engine(
        prepare_reference_engine_snapshot(snap),
        max_combos=hero_max_combos_for_round("aisha", skill_round),
    ).as_dict()
    return int(res.get("balanced") or 0), dict(_CAP)


# keeps to probe for the engine-true displayed bias (baseline 0.30 is current default)
KEEP_GRID = ["1.0", "0.30", "0.15", "0.05", "0.0"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--round", type=int, default=4)
    args = ap.parse_args()
    skill_idx = args.round - 1
    skill_round, skill_id, _ = AISHA_SKILL_ROUNDS[skill_idx]

    samples = sorted(FATBEANS_DIR.glob("*aisha*4rounds*.json"))
    if args.limit:
        samples = samples[: args.limit]
    total = len(samples)
    print(f"TASK B base-rate: {total} samples @ R{skill_round} keeps={KEEP_GRID}", flush=True)

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
        fam = _family(map_id)
        # run at each keep, record displayed balanced + capture (capture identical across keeps
        # except for whether F1 fired; rv50/pure_prior are keep-independent)
        bal_by_keep: dict[str, int] = {}
        cap0 = None
        for keep in KEEP_GRID:
            bal, cap = _run_one(snap, skill_round, keep)
            bal_by_keep[keep] = bal
            if cap0 is None:
                cap0 = cap
        rows.append({
            "name": sample.name, "fam": fam,
            "t_cnt": t_cnt, "t_total": t_total,
            "pure_prior": (cap0 or {}).get("pure_prior"),
            "rv50": (cap0 or {}).get("rv50"),
            "p50": (cap0 or {}).get("p50"),
            "min_red": (cap0 or {}).get("min_red"),
            "bal": bal_by_keep,
        })
        if k % 10 == 0 or k == total:
            el = time.time() - start
            print(f"  [progress] {k}/{total} used={len(rows)} elapsed={el:.0f}s eta~{el / k * (total - k):.0f}s", flush=True)

    os.environ.pop("AISHA_PURE_PRIOR_RED_KEEP", None)
    os.environ.pop("AISHA_PURE_PRIOR_RED_KEEP_BY_FAMILY", None)

    # ---------- report ----------
    pp_rows = [r for r in rows if r["pure_prior"] is True]
    print(f"\n=== overall: used={len(rows)} pure_prior={len(pp_rows)} ===")

    fams = ("24", "25", "45")  # exclude 26 (hidden) and any other
    print("\n=== per-family pure-prior base rate (EXCLUDING 26 hidden) ===")
    hdr = f"{'fam':>4s} {'n_pp':>5s} {'tr0':>4s} {'tr>0':>5s} {'frac_tr0':>8s} | {'rv50@tr0_k':>11s} {'rv50@tr>0_k':>12s}"
    print(hdr)
    fam_summ: dict[str, dict] = {}
    for fam in fams:
        fr = [r for r in pp_rows if r["fam"] == fam]
        n = len(fr)
        if n == 0:
            print(f"{fam:>4s} {'0':>5s}  (no pure-prior samples)")
            fam_summ[fam] = {"n": 0}
            continue
        tr0 = [r for r in fr if r["t_cnt"] == 0]
        trp = [r for r in fr if r["t_cnt"] > 0]
        frac0 = len(tr0) / n
        rv0 = (sum((r["rv50"] or 0) for r in tr0) / len(tr0) / 1000.0) if tr0 else 0.0
        rvp = (sum((r["rv50"] or 0) for r in trp) / len(trp) / 1000.0) if trp else 0.0
        print(f"{fam:>4s} {n:>5d} {len(tr0):>4d} {len(trp):>5d} {frac0:>8.0%} | {rv0:>11.0f} {rvp:>12.0f}")
        fam_summ[fam] = {"n": n, "tr0": tr0, "trp": trp, "frac0": frac0, "rv0_k": rv0, "rvp_k": rvp}

    # ---------- justified keep sweep (engine-true displayed bias) ----------
    # For each family, score each keep on the pure-prior subset:
    #   over0  = mean displayed over-value on truth-red0 pure-prior (want -> ~0)
    #   underP = mean displayed bias on truth-red>0 pure-prior (more negative = worse under)
    # justified_keep = the SMALLEST keep whose truth-red>0 bias is still >= a tolerance of
    #   the keep=1.0 (baseline-most-generous) truth-red>0 bias minus an allowed slack,
    #   i.e. it stops where cutting red starts hurting real-red games materially.
    print("\n=== keep sweep on pure-prior subset (engine displayed 参考 bias vs truth, k) ===")
    print("    per family: bias on truth-red0 (over) and truth-red>0 (under) at each keep")
    for fam in fams:
        s = fam_summ.get(fam) or {}
        if not s.get("n"):
            continue
        tr0, trp = s["tr0"], s["trp"]
        print(f"\n  fam {fam}: n_pp={s['n']} tr0={len(tr0)} tr>0={len(trp)}")
        print(f"    {'keep':>5s} | {'tr0_bias_k':>10s} {'tr0_over%':>9s} | {'tr>0_bias_k':>11s} {'tr>0_under%':>11s}")
        per_keep = {}
        for keep in KEEP_GRID:
            b0 = (sum(r["bal"][keep] - r["t_total"] for r in tr0) / len(tr0) / 1000.0) if tr0 else 0.0
            o0 = (sum(1 for r in tr0 if r["t_total"] > 0 and r["bal"][keep] > r["t_total"] * 1.10) / len(tr0)) if tr0 else 0.0
            bp = (sum(r["bal"][keep] - r["t_total"] for r in trp) / len(trp) / 1000.0) if trp else 0.0
            up = (sum(1 for r in trp if r["t_total"] > 0 and r["bal"][keep] < r["t_total"] * 0.90) / len(trp)) if trp else 0.0
            per_keep[keep] = {"b0": b0, "o0": o0, "bp": bp, "up": up}
            print(f"    {keep:>5s} | {b0:>+10.0f} {o0:>9.0%} | {bp:>+11.0f} {up:>11.0%}")
        fam_summ[fam]["per_keep"] = per_keep

    # ---------- choose justified keep + residual ----------
    print("\n=== JUSTIFIED KEEP + residual (the deliverable) ===")
    print("rule: pick smallest keep where truth-red>0 pure-prior bias does NOT drop below")
    print("      (keep=1.0 tr>0 bias) - SLACK; SLACK=80k. residual = tr0 bias at that keep.")
    SLACK_K = 80.0
    deliverable = []
    for fam in fams:
        s = fam_summ.get(fam) or {}
        if not s.get("n"):
            deliverable.append({"family": fam, "n_pure_prior": 0, "frac_truth_red0": None,
                                "justified_keep": None, "residual_that_keep_cannot_fix": None})
            continue
        pk = s["per_keep"]
        trp = s["trp"]
        base_bp = pk["1.0"]["bp"] if trp else 0.0
        floor_bp = base_bp - SLACK_K
        # iterate keeps high->low; the grid is descending already
        chosen = "1.0"
        for keep in KEEP_GRID:
            bp = pk[keep]["bp"]
            if not trp or bp >= floor_bp:
                chosen = keep
            else:
                break
        residual = pk[chosen]["b0"]  # tr0 bias remaining at chosen keep (over-value F1 can't fix)
        # how many real-red games hurt + by how much, going from keep=1.0 to chosen
        hurt_delta = (pk[chosen]["bp"] - base_bp) if trp else 0.0  # negative = extra under on real red
        deliverable.append({
            "family": fam,
            "n_pure_prior": s["n"],
            "frac_truth_red0": round(s["frac0"], 3),
            "justified_keep": float(chosen),
            "residual_that_keep_cannot_fix_k": round(residual, 0),
            "real_red_extra_under_k": round(hurt_delta, 0),
            "n_real_red_pure_prior": len(trp),
        })
    print()
    for d in deliverable:
        print(d)

    # explicit trade quantification
    print("\n=== trade: how many real-red games a low villa keep hurts ===")
    for fam in fams:
        s = fam_summ.get(fam) or {}
        if not s.get("n"):
            continue
        trp = s["trp"]
        if not trp:
            print(f"  fam {fam}: 0 real-red pure-prior games -> a low keep hurts NOBODY in this family")
            continue
        pk = s["per_keep"]
        print(f"  fam {fam}: {len(trp)} real-red pure-prior games; tr>0 bias 1.0->{KEEP_GRID[-1]}: "
              f"{pk['1.0']['bp']:+.0f}k -> {pk[KEEP_GRID[-1]]['bp']:+.0f}k")

    print(f"\nskipped={skipped} used={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
