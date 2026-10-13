"""Prove current RankMap capture-to-UI quotes without map/pool rebinding.

The two private captures remain external.  This checker verifies their exact
identity, consumes only the last pre-settlement prefix, and prints no raw,
session, player, item, or pool data.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
_CAPTURE_CONTRACTS = (
    {
        "map_id": 5601,
        "bytes": 46_940,
        "sha256": "077299283079c20c4aafda9267816d5bca4e8a082f70d8e7dfbb5014b745b8d6",
        "prefix_rows": 32,
        "round": 4,
    },
    {
        "map_id": 5607,
        "bytes": 37_797,
        "sha256": "c3a1ab2ec4ceee0f87574acfc2547cebe25a8704a575b5bc73f2dd9a91931bc1",
        "prefix_rows": 24,
        "round": 3,
    },
)


def _read_exact_capture(path: Path, contract: dict[str, Any]) -> list[Any]:
    raw = path.read_bytes()
    if (
        len(raw) != contract["bytes"]
        or hashlib.sha256(raw).hexdigest() != contract["sha256"]
    ):
        raise ValueError("capture_identity_mismatch")
    parsed = json.loads(raw)
    if type(parsed) is not list or len(parsed) < contract["prefix_rows"]:  # noqa: E721
        raise ValueError("capture_shape_mismatch")
    return parsed


def _positive_quotes(summary: dict[str, Any]) -> tuple[int, int, int]:
    ref = summary.get("ahmed_ref")
    if not isinstance(ref, dict):
        raise AssertionError("reference_summary_missing")
    values = tuple(ref.get(key) for key in ("conservative", "balanced", "aggressive"))
    if not all(type(value) is int and value > 0 for value in values):  # noqa: E721
        raise AssertionError("reference_quote_missing")
    if not values[0] < values[1] < values[2]:
        raise AssertionError("reference_quote_order_invalid")
    return values


def _runtime_paths(runtime_root: Path) -> None:
    for candidate in (
        runtime_root / "src",
        runtime_root / "hero_ref" / "src",
        runtime_root / "hero_ref" / "tools",
    ):
        if str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))


def _check_capture(
    *,
    path: Path,
    contract: dict[str, Any],
    tables: Any,
    monitor: Any,
    engine: Any,
    panel: Any,
    overlay_type: Any,
    bound_type: Any,
) -> dict[str, Any]:
    rows = _read_exact_capture(path, contract)
    map_id = contract["map_id"]
    bid_map = tables.maps.get(map_id)
    if (
        bid_map is None
        or not isinstance(tables.drops, bound_type)
        or bid_map.drop_pool_id not in tables.drops.bound_pool_ids
    ):
        raise AssertionError("same_pool_authority_missing")
    prefix = json.dumps(
        rows[: contract["prefix_rows"]],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    artifact = monitor.build_monitor_artifact_from_payload(
        prefix,
        file="support-safe-exact-prefix",
        tables=tables,
        n_trials=80,
        roi_trials=0,
        shadow_trials=20,
        run_debug_shadows=False,
        seed=1,
        formal_mode="v3-practical",
    )
    support = artifact.get("rankmap_distribution_support")
    if not (
        artifact.get("map_id") == map_id
        and artifact.get("model_map_id") == map_id
        and artifact.get("map_alias") == {}
        and artifact.get("map_alias_mode") == ""
        and artifact.get("phase") == "bidding"
        and artifact.get("round") == contract["round"]
        and isinstance(artifact.get("states"), int)
        and artifact["states"] > 0
        and isinstance(artifact.get("batches"), int)
        and artifact["batches"] > 0
        and isinstance(artifact.get("structured_ref_inputs"), dict)
        and isinstance(support, dict)
        and support.get("status") == "current_rankmap_algorithm_prior"
        and support.get("active_map_id") == map_id
        and support.get("map_rebound") is False
        and support.get("pool_rebound") is False
        and support.get("drop_or_dropmap_textasset") is False
        and support.get("claims_item_level_drop_graph") is False
    ):
        raise AssertionError("snapshot_authority_contract_failed")

    prepared = engine.prepare_reference_engine_snapshot(artifact)
    direct = engine.run_reference_engine(prepared, max_combos=8_000)
    direct_quotes = (direct.conservative, direct.balanced, direct.aggressive)
    if not all(type(value) is int and value > 0 for value in direct_quotes):  # noqa: E721
        raise AssertionError("engine_quote_missing")

    panel_summary = panel.summarize_snapshot(
        artifact,
        snapshot_path=Path("support-safe-exact-prefix.json"),
    )
    panel_quotes = _positive_quotes(panel_summary)
    overlay = object.__new__(overlay_type)
    tk_summary = overlay._manual_result_summary(artifact)  # noqa: SLF001
    tk_quotes = _positive_quotes(tk_summary)
    if not panel_quotes or not tk_quotes:
        raise AssertionError("consumer_quote_missing")
    if (
        panel_summary.get("rankmap_distribution_support") != support
        or panel_summary.get("diagnostics", {}).get(
            "rankmap_distribution_support"
        )
        != support
    ):
        raise AssertionError("consumer_authority_receipt_lost")
    return {
        "map_id": map_id,
        "phase": "bidding",
        "round": contract["round"],
        "capture_scheduled": True,
        "snapshot_ready": True,
        "quote_ready": True,
        "panel_api_ready": True,
        "tk_ready": True,
        "map_rebound": False,
        "pool_rebound": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-root", required=True, type=Path)
    parser.add_argument("--runtime-root", type=Path, default=ROOT)
    parser.add_argument("--capture-5601", required=True, type=Path)
    parser.add_argument("--capture-5607", required=True, type=Path)
    parser.add_argument("--require-compiled-engine", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        package_root = args.package_root.resolve(strict=True)
        runtime_root = args.runtime_root.resolve(strict=True)
        os.environ["BIDKING_LAB_ROOT"] = str(package_root)
        os.environ["BIDKING_PROJECT_ROOT"] = str(package_root)
        static_data = (
            runtime_root
            / "external_references"
            / "AuctionAnalyzer4.13.3"
            / "_decompiled"
            / "MapBidCalculator"
            / "MapBidCalculator"
            / "Models"
            / "StaticData.cs"
        )
        if static_data.is_file():
            os.environ["BIDKING_AHMAD_STATIC_DATA"] = str(static_data)
        _runtime_paths(runtime_root)
        monitor = importlib.import_module("bidking_lab.live.monitor")
        engine = importlib.import_module("ahmad_ref_engine")
        panel = importlib.import_module("ahmad_live_panel_server")
        overlay = importlib.import_module("ahmad_tk_overlay")
        basic_mc = importlib.import_module("bidking_lab.simulation.basic_mc")
        if args.require_compiled_engine and Path(engine.__file__).suffix.lower() != ".pyd":
            raise AssertionError("compiled_engine_required")
        tables = monitor.load_monitor_tables(
            tables_dir=package_root / "data" / "raw" / "tables"
        )
        if tables.rankmap_distribution_status != "ok":
            raise AssertionError("rankmap_authority_unavailable")
        paths = (args.capture_5601, args.capture_5607)
        results = [
            _check_capture(
                path=path,
                contract=contract,
                tables=tables,
                monitor=monitor,
                engine=engine,
                panel=panel,
                overlay_type=overlay.AhmadTkOverlay,
                bound_type=basic_mc.RankMapAlgorithmPriorBoundDropPools,
            )
            for path, contract in zip(paths, _CAPTURE_CONTRACTS, strict=True)
        ]
    except Exception as exc:  # noqa: BLE001 - formal checker emits type only
        print(
            json.dumps(
                {"schema_version": 1, "status": "fail", "error": type(exc).__name__},
                separators=(",", ":"),
            )
        )
        return 1
    print(
        json.dumps(
            {
                "schema_version": 1,
                "status": "pass",
                "authority_mode": "rankmap_drop_model_algorithm_prior",
                "captures": results,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
