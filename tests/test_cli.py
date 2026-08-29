from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from auction_inference.cli import evaluate_document


ROOT = Path(__file__).resolve().parents[1]


class CliTest(unittest.TestCase):
    def run_fixture(self, name: str) -> dict[str, object]:
        result = subprocess.run(
            [sys.executable, "-m", "auction_inference.cli", str(ROOT / "examples" / name)],
            cwd=ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")},
        )
        return json.loads(result.stdout)

    def test_documented_fixture_runs_without_private_inputs(self) -> None:
        document = self.run_fixture("synthetic_session.json")
        self.assertTrue(document["synthetic"])
        self.assertEqual([row["label"] for row in document["posterior"]["rows"]], ["balanced", "dense"])
        self.assertEqual(document["posterior"]["rejected"][0]["label"], "small")

    def test_multidimensional_fixture_returns_diagnostics(self) -> None:
        document = self.run_fixture("synthetic_multidimensional.json")
        self.assertEqual(document["mode"], "weighted")
        self.assertEqual(document["posterior"]["rejected"][0]["label"], "compact")
        self.assertGreater(document["posterior"]["diagnostics"]["effective_sample_size"], 1)
        self.assertTrue(document["posterior"]["credible_set"])

    def test_non_object_document_fails_closed(self) -> None:
        with self.assertRaises(TypeError):
            evaluate_document([])


if __name__ == "__main__":
    unittest.main()
