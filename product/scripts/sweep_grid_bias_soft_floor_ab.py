"""A/B grid-accuracy sweep for the unpinned-tail soft-floor experiment.

For every aisha 4-round sample with a settlement truth, run the decision round (R4) with the
soft-floor flag OFF then ON and compare grid_mid vs truth total_cells. Reports the per-sample
delta for the samples the flag actually changes (unpinned-tail: a counted tier with unknown
cells), plus aggregate grid bias/MAE on that affected subset. Throwaway A/B harness.
"""

from __future__ import annotations

import os
import sys
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


def _grid_mid_at_r4(sample: Path) -> int | None:
    events = parse_fatbeans_capture(sample)
    pre = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
    map_id = _map_id_from_events(events)
    snaps = [
        _snapshot_at_batch(events=events, pre_batches=pre, batch_no=i, map_id=map_id)
        for i in range(1, len(pre) + 1)
    ]
    sr, sid, _ = AISHA_SKILL_ROUNDS[3]  # R4
    snap = next((s for s in snaps if sid in _skill_ids(s)), None)
    if snap is None:
        return None
    snap.setdefault("ui_contract", {}).setdefault("context", {})["round"] = sr
    result = run_reference_engine(
        prepare_reference_engine_snapshot(snap), max_combos=hero_max_combos_for_round("aisha", sr)
    ).as_dict()
    grid = result.get("total_grid_range") or [None, None, None]
    return int(grid[1]) if grid[1] is not None else None


def main() -> int:
    samples = sorted(FATBEANS_DIR.glob("*aisha*4rounds*.json"))
    changed: list[tuple[str, int, int, int]] = []  # name, truth, mid_off, mid_on
    for sample in samples:
        try:
            events = parse_fatbeans_capture(sample)
            truth = int((_settlement_breakdown(events) or {}).get("total_cells") or 0)
        except Exception:  # noqa: BLE001
            continue
        if truth <= 0:
            continue
        os.environ.pop("AISHA_SOFT_GRID_FLOOR_UNPINNED", None)
        try:
            mid_off = _grid_mid_at_r4(sample)
        except Exception:  # noqa: BLE001
            mid_off = None
        os.environ["AISHA_SOFT_GRID_FLOOR_UNPINNED"] = "1"
        try:
            mid_on = _grid_mid_at_r4(sample)
        except Exception:  # noqa: BLE001
            mid_on = None
        os.environ.pop("AISHA_SOFT_GRID_FLOOR_UNPINNED", None)
        if mid_off is None or mid_on is None or mid_off == mid_on:
            continue
        changed.append((sample.name, truth, mid_off, mid_on))

    print(f"samples_with_truth scanned; flag CHANGED R4 grid on {len(changed)} sample(s):\n")
    print(f"{'sample':54s} {'truth':>5s} {'OFF':>5s} {'ON':>5s} {'dOFF':>5s} {'dON':>5s}")
    sum_abs_off = sum_abs_on = 0
    for name, truth, off, on in changed:
        d_off, d_on = off - truth, on - truth
        sum_abs_off += abs(d_off)
        sum_abs_on += abs(d_on)
        print(f"{name[:54]:54s} {truth:>5d} {off:>5d} {on:>5d} {d_off:>+5d} {d_on:>+5d}")
    if changed:
        n = len(changed)
        print(f"\naffected n={n}  MAE OFF={sum_abs_off / n:.1f}  MAE ON={sum_abs_on / n:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
