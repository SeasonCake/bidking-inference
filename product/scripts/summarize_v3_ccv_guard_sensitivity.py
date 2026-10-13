"""Compare v3 CCV tail-guard variants on the same archive windows."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", newline="")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", newline="")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from bidking_lab.inference.v3 import V3CcvOptions  # noqa: E402
from evaluate_fatbeans_v3_samples import (  # noqa: E402
    _default_calibration_path,
    _default_paths,
    _float_or_none,
    _round_metric,
    evaluate_paths,
    load_monitor_tables,
    load_prior_calibration_entries,
    summarize_rows,
)
from summarize_v3_ccv_layer_audit import (  # noqa: E402
    DEFAULT_GROUP_FIELDS,
    summarize_layers,
)


def _paired_ccv_rows(
    default_rows: Iterable[dict[str, Any]],
    alternative_rows: Iterable[dict[str, Any]],
) -> tuple[tuple[dict[str, Any], dict[str, Any]], ...]:
    default_by_file = {
        str(row.get("file")): row
        for row in default_rows
        if row.get("status") == "ready" and row.get("v3_truth_available")
    }
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for alternative in alternative_rows:
        key = str(alternative.get("file"))
        default = default_by_file.get(key)
        if default is None:
            continue
        if alternative.get("status") != "ready":
            continue
        if not default.get("v3_ccv_ready") or not alternative.get("v3_ccv_ready"):
            continue
        pairs.append((default, alternative))
    return tuple(pairs)


def _component_metrics(
    pairs: Iterable[tuple[dict[str, Any], dict[str, Any]]],
    *,
    component: str,
    truth_key: str,
) -> dict[str, Any]:
    rows: list[dict[str, float]] = []
    for default, alternative in pairs:
        truth = _float_or_none(default.get(truth_key))
        default_p50 = _float_or_none(default.get(f"v3_ccv_{component}_p50"))
        alternative_p50 = _float_or_none(alternative.get(f"v3_ccv_{component}_p50"))
        default_p90 = _float_or_none(default.get(f"v3_ccv_{component}_p90"))
        alternative_p90 = _float_or_none(alternative.get(f"v3_ccv_{component}_p90"))
        if truth is None or default_p50 is None or alternative_p50 is None:
            continue
        rows.append(
            {
                "truth": truth,
                "default_p50": default_p50,
                "alternative_p50": alternative_p50,
                "default_p90": default_p90 if default_p90 is not None else default_p50,
                "alternative_p90": (
                    alternative_p90 if alternative_p90 is not None else alternative_p50
                ),
            }
        )
    if not rows:
        return {
            f"{component}_rows": 0,
            f"{component}_changed_rows": 0,
            f"{component}_prediction_delta_mean": None,
            f"{component}_mae_delta": None,
            f"{component}_below_delta": None,
            f"{component}_p90_coverage_delta": None,
        }
    changed_rows = sum(
        1
        for row in rows
        if abs(row["alternative_p50"] - row["default_p50"]) > 1e-9
    )
    default_mae = sum(abs(row["default_p50"] - row["truth"]) for row in rows) / len(rows)
    alternative_mae = sum(
        abs(row["alternative_p50"] - row["truth"]) for row in rows
    ) / len(rows)
    default_below = sum(
        1.0 if row["default_p50"] < row["truth"] else 0.0 for row in rows
    ) / len(rows)
    alternative_below = sum(
        1.0 if row["alternative_p50"] < row["truth"] else 0.0 for row in rows
    ) / len(rows)
    default_p90_cover = sum(
        1.0 if row["truth"] <= row["default_p90"] else 0.0 for row in rows
    ) / len(rows)
    alternative_p90_cover = sum(
        1.0 if row["truth"] <= row["alternative_p90"] else 0.0 for row in rows
    ) / len(rows)
    return {
        f"{component}_rows": len(rows),
        f"{component}_changed_rows": changed_rows,
        f"{component}_prediction_delta_mean": _round_metric(
            sum(row["alternative_p50"] - row["default_p50"] for row in rows)
            / len(rows),
            3,
        ),
        f"{component}_mae_delta": _round_metric(alternative_mae - default_mae, 3),
        f"{component}_below_delta": _round_metric(alternative_below - default_below, 6),
        f"{component}_p90_coverage_delta": _round_metric(
            alternative_p90_cover - default_p90_cover,
            6,
        ),
    }


def _paired_diff(
    default_rows: Iterable[dict[str, Any]],
    alternative_rows: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    pairs = _paired_ccv_rows(default_rows, alternative_rows)
    out: dict[str, Any] = {"paired_rows": len(pairs)}
    out.update(
        _component_metrics(
            pairs,
            component="q6_count",
            truth_key="v3_truth_q6_count",
        )
    )
    out.update(
        _component_metrics(
            pairs,
            component="q6_cells",
            truth_key="v3_truth_q6_cells",
        )
    )
    return out


def summarize_sensitivity(
    default_rows: list[dict[str, Any]],
    default_errors: list[dict[str, str]],
    alternative_rows: list[dict[str, Any]],
    alternative_errors: list[dict[str, str]],
    *,
    alternative_options: V3CcvOptions,
    group_fields: Iterable[str] = DEFAULT_GROUP_FIELDS,
    folds: int = 5,
    min_windows: int = 20,
    min_sessions: int = 8,
    min_ccv_likelihood_rate: float = 0.20,
) -> dict[str, Any]:
    fields = tuple(group_fields)
    return {
        "alternative_options": {
            "count_cell_tail_guard": bool(alternative_options.count_cell_tail_guard),
            "value_tail_guard": bool(alternative_options.value_tail_guard),
            "condition_temperature": alternative_options.condition_temperature,
            "relative_floor": alternative_options.relative_floor,
        },
        "default_errors": default_errors,
        "alternative_errors": alternative_errors,
        "default_summary": summarize_rows(default_rows, default_errors),
        "alternative_summary": summarize_rows(alternative_rows, alternative_errors),
        "paired_diff": _paired_diff(default_rows, alternative_rows),
        "default_layers": summarize_layers(
            default_rows,
            group_fields=fields,
            folds=folds,
            min_windows=min_windows,
            min_sessions=min_sessions,
            min_ccv_likelihood_rate=min_ccv_likelihood_rate,
        ),
        "alternative_layers": summarize_layers(
            alternative_rows,
            group_fields=fields,
            folds=folds,
            min_windows=min_windows,
            min_sessions=min_sessions,
            min_ccv_likelihood_rate=min_ccv_likelihood_rate,
        ),
    }


def _layer_by_field(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row.get("group_field")): row for row in result.get("layers", ())}


def _print_summary(result: dict[str, Any]) -> None:
    default = result["default_summary"]
    alternative = result["alternative_summary"]
    diff = result["paired_diff"]
    print(
        " ".join(
            (
                "default",
                f"ccv_likelihood_rows={default['v3_ccv_likelihood_rows']}",
                f"count_delta={default['v3_ccv_delta_q6_count_p50_mae']}",
                f"cells_delta={default['v3_ccv_delta_q6_cells_p50_mae']}",
                f"count_mae={default['v3_ccv_q6_count_p50_mae']}",
                f"cells_mae={default['v3_ccv_q6_cells_p50_mae']}",
            )
        )
    )
    print(
        " ".join(
            (
                "alternative",
                "count_cell_tail_guard="
                + ("on" if result["alternative_options"]["count_cell_tail_guard"] else "off"),
                f"ccv_likelihood_rows={alternative['v3_ccv_likelihood_rows']}",
                f"count_delta={alternative['v3_ccv_delta_q6_count_p50_mae']}",
                f"cells_delta={alternative['v3_ccv_delta_q6_cells_p50_mae']}",
                f"count_mae={alternative['v3_ccv_q6_count_p50_mae']}",
                f"cells_mae={alternative['v3_ccv_q6_cells_p50_mae']}",
            )
        )
    )
    print(
        " ".join(
            (
                "paired_diff",
                f"rows={diff['paired_rows']}",
                f"count_changed={diff['q6_count_changed_rows']}",
                f"count_pred_delta={diff['q6_count_prediction_delta_mean']}",
                f"count_mae_delta={diff['q6_count_mae_delta']}",
                f"count_below_delta={diff['q6_count_below_delta']}",
                f"count_p90_cover_delta={diff['q6_count_p90_coverage_delta']}",
                f"cells_changed={diff['q6_cells_changed_rows']}",
                f"cells_pred_delta={diff['q6_cells_prediction_delta_mean']}",
                f"cells_mae_delta={diff['q6_cells_mae_delta']}",
                f"cells_below_delta={diff['q6_cells_below_delta']}",
                f"cells_p90_cover_delta={diff['q6_cells_p90_coverage_delta']}",
            )
        )
    )
    print(
        " ".join(
            (
                "layers",
                f"default_status={result['default_layers']['overall_status']}",
                f"alternative_status={result['alternative_layers']['overall_status']}",
            )
        )
    )
    alternative_layers = _layer_by_field(result["alternative_layers"])
    for default_layer in result["default_layers"].get("layers", ()):
        field = str(default_layer.get("group_field"))
        alternative_layer = alternative_layers.get(field, {})
        print(
            " ".join(
                (
                    f"group_field={field}",
                    f"default_status={default_layer.get('status')}",
                    f"alternative_status={alternative_layer.get('status')}",
                    "default_hurts="
                    + ",".join(default_layer.get("applied_ccv_hurts_groups") or ()),
                    "alternative_hurts="
                    + ",".join(alternative_layer.get("applied_ccv_hurts_groups") or ()),
                    f"alternative_rows={alternative_layer.get('candidate_rows')}",
                    f"alternative_cells_delta={alternative_layer.get('candidate_delta_q6_cells_p50_mae')}",
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare v3 CCV tail-guard variants on archive samples.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan. Defaults to data/samples/fatbeans.",
    )
    parser.add_argument(
        "--group-field",
        action="append",
        dest="group_fields",
        help="Grouping field to audit. Can be passed more than once.",
    )
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--min-windows", type=int, default=20)
    parser.add_argument("--min-sessions", type=int, default=8)
    parser.add_argument("--min-ccv-likelihood-rate", type=float, default=0.20)
    parser.add_argument("--posterior-trials", type=int, default=512)
    parser.add_argument("--posterior-seed", type=int, default=0)
    parser.add_argument(
        "--alt-count-cell-tail-guard",
        choices=("on", "off"),
        default="off",
    )
    parser.add_argument(
        "--alt-value-tail-guard",
        choices=("on", "off"),
        default="on",
    )
    parser.add_argument("--alt-condition-temperature", type=float, default=None)
    parser.add_argument("--alt-relative-floor", type=float, default=None)
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    args = parser.parse_args(argv)

    paths = args.paths or _default_paths()
    tables = load_monitor_tables()
    calibration_entries = load_prior_calibration_entries(_default_calibration_path())
    default_rows, default_errors = evaluate_paths(
        paths,
        tables=tables,
        calibration_entries=calibration_entries,
        posterior_trials=args.posterior_trials,
        posterior_seed=args.posterior_seed,
    )
    alternative_options = V3CcvOptions(
        count_cell_tail_guard=args.alt_count_cell_tail_guard == "on",
        value_tail_guard=args.alt_value_tail_guard == "on",
        condition_temperature=args.alt_condition_temperature,
        relative_floor=args.alt_relative_floor,
    )
    alternative_rows, alternative_errors = evaluate_paths(
        paths,
        tables=tables,
        calibration_entries=calibration_entries,
        posterior_trials=args.posterior_trials,
        posterior_seed=args.posterior_seed,
        ccv_options=alternative_options,
    )
    result = summarize_sensitivity(
        default_rows,
        default_errors,
        alternative_rows,
        alternative_errors,
        alternative_options=alternative_options,
        group_fields=args.group_fields or DEFAULT_GROUP_FIELDS,
        folds=args.folds,
        min_windows=args.min_windows,
        min_sessions=args.min_sessions,
        min_ccv_likelihood_rate=args.min_ccv_likelihood_rate,
    )
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if default_errors:
            print(f"default_errors={len(default_errors)}")
        if alternative_errors:
            print(f"alternative_errors={len(alternative_errors)}")
        _print_summary(result)
    return 1 if default_errors or alternative_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
