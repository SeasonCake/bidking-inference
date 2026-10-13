#!/usr/bin/env python3
"""Read-only unit/state PRELIGHT for the independent endpoint_3 relay service."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from activation_endpoint3_relay import ENABLE_ENV, RELAY_VERSION
from activation_endpoint3_relay_state_admin import (
    MAX_AUTH_WINDOW_SECONDS,
    inspect_replay_database,
)


_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_OCTAL_RE = re.compile(r"^0?[0-7]{3,4}$", re.ASCII)
_VALUE_OPTIONS = frozenset(
    {
        "--mode",
        "--host",
        "--port",
        "--key-file",
        "--upstream-base",
        "--replay-db",
        "--relay-security-epoch",
        "--auth-window-seconds",
        "--timeout-seconds",
    }
)
_MULTI_VALUE_OPTIONS = frozenset({"--forbidden-key-sha256"})
_FLAG_OPTIONS = frozenset({"--enable", "--allow-http-loopback"})


class RelayDeployPreflightError(RuntimeError):
    """A fixed, privacy-safe relay deployment contract failure."""


@dataclass(frozen=True)
class FileFingerprint:
    state: str
    sha256: str | None
    device: int | None
    inode: int | None
    uid: int | None
    gid: int | None
    mode: int | None
    size: int | None


@dataclass(frozen=True)
class UnitContract:
    sha256: str
    enabled: bool
    security_epoch: int
    auth_window_seconds: int
    timeout_seconds: float
    config_sha256: str


def _fingerprint_file(path: Path, *, missing_ok: bool = False) -> FileFingerprint:
    try:
        before = path.lstat()
    except FileNotFoundError:
        if missing_ok:
            return FileFingerprint("missing", None, None, None, None, None, None, None)
        raise RelayDeployPreflightError("required_file_missing") from None
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise RelayDeployPreflightError("unsafe_file_type")
    try:
        raw = path.read_bytes()
        after = path.lstat()
    except OSError as exc:
        raise RelayDeployPreflightError("file_unreadable") from exc
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
    ):
        raise RelayDeployPreflightError("file_changed_during_preflight")
    return FileFingerprint(
        "ready",
        hashlib.sha256(raw).hexdigest(),
        int(after.st_dev),
        int(after.st_ino),
        int(after.st_uid),
        int(after.st_gid),
        stat.S_IMODE(after.st_mode),
        int(after.st_size),
    )


def _fingerprint_directory(path: Path) -> FileFingerprint:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return FileFingerprint("missing", None, None, None, None, None, None, None)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise RelayDeployPreflightError("unsafe_state_directory")
    return FileFingerprint(
        "ready",
        None,
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_uid),
        int(info.st_gid),
        stat.S_IMODE(info.st_mode),
        None,
    )


def _fingerprint_bundle(items: dict[str, FileFingerprint]) -> str:
    payload = {
        label: {
            "state": item.state,
            "sha256": item.sha256,
            "device": item.device,
            "inode": item.inode,
            "uid": item.uid,
            "gid": item.gid,
            "mode": item.mode,
            "size": item.size,
        }
        for label, item in sorted(items.items())
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def _unit_entries(raw: bytes) -> list[tuple[str, str, str]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise RelayDeployPreflightError("unit_not_utf8") from exc
    physical: list[str] = []
    pending = ""
    for source in text.splitlines():
        stripped = source.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        pending += stripped
        if pending.endswith("\\"):
            pending = pending[:-1] + " "
            continue
        physical.append(pending)
        pending = ""
    if pending:
        raise RelayDeployPreflightError("unit_unterminated_continuation")
    section = ""
    result: list[tuple[str, str, str]] = []
    for line in physical:
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if "=" not in line:
            raise RelayDeployPreflightError("unit_malformed_directive")
        key, value = line.split("=", 1)
        if not key or not value:
            raise RelayDeployPreflightError("unit_malformed_directive")
        result.append((section, key, value.strip()))
    return result


def _single_directive(
    entries: list[tuple[str, str, str]], section: str, name: str
) -> str:
    values = [value for item_section, key, value in entries if item_section == section and key == name]
    if len(values) != 1:
        raise RelayDeployPreflightError("unit_directive_not_exact_once")
    return values[0]


def _option_values(tokens: list[str], name: str) -> list[str]:
    values: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == name:
            if index + 1 >= len(tokens):
                raise RelayDeployPreflightError("unit_option_missing_value")
            values.append(tokens[index + 1])
            index += 2
            continue
        prefix = name + "="
        if token.startswith(prefix):
            values.append(token[len(prefix) :])
        index += 1
    return values


def _single_option(tokens: list[str], name: str) -> str:
    values = _option_values(tokens, name)
    if len(values) != 1:
        raise RelayDeployPreflightError("unit_option_not_exact_once")
    return values[0]


def _parse_octal(value: str) -> int:
    if _OCTAL_RE.fullmatch(value) is None:
        raise RelayDeployPreflightError("unit_mode_malformed")
    return int(value, 8)


def _parse_positive_int(value: str, reason: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RelayDeployPreflightError(reason) from exc
    if parsed <= 0:
        raise RelayDeployPreflightError(reason)
    return parsed


def _parse_positive_float(value: str, reason: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RelayDeployPreflightError(reason) from exc
    if not (0 < parsed <= 30):
        raise RelayDeployPreflightError(reason)
    return parsed


def _same_resolved_path(left: str, right: str) -> bool:
    try:
        return Path(left).resolve() == Path(right).resolve()
    except (OSError, ValueError):
        return False


def validate_unit(
    path: Path,
    *,
    expected_python: str,
    expected_relay_script: str,
    expected_user: str,
    expected_group: str,
    expected_umask: int,
    expected_working_directory: str,
    expected_state_directory: str,
    expected_state_directory_mode: int,
    expected_key_file: str,
    expected_replay_db: str,
    expected_security_epoch: int | None,
    expected_auth_window_seconds: int,
    expected_timeout_seconds: float,
    expected_enabled: bool | None,
) -> UnitContract:
    fingerprint = _fingerprint_file(path)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != fingerprint.sha256:
        raise RelayDeployPreflightError("unit_changed_during_preflight")
    entries = _unit_entries(raw)
    if _single_directive(entries, "Service", "User") != expected_user:
        raise RelayDeployPreflightError("unit_user_mismatch")
    if _single_directive(entries, "Service", "Group") != expected_group:
        raise RelayDeployPreflightError("unit_group_mismatch")
    if _parse_octal(_single_directive(entries, "Service", "UMask")) != expected_umask:
        raise RelayDeployPreflightError("unit_umask_mismatch")
    if _single_directive(entries, "Service", "WorkingDirectory") != expected_working_directory:
        raise RelayDeployPreflightError("unit_working_directory_mismatch")
    if _single_directive(entries, "Service", "StateDirectory") != expected_state_directory:
        raise RelayDeployPreflightError("unit_state_directory_mismatch")
    if (
        _parse_octal(_single_directive(entries, "Service", "StateDirectoryMode"))
        != expected_state_directory_mode
    ):
        raise RelayDeployPreflightError("unit_state_directory_mode_mismatch")
    environment = _single_directive(entries, "Service", "Environment")
    normalized_environment = environment.strip('"\'')
    if normalized_environment not in {f"{ENABLE_ENV}=0", f"{ENABLE_ENV}=1"}:
        raise RelayDeployPreflightError("unit_kill_switch_mismatch")
    unit_enabled = normalized_environment == f"{ENABLE_ENV}=1"
    if expected_enabled is not None and unit_enabled is not expected_enabled:
        raise RelayDeployPreflightError("unit_kill_switch_mismatch")
    try:
        tokens = shlex.split(_single_directive(entries, "Service", "ExecStart"), posix=True)
    except ValueError as exc:
        raise RelayDeployPreflightError("unit_execstart_malformed") from exc
    if len(tokens) < 2 or tokens[0] != expected_python or tokens[1] != expected_relay_script:
        raise RelayDeployPreflightError("unit_relay_script_mismatch")
    if tokens.count(expected_relay_script) != 1:
        raise RelayDeployPreflightError("unit_relay_script_mismatch")
    if tokens.count("--preflight"):
        raise RelayDeployPreflightError("unit_contains_preflight_mode")
    index = 2
    while index < len(tokens):
        token = tokens[index]
        if not token.startswith("--"):
            raise RelayDeployPreflightError("unit_unexpected_positional_argument")
        option = token.split("=", 1)[0]
        if option not in _VALUE_OPTIONS | _MULTI_VALUE_OPTIONS | _FLAG_OPTIONS:
            raise RelayDeployPreflightError("unit_unknown_option")
        if option in _VALUE_OPTIONS | _MULTI_VALUE_OPTIONS and "=" not in token:
            if index + 1 >= len(tokens) or tokens[index + 1].startswith("--"):
                raise RelayDeployPreflightError("unit_option_missing_value")
            index += 2
            continue
        index += 1
    if _single_option(tokens, "--mode") != "origin":
        raise RelayDeployPreflightError("unit_mode_mismatch")
    host = _single_option(tokens, "--host")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RelayDeployPreflightError("unit_bind_host_not_loopback")
    port = _parse_positive_int(_single_option(tokens, "--port"), "unit_port_malformed")
    if port > 65535:
        raise RelayDeployPreflightError("unit_port_malformed")
    if not _same_resolved_path(_single_option(tokens, "--key-file"), expected_key_file):
        raise RelayDeployPreflightError("unit_key_path_mismatch")
    if not _same_resolved_path(_single_option(tokens, "--replay-db"), expected_replay_db):
        raise RelayDeployPreflightError("unit_replay_path_mismatch")
    upstream = _single_option(tokens, "--upstream-base")
    parsed_upstream = urlsplit(upstream)
    if (
        parsed_upstream.scheme not in {"http", "https"}
        or parsed_upstream.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed_upstream.username is not None
        or parsed_upstream.password is not None
        or parsed_upstream.path not in {"", "/"}
        or parsed_upstream.query
        or parsed_upstream.fragment
    ):
        raise RelayDeployPreflightError("unit_origin_upstream_not_loopback")
    security_epoch = _parse_positive_int(
        _single_option(tokens, "--relay-security-epoch"), "unit_epoch_malformed"
    )
    auth_window = _parse_positive_int(
        _single_option(tokens, "--auth-window-seconds"), "unit_auth_window_malformed"
    )
    timeout_seconds = _parse_positive_float(
        _single_option(tokens, "--timeout-seconds"), "unit_timeout_malformed"
    )
    if expected_security_epoch is not None and security_epoch != expected_security_epoch:
        raise RelayDeployPreflightError("unit_epoch_mismatch")
    if auth_window != expected_auth_window_seconds:
        raise RelayDeployPreflightError("unit_auth_window_mismatch")
    if timeout_seconds != expected_timeout_seconds:
        raise RelayDeployPreflightError("unit_timeout_mismatch")
    if tokens.count("--allow-http-loopback") != 1:
        raise RelayDeployPreflightError("unit_loopback_origin_contract_mismatch")
    if tokens.count("--enable") != int(unit_enabled):
        raise RelayDeployPreflightError("unit_enable_flag_mismatch")
    digests = _option_values(tokens, "--forbidden-key-sha256")
    if len(digests) < 3 or len(set(digests)) != len(digests) or any(
        _DIGEST_RE.fullmatch(item) is None for item in digests
    ):
        raise RelayDeployPreflightError("unit_forbidden_digest_contract_invalid")
    config = {
        "relay_version": RELAY_VERSION,
        "enabled": unit_enabled,
        "mode": "origin",
        "upstream_sha256": hashlib.sha256(upstream.encode("utf-8")).hexdigest(),
        "key_path_sha256": hashlib.sha256(expected_key_file.encode("utf-8")).hexdigest(),
        "replay_path_sha256": hashlib.sha256(expected_replay_db.encode("utf-8")).hexdigest(),
        "security_epoch": security_epoch,
        "auth_window_seconds": auth_window,
        "timeout_seconds": timeout_seconds,
        "forbidden_digest_set_sha256": hashlib.sha256(
            "\n".join(sorted(digests)).encode("ascii")
        ).hexdigest(),
    }
    config_sha = hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()
    return UnitContract(
        fingerprint.sha256 or "",
        unit_enabled,
        security_epoch,
        auth_window,
        timeout_seconds,
        config_sha,
    )


def _validate_key(
    path: Path,
    *,
    expected_uid: int,
    expected_gid: int,
    expected_mode: int,
) -> FileFingerprint:
    fingerprint = _fingerprint_file(path)
    if fingerprint.uid != expected_uid or fingerprint.gid != expected_gid:
        raise RelayDeployPreflightError("relay_key_owner_mismatch")
    if os.name == "posix" and fingerprint.mode != expected_mode:
        raise RelayDeployPreflightError("relay_key_mode_mismatch")
    if fingerprint.size is None or not (32 <= fingerprint.size <= 4096):
        raise RelayDeployPreflightError("relay_key_size_invalid")
    raw = path.read_bytes()
    value = raw.strip()
    if not value or b"\n" in value or b"\r" in value:
        raise RelayDeployPreflightError("relay_key_format_invalid")
    return fingerprint


def _authoritative_fingerprints(
    *,
    installed_unit: Path,
    installed_unit_absent: bool,
    key_file: Path,
    replay_db: Path,
) -> dict[str, FileFingerprint]:
    return {
        "installed_unit": _fingerprint_file(
            installed_unit, missing_ok=installed_unit_absent
        ),
        "key": _fingerprint_file(key_file),
        "replay_db": _fingerprint_file(replay_db),
        "replay_state_directory": _fingerprint_directory(replay_db.parent),
    }


def _positive_int_arg(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _octal_arg(value: str) -> int:
    if _OCTAL_RE.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("must be octal")
    return int(value, 8)


def _auth_window_arg(value: str) -> int:
    parsed = _positive_int_arg(value)
    if parsed > MAX_AUTH_WINDOW_SECONDS:
        raise argparse.ArgumentTypeError("auth window exceeds fixed maximum")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-unit", required=True)
    parser.add_argument("--installed-unit", required=True)
    parser.add_argument("--installed-unit-absent", action="store_true")
    parser.add_argument("--rollback-unit", required=True)
    parser.add_argument("--expected-python", required=True)
    parser.add_argument("--expected-relay-script", required=True)
    parser.add_argument("--expected-user", required=True)
    parser.add_argument("--expected-group", required=True)
    parser.add_argument("--expected-umask", required=True, type=_octal_arg)
    parser.add_argument("--expected-working-directory", required=True)
    parser.add_argument("--expected-state-directory", required=True)
    parser.add_argument("--expected-state-directory-mode", required=True, type=_octal_arg)
    parser.add_argument("--key-file", required=True)
    parser.add_argument("--expected-key-uid", required=True, type=int)
    parser.add_argument("--expected-key-gid", required=True, type=int)
    parser.add_argument("--expected-key-mode", required=True, type=_octal_arg)
    parser.add_argument("--replay-db", required=True)
    parser.add_argument("--relay-security-epoch", required=True, type=_positive_int_arg)
    parser.add_argument("--auth-window-seconds", required=True, type=_auth_window_arg)
    parser.add_argument("--timeout-seconds", required=True, type=float)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    candidate_unit = Path(args.candidate_unit)
    installed_unit = Path(args.installed_unit)
    rollback_unit = Path(args.rollback_unit)
    key_file = Path(args.key_file)
    replay_db = Path(args.replay_db)
    try:
        before_items = _authoritative_fingerprints(
            installed_unit=installed_unit,
            installed_unit_absent=args.installed_unit_absent,
            key_file=key_file,
            replay_db=replay_db,
        )
        before_sha = _fingerprint_bundle(before_items)
        if args.installed_unit_absent and before_items["installed_unit"].state != "missing":
            raise RelayDeployPreflightError("installed_unit_expected_absent")
        if not args.installed_unit_absent and before_items["installed_unit"].state != "ready":
            raise RelayDeployPreflightError("installed_unit_expected_present")
        expected_uid = args.expected_key_uid
        expected_gid = args.expected_key_gid
        if os.name == "posix":
            try:
                import grp
                import pwd

                resolved_uid = pwd.getpwnam(args.expected_user).pw_uid
                resolved_gid = grp.getgrnam(args.expected_group).gr_gid
            except (KeyError, OSError) as exc:
                raise RelayDeployPreflightError("unit_account_lookup_failed") from exc
            if expected_uid != resolved_uid or expected_gid != resolved_gid:
                raise RelayDeployPreflightError("unit_account_identity_mismatch")
            expected_uid = resolved_uid
            expected_gid = resolved_gid
        if replay_db.parent.name != args.expected_state_directory:
            raise RelayDeployPreflightError("relay_state_directory_path_mismatch")
        state_directory = before_items["replay_state_directory"]
        if state_directory.state != "ready":
            raise RelayDeployPreflightError("relay_state_directory_missing")
        if (
            state_directory.uid != expected_uid
            or state_directory.gid != expected_gid
        ):
            raise RelayDeployPreflightError("relay_state_directory_owner_mismatch")
        if os.name == "posix" and state_directory.mode != args.expected_state_directory_mode:
            raise RelayDeployPreflightError("relay_state_directory_mode_mismatch")
        replay_file = before_items["replay_db"]
        if replay_file.uid != expected_uid or replay_file.gid != expected_gid:
            raise RelayDeployPreflightError("relay_replay_store_owner_mismatch")
        if os.name == "posix" and replay_file.mode != args.expected_key_mode:
            raise RelayDeployPreflightError("relay_replay_store_mode_mismatch")
        _validate_key(
            key_file,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
            expected_mode=args.expected_key_mode,
        )
        replay = inspect_replay_database(
            replay_db,
            expected_security_epoch=args.relay_security_epoch,
            expected_auth_window_seconds=args.auth_window_seconds,
        )
        if not replay.ready:
            raise RelayDeployPreflightError("relay_replay_store_not_ready")
        shared = {
            "expected_python": args.expected_python,
            "expected_relay_script": args.expected_relay_script,
            "expected_user": args.expected_user,
            "expected_group": args.expected_group,
            "expected_umask": args.expected_umask,
            "expected_working_directory": args.expected_working_directory,
            "expected_state_directory": args.expected_state_directory,
            "expected_state_directory_mode": args.expected_state_directory_mode,
            "expected_key_file": str(key_file),
            "expected_replay_db": str(replay_db),
            "expected_auth_window_seconds": args.auth_window_seconds,
            "expected_timeout_seconds": args.timeout_seconds,
        }
        candidate = validate_unit(
            candidate_unit,
            expected_security_epoch=args.relay_security_epoch,
            expected_enabled=True,
            **shared,
        )
        rollback = validate_unit(
            rollback_unit,
            expected_security_epoch=None,
            expected_enabled=False,
            **shared,
        )
        if args.installed_unit_absent:
            installed = None
            before_epoch = 0
            installed_state = "missing"
            installed_sha = None
        else:
            installed = validate_unit(
                installed_unit,
                expected_security_epoch=None,
                expected_enabled=None,
                **shared,
            )
            before_epoch = installed.security_epoch
            installed_state = "ready"
            installed_sha = installed.sha256
        if candidate.security_epoch < before_epoch:
            raise RelayDeployPreflightError("relay_security_epoch_downgrade")
        if rollback.security_epoch < candidate.security_epoch:
            raise RelayDeployPreflightError("rollback_security_epoch_downgrade")
        after_items = _authoritative_fingerprints(
            installed_unit=installed_unit,
            installed_unit_absent=args.installed_unit_absent,
            key_file=key_file,
            replay_db=replay_db,
        )
        after_sha = _fingerprint_bundle(after_items)
        if after_items != before_items or after_sha != before_sha:
            raise RelayDeployPreflightError("preflight_modified_authoritative_state")
        posix_metadata_verified = os.name == "posix"
        payload = {
            "schema_version": 1,
            "mode": "preflight_only",
            "status": (
                "eligible" if posix_metadata_verified else "target_host_prelight_pending"
            ),
            "relay_version": RELAY_VERSION,
            "installed_unit_state": installed_state,
            "installed_unit_sha256": installed_sha,
            "candidate_unit_sha256": candidate.sha256,
            "candidate_config_sha256": candidate.config_sha256,
            "rollback_unit_sha256": rollback.sha256,
            "rollback_config_sha256": rollback.config_sha256,
            "relay_security_epoch_before": before_epoch,
            "relay_security_epoch_after": candidate.security_epoch,
            "rollback_relay_security_epoch": rollback.security_epoch,
            "auth_window_seconds": candidate.auth_window_seconds,
            "timeout_seconds": candidate.timeout_seconds,
            "replay_store_state": replay.state,
            "replay_store_schema_version": replay.database_schema_version,
            "key_owner_match": True,
            "key_group_match": True,
            "key_mode_match": posix_metadata_verified,
            "posix_owner_mode_verified": posix_metadata_verified,
            "target_host_prelight_required": not posix_metadata_verified,
            "authoritative_before_sha256": before_sha,
            "authoritative_after_sha256": after_sha,
            "authoritative_unchanged": True,
        }
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 0 if posix_metadata_verified else 4
    except (OSError, TypeError, ValueError, RelayDeployPreflightError) as exc:
        reason = str(exc) if type(exc) is RelayDeployPreflightError else type(exc).__name__
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "mode": "preflight_only",
                    "status": "blocked",
                    "reason": reason,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
