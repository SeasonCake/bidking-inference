import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from research.aisha.penalty_example import demo, penalty_trace, write_csv

ROOT = Path(__file__).resolve().parents[1]


class PenaltyTeachingTests(unittest.TestCase):
    def trace(self, score, **kwargs):
        return penalty_trace(1000, score, **{"strength": .4, "exponent": 2, **kwargs})

    def test_hand_calculated_midpoint_and_endpoints(self):
        middle = self.trace(.5)
        self.assertEqual(middle["gap"], .5)
        self.assertEqual(middle["curved_gap"], .25)
        self.assertAlmostEqual(middle["penalty_fraction"], .1)
        self.assertEqual(middle["penalized_quote"], 900)
        self.assertEqual([self.trace(score)["penalized_quote"] for score in (0, .25, .5, .75, 1)],
                         [600, 775, 900, 975, 1000])

    def test_monotonic_and_bounded_across_the_demo_domain(self):
        outputs = [self.trace(i / 100)["penalized_quote"] for i in range(101)]
        self.assertEqual(outputs, sorted(outputs))
        self.assertTrue(all(600 <= output <= 1000 for output in outputs))

    def test_parameters_are_actually_exercised_not_identical_arms(self):
        self.assertEqual(self.trace(.5, exponent=1)["penalized_quote"], 800)
        self.assertEqual(self.trace(.5, exponent=2)["penalized_quote"], 900)
        self.assertEqual(self.trace(.5, strength=0)["penalized_quote"], 1000)
        self.assertEqual(self.trace(0, strength=1)["penalized_quote"], 0)

    def test_zero_quote_is_valid(self):
        self.assertEqual(penalty_trace(0, 0, strength=.4, exponent=2)["penalized_quote"], 0)

    def test_malformed_and_nonfinite_inputs_rejected(self):
        for field in ("base_quote", "support_score", "strength", "exponent"):
            for bad in (True, "0.5", None, float("nan"), float("inf"), 10 ** 1000):
                values = dict(base_quote=1000, support_score=.5, strength=.4, exponent=2)
                values[field] = bad
                with self.subTest(field=field, bad=type(bad).__name__), self.assertRaises(ValueError):
                    penalty_trace(**values)

    def test_out_of_range_inputs_rejected(self):
        for field, value in (("base_quote", -1), ("support_score", -.1), ("support_score", 1.1),
                             ("strength", -.1), ("strength", 1.1), ("exponent", 0), ("exponent", -1)):
            values = dict(base_quote=1000, support_score=.5, strength=.4, exponent=2)
            values[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                penalty_trace(**values)

    def test_synthetic_trace_csv_matches_committed_result(self):
        result = demo()
        self.assertTrue(result["synthetic"])
        self.assertTrue(result["not_product_model"])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "trace.csv"
            write_csv(output, result["cases"])
            self.assertEqual(output.read_bytes(), (ROOT / "docs/assets/charts/penalty_teaching_trace.csv").read_bytes())

    def test_cli_reports_the_real_function_output(self):
        completed = subprocess.run([sys.executable, "-B", "-m", "research.aisha.penalty_example"],
                                   cwd=ROOT, capture_output=True, text=True, check=True, timeout=10)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(json.loads(completed.stdout), demo())
