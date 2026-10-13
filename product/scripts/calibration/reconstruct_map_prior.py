# -*- coding: utf-8 -*-
"""备份方案:从【结算样本】经验反推一张图的爆率派生先验(不依赖拿不到的 Drop 权重表)。

背景(2026-06-23):06-18 YooAsset 后游戏把客户端静态表删了,Drop 权重在服务器、拿不到;
mitmproxy/注入都拿不到权重(服务器从不下发)。但权重的唯一用途=每图每品质的「期望价值/
均格/件数分布/长尾」,这些可以从【每局结算揭示整仓所有件】统计反推出来——结算样本越多越准。

本工具:
  输入  一批 fatbeans 结算样本(raw json / HeroRefDiag 导出目录),按 map_id 分组
  Part A  从 wire 揭示(skill_reveals + public_infos 的 observed_items,带 item_id+value+shape+cells)
          收割 item_id -> {value,shape,cells,quality},补足旧 Item.txt 没有的新物品
  Part B  对每图聚合结算 inventory:每品质 件数分布 / 均格 / 加权均价 / 物品经验频率(=相对权重)
  输出  data/processed/reconstructed_map_prior_<map>.json(+ 收割的新物品目录补丁)

价值来源优先级:wire 收割目录(新物品在这) > Item.txt(旧物品)。结算 inventory_items 本身不带
value(协议只发 id/品质/占格),所以新物品的 value 必须靠 wire 揭示收割——这就是为什么 25 号要
「打几局新内容抓对局包」。

用法:
  python scripts/calibration/reconstruct_map_prior.py --samples "data/samples/fatbeans/*valid_ahmed_2501*" --out-dir data/processed
  python scripts/calibration/reconstruct_map_prior.py --samples "C:/path/to/HeroRefDiag-*" --map 2531
只读样本,不改引擎。"""
from __future__ import annotations
import argparse, glob, io, json, re, sys
from collections import defaultdict
from pathlib import Path
from statistics import median, mean

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT/"src", ROOT/"scripts", ROOT/"scripts/calibration"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from bidking_lab.live.fatbeans import parse_fatbeans_capture  # noqa: E402
from bidking_lab.live.monitor import load_monitor_tables  # noqa: E402

QKEYS = {1: "q1", 2: "q1", 3: "q3", 4: "q4", 5: "q5", 6: "q6"}  # q2(绿) 并入 q1, 与引擎一致


def _quantiles(xs):
    xs = sorted(xs)
    if not xs:
        return {}
    def q(p):
        i = max(0, min(len(xs) - 1, int(round(p * (len(xs) - 1)))))
        return xs[i]
    return {"n": len(xs), "mean": round(mean(xs), 2), "p10": q(0.10), "p50": q(0.50), "p90": q(0.90),
            "min": xs[0], "max": xs[-1]}


def _iter_sample_paths(patterns):
    for pat in patterns:
        for hit in glob.glob(pat):
            p = Path(hit)
            if p.is_dir():
                # HeroRefDiag 导出目录: 用 raw/npcap_live.json
                raw = p / "raw" / "npcap_live.json"
                if raw.exists():
                    yield raw
            elif p.suffix == ".json":
                yield p


def _map_id_from_events(ev, path):
    for st in ev.states:
        if getattr(st, "map_id", None):
            return int(st.map_id)
    m = re.search(r"_(\d{4})_", Path(path).name)
    return int(m.group(1)) if m else None


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", nargs="+", default=None, help="glob(s) of fatbeans json / HeroRefDiag dirs")
    ap.add_argument("--sessions", default=None,
                    help="data/logs/live/sessions.jsonl(自动收集流;每结算帧=一个样本,免手动导出)")
    ap.add_argument("--map", type=int, default=None, help="只处理这个 map_id(默认按样本自动分组)")
    # Default writes to the OLD-era home (the existing reconstructions are all pre-2026-06-25-noon
    # OLD-rate). ⚠️ This generator aggregates whatever --samples/--sessions you give it; the live
    # sample pool is now ERA-MIXED (old-rate pre-noon + new-rate post-noon). For a NEW-era prior pass
    # an EXPLICIT --out-dir AND era-filter the samples via data/sample_manifests/era_partition_noon_
    # 2026-06-27.json — do NOT blend eras into one prior. See HANDOFF §8.4-era.
    ap.add_argument("--out-dir",
                    default=str(ROOT / "data" / "processed" / "reconstructed_prior_OLD_only_pre_20260625_1200"))
    args = ap.parse_args(argv[1:])
    if not args.samples and not args.sessions:
        ap.error("需要 --samples 或 --sessions 之一")

    tables = load_monitor_tables()
    item_catalog = {it.item_id: {"value": it.value, "quality": it.quality, "cells": (it.shape_w * it.shape_h)
                                 if getattr(it, "shape_w", 0) else None, "name": it.name, "source": "item_txt"}
                    for it in tables.items.values()}
    wire_harvest = {}  # item_id -> {value,shape_code,cells,quality,source:wire}

    by_map_sessions = defaultdict(list)  # map -> list of per-session dict
    out = io.StringIO()
    def log(*a): print(*a, file=out)
    errs = 0

    # 入口 1: sessions.jsonl 自动收集流(每结算帧=一个样本,无需手动导出)
    if args.sessions:
        def _qmap(s):  # "q1=2;q2=8;q3=10" -> {"q1":..} (q2 绿并入 q1)
            out_d = defaultdict(int)
            for part in str(s or "").split(";"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    k = "q1" if k.strip() == "q2" else k.strip()
                    try:
                        out_d[k] += int(float(v))
                    except ValueError:
                        pass
            return out_d
        n_lines = n_settled = n_drop = 0
        seen_sess = set()
        for ln in Path(args.sessions).read_text(encoding="utf-8").splitlines():
            n_lines += 1
            try:
                d = json.loads(ln)
            except Exception:  # noqa: BLE001
                continue
            if d.get("phase") not in (None, "settled"):  # 收集服务器裁剪记录无 phase(本就全结算)
                continue
            if d.get("settlement_incomplete"):  # 4409 型残缺样本,自动剔除
                n_drop += 1; continue
            sid = d.get("session_id") or d.get("session_hash")  # 兼容 sessions.jsonl 与收集 samples.jsonl
            if sid in seen_sess:  # 同一局可能多帧,去重
                continue
            seen_sess.add(sid)
            mp = d.get("map_id")
            if args.map is not None and mp != args.map:
                continue
            counts, cells, values = _qmap(d.get("final_quality_counts")), _qmap(d.get("final_quality_cells")), _qmap(d.get("final_quality_values"))
            sess = {"counts": defaultdict(int), "cells": defaultdict(int), "items": defaultdict(int), "values": defaultdict(list)}
            for q in ("q1", "q3", "q4", "q5", "q6"):
                c = counts.get(q, 0)
                sess["counts"][q] = c
                sess["cells"][q] = cells.get(q, 0)
                if c > 0 and values.get(q):
                    sess["values"][q] = [values[q] / c]  # 该局该品质均价(sessions.jsonl 无逐件价, 用局均)
            n_settled += 1
            by_map_sessions[mp].append(sess)
        log(f"sessions.jsonl: {n_lines} 帧 -> {n_settled} 个结算样本(去重后), 剔除残缺 {n_drop}")
        # 跳过 fatbeans 样本路径
        return _emit(args, by_map_sessions, wire_harvest, item_catalog, out, log)

    paths = list(_iter_sample_paths(args.samples or []))
    log(f"样本文件数: {len(paths)}")
    for path in paths:
        try:
            ev = parse_fatbeans_capture(path)
        except Exception as e:  # noqa: BLE001
            errs += 1; continue
        mp = _map_id_from_events(ev, path)
        if args.map is not None and mp != args.map:
            continue
        # Part A: 收割 wire 揭示物品属性
        for st in ev.states:
            for grp in (getattr(st, "skill_reveals", ()) or (), getattr(st, "public_infos", ()) or ()):
                for sr in grp:
                    for oi in (getattr(sr, "observed_items", ()) or ()):
                        iid = getattr(oi, "item_id", None)
                        val = getattr(oi, "value", None)
                        if iid and val:
                            wire_harvest[int(iid)] = {
                                "value": int(val), "shape_code": getattr(oi, "shape_code", None),
                                "cells": getattr(oi, "cells", None), "quality": getattr(oi, "quality", None),
                                "source": "wire"}
        # Part B: 结算 inventory(取最后一个有 inventory 的 state)
        settled = None
        for st in reversed(ev.states):
            if getattr(st, "inventory_items", None):
                settled = st; break
        if settled is None:
            continue
        sess = {"counts": defaultdict(int), "cells": defaultdict(int), "items": defaultdict(int), "values": defaultdict(list)}
        for inv in settled.inventory_items:
            q = QKEYS.get(int(inv.quality) if inv.quality is not None else 0)
            if q is None:
                continue
            sess["counts"][q] += 1
            sess["cells"][q] += int(getattr(inv, "cells", 0) or 0)
            sess["items"][int(inv.item_id)] += 1
            v = wire_harvest.get(int(inv.item_id), {}).get("value") or item_catalog.get(int(inv.item_id), {}).get("value")
            if v:
                sess["values"][q].append(int(v))
        by_map_sessions[mp].append(sess)

    log(f"解析失败: {errs}  涉及 map: {sorted(k for k in by_map_sessions if k)}")
    return _emit(args, by_map_sessions, wire_harvest, item_catalog, out, log)


def _emit(args, by_map_sessions, wire_harvest, item_catalog, out, log):
    Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    for mp, sessions in by_map_sessions.items():
        if not mp or not sessions:
            continue
        prior = {"map_id": mp, "n_sessions": len(sessions), "schema": "reconstructed_map_prior_v0",
                 "per_quality": {}, "item_empirical_weight": {}}
        for q in ("q1", "q3", "q4", "q5", "q6"):
            counts = [s["counts"].get(q, 0) for s in sessions]
            cells_per_item, values = [], []
            for s in sessions:
                c, n = s["cells"].get(q, 0), s["counts"].get(q, 0)
                if n > 0:
                    cells_per_item.append(c / n)
                values += s["values"].get(q, [])
            prior["per_quality"][q] = {
                "count": _quantiles(counts),
                "cells_per_item": _quantiles(cells_per_item),
                "value_per_item": _quantiles(values),
                "value_coverage": round(len(values) / max(1, sum(counts)), 2),  # 有价占比(新物品缺价会低)
            }
        # 物品经验频率 = 出现局数 / 总局数(相对权重)。sessions.jsonl 无逐件 id,此项仅 fatbeans/diag 路径有
        item_freq = defaultdict(int)
        for s in sessions:
            for iid in s["items"]:
                item_freq[iid] += 1
        prior["item_empirical_weight"] = {
            str(iid): {"freq": round(n / len(sessions), 4), "in_n_sessions": n,
                       "value": wire_harvest.get(iid, {}).get("value") or item_catalog.get(iid, {}).get("value"),
                       "known": iid in item_catalog, "wire_harvested": iid in wire_harvest}
            for iid, n in sorted(item_freq.items(), key=lambda x: -x[1])}
        op = Path(args.out_dir) / f"reconstructed_map_prior_{mp}.json"
        op.write_text(json.dumps(prior, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"map {mp}: {len(sessions)} 局 -> {op.name}")
        for q in ("q1", "q3", "q4", "q5", "q6"):
            pq = prior["per_quality"][q]
            log(f"   {q}: 件数{pq['count'].get('mean')}(p10-90 {pq['count'].get('p10')}-{pq['count'].get('p90')}) "
                f"均格{pq['cells_per_item'].get('mean')} 均价{pq['value_per_item'].get('mean')} 有价{pq['value_coverage']}")
    new_items = {str(i): v for i, v in wire_harvest.items() if i not in item_catalog}
    if new_items:
        patch = Path(args.out_dir) / "wire_harvested_item_catalog.json"
        patch.write_text(json.dumps(new_items, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"\nwire 收割到 Item.txt 没有的新物品: {len(new_items)} -> {patch.name}")
    print(out.getvalue())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
