"""服务端激活核心(供 collector 的 /activate 用) —— token 签发/校验 + 设备绑定 + 撤销 + lease。

设计(承作者拍板):
- **版本无关 token**: token = 权益凭证, 不绑版本 -> 新版本同 token 直接用, 更新【不需重发 token】。
- **服务端权威**: 激活时验 token 签名 + 查撤销 + 查/记设备绑定, 发短期 lease(离线容忍, 到期再联网)。
- **设备绑定防转卖**: 首次激活记 uid->device; 换设备激活默认拒(走人工改绑)。
- **撤销表治支付欺诈**: 刷出的 token 一键撤销, 不动别人的。
- token 格式 = "payload.sighex"(payload="v1|uid|iat", sig=HMAC-SHA256(secret,payload).hex)
  -> 与 Rust 内核 bidking_kernel.verify_activation(token, secret) 【同式】(客户端可本地验签, 权威仍在服务端)。

纯逻辑(无 IO/网络), 故可独立单测; collector 负责持久化(撤销表/绑定/主密钥落文件)。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import time
from dataclasses import dataclass
from typing import Optional

_MAX_SAFE_EPOCH = 253_402_300_799
_MAX_FUTURE_STATE_SKEW = 300
TRIAL_RECORD_SCHEMA_VERSION = 2
DEFAULT_TRIAL_HOURS = 12.0
UNIFIED_TRIAL_WINDOW_SECONDS = 12 * 3600
_LEGACY_SHORT_TRIAL_WINDOW_SECONDS = 3 * 3600
_TRIAL_RECORD_KEYS = frozenset(
    {"schema_version", "start", "window_seconds", "last_server_now"}
)
_LEGACY_TRIAL_USAGE_KEYS = frozenset({"used", "last"})
_MAX_TRIAL_WINDOW_SECONDS = 366 * 86_400

# Increment only when activation_server must deploy with a matching core contract.
# server_update.sh and the non-writing readiness endpoint both enforce this value so
# a mixed production file set fails before it can accept activation traffic.
ACTIVATION_CORE_API_VERSION = 7
ROAMING_REBINDS_LIMIT = 8
ROAMING_REBINDS_POLICY_EPOCH = 1
_BINDING_RECORD_KEYS = frozenset({"schema_version", "device", "roaming_rebinds_used"})


def binding_state(record) -> tuple[str, int]:
    """Read a legacy device or one complete versioned binding without coercion."""
    if type(record) is str and record:
        return record, 0
    if (
        type(record) is not dict
        or any(type(key) is not str for key in record)
        or frozenset(record) != _BINDING_RECORD_KEYS
        or type(record["schema_version"]) is not int or record["schema_version"] != 1
        or type(record["device"]) is not str or not record["device"]
        or type(record["roaming_rebinds_used"]) is not int
        or not 0 <= record["roaming_rebinds_used"] <= ROAMING_REBINDS_LIMIT
    ):
        raise ValueError("invalid device binding record")
    return record["device"], record["roaming_rebinds_used"]


def roaming_rebinds_state(used: int) -> dict:
    """Fixed signed/wire projection of an already validated binding counter."""
    if type(used) is not int or not 0 <= used <= ROAMING_REBINDS_LIMIT:
        raise ValueError("invalid roaming rebind counter")
    return {"schema_version": 1, "policy_epoch": ROAMING_REBINDS_POLICY_EPOCH,
            "limit": ROAMING_REBINDS_LIMIT, "used": used,
            "remaining": ROAMING_REBINDS_LIMIT - used}

_DAY_SECONDS = 86400
PET_FEATURE_USAGE_SCHEMA_VERSION = 1
PET_FREE_TRIAL_SECONDS = 600
PET_30D_QUOTA_SECONDS = 43_200
PET_31_TO_89D_QUOTA_SECONDS = 86_400
_PLAN_BY_DURATION = {
    30 * _DAY_SECONDS: "30d",
    90 * _DAY_SECONDS: "90d",
    365 * _DAY_SECONDS: "365d",
}


def _paid_duration_days(*, token_iat: int, token_exp: Optional[int]) -> Optional[int]:
    """Return an exact whole-day duration from an already verified token.

    Pet must not infer a paid duration from a local expiry or a plan label.  The
    signed token payload is the server-side order/authorization source currently
    available to this service, and non-whole-day/invalid durations deliberately
    remain ineligible.
    """
    if token_exp is None:
        return None
    if type(token_iat) is not int or type(token_exp) is not int:
        return None
    duration = token_exp - token_iat
    if duration <= 0 or duration % _DAY_SECONDS:
        return None
    return duration // _DAY_SECONDS


def _pet_companion_contract(
    *,
    token_iat: Optional[int] = None,
    token_exp: Optional[int] = None,
    trial: bool = False,
) -> dict:
    """Derive the signed Pet contract from server-authoritative duration data."""
    if trial:
        return {
            "kind": "trial",
            "total_seconds": PET_FREE_TRIAL_SECONDS,
        }
    if token_exp is None:
        return {"kind": "full"}
    if type(token_iat) is not int:
        return {"kind": "none"}
    duration_days = _paid_duration_days(token_iat=token_iat, token_exp=token_exp)
    if duration_days is None or duration_days < 30:
        return {"kind": "none"}
    if duration_days == 30:
        return {
            "kind": "quota",
            "total_seconds": PET_30D_QUOTA_SECONDS,
            "duration_days": duration_days,
        }
    if duration_days < 90:
        return {
            "kind": "quota",
            "total_seconds": PET_31_TO_89D_QUOTA_SECONDS,
            "duration_days": duration_days,
        }
    return {
        "kind": "full",
        "duration_days": duration_days,
    }


def _plan_entitlements(
    *,
    token_iat: int,
    token_exp: Optional[int],
) -> tuple[str, dict[str, str], dict]:
    """Derive plan and Pet access only from an already verified token."""
    if token_exp is None:
        plan_kind = "legacy_permanent"
    else:
        duration = int(token_exp) - int(token_iat)
        plan_kind = _PLAN_BY_DURATION.get(duration, "custom")
    pet_contract = _pet_companion_contract(
        token_iat=token_iat,
        token_exp=token_exp,
    )
    pet_access = str(pet_contract["kind"])
    return plan_kind, {"pet_companion": pet_access}, pet_contract


@dataclass(frozen=True)
class CanonicalMembershipAuthority:
    """Server-validated STORE snapshot; never constructed from client fields."""
    account_uid: str
    membership_exp: int
    device_permission: str
    revision: int
    plan_kind: Optional[str]
    plan_exp: Optional[int]
    my_records_exp: Optional[int]
    private_dashboard_exp: Optional[int]
    full_pet_exp: Optional[int]
    purchased_quota_seconds: int


class CanonicalMembershipExpired(ValueError):
    pass


def canonical_membership_lease_fields(authority: CanonicalMembershipAuthority, *, now: int) -> dict:
    """Keep purchase tier/feature deadlines separate from accumulated expiry."""
    if type(authority) is not CanonicalMembershipAuthority or type(now) is not int:
        raise ValueError("invalid canonical membership")
    if type(authority.account_uid) is not str or not authority.account_uid:
        raise ValueError("invalid canonical identity")
    if type(authority.device_permission) is not str or authority.device_permission not in {"bound", "roaming"}:
        raise ValueError("invalid canonical permission")
    for value in (authority.membership_exp, authority.revision):
        if type(value) is not int or not 0 < value <= _MAX_SAFE_EPOCH:
            raise ValueError("invalid canonical epoch")
    if type(authority.purchased_quota_seconds) is not int or not 0 <= authority.purchased_quota_seconds <= _MAX_SAFE_EPOCH:
        raise ValueError("invalid purchased quota")
    if authority.membership_exp <= now:
        raise CanonicalMembershipExpired("expired")
    if authority.plan_kind not in {"30d", "90d", "365d"} or type(authority.plan_kind) is not str:
        raise ValueError("invalid canonical plan")
    for value in (authority.plan_exp, authority.my_records_exp, authority.private_dashboard_exp, authority.full_pet_exp):
        if value is not None and (type(value) is not int or not now < value <= authority.membership_exp):
            raise ValueError("invalid canonical feature epoch")
    if authority.plan_exp is None:
        raise ValueError("missing canonical plan period")
    if authority.plan_kind == "30d":
        if any(value is not None for value in (authority.my_records_exp, authority.private_dashboard_exp, authority.full_pet_exp)):
            raise ValueError("inconsistent canonical lower tier")
        if authority.purchased_quota_seconds <= 0:
            raise ValueError("missing canonical quota purchase")
        pet = {"kind": "quota", "total_seconds": authority.purchased_quota_seconds, "duration_days": 30}
    else:
        if (authority.my_records_exp is None or authority.full_pet_exp != authority.my_records_exp
            or authority.full_pet_exp < authority.plan_exp):
            raise ValueError("inconsistent canonical upper tier")
        if ((authority.plan_kind == "365d" and authority.private_dashboard_exp != authority.plan_exp)
            or (authority.plan_kind == "90d" and authority.private_dashboard_exp is not None)):
            raise ValueError("inconsistent canonical private tier")
        pet = {"kind": "full", "duration_days": 365 if authority.plan_kind == "365d" else 90,
               "entitlement_exp": authority.full_pet_exp}
    return {
        "uid": authority.account_uid, "paid_exp": authority.membership_exp,
        "plan_kind": authority.plan_kind, "feature_entitlements": {"pet_companion": pet["kind"]},
        "feature_usage": {"schema_version": 2 if authority.revision > 1 else 1, "pet_companion": pet},
        "membership": {"schema_version": 1, "revision": authority.revision,
                       "plan_exp": authority.plan_exp, "my_records_exp": authority.my_records_exp,
                       "private_dashboard_exp": authority.private_dashboard_exp},
    }


def _strict_state_timestamp(value, *, now: int) -> int:
    """Validate a persisted security-state timestamp without coercion."""
    if type(value) is not int:
        raise ValueError("state timestamp must be an exact int")
    if value <= 0 or value > _MAX_SAFE_EPOCH:
        raise ValueError("state timestamp out of range")
    if value > now + _MAX_FUTURE_STATE_SKEW:
        raise ValueError("state timestamp is in the future")
    return value


def _sig(secret: bytes, payload: str) -> str:
    return hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def mint_token(uid: str, secret: bytes, iat: Optional[int] = None,
               exp: Optional[int] = None, roaming: bool = False) -> str:
    """管理员签发 token(确认付款后)。uid = 买家标识(自定, 如订单号/QQ)。
    exp=None -> 【永久】token(payload 3 段, 向后兼容旧 token);
    exp=到期 ts -> 【限期】token(payload 4 段, 如 1 个月=iat+30*86400, 3 小时=iat+3*3600)。
    限期到期【从首次激活起算】(见 activate); 关着也走(日历), 用满 wall-clock 即失效。

    roaming=True(仅限期 token 有效): 【可换机】token —— 换设备激活时【重新绑定到新设备】而非拒绝
    (网吧/换机场景: 同一 token 在不同电脑都能用; payload 5 段 v1|uid|iat|exp|r)。默认 False = 设备绑定
    (防转卖: 换机拒)。永久 token 不支持 roaming(始终设备绑定防转卖)。

    ⚠️ uid 不能含 '|': payload 用 '|' 分段, uid 含 '|' 会让永久 token(3段)被 verify_token 误判为
    限期(4段)→ 提前锁死; 且撤销写完整 uid、verify 返回截断 uid → 撤销绕过。故 mint 端硬拒(#3)。"""
    if "|" in str(uid):
        raise ValueError("uid 不能含 '|'(会破坏 token payload 分段: 永久/限期误判 + 撤销绕过)")
    iat = int(time.time()) if iat is None else int(iat)
    if exp is not None:
        payload = f"v1|{uid}|{iat}|{int(exp)}"   # 4 段 = 限期(从首次激活起算)
        if roaming:
            payload += "|r"                       # 5 段 = 限期 + 可换机(网吧/换机)
    else:
        payload = f"v1|{uid}|{iat}"              # 3 段 = 永久(设备绑定, 不 roaming)
    return payload + "." + _sig(secret, payload)


def verify_token(token: str, secret: bytes) -> Optional[dict]:
    """验 HMAC 签名(常数时间)。通过返回 {uid, iat, exp, roaming}(exp=None 即永久); 否则 None。
    兼容 3 段(永久)/ 4 段(限期设备绑定)/ 5 段(限期可换机 v1|uid|iat|exp|r)payload。版本无关。"""
    if "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(_sig(secret, payload), sig):
        return None
    parts = payload.split("|")
    if parts[0] != "v1" or len(parts) not in (3, 4, 5):
        return None
    try:
        iat = int(parts[2])
        exp = int(parts[3]) if len(parts) >= 4 else None
    except ValueError:
        return None
    roaming = len(parts) == 5 and parts[4] == "r"   # 5 段第 5 位 'r' = 可换机
    return {"uid": parts[1], "iat": iat, "exp": exp, "roaming": roaming}


def activate(
    token: str,
    secret: bytes,
    device: str,
    *,
    bindings: dict,
    revoked: set,
    paid_starts: Optional[dict] = None,
    lease_days: int = 30,
    now: Optional[int] = None,
    canonical_membership: Optional[CanonicalMembershipAuthority] = None,
) -> tuple[bool, str, Optional[dict]]:
    """激活: 验 token + 查撤销 + 查/记设备绑定 -> (ok, reason, lease|None)。
    bindings: uid->legacy device/versioned record(调用方负责持久化); revoked: 撤销的 uid 集。
    reason ∈ {ok, bad_token, revoked, device_mismatch, roaming_rebind_limit, expired, state_error}。

    限期会员到期【从首次激活起算】(不是 token 生成时刻): 时长 = mint 时设的 exp-iat;
    paid_starts(uid->首次激活 ts, 调用方持久化)只在首次放行时记一次 —— 心跳/换机(同 uid)都不重置。
    这样【预生成/囤票的 token 也不会白白流逝时间】, 买家激活当天才开始算 N 天。lease.exp 取
    min(离线宽限, 会员到期), 离线宽限不超会员结束(防到期后离线白嫖)。paid_starts=None 视为空(每次现算)。"""
    now = int(time.time()) if now is None else int(now)
    info = verify_token(token, secret)
    if info is None:
        return False, "bad_token", None
    canonical_fields = None
    if canonical_membership is not None:
        try:
            canonical_fields = canonical_membership_lease_fields(canonical_membership, now=now)
        except CanonicalMembershipExpired:
            return False, "expired", None
        except (TypeError, ValueError):
            return False, "state_error", None
        if bool(info.get("roaming")) != (canonical_membership.device_permission == "roaming"):
            return False, "state_error", None
    uid = canonical_fields["uid"] if canonical_fields is not None else info["uid"]
    if uid in revoked or info["uid"] in revoked:
        return False, "revoked", None
    # 设备绑定先判(被拒的激活不应起算会员时钟)。换设备 -> 默认拒(人工改绑: 从 bindings 删该 uid);
    # roaming token(网吧/换机)-> 换设备【重新绑定】而非拒(见下 bind 处)。
    has_bound = uid in bindings
    record = bindings.get(uid)
    try:
        bound, rebinds_used = binding_state(record) if has_bound else (None, 0)
    except ValueError:
        # A stored null must not be treated as "never bound": doing so would
        # allow a damaged binding to be silently rebound on a new device.
        return False, "state_error", None
    roaming = bool(info.get("roaming"))
    if bound is not None and bound != device and not roaming:
        return False, "device_mismatch", None
    # 限期会员: 到期 = 首次激活 + 时长(exp-iat)。首次激活 ts 记 paid_starts(只记一次)。
    membership_exp = None
    tok_exp = info.get("exp")
    if canonical_fields is not None:
        membership_exp = canonical_fields["paid_exp"]
    elif tok_exp is not None:
        duration = max(0, int(tok_exp) - int(info["iat"]))     # = mint 时设的 N 天(秒)
        if paid_starts is None:
            paid_starts = {}
        has_recorded_start = uid in paid_starts
        recorded = paid_starts.get(uid)
        try:
            start = (
                now
                if not has_recorded_start
                else _strict_state_timestamp(recorded, now=now)
            )  # 首次=现在起算
        except (TypeError, ValueError, OverflowError):
            # 权益起点损坏时必须拒绝并等待人工修复，不能重置为 now（会延长会员），
            # 也不能让异常上冒到 HTTP 层直接断开所有版本的激活连接。
            return False, "state_error", None
        membership_exp = start + duration
        if now >= membership_exp:
            return False, "expired", None                      # 从首次激活算已到期(未记 start)
    rebind = has_bound and bound != device
    if roaming and rebind and rebinds_used >= ROAMING_REBINDS_LIMIT:
        return False, "roaming_rebind_limit", None
    if canonical_fields is None and tok_exp is not None and not has_recorded_start:
        paid_starts[uid] = now                                 # 只在【放行且首次】时记起点
    if roaming:
        rebinds_used += int(rebind)
        if not has_bound or type(record) is str or rebind:
            # Replace this row, never mutate a nested record shared by the
            # server's completely validated parsed-snapshot cache.
            bindings[uid] = {"schema_version": 1, "device": device,
                             "roaming_rebinds_used": rebinds_used}
    elif not has_bound:
        bindings[uid] = device
    lease_exp = now + lease_days * 86400
    plan_kind, feature_entitlements, pet_contract = _plan_entitlements(
        token_iat=info["iat"],
        token_exp=tok_exp,
    )
    lease = {
        "uid": uid,
        "device": device,
        "exp": lease_exp,
        "plan_kind": plan_kind,
        "feature_entitlements": feature_entitlements,
        "feature_usage": {
            "schema_version": PET_FEATURE_USAGE_SCHEMA_VERSION,
            "pet_companion": pet_contract,
        },
    }
    if canonical_fields is not None:
        lease.update(canonical_fields)
        lease["membership"]["credential_uid"] = info["uid"]
        lease["membership"]["canonical_uid"] = uid
    if membership_exp is not None:
        lease_exp = min(lease_exp, membership_exp)             # 离线宽限不超会员期
        lease["exp"] = lease_exp
        lease["paid_exp"] = membership_exp                     # 客户端显示"剩余X天"+到期降级
    if roaming:
        lease["roaming_rebinds"] = roaming_rebinds_state(rebinds_used)
    return True, "ok", lease


def grant_trial(
    device: str,
    *,
    trials: dict,
    trial_hours: float = DEFAULT_TRIAL_HOURS,
    grandfather_cutover_at: Optional[int] = None,
    grandfather_trial_hours: Optional[float] = None,
    now: Optional[int] = None,
) -> tuple[bool, str, Optional[dict]]:
    """半免费试用(无 token): 每设备固定窗口【自然日历】(从首次激活起算, 关着也走)。
    与会员 token 统一为日历计时(作者 2026-06-28 定: 统一服务器按自然时间扣, 不再"开着才扣")。

    新记录显式保存 ``start`` 和 ``window_seconds``。统一十二小时策略会把严格合法的
    旧三小时 v2 记录沿用原 ``start`` 扩为十二小时；不会重置起点或再发第二段窗口。
    其他时长仍不得随默认值变化而追溯改变。启用显式 cutover 前，必须停服并用
    ``migrate_trials_for_cutover`` 原子固化全部旧整数/用时制记录；启用后任何残留、
    迟到或恢复的旧形状均 fail-closed，不能仅凭旧时间戳冒充 grandfathered 设备。

    返回 (ok, reason, lease|None);reason ∈ {ok, trial_expired, state_error};lease.exp = start + window =
    客户端展示倒计时(自然日历, 关不关都走)。"""
    if type(device) is not str or not device or type(trials) is not dict:
        return False, "state_error", None
    if not _exact_string_key_dict(trials):
        return False, "state_error", None
    if now is None:
        now = int(time.time())
    elif type(now) is not int:
        return False, "state_error", None
    try:
        _strict_state_timestamp(now, now=now)
        new_window = _strict_trial_window_seconds(trial_hours)
        cutover, grandfather_window = _strict_trial_cutover(
            grandfather_cutover_at,
            grandfather_trial_hours,
            new_window=new_window,
            now=now,
        )
    except (TypeError, ValueError, OverflowError):
        return False, "state_error", None
    has_record = device in trials
    rec = trials.get(device)
    if not has_record:
        start = now
        window = new_window
        trials[device] = _trial_record(
            start=start,
            window_seconds=window,
            last_server_now=now,
        )
    elif type(rec) is int:
        if cutover is not None:
            # Once the cutover is enabled, every pre-existing integer must already
            # have been atomically migrated while the service was stopped.  Accepting
            # an integer here would let a late/restored record impersonate a
            # grandfathered device merely by choosing an old timestamp.
            return False, "state_error", None
        try:
            start = _strict_state_timestamp(rec, now=now)
        except (TypeError, ValueError, OverflowError):
            return False, "state_error", None
        window = new_window
        trials[device] = _trial_record(
            start=start,
            window_seconds=window,
            last_server_now=now,
        )
    elif (
        type(rec) is dict
        and _exact_string_key_dict(rec)
        and frozenset(dict.keys(rec)) == _TRIAL_RECORD_KEYS
    ):
        try:
            start, window, last_server_now = _strict_trial_record(rec, now=now)
        except (TypeError, ValueError, OverflowError):
            return False, "state_error", None
        if now < last_server_now:
            return False, "state_error", None
        if cutover is not None:
            expected_window = (
                grandfather_window if start < cutover else new_window
            )
            unified_policy = (
                new_window == UNIFIED_TRIAL_WINDOW_SECONDS
                and grandfather_window == UNIFIED_TRIAL_WINDOW_SECONDS
            )
            if (
                unified_policy
                and window == _LEGACY_SHORT_TRIAL_WINDOW_SECONDS
            ):
                window = UNIFIED_TRIAL_WINDOW_SECONDS
            elif window != expected_window:
                return False, "state_error", None
        elif (
            new_window == UNIFIED_TRIAL_WINDOW_SECONDS
            and window == _LEGACY_SHORT_TRIAL_WINDOW_SECONDS
        ):
            window = UNIFIED_TRIAL_WINDOW_SECONDS
        if now > last_server_now or rec["window_seconds"] != window:
            trials[device] = _trial_record(
                start=start,
                window_seconds=window,
                last_server_now=now,
            )
    elif (
        type(rec) is dict
        and _exact_string_key_dict(rec)
        and frozenset(dict.keys(rec)) == _LEGACY_TRIAL_USAGE_KEYS
    ):
        if cutover is not None:
            return False, "state_error", None
        used = rec.get("used")
        if type(used) is not int or used < 0:
            return False, "state_error", None
        start = now - used
        if start <= 0:
            return False, "state_error", None
        window = new_window
        trials[device] = _trial_record(
            start=start,
            window_seconds=window,
            last_server_now=now,
        )
    else:
        # Existing invalid state must not silently reset a device's trial.
        return False, "state_error", None
    if start > _MAX_SAFE_EPOCH - window:
        return False, "state_error", None
    if now >= start + window:
        return False, "trial_expired", None                   # 日历到期 -> 引导付费
    exp = start + window
    lease = {
        "uid": f"trial:{device}",
        "device": device,
        "exp": exp,
        "trial": True,
        "plan_kind": "trial",
        "feature_entitlements": {"pet_companion": "trial"},
        "feature_usage": {
            "schema_version": PET_FEATURE_USAGE_SCHEMA_VERSION,
            "pet_companion": _pet_companion_contract(trial=True),
        },
    }
    return True, "ok", lease


def _strict_trial_window_seconds(hours) -> int:
    """Project a configured duration without accepting coercion or non-finite values."""
    if type(hours) not in (int, float):
        raise ValueError("trial hours must be an exact number")
    if type(hours) is float and not math.isfinite(hours):
        raise ValueError("trial hours must be finite")
    if hours <= 0 or hours > 366 * 24:
        raise ValueError("trial hours out of range")
    seconds_value = hours * 3600
    if type(seconds_value) is float:
        if not seconds_value.is_integer():
            raise ValueError("trial hours must resolve to whole seconds")
        seconds = int(seconds_value)
    else:
        seconds = seconds_value
    if seconds <= 0 or seconds > _MAX_TRIAL_WINDOW_SECONDS:
        raise ValueError("trial window out of range")
    return seconds


def _strict_trial_cutover(
    cutover_at,
    grandfather_hours,
    *,
    new_window: int,
    now: int,
) -> tuple[Optional[int], Optional[int]]:
    """Validate the all-or-nothing grandfather configuration."""
    if cutover_at is None and grandfather_hours is None:
        return None, None
    if cutover_at is None or grandfather_hours is None:
        raise ValueError("trial grandfather configuration is incomplete")
    if type(cutover_at) is not int:
        raise ValueError("trial cutover must be an exact int")
    if cutover_at <= 0 or cutover_at > _MAX_SAFE_EPOCH or cutover_at > now:
        raise ValueError("trial cutover out of range")
    grandfather_window = _strict_trial_window_seconds(grandfather_hours)
    if grandfather_window < new_window:
        raise ValueError("grandfather window cannot shorten existing trials")
    return cutover_at, grandfather_window


def _trial_record(
    *,
    start: int,
    window_seconds: int,
    last_server_now: int,
) -> dict:
    return {
        "schema_version": TRIAL_RECORD_SCHEMA_VERSION,
        "start": start,
        "window_seconds": window_seconds,
        "last_server_now": last_server_now,
    }


def _strict_trial_record(record: dict, *, now: int) -> tuple[int, int, int]:
    if (
        type(record) is not dict
        or not _exact_string_key_dict(record)
        or frozenset(dict.keys(record)) != _TRIAL_RECORD_KEYS
    ):
        raise ValueError("invalid trial record shape")
    if type(record["schema_version"]) is not int:
        raise ValueError("invalid trial record schema")
    if record["schema_version"] != TRIAL_RECORD_SCHEMA_VERSION:
        raise ValueError("unsupported trial record schema")
    start = _strict_state_timestamp(record["start"], now=now)
    window = record["window_seconds"]
    if (
        type(window) is not int
        or window <= 0
        or window > _MAX_TRIAL_WINDOW_SECONDS
        or start > _MAX_SAFE_EPOCH - window
    ):
        raise ValueError("invalid trial record window")
    last_server_now = _strict_state_timestamp(record["last_server_now"], now=now)
    if last_server_now < start:
        raise ValueError("trial record clock precedes start")
    return start, window, last_server_now


def migrate_trials_for_cutover(
    trials: dict,
    *,
    cutover_at: int,
    grandfather_trial_hours: float,
    now: Optional[int] = None,
) -> dict:
    """Return an all-or-nothing v2 snapshot for an offline duration cutover.

    The caller must stop the activation service and atomically persist the returned
    object before enabling the cutover.  Every accepted record must have begun
    strictly before ``cutover_at``; this prevents a restored or injected legacy
    integer from acquiring grandfather status after the boundary.
    """
    if now is None:
        now = int(time.time())
    elif type(now) is not int:
        raise ValueError("migration time must be an exact int")
    _strict_state_timestamp(now, now=now)
    if type(trials) is not dict:
        raise ValueError("trial state root must be an exact dict")
    if not _exact_string_key_dict(trials):
        raise ValueError("trial state keys must be exact strings")
    if type(cutover_at) is not int:
        raise ValueError("trial cutover must be an exact int")
    if (
        cutover_at <= 0
        or cutover_at > _MAX_SAFE_EPOCH
        or cutover_at > now + _MAX_FUTURE_STATE_SKEW
    ):
        raise ValueError("trial cutover out of range")
    window = _strict_trial_window_seconds(grandfather_trial_hours)
    migrated: dict[str, dict] = {}
    for device, record in trials.items():
        if type(device) is not str or not device:
            raise ValueError("invalid trial device")
        if type(record) is int:
            start = _strict_state_timestamp(record, now=now)
            if start > now:
                raise ValueError("trial start is ahead of migration time")
        elif (
            type(record) is dict
            and _exact_string_key_dict(record)
            and frozenset(dict.keys(record)) == _LEGACY_TRIAL_USAGE_KEYS
        ):
            used = record.get("used")
            last = record.get("last")
            if type(used) is not int or used < 0:
                raise ValueError("invalid legacy trial usage")
            last = _strict_state_timestamp(last, now=now)
            if last > now:
                raise ValueError("legacy trial clock is ahead of migration time")
            start = now - used
            if start <= 0:
                raise ValueError("invalid legacy trial start")
        elif (
            type(record) is dict
            and _exact_string_key_dict(record)
            and frozenset(dict.keys(record)) == _TRIAL_RECORD_KEYS
        ):
            start, existing_window, _last_server_now = _strict_trial_record(
                record,
                now=now,
            )
            if existing_window != window:
                raise ValueError("existing trial window conflicts with grandfathering")
            if start > now or _last_server_now > now:
                raise ValueError("trial record clock is ahead of migration time")
        else:
            raise ValueError("invalid trial record")
        if start >= cutover_at:
            raise ValueError("trial record is not before the cutover")
        migrated[device] = _trial_record(
            start=start,
            window_seconds=window,
            last_server_now=now,
        )
    return migrated


def reset_trials_for_public_refresh(
    trials: dict,
    *,
    refresh_at: int,
    trial_hours: float = DEFAULT_TRIAL_HOURS,
    now: Optional[int] = None,
) -> dict:
    """Validate and clear the ledger for an authorized public trial refresh.

    The next no-token activation recreates one fixed twelve-hour record from its own
    server-authoritative request time.  A frozen ``refresh_at`` rejects any record
    written after the maintenance boundary, so retrying the same operation cannot
    silently erase users that arrived after service resumed.  The caller must stop
    the activation service and use the offline CAS/backup admin before publishing
    this empty snapshot.
    """
    if now is None:
        now = int(time.time())
    elif type(now) is not int:
        raise ValueError("maintenance time must be an exact int")
    _strict_state_timestamp(now, now=now)
    if type(refresh_at) is not int:
        raise ValueError("trial refresh time must be an exact int")
    refresh_at = _strict_state_timestamp(refresh_at, now=now)
    if refresh_at > now:
        raise ValueError("trial refresh time is ahead of maintenance time")
    window = _strict_trial_window_seconds(trial_hours)
    if window != UNIFIED_TRIAL_WINDOW_SECONDS:
        raise ValueError("public trial refresh must use exactly twelve hours")
    if type(trials) is not dict or not _exact_string_key_dict(trials):
        raise ValueError("trial state must be an exact string-key dict")

    for device, record in dict.items(trials):
        if type(device) is not str or not device:
            raise ValueError("invalid trial device")
        start, _existing_window, last_server_now = _strict_trial_record(
            record,
            now=now,
        )
        if start > now or last_server_now > now:
            raise ValueError("trial record clock is ahead of maintenance time")
        if last_server_now >= refresh_at:
            raise ValueError("trial record changed at or after the refresh boundary")
    return {}


def downgrade_trials_for_legacy_runtime(
    trials: dict,
    *,
    legacy_trial_hours: float,
    now: Optional[int] = None,
) -> dict:
    """Return legacy integer starts while preserving every v2 record's exact expiry.

    This is the only safe rollback path after the fixed-window runtime has accepted
    new trials.  Restoring a pre-cutover backup would discard those records; copying
    ``start`` alone would silently stretch three-hour records under a twelve-hour
    legacy default.  The transformed start satisfies::

        legacy_start + legacy_window == v2_start + v2_window

    The operation is deliberately all-or-nothing and accepts v2 records only.
    """
    if now is None:
        now = int(time.time())
    elif type(now) is not int:
        raise ValueError("downgrade time must be an exact int")
    _strict_state_timestamp(now, now=now)
    if type(trials) is not dict or not _exact_string_key_dict(trials):
        raise ValueError("trial state must be an exact string-key dict")
    legacy_window = _strict_trial_window_seconds(legacy_trial_hours)
    downgraded: dict[str, int] = {}
    for device, record in dict.items(trials):
        if type(device) is not str or not device:
            raise ValueError("invalid trial device")
        start, window, last_server_now = _strict_trial_record(record, now=now)
        if start > now or last_server_now > now:
            raise ValueError("trial record clock is ahead of downgrade time")
        if legacy_window < window:
            raise ValueError("legacy window cannot preserve a longer trial safely")
        legacy_start = start + window - legacy_window
        if legacy_start <= 0 or legacy_start > now:
            raise ValueError("downgraded trial start out of range")
        if legacy_start > _MAX_SAFE_EPOCH - legacy_window:
            raise ValueError("downgraded trial expiry out of range")
        if legacy_start + legacy_window != start + window:
            raise ValueError("downgraded trial expiry mismatch")
        downgraded[device] = legacy_start
    return downgraded


def _exact_string_key_dict(value: dict) -> bool:
    """Inspect an exact dict without dispatching user Mapping/key protocols."""
    if type(value) is not dict:
        return False
    return all(type(key) is str for key in dict.keys(value))


def sign_lease(lease: dict, secret: bytes) -> str:
    """服务端签 lease(客户端缓存, 离线期内自验有效 + 未过期)。"""
    payload = json.dumps(lease, sort_keys=True, separators=(",", ":"))
    return payload + "." + _sig(secret, payload)


def verify_lease(signed: str, secret: bytes, now: Optional[int] = None) -> Optional[dict]:
    """客户端验缓存的 lease: 签名有效且未过期则返回 lease, 否则 None(需重新联网激活)。"""
    now = int(time.time()) if now is None else int(now)
    if "." not in signed:
        return None
    payload, sig = signed.rsplit(".", 1)
    if not hmac.compare_digest(_sig(secret, payload), sig):
        return None
    try:
        lease = json.loads(payload)
    except Exception:  # noqa: BLE001
        return None
    if int(lease.get("exp", 0)) < now:
        return None  # 过期
    return lease
