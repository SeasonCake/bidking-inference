from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ExampleTest(unittest.TestCase):
    def run_example(self, name: str) -> dict[str, object]:
        result = subprocess.run(
            [sys.executable, str(ROOT / "examples" / name)],
            cwd=ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return json.loads(result.stdout)

    def test_constraint_filter(self) -> None:
        document = self.run_example("constraint_filter.py")
        self.assertEqual(document["accepted"], ["balanced", "upper-edge"])
        self.assertIn("total_count_outside_interval", document["rejected"]["too-small"])

    def test_posterior_summary(self) -> None:
        document = self.run_example("posterior_summary.py")
        self.assertAlmostEqual(sum(document["posterior"].values()), 1.0)
        self.assertEqual(document["rejected"], ["outside"])

    def test_monte_carlo_summary_is_deterministic(self) -> None:
        first = self.run_example("monte_carlo_summary.py")
        second = self.run_example("monte_carlo_summary.py")
        self.assertEqual(first, second)
        self.assertEqual(first["summary"]["seed"], 17)


if __name__ == "__main__":
    unittest.main()
