"""Evaluate Fatbeans JSON samples for layout-depth fitting logs."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", newline="")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", newline="")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bidking_lab.live.evaluation import (  # noqa: E402
    evaluate_fatbeans_layout_path,
    fit_layout_estimate_policy,
    layout_policy_error_metrics,
    summarize_fatbeans_layout_evaluation,
)
from bidking_lab.live.layout import DEFAULT_LAYOUT_ESTIMATE_POLICY  # noqa: E402


def _write_csv(rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    writer = csv.DictWriter(
        sys.stdout,
        fieldnames=list(rows[0].keys()),
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)


def _write_jsonl(rows: list[dict[str, object]]) -> None:
    for row in rows:
        print(json.dumps(row, ensure_ascii=False, separators=(",", ":")))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate Fatbeans JSON captures for layout fitting.",
    )
    parser.add_argument("path", help="Fatbeans JSON file or directory")
    parser.add_argument(
        "--format",
        choices=("csv", "jsonl", "summary", "policy"),
        default="csv",
        help="Output format for evaluation rows, or summary only",
    )
    parser.add_argument(
        "--name-regex",
        default=None,
        help="Only include JSON files whose basename matches this regex",
    )
    args = parser.parse_args()

    result = evaluate_fatbeans_layout_path(
        args.path,
        name_regex=args.name_regex,
    )
    if args.format == "summary":
        print(
            json.dumps(
                summarize_fatbeans_layout_evaluation(result).as_dict(),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        for error in result.errors:
            print(f"[error] {error.file}: {error.message}", file=sys.stderr)
        return 1 if result.errors and not result.rows else 0
    if args.format == "policy":
        fit = fit_layout_estimate_policy(result)
        payload = fit.as_dict()
        payload["default_metrics"] = layout_policy_error_metrics(
            result,
            policy=DEFAULT_LAYOUT_ESTIMATE_POLICY,
        ).as_dict()
        payload["fitted_metrics"] = layout_policy_error_metrics(
            result,
            policy=fit.policy,
        ).as_dict()
        print(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        for error in result.errors:
            print(f"[error] {error.file}: {error.message}", file=sys.stderr)
        return 1 if result.errors and not result.rows else 0

    rows = [row.as_dict() for row in result.rows]
    if args.format == "jsonl":
        _write_jsonl(rows)
    else:
        _write_csv(rows)

    for error in result.errors:
        print(f"[error] {error.file}: {error.message}", file=sys.stderr)
    return 1 if result.errors and not rows else 0


if __name__ == "__main__":
    raise SystemExit(main())
