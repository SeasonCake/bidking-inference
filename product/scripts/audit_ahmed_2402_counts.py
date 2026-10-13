"""Ahmed 2402: verify total_count=51 aligns with tier counts."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hero_ref" / "src"))

import ahmad_ref_engine as eng  # noqa: E402
from ahmad_ref_engine import (  # noqa: E402
    QUALITY_KEYS,
    _count_sum_matches,
    _effective_min_count,
    _enumerate_prior_count_combos,
    _grids_for_counts,
    _prior_count_values,
    extract_evidence,
    load_reference_static_data,
    run_reference_engine,
)

BID = Path(
    r"C:\Users\example\Desktop\HeroRefDiag-20260629-001208-2402_1630026030858415"
    r"\round_snapshots\r02_bidding.json"
)
SETTLED = Path(
    r"C:\Users\example\Desktop\HeroRefDiag-20260629-001208-2402_1630026030858415"
    r"\round_snapshots\r02_settled.json"
)


def parse_truth(snap: dict) -> dict[str, int]:
    parts: dict[str, int] = {}
    for part in (snap.get("final_quality_counts") or "").split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            parts[key.strip()] = int(value)
    return {
        "q1": parts["q1"] + parts["q2"],
        "q3": parts["q3"],
        "q4": parts["q4"],
        "q5": parts["q5"],
        "q6": parts["q6"],
    }


def main() -> None:
    bid = json.loads(BID.read_text(encoding="utf-8-sig"))
    settled = json.loads(SETTLED.read_text(encoding="utf-8-sig"))
    truth = parse_truth(settled)
    total_truth = int(settled["inventory_count"])

    print("=" * 60)
    print("Ahmed 2402 R2 bidding — total count alignment")
    print("=" * 60)
    print(f"settlement: total={total_truth} tiers={truth} sum={sum(truth.values())}")
    print()

    ev = extract_evidence(bid)
    print("--- Ahmed 总件数 skill → engine ---")
    print(f"evidence.total_count = {ev.total_count}")
    print(f"inference_input_constraints: {bid.get('inference_input_constraints')}")
    for row in bid.get("skill_reveal_rows") or []:
        if row.get("skill_id") == 100204:
            print(f"skill 100204 (R1总件数): result={row.get('result')}")
    print(f"structured_ref total_count: {(bid.get('structured_ref_inputs') or {}).get('total_count')}")
    print()

    notes: list[str] = list(ev.source_notes)
    static = load_reference_static_data()
    probs, prob_note = eng._quality_probabilities(ev.map_id, static)
    total_candidates, prior_center = eng._total_count_candidates(
        ev, notes, round_no=bid.get("round"), probs=probs
    )
    print(f"total_candidates={total_candidates} prior_center={prior_center}")
    print(f"count notes: {[n for n in notes if 'total' in n or 'count' in n][:15]}")
    print()

    print("--- locks at bid ---")
    print("fixed:", dict(ev.fixed_counts))
    print("min:", dict(ev.min_counts))
    print("avg_cells:", dict(ev.avg_cells))
    print()

    result = run_reference_engine(bid).as_dict()
    qcr = result.get("quality_count_ranges") or {}
    print("--- engine P50 vs truth ---")
    for key in QUALITY_KEYS:
        tier = qcr.get(key, [None, None, None])
        p50 = tier[1]
        t = truth[key]
        d = (p50 - t) if p50 is not None else None
        flag = f"  delta={d:+d}" if d not in (None, 0) else "  OK"
        print(f"  {key}: P50={p50} truth={t}{flag}")
    p50_sum = sum((qcr.get(k) or [0, 0, 0])[1] for k in QUALITY_KEYS)
    print(f"P50 tier sum={p50_sum} vs total_count={ev.total_count}")
    print(f"combo_count={result.get('combo_count')}")
    print()

    print("--- why truth combo q6=5 fails ---")
    counts = dict(truth)
    for key in QUALITY_KEYS:
        eff_min = _effective_min_count(key, ev)
        fixed = ev.fixed_counts.get(key)
        ok = counts[key] >= eff_min and (fixed is None or counts[key] == fixed)
        print(f"  {key}: truth={counts[key]} eff_min={eff_min} fixed={fixed} pass={ok}")
    print(f"  _count_sum_matches: {_count_sum_matches(counts, ev)}")
    grids = _grids_for_counts(counts, ev)
    print(f"  _grids_for_counts: {grids}")
    if grids is None:
        print("  => REJECTED at grid composition")
    print()

    print("--- q6 sweep @ total=51 (q3=19 fixed, q4/q5=truth) ---")
    for q6 in range(0, 8):
        q1 = 51 - 19 - truth["q4"] - truth["q5"] - q6
        c = {"q1": q1, "q3": 19, "q4": truth["q4"], "q5": truth["q5"], "q6": q6}
        g = _grids_for_counts(c, ev)
        mins_ok = all(c[k] >= _effective_min_count(k, ev) for k in QUALITY_KEYS)
        mark = " <--TRUTH" if q6 == truth["q6"] else ""
        print(
            f"  q6={q6} q1={q1:2d} mins_ok={mins_ok} "
            f"grids_ok={g is not None}{mark}"
        )
    print()

    print("--- prior domain per tier @ total=51 ---")
    for key in QUALITY_KEYS:
        vals = _prior_count_values(51, key, ev, probs)
        print(f"  {key}: allowed={vals} truth_in={truth[key] in vals}")
    print()

    combos = _enumerate_prior_count_combos(
        51, evidence=ev, probs=probs, total_prior_center=51, max_new=50_000, round_no=2
    )
    print(f"--- enumerated combos ({len(combos)}) ---")
    for combo in combos:
        mark = " TRUTH" if combo.counts == truth else ""
        print(f"  {combo.counts}{mark}")


if __name__ == "__main__":
    main()
