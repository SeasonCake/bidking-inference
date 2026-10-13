"""Stage-1 CDN discovery capture: find where the game downloads its tables.

Non-invasive: just sniffs the box's HTTP(S)/DNS while the game launches. No proxy,
no cert. Goal = identify the CDN host(s) and whether the table/hot-update download
is plain HTTP (then we extract it straight from here) or HTTPS (then we escalate to
mitmproxy for the body). Game protobuf (tcp 10000) is intentionally excluded.

Outputs (under data/logs/cdn_capture/):
  - capture.pcap            full raw capture for offline re-analysis
  - hosts.json             DNS answers + TLS SNI -> the hosts the game talks to
  - http_flows.jsonl       every HTTP request/response line (method,url,status,len,ctype)
  - bodies/<n>_<host>_<name>   saved HTTP response bodies (non-HTML)

Run (Administrator, Npcap installed):
  python scripts/capture_cdn_table_download.py --seconds 180
Then launch the game (test account) within that window.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "logs" / "cdn_capture"
BODIES = OUT / "bodies"

from scapy.all import AsyncSniffer, wrpcap, Raw, DNS, DNSRR, IP, TCP, UDP, get_working_ifaces  # noqa: E402


def _capture_ifaces():
    """All working ifaces except the useless WAN Miniport pseudo-adapters.

    Critical on this box: the user runs Clash/Mihomo (TUN at 198.18.0.1), so the
    game's HTTP(S) to the CDN appears on the Mihomo tunnel (and/or loopback) with a
    real Host header BEFORE Clash proxies it upstream -- not just on WLAN.
    """
    out = []
    for i in get_working_ifaces():
        desc = (getattr(i, "description", "") or "")
        if "WAN Miniport" in desc or "Wi-Fi Direct" in desc:
            continue
        out.append(i)
    return out

try:
    from scapy.layers.tls.all import TLS  # noqa: F401
    from scapy.layers.tls.handshake import TLSClientHello  # noqa: F401
    from scapy.layers.tls.extensions import TLS_Ext_ServerName  # noqa: F401
    _HAS_TLS = True
except Exception:  # noqa: BLE001
    _HAS_TLS = False

hosts: dict[str, set] = {"dns": {}, "sni": set()}
http_flows: list[dict] = []
_body_n = [0]
INTERESTING = ("bidking", "cdn", "laolin", "oss", "aliyun", "table", "/Tables", "filelist",
               "fileVersion", "Scripts", ".bytes", "DefaultPackage")


def _sni_from_clienthello(payload: bytes) -> str | None:
    # Proper TLS ClientHello server_name parse: 16(rec) 03 ?? len ; 01(CH) len ver rand ...
    try:
        if payload[0] != 0x16 or payload[5] != 0x01:
            return None
        p = 43
        p += 1 + payload[p]                                   # session id
        p += 2 + int.from_bytes(payload[p:p+2], "big")        # cipher suites
        p += 1 + payload[p]                                   # compression
        ext_total = int.from_bytes(payload[p:p+2], "big"); p += 2
        end = p + ext_total
        while p + 4 <= end:
            etype = int.from_bytes(payload[p:p+2], "big")
            elen = int.from_bytes(payload[p+2:p+4], "big"); p += 4
            if etype == 0x0000:
                nlen = int.from_bytes(payload[p+3:p+5], "big")
                return payload[p+5:p+5+nlen].decode("latin1", "ignore")
            p += elen
    except Exception:  # noqa: BLE001
        return None
    return None


def _handle(pkt) -> None:
    try:
        # DNS answers -> host:ip
        if pkt.haslayer(DNS) and pkt[DNS].ancount:
            for k in range(pkt[DNS].ancount):
                rr = pkt[DNS].an[k]
                if isinstance(rr, DNSRR):
                    name = rr.rrname.decode("latin1").rstrip(".")
                    rdata = rr.rdata
                    hosts["dns"].setdefault(name, set())
                    try:
                        hosts["dns"][name].add(str(rdata))
                    except Exception:  # noqa: BLE001
                        pass
        if pkt.haslayer(TCP) and pkt.haslayer(Raw):
            payload = bytes(pkt[Raw].load)
            dport = pkt[TCP].dport
            sport = pkt[TCP].sport
            # TLS ClientHello on 443 -> SNI
            if (dport == 443 or sport == 443) and payload[:1] == b"\x16":
                sni = _sni_from_clienthello(payload)
                if sni:
                    hosts["sni"].add(sni)
            # HTTP request on 80
            if payload[:3] in (b"GET", b"POS", b"HEA") or payload.startswith(b"PUT"):
                line = payload.split(b"\r\n", 1)[0].decode("latin1", "ignore")
                host = ""
                for h in payload.split(b"\r\n"):
                    if h.lower().startswith(b"host:"):
                        host = h.split(b":", 1)[1].strip().decode("latin1", "ignore")
                        break
                rec = {"t": "req", "line": line, "host": host,
                       "dst": f"{pkt[IP].dst}:{dport}" if pkt.haslayer(IP) else ""}
                http_flows.append(rec)
                if any(k.lower() in (line + host).lower() for k in INTERESTING):
                    print(f"  [HTTP REQ] {host} {line}", flush=True)
            # HTTP response on 80 -> save body
            elif payload[:5] == b"HTTP/":
                head, _, body = payload.partition(b"\r\n\r\n")
                status = head.split(b"\r\n", 1)[0].decode("latin1", "ignore")
                ctype = ""
                clen = ""
                for h in head.split(b"\r\n"):
                    hl = h.lower()
                    if hl.startswith(b"content-type:"):
                        ctype = h.split(b":", 1)[1].strip().decode("latin1", "ignore")
                    if hl.startswith(b"content-length:"):
                        clen = h.split(b":", 1)[1].strip().decode("latin1", "ignore")
                rec = {"t": "resp", "status": status, "ctype": ctype, "len": clen,
                       "src": f"{pkt[IP].src}:{sport}" if pkt.haslayer(IP) else ""}
                http_flows.append(rec)
                # save non-HTML bodies (potential table/DLL/bundle)
                if body and "html" not in ctype.lower():
                    _body_n[0] += 1
                    BODIES.mkdir(parents=True, exist_ok=True)
                    fn = BODIES / f"{_body_n[0]:04d}_{(pkt[IP].src if pkt.haslayer(IP) else 'x')}.bin"
                    with open(fn, "ab") as f:
                        f.write(body)
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=180)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    bpf = "tcp port 80 or tcp port 443 or udp port 53 or tcp port 8080"
    ifaces = _capture_ifaces()
    print(f"capturing {args.seconds}s on {len(ifaces)} ifaces (bpf: {bpf})", flush=True)
    for i in ifaces:
        print(f"   - {getattr(i,'description','')} ({getattr(i,'ip',None)})", flush=True)
    print("-> launch the game now (test account)", flush=True)
    sn = AsyncSniffer(iface=ifaces, filter=bpf, prn=_handle, store=True)
    sn.start()
    t0 = time.time()
    try:
        while time.time() - t0 < args.seconds:
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    pkts = sn.stop()
    try:
        wrpcap(str(OUT / "capture.pcap"), pkts)
    except Exception as e:  # noqa: BLE001
        print("pcap save failed:", e, flush=True)
    (OUT / "hosts.json").write_text(json.dumps(
        {"dns": {k: sorted(v) for k, v in hosts["dns"].items()}, "sni": sorted(hosts["sni"])},
        ensure_ascii=False, indent=2), encoding="utf-8")
    with open(OUT / "http_flows.jsonl", "w", encoding="utf-8") as f:
        for r in http_flows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n=== done: {len(pkts)} pkts, {len(http_flows)} http lines, {_body_n[0]} bodies ===")
    print("hosts (dns):", sorted(hosts["dns"]))
    print("hosts (sni):", sorted(hosts["sni"]))
    print(f"artifacts in {OUT}")


if __name__ == "__main__":
    main()
