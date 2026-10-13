"""Emit synth-B ethan samples (author 2026-07-03, mechanism confirmed correct).

Source = AHMED clean vaults (no reveal-skill/props => cleanest, easiest-verified settlement truth) in
families villa(24/34/44) + 排位56xx + 沉船(25/35/45), time-windowed:
  * villa / 56xx : capture date >= 2026-06-25 12:00 (NEW drop-rate regime; no upper bound).
  * 沉船         : [2026-06-25 12:00, 2026-07-02 12:00)  (before the shipwreck rate change/activity).

Per vault: draw a terminal (decision) round R3-R5 weighted (R3/R4 common, R5 full-outline rare ~20%);
reveal a coverage-calibrated UNIFORM skill subset ∪ ALL public-revealed items (public is prior->
skill-outlined by any R>=3 decision round), sized to real ethan coverage {R1 51 R2 55 R3 57 R4 60 R5 100}.
Public reveals broadened to ANY quality-bearing item (incl. uncatalogued type reveals 200040/041 that
show 10+ items at once). Emits engine-consumable snapshot + settlement truth, same record format as the
draw-hero pipeline, to data/samples/synthetic_draw/ethan/. See memory ethan-synthesis-b-reveal-mechanism.

Usage: python scripts/emit_ethan_synth.py [n=500] [seed=7]
"""
from __future__ import annotations
import glob, json, random, re, struct, sys, time
from pathlib import Path


def _f32(x: float) -> float:
    """Round-trip through IEEE-754 single precision — the game's avg packets are float32 on the wire
    (verified vs a real ethan 优品均格: 35/9 -> 3.8888888359069824). The engine's integer-product lock
    uses ULP tolerance for wire values, so feeding the raw float32 ratio (NOT a display-truncated value)
    matches live and lets the lock recover the true count."""
    return struct.unpack("f", struct.pack("f", float(x)))[0]

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "scripts", ROOT / "hero_ref/src", ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
from synthesize_draw_hero_samples import (  # noqa: E402
    load_warehouse, _truth_consistent, _synthesize_ethan_snapshot, SYNTH_DATASET_DIR)
from build_clean_eval_set import (  # noqa: E402
    _load_session_dates, _dt6, WINDOW_LOW, WINDOW_HIGH, _SID, clean_eval_files)
from bidking_lab.live.fatbeans import parse_fatbeans_capture  # noqa: E402
from bidking_lab.live.monitor import _public_info_rows  # noqa: E402
from bidking_lab.runtime.snapshot import _ui_public_numeric_contract  # noqa: E402

COV = {1: 0.51, 2: 0.55, 3: 0.57, 4: 0.60, 5: 1.0}  # real per-round median coverage
TERM_ROUNDS, TERM_W = [3, 4, 5], [0.45, 0.35, 0.20]  # decision round: R3/R4 common, R5 rare

# ── New-standard ethan tool chain (author 2026-07-09) ──────────────────────────────────────────────
# Replace ONLY ethan's skill+tool usage on an ahmed base vault; keep public info + geometry + settled
# truth; NO round drift. Five tools, applied by GAME round (term). Fed through the FAITHFUL channel the
# live monitor uses — {action_id, result} rows in ui_contract.actions.results / action_result_rows that
# extract_evidence reads (ACTION_TOTAL_CELLS/AVG_CELLS/VALUE_SUM, ahmad_ref_engine.py:5270-5300) — NOT the
# structured_ref_inputs.field_updates bucket bridge (the live v2/v3 monitor does NOT bridge that for ethan;
# feeding it was one of the old synth's unfaithfulness modes). Action ids verified vs the engine tables:
#   普品扫描 100104→q1 cells (q1 = white+green merged, QUALITY_NUM_TO_KEY 1,2→q1) ; 良品扫描 100105→q3 cells ;
#   优品均格 100112→q4 avg_cells = RAW cells/count as float32 (matches the live wire packet: verified vs a
#     real ethan 优品均格 = 3.8888888359069824 = f32(35/9); the wire is NOT display-truncated, and the
#     engine's integer-product lock uses ULP tolerance on the raw ratio to recover the count) ;
#   极品扫描 100107→q5 cells  OR  极品估价 100125→q5 value_sum  (66/33) ;  抽检×2 → public 2-item random avg
#     (random_avg_red_anchor is default-OFF for ethan, so this is a soft floor only — never a red anchor).
GOLD_SCAN_PROB = 0.66  # 极品扫描 (cells) vs 极品估价 (value) split
# 优品均格 (avg cells) vs 优品估价 (value) split for the R3 purple slot. Author-approved 2026-07-10
# addition: the original 5-tool spec was 均格-only, but REAL ethan q4 evidence is value-dominant
# (总价 ~24% of samples vs 均格 ~9%), and the q4-mid lever reads ONLY the value path — the 均格-only
# 500-pool could never exercise it (census: q4_src value=0/500). Mirrors the q5 66/33 pattern.
PURPLE_AVG_PROB = 0.67


def _ethan_toolchain_action_rows(wh, term: int, rng: random.Random) -> tuple[list, list]:
    """(action_result_rows, public_numeric_facts) for the 5-tool chain at decision round `term`.
    Derived from settlement truth. Only tiers with >0 count emit a row (no phantom-zero)."""
    q_cells: dict[int, int] = {}
    q_count: dict[int, int] = {}
    q_value: dict[int, float] = {}
    for it in wh.items:
        q = int(it["quality"])
        q_cells[q] = q_cells.get(q, 0) + int(it.get("cells") or 0)
        q_count[q] = q_count.get(q, 0) + 1
        q_value[q] = q_value.get(q, 0.0) + float(it.get("value") or 0)
    q1_cells = q_cells.get(1, 0) + q_cells.get(2, 0)  # white+green merge into q1
    q3_cells = q_cells.get(3, 0)
    q4_cells, q4_count = q_cells.get(4, 0), q_count.get(4, 0)
    q5_cells, q5_value = q_cells.get(5, 0), q_value.get(5, 0.0)
    rows: list[dict] = []
    # R1/R2 (random which lands first; immaterial at a terminal snapshot term>=3 where both are in)
    if term >= 2:
        if q1_cells > 0:
            rows.append({"action_id": 100104, "result": q1_cells})
        if q3_cells > 0:
            rows.append({"action_id": 100105, "result": q3_cells})
    elif term == 1:
        if rng.random() < 0.5 and q1_cells > 0:
            rows.append({"action_id": 100104, "result": q1_cells})
        elif q3_cells > 0:
            rows.append({"action_id": 100105, "result": q3_cells})
    # R3 purple: 67% 优品均格 -> q4 avg_cells = RAW cells/count as float32 (matches the live wire
    # packet exactly; a display-truncated value would break the integer-product count lock — verified
    # vs real ethan) / 33% 优品估价 -> q4 value_sum (100124, mirrors the q5 100125 wire).
    if term >= 3 and q4_count > 0 and q4_cells > 0:
        if rng.random() < PURPLE_AVG_PROB:
            rows.append({"action_id": 100112, "result": _f32(q4_cells / q4_count)})
        elif q_value.get(4, 0.0) > 0:
            rows.append({"action_id": 100124, "result": q_value.get(4, 0.0)})
    # R4 gold: 66% 极品扫描 (cells) / 33% 极品估价 (value)
    if term >= 4:
        if rng.random() < GOLD_SCAN_PROB:
            if q5_cells > 0:
                rows.append({"action_id": 100107, "result": q5_cells})
        elif q5_value > 0:
            rows.append({"action_id": 100125, "result": q5_value})
    # 抽检 x2 (last): mean value of 2 random items -> public random_2_avg_value (soft, anchor-OFF for ethan)
    pub_facts: list[dict] = []
    if term >= 5 and len(wh.items) >= 2:
        picks = rng.sample(list(wh.items), 2)
        avg2 = sum(float(it.get("value") or 0) for it in picks) / 2.0
        if avg2 > 0:
            pub_facts.append({"semantic": "random_2_avg_value", "sample_count": 2, "value": avg2})
    return rows, pub_facts


def _new_standard_snapshot(wh, term: int, rng: random.Random, seen: set,
                           pub_quality: dict | None = None,
                           source_path: str | None = None) -> dict:
    """Ethan new-standard snapshot: reuse the validated outline/minimap/public base, drop the unfaithful
    field_updates bucket bridge, feed the 5-tool chain through the live actions.results channel.
    pub_quality (item idx -> quality) = PUBLICLY revealed qualities: stamped onto minimap_grid_items to
    mirror live (_minimap_grid_items carries public-revealed quality; the skill outline rows stay
    quality=None — ethan's skill gives outline only).
    source_path: the ahmed source capture — its public rows are routed through the LIVE contract builder
    (_public_info_rows -> _ui_public_numeric_contract), same as live/_build_snapshot. Same-vault
    real-vs-synth (2026-07-09): _synthesize_ethan_snapshot's own constraints carry ONLY total_avg_cells,
    which starved the engine of exact public counts/avgs — tool-less vaults diverged ±15-22% with wrong
    q-mids; with the full contract copied, 8/8 samples converge to d=0.0% byte-identical q-mids."""
    snap = _synthesize_ethan_snapshot(wh, term, rng, revealed_idxs=seen, feed_appraisal_value=False)
    snap["structured_ref_inputs"] = {}  # drop the field_updates bucket bridge (unfaithful for ethan)
    if source_path:
        try:
            ev = parse_fatbeans_capture(Path(source_path))
            rows = _public_info_rows(ev, {})
            snap.setdefault("ui_contract", {}).setdefault("constraints", {})["public_info"] = \
                _ui_public_numeric_contract(rows)
            snap["public_info_rows"] = rows
        except Exception:
            pass  # keep the (weaker) total_avg_cells-only contract from the base builder
    if pub_quality:
        rid_q = {}
        for i, q in pub_quality.items():
            it = wh.items[i] if 0 <= i < len(wh.items) else None
            if it is not None and it.get("runtime_id") is not None:
                rid_q[int(it["runtime_id"])] = int(q)
        for mm in snap.get("minimap_grid_items") or []:
            rid = mm.get("runtime_id")
            if rid is not None and int(rid) in rid_q:
                mm["quality"] = rid_q[int(rid)]
    tool_rows, pub_facts = _ethan_toolchain_action_rows(wh, term, rng)
    snap["action_result_rows"] = tool_rows
    uic = snap.setdefault("ui_contract", {})
    uic["actions"] = {"results": tool_rows}
    if pub_facts:
        cons = uic.setdefault("constraints", {})
        pinfo = cons.setdefault("public_info", {})
        pinfo.setdefault("public_numeric_facts", []).extend(pub_facts)
    snap["_synthetic"]["standard"] = "new_2026-07-09_toolchain"
    return snap


def fam(m: int | None) -> str:
    p = int(m or 0) // 100
    return ("villa" if p in (24, 34, 44) else "沉船" if p in (25, 35, 45)
            else "56xx" if p == 56 else "hidden" if p == 26 else "other")


_MAP_RE = re.compile(r"_(?:valid|mixed)_[a-z]+_(\d{3,4})_")


def _is_activity_ship(fname: str) -> bool:
    # Activity-exclusive shipwreck ids X511-X520 (2026-07-06 census); mirror of the engine helper.
    mm = _MAP_RE.search(fname)
    if not mm:
        return False
    mid = int(mm.group(1))
    return (2500 <= mid < 2600 or 4500 <= mid < 4600) and 11 <= (mid % 100) <= 20


def in_window(fname: str, family: str, sd: dict) -> bool:
    m = _SID.search(fname)
    dt = _dt6(sd.get(m.group(1))) if m else None
    if dt is None or dt < WINDOW_LOW:
        return False
    # ⭐ 2026-07-06: exclude shipwreck ACTIVITY by map NUMBER (X511-X520), not the 07-02 time bound —
    # normal X501-X510 shipwreck post-07-02 are valid NEW-rate sources (were wrongly dropped before).
    if _is_activity_ship(fname):
        return False
    return True  # villa/56xx/normal-shipwreck: no upper bound (NEW-rate regime)


def spatial_ok(items) -> bool:
    """Hardened validity (same as sample-validity filter): every item positioned, zero overlap,
    distinct-covered==sum, top-55% rows full. ahmed vaults are clean but verify anyway."""
    pos = [it for it in items if it.get("row") is not None and it.get("col") is not None]
    if len(pos) != len(items) or not pos:
        return False
    maxr = max(it["row"] + (it.get("height") or 1) - 1 for it in pos)
    cols = max(max(it["col"] + (it.get("width") or 1) - 1 for it in pos), 10)
    grid = [[0] * cols for _ in range(maxr)]
    overlap = 0
    for it in pos:
        for r in range(it["row"] - 1, it["row"] - 1 + (it.get("height") or 1)):
            for c in range(it["col"] - 1, it["col"] - 1 + (it.get("width") or 1)):
                if 0 <= r < maxr and 0 <= c < cols:
                    if grid[r][c]:
                        overlap += 1
                    grid[r][c] += 1
    distinct = sum(1 for r in range(maxr) for c in range(cols) if grid[r][c])
    tcells = sum(int(it.get("cells") or 0) for it in items)
    rf = [sum(1 for c in range(cols) if grid[r][c]) for r in range(maxr)]
    top = max(2, round(maxr * 0.55))
    return overlap == 0 and abs(distinct - tcells) <= 2 and sum(cols - rf[r] for r in range(top)) == 0


def public_item_idxs(source_path: str, items) -> dict[int, int]:
    """ANY quality-bearing public reveal item (broadened: incl. uncatalogued type reveals 200040/041).
    Public is hero-independent + prior to skill => outlined (footprint known) by any R>=3 decision round.
    Returns {item idx: TRUTH quality} — the quality is stamped onto minimap_grid_items (live parity)."""
    idx_by_rid = {int(it["runtime_id"]): i for i, it in enumerate(items) if it.get("runtime_id") is not None}
    ev = parse_fatbeans_capture(source_path)
    out: dict[int, int] = {}
    for st in ev.states:
        for pi in getattr(st, "public_infos", ()) or ():
            for oi in getattr(pi, "observed_items", ()) or ():
                if getattr(oi, "quality", None) is None:
                    continue
                i = idx_by_rid.get(int(getattr(oi, "runtime_id", 0) or 0))
                if i is not None:
                    out[i] = int(items[i].get("quality") or 0)
    return out


def build_seen(items, pub_idxs: set[int], term: int, rng: random.Random) -> set[int]:
    """Footprints ethan sees at the decision round = ALL public (outlined by R>=3) ∪ a uniform-random
    skill sample, sized so the UNION reaches the round's coverage target (public embedded, not extra)."""
    if term >= 5:
        return set(range(len(items)))  # R5 full outline
    cells = [int(it.get("cells") or 0) for it in items]
    tgt = COV[term] * (sum(cells) or 1)
    seen = set(i for i in pub_idxs if 0 <= i < len(items))
    nonpub = [i for i in range(len(items)) if i not in seen]
    rng.shuffle(nonpub)
    k = 0
    while k < len(nonpub) and sum(cells[i] for i in seen) < tgt:
        seen.add(nonpub[k])
        k += 1
    return seen


def emit(n: int = 500, seed: int = 7, new_standard: bool = True) -> int:
    rng = random.Random(seed)
    # Base = clean_eval AHMED (author-fixed): already windowed to the NEW-rate main window, activity ids
    # X511-X520 excluded, and the 82 declared-vs-settled mismatch ahmed removed (bad truth). This replaces
    # the old raw fatbeans_valid_ahmed_* glob which skipped the declared-mismatch exclusion.
    cands = list(clean_eval_files("ahmed"))
    rng.shuffle(cands)
    out_dir = SYNTH_DATASET_DIR / "ethan"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("synth_*.json"):
        old.unlink()
    written, skipped, fam_ct, term_ct, t0 = 0, 0, {}, {}, time.time()
    for f in cands:
        if written >= n:
            break
        name = Path(f).name
        if "INVALID" in f or "mixed" in name:
            continue
        wh = load_warehouse(Path(f), with_geometry=True)
        if wh is None or wh.total_count <= 0:
            continue
        family = fam(wh.map_id)
        # include villa / 沉船 / 56xx / hidden (the point is to fill the MISSING villa/hidden family
        # priors that the real 99-sample ethan pool, almost all 56xx, lacks). clean_eval already windowed.
        if family == "other" or not _truth_consistent(wh):
            skipped += 1
            continue
        if not spatial_ok(wh.items):
            skipped += 1
            continue
        srng = random.Random(rng.random())
        term = srng.choices(TERM_ROUNDS, weights=TERM_W)[0]
        pub = public_item_idxs(f, wh.items)
        seen = build_seen(wh.items, pub, term, srng)
        if new_standard:
            snap = _new_standard_snapshot(wh, term, srng, seen, pub_quality=pub, source_path=f)
        else:
            # legacy field_updates-bridge path (UNFAITHFUL for ethan; kept for A/B only)
            snap = _synthesize_ethan_snapshot(wh, term, srng, revealed_idxs=seen, feed_appraisal_value=False)
        tcells = sum(int(it.get("cells") or 0) for it in wh.items)
        rec = {
            "snapshot": snap, "hero": "ethan", "round": term, "era": "NEW", "source": wh.source,
            "map_id": wh.map_id, "truth_count": wh.total_count,
            "truth_counts": {str(k): v for k, v in wh.truth_counts.items()},
            "truth_total_value": wh.truth_total_value, "truth_cells": tcells,
            "synth_meta": {"family": family, "term_round": term, "n_public": len(pub),
                           "n_seen": len(seen), "seen_cells": sum(int(wh.items[i].get("cells") or 0) for i in seen),
                           "coverage": round(sum(int(wh.items[i].get("cells") or 0) for i in seen) / (tcells or 1), 3)},
        }
        (out_dir / f"synth_ethan_{written:04d}.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        written += 1
        fam_ct[family] = fam_ct.get(family, 0) + 1
        term_ct[term] = term_ct.get(term, 0) + 1
        if written % 100 == 0:
            print(f"  {written}/{n} ({time.time() - t0:.0f}s)", flush=True)
    print(f"[emit ethan] {out_dir}  {written} samples ({time.time() - t0:.0f}s; skipped {skipped})")
    print(f"  family: {fam_ct}")
    print(f"  terminal round: {dict(sorted(term_ct.items()))}")
    return 0


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 7
    raise SystemExit(emit(n, seed))
