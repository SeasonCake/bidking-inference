"""Recover complete source-mixed settlements from the collector quarantine.

The post-update collector originally quarantined every non-official warehouse
source.  That removed a large number of otherwise complete settlements from
the normal big-data pool.  The current policy is:

* ranked 55xx/56xx unknown rows are official (player warehouses cannot occur);
* non-ranked unknown/player rows are accepted as mixed-source observations;
* malformed source pairs, incomplete settlements, declared shortfalls and
  invalid values remain quarantined.

Dry-run is the default.  ``--apply`` requires a new backup directory and the
collector service must be stopped.  Both JSONL outputs are built as adjacent
temporary files, verified, and then replaced.  Processing is streaming; row
payloads are never accumulated in memory.

The CLI requires an explicit policy.  ``legacy-mixed-source`` preserves the
original July recovery contract.  ``d66aee-current`` additionally binds every
promoted row to the deployed collector authority and only reprojects either
the exact old PW2/map-missing cohort or the exact hotfix3/4 d66aee-full-table
false-stale cohort through the current server-owned map table.
``update606-current`` remains a CLI alias for the same current policy.
Malformed, mixed-generation or otherwise unverifiable PW2 rows remain in the
quarantine file.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import ModuleType
from typing import Any, BinaryIO, Iterator


RANKED_MAP_PREFIXES = frozenset({55, 56})
UNKNOWN_SOURCE = "postupdate_unknown_warehouse_source"
OFFICIAL_SOURCE = "official_random_warehouse"
PLAYER_SOURCE = "player_custom_warehouse"
SOURCE_PENDING_REASON = "warehouse_source_pending"
UPDATE606_UNOPENED_MAP_IDS = frozenset({5506, 6001})
_OLD_PW2_SCHEMA = "player_warehouse_source_pw2_v1"
_OLD_PW2_GENERATION = "postupdate_player_warehouse_activity"
_OLD_PW2_MAP_BUILD = "24332441"
_OLD_PW2_MAP_SHA256 = (
    "4f5c1e225b6434fefdaceaae3ac5ca1bfa33604484dc8412bfc78f42ee510b69"
)
_OLD_PW2_SOURCE_CONTRACT_SHA256 = (
    "5bad92a2219fed38d264a03f98f58090ae76a9f70158ed3148a22f6d0c7254a0"
)
NON_SOURCE_REASONS = frozenset(
    {
        "settlement_incomplete",
        "declared_shortfall",
        "invalid_value",
        "update606_map_unopened",
    }
)


class Update606RecoveryPolicy:
    """Server-owned one-time policy for current-authority recovery."""

    def __init__(
        self,
        collector: ModuleType,
        map_meta: dict[int, dict[str, Any]],
    ) -> None:
        self.collector = collector
        self.map_meta = map_meta

    def _legacy_map_projection(
        self,
        row: dict[str, Any],
    ) -> dict[str, Any] | None:
        collector = self.collector
        map_id = row.get("map_id")
        if type(map_id) is not int:  # noqa: E721
            return None
        auction_mode = collector._PW2_TRUSTED_AUCTION_MODE_BY_MAP.get(map_id)
        if type(auction_mode) is not str or map_id in UPDATE606_UNOPENED_MAP_IDS:
            return None
        received = row.get("received_ts")
        if type(received) not in {int, float}:  # noqa: E721
            return None
        try:
            received_is_finite = math.isfinite(float(received))
        except (TypeError, ValueError, OverflowError):
            return None
        if (
            not received_is_finite
            or received < collector._PW2_SERVER_POSTUPDATE_EPOCH
        ):
            return None
        exact_values = {
            "warehouse_source_schema": _OLD_PW2_SCHEMA,
            "warehouse_source_generation": _OLD_PW2_GENERATION,
            "warehouse_map_table_build": _OLD_PW2_MAP_BUILD,
            "warehouse_map_table_sha256": _OLD_PW2_MAP_SHA256,
            "warehouse_expected_map_table_sha256": _OLD_PW2_MAP_SHA256,
            "warehouse_map_source_contract_sha256": (
                _OLD_PW2_SOURCE_CONTRACT_SHA256
            ),
            "warehouse_expected_map_source_contract_sha256": (
                _OLD_PW2_SOURCE_CONTRACT_SHA256
            ),
            "warehouse_map_name": "",
            "warehouse_map_name_source": "bid_map_table",
            "warehouse_table_status": "missing",
            "warehouse_auction_mode": "",
            "warehouse_owner_evidence_status": "absent",
            "warehouse_source_qualification": UNKNOWN_SOURCE,
            "warehouse_source_reason": "pw2_map_contract_untrusted",
        }
        if any(
            type(row.get(key)) is not str or row.get(key) != value  # noqa: E721
            for key, value in exact_values.items()
        ):
            return None
        if any(
            type(row.get(key)) is not bool or row.get(key) is not value  # noqa: E721
            for key, value in {
                "official_distribution_eligible": False,
                "warehouse_source_conflict": True,
                "warehouse_quarantine_required": True,
            }.items()
        ):
            return None
        meta = self.map_meta.get(map_id)
        map_name = meta.get("name") if type(meta) is dict else None
        if type(map_name) is not str or not map_name.strip():  # noqa: E721
            return None
        qualification, eligible, reason, conflict = (
            collector._pw2_expected_source_values(
                auction_mode=auction_mode,
                owner_status="absent",
                source_generation=collector._POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
            )
        )
        if conflict is not False:
            return None
        result = dict(row)
        result.update(
            {
                "warehouse_source_schema": collector._PW2_SCHEMA,
                "warehouse_source_qualification": qualification,
                "warehouse_map_name": map_name,
                "warehouse_map_name_source": "bid_map_table",
                "warehouse_table_status": "verified",
                "warehouse_source_reason": reason,
                "warehouse_source_generation": (
                    collector._POSTUPDATE_PLAYER_WAREHOUSE_GENERATION
                ),
                "warehouse_map_table_build": (
                    collector._PW2_TRUSTED_MAP_TABLE_BUILD
                ),
                "warehouse_map_table_sha256": (
                    collector._PW2_TRUSTED_MAP_TABLE_SHA256
                ),
                "warehouse_expected_map_table_sha256": (
                    collector._PW2_TRUSTED_MAP_TABLE_SHA256
                ),
                "warehouse_map_source_contract_sha256": (
                    collector._PW2_TRUSTED_SOURCE_CONTRACT_SHA256
                ),
                "warehouse_expected_map_source_contract_sha256": (
                    collector._PW2_TRUSTED_SOURCE_CONTRACT_SHA256
                ),
                "warehouse_auction_mode": auction_mode,
                "warehouse_owner_evidence_status": "absent",
                "warehouse_source_conflict": conflict,
                "warehouse_quarantine_required": conflict,
                "official_distribution_eligible": eligible,
                "server_recovery_reason": (
                    "update606_legacy_map_authority_reprojection"
                ),
                "server_recovery_source_generation": _OLD_PW2_GENERATION,
            }
        )
        if (
            collector._pw2_server_contract_valid(result) is not True
            or collector._quarantine_reason(result) is not None
        ):
            return None
        return result

    def _d66aee_false_stale_projection(
        self,
        row: dict[str, Any],
    ) -> dict[str, Any] | None:
        result = dict(row)
        if not self.collector._reproject_exact_d66aee_legacy_pw2(result):
            return None
        result.update(
            {
                "server_recovery_reason": (
                    "d66aee_legacy_full_map_contract_reprojection"
                ),
                "server_recovery_source_generation": (
                    self.collector._UPDATE606_PLAYER_WAREHOUSE_GENERATION
                ),
            }
        )
        if (
            self.collector._pw2_server_contract_valid(result) is not True
            or self.collector._quarantine_reason(result) is not None
        ):
            return None
        return result

    def prepare(
        self,
        row: dict[str, Any],
    ) -> tuple[str, dict[str, Any] | None]:
        reason = self.collector._quarantine_reason(row)
        if reason is None:
            return "current_collector_accept", dict(row)
        projected = self._d66aee_false_stale_projection(row)
        if projected is not None:
            return "d66aee_false_stale_reprojected", projected
        projected = self._legacy_map_projection(row)
        if projected is not None:
            return "update606_legacy_reprojected", projected
        return f"collector_{reason}", None


def _load_exact_module(name: str, path: Path) -> ModuleType:
    info = path.lstat()
    if not info or not path.is_file() or path.is_symlink():
        raise ValueError(f"unsafe runtime module: {path.name}")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"runtime module unavailable: {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def load_update606_policy(runtime_dir: Path) -> Update606RecoveryPolicy:
    if runtime_dir.is_symlink():
        raise ValueError("server runtime directory is unsafe")
    runtime_dir = runtime_dir.resolve()
    if not runtime_dir.is_dir():
        raise ValueError("server runtime directory is unsafe")
    runtime_text = str(runtime_dir)
    if runtime_text not in sys.path:
        sys.path.insert(0, runtime_text)
    collector = _load_exact_module(
        "bidking_recovery_update606_collector",
        runtime_dir / "sample_collector_server.py",
    )
    match_records = _load_exact_module(
        "bidking_recovery_update606_match_records",
        runtime_dir / "build_match_records.py",
    )
    if collector._UPDATE606_UNOPENED_MAP_IDS != UPDATE606_UNOPENED_MAP_IDS:
        raise ValueError("recovery and collector unopened-map authority differ")
    map_meta = match_records._load_map_meta()
    if type(map_meta) is not dict:
        raise ValueError("server map metadata is invalid")
    return Update606RecoveryPolicy(collector, map_meta)


def _ranked_map(value: object) -> bool:
    return (
        type(value) is int
        and value > 0
        and value // 100 in RANKED_MAP_PREFIXES
    )


def _source_scope(row: dict[str, Any]) -> str | None:
    """Return official/mixed for accepted source pairs, else None."""
    qualification = row.get("warehouse_source_qualification")
    eligible = row.get("official_distribution_eligible")
    if qualification == OFFICIAL_SOURCE and eligible is True:
        return "official"
    if _ranked_map(row.get("map_id")):
        if (
            qualification == UNKNOWN_SOURCE
            and eligible is False
        ) or (qualification is None and eligible is None):
            return "official"
        return None
    if (
        qualification in {UNKNOWN_SOURCE, PLAYER_SOURCE}
        and eligible is False
    ) or (qualification is None and eligible is None):
        return "mixed"
    return None


def _declared_shortfall(row: dict[str, Any]) -> bool:
    declared = row.get("total_item_count")
    captured = row.get("inventory_count")
    return (
        type(declared) is int
        and type(captured) is int
        and declared > 0
        and captured < declared
    )


def _valid_value(row: dict[str, Any]) -> bool:
    value = row.get("known_value_sum")
    return (
        not isinstance(value, bool)
        and type(value) in (int, float)
        and math.isfinite(float(value))
        and value > 0
    )


def _retained_reason(row: dict[str, Any]) -> str | None:
    stored = row.get("server_quarantine_reason")
    if stored in NON_SOURCE_REASONS:
        return str(stored)
    map_id = row.get("map_id")
    if type(map_id) is int and map_id in UPDATE606_UNOPENED_MAP_IDS:  # noqa: E721
        # Older collectors stored these rows under the generic source-pending
        # reason.  The update606 authority now identifies both maps as unopened,
        # so recovery must retain them even when the stored reason predates that
        # more precise classification.
        return "update606_map_unopened"
    if row.get("settlement_incomplete"):
        return "settlement_incomplete"
    if _declared_shortfall(row):
        return "declared_shortfall"
    if not _valid_value(row):
        return "invalid_value"
    counts = row.get("final_quality_counts")
    if not (
        (isinstance(counts, dict) and bool(counts))
        or (isinstance(counts, str) and bool(counts.strip()))
    ):
        return "missing_quality_counts"
    session_hash = row.get("session_hash")
    if not isinstance(session_hash, str) or not session_hash:
        return "missing_session_hash"
    if _source_scope(row) is None:
        return "source_conflict"
    return None


def _promoted(row: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    scope = _source_scope(row)
    if scope not in {"official", "mixed"}:
        raise ValueError("row source is not promotable")
    result = dict(row)
    result.pop("server_quarantine_reason", None)
    if (
        scope == "official"
        and _ranked_map(result.get("map_id"))
        and "warehouse_source_schema" not in result
    ):
        result["warehouse_source_qualification"] = OFFICIAL_SOURCE
        result["official_distribution_eligible"] = True
        result["warehouse_source_reason"] = (
            "server_ranked_map_excludes_player_custom"
        )
    elif (
        result.get("warehouse_source_qualification") is None
        and result.get("official_distribution_eligible") is None
    ):
        result["warehouse_source_qualification"] = UNKNOWN_SOURCE
        result["official_distribution_eligible"] = False
        result["warehouse_source_reason"] = (
            "server_mixed_source_without_owner_metadata"
        )
    return scope, result


def _json_line(row: dict[str, Any]) -> bytes:
    return (
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _iter_jsonl(path: Path, label: str) -> Iterator[tuple[bytes, dict[str, Any]]]:
    with path.open("rb") as stream:
        for line_number, raw in enumerate(stream, 1):
            if not raw.strip():
                yield raw, {}
                continue
            try:
                row = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(
                    f"{label} JSONL unreadable at line {line_number}"
                ) from exc
            if not isinstance(row, dict):
                raise ValueError(
                    f"{label} JSONL row is not an object at line {line_number}"
                )
            yield raw, row


def _load_main_hashes(path: Path) -> tuple[set[str], int]:
    hashes: set[str] = set()
    rows = 0
    for _, row in _iter_jsonl(path, "main"):
        if not row:
            continue
        rows += 1
        value = row.get("session_hash")
        if isinstance(value, str) and value:
            hashes.add(value)
    return hashes, rows


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_stat_and_owner(source: Path, destination: Path) -> None:
    """Preserve mode/times and, on POSIX, the source uid/gid before replace."""

    shutil.copystat(source, destination)
    chown = getattr(os, "chown", None)
    if chown is None:
        return
    source_stat = source.stat()
    destination_stat = destination.stat()
    if (
        destination_stat.st_uid != source_stat.st_uid
        or destination_stat.st_gid != source_stat.st_gid
    ):
        chown(
            destination,
            source_stat.st_uid,
            source_stat.st_gid,
            follow_symlinks=False,
        )


def _fsync_copy(source: Path, destination: Path) -> None:
    with source.open("rb") as src, destination.open("wb") as dst:
        shutil.copyfileobj(src, dst, length=1 << 20)
        dst.flush()
        os.fsync(dst.fileno())
    _copy_stat_and_owner(source, destination)


def _copy_stream(source: Path, destination: BinaryIO) -> None:
    with source.open("rb") as stream:
        shutil.copyfileobj(stream, destination, length=1 << 20)


def _append_boundary(source: Path, destination: BinaryIO) -> None:
    if source.stat().st_size <= 0:
        return
    with source.open("rb") as stream:
        stream.seek(-1, os.SEEK_END)
        if stream.read(1) not in {b"\n", b"\r"}:
            destination.write(b"\n")


def _process_quarantine(
    quarantine_path: Path,
    observed_hashes: set[str],
    *,
    main_output: BinaryIO | None,
    quarantine_output: BinaryIO | None,
    policy: Update606RecoveryPolicy | None = None,
) -> Counter[str]:
    counters: Counter[str] = Counter()
    for raw, row in _iter_jsonl(quarantine_path, "quarantine"):
        if not row:
            if quarantine_output is not None:
                quarantine_output.write(raw)
            continue
        counters["quarantine_rows_before"] += 1
        prepared = row
        if policy is not None:
            policy_reason, policy_row = policy.prepare(row)
            if policy_row is None:
                counters[f"retained_policy_{policy_reason}"] += 1
                if quarantine_output is not None:
                    quarantine_output.write(raw)
                continue
            prepared = policy_row
            counters[f"policy_{policy_reason}"] += 1
        reason = _retained_reason(prepared)
        if reason is not None:
            counters[f"retained_{reason}"] += 1
            if quarantine_output is not None:
                quarantine_output.write(raw)
            continue
        session_hash = prepared["session_hash"]
        if session_hash in observed_hashes:
            counters["duplicate_removed"] += 1
            continue
        observed_hashes.add(session_hash)
        scope, promoted = _promoted(prepared)
        counters[f"{scope}_promoted"] += 1
        if scope == "mixed":
            qualification = row.get("warehouse_source_qualification")
            if qualification == PLAYER_SOURCE:
                counters["mixed_player_promoted"] += 1
            elif qualification == UNKNOWN_SOURCE:
                counters["mixed_unknown_promoted"] += 1
            else:
                counters["mixed_missing_promoted"] += 1
        if main_output is not None:
            main_output.write(_json_line(promoted))
    counters["promoted_total"] = (
        counters["official_promoted"] + counters["mixed_promoted"]
    )
    counters["retained_total"] = sum(
        value
        for key, value in counters.items()
        if key.startswith("retained_") and key != "retained_total"
    )
    counters["quarantine_rows_after"] = counters["retained_total"]
    return counters


def _file_state(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return int(stat.st_mtime_ns), int(stat.st_size)


def recover(
    main_path: Path,
    quarantine_path: Path,
    *,
    apply: bool,
    backup_dir: Path | None,
    policy: Update606RecoveryPolicy | None = None,
) -> dict[str, Any]:
    main_path = main_path.resolve()
    quarantine_path = quarantine_path.resolve()
    if main_path == quarantine_path:
        raise ValueError("main and quarantine paths must differ")
    if not main_path.is_file() or not quarantine_path.is_file():
        raise FileNotFoundError("main and quarantine JSONL files must exist")
    if apply and backup_dir is None:
        raise ValueError("--backup-dir is required with --apply")

    main_state_before = _file_state(main_path)
    quarantine_state_before = _file_state(quarantine_path)
    main_sha_before = _sha256(main_path)
    quarantine_sha_before = _sha256(quarantine_path)
    main_hashes, main_rows = _load_main_hashes(main_path)

    if not apply:
        counters = _process_quarantine(
            quarantine_path,
            set(main_hashes),
            main_output=None,
            quarantine_output=None,
            policy=policy,
        )
        counters["main_rows_before"] = main_rows
        counters["main_rows_after"] = main_rows + counters["promoted_total"]
        return {
            "schema": 1,
            "mode": "dry-run",
            "counts": dict(sorted(counters.items())),
            "main_sha256_before": main_sha_before,
            "quarantine_sha256_before": quarantine_sha_before,
        }

    assert backup_dir is not None
    backup_dir = backup_dir.resolve()
    backup_dir.mkdir(parents=True, exist_ok=False)
    main_backup = backup_dir / main_path.name
    quarantine_backup = backup_dir / quarantine_path.name
    _fsync_copy(main_path, main_backup)
    _fsync_copy(quarantine_path, quarantine_backup)

    main_fd, main_tmp_name = tempfile.mkstemp(
        prefix=f".{main_path.name}.source-recovery-", dir=main_path.parent
    )
    quarantine_fd, quarantine_tmp_name = tempfile.mkstemp(
        prefix=f".{quarantine_path.name}.source-recovery-",
        dir=quarantine_path.parent,
    )
    os.close(main_fd)
    os.close(quarantine_fd)
    main_tmp = Path(main_tmp_name)
    quarantine_tmp = Path(quarantine_tmp_name)
    replaced_main = False
    try:
        with (
            main_tmp.open("wb") as main_output,
            quarantine_tmp.open("wb") as quarantine_output,
        ):
            _copy_stream(main_path, main_output)
            _append_boundary(main_path, main_output)
            counters = _process_quarantine(
                quarantine_path,
                set(main_hashes),
                main_output=main_output,
                quarantine_output=quarantine_output,
                policy=policy,
            )
            main_output.flush()
            os.fsync(main_output.fileno())
            quarantine_output.flush()
            os.fsync(quarantine_output.fileno())
        _copy_stat_and_owner(main_path, main_tmp)
        _copy_stat_and_owner(quarantine_path, quarantine_tmp)

        if _file_state(main_path) != main_state_before:
            raise RuntimeError("main file changed during recovery")
        if _file_state(quarantine_path) != quarantine_state_before:
            raise RuntimeError("quarantine file changed during recovery")

        counters["main_rows_before"] = main_rows
        counters["main_rows_after"] = main_rows + counters["promoted_total"]
        check_main_hashes, check_main_rows = _load_main_hashes(main_tmp)
        if check_main_rows != counters["main_rows_after"]:
            raise RuntimeError("main row count verification failed")
        if len(check_main_hashes) < len(main_hashes) + counters["promoted_total"]:
            raise RuntimeError("main session hash verification failed")
        _, check_quarantine_rows = _load_main_hashes(quarantine_tmp)
        if check_quarantine_rows != counters["quarantine_rows_after"]:
            raise RuntimeError("quarantine row count verification failed")

        os.replace(main_tmp, main_path)
        replaced_main = True
        os.replace(quarantine_tmp, quarantine_path)
    except Exception:
        main_tmp.unlink(missing_ok=True)
        quarantine_tmp.unlink(missing_ok=True)
        if replaced_main:
            _fsync_copy(main_backup, main_path)
        _fsync_copy(quarantine_backup, quarantine_path)
        raise

    report: dict[str, Any] = {
        "schema": 1,
        "mode": "apply",
        "counts": dict(sorted(counters.items())),
        "main_sha256_before": main_sha_before,
        "quarantine_sha256_before": quarantine_sha_before,
        "main_sha256_after": _sha256(main_path),
        "quarantine_sha256_after": _sha256(quarantine_path),
        "backup_main_sha256": _sha256(main_backup),
        "backup_quarantine_sha256": _sha256(quarantine_backup),
    }
    report_path = backup_dir / "mixed_source_quarantine_recovery_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--main", type=Path, required=True)
    parser.add_argument("--quarantine", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument(
        "--policy",
        choices=(
            "legacy-mixed-source",
            "update606-current",
            "d66aee-current",
        ),
        required=True,
    )
    parser.add_argument("--server-runtime-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.policy in {"update606-current", "d66aee-current"}:
            if args.server_runtime_dir is None:
                raise ValueError(
                    "--server-runtime-dir is required for current authority policy"
                )
            policy = load_update606_policy(args.server_runtime_dir)
        else:
            if args.server_runtime_dir is not None:
                raise ValueError(
                    "--server-runtime-dir is not valid for legacy policy"
                )
            policy = None
        report = recover(
            args.main,
            args.quarantine,
            apply=args.apply,
            backup_dir=args.backup_dir,
            policy=policy,
        )
    except Exception as exc:  # noqa: BLE001
        print(
            "MIXED_SOURCE_QUARANTINE_RECOVERY_FAILED "
            f"type={type(exc).__name__}",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
