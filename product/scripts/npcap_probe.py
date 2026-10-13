"""Npcap probe — run on the machine that has Npcap + the game (admin shell).

Validates the recommended capture binding (ctypes -> wpcap.dll, no pip build dep)
and collects what's needed to write NpcapBackend: interface list, each adapter's
datalink type, and (optionally) a short live capture to confirm game packets are
visible. Run it BOTH with VPN on and off — the game may ride a different adapter /
link type in each, and we must confirm Npcap sees real payload (not encrypted VPN
blobs) under VPN.

Usage (admin PowerShell, game running):
    python scripts/npcap_probe.py                 # list interfaces + link types
    python scripts/npcap_probe.py --capture 0 8443  # 5s capture on iface idx 0, port 8443

Paste the output back so the backend can pick the right adapter + datalink.
"""

from __future__ import annotations

import argparse
import ctypes
import sys
from ctypes import POINTER, Structure, c_char, c_char_p, c_int, c_uint, c_void_p, pointer

PCAP_ERRBUF_SIZE = 256


class _BpfProgram(Structure):
    _fields_ = [("bf_len", c_uint), ("bf_insns", c_void_p)]


class _PcapPktHdr(Structure):
    # struct pcap_pkthdr: ts (timeval), caplen, len
    _fields_ = [
        ("ts_sec", ctypes.c_long),
        ("ts_usec", ctypes.c_long),
        ("caplen", c_uint),
        ("len", c_uint),
    ]


class _PcapIf(Structure):
    pass


_PcapIf._fields_ = [
    ("next", POINTER(_PcapIf)),
    ("name", c_char_p),
    ("description", c_char_p),
    ("addresses", c_void_p),
    ("flags", c_uint),
]


def _load_wpcap() -> ctypes.CDLL:
    for name in ("wpcap", "wpcap.dll", r"C:\Windows\System32\Npcap\wpcap.dll"):
        try:
            return ctypes.CDLL(name)
        except OSError:
            continue
    print("[error] wpcap.dll not found — is Npcap installed? (https://npcap.com/#download)")
    sys.exit(2)


def _bind(lib: ctypes.CDLL) -> ctypes.CDLL:
    lib.pcap_findalldevs.argtypes = [POINTER(POINTER(_PcapIf)), c_char_p]
    lib.pcap_findalldevs.restype = c_int
    lib.pcap_open_live.argtypes = [c_char_p, c_int, c_int, c_int, c_char_p]
    lib.pcap_open_live.restype = c_void_p
    lib.pcap_datalink.argtypes = [c_void_p]
    lib.pcap_datalink.restype = c_int
    lib.pcap_datalink_val_to_name.argtypes = [c_int]
    lib.pcap_datalink_val_to_name.restype = c_char_p
    lib.pcap_compile.argtypes = [c_void_p, POINTER(_BpfProgram), c_char_p, c_int, c_uint]
    lib.pcap_compile.restype = c_int
    lib.pcap_setfilter.argtypes = [c_void_p, POINTER(_BpfProgram)]
    lib.pcap_setfilter.restype = c_int
    lib.pcap_next_ex.argtypes = [c_void_p, POINTER(POINTER(_PcapPktHdr)), POINTER(POINTER(c_char))]
    lib.pcap_next_ex.restype = c_int
    lib.pcap_close.argtypes = [c_void_p]
    return lib


def list_devices(lib: ctypes.CDLL) -> list[str]:
    devs = POINTER(_PcapIf)()
    err = ctypes.create_string_buffer(PCAP_ERRBUF_SIZE)
    if lib.pcap_findalldevs(ctypes.byref(devs), err) != 0:
        print("[error] pcap_findalldevs:", err.value.decode(errors="ignore"))
        return []
    names: list[str] = []
    node = devs
    idx = 0
    while node:
        d = node.contents
        name = d.name.decode(errors="ignore") if d.name else ""
        desc = d.description.decode(errors="ignore") if d.description else ""
        names.append(name)
        print(f"[{idx}] {name}\n      {desc}")
        node = d.next
        idx += 1
    return names


def capture(lib: ctypes.CDLL, dev: str, port: int, seconds: int = 5) -> None:
    err = ctypes.create_string_buffer(PCAP_ERRBUF_SIZE)
    handle = lib.pcap_open_live(dev.encode(), 65536, 1, 1000, err)
    if not handle:
        print("[error] pcap_open_live:", err.value.decode(errors="ignore"))
        return
    dlt = lib.pcap_datalink(handle)
    dlt_name = lib.pcap_datalink_val_to_name(dlt)
    print(f"[datalink] {dlt} ({dlt_name.decode() if dlt_name else '?'})")
    if port:
        prog = _BpfProgram()
        bpf = f"tcp and port {int(port)}".encode()
        if lib.pcap_compile(handle, ctypes.byref(prog), bpf, 1, 0xFFFFFFFF) == 0:
            lib.pcap_setfilter(handle, ctypes.byref(prog))
            print(f"[filter] {bpf.decode()}")
        else:
            print("[warn] pcap_compile failed; capturing unfiltered")
    import time as _t  # local to keep module import-safe in the engine env
    hdr = POINTER(_PcapPktHdr)()
    data = POINTER(c_char)()
    seen = with_payload = 0
    # Wall-clock deadline so a wrong/idle adapter exits cleanly instead of hanging.
    start = _t.monotonic()
    print(f"[capture] {seconds}s on {dev} ... (Ctrl+C to stop early)")
    while _t.monotonic() - start < seconds:
        rc = lib.pcap_next_ex(handle, ctypes.byref(hdr), ctypes.byref(data))
        if rc == 1:
            seen += 1
            if hdr.contents.caplen > 54:  # rough: eth+ip+tcp headers
                with_payload += 1
            if seen >= 20000:
                break
        elif rc == 0:
            continue  # read timeout (1000ms); loop re-checks wall clock
        else:
            break  # error / handle closed
    print(f"[result] packets={seen} with_payload~={with_payload} "
          f"(>0 with_payload ⇒ Npcap sees game data on this adapter)")
    lib.pcap_close(handle)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", nargs=2, metavar=("IFACE_IDX", "PORT"),
                    help="capture on interface index for ~5s filtering tcp port")
    args = ap.parse_args()
    lib = _bind(_load_wpcap())
    print("=== Npcap interfaces ===")
    names = list_devices(lib)
    if args.capture:
        idx, port = int(args.capture[0]), int(args.capture[1])
        if 0 <= idx < len(names):
            capture(lib, names[idx], port)
        else:
            print(f"[error] interface index {idx} out of range (0..{len(names)-1})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
