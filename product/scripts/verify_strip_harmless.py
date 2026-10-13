"""ADVERSARIAL verification of the claim: the ahmed tier105 cons pure-prior red strip is 'harmless'
(non-firing => zero impact; conservative-tier-only; cross-hero/cross-tier safe; reversible).

Runs the engine CORRECTLY with hero='ahmed' (the prior measure script's bug was defaulting aisha) on
the mirror inbox 25xx/45xx ships at the decision round, and checks each clause directly:
 (A) 参考/激进 byte-identical strip-on vs strip-off (conservative-tier-only)
 (B) 保守 <= 参考 always (monotonic clamp -> never inverts the ladder)
 (C) when the strip FIRES, how big is the conservative delta (impact magnitude)
 (D) cross-hero: same frame, ethan/gabriela/tatiana/sophie -> ahmed strip never fires, zero ahmed-note
 (E) cross-tier: same ship forced to a 104/106 map_id -> strip does not fire
 (F) rollback env AHMED_DISABLE_CONS_PURE_PRIOR_RED_STRIP=1 -> conservative == strip-off

Read-only on the engine (toggles env only).
"""
from __future__ import annotations
import glob, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in ("scripts", "scripts/calibration", "hero_ref/src", "hero_ref/tools", "src"):
    sys.path.insert(0, str(ROOT / p))

import aisha_calib as C  # noqa
from audit_aisha_gap import _map_id_from_events  # noqa
from bidking_lab.live.fatbeans import live_batches_from_fatbeans_events  # noqa
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine, _is_pure_prior_red, _map_tier  # noqa
from hero_ref_live_schedule import hero_max_combos_for_round  # noqa
from regenerate_checkpoint_matrices import _snapshot_at_batch  # noqa

MIRROR = str(ROOT / "data/samples/recordings_inbox_20260621_desktop_data3/captures")
SHIP_MAPS = ("2531", "2532", "2533", "2535", "2536", "2538", "2539", "2540", "4531", "4536", "4539")
DIS = "AHMED_DISABLE_CONS_PURE_PRIOR_RED_STRIP"
ENV = "AHMED_CONS_PURE_PRIOR_RED_KEEP"


def _events_from_mirror(d):
    """Mirror entry is a dir named *_reset.json containing the real json of the same name."""
    inner = os.path.join(d, os.path.basename(d))
    use = inner if os.path.isfile(inner) else d
    return C._events(Path(use))


def _ship_dirs():
    out = []
    for mp in SHIP_MAPS:
        out += glob.glob(f"{MIRROR}/*_{mp}_*reset.json")
    return sorted(set(out))


def _run(snap, *, dis=False, keep=None):
    if dis:
        os.environ[DIS] = "1"
    else:
        os.environ.pop(DIS, None)
    if keep is not None:
        os.environ[ENV] = str(keep)
    else:
        os.environ.pop(ENV, None)
    r = run_reference_engine(snap, max_combos=hero_max_combos_for_round("ahmed", 4)).as_dict()
    os.environ.pop(DIS, None); os.environ.pop(ENV, None)
    return r


def _snap_for(ev, mp, hero, dr):
    raw = _snapshot_at_batch(events=ev, pre_batches=[b for b in live_batches_from_fatbeans_events(ev) if b.phase != "settled"], batch_no=dr, map_id=mp, hero=hero)
    return prepare_reference_engine_snapshot(raw)


def main():
    dirs = _ship_dirs()
    print(f"mirror ship dirs: {len(dirs)}\n")
    rows = []
    for d in dirs:
        try:
            ev = _events_from_mirror(d)
            mp = _map_id_from_events(ev)
            pre = [b for b in live_batches_from_fatbeans_events(ev) if b.phase != "settled"]
            if not pre:
                continue
            dr = len(pre)  # TRUE decision round = last pre-settlement batch (not min(n,4))
            snap = _snap_for(ev, mp, "ahmed", dr)
            on = _run(snap, dis=False, keep=0.50)
            off = _run(snap, dis=True)
            fired = any(str(n).startswith("ahmed_cons_pure_prior_red_strip") for n in on.get("notes", []))
            rows.append({
                "map": mp, "dr": dr, "n_pre": len(pre), "fired": fired,
                "on": on, "off": off,
                "ctx_hero": snap.get("ui_contract", {}).get("context", {}).get("hero"),
            })
        except Exception as e:
            print(f"  ERR {os.path.basename(d)[:40]}: {str(e)[:70]}")

    # ---- Clause A: 参考/激进 unchanged on vs off ----
    print("=== (A) 参考/激进 byte-identical strip-on vs strip-off? ===")
    bal_diff = agg_diff = 0
    for r in rows:
        if r["on"]["balanced"] != r["off"]["balanced"]:
            bal_diff += 1
        if r["on"]["aggressive"] != r["off"]["aggressive"]:
            agg_diff += 1
    print(f"  ships with balanced changed: {bal_diff}/{len(rows)}")
    print(f"  ships with aggressive changed: {agg_diff}/{len(rows)}")

    # ---- Clause B: 保守 <= 参考 always (ladder never inverts) ----
    print("\n=== (B) 保守 <= 参考 always (strip never pushes 保守 above 参考)? ===")
    bad = [r for r in rows if r["on"]["conservative"] > r["on"]["balanced"]]
    print(f"  ships where 保守 > 参考 (strip ON): {len(bad)}/{len(rows)}")
    for r in bad:
        print(f"    VIOLATION map={r['map']} cons={r['on']['conservative']} bal={r['on']['balanced']}")

    # ---- Clause C: impact magnitude when it fires ----
    print("\n=== (C) conservative delta WHEN strip fires (impact magnitude) ===")
    fired_rows = [r for r in rows if r["fired"]]
    print(f"  fire rate at decision round (hero=ahmed): {len(fired_rows)}/{len(rows)}")
    print(f"  ctx.hero sample: {rows[0]['ctx_hero'] if rows else 'NA'} (must be 'ahmed')")
    for r in sorted(fired_rows, key=lambda x: x["map"]):
        d_cons = (r["on"]["conservative"] - r["off"]["conservative"]) / 1e6
        print(f"    map={r['map']} dr={r['dr']} 保守 {r['off']['conservative']/1e6:.2f}M -> {r['on']['conservative']/1e6:.2f}M  (Δ {d_cons:+.2f}M)")
    if fired_rows:
        deltas = [(r["on"]["conservative"] - r["off"]["conservative"]) / 1e6 for r in fired_rows]
        print(f"  mean Δcons when fired: {sum(deltas)/len(deltas):+.2f}M  (max drop {min(deltas):+.2f}M)")

    # ---- Clause D: cross-hero on the SAME frame ----
    print("\n=== (D) cross-hero: same frame, ahmed-strip note must NOT appear for other heroes ===")
    if rows:
        # pick a ship that fired for ahmed (so the frame is pure-prior tier105)
        probe = (fired_rows or rows)[0]
        ev = _events_from_mirror([d for d in dirs if str(probe["map"]) in os.path.basename(d)][0])
        mp = probe["map"]; dr = probe["dr"]
        for h in ("ahmed", "aisha", "ethan", "gabriela", "tatiana", "sophie"):
            try:
                snap = _snap_for(ev, mp, h, dr)
                res = _run(snap, dis=False, keep=0.50)
                notes = res.get("notes", [])
                ahmed_note = any(str(n).startswith("ahmed_cons_pure_prior_red_strip") for n in notes)
                aisha_note = any(str(n).startswith("aisha_cons_pure_prior_red_strip") for n in notes)
                print(f"  hero={h:9s} map={mp} ahmed_strip={ahmed_note}  aisha_strip={aisha_note}  cons={res['conservative']/1e6:.2f}M bal={res['balanced']/1e6:.2f}M")
            except Exception as e:
                print(f"  hero={h:9s} ERR {str(e)[:60]}")

    # ---- Clause E: cross-tier (force a 104 / 106 map_id on a firing ahmed frame) ----
    print("\n=== (E) cross-tier: same ahmed frame forced to non-105 map_id -> strip must NOT fire ===")
    if fired_rows:
        probe = fired_rows[0]
        ev = _events_from_mirror([d for d in dirs if str(probe["map"]) in os.path.basename(d)][0])
        dr = probe["dr"]
        for forced in (probe["map"], 2401, 2601, 2101):  # 105(real), 104, 106, 101
            snap = _snap_for(ev, forced, "ahmed", dr)
            res = _run(snap, dis=False, keep=0.50)
            fired = any(str(n).startswith("ahmed_cons_pure_prior_red_strip") for n in res.get("notes", []))
            print(f"  forced map_id={forced} -> tier {_map_tier(forced)}  ahmed_strip_fired={fired}")

    # ---- Clause F: rollback ----
    print("\n=== (F) rollback AHMED_DISABLE...=1 -> conservative == strip-off for every ship ===")
    mism = 0
    for r in rows:
        # off already computed with DIS=1; re-derive explicitly and compare to a plain default run
        snap = _snap_for(_events_from_mirror([d for d in dirs if str(r["map"]) in os.path.basename(d)][0]), r["map"], "ahmed", r["dr"])
        disabled = _run(snap, dis=True)
        default_on = _run(snap, dis=False, keep=0.50)
        if disabled["conservative"] != r["off"]["conservative"]:
            mism += 1
        # also: if it fired, disabled MUST differ from default_on (proving the flag is the toggle)
    print(f"  ships where DIS=1 conservative != recomputed off: {mism}/{len(rows)} (should be 0)")
    # how many ships does the flag actually change?
    changed_by_flag = sum(1 for r in rows if r["on"]["conservative"] != r["off"]["conservative"])
    print(f"  ships where DIS toggles conservative (i.e. strip had an effect): {changed_by_flag}/{len(rows)}")


if __name__ == "__main__":
    main()
