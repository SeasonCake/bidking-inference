"""Npcap end-to-end verify — does Npcap capture yield parseable GAME frames?

Captures on a chosen interface via NpcapBackend, attributes each packet to
BidKing.exe via the same FlowIndex (OS TCP table) the WinDivert path uses, feeds
the existing GameFrameGate, and reports how many real game frames + session ids
emerge. If game frames > 0, Npcap sees the game PLAINTEXT on that adapter (the
decisive test under Clash TUN / VPN) and the migration is viable.

Run on the user's machine (admin PowerShell, Npcap installed, game in an auction):
    cd C:\\example\\bidking-workspace\\bidking-lab
    python scripts\\npcap_capture_verify.py --device-index 4 --seconds 25
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import math
import sys
import time
from collections import Counter
from ctypes import POINTER, c_char_p, c_int, c_void_p
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "scripts", ROOT / "src", ROOT / "hero_ref/src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from bidking_lab.live.capture import NpcapBackend, build_bpf_filter  # noqa: E402
from npcap_probe import _PcapIf, _load_wpcap  # noqa: E402  (reuse device listing)
from run_windivert_live_monitor import (  # noqa: E402
    FlowIndex,
    GameFrameGate,
    _capture_time,
)


def _is_private_ip(ip: str) -> bool:
    if ip.startswith(("10.", "192.168.", "127.", "100.64.", "169.254.")):
        return True
    if ip.startswith("172."):
        try:
            return 16 <= int(ip.split(".")[1]) <= 31
        except (ValueError, IndexError):
            return False
    return False


def _guess_direction(src_ip: str, dst_ip: str) -> str | None:
    """Broad-mode direction guess (no TCP table): local(private)->server(public)=SEND."""
    s_priv, d_priv = _is_private_ip(src_ip), _is_private_ip(dst_ip)
    if s_priv and not d_priv:
        return "SEND"
    if d_priv and not s_priv:
        return "REV"
    return None


def _byte_entropy(data: bytes) -> float:
    """Shannon entropy (bits/byte). Encrypted/TLS ~7.9; plaintext/struct lower."""
    if not data:
        return 0.0
    counts = Counter(data)
    n = len(data)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _device_list() -> list[tuple[str, str]]:
    """Return [(npf_name, description), ...]."""
    lib = _load_wpcap()
    lib.pcap_findalldevs.argtypes = [POINTER(POINTER(_PcapIf)), c_char_p]
    lib.pcap_findalldevs.restype = c_int
    devs = POINTER(_PcapIf)()
    err = ctypes.create_string_buffer(256)
    if lib.pcap_findalldevs(ctypes.byref(devs), err) != 0:
        return []
    out: list[tuple[str, str]] = []
    node = devs
    while node:
        d = node.contents
        out.append((
            d.name.decode(errors="ignore") if d.name else "",
            d.description.decode(errors="ignore") if d.description else "",
        ))
        node = d.next
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device-index", type=int, default=None, help="interface index from npcap_probe.py")
    ap.add_argument("--device-name", default=None,
                    help="case-insensitive substring of the adapter (stable across VPN on/off, "
                         "unlike index). e.g. --device-name Wi-Fi  or  --device-name Loopback")
    ap.add_argument("--seconds", type=int, default=25)
    ap.add_argument("--process-name", default="BidKing.exe")
    ap.add_argument("--ports", default="", help="comma-separated game server ports (optional)")
    ap.add_argument("--broad", action="store_true",
                    help="skip BidKing TCP-table attribution; feed all TCP (guess direction by IP). "
                         "Use on a Clash/VPN TUN adapter where physical-NIC traffic is owned by Clash.exe.")
    args = ap.parse_args()

    devs = _device_list()
    device = None
    if args.device_name:
        for name, desc in devs:
            if args.device_name.lower() in desc.lower() or args.device_name.lower() in name.lower():
                device = name
                print(f"[verify] matched --device-name {args.device_name!r} -> {desc}")
                break
        if device is None:
            print(f"[error] no adapter matches {args.device_name!r}. Available:")
            for i, (n, d) in enumerate(devs):
                print(f"  [{i}] {d}")
            return 2
    elif args.device_index is not None and 0 <= args.device_index < len(devs):
        device = devs[args.device_index][0]
    else:
        print(f"[error] give --device-name (stable) or valid --device-index (0..{len(devs)-1})")
        return 2
    ports = {int(p) for p in args.ports.split(",") if p.strip().isdigit()}
    bpf = build_bpf_filter(sorted(ports) or None, broad=not ports)

    flow = FlowIndex(process_name=args.process_name, refresh_seconds=1.0, server_ports=ports or None)
    flow.refresh_if_due(force=True)
    gate = GameFrameGate()

    pkts = matched = frames = 0
    sessions: set[str] = set()
    msg_ids: dict[str, int] = {}
    ent_sum = 0.0
    ent_n = 0
    start = time.monotonic()
    print(f"[verify] device={device}\n[verify] bpf={bpf!r} process={args.process_name} {args.seconds}s ...")
    with NpcapBackend(device, bpf=bpf) as backend:
        print(f"[verify] datalink={backend.linktype} (1=EN10MB,12=RAW,0=NULL)")
        sort_id = 0
        for pkt in backend.packets():
            pkts += 1
            if len(pkt.payload) >= 16:  # entropy on real payloads only
                ent_sum += _byte_entropy(pkt.payload[:512])
                ent_n += 1
            key = (pkt.src_ip, pkt.src_port, pkt.dst_ip, pkt.dst_port)
            if args.broad:
                direction = _guess_direction(pkt.src_ip, pkt.dst_ip)
                pid, proc = 0, "?"
            else:
                m = flow.match(key)
                direction = m.direction if m else None
                pid, proc = (m.pid, m.process_name) if m else (0, "?")
            if direction is not None:
                matched += 1
                row = {
                    "SortID": sort_id, "Direct": direction, "Protocol": "Tcp",
                    "SrcIP": pkt.src_ip, "SrcPort": pkt.src_port,
                    "DstIP": pkt.dst_ip, "DstPort": pkt.dst_port,
                    "CaptureTime": _capture_time(),
                    "Data": base64.b64encode(pkt.payload).decode("ascii"),
                    "DataLength": len(pkt.payload),
                    "PID": pid, "ProcessName": proc, "Source": "Npcap",
                }
                sort_id += 1
                emit = gate.feed_row(row)
                for gf in emit.rows:  # FrameGateEmit.rows are dicts (SessionID/MessageID keys)
                    frames += 1
                    sid = str(gf.get("SessionID") or gf.get("session_id") or "")
                    if sid:
                        sessions.add(sid)
                    mid = str(gf.get("MessageID") or "0x0000")
                    msg_ids[mid] = msg_ids.get(mid, 0) + 1
            if time.monotonic() - start >= args.seconds:
                break

    mean_ent = (ent_sum / ent_n) if ent_n else 0.0
    attr = "dir_guessed" if args.broad else "bidking_matched"
    print(f"\n[result] tcp_packets={pkts} {attr}={matched} game_frames={frames}")
    # gate's own authoritative counters (frames emitted only after session validation)
    print(f"[result] gate.accepted_frames={getattr(gate, 'accepted_frames', '?')} "
          f"current_session={getattr(gate, '_current_session_id', None)!r} "
          f"ignored={dict(getattr(gate, 'ignored_reasons', {}))}")
    print(f"[result] mean_payload_entropy={mean_ent:.2f} bits/byte over {ent_n} payloads "
          f"(~7.9=加密/TLS, <~6.5=明文/结构化)")
    print(f"[result] sessions={sorted(sessions)}")
    print(f"[result] message_ids={dict(sorted(msg_ids.items(), key=lambda kv: -kv[1])[:10])}")
    if frames > 0 and sessions:
        print("\n✅ Npcap 抓到游戏明文帧（含 session）→ 此网卡可用，迁移可行。")
    elif ent_n and mean_ent >= 7.5:
        print("\n⚠️ payload 熵接近满(加密) → 此网卡上是 Clash 代理/TLS 加密流量，非游戏明文。")
    elif matched > 0:
        print("\n⚠️ 有流量但未解析出游戏帧 → 端口/方向或非游戏流量；看熵判断明文与否。")
    else:
        print("\n⚠️ 此网卡无可归属流量 → 换网卡，或游戏明文可能在 loopback[8]（系统代理）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
