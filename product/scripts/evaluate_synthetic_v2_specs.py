"""Evaluate small synthetic v2 inference specs.

These specs are not Fatbeans captures. They are compact JSON fixtures for
probing how a specific evidence pattern changes the v2 posterior.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", newline="")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bidking_lab.inference.observation import (  # noqa: E402
    CategoryItemObservation,
    QualityBucketObs,
    SessionObs,
)
from bidking_lab.inference.v2 import (  # noqa: E402
    EvidenceStoreBuilder,
    estimate_posterior_v2,
)
from bidking_lab.live.monitor import load_monitor_tables  # noqa: E402
from bidking_lab.inference.ground_truth import prepare_session_sampler  # noqa: E402


def _default_paths() -> list[Path]:
    root = ROOT / "data" / "samples" / "synthetic_v2"
    if not root.exists():
        return []
    return sorted(root.glob("*.json"))


def _round(value: float | int | None) -> int | None:
    if value is None:
        return None
    return int(round(float(value)))


def _shape_dims(shape_key: str | int | None) -> tuple[int, int] | None:
    if shape_key is None:
        return None
    try:
        code = int(shape_key)
    except (TypeError, ValueError):
        return None
    width = code // 10
    height = code % 10
    if width <= 0 or height <= 0:
        return None
    return width, height


def _category_item(raw: dict[str, Any]) -> CategoryItemObservation:
    return CategoryItemObservation(
        category=int(raw["category"]),
        cells=int(raw["cells"]) if raw.get("cells") is not None else None,
        shape_key=str(raw["shape_key"]) if raw.get("shape_key") is not None else None,
        quality=int(raw["quality"]) if raw.get("quality") is not None else None,
        count=int(raw.get("count", 1)),
    )


def _bucket(raw: dict[str, Any]) -> QualityBucketObs:
    return QualityBucketObs(
        quality=int(raw["quality"]),
        total_cells=int(raw["total_cells"]) if raw.get("total_cells") is not None else None,
        total_cells_min=(
            int(raw["total_cells_min"]) if raw.get("total_cells_min") is not None else None
        ),
        count=int(raw["count"]) if raw.get("count") is not None else None,
        count_min=int(raw["count_min"]) if raw.get("count_min") is not None else None,
        value_sum=int(raw["value_sum"]) if raw.get("value_sum") is not None else None,
        avg_value=float(raw["avg_value"]) if raw.get("avg_value") is not None else None,
    )


def _matches(item: Any, target: CategoryItemObservation) -> bool:
    if target.category not in item.tags:
        return False
    if target.quality is not None and item.quality != target.quality:
        return False
    if target.cells is not None and item.shape_w * item.shape_h != target.cells:
        return False
    dims = _shape_dims(target.shape_key)
    if dims is not None and (item.shape_w, item.shape_h) != dims:
        return False
    return True


def _candidate_rows(map_id: int, targets: tuple[CategoryItemObservation, ...], tables: Any) -> list[dict[str, Any]]:
    sampler = prepare_session_sampler(
        map_id,
        maps=tables.maps,
        drops=tables.drops,
        items=tables.items,
    )
    seen: set[tuple[int, int]] = set()
    rows: list[dict[str, Any]] = []
    for target_index, target in enumerate(targets):
        for pool in sampler.pools:
            for item in pool.items:
                key = (target_index, item.item_id)
                if key in seen or not _matches(item, target):
                    continue
                seen.add(key)
                rows.append(
                    {
                        "target": target_index,
                        "item_id": item.item_id,
                        "name": item.name,
                        "quality": item.quality,
                        "shape": f"{item.shape_w}x{item.shape_h}",
                        "value": item.value,
                        "tags": item.tags,
                    }
                )
    rows.sort(key=lambda row: (row["target"], -row["value"], row["item_id"]))
    return rows


def _quantiles(report: Any, field: str) -> dict[str, int | None]:
    summary = getattr(report, field)
    return {
        "p10": _round(summary.p10 if summary else None),
        "p50": _round(summary.p50 if summary else None),
        "p90": _round(summary.p90 if summary else None),
    }


def evaluate_spec(path: Path, *, tables: Any, trials: int, seed: int) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    map_id = int(raw["map_id"])
    targets = tuple(_category_item(item) for item in raw.get("category_items", []))
    buckets = {
        int(item["quality"]): _bucket(item)
        for item in raw.get("buckets", [])
    }
    obs = SessionObs(
        map_id=map_id,
        hero=raw.get("hero", "aisha"),
        warehouse_total_cells=raw.get("warehouse_total_cells"),
        total_item_count=raw.get("total_item_count"),
        category_items=targets,
        buckets=buckets,
    )
    baseline_obs = SessionObs(
        map_id=map_id,
        hero=obs.hero,
        warehouse_total_cells=obs.warehouse_total_cells,
        total_item_count=obs.total_item_count,
        buckets=buckets,
    )
    store = EvidenceStoreBuilder().build()
    baseline = estimate_posterior_v2(
        map_id,
        baseline_obs,
        store,
        maps=tables.maps,
        drops=tables.drops,
        items=tables.items,
        n_trials=trials,
        seed=seed,
    )
    conditioned = estimate_posterior_v2(
        map_id,
        obs,
        store,
        maps=tables.maps,
        drops=tables.drops,
        items=tables.items,
        n_trials=trials,
        seed=seed,
    )
    return {
        "file": path.name,
        "name": raw.get("name", path.stem),
        "map_id": map_id,
        "hero": obs.hero,
        "candidates": _candidate_rows(map_id, targets, tables),
        "baseline": {
            "matched": baseline.n_matched,
            "value": _quantiles(baseline, "total_value"),
            "decision_value": _quantiles(baseline, "decision_value"),
            "q6_value": _quantiles(baseline, "q6_value"),
            "diagnostics": baseline.diagnostics,
        },
        "conditioned": {
            "matched": conditioned.n_matched,
            "value": _quantiles(conditioned, "total_value"),
            "decision_value": _quantiles(conditioned, "decision_value"),
            "q6_value": _quantiles(conditioned, "q6_value"),
            "diagnostics": conditioned.diagnostics,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate synthetic v2 JSON specs.")
    parser.add_argument("paths", nargs="*", help="Spec JSON files. Defaults to data/samples/synthetic_v2/*.json.")
    parser.add_argument("--trials", type=int, default=500)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()

    paths = [Path(path) for path in args.paths] if args.paths else _default_paths()
    tables = load_monitor_tables()
    for path in paths:
        print(json.dumps(
            evaluate_spec(path, tables=tables, trials=args.trials, seed=args.seed),
            ensure_ascii=False,
            separators=(",", ":"),
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
