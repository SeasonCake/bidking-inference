"""Rebuild historical CSVs/figures; never edits the frozen source tables.

Run from the checkout: python -m research.historical_data.generate_charts
Only figure generation requires matplotlib; tabulation uses the standard library.
Provenance: historical-data-atlas in docs/research/PROVENANCE.json.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "legacy/data-v0.2.7-hotfix3/data/processed"
FILES = (
    "battle_items.json", "dropmap_mem_0625.json", "heroes.json",
    "item_category_mem_0625.json", "items_droppable.json", "maps.json",
    "quality_weights_measured.json",
)
QUALITIES = ("q1", "q3", "q4", "q5", "q6")
COLORS = ("#8c9aa9", "#3984bf", "#8862b7", "#cf9624", "#cc5654")


def quality_rows(data: dict) -> list[dict]:
    """Keep the historical merged q1+q2 bucket; sessions are not item draws."""
    if set(data["by_tier"]) != set(data["tier_sessions"]):
        raise ValueError("quality groups and session coverage differ")
    rows = []
    for tier, weights in sorted(data["by_tier"].items()):
        n = data["tier_sessions"][tier]
        if type(n) is not int or n <= 0 or set(weights) != set(QUALITIES):
            raise ValueError("invalid historical coverage or quality keys")
        values = list(weights.values())
        if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in values):
            raise ValueError("invalid historical weight")
        if not math.isclose(sum(values), 1, abs_tol=1e-12):
            raise ValueError("historical weights must sum to one")
        for quality in QUALITIES:
            rows.append({"kind": "historical_aggregate", "tier": tier,
                         "quality": "q1+q2" if quality == "q1" else quality,
                         "weight": weights[quality], "session_count": n})
    return rows


def catalog_rows(items: list[dict]) -> list[dict]:
    """Count catalog records, not drop frequency, probability or item value."""
    counts = Counter(item["quality"] for item in items)
    if any(type(q) is not int or q not in range(1, 7) for q in counts):
        raise ValueError("unexpected catalog quality")
    ids = [item["item_id"] for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate catalog item identity")
    return [{"kind": "historical_catalog_summary", "quality": q,
             "catalog_records": counts[q]} for q in range(1, 7)]


def tables() -> dict[str, list[dict]]:
    inventory = []
    loaded = {}
    for name in FILES:
        payload = (DATA / name).read_bytes()
        value = json.loads(payload)
        loaded[name] = value
        inventory.append({"file": name, "bytes": len(payload),
                          "sha256": hashlib.sha256(payload).hexdigest(),
                          "top_level_type": type(value).__name__,
                          "top_level_entries": len(value)})
    summary = json.loads((ROOT / "research/aisha/fixtures/historical_summary.json").read_text(encoding="utf-8"))
    return {"snapshot_inventory": inventory,
            "quality_weights": quality_rows(loaded["quality_weights_measured.json"]),
            "catalog_coverage": catalog_rows(loaded["items_droppable.json"]),
            "aisha_historical_metrics": aisha_rows(summary)}


def aisha_rows(summary: dict) -> list[dict]:
    """Reformat approved old summary fields; do not rerun or invent observations."""
    if summary.get("kind") != "historical_aggregate":
        raise ValueError("Aisha input must be the approved historical aggregate")
    if summary.get("cases") != 80 or summary.get("value_cases") != 79:
        raise ValueError("unexpected historical Aisha denominator")
    rows = []
    for arm in ("real", "synth"):
        for metric in ("median_bias_pct", "mean_bias_pct", "mae_pct"):
            value = summary[arm][metric]
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("invalid historical Aisha metric")
            rows.append({"kind": "historical_aggregate", "arm": arm,
                         "metric": metric, "value_pct": value, "value_cases": 79})
    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def plots(output: Path, records: dict[str, list[dict]]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "axes.titleweight": "bold", "svg.hashsalt": "bidking-historical-v1"})

    def finish(fig, name, footnote):
        fig.text(0.08, 0.025, footnote, fontsize=9, color="#506071")
        fig.subplots_adjust(left=.09, right=.96, top=.83, bottom=.19)
        fig.savefig(output / f"{name}.png", dpi=150, metadata={"Software": None})
        plt.close(fig)

    rows = records["quality_weights"]
    groups = sorted({row["tier"] for row in rows})
    fig, ax = plt.subplots(figsize=(10, 5.4))
    left = [0.] * len(groups)
    for quality, color in zip(("q1+q2", "q3", "q4", "q5", "q6"), COLORS):
        values = [next(r["weight"] for r in rows if r["tier"] == g and r["quality"] == quality) for g in groups]
        ax.barh(groups, values, left=left, label=quality, color=color, height=.58)
        for i, value in enumerate(values):
            if value >= .075:
                ax.text(left[i] + value / 2, i, f"{value:.1%}", ha="center", va="center", color="white", fontsize=10)
        left = [a+b for a, b in zip(left, values)]
    ax.set(xlim=(0, 1), xlabel="Recorded aggregate weight", ylabel="Historical tier group")
    ax.xaxis.set_major_formatter(PercentFormatter(1))
    ax.set_title("Historical quality mix", loc="left", pad=35)
    ax.legend(ncol=5, loc="lower left", bbox_to_anchor=(0, 1.01), frameon=False, borderaxespad=0)
    finish(fig, "quality-mix", "Frozen v0.2.7-hotfix3 snapshot | q1 includes q2 | Not current drop probabilities")

    fig, ax = plt.subplots(figsize=(10, 5.4))
    counts = [next(r["session_count"] for r in rows if r["tier"] == g) for g in groups]
    bars = ax.barh(groups, counts, color="#327e93", height=.58)
    ax.bar_label(bars, padding=7)
    ax.set(xlim=(0, max(counts)*1.15), xlabel="Recorded sessions (not independent item draws)", ylabel="Historical tier group")
    ax.set_title("Uneven historical coverage", loc="left", pad=20)
    finish(fig, "session-coverage", "8 / 222 / 471 / 143 / 189 sessions | A zero recorded weight does not prove impossibility")

    fig, ax = plt.subplots(figsize=(10, 5.4))
    catalog = records["catalog_coverage"]
    counts = [r["catalog_records"] for r in catalog]
    bars = ax.bar([f"q{r['quality']}" for r in catalog], counts,
                  color=["#8c9aa9", "#479b7b", *COLORS[1:]], width=.6)
    ax.bar_label(bars, padding=5)
    ax.set(ylim=(0, max(counts)*1.2), ylabel="Catalog records", xlabel="Historical quality category")
    ax.set_title("What is in the frozen item catalog?", loc="left", pad=20)
    finish(fig, "catalog-coverage", "One row per catalog item ID | Catalog composition is not observed loot frequency")

    fig, ax = plt.subplots(figsize=(10, 5.4))
    metrics = ("median_bias_pct", "mean_bias_pct", "mae_pct")
    for arm, shift, color in (("real", -.19, "#327e93"), ("synth", .19, "#d89535")):
        values = [next(r["value_pct"] for r in records["aisha_historical_metrics"]
                       if r["arm"] == arm and r["metric"] == metric) for metric in metrics]
        bars = ax.bar([i+shift for i in range(3)], values, width=.36, label=arm, color=color)
        ax.bar_label(bars, labels=[f"{v:.1f}%" for v in values], padding=4)
    ax.axhline(0, color="#8c9aa9", linewidth=.8)
    ax.set(xticks=range(3), xticklabels=["Median bias", "Mean bias", "Mean absolute error"],
           ylim=(-9, 65), ylabel="Percent error against historical truth")
    ax.set_title("Similar summaries do not imply faithful replay", loc="left", pad=25)
    ax.legend(frameon=False, loc="upper left")
    finish(fig, "aisha-real-synthetic", "Historical 2026-06-27 summary | 80 cases; 79 value cases | Not rerun; no reconstructed intervals")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/historical-data")
    parser.add_argument("--csv-only", action="store_true", help="No optional plotting dependency")
    args = parser.parse_args()
    records = tables()
    args.output.mkdir(parents=True, exist_ok=True)
    for name, rows in records.items():
        write_csv(args.output / f"{name}.csv", rows)
    if not args.csv_only:
        plots(args.output, records)
    print(json.dumps({"result": "PASS", "csv_tables": len(records),
                      "figures": 0 if args.csv_only else 4,
                      "kind": "historical_derivative"}))


if __name__ == "__main__":
    main()
