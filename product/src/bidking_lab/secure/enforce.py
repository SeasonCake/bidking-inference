# -*- coding: utf-8 -*-
"""L4 启动激活强制(②的强制层)。启动时调激活、打状态横幅、装好 provider,并给调用方
【时间感知的放行判据 + UI 渲染状态】,供 monitor(gate 推理)与 overlay(显示倒计时 + 降级 + 弹 token)共用。

0.2.2 起 = 付费版【真强制】: 未激活 / 试用到期 → should_run_full=False → 值钱的报价计算被降级。
【真门在 overlay(用户面)】: 报价推荐只在 overlay 跑 run_reference_engine, 手填/worker/live-apply 三路全 gate
在 should_run_full + 弹 token 清空英雄提示。**出货 monitor 只捕获原始帧、不算 bids**(启动器不带 --formal-mode),
写盘快照无算好的报价可绕, 故它本身【无推理门也无需推理门】; 仅 dev/eval 的 `run_windivert_live_monitor.py
--formal-mode` 会跑 monitor 推理(build_monitor_artifact_from_events·刻意未 gate·仅内部标定·见该函数注释)。
试用剩余 < 15 分钟 → activation_state.warn=True(UI 变红)。见 WARN_SECONDS。

铁律: 绝不抛、绝不阻塞启动。激活失败/异常 = 降级结果(activated=False)+ 横幅, 绝不崩监控
(采样/激活是附加, 不能拖垮对局)。强制只锁【计算输出】, 不锁抓包/进程本身。
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import threading
import time
from typing import Any, Callable, Optional

# 试用剩余低于此(秒)→ UI 变红告警(作者 07-09: 变红阈值 1h→15min, 免短试用全程红;
# 前 45 分正常显示倒计时, 最后 15 分才变红=倒计时提醒而非一直报警)。
WARN_SECONDS = 900
# 限期会员剩余低于此(秒)→ UI 变红(剩 1 天; 会员按日历, 提前一天提醒续费)。
WARN_PAID_SECONDS = 86400

# 激活成功/权威拒绝维持 60s 心跳；只有断网、无地址或本地异常这类瞬时失败快速退避。
# 旧实现首次失败后固定等 60s，UI 会先显示锁定，网络恢复后仍要等整整一个心跳周期。
_ACTIVATION_HEARTBEAT_SECONDS = 60.0
_ACTIVATION_TRANSIENT_RETRY_SECONDS = (2.0, 5.0, 15.0, 60.0)
_ACTIVATION_TRANSPORT_GRACE_SECONDS = 15.0
_ACTIVATION_TRANSPORT_GRACE_FAILURES = 2
_ACTIVATION_ONLINE_GRANT_RECENCY_SECONDS = 120.0
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
_TRANSIENT_NON_ENTITLEMENT_REASONS = frozenset(
    {"offline", "no_server", "state_error", "rate_limited", "server_error"}
)
_SAFE_TRANSPORT_FAILURE_CLASSES = frozenset(
    {
        "dns_lookup_failed",
        "connect_timeout",
        "connect_refused",
        "tls_connection_reset",
        "tls_handshake_failed",
        "transport_os_error",
        "transport_unknown",
        "http_reachable_invalid_payload",
    }
)
_SYSTEM_PROXY_TLS_PATH_INCOMPATIBLE = "system_proxy_tls_path_incompatible"
_GRACEABLE_NETWORK_FAILURE_CLASSES = frozenset(
    {
        "dns_lookup_failed",
        "connect_timeout",
        "connect_refused",
        "tls_connection_reset",
        "tls_handshake_failed",
        "transport_os_error",
        "transport_unknown",
    }
)
_SAFE_TRANSPORT_STAGES = ("dns", "tcp", "tls", "http", "unknown")
_ACTIVATION_IDENTITY_TOKEN_MAX_CHARS = 4096
_ACTIVATION_IDENTITY_DEVICE_MAX_CHARS = 512
_ACTIVATION_IDENTITY_COUNTER_MAX = (1 << 64) - 1
_ACTIVATION_IDENTITY_SNAPSHOT_KEYS = frozenset(
    {"token", "device", "revision", "clear_epoch"}
)

# monitor / overlay 各自进程独立维护。_LAST 表示当前授权视图，
# _LAST_HEARTBEAT 单独保存最近一次线路结果，避免单次闪断覆盖在线成功 grant。
_LAST: Any = None
_LAST_ONLINE_GRANT: Any = None
_LAST_ONLINE_IDENTITY: Optional[tuple[bytes, bytes, int, int]] = None
_LAST_ONLINE_CONFIRMED_AT: Optional[float] = None
_LAST_PET_USAGE_AUTHORITY: Optional[dict[str, Any]] = None
_LAST_PET_USAGE_AUTHORITY_SCHEMA = 1
# Online metadata rollback floor only; never an offline grant or a token source.
_MEMBERSHIP_REVISION_FLOORS: dict[tuple[bytes, bytes], tuple[str, int]] = {}
_LAST_HEARTBEAT: Any = None
_HEARTBEAT_FAILURE_COUNT = 0
_HEARTBEAT_FAILURE_STARTED_AT: Optional[float] = None
# Latest-request-wins generation.  It protects the commit boundary without
# holding _LOCK across the network call, so an A -> B -> A UI sequence cannot
# let the oldest A response overwrite the newer identity's result.
_ACTIVATION_REQUEST_GENERATION = 0
_PET_USAGE_IDENTITY_GENERATION = 0
_LAST_OBSERVED_PET_USAGE_IDENTITY: Optional[tuple[bytes, bytes, int, int]] = None
_LOCK = threading.Lock()


def _strictly_time_valid_process_grant(result: Any, *, now: Optional[int] = None) -> bool:
    """Return true only for a still-valid grant obtained in this process."""
    if (
        result is None
        or not bool(getattr(result, "activated", False))
        or getattr(result, "source", None) != "server"
    ):
        return False
    now = int(time.time()) if now is None else int(now)
    lease_exp = getattr(result, "lease_exp", None)
    if not isinstance(lease_exp, int) or isinstance(lease_exp, bool) or lease_exp <= now:
        return False
    mode = getattr(result, "mode", "")
    if mode == "trial":
        trial_exp = getattr(result, "trial_exp", None)
        return (
            isinstance(trial_exp, int)
            and not isinstance(trial_exp, bool)
            and trial_exp > now
        )
    if mode != "paid":
        return False
    paid_exp = getattr(result, "paid_exp", None)
    if paid_exp is None:
        return True
    return (
        isinstance(paid_exp, int)
        and not isinstance(paid_exp, bool)
        and paid_exp > now
    )


def _activation_identity(
    client_activate: Any,
    ui_token: Optional[str],
) -> Optional[tuple[bytes, bytes, int, int]]:
    """Return one versioned, non-reversible credential/device authority snapshot."""
    try:
        snapshot = client_activate.activation_identity_snapshot(ui_token)
    except Exception:  # noqa: BLE001
        return None
    return _activation_identity_from_snapshot(snapshot)


def _activation_identity_from_snapshot(
    snapshot: Any,
) -> Optional[tuple[bytes, bytes, int, int]]:
    """Hash one already-atomic client identity snapshot for process state."""
    try:
        from .client_activate import ActivationIdentitySnapshot
    except Exception:  # noqa: BLE001
        return None
    if type(snapshot) is not ActivationIdentitySnapshot:
        return None
    try:
        fields = object.__getattribute__(snapshot, "__dict__")
    except Exception:  # noqa: BLE001
        return None
    if type(fields) is not dict or dict.__len__(fields) != 4:
        return None
    keys = list(dict.keys(fields))
    if (
        any(type(key) is not str for key in keys)
        or frozenset(keys) != _ACTIVATION_IDENTITY_SNAPSHOT_KEYS
    ):
        return None
    token = dict.get(fields, "token")
    device = dict.get(fields, "device")
    revision = dict.get(fields, "revision")
    clear_epoch = dict.get(fields, "clear_epoch")
    if (
        type(token) is not str
        or len(token) > _ACTIVATION_IDENTITY_TOKEN_MAX_CHARS
        or type(device) is not str
        or not device
        or len(device) > _ACTIVATION_IDENTITY_DEVICE_MAX_CHARS
        or type(revision) is not int
        or not 0 <= revision <= _ACTIVATION_IDENTITY_COUNTER_MAX
        or revision % 2
        or type(clear_epoch) is not int
        or not 0 <= clear_epoch <= _ACTIVATION_IDENTITY_COUNTER_MAX
    ):
        return None
    try:
        token_bytes = str.encode(token, "utf-8", "strict")
        device_bytes = str.encode(device, "utf-8", "strict")
        return (
            hashlib.sha256(
                b"bidking-activation-credential-v1\0" + token_bytes
            ).digest(),
            hashlib.sha256(
                b"bidking-activation-device-v1\0" + device_bytes
            ).digest(),
            revision,
            clear_epoch,
        )
    except Exception:  # noqa: BLE001 - identity projection is a fail-closed boundary
        return None


def _same_activation_identity(
    left: Optional[tuple[bytes, bytes, int, int]],
    right: Optional[tuple[bytes, bytes, int, int]],
) -> bool:
    if left is None or right is None:
        return False
    return hmac.compare_digest(left[0], right[0]) and hmac.compare_digest(left[1], right[1])


def _same_activation_snapshot(
    left: Optional[tuple[bytes, bytes, int, int]],
    right: Optional[tuple[bytes, bytes, int, int]],
) -> bool:
    return (
        _same_activation_identity(left, right)
        and left is not None
        and right is not None
        and left[2] == right[2]
        and left[3] == right[3]
    )


def _observe_pet_usage_request_authority_locked(
    identity: Optional[tuple[bytes, bytes, int, int]],
) -> Optional[tuple[bytes, bytes, int, int, int]]:
    """Bind Pet work to one irreversible credential/device observation."""
    global _PET_USAGE_IDENTITY_GENERATION
    global _LAST_OBSERVED_PET_USAGE_IDENTITY
    if not _same_activation_snapshot(
        identity,
        _LAST_OBSERVED_PET_USAGE_IDENTITY,
    ):
        if identity is not None or _LAST_OBSERVED_PET_USAGE_IDENTITY is not None:
            _PET_USAGE_IDENTITY_GENERATION += 1
        _LAST_OBSERVED_PET_USAGE_IDENTITY = identity
    if identity is None:
        return None
    return (
        identity[0],
        identity[1],
        identity[2],
        identity[3],
        _PET_USAGE_IDENTITY_GENERATION,
    )


def _same_pet_usage_request_authority(left: Any, right: Any) -> bool:
    if (
        type(left) is not tuple
        or type(right) is not tuple
        or len(left) != 5
        or len(right) != 5
        or type(left[0]) is not bytes
        or type(left[1]) is not bytes
        or type(right[0]) is not bytes
        or type(right[1]) is not bytes
        or any(type(value) is not int or value < 0 for value in left[2:])
        or any(type(value) is not int or value < 0 for value in right[2:])
    ):
        return False
    return (
        hmac.compare_digest(left[0], right[0])
        and hmac.compare_digest(left[1], right[1])
        and left[2:] == right[2:]
    )


def _product_authority_fence_locked(
    client_activate: Any,
) -> Optional[tuple[bytes, bytes, int, int, int]]:
    """Return one opaque fence while ``_LOCK`` protects process authority."""
    if _activation_runtime_denied():
        return None
    identity = _activation_identity(client_activate, None)
    generation = _ACTIVATION_REQUEST_GENERATION
    if (
        identity is None
        or type(generation) is not int
        or not 0 <= generation <= _ACTIVATION_IDENTITY_COUNTER_MAX
    ):
        return None
    return (
        identity[0],
        identity[1],
        identity[2],
        identity[3],
        generation,
    )


def _same_product_authority_fence(left: Any, right: Any) -> bool:
    """Compare only exact built-in fence fields without invoking user protocols."""
    return _same_pet_usage_request_authority(left, right)


def _is_graceable_heartbeat_failure(result: Any) -> bool:
    if result is None or getattr(result, "activated", None) is True:
        return False
    reason = getattr(result, "reason", None)
    failures = getattr(result, "transport_failure_classes", None)
    summary = getattr(result, "transport_summary", None)
    if (
        type(reason) is not str
        or reason != "offline"
        or type(failures) is not tuple
        or not failures
        or any(
            type(value) is not str
            or value not in _GRACEABLE_NETWORK_FAILURE_CLASSES
            for value in failures
        )
        or summary is None
    ):
        return False
    attempt_count = getattr(summary, "attempt_count", None)
    endpoint_count = getattr(summary, "endpoint_count", None)
    stage_counts = getattr(summary, "stage_counts", None)
    all_endpoints_failed = getattr(summary, "all_endpoints_failed", None)
    if (
        type(attempt_count) is not int
        or type(endpoint_count) is not int
        or attempt_count <= 0
        or endpoint_count <= 0
        or attempt_count != endpoint_count
        or type(all_endpoints_failed) is not bool
        or all_endpoints_failed is not True
        or type(stage_counts) is not tuple
    ):
        return False
    counts = {stage: 0 for stage in _SAFE_TRANSPORT_STAGES}
    seen_stages: set[str] = set()
    for row in stage_counts:
        if (
            type(row) is not tuple
            or len(row) != 2
            or type(row[0]) is not str
            or row[0] not in counts
            or row[0] in seen_stages
            or type(row[1]) is not int
            or row[1] < 0
        ):
            return False
        seen_stages.add(row[0])
        counts[row[0]] = row[1]
    return (
        seen_stages == set(_SAFE_TRANSPORT_STAGES)
        and counts["http"] == 0
        and sum(counts.values()) == attempt_count
    )


def _has_recent_online_grant_locked(
    identity: Optional[tuple[bytes, bytes, int, int]],
    *,
    monotonic_now: float,
) -> bool:
    confirmed_at = _LAST_ONLINE_CONFIRMED_AT
    return (
        _LAST_ONLINE_GRANT is not None
        and _same_activation_identity(identity, _LAST_ONLINE_IDENTITY)
        and type(confirmed_at) is float
        and type(monotonic_now) is float
        and 0.0 <= monotonic_now - confirmed_at
        <= _ACTIVATION_ONLINE_GRANT_RECENCY_SECONDS
    )


def _clear_process_grant_locked() -> None:
    global _LAST_ONLINE_GRANT, _LAST_ONLINE_IDENTITY, _LAST_ONLINE_CONFIRMED_AT
    global _LAST_PET_USAGE_AUTHORITY
    global _LAST_PET_USAGE_AUTHORITY_SCHEMA
    global _HEARTBEAT_FAILURE_COUNT, _HEARTBEAT_FAILURE_STARTED_AT
    _LAST_ONLINE_GRANT = None
    _LAST_ONLINE_IDENTITY = None
    _LAST_ONLINE_CONFIRMED_AT = None
    _LAST_PET_USAGE_AUTHORITY = None
    _LAST_PET_USAGE_AUTHORITY_SCHEMA = 1
    _HEARTBEAT_FAILURE_COUNT = 0
    _HEARTBEAT_FAILURE_STARTED_AT = None


def _fail_closed_identity_result(result: Any) -> Any:
    """Keep an existing failure, but never expose a success for a changed identity."""
    persistent_reason = None
    try:
        from . import client_activate

        persistent_reason = client_activate.persistent_authoritative_deny_reason()
    except Exception:  # noqa: BLE001
        pass
    if persistent_reason is not None:
        try:
            from .client_activate import ActivationResult

            return ActivationResult(
                activated=False,
                mode=(
                    "paid"
                    if persistent_reason in {"revoked", "device_revoked"}
                    else "none"
                ),
                reason=persistent_reason,
                source="server",
            )
        except Exception:  # noqa: BLE001
            pass
    if result is not None and not bool(getattr(result, "activated", False)):
        return result
    try:
        from .client_activate import ActivationResult

        return ActivationResult(
            activated=False,
            mode="none",
            reason="state_error",
            source="server",
        )
    except Exception:  # noqa: BLE001
        class _IdentityChanged:
            activated = False
            mode = "none"
            reason = "state_error"
            source = "server"
            trial_exp = None
            transport_failure_classes: tuple[str, ...] = ()

            def trial_seconds_left(self, now=None):  # noqa: ANN001, D401
                return None

            def status_line(self, now=None):  # noqa: ANN001, D401
                return "激活: 身份状态已变化，请稍后重试"

        return _IdentityChanged()


def _activation_runtime_denied() -> bool:
    """Consume the same no-fail revocation latch as the low-level provider."""
    try:
        from . import client_activate

        return client_activate.activation_runtime_denied() is True
    except Exception:  # noqa: BLE001
        # If the authority module itself cannot be read, no product-facing
        # surface may retain a previously successful process grant.
        return True


def persistent_authoritative_deny_state(
    *,
    now: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    """Project a persisted deny before the first network activation finishes."""
    try:
        from . import client_activate
        from .client_activate import ActivationResult

        reason = client_activate.persistent_authoritative_deny_reason()
        if reason is None:
            return None
        result = ActivationResult(
            activated=False,
            mode=("paid" if reason in {"revoked", "device_revoked"} else "none"),
            reason=reason,
            source="server",
        )
        return activation_state(result, now=now)
    except Exception:  # noqa: BLE001
        # A malformed/unreadable marker is itself a persistent fail-closed state.
        return {
            "run_full": False,
            "mode": "none",
            "reason": "state_error",
            "seconds_left": None,
            "needs_token": False,
            "warn": True,
            "text": "服务器激活服务暂时异常 · 请稍后重试",
        }


def pet_usage_request_authority_snapshot(
) -> Optional[tuple[bytes, bytes, int, int, int]]:
    """Return an opaque, versioned authority fence for queued Pet work."""
    try:
        from . import client_activate
    except Exception:  # noqa: BLE001
        return None
    with _LOCK:
        if _activation_runtime_denied():
            return None
        identity = _activation_identity(client_activate, None)
        return _observe_pet_usage_request_authority_locked(identity)


def product_authority_fence_snapshot(
) -> Optional[tuple[bytes, bytes, int, int, int]]:
    """Bind queued product work to one process request generation and identity."""
    try:
        from . import client_activate
    except Exception:  # noqa: BLE001
        return None
    with _LOCK:
        return _product_authority_fence_locked(client_activate)


def product_authority_fence_is_current(expected: Any) -> bool:
    """Return true only while an opaque queued-work fence is still current."""
    try:
        from . import client_activate
    except Exception:  # noqa: BLE001
        return False
    with _LOCK:
        current = _product_authority_fence_locked(client_activate)
        return _same_product_authority_fence(expected, current)


def _is_local_candidate_failure(result: Any) -> bool:
    """Keep current local feedback without changing persisted deny authority."""
    reason = getattr(result, "reason", None)
    return (
        getattr(result, "activated", None) is False
        and getattr(result, "source", None) == "client"
        and type(reason) is str
        and reason in {
            "local_commit_failed", "local_identity_changed", "local_activation_state_error",
        }
    )


def _is_current_server_rejection(result: Any) -> bool:
    """Recognize this request's real rejection for display, never authorization."""
    from . import client_activate

    context = getattr(result, "_authoritative_rejection_context", None)
    reason = getattr(result, "reason", None)
    if (
        getattr(result, "activated", None) is not False
        or getattr(result, "source", None) != "server"
        or type(reason) is not str
        or reason not in client_activate._AUTHORITATIVE_DENY_REASONS
        or type(context) is not tuple or len(context) != 3
        or type(context[0]) is not int
        or context[0] != _ACTIVATION_REQUEST_GENERATION
    ):
        return False
    try:
        return client_activate._same_source_identity(
            context[1], client_activate.activation_identity_snapshot(context[2]),
        )
    except Exception:  # noqa: BLE001 - uncertain provenance uses persisted denial
        return False


def _effective_activation_snapshot(
    *,
    now: Optional[int] = None,
) -> tuple[Any, Any]:
    """Return (effective grant/result, current heartbeat failure during grace)."""
    epoch_now = int(time.time()) if now is None else int(now)
    monotonic_now = time.monotonic()
    with _LOCK:
        last = _LAST
        grant = _LAST_ONLINE_GRANT
        heartbeat = _LAST_HEARTBEAT
        failure_count = _HEARTBEAT_FAILURE_COUNT
        failure_started_at = _HEARTBEAT_FAILURE_STARTED_AT
        if _activation_runtime_denied():
            if _is_local_candidate_failure(last) or _is_current_server_rejection(last):
                return last, None
            return _fail_closed_identity_result(last), None
    if (
        last is not grant
        or grant is None
        or heartbeat is None
        or failure_started_at is None
        or failure_count < 1
        or failure_count > _ACTIVATION_TRANSPORT_GRACE_FAILURES
    ):
        return last, None
    # Local paid/trial/lease expiry always wins; transport grace cannot extend it.
    if not _strictly_time_valid_process_grant(grant, now=epoch_now):
        return grant, None
    if monotonic_now - failure_started_at >= _ACTIVATION_TRANSPORT_GRACE_SECONDS:
        return heartbeat, None
    return grant, heartbeat


def startup_activate(*, ui_token: Optional[str] = None, timeout: float = 4.0,
                     force_server: bool = False,
                     pet_usage_event: Optional[dict[str, Any]] = None,
                     preserve_main_on_pet_transport_failure: bool = False,
                     expected_pet_usage_authority: Any = None,
                     card_token: Optional[str] = None,
                     ) -> Any:
    """启动激活: 网络/解析先返回candidate，最终身份门通过后再提交provider/cache。任何异常 -> 降级结果。
    在线成功 grant 与线路心跳分开保存，供 should_run_full()/activation_state() 无参调用。
    force_server: 周期心跳传 True(直连服务端"花掉"试用使用时长 + 捡撤销/付费)。"""
    global _LAST, _LAST_ONLINE_GRANT, _LAST_ONLINE_IDENTITY
    global _LAST_ONLINE_CONFIRMED_AT, _LAST_HEARTBEAT
    global _HEARTBEAT_FAILURE_COUNT, _HEARTBEAT_FAILURE_STARTED_AT
    global _ACTIVATION_REQUEST_GENERATION
    identity = None
    source_identity_snapshot = None
    client_activate = None
    activation_candidate = None
    request_generation = 0
    request_identity_revision = 0
    request_clear_epoch = 0
    request_had_recent_online_grant = False
    transport_trace: tuple[tuple[str, str, str, int], ...] = ()
    history_update: Optional[tuple[Any, bool, bool]] = None
    pet_usage_request_is_current = True
    try:
        from . import client_activate
        with _LOCK:
            try:
                source_identity_snapshot = (
                    client_activate.activation_identity_snapshot(ui_token)
                )
            except Exception:  # noqa: BLE001
                source_identity_snapshot = None
            identity = _activation_identity_from_snapshot(
                source_identity_snapshot
            )
            request_had_recent_online_grant = (
                _has_recent_online_grant_locked(
                    identity,
                    monotonic_now=float(time.monotonic()),
                )
            )
            pet_usage_authority = (
                _observe_pet_usage_request_authority_locked(identity)
            )
            pet_usage_request_is_current = (
                expected_pet_usage_authority is None
                or _same_pet_usage_request_authority(
                    expected_pet_usage_authority,
                    pet_usage_authority,
                )
            )
            if pet_usage_request_is_current:
                _ACTIVATION_REQUEST_GENERATION += 1
                request_generation = _ACTIVATION_REQUEST_GENERATION
                request_identity_revision = (
                    identity[2] if identity is not None else -1
                )
                request_clear_epoch = (
                    identity[3] if identity is not None else -1
                )
                # A credential/device change is a security boundary, not a
                # failure. Revoke the old grant before starting network I/O.
                if (
                    _LAST_ONLINE_GRANT is not None
                    and not _same_activation_identity(
                        identity,
                        _LAST_ONLINE_IDENTITY,
                    )
                ):
                    _LAST = None
                    _LAST_HEARTBEAT = None
                    _clear_process_grant_locked()
        if not pet_usage_request_is_current:
            return None
        try:
            client_activate.consume_activation_transport_trace()
        except Exception:  # noqa: BLE001 - diagnostics never affect activation
            pass
        activation_attempt = client_activate.activate(
            ui_token=ui_token,
            timeout=timeout,
            force_server=force_server,
            defer_commit=True,
            pet_usage_event=pet_usage_event,
            expected_identity=(
                source_identity_snapshot
                if pet_usage_event is not None or card_token is not None
                else None
            ),
            **({"card_token": card_token} if card_token is not None else {}),
        )
        if isinstance(
            activation_attempt,
            getattr(client_activate, "ActivationCandidate", ()),
        ):
            activation_candidate = activation_attempt
            res = activation_attempt.result
        else:
            # Compatibility seam for focused tests and older plugin activators.
            res = activation_attempt
    except Exception as exc:  # noqa: BLE001  激活绝不拖垮启动
        try:
            from .client_activate import ActivationResult
            res = ActivationResult(activated=False, mode="none", reason=f"error:{type(exc).__name__}")
        except Exception:  # noqa: BLE001  连结果类都导不进 -> 最简降级对象
            class _Degraded:
                activated = False
                mode = "none"
                reason = "error"
                trial_exp = None

                def trial_seconds_left(self, now=None):  # noqa: ANN001, D401
                    return None

                def status_line(self, now=None):  # noqa: ANN001, D401
                    return "激活: 异常降级"
            res = _Degraded()
    if client_activate is not None:
        try:
            transport_trace = client_activate.consume_activation_transport_trace()
        except Exception:  # noqa: BLE001 - diagnostics never affect activation
            transport_trace = ()
    post_identity = (
        _activation_identity(client_activate, ui_token)
        if client_activate is not None
        else None
    )
    identity_commit_guard = (
        client_activate.activation_identity_commit_guard()
        if client_activate is not None
        else contextlib.nullcontext()
    )
    with _LOCK, identity_commit_guard:
        # Read authority again *inside* the final short lock.  The earlier
        # post-call read is useful evidence, but cannot authorize a commit
        # after an intervening token/device write.
        commit_identity = (
            _activation_identity(client_activate, ui_token)
            if client_activate is not None
            else None
        )
        identity_values_stable = (
            _same_activation_identity(identity, post_identity)
            and _same_activation_identity(identity, commit_identity)
        )
        post_commit_snapshot_stable = _same_activation_snapshot(
            post_identity,
            commit_identity,
        )
        commit_revision = commit_identity[2] if commit_identity is not None else -1
        commit_clear_epoch = commit_identity[3] if commit_identity is not None else -1
        # The real client persists a newly validated UI token in one versioned
        # identity write (+2: odd in-progress, then even stable). Test/plugin
        # activators may return success without persistence, so +0 is also
        # valid. Any other revision movement, including A -> B -> A, is stale.
        expected_ui_token_write = (
            activation_candidate is None
            and bool(ui_token and ui_token.strip())
            and bool(getattr(res, "activated", False))
            and commit_revision == request_identity_revision + 2
        )
        revision_stable = (
            commit_revision == request_identity_revision
            or expected_ui_token_write
        )
        clear_epoch_stable = commit_clear_epoch == request_clear_epoch
        identity_stable = (
            identity_values_stable
            and post_commit_snapshot_stable
            and revision_stable
            and clear_epoch_stable
        )
        generation_is_current = (
            request_generation == _ACTIVATION_REQUEST_GENERATION
        )
        request_is_current = generation_is_current and identity_stable
        if not identity_stable or not request_is_current:
            res = (
                client_activate._candidate_commit_failure(
                    activation_candidate, stage="identity", recovery="not_started",
                    reason="local_identity_changed",
                )
                if activation_candidate is not None else _fail_closed_identity_result(res)
            )
        # A newer refresh owns process state.  Do not let an older network
        # response (including A -> B -> A ABA) overwrite its grant or heartbeat.
        if not request_is_current:
            if generation_is_current:
                # No newer request owns the UI, but authority changed after
                # this one started.  Publish a stable fail-closed view instead
                # of leaving current() as an ambiguous None.
                _LAST = res
                _LAST_HEARTBEAT = res
                _clear_process_grant_locked()
            return res
        # Reject metadata rollback before any cache/provider/upload side effect.
        # Credential/device hash keys separate aliases; canonical UID must not
        # change underneath the same credential's previously observed member.
        member = getattr(res, "membership", None)
        floor_key = (identity[0], identity[1]) if identity is not None else None
        previous_member = _MEMBERSHIP_REVISION_FLOORS.get(floor_key) if floor_key is not None else None
        canonical_revision_floor = max((seen[1] for key, seen in _MEMBERSHIP_REVISION_FLOORS.items()
                                        if floor_key is not None and key[1] == floor_key[1]
                                        and type(member) is dict and seen[0] == member.get("canonical_uid")), default=0)
        if getattr(res, "activated", False) is True and previous_member is not None and (
            type(member) is not dict or member.get("canonical_uid") != previous_member[0]
            or type(member.get("revision")) is not int or member["revision"] < previous_member[1]
        ):
            res = client_activate.ActivationResult(activated=False, mode="paid", source="server",
                                                   reason="membership_revision_rollback")
            activation_candidate = None
        elif getattr(res, "activated", False) is True and type(member) is dict and member["revision"] < canonical_revision_floor:
            res = client_activate.ActivationResult(activated=False, mode="paid", source="server",
                                                   reason="membership_revision_rollback")
            activation_candidate = None
        if activation_candidate is not None:
            plan = activation_candidate.commit_plan
            if (
                activation_candidate._authoritative_rejection is True
                and plan is not None and plan.kind == "authoritative_clear"
                and res is activation_candidate.result
                and res.activated is False and res.source == "server"
                and type(res.reason) is str
                and res.reason in client_activate._AUTHORITATIVE_DENY_REASONS
            ):
                # The generation/identity gate above has already accepted this
                # parsed response. Keep its feedback while persistent denial
                # remains authoritative for all runtime access and restarts.
                res._authoritative_rejection_context = (
                    request_generation, activation_candidate.identity_snapshot,
                    activation_candidate.ui_token,
                )
            try:
                commit_outcome = client_activate.commit_activation_candidate(
                    activation_candidate
                )
            except Exception:  # noqa: BLE001
                # Defense in depth: candidate commit is designed not to raise,
                # but an injected/provider/filesystem failure must still leave
                # every runtime authorization surface fail-closed.
                try:
                    client_activate.force_activation_runtime_fail_closed()
                except Exception:  # noqa: BLE001
                    pass
                res = client_activate._candidate_state_error(activation_candidate)
                _LAST = res
                _LAST_HEARTBEAT = res
                _clear_process_grant_locked()
                return res
            res = commit_outcome.result
            committed_identity = _activation_identity_from_snapshot(
                commit_outcome.identity_snapshot
            )
            if (
                not commit_outcome.committed
                or not _same_activation_identity(identity, committed_identity)
            ):
                if not (_is_local_candidate_failure(res) or _is_current_server_rejection(res)):
                    res = _fail_closed_identity_result(res)
                _LAST = res
                _LAST_HEARTBEAT = res
                _clear_process_grant_locked()
                return res
            commit_identity = committed_identity
            identity_stable = True
        if getattr(res, "activated", False) is True and type(member) is dict and floor_key is not None:
            _MEMBERSHIP_REVISION_FLOORS[floor_key] = (member["canonical_uid"], member["revision"])
        res = _guard_pet_usage_revision(
            res,
            # Pet authority follows the credential/device identity, not the
            # irreversible snapshot revision used to reject stale/ABA network
            # responses above. A successful UI-token persistence write may
            # advance that revision while leaving the actual authority
            # unchanged; treating it as a new Pet identity would reset the
            # server-time rollback floor on every refresh.
            same_identity=_same_activation_identity(
                commit_identity,
                _LAST_ONLINE_IDENTITY,
            ),
            now=int(time.time()),
            max_epoch=client_activate.MAX_SAFE_ACTIVATION_EPOCH,
            result_type=client_activate.ActivationResult,
            contract_projector=client_activate.project_normalized_pet_contract,
        )
        if (
            card_token is not None and getattr(res, "activated", False) is not True
            and not _is_current_server_rejection(res) and not _is_local_candidate_failure(res)
            and _strictly_time_valid_process_grant(_LAST_ONLINE_GRANT, now=int(time.time()))
            and _same_activation_snapshot(commit_identity, _LAST_ONLINE_IDENTITY)
        ):
            # Card operation feedback is returned separately from current A.
            # No grace expansion, cache recovery, credit or token replacement.
            return res
        if (
            pet_usage_event is not None
            and preserve_main_on_pet_transport_failure
            and _is_graceable_heartbeat_failure(res)
            and _strictly_time_valid_process_grant(
                _LAST_ONLINE_GRANT,
                now=int(time.time()),
            )
            and _same_activation_snapshot(
                commit_identity,
                _LAST_ONLINE_IDENTITY,
            )
        ):
            # A metering event is a Pet-only side channel.  A network failure
            # keeps that event pending for retry, but cannot revoke a still
            # valid calculator grant.  Authoritative HTTP denials do not enter
            # this branch and therefore still revoke immediately.
            return res
        _LAST_HEARTBEAT = res
        now = int(time.time())
        completion_monotonic = float(time.monotonic())
        if not identity_stable:
            _LAST = res
            _clear_process_grant_locked()
        elif (
            _strictly_time_valid_process_grant(res, now=now)
            and identity is not None
        ):
            _LAST = res
            _LAST_ONLINE_GRANT = res
            _LAST_ONLINE_IDENTITY = commit_identity
            _LAST_ONLINE_CONFIRMED_AT = completion_monotonic
            _HEARTBEAT_FAILURE_COUNT = 0
            _HEARTBEAT_FAILURE_STARTED_AT = None
        elif (
            force_server
            and not (ui_token and ui_token.strip())
            and _is_graceable_heartbeat_failure(res)
            and request_had_recent_online_grant
            and _has_recent_online_grant_locked(
                commit_identity,
                monotonic_now=completion_monotonic,
            )
            and _strictly_time_valid_process_grant(_LAST_ONLINE_GRANT, now=now)
            and _same_activation_snapshot(commit_identity, _LAST_ONLINE_IDENTITY)
            and _LAST is _LAST_ONLINE_GRANT
        ):
            if _HEARTBEAT_FAILURE_COUNT == 0:
                _HEARTBEAT_FAILURE_STARTED_AT = completion_monotonic
            _HEARTBEAT_FAILURE_COUNT += 1
            within_grace = (
                _HEARTBEAT_FAILURE_STARTED_AT is not None
                and completion_monotonic - _HEARTBEAT_FAILURE_STARTED_AT
                < _ACTIVATION_TRANSPORT_GRACE_SECONDS
                and _HEARTBEAT_FAILURE_COUNT <= _ACTIVATION_TRANSPORT_GRACE_FAILURES
            )
            if not within_grace:
                _LAST = res
                _clear_process_grant_locked()
        else:
            _LAST = res
            _clear_process_grant_locked()
        if pet_usage_event is None and transport_trace:
            history_update = (
                transport_trace,
                True,
                any(
                    type(row) is tuple
                    and len(row) == 4
                    and row[1] == "http"
                    for row in transport_trace
                ),
            )
    if history_update is not None and client_activate is not None:
        try:
            trace, allow_new_sequence, recovered = history_update
            client_activate.record_activation_recheck_outcome(
                trace,
                allow_new_sequence=allow_new_sequence,
                recovered=recovered,
            )
        except Exception:  # noqa: BLE001 - diagnostics never affect activation
            pass
    return res


# refresh 是 startup_activate 的别名语义(周期性重连 / token 提交后重激活都走它)。
def refresh_activation(*, ui_token: Optional[str] = None, timeout: float = 4.0) -> Any:
    """周期性心跳 / token 提交后重激活, 刷新 _LAST。force_server=True: 直连服务端
    (试用按使用时长累计需每拍真到达服务端"花掉"时间; 也更快捡撤销/付费变更)。"""
    return startup_activate(ui_token=ui_token, timeout=timeout, force_server=True)


def redeem_membership_card(*, card_token: str, timeout: float = 4.0) -> Any:
    """Redeem B against the current credential A; never implicitly switch to B.

    Return operation feedback (``card_redemption``) while ``current()`` retains
    a still-valid A after switch-required/confirmation-pending/transport errors.
    A confirmed switch uses the existing ``refresh_activation(ui_token=B)``.
    """
    from .client_activate import ActivationResult, is_single_activation_token_input
    if not is_single_activation_token_input(card_token):
        return ActivationResult(activated=False, mode="paid", reason="invalid_card_request", source="client")
    return startup_activate(card_token=card_token, timeout=timeout, force_server=True)


def refresh_pet_usage(
    pet_usage_event: dict[str, Any],
    *,
    timeout: float = 4.0,
    expected_authority: Any = None,
) -> Any:
    """Submit one server-authoritative Pet visibility event.

    Network failures leave the event retryable without revoking a valid main
    calculator grant.  Signed success and authoritative denials still pass
    through the normal generation, identity, provider and A2 authority gates.
    """
    return startup_activate(
        timeout=timeout,
        force_server=True,
        pet_usage_event=pet_usage_event,
        preserve_main_on_pet_transport_failure=True,
        expected_pet_usage_authority=expected_authority,
    )


def clear_activation() -> dict:
    """换绑/清除: 清本机 token+缓存(+本进程 env token), 然后重激活刷新 _LAST(无 token → 试用/降级)。
    UI"换绑"按钮调它, 清除后用户可粘贴新 token。返回 client_activate.clear_activation() 的 info
    (含 env_was_set, 供 UI 提示"环境变量 token 已清本进程, 若仍失败请重启")。绝不抛。"""
    global _LAST, _LAST_HEARTBEAT
    global _ACTIVATION_REQUEST_GENERATION
    info: dict = {"cleared": False, "env_was_set": False}
    client_activate = None
    runtime_deny_revision = None
    try:
        from . import client_activate
    except Exception:  # noqa: BLE001
        pass
    identity_guard = (
        client_activate.activation_identity_commit_guard()
        if client_activate is not None
        else contextlib.nullcontext()
    )
    # Invalidate in-flight responses and the low-level provider before token,
    # env, cache, or network work.  Lock order matches startup_activate:
    # enforce request state first, then the client identity authority.
    with _LOCK, identity_guard:
        _ACTIVATION_REQUEST_GENERATION += 1
        _LAST = None
        _LAST_HEARTBEAT = None
        _clear_process_grant_locked()
        if client_activate is not None:
            try:
                runtime_deny_revision = (
                    client_activate.begin_activation_identity_clear()
                )
            except Exception:  # noqa: BLE001
                try:
                    client_activate.force_activation_runtime_fail_closed()
                except Exception:  # noqa: BLE001
                    pass
    try:
        if client_activate is not None:
            info = client_activate.clear_activation(
                _runtime_deny_revision=runtime_deny_revision,
            )
    except Exception:  # noqa: BLE001
        pass
    # 清除后重激活(force_server, 无 token → 试用/降级), 更新 _LAST 供 UI 立即反映清除后的态。
    try:
        startup_activate(force_server=True)
    except Exception:  # noqa: BLE001
        pass
    return info


def current() -> Any:
    """进程内当前有效结果；短宽限超时后立即返回心跳失败。"""
    result, _heartbeat = _effective_activation_snapshot()
    return result


def activation_banner(result: Any) -> str:
    """启动日志横幅一行(给 [activation] 前缀用)。"""
    try:
        return result.status_line()
    except Exception:  # noqa: BLE001
        return "激活: 状态未知"


def is_transient_activation_failure(result: Any) -> bool:
    """只把可恢复的连接/本地异常列为快速重试；到期、撤销、坏 token 等权威拒绝不打快拍。"""
    if result is None or bool(getattr(result, "activated", False)):
        return result is None
    reason = str(getattr(result, "reason", "") or "")
    return reason in {
        "offline",
        "no_server",
        "pending",
        "state_error",
        "server_error",
    } or reason.startswith("error")


def activation_refresh_delay(result: Any, consecutive_transient_failures: int = 0) -> float:
    """返回下一次服务端激活的等待秒数，供 UI 与 Npcap 两个进程共享同一退避契约。"""
    if not is_transient_activation_failure(result):
        return _ACTIVATION_HEARTBEAT_SECONDS
    failure_count = max(1, int(consecutive_transient_failures or 0))
    index = min(failure_count - 1, len(_ACTIVATION_TRANSIENT_RETRY_SECONDS) - 1)
    return _ACTIVATION_TRANSIENT_RETRY_SECONDS[index]


def should_run_full(result: Any = None, now: Optional[int] = None) -> bool:
    """是否放行完整功能(出价 + 各英雄计算 + 解密 moat)。【时间感知】: 试用即使启动时 activated,
    到期(trial_seconds_left<=0)也立即降级。result 省略则用进程内 _LAST。

    付费(paid): 当前进程已有在线成功结果时按会员日历到期放行；冷启动不从磁盘恢复。
    试用(trial): activated 且 剩余 > 0 才放行。
    其它(none/offline/expired): 不放行。
    """
    if _activation_runtime_denied():
        return False
    r = result if result is not None else _effective_activation_snapshot(now=now)[0]
    if r is None:
        return False
    if not bool(getattr(r, "activated", False)):
        return False
    if getattr(r, "mode", "") == "trial":
        try:
            left = r.trial_seconds_left(now)
        except Exception:  # noqa: BLE001
            left = None
        return left is not None and left > 0
    if getattr(r, "mode", "") == "paid" and getattr(r, "paid_exp", None) is not None:
        # 订阅: 日历到期即降级(离线本地兜底; 在线时服务端回 expired)。历史无期限授权 paid_exp=None 继续兼容。
        try:
            left = r.paid_seconds_left(now)
        except Exception:  # noqa: BLE001
            left = None
        return left is not None and left > 0
    return True


def _validated_entitlement_state(
    result: Any,
    *,
    run_full: bool,
    mode: str,
    now: int,
) -> dict:
    """Expose only a complete, internally consistent entitlement pair to UI."""
    if not run_full:
        return {}
    plan_kind = getattr(result, "plan_kind", None)
    features = getattr(result, "feature_entitlements", None)
    usage = getattr(result, "feature_usage", None)
    if usage is not None:
        if (
            type(features) is not dict
            or type(usage) is not dict
            or any(type(key) is not str for key in dict.keys(features))
            or any(type(key) is not str for key in dict.keys(usage))
            or frozenset(dict.keys(features)) != {"pet_companion"}
            or frozenset(dict.keys(usage)) != {"pet_companion"}
            or type(features.get("pet_companion")) is not str
            or type(usage.get("pet_companion")) is not dict
        ):
            return {}
        pet = usage["pet_companion"]
        kind = pet.get("kind")
        status = pet.get("status")
        if type(kind) is not str or type(status) is not str:
            return {}
        if features["pet_companion"] != kind:
            return {}
        if type(plan_kind) is not str:
            pet_keys = list(dict.keys(pet))
            server_now = pet.get("server_now")
            if (
                plan_kind is not None
                or kind != "none"
                or status != "state_error"
                or any(type(key) is not str for key in pet_keys)
                or frozenset(pet_keys) != {"kind", "status", "server_now"}
                or type(server_now) is not int
                or server_now <= 0
            ):
                return {}
            return {
                "plan_kind": None,
                "feature_entitlements": {"pet_companion": "none"},
                "feature_usage": {"pet_companion": dict(pet)},
            }
        metadata = {
            "feature_usage_schema_version": getattr(result, "feature_usage_schema_version", 1),
            "membership": getattr(result, "membership", None),
            "membership_card_capability": getattr(result, "membership_card_capability", None),
        }
        member = metadata["membership"]
        if type(member) is dict and type(member.get("plan_exp")) is int:
            metadata["membership_plan_refresh_required"] = now >= member["plan_exp"]
        entitlement_exp = pet.get("entitlement_exp")
        if kind in {"full", "quota", "trial"} and type(entitlement_exp) is int and entitlement_exp <= now:
            return {
                "plan_kind": plan_kind, **metadata,
                "feature_entitlements": {"pet_companion": "none"},
                "feature_usage": {"pet_companion": {"kind": "none", "status": "expired", "server_now": now}},
            }
        if status == "active" and kind in {"trial", "quota"}:
            visibility_lease_exp = pet.get("visibility_lease_exp")
            if (
                type(visibility_lease_exp) is not int
                or visibility_lease_exp <= now
            ):
                return {
                    "plan_kind": plan_kind,
                    **metadata,
                    "feature_entitlements": {"pet_companion": "none"},
                    "feature_usage": {
                        "pet_companion": {
                            "kind": "none",
                            "status": "state_error",
                            "server_now": now,
                        }
                    },
                }
        return {
            "plan_kind": plan_kind,
            **metadata,
            "feature_entitlements": {"pet_companion": kind},
            "feature_usage": {"pet_companion": dict(pet)},
        }
    if not isinstance(plan_kind, str) or plan_kind not in _PLAN_PET_ACCESS:
        return {}
    if not isinstance(features, dict):
        return {}
    pet_access = features.get("pet_companion")
    if pet_access != _PLAN_PET_ACCESS[plan_kind]:
        return {}
    paid_exp = getattr(result, "paid_exp", None)
    if mode == "trial" and plan_kind != "trial":
        return {}
    if mode == "paid":
        if plan_kind == "trial":
            return {}
        if (plan_kind in _PERMANENT_PLAN_KINDS) != (paid_exp is None):
            return {}
    return {
        "plan_kind": plan_kind,
        "feature_entitlements": {"pet_companion": pet_access},
    }


def _pet_usage_view(result: Any) -> Optional[dict[str, Any]]:
    usage = getattr(result, "feature_usage", None)
    if (
        type(usage) is not dict
        or any(type(key) is not str for key in dict.keys(usage))
        or frozenset(dict.keys(usage)) != {"pet_companion"}
    ):
        return None
    pet = usage.get("pet_companion")
    return pet if type(pet) is dict else None


_PET_AUTHORITY_TEXT_FIELDS = frozenset(
    {"kind", "status", "entitlement_id"}
)
_PET_AUTHORITY_INT_FIELDS = frozenset(
    {
        "duration_days",
        "entitlement_exp",
        "last_seq",
        "remaining_seconds",
        "server_now",
        "total_seconds",
        "usage_revision",
        "visibility_lease_exp",
    }
)
_PET_AUTHORITY_BOOL_FIELDS = frozenset({"visible"})
_PET_AUTHORITY_METERED_KEYS = frozenset(
    {
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
)
_PET_AUTHORITY_METERED_STATUSES = frozenset(
    {
        "active",
        "available",
        "exhausted",
        "paused",
        "sequence_conflict",
        "session_conflict",
    }
)
_PET_AUTHORITY_MAX_SEQUENCE = (1 << 63) - 1
_ACTIVATION_RESULT_FIELD_NAMES = (
    "activated",
    "mode",
    "reason",
    "source",
    "trial_exp",
    "master_key",
    "lease_exp",
    "paid_exp",
    "plan_kind",
    "feature_entitlements",
    "feature_usage",
    "transport_failure_classes",
    "transport_summary",
)
_ACTIVATION_RESULT_FIELD_SET = frozenset(_ACTIVATION_RESULT_FIELD_NAMES)
_ACTIVATION_RESULT_EXTENSION_FIELDS = frozenset({"membership", "feature_usage_schema_version"})
_ACTIVATION_RESULT_DIAGNOSTIC_FIELDS = frozenset({
    "local_commit_stage", "local_commit_recovery", "_authoritative_rejection_context",
    "roaming_rebinds", "roaming_rebind_limit_reached",
    "membership_card_capability", "card_redemption",
})


def _activation_result_raw_items(
    result: Any,
    *,
    result_type: type,
    allow_dict_subclass: bool,
) -> Optional[list[tuple[Any, Any]]]:
    """Read the instance dictionary without invoking mapping/key protocols."""
    if type(result) is not result_type:
        return None
    try:
        fields = object.__getattribute__(result, "__dict__")
    except Exception:  # noqa: BLE001
        return None
    field_type = type(fields)
    if field_type is not dict:
        if not allow_dict_subclass:
            return None
        try:
            if not issubclass(field_type, dict):
                return None
        except TypeError:
            return None
    try:
        return list(dict.items(fields))
    except Exception:  # noqa: BLE001
        return None


def _project_activation_result_fields(
    result: Any,
    *,
    result_type: type,
) -> Optional[dict[str, Any]]:
    """Atomically copy the complete exact-key ActivationResult shape."""
    items = _activation_result_raw_items(
        result,
        result_type=result_type,
        allow_dict_subclass=False,
    )
    if items is None:
        return None
    projected: dict[str, Any] = {}
    for key, value in items:
        if type(key) is not str:
            return None
        # Optional display metadata is not part of the authority contract.
        # Never inspect its values or relax the required authority key set.
        if key in _ACTIVATION_RESULT_DIAGNOSTIC_FIELDS or key in _ACTIVATION_RESULT_EXTENSION_FIELDS:
            continue
        if key not in _ACTIVATION_RESULT_FIELD_SET:
            return None
        if key in projected:
            return None
        projected[key] = value
    if frozenset(projected) != _ACTIVATION_RESULT_FIELD_SET:
        return None
    return projected


def _recover_activation_result_fields(
    result: Any,
    *,
    result_type: type,
) -> Optional[dict[str, Any]]:
    """Recover values for a Pet-only error without calling hostile key methods."""
    items = _activation_result_raw_items(
        result,
        result_type=result_type,
        allow_dict_subclass=True,
    )
    if items is None:
        return None

    recovered: dict[str, Any] = {}
    deferred: list[tuple[str, Any]] = []
    for key, value in items:
        if type(key) is str:
            if key in (_ACTIVATION_RESULT_FIELD_SET | _ACTIVATION_RESULT_EXTENSION_FIELDS) and key not in recovered:
                recovered[key] = value
            continue
        key_type = type(key)
        try:
            is_str_subclass = issubclass(key_type, str)
        except TypeError:
            is_str_subclass = False
        if not is_str_subclass:
            continue
        for expected in _ACTIVATION_RESULT_FIELD_NAMES:
            try:
                matched = str.__eq__(key, expected)
            except Exception:  # noqa: BLE001
                matched = False
            if matched is True:
                deferred.append((expected, value))
                break

    for key, value in deferred:
        if key not in recovered:
            recovered[key] = value
    return recovered


def _project_pet_authority_dict(
    pet: Any,
    *,
    max_epoch: int,
    usage_schema: int = 1,
) -> Optional[dict[str, str | int | bool]]:
    """Copy one complete Pet view using exact built-ins and no value protocols."""
    if type(max_epoch) is not int or max_epoch <= 0:
        return None
    if type(pet) is not dict:
        return None
    keys = list(dict.keys(pet))
    if any(type(key) is not str for key in keys):
        return None
    projected: dict[str, str | int | bool] = {}
    for key in keys:
        value = dict.__getitem__(pet, key)
        if key in _PET_AUTHORITY_TEXT_FIELDS:
            if type(value) is not str:
                return None
        elif key in _PET_AUTHORITY_INT_FIELDS:
            if type(value) is not int:
                return None
        elif key in _PET_AUTHORITY_BOOL_FIELDS:
            if type(value) is not bool:
                return None
        else:
            return None
        projected[key] = value

    kind = projected.get("kind")
    status = projected.get("status")
    server_now = projected.get("server_now")
    minimal_keys = frozenset({"kind", "status", "server_now"})
    projected_keys = frozenset(keys)
    if type(kind) is not str or type(status) is not str:
        return None
    if (
        type(server_now) is not int
        or server_now <= 0
        or server_now > max_epoch
    ):
        return None
    if status in {"state_error", "expired"}:
        return (
            projected
            if kind == "none" and projected_keys == minimal_keys
            else None
        )
    if kind == "none":
        return (
            projected
            if status == "locked" and projected_keys == minimal_keys
            else None
        )
    if kind == "full":
        paid_full_keys = minimal_keys | {"duration_days", "entitlement_exp"}
        if status != "active" or projected_keys not in {
            minimal_keys,
            paid_full_keys,
        }:
            return None
        if projected_keys == paid_full_keys and (
            projected["duration_days"] < 90
            or projected["entitlement_exp"] <= 0
            or projected["entitlement_exp"] > max_epoch
            or projected["entitlement_exp"] <= server_now
        ):
            return None
        return projected
    if kind not in {"trial", "quota"}:
        return None

    expected_keys = _PET_AUTHORITY_METERED_KEYS
    if kind == "quota":
        expected_keys = expected_keys | {"duration_days"}
    if (
        projected_keys != expected_keys
        or status not in _PET_AUTHORITY_METERED_STATUSES
    ):
        return None
    total_seconds = projected["total_seconds"]
    remaining_seconds = projected["remaining_seconds"]
    usage_revision = projected["usage_revision"]
    last_seq = projected["last_seq"]
    visibility_lease_exp = projected["visibility_lease_exp"]
    entitlement_exp = projected["entitlement_exp"]
    entitlement_id = projected["entitlement_id"]
    if (
        total_seconds <= 0
        or remaining_seconds < 0
        or remaining_seconds > total_seconds
        or usage_revision <= 0
        or usage_revision > _PET_AUTHORITY_MAX_SEQUENCE
        or last_seq < -1
        or last_seq > _PET_AUTHORITY_MAX_SEQUENCE
        or visibility_lease_exp <= 0
        or visibility_lease_exp > max_epoch
        or entitlement_exp <= 0
        or entitlement_exp > max_epoch
        or entitlement_exp <= server_now
        or len(entitlement_id) != 64
        or any(char not in "0123456789abcdef" for char in entitlement_id)
    ):
        return None
    if kind == "trial":
        if total_seconds != 600:
            return None
    else:
        duration_days = projected["duration_days"]
        if (
            duration_days < 30
            or duration_days >= 90
            or (duration_days == 30 and (total_seconds != 43_200 if usage_schema == 1
                                       else total_seconds > max_epoch or total_seconds % 43_200 != 0))
            or (duration_days > 30 and total_seconds != 86_400)
            or (usage_schema == 2 and duration_days != 30)
        ):
            return None
    visible = projected["visible"]
    if status == "active":
        if (
            not visible
            or remaining_seconds <= 0
            or last_seq < 0
            or visibility_lease_exp <= server_now
            or visibility_lease_exp > min(server_now + 60, entitlement_exp)
        ):
            return None
    elif status == "available":
        if (
            visible
            or last_seq != -1
            or remaining_seconds != total_seconds
            or visibility_lease_exp > server_now
        ):
            return None
    elif status == "paused":
        if (
            visible
            or last_seq < 0
            or remaining_seconds <= 0
            or visibility_lease_exp > server_now
        ):
            return None
    elif status == "exhausted":
        if (
            visible
            or remaining_seconds != 0
            or visibility_lease_exp > server_now
        ):
            return None
    elif status in {"sequence_conflict", "session_conflict"}:
        if (
            (visible and visibility_lease_exp <= server_now)
            or (not visible and visibility_lease_exp > server_now)
        ):
            return None
    return projected


def _stable_pet_usage_authority(
    result: Any,
    *,
    max_epoch: int,
    result_type: type,
    contract_projector: Callable[..., Optional[dict[str, Any]]],
) -> tuple[bool, Optional[dict[str, str | int | bool]]]:
    """Return (usage_present, stable_view); invalid present data stays distinct."""
    fields = _project_activation_result_fields(
        result,
        result_type=result_type,
    )
    if fields is None:
        return True, None
    usage = fields["feature_usage"]
    mode = fields["mode"]
    plan_kind = fields["plan_kind"]
    feature_entitlements = fields["feature_entitlements"]
    paid_exp = fields["paid_exp"]
    trial_exp = fields["trial_exp"]
    raw_fields = object.__getattribute__(result, "__dict__")
    usage_schema = dict.get(raw_fields, "feature_usage_schema_version", 1)
    if type(usage_schema) is not int or usage_schema not in {1, 2}:
        return True, None
    member = dict.get(raw_fields, "membership")
    member_revision = dict.get(member, "revision") if type(member) is dict else None
    if usage is None:
        return False, None
    if type(usage) is not dict:
        return True, None
    usage_keys = list(dict.keys(usage))
    if (
        len(usage_keys) != 1
        or type(usage_keys[0]) is not str
        or usage_keys[0] != "pet_companion"
    ):
        return True, None
    current_pet = _project_pet_authority_dict(
        dict.__getitem__(usage, "pet_companion"),
        max_epoch=max_epoch,
        usage_schema=usage_schema,
    )
    if current_pet is None:
        return True, None
    try:
        canonical_pet = contract_projector(
            mode=mode,
            plan_kind=plan_kind,
            feature_entitlements=feature_entitlements,
            feature_usage={"pet_companion": current_pet},
            paid_exp=paid_exp,
            trial_exp=trial_exp,
            **({"feature_usage_schema_version": 2, "membership_revision": member_revision}
               if usage_schema == 2 else {}),
        )
    except Exception:  # noqa: BLE001
        return True, None
    return True, canonical_pet


def _pet_state_error_result(
    result: Any,
    *,
    now: int,
    result_type: type,
) -> Any:
    fields = _recover_activation_result_fields(
        result,
        result_type=result_type,
    ) or {}
    activated = fields.get("activated")
    mode = fields.get("mode")
    reason = fields.get("reason")
    source = fields.get("source")
    trial_exp = fields.get("trial_exp")
    master_key = fields.get("master_key")
    lease_exp = fields.get("lease_exp")
    paid_exp = fields.get("paid_exp")
    plan_kind = fields.get("plan_kind")
    transport_failure_classes = fields.get("transport_failure_classes")
    kwargs = {
        "activated": activated if type(activated) is bool else False,
        "mode": mode if type(mode) is str else "none",
        "reason": reason if type(reason) is str else "state_error",
        "source": source if type(source) is str else "server",
        "trial_exp": trial_exp if type(trial_exp) is int else None,
        "master_key": master_key if type(master_key) is bytes else None,
        "lease_exp": lease_exp if type(lease_exp) is int else None,
        "paid_exp": paid_exp if type(paid_exp) is int else None,
        "plan_kind": plan_kind if type(plan_kind) is str else None,
        "feature_entitlements": {"pet_companion": "none"},
        "feature_usage": {
            "pet_companion": {
                "kind": "none",
                "status": "state_error",
                "server_now": now,
            }
        },
        "transport_failure_classes": (
            transport_failure_classes
            if (
                type(transport_failure_classes) is tuple
                and all(
                    type(item) is str
                    for item in transport_failure_classes
                )
            )
            else ()
        ),
        "membership": fields.get("membership"),
        "feature_usage_schema_version": fields.get("feature_usage_schema_version", 1),
        "membership_card_capability": getattr(result, "membership_card_capability", None),
        "card_redemption": getattr(result, "card_redemption", None),
    }
    try:
        return result_type(**kwargs)
    except Exception:  # noqa: BLE001
        return result_type(
            activated=False,
            mode="none",
            reason="state_error",
            feature_entitlements={"pet_companion": "none"},
            feature_usage={
                "pet_companion": {
                    "kind": "none",
                    "status": "state_error",
                    "server_now": now,
                }
            },
        )


def _guard_pet_usage_revision(
    result: Any,
    *,
    same_identity: bool,
    now: int,
    max_epoch: int,
    result_type: type,
    contract_projector: Callable[..., Optional[dict[str, Any]]],
) -> Any:
    """Reject a stale online Pet view without revoking the calculator grant."""
    global _LAST_PET_USAGE_AUTHORITY
    global _LAST_PET_USAGE_AUTHORITY_SCHEMA

    usage_present, current_pet = _stable_pet_usage_authority(
        result,
        max_epoch=max_epoch,
        result_type=result_type,
        contract_projector=contract_projector,
    )
    if usage_present and current_pet is None:
        return _pet_state_error_result(
            result,
            now=now,
            result_type=result_type,
        )
    current_id = (
        current_pet.get("entitlement_id")
        if current_pet is not None
        else None
    )
    current_revision = (
        current_pet.get("usage_revision")
        if current_pet is not None
        else None
    )
    current_is_metered = (
        type(current_id) is str
        and type(current_revision) is int
    )
    usage_schema = getattr(result, "feature_usage_schema_version", 1)
    if not same_identity:
        _LAST_PET_USAGE_AUTHORITY = (
            dict(current_pet) if current_is_metered else None
        )
        _LAST_PET_USAGE_AUTHORITY_SCHEMA = usage_schema
        return result
    previous_pet = _LAST_PET_USAGE_AUTHORITY
    if current_pet is None or not current_is_metered:
        return result
    if previous_pet is None:
        _LAST_PET_USAGE_AUTHORITY = dict(current_pet)
        _LAST_PET_USAGE_AUTHORITY_SCHEMA = usage_schema
        return result
    previous_pet = _project_pet_authority_dict(
        previous_pet,
        max_epoch=max_epoch,
        usage_schema=_LAST_PET_USAGE_AUTHORITY_SCHEMA,
    )
    if previous_pet is None:
        return _pet_state_error_result(
            result,
            now=now,
            result_type=result_type,
        )
    previous_id = previous_pet.get("entitlement_id")
    previous_revision = previous_pet.get("usage_revision")
    if (
        type(previous_id) is not str
        or current_id != previous_id
        or type(previous_revision) is not int
    ):
        return _pet_state_error_result(
            result,
            now=now,
            result_type=result_type,
        )
    current_now = current_pet.get("server_now")
    previous_now = previous_pet.get("server_now")
    if (
        type(current_now) is not int
        or type(previous_now) is not int
        or current_now < previous_now
    ):
        return _pet_state_error_result(
            result,
            now=now,
            result_type=result_type,
        )
    if current_revision < previous_revision:
        return _pet_state_error_result(
            result,
            now=now,
            result_type=result_type,
        )
    if current_revision > previous_revision:
        _LAST_PET_USAGE_AUTHORITY = dict(current_pet)
        _LAST_PET_USAGE_AUTHORITY_SCHEMA = usage_schema
        return result
    current_stable = {
        key: value for key, value in current_pet.items() if key != "server_now"
    }
    previous_stable = {
        key: value for key, value in previous_pet.items() if key != "server_now"
    }
    if current_stable != previous_stable:
        return _pet_state_error_result(
            result,
            now=now,
            result_type=result_type,
        )
    _LAST_PET_USAGE_AUTHORITY = dict(current_pet)
    _LAST_PET_USAGE_AUTHORITY_SCHEMA = usage_schema
    return result


def activation_state(result: Any = None, now: Optional[int] = None) -> dict:
    """给 UI(overlay)渲染的统一状态。result 省略则合并在线 grant 与线路心跳。永不抛。

    返回字段:
      run_full   : bool  是否放行计算(= 时间感知 should_run_full)
      mode       : str   paid | trial | none
      reason     : str   ok | trial_expired | offline | no_server | revoked | ...
      seconds_left: int|None  试用剩余秒(trial 且未到期)
      needs_token: bool  是否该提示/弹 token(未放行时为 True)
      warn       : bool  试用剩余 < WARN_SECONDS(UI 变红); 已到期也算 warn
      text       : str   一行文案(标题栏显示)
      transport_grace: bool  可选；同 credential/device 的短线路宽限
    """
    heartbeat_failure = None
    if result is None:
        r, heartbeat_failure = _effective_activation_snapshot(now=now)
    else:
        r = result
    now = int(time.time()) if now is None else int(now)
    if r is None:
        return {"run_full": False, "mode": "none", "reason": "pending", "seconds_left": None,
                "needs_token": True, "warn": True, "text": "激活: 检测中…"}
    run_full = should_run_full(r, now)
    mode = str(getattr(r, "mode", "none"))
    reason = str(getattr(r, "reason", ""))
    from .client_activate import (
        _LOCAL_ACTIVATION_MESSAGES, _LOCAL_COMMIT_STAGES, _LOCAL_COMMIT_RECOVERIES,
        _activation_roaming_display, _ROAMING_REBIND_LIMIT_MESSAGE,
    )
    roaming_rebinds, roaming_limit_reached = _activation_roaming_display(r)
    roaming_state = {
        "roaming_rebinds": roaming_rebinds if run_full or roaming_limit_reached else None,
        "roaming_rebind_limit_reached": roaming_limit_reached,
    }
    local_commit_state = {}
    for name, allowed in (
        ("local_commit_stage", _LOCAL_COMMIT_STAGES),
        ("local_commit_recovery", _LOCAL_COMMIT_RECOVERIES),
    ):
        value = getattr(r, name, "")
        local_commit_state[name] = value if type(value) is str and value in allowed else ""
    transport_result = heartbeat_failure if heartbeat_failure is not None else r
    raw_transport_failures = getattr(transport_result, "transport_failure_classes", ())
    transport_failure_classes = tuple(
        value
        for value in raw_transport_failures
        if isinstance(value, str) and value in _SAFE_TRANSPORT_FAILURE_CLASSES
    ) if isinstance(raw_transport_failures, (tuple, list)) else ()
    raw_transport_summary = getattr(transport_result, "transport_summary", None)
    transport_availability = ""
    transport_summary = {
        "attempt_count": 0,
        "endpoint_count": 0,
        "stage_counts": tuple((stage, 0) for stage in _SAFE_TRANSPORT_STAGES),
        "all_endpoints_failed": False,
    }
    if raw_transport_summary is not None:
        try:
            raw_transport_availability = getattr(
                raw_transport_summary,
                "availability",
                "",
            )
        except BaseException:  # noqa: BLE001 - UI/privacy boundary
            raw_transport_availability = ""
        if (
            type(raw_transport_availability) is str
            and raw_transport_availability
            == _SYSTEM_PROXY_TLS_PATH_INCOMPATIBLE
        ):
            transport_availability = raw_transport_availability
        attempt_count = getattr(raw_transport_summary, "attempt_count", None)
        endpoint_count = getattr(raw_transport_summary, "endpoint_count", None)
        raw_stage_counts = getattr(raw_transport_summary, "stage_counts", None)
        all_endpoints_failed = getattr(
            raw_transport_summary,
            "all_endpoints_failed",
            None,
        )
        if (
            type(attempt_count) is int
            and 0 <= attempt_count <= 64
            and type(endpoint_count) is int
            and 0 <= endpoint_count <= 64
            and isinstance(raw_stage_counts, (tuple, list))
            and type(all_endpoints_failed) is bool
        ):
            counts = {stage: 0 for stage in _SAFE_TRANSPORT_STAGES}
            valid_counts = True
            for row in raw_stage_counts:
                if (
                    not isinstance(row, (tuple, list))
                    or len(row) != 2
                    or row[0] not in counts
                    or type(row[1]) is not int
                    or not 0 <= row[1] <= 64
                ):
                    valid_counts = False
                    break
                counts[row[0]] = row[1]
            if valid_counts and sum(counts.values()) == attempt_count:
                transport_summary = {
                    "attempt_count": attempt_count,
                    "endpoint_count": endpoint_count,
                    "stage_counts": tuple(
                        (stage, counts[stage]) for stage in _SAFE_TRANSPORT_STAGES
                    ),
                    "all_endpoints_failed": bool(
                        all_endpoints_failed
                        and endpoint_count > 0
                        and attempt_count == endpoint_count
                        and counts["http"] == 0
                    ),
                }
    # “带付费 token 的连接失败”不是“会员已到期”。对 UI 暴露为无当前授权，
    # 避免 overlay 的到期锁存要求用户反复粘贴 token。
    state_mode = (
        "none"
        if reason in _TRANSIENT_NON_ENTITLEMENT_REASONS or reason.startswith("error")
        else mode
    )
    entitlement_state = _validated_entitlement_state(
        r,
        run_full=run_full,
        mode=mode,
        now=now,
    )
    seconds_left = None
    try:
        seconds_left = r.trial_seconds_left(now)
    except Exception:  # noqa: BLE001
        seconds_left = None

    def _with_transport_grace(state: dict) -> dict:
        state.update(roaming_state)
        if heartbeat_failure is None:
            return state
        heartbeat_reason = str(getattr(heartbeat_failure, "reason", "") or "")
        safe_reason = (
            heartbeat_reason
            if heartbeat_reason in _TRANSIENT_NON_ENTITLEMENT_REASONS
            else "error"
        )
        state.update(
            {
                "run_full": True,
                "reason": "transport_grace",
                "transport_reason": safe_reason,
                "transport_grace": True,
                "transport_failure_classes": transport_failure_classes,
                "transport_summary": transport_summary,
                "activation_availability": "runtime_short_fluctuation",
                "credential_present": mode == "paid",
                "needs_token": False,
                "warn": False,
                "text": "激活连接波动，正在重试",
            }
        )
        return state

    if run_full and mode == "paid":
        pe = getattr(r, "paid_exp", None)
        if pe is not None:  # 限期会员: 剩余粒度 天时 → (剩<1天)时分 → (剩<1时)分秒; 剩<1天变红
            try:
                pleft = r.paid_seconds_left(now) or 0
            except Exception:  # noqa: BLE001
                pleft = 0
            d, prem = divmod(int(pleft), 86400)
            h, hrem = divmod(prem, 3600)
            m, s = divmod(hrem, 60)
            if d > 0:
                ptxt = f"会员 · 剩余 {d} 天 {h} 时"
            elif h > 0:
                ptxt = f"会员 · 剩余 {h} 时 {m} 分"
            else:
                ptxt = f"会员 · 剩余 {m} 分 {s} 秒"
            return _with_transport_grace(
                {"run_full": True, "mode": "paid", "reason": reason, "seconds_left": int(pleft),
                 "paid_exp": pe,
                 "needs_token": False, "warn": pleft < WARN_PAID_SECONDS, "text": ptxt,
                 **entitlement_state}
            )
        return _with_transport_grace(
            {"run_full": True, "mode": "paid", "reason": reason, "seconds_left": None,
             "paid_exp": pe,
             "needs_token": False, "warn": False, "text": "已激活",
             **entitlement_state}
        )
    if run_full and mode == "trial":
        left = seconds_left or 0
        h, rem = divmod(int(left), 3600)
        m, s = divmod(rem, 60)
        # <1h 显示分+秒(否则最后不到 1 分钟只显示"0时0分"易误以为已结束); ≥1h 显示时+分。
        txt = f"试用中 · 剩余 {m} 分 {s} 秒" if h == 0 else f"试用中 · 剩余 {h} 小时 {m} 分"
        return _with_transport_grace(
            {"run_full": True, "mode": "trial", "reason": reason, "seconds_left": int(left),
             "needs_token": False, "warn": left < WARN_SECONDS, "text": txt,
             **entitlement_state}
        )
    # 未放行(降级): 按 reason 给文案。只有 token/权益相关失败引导重新激活；
    # state_error、限流、升级门等不是换 token 能解决的问题，避免误导用户。
    needs_token = True
    activation_availability = "entitlement_unavailable"
    credential_present = mode == "paid"
    if reason in ("offline", "no_server"):
        needs_token = False
        if (
            transport_availability == _SYSTEM_PROXY_TLS_PATH_INCOMPATIBLE
            and "tls_handshake_failed" in transport_failure_classes
        ):
            activation_availability = _SYSTEM_PROXY_TLS_PATH_INCOMPATIBLE
            text = (
                "系统代理/TLS路径不兼容 · "
                "可关闭非必要代理或由客户端直连重试；无需更换 token"
            )
        elif transport_summary["all_endpoints_failed"]:
            if credential_present:
                activation_availability = "credential_online_recheck_failed"
                text = (
                    "已有激活凭据，但所有激活线路在线复核失败 · "
                    "无需更换 token；请运行激活网络诊断"
                )
            else:
                activation_availability = "cold_start_all_endpoints_failed"
                text = (
                    "冷启动时所有激活线路均未连接 · "
                    "无需更换 token；请运行激活网络诊断"
                )
        elif "http_reachable_invalid_payload" in transport_failure_classes:
            activation_availability = "http_response_invalid"
            text = "激活服务响应异常 · 无需更换 token，请稍后重试"
        elif any(
            value in transport_failure_classes
            for value in ("tls_connection_reset", "tls_handshake_failed")
        ):
            text = "当前网络出口或过滤组件阻断激活服务 · 无需更换 token；可尝试手机 USB 网络共享或另一有线出口"
        elif "dns_lookup_failed" in transport_failure_classes:
            text = "当前网络无法解析激活服务 · 无需更换 token；请运行激活网络诊断"
        elif any(
            value in transport_failure_classes
            for value in ("connect_timeout", "connect_refused")
        ):
            text = "当前网络连接激活服务超时或被拒绝 · 无需更换 token；可尝试另一网络出口"
        else:
            text = "未连上激活服务 · 请运行激活网络诊断，无需重复更换 token"
    elif reason in _LOCAL_ACTIVATION_MESSAGES:
        text = _LOCAL_ACTIVATION_MESSAGES[reason]
        needs_token = False
        activation_availability = reason
    elif reason == "local_clear_incomplete":
        text = "本机清除未完成 · 请关闭软件并重启；仍异常请导出诊断联系支持"
        needs_token = False
        activation_availability = "local_clear_incomplete"
    elif reason == "state_error":
        text = "服务器激活服务暂时异常 · 请稍后重试"
        needs_token = False
    elif reason == "rate_limited":
        text = "激活请求过于频繁 · 请稍后重试"
        needs_token = False
    elif reason == "server_error":
        text = "激活服务器暂时异常 · 请稍后重试"
        needs_token = False
    elif reason.startswith("error"):
        text = "激活状态检查异常 · 请稍后重试"
        needs_token = False
    elif reason == "upgrade_required":
        text = "当前版本已停用 · 请下载最新版"
        needs_token = False
    elif reason == "server_rejected":
        text = "激活请求被服务器拒绝 · 请核对后重试"
    elif reason == "revoked":
        text = "当前凭证已停用 · 请联系支持"
        needs_token = False
    elif reason == "device_revoked":
        text = "当前设备已停用 · 请联系支持"
        needs_token = False
    elif reason == "device_mismatch":
        text = (
            _ROAMING_REBIND_LIMIT_MESSAGE if roaming_limit_reached
            else "当前设备与授权不匹配 · 请重新绑定"
        )
    elif reason == "bad_token":
        text = "token 无效 · 请重新粘贴"
    elif reason == "token_persist_failed":
        text = "token 已验证但保存失败 · 请检查程序目录或当前身份的写入权限"
    elif reason == "expired" or mode == "paid":
        # 限期会员到期(服务端 expired, 或本地 paid_exp 已过但还没下次心跳确认)
        text = "会员已到期 · 需续费 token"
    elif reason in ("trial_expired", "none", "expired_trial") or mode == "trial":
        text = "试用已结束 · 需激活 token"
    else:
        text = "激活未通过 · 请稍后重试或联系支持"
    return {
        "run_full": False,
        "mode": state_mode,
        "reason": reason,
        "seconds_left": None,
        "needs_token": needs_token,
        "warn": True,
        "text": text,
        "transport_failure_classes": transport_failure_classes,
        "transport_summary": transport_summary,
        "activation_availability": activation_availability,
        "credential_present": credential_present,
        **local_commit_state,
        **roaming_state,
    }
