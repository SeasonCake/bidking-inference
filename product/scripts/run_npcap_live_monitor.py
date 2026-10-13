"""Live capture via Npcap (libpcap/wpcap.dll), feeding the same monitor pipeline
as the WinDivert path. Multi-device by default (physical + tunnels + loopback),
each on its own DRAIN thread so a kernel buffer never overflows during settlement
bursts; the heavy parse/attribution/inference runs in the single consumer.

Verified recipe (2026-06-16): BPF `tcp and (port 10000)` + FlowIndex(BidKing.exe)
+ existing GameFrameGate yields real game frames + session ids, even under Clash
TUN. Downstream (FlowIndex / GameFrameGate / FatbeansWebhookMonitor) is reused
unchanged from run_windivert_live_monitor; only the packet source differs.

Run (admin PowerShell, Npcap installed):
    python scripts\\run_npcap_live_monitor.py
    python scripts\\run_npcap_live_monitor.py --device-name Wi-Fi   # single adapter
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import queue
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "scripts", ROOT / "src", ROOT / "hero_ref/src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from bidking_lab.live.capture import (  # noqa: E402
    CapturedPacket,
    NpcapBackend,
    NpcapDeviceOpenError,
    build_bpf_filter,
    default_capture_devices,
    list_devices,
)
from bidking_lab.live.stream_diag import StreamSeqDiag  # noqa: E402
from bidking_lab.live.tcp_reassembly import (  # noqa: E402
    TcpSeqReassembler,
    reassembly_enabled_from_env,
)
from bidking_lab.live.monitor import load_monitor_tables  # noqa: E402
from run_fatbeans_webhook_monitor import (  # noqa: E402
    FatbeansWebhookMonitor,
    WebhookMonitorConfig,
    _acquire_lock,
    _validated_package_generation_identity,
)
from run_windivert_live_monitor import (  # noqa: E402
    FlowIndex,
    GameFrameGate,
    _capture_time,
    _should_schedule_monitor_process,
    _write_source_status,
)


_FLOW_LOG_SALT = os.urandom(16)


def _packet_dedup_key(
    pkt: CapturedPacket,
) -> tuple[str, int, str, int, int, bytes]:
    """Identify the same TCP segment captured on more than one adapter.

    TCP sequence is part of the identity: repeated protocol payloads at different stream
    offsets are distinct bytes and must both reach the frame gate.  The former
    ``4-tuple + payload`` key silently ate such legitimate segments, leaving the gate with
    an unresolved hole even when the wire-side sequence detector later reached zero.
    """

    return (
        pkt.src_ip,
        pkt.src_port,
        pkt.dst_ip,
        pkt.dst_port,
        pkt.seq,
        pkt.payload,
    )


def _flow_log_digest(key: tuple[str, int, str, int]) -> str:
    raw = "\x1f".join(str(part) for part in key).encode("utf-8", errors="replace")
    return hashlib.blake2s(raw, key=_FLOW_LOG_SALT, digest_size=6).hexdigest()


def _format_seq_jump(
    *,
    gap_no: int,
    direction: str,
    key: tuple[str, int, str, int],
    jump_bytes: int,
    seq: int,
    wire_gaps: int,
) -> str:
    """Keep gap diagnostics useful without exposing the network four-tuple."""

    return (
        f"[seq-jump] #{gap_no} {direction} flow={_flow_log_digest(key)} "
        f"jump={jump_bytes}B seq={seq} (wire_gaps={wire_gaps})"
    )


def _select_devices(name_filter: str | None) -> list[tuple[str, str]]:
    if name_filter:
        for name, desc in list_devices():
            if name_filter.lower() in desc.lower() or name_filter.lower() in name.lower():
                return [(name, desc)]
        raise SystemExit(f"[error] no adapter matches {name_filter!r}")
    return default_capture_devices()


def _safe_device_description(value: str) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:160] or "未命名抓包设备"


def _device_statuses_snapshot(
    stats_sink: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return a serialization-safe snapshot without exposing adapter GUIDs."""

    statuses: list[dict[str, Any]] = []
    for raw in list(stats_sink.values()):
        if not isinstance(raw, Mapping):
            continue
        status = dict(raw)
        status["description"] = _safe_device_description(
            str(status.get("description") or "")
        )
        statuses.append(status)
    statuses.sort(key=lambda item: str(item.get("description") or ""))
    return statuses


def _capture_open_error_payload(
    device_statuses: list[Mapping[str, Any]],
    *,
    sniffed_packets: int,
) -> dict[str, str]:
    """Promote device-open failures only while no adapter is receiving packets."""

    failures = [
        status
        for status in device_statuses
        if status.get("error_code") == "npcap_device_open_failed"
    ]
    usable_device = any(status.get("open_ok") is True for status in device_statuses)
    if not failures or usable_device or sniffed_packets > 0:
        return {}

    descriptions = ", ".join(
        _safe_device_description(str(status.get("description") or ""))
        for status in failures[:3]
    )
    attempted_fallback = any(
        bool(status.get("non_promisc_fallback_attempted"))
        for status in failures
    )
    hint = (
        "混杂模式和普通抓包兼容模式均无法打开该网卡；请导出诊断。"
        if attempted_fallback
        else "该网卡初始化失败；请导出诊断。"
    )
    return {
        "error_code": "npcap_device_open_failed",
        "error_message": f"无法打开抓包网卡: {descriptions}",
        "error_hint": hint,
    }


def _drain_device(
    device: str,
    bpf: str,
    q: "queue.Queue",
    stop: threading.Event,
    stats_sink: dict[str, dict[str, Any]],
    device_label: str = "",
) -> None:
    """Capture-only worker: open device, push (device_label, CapturedPacket) to the
    shared queue — the label rides into row["CaptureDevice"] so desync/ignored evidence
    attributes frames to an adapter (multi-adapter interleave diagnosis, 0710).

    Also stashes per-device kernel pcap_stats (recv/drop) + queue.Full drop count
    into ``stats_sink`` so the consumer loop can surface invisible kernel drops
    (the 5606 under-count path) and user-space queue backpressure in the status
    line + capture_source_status.json. Stats are polled from this drain thread
    (the handle owner) so pcap_stats is never touched cross-thread.
    """
    qfull = 0
    last_stats_t = 0.0
    description = _safe_device_description(device_label)
    stats_sink[device] = {
        "description": description,
        "state": "opening",
        "open_ok": False,
        "capture_mode": "opening",
        "promisc": None,
        "non_promisc_fallback_attempted": False,
        "recv": 0,
        "drop": 0,
        "ifdrop": 0,
        "qfull": 0,
    }
    try:
        with NpcapBackend(device, bpf=bpf) as backend:
            base_status = {
                "description": description,
                "state": "open",
                "open_ok": True,
                "capture_mode": backend.capture_mode,
                "promisc": 1 if backend.promiscuous_mode else 0,
                "non_promisc_fallback_attempted": bool(
                    backend.non_promisc_fallback_attempted
                ),
                "recv": 0,
                "drop": 0,
                "ifdrop": 0,
                "qfull": 0,
            }
            stats_sink[device] = base_status
            if backend.non_promisc_fallback_attempted:
                print(
                    f"[npcap] compatibility mode active on {description}: promisc=0",
                    flush=True,
                )
            for pkt in backend.packets():
                if stop.is_set():
                    break
                try:
                    q.put((device_label, pkt), timeout=1.0)
                except queue.Full:
                    qfull += 1  # consumer lagging; drop rather than block the drain
                    stats_sink[device]["qfull"] = qfull
                    continue
                now = time.monotonic()
                if now - last_stats_t >= 2.0:
                    last_stats_t = now
                    try:
                        st = backend.stats()
                        if st:
                            st["qfull"] = qfull
                            stats_sink[device] = {**base_status, **st}
                    except Exception:  # noqa: BLE001 — stats must never break capture
                        pass
            try:
                st = backend.stats()
                if st:
                    st["qfull"] = qfull
                    stats_sink[device] = {**base_status, **st}
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001 - surface per-device failure, keep others alive
        fallback_attempted = bool(
            isinstance(exc, NpcapDeviceOpenError)
            and exc.non_promisc_fallback_attempted
        )
        error_summary = " ".join(str(exc).split())[:320]
        stats_sink[device] = {
            "description": description,
            "state": "error",
            "open_ok": False,
            "capture_mode": "open_failed",
            "promisc": None,
            "non_promisc_fallback_attempted": fallback_attempted,
            "error_code": "npcap_device_open_failed",
            "error_message": error_summary,
            "error_hint": (
                "混杂模式和普通抓包兼容模式均失败；请导出诊断。"
                if fallback_attempted
                else "网卡抓包初始化失败；请导出诊断。"
            ),
            "recv": 0,
            "drop": 0,
            "ifdrop": 0,
            "qfull": qfull,
        }
        print(
            f"[npcap] drain stopped on {description}: {error_summary}",
            flush=True,
        )


def _prune_round_archive(raw_dir: Path, *, threshold: int = 500, keep: int = 200) -> None:
    """每局结算会在 raw/archive/ 留一份 *_reset.json 对局归档, 玩家长期使用会越积越多。
    启动时检查: 归档 json 数 > threshold(500) 就按修改时间删旧, 只留最新 keep(200) 份。
    只清自动归档, 不动 exports/(玩家主动导出的诊断 zip) 和 round_snapshots/(按轮次名覆盖、本就有界)。
    纯尽力清理: 任何 IO 异常都吞掉, 绝不让清理失败影响抓包启动。"""
    try:
        archive_dir = raw_dir / "archive"
        if not archive_dir.is_dir():
            return
        files = [p for p in archive_dir.rglob("*.json") if p.is_file()]
        if len(files) <= threshold:
            return
        files.sort(key=lambda p: p.stat().st_mtime)  # 旧 -> 新
        removed = 0
        for p in files[:-keep]:  # 保留最新 keep 份, 其余删除
            try:
                p.unlink()
                removed += 1
            except OSError:
                continue
        print(f"[cleanup] 对局归档 {len(files)} 份 > {threshold}, 删旧 {removed} 份, 保留最新 {keep} 份", flush=True)
    except Exception:  # noqa: BLE001 — 清理永不阻塞启动
        pass


def _prune_session_history(path: Path, *, keep_sessions: int = 300) -> None:
    """启动时只保留最近 ``keep_sessions`` 个 session 的完整逐轮历史。

    ``sessions.jsonl`` 每局会写入多轮 reading/bidding/settled 大对象，不能按 300 行硬截断，
    否则会切掉同一局前面的公开证据并破坏本机对局记录。这里先按 session 最后出现顺序选出
    最近 300 局，再原子重写；文件未超过上限、存在坏 JSON 或 IO 失败时一律保留原文件。
    """
    if keep_sessions < 1 or not path.is_file():
        return
    first_row: dict[str, int] = {}
    last_row: dict[str, int] = {}
    row_count = 0
    try:
        with path.open("r", encoding="utf-8") as fh:
            for row_index, raw in enumerate(fh):
                row_count = row_index + 1
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError(f"row {row_count} is not an object")
                session_id = str(
                    payload.get("session_id") or payload.get("session_hash") or ""
                ).strip()
                if not session_id:
                    continue
                first_row.setdefault(session_id, row_index)
                last_row[session_id] = row_index
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(
            f"[cleanup-warning] 本地对局历史未裁剪: {type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return

    if len(last_row) <= keep_sessions:
        return

    retained = sorted(last_row, key=last_row.__getitem__)[-keep_sessions:]
    retained_ids = set(retained)
    cutoff_row = min(first_row[session_id] for session_id in retained)
    tmp_path: Path | None = None
    kept_rows = 0
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            delete=False,
        ) as out:
            tmp_path = Path(out.name)
            with path.open("r", encoding="utf-8") as source:
                for row_index, raw in enumerate(source):
                    if row_index < cutoff_row:
                        continue
                    payload = json.loads(raw)
                    session_id = str(
                        payload.get("session_id") or payload.get("session_hash") or ""
                    ).strip()
                    if session_id and session_id not in retained_ids:
                        continue
                    out.write(raw if raw.endswith("\n") else raw + "\n")
                    kept_rows += 1
        tmp_path.replace(path)
        tmp_path = None
        print(
            f"[cleanup] 本地对局历史 {len(last_row)} 局/{row_count} 行 -> "
            f"最近 {keep_sessions} 局/{kept_rows} 行",
            flush=True,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(
            f"[cleanup-warning] 本地对局历史裁剪失败，已保留原文件: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError as exc:
                print(
                    f"[cleanup-warning] 临时文件清理失败: {tmp_path}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )


def main() -> int:
    ap = argparse.ArgumentParser(description="Npcap live capture -> fatbeans monitor.")
    ap.add_argument("--log-dir", default=str(ROOT / "data/logs/live"))
    ap.add_argument("--raw-dir", default=None)
    ap.add_argument("--process-name", default="BidKing.exe")
    ap.add_argument("--server-port", type=int, action="append", default=None,
                    help="game server TCP port (default 10000)")
    ap.add_argument("--device-name", default=None, help="single adapter substring (default: all real adapters)")
    ap.add_argument("--flow-refresh-seconds", type=float, default=1.0)
    ap.add_argument("--status-seconds", type=float, default=2.0)
    ap.add_argument("--include-loopback", action="store_true", default=True)
    ap.add_argument("--queue-max", type=int, default=20000)
    ap.add_argument("--no-lock", action="store_true")
    ap.add_argument("--package-generation-identity", default="")
    # inference trial knobs — production defaults (match run_windivert_live_monitor)
    ap.add_argument("--n-trials", type=int, default=500)
    ap.add_argument("--roi-trials", type=int, default=250)
    ap.add_argument("--shadow-trials", type=int, default=None)
    ap.add_argument("--full-shadow-trials", type=int, default=20)
    ap.add_argument("--run-debug-shadows", action="store_true", default=False)
    ap.add_argument("--seed", type=int, default=20260530)
    args = ap.parse_args()

    log_dir = Path(args.log_dir)
    raw_dir = Path(args.raw_dir) if args.raw_dir else log_dir / "raw"
    _prune_round_archive(raw_dir)  # 启动即清理过量对局归档, 避免长期使用越积越多
    if not args.no_lock:
        try:
            monitor_path = Path(sys.argv[0])
            package_generation_identity = _validated_package_generation_identity(
                args.package_generation_identity,
                monitor_path=monitor_path,
            )
            _acquire_lock(
                log_dir,
                monitor_path=monitor_path,
                package_generation_identity=package_generation_identity,
            )
        except RuntimeError as exc:
            print(f"[error] {exc}", file=sys.stderr)
            return 2
    _prune_session_history(log_dir / "sessions.jsonl", keep_sessions=300)

    ports = args.server_port or [10000]
    bpf = build_bpf_filter(ports)
    devices = _select_devices(args.device_name)
    if not devices:
        print("[error] no capture devices found (Npcap installed?)", file=sys.stderr)
        return 2

    tables = load_monitor_tables()
    monitor = FatbeansWebhookMonitor(
        config=WebhookMonitorConfig(
            log_dir=log_dir,
            raw_dir=raw_dir,
            process_name=args.process_name,
            server_ports=tuple(ports),
            n_trials=args.n_trials,
            roi_trials=args.roi_trials,
            shadow_trials=args.shadow_trials,
            full_shadow_trials=max(1, int(args.full_shadow_trials)),
            run_debug_shadows=bool(args.run_debug_shadows),
            seed=args.seed,
            file_name="npcap_live.json",
            source_name="npcap",
            packet_count_key="npcap_frames",
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
        server_ports=set(ports),
        include_loopback=args.include_loopback,
    )
    flow_index.refresh_if_due(force=True)
    frame_gate = GameFrameGate()
    tcp_reassembler = (
        TcpSeqReassembler() if reassembly_enabled_from_env() else None
    )

    print(f"[listen] Python: {sys.executable}", flush=True)
    print(f"[listen] bpf={bpf!r} process={args.process_name} log_dir={log_dir}", flush=True)
    print(
        "[listen] tcp-seq-reassembly="
        + ("enabled" if tcp_reassembler is not None else "disabled (rollback arm)"),
        flush=True,
    )
    for name, desc in devices:
        print(f"[listen] device: {desc}  ({name})", flush=True)

    def _version_banner() -> None:  # 后台比对版本(连服务器 /version), 永不阻塞或拖垮启动。
        try:
            from bidking_lab.live import version_check
            from bidking_lab.live.startup_version_notice import check_and_publish_startup_version

            # 每次 monitor 启动只查一次；结果落到本地状态文件供 overlay 顶栏展示，
            # overlay 不再发第二次 /version 请求，也不把版本检查做成周期心跳。
            st = check_and_publish_startup_version(log_dir)
            print(f"[version] {version_check.format_status_line(st)}", flush=True)
        except Exception as exc:  # noqa: BLE001  版本检查失败不影响监控
            print(f"[version] 跳过(版本检查异常: {type(exc).__name__})", flush=True)

    threading.Thread(target=_version_banner, daemon=True).start()

    def _activation_banner() -> None:  # 后台激活(连服务器 /activate), 永不阻塞/拖垮启动。
        # 0.2.2 付费版【真强制】: startup_activate 存进 enforce._LAST, _process_snapshot 据此 gate 推理。
        # 周期重激活(缓存优先, 便宜)= 捡起 overlay 里贴 token 付费 / 服务端撤销 的变更, 让 monitor 跟着解锁/上锁。
        try:
            from bidking_lab.secure import enforce
        except Exception as exc:  # noqa: BLE001  激活失败不影响监控
            print(f"[activation] 跳过(激活异常: {type(exc).__name__})", flush=True)
            return
        first = True
        consecutive_transient_failures = 0
        last_status = None
        while True:
            started = time.monotonic()
            try:
                res = enforce.startup_activate() if first else enforce.refresh_activation()
            except Exception:  # noqa: BLE001  周期刷新失败不影响监控
                res = None
            elapsed_ms = round((time.monotonic() - started) * 1000.0, 1)
            transient = enforce.is_transient_activation_failure(res)
            consecutive_transient_failures = (
                consecutive_transient_failures + 1 if transient else 0
            )
            delay = enforce.activation_refresh_delay(res, consecutive_transient_failures)
            status = (
                bool(getattr(res, "activated", False)),
                str(getattr(res, "mode", "none")),
                str(getattr(res, "reason", "error")),
                str(getattr(res, "source", "unknown")),
            )
            if first or transient or status != last_status:
                print(
                    f"[activation] {enforce.activation_banner(res)} "
                    f"elapsed_ms={elapsed_ms:.1f} next_retry_seconds={delay:g}",
                    flush=True,
                )
            last_status = status
            first = False
            time.sleep(delay)

    threading.Thread(target=_activation_banner, daemon=True).start()

    q: "queue.Queue" = queue.Queue(maxsize=args.queue_max)
    stop = threading.Event()
    # Per-device kernel pcap_stats + queue.Full drops, stashed by each drain thread.
    device_stats: dict[str, dict[str, Any]] = {}
    drains = [
        threading.Thread(target=_drain_device, args=(name, bpf, q, stop, device_stats, desc),
                         name=f"npcap-drain-{i}", daemon=True)
        for i, (name, desc) in enumerate(devices)
    ]
    for t in drains:
        t.start()

    sort_id = 1
    accepted = sniffed = raw_packets = deduped = 0
    last_status = 0.0
    last_dropped_bytes = last_kdrop = last_qfull = last_krecv = 0
    # Cross-adapter dedup: the same TCP segment is captured on multiple adapters
    # (e.g. physical Wi-Fi + Clash TUN). Feeding duplicates into one GameFrameGate
    # corrupts per-stream reassembly (length prefix misaligns) -> 0 frames. Drop a
    # segment already seen (by 4-tuple + TCP seq + payload) within a recent window.
    # seq is required: protocol heartbeats and repeated game-state chunks can have identical
    # payload at different stream offsets; treating those as duplicates creates a real hole.
    DEDUP_MAX = 4096
    seen_segments: set[tuple[str, int, str, int, int, bytes]] = set()
    seen_order: deque[tuple[str, int, str, int, int, bytes]] = deque()
    # TCP-seq 连续性检测(只读取证, 不改任何包)。0711 诊断包证明: 27/30 次楔死是【真帧的尾巴
    # 没到】(帧头合法, 缓冲短 45~9990B), 不是"锁死在假长度前缀"。既然游戏本体显示正常, 字节
    # 一定过了网卡 -> 是我们自己的抓包管道丢的。kdrop / qfull 恒为 0 已排除两条【有计数】的路,
    # 这里量出没人数过的那条。
    # 【dedup 前后各测一次】= 一次实战就能把两个嫌疑分开:
    # 【必须看 unresolved_gap_bytes, 不能只看 gaps】:多线程/多网卡乱序会先前跳、后回填。
    #   gate.unresolved == wire.unresolved -> 跨网卡 dedup 没新增未回填区间
    #   gate.unresolved >  wire.unresolved -> 是我们自己的 dedup 把需要的段吃了(它按 payload判重,认不出
    #                              一个合法重复的段其实是本帧的一部分)
    # kill: BIDKING_DISABLE_STREAM_DIAG=1
    seq_diag_on = os.environ.get("BIDKING_DISABLE_STREAM_DIAG") != "1"
    seq_wire = StreamSeqDiag(label="wire") if seq_diag_on else None
    seq_gate = StreamSeqDiag(label="gate") if seq_diag_on else None
    GAP_LOG_MAX = 20  # 前 20 个空洞打详细行(够和 [gate] resync 对时间线), 之后只计数
    gap_logged = 0
    try:
        while True:
            try:
                dev_label, pkt = q.get(timeout=0.5)
            except queue.Empty:
                dev_label, pkt = "", None
            now = time.monotonic()
            if pkt is not None:
                sniffed += 1
                if seq_wire is not None:
                    seq_wire.observe(
                        (pkt.src_ip, pkt.src_port, pkt.dst_ip, pkt.dst_port),
                        pkt.seq, len(pkt.payload),
                    )
                dedup_key = _packet_dedup_key(pkt)
                if dedup_key in seen_segments:
                    deduped += 1
                    pkt = None
                else:
                    if len(seen_order) >= DEDUP_MAX:
                        seen_segments.discard(seen_order.popleft())
                    seen_order.append(dedup_key)
                    seen_segments.add(dedup_key)
            if pkt is not None:
                key = (pkt.src_ip, pkt.src_port, pkt.dst_ip, pkt.dst_port)
                match = flow_index.match(key)
                if match is not None:
                    raw_packets += 1
                    if seq_gate is not None:
                        verdict = seq_gate.observe(key, pkt.seq, len(pkt.payload))
                        if verdict == "gap" and gap_logged < GAP_LOG_MAX:
                            gap_logged += 1
                            print(
                                _format_seq_jump(
                                    gap_no=seq_gate.gaps,
                                    direction=match.direction,
                                    key=key,
                                    jump_bytes=seq_gate.last_gap_bytes,
                                    seq=pkt.seq,
                                    wire_gaps=seq_wire.gaps if seq_wire else -1,
                                ),
                                flush=True,
                            )
                    ordered_payload = (
                        tcp_reassembler.feed(key, pkt.seq, pkt.payload, now=now)
                        if tcp_reassembler is not None
                        else pkt.payload
                    )
                    # A forward TCP gap is held here. Later application bytes must not
                    # enter GameFrameGate until the missing segment closes the gap.
                    if ordered_payload:
                        row = {
                            "SortID": sort_id, "Direct": match.direction, "Protocol": "Tcp",
                            "SrcIP": pkt.src_ip, "SrcPort": pkt.src_port,
                            "DstIP": pkt.dst_ip, "DstPort": pkt.dst_port,
                            "CaptureTime": _capture_time(),
                            "Data": base64.b64encode(ordered_payload).decode("ascii"),
                            "DataLength": len(ordered_payload),
                            "PID": match.pid, "ProcessName": match.process_name, "Source": "Npcap",
                            "CaptureDevice": dev_label,
                        }
                        sort_id += 1
                        emit = frame_gate.feed_row(row)
                        if emit.reset_session:
                            _emit_msgs = [str(r.get("MessageID")) for r in emit.rows[:6]]
                            print(
                                f"[reset] npcap session-reset "
                                f"new={getattr(frame_gate, '_current_session_id', None)} "
                                f"emit_rows={len(emit.rows)} msgids={_emit_msgs}",
                                flush=True,
                            )
                            # The game session can change on one continuous TCP stream.
                            # Do not clear seq state here or the next segment would be
                            # accepted as an arbitrary new anchor; process/flow reset and
                            # stale timeout own reassembler lifecycle instead.
                            monitor.reset_rows()
                        for game_row in emit.rows:
                            monitor.accept_row(
                                game_row,
                                schedule_process=_should_schedule_monitor_process(game_row),
                            )
                            accepted += 1
            if now - last_status >= args.status_seconds:
                if tcp_reassembler is not None:
                    tcp_reassembler.sweep(now=now)
                    tcp_reassembly_status = tcp_reassembler.as_dict()
                else:
                    tcp_reassembly_status = {"enabled": False}
                device_statuses = _device_statuses_snapshot(device_stats)
                kdrop = sum(int(s.get("drop", 0)) for s in device_statuses)
                krecv = sum(int(s.get("recv", 0)) for s in device_statuses)
                qfull = sum(int(s.get("qfull", 0)) for s in device_statuses)
                # ifdrop(网卡/驱动层丢包)一直被 stats() 采集, 却从来没被报出来过 —— 而它恰好是
                # "kdrop=0 但字节还是没了" 时最先要看的那条路。snap/ip_truncated 同理: 静默截断。
                kifdrop = sum(int(s.get("ifdrop", 0)) for s in device_statuses)
                snaptrunc = sum(int(s.get("snap_truncated", 0)) for s in device_statuses)
                iptrunc = sum(int(s.get("ip_truncated", 0)) for s in device_statuses)
                dropped = frame_gate.dropped_bytes
                open_error = _capture_open_error_payload(
                    device_statuses,
                    sniffed_packets=sniffed,
                )
                _write_source_status(
                    log_dir,
                    source="npcap",
                    process_name=args.process_name,
                    filter_text=bpf,
                    active_flows=flow_index.active_flow_count(),
                    accepted_packets=accepted,
                    sniffed_packets=sniffed,
                    raw_packets=raw_packets,
                    ignored_frames=frame_gate.ignored_frames,
                    dropped_bytes=dropped,
                    dropped_bytes_delta=dropped - last_dropped_bytes,
                    kernel_drop=kdrop,
                    kernel_recv=krecv,
                    queue_full_drops=qfull,
                    buffer_resyncs=frame_gate.buffer_resyncs,
                    boundary_resyncs=frame_gate.boundary_resyncs,
                    active_session_id=getattr(frame_gate, "_current_session_id", None),
                    ignored_reasons=frame_gate.ignored_reasons_dict(),
                    ignored_samples=frame_gate.ignored_samples_dict(),
                    kernel_ifdrop=kifdrop,
                    snap_truncated=snaptrunc,
                    ip_truncated=iptrunc,
                    deduped_packets=deduped,
                    seq_gaps_wire=seq_wire.as_dict() if seq_wire else None,
                    seq_gaps_gate=seq_gate.as_dict() if seq_gate else None,
                    tcp_reassembly=tcp_reassembly_status,
                    error_code=open_error.get("error_code"),
                    error_message=open_error.get("error_message"),
                    error_hint=open_error.get("error_hint"),
                    device_statuses=device_statuses,
                )
                # krecv(内核收包)加进状态行: 卡住诊断关键——flatline 窗内对比 Δkrecv vs Δsniffed 可判
                # "包进了内核但 sniffed 前丢(Δkrecv≫Δsniffed=capture可修)" vs "服务器没推状态帧(Δkrecv≈Δsniffed
                # 只心跳)"。此前只写进 capture_source_status.json(单快照·无时间序列),无法看卡住窗的增量。
                print(
                    f"[npcap] sniffed={sniffed} deduped={deduped} matched={raw_packets} "
                    f"accepted_frames={getattr(frame_gate, 'accepted_frames', 0)} "
                    f"session={getattr(frame_gate, '_current_session_id', None)} "
                    f"qdepth={q.qsize()} "
                    f"krecv={krecv}(+{krecv - last_krecv}) "
                    f"dropped_bytes={dropped}(+{dropped - last_dropped_bytes}) "
                    f"kdrop={kdrop}(+{kdrop - last_kdrop}) "
                    f"qfull={qfull}(+{qfull - last_qfull}) "
                    f"ifdrop={kifdrop} trunc={snaptrunc}/{iptrunc} "
                    # jump=观测到的前跳事件；unresolved=截至此刻仍未被迟到段回填的字节，后者才是缺失证据。
                    f"jump={seq_wire.gaps if seq_wire else -1}->{seq_gate.gaps if seq_gate else -1} "
                    f"unresolved={seq_wire.unresolved_gap_bytes if seq_wire else -1}"
                    f"->{seq_gate.unresolved_gap_bytes if seq_gate else -1}B "
                    f"ovl={seq_gate.overlaps if seq_gate else -1} "
                    f"tcpseq={'on' if tcp_reassembler is not None else 'off'} "
                    f"tcpbuf={tcp_reassembly_status.get('buffered_bytes', 0)}B "
                    f"tcpgap={tcp_reassembly_status.get('unresolved_gap_bytes', 0)}B "
                    f"resync={frame_gate.buffer_resyncs}"
                    f"(boundary={frame_gate.boundary_resyncs})",
                    flush=True,
                )
                last_dropped_bytes = dropped
                last_kdrop = kdrop
                last_qfull = qfull
                last_krecv = krecv
                last_status = now
    except KeyboardInterrupt:
        print("\n[npcap] stopping ...", flush=True)
    finally:
        stop.set()
        monitor.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
