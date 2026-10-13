#!/usr/bin/env python3
"""Validate previously extracted official update606 TextAssets.

The extractor is intentionally out of scope: this checker never runs the game,
downloads a bundle, or guesses a missing table.  Point it at a directory that
contains exact ``Activity.txt``, ``ActivityMap.txt``, ``BidMap.txt``,
``Item.txt`` and ``Map.txt`` TextAsset bytes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bidking_lab.extract.update606_release_contract import (
    UPDATE606_MANIFEST_SHA256,
    UPDATE606_PACKAGE_GENERATION,
    UPDATE606_SCRIPTS_SHA256,
    UPDATE606_TABLES_BUNDLE_SHA256,
    load_update606_text_asset_directory,
    validate_update606_release_contract,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text-assets-dir", type=Path, required=True)
    parser.add_argument(
        "--package-generation", default=UPDATE606_PACKAGE_GENERATION
    )
    parser.add_argument("--manifest-sha256", default=UPDATE606_MANIFEST_SHA256)
    parser.add_argument(
        "--tables-bundle-sha256", default=UPDATE606_TABLES_BUNDLE_SHA256
    )
    parser.add_argument("--scripts-sha256", default=UPDATE606_SCRIPTS_SHA256)
    args = parser.parse_args()

    text_assets = load_update606_text_asset_directory(args.text_assets_dir)
    receipt = validate_update606_release_contract(
        text_assets,
        package_generation=args.package_generation,
        manifest_sha256=args.manifest_sha256,
        tables_bundle_sha256=args.tables_bundle_sha256,
        scripts_sha256=args.scripts_sha256,
    )
    print(json.dumps(receipt.as_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
