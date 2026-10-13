# -*- coding: utf-8 -*-
"""从运行中的 BidKing.exe 进程内存提取整张 TSV 原文(BidMap/RankMap/Language)。

背景(2026-06-25):游戏改 YooAsset 后配置表不落盘、不走系统代理。但表在内存里是
【UTF-8 的 TSV 纯文本】(TextAsset 原文,带内嵌 JSON 数组),可像老 data/raw/tables/*.txt
一样逐行解析。本工具扫可读内存,抠出包含地图/掉落签名的最大连续 TSV 文本块并存盘。

与 extract_catalog_from_memory.py 互补:那个抠"物品目录"(对象/字符串),这个抠"整表文本"。
历史工具曾把七列表误名为 DropMap；官方包和逐行对照均证明它是 RankMap。
RankMap 只描述地图级算法参数，不是 item-level Drop/DropMap 权威。

用法(游戏必须在跑、进过地图选择/对局让表载入内存):
  python scripts/extract_tables_from_memory.py --out data/raw/tables_mem_0625
仅 Windows;需读另一进程内存权限(同用户一般够,不行管理员运行)。
"""
from __future__ import annotations
import argparse
import ctypes
import ctypes.wintypes as w
import hashlib
import json
import os
import subprocess
import sys

_k = ctypes.windll.kernel32
PROCESS_VM_READ, PROCESS_QUERY_INFORMATION = 0x10, 0x400
MEM_COMMIT = 0x1000
READABLE = (0x02, 0x04, 0x20, 0x40)


class _MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", w.DWORD), ("RegionSize", ctypes.c_size_t),
                ("State", w.DWORD), ("Protect", w.DWORD), ("Type", w.DWORD)]


def _pid(name="BidKing.exe"):
    out = subprocess.run(["tasklist", "/fi", f"imagename eq {name}", "/fo", "csv", "/nh"],
                         capture_output=True, text=True).stdout
    return int(out.split('","')[1]) if name in out else None


def _regions(h):
    out, addr = [], 0
    while addr < 0x7fffffffffff:
        mbi = _MBI()
        if not _k.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            break
        if mbi.State == MEM_COMMIT and (mbi.Protect & 0xFF) in READABLE and 0 < mbi.RegionSize < 400 * 1024 * 1024:
            out.append((mbi.BaseAddress or 0, mbi.RegionSize))
        addr += mbi.RegionSize
    return out


def _rpm(h, addr, n):
    buf = ctypes.create_string_buffer(n); got = ctypes.c_size_t(0)
    if _k.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, n, ctypes.byref(got)) and got.value:
        return buf.raw[:got.value]
    return None


def _read_region(h, base, size, chunk=1 << 20):
    """分块读整个 region(跨 guard page 也尽量多读)。"""
    parts, off = [], 0
    while off < size:
        b = _rpm(h, base + off, min(chunk, size - off))
        parts.append(b if b else b"\x00" * min(chunk, size - off))
        off += chunk
    return b"".join(parts)


def _is_text(byte):
    return byte in (0x09, 0x0A, 0x0D) or 0x20 <= byte <= 0x7E or byte >= 0x80


def _expand_block(buf, pos):
    """从 pos 向两侧扩张到最大连续 TSV 文本块(遇 NUL 或连续控制字节止)。"""
    n = len(buf)
    # 向左
    lo = pos
    bad = 0
    while lo > 0:
        b = buf[lo - 1]
        if b == 0:
            break
        if not _is_text(b):
            bad += 1
            if bad >= 2:
                break
        else:
            bad = 0
        lo -= 1
    # 向右
    hi = pos
    bad = 0
    while hi < n:
        b = buf[hi]
        if b == 0:
            break
        if not _is_text(b):
            bad += 1
            if bad >= 2:
                break
        else:
            bad = 0
        hi += 1
    return buf[lo:hi]


# 表签名:(文件名, 用于定位的字节锚, 用于确认整块属于该表的子串)
# BidMap 的旧锚只有本地化 key，本地化表也会命中并因体积更大而被误选。这里把
# 名称 key 与紧邻的配置字段一起作为结构锚，并把本地化表独立输出为 Language.tsv。
SIGNS = [
    ("BidMap", b"\tbid_map_N_2101\tui_map_icon_area_01\t", b"\t[40,40,40,40,40]\t"),
    ("RankMap", b"[[11,15,1000],[16,20,1000],[21,25,50],[26,30,50]]", b"[[101,50]"),
    ("Language", b"itemName_1004\t", b"bid_map_N_2401\t"),
]


def _positive_int(value):
    return value.__class__ is int and value > 0


def _weighted_ranges(value):
    if value.__class__ is not list or not value:
        return False
    previous_high = None
    for entry in value:
        if (
            entry.__class__ is not list
            or len(entry) != 3
            or not all(_positive_int(part) for part in entry)
            or entry[0] > entry[1]
            or (previous_high is not None and entry[0] < previous_high)
        ):
            return False
        previous_high = entry[1]
    return True


def _weighted_ids(value):
    if value.__class__ is not list or not value:
        return False
    seen = set()
    for entry in value:
        if (
            entry.__class__ is not list
            or len(entry) != 2
            or not all(_positive_int(part) for part in entry)
            or entry[0] in seen
        ):
            return False
        seen.add(entry[0])
    return True


def _anchor_pairs(value):
    return (
        value.__class__ is list
        and len(value) >= 4
        and len(value) % 2 == 0
        and all(_positive_int(part) for part in value)
        and value[::2] == sorted(set(value[::2]))
    )


def _looks_like_table(name, blk):
    """Reject a text block whose row shape does not match the named table."""
    txt = blk.decode("utf-8-sig", "replace")
    rows = [row for row in txt.replace("\r", "").split("\n") if row.strip()]
    if not rows:
        return False
    if name == "BidMap":
        return all(
            len(fields := row.split("\t")) == 23
            and fields[0].lstrip("\ufeff").isascii()
            and fields[0].lstrip("\ufeff").isdigit()
            for row in rows
        )
    if name == "RankMap":
        seen = set()
        for row in rows:
            fields = row.split("\t")
            raw_id = fields[0].lstrip("\ufeff") if fields else ""
            if (
                len(fields) != 7
                or not raw_id.isascii()
                or not raw_id.isdigit()
                or str(int(raw_id)) != raw_id
                or int(raw_id) <= 0
                or int(raw_id) in seen
            ):
                return False
            try:
                range39, area_w, value_dist, col6 = (
                    json.loads(fields[index]) for index in range(3, 7)
                )
            except (TypeError, json.JSONDecodeError):
                return False
            if not (
                _weighted_ranges(range39)
                and _weighted_ids(area_w)
                and _weighted_ranges(value_dist)
                and _anchor_pairs(col6)
            ):
                return False
            seen.add(int(raw_id))
        return True
    if name == "Language":
        return any(row.startswith("itemName_") for row in rows) and any(
            row.startswith("bid_map_N_") for row in rows
        )
    return False


def extract(h, outdir):
    os.makedirs(outdir, exist_ok=True)
    regions = _regions(h)
    found = {}          # name -> sha256 -> block bytes
    for base, sz in regions:
        buf = _read_region(h, base, sz)
        for name, anchor, confirm in SIGNS:
            start = 0
            while True:
                i = buf.find(anchor, start)
                if i < 0:
                    break
                blk = _expand_block(buf, i)
                if confirm in blk and _looks_like_table(name, blk):
                    found.setdefault(name, {})[
                        hashlib.sha256(blk).hexdigest()
                    ] = blk
                start = i + len(anchor)
    selected = {}
    for name, candidates in found.items():
        if len(candidates) != 1:
            raise RuntimeError(f"conflicting complete {name} memory tables")
        selected[name] = next(iter(candidates.values()))
    targets = {
        name: os.path.join(outdir, f"{name}.tsv") for name in selected
    }
    existing = [path for path in targets.values() if os.path.exists(path)]
    if existing:
        raise FileExistsError(f"refusing to overwrite memory table output: {existing[0]}")
    results = {}
    for name, blk in selected.items():
        path = targets[name]
        with open(path, "wb") as f:
            f.write(blk)
        txt = blk.decode("utf-8", "replace")
        rows = [r for r in txt.split("\n") if r.strip()]
        results[name] = (path, len(blk), len(rows))
    return results


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/raw/tables_mem_0625")
    ap.add_argument("--process", default="BidKing.exe")
    args = ap.parse_args(argv[1:])
    pid = _pid(args.process)
    if not pid:
        print(f"{args.process} 没在运行。启动游戏->登录->进地图选择/对局让表载入->再跑本脚本。")
        return 1
    h = _k.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        print("OpenProcess 失败(可能需管理员运行)。")
        return 1
    try:
        res = extract(h, args.out)
    finally:
        _k.CloseHandle(h)
    if not res:
        print("没找到表块(表可能未载入内存:先在游戏里进地图选择/打一局)。")
        return 1
    for name, (path, nbytes, nrows) in res.items():
        print(f"{name}: {nbytes} bytes, ~{nrows} 行 -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
