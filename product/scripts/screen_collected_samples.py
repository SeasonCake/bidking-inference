# -*- coding: utf-8 -*-
"""筛查 collector 聚合样本库 samples.jsonl 的有效性 + 给存储精简建议。

配套 sample_collector_server.py(它 append-only 存 data/collected/samples.jsonl, 无轮转)。
本脚本【流式逐行读】(不把整库塞进内存, 本身就是省内存的读法示范), 输出:
  · 有效性分桶(和服务端 _is_abnormal 判据一致 + 额外一致性检查)
  · 去重(session_hash)、字段完整性、按 map/hero/version/日期分布
  · 存储体量 + gzip 估算 + 精简后大小
  · 可选 --write-clean 输出【去重 + 剔异常】的干净子集(供回灌/冷存)

纯 stdlib。可在服务器直接跑:
  curl -H "X-Api-Key: <EXPORT_KEY>" http://127.0.0.1:18900/export -o samples.jsonl
  python3 screen_collected_samples.py samples.jsonl
  python3 screen_collected_samples.py samples.jsonl --write-clean clean.jsonl --gzip
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterator, Optional

# 上传字段白名单里【必须有】才算结构完整的核心字段(与 sample_upload._ALLOWED 对齐)。
_CORE_FIELDS = ("map_id", "session_hash", "final_quality_counts")


def _is_abnormal(rec: dict) -> bool:
    """与服务端 sample_collector_server._is_abnormal 完全一致的判据(保持口径统一):
    结算不完整, 或无已知价值(known_value_sum<=0/缺失/类型异常)。"""
    if rec.get("settlement_incomplete"):
        return True
    v = rec.get("known_value_sum")
    return not isinstance(v, (int, float)) or v <= 0


def _parse_quality_map(val: Any) -> Optional[dict[str, int]]:
    """容错解析品质字段: 支持 dict {"q6":2} 或字符串 "q1=10;q3=20;q6=2"。解析不了返回 None。"""
    if isinstance(val, dict):
        out: dict[str, int] = {}
        for k, v in val.items():
            try:
                out[str(k)] = int(v)
            except (TypeError, ValueError):
                return None
        return out
    if isinstance(val, str) and val.strip():
        out = {}
        for part in val.replace(",", ";").split(";"):
            part = part.strip()
            if not part:
                continue
            if "=" not in part:
                return None
            k, _, v = part.partition("=")
            try:
                out[k.strip()] = int(float(v.strip()))
            except (TypeError, ValueError):
                return None
        return out or None
    return None


def _quality_sum(val: Any) -> Optional[int]:
    m = _parse_quality_map(val)
    return sum(m.values()) if m is not None else None


def _consistency_flags(rec: dict) -> list[str]:
    """聚合字段的内部一致性(不依赖原始抓包; 只抓明显自相矛盾的坏样本)。返回命中的问题标签。"""
    flags: list[str] = []
    # 品质件数总和 vs 总件数(若都在): 应相等(公开件数是权威; 差异=漏件/坏真值线索)
    qc_sum = _quality_sum(rec.get("final_quality_counts"))
    tic = rec.get("total_item_count")
    inv = rec.get("inventory_count")
    if qc_sum is not None and isinstance(tic, (int, float)) and int(tic) > 0:
        if qc_sum != int(tic):
            flags.append("qcount_ne_total_item_count")
    # 品质占格总和 vs 总占格
    qcell_sum = _quality_sum(rec.get("final_quality_cells"))
    invc = rec.get("inventory_cells")
    if qcell_sum is not None and isinstance(invc, (int, float)) and int(invc) > 0:
        if qcell_sum > int(invc):  # 品质占格不该超过总占格(可小于: 还有未分类)
            flags.append("qcells_gt_inventory_cells")
    # 占格 < 件数(每件至少占 1 格, 物理下限)
    if qc_sum is not None and isinstance(invc, (int, float)) and int(invc) > 0:
        if int(invc) < qc_sum:
            flags.append("inventory_cells_lt_count")
    return flags


def _iter_jsonl(path: Path) -> Iterator[tuple[int, Optional[dict], Optional[str]]]:
    """流式产出 (行号, rec 或 None, 错误原因 或 None)。不整文件读入内存。"""
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:  # type: ignore[operator]
        for i, ln in enumerate(f, 1):
            ln = ln.strip()
            if not ln:
                continue
            try:
                rec = json.loads(ln)
            except Exception as exc:  # noqa: BLE001
                yield i, None, f"json_error:{type(exc).__name__}"
                continue
            if not isinstance(rec, dict):
                yield i, None, "not_a_dict"
                continue
            yield i, rec, None


def screen(path: Path, *, write_clean: Optional[Path], do_gzip: bool) -> dict:
    total = parse_err = dup = abnormal = incomplete_field = 0
    inconsistent = 0
    seen: set[str] = set()
    by_map: Counter = Counter()
    by_hero: Counter = Counter()
    by_version: Counter = Counter()
    by_day: Counter = Counter()
    flag_counts: Counter = Counter()
    total_bytes = 0
    clean_records: list[str] = []  # 只在 write_clean 时累积(否则不留内存)

    for _lineno, rec, err in _iter_jsonl(path):
        if err is not None:
            parse_err += 1
            continue
        assert rec is not None
        total += 1
        line = json.dumps(rec, ensure_ascii=False)
        total_bytes += len(line.encode("utf-8")) + 1

        # 去重(session_hash): 服务端 ingest 已去重, 这里核对导出里是否仍有重复(异常/手动合并可能引入)
        sh = rec.get("session_hash")
        is_dup = False
        if isinstance(sh, str) and sh:
            if sh in seen:
                dup += 1
                is_dup = True
            else:
                seen.add(sh)

        missing_core = [k for k in _CORE_FIELDS if not rec.get(k)]
        if missing_core:
            incomplete_field += 1

        abn = _is_abnormal(rec)
        if abn:
            abnormal += 1

        flags = _consistency_flags(rec)
        if flags:
            inconsistent += 1
            for fl in flags:
                flag_counts[fl] += 1

        by_map[str(rec.get("map_id", "?"))] += 1
        by_hero[str(rec.get("hero", "?"))] += 1
        by_version[str(rec.get("app_version") or rec.get("client_version") or "?")] += 1
        ts = rec.get("client_ts") or rec.get("received_ts")
        if isinstance(ts, str) and len(ts) >= 10:
            by_day[ts[:10]] += 1

        # 干净子集 = 未重复 + 核心字段齐 + 非异常 + 无一致性冲突
        if write_clean is not None and not (is_dup or missing_core or abn or flags):
            clean_records.append(line)

    valid = total - abnormal  # 与服务端口径一致的"主库应留"(异常已被隔离/应隔离)
    result: dict[str, Any] = {
        "total": total,
        "parse_errors": parse_err,
        "duplicates": dup,
        "abnormal": abnormal,
        "missing_core_fields": incomplete_field,
        "inconsistent": inconsistent,
        "flag_counts": dict(flag_counts.most_common()),
        "unique_sessions": len(seen),
        "valid_estimate": valid,
        "total_bytes": total_bytes,
        "avg_bytes": round(total_bytes / total) if total else 0,
        "top_maps": dict(by_map.most_common(15)),
        "n_maps": len(by_map),
        "by_hero": dict(by_hero.most_common()),
        "by_version": dict(by_version.most_common()),
        "day_range": (min(by_day) + " .. " + max(by_day)) if by_day else "-",
        "n_days": len(by_day),
    }

    if do_gzip:
        # gzip 估算: 把全部行压一遍(流式, 不落盘)测压缩率
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
            for _lineno, rec, err in _iter_jsonl(path):
                if rec is not None:
                    gz.write((json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8"))
        result["gzip_bytes"] = buf.tell()
        if total_bytes:
            result["gzip_ratio"] = round(buf.tell() / total_bytes, 3)

    if write_clean is not None:
        write_clean.write_text("\n".join(clean_records) + ("\n" if clean_records else ""),
                               encoding="utf-8")
        result["clean_written"] = len(clean_records)
        result["clean_path"] = str(write_clean)

    return result


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024  # type: ignore[assignment]
    return f"{n}B"


def main() -> int:
    ap = argparse.ArgumentParser(description="筛查 collector 聚合样本库有效性 + 存储精简建议")
    ap.add_argument("path", help="samples.jsonl(或 .jsonl.gz)路径")
    ap.add_argument("--write-clean", type=Path, default=None,
                    help="输出去重+剔异常+一致的干净子集到此路径")
    ap.add_argument("--gzip", action="store_true", help="估算 gzip 压缩后大小")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出(供程序消费)")
    args = ap.parse_args()

    p = Path(args.path)
    if not p.is_file():
        print(f"[error] 找不到文件: {p}", file=sys.stderr)
        return 2

    r = screen(p, write_clean=args.write_clean, do_gzip=args.gzip)

    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0

    print(f"=== 样本库筛查: {p} ===")
    print(f"总样本         : {r['total']}  (解析失败 {r['parse_errors']})")
    print(f"唯一 session   : {r['unique_sessions']}  (重复 {r['duplicates']})")
    print(f"异常(应隔离)   : {r['abnormal']}   [settlement_incomplete 或 known_value_sum<=0]")
    print(f"缺核心字段     : {r['missing_core_fields']}  {_CORE_FIELDS}")
    print(f"内部不一致     : {r['inconsistent']}  {r['flag_counts']}")
    print(f"有效估计(主库) : {r['valid_estimate']}")
    print(f"存储           : {_human(r['total_bytes'])}  (平均 {r['avg_bytes']} B/条)")
    if "gzip_bytes" in r:
        print(f"gzip 后        : {_human(r['gzip_bytes'])}  (压缩率 {r.get('gzip_ratio')})")
    print(f"地图           : {r['n_maps']} 张, top: {r['top_maps']}")
    print(f"英雄           : {r['by_hero']}")
    print(f"版本           : {r['by_version']}")
    print(f"日期跨度       : {r['day_range']}  ({r['n_days']} 天)")
    if "clean_written" in r:
        print(f"干净子集       : 写出 {r['clean_written']} 条 -> {r['clean_path']}")
    print()
    print("建议: 聚合库本就无原始抓包(每条几百字节), 主要精简 = 剔异常/去重 + gzip 冷存 + 加轮转;")
    print("     真占空间的通常是【原始抓包 data/samples/fatbeans/】(冷存/删可省大头), 与本库不同。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
