"""Read-only era audit for player-custom warehouse sample qualification.

This tool deliberately does not rebuild the truth registry or mutate samples.
Historical captures before the author-confirmed 2026-07-23 activity remain
official-random even when their localized map name starts with ``未知`` or the
map id is 2601. Only activity-era rows are evaluated by the new source rule.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bidking_lab.live.warehouse_source import (  # noqa: E402
    OFFICIAL_RANDOM_WAREHOUSE,
    POSTUPDATE_PLAYER_WAREHOUSE_GENERATION,
    classify_warehouse_source,
    warehouse_map_name_table_sha256,
    warehouse_map_table_status,
    warehouse_generation_from_capture_time,
)


DEFAULT_REGISTRY = ROOT / "data/sample_manifests/sample_pool_registry.json"
DEFAULT_MAPS = ROOT / "data/processed/maps.json"


def _map_names(rows: Any) -> dict[int, str]:
    if not isinstance(rows, list):
        raise ValueError("processed maps must be a list")
    names: dict[int, str] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        try:
            map_id = int(row.get("map_id"))
        except (TypeError, ValueError):
            continue
        names[map_id] = str(row.get("name") or "").strip()
    return names


def audit_registry(
    registry: Mapping[str, Any],
    map_names: Mapping[int, str],
    *,
    table_status: str | None = None,
) -> dict[str, Any]:
    samples = registry.get("samples")
    if not isinstance(samples, list):
        raise ValueError("truth registry has no samples list")

    active_valid = [
        row
        for row in samples
        if isinstance(row, Mapping)
        and bool(row.get("replay_eligible", row.get("active")))
        and row.get("verdict") == "valid"
    ]
    historical_same_name: Counter[int] = Counter()
    postupdate_by_qualification: Counter[str] = Counter()
    postupdate_rows = 0
    excluded_active_valid = 0
    missing_capture_time = 0
    capture_dates: list[str] = []
    map_entries = {
        map_id: {"name": name}
        for map_id, name in map_names.items()
    }
    map_table_sha256 = warehouse_map_name_table_sha256(map_entries)
    resolved_table_status = (
        str(table_status)
        if table_status is not None
        else warehouse_map_table_status(map_entries)
    )

    for row in active_valid:
        try:
            map_id = int(row.get("map_id"))
        except (TypeError, ValueError):
            map_id = 0
        map_name = str(map_names.get(map_id) or "")
        capture_time = str(row.get("capture_date") or "")
        generation = warehouse_generation_from_capture_time(capture_time)
        result = classify_warehouse_source(
            map_id,
            map_name,
            source_generation=generation,
            table_status=resolved_table_status,
        )
        if capture_time:
            capture_dates.append(capture_time)
        else:
            missing_capture_time += 1
        if generation == POSTUPDATE_PLAYER_WAREHOUSE_GENERATION:
            postupdate_rows += 1
            postupdate_by_qualification[result.qualification] += 1
        elif map_name.startswith("未知") or (
            map_id == 2601 and map_name == "隐秘拍卖会"
        ):
            historical_same_name[map_id] += 1
        if not result.official_distribution_eligible:
            excluded_active_valid += 1

    return {
        "registry_schema_version": registry.get("schema_version"),
        "registry_generated_at": registry.get("generated_at"),
        "activity_start_date": "2026-07-23",
        "map_table_status": resolved_table_status,
        "map_table_sha256": map_table_sha256,
        "active_valid_rows": len(active_valid),
        "capture_time_min": min(capture_dates) if capture_dates else None,
        "capture_time_max": max(capture_dates) if capture_dates else None,
        "missing_capture_time_rows": missing_capture_time,
        "historical_same_name_rows": sum(historical_same_name.values()),
        "historical_same_name_by_map": {
            str(map_id): count
            for map_id, count in sorted(historical_same_name.items())
        },
        "postupdate_rows": postupdate_rows,
        "postupdate_by_qualification": dict(
            sorted(postupdate_by_qualification.items())
        ),
        "excluded_active_valid_rows": excluded_active_valid,
        "official_active_valid_rows": (
            len(active_valid) - excluded_active_valid
        ),
        "conclusion": (
            "no_postupdate_samples_no_historical_reclassification"
            if postupdate_rows == 0 and excluded_active_valid == 0
            else "postupdate_qualification_present"
        ),
        "historical_contract": OFFICIAL_RANDOM_WAREHOUSE,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--maps", type=Path, default=DEFAULT_MAPS)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    maps = _map_names(json.loads(args.maps.read_text(encoding="utf-8")))
    result = audit_registry(registry, maps)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
