# -*- coding: utf-8 -*-
"""从运行中的 BidKing.exe 进程内存提取藏品目录(item_id -> 价值/品质/中文名)。

背景(2026-06-25 攻关结论):游戏改 YooAsset 后,配置表【不在磁盘、不走系统代理(mitm 抓不到)、
HTTPS 加密拿不到、且价格是客户端按品质+占格算的不在网络里传】。唯一可行 = 读运行中进程的内存——
藏品目录登录后载入内存(断网仍能看图鉴即证),用已知真值(某藏品价格)当锚点定位结构后批量抠出。

机制:
- 物品配置对象会在同一 `itemName_<id>` 引用结构的偏移 28/36 重复保存
  `[quality(1-6), price(int32)]`。两个偏移必须给出完全相同的值才接受；单一偏移命中
  会混入邮件、勋章、技能等相邻对象，必须 fail-closed。
- 通用提取: 扫所有 `itemName_<id>` 字符串对象 -> 汇总全部引用 -> 只保留上述重复结构。
- 中文名不再猜任意相邻指针。请先用 `extract_tables_from_memory.py` 抽取本地化 TSV，
  再通过 `--localization-tsv` 提供；缺少该文件时输出仍保留可靠的品质/价格，但不填名称。

用法(游戏必须在跑、且打开过藏品图鉴让目录载入内存):
  python scripts/extract_tables_from_memory.py --out <tables_dir>
  python scripts/extract_catalog_from_memory.py --out data/processed/catalog_mem.json \
    --localization-tsv <tables_dir>/Language.tsv
需读取另一进程内存的权限(同用户一般够; 不行就管理员运行)。仅 Windows。

注意:这是【作者本机、本人游戏、图鉴本就能看的数据】的合法提取, 不碰服务器、不改游戏。
"""
from __future__ import annotations
import argparse, ctypes, ctypes.wintypes as w, json, re, struct, subprocess, sys
from collections import defaultdict
from pathlib import Path

_k = ctypes.windll.kernel32
PROCESS_VM_READ, PROCESS_QUERY_INFORMATION = 0x10, 0x400
MEM_COMMIT = 0x1000
READABLE = (0x02, 0x04, 0x20, 0x40)  # READONLY/READWRITE/EXECUTE_READ/EXECUTE_READWRITE 的低字节


class _MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", w.DWORD), ("RegionSize", ctypes.c_size_t),
                ("State", w.DWORD), ("Protect", w.DWORD), ("Type", w.DWORD)]


def _pid(name="BidKing.exe"):
    out = subprocess.run(["tasklist", "/fi", f"imagename eq {name}", "/fo", "csv", "/nh"],
                         capture_output=True, text=True).stdout
    if name not in out:
        return None
    return int(out.split('","')[1])


def _regions(h):
    out, addr = [], 0
    while addr < 0x7fffffffffff:
        mbi = _MBI()
        if not _k.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            break
        if mbi.State == MEM_COMMIT and (mbi.Protect & 0xFF) in READABLE and 0 < mbi.RegionSize < 300 * 1024 * 1024:
            out.append((mbi.BaseAddress or 0, mbi.RegionSize))
        addr += mbi.RegionSize
    return out


def _rpm(h, addr, n):
    buf = ctypes.create_string_buffer(n); got = ctypes.c_size_t(0)
    if _k.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, n, ctypes.byref(got)) and got.value:
        return buf.raw[:got.value]
    return None


def _read_cs(h, ptr, maxlen=60):
    """读 IL2CPP System.String: [klass:8][monitor:8][len:4@16][utf16 chars@20]。"""
    if not (0x10000 < ptr < 0x7fffffffffff):
        return None
    hd = _rpm(h, ptr + 16, 4)
    if not hd or len(hd) < 4:
        return None
    ln = struct.unpack("<i", hd)[0]
    if not (0 < ln < maxlen):
        return None
    d = _rpm(h, ptr + 20, ln * 2)
    if not d:
        return None
    try:
        return d.decode("utf-16-le")
    except UnicodeDecodeError:
        return None


_KEYPAT = b'i\x00t\x00e\x00m\x00N\x00a\x00m\x00e\x00_\x00'  # "itemName_" UTF-16LE
_KEYRE = re.compile(r'^itemName_(\d+)$')
_RECORD_PAIR_OFFSETS = frozenset((28, 36))


def _select_record_candidate(
    candidates: list[tuple[int, int, int]],
) -> tuple[int, int] | None:
    """Select one structurally repeated (quality, price) pair or fail closed.

    A raw `itemName_*` string is referenced by localization, mail, skills and other
    objects as well as by the collectible record.  The collectible object is the
    only observed structure that repeats the same pair at offsets 28 and 36.
    """
    offsets_by_pair: dict[tuple[int, int], set[int]] = defaultdict(set)
    for offset, quality, price in candidates:
        offsets_by_pair[(quality, price)].add(offset)
    accepted = [
        pair
        for pair, offsets in offsets_by_pair.items()
        if _RECORD_PAIR_OFFSETS.issubset(offsets)
    ]
    return accepted[0] if len(accepted) == 1 else None


def _localized_names_from_tsv(path: str | Path) -> dict[str, str]:
    """Read simplified-Chinese item names from a plain UTF-8 localization TSV."""
    values: dict[str, set[str]] = defaultdict(set)
    for line in Path(path).read_text(encoding="utf-8-sig", errors="replace").splitlines():
        fields = line.split("\t")
        if len(fields) < 2:
            continue
        match = _KEYRE.fullmatch(fields[0].strip())
        name = fields[1].strip()
        if match and name:
            values[match.group(1)].add(name)
    return {
        item_key: next(iter(names))
        for item_key, names in values.items()
        if len(names) == 1
    }


def extract(h):
    regions = _regions(h)
    # Pass A: 所有 itemName_<id> 字符串对象地址 -> id(对象基址 = 内容地址 - 20)
    keyobj = {}
    for base, sz in regions:
        off = 0
        while off < sz:
            blk = _rpm(h, base + off, min(0x100000, sz - off))
            if blk:
                for m in re.finditer(_KEYPAT, blk):
                    cs = m.start()
                    if cs >= 4:
                        ln = struct.unpack_from("<i", blk, cs - 4)[0]
                        if 0 < ln < 30:
                            try:
                                t = blk[cs:cs + ln * 2].decode("utf-16-le")
                            except UnicodeDecodeError:
                                continue
                            mm = _KEYRE.match(t)
                            if mm:
                                keyobj[base + off + cs - 20] = mm.group(1)
            off += 0x100000
    # Pass B: 找引用 itemName 键的记录, 窗口抓 quality+price
    try:
        import numpy as np
    except ImportError:
        np = None
    karr = sorted(keyobj.keys())
    record_candidates: dict[str, list[tuple[int, int, int]]] = defaultdict(list)
    karr_np = np.array(karr, dtype=np.uint64) if np else None
    keyset = set(karr)
    for base, sz in regions:
        off = 0
        while off < sz:
            blk = _rpm(h, base + off, min(0x100000, sz - off))
            if blk and len(blk) >= 8:
                arr = np.frombuffer(blk[:len(blk) // 8 * 8], dtype='<u8') if np else None
                idxs = (int(i) for i in __import__("numpy").nonzero(__import__("numpy").isin(arr, karr_np))[0]) if np \
                    else (i for i in range(0, len(blk) - 8, 8) if struct.unpack_from("<Q", blk, i)[0] in keyset)
                for idx in idxs:
                    P = idx * 8 if np else idx
                    kaddr = int(arr[idx]) if np else struct.unpack_from("<Q", blk, P)[0]
                    iid = keyobj.get(kaddr)
                    if not iid:
                        continue
                    for d in range(16, 52, 4):  # 键指针后的窗口找 quality+price
                        if P + d + 8 <= len(blk):
                            q = struct.unpack_from("<i", blk, P + d)[0]
                            pr = struct.unpack_from("<i", blk, P + d + 4)[0]
                            # 价格下限 100: 实测最低价藏品=白色1×1未知藏品箱 107(作者 2026-06-26 确认)。
                            # 旧下限 1000 会漏掉整族白/绿低品质"未知藏品箱"填充件(价 107-810),
                            # 这正是 6.25 提取漏 12 个填充藏品的根因。100 仍能挡掉 0/小整数噪声。
                            if 1 <= q <= 6 and 100 <= pr <= 50000000:
                                record_candidates[iid].append((d, q, pr))
            off += 0x100000
    items = {}
    for iid, candidates in record_candidates.items():
        selected = _select_record_candidate(candidates)
        if selected is None:
            continue
        quality, price = selected
        items[iid] = {"price": price, "q": quality}
    return items


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/processed/catalog_mem.json")
    ap.add_argument("--process", default="BidKing.exe")
    ap.add_argument(
        "--localization-tsv",
        default=None,
        help="extract_tables_from_memory.py 产出的明文 TSV；用于可靠读取中文名",
    )
    args = ap.parse_args(argv[1:])
    pid = _pid(args.process)
    if not pid:
        print(f"{args.process} 没在运行。请启动游戏->登录->打开藏品图鉴(翻全各品质)->保持开着, 再跑本脚本。")
        return 1
    h = _k.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        print("OpenProcess 失败(可能需管理员运行)。")
        return 1
    try:
        items = extract(h)
    finally:
        _k.CloseHandle(h)
    if args.localization_tsv:
        names = _localized_names_from_tsv(args.localization_tsv)
        for iid, row in items.items():
            if iid in names:
                row["name"] = names[iid]
    else:
        print("警告: 未提供 --localization-tsv；为避免相邻指针误配，本次不输出中文名。")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with Path(args.out).open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(items, handle, ensure_ascii=False, indent=1, sort_keys=True)
    named = sum(1 for v in items.values() if v.get("name"))
    print(f"提取 {len(items)} 个物品(已命名 {named}) -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
