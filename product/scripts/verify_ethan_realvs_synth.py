"""SAME-VAULT real-vs-synth fidelity comparison (author 2026-07-09 evening request).

For each REAL clean_eval ethan sample at its decision round:
  A) REAL: the sample's own snapshot (real skill frames + real tool rows + real public) -> engine.
  B) SYNTH-REBUILD: same vault's settlement truth -> strip the real ethan skill frames, re-add SYNTH
     skill frames covering the SAME revealed item set (mapped by runtime_id), and script-generated tool
     rows for the SAME tool loadout the real player used (values recomputed from truth).  -> engine.
Compare per sample:
  - tool wire values: real captured value vs truth-derived value (exact match expected — proves the
    script derives exactly what the game sends);
  - engine outputs: balanced / total_grid / per-quality count ranges (A vs B should be ~identical if
    the synth channel is faithful; residual diff = public/round context differences, reported).
"""
import sys, json, struct
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT/"scripts", ROOT/"hero_ref"/"src", ROOT/"hero_ref"/"tools", ROOT/"src", ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
from synthesize_draw_hero_samples import load_warehouse, _synthesize_ethan_snapshot
from ahmad_ref_engine import run_reference_engine, ACTION_TOTAL_CELLS, ACTION_AVG_CELLS, ACTION_VALUE_SUM
from hero_ref_live_schedule import hero_max_combos_for_round
from build_clean_eval_set import clean_eval_files
from audit_aisha_gap import (_build_snapshot, _events_through_sort, _prefix_sort_id,
    _settlement_breakdown, _map_id_from_events, _action_result_rows_from_events)
from bidking_lab.live.fatbeans import parse_fatbeans_capture, live_batches_from_fatbeans_events
from bidking_lab.live.monitor import _skill_reveal_rows

ETHAN_ID = 208
Q2KEY = {"q1": (1, 2), "q3": (3,), "q4": (4,), "q5": (5,), "q6": (6,)}


def _f32(x):
    return struct.unpack("f", struct.pack("f", float(x)))[0]


def truth_tool_value(wh, action_id):
    """Recompute the wire value of a tool from settlement truth (generic across loadouts)."""
    aid = str(action_id)
    def tier_items(qkey):
        quals = Q2KEY.get(qkey, ())
        return [it for it in wh.items if int(it.get("quality") or 0) in quals]
    if aid in ACTION_TOTAL_CELLS:
        its = tier_items(ACTION_TOTAL_CELLS[aid])
        return float(sum(int(it.get("cells") or 0) for it in its))
    if aid in ACTION_AVG_CELLS:
        its = tier_items(ACTION_AVG_CELLS[aid])
        if not its:
            return None
        return _f32(sum(int(it.get("cells") or 0) for it in its) / len(its))
    if aid in ACTION_VALUE_SUM:
        its = tier_items(ACTION_VALUE_SUM[aid])
        return float(sum(float(it.get("value") or 0) for it in its))
    return None


def qmid(res, q):
    r = (res.get("quality_count_ranges") or {}).get(q)
    return r[1] if r and len(r) == 3 else None


def run(snap, rnd):
    mc = min(20000, hero_max_combos_for_round("ethan", min(int(rnd or 5), 5)))
    return run_reference_engine(snap, max_combos=mc).as_dict()


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    files = clean_eval_files("ethan")[:n]
    rows = []
    for fi, f in enumerate(files):
        try:
            events = parse_fatbeans_capture(Path(f))
        except Exception as e:
            print("PARSE FAIL", Path(f).name, e); continue
        st = _settlement_breakdown(events)
        if not st:
            continue
        tval = int(st.get("total_value") or sum((st.get("values") or {}).values()) or 0)
        mid_ = _map_id_from_events(events)
        wh = load_warehouse(Path(f), with_geometry=True)
        if wh is None:
            print("no warehouse", Path(f).name); continue
        pre = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
        prefix = None
        for r in range(len(pre), 0, -1):
            if not all(b.phase == "reading" for b in pre[:r]):
                prefix = pre[:r]; break
        if not prefix:
            continue
        sid = _prefix_sort_id(prefix)
        pe = _events_through_sort(events, sid) if sid else events
        # ---- A) REAL snapshot ----
        real_snap = _build_snapshot(hero="ethan", events=pe, prefix_batches=prefix, map_id=mid_)
        ar = ((real_snap.get("ui_contract") or {}).get("context") or {}).get("round")
        res_a = run(real_snap, ar)
        # real tool rows (dedup by action_id, keep first value)
        real_tools = {}
        for row in _action_result_rows_from_events(pe):
            aid = str(row.get("action_id"))
            if aid in ACTION_TOTAL_CELLS or aid in ACTION_AVG_CELLS or aid in ACTION_VALUE_SUM:
                real_tools.setdefault(aid, row.get("result"))
        # real revealed footprint set from ethan skill rows -> warehouse idx
        idx_by_rid = {int(it["runtime_id"]): i for i, it in enumerate(wh.items)
                      if it.get("runtime_id") is not None}
        seen = set()
        for sr in _skill_reveal_rows(pe, {}):
            if int(sr.get("hero_id") or -1) != ETHAN_ID:
                continue
            for oi in (sr.get("observed_items") or []):
                i = idx_by_rid.get(int(oi.get("runtime_id") or 0))
                if i is not None:
                    seen.add(i)
        # ---- B) SYNTH rebuild: same vault, same revealed set, same tool loadout w/ truth values ----
        synth_snap = _synthesize_ethan_snapshot(wh, int(ar or 5), __import__("random").Random(7),
                                                revealed_idxs=seen, feed_appraisal_value=False)
        synth_snap["structured_ref_inputs"] = {}
        # live parity: publicly-revealed qualities ride in minimap_grid_items (same as the generator's
        # _new_standard_snapshot pub_quality stamp) — without this, tool-less vaults diverge.
        import emit_ethan_synth as E
        pubq = E.public_item_idxs(str(f), wh.items)
        rid_q = {}
        for i, q in pubq.items():
            it = wh.items[i]
            if it.get("runtime_id") is not None:
                rid_q[int(it["runtime_id"])] = int(q)
        for mm in synth_snap.get("minimap_grid_items") or []:
            rid = mm.get("runtime_id")
            if rid is not None and int(rid) in rid_q:
                mm["quality"] = rid_q[int(rid)]
        # HYPOTHESIS 2 (public numeric facts channel): _synthesize_ethan_snapshot only feeds
        # total_avg_cells into constraints.public_info, while live/_build_snapshot routes ALL public
        # rows through _ui_public_numeric_contract. "Keep public as-is" per the author's standard =
        # copy the real contract verbatim (generator will build it from the source capture).
        synth_snap.setdefault("ui_contract", {}).setdefault("constraints", {})["public_info"] = \
            ((real_snap.get("ui_contract") or {}).get("constraints") or {}).get("public_info") or {}
        synth_snap["public_info_rows"] = real_snap.get("public_info_rows") or []
        tool_rows, tool_cmp = [], []
        for aid, real_v in real_tools.items():
            tv = truth_tool_value(wh, aid)
            if tv is None:
                continue
            tool_rows.append({"action_id": int(aid), "result": tv})
            match = (real_v is not None and abs(float(real_v) - float(tv)) < 0.01)
            tool_cmp.append((aid, real_v, round(tv, 3), match))
        synth_snap["action_result_rows"] = tool_rows
        synth_snap.setdefault("ui_contract", {})["actions"] = {"results": tool_rows}
        res_b = run(synth_snap, ar)
        bal_a, bal_b = res_a.get("balanced"), res_b.get("balanced")
        ga = (res_a.get("total_grid_range") or [None]*3)[1]
        gb = (res_b.get("total_grid_range") or [None]*3)[1]
        d_bal = (100.0*(bal_b-bal_a)/bal_a) if (bal_a and bal_b) else None
        tools_ok = all(m for _, _, _, m in tool_cmp) if tool_cmp else True
        rows.append(dict(map=mid_, r=ar, nseen=len(seen), nitems=len(wh.items),
                         tools=tool_cmp, tools_ok=tools_ok,
                         bal_a=bal_a, bal_b=bal_b, d_bal=round(d_bal, 1) if d_bal is not None else None,
                         grid_a=ga, grid_b=gb, tval=tval,
                         q_a={q: qmid(res_a, q) for q in ("q1", "q3", "q4", "q5", "q6")},
                         q_b={q: qmid(res_b, q) for q in ("q1", "q3", "q4", "q5", "q6")}))
        print(f"[{fi+1}/{len(files)}] map={mid_} r={ar} seen={len(seen)}/{len(wh.items)} "
              f"tools_ok={tools_ok} bal A={bal_a} B={bal_b} dBal={rows[-1]['d_bal']}% grid {ga}->{gb}", flush=True)
    print("\n==== SAME-VAULT REAL vs SYNTH-REBUILD ====")
    for x in rows:
        tl = " ".join(f"{a}:{'=' if m else f'{rv}!={tv}'}" for a, rv, tv, m in x["tools"]) or "(no tools)"
        print(f"  map={x['map']} r={x['r']} seen={x['nseen']}/{x['nitems']} | tools {tl}")
        print(f"    bal {x['bal_a']} vs {x['bal_b']} (d={x['d_bal']}%)  grid {x['grid_a']} vs {x['grid_b']}  "
              f"qmids A={x['q_a']} B={x['q_b']}")
    ok_tools = sum(1 for x in rows if x["tools_ok"])
    small = [x for x in rows if x["d_bal"] is not None and abs(x["d_bal"]) <= 10]
    print(f"\n  tool wire values exact-match: {ok_tools}/{len(rows)} samples")
    print(f"  |engine balanced diff| <= 10%: {len(small)}/{len(rows)}")


if __name__ == "__main__":
    main()
