"""Build q4/q5/q6 count/cell presolve data from local game tables."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bidking_lab.config import project_root  # noqa: E402
from bidking_lab.extract.bid_map_table import load_bid_map_table  # noqa: E402
from bidking_lab.extract.drop_table import load_drop_table  # noqa: E402
from bidking_lab.extract.item_table import load_item_table  # noqa: E402
from bidking_lab.inference.quality_combo_presolve import (  # noqa: E402
    quality_combo_presolve_for_map,
    quality_combo_presolve_payload,
)


def _default_tables_dir() -> Path:
    return project_root() / "data" / "raw" / "tables"


def modeled_presolve_payload(model_root: Path, *, map_ids=None, max_count=None):
    """Use the frozen current model's physical-map support, not stale Drop rows."""
    from bidking_lab.extract import official_20261008_model_profile as profile
    from bidking_lab.inference.ground_truth import prepare_session_sampler
    from bidking_lab.simulation.basic_mc import FlattenedPool, bind_rankmap_algorithm_prior

    outputs = {relative: (model_root / relative).read_bytes() for relative in profile.OUTPUT_IDENTITIES}
    profile.validate_profile_outputs(outputs, generation=profile.GENERATION)
    maps = load_bid_map_table(model_root / "data/raw/tables/BidMap.txt")
    items = load_item_table(model_root / "data/raw/tables/Item.txt")
    ordinary = json.loads(outputs["data/processed/drop_weights_all_maps_0627.json"])
    ranked = json.loads(outputs["data/processed/rankmap_drop_weights_current.json"])
    for key in ordinary.keys() & ranked.keys():
        if ordinary[key] != ranked[key]:
            raise ValueError("conflicting physical-map model inputs")
    weights = ordinary | ranked
    selected = sorted(weights, key=int) if map_ids is None else [str(mid) for mid in sorted(set(map_ids))]
    if not selected or any(key not in weights for key in selected):
        raise ValueError("map is outside the selected model profile")
    if max_count is not None and (type(max_count) is not int or max_count <= 0):
        raise ValueError("max_count must be positive")

    scoped = {}
    for key, row in weights.items():
        mid = int(key)
        entries = [(int(iid), triple) for iid, triple in row["items"].items() if triple[0] > 0]
        scoped[mid] = (maps[mid].drop_pool_id, FlattenedPool(
            item_ids=[iid for iid, _ in entries], probabilities=[triple[0] for _, triple in entries],
            n_min=[1] * len(entries), n_max=[1] * len(entries),
            values=[items[iid].value for iid, _ in entries],
            resolution_policy="rankmap_drop_model_algorithm_prior", resolution_generation=profile.GENERATION,
        ))
    drops = bind_rankmap_algorithm_prior({}, {}, package_generation=profile.GENERATION,
                                         authority_mode="rankmap_drop_model_algorithm_prior", flattened_by_map_id=scoped)
    result = {"version": 1, "qualities": [4, 5, 6], "model_generation": profile.GENERATION,
              "model_distribution_authority": True, "maps": {}}
    geometry_cache = {}
    for key in selected:
        mid = int(key)
        sampler = prepare_session_sampler(mid, maps=maps, drops=drops, items=items)
        limit = max_count if max_count is not None else sampler.items_per_session_max
        options = set()
        for pool in sampler.pools:
            for item, lo, hi in zip(pool.items, pool.n_min, pool.n_max):
                if item.quality in (4, 5, 6):
                    options.add((item.quality, item.shape_w * item.shape_h, int(lo), int(hi)))
        geometry = (limit, tuple(sorted(options)))
        if geometry not in geometry_cache:
            geometry_cache[geometry] = quality_combo_presolve_for_map(
                mid, maps=maps, drops=drops, items=items, max_count=max_count)
        result["maps"][key] = {
            str(quality): {str(count): list(cells) for count, cells in by_count.items()}
            for quality, by_count in geometry_cache[geometry].items()
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Precompute reachable q4/q5/q6 count/cell combinations.",
    )
    parser.add_argument(
        "--tables-dir",
        default=str(_default_tables_dir()),
        help="Directory containing BidMap.txt, Drop.txt, and Item.txt.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output JSON path.",
    )
    parser.add_argument("--model-root", type=Path, help="Frozen 76b7ca model output root; requires explicit --out.")
    parser.add_argument(
        "--maps",
        nargs="*",
        type=int,
        help="Map ids to include. Defaults to all loaded maps.",
    )
    parser.add_argument(
        "--max-count",
        type=int,
        default=None,
        help="Optional max per-quality count to retain.",
    )
    args = parser.parse_args()

    if args.model_root:
        if not args.out:
            parser.error("--model-root requires explicit --out (no implicit package-data overwrite)")
        payload = modeled_presolve_payload(args.model_root, map_ids=args.maps or None, max_count=args.max_count)
        map_ids = list(payload["maps"])
    else:
        tables_dir = Path(args.tables_dir)
        maps = load_bid_map_table(tables_dir / "BidMap.txt")
        drops = load_drop_table(tables_dir / "Drop.txt")
        items = load_item_table(tables_dir / "Item.txt")
        map_ids = sorted(args.maps or maps)
        payload = quality_combo_presolve_payload(
            map_ids, maps=maps, drops=drops, items=items, max_count=args.max_count)
    out = Path(args.out) if args.out else project_root() / "data/processed/quality_combo_presolve_q456.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"wrote {out} maps={len(map_ids)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
