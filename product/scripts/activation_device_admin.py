#!/usr/bin/env python3
"""Privacy-preserving device revocation administration.

The tool never prints or stores a raw device fingerprint or complete token.  Mutating
commands are dry-run unless ``--apply`` is present.  It operates on the activation
service's private state directory and uses the same domain-separated HMAC contract as
``activation_server.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import activation_server
import upload_identity_store


_BLOCK_REASON_CODES = frozenset({"confirmed_abuse"})
_UNBLOCK_REASON_CODES = frozenset({"manual_appeal"})
_ACTOR_CODES = frozenset({"author"})
_EVIDENCE_REF_RE = re.compile(r"^(?:INC|APL)-[A-Z0-9]{12,40}$")


def _uid_alias(block_key: bytes, uid: str) -> str:
    return hmac.new(
        block_key,
        b"uid-admin-alias-v1|" + uid.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:16]


def _path(state_dir: Path, name: str) -> Path:
    return state_dir / name


def _read_secret(path: Path) -> bytes:
    try:
        secret = path.read_bytes().strip()
    except OSError as exc:
        raise RuntimeError("activation key unavailable") from exc
    if not secret:
        raise RuntimeError("activation key unavailable")
    return secret


def _load_revoked(path: Path) -> set[str]:
    return activation_server._load_lines(path)


def _atomic_write_lines(path: Path, values: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text("".join(f"{value}\n" for value in sorted(values)), encoding="utf-8")
    if path.exists():
        os.chmod(tmp, path.stat().st_mode & 0o777)
    os.replace(tmp, path)


def _append_audit(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)


def _backup(path: Path) -> str | None:
    if not path.exists():
        return None
    stamp = f"{int(time.time())}-{os.getpid()}"
    backup = path.with_name(f"{path.name}.before-admin-{stamp}.bak")
    shutil.copy2(path, backup)
    return backup.name


def _allowlisted_code(value: str, *, label: str, allowed: frozenset[str]) -> str:
    if value not in allowed:
        raise RuntimeError(f"invalid {label}")
    return value


def _evidence_reference(value: str) -> str:
    if _EVIDENCE_REF_RE.fullmatch(value or "") is None:
        raise RuntimeError("invalid evidence reference")
    return value


def _load_context(args: argparse.Namespace) -> tuple[Path, bytes, dict, dict, set[str], dict]:
    state_dir = Path(args.state_dir).resolve()
    block_key = _read_secret(_path(state_dir, args.device_block_key_file))
    bindings = activation_server._load_bindings_state(_path(state_dir, args.bindings_file))
    bindings = {
        uid: activation_server.activation_core.binding_state(record)[0]
        for uid, record in bindings.items()
    }
    starts = activation_server._load_paid_starts_state(
        _path(state_dir, args.paid_starts_file),
        now=int(time.time()),
    )
    revoked = _load_revoked(_path(state_dir, args.revoked_file))
    blocks = activation_server._load_device_blocks_state(
        _path(state_dir, args.device_blocks_file)
    )
    return state_dir, block_key, bindings, starts, revoked, blocks


def _session_target(args: argparse.Namespace):
    db_file = getattr(args, "upload_identity_db_file", None)
    expected_epoch = getattr(args, "upload_security_epoch", None)
    if type(db_file) is not str or not db_file:  # noqa: E721
        raise RuntimeError("upload identity database is required")
    if type(expected_epoch) is not int or expected_epoch <= 0:  # noqa: E721
        raise RuntimeError("upload security epoch is required")
    store = upload_identity_store.UploadIdentityStore(Path(db_file).resolve())
    target = store.session_admin_target(args.session_hash)
    if target.security_epochs != (expected_epoch,):
        raise RuntimeError("session security epoch mismatch")
    return target


def _inspect_session(args: argparse.Namespace) -> int:
    target = _session_target(args)
    print(
        json.dumps(
            {
                "ok": True,
                "candidate": {
                    "device_alias": target.device_block_id[:16],
                    "subject_kinds": list(target.subject_kinds),
                    "dispositions": list(target.dispositions),
                    "record_count": target.record_count,
                    "security_epoch": target.security_epochs[0],
                },
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0


def _block_session(args: argparse.Namespace) -> int:
    state_dir, _, _, _, _, blocks = _load_context(args)
    target = _session_target(args)
    reason = _allowlisted_code(
        args.reason,
        label="reason",
        allowed=_BLOCK_REASON_CODES,
    )
    evidence_ref = _evidence_reference(args.evidence_ref)
    actor = _allowlisted_code(args.actor, label="actor", allowed=_ACTOR_CODES)
    block_id = target.device_block_id
    already_blocked = block_id in blocks["blocked"]
    report = {
        "ok": True,
        "applied": bool(args.apply),
        "already_blocked": already_blocked,
        "device_alias": block_id[:16],
        "subject_kinds": list(target.subject_kinds),
        "matched_record_count": target.record_count,
        "uid_revocations_unchanged": True,
    }
    if not args.apply:
        print(json.dumps(report, ensure_ascii=True, sort_keys=True))
        return 0
    now = int(time.time())
    if not already_blocked:
        _backup(_path(state_dir, args.device_blocks_file))
        blocks["blocked"][block_id] = {
            "blocked_at": now,
            "reason": reason,
            "evidence_ref": evidence_ref,
            "actor": actor,
        }
        activation_server._atomic_write_json(
            _path(state_dir, args.device_blocks_file),
            blocks,
        )
    _append_audit(
        _path(state_dir, args.audit_file),
        {
            "ts": now,
            "action": "block_session",
            "changed": not already_blocked,
            "device_alias": block_id[:16],
            "reason": reason,
            "evidence_ref": evidence_ref,
            "actor": actor,
            "subject_kinds": list(target.subject_kinds),
            "matched_record_count": target.record_count,
        },
    )
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


def _unblock_session(args: argparse.Namespace) -> int:
    state_dir, _, _, _, _, blocks = _load_context(args)
    target = _session_target(args)
    reason = _allowlisted_code(
        args.reason,
        label="reason",
        allowed=_UNBLOCK_REASON_CODES,
    )
    evidence_ref = _evidence_reference(args.evidence_ref)
    actor = _allowlisted_code(args.actor, label="actor", allowed=_ACTOR_CODES)
    block_id = target.device_block_id
    existed = block_id in blocks["blocked"]
    report = {
        "ok": True,
        "applied": bool(args.apply),
        "was_blocked": existed,
        "device_alias": block_id[:16],
        "subject_kinds": list(target.subject_kinds),
        "uid_revocations_unchanged": True,
    }
    if not args.apply:
        print(json.dumps(report, ensure_ascii=True, sort_keys=True))
        return 0
    if existed:
        _backup(_path(state_dir, args.device_blocks_file))
        del blocks["blocked"][block_id]
        activation_server._atomic_write_json(
            _path(state_dir, args.device_blocks_file),
            blocks,
        )
    _append_audit(
        _path(state_dir, args.audit_file),
        {
            "ts": int(time.time()),
            "action": "unblock_session",
            "changed": existed,
            "device_alias": block_id[:16],
            "reason": reason,
            "evidence_ref": evidence_ref,
            "actor": actor,
            "subject_kinds": list(target.subject_kinds),
        },
    )
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


def _inspect(args: argparse.Namespace) -> int:
    _, block_key, bindings, starts, revoked, blocks = _load_context(args)
    rows = []
    for index, uid in enumerate(args.uid, start=1):
        device = bindings.get(uid)
        block_id = (
            activation_server.device_block_id(block_key, device)
            if isinstance(device, str) and device
            else None
        )
        started_at = starts.get(uid)
        rows.append(
            {
                "candidate_index": index,
                "uid_alias": _uid_alias(block_key, uid),
                "paid_started_at": (
                    datetime.fromtimestamp(started_at, timezone.utc).isoformat()
                    if isinstance(started_at, int) and not isinstance(started_at, bool)
                    else None
                ),
                "revoked": uid in revoked,
                "bound": block_id is not None,
                "device_alias": block_id[:16] if block_id else None,
                "device_blocked": bool(block_id and block_id in blocks["blocked"]),
                "same_device_uid_count": (
                    sum(1 for item in bindings.values() if item == device)
                    if block_id
                    else 0
                ),
            }
        )
    print(json.dumps({"ok": True, "candidates": rows}, ensure_ascii=True, sort_keys=True))
    return 0


def _block(args: argparse.Namespace) -> int:
    state_dir, block_key, bindings, _, revoked, blocks = _load_context(args)
    device = bindings.get(args.uid)
    if not isinstance(device, str) or not device:
        raise RuntimeError("target uid has no valid device binding")
    block_id = activation_server.device_block_id(block_key, device)
    reason = _allowlisted_code(
        args.reason,
        label="reason",
        allowed=_BLOCK_REASON_CODES,
    )
    evidence_ref = _evidence_reference(args.evidence_ref)
    actor = _allowlisted_code(
        args.actor,
        label="actor",
        allowed=_ACTOR_CODES,
    )
    same_device_uids = {
        uid for uid, bound_device in bindings.items() if bound_device == device
    }
    already_blocked = block_id in blocks["blocked"]
    newly_revoked: set[str] = set()
    if args.revoke_bound_uids:
        newly_revoked = same_device_uids - revoked
    report = {
        "ok": True,
        "applied": bool(args.apply),
        "already_blocked": already_blocked,
        "device_alias": block_id[:16],
        "matched_uid_count": len(same_device_uids),
        "newly_revoked_count": len(newly_revoked),
        "target_uid_alias": _uid_alias(block_key, args.uid),
    }
    if not args.apply:
        print(json.dumps(report, ensure_ascii=True, sort_keys=True))
        return 0

    now = int(time.time())
    if not already_blocked:
        _backup(_path(state_dir, args.device_blocks_file))
        blocks["blocked"][block_id] = {
            "blocked_at": now,
            "reason": reason,
            "evidence_ref": evidence_ref,
            "actor": actor,
        }
        activation_server._atomic_write_json(
            _path(state_dir, args.device_blocks_file),
            blocks,
        )
    if newly_revoked:
        _backup(_path(state_dir, args.revoked_file))
        _atomic_write_lines(
            _path(state_dir, args.revoked_file),
            revoked | newly_revoked,
        )
    _append_audit(
        _path(state_dir, args.audit_file),
        {
            "ts": now,
            "action": "block",
            "changed": not already_blocked,
            "device_alias": block_id[:16],
            "reason": reason,
            "evidence_ref": evidence_ref,
            "actor": actor,
            "matched_uid_count": len(same_device_uids),
            "newly_revoked_count": len(newly_revoked),
        },
    )
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


def _unblock(args: argparse.Namespace) -> int:
    state_dir, block_key, bindings, _, _, blocks = _load_context(args)
    device = bindings.get(args.uid)
    if not isinstance(device, str) or not device:
        raise RuntimeError("target uid has no valid device binding")
    block_id = activation_server.device_block_id(block_key, device)
    reason = _allowlisted_code(
        args.reason,
        label="reason",
        allowed=_UNBLOCK_REASON_CODES,
    )
    evidence_ref = _evidence_reference(args.evidence_ref)
    actor = _allowlisted_code(
        args.actor,
        label="actor",
        allowed=_ACTOR_CODES,
    )
    existed = block_id in blocks["blocked"]
    report = {
        "ok": True,
        "applied": bool(args.apply),
        "was_blocked": existed,
        "device_alias": block_id[:16],
        "uid_revocations_unchanged": True,
    }
    if not args.apply:
        print(json.dumps(report, ensure_ascii=True, sort_keys=True))
        return 0
    if existed:
        _backup(_path(state_dir, args.device_blocks_file))
        del blocks["blocked"][block_id]
        activation_server._atomic_write_json(
            _path(state_dir, args.device_blocks_file),
            blocks,
        )
    _append_audit(
        _path(state_dir, args.audit_file),
        {
            "ts": int(time.time()),
            "action": "unblock",
            "changed": existed,
            "device_alias": block_id[:16],
            "reason": reason,
            "evidence_ref": evidence_ref,
            "actor": actor,
        },
    )
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


def _list_blocks(args: argparse.Namespace) -> int:
    state_dir = Path(args.state_dir).resolve()
    blocks = activation_server._load_device_blocks_state(
        _path(state_dir, args.device_blocks_file)
    )
    rows = [
        {
            "device_alias": block_id[:16],
            "blocked_at": record["blocked_at"],
            "reason": record["reason"],
            "evidence_ref": record["evidence_ref"],
            "actor": record["actor"],
        }
        for block_id, record in sorted(blocks["blocked"].items())
    ]
    print(json.dumps({"ok": True, "blocked": rows}, ensure_ascii=True, sort_keys=True))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", default=".")
    parser.add_argument("--device-block-key-file", default="device_block_key.txt")
    parser.add_argument("--bindings-file", default="bindings.json")
    parser.add_argument("--paid-starts-file", default="paid_starts.json")
    parser.add_argument("--revoked-file", default="revoked.txt")
    parser.add_argument("--device-blocks-file", default="device_blocks.json")
    parser.add_argument("--audit-file", default="device_block_audit.jsonl")
    parser.add_argument("--upload-identity-db-file")
    parser.add_argument("--upload-security-epoch", type=int)
    commands = parser.add_subparsers(dest="command", required=True)

    inspect = commands.add_parser("inspect", help="read-only redacted candidate lookup")
    inspect.add_argument("--uid", action="append", required=True)
    inspect.set_defaults(handler=_inspect)

    block = commands.add_parser("block", help="block the device currently bound to one uid")
    block.add_argument("--uid", required=True)
    block.add_argument("--reason", required=True)
    block.add_argument("--evidence-ref", required=True)
    block.add_argument("--actor", default="author")
    block.add_argument("--revoke-bound-uids", action="store_true")
    block.add_argument("--apply", action="store_true")
    block.set_defaults(handler=_block)

    unblock = commands.add_parser("unblock", help="remove only the device block")
    unblock.add_argument("--uid", required=True)
    unblock.add_argument("--reason", required=True)
    unblock.add_argument("--evidence-ref", required=True)
    unblock.add_argument("--actor", default="author")
    unblock.add_argument("--apply", action="store_true")
    unblock.set_defaults(handler=_unblock)

    list_blocks = commands.add_parser("list", help="list redacted blocked-device records")
    list_blocks.set_defaults(handler=_list_blocks)

    inspect_session = commands.add_parser(
        "inspect-session",
        help="resolve one trusted upload session without exposing private identity",
    )
    inspect_session.add_argument("--session-hash", required=True)
    inspect_session.set_defaults(handler=_inspect_session)

    block_session = commands.add_parser(
        "block-session",
        help="block the trusted device bound to one upload session",
    )
    block_session.add_argument("--session-hash", required=True)
    block_session.add_argument("--reason", required=True)
    block_session.add_argument("--evidence-ref", required=True)
    block_session.add_argument("--actor", default="author")
    block_session.add_argument("--apply", action="store_true")
    block_session.set_defaults(handler=_block_session)

    unblock_session = commands.add_parser(
        "unblock-session",
        help="remove the trusted device block bound to one upload session",
    )
    unblock_session.add_argument("--session-hash", required=True)
    unblock_session.add_argument("--reason", required=True)
    unblock_session.add_argument("--evidence-ref", required=True)
    unblock_session.add_argument("--actor", default="author")
    unblock_session.add_argument("--apply", action="store_true")
    unblock_session.set_defaults(handler=_unblock_session)
    return parser


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv[1:])
    try:
        return int(args.handler(args))
    except (
        RuntimeError,
        activation_server.StateFileError,
        upload_identity_store.UploadIdentityStateError,
        OSError,
        ValueError,
    ) as exc:
        print(
            json.dumps(
                {"ok": False, "error": str(exc), "error_type": type(exc).__name__},
                ensure_ascii=True,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
