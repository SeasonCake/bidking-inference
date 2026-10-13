"""Build v3 capacity/source expansion shadow artifact from archive cohorts."""

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

from bidking_lab.inference.v3.capacity_source_expansion import (  # noqa: E402
    entry_from_mapping,
)
from summarize_v3_settlement_source_semantics_audit import (  # noqa: E402
    summarize_settlement_source_semantics_audit,
)

DEFAULT_OUTPUT = ROOT / "data" / "processed" / "v3_capacity_source_expansion_shadow.json"
DEFAULT_COHORTS = (
    ("default_archive", ROOT / "data" / "samples" / "fatbeans"),
    ("activity_20260605_shipwreck", ROOT / "data" / "samples" / "fatbeans_activity_20260605_shipwreck"),
)
DEFAULT_GROUP_BYS = ("map_id", "map_family")


def _entry_from_row(
    row: Mapping[str, Any],
    *,
    cohort: str,
) -> dict[str, Any]:
    payload = {
        **row,
        "scope": str(row.get("group_by") or "map_id"),
        "group": str(row.get("group")),
        "source": f"archive_capacity_source_expansion_shadow:{cohort}",
    }
    return entry_from_mapping(payload).to_dict()


def build_artifact(
    cohorts: Iterable[tuple[str, Path]],
    *,
    group_bys: Iterable[str] = DEFAULT_GROUP_BYS,
) -> dict[str, Any]:
    entries_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    cohort_summaries: list[dict[str, Any]] = []
    overlay_metadata: dict[str, Any] | None = None
    for label, path in cohorts:
        if not path.exists():
            cohort_summaries.append(
                {
                    "label": label,
                    "path": str(path),
                    "status": "missing_path",
                }
            )
            continue
        for group_by in group_bys:
            result = summarize_settlement_source_semantics_audit(
                [path],
                group_by=group_by,
            )
            overlay_metadata = overlay_metadata or result.get("table_overlay_metadata")
            for row in result.get("rows", ()):
                entry = _entry_from_row(row, cohort=label)
                entries_by_key.setdefault(
                    (str(entry.get("scope")), str(entry.get("group"))),
                    entry,
                )
            cohort_summaries.append(
                {
                    "label": label,
                    "path": str(path),
                    "group_by": group_by,
                    "status": "ok",
                    "files": result.get("files"),
                    "settlement_rows": result.get("settlement_rows"),
                    "groups": len(result.get("rows", ())),
                    "source_evidence_classes": result.get("overall", {}).get(
                        "source_evidence_classes",
                        {},
                    ),
                    "source_context_classes": result.get("overall", {}).get(
                        "source_context_classes",
                        {},
                    ),
                    "mechanism_classes": result.get("overall", {}).get(
                        "mechanism_classes",
                        {},
                    ),
                    "unique_round_overflow_rows": result.get("overall", {}).get(
                        "unique_above_round_after_temp_zodiac_rows",
                    ),
                }
            )
    return {
        "affects_bid": False,
        "active": False,
        "generated_at": "2026-06-07",
        "source": "archive_settlement_source_semantics_audit",
        "group_bys": list(group_bys),
        "table_overlay_metadata": overlay_metadata or {},
        "cohorts": cohort_summaries,
        "entries": sorted(
            entries_by_key.values(),
            key=lambda row: (
                str(row.get("scope")),
                str(row.get("group")),
                str(row.get("source")),
            ),
        ),
    }


def _cohort_arg(value: str) -> tuple[str, Path]:
    parts = value.split(":", 1)
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("expected LABEL:PATH")
    label, path = parts
    return (label, Path(path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build v3 capacity/source expansion shadow artifact.",
    )
    parser.add_argument(
        "--cohort",
        action="append",
        type=_cohort_arg,
        help="Cohort in LABEL:PATH form. Defaults to archive + 0605 activity.",
    )
    parser.add_argument(
        "--group-by",
        action="append",
        choices=("map_id", "map_family", "session_token_prefix6"),
        help="Grouping to include. Defaults to map_id and map_family.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    cohorts = tuple(args.cohort) if args.cohort else DEFAULT_COHORTS
    group_bys = tuple(args.group_by) if args.group_by else DEFAULT_GROUP_BYS
    artifact = build_artifact(cohorts, group_bys=group_bys)
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
                f"group_bys={','.join(artifact['group_bys'])}",
                f"affects_bid={artifact['affects_bid']}",
                f"active={artifact['active']}",
            )
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
