"""Capture-backend abstraction for live packet sniffing.

Decouples *how* packets are captured (WinDivert today, Npcap next) from the
fatbeans parse/frame-gate pipeline. A backend only needs to yield CapturedPacket
(TCP payload + 4-tuple); flow→process attribution and frame reassembly stay
backend-agnostic.

WinDivert delivers already-parsed packets (payload directly accessible). Npcap /
libpcap delivers raw link-layer frames, so the Npcap backend must parse
Ethernet/IPv4/TCP itself — that parser (`parse_frame`) and the BPF filter builder
(`build_bpf_filter`) are pure and unit-tested here. The live wpcap.dll wiring
(`NpcapBackend`) needs Npcap installed + real traffic, so it is verified on the
user's machine, not in CI.

Migration rationale (2026-06-15, user decision): switch to Npcap — Microsoft
co-signed driver (Wireshark-grade, AV-whitelisted), higher-throughput passive
capture (lower settlement-burst drop than WinDivert's default SNIFF queue), and
free LGPL-style runtime use. Commercialization (Npcap OEM redistribution license)
is deferred; revisit only if the tool is sold.
"""

from __future__ import annotations

import os
import socket
import struct
from dataclasses import dataclass
from typing import Iterator, Protocol

# libpcap link-layer types we may see on Windows capture handles.
DLT_EN10MB = 1   # Ethernet II
DLT_RAW = 12     # raw IP (some loopback/VPN adapters)
DLT_NULL = 0     # BSD loopback (4-byte family header)

_ETHERTYPE_IPV4 = 0x0800
_IPPROTO_TCP = 6
# 802.1Q / QinQ VLAN tag TPIDs. Intel Wi-Fi drivers (Wireless-AC 9462 / Wi-Fi 6
# AX101, …) hand RECEIVED frames to Npcap still carrying an 802.1p priority tag
# (TPID 0x8100, usually VID 0) while TRANSMITTED frames are captured untagged.
# Without stripping it the inner ethertype sits 4 bytes deeper, so parse_frame read
# every inbound (REV) game frame as non-IPv4 and dropped it -> the whole inbound
# direction was lost -> "整局不出数" on those NICs. 2026-07-05 repro on group
# members 笑待/1234! pcapng: REV 0/101 survived parse_frame; after stripping the tag
# 97/97 survived and settlement 0x2D was recovered. Wireshark shows these because it
# parses VLAN natively. Strip up to 2 stacked tags (QinQ) before reading the ethertype.
_ETHERTYPE_VLAN = frozenset((0x8100, 0x88A8, 0x9100))
_MAX_VLAN_TAGS = 2


@dataclass(frozen=True)
class CapturedPacket:
    """Minimal capture-backend output: TCP payload + connection 4-tuple.

    ``seq`` is the TCP sequence number of the FIRST payload byte. It was always present in
    the header we parse, just discarded — and without it a reassembler cannot tell "these
    bytes arrived out of order" from "these bytes never arrived". On Npcap it feeds the
    bounded TCP seq reassembler before GameFrameGate as well as the read-only StreamSeqDiag.
    WinDivert currently has no real seq source and stays on its legacy arrival-order path.

    ``ip_truncated`` = the IPv4 total_length field claims MORE bytes than we captured, i.e.
    the payload handed downstream is SHORT. That is a silent byte-loss path (it makes a frame
    permanently un-completable), so it is counted rather than swallowed.
    """

    src_ip: str
    src_port: int
    dst_ip: str
    dst_port: int
    payload: bytes
    timestamp: float | None = None
    seq: int = 0
    ip_truncated: bool = False


def _parse_ipv4_tcp(ip_bytes: bytes, timestamp: float | None) -> CapturedPacket | None:
    if len(ip_bytes) < 20:
        return None
    ver_ihl = ip_bytes[0]
    if (ver_ihl >> 4) != 4:
        return None
    ihl = (ver_ihl & 0x0F) * 4
    if ihl < 20 or len(ip_bytes) < ihl:
        return None
    if ip_bytes[9] != _IPPROTO_TCP:
        return None
    total_len = struct.unpack("!H", ip_bytes[2:4])[0]
    # total_len > what we hold => the capture is SHORT of what the sender put on the wire, so
    # `payload` below is a truncated segment. Flag (don't drop): dropping would lose the bytes
    # we DID get, and the frame is already unrecoverable either way — but the count tells us
    # whether truncation is the wedge's byte-loss path.
    ip_truncated = bool(total_len) and total_len > len(ip_bytes)
    if total_len and total_len <= len(ip_bytes):
        ip_bytes = ip_bytes[:total_len]
    src_ip = socket.inet_ntoa(ip_bytes[12:16])
    dst_ip = socket.inet_ntoa(ip_bytes[16:20])
    tcp = ip_bytes[ihl:]
    if len(tcp) < 20:
        return None
    src_port, dst_port = struct.unpack("!HH", tcp[0:4])
    seq = struct.unpack("!I", tcp[4:8])[0]
    data_offset = (tcp[12] >> 4) * 4
    if data_offset < 20 or len(tcp) < data_offset:
        return None
    payload = tcp[data_offset:]
    if not payload:
        return None
    return CapturedPacket(
        src_ip, int(src_port), dst_ip, int(dst_port), payload, timestamp,
        int(seq), ip_truncated,
    )


def parse_frame(data: bytes, linktype: int, *, timestamp: float | None = None) -> CapturedPacket | None:
    """Parse a raw libpcap frame into a CapturedPacket, or None if not TCP-with-payload.

    Handles Ethernet II / raw-IP / BSD-loopback link types. IPv4 only (the game
    speaks IPv4); IPv6 frames return None and are simply ignored upstream.
    """
    if not data:
        return None
    if linktype == DLT_EN10MB:
        if len(data) < 14:
            return None
        off = 12
        ethertype = struct.unpack("!H", data[off:off + 2])[0]
        tags = 0
        # Strip 802.1Q/QinQ VLAN tags (Intel Wi-Fi RX priority tagging, see above)
        # before reading the real ethertype; each tag is 4 bytes (2 TPID + 2 TCI).
        while ethertype in _ETHERTYPE_VLAN and tags < _MAX_VLAN_TAGS and len(data) >= off + 6:
            off += 4
            ethertype = struct.unpack("!H", data[off:off + 2])[0]
            tags += 1
        if ethertype != _ETHERTYPE_IPV4:
            return None
        return _parse_ipv4_tcp(data[off + 2:], timestamp)
    if linktype == DLT_RAW:
        return _parse_ipv4_tcp(data, timestamp)
    if linktype == DLT_NULL:
        if len(data) < 4:
            return None
        return _parse_ipv4_tcp(data[4:], timestamp)
    return None


def build_bpf_filter(server_ports: list[int] | None, *, broad: bool = False) -> str:
    """Translate the WinDivert port filter intent into a libpcap BPF expression.

    WinDivert: ``tcp.SrcPort == X or tcp.DstPort == X`` per game server port.
    BPF:       ``tcp and (port X or port Y ...)`` ('port' matches src or dst).

    VLAN-aware (see parse_frame / the 802.1Q note above): Intel Wi-Fi drivers hand
    RECEIVED frames to Npcap still carrying an 802.1p tag (TPID 0x8100). A plain
    ``tcp ...`` BPF assumes the IP ethertype sits at offset 12, so it does NOT match a
    VLAN-tagged frame — the kernel filter would drop every inbound REV frame BEFORE
    parse_frame's userspace VLAN strip ever runs, i.e. the strip alone fixes offline
    replay but is a no-op live (verified: ``pcap_offline_filter`` on wpcap.dll rejects a
    tagged frame under a plain ``tcp and port X`` filter). Add ``or (vlan and <same>)``
    so the kernel admits the single-tagged inbound frames too; parse_frame then strips
    the tag. One tag matches the observed Intel case (parse_frame strips up to 2 as
    defense-in-depth; libpcap's nested ``vlan`` does not cleanly match stacked QinQ, and
    QinQ is not the observed symptom). Memory: npcap-inbound-rev-vlan-tag-dropped-rootcause.
    """
    ports = sorted({int(p) for p in (server_ports or []) if int(p) > 0})
    if broad or not ports:
        return "tcp or (vlan and tcp)"
    clause = " or ".join(f"port {p}" for p in ports)
    base = f"tcp and ({clause})"
    return f"({base}) or (vlan and {base})"


def bpf_without_vlan(bpf: str) -> str:
    """Drop the ``or (vlan and ...)`` alternative from a build_bpf_filter() expression.

    The libpcap ``vlan`` primitive is Ethernet-only: DLT_RAW (raw IP / accelerator TUN) and
    DLT_NULL (BSD loopback) reject it ("no VLAN support for Raw IP") and the WHOLE compile fails,
    which would leave the capture running UNFILTERED (every packet crosses to user space, raising
    settlement-burst drop risk). A VLAN tag can't occur on those link types anyway, so the backend
    falls back to this plain filter there. No-op when the expression carries no vlan alternative.
    """
    marker = " or (vlan and "
    i = bpf.find(marker)
    if i == -1:
        return bpf
    head = bpf[:i]
    return head[1:-1] if head.startswith("(") and head.endswith(")") else head


class CaptureBackend(Protocol):
    """A live packet source. Implementations: WinDivertBackend, NpcapBackend."""

    def __enter__(self) -> "CaptureBackend": ...
    def __exit__(self, *exc: object) -> None: ...
    def packets(self) -> Iterator[CapturedPacket]:
        """Yield captured TCP-payload packets until the handle is closed."""
        ...


# --- NpcapBackend (live wiring; ctypes -> wpcap.dll, no pip build dep) ----------
# Verified on the user's machine (Npcap installed + game running). In CI there is
# no wpcap.dll, so importing this module stays cheap and the class only touches
# ctypes at __enter__.

import ctypes as _ct  # noqa: E402
import struct as _struct_unused  # noqa: F401,E402  (parse_frame already imports struct)

PCAP_ERRBUF_SIZE = 256


class _PcapPktHdr(_ct.Structure):
    _fields_ = [
        ("ts_sec", _ct.c_long),
        ("ts_usec", _ct.c_long),
        ("caplen", _ct.c_uint),
        ("len", _ct.c_uint),
    ]


class _BpfProgram(_ct.Structure):
    _fields_ = [("bf_len", _ct.c_uint), ("bf_insns", _ct.c_void_p)]


class _PcapStat(_ct.Structure):
    # pcap_stats() out: kernel-side capture counters. ps_recv = packets accepted
    # by the filter; ps_drop = packets dropped by the kernel buffer because the
    # user-space drain lagged (the invisible loss path behind 5606's 16/33 item
    # settlement under-count); ps_ifdrop = interface-level drops.
    _fields_ = [
        ("ps_recv", _ct.c_uint),
        ("ps_drop", _ct.c_uint),
        ("ps_ifdrop", _ct.c_uint),
    ]


def _load_wpcap() -> "_ct.CDLL":
    for name in ("wpcap", "wpcap.dll", r"C:\Windows\System32\Npcap\wpcap.dll"):
        try:
            return _ct.CDLL(name)
        except OSError:
            continue
    raise OSError("wpcap.dll not found — install Npcap (https://npcap.com/#download)")


class _PcapIf(_ct.Structure):
    pass


_PcapIf._fields_ = [
    ("next", _ct.POINTER(_PcapIf)),
    ("name", _ct.c_char_p),
    ("description", _ct.c_char_p),
    ("addresses", _ct.c_void_p),
    ("flags", _ct.c_uint),
]


def list_devices() -> list[tuple[str, str]]:
    """Return [(npf_name, description), ...] from pcap_findalldevs."""
    lib = _load_wpcap()
    lib.pcap_findalldevs.argtypes = [_ct.POINTER(_ct.POINTER(_PcapIf)), _ct.c_char_p]
    lib.pcap_findalldevs.restype = _ct.c_int
    devs = _ct.POINTER(_PcapIf)()
    err = _ct.create_string_buffer(PCAP_ERRBUF_SIZE)
    if lib.pcap_findalldevs(_ct.byref(devs), err) != 0:
        raise OSError(f"pcap_findalldevs failed: {err.value.decode(errors='ignore')}")
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


# Virtual/uninteresting adapters to skip in multi-device capture (substring match
# on description). Loopback is kept (Clash system-proxy plaintext can land there).
SKIP_ADAPTER_SUBSTRINGS = (
    "WAN Miniport", "Wi-Fi Direct", "Bluetooth", "Microsoft Wi-Fi Direct",
)


def default_capture_devices() -> list[tuple[str, str]]:
    """Real adapters worth capturing on (physical + tunnels + loopback), virtual skipped."""
    out: list[tuple[str, str]] = []
    for name, desc in list_devices():
        if any(s.lower() in desc.lower() for s in SKIP_ADAPTER_SUBSTRINGS):
            continue
        out.append((name, desc))
    return out


def _clean_pcap_error(value: bytes | str) -> str:
    """Collapse libpcap error text into a bounded, log-safe summary."""

    if isinstance(value, bytes):
        text = value.decode(errors="ignore")
    else:
        text = str(value or "")
    text = " ".join(text.replace("\x00", " ").split())
    return text[:240] or "unknown pcap_open_live error"


def _is_promiscuous_mode_error(message: str) -> bool:
    """Return whether an open failure is specifically eligible for non-promisc retry."""

    lowered = str(message or "").lower()
    return any(
        marker in lowered
        for marker in (
            "promiscuous",
            "promisc mode",
            "hardware filter",
        )
    )


class NpcapDeviceOpenError(OSError):
    """Structured pcap_open_live failure used by the multi-device monitor."""

    def __init__(
        self,
        primary_error: str,
        *,
        fallback_error: str | None = None,
        non_promisc_fallback_attempted: bool = False,
    ) -> None:
        self.primary_error = _clean_pcap_error(primary_error)
        self.fallback_error = (
            _clean_pcap_error(fallback_error) if fallback_error is not None else None
        )
        self.non_promisc_fallback_attempted = bool(
            non_promisc_fallback_attempted
        )
        summary = f"pcap_open_live promisc=1 failed: {self.primary_error}"
        if self.non_promisc_fallback_attempted:
            summary += (
                "; promisc=0 fallback failed: "
                f"{self.fallback_error or 'unknown pcap_open_live error'}"
            )
        super().__init__(summary)


class NpcapBackend:
    """Passive capture via Npcap/libpcap (wpcap.dll). Drains in a worker thread so
    the kernel buffer never overflows during settlement bursts (the WinDivert
    SNIFF-queue drop fix); parse_frame() runs in the consumer."""

    def __init__(self, device: str, *, bpf: str = "tcp", snaplen: int = 65536,
                 queue_max: int = 20000, kernel_buffer_bytes: int | None = None):
        self.device = device
        self.bpf = bpf
        self.snaplen = snaplen
        self._queue_max = queue_max
        # Kernel capture buffer. The default WinPcap/Npcap kernel buffer (~1MB) can overflow
        # on the settlement burst (the 45-item loot dump is a multi-segment ~9-25KB message
        # arriving back-to-back), silently dropping segments BEFORE the drain thread reads them
        # -> incomplete settlement (4409 live repro: only 13/45 items captured). Enlarge to 32MB
        # so a burst has headroom. Env override BIDKING_NPCAP_BUFFER_MB; 0 disables (keep default).
        if kernel_buffer_bytes is None:
            try:
                _mb = int(os.environ.get("BIDKING_NPCAP_BUFFER_MB", "32"))
            except (TypeError, ValueError):
                _mb = 32
            kernel_buffer_bytes = max(0, _mb) * 1024 * 1024
        self._kernel_buffer_bytes = kernel_buffer_bytes
        self._lib = None
        self._handle = None
        self._linktype = DLT_EN10MB
        self._closed = False
        self.promiscuous_mode: bool | None = None
        self.non_promisc_fallback_attempted = False
        # Byte-loss paths that used to be SILENT. kernel_drop/queue_full are already counted and
        # are 0 across every 0711 diag package, yet the gate still wedges on frames whose tail
        # never arrived — so the loss must be on a path nobody was counting. These are those paths:
        #   snap_truncated  npcap handed us fewer bytes than the wire carried (caplen < len)
        #   ip_truncated    the IPv4 total_length exceeds what we hold -> short payload
        #   parse_skipped   parse_frame returned None. Mostly pure ACKs (no payload) and thus
        #                   expected to be LARGE and harmless — it is the DELTA during a wedge
        #                   that would matter, not the absolute value.
        self.snap_truncated = 0
        self.ip_truncated = 0
        self.parse_skipped = 0

    def __enter__(self) -> "NpcapBackend":
        lib = _load_wpcap()
        lib.pcap_open_live.argtypes = [_ct.c_char_p, _ct.c_int, _ct.c_int, _ct.c_int, _ct.c_char_p]
        lib.pcap_open_live.restype = _ct.c_void_p
        lib.pcap_datalink.argtypes = [_ct.c_void_p]
        lib.pcap_datalink.restype = _ct.c_int
        lib.pcap_compile.argtypes = [_ct.c_void_p, _ct.POINTER(_BpfProgram), _ct.c_char_p, _ct.c_int, _ct.c_uint]
        lib.pcap_compile.restype = _ct.c_int
        lib.pcap_setfilter.argtypes = [_ct.c_void_p, _ct.POINTER(_BpfProgram)]
        lib.pcap_setfilter.restype = _ct.c_int
        lib.pcap_next_ex.argtypes = [
            _ct.c_void_p, _ct.POINTER(_ct.POINTER(_PcapPktHdr)), _ct.POINTER(_ct.POINTER(_ct.c_char))
        ]
        lib.pcap_next_ex.restype = _ct.c_int
        lib.pcap_close.argtypes = [_ct.c_void_p]
        # pcap_stats: read kernel capture counters (recv/drop). Wired here, called
        # from the drain thread via stats() so the handle is never touched cross-thread.
        try:
            lib.pcap_stats.argtypes = [_ct.c_void_p, _ct.POINTER(_PcapStat)]
            lib.pcap_stats.restype = _ct.c_int
        except (AttributeError, OSError, ValueError):
            pass  # symbol absent on stock libpcap -> stats() returns None
        err = _ct.create_string_buffer(PCAP_ERRBUF_SIZE)
        handle = lib.pcap_open_live(self.device.encode(), self.snaplen, 1, 1000, err)
        if handle:
            self.promiscuous_mode = True
        else:
            primary_error = _clean_pcap_error(err.value)
            if not _is_promiscuous_mode_error(primary_error):
                raise NpcapDeviceOpenError(primary_error)

            # Some physical NIC drivers reject only the hardware promiscuous-mode
            # request. Retrying the same handle cannot recover, so use a fresh
            # pcap_open_live call and a fresh errbuf in ordinary capture mode.
            self.non_promisc_fallback_attempted = True
            fallback_err = _ct.create_string_buffer(PCAP_ERRBUF_SIZE)
            handle = lib.pcap_open_live(
                self.device.encode(), self.snaplen, 0, 1000, fallback_err
            )
            if not handle:
                raise NpcapDeviceOpenError(
                    primary_error,
                    fallback_error=_clean_pcap_error(fallback_err.value),
                    non_promisc_fallback_attempted=True,
                )
            self.promiscuous_mode = False
        self._lib, self._handle = lib, handle
        # Enlarge the kernel buffer (WinPcap/Npcap pcap_setbuff extension) to survive settlement
        # bursts. Best-effort: the symbol is absent on stock libpcap (non-Windows) -> skip.
        if self._kernel_buffer_bytes and self._kernel_buffer_bytes > 0:
            try:
                lib.pcap_setbuff.argtypes = [_ct.c_void_p, _ct.c_int]
                lib.pcap_setbuff.restype = _ct.c_int
                lib.pcap_setbuff(handle, int(self._kernel_buffer_bytes))
            except (AttributeError, OSError, ValueError):
                pass
        self._linktype = int(lib.pcap_datalink(handle))
        if self.bpf:
            # Try the (VLAN-aware) filter; on a non-Ethernet link (DLT_RAW TUN / DLT_NULL loopback)
            # the `vlan` clause fails to compile, so retry with it stripped rather than fall through
            # to an UNFILTERED handle (every packet to user space -> higher settlement-burst drop).
            for _candidate in (self.bpf, bpf_without_vlan(self.bpf)):
                prog = _BpfProgram()
                if lib.pcap_compile(handle, _ct.byref(prog), _candidate.encode(), 1, 0xFFFFFFFF) == 0:
                    lib.pcap_setfilter(handle, _ct.byref(prog))
                    break
        return self

    @property
    def linktype(self) -> int:
        return self._linktype

    @property
    def capture_mode(self) -> str:
        if self.promiscuous_mode is True:
            return "promisc"
        if self.promiscuous_mode is False:
            return "non_promisc_fallback"
        return "unopened"

    def stats(self) -> dict[str, int] | None:
        """Kernel capture counters {recv, drop, ifdrop}, or None if unavailable.

        Must be called from the drain thread that owns the handle (pcap_stats is
        not guaranteed cross-thread safe). drop > 0 means the kernel buffer
        dropped packets the user-space drain never saw — the invisible loss path.
        """
        if not self._lib or not self._handle:
            return None
        try:
            st = _PcapStat()
            if self._lib.pcap_stats(self._handle, _ct.byref(st)) == 0:
                return {
                    "recv": int(st.ps_recv),
                    "drop": int(st.ps_drop),
                    "ifdrop": int(st.ps_ifdrop),
                    "snap_truncated": int(self.snap_truncated),
                    "ip_truncated": int(self.ip_truncated),
                    "parse_skipped": int(self.parse_skipped),
                }
        except Exception:  # noqa: BLE001 — stats must never break capture
            pass
        return None

    def packets(self):
        lib, handle = self._lib, self._handle
        hdr = _ct.POINTER(_PcapPktHdr)()
        data = _ct.POINTER(_ct.c_char)()
        while not self._closed:
            rc = lib.pcap_next_ex(handle, _ct.byref(hdr), _ct.byref(data))
            if rc == 1:
                caplen = int(hdr.contents.caplen)
                wirelen = int(hdr.contents.len)
                if wirelen and caplen < wirelen:
                    self.snap_truncated += 1
                raw = _ct.string_at(data, caplen)
                pkt = parse_frame(raw, self._linktype, timestamp=float(hdr.contents.ts_sec))
                if pkt is None:
                    self.parse_skipped += 1
                    continue
                if pkt.ip_truncated:
                    self.ip_truncated += 1
                yield pkt
            elif rc == 0:
                continue  # 1000ms read timeout; loop
            else:
                break  # error / closed

    def __exit__(self, *exc: object) -> None:
        self._closed = True
        if self._lib and self._handle:
            try:
                self._lib.pcap_close(self._handle)
            except Exception:
                pass
