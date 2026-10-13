# -*- coding: utf-8 -*-
"""L4 客户端激活流程: 取设备指纹 + token -> POST /activate -> 拿 lease+master_key
-> 写设备绑定诊断缓存 -> 装 LeaseProvider(上层 data_crypt/is_activated 走它)。

与服务端(scripts/activation_server.py)配对:
  · 有 token = 付费订阅(mode=paid); 无 token = 半免费试用(mode=trial, 每设备 trial_hours 小时)。
  · 服务端是权威; 当前协议不能在客户端独立验 lease 签名，故落盘缓存不形成离线授权。

token 双通道(作者拍板): 优先读【激活文件】activation.txt(群友收到 token 粘贴存盘, 持久);
文件没有时由 UI 激活框传入 ui_token, 成功后写入激活文件 -> 下次自动激活。env 可覆盖(仅测试)。

⚠️【信任边界】服务端返回 HMAC 签名 lease，但客户端没有服务端 secret，当前内核也没有独立公钥
verify_lease 接口。设备绑定 AES-GCM 只能提供本机 at-rest 包装，不能证明授权来自服务端：知道 token
和设备信息的本机调用者可以重新封装任意字段。因此缓存只保留给原子持久化、诊断和后续迁移使用；
每次冷启动授权都必须得到 HTTPS 服务端的当前成功响应。后续若接入独立公钥签名，必须先在内核中
实现不可伪造的 lease 验证，再单独恢复离线授权。
"""
from __future__ import annotations

import base64
import binascii
import contextlib
import json
import os
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..http_tls import (
    PRODUCT_USER_AGENT,
    urlopen_verified as _shared_urlopen_verified,
    verified_ssl_context,
)
from . import activation_http_e2ee
from .data_crypt import DataKeyUnavailable, decrypt_bytes, encrypt_bytes

# 联系方式(试用到期/购买引导) —— 与 handoff / 记忆一致。
CONTACT = "Open-source support: https://github.com/SeasonCake/bidking-inference/issues"

_UI_ACTIVATION_TOKEN_MAX_CHARS = 4096
_UI_ACTIVATION_TOKEN_SIGNATURE_CHARS = 64

_ENV_TOKEN = "BIDKING_ACTIVATION_TOKEN"      # 测试用: 直接给 token(优先级最高)
_ENV_TOKEN_FILE = "BIDKING_ACTIVATION_FILE"  # 覆盖激活文件路径
_ENV_DISABLE_ACTIVATION_DIRECT_RETRY = "BIDKING_DISABLE_ACTIVATION_DIRECT_RETRY"
CLIENT_SECURITY_EPOCH = 1                    # 生产切代门：1 = HTTPS-only + 新激活协议

# 【权威拒绝】的 deny reason(服务端 401/403/426 明确拒)—— 只有这些才清缓存+硬降级。
# 其余(429/5xx/503/无 reason = 服务端重启/限流/未配置等瞬时抖动)不清缓存，
# 但缓存仅用于诊断/迁移，绝不据此恢复授权。见 activation_server 的 reason 集。
_AUTHORITATIVE_DENY_REASONS = frozenset(
    {
        "revoked",
        "device_revoked",
        "device_mismatch",
        "bad_token",
        "trial_expired",
        "expired",
        "upgrade_required",
    }
)
_PERSISTENT_AUTHORITATIVE_DENY_REASONS = frozenset(
    {"revoked", "device_revoked", "upgrade_required"}
)
_AUTHORITATIVE_DENY_SCHEMA_VERSION = 1
# Shared by the signed-response parser and enforce's compatibility projection.
# Keeping one bound prevents a raw ActivationResult from bypassing the exact
# same epoch limit applied to a signed online lease.
MAX_SAFE_ACTIVATION_EPOCH = 253_402_300_799
_MAX_SAFE_EPOCH = MAX_SAFE_ACTIVATION_EPOCH
_MASTER_KEY_BYTES = 32
_MISSING = object()
_CACHE_INVALIDATED_IN_PROCESS = False
_ACTIVATION_IDENTITY_SOURCE_LOCK = threading.RLock()
# Serialize the short "last identity check -> transport thread entered" edge
# against every supported identity writer.  Network completion never holds
# this lock; clear/rebind can proceed as soon as the transport call has begun.
_ACTIVATION_TRANSPORT_START_LOCK = threading.RLock()
_ACTIVATION_TRANSPORT_TRACE_LOCAL = threading.local()
_ACTIVATION_DIRECT_RETRY_FENCE_LOCAL = threading.local()
_ACTIVATION_RECHECK_HISTORY_LOCK = threading.Lock()
_RUNTIME_ACTIVATION_DENIED = threading.Event()
# Even values are stable snapshots; every authoritative identity write advances
# through an odd in-progress value to a new even value.  Readers and writers
# share the same short lock, so token/device can never be assembled from two
# different product writes.
_ACTIVATION_IDENTITY_SOURCE_REVISION = 0
_ACTIVATION_CLEAR_EPOCH = 0
_ACTIVATION_CLEAR_STATE = None
_UPLOAD_LEASE_SCHEMA_VERSION = 1
_UPLOAD_LEASE_MAX_CHARS = 4096
_UPLOAD_LEASE_KEYS = frozenset(
    {
        "schema_version", "kind", "subject_kind", "subject_id", "uid_hash",
        "device_alias", "security_epoch", "lease_id", "iat", "exp",
        "entitlement_exp",
    }
)
_PROCESS_UPLOAD_LEASE = None
_PET_FEATURE_KEY = "pet_companion"
# A2 only consumes this structure from the current successful HTTPS activation
# response.  The suffix shape is an ingress-integrity check, not independent
# client-side cryptographic verification of the server HMAC.
_PET_FEATURE_USAGE_SCHEMA_VERSION = 1
_PET_SERVER_SIGNATURE_HEX_LENGTH = 64
_PET_MAX_SEQUENCE = (1 << 63) - 1
_PET_USAGE_EVENT_KEYS = frozenset({"session_id", "seq", "visible"})
_PET_STATUS_VALUES = frozenset(
    {
        "active",
        "available",
        "exhausted",
        "expired",
        "locked",
        "paused",
        "sequence_conflict",
        "session_conflict",
        "state_error",
    }
)
_PLAN_PET_ACCESS = {
    "30d": "none",
    "90d": "none",
    "365d": "full",
    "custom": "none",
    "trial": "none",
    "legacy_permanent": "full",
    "permanent": "full",
}
_PERMANENT_PLAN_KINDS = frozenset({"legacy_permanent", "permanent"})

_ACTIVATION_RECHECK_HISTORY_SCHEMA = 1
_ACTIVATION_RECHECK_HISTORY_MAX_SEQUENCES = 4
_ACTIVATION_RECHECK_HISTORY_MAX_ATTEMPTS = 12
_ACTIVATION_RECHECK_HISTORY_MAX_FILES = 16
_ACTIVATION_RECHECK_HISTORY_TTL_SECONDS = 86_400
_ACTIVATION_RECHECK_HISTORY_MAX_BYTES = 65_536
_ACTIVATION_RECHECK_ENDPOINTS = frozenset(
    {"endpoint_1", "endpoint_2", "endpoint_3", "endpoint_4"}
)
_ACTIVATION_RECHECK_NETWORK_FAILURE_STAGES = {
    "dns_lookup_failed": "dns",
    "connect_timeout": "tcp",
    "connect_refused": "tcp",
    "transport_os_error": "tcp",
    "tls_connection_reset": "tls",
    "tls_handshake_failed": "tls",
    "transport_unknown": "unknown",
}
_ACTIVATION_RECHECK_HTTP_CLASSES = frozenset(
    {"http_response", "http_reachable_invalid_payload"}
)


# --------------------------------------------------------------------------- #
# 内核 / 设备指纹 / 设备绑定 key —— 优先 Rust 内核(高逆向成本), dev 无 .pyd 时 Python 兜底(与 Rust 同式)
# --------------------------------------------------------------------------- #
def _kernel() -> Optional[Any]:
    try:
        from .rust_provider import load_kernel
        return load_kernel()
    except Exception:  # noqa: BLE001
        return None


def _read_machine_guid() -> Optional[str]:
    """Windows MachineGuid(64 位视图, 与 Rust winreg 一致)。非 Windows/取不到 -> None。"""
    try:
        import winreg  # Windows only
        # KEY_WOW64_64KEY: 强制 64 位注册表视图, 与 native .pyd(64 位)读到同一 GUID。
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as k:
            v = winreg.QueryValueEx(k, "MachineGuid")[0]
            return str(v) if v else None
    except Exception:  # noqa: BLE001
        return None


def device_fingerprint() -> str:
    """设备指纹: 内核优先; dev 兜底 = sha256(b"bidking-dev-v1:" + MachineGuid)[:32](与 lib.rs 同式)。"""
    k = _kernel()
    if k is not None:
        try:
            return str(k.device_fingerprint())
        except Exception:  # noqa: BLE001
            pass
    import hashlib
    raw = _read_machine_guid() or "unknown-device"
    return hashlib.sha256(b"bidking-dev-v1:" + raw.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class ActivationIdentitySnapshot:
    """Process-private, versioned credential/device snapshot.

    Raw values never leave the activation client and are excluded from repr.
    ``revision`` is irreversible for the process lifetime and can therefore
    distinguish A -> B -> A even when the final strings match again.
    """

    token: str = field(repr=False)
    device: str = field(repr=False)
    revision: int
    clear_epoch: int = 0


@dataclass(frozen=True)
class UploadAuthoritySnapshot:
    """One atomic process-private identity/lease view for an upload attempt."""

    identity: ActivationIdentitySnapshot = field(repr=False)
    lease: Optional[str] = field(default=None, repr=False)


@dataclass(frozen=True)
class _ActivationClearEpochState:
    """One process-local clear/rebind boundary.

    A failed clear keeps the old credential physically reachable.  The runtime
    deny latch may therefore only be released by a server grant whose identity
    belongs to this exact epoch, after the identity sources were fully cleared,
    and whose credential/device has crossed away from the pre-clear identity.
    """

    epoch: int
    begin_revision: int
    pre_clear_identity: Optional[ActivationIdentitySnapshot] = field(
        default=None,
        repr=False,
    )
    identity_sources_cleared: bool = False
    post_clear_revision: int = -1


def activation_identity_snapshot(
    ui_token: Optional[str] = None,
) -> Optional[ActivationIdentitySnapshot]:
    """Read credential and device as one stable, versioned authority snapshot."""
    with _ACTIVATION_IDENTITY_SOURCE_LOCK:
        revision = _ACTIVATION_IDENTITY_SOURCE_REVISION
        if revision % 2:
            return None
        try:
            token = resolve_token(ui_token)
            device = device_fingerprint()
            # A second in-lock read catches uncoordinated external/test sources
            # changing during an accessor call. Product writers cannot interleave
            # because they use the same lock and revision below.
            token_check = resolve_token(ui_token)
            device_check = device_fingerprint()
        except Exception:  # noqa: BLE001
            return None
        if (
            revision != _ACTIVATION_IDENTITY_SOURCE_REVISION
            or not isinstance(token, str)
            or not isinstance(device, str)
            or not device
            or token != token_check
            or device != device_check
        ):
            return None
        return ActivationIdentitySnapshot(
            token=token,
            device=device,
            revision=revision,
            clear_epoch=_ACTIVATION_CLEAR_EPOCH,
        )


@contextlib.contextmanager
def activation_identity_commit_guard():
    """Hold the one global lock order used by transport begin and identity writes.

    Enforce callers may invoke a lower-level identity writer while this guard is
    active.  Acquiring transport-start before identity here keeps that nested
    path re-entrant and prevents an identity -> transport inversion against a
    concurrent Pet request.
    """
    with (
        _ACTIVATION_TRANSPORT_START_LOCK,
        _ACTIVATION_IDENTITY_SOURCE_LOCK,
    ):
        yield


def activation_runtime_denied() -> bool:
    """Expose the process-local revocation latch to every authorization view."""
    if _RUNTIME_ACTIVATION_DENIED.is_set():
        return True
    reason = persistent_authoritative_deny_reason()
    if reason is not None:
        _RUNTIME_ACTIVATION_DENIED.set()
        return True
    return False


def _run_activation_identity_write(
    operation,
    *,
    expected_revision: Optional[int] = None,
) -> tuple[bool, Any, int]:
    """Run one authoritative identity mutation under the versioned short lock."""
    global _ACTIVATION_IDENTITY_SOURCE_REVISION
    with (
        _ACTIVATION_TRANSPORT_START_LOCK,
        _ACTIVATION_IDENTITY_SOURCE_LOCK,
    ):
        if (
            expected_revision is not None
            and expected_revision != _ACTIVATION_IDENTITY_SOURCE_REVISION
        ):
            return False, None, _ACTIVATION_IDENTITY_SOURCE_REVISION
        _ACTIVATION_IDENTITY_SOURCE_REVISION += 1
        try:
            result = operation()
        finally:
            _ACTIVATION_IDENTITY_SOURCE_REVISION += 1
        return True, result, _ACTIVATION_IDENTITY_SOURCE_REVISION


def begin_activation_identity_clear() -> int:
    """Irreversibly revoke runtime access before clear/rebind side effects.

    The deny latch and identity revision advance under the same short authority
    lock.  Filesystem/env/cache work happens later and may fail; none of those
    failures can reopen the previous provider.  A complete current-identity
    server grant is the only code path allowed to clear the latch.
    """

    global _ACTIVATION_CLEAR_EPOCH, _ACTIVATION_CLEAR_STATE

    with (
        _ACTIVATION_TRANSPORT_START_LOCK,
        _ACTIVATION_IDENTITY_SOURCE_LOCK,
    ):
        pre_clear_identity = activation_identity_snapshot()
        _ACTIVATION_CLEAR_EPOCH += 1
        _accepted, _result, revision = _run_activation_identity_write(
            _RUNTIME_ACTIVATION_DENIED.set
        )
        _ACTIVATION_CLEAR_STATE = _ActivationClearEpochState(
            epoch=_ACTIVATION_CLEAR_EPOCH,
            begin_revision=revision,
            pre_clear_identity=pre_clear_identity,
        )
        return revision


def _activation_clear_epoch_prepared(revision: Optional[int]) -> bool:
    with _ACTIVATION_IDENTITY_SOURCE_LOCK:
        state = _ACTIVATION_CLEAR_STATE
        return bool(
            isinstance(revision, int)
            and not isinstance(revision, bool)
            and state is not None
            and state.begin_revision == revision
            and revision == _ACTIVATION_IDENTITY_SOURCE_REVISION
            and _RUNTIME_ACTIVATION_DENIED.is_set()
        )


def _finish_activation_identity_clear(
    *,
    begin_revision: Optional[int],
    cleared: bool,
) -> bool:
    """Record a completed identity-source clear without weakening the deny latch."""
    global _ACTIVATION_CLEAR_STATE

    with _ACTIVATION_IDENTITY_SOURCE_LOCK:
        state = _ACTIVATION_CLEAR_STATE
        if (
            state is None
            or not isinstance(begin_revision, int)
            or isinstance(begin_revision, bool)
            or state.begin_revision != begin_revision
        ):
            return False
        post_clear_identity = activation_identity_snapshot()
        crossed = bool(
            cleared
            and state.pre_clear_identity is not None
            and post_clear_identity is not None
            and post_clear_identity.clear_epoch == state.epoch
            and post_clear_identity.revision >= state.begin_revision
            and (
                post_clear_identity.token != state.pre_clear_identity.token
                or post_clear_identity.device != state.pre_clear_identity.device
            )
        )
        _ACTIVATION_CLEAR_STATE = _ActivationClearEpochState(
            epoch=state.epoch,
            begin_revision=state.begin_revision,
            pre_clear_identity=state.pre_clear_identity,
            identity_sources_cleared=crossed,
            post_clear_revision=(
                post_clear_identity.revision
                if crossed and post_clear_identity is not None
                else -1
            ),
        )
        return crossed


def _activation_clear_epoch_allows_grant(
    identity: Optional[ActivationIdentitySnapshot],
) -> bool:
    """Return whether a grant may release the current clear/rebind deny epoch."""
    if not _RUNTIME_ACTIVATION_DENIED.is_set():
        return True
    state = _ACTIVATION_CLEAR_STATE
    if state is None:
        # Runtime denial from commit recovery or a server rejection is released
        # by the established complete-current-grant path, not by a clear epoch.
        return True
    return bool(
        identity is not None
        and state.identity_sources_cleared
        and state.pre_clear_identity is not None
        and identity.clear_epoch == state.epoch
        and identity.revision >= state.post_clear_revision
        and (
            identity.token != state.pre_clear_identity.token
            or identity.device != state.pre_clear_identity.device
        )
    )


def _complete_activation_clear_epoch(
    identity: Optional[ActivationIdentitySnapshot],
) -> bool:
    """Consume the epoch only after the same complete grant remains current."""
    global _ACTIVATION_CLEAR_STATE

    if not _activation_clear_epoch_allows_grant(identity):
        return False
    if _ACTIVATION_CLEAR_STATE is not None:
        _ACTIVATION_CLEAR_STATE = None
    return True


def activation_submission_preflight(ui_token: Optional[str] = None) -> bool:
    """Whether the current clear boundary permits this identity to seek a grant.

    This is an eligibility check only; server authority and the final commit
    gate remain mandatory. No identity values leave the client through it.
    """
    with _ACTIVATION_IDENTITY_SOURCE_LOCK:
        return _activation_clear_epoch_allows_grant(
            activation_identity_snapshot(ui_token)
        )


def _wrap_key(token: str, device: str) -> bytes:
    """缓存 at-rest 加密 key = 设备绑定 HKDF(同 Rust derive_data_key)。内核优先, dev Python 兜底。
    用途仅【本机缓存包装】(拷别机解不开 + 防手改); 非出厂数据主密钥。"""
    k = _kernel()
    if k is not None:
        try:
            return bytes(k.derive_data_key(token, device))
        except Exception:  # noqa: BLE001
            pass
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    ikm = f"{token}|{device}".encode("utf-8")
    return HKDF(
        algorithm=hashes.SHA256(), length=32,
        salt=b"bidking-data-key-v1", info=b"aes-256-data",
    ).derive(ikm)


# --------------------------------------------------------------------------- #
# 路径: 激活文件(token) + 加密缓存(lease+master_key)
# --------------------------------------------------------------------------- #
def _app_dir() -> Path:
    """每用户可写目录(更新不丢、设备本地)。%LOCALAPPDATA%\\bidking, 回退 ~/.bidking。"""
    base = os.environ.get("LOCALAPPDATA")
    d = (Path(base) if base else Path.home()) / "bidking"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        pass
    return d


def _package_dir() -> Path:
    """包/仓库根(群友可把 activation.txt 丢这里)。secure -> bidking_lab -> src -> 根。"""
    return Path(__file__).resolve().parents[3]


def _token_file() -> Path:
    """写入用激活文件(UI 输入持久化到此)。"""
    if p := os.environ.get(_ENV_TOKEN_FILE):
        return Path(p)
    return _app_dir() / "activation.txt"


def _token_candidates() -> list[Path]:
    """读取候选(优先级): 覆盖路径 -> 用户目录 -> 包目录。"""
    out: list[Path] = []
    if p := os.environ.get(_ENV_TOKEN_FILE):
        out.append(Path(p))
    out.append(_app_dir() / "activation.txt")
    out.append(_package_dir() / "activation.txt")
    return out


def _read_persisted_token() -> str:
    """读磁盘上已存的 token(不含 env / UI 参数)。多路径时与 resolve_token 同优先级。"""
    for p in _token_candidates():
        try:
            if p.is_file():
                t = p.read_text(encoding="utf-8").strip()
                if t:
                    return t
        except Exception:  # noqa: BLE001
            continue
    return ""


def is_single_activation_token_input(value: Any) -> bool:
    """Accept only one complete token minted by the current public issuer.

    This is a local UI boundary, not signature verification.  It prevents a
    copied chat message, surrounding whitespace, or a partial token from being
    sent to any activation endpoint.  Persisted and environment credentials
    keep their existing compatibility path; the server remains authoritative.
    """
    if (
        type(value) is not str
        or not value
        or len(value) > _UI_ACTIVATION_TOKEN_MAX_CHARS
        or value != value.strip()
        or any(char.isspace() for char in value)
        or "." not in value
    ):
        return False
    payload, signature = value.rsplit(".", 1)
    if (
        len(signature) != _UI_ACTIVATION_TOKEN_SIGNATURE_CHARS
        or any(char not in "0123456789abcdef" for char in signature)
    ):
        return False
    parts = payload.split("|")
    if len(parts) not in (3, 4, 5) or parts[0] != "v1" or not parts[1]:
        return False
    if not parts[2] or any(char not in "0123456789" for char in parts[2]):
        return False
    if len(parts) >= 4 and (
        not parts[3]
        or any(char not in "0123456789" for char in parts[3])
    ):
        return False
    return len(parts) != 5 or parts[4] == "r"


def _clear_persisted_tokens() -> bool:
    """删掉所有候选 activation.txt(换 token 时用, 防旧文件在下次冷启动盖回新 token)。"""
    cleared = True
    for p in _token_candidates():
        try:
            p.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            cleared = False
    return cleared


def resolve_token(ui_token: Optional[str] = None) -> str:
    """双通道取 token: env(测试) -> UI 显式输入 -> 激活文件。都没有 -> ""(走试用)。
    ⚠️【纯读取, 无副作用】: 不在此清旧 token/缓存。旧实现在这里就 _clear_cache+_clear_persisted_tokens+save,
    等于在新 token 未经服务端校验前先毁掉旧的有效激活 —— 粘错 token 或断网时把用户锁死。
    清旧+持久化改由 activate() 在 POST 校验【成功后】调 _commit_ui_token()(#1)。"""
    if v := os.environ.get(_ENV_TOKEN):
        return v.strip()
    if ui_token and ui_token.strip():
        return ui_token.strip()
    return _read_persisted_token()


def _commit_ui_token(
    token: str,
    *,
    expected_revision: Optional[int] = None,
) -> bool:
    """原子持久化已验证 token；影子副本只在 token+cache 都成功后清理。"""
    token = token.strip()
    if not token:
        return False
    with (
        _ACTIVATION_TRANSPORT_START_LOCK,
        _ACTIVATION_IDENTITY_SOURCE_LOCK,
    ):
        if (
            expected_revision is not None
            and expected_revision != _ACTIVATION_IDENTITY_SOURCE_REVISION
        ):
            return False
        # Keep the established save_token failure seam while the shared RLock
        # makes the expected-revision check and versioned write indivisible.
        return save_token(token)


def _cleanup_shadow_tokens() -> None:
    """Best-effort cleanup after the new token and its cache are both durable."""
    target = _token_file()
    for candidate in _token_candidates():
        if candidate == target:
            continue
        try:
            candidate.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            continue


def _snapshot_activation_persistence() -> Optional[dict[str, Any]]:
    """Capture the two-file activation state before rotating a UI token.

    Atomic replacement protects each file individually.  This snapshot supplies the cross-file
    rollback needed when the token replacement succeeds but the matching encrypted cache does not.
    """
    files: dict[Path, Optional[bytes]] = {}
    try:
        for path in (_token_file(), _cache_file(), _cache_invalid_file()):
            files[path] = path.read_bytes() if path.exists() else None
    except (OSError, ValueError):
        return None
    return {
        "files": files,
        "cache_invalidated_in_process": bool(_CACHE_INVALIDATED_IN_PROCESS),
    }


def _restore_activation_persistence(snapshot: dict[str, Any]) -> bool:
    """Restore an earlier token/cache/tombstone set using atomic writes."""
    global _CACHE_INVALIDATED_IN_PROCESS
    restored = True
    files = snapshot.get("files")
    if not isinstance(files, dict):
        return False
    for path, content in files.items():
        try:
            path = Path(path)
            if content is None:
                path.unlink(missing_ok=True)
            elif isinstance(content, bytes):
                _atomic_write_bytes(path, content)
            else:
                restored = False
        except Exception:  # noqa: BLE001
            restored = False
    _CACHE_INVALIDATED_IN_PROCESS = bool(snapshot.get("cache_invalidated_in_process"))
    return restored


def save_token(token: str) -> bool:
    """把 token 原子持久化到激活文件(UI 输入后调)。"""
    def _write() -> bool:
        try:
            _atomic_write_bytes(_token_file(), (token.strip() + "\n").encode("utf-8"))
            return True
        except Exception:  # noqa: BLE001
            return False

    _accepted, result, _revision = _run_activation_identity_write(_write)
    return bool(result)


def _cache_file() -> Path:
    return _app_dir() / "activation_cache.bin"


def _cache_invalid_file() -> Path:
    """Persistent fail-closed marker used when a rejected cache cannot be removed."""
    return _app_dir() / "activation_cache.invalid"


def _authoritative_deny_file() -> Path:
    """Privacy-safe cross-process authority marker; never stores identity data."""
    return _app_dir() / "activation_authoritative_deny.json"


def persistent_authoritative_deny_reason() -> Optional[str]:
    """Return the persisted safe reason, or ``state_error`` for a malformed marker."""
    path = _authoritative_deny_file()
    try:
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return "state_error"
    if (
        type(payload) is not dict
        or any(type(key) is not str for key in dict.keys(payload))
        or frozenset(dict.keys(payload)) != frozenset({"schema", "reason"})
        or type(dict.get(payload, "schema")) is not int
        or dict.get(payload, "schema") != _AUTHORITATIVE_DENY_SCHEMA_VERSION
        or type(dict.get(payload, "reason")) is not str
        or dict.get(payload, "reason") not in _PERSISTENT_AUTHORITATIVE_DENY_REASONS
    ):
        return "state_error"
    return dict.get(payload, "reason")


def _persist_authoritative_deny(reason: str) -> bool:
    """Persist one bounded non-identity reason before revoking runtime authority."""
    if type(reason) is not str or reason not in _PERSISTENT_AUTHORITATIVE_DENY_REASONS:
        return False
    try:
        payload = json.dumps(
            {
                "schema": _AUTHORITATIVE_DENY_SCHEMA_VERSION,
                "reason": reason,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        _atomic_write_bytes(_authoritative_deny_file(), payload)
        return persistent_authoritative_deny_reason() == reason
    except Exception:  # noqa: BLE001
        return False


def _clear_persistent_authoritative_deny() -> bool:
    try:
        _authoritative_deny_file().unlink(missing_ok=True)
        return not _authoritative_deny_file().exists()
    except Exception:  # noqa: BLE001
        return False


def _revoke_cached_upload_authority() -> bool:
    """Clear an already-decrypted upload config without importing the live stack."""
    global _PROCESS_UPLOAD_LEASE
    with _ACTIVATION_IDENTITY_SOURCE_LOCK:
        _PROCESS_UPLOAD_LEASE = None
    module = sys.modules.get("bidking_lab.live.sample_upload")
    if module is None:
        return True
    revoke = getattr(module, "revoke_upload_authority", None)
    if not callable(revoke):
        return False
    try:
        return revoke() is True
    except Exception:  # noqa: BLE001
        return False


def _project_upload_lease(envelope: Any, *, now: int) -> Optional[tuple[str, int]]:
    """Project only the opaque envelope and expiry from one online response."""
    if (
        type(envelope) is not str
        or not envelope
        or len(envelope) > _UPLOAD_LEASE_MAX_CHARS
        or envelope.count(".") != 1
    ):
        return None
    raw, signature = envelope.rsplit(".", 1)
    if (
        len(signature) != 64
        or any(char not in "0123456789abcdef" for char in signature)
    ):
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if (
        type(payload) is not dict
        or any(type(key) is not str for key in dict.keys(payload))
        or frozenset(dict.keys(payload)) != _UPLOAD_LEASE_KEYS
        or dict.get(payload, "schema_version") != _UPLOAD_LEASE_SCHEMA_VERSION
        or dict.get(payload, "kind") != "sample_upload"
        or type(dict.get(payload, "exp")) is not int
        or not now < dict.get(payload, "exp") <= MAX_SAFE_ACTIVATION_EPOCH
    ):
        return None
    return envelope, dict.get(payload, "exp")


def _install_process_upload_lease(
    projected: Optional[tuple[str, int]],
    identity: ActivationIdentitySnapshot,
) -> None:
    global _PROCESS_UPLOAD_LEASE
    if projected is None:
        _PROCESS_UPLOAD_LEASE = None
        return
    envelope, exp = projected
    _PROCESS_UPLOAD_LEASE = (
        envelope,
        exp,
        identity.token,
        identity.device,
        identity.revision,
        identity.clear_epoch,
    )


def current_upload_lease(*, now: Optional[int] = None) -> Optional[str]:
    """Return the current process-only lease iff activation identity still matches."""
    snapshot = current_upload_authority_snapshot(now=now)
    return snapshot.lease if snapshot is not None else None


def current_upload_authority_snapshot(
    *,
    now: Optional[int] = None,
) -> Optional[UploadAuthoritySnapshot]:
    """Atomically bind the optional opaque lease to one versioned identity."""
    now = int(time.time()) if now is None else now
    if type(now) is not int or activation_runtime_denied():  # noqa: E721
        return None
    with _ACTIVATION_IDENTITY_SOURCE_LOCK:
        current = activation_identity_snapshot()
        if current is None:
            return None
        value = _PROCESS_UPLOAD_LEASE
        if type(value) is not tuple or len(value) != 6:  # noqa: E721
            return UploadAuthoritySnapshot(identity=current, lease=None)
        envelope, exp, token, device, revision, clear_epoch = value
        if type(exp) is not int or exp <= now:  # noqa: E721
            return UploadAuthoritySnapshot(identity=current, lease=None)
        if (
            current.token,
            current.device,
            current.revision,
            current.clear_epoch,
        ) != (token, device, revision, clear_epoch):
            return UploadAuthoritySnapshot(identity=current, lease=None)
        return UploadAuthoritySnapshot(
            identity=current,
            lease=envelope if type(envelope) is str else None,
        )


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{time.time_ns()}")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


def _activation_recheck_history_file() -> Path:
    """Keep each process writer independent; readers merge only safe rows."""
    return _app_dir() / f"activation_recheck_history_{os.getpid()}.json"


def _safe_recheck_epoch(value: Any) -> Optional[int]:
    if type(value) is not int or not 0 <= value <= MAX_SAFE_ACTIVATION_EPOCH:
        return None
    return value


def _project_activation_recheck_attempt(value: Any) -> Optional[dict[str, Any]]:
    if type(value) is not dict or frozenset(dict.keys(value)) != frozenset(
        {"at", "endpoint", "stage", "failure_class"}
    ):
        return None
    if any(type(key) is not str for key in dict.keys(value)):
        return None
    at = _safe_recheck_epoch(dict.get(value, "at"))
    endpoint = dict.get(value, "endpoint")
    stage = dict.get(value, "stage")
    failure_class = dict.get(value, "failure_class")
    if (
        at is None
        or type(endpoint) is not str
        or endpoint not in _ACTIVATION_RECHECK_ENDPOINTS
        or type(stage) is not str
        or stage not in {"dns", "tcp", "tls", "http", "unknown"}
        or type(failure_class) is not str
        or failure_class
        not in (
            frozenset(_ACTIVATION_RECHECK_NETWORK_FAILURE_STAGES)
            | _ACTIVATION_RECHECK_HTTP_CLASSES
        )
    ):
        return None
    expected_stage = _ACTIVATION_RECHECK_NETWORK_FAILURE_STAGES.get(failure_class)
    if expected_stage is not None and stage != expected_stage:
        return None
    if failure_class in _ACTIVATION_RECHECK_HTTP_CLASSES and stage != "http":
        return None
    return {
        "at": at,
        "endpoint": endpoint,
        "stage": stage,
        "failure_class": failure_class,
    }


def _project_activation_recheck_sequence(value: Any) -> Optional[dict[str, Any]]:
    expected_keys = frozenset(
        {
            "started_at",
            "last_failure_at",
            "recovered_at",
            "attempt_count",
            "attempts",
        }
    )
    if (
        type(value) is not dict
        or any(type(key) is not str for key in dict.keys(value))
        or frozenset(dict.keys(value)) != expected_keys
    ):
        return None
    started_at = _safe_recheck_epoch(dict.get(value, "started_at"))
    last_failure_at = _safe_recheck_epoch(dict.get(value, "last_failure_at"))
    recovered_at_value = dict.get(value, "recovered_at")
    recovered_at = (
        None
        if recovered_at_value is None
        else _safe_recheck_epoch(recovered_at_value)
    )
    attempt_count = dict.get(value, "attempt_count")
    raw_attempts = dict.get(value, "attempts")
    if (
        started_at is None
        or last_failure_at is None
        or last_failure_at < started_at
        or (recovered_at_value is not None and recovered_at is None)
        or (recovered_at is not None and recovered_at < last_failure_at)
        or type(attempt_count) is not int
        or not 1 <= attempt_count <= 64
        or type(raw_attempts) is not list
        or not 1 <= len(raw_attempts) <= _ACTIVATION_RECHECK_HISTORY_MAX_ATTEMPTS
        or attempt_count < len(raw_attempts)
    ):
        return None
    attempts: list[dict[str, Any]] = []
    for raw_attempt in raw_attempts:
        attempt = _project_activation_recheck_attempt(raw_attempt)
        if attempt is None or attempt["stage"] == "http":
            return None
        attempts.append(attempt)
    attempt_times = [attempt["at"] for attempt in attempts]
    if (
        any(
            at < started_at or at > last_failure_at
            for at in attempt_times
        )
        or any(
            previous > current
            for previous, current in zip(attempt_times, attempt_times[1:])
        )
        or attempt_times[-1] != last_failure_at
        or (
            attempt_count == len(attempts)
            and attempt_times[0] != started_at
        )
        or (
            attempt_count > len(attempts)
            and len(attempts) != _ACTIVATION_RECHECK_HISTORY_MAX_ATTEMPTS
        )
    ):
        return None
    return {
        "started_at": started_at,
        "last_failure_at": last_failure_at,
        "recovered_at": recovered_at,
        "attempt_count": attempt_count,
        "attempts": attempts,
    }


def _read_activation_recheck_file(
    path: Path,
    *,
    now: int,
) -> list[dict[str, Any]]:
    try:
        if not path.is_file() or path.stat().st_size > _ACTIVATION_RECHECK_HISTORY_MAX_BYTES:
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - diagnostics are best-effort and no-throw
        return []
    if (
        type(payload) is not dict
        or any(type(key) is not str for key in dict.keys(payload))
        or frozenset(dict.keys(payload))
        != frozenset({"schema", "updated_at", "sequences"})
        or dict.get(payload, "schema") != _ACTIVATION_RECHECK_HISTORY_SCHEMA
        or _safe_recheck_epoch(dict.get(payload, "updated_at")) is None
        or type(dict.get(payload, "sequences")) is not list
        or len(dict.get(payload, "sequences"))
        > _ACTIVATION_RECHECK_HISTORY_MAX_SEQUENCES
    ):
        return []
    projected_sequences: list[dict[str, Any]] = []
    for raw_sequence in dict.get(payload, "sequences"):
        sequence = _project_activation_recheck_sequence(raw_sequence)
        if sequence is None:
            return []
        projected_sequences.append(sequence)
    for previous, current in zip(
        projected_sequences, projected_sequences[1:]
    ):
        recovered_at = previous["recovered_at"]
        if recovered_at is None or current["started_at"] < recovered_at:
            return []
    earliest = max(0, now - _ACTIVATION_RECHECK_HISTORY_TTL_SECONDS)
    return [
        sequence
        for sequence in projected_sequences
        if earliest <= sequence["last_failure_at"] <= now + 300
    ]


def read_activation_recheck_history(*, now: Optional[int] = None) -> dict[str, Any]:
    """Return a fixed, privacy-safe bounded history merged across client processes."""
    epoch_now = int(time.time()) if now is None else _safe_recheck_epoch(now)
    if epoch_now is None:
        return {"schema": _ACTIVATION_RECHECK_HISTORY_SCHEMA, "sequences": []}
    sequences: list[dict[str, Any]] = []
    try:
        candidates = sorted(
            _app_dir().glob("activation_recheck_history_*.json"),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )[:_ACTIVATION_RECHECK_HISTORY_MAX_FILES]
    except Exception:  # noqa: BLE001
        candidates = []
    for path in candidates:
        sequences.extend(_read_activation_recheck_file(path, now=epoch_now))
    sequences.sort(
        key=lambda row: (row["last_failure_at"], row["started_at"]),
        reverse=True,
    )
    recent = sequences[:_ACTIVATION_RECHECK_HISTORY_MAX_SEQUENCES]
    recent.reverse()
    return {"schema": _ACTIVATION_RECHECK_HISTORY_SCHEMA, "sequences": recent}


def _project_activation_recheck_trace(
    trace: Any,
) -> Optional[list[dict[str, Any]]]:
    if type(trace) is not tuple or len(trace) > _ACTIVATION_RECHECK_HISTORY_MAX_ATTEMPTS:
        return None
    projected: list[dict[str, Any]] = []
    for row in trace:
        if type(row) is not tuple or len(row) != 4:
            return None
        endpoint, stage, failure_class, at = row
        attempt = _project_activation_recheck_attempt(
            {
                "at": at,
                "endpoint": endpoint,
                "stage": stage,
                "failure_class": failure_class,
            }
        )
        if attempt is None:
            return None
        projected.append(attempt)
    return projected


def record_activation_recheck_outcome(
    trace: Any,
    *,
    allow_new_sequence: bool,
    recovered: bool,
    now: Optional[int] = None,
) -> None:
    """Persist one current-identity recheck outcome using a strict fixed schema."""
    epoch_now = int(time.time()) if now is None else _safe_recheck_epoch(now)
    projected = _project_activation_recheck_trace(trace)
    if epoch_now is None or projected is None or not projected:
        return
    has_http = any(row["stage"] == "http" for row in projected)
    network_attempts = [row for row in projected if row["stage"] != "http"]
    if recovered:
        if not has_http:
            return
    elif has_http or len(network_attempts) != len(projected):
        return

    path = _activation_recheck_history_file()
    with _ACTIVATION_RECHECK_HISTORY_LOCK:
        sequences = _read_activation_recheck_file(path, now=epoch_now)
        if recovered:
            if sequences and sequences[-1]["recovered_at"] is None:
                current = sequences[-1]
                attempts = (current["attempts"] + network_attempts)[
                    -_ACTIVATION_RECHECK_HISTORY_MAX_ATTEMPTS:
                ]
                sequences[-1] = {
                    **current,
                    "last_failure_at": (
                        network_attempts[-1]["at"]
                        if network_attempts
                        else current["last_failure_at"]
                    ),
                    "recovered_at": epoch_now,
                    "attempt_count": min(
                        64,
                        current["attempt_count"] + len(network_attempts),
                    ),
                    "attempts": attempts,
                }
            elif allow_new_sequence and network_attempts:
                sequences.append(
                    {
                        "started_at": network_attempts[0]["at"],
                        "last_failure_at": network_attempts[-1]["at"],
                        "recovered_at": epoch_now,
                        "attempt_count": len(network_attempts),
                        "attempts": network_attempts,
                    }
                )
            else:
                return
        else:
            if not sequences or sequences[-1]["recovered_at"] is not None:
                if not allow_new_sequence:
                    return
                sequences.append(
                    {
                        "started_at": network_attempts[0]["at"],
                        "last_failure_at": network_attempts[-1]["at"],
                        "recovered_at": None,
                        "attempt_count": len(network_attempts),
                        "attempts": network_attempts,
                    }
                )
            else:
                current = sequences[-1]
                attempts = (current["attempts"] + network_attempts)[
                    -_ACTIVATION_RECHECK_HISTORY_MAX_ATTEMPTS:
                ]
                sequences[-1] = {
                    **current,
                    "last_failure_at": network_attempts[-1]["at"],
                    "attempt_count": min(
                        64,
                        current["attempt_count"] + len(network_attempts),
                    ),
                    "attempts": attempts,
                }
        sequences = sequences[-_ACTIVATION_RECHECK_HISTORY_MAX_SEQUENCES:]
        try:
            payload = json.dumps(
                {
                    "schema": _ACTIVATION_RECHECK_HISTORY_SCHEMA,
                    "updated_at": epoch_now,
                    "sequences": sequences,
                },
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
            _atomic_write_bytes(path, payload)
        except Exception:  # noqa: BLE001 - diagnostics never affect authorization
            return


def _reset_activation_transport_trace() -> None:
    _ACTIVATION_TRANSPORT_TRACE_LOCAL.rows = []


def _append_activation_transport_trace(
    *,
    endpoint_id: str,
    stage: str,
    failure_class: str,
) -> None:
    if endpoint_id not in _ACTIVATION_RECHECK_ENDPOINTS:
        return
    row = (endpoint_id, stage, failure_class, int(time.time()))
    projected = _project_activation_recheck_trace((row,))
    if projected is None:
        return
    rows = getattr(_ACTIVATION_TRANSPORT_TRACE_LOCAL, "rows", None)
    if type(rows) is not list:
        rows = []
        _ACTIVATION_TRANSPORT_TRACE_LOCAL.rows = rows
    if len(rows) < _ACTIVATION_RECHECK_HISTORY_MAX_ATTEMPTS:
        rows.append(row)


def consume_activation_transport_trace() -> tuple[tuple[str, str, str, int], ...]:
    rows = getattr(_ACTIVATION_TRANSPORT_TRACE_LOCAL, "rows", None)
    _ACTIVATION_TRANSPORT_TRACE_LOCAL.rows = []
    if type(rows) is not list:
        return ()
    trace = tuple(rows)
    return trace if _project_activation_recheck_trace(trace) is not None else ()


def _save_cache(*, lease: dict, master_key: bytes, device: str, token: str,
                mode: str, now: int) -> bool:
    """保存设备绑定的诊断/迁移缓存；该文件绝不作为离线授权证明。"""
    global _CACHE_INVALIDATED_IN_PROCESS
    blob = {
        "lease": lease,
        "mk": base64.b64encode(master_key).decode("ascii"),
        "mode": mode,
        "device": device,
        "saved_at": now,
    }
    try:
        enc = encrypt_bytes(json.dumps(blob).encode("utf-8"), _wrap_key(token, device))
        # Cache replacement must finish before an earlier tombstone is cleared.
        _atomic_write_bytes(_cache_file(), enc)
    except Exception:  # noqa: BLE001
        return False
    try:
        _cache_invalid_file().unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        _CACHE_INVALIDATED_IN_PROCESS = True
        return False
    _CACHE_INVALIDATED_IN_PROCESS = False
    return True


def _load_cache(*, device: str, token: str) -> Optional[dict]:
    """读并解密缓存。tag 失败(篡改/错设备/错 token)-> None。返回内层 blob 或 None。"""
    if _CACHE_INVALIDATED_IN_PROCESS:
        return None
    try:
        if _cache_invalid_file().exists():
            return None
    except Exception:  # noqa: BLE001
        return None
    p = _cache_file()
    try:
        if not p.is_file():
            return None
        plain = decrypt_bytes(p.read_bytes(), _wrap_key(token, device))
        blob = json.loads(plain.decode("utf-8"))
        return blob if isinstance(blob, dict) else None
    except Exception:  # noqa: BLE001  解密失败/损坏/篡改 -> 当无缓存
        return None


def inspect_local_activation_state(*, now: Optional[int] = None) -> dict[str, Any]:
    """Return a privacy-safe local activation/cache classification for support.

    This exposes neither the credential nor the device fingerprint. A present
    cache may be classified structurally, but cannot authorize the product
    because the current client cannot independently verify its server signature.
    """
    now = int(time.time()) if now is None else int(now)
    credential = resolve_token()
    cache_path = _cache_file()
    invalid_path = _cache_invalid_file()

    def _is_file(path: Path) -> bool:
        try:
            return path.is_file()
        except Exception:  # noqa: BLE001
            return False

    report: dict[str, Any] = {
        "credential_available": bool(credential),
        "credential_file_present": any(_is_file(path) for path in _token_candidates()),
        "cache_file_present": _is_file(cache_path),
        "invalid_marker_present": bool(_CACHE_INVALIDATED_IN_PROCESS) or _is_file(invalid_path),
        "cache_state": "missing",
        "authorization_reusable": False,
        "online_refresh_required": True,
    }
    if report["cache_file_present"]:
        try:
            stat = cache_path.stat()
            report["cache_bytes"] = int(stat.st_size)
            report["cache_age_seconds"] = max(0, now - int(stat.st_mtime))
        except Exception:  # noqa: BLE001
            pass
    if report["invalid_marker_present"]:
        report["cache_state"] = "invalidated"
        return report
    if not report["cache_file_present"]:
        return report

    try:
        device = device_fingerprint()
        cached = _load_cache(device=device, token=credential)
    except Exception:  # noqa: BLE001
        report["cache_state"] = "inspection_error"
        return report
    if not isinstance(cached, dict):
        report["cache_state"] = "unreadable_for_current_identity"
        return report

    lease = cached.get("lease")
    mode = cached.get("mode")
    fields = _validated_activation_fields(
        lease=lease,
        master_key_b64=cached.get("mk"),
        mode=mode,
        device=device,
        now=now,
        trial_exp=(
            lease.get("exp")
            if isinstance(lease, dict) and mode == "trial"
            else _MISSING
        ),
    )
    if fields is None:
        exp = lease.get("exp") if isinstance(lease, dict) else None
        report["cache_state"] = (
            "expired"
            if isinstance(exp, int) and not isinstance(exp, bool) and 0 < exp <= now
            else "malformed"
        )
        return report

    report["cache_state"] = "readable_current_identity"
    report["lease_seconds_left"] = max(0, int(fields["exp"]) - now)
    return report


def _write_cache_tombstone() -> bool:
    global _CACHE_INVALIDATED_IN_PROCESS
    _CACHE_INVALIDATED_IN_PROCESS = True
    try:
        _atomic_write_bytes(_cache_invalid_file(), b"invalid\n")
        return True
    except Exception:  # noqa: BLE001
        # The in-process marker still prevents reuse for this process.
        return False


def _clear_cache() -> dict[str, bool]:
    """Remove cache truthfully; persist a tombstone if removal is not possible."""
    global _CACHE_INVALIDATED_IN_PROCESS
    try:
        _cache_file().unlink(missing_ok=True)
        cache_cleared = True
    except Exception:  # noqa: BLE001
        cache_cleared = False

    if cache_cleared:
        # Keep a pre-existing tombstone until a validated online response atomically
        # replaces the cache. It is harmless when the cache file is absent.
        try:
            cache_invalidated = _cache_invalid_file().exists()
        except Exception:  # noqa: BLE001
            cache_invalidated = True
            _CACHE_INVALIDATED_IN_PROCESS = True
        return {
            "cache_cleared": True,
            "cache_invalidated": cache_invalidated,
        }

    cache_invalidated = _write_cache_tombstone()
    return {
        "cache_cleared": False,
        "cache_invalidated": cache_invalidated,
    }


def clear_activation(
    *,
    _runtime_deny_revision: Optional[int] = None,
) -> dict:
    """换绑/清除本机激活态: 删所有已存 token(activation.txt 候选)+ 加密缓存(lease/master_key),
    使下次激活回到干净态(可粘贴新 token, 或无 token 走试用)。

    关键: 也从【本进程】env 移除测试用 BIDKING_ACTIVATION_TOKEN —— 它优先级最高(见 resolve_token),
    不清就会盖过 UI 新贴的 token, 正是"填了错 token 到 env、没清除、一直绑定失败"的根因。
    注意: 只能清【本进程】env; 父进程 / 另一进程(monitor)的 env 需重启才彻底清 -> 用 env_was_set 提示用户。

    返回逐项真实结果。缓存无法删除时即使已写 tombstone，也不能声称 cleared=True。
    """
    global _CACHE_INVALIDATED_IN_PROCESS

    # Direct callers and enforce both enter through this boundary.  enforce may
    # already have advanced request generation + identity revision + deny latch
    # atomically; only reuse that preparation while its exact revision remains
    # current.  Any intervening identity write requires a fresh deny revision.
    prepared = _activation_clear_epoch_prepared(_runtime_deny_revision)
    if not prepared:
        _runtime_deny_revision = begin_activation_identity_clear()

    def _clear_identity_sources() -> tuple[bool, bool]:
        token_cleared = _clear_persisted_tokens()
        env_was_set = _ENV_TOKEN in os.environ
        if env_was_set:
            try:
                os.environ.pop(_ENV_TOKEN, None)
            except Exception:  # noqa: BLE001
                token_cleared = False
        return token_cleared, env_was_set

    try:
        _accepted, identity_result, _revision = _run_activation_identity_write(
            _clear_identity_sources
        )
    except Exception:  # noqa: BLE001
        identity_result = (False, _ENV_TOKEN in os.environ)
    token_cleared, env_was_set = (
        identity_result if isinstance(identity_result, tuple) else (False, False)
    )
    try:
        cache_state = _clear_cache()
    except Exception:  # noqa: BLE001
        _CACHE_INVALIDATED_IN_PROCESS = True
        cache_state = {
            "cache_cleared": False,
            "cache_invalidated": True,
        }
    cache_cleared = cache_state["cache_cleared"]
    cleared = bool(token_cleared and cache_cleared)
    can_activate = _finish_activation_identity_clear(
        begin_revision=_runtime_deny_revision,
        cleared=cleared,
    )
    return {
        "cleared": cleared,
        "can_activate": can_activate,
        "token_cleared": token_cleared,
        "cache_cleared": cache_cleared,
        "cache_invalidated": cache_state["cache_invalidated"],
        "env_was_set": env_was_set,
    }


# --------------------------------------------------------------------------- #
# 地址
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _ActivationEndpointCandidate:
    endpoint_id: str
    base: str
    activation_url: str
    telemetry_url: Optional[str]


def _activate_urls(ingest_url: Optional[str]) -> list[str]:
    """Compatibility projection of the controlled activation endpoint generation.

    ``ingest_url`` is intentionally ignored: upload configuration, environment
    variables and local files may not change the distributed activation authority.
    """
    del ingest_url
    from ..live.version_check import iter_service_endpoints

    try:
        return [endpoint.base + "/activate" for endpoint in iter_service_endpoints()]
    except Exception:  # noqa: BLE001
        return []


def _activation_endpoint_candidates(
    ingest_url: Optional[str],
) -> list[_ActivationEndpointCandidate]:
    """Attach stable endpoint IDs/telemetry without exposing origins to diagnostics."""
    from ..live.version_check import iter_service_endpoints

    urls = _activate_urls(ingest_url)
    try:
        configured = {
            endpoint.base + "/activate": endpoint
            for endpoint in iter_service_endpoints()
        }
    except Exception:  # noqa: BLE001
        configured = {}
    candidates: list[_ActivationEndpointCandidate] = []
    for index, url in enumerate(urls):
        endpoint = configured.get(url)
        if endpoint is not None:
            candidates.append(
                _ActivationEndpointCandidate(
                    endpoint_id=endpoint.endpoint_id,
                    base=endpoint.base,
                    activation_url=url,
                    telemetry_url=endpoint.telemetry,
                )
            )
            continue
        # Preserve the existing monkeypatch seam for tests/plugins without letting
        # any runtime value mutate the production endpoint generation.
        base = url.rsplit("/", 1)[0]
        candidates.append(
            _ActivationEndpointCandidate(
                endpoint_id=f"endpoint_{index + 1}",
                base=base,
                activation_url=url,
                telemetry_url=base + "/telemetry",
            )
        )
    return candidates


def _activate_url(ingest_url: Optional[str]) -> Optional[str]:
    """首选激活地址(单个)。保留给只需要一个地址的调用方(如 _telemetry_url)。"""
    urls = _activate_urls(ingest_url)
    return urls[0] if urls else None


def _telemetry_url(ingest_url: Optional[str]) -> Optional[str]:
    au = _activate_url(ingest_url)
    if not au:
        return None
    base = au.rsplit("/", 1)[0]
    return base + "/telemetry"


def _telemetry_url_from_activation_url(activation_url: Optional[str]) -> Optional[str]:
    """Use the explicit telemetry policy of the origin that accepted activation."""
    if not activation_url:
        return None
    from ..live.version_check import iter_service_endpoints, validate_service_url

    checked = validate_service_url(activation_url, purpose="activate")
    try:
        for endpoint in iter_service_endpoints():
            if checked == endpoint.base + "/activate":
                return endpoint.telemetry
    except Exception:  # noqa: BLE001
        return None
    return checked.rsplit("/", 1)[0] + "/telemetry"


def _resolve_ingest_url() -> Optional[str]:
    try:
        from ..live.version_check import resolve_ingest_url
        return resolve_ingest_url()
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# 网络
# --------------------------------------------------------------------------- #
def _client_version() -> str:
    """本包版本(打包时的 BUILD_MANIFEST 优先, 否则源码常量)。取不到 -> "" -> 服务端按老客户端发旧 key。"""
    try:
        from ..live.version_check import app_version
        return str(app_version() or "")
    except Exception:  # noqa: BLE001  版本拿不到绝不能挡住激活
        return ""


class _ActivationProxiedHTTPSRequest(urllib.request.Request):
    """Exact request type for the existing urllib/system-proxy path."""


class _ActivationDirectHTTPSRequest(urllib.request.Request):
    """Exact request type for one activation-only no-proxy retry."""


class _ActivationNoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Treat every redirect as the endpoint's authoritative HTTP response."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


_ACTIVATION_REQUEST_TYPES = (
    _ActivationProxiedHTTPSRequest,
    _ActivationDirectHTTPSRequest,
)
_ACTIVATION_DIRECT_RETRY_STAGES = frozenset({"dns", "tcp", "tls"})


def _activation_direct_retry_enabled() -> bool:
    """Default ON; an exact fresh-process env switch provides rollback.

    Invalid or protocol-shaped values fail closed to disabled.  This avoids
    invoking equality or truthiness hooks at the release-control boundary.
    """

    try:
        raw = os.environ.get(_ENV_DISABLE_ACTIVATION_DIRECT_RETRY)
    except BaseException:  # noqa: BLE001 - release switch must be no-throw
        return False
    if raw is None:
        return True
    if type(raw) is not str:  # noqa: E721 - exact built-in release control
        return False
    if raw == "1":
        return False
    return raw in {"", "0"}


@contextlib.contextmanager
def _activation_direct_retry_fence(revision: int, clear_epoch: int):
    """Bind a logical activation identity to this thread's possible retry."""

    previous = getattr(_ACTIVATION_DIRECT_RETRY_FENCE_LOCAL, "value", _MISSING)
    value = (
        (revision, clear_epoch)
        if (
            type(revision) is int
            and revision >= 0
            and revision % 2 == 0
            and type(clear_epoch) is int
            and clear_epoch >= 0
        )
        else None
    )
    _ACTIVATION_DIRECT_RETRY_FENCE_LOCAL.value = value
    try:
        yield
    finally:
        if previous is _MISSING:
            try:
                del _ACTIVATION_DIRECT_RETRY_FENCE_LOCAL.value
            except AttributeError:
                pass
        else:
            _ACTIVATION_DIRECT_RETRY_FENCE_LOCAL.value = previous


def _activation_direct_retry_fence_value() -> tuple[int, int] | None:
    value = getattr(_ACTIVATION_DIRECT_RETRY_FENCE_LOCAL, "value", None)
    if (
        type(value) is tuple
        and len(value) == 2
        and type(value[0]) is int
        and value[0] >= 0
        and value[0] % 2 == 0
        and type(value[1]) is int
        and value[1] >= 0
    ):
        return value
    return None


def _activation_urlopen_verified(target: Any, *, timeout: float):
    """Open only exact activation request types with an isolated policy.

    The first request retains urllib's existing proxy discovery.  The retry
    installs ``ProxyHandler({})`` so neither environment, WinINET proxy nor a
    PAC-derived handler can select a proxy.  Both paths keep the shipped CA,
    hostname verification, user agent and a no-redirect HTTP stop boundary.
    Other client consumers continue through the shared transport unchanged.
    """

    request_type = type(target)
    if request_type not in _ACTIVATION_REQUEST_TYPES:
        return _shared_urlopen_verified(target, timeout=timeout)
    if request_type is _ActivationDirectHTTPSRequest:
        proxy_handler = urllib.request.ProxyHandler({})
    else:
        proxy_handler = urllib.request.ProxyHandler()
    if not any(
        type(name) is str and name.lower() == "user-agent"
        for name, _ in target.header_items()
    ):
        target.add_unredirected_header("User-Agent", PRODUCT_USER_AGENT)
    opener = urllib.request.build_opener(
        proxy_handler,
        urllib.request.HTTPSHandler(context=verified_ssl_context()),
        _ActivationNoRedirectHandler(),
    )
    return opener.open(target, timeout=timeout)  # noqa: S310 - strict HTTPS types


# Preserve the established test/plugin seam while limiting the new routing
# behavior to the exact private request types created by _post_activate.
urlopen_verified = _activation_urlopen_verified


@dataclass(frozen=True)
class _ActivationPostFailure:
    """Privacy-safe failure returned by the real transport boundary."""

    failure_class: str
    retry_fallback: bool = True
    stage: str = "unknown"
    prior_failure_classes: tuple[str, ...] = ()


@dataclass(frozen=True, eq=False)
class _ActivationPostResponse:
    """Internal two-field response plus a fixed, privacy-safe route fact."""

    code: int
    body: dict
    transport_availability: str = ""
    prior_failure_classes: tuple[str, ...] = ()

    def __eq__(self, other: object) -> bool:
        # Preserve the established direct `_post_activate() == (code, body)`
        # test/plugin seam without exposing route metadata to server parsing.
        if type(other) is not tuple or len(other) != 2:
            return False
        return self.code == other[0] and self.body == other[1]


_ACTIVATION_TRANSPORT_STAGES = ("dns", "tcp", "tls", "http", "unknown")
_ACTIVATION_FAILURE_STAGES = {
    "dns_lookup_failed": "dns",
    "connect_timeout": "tcp",
    "connect_refused": "tcp",
    "transport_os_error": "tcp",
    "tls_connection_reset": "tls",
    "tls_handshake_failed": "tls",
    "http_reachable_invalid_payload": "http",
}
_ACTIVATION_FAILURE_CLASSES = frozenset(
    (*_ACTIVATION_FAILURE_STAGES, "transport_unknown")
)


def _activation_transport_stage(failure_class: str) -> str:
    return _ACTIVATION_FAILURE_STAGES.get(failure_class, "unknown")


def _activation_exception_attribute(
    exc: BaseException,
    name: str,
) -> tuple[bool, Any]:
    """Read an exception attribute without letting a descriptor escape."""
    try:
        return True, getattr(exc, name, None)
    except BaseException:  # noqa: BLE001 - transport classification must not throw
        return False, None


def _activation_exception_mro(
    value: Any,
) -> tuple[type, ...]:
    """Read the actual type MRO without consulting an instance __class__ hook."""
    try:
        actual_type = type(value)
        mro = type.__getattribute__(actual_type, "__mro__")
    except BaseException:  # noqa: BLE001 - classification must remain no-throw
        return ()
    return mro if type(mro) is tuple else ()


def _activation_mro_contains(
    mro: tuple[type, ...],
    expected: type,
) -> bool:
    return any(base is expected for base in mro)


def _activation_transport_failure(exc: BaseException) -> str:
    """Map a network exception without retaining its text, URL or local path."""
    try:
        readable, reason = _activation_exception_attribute(exc, "reason")
        if not readable:
            return "transport_unknown"
        if reason is not None:
            reason_mro = _activation_exception_mro(reason)
            if not _activation_mro_contains(reason_mro, BaseException):
                return "transport_unknown"
            exc = reason
            exc_mro = reason_mro
        else:
            exc_mro = _activation_exception_mro(exc)
        if not _activation_mro_contains(exc_mro, BaseException):
            return "transport_unknown"
        readable, raw_winerror = _activation_exception_attribute(exc, "winerror")
        if not readable:
            return "transport_unknown"
        if raw_winerror is None:
            winerror = None
        elif type(raw_winerror) is int:  # noqa: E721 - subclasses are untrusted
            winerror = raw_winerror
        else:
            return "transport_unknown"
        if _activation_mro_contains(exc_mro, socket.gaierror):
            return "dns_lookup_failed"
        if _activation_mro_contains(exc_mro, ssl.SSLError):
            return "tls_handshake_failed"
        if (
            _activation_mro_contains(exc_mro, ConnectionResetError)
            or winerror == 10054
        ):
            return "tls_connection_reset"
        if _activation_mro_contains(exc_mro, TimeoutError):
            return "connect_timeout"
        if (
            _activation_mro_contains(exc_mro, ConnectionRefusedError)
            or winerror == 10061
        ):
            return "connect_refused"
        if _activation_mro_contains(exc_mro, OSError):
            return "transport_os_error"
        return "transport_unknown"
    except BaseException:  # noqa: BLE001 - classification is a privacy boundary
        return "transport_unknown"


def _post_activate(
    url: str,
    device: str,
    token: str,
    timeout: float,
    pet_usage_event: Optional[dict[str, Any]] = None,
    card_token: Optional[str] = None,
) -> Optional[tuple[int, dict] | _ActivationPostFailure]:
    """POST /activate {device, token?, client_version, client_security_epoch}。

    client_version 用于服务端【双密钥窗口】按版本发主密钥(0.2.7+ 拿轮换后的新 key, 旧包继续拿旧 key)。
    client_security_epoch 用于作者显式启用的旧客户端退役门；老服务端会忽略多余字段。"""
    payload = _activation_request_payload(
        device=device,
        token=token,
        pet_usage_event=pet_usage_event,
        card_token=card_token,
    )
    try:
        body = json.dumps(payload).encode("utf-8")
    except Exception as exc:  # noqa: BLE001 - fixed privacy-safe classification
        failure_class = _activation_transport_failure(exc)
        return _ActivationPostFailure(
            failure_class,
            stage=_activation_transport_stage(failure_class),
        )
    retry_fence = _activation_direct_retry_fence_value()
    if retry_fence is None:
        with _ACTIVATION_IDENTITY_SOURCE_LOCK:
            retry_fence = (
                _ACTIVATION_IDENTITY_SOURCE_REVISION,
                _ACTIVATION_CLEAR_EPOCH,
            )

    def _attempt(
        request_type: type[urllib.request.Request],
    ) -> tuple[int, dict] | _ActivationPostFailure:
        try:
            req = request_type(url, data=body, method="POST")
            req.add_header("Content-Type", "application/json; charset=utf-8")
            response = urlopen_verified(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            # Redirects and every other HTTP status stop both direct retry and
            # cross-endpoint fallback.  Their payload remains subject to the
            # same strict response parser below.
            if type(exc) is not urllib.error.HTTPError:
                # HTTPError subclasses are outside the trusted transport
                # protocol.  Do not touch code/read/fp/header descriptors: the
                # observed HTTP boundary is enough to stop every fallback.
                return _ActivationPostFailure(
                    "http_reachable_invalid_payload",
                    retry_fallback=False,
                    stage="http",
                )
            try:
                code = object.__getattribute__(exc, "code")
            except BaseException:  # noqa: BLE001 - privacy/protocol boundary
                return _ActivationPostFailure(
                    "http_reachable_invalid_payload",
                    retry_fallback=False,
                    stage="http",
                )
            if type(code) is not int:
                return _ActivationPostFailure(
                    "http_reachable_invalid_payload",
                    retry_fallback=False,
                    stage="http",
                )
            try:
                raw = exc.read()
                data = (
                    json.loads(raw.decode("utf-8"))
                    if type(raw) is bytes
                    else {}
                )
            except BaseException:  # noqa: BLE001 - privacy/protocol boundary
                data = {}
            return code, (data if type(data) is dict else {})
        except Exception as exc:  # noqa: BLE001 - no HTTP response observed
            failure_class = _activation_transport_failure(exc)
            return _ActivationPostFailure(
                failure_class,
                stage=_activation_transport_stage(failure_class),
            )

        # urlopen returning is the HTTP-response boundary.  Any later status,
        # context-manager or body failure must not cause a duplicate request.
        try:
            with response as opened:
                code = opened.getcode()
                raw = opened.read()
        except Exception:  # noqa: BLE001 - fixed, privacy-safe HTTP failure
            return _ActivationPostFailure(
                "http_reachable_invalid_payload",
                retry_fallback=False,
                stage="http",
            )
        if type(code) is not int or type(raw) is not bytes:
            return _ActivationPostFailure(
                "http_reachable_invalid_payload",
                retry_fallback=False,
                stage="http",
            )
        try:
            data = json.loads(raw.decode("utf-8"))
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
            # HTTP 已可达，畸形载荷不能换线路重试，更不能误报 token。
            return _ActivationPostFailure(
                "http_reachable_invalid_payload",
                retry_fallback=False,
                stage="http",
            )
        return code, (data if type(data) is dict else {})

    first = _attempt(_ActivationProxiedHTTPSRequest)
    if not (
        type(first) is _ActivationPostFailure
        and first.retry_fallback is True
        and first.stage in _ACTIVATION_DIRECT_RETRY_STAGES
        and _activation_direct_retry_enabled()
    ):
        return first
    # Start the second request only while the logical identity still matches
    # the first request.  Thread start is the irreversible boundary shared
    # with clear/rebind writers; the network wait happens outside both locks.
    direct_outcome: list[Any] = []
    direct_errors: list[BaseException] = []

    def _direct_transport() -> None:
        try:
            direct_outcome.append(_attempt(_ActivationDirectHTTPSRequest))
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            direct_errors.append(exc)

    with (
        _ACTIVATION_TRANSPORT_START_LOCK,
        _ACTIVATION_IDENTITY_SOURCE_LOCK,
    ):
        identity_unchanged = bool(
            type(retry_fence) is tuple
            and len(retry_fence) == 2
            and _ACTIVATION_IDENTITY_SOURCE_REVISION == retry_fence[0]
            and _ACTIVATION_CLEAR_EPOCH == retry_fence[1]
        )
        if not identity_unchanged:
            return _ActivationPostFailure(
                "transport_unknown",
                retry_fallback=False,
                stage="unknown",
                prior_failure_classes=(first.failure_class,),
            )
        worker = threading.Thread(
            target=_direct_transport,
            name="bidking-activation-direct-retry",
            daemon=True,
        )
        worker.start()
    worker.join()
    if direct_errors:
        raise direct_errors[0]
    direct = (
        direct_outcome[0]
        if direct_outcome
        else _ActivationPostFailure(
            "transport_unknown",
            retry_fallback=False,
            stage="unknown",
        )
    )
    if type(direct) is not _ActivationPostFailure:
        if (
            type(direct) is tuple
            and len(direct) == 2
            and first.failure_class == "tls_handshake_failed"
        ):
            return _ActivationPostResponse(
                direct[0],
                direct[1],
                transport_availability=(
                    "system_proxy_tls_path_incompatible"
                ),
                prior_failure_classes=(first.failure_class,),
            )
        return direct
    # Preserve the fixed proxy-path class for support without retaining any
    # exception, proxy or endpoint detail.  Only a classified direct network
    # failure may advance to the next controlled endpoint.
    return _ActivationPostFailure(
        direct.failure_class,
        retry_fallback=bool(
            direct.retry_fallback is True
            and direct.stage in _ACTIVATION_DIRECT_RETRY_STAGES
        ),
        stage=direct.stage,
        prior_failure_classes=(first.failure_class,),
    )


def _activation_request_payload(
    *,
    device: str,
    token: str,
    pet_usage_event: Optional[dict[str, Any]] = None,
    card_token: Optional[str] = None,
) -> dict[str, Any]:
    """Build the one activation body shared by HTTPS and encrypted HTTP."""
    payload: dict[str, Any] = {
        "device": device,
        "client_security_epoch": CLIENT_SECURITY_EPOCH,
        "upload_lease_schema": _UPLOAD_LEASE_SCHEMA_VERSION,
    }
    if token:
        payload["token"] = token
    if pet_usage_event is not None:
        payload["pet_usage"] = pet_usage_event
    if card_token is not None:
        payload["card_token"] = card_token
        payload["card_redemption_schema"] = 1
    cv = _client_version()
    if cv:
        payload["client_version"] = cv
    return payload


def _load_activation_http_e2ee_config(
) -> activation_http_e2ee.ActivationHttpE2EEConfig | None:
    """Load the version-controlled emergency channel; runtime may only disable it."""
    if os.environ.get("BIDKING_DISABLE_ACTIVATION_HTTP_E2EE") == "1":
        return None
    try:
        config = activation_http_e2ee.load_config()
    except Exception:  # package/config mismatch disables the emergency channel
        return None
    return config if config is not None and config.enabled is True else None


class _ActivationHttpE2EENoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep the emergency request on its pinned, version-controlled IP path."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


def _post_activate_http_e2ee(
    config: activation_http_e2ee.ActivationHttpE2EEConfig,
    device: str,
    token: str,
    timeout: float,
    pet_usage_event: Optional[dict[str, Any]] = None,
    card_token: Optional[str] = None,
) -> Optional[tuple[int, dict] | _ActivationPostFailure]:
    """Use plain HTTP only as a carrier for one pinned, authenticated envelope."""
    try:
        payload = _activation_request_payload(
            device=device,
            token=token,
            pet_usage_event=pet_usage_event,
            card_token=card_token,
        )
        request_body, context = activation_http_e2ee.build_client_request(
            config,
            payload,
        )
        req = urllib.request.Request(config.url, data=request_body, method="POST")
        req.add_header("Content-Type", "application/vnd.bidking.activation-e2ee+json")
        # This is the only public-HTTP callsite.  URL/key identity came from the
        # strict version-controlled config, and no credential exists outside AEAD.
        opener = urllib.request.build_opener(_ActivationHttpE2EENoRedirectHandler())
        with opener.open(req, timeout=timeout) as response:  # noqa: S310
            outer_code = response.getcode()
            raw = response.read(activation_http_e2ee.MAX_ENVELOPE_BYTES + 1)
        if outer_code != 200 or len(raw) > activation_http_e2ee.MAX_ENVELOPE_BYTES:
            raise activation_http_e2ee.ActivationHttpE2EEError(
                "invalid outer response"
            )
        code, data = activation_http_e2ee.open_client_response(raw, context)
        return code, data
    except urllib.error.HTTPError:
        return _ActivationPostFailure(
            "http_reachable_invalid_payload",
            retry_fallback=False,
            stage="http",
        )
    except activation_http_e2ee.ActivationHttpE2EEError:
        return _ActivationPostFailure(
            "http_reachable_invalid_payload",
            retry_fallback=False,
            stage="http",
        )
    except Exception as exc:  # noqa: BLE001 - fixed transport classes only
        failure_class = _activation_transport_failure(exc)
        # A reset on this route happens before any TLS layer; retain the existing
        # privacy-safe TCP class instead of mislabelling it as a TLS failure.
        if failure_class == "tls_connection_reset":
            failure_class = "transport_os_error"
        return _ActivationPostFailure(
            failure_class,
            stage=_activation_transport_stage(failure_class),
        )


# --------------------------------------------------------------------------- #
# Provider(装进 activation 接缝 -> 上层 is_activated()/data_key() 走它)
# --------------------------------------------------------------------------- #
class LeaseProvider:
    """客户端激活态 provider: is_activated = 有 master_key 且 lease 未过期; data_key = 出厂主密钥。
    与 RustProvider 区别: 客户端无签名密钥, 不能本地验 token, '已激活'真值 = 服务端发的 lease(缓存)。"""

    def __init__(self, *, master_key: Optional[bytes], exp: int, device: str,
                 telemetry_url: Optional[str] = None) -> None:
        self._mk = master_key
        self._exp = int(exp)
        self._device = device
        self._tele = telemetry_url

    def is_activated(self) -> bool:
        if activation_runtime_denied():
            return False
        return self._mk is not None and int(time.time()) < self._exp

    def data_key(self) -> bytes:
        if activation_runtime_denied() or self._mk is None:
            raise DataKeyUnavailable("未激活(无主密钥)")
        return self._mk

    def integrity_digest(self) -> Optional[str]:
        if activation_runtime_denied():
            return None
        return os.environ.get("BIDKING_INTEGRITY_DIGEST")

    def report_suspicious(self, verdict: Any) -> None:
        """C: best-effort 上报可疑(反调试命中)。绝不抛、绝不阻塞。"""
        if activation_runtime_denied() or not self._tele:
            return
        try:
            summary = verdict.summary() if hasattr(verdict, "summary") else str(verdict)
            body = json.dumps({"device": self._device, "verdict": str(summary)[:300]}).encode("utf-8")
            req = urllib.request.Request(self._tele, data=body, method="POST")
            req.add_header("Content-Type", "application/json; charset=utf-8")
            urlopen_verified(req, timeout=3).read()
        except Exception:  # noqa: BLE001
            pass


def _install_provider(master_key: Optional[bytes], exp: int, device: str,
                      ingest_url: Optional[str],
                      activation_url: Optional[str] = None,
                      telemetry_url: Any = _MISSING) -> bool:
    try:
        from . import activation
        selected_telemetry = telemetry_url
        if selected_telemetry is _MISSING:
            selected_telemetry = (
                _telemetry_url_from_activation_url(activation_url)
                if activation_url
                else _telemetry_url(ingest_url)
            )
        activation.set_provider(
            LeaseProvider(master_key=master_key, exp=exp, device=device,
                          telemetry_url=selected_telemetry)
        )
        return True
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# 结果
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ActivationTransportSummary:
    """Privacy-safe activation path summary.

    It deliberately stores only fixed stages and counts.  Endpoint URLs,
    resolved addresses, exception text, credentials and device identity never
    cross this boundary.
    """

    attempt_count: int = 0
    endpoint_count: int = 0
    stage_counts: tuple[tuple[str, int], ...] = (
        ("dns", 0),
        ("tcp", 0),
        ("tls", 0),
        ("http", 0),
        ("unknown", 0),
    )
    all_endpoints_failed: bool = False
    # Fixed client-side route fact only; never contains endpoint/proxy/error data.
    availability: str = ""


def _activation_transport_summary(
    stages: list[str],
    *,
    endpoint_count: int,
    availability: str = "",
) -> ActivationTransportSummary:
    counts = {stage: 0 for stage in _ACTIVATION_TRANSPORT_STAGES}
    for raw_stage in stages:
        stage = (
            raw_stage
            if isinstance(raw_stage, str)
            and raw_stage in _ACTIVATION_TRANSPORT_STAGES
            else "unknown"
        )
        counts[stage] += 1
    safe_endpoint_count = (
        endpoint_count
        if type(endpoint_count) is int and endpoint_count >= 0
        else 0
    )
    attempt_count = len(stages)
    return ActivationTransportSummary(
        attempt_count=attempt_count,
        endpoint_count=safe_endpoint_count,
        stage_counts=tuple(
            (stage, counts[stage]) for stage in _ACTIVATION_TRANSPORT_STAGES
        ),
        all_endpoints_failed=bool(
            safe_endpoint_count > 0
            and attempt_count == safe_endpoint_count
            and counts["http"] == 0
        ),
        availability=(
            availability
            if type(availability) is str
            and availability == "system_proxy_tls_path_incompatible"
            else ""
        ),
    )


@dataclass
class ActivationResult:
    activated: bool
    mode: str                       # paid | trial | none
    reason: str = "ok"              # ok | ... | state_error | rate_limited | offline | no_server
    source: str = "server"          # 当前运行时授权只接受 server；cache 值仅保留历史兼容
    trial_exp: Optional[int] = None  # 试用到期 ts(mode=trial)
    master_key: Optional[bytes] = field(default=None, repr=False)
    lease_exp: Optional[int] = None
    paid_exp: Optional[int] = None   # 订阅日历到期 ts；历史无期限授权兼容值为 None
    plan_kind: Optional[str] = None
    feature_entitlements: dict[str, str] = field(default_factory=dict)
    # Current online response only.  This is a strict normalized Pet view, not
    # a client-verifiable signed cache and never a source for local inference.
    feature_usage: Optional[dict[str, dict[str, Any]]] = None
    # 固定分类，不保存 URL、异常正文、DNS 答案、设备信息或请求内容。
    transport_failure_classes: tuple[str, ...] = ()
    transport_summary: ActivationTransportSummary = field(
        default_factory=ActivationTransportSummary
    )
    local_commit_stage: str = ""
    local_commit_recovery: str = ""
    # Current-request display provenance only; never persisted or authorizing.
    _authoritative_rejection_context: Any = field(default=None, repr=False)
    # Last online verification only; display metadata never grants authority.
    roaming_rebinds: Optional[dict[str, int]] = None
    roaming_rebind_limit_reached: bool = False
    membership: Optional[dict[str, Any]] = field(default=None, repr=False)
    membership_card_capability: Optional[dict[str, int]] = None
    card_redemption: Optional[dict[str, Any]] = None
    feature_usage_schema_version: int = 1

    def trial_seconds_left(self, now: Optional[int] = None) -> Optional[int]:
        if self.mode != "trial" or self.trial_exp is None:
            return None
        now = int(time.time()) if now is None else int(now)
        return max(0, int(self.trial_exp) - now)

    def paid_seconds_left(self, now: Optional[int] = None) -> Optional[int]:
        """订阅剩余秒(日历); 历史无期限授权/非付费返回 None。"""
        if self.mode != "paid" or self.paid_exp is None:
            return None
        now = int(time.time()) if now is None else int(now)
        return max(0, int(self.paid_exp) - now)

    def status_line(self, now: Optional[int] = None) -> str:
        """一行人类可读(给启动日志/UI)。"""
        if self.activated and self.mode == "paid":
            return "激活: 已激活"
        if self.activated and self.mode == "trial":
            left = self.trial_seconds_left(now) or 0
            h, rem = divmod(left, 3600)
            m = rem // 60
            return f"激活: 试用中 · 剩余 {h} 小时 {m} 分 · {CONTACT}"
        if self.reason == "trial_expired":
            return f"激活: 试用已结束 · {CONTACT}"
        if self.reason == "upgrade_required":
            return "激活: 当前版本已停用，请下载最新版后重试"
        if self.reason == "local_clear_incomplete":
            return "激活: 本机清除未完成，请关闭软件并重启；仍异常请导出诊断联系支持"
        if self.reason in _LOCAL_ACTIVATION_MESSAGES:
            return "激活: " + _LOCAL_ACTIVATION_MESSAGES[self.reason]
        if self.reason == "state_error":
            return "激活: 服务器激活服务暂时异常，请稍后重试"
        if self.reason == "rate_limited":
            return "激活: 请求过于频繁，请稍后重试"
        if self.reason == "server_error":
            return "激活: 服务器暂时异常，请稍后重试"
        if self.reason == "server_rejected":
            return f"激活: 服务器拒绝了本次请求 · {CONTACT}"
        if self.reason == "device_mismatch" and _activation_roaming_display(self)[1]:
            return "激活: " + _ROAMING_REBIND_LIMIT_MESSAGE
        if self.reason in ("revoked", "device_revoked", "device_mismatch"):
            return f"激活: 失败({self.reason}) · {CONTACT}"
        if self.reason in ("offline", "no_server"):
            if self.transport_summary.all_endpoints_failed:
                if self.mode == "paid":
                    return (
                        "激活: 已有凭据，但所有激活线路在线复核失败"
                        "(无需更换 token)"
                    )
                return "激活: 冷启动所有激活线路均未连接(无需更换 token)"
            if self.transport_failure_classes:
                return "激活: 当前网络出口未能连接激活服务(无需更换 token)"
            return "激活: 未连上激活服务(降级运行)"
        return f"激活: 未激活({self.reason}) · {CONTACT}"


_ROAMING_REBINDS_KEYS = frozenset({"schema_version", "policy_epoch", "limit", "used", "remaining"})
_ROAMING_REBIND_LIMIT_MESSAGE = "换绑次数已用完，请在当前绑定的电脑使用"


def _project_roaming_rebinds(value: Any) -> Optional[dict[str, int]]:
    """Copy an optional display counter without invoking conversion/key protocols."""
    if type(value) is not dict or len(value) != 5:
        return None
    if any(type(key) is not str for key in value) or frozenset(value) != _ROAMING_REBINDS_KEYS:
        return None
    if any(type(item) is not int for item in value.values()):
        return None
    if (
        value["schema_version"] != 1 or value["policy_epoch"] != 1 or value["limit"] != 8
        or not 0 <= value["used"] <= 8 or not 0 <= value["remaining"] <= 8
        or value["used"] + value["remaining"] != 8
    ):
        return None
    return dict(value)


def _activation_roaming_display(result: Any) -> tuple[Optional[dict[str, int]], bool]:
    """Project only exact online paid result metadata; never authorize from it."""
    if type(result) is not ActivationResult:
        return None, False
    fields = object.__getattribute__(result, "__dict__")
    if type(fields) is not dict or any(type(key) is not str for key in fields):
        return None, False
    if (
        type(fields.get("source")) is not str or fields["source"] != "server"
        or type(fields.get("mode")) is not str or fields["mode"] != "paid"
    ):
        return None, False
    reached = (
        fields.get("activated") is False
        and type(fields.get("reason")) is str and fields["reason"] == "device_mismatch"
        and fields.get("roaming_rebind_limit_reached") is True
    )
    if fields.get("activated") is not True and not reached:
        return None, False
    return _project_roaming_rebinds(fields.get("roaming_rebinds")), reached


@dataclass(frozen=True)
class _ActivationCommitPlan:
    """Validated side effects held until enforce accepts the request identity."""

    kind: str
    lease: Optional[dict[str, Any]] = field(default=None, repr=False)
    master_key: Optional[bytes] = field(default=None, repr=False)
    device: str = field(default="", repr=False)
    token: str = field(default="", repr=False)
    mode: str = "none"
    now: int = 0
    exp: int = 0
    ingest_url: Optional[str] = field(default=None, repr=False)
    activation_url: Optional[str] = field(default=None, repr=False)
    telemetry_url: Any = field(default=_MISSING, repr=False)
    rotating_ui_token: bool = False
    upload_lease: Optional[tuple[str, int]] = field(default=None, repr=False)


@dataclass(frozen=True)
class ActivationCandidate:
    """Side-effect-free network/parse result awaiting the final authority gate."""

    result: ActivationResult
    identity_snapshot: Optional[ActivationIdentitySnapshot] = field(
        default=None,
        repr=False,
    )
    ui_token: Optional[str] = field(default=None, repr=False)
    commit_plan: Optional[_ActivationCommitPlan] = field(default=None, repr=False)
    # Diagnostic provenance only; authorization still uses the existing gates.
    _validated_grant: bool = field(default=False, repr=False)
    _authoritative_rejection: bool = field(default=False, repr=False)


@dataclass(frozen=True)
class ActivationCommitOutcome:
    """Result of applying a candidate under the identity commit guard."""

    result: ActivationResult
    identity_snapshot: Optional[ActivationIdentitySnapshot] = field(
        default=None,
        repr=False,
    )
    committed: bool = False


def _emergency_fail_closed_provider(device: str) -> bool:
    """Install a non-authorizing provider without trusting the public setter."""
    try:
        from . import activation

        provider = LeaseProvider(
            master_key=None,
            exp=0,
            device=device,
            telemetry_url=None,
        )
        activation._provider = provider
        return activation._provider is provider and not provider.is_activated()
    except Exception:  # noqa: BLE001
        return False


def _force_fail_closed_provider(device: str) -> bool:
    """Replace any partial/old provider with a non-authorizing provider."""
    try:
        from . import activation

        provider = LeaseProvider(
            master_key=None,
            exp=0,
            device=device,
            telemetry_url=None,
        )
        activation.set_provider(provider)
        return activation._provider is provider and not provider.is_activated()
    except Exception:  # noqa: BLE001
        # A custom/injected setter may mutate and then raise.  This direct
        # fallback still leaves the process with a non-authorizing provider.
        return _emergency_fail_closed_provider(device)


_LOCAL_ACTIVATION_MESSAGES = {
    "local_commit_failed": "验证通过，本机激活未完成；请重启重试，仍异常请导出诊断",
    "local_identity_changed": "本机激活状态已变化，请重试；仍异常请重启并导出诊断",
    "local_activation_state_error": "本机激活状态异常，请重启后重试；仍异常请导出诊断",
}
_LOCAL_COMMIT_STAGES = frozenset({
    "", "snapshot", "token", "cache", "provider", "shadow_cleanup", "identity",
    "clear_epoch", "deny_marker", "upload_lease", "unexpected",
})
_LOCAL_COMMIT_RECOVERIES = frozenset({"", "not_started", "complete", "incomplete", "unknown"})


def _candidate_commit_failure(
    candidate: ActivationCandidate,
    *,
    stage: str = "unexpected",
    recovery: str = "unknown",
    reason: Optional[str] = None,
) -> ActivationResult:
    plan = candidate.commit_plan
    validated_grant = (
        candidate._validated_grant is True
        and plan is not None and plan.kind == "grant"
        and plan.lease is not None and plan.master_key is not None
        and candidate.result.activated is True and candidate.result.source == "server"
    )
    return ActivationResult(
        activated=False,
        mode="none",
        reason=reason or ("local_commit_failed" if validated_grant else "local_activation_state_error"),
        source="client",
        local_commit_stage=stage,
        local_commit_recovery=recovery,
        transport_failure_classes=candidate.result.transport_failure_classes,
        transport_summary=candidate.result.transport_summary,
    )


def _candidate_state_error(candidate: ActivationCandidate) -> ActivationResult:
    """Preserve server denial; classify an uncertain local boundary separately."""
    if candidate.commit_plan is not None and candidate.commit_plan.kind == "authoritative_clear":
        return candidate.result
    return _candidate_commit_failure(candidate)


def _recover_failed_candidate_commit(
    persistence_snapshot: dict[str, Any],
    *,
    device: str,
) -> tuple[bool, bool]:
    """Best-effort rollback whose recovery steps cannot suppress one another.

    The first boolean means the pre-commit persistent state was fully restored.
    The second means the current process is definitely fail-closed.  A failed
    persistence restore invalidates cache reuse in-process before any cleanup
    attempt, so a second cleanup failure cannot re-authorize the partial write.
    """
    global _CACHE_INVALIDATED_IN_PROCESS

    persistence_restored = False
    try:
        persistence_restored = bool(
            _restore_activation_persistence(persistence_snapshot)
        )
    except Exception:  # noqa: BLE001
        persistence_restored = False

    cache_fail_closed = persistence_restored
    if not persistence_restored:
        _CACHE_INVALIDATED_IN_PROCESS = True
        try:
            cache_state = _clear_cache()
            cache_fail_closed = bool(
                cache_state.get("cache_cleared")
                or cache_state.get("cache_invalidated")
                or _CACHE_INVALIDATED_IN_PROCESS
            )
        except Exception:  # noqa: BLE001
            # The in-process marker above is authoritative for this process
            # even when filesystem cleanup itself cannot be confirmed.
            cache_fail_closed = bool(_CACHE_INVALIDATED_IN_PROCESS)

    provider_step_succeeded = False
    try:
        provider_step_succeeded = bool(_force_fail_closed_provider(device))
    except Exception:  # noqa: BLE001
        provider_step_succeeded = False

    provider_fail_closed = provider_step_succeeded
    if not provider_fail_closed:
        try:
            provider_fail_closed = bool(_emergency_fail_closed_provider(device))
        except Exception:  # noqa: BLE001
            provider_fail_closed = False

    recovery_complete = persistence_restored and provider_step_succeeded
    runtime_fail_closed = cache_fail_closed and provider_fail_closed
    return recovery_complete, runtime_fail_closed


def force_activation_runtime_fail_closed(device: str = "") -> bool:
    """No-throw final boundary for enforce after an unexpected commit error."""
    global _CACHE_INVALIDATED_IN_PROCESS

    # This process-local latch is deliberately independent from provider
    # replacement.  A custom setter may mutate to a grant and then raise, while
    # every later restore/deny setter also fails.  LeaseProvider authorization
    # entry points consume this Event directly, so those failures cannot expose
    # the partial key.
    _RUNTIME_ACTIVATION_DENIED.set()
    _CACHE_INVALIDATED_IN_PROCESS = True
    try:
        _clear_cache()
    except Exception:  # noqa: BLE001
        pass
    try:
        return bool(_emergency_fail_closed_provider(device))
    except Exception:  # noqa: BLE001
        return False


def _same_source_identity(
    left: Optional[ActivationIdentitySnapshot],
    right: Optional[ActivationIdentitySnapshot],
) -> bool:
    return (
        left is not None
        and right is not None
        and left.token == right.token
        and left.device == right.device
        and left.revision == right.revision
        and left.clear_epoch == right.clear_epoch
    )


def _commit_activation_candidate_guarded(
    candidate: ActivationCandidate,
) -> ActivationCommitOutcome:
    """Apply a validated candidate once, only for its still-current identity.

    Network I/O and response parsing are already complete.  Token/cache/provider
    changes share the versioned identity guard, and persistence/provider state is
    restored together if any commit step fails.
    """
    if not isinstance(candidate, ActivationCandidate):
        raise TypeError("invalid activation candidate")
    plan = candidate.commit_plan
    if plan is None:
        return ActivationCommitOutcome(
            result=(
                _candidate_commit_failure(candidate, recovery="not_started")
                if candidate.result.activated else candidate.result
            ),
            identity_snapshot=candidate.identity_snapshot,
            committed=not candidate.result.activated,
        )

    with (
        _ACTIVATION_TRANSPORT_START_LOCK,
        _ACTIVATION_IDENTITY_SOURCE_LOCK,
    ):
        current_identity = activation_identity_snapshot(candidate.ui_token)
        if not _same_source_identity(candidate.identity_snapshot, current_identity):
            return ActivationCommitOutcome(
                result=_candidate_commit_failure(
                    candidate,
                    stage="identity", recovery="not_started", reason="local_identity_changed",
                ),
                identity_snapshot=current_identity,
                committed=False,
            )

        if (
            plan.kind == "grant"
            and not _activation_clear_epoch_allows_grant(current_identity)
        ):
            return ActivationCommitOutcome(
                result=ActivationResult(
                    activated=False, mode="none", source="client", reason="local_clear_incomplete",
                    local_commit_stage="clear_epoch", local_commit_recovery="not_started",
                ),
                identity_snapshot=current_identity,
                committed=False,
            )

        # Once a current authoritative candidate starts mutating process or
        # persistence state, runtime access is denied until the *whole* grant
        # commit and its final identity check succeed.  Stale candidates return
        # above and therefore cannot deny or re-enable a newer grant.
        _RUNTIME_ACTIVATION_DENIED.set()

        if plan.kind == "authoritative_clear":
            deny_persisted = (
                candidate.result.reason not in _PERSISTENT_AUTHORITATIVE_DENY_REASONS
                or _persist_authoritative_deny(candidate.result.reason)
            )
            cache_state = _clear_cache()
            upload_revoked = _revoke_cached_upload_authority()
            provider_revoked = _force_fail_closed_provider(
                current_identity.device
            )
            committed = (
                deny_persisted
                and bool(
                    cache_state.get("cache_cleared")
                    or cache_state.get("cache_invalidated")
                )
                and upload_revoked
                and provider_revoked
            )
            return ActivationCommitOutcome(
                result=candidate.result,
                identity_snapshot=current_identity,
                committed=committed,
            )

        if plan.kind != "grant" or plan.lease is None or plan.master_key is None:
            return ActivationCommitOutcome(
                result=_candidate_commit_failure(
                    candidate,
                    recovery="not_started", reason="local_activation_state_error",
                ),
                identity_snapshot=current_identity,
                committed=False,
            )

        try:
            persistence_snapshot = _snapshot_activation_persistence()
        except Exception:  # noqa: BLE001
            force_activation_runtime_fail_closed(current_identity.device)
            return ActivationCommitOutcome(
                result=_candidate_commit_failure(candidate, stage="snapshot", recovery="not_started"),
                identity_snapshot=current_identity, committed=False,
            )
        if persistence_snapshot is None:
            return ActivationCommitOutcome(
                result=_candidate_commit_failure(
                    candidate,
                    stage="snapshot", recovery="not_started",
                ),
                identity_snapshot=current_identity,
                committed=False,
            )

        committed = False
        stage = "token"
        try:
            if plan.rotating_ui_token and not _commit_ui_token(
                plan.token,
                expected_revision=current_identity.revision,
            ):
                raise RuntimeError("token commit failed")
            stage = "cache"
            if not _save_cache(
                lease=plan.lease,
                master_key=plan.master_key,
                device=plan.device,
                token=plan.token,
                mode=plan.mode,
                now=plan.now,
            ):
                raise RuntimeError("cache commit failed")
            stage = "provider"
            if not _install_provider(
                plan.master_key,
                plan.exp,
                plan.device,
                plan.ingest_url,
                activation_url=plan.activation_url,
                telemetry_url=plan.telemetry_url,
            ):
                raise RuntimeError("provider commit failed")
            if plan.rotating_ui_token:
                stage = "shadow_cleanup"
                _cleanup_shadow_tokens()
            stage = "identity"
            final_identity = activation_identity_snapshot(candidate.ui_token)
            expected_revision = (
                current_identity.revision + 2
                if plan.rotating_ui_token
                else current_identity.revision
            )
            committed = bool(
                final_identity is not None
                and final_identity.token == current_identity.token
                and final_identity.device == current_identity.device
                and final_identity.revision == expected_revision
            )
            if not committed:
                raise RuntimeError("identity changed during commit")
            stage = "clear_epoch"
            if not _complete_activation_clear_epoch(final_identity):
                raise RuntimeError("activation clear epoch changed during commit")
            stage = "deny_marker"
            if not _clear_persistent_authoritative_deny():
                raise RuntimeError("authoritative deny marker clear failed")
            stage = "upload_lease"
            _install_process_upload_lease(plan.upload_lease, final_identity)
            # Event.clear() has no provider/filesystem dependency.  This is the
            # only re-enable edge: a complete current-identity grant commit.
            _RUNTIME_ACTIVATION_DENIED.clear()
            return ActivationCommitOutcome(
                result=candidate.result,
                identity_snapshot=final_identity,
                committed=True,
            )
        except Exception:  # noqa: BLE001
            recovery_complete, runtime_fail_closed = (
                _recover_failed_candidate_commit(
                    persistence_snapshot,
                    device=current_identity.device,
                )
            )
            return ActivationCommitOutcome(
                result=_candidate_commit_failure(
                    candidate, stage=stage,
                    recovery="complete" if recovery_complete and runtime_fail_closed else "incomplete",
                    reason="local_identity_changed" if stage in {"identity", "clear_epoch"} else None,
                ),
                identity_snapshot=activation_identity_snapshot(candidate.ui_token),
                committed=False,
            )


def commit_activation_candidate(
    candidate: ActivationCandidate,
) -> ActivationCommitOutcome:
    """Commit a candidate without allowing recovery errors to escape."""
    if not isinstance(candidate, ActivationCandidate):
        raise TypeError("invalid activation candidate")
    try:
        return _commit_activation_candidate_guarded(candidate)
    except Exception:  # noqa: BLE001
        plan = candidate.commit_plan
        device = plan.device if plan is not None else ""
        force_activation_runtime_fail_closed(device)
        try:
            identity_snapshot = activation_identity_snapshot(candidate.ui_token)
        except Exception:  # noqa: BLE001
            identity_snapshot = None
        return ActivationCommitOutcome(
            result=_candidate_state_error(candidate),
            identity_snapshot=identity_snapshot,
            committed=False,
        )


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def _strict_epoch(value: Any) -> int:
    """Security timestamp: exact positive int in a representable civil-time range."""
    if type(value) is not int or value <= 0 or value > _MAX_SAFE_EPOCH:
        raise ValueError("invalid activation timestamp")
    return value


_MEMBERSHIP_KEYS = frozenset({"schema_version", "revision", "plan_exp", "my_records_exp",
                             "private_dashboard_exp", "credential_uid", "canonical_uid"})
_CARD_ACTION_STATUSES = frozenset({"switch_required_permissions", "switch_required_used_card",
    "switch_required_permanent", "switch_required_device_activation", "unsupported_current_membership",
    "unsupported_expired_account", "unsupported_plan", "unsupported_card_identity", "unsupported_card_usage",
    "card_unavailable", "invalid_new_card", "new_card_revoked", "renewal_not_configured"})


def _credential_uid(token: Any) -> Optional[str]:
    # Syntax/identity only, not local signature verification or authorization.
    if not is_single_activation_token_input(token):
        return None
    return token.rsplit(".", 1)[0].split("|")[1]


def _validated_membership(lease: dict, *, mode: str, paid_exp: Optional[int],
                          credential_uid: Any = _MISSING) -> Optional[dict]:
    raw = lease.get("membership", _MISSING)
    if raw is _MISSING:
        return None
    if (not _exact_dict_has_keys(raw, _MEMBERSHIP_KEYS) or type(mode) is not str or mode != "paid"
        or type(paid_exp) is not int or raw["schema_version"] != 1 or type(raw["schema_version"]) is not int
        or type(raw["revision"]) is not int or not 1 <= raw["revision"] <= _PET_MAX_SEQUENCE):
        raise ValueError("invalid membership contract")
    for name in ("credential_uid", "canonical_uid"):
        if type(raw[name]) is not str or not raw[name] or len(raw[name]) > 256:
            raise ValueError("invalid membership identity")
    if type(lease.get("uid")) is not str or raw["canonical_uid"] != lease["uid"]:
        raise ValueError("invalid canonical membership")
    if credential_uid is not _MISSING and (type(credential_uid) is not str or raw["credential_uid"] != credential_uid):
        raise ValueError("invalid credential membership")
    for name in ("plan_exp", "my_records_exp", "private_dashboard_exp"):
        if raw[name] is None and name != "plan_exp":
            continue
        if _strict_epoch(raw[name]) > paid_exp:
            raise ValueError("invalid membership feature deadline")
    return dict(raw)


def _membership_capability(raw: Any) -> Optional[dict[str, int]]:
    if (not _exact_dict_has_keys(raw, frozenset({"schema_version", "pet_quota_schema_version"}))
        or type(raw["schema_version"]) is not int or raw["schema_version"] != 1
        or type(raw["pet_quota_schema_version"]) is not int or raw["pet_quota_schema_version"] != 2):
        return None
    return dict(raw)


def _validated_card_ack(body: dict, fields: dict) -> Optional[dict]:
    raw = body.get("card_redemption")
    keys = frozenset({"schema_version", "status", "receipt_id", "credited_seconds", "receipt_revision",
                      "membership_revision", "membership_exp", "pet_quota_added_seconds", "roaming_rebinds_added", "committed"})
    member = fields.get("membership")
    if (_membership_capability(body.get("membership_card_capability")) is None or type(member) is not dict
        or fields["mode"] != "paid" or not _exact_dict_has_keys(raw, keys)
        or type(raw["schema_version"]) is not int or raw["schema_version"] != 1
        or type(raw["status"]) is not str or raw["status"] not in {"redeemed", "replayed"}
        or raw["committed"] is not True):
        return None
    for key in ("credited_seconds", "receipt_revision", "membership_revision", "membership_exp",
                "pet_quota_added_seconds", "roaming_rebinds_added"):
        if type(raw[key]) is not int:
            return None
    receipt_id = raw["receipt_id"]
    if (type(receipt_id) is not str or len(receipt_id) != 64 or any(c not in "0123456789abcdef" for c in receipt_id)
        or raw["credited_seconds"] not in {30 * 86400, 90 * 86400, 365 * 86400}
        or not 2 <= raw["receipt_revision"] <= raw["membership_revision"] <= _PET_MAX_SEQUENCE
        or raw["membership_revision"] != member["revision"] or raw["membership_exp"] != fields["paid_exp"]
        or raw["pet_quota_added_seconds"] != (43200 if raw["credited_seconds"] == 30 * 86400 else 0)
        or raw["roaming_rebinds_added"] != 0):
        return None
    return dict(raw)


def _validated_card_action(code: Any, body: dict) -> Optional[dict]:
    raw = body.get("card_redemption")
    if (body.get("ok") is not False or type(body.get("mode")) is not str or body.get("mode") != "paid"
        or type(body.get("reason")) is not str
        or not _exact_dict_has_keys(raw, frozenset({"schema_version", "status", "committed"}))
        or type(raw["schema_version"]) is not int or raw["schema_version"] != 1 or type(raw["status"]) is not str):
        return None
    if (type(code) is int and code == 409 and body.get("reason") == "card_action"
        and raw["status"] in _CARD_ACTION_STATUSES and raw["committed"] is False):
        return dict(raw)
    if (type(code) is int and code == 503 and body.get("reason") == "state_error"
        and raw["status"] == "confirmation_pending" and raw["committed"] is None):
        return dict(raw)
    return None


def _decode_master_key(value: Any) -> bytes:
    if not isinstance(value, str) or not value:
        raise ValueError("invalid activation master key")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (UnicodeEncodeError, TypeError, ValueError, binascii.Error) as exc:
        raise ValueError("invalid activation master key") from exc
    if len(decoded) != _MASTER_KEY_BYTES:
        raise ValueError("invalid activation master key length")
    return decoded


def _validated_entitlement_fields(lease: dict[str, Any]) -> Optional[tuple[Optional[str], dict[str, str]]]:
    """Validate the atomic signed plan/feature pair.

    Fully absent fields are accepted for pre-0.3.0 calculator leases. A partial,
    unknown or internally inconsistent pair is rejected instead of guessing from
    remaining days.
    """
    raw_plan = lease.get("plan_kind", _MISSING)
    raw_features = lease.get("feature_entitlements", _MISSING)
    if raw_plan is _MISSING and raw_features is _MISSING:
        return None, {}
    if raw_plan is _MISSING or raw_features is _MISSING:
        return None
    if not isinstance(raw_plan, str) or raw_plan not in _PLAN_PET_ACCESS:
        return None
    if not isinstance(raw_features, dict):
        return None
    pet_access = raw_features.get(_PET_FEATURE_KEY, _MISSING)
    if pet_access != _PLAN_PET_ACCESS[raw_plan]:
        return None
    return raw_plan, {_PET_FEATURE_KEY: pet_access}


def _pet_state_error(*, server_now: int) -> dict[str, Any]:
    return {
        "kind": "none",
        "status": "state_error",
        "server_now": server_now,
    }


def _exact_dict_has_keys(value: Any, expected: frozenset[str]) -> bool:
    if type(value) is not dict:
        return False
    keys = list(dict.keys(value))
    if any(type(key) is not str for key in keys):
        return False
    return frozenset(keys) == expected


def _strict_pet_int(
    value: Any,
    *,
    minimum: int,
    maximum: int,
) -> Optional[int]:
    if type(value) is not int or value < minimum or value > maximum:
        return None
    return value


def _strict_pet_text(
    value: Any,
    *,
    values: Optional[frozenset[str]] = None,
) -> Optional[str]:
    if type(value) is not str or not value or len(value) > 64:
        return None
    if not value.isascii():
        return None
    if values is not None and value not in values:
        return None
    return value


def _strict_pet_plan_pair(
    lease: dict[str, Any],
) -> Optional[tuple[Optional[str], Optional[str]]]:
    raw_plan = lease.get("plan_kind", _MISSING)
    raw_features = lease.get("feature_entitlements", _MISSING)
    if raw_plan is _MISSING and raw_features is _MISSING:
        return None, None
    if (
        type(raw_plan) is not str
        or raw_plan not in _PLAN_PET_ACCESS
        or not _exact_dict_has_keys(
            raw_features,
            frozenset({_PET_FEATURE_KEY}),
        )
    ):
        return None
    pet_access = raw_features.get(_PET_FEATURE_KEY)
    allowed = {
        "30d": frozenset({"none", "quota"}),
        "90d": frozenset({"none", "full"}),
        "365d": frozenset({"none", "full"}),
        "custom": frozenset({"none", "quota", "full"}),
        "trial": frozenset({"none", "trial"}),
        "legacy_permanent": frozenset({"none", "full"}),
        "permanent": frozenset({"none", "full"}),
    }
    if type(pet_access) is not str or pet_access not in allowed[raw_plan]:
        return None
    return raw_plan, pet_access


def _strict_pet_usage_payload(
    lease: dict[str, Any],
    *,
    plan_kind: Optional[str],
    pet_access: Optional[str],
    mode: str,
    paid_exp: Optional[int],
    trial_exp: Optional[int],
    server_now_fallback: int,
    signed_envelope_valid: bool,
    membership_revision: Optional[int] = None,
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """Project the A1 online Pet contract without coercion or user protocols."""
    fail_closed = (
        {_PET_FEATURE_KEY: "none"},
        {_PET_FEATURE_KEY: _pet_state_error(server_now=server_now_fallback)},
    )
    if not signed_envelope_valid:
        return fail_closed
    raw_usage = lease.get("feature_usage", _MISSING)
    if (
        not _exact_dict_has_keys(
            raw_usage,
            frozenset({"schema_version", _PET_FEATURE_KEY}),
        )
        or raw_usage.get("schema_version") not in (1, 2)
        or type(raw_usage.get("schema_version")) is not int
    ):
        return fail_closed
    usage_schema = raw_usage["schema_version"]
    if usage_schema == 2 and (type(membership_revision) is not int or membership_revision < 2):
        return fail_closed
    if usage_schema == 1 and type(membership_revision) is int and membership_revision > 1:
        return fail_closed
    pet = raw_usage.get(_PET_FEATURE_KEY)
    if type(pet) is not dict:
        return fail_closed
    kind = _strict_pet_text(
        pet.get("kind"),
        values=frozenset({"none", "trial", "quota", "full"}),
    )
    status = _strict_pet_text(
        pet.get("status"),
        values=_PET_STATUS_VALUES,
    )
    server_now = _strict_pet_int(
        pet.get("server_now"),
        minimum=1,
        maximum=_MAX_SAFE_EPOCH,
    )
    if (
        kind is None
        or status is None
        or server_now is None
        or plan_kind is None
        or pet_access is None
        or kind != pet_access
    ):
        return fail_closed
    minimal_error_keys = {"kind", "status", "server_now"}
    if status in {"state_error", "expired"}:
        if not _exact_dict_has_keys(pet, frozenset(minimal_error_keys)):
            return fail_closed
        return fail_closed
    if kind == "none":
        if (
            not _exact_dict_has_keys(pet, frozenset(minimal_error_keys))
            or status != "locked"
        ):
            return fail_closed
        return (
            {_PET_FEATURE_KEY: "none"},
            {_PET_FEATURE_KEY: dict(pet)},
        )
    if kind == "full":
        expected_keys = {"kind", "status", "server_now"}
        if plan_kind not in _PERMANENT_PLAN_KINDS:
            expected_keys.update({"duration_days", "entitlement_exp"})
        if (
            not _exact_dict_has_keys(pet, frozenset(expected_keys))
            or status != "active"
        ):
            return fail_closed
        if plan_kind in _PERMANENT_PLAN_KINDS:
            if mode != "paid" or paid_exp is not None:
                return fail_closed
        else:
            duration_days = _strict_pet_int(
                pet.get("duration_days"),
                minimum=90,
                maximum=10_000,
            )
            entitlement_exp = _strict_pet_int(
                pet.get("entitlement_exp"),
                minimum=1,
                maximum=_MAX_SAFE_EPOCH,
            )
            if (
                mode != "paid"
                or paid_exp is None
                or duration_days is None
                or (usage_schema == 2 and duration_days not in {90, 365})
                or (entitlement_exp != paid_exp if usage_schema == 1 else not server_now < entitlement_exp <= paid_exp)
                or (plan_kind == "90d" and duration_days != 90)
                or (plan_kind == "365d" and duration_days != 365)
                or (plan_kind == "custom" and duration_days < 90)
            ):
                return fail_closed
        return (
            {_PET_FEATURE_KEY: "full"},
            {_PET_FEATURE_KEY: dict(pet)},
        )
    expected_keys = {
        "kind",
        "total_seconds",
        "status",
        "server_now",
        "entitlement_id",
        "usage_revision",
        "remaining_seconds",
        "last_seq",
        "visible",
        "visibility_lease_exp",
        "entitlement_exp",
    }
    if kind == "quota":
        expected_keys.add("duration_days")
    if not _exact_dict_has_keys(pet, frozenset(expected_keys)):
        return fail_closed
    total_seconds = _strict_pet_int(
        pet.get("total_seconds"),
        minimum=1,
        maximum=86_400 if usage_schema == 1 else _MAX_SAFE_EPOCH,
    )
    duration_days = (
        _strict_pet_int(
            pet.get("duration_days"),
            minimum=30,
            maximum=89,
        )
        if kind == "quota"
        else None
    )
    entitlement_id = _strict_pet_text(pet.get("entitlement_id"))
    usage_revision = _strict_pet_int(
        pet.get("usage_revision"),
        minimum=1,
        maximum=_PET_MAX_SEQUENCE,
    )
    remaining_seconds = _strict_pet_int(
        pet.get("remaining_seconds"),
        minimum=0,
        maximum=(total_seconds if total_seconds is not None else 0),
    )
    last_seq = _strict_pet_int(
        pet.get("last_seq"),
        minimum=-1,
        maximum=_PET_MAX_SEQUENCE,
    )
    visible = pet.get("visible")
    visibility_lease_exp = _strict_pet_int(
        pet.get("visibility_lease_exp"),
        minimum=1,
        maximum=_MAX_SAFE_EPOCH,
    )
    entitlement_exp = _strict_pet_int(
        pet.get("entitlement_exp"),
        minimum=1,
        maximum=_MAX_SAFE_EPOCH,
    )
    if (
        total_seconds is None
        or entitlement_id is None
        or len(entitlement_id) != 64
        or any(char not in "0123456789abcdef" for char in entitlement_id)
        or usage_revision is None
        or remaining_seconds is None
        or last_seq is None
        or type(visible) is not bool
        or visibility_lease_exp is None
        or entitlement_exp is None
    ):
        return fail_closed
    if kind == "trial":
        if (
            mode != "trial"
            or plan_kind != "trial"
            or total_seconds != 600
            or trial_exp is None
            or entitlement_exp != trial_exp
        ):
            return fail_closed
    elif (
        mode != "paid"
        or plan_kind not in {"30d", "custom"}
        or paid_exp is None
        or duration_days is None
        or (entitlement_exp != paid_exp if usage_schema == 1 else not server_now < entitlement_exp <= paid_exp)
        or (plan_kind == "30d" and duration_days != 30)
        or (plan_kind == "30d" and (total_seconds != 43_200 if usage_schema == 1 else total_seconds % 43_200 != 0))
        or (usage_schema == 2 and (plan_kind != "30d" or duration_days != 30))
        or (plan_kind == "custom" and not 31 <= duration_days <= 89)
        or (plan_kind == "custom" and total_seconds != 86_400)
    ):
        return fail_closed
    if status == "active":
        if (
            not visible
            or remaining_seconds <= 0
            or last_seq < 0
            or visibility_lease_exp <= server_now
            or visibility_lease_exp > min(server_now + 60, entitlement_exp)
        ):
            return fail_closed
    elif status == "available":
        if visible or last_seq != -1 or remaining_seconds != total_seconds:
            return fail_closed
        if visibility_lease_exp > server_now:
            return fail_closed
    elif status == "paused":
        if visible or last_seq < 0 or remaining_seconds <= 0:
            return fail_closed
        if visibility_lease_exp > server_now:
            return fail_closed
    elif status == "exhausted":
        if visible or remaining_seconds != 0 or visibility_lease_exp > server_now:
            return fail_closed
    elif status in {"sequence_conflict", "session_conflict"}:
        if visible and visibility_lease_exp <= server_now:
            return fail_closed
        if not visible and visibility_lease_exp > server_now:
            return fail_closed
    else:
        return fail_closed
    return (
        {_PET_FEATURE_KEY: kind},
        {_PET_FEATURE_KEY: dict(pet)},
    )


def project_normalized_pet_contract(
    *,
    mode: Any,
    plan_kind: Any,
    feature_entitlements: Any,
    feature_usage: Any,
    paid_exp: Any,
    trial_exp: Any,
    feature_usage_schema_version: int = 1,
    membership_revision: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    """Revalidate a normalized result through the signed Pet plan/shape contract."""
    if type(mode) is not str or type(plan_kind) is not str:
        return None
    if not _exact_dict_has_keys(
        feature_entitlements,
        frozenset({_PET_FEATURE_KEY}),
    ):
        return None
    pet_access = dict.__getitem__(feature_entitlements, _PET_FEATURE_KEY)
    if type(pet_access) is not str:
        return None
    if not _exact_dict_has_keys(
        feature_usage,
        frozenset({_PET_FEATURE_KEY}),
    ):
        return None
    raw_pet = dict.__getitem__(feature_usage, _PET_FEATURE_KEY)
    if type(raw_pet) is not dict:
        return None
    pet_keys = list(dict.keys(raw_pet))
    if any(type(key) is not str for key in pet_keys):
        return None
    stable_pet: dict[str, str | int | bool] = {}
    for key in pet_keys:
        value = dict.__getitem__(raw_pet, key)
        if type(value) not in {str, int, bool}:
            return None
        stable_pet[key] = value

    normalized_paid_exp: Optional[int] = None
    normalized_trial_exp: Optional[int] = None
    if mode == "trial":
        if (
            plan_kind != "trial"
            or paid_exp is not None
            or type(trial_exp) is not int
        ):
            return None
        normalized_trial_exp = _strict_pet_int(
            trial_exp,
            minimum=1,
            maximum=_MAX_SAFE_EPOCH,
        )
        if normalized_trial_exp is None:
            return None
    elif mode == "paid":
        if trial_exp is not None or plan_kind == "trial":
            return None
        if plan_kind in _PERMANENT_PLAN_KINDS:
            if paid_exp is not None:
                return None
        else:
            if type(paid_exp) is not int:
                return None
            normalized_paid_exp = _strict_pet_int(
                paid_exp,
                minimum=1,
                maximum=_MAX_SAFE_EPOCH,
            )
            if normalized_paid_exp is None:
                return None
    else:
        return None

    lease = {
        "plan_kind": plan_kind,
        "feature_entitlements": {
            _PET_FEATURE_KEY: pet_access,
        },
        "feature_usage": {
            "schema_version": feature_usage_schema_version,
            _PET_FEATURE_KEY: stable_pet,
        },
    }
    plan_pair = _strict_pet_plan_pair(lease)
    if plan_pair is None or plan_pair != (plan_kind, pet_access):
        return None
    server_now = stable_pet.get("server_now")
    if type(server_now) is not int:
        return None
    canonical_features, canonical_usage = _strict_pet_usage_payload(
        lease,
        plan_kind=plan_kind,
        pet_access=pet_access,
        mode=mode,
        paid_exp=normalized_paid_exp,
        trial_exp=normalized_trial_exp,
        server_now_fallback=server_now,
        signed_envelope_valid=True,
        membership_revision=membership_revision,
    )
    canonical_pet = canonical_usage.get(_PET_FEATURE_KEY)
    if (
        canonical_features != {_PET_FEATURE_KEY: pet_access}
        or type(canonical_pet) is not dict
        or canonical_pet != stable_pet
    ):
        return None
    return canonical_pet


def _validated_activation_fields(
    *,
    lease: Any,
    master_key_b64: Any,
    mode: Any,
    device: str,
    now: int,
    trial_exp: Any = _MISSING,
    signed_envelope_valid: Optional[bool] = None,
    credential_uid: Any = _MISSING,
) -> Optional[dict[str, Any]]:
    """Normalize a complete activation result, or reject the whole result.

    Online responses and diagnostic cache inspection use the same structural
    contract. Only a current online response may authorize the product. This
    deliberately avoids permissive int()/str() coercions: booleans, floats,
    numeric strings and absurd integers must never become a valid lease or a
    legacy-permanent paid grant.
    """
    try:
        if mode not in ("paid", "trial") or not isinstance(lease, dict):
            return None
        uid = lease.get("uid")
        if not isinstance(uid, str) or not uid:
            return None
        if lease.get("device") != device:
            return None
        exp = _strict_epoch(lease.get("exp"))
        if exp <= now:
            return None
        master_key = _decode_master_key(master_key_b64)

        paid_exp: Optional[int] = None
        normalized_trial_exp: Optional[int] = None
        if mode == "trial":
            if trial_exp is _MISSING:
                return None
            normalized_trial_exp = _strict_epoch(trial_exp)
            if normalized_trial_exp != exp:
                return None
        else:
            raw_paid_exp = lease.get("paid_exp", _MISSING)
            if raw_paid_exp is not _MISSING:
                paid_exp = _strict_epoch(raw_paid_exp)
                if paid_exp < exp:
                    return None

        membership = _validated_membership(lease, mode=mode, paid_exp=paid_exp, credential_uid=credential_uid)
        if membership is not None and signed_envelope_valid is False:
            return None
        if signed_envelope_valid is None:
            entitlement_fields = _validated_entitlement_fields(lease)
            if entitlement_fields is None:
                return None
            plan_kind, feature_entitlements = entitlement_fields
            feature_usage = None
        else:
            entitlement_fields = _strict_pet_plan_pair(lease)
            if entitlement_fields is None:
                return None
            plan_kind, raw_pet_access = entitlement_fields
            if plan_kind is None:
                feature_entitlements = {}
                feature_usage = None
            elif (
                not signed_envelope_valid
                and lease.get("feature_usage", _MISSING) is _MISSING
                and _validated_entitlement_fields(lease) is not None
            ):
                # Legacy unit/plugin responses used a non-production suffix and
                # predate A1 feature_usage.  Preserve only their old in-process
                # entitlement seam; current A1 responses never enter this arm.
                plan_kind, feature_entitlements = (
                    _validated_entitlement_fields(lease)
                )
                feature_usage = None
            else:
                feature_entitlements, feature_usage = _strict_pet_usage_payload(
                    lease,
                    plan_kind=plan_kind,
                    pet_access=raw_pet_access,
                    mode=mode,
                    paid_exp=paid_exp,
                    trial_exp=normalized_trial_exp,
                    server_now_fallback=now,
                    signed_envelope_valid=signed_envelope_valid,
                    membership_revision=membership["revision"] if membership else None,
                )
        if plan_kind is not None:
            if mode == "trial" and plan_kind != "trial":
                return None
            if mode == "paid" and plan_kind == "trial":
                return None
            if mode == "paid" and paid_exp is None and plan_kind not in _PERMANENT_PLAN_KINDS:
                return None
            if mode == "paid" and paid_exp is not None and plan_kind in _PERMANENT_PLAN_KINDS:
                return None
    except (TypeError, ValueError, OverflowError):
        return None

    return {
        "lease": lease,
        "master_key": master_key,
        "mode": mode,
        "exp": exp,
        "trial_exp": normalized_trial_exp,
        "paid_exp": paid_exp,
        "plan_kind": plan_kind,
        "feature_entitlements": feature_entitlements,
        "feature_usage": feature_usage,
        "membership": membership,
        "feature_usage_schema_version": 2 if type(lease.get("feature_usage")) is dict
            and type(lease["feature_usage"].get("schema_version")) is int
            and lease["feature_usage"].get("schema_version") == 2 else 1,
    }


def _result_from_cache(cached: Optional[dict], *, device: str, ingest_url: Optional[str], now: int) -> Optional[ActivationResult]:
    """Fail closed: current disk cache has no client-verifiable server signature."""
    return None


def _strict_pet_usage_event(
    value: Any,
) -> Optional[dict[str, Any]]:
    if (
        type(value) is not dict
        or any(type(key) is not str for key in dict.keys(value))
        or frozenset(dict.keys(value)) != _PET_USAGE_EVENT_KEYS
    ):
        return None
    session_id = dict.get(value, "session_id")
    seq = dict.get(value, "seq")
    visible = dict.get(value, "visible")
    if (
        type(session_id) is not str
        or not 16 <= len(session_id) <= 128
        or any(
            not (
                "A" <= char <= "Z"
                or "a" <= char <= "z"
                or "0" <= char <= "9"
                or char in "._:-"
            )
            for char in session_id
        )
        or type(seq) is not int
        or not 0 <= seq <= _PET_MAX_SEQUENCE
        or type(visible) is not bool
    ):
        return None
    return {
        "session_id": session_id,
        "seq": seq,
        "visible": visible,
    }


@dataclass(frozen=True)
class _ActivationPostHandle:
    """One irreversibly started activation transport with an external wait."""

    worker: threading.Thread
    outcome: list[Any]
    errors: list[BaseException]

    def result(self) -> Any:
        self.worker.join()
        if self.errors:
            raise self.errors[0]
        return self.outcome[0] if self.outcome else None


def _begin_activation_transport(
    url: str,
    device: str,
    token: str,
    timeout: float,
    pet_usage_event: Optional[dict[str, Any]],
    card_token: Optional[str] = None,
) -> _ActivationPostHandle:
    """Irreversibly begin one transport without waiting for its response."""

    outcome: list[Any] = []
    errors: list[BaseException] = []
    with _ACTIVATION_IDENTITY_SOURCE_LOCK:
        retry_revision = _ACTIVATION_IDENTITY_SOURCE_REVISION
        retry_clear_epoch = _ACTIVATION_CLEAR_EPOCH

    def _transport() -> None:
        try:
            with _activation_direct_retry_fence(
                retry_revision,
                retry_clear_epoch,
            ):
                # Keep the four-argument seam for ordinary activation callers.
                if card_token is not None:
                    outcome.append(_post_activate(url, device, token, timeout, pet_usage_event, card_token))
                elif pet_usage_event is None:
                    outcome.append(_post_activate(url, device, token, timeout))
                else:
                    outcome.append(
                        _post_activate(url, device, token, timeout, pet_usage_event)
                    )
        except BaseException as exc:  # noqa: BLE001 - re-raised on caller
            errors.append(exc)

    worker = threading.Thread(
        target=_transport,
        name="bidking-activation-pet-transport",
        daemon=True,
    )
    worker.start()
    return _ActivationPostHandle(
        worker=worker,
        outcome=outcome,
        errors=errors,
    )


def _begin_activation_post(
    url: str,
    device: str,
    token: str,
    timeout: float,
    pet_usage_event: dict[str, Any],
) -> _ActivationPostHandle:
    """Preserve the established Pet-only transport-start seam."""
    return _begin_activation_transport(url, device, token, timeout, pet_usage_event)


def _identity_bound_pet_usage_post(
    url: str,
    *,
    ui_token: Optional[str],
    expected_identity: ActivationIdentitySnapshot,
    timeout: float,
    pet_usage_event: Optional[dict[str, Any]],
    card_token: Optional[str] = None,
) -> tuple[bool, Any]:
    """Start an activation/Pet transport only while its identity is eligible.

    The start barrier is shared with every supported identity writer.  The
    synchronous transport-begin call is the irreversible
    linearization point; response waiting happens only after both short locks
    have been released.
    """
    if (
        type(expected_identity) is not ActivationIdentitySnapshot
        or type(expected_identity.token) is not str
        or type(expected_identity.device) is not str
        or not expected_identity.device
        or type(expected_identity.revision) is not int
        or expected_identity.revision < 0
        or expected_identity.revision % 2
        or type(expected_identity.clear_epoch) is not int
        or expected_identity.clear_epoch < 0
    ):
        return False, None

    with (
        _ACTIVATION_TRANSPORT_START_LOCK,
        _ACTIVATION_IDENTITY_SOURCE_LOCK,
    ):
        current_identity = activation_identity_snapshot(ui_token)
        if (
            not _same_source_identity(expected_identity, current_identity)
            or not _activation_clear_epoch_allows_grant(current_identity)
        ):
            return False, None
        device = current_identity.device
        token = current_identity.token
        stable_event = None if pet_usage_event is None else {
            "session_id": pet_usage_event["session_id"],
            "seq": pet_usage_event["seq"],
            "visible": pet_usage_event["visible"],
        }
        if card_token is not None:
            handle = _begin_activation_transport(url, device, token, timeout, stable_event, card_token)
        else:
            begin = _begin_activation_transport if stable_event is None else _begin_activation_post
            handle = begin(url, device, token, timeout, stable_event)

    return True, handle.result()


def _identity_bound_pet_usage_http_e2ee_post(
    config: activation_http_e2ee.ActivationHttpE2EEConfig,
    *,
    ui_token: Optional[str],
    expected_identity: ActivationIdentitySnapshot,
    timeout: float,
    pet_usage_event: Optional[dict[str, Any]],
    card_token: Optional[str] = None,
) -> tuple[bool, Any]:
    """Apply the same irreversible-start identity gate to the emergency carrier."""
    if (
        type(expected_identity) is not ActivationIdentitySnapshot
        or type(expected_identity.token) is not str
        or type(expected_identity.device) is not str
        or not expected_identity.device
        or type(expected_identity.revision) is not int
        or expected_identity.revision < 0
        or expected_identity.revision % 2
        or type(expected_identity.clear_epoch) is not int
        or expected_identity.clear_epoch < 0
    ):
        return False, None

    outcome: list[Any] = []
    errors: list[BaseException] = []
    with (
        _ACTIVATION_TRANSPORT_START_LOCK,
        _ACTIVATION_IDENTITY_SOURCE_LOCK,
    ):
        current_identity = activation_identity_snapshot(ui_token)
        if (
            not _same_source_identity(expected_identity, current_identity)
            or not _activation_clear_epoch_allows_grant(current_identity)
        ):
            return False, None
        stable_event = None if pet_usage_event is None else {
            "session_id": pet_usage_event["session_id"],
            "seq": pet_usage_event["seq"],
            "visible": pet_usage_event["visible"],
        }

        def _transport() -> None:
            try:
                args = (config, current_identity.device, current_identity.token, timeout)
                if card_token is not None:
                    outcome.append(_post_activate_http_e2ee(*args, stable_event, card_token))
                else:
                    outcome.append(_post_activate_http_e2ee(*args) if stable_event is None
                                   else _post_activate_http_e2ee(*args, stable_event))
            except BaseException as exc:  # noqa: BLE001 - re-raised below
                errors.append(exc)

        worker = threading.Thread(
            target=_transport,
            name="bidking-activation-http-e2ee-pet-transport",
            daemon=True,
        )
        worker.start()

    worker.join()
    if errors:
        raise errors[0]
    return True, outcome[0] if outcome else None


def activate(*, ui_token: Optional[str] = None, ingest_url: Optional[str] = None,
             timeout: float = 4.0, now: Optional[int] = None,
             force_server: bool = False,
             defer_commit: bool = False,
             pet_usage_event: Optional[dict[str, Any]] = None,
             card_token: Optional[str] = None,
             expected_identity: Optional[ActivationIdentitySnapshot] = None,
             ) -> ActivationResult | ActivationCandidate:
    """启动激活: POST /activate 后返回结果，或返回待最终门提交的 candidate。

    无 token 自动走试用(mode=trial+trial_exp); 有 token 走付费。硬拒(撤销/换机/试用到期/坏 token)
    清缓存防残留。服务不可达时 fail-closed 降级(activated=False)，落盘缓存不恢复授权。

    force_server: True = 周期【心跳】, 跳过缓存优先直接回连服务端(试用按使用时长累计需要每拍真到达
    服务端才"花掉"时间; 也更快捡撤销/付费)。

    ``defer_commit=True`` 只执行身份快照、网络和解析；token/cache/provider
    副作用由 enforce 通过 generation + identity 最终门后调用
    :func:`commit_activation_candidate` 一次性提交。
    """
    _reset_activation_transport_trace()
    now = int(time.time()) if now is None else int(now)
    normalized_pet_usage_event = None
    if pet_usage_event is not None:
        normalized_pet_usage_event = _strict_pet_usage_event(pet_usage_event)
        if normalized_pet_usage_event is None:
            return ActivationResult(
                activated=False,
                mode="none",
                reason="state_error",
                source="client",
            )
    identity_snapshot = activation_identity_snapshot(ui_token)
    if card_token is not None and (
        ui_token is not None or pet_usage_event is not None
        or _credential_uid(card_token) is None
        or identity_snapshot is None or _credential_uid(identity_snapshot.token) is None
    ):
        return ActivationResult(activated=False, mode="paid", reason="invalid_card_request", source="client")

    if not activation_submission_preflight(ui_token):
        return ActivationResult(
            activated=False, mode="none", reason="local_clear_incomplete", source="client",
        )

    def _complete(
        candidate: ActivationCandidate,
    ) -> ActivationResult | ActivationCandidate:
        if defer_commit:
            return candidate
        return commit_activation_candidate(candidate).result

    if identity_snapshot is None:
        return ActivationResult(
            activated=False,
            mode="none",
            reason="state_error",
            source="server",
        )
    pet_transport_identity = (
        expected_identity
        if expected_identity is not None
        else identity_snapshot
    )
    if (
        normalized_pet_usage_event is not None
        and not _same_source_identity(
            pet_transport_identity,
            identity_snapshot,
        )
    ):
        return ActivationResult(
            activated=False,
            mode="none",
            reason="state_error",
            source="server",
        )
    device = identity_snapshot.device
    token = identity_snapshot.token
    ingest_url = ingest_url or _resolve_ingest_url()

    # 回连服务端(逐个试候选地址: 连不上就换下一个; 服务器有响应即停 —— 4xx/5xx 不该换地址重试)
    endpoints = _activation_endpoint_candidates(ingest_url)
    if not endpoints:
        # 无地址：落盘缓存不能形成授权。
        return ActivationResult(
            activated=False, mode="none", reason="no_server", source="server"
        )

    online = None
    activation_url = None
    activation_telemetry_url: Any = _MISSING
    transport_failures: list[str] = []
    transport_stages: list[str] = []
    transport_availability = ""
    for endpoint in endpoints:
        url = endpoint.activation_url
        transport_started, candidate = _identity_bound_pet_usage_post(
            url,
            ui_token=ui_token,
            expected_identity=pet_transport_identity,
            timeout=timeout,
            pet_usage_event=normalized_pet_usage_event,
            **({"card_token": card_token} if card_token is not None else {}),
        )
        if not transport_started:
            clear_blocked = not activation_submission_preflight(ui_token)
            return ActivationResult(
                activated=False,
                mode="none",
                reason="local_clear_incomplete" if clear_blocked else "state_error",
                source="client" if clear_blocked else "server",
            )
        if isinstance(candidate, _ActivationPostFailure):
            safe_stage = (
                candidate.stage
                if candidate.stage in _ACTIVATION_TRANSPORT_STAGES
                else _activation_transport_stage(candidate.failure_class)
            )
            transport_stages.append(safe_stage)
            _append_activation_transport_trace(
                endpoint_id=endpoint.endpoint_id,
                stage=safe_stage,
                failure_class=candidate.failure_class,
            )
            prior_failure_classes = (
                candidate.prior_failure_classes
                if type(candidate.prior_failure_classes) is tuple
                else ()
            )
            for failure_class in (*prior_failure_classes, candidate.failure_class):
                if (
                    type(failure_class) is str
                    and failure_class in _ACTIVATION_FAILURE_CLASSES
                    and failure_class not in transport_failures
                ):
                    transport_failures.append(failure_class)
            if not candidate.retry_fallback:
                break
            continue
        if candidate is None:
            # 保留旧测试/插件 monkeypatch 的 None 合同，同时给产品态固定分类。
            transport_stages.append("unknown")
            _append_activation_transport_trace(
                endpoint_id=endpoint.endpoint_id,
                stage="unknown",
                failure_class="transport_unknown",
            )
            if "transport_unknown" not in transport_failures:
                transport_failures.append("transport_unknown")
            continue
        transport_stages.append("http")
        _append_activation_transport_trace(
            endpoint_id=endpoint.endpoint_id,
            stage="http",
            failure_class="http_response",
        )
        if type(candidate) is _ActivationPostResponse:
            prior_failure_classes = (
                candidate.prior_failure_classes
                if type(candidate.prior_failure_classes) is tuple
                else ()
            )
            for failure_class in prior_failure_classes:
                if (
                    type(failure_class) is str
                    and failure_class in _ACTIVATION_FAILURE_CLASSES
                    and failure_class not in transport_failures
                ):
                    transport_failures.append(failure_class)
            if (
                type(candidate.transport_availability) is str
                and candidate.transport_availability
                == "system_proxy_tls_path_incompatible"
            ):
                transport_availability = candidate.transport_availability
        online = candidate
        if online is not None:   # 服务器响应了(含 4xx/5xx)→ 就是它, 不再试兜底地址
            activation_url = url
            activation_telemetry_url = endpoint.telemetry_url
            break
    https_only_network_failure = bool(
        online is None
        and len(transport_stages) == len(endpoints)
        and all(stage in {"dns", "tcp", "tls"} for stage in transport_stages)
    )
    emergency_config = (
        _load_activation_http_e2ee_config()
        if https_only_network_failure
        else None
    )
    if emergency_config is not None:
        transport_started, candidate = _identity_bound_pet_usage_http_e2ee_post(
            emergency_config,
            ui_token=ui_token,
            expected_identity=pet_transport_identity,
            timeout=timeout,
            pet_usage_event=normalized_pet_usage_event,
            **({"card_token": card_token} if card_token is not None else {}),
        )
        if not transport_started:
            clear_blocked = not activation_submission_preflight(ui_token)
            return ActivationResult(
                activated=False,
                mode="none",
                reason="local_clear_incomplete" if clear_blocked else "state_error",
                source="client" if clear_blocked else "server",
            )
        if isinstance(candidate, _ActivationPostFailure):
            safe_stage = (
                candidate.stage
                if candidate.stage in _ACTIVATION_TRANSPORT_STAGES
                else _activation_transport_stage(candidate.failure_class)
            )
            transport_stages.append(safe_stage)
            _append_activation_transport_trace(
                endpoint_id=activation_http_e2ee.ENDPOINT_ID,
                stage=safe_stage,
                failure_class=candidate.failure_class,
            )
            if candidate.failure_class not in transport_failures:
                transport_failures.append(candidate.failure_class)
        elif candidate is None:
            transport_stages.append("unknown")
            _append_activation_transport_trace(
                endpoint_id=activation_http_e2ee.ENDPOINT_ID,
                stage="unknown",
                failure_class="transport_unknown",
            )
            if "transport_unknown" not in transport_failures:
                transport_failures.append("transport_unknown")
        else:
            transport_stages.append("http")
            _append_activation_transport_trace(
                endpoint_id=activation_http_e2ee.ENDPOINT_ID,
                stage="http",
                failure_class="http_response",
            )
            online = candidate
            activation_url = emergency_config.url
            activation_telemetry_url = None
    transport_summary = _activation_transport_summary(
        transport_stages,
        endpoint_count=len(endpoints) + (1 if emergency_config is not None else 0),
        availability=transport_availability,
    )
    if online is None:
        # 连不上：落盘缓存不能形成授权。
        return ActivationResult(activated=False, mode=("paid" if token else "none"),
                                reason="offline", source="server",
                                transport_failure_classes=tuple(transport_failures),
                                transport_summary=transport_summary)

    if type(online) is _ActivationPostResponse:
        code, body = online.code, online.body
    else:
        code, body = online
    invalid_transport_failures = tuple(transport_failures)
    if "http_reachable_invalid_payload" not in invalid_transport_failures:
        invalid_transport_failures = (
            *invalid_transport_failures,
            "http_reachable_invalid_payload",
        )
    response_ok = body.get("ok", _MISSING)
    # The activation response is security-sensitive.  Truthiness is not a
    # protocol: values such as "false", 1 or [] must never select either the
    # success path or an authoritative-denial path. Treat a malformed `ok`
    # field like any other invalid server response.
    if response_ok is not True and response_ok is not False:
        return ActivationResult(
            activated=False,
            mode=("paid" if token else "none"),
            reason="offline",
            source="server",
            transport_failure_classes=invalid_transport_failures,
            transport_summary=transport_summary,
        )
    if response_ok is True and not (type(code) is int and 200 <= code < 300):
        # A JSON body cannot override HTTP failure semantics.  In particular, a proxy or broken
        # server returning 401/500 with {"ok": true} must not rotate token/cache or activate.
        return ActivationResult(
            activated=False,
            mode=("paid" if token else "none"),
            reason="offline",
            source="server",
            transport_failure_classes=invalid_transport_failures,
            transport_summary=transport_summary,
        )
    if response_ok is True:
        lease, signed_envelope_valid = _lease_envelope_payload(body.get("lease"))
        mode = body.get("mode")
        fields = _validated_activation_fields(
            lease=lease,
            master_key_b64=body.get("master_key"),
            mode=mode,
            device=device,
            now=now,
            trial_exp=(body.get("trial_exp", _MISSING) if mode == "trial" else _MISSING),
            signed_envelope_valid=signed_envelope_valid,
            credential_uid=_credential_uid(token) if token else _MISSING,
        )
        # A malformed ok=true response is a transport/server failure, never a token
        # denial and never something that may be persisted as a cache.
        if fields is None:
            return ActivationResult(
                activated=False,
                mode=("paid" if token else "none"),
                reason="offline",
                source="server",
                transport_failure_classes=invalid_transport_failures,
                transport_summary=transport_summary,
            )
        card_ack = _validated_card_ack(body, fields) if card_token is not None else None
        if card_token is not None and card_ack is None:
            return ActivationResult(activated=False, mode="paid", reason="invalid_card_response", source="server")
        mk = fields["master_key"]
        exp = fields["exp"]
        upload_lease_projection = _project_upload_lease(
            body.get("upload_lease"),
            now=now,
        )
        rotating_ui_token = bool(ui_token and ui_token.strip())
        return _complete(
            ActivationCandidate(
                result=ActivationResult(
                    activated=True,
                    mode=fields["mode"],
                    reason="ok",
                    source="server",
                    trial_exp=fields["trial_exp"],
                    master_key=mk,
                    lease_exp=exp,
                    paid_exp=fields["paid_exp"],
                    plan_kind=fields["plan_kind"],
                    feature_entitlements=fields["feature_entitlements"],
                    feature_usage=fields["feature_usage"],
                    membership=fields["membership"],
                    membership_card_capability=_membership_capability(body.get("membership_card_capability")),
                    card_redemption=card_ack,
                    feature_usage_schema_version=fields["feature_usage_schema_version"],
                    transport_summary=transport_summary,
                    roaming_rebinds=(
                        _project_roaming_rebinds(fields["lease"].get("roaming_rebinds"))
                        if fields["mode"] == "paid" else None
                    ),
                ),
                identity_snapshot=identity_snapshot,
                ui_token=ui_token,
                commit_plan=_ActivationCommitPlan(
                    kind="grant",
                    lease=fields["lease"],
                    master_key=mk,
                    device=device,
                    token=token,
                    mode=fields["mode"],
                    now=now,
                    exp=exp,
                    ingest_url=ingest_url,
                    activation_url=activation_url,
                    telemetry_url=activation_telemetry_url,
                    rotating_ui_token=rotating_ui_token,
                    upload_lease=upload_lease_projection,
                ),
                _validated_grant=True,
            ),
        )

    # 区分【权威拒绝】vs【瞬时错误】: 401/403/426 + 确定 deny reason 才是权威拒绝(清缓存+硬降级);
    # 瞬时错误不清缓存，但也不从缓存恢复授权；保留 HTTP 故障类别，避免把
    # state_error/429/普通 5xx 都误导成“网络离线”。
    reason = str(body.get("reason", "") or "")
    is_authoritative = (code in (401, 403, 426)) and (reason in _AUTHORITATIVE_DENY_REASONS)
    if card_token is not None and not is_authoritative:
        action = _validated_card_action(code, body)
        return ActivationResult(activated=False, mode="paid", source="server",
                                reason=("card_action" if code == 409 else "confirmation_pending")
                                if action is not None else "invalid_card_response",
                                card_redemption=action, transport_summary=transport_summary)
    if not is_authoritative:
        if code == 429:
            transient_reason = "rate_limited"
        elif code == 503 and reason == "state_error":
            transient_reason = "state_error"
        elif code == 426:
            transient_reason = "upgrade_required"
        elif code in (401, 403):
            transient_reason = "server_rejected"
        elif type(code) is int and 500 <= code < 600:
            transient_reason = "server_error"
        else:
            transient_reason = "offline"
        return ActivationResult(activated=False, mode=("paid" if token else "none"),
                                reason=transient_reason, source="server",
                                transport_summary=transport_summary)
    # 权威拒绝(撤销/换机/坏token/试用或会员到期)-> 清缓存(防离线残留旧 lease)+ 硬降级。
    # 但若被拒的是【新粘贴的、尚未持久化的不同 token】(粘错), 别清旧持久态；
    # 进程内旧授权是否临时保留由 enforce 的严格 allowlist 与时间门单独决定。
    mode = str(body.get("mode", "none"))
    pasted_new = bool(ui_token and ui_token.strip()) and token != _read_persisted_token()
    # upgrade_required 是客户端代际拒绝；device_revoked 是设备级全局拒绝。两者都与
    # 粘贴哪个 token 无关，必须清除旧缓存，不能把“保护错 token”扩大成继续保留旧设备授权。
    clear_authoritative_cache = (
        reason in {"upgrade_required", "device_revoked"} or not pasted_new
    )
    result = ActivationResult(
        activated=False,
        mode=mode,
        reason=reason,
        source="server",
        transport_summary=transport_summary,
        roaming_rebind_limit_reached=(
            type(code) is int and code == 403
            and type(body.get("mode")) is str and body["mode"] == "paid"
            and type(body.get("reason")) is str and body["reason"] == "device_mismatch"
            and type(body.get("detail")) is str and body["detail"] == "roaming_rebind_limit"
        ),
    )
    if result.roaming_rebind_limit_reached:
        result.roaming_rebinds = _project_roaming_rebinds(body.get("roaming_rebinds"))
    if not clear_authoritative_cache:
        return result
    return _complete(
        ActivationCandidate(
            result=result,
            identity_snapshot=identity_snapshot,
            ui_token=ui_token,
            commit_plan=_ActivationCommitPlan(
                kind="authoritative_clear",
                device=device,
            ),
            _authoritative_rejection=True,
        )
    )


def _lease_envelope_payload(signed_lease: Any) -> tuple[dict, bool]:
    """从当前 HTTPS 成功响应的签名 lease 取 payload。

    当前客户端没有服务端 HMAC secret 或独立公钥 verifier，所以这里只能在本次在线
    响应内使用 payload；后缀检查只验证协议形状，不能冒充密码学验签。提取结果写入
    磁盘后绝不能作为离线授权证明。
    """
    if type(signed_lease) is not str or "." not in signed_lease:
        return {}, False
    payload, suffix = signed_lease.rsplit(".", 1)
    suffix_valid = bool(
        len(suffix) == _PET_SERVER_SIGNATURE_HEX_LENGTH
        and all(char in "0123456789abcdef" for char in suffix)
    )
    try:
        d = json.loads(payload)
        return (d if type(d) is dict else {}), suffix_valid
    except Exception:  # noqa: BLE001
        return {}, suffix_valid


def _lease_payload(signed_lease: Any) -> dict:
    """Compatibility wrapper for diagnostics/tests that only need the payload."""
    return _lease_envelope_payload(signed_lease)[0]
