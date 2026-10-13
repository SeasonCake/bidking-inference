# -*- coding: utf-8 -*-
"""把主库里【漏网的欠计样本】补隔离进 samples_quarantine.jsonl(一次性补跑, 与 collector 判据一致)。

为什么需要它
------------
客户端的结算完整性判据【被收紧过】: `e04e259`(2026-06-23)初版是 **0.8 比例门** ——
`inventory_count < declared * 0.8` 才算残缺, 少抓不到 20% 就放行。后来才收紧成"缺一件就算"。
**但群里大量客户端仍在跑老包**, 于是"少抓 1~11 件"的欠计样本带着 `settlement_incomplete=False`
混进了主库。实测(2026-07-12, 45,693 条快照): **121 / 26,813 = 0.45%**, 且这 121 条的
captured/declared 比例 **全部落在 0.800~0.984**(= 恰好老门放行的区间, 零例外)⇒ 真欠计, 非噪声。

collector 已补上同一判据(`_declared_gt_captured`), 新样本从此不会再漏; 本脚本负责**存量**。

判据(与 collector 完全一致, 故意不另立一套)
-----------------------------------------
`total_item_count`(游戏声明的精确总件数) > `inventory_count`(实抓件数) ⇒ 欠计 ⇒ 隔离。
⚠️ **必须用 `inventory_count`, 不能用 `sum(final_quality_counts)`** —— 后者只统计【有品质】的藏品,
品质解析不出的件不在里面, 会制造假阳性(实测多报 28 条)。

安全设计(它动的是生产数据)
--------------------------
· **默认 dry-run**, 只报数不改文件; 要改必须显式 `--apply`。
· `--apply` 前**强制备份** `samples.jsonl` 到 `samples.jsonl.bak_rescreen_<ts>`。
· 写临时文件 + `os.replace` **原子替换**, 不会写坏半个库。
· **幂等**: 再跑一次不会重复隔离(被移走的行已不在主库里)。
· ⚠️ **必须先停 collector** —— 它对 samples.jsonl 是 append-only, 边写边重写 = 丢数据。
  脚本会检测文件在读取期间是否变大, 变大就中止。

用法
----
    python3 rescreen_collected_samples.py data/collected/samples.jsonl            # dry-run
    python3 rescreen_collected_samples.py data/collected/samples.jsonl --apply    # 真改(先停 collector)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from collections import Counter
from pathlib import Path


def declared_gt_captured(rec: dict) -> bool:
    """与 sample_collector_server._declared_gt_captured 同判据(不要分叉)。"""
    declared = rec.get("total_item_count")
    captured = rec.get("inventory_count")
    if not isinstance(declared, int) or not isinstance(captured, int):
        return False
    return declared > 0 and captured < declared


def main() -> int:
    ap = argparse.ArgumentParser(description="补隔离主库里漏网的欠计样本")
    ap.add_argument("main", help="主库 samples.jsonl")
    ap.add_argument("--quarantine", default=None, help="默认 = 同目录 samples_quarantine.jsonl")
    ap.add_argument("--apply", action="store_true", help="真正改动文件(默认只 dry-run)")
    args = ap.parse_args()

    main_path = Path(args.main)
    if not main_path.exists():
        print(f"[error] 主库不存在: {main_path}")
        return 2
    q_path = Path(args.quarantine) if args.quarantine else main_path.with_name("samples_quarantine.jsonl")

    size_before = main_path.stat().st_size
    keep: list[str] = []
    move: list[dict] = []
    bad = 0
    with main_path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception:  # noqa: BLE001 - 解析不了的行原样保留, 绝不丢
                bad += 1
                keep.append(line.rstrip("\n"))
                continue
            if declared_gt_captured(rec):
                move.append(rec)
            else:
                keep.append(line.rstrip("\n"))

    # collector 还在跑? append-only 会让文件在我们读的时候变大 -> 重写就会吞掉新样本。
    size_after = main_path.stat().st_size
    if size_after != size_before:
        print(f"[abort] 读取期间主库变大了({size_before} -> {size_after}) —— collector 还在写。")
        print("        先停 collector(systemctl stop <svc>)再跑, 否则会丢掉新收的样本。")
        return 3

    total = len(keep) + len(move)
    print(f"主库 {main_path}")
    print(f"  总行数        : {total}   (解析失败原样保留: {bad})")
    print(f"  ⭐漏网的欠计   : {len(move)}   ({len(move)/total*100:.2f}%)")
    print(f"  保留          : {len(keep)}")
    if move:
        vers = Counter(str(r.get("app_version")) for r in move)
        gaps = sorted(int(r["total_item_count"]) - int(r["inventory_count"]) for r in move)
        ratios = sorted(int(r["inventory_count"]) / int(r["total_item_count"]) for r in move)
        print(f"  版本分布      : {dict(vers.most_common(6))}")
        print(f"  欠计件数      : 中位 {gaps[len(gaps)//2]}  最大 {gaps[-1]}  合计丢 {sum(gaps)} 件")
        print(f"  captured/declared 比例: {ratios[0]:.3f} ~ {ratios[-1]:.3f}")
        # 自检: 老客户端的 0.8 比例门恰好放行 [0.8, 1.0) —— 若有 < 0.8 的, 说明另有成因, 值得看一眼。
        odd = [r for r in ratios if r < 0.8]
        if odd:
            print(f"  ⚠️ 有 {len(odd)} 条比例 < 0.8 —— 老的 0.8 门本该抓到它们却没抓, 另有成因, 值得单查。")

    if not args.apply:
        print()
        print("[dry-run] 未改动任何文件。确认无误后加 --apply(并先停 collector)。")
        return 0
    if not move:
        print()
        print("[apply] 没有需要隔离的行, 文件未改动。")
        return 0

    ts = time.strftime("%Y%m%d-%H%M%S")
    backup = main_path.with_name(f"{main_path.name}.bak_rescreen_{ts}")
    shutil.copy2(main_path, backup)
    print()
    print(f"[backup] {backup}  ({backup.stat().st_size} B)")

    with q_path.open("a", encoding="utf-8") as qf:
        for rec in move:
            rec = dict(rec)
            rec["_quarantine_reason"] = "declared_gt_captured_rescreen"   # 留痕: 事后可甄别/回滚
            rec["_quarantined_at"] = ts
            qf.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"[quarantine] 追加 {len(move)} 条 -> {q_path}")

    tmp = main_path.with_name(main_path.name + f".tmp_{ts}")
    with tmp.open("w", encoding="utf-8") as tf:
        for line in keep:
            tf.write(line + "\n")
    os.replace(tmp, main_path)      # 原子替换
    print(f"[main] 重写 {len(keep)} 条 -> {main_path}")

    # 写后自检: 行数必须对得上, 否则立刻喊出来(别等下游发现)
    n = sum(1 for line in main_path.open("r", encoding="utf-8", errors="replace") if line.strip())
    ok = n == len(keep)
    print(f"[verify] 主库现有 {n} 行, 期望 {len(keep)} 行 -> {'✅一致' if ok else '❌不一致! 用备份回滚'}")
    return 0 if ok else 4


if __name__ == "__main__":
    sys.exit(main())
