from __future__ import annotations

import unittest

from auction_inference import PoolItem, simulate_pool


class PoolSimulationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.items = (
            PoolItem("a", 10, 3.0, "common"),
            PoolItem("b", 20, 2.0, "common"),
            PoolItem("c", 100, 1.0, "rare"),
        )

    def test_seed_is_deterministic_with_replacement(self) -> None:
        first = simulate_pool(self.items, draws_per_trial=2, trials=100, seed=7)
        second = simulate_pool(self.items, draws_per_trial=2, trials=100, seed=7)
        self.assertEqual(first, second)

    def test_without_replacement_respects_pool_bound(self) -> None:
        result = simulate_pool(
            self.items, draws_per_trial=3, trials=10, seed=2, replacement=False
        )
        self.assertEqual(result.totals.minimum, 130)
        self.assertEqual(result.totals.maximum, 130)

    def test_category_means_sum_to_draw_count(self) -> None:
        result = simulate_pool(self.items, draws_per_trial=2, trials=100, seed=3)
        self.assertAlmostEqual(sum(row.mean_draws for row in result.categories), 2.0)

    def test_too_many_without_replacement_fails(self) -> None:
        with self.assertRaises(ValueError):
            simulate_pool(self.items, draws_per_trial=4, trials=1, seed=1, replacement=False)

    def test_invalid_pool_or_replacement_type_fails(self) -> None:
        with self.assertRaises(ValueError):
            simulate_pool((), draws_per_trial=1, trials=1, seed=1)
        with self.assertRaises(TypeError):
            simulate_pool(self.items, draws_per_trial=1, trials=1, seed=1, replacement=1)


if __name__ == "__main__":
    unittest.main()
