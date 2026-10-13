"""Cold-start BidKing WSS/TLS capture feeding the existing live monitor.

The official 2026-07-23 client moved the unchanged application frames from
clear TCP to WSS.  This adapter starts mitmdump in Windows process-local mode
*before* BidKing.exe launches, receives only sanitized allowlisted frames from
the addon, and reuses GameFrameGate + FatbeansWebhookMonitor unchanged.

No mitmproxy flow file is written.  Unknown client messages, including login and
authentication traffic, are discarded in the addon before crossing stdout.
"""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import ctypes
from dataclasses import dataclass, field
import hashlib
import json
import math
import ntpath
import os
from pathlib import Path
import queue
import re
import ssl
import stat as stat_module
import subprocess
import sys
import threading
import time
from typing import Any, BinaryIO, Callable, Mapping, TextIO


ROOT = Path(__file__).resolve().parents[1]
for path in (
    ROOT / "scripts",
    ROOT / "src",
    ROOT / "hero_ref/src",
    ROOT / "hero_ref/tools",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from _local_compat_core import (  # noqa: E402
    FRAME_STREAM_PREFIX,
    FLOW_STREAM_PREFIX,
    allowed_message_ids,
    frame_message_id,
)
from bidking_lab.live.monitor import load_monitor_tables  # noqa: E402
from run_fatbeans_webhook_monitor import (  # noqa: E402
    FatbeansWebhookMonitor,
    WebhookMonitorConfig,
    _acquire_lock,
    _validated_package_generation_identity,
)
from run_windivert_live_monitor import (  # noqa: E402
    GameFrameGate,
    _capture_time,
    _should_schedule_monitor_process,
    _write_source_status,
)
from network_connection_status import (  # noqa: E402
    GameCaptureLivenessEpoch,
    NETWORK_CONNECTION_STATUS_FILENAME,
    PlayerLogConnectionWindow,
    atomic_write_network_status,
    derive_network_connection_status,
    read_json_mapping,
)
try:  # noqa: E402
    from _runtime_component import ComponentError, validate_bundled_runtime_copy
except ImportError:  # source-tree / standard portable profile
    from runtime_component import ComponentError, validate_bundled_runtime_copy


DEFAULT_SERVER_HOST = "release.bid-king.cn"
DEFAULT_PROCESS_NAME = "BidKing.exe"
DEFAULT_CA_DIR_NAME = "BidKingLive/mitmproxy"
WSS_CA_FAILURE_SENTINEL = "BIDKING_WSS_CA_FAILURE="
LIFECYCLE_SCHEMA_VERSION = 1
LIFECYCLE_STATUS_FILENAME = "lifecycle_status.json"
LIFECYCLE_STARTUP_ATTACH_GRACE_SECONDS = 15.0
LIFECYCLE_DRAIN_GRACE_SECONDS = 5.0
LIFECYCLE_EXIT_CONFIRM_POLLS = 3
LIFECYCLE_POLL_SECONDS = 0.5
DISABLE_IDLE_AUTOSTOP_ENV = "BIDKING_DISABLE_WSS_IDLE_AUTOSTOP"
DISABLE_WINDIVERT_AUTO_STALE_REPAIR_ENV = (
    "BIDKING_DISABLE_WINDIVERT_AUTO_STALE_REPAIR"
)
WSS_HELPER_STATUS_SCHEMA_VERSION = 4
WSS_HELPER_STATUS_FILENAME = "wss_helper_status.json"
WSS_OPERATIONAL_ABI = "bidking_wss_addon_operational_v1"
WSS_OPERATIONAL_GENERATION = (
    "f14045323478eeab5c5654854dccd444e75e626a15c64bd6fed8332b4093c153"
)


class WssCaPrepareError(RuntimeError):
    """Stable, path-free failure classification for CA generation."""

    def __init__(self, classification: str) -> None:
        if classification not in {
            "component_write_denied",
            "component_execution_blocked",
        }:
            classification = "unknown"
        super().__init__(classification)
        self.classification = classification


WSS_OPERATIONAL_ABI_ENV = "BIDKING_WSS_OPERATIONAL_ABI"
WSS_OPERATIONAL_GENERATION_ENV = "BIDKING_WSS_OPERATIONAL_GENERATION"
WSS_PACKAGE_GENERATION_ENV = "BIDKING_WSS_PACKAGE_GENERATION"
WSS_PACKAGE_ROOT_ENV = "BIDKING_WSS_PACKAGE_ROOT"
WSS_RUNTIME_ROLE_ENV = "BIDKING_WSS_RUNTIME_ROLE"
REDIRECTOR_PROCESS_NAME = "windows-redirector.exe"
REDIRECTOR_STARTUP_GRACE_SECONDS = 15.0
REDIRECTOR_MISSING_CONFIRM_POLLS = 3
PLAYER_LOG_PATH_ENV = "BIDKING_PLAYER_LOG_PATH"
KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256 = (
    "8da085332782708d8767bcace5327a6ec7283c17cfb85e40b03cd2323a90ddc2"
)
# Immutable public-package inventory only. Expanding repair eligibility to
# another package generation requires another exact manifest entry and audit.
KNOWN_BIDKING_WINDIVERT_SIGNER_THUMBPRINT = (
    "043589f75fce2795e7f2cc3e526d46784d5ddab3"
)
KNOWN_OLD_BIDKING_PACKAGE_MANIFEST_SHA256 = (
    "a3f2575e9f85f945ce117b78acd1ce582832c24b575acf23c36fb2c46b3672b1"
)
# Exact BUILD_MANIFEST.txt digests from the immutable public packages that can
# have created BidKingWinDivertR1 before the current launch.  The driver digest,
# Authenticode signer, stopped-service shape and stable path/file identity gates
# below still apply; this only stops treating every post-0.3.0 package as a
# foreign service generation.
KNOWN_PUBLISHED_BIDKING_PACKAGE_MANIFEST_SHA256S = frozenset(
    {
        KNOWN_OLD_BIDKING_PACKAGE_MANIFEST_SHA256,
        # 0.3.0 hotfix1 / hotfix1-r1 / hotfix2.
        "5f68750eea71ac89c3a9b38c0b399efec8a29abf9ace330013f8ed9dddba0581",
        "e32bfe529b1b8099b10990ec02d86c2bf572e2c37f097070ef79d3613fe9c1c8",
        "942842957e1e6fe289bab29531c7216055f961d1b527fd05ffe716c9d364dd56",
        # 0.3.1 through 0.3.1-hotfix5.
        "e5de65c97d0198ec38b759328f0e63ea7d89da9f8661a0d7d2be6f43bbc58f06",
        "54ad1dee92ce37b6ee86ba8b1c8e8f1b96f2174db5d48f536cc352ac6c07475b",
        "c37429bdccde205151bd8316631a85756d86a9084ab6bda5817c28d9ff188e9c",
        "52e3e6446aedaefcb680464efe09664c2604e8ecd76a8f0bf13d847d59d085e1",
        "e7ff0e81969f67c79fc05dbc44c5a6299d2ea865d65af4f08026d633dac1448c",
        "b839666a67e5ac84a094edad510718c3590e6dc83c3d59f9291c522b02231ce7",
        # 0.3.2 through 0.3.2-hotfix2.
        "f8180da40eb43ad8270af58b1c2d532e6209bcd80e56bca3bbb6918fdb0afe90",
        "8bac0b68a8a9508014116ac1956e5da1cdd766618f30f252921d6f57d9fc3409",
        "f7b9f8459d17ea886754a2126c9b94ab95b47ff28343cfca0caedd536f9d0146",
        # Group-distributed 0.3.3-hotfix2 fixed9 and final 0.3.3-hotfix3.
        "7ba99e43e1a5e831535022e2d12f25c9d711f629ef0225f5b459da59b3593e49",
        "020dba82050619e20710681acd5382827b9d9657af848605bbd965c3646f5d0b",
    }
)
_KNOWN_OLD_BIDKING_WINDIVERT_RELATIVE_PARTS = (
    "vendor",
    "mitmproxy",
    "_internal",
    "mitmproxy_windows",
    "WinDivert64.sys",
)
_WINDIVERT_SERVICE_LIFECYCLE_MUTEX = (
    "Global\\BidKing.WinDivertCompatibilityServiceLifecycle.v1"
)
_WINDIVERT_SERVICE_LIFECYCLE_LOCAL = threading.local()
WINDIVERT_PRELOAD_SERVICE_NAME = "BidKingWinDivertR1"
_WINDIVERT_REPAIR_STATES = frozenset(
    {
        "not_started",
        "not_present",
        "repaired_restart_required",
        "failed_not_applicable",
        "failed_not_elevated",
        "failed_repair_busy",
        "failed_untrusted_driver",
        "failed_same_path_mixed_generation",
        "failed_service_conflict",
        "failed_access_denied",
        "failed_marked_for_delete",
        "failed_unclassified",
    }
)
_WINDIVERT_REPAIR_PHASES = frozenset(
    {
        "not_started",
        "authorize",
        "lock",
        "query_product",
        "verify_product",
        "delete_product",
        "complete",
    }
)
_WINDIVERT_PRELOAD_STATES = frozenset(
    {
        "not_applicable",
        "not_needed",
        "preloaded",
        "already_preloaded",
        "repaired_restart_required",
        "failed_missing_driver",
        "failed_untrusted_driver",
        "failed_access_denied",
        "failed_invalid_image_hash",
        "failed_driver_blocked",
        "failed_same_path_mixed_generation",
        "failed_service_conflict",
        "failed_unclassified",
    }
)
_WINDIVERT_PRELOAD_PHASES = frozenset(
    {
        "not_started",
        "validate_driver",
        "query_legacy",
        "query_product",
        "create_product",
        "verify_product",
        "start_product",
        "query_running",
        "cleanup",
        "complete",
    }
)
_MONITOR_ELEVATION_STATES = frozenset(
    {"elevated", "not_elevated", "unknown", "not_applicable"}
)
RUNTIME_COMPATIBILITY_FAILED_STATE = "failed_known_historical_driver"
_REDIRECT_DAEMON_PREMATURE_EXIT = "wss_redirect_daemon_premature_exit"
_RUNTIME_COMPATIBILITY_ERROR = "wss_runtime_compatibility_failed"
_REDIRECTOR_MISSING_STABLE_ERROR = "wss_redirector_missing_stable"
_REDIRECTOR_OBSERVATION_STATES = frozenset(
    {"unknown", "starting", "running", "missing_stable"}
)
_WSS_HELPER_PHASES = frozenset(
    {
        "helper_running",
        "helper_start_failed",
        "exited_unexpectedly",
        "stopped",
        "monitor_guard_failed",
    }
)

_MITMDUMP_STDERR_CLASSIFIERS = (
    (
        "Failed to start the interception process as administrator.",
        "wss_redirector_elevation_denied",
    ),
    (
        "Task failed: redirect daemon exited prematurely.",
        _REDIRECT_DAEMON_PREMATURE_EXIT,
    ),
    ("Failed to start the executable:", "wss_redirector_start_failed"),
    ("WinDivert Error:", "wss_windivert_start_failed"),
    ("Error opening WinDivert handle", "wss_windivert_start_failed"),
    ("Error loading script", "wss_addon_start_failed"),
    ("Addon error:", "wss_addon_start_failed"),
    ("No module named", "wss_addon_start_failed"),
    ("Error logged during startup, exiting...", "wss_capture_helper_start_failed"),
)
_WSS_HELPER_ERROR_PRIORITY = (
    "wss_redirector_elevation_denied",
    _REDIRECT_DAEMON_PREMATURE_EXIT,
    "wss_windivert_start_failed",
    "wss_redirector_start_failed",
    "wss_addon_start_failed",
    "wss_capture_helper_start_failed",
)
_WSS_HELPER_ERROR_DETAILS = {
    "wss_redirector_elevation_denied": (
        "Windows 拒绝启动本地兼容拦截组件。",
        "核对官方包完整性和下载来源标记；若压缩包属性显示“解除锁定”，请解除后重新完整解压。",
    ),
    "wss_windivert_start_failed": (
        "本地网络重定向驱动未能启动。",
        "请保持游戏关闭并导出诊断；不要仅凭安全软件清单判断根因。",
    ),
    "wss_redirector_start_failed": (
        "本地兼容拦截组件未能启动。",
        "请重新完整解压当前版本，并把诊断私发支持人员。",
    ),
    "wss_addon_start_failed": (
        "本地兼容安全桥启动失败。",
        "请重新完整解压当前版本，并把诊断私发支持人员。",
    ),
    "wss_capture_helper_start_failed": (
        "本地兼容读取组件在启动阶段退出。",
        "请保持游戏关闭并导出诊断，由支持人员结合组件状态分类。",
    ),
    "wss_stale_product_service_conflict": (
        "产品兼容服务身份冲突，本次读取未启动。",
        "请保留现场并把诊断私发支持人员；不要处理身份未确认的 WinDivert 服务。",
    ),
    "wss_same_path_mixed_generation": (
        "产品兼容服务与当前目录的代际身份无法完整确认。",
        "请完全退出后把官方 ZIP 完整解压到新的最终目录；不要先删除旧目录。",
    ),
    _RUNTIME_COMPATIBILITY_ERROR: (
        "本地兼容组件与当前 Windows 安全策略不兼容。",
        "请保持系统安全功能开启并导出诊断；不要关闭易受攻击驱动阻止列表或内存完整性。",
    ),
    _REDIRECTOR_MISSING_STABLE_ERROR: (
        "本地兼容组件在启动宽限后仍未接管游戏流量。",
        "请保持系统安全功能开启并导出诊断，等待实机兼容处理；不要修改代理、路由或安全设置。",
    ),
    "wss_capture_helper_exit_unclassified": (
        "本地兼容读取组件意外退出。",
        "请保持游戏关闭并导出诊断；诊断不会收集请求内容或原始连接数据。",
    ),
}


def _default_confdir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
    return base / DEFAULT_CA_DIR_NAME


def _candidate_mitmdump_paths(
    *, allow_engineering_runtime: bool = False
) -> tuple[Path, ...]:
    candidates = [ROOT / "vendor" / "mitmproxy" / "mitmdump.exe"]
    if allow_engineering_runtime and (ROOT / ".git").exists():
        candidates.append(
            ROOT / "scripts" / "pkg_assets" / "mitmproxy" / "mitmdump.exe"
        )
    return tuple(candidates)


def resolve_mitmdump(
    explicit: str | None = None,
    *,
    allow_engineering_runtime: bool = False,
) -> Path:
    allowed = tuple(
        candidate.resolve()
        for candidate in _candidate_mitmdump_paths(
            allow_engineering_runtime=allow_engineering_runtime
        )
    )
    requested = Path(explicit).resolve() if explicit else None
    if requested is None:
        candidates = allowed
    elif requested in allowed:
        candidates = (requested,)
    else:
        candidates = ()
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    if requested is not None:
        try:
            cache_root = (
                Path(os.environ["LOCALAPPDATA"])
                / "BidKingLive"
                / "components"
            )
            payload = validate_bundled_runtime_copy(
                requested.parent,
                ROOT / "scripts" / "mitmproxy_runtime_manifest.json",
                cache_root=cache_root,
            )
            expected = payload / "mitmdump.exe"
            if requested == expected and expected.is_file():
                return expected
        except (ComponentError, KeyError, OSError):
            pass
    raise FileNotFoundError(
        "fixed mitmdump runtime unavailable; "
        "expected vendor/mitmproxy/mitmdump.exe"
    )


def ca_certificate_path(confdir: Path) -> Path:
    return confdir / "mitmproxy-ca-cert.cer"


def ca_is_trusted_for_current_user(certificate_path: Path) -> bool:
    if not certificate_path.is_file() or not hasattr(ssl, "enum_certificates"):
        return False
    try:
        pem = certificate_path.read_text(encoding="ascii")
        target = ssl.PEM_cert_to_DER_cert(pem)
        return any(
            encoding == "x509_asn" and certificate == target
            for certificate, encoding, _trust in ssl.enum_certificates("ROOT")
        )
    except Exception:
        return False


def _named_process_running_state(
    process_name: str,
    *,
    process_iter: Any = None,
) -> bool | None:
    """Return True/False only after a complete process enumeration.

    ``None`` is deliberately fail-safe: lifecycle cleanup must never interpret
    a missing dependency or a protected/failed process query as "game exited".
    """

    if process_iter is None:
        try:
            import psutil
        except Exception:
            return None
        process_iter = psutil.process_iter
    wanted = process_name.casefold()
    scan_incomplete = False
    try:
        for process in process_iter(["name"]):
            try:
                if str(process.info.get("name") or "").casefold() == wanted:
                    return True
            except Exception:
                scan_incomplete = True
                continue
    except Exception:
        return None
    return None if scan_incomplete else False


def _game_process_ids(process_name: str) -> tuple[int, ...]:
    try:
        import psutil
    except Exception:
        return ()
    wanted = process_name.casefold()
    found: list[int] = []
    try:
        for process in psutil.process_iter(["pid", "name"]):
            try:
                if str(process.info.get("name") or "").casefold() == wanted:
                    found.append(int(process.info["pid"]))
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
    except Exception:
        return ()
    return tuple(sorted(found))


def _game_process_evidence(
    process_name: str,
    *,
    process_iter: Any = None,
) -> tuple[bool | None, int | None, float | None]:
    """Return process-bound game evidence without exposing its executable path."""

    if process_iter is None:
        try:
            import psutil
        except Exception:
            return None, None, None
        process_iter = psutil.process_iter
    wanted = process_name.casefold()
    scan_incomplete = False
    found: list[tuple[float, int]] = []
    try:
        for process in process_iter(["pid", "name", "create_time"]):
            try:
                info = process.info
                if str(info.get("name") or "").casefold() != wanted:
                    continue
                pid = int(info["pid"])
                created_at = float(info.get("create_time") or 0.0)
                if pid > 0:
                    found.append((created_at, pid))
            except Exception:
                scan_incomplete = True
    except Exception:
        return None, None, None
    if found:
        created_at, pid = max(found)
        return True, pid, created_at if created_at > 0 else None
    return (None if scan_incomplete else False), None, None


def _default_player_log_path() -> Path:
    override = str(os.environ.get(PLAYER_LOG_PATH_ENV) or "").strip()
    if override:
        return Path(override)
    return Path.home() / "AppData" / "LocalLow" / "laolin" / "BidKing" / "Player.log"


def _pid_state_file_running_state(state_path: Path) -> bool | None:
    """Validate a launcher-owned PID file without trusting a recycled PID."""

    if not state_path.is_file():
        return False
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8-sig"))
        pid = int(payload["pid"])
        expected_started_at = float(payload.get("started_at") or 0.0)
        if pid <= 0:
            return None
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None

    try:
        import psutil
    except Exception:
        return None
    try:
        process = psutil.Process(pid)
        name = str(process.name() or "").casefold()
        if not name.startswith("python"):
            return False
        if expected_started_at > 0:
            actual_started_at = float(process.create_time())
            if abs(actual_started_at - expected_started_at) > 120.0:
                return False
        return bool(process.is_running())
    except psutil.NoSuchProcess:
        return False
    except (psutil.AccessDenied, psutil.ZombieProcess):
        return None
    except Exception:
        return None


def _env_switch_enabled(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _lifecycle_policy_enabled(policy_path: Path) -> bool | None:
    if not policy_path.is_file():
        return None
    try:
        payload = json.loads(policy_path.read_text(encoding="utf-8-sig"))
        enabled = payload["auto_stop_when_idle"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return None
    return enabled if isinstance(enabled, bool) else None


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def classify_mitmdump_stderr_line(line: str) -> str | None:
    """Reduce private mitmdump stderr to a fixed support-safe error code."""

    for marker, error_code in _MITMDUMP_STDERR_CLASSIFIERS:
        if marker in line:
            return error_code
    return None


@dataclass
class WssHelperDiagnostics:
    """Thread-safe classifier that never retains the stderr text it observes."""

    _error_codes: set[str] = field(default_factory=set, init=False, repr=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
    )

    def observe_line(self, line: str) -> None:
        error_code = classify_mitmdump_stderr_line(line)
        if error_code:
            with self._lock:
                self._error_codes.add(error_code)

    def primary_error_code(self) -> str | None:
        with self._lock:
            observed = set(self._error_codes)
        return next(
            (code for code in _WSS_HELPER_ERROR_PRIORITY if code in observed),
            None,
        )

    def redirector_state(self) -> str:
        error_code = self.primary_error_code()
        if error_code == "wss_redirector_elevation_denied":
            return "elevation_denied"
        if error_code in {
            _REDIRECT_DAEMON_PREMATURE_EXIT,
            "wss_redirector_start_failed",
        }:
            return "start_failed"
        return "unknown"

    def windivert_state(self) -> str:
        if self.primary_error_code() == "wss_windivert_start_failed":
            return "start_failed"
        return "unknown"


@dataclass
class WssRedirectorLiveness:
    """Classify persistent redirector absence without trusting one process scan."""

    grace_seconds: float = REDIRECTOR_STARTUP_GRACE_SECONDS
    missing_confirm_polls: int = REDIRECTOR_MISSING_CONFIRM_POLLS
    _game_seen_at: float | None = field(default=None, init=False, repr=False)
    _missing_polls: int = field(default=0, init=False, repr=False)

    def reset_game(self) -> None:
        self._game_seen_at = None
        self._missing_polls = 0

    def observe(
        self,
        *,
        now: float,
        game_running: bool | None,
        redirector_running: bool | None,
        has_current_wss_health: bool,
    ) -> str:
        if game_running is not True:
            self.reset_game()
            return "unknown"
        if self._game_seen_at is None:
            self._game_seen_at = max(0.0, float(now))
        if has_current_wss_health:
            # Current per-game flow/data/session evidence is stronger than a
            # process-list snapshot and must clear any pending absence verdict.
            self._missing_polls = 0
            return "unknown"
        if redirector_running is True:
            self._missing_polls = 0
            return "running"
        if redirector_running is not False:
            # An incomplete enumeration is not an absence observation.
            self._missing_polls = 0
            return "unknown"
        elapsed = max(0.0, float(now) - self._game_seen_at)
        if elapsed < max(0.0, float(self.grace_seconds)):
            self._missing_polls = 0
            return "starting"
        self._missing_polls += 1
        required = max(1, int(self.missing_confirm_polls))
        return "missing_stable" if self._missing_polls >= required else "starting"


def _consume_mitmdump_stderr(
    stream: TextIO,
    diagnostics: WssHelperDiagnostics,
) -> None:
    for line in stream:
        diagnostics.observe_line(line)


def _bundled_windivert_driver(mitmdump_path: Path) -> Path:
    return (
        mitmdump_path.parent
        / "_internal"
        / "mitmproxy_windows"
        / "WinDivert64.sys"
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class _StableOperationalMember:
    path: Path
    stream: BinaryIO
    identity: tuple[int, int]
    size: int
    sha256: str

    def assert_unchanged(self) -> None:
        try:
            if _windows_stream_identity(self.stream) != self.identity:
                raise ValueError("operational member identity changed")
            self.stream.seek(0)
            raw = self.stream.read()
            if len(raw) != self.size or hashlib.sha256(raw).hexdigest() != self.sha256:
                raise ValueError("operational member content changed")
        except BaseException as exc:
            raise RuntimeError("wss operational identity unavailable") from exc


def _windows_stream_identity(stream: BinaryIO) -> tuple[int, int]:
    if os.name != "nt":
        raise OSError("stable WSS operational identity is Windows-only")

    import msvcrt
    from ctypes import wintypes

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("file_attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("last_access_time", wintypes.FILETIME),
            ("last_write_time", wintypes.FILETIME),
            ("volume_serial_number", wintypes.DWORD),
            ("file_size_high", wintypes.DWORD),
            ("file_size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    )
    get_information.restype = wintypes.BOOL
    information = _ByHandleFileInformation()
    handle = wintypes.HANDLE(msvcrt.get_osfhandle(stream.fileno()))
    if not get_information(handle, ctypes.byref(information)):
        raise OSError(
            ctypes.get_last_error(),
            "WSS operational file identity unavailable",
        )
    if information.file_attributes & (0x00000010 | 0x00000400):
        raise OSError("WSS operational member is not a regular file")
    file_index = (int(information.file_index_high) << 32) | int(
        information.file_index_low
    )
    return int(information.volume_serial_number), file_index


def _open_stable_operational_member(
    path: Path,
    *,
    max_bytes: int,
) -> _StableOperationalMember:
    """Open one immutable packaged member and deny write/delete replacement.

    The product package is Windows-only.  Holding this handle for the helper
    lifetime allows child reads but prevents an already validated bootstrap,
    manifest, or native loader from being modified, renamed, or replaced.
    """

    if os.name != "nt":
        raise OSError("stable WSS operational snapshot is Windows-only")

    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path),
        0x80000000,  # GENERIC_READ
        0x00000001,  # FILE_SHARE_READ; deny write/delete/rename
        None,
        3,  # OPEN_EXISTING
        0x00200000 | 0x00000080,  # OPEN_REPARSE_POINT | ATTRIBUTE_NORMAL
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle in (None, invalid_handle):
        raise OSError(
            ctypes.get_last_error(),
            "WSS operational member unavailable",
        )
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    try:
        descriptor = msvcrt.open_osfhandle(int(handle), flags)
    except BaseException:
        kernel32.CloseHandle(handle)
        raise
    try:
        stream = os.fdopen(descriptor, "rb", buffering=0)
    except BaseException:
        os.close(descriptor)
        raise
    try:
        identity = _windows_stream_identity(stream)
        size = int(os.fstat(stream.fileno()).st_size)
        if size <= 0 or size > max_bytes:
            raise ValueError("WSS operational member size is invalid")
        raw = stream.read()
        if len(raw) != size:
            raise OSError("WSS operational member read was incomplete")
        return _StableOperationalMember(
            path=path,
            stream=stream,
            identity=identity,
            size=size,
            sha256=hashlib.sha256(raw).hexdigest(),
        )
    except BaseException:
        stream.close()
        raise


@dataclass
class _WssOperationalSnapshot:
    root: Path
    scripts_root: Path
    manifest: _StableOperationalMember
    bootstrap: _StableOperationalMember
    loader: _StableOperationalMember
    fields: dict[str, str]
    package_generation_identity: str = ""
    _closed: bool = False

    def bind_package_generation(self, value: object) -> dict[str, str]:
        if (
            type(value) is not str  # noqa: E721 - reject protocol subclasses
            or re.fullmatch(r"[0-9a-f]{64}", value) is None
        ):
            raise RuntimeError("wss operational identity unavailable")
        self.package_generation_identity = value
        return self.environment()

    def environment(self) -> dict[str, str]:
        if self._closed or not self.package_generation_identity:
            raise RuntimeError("wss operational identity unavailable")
        return {
            WSS_OPERATIONAL_ABI_ENV: WSS_OPERATIONAL_ABI,
            WSS_PACKAGE_ROOT_ENV: str(self.root),
            WSS_PACKAGE_GENERATION_ENV: self.package_generation_identity,
            WSS_OPERATIONAL_GENERATION_ENV: WSS_OPERATIONAL_GENERATION,
            WSS_RUNTIME_ROLE_ENV: "mitmdump",
        }

    def assert_unchanged(self) -> None:
        if self._closed:
            raise RuntimeError("wss operational identity unavailable")
        try:
            portable = self.fields.get("PortableProfile") == "standard"
            if not portable and [
                path.name
                for path in self.scripts_root.glob("_local_compat_addon_loader*.pyd")
            ] != ["_local_compat_addon_loader.pyd"]:
                raise ValueError("operational loader identity changed")
            self.manifest.assert_unchanged()
            self.bootstrap.assert_unchanged()
            self.loader.assert_unchanged()
        except BaseException as exc:
            raise RuntimeError("wss operational identity unavailable") from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for member in (self.loader, self.bootstrap, self.manifest):
            try:
                member.stream.close()
            except Exception:
                pass

    def __del__(self) -> None:
        self.close()


def _acquire_wss_operational_snapshot(
    *,
    package_root: Path,
    addon_path: Path,
) -> _WssOperationalSnapshot | None:
    try:
        root = package_root.resolve(strict=True)
        manifest_path = root / "BUILD_MANIFEST.txt"
        if not manifest_path.exists():
            return None
        scripts_root = (root / "scripts").resolve(strict=True)
        members: list[_StableOperationalMember] = []
        try:
            manifest = _open_stable_operational_member(
                manifest_path,
                max_bytes=1_048_576,
            )
            members.append(manifest)
            manifest.stream.seek(0)
            text = manifest.stream.read().decode("utf-8-sig")
            profile_fields = dict(
                line.split(": ", 1) for line in text.splitlines() if ": " in line
            )
            portable = (
                profile_fields.get("ProductProfile") == "standard"
                and profile_fields.get("Encrypted") == "False"
                and profile_fields.get("NativeBinaryDistribution") == "NotRequired"
            )
            expected_addon = scripts_root / (
                "mitm_bidking_wss_addon.py" if portable else "local_compat_addon.py"
            )
            if addon_path.resolve(strict=True) != expected_addon:
                raise ValueError("operational bootstrap mismatch")
            loader_path = scripts_root / (
                "_local_compat_core.py" if portable else "_local_compat_addon_loader.pyd"
            )
            if not portable and [path.name for path in scripts_root.glob("_local_compat_addon_loader*.pyd")] != ["_local_compat_addon_loader.pyd"]:
                raise ValueError("operational loader ambiguous")
            bootstrap = _open_stable_operational_member(
                expected_addon,
                max_bytes=1_048_576,
            )
            members.append(bootstrap)
            loader = _open_stable_operational_member(
                loader_path,
                max_bytes=64 * 1024 * 1024,
            )
            members.append(loader)

            if portable:
                fields = {"PortableProfile": "standard"}
                snapshot = _WssOperationalSnapshot(root=root, scripts_root=scripts_root, manifest=manifest, bootstrap=bootstrap, loader=loader, fields=fields)
                snapshot.assert_unchanged()
                return snapshot
            expected_fields = {
                "WssOperationalABI",
                "WssOperationalGeneration",
                "WssAddonBootstrapSHA256",
                "WssAddonLoaderSHA256",
            }
            fields: dict[str, str] = {}
            for line in text.splitlines():
                if ":" not in line:
                    continue
                key, raw_value = line.split(":", 1)
                if key not in expected_fields:
                    continue
                if key in fields or not raw_value.startswith(" "):
                    raise ValueError("manifest identity ambiguous")
                value = raw_value[1:]
                if not value or value != value.strip():
                    raise ValueError("manifest identity malformed")
                fields[key] = value
            if set(fields) != expected_fields:
                raise ValueError("manifest identity missing")
            if fields["WssOperationalABI"] != WSS_OPERATIONAL_ABI:
                raise ValueError("operational ABI mismatch")
            if fields["WssOperationalGeneration"] != WSS_OPERATIONAL_GENERATION:
                raise ValueError("operational generation mismatch")
            for key in (
                "WssAddonBootstrapSHA256",
                "WssAddonLoaderSHA256",
            ):
                if re.fullmatch(r"[0-9a-f]{64}", fields[key]) is None:
                    raise ValueError("operational digest malformed")
            if (
                bootstrap.sha256 != fields["WssAddonBootstrapSHA256"]
                or loader.sha256 != fields["WssAddonLoaderSHA256"]
            ):
                raise ValueError("operational member identity mismatch")
            snapshot = _WssOperationalSnapshot(
                root=root,
                scripts_root=scripts_root,
                manifest=manifest,
                bootstrap=bootstrap,
                loader=loader,
                fields=fields,
            )
            snapshot.assert_unchanged()
            return snapshot
        except BaseException:
            for member in reversed(members):
                member.stream.close()
            raise
    except BaseException as exc:
        raise RuntimeError("wss operational identity unavailable") from exc


def _validated_wss_operational_snapshot(
    *,
    package_root: Path,
    addon_path: Path,
    package_generation_validator: Callable[[], str],
) -> tuple[_WssOperationalSnapshot, dict[str, str]]:
    snapshot = _acquire_wss_operational_snapshot(
        package_root=package_root,
        addon_path=addon_path,
    )
    if snapshot is None:
        raise RuntimeError("wss operational identity unavailable")
    try:
        environment = snapshot.bind_package_generation(package_generation_validator())
        snapshot.assert_unchanged()
        return snapshot, environment
    except BaseException:
        snapshot.close()
        raise


def _validated_wss_operational_environment(
    *,
    package_root: Path,
    addon_path: Path,
    package_generation_identity: str,
) -> dict[str, str]:
    """Bind the out-of-process addon to this packaged generation.

    Source-tree execution keeps using the plaintext engineering addon and has no
    BUILD_MANIFEST.  A packaged bootstrap, however, must match the manifest-bound
    native loader before mitmdump is created.  All failures collapse to one fixed
    label so paths or malformed manifest content never reach diagnostics.
    """

    snapshot = _acquire_wss_operational_snapshot(
        package_root=package_root,
        addon_path=addon_path,
    )
    if snapshot is None:
        return {}
    try:
        return snapshot.bind_package_generation(package_generation_identity)
    finally:
        snapshot.close()


def _run_sc_command(command: list[str], *, timeout: float = 10.0) -> Any:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


_SC_DWORD_MAX = 0xFFFFFFFF
_SC_TEXT_MAX_BYTES = 1024 * 1024


@dataclass(frozen=True)
class _ScCommandResult:
    returncode: int
    stdout: str
    stderr: str


def _is_bounded_exact_sc_text(value: Any) -> bool:
    if type(value) is not str:  # noqa: E721
        return False
    if "\x00" in value or len(value) > _SC_TEXT_MAX_BYTES:
        return False
    try:
        return len(value.encode("utf-8")) <= _SC_TEXT_MAX_BYTES
    except UnicodeEncodeError:
        return False


def _project_sc_command_result(result: Any) -> _ScCommandResult | None:
    """Read each raw subprocess field once into a bounded immutable value."""

    try:
        returncode = getattr(result, "returncode")
    except Exception:
        return None
    if (
        type(returncode) is not int  # noqa: E721 - subclasses are untrusted here
        or not 0 <= returncode <= _SC_DWORD_MAX
    ):
        return None
    try:
        stdout = getattr(result, "stdout")
    except Exception:
        return None
    if not _is_bounded_exact_sc_text(stdout):
        return None
    try:
        stderr = getattr(result, "stderr")
    except Exception:
        return None
    if not _is_bounded_exact_sc_text(stderr):
        return None
    return _ScCommandResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _run_projected_sc_command(
    runner: Callable[[list[str]], Any],
    command: list[str],
) -> _ScCommandResult | None:
    try:
        raw_result = runner(command)
        return _project_sc_command_result(raw_result)
    except Exception:
        # SCM adapters are an exception boundary. Process-control BaseExceptions
        # such as KeyboardInterrupt and SystemExit must still propagate.
        return None


def _ascii_uint(
    value: str,
    *,
    minimum: int = 0,
    maximum: int = _SC_DWORD_MAX,
) -> int | None:
    if type(value) is not str or not re.fullmatch(  # noqa: E721
        r"[0-9]+",
        value,
    ):
        return None
    parsed = int(value)
    return parsed if minimum <= parsed <= maximum else None


def _sc_service_state(result: _ScCommandResult) -> int | None:
    if type(result) is not _ScCommandResult or result.returncode != 0:  # noqa: E721
        return None
    matches = re.findall(
        r"^[ \t]*STATE[ \t]*:[ \t]*([0-9]+)(?=[ \t]|$)",
        result.stdout,
        flags=re.MULTILINE,
    )
    if len(matches) != 1:
        return None
    return _ascii_uint(matches[0], minimum=1, maximum=7)


def _sc_returncode(result: _ScCommandResult) -> int | None:
    if type(result) is not _ScCommandResult:  # noqa: E721
        return None
    return result.returncode


def _sc_error_code(result: _ScCommandResult) -> int | None:
    if type(result) is not _ScCommandResult:  # noqa: E721
        return None
    output = "\n".join((result.stdout, result.stderr))
    failed_markers = re.findall(r"\bFAILED\b", output, flags=re.IGNORECASE)
    if len(failed_markers) > 1:
        return None
    if not failed_markers:
        return result.returncode
    matches = re.findall(
        r"\bFAILED[ \t]+([0-9]+)(?=[ \t\r\n:]|$)",
        output,
        flags=re.IGNORECASE,
    )
    if len(matches) != 1:
        return None
    return _ascii_uint(matches[0])


def _classify_sc_failure(result: _ScCommandResult) -> str:
    error_code = _sc_error_code(result)
    return {
        2: "failed_missing_driver",
        5: "failed_access_denied",
        577: "failed_invalid_image_hash",
        654: "failed_service_conflict",
        1058: "failed_service_conflict",
        1073: "failed_service_conflict",
        1275: "failed_driver_blocked",
    }.get(error_code, "failed_unclassified")


def _windows_elevation_state(
    checker: Callable[[], Any] | None = None,
) -> str:
    """Return a fixed monitor identity class without retaining probe details."""

    if os.name != "nt":
        return "not_applicable"
    try:
        is_admin = checker or ctypes.windll.shell32.IsUserAnAdmin
        result = is_admin()
    except (AttributeError, OSError, TypeError, ValueError):
        return "unknown"
    if type(result) is not int or result not in {0, 1}:  # noqa: E721
        return "unknown"
    return "elevated" if result == 1 else "not_elevated"


@dataclass(frozen=True)
class _ScDriverServiceConfig:
    service_type: int
    start_type: int
    error_control: int
    binary_path: str
    dependencies: tuple[str, ...]


def _single_sc_numeric_field(
    output: str,
    field_name: str,
    *,
    minimum: int = 0,
    maximum: int = _SC_DWORD_MAX,
) -> int | None:
    if (  # Exact built-in strings avoid subclass behavior at the parse boundary.
        type(output) is not str  # noqa: E721
        or type(field_name) is not str  # noqa: E721
    ):
        return None
    matches = re.findall(
        rf"^[ \t]*{re.escape(field_name)}[ \t]*:[ \t]*"
        r"([0-9]+)(?=[ \t]|$)",
        output,
        flags=re.MULTILINE,
    )
    if len(matches) != 1:
        return None
    return _ascii_uint(matches[0], minimum=minimum, maximum=maximum)


def _single_sc_text_field(output: str, field_name: str) -> str | None:
    if (
        type(output) is not str  # noqa: E721
        or type(field_name) is not str  # noqa: E721
    ):
        return None
    matches = re.findall(
        rf"^[ \t]*{re.escape(field_name)}[ \t]*:[ \t]*(.*?)[ \t]*$",
        output,
        flags=re.MULTILINE,
    )
    return matches[0].strip() if len(matches) == 1 else None


def _sc_dependencies(output: str) -> tuple[str, ...] | None:
    if type(output) is not str:  # noqa: E721
        return None
    lines = output.splitlines()
    indices = [
        index
        for index, line in enumerate(lines)
        if re.match(r"^[ \t]*DEPENDENCIES[ \t]*:", line)
    ]
    if len(indices) != 1:
        return None
    index = indices[0]
    first = lines[index].split(":", 1)[1].strip()
    dependencies = [first] if first else []
    for line in lines[index + 1 :]:
        if re.match(r"^[ \t]*[A-Z][A-Z0-9_ ]*[ \t]*:", line):
            break
        continuation = line.strip()
        if continuation:
            dependencies.append(continuation)
    return tuple(dependencies)


def _parse_sc_driver_service_config(
    result: _ScCommandResult,
) -> _ScDriverServiceConfig | None:
    if type(result) is not _ScCommandResult or result.returncode != 0:  # noqa: E721
        return None
    output = result.stdout
    service_type = _single_sc_numeric_field(output, "TYPE")
    start_type = _single_sc_numeric_field(
        output,
        "START_TYPE",
        maximum=4,
    )
    error_control = _single_sc_numeric_field(
        output,
        "ERROR_CONTROL",
        maximum=3,
    )
    binary_path = _single_sc_text_field(output, "BINARY_PATH_NAME")
    dependencies = _sc_dependencies(output)
    if (
        service_type is None
        or start_type is None
        or error_control is None
        or binary_path is None
        or dependencies is None
    ):
        return None
    if not binary_path:
        return None
    return _ScDriverServiceConfig(
        service_type=service_type,
        start_type=start_type,
        error_control=error_control,
        binary_path=binary_path,
        dependencies=dependencies,
    )


def _normalize_windows_driver_path(value: str) -> str | None:
    if type(value) is not str:  # noqa: E721
        return None
    path = value.strip()
    if not path or "\x00" in path:
        return None
    if path.startswith('"'):
        if len(path) < 2 or not path.endswith('"'):
            return None
        path = path[1:-1].strip()
        if '"' in path:
            return None
    elif '"' in path:
        return None
    path = path.replace("/", "\\")
    lowered = path.casefold()
    for prefix in ("\\\\?\\", "\\??\\", "\\dosdevices\\"):
        if lowered.startswith(prefix.casefold()):
            path = path[len(prefix) :]
            break
    normalized = ntpath.normpath(path)
    if not ntpath.isabs(normalized):
        return None
    return ntpath.normcase(normalized)


def _driver_service_config_matches(
    result: _ScCommandResult,
    *,
    expected_driver_path: str,
) -> bool:
    config = _parse_sc_driver_service_config(result)
    expected = _normalize_windows_driver_path(expected_driver_path)
    actual = (
        _normalize_windows_driver_path(config.binary_path)
        if config is not None
        else None
    )
    return bool(
        config is not None
        and config.service_type == 1
        and config.start_type == 3
        and config.error_control == 1
        and config.dependencies == ()
        and expected is not None
        and actual == expected
    )


@contextmanager
def _exclusive_windivert_service_lifecycle_lock():
    """Hold the cross-process service lifecycle mutex without waiting."""

    if os.name != "nt":
        yield False
        return
    handle = None
    kernel32 = None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
        kernel32.ReleaseMutex.restype = ctypes.c_bool
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_bool
        handle = kernel32.CreateMutexW(
            None,
            False,
            _WINDIVERT_SERVICE_LIFECYCLE_MUTEX,
        )
    except (AttributeError, OSError, TypeError, ValueError):
        yield False
        return
    if not handle:
        yield False
        return
    wait_result = int(kernel32.WaitForSingleObject(handle, 0))
    acquired = wait_result in {0x00000000, 0x00000080}
    try:
        yield acquired
    finally:
        if acquired:
            try:
                kernel32.ReleaseMutex(handle)
            except (AttributeError, OSError, TypeError, ValueError):
                pass
        try:
            kernel32.CloseHandle(handle)
        except (AttributeError, OSError, TypeError, ValueError):
            pass


@contextmanager
def _non_reentrant_windivert_service_lifecycle_entry():
    """Reject same-thread recursive lifecycle entry before any SCM adapter runs.

    Windows named mutexes are recursive for their owning thread.  This process-local
    gate therefore complements rather than replaces the named mutex: other threads
    and processes still contend on the existing cross-process lock, while callbacks
    from an untrusted runner/result projection cannot enter repair, prepare, or
    release again on the same thread, even through a different object instance.
    """

    if getattr(_WINDIVERT_SERVICE_LIFECYCLE_LOCAL, "active", False):
        yield False
        return
    _WINDIVERT_SERVICE_LIFECYCLE_LOCAL.active = True
    try:
        yield True
    finally:
        try:
            del _WINDIVERT_SERVICE_LIFECYCLE_LOCAL.active
        except AttributeError:
            pass


def _file_identity_tuple(file_stat: os.stat_result) -> tuple[int, ...] | None:
    try:
        link_count = getattr(file_stat, "st_nlink")
        values = (
            getattr(file_stat, "st_dev", None),
            getattr(file_stat, "st_ino", None),
            link_count,
            getattr(file_stat, "st_size", None),
            getattr(file_stat, "st_mtime_ns", None),
            getattr(file_stat, "st_ctime_ns", None),
        )
    except Exception:
        return None
    if type(link_count) is not int or link_count != 1:  # noqa: E721
        return None
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        return None
    if int(values[1]) <= 0 or int(values[3]) < 0:
        return None
    return tuple(int(value) for value in values)


def _stable_file_digest(path: Path) -> tuple[tuple[int, ...], str] | None:
    try:
        link_stat = path.lstat()
        reparse_flag = int(getattr(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        if stat_module.S_ISLNK(link_stat.st_mode) or (
            int(getattr(link_stat, "st_file_attributes", 0)) & reparse_flag
        ):
            return None
        before = path.stat()
        if not stat_module.S_ISREG(before.st_mode):
            return None
        before_identity = _file_identity_tuple(before)
        if before_identity is None:
            return None
        digest = _sha256_file(path)
        after_identity = _file_identity_tuple(path.stat())
    except (OSError, TypeError, ValueError):
        return None
    if before_identity != after_identity:
        return None
    return before_identity, digest.casefold()


def _held_file_identity(file_stat: os.stat_result) -> tuple[int, ...] | None:
    """Keep the immutable object identity fields stable across a Windows handle."""

    identity = _file_identity_tuple(file_stat)
    return identity[:4] if identity is not None else None


@dataclass
class _HeldStableFile:
    """One no-reparse Windows file object retained through stale-service repair."""

    path: Path
    stream: BinaryIO = field(repr=False)
    identity: tuple[int, ...]
    sha256: str

    def _digest_from_held_stream(self) -> str | None:
        try:
            self.stream.seek(0)
            digest = hashlib.sha256()
            for chunk in iter(lambda: self.stream.read(1024 * 1024), b""):
                digest.update(chunk)
            self.stream.seek(0)
        except (OSError, ValueError):
            return None
        return digest.hexdigest().casefold()

    def assert_unchanged(self) -> bool:
        """Require the held object and its original path to still be one generation."""

        try:
            held_stat = os.fstat(self.stream.fileno())
            held_identity = _held_file_identity(held_stat)
            link_stat = self.path.lstat()
            reparse_flag = int(
                getattr(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            )
            if stat_module.S_ISLNK(link_stat.st_mode) or (
                int(getattr(link_stat, "st_file_attributes", 0)) & reparse_flag
            ):
                return False
            path_stat = self.path.stat()
            path_identity = _held_file_identity(path_stat)
        except (OSError, TypeError, ValueError):
            return False
        return bool(
            stat_module.S_ISREG(held_stat.st_mode)
            and held_identity == self.identity
            and path_identity == self.identity
            and self._digest_from_held_stream() == self.sha256
        )

    def close(self) -> None:
        try:
            self.stream.close()
        except (OSError, ValueError):
            pass


def _open_held_stable_file(path: Path) -> _HeldStableFile | None:
    """Open one regular file with no write/delete sharing and bind its digest."""

    if os.name != "nt":
        return None
    handle: int | None = None
    stream: BinaryIO | None = None
    kernel32: Any | None = None
    try:
        link_stat = path.lstat()
        reparse_flag = int(getattr(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        if stat_module.S_ISLNK(link_stat.st_mode) or (
            int(getattr(link_stat, "st_file_attributes", 0)) & reparse_flag
        ):
            return None
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        kernel32.CreateFileW.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_bool
        raw_handle = kernel32.CreateFileW(
            str(path),
            0x80000000,  # GENERIC_READ
            0x00000001,  # FILE_SHARE_READ only: deny later writes and replacement.
            None,
            3,  # OPEN_EXISTING
            0x00200080,  # FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT
            None,
        )
        invalid_handle = ctypes.c_void_p(-1).value
        if raw_handle is None or int(raw_handle) == invalid_handle:
            return None
        handle = int(raw_handle)
        import msvcrt

        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        handle = None  # The CRT stream now owns the native handle.
        stream = os.fdopen(descriptor, "rb", closefd=True)
        held_stat = os.fstat(stream.fileno())
        identity = _held_file_identity(held_stat)
        if not stat_module.S_ISREG(held_stat.st_mode) or identity is None:
            return None
        snapshot = _HeldStableFile(
            path=path,
            stream=stream,
            identity=identity,
            sha256="",
        )
        digest = snapshot._digest_from_held_stream()
        if digest is None:
            return None
        snapshot.sha256 = digest
        if not snapshot.assert_unchanged():
            return None
        stream = None
        return snapshot
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    finally:
        if stream is not None:
            try:
                stream.close()
            except (OSError, ValueError):
                pass
        if handle is not None and kernel32 is not None:
            try:
                kernel32.CloseHandle(handle)
            except (AttributeError, OSError, TypeError, ValueError):
                pass


@dataclass(frozen=True)
class _AuthenticodeIdentity:
    status: str
    signer_thumbprint: str


def _windows_system_powershell() -> Path | None:
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetSystemDirectoryW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
        kernel32.GetSystemDirectoryW.restype = ctypes.c_uint32
        buffer = ctypes.create_unicode_buffer(32768)
        length = int(kernel32.GetSystemDirectoryW(buffer, len(buffer)))
        if length <= 0 or length >= len(buffer):
            return None
        executable = (
            Path(buffer.value)
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
        resolved = executable.resolve(strict=True)
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    return resolved if resolved.is_file() else None


def _read_authenticode_identity(path: Path) -> _AuthenticodeIdentity | None:
    command = (
        "$ErrorActionPreference='Stop';"
        "$p=[Environment]::GetEnvironmentVariable("
        "'_BIDKING_WINDIVERT_SIGNATURE_PATH','Process');"
        "$s=Get-AuthenticodeSignature -LiteralPath $p;"
        "$t=if($null -ne $s.SignerCertificate)"
        "{$s.SignerCertificate.Thumbprint}else{''};"
        "[Console]::Out.Write($s.Status.ToString());"
        "[Console]::Out.Write([Environment]::NewLine);"
        "[Console]::Out.Write($t)"
    )
    powershell_path = _windows_system_powershell()
    if powershell_path is None:
        return None
    child_env = os.environ.copy()
    child_env["_BIDKING_WINDIVERT_SIGNATURE_PATH"] = str(path)
    child_env["PSModulePath"] = str(
        powershell_path.parent / "Modules"
    )
    try:
        result = subprocess.run(
            [
                str(powershell_path),
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                command,
            ],
            env=child_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    lines = str(result.stdout or "").splitlines()
    if result.returncode != 0 or len(lines) != 2:
        return None
    status = lines[0].strip()
    thumbprint = lines[1].strip().casefold()
    if not status or not re.fullmatch(r"[0-9a-f]{40}", thumbprint):
        return None
    return _AuthenticodeIdentity(status=status, signer_thumbprint=thumbprint)


@dataclass(frozen=True)
class _StaleBidKingDriverIdentity:
    normalized_path: str | None
    driver_file_identity: tuple[int, ...]
    driver_sha256: str
    signer_thumbprint: str
    package_manifest_sha256: str
    manifest_file_identity: tuple[int, ...]
    held_driver: _HeldStableFile | None = field(default=None, compare=False, repr=False)
    held_manifest: _HeldStableFile | None = field(default=None, compare=False, repr=False)

    def assert_unchanged(self) -> bool:
        return bool(
            (self.held_driver is None or self.held_driver.assert_unchanged())
            and (self.held_manifest is None or self.held_manifest.assert_unchanged())
        )

    def close(self) -> None:
        if self.held_manifest is not None:
            self.held_manifest.close()
        if self.held_driver is not None:
            self.held_driver.close()


def _inspect_known_old_bidking_driver(
    driver_path: str,
) -> _StaleBidKingDriverIdentity | None:
    normalized = _normalize_windows_driver_path(driver_path)
    if normalized is None or normalized.startswith("\\\\"):
        return None
    drive, _tail = ntpath.splitdrive(normalized)
    if not drive.endswith(":"):
        return None
    candidate = Path(normalized)
    relative_count = len(_KNOWN_OLD_BIDKING_WINDIVERT_RELATIVE_PARTS)
    if len(candidate.parts) <= relative_count:
        return None
    if tuple(part.casefold() for part in candidate.parts[-relative_count:]) != tuple(
        part.casefold() for part in _KNOWN_OLD_BIDKING_WINDIVERT_RELATIVE_PARTS
    ):
        return None
    package_root = candidate.parents[relative_count - 1]
    manifest_path = package_root / "BUILD_MANIFEST.txt"
    try:
        if _normalize_windows_driver_path(str(candidate.resolve(strict=True))) != normalized:
            return None
        resolved_manifest = manifest_path.resolve(strict=True)
        if resolved_manifest != manifest_path.absolute():
            return None
    except OSError:
        return None

    held_driver = _open_held_stable_file(candidate)
    held_manifest = _open_held_stable_file(manifest_path)
    if held_driver is None or held_manifest is None:
        if held_manifest is not None:
            held_manifest.close()
        if held_driver is not None:
            held_driver.close()
        return None
    try:
        if (
            held_driver.sha256 != KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256
            or held_manifest.sha256 not in KNOWN_PUBLISHED_BIDKING_PACKAGE_MANIFEST_SHA256S
        ):
            return None
        signature = _read_authenticode_identity(candidate)
        if signature is None or (
            signature.status != "Valid"
            or signature.signer_thumbprint != KNOWN_BIDKING_WINDIVERT_SIGNER_THUMBPRINT
        ):
            return None
        if not held_driver.assert_unchanged() or not held_manifest.assert_unchanged():
            return None
        identity = _StaleBidKingDriverIdentity(
            normalized_path=normalized,
            driver_file_identity=held_driver.identity,
            driver_sha256=held_driver.sha256,
            signer_thumbprint=signature.signer_thumbprint,
            package_manifest_sha256=held_manifest.sha256,
            manifest_file_identity=held_manifest.identity,
            held_driver=held_driver,
            held_manifest=held_manifest,
        )
        held_driver = None
        held_manifest = None
        return identity
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return None
    finally:
        if held_manifest is not None:
            held_manifest.close()
        if held_driver is not None:
            held_driver.close()


@dataclass(frozen=True)
class _ScProductServiceStatus:
    service_name: str
    state: int


def _parse_sc_product_service_status(
    result: _ScCommandResult,
) -> _ScProductServiceStatus | None:
    if type(result) is not _ScCommandResult or result.returncode != 0:  # noqa: E721
        return None
    output = result.stdout
    names = re.findall(
        r"^[ \t]*SERVICE_NAME[ \t]*:[ \t]*(\S+)[ \t]*$",
        output,
        flags=re.MULTILINE,
    )
    states = re.findall(
        r"^[ \t]*STATE[ \t]*:[ \t]*([0-9]+)(?=[ \t]|$)",
        output,
        flags=re.MULTILINE,
    )
    if len(names) != 1 or len(states) != 1:
        return None
    state = _ascii_uint(states[0], minimum=1, maximum=7)
    if state is None:
        return None
    return _ScProductServiceStatus(service_name=names[0], state=state)


@dataclass(frozen=True)
class _StaleProductServiceEvidence:
    service_status: _ScProductServiceStatus
    service_config: _ScDriverServiceConfig
    driver_identity: _StaleBidKingDriverIdentity


@dataclass
class WinDivertStaleProductServiceRepair:
    """Explicitly delete one proven stale BidKing-owned stopped driver service."""

    runner: Callable[[list[str]], Any] = field(default=_run_sc_command, repr=False)
    service_name: str = WINDIVERT_PRELOAD_SERVICE_NAME
    elevation_checker: Callable[[], Any] | None = field(default=None, repr=False)
    lock_factory: Callable[[], Any] = field(
        default=_exclusive_windivert_service_lifecycle_lock,
        repr=False,
    )
    inventory_reader: Callable[[str], _StaleBidKingDriverIdentity | None] = field(
        default=_inspect_known_old_bidking_driver,
        repr=False,
    )
    state: str = field(default="not_started", init=False)
    phase: str = field(default="not_started", init=False)

    def _run(self, command: list[str]) -> _ScCommandResult | None:
        return _run_projected_sc_command(self.runner, command)

    def _fixed_failure(self, result: _ScCommandResult | None) -> str:
        if result is None:
            return "failed_unclassified"
        return {
            5: "failed_access_denied",
            1072: "failed_marked_for_delete",
        }.get(_sc_error_code(result), "failed_unclassified")

    def _inspect_once(
        self,
        *,
        current_driver_path: str,
    ) -> _StaleProductServiceEvidence | str:
        self.phase = "query_product"
        status_result = self._run(["sc.exe", "query", self.service_name])
        if status_result is None:
            return "failed_unclassified"
        if _sc_returncode(status_result) != 0:
            if _sc_error_code(status_result) == 1060:
                return "not_present"
            return self._fixed_failure(status_result)
        status = _parse_sc_product_service_status(status_result)
        if (
            status is None
            or status.service_name != WINDIVERT_PRELOAD_SERVICE_NAME
            or status.state != 1
        ):
            return "failed_service_conflict"

        self.phase = "verify_product"
        config_result = self._run(["sc.exe", "qc", self.service_name])
        if config_result is None:
            return "failed_unclassified"
        if _sc_returncode(config_result) != 0:
            if _sc_error_code(config_result) == 1060:
                return "not_present"
            return self._fixed_failure(config_result)
        config = _parse_sc_driver_service_config(config_result)
        if (
            config is None
            or config.service_type != 1
            or config.start_type != 3
            or config.error_control != 1
            or config.dependencies != ()
        ):
            return "failed_service_conflict"
        normalized_path = _normalize_windows_driver_path(config.binary_path)
        if normalized_path is None or (
            ntpath.basename(normalized_path).casefold() != "windivert64.sys"
        ):
            return "failed_service_conflict"
        if normalized_path == current_driver_path:
            # The launch preloader already rejected the existing service while
            # verifying the current driver.  With an identical absolute path,
            # directory layout is no generation boundary: an old package may
            # have been overwritten in place.  Never treat this as frozen-old
            # inventory and never mutate SCM from this state.
            return "failed_same_path_mixed_generation"
        try:
            identity = self.inventory_reader(config.binary_path)
        except (OSError, subprocess.SubprocessError, TypeError, ValueError):
            identity = None
        if (
            identity is None
            or identity.normalized_path != normalized_path
            or identity.driver_sha256
            != KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256
            or identity.signer_thumbprint
            != KNOWN_BIDKING_WINDIVERT_SIGNER_THUMBPRINT
            or identity.package_manifest_sha256
            not in KNOWN_PUBLISHED_BIDKING_PACKAGE_MANIFEST_SHA256S
        ):
            return "failed_service_conflict"
        return _StaleProductServiceEvidence(
            service_status=status,
            service_config=config,
            driver_identity=identity,
        )

    def repair(self, current_driver_path: Path) -> str:
        with _non_reentrant_windivert_service_lifecycle_entry() as entered:
            if not entered:
                return "failed_repair_busy"
            return self._repair(current_driver_path)

    def _repair(self, current_driver_path: Path) -> str:
        self.state = "not_started"
        self.phase = "authorize"
        if os.name != "nt":
            self.state = "failed_not_applicable"
            return self.state
        if self.service_name != WINDIVERT_PRELOAD_SERVICE_NAME:
            self.state = "failed_service_conflict"
            return self.state
        if _windows_elevation_state(self.elevation_checker) != "elevated":
            self.state = "failed_not_elevated"
            return self.state
        try:
            current_driver = current_driver_path.resolve(strict=True)
        except OSError:
            self.state = "failed_untrusted_driver"
            return self.state
        current_normalized = _normalize_windows_driver_path(str(current_driver))
        if current_normalized is None:
            self.state = "failed_untrusted_driver"
            return self.state

        self.phase = "lock"
        try:
            lock_context = self.lock_factory()
            with lock_context as acquired:
                if not acquired:
                    self.state = "failed_repair_busy"
                    return self.state
                first: _StaleProductServiceEvidence | str | None = None
                second: _StaleProductServiceEvidence | str | None = None
                try:
                    first = self._inspect_once(
                        current_driver_path=current_normalized,
                    )
                    if isinstance(first, str):
                        self.state = first
                        if first == "not_present":
                            self.phase = "complete"
                        return self.state
                    second = self._inspect_once(
                        current_driver_path=current_normalized,
                    )
                    if isinstance(second, str):
                        self.state = second
                        if second == "not_present":
                            self.phase = "complete"
                        return self.state
                    if second != first or not (
                        first.driver_identity.assert_unchanged()
                        and second.driver_identity.assert_unchanged()
                    ):
                        self.state = "failed_service_conflict"
                        return self.state

                    self.phase = "delete_product"
                    deleted = self._run(["sc.exe", "delete", self.service_name])
                    if deleted is None:
                        self.state = "failed_unclassified"
                        return self.state
                    if _sc_returncode(deleted) == 0:
                        self.state = "repaired_restart_required"
                        self.phase = "complete"
                        return self.state
                    if _sc_error_code(deleted) == 1060:
                        self.state = "not_present"
                        self.phase = "complete"
                        return self.state
                    self.state = self._fixed_failure(deleted)
                    return self.state
                finally:
                    if isinstance(second, _StaleProductServiceEvidence):
                        second.driver_identity.close()
                    if isinstance(first, _StaleProductServiceEvidence):
                        first.driver_identity.close()
        except (AttributeError, OSError, TypeError, ValueError):
            self.state = "failed_repair_busy"
            return self.state

    def confirm_absent_after_repair(self) -> str:
        """Read back 1060 after a successful delete without deleting again."""

        with _non_reentrant_windivert_service_lifecycle_entry() as entered:
            if not entered:
                return "failed_repair_busy"
            if (
                self.service_name != WINDIVERT_PRELOAD_SERVICE_NAME
                or self.state != "repaired_restart_required"
            ):
                return "failed_service_conflict"
            self.phase = "lock"
            try:
                with self.lock_factory() as acquired:
                    if not acquired:
                        self.state = "failed_repair_busy"
                        return self.state
                    self.phase = "query_product"
                    result = self._run(["sc.exe", "query", self.service_name])
                    if result is None:
                        self.state = "failed_unclassified"
                        return self.state
                    if _sc_returncode(result) == 0:
                        # Delete was accepted but the service is still visible.
                        # Never issue a second delete from the automatic path.
                        self.state = "repaired_restart_required"
                        return self.state
                    if _sc_error_code(result) == 1060:
                        self.state = "not_present"
                        self.phase = "complete"
                        return self.state
                    self.state = self._fixed_failure(result)
                    return self.state
            except (AttributeError, OSError, TypeError, ValueError):
                self.state = "failed_repair_busy"
                return self.state


@dataclass
class WinDivertCompatibilityPreloader:
    """Preload the audited driver without touching an existing WinDivert service.

    WinDivert 2.2.2 reuses any existing service named ``WinDivert`` without
    validating its binary path. A stopped third-party or stale service can
    therefore prevent the bundled redirector from loading its own driver. When
    that exact conflict is present, an isolated product-owned demand-start
    service loads the same audited driver first. The redirector then opens the
    already-present ``\\\\.\\WinDivert`` device and never mutates the conflicting
    service.
    """

    runner: Callable[[list[str]], Any] = field(default=_run_sc_command, repr=False)
    service_name: str = WINDIVERT_PRELOAD_SERVICE_NAME
    lock_factory: Callable[[], Any] = field(
        default=_exclusive_windivert_service_lifecycle_lock,
        repr=False,
    )
    state: str = field(default="not_applicable", init=False)
    phase: str = field(default="not_started", init=False)
    _service_ownership: str | None = field(default=None, init=False, repr=False)
    _expected_driver_path: str | None = field(default=None, init=False, repr=False)
    _lock_held: bool = field(default=False, init=False, repr=False)
    _release_deadline: float | None = field(default=None, init=False, repr=False)
    release_result: str = field(default="not_requested", init=False)

    def _run(self, command: list[str]) -> _ScCommandResult | None:
        if self._release_deadline is not None:
            remaining = self._release_deadline - time.monotonic()
            if remaining <= 0:
                self.release_result = "deadline_exceeded"
                return None
            if self.runner is _run_sc_command:
                return _run_projected_sc_command(
                    lambda args: _run_sc_command(args, timeout=remaining), command
                )
        return _run_projected_sc_command(self.runner, command)

    def _forget_service_ownership(self) -> None:
        self._service_ownership = None
        self._expected_driver_path = None

    def _claim_service_ownership(self, kind: str, driver: Path) -> None:
        self._service_ownership = kind
        self._expected_driver_path = str(driver)

    def _cleanup_product_service(self, *, preserve_failure: bool = False) -> None:
        saved_state = self.state
        saved_phase = self.phase
        self.phase = "cleanup"
        try:
            if not self._service_ownership or not self._expected_driver_path:
                return
            config = self._run(["sc.exe", "qc", self.service_name])
            if config is None:
                self.state = "failed_unclassified"
                self._forget_service_ownership()
                return
            if _sc_returncode(config) != 0:
                self.state = _classify_sc_failure(config)
                self._forget_service_ownership()
                return
            if not _driver_service_config_matches(
                config,
                expected_driver_path=self._expected_driver_path,
            ):
                self.state = "failed_service_conflict"
                self._forget_service_ownership()
                return
            stopped = self._run(["sc.exe", "stop", self.service_name])
            if stopped is None:
                self.state = "failed_unclassified"
                self._forget_service_ownership()
                return
            if (
                _sc_returncode(stopped) != 0
                and _sc_error_code(stopped) != 1062
            ):
                self.state = _classify_sc_failure(stopped)
                self._forget_service_ownership()
                return
            # Stop and delete are separate trust boundaries. Re-read SCM config
            # so a replaced service is never deleted merely because its name
            # matches.
            config = self._run(["sc.exe", "qc", self.service_name])
            if config is None:
                self.state = "failed_unclassified"
                self._forget_service_ownership()
                return
            if _sc_returncode(config) != 0:
                self.state = _classify_sc_failure(config)
                self._forget_service_ownership()
                return
            if not _driver_service_config_matches(
                config,
                expected_driver_path=self._expected_driver_path,
            ):
                self.state = "failed_service_conflict"
                self._forget_service_ownership()
                return
            deleted = self._run(["sc.exe", "delete", self.service_name])
            if deleted is None:
                self.state = "failed_unclassified"
            elif _sc_returncode(deleted) != 0:
                self.state = _classify_sc_failure(deleted)
            self._forget_service_ownership()
        finally:
            if preserve_failure:
                self.state = saved_state
                self.phase = saved_phase

    def prepare(self, driver_path: Path) -> str:
        with _non_reentrant_windivert_service_lifecycle_entry() as entered:
            if not entered:
                return "failed_service_conflict"
            return self._prepare(driver_path)

    def _prepare(self, driver_path: Path) -> str:
        self.state = "not_applicable"
        self.phase = "validate_driver"
        self._forget_service_ownership()
        if os.name != "nt":
            self.phase = "complete"
            return self.state
        try:
            driver = driver_path.resolve(strict=True)
        except OSError:
            self.state = "failed_missing_driver"
            return self.state
        try:
            driver_sha256 = _sha256_file(driver)
        except OSError:
            self.state = "failed_missing_driver"
            return self.state
        if driver_sha256 != KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256:
            self.state = "failed_untrusted_driver"
            return self.state
        if not self._lock_held:
            try:
                with self.lock_factory() as acquired:
                    if not acquired:
                        self.state = "failed_service_conflict"
                        self.phase = "query_product"
                        return self.state
                    self._lock_held = True
                    try:
                        return self._prepare(driver_path)
                    finally:
                        self._lock_held = False
            except (AttributeError, OSError, TypeError, ValueError):
                self.state = "failed_service_conflict"
                self.phase = "query_product"
                return self.state

        self.phase = "query_legacy"
        legacy = self._run(["sc.exe", "query", "WinDivert"])
        if legacy is None:
            self.state = "failed_unclassified"
            return self.state
        legacy_returncode = _sc_returncode(legacy)
        if legacy_returncode != 0:
            self.state = (
                "not_needed"
                if _sc_error_code(legacy) == 1060
                else _classify_sc_failure(legacy)
            )
            return self.state
        legacy_state = _sc_service_state(legacy)
        if legacy_state == 4:
            self.state = "not_needed"
            return self.state
        if legacy_state != 1:
            self.state = "failed_service_conflict"
            return self.state

        self.phase = "query_product"
        existing = self._run(["sc.exe", "query", self.service_name])
        if existing is None:
            self.state = "failed_unclassified"
            return self.state
        existing_returncode = _sc_returncode(existing)
        if existing_returncode == 0:
            existing_state = _sc_service_state(existing)
            if existing_state not in {1, 4}:
                self.state = "failed_service_conflict"
                return self.state
            self.phase = "verify_product"
            config = self._run(["sc.exe", "qc", self.service_name])
            if config is None:
                self.state = "failed_unclassified"
                return self.state
            if _sc_returncode(config) != 0:
                self.state = _classify_sc_failure(config)
                return self.state
            if not _driver_service_config_matches(
                config,
                expected_driver_path=str(driver),
            ):
                self.state = "failed_service_conflict"
                return self.state
            self._claim_service_ownership("verified_existing", driver)
            if existing_state == 4:
                self.state = "already_preloaded"
                self.phase = "complete"
                return self.state
        elif _sc_error_code(existing) != 1060:
            self.state = _classify_sc_failure(existing)
            return self.state
        else:
            self.phase = "create_product"
            created = self._run(
                [
                    "sc.exe",
                    "create",
                    self.service_name,
                    "type=",
                    "kernel",
                    "start=",
                    "demand",
                    "error=",
                    "normal",
                    "binPath=",
                    str(driver),
                ]
            )
            if created is None or _sc_returncode(created) != 0:
                self.state = (
                    "failed_unclassified"
                    if created is None
                    else _classify_sc_failure(created)
                )
                return self.state
            self._claim_service_ownership("created_this_run", driver)
            self.phase = "verify_product"
            config = self._run(["sc.exe", "qc", self.service_name])
            if config is None:
                self.state = "failed_unclassified"
                self._forget_service_ownership()
                return self.state
            if _sc_returncode(config) != 0:
                self.state = _classify_sc_failure(config)
                self._forget_service_ownership()
                return self.state
            if not _driver_service_config_matches(
                config,
                expected_driver_path=str(driver),
            ):
                self.state = "failed_service_conflict"
                self._forget_service_ownership()
                return self.state

        self.phase = "start_product"
        started = self._run(["sc.exe", "start", self.service_name])
        if started is None or (
            _sc_returncode(started) != 0
            and _sc_error_code(started) != 1056
        ):
            self.state = (
                "failed_unclassified"
                if started is None
                else _classify_sc_failure(started)
            )
            self._cleanup_product_service(preserve_failure=True)
            return self.state

        self.phase = "query_running"
        verified = self._run(["sc.exe", "query", self.service_name])
        if verified is None:
            self.state = "failed_unclassified"
            self._cleanup_product_service(preserve_failure=True)
            return self.state
        if _sc_returncode(verified) != 0:
            self.state = _classify_sc_failure(verified)
            self._cleanup_product_service(preserve_failure=True)
            return self.state
        if _sc_service_state(verified) != 4:
            self.state = "failed_unclassified"
            self._cleanup_product_service(preserve_failure=True)
            return self.state

        self.state = "preloaded"
        self.phase = "complete"
        return self.state

    def release(self, *, deadline: float | None = None) -> str:
        self._release_deadline = deadline
        owned = bool(self._service_ownership)
        self.release_result = "not_owned" if not owned else "unknown"
        with _non_reentrant_windivert_service_lifecycle_entry() as entered:
            if not entered:
                self.release_result = "owner_busy"
                return self.release_result
            self._release()
            if owned and deadline is not None and self.release_result != "deadline_exceeded":
                # Command success is not unload proof. Only SCM absence after
                # this launch's identity-checked cleanup confirms removal.
                result = self._run(["sc.exe", "query", self.service_name])
                if self.state.startswith("failed_"):
                    self.release_result = "cleanup_failed"
                elif result is not None and _sc_error_code(result) == 1060:
                    self.release_result = "absent"
                elif result is not None and _sc_returncode(result) == 0:
                    self.release_result = "remaining"
                elif self.release_result != "deadline_exceeded":
                    self.release_result = "unknown"
        return self.release_result

    def _release(self) -> None:
        if self._service_ownership:
            try:
                with self.lock_factory() as acquired:
                    if not acquired:
                        self.state = "failed_service_conflict"
                        self.phase = "cleanup"
                        return
                    self._cleanup_product_service()
            except (AttributeError, OSError, TypeError, ValueError):
                self.state = "failed_service_conflict"
                self.phase = "cleanup"


@dataclass(frozen=True)
class _WinDivertAutoRepairOutcome:
    preload_state: str
    repair_state: str = "not_started"
    repair_phase: str = "not_started"


def _attempt_automatic_stale_product_service_repair(
    preloader: WinDivertCompatibilityPreloader,
    driver_path: Path,
    *,
    enabled: bool,
    helper_attached: bool,
    accepted_frames: int,
    active_session_id: str,
    repairer_factory: Callable[[], Any] = WinDivertStaleProductServiceRepair,
) -> _WinDivertAutoRepairOutcome:
    """Repair only the exact pre-launch stale-product conflict.

    The caller supplies current-launch capture evidence explicitly. Unknown or
    already-active capture state is fail-closed and never reaches the repair API.
    """

    initial_state = str(preloader.state or "")
    if (
        enabled is not True
        or initial_state != "failed_service_conflict"
        or str(preloader.phase or "") != "verify_product"
        or type(helper_attached) is not bool  # noqa: E721
        or helper_attached
        or type(accepted_frames) is not int  # noqa: E721
        or accepted_frames != 0
        or type(active_session_id) is not str  # noqa: E721
        or bool(active_session_id)
    ):
        return _WinDivertAutoRepairOutcome(preload_state=initial_state)

    try:
        repairer = repairer_factory()
        repair_state = repairer.repair(driver_path)
    except (AttributeError, OSError, TypeError, ValueError):
        return _WinDivertAutoRepairOutcome(
            preload_state=initial_state,
            repair_state="failed_unclassified",
        )

    repair_phase = str(getattr(repairer, "phase", "not_started") or "not_started")
    if repair_state == "failed_same_path_mixed_generation":
        preloader.state = "failed_same_path_mixed_generation"
        preloader.phase = (
            repair_phase
            if repair_phase in _WINDIVERT_PRELOAD_PHASES
            else "verify_product"
        )
        return _WinDivertAutoRepairOutcome(
            preload_state=preloader.state,
            repair_state=repair_state,
            repair_phase=repair_phase,
        )
    delete_accepted = repair_state == "repaired_restart_required"
    if delete_accepted:
        try:
            repair_state = repairer.confirm_absent_after_repair()
            repair_phase = str(
                getattr(repairer, "phase", "not_started") or "not_started"
            )
        except (AttributeError, OSError, TypeError, ValueError):
            repair_state = "failed_unclassified"

    if delete_accepted:
        # Delete was accepted. Even a 1060 readback proves only registration
        # absence, not that the old driver/handles are unloaded; an unknown
        # readback is even less authority to continue. Stop this launch and
        # require a reboot; never retry in-process.
        preloader.state = "repaired_restart_required"
        preloader.phase = "complete"
        return _WinDivertAutoRepairOutcome(
            preload_state=preloader.state,
            repair_state=str(repair_state),
            repair_phase=repair_phase,
        )

    return _WinDivertAutoRepairOutcome(
        preload_state=initial_state,
        repair_state=(
            str(repair_state)
            if str(repair_state) in _WINDIVERT_REPAIR_STATES
            else "failed_unclassified"
        ),
        repair_phase=(
            repair_phase
            if repair_phase in _WINDIVERT_REPAIR_PHASES
            else "not_started"
        ),
    )


def inspect_runtime_compatibility(
    diagnostics: WssHelperDiagnostics,
    *,
    mitmdump_path: Path,
) -> dict[str, str]:
    """Return fixed, privacy-safe evidence for the exact known runtime failure."""

    if diagnostics.primary_error_code() != _REDIRECT_DAEMON_PREMATURE_EXIT:
        return {}
    try:
        driver_sha256 = _sha256_file(_bundled_windivert_driver(mitmdump_path))
    except OSError:
        return {}
    if driver_sha256 != KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256:
        return {}
    return {
        "runtime_compatibility_state": RUNTIME_COMPATIBILITY_FAILED_STATE,
        "windivert_driver_sha256": driver_sha256,
    }


def _helper_error_details(
    diagnostics: WssHelperDiagnostics,
    *,
    runtime_compatibility: Mapping[str, str] | None = None,
) -> tuple[str, str, str]:
    error_code = diagnostics.primary_error_code()
    if error_code == _REDIRECT_DAEMON_PREMATURE_EXIT:
        error_code = (
            _RUNTIME_COMPATIBILITY_ERROR
            if (runtime_compatibility or {}).get("runtime_compatibility_state")
            == RUNTIME_COMPATIBILITY_FAILED_STATE
            else "wss_redirector_start_failed"
        )
    error_code = error_code or "wss_capture_helper_exit_unclassified"
    message, hint = _WSS_HELPER_ERROR_DETAILS[error_code]
    return error_code, message, hint


def _write_wss_helper_status(
    log_dir: Path,
    *,
    phase: str,
    helper_pid: int,
    launch_id: str,
    diagnostics: WssHelperDiagnostics,
    helper_exit_code: int | None = None,
    error_code: str | None = None,
    runtime_compatibility: Mapping[str, str] | None = None,
    redirector_observation_state: str | None = None,
    windivert_preload_state: str | None = None,
    windivert_preload_phase: str | None = None,
    monitor_elevation_state: str | None = None,
    package_generation_identity: str = "",
    runtime_identity: str = "",
    component_identity: str = "",
    owner_sid: str = "",
    monitor_started_at_unix_ms: int = 0,
) -> None:
    runtime_evidence = dict(runtime_compatibility or {})
    runtime_compatibility_state = str(
        runtime_evidence.get("runtime_compatibility_state") or ""
    )
    windivert_driver_sha256 = str(
        runtime_evidence.get("windivert_driver_sha256") or ""
    )
    compatibility_failed = (
        runtime_compatibility_state == RUNTIME_COMPATIBILITY_FAILED_STATE
        and windivert_driver_sha256 == KNOWN_HISTORICAL_BLOCKED_WINDIVERT_SHA256
    )
    observed_redirector_state = str(redirector_observation_state or "")
    if observed_redirector_state not in _REDIRECTOR_OBSERVATION_STATES:
        observed_redirector_state = "unknown"
    diagnostic_redirector_state = diagnostics.redirector_state()
    redirector_state = (
        diagnostic_redirector_state
        if diagnostic_redirector_state != "unknown"
        else observed_redirector_state
    )
    safe_error_codes = frozenset(
        {
            *_WSS_HELPER_ERROR_DETAILS,
            "wss_child_lifecycle_guard_failed",
        }
    )
    safe_error_code = str(error_code or "")
    if safe_error_code not in safe_error_codes:
        safe_error_code = ""
    safe_phase = str(phase or "")
    if safe_phase not in _WSS_HELPER_PHASES:
        safe_phase = "unknown"
    payload: dict[str, Any] = {
        "schema_version": WSS_HELPER_STATUS_SCHEMA_VERSION,
        "owner_kind": "bidking_wss_monitor",
        "owner_sid": str(owner_sid or ""),
        "package_generation_identity": str(package_generation_identity or ""),
        "runtime_identity": str(runtime_identity or ""),
        "component_identity": str(component_identity or ""),
        "monitor_epoch": str(launch_id or ""),
        "monitor_started_at_unix_ms": int(monitor_started_at_unix_ms),
        "ts_unix_ms": int(time.time_ns() // 1_000_000),
        "phase": safe_phase,
        "monitor_pid": os.getpid(),
        "helper_pid": int(helper_pid),
        "launch_id": str(launch_id or ""),
        "redirector_state": (
            "start_failed" if compatibility_failed else redirector_state
        ),
        "windivert_state": (
            "known_historical_blocked_driver"
            if compatibility_failed
            else diagnostics.windivert_state()
        ),
    }
    if helper_exit_code is not None:
        payload["helper_exit_code"] = int(helper_exit_code)
    if safe_error_code:
        payload["error_code"] = safe_error_code
    safe_preload_state = str(windivert_preload_state or "")
    if safe_preload_state in _WINDIVERT_PRELOAD_STATES:
        payload["windivert_preload_state"] = safe_preload_state
    safe_preload_phase = str(windivert_preload_phase or "")
    if safe_preload_phase in _WINDIVERT_PRELOAD_PHASES:
        payload["windivert_preload_phase"] = safe_preload_phase
    safe_elevation_state = str(monitor_elevation_state or "")
    if safe_elevation_state in _MONITOR_ELEVATION_STATES:
        payload["monitor_elevation_state"] = safe_elevation_state
    if compatibility_failed:
        payload["runtime_compatibility_state"] = runtime_compatibility_state
        payload["windivert_driver_sha256"] = windivert_driver_sha256
    try:
        _atomic_write_json(log_dir / WSS_HELPER_STATUS_FILENAME, payload)
    except OSError:
        # Diagnostics must never take down capture or the calculator. The normal
        # capture status remains the primary health path when this sidecar cannot
        # be written.
        return


def _bound_game_running_state(pid: int, started_at: float) -> bool | None:
    """Check the original instance, even if another same-name game now exists."""
    try:
        import psutil
    except ImportError:
        return None
    try:
        process = psutil.Process(pid)
        if process.create_time() != started_at:
            return False
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
    except Exception:
        return None


@dataclass
class WssIdleLifecycleGuard:
    """One-launch game-exit authority; unknown game identity never means exit.

    Main supplies cached process evidence and binds its first real incarnation.
    Legacy boolean-reader fixtures retain the prior unbound idle fallback.
    """

    process_name: str
    overlay_state_path: Path
    policy_state_path: Path
    status_path: Path
    launch_id: str = ""
    enabled: bool = True
    startup_grace_seconds: float = LIFECYCLE_STARTUP_ATTACH_GRACE_SECONDS
    drain_grace_seconds: float = LIFECYCLE_DRAIN_GRACE_SECONDS
    exit_confirm_polls: int = LIFECYCLE_EXIT_CONFIRM_POLLS
    poll_seconds: float = LIFECYCLE_POLL_SECONDS
    game_state_reader: Callable[[str], bool | None] = _named_process_running_state
    ui_state_reader: Callable[[Path], bool | None] = _pid_state_file_running_state
    policy_state_reader: Callable[[Path], bool | None] = _lifecycle_policy_enabled
    game_evidence_reader: Callable[[], tuple[bool | None, int | None, float | None]] | None = None
    bound_game_reader: Callable[[int, float], bool | None] = _bound_game_running_state
    monitor_started_at_unix_ms: int = 0
    owner_sid: str = ""

    def __post_init__(self) -> None:
        self.started_monotonic = time.monotonic()
        self.started_at = time.time()
        self.next_poll_monotonic = 0.0
        self.drain_deadline_monotonic: float | None = None
        self.confirmed_idle_polls = 0
        self.phase = "ready" if self.enabled else "disabled"
        self.last_game_state: bool | None = None
        self.last_ui_state: bool | None = None
        self.last_policy_state: bool | None = None
        self.stop_reason: str | None = None
        self.bound_game: tuple[int, float] | None = None
        self.bound_ui: tuple[int, float] | None = None
        self.observed_running = False
        self.exit_observed_monotonic: float | None = None
        self.cleanup_result: dict[str, Any] | None = None
        self._last_status_signature: tuple[Any, ...] | None = None
        self._write_status()

    @staticmethod
    def _state_label(state: bool | None) -> str:
        if state is True:
            return "running"
        if state is False:
            return "exited"
        return "unknown"

    @staticmethod
    def _policy_label(state: bool | None) -> str:
        if state is True:
            return "enabled"
        if state is False:
            return "disabled"
        return "unknown"

    def _write_status(self) -> None:
        signature = (
            self.phase,
            self.last_game_state,
            self.last_ui_state,
            self.last_policy_state,
            self.stop_reason,
            self.confirmed_idle_polls,
            self.drain_deadline_monotonic,
            repr(self.cleanup_result),
        )
        if signature == self._last_status_signature:
            return
        payload = {
            "schema_version": LIFECYCLE_SCHEMA_VERSION,
            "owner_kind": "wss_monitor_game_exit_group",
            "group_exit_schema_version": 1,
            "monitor_pid": os.getpid(),
            "monitor_started_at": self.started_at,
            "monitor_started_at_unix_ms": self.monitor_started_at_unix_ms,
            "owner_sid": self.owner_sid,
            "bound_game_pid": self.bound_game[0] if self.bound_game else None,
            "bound_game_started_at": self.bound_game[1] if self.bound_game else None,
            "exit_observed_monotonic": self.exit_observed_monotonic,
            "cleanup": self.cleanup_result,
            "launch_id": self.launch_id or None,
            "phase": self.phase,
            "game_state": self._state_label(self.last_game_state),
            "ui_state": self._state_label(self.last_ui_state),
            "policy_state": self._policy_label(self.last_policy_state),
            "auto_stop_enabled": self.enabled,
            "drain_grace_seconds": max(0.0, float(self.drain_grace_seconds)),
            "stop_reason": self.stop_reason,
            "updated_at": time.time(),
        }
        try:
            _atomic_write_json(self.status_path, payload)
        except OSError:
            # Diagnostics must not become a second lifecycle authority.
            return
        self._last_status_signature = signature

    def _cancel_idle_drain(self) -> None:
        self.confirmed_idle_polls = 0
        self.drain_deadline_monotonic = None
        self.stop_reason = None

    def tick(self, now_monotonic: float | None = None) -> bool:
        if not self.enabled:
            return False
        if self.exit_observed_monotonic is not None:
            return True
        now = time.monotonic() if now_monotonic is None else float(now_monotonic)
        if now < self.next_poll_monotonic:
            return False
        self.next_poll_monotonic = now + max(0.05, float(self.poll_seconds))

        try:
            policy_state = self.policy_state_reader(self.policy_state_path)
        except Exception:
            policy_state = None
        self.last_policy_state = policy_state
        if policy_state is not True:
            self._cancel_idle_drain()
            self.phase = "policy_disabled" if policy_state is False else "policy_unknown"
            self._write_status()
            return False

        try:
            if self.game_evidence_reader is not None:
                evidence = self.game_evidence_reader()
                if self.bound_game is None and evidence[0] is True:
                    pid, started = evidence[1:]
                    if type(pid) is int and pid > 0 and type(started) in (int, float) and started > 0:
                        self.bound_game = (pid, float(started))
                        self.observed_running = True
                game_state = (self.bound_game_reader(*self.bound_game)
                              if self.bound_game else evidence[0])
            else:
                game_state = self.game_state_reader(self.process_name)
        except Exception:
            game_state = None
        try:
            ui_state = self.ui_state_reader(self.overlay_state_path)
        except Exception:
            ui_state = None
        self.last_game_state = game_state
        self.last_ui_state = ui_state
        if ui_state is True and self.bound_ui is None:
            try:
                import psutil
                ui = json.loads(self.overlay_state_path.read_text(encoding="utf-8-sig"))
                pid = ui.get("pid")
                if type(pid) is int and pid > 0:
                    self.bound_ui = (pid, psutil.Process(pid).create_time())
            except Exception:
                pass

        if game_state is True:
            if self.game_evidence_reader is None:
                self.observed_running = True
            self._cancel_idle_drain()
            self.phase = "game_active"
        elif game_state is None:
            self._cancel_idle_drain()
            self.phase = "game_unknown"
        elif self.observed_running:
            self.phase = "stop_requested"
            self.stop_reason = "bound_game_exited"
            self.exit_observed_monotonic = now
            self._write_status()
            return True
        elif self.game_evidence_reader is not None:
            self._cancel_idle_drain()
            self.phase = "waiting_for_game"
        elif ui_state is True:
            self._cancel_idle_drain()
            self.phase = "ready"
        elif ui_state is None:
            self._cancel_idle_drain()
            self.phase = "ui_unknown"
        elif now - self.started_monotonic < max(
            0.0, float(self.startup_grace_seconds)
        ):
            self._cancel_idle_drain()
            self.phase = "ready"
        else:
            self.confirmed_idle_polls += 1
            if self.confirmed_idle_polls >= max(1, int(self.exit_confirm_polls)):
                if self.drain_deadline_monotonic is None:
                    self.drain_deadline_monotonic = now + max(
                        0.0, float(self.drain_grace_seconds)
                    )
                self.phase = "draining"
                if now >= self.drain_deadline_monotonic:
                    self.phase = "stopped"
                    self.stop_reason = "ui_detached_game_exited"
                    self._write_status()
                    return True
            else:
                self.phase = "ready"
        self._write_status()
        return False


def build_mitmdump_command(
    *,
    mitmdump_path: Path,
    addon_path: Path,
    confdir: Path,
    process_name: str,
) -> list[str]:
    return [
        str(mitmdump_path),
        "-q",
        "--mode",
        f"local:{process_name}",
        "--set",
        f"confdir={confdir}",
        "--set",
        "termlog_verbosity=error",
        "-s",
        str(addon_path),
    ]


def assign_windows_kill_on_close_job(process: subprocess.Popen[Any]) -> Any | None:
    """Bind a child to a Windows job that dies if this monitor is terminated.

    The overlay stops the monitor PID directly.  On Windows that does not run
    Python ``finally`` blocks, so an ordinary subprocess would survive as an
    orphan.  A non-inheritable job handle gives the OS the lifecycle contract:
    when this parent exits for any reason, mitmdump and its descendants exit.
    """

    if os.name != "nt":
        return None

    import ctypes
    from ctypes import wintypes

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())

    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = 0x00002000
    if not kernel32.SetInformationJobObject(
        job,
        9,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(job)
        raise ctypes.WinError(error)

    process_handle = wintypes.HANDLE(int(getattr(process, "_handle")))
    if not kernel32.AssignProcessToJobObject(job, process_handle):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(job)
        raise ctypes.WinError(error)
    return job


def close_windows_job_handle(job: Any | None) -> None:
    if job is None or os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    if not kernel32.CloseHandle(job):
        raise ctypes.WinError(ctypes.get_last_error())


def _stop_capture_job(job: Any | None, deadline: float) -> str:
    """Terminate only the held Job and read its active count before closing it."""
    if job is None or os.name != "nt":
        return "not_available"
    from ctypes import wintypes

    class Accounting(ctypes.Structure):
        _fields_ = [(name, ctypes.c_longlong) for name in (
            "user", "kernel", "period_user", "period_kernel")]
        _fields_ += [(name, wintypes.DWORD) for name in (
            "faults", "total", "active", "terminated")]

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        kernel32.QueryInformationJobObject.restype = wintypes.BOOL
        if not kernel32.TerminateJobObject(job, 1):
            return "terminate_failed"
        while True:
            info = Accounting()
            if not kernel32.QueryInformationJobObject(job, 1, ctypes.byref(info), ctypes.sizeof(info), None):
                return "unknown"
            if info.active == 0:
                return "empty"
            if time.monotonic() >= deadline:
                return "remaining"
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
    except Exception:
        return "unknown"


def _shutdown_capture_group(*, monitor: Any, child: Any, child_job: Any,
                            preloader: Any, snapshot: Any = None,
                            observed_at: float | None = None,
                            ui_result_reader: Callable[[], str] | None = None,
                            budget_seconds: float = 2.0) -> dict[str, Any]:
    """One deadline; original monitor worker concurrently owns reap and flush."""
    started = time.monotonic() if observed_at is None else observed_at
    observed_delay = max(0.0, time.monotonic() - started)
    measured_start = time.perf_counter()
    deadline = started + max(0.0, budget_seconds)
    result: dict[str, Any] = {"budget_seconds": budget_seconds, "components": {}, "timeline": {}}
    states = result["components"]
    def mark(name: str, state: Any) -> None:
        states[name] = state
        result["timeline"][name] = round(observed_delay + time.perf_counter() - measured_start, 6)
    try:
        monitor.request_stop()
        mark("dispatch", "stopped")
    except Exception:
        mark("dispatch", "unknown")
    try:
        if child.poll() is None:
            child.terminate()
        mark("helper_stop_request", "sent")
    except Exception:
        mark("helper_stop_request", "failed")
    try:
        mark("capture_job", _stop_capture_job(child_job, min(deadline, time.monotonic() + 0.3)))
    finally:
        try:
            close_windows_job_handle(child_job)
            mark("job_handle", "closed" if child_job is not None else "not_available")
        except Exception:
            mark("job_handle", "unknown")
    try:
        child.wait(timeout=max(0.0, min(0.3, deadline - time.monotonic())))
    except subprocess.TimeoutExpired:
        try:
            child.kill()
            child.wait(timeout=max(0.0, min(0.2, deadline - time.monotonic())))
        except Exception:
            pass
    except Exception:
        pass
    try:
        mark("helper", "exited" if child.poll() is not None else "remaining")
    except Exception:
        mark("helper", "unknown")
    try:
        mark("owned_service", preloader.release(deadline=deadline))
    except Exception:
        mark("owned_service", "unknown")
    try:
        if snapshot is not None:
            snapshot.close()
            closed = all(member.stream.closed for member in (snapshot.loader, snapshot.bootstrap, snapshot.manifest))
        else:
            closed = True
        mark("operational_handles", "closed" if closed else "remaining")
    except Exception:
        mark("operational_handles", "unknown")
    try:
        mark("monitor_worker", "stopped" if monitor.stop(timeout=max(0.0, deadline - time.monotonic())) else "remaining")
    except Exception:
        mark("monitor_worker", "failed")
    ui_state = "unverified"
    if ui_result_reader is not None:
        while True:
            try:
                ui_state = ui_result_reader()
            except Exception:
                ui_state = "unknown"
            if ui_state in {"exited", "not_running"} or time.monotonic() >= deadline:
                break
            time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))
    mark("ui", ui_state)
    result["elapsed_seconds"] = round(observed_delay + time.perf_counter() - measured_start, 6)
    result["within_budget"] = result["elapsed_seconds"] <= budget_seconds
    result["resources_complete"] = (
        states.get("dispatch") == "stopped" and states.get("helper") == "exited"
        and states.get("capture_job") == "empty" and states.get("job_handle") == "closed"
        and states.get("owned_service") in {"absent", "not_owned"}
        and states.get("operational_handles") == "closed"
        and states.get("monitor_worker") == "stopped" and ui_state in {"exited", "not_running"}
    )
    # This process cannot read back its own future exit. Only the external
    # coordinator can promote resources_complete to whole-group completion.
    result["owner_process_state"] = "exit_pending"
    result["complete"] = False
    return result


def _group_ui_exit_state(guard: WssIdleLifecycleGuard) -> str:
    if guard.bound_ui is None:
        # A missing PID file may also mean the selected UI has not attached yet;
        # it does not prove that all UI-owned pools were reclaimed.
        return "unknown"
    try:
        ack = json.loads((guard.status_path.parent / "lifecycle_ui_exit.json").read_text(encoding="utf-8"))
        if (type(ack.get("group_exit_schema_version")) is not int or ack["group_exit_schema_version"] != 1
                or ack.get("launch_id") != guard.launch_id
                or type(ack.get("monitor_pid")) is not int or ack["monitor_pid"] != os.getpid()
                or type(ack.get("monitor_started_at_unix_ms")) is not int
                or ack["monitor_started_at_unix_ms"] != guard.monitor_started_at_unix_ms
                or ack.get("pools_reaped") is not True
                or type(ack.get("ui_pid")) is not int or ack["ui_pid"] <= 0
                or type(ack.get("ui_started_at")) not in (int, float) or ack["ui_started_at"] <= 0):
            return "unknown"
        if guard.bound_ui != (ack["ui_pid"], ack["ui_started_at"]):
            return "unknown"
        return "exited" if _bound_game_running_state(ack["ui_pid"], ack["ui_started_at"]) is False else "remaining"
    except Exception:
        return "unknown"


def prepare_ca(
    *,
    mitmdump_path: Path,
    confdir: Path,
    timeout_seconds: float = 12.0,
) -> Path:
    """Generate a per-user mitmproxy CA without installing or trusting it."""

    try:
        confdir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WssCaPrepareError("component_write_denied") from exc
    certificate = ca_certificate_path(confdir)
    if certificate.is_file():
        return certificate

    try:
        process = subprocess.Popen(
            [
                str(mitmdump_path),
                "-q",
                "--listen-host",
                "127.0.0.1",
                "--listen-port",
                "0",
                "--set",
                f"confdir={confdir}",
                "--set",
                "termlog_verbosity=error",
            ],
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        raise WssCaPrepareError("component_execution_blocked") from exc
    try:
        deadline = time.monotonic() + max(1.0, float(timeout_seconds))
        while time.monotonic() < deadline:
            if certificate.is_file():
                return certificate
            if process.poll() is not None:
                break
            time.sleep(0.1)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3.0)
    raise WssCaPrepareError("component_execution_blocked")


def parse_frame_record(line: str) -> dict[str, Any] | None:
    if not line.startswith(FRAME_STREAM_PREFIX):
        return None
    try:
        record = json.loads(line[len(FRAME_STREAM_PREFIX) :])
        flow_id = str(record["flow_id"])
        if not flow_id or len(flow_id) > 128:
            return None
        direction = str(record["direct"]).upper()
        if direction not in ("SEND", "REV"):
            return None
        raw = base64.b64decode(str(record["data_b64"]), validate=True)
        message_id = frame_message_id(direction, raw)  # type: ignore[arg-type]
        if message_id not in allowed_message_ids(direction):  # type: ignore[arg-type]
            return None
        if int(record.get("message_id")) != message_id:
            return None
        if int(record.get("data_length")) != len(raw):
            return None
        captured_monotonic = record.get("captured_monotonic")
        if not isinstance(captured_monotonic, (int, float)) or not math.isfinite(
            float(captured_monotonic)
        ):
            captured_monotonic = None
        return {
            "captured_at_ms": int(record.get("captured_at_ms") or 0),
            "captured_monotonic": captured_monotonic,
            "flow_id": flow_id,
            "direct": direction,
            "message_id": message_id,
            "raw": raw,
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def parse_flow_record(line: str) -> tuple[str, str] | None:
    if not line.startswith(FLOW_STREAM_PREFIX):
        return None
    try:
        record = json.loads(line[len(FLOW_STREAM_PREFIX) :])
        event = str(record["event"])
        flow_id = str(record["flow_id"])
        if event not in ("open", "close") or not flow_id or len(flow_id) > 128:
            return None
        return event, flow_id
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def frame_record_to_row(
    record: Mapping[str, Any],
    *,
    sort_id: int,
    process_name: str,
) -> dict[str, Any]:
    direction = str(record["direct"]).upper()
    raw = bytes(record["raw"])
    if direction == "SEND":
        src_ip, src_port, dst_ip, dst_port = "127.0.0.1", 1, "203.0.113.1", 443
    else:
        src_ip, src_port, dst_ip, dst_port = "203.0.113.1", 443, "127.0.0.1", 1
    return {
        "SortID": int(sort_id),
        "Direct": direction,
        # The downstream Fatbeans loader accepts its historical transport label.
        # WSS has already been terminated and only the application frame remains.
        "Protocol": "Tcp",
        "SrcIP": src_ip,
        "SrcPort": src_port,
        "DstIP": dst_ip,
        "DstPort": dst_port,
        "CaptureTime": _capture_time(),
        "Data": base64.b64encode(raw).decode("ascii"),
        "DataLength": len(raw),
        "PID": None,
        "ProcessName": process_name,
        "Source": "WssLocal",
    }


def _pipe_reader(
    stream: TextIO,
    sink: "queue.Queue[tuple[float, str] | None]",
) -> None:
    try:
        for line in stream:
            sink.put((time.monotonic(), line.rstrip("\r\n")))
    finally:
        sink.put(None)


def _start_version_and_activation_threads(log_dir: Path) -> None:
    def version_banner() -> None:
        try:
            from bidking_lab.live import version_check
            from bidking_lab.live.startup_version_notice import (
                check_and_publish_startup_version,
            )

            status = check_and_publish_startup_version(log_dir)
            print(f"[version] {version_check.format_status_line(status)}", flush=True)
        except Exception as exc:
            print(f"[version] 跳过(版本检查异常: {type(exc).__name__})", flush=True)

    def activation_banner() -> None:
        try:
            from bidking_lab.secure import enforce
        except Exception as exc:
            print(f"[activation] 跳过(激活异常: {type(exc).__name__})", flush=True)
            return
        first = True
        consecutive_transient_failures = 0
        last_status = None
        while True:
            started = time.monotonic()
            try:
                result = (
                    enforce.startup_activate()
                    if first
                    else enforce.refresh_activation()
                )
            except Exception:
                result = None
            elapsed_ms = round((time.monotonic() - started) * 1000.0, 1)
            transient = enforce.is_transient_activation_failure(result)
            consecutive_transient_failures = (
                consecutive_transient_failures + 1 if transient else 0
            )
            delay = enforce.activation_refresh_delay(
                result, consecutive_transient_failures
            )
            status = (
                bool(getattr(result, "activated", False)),
                str(getattr(result, "mode", "none")),
                str(getattr(result, "reason", "error")),
                str(getattr(result, "source", "unknown")),
            )
            if first or transient or status != last_status:
                print(
                    f"[activation] {enforce.activation_banner(result)} "
                    f"elapsed_ms={elapsed_ms:.1f} next_retry_seconds={delay:g}",
                    flush=True,
                )
            last_status = status
            first = False
            time.sleep(delay)

    threading.Thread(target=version_banner, daemon=True).start()
    threading.Thread(target=activation_banner, daemon=True).start()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cold-start process-local WSS capture -> BidKing live monitor."
    )
    parser.add_argument("--log-dir", default=str(ROOT / "data/logs/live"))
    parser.add_argument("--raw-dir", default=None)
    parser.add_argument("--process-name", default=DEFAULT_PROCESS_NAME)
    parser.add_argument("--server-host", default=DEFAULT_SERVER_HOST)
    parser.add_argument("--mitmdump", default=None)
    parser.add_argument("--allow-engineering-runtime", action="store_true")
    parser.add_argument("--addon", default=str(ROOT / "scripts/mitm_bidking_wss_addon.py"))
    parser.add_argument("--confdir", default=str(_default_confdir()))
    parser.add_argument("--status-seconds", type=float, default=2.0)
    parser.add_argument("--n-trials", type=int, default=500)
    parser.add_argument("--roi-trials", type=int, default=250)
    parser.add_argument("--shadow-trials", type=int, default=None)
    parser.add_argument("--full-shadow-trials", type=int, default=20)
    parser.add_argument("--run-debug-shadows", action="store_true", default=False)
    parser.add_argument("--seed", type=int, default=20260530)
    parser.add_argument("--no-lock", action="store_true")
    parser.add_argument("--package-generation-identity", default="")
    parser.add_argument("--allow-running-game", action="store_true")
    parser.add_argument("--prepare-ca", action="store_true")
    parser.add_argument(
        "--repair-stale-windivert-service",
        action="store_true",
        help=(
            "explicitly remove only a stopped, twice-verified stale "
            "BidKingWinDivertR1 service and then exit"
        ),
    )
    parser.add_argument("--lifecycle-overlay-state", default=None)
    parser.add_argument("--lifecycle-policy-state", default=None)
    parser.add_argument("--lifecycle-launch-id", default="")
    parser.add_argument("--internet-cafe-runtime-identity", default="")
    parser.add_argument("--internet-cafe-component-identity", default="")
    parser.add_argument("--internet-cafe-owner-sid", default="")
    args = parser.parse_args()

    try:
        mitmdump_path = resolve_mitmdump(
            args.mitmdump,
            allow_engineering_runtime=args.allow_engineering_runtime,
        )
    except FileNotFoundError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2
    if args.repair_stale_windivert_service:
        repairer = WinDivertStaleProductServiceRepair()
        repair_state = repairer.repair(_bundled_windivert_driver(mitmdump_path))
        safe_repair_state = (
            repair_state
            if repair_state in _WINDIVERT_REPAIR_STATES
            else "failed_unclassified"
        )
        safe_repair_phase = (
            repairer.phase
            if repairer.phase in _WINDIVERT_REPAIR_PHASES
            else "not_started"
        )
        print(
            "[wss-compat] "
            f"stale_product_service_repair={safe_repair_state} "
            f"phase={safe_repair_phase}",
            flush=True,
        )
        return (
            0
            if safe_repair_state in {"repaired_restart_required", "not_present"}
            else 6
        )
    confdir = Path(args.confdir).resolve()
    if args.prepare_ca:
        try:
            mitmdump_path = resolve_mitmdump(
                str(mitmdump_path),
                allow_engineering_runtime=args.allow_engineering_runtime,
            )
            print(
                str(prepare_ca(mitmdump_path=mitmdump_path, confdir=confdir)),
                flush=True,
            )
            return 0
        except Exception as exc:
            failure_class = (
                exc.classification
                if isinstance(exc, WssCaPrepareError)
                else "component_execution_blocked"
                if isinstance(exc, FileNotFoundError)
                else "unknown"
            )
            print(f"{WSS_CA_FAILURE_SENTINEL}{failure_class}", file=sys.stderr)
            print(
                f"[error] wss_ca_prepare_failed:{type(exc).__name__}",
                file=sys.stderr,
            )
            return 2

    certificate = ca_certificate_path(confdir)
    if not ca_is_trusted_for_current_user(certificate):
        print(
            "[error] wss_capture_ca_not_trusted; run the launcher once to install "
            "the per-user BidKing local capture certificate",
            file=sys.stderr,
        )
        return 3
    running_game_pids = _game_process_ids(args.process_name)
    if running_game_pids and not args.allow_running_game:
        print(
            "[error] wss_cold_start_required; Steam may stay open, but close "
            "BidKing.exe before starting or restarting Hero Ref; wait for the "
            "overlay, then launch the game",
            file=sys.stderr,
        )
        return 4

    addon_path = Path(args.addon).resolve()
    if not addon_path.is_file():
        print(f"[error] WSS addon not found: {addon_path}", file=sys.stderr)
        return 2

    log_dir = Path(args.log_dir).resolve()
    raw_dir = Path(args.raw_dir).resolve() if args.raw_dir else log_dir / "raw"
    player_log_window = PlayerLogConnectionWindow(_default_player_log_path())
    package_generation_identity = ""
    wss_operational_environment: dict[str, str] = {}
    wss_operational_snapshot: _WssOperationalSnapshot | None = None
    try:
        packaged = (ROOT / "BUILD_MANIFEST.txt").is_file()
        monitor_path = Path(sys.argv[0])
        if packaged:
            (
                wss_operational_snapshot,
                wss_operational_environment,
            ) = _validated_wss_operational_snapshot(
                package_root=ROOT,
                addon_path=addon_path,
                package_generation_validator=lambda: (
                    _validated_package_generation_identity(
                        args.package_generation_identity,
                        monitor_path=monitor_path,
                    )
                ),
            )
            package_generation_identity = (
                wss_operational_snapshot.package_generation_identity
            )
        elif not args.no_lock:
            package_generation_identity = _validated_package_generation_identity(
                args.package_generation_identity,
                monitor_path=monitor_path,
            )
        if not args.no_lock:
            _acquire_lock(
                log_dir,
                monitor_path=monitor_path,
                package_generation_identity=package_generation_identity,
            )
    except RuntimeError as exc:
        if wss_operational_snapshot is not None:
            wss_operational_snapshot.close()
        print(f"[error] {exc}", file=sys.stderr)
        return 2

    helper_binding_values = (
        str(args.internet_cafe_runtime_identity or ""),
        str(args.internet_cafe_component_identity or ""),
        str(args.internet_cafe_owner_sid or ""),
        str(args.lifecycle_launch_id or ""),
    )
    helper_identity_requested = any(helper_binding_values)
    helper_identity_complete = bool(package_generation_identity) and all(
        helper_binding_values
    )
    if helper_identity_requested and not helper_identity_complete:
        print("[error] wss_helper_status_identity_incomplete", file=sys.stderr)
        return 2
    monitor_started_at_unix_ms = 0
    lifecycle_monitor_started_at_ms = 0
    try:
        import psutil
        lifecycle_monitor_started_at_ms = int(psutil.Process(os.getpid()).create_time() * 1000.0)
    except Exception:
        pass
    if helper_identity_complete:
        if (
            re.fullmatch(r"[0-9a-f]{64}", package_generation_identity) is None
            or re.fullmatch(
                r"[0-9a-f]{64}", args.internet_cafe_runtime_identity
            )
            is None
            or re.fullmatch(
                r"[0-9a-f]{64}", args.internet_cafe_component_identity
            )
            is None
            or re.fullmatch(
                r"S-1-(?:[0-9]+-){1,14}[0-9]+", args.internet_cafe_owner_sid
            )
            is None
            or re.fullmatch(
                r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
                r"[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}",
                args.lifecycle_launch_id,
            )
            is None
        ):
            print("[error] wss_helper_status_identity_invalid", file=sys.stderr)
            return 2
        try:
            import psutil

            monitor_started_at_unix_ms = int(
                psutil.Process(os.getpid()).create_time() * 1000.0
            )
        except Exception:
            print("[error] wss_helper_status_process_identity_unavailable", file=sys.stderr)
            return 2
        if monitor_started_at_unix_ms <= 0:
            print("[error] wss_helper_status_process_identity_invalid", file=sys.stderr)
            return 2
    helper_status_identity: dict[str, object] = {
        "package_generation_identity": package_generation_identity,
        "runtime_identity": str(args.internet_cafe_runtime_identity or ""),
        "component_identity": str(args.internet_cafe_component_identity or ""),
        "owner_sid": str(args.internet_cafe_owner_sid or ""),
        "monitor_started_at_unix_ms": monitor_started_at_unix_ms,
    }
    lifecycle_guard: WssIdleLifecycleGuard | None = None
    if args.lifecycle_overlay_state and args.lifecycle_policy_state:
        lifecycle_guard = WssIdleLifecycleGuard(
            process_name=args.process_name,
            overlay_state_path=Path(args.lifecycle_overlay_state).resolve(),
            policy_state_path=Path(args.lifecycle_policy_state).resolve(),
            status_path=log_dir / LIFECYCLE_STATUS_FILENAME,
            launch_id=str(args.lifecycle_launch_id or ""),
            enabled=not _env_switch_enabled(DISABLE_IDLE_AUTOSTOP_ENV),
            monitor_started_at_unix_ms=lifecycle_monitor_started_at_ms,
            owner_sid=str(args.internet_cafe_owner_sid or ""),
        )

    monitor = FatbeansWebhookMonitor(
        config=WebhookMonitorConfig(
            log_dir=log_dir,
            raw_dir=raw_dir,
            process_name=args.process_name,
            server_ports=(443,),
            n_trials=args.n_trials,
            roi_trials=args.roi_trials,
            shadow_trials=args.shadow_trials,
            full_shadow_trials=max(1, int(args.full_shadow_trials)),
            run_debug_shadows=bool(args.run_debug_shadows),
            seed=args.seed,
            file_name="wss_live.json",
            source_name="wss_local",
            packet_count_key="wss_frames",
        ),
        tables=load_monitor_tables(),
    )
    monitor.start()
    try:
        from bidking_lab.live import fatbeans as _fatbeans

        if _fatbeans.configure_live_settlement_diagnostics():
            print(
                "[privacy-warning] BIDKING_ENABLE_RAW_SETTLEMENT_FORENSICS=1; "
                "monitor.stdout.log contains unredacted application data and must stay private",
                flush=True,
            )
    except Exception:
        pass
    _start_version_and_activation_threads(log_dir)

    command = build_mitmdump_command(
        mitmdump_path=mitmdump_path,
        addon_path=addon_path,
        confdir=confdir,
        process_name=args.process_name,
    )
    helper_diagnostics = WssHelperDiagnostics()
    bundled_windivert_driver = _bundled_windivert_driver(mitmdump_path)
    windivert_preloader = WinDivertCompatibilityPreloader()
    windivert_preload_state = windivert_preloader.prepare(
        bundled_windivert_driver
    )
    auto_repair = _attempt_automatic_stale_product_service_repair(
        windivert_preloader,
        bundled_windivert_driver,
        enabled=not _env_switch_enabled(
            DISABLE_WINDIVERT_AUTO_STALE_REPAIR_ENV
        ),
        # The redirector child has not been created yet, so this launch cannot
        # be attached, have accepted a frame, or own a valid session here.
        helper_attached=False,
        accepted_frames=0,
        active_session_id="",
    )
    windivert_preload_state = auto_repair.preload_state
    if auto_repair.repair_state != "not_started":
        print(
            "[wss-compat] "
            f"stale_product_service_auto_repair={auto_repair.repair_state} "
            f"phase={auto_repair.repair_phase} "
            f"launch_preload={windivert_preload_state}",
            flush=True,
        )
    monitor_elevation_state = _windows_elevation_state()
    print(
        "[wss-compat] "
        f"windivert_preload={windivert_preload_state} "
        f"phase={windivert_preloader.phase} "
        f"monitor_elevation={monitor_elevation_state}",
        flush=True,
    )
    if windivert_preload_state == "repaired_restart_required":
        _write_wss_helper_status(
            log_dir,
            phase="helper_start_failed",
            helper_pid=0,
            launch_id=str(args.lifecycle_launch_id or ""),
            diagnostics=helper_diagnostics,
            windivert_preload_state=windivert_preloader.state,
            windivert_preload_phase=windivert_preloader.phase,
            monitor_elevation_state=monitor_elevation_state,
            **helper_status_identity,
        )
        try:
            monitor.stop()
        except Exception:
            pass
        if wss_operational_snapshot is not None:
            wss_operational_snapshot.close()
        print(
            "[error] stale_product_service_repaired_restart_required; "
            "restart Windows, fully extract this package to a new ASCII-only "
            "directory, then launch Hero Ref before the game",
            file=sys.stderr,
        )
        return 6
    terminal_preload_error = {
        "failed_service_conflict": "wss_stale_product_service_conflict",
        "failed_same_path_mixed_generation": "wss_same_path_mixed_generation",
    }.get(windivert_preload_state)
    if terminal_preload_error and windivert_preloader.phase == "verify_product":
        # No helper exists at this point, so this launch has no accepted frame
        # or attached session. The exact repair gate has already refused the
        # conflicting service: a living helper would only disguise that failure
        # as a long wait for its first packet. Preserve the service and publish
        # this launch's fixed identity before stopping the managed monitor.
        _write_wss_helper_status(
            log_dir,
            phase="helper_start_failed",
            helper_pid=0,
            launch_id=str(args.lifecycle_launch_id or ""),
            diagnostics=helper_diagnostics,
            error_code=terminal_preload_error,
            windivert_preload_state=windivert_preloader.state,
            windivert_preload_phase=windivert_preloader.phase,
            monitor_elevation_state=monitor_elevation_state,
            **helper_status_identity,
        )
        try:
            monitor.stop()
        except Exception:
            pass
        if wss_operational_snapshot is not None:
            try:
                wss_operational_snapshot.close()
            except Exception:
                pass
        print(f"[error] {terminal_preload_error}", file=sys.stderr)
        return 6
    child_env = os.environ.copy()
    child_env["BIDKING_WSS_SERVER_HOST"] = str(args.server_host).strip().lower()
    child_env.update(wss_operational_environment)
    try:
        if wss_operational_snapshot is not None:
            wss_operational_snapshot.assert_unchanged()
        if resolve_mitmdump(
            str(mitmdump_path),
            allow_engineering_runtime=args.allow_engineering_runtime,
        ) != mitmdump_path:
            raise FileNotFoundError("fixed mitmdump runtime changed")
        child = subprocess.Popen(
            command,
            cwd=str(ROOT),
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        try:
            windivert_preloader.release()
        except Exception:
            # Cleanup is a trust boundary too.  Never expose an SCM/runner
            # exception, but preserve a fixed final classification for this
            # launch so diagnostics cannot fall back to stale evidence.
            windivert_preloader.state = "failed_unclassified"
            windivert_preloader.phase = "cleanup"
        try:
            monitor.stop()
        except Exception:
            pass
        if wss_operational_snapshot is not None:
            wss_operational_snapshot.close()
        _write_wss_helper_status(
            log_dir,
            phase="helper_start_failed",
            helper_pid=0,
            launch_id=str(args.lifecycle_launch_id or ""),
            diagnostics=helper_diagnostics,
            error_code="wss_capture_helper_start_failed",
            windivert_preload_state=windivert_preloader.state,
            windivert_preload_phase=windivert_preloader.phase,
            monitor_elevation_state=monitor_elevation_state,
            **helper_status_identity,
        )
        print("[error] wss_capture_helper_start_failed", file=sys.stderr)
        return 5
    redirector_liveness = WssRedirectorLiveness()
    _write_wss_helper_status(
        log_dir,
        phase="helper_running",
        helper_pid=child.pid,
        launch_id=str(args.lifecycle_launch_id or ""),
        diagnostics=helper_diagnostics,
        windivert_preload_state=windivert_preloader.state,
        windivert_preload_phase=windivert_preloader.phase,
        monitor_elevation_state=monitor_elevation_state,
        **helper_status_identity,
    )
    try:
        child_job = assign_windows_kill_on_close_job(child)
    except OSError as exc:
        child.terminate()
        try:
            child.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=3.0)
        monitor.stop()
        windivert_preloader.release()
        if wss_operational_snapshot is not None:
            wss_operational_snapshot.close()
        _write_wss_helper_status(
            log_dir,
            phase="monitor_guard_failed",
            helper_pid=child.pid,
            launch_id=str(args.lifecycle_launch_id or ""),
            diagnostics=helper_diagnostics,
            helper_exit_code=child.returncode,
            error_code="wss_child_lifecycle_guard_failed",
            windivert_preload_state=windivert_preloader.state,
            windivert_preload_phase=windivert_preloader.phase,
            monitor_elevation_state=monitor_elevation_state,
            **helper_status_identity,
        )
        try:
            game_running, game_pid, game_started_at = _game_process_evidence(
                args.process_name
            )
            atomic_write_network_status(
                log_dir / NETWORK_CONNECTION_STATUS_FILENAME,
                derive_network_connection_status(
                    launch_id=str(args.lifecycle_launch_id or ""),
                    game_running=game_running,
                    game_pid=game_pid,
                    game_started_at=game_started_at,
                    player_log_window_started_at=game_started_at,
                    player_log_error_counts=player_log_window.observe(
                        game_running=game_running,
                        game_pid=game_pid,
                        game_started_at=game_started_at,
                    ),
                    capture_status={
                        "source": "wss_local",
                        "error_code": "wss_child_lifecycle_guard_failed",
                    },
                    helper_status=read_json_mapping(
                        log_dir / WSS_HELPER_STATUS_FILENAME
                    ),
                ),
            )
        except Exception:
            pass
        print(
            f"[error] wss_child_lifecycle_guard_failed:{type(exc).__name__}",
            file=sys.stderr,
        )
        return 5
    assert child.stdout is not None
    assert child.stderr is not None
    lines: "queue.Queue[tuple[float, str] | None]" = queue.Queue()
    threading.Thread(
        target=_pipe_reader,
        args=(child.stdout, lines),
        name="wss-frame-reader",
        daemon=True,
    ).start()
    # Mitmdump error logs may contain endpoint/request context.  Drain them so the
    # child cannot block.  Only fixed error codes are retained; the original text
    # is never persisted or echoed.
    stderr_thread = threading.Thread(
        target=_consume_mitmdump_stderr,
        args=(child.stderr, helper_diagnostics),
        name="wss-private-stderr-drain",
        daemon=True,
    )
    stderr_thread.start()

    frame_gate = GameFrameGate()
    game_capture_epoch = GameCaptureLivenessEpoch()
    sort_id = 1
    sanitized_frames = accepted_frames = malformed_records = 0
    active_flow_ids: set[str] = set()
    last_status = 0.0
    last_game_evidence_poll = 0.0
    current_game_evidence: tuple[bool | None, int | None, float | None] = (
        None,
        None,
        None,
    )
    helper_exited_unexpectedly = False

    def refresh_game_evidence(
        *,
        force: bool = False,
    ) -> tuple[bool | None, int | None, float | None]:
        nonlocal current_game_evidence, last_game_evidence_poll
        now = time.monotonic()
        if force or now - last_game_evidence_poll >= 0.25:
            current_game_evidence = _game_process_evidence(args.process_name)
            process_changed = game_capture_epoch.observe_process(
                game_running=current_game_evidence[0],
                game_pid=current_game_evidence[1],
                game_started_at=current_game_evidence[2],
            )
            if process_changed and not (lifecycle_guard is not None and lifecycle_guard.bound_game is not None):
                frame_gate.reset_stream_buffers(clear_session=True)
                monitor.reset_rows()
                redirector_liveness.reset_game()
            last_game_evidence_poll = now
        return current_game_evidence

    if lifecycle_guard is not None:
        lifecycle_guard.game_evidence_reader = refresh_game_evidence
        lifecycle_guard.poll_seconds = 0.1

    def write_capture_status(
        *,
        error_code: str | None = None,
        error_message: str | None = None,
        error_hint: str | None = None,
    ) -> None:
        game_running, game_pid, game_started_at = refresh_game_evidence(force=True)
        epoch_capture_status = game_capture_epoch.capture_status(
            error_code=error_code
        )
        has_current_wss_health = bool(
            int(epoch_capture_status["active_flows"]) > 0
            or int(epoch_capture_status["raw_packets"]) > 0
            or int(epoch_capture_status["accepted_frames"]) > 0
            or str(epoch_capture_status["active_session_id"] or "")
        )
        if child.poll() is None:
            redirector_observation_state = redirector_liveness.observe(
                now=time.monotonic(),
                game_running=game_running,
                redirector_running=_named_process_running_state(
                    REDIRECTOR_PROCESS_NAME
                ),
                has_current_wss_health=has_current_wss_health,
            )
            redirector_error_code = (
                _REDIRECTOR_MISSING_STABLE_ERROR
                if redirector_observation_state == "missing_stable"
                else None
            )
            _write_wss_helper_status(
                log_dir,
                phase="helper_running",
                helper_pid=child.pid,
                launch_id=str(args.lifecycle_launch_id or ""),
                diagnostics=helper_diagnostics,
                error_code=redirector_error_code,
                redirector_observation_state=redirector_observation_state,
                windivert_preload_state=windivert_preloader.state,
                windivert_preload_phase=windivert_preloader.phase,
                monitor_elevation_state=monitor_elevation_state,
                **helper_status_identity,
            )
        _write_source_status(
            log_dir,
            source="wss_local",
            process_name=args.process_name,
            filter_text=(
                f"local:{args.process_name} host={args.server_host} "
                "allowlist=rev:21,25,27,2d,77 send:22,26"
            ),
            active_flows=int(epoch_capture_status["active_flows"]),
            accepted_packets=int(epoch_capture_status["accepted_frames"]),
            sniffed_packets=int(epoch_capture_status["sniffed_packets"]),
            raw_packets=int(epoch_capture_status["raw_packets"]),
            ignored_frames=int(epoch_capture_status["ignored_frames"]),
            dropped_bytes=frame_gate.dropped_bytes,
            ignored_reasons=frame_gate.ignored_reasons_dict(),
            ignored_samples=frame_gate.ignored_samples_dict(),
            active_session_id=str(
                epoch_capture_status["active_session_id"] or ""
            ),
            buffer_resyncs=frame_gate.buffer_resyncs,
            boundary_resyncs=frame_gate.boundary_resyncs,
            error_code=error_code,
            error_message=error_message,
            error_hint=error_hint,
        )
        try:
            player_log_error_counts = player_log_window.observe(
                game_running=game_running,
                game_pid=game_pid,
                game_started_at=game_started_at,
            )
            helper_status = read_json_mapping(
                log_dir / WSS_HELPER_STATUS_FILENAME
            )
            network_status = derive_network_connection_status(
                launch_id=str(args.lifecycle_launch_id or ""),
                game_running=game_running,
                game_pid=game_pid,
                game_started_at=game_started_at,
                player_log_window_started_at=game_started_at,
                player_log_error_counts=player_log_error_counts,
                capture_status=epoch_capture_status,
                helper_status=helper_status,
            )
            atomic_write_network_status(
                log_dir / NETWORK_CONNECTION_STATUS_FILENAME,
                network_status,
            )
        except Exception:
            # Status diagnostics are best-effort and must never interrupt WSS
            # capture, frame parsing, or the calculator UI.
            pass

    print(
        f"[listen] WSS cold-start capture ready; process={args.process_name} "
        f"host={args.server_host} log_dir={log_dir}",
        flush=True,
    )
    try:
        while True:
            try:
                line_record = lines.get(timeout=0.25)
            except queue.Empty:
                line_record = (time.monotonic(), "")
            if line_record is None:
                if child.poll() is not None:
                    helper_exited_unexpectedly = True
                    break
                continue
            line_received_monotonic, line = line_record
            refresh_game_evidence()
            if lifecycle_guard is not None and lifecycle_guard.tick():
                break
            if line:
                flow_event = parse_flow_record(line)
                if flow_event is not None:
                    event, flow_id = flow_event
                    if event == "open":
                        active_flow_ids.add(flow_id)
                        game_capture_epoch.open_flow(flow_id)
                    else:
                        active_flow_ids.discard(flow_id)
                        game_capture_epoch.close_flow(flow_id)
                else:
                    record = parse_frame_record(line)
                    if record is None:
                        if line.startswith((FRAME_STREAM_PREFIX, FLOW_STREAM_PREFIX)):
                            malformed_records += 1
                            game_capture_epoch.record_ignored_frames()
                    else:
                        captured_monotonic = record.get("captured_monotonic")
                        frame_received_monotonic = (
                            float(captured_monotonic)
                            if isinstance(captured_monotonic, (int, float))
                            else line_received_monotonic
                        )
                        flow_id = str(record["flow_id"])
                        active_flow_ids.add(flow_id)
                        game_capture_epoch.record_raw_frame(flow_id)
                        sanitized_frames += 1
                        row = frame_record_to_row(
                            record,
                            sort_id=sort_id,
                            process_name=args.process_name,
                        )
                        sort_id += 1
                        ignored_frames_before = frame_gate.ignored_frames
                        emitted = frame_gate.feed_row(row)
                        parser_recognized_monotonic = time.monotonic()
                        game_capture_epoch.record_ignored_frames(
                            frame_gate.ignored_frames - ignored_frames_before
                        )
                        if emitted.reset_session:
                            monitor.reset_rows()
                        for game_row in emitted.rows:
                            monitor.accept_row(
                                game_row,
                                schedule_process=_should_schedule_monitor_process(
                                    game_row
                                ),
                                frame_received_monotonic=frame_received_monotonic,
                                parser_recognized_monotonic=parser_recognized_monotonic,
                            )
                            accepted_frames += 1
                            game_capture_epoch.record_accepted_frame(
                                frame_gate.active_session_id
                            )

            now = time.monotonic()
            if lifecycle_guard is not None and lifecycle_guard.tick(now):
                print(
                    "[lifecycle] bound game exited; "
                    "graceful background shutdown",
                    flush=True,
                )
                break
            if now - last_status >= max(0.25, float(args.status_seconds)):
                write_capture_status()
                print(
                    f"[wss] sanitized={sanitized_frames} "
                    f"accepted_frames={accepted_frames} "
                    f"session={frame_gate.active_session_id} "
                    f"malformed_records={malformed_records}",
                    flush=True,
                )
                last_status = now
            if child.poll() is not None and lines.empty():
                helper_exited_unexpectedly = True
                break
    except KeyboardInterrupt:
        print("\n[wss] stopping ...", flush=True)
    finally:
        cleanup_result = _shutdown_capture_group(
            monitor=monitor, child=child, child_job=child_job,
            preloader=windivert_preloader, snapshot=wss_operational_snapshot,
            observed_at=(lifecycle_guard.exit_observed_monotonic if lifecycle_guard else None),
            ui_result_reader=(lambda: _group_ui_exit_state(lifecycle_guard)) if lifecycle_guard else None,
        )
        if lifecycle_guard is not None:
            lifecycle_guard.cleanup_result = cleanup_result
            lifecycle_guard.phase = "cleanup_complete" if cleanup_result["resources_complete"] else "cleanup_partial"
            lifecycle_guard._write_status()
    stderr_thread.join(timeout=0.0)

    if helper_exited_unexpectedly:
        runtime_compatibility = inspect_runtime_compatibility(
            helper_diagnostics,
            mitmdump_path=mitmdump_path,
        )
        error_code, error_message, error_hint = _helper_error_details(
            helper_diagnostics,
            runtime_compatibility=runtime_compatibility,
        )
        write_capture_status(
            error_code=error_code,
            error_message=error_message,
            error_hint=error_hint,
        )
        _write_wss_helper_status(
            log_dir,
            phase="exited_unexpectedly",
            helper_pid=child.pid,
            launch_id=str(args.lifecycle_launch_id or ""),
            diagnostics=helper_diagnostics,
            helper_exit_code=child.returncode,
            error_code=error_code,
            runtime_compatibility=runtime_compatibility,
            windivert_preload_state=windivert_preloader.state,
            windivert_preload_phase=windivert_preloader.phase,
            monitor_elevation_state=monitor_elevation_state,
            **helper_status_identity,
        )
        write_capture_status(
            error_code=error_code,
            error_message=error_message,
            error_hint=error_hint,
        )
        print(
            "[error] wss_capture_helper_stopped_unexpectedly "
            f"code={child.returncode} reason={error_code}",
            file=sys.stderr,
        )
        return 5
    _write_wss_helper_status(
        log_dir,
        phase="stopped" if cleanup_result["components"].get("helper") == "exited" else "cleanup_partial",
        helper_pid=child.pid,
        launch_id=str(args.lifecycle_launch_id or ""),
        diagnostics=helper_diagnostics,
        helper_exit_code=child.returncode,
        windivert_preload_state=windivert_preloader.state,
        windivert_preload_phase=windivert_preloader.phase,
        monitor_elevation_state=monitor_elevation_state,
        **helper_status_identity,
    )
    if (lifecycle_guard is not None and lifecycle_guard.exit_observed_monotonic is not None
            and (not cleanup_result["resources_complete"] or not cleanup_result["within_budget"])):
        print("[error] game_exit_cleanup_partial; see lifecycle_status.json", file=sys.stderr, flush=True)
        return 5
    if child.returncode not in (0, -15, 1):
        print(
            f"[error] wss capture helper exited code={child.returncode}",
            file=sys.stderr,
        )
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
