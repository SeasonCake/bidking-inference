#!/usr/bin/env python3
"""Prepare update606 legacy-only distribution outputs with one exact scope."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from bidking_lab.extract.update606_package_input_contract import (  # noqa: E402
    LEGACY_DISTRIBUTION_POLICY,
    LEGACY_PACKAGE_DROP,
    Update606PackageInputError,
    carry_forward_legacy_map_payload,
    derive_legacy_distribution_scope,
)


_OUTPUT_NAMES = {
    "drop_weights_all_maps_0627": "drop_weights_all_maps_0627.json",
    "drop_table_priors_0627": "drop_table_priors_0627.json",
    "quality_combo_presolve_q456": "quality_combo_presolve_q456.json",
}
_RED_LONGTAIL = {
    "bytes": 992,
    "sha256": "c5cba6367d1a314533aead17488b12a319f24f21c3f8bf016d275472af21d5b0",
}


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _exact(path: Path, *, label: str, size: int, sha256: str) -> bytes:
    try:
        value = path.read_bytes()
    except OSError as exc:
        raise Update606PackageInputError(f"missing/unreadable {label}") from exc
    if len(value) != size or _sha256(value) != sha256:
        raise Update606PackageInputError(f"{label} identity mismatch")
    return value


def prepare(
    *,
    legacy_bid_map: Path,
    update606_bid_map: Path,
    legacy_drop: Path,
    legacy_drop_weights: Path,
    legacy_drop_priors: Path,
    legacy_quality_presolve: Path,
    legacy_red_longtail: Path,
) -> tuple[dict[str, bytes], dict[str, object]]:
    try:
        legacy_bid_map_raw = legacy_bid_map.read_bytes()
        update_bid_map_raw = update606_bid_map.read_bytes()
        legacy_drop_raw = legacy_drop.read_bytes()
    except OSError as exc:
        raise Update606PackageInputError("required table input is missing/unreadable") from exc
    scope = derive_legacy_distribution_scope(
        legacy_bid_map_package=legacy_bid_map_raw,
        update606_bid_map_package=update_bid_map_raw,
        legacy_drop_package=legacy_drop_raw,
    )
    sources = {
        "drop_weights_all_maps_0627": legacy_drop_weights,
        "drop_table_priors_0627": legacy_drop_priors,
        "quality_combo_presolve_q456": legacy_quality_presolve,
    }
    outputs: dict[str, bytes] = {}
    receipts: dict[str, object] = {}
    for name, path in sources.items():
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise Update606PackageInputError(f"missing/unreadable {name}") from exc
        rendered, receipt = carry_forward_legacy_map_payload(name, raw, scope=scope)
        outputs[_OUTPUT_NAMES[name]] = rendered
        receipts[name] = receipt
    red = _exact(
        legacy_red_longtail,
        label="red_longtail_normalized_0627",
        size=_RED_LONGTAIL["bytes"],
        sha256=_RED_LONGTAIL["sha256"],
    )
    outputs["red_longtail_normalized_0627.json"] = red
    receipts["red_longtail_normalized_0627"] = {
        "policy": "item_identity_unchanged_exact_carry_forward",
        "bytes": len(red),
        "sha256": _sha256(red),
    }
    receipt: dict[str, object] = {
        "schema": "bidking_update606_legacy_distribution_build_receipt_v1",
        "policy": LEGACY_DISTRIBUTION_POLICY,
        "scope": scope.as_receipt(),
        "legacy_drop": {
            "bytes": len(legacy_drop_raw),
            "sha256": _sha256(legacy_drop_raw),
            "allowed_map_ids_sha256": scope.as_receipt()["allowed_map_ids_sha256"],
        },
        "outputs": receipts,
    }
    if receipt["legacy_drop"] != {
        "bytes": LEGACY_PACKAGE_DROP["bytes"],
        "sha256": LEGACY_PACKAGE_DROP["sha256"],
        "allowed_map_ids_sha256": scope.as_receipt()["allowed_map_ids_sha256"],
    }:
        raise Update606PackageInputError("legacy Drop receipt identity mismatch")
    return outputs, receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-bid-map", type=Path, required=True)
    parser.add_argument("--update606-bid-map", type=Path, required=True)
    parser.add_argument("--legacy-drop", type=Path, required=True)
    parser.add_argument("--legacy-drop-weights", type=Path, required=True)
    parser.add_argument("--legacy-drop-priors", type=Path, required=True)
    parser.add_argument("--legacy-quality-presolve", type=Path, required=True)
    parser.add_argument("--legacy-red-longtail", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--receipt-out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        outputs, receipt = prepare(
            legacy_bid_map=args.legacy_bid_map,
            update606_bid_map=args.update606_bid_map,
            legacy_drop=args.legacy_drop,
            legacy_drop_weights=args.legacy_drop_weights,
            legacy_drop_priors=args.legacy_drop_priors,
            legacy_quality_presolve=args.legacy_quality_presolve,
            legacy_red_longtail=args.legacy_red_longtail,
        )
    except Update606PackageInputError as exc:
        print(f"input-assembly-not-ready: {exc}", file=sys.stderr)
        return 2
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.receipt_out.parent.mkdir(parents=True, exist_ok=True)
    for name, payload in outputs.items():
        (args.output_dir / name).write_bytes(payload)
    args.receipt_out.write_text(
        json.dumps(receipt, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        f"wrote update606 legacy distribution outputs={len(outputs)} "
        f"policy={LEGACY_DISTRIBUTION_POLICY}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
