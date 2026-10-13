# -*- coding: utf-8 -*-
"""一次性「明文在哪张网卡」抓包诊断 — 专治加速器(UU/迅游等)抓不到包。

群友症状:工具显示"已连接·等待对局包"(active_flows>0)但一个游戏包都抓不到(sniffed=0)。
根因候选两种,必须区分(见 memory accelerator-virtual-nic-blocks-capture):
  ① 加速器把游戏流量导进自己的虚拟网卡(TUN),而那张网卡 Npcap 枚举不到 → 明文在工具看不见的网卡上;
  ② 加速器在自己网卡上也加密/封装 → 任何 Npcap 能看的网卡上都没有明文。

本脚本在【对局中、加速器开着】跑一次,同时在所有网卡上抓 N 秒,逐网卡报告:
  - 是否有 TCP 流量(网卡是否活跃)
  - 是否有【游戏服务器端口】的包(= 该网卡上有游戏明文)
  - 是否能解析出游戏帧(更强佐证)
并给出判定 + 检测到的加速器进程。把输出截图发回即可定方案。

用法(管理员 PowerShell,Npcap 已装,游戏在对局中):
    cd C:\\example\\bidking-workspace\\bidking-lab
    python scripts\\diagnose_capture_adapters.py --seconds 30 --port 10000
"""
from __future__ import annotations

import argparse
import base64
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "scripts", ROOT / "src", ROOT / "hero_ref" / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from bidking_lab.live.capture import (  # noqa: E402
    NpcapBackend,
    build_bpf_filter,
    default_capture_devices,
    list_devices,
)

try:
    from run_windivert_live_monitor import GameFrameGate, _capture_time  # noqa: E402
    _HAVE_GATE = True
except Exception:  # noqa: BLE001 - frame parsing is a bonus; port match is the primary signal
    _HAVE_GATE = False

# 加速器/虚拟网卡的描述关键词(网卡描述里出现 = 疑似加速器/隧道网卡)
ACCEL_ADAPTER_HINTS = (
    "uu", "网易uu", "迅游", "xunyou", "雷神", "leigod", "biubiu", "奇游", "qiyou",
    "wtfast", "tap-windows", "tap-win32", "wintun", "tun", "tap", "openvpn",
    "accelerat", "booster", "vpn", "nordlynx", "wireguard",
)
# 加速器进程名(任务栏有这些 = 开着加速器)
ACCEL_PROCESS_HINTS = (
    "uu", "网易uu", "uuaccel", "uubooster", "迅游", "xunyou", "雷神", "leigod",
    "biubiu", "奇游", "qiyou", "wtfast", "accelerat", "booster", "haitun", "海豚",
)


def _detect_accelerator_processes() -> list[str]:
    """扫描运行进程,返回疑似加速器进程名(去重)。失败返回空(不阻断诊断)。"""
    found: set[str] = set()
    try:
        out = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:  # noqa: BLE001
        return []
    for line in out.splitlines():
        name = line.split(",", 1)[0].strip().strip('"')
        low = name.lower()
        if any(h in low for h in ACCEL_PROCESS_HINTS):
            found.add(name)
    return sorted(found)


class _DevCounters:
    __slots__ = ("tcp", "port", "frames", "sessions", "error")

    def __init__(self) -> None:
        self.tcp = 0
        self.port = 0
        self.frames = 0
        self.sessions: set[str] = set()
        self.error: str | None = None


def _drain(device: str, desc: str, port: int, ctr: _DevCounters, stop: threading.Event) -> None:
    """单网卡抓 N 秒:统计 TCP 总数 / 游戏端口包 / 可解析游戏帧。每网卡独立 GameFrameGate
    (避免跨网卡重组互相污染——live 监控是跨网卡去重喂一个 gate,诊断要每网卡独立计数)。"""
    gate = GameFrameGate() if _HAVE_GATE else None
    sort_id = 1
    try:
        # VLAN-aware broad filter: a plain "tcp" BPF drops 802.1Q-tagged inbound frames at
        # the kernel (Intel Wi-Fi RX tagging), which would make this diagnostic falsely report
        # "no inbound / can't capture" on exactly the machines hit by that bug. See build_bpf_filter.
        with NpcapBackend(device, bpf=build_bpf_filter(None, broad=True)) as backend:
            for pkt in backend.packets():
                if stop.is_set():
                    break
                ctr.tcp += 1
                if pkt.src_port != port and pkt.dst_port != port:
                    continue
                ctr.port += 1
                if gate is None:
                    continue
                # 喂 GameFrameGate 确认是否真是游戏帧(端口对了但可能是隧道封装)
                direction = "Send" if pkt.dst_port == port else "Recv"
                row = {
                    "SortID": sort_id, "Direct": direction, "Protocol": "Tcp",
                    "SrcIP": pkt.src_ip, "SrcPort": pkt.src_port,
                    "DstIP": pkt.dst_ip, "DstPort": pkt.dst_port,
                    "CaptureTime": _capture_time(),
                    "Data": base64.b64encode(pkt.payload).decode("ascii"),
                    "DataLength": len(pkt.payload),
                    "PID": 0, "ProcessName": "diag", "Source": "Npcap",
                }
                sort_id += 1
                try:
                    emit = gate.feed_row(row)
                    ctr.frames += len(emit.rows)
                    sid = getattr(gate, "_current_session_id", None)
                    if sid:
                        ctr.sessions.add(str(sid))
                except Exception:  # noqa: BLE001 - frame parse is best-effort
                    pass
    except Exception as exc:  # noqa: BLE001 - per-device failure surfaced, others keep going
        ctr.error = f"{type(exc).__name__}: {exc}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seconds", type=int, default=30, help="抓包时长(对局中跑)")
    ap.add_argument("--port", type=int, default=10000, help="游戏服务器端口(默认 10000)")
    args = ap.parse_args()

    try:
        devices = default_capture_devices()
    except OSError as exc:
        print(f"[error] 无法枚举网卡 — Npcap 装了吗?({exc})")
        print("        下载: https://npcap.com/#download")
        return 2
    if not devices:
        print("[error] Npcap 一张网卡都没枚举到(Npcap 未装 / 未勾选完整选项 / 非管理员)")
        return 2

    all_names = {n for n, _ in list_devices()}
    print(f"=== Npcap 枚举到 {len(devices)} 张可抓网卡 ===")
    suspicious = []
    for i, (name, desc) in enumerate(devices):
        flag = ""
        if any(h in desc.lower() for h in ACCEL_ADAPTER_HINTS):
            flag = "  <== 疑似加速器/隧道网卡"
            suspicious.append(desc)
        print(f"  [{i}] {desc}{flag}")

    accel_procs = _detect_accelerator_processes()
    print(f"\n=== 检测到的疑似加速器进程: {accel_procs or '无'} ===")
    if accel_procs and not suspicious:
        print("  [注意] 开着加速器进程,但 Npcap 列表里【没有】加速器/隧道网卡")
        print("     → 很可能根因①:加速器的虚拟网卡(TUN)Npcap 绑不到,明文在工具看不见的网卡上")

    print(f"\n=== 在所有网卡同时抓 {args.seconds}s(请保持在对局中)... ===", flush=True)
    counters = {name: _DevCounters() for name, _ in devices}
    stop = threading.Event()
    threads = [
        threading.Thread(target=_drain, args=(name, desc, args.port, counters[name], stop),
                         name=f"diag-{i}", daemon=True)
        for i, (name, desc) in enumerate(devices)
    ]
    for t in threads:
        t.start()
    # 进度
    for elapsed in range(args.seconds):
        time.sleep(1)
        if elapsed and elapsed % 5 == 0:
            hot = sum(c.port for c in counters.values())
            print(f"  [{elapsed}/{args.seconds}s] 游戏端口包累计 = {hot}", flush=True)
    stop.set()
    for t in threads:
        t.join(timeout=2.0)

    print(f"\n=== 逐网卡结果(端口 {args.port}) ===")
    print(f"  {'网卡':<46} {'TCP包':>8} {'游戏端口包':>10} {'游戏帧':>8}")
    plaintext_on = []
    for name, desc in devices:
        c = counters[name]
        tag = ""
        if c.error:
            tag = f"  抓包失败: {c.error}"
        elif c.port > 0:
            tag = "  <== 游戏明文在这张网卡!"
            plaintext_on.append((desc, c))
        print(f"  {desc[:46]:<46} {c.tcp:>8} {c.port:>10} {c.frames:>8}{tag}")

    print("\n=== 判定 ===")
    if plaintext_on:
        names = ", ".join(d for d, _ in plaintext_on)
        any_frames = any(c.frames > 0 for _, c in plaintext_on)
        print(f"  [明文找到] 游戏明文出现在: {names}")
        if any_frames:
            print("     且能解析出游戏帧 → 工具的多网卡抓包【本应】能抓到。")
            print("     若实际工具仍抓不到,把这张网卡名发回 → 加 --device 强制指定 / 查为何被漏。")
        else:
            print("     有游戏端口包但没解析出帧 → 可能是隧道封装的同端口流量,需进一步看 payload。")
    elif accel_procs:
        print("  [注意] 所有 Npcap 网卡都没有游戏端口的明文,但开着加速器进程。")
        if suspicious:
            print("     加速器网卡在列表里却没明文 → 根因②:加速器在自己网卡上也加密/封装,Npcap 解不了。")
        else:
            print("     加速器网卡不在 Npcap 列表 → 根因①:Npcap 绑不到加速器的 TUN 网卡。")
        print("     解法候选(发回结果一起定):关加速器直连 / 重装 Npcap 勾完整选项 / 试 WinDivert 后端 / 加速器换不走网卡的模式。")
    else:
        print("  [?] 没抓到游戏端口流量,且没检测到加速器进程。请确认:")
        print(f"     (1) 真的在对局中(不是大厅) (2) 游戏服务器端口确实是 {args.port}(不对用 --port 改)")
        print("     (3) 是管理员运行 (4) 没开 VPN/代理把流量导走")
    print(f"\n  (参考:已知列表外网卡共 {len(all_names - {n for n,_ in devices})} 张被跳过=WAN Miniport/蓝牙等)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
