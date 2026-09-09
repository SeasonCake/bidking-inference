"""Independent nonlinear penalty teaching example, NOT the BidKing quote model.

All demo inputs and parameters are fictional. The support score is an illustrative
input in [0, 1], not a probability, calibrated confidence, or game-field mapping.
Run: python -m research.aisha.penalty_example
Optional CSV/plot: add --output outputs/penalty-teaching (matplotlib for the plot).
Provenance: docs/research/PROVENANCE.json, nonlinear-penalty-teaching.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path


def _number(value: object, name: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(name + " must be a finite number, not bool or text")
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(name + " must be finite") from exc
    if not math.isfinite(number):
        raise ValueError(name + " must be finite")
    return number


def penalty_trace(base_quote: float, support_score: float, *, strength: float, exponent: float) -> dict:
    """Return every arithmetic step of a generic illustrative penalty.

    Parameters have no production defaults. Callers must choose them explicitly.
    This function neither estimates support from observations nor evaluates accuracy.
    """
    base_quote = _number(base_quote, "base_quote")
    support_score = _number(support_score, "support_score")
    strength = _number(strength, "strength")
    exponent = _number(exponent, "exponent")
    if base_quote < 0 or not 0 <= support_score <= 1 or not 0 <= strength <= 1 or exponent <= 0:
        raise ValueError("require quote>=0, score/strength in [0,1], exponent>0")
    gap = 1 - support_score
    curved_gap = gap ** exponent
    penalty_fraction = strength * curved_gap
    multiplier = 1 - penalty_fraction
    return {"base_quote": base_quote, "support_score": support_score,
            "strength": strength, "exponent": exponent, "gap": gap,
            "curved_gap": curved_gap, "penalty_fraction": penalty_fraction,
            "multiplier": multiplier, "penalized_quote": base_quote * multiplier}


def demo() -> dict:
    return {"synthetic": True, "kind": "nonlinear_penalty_teaching",
            "not_product_model": True, "unit": "arbitrary_teaching_money",
            "parameter_origin": "Invented for this example; not fitted to any game or real sample",
            "cases": [penalty_trace(1000, score, strength=.4, exponent=2)
                      for score in (0, .25, .5, .75, 1)]}


def write_csv(path: Path, cases: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["synthetic", *cases[0]], lineterminator="\n")
        writer.writeheader()
        writer.writerows({"synthetic": True, **case} for case in cases)


def plot(path: Path, cases: list[dict]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    params = {key: cases[0][key] for key in ("base_quote", "strength", "exponent")}
    scores = [i / 100 for i in range(101)]
    outputs = [penalty_trace(support_score=score, **params)["penalized_quote"] for score in scores]
    with plt.rc_context({"font.family": "DejaVu Sans", "font.size": 11,
                         "axes.spines.top": False, "axes.spines.right": False}):
        fig, ax = plt.subplots(figsize=(10, 5.4))
        ax.plot(scores, outputs, color="#327e93", linewidth=2.5)
        ax.scatter([c["support_score"] for c in cases], [c["penalized_quote"] for c in cases],
                   color="#d89535", zorder=3)
        for case in cases:
            ax.annotate(f"{case['penalized_quote']:.0f}",
                        (case["support_score"], case["penalized_quote"]),
                        xytext=(0, 12), textcoords="offset points", ha="center")
        ax.set(xlim=(-.05, 1.05), ylim=(500, 1100),
               xlabel="Teaching support score (not a probability)",
               ylabel="Output in arbitrary teaching money",
               title="Nonlinear penalty: executable example, fictional parameters")
        ax.grid(axis="y", alpha=.18)
        fig.subplots_adjust(left=.1, right=.96, top=.87, bottom=.2)
        fig.text(.08, .035, "base=1000 | strength=0.4 | exponent=2 | Synthetic input; not the product model", fontsize=9, color="#506071")
        fig.savefig(path, dpi=150, metadata={"Software": None})
        plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="write CSV and optional plot to this directory")
    parser.add_argument("--csv-only", action="store_true", help="skip optional matplotlib plot")
    args = parser.parse_args()
    if args.csv_only and args.output is None:
        parser.error("--csv-only requires --output")
    result = demo()
    if args.output is not None:
        args.output.mkdir(parents=True, exist_ok=True)
        write_csv(args.output / "penalty_teaching_trace.csv", result["cases"])
        if not args.csv_only:
            plot(args.output / "penalty-teaching.png", result["cases"])
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
