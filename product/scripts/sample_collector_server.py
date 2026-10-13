# -*- coding: utf-8 -*-
"""轻量样本收集服务器:接收客户端自动上传的【结算样本】,去重落盘,供 reconstruct_map_prior 分析。

只收 gameplay 统计(map_id + 每品质件数/格子/价值 + 占格/总件 + 是否残缺),不收任何 PII;
session_id 在客户端已 hash 匿名化(仅用于去重)。纯 stdlib,无需装依赖。

启动:
  python scripts/sample_collector_server.py --port 18900 --key "改成你的密钥" --out data/collected/samples.jsonl

接口:
  POST /ingest   header X-Api-Key: <key>   body = 一个样本 dict 或 dict 列表  -> {accepted,duplicate,rejected}
  GET  /stats                                                                  -> 每图样本数 + 总数
  GET  /version                                                                -> 版本清单(公开;客户端启动比对/提示更新)
  GET  /healthz                                                                -> ok
  GET  /activation-readiness                                                   -> 激活模块无写入兼容性门

客户端见 src/bidking_lab/live/sample_upload.py(monitor 结算时自动调用)。
"""
from __future__ import annotations
import argparse, base64, errno, gzip, hashlib, hmac, ipaddress, json, os, re, ssl, sys, threading, time
import math
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from collections import Counter, OrderedDict, defaultdict
from urllib.parse import parse_qs, urlencode, urlsplit

import upload_identity_store
import activation_core

try:  # production candidates flatten this exact shared source beside the collector
    import quote_preferences as _quote_preferences
except ModuleNotFoundError as exc:
    if exc.name != "quote_preferences":
        raise
    from bidking_lab.live import quote_preferences as _quote_preferences

try:  # production deploy flattens the shared module beside this script
    import activation_http_e2ee
except ModuleNotFoundError as exc:
    if exc.name != "activation_http_e2ee":
        raise
    from bidking_lab.secure import activation_http_e2ee

_LOCK = threading.Lock()
_SEEN: set[str] = set()          # 已收【主库】session_hash(去重)
_QUAR_SEEN: set[str] = set()     # 已隔离 session_hash(独立去重: 不进 _SEEN, 故同 session 后来补传成有效样本仍能进主库救回)
_MAP_COUNT: Counter = Counter()  # map_id -> 样本数
_OUT: Path = Path("data/collected/samples.jsonl")
_KEY: str = ""                   # ingest key(/ingest 用, 打包发给客户端)
_EXPORT_KEY: str = ""            # export key(/export 用, 只管理员留)。空=/export 【fail-closed 拒绝】(见 do_GET /export:绝不回退 ingest key)
_DASH_PASS: str = ""             # /dashboard + /stats 访问口令(HTTP Basic)。空+公网绑定=拒绝(fail-closed); 空+loopback=开放(本地 dev)
_DASH_ALLOW_ANON: bool = False   # 无口令时是否放行匿名看板访问 —— 仅 loopback 绑定为 True; 公网(0.0.0.0)绑定=False(防外人裸看全库)
_ACT = None                      # 激活服务 ActivationService(/activate /telemetry);配了 activation_key 才启用
_ACTIVATION_HTTP_E2EE = None     # 可选HTTP密文载体；缺私钥时严格关闭
_UPLOAD_IDENTITY_MODE = "compat"
_UPLOAD_IDENTITY_STORE = None
_HISTORY_INDEX_DB_FILE: Path | None = None
_SAMPLE_HISTORY_INDEX = None


class CollectorHistoryUnavailable(RuntimeError):
    """Derived sample index unavailable; never equivalent to unseen session."""


def _new_sample_history_index(db_file: Path):
    """Use the existing readers' date/qualification provider, pinned by bytes."""
    import my_records
    import public_stats
    import sample_dashboard
    from sample_history_index import HistoryIndex, Projection

    digest = hashlib.sha256(b"collector-reader-projection-v1\0")
    for module in (my_records, public_stats, sample_dashboard):
        digest.update(Path(module.__file__).read_bytes())
    # Persisted quarantine classification and provider live in this module.
    digest.update(Path(__file__).read_bytes())

    def project(record, kind):
        if kind == "quarantine":
            return Projection(quarantine_reason=_stored_quarantine_reason(record))
        error = False
        try:
            ts = my_records._record_ts(record)
        except (OverflowError, TypeError, ValueError):
            ts, error = None, True
        try:
            day = datetime.fromtimestamp(ts, tz=_SHANGHAI_TZ).date().isoformat() if ts is not None else None
        except (OverflowError, OSError, TypeError, ValueError):
            day = None
        return Projection(public_stats._day_of(record), day,
                          bool(sample_dashboard._dashboard_sample_allowed(record) and record.get("final_quality_counts")), error)

    return HistoryIndex(db_file, _OUT, _quarantine_path(), projector=project,
                        parser_version="collector-reader-v1:" + digest.hexdigest())


def _refresh_sample_history_index(*, publish_maps: bool = True) -> None:
    """Caller holds _LOCK; raw append and derived publication stay serialized."""
    global _MAP_COUNT, _HISTORY_INDEX_STATE
    if _SAMPLE_HISTORY_INDEX is None:
        if _HISTORY_INDEX_DB_FILE is not None:
            raise CollectorHistoryUnavailable("history_index_unavailable")
        return
    try:
        _SAMPLE_HISTORY_INDEX.refresh()
        if publish_maps:
            _MAP_COUNT = _SAMPLE_HISTORY_INDEX.summary()["map_count"]
    except Exception as exc:  # noqa: BLE001 - fixed data-plane failure, no raw exception
        with _HISTORY_INDEX_LOCK:
            _HISTORY_INDEX_STATE = "failed"
        print(f"[collector-history] category=derived_index_unavailable class={type(exc).__name__}", flush=True)
        raise CollectorHistoryUnavailable("history_index_unavailable") from exc


def _history_contains(kind: str, session_hash: object) -> bool:
    if _SAMPLE_HISTORY_INDEX is not None:
        return _SAMPLE_HISTORY_INDEX.contains(kind, session_hash)
    return session_hash in (_SEEN if kind == "main" else _QUAR_SEEN)


def _log_sample_payload_read(name: str, source) -> None:
    if _SAMPLE_HISTORY_INDEX is None:
        return  # legacy my text prefilter has no byte-exact read counter
    print(f"[collector-history-read] op={name} payload_read_bytes={int(getattr(source, 'total_payload_read_bytes', 0))} "
          f"payload_read_rows={int(getattr(source, 'total_payload_read_rows', 0))} "
          f"passes={int(getattr(source, 'passes', 1))}", flush=True)

# 历史样本索引只支撑上传去重、/stats 与重型全库页面；激活权威状态不依赖它。
# 模块被测试直接导入时保持 ready，生产 main() 会在开始监听前切到 loading，并在
# 后台用局部集合完成全量扫描后一次性发布。任何半成品索引都不能被请求线程观察。
_HISTORY_INDEX_LOCK = threading.Lock()
_HISTORY_INDEX_STATE = "ready"
_HISTORY_INDEX_DONE = threading.Event()
_HISTORY_INDEX_DONE.set()
_HISTORY_INDEX_THREAD = None
_HISTORY_INDEX_RETRY_AFTER_SECONDS = 5
_HISTORY_INDEX_STATES = frozenset({"loading", "ready", "failed"})

# /dashboard 渲染缓存。每次请求原本都全量读 samples.jsonl(2万+行)+建 HTML(内嵌大 JSON)——很重;
# ThreadingHTTPServer 下多人同时打开 = 多线程同时全量重算 → 拖垮/崩服务器(与激活同一进程)。改为按
# 样本文件 (mtime, size) 缓存【已建的 plain + gzip 两份字节】:数据没变直接返回缓存(instant, 零重算),
# 只有新样本进来(文件变)才重建一次。锁防"新数据后一堆并发请求同时重建"的惊群。私有统计无需逐秒
# 更新；持续上传时最多每 5 分钟重建一次，避免每个访客都替全库做一次同步重算。
DASH_REBUILD_INTERVAL = 300.0
_DASH_CACHE: dict = {"key": None, "plain": None, "gz": None, "etag": None, "built_mono": 0.0}
_DASH_LOCK = threading.Lock()
# 稀有藏品、道具/技能、玩家排位只在对应模块点击时加载；否则 55k 行会把约几十 MB 明细塞进首屏。
_DASH_DETAIL_CACHE: dict[str, dict] = {}
_DASH_DETAIL_LOCK = threading.Lock()
QUARANTINE_SUMMARY_TTL = 300.0
_QUARANTINE_SUMMARY_CACHE: dict = {
    "key": None,
    "value": None,
    "built_mono": 0.0,
}
_QUARANTINE_SUMMARY_LOCK = threading.Lock()

# /public 公开大数据页(0.2.7)。与 /dashboard 同款缓存: 4.5万行全量聚合+渲染很重, 而这个端点是
# 【公开无鉴权】的 —— 任何人都能刷, 不缓存就是一个现成的 DoS 面(还跟激活服务同进程!)。
# 独立于 _DASH_CACHE: 两个页的渲染不同, 共用一份缓存会互相踩。
#
# 【重算节流】(作者 0712): 光按"数据变了就重建"不够 —— 群友在持续上传, samples.jsonl 几乎一直在变,
# 于是每次有人打开公开页都可能触发一次全量聚合(3万+行), 而这跟【激活服务是同一个进程】, 白烧的
# CPU 会拖慢别人激活。公共统计本来就没有实时的必要(掉率是长期统计, 多等半小时毫无影响), 故设
# 最短重建间隔: 距上次重建不足 PUBLIC_REBUILD_INTERVAL 就直接吃缓存, 哪怕数据已经变了。
# ⚠️ 个人对局记录页(uid-scope)不走 30 分钟节流 —— 那个要【近实时】(用户打完一局就想看到)；
# 但它仍须流式扫全库，所以另有 5 秒短缓存、uid 限流和单 build 锁保护。
PUBLIC_REBUILD_INTERVAL = 1800.0   # 30 分钟
_PUB_CACHE: dict = {"key": None, "plain": None, "gz": None, "etag": None,
                    "built_mono": 0.0, "built_wall": 0.0}
_PUB_LOCK = threading.Lock()
# 生产 samples.jsonl 已超过 200 MiB。public/dashboard/detail/my/export 原先只有各自的锁，
# ThreadingHTTPServer 下仍可跨路由并行展开多份全库 Python 对象；4 GiB 实例已因此反复 OOM。
# 统一串行化所有重型全库操作，保留轻量 health/version/activate/ingest 的独立响应能力。
_HEAVY_SCAN_LOCK = threading.Lock()
_CACHE_SCHEMA = "oom-window-v1"
_SHANGHAI_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
DASH_DEFAULT_DAYS = 30
PUBLIC_DEFAULT_DAYS = 30
DASH_PRESET_DAYS = frozenset({7, 30, 90})
DASH_MAX_WINDOW_DAYS = 366
HEAVY_RETRY_AFTER_SECONDS = 5
_DASH_DETAIL_CACHE_MAX_BYTES = 32 << 20
# /healthz 自检结果缓存(30s): 该端点不限流(update.sh 依赖), 不缓存 = 免费 CPU 燃烧点。
_HEALTH_CACHE: dict = {"ts": 0.0, "code": 200, "payload": None}

# 激活服务与 dashboard/ingest 共进程。这里只记聚合耗时与固定结果分类，不记
# device、token、UID、IP、URL、header、payload 或异常正文。所有分类都先经过
# exact-type allowlist；畸形/未来值只能折叠为 none/other，不能进入日志原文。
_ACT_METRICS_LOCK = threading.Lock()
_ACT_METRIC_MODES = ("paid", "trial", "none")
_ACT_METRIC_REASONS = (
    "ok",
    "bad_request",
    "rate_limited",
    "activation_not_configured",
    "upgrade_required",
    "security_key_unavailable",
    "bad_token",
    "revoked",
    "device_revoked",
    "device_mismatch",
    "expired",
    "trial_expired",
    "state_error",
    "other",
)
_ACT_METRIC_CODE_FAMILIES = ("2xx", "4xx", "5xx", "other")
_ACT_METRIC_RESPONSE_WRITES = ("ok", "broken_pipe", "failed")


def _new_activation_metrics(*, window_started: float | None = None) -> dict:
    metrics: dict = {
        "window_started": time.monotonic() if window_started is None else float(window_started),
        "requests": 0,
        "decision_ok": 0,
        "decision_failed": 0,
        "response_write_ok": 0,
        "response_write_broken_pipe": 0,
        "response_write_failed": 0,
        "elapsed_total_ms": 0.0,
        "elapsed_max_ms": 0.0,
    }
    for family in _ACT_METRIC_CODE_FAMILIES:
        metrics[f"code_{family}"] = 0
    for mode in _ACT_METRIC_MODES:
        metrics[f"{mode}_requests"] = 0
        metrics[f"{mode}_decision_ok"] = 0
        metrics[f"{mode}_decision_failed"] = 0
        for response_write in _ACT_METRIC_RESPONSE_WRITES:
            metrics[f"{mode}_response_write_{response_write}"] = 0
        metrics[f"{mode}_elapsed_total_ms"] = 0.0
        metrics[f"{mode}_elapsed_max_ms"] = 0.0
    for reason in _ACT_METRIC_REASONS:
        metrics[f"reason_{reason}"] = 0
    return metrics


_ACT_METRICS: dict = _new_activation_metrics()
_ACT_METRICS_INTERVAL_SECONDS = 60.0
_ACT_SLOW_MS = 1500.0

# /healthz 与 /activation-readiness 原先静音默认 access log；事故恢复后无法判断请求是否
# 到达、响应是否真正写出。这里只保留固定路由的聚合计数，不记录 URL、IP、header、
# device、token 或 payload。进程未重启时，恢复后的下一次探测会把上一窗口一起刷入 journal。
_ACT_PROBE_METRICS_LOCK = threading.Lock()
_ACT_PROBE_METRICS: dict = {
    "window_started": time.monotonic(),
    "healthz_requests": 0,
    "healthz_code_2xx": 0,
    "healthz_code_5xx": 0,
    "healthz_code_other": 0,
    "healthz_response_write_ok": 0,
    "healthz_response_write_broken_pipe": 0,
    "healthz_response_write_failed": 0,
    "activation_readiness_requests": 0,
    "activation_readiness_code_2xx": 0,
    "activation_readiness_code_5xx": 0,
    "activation_readiness_code_other": 0,
    "activation_readiness_response_write_ok": 0,
    "activation_readiness_response_write_broken_pipe": 0,
    "activation_readiness_response_write_failed": 0,
}
_ACT_PROBE_METRICS_INTERVAL_SECONDS = 60.0

# /ingest rollout observability is intentionally process-local and fixed-field.
# It measures only the trusted/legacy mix and aggregate outcomes needed to decide
# whether compat can move to required.  A restart loses the unflushed window; no
# request identity, endpoint, header or error text is retained here.
_UPLOAD_METRICS_LOCK = threading.Lock()
_UPLOAD_METRIC_IDENTITIES = ("trusted", "legacy", "none")
_UPLOAD_METRIC_DISPOSITIONS = (
    "accepted",
    "quarantined",
    "duplicate",
    "rejected",
)
_UPLOAD_METRIC_CODE_FAMILIES = ("2xx", "4xx", "5xx", "other")
_UPLOAD_METRIC_RESPONSE_WRITES = ("ok", "broken_pipe", "failed")
_UPLOAD_METRIC_LATENCY_BUCKETS = (
    "lt_100ms",
    "100_to_500ms",
    "500_to_1500ms",
    "ge_1500ms",
)
_UPLOAD_METRIC_REASONS = (
    "ok",
    "rate_limited",
    "bad_content_length",
    "payload_too_large",
    "bad_auth",
    "identity_required",
    "identity_expired",
    "identity_revoked",
    "identity_device_revoked",
    "identity_state_error",
    "identity_replay",
    "identity_in_flight",
    "identity_invalid",
    "request_replayed",
    "bad_json",
    "other",
)
_UPLOAD_METRICS_INTERVAL_SECONDS = 60.0


def _upload_metric_nonnegative_float(value: object) -> float:
    """Project an exact builtin number without invoking subclass protocols."""
    if type(value) not in {int, float}:
        return 0.0
    try:
        projected = float(value)
    except (OverflowError, ValueError):
        return 0.0
    if not math.isfinite(projected) or projected < 0.0:
        return 0.0
    return projected


def _new_upload_identity_metrics(*, window_started: float | None = None) -> dict:
    metrics: dict = {
        "window_started": _upload_metric_nonnegative_float(
            time.monotonic() if window_started is None else window_started
        ),
        "requests": 0,
    }
    for identity in _UPLOAD_METRIC_IDENTITIES:
        metrics[f"identity_{identity}"] = 0
    for family in _UPLOAD_METRIC_CODE_FAMILIES:
        metrics[f"code_{family}"] = 0
    for response_write in _UPLOAD_METRIC_RESPONSE_WRITES:
        metrics[f"response_write_{response_write}"] = 0
    for bucket in _UPLOAD_METRIC_LATENCY_BUCKETS:
        metrics[f"latency_{bucket}"] = 0
    for disposition in _UPLOAD_METRIC_DISPOSITIONS:
        metrics[f"records_{disposition}"] = 0
    for reason in _UPLOAD_METRIC_REASONS:
        metrics[f"reason_{reason}"] = 0
    return metrics


_UPLOAD_METRICS: dict = _new_upload_identity_metrics()


class HeavyOperationBusy(RuntimeError):
    """另一项重型扫描正在运行；请求线程不得排队占满。"""


class InvalidDashboardWindow(ValueError):
    """私有看板服务端日期窗口不符合合同。"""


_CLIENT_DISCONNECT_WINERRORS = frozenset({64, 109, 232, 10053, 10054})
_CLIENT_DISCONNECT_ERRNOS = frozenset({
    errno.EPIPE,
    errno.ECONNABORTED,
    errno.ECONNRESET,
})


def _is_client_disconnect_error(exc: BaseException) -> bool:
    """Recognize write-side disconnects without swallowing unrelated I/O errors."""
    if isinstance(
        exc,
        (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, ssl.SSLEOFError),
    ):
        return True
    return (
        isinstance(exc, OSError)
        and (
            getattr(exc, "winerror", None) in _CLIENT_DISCONNECT_WINERRORS
            or getattr(exc, "errno", None) in _CLIENT_DISCONNECT_ERRNOS
        )
    )


def _process_memory_kib() -> tuple[int, int]:
    """返回 Linux 当前/峰值 RSS；非 Linux 或读取失败时用 -1，不记录请求内容。"""
    try:
        values = {}
        with Path("/proc/self/status").open("r", encoding="ascii", errors="replace") as stream:
            for line in stream:
                key, sep, value = line.partition(":")
                if sep and key in {"VmRSS", "VmHWM"}:
                    values[key] = int(value.strip().split()[0])
        return int(values.get("VmRSS", -1)), int(values.get("VmHWM", -1))
    except (OSError, ValueError, IndexError):
        return -1, -1


@contextmanager
def _serialized_heavy_operation(name: str):
    """非阻塞串行全库任务；忙时立即 503/旧缓存，避免请求线程排队。"""
    if not _HEAVY_SCAN_LOCK.acquire(blocking=False):
        raise HeavyOperationBusy(name)
    started = time.monotonic()
    try:
        if _SAMPLE_HISTORY_INDEX is not None:
            with _LOCK:
                _refresh_sample_history_index()
                try:
                    source_stat = _OUT.stat()
                    source_state = (source_stat.st_mtime_ns, source_stat.st_size)
                except OSError:
                    source_state = (0, 0)
        else:
            try:
                source_stat = _OUT.stat()
                source_state = (source_stat.st_mtime_ns, source_stat.st_size)
            except OSError:
                source_state = (0, 0)
        source_bytes = source_state[1]
        rss_kib, hwm_kib = _process_memory_kib()
        print(f"[collector-heavy] start op={name} source_bytes={source_bytes} "
              f"rss_kib={rss_kib} hwm_kib={hwm_kib}", flush=True)
        yield source_state
    finally:
        rss_kib, hwm_kib = _process_memory_kib()
        print(
            f"[collector-heavy] end op={name} elapsed_ms="
            f"{(time.monotonic() - started) * 1000.0:.1f} "
            f"rss_kib={rss_kib} hwm_kib={hwm_kib}",
            flush=True,
        )
        _HEAVY_SCAN_LOCK.release()


def _today_shanghai(*, now: float | None = None) -> date:
    return datetime.fromtimestamp(time.time() if now is None else now, tz=_SHANGHAI_TZ).date()


def _recent_window(days: int, *, now: float | None = None) -> dict:
    end = _today_shanghai(now=now)
    start = end - timedelta(days=int(days) - 1)
    return {"mode": f"recent-{days}", "start": start, "end": end,
            "label": f"最近 {days} 天", "schema": _CACHE_SCHEMA}


def _window_token(window: dict | None) -> str:
    if window is None:
        return f"{_CACHE_SCHEMA}:all"
    return f"{_CACHE_SCHEMA}:{window['mode']}:{window['start'].isoformat()}:{window['end'].isoformat()}"


def _cache_matches_window(cache: dict, window: dict | None) -> bool:
    token = cache.get("window_token")
    return token == _window_token(window) or (token is None and window is None)


def _window_payload(window: dict | None) -> dict:
    if window is None:
        return {"mode": "all", "label": "全部历史聚合", "start": None, "end": None,
                "timezone": "Asia/Shanghai", "schema": _CACHE_SCHEMA}
    return {"mode": window["mode"], "label": window["label"],
            "start": window["start"].isoformat(), "end": window["end"].isoformat(),
            "timezone": "Asia/Shanghai", "schema": _CACHE_SCHEMA}


def _parse_dashboard_window(raw_path: str, *, allow_kind: bool = False) -> dict:
    """严格解析预设或自定义日期；北京时间、两端包含，拒绝未知/重复/混合参数。"""
    query = parse_qs(urlsplit(raw_path).query, keep_blank_values=True)
    allowed = {"window", "start", "end"} | ({"kind"} if allow_kind else set())
    if set(query) - allowed or any(len(values) != 1 for values in query.values()):
        raise InvalidDashboardWindow("invalid query")
    date_keys = set(query) - ({"kind"} if allow_kind else set())
    if not date_keys:
        return _recent_window(DASH_DEFAULT_DAYS)
    if date_keys == {"window"}:
        raw_days = query["window"][0]
        if not raw_days.isascii() or not raw_days.isdigit() or int(raw_days) not in DASH_PRESET_DAYS:
            raise InvalidDashboardWindow("invalid preset")
        return _recent_window(int(raw_days))
    if date_keys != {"start", "end"}:
        raise InvalidDashboardWindow("start and end are required together")
    if not all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", query[key][0]) for key in ("start", "end")):
        raise InvalidDashboardWindow("invalid ISO date")
    try:
        start = date.fromisoformat(query["start"][0])
        end = date.fromisoformat(query["end"][0])
    except ValueError as exc:
        raise InvalidDashboardWindow("invalid ISO date") from exc
    if start > end or (end - start).days + 1 > DASH_MAX_WINDOW_DAYS:
        raise InvalidDashboardWindow("invalid date range")
    return {"mode": "custom", "start": start, "end": end,
            "label": "自定义范围", "schema": _CACHE_SCHEMA}


def _dashboard_detail_urls(window: dict | None) -> dict[str, str]:
    params = {} if window is None else {
        "start": window["start"].isoformat(), "end": window["end"].isoformat(),
    }
    return {kind: "/dashboard-data?" + urlencode({"kind": kind, **params})
            for kind in ("rare", "tools", "player_ranks")}


def _activation_metric_projection(
    response,
    *,
    code: int,
    mode_hint: str = "none",
    reason_hint: str = "other",
) -> tuple[str, str]:
    """Project an exact response dict without invoking untrusted key protocols."""
    code = code if type(code) is int and code >= 0 else 0
    if type(response) is not dict:
        return "none", "other"
    # Exact dict iteration itself does not hash or compare stored keys. Check every
    # raw key before the first contains/get; a str subclass can otherwise run its
    # attacker-controlled __eq__ after the HTTP response has already been written.
    if any(type(raw_key) is not str for raw_key in response):
        return "none", "other"

    response_has_mode = "mode" in response
    response_mode = response.get("mode") if response_has_mode else None
    if response_has_mode:
        mode = (
            response_mode
            if type(response_mode) is str and response_mode in _ACT_METRIC_MODES
            else "none"
        )
    elif type(mode_hint) is str and mode_hint in _ACT_METRIC_MODES:
        mode = mode_hint
    else:
        mode = "none"

    response_has_reason = "reason" in response
    response_reason = response.get("reason") if response_has_reason else None
    if response_has_reason:
        reason = (
            response_reason
            if type(response_reason) is str and response_reason in _ACT_METRIC_REASONS
            else "other"
        )
    elif code == 200 and response.get("ok") is True:
        reason = "ok"
    elif type(reason_hint) is str and reason_hint in _ACT_METRIC_REASONS:
        reason = reason_hint
    else:
        reason = "other"
    return mode, reason


def _record_activation_metric(
    code: int,
    response: dict,
    elapsed_ms: float,
    *,
    response_write: str = "ok",
    mode_hint: str = "none",
    reason_hint: str = "other",
    _projection: tuple[str, str] | None = None,
    now: float | None = None,
) -> None:
    """写固定分类的告警和一分钟窗口摘要；调用方不传任何用户标识。"""
    now = time.monotonic() if type(now) not in {int, float} else max(0.0, float(now))
    code = code if type(code) is int and code >= 0 else 0
    elapsed_ms = (
        max(0.0, float(elapsed_ms))
        if type(elapsed_ms) in {int, float} and math.isfinite(float(elapsed_ms))
        else 0.0
    )
    response_write = (
        response_write
        if type(response_write) is str and response_write in _ACT_METRIC_RESPONSE_WRITES
        else "failed"
    )
    if (
        type(_projection) is tuple
        and len(_projection) == 2
        and type(_projection[0]) is str
        and _projection[0] in _ACT_METRIC_MODES
        and type(_projection[1]) is str
        and _projection[1] in _ACT_METRIC_REASONS
    ):
        mode, reason = _projection
    else:
        mode, reason = _activation_metric_projection(
            response,
            code=code,
            mode_hint=mode_hint,
            reason_hint=reason_hint,
        )
    if 200 <= code < 300:
        code_family = "2xx"
    elif 400 <= code < 500:
        code_family = "4xx"
    elif 500 <= code < 600:
        code_family = "5xx"
    else:
        code_family = "other"
    summary = None
    with _ACT_METRICS_LOCK:
        metrics = _ACT_METRICS
        # Older focused tests and safe runtime probes replace only the historical
        # fields. Backfill the fixed schema under the same lock without losing their
        # explicit window boundary.
        if "reason_other" not in metrics:
            defaults = _new_activation_metrics(window_started=now)
            for key, value in defaults.items():
                metrics.setdefault(key, value)
        metrics["requests"] += 1
        decision = "decision_ok" if code == 200 else "decision_failed"
        metrics[decision] += 1
        metrics[f"response_write_{response_write}"] += 1
        metrics["elapsed_total_ms"] += elapsed_ms
        metrics["elapsed_max_ms"] = max(float(metrics["elapsed_max_ms"]), elapsed_ms)
        metrics[f"code_{code_family}"] += 1
        metrics[f"{mode}_requests"] += 1
        metrics[f"{mode}_{decision}"] += 1
        metrics[f"{mode}_response_write_{response_write}"] += 1
        metrics[f"{mode}_elapsed_total_ms"] += elapsed_ms
        metrics[f"{mode}_elapsed_max_ms"] = max(
            float(metrics[f"{mode}_elapsed_max_ms"]), elapsed_ms
        )
        metrics[f"reason_{reason}"] += 1
        window_seconds = max(0.0, now - float(metrics["window_started"]))
        if window_seconds >= _ACT_METRICS_INTERVAL_SECONDS:
            requests = int(metrics["requests"])
            summary = {
                "window_seconds": window_seconds,
                "requests": requests,
                "decision_ok": int(metrics["decision_ok"]),
                "decision_failed": int(metrics["decision_failed"]),
                "response_write_ok": int(metrics["response_write_ok"]),
                "response_write_broken_pipe": int(metrics["response_write_broken_pipe"]),
                "response_write_failed": int(metrics["response_write_failed"]),
                "mean_ms": float(metrics["elapsed_total_ms"]) / max(1, requests),
                "max_ms": float(metrics["elapsed_max_ms"]),
            }
            for family in _ACT_METRIC_CODE_FAMILIES:
                summary[f"code_{family}"] = int(metrics[f"code_{family}"])
            for fixed_mode in _ACT_METRIC_MODES:
                mode_requests = int(metrics[f"{fixed_mode}_requests"])
                summary[f"{fixed_mode}_requests"] = mode_requests
                summary[f"{fixed_mode}_decision_ok"] = int(
                    metrics[f"{fixed_mode}_decision_ok"]
                )
                summary[f"{fixed_mode}_decision_failed"] = int(
                    metrics[f"{fixed_mode}_decision_failed"]
                )
                for fixed_write in _ACT_METRIC_RESPONSE_WRITES:
                    summary[f"{fixed_mode}_response_write_{fixed_write}"] = int(
                        metrics[f"{fixed_mode}_response_write_{fixed_write}"]
                    )
                summary[f"{fixed_mode}_mean_ms"] = (
                    float(metrics[f"{fixed_mode}_elapsed_total_ms"])
                    / max(1, mode_requests)
                )
                summary[f"{fixed_mode}_max_ms"] = float(
                    metrics[f"{fixed_mode}_elapsed_max_ms"]
                )
            for fixed_reason in _ACT_METRIC_REASONS:
                summary[f"reason_{fixed_reason}"] = int(
                    metrics[f"reason_{fixed_reason}"]
                )
            metrics.clear()
            metrics.update(_new_activation_metrics(window_started=now))
    if code != 200 or response_write != "ok" or elapsed_ms >= _ACT_SLOW_MS:
        print(
            f"[activation-alert] code={code} mode={mode} reason={reason} "
            f"elapsed_ms={elapsed_ms:.1f} code_family={code_family} "
            f"response_write={response_write}",
            flush=True,
        )
    if summary is not None:
        fields = [
            f"window_seconds={summary['window_seconds']:.1f}",
            f"requests={summary['requests']}",
            f"decision_ok={summary['decision_ok']}",
            f"decision_failed={summary['decision_failed']}",
            f"response_write_ok={summary['response_write_ok']}",
            f"response_write_broken_pipe={summary['response_write_broken_pipe']}",
            f"response_write_failed={summary['response_write_failed']}",
            f"mean_ms={summary['mean_ms']:.1f}",
            f"max_ms={summary['max_ms']:.1f}",
        ]
        fields.extend(
            f"code_{family}={summary[f'code_{family}']}"
            for family in _ACT_METRIC_CODE_FAMILIES
        )
        for fixed_mode in _ACT_METRIC_MODES:
            fields.extend([
                f"{fixed_mode}_requests={summary[f'{fixed_mode}_requests']}",
                f"{fixed_mode}_decision_ok={summary[f'{fixed_mode}_decision_ok']}",
                f"{fixed_mode}_decision_failed={summary[f'{fixed_mode}_decision_failed']}",
                f"{fixed_mode}_response_write_ok="
                f"{summary[f'{fixed_mode}_response_write_ok']}",
                f"{fixed_mode}_response_write_broken_pipe="
                f"{summary[f'{fixed_mode}_response_write_broken_pipe']}",
                f"{fixed_mode}_response_write_failed="
                f"{summary[f'{fixed_mode}_response_write_failed']}",
                f"{fixed_mode}_mean_ms={summary[f'{fixed_mode}_mean_ms']:.1f}",
                f"{fixed_mode}_max_ms={summary[f'{fixed_mode}_max_ms']:.1f}",
            ])
        fields.extend(
            f"reason_{fixed_reason}={summary[f'reason_{fixed_reason}']}"
            for fixed_reason in _ACT_METRIC_REASONS
        )
        print("[activation-summary] " + " ".join(fields), flush=True)


def _record_activation_probe_metric(
    route: str,
    code: int,
    elapsed_ms: float,
    *,
    response_write: str = "ok",
    now: float | None = None,
) -> None:
    """Record fixed-field activation probe observability without request identity."""
    route = route if route in {"healthz", "activation_readiness"} else "unknown"
    code = int(code)
    elapsed_ms = max(0.0, float(elapsed_ms))
    if response_write not in {"ok", "broken_pipe", "failed"}:
        response_write = "failed"
    if 200 <= code < 300:
        code_family = "2xx"
    elif 500 <= code < 600:
        code_family = "5xx"
    else:
        code_family = "other"
    now = time.monotonic() if now is None else float(now)
    summary = None
    if route != "unknown":
        with _ACT_PROBE_METRICS_LOCK:
            metrics = _ACT_PROBE_METRICS
            metrics[f"{route}_requests"] += 1
            metrics[f"{route}_code_{code_family}"] += 1
            metrics[f"{route}_response_write_{response_write}"] += 1
            window_seconds = max(0.0, now - float(metrics["window_started"]))
            if window_seconds >= _ACT_PROBE_METRICS_INTERVAL_SECONDS:
                summary = {key: value for key, value in metrics.items()}
                summary["window_seconds"] = window_seconds
                for key in tuple(metrics):
                    metrics[key] = now if key == "window_started" else 0
    if route == "unknown":
        print(
            "[activation-probe-alert] route=unknown code_family=other "
            "response_write=failed elapsed_ms=0.0",
            flush=True,
        )
        return
    if code_family != "2xx" or response_write != "ok" or elapsed_ms >= _ACT_SLOW_MS:
        print(
            f"[activation-probe-alert] route={route} code={code} "
            f"response_write={response_write} elapsed_ms={elapsed_ms:.1f}",
            flush=True,
        )
    if summary is not None:
        print(
            "[activation-probe-summary] "
            f"window_seconds={summary['window_seconds']:.1f} "
            f"healthz_requests={summary['healthz_requests']} "
            f"healthz_code_2xx={summary['healthz_code_2xx']} "
            f"healthz_code_5xx={summary['healthz_code_5xx']} "
            f"healthz_code_other={summary['healthz_code_other']} "
            f"healthz_response_write_ok={summary['healthz_response_write_ok']} "
            "healthz_response_write_broken_pipe="
            f"{summary['healthz_response_write_broken_pipe']} "
            f"healthz_response_write_failed={summary['healthz_response_write_failed']} "
            "activation_readiness_requests="
            f"{summary['activation_readiness_requests']} "
            "activation_readiness_code_2xx="
            f"{summary['activation_readiness_code_2xx']} "
            "activation_readiness_code_5xx="
            f"{summary['activation_readiness_code_5xx']} "
            "activation_readiness_code_other="
            f"{summary['activation_readiness_code_other']} "
            "activation_readiness_response_write_ok="
            f"{summary['activation_readiness_response_write_ok']} "
            "activation_readiness_response_write_broken_pipe="
            f"{summary['activation_readiness_response_write_broken_pipe']} "
            "activation_readiness_response_write_failed="
            f"{summary['activation_readiness_response_write_failed']}",
            flush=True,
        )


def _upload_metric_identity(identity: object) -> str:
    if type(identity) is not str:  # noqa: E721 - observability trust boundary
        return "none"
    if identity in _UPLOAD_METRIC_IDENTITIES:
        return identity
    return "none"


def _upload_metric_reason(reason: object) -> str:
    if type(reason) is not str:  # noqa: E721 - observability trust boundary
        return "other"
    if reason in _UPLOAD_METRIC_REASONS:
        return reason
    return "other"


def _upload_metric_record_counts(records: object) -> dict[str, int] | None:
    if type(records) is not dict:  # noqa: E721 - reject mapping subclasses
        return None
    if any(type(key) is not str for key in records):
        return None
    if set(records) != set(_UPLOAD_METRIC_DISPOSITIONS):
        return None
    projected: dict[str, int] = {}
    for disposition in _UPLOAD_METRIC_DISPOSITIONS:
        value = records.get(disposition)
        if type(value) is not int or value < 0:  # noqa: E721 - bool is invalid
            return None
        projected[disposition] = value
    return projected


def _record_upload_identity_metric(
    code: int,
    elapsed_ms: float,
    *,
    identity: str = "none",
    reason: str = "other",
    response_write: str = "ok",
    records: dict | None = None,
    now: float | None = None,
) -> None:
    """Record one privacy-safe /ingest outcome into a fixed 60-second window."""
    code = code if type(code) is int and code >= 0 else 0
    elapsed_ms = _upload_metric_nonnegative_float(elapsed_ms)
    identity = _upload_metric_identity(identity)
    reason = _upload_metric_reason(reason)
    response_write = (
        response_write
        if type(response_write) is str
        and response_write in _UPLOAD_METRIC_RESPONSE_WRITES
        else "failed"
    )
    record_counts = _upload_metric_record_counts(records)
    if records is not None and record_counts is None:
        identity = "none"
        reason = "other"
    if 200 <= code < 300:
        code_family = "2xx"
    elif 400 <= code < 500:
        code_family = "4xx"
    elif 500 <= code < 600:
        code_family = "5xx"
    else:
        code_family = "other"
    if elapsed_ms < 100.0:
        latency_bucket = "lt_100ms"
    elif elapsed_ms < 500.0:
        latency_bucket = "100_to_500ms"
    elif elapsed_ms < 1500.0:
        latency_bucket = "500_to_1500ms"
    else:
        latency_bucket = "ge_1500ms"
    now = _upload_metric_nonnegative_float(
        time.monotonic() if now is None else now
    )
    summary = None
    with _UPLOAD_METRICS_LOCK:
        metrics = _UPLOAD_METRICS
        metrics["requests"] += 1
        metrics[f"identity_{identity}"] += 1
        metrics[f"code_{code_family}"] += 1
        metrics[f"response_write_{response_write}"] += 1
        metrics[f"latency_{latency_bucket}"] += 1
        metrics[f"reason_{reason}"] += 1
        if record_counts is not None:
            for disposition, count in record_counts.items():
                metrics[f"records_{disposition}"] += count
        window_seconds = max(
            0.0,
            now - _upload_metric_nonnegative_float(metrics["window_started"]),
        )
        if window_seconds >= _UPLOAD_METRICS_INTERVAL_SECONDS:
            summary = {key: value for key, value in metrics.items()}
            summary["window_seconds"] = window_seconds
            metrics.clear()
            metrics.update(_new_upload_identity_metrics(window_started=now))
    if summary is None:
        return
    fields = [
        f"window_seconds={summary['window_seconds']:.1f}",
        f"requests={summary['requests']}",
    ]
    fields.extend(
        f"identity_{fixed}={summary[f'identity_{fixed}']}"
        for fixed in _UPLOAD_METRIC_IDENTITIES
    )
    fields.extend(
        f"code_{fixed}={summary[f'code_{fixed}']}"
        for fixed in _UPLOAD_METRIC_CODE_FAMILIES
    )
    fields.extend(
        f"response_write_{fixed}={summary[f'response_write_{fixed}']}"
        for fixed in _UPLOAD_METRIC_RESPONSE_WRITES
    )
    fields.extend(
        f"latency_{fixed}={summary[f'latency_{fixed}']}"
        for fixed in _UPLOAD_METRIC_LATENCY_BUCKETS
    )
    fields.extend(
        f"records_{fixed}={summary[f'records_{fixed}']}"
        for fixed in _UPLOAD_METRIC_DISPOSITIONS
    )
    fields.extend(
        f"reason_{fixed}={summary[f'reason_{fixed}']}"
        for fixed in _UPLOAD_METRIC_REASONS
    )
    line = "[upload-identity-summary] " + " ".join(fields)
    try:
        print(line, flush=True)
    except Exception:  # noqa: BLE001 - non-business best-effort emit boundary
        # Observability must never change an already-completed HTTP response.
        # The swapped window remains consumed and no exception text is emitted.
        return


def _dashboard_bodies(window: dict | None = None) -> tuple[bytes, bytes, str | None]:
    """Return (plain_utf8_html, gzipped_html, etag) for /dashboard, rebuilt ONLY when samples.jsonl
    changed. The heavy load+render happens at most once per data change; concurrent opens share one
    rebuild. etag 跟数据键(mtime,size)走: 浏览器刷新时数据没变 → If-None-Match 命中 → 304 零传输。"""
    import sample_dashboard  # 同目录
    quarantine_state = _quarantine_file_state()
    if not _OUT.exists():
        b = sample_dashboard.build_dashboard_html(
            [], detail_urls=_dashboard_detail_urls(window),
            server_window=_window_payload(window),
            quarantine_summary=_quarantine_summary(),
        ).encode("utf-8")
        return b, gzip.compress(b, 6), None
    st = _OUT.stat()
    key = (st.st_mtime_ns, st.st_size, quarantine_state, _window_token(window))
    has_cache = _DASH_CACHE.get("plain") is not None
    if has_cache and _DASH_CACHE.get("key") == key:
        return _DASH_CACHE["plain"], _DASH_CACHE["gz"], _DASH_CACHE["etag"]
    # 不同日期窗口绝不能拿到旧窗口缓存；同窗口数据刚变化时才允许短暂复用。
    if (has_cache and _cache_matches_window(_DASH_CACHE, window)
            and time.monotonic() - float(_DASH_CACHE.get("built_mono") or 0.0) < DASH_REBUILD_INTERVAL):
        return _DASH_CACHE["plain"], _DASH_CACHE["gz"], _DASH_CACHE["etag"]
    with _DASH_LOCK:
        if _DASH_CACHE.get("plain") is not None and _DASH_CACHE.get("key") == key:
            return _DASH_CACHE["plain"], _DASH_CACHE["gz"], _DASH_CACHE["etag"]
        if (_DASH_CACHE.get("plain") is not None
                and _cache_matches_window(_DASH_CACHE, window) and
                time.monotonic() - float(_DASH_CACHE.get("built_mono") or 0.0) < DASH_REBUILD_INTERVAL):
            return _DASH_CACHE["plain"], _DASH_CACHE["gz"], _DASH_CACHE["etag"]
    try:
        with _serialized_heavy_operation("dashboard") as snapshot_state:
            snapshot_mtime_ns, snapshot_bytes = snapshot_state
            build_key = (
                snapshot_mtime_ns,
                snapshot_bytes,
                quarantine_state,
                _window_token(window),
            )
            loader_kwargs = {"snapshot_bytes": snapshot_bytes, "history_index": _SAMPLE_HISTORY_INDEX} if _SAMPLE_HISTORY_INDEX is not None else {"snapshot_bytes": snapshot_bytes}
            if window is not None:
                loader_kwargs.update(start=window["start"], end=window["end"])
            samples = sample_dashboard._load_samples(_OUT, **loader_kwargs)
            rendered = sample_dashboard.build_dashboard_html(
                samples, detail_urls=_dashboard_detail_urls(window),
                server_window=_window_payload(window),
                quarantine_summary=_quarantine_summary(),
            )
            _log_sample_payload_read("dashboard", samples)
            plain = rendered.encode("utf-8")
            gz = gzip.compress(plain, 6)
            etag = f'"d-{hashlib.sha256(str(build_key).encode()).hexdigest()[:16]}-{len(plain):x}"'
            key = build_key
    except HeavyOperationBusy:
        with _DASH_LOCK:
            if (_DASH_CACHE.get("plain") is not None
                    and _cache_matches_window(_DASH_CACHE, window)):
                return _DASH_CACHE["plain"], _DASH_CACHE["gz"], _DASH_CACHE["etag"]
        raise
    with _DASH_LOCK:
        _DASH_CACHE.update({"key": key, "window_token": _window_token(window),
                            "plain": plain, "gz": gz, "etag": etag,
                            "built_mono": time.monotonic()})
    return plain, gz, etag


def _dashboard_detail_bodies(kind: str, window: dict | None = None) -> tuple[bytes, bytes, str | None]:
    """按需返回私有看板重字段；每个模块独立缓存并复用 5 分钟节流。"""
    import sample_dashboard  # 同目录
    if kind not in {"rare", "tools", "player_ranks"}:
        raise ValueError(f"unsupported dashboard detail kind: {kind}")
    if not _OUT.exists():
        plain = json.dumps({"kind": kind, "items": []}, separators=(",", ":")).encode("utf-8")
        return plain, gzip.compress(plain, 6), None
    st = _OUT.stat()
    key = (st.st_mtime_ns, st.st_size, _window_token(window))
    cache_id = f"{kind}:{_window_token(window)}"
    cached = _DASH_DETAIL_CACHE.get(cache_id)
    if cached is not None:
        if cached.get("key") == key:
            return cached["plain"], cached["gz"], cached["etag"]
        if time.monotonic() - float(cached.get("built_mono") or 0.0) < DASH_REBUILD_INTERVAL:
            return cached["plain"], cached["gz"], cached["etag"]
    with _DASH_DETAIL_LOCK:
        cached = _DASH_DETAIL_CACHE.get(cache_id)
        if cached is not None:
            if cached.get("key") == key:
                return cached["plain"], cached["gz"], cached["etag"]
            if time.monotonic() - float(cached.get("built_mono") or 0.0) < DASH_REBUILD_INTERVAL:
                return cached["plain"], cached["gz"], cached["etag"]
    try:
        with _serialized_heavy_operation(f"dashboard_detail_{kind}") as snapshot_state:
            snapshot_mtime_ns, snapshot_bytes = snapshot_state
            build_key = (snapshot_mtime_ns, snapshot_bytes, _window_token(window))
            loader_kwargs = {"snapshot_bytes": snapshot_bytes, "history_index": _SAMPLE_HISTORY_INDEX} if _SAMPLE_HISTORY_INDEX is not None else {"snapshot_bytes": snapshot_bytes}
            if window is not None:
                loader_kwargs.update(start=window["start"], end=window["end"])
            samples = sample_dashboard._load_samples(_OUT, **loader_kwargs)
            payload = sample_dashboard.dashboard_detail_payload(samples, kind)
            _log_sample_payload_read("dashboard_detail", samples)
            rendered = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            plain = rendered.encode("utf-8")
            gz = gzip.compress(plain, 6)
            etag = f'"dd-{hashlib.sha256(str((kind, build_key)).encode()).hexdigest()[:16]}-{len(plain):x}"'
            key = build_key
    except HeavyOperationBusy:
        with _DASH_DETAIL_LOCK:
            cached = _DASH_DETAIL_CACHE.get(cache_id)
            if cached is not None:
                return cached["plain"], cached["gz"], cached["etag"]
        raise
    cached = {"key": key, "plain": plain, "gz": gz, "etag": etag,
              "built_mono": time.monotonic()}
    with _DASH_DETAIL_LOCK:
        _DASH_DETAIL_CACHE[cache_id] = cached
        total = sum(len(v["plain"]) + len(v["gz"]) for v in _DASH_DETAIL_CACHE.values())
        while _DASH_DETAIL_CACHE and total > _DASH_DETAIL_CACHE_MAX_BYTES:
            oldest = next(iter(_DASH_DETAIL_CACHE))
            evicted = _DASH_DETAIL_CACHE.pop(oldest)
            total -= len(evicted["plain"]) + len(evicted["gz"])
    return plain, gz, etag

def _public_cached() -> tuple[bytes, bytes, str | None]:
    return _PUB_CACHE["plain"], _PUB_CACHE["gz"], _PUB_CACHE["etag"]


def _public_bodies(window: dict | None = None) -> tuple[bytes, bytes, str | None]:
    """/public 的 (plain, gzip, etag)。

    重建条件 = 【数据变了】且【距上次重建 >= PUBLIC_REBUILD_INTERVAL】。
    也就是说: 数据没变永不重建; 数据变了也最多每 30 分钟重建一次(见上方节流说明)。"""
    import public_stats  # 同目录
    if not _OUT.exists():
        aggregate = public_stats.aggregate_public([])
        aggregate["window"] = {**_window_payload(window), "parsed_rows": 0,
                               "matched_rows": 0, "unknown_date_count": 0}
        b = public_stats.build_public_html(
            aggregate, generated_at=time.time(),
            rebuild_interval=PUBLIC_REBUILD_INTERVAL).encode("utf-8")
        return b, gzip.compress(b, 6), None

    st = _OUT.stat()
    key = (st.st_mtime_ns, st.st_size, _window_token(window))
    has_cache = _PUB_CACHE["plain"] is not None
    if has_cache:
        if _PUB_CACHE["key"] == key:
            return _public_cached()                      # 数据没变 -> 永远吃缓存
        if (_cache_matches_window(_PUB_CACHE, window)
                and time.monotonic() - _PUB_CACHE["built_mono"] < PUBLIC_REBUILD_INTERVAL):
            return _public_cached()                      # 数据变了但没到重建间隔 -> 仍吃缓存(节流)

    with _PUB_LOCK:
        # 锁内复查(防惊群: 30 分钟到点时一堆并发请求同时重建)
        if _PUB_CACHE["plain"] is not None:
            if _PUB_CACHE["key"] == key:
                return _public_cached()
            if (_cache_matches_window(_PUB_CACHE, window)
                    and time.monotonic() - _PUB_CACHE["built_mono"] < PUBLIC_REBUILD_INTERVAL):
                return _public_cached()
    try:
        with _serialized_heavy_operation("public") as snapshot_state:
            snapshot_mtime_ns, snapshot_bytes = snapshot_state
            build_key = (snapshot_mtime_ns, snapshot_bytes, _window_token(window))
            wall = time.time()
            loader_kwargs = {"snapshot_bytes": snapshot_bytes, "history_index": _SAMPLE_HISTORY_INDEX} if _SAMPLE_HISTORY_INDEX is not None else {"snapshot_bytes": snapshot_bytes}
            if window is not None:
                loader_kwargs.update(start=window["start"], end=window["end"])
            samples = public_stats.load_samples(_OUT, **loader_kwargs)
            aggregate = public_stats.aggregate_public(samples)
            _log_sample_payload_read("public", samples)
            aggregate["window"] = {
                **_window_payload(window),
                "parsed_rows": int(getattr(samples, "last_parsed_count", len(samples) if hasattr(samples, "__len__") else 0)),
                "matched_rows": int(getattr(samples, "last_matched_count", len(samples) if hasattr(samples, "__len__") else 0)),
                "unknown_date_count": int(getattr(samples, "last_unknown_date_count", 0)),
            }
            rendered = public_stats.build_public_html(
                aggregate, generated_at=wall, rebuild_interval=PUBLIC_REBUILD_INTERVAL)
            plain = rendered.encode("utf-8")
            gz = gzip.compress(plain, 6)
            etag = f'"p-{hashlib.sha256(str(build_key).encode()).hexdigest()[:16]}-{len(plain):x}"'
            key = build_key
    except HeavyOperationBusy:
        if _PUB_CACHE["plain"] is not None and _cache_matches_window(_PUB_CACHE, window):
            return _public_cached()
        raise
    with _PUB_LOCK:
        _PUB_CACHE.update({"key": key, "window_token": _window_token(window),
                           "plain": plain, "gz": gz, "etag": etag,
                           "built_mono": time.monotonic(), "built_wall": wall})
    return plain, gz, etag


# --------------------------------------------------------------------------- #
# /my 个人对局记录(0.2.7 · uid-scope)—— 登录态
# --------------------------------------------------------------------------- #
# /my 会流式扫描整个 samples.jsonl，当前生产库已超过 200 MiB。缓存按 uid 有界，
# 数据没变时直接复用；数据持续上传时最多允许页面旧 5s，换取同一用户快速刷新不重复扫库。
# 全局 build 锁还会防止多个登录用户同时扫库把磁盘和激活线程拖住。
MY_PAGE_STALE_SECONDS = 5.0
_MY_PAGE_CACHE_MAX_ENTRIES = 32
_MY_PAGE_CACHE_MAX_CHARS = 8 << 20
_MY_PAGE_CACHE: OrderedDict[str, dict] = OrderedDict()
_MY_PAGE_LOCK = threading.Lock()

# 登录后的 /my 是唯一会每个用户触发全库扫描的公网路由；通用 IP 限流之外再按签名
# uid_hash 限制刷新频率。正常手动刷新远低于 12/min，且 NAT 下不同用户不会互相抢配额。
_MY_VIEW_RATE_WINDOW = 60.0
_MY_VIEW_RATE_MAX = 12
_MY_VIEW_RATE: dict[str, list[float]] = {}
_MY_VIEW_RATE_LOCK = threading.Lock()

# 会话 cookie 的设计(安全要点, 别简化):
#  · token 只在【POST 表单】里出现一次, 绝不进 URL —— URL 会落进浏览器历史、Referer、caddy 访问日志,
#    而 token 是能激活软件的凭证, 泄露 = 被人白嫖。
#  · cookie 里【只放 uid_hash, 不放 token】。就算 cookie 被偷, 拿到的也只是"能看某人对局记录",
#    而不是"能激活软件"。
#  · cookie 用 activation_key 做 HMAC 签名 -> 用户改不了 uid_hash 去看别人的数据。
#  · HttpOnly(JS 读不到) + SameSite=Lax(防 CSRF) + 有效期 7 天。HTTPS 下加 Secure。
_MY_COOKIE = "bk_my"
_MY_TTL = 7 * 24 * 3600
_DASH_COOKIE = "bk_dash"
_DASH_TTL = 7 * 24 * 3600


def _my_source_key() -> tuple[int, int] | None:
    try:
        st = _OUT.stat()
        return st.st_mtime_ns, st.st_size
    except OSError:
        return None


def _my_page_html(
    uid_hash: str, *, now: float | None = None, window: dict | None = None,
) -> str:
    """返回个人记录页；同数据键命中，持续 ingest 时最多允许短暂旧数据。"""
    import my_records

    now = time.monotonic() if now is None else float(now)
    source_key = _my_source_key()
    key = None if source_key is None else (*source_key, _window_token(window))
    with _MY_PAGE_LOCK:
        cached = _MY_PAGE_CACHE.get(uid_hash)
        if cached is not None and (
            cached["key"] == key or now - float(cached["built_mono"]) < MY_PAGE_STALE_SECONDS
        ):
            _MY_PAGE_CACHE.move_to_end(uid_hash)
            return str(cached["html"])
    try:
        with _serialized_heavy_operation("my") as snapshot_state:
            index_kwargs = ({"history_index": _SAMPLE_HISTORY_INDEX, "snapshot_bytes": snapshot_state[1]}
                            if _SAMPLE_HISTORY_INDEX is not None else {})
            samples = (my_records.load_user_samples(
                           _OUT, uid_hash, identity_store=_UPLOAD_IDENTITY_STORE,
                           **index_kwargs,
                       ) if window is None else
                       my_records.load_user_samples(
                           _OUT, uid_hash, start=window["start"], end=window["end"],
                           identity_store=_UPLOAD_IDENTITY_STORE,
                           **index_kwargs,
                       ))
            _log_sample_payload_read("my", samples)
            window_meta = None if window is None else {
                **_window_payload(window),
                "unknown_date_count": int(getattr(samples, "unknown_date_count", 0)),
                "parsed_rows": int(getattr(samples, "parsed_count", 0)),
            }
            summary = my_records.summarize_user(samples)
            html = (my_records.build_my_html(summary, samples) if window_meta is None else
                    my_records.build_my_html(summary, samples, server_window=window_meta))
    except HeavyOperationBusy:
        # 仅允许返回同一 uid 的旧页；没有可用旧缓存时由 Handler 立即回 503。
        with _MY_PAGE_LOCK:
            cached = _MY_PAGE_CACHE.get(uid_hash)
            if cached is not None:
                _MY_PAGE_CACHE.move_to_end(uid_hash)
                return str(cached["html"])
        raise
    with _MY_PAGE_LOCK:
        _MY_PAGE_CACHE[uid_hash] = {"key": key, "built_mono": now, "html": html}
        _MY_PAGE_CACHE.move_to_end(uid_hash)

        total_chars = sum(len(str(entry["html"])) for entry in _MY_PAGE_CACHE.values())
        while _MY_PAGE_CACHE and (
            len(_MY_PAGE_CACHE) > _MY_PAGE_CACHE_MAX_ENTRIES
            or total_chars > _MY_PAGE_CACHE_MAX_CHARS
        ):
            _, evicted = _MY_PAGE_CACHE.popitem(last=False)
            total_chars -= len(str(evicted["html"]))
        return html


def _my_view_rate_ok(uid_hash: str, *, now: float | None = None) -> bool:
    """按已验签 uid 限制重型 /my 刷新；每次顺手回收过期桶，避免长期增长。"""
    now = time.time() if now is None else float(now)
    cut = now - _MY_VIEW_RATE_WINDOW
    with _MY_VIEW_RATE_LOCK:
        for stale_uid in [
            key for key, values in _MY_VIEW_RATE.items() if not values or values[-1] < cut
        ]:
            del _MY_VIEW_RATE[stale_uid]
        values = _MY_VIEW_RATE.setdefault(uid_hash, [])
        while values and values[0] < cut:
            values.pop(0)
        if len(values) >= _MY_VIEW_RATE_MAX:
            return False
        values.append(now)
        return True


def _my_sign(payload: str) -> str:
    """HMAC(activation_key, 稳定历史 my365 域 + payload)；保留字节以兼容既有会话。"""
    if _ACT is None:
        return ""
    return hmac.new(_ACT.secret, f"my365|{payload}".encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def _my_make_cookie(
    uid_hash: str,
    membership_exp: int | None = None,
    *,
    now: int | None = None,
) -> str:
    server_now = int(time.time()) if now is None else int(now)
    exp = server_now + _MY_TTL
    if membership_exp is not None:
        exp = min(exp, int(membership_exp))
    payload = f"{uid_hash}|{exp}"
    return f"{payload}|{_my_sign(payload)}"


def _my_read_cookie(raw: str, *, now: int | None = None) -> str | None:
    """验签 + 验期 -> uid_hash;任何不对劲都返回 None(当作没登录)。"""
    if _ACT is None:
        return None
    try:
        server_now = int(time.time()) if now is None else int(now)
        parts = str(raw or "").split("|")
        if len(parts) != 3:
            return None
        uid_hash, exp_s, sig = parts
        if not hmac.compare_digest(_my_sign(f"{uid_hash}|{exp_s}"), sig):
            return None                      # 签名不对 = 有人改了 cookie 想看别人的数据
        if int(exp_s) <= server_now:
            return None                      # 过期
        if _uid_hash_is_revoked(uid_hash):
            return None                      # 已登录后撤销也立即失效，不等 7 天 TTL
        return uid_hash
    except Exception:  # noqa: BLE001
        return None


def _my_revoked() -> set[str] | None:
    """实时读取撤销表；缺失视为尚未初始化，读取失败则返回不可用。"""
    if _ACT is None:
        return None
    p = Path(_ACT.revoked_path)
    if not p.exists():
        return set()
    if not p.is_file():
        return None
    try:
        return {ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()}
    except Exception:  # noqa: BLE001
        return None


def _uid_hash_is_revoked(uid_hash: str) -> bool:
    """uid_hash 会话共用的实时撤销门：/my 与 /dashboard 必须保持同一语义。"""
    import my_records
    resolver = getattr(_ACT, "revoked_canonical_uid_hashes", None)
    if callable(resolver):
        try:
            hashes = resolver()
            if type(hashes) is not frozenset or any(
                type(value) is not str or re.fullmatch(r"[0-9a-f]{16}", value) is None
                for value in hashes
            ):
                return True
            return any(hmac.compare_digest(value, uid_hash) for value in hashes)
        except Exception:  # noqa: BLE001 - uncertain canonical revocation denies private cookies
            return True
    revoked = _my_revoked()
    if revoked is None:
        # 撤销表状态不可确认时拒绝私有会话，避免 I/O 故障把已撤销 cookie 放回。
        return True
    return any(
        hmac.compare_digest(my_records.uid_hash_of(revoked_uid), uid_hash)
        for revoked_uid in revoked
    )


def _feature_cookie_expiry(entitlement: dict) -> int | None:
    """Keep purchased feature windows separate from the longer base membership.

    Old services/long-term tokens retain the old membership-expiry response.
    A renewal-aware response may never silently fall back to that later date.
    """
    if "feature_exp" in entitlement or "membership_revision" in entitlement:
        feature_exp = entitlement.get("feature_exp")
        membership_exp = entitlement.get("membership_exp")
        if (type(feature_exp) is not int or type(membership_exp) is not int
                or not 0 < feature_exp <= membership_exp):
            raise ValueError("invalid membership feature expiry")
        return feature_exp
    membership_exp = entitlement.get("membership_exp")
    return int(membership_exp) if isinstance(membership_exp, (int, float)) else None


def _my_uid_hash_from_token(
    token: str,
    *,
    now: int | None = None,
) -> tuple[str | None, str, int | None]:
    """>=90 天/历史长期 token -> 本人样本 uid_hash；不足 90 天不能查看个人云记录。"""
    if _ACT is None:
        return None, "服务器未开启激活服务", None
    tok = (token or "").strip()
    if not tok:
        return None, "请输入 token", None
    server_now = int(time.time()) if now is None else int(now)
    ok, reason, entitlement = _ACT.authorize_my_records(tok, now=server_now)
    if not ok or not entitlement:
        errors = {
            "bad_token": "token 无效（请核对是否粘贴完整）",
            "revoked": "该 token 已被撤销",
            "plan_required": "当前档位不含“我的记录”权限（需要有效的 90 天及以上订阅）",
            "expired": "订阅已到期",
            "state_error": "服务器暂时无法确认订阅起点，请稍后重试",
        }
        return None, errors.get(reason, "权限验证失败"), None
    import my_records
    try:
        feature_exp = _feature_cookie_expiry(entitlement)
    except (ValueError, TypeError, OverflowError):
        return None, "服务器暂时无法确认会员权益，请稍后重试", None
    return my_records.uid_hash_of(str(entitlement["uid"])), "", feature_exp


def _dash_sign(payload: str) -> str:
    """私有 dashboard cookie 独立域签名，不能拿任意 /my cookie 横向升级权限。"""
    if _ACT is None:
        return ""
    scoped = f"dashboard|{payload}"
    return hmac.new(_ACT.secret, scoped.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def _dash_make_cookie(uid_hash: str, membership_exp: int | None, *, now: int | None = None) -> tuple[str, int]:
    now = int(time.time()) if now is None else int(now)
    session_exp = now + _DASH_TTL
    if membership_exp is not None:
        session_exp = min(session_exp, int(membership_exp))
    max_age = max(1, session_exp - now)
    payload = f"{uid_hash}|{session_exp}"
    return f"{payload}|{_dash_sign(payload)}", max_age


def _dash_read_cookie(raw: str, *, now: int | None = None) -> str | None:
    """验私有 dashboard 会话，并实时应用 revoked.txt。cookie 不含 token 或原始 uid。"""
    if _ACT is None:
        return None
    now = int(time.time()) if now is None else int(now)
    try:
        uid_hash, exp_s, sig = str(raw or "").split("|")
        if not re.fullmatch(r"[0-9a-f]{16}", uid_hash):
            return None
        if not hmac.compare_digest(_dash_sign(f"{uid_hash}|{exp_s}"), sig):
            return None
        if int(exp_s) <= now:
            return None
        if _uid_hash_is_revoked(uid_hash):
            return None
        return uid_hash
    except Exception:  # noqa: BLE001
        return None


def _dashboard_uid_hash_from_token(token: str, *, now: int | None = None) -> tuple[str | None, str, int | None]:
    """365 天/历史永久 token -> 私有 dashboard cookie 所需身份与真实会员到期点。"""
    if _ACT is None:
        return None, "服务器未开启激活服务", None
    ok, reason, entitlement = _ACT.authorize_private_dashboard(token, now=now)
    if not ok or not entitlement:
        errors = {
            "bad_token": "token 无效（请核对是否粘贴完整）",
            "revoked": "该 token 已被撤销",
            "plan_required": "当前档位不含私有大数据权限（需要有效的 365 天订阅）",
            "expired": "365 天订阅已到期",
            "state_error": "服务器暂时无法确认订阅起点，请稍后重试",
        }
        return None, errors.get(reason, "权限验证失败"), None
    import my_records
    try:
        feature_exp = _feature_cookie_expiry(entitlement)
    except (ValueError, TypeError, OverflowError):
        return None, "服务器暂时无法确认会员权益，请稍后重试", None
    return my_records.uid_hash_of(str(entitlement["uid"])), "", feature_exp


# Caddy 在本机反代 HTTPS -> 127.0.0.1:18900，因此不能直接拿 socket peer 做限流：
# 否则所有域名请求都被误当 localhost 而绕过限流。只当 peer 确实是 loopback 时才信任
# Caddy 写入的 X-Forwarded-For；直连 18900 的公网请求即使伪造该头也以 socket peer 为准。
def _effective_client_ip(peer_ip: str, forwarded_for: str | None) -> str:
    raw_peer = str(peer_ip or "").strip()
    try:
        peer = ipaddress.ip_address(raw_peer)
    except ValueError:
        return raw_peer[:64] or "unknown"
    if not peer.is_loopback:
        return str(peer)
    first_forwarded = str(forwarded_for or "").split(",", 1)[0].strip()
    if first_forwarded:
        try:
            return str(ipaddress.ip_address(first_forwarded))
        except ValueError:
            pass
    return str(peer)


# 限流: 每 IP 每窗口最多 N 次(防 flood DoS;朋友圈用量远低于此)。localhost 不限(本地运维)。
_RATE: dict[str, list] = defaultdict(list)
_RATE_LOCK = threading.Lock()
_RATE_WINDOW = 60.0
_RATE_MAX = 120
_RATE_SWEEP_EVERY = 1000   # 每 N 次调用清一次过期桶(#7: 防不同 IP 各留一项无界增长)
_rate_calls = 0


def _rate_sweep(now: float) -> None:
    """删掉【已过期(最近时间戳超窗口)或空】的 IP 桶(#7)。调用方须持 _RATE_LOCK。
    defaultdict 每见新 IP 建一项, 静默的 IP 桶永不回收 → 缓慢无界增长; 周期清扫钉住上界。"""
    cut = now - _RATE_WINDOW
    for ip in [k for k, q in _RATE.items() if not q or q[-1] < cut]:
        del _RATE[ip]


def _rate_ok(ip: str, now: float | None = None) -> bool:
    if ip in ("127.0.0.1", "::1", "localhost"):
        return True
    now = time.time() if now is None else now
    with _RATE_LOCK:
        global _rate_calls
        _rate_calls += 1
        if _rate_calls % _RATE_SWEEP_EVERY == 0:
            _rate_sweep(now)
        q = _RATE[ip]
        cut = now - _RATE_WINDOW
        while q and q[0] < cut:
            q.pop(0)
        if len(q) >= _RATE_MAX:
            return False
        q.append(now)
        return True


_MAX_BODY = 1 << 20  # /ingest 请求体上限 1 MiB(防 DoS;一次上传几十个样本也远小于此)
_SIG_WINDOW = 300.0  # HMAC 上传签名的时间戳容差(秒): 防重放, 过期/未来戳拒收
_UPLOAD_IDENTITY_MODES = frozenset({"disabled", "compat", "required"})
_UPLOAD_NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
_UPLOAD_FAILURE_CLASSES = frozenset(
    {
        "identity_required",
        "lease_expired",
        "uid_revoked",
        "device_revoked",
        "identity_invalid",
        "identity_state_error",
        "request_replay",
        "request_in_flight",
    }
)


def _public_upload_failure(reason: str) -> str:
    return {
        "identity_required": "identity_required",
        "identity_expired": "lease_expired",
        "identity_revoked": "uid_revoked",
        "identity_device_revoked": "device_revoked",
        "identity_state_error": "identity_state_error",
        "identity_replay": "request_replay",
        "identity_in_flight": "request_in_flight",
    }.get(reason, "identity_invalid")


@dataclass(frozen=True)
class UploadIdentity:
    trust: str
    subject_kind: str | None = None
    subject_id: str | None = None
    uid_hash: str | None = None
    device_alias: str | None = None
    security_epoch: int | None = None
    lease_id: str | None = None
    lease_exp: int | None = None
    request_nonce: str | None = None


def _upload_metric_identity_from_authority(identity: object) -> str:
    if type(identity) is not UploadIdentity:  # noqa: E721 - authority boundary
        return "none"
    if type(identity.trust) is not str:  # noqa: E721
        return "none"
    if identity.trust == "server_signed":
        return "trusted"
    if identity.trust == "legacy_unverified":
        return "legacy"
    return "none"


@dataclass(frozen=True)
class UploadIdentityDecision:
    http_code: int
    reason: str
    identity: UploadIdentity | None


def _header_exact(headers, name: str):
    try:
        value = headers.get(name)
    except Exception:  # noqa: BLE001 - HTTP boundary is fail closed
        return None
    return value if type(value) is str else None


def _read_upload_identity_server_now() -> int | None:
    """Read one signed-upload authority clock without escaping the HTTP boundary."""
    try:
        return int(time.time())
    except Exception:  # noqa: BLE001 - unavailable authority clock fails closed
        return None


def _authorize_upload_identity(
    headers,
    raw: bytes,
    *,
    mode: str,
    service,
    now: int | None = None,
) -> UploadIdentityDecision:
    """Bind an ingest request to current server authority or explicit legacy."""
    if type(mode) is not str or mode not in _UPLOAD_IDENTITY_MODES:  # noqa: E721
        return UploadIdentityDecision(503, "identity_state_error", None)
    envelope = _header_exact(headers, "X-Upload-Lease")
    nonce = _header_exact(headers, "X-Upload-Nonce")
    supplied = envelope is not None or nonce is not None
    if mode == "disabled":
        # Rollback means the signed-identity feature is ignored as a whole.
        # New clients may still carry the two headers until their process lease
        # expires; rejecting them would make rollback itself an outage.  The
        # request remains explicitly untrusted and can never enter the private
        # identity index.
        return UploadIdentityDecision(200, "legacy_unverified", UploadIdentity("legacy_unverified"))
    if not supplied:
        if mode == "required":
            return UploadIdentityDecision(401, "identity_required", None)
        return UploadIdentityDecision(200, "legacy_unverified", UploadIdentity("legacy_unverified"))
    if (
        envelope is None
        or nonce is None
        or _UPLOAD_NONCE_RE.fullmatch(nonce) is None
        or type(raw) is not bytes  # noqa: E721
    ):
        return UploadIdentityDecision(401, "identity_invalid", None)
    if service is None or not hasattr(service, "authorize_upload_lease"):
        return UploadIdentityDecision(503, "identity_state_error", None)
    if now is None:
        now = _read_upload_identity_server_now()
        if now is None:
            return UploadIdentityDecision(503, "identity_state_error", None)
    try:
        ok, reason, payload = service.authorize_upload_lease(envelope, now=now)
    except Exception:  # noqa: BLE001 - never expose service exception details
        return UploadIdentityDecision(503, "identity_state_error", None)
    if not ok or type(payload) is not dict:  # noqa: E721
        code = 503 if reason == "identity_state_error" else 401
        safe_reason = reason if type(reason) is str and reason.startswith("identity_") else "identity_invalid"
        return UploadIdentityDecision(code, safe_reason, None)
    try:
        identity = UploadIdentity(
            trust="server_signed",
            subject_kind=payload["subject_kind"],
            subject_id=payload["subject_id"],
            uid_hash=payload["uid_hash"],
            device_alias=payload["device_alias"],
            security_epoch=payload["security_epoch"],
            lease_id=payload["lease_id"],
            lease_exp=payload["exp"],
            request_nonce=nonce,
        )
    except (KeyError, TypeError):
        return UploadIdentityDecision(401, "identity_invalid", None)
    return UploadIdentityDecision(200, "ok", identity)


def _upload_signature_message(
    timestamp: str,
    raw: bytes,
    *,
    upload_lease: str | None,
    nonce: str | None,
) -> bytes:
    """Return the exact legacy or lease-bound request-auth message."""
    if type(timestamp) is not str or not timestamp.isascii():  # noqa: E721
        raise ValueError("invalid upload timestamp")
    if type(raw) is not bytes:  # noqa: E721
        raise ValueError("invalid upload body")
    if upload_lease is None and nonce is None:
        return timestamp.encode("ascii") + b"." + raw
    if (
        type(upload_lease) is not str  # noqa: E721
        or type(nonce) is not str  # noqa: E721
        or _UPLOAD_NONCE_RE.fullmatch(nonce) is None
    ):
        raise ValueError("invalid upload identity signature input")
    try:
        lease_digest = hashlib.sha256(upload_lease.encode("ascii")).hexdigest()
    except UnicodeEncodeError as exc:
        raise ValueError("invalid upload identity signature input") from exc
    return (
        b"bidking/upload-request/v1\x00"
        + timestamp.encode("ascii")
        + b"\x00"
        + nonce.encode("ascii")
        + b"\x00"
        + lease_digest.encode("ascii")
        + b"\x00"
        + raw
    )


def _request_content_length(headers) -> int:
    """Return a nonnegative decimal length, or reject before any body read."""
    values = headers.get_all("Content-Length") if hasattr(headers, "get_all") else None
    if values is not None and len(values) != 1:
        raise ValueError("ambiguous content-length")
    value = headers.get("Content-Length")
    if value is None or value == "":
        return 0
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value.strip()):
        raise ValueError("bad content-length")
    return int(value.strip())


def _loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _auth_ok(headers, raw: bytes) -> bool:
    """Authenticate legacy requests or bind HMAC to nonce+lease digest+body."""
    if not _KEY:
        return True
    sig = _header_exact(headers, "X-Sig")
    ts = _header_exact(headers, "X-Sig-Ts")
    upload_lease = _header_exact(headers, "X-Upload-Lease")
    nonce = _header_exact(headers, "X-Upload-Nonce")
    identity_supplied = upload_lease is not None or nonce is not None
    if sig and ts:
        try:
            tsi = float(ts)
            message = _upload_signature_message(
                ts,
                raw,
                upload_lease=upload_lease,
                nonce=nonce,
            )
        except (TypeError, ValueError, OverflowError):
            return False
        if abs(time.time() - tsi) > _SIG_WINDOW:                       # 防重放
            return False
        mac = hmac.new(_KEY.encode("utf-8"), message, hashlib.sha256).hexdigest()
        return hmac.compare_digest(mac, sig)                           # 常量时间比较, 防计时侧信道
    if identity_supplied:
        return False
    return _header_exact(headers, "X-Api-Key") == _KEY                 # legacy 回退
_REQUIRED = ("map_id", "final_quality_counts")
_ALLOWED = {  # 白名单字段(只落这些)
    "quote_application",
    "schema", "map_id", "hero", "session_hash", "round", "action_round",
    "final_quality_counts", "final_quality_cells", "final_quality_values",
    "inventory_count", "inventory_cells", "known_value_sum", "total_item_count",
    # 0.2.7 起: 真藏品件数(目录内 且 占格>0)。inventory_count − catalog_item_count = 混进结算的
    # 【非目录垃圾件】(0格/0价, id 形如 1888888, 疑似排位代币)。
    # 用途: 定案"游戏声明的 total_item_count 到底数不数垃圾件" —— 这个未决事实决定
    #   · 声明【不数】-> 下面的 _declared_gt_captured 有【假阴性】(垃圾件垫高实抓, 真欠计不报警)
    #   · 声明【也数】-> 判据没问题, 但引擎的 total_item_count 硬剪枝被垫高的件数污染(过估)
    # 聚合字段判不出来(实测 45,693 条: 两个假说各被一半数据证伪), 只有逐件清单能定案, 故让客户端算好传上来。
    # ⚠️ 判据【先别改】—— 改错方向 = 把健康样本静默隔离, 比现在的假阴性更糟(parse-residual 那次的教训)。
    "catalog_item_count",
    "catalog_value_complete", "catalog_unknown_item_ids",
    "catalog_valued_item_count", "catalog_settled_item_count",
    "settlement_incomplete", "client_version", "client_ts", "received_ts", "player",
    "warehouse_source_schema",
    "warehouse_source_qualification", "warehouse_map_name",
    "warehouse_map_name_source", "warehouse_table_status",
    "warehouse_source_reason", "warehouse_source_generation",
    "warehouse_map_table_build", "warehouse_map_table_sha256",
    "warehouse_expected_map_table_sha256",
    "warehouse_map_source_contract_sha256",
    "warehouse_expected_map_source_contract_sha256",
    "warehouse_map_profile_generation", "warehouse_game_version",
    "warehouse_auction_mode", "warehouse_owner_evidence_status",
    "warehouse_source_conflict", "warehouse_quarantine_required",
    "official_distribution_eligible",
    # 完整结算的去身份化类别事实。服务器在落盘前会再次按 PW2 schema
    # generation、physical/model 关系、authority generation/identity 与内建类型逐项校验；
    # 客户端只负责提供候选，不能借这些字段取得 official-distribution 权威。
    "physical_map_id", "model_map_id", "warehouse_generation",
    "category_authority_generation", "category_authority_identity",
    "settled_items_v1",
    "players", "winner", "winner_bid", "self_bid", "winner_is_self",
    "settlement_welfare", "tools", "hskills", "rare_items",
    "rank_points", "rank_tier", "rank_delta", "rank_after",   # 排位赛: 本机赛前分/段位/本局涨跌/赛后分(看板玩家排位变化)
    "player_ranks",   # 各玩家(含非我方)排位分: 昵称->赛前分/段位(看板显示对手排位; 只昵称+分数无账号id)
    "app_version", "game_version",   # 产品版本 + 探测到的游戏版本(版本检测/发行/会员地基)
    "ref_inputs",   # 决策轮引擎证据桥(structured_ref_inputs): 下拉后可离线回放 live 估计做报价/件数误差分析+旋钮A/B。纯游戏数值证据无PII, 典型5.5KB
}

# 版本清单(GET /version 下发给客户端: 客户端启动比对"我的版本 vs 最新版/游戏版本")。
# 【mtime 热重载】(审计 0710, 同 _DASH_CACHE 模式): 改 version_manifest.json 保存即生效, 不再需要
# 重启 collector——消掉发版夜"编辑清单忘重启 → 客户端一直看不到新版"的坑。解析失败保留上一份好的
# (清单坏掉不该把 /version 打回空)。没有该文件 = 不下发版本信息(latest=None → 客户端不提示更新,
# 向后兼容)。字段可扩展(将来加 features 做会员开关亦只是此 JSON 加键)。
_VM_PATH: Path | None = None
_VM_CACHE: dict = {"key": None, "data": {}}


def _version_manifest() -> dict:
    """当前版本清单(mtime 变了才重读; 坏 JSON 保留旧值并告警一次)。"""
    p = _VM_PATH
    if p is None or not p.exists():
        return _VM_CACHE["data"] if _VM_CACHE["key"] is not None else {}
    try:
        st = p.stat()
        key = (st.st_mtime_ns, st.st_size)
        if key != _VM_CACHE["key"]:
            _VM_CACHE["data"] = json.loads(p.read_text(encoding="utf-8"))
            _VM_CACHE["key"] = key
    except Exception as e:  # noqa: BLE001  清单坏掉不该拖垮收集服务; 保留上一份好的
        if _VM_CACHE["key"] != "bad":
            print(f"[collector] 警告: 版本清单 {p} 解析失败({type(e).__name__}), 沿用上一份", flush=True)
            _VM_CACHE["key"] = "bad"   # 只告警一次, 直到文件再变(下次 stat key 不同会重试)
    return _VM_CACHE["data"]


def _public_version_payload() -> dict:
    """对外 /version 响应: 只回客户端比对需要的字段, 给稳定默认(缺字段不崩客户端)。"""
    m = _version_manifest() or {}
    return {
        "latest": m.get("latest"),          # 最新客户端版本(None=未配置→客户端不提示更新)
        "min": m.get("min"),                # 最低受支持版本(低于则强提示更新)
        "url": m.get("url") or "",          # 下载/通知地址(可空)
        "notice": m.get("notice") or "",    # 给用户的通知文案(可空)
        "game_version": m.get("game_version"),  # 服务器已知的当前游戏版本(可空)
    }


def _build_existing_index() -> tuple[set[str], set[str], Counter]:
    """流式构建完整历史索引；返回前不修改任何请求可见全局状态。"""
    seen: set[str] = set()
    quarantine_seen: set[str] = set()
    map_count: Counter = Counter()
    # 主库: 计入去重 + 统计。隔离库: 只计入去重(不进统计/分析)。
    if _OUT.exists():
        with _OUT.open("r", encoding="utf-8") as stream:
            for ln in stream:
                try:
                    d = json.loads(ln)
                except Exception:  # noqa: BLE001
                    continue
                h = d.get("session_hash")
                if h:
                    seen.add(h)
                if d.get("map_id") is not None:
                    map_count[d["map_id"]] += 1
    q = _OUT.with_name("samples_quarantine.jsonl")
    if q.exists():
        with q.open("r", encoding="utf-8") as stream:
            for ln in stream:
                try:
                    h = json.loads(ln).get("session_hash")
                except Exception:  # noqa: BLE001
                    continue
                if h:
                    # 隔离去重独立集(不进主库seen, 允许有效重传救回)。
                    quarantine_seen.add(h)
    return seen, quarantine_seen, map_count


def _load_existing() -> None:
    """完整扫描成功后原子发布；失败时保留此前完整索引。"""
    global _SEEN, _QUAR_SEEN, _MAP_COUNT, _SAMPLE_HISTORY_INDEX
    if _HISTORY_INDEX_DB_FILE is not None:
        candidate = _new_sample_history_index(_HISTORY_INDEX_DB_FILE)
        try:
            report = candidate.refresh()
            summary = candidate.summary()
        except Exception:
            candidate.close()
            raise
        with _LOCK:
            previous = _SAMPLE_HISTORY_INDEX
            _SAMPLE_HISTORY_INDEX = candidate
            _SEEN, _QUAR_SEEN = set(), set()
            _MAP_COUNT = summary["map_count"]
            if previous is not None:
                previous.close()
        print(f"[collector-history] scan_bytes={report['scan_bytes']} scan_rows={report['scan_lines']} "
              f"validation_bytes={report['validation_bytes']} rebuilt={int(report['rebuilt'])}", flush=True)
        return
    seen, quarantine_seen, map_count = _build_existing_index()
    with _LOCK:
        _SEEN = seen
        _QUAR_SEEN = quarantine_seen
        _MAP_COUNT = map_count


def _history_index_state() -> str:
    with _HISTORY_INDEX_LOCK:
        state = _HISTORY_INDEX_STATE
    if state == "ready" and _HISTORY_INDEX_DB_FILE is not None and (
        _SAMPLE_HISTORY_INDEX is None or not _SAMPLE_HISTORY_INDEX.ready
    ):
        return "failed"
    return state if state in _HISTORY_INDEX_STATES else "failed"


def _history_index_ready() -> bool:
    return _history_index_state() == "ready"


def _start_history_index_load() -> threading.Thread:
    """后台建立历史索引；激活可先服务，数据路由在 ready 前保持关闭。"""
    global _HISTORY_INDEX_STATE, _HISTORY_INDEX_THREAD
    load_existing = _load_existing
    warm_public_cache = _warm_public_cache
    with _HISTORY_INDEX_LOCK:
        if _HISTORY_INDEX_STATE == "loading":
            raise RuntimeError("history index load already running")
        _HISTORY_INDEX_STATE = "loading"
        _HISTORY_INDEX_DONE.clear()

    def _run() -> None:
        global _HISTORY_INDEX_STATE
        started = time.monotonic()
        try:
            load_existing()
        except Exception as exc:  # noqa: BLE001 - 请求面必须保持激活可用、数据面关闭
            with _HISTORY_INDEX_LOCK:
                _HISTORY_INDEX_STATE = "failed"
            print(
                f"[collector] 历史索引加载失败 category={type(exc).__name__}; "
                "上传与全库页面保持关闭",
                flush=True,
            )
        else:
            with _HISTORY_INDEX_LOCK:
                _HISTORY_INDEX_STATE = "ready"
            counts = (_SAMPLE_HISTORY_INDEX.summary() if _SAMPLE_HISTORY_INDEX is not None else
                      {"main_unique": len(_SEEN), "quarantine_unique": len(_QUAR_SEEN)})
            print(
                f"[collector] 历史索引已就绪 main={counts['main_unique']} "
                f"quarantine={counts['quarantine_unique']} "
                f"elapsed_ms={(time.monotonic() - started) * 1000.0:.1f}",
                flush=True,
            )
            warm_public_cache()
        finally:
            _HISTORY_INDEX_DONE.set()

    thread = threading.Thread(target=_run, name="history-index-load", daemon=True)
    with _HISTORY_INDEX_LOCK:
        _HISTORY_INDEX_THREAD = thread
    thread.start()
    return thread


def _quarantine_path() -> Path:
    return _OUT.with_name("samples_quarantine.jsonl")


def _quarantine_file_state() -> tuple[int, int]:
    """Return a privacy-safe cache key for the quarantine file."""
    try:
        st = _quarantine_path().stat()
    except OSError:
        return (0, 0)
    return (int(st.st_mtime_ns), int(st.st_size))


_RANKED_WAREHOUSE_MAP_PREFIXES = frozenset({55, 56})
_POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE = "postupdate_unknown_warehouse_source"
_OFFICIAL_RANDOM_WAREHOUSE = "official_random_warehouse"
_PLAYER_CUSTOM_WAREHOUSE = "player_custom_warehouse"
_PREUPDATE_OFFICIAL_GENERATION = "preupdate_official_random"
_LEGACY_POSTUPDATE_PLAYER_WAREHOUSE_GENERATION = (
    "postupdate_player_warehouse_activity"
)
_UPDATE606_PLAYER_WAREHOUSE_GENERATION = "cd4af1-2026-08-13-606"
_POSTUPDATE_PLAYER_WAREHOUSE_GENERATION = "d66aee-2026-08-20-599"
_CURRENT_PLAYER_WAREHOUSE_GENERATION = "4ae819-2026-08-27-665"
_OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION = "eb1e8f-2026-09-03-711"
_HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION = "68171c-2026-09-03-907"
_OFFICIAL_20260910_PLAYER_WAREHOUSE_GENERATION = "b049c3-2026-09-10-597"
_OFFICIAL_20261008_PLAYER_WAREHOUSE_GENERATION = "76b7ca-2026-10-08-723"
_UNKNOWN_WAREHOUSE_GENERATION = "unknown_warehouse_generation"
_PW2_OWNER_EVIDENCE_STATUSES = frozenset(
    {"absent", "valid", "malformed_conflict"}
)
_PW2_SOURCE_GENERATIONS = frozenset(
    {
        _PREUPDATE_OFFICIAL_GENERATION,
        _LEGACY_POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
        _UPDATE606_PLAYER_WAREHOUSE_GENERATION,
        _POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
        _CURRENT_PLAYER_WAREHOUSE_GENERATION,
        _OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
        _HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        _OFFICIAL_20260910_PLAYER_WAREHOUSE_GENERATION,
        _OFFICIAL_20261008_PLAYER_WAREHOUSE_GENERATION,
        _UNKNOWN_WAREHOUSE_GENERATION,
    }
)
_PW2_TRUSTED_MAP_TABLE_BUILD = "24830618"
_PW2_TRUSTED_MAP_TABLE_SHA256 = (
    "6e2f8aa772c23184042cdfb0b4ce99c822676707378f9a1dc2bcaf4c10ae237f"
)
_PW2_TRUSTED_SOURCE_CONTRACT_SHA256 = (
    "bdae544b3b324b4037d99fbc07e94e1ae55a20d9777f1f06ba15db32e54895f5"
)
_PW2_SCHEMA = "player_warehouse_source_pw2_d66aee_v3"
_PW2_CURRENT_SCHEMA = "player_warehouse_source_pw2_4ae819_v4"
_PW2_OFFICIAL_20260903_SCHEMA = "player_warehouse_source_pw2_eb1e8f_v5"
_PW2_HOTFIX_20260903_SCHEMA = "player_warehouse_source_pw2_68171c_v6"
_PW2_OFFICIAL_20260910_SCHEMA = "player_warehouse_source_pw2_b049c3_v7"
_PW2_OFFICIAL_20261008_SCHEMA = "player_warehouse_source_pw2_76b7ca_v8"
_PW2_CURRENT_MAP_TABLE_BUILD = "24962364"
_PW2_CURRENT_MAP_TABLE_SHA256 = (
    "06f69afd8c2196ab945d829e9140489e2f0f4df8f7af20f614565d7af6447e52"
)
_PW2_CURRENT_SOURCE_CONTRACT_SHA256 = (
    "d51d12095f867b8325da57721a9d829dee078e3f777b1ac867ee9eb6874a23ba"
)
_PW2_OFFICIAL_20260903_MAP_TABLE_BUILD = "25094235"
_PW2_OFFICIAL_20260903_MAP_TABLE_SHA256 = (
    "7abe97f33ec48c8b18887de5abcddc1969b5decc2b9be30b1f8fd73f5b71c9fc"
)
_PW2_OFFICIAL_20260903_SOURCE_CONTRACT_SHA256 = (
    "b6e0635f8033bb20dbac7a6c6a985fe5bd4d923da1bf9a2d3d21dd524273a6f3"
)
_PW2_HOTFIX_20260903_MAP_TABLE_BUILD = "25096377"
_PW2_HOTFIX_20260903_MAP_TABLE_SHA256 = (
    _PW2_OFFICIAL_20260903_MAP_TABLE_SHA256
)
_PW2_HOTFIX_20260903_SOURCE_CONTRACT_SHA256 = (
    _PW2_OFFICIAL_20260903_SOURCE_CONTRACT_SHA256
)
_CURRENT_CATEGORY_AUTHORITY_GENERATION = (
    "item-table-sha256:"
    "7ac11867652b21ebbd62461cd95da84addfe721dea5c01307e4dd568fd60933c"
)
_OFFICIAL_20260903_CATEGORY_AUTHORITY_GENERATION = (
    "item-table-sha256:"
    "72c3430e82e82a4b01a1fa42e79335b50ab6bbbb7f09c9b438b2511caef566c3"
)
_OFFICIAL_20260903_CATEGORY_AUTHORITY_IDENTITY = (
    "2e70477d3b0ccc6e1b764c5ecbfce2eb128a330a480e9e4a62875a1f52e8aab7"
)
_HOTFIX_20260903_CATEGORY_AUTHORITY_GENERATION = (
    "item-table-sha256:"
    "bfec359189e9fb5c77440abdc36f2fad7d77299e5d37394b5b855a8e4dcae57e"
)
_HOTFIX_20260903_CATEGORY_AUTHORITY_IDENTITY = (
    "d23d90c0d4f432f94a129435417063177ab21646fa4987dce5118ccce5a55db5"
)
_SETTLEMENT_CATEGORY_FIELDS = frozenset(
    {
        "physical_map_id",
        "model_map_id",
        "warehouse_generation",
        "category_authority_generation",
        "settled_items_v1",
    }
)
_SETTLEMENT_CATEGORY_OPTIONAL_FIELDS = frozenset(
    {"category_authority_identity"}
)
_RAVEN_SETTLEMENT_PHYSICAL_MAP_IDS = frozenset(range(5101, 5111))
_SETTLEMENT_PRIMARY_CATEGORIES = frozenset(range(101, 111))
_SETTLEMENT_PRIMARY_CATEGORIES_OFFICIAL_20260903 = frozenset(
    range(100, 112)
)
_MAX_SETTLEMENT_CATEGORY_ITEMS = 512
_PW2_LEGACY_FULL_MAP_SCHEMA = "player_warehouse_source_pw2_update606_v2"
_PW2_LEGACY_FULL_MAP_BUILD = "24704978"
_PW2_LEGACY_FULL_MAP_EXPECTED_SHA256 = (
    "4efb7c67856b6260fbcf040d0e89dc755e9fe8d476276d0074d637b63052d6b9"
)
_PW2_LEGACY_FULL_MAP_EXPECTED_SOURCE_CONTRACT_SHA256 = (
    "a5f019d77de19599a981c26d4b3be7eff32e990fb2c93344542570314d3caea3"
)
_UPDATE606_UNOPENED_MAP_IDS = frozenset({5506, 6001})
_CURRENT_UNOPENED_MAP_IDS = frozenset({5506})
_OFFICIAL_20260903_UNOPENED_MAP_IDS = frozenset({5506, 6001})
_PW2_SERVER_POSTUPDATE_EPOCH = datetime(
    2026, 8, 20, tzinfo=timezone.utc
).timestamp()
_PW2_SERVER_CURRENT_EPOCH = datetime(
    2026, 8, 27, 4, tzinfo=timezone.utc
).timestamp()
_PW2_SERVER_OFFICIAL_20260903_EPOCH = datetime(
    2026, 9, 3, 4, tzinfo=timezone.utc
).timestamp()
_PW2_SERVER_HOTFIX_20260903_EPOCH = datetime(
    2026, 9, 3, 8, 38, tzinfo=timezone.utc
).timestamp()
# Exact official manifest build time 2026/9/10 09:57:00 (UTC+8).
_PW2_SERVER_OFFICIAL_20260910_EPOCH = datetime(
    2026, 9, 10, 1, 57, tzinfo=timezone.utc
).timestamp()
# Official October 8 update announcement: 10:00 UTC+8. This intake boundary
# is not evidence that any particular activity map is currently open.
_PW2_SERVER_OFFICIAL_20261008_EPOCH = datetime(
    2026, 10, 8, 2, tzinfo=timezone.utc
).timestamp()
_PW2_MAX_SAFE_SERVER_EPOCH = 253_402_300_799
_PW2_PROJECTION_FIELDS = frozenset(
    {
        "warehouse_source_qualification",
        "warehouse_map_name",
        "warehouse_map_name_source",
        "warehouse_table_status",
        "warehouse_source_reason",
        "warehouse_source_generation",
        "warehouse_map_table_build",
        "warehouse_map_table_sha256",
        "warehouse_expected_map_table_sha256",
        "warehouse_map_source_contract_sha256",
        "warehouse_expected_map_source_contract_sha256",
        "warehouse_auction_mode",
        "warehouse_owner_evidence_status",
        "warehouse_source_conflict",
        "warehouse_quarantine_required",
        "official_distribution_eligible",
    }
)


def _normalize_pw2_server_received_at(
    value,
) -> tuple[int | float | None, int | float | None]:
    """Return ``(qualification_value, persisted_millis)`` without numeric coercion.

    ``time.time()`` normally returns a finite built-in float.  Tests and embedded
    runtimes can still supply malformed values, and ``math.isfinite(10**400)``
    raises ``OverflowError`` while trying to coerce that int to float.  Reject
    non-exact numeric types before touching their protocols, probe representability
    inside a fixed exception boundary, and keep the original value for the
    unrounded PW2 launch-boundary comparison.
    """
    if type(value) not in {int, float}:  # noqa: E721 - reject bool/subclasses
        return None, None
    try:
        finite_probe = float(value)
        if (
            not math.isfinite(finite_probe)
            or finite_probe < 0
            or finite_probe > _PW2_MAX_SAFE_SERVER_EPOCH
        ):
            return None, None
        persisted = round(value, 3)
    except (TypeError, ValueError, OverflowError):
        return None, None
    if type(persisted) not in {int, float}:  # noqa: E721
        return None, None
    return value, persisted


def _read_pw2_server_received_at() -> tuple[
    int | float | None,
    int | float | None,
]:
    """Read the authoritative server clock once and fail closed on read errors."""
    try:
        raw_value = time.time()
    except Exception:  # noqa: BLE001 - clock failure must not abort batch ingest
        return None, None
    return _normalize_pw2_server_received_at(raw_value)


_PW2_REQUIRED_FIELDS = _PW2_PROJECTION_FIELDS | {"warehouse_source_schema"}
_PW2_V5_EXTRA_FIELDS = frozenset(
    {"warehouse_map_profile_generation", "warehouse_game_version"}
)
_PW2_EXACT_GENERATION_SCHEMAS = frozenset(
    {_PW2_OFFICIAL_20260903_SCHEMA, _PW2_HOTFIX_20260903_SCHEMA, _PW2_OFFICIAL_20260910_SCHEMA,
     _PW2_OFFICIAL_20261008_SCHEMA}
)
# Every PW2 field is a trigger.  A client cannot delete the old six trigger
# fields and fall back to the permissive legacy route while retaining forged
# qualification metadata.
_PW2_TRIGGER_FIELDS = _PW2_REQUIRED_FIELDS | _PW2_V5_EXTRA_FIELDS


def _inclusive_ids(*bounds: tuple[int, int]) -> set[int]:
    return {
        map_id
        for first, last in bounds
        for map_id in range(first, last + 1)
    }


# This is the server-owned map-id -> auction-mode projection of the exact
# d66/current BidMap contracts.  The old schemas keep 5506/6001 unopened;
# current v4 admits observed 6001 settlement truth as ``auction_hall`` while
# retaining distribution eligibility=false.  5506 remains unopened.
# The ingest HMAC is shipped to clients, so client-provided
# qualification/mode/hash fields are evidence to verify, never server
# authority.
_PW2_TRUSTED_AUCTION_MODE_BY_MAP = {
    **{
        map_id: "open"
        for map_id in _inclusive_ids(
            (2101, 2107),
            (2201, 2205),
            (2301, 2310),
            (2401, 2410),
            (2501, 2550),
            (2601, 2601),
        )
    },
    **{
        map_id: "training"
        for map_id in _inclusive_ids(
            (3101, 3107),
            (3201, 3205),
            (3301, 3310),
            (3401, 3410),
            (3501, 3510),
        )
    },
    **{
        map_id: "sealed"
        for map_id in _inclusive_ids(
            (4401, 4410),
            (4501, 4550),
            (5601, 5611),
        )
    },
}
_PW2_CURRENT_TRUSTED_AUCTION_MODE_BY_MAP = {
    **_PW2_TRUSTED_AUCTION_MODE_BY_MAP,
    6001: "auction_hall",
}
_PW2_OFFICIAL_20260903_TRUSTED_AUCTION_MODE_BY_MAP = {
    **_PW2_TRUSTED_AUCTION_MODE_BY_MAP,
    **{map_id: "sealed" for map_id in range(5203, 5220)},
}
_PW2_HOTFIX_20260903_TRUSTED_AUCTION_MODE_BY_MAP = dict(
    _PW2_OFFICIAL_20260903_TRUSTED_AUCTION_MODE_BY_MAP
)

_PW2_AUTHORITIES = {
    _PW2_SCHEMA: {
        "generation": _POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
        "map_table_build": _PW2_TRUSTED_MAP_TABLE_BUILD,
        "map_table_sha256": _PW2_TRUSTED_MAP_TABLE_SHA256,
        "source_contract_sha256": _PW2_TRUSTED_SOURCE_CONTRACT_SHA256,
        "min_server_epoch": _PW2_SERVER_POSTUPDATE_EPOCH,
        "auction_mode_by_map": _PW2_TRUSTED_AUCTION_MODE_BY_MAP,
        "unopened_map_ids": _UPDATE606_UNOPENED_MAP_IDS,
        "game_version": _POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
    },
    _PW2_CURRENT_SCHEMA: {
        "generation": _CURRENT_PLAYER_WAREHOUSE_GENERATION,
        "map_table_build": _PW2_CURRENT_MAP_TABLE_BUILD,
        "map_table_sha256": _PW2_CURRENT_MAP_TABLE_SHA256,
        "source_contract_sha256": _PW2_CURRENT_SOURCE_CONTRACT_SHA256,
        "min_server_epoch": _PW2_SERVER_CURRENT_EPOCH,
        "auction_mode_by_map": _PW2_CURRENT_TRUSTED_AUCTION_MODE_BY_MAP,
        "unopened_map_ids": _CURRENT_UNOPENED_MAP_IDS,
        "game_version": _CURRENT_PLAYER_WAREHOUSE_GENERATION,
        "category_authority_generation": (
            _CURRENT_CATEGORY_AUTHORITY_GENERATION
        ),
        "category_authority_identity": None,
        "settlement_primary_categories": _SETTLEMENT_PRIMARY_CATEGORIES,
    },
    _PW2_OFFICIAL_20260903_SCHEMA: {
        "generation": _OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
        "map_table_build": _PW2_OFFICIAL_20260903_MAP_TABLE_BUILD,
        "map_table_sha256": _PW2_OFFICIAL_20260903_MAP_TABLE_SHA256,
        "source_contract_sha256": (
            _PW2_OFFICIAL_20260903_SOURCE_CONTRACT_SHA256
        ),
        "min_server_epoch": _PW2_SERVER_OFFICIAL_20260903_EPOCH,
        "auction_mode_by_map": (
            _PW2_OFFICIAL_20260903_TRUSTED_AUCTION_MODE_BY_MAP
        ),
        "unopened_map_ids": _OFFICIAL_20260903_UNOPENED_MAP_IDS,
        "game_version": _OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
        "category_authority_generation": (
            _OFFICIAL_20260903_CATEGORY_AUTHORITY_GENERATION
        ),
        "category_authority_identity": (
            _OFFICIAL_20260903_CATEGORY_AUTHORITY_IDENTITY
        ),
        "settlement_primary_categories": (
            _SETTLEMENT_PRIMARY_CATEGORIES_OFFICIAL_20260903
        ),
    },
    _PW2_HOTFIX_20260903_SCHEMA: {
        "generation": _HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        "map_table_build": _PW2_HOTFIX_20260903_MAP_TABLE_BUILD,
        "map_table_sha256": _PW2_HOTFIX_20260903_MAP_TABLE_SHA256,
        "source_contract_sha256": (
            _PW2_HOTFIX_20260903_SOURCE_CONTRACT_SHA256
        ),
        "min_server_epoch": _PW2_SERVER_HOTFIX_20260903_EPOCH,
        "auction_mode_by_map": (
            _PW2_HOTFIX_20260903_TRUSTED_AUCTION_MODE_BY_MAP
        ),
        "unopened_map_ids": _OFFICIAL_20260903_UNOPENED_MAP_IDS,
        "game_version": _HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        "category_authority_generation": (
            _HOTFIX_20260903_CATEGORY_AUTHORITY_GENERATION
        ),
        "category_authority_identity": (
            _HOTFIX_20260903_CATEGORY_AUTHORITY_IDENTITY
        ),
        "settlement_primary_categories": (
            _SETTLEMENT_PRIMARY_CATEGORIES_OFFICIAL_20260903
        ),
    },
    _PW2_OFFICIAL_20260910_SCHEMA: {
        "generation": _OFFICIAL_20260910_PLAYER_WAREHOUSE_GENERATION,
        "map_table_build": "25221911",
        "map_table_sha256": _PW2_OFFICIAL_20260903_MAP_TABLE_SHA256,
        "source_contract_sha256": _PW2_OFFICIAL_20260903_SOURCE_CONTRACT_SHA256,
        "min_server_epoch": _PW2_SERVER_OFFICIAL_20260910_EPOCH,
        "auction_mode_by_map": _PW2_OFFICIAL_20260903_TRUSTED_AUCTION_MODE_BY_MAP,
        "unopened_map_ids": _OFFICIAL_20260903_UNOPENED_MAP_IDS,
        "game_version": _OFFICIAL_20260910_PLAYER_WAREHOUSE_GENERATION,
        "category_authority_generation": "item-table-sha256:8a0da40495636e282c8641b6acbd5965b2f900b805baf75c97781641fba17d47",
        "category_authority_identity": "18256cb459f2c748bb1e83e6c899df255189a0f557e8a0be990c05ef865f079a",
        "settlement_primary_categories": _SETTLEMENT_PRIMARY_CATEGORIES_OFFICIAL_20260903,
    },
    _PW2_OFFICIAL_20261008_SCHEMA: {
        "generation": _OFFICIAL_20261008_PLAYER_WAREHOUSE_GENERATION,
        "map_table_build": "25795126",
        "map_table_sha256": _PW2_OFFICIAL_20260903_MAP_TABLE_SHA256,
        "source_contract_sha256": _PW2_OFFICIAL_20260903_SOURCE_CONTRACT_SHA256,
        "min_server_epoch": _PW2_SERVER_OFFICIAL_20261008_EPOCH,
        "auction_mode_by_map": _PW2_OFFICIAL_20260903_TRUSTED_AUCTION_MODE_BY_MAP,
        "unopened_map_ids": _OFFICIAL_20260903_UNOPENED_MAP_IDS,
        "game_version": _OFFICIAL_20261008_PLAYER_WAREHOUSE_GENERATION,
        "category_authority_generation": "item-table-sha256:125550adca27edc14378886d7ab04c7b0b537015a1c59906e374c0659c1d4372",
        "category_authority_identity": "36600b5116701a8152af992e51e1c7d6a0492fed0e841d36c79e0de285f89dde",
        "settlement_primary_categories": _SETTLEMENT_PRIMARY_CATEGORIES_OFFICIAL_20260903,
    },
}


def _ranked_warehouse_map(value: object) -> bool:
    """Recognize only canonical integer 55xx/56xx ranked map ids."""
    return (
        type(value) is int
        and value > 0
        and value // 100 in _RANKED_WAREHOUSE_MAP_PREFIXES
    )


def _warehouse_source_pair(rec: dict) -> tuple[object, object]:
    return (
        rec.get("warehouse_source_qualification"),
        rec.get("official_distribution_eligible"),
    )


def _update606_unopened_map(rec: dict) -> bool:
    map_id = rec.get("map_id")
    if type(map_id) is not int:  # noqa: E721 - reject bool/subclasses
        return False
    schema = rec.get("warehouse_source_schema")
    if schema in _PW2_EXACT_GENERATION_SCHEMAS:
        return map_id in _OFFICIAL_20260903_UNOPENED_MAP_IDS
    if schema == _PW2_CURRENT_SCHEMA:
        return map_id in _CURRENT_UNOPENED_MAP_IDS
    return map_id in _UPDATE606_UNOPENED_MAP_IDS


def _pw2_expected_source_values(
    *,
    auction_mode: str,
    owner_status: str,
    source_generation: str,
) -> tuple[str, bool, str, bool]:
    """Recompute the four client claims from server-owned PW2 inputs."""

    if source_generation == _PREUPDATE_OFFICIAL_GENERATION:
        if owner_status != "absent":
            return (
                _POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
                False,
                "pw2_preupdate_owner_block_conflict",
                True,
            )
        return (
            _OFFICIAL_RANDOM_WAREHOUSE,
            True,
            "preupdate_official_random_generation",
            False,
        )
    if source_generation not in {
        _POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
        _CURRENT_PLAYER_WAREHOUSE_GENERATION,
        _OFFICIAL_20260903_PLAYER_WAREHOUSE_GENERATION,
        _HOTFIX_20260903_PLAYER_WAREHOUSE_GENERATION,
        _OFFICIAL_20260910_PLAYER_WAREHOUSE_GENERATION,
        _OFFICIAL_20261008_PLAYER_WAREHOUSE_GENERATION,
    }:
        return (
            _POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
            False,
            "warehouse_generation_unverified",
            owner_status != "absent",
        )
    if owner_status == "malformed_conflict":
        return (
            _POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
            False,
            "pw2_owner_evidence_conflict",
            True,
        )
    if owner_status == "valid":
        if auction_mode in {"auction_hall", "open"}:
            return (
                _PLAYER_CUSTOM_WAREHOUSE,
                False,
                f"pw2_{auction_mode}_valid_owner_block",
                False,
            )
        return (
            _POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
            False,
            f"pw2_{auction_mode}_owner_block_conflict",
            True,
        )
    if auction_mode in {"auction_hall", "open"}:
        return (
            _POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
            False,
            f"pw2_{auction_mode}_owner_block_absent",
            False,
        )
    if auction_mode == "sealed":
        return (
            _OFFICIAL_RANDOM_WAREHOUSE,
            True,
            "pw2_sealed_owner_block_absent",
            False,
        )
    return (
        _POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE,
        False,
        "pw2_training_owner_block_absent",
        False,
    )


def _reproject_exact_d66aee_legacy_pw2(rec: dict) -> bool:
    """Upgrade only the exact v2 false-stale cohort to current authority.

    Hotfix3/4 packaged the audited 228-row d66aee map table but still compared
    it with the preceding 198-row update606 identity.  Those clients therefore
    emitted a complete v2 projection whose *observed* hashes are the current
    table and whose expected hashes/status remain update606/stale.  The server
    owns the map-mode projection and recomputes every qualification field; no
    other stale, partial or mismatched row is eligible for this compatibility
    path.
    """

    map_id = rec.get("map_id")
    if type(map_id) is not int:  # noqa: E721 - reject bool/coercive subclasses
        return False
    trusted_mode = _PW2_TRUSTED_AUCTION_MODE_BY_MAP.get(map_id)
    if trusted_mode is None or map_id in _UPDATE606_UNOPENED_MAP_IDS:
        return False
    received_ts, _persisted_received_ts = _normalize_pw2_server_received_at(
        rec.get("received_ts")
    )
    if received_ts is None or received_ts < _PW2_SERVER_POSTUPDATE_EPOCH:
        return False

    exact_text = {
        "warehouse_source_schema": _PW2_LEGACY_FULL_MAP_SCHEMA,
        "warehouse_source_qualification": (
            _POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE
        ),
        "warehouse_map_name_source": "bid_map_table",
        "warehouse_table_status": "stale",
        "warehouse_source_reason": "pw2_map_contract_untrusted",
        "warehouse_source_generation": (
            _UPDATE606_PLAYER_WAREHOUSE_GENERATION
        ),
        "warehouse_map_table_build": _PW2_LEGACY_FULL_MAP_BUILD,
        "warehouse_map_table_sha256": _PW2_TRUSTED_MAP_TABLE_SHA256,
        "warehouse_expected_map_table_sha256": (
            _PW2_LEGACY_FULL_MAP_EXPECTED_SHA256
        ),
        "warehouse_map_source_contract_sha256": (
            _PW2_TRUSTED_SOURCE_CONTRACT_SHA256
        ),
        "warehouse_expected_map_source_contract_sha256": (
            _PW2_LEGACY_FULL_MAP_EXPECTED_SOURCE_CONTRACT_SHA256
        ),
        "warehouse_auction_mode": trusted_mode,
    }
    if any(
        type(rec.get(key)) is not str or rec.get(key) != value  # noqa: E721
        for key, value in exact_text.items()
    ):
        return False
    if (
        type(rec.get("warehouse_map_name")) is not str  # noqa: E721
        or not rec["warehouse_map_name"].strip()
    ):
        return False
    owner_status = rec.get("warehouse_owner_evidence_status")
    if (
        type(owner_status) is not str  # noqa: E721
        or owner_status not in _PW2_OWNER_EVIDENCE_STATUSES
    ):
        return False
    if any(
        type(rec.get(key)) is not bool or rec.get(key) is not value  # noqa: E721
        for key, value in {
            "warehouse_source_conflict": True,
            "warehouse_quarantine_required": True,
            "official_distribution_eligible": False,
        }.items()
    ):
        return False

    qualification, eligible, reason, conflict = _pw2_expected_source_values(
        auction_mode=trusted_mode,
        owner_status=owner_status,
        source_generation=_POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
    )
    if conflict:
        return False
    rec.update(
        {
            "warehouse_source_schema": _PW2_SCHEMA,
            "warehouse_source_qualification": qualification,
            "warehouse_table_status": "verified",
            "warehouse_source_reason": reason,
            "warehouse_source_generation": (
                _POSTUPDATE_PLAYER_WAREHOUSE_GENERATION
            ),
            "warehouse_map_table_build": _PW2_TRUSTED_MAP_TABLE_BUILD,
            "warehouse_map_table_sha256": _PW2_TRUSTED_MAP_TABLE_SHA256,
            "warehouse_expected_map_table_sha256": (
                _PW2_TRUSTED_MAP_TABLE_SHA256
            ),
            "warehouse_map_source_contract_sha256": (
                _PW2_TRUSTED_SOURCE_CONTRACT_SHA256
            ),
            "warehouse_expected_map_source_contract_sha256": (
                _PW2_TRUSTED_SOURCE_CONTRACT_SHA256
            ),
            "warehouse_auction_mode": trusted_mode,
            "warehouse_source_conflict": conflict,
            "warehouse_quarantine_required": conflict,
            "official_distribution_eligible": eligible,
            "server_authority_reprojection": (
                "d66aee_legacy_full_map_contract"
            ),
            "server_authority_source_schema": _PW2_LEGACY_FULL_MAP_SCHEMA,
        }
    )
    return True


def _pw2_server_contract_valid(rec: dict) -> bool | None:
    """Validate a complete PW2 projection against server-owned authority.

    ``None`` means the row predates PW2 and follows the legacy source-routing
    contract.  Once any PW2 evidence field is present, incomplete or malformed
    projections fail closed.
    """

    if not (_PW2_TRIGGER_FIELDS & rec.keys()):
        return None
    if not _PW2_REQUIRED_FIELDS.issubset(rec):
        return False

    map_id = rec.get("map_id")
    if type(map_id) is not int:  # noqa: E721 - reject bool/coercive subclasses
        return False
    schema = rec.get("warehouse_source_schema")
    if type(schema) is not str:  # noqa: E721
        return False
    authority = _PW2_AUTHORITIES.get(schema)
    if authority is None:
        return False
    if schema in _PW2_EXACT_GENERATION_SCHEMAS:
        if (
            type(rec.get("game_version")) is not str  # noqa: E721
            or rec["game_version"] != authority["game_version"]
        ):
            return False
        if not _PW2_V5_EXTRA_FIELDS.issubset(rec):
            return False
        if (
            type(rec.get("warehouse_map_profile_generation")) is not str  # noqa: E721
            or rec["warehouse_map_profile_generation"]
            != authority["generation"]
            or type(rec.get("warehouse_game_version")) is not str  # noqa: E721
            or rec["warehouse_game_version"] != authority["game_version"]
        ):
            return False
    else:
        # Historical rows may predate the generic game-version field.  Preserve
        # those bytes, while refusing any newly observed explicit generation
        # (notably eb1/e8) that tries to borrow a v3/v4 schema.
        observed_game_version = rec.get("game_version")
        if observed_game_version is not None and (
            type(observed_game_version) is not str  # noqa: E721
            or observed_game_version != authority["game_version"]
        ):
            return False
        for field in _PW2_V5_EXTRA_FIELDS:
            value = rec.get(field)
            if value is not None and (
                type(value) is not str  # noqa: E721
                or value != authority["generation"]
            ):
                return False
    trusted_mode = authority["auction_mode_by_map"].get(map_id)
    if trusted_mode is None or map_id in authority["unopened_map_ids"]:
        return False

    exact_text_fields = {
        "warehouse_source_schema",
        "warehouse_source_qualification",
        "warehouse_map_name",
        "warehouse_map_name_source",
        "warehouse_table_status",
        "warehouse_source_reason",
        "warehouse_source_generation",
        "warehouse_map_table_build",
        "warehouse_map_table_sha256",
        "warehouse_expected_map_table_sha256",
        "warehouse_map_source_contract_sha256",
        "warehouse_expected_map_source_contract_sha256",
        "warehouse_auction_mode",
        "warehouse_owner_evidence_status",
    }
    if any(type(rec.get(key)) is not str for key in exact_text_fields):  # noqa: E721
        return False
    if not rec["warehouse_map_name"].strip():
        return False
    if rec["warehouse_map_name_source"] != "bid_map_table":
        return False
    if rec["warehouse_table_status"] != "verified":
        return False
    if rec["warehouse_map_table_build"] != authority["map_table_build"]:
        return False
    if (
        rec["warehouse_map_table_sha256"] != authority["map_table_sha256"]
        or rec["warehouse_expected_map_table_sha256"]
        != authority["map_table_sha256"]
    ):
        return False
    if (
        rec["warehouse_map_source_contract_sha256"]
        != authority["source_contract_sha256"]
        or rec["warehouse_expected_map_source_contract_sha256"]
        != authority["source_contract_sha256"]
    ):
        return False
    if rec["warehouse_auction_mode"] != trusted_mode:
        return False

    owner_status = rec["warehouse_owner_evidence_status"]
    client_generation = rec["warehouse_source_generation"]
    if owner_status not in _PW2_OWNER_EVIDENCE_STATUSES:
        return False
    if client_generation not in _PW2_SOURCE_GENERATIONS:
        return False
    received_ts, _persisted_received_ts = _normalize_pw2_server_received_at(
        rec.get("received_ts")
    )
    if (
        received_ts is None
        or received_ts < authority["min_server_epoch"]
    ):
        return False
    # This live ingest route is server-time post-update. Historical/pre-update
    # classification is allowed only through a separately audited migration
    # channel; a client claim cannot authorize it.
    server_generation = authority["generation"]
    if client_generation != server_generation:
        return False
    if any(
        type(rec.get(key)) is not bool  # noqa: E721 - reject 0/1
        for key in (
            "warehouse_source_conflict",
            "warehouse_quarantine_required",
            "official_distribution_eligible",
        )
    ):
        return False

    qualification, eligible, reason, conflict = _pw2_expected_source_values(
        auction_mode=trusted_mode,
        owner_status=owner_status,
        source_generation=server_generation,
    )
    return (
        rec["warehouse_source_qualification"] == qualification
        and rec["official_distribution_eligible"] is eligible
        and rec["warehouse_source_reason"] == reason
        and rec["warehouse_source_conflict"] is conflict
        and rec["warehouse_quarantine_required"] is conflict
    )


def _validated_current_settlement_category_fields(
    rec: dict,
    *,
    pw2_contract_valid: bool | None = None,
) -> dict | None:
    """Return one closed current-generation category fact, else no candidate.

    This fact is independent from official-distribution eligibility: a valid
    current settlement may remain player/mixed source or be quarantined while
    its deidentified category projection stays useful for later calibration.
    Invalid or partial category fields are stripped without changing the
    enclosing settlement's disposition.
    """

    all_category_fields = (
        _SETTLEMENT_CATEGORY_FIELDS | _SETTLEMENT_CATEGORY_OPTIONAL_FIELDS
    )
    present = {key for key in all_category_fields if key in rec}
    if not present:
        return None
    schema = rec.get("warehouse_source_schema")
    authority = _PW2_AUTHORITIES.get(schema)
    if authority is None or "category_authority_generation" not in authority:
        return None
    if (
        schema in _PW2_EXACT_GENERATION_SCHEMAS
        and pw2_contract_valid is not True
    ):
        return None
    expected_identity = authority.get("category_authority_identity")
    required = set(_SETTLEMENT_CATEGORY_FIELDS)
    if expected_identity is not None:
        required.add("category_authority_identity")
    if present != required:
        return None
    physical_map_id = rec.get("physical_map_id")
    model_map_id = rec.get("model_map_id")
    root_map_id = rec.get("map_id")
    warehouse_generation = rec.get("warehouse_generation")
    root_generation = rec.get("warehouse_source_generation")
    category_generation = rec.get("category_authority_generation")
    items = rec.get("settled_items_v1")
    if (
        type(root_map_id) is not int  # noqa: E721 - reject bool/subclasses
        or root_map_id <= 0
        or type(physical_map_id) is not int  # noqa: E721
        or physical_map_id != root_map_id
        or type(model_map_id) is not int  # noqa: E721
        or model_map_id <= 0
        or model_map_id
        != (
            physical_map_id - 2600
            if physical_map_id in _RAVEN_SETTLEMENT_PHYSICAL_MAP_IDS
            else physical_map_id
        )
        or type(warehouse_generation) is not str  # noqa: E721
        or warehouse_generation != authority["generation"]
        or type(root_generation) is not str  # noqa: E721
        or root_generation != warehouse_generation
        or type(category_generation) is not str  # noqa: E721
        or category_generation != authority["category_authority_generation"]
        or (
            expected_identity is not None
            and (
                type(rec.get("category_authority_identity")) is not str  # noqa: E721
                or rec["category_authority_identity"] != expected_identity
            )
        )
        or type(items) is not list  # noqa: E721
        or not 1 <= len(items) <= _MAX_SETTLEMENT_CATEGORY_ITEMS
    ):
        return None
    frozen_items: list[dict[str, int]] = []
    for row in items:
        if (
            type(row) is not dict  # noqa: E721
            or set(row) != {"item_id", "quality", "primary_category"}
            or type(row.get("item_id")) is not int  # noqa: E721
            or row["item_id"] <= 0
            or type(row.get("quality")) is not int  # noqa: E721
            or row["quality"] not in {1, 2, 3, 4, 5, 6}
            or type(row.get("primary_category")) is not int  # noqa: E721
            or row["primary_category"]
            not in authority["settlement_primary_categories"]
        ):
            return None
        frozen_items.append(
            {
                "item_id": row["item_id"],
                "quality": row["quality"],
                "primary_category": row["primary_category"],
            }
        )
    projected = {
        "physical_map_id": physical_map_id,
        "model_map_id": model_map_id,
        "warehouse_generation": warehouse_generation,
        "category_authority_generation": category_generation,
        "settled_items_v1": frozen_items,
    }
    if expected_identity is not None:
        projected["category_authority_identity"] = expected_identity
    return projected


def _ranked_unknown_source_can_promote(rec: dict) -> bool:
    """Whether a ranked row is unknown only because owner metadata is unavailable."""
    if not _ranked_warehouse_map(rec.get("map_id")):
        return False
    qualification, eligible = _warehouse_source_pair(rec)
    return (
        qualification == _POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE
        and eligible is False
    ) or (qualification is None and eligible is None)


def _mixed_source_pair_is_accepted(rec: dict) -> bool:
    """Accept a canonical non-ranked player/unknown source without relabelling it.

    Post-update non-ranked maps can contain player-submitted warehouses.  Until
    owner metadata lands, these complete settlements are useful mixed-source
    observations and belong in the normal big-data pool with an explicit map
    annotation.  Contradictory pairs remain fail-closed.
    """
    if _ranked_warehouse_map(rec.get("map_id")):
        return False
    return _warehouse_source_is_mixed(rec)


_PW2_CONTRACT_UNCHECKED = object()


def _normalize_warehouse_source(
    rec: dict,
    *,
    pw2_contract_valid: object = _PW2_CONTRACT_UNCHECKED,
) -> None:
    """Persist the server-owned source scope before routing a normal sample."""
    if pw2_contract_valid is _PW2_CONTRACT_UNCHECKED:
        pw2_contract_valid = _pw2_server_contract_valid(rec)
    if pw2_contract_valid is not None:
        # Full PW2 records are already validated against the server-owned
        # contract.  Do not rewrite them from client qualification fields.
        return
    if _ranked_unknown_source_can_promote(rec):
        rec["warehouse_source_qualification"] = _OFFICIAL_RANDOM_WAREHOUSE
        rec["official_distribution_eligible"] = True
        rec["warehouse_source_reason"] = "server_ranked_map_excludes_player_custom"
        return
    qualification, eligible = _warehouse_source_pair(rec)
    if (
        not _ranked_warehouse_map(rec.get("map_id"))
        and qualification is None
        and eligible is None
    ):
        rec["warehouse_source_qualification"] = (
            _POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE
        )
        rec["official_distribution_eligible"] = False
        rec["warehouse_source_reason"] = (
            "server_mixed_source_without_owner_metadata"
        )


def _warehouse_source_is_mixed(rec: dict) -> bool:
    qualification, eligible = _warehouse_source_pair(rec)
    return (
        qualification
        in {_POSTUPDATE_UNKNOWN_WAREHOUSE_SOURCE, _PLAYER_CUSTOM_WAREHOUSE}
        and eligible is False
    )


def _warehouse_source_requires_quarantine(
    rec: dict,
    *,
    pw2_contract_valid: object = _PW2_CONTRACT_UNCHECKED,
) -> bool:
    """Reject only contradictory source metadata, not useful mixed-source rows."""

    if _update606_unopened_map(rec):
        return True
    if pw2_contract_valid is _PW2_CONTRACT_UNCHECKED:
        pw2_contract_valid = _pw2_server_contract_valid(rec)
    if pw2_contract_valid is False:
        return True
    if pw2_contract_valid is True:
        return (
            rec["warehouse_source_conflict"] is True
            or rec["warehouse_quarantine_required"] is True
        )

    if (
        os.environ.get(
            "BIDKING_DISABLE_PLAYER_CUSTOM_WAREHOUSE_QUALIFICATION", ""
        ).strip()
        == "1"
    ):
        return False

    source_conflict = rec.get("warehouse_source_conflict")
    quarantine_required = rec.get("warehouse_quarantine_required")
    if source_conflict is not None or quarantine_required is not None:
        if source_conflict is not False or quarantine_required is not False:
            return True

    qualification, eligible = _warehouse_source_pair(rec)
    if _ranked_unknown_source_can_promote(rec):
        return False
    if qualification == _OFFICIAL_RANDOM_WAREHOUSE and eligible is True:
        return False
    if _mixed_source_pair_is_accepted(rec):
        return False
    if qualification is None and eligible is None:
        return False
    return True


def _is_abnormal(rec: dict) -> bool:
    """异常结算: 抓包不完整, 或无有效拍品价值。这种隔离留存、不进主分析库, 但不丢弃
    (可能是误判/可挽救)。"""
    return _quarantine_reason(rec) is not None


def _quarantine_reason(
    rec: dict,
    *,
    pw2_contract_valid: object = _PW2_CONTRACT_UNCHECKED,
) -> str | None:
    """Classify quarantine rows without exposing row-level identity or content."""
    if rec.get("settlement_incomplete"):
        return "settlement_incomplete"
    if rec.get("catalog_value_complete") is False:
        return "catalog_value_incomplete"
    if _declared_gt_captured(rec):
        return "declared_shortfall"
    if _update606_unopened_map(rec):
        return "update606_map_unopened"
    if _warehouse_source_requires_quarantine(
        rec,
        pw2_contract_valid=pw2_contract_valid,
    ):
        # Only malformed/contradictory source metadata reaches this branch.
        # Canonical player/unknown rows are retained in the normal mixed-source
        # pool and annotated by map in the public/private statistics surfaces.
        return "warehouse_source_pending"
    value = rec.get("known_value_sum")
    if not isinstance(value, (int, float)) or value <= 0:
        return "invalid_value"
    return None


def _build_quarantine_summary(path: Path, key: tuple[int, int]) -> dict:
    """Build one persisted-facts aggregate without retaining quarantine rows."""
    empty = {
        "total": 0,
        "warehouse_source_pending": 0,
        "update606_map_unopened": 0,
        "settlement_incomplete": 0,
        "catalog_value_incomplete": 0,
        "declared_shortfall": 0,
        "invalid_value": 0,
        "other": 0,
        "unreadable": 0,
        "bytes": key[1],
    }
    if key == (0, 0):
        return empty
    summary = dict(empty)
    snapshot_bytes = key[1]
    try:
        with path.open("rb") as stream:
            while stream.tell() < snapshot_bytes:
                raw = stream.readline()
                if stream.tell() > snapshot_bytes:
                    break
                if not raw.strip():
                    continue
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    summary["unreadable"] += 1
                    continue
                if not isinstance(rec, dict):
                    summary["unreadable"] += 1
                    continue
                summary["total"] += 1
                reason = _stored_quarantine_reason(rec)
                summary[reason if reason in summary else "other"] += 1
    except OSError:
        return empty
    return summary


def _quarantine_summary(*, refresh: bool = False) -> dict:
    """Return the shared TTL aggregate; explicit refresh remains single-flight.

    File growth does not invalidate the aggregate inside the TTL. This keeps
    authenticated /stats polling from turning a continuously appended quarantine
    file into repeated O(N) scans. The lock recheck also makes concurrent expiry or
    explicit refresh share the first completed rebuild.
    """
    now = time.monotonic()
    observed_value = _QUARANTINE_SUMMARY_CACHE.get("value")
    built_mono = float(_QUARANTINE_SUMMARY_CACHE.get("built_mono") or 0.0)
    if (
        observed_value is not None
        and not refresh
        and now - built_mono < QUARANTINE_SUMMARY_TTL
    ):
        return dict(observed_value)
    with _QUARANTINE_SUMMARY_LOCK:
        cached = _QUARANTINE_SUMMARY_CACHE.get("value")
        current_built_mono = float(
            _QUARANTINE_SUMMARY_CACHE.get("built_mono") or 0.0
        )
        now = time.monotonic()
        if cached is not None and (
            cached is not observed_value
            or (
                not refresh
                and now - current_built_mono < QUARANTINE_SUMMARY_TTL
            )
        ):
            return dict(cached)
        path = _quarantine_path()
        key = _quarantine_file_state()
        if _SAMPLE_HISTORY_INDEX is None:
            summary = _build_quarantine_summary(path, key)
        else:
            with _LOCK:
                _refresh_sample_history_index()
                indexed = _SAMPLE_HISTORY_INDEX.summary()
            summary = dict.fromkeys(("total", "warehouse_source_pending", "update606_map_unopened",
                "settlement_incomplete", "catalog_value_incomplete", "declared_shortfall", "invalid_value",
                "other", "unreadable"), 0)
            summary.update(indexed["quarantine"])
            summary["total"] = sum(n for reason, n in indexed["quarantine"].items() if reason != "unreadable")
            summary["bytes"] = indexed["quarantine_bytes"]
        _QUARANTINE_SUMMARY_CACHE.update({
            "key": key,
            "value": dict(summary),
            "built_mono": time.monotonic(),
        })
        return summary


_QUARANTINE_REASONS = frozenset({
    "warehouse_source_pending",
    "settlement_incomplete",
    "catalog_value_incomplete",
    "declared_shortfall",
    "invalid_value",
})


def _stored_quarantine_reason(rec: dict) -> str | None:
    """Classify persisted rows from persisted facts, independent of live rollback flags."""
    stored = rec.get("server_quarantine_reason")
    if stored in _QUARANTINE_REASONS:
        return stored
    if rec.get("settlement_incomplete"):
        return "settlement_incomplete"
    if rec.get("catalog_value_complete") is False:
        return "catalog_value_incomplete"
    if _declared_gt_captured(rec):
        return "declared_shortfall"
    source_conflict = rec.get("warehouse_source_conflict")
    quarantine_required = rec.get("warehouse_quarantine_required")
    if (
        (source_conflict is not None or quarantine_required is not None)
        and (
            source_conflict is not False
            or quarantine_required is not False
        )
    ):
        return "warehouse_source_pending"
    qualification = rec.get("warehouse_source_qualification")
    eligible = rec.get("official_distribution_eligible")
    if qualification is not None or eligible is not None:
        if qualification != "official_random_warehouse" or eligible is not True:
            return "warehouse_source_pending"
    value = rec.get("known_value_sum")
    if not isinstance(value, (int, float)) or value <= 0:
        return "invalid_value"
    return None


def _stats_payload() -> dict:
    """Expose the normal pool and keep official_total as a legacy API alias."""
    with _LOCK:
        _refresh_sample_history_index()
        normal_total = sum(_MAP_COUNT.values())
        by_map = dict(sorted(_MAP_COUNT.items()))
    return {
        "total": normal_total,
        "normal_total": normal_total,
        # Deprecated compatibility key. The normal pool may now include clearly
        # annotated player/unknown-source settlements on non-ranked maps.
        "official_total": normal_total,
        "by_map": by_map,
        "quarantine": _quarantine_summary(),
    }


# ❌【已证伪·别再实现】settlement_parse_residual > 0 作为完整性判据 —— 100% 假阳性。
#
# 想法是: 0x2D 帧顶层 protobuf walk 没走完(top_stop < len(body)) ⇒ 结算不完整。
# 它【不依赖游戏声明】, 所以看起来能补上"精确总件数基本只有 ahmed 有"造成的非-ahmed 盲区。
# 发布安全窗口 2026-07-12 一度实现并提交(350bcb1), 被审计窗口当天证伪、随即回退。
#
# 证伪(离线金标准 = fatbeans valid_ahmed, 34ea6f1 的 body-union 一件都没捞回 ⇒ 结算确实完整):
#     400 局里判据报阳 10 局 —— 真欠计 0, 【假阳性 10 = 100%】
#     residual 6 ~ 2,462 B, 而每一局 primary == merged(一件没丢)
# ⇒ **「walk 断了」≠「件数丢了」**: 残段里装的是别的结构, 不是藏品。
# ⚠️**阈值救不了**: 血色那帧的【真】欠计 residual = 139 B, 假阳性却高达 2,462 B ——
#   真阳性比假阳性还小, 没有任何阈值能把两者分开。
# ⚠️为什么在这里格外危险: 它要补的正是【非 ahmed 的盲区】, 而那些英雄没有任何交叉验证手段
#   (gabriela 声明覆盖 3.7% / raven 5.7%) ⇒ 误伤 ~2% 的健康样本【没有任何人会发现】。
#   这是把"盲区"升级成"主动丢数据", 比现状更糟。
#
# 发布安全窗口的方法论错误(值得记住): 只在【阳性侧】验了(离群机 11/17 报阳), 拿"6/17 能报 0"
# 当成了特异性证明。**"能报 clean" ≠ "报阳的都是真的"** —— 特异性必须在【已知阴性金标准】上验。
#
# 真正的方向(审计窗口): 判据应看【残段里有没有 item 形状的记录】(= body-union 能不能捞回件),
# 而不是残段有多长。但那个信号在 34ea6f1 发出去后会自愈(件已被捞回 = 结算完整), 再隔离又是误伤。
# ⇒ 剩下真正测不到的只有【字节丢失类】(楔死), 目前没有已验证的英雄无关信号。留 0.2.8,
#   且必须在一个【含已知好样本】的标注集上重新设计 + 验证。记忆: parse-residual-completeness-gate-false-positives


def _declared_gt_captured(rec: dict) -> bool:
    """服务端兜底: 游戏声明的【精确】总件数 > 实际抓到的件数 => 结算欠计。

    为什么服务端还要再判一次(客户端不是已经有 settlement_incomplete 了吗):
    客户端那道门【被收紧过】。`e04e259`(2026-06-23)的初版用的是【0.8 比例门】——
        inventory_count < declared * 0.8      # 少抓不到 20% 就【放行】
    后来才收紧成"缺一件就算"。但**群里大量客户端仍在跑老包**(实测欠计漏网局的版本:
    0.2.4-hotfix2 89 / 0.2.4-hotfix1 22 / 0.2.3-* 10),于是"少抓 1~11 件"的欠计样本
    带着 `settlement_incomplete=False` 混进了主库 —— 实测 **121 / 26,813 = 0.45%**,
    且这 121 条的 captured/declared 比例**全部落在 0.800~0.984**(= 恰好是老门放行的区间,
    一条例外都没有),证实它们是真欠计而非判据噪声。

    ⚠️ 判据必须用 `inventory_count`(实抓件数),**不能**用 `sum(final_quality_counts)` ——
    后者只统计【有品质】的藏品,品质解析不出的件不在里面,会制造假阳性(实测会多报 28 条)。

    两个字段都只在 `confidence=="exact"` 时才由客户端上传(见 sample_upload._extract_total_item_count),
    所以这里拿到的声明就是精确值;缺任一字段 => 判不了 => 不隔离(保守)。
    """
    declared = rec.get("total_item_count")
    captured = rec.get("inventory_count")
    if not isinstance(declared, int) or not isinstance(captured, int):
        return False
    return declared > 0 and captured < declared


def _append(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    # Original JSONL remains authority. A failed derived commit closes the data
    # plane; the same bytes are replayed from the old checkpoint on restart.
    _refresh_sample_history_index(publish_maps=False)


def _accept_one(
    rec: dict,
    *,
    upload_identity: UploadIdentity | None = None,
    body_sha256: str | None = None,
    identity_store=None,
    request_recovery: bool = False,
    request_claim_id: str | None = None,
) -> str:
    if not isinstance(rec, dict) or any(rec.get(k) in (None, "") for k in _REQUIRED):
        return "rejected"
    identity = upload_identity or UploadIdentity("legacy_unverified")
    if identity.trust not in {"legacy_unverified", "server_signed"}:
        return "rejected"
    h = rec.get("session_hash")
    clean = {k: rec[k] for k in _ALLOWED if k in rec}
    clean["quote_application"] = (
        _quote_preferences.validate_settlement_record(
            rec.get("quote_application", _quote_preferences.unknown("missing")), session_hash=h,
            settlement_cutoff=rec.get("client_ts"),
        ) if h else _quote_preferences.unknown("unavailable")
    )
    # Client-provided identity is never authority.  Compat traffic remains usable
    # for public aggregation, but it cannot enter the private identity index.
    clean["identity_trust"] = identity.trust
    if identity.trust == "server_signed":
        if (
            type(h) is not str
            or not h
            or type(body_sha256) is not str
            or identity_store is None
            or type(request_claim_id) is not str
        ):
            return "rejected"
        # uid_hash remains private in upload_identity.sqlite3.  Public/main,
        # quarantine, export and dashboard JSONL never receive the identity key.
    # 服务器盖收样时间(爆率时间轴权威)。必须【无条件覆盖】: received_ts 在 _ALLOWED 白名单里,
    # setdefault 会保留客户端自带的值 = 可伪造/回填(审计 0710 揪出), 与"服务器权威"设计矛盾。
    # PW2 上线边界必须先用同一次读取的原始 finite 时间判断；若先舍入到毫秒，
    # boundary-0.0004 会被抬到 boundary 并错误进入主库。资格判定完成后才生成落盘时间。
    server_received_at, persisted_received_at = _read_pw2_server_received_at()
    clean["received_ts"] = server_received_at
    with _LOCK:
        _refresh_sample_history_index()
        # Exact hotfix3/4 false-stale rows are upgraded before ordinary PW2
        # validation.  Near matches and genuine conflicts remain untouched.
        _reproject_exact_d66aee_legacy_pw2(clean)
        # Classify PW2 presence before the later fieldless-legacy normalization
        # adds source fields.  This preserves truly fieldless legacy rows
        # without letting partial PW2 claims downgrade to legacy.
        pw2_contract_valid = _pw2_server_contract_valid(clean)
        category_fields = _validated_current_settlement_category_fields(
            clean,
            pw2_contract_valid=pw2_contract_valid,
        )
        for key in (
            _SETTLEMENT_CATEGORY_FIELDS
            | _SETTLEMENT_CATEGORY_OPTIONAL_FIELDS
        ):
            clean.pop(key, None)
        if category_fields is not None:
            clean.update(category_fields)
        _normalize_warehouse_source(
            clean,
            pw2_contract_valid=pw2_contract_valid,
        )
        quarantine_reason = _quarantine_reason(
            clean,
            pw2_contract_valid=pw2_contract_valid,
        )
        clean["received_ts"] = persisted_received_at
        if quarantine_reason is not None:
            intended_disposition = "duplicate" if h and _history_contains("quarantine", h) else "quarantined"
        else:
            intended_disposition = "duplicate" if h and _history_contains("main", h) else "accepted"
        if identity.trust == "server_signed":
            reservation = identity_store.prepare_identity(
                session_hash=h,
                subject_kind=identity.subject_kind,
                uid_hash=identity.uid_hash,
                subject_id=identity.subject_id,
                device_alias=identity.device_alias,
                security_epoch=identity.security_epoch,
                received_ts=server_received_at,
                intended_disposition=intended_disposition,
                body_sha256=body_sha256,
                lease_id=identity.lease_id,
                nonce=identity.request_nonce,
                claim_id=request_claim_id,
            )
            if request_recovery and reservation.state == "existing":
                recovered = reservation.intended_disposition
                if recovered in {"accepted", "quarantined", "duplicate"}:
                    return recovered
                raise upload_identity_store.UploadIdentityStateError(
                    "identity recovery disposition invalid"
                )
            if (
                quarantine_reason is None
                and reservation.intended_disposition == "quarantined"
            ):
                quarantine_reason = "identity_conflict"
                intended_disposition = "quarantined"

            def finalize_identity(
                disposition: str,
                *,
                append_action=None,
            ) -> None:
                identity_store.finalize_identity(
                    session_hash=h,
                    subject_id=identity.subject_id,
                    body_sha256=body_sha256,
                    lease_id=identity.lease_id,
                    nonce=identity.request_nonce,
                    disposition=disposition,
                    claim_id=request_claim_id,
                    append_action=append_action,
                )
        if quarantine_reason is not None:
            if h and _history_contains("quarantine", h):
                if (
                    identity.trust == "server_signed"
                    and reservation.state in {"prepared", "resumed"}
                ):
                    finalize_identity(reservation.intended_disposition)
                return "duplicate"                      # 同一异常样本已隔离, 不重复写
            # 异常结算 -> 隔离文件 samples_quarantine.jsonl(留存待查, 不进主库, 不丢弃)。
            # session_hash 进【独立】_QUAR_SEEN(不进 _SEEN)→ 该 session 后来补传成【有效】样本时
            # L134 不判重复, 仍能进主库救回(修复"异常去重致重传永久失效")。
            quarantine_record = {**clean, "server_quarantine_reason": quarantine_reason}
            if identity.trust == "server_signed":

                def append_quarantine() -> None:
                    _append(_quarantine_path(), quarantine_record)
                    if h and _SAMPLE_HISTORY_INDEX is None:
                        _QUAR_SEEN.add(h)

                finalize_identity(
                    "quarantined",
                    append_action=append_quarantine,
                )
            else:
                _append(_quarantine_path(), quarantine_record)
                if h and _SAMPLE_HISTORY_INDEX is None:
                    _QUAR_SEEN.add(h)
            return "quarantined"
        if h and _history_contains("main", h):
            if (
                identity.trust == "server_signed"
                and reservation.state in {"prepared", "resumed"}
            ):
                finalize_identity(reservation.intended_disposition)
            return "duplicate"
        if identity.trust == "server_signed":

            def append_accepted() -> None:
                _append(_OUT, clean)
                if h and _SAMPLE_HISTORY_INDEX is None:
                    _SEEN.add(h)
                if clean.get("map_id") is not None:
                    _MAP_COUNT[clean["map_id"]] += 1

            finalize_identity(
                "accepted",
                append_action=append_accepted,
            )
        else:
            _append(_OUT, clean)
            if h and _SAMPLE_HISTORY_INDEX is None:
                _SEEN.add(h)
            if clean.get("map_id") is not None:
                _MAP_COUNT[clean["map_id"]] += 1
    return "accepted"


class SafeThreadingHTTPServer(ThreadingHTTPServer):
    """Keep unexpected handler failures observable without leaking peer details."""

    def handle_error(self, request, client_address) -> None:
        print("[http-handler-error] category=unexpected_handler_error", flush=True)


class Handler(BaseHTTPRequestHandler):
    def end_headers(self) -> None:
        """🔒 每一个响应都带 X-Robots-Tag(第 1 层防索引)。

        放在 end_headers 而不是各个 send_header 处: 本 server 只有这一个 Handler 类, 而每条响应路径
        (含 304 / 401 / 302 / 静态 / JSON)最后都会走到这里 ⇒ 【一个路由都漏不掉】, 以后新增路由也自动带上。
        逐处去加 = 早晚漏一个, 而漏掉的那个恰恰可能是被爬到的那个。

        为什么要防: 作者定 /public 是【群内自用】, 后续收成"正式用户才能访问"。但它现在【免登录、公网可达】,
        "不要外传"只是口头请求, 没有技术保障。而搜索引擎的索引【收不回来】—— 就算之后加了 token 门,
        快照与摘要仍会留在结果页里。所以必须在链接扩散【之前】挡住爬虫。
        (X-Robots-Tag 对 /export /stats 这类非 HTML 响应也生效, 是 <meta> 做不到的。)
        """
        self.send_header("X-Robots-Tag", "noindex, nofollow, noarchive, nosnippet")
        super().end_headers()

    def _write_response_body(self, body: bytes) -> bool:
        """Write one buffered response without retrying after a client disconnect.

        Dashboard/public aggregation can finish after the browser has navigated away.
        A failed body write is not a server-side render failure, and attempting to
        send a second JSON 500 on the same closed socket only creates another
        ``BrokenPipeError``. Streaming exports keep their separate explicit loop.
        """
        try:
            self.wfile.write(body)
            return True
        except OSError as exc:
            if _is_client_disconnect_error(exc):
                self.close_connection = True
                return False
            raise

    def _send(
        self,
        code: int,
        payload: dict,
        *,
        upload_failure: str | None = None,
    ) -> bool:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            if upload_failure in _UPLOAD_FAILURE_CLASSES:
                self.send_header("X-BidKing-Upload-Status", upload_failure)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
        except OSError as exc:
            if _is_client_disconnect_error(exc):
                self.close_connection = True
                return False
            raise
        return self._write_response_body(body)

    def _send_activation_probe(
        self,
        route: str,
        code: int,
        payload: dict,
        *,
        started: float,
    ) -> bool:
        response_write = "failed"
        try:
            delivered = self._send(code, payload)
            response_write = "ok" if delivered else "broken_pipe"
            return delivered
        finally:
            _record_activation_probe_metric(
                route,
                code,
                (time.monotonic() - started) * 1000.0,
                response_write=response_write,
            )

    def _send_activation_response(
        self,
        code: int,
        payload: dict,
        *,
        started: float,
        mode_hint: str = "none",
        reason_hint: str = "other",
    ) -> bool:
        """Send and record one /activate response exactly once after write outcome."""
        response_write = "failed"
        try:
            delivered = self._send(code, payload)
            response_write = "ok" if delivered else "broken_pipe"
            return delivered
        finally:
            projection = _activation_metric_projection(
                payload,
                code=code,
                mode_hint=mode_hint,
                reason_hint=reason_hint,
            )
            _record_activation_metric(
                code,
                payload,
                (time.monotonic() - started) * 1000.0,
                response_write=response_write,
                _projection=projection,
            )

    def _send_activation_e2ee_response(
        self,
        envelope: bytes,
        *,
        decision_code: int,
        decision_payload: dict,
        started: float,
        mode_hint: str,
    ) -> bool:
        """Send ciphertext while recording only the existing fixed decision metric."""
        response_write = "failed"
        try:
            try:
                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    "application/vnd.bidking.activation-e2ee+json",
                )
                self.send_header("Cache-Control", "no-store")
                self.send_header("Pragma", "no-cache")
                self.send_header("Content-Length", str(len(envelope)))
                self.end_headers()
            except OSError as exc:
                if not _is_client_disconnect_error(exc):
                    raise
                self.close_connection = True
                delivered = False
            else:
                delivered = self._write_response_body(envelope)
            response_write = "ok" if delivered else "broken_pipe"
            return delivered
        finally:
            projection = _activation_metric_projection(
                decision_payload,
                code=decision_code,
                mode_hint=mode_hint,
            )
            _record_activation_metric(
                decision_code,
                decision_payload,
                (time.monotonic() - started) * 1000.0,
                response_write=response_write,
                _projection=projection,
            )

    def _send_upload_response(
        self,
        code: int,
        payload: dict,
        *,
        started: float,
        identity: str = "none",
        reason: str = "other",
        records: dict | None = None,
        upload_failure: str | None = None,
    ) -> bool:
        """Send one /ingest response and record only fixed aggregate fields."""
        response_write = "failed"
        try:
            delivered = (
                self._send(code, payload)
                if upload_failure is None
                else self._send(code, payload, upload_failure=upload_failure)
            )
            response_write = "ok" if delivered else "broken_pipe"
            return delivered
        finally:
            _record_upload_identity_metric(
                code,
                (time.monotonic() - started) * 1000.0,
                identity=identity,
                reason=reason,
                response_write=response_write,
                records=records,
            )

    def _send_heavy_busy(self) -> None:
        body = json.dumps({"ok": False, "error": "heavy operation busy"}).encode("utf-8")
        self.send_response(503)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Retry-After", str(HEAVY_RETRY_AFTER_SECONDS))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self._write_response_body(body)

    def _send_history_index_unavailable(self) -> None:
        state = _history_index_state()
        body = json.dumps({
            "ok": False,
            "error": "history index unavailable",
            "state": state,
        }).encode("utf-8")
        self.send_response(503)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Retry-After", str(_HISTORY_INDEX_RETRY_AFTER_SECONDS))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self._write_response_body(body)

    def log_message(self, *a):  # 静音默认日志
        pass

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self._write_response_body(body)

    def _dash_authed(self) -> bool:
        """/dashboard + /stats 访问鉴权。365/历史永久 cookie 或运维 Basic Auth 均可放行。

        配了 _DASH_PASS -> 运维仍可用 HTTP Basic(口令匹配, 用户名随意)。
        没配口令: 公网绑定(0.0.0.0 等)=【拒绝】(fail-closed·防外人裸看全库·与 /export 一致);
        loopback 绑定(127.0.0.1)=放行(本地 dev 便利)。=_DASH_ALLOW_ANON 在 main() 按 --host 定。"""
        if _dash_read_cookie(self._cookie(_DASH_COOKIE)):
            return True
        if not _DASH_PASS:
            return _DASH_ALLOW_ANON
        h = self.headers.get("Authorization", "")
        if h.startswith("Basic "):
            try:
                dec = base64.b64decode(h[6:]).decode("utf-8", "ignore")
                return hmac.compare_digest(dec.split(":", 1)[-1], _DASH_PASS)  # 常量时间, 同 /ingest 签名比较
            except Exception:  # noqa: BLE001
                return False
        return False

    def _require_dash_auth(self) -> bool:
        if self._dash_authed():
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="BidKing Dashboard"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def _cookie(self, name: str) -> str:
        """从 Cookie 头里取一个值(不引 http.cookies: 我们只需要读一个自己签的值)。"""
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return ""

    def _client_ip(self) -> str:
        """返回限流/诊断用的真实客户端 IP；只信任来自本机 Caddy 的代理头。"""
        return _effective_client_ip(
            self.client_address[0], self.headers.get("X-Forwarded-For")
        )

    def _send_html(self, code: int, html: str, *, set_cookie: str | None = None):
        body = html.encode("utf-8")
        use_gz = "gzip" in (self.headers.get("Accept-Encoding") or "").lower()
        if use_gz:
            body = gzip.compress(body, 6)
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        if use_gz:
            self.send_header("Content-Encoding", "gzip")
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        # 个人页绝不能被中间层/浏览器缓存(下一个用户可能在同一台机器上打开)
        self.send_header("Cache-Control", "no-store, private")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self._write_response_body(body)

    def do_GET(self):
        # 健康检查 + 看板自检(build 空看板抓 import 不一致致 500): 不鉴权不限流, 给 update.sh 用。
        # 自检结果缓存 30s: 该端点在限流之前, 不缓存 = 公网免费 CPU 燃烧点(每次真建一遍空看板)。
        if self.path == "/healthz":
            started = time.monotonic()
            if not _history_index_ready():
                state = _history_index_state()
                return self._send_activation_probe(
                    "healthz",
                    503,
                    {"ok": False, "reason": "history_index_unavailable", "state": state},
                    started=started,
                )
            now = time.time()
            if _HEALTH_CACHE["payload"] is not None and now - _HEALTH_CACHE["ts"] < 30.0:
                return self._send_activation_probe(
                    "healthz",
                    _HEALTH_CACHE["code"],
                    _HEALTH_CACHE["payload"],
                    started=started,
                )
            try:
                import sample_dashboard  # 同目录
                sample_dashboard.build_dashboard_html([])
                code, payload = 200, {"ok": True}
            except Exception as e:  # noqa: BLE001
                code, payload = 500, {"ok": False, "error": f"{type(e).__name__}: {e}"}
            _HEALTH_CACHE.update({"ts": now, "code": code, "payload": payload})
            return self._send_activation_probe(
                "healthz",
                code,
                payload,
                started=started,
            )
        # 无凭据、无状态写入的激活 readiness。不能用虚构设备或 token 探测生产
        # /activate；这里只证明 ActivationService 已构造且 server/core 接口兼容。
        if self.path == "/activation-readiness":
            started = time.monotonic()
            import activation_server

            code, payload = activation_server.activation_readiness(_ACT)
            return self._send_activation_probe(
                "activation_readiness",
                code,
                payload,
                started=started,
            )
        # 🔒 robots.txt(第 2 层防索引): 全站 Disallow。放在限流【之前】——
        # 爬虫被 429 挡住时【不会】把它当成"禁止抓取", 反而可能按"临时故障"稍后重试整站。
        # 这是爬虫的第一站, 必须永远拿得到确定的答案。
        if self.path == "/robots.txt":
            body = b"User-agent: *\nDisallow: /\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        if not _rate_ok(self._client_ip()):
            return self._send(429, {"error": "rate limited"})
        # 版本清单: 公开(客户端更新前就要能查)、限流、免鉴权。客户端据此比对版本/提示更新。
        if self.path == "/version":
            return self._send(200, _public_version_payload())
        # 公共大数据页(0.2.7): 【公开·免鉴权】—— 这是给群友的回馈页(你传数据 → 你拿到更准的表),
        # 也是让更多人愿意开上传的理由。只出【聚合数字】, 绝无昵称/uid/session(隐私红线由
        # public_stats.aggregate_public 保证 + tests/test_public_stats.py 硬断言守着)。
        # 已过限流; 走 _public_bodies 缓存(免鉴权端点不缓存 = 现成的 DoS 面)。
        if self.path in ("/public", "/public/"):
            if not _history_index_ready():
                return self._send_history_index_unavailable()
            try:
                plain, gz, etag = _public_bodies(_recent_window(PUBLIC_DEFAULT_DAYS))
                if etag and self.headers.get("If-None-Match") == etag:
                    self.send_response(304)
                    self.send_header("ETag", etag)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                use_gz = "gzip" in (self.headers.get("Accept-Encoding") or "").lower()
                body = gz if use_gz else plain
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                if use_gz:
                    self.send_header("Content-Encoding", "gzip")
                if etag:
                    self.send_header("ETag", etag)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self._write_response_body(body)
            except HeavyOperationBusy:
                return self._send_heavy_busy()
            except Exception as e:  # noqa: BLE001 - 公开页绝不能把服务搞挂
                return self._send(500, {"error": f"{type(e).__name__}"})
            return
        # 完整私有大数据入口每次都回到 token 确认页；验证后 /dashboard 内部仍走签名 cookie，
        # 这样筛选/模块懒加载无需反复携带 token，而再次点击顶栏“私有大数据”会明确重新确认。
        if self.path.split("?")[0] in ("/private", "/private/"):
            import web_ui
            return self._send_html(
                200,
                web_ui.private_dashboard_login_html(),
                set_cookie=f"{_DASH_COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax",
            )
        # 个人对局记录(0.2.7 · uid-scope): 必须 token 登录, 只看得到自己的数据。
        # 【近实时】—— 不走 /public 的 30 分钟节流；5 秒短缓存吸收连续刷新，避免反复扫完整主库。
        if self.path.split("?")[0] in ("/my", "/my/"):
            import my_records
            # 入口页始终要求用户确认；浏览器密码管理器可自动填充，但旧 cookie 不得自动放行。
            return self._send_html(
                200,
                my_records.build_login_html(),
                set_cookie=f"{_MY_COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax",
            )
        if self.path.split("?")[0] in ("/my/view", "/my/view/"):
            import my_records
            server_now = int(time.time())
            uid_hash = _my_read_cookie(self._cookie(_MY_COOKIE), now=server_now)
            if not uid_hash:
                self.send_response(302)
                self.send_header("Location", "/my")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if not _my_view_rate_ok(uid_hash):
                return self._send(429, {"error": "personal records refresh rate limited"})
            if not _history_index_ready():
                return self._send_history_index_unavailable()
            try:
                return self._send_html(
                    200, _my_page_html(uid_hash, window=_recent_window(DASH_DEFAULT_DAYS)),
                )
            except HeavyOperationBusy:
                return self._send_heavy_busy()
        if self.path == "/stats":
            if not self._require_dash_auth():
                return
            if not _history_index_ready():
                return self._send_history_index_unavailable()
            return self._send(200, _stats_payload())
        # 一键导出原始样本(独立 export key 保护; 客户端只有 ingest key 故拖不走库):
        #   curl -H "X-Api-Key: <EXPORT_KEY>" .../export -o samples.jsonl
        if self.path == "/export":
            # #4 fail-closed: 无独立 export key 则【拒绝导出】, 【绝不回退 ingest key】——
            # ingest key 随包下发给每个客户端(解 upload_config.enc 即得), 回退=任一群友可拖全库。
            if not _EXPORT_KEY:
                return self._send(403, {"error": "export disabled (set export_key.txt)"})
            if self.headers.get("X-Api-Key") != _EXPORT_KEY:
                return self._send(401, {"error": "bad key"})
            if not _history_index_ready():
                return self._send_history_index_unavailable()
            if not _OUT.exists():
                return self._send(404, {"error": "no data yet"})
            # 客户端带 Accept-Encoding: gzip(curl --compressed)→ 压缩后传(全库 ~85MB → ~10MB,
            # 大 export 不再需要超长超时); 不带则维持原始 ndjson 行为(向后兼容旧拉库脚本)。
            use_gz = "gzip" in (self.headers.get("Accept-Encoding") or "").lower()
            try:
                # 固定本次导出的文件长度，随后分块读取；新 ingest 可继续 append，但不进入本次快照。
                with _serialized_heavy_operation("export"):
                    with _LOCK:
                        snapshot_bytes = _OUT.stat().st_size
                        stream = _OUT.open("rb")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                    self.send_header("Content-Disposition", "attachment; filename=samples.jsonl")
                    if use_gz:
                        self.send_header("Content-Encoding", "gzip")
                        self.send_header("Connection", "close")
                        self.close_connection = True
                    else:
                        self.send_header("Content-Length", str(snapshot_bytes))
                    self.end_headers()
                    remaining = snapshot_bytes
                    with stream:
                        if use_gz:
                            with gzip.GzipFile(filename="samples.jsonl", mode="wb", compresslevel=6,
                                               fileobj=self.wfile, mtime=0) as zipped:
                                while remaining:
                                    chunk = stream.read(min(256 << 10, remaining))
                                    if not chunk:
                                        break
                                    zipped.write(chunk)
                                    remaining -= len(chunk)
                        else:
                            while remaining:
                                chunk = stream.read(min(256 << 10, remaining))
                                if not chunk:
                                    break
                                self.wfile.write(chunk)
                                remaining -= len(chunk)
            except HeavyOperationBusy:
                return self._send_heavy_busy()
            return
        # 私有看板重字段按模块懒加载；与 /dashboard 共用鉴权，但不把 token 放进 URL。
        if self.path.split("?", 1)[0] == "/dashboard-data":
            if not self._require_dash_auth():
                return
            if not _history_index_ready():
                return self._send_history_index_unavailable()
            query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
            kind = (query.get("kind") or [""])[0]
            if kind not in {"rare", "tools", "player_ranks"}:
                return self._send(400, {"error": "invalid dashboard detail kind"})
            try:
                window = _parse_dashboard_window(self.path, allow_kind=True)
                plain, gz, etag = _dashboard_detail_bodies(kind, window)
                if etag and self.headers.get("If-None-Match") == etag:
                    self.send_response(304)
                    self.send_header("ETag", etag)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                use_gz = "gzip" in (self.headers.get("Accept-Encoding") or "").lower()
                body = gz if use_gz else plain
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                if use_gz:
                    self.send_header("Content-Encoding", "gzip")
                if etag:
                    self.send_header("ETag", etag)
                    self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self._write_response_body(body)
                return
            except InvalidDashboardWindow as e:
                return self._send(400, {"error": str(e)})
            except HeavyOperationBusy:
                return self._send_heavy_busy()
            except Exception as e:  # noqa: BLE001
                return self._send(500, {"error": f"dashboard detail: {type(e).__name__}: {e}"})
        # 分析看板: 动态读当前落盘样本生成自包含 HTML(_DASH_PASS 设了则要 Basic Auth)。
        if urlsplit(self.path).path in ("/", "/dashboard"):
            if not self._require_dash_auth():
                return
            if not _history_index_ready():
                return self._send_history_index_unavailable()
            try:
                window = _parse_dashboard_window(self.path)
                plain, gz, etag = _dashboard_bodies(window)  # 缓存: 数据没变时零重算
                # 条件请求: 数据没变(etag 同) → 304 零字节(作者反复刷新时省 ~6.5MB/次传输)。
                if etag and self.headers.get("If-None-Match") == etag:
                    self.send_response(304)
                    self.send_header("ETag", etag)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                use_gz = "gzip" in (self.headers.get("Accept-Encoding") or "").lower()
                body = gz if use_gz else plain
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                if use_gz:
                    self.send_header("Content-Encoding", "gzip")
                if etag:
                    self.send_header("ETag", etag)
                    self.send_header("Cache-Control", "no-cache")  # 每次先问服务器, 命中 etag 才用本地缓存
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self._write_response_body(body)
                return
            except InvalidDashboardWindow as e:
                return self._send(400, {"error": str(e)})
            except HeavyOperationBusy:
                return self._send_heavy_busy()
            except Exception as e:  # noqa: BLE001
                return self._send(500, {"error": f"dashboard: {type(e).__name__}: {e}"})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path == activation_http_e2ee.ROUTE:
            return self._do_activation_http_e2ee()
        if self.path in ("/activate", "/telemetry"):
            return self._do_activation(self.path)
        if self.path in ("/private", "/private/"):
            if not _rate_ok(self._client_ip()):
                return self._send(429, {"error": "rate limited"})
            import web_ui
            try:
                n = _request_content_length(self.headers)
            except ValueError:
                return self._send(400, {"error": "bad content-length"})
            if n > 8192:
                return self._send(413, {"error": "payload too large"})
            raw = self.rfile.read(n) if n else b""
            from urllib.parse import parse_qs
            try:
                token = (parse_qs(raw.decode("utf-8", "replace")).get("token") or [""])[0]
            except Exception:  # noqa: BLE001
                token = ""
            uid_hash, err, membership_exp = _dashboard_uid_hash_from_token(token)
            if not uid_hash:
                return self._send_html(200, web_ui.private_dashboard_login_html(err or "权限验证失败"))
            cookie_value, max_age = _dash_make_cookie(uid_hash, membership_exp)
            secure = "; Secure" if (self.headers.get("X-Forwarded-Proto") == "https") else ""
            cookie = (f"{_DASH_COOKIE}={cookie_value}; Max-Age={max_age}; "
                      f"Path=/; HttpOnly; SameSite=Lax{secure}")
            self.send_response(302)
            self.send_header("Location", "/dashboard")
            self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        # /my 登录: token 走【表单 POST】(绝不进 URL —— URL 会落进浏览器历史/Referer/caddy 访问日志,
        # 而 token 是能激活软件的凭证)。验完只把【签名过的 uid_hash】写进 cookie, token 本身不留。
        if self.path in ("/my", "/my/"):
            if not _rate_ok(self._client_ip()):          # 防爆破 token
                return self._send(429, {"error": "rate limited"})
            import my_records
            try:
                n = _request_content_length(self.headers)
            except ValueError:
                return self._send(400, {"error": "bad content-length"})
            if n > 8192:                                  # 登录表单不可能这么大
                return self._send(413, {"error": "payload too large"})
            raw = self.rfile.read(n) if n else b""
            from urllib.parse import parse_qs
            token = ""
            try:
                fields = parse_qs(raw.decode("utf-8", "replace"))
                token = (fields.get("token") or [""])[0]
            except Exception:  # noqa: BLE001
                token = ""
            server_now = int(time.time())
            uid_hash, err, membership_exp = _my_uid_hash_from_token(token, now=server_now)
            if not uid_hash:
                return self._send_html(200, my_records.build_login_html(err or "登录失败"))
            secure = "; Secure" if (self.headers.get("X-Forwarded-Proto") == "https") else ""
            max_age = _MY_TTL
            if membership_exp is not None:
                max_age = min(max_age, int(membership_exp) - server_now)
                if max_age <= 0:
                    return self._send_html(200, my_records.build_login_html("订阅已到期"))
            cookie_value = _my_make_cookie(uid_hash, membership_exp, now=server_now)
            cookie = (f"{_MY_COOKIE}={cookie_value}; Max-Age={max_age}; "
                      f"Path=/; HttpOnly; SameSite=Lax{secure}")
            # 302 回独立结果页: POST-Redirect-GET 避免刷新重提 token；重新点击 /my 仍会清 cookie 并确认。
            self.send_response(302)
            self.send_header("Location", "/my/view")
            self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path != "/ingest":
            return self._send(404, {"error": "not found"})
        upload_started = time.monotonic()
        metric_identity = "none"

        def send_upload(
            code: int,
            payload: dict,
            *,
            reason: str,
            records: dict | None = None,
            upload_failure: str | None = None,
            identity_override: str | None = None,
        ) -> bool:
            return self._send_upload_response(
                code,
                payload,
                started=upload_started,
                identity=(
                    metric_identity
                    if identity_override is None
                    else identity_override
                ),
                reason=reason,
                records=records,
                upload_failure=upload_failure,
            )

        if not _rate_ok(self._client_ip()):
            return send_upload(
                429,
                {"error": "rate limited"},
                reason="rate_limited",
            )
        try:
            n = _request_content_length(self.headers)
        except ValueError:
            return send_upload(
                400,
                {"error": "bad content-length"},
                reason="bad_content_length",
            )
        if n > _MAX_BODY:   # 防超大请求体撑爆内存(单次上传的样本批远小于此; 1 MiB 很宽松)
            return send_upload(
                413,
                {"error": "payload too large", "max": _MAX_BODY},
                reason="payload_too_large",
            )
        raw = self.rfile.read(n) if n else b""            # 先读 body: HMAC 要对原始字节验签
        if not _auth_ok(self.headers, raw):               # HMAC 签名(推荐) 或 X-Api-Key(legacy)
            return send_upload(401, {"error": "bad auth"}, reason="bad_auth")
        if not _history_index_ready():
            return send_upload(
                503,
                {"error": "history_index_unavailable", "state": _history_index_state()},
                reason="other",
            )
        identity_decision = _authorize_upload_identity(
            self.headers,
            raw,
            mode=_UPLOAD_IDENTITY_MODE,
            service=_ACT,
        )
        if identity_decision.http_code != 200 or identity_decision.identity is None:
            return send_upload(
                identity_decision.http_code,
                {"error": identity_decision.reason},
                reason=_upload_metric_reason(identity_decision.reason),
                upload_failure=_public_upload_failure(identity_decision.reason),
            )
        identity = identity_decision.identity
        metric_identity = _upload_metric_identity_from_authority(identity)
        body_sha256 = hashlib.sha256(raw).hexdigest()
        request_recovery = False
        request_claim_id = None
        if identity.trust == "server_signed":
            if _UPLOAD_IDENTITY_STORE is None:
                return send_upload(
                    503,
                    {"error": "identity_state_error"},
                    reason="identity_state_error",
                    upload_failure="identity_state_error",
                )
            reservation_now = _read_upload_identity_server_now()
            if reservation_now is None:
                return send_upload(
                    503,
                    {"error": "identity_state_error"},
                    reason="identity_state_error",
                    upload_failure="identity_state_error",
                )
            try:
                reservation = _UPLOAD_IDENTITY_STORE.reserve_request(
                    lease_id=identity.lease_id,
                    nonce=identity.request_nonce,
                    body_sha256=body_sha256,
                    expires_at=identity.lease_exp,
                    now=reservation_now,
                )
            except upload_identity_store.UploadIdentityStateError:
                return send_upload(
                    503,
                    {"error": "identity_state_error"},
                    reason="identity_state_error",
                    upload_failure="identity_state_error",
                )
            if reservation.state == "replay_rejected":
                return send_upload(
                    409,
                    {"error": "identity_replay"},
                    reason="identity_replay",
                    upload_failure="request_replay",
                )
            if reservation.state == "in_flight":
                return send_upload(
                    409,
                    {"error": "identity_in_flight"},
                    reason="identity_in_flight",
                    upload_failure="request_in_flight",
                )
            if reservation.state == "replayed" and reservation.response is not None:
                return send_upload(
                    200,
                    reservation.response,
                    reason="request_replayed",
                )
            request_recovery = reservation.state == "resumed"
            request_claim_id = reservation.claim_id
            if type(request_claim_id) is not str:
                return send_upload(
                    503,
                    {"error": "identity_state_error"},
                    reason="identity_state_error",
                    upload_failure="identity_state_error",
                )
        try:
            data = json.loads(raw.decode("utf-8")) if raw else None
        except Exception as e:  # noqa: BLE001
            return send_upload(
                400,
                {"error": f"bad json: {type(e).__name__}"},
                reason="bad_json",
                identity_override="none",
            )
        recs = data if isinstance(data, list) else [data]
        response = None
        try:
            res = Counter(
                _accept_one(
                    r,
                    upload_identity=identity,
                    body_sha256=body_sha256,
                    identity_store=_UPLOAD_IDENTITY_STORE,
                    request_recovery=request_recovery,
                    request_claim_id=request_claim_id,
                )
                for r in recs
            )
            response = {"accepted": res["accepted"], "duplicate": res["duplicate"],
                        "quarantined": res["quarantined"], "rejected": res["rejected"]}
            if identity.trust == "server_signed":
                _UPLOAD_IDENTITY_STORE.finalize_request(
                    lease_id=identity.lease_id,
                    nonce=identity.request_nonce,
                    body_sha256=body_sha256,
                    response=response,
                    claim_id=request_claim_id,
                )
        except upload_identity_store.UploadIdentityStateError:
            return send_upload(
                503,
                {"error": "identity_state_error"},
                reason="identity_state_error",
                records=response,
                upload_failure="identity_state_error",
            )
        except CollectorHistoryUnavailable:
            return send_upload(503, {"error": "history_index_unavailable", "state": "failed"}, reason="other", records=response)
        return send_upload(200, response, reason="ok", records=response)

    def _do_activation(self, path: str):
        # /activate(token+device -> lease+主密钥) 与 /telemetry(C 上报)。token 本身是 /activate 的鉴权,
        # 故不要求 ingest HMAC; 限流防刷。未配 activation_key -> 503。
        is_activation = path == "/activate"
        started = time.monotonic() if is_activation else 0.0

        def send_result(
            code: int,
            payload: dict,
            *,
            mode_hint: str = "none",
            reason_hint: str = "other",
        ) -> bool:
            if is_activation:
                return self._send_activation_response(
                    code,
                    payload,
                    started=started,
                    mode_hint=mode_hint,
                    reason_hint=reason_hint,
                )
            return self._send(code, payload)

        if not _rate_ok(self._client_ip()):
            return send_result(
                429,
                {"error": "rate limited"},
                reason_hint="rate_limited",
            )
        if _ACT is None:
            return send_result(
                503,
                {"error": "activation not configured"},
                reason_hint="activation_not_configured",
            )
        try:
            n = _request_content_length(self.headers)
        except ValueError:
            return send_result(
                400,
                {"error": "bad content-length"},
                reason_hint="bad_request",
            )
        if n > _MAX_BODY:
            return send_result(
                413,
                {"error": "payload too large"},
                reason_hint="bad_request",
            )
        raw = self.rfile.read(n) if n else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:  # noqa: BLE001
            return send_result(400, {"error": "bad json"}, reason_hint="bad_request")
        if not isinstance(body, dict):
            return send_result(400, {"error": "bad body"}, reason_hint="bad_request")
        if is_activation:
            token = body.get("token")
            mode_hint = "paid" if type(token) is str and bool(token) else "trial"
            code, resp = _handle_activation_body(body)
            return send_result(code, resp, mode_hint=mode_hint)
        else:
            code, resp = _ACT.handle_telemetry(body, self._client_ip())
        return send_result(code, resp)

    def _do_activation_http_e2ee(self):
        """Decrypt one emergency request and keep the authority response encrypted."""
        started = time.monotonic()
        if not _rate_ok(self._client_ip()):
            return self._send_activation_response(
                429,
                {"error": "rate limited"},
                started=started,
                reason_hint="rate_limited",
            )
        if _ACT is None or _ACTIVATION_HTTP_E2EE is None:
            return self._send_activation_response(
                503,
                {"error": "activation e2ee not configured"},
                started=started,
                reason_hint="activation_not_configured",
            )
        try:
            n = _request_content_length(self.headers)
        except ValueError:
            n = 0
        if n <= 0 or n > activation_http_e2ee.MAX_ENVELOPE_BYTES:
            return self._send_activation_response(
                400,
                {"error": "invalid envelope"},
                started=started,
                reason_hint="bad_request",
            )
        raw = self.rfile.read(n)
        try:
            body, context = _ACTIVATION_HTTP_E2EE.open_request(raw)
        except activation_http_e2ee.ActivationHttpE2EEError:
            return self._send_activation_response(
                400,
                {"error": "invalid envelope"},
                started=started,
                reason_hint="bad_request",
            )
        token = body.get("token")
        mode_hint = "paid" if type(token) is str and bool(token) else "trial"
        decision_code, decision_payload = _handle_activation_body(body)
        try:
            envelope = activation_http_e2ee.build_server_response(
                context,
                decision_code,
                decision_payload,
            )
        except activation_http_e2ee.ActivationHttpE2EEError:
            return self._send_activation_response(
                503,
                {"error": "activation e2ee state error"},
                started=started,
                mode_hint=mode_hint,
                reason_hint="state_error",
            )
        return self._send_activation_e2ee_response(
            envelope,
            decision_code=decision_code,
            decision_payload=decision_payload,
            started=started,
            mode_hint=mode_hint,
        )


def _handle_activation_body(body: dict) -> tuple[int, dict]:
    """把激活内部异常收敛为安全的结构化响应，避免 TCP 断连和客户端误报 token 错。"""
    if _ACT is None:
        return 503, {"ok": False, "reason": "activation_not_configured"}
    try:
        return _ACT.handle_activate(body)
    except Exception as exc:  # noqa: BLE001 - HTTP 边界必须 fail-closed
        mode = "paid" if isinstance(body.get("token"), str) and body.get("token") else "trial"
        print(f"[activation-internal-error] type={type(exc).__name__}", flush=True)
        return 503, {"ok": False, "mode": mode, "reason": "state_error"}


def _ingest_auth_status(key: str) -> str:
    """启动 banner 使用 Windows GBK 也能编码的纯文本安全标记。"""
    return "ON(HMAC/ApiKey)" if key else "[WARN] OFF(未配 --key·任何人可写入)"


def main(argv):
    global _OUT, _KEY, _EXPORT_KEY, _DASH_PASS, _DASH_ALLOW_ANON, _VM_PATH, _ACT
    global _ACTIVATION_HTTP_E2EE
    global _UPLOAD_IDENTITY_MODE, _UPLOAD_IDENTITY_STORE
    global _HISTORY_INDEX_DB_FILE, _SAMPLE_HISTORY_INDEX
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18900)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--key", default="", help="ingest key(/ingest;打包发客户端)")
    ap.add_argument("--out", default="data/collected/samples.jsonl")
    ap.add_argument("--history-index-db-file", default="",
                    help="private rebuildable sample-history SQLite; empty preserves legacy readers")
    ap.add_argument(
        "--upload-identity-mode",
        choices=sorted(_UPLOAD_IDENTITY_MODES),
        default="disabled",
        help="disabled=rollback; compat=legacy public + signed private; required=signed only",
    )
    ap.add_argument(
        "--upload-identity-db-file",
        default="/var/lib/bidking-upload-identity/upload_identity.sqlite3",
        help="pre-created private identity/replay SQLite database",
    )
    ap.add_argument(
        "--upload-security-epoch",
        type=int,
        default=0,
        help="server authority epoch; positive is required before signed upload leases",
    )
    ap.add_argument(
        "--upload-lease-seconds",
        type=int,
        default=300,
        help="short signed upload lease lifetime (1..900 seconds)",
    )
    # /dashboard + /stats 访问口令文件(默认 ~/bidking 工作目录下 dashboard_pass.txt)。
    # 文件存在且非空 -> 启用 HTTP Basic Auth；缺失/空文件时仅 loopback 开放，公网绑定 fail-closed。
    ap.add_argument("--dashboard-pass-file", default="dashboard_pass.txt")
    # /export 独立 key 文件(默认 export_key.txt)。设了 -> 导出要它(客户端只有 ingest key 故拖不走库);
    # 没设 -> /export 【fail-closed 直接 403】(见 do_GET /export), 【绝不回退 ingest key】(回退=任一群友拖全库)。
    ap.add_argument("--export-key-file", default="export_key.txt")
    # 版本清单文件(GET /version 下发)。存在则读;没有=不下发版本信息(客户端不提示更新, 向后兼容)。
    # 改版本发布只需编辑此 JSON【保存即生效, mtime 热重载, 无需重启】(见 _version_manifest)。
    ap.add_argument("--version-manifest-file", default="version_manifest.json")
    # 激活(/activate): activation_key.txt(签 token/lease, 与 ingest key 分开) + master_key.txt(出厂数据
    # 主密钥 base64, 激活成功交付客户端解密 data/processed)。两者都在 -> 启用激活; 缺则 /activate 返回 503。
    ap.add_argument("--activation-key-file", default="activation_key.txt")
    ap.add_argument("--master-key-file", default="master_key.txt")
    ap.add_argument(
        "--activation-http-e2ee-private-key-file",
        default="",
        help="optional X25519 private-key file; empty keeps /activate-e2ee disabled",
    )
    # 双密钥窗口(0.2.7 P0): 主密钥曾走明文 HTTP 下发 → 视为已泄露 → 轮换。但旧包的数据是用旧 key
    # 加密的, 单 key 直接换 = 旧包全解不开。故按 client_version 发: >=v2-min 发新 key, 老客户端发旧 key。
    # ⚠️不放 master_key_v2.txt 时行为与轮换前【完全等价】(恒发旧 key), 可安全先部署代码再择期轮换。
    ap.add_argument("--master-key-v2-file", default="master_key_v2.txt",
                    help="轮换后的新主密钥(可选; 文件不存在=未轮换=一律发旧 key)")
    ap.add_argument("--v2-min-version", default="0.2.7",
                    help="拿新主密钥的最低客户端版本(= 走 HTTPS 的第一个版本)")
    ap.add_argument(
        "--min-client-security-epoch",
        type=int,
        default=0,
        help="最低客户端安全代际；0=兼容旧包，1=拒绝缺少HTTPS-only协议字段的旧包",
    )
    ap.add_argument(
        "--min-client-version",
        default="",
        help="权威最低产品版本；空=关闭。独立于 --v2-min-version，示例 0.2.9-hotfix1",
    )
    ap.add_argument("--bindings-file", default="bindings.json")
    ap.add_argument("--revoked-file", default="revoked.txt")
    ap.add_argument(
        "--device-blocks-file",
        default="device_blocks.json",
        help="设备撤销状态；只保存域分离 HMAC，不保存原始设备指纹",
    )
    ap.add_argument(
        "--device-block-key-file",
        default="device_block_key.txt",
        help="设备撤销专用 HMAC key；文件缺失时仅允许空 denylist",
    )
    ap.add_argument("--telemetry-file", default="activation_telemetry.jsonl")
    ap.add_argument("--trials-file", default="trials.json")
    ap.add_argument(
        "--membership-store-file",
        default="",
        help="可选续费事务库；须显式初始化。空值保持旧卡激活，不启用自动续费或迁移",
    )
    ap.add_argument(
        "--pet-usage-file",
        default="pet_usage.json",
        help=argparse.SUPPRESS,
    )
    ap.add_argument(
        "--pet-usage-db-file",
        default="/var/lib/bidking-activation-anchor/pet_usage.sqlite3",
        help=(
            "Pet服务器权威SQLite账本；须由systemd StateDirectory和离线管理员"
            "初始化，服务不会自动创建或迁移"
        ),
    )
    ap.add_argument(
        "--pet-usage-lease-seconds",
        type=int,
        default=15,
        help="Pet可见计量短租约秒数（客户端不得上报已用秒数）",
    )
    ap.add_argument(
        "--trial-hours",
        type=float,
        default=activation_core.DEFAULT_TRIAL_HOURS,
        help="半免费试用时长(小时, 每设备一次; 当前统一12h)",
    )
    ap.add_argument(
        "--trial-grandfather-cutover-at",
        type=int,
        default=None,
        help="试用时长切换的显式服务端epoch；须停服迁移旧记录后与历史时长同时启用",
    )
    ap.add_argument(
        "--trial-grandfather-hours",
        type=float,
        default=None,
        help="cutover前已迁移试用记录的历史固定时长；不得小于新试用时长",
    )
    ap.add_argument("--lease-days", type=int, default=30, help="付费租约离线宽限窗口(天, 默认 1 个月); token 永久, 撤销靠回连生效")
    args = ap.parse_args(argv[1:])
    args.key = args.key.strip()
    if not _loopback_host(args.host) and not args.key:
        ap.error("non-loopback binding requires a non-empty ingest key")
    _UPLOAD_IDENTITY_MODE = args.upload_identity_mode
    _UPLOAD_IDENTITY_STORE = None
    if _UPLOAD_IDENTITY_MODE in {"compat", "required"}:
        if args.upload_security_epoch <= 0:
            ap.error("signed upload identity requires --upload-security-epoch > 0")
        try:
            _UPLOAD_IDENTITY_STORE = upload_identity_store.UploadIdentityStore(
                Path(args.upload_identity_db_file)
            )
        except upload_identity_store.UploadIdentityStateError:
            ap.error("upload identity database is unavailable or invalid")
    _OUT = Path(args.out)
    _HISTORY_INDEX_DB_FILE = Path(args.history_index_db_file) if args.history_index_db_file else None
    _SAMPLE_HISTORY_INDEX = None
    _KEY = args.key
    dp = Path(args.dashboard_pass_file)
    if dp.exists():
        _DASH_PASS = dp.read_text(encoding="utf-8").strip()
    # 无口令时: 仅 loopback 绑定放行匿名看板(本地 dev); 公网绑定拒绝(fail-closed·防外人裸看)。
    # ⚠️空 host "" 在 Python socket = INADDR_ANY = 绑所有接口 = 公网(等同 0.0.0.0), 【不算 loopback】→
    # 必须排除, 否则 `--host ""` + 无口令会绕过 fail-closed(审计 0709 揪出的边)。
    _DASH_ALLOW_ANON = _loopback_host(args.host)
    ek = Path(args.export_key_file)
    if ek.exists():
        _EXPORT_KEY = ek.read_text(encoding="utf-8").strip()
    _VM_PATH = Path(args.version_manifest_file)   # mtime 热重载(改文件即生效); 坏 JSON 在 _version_manifest 告警
    # 激活服务: activation_key + master_key 都在才启用(/activate /telemetry)
    akf = Path(args.activation_key_file)
    mkf = Path(args.master_key_file)
    _master_v2 = ""
    mk2 = Path(args.master_key_v2_file)
    if mk2.is_file():
        _master_v2 = mk2.read_text(encoding="utf-8").strip()
    if akf.is_file() and mkf.is_file():
        _secret = akf.read_bytes().strip()
        _master = mkf.read_text(encoding="utf-8").strip()
        dbkf = Path(args.device_block_key_file)
        try:
            # None is reserved for a genuinely absent optional key.  Existing
            # empty/blank/unreadable paths remain supplied-but-invalid (b"") so
            # ActivationService fails readiness and activation closed.
            _device_block_key = dbkf.read_bytes().strip()
        except FileNotFoundError:
            _device_block_key = None
        except OSError:
            _device_block_key = b""
        if _secret and _master:
            import activation_server  # 同目录
            _ACT = activation_server.ActivationService(
                secret=_secret, master_key_b64=_master,
                bindings_path=Path(args.bindings_file), revoked_path=Path(args.revoked_file),
                device_blocks_path=Path(args.device_blocks_file),
                device_block_key=_device_block_key,
                telemetry_path=Path(args.telemetry_file), trials_path=Path(args.trials_file),
                membership_store_path=(Path(args.membership_store_file) if args.membership_store_file else None),
                pet_usage_db_path=Path(args.pet_usage_db_file),
                pet_usage_path=Path(args.pet_usage_file),
                pet_usage_lease_seconds=args.pet_usage_lease_seconds,
                trial_hours=args.trial_hours, lease_days=args.lease_days,
                trial_grandfather_cutover_at=args.trial_grandfather_cutover_at,
                trial_grandfather_hours=args.trial_grandfather_hours,
                master_key_v2_b64=(_master_v2 or None), v2_min_version=args.v2_min_version,
                min_client_security_epoch=args.min_client_security_epoch,
                min_client_version=args.min_client_version,
                upload_security_epoch=args.upload_security_epoch,
                upload_lease_seconds=args.upload_lease_seconds)
    if args.activation_http_e2ee_private_key_file:
        if _ACT is None:
            raise SystemExit("activation HTTP E2EE requires activation service")
        try:
            _ACTIVATION_HTTP_E2EE = activation_http_e2ee.ServerProtocol.from_key_file(
                Path(args.activation_http_e2ee_private_key_file)
            )
        except activation_http_e2ee.ActivationHttpE2EEError as exc:
            raise SystemExit("activation HTTP E2EE key is invalid") from exc
    else:
        _ACTIVATION_HTTP_E2EE = None
    if _UPLOAD_IDENTITY_MODE == "required" and _ACT is None:
        ap.error("required upload identity needs activation authority")
    httpd = SafeThreadingHTTPServer((args.host, args.port), Handler)
    _start_history_index_load()
    if _DASH_PASS:
        auth = "ON(Basic Auth)"
    elif _DASH_ALLOW_ANON:
        auth = "OFF(loopback 本地开放)"
    else:
        auth = "LOCKED(公网无口令→拒绝·部署 dashboard_pass.txt 才可访问)"
    exp = "独立key" if _EXPORT_KEY else "关闭(fail-closed·需 export_key.txt)"
    ver = (_version_manifest().get("latest") or "未配置")
    if _ACT:
        # 双密钥窗口状态要能一眼看出来: 轮换后若 banner 仍是"单key", 说明 v2 文件没放对 → 新包会拿到旧 key 解不开数据。
        _k2 = f"·双key(>={args.v2_min_version} 发新key)" if _master_v2 else "·单key(未轮换)"
        _epoch = f"·安全代际>={args.min_client_security_epoch}"
        _product = f"·产品版本>={args.min_client_version}" if args.min_client_version else "·产品版本门关闭"
        if _ACT.device_block_key_state == "valid":
            _device_gate = "·设备撤销key=ON"
        elif _ACT.device_block_key_state == "missing":
            _device_gate = "·设备撤销key=OFF"
        else:
            _device_gate = "·设备撤销key=INVALID"
        act = (
            f"ON(试用 {args.trial_hours:g}h/租约 {args.lease_days}d"
            f"{f'·历史试用{args.trial_grandfather_hours:g}h' if args.trial_grandfather_cutover_at is not None else ''}"
            f"{_k2}{_epoch}{_product}{_device_gate})"
        )
    else:
        act = "OFF(缺 activation_key/master_key)"
    ing = _ingest_auth_status(_KEY)
    print(f"[collector] 历史索引状态={_history_index_state()}; 监听 {args.host}:{args.port} -> {_OUT}; "
          f"ingest鉴权 {ing}; 看板鉴权 {auth}; export {exp}; 版本清单 latest={ver}(热重载); 激活 {act}; "
          f"限流 {_RATE_MAX}/{int(_RATE_WINDOW)}s/IP", flush=True)
    httpd.serve_forever()
    return 0


def _warm_public_cache() -> None:
    """启动时在后台把 /public 的缓存预热一次。

    为什么: `_public_bodies()` 的重建是【在请求线程里同步跑】的, 而进程刚起来时 `_PUB_CACHE` 是空的
    ⇒ **重启后的第一个访客要替所有人跑一次全量聚合**(读盘+解析 4.6 万行 ≈ 本机 SSD 2.8s, VPS 上更久;
    2026-07-12 实测线上冷缓存首请求 >10s 超时, 缓存热了之后 3.1s)。而 collector 每次部署都会重启 ⇒
    每次发版后第一个打开公开页的人都吃这一下。

    预热放后台线程: 绝不阻塞 serve_forever(否则端口迟迟不 listen, server_update.sh 的健康检查会误判
    成"启动失败"→ 假回滚, 见 deploy-pipeline-footguns-bidking)。失败只打一行、绝不影响服务
    —— 预热本来就只是优化, 失败了顶多退回"第一个访客等一会儿"的旧行为。
    """
    def _run() -> None:
        try:
            t0 = time.monotonic()
            _public_bodies(_recent_window(PUBLIC_DEFAULT_DAYS))
            print(f"[collector] /public 缓存已预热 ({time.monotonic() - t0:.1f}s)", flush=True)
        except Exception as exc:  # noqa: BLE001 - 预热失败绝不能拖垮服务
            print(f"[collector] /public 预热失败({type(exc).__name__}: {exc}); "
                  f"退回首次请求时重建", flush=True)

    threading.Thread(target=_run, name="public-cache-warm", daemon=True).start()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
