"""通道审计: 确认合成样本的【技能 / 道具 / 公开信息】每条关键数据都被引擎消费(不是像 quality_cells
那样喂了却没进引擎)。对一个真实艾莎仓的合成快照, 打印:
  输入通道: skill_reveal_rows / action_result_rows(道具,含 T3 价值) / public_info_rows / field_updates
  引擎证据: fixed/min counts, quality_cells/floors, 各类价值(avg_values/quality_values/value_floors/
            random_value_floors), source_notes(消费痕迹)
并逐项判定: 每个道具揭示的件/价值、每条公开信息, 是否在引擎证据里留下痕迹。

用法: python scripts/calibration/audit_synth_channels.py [源文件名子串] [round]
"""
from __future__ import annotations

import glob
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _sub in ("scripts", "scripts/calibration", "hero_ref/src", "src"):
    p = str(ROOT / _sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine  # noqa: E402
from synthesize_draw_hero_samples import load_warehouse, synthesize_snapshot, reveal_trace, TOOL_LABEL  # noqa: E402

DIRS = ["data/samples/group_0625_56xx", "data/samples/group_post0625_newrate_classic", "data/samples/fatbeans"]


def _find(sub):
    for d in DIRS:
        for f in glob.glob(str(ROOT / d / "*.json")):
            if sub in Path(f).name:
                return Path(f)
    return None


def main(argv):
    sub = argv[1] if len(argv) > 1 else "2026-06-26_214029_5607"
    rnd = int(argv[2]) if len(argv) > 2 else 4
    f = _find(sub)
    wh = load_warehouse(f, with_geometry=True)
    snap = synthesize_snapshot(wh, "aisha", rnd, random.Random(0))
    revealed, log = reveal_trace(wh, "aisha", rnd, random.Random(0))
    res = run_reference_engine(prepare_reference_engine_snapshot(snap), max_combos=6000).as_dict()
    ev = res.get("evidence", {}) or {}

    print(f"源: {f.name} | map {wh.map_id} R{rnd} | 真值 {wh.truth_counts} 总值 {wh.truth_total_value:,}")

    print("\n========== 输入通道 ==========")
    print(f"[skill_reveal_rows] {len(snap.get('skill_reveal_rows') or [])} 行")
    for r in (snap.get("skill_reveal_rows") or [])[:3]:
        det = r.get("revealed_items_detail") or []
        print(f"   skill_id={r.get('skill_id')} ×{len(det)} 例:{det[:2]}")
    print(f"[action_result_rows 道具] {len(snap.get('action_result_rows') or [])} 行")
    for r in (snap.get("action_result_rows") or []):
        det = r.get("revealed_items_detail") or []
        hasval = [d for d in det if d.get("value") is not None]
        print(f"   {TOOL_LABEL.get(r.get('tool'), r.get('tool'))}(action={r.get('action_id')}) ×{len(det)} "
              f"带价值{len(hasval)}件 例:{det[:2]}")
    print(f"[public_info_rows 公开] {len(snap.get('public_info_rows') or [])} 行")
    for r in (snap.get("public_info_rows") or []):
        print(f"   info_id={r.get('info_id')} {r.get('label')} value={r.get('value')} gran={r.get('gran')} revealed={r.get('revealed')}")
    print(f"[field_updates 结构化锁] {(snap.get('structured_ref_inputs') or {}).get('field_updates')}")

    print("\n========== 引擎证据(消费结果) ==========")
    for k in ("total_count", "fixed_counts", "min_counts", "split_counts", "quality_cells",
              "quality_cell_floors", "avg_cells", "avg_values", "quality_values",
              "quality_value_floors", "quality_value_floor_item_counts", "random_value_floors",
              "count_sums", "total_grid_target"):
        v = ev.get(k)
        if v:
            print(f"   {k} = {v}")
    notes = ev.get("source_notes") or []
    print(f"\n[source_notes 消费痕迹] {len(notes)} 条:")
    for n in notes:
        print(f"   - {n}")

    print("\n========== 逐通道判定 ==========")
    # tools: each tool's reveals -> did min_counts / value floors move?
    tool_vals = []
    for entry in log:
        for e in entry.get("events") or []:
            if e.get("kind") == "tool" and e.get("tool") not in (None, "none"):
                tool_vals.append((e.get("tool"), len(e.get("items") or []), e.get("gran"), e.get("q5_cells_total")))
    print(f"道具揭示: {tool_vals}")
    has_value_floor = bool(ev.get("quality_value_floors") or ev.get("random_value_floors"))
    print(f"  -> 引擎是否有价值floor(抽检/估价价值应进这里): {'有' if has_value_floor else '❌无(抽检价值没被消费!)'}")
    pub_consumed = [n for n in notes if "public" in n or "tier_prob" in n or "drop_table" in n or "avg" in n]
    print(f"  -> 公开信息消费痕迹: {pub_consumed if pub_consumed else '❌无'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
