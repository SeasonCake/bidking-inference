"""样本池的维护入口(⚠️**未完成 · --apply 已封死** · 排 0.2.8).

╔══════════════════════════════════════════════════════════════════════════════════════╗
║ 🔴 本脚本的质量门【有巨大盲区】, 现在用它自动入池会【主动污染标定库】。--apply 已硬封。 ║
╚══════════════════════════════════════════════════════════════════════════════════════╝

## 盲区实测(2026-07-12, 这是唯一重要的事)

拿隔离区里 **31 个真实坏样本**(覆盖全部 **9 个英雄**: ahmed/aisha/ethan/gabriela/maria/raven/
sophie/tatiana/victor)喂给本脚本的 `_screen()`:

        **抓到 5 · 放过 26**   ⇒ 漏网率 **84%**, 且横跨每一个英雄。

**为什么**: 本脚本的门查的是「游戏**声明**的总件数 > 结算**实抓**件数」(欠计);
而库里 `mixed` 的判据是一条**完全正交**的物理铁律 ——

    **揭示 ⊆ 真值**:  `breakdown_total_cells < R4 实际揭示格`  ⇒ 结算真值残缺
    (实证: 2509/2507/2510 三个 mixed 样本, R4 小地图揭示 44/47/83 格,
     而结算只读出 35/32/56 格 —— **揭示 > 真值, 物理上不可能**。
     把它们当有效样本, 会把引擎的【正确】估计误判成「过估」, 虚增 red=0 villa 过估 ~+47k。)

⇒ **两个信号必须【都】接上**, 少一个就漏 84%。

🔴 **别去找 `scripts/verify_settlement_truth.py` —— 它【从未存在过】**(git 全历史查无此文件,
   但旧 README/manifest 曾在指名它)。当年那批 `mixed` 样本的判决是**手工做的**,
   判据只以【文字】形式活在 `ZZZ_INVALID_DO_NOT_USE_mixed_bad_truth/README.md` 里。
   当前严格真值门已由 `scripts/sample_truth_gate.py` 实现, registry 入口是
   `scripts/build_sample_truth_registry.py`; 本脚本的 `_screen()` 仍是旧维护路径, 不得用它代替严格门。

## 🔴 这个盲区是【怎么被抓到的】—— 比盲区本身更值得记

我原本只用**一个**坏样本做证伪门, 它**碰巧被抓到了** ⇒ 我差点据此认为门是好的。
是**作者坚持"一个不够, 要十几个、不同地图、不同英雄"**, 才把 84% 的漏网逼了出来。

**⇒ 证伪门的【多样性】和它的【存在】同等重要。** 一个样本的通过, 证明不了任何事。
   (AGENTS §6 阳性对照 / §5 "单个案例不能确立比率")

## 完成它需要做的(排 0.2.8)
1. 把「揭示 ⊆ 真值」接进 `_screen()`(读 R4 小地图揭示格 vs `breakdown_total_cells`)
2. 用**全部 373 个** mixed + **82 个** declared-mismatch 做阳性对照, **必须全抓**
3. 用 N 个已入库好样本做阴性对照, 误伤率必须极低
4. 三者都过, 才解开 `--apply`

────────────────────────────────────────────────────────────────────────────────────────

## 它本来要解决的两个病(设计仍然成立, 只是门不完整)

作者 0712 的要求:
  "如果有新增, 最好能够自动添加有效样本到调整和调参相关测试中, 就不用每次单独添加了
   (不过需要核对好, 以免把错误和无效样本也归入)"

1. **新样本进不了池子** —— `clean_eval_files()` 靠 `session_dates_*.json` 做世代过滤, 而那份清单
   一直是【手工维护】的 ⇒ 新样本的 session-id 不在里面, 永远看不见.
   **修法: 日期就在样本自己身上**(报文的 `CaptureTime`) ⇒ 清单可以【自动派生】, 不必手工维护.
   ✅已验证: 120 个抽样中, 派生日期与现有清单 **72/72 逐条吻合, 零不一致**.

2. **坏样本混进去没人拦** —— 坏真值会把【欠估读成过估(符号相反)】, 用它调参 = 朝错误方向调.
   **修法: 每个样本入池前必须过库自身的坏真值门**, 不过的进隔离区 + 写排除清单.

## 🔴 绝不能放宽的世代约束(别以为"多加样本总是好的")

池子是按日期窗口筛的: `WINDOW_LOW = 2026-06-25 12:00` = **新掉率制度**的起点.
更早的样本是【旧掉率 + 错先验】. `build_clean_eval_set.py` 的文档里有血泪:
  "裸 glob 混了不同世代 ⇒ 一次金/紫假锁 sweep 报出 5 个'回归', 其中 3 个是旧世代/无日期污染,
   限定到干净窗口后全部消失."
⇒ 本脚本**只负责把日期算对**, 窗口过滤仍然由 `clean_eval_files()` 做. **不要绕过它.**

## 三层防线(任何一层单独失效, 其余两层仍拦得住)

1. **命名**: 有效 `fatbeans_valid_*` ｜ 无效 `fatbeans_INVALID_*` (clean_eval_files 见 INVALID 即跳过)
2. **物理隔离**: 无效样本进 `ZZZ_INVALID_DO_NOT_USE_*/` 子目录 —— glob 不递归, 根本扫不到
3. **排除清单**: 同时写进 `declared_mismatch_exclude.json` —— 即使被挪回根目录、改回 valid 名字, 仍按文件名排除

## 用法(当前)
    python scripts/refresh_sample_pool.py            # 只体检并报告(可以跑)
    python scripts/refresh_sample_pool.py --apply    # 🔴【已封死】立即退出(码 2), 不会写任何东西
    python scripts/refresh_sample_pool.py --full     # 忽略缓存重新体检(慢)

`tests/test_sample_pool_integrity.py` 里两条【不依赖登记表】的不变量【现在就生效】:
排除清单里的坏样本不许漏进池子 · 隔离区的样本不许被扫进池子.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
for _p in ("scripts", "hero_ref/src", "hero_ref/tools", "src", "."):
    _f = str(ROOT / _p)
    if _f not in sys.path:
        sys.path.insert(0, _f)

from audit_aisha_gap import _map_id_from_events, _settlement_breakdown  # noqa: E402
from build_clean_eval_set import _SID  # noqa: E402
from bidking_lab.live.fatbeans import (  # noqa: E402
    live_batches_from_fatbeans_events,
    parse_fatbeans_capture,
)
from bidking_lab.live.monitor import (  # noqa: E402
    build_monitor_artifact_from_events,
    load_monitor_tables,
)

FATBEANS = ROOT / "data" / "samples" / "fatbeans"
MANIFESTS = ROOT / "data" / "sample_manifests"
POOL_MANIFEST = MANIFESTS / "sample_pool_registry.json"   # ← 守门测试读这个
EXCLUDE = MANIFESTS / "declared_mismatch_exclude.json"
QUARANTINE = FATBEANS / "ZZZ_INVALID_DO_NOT_USE_auto_screen"

_TABLES = None


def _tables():
    global _TABLES
    if _TABLES is None:
        _TABLES = load_monitor_tables()
    return _TABLES


def _capture_date(path: Path) -> str | None:
    """采集时间 = 报文里最早的 CaptureTime. 这是【样本自证】的日期, 不依赖任何手工清单.

    ✅阳性对照(120 抽样): 与现有 session_dates_20260708.json 逐条吻合 72/72, 零不一致.
    """
    try:
        rows = json.load(open(path, encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(rows, list):
        return None
    stamps = [r.get("CaptureTime") for r in rows
              if isinstance(r, dict) and r.get("CaptureTime")]
    if not stamps:
        return None
    return str(min(stamps))[:19].replace("/", "-")


def _hero_of(events) -> str | None:
    """英雄来自 FieldUpdate(path=("session","hero")).
    ⚠️不是 batch 的属性 —— 第一版写成 getattr(batch,"hero") 把 6098/6186 个候选全判成"无英雄",
      工具报出"0 个新样本"的假结论. 报【零】之前先怀疑工具."""
    try:
        for b in live_batches_from_fatbeans_events(events):
            for fu in (b.field_updates or ()):
                if tuple(getattr(fu, "path", ())) == ("session", "hero") and fu.value:
                    return str(fu.value).strip().lower()
    except Exception:
        pass
    return None


def _screen(path: Path) -> dict:
    """库自身的质量门. 返回 {verdict, reason, hero, map_id, date, ...}"""
    out: dict = {"file": path.name}
    try:
        events = parse_fatbeans_capture(path)
    except Exception as exc:
        return {**out, "verdict": "INVALID", "reason": f"parse_failed:{type(exc).__name__}"}

    out["hero"] = _hero_of(events)
    out["map_id"] = _map_id_from_events(events)
    out["date"] = _capture_date(path)
    if not out["hero"]:
        return {**out, "verdict": "INVALID", "reason": "no_hero"}
    if not out["map_id"]:
        return {**out, "verdict": "INVALID", "reason": "no_map_id"}
    if not out["date"]:
        return {**out, "verdict": "INVALID", "reason": "no_capture_date"}

    st = _settlement_breakdown(events)
    if not st:
        return {**out, "verdict": "INVALID", "reason": "no_settlement_truth"}
    counts = sum(int(v) for v in (st.get("counts") or {}).values())
    cells = sum(int(v) for v in (st.get("cells") or {}).values())
    value = float(st.get("total_value") or 0.0)
    out.update(settled_counts=counts, settled_cells=cells, settled_value=value)
    if counts <= 0 or cells <= 0 or value <= 0:
        return {**out, "verdict": "INVALID",
                "reason": f"abnormal:count{counts}/cells{cells}/value{value:.0f}"}

    # ---- 坏真值门 ----
    try:
        art = build_monitor_artifact_from_events(events, tables=_tables())
    except Exception:
        return {**out, "verdict": "valid", "reason": None}  # 判不了就别拦(与老库口径一致)
    d = art if isinstance(art, dict) else (art.as_dict() if hasattr(art, "as_dict") else vars(art))
    if d.get("settlement_incomplete"):
        return {**out, "verdict": "INVALID", "reason": "settlement_incomplete"}
    if d.get("catalog_value_complete") is False:
        return {**out, "verdict": "INVALID", "reason": "catalog_value_incomplete"}
    tic = ((d.get("inference_input_constraints") or {}).get("total_item_count")) or {}
    if tic.get("confidence") == "exact":
        # 🔴实抓件数必须用 Σ_settlement_breakdown[counts].
        #   artifact 的 `inventory_count` 在【离线】样本上被【声明值回填】(实测: 声明42 -> inventory_count 42,
        #   而真实实抓只有 28) ⇒ 拿它做门 = 一道【永远判不出欠计】的假门.
        #   (记忆 fatbeans-mixed-samples-bad-truth 里"要用 inventory_count"是【云端上传件】的口径.)
        try:
            declared = int(tic["value"])
        except (TypeError, ValueError, KeyError):
            declared = None
        if declared is not None and declared > counts:
            return {**out, "verdict": "INVALID",
                    "reason": f"undercount:declared{declared}>captured{counts}"}
    return {**out, "verdict": "valid", "reason": None}


def _fingerprint(p: Path) -> str:
    stat = p.stat()
    return hashlib.md5(f"{p.name}|{stat.st_size}".encode()).hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="(已封死) 真的写入")
    ap.add_argument("--full", action="store_true", help="忽略缓存, 重新体检每一个样本")
    args = ap.parse_args()

    # ⚠️封锁必须在【最前面】—— 放在体检之后的话, 得先扫 1900+ 个样本才拒绝, 用户等半天还以为在干活.
    if args.apply:
        print("=" * 78)
        print("🔴 --apply 已【硬封】—— 本脚本的质量门有 84% 漏网率,")
        print("   现在用它自动入池会【主动污染标定库】。")
        print("=" * 78)
        print("实测(31 个真实坏样本 · 覆盖全部 9 个英雄): 抓到 5 · 放过 26。")
        print()
        print("漏掉的是【揭示 ⊆ 真值】那条物理铁律:")
        print("    breakdown_total_cells < R4 实际揭示格   ⇒   结算真值残缺")
        print("它与本脚本查的「声明件数 > 实抓件数」是【两个正交信号】—— 少一个就漏 84%。")
        print()
        print("解封条件(排 0.2.8,见文件头):")
        print("  ① 把「揭示 ⊆ 真值」接进 _screen()(走 monitor._minimap_grid_items)")
        print("  ② 全部 373 个 mixed + 82 个 declared-mismatch 做阳性对照,**必须全抓**")
        print("  ③ 已入库好样本做阴性对照,误伤率极低")
        print()
        print("在那之前:**样本入库保持手动**(作者 0712 定)。")
        print()
        print("(想看体检报告 / 不写入: 去掉 --apply 即可)")
        return 2

    cache: dict[str, dict] = {}
    if POOL_MANIFEST.exists() and not args.full:
        try:
            cache = {r["file"]: r for r in json.loads(POOL_MANIFEST.read_text(encoding="utf-8"))["samples"]}
        except Exception:
            cache = {}

    files = sorted(FATBEANS.glob("*.json"))
    print(f"体检 {len(files)} 个样本 ({'全量' if args.full else '增量(缓存 %d 条)' % len(cache)})")

    records: list[dict] = []
    n_cached = 0
    for i, p in enumerate(files, 1):
        if i % 500 == 0:
            print(f"  {i}/{len(files)} ...", flush=True)
        fp = _fingerprint(p)
        old = cache.get(p.name)
        if old and old.get("fingerprint") == fp:
            records.append(old)
            n_cached += 1
            continue
        rec = _screen(p)
        rec["fingerprint"] = fp
        records.append(rec)

    valid = [r for r in records if r["verdict"] == "valid"]
    invalid = [r for r in records if r["verdict"] != "valid"]
    print(f"\n  ✅ 有效 {len(valid)}   🔒 无效 {len(invalid)}   (缓存命中 {n_cached})")
    from collections import Counter
    for reason, c in Counter(r["reason"] for r in invalid).most_common(8):
        print(f"       {reason}: {c}")

    # ---- 阳性对照: 派生日期必须与【现有清单】一致, 否则我的派生是错的, 整个重建不能上 ----
    from build_clean_eval_set import _load_session_dates
    sd_old = _load_session_dates()
    hit = bad = 0
    for r in valid:
        m = _SID.search(r["file"])
        if not m or m.group(1) not in sd_old:
            continue
        if str(sd_old[m.group(1)])[:19] == str(r["date"])[:19]:
            hit += 1
        else:
            bad += 1
    print(f"\n  阳性对照(派生日期 vs 现有 session_dates): 吻合 {hit}  不一致 {bad}")
    if bad > 0:
        print("  🔴 派生日期与现有清单不一致 —— 我的派生有问题, 拒绝重建. 先查清楚.")
        return 1

