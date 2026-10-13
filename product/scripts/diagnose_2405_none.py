"""One-off diagnostic: why does 2405_0068 return None grid/count at R3/R4?

The settled warehouse exists (grid=100, total=43, gold 6/21), so the TRUTH (counts,
grid) is a feasible point. If the engine returns no_reachable_combo, some constraint
wrongly excludes the truth. This probe feeds the TRUTH counts into _grids_for_counts
for the R3 snapshot and walks each rejection gate to pinpoint the over-tight one.

Retire after the 2405 None investigation closes.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "scripts", ROOT / "hero_ref" / "src", ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from audit_aisha_gap import _map_id_from_events  # noqa: E402
from bidking_lab.live.fatbeans import live_batches_from_fatbeans_events, parse_fatbeans_capture  # noqa: E402
import ahmad_ref_engine as eng  # noqa: E402
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402
from regenerate_checkpoint_matrices import AISHA_SKILL_ROUNDS, _skill_ids, _snapshot_at_batch  # noqa: E402

FATBEANS = ROOT / "data/samples/fatbeans/fatbeans_valid_aisha_2405_4rounds_2405_1295018570232927_0068.json"
QKEYS = eng.QUALITY_KEYS
# settlement truth per count_eyeball report (q1=white+green, q3=blue, q4=purple, q5=gold, q6=red)
TRUTH = {"q1": 12, "q3": 13, "q4": 11, "q5": 6, "q6": 1}
TRUTH_GRID = 100


def _probe(snap, skill_round: int, skill_label: str) -> None:
    snap.setdefault("ui_contract", {}).setdefault("context", {})["round"] = skill_round
    prepared = prepare_reference_engine_snapshot(snap)
    ev = eng.extract_evidence(prepared)
    print(f"\n===== R{skill_round}{skill_label} evidence =====")
    print(f"  hero={ev.hero} map={ev.map_id}")
    print(f"  fixed_counts={dict(ev.fixed_counts)}  min_counts={dict(ev.min_counts)}")
    print(f"  count_sums={dict(ev.count_sums)}")
    print(f"  quality_cells(exact)={dict(ev.quality_cells)}")
    print(f"  avg_cells={dict(ev.avg_cells)}  soft_avg_cell_keys={set(ev.soft_avg_cell_keys)}")
    print(f"  total_grid_target={ev.total_grid_target}  hard_total={eng._hard_total_grid_target_int(ev)}")
    print("  per-quality: exact_cells / cell_floor:")
    for k in QKEYS:
        print(
            f"    {k}: exact={eng._quality_exact_cells(k, ev)} "
            f"floor={eng._quality_cell_floor(k, ev)} avg={ev.avg_cells.get(k)} "
            f"truth_count={TRUTH[k]}"
        )
    # Does the TRUTH survive per-quality count gates that the enumerator applies?
    print("  truth count-gate checks:")
    for k in QKEYS:
        emin = eng._effective_min_count(k, ev)
        val_ok = eng._quality_count_matches_value_evidence(k, TRUTH[k], ev)
        print(f"    {k}: effective_min={emin} (truth {TRUTH[k]} {'OK' if TRUTH[k] >= emin else 'BELOW-MIN'})  value_match={val_ok}")
    print(f"  count_sum_matches(truth)={eng._count_sum_matches(TRUTH, ev)}")
    grids = eng._grids_for_counts(dict(TRUTH), ev)
    print(f"  >>> _grids_for_counts(TRUTH) = {grids}")
    if grids is not None:
        print(f"      Σgrid={sum(grids.values())} (truth grid {TRUTH_GRID})")
    # total-candidate window vs fixed-implied total
    sd = eng.load_reference_static_data()
    probs, _ = eng._quality_probabilities(ev.map_id, sd)
    fixed_sum = sum(max(0, int(v)) for v in ev.fixed_counts.values())
    cands, center = eng._total_count_candidates(ev, [], round_no=skill_round)
    print(f"  fixed_sum(q1+q3+q4+q5+...)={fixed_sum}  known_count_floor={eng._known_count_floor(ev)}")
    print(f"  total_candidates={cands[:3]}..{cands[-3:] if len(cands) > 3 else ''}  center={center}")
    print(f"  truth_total={sum(TRUTH.values())} in window? {sum(TRUTH.values()) in cands}")
    # for each total in window, what q6 would the fixed counts force, and is it a candidate?
    for total in cands:
        q6_forced = total - fixed_sum
        q6_cands = eng._prior_count_values(total, "q6", ev, probs)
        print(f"    total={total}: q6_forced={q6_forced}  q6_cands={list(q6_cands)}  reachable={q6_forced in q6_cands}")


def main() -> int:
    events = parse_fatbeans_capture(FATBEANS)
    pre_batches = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
    map_id = _map_id_from_events(events)
    prefix_snaps = [
        _snapshot_at_batch(events=events, pre_batches=pre_batches, batch_no=i, map_id=map_id)
        for i in range(1, len(pre_batches) + 1)
    ]
    for skill_round, skill_id, skill_label in AISHA_SKILL_ROUNDS:
        snap = next((s for s in prefix_snaps if skill_id in _skill_ids(s)), None)
        if snap is None:
            print(f"\n===== R{skill_round}{skill_label}: snapshot NOT FOUND")
            continue
        _probe(snap, skill_round, skill_label)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
