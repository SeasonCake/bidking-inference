"""Build v3 settlement count-prior shadow artifact from archive cohorts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", newline="")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", newline="")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from bidking_lab.inference.v3.settlement_count_prior import (  # noqa: E402
    entry_from_mapping,
)
from summarize_v3_settlement_count_prior_candidates import (  # noqa: E402
    summarize_settlement_count_prior_candidates,
)

DEFAULT_OUTPUT = ROOT / "data" / "processed" / "v3_settlement_count_prior_shadow.json"
DEFAULT_COHORTS = (
    (
        "default_archive",
        ROOT / "data" / "samples" / "fatbeans",
        10,
    ),
    (
        "activity_20260605_shipwreck",
        ROOT / "data" / "samples" / "fatbeans_activity_20260605_shipwreck",
        3,
    ),
)


def _entry_from_row(
    row: Mapping[str, Any],
    *,
    cohort: str,
) -> dict[str, Any]:
    payload = {
        **row,
        "scope": str(row.get("group_by") or "map_id"),
        "group": str(row.get("group")),
        "status": str(row.get("candidate_status") or "unscored"),
        "gate_reason": _gate_reason(row),
        "source": f"archive_settlement_count_prior_shadow:{cohort}",
    }
    return entry_from_mapping(payload).to_dict()


def _gate_reason(row: Mapping[str, Any]) -> str:
    status = str(row.get("candidate_status") or "")
    if status == "observed_exceeds_table_caps_shadow_only":
        return "observed_settlement_count_exceeds_current_table_caps"
    if status == "missing_table_shadow_only":
        return "missing_current_bidmap_for_activity_cohort"
    if status == "insufficient_samples_shadow_only":
        return "insufficient_archive_sessions"
    return "settlement_count_prior_shadow_only"


def build_artifact(
    cohorts: Iterable[tuple[str, Path, int]],
    *,
    group_by: str = "map_id",
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    cohort_summaries: list[dict[str, Any]] = []
    for label, path, min_samples in cohorts:
        if not path.exists():
            cohort_summaries.append(
                {
                    "label": label,
                    "path": str(path),
                    "status": "missing_path",
                }
            )
            continue
        result = summarize_settlement_count_prior_candidates(
            [path],
            group_by=group_by,
            min_samples=min_samples,
        )
        entries.extend(
            _entry_from_row(row, cohort=label)
            for row in result.get("rows", ())
        )
        cohort_summaries.append(
            {
                "label": label,
                "path": str(path),
                "status": "ok",
                "files": result.get("files"),
                "settlement_rows": result.get("settlement_rows"),
                "groups": len(result.get("rows", ())),
                "candidate_statuses": result.get("overall", {}).get(
                    "candidate_statuses",
                    {},
                ),
                "missing_table_rows": result.get("overall", {}).get(
                    "missing_table_rows",
                ),
            }
        )
    return {
        "affects_bid": False,
        "active": False,
        "generated_at": "2026-06-06",
        "source": "archive_settlement_count_prior_candidates",
        "group_by": group_by,
        "cohorts": cohort_summaries,
        "entries": entries,
    }


def _cohort_arg(value: str) -> tuple[str, Path, int]:
    parts = value.split(":", 2)
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("expected LABEL:PATH:MIN_SAMPLES")
    label, path, min_samples = parts
    return (label, Path(path), int(min_samples))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build v3 settlement count-prior shadow artifact.",
    )
    parser.add_argument(
        "--cohort",
        action="append",
        type=_cohort_arg,
        help="Cohort in LABEL:PATH:MIN_SAMPLES form. Defaults to archive + 0605 activity.",
    )
    parser.add_argument(
        "--group-by",
        choices=("map_id", "map_prefix3"),
        default="map_id",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    cohorts = tuple(args.cohort) if args.cohort else DEFAULT_COHORTS
    artifact = build_artifact(cohorts, group_by=args.group_by)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        " ".join(
            (
                f"output={args.output}",
                f"entries={len(artifact['entries'])}",
                f"cohorts={len(artifact['cohorts'])}",
                f"affects_bid={artifact['affects_bid']}",
                f"active={artifact['active']}",
            )
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
