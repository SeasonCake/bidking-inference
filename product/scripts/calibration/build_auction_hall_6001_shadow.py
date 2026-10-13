#!/usr/bin/env python3
"""Build a generation-bound provisional distribution for auction hall 6001.

Exact managed code proves both 6001 and 2601 call the same server selector:

``BidMap.drop_group_id -> RandomItems -> DoDrop(2601) -> Table_Drop.items_list``.

The client package does not contain the current server ``Table_Drop`` leaves,
so this output is not an official rate table. It recomputes two explicit
same-pool scenarios from the frozen 2601 quality prior and exact 6001 value
curve: a placement-aware family candidate and an all-current-catalog upper
envelope. It never copies old per-item probabilities and never uses
``map_random_skill`` as Item categories; those values are per-round information
SkillGroup IDs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from bidking_lab.extract.bid_map_table import load_bid_map_table  # noqa: E402
from bidking_lab.extract.current_rankmap_package_contract import (  # noqa: E402
    CURRENT_PACKAGE_BID_MAP,
    CURRENT_PACKAGE_GENERATION,
    CURRENT_PACKAGE_ITEM,
    CURRENT_SCRIPTS,
)
from bidking_lab.extract.item_table import load_item_table  # noqa: E402
from bidking_lab.extract.tables import load_table_rows  # noqa: E402

MAP_ID = 6001
FAMILY_MAP_ID = 2601
ITEMS_DROPPABLE = {
    "bytes": 325_697,
    "sha256": "3c99d6852920a145d823ecd98b84ff3b477017ed20d35cb0a627b188902a63b7",
}
DROP_WEIGHTS = {
    "bytes": 2_485_268,
    "sha256": "5808321f22f3e37cf6ed1a5761893e645284f0a2d315c1e859fd0832e0d67207",
}
DROP_PRIORS = {
    "bytes": 20_740,
    "sha256": "dad18c5102fb20a64404e1093164b321c79037d95b6b1e3b8ddc24a1c5ad5ed8",
}
RED_LONGTAIL = {
    "bytes": 898,
    "sha256": "e0711f12e2c846bad7550e885d751cf58e3cb21481169f4ae8e39931d9bd699b",
}
RANKMAP_PACKAGE = {
    "bytes": 118_344,
    "sha256": "e6b4fae0caa8e71b342c79c3fc2235a9285ac4d28b918604af088b156885d1a7",
}
EXPECTED_ROUND_SKILL_GROUP_IDS = (103, 103, 103, 104, 105)
EXPECTED_VALUE_CURVE = (10, 1, 100, 1, 1_000, 10, 10_000, 100, 100_000, 30)
EXPECTED_QUALITY_PRIOR = {
    "q1": 0.1134022,
    "q3": 0.2061858,
    "q4": 0.2474221,
    "q5": 0.2268039,
    "q6": 0.2061861,
}
QUALITY_PRIOR_TOTAL = sum(EXPECTED_QUALITY_PRIOR.values())
NORMALIZED_QUALITY_PRIOR = {
    key: value / QUALITY_PRIOR_TOTAL
    for key, value in EXPECTED_QUALITY_PRIOR.items()
}
RETIRED_WORLD_CUP_Q6_IDS = frozenset({1016007, 1036006, 1036007, 1036008})
LOCALIZATION_ALIASES = frozenset({1046010, 1046011, 1046012})


class AuctionHallShadowError(ValueError):
    """An exact input or provisional-model invariant failed."""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _exact(path: Path, contract: dict[str, Any], *, label: str) -> bytes:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise AuctionHallShadowError(f"missing/unreadable {label}") from exc
    if len(raw) != contract["bytes"] or _sha256(raw) != contract["sha256"]:
        raise AuctionHallShadowError(f"{label} identity mismatch")
    return raw


def _strict_json(raw: bytes, *, label: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                raise AuctionHallShadowError(f"duplicate {label} key")
            out[key] = value
        return out

    try:
        return json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=reject_duplicates,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AuctionHallShadowError(f"invalid {label}") from exc


def _value_weight(anchors: tuple[int, ...], value: int) -> float:
    """Match the validated ``rankmap_drop_model._col6_weight`` algorithm."""

    points = [
        (anchors[index], anchors[index + 1])
        for index in range(0, len(anchors), 2)
    ]
    xs = [math.log10(point[0]) for point in points]
    ys = [math.log10(max(point[1], 0.01)) for point in points]
    probe = math.log10(max(value, 1))
    if probe <= xs[0]:
        index = 0
    elif probe >= xs[-1]:
        index = len(xs) - 2
    else:
        index = next(
            idx
            for idx in range(len(xs) - 1)
            if xs[idx] <= probe <= xs[idx + 1]
        )
    # Ratios outside [0, 1] intentionally extrapolate the first/last segment;
    # this is the existing validated algorithm, not endpoint clamping.
    ratio = (probe - xs[index]) / (xs[index + 1] - xs[index])
    return 10 ** (ys[index] + ratio * (ys[index + 1] - ys[index]))


def _quality_key(quality: int) -> str | None:
    if quality in {1, 2}:
        return "q1"
    if quality in {3, 4, 5, 6}:
        return f"q{quality}"
    return None


def _scenario(
    *,
    support_ids: set[int],
    catalog: dict[int, dict[str, Any]],
    value_curve: tuple[int, ...],
) -> dict[int, float]:
    by_quality = {key: [] for key in NORMALIZED_QUALITY_PRIOR}
    for item_id in sorted(support_ids):
        quality_key = catalog[item_id]["quality_key"]
        if quality_key in by_quality:
            by_quality[quality_key].append(item_id)
    if any(not by_quality[key] for key in NORMALIZED_QUALITY_PRIOR):
        raise AuctionHallShadowError("quality support incomplete")
    distribution: dict[int, float] = {}
    for quality_key, item_ids in by_quality.items():
        weights = {
            item_id: _value_weight(value_curve, catalog[item_id]["raw_value"])
            for item_id in item_ids
        }
        total = sum(weights.values())
        if not math.isfinite(total) or total <= 0:
            raise AuctionHallShadowError("value support invalid")
        for item_id, weight in weights.items():
            distribution[item_id] = NORMALIZED_QUALITY_PRIOR[quality_key] * weight / total
    if not math.isclose(sum(distribution.values()), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise AuctionHallShadowError("scenario is not normalized")
    return distribution


def _render(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def build_shadow(
    *,
    item_table: Path,
    bid_map: Path,
    rank_map: Path,
    scripts_dll: Path,
    items_droppable: Path,
    drop_weights: Path,
    drop_priors: Path,
    red_longtail: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _exact(item_table, CURRENT_PACKAGE_ITEM, label="Item package")
    _exact(bid_map, CURRENT_PACKAGE_BID_MAP, label="BidMap package")
    _exact(rank_map, RANKMAP_PACKAGE, label="RankMap package")
    _exact(scripts_dll, CURRENT_SCRIPTS, label="Scripts.dll")
    droppable_raw = _exact(items_droppable, ITEMS_DROPPABLE, label="items_droppable")
    weights_raw = _exact(drop_weights, DROP_WEIGHTS, label="drop weights")
    priors_raw = _exact(drop_priors, DROP_PRIORS, label="drop priors")
    red_raw = _exact(red_longtail, RED_LONGTAIL, label="red longtail")

    bid_maps = load_bid_map_table(bid_map)
    current = bid_maps.get(MAP_ID)
    family = bid_maps.get(FAMILY_MAP_ID)
    if current is None or family is None:
        raise AuctionHallShadowError("6001/2601 BidMap row missing")
    if (
        current.drop_pool_id != FAMILY_MAP_ID
        or family.drop_pool_id != FAMILY_MAP_ID
        or tuple(current.round_skill_group_ids) != EXPECTED_ROUND_SKILL_GROUP_IDS
        or current.auction_mode != "auction_hall"
        or current.items_per_session_min != 22
        or current.items_per_session_max != 44
    ):
        raise AuctionHallShadowError("6001 selector structure mismatch")

    rank_rows = {int(row[0]): row for row in load_table_rows(rank_map)}
    if len(rank_rows) != 176 or MAP_ID not in rank_rows or FAMILY_MAP_ID not in rank_rows:
        raise AuctionHallShadowError("RankMap scope mismatch")
    try:
        value_curve = tuple(json.loads(rank_rows[MAP_ID][6]))
    except (IndexError, TypeError, json.JSONDecodeError) as exc:
        raise AuctionHallShadowError("6001 value curve malformed") from exc
    if value_curve != EXPECTED_VALUE_CURVE:
        raise AuctionHallShadowError("6001 value curve mismatch")

    priors = _strict_json(priors_raw, label="drop priors")
    prior = priors.get(str(FAMILY_MAP_ID)) if priors.__class__ is dict else None
    quality_prior = prior.get("quality_probs") if prior.__class__ is dict else None
    if quality_prior != EXPECTED_QUALITY_PRIOR:
        raise AuctionHallShadowError("2601 family quality prior mismatch")
    weights = _strict_json(weights_raw, label="drop weights")
    family_weights = weights.get(str(FAMILY_MAP_ID)) if weights.__class__ is dict else None
    family_items = family_weights.get("items") if family_weights.__class__ is dict else None
    if family_items.__class__ is not dict:
        raise AuctionHallShadowError("2601 family support missing")
    family_support = {
        int(item_id)
        for item_id, row in family_items.items()
        if (
            item_id.__class__ is str
            and item_id.isascii()
            and item_id.isdigit()
            and row.__class__ is list
            and len(row) == 3
            and row[0].__class__ in {int, float}
            and not isinstance(row[0], bool)
            and math.isfinite(float(row[0]))
            and row[0] > 0
        )
    }

    rows = _strict_json(droppable_raw, label="items_droppable")
    red_values = _strict_json(red_raw, label="red longtail")
    if rows.__class__ is not list or red_values.__class__ is not dict:
        raise AuctionHallShadowError("current catalog input malformed")
    raw_items = load_item_table(item_table)
    catalog: dict[int, dict[str, Any]] = {}
    for row in rows:
        if row.__class__ is not dict:
            raise AuctionHallShadowError("droppable row malformed")
        item_id = row.get("item_id")
        value = row.get("value")
        quality = row.get("quality")
        width = row.get("shape_w")
        height = row.get("shape_h")
        if (
            item_id.__class__ is not int
            or value.__class__ is not int
            or quality.__class__ is not int
            or width.__class__ is not int
            or height.__class__ is not int
            or item_id in catalog
        ):
            raise AuctionHallShadowError("droppable row identity malformed")
        source = raw_items.get(item_id)
        if source is None or (
            int(source.value),
            int(source.quality),
            int(source.shape_w),
            int(source.shape_h),
        ) != (value, quality, width, height):
            raise AuctionHallShadowError("Item/droppable identity mismatch")
        normalized = red_values.get(str(item_id), value)
        if normalized.__class__ is not int or normalized <= 0:
            raise AuctionHallShadowError("normalized value malformed")
        catalog[item_id] = {
            "normalized_value": normalized,
            "quality": quality,
            "quality_key": _quality_key(quality),
            "raw_value": value,
        }
    current_support = set(catalog)
    if (
        len(current_support) != 680
        or RETIRED_WORLD_CUP_Q6_IDS & current_support
        or LOCALIZATION_ALIASES & current_support
        or not family_support
        or not family_support <= current_support
    ):
        raise AuctionHallShadowError("current/legacy support scope mismatch")

    scenario_support = {
        "group2601_placement_aware_candidate": family_support,
        "all_current_catalog_upper_envelope": current_support,
    }
    scenarios = {
        name: _scenario(
            support_ids=support,
            catalog=catalog,
            value_curve=value_curve,
        )
        for name, support in scenario_support.items()
    }
    item_rows = []
    for item_id in sorted(current_support):
        meta = catalog[item_id]
        probabilities = {
            scenario: distribution.get(item_id, 0.0)
            for scenario, distribution in scenarios.items()
        }
        item_rows.append(
            {
                "item_id": item_id,
                "normalized_value": meta["normalized_value"],
                "probability_range_per_draw": [
                    min(probabilities.values()),
                    max(probabilities.values()),
                ],
                "quality": meta["quality"],
                "raw_value": meta["raw_value"],
                "scenario_probabilities": probabilities,
            }
        )
    item_rows.sort(
        key=lambda row: (
            -row["scenario_probabilities"]["group2601_placement_aware_candidate"],
            row["item_id"],
        )
    )
    scenario_summaries: dict[str, dict[str, Any]] = {}
    for name, distribution in scenarios.items():
        quality_mass: dict[str, float] = {}
        for item_id, probability in distribution.items():
            key = catalog[item_id]["quality_key"]
            quality_mass[key] = quality_mass.get(key, 0.0) + probability
        scenario_summaries[name] = {
            "expected_normalized_value_per_draw": round(
                sum(
                    probability * catalog[item_id]["normalized_value"]
                    for item_id, probability in distribution.items()
                ),
                3,
            ),
            "expected_raw_value_per_draw": round(
                sum(
                    probability * catalog[item_id]["raw_value"]
                    for item_id, probability in distribution.items()
                ),
                3,
            ),
            "probability_sum": sum(
                row["scenario_probabilities"][name] for row in item_rows
            ),
            "quality_mass": {
                key: round(value, 12)
                for key, value in sorted(quality_mass.items())
            },
            "support_items": len(scenario_support[name]),
        }

    document = {
        "schema": "bidking_auction_hall_6001_shadow_distribution_v1",
        "generation": CURRENT_PACKAGE_GENERATION,
        "map_id": MAP_ID,
        "lifecycle": "recognized_provisional_shadow",
        "distribution_authority": False,
        "official_distribution_eligible": False,
        "provisional_rate_available": True,
        "provisional_distribution_available": True,
        "provisional_quote_available": True,
        "quote_authority": False,
        "official_quote_authority": False,
        "affects_bid": True,
        "bid_mode": "provisional_estimated_only",
        "algorithm": {
            "id": "6001_same_pool_family_quality_placement_aware_v2",
            "family_quality_prior_map_id": FAMILY_MAP_ID,
            "primary_scenario": "group2601_placement_aware_candidate",
            "upper_envelope_scenario": "all_current_catalog_upper_envelope",
            "item_weight_algorithm": "validated_rankmap_col6_loglog_extrapolation",
            "value_curve": list(value_curve),
            "count_model": None,
            "count_policy": "observed_count_only",
            "round_skill_group_ids": list(EXPECTED_ROUND_SKILL_GROUP_IDS),
            "round_skill_group_policy": "identity_only_not_distribution_input",
            "weak_family_quality_prior_raw": EXPECTED_QUALITY_PRIOR,
            "weak_family_quality_prior_sum": QUALITY_PRIOR_TOTAL,
            "weak_family_quality_prior_normalized": NORMALIZED_QUALITY_PRIOR,
        },
        "selector": {
            "consumer": (
                "RandomItems(mapId)->BidMap.drop_group_id->"
                "DoDrop(groupId,times)->Table_Drop.items_list"
            ),
            "drop_pool_id": FAMILY_MAP_ID,
            "static_item_count_min_diagnostic": current.items_per_session_min,
            "static_item_count_max_diagnostic": current.items_per_session_max,
            "static_item_count_policy": "diagnostic_not_probability_input",
            "server_table_drop_present_in_client": False,
            "server_table_drop_presence_basis": (
                "external_static_asset_inventory_observation"
            ),
        },
        "confidence": {
            "structure": "exact_current_package_and_managed_consumer",
            "support": "provisional_same_pool_placement_aware",
            "quality_prior": "weak_family_2601",
            "item_rate": "provisional_recomputed_not_server_table_drop",
            "count": "unavailable_observed_only",
        },
        "summary": {
            "catalog_envelope_only_ids": sorted(current_support - family_support),
            "scenarios": scenario_summaries,
        },
        "items": item_rows,
        "sources": {
            "BidMap.txt": dict(CURRENT_PACKAGE_BID_MAP),
            "Item.txt": dict(CURRENT_PACKAGE_ITEM),
            "RankMap.txt": dict(RANKMAP_PACKAGE),
            "Scripts.dll": dict(CURRENT_SCRIPTS),
            "drop_table_priors_0627.json": dict(DROP_PRIORS),
            "drop_weights_all_maps_0627.json": dict(DROP_WEIGHTS),
            "items_droppable.json": dict(ITEMS_DROPPABLE),
            "red_longtail_normalized_0627.json": dict(RED_LONGTAIL),
        },
    }
    rendered = _render(document)
    receipt = {
        "schema": "bidking_auction_hall_6001_shadow_build_receipt_v1",
        "generation": CURRENT_PACKAGE_GENERATION,
        "map_id": MAP_ID,
        "algorithm": document["algorithm"]["id"],
        "frozen_prior_unchanged": True,
        "distribution_authority": False,
        "quote_authority": False,
        "provisional_distribution_available": True,
        "provisional_quote_available": True,
        "bid_mode": "provisional_estimated_only",
        "generator_source": {
            "bytes": len(Path(__file__).read_bytes()),
            "sha256": _sha256(Path(__file__).read_bytes()),
        },
        "inputs": document["sources"],
        "evidence_boundary": {
            "consumer_semantics": "external_static_analysis_not_rederived_by_builder",
            "server_table_drop_presence": (
                "external_static_asset_inventory_not_rederived_by_builder"
            ),
        },
        "output": {
            "bytes": len(rendered),
            "sha256": _sha256(rendered),
            "items": len(item_rows),
            "scenario_probability_sums": {
                scenario: sum(
                    row["scenario_probabilities"][scenario] for row in item_rows
                )
                for scenario in scenarios
            },
        },
    }
    return document, receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--item-table", type=Path, required=True)
    parser.add_argument("--bid-map", type=Path, required=True)
    parser.add_argument("--rank-map", type=Path, required=True)
    parser.add_argument("--scripts-dll", type=Path, required=True)
    parser.add_argument("--items-droppable", type=Path, required=True)
    parser.add_argument("--drop-weights", type=Path, required=True)
    parser.add_argument("--drop-priors", type=Path, required=True)
    parser.add_argument("--red-longtail", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--receipt-out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.out.resolve() == args.receipt_out.resolve():
            raise AuctionHallShadowError("output and receipt paths must differ")
        document, receipt = build_shadow(
            item_table=args.item_table,
            bid_map=args.bid_map,
            rank_map=args.rank_map,
            scripts_dll=args.scripts_dll,
            items_droppable=args.items_droppable,
            drop_weights=args.drop_weights,
            drop_priors=args.drop_priors,
            red_longtail=args.red_longtail,
        )
        rendered = _render(document)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.receipt_out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(rendered)
        args.receipt_out.write_text(
            json.dumps(receipt, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    except (AuctionHallShadowError, OSError) as exc:
        print(f"input-assembly-not-ready: {exc}", file=sys.stderr)
        return 2
    print(
        f"wrote {args.out}: items={receipt['output']['items']} "
        f"sha256={receipt['output']['sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
