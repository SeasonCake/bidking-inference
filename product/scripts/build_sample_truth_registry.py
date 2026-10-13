"""Build the content-hashed truth registry consumed by ``clean_eval_files``.

This is deliberately separate from ``refresh_sample_pool.py``.  It never renames, moves, deletes,
or quarantines captures; it inventories every active replay sample, isolated settlement truth-only
sample and quarantine positive control, runs the strict ``sample_truth_gate``, and optionally writes
one registry JSON.  Registered INVALID captures stay available as positive controls but cannot enter
the clean tuning set.

Usage:
    python scripts/build_sample_truth_registry.py --write --full
    python scripts/build_sample_truth_registry.py          # check/preview, reuse unchanged rows
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _sub in ("scripts", "src", "hero_ref/src", "hero_ref/tools"):
    _path = str(ROOT / _sub)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from bidking_lab.live.fatbeans import parse_fatbeans_capture  # noqa: E402
from bidking_lab.live.monitor import load_monitor_tables  # noqa: E402
from build_clean_eval_set import (  # noqa: E402
    TRUTH_REGISTRY,
    TRUTH_REGISTRY_SCHEMA_VERSION,
    _capture_date_from_path,
    _load_session_dates,
    _sha256_file,
    _truth_gate_code_sha256,
    _truth_gate_context_sha256,
)
from sample_truth_gate import validate_events  # noqa: E402


FATBEANS = ROOT / "data" / "samples" / "fatbeans"
ACTIVITY_POOL = ROOT / "data" / "samples" / "fatbeans_activity_20260702_shipwreck"
RED_CRATE_ACTIVITY_POOL = (
    ROOT / "data" / "samples" / "fatbeans_activity_20260716_red_crate_shipwreck"
)
TRUTH_ONLY_ROOT = ROOT / "data" / "samples" / "fatbeans_truth_only_settlement"
TRUTH_ONLY_MAIN_POOL = TRUTH_ONLY_ROOT / "normal"
TRUTH_ONLY_ACTIVITY_POOL = TRUTH_ONLY_ROOT / "activity_20260702_shipwreck"
TRUTH_ONLY_RED_CRATE_ACTIVITY_POOL = TRUTH_ONLY_ROOT / "activity_20260716_red_crate_shipwreck"
_HERO_RE = re.compile(r"^fatbeans_(?:valid|truth|mixed|INVALID)_([a-z]+)_")
_MAP_RE = re.compile(r"^fatbeans_(?:valid|truth|mixed|INVALID)_[a-z]+_(\d{3,4})_")
_SID_RE = re.compile(r"_(\d{16})_")


def _path_metadata(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    if resolved.parent == FATBEANS.resolve():
        pool = "main"
        active = True
        truth_eligible = True
    elif resolved.parent == ACTIVITY_POOL.resolve():
        pool = "activity_shipwreck"
        active = True
        truth_eligible = True
    elif resolved.parent == RED_CRATE_ACTIVITY_POOL.resolve():
        pool = "activity_red_crate_shipwreck"
        active = True
        truth_eligible = True
    elif resolved.parent == TRUTH_ONLY_MAIN_POOL.resolve():
        pool = "truth_only_settlement"
        active = False
        truth_eligible = True
    elif resolved.parent == TRUTH_ONLY_ACTIVITY_POOL.resolve():
        pool = "truth_only_activity_shipwreck"
        active = False
        truth_eligible = True
    elif resolved.parent == TRUTH_ONLY_RED_CRATE_ACTIVITY_POOL.resolve():
        pool = "truth_only_activity_red_crate_shipwreck"
        active = False
        truth_eligible = True
    else:
        pool = "quarantine"
        active = False
        truth_eligible = False
    return {
        "path": str(resolved.relative_to(ROOT)).replace("\\", "/"),
        "pool": pool,
        "active": active,
        "replay_eligible": active,
        "truth_eligible": truth_eligible,
    }


def _registry_paths() -> list[Path]:
    """All replay, settlement truth-only and retained quarantine control captures."""
    paths = {
        path.resolve()
        for pattern in ("fatbeans_valid_*.json", "fatbeans_mixed_*.json")
        for path in FATBEANS.glob(pattern)
    }
    paths.update(path.resolve() for path in ACTIVITY_POOL.glob("fatbeans_valid_*.json"))
    paths.update(
        path.resolve() for path in RED_CRATE_ACTIVITY_POOL.glob("fatbeans_valid_*.json")
    )
    for directory in (
        TRUTH_ONLY_MAIN_POOL,
        TRUTH_ONLY_ACTIVITY_POOL,
        TRUTH_ONLY_RED_CRATE_ACTIVITY_POOL,
    ):
        paths.update(path.resolve() for path in directory.glob("fatbeans_truth_*.json"))
    for directory in FATBEANS.iterdir():
        if not directory.is_dir():
            continue
        if not (
            directory.name.startswith("ZZZ_INVALID_DO_NOT_USE")
            or directory.name.startswith("_quarantine")
        ):
            continue
        paths.update(path.resolve() for path in directory.glob("fatbeans_*.json"))
    return sorted(paths, key=lambda path: (_path_metadata(path)["active"], path.name), reverse=True)


def _existing_cache(
    *, full: bool, gate_sha256: str, gate_context_sha256: str
) -> dict[str, dict[str, Any]]:
    if full or not TRUTH_REGISTRY.is_file():
        return {}
    try:
        payload = json.loads(TRUTH_REGISTRY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if (
        payload.get("schema_version") != TRUTH_REGISTRY_SCHEMA_VERSION
        or payload.get("gate_code_sha256") != gate_sha256
        or payload.get("gate_context_sha256") != gate_context_sha256
    ):
        return {}
    rows = payload.get("samples")
    if not isinstance(rows, list):
        return {}
    return {
        str(row["file"]): row
        for row in rows
        if isinstance(row, dict) and row.get("file") and row.get("sha256")
    }


def _row_for(path: Path, *, sha256: str, tables: Any, dates: dict[str, str]) -> dict[str, Any]:
    hero_match = _HERO_RE.match(path.name)
    map_match = _MAP_RE.match(path.name)
    sid_match = _SID_RE.search(path.name)
    session_id = sid_match.group(1) if sid_match else None
    indexed_date = dates.get(session_id) if session_id else None
    packet_date = _capture_date_from_path(path)
    row: dict[str, Any] = {
        "file": path.name,
        **_path_metadata(path),
        "sha256": sha256,
        "bytes": path.stat().st_size,
        "hero": hero_match.group(1) if hero_match else None,
        "map_id": int(map_match.group(1)) if map_match else None,
        "session_id": session_id,
        "capture_date": packet_date or indexed_date,
        "capture_date_source": (
            "packet_capture_time"
            if packet_date
            else "session_dates_manifest" if indexed_date else None
        ),
    }
    try:
        result = validate_events(parse_fatbeans_capture(path), tables=tables)
    except Exception as exc:  # external capture boundary: registry must fail closed
        result = {
            "verdict": "INVALID",
            "reason": f"parse_or_gate_failed:{type(exc).__name__}",
            "reasons": [f"parse_or_gate_failed:{type(exc).__name__}"],
            "claims_present": 0,
            "claims_matched": 0,
            "truth": {},
        }
    truth = result.get("truth") if isinstance(result.get("truth"), dict) else {}
    row.update(
        verdict=result.get("verdict") if result.get("verdict") == "valid" else "INVALID",
        reason=result.get("reason"),
        reasons=list(result.get("reasons") or ()),
        claims_present=int(result.get("claims_present") or 0),
        claims_matched=int(result.get("claims_matched") or 0),
        truth={
            "collectible_count": int(truth.get("collectible_count") or 0),
            "total_cells": int(truth.get("total_cells") or 0),
            "total_value": int(truth.get("total_value") or 0),
        },
    )
    if row["truth_eligible"] and not row["active"]:
        row["truth_only_basis"] = "settlement_valid_but_replay_workflow_incomplete"
    elif not row["active"]:
        row["quarantine_basis"] = (
            "truth_positive_control"
            if row["verdict"] != "valid"
            else "legacy_structural_or_provenance"
        )
    return row


def build_registry(*, full: bool = False) -> dict[str, Any]:
    gate_sha256 = _truth_gate_code_sha256()
    gate_context_sha256 = _truth_gate_context_sha256()
    cache = _existing_cache(
        full=full,
        gate_sha256=gate_sha256,
        gate_context_sha256=gate_context_sha256,
    )
    candidates = _registry_paths()
    tables = load_monitor_tables()
    dates = _load_session_dates()
    records: list[dict[str, Any]] = []
    cache_hits = 0
    started = time.perf_counter()
    for index, path in enumerate(candidates, 1):
        sha256 = _sha256_file(path)
        cached = cache.get(path.name)
        metadata = _path_metadata(path)
        if (
            cached is not None
            and cached.get("sha256") == sha256
            and cached.get("path") == metadata["path"]
            and cached.get("pool") == metadata["pool"]
            and cached.get("active") is metadata["active"]
            and cached.get("replay_eligible") is metadata["replay_eligible"]
            and cached.get("truth_eligible") is metadata["truth_eligible"]
        ):
            records.append(cached)
            cache_hits += 1
        else:
            records.append(_row_for(path, sha256=sha256, tables=tables, dates=dates))
        if index % 100 == 0 or index == len(candidates):
            elapsed = time.perf_counter() - started
            eta = elapsed / index * (len(candidates) - index) if index else 0.0
            print(
                f"  {index}/{len(candidates)} elapsed={elapsed:.1f}s eta={eta:.1f}s",
                flush=True,
            )
    verdicts = Counter(row["verdict"] for row in records)
    active_records = [row for row in records if row["replay_eligible"]]
    truth_only_records = [
        row for row in records if row["truth_eligible"] and not row["replay_eligible"]
    ]
    quarantine_records = [row for row in records if not row["truth_eligible"]]
    active_verdicts = Counter(row["verdict"] for row in active_records)
    truth_only_verdicts = Counter(row["verdict"] for row in truth_only_records)
    quarantine_verdicts = Counter(row["verdict"] for row in quarantine_records)
    reasons = Counter(
        str(row.get("reason") or "")
        for row in records
        if row["verdict"] != "valid"
    )
    current_context_sha256 = _truth_gate_context_sha256()
    if current_context_sha256 != gate_context_sha256:
        raise RuntimeError(
            "truth-gate parser/engine/table context changed while the registry was being built; "
            "discard this mixed-context result and rerun"
        )
    return {
        "schema_version": TRUTH_REGISTRY_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generator": "scripts/build_sample_truth_registry.py",
        "gate": "scripts/sample_truth_gate.py::validate_events",
        "gate_code_sha256": gate_sha256,
        "gate_context_sha256": gate_context_sha256,
        "scope": (
            "all replay-active main/activity raw captures, isolated settlement truth-only raw, "
            "and retained quarantine positive controls"
        ),
        "summary": {
            "files": len(records),
            "active_files": len(active_records),
            "active_valid": active_verdicts.get("valid", 0),
            "active_INVALID": active_verdicts.get("INVALID", 0),
            "truth_only_files": len(truth_only_records),
            "truth_only_valid": truth_only_verdicts.get("valid", 0),
            "truth_only_INVALID": truth_only_verdicts.get("INVALID", 0),
            "quarantine_files": len(quarantine_records),
            "quarantine_valid": quarantine_verdicts.get("valid", 0),
            "quarantine_INVALID": quarantine_verdicts.get("INVALID", 0),
            "quarantine_structural_or_provenance": quarantine_verdicts.get("valid", 0),
            "quarantine_truth_positive": quarantine_verdicts.get("INVALID", 0),
            "valid": verdicts.get("valid", 0),
            "INVALID": verdicts.get("INVALID", 0),
            "cache_hits": cache_hits,
            "invalid_reasons": dict(sorted(reasons.items())),
        },
        "samples": records,
    }


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    ) as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
        temporary = Path(fh.name)
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="atomically write the registry")
    parser.add_argument("--full", action="store_true", help="ignore cached unchanged rows")
    args = parser.parse_args(argv)

    payload = build_registry(full=args.full)
    summary = payload["summary"]
    print(
        "registry summary: "
        f"files={summary['files']} active={summary['active_files']} "
        f"active_valid={summary['active_valid']} active_INVALID={summary['active_INVALID']} "
        f"truth_only={summary['truth_only_files']} "
        f"truth_only_valid={summary['truth_only_valid']} "
        f"quarantine={summary['quarantine_files']} "
        f"quarantine_valid={summary['quarantine_valid']} cache_hits={summary['cache_hits']}"
    )
    if args.write:
        _atomic_write(TRUTH_REGISTRY, payload)
        print(f"wrote {TRUTH_REGISTRY.relative_to(ROOT)}")
        return 0
    if not TRUTH_REGISTRY.is_file():
        print("registry is missing; rerun with --write")
        return 1
    current = json.loads(TRUTH_REGISTRY.read_text(encoding="utf-8"))
    same_rows = current.get("samples") == payload.get("samples")
    same_gate = current.get("gate_code_sha256") == payload.get("gate_code_sha256")
    same_context = current.get("gate_context_sha256") == payload.get("gate_context_sha256")
    print(f"registry current: rows={same_rows} gate={same_gate} context={same_context}")
    return 0 if same_rows and same_gate and same_context else 1


if __name__ == "__main__":
    raise SystemExit(main())
