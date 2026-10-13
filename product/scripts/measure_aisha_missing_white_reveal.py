# -*- coding: utf-8 -*-
"""量化 aisha "白揭示帧缺失" 样本的结算 q1 分布 —— 决定"后续品质已揭示→锁前置品质=0"修法是否安全。

背景: 5609 实测 = 0白仓时游戏不发白揭示帧(1001034 全局缺失) → 白件 q1 永远不锁(停先验)。
修法草案: aisha 逐轮顺序揭示 q1→q2→q3→q4; 若后续品质(绿1001033+)已揭示但白(1001034)缺失 → 锁 q1=0。
风险: 白帧【被丢包】(白其实>0)时也表现为"白缺失", 锁0会错。抓包无法区分 0白 vs 丢包。
本脚本: 全库扫 aisha 样本, 找"绿已揭示 但 白缺失"的, 看结算 q1 到底是 0(锁对) 还是 >0(锁错=回归)。
判据: 若这类样本结算 q1 绝大多数=0 → 修法安全(白缺失≈真0白); 若很多>0 → 丢包多, 修法有回归风险。

用法: python scripts/measure_aisha_missing_white_reveal.py [out.json] [--limit N]
过滤: 排除活动图(25xx/26xx/45xx boosted, 承 activity-is-temporary); 按 map 前两位分族只做诊断标注。
"""
from __future__ import annotations
import sys, json, glob, re, os
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT/"scripts", ROOT/"scripts/calibration", ROOT/"hero_ref/src", ROOT/"hero_ref/tools", ROOT/"src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
os.environ.setdefault("BIDKING_PROJECT_ROOT", str(ROOT))

import aisha_calib as C  # noqa: E402
from audit_aisha_gap import _map_id_from_events  # noqa: E402

AISHA_SKILL_BY_Q = {1: 1001034, 2: 1001033, 3: 1001032, 4: 1001031}  # q -> skill_id
# 活动图(boosted, 排除): 前两位 25/26/45 家族 (承 activity-is-temporary-calibrate-steady-state)
def _is_activity(map_id: int | None) -> bool:
    if map_id is None:
        return False
    s = str(map_id)
    return len(s) == 4 and s[:2] in ("25", "26", "45")


def _aisha_reveals_present(events) -> set[int]:
    """返回本局出现过的 aisha(hero103)品质揭示 skill_id 集合。"""
    got: set[int] = set()
    for st in events.states:
        for r in st.skill_reveals:
            if r.hero_id == 103:
                got.add(int(r.skill_id))
    return got


def _settled_q1(events) -> int | None:
    """结算真值 q1(白)件数; breakdown 无效则 None。counts 用品质数字键 1-6; q1 缺=0白。"""
    bd = C.settlement_breakdown(events)
    if not bd or not isinstance(bd.get("counts"), dict):
        return None
    counts = bd["counts"]
    if int(bd.get("total_items") or 0) <= 0:
        return None  # 坏/空结算, 真值不可信
    return int(counts.get(1, counts.get("1", 0)))  # 缺键=0白


def main(argv: list[str]) -> int:
    out = argv[1] if len(argv) > 1 and not argv[1].startswith("--") else None
    limit = None
    if "--limit" in argv:
        limit = int(argv[argv.index("--limit") + 1])
    files = sorted(glob.glob(str(ROOT / "data/samples/fatbeans/fatbeans_*aisha*.json")))
    if limit:
        files = files[:limit]

    cats = Counter()
    missing_white_q1: list[int] = []   # "绿已揭示 但 白缺失" 样本的结算 q1
    examples_lock_wrong: list[str] = []  # q1>0 却会被锁0 的样本(回归案例)
    activity_skipped = 0
    errors = 0
    for f in files:
        try:
            ev = C._events(Path(f))
            mp = _map_id_from_events(ev)
            if _is_activity(mp):
                activity_skipped += 1
                continue
            present = _aisha_reveals_present(ev)
            white_present = AISHA_SKILL_BY_Q[1] in present
            green_present = AISHA_SKILL_BY_Q[2] in present
            q1 = _settled_q1(ev)
        except Exception as e:  # noqa: BLE001
            errors += 1
            continue
        if white_present:
            cats["white_present"] += 1
            continue
        # white ABSENT:
        if not green_present:
            cats["white_absent_no_green"] += 1  # 早局/无后续揭示, 修法不 fire
            continue
        # white ABSENT but green present -> 修法会 fire (锁 q1=0)
        cats["white_absent_green_present_FIRES"] += 1
        if q1 is None:
            cats["fires_but_truth_unknown"] += 1
            continue
        missing_white_q1.append(q1)
        if q1 == 0:
            cats["FIRES_and_truth_q1_0_LOCK_CORRECT"] += 1
        else:
            cats["FIRES_but_truth_q1_gt0_LOCK_WRONG"] += 1
            if len(examples_lock_wrong) < 15:
                examples_lock_wrong.append(f"{Path(f).name} q1={q1}")

    q1_dist = Counter(missing_white_q1)
    result = {
        "n_files": len(files),
        "activity_skipped": activity_skipped,
        "errors": errors,
        "categories": dict(cats),
        "fires_q1_distribution": dict(sorted(q1_dist.items())),
        "lock_wrong_examples": examples_lock_wrong,
        "verdict": (
            "SAFE(白缺失≈真0白)" if (cats.get("FIRES_but_truth_q1_gt0_LOCK_WRONG", 0) == 0
                                    and cats.get("FIRES_and_truth_q1_0_LOCK_CORRECT", 0) > 0)
            else "RISK(有丢包锁错)" if cats.get("FIRES_but_truth_q1_gt0_LOCK_WRONG", 0) > 0
            else "NO_FIRE"
        ),
    }
    txt = json.dumps(result, ensure_ascii=False, indent=2)
    if out:
        Path(out).write_text(txt, encoding="utf-8")
    print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
