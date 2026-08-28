from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CliTest(unittest.TestCase):
    def test_documented_fixture_runs_without_private_inputs(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "auction_inference.cli", str(ROOT / "examples" / "synthetic_session.json")],
            cwd=ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")},
        )
        document = json.loads(result.stdout)
        self.assertTrue(document["synthetic"])
        self.assertEqual([row["label"] for row in document["posterior"]["rows"]], ["balanced", "dense"])
        self.assertEqual(document["posterior"]["rejected"][0]["label"], "small")


if __name__ == "__main__":
    unittest.main()
