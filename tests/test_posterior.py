from __future__ import annotations

import unittest

from auction_inference.models import Evidence, Hypothesis, Interval
from auction_inference.posterior import infer_posterior, normalize_weights


class PosteriorTest(unittest.TestCase):
    def test_normalizes_only_accepted_hypotheses(self) -> None:
        result = infer_posterior(
            (
                Hypothesis("a", 10, 1000, 1.0),
                Hypothesis("b", 12, 1400, 3.0),
                Hypothesis("rejected", 20, 3000, 99.0),
            ),
            Evidence(Interval(10, 12), Interval(900, 1500)),
        )
        self.assertEqual([round(row.probability, 2) for row in result.rows], [0.25, 0.75])
        self.assertEqual(result.expected_count, 11.5)
        self.assertEqual(result.expected_value, 1300.0)
        self.assertEqual(result.rejected[0].hypothesis.label, "rejected")

    def test_rejects_zero_or_non_finite_weight_sets(self) -> None:
        with self.assertRaises(ValueError):
            normalize_weights([0.0, 0.0])
        with self.assertRaises(ValueError):
            normalize_weights([1.0, float("nan")])


if __name__ == "__main__":
    unittest.main()
