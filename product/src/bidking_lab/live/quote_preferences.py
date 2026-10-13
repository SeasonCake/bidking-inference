"""Local bid preferences and bounded, client-reported applied-quote evidence.

No model inputs, entitlement decisions, networking or Tk belong in this module.
Recorded arithmetic is not evidence of a game bid or a faithful model replay.
"""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any

TIERS = ("conservative", "balanced", "aggressive")
POLICY_SCHEMA = "quote_policy_v1"
PREFS_SCHEMA = "quote_preferences_v1"
RECORD_SCHEMA = "quote_application_v1"
COVERAGE = "last_recorded_applied_pre_settlement"
RECORD_LIMIT = 16 * 1024
MAX_AGE = 24 * 60 * 60
UNKNOWN_REASONS = frozenset({"missing", "invalid", "different_session", "stale", "future",
                             "writer_conflict", "busy", "io_error", "manual", "unavailable",
                             "settlement_cutoff_invalid", "not_before_settlement"})


def _keys(value, expected):
    if type(value) is not dict or set(value) != set(expected):
        raise ValueError("invalid fields")


def default_policy():
    return {"schema": POLICY_SCHEMA, "ratios_bps": dict.fromkeys(TIERS, 10000), "cap": None}


def validate_policy(value):
    _keys(value, ("schema", "ratios_bps", "cap"))
    if value["schema"] != POLICY_SCHEMA:
        raise ValueError("unknown policy")
    _keys(value["ratios_bps"], TIERS)
    if any(type(v) is not int or not 0 <= v <= 20000 for v in value["ratios_bps"].values()):
        raise ValueError("比例须在0%到200%之间，最多两位小数")
    cap = value["cap"]
    if cap is not None and (type(cap) is not int or not 1 <= cap <= 999999999):
        raise ValueError("上限须为空或1到999999999的整数游戏金额")
    return {"schema": POLICY_SCHEMA, "ratios_bps": dict(value["ratios_bps"]), "cap": cap}


def policy_from_text(ratios, cap):
    _keys(ratios, TIERS)
    values = {}
    for key in TIERS:
        text = ratios[key]
        if type(text) is not str or re.fullmatch(r"\d{1,3}(?:\.\d{1,2})?", text.strip()) is None:
            raise ValueError("比例须在0%到200%之间，最多两位小数")
        values[key] = int(Decimal(text.strip()) * 100)
    if type(cap) is not str or (cap.strip() and re.fullmatch(r"[1-9]\d{0,8}", cap.strip()) is None):
        raise ValueError("上限留空表示不设；否则填整数游戏金额")
    return validate_policy({"schema": POLICY_SCHEMA, "ratios_bps": values,
                            "cap": int(cap) if cap.strip() else None})


def is_default(policy):
    return validate_policy(policy) == default_policy()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def hash_session(sid):
    return hashlib.sha1(str(sid).encode("utf-8")).hexdigest()[:16] if sid else None


def validate_raw(raw, *, maximum=1e12):
    _keys(raw, TIERS)
    if any(type(v) not in (int, float) or not 0 <= v <= maximum or not math.isfinite(v)
           for v in raw.values()):
        raise ValueError("invalid raw quote")
    return dict(raw)


def apply_policy(raw, policy):
    raw, policy = validate_raw(raw), validate_policy(policy)
    if is_default(policy):
        return raw  # Preserve raw type/precision and the established display formatter.
    result = {}
    with localcontext() as ctx:
        ctx.prec = 40
        for key in TIERS:
            value = Decimal(str(raw[key])) * Decimal(policy["ratios_bps"][key]) / 10000
            if policy["cap"] is not None:
                value = min(value, Decimal(policy["cap"]))
            result[key] = int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return result


def quote_basis(result, *, eligible, source_mode="live", generation_hash=None):
    if not eligible or source_mode not in {"live", "manual", "manual_overlay"}:
        return {"schema": "quote_basis_v1", "status": "unavailable", "source_mode": source_mode}
    try:
        raw = validate_raw({k: result.get(k) for k in TIERS})
    except (ValueError, AttributeError):
        return {"schema": "quote_basis_v1", "status": "unavailable", "source_mode": source_mode}
    return {"schema": "quote_basis_v1", "status": "available", "source_mode": source_mode,
            "raw": raw, "generation_hash": generation_hash}


def monitor_generation(payload):
    """Hash only an actual monitor launch identity, never an ordinary snapshot fingerprint."""
    if type(payload) is not dict:
        return None
    started = payload.get("started_at")
    pid = payload.get("pid")
    if type(started) not in (int, float) or not math.isfinite(started) or started <= 0 or type(pid) is not int or pid <= 0:
        return None
    return fingerprint({"monitor_started_at": started, "monitor_pid": pid})


def read_monitor_generation(path):
    try:
        return monitor_generation(_read(path, RECORD_LIMIT))
    except (OSError, ValueError, TypeError):
        return None


def unknown(reason="invalid"):
    return {"schema": RECORD_SCHEMA, "status": "unknown",
            "reason": reason if reason in UNKNOWN_REASONS else "invalid"}


def validate_record(value, *, session_hash=None, now=None, allow_manual=False):
    """Validate unbound local structure/arithmetic, not settlement association.

    COVERAGE is only a target scope here. Settlement/upload/replay consumers must
    establish that scope with validate_settlement_record and an outer cutoff.
    """
    now = time.time() if now is None else now
    try:
        if len(_json(value).encode("utf-8")) > RECORD_LIMIT:
            raise ValueError("large record")
        if type(value) is dict and value.get("status") == "unknown":
            _keys(value, ("schema", "status", "reason"))
            if value["schema"] != RECORD_SCHEMA or value["reason"] not in UNKNOWN_REASONS:
                raise ValueError("invalid unknown")
            return dict(value)
        _keys(value, ("schema", "status", "coverage", "session_hash", "writer_id", "revision",
                      "source_mode", "input_fingerprint", "result_identity", "round", "action_round",
                      "observed_round", "phase", "generation_hash", "raw", "policy", "policy_fingerprint",
                      "is_default", "adjusted", "applied_at", "snapshot_at"))
        if value["schema"] != RECORD_SCHEMA or value["status"] != "recorded" or value["coverage"] != COVERAGE:
            raise ValueError("invalid schema")
        for key, length in (("session_hash", 16), ("writer_id", 32), ("policy_fingerprint", 64)):
            if type(value[key]) is not str or re.fullmatch(r"[0-9a-f]{%d}" % length, value[key]) is None:
                raise ValueError("invalid identifier")
        for key in ("input_fingerprint", "result_identity", "generation_hash"):
            if value[key] is not None and (type(value[key]) is not str or re.fullmatch(r"[0-9a-f]{64}", value[key]) is None):
                raise ValueError("invalid identity")
        for key in ("revision", "round", "action_round", "observed_round"):
            if type(value[key]) is not int or not 0 <= value[key] <= 2**31 - 1:
                raise ValueError("invalid revision/round")
        if value["revision"] < 1 or value["source_mode"] not in {"live", "manual_overlay", "manual"}:
            raise ValueError("invalid source")
        if value["source_mode"] == "manual" and not allow_manual:
            return unknown("manual")
        if value["phase"] not in {"reading", "bidding"}:
            raise ValueError("invalid phase")
        for key in ("applied_at", "snapshot_at"):
            if type(value[key]) not in (int, float) or not math.isfinite(value[key]) or value[key] <= 0:
                raise ValueError("invalid time")
            if value[key] > now:
                return unknown("future")
        if value["snapshot_at"] > value["applied_at"]:
            raise ValueError("time order")
        if session_hash is not None and value["session_hash"] != session_hash:
            return unknown("different_session")
        policy = validate_policy(value["policy"])
        if fingerprint(policy) != value["policy_fingerprint"] or type(value["is_default"]) is not bool or value["is_default"] != is_default(policy):
            raise ValueError("policy mismatch")
        expected = apply_policy(value["raw"], policy)
        actual = validate_raw(value["adjusted"], maximum=2e12)
        if actual != expected or (not is_default(policy) and any(type(v) is not int for v in actual.values())):
            raise ValueError("arithmetic mismatch")
        if is_default(policy) and any(type(actual[key]) is not type(expected[key]) for key in TIERS):
            raise ValueError("default identity type mismatch")
        return json.loads(_json(value))
    except (ValueError, TypeError, KeyError, OverflowError, InvalidOperation):
        return unknown("invalid")


def validate_settlement_record(value, *, settlement_cutoff, session_hash, now=None):
    """Associate client-reported evidence with one local settlement object's time.

    The required cutoff comes from that object's created_at / outer client_ts,
    never the quote itself, receipt time, or the validation clock. It does not
    establish the game server's exact settlement event time or an actual bid.
    """
    now = time.time() if now is None else now
    checked = validate_record(value, session_hash=session_hash, now=now)
    if checked.get("status") != "recorded":
        return checked
    if type(session_hash) is not str or not session_hash:
        return unknown("unavailable")
    try:
        if any(type(stamp) not in (int, float) or stamp <= 0 or not math.isfinite(stamp)
               for stamp in (settlement_cutoff, now)):
            return unknown("settlement_cutoff_invalid")
        if settlement_cutoff > now:
            return unknown("settlement_cutoff_invalid")
        if checked["applied_at"] >= settlement_cutoff:
            return unknown("not_before_settlement")
    except (TypeError, ValueError, OverflowError):
        return unknown("settlement_cutoff_invalid")
    return checked


def new_preferences():
    return {"schema": PREFS_SCHEMA, "revision": 0, "active_name": "默认", "schemes": {"默认": default_policy()}}


def validate_preferences(value):
    _keys(value, ("schema", "revision", "active_name", "schemes"))
    if value["schema"] != PREFS_SCHEMA or type(value["revision"]) is not int or value["revision"] < 0:
        raise ValueError("invalid preferences")
    schemes = value["schemes"]
    if type(schemes) is not dict or not 1 <= len(schemes) <= 17 or schemes.get("默认") != default_policy():
        raise ValueError("默认方案不可覆盖，最多16个自定义方案")
    for name, policy in schemes.items():
        if type(name) is not str or not 1 <= len(name) <= 24 or name != name.strip() or any(ord(c) < 32 for c in name):
            raise ValueError("方案名称须为1到24个字符")
        validate_policy(policy)
    if type(value["active_name"]) is not str or value["active_name"] not in schemes:
        raise ValueError("invalid active scheme")
    return json.loads(_json(value))


def _read(path, limit):
    with Path(path).open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("file too large")
    return json.loads(data)


def load_preferences(path):
    try:
        return validate_preferences(_read(path, 65536)), None
    except FileNotFoundError:
        return new_preferences(), None
    except (OSError, ValueError, TypeError, KeyError):
        return new_preferences(), "报价设置文件不可用，暂用默认；原文件已保留"


@contextmanager
def _locked(path):
    """Nonblocking OS lock; process exit releases it, unlike exclusive sentinel files."""
    lock = Path(str(path) + ".lock").open("a+b")
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt
            lock.seek(0, 2)
            if lock.tell() == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        acquired = True
        yield
    finally:
        if acquired and os.name == "nt":
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        lock.close()


def _atomic(path, value):
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(_json(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def save_preferences(path, *, expected_revision, name, policy):
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("invalid expected revision")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _locked(path):
        current, error = load_preferences(path)
        if error:
            raise ValueError(error)
        if current["revision"] != expected_revision:
            raise ValueError("报价设置已被另一窗口修改，请关闭后重新打开")
        policy = validate_policy(policy)
        if name == "默认" and not is_default(policy):
            raise ValueError("请为自定义比例填写新的方案名称")
        current["schemes"][name] = policy
        current["active_name"] = name
        current["revision"] += 1
        current = validate_preferences(current)
        _atomic(path, current)
        return current


def write_applied(path, record, *, now=None):
    now = time.time() if now is None else now
    checked = validate_record(record, now=now)
    if checked.get("status") != "recorded":
        return checked
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _locked(path):
            try:
                old = _read(path, RECORD_LIMIT)
            except FileNotFoundError:
                old = {}
            if type(old) is not dict:
                return unknown("invalid")
            if old and (set(old) != {"schema", "session_hash", "writer_id", "conflict", "record"}
                        or old.get("schema") != "quote_bridge_v1" or type(old.get("conflict")) is not bool):
                return unknown("invalid")
            same = old.get("session_hash") == checked["session_hash"]
            if same and (old.get("conflict") is True or old.get("writer_id") != checked["writer_id"]):
                _atomic(path, {"schema": "quote_bridge_v1", "session_hash": checked["session_hash"],
                               "writer_id": checked["writer_id"], "conflict": True, "record": unknown("writer_conflict")})
                return unknown("writer_conflict")
            if same:
                previous = validate_record(old.get("record"), now=now)
                if previous.get("status") == "recorded":
                    if checked["revision"] <= previous["revision"] or checked["applied_at"] < previous["applied_at"]:
                        return unknown("stale")
            envelope = {"schema": "quote_bridge_v1", "session_hash": checked["session_hash"],
                        "writer_id": checked["writer_id"], "conflict": False, "record": checked}
            if len(_json(envelope).encode("utf-8")) > RECORD_LIMIT:
                return unknown("invalid")
            _atomic(path, envelope)
            return checked
    except (OSError, ValueError, TypeError):
        return unknown("io_error")


def read_applied(path, *, session_hash, now=None, generation_hash=None):
    """Read a pending local record; settlement association needs its own cutoff."""
    now = time.time() if now is None else now
    try:
        with _locked(path):
            value = _read(path, RECORD_LIMIT)
        _keys(value, ("schema", "session_hash", "writer_id", "conflict", "record"))
        if value["schema"] != "quote_bridge_v1" or type(value["conflict"]) is not bool:
            return unknown("invalid")
        if not session_hash or value["session_hash"] != session_hash:
            return unknown("different_session")
        if value["conflict"]:
            return unknown("writer_conflict")
        record = validate_record(value["record"], session_hash=session_hash, now=now)
        if record.get("status") == "recorded":
            if record["generation_hash"] is not None and record["generation_hash"] != generation_hash:
                return unknown("unavailable")
            if record["writer_id"] != value["writer_id"]:
                return unknown("invalid")
            if now - record["snapshot_at"] > MAX_AGE or now - record["applied_at"] > MAX_AGE:
                return unknown("stale")
        return record
    except FileNotFoundError:
        return unknown("missing")
    except (OSError, ValueError, TypeError, KeyError):
        return unknown("io_error")
