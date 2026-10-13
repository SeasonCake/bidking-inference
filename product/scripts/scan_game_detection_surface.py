# -*- coding: utf-8 -*-
"""扫描游戏本地文件的【检测面】—— 每次游戏官方更新后重跑, 看游戏有没有新加针对抓包/反作弊/反调试的逻辑。

背景(2026-07-08 A5 探测): 本地静态分析确认当前版本【零抓包检测/零本地反作弊】, 封禁是服务器端按行为
判(见 docs/handoffs/ANTIDETECT_groundwork_2026-07-08)。但"现在没有 ≠ 下次更新不会加"——所以把探测固化
成可复用工具, 每次游戏更新后一键重扫, 有新命中就人工深查 + 决定是否上伪装。

扫两侧:
  1) il2cpp AOT: global-metadata.dat + GameAssembly.dll(字符串/元数据·含全部类/方法名);
  2) HybridCLR 热更 DLL: Scripts.dll.bytes(XOR key 'ryrs' 解密后=活的游戏逻辑), 由 --hotfix 指定。

用法:
  python scripts/scan_game_detection_surface.py                 # 自动定位游戏, 只扫 AOT
  python scripts/scan_game_detection_surface.py --hotfix "C:\\...\\Scripts.dll.bytes"   # 也扫热更
  python scripts/scan_game_detection_surface.py --game "C:\\...\\steamapps\\common\\BidKing"

退出码: 0=干净(无检测面命中) / 2=有命中需人工深查 / 1=找不到游戏。
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

# 关键词分组。命中 = 需人工确认是"真检测逻辑"还是"框架/Steam SDK/游戏功能的假子串"。
# (A5 探测已知的假朋友: k_EDeny*Anticheat/k_EDenyCheater=Steam SDK; noNProtected=.NET; hipcap=Championship;
#  Bottom/autoSet*/CollisionDetection=UI/游戏功能。这些出现属正常, 真正要警惕的是【新出现的、成对的】命中。)
CAPTURE = ["npcap", "wpcap", "winpcap", "WinDivert", "promiscu", "RawSocket", "Wireshark", "PacketCapture"]
CAPTURE_WEAK = ["pcap", "sniff"]  # 易假子串, 单独列
ANTICHEAT = ["AntiCheat", "antiCheat", "AntiBot", "GameGuard", "nProtect", "XignCode", "BattlEye", "EasyAntiCheat"]
PROC_ENUM = ["EnumProcess", "CreateToolhelp32Snapshot", "ReadProcessMemory", "OpenProcess",
             "Process.GetProcesses", "GetModuleHandle", "EnumDeviceDrivers"]
ANTIDEBUG = ["IsDebuggerPresent", "CheckRemoteDebugger", "NtQueryInformationProcess", "OutputDebugString"]
GROUPS = {"抓包检测": CAPTURE, "抓包(弱/易假子串)": CAPTURE_WEAK, "反作弊中间件": ANTICHEAT,
          "进程/模块枚举": PROC_ENUM, "反调试": ANTIDEBUG}


def _steam_libraries() -> list[Path]:
    libs: list[Path] = []
    # 注册表 SteamPath
    try:
        import winreg  # type: ignore
        for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\\Valve\\Steam"),
                          (winreg.HKEY_LOCAL_MACHINE, r"Software\\WOW6432Node\\Valve\\Steam")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    p = Path(winreg.QueryValueEx(k, "SteamPath")[0])
                    libs.append(p)
            except OSError:
                continue
    except Exception:  # noqa: BLE001
        pass
    # libraryfolders.vdf 里的其它库
    for base in list(libs):
        vdf = base / "steamapps" / "libraryfolders.vdf"
        try:
            for line in vdf.read_text(encoding="utf-8", errors="ignore").splitlines():
                if '"path"' in line:
                    libs.append(Path(line.split('"path"')[1].strip().strip('"').replace("\\\\", "\\")))
        except OSError:
            continue
    # 常见硬路径兜底(含作者已知库)
    for hard in [r"C:\Games", r"C:\Program Files (x86)\Steam", r"D:\Steam", r"E:\Steam"]:
        libs.append(Path(hard))
    return libs


def _find_game(explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit)
        return p if (p / "GameAssembly.dll").is_file() else None
    for lib in _steam_libraries():
        cand = lib / "steamapps" / "common" / "BidKing"
        if (cand / "GameAssembly.dll").is_file():
            return cand
    return None


def _scan_bytes(data: bytes, keywords: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for kw in keywords:
        c = data.count(kw.encode("utf-8"))
        if c:
            out[kw] = c
    return out


def _sample_context(data: bytes, kw: str, limit: int = 3) -> list[str]:
    """抠出命中周围的可打印字符串, 供人工判"真检测 vs 假子串"。"""
    out: list[str] = []
    needle = kw.encode("utf-8")
    i = data.find(needle)
    while i != -1 and len(out) < limit:
        s = i
        while s > 0 and 32 <= data[s - 1] < 127:
            s -= 1
        e = i
        while e < len(data) and 32 <= data[e] < 127:
            e += 1
        tok = data[s:e].decode("ascii", "replace")
        if 4 <= len(tok) < 80:
            out.append(tok)
        i = data.find(needle, i + 1)
    return out


def _xor_decrypt(path: Path, key: bytes = b"ryrs") -> bytes | None:
    try:
        enc = path.read_bytes()
    except OSError:
        return None
    dec = bytes(enc[i] ^ key[i % len(key)] for i in range(len(enc)))
    return dec if dec[:2] == b"MZ" else None


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="扫游戏本地检测面(抓包/反作弊/反调试)")
    ap.add_argument("--game", default=None, help="游戏安装目录(含 GameAssembly.dll)")
    ap.add_argument("--hotfix", default=None, help="HybridCLR 热更 Scripts.dll.bytes 路径(XOR ryrs 解密后扫)")
    args = ap.parse_args(argv)

    game = _find_game(args.game)
    if game is None:
        print("[error] 找不到游戏(GameAssembly.dll)。用 --game 指定安装目录。", file=sys.stderr)
        return 1
    print(f"[game] {game}")

    targets: list[tuple[str, bytes]] = []
    meta = game / "BidKing_Data" / "il2cpp_data" / "Metadata" / "global-metadata.dat"
    ga = game / "GameAssembly.dll"
    for label, p in (("global-metadata.dat", meta), ("GameAssembly.dll", ga)):
        try:
            targets.append((label, p.read_bytes()))
        except OSError:
            print(f"[warn] 读不到 {label}", file=sys.stderr)
    if args.hotfix:
        dec = _xor_decrypt(Path(args.hotfix))
        if dec:
            targets.append(("hotfix(Scripts_orig.dll)", dec))
            print(f"[hotfix] 解密OK {len(dec)}B")
        else:
            print(f"[warn] 热更解密失败(非MZ/读不到): {args.hotfix}", file=sys.stderr)

    # 指纹 = {"group/label/kw": count}。基线存 data/reports/, 重扫只报【新增/变化】——
    # 因标准 Windows API(GetModuleHandle/IsDebuggerPresent/OutputDebugStringW 等)每个 native 二进制都有,
    # 每次都报=噪音。真正要抓的是"游戏更新后【新出现】的检测关键词"。
    import json
    fingerprint: dict[str, int] = {}
    capture_strong_hit = False
    for group, kws in GROUPS.items():
        print(f"\n=== {group} ===")
        any_hit = False
        for label, data in targets:
            for kw, c in _scan_bytes(data, kws).items():
                any_hit = True
                ctx = _sample_context(data, kw)
                fingerprint[f"{group}/{label}/{kw}"] = c
                if group == "抓包检测":  # 强抓包关键词任何命中都是真信号(正常Unity游戏不会有)
                    capture_strong_hit = True
                print(f"  [{label}] {kw} x{c}  样本={ctx}")
        if not any_hit:
            print("  (无命中)")

    base_path = Path(__file__).resolve().parent.parent / "data" / "reports" / "game_detection_baseline.json"
    is_first_run = not base_path.is_file()
    baseline: dict[str, int] = {}
    if not is_first_run:
        try:
            baseline = json.loads(base_path.read_text(encoding="utf-8")).get("fingerprint", {})
        except (OSError, ValueError):
            baseline = {}
    new_keys = [] if is_first_run else [k for k in fingerprint if k not in baseline]

    print("\n" + "=" * 50)
    if is_first_run:
        base_path.parent.mkdir(parents=True, exist_ok=True)
        base_path.write_text(json.dumps({"fingerprint": fingerprint}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[baseline] 首次运行, 已存基线({len(fingerprint)}项): {base_path}")
        print("   下次游戏官方更新后重跑本脚本, 只会报【新增】命中(游戏新加的检测)。")

    if capture_strong_hit:
        print("🔴 抓包检测强命中(npcap/WinDivert 等)! 游戏可能新加了抓包检测 → 立即人工深查 + 评估上伪装。")
        return 2
    if new_keys:
        print(f"⚠️ 相比基线【新增 {len(new_keys)} 个检测关键词命中】(游戏更新引入?)→ 人工核对:")
        for k in new_keys:
            print(f"   + {k} = {fingerprint[k]}")
        print("   核对是'真检测逻辑'还是'框架/SDK假子串'; 确认是新检测→评估伪装(ANTIDETECT_groundwork)。")
        return 2
    print("✅ 检测面 vs 基线无变化: 无抓包强命中、无新增检测关键词。")
    print("   已知基线噪音(假朋友): 标准WinAPI GetModuleHandle/IsDebuggerPresent/OutputDebugStringW ·")
    print("   Steam SDK k_EDeny*Anticheat/Cheater · .NET noNProtected · 编译器 Debugger*Attribute。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
