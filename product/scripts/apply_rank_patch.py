#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在服务器上【就地订正】samples.jsonl 里排位分错值(旧版客户端选错块=slot7, 应为本机slot8)。
不重传(避去重), 直接按 session_hash 把 rank_* 字段替成本地重算的正确值。

用法(服务器上, ~/bidking 目录):
  python3 ~/apply_rank_patch.py            # 默认 patch=~/rank_patch_0626.json, samples=~/bidking/data/collected/samples.jsonl
  python3 ~/apply_rank_patch.py <patch.json> <samples.jsonl>
跑完自动备份 + 改完后【重启 collector】: sudo systemctl restart bidking-collector

覆盖范围: 只订正 patch 里有的 session(=作者本地能重算的排位局: 我方游戏 + 已知样本)。
群友自己机器上打的排位局其 session 不在 patch 里 → 不动(等他们装修复版客户端重传才会正确)。
"""
import json, os, shutil, sys, time

patch_path = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/rank_patch_0626.json")
samples_path = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser("~/bidking/data/collected/samples.jsonl")

patch = json.load(open(patch_path, encoding="utf-8"))
if not os.path.isfile(samples_path):
    sys.exit(f"找不到 samples.jsonl: {samples_path}(用第2个参数指定真实路径)")

bak = samples_path + ".bak_" + time.strftime("%Y%m%d-%H%M%S")
shutil.copy2(samples_path, bak)

lines = open(samples_path, encoding="utf-8").read().splitlines()
out, fixed, matched = [], 0, 0
for ln in lines:
    if not ln.strip():
        continue
    try:
        d = json.loads(ln)
    except Exception:
        out.append(ln)
        continue
    sh = d.get("session_hash")
    if sh in patch:
        matched += 1
        before = (d.get("rank_after"), d.get("rank_delta"))
        d.update(patch[sh])
        if (d.get("rank_after"), d.get("rank_delta")) != before:
            fixed += 1
    out.append(json.dumps(d, ensure_ascii=False))

with open(samples_path, "w", encoding="utf-8") as f:
    f.write("\n".join(out) + "\n")

print(f"patch 条目 {len(patch)}; samples 命中 {matched} 条; 实际订正 {fixed} 条。备份: {bak}")
print("下一步: sudo systemctl restart bidking-collector  (重载内存, /dashboard 排位即正确)")
