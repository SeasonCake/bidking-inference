"""Offline analyzer for a CDN-discovery pcap (robust, scapy-layer based).

Extracts, from a saved capture.pcap:
  - TLS SNI hostnames (from ClientHello) -> the HTTPS hosts the game contacts
  - plain HTTP request lines + Host + URL (if any tables come over HTTP)
  - DNS A answers (if any plaintext DNS)
  - per-host connection + byte tallies (which host got the big downloads)

Usage: python scripts/analyze_cdn_capture.py [path/to/capture.pcap]
"""

from __future__ import annotations

import io
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from scapy.all import rdpcap, TCP, IP, IPv6, Raw, DNS, DNSRR  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _sni(payload: bytes) -> str | None:
    # TLS record: 16 03 ?? len(2) ; handshake: 01(ClientHello) len(3) ver(2) rand(32)...
    try:
        if payload[0] != 0x16 or payload[5] != 0x01:
            return None
        p = 43  # 5 rec hdr + 4 hs hdr + 2 ver + 32 rand
        sid_len = payload[p]; p += 1 + sid_len
        cs_len = int.from_bytes(payload[p:p+2], "big"); p += 2 + cs_len
        comp_len = payload[p]; p += 1 + comp_len
        ext_total = int.from_bytes(payload[p:p+2], "big"); p += 2
        end = p + ext_total
        while p + 4 <= end:
            etype = int.from_bytes(payload[p:p+2], "big")
            elen = int.from_bytes(payload[p+2:p+4], "big"); p += 4
            if etype == 0x0000:  # server_name
                # server_name_list(2) + type(1) + name_len(2) + name
                nlen = int.from_bytes(payload[p+3:p+5], "big")
                return payload[p+5:p+5+nlen].decode("latin1", "ignore")
            p += elen
    except Exception:  # noqa: BLE001
        return None
    return None


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "data/logs/cdn_capture/capture.pcap")
    pkts = rdpcap(path)
    sni_hosts: Counter = Counter()
    http_reqs: list[str] = []
    dns: dict[str, set] = defaultdict(set)
    bytes_by_dst: Counter = Counter()
    for p in pkts:
        if p.haslayer(DNS) and getattr(p[DNS], "ancount", 0):
            an = p[DNS].an
            for k in range(p[DNS].ancount):
                rr = an[k] if not isinstance(an, DNSRR) else an
                if isinstance(rr, DNSRR):
                    try:
                        dns[rr.rrname.decode("latin1").rstrip(".")].add(str(rr.rdata))
                    except Exception:  # noqa: BLE001
                        pass
        if p.haslayer(TCP) and p.haslayer(Raw):
            pl = bytes(p[Raw].load)
            dst = (p[IP].dst if p.haslayer(IP) else (p[IPv6].dst if p.haslayer(IPv6) else "?"))
            bytes_by_dst[f"{dst}:{p[TCP].dport}"] += len(pl)
            s = _sni(pl)
            if s:
                sni_hosts[s] += 1
            if pl[:3] in (b"GET", b"POS", b"HEA") or pl[:4] == b"PUT ":
                line = pl.split(b"\r\n", 1)[0].decode("latin1", "ignore")
                host = ""
                for h in pl.split(b"\r\n"):
                    if h.lower().startswith(b"host:"):
                        host = h.split(b":", 1)[1].strip().decode("latin1", "ignore")
                http_reqs.append(f"{host}  {line}")

    print(f"=== {path}: {len(pkts)} pkts ===")
    print("\n[TLS SNI hosts] (HTTPS the game contacts):")
    for h, c in sni_hosts.most_common(40):
        print(f"  {c:4d}  {h}")
    print("\n[plain HTTP requests]:")
    for r in http_reqs[:40]:
        print(f"  {r}")
    if not http_reqs:
        print("  (none -- all CDN traffic is HTTPS)")
    print("\n[plaintext DNS answers]:")
    for h, ips in sorted(dns.items()):
        print(f"  {h} -> {sorted(ips)}")
    if not dns:
        print("  (none -- DNS handled by Clash/DoH)")
    print("\n[top dst by bytes] (who got the big downloads):")
    for d, n in bytes_by_dst.most_common(15):
        print(f"  {n//1024:6d} KB  {d}")


if __name__ == "__main__":
    main()
