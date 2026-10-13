"""Move registry-confirmed INVALID active captures into a non-recursive quarantine.

The operation is reversible and fail-closed: the current registry schema/context and every source
SHA-256 must match before the first move.  No file is deleted or overwritten.  A planned manifest is
written before moving so an interrupted run retains the complete original/destination mapping.

Usage:
    python scripts/quarantine_invalid_sample_pool.py          # dry-run
    python scripts/quarantine_invalid_sample_pool.py --apply  # move after preflight
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _sub in ("scripts", "src", "hero_ref/src", "hero_ref/tools"):
    _path = str(ROOT / _sub)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from build_clean_eval_set import (  # noqa: E402
    TRUTH_REGISTRY,
    _load_truth_registry,
    _sha256_file,
)
from build_sample_truth_registry import (  # noqa: E402
    ACTIVITY_POOL,
    FATBEANS,
    RED_CRATE_ACTIVITY_POOL,
)


QUARANTINE = FATBEANS / "ZZZ_INVALID_DO_NOT_USE_truth_gate_20260715"
MANIFEST = ROOT / "data" / "sample_manifests" / "sample_quarantine_20260715.json"


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _assert_plain_directory(path: Path) -> None:
    attributes = int(getattr(path.lstat(), "st_file_attributes", 0))
    if path.is_symlink() or attributes & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
        raise RuntimeError(f"refusing to move through a symlink/junction/reparse point: {path}")


def _plan() -> list[dict[str, Any]]:
    # Validates schema, gate/parser/table context, duplicate names and row structure.
    by_name = _load_truth_registry()
    payload = json.loads(TRUTH_REGISTRY.read_text(encoding="utf-8"))
    rows = [row for row in payload["samples"] if row.get("active") is True]
    invalid = [row for row in rows if row.get("verdict") != "valid"]
    plan: list[dict[str, Any]] = []
    allowed_parents = {
        FATBEANS.resolve(),
        ACTIVITY_POOL.resolve(),
        RED_CRATE_ACTIVITY_POOL.resolve(),
    }
    for row in invalid:
        source = (ROOT / str(row["path"])).resolve()
        if source.parent not in allowed_parents:
            raise RuntimeError(f"registry active row is outside an active pool: {source}")
        if source.name not in by_name:
            raise RuntimeError(f"registry lookup lost active row: {source.name}")
        if not source.is_file():
            raise FileNotFoundError(f"active INVALID source is missing: {source}")
        if _sha256_file(source) != row.get("sha256"):
            raise RuntimeError(f"active INVALID content drifted after registry build: {source.name}")
        destination = QUARANTINE / source.name
        if destination.exists():
            raise FileExistsError(f"refusing to overwrite quarantine destination: {destination}")
        plan.append(
            {
                "file": source.name,
                "hero": row.get("hero"),
                "session_id": row.get("session_id"),
                "sha256": row.get("sha256"),
                "reason": row.get("reason"),
                "reasons": row.get("reasons") or [],
                "source": str(source.relative_to(ROOT)).replace("\\", "/"),
                "destination": str(destination.relative_to(ROOT)).replace("\\", "/"),
            }
        )
    return sorted(plan, key=lambda row: row["file"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="perform the verified moves")
    args = parser.parse_args(argv)

    _assert_plain_directory(FATBEANS)
    _assert_plain_directory(ACTIVITY_POOL)
    _assert_plain_directory(RED_CRATE_ACTIVITY_POOL)
    plan = _plan()
    reasons = Counter(str(row.get("reason") or "") for row in plan)
    print(f"registry-confirmed active INVALID files: {len(plan)}")
    print(f"reasons: {dict(sorted(reasons.items()))}")
    print(f"destination: {QUARANTINE.relative_to(ROOT)}")
    if not args.apply:
        print("dry-run only; re-run with --apply after reviewing the count")
        return 0
    if not plan:
        print("nothing to quarantine")
        return 0
    if MANIFEST.exists():
        raise FileExistsError(f"refusing to overwrite existing quarantine manifest: {MANIFEST}")

    QUARANTINE.mkdir(parents=True, exist_ok=False)
    _assert_plain_directory(QUARANTINE)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "planned",
        "registry": str(TRUTH_REGISTRY.relative_to(ROOT)).replace("\\", "/"),
        "registry_sha256": _sha256_file(TRUTH_REGISTRY),
        "rules": {
            "selector": "registry active=true and verdict!=valid",
            "source_content": "must match registry sha256",
            "source_mutation": "rename only; no delete and no overwrite",
        },
        "summary": {"planned": len(plan), "moved": 0, "reasons": dict(sorted(reasons.items()))},
        "moves": plan,
    }
    _atomic_write(MANIFEST, payload)

    moved = 0
    for row in plan:
        source = ROOT / row["source"]
        destination = ROOT / row["destination"]
        source.rename(destination)
        moved += 1
    payload["status"] = "complete"
    payload["summary"]["moved"] = moved
    payload["completed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _atomic_write(MANIFEST, payload)
    print(f"moved={moved}; manifest={MANIFEST.relative_to(ROOT)}")
    print("rebuild sample_pool_registry.json before running any selector")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
