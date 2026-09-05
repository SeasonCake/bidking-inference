#!/usr/bin/env python3
"""Synthetic example: qualify an observation before constructing a posterior."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from auction_inference import (  # noqa: E402
    Candidate,
    EvidenceTerm,
    ExactObservation,
    IntervalObservation,
    infer_weighted_posterior,
)

# A deliberately small, invented population. No external data is loaded.
CAPACITY = 6
CANDIDATES = (
    Candidate("few", {"blue_count": 1, "total_count": CAPACITY}, 0.25),
    Candidate("observed", {"blue_count": 2, "total_count": CAPACITY}, 0.50),
    Candidate("more", {"blue_count": 3, "total_count": CAPACITY}, 0.25),
)


def _identity(value: object, name: str, *, unknown_allowed: bool = False) -> None:
    if unknown_allowed and value is None:
        return
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


@dataclass(frozen=True)
class Snapshot:
    session: str
    revision: str | None
    complete: bool
    seen_blue: int | None

    def __post_init__(self) -> None:
        _identity(self.session, "session")
        _identity(self.revision, "revision", unknown_allowed=True)
        if type(self.complete) is not bool:
            raise TypeError("complete must be a boolean")
        if self.seen_blue is None:
            if self.complete:
                raise ValueError("a complete observation must provide its count")
        elif type(self.seen_blue) is not int or not 0 <= self.seen_blue <= CAPACITY:
            raise ValueError("seen_blue must be an integer within the synthetic capacity")


def infer_snapshot(
    snapshot: Snapshot, *, current_session: str, current_revision: str | None
) -> dict[str, object]:
    """Return a replacement view, never relabel or reuse a previous posterior."""
    _identity(current_session, "current_session")
    _identity(current_revision, "current_revision", unknown_allowed=True)
    if snapshot.session != current_session:
        reason = "session_mismatch"
    elif snapshot.revision is None or current_revision is None:
        reason = "revision_unknown"
    elif snapshot.revision != current_revision:
        reason = "revision_mismatch"
    else:
        reason = None
    if reason:
        return {"status": "withheld", "reason": reason, "posterior": {}}

    terms = [EvidenceTerm("total_count", ExactObservation(CAPACITY))]
    if snapshot.seen_blue is not None:
        observation = (
            ExactObservation(snapshot.seen_blue)
            if snapshot.complete
            else IntervalObservation(snapshot.seen_blue, CAPACITY)
        )
        terms.append(EvidenceTerm("blue_count", observation))
    summary = infer_weighted_posterior(CANDIDATES, terms)
    return {
        "status": "ready",
        "reason": None,
        "posterior": {row.label: row.probability for row in summary.rows},
    }


def main() -> int:
    context = {"current_session": "run-a", "current_revision": "revision-b"}
    cases = {
        "complete": Snapshot("run-a", "revision-b", True, 2),
        "partial": Snapshot("run-a", "revision-b", False, 2),
        "missing": Snapshot("run-a", "revision-b", False, None),
        "stale_revision": Snapshot("run-a", "revision-a", True, 2),
        "unknown_revision": Snapshot("run-a", None, True, 2),
        "wrong_session": Snapshot("run-old", "revision-b", True, 2),
    }
    print(json.dumps({
        "synthetic": True,
        "cases": {name: infer_snapshot(value, **context) for name, value in cases.items()},
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
