from __future__ import annotations

import unittest

from auction_inference.monte_carlo import simulate_totals


class MonteCarloTest(unittest.TestCase):
    def test_seed_makes_summary_reproducible(self) -> None:
        first = simulate_totals([1, 3], [0.75, 0.25], draws_per_trial=5, trials=100, seed=11)
        second = simulate_totals([1, 3], [0.75, 0.25], draws_per_trial=5, trials=100, seed=11)
        self.assertEqual(first, second)
        self.assertLessEqual(first.minimum, first.median)
        self.assertLessEqual(first.median, first.maximum)

    def test_rejects_invalid_distribution(self) -> None:
        with self.assertRaises(ValueError):
            simulate_totals([1], [0.0], draws_per_trial=1, trials=1, seed=1)


if __name__ == "__main__":
    unittest.main()
