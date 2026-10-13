#!/usr/bin/env python3
"""Build the 68171c S2/current RankMap modeled-distribution sidecars.

The previous 4ae 18-map output supplies the already validated quality/value
priors.  Exact 68171c Item/BidMap/RankMap/Map bytes supply the current support and
value curves.  It follows the established post-2026-06-25 input contract and
keeps modeled/empirical authority separate from official item-level exactness;
the lack of a Drop/DropMap input is neither a 68171c gap nor a wait condition.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from bidking_lab.extract.current_rankmap_package_contract import (  # noqa: E402
    CURRENT_ALL_MAP_DISTRIBUTION_OUTPUTS,
    CURRENT_PACKAGE_GENERATION,
    CURRENT_RED_LONGTAIL_NORMALIZED,
    CURRENT_RANKMAP_AUTHORITY_OUTPUT,
    CURRENT_RANKMAP_DISTRIBUTION_OUTPUTS,
    CURRENT_STEAM_BUILD_ID,
    PREVIOUS_RED_LONGTAIL_NORMALIZED,
    build_current_all_map_distribution,
    build_current_red_longtail_normalized,
    build_current_rankmap_distribution,
)


class CurrentRankMapBuildError(ValueError):
    """An exact input or deterministic output identity did not match."""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _stable_read(path: Path) -> bytes:
    if not path.is_file() or path.is_symlink():
        raise CurrentRankMapBuildError(f"missing/non-regular input: {path}")
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or len(raw) != after.st_size
    ):
        raise CurrentRankMapBuildError(f"input changed while reading: {path}")
    return raw


def _authority_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def build_outputs(
    *,
    text_assets_dir: Path,
    previous_weights: bytes,
    previous_priors: bytes,
    previous_all_map_weights: bytes,
    previous_all_map_priors: bytes,
    previous_red_longtail: bytes,
    items_droppable: bytes,
) -> tuple[dict[str, bytes], dict[str, Any]]:
    common = {
        "rankmap_text_asset": _stable_read(text_assets_dir / "RankMap.txt"),
        "bidmap_text_asset": _stable_read(text_assets_dir / "BidMap.txt"),
        "item_text_asset": _stable_read(text_assets_dir / "Item.txt"),
        "map_text_asset": _stable_read(text_assets_dir / "Map.txt"),
        "items_droppable": items_droppable,
    }
    all_map_weights, all_map_priors, all_map_authority = (
        build_current_all_map_distribution(
            **common,
            previous_weights=previous_all_map_weights,
            previous_priors=previous_all_map_priors,
        )
    )
    weights, priors, authority = build_current_rankmap_distribution(
        **common,
        calibrated_weights=previous_weights,
        calibrated_priors=previous_priors,
        current_all_map_weights=all_map_weights,
        current_all_map_priors=all_map_priors,
    )
    authority_raw = _authority_bytes(authority)
    if (
        len(previous_red_longtail) != PREVIOUS_RED_LONGTAIL_NORMALIZED["bytes"]
        or _sha256(previous_red_longtail)
        != PREVIOUS_RED_LONGTAIL_NORMALIZED["sha256"]
    ):
        raise CurrentRankMapBuildError("previous red longtail identity mismatch")
    red_longtail_raw, red_longtail_receipt = build_current_red_longtail_normalized(
        item_text_asset=common["item_text_asset"],
        items_droppable=items_droppable,
    )
    outputs = {
        "data/processed/drop_weights_all_maps_0627.json": all_map_weights,
        "data/processed/drop_table_priors_0627.json": all_map_priors,
        "data/processed/rankmap_drop_weights_current.json": weights,
        "data/processed/rankmap_drop_priors_current.json": priors,
        "data/processed/rankmap_distribution_authority_current.json": authority_raw,
        "data/processed/red_longtail_normalized_0627.json": red_longtail_raw,
    }
    expected = {
        "data/processed/drop_weights_all_maps_0627.json": (
            CURRENT_ALL_MAP_DISTRIBUTION_OUTPUTS["weights"]
        ),
        "data/processed/drop_table_priors_0627.json": (
            CURRENT_ALL_MAP_DISTRIBUTION_OUTPUTS["priors"]
        ),
        "data/processed/rankmap_drop_weights_current.json": (
            CURRENT_RANKMAP_DISTRIBUTION_OUTPUTS["weights"]
        ),
        "data/processed/rankmap_drop_priors_current.json": (
            CURRENT_RANKMAP_DISTRIBUTION_OUTPUTS["priors"]
        ),
        "data/processed/rankmap_distribution_authority_current.json": (
            CURRENT_RANKMAP_AUTHORITY_OUTPUT
        ),
        "data/processed/red_longtail_normalized_0627.json": (
            CURRENT_RED_LONGTAIL_NORMALIZED
        ),
    }
    for relative, raw in outputs.items():
        identity = expected[relative]
        if len(raw) != identity["bytes"] or _sha256(raw) != identity["sha256"]:
            raise CurrentRankMapBuildError(f"output identity mismatch: {relative}")
    receipt = {
        "schema": "bidking_official_20260903_rankmap_distribution_build_v1",
        "steam_build_id": CURRENT_STEAM_BUILD_ID,
        "package_generation": CURRENT_PACKAGE_GENERATION,
        "authority": {
            "mode": authority["authority_mode"],
            "model_distribution_authority": True,
            "official_distribution_authority": False,
            "empirical_calibration": authority["empirical_calibration"],
        },
        "all_map_authority": all_map_authority,
        "red_longtail": red_longtail_receipt,
        "inputs": {
            "previous_weights": {
                "bytes": len(previous_weights),
                "sha256": _sha256(previous_weights),
            },
            "previous_priors": {
                "bytes": len(previous_priors),
                "sha256": _sha256(previous_priors),
            },
            "previous_red_longtail": {
                "bytes": len(previous_red_longtail),
                "sha256": _sha256(previous_red_longtail),
            },
            "previous_all_map_weights": {
                "bytes": len(previous_all_map_weights),
                "sha256": _sha256(previous_all_map_weights),
            },
            "previous_all_map_priors": {
                "bytes": len(previous_all_map_priors),
                "sha256": _sha256(previous_all_map_priors),
            },
            "items_droppable": {
                "bytes": len(items_droppable),
                "sha256": _sha256(items_droppable),
            },
        },
        "outputs": {
            relative: {"bytes": len(raw), "sha256": _sha256(raw)}
            for relative, raw in sorted(outputs.items())
        },
        "scopes": authority["scopes"],
        "not_run": [
            "build",
            "package",
            "E07",
            "native",
            "push",
            "upload",
            "deploy",
            "production",
        ],
    }
    return outputs, receipt


def _write_atomic(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise CurrentRankMapBuildError(f"output is a symlink: {path}")
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_bytes(raw)
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text-assets-dir", type=Path, required=True)
    parser.add_argument("--previous-weights", type=Path, required=True)
    parser.add_argument("--previous-priors", type=Path, required=True)
    parser.add_argument("--previous-all-map-weights", type=Path, required=True)
    parser.add_argument("--previous-all-map-priors", type=Path, required=True)
    parser.add_argument("--previous-red-longtail", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--receipt-out", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    droppable_path = args.output_root / "data/processed/items_droppable.json"
    try:
        previous_weights = _stable_read(args.previous_weights)
        previous_priors = _stable_read(args.previous_priors)
        previous_all_map_weights = _stable_read(args.previous_all_map_weights)
        previous_all_map_priors = _stable_read(args.previous_all_map_priors)
        previous_red_longtail = _stable_read(args.previous_red_longtail)
        items_droppable = _stable_read(droppable_path)
        outputs, receipt = build_outputs(
            text_assets_dir=args.text_assets_dir,
            previous_weights=previous_weights,
            previous_priors=previous_priors,
            previous_all_map_weights=previous_all_map_weights,
            previous_all_map_priors=previous_all_map_priors,
            previous_red_longtail=previous_red_longtail,
            items_droppable=items_droppable,
        )
        receipt_raw = _authority_bytes(receipt)
        if args.check:
            for relative, raw in outputs.items():
                if _stable_read(args.output_root / relative) != raw:
                    raise CurrentRankMapBuildError(
                        f"checked output differs: {relative}"
                    )
            if _stable_read(args.receipt_out) != receipt_raw:
                raise CurrentRankMapBuildError("checked receipt differs")
            action = "checked"
        else:
            for relative, raw in outputs.items():
                _write_atomic(args.output_root / relative, raw)
            _write_atomic(args.receipt_out, receipt_raw)
            action = "wrote"
    except (OSError, ValueError, CurrentRankMapBuildError) as exc:
        print(f"input-assembly-not-ready: {exc}", file=sys.stderr)
        return 2
    print(f"{action} 68171c current RankMap distribution")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
