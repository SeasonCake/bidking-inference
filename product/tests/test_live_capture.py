"""Unit tests for the capture-backend pure core (frame parse + BPF filter)."""

from __future__ import annotations

import socket
import struct

import pytest

from bidking_lab.live import capture as capture_module

from bidking_lab.live.capture import (
    DLT_EN10MB,
    DLT_NULL,
    DLT_RAW,
    NpcapBackend,
    NpcapDeviceOpenError,
    bpf_without_vlan,
    build_bpf_filter,
    parse_frame,
)


class _FakeFunction:
    def __init__(self, callback):
        self.callback = callback
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.callback(*args)


class _FakePcapLib:
    def __init__(self, open_plan: dict[int, tuple[int, str]]) -> None:
        self.open_plan = dict(open_plan)
        self.open_modes: list[int] = []
        self.compile_calls = 0
        self.filter_calls = 0
        self.buffer_calls = 0
        self.closed: list[int] = []
        self.pcap_open_live = _FakeFunction(self._open)
        self.pcap_datalink = _FakeFunction(lambda handle: DLT_EN10MB)
        self.pcap_compile = _FakeFunction(self._compile)
        self.pcap_setfilter = _FakeFunction(self._setfilter)
        self.pcap_next_ex = _FakeFunction(lambda *args: -1)
        self.pcap_close = _FakeFunction(self.closed.append)
        self.pcap_stats = _FakeFunction(lambda *args: -1)
        self.pcap_setbuff = _FakeFunction(self._setbuff)

    def _open(self, device, snaplen, promisc, timeout, errbuf):
        del device, snaplen, timeout
        self.open_modes.append(int(promisc))
        handle, message = self.open_plan[int(promisc)]
        if not handle:
            errbuf.value = message.encode("utf-8")
        return handle

    def _compile(self, *args):
        self.compile_calls += 1
        return 0

    def _setfilter(self, *args):
        self.filter_calls += 1
        return 0

    def _setbuff(self, *args):
        self.buffer_calls += 1
        return 0


def _ipv4_tcp(
    src_ip: str, src_port: int, dst_ip: str, dst_port: int, payload: bytes,
    *, seq: int = 0, claim_len: int | None = None,
) -> bytes:
    """Build an IPv4/TCP packet. ``claim_len`` overrides the IP total_length field so a
    TRUNCATED capture (header claims more bytes than are present) can be simulated."""
    tcp_header = struct.pack(
        "!HHIIBBHHH",
        src_port, dst_port, seq, 0,
        (5 << 4), 0x18, 0, 0, 0,  # data offset 5 words (20 bytes), flags PSH+ACK
    )
    tcp = tcp_header + payload
    total_len = 20 + len(tcp) if claim_len is None else claim_len
    ip_header = struct.pack(
        "!BBHHHBBH4s4s",
        0x45, 0, total_len, 0, 0, 64, 6, 0,
        socket.inet_aton(src_ip), socket.inet_aton(dst_ip),
    )
    return ip_header + tcp


def test_parse_frame_ethernet_extracts_payload_and_tuple() -> None:
    ip = _ipv4_tcp("10.0.0.1", 5000, "10.0.0.2", 443, b"hello-game")
    eth = b"\xaa" * 6 + b"\xbb" * 6 + struct.pack("!H", 0x0800) + ip
    pkt = parse_frame(eth, DLT_EN10MB)
    assert pkt is not None
    assert (pkt.src_ip, pkt.src_port, pkt.dst_ip, pkt.dst_port) == ("10.0.0.1", 5000, "10.0.0.2", 443)
    assert pkt.payload == b"hello-game"


def test_parse_frame_extracts_tcp_seq() -> None:
    """⭐seq 一直就在 TCP 头里(tcp[4:8]), 只是以前被丢掉。没有它就无法区分
    "字节乱序到达" 和 "字节根本没到" —— 而这正是楔死根因的分水岭。"""
    ip = _ipv4_tcp("10.0.0.1", 5000, "10.0.0.2", 443, b"abc", seq=0xDEADBEEF)
    eth = b"\xaa" * 6 + b"\xbb" * 6 + struct.pack("!H", 0x0800) + ip
    pkt = parse_frame(eth, DLT_EN10MB)
    assert pkt.seq == 0xDEADBEEF
    assert pkt.ip_truncated is False


def test_parse_frame_seq_survives_vlan_strip() -> None:
    """入站 REV 帧带 802.1Q 标签(Intel Wi-Fi RX)。seq 必须在剥标签后依然读对 ——
    否则恰恰在出问题的那个方向上取不到证据。"""
    ip = _ipv4_tcp("10.0.0.1", 5000, "10.0.0.2", 443, b"z", seq=12345)
    vlan = struct.pack("!HH", 0x8100, 0x0000) + struct.pack("!H", 0x0800)
    pkt = parse_frame(b"\xaa" * 6 + b"\xbb" * 6 + vlan + ip, DLT_EN10MB)
    assert pkt.seq == 12345


def test_parse_frame_flags_truncated_capture() -> None:
    """IP total_length 声称的字节数 > 实际抓到的 -> payload 是短的 = 一条静默丢字节路径。
    只标记不丢弃: 丢掉会把已经拿到的字节也扔了, 而这个计数正是要回答"字节是不是在这里没的"。"""
    ip = _ipv4_tcp("10.0.0.1", 5000, "10.0.0.2", 443, b"short", claim_len=9999)
    pkt = parse_frame(b"\xaa" * 6 + b"\xbb" * 6 + struct.pack("!H", 0x0800) + ip, DLT_EN10MB)
    assert pkt is not None and pkt.ip_truncated is True
    assert pkt.payload == b"short"       # 已抓到的字节仍然交出去


def test_parse_frame_strips_8021q_vlan_tag() -> None:
    # Intel Wi-Fi RX priority tagging: inbound frames carry an 802.1Q tag
    # (TPID 0x8100, TCI 0x0000) before the real IPv4 ethertype. Regression for the
    # 2026-07-05 "整局不出数" root cause (inbound REV dropped as non-IPv4).
    ip = _ipv4_tcp("8.133.195.27", 10000, "192.168.0.106", 53078, b"rev-game-frame")
    vlan = struct.pack("!HH", 0x8100, 0x0000) + struct.pack("!H", 0x0800)
    eth = b"\xaa" * 6 + b"\xbb" * 6 + vlan + ip
    pkt = parse_frame(eth, DLT_EN10MB)
    assert pkt is not None
    assert (pkt.src_ip, pkt.src_port) == ("8.133.195.27", 10000)
    assert pkt.payload == b"rev-game-frame"


def test_parse_frame_strips_stacked_qinq_and_rejects_non_ipv4_after_vlan() -> None:
    ip = _ipv4_tcp("10.0.0.1", 1, "10.0.0.2", 2, b"q")
    # QinQ: outer 0x88a8 + inner 0x8100 + IPv4
    stacked = struct.pack("!HH", 0x88A8, 0x0000) + struct.pack("!HH", 0x8100, 0x0000) + struct.pack("!H", 0x0800)
    assert parse_frame(b"\x00" * 12 + stacked + ip, DLT_EN10MB).payload == b"q"
    # VLAN-tagged non-IPv4 (e.g. ARP 0x0806) is still ignored, not misparsed.
    vlan_arp = struct.pack("!HH", 0x8100, 0x0000) + struct.pack("!H", 0x0806) + b"\x00" * 28
    assert parse_frame(b"\x00" * 12 + vlan_arp, DLT_EN10MB) is None


def test_parse_frame_raw_ip_and_loopback() -> None:
    ip = _ipv4_tcp("1.2.3.4", 80, "5.6.7.8", 9000, b"x")
    assert parse_frame(ip, DLT_RAW).payload == b"x"
    assert parse_frame(struct.pack("=I", 2) + ip, DLT_NULL).src_port == 80


def test_parse_frame_skips_non_tcp_and_empty_payload() -> None:
    # ARP-ish ethertype -> ignored
    eth = b"\x00" * 12 + struct.pack("!H", 0x0806) + b"\x00" * 28
    assert parse_frame(eth, DLT_EN10MB) is None
    # TCP with no payload (pure ACK) -> None
    ip = _ipv4_tcp("1.1.1.1", 1, "2.2.2.2", 2, b"")
    assert parse_frame(ip, DLT_RAW) is None


def test_build_bpf_filter() -> None:
    # VLAN-aware: each term is duplicated behind `vlan` so the kernel filter admits the
    # 802.1Q-tagged inbound frames (Intel Wi-Fi RX tagging) that a plain `tcp ...` filter
    # would drop before parse_frame's userspace VLAN strip (see build_bpf_filter docstring).
    assert build_bpf_filter([8443, 8443, 9000]) == (
        "(tcp and (port 8443 or port 9000)) or (vlan and tcp and (port 8443 or port 9000))"
    )
    assert build_bpf_filter([]) == "tcp or (vlan and tcp)"
    assert build_bpf_filter([8443], broad=True) == "tcp or (vlan and tcp)"


def test_bpf_without_vlan_strips_vlan_alternative() -> None:
    # The `vlan` clause is Ethernet-only; DLT_RAW/DLT_NULL reject it. The backend falls back to
    # this plain form there so those links keep kernel filtering instead of running unfiltered.
    assert bpf_without_vlan("(tcp and (port 10000)) or (vlan and tcp and (port 10000))") == (
        "tcp and (port 10000)"
    )


def test_npcap_promisc_success_does_not_retry(monkeypatch) -> None:
    fake = _FakePcapLib({1: (101, ""), 0: (102, "")})
    monkeypatch.setattr(capture_module, "_load_wpcap", lambda: fake)

    with NpcapBackend("device", bpf="tcp") as backend:
        assert backend.capture_mode == "promisc"
        assert backend.non_promisc_fallback_attempted is False

    assert fake.open_modes == [1]
    assert fake.buffer_calls == 1
    assert fake.compile_calls == 1
    assert fake.filter_calls == 1
    assert fake.closed == [101]


def test_npcap_promisc_hardware_filter_error_retries_non_promisc(monkeypatch) -> None:
    fake = _FakePcapLib(
        {
            1: (0, "failed to set hardware filter to promiscuous mode"),
            0: (202, ""),
        }
    )
    monkeypatch.setattr(capture_module, "_load_wpcap", lambda: fake)

    with NpcapBackend("device", bpf="tcp") as backend:
        assert backend.capture_mode == "non_promisc_fallback"
        assert backend.promiscuous_mode is False
        assert backend.non_promisc_fallback_attempted is True

    assert fake.open_modes == [1, 0]
    assert fake.buffer_calls == 1
    assert fake.compile_calls == 1
    assert fake.filter_calls == 1
    assert fake.closed == [202]


def test_npcap_both_open_modes_fail_with_structured_summary(monkeypatch) -> None:
    fake = _FakePcapLib(
        {
            1: (0, "failed to set hardware filter to promiscuous mode"),
            0: (0, "ordinary capture denied"),
        }
    )
    monkeypatch.setattr(capture_module, "_load_wpcap", lambda: fake)

    with pytest.raises(NpcapDeviceOpenError) as caught:
        NpcapBackend("device").__enter__()

    assert fake.open_modes == [1, 0]
    assert caught.value.non_promisc_fallback_attempted is True
    assert "promisc=1 failed" in str(caught.value)
    assert "promisc=0 fallback failed" in str(caught.value)


def test_npcap_non_promisc_unrelated_error_is_not_retried(monkeypatch) -> None:
    fake = _FakePcapLib({1: (0, "access denied"), 0: (303, "")})
    monkeypatch.setattr(capture_module, "_load_wpcap", lambda: fake)

    with pytest.raises(NpcapDeviceOpenError) as caught:
        NpcapBackend("device").__enter__()

    assert fake.open_modes == [1]
    assert caught.value.non_promisc_fallback_attempted is False
    assert bpf_without_vlan("tcp or (vlan and tcp)") == "tcp"
    # No-op when there is no vlan alternative (already plain).
    assert bpf_without_vlan("tcp and (port 10000)") == "tcp and (port 10000)"
    assert bpf_without_vlan("tcp") == "tcp"


def test_live_bpf_admits_vlan_tagged_inbound_frame() -> None:
    """The kernel BPF must MATCH an 802.1Q-tagged TCP frame, else inbound REV frames are
    dropped before parse_frame's userspace VLAN strip (whole-match no-data root cause).

    Compiles build_bpf_filter's output through the real wpcap.dll BPF engine
    (pcap_offline_filter) — the string assertion above can't catch a libpcap semantics
    regression. wpcap.dll is Windows/Npcap-only, so SKIP where absent (CI/non-Windows).
    """
    import ctypes as ct

    import pytest

    try:
        from bidking_lab.live.capture import _BpfProgram, _PcapPktHdr, _load_wpcap
        lib = _load_wpcap()
    except OSError:
        pytest.skip("wpcap.dll absent (non-Windows / Npcap not installed) — live-only gate")

    lib.pcap_open_dead.argtypes = [ct.c_int, ct.c_int]
    lib.pcap_open_dead.restype = ct.c_void_p
    lib.pcap_compile.argtypes = [ct.c_void_p, ct.POINTER(_BpfProgram), ct.c_char_p, ct.c_int, ct.c_uint]
    lib.pcap_compile.restype = ct.c_int
    lib.pcap_offline_filter.argtypes = [ct.POINTER(_BpfProgram), ct.POINTER(_PcapPktHdr), ct.c_char_p]
    lib.pcap_offline_filter.restype = ct.c_uint
    lib.pcap_close.argtypes = [ct.c_void_p]

    ip_tcp = _ipv4_tcp("10.0.0.1", 40000, "10.0.0.2", 10000, b"x")
    untagged = b"\xaa" * 6 + b"\xbb" * 6 + struct.pack("!H", 0x0800) + ip_tcp
    vlan_tagged = (b"\xaa" * 6 + b"\xbb" * 6 + struct.pack("!HH", 0x8100, 0x0000)
                   + struct.pack("!H", 0x0800) + ip_tcp)

    def _matches(bpf: str, pkt: bytes) -> bool:
        handle = lib.pcap_open_dead(DLT_EN10MB, 65536)
        prog = _BpfProgram()
        assert lib.pcap_compile(handle, ct.byref(prog), bpf.encode(), 1, 0xFFFFFFFF) == 0, bpf
        hdr = _PcapPktHdr(0, 0, len(pkt), len(pkt))
        r = lib.pcap_offline_filter(ct.byref(prog), ct.byref(hdr), pkt)
        lib.pcap_close(handle)
        return bool(r)

    bpf = build_bpf_filter([10000])
    assert _matches(bpf, untagged), "VLAN-aware filter must still match untagged frames"
    assert _matches(bpf, vlan_tagged), "VLAN-aware filter must match 802.1Q-tagged inbound frames"
    # Guard the regression this fixes: the old plain filter drops the tagged frame.
    assert not _matches("tcp and (port 10000)", vlan_tagged), (
        "plain 'tcp and port' unexpectedly matched a VLAN frame — the gap this test guards is gone"
    )


def test_live_bpf_vlan_falls_back_on_non_ethernet_links() -> None:
    """The `vlan` clause is Ethernet-only: on DLT_RAW (accelerator TUN) / DLT_NULL (loopback) the
    VLAN-aware filter FAILS to compile, so NpcapBackend must fall back to bpf_without_vlan() —
    otherwise those links run unfiltered (settlement-burst drop risk). Guards the regression the
    VLAN-aware filter would otherwise introduce for TUN/loopback capture. wpcap-only -> SKIP if absent.
    """
    import ctypes as ct

    import pytest

    try:
        from bidking_lab.live.capture import _BpfProgram, _load_wpcap
        lib = _load_wpcap()
    except OSError:
        pytest.skip("wpcap.dll absent (non-Windows / Npcap not installed) — live-only gate")

    lib.pcap_open_dead.argtypes = [ct.c_int, ct.c_int]
    lib.pcap_open_dead.restype = ct.c_void_p
    lib.pcap_compile.argtypes = [ct.c_void_p, ct.POINTER(_BpfProgram), ct.c_char_p, ct.c_int, ct.c_uint]
    lib.pcap_compile.restype = ct.c_int
    lib.pcap_close.argtypes = [ct.c_void_p]

    def _compiles(dlt: int, bpf: str) -> bool:
        handle = lib.pcap_open_dead(dlt, 65536)
        prog = _BpfProgram()
        rc = lib.pcap_compile(handle, ct.byref(prog), bpf.encode(), 1, 0xFFFFFFFF)
        lib.pcap_close(handle)
        return rc == 0

    vlan_aware = build_bpf_filter([10000])
    plain = bpf_without_vlan(vlan_aware)
    for dlt in (DLT_RAW, DLT_NULL):
        assert not _compiles(dlt, vlan_aware), f"expected vlan clause to reject DLT {dlt}"
        assert _compiles(dlt, plain), f"fallback filter must compile on DLT {dlt}"
    # Ethernet takes the VLAN-aware form directly.
    assert _compiles(DLT_EN10MB, vlan_aware)
