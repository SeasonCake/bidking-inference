"""collector 端的激活服务(/activate /telemetry 的处理 + 持久化)。逻辑核心在 activation_core(纯函数)。

一次性永久(作者拍板 2026-06-27)= 【token 永不过期】(verify_token 不校验过期)+ 服务端每次回连【重发短租约】。
故 lease ≠ 永久: lease_days 取【短宽限窗口】(默认 30 天 = 1 个月, 好管理)。这样:
  · 买一次 → token 永久有效, 客户端离线宽限期内不必回连(最长 30 天);
  · 撤销/退款 → uid 写进 revoked.txt → 在线【立即生效】, 离线最迟下次回连(≤30 天)生效;
  · 试用到期同理(trial lease 仅 trial_hours 小时)。
若 lease 取 100 年(旧设计)则离线用户几乎永不回连 → 撤销形同失效, 故【不再用长租约】。
设备绑定持久化到 bindings.json(首次激活记 uid->device)。

文件(都在服务端工作目录, gitignore, 不入包):
- activation_key.txt : 激活密钥(签 token/lease; 与 ingest key 分开)
- master_key.txt     : 出厂数据主密钥 base64(激活成功后交付客户端解密 data/processed)
- master_key_v2.txt  : 【可选】轮换后的新主密钥(见下"双密钥窗口"; 文件不存在 = 行为与轮换前完全一致)
- device_block_key.txt : 设备撤销专用 HMAC key；不与 token/lease 签名 key 混用
- bindings.json      : uid -> device 绑定(本服务写)
- revoked.txt        : 每行一个被撤销的 uid(管理员手写; 每次请求实时读 → 撤销立即生效)
- device_blocks.json : 只保存设备指纹的域分离 HMAC 和最小审计元数据；不保存原始 device
- /var/lib/bidking-activation-anchor/pet_usage.sqlite3 :
  Pet 可见用量事务数据库；只保存域分离 HMAC 身份、序号、服务端秒数、revision、
  canonical ledger 摘要和 HMAC 提交链
- /var/lib/bidking-activation-anchor/pet_usage.head.jsonl :
  SQLite 外部 append-only HMAC 单调提交头；防止只恢复旧数据库文件后重放旧额度
- pet_usage.json / pet_usage.anchor.jsonl :
  未部署 A1 前的保留名；发现即 fail-closed 并要求显式离线迁移，绝不参与授权
- activation_telemetry.jsonl : C 上报落盘

【双密钥窗口 / 主密钥轮换】(0.2.7 P0 配套)
0.2.6 及更早的 /activate 走【明文 HTTP】下发 master_key —— 能嗅探流量者抓一次激活即可拿到主密钥,
解开包内全部 BKENC1 数据(Cython+加密的护城河被明文信道绕过)。故主密钥【必须视为已泄露 → 轮换】。
但客户端包的数据是用【打包时那把 key】加密的: 服务端若单 key 直接换新, 所有旧包立刻解不开 = 全员失活。

解法 = 按客户端版本发对应的 key:
  · client_version >= v2_min_version(默认 0.2.7 = 客户端走 HTTPS 的第一个版本) → 发 master_key_v2
  · 更低版本 / 老客户端(请求里根本没有 client_version 字段)                    → 发 master_key(旧)
0.2.7 包用【新 key】加密数据且只走 HTTPS → 新 key 从不经明文信道; 旧包继续用旧 key 正常工作。
⚠️ 未部署 master_key_v2.txt 时 _key_for_client() 恒返回旧 key = 与轮换前【行为完全等价】(零风险增量)。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional  # __init__ 注解 paid_starts_path: Optional[Path] 用到; 缺它 get_type_hints 会 NameError

import activation_core
import upload_lease
try:
    import membership_card_store
except ModuleNotFoundError as exc:
    if exc.name != "membership_card_store":
        raise
    membership_card_store = None  # Old, unconfigured deploy bundles remain usable.

_LEASE_GRACE_DAYS = 30   # 离线宽限/回连窗口(默认 1 个月)。token 才是永久权益; 撤销靠周期回连生效
_V2_MIN_VERSION = "0.2.7"  # 拿新主密钥的最低客户端版本(= 客户端走 HTTPS 的第一个版本)
_PRIVATE_DASHBOARD_DURATION = 365 * 86400  # 公版最高档: 365 天订阅继承历史永久版的私有大数据权益
_MY_RECORDS_MIN_DURATION = 90 * 86400  # 90 天及以上在主会员有效期内可查看本人对局记录
_DEVICE_BLOCK_SCHEMA_VERSION = 1
_DEVICE_BLOCK_DOMAIN = b"device-block-v1|"
_DEVICE_BLOCK_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_DEVICE_BLOCK_KEY_MIN_BYTES = 32
_DEVICE_BLOCK_KEY_EQUALITY_DOMAIN = b"device-block-key-equality-v1|"
_DEVICE_BLOCK_REASON_CODES = frozenset({"confirmed_abuse", "deploy_gate"})
_DEVICE_BLOCK_ACTOR_CODES = frozenset({"author", "activation_deploy_gate"})
_DEVICE_BLOCK_EVIDENCE_RE = re.compile(r"^(?:INC|APL|TEST)-[A-Z0-9]{12,40}$")
_PET_FEATURE_USAGE_SCHEMA_VERSION = 1
_PET_USAGE_LEDGER_SCHEMA_VERSION = 2
_PET_USAGE_DB_SCHEMA_VERSION = 1
_PET_USAGE_DB_APPLICATION_ID = 0x42504B31  # "BPK1"
_PET_USAGE_DB_DEFAULT_PATH = Path(
    "/var/lib/bidking-activation-anchor/pet_usage.sqlite3"
)
_PET_USAGE_DOMAIN = b"pet-usage-entitlement-v1|"
_PET_DEVICE_DOMAIN = b"pet-usage-device-v1|"
_PET_SESSION_DOMAIN = b"pet-usage-session-v1|"
_PET_USAGE_ANCHOR_DOMAIN = b"pet-usage-sqlite-chain-v1|"
_PET_USAGE_HEAD_DOMAIN = b"pet-usage-external-head-v1|"
_PET_USAGE_HEAD_SCHEMA_VERSION = 1
_PET_USAGE_HEAD_MAX_BYTES = 32 * 1024 * 1024
_PET_USAGE_HEAD_ZERO_TAG = "0" * 64
_PET_OPAQUE_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_PET_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
_PET_USAGE_EVENT_KEYS = frozenset({"session_id", "seq", "visible"})
_PET_USAGE_LEASE_SECONDS = 15
_PET_USAGE_IN_PROCESS_WAIT_SECONDS = 0.25
_PET_USAGE_DB_MAX_LEDGER_BYTES = 16 * 1024 * 1024
_PET_USAGE_STATE_TABLE_SQL = """
CREATE TABLE pet_usage_state (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version INTEGER NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    ledger_json TEXT NOT NULL,
    ledger_sha256 TEXT NOT NULL CHECK (length(ledger_sha256) = 64),
    chain_tag TEXT NOT NULL CHECK (length(chain_tag) = 64)
) WITHOUT ROWID
"""
_PET_USAGE_COMMITS_TABLE_SQL = """
CREATE TABLE pet_usage_commits (
    revision INTEGER PRIMARY KEY CHECK (revision > 0),
    ledger_sha256 TEXT NOT NULL CHECK (length(ledger_sha256) = 64),
    previous_tag TEXT NOT NULL CHECK (length(previous_tag) = 64),
    tag TEXT NOT NULL CHECK (length(tag) = 64)
) WITHOUT ROWID
"""
_REQUIRED_ACTIVATION_CORE_API_VERSION = 7
_REQUIRED_ACTIVATION_CORE_CALLABLES = (
    "_pet_companion_contract",
    "_strict_state_timestamp",
    "activate",
    "binding_state",
    "roaming_rebinds_state",
    "grant_trial",
    "mint_token",
    "sign_lease",
    "verify_token",
    "canonical_membership_lease_fields",
)


def activation_core_compatibility_error() -> Optional[str]:
    """Return a stable error code without exposing server paths or secrets."""
    if getattr(activation_core, "ACTIVATION_CORE_API_VERSION", None) != (
        _REQUIRED_ACTIVATION_CORE_API_VERSION
    ):
        return "activation_core_api_mismatch"
    if any(
        not callable(getattr(activation_core, name, None))
        for name in _REQUIRED_ACTIVATION_CORE_CALLABLES
    ):
        return "activation_core_symbol_missing"
    return None


def require_activation_core_compatible() -> None:
    error = activation_core_compatibility_error()
    if error is not None:
        raise RuntimeError(error)


def activation_readiness(service) -> tuple[int, dict]:
    """Non-writing activation readiness for deploy and client diagnostics.

    This deliberately does not inspect paid/trial state or synthesize an activation
    request.  It proves only that the service was constructed from a compatible
    server/core pair and is ready to receive real traffic.
    """
    error = activation_core_compatibility_error()
    if error is not None:
        return 503, {"ok": False, "service": "activation", "reason": error}
    if not isinstance(service, ActivationService):
        return 503, {
            "ok": False,
            "service": "activation",
            "reason": "activation_not_configured",
        }
    try:
        device_blocks = _load_device_blocks_state(service.device_blocks_path)
        # Pet authority has its own bounded serialization gate.  Readiness must
        # not queue behind bindings/trials writes, nor may a slow Pet chain hold
        # the global calculator activation lock and stall every HTTP worker.
        with service._pet_usage_transaction(now=int(time.time())):
            pass
    except StateFileError:
        return 503, {"ok": False, "service": "activation", "reason": "state_error"}
    if service._device_block_key_decision(device_blocks, device=None) == "state_error":
        return 503, {"ok": False, "service": "activation", "reason": "state_error"}
    return 200, {"ok": True, "service": "activation"}


def _vtuple(s) -> tuple:
    """版本号 -> 可比数字元组(每段取前导数字, 忽略 -hotfix 等后缀)。与客户端 version_check._vtuple 同语义:
    '0.2.6-hotfix1' -> (0,2,6); '0.2.10' > '0.2.2'。"""
    out = []
    for part in str(s or "").split("."):
        m = re.match(r"\d+", part.strip())
        out.append(int(m.group()) if m else 0)
    return tuple(out)


def _version_ge(a, b) -> bool:
    """a >= b(数字段比较, 段数不同右侧补 0)。空/None -> (0,) -> 恒小于任何真实版本 = 发旧 key(向后兼容)。"""
    ta, tb = _vtuple(a), _vtuple(b)
    n = max(len(ta), len(tb))
    ta = ta + (0,) * (n - len(ta))
    tb = tb + (0,) * (n - len(tb))
    return ta >= tb


_RELEASE_VERSION_RE = re.compile(
    r"^([0-9]{1,9})\.([0-9]{1,9})\.([0-9]{1,9})"
    r"(?:-(r|hotfix)([0-9]{1,9})(?:-r([0-9]{1,9}))?)?$",
    re.IGNORECASE | re.ASCII,
)


def _release_vtuple(value) -> Optional[tuple[int, int, int, int, int, int]]:
    """严格产品版本元组：``base < rN < hotfixN[-rN]``；畸形输入返回 None。

    该比较只供权威最低产品版本门使用。双密钥窗口继续使用上面的数字基线比较，
    避免把产品退役语义混入 ``--v2-min-version`` 并误发主密钥。
    """
    if type(value) is not str:  # noqa: E721 - authority boundary rejects subclasses
        return None
    match = _RELEASE_VERSION_RE.fullmatch(value)
    if match is None:
        return None
    major, minor, patch = (int(match.group(index)) for index in (1, 2, 3))
    suffix = (match.group(4) or "").lower()
    reroll = int(match.group(6) or 0)
    if reroll and suffix != "hotfix":
        return None
    channel = 2 if suffix == "hotfix" else (1 if suffix == "r" else 0)
    revision = int(match.group(5) or 0)
    return major, minor, patch, channel, revision, reroll


def _release_version_ge(value, minimum) -> bool:
    current = _release_vtuple(value)
    floor = _release_vtuple(minimum)
    return current is not None and floor is not None and current >= floor


def _load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


class StateFileError(RuntimeError):
    """An existing security-state file could not be read as a JSON object."""


def _load_state_dict(p: Path, *, missing_ok: bool = True) -> dict:
    """Strict reader for bindings/trials/paid_starts security state.

    Missing state is a legal first-run value. Existing unreadable, malformed or
    non-object state is an outage that must fail closed rather than silently reset
    paid clocks, bindings or trial history.
    """
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        if missing_ok:
            return {}
        raise StateFileError(f"missing state file: {p.name}") from exc
    except (OSError, UnicodeError) as exc:
        raise StateFileError(f"unreadable state file: {p.name}") from exc
    try:
        value = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise StateFileError(f"malformed state file: {p.name}") from exc
    if not isinstance(value, dict):
        raise StateFileError(f"state root is not an object: {p.name}")
    return value


def _require_state_key(value, *, state_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise StateFileError(f"invalid identity key in {state_name}")
    return value


def _load_bindings_state(p: Path) -> dict:
    """Validate every legacy/versioned binding without treating damage as absence."""
    value = _load_state_dict(p)
    for uid, record in value.items():
        _require_state_key(uid, state_name=p.name)
        try:
            activation_core.binding_state(record)
        except ValueError as exc:
            raise StateFileError(f"invalid device binding in {p.name}") from exc
    return value


def _load_paid_starts_state(p: Path, *, now: int) -> dict:
    """Validate the complete membership clock table, not only the requested uid."""
    value = _load_state_dict(p)
    for uid, started_at in value.items():
        _require_state_key(uid, state_name=p.name)
        try:
            activation_core._strict_state_timestamp(started_at, now=now)
        except (TypeError, ValueError, OverflowError) as exc:
            raise StateFileError(f"invalid membership clock in {p.name}") from exc
    return value


def _load_trials_state(p: Path, *, now: int) -> dict:
    """Validate fixed-window records and the two explicitly supported legacy forms."""
    value = _load_state_dict(p)
    for device, record in value.items():
        _require_state_key(device, state_name=p.name)
        if type(record) is int:
            try:
                activation_core._strict_state_timestamp(record, now=now)
            except (TypeError, ValueError, OverflowError) as exc:
                raise StateFileError(f"invalid trial clock in {p.name}") from exc
            continue
        if (
            type(record) is dict
            and frozenset(dict.keys(record)) == activation_core._TRIAL_RECORD_KEYS
        ):
            try:
                activation_core._strict_trial_record(record, now=now)
            except (TypeError, ValueError, OverflowError) as exc:
                raise StateFileError(f"invalid fixed trial in {p.name}") from exc
            continue
        if (
            type(record) is dict
            and frozenset(dict.keys(record))
            == activation_core._LEGACY_TRIAL_USAGE_KEYS
        ):
            used = record.get("used")
            last = record.get("last")
            if type(used) is not int or used < 0 or used >= now:
                raise StateFileError(f"invalid legacy trial usage in {p.name}")
            try:
                activation_core._strict_state_timestamp(last, now=now)
            except (TypeError, ValueError, OverflowError) as exc:
                raise StateFileError(f"invalid legacy trial clock in {p.name}") from exc
            continue
        raise StateFileError(f"invalid trial record in {p.name}")
    return value


def device_block_id(block_key: bytes, device: str) -> str:
    """Return the domain-separated identifier stored in the device denylist."""
    if not isinstance(block_key, bytes) or not block_key:
        raise ValueError("device block key material is unavailable")
    if not isinstance(device, str) or not device:
        raise ValueError("device identity is unavailable")
    return hmac.new(
        block_key,
        _DEVICE_BLOCK_DOMAIN + device.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _classify_device_block_key(secret: bytes, block_key: Optional[bytes]) -> str:
    """Classify the dedicated denylist key without ordinary equality timing.

    Missing is a supported bootstrap state while the denylist is empty.  A supplied
    key must be strong enough for HMAC use and must not reuse the activation secret.
    The equality decision compares fixed-size digests so the secret relationship is
    never tested with ordinary ``==``.
    """
    if block_key is None:
        return "missing"
    if not isinstance(block_key, bytes) or not isinstance(secret, bytes) or not secret:
        return "invalid"
    secret_digest = hashlib.sha256(_DEVICE_BLOCK_KEY_EQUALITY_DOMAIN + secret).digest()
    block_digest = hashlib.sha256(_DEVICE_BLOCK_KEY_EQUALITY_DOMAIN + block_key).digest()
    if hmac.compare_digest(secret_digest, block_digest):
        return "shared_activation_secret"
    if len(block_key) < _DEVICE_BLOCK_KEY_MIN_BYTES:
        return "invalid"
    return "valid"


def _valid_device_block_metadata(*, reason: str, evidence_ref: str, actor: str) -> bool:
    return (
        reason in _DEVICE_BLOCK_REASON_CODES
        and actor in _DEVICE_BLOCK_ACTOR_CODES
        and _DEVICE_BLOCK_EVIDENCE_RE.fullmatch(evidence_ref or "") is not None
    )


def _empty_device_blocks_state() -> dict:
    return {"schema_version": _DEVICE_BLOCK_SCHEMA_VERSION, "blocked": {}}


def _load_device_blocks_state(p: Path) -> dict:
    """Strictly load the privacy-preserving device denylist.

    Missing is the legal initial empty state.  An existing malformed state fails
    closed at activation time so corruption cannot silently unban a device.
    """
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _empty_device_blocks_state()
    except (OSError, UnicodeError) as exc:
        raise StateFileError(f"unreadable device block state: {p.name}") from exc
    try:
        value = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise StateFileError(f"malformed device block state: {p.name}") from exc
    if not isinstance(value, dict):
        raise StateFileError(f"device block state root is not an object: {p.name}")
    if value.get("schema_version") != _DEVICE_BLOCK_SCHEMA_VERSION:
        raise StateFileError(f"unsupported device block schema in {p.name}")
    blocked = value.get("blocked")
    if not isinstance(blocked, dict):
        raise StateFileError(f"invalid device block table in {p.name}")
    for block_id, record in blocked.items():
        if not isinstance(block_id, str) or _DEVICE_BLOCK_ID_RE.fullmatch(block_id) is None:
            raise StateFileError(f"invalid device block id in {p.name}")
        if not isinstance(record, dict):
            raise StateFileError(f"invalid device block record in {p.name}")
        blocked_at = record.get("blocked_at")
        if type(blocked_at) is not int or blocked_at <= 0:
            raise StateFileError(f"invalid device block timestamp in {p.name}")
        reason = record.get("reason")
        evidence_ref = record.get("evidence_ref")
        actor = record.get("actor")
        if not all(isinstance(item, str) for item in (reason, evidence_ref, actor)):
            raise StateFileError(f"invalid device block metadata in {p.name}")
        if not _valid_device_block_metadata(
            reason=reason,
            evidence_ref=evidence_ref,
            actor=actor,
        ):
            raise StateFileError(f"invalid device block metadata in {p.name}")
    return {"schema_version": _DEVICE_BLOCK_SCHEMA_VERSION, "blocked": dict(blocked)}


def _empty_pet_usage_state() -> dict:
    return {
        "schema_version": _PET_USAGE_LEDGER_SCHEMA_VERSION,
        "revision": 0,
        "records": {},
    }


def _exact_nonnegative_int(value, *, field: str) -> int:
    if type(value) is not int or value < 0:
        raise StateFileError(f"invalid {field} in pet usage state")
    return value


def _pet_usage_ledger_json(state: dict) -> str:
    return json.dumps(
        state,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _pet_usage_ledger_digest(state: dict) -> str:
    return hashlib.sha256(_pet_usage_ledger_json(state).encode("utf-8")).hexdigest()


def _pet_usage_genesis_tag(secret: bytes, *, ledger_sha256: str) -> str:
    if not isinstance(secret, bytes) or not secret:
        raise StateFileError("pet usage database key unavailable")
    payload = (
        f"{_PET_USAGE_DB_SCHEMA_VERSION}|0|{ledger_sha256}|genesis"
    ).encode("ascii")
    return hmac.new(
        secret,
        _PET_USAGE_ANCHOR_DOMAIN + payload,
        hashlib.sha256,
    ).hexdigest()


def _pet_usage_commit_tag(
    secret: bytes,
    *,
    revision: int,
    ledger_sha256: str,
    previous_tag: str,
) -> str:
    if not isinstance(secret, bytes) or not secret:
        raise StateFileError("pet usage database key unavailable")
    payload = (
        f"{_PET_USAGE_DB_SCHEMA_VERSION}|{revision}|"
        f"{ledger_sha256}|{previous_tag}"
    ).encode("ascii")
    return hmac.new(
        secret,
        _PET_USAGE_ANCHOR_DOMAIN + payload,
        hashlib.sha256,
    ).hexdigest()


def _pet_usage_head_path(database_path: Path) -> Path:
    path = Path(database_path)
    return path.with_name(f"{path.stem}.head.jsonl")


def _pet_usage_head_tag(secret: bytes, record: dict) -> str:
    if not isinstance(secret, bytes) or not secret:
        raise StateFileError("pet usage database key unavailable")
    payload = json.dumps(
        record,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hmac.new(
        secret,
        _PET_USAGE_HEAD_DOMAIN + payload,
        hashlib.sha256,
    ).hexdigest()


def _pet_usage_head_record(
    *,
    secret: bytes,
    sequence: int,
    kind: str,
    revision: int,
    ledger_sha256: str,
    chain_tag: str,
    previous_head_tag: str,
) -> dict:
    record = {
        "schema_version": _PET_USAGE_HEAD_SCHEMA_VERSION,
        "sequence": sequence,
        "kind": kind,
        "revision": revision,
        "ledger_sha256": ledger_sha256,
        "chain_tag": chain_tag,
        "previous_head_tag": previous_head_tag,
    }
    return {**record, "head_tag": _pet_usage_head_tag(secret, record)}


def _pet_usage_head_record_tuple(record: dict) -> tuple[int, str, str]:
    return (
        record["revision"],
        record["ledger_sha256"],
        record["chain_tag"],
    )


def _read_pet_usage_head_bytes(path: Path) -> bytes:
    path = Path(path)
    descriptor = None
    try:
        if path.is_symlink() or not path.is_file():
            raise StateFileError("pet usage external head missing")
        flags = os.O_RDWR | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        data = bytearray()
        while len(data) <= _PET_USAGE_HEAD_MAX_BYTES:
            chunk = os.read(descriptor, min(64 * 1024, _PET_USAGE_HEAD_MAX_BYTES + 1))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > _PET_USAGE_HEAD_MAX_BYTES:
            raise StateFileError("pet usage external head too large")
        if not data:
            raise StateFileError("pet usage external head empty")
        if data[-1:] != b"\n":
            last_complete = data.rfind(b"\n")
            if last_complete < 0:
                raise StateFileError("pet usage external head torn")
            os.ftruncate(descriptor, last_complete + 1)
            os.fsync(descriptor)
            del data[last_complete + 1 :]
        return bytes(data)
    except StateFileError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise StateFileError("pet usage external head unavailable") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _load_pet_usage_head_state(path: Path, *, secret: bytes) -> dict:
    data = _read_pet_usage_head_bytes(path)
    committed = None
    pending = None
    previous_head_tag = _PET_USAGE_HEAD_ZERO_TAG
    expected_sequence = 0
    for raw_line in data.splitlines():
        try:
            record = json.loads(raw_line.decode("ascii"))
        except (UnicodeError, TypeError, ValueError) as exc:
            raise StateFileError("malformed pet usage external head") from exc
        if (
            type(record) is not dict  # noqa: E721
            or set(record)
            != {
                "schema_version",
                "sequence",
                "kind",
                "revision",
                "ledger_sha256",
                "chain_tag",
                "previous_head_tag",
                "head_tag",
            }
            or type(record["schema_version"]) is not int  # noqa: E721
            or record["schema_version"] != _PET_USAGE_HEAD_SCHEMA_VERSION
            or type(record["sequence"]) is not int  # noqa: E721
            or record["sequence"] != expected_sequence
            or record["kind"] not in {"intent", "commit", "abort"}
            or type(record["revision"]) is not int  # noqa: E721
            or record["revision"] < 0
            or type(record["ledger_sha256"]) is not str  # noqa: E721
            or _PET_OPAQUE_ID_RE.fullmatch(record["ledger_sha256"]) is None
            or type(record["chain_tag"]) is not str  # noqa: E721
            or _PET_OPAQUE_ID_RE.fullmatch(record["chain_tag"]) is None
            or type(record["previous_head_tag"]) is not str  # noqa: E721
            or not hmac.compare_digest(record["previous_head_tag"], previous_head_tag)
            or type(record["head_tag"]) is not str  # noqa: E721
            or _PET_OPAQUE_ID_RE.fullmatch(record["head_tag"]) is None
        ):
            raise StateFileError("invalid pet usage external head record")
        unsigned = dict(record)
        actual_head_tag = unsigned.pop("head_tag")
        expected_head_tag = _pet_usage_head_tag(secret, unsigned)
        if not hmac.compare_digest(actual_head_tag, expected_head_tag):
            raise StateFileError("invalid pet usage external head authentication")

        value = _pet_usage_head_record_tuple(record)
        if expected_sequence == 0:
            if record["kind"] != "commit" or record["revision"] != 0:
                raise StateFileError("invalid pet usage external head genesis")
            committed = value
        elif record["kind"] == "intent":
            if (
                committed is None
                or pending is not None
                or record["revision"] != committed[0] + 1
            ):
                raise StateFileError("invalid pet usage external head intent")
            pending = value
        elif record["kind"] == "commit":
            if pending is None or value != pending:
                raise StateFileError("invalid pet usage external head commit")
            committed = value
            pending = None
        else:
            if pending is None or value != pending:
                raise StateFileError("invalid pet usage external head abort")
            pending = None
        previous_head_tag = actual_head_tag
        expected_sequence += 1

    if committed is None:
        raise StateFileError("pet usage external head has no genesis")
    return {
        "sequence": expected_sequence - 1,
        "head_tag": previous_head_tag,
        "committed": committed,
        "pending": pending,
    }


def _append_pet_usage_head_event(
    path: Path,
    *,
    secret: bytes,
    head_state: dict,
    kind: str,
    value: tuple[int, str, str],
) -> dict:
    record = _pet_usage_head_record(
        secret=secret,
        sequence=head_state["sequence"] + 1,
        kind=kind,
        revision=value[0],
        ledger_sha256=value[1],
        chain_tag=value[2],
        previous_head_tag=head_state["head_tag"],
    )
    encoded = (
        json.dumps(
            record,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        + b"\n"
    )
    descriptor = None
    try:
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            raise StateFileError("pet usage external head missing")
        flags = os.O_WRONLY | os.O_APPEND | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        offset = 0
        while offset < len(encoded):
            written = os.write(descriptor, encoded[offset:])
            if written <= 0:
                raise OSError("short pet usage external head append")
            offset += written
        os.fsync(descriptor)
    except StateFileError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise StateFileError("pet usage external head append failed") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return _load_pet_usage_head_state(path, secret=secret)


def _canonical_pet_usage_state(
    value,
    *,
    now: int,
    allow_empty: bool = False,
) -> dict:
    if not isinstance(value, dict):
        raise StateFileError("pet usage state root is not an object")
    if set(value) != {"schema_version", "revision", "records"}:
        raise StateFileError("invalid pet usage state fields")
    if value.get("schema_version") != _PET_USAGE_LEDGER_SCHEMA_VERSION:
        raise StateFileError("unsupported pet usage schema")
    revision = value.get("revision")
    if (
        type(revision) is not int  # noqa: E721 - bool is not a revision
        or revision < 0
        or (revision == 0 and not allow_empty)
    ):
        raise StateFileError("invalid pet usage revision")
    records = value.get("records")
    if not isinstance(records, dict):
        raise StateFileError("invalid pet usage records")
    if revision == 0 and records:
        raise StateFileError("non-empty initial pet usage state")

    clean_records = {}
    for entitlement_id, record in records.items():
        if (
            not isinstance(entitlement_id, str)
            or _PET_OPAQUE_ID_RE.fullmatch(entitlement_id) is None
            or not isinstance(record, dict)
        ):
            raise StateFileError("invalid pet usage record identity")
        if set(record) != {
            "kind",
            "total_seconds",
            "used_seconds",
            "device_id",
            "session_id",
            "last_seq",
            "last_command_visible",
            "visible",
            "last_event_at",
            "visibility_lease_exp",
        }:
            raise StateFileError("invalid pet usage record fields")
        if record.get("kind") not in {"trial", "quota"}:
            raise StateFileError("invalid pet usage kind")
        total_seconds = _exact_nonnegative_int(
            record.get("total_seconds"),
            field="total_seconds",
        )
        used_seconds = _exact_nonnegative_int(
            record.get("used_seconds"),
            field="used_seconds",
        )
        if total_seconds <= 0 or used_seconds > total_seconds:
            raise StateFileError("invalid pet usage balance")
        device_id = record.get("device_id")
        session_id = record.get("session_id")
        if (
            not isinstance(device_id, str)
            or _PET_OPAQUE_ID_RE.fullmatch(device_id) is None
            or (
                session_id is not None
                and (
                    not isinstance(session_id, str)
                    or _PET_OPAQUE_ID_RE.fullmatch(session_id) is None
                )
            )
        ):
            raise StateFileError("invalid pet usage private identity")
        last_seq = record.get("last_seq")
        if type(last_seq) is not int or last_seq < -1:
            raise StateFileError("invalid pet usage sequence")
        if type(record.get("last_command_visible")) is not bool:
            raise StateFileError("invalid pet usage command state")
        if type(record.get("visible")) is not bool:
            raise StateFileError("invalid pet usage visibility")
        last_event_at = _exact_nonnegative_int(
            record.get("last_event_at"),
            field="last_event_at",
        )
        visibility_lease_exp = _exact_nonnegative_int(
            record.get("visibility_lease_exp"),
            field="visibility_lease_exp",
        )
        if (
            last_event_at > now + 300
            or visibility_lease_exp > now + 300
            or visibility_lease_exp < last_event_at
        ):
            raise StateFileError("invalid pet usage visibility lease")
        clean_records[entitlement_id] = {
            "kind": record["kind"],
            "total_seconds": total_seconds,
            "used_seconds": used_seconds,
            "device_id": device_id,
            "session_id": session_id,
            "last_seq": last_seq,
            "last_command_visible": record["last_command_visible"],
            "visible": record["visible"],
            "last_event_at": last_event_at,
            "visibility_lease_exp": visibility_lease_exp,
        }
    return {
        "schema_version": _PET_USAGE_LEDGER_SCHEMA_VERSION,
        "revision": revision,
        "records": clean_records,
    }


def _normalized_sqlite_schema_sql(value) -> Optional[str]:
    if not isinstance(value, str):
        return None
    return " ".join(value.split())


def _pet_usage_database_schema_valid(connection: sqlite3.Connection) -> bool:
    main_objects = connection.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM main.sqlite_schema
        ORDER BY type, name
        """
    ).fetchall()
    temporary_objects = connection.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM temp.sqlite_schema
        ORDER BY type, name
        """
    ).fetchall()
    actual_objects = [
        (kind, name, table, _normalized_sqlite_schema_sql(sql))
        for kind, name, table, sql in main_objects
    ]
    expected_objects = [
        (
            "table",
            "pet_usage_commits",
            "pet_usage_commits",
            _normalized_sqlite_schema_sql(_PET_USAGE_COMMITS_TABLE_SQL),
        ),
        (
            "table",
            "pet_usage_state",
            "pet_usage_state",
            _normalized_sqlite_schema_sql(_PET_USAGE_STATE_TABLE_SQL),
        ),
    ]
    # The canonical WITHOUT ROWID schema creates no SQLite-owned index rows.
    # Consequently the explicit internal-object allowlist is empty: any extra
    # main/temp trigger, view, index or table (including a writable_schema row
    # whose name starts with sqlite_) changes authorization semantics.
    if actual_objects != expected_objects or temporary_objects:
        return False
    expected = {
        "pet_usage_state": [
            ("singleton", "INTEGER", 1, 1),
            ("schema_version", "INTEGER", 1, 0),
            ("revision", "INTEGER", 1, 0),
            ("ledger_json", "TEXT", 1, 0),
            ("ledger_sha256", "TEXT", 1, 0),
            ("chain_tag", "TEXT", 1, 0),
        ],
        "pet_usage_commits": [
            ("revision", "INTEGER", 1, 1),
            ("ledger_sha256", "TEXT", 1, 0),
            ("previous_tag", "TEXT", 1, 0),
            ("tag", "TEXT", 1, 0),
        ],
    }
    for table, columns in expected.items():
        actual = [
            (row[1], str(row[2]).upper(), row[3], row[5])
            for row in connection.execute(f'PRAGMA table_info("{table}")')
        ]
        if actual != columns:
            return False
    return True


def _configure_pet_usage_connection(
    connection: sqlite3.Connection,
    *,
    initializing: bool = False,
) -> None:
    connection.execute("PRAGMA busy_timeout=0")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.execute("PRAGMA synchronous=FULL")
    if initializing:
        mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()
    else:
        mode = connection.execute("PRAGMA journal_mode").fetchone()
    if not mode or str(mode[0]).lower() != "wal":
        raise StateFileError("pet usage database journal mode unavailable")
    synchronous = connection.execute("PRAGMA synchronous").fetchone()
    if not synchronous or synchronous[0] != 2:
        raise StateFileError("pet usage database durability unavailable")


def _connect_pet_usage_database(path: Path) -> sqlite3.Connection:
    path = Path(path)
    try:
        if path.is_symlink() or not path.is_file():
            raise StateFileError("pet usage database missing")
        database_uri = f"{path.resolve(strict=True).as_uri()}?mode=rw"
        connection = sqlite3.connect(
            database_uri,
            uri=True,
            timeout=0.0,
            isolation_level=None,
            check_same_thread=False,
        )
        _configure_pet_usage_connection(connection)
        return connection
    except StateFileError:
        raise
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        raise StateFileError("pet usage database unavailable") from exc


def _legacy_pet_usage_state_present(paths: tuple[Path, ...]) -> bool:
    try:
        return any(path.exists() or path.is_symlink() for path in paths)
    except OSError as exc:
        raise StateFileError("legacy pet usage state is not inspectable") from exc


def initialize_pet_usage_database(
    path: Path,
    *,
    secret: bytes,
    legacy_paths: tuple[Path, ...] = (),
) -> None:
    """Create an empty external Pet authority once, while the service is stopped.

    The live service never calls this function.  Existing SQLite or legacy JSON
    state requires an explicit migration decision and is never reset or imported.
    """
    path = Path(path)
    head_path = _pet_usage_head_path(path)
    if not isinstance(secret, bytes) or not secret:
        raise StateFileError("pet usage database key unavailable")
    for legacy in legacy_paths:
        try:
            if Path(legacy).exists() or Path(legacy).is_symlink():
                raise StateFileError("legacy pet usage state requires migration")
        except OSError as exc:
            raise StateFileError("legacy pet usage state is not inspectable") from exc
    if (
        path.exists()
        or path.is_symlink()
        or head_path.exists()
        or head_path.is_symlink()
    ):
        raise StateFileError("pet usage authority already exists")
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise StateFileError("pet usage StateDirectory missing")

    descriptor = None
    connection = None
    try:
        descriptor = os.open(
            path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        os.close(descriptor)
        descriptor = None
        connection = sqlite3.connect(
            f"{path.resolve(strict=True).as_uri()}?mode=rw",
            uri=True,
            timeout=0.0,
            isolation_level=None,
            check_same_thread=False,
        )
        _configure_pet_usage_connection(connection, initializing=True)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(_PET_USAGE_STATE_TABLE_SQL)
        connection.execute(_PET_USAGE_COMMITS_TABLE_SQL)
        connection.execute(
            f"PRAGMA application_id={_PET_USAGE_DB_APPLICATION_ID}"
        )
        connection.execute(f"PRAGMA user_version={_PET_USAGE_DB_SCHEMA_VERSION}")
        state = _empty_pet_usage_state()
        ledger_json = _pet_usage_ledger_json(state)
        ledger_sha256 = hashlib.sha256(ledger_json.encode("utf-8")).hexdigest()
        chain_tag = _pet_usage_genesis_tag(
            secret,
            ledger_sha256=ledger_sha256,
        )
        connection.execute(
            """
            INSERT INTO pet_usage_state (
                singleton, schema_version, revision, ledger_json,
                ledger_sha256, chain_tag
            ) VALUES (1, ?, 0, ?, ?, ?)
            """,
            (
                _PET_USAGE_DB_SCHEMA_VERSION,
                ledger_json,
                ledger_sha256,
                chain_tag,
            ),
        )
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        os.chmod(path, 0o600)
        genesis = _pet_usage_head_record(
            secret=secret,
            sequence=0,
            kind="commit",
            revision=0,
            ledger_sha256=ledger_sha256,
            chain_tag=chain_tag,
            previous_head_tag=_PET_USAGE_HEAD_ZERO_TAG,
        )
        encoded_genesis = (
            json.dumps(
                genesis,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
            + b"\n"
        )
        descriptor = os.open(
            head_path,
            os.O_CREAT
            | os.O_EXCL
            | os.O_WRONLY
            | getattr(os, "O_BINARY", 0),
            0o600,
        )
        offset = 0
        while offset < len(encoded_genesis):
            written = os.write(descriptor, encoded_genesis[offset:])
            if written <= 0:
                raise OSError("short pet usage external head initialization")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.chmod(head_path, 0o600)
        _load_pet_usage_head_state(head_path, secret=secret)
    except Exception as exc:
        if connection is not None:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            try:
                connection.close()
            except sqlite3.Error:
                pass
            connection = None
        for created in (
            head_path,
            path,
            path.with_name(f"{path.name}-wal"),
            path.with_name(f"{path.name}-shm"),
        ):
            try:
                if created.exists() or created.is_symlink():
                    created.unlink()
            except OSError:
                pass
        if isinstance(exc, StateFileError):
            raise
        raise StateFileError("unable to initialize pet usage database") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if connection is not None:
            connection.close()


def _load_pet_usage_sqlite_state(
    connection: sqlite3.Connection,
    *,
    secret: bytes,
    now: int,
) -> tuple[dict, str]:
    integrity = connection.execute("PRAGMA quick_check(1)").fetchall()
    if integrity != [("ok",)]:
        raise StateFileError("pet usage database integrity failure")
    application_id = connection.execute("PRAGMA application_id").fetchone()
    user_version = connection.execute("PRAGMA user_version").fetchone()
    if (
        application_id != (_PET_USAGE_DB_APPLICATION_ID,)
        or user_version != (_PET_USAGE_DB_SCHEMA_VERSION,)
        or not _pet_usage_database_schema_valid(connection)
    ):
        raise StateFileError("unsupported pet usage database schema")
    rows = connection.execute(
        """
        SELECT singleton, schema_version, revision, ledger_json,
               ledger_sha256, chain_tag
        FROM pet_usage_state
        ORDER BY singleton
        """
    ).fetchall()
    if len(rows) != 1 or rows[0][0] != 1:
        raise StateFileError("invalid pet usage database state row")
    (
        _singleton,
        schema_version,
        revision,
        ledger_json,
        ledger_sha256,
        chain_tag,
    ) = rows[0]
    if (
        type(schema_version) is not int  # noqa: E721
        or schema_version != _PET_USAGE_DB_SCHEMA_VERSION
        or type(revision) is not int  # noqa: E721
        or revision < 0
        or not isinstance(ledger_json, str)
        or len(ledger_json.encode("utf-8")) > _PET_USAGE_DB_MAX_LEDGER_BYTES
        or not isinstance(ledger_sha256, str)
        or _PET_OPAQUE_ID_RE.fullmatch(ledger_sha256) is None
        or not isinstance(chain_tag, str)
        or _PET_OPAQUE_ID_RE.fullmatch(chain_tag) is None
    ):
        raise StateFileError("invalid pet usage database state")
    try:
        value = json.loads(ledger_json)
    except (TypeError, ValueError) as exc:
        raise StateFileError("malformed pet usage database ledger") from exc
    state = _canonical_pet_usage_state(
        value,
        now=now,
        allow_empty=True,
    )
    if state["revision"] != revision or _pet_usage_ledger_json(state) != ledger_json:
        raise StateFileError("non-canonical pet usage database ledger")
    expected_digest = _pet_usage_ledger_digest(state)
    if not hmac.compare_digest(ledger_sha256, expected_digest):
        raise StateFileError("pet usage database ledger digest mismatch")

    previous_tag = _pet_usage_genesis_tag(
        secret,
        ledger_sha256=(
            _pet_usage_ledger_digest(_empty_pet_usage_state())
        ),
    )
    commits = connection.execute(
        """
        SELECT revision, ledger_sha256, previous_tag, tag
        FROM pet_usage_commits
        ORDER BY revision
        """
    ).fetchall()
    if len(commits) != revision:
        raise StateFileError("pet usage database commit history mismatch")
    for expected_revision, entry in enumerate(commits, start=1):
        entry_revision, entry_digest, entry_previous, entry_tag = entry
        if (
            type(entry_revision) is not int  # noqa: E721
            or entry_revision != expected_revision
            or not isinstance(entry_digest, str)
            or _PET_OPAQUE_ID_RE.fullmatch(entry_digest) is None
            or not isinstance(entry_previous, str)
            or not hmac.compare_digest(entry_previous, previous_tag)
            or not isinstance(entry_tag, str)
            or _PET_OPAQUE_ID_RE.fullmatch(entry_tag) is None
        ):
            raise StateFileError("invalid pet usage database commit chain")
        expected_tag = _pet_usage_commit_tag(
            secret,
            revision=entry_revision,
            ledger_sha256=entry_digest,
            previous_tag=entry_previous,
        )
        if not hmac.compare_digest(entry_tag, expected_tag):
            raise StateFileError("invalid pet usage database authentication")
        previous_tag = entry_tag
    if (
        not hmac.compare_digest(chain_tag, previous_tag)
        or (
            revision == 0
            and not hmac.compare_digest(chain_tag, previous_tag)
        )
        or (
            revision > 0
            and not hmac.compare_digest(commits[-1][1], ledger_sha256)
        )
    ):
        raise StateFileError("pet usage database chain head mismatch")
    return state, chain_tag


def _prepare_pet_usage_sqlite_revision(
    *,
    secret: bytes,
    state: dict,
    previous_tag: str,
) -> tuple[dict, str, str]:
    next_state = {
        "schema_version": _PET_USAGE_LEDGER_SCHEMA_VERSION,
        "revision": state["revision"] + 1,
        "records": state["records"],
    }
    ledger_json = _pet_usage_ledger_json(next_state)
    if len(ledger_json.encode("utf-8")) > _PET_USAGE_DB_MAX_LEDGER_BYTES:
        raise StateFileError("pet usage database ledger too large")
    ledger_sha256 = hashlib.sha256(ledger_json.encode("utf-8")).hexdigest()
    tag = _pet_usage_commit_tag(
        secret,
        revision=next_state["revision"],
        ledger_sha256=ledger_sha256,
        previous_tag=previous_tag,
    )
    return next_state, ledger_sha256, tag


def _write_pet_usage_sqlite_revision(
    connection: sqlite3.Connection,
    *,
    secret: bytes,
    state: dict,
    previous_tag: str,
) -> tuple[dict, str]:
    next_state, ledger_sha256, tag = _prepare_pet_usage_sqlite_revision(
        secret=secret,
        state=state,
        previous_tag=previous_tag,
    )
    ledger_json = _pet_usage_ledger_json(next_state)
    insert_cursor = connection.execute(
        """
        INSERT INTO pet_usage_commits (
            revision, ledger_sha256, previous_tag, tag
        ) VALUES (?, ?, ?, ?)
        """,
        (
            next_state["revision"],
            ledger_sha256,
            previous_tag,
            tag,
        ),
    )
    if insert_cursor.rowcount != 1:
        raise StateFileError("pet usage database commit row unavailable")
    cursor = connection.execute(
        """
        UPDATE pet_usage_state
        SET revision = ?, ledger_json = ?, ledger_sha256 = ?, chain_tag = ?
        WHERE singleton = 1 AND revision = ?
        """,
        (
            next_state["revision"],
            ledger_json,
            ledger_sha256,
            tag,
            state["revision"],
        ),
    )
    if cursor.rowcount != 1:
        raise StateFileError("pet usage database concurrent revision")
    return next_state, tag


def _verify_pet_usage_sqlite_revision(
    connection: sqlite3.Connection,
    *,
    secret: bytes,
    now: int,
    expected_state: dict,
    expected_chain_tag: str,
) -> tuple[dict, str]:
    actual_state, actual_chain_tag = _load_pet_usage_sqlite_state(
        connection,
        secret=secret,
        now=now,
    )
    if (
        _pet_usage_ledger_json(actual_state)
        != _pet_usage_ledger_json(expected_state)
        or not hmac.compare_digest(actual_chain_tag, expected_chain_tag)
    ):
        raise StateFileError("pet usage database committed state mismatch")
    return actual_state, actual_chain_tag


def _reconcile_pet_usage_external_head(
    path: Path,
    *,
    secret: bytes,
    database_state: dict,
    database_chain_tag: str,
) -> dict:
    head_state = _load_pet_usage_head_state(path, secret=secret)
    database_value = (
        database_state["revision"],
        _pet_usage_ledger_digest(database_state),
        database_chain_tag,
    )
    pending = head_state["pending"]
    if pending is not None:
        if database_value == pending:
            head_state = _append_pet_usage_head_event(
                path,
                secret=secret,
                head_state=head_state,
                kind="commit",
                value=pending,
            )
        elif database_value == head_state["committed"]:
            head_state = _append_pet_usage_head_event(
                path,
                secret=secret,
                head_state=head_state,
                kind="abort",
                value=pending,
            )
        else:
            raise StateFileError("pet usage database/external head recovery mismatch")
    if head_state["pending"] is not None or database_value != head_state["committed"]:
        raise StateFileError("pet usage database/external head mismatch")
    return head_state


class _PetUsageSqliteTransaction:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        secret: bytes,
        head_path: Path,
        head_state: dict,
        state: dict,
        chain_tag: str,
        now: int,
    ) -> None:
        self.connection = connection
        self.secret = secret
        self.head_path = Path(head_path)
        self.head_state = head_state
        self.state = state
        self.chain_tag = chain_tag
        self.now = now
        self.committed = False

    def commit(self) -> None:
        if self.committed:
            raise StateFileError("pet usage database transaction already committed")
        prepared_state, prepared_digest, prepared_chain_tag = (
            _prepare_pet_usage_sqlite_revision(
                secret=self.secret,
                state=self.state,
                previous_tag=self.chain_tag,
            )
        )
        prepared_value = (
            prepared_state["revision"],
            prepared_digest,
            prepared_chain_tag,
        )
        self.head_state = _append_pet_usage_head_event(
            self.head_path,
            secret=self.secret,
            head_state=self.head_state,
            kind="intent",
            value=prepared_value,
        )
        next_state, next_chain_tag = _write_pet_usage_sqlite_revision(
            self.connection,
            secret=self.secret,
            state=self.state,
            previous_tag=self.chain_tag,
        )
        if (
            _pet_usage_ledger_json(next_state)
            != _pet_usage_ledger_json(prepared_state)
            or not hmac.compare_digest(next_chain_tag, prepared_chain_tag)
        ):
            raise StateFileError("pet usage prepared revision mismatch")
        # Verify all row effects and the authenticated chain before COMMIT so
        # trigger-driven partial writes can still be rolled back.
        _verify_pet_usage_sqlite_revision(
            self.connection,
            secret=self.secret,
            now=self.now,
            expected_state=next_state,
            expected_chain_tag=next_chain_tag,
        )
        self.connection.commit()
        # Reacquire the cross-process database write lock before finalizing the
        # external intent. Another service may have recovered this exact commit
        # (or advanced the authority again) in the post-COMMIT window, so the
        # pre-COMMIT head snapshot is no longer authoritative.
        self.connection.execute("BEGIN IMMEDIATE")
        persisted_state, persisted_chain_tag = _load_pet_usage_sqlite_state(
            self.connection,
            secret=self.secret,
            now=self.now,
        )
        reconciled = _reconcile_pet_usage_external_head(
            self.head_path,
            secret=self.secret,
            database_state=persisted_state,
            database_chain_tag=persisted_chain_tag,
        )
        if reconciled["pending"] is not None:
            raise StateFileError("pet usage external head remains pending")
        self.head_state = reconciled
        if persisted_state["revision"] != next_state["revision"]:
            # The current authority is newer than this request. Do not publish
            # or return a stale lease; the newer revision remains intact and
            # this request fails closed at the Pet-only boundary.
            raise StateFileError("pet usage database transaction superseded")

        # Verify the exact persisted state before the caller can construct a
        # signed Pet lease. The database lock keeps DB/head stable here.
        persisted_state, persisted_chain_tag = _verify_pet_usage_sqlite_revision(
            self.connection,
            secret=self.secret,
            now=self.now,
            expected_state=next_state,
            expected_chain_tag=next_chain_tag,
        )
        self.state.clear()
        self.state.update(persisted_state)
        self.chain_tag = persisted_chain_tag
        self.committed = True


@contextmanager
def _pet_usage_sqlite_transaction(
    path: Path,
    *,
    secret: bytes,
    now: int,
) -> Iterator[_PetUsageSqliteTransaction]:
    connection = None
    try:
        connection = _connect_pet_usage_database(path)
        connection.execute("BEGIN IMMEDIATE")
        state, chain_tag = _load_pet_usage_sqlite_state(
            connection,
            secret=secret,
            now=now,
        )
        head_path = _pet_usage_head_path(path)
        head_state = _reconcile_pet_usage_external_head(
            head_path,
            secret=secret,
            database_state=state,
            database_chain_tag=chain_tag,
        )
        # BEGIN IMMEDIATE alone may still succeed on a read-only SQLite URI.
        # A no-op UPDATE inside the same transaction proves that the authority
        # can actually commit future quota changes; rollback below keeps
        # readiness non-mutating when no Pet state change is requested.
        writable = connection.execute(
            """
            UPDATE pet_usage_state
            SET revision = revision
            WHERE singleton = 1
            """
        )
        if writable.rowcount != 1:
            raise StateFileError("pet usage database state row unavailable")
        transaction = _PetUsageSqliteTransaction(
            connection,
            secret=secret,
            head_path=head_path,
            head_state=head_state,
            state=state,
            chain_tag=chain_tag,
            now=now,
        )
        yield transaction
    except StateFileError:
        raise
    except (OSError, sqlite3.Error, TypeError, ValueError, OverflowError) as exc:
        raise StateFileError("pet usage database transaction failed") from exc
    finally:
        if connection is not None:
            try:
                if connection.in_transaction:
                    connection.rollback()
            except sqlite3.Error:
                pass
            connection.close()


def _pet_opaque_id(secret: bytes, domain: bytes, value: str) -> str:
    if not isinstance(secret, bytes) or not secret:
        raise ValueError("pet usage signing key unavailable")
    if not isinstance(value, str) or not value:
        raise ValueError("pet usage identity unavailable")
    return hmac.new(
        secret,
        domain + value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _pet_entitlement_id(secret: bytes, *, token: Optional[str], device: str) -> str:
    if token:
        payload = token.rsplit(".", 1)[0]
        return _pet_opaque_id(secret, _PET_USAGE_DOMAIN, payload)
    return _pet_opaque_id(secret, _PET_USAGE_DOMAIN, f"trial|{device}")


def _pet_device_id(secret: bytes, device: str) -> str:
    return _pet_opaque_id(secret, _PET_DEVICE_DOMAIN, device)


def _pet_session_id(secret: bytes, session_id: str) -> str:
    return _pet_opaque_id(secret, _PET_SESSION_DOMAIN, session_id)


def _validate_pet_usage_event(raw) -> Optional[dict]:
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != _PET_USAGE_EVENT_KEYS:
        raise ValueError("invalid pet usage event")
    session_id = raw.get("session_id")
    seq = raw.get("seq")
    visible = raw.get("visible")
    if (
        not isinstance(session_id, str)
        or _PET_SESSION_ID_RE.fullmatch(session_id) is None
        or type(seq) is not int
        or seq < 0
        or type(visible) is not bool
    ):
        raise ValueError("invalid pet usage event")
    return {
        "session_id": session_id,
        "seq": seq,
        "visible": visible,
    }


def _atomic_write_json(p: Path, obj) -> None:
    """原子写 JSON: 同目录写临时文件 + os.replace(原子重命名)。
    非原子 write_text(截断+写)若写到一半崩溃/磁盘满/重启被 kill, 会把 paid_starts/bindings/
    trials 留成损坏 JSON -> 下次 _load_json 解析失败回退 {} -> 全体状态丢失(会员时钟集体重置/
    设备绑定重置/试用可重刷)。os.replace 保证读者要么看到旧完整文件、要么看到新完整文件。"""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def _load_lines(p: Path) -> set[str]:
    """Load the revocation set fail-closed when an existing file is unreadable."""
    try:
        return {ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()}
    except FileNotFoundError:
        return set()
    except (OSError, UnicodeError) as exc:
        raise StateFileError(f"unreadable revocation state: {p.name}") from exc


def _authority_file_stamp(path: Path) -> tuple:
    """Return a cheap identity/change stamp for one fixed authority file.

    ``mtime`` alone is not an authority identity: an operator can restore it while
    replacing a file.  Identity, size and change-time close that ordinary rollback
    path on both Windows and POSIX.  A supported in-process writer additionally
    refreshes the verified cache explicitly after its atomic commit, so it does not
    depend on any filesystem timestamp granularity.
    """
    try:
        stat = path.stat()
    except FileNotFoundError:
        return ("missing",)
    except OSError as exc:
        raise StateFileError(f"unreadable authority metadata: {path.name}") from exc
    return (
        "present",
        stat.st_dev,
        stat.st_ino,
        stat.st_mode,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
    )


class ActivationService:
    def __init__(
        self,
        *,
        secret: bytes,
        master_key_b64: str,
        bindings_path: Path,
        revoked_path: Path,
        telemetry_path: Path,
        trials_path: Path,
        paid_starts_path: Optional[Path] = None,
        pet_usage_db_path: Optional[Path] = None,
        pet_usage_path: Optional[Path] = None,
        pet_usage_anchor_path: Optional[Path] = None,
        pet_usage_lease_seconds: int = _PET_USAGE_LEASE_SECONDS,
        device_blocks_path: Optional[Path] = None,
        device_block_key: Optional[bytes] = None,
        lease_days: int = _LEASE_GRACE_DAYS,
        trial_hours: float = activation_core.DEFAULT_TRIAL_HOURS,
        master_key_v2_b64: Optional[str] = None,
        v2_min_version: str = _V2_MIN_VERSION,
        min_client_security_epoch: int = 0,
        min_client_version: Optional[str] = None,
        upload_security_epoch: int = 0,
        upload_lease_seconds: int = upload_lease.DEFAULT_TTL_SECONDS,
        trial_grandfather_cutover_at: Optional[int] = None,
        trial_grandfather_hours: Optional[float] = None,
        membership_store_path: Optional[Path] = None,
        membership_gate_seconds: float = 2.0,
        membership_fault_hook=None,
    ) -> None:
        require_activation_core_compatible()
        self.secret = secret
        self.master_key_b64 = master_key_b64
        # 轮换后的新主密钥(None = 未轮换 = _key_for_client 恒发旧 key = 行为与轮换前等价)。
        self.master_key_v2_b64 = (master_key_v2_b64 or "").strip() or None
        self.v2_min_version = v2_min_version
        self.min_client_security_epoch = max(0, int(min_client_security_epoch))
        if type(upload_security_epoch) is not int or upload_security_epoch < 0:  # noqa: E721
            raise ValueError("invalid upload security epoch")
        if (
            type(upload_lease_seconds) is not int
            or upload_lease_seconds <= 0
            or upload_lease_seconds > upload_lease.MAX_TTL_SECONDS
        ):
            raise ValueError("invalid upload lease lifetime")
        self.upload_security_epoch = upload_security_epoch
        self.upload_lease_seconds = upload_lease_seconds
        if min_client_version is None:
            configured_min_version = None
        elif type(min_client_version) is not str:  # noqa: E721
            raise ValueError("invalid min_client_version")
        elif not min_client_version:
            configured_min_version = None
        elif _release_vtuple(min_client_version) is None:
            raise ValueError("invalid min_client_version")
        else:
            configured_min_version = min_client_version
        # None = 默认兼容，不改变已部署行为。只有发布侧显式配置后才成为权威退役门。
        self.min_client_version = configured_min_version
        self.bindings_path = Path(bindings_path)
        self.revoked_path = Path(revoked_path)
        self.telemetry_path = Path(telemetry_path)
        self.trials_path = Path(trials_path)
        # 限期会员【首次激活起算】的起点表(uid->首次激活ts)。默认放 bindings 同目录。
        self.paid_starts_path = Path(paid_starts_path) if paid_starts_path is not None \
            else self.bindings_path.with_name("paid_starts.json")
        self.pet_usage_path = (
            Path(pet_usage_path)
            if pet_usage_path is not None
            else self.bindings_path.with_name("pet_usage.json")
        )
        self.pet_usage_anchor_path = (
            Path(pet_usage_anchor_path)
            if pet_usage_anchor_path is not None
            else self.pet_usage_path.with_name(
                f"{self.pet_usage_path.stem}.anchor.jsonl"
            )
        )
        self.pet_usage_db_path = (
            Path(pet_usage_db_path)
            if pet_usage_db_path is not None
            else _PET_USAGE_DB_DEFAULT_PATH
        )
        self.pet_usage_head_path = _pet_usage_head_path(self.pet_usage_db_path)
        if (
            type(pet_usage_lease_seconds) is not int
            or pet_usage_lease_seconds < 5
            or pet_usage_lease_seconds > 60
        ):
            raise ValueError("invalid pet usage lease")
        self.pet_usage_lease_seconds = pet_usage_lease_seconds
        self.device_blocks_path = (
            Path(device_blocks_path)
            if device_blocks_path is not None
            else self.bindings_path.with_name("device_blocks.json")
        )
        self.device_block_key_state = _classify_device_block_key(
            self.secret,
            device_block_key,
        )
        # None means the key file was genuinely absent.  Preserve supplied empty
        # bytes long enough for the classifier to reject them; truthiness here
        # would turn an existing blank security key into a legal bootstrap state.
        self.device_block_key = (
            bytes(device_block_key) if isinstance(device_block_key, bytes) else None
        )
        self.lease_days = lease_days
        try:
            new_window = activation_core._strict_trial_window_seconds(trial_hours)
            activation_core._strict_trial_cutover(
                trial_grandfather_cutover_at,
                trial_grandfather_hours,
                new_window=new_window,
                now=int(time.time()),
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("invalid trial duration configuration") from exc
        self.trial_hours = trial_hours
        self.trial_grandfather_cutover_at = trial_grandfather_cutover_at
        self.trial_grandfather_hours = trial_grandfather_hours
        # bindings/trials 的读-改-写-落盘串行化(#2/#6): collector 用 ThreadingHTTPServer, 多线程
        # 并发 /activate 会 read-modify-write 同一 dict + json.dumps 落盘 -> 竞态(绑多机 / dict 崩 /
        # lost-update)。一把锁包住; 且锁内【重读磁盘】(权威 + 拿到并发/人工改绑 revoked 后的最新态)。
        self._lock = threading.Lock()
        # This is a fixed four-file parsed-snapshot cache, never an authorization
        # decision cache.  Every request still verifies the token/device and stats
        # every authority file; only unchanged, completely validated file contents
        # skip JSON/text parsing.  Fixed paths keep memory bounded.
        self._paid_authority_paths = frozenset(
            {
                self.bindings_path,
                self.paid_starts_path,
                self.revoked_path,
                self.device_blocks_path,
            }
        )
        self._paid_authority_cache: dict[Path, tuple[tuple, object]] = {}
        # Pet SQLite/head I/O is independent of bindings/trials.  Keep one
        # in-process writer, but bound contention so a slow filesystem or a
        # first-deploy reconnect storm degrades Pet only instead of queueing
        # every calculator activation and readiness request.
        self._pet_usage_lock = threading.Lock()
        # The external journal detects database-only rollback across restarts.
        # This in-process floor additionally rejects restoring both files while
        # the same authority process is alive; full StateDirectory snapshot
        # rollback across a service restart remains an explicit operator boundary.
        self._pet_usage_revision_floor = 0
        if type(membership_gate_seconds) not in (int, float) or not 0 < membership_gate_seconds <= 10:
            raise ValueError("invalid membership gate deadline")
        self.membership_gate_seconds = float(membership_gate_seconds)
        self._membership_fault_hook = membership_fault_hook
        if membership_store_path is not None and membership_card_store is None:
            raise ValueError("membership module unavailable")
        self.membership_store = (
            membership_card_store.MembershipCardStore(Path(membership_store_path))
            if membership_store_path is not None else None
        )
        self._membership_gate_path = (
            Path(str(self.membership_store.path) + ".service.lock") if self.membership_store is not None else None
        )
        self._membership_pending_path = (
            Path(str(self.membership_store.path) + ".projection.json") if self.membership_store is not None else None
        )
        try:
            self.bindings = _load_bindings_state(self.bindings_path)
        except StateFileError:
            # Keep construction available so requests can return structured 503;
            # every authoritative decision below re-reads the file strictly.
            self.bindings = {}

    @contextmanager
    def _membership_request_gate(self):
        """Cooperative cross-process qualifier/legacy projection gate, bounded."""
        if self.membership_store is None:
            yield
            return
        path = self._membership_gate_path
        if path.is_symlink():
            raise StateFileError("membership gate unavailable")
        stream = None
        descriptor = None
        acquired = False
        try:
            # Byte-range locks may extend beyond EOF on Windows. The gate is
            # coordination only: do not race an initial buffered write before
            # locking. Existing one-byte gate files remain compatible.
            descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0), 0o600)
            stream = os.fdopen(descriptor, "r+b", buffering=0)
            descriptor = None
            deadline = time.monotonic() + self.membership_gate_seconds
            while True:
                try:
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise StateFileError("membership gate busy") from None
                    time.sleep(0.01)
            yield
        except OSError:
            raise StateFileError("membership gate unavailable") from None
        finally:
            if stream is not None:
                try:
                    if acquired:
                        stream.seek(0)
                        if os.name == "nt":
                            import msvcrt
                            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                finally:
                    stream.close()
            elif descriptor is not None:
                os.close(descriptor)

    def _membership_fault(self, phase: str) -> None:
        if self._membership_fault_hook is not None:
            self._membership_fault_hook(phase)

    def _membership_verified_card(self, token: str, *, bindings: dict, starts: dict):
        info = activation_core.verify_token(token, self.secret)
        if info is None or not info.get("uid"):
            raise StateFileError("membership credential unavailable")
        duration = None if info["exp"] is None else info["exp"] - info["iat"]
        if duration is not None and (type(duration) is not int or duration <= 0):
            raise StateFileError("membership duration unavailable")
        used = info["uid"] in starts or info["uid"] in bindings
        return info, membership_card_store.VerifiedCard(
            info["uid"], hashlib.sha256(token.rsplit(".", 1)[0].encode("utf-8")).hexdigest(),
            duration, "roaming" if info["roaming"] else "bound", "activated" if used else "unused",
        )

    def _membership_binding_reference(self, uid: str, device: Optional[str]) -> str:
        value = "unbound|" + uid if device is None else "device|" + device
        return hmac.new(self.secret, b"membership-binding-reference-v1|" + value.encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def _membership_authority(snapshot):
        return activation_core.CanonicalMembershipAuthority(
            snapshot.account_uid, snapshot.membership_exp, snapshot.device_permission, snapshot.revision,
            snapshot.current_plan_kind, snapshot.current_plan_exp, snapshot.my_records_exp,
            snapshot.private_dashboard_exp, snapshot.full_pet_exp, snapshot.pet_purchased_quota_seconds,
        )

    def _membership_seed(self, card, *, start: int, bound_record, token: str, now: int):
        if card.plan_kind is None:
            raise StateFileError("canonical membership seed unavailable")
        bound_device, used = activation_core.binding_state(bound_record) if bound_record is not None else (None, None)
        if type(bound_record) is str:
            used = None
        return membership_card_store.ValidatedMembershipSeed(
            card.card_uid, card, start, start + card.duration_seconds,
            (membership_card_store.BenefitSegment(card.plan_kind, start, start + card.duration_seconds, card.payload_fingerprint),),
            membership_card_store.BindingObservation(
                self._membership_binding_reference(card.card_uid, bound_device), used,
                "existing-versioned-counter" if type(bound_record) is dict else "legacy-unrecorded-counter",
            ),
            43_200 if card.plan_kind == "30d" else 0, None,
            _pet_entitlement_id(self.secret, token=token, device=bound_device or "unbound"), now, True,
        )

    def _membership_sources_valid(self, uid: str, revoked: set) -> bool:
        if uid in revoked:
            return False
        return all(receipt.card_uid not in revoked for receipt in self.membership_store.claims_for_account(uid))

    def revoked_canonical_uid_hashes(self) -> Optional[frozenset[str]]:
        """Private cookie adapter: named revoked cards plus validated canonical.

        No account enumeration, writes or raw UID inventory returned. None is
        authority unavailable and MUST fail the caller's cookie check closed.
        """
        try:
            with self._membership_request_gate(), self._lock:
                revoked = self._load_paid_authority_snapshot(self.revoked_path, now=int(time.time()))
                result = {hashlib.sha256(uid.encode("utf-8")).hexdigest()[:16] for uid in revoked}
                if self.membership_store is not None:
                    # Empty revoked is not permission to ignore corrupt/missing
                    # configured authority. Public construction checks schema
                    # without enumerating any member account.
                    membership_card_store.MembershipCardStore(
                        self.membership_store.path, timeout_seconds=self.membership_store.timeout_seconds)
                    for uid in revoked:
                        claim = self.membership_store.claim_for_card_uid(uid)
                        if claim is not None:
                            result.add(hashlib.sha256(claim.account_uid.encode("utf-8")).hexdigest()[:16])
                return frozenset(result)
        except Exception:
            return None

    def _membership_pending(self) -> dict:
        if not self._membership_pending_path.exists():
            return {"schema_version": 1, "entries": {}}
        raw = _load_state_dict(self._membership_pending_path)
        if set(raw) != {"schema_version", "entries"} or type(raw["schema_version"]) is not int or raw["schema_version"] != 1 or type(raw["entries"]) is not dict:
            raise StateFileError("membership projection schema unavailable")
        for fingerprint, entry in raw["entries"].items():
            if _PET_OPAQUE_ID_RE.fullmatch(fingerprint) is None or type(entry) is not dict or set(entry) != {"card", "start", "binding_before", "binding_after"}:
                raise StateFileError("membership projection entry unavailable")
            if type(entry["card"]) is not dict or set(entry["card"]) != set(membership_card_store.VerifiedCard.__dataclass_fields__):
                raise StateFileError("membership projection card unavailable")
            card = membership_card_store.VerifiedCard(**entry["card"])
            # Public lookup validates the exact card fields without guessing use.
            self.membership_store.lookup_claim(card)
            if card.payload_fingerprint != fingerprint or type(entry["start"]) is not int or entry["start"] <= 0:
                raise StateFileError("membership projection identity unavailable")
            for record in (entry["binding_before"], entry["binding_after"]):
                if record is not None:
                    activation_core.binding_state(record)
        return raw

    def _membership_write_pending(self, pending: dict) -> None:
        _atomic_write_json(self._membership_pending_path, pending)
        if self._membership_pending() != pending:
            raise StateFileError("membership projection write unavailable")

    def _membership_recover_projection(self, uid: str, *, now: int) -> None:
        """Recover only this account; unrelated pending IO does not reset it."""
        pending = self._membership_pending()
        touched = False
        for fingerprint, entry in tuple(pending["entries"].items()):
            if entry["card"]["card_uid"] != uid:
                continue
            card = membership_card_store.VerifiedCard(**entry["card"])
            claim = self.membership_store.lookup_claim(card)
            if claim is None:
                # No durable SQL claim: JSON projections have not begun.
                del pending["entries"][fingerprint]
                touched = True
                continue
            snapshot = self.membership_store.read(uid, now=now)
            if claim.operation != "first_activation" or claim.account_uid != uid or snapshot is None or entry["start"] != snapshot.benefit_segments[0].starts_at:
                raise StateFileError("membership projection authority mismatch")
            after = entry["binding_after"]
            if after is not None:
                device, count = activation_core.binding_state(after)
                expected_count = snapshot.preserved_binding_observation.rebinds_used
                legacy_takeover = (expected_count is None and type(entry["binding_before"]) is str
                                   and entry["binding_before"] == device and type(after) is dict and count == 0)
                if (not hmac.compare_digest(snapshot.preserved_binding_observation.binding_reference,
                                            self._membership_binding_reference(uid, device))
                    or (expected_count is None and type(after) is not str and not legacy_takeover)
                    or (expected_count is not None and count != expected_count)):
                    raise StateFileError("membership projection binding mismatch")
            starts = dict(self._load_paid_authority_snapshot(self.paid_starts_path, now=now))
            if uid in starts and starts[uid] != entry["start"]:
                raise StateFileError("membership projection clock mismatch")
            if uid not in starts:
                starts[uid] = entry["start"]
                self._membership_fault("before_paid_start_projection")
                self._write_paid_authority_snapshot(self.paid_starts_path, starts, now=now)
                self._membership_fault("after_paid_start_projection")
            bindings = dict(self._load_paid_authority_snapshot(self.bindings_path, now=now))
            current = bindings.get(uid)
            if after is not None and current != after:
                if current != entry["binding_before"]:
                    raise StateFileError("membership projection changed externally")
                bindings[uid] = after
                self._membership_fault("before_binding_projection")
                self._write_paid_authority_snapshot(self.bindings_path, bindings, now=now)
                self._membership_fault("after_binding_projection")
            del pending["entries"][fingerprint]
            touched = True
        if touched:
            self._membership_write_pending(pending)

    def _membership_read_authorities(self, *, now: int):
        bindings = self._load_paid_authority_snapshot(self.bindings_path, now=now)
        starts = self._load_paid_authority_snapshot(self.paid_starts_path, now=now)
        revoked = self._load_paid_authority_snapshot(self.revoked_path, now=now)
        blocks = self._load_paid_authority_snapshot(self.device_blocks_path, now=now)
        return bindings, starts, revoked, blocks

    def _membership_resolve(self, token: str, *, now: int, bindings: dict, starts: dict, revoked: set):
        info, card = self._membership_verified_card(token, bindings=bindings, starts=starts)
        if info["uid"] in revoked:
            return "revoked", info, card, None
        claim = self.membership_store.lookup_claim(card)
        uid = claim.account_uid if claim is not None else info["uid"]
        self._membership_recover_projection(uid, now=now)
        snapshot = self.membership_store.read(uid, now=now)
        if snapshot is not None and snapshot.current_plan_kind is None:
            start = snapshot.benefit_segments[0].starts_at
            if now < start <= now + 300:
                # Same bounded start-clock skew accepted by the legacy reader.
                # This is a projection of validated sources, not a new clock.
                snapshot = self.membership_store.read(uid, now=start)
        if not self._membership_sources_valid(uid, revoked):
            return "revoked", info, card, snapshot
        return "ok", info, card, snapshot

    def _membership_first_use(self, seed, *, before_binding, after_binding, now: int):
        card = seed.original_card
        pending = self._membership_pending()
        pending["entries"][card.payload_fingerprint] = {
            "card": {field: getattr(card, field) for field in card.__dataclass_fields__},
            "start": seed.started_at, "binding_before": before_binding, "binding_after": after_binding,
        }
        self._membership_fault("before_pending_projection")
        self._membership_write_pending(pending)
        self._membership_fault("after_pending_projection")
        self._membership_fault("before_membership_claim")
        result = self.membership_store.record_first_activation(seed, now=now)
        self._membership_fault("after_membership_claim")
        if result.status not in {membership_card_store.OperationStatus.FIRST_ACTIVATED,
                                 membership_card_store.OperationStatus.REPLAYED}:
            raise StateFileError("membership first use changed")
        self._membership_recover_projection(seed.account_uid, now=now)
        return result.snapshot

    @staticmethod
    def _membership_card_action(status: str, *, committed: bool = False):
        return 409, {
            "ok": False, "mode": "paid", "reason": "card_action",
            "card_redemption": {"schema_version": 1, "status": status, "committed": committed},
        }

    def _membership_prepare_paid(self, token: str, device: str, *, card_token: Optional[str], now: int):
        """Core prepare/commit, called under the configured file+service gate.

        Returns (error_response, lease, snapshot, redemption_ack). Pet signing
        happens after the shared core gate has been released.
        """
        bindings, starts, revoked, blocks = self._membership_read_authorities(now=now)
        blocked = self._device_block_key_decision(blocks, device=device)
        if blocked is not None:
            return (503 if blocked == "state_error" else 403, {"ok": False, "mode": "paid", "reason": blocked}), None, None, None
        if activation_core.verify_token(token, self.secret) is None:
            return (401, {"ok": False, "mode": "paid", "reason": "bad_token"}), None, None, None
        reason, info, current_card, snapshot = self._membership_resolve(
            token, now=now, bindings=bindings, starts=starts, revoked=revoked)
        if reason != "ok":
            return (403, {"ok": False, "mode": "paid", "reason": reason}), None, None, None
        # Recovery may have changed these files; never use the earlier snapshot.
        bindings, starts, revoked, blocks = self._membership_read_authorities(now=now)
        uid = snapshot.account_uid if snapshot is not None else info["uid"]
        proposed_bindings, proposed_starts = dict(bindings), dict(starts)
        canonical = self._membership_authority(snapshot) if snapshot is not None else None
        ok, reason, lease = activation_core.activate(
            token, self.secret, device, bindings=proposed_bindings, revoked=revoked,
            paid_starts=proposed_starts, lease_days=self.lease_days, now=now,
            canonical_membership=canonical,
        )
        if not ok:
            code = 503 if reason == "state_error" else 403 if reason in {"revoked", "device_mismatch", "roaming_rebind_limit"} else 401
            if card_token is not None and reason == "expired":
                return self._membership_card_action("unsupported_expired_account"), None, None, None
            if reason == "roaming_rebind_limit":
                return (403, {"ok": False, "mode": "paid", "reason": "device_mismatch",
                              "detail": "roaming_rebind_limit", "roaming_rebinds": activation_core.roaming_rebinds_state(
                                  activation_core.binding_state(bindings[uid])[1])}), None, None, None
            return (code, {"ok": False, "mode": "paid", "reason": reason}), None, None, None
        ack = None
        if card_token is not None:
            # The current account is authorized without persisting a rebind or
            # starting any trial/new account just to test a card operation.
            if snapshot is None and (current_card.plan_kind is None or uid not in starts):
                status = "switch_required_permanent" if current_card.duration_seconds is None else "unsupported_current_membership"
                return self._membership_card_action(status), None, None, None
            if (snapshot is None and starts.get(uid, now) > now) or (
                snapshot is not None and snapshot.benefit_segments[0].starts_at > now):
                return self._membership_card_action("unsupported_current_membership"), None, None, None
            if uid in bindings and activation_core.binding_state(bindings[uid])[0] != device:
                return self._membership_card_action("switch_required_device_activation"), None, None, None
            if uid not in bindings:
                return self._membership_card_action("switch_required_device_activation"), None, None, None
            if activation_core.verify_token(card_token, self.secret) is None:
                return self._membership_card_action("invalid_new_card"), None, None, None
            _, new_card = self._membership_verified_card(card_token, bindings=bindings, starts=starts)
            if new_card.card_uid in revoked:
                return self._membership_card_action("new_card_revoked"), None, None, None
            legacy_seed = None
            if snapshot is None:
                legacy_seed = self._membership_seed(
                    current_card, start=starts[uid], bound_record=bindings.get(uid), token=token, now=now)
            self._membership_fault("before_membership_redeem")
            result = self.membership_store.redeem(
                uid, new_card, now=now, policy=membership_card_store.SELECTED_PURCHASE_POLICY,
                legacy_seed=legacy_seed,
            )
            self._membership_fault("after_membership_redeem")
            if result.status not in {membership_card_store.OperationStatus.REDEEMED, membership_card_store.OperationStatus.REPLAYED}:
                return self._membership_card_action(result.status.value), None, None, None
            snapshot = result.snapshot
            if not self._membership_sources_valid(uid, revoked):
                return (403, {"ok": False, "mode": "paid", "reason": "revoked"}), None, None, None
            # Renewal does not spend a roaming rebind. Use the original binding,
            # not the speculative core.activate mutation above.
            final_bindings = dict(bindings)
            ok, reason, lease = activation_core.activate(
                token, self.secret, device, bindings=final_bindings, revoked=revoked,
                paid_starts=dict(starts), lease_days=self.lease_days, now=now,
                canonical_membership=self._membership_authority(snapshot),
            )
            if not ok:
                raise StateFileError("membership grant changed after commit")
            if final_bindings != bindings:
                # Same-device v5 string -> existing v6 counter0 takeover is a
                # representation migration, not a spent rebind or new allowance.
                before, after = bindings.get(uid), final_bindings.get(uid)
                if not (type(before) is str and before == device and type(after) is dict
                        and activation_core.binding_state(after) == (device, 0)):
                    raise StateFileError("membership renewal requires current binding")
                origin_receipt = next(r for r in self.membership_store.claims_for_account(uid) if r.operation == "first_activation")
                origin_card = membership_card_store.VerifiedCard(
                    uid, origin_receipt.payload_fingerprint,
                    origin_receipt.membership_exp - snapshot.benefit_segments[0].starts_at,
                    snapshot.device_permission, "activated",
                )
                pending = self._membership_pending()
                pending["entries"][origin_card.payload_fingerprint] = {
                    "card": {field: getattr(origin_card, field) for field in origin_card.__dataclass_fields__},
                    "start": snapshot.benefit_segments[0].starts_at,
                    "binding_before": before, "binding_after": after,
                }
                self._membership_write_pending(pending)
                self._membership_recover_projection(uid, now=now)
            receipt = result.receipt
            ack = {"schema_version": 1, "status": result.status.value, "receipt_id": receipt.receipt_id,
                   "credited_seconds": receipt.credited_seconds, "receipt_revision": receipt.revision,
                   "membership_revision": snapshot.revision, "membership_exp": snapshot.membership_exp,
                   "pet_quota_added_seconds": receipt.pet_quota_added_seconds,
                   "roaming_rebinds_added": receipt.roaming_rebinds_added, "committed": True}
        elif snapshot is None and current_card.plan_kind is not None and proposed_starts[uid] <= now:
            seed = self._membership_seed(current_card, start=proposed_starts[uid],
                                         bound_record=proposed_bindings.get(uid), token=token, now=now)
            snapshot = self._membership_first_use(seed, before_binding=bindings.get(uid),
                                                   after_binding=proposed_bindings.get(uid), now=now)
            ok, reason, lease = activation_core.activate(
                token, self.secret, device, bindings=dict(proposed_bindings), revoked=revoked,
                paid_starts=dict(proposed_starts), lease_days=self.lease_days, now=now,
                canonical_membership=self._membership_authority(snapshot),
            )
            if not ok:
                raise StateFileError("membership first grant unavailable")
        else:
            if proposed_starts != starts:
                self._write_paid_authority_snapshot(self.paid_starts_path, proposed_starts, now=now)
            if proposed_bindings != bindings:
                self._write_paid_authority_snapshot(self.bindings_path, proposed_bindings, now=now)
        self.bindings = self._load_paid_authority_snapshot(self.bindings_path, now=now)
        return None, lease, snapshot, ack

    def _activate_membership_paid(self, token: str, device: str, client_version=None, *,
                                  card_token=None, pet_usage_event=None, request_upload_lease=False):
        try:
            with self._membership_request_gate(), self._lock:
                now = int(time.time())
                error, lease, snapshot, ack = self._membership_prepare_paid(token, device, card_token=card_token, now=now)
            if error is not None:
                return error
        except Exception:  # Fixed response; durable claim is recovered, not rolled back.
            return 503, {"ok": False, "mode": "paid", "reason": "state_error",
                         **({"card_redemption": {"schema_version": 1, "status": "confirmation_pending", "committed": None}} if card_token is not None else {})}
        if snapshot is not None:
            self._attach_membership_pet_usage(lease, snapshot=snapshot, device=device, event_raw=pet_usage_event, now=now)
        else:
            self._attach_pet_usage(lease, token=token, device=device, event_raw=pet_usage_event, now=now)
        response = {"ok": True, "mode": "paid", "lease": activation_core.sign_lease(lease, self.secret),
                    "master_key": self._key_for_client(client_version),
                    "membership_card_capability": {"schema_version": 1, "pet_quota_schema_version": 2}}
        if ack is not None:
            response["card_redemption"] = ack
        if request_upload_lease:
            response["upload_lease"] = self._issue_upload_lease(lease, mode="paid", device=device, now=now)
        return 200, response

    def _authorize_membership_feature(self, token: str, feature: str, *, now: int):
        try:
            with self._membership_request_gate():
                with self._lock:
                    bindings, starts, revoked, _ = self._membership_read_authorities(now=now)
                    if activation_core.verify_token((token or "").strip(), self.secret) is None:
                        return False, "bad_token", None
                    reason, info, card, snapshot = self._membership_resolve(
                        (token or "").strip(), now=now, bindings=bindings, starts=starts, revoked=revoked)
                    if reason != "ok":
                        return False, reason, None
                    if snapshot is None and card.plan_kind is not None:
                        eligible = card.plan_kind == "365d" if feature == "private" else card.plan_kind in {"90d", "365d"}
                        if not eligible:
                            return False, "plan_required", None
                        bindings, starts, revoked, _ = self._membership_read_authorities(now=now)
                        start = starts.get(info["uid"], now)
                        activation_core._strict_state_timestamp(start, now=now)
                        if now >= start + card.duration_seconds:
                            return False, "expired", None
                        if start <= now:
                            seed = self._membership_seed(card, start=start, bound_record=bindings.get(info["uid"]), token=token, now=now)
                            snapshot = self._membership_first_use(seed, before_binding=bindings.get(info["uid"]),
                                                                   after_binding=bindings.get(info["uid"]), now=now)
                    if snapshot is not None:
                        if now >= snapshot.membership_exp:
                            return False, "expired", None
                        feature_exp = snapshot.private_dashboard_exp if feature == "private" else snapshot.my_records_exp
                        if feature_exp is None or now >= feature_exp:
                            return False, "plan_required", None
                        return True, "ok", {"uid": snapshot.account_uid, "kind": snapshot.current_plan_kind,
                                            "membership_exp": snapshot.membership_exp, "feature_exp": feature_exp,
                                            "membership_revision": snapshot.revision}
                # Permanent and hours/custom retain the original ordinary policy.
                legacy = self._authorize_private_dashboard_legacy if feature == "private" else self._authorize_my_records_legacy
                return legacy(token, now=now)
        except Exception:
            return False, "state_error", None

    def _load_paid_authority_snapshot(
        self,
        path: Path,
        *,
        now: int,
    ):
        if path not in self._paid_authority_paths:
            raise StateFileError("unsupported paid authority path")

        stamp_before = _authority_file_stamp(path)
        cached = self._paid_authority_cache.get(path)
        if cached is not None and cached[0] == stamp_before:
            return cached[1]

        # A cooperative writer uses atomic replace, but an operator or another
        # process may still update a file during this read.  Cache only a snapshot
        # whose identity is stable across the complete parse/validation.
        for _attempt in range(3):
            if path == self.bindings_path:
                value = _load_bindings_state(path)
            elif path == self.paid_starts_path:
                value = _load_paid_starts_state(path, now=now)
            elif path == self.revoked_path:
                value = _load_lines(path)
            else:
                value = _load_device_blocks_state(path)
            stamp_after = _authority_file_stamp(path)
            if stamp_before == stamp_after:
                self._paid_authority_cache[path] = (stamp_after, value)
                return value
            stamp_before = stamp_after
        raise StateFileError(f"authority state changed during read: {path.name}")

    def _write_paid_authority_snapshot(
        self,
        path: Path,
        value: dict,
        *,
        now: int,
    ) -> None:
        """Commit and refresh one supported authority snapshot.

        Reloading after the write proves that the persisted bytes satisfy the same
        complete validator used for external changes.  The cache is updated only
        after that proof, so write/parse/stat failures cannot publish an in-memory
        grant that is absent or invalid on disk.
        """
        if path not in {self.bindings_path, self.paid_starts_path}:
            raise StateFileError("unsupported paid authority write")
        # A writer may replace the file and then raise. Invalidate before that
        # boundary so even unchanged/coarse metadata cannot revive the old row.
        self._paid_authority_cache.pop(path, None)
        _atomic_write_json(path, value)
        persisted = self._load_paid_authority_snapshot(path, now=now)
        if persisted != value:
            self._paid_authority_cache.pop(path, None)
            raise StateFileError(f"authority write verification failed: {path.name}")

    @contextmanager
    def _pet_usage_transaction(
        self,
        *,
        now: int,
    ) -> Iterator[_PetUsageSqliteTransaction]:
        acquired = self._pet_usage_lock.acquire(
            timeout=_PET_USAGE_IN_PROCESS_WAIT_SECONDS
        )
        if not acquired:
            raise StateFileError("pet usage authority busy")
        try:
            if _legacy_pet_usage_state_present(
                (self.pet_usage_path, self.pet_usage_anchor_path)
            ):
                raise StateFileError("legacy pet usage state requires migration")
            with _pet_usage_sqlite_transaction(
                self.pet_usage_db_path,
                secret=self.secret,
                now=now,
            ) as transaction:
                if transaction.state["revision"] < self._pet_usage_revision_floor:
                    raise StateFileError("pet usage authority revision rolled back")
                self._pet_usage_revision_floor = max(
                    self._pet_usage_revision_floor,
                    transaction.state["revision"],
                )
                try:
                    yield transaction
                finally:
                    if transaction.committed:
                        self._pet_usage_revision_floor = max(
                            self._pet_usage_revision_floor,
                            transaction.state["revision"],
                        )
        finally:
            self._pet_usage_lock.release()

    def _device_block_key_decision(
        self,
        device_blocks: dict,
        *,
        device: Optional[str],
    ) -> Optional[str]:
        """Return a fixed activation decision for the current denylist/key pair."""
        if self.device_block_key_state in {"invalid", "shared_activation_secret"}:
            return "state_error"
        blocked = device_blocks["blocked"]
        if not blocked:
            return None
        if self.device_block_key_state != "valid" or self.device_block_key is None:
            return "state_error"
        if device is not None and device_block_id(self.device_block_key, device) in blocked:
            return "device_revoked"
        return None

    def _issue_upload_lease(
        self,
        lease: dict,
        *,
        mode: str,
        device: str,
        now: int,
    ) -> Optional[str]:
        """Create a process-independent short identity proof from server authority."""
        if (
            self.upload_security_epoch <= 0
            or self.device_block_key_state != "valid"
            or self.device_block_key is None
        ):
            return None
        try:
            if mode == "paid":
                uid = lease.get("uid")
                subject_kind = "paid"
                authority_id = uid
                uid_hash = upload_lease.paid_uid_hash(uid)
                entitlement_exp = lease.get("paid_exp")
            elif mode == "trial":
                subject_kind = "trial"
                authority_id = device
                uid_hash = None
                entitlement_exp = lease.get("exp")
            else:
                return None
            if entitlement_exp is not None and (
                type(entitlement_exp) is not int or entitlement_exp <= now  # noqa: E721
            ):
                return None
            exp = now + self.upload_lease_seconds
            if entitlement_exp is not None:
                exp = min(exp, entitlement_exp)
            if exp <= now:
                return None
            payload = {
                "schema_version": upload_lease.SCHEMA_VERSION,
                "kind": upload_lease.KIND,
                "subject_kind": subject_kind,
                "subject_id": upload_lease.opaque_subject_id(
                    self.secret,
                    subject_kind=subject_kind,
                    authority_id=authority_id,
                ),
                "uid_hash": uid_hash,
                "device_alias": device_block_id(self.device_block_key, device),
                "security_epoch": self.upload_security_epoch,
                "lease_id": secrets.token_hex(16),
                "iat": now,
                "exp": exp,
                "entitlement_exp": entitlement_exp,
            }
            return upload_lease.sign(payload, self.secret)
        except (TypeError, ValueError, OverflowError, upload_lease.UploadLeaseError):
            return None

    def authorize_upload_lease(self, envelope, *, now: Optional[int] = None) -> tuple[bool, str, Optional[dict]]:
        try:
            return self._authorize_upload_lease_impl(envelope, now=now)
        except Exception:
            if self.membership_store is not None:
                return False, "identity_state_error", None
            raise

    def _authorize_upload_lease_impl(
        self,
        envelope,
        *,
        now: Optional[int] = None,
    ) -> tuple[bool, str, Optional[dict]]:
        """Verify a short lease and recheck current revoke/binding authority."""
        now = int(time.time()) if now is None else now
        try:
            payload = upload_lease.verify(envelope, self.secret, now=now)
        except upload_lease.UploadLeaseExpiredError:
            return False, "identity_expired", None
        except (TypeError, ValueError, OverflowError, upload_lease.UploadLeaseError):
            return False, "identity_invalid", None
        if (
            self.upload_security_epoch <= 0
            or payload["security_epoch"] != self.upload_security_epoch
        ):
            return False, "identity_epoch_mismatch", None
        if self.device_block_key_state != "valid" or self.device_block_key is None:
            return False, "identity_state_error", None

        with self._membership_request_gate(), self._lock:
            try:
                device_blocks = self._load_paid_authority_snapshot(
                    self.device_blocks_path,
                    now=now,
                )
                if payload["subject_kind"] == "paid":
                    bindings = self._load_paid_authority_snapshot(
                        self.bindings_path,
                        now=now,
                    )
                    revoked = self._load_paid_authority_snapshot(
                        self.revoked_path,
                        now=now,
                    )
                    matched = [
                        (uid, activation_core.binding_state(record)[0])
                        for uid, record in bindings.items()
                        if hmac.compare_digest(
                            upload_lease.opaque_subject_id(
                                self.secret,
                                subject_kind="paid",
                                authority_id=uid,
                            ),
                            payload["subject_id"],
                        )
                    ]
                    if len(matched) != 1:
                        return False, "identity_revoked", None
                    uid, device = matched[0]
                    if uid in revoked:
                        return False, "identity_revoked", None
                    if self.membership_store is not None and not self._membership_sources_valid(uid, revoked):
                        return False, "identity_revoked", None
                    if not hmac.compare_digest(
                        device_block_id(self.device_block_key, device),
                        payload["device_alias"],
                    ):
                        return False, "identity_device_mismatch", None
                else:
                    trials = _load_trials_state(self.trials_path, now=now)
                    matched_devices = [
                        device
                        for device in trials
                        if hmac.compare_digest(
                            device_block_id(self.device_block_key, device),
                            payload["device_alias"],
                        )
                    ]
                    if len(matched_devices) != 1:
                        return False, "identity_revoked", None
                    device = matched_devices[0]
                decision = self._device_block_key_decision(
                    device_blocks,
                    device=device,
                )
            except (StateFileError, TypeError, ValueError, OverflowError):
                return False, "identity_state_error", None
        if decision == "device_revoked":
            return False, "identity_device_revoked", None
        if decision is not None:
            return False, "identity_state_error", None
        return True, "ok", payload

    def _key_for_client(self, client_version) -> str:
        """按客户端版本选主密钥(双密钥窗口, 见模块 docstring)。

        未部署 v2 key -> 恒发旧 key(与轮换前逐字节等价)。老客户端不带 client_version ->
        _version_ge(None, ...) = False -> 旧 key, 故轮换【不会打死任何已发出的包】。"""
        if self.master_key_v2_b64 and _version_ge(client_version, self.v2_min_version):
            return self.master_key_v2_b64
        return self.master_key_b64

    def authorize_private_dashboard(self, token: str, *, now: Optional[int] = None) -> tuple[bool, str, Optional[dict]]:
        if self.membership_store is not None:
            return self._authorize_membership_feature(token, "private", now=int(time.time()) if now is None else now)
        return self._authorize_private_dashboard_legacy(token, now=now)

    def _authorize_private_dashboard_legacy(self, token: str, *, now: Optional[int] = None) -> tuple[bool, str, Optional[dict]]:
        """验证完整私有大数据权益，不绑定浏览器为一台软件设备。

        允许历史永久 token 与【精确 365 天】限期 token。30/90 天等其他档位拒绝。365 天会员若尚未
        在客户端激活，首次使用私有大数据也会写入同一 paid_starts，确保订阅时钟只有一个起点；
        后续客户端激活继续复用该起点，不会重置。返回 entitlement 含 uid 与实际会员到期点。
        """
        now = int(time.time()) if now is None else int(now)
        info = activation_core.verify_token((token or "").strip(), self.secret)
        if info is None or not info.get("uid"):
            return False, "bad_token", None
        uid = str(info["uid"])
        try:
            revoked = _load_lines(self.revoked_path)
        except StateFileError:
            return False, "state_error", None
        if uid in revoked:
            return False, "revoked", None

        tok_exp = info.get("exp")
        if tok_exp is None:
            return True, "ok", {"uid": uid, "kind": "legacy_permanent", "membership_exp": None}
        duration = max(0, int(tok_exp) - int(info["iat"]))
        if duration != _PRIVATE_DASHBOARD_DURATION:
            return False, "plan_required", None

        with self._lock:
            try:
                paid_starts = self._load_paid_authority_snapshot(
                    self.paid_starts_path,
                    now=now,
                )
            except StateFileError:
                return False, "state_error", None
            has_recorded_start = uid in paid_starts
            recorded = paid_starts.get(uid)
            try:
                start = (
                    now
                    if not has_recorded_start
                    else activation_core._strict_state_timestamp(recorded, now=now)
                )
            except (TypeError, ValueError, OverflowError):
                return False, "state_error", None
            membership_exp = start + duration
            if now >= membership_exp:
                return False, "expired", None
            if not has_recorded_start:
                paid_starts = dict(paid_starts)
                paid_starts[uid] = now
                try:
                    self._write_paid_authority_snapshot(
                        self.paid_starts_path,
                        paid_starts,
                        now=now,
                    )
                except Exception:  # noqa: BLE001 - 起点落盘失败就拒绝，避免反复登录无限重置 365 天
                    return False, "state_error", None
        return True, "ok", {"uid": uid, "kind": "365d", "membership_exp": membership_exp}

    def authorize_my_records(self, token: str, *, now: Optional[int] = None) -> tuple[bool, str, Optional[dict]]:
        if self.membership_store is not None:
            return self._authorize_membership_feature(token, "my", now=int(time.time()) if now is None else now)
        return self._authorize_my_records_legacy(token, now=now)

    def _authorize_my_records_legacy(
        self,
        token: str,
        *,
        now: Optional[int] = None,
    ) -> tuple[bool, str, Optional[dict]]:
        """验证个人对局记录权益，不扩大完整私有大数据权限。

        历史永久 token 与服务端确认的整日时长 ``>= 90`` 天订阅可用；有限期权益仍与
        客户端激活共用 ``paid_starts`` 的唯一订阅起点，并在主会员到期点立即失效。
        不足 90 天、非整日或畸形时长一律拒绝，客户端不能从套餐文案自行升级。
        """
        now = int(time.time()) if now is None else int(now)
        info = activation_core.verify_token((token or "").strip(), self.secret)
        if info is None or not info.get("uid"):
            return False, "bad_token", None
        uid = str(info["uid"])
        try:
            revoked = _load_lines(self.revoked_path)
        except StateFileError:
            return False, "state_error", None
        if uid in revoked:
            return False, "revoked", None

        tok_exp = info.get("exp")
        if tok_exp is None:
            return True, "ok", {
                "uid": uid,
                "kind": "legacy_permanent",
                "membership_exp": None,
            }
        duration = max(0, int(tok_exp) - int(info["iat"]))
        if duration < _MY_RECORDS_MIN_DURATION or duration % 86400 != 0:
            return False, "plan_required", None

        with self._lock:
            try:
                paid_starts = self._load_paid_authority_snapshot(
                    self.paid_starts_path,
                    now=now,
                )
            except StateFileError:
                return False, "state_error", None
            has_recorded_start = uid in paid_starts
            recorded = paid_starts.get(uid)
            try:
                start = (
                    now
                    if not has_recorded_start
                    else activation_core._strict_state_timestamp(recorded, now=now)
                )
            except (TypeError, ValueError, OverflowError):
                return False, "state_error", None
            membership_exp = start + duration
            if now >= membership_exp:
                return False, "expired", None
            if not has_recorded_start:
                paid_starts = dict(paid_starts)
                paid_starts[uid] = now
                try:
                    self._write_paid_authority_snapshot(
                        self.paid_starts_path,
                        paid_starts,
                        now=now,
                    )
                except Exception:  # noqa: BLE001 - 起点写入失败则不得反复重置权益
                    return False, "state_error", None

        duration_days = duration // 86400
        kind = f"{duration_days}d" if duration_days in {90, 365} else "custom"
        return True, "ok", {
            "uid": uid,
            "kind": kind,
            "membership_exp": membership_exp,
        }

    @staticmethod
    def _pet_usage_error(lease: dict, *, now: int, reason: str) -> dict:
        base = (
            lease.get("feature_usage", {}).get("pet_companion")
            if isinstance(lease.get("feature_usage"), dict)
            else None
        )
        kind = base.get("kind") if isinstance(base, dict) else None
        return {
            "kind": kind if kind in {"trial", "quota", "full", "none"} else "none",
            "status": reason,
            "server_now": now,
        }

    def _apply_pet_usage_transaction(
        self,
        transaction: _PetUsageSqliteTransaction,
        *,
        lease: dict,
        base: dict,
        kind: str,
        total_seconds: int,
        event: Optional[dict],
        entitlement_id: str,
        device_id: str,
        now: int,
        force_changed: bool = False,
        entitlement_exp_override: Optional[int] = None,
    ) -> dict:
        state = transaction.state
        records = state["records"]
        record = records.get(entitlement_id)
        changed = force_changed
        if record is None:
            record = {
                "kind": kind,
                "total_seconds": total_seconds,
                "used_seconds": 0,
                "device_id": device_id,
                "session_id": None,
                "last_seq": -1,
                "last_command_visible": False,
                "visible": False,
                "last_event_at": now,
                "visibility_lease_exp": now,
            }
            records[entitlement_id] = record
            changed = True
        elif (
            record["kind"] != kind
            or record["total_seconds"] != total_seconds
            or record["last_event_at"] > now
        ):
            raise StateFileError("pet usage entitlement state mismatch")

        entitlement_exp = entitlement_exp_override if entitlement_exp_override is not None else (
            lease.get("paid_exp") if kind == "quota" else lease.get("exp")
        )
        if type(entitlement_exp) is not int or entitlement_exp <= now:
            return self._pet_usage_error(
                lease,
                now=now,
                reason="expired",
            )

        if record["visible"]:
            charged_until = min(now, record["visibility_lease_exp"], entitlement_exp)
            elapsed = max(0, charged_until - record["last_event_at"])
            if elapsed:
                record["used_seconds"] = min(
                    total_seconds,
                    record["used_seconds"] + elapsed,
                )
                record["last_event_at"] = charged_until
                changed = True
            if (
                now >= record["visibility_lease_exp"]
                or now >= entitlement_exp
                or record["used_seconds"] >= total_seconds
            ):
                record["visible"] = False
                changed = True

        event_status = None
        if record["device_id"] != device_id:
            if record["visible"]:
                event_status = "session_conflict"
            else:
                record.update(
                    device_id=device_id,
                    session_id=None,
                    last_seq=-1,
                    last_command_visible=False,
                    last_event_at=now,
                    visibility_lease_exp=now,
                )
                changed = True

        if event is not None and event_status is None:
            session_id = _pet_session_id(self.secret, event["session_id"])
            if record["session_id"] != session_id:
                if record["visible"]:
                    event_status = "session_conflict"
                else:
                    record.update(
                        session_id=session_id,
                        last_seq=-1,
                        last_command_visible=False,
                        last_event_at=now,
                        visibility_lease_exp=now,
                    )
                    changed = True
            if event_status is None:
                if event["seq"] == record["last_seq"]:
                    if event["visible"] != record["last_command_visible"]:
                        event_status = "sequence_conflict"
                elif event["seq"] != record["last_seq"] + 1:
                    event_status = "sequence_conflict"
                else:
                    record["last_seq"] = event["seq"]
                    record["last_command_visible"] = event["visible"]
                    record["last_event_at"] = now
                    remaining = total_seconds - record["used_seconds"]
                    record["visible"] = bool(event["visible"] and remaining > 0)
                    record["visibility_lease_exp"] = (
                        min(
                            now + self.pet_usage_lease_seconds,
                            entitlement_exp,
                        )
                        if record["visible"]
                        else now
                    )
                    changed = True

        if record["used_seconds"] >= total_seconds:
            record["visible"] = False
            record["visibility_lease_exp"] = min(
                record["visibility_lease_exp"],
                now,
            )
            changed = True

        if changed:
            transaction.commit()

        remaining_seconds = max(0, total_seconds - record["used_seconds"])
        if event_status is not None:
            status = event_status
        elif remaining_seconds <= 0:
            status = "exhausted"
        elif record["visible"]:
            status = "active"
        elif record["last_seq"] >= 0:
            status = "paused"
        else:
            status = "available"
        return {
            **base,
            "status": status,
            "server_now": now,
            "entitlement_id": entitlement_id,
            "usage_revision": state["revision"],
            "remaining_seconds": remaining_seconds,
            "last_seq": record["last_seq"],
            "visible": record["visible"],
            "visibility_lease_exp": record["visibility_lease_exp"],
            "entitlement_exp": entitlement_exp,
        }

    def _attach_membership_pet_usage(self, lease: dict, *, snapshot, device: str, event_raw, now: int) -> None:
        """Reconcile unique purchase budget with LIVE authenticated usage.

        Setting the verified target total is idempotent. Never restore used from
        the membership seed, or reset sessions/sequence/monotonic Pet authority.
        This is a Pet-only transaction, outside the core member/file gate.
        """
        base = lease["feature_usage"]["pet_companion"]
        schema = lease["feature_usage"]["schema_version"]
        try:
            reference = snapshot.pet_ledger_reference
            if type(reference) is not str or _PET_OPAQUE_ID_RE.fullmatch(reference) is None:
                raise StateFileError("membership Pet reference unavailable")
            total = snapshot.pet_purchased_quota_seconds
            kind = base["kind"]
            with self._pet_usage_transaction(now=now) as transaction:
                record = transaction.state["records"].get(reference)
                changed = False
                if record is not None:
                    if record["kind"] != "quota" or record["total_seconds"] > total or record["last_event_at"] > now:
                        raise StateFileError("membership Pet authority mismatch")
                    if record["visible"]:
                        until = min(now, record["visibility_lease_exp"])
                        elapsed = max(0, until - record["last_event_at"])
                        if elapsed:
                            record["used_seconds"] = min(record["total_seconds"], record["used_seconds"] + elapsed)
                            record["last_event_at"] = until
                            changed = True
                        if kind == "full" or now >= record["visibility_lease_exp"] or record["used_seconds"] >= record["total_seconds"]:
                            record["visible"] = False
                            record["visibility_lease_exp"] = now
                            changed = True
                    if record["total_seconds"] < total:
                        record["total_seconds"] = total
                        changed = True
                elif snapshot.pet_preserved_used_seconds not in (None, 0):
                    # A known old observation is not permission to synthesize a
                    # replacement live usage row after authority loss.
                    raise StateFileError("membership Pet usage unavailable")
                if kind == "full":
                    if snapshot.full_pet_exp is None or now >= snapshot.full_pet_exp:
                        raise StateFileError("membership Pet period expired")
                    if changed:
                        transaction.commit()
                    response = {**base, "status": "active", "server_now": now,
                                "entitlement_exp": snapshot.full_pet_exp}
                elif kind == "quota":
                    if total <= 0:
                        raise StateFileError("membership Pet purchase unavailable")
                    response = self._apply_pet_usage_transaction(
                        transaction, lease=lease, base=base, kind="quota", total_seconds=total,
                        event=_validate_pet_usage_event(event_raw), entitlement_id=reference,
                        device_id=_pet_device_id(self.secret, device), now=now, force_changed=changed,
                        entitlement_exp_override=snapshot.current_plan_exp,
                    )
                else:
                    raise StateFileError("membership Pet plan unavailable")
            lease["feature_usage"] = {"schema_version": schema, "pet_companion": response}
        except Exception:
            lease["feature_entitlements"] = {"pet_companion": "none"}
            lease["feature_usage"] = {"schema_version": schema, "pet_companion": {"kind": "none", "status": "state_error", "server_now": now}}

    def _attach_pet_usage(
        self,
        lease: dict,
        *,
        token: Optional[str],
        device: str,
        event_raw,
        now: int,
    ) -> None:
        """Attach a signed, server-authoritative Pet view to an activation lease.

        Quota time is derived only from server time between accepted visibility
        events and is capped by a short visibility lease.  The client supplies no
        elapsed/used value.  Any Pet-specific state failure denies Pet while leaving
        the calculator activation response available.
        """
        feature_usage = lease.get("feature_usage")
        base = (
            feature_usage.get("pet_companion")
            if isinstance(feature_usage, dict)
            else None
        )
        if not isinstance(base, dict):
            lease["feature_usage"] = {
                "schema_version": _PET_FEATURE_USAGE_SCHEMA_VERSION,
                "pet_companion": self._pet_usage_error(
                    lease,
                    now=now,
                    reason="state_error",
                ),
            }
            lease["feature_entitlements"] = {"pet_companion": "none"}
            return
        kind = base.get("kind")
        if kind == "full":
            try:
                # Full plans do not consume seconds, but Pet must not remain
                # authorized while the shared authority is unavailable.
                with self._pet_usage_transaction(now=now):
                    pass
                response = {
                    **base,
                    "status": "active",
                    "server_now": now,
                }
                if "paid_exp" in lease:
                    response["entitlement_exp"] = lease["paid_exp"]
            except Exception:  # noqa: BLE001 - calculator remains independent
                response = self._pet_usage_error(
                    lease,
                    now=now,
                    reason="state_error",
                )
                lease["feature_entitlements"] = {"pet_companion": "none"}
            lease["feature_usage"] = {
                "schema_version": _PET_FEATURE_USAGE_SCHEMA_VERSION,
                "pet_companion": response,
            }
            return
        if kind == "none":
            lease["feature_usage"] = {
                "schema_version": _PET_FEATURE_USAGE_SCHEMA_VERSION,
                "pet_companion": {
                    **base,
                    "status": "locked",
                    "server_now": now,
                },
            }
            return
        total_seconds = base.get("total_seconds")
        if (
            kind not in {"trial", "quota"}
            or type(total_seconds) is not int
            or total_seconds <= 0
        ):
            lease["feature_usage"] = {
                "schema_version": _PET_FEATURE_USAGE_SCHEMA_VERSION,
                "pet_companion": self._pet_usage_error(
                    lease,
                    now=now,
                    reason="state_error",
                ),
            }
            lease["feature_entitlements"] = {"pet_companion": "none"}
            return
        try:
            event = _validate_pet_usage_event(event_raw)
            entitlement_id = _pet_entitlement_id(
                self.secret,
                token=token,
                device=device,
            )
            device_id = _pet_device_id(self.secret, device)
            with self._pet_usage_transaction(now=now) as transaction:
                response = self._apply_pet_usage_transaction(
                    transaction,
                    lease=lease,
                    base=base,
                    kind=kind,
                    total_seconds=total_seconds,
                    event=event,
                    entitlement_id=entitlement_id,
                    device_id=device_id,
                    now=now,
                )
        except Exception:  # noqa: BLE001 - Pet fails closed without blocking calculator
            response = self._pet_usage_error(
                lease,
                now=now,
                reason="state_error",
            )
        lease["feature_usage"] = {
            "schema_version": _PET_FEATURE_USAGE_SCHEMA_VERSION,
            "pet_companion": response,
        }

    def handle_activate(self, body: dict) -> tuple[int, dict]:
        """有 token = 付费激活(永久); 无 token = 半免费试用(按设备 trial_hours 小时)。

        client_version: 用于双密钥窗口选 key；显式启用产品版本门后也用于权威退役。
        client_security_epoch: 发布侧显式启用后拒绝旧客户端；默认 0 保持部署兼容。"""
        token = body.get("token")
        device = body.get("device")
        client_version = body.get("client_version")
        mode = "paid" if isinstance(token, str) and token else "trial"
        card_token = body.get("card_token")
        card_schema = body.get("card_redemption_schema")
        has_card_action = card_token is not None or card_schema is not None
        if not isinstance(device, str) or not device:
            return 400, {"ok": False, "reason": "bad_request"}
        epoch = body.get("client_security_epoch", 0)
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
            return 400, {"ok": False, "reason": "bad_request"}
        upload_schema = body.get("upload_lease_schema")
        if upload_schema is not None and (
            type(upload_schema) is not int
            or upload_schema != upload_lease.SCHEMA_VERSION
        ):
            return 400, {"ok": False, "reason": "bad_request"}
        request_upload_lease = upload_schema == upload_lease.SCHEMA_VERSION
        if epoch < self.min_client_security_epoch:
            return 426, {
                "ok": False,
                "mode": mode,
                "reason": "upgrade_required",
                "min_client_security_epoch": self.min_client_security_epoch,
            }
        if self.min_client_version and not _release_version_ge(
            client_version, self.min_client_version
        ):
            # 产品版本门独立于安全 epoch 和 v2 密钥路由；拒绝发生在绑定/试用状态写入、
            # lease 签发和 master key 选择之前。缺失、空值、未知后缀均按需升级处理。
            return 426, {
                "ok": False,
                "mode": mode,
                "reason": "upgrade_required",
                "min_client_version": self.min_client_version,
            }
        if self.min_client_security_epoch > 0:
            # 一旦启用正式切代门，就不能再让“自报新 epoch、缺版本”回落旧 key，也不能在
            # v2 key 部署错误时静默发旧 key。默认 epoch=0 的双密钥兼容窗口保持原行为。
            if not self.master_key_v2_b64:
                return 503, {
                    "ok": False,
                    "mode": mode,
                    "reason": "security_key_unavailable",
                }
            if not _version_ge(client_version, self.v2_min_version):
                return 426, {
                    "ok": False,
                    "mode": mode,
                    "reason": "upgrade_required",
                    "min_client_security_epoch": self.min_client_security_epoch,
                    "min_client_version": self.v2_min_version,
                }
        if has_card_action:
            if type(card_token) is not str or not card_token or type(card_schema) is not int or card_schema != 1:
                return 400, {"ok": False, "mode": mode, "reason": "bad_request"}
            if self.membership_store is None:
                return self._membership_card_action("renewal_not_configured")
            if mode != "paid":
                return self._membership_card_action("unsupported_current_membership")
        if isinstance(token, str) and token:
            if self.membership_store is not None:
                return self._activate_membership_paid(
                    token, device, client_version, card_token=card_token,
                    pet_usage_event=body.get("pet_usage"), request_upload_lease=request_upload_lease,
                )
            return self._activate_paid(
                token,
                device,
                client_version,
                pet_usage_event=body.get("pet_usage"),
                request_upload_lease=request_upload_lease,
            )
        try:
            return self._activate_trial(
                device,
                client_version,
                pet_usage_event=body.get("pet_usage"),
                request_upload_lease=request_upload_lease,
            )
        except StateFileError:
            if self.membership_store is None:
                raise
            return 503, {"ok": False, "mode": "trial", "reason": "state_error"}

    def _activate_paid(
        self,
        token: str,
        device: str,
        client_version=None,
        *,
        pet_usage_event=None,
        request_upload_lease: bool = False,
    ) -> tuple[int, dict]:
        with self._lock:  # #2: 读-改-写-落盘原子, 锁内重读磁盘(权威 + 人工改绑/并发最新态)
            now = int(time.time())
            try:
                bindings = self._load_paid_authority_snapshot(
                    self.bindings_path,
                    now=now,
                )
                paid_starts = self._load_paid_authority_snapshot(
                    self.paid_starts_path,
                    now=now,
                )
                revoked = self._load_paid_authority_snapshot(
                    self.revoked_path,
                    now=now,
                )
                device_blocks = self._load_paid_authority_snapshot(
                    self.device_blocks_path,
                    now=now,
                )
            except StateFileError:
                return 503, {"ok": False, "mode": "paid", "reason": "state_error"}
            device_block_decision = self._device_block_key_decision(
                device_blocks,
                device=device,
            )
            if device_block_decision == "state_error":
                return 503, {"ok": False, "mode": "paid", "reason": "state_error"}
            if device_block_decision == "device_revoked":
                return 403, {"ok": False, "mode": "paid", "reason": "device_revoked"}
            # activate() can only mutate the verified UID's binding/start entry.
            # Track that transaction slice instead of copying and comparing the
            # complete authority maps on every existing paid heartbeat.
            verified = activation_core.verify_token(token, self.secret)
            tracked_uid = verified["uid"] if verified is not None else None
            tracked_roaming = bool(verified.get("roaming")) if verified is not None else False
            if tracked_uid is not None:
                record = bindings.get(tracked_uid)
                if tracked_uid not in bindings or (
                    tracked_roaming and (
                        type(record) is str or activation_core.binding_state(record)[0] != device
                    )
                ):
                    bindings = dict(bindings)
                if verified.get("exp") is not None and tracked_uid not in paid_starts:
                    paid_starts = dict(paid_starts)
            binding_before = (
                tracked_uid in bindings,
                bindings.get(tracked_uid),
            )
            paid_start_before = (
                tracked_uid in paid_starts,
                paid_starts.get(tracked_uid),
            )
            ok, reason, lease = activation_core.activate(
                token, self.secret, device,
                bindings=bindings, revoked=revoked, paid_starts=paid_starts,
                lease_days=self.lease_days, now=now,
            )
            if ok:
                paid_starts_changed = paid_start_before != (
                    tracked_uid in paid_starts,
                    paid_starts.get(tracked_uid),
                )
                bindings_changed = binding_before != (
                    tracked_uid in bindings,
                    bindings.get(tracked_uid),
                )
                try:
                    # Existing paid heartbeats are the dominant reconnect path.
                    # Rewriting unchanged JSON while holding the global lock
                    # serializes every client behind filesystem/AV latency.
                    if paid_starts_changed:
                        self._write_paid_authority_snapshot(
                            self.paid_starts_path,
                            paid_starts,
                            now=now,
                        )
                    if bindings_changed:
                        # A changed start/binding must still be durable before
                        # issuing its lease; unchanged authority needs no write.
                        self._write_paid_authority_snapshot(
                            self.bindings_path,
                            bindings,
                            now=now,
                        )
                except Exception:  # noqa: BLE001
                    return 503, {"ok": False, "mode": "paid", "reason": "state_error"}
                self.bindings = bindings  # 内存只在两份权威状态都落盘后更新
        if not ok:
            if reason == "roaming_rebind_limit":
                return 403, {
                    "ok": False, "mode": "paid", "reason": "device_mismatch",
                    "detail": "roaming_rebind_limit",
                    "roaming_rebinds": activation_core.roaming_rebinds_state(
                        activation_core.binding_state(bindings[tracked_uid])[1]
                    ),
                }
            if reason == "state_error":
                code = 503
            else:
                code = 403 if reason in ("revoked", "device_mismatch") else 401
            return code, {"ok": False, "mode": "paid", "reason": reason}
        # The calculator grant and its binding/start timestamps are fully
        # committed above.  Pet is a separately signed, fail-closed entitlement
        # and must never extend the global activation critical section.
        self._attach_pet_usage(
            lease,
            token=token,
            device=device,
            event_raw=pet_usage_event,
            now=now,
        )
        signed = activation_core.sign_lease(lease, self.secret)
        response = {"ok": True, "mode": "paid", "lease": signed,
                    "master_key": self._key_for_client(client_version)}
        if request_upload_lease:
            response["upload_lease"] = self._issue_upload_lease(
                lease,
                mode="paid",
                device=device,
                now=now,
            )
        return 200, response

    def _activate_trial(
        self,
        device: str,
        client_version=None,
        *,
        pet_usage_event=None,
        request_upload_lease: bool = False,
    ) -> tuple[int, dict]:
        with self._membership_request_gate(), self._lock:  # Core IO only; Pet remains outside.
            now = int(time.time())
            try:
                trials = _load_trials_state(self.trials_path, now=now)
                device_blocks = _load_device_blocks_state(self.device_blocks_path)
            except StateFileError:
                return 503, {"ok": False, "mode": "trial", "reason": "state_error"}
            device_block_decision = self._device_block_key_decision(
                device_blocks,
                device=device,
            )
            if device_block_decision == "state_error":
                return 503, {"ok": False, "mode": "trial", "reason": "state_error"}
            if device_block_decision == "device_revoked":
                return 403, {"ok": False, "mode": "trial", "reason": "device_revoked"}
            trials_before = dict(trials)
            ok, reason, lease = activation_core.grant_trial(
                device,
                trials=trials,
                trial_hours=self.trial_hours,
                grandfather_cutover_at=self.trial_grandfather_cutover_at,
                grandfather_trial_hours=self.trial_grandfather_hours,
                now=now,
            )
            if reason in {"ok", "trial_expired"} and trials != trials_before:
                try:
                    # 固定窗口记录同时保存最后一次权威服务器时间；同秒重复请求不写，
                    # 时间推进则原子落盘，之后的时钟回拨不能重新激活已消耗窗口。
                    _atomic_write_json(self.trials_path, trials)
                except Exception:  # noqa: BLE001
                    return 503, {"ok": False, "mode": "trial", "reason": "state_error"}
        if not ok:
            if reason == "state_error":
                return 503, {"ok": False, "mode": "trial", "reason": reason}
            return 403, {"ok": False, "mode": "trial", "reason": reason}  # trial_expired -> 引导付费
        # Trial grant persistence is complete before the independent Pet
        # authority is consulted, for the same bounded-contention contract as
        # paid activation.
        self._attach_pet_usage(
            lease,
            token=None,
            device=device,
            event_raw=pet_usage_event,
            now=now,
        )
        signed = activation_core.sign_lease(lease, self.secret)
        response = {"ok": True, "mode": "trial", "lease": signed,
                    "master_key": self._key_for_client(client_version),
                    "trial_exp": lease["exp"]}
        if request_upload_lease:
            response["upload_lease"] = self._issue_upload_lease(
                lease,
                mode="trial",
                device=device,
                now=now,
            )
        return 200, response

    def handle_telemetry(self, body: dict, ip: str) -> tuple[int, dict]:
        rec = {
            "ts": int(time.time()),
            "ip": ip,
            "device": str(body.get("device", ""))[:64],
            "verdict": str(body.get("verdict", ""))[:300],
        }
        try:
            self.telemetry_path.parent.mkdir(parents=True, exist_ok=True)
            with self.telemetry_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001
            pass
        return 200, {"ok": True}
