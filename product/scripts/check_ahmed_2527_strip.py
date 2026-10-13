"""Run the engine per pre-settlement round on the one settled ahmed tier105 ship (2527, truth red=3)
with the conservative red strip ON vs OFF — confirm the strip helps on a genuine low-red ship.

Read-only. Reuses the calibration fatbeans pipeline. Run: python scripts/check_ahmed_2527_strip.py
"""
from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in ("scripts", "scripts/calibration", "hero_ref/src", "hero_ref/tools", "src"):
    sys.path.insert(0, str(ROOT / p))

import aisha_calib as C  # noqa: E402
from audit_aisha_gap import _map_id_from_events  # noqa: E402
from bidking_lab.live.fatbeans import live_batches_from_fatbeans_events  # noqa: E402
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402
from hero_ref_live_schedule import hero_max_combos_for_round  # noqa: E402
from regenerate_checkpoint_matrices import _snapshot_at_batch  # noqa: E402


def _m(x):
    return f"{x/1e6:.2f}M" if x else "0"


def main():
    f = glob.glob(str(ROOT / "data/samples/fatbeans_activity_20260605_shipwreck/*ahmed_2527*.json"))[0]
    ev = C._events(Path(f))
    bd = C.settlement_breakdown(ev)
    nt = C.normalized_truth(ev)
    truth_red = int((bd.get("counts") or {}).get(6, 0))
    print(f"=== ahmed 2527  truth: red={truth_red}  rawVal={_m(bd.get('total_value'))}  "
          f"normVal={_m(nt.get('norm_value'))} ({nt.get('category')}) ===")
    pre = [b for b in live_batches_from_fatbeans_events(ev) if b.phase != "settled"]
    mp = _map_id_from_events(ev)
    snaps = [_snapshot_at_batch(events=ev, pre_batches=pre, batch_no=i, map_id=mp)
             for i in range(1, len(pre) + 1)]
    for ridx, snap in enumerate(snaps, 1):
        prepped = prepare_reference_engine_snapshot(snap)
        mc = hero_max_combos_for_round("ahmed", ridx)

        os.environ.pop("AHMED_DISABLE_CONS_PURE_PRIOR_RED_STRIP", None)
        on = run_reference_engine(prepped, max_combos=mc).as_dict()
        os.environ["AHMED_DISABLE_CONS_PURE_PRIOR_RED_STRIP"] = "1"
        off = run_reference_engine(prepped, max_combos=mc).as_dict()
        os.environ.pop("AHMED_DISABLE_CONS_PURE_PRIOR_RED_STRIP", None)

        fired = any(str(n).startswith("ahmed_cons_pure_prior_red_strip") for n in on.get("notes", []))
        print(f"R{ridx}: 保守 {_m(off['conservative'])} -> {_m(on['conservative'])}"
              f"  | 参考 {_m(off['balanced'])}=={_m(on['balanced'])}"
              f"  激进 {_m(off['aggressive'])}=={_m(on['aggressive'])}"
              f"  red_est={on.get('red_count_range')}  strip={'Y' if fired else 'n'}")


if __name__ == "__main__":
    main()
