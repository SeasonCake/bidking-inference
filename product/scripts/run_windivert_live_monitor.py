"""Capture BidKing TCP packets directly with WinDivert.

This is the legal fallback when Fatbeans WebHook is unavailable. It does not
depend on Fatbeans export/WebHook membership: packets are sniffed locally,
attributed to BidKing.exe using the Windows TCP table, converted into the same
Fatbeans export-row shape, then fed to the existing live monitor artifact path.
"""

from __future__ import annotations

import argparse
import base64
from collections import Counter, deque
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SRC = ROOT / "src"
for path in (SCRIPTS, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from run_fatbeans_webhook_monitor import (  # noqa: E402
    FatbeansWebhookMonitor,
    WebhookMonitorConfig,
    _acquire_lock,
)
from bidking_lab.live.fatbeans import (  # noqa: E402
    FatbeansFrame,
    _parse_direct_action_response,
    _parse_send_event,
    _parse_state_event,
    _parse_status_event,
)
from bidking_lab.live.monitor import load_monitor_tables  # noqa: E402


FlowKey = tuple[str, int, str, int]
StreamKey = tuple[str, str, int, str, int]
GAME_SEND_MESSAGE_IDS = {0x0022, 0x0026}
GAME_STATE_MESSAGE_IDS = {0x0021, 0x0025, 0x0027, 0x002D}
SESSION_ID_RE = re.compile(r"^[0-9]{4}:[0-9]+$")
SESSION_NATIVE_TOKEN_MAX_DIGITS = 20
SESSION_NATIVE_TOKEN_MAX = (1 << 64) - 1
MAX_FRAME_BYTES = 1_000_000


def _process_is_elevated() -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def _normalize_ip(value: Any) -> str:
    text = str(value or "")
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return text
    if getattr(ip, "ipv4_mapped", None) is not None:
        return str(ip.ipv4_mapped)
    return str(ip)


def _endpoint(value: Any) -> tuple[str, int] | None:
    if value in (None, (), ""):
        return None
    try:
        ip = getattr(value, "ip")
        port = getattr(value, "port")
    except AttributeError:
        try:
            ip, port = value
        except (TypeError, ValueError):
            return None
    try:
        return _normalize_ip(ip), int(port)
    except (TypeError, ValueError):
        return None


def _process_name_for_pid(pid: int | None) -> str | None:
    if pid is None:
        return None
    try:
        import psutil

        return psutil.Process(int(pid)).name()
    except Exception:  # noqa: BLE001 - process can exit while inspecting
        return None


def _matches_process_name(actual: Any, expected: str) -> bool:
    return str(actual or "").casefold() == expected.casefold()


def _is_loopback_ip(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class FlowMatch:
    direction: str
    pid: int | None
    process_name: str
    local: tuple[str, int]
    remote: tuple[str, int]


def _flow_direction_map(process_name: str) -> dict[FlowKey, FlowMatch]:
    """Return packet 4-tuple -> SEND/REV for current BidKing TCP flows."""
    try:
        import psutil
    except ImportError as exc:
        raise RuntimeError("psutil is required for process-aware capture") from exc

    flows: dict[FlowKey, FlowMatch] = {}
    for conn in psutil.net_connections(kind="tcp"):
        if conn.pid is None:
            continue
        actual_name = _process_name_for_pid(conn.pid)
        if not _matches_process_name(actual_name, process_name):
            continue
        local = _endpoint(conn.laddr)
        remote = _endpoint(conn.raddr)
        if local is None or remote is None:
            continue
        local_ip, local_port = local
        remote_ip, remote_port = remote
        flows[(local_ip, local_port, remote_ip, remote_port)] = FlowMatch(
            direction="SEND",
            pid=conn.pid,
            process_name=actual_name or process_name,
            local=local,
            remote=remote,
        )
        flows[(remote_ip, remote_port, local_ip, local_port)] = FlowMatch(
            direction="REV",
            pid=conn.pid,
            process_name=actual_name or process_name,
            local=local,
            remote=remote,
        )
    return flows


def _flow_is_capture_target(
    match: FlowMatch,
    *,
    server_ports: set[int],
    include_loopback: bool,
) -> bool:
    remote_ip, remote_port = match.remote
    local_ip, local_port = match.local
    if server_ports and (remote_port in server_ports or local_port in server_ports):
        return True
    if include_loopback:
        return True
    return not _is_loopback_ip(remote_ip) and not _is_loopback_ip(local_ip)


class FlowIndex:
    def __init__(
        self,
        *,
        process_name: str,
        refresh_seconds: float,
        server_ports: set[int] | None = None,
        include_loopback: bool = False,
    ) -> None:
        self.process_name = process_name
        self.refresh_seconds = max(0.05, refresh_seconds)
        self.server_ports = set(server_ports or ())
        self.include_loopback = include_loopback
        self._flows: dict[FlowKey, FlowMatch] = {}
        self._last_refresh = 0.0

    def refresh_if_due(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self._last_refresh >= self.refresh_seconds:
            flows = _flow_direction_map(self.process_name)
            self._flows = {
                key: match
                for key, match in flows.items()
                if _flow_is_capture_target(
                    match,
                    server_ports=self.server_ports,
                    include_loopback=self.include_loopback,
                )
            }
            self._last_refresh = now

    def match(self, key: FlowKey) -> FlowMatch | None:
        self.refresh_if_due()
        match = self._flows.get(key)
        if match is not None:
            return match
        # The first packet can race ahead of the TCP table refresh.
        self.refresh_if_due(force=True)
        return self._flows.get(key)

    def active_flow_count(self) -> int:
        self.refresh_if_due()
        return len(self._flows) // 2

    def flow_signature(self) -> tuple[FlowKey, ...]:
        self.refresh_if_due()
        return tuple(sorted(self._flows.keys()))


def _packet_payload(packet: Any) -> bytes:
    payload = getattr(packet, "payload", b"") or b""
    if isinstance(payload, memoryview):
        return payload.tobytes()
    return bytes(payload)


def _packet_key(packet: Any) -> FlowKey | None:
    try:
        return (
            _normalize_ip(getattr(packet, "src_addr")),
            int(getattr(packet, "src_port")),
            _normalize_ip(getattr(packet, "dst_addr")),
            int(getattr(packet, "dst_port")),
        )
    except (TypeError, ValueError):
        return None


def _capture_time() -> str:
    now = time.time()
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)) + (
        f".{int((now % 1) * 1000):03d}"
    )


def _packet_to_fatbeans_row(
    packet: Any,
    *,
    sort_id: int,
    flow_index: FlowIndex,
) -> dict[str, Any] | None:
    payload = _packet_payload(packet)
    if not payload:
        return None
    key = _packet_key(packet)
    if key is None:
        return None
    match = flow_index.match(key)
    if match is None:
        return None
    src_ip, src_port, dst_ip, dst_port = key
    return {
        "SortID": int(sort_id),
        "Direct": match.direction,
        "Protocol": "Tcp",
        "SrcIP": src_ip,
        "SrcPort": src_port,
        "DstIP": dst_ip,
        "DstPort": dst_port,
        "CaptureTime": _capture_time(),
        "Data": base64.b64encode(payload).decode("ascii"),
        "DataLength": len(payload),
        "PID": match.pid,
        "ProcessName": match.process_name,
        "Source": "WinDivert",
    }


@dataclass(frozen=True)
class GameFrame:
    template_row: Mapping[str, Any]
    raw: bytes
    direction: str
    message_id: int
    session_id: str
    is_state: bool
    is_status: bool = False


@dataclass(frozen=True)
class FrameGateEmit:
    rows: tuple[dict[str, Any], ...]
    reset_session: bool = False


def _valid_session_id(value: str | None) -> bool:
    return _native_session_generation(value) is not None


def _native_session_generation(value: str | None) -> int | None:
    """Return the wire session generation under the monitor's native grammar."""

    if (
        type(value) is not str  # noqa: E721 - reject coercive string subclasses
        or not value
        or not value.isascii()
        or not SESSION_ID_RE.fullmatch(value)
    ):
        return None
    token = value.split(":", 1)[1]
    if len(token) > SESSION_NATIVE_TOKEN_MAX_DIGITS:
        return None
    generation = int(token)
    if generation > SESSION_NATIVE_TOKEN_MAX:
        return None
    return generation


def _frame_message_id(direction: str, raw: bytes) -> int | None:
    if direction == "SEND" and len(raw) >= 12:
        return int.from_bytes(raw[8:12], "big")
    if direction == "REV" and len(raw) >= 16:
        return int.from_bytes(raw[12:16], "big")
    return None


def _frame_packet_tag(direction: str, raw: bytes) -> int | None:
    if direction == "SEND" and len(raw) >= 8:
        return int.from_bytes(raw[4:8], "big")
    if direction == "REV" and len(raw) >= 12:
        return int.from_bytes(raw[8:12], "big")
    return None


class GameFrameGate:
    """Turn TCP payload chunks into game-session frame rows.

    The raw BidKing connection also carries heartbeat and non-auction UI frames.
    The live parser only needs bid/action sends and round/settlement state
    pushes, so this gate keeps complete frames with known session ids and drops
    the rest before they reach the monitor artifact builder.
    """

    # A reassembly buffer that holds pending bytes this long without extracting a single frame
    # is wedged: it is waiting for bytes that will never come, so it never self-recovers.
    #
    # ⚠️ 楔死的成因(0712 取证, 13 个 hotfix1 诊断包 / 30 次丢弃型 resync)—— 此前这里写的是
    # "锁死在【假】长度前缀上", 那个说法【已被证伪】, 只占 3/30。真正的主因是相反的:
    #   27/30 = 缓冲里躺着一个【真】帧头(长度合法·计数器与 packet_tag 单调且落在实测区间·
    #           msgid 是 gate 天天解析并忽略的 0x5 / 0x179), 而缓冲比帧头声明的长度【短 45~9990 字节】
    #           —— 例: 头声明 20093B, 缓冲只有 10103B。这个帧的尾巴【根本没到】。
    #    3/30 = 才是真的假前缀(声明长度荒谬 + msgid 位是 protobuf 垃圾), 且多半是前一次
    #           丢字节导致失步后的【后果】, 不是独立成因。
    # 为什么这个区分是承重的: _extract_frames 用朴素的 buffer.extend 按到达顺序拼接, 所以【乱序】
    # 的字节照样会落进缓冲、长度照样能凑满 —— 帧会拼出一个内容错乱的 body, 但【不会楔死】。
    # 缓冲永远凑不满 == 那些字节我们【从头到尾没抓到】。而游戏本体显示正常 => 字节确实过了网卡
    # (TCP 保证交付) => 丢失发生在【我们自己的抓包管道】里, 不在网线上。
    # kernel_drop 与 queue_full_drops 在全部 13 个包里恒为 0, 已排除两条【有计数】的丢字节路径;
    # 没人数过的那几条现在由 live/stream_diag.py(TCP seq 空洞)+ kernel_ifdrop / snap_truncated /
    # ip_truncated 量出来, 结论落进 capture_source_status.json。真修(按 seq 重组 / 等重传补洞)
    # 留 0.2.8 —— 别照着"假前缀"的旧故事去加严长度校验, 那治不了缺字节。
    #
    # 20s 这个阈值本身仍然有效(两种成因都靠它兜底): >> 真帧的填充时间(结算爆发 <1s),
    # << 一轮(~1min)。回滚: BIDKING_DISABLE_GATE_SELFHEAL=1。
    # ⚠️ 但它是【清流丢弃】的 —— 见 BUG-K: 自愈会把缓冲里【完好的后续帧】一起扔掉。所以边界
    # relock(下方 _BOUNDARY_SCAN_PATTERNS)才是保帧的那条路, 它只丢前面那段残帧。
    STALE_BUFFER_SECONDS = 20.0
    # BUG-A (0710 opening blind window): a wedge that formed on the PREVIOUS match's tail
    # swallows the next match's opening frames until the 20s sweep fires — 20-25s with no
    # r01 snapshot while the overlay shows the stale match. Two boundary-aware paths close
    # that window (rollback for both: BIDKING_DISABLE_GATE_BOUNDARY_RESYNC=1):
    #   1. Boundary relock (_try_boundary_resync): a wedged buffer usually holds
    #      [garbage from the false prefix][intact new-session frames]; scan the pending
    #      bytes for a complete session-opening frame and re-lock there, PRESERVING the
    #      opening frames the 20s sweep would have discarded.
    #   2. Between matches (session settled / none locked yet) no legitimate large frame
    #      can be mid-fill, so the sweep threshold drops to SETTLED_STALE_BUFFER_SECONDS.
    # 3s grace before scanning: real frames complete their fill in <1s (see above), so a
    # buffer with pending bytes and no progress for 3s is wedged, not filling — scanning
    # a healthy mid-fill frame's payload could false-positive on embedded bytes.
    BOUNDARY_RESYNC_GRACE_SECONDS = 3.0
    SETTLED_STALE_BUFFER_SECONDS = 5.0
    # Frames a session opens with (session-reset emit msgids, BUG-A取证): REV 0x0021
    # state / 0x0077 status (both packet_tag==0 -> 8-byte pattern at boundary+8), SEND
    # 0x0026 (msgid-only 4-byte pattern at boundary+8; SEND packet tags vary). Each
    # candidate must still be a COMPLETE frame whose parsed session id validates before
    # the gate re-locks on it, so the short SEND pattern cannot misfire on its own.
    # 0x002D = 结算帧。⚠️必须扫它 —— 这是 BUG-K(0712 作者报"结算冻在上一轮、等 20 秒多才跳结算,
    # ⚠️代号: 本 bug 原名 BUG-F, 因与调参窗口 TRIAGE 里的 BUG-F(尼沦 2601 红 14→3·族 D)【撞车】,
    # 2026-07-12 由发布安全窗口改名为 BUG-K(下一个空闲字母)。commit 7ca7bd8 的消息里仍写作 BUG-F。
    # 而且结算异常/藏品缺失")的根因: 结算前 REV 流楔死 -> 结算帧被吞进楔死缓冲 -> 因为它不在扫描
    # 列表里, 边界 relock 不触发; 而 settled-5s 快扫要 _current_session_settled=True, 结算【前】它
    # 还是 False -> 只能等 20s 自愈, 而自愈是【清流丢弃缓冲】的 -> 结算帧连同缓冲一起丢掉。
    # 这同时解释了两个现象: 卡 20 秒(等自愈)+ 藏品缺失(结算帧被丢)。
    # relock 与自愈的关键区别: relock 【保留】缓冲里的完整帧, 只丢前面的垃圾前缀 -> 结算帧保得住。
    # (与引擎 BUG-E 是两回事: 那个是帧到了但解析漏了; 这个是帧根本没到。)
    _BOUNDARY_SCAN_PATTERNS: dict[str, tuple[tuple[bytes, int, int], ...]] = {
        "REV": (
            (b"\x00\x00\x00\x00" + (0x0021).to_bytes(4, "big"), 8, 0x0021),
            (b"\x00\x00\x00\x00" + (0x0077).to_bytes(4, "big"), 8, 0x0077),
            (b"\x00\x00\x00\x00" + (0x002D).to_bytes(4, "big"), 8, 0x002D),  # 结算帧: 丢了=藏品缺失
        ),
        "SEND": (((0x0026).to_bytes(4, "big"), 8, 0x0026),),
    }

    def __init__(self) -> None:
        self._buffers: dict[StreamKey, bytearray] = {}
        self._current_session_id: str | None = None
        self._current_session_settled = False
        # Session that received 0x002D; kept across the next-session boundary so
        # late settlement tails still emit without reset_rows wiping live rows.
        self._settled_session_id: str | None = None
        # Capture adapters can deliver a complete status/state frame from an older
        # TCP flow after the next auction is already active.  Re-entering that known
        # retired session makes the monitor clear the current rows, then flip back a
        # few frames later; the overlay legitimately shows standby in that gap.  Keep
        # a bounded process-lifetime history so late ordinary frames cannot reopen a
        # prior auction.  A process epoch reset clears this history, and the dedicated
        # late-settlement path below remains allowed.
        self._retired_session_ids: deque[str] = deque(maxlen=16)
        self._pending_next_session_frames: deque[GameFrame] = deque(maxlen=8)
        self._pending_action_ids: dict[str, deque[int]] = {}
        self._next_sort_id = 1
        self.dropped_bytes = 0
        self.ignored_frames = 0
        self.accepted_frames = 0
        self.ignored_reasons: Counter[str] = Counter()
        # 24 (not 8): a "stuck" session rejects many frames while accepted_frames flatlines; 8 samples
        # is too few to tell whether the rejected frames are clean keepalives (game paused) or misaligned
        # (gate corruption). 24 captures more of the stuck tail without bloating capture_source_status.json.
        self._ignored_samples: deque[dict[str, Any]] = deque(maxlen=24)
        # Wedged-buffer self-heal state: last time each stream's buffer made progress
        # (extracted a frame / drained empty / was created), + sweep throttle + counter.
        self._buffer_progress: dict[StreamKey, float] = {}
        self._last_stale_sweep = 0.0
        self.buffer_resyncs = 0
        # Breakdown of buffer_resyncs: how many were boundary relocks (frame-preserving)
        # vs stale sweeps (discarding). buffer_resyncs stays the total for status/regression.
        self.boundary_resyncs = 0
        self._selfheal_disabled = os.environ.get("BIDKING_DISABLE_GATE_SELFHEAL") == "1"
        self._boundary_resync_disabled = (
            os.environ.get("BIDKING_DISABLE_GATE_BOUNDARY_RESYNC") == "1"
        )

    def _note_ignore(
        self,
        reason: str,
        *,
        sample: Mapping[str, Any] | None = None,
    ) -> None:
        self.ignored_frames += 1
        self.ignored_reasons[reason] += 1
        if sample is not None:
            stored = dict(sample)
            stored["reason"] = reason
            self._ignored_samples.append(stored)

    def ignored_reasons_dict(self) -> dict[str, int]:
        return dict(sorted(self.ignored_reasons.items()))

    @property
    def ignored_samples(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._ignored_samples)

    def ignored_samples_dict(self) -> list[dict[str, Any]]:
        return [dict(sample) for sample in self._ignored_samples]

    @staticmethod
    def _packet_key_from_stream_key(stream_key: StreamKey) -> FlowKey:
        direction, local_ip, local_port, remote_ip, remote_port = stream_key
        if direction == "SEND":
            return (local_ip, local_port, remote_ip, remote_port)
        return (remote_ip, remote_port, local_ip, local_port)

    def _ignored_frame_sample(
        self,
        row: Mapping[str, Any],
        *,
        direction: str,
        raw: bytes,
        message_id: int | None,
        frame_session_id: str | None = None,
    ) -> dict[str, Any]:
        sample = {
            "direction": direction,
            "sort_id": int(row.get("SortID") or 0),
            "capture_time": str(row.get("CaptureTime") or ""),
            "src": f"{_normalize_ip(row.get('SrcIP'))}:{int(row.get('SrcPort') or 0)}",
            "dst": f"{_normalize_ip(row.get('DstIP'))}:{int(row.get('DstPort') or 0)}",
            "packet_tag": _frame_packet_tag(direction, raw),
            "message_id": (
                f"0x{message_id:04x}" if message_id is not None else None
            ),
            "raw_length": len(raw),
            "raw_prefix": raw[:16].hex(),
            "active_session_id": self._current_session_id,
        }
        if frame_session_id is not None:
            sample["frame_session_id"] = frame_session_id
        # npcap rows carry the capture adapter (multi-adapter desync attribution); windivert
        # rows don't have it — omit rather than emit null noise.
        device = row.get("CaptureDevice")
        if device:
            sample["device"] = str(device)
        return sample

    def reset_stream_buffers(
        self,
        *,
        keep_flow_keys: Iterable[FlowKey] | None = None,
        clear_session: bool = False,
    ) -> None:
        if keep_flow_keys is None:
            self._buffers.clear()
        else:
            keep_keys = set(keep_flow_keys)
            if keep_keys:
                self._buffers = {
                    stream_key: buffer
                    for stream_key, buffer in self._buffers.items()
                    if self._packet_key_from_stream_key(stream_key) in keep_keys
                }
            else:
                self._buffers.clear()
        self._buffer_progress = {
            key: ts for key, ts in self._buffer_progress.items() if key in self._buffers
        }
        self.dropped_bytes = 0
        if clear_session:
            self._current_session_id = None
            self._current_session_settled = False
            self._settled_session_id = None
            self._retired_session_ids.clear()
            self._pending_next_session_frames.clear()
            self._pending_action_ids.clear()
            self._next_sort_id = 1

    @property
    def active_session_id(self) -> str | None:
        return self._current_session_id

    def _retire_session(self, session_id: str | None) -> None:
        if not _valid_session_id(session_id):
            return
        assert session_id is not None
        try:
            self._retired_session_ids.remove(session_id)
        except ValueError:
            pass
        self._retired_session_ids.append(session_id)

    def _is_retired_session(self, session_id: str) -> bool:
        return (
            session_id != self._current_session_id
            and session_id in self._retired_session_ids
        )

    def _is_older_native_session(self, session_id: str) -> bool:
        """Compare native generations so an unseen late TCP flow cannot reopen."""

        active_generation = _native_session_generation(self._current_session_id)
        candidate_generation = _native_session_generation(session_id)
        return (
            active_generation is not None
            and candidate_generation is not None
            and candidate_generation < active_generation
        )

    def _sweep_stale_buffers(self, now: float) -> None:
        """Clear reassembly buffers that pend bytes but made no progress for
        STALE_BUFFER_SECONDS — the wedged-desync self-heal. Clearing loses at most the
        already-unparseable bytes; the next arriving segment starts at a fresh frame
        boundary, so the stream re-locks within one packet instead of staying dead."""
        threshold = self.STALE_BUFFER_SECONDS
        if not self._boundary_resync_disabled and (
            self._current_session_settled or self._current_session_id is None
        ):
            # Between matches (or before any session locks) no legitimate frame can be
            # mid-fill for long; a wedge here is the BUG-A opening blind window, so heal
            # at the boundary threshold instead of the mid-match-safe 20s.
            threshold = self.SETTLED_STALE_BUFFER_SECONDS
        for key, buf in self._buffers.items():
            if not buf:
                continue
            progress = self._buffer_progress.get(key)
            if progress is None:
                self._buffer_progress[key] = now
                continue
            if now - progress >= threshold:
                self.buffer_resyncs += 1
                print(
                    f"[gate] resync#{self.buffer_resyncs} wedged {int(now - progress)}s "
                    f"stream={key[0]} {key[1]}:{key[2]}->{key[3]}:{key[4]} "
                    f"cleared={len(buf)}B prefix={bytes(buf[:16]).hex()}",
                    flush=True,
                )
                buf.clear()
                self._buffer_progress[key] = now

    def feed_row(self, row: Mapping[str, Any]) -> FrameGateEmit:
        direction = str(row.get("Direct") or "").upper()
        if direction not in ("SEND", "REV"):
            return FrameGateEmit(())
        if not self._selfheal_disabled:
            now = time.monotonic()
            if now - self._last_stale_sweep >= 1.0:
                self._last_stale_sweep = now
                self._sweep_stale_buffers(now)
        stream_key = self._stream_key(row, direction)
        try:
            payload = base64.b64decode(row.get("Data") or "", validate=True)
        except Exception:  # noqa: BLE001 - malformed packet boundary
            return FrameGateEmit(())
        if not payload:
            return FrameGateEmit(())

        frames = self._extract_frames(stream_key, direction, payload)
        emitted: list[dict[str, Any]] = []
        reset_session = False
        for raw in frames:
            game_frame, ignore_reason, ignore_sample = self._classify_frame(
                row,
                direction,
                raw,
            )
            if game_frame is None:
                if ignore_reason == "rev_tool_pending_next_session":
                    continue
                self._note_ignore(
                    ignore_reason or "unclassified",
                    sample=ignore_sample,
                )
                continue
            should_emit, should_reset = self._should_emit(game_frame)
            if should_reset:
                reset_session = True
                # Do not clear `emitted` or TCP reassembly buffers here: same-chunk
                # frames before the reset marker must still reach the monitor, and
                # partial settlement tails may still be in-flight on this stream.
                self._next_sort_id = 1
                self._pending_action_ids = {
                    game_frame.session_id: self._pending_action_ids.get(
                        game_frame.session_id,
                        deque(maxlen=64),
                    )
                }
                emitted.extend(
                    self._flush_pending_next_session_frames(game_frame.session_id)
                )
            if should_emit:
                self._record_outbound_action(game_frame)
                emitted.append(self._row_from_game_frame(game_frame))
                self.accepted_frames += 1
            else:
                self._note_ignore(
                    (
                        "retired_session"
                        if self._is_retired_session(game_frame.session_id)
                        else "send_wrong_session"
                    ),
                    sample=self._ignored_frame_sample(
                        row,
                        direction=direction,
                        raw=raw,
                        message_id=game_frame.message_id,
                        frame_session_id=game_frame.session_id,
                    ),
                )
        return FrameGateEmit(tuple(emitted), reset_session=reset_session)

    def _stream_key(self, row: Mapping[str, Any], direction: str) -> StreamKey:
        src_ip = _normalize_ip(row.get("SrcIP"))
        dst_ip = _normalize_ip(row.get("DstIP"))
        try:
            src_port = int(row.get("SrcPort") or 0)
            dst_port = int(row.get("DstPort") or 0)
        except (TypeError, ValueError):
            src_port = 0
            dst_port = 0
        if direction == "SEND":
            local_ip, local_port = src_ip, src_port
            remote_ip, remote_port = dst_ip, dst_port
        else:
            local_ip, local_port = dst_ip, dst_port
            remote_ip, remote_port = src_ip, src_port
        return (direction, local_ip, local_port, remote_ip, remote_port)

    def _record_outbound_action(self, frame: GameFrame) -> None:
        """Remember only accepted same-session action ids for correlated replies."""
        if frame.direction != "SEND" or frame.message_id != 0x0026:
            return
        parsed = _parse_send_event(
            FatbeansFrame(
                index=0,
                direction="SEND",
                sort_id=0,
                capture_time="",
                raw=frame.raw,
            )
        )
        if (
            parsed is None
            or parsed.session_id != frame.session_id
            or not isinstance(parsed.value, int)
            or isinstance(parsed.value, bool)
            or parsed.value <= 0
        ):
            return
        pending = self._pending_action_ids.setdefault(
            frame.session_id,
            deque(maxlen=64),
        )
        pending.append(int(parsed.value))

    def _consume_correlated_action(
        self,
        session_id: str,
        action_id: int,
    ) -> bool:
        pending = self._pending_action_ids.get(session_id)
        if not pending:
            return False
        try:
            pending.remove(int(action_id))
        except ValueError:
            return False
        return True

    def _extract_frames(
        self,
        stream_key: StreamKey,
        direction: str,
        payload: bytes,
    ) -> list[bytes]:
        buffer = self._buffers.setdefault(stream_key, bytearray())
        buffer.extend(payload)
        frames: list[bytes] = []
        while True:
            while len(buffer) >= 4:
                frame_len = int.from_bytes(buffer[:4], "big")
                min_len = 12 if direction == "SEND" else 16
                if frame_len < min_len or frame_len > MAX_FRAME_BYTES:
                    del buffer[0]
                    self.dropped_bytes += 1
                    continue
                if len(buffer) < frame_len:
                    break
                frames.append(bytes(buffer[:frame_len]))
                del buffer[:frame_len]
            if frames or not buffer:
                break
            # Head is pending fill with no progress: wedge candidate. A successful
            # boundary relock drops the garbage prefix, so re-run extraction from the
            # relocked boundary (the relock guarantees a complete frame sits there).
            if not self._try_boundary_resync(stream_key, direction, buffer):
                break
        # Progress = extracted something or drained empty; a buffer that only accumulates
        # is a wedge candidate for _sweep_stale_buffers. First bytes count as progress so
        # a fresh partial frame gets the full STALE_BUFFER_SECONDS to complete.
        if frames or not buffer or stream_key not in self._buffer_progress:
            self._buffer_progress[stream_key] = time.monotonic()
        return frames

    def _boundary_frame_session_id(
        self,
        direction: str,
        message_id: int,
        raw: bytes,
    ) -> str | None:
        """Parse the session id out of a boundary-relock candidate frame; None if the
        bytes do not actually parse as that frame type (false-positive pattern hit)."""
        frame = FatbeansFrame(
            index=0,
            direction=direction,  # type: ignore[arg-type]
            sort_id=0,
            capture_time="",
            raw=raw,
        )
        try:
            if direction == "REV" and message_id == 0x0077:
                status = _parse_status_event(frame)
                return status.session_id if status is not None else None
            if direction == "REV" and message_id in (0x0021, 0x002D):
                # 0x002D = 结算帧, 与 0x0021 同走 _parse_state_event(_state_payload 里分别取 f1/f2)。
                # 结算帧尤其不能丢: 它一丢就是"结算异常/藏品缺失"(见 _BOUNDARY_SCAN_PATTERNS 注释)。
                state = _parse_state_event(frame)
                return state.session_id if state is not None else None
            if direction == "SEND" and message_id == 0x0026:
                event = _parse_send_event(frame)
                return event.session_id if event is not None else None
        except Exception:  # noqa: BLE001 - candidate bytes are untrusted
            return None
        return None

    def _try_boundary_resync(
        self,
        stream_key: StreamKey,
        direction: str,
        buffer: bytearray,
    ) -> bool:
        """BUG-A boundary relock: a wedged buffer often holds [garbage from a false
        length prefix][intact new-session frames]. Waiting for the stale sweep costs the
        whole opening (20-25s blind window) AND discards those frames. Instead, scan the
        pending bytes for a complete session-opening frame (msgid pattern + plausible
        length + parses with a valid session id) and re-lock the stream there. False
        positives require a full valid frame image embedded in garbage/payload bytes —
        and the stale sweep still backstops if this scan never matches."""
        if self._selfheal_disabled or self._boundary_resync_disabled:
            return False
        now = time.monotonic()
        progress = self._buffer_progress.get(stream_key)
        if progress is None or now - progress < self.BOUNDARY_RESYNC_GRACE_SECONDS:
            return False
        patterns = self._BOUNDARY_SCAN_PATTERNS.get(direction)
        if not patterns:
            return False
        view = bytes(buffer)
        min_len = 12 if direction == "SEND" else 16
        best: tuple[int, int, str] | None = None
        for pattern, header_offset, message_id in patterns:
            search_from = 0
            while True:
                hit = view.find(pattern, search_from)
                if hit < 0:
                    break
                search_from = hit + 1
                boundary = hit - header_offset
                if boundary < 0:
                    continue
                frame_len = int.from_bytes(view[boundary : boundary + 4], "big")
                if frame_len < min_len or frame_len > MAX_FRAME_BYTES:
                    continue
                if boundary + frame_len > len(view):
                    # Candidate not fully delivered yet; keep scanning (a later small
                    # complete frame beats waiting on what may be another false prefix)
                    # and let the next chunk retry this one.
                    continue
                session_id = self._boundary_frame_session_id(
                    direction, message_id, view[boundary : boundary + frame_len]
                )
                if not _valid_session_id(session_id):
                    continue
                if best is None or boundary < best[0]:
                    best = (boundary, message_id, session_id or "")
                break  # earliest valid candidate for this pattern; later ones are worse
        if best is None:
            return False
        boundary, message_id, session_id = best
        if boundary <= 0:
            # A complete valid frame at offset 0 would already have been extracted by the
            # caller's inner loop, so we only relock when there is a garbage prefix to
            # drop. Guarding <=0 also makes the caller's re-extraction loop strictly
            # progress (del buffer[:boundary] must shrink the buffer).
            return False
        self.buffer_resyncs += 1
        self.boundary_resyncs += 1
        print(
            f"[gate] resync#{self.buffer_resyncs} boundary msgid=0x{message_id:04x} "
            f"session={session_id} wedged {int(now - progress)}s "
            f"stream={stream_key[0]} {stream_key[1]}:{stream_key[2]}->{stream_key[3]}:{stream_key[4]} "
            f"dropped={boundary}B prefix={view[:16].hex()}",
            flush=True,
        )
        self.dropped_bytes += boundary
        del buffer[:boundary]
        self._buffer_progress[stream_key] = now
        return True

    def _classify_frame(
        self,
        row: Mapping[str, Any],
        direction: str,
        raw: bytes,
    ) -> tuple[GameFrame | None, str | None, dict[str, Any] | None]:
        message_id = _frame_message_id(direction, raw)
        if message_id is None:
            return (
                None,
                "no_message_id",
                self._ignored_frame_sample(
                    row,
                    direction=direction,
                    raw=raw,
                    message_id=None,
                ),
            )
        frame = FatbeansFrame(
            index=0,
            direction=direction,  # type: ignore[arg-type]
            sort_id=int(row.get("SortID") or 0),
            capture_time=str(row.get("CaptureTime") or ""),
            raw=raw,
        )
        if direction == "SEND" and message_id in GAME_SEND_MESSAGE_IDS:
            event = _parse_send_event(frame)
            if event is None:
                return (
                    None,
                    "send_parse_failed",
                    self._ignored_frame_sample(
                        row,
                        direction=direction,
                        raw=raw,
                        message_id=message_id,
                    ),
                )
            if not _valid_session_id(event.session_id):
                return (
                    None,
                    "send_bad_session",
                    self._ignored_frame_sample(
                        row,
                        direction=direction,
                        raw=raw,
                        message_id=message_id,
                        frame_session_id=event.session_id,
                    ),
                )
            return GameFrame(
                template_row=row,
                raw=raw,
                direction=direction,
                message_id=message_id,
                session_id=event.session_id or "",
                is_state=False,
            ), None, None
        if (
            direction == "REV"
            and _frame_packet_tag(direction, raw) == 0
            and message_id == 0x0077
        ):
            status = _parse_status_event(frame)
            if status is None:
                return (
                    None,
                    "rev_status_parse_failed",
                    self._ignored_frame_sample(
                        row,
                        direction=direction,
                        raw=raw,
                        message_id=message_id,
                    ),
                )
            if not _valid_session_id(status.session_id):
                return (
                    None,
                    "rev_status_bad_session",
                    self._ignored_frame_sample(
                        row,
                        direction=direction,
                        raw=raw,
                        message_id=message_id,
                        frame_session_id=status.session_id,
                    ),
                )
            return GameFrame(
                template_row=row,
                raw=raw,
                direction=direction,
                message_id=message_id,
                session_id=status.session_id or "",
                is_state=False,
                is_status=True,
            ), None, None
        if (
            direction == "REV"
            and _frame_packet_tag(direction, raw) == 0
            and message_id in GAME_STATE_MESSAGE_IDS
            and message_id != 0x0027
        ):
            state = _parse_state_event(frame)
            if state is None:
                return (
                    None,
                    "rev_state_parse_failed",
                    self._ignored_frame_sample(
                        row,
                        direction=direction,
                        raw=raw,
                        message_id=message_id,
                    ),
                )
            if not _valid_session_id(state.session_id):
                return (
                    None,
                    "rev_state_bad_session",
                    self._ignored_frame_sample(
                        row,
                        direction=direction,
                        raw=raw,
                        message_id=message_id,
                        frame_session_id=state.session_id,
                    ),
                )
            if (
                state.map_id is None
                and state.round_index is None
                and not state.inventory_items
                and not state.bids
                and not state.action_results
                and not state.public_infos
                and not state.skill_reveals
            ):
                return (
                    None,
                    "rev_state_empty",
                    self._ignored_frame_sample(
                        row,
                        direction=direction,
                        raw=raw,
                        message_id=message_id,
                        frame_session_id=state.session_id,
                    ),
                )
            return GameFrame(
                template_row=row,
                raw=raw,
                direction=direction,
                message_id=message_id,
                session_id=state.session_id or "",
                is_state=True,
            ), None, None
        if (
            direction == "REV"
            and message_id == 0x0027
        ):
            packet_tag = _frame_packet_tag(direction, raw)
            result = _parse_direct_action_response(
                frame,
                allow_correlated_tag=True,
            )
            if result is None:
                return (
                    None,
                    "rev_tool_parse_failed",
                    self._ignored_frame_sample(
                        row,
                        direction=direction,
                        raw=raw,
                        message_id=message_id,
                    ),
                )
            if not _valid_session_id(self._current_session_id):
                return (
                    None,
                    "rev_tool_no_active_session",
                    self._ignored_frame_sample(
                        row,
                        direction=direction,
                        raw=raw,
                        message_id=message_id,
                    ),
                )
            if packet_tag != 0 and not self._consume_correlated_action(
                self._current_session_id or "",
                int(result.action_id),
            ):
                return (
                    None,
                    "rev_tool_unmatched_correlated_response",
                    self._ignored_frame_sample(
                        row,
                        direction=direction,
                        raw=raw,
                        message_id=message_id,
                    ),
                )
            if self._current_session_settled:
                if packet_tag != 0:
                    return (
                        None,
                        "rev_tool_correlated_after_settlement",
                        self._ignored_frame_sample(
                            row,
                            direction=direction,
                            raw=raw,
                            message_id=message_id,
                        ),
                    )
                self._pending_next_session_frames.append(
                    GameFrame(
                        template_row=row,
                        raw=raw,
                        direction=direction,
                        message_id=message_id,
                        session_id="",
                        is_state=True,
                    )
                )
                return (
                    None,
                    "rev_tool_pending_next_session",
                    None,
                )
            return GameFrame(
                template_row=row,
                raw=raw,
                direction=direction,
                message_id=message_id,
                session_id=self._current_session_id or "",
                is_state=True,
            ), None, None
        return (
            None,
            "rev_not_game_frame",
            self._ignored_frame_sample(
                row,
                direction=direction,
                raw=raw,
                message_id=message_id,
            ),
        )

    def _should_emit(self, frame: GameFrame) -> tuple[bool, bool]:
        older_native_session = self._is_older_native_session(frame.session_id)
        if (
            frame.is_state
            and frame.message_id == 0x002D
            and (
                frame.session_id == self._settled_session_id
                or self._is_retired_session(frame.session_id)
                or older_native_session
            )
        ):
            # Supplemental or split late settlement inventory for a prior
            # session. Never treat it as a reset, and never mark the active new
            # session settled merely because the old tail completed now.
            if frame.session_id == self._current_session_id:
                self._current_session_settled = True
            return True, False
        if older_native_session:
            self._retire_session(frame.session_id)
            return False, False
        if self._is_retired_session(frame.session_id):
            return False, False
        if frame.is_status:
            reset = (
                self._current_session_id is not None
                and self._current_session_id != frame.session_id
            )
            if reset:
                self._retire_session(self._current_session_id)
            self._current_session_id = frame.session_id
            if reset:
                self._current_session_settled = False
            return True, reset
        if frame.is_state:
            reset = (
                self._current_session_id is not None
                and self._current_session_id != frame.session_id
            )
            if reset:
                self._retire_session(self._current_session_id)
            self._current_session_id = frame.session_id
            if reset:
                self._current_session_settled = False
            if frame.message_id == 0x002D:
                self._current_session_settled = True
                self._settled_session_id = frame.session_id
            return True, reset
        if self._current_session_id is None:
            self._current_session_id = frame.session_id
            self._current_session_settled = False
            return True, False
        if self._current_session_id == frame.session_id:
            return True, False
        if (
            self._current_session_id is not None
            and (frame.message_id == 0x0022 or self._current_session_settled)
        ):
            self._retire_session(self._current_session_id)
            self._current_session_id = frame.session_id
            self._current_session_settled = False
            # Keep _settled_session_id so late 0x002D for the prior session still
            # emits without reset_rows clearing the accumulated capture rows.
            return True, True
        return False, False

    def _flush_pending_next_session_frames(self, session_id: str) -> list[dict[str, Any]]:
        if not self._pending_next_session_frames:
            return []
        rows: list[dict[str, Any]] = []
        while self._pending_next_session_frames:
            frame = self._pending_next_session_frames.popleft()
            rows.append(
                self._row_from_game_frame(
                    GameFrame(
                        template_row=frame.template_row,
                        raw=frame.raw,
                        direction=frame.direction,
                        message_id=frame.message_id,
                        session_id=session_id,
                        is_state=frame.is_state,
                        is_status=frame.is_status,
                    )
                )
            )
        return rows

    def _row_from_game_frame(self, frame: GameFrame) -> dict[str, Any]:
        row = dict(frame.template_row)
        row["SortID"] = self._next_sort_id
        row["Data"] = base64.b64encode(frame.raw).decode("ascii")
        row["DataLength"] = len(frame.raw)
        row["Source"] = "WinDivertFrameGate"
        row["MessageID"] = f"0x{frame.message_id:04x}"
        row["SessionID"] = frame.session_id
        self._next_sort_id += 1
        return row


def _should_schedule_monitor_process(row: Mapping[str, Any]) -> bool:
    return row.get("Direct") == "REV" and row.get("MessageID") != "0x0077"


def _default_filter(server_ports: list[int], *, broad: bool) -> str:
    if broad or not server_ports:
        return "tcp and tcp.PayloadLength > 0"
    parts = []
    for port in server_ports:
        parts.append(f"tcp.SrcPort == {int(port)}")
        parts.append(f"tcp.DstPort == {int(port)}")
    return "tcp and tcp.PayloadLength > 0 and (" + " or ".join(parts) + ")"


def _write_source_status(
    log_dir: Path,
    *,
    source: str = "windivert",
    process_name: str,
    filter_text: str,
    active_flows: int,
    accepted_packets: int,
    sniffed_packets: int = 0,
    raw_packets: int = 0,
    ignored_frames: int = 0,
    dropped_bytes: int = 0,
    ignored_reasons: Mapping[str, int] | None = None,
    ignored_samples: Iterable[Mapping[str, Any]] | None = None,
    active_session_id: str | None = None,
    dropped_bytes_delta: int | None = None,
    kernel_drop: int | None = None,
    kernel_recv: int | None = None,
    queue_full_drops: int | None = None,
    buffer_resyncs: int | None = None,
    boundary_resyncs: int | None = None,
    kernel_ifdrop: int | None = None,
    snap_truncated: int | None = None,
    ip_truncated: int | None = None,
    deduped_packets: int | None = None,
    seq_gaps_wire: Mapping[str, int] | None = None,
    seq_gaps_gate: Mapping[str, int] | None = None,
    tcp_reassembly: Mapping[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    error_hint: str | None = None,
    device_statuses: Iterable[Mapping[str, Any]] | None = None,
) -> None:
    path = log_dir / "capture_source_status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": time.time(),
        "source": source,
        "process_name": process_name,
        "filter": filter_text,
        "active_flows": active_flows,
        "sniffed_packets": sniffed_packets,
        "accepted_frames": accepted_packets,
        "accepted_packets": accepted_packets,
        "raw_packets": raw_packets,
        "ignored_frames": ignored_frames,
        "dropped_bytes": dropped_bytes,
        "ignored_reasons": dict(ignored_reasons or ()),
        "ignored_samples": [
            dict(sample) for sample in (ignored_samples or ())
        ],
        "active_session_id": active_session_id,
    }
    # Capture-health deltas (added to surface the 5606 under-count class): dropped_bytes
    # since last status, kernel pcap_stats drop/recv (npcap only), user-space queue.Full
    # drops (npcap only). None = not applicable to this source (e.g. windivert) -> omitted.
    if dropped_bytes_delta is not None:
        payload["dropped_bytes_delta"] = dropped_bytes_delta
    if kernel_drop is not None:
        payload["kernel_drop"] = kernel_drop
    if kernel_recv is not None:
        payload["kernel_recv"] = kernel_recv
    if queue_full_drops is not None:
        payload["queue_full_drops"] = queue_full_drops
    if buffer_resyncs is not None:
        payload["buffer_resyncs"] = buffer_resyncs
    if boundary_resyncs is not None:
        payload["boundary_resyncs"] = boundary_resyncs
    # 楔死取证(0712): 27/30 次楔死 = 真帧的尾巴没到齐, 不是"假长度前缀"。kernel_drop/queue_full
    # 恒为 0 已排除两条【有计数】的丢字节路径, 以下是此前【没人数过】的那几条 ——
    #   kernel_ifdrop  网卡/驱动层丢包(stats() 一直在采, 只是从没报出来)
    #   snap/ip_truncated  静默截断(npcap 给的字节 < 线上字节 / IP 声明长度 > 实收)
    #   seq_gaps_*     TCP seq 空洞 = 这些字节【从没抓到】; wire=dedup 前, gate=dedup 后。
    #                  两者相等 -> dedup 无辜; gate 更大 -> 是我们自己的 dedup 吃掉了需要的段。
    if kernel_ifdrop is not None:
        payload["kernel_ifdrop"] = kernel_ifdrop
    if snap_truncated is not None:
        payload["snap_truncated"] = snap_truncated
    if ip_truncated is not None:
        payload["ip_truncated"] = ip_truncated
    if deduped_packets is not None:
        payload["deduped_packets"] = deduped_packets
    if seq_gaps_wire is not None:
        payload["seq_gaps_wire"] = dict(seq_gaps_wire)
    if seq_gaps_gate is not None:
        payload["seq_gaps_gate"] = dict(seq_gaps_gate)
    if tcp_reassembly is not None:
        payload["tcp_reassembly"] = dict(tcp_reassembly)
    if error_code:
        payload["error_code"] = error_code
    if error_message:
        payload["error_message"] = error_message
    if error_hint:
        payload["error_hint"] = error_hint
    if device_statuses is not None:
        payload["capture_devices"] = [
            dict(status) for status in device_statuses
        ]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture BidKing packets with WinDivert and feed live overlay.",
    )
    parser.add_argument("--log-dir", default=str(ROOT / "data" / "logs" / "live"))
    parser.add_argument("--raw-dir", default=None)
    parser.add_argument("--tables-dir", default=None)
    parser.add_argument("--process-name", default="BidKing.exe")
    parser.add_argument(
        "--server-port",
        type=int,
        action="append",
        default=None,
        help="Restrict capture to known server port. Repeatable.",
    )
    parser.add_argument(
        "--broad",
        action="store_true",
        help=(
            "Capture all TCP payload packets in sniff mode, then keep only "
            "flows owned by --process-name. Use this for UU/VPN/TUN/proxy cases "
            "where the real port is unknown."
        ),
    )
    parser.add_argument("--filter", default=None, help="Override WinDivert filter")
    parser.add_argument("--flow-refresh-seconds", type=float, default=0.25)
    parser.add_argument(
        "--include-loopback",
        action="store_true",
        help="Also keep BidKing.exe loopback flows. Off by default because local control flows are not game frames.",
    )
    parser.add_argument("--status-seconds", type=float, default=2.0)
    parser.add_argument("--debounce-seconds", type=float, default=1.0)
    parser.add_argument("--min-inference-interval-seconds", type=float, default=2.0)
    parser.add_argument("--n-trials", type=int, default=500)
    parser.add_argument("--roi-trials", type=int, default=250)
    parser.add_argument("--shadow-trials", type=int, default=None)
    parser.add_argument(
        "--full-shadow-trials",
        type=int,
        default=20,
        help="Shadow trials for full/archive snapshots (fast uses 1).",
    )
    parser.add_argument(
        "--skip-debug-shadows",
        action="store_true",
        help="Skip profile_b5 debug shadow (default unless --enable-debug-shadows).",
    )
    parser.add_argument(
        "--fast-n-trials",
        type=int,
        default=10,
        help=(
            "Trials for non-force live snapshots. Use 0 to disable fast "
            "snapshots and run full inference on every update."
        ),
    )
    parser.add_argument(
        "--no-round-snapshots",
        action="store_true",
        help=(
            "Disable the per-round snapshot archive (round_snapshots/) that "
            "preserves each round's bidding/settled state for diagnostics."
        ),
    )
    parser.add_argument(
        "--no-full-upgrade",
        action="store_true",
        help=(
            "Disable upgrading a settled fast snapshot to full (n-trials) "
            "inference once packets stop arriving."
        ),
    )
    parser.add_argument(
        "--full-upgrade-idle-seconds",
        type=float,
        default=1.5,
        help="Seconds with no new packets before a full-inference upgrade runs.",
    )
    parser.add_argument(
        "--full-upgrade-max-seconds",
        type=float,
        default=6.0,
        help=(
            "Skip the full-inference upgrade when the estimated full runtime "
            "(from the last fast run) exceeds this, to stay responsive on heavy "
            "late-round enumeration."
        ),
    )
    parser.add_argument(
        "--enable-debug-shadows",
        action="store_true",
        help="Run profile_b5 debug shadow during live inference (off by default).",
    )
    parser.add_argument("--seed", type=int, default=20260530)
    parser.add_argument(
        "--formal-mode",
        default=os.environ.get("BIDKING_LIVE_FORMAL_MODE", "v3_practical"),
        choices=("v3_practical", "v2"),
        help=(
            "Formal live bid source. Defaults to BIDKING_LIVE_FORMAL_MODE "
            "or v3_practical."
        ),
    )
    parser.add_argument("--no-lock", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    try:
        import pydivert
    except ImportError:
        print(
            "[error] pydivert is not installed. Install it with: "
            f'"{sys.executable}" -m pip install pydivert',
            file=sys.stderr,
        )
        print(f"[env] python={sys.executable}", file=sys.stderr)
        return 2
    sniff_flag = getattr(getattr(pydivert, "Flag", object), "SNIFF", None)
    if sniff_flag is None:
        print(
            "[error] installed pydivert does not expose Flag.SNIFF; "
            "upgrade with: python -m pip install -U pydivert",
            file=sys.stderr,
        )
        return 2

    log_dir = Path(args.log_dir)
    raw_dir = Path(args.raw_dir) if args.raw_dir else log_dir / "raw"
    if not args.no_lock:
        try:
            _acquire_lock(log_dir)
        except RuntimeError as exc:
            print(f"[error] {exc}", file=sys.stderr)
            return 2
    tables = load_monitor_tables(tables_dir=args.tables_dir)
    monitor = FatbeansWebhookMonitor(
        config=WebhookMonitorConfig(
            log_dir=log_dir,
            raw_dir=raw_dir,
            process_name=args.process_name,
            server_ports=tuple(args.server_port or ()),
            n_trials=args.n_trials,
            roi_trials=args.roi_trials,
            shadow_trials=args.shadow_trials,
            full_shadow_trials=max(1, int(args.full_shadow_trials)),
            run_debug_shadows=bool(args.enable_debug_shadows)
            and not args.skip_debug_shadows,
            seed=args.seed,
            formal_mode=args.formal_mode,
            debounce_seconds=args.debounce_seconds,
            min_inference_interval_seconds=args.min_inference_interval_seconds,
            fast_n_trials=args.fast_n_trials if args.fast_n_trials > 0 else None,
            file_name="windivert_live.json",
            source_name="windivert",
            packet_count_key="windivert_frames",
            archive_round_snapshots=not args.no_round_snapshots,
            full_upgrade_when_idle=not args.no_full_upgrade,
            full_upgrade_idle_seconds=args.full_upgrade_idle_seconds,
            full_upgrade_max_seconds=args.full_upgrade_max_seconds,
        ),
        tables=tables,
    )
    monitor.start()
    # Safe numeric settlement diagnostics are always enabled. Complete application
    # bodies require an explicit local raw-forensics opt-in and are excluded from
    # normal support reports by collect_diagnostics.ps1.
    try:
        from bidking_lab.live import fatbeans as _fatbeans
        if _fatbeans.configure_live_settlement_diagnostics():
            print(
                "[privacy-warning] BIDKING_ENABLE_RAW_SETTLEMENT_FORENSICS=1; "
                "monitor.stdout.log contains unredacted application data and must stay private",
                flush=True,
            )
    except Exception:  # noqa: BLE001 — diag opt-in must never block live capture
        pass
    flow_index = FlowIndex(
        process_name=args.process_name,
        refresh_seconds=args.flow_refresh_seconds,
        server_ports=set(args.server_port or [10000]),
        include_loopback=args.include_loopback,
    )
    flow_index.refresh_if_due(force=True)
    frame_gate = GameFrameGate()

    server_ports = args.server_port or [10000]
    filter_text = args.filter or _default_filter(server_ports, broad=args.broad)
    _write_source_status(
        log_dir,
        process_name=args.process_name,
        filter_text=filter_text,
        active_flows=flow_index.active_flow_count(),
        accepted_packets=0,
        sniffed_packets=0,
        raw_packets=0,
        ignored_frames=0,
        dropped_bytes=0,
        active_session_id=None,
        ignored_samples=(),
    )
    print(f"[listen] Python: {sys.executable}", flush=True)
    print(f"[listen] process_elevated={_process_is_elevated()}", flush=True)
    print(f"[listen] WinDivert filter: {filter_text}", flush=True)
    print(
        f"[listen] process={args.process_name} active_flows={flow_index.active_flow_count()} "
        f"log_dir={log_dir}",
        flush=True,
    )

    def _version_banner() -> None:  # 后台比对版本(连服务器 /version), 永不阻塞或拖垮启动。
        try:
            from bidking_lab.live import version_check
            from bidking_lab.live.startup_version_notice import check_and_publish_startup_version

            st = check_and_publish_startup_version(log_dir)
            print(f"[version] {version_check.format_status_line(st)}", flush=True)
        except Exception as exc:  # noqa: BLE001  版本检查失败不影响监控
            print(f"[version] 跳过(版本检查异常: {type(exc).__name__})", flush=True)

    import threading as _threading
    _threading.Thread(target=_version_banner, daemon=True).start()

    sort_id = 1
    accepted = 0
    sniffed_packets = 0
    raw_packets = 0
    last_status = 0.0
    last_flow_signature: tuple[FlowKey, ...] = ()
    last_parse_warn = 0.0
    last_dropped_bytes = 0
    try:
        with pydivert.WinDivert(filter_text, flags=sniff_flag) as handle:
            for packet in handle:
                sniffed_packets += 1
                row = _packet_to_fatbeans_row(
                    packet,
                    sort_id=sort_id,
                    flow_index=flow_index,
                )
                now = time.monotonic()
                if row is not None:
                    raw_packets += 1
                    sort_id += 1
                    emitted = frame_gate.feed_row(row)
                    if emitted.reset_session:
                        monitor.reset_rows()
                    for game_row in emitted.rows:
                        monitor.accept_row(
                            game_row,
                            schedule_process=_should_schedule_monitor_process(
                                game_row
                            ),
                        )
                        accepted += 1
                    if args.verbose:
                        print(
                            f"[packet] raw={raw_packets} emitted={accepted} "
                            f"{row['Direct']} {row['SrcIP']}:{row['SrcPort']} "
                            f"-> {row['DstIP']}:{row['DstPort']} len={row['DataLength']}",
                            flush=True,
                        )
                if now - last_status >= args.status_seconds:
                    active = flow_index.active_flow_count()
                    flow_signature = flow_index.flow_signature()
                    if flow_signature and flow_signature != last_flow_signature:
                        frame_gate.reset_stream_buffers(
                            keep_flow_keys=flow_signature,
                            clear_session=False,
                        )
                        last_flow_signature = flow_signature
                        print(
                            f"[listen] bid flow changed ({active} active); "
                            "prune stale frame buffers, keep active session",
                            flush=True,
                        )
                    elif (
                        accepted == 0
                        and raw_packets >= 80
                        and frame_gate.ignored_frames >= 40
                        and now - last_parse_warn >= 15.0
                    ):
                        frame_gate.reset_stream_buffers(clear_session=True)
                        last_parse_warn = now
                        print(
                            "[warn] raw packets seen but no game frames parsed; "
                            f"reset buffers ignored={frame_gate.ignored_reasons_dict()}",
                            flush=True,
                        )
                    _write_source_status(
                        log_dir,
                        process_name=args.process_name,
                        filter_text=filter_text,
                        active_flows=active,
                        accepted_packets=accepted,
                        sniffed_packets=sniffed_packets,
                        raw_packets=raw_packets,
                        ignored_frames=frame_gate.ignored_frames,
                        dropped_bytes=frame_gate.dropped_bytes,
                        dropped_bytes_delta=frame_gate.dropped_bytes - last_dropped_bytes,
                        buffer_resyncs=frame_gate.buffer_resyncs,
                        boundary_resyncs=frame_gate.boundary_resyncs,
                        ignored_reasons=frame_gate.ignored_reasons_dict(),
                        ignored_samples=frame_gate.ignored_samples_dict(),
                        active_session_id=frame_gate.active_session_id,
                    )
                    if args.verbose:
                        print(
                            f"[status] active_flows={active} accepted={accepted}",
                            flush=True,
                        )
                    last_dropped_bytes = frame_gate.dropped_bytes
                    last_status = now
    except KeyboardInterrupt:
        print("[stop] interrupted", flush=True)
    except PermissionError:
        elevated = _process_is_elevated()
        _write_source_status(
            log_dir,
            process_name=args.process_name,
            filter_text=filter_text,
            active_flows=flow_index.active_flow_count(),
            accepted_packets=accepted,
            sniffed_packets=sniffed_packets,
            raw_packets=raw_packets,
            ignored_frames=frame_gate.ignored_frames,
            dropped_bytes=frame_gate.dropped_bytes,
            ignored_reasons=frame_gate.ignored_reasons_dict(),
            ignored_samples=frame_gate.ignored_samples_dict(),
            active_session_id=frame_gate.active_session_id,
            error_code="windivert_permission_denied",
            error_message="WinDivert open failed: permission denied",
            error_hint="用管理员权限启动 Hero Ref；若已管理员仍失败，检查安全软件/驱动冲突后重启。",
        )
        print(
            "[error] WinDivert capture requires an elevated PowerShell/admin process. "
            f"process_elevated={elevated}",
            file=sys.stderr,
        )
        if elevated:
            print(
                "[error] Process is elevated but WinDivert still denied. "
                "Try: python -m pip install -U pydivert ; reboot ; "
                "disable other packet capture tools.",
                file=sys.stderr,
            )
        return 2
    except FileNotFoundError as exc:
        _write_source_status(
            log_dir,
            process_name=args.process_name,
            filter_text=filter_text,
            active_flows=flow_index.active_flow_count(),
            accepted_packets=accepted,
            sniffed_packets=sniffed_packets,
            raw_packets=raw_packets,
            ignored_frames=frame_gate.ignored_frames,
            dropped_bytes=frame_gate.dropped_bytes,
            ignored_reasons=frame_gate.ignored_reasons_dict(),
            ignored_samples=frame_gate.ignored_samples_dict(),
            active_session_id=frame_gate.active_session_id,
            error_code="windivert_dependency_missing",
            error_message=str(exc),
            error_hint=(
                "WinDivert64.dll/sys 未找到或被安全软件隔离；重新解压 full 包，"
                "信任整个文件夹后用管理员入口启动。"
            ),
        )
        print(
            "[error] WinDivert dependency missing. Re-extract the full package "
            "and trust the whole folder in security software.",
            file=sys.stderr,
        )
        print(f"[error] {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        _write_source_status(
            log_dir,
            process_name=args.process_name,
            filter_text=filter_text,
            active_flows=flow_index.active_flow_count(),
            accepted_packets=accepted,
            sniffed_packets=sniffed_packets,
            raw_packets=raw_packets,
            ignored_frames=frame_gate.ignored_frames,
            dropped_bytes=frame_gate.dropped_bytes,
            ignored_reasons=frame_gate.ignored_reasons_dict(),
            ignored_samples=frame_gate.ignored_samples_dict(),
            active_session_id=frame_gate.active_session_id,
            error_code="windivert_open_failed",
            error_message=str(exc),
            error_hint="WinDivert 打开失败；检查管理员权限、安全软件、驱动占用或重启后再试。",
        )
        print(f"[error] WinDivert open failed: {exc}", file=sys.stderr)
        return 2
    finally:
        monitor.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
