"""Audit ref_v0 tier counts at bidding vs settlement truth."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERO_REF_SRC = ROOT / "hero_ref" / "src"
sys.path.insert(0, str(HERO_REF_SRC))

import ahmad_ref_engine as eng  # noqa: E402
from ahmad_ref_engine import (  # noqa: E402
    QUALITY_KEYS,
    _enumerate_prior_count_combos,
    extract_evidence,
    load_reference_static_data,
    run_reference_engine,
)

TIER_LABEL = {"q1": "白绿", "q3": "蓝", "q4": "紫", "q5": "金", "q6": "红"}


def parse_truth(snap: dict) -> dict:
    parts: dict[str, int] = {}
    for part in (snap.get("final_quality_counts") or "").split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            parts[key.strip()] = int(value)
    q1_merged = parts.get("q1", 0) + parts.get("q2", 0)
    return {
        "q1": q1_merged,
        "q3": parts.get("q3"),
        "q4": parts.get("q4"),
        "q5": parts.get("q5"),
        "q6": parts.get("q6"),
        "total": snap.get("inventory_count"),
    }


def weighted_p50(combos, key: str) -> int | None:
    if not combos:
        return None
    max_logw = max(c.weight for c in combos)
    pairs = [
        (c.counts[key], math.exp(max(-745.0, c.weight - max_logw)))
        for c in combos
    ]
    total = sum(weight for _, weight in pairs)
    if total <= 0:
        return None
    target = total * 0.5
    acc = 0.0
    for value, weight in sorted(pairs, key=lambda item: item[0]):
        acc += weight
        if acc >= target:
            return int(value)
    return int(pairs[-1][0])


def _print_combo_block(combos, truth: dict, truth_counts: dict) -> None:
    if not combos:
        print("  (empty)")
        return
    max_logw = max(combo.weight for combo in combos)
    scored = [
        (math.exp(max(-745.0, combo.weight - max_logw)), combo)
        for combo in combos
    ]
    scored.sort(reverse=True, key=lambda item: item[0])
    print(f"  {len(combos)} combos; top:")
    top_weight = scored[0][0] if scored else 1.0
    for weight, combo in scored[:8]:
        pct = 100.0 * weight / top_weight
        mark = " <--TRUTH" if combo.counts == truth_counts else ""
        print(
            f"    {pct:5.1f}%  q1={combo.counts['q1']:2d} q3={combo.counts['q3']:2d} "
            f"q4={combo.counts['q4']:2d} q5={combo.counts['q5']:2d} q6={combo.counts['q6']:2d}{mark}"
        )
    truth_combo = next((combo for _, combo in scored if combo.counts == truth_counts), None)
    if truth_combo:
        truth_weight = math.exp(max(-745.0, truth_combo.weight - max_logw))
        rank = sum(1 for weight, _ in scored if weight > truth_weight) + 1
        print(f"  truth combo rank: {rank}/{len(scored)}")
    else:
        print("  truth combo NOT in set")
    print("  marginal P50:", end=" ")
    parts = []
    for key in QUALITY_KEYS:
        p50 = weighted_p50(combos, key)
        truth_count = truth.get(key)
        delta = (p50 - truth_count) if (p50 is not None and truth_count is not None) else None
        if delta not in (None, 0):
            parts.append(f"{TIER_LABEL[key]}{delta:+d}")
    print(", ".join(parts) if parts else "all match at P50")


def analyze_session(label: str, bid_path: Path, settled_path: Path) -> None:
    bid = json.loads(bid_path.read_text(encoding="utf-8-sig"))
    settled = json.loads(settled_path.read_text(encoding="utf-8-sig"))
    truth = parse_truth(settled)
    result = run_reference_engine(bid, max_combos=50_000)
    payload = result.as_dict()
    evidence = result.evidence
    notes: list[str] = list(evidence.source_notes)
    static = load_reference_static_data()
    map_id = evidence.map_id or bid.get("map_id")
    probs, prob_note = eng._quality_probabilities(map_id, static)
    total_candidates, total_prior_center = eng._total_count_candidates(
        evidence,
        notes,
        round_no=bid.get("round"),
        probs=probs,
    )

    print("=" * 72)
    print(label)
    print(
        f"hero={bid.get('hero')} session={bid.get('session_id')} "
        f"round=R{bid.get('round')} phase=bidding"
    )
    print(
        f"truth total={truth['total']} settlement: "
        f"白绿={truth['q1']} 蓝={truth['q3']} 紫={truth['q4']} "
        f"金={truth['q5']} 红={truth['q6']}"
    )
    print()
    print("--- evidence locks (出价时) ---")
    print("fixed_counts:", dict(evidence.fixed_counts))
    print("min_counts:", dict(evidence.min_counts))
    print("split_counts:", dict(evidence.split_counts))
    print("total_count:", evidence.total_count)
    print("avg_cells:", dict(evidence.avg_cells))
    print("quality_cells:", dict(evidence.quality_cells))
    print("total_candidates:", total_candidates, "prior_center:", total_prior_center)
    print("prob_note:", prob_note)
    print("engine combo_count:", payload.get("combo_count"), "status:", payload.get("status"))
    print()
    ranges = payload.get("quality_count_ranges") or {}
    print("--- engine quality_count_ranges vs truth (P10/P50/P90) ---")
    for key in QUALITY_KEYS:
        tier_range = ranges.get(key, [None, None, None])
        truth_count = truth.get(key)
        p50 = tier_range[1] if len(tier_range) > 1 else None
        delta = (p50 - truth_count) if (p50 is not None and truth_count is not None) else None
        flag = f"  <-- P50偏差 {delta:+d}" if delta not in (None, 0) else ""
        print(f"  {TIER_LABEL[key]:2} {key}: {tier_range}  truth={truth_count}{flag}")
    print()

    evidence_for_enum = extract_evidence(bid)
    truth_counts = {key: truth[key] for key in QUALITY_KEYS}
    totals_to_try = [evidence_for_enum.total_count or truth["total"]]
    if total_candidates:
        totals_to_try = sorted(set(int(t) for t in total_candidates))
    for total in totals_to_try:
        print(f"--- prior combos @ total={total} ---")
        combos = _enumerate_prior_count_combos(
            int(total),
            evidence=evidence_for_enum,
            probs=probs,
            total_prior_center=int(total_prior_center or total),
            max_new=50_000,
            round_no=bid.get("round"),
        )
        _print_combo_block(combos, truth, truth_counts)
    print()

    p50s = {
        key: (ranges.get(key) or [None, None, None])[1]
        for key in QUALITY_KEYS
    }
    tier_sum = sum(value for value in p50s.values() if value is not None)
    print(f"P50 tier sum={tier_sum} vs total={truth['total']}")
    if truth["q6"] is not None and p50s.get("q6") is not None:
        print(f"红件 P50误差 = {p50s['q6'] - truth['q6']:+d}")
    print()


def main() -> None:
    analyze_session(
        "2402 Ahmed R2",
        Path(r"C:\Users\example\Desktop\HeroRefDiag-20260629-001208-2402_1630026030858415\round_snapshots\r02_bidding.json"),
        Path(r"C:\Users\example\Desktop\HeroRefDiag-20260629-001208-2402_1630026030858415\round_snapshots\r02_settled.json"),
    )
    analyze_session(
        "2507 Aisha R4",
        Path(r"C:\Users\example\Desktop\HeroRefDiag-20260629-001516-2507_1630026030934075\round_snapshots\r04_bidding.json"),
        Path(r"C:\Users\example\Desktop\HeroRefDiag-20260629-001516-2507_1630026030934075\round_snapshots\r04_settled.json"),
    )


if __name__ == "__main__":
    main()
