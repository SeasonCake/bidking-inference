"""Print a reproducible synthetic missing/null/zero comparison and epoch replay."""

from dataclasses import asdict
import json

from .compare import compare
from .events import EpochConsumer


def main() -> None:
    left = {"bucket": {"count": 0}, "round": 1}
    right = {"bucket": {"count": None, "cells": 0}, "round": 1}
    consumer = EpochConsumer(2)

    def event(epoch, sequence, facts):
        return dict(
            schema_version=1,
            producer_id="synthetic",
            epoch=epoch,
            sequence=sequence,
            facts=facts,
        )

    statuses = [
        consumer.accept(item)
        for item in [
            event(2, 1, {"count": 0}),
            event(2, 1, {"count": 0}),
            event(1, 9, {"count": 8}),
            event(2, 2, {}),
        ]
    ]
    print(
        json.dumps(
            {
                "synthetic": True,
                "differences": [asdict(d) for d in compare(left, right)],
                "statuses": statuses,
                "final_facts": consumer.facts,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
