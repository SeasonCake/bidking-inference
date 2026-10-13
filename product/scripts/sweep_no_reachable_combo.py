"""Reproduction sweep: how often does Aisha return no_reachable_combo at the
decision rounds, and is it the soft-count-hint-overrides-fixed-counts conflict?

For every aisha 4-round fatbeans sample, build each skill-round snapshot, run the
engine at the LIVE per-round max_combos budget, and tally status. For each
no_reachable_combo, also report fixed_sum vs the soft count center, so we can
confirm the conflict signature (center pushed above fixed-implied total).

Retire after the no_reachable_combo fix lands.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "scripts", ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools", ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from audit_aisha_gap import _map_id_from_events  # noqa: E402
from bidking_lab.live.fatbeans import live_batches_from_fatbeans_events, parse_fatbeans_capture  # noqa: E402
import ahmad_ref_engine as eng  # noqa: E402
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402
from hero_ref_live_schedule import hero_max_combos_for_round  # noqa: E402
from regenerate_checkpoint_matrices import AISHA_SKILL_ROUNDS, _skill_ids, _snapshot_at_batch  # noqa: E402

FATBEANS_DIR = ROOT / "data/samples/fatbeans"


def main() -> int:
    samples = sorted(p for p in FATBEANS_DIR.glob("*aisha*4rounds*.json"))
    status_by_round: dict[int, Counter] = {r: Counter() for r, _, _ in AISHA_SKILL_ROUNDS}
    failures: list[str] = []
    for sample in samples:
        try:
            events = parse_fatbeans_capture(sample)
            pre = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
            map_id = _map_id_from_events(events)
            snaps = [
                _snapshot_at_batch(events=events, pre_batches=pre, batch_no=i, map_id=map_id)
                for i in range(1, len(pre) + 1)
            ]
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{sample.name}: PARSE-ERR {exc}")
            continue
        for skill_round, skill_id, _ in AISHA_SKILL_ROUNDS:
            snap = next((s for s in snaps if skill_id in _skill_ids(s)), None)
            if snap is None:
                status_by_round[skill_round]["<no-snapshot>"] += 1
                continue
            snap.setdefault("ui_contract", {}).setdefault("context", {})["round"] = skill_round
            budget = hero_max_combos_for_round("aisha", skill_round)
            result = run_reference_engine(prepare_reference_engine_snapshot(snap), max_combos=budget).as_dict()
            status = result.get("status")
            status_by_round[skill_round][status] += 1
            if status == "no_reachable_combo":
                ev = eng.extract_evidence(prepare_reference_engine_snapshot(snap))
                fixed_sum = sum(max(0, int(v)) for v in ev.fixed_counts.values())
                center_note = next((n for n in (result.get("notes") or []) if n.startswith("total_count_prior_center:")), "?")
                failures.append(
                    f"{sample.name}  R{skill_round}  fixed_sum={fixed_sum} "
                    f"fixed={dict(ev.fixed_counts)} {center_note}"
                )

    print(f"samples={len(samples)}")
    for skill_round, _, label in AISHA_SKILL_ROUNDS:
        print(f"\nR{skill_round}{label}: {dict(status_by_round[skill_round])}")
    print(f"\n=== no_reachable_combo / parse failures ({len(failures)}) ===")
    for line in failures:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
