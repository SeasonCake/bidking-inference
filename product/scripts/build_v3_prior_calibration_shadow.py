"""Build the v3 empirical prior calibration shadow table.

The output is aggregate calibration metadata only. Raw Fatbeans captures remain
ignored under data/samples.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", newline="")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", newline="")

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SRC = ROOT / "src"
for path in (SCRIPTS, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from bidking_lab.inference.v3.calibration import propose_prior_calibration  # noqa: E402
from evaluate_fatbeans_v3_samples import (  # noqa: E402
    _default_paths,
    evaluate_paths,
    load_monitor_tables,
)
from summarize_v3_map_audit import summarize_maps  # noqa: E402
from summarize_v3_prior_archive_calibration import (  # noqa: E402
    _archive_raw_truth_by_map,
    _prior_values_for_map,
    summarize_calibration_from_values,
)


def _default_output() -> Path:
    return ROOT / "data" / "processed" / "v3_prior_calibration_shadow.json"


def build_payload(
    *,
    paths: list[Path],
    prior_trials: int,
    prior_seed: int,
    posterior_trials: int,
    posterior_seed: int,
    min_sessions: int,
) -> dict[str, Any]:
    tables = load_monitor_tables()
    rows, errors = evaluate_paths(
        paths or list(_default_paths()),
        tables=tables,
        posterior_trials=posterior_trials,
        posterior_seed=posterior_seed,
        calibration_entries={},
    )
    actual_by_map = _archive_raw_truth_by_map(rows)
    prior_by_map = {
        map_id: _prior_values_for_map(
            map_id,
            tables=tables,
            prior_trials=prior_trials,
            seed=prior_seed,
        )
        for map_id in actual_by_map
        if len(actual_by_map[map_id]) >= int(min_sessions)
    }
    map_names = {
        int(map_id): str(getattr(bid_map, "name", ""))
        for map_id, bid_map in tables.maps.items()
    }
    audit_by_map = {
        int(row["map_id"]): row
        for row in summarize_maps(rows)
        if str(row.get("map_id") or "").isdigit()
    }
    raw_rows = summarize_calibration_from_values(
        actual_by_map,
        prior_by_map,
        map_names=map_names,
        min_sessions=min_sessions,
    )
    entries = []
    for row in raw_rows:
        map_id = int(row["map_id"])
        audit = audit_by_map.get(map_id, {})
        entry = propose_prior_calibration(
            map_id=map_id,
            map_name=str(row.get("map_name") or ""),
            map_family=str(audit.get("map_family") or ""),
            archive_sessions=int(row.get("archive_sessions") or 0),
            prior_trials=int(row.get("prior_trials") or 0),
            actual_raw_p50=row.get("actual_raw_p50"),
            actual_raw_p90=row.get("actual_raw_p90"),
            prior_raw_p50=row.get("prior_raw_p50"),
            prior_raw_p90=row.get("prior_raw_p90"),
            median_ratio=row.get("median_ratio"),
            p90_ratio=row.get("p90_ratio"),
            formal_p50_over_rate=audit.get("formal_p50_over_rate"),
            baseline_formal_p50_mae=audit.get("formal_p50_mae"),
            baseline_formal_p50_bias=audit.get("formal_p50_bias"),
            source="archive_prior_shadow_in_sample",
        )
        entries.append(entry.to_dict())
    entries.sort(
        key=lambda item: (
            item["status"] != "active_shadow",
            -abs(float(item["median_ratio"] or 1.0) - 1.0),
            item["map_id"],
        )
    )
    return {
        "schema_version": 1,
        "label": "v3_prior_calibration_shadow",
        "affects_bid": False,
        "source": "fatbeans_archive_v3_in_sample",
        "paths": [str(path) for path in (paths or list(_default_paths()))],
        "posterior_trials": int(posterior_trials),
        "posterior_seed": int(posterior_seed),
        "prior_trials": int(prior_trials),
        "prior_seed": int(prior_seed),
        "min_sessions": int(min_sessions),
        "row_count": len(rows),
        "parse_errors": errors,
        "entries": entries,
    }


def _print_summary(payload: dict[str, Any], *, top: int) -> None:
    entries = list(payload.get("entries") or [])
    active = [row for row in entries if row.get("status") == "active_shadow"]
    print(
        " ".join(
            (
                f"entries={len(entries)}",
                f"active={len(active)}",
                f"affects_bid={payload.get('affects_bid')}",
                f"parse_errors={len(payload.get('parse_errors') or [])}",
            )
        )
    )
    for row in entries[:top]:
        print(
            " ".join(
                (
                    f"map_id={row['map_id']}",
                    f"name={row.get('map_name') or '-'}",
                    f"family={row.get('map_family') or '-'}",
                    f"sessions={row.get('archive_sessions')}",
                    f"median_ratio={row.get('median_ratio')}",
                    f"p90_ratio={row.get('p90_ratio')}",
                    f"over={row.get('formal_p50_over_rate')}",
                    f"bias={row.get('baseline_formal_p50_bias')}",
                    f"status={row.get('status')}",
                    f"gate={row.get('gate_reason')}",
                    f"scale={row.get('scale')}",
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build v3 prior calibration shadow table from archive samples.",
    )
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--output", type=Path, default=_default_output())
    parser.add_argument("--prior-trials", type=int, default=10_000)
    parser.add_argument("--prior-seed", type=int, default=0)
    parser.add_argument("--posterior-trials", type=int, default=512)
    parser.add_argument("--posterior-seed", type=int, default=0)
    parser.add_argument("--min-sessions", type=int, default=5)
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    payload = build_payload(
        paths=args.paths,
        prior_trials=args.prior_trials,
        prior_seed=args.prior_seed,
        posterior_trials=args.posterior_trials,
        posterior_seed=args.posterior_seed,
        min_sessions=args.min_sessions,
    )
    _print_summary(payload, top=args.top)
    if args.dry_run:
        return 1 if payload.get("parse_errors") else 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    print(f"wrote={args.output}")
    return 1 if payload.get("parse_errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
