"""3 端到端测试场景 — 含均格 + 巨物，便于在 Streamlit UI 里对照验证。

每个场景打印：
  - 输入摘要（可以照着填 UI）
  - 联合推断 top-3（实验性 tab）
  - 价值分布 p25/p50/p75（出价 tab 的图同源）
  - 秒仓 / 放仓建议（如果 gate 触发）

跑法：
    python scripts/demo_scenarios.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

from bidking_lab.extract.bid_map_table import load_bid_map_table
from bidking_lab.extract.drop_table import load_drop_table
from bidking_lab.extract.item_table import load_item_table
from bidking_lab.inference.display import parse_reading
from bidking_lab.inference.ground_truth import sample_session_truth
from bidking_lab.inference.joint import joint_top_k_for_session
from bidking_lab.inference.observation import QualityBucketObs, SessionObs
from bidking_lab.inference.snipe import (
    compute_pass_recommendation,
    compute_snipe_recommendation,
)

REPO = Path(__file__).resolve().parent.parent
TABLES = REPO / "data" / "raw" / "tables"
QLABEL = {1: "白品", 2: "绿品", 3: "蓝品", 4: "紫品", 5: "金品", 6: "红品"}


def banner(s: str) -> None:
    print("\n" + "=" * 78)
    print(s)
    print("=" * 78)


def print_session_summary(name: str, map_id: int, hero: str, wh: int,
                           session: SessionObs) -> None:
    extra = ""
    if session.total_item_count is not None:
        extra = f"  ·  总藏品 {session.total_item_count} 件"
    print(f"\n[场景 {name}]  地图 {map_id}  ·  英雄 {hero}  ·  仓库 {wh} 格{extra}")
    for q in (1, 2, 3, 4, 5, 6):
        b = session.buckets.get(q)
        if b is None:
            continue
        parts = []
        if b.total_cells is not None:
            parts.append(f"总格 {b.total_cells}")
        if b.count is not None:
            parts.append(f"件数 {b.count}")
        if b.avg_cells is not None:
            tz = "(尾零)" if b.avg_cells.trailing_zero else ""
            parts.append(f"均格 {b.avg_cells.raw}{tz}")
        if b.value_sum is not None:
            parts.append(f"总价 {b.value_sum:,}")
        if b.huge_band != "none":
            parts.append(f"巨物 {b.huge_band}")
        if b.value_range:
            parts.append(f"红区间 {b.value_range[0]:,}–{b.value_range[1]:,}")
        print(f"   q={q} {QLABEL[q]:<3}  " + " · ".join(parts))


def run_joint(session: SessionObs, k: int = 3) -> None:
    print("\n  ▶ 联合推断 top-3")
    hyps = joint_top_k_for_session(session, k=k, per_bucket_top=10)
    if not hyps:
        print("    （无候选 — 输入约束过紧）")
        return
    for i, h in enumerate(hyps, 1):
        per = []
        for q in sorted(h.per_bucket):
            c = h.per_bucket[q]
            per.append(f"q={q} {c.total_cells}格/{c.count}件")
        print(
            f"    #{i}  composite={h.composite:.3f}  "
            f"总格={h.total_cells}  超容罚={h.warehouse_penalty:.2f}  "
            f"| {' · '.join(per)}"
        )


def run_bidding(session: SessionObs, *, maps, drops, items,
                n_trials: int, seed: int) -> None:
    print(f"\n  ▶ MC 价值分布 (n_trials={n_trials})")
    rng = np.random.default_rng(seed)
    truths = [
        sample_session_truth(session.map_id, maps=maps, drops=drops,
                              items=items, rng=rng)
        for _ in range(n_trials)
    ]

    wh = session.warehouse_total_cells
    matched = [t.total_value() for t in truths
               if abs(t.warehouse_total_cells - wh) <= 8]
    if matched:
        arr = np.asarray(matched, dtype=np.int64)
        print(
            f"    匹配 {len(matched)}/{n_trials} 个样本  "
            f"p25={int(np.percentile(arr, 25)):,}  "
            f"p50={int(np.percentile(arr, 50)):,}  "
            f"p75={int(np.percentile(arr, 75)):,}"
        )
    else:
        print(f"    匹配 0/{n_trials} — 该仓库格数采样到的概率太低")

    snipe = compute_snipe_recommendation(
        session, maps=maps, drops=drops, items=items, truths=truths,
    )
    if snipe is None:
        print("    秒仓建议：—（gate 未触发）")
    else:
        conf_tag = " ⚠️低置信" if snipe.low_confidence else ""
        print(
            f"    🎯 秒仓推荐  起步 {snipe.safe_floor_bid:,} → 顶价 "
            f"{snipe.snipe_max_bid:,}  (P50={snipe.expected_value:,}, "
            f"P75={snipe.p75_value:,}, 样本 {snipe.n_matching_samples}{conf_tag})"
        )

    pass_rec = compute_pass_recommendation(
        session, maps=maps, drops=drops, items=items, truths=truths,
    )
    if pass_rec is None:
        print("    放仓建议：—（gate 未触发）")
    else:
        conf_tag = " ⚠️低置信" if pass_rec.low_confidence else ""
        print(
            f"    🚫 放仓上限  超过 {pass_rec.pass_max_bid:,} 就放  "
            f"(进仓 P25={pass_rec.safe_entry_bid:,}, "
            f"全图均值={pass_rec.unconditional_p50:,}, "
            f"本仓只是 {pass_rec.value_ratio:.0%}{conf_tag})"
        )


def main() -> int:
    print("加载游戏表 ...", flush=True)
    maps = load_bid_map_table(TABLES / "BidMap.txt")
    drops = load_drop_table(TABLES / "Drop.txt")
    items = load_item_table(TABLES / "Item.txt")

    # ========================================================================
    # 场景 A：伊森 · 沉船大仓 · 良品扫描见蓝 + 紫品均格 2.90（含尾零）+ 1 红巨物
    # ========================================================================
    banner("场景 A：伊森 · 沉船 2510 大仓 · 紫均格小数尾零泄露 + 1 红巨物")
    print("""
适用：伊森第 2 轮拿到 普品/良品/优品均格 三件套读数；至宝寻踪扫到 1 个红巨物。
紫品均格游戏内显示 2.90（注意尾零 → 截断后真值在 [2.90, 2.91)，意味着
total_cells / count 很可能是 32/11 而不是 29/10）。
""".strip())

    session_A = SessionObs(
        hero="ethan",
        map_id=2510,
        warehouse_total_cells=145,
        buckets={
            1: QualityBucketObs(quality=1, total_cells=22),    # 普品扫描 W+G 合并
            3: QualityBucketObs(quality=3, total_cells=20),    # 良品扫描蓝
            4: QualityBucketObs(quality=4, avg_cells=parse_reading("2.90")),  # 优品均格 - 尾零
            6: QualityBucketObs(quality=6, huge_band="1"),     # 至宝寻踪 见 1 红巨物
        },
    )
    print_session_summary("A", 2510, "ethan", 145, session_A)
    run_joint(session_A)
    # 提高 n_trials 因为 145 格在 2510 是较稀的抽样位置
    run_bidding(session_A, maps=maps, drops=drops, items=items,
                 n_trials=3000, seed=20260515)

    # ========================================================================
    # 场景 B：伊森 · 沉船中小仓 · 紫均格整数（4.00）+ 无巨物 → 应触发放仓
    # ========================================================================
    banner("场景 B：伊森 · 沉船 2510 中小仓 · 紫均格整数 4.00 + 无巨物")
    print("""
适用：仓库估计 95 格，白绿格数高、蓝紫一般、无任何巨物。紫品均格 = 4.00
意味着 total_cells / count 是整数 4，紫品占比也低 → 仓库价值上限有限。
预期：放仓 gate 触发，给出 walk_away_price。
""".strip())

    session_B = SessionObs(
        hero="ethan",
        map_id=2510,
        warehouse_total_cells=95,
        buckets={
            1: QualityBucketObs(quality=1, total_cells=28),
            3: QualityBucketObs(quality=3, total_cells=14),
            4: QualityBucketObs(quality=4,
                                 avg_cells=parse_reading("4"),  # 整数 (精确，无尾零)
                                 total_cells=12),
            5: QualityBucketObs(quality=5, huge_band="none"),
            6: QualityBucketObs(quality=6, huge_band="none"),
        },
    )
    print_session_summary("B", 2510, "ethan", 95, session_B)
    run_joint(session_B)
    run_bidding(session_B, maps=maps, drops=drops, items=items,
                 n_trials=1000, seed=20260515)

    # ========================================================================
    # 场景 C：艾莎 · 别墅大仓 · 4 轮轮廓 + 优品估价 + 紫巨物 2-3
    # ========================================================================
    banner("场景 C：艾莎 · 别墅 2407 · R1-R4 轮廓全开 + 优品估价 + 紫巨物 2-3")
    print("""
适用：艾莎挂机到 R4，逐轮拿到 白/绿/蓝/紫 轮廓 → 总格数已经全部确定。
R4 用优品估价给出 89_400（紫品总估价）。R4 还能看到紫品巨物有 2-3 个。
预期：联合推断的紫品 bucket 应被 (cells × count × value × huge) 多重收紧。

提示：48/8 = 6.0 精确整数，游戏会显示 "6"（不会显示 "6.00"）。
若你只看到 "6" 没尾零，可以填整数 6；这里直接用 cells+count 锁定，更稳。
""".strip())

    session_C = SessionObs(
        hero="aisha",
        map_id=2407,
        warehouse_total_cells=128,
        total_item_count=35,                                   # 地图 hint
        buckets={
            1: QualityBucketObs(quality=1, total_cells=24, count=12),  # R1 轮廓 → 数件数
            2: QualityBucketObs(quality=2, total_cells=18, count=7),
            3: QualityBucketObs(quality=3, total_cells=22, count=5),
            4: QualityBucketObs(
                quality=4,
                total_cells=48,
                count=8,            # 紫轮廓数出 8 件
                value_sum=89400,
                huge_band="2-3",    # 巨物 2-3
            ),
        },
    )
    print_session_summary("C", 2407, "aisha", 128, session_C)
    run_joint(session_C)
    run_bidding(session_C, maps=maps, drops=drops, items=items,
                 n_trials=1000, seed=20260515)

    print("\n" + "=" * 78)
    print("完成。把上述输入照搬到 Streamlit UI 应该得到一致输出（±MC 随机）。")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
