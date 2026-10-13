"""A/B regression sweep for the high-tier-grid no_reachable fallback.

For every aisha 4-round sample, run each skill-round snapshot TWICE: with the
fallback ON (default) and OFF (_allow_high_tier_grid_fallback=False). Report:
  - status distribution per round (ON)
  - RESCUED: was no_reachable OFF, now ok/count_prior ON  (the benefit)
  - REGRESSED: ON differs from OFF in any way OTHER than rescue (must be ZERO)
  - value drift on shared ok/count_prior rounds (must be ZERO — fallback never
    fires when combos exist)

Run: python scripts/sweep_high_tier_grid_fallback_ab.py
"""

from __future__ import annotations

import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "scripts", ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools", ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from audit_aisha_gap import _map_id_from_events  # noqa: E402
from bidking_lab.live.fatbeans import live_batches_from_fatbeans_events, parse_fatbeans_capture  # noqa: E402
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402
from hero_ref_live_schedule import hero_max_combos_for_round  # noqa: E402
from regenerate_checkpoint_matrices import AISHA_SKILL_ROUNDS, _skill_ids, _snapshot_at_batch  # noqa: E402

FATBEANS_DIR = ROOT / "data/samples/fatbeans"


def main() -> int:
    samples = sorted(p for p in FATBEANS_DIR.glob("*valid_aisha*4rounds*.json"))
    status_on: dict[int, Counter] = {r: Counter() for r, _, _ in AISHA_SKILL_ROUNDS}
    rescued: list[str] = []
    regressed: list[str] = []
    drift: list[str] = []
    n_rounds = 0
    t0 = time.time()
    for idx, sample in enumerate(samples):
        try:
            events = parse_fatbeans_capture(sample)
            pre = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
            map_id = _map_id_from_events(events)
            snaps = [
                _snapshot_at_batch(events=events, pre_batches=pre, batch_no=i, map_id=map_id)
                for i in range(1, len(pre) + 1)
            ]
        except Exception as exc:  # noqa: BLE001
            regressed.append(f"{sample.name}: PARSE-ERR {exc}")
            continue
        for skill_round, skill_id, _ in AISHA_SKILL_ROUNDS:
            snap = next((s for s in snaps if skill_id in _skill_ids(s)), None)
            if snap is None:
                continue
            snap.setdefault("ui_contract", {}).setdefault("context", {})["round"] = skill_round
            budget = hero_max_combos_for_round("aisha", skill_round)
            prepared = prepare_reference_engine_snapshot(snap)
            on = run_reference_engine(prepared, max_combos=budget).as_dict()
            off = run_reference_engine(
                prepared, max_combos=budget, _allow_high_tier_grid_fallback=False
            ).as_dict()
            n_rounds += 1
            status_on[skill_round][on.get("status")] += 1
            s_on, s_off = on.get("status"), off.get("status")
            if s_on != s_off:
                if s_off == "no_reachable_combo" and s_on in {"ok", "count_prior"}:
                    rescued.append(f"{sample.name} R{skill_round}: {s_off}->{s_on} bal={on.get('balanced')}")
                else:
                    regressed.append(f"{sample.name} R{skill_round}: OFF={s_off} ON={s_on}")
            else:
                # same status -> values must be identical (fallback must not have fired)
                if on.get("balanced") != off.get("balanced") or on.get("total_grid_range") != off.get("total_grid_range"):
                    drift.append(
                        f"{sample.name} R{skill_round}: bal {off.get('balanced')}->{on.get('balanced')} "
                        f"grid {off.get('total_grid_range')}->{on.get('total_grid_range')}"
                    )
        if (idx + 1) % 20 == 0:
            print(f"  ...{idx+1}/{len(samples)} ({time.time()-t0:.0f}s)", flush=True)

    print(f"\nsamples={len(samples)} rounds={n_rounds} elapsed={time.time()-t0:.0f}s")
    for skill_round, _, label in AISHA_SKILL_ROUNDS:
        print(f"R{skill_round}{label}: {dict(status_on[skill_round])}")
    print(f"\n=== RESCUED ({len(rescued)}) — no_reachable now quotes ===")
    for line in rescued:
        print(f"  {line}")
    print(f"\n=== REGRESSED ({len(regressed)}) — MUST be 0 ===")
    for line in regressed:
        print(f"  {line}")
    print(f"\n=== VALUE DRIFT on shared rounds ({len(drift)}) — MUST be 0 ===")
    for line in drift:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
