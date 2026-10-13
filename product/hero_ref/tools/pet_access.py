from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Any, Mapping


PET_TRIAL_SECONDS = 10 * 60
PET_30D_QUOTA_SECONDS = 12 * 60 * 60
PET_31_89D_QUOTA_SECONDS = 24 * 60 * 60
PET_FEATURE_KEY = "pet_companion"
PET_ACCESS_PREVIEWS = frozenset(
    {
        "free",
        "trial",
        "trial_paused",
        "expired",
        "full90",
        "365",
        "permanent",
        "short_paid",
        "short_paid_active",
        "short_paid_paused",
        "short_paid_exhausted",
        "quota30",
        "quota30_active",
        "quota30_paused",
        "quota30_exhausted",
        "quota60",
        "quota60_active",
        "quota60_paused",
        "quota60_exhausted",
        "quota90",
        "quota90_active",
        "quota90_paused",
        "quota90_exhausted",
        "checking",
        "pending",
        "error",
    }
)
_METERED_STATUSES = frozenset(
    {
        "trial_available",
        "trial_active",
        "trial_paused",
        "trial_exhausted",
        "quota_available",
        "quota_active",
        "quota_paused",
        "quota_exhausted",
    }
)


@dataclass(frozen=True)
class PetAccessState:
    status: str
    allowed: bool
    seconds_left: int | None
    reason: str
    plan_kind: str | None = None
    usage_kind: str | None = None
    total_seconds: int | None = None
    duration_days: int | None = None

    @property
    def can_start_trial(self) -> bool:
        return self.status == "trial_available"

    @property
    def can_start_usage(self) -> bool:
        return self.status in {
            "trial_available",
            "trial_paused",
            "quota_available",
            "quota_paused",
        }

    @property
    def is_metered(self) -> bool:
        return self.status in _METERED_STATUSES

    @property
    def can_persist_preferences(self) -> bool:
        """Only a currently verified full grant may restore Pet preferences."""
        return self.status == "full"


def _strict_timestamp(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if not math.isfinite(numeric) or numeric <= 0:
        return None
    return int(numeric)


def normalize_pet_trial_state(
    started_at: Any,
    expires_at: Any,
) -> tuple[int | None, int | None]:
    start = _strict_timestamp(started_at)
    expiry = _strict_timestamp(expires_at)
    if start is None or expiry is None or expiry <= start:
        return None, None
    # Keep the old local value bounded for preference-file compatibility. The
    # 0.3.0 stable-release access path does not use it as an entitlement grant.
    expiry = min(expiry, start + PET_TRIAL_SECONDS)
    return start, expiry


def normalize_pet_trial_seconds(
    value: Any,
    *,
    legacy_started_at: Any = None,
    legacy_expires_at: Any = None,
) -> int | None:
    if value is None:
        legacy_start, legacy_expiry = normalize_pet_trial_state(
            legacy_started_at,
            legacy_expires_at,
        )
        if legacy_start is None or legacy_expiry is None:
            return None
        # Schema 4 stored a wall-clock expiry. That build never shipped, so migrate
        # its declared allowance rather than burning time while the cat was hidden.
        value = legacy_expiry - legacy_start
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if not math.isfinite(numeric):
        return None
    return max(0, min(PET_TRIAL_SECONDS, int(numeric)))


def _exact_string_dict(value: Any) -> dict[str, Any] | None:
    if type(value) is not dict:
        return None
    keys = list(dict.keys(value))
    if any(type(key) is not str for key in keys):
        return None
    return value


def _strict_int(value: Any) -> int | None:
    return value if type(value) is int else None


def _signed_pet_authority(
    activation_state: dict[str, Any],
) -> tuple[str | None, str | None, dict[str, Any] | None, bool]:
    plan_raw = dict.get(activation_state, "plan_kind")
    plan_kind = plan_raw if type(plan_raw) is str else None
    if plan_raw is not None and plan_kind is None:
        return None, None, None, True

    grants = _exact_string_dict(
        dict.get(activation_state, "feature_entitlements")
    )
    usage = _exact_string_dict(dict.get(activation_state, "feature_usage"))
    if grants is None or usage is None:
        return None, plan_kind, None, True
    if (
        frozenset(dict.keys(grants)) != {PET_FEATURE_KEY}
        or frozenset(dict.keys(usage)) != {PET_FEATURE_KEY}
    ):
        return None, plan_kind, None, True
    grant = dict.__getitem__(grants, PET_FEATURE_KEY)
    pet_usage = _exact_string_dict(dict.__getitem__(usage, PET_FEATURE_KEY))
    if type(grant) is not str or pet_usage is None:
        return None, plan_kind, None, True
    return grant, plan_kind, pet_usage, False


_METERED_BASE_KEYS = frozenset(
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
_MAX_SAFE_EPOCH = 253_402_300_799


def _metered_plan_contract(
    *,
    usage_kind: str,
    plan_kind: str | None,
    duration_days: int | None,
    total_seconds: int | None,
    usage_schema: int = 1,
) -> tuple[int, int | None] | None:
    if usage_kind == "trial":
        if (
            plan_kind != "trial"
            or duration_days is not None
            or total_seconds != PET_TRIAL_SECONDS
        ):
            return None
        return PET_TRIAL_SECONDS, None
    if usage_kind != "quota" or duration_days is None:
        return None
    if duration_days == 30:
        if plan_kind != "30d" or (total_seconds != PET_30D_QUOTA_SECONDS if usage_schema == 1 else
                                  total_seconds is None or not 0 < total_seconds <= _MAX_SAFE_EPOCH
                                  or total_seconds % PET_30D_QUOTA_SECONDS != 0):
            return None
        return total_seconds, 30
    if 31 <= duration_days < 90:
        if usage_schema != 1 or plan_kind != "custom" or total_seconds != PET_31_89D_QUOTA_SECONDS:
            return None
        return PET_31_89D_QUOTA_SECONDS, duration_days
    return None


def _metered_access(
    usage: dict[str, Any],
    *,
    mode: str,
    plan_kind: str | None,
    usage_schema: int = 1,
    now: int | None = None,
) -> PetAccessState:
    keys = frozenset(dict.keys(usage))
    duration_days = (
        _strict_int(dict.get(usage, "duration_days"))
        if "duration_days" in keys
        else None
    )
    expected_keys = _METERED_BASE_KEYS | (
        {"duration_days"} if duration_days is not None else set()
    )
    if keys != expected_keys:
        return PetAccessState(
            "state_error",
            False,
            None,
            "invalid_usage_grant",
            plan_kind,
        )
    usage_kind = dict.get(usage, "kind")
    status = dict.get(usage, "status")
    total = _strict_int(dict.get(usage, "total_seconds"))
    remaining = _strict_int(dict.get(usage, "remaining_seconds"))
    server_now = _strict_int(dict.get(usage, "server_now"))
    entitlement_exp = _strict_int(dict.get(usage, "entitlement_exp"))
    visibility_lease_exp = _strict_int(
        dict.get(usage, "visibility_lease_exp")
    )
    usage_revision = _strict_int(dict.get(usage, "usage_revision"))
    last_seq = _strict_int(dict.get(usage, "last_seq"))
    visible = dict.get(usage, "visible")
    entitlement_id = dict.get(usage, "entitlement_id")
    contract = _metered_plan_contract(
        usage_kind=usage_kind if type(usage_kind) is str else "",
        plan_kind=plan_kind,
        duration_days=duration_days,
        total_seconds=total,
        usage_schema=usage_schema,
    )
    if (
        type(usage_kind) is not str
        or type(status) is not str
        or type(visible) is not bool
        or type(entitlement_id) is not str
        or contract is None
        or mode != ("trial" if usage_kind == "trial" else "paid")
        or status not in {"available", "active", "paused", "exhausted"}
        or remaining is None
        or server_now is None
        or entitlement_exp is None
        or visibility_lease_exp is None
        or usage_revision is None
        or last_seq is None
        or not 0 < server_now <= _MAX_SAFE_EPOCH
        or not server_now < entitlement_exp <= _MAX_SAFE_EPOCH
        or not 0 < usage_revision <= (1 << 63) - 1
        or not -1 <= last_seq <= (1 << 63) - 1
        or len(entitlement_id) != 64
        or any(char not in "0123456789abcdef" for char in entitlement_id)
    ):
        return PetAccessState(
            "state_error",
            False,
            None,
            "invalid_usage_grant",
            plan_kind,
            usage_kind if type(usage_kind) is str else None,
        )
    expected_total, duration_days = contract
    if not 0 <= remaining <= expected_total:
        return PetAccessState(
            "state_error",
            False,
            None,
            "invalid_usage_balance",
            plan_kind,
            usage_kind,
            expected_total,
            duration_days,
        )
    if status == "available":
        valid_state = (
            not visible
            and last_seq == -1
            and remaining == expected_total
            and visibility_lease_exp <= server_now
        )
    elif status == "active":
        valid_state = (
            visible
            and last_seq >= 0
            and remaining > 0
            and server_now < visibility_lease_exp
            and visibility_lease_exp <= min(server_now + 60, entitlement_exp)
        )
    elif status == "paused":
        valid_state = (
            not visible
            and last_seq >= 0
            and remaining > 0
            and visibility_lease_exp <= server_now
        )
    else:
        valid_state = (
            not visible
            and remaining == 0
            and visibility_lease_exp <= server_now
        )
    if not valid_state:
        return PetAccessState(
            "state_error",
            False,
            None,
            "invalid_usage_balance",
            plan_kind,
            usage_kind,
            expected_total,
            duration_days,
        )
    if now is not None and now >= entitlement_exp:
        return PetAccessState("locked", False, None, "pet_authority_expired", plan_kind, usage_kind)
    if now is not None and status == "active" and now >= visibility_lease_exp:
        return PetAccessState("state_error", False, None, "visibility_lease_expired", plan_kind, usage_kind)
    display_status = f"{usage_kind}_{status}"
    return PetAccessState(
        display_status,
        status == "active",
        remaining,
        "server_visible_usage",
        plan_kind,
        usage_kind,
        expected_total,
        duration_days,
    )


def _preview_metered_access(
    preview_key: str,
    seconds_remaining: Any,
) -> PetAccessState:
    if preview_key in {"free", "trial", "trial_paused", "expired"}:
        plan_kind = "trial"
        usage_kind = "trial"
        total = PET_TRIAL_SECONDS
        default_remaining = PET_TRIAL_SECONDS
        status = {
            "free": "trial_available",
            "trial": "trial_active",
            "trial_paused": "trial_paused",
            "expired": "trial_exhausted",
        }[preview_key]
        duration_days = None
    elif preview_key.startswith(("short_paid", "quota30")):
        plan_kind = "30d"
        usage_kind = "quota"
        total = PET_30D_QUOTA_SECONDS
        default_remaining = PET_30D_QUOTA_SECONDS
        status = {
            "short_paid": "quota_available",
            "short_paid_active": "quota_active",
            "short_paid_paused": "quota_paused",
            "short_paid_exhausted": "quota_exhausted",
            "quota30": "quota_available",
            "quota30_active": "quota_active",
            "quota30_paused": "quota_paused",
            "quota30_exhausted": "quota_exhausted",
        }[preview_key]
        duration_days = 30
    else:
        plan_kind = "custom"
        usage_kind = "quota"
        total = PET_31_89D_QUOTA_SECONDS
        default_remaining = PET_31_89D_QUOTA_SECONDS
        status = {
            "quota60": "quota_available",
            "quota60_active": "quota_active",
            "quota60_paused": "quota_paused",
            "quota60_exhausted": "quota_exhausted",
        }[preview_key]
        duration_days = 60
    if status.endswith("_exhausted"):
        remaining = 0
    else:
        normalized = _strict_int(seconds_remaining)
        remaining = (
            default_remaining
            if normalized is None
            else max(0, min(total, normalized))
        )
    if remaining <= 0:
        status = "trial_exhausted" if usage_kind == "trial" else "quota_exhausted"
    return PetAccessState(
        status,
        status.endswith("_active"),
        remaining,
        "controlled_preview",
        plan_kind,
        usage_kind,
        total,
        duration_days,
    )


def _full_access(
    usage: dict[str, Any],
    *,
    mode: str,
    plan_kind: str | None,
    usage_schema: int = 1,
    now: int | None = None,
) -> PetAccessState:
    keys = frozenset(dict.keys(usage))
    minimal_keys = {"kind", "status", "server_now"}
    paid_keys = minimal_keys | {"duration_days", "entitlement_exp"}
    if keys not in {frozenset(minimal_keys), frozenset(paid_keys)}:
        return PetAccessState(
            "state_error",
            False,
            None,
            "invalid_full_grant",
            plan_kind,
        )
    kind = dict.get(usage, "kind")
    status = dict.get(usage, "status")
    server_now = _strict_int(dict.get(usage, "server_now"))
    if (
        mode != "paid"
        or type(kind) is not str
        or type(status) is not str
        or kind != "full"
        or status != "active"
        or server_now is None
        or not 0 < server_now <= _MAX_SAFE_EPOCH
    ):
        return PetAccessState(
            "state_error",
            False,
            None,
            "invalid_full_grant",
            plan_kind,
        )
    if keys == frozenset(minimal_keys):
        if plan_kind not in {"legacy_permanent", "permanent"}:
            return PetAccessState(
                "state_error",
                False,
                None,
                "invalid_full_grant",
                plan_kind,
            )
        duration_days = None
    else:
        duration_days = _strict_int(dict.get(usage, "duration_days"))
        entitlement_exp = _strict_int(dict.get(usage, "entitlement_exp"))
        expected_plan = (
            "90d"
            if duration_days == 90
            else "365d"
            if duration_days == 365
            else "custom"
        )
        if (
            duration_days is None
            or duration_days < 90
            or (usage_schema == 2 and duration_days not in {90, 365})
            or entitlement_exp is None
            or not server_now < entitlement_exp <= _MAX_SAFE_EPOCH
            or plan_kind != expected_plan
        ):
            return PetAccessState(
                "state_error",
                False,
                None,
                "invalid_full_grant",
                plan_kind,
            )
        if now is not None and now >= entitlement_exp:
            return PetAccessState("locked", False, None, "pet_authority_expired", plan_kind, "full")
    return PetAccessState(
        "full",
        True,
        None,
        "server_full_entitlement",
        plan_kind,
        "full",
        None,
        duration_days,
    )


def pet_access_from_activation(
    activation_state: Mapping[str, Any] | None,
    *,
    trial_seconds_remaining: Any = None,
    trial_started_at: Any = None,
    trial_expires_at: Any = None,
    now: int | None = None,
    preview: str | None = None,
    preview_seconds_remaining: Any = None,
) -> PetAccessState:
    now = int(time.time()) if now is None else now
    if type(now) is not int or not 0 < now <= _MAX_SAFE_EPOCH:
        return PetAccessState("state_error", False, None, "invalid_current_time")
    preview_key = str(preview or "").strip().lower()
    if preview_key:
        if preview_key not in PET_ACCESS_PREVIEWS:
            raise ValueError(f"unknown pet access preview: {preview!r}")
        if preview_key in {
            "full90",
            "quota90",
            "quota90_active",
            "quota90_paused",
            "quota90_exhausted",
        }:
            return PetAccessState(
                "full",
                True,
                None,
                "controlled_preview",
                "90d",
                "full",
                None,
                90,
            )
        if preview_key == "365":
            return PetAccessState(
                "full",
                True,
                None,
                "controlled_preview",
                "365d",
                "full",
                None,
                365,
            )
        if preview_key == "permanent":
            return PetAccessState(
                "full",
                True,
                None,
                "controlled_preview",
                "legacy_permanent",
                "full",
            )
        if preview_key == "checking":
            return PetAccessState("checking", False, None, "checking")
        if preview_key == "pending":
            return PetAccessState(
                "entitlement_pending",
                False,
                None,
                "usage_ledger_pending",
            )
        if preview_key == "error":
            return PetAccessState(
                "state_error",
                False,
                None,
                "usage_service_error",
            )
        preview_seconds = (
            preview_seconds_remaining
            if preview_seconds_remaining is not None
            else trial_seconds_remaining
        )
        return _preview_metered_access(preview_key, preview_seconds)

    state = _exact_string_dict(activation_state)
    if state is None:
        return PetAccessState(
            "state_error",
            False,
            None,
            "invalid_activation_state",
        )
    raw_mode = dict.get(state, "mode", "checking")
    raw_reason = dict.get(state, "reason", "")
    if type(raw_mode) is not str or type(raw_reason) is not str:
        return PetAccessState(
            "state_error",
            False,
            None,
            "invalid_activation_state",
        )
    mode = raw_mode
    reason = raw_reason
    usage_schema = dict.get(state, "feature_usage_schema_version", 1)
    if type(usage_schema) is not int or usage_schema not in {1, 2}:
        return PetAccessState("state_error", False, None, "invalid_usage_schema")

    if mode == "checking":
        return PetAccessState("checking", False, None, "checking")
    if mode == "pending" or reason == "pending":
        return PetAccessState(
            "entitlement_pending",
            False,
            None,
            "entitlement_pending",
        )
    run_full = dict.get(state, "run_full")
    if run_full is not True or mode not in {"paid", "trial"}:
        if reason in {
            "error",
            "offline",
            "no_server",
            "timeout",
            "state_error",
            "server_error",
            "rate_limited",
            "bad_signature",
            "invalid_lease",
        }:
            return PetAccessState(
                "state_error",
                False,
                None,
                reason or "activation_error",
            )
        return PetAccessState("locked", False, None, reason or "activation_required")

    if (
        "feature_entitlements" not in state
        or "feature_usage" not in state
    ):
        pending_plan = dict.get(state, "plan_kind")
        if pending_plan is not None and (
            type(pending_plan) is not str
            or pending_plan
            not in {
                "trial",
                "30d",
                "90d",
                "365d",
                "custom",
                "legacy_permanent",
                "permanent",
            }
        ):
            return PetAccessState(
                "state_error",
                False,
                None,
                "invalid_plan_kind",
            )
        return PetAccessState(
            "entitlement_pending",
            False,
            None,
            "usage_ledger_pending",
            pending_plan,
        )
    grant, plan_kind, usage, authority_invalid = _signed_pet_authority(state)
    if authority_invalid or usage is None:
        return PetAccessState(
            "state_error",
            False,
            None,
            "invalid_entitlement_shape",
            plan_kind,
        )

    usage_kind = dict.get(usage, "kind")
    usage_status = dict.get(usage, "status")
    if type(usage_kind) is not str or type(usage_status) is not str:
        return PetAccessState(
            "state_error",
            False,
            None,
            "invalid_usage_grant",
            plan_kind,
        )
    if grant == "none" and usage_kind == "none":
        server_now = _strict_int(dict.get(usage, "server_now"))
        if (
            frozenset(dict.keys(usage)) != {"kind", "status", "server_now"}
            or server_now is None
            or not 0 < server_now <= _MAX_SAFE_EPOCH
            or usage_status not in {"locked", "expired", "state_error"}
        ):
            return PetAccessState(
                "state_error",
                False,
                None,
                "invalid_denial_authority",
                plan_kind,
            )
        if usage_status == "state_error":
            return PetAccessState(
                "state_error",
                False,
                None,
                "pet_authority_state_error",
                plan_kind,
            )
        return PetAccessState(
            "locked",
            False,
            None,
            f"pet_authority_{usage_status}",
            plan_kind,
        )
    if grant == "full" and usage_kind == "full":
        if usage_schema == 2 and (type(state.get("paid_exp")) is not int
                                 or type(usage.get("entitlement_exp")) is not int
                                 or usage["entitlement_exp"] > state["paid_exp"]):
            return PetAccessState("state_error", False, None, "invalid_full_deadline", plan_kind)
        return _full_access(usage, mode=mode, plan_kind=plan_kind, usage_schema=usage_schema, now=now)
    if grant in {"trial", "quota"} and usage_kind == grant:
        if usage_schema == 2 and (grant != "quota" or type(state.get("paid_exp")) is not int
                                 or type(usage.get("entitlement_exp")) is not int
                                 or usage["entitlement_exp"] > state["paid_exp"]):
            return PetAccessState("state_error", False, None, "invalid_quota_deadline", plan_kind)
        return _metered_access(
            usage,
            mode=mode,
            plan_kind=plan_kind,
            usage_schema=usage_schema,
            now=now,
        )
    return PetAccessState(
        "state_error",
        False,
        None,
        "inconsistent_signed_authority",
        plan_kind,
    )


def format_pet_trial_clock(seconds_left: int | None) -> str:
    seconds = max(0, int(seconds_left or 0))
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes:02d}:{seconds:02d}"


def format_pet_quota_clock(seconds_left: int | None) -> str:
    seconds = max(0, int(seconds_left or 0))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def format_pet_access_clock(state: PetAccessState) -> str:
    if state.usage_kind == "quota":
        return format_pet_quota_clock(state.seconds_left)
    return format_pet_trial_clock(state.seconds_left)


def pet_access_tier_label(state: PetAccessState) -> str:
    if state.usage_kind == "trial":
        return "免费猫猫体验"
    if state.usage_kind == "quota":
        if state.duration_days == 30:
            return "30 天猫猫额度"
        if (
            state.duration_days is not None
            and 31 <= state.duration_days < 90
        ):
            return f"{state.duration_days} 天猫猫额度"
        return "猫猫计时额度"
    if state.status == "full":
        if state.duration_days is not None:
            return f"{state.duration_days} 天猫猫权限"
        return "历史永久猫猫权限"
    return "猫猫权限"


def pet_access_contract_text(state: PetAccessState) -> str:
    if state.usage_kind == "trial":
        return "免费试用可使用猫猫 10 分钟"
    if state.usage_kind == "quota" and state.duration_days == 30:
        return "30 天授权可使用猫猫 12 小时"
    if (
        state.usage_kind == "quota"
        and state.duration_days is not None
        and 31 <= state.duration_days < 90
    ):
        return "31–89 天授权可使用猫猫 24 小时"
    if state.status == "full" and state.duration_days is not None:
        return "90 天及以上在订阅有效期内不限累计时长"
    if state.status == "full":
        return "历史永久用户可持续使用猫猫"
    return "猫猫权限以服务端签名状态为准"
