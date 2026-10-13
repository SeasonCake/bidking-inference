#!/usr/bin/env python3
"""Verify one immutable current final package/workflow input root."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bidking_lab.extract.current_rankmap_package_contract import (  # noqa: E402
    CURRENT_PACKAGE_GENERATION,
    CURRENT_RANKMAP_AUTHORITY_MODE,
    CURRENT_RANKMAP_MAP_IDS,
    CURRENT_RANKMAP_WORKFLOW_INPUT_FINGERPRINT,
)


PACKAGE_BASELINE_ID = "package-data-2026-08-20-0.3.3-hotfix2-raven510x-d66aee"
PACKAGE_MANIFEST_SHA256 = (
    "d001bec1e19ccaf37b97f618e0aa4c914ceb3a4c742505e5a847b88ea86cd9f6"
)


class FinalInputCheckError(ValueError):
    """The immutable input root changed or is internally inconsistent."""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _fingerprint(files: Mapping[str, bytes]) -> str:
    payload = [
        {"path": path, "bytes": len(raw), "sha256": _sha256(raw)}
        for path, raw in sorted(files.items())
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256(encoded)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FinalInputCheckError(f"missing/unreadable JSON: {path.name}") from exc


def _read_regular_read_only(root: Path, relative: str) -> bytes:
    path = root / Path(relative)
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
        info = path.stat()
        raw = path.read_bytes()
    except (OSError, ValueError) as exc:
        raise FinalInputCheckError(f"missing/unreadable input: {relative}") from exc
    reparse = bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )
    if (
        not path.is_file()
        or path.is_symlink()
        or reparse
        or info.st_nlink != 1
        or info.st_mode & stat.S_IWRITE
    ):
        raise FinalInputCheckError(
            f"input is not a read-only regular single-link leaf: {relative}"
        )
    return raw


def _tracked_baseline() -> dict[str, dict[str, Any]]:
    value = _read_json(ROOT / "scripts/package_data_fingerprints.json")
    if (
        value.__class__ is not dict
        or value.get("baseline_id") != PACKAGE_BASELINE_ID
        or value.get("manifest") != "scripts/package_data_manifest.json"
        or value.get("manifest_sha256") != PACKAGE_MANIFEST_SHA256
        or _sha256((ROOT / "scripts/package_data_manifest.json").read_bytes())
        != PACKAGE_MANIFEST_SHA256
    ):
        raise FinalInputCheckError("tracked package baseline is not current final")
    files = value.get("files")
    if files.__class__ is not dict or len(files) != 19:
        raise FinalInputCheckError("tracked package baseline is not exact 19/19")
    return files


def verify(root: Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    manifest_raw = _read_regular_read_only(root, "input_manifest.json")
    try:
        manifest = json.loads(manifest_raw)
    except json.JSONDecodeError as exc:
        raise FinalInputCheckError("input manifest is malformed") from exc
    if (
        manifest.__class__ is not dict
        or manifest.get("schema")
        != "bidking_update606_final_input_root_manifest_v1"
        or manifest.get("root") != str(root)
        or manifest.get("package_generation") != CURRENT_PACKAGE_GENERATION
        or manifest.get("match10") != "deferred_excluded"
    ):
        raise FinalInputCheckError("input manifest root/generation/schema mismatch")
    entries = manifest.get("entries")
    if entries.__class__ is not list:
        raise FinalInputCheckError("input manifest entries are malformed")
    grouped: dict[str, dict[str, bytes]] = {}
    seen: set[str] = set()
    for entry in entries:
        if entry.__class__ is not dict:
            raise FinalInputCheckError("input manifest entry is malformed")
        relative = entry.get("relative_path")
        absolute = entry.get("absolute_path")
        group = entry.get("group")
        if (
            relative.__class__ is not str
            or absolute.__class__ is not str
            or group.__class__ is not str
            or relative in seen
        ):
            raise FinalInputCheckError("input manifest path/group is malformed")
        seen.add(relative)
        expected_path = (root / relative).resolve()
        if Path(absolute).resolve() != expected_path:
            raise FinalInputCheckError(f"absolute/relative path mismatch: {relative}")
        raw = _read_regular_read_only(root, relative)
        if (
            len(raw) != entry.get("bytes")
            or _sha256(raw) != entry.get("sha256")
            or entry.get("read_only") is not True
            or entry.get("reparse_point") is not False
            or entry.get("hardlink_count") != 1
        ):
            raise FinalInputCheckError(f"input identity mismatch: {relative}")
        grouped.setdefault(group, {})[relative] = raw

    package = grouped.get("package", {})
    workflow = grouped.get("workflow_release", {})
    selector = grouped.get("selector", {})
    if len(package) != 19 or len(workflow) != 14 or len(selector) != 2:
        raise FinalInputCheckError("package/workflow/selector count mismatch")
    baseline = _tracked_baseline()
    package_by_contract: dict[str, bytes] = {}
    for relative, raw in package.items():
        if not relative.startswith("final/"):
            raise FinalInputCheckError("package input is outside final directory")
        package_relative = relative.removeprefix("final/")
        expected = baseline.get(package_relative)
        if (
            expected.__class__ is not dict
            or len(raw) != expected.get("size")
            or _sha256(raw) != expected.get("sha256")
        ):
            raise FinalInputCheckError(
                f"tracked package baseline mismatch: {package_relative}"
            )
        package_by_contract[package_relative] = raw
    package_fingerprint = _fingerprint(package_by_contract)
    package_contract = manifest.get("package", {})
    workflow_contract = manifest.get("workflow", {})
    selector_contract = manifest.get("selector", {})
    if (
        package_contract.get("count") != 19
        or package_contract.get("fingerprint") != package_fingerprint
    ):
        raise FinalInputCheckError("package fingerprint mismatch")
    if (
        workflow_contract.get("count") != 14
        or workflow_contract.get("contract_fingerprint")
        != CURRENT_RANKMAP_WORKFLOW_INPUT_FINGERPRINT
        or workflow_contract.get("file_fingerprint") != _fingerprint(workflow)
    ):
        raise FinalInputCheckError("workflow fingerprint mismatch")
    if (
        selector_contract.get("count") != 2
        or selector_contract.get("file_fingerprint") != _fingerprint(selector)
    ):
        raise FinalInputCheckError("selector fingerprint mismatch")

    receipt_contract = manifest.get("generation_receipt", {})
    receipt_relative = receipt_contract.get("relative_path")
    if receipt_relative.__class__ is not str:
        raise FinalInputCheckError("generation receipt path is malformed")
    receipt_raw = _read_regular_read_only(root, receipt_relative)
    if (
        len(receipt_raw) != receipt_contract.get("bytes")
        or _sha256(receipt_raw) != receipt_contract.get("sha256")
    ):
        raise FinalInputCheckError("generation receipt identity mismatch")
    try:
        receipt = json.loads(receipt_raw)
    except json.JSONDecodeError as exc:
        raise FinalInputCheckError("generation receipt is malformed") from exc
    if (
        receipt.get("package_generation") != CURRENT_PACKAGE_GENERATION
        or receipt.get("match10") != "deferred_excluded"
        or receipt.get("package", {}).get("baseline_id") != PACKAGE_BASELINE_ID
        or receipt.get("package", {}).get("files") != 19
        or receipt.get("package", {}).get("fingerprint") != package_fingerprint
    ):
        raise FinalInputCheckError("generation receipt package contract mismatch")
    if receipt.get("raw_drop_closure", {}).get("counts") != {
        "empty_pool": 14,
        "missing_pool": 20,
        "unknown_item": 78,
        "valid": 31,
    }:
        raise FinalInputCheckError("raw Drop closure receipt mismatch")
    distribution = receipt.get("distribution_validation", {})
    independent = distribution.get("independent_map_authorities", {})
    if {
        name: independent.get(name, {}).get("maps")
        for name in ("weights", "priors", "presolve")
    } != {"weights": 101, "priors": 72, "presolve": 123}:
        raise FinalInputCheckError("independent derived map authorities mismatch")
    if (
        distribution.get("approved_droppable", {}).get("rows") != 680
        or distribution.get("approved_droppable", {}).get(
            "regenerated_from_raw_drop"
        )
        is not False
        or distribution.get("derived_weight_items", {}).get(
            "subset_of_approved_droppable"
        )
        is not True
        or distribution.get("hard_excluded_map_ids") != [5506, 6001]
    ):
        raise FinalInputCheckError("distribution authority boundary mismatch")
    rankmap = receipt.get("table_generation", {}).get(
        "current_rankmap_distribution", {}
    )
    if (
        rankmap.get("package_generation") != CURRENT_PACKAGE_GENERATION
        or rankmap.get("authority_mode") != CURRENT_RANKMAP_AUTHORITY_MODE
        or rankmap.get("drop_or_dropmap_textasset") is not False
        or rankmap.get("claims_item_level_drop_graph") is not False
        or rankmap.get("outputs", {}).get("weights", {}).get("maps")
        != len(CURRENT_RANKMAP_MAP_IDS)
        or rankmap.get("outputs", {}).get("priors", {}).get("maps")
        != len(CURRENT_RANKMAP_MAP_IDS)
        or rankmap.get("chenmei")
        != {"map_id": 5506, "status": "pending_isolated"}
        or rankmap.get("ranked_5606")
        != {"map_id": 5606, "status": "normal_ranked"}
    ):
        raise FinalInputCheckError("current RankMap authority receipt mismatch")
    return {
        "schema": "bidking_update606_final_input_preflight_receipt_v1",
        "package_generation": CURRENT_PACKAGE_GENERATION,
        "package": {"count": 19, "fingerprint": package_fingerprint},
        "workflow": {
            "count": workflow_contract["count"],
            "contract_fingerprint": CURRENT_RANKMAP_WORKFLOW_INPUT_FINGERPRINT,
            "file_fingerprint": workflow_contract["file_fingerprint"],
        },
        "selector": {
            "count": 2,
            "file_fingerprint": selector_contract["file_fingerprint"],
        },
        "authority": manifest.get("authority"),
        "setup_error_count": 0,
        "match10": "deferred_excluded",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        receipt = verify(args.root)
    except (FinalInputCheckError, OSError, ValueError) as exc:
        print(f"input-assembly-not-ready: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(receipt, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
