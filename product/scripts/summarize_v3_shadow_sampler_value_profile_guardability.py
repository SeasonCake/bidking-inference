"""Assess whether q6_value map-only details contain separable profile guards."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", newline="")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", newline="")

INTERFACE = "v3_ccvc_q6_value_profile_guardability"
COMPONENT = "q6_value"
DEFAULT_DIMENSIONS = (
    "evidence_profile_key",
    "profile_semantic_class",
    "profile_source_class",
    "profile_anchor_class",
    "map_id,evidence_profile_key",
    "map_id,profile_semantic_class",
)


def _counter_dict(values: Iterable[Any]) -> dict[str, int]:
    return dict(sorted(Counter(str(value) for value in values).items()))


def _as_list(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _group_value(row: Mapping[str, Any], dimension: str) -> str:
    parts = tuple(part.strip() for part in str(dimension).split(",") if part.strip())
    if len(parts) > 1:
        return "|".join(f"{part}={_group_value(row, part)}" for part in parts)
    value = row.get(dimension)
    return str(value) if value not in (None, "") else "unknown"


def _flatten_detail_rows(details: Mapping[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for label in _as_list(details.get("labels")):
        watch_label = str(label.get("watch_label") or "")
        for row in _as_list(label.get("detail_rows")):
            item = dict(row)
            item.update(
                {
                    "watch_label": watch_label,
                    "label_group": label.get("group"),
                    "label_movement_policy": label.get("movement_policy"),
                    "posterior_seed": label.get("posterior_seed"),
                }
            )
            out.append(item)
    return out


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def _cluster_status(
    *,
    rows: int,
    sessions: int,
    labels: int,
    maps: int,
    hurt_rate: float | None,
    helped_rate: float | None,
    min_rows: int,
    min_sessions: int,
    min_labels: int,
    min_maps: int,
    min_hurt_rate: float,
    max_helped_rate: float,
) -> str:
    if rows < min_rows or sessions < min_sessions:
        return "sample_limited"
    if hurt_rate is None or helped_rate is None:
        return "sample_limited"
    if hurt_rate >= min_hurt_rate and helped_rate <= max_helped_rate:
        if labels >= min_labels and maps >= min_maps:
            return "profile_guard_candidate_needs_holdout"
        return "overfit_risk_single_label_or_map"
    if hurt_rate >= min_hurt_rate and helped_rate > max_helped_rate:
        return "mixed_hurt_helped"
    return "not_hurt_dominant"


def _summarize_cluster(
    rows: Iterable[Mapping[str, Any]],
    *,
    dimension: str,
    group: str,
    min_rows: int,
    min_sessions: int,
    min_labels: int,
    min_maps: int,
    min_hurt_rate: float,
    max_helped_rate: float,
) -> dict[str, Any]:
    seq = tuple(rows)
    effects = Counter(str(row.get("effect") or "unknown") for row in seq)
    sessions = {str(row.get("session_id") or "") for row in seq}
    labels = {str(row.get("watch_label") or "") for row in seq}
    maps = {str(row.get("map_id") or "") for row in seq}
    seeds = {str(row.get("posterior_seed") or "") for row in seq}
    hurt_rows = effects.get("hurt", 0)
    helped_rows = effects.get("helped", 0)
    directional_error_rows = sum(1 for row in seq if row.get("directional_error"))
    hurt_rate = _rate(hurt_rows, len(seq))
    helped_rate = _rate(helped_rows, len(seq))
    status = _cluster_status(
        rows=len(seq),
        sessions=len(sessions),
        labels=len(labels),
        maps=len(maps),
        hurt_rate=hurt_rate,
        helped_rate=helped_rate,
        min_rows=min_rows,
        min_sessions=min_sessions,
        min_labels=min_labels,
        min_maps=min_maps,
        min_hurt_rate=min_hurt_rate,
        max_helped_rate=max_helped_rate,
    )
    return {
        "dimension": dimension,
        "group": group,
        "status": status,
        "rows": len(seq),
        "sessions": len(sessions),
        "labels": len(labels),
        "maps": len(maps),
        "seeds": len(seeds),
        "effect_counts": dict(sorted(effects.items())),
        "hurt_rows": hurt_rows,
        "helped_rows": helped_rows,
        "hurt_rate": hurt_rate,
        "helped_rate": helped_rate,
        "directional_error_rows": directional_error_rows,
        "directional_error_rate": _rate(directional_error_rows, len(seq)),
        "watch_label_counts": _counter_dict(row.get("watch_label") for row in seq),
        "map_counts": _counter_dict(row.get("map_id") for row in seq),
        "evidence_profile_counts": _counter_dict(
            row.get("evidence_profile_key") for row in seq
        ),
        "profile_semantic_class_counts": _counter_dict(
            row.get("profile_semantic_class") for row in seq
        ),
        "profile_source_class_counts": _counter_dict(
            row.get("profile_source_class") for row in seq
        ),
        "profile_anchor_class_counts": _counter_dict(
            row.get("profile_anchor_class") for row in seq
        ),
    }


def summarize_guardability(
    details: Mapping[str, Any],
    *,
    dimensions: Iterable[str] = DEFAULT_DIMENSIONS,
    min_rows: int = 12,
    min_sessions: int = 4,
    min_labels: int = 2,
    min_maps: int = 2,
    min_hurt_rate: float = 0.65,
    max_helped_rate: float = 0.2,
    top: int = 12,
) -> dict[str, Any]:
    detail_rows = _flatten_detail_rows(details)
    clusters: list[dict[str, Any]] = []
    for dimension in dimensions:
        groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in detail_rows:
            groups[_group_value(row, dimension)].append(row)
        for group, rows in sorted(groups.items()):
            clusters.append(
                _summarize_cluster(
                    rows,
                    dimension=dimension,
                    group=group,
                    min_rows=min_rows,
                    min_sessions=min_sessions,
                    min_labels=min_labels,
                    min_maps=min_maps,
                    min_hurt_rate=min_hurt_rate,
                    max_helped_rate=max_helped_rate,
                )
            )
    clusters.sort(
        key=lambda row: (
            str(row.get("status") or ""),
            -float(row.get("hurt_rate") or 0.0),
            -int(row.get("rows") or 0),
            str(row.get("dimension") or ""),
            str(row.get("group") or ""),
        )
    )
    candidate_clusters = [
        row
        for row in clusters
        if row.get("status") == "profile_guard_candidate_needs_holdout"
    ]
    overfit_clusters = [
        row
        for row in clusters
        if row.get("status") == "overfit_risk_single_label_or_map"
    ][:top]
    mixed_clusters = [
        row for row in clusters if row.get("status") == "mixed_hurt_helped"
    ][:top]
    if candidate_clusters:
        status = "blocked_profile_guard_candidates_need_holdout"
        next_action = (
            "run source/profile guard candidates through session/map-family/seed "
            "holdout before sampler use"
        )
    else:
        status = "blocked_no_stable_profile_guard"
        next_action = (
            "keep q6_value inactive; current source/profile clusters do not "
            "separate hurt from helped rows"
        )
    return {
        "interface": INTERFACE,
        "status": status,
        "shadow_only": True,
        "affects_bid": False,
        "active": False,
        "can_promote": False,
        "component": COMPONENT,
        "source_details_status": details.get("status"),
        "source_label_count": details.get("label_count"),
        "source_candidate_rows": details.get("candidate_rows"),
        "detail_rows": len(detail_rows),
        "cluster_count": len(clusters),
        "candidate_cluster_count": len(candidate_clusters),
        "overfit_risk_cluster_count": len(overfit_clusters),
        "mixed_cluster_count": len(mixed_clusters),
        "thresholds": {
            "min_rows": min_rows,
            "min_sessions": min_sessions,
            "min_labels": min_labels,
            "min_maps": min_maps,
            "min_hurt_rate": min_hurt_rate,
            "max_helped_rate": max_helped_rate,
        },
        "candidate_clusters": candidate_clusters[:top],
        "overfit_risk_clusters": overfit_clusters,
        "mixed_clusters": mixed_clusters,
        "top_clusters": clusters[:top],
        "next_action": next_action,
        "blocked_actions": [
            "change formal bid path",
            "wire q6 value profile guard into live decisions",
            "promote source/profile guard without holdout",
            "archive v2 fallback",
            "relax readiness or promotion gates",
        ],
    }


def _print_summary(result: Mapping[str, Any], *, top: int) -> None:
    print(
        " ".join(
            (
                f"status={result.get('status')}",
                f"component={result.get('component')}",
                f"rows={result.get('detail_rows')}",
                f"clusters={result.get('cluster_count')}",
                f"candidates={result.get('candidate_cluster_count')}",
                f"overfit={result.get('overfit_risk_cluster_count')}",
                f"mixed={result.get('mixed_cluster_count')}",
                f"next_action=\"{result.get('next_action')}\"",
            )
        )
    )
    for row in (result.get("top_clusters") or ())[:top]:
        print(
            " ".join(
                (
                    f"cluster={row.get('dimension')}:{row.get('group')}",
                    f"status={row.get('status')}",
                    f"rows={row.get('rows')}",
                    f"sessions={row.get('sessions')}",
                    f"labels={row.get('labels')}",
                    f"maps={row.get('maps')}",
                    f"hurt_rate={row.get('hurt_rate')}",
                    f"helped_rate={row.get('helped_rate')}",
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize q6_value source/profile guardability.",
    )
    parser.add_argument("--details-json", type=Path, required=True)
    parser.add_argument("--dimension", action="append")
    parser.add_argument("--min-rows", type=int, default=12)
    parser.add_argument("--min-sessions", type=int, default=4)
    parser.add_argument("--min-labels", type=int, default=2)
    parser.add_argument("--min-maps", type=int, default=2)
    parser.add_argument("--min-hurt-rate", type=float, default=0.65)
    parser.add_argument("--max-helped-rate", type=float, default=0.2)
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    args = parser.parse_args(argv)

    with args.details_json.open("r", encoding="utf-8-sig") as handle:
        details = json.load(handle)
    result = summarize_guardability(
        details,
        dimensions=args.dimension or DEFAULT_DIMENSIONS,
        min_rows=args.min_rows,
        min_sessions=args.min_sessions,
        min_labels=args.min_labels,
        min_maps=args.min_maps,
        min_hurt_rate=args.min_hurt_rate,
        max_helped_rate=args.max_helped_rate,
        top=args.top,
    )
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_summary(result, top=args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
