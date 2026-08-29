from __future__ import annotations

import unittest

from auction_inference import posterior_diagnostics, smallest_credible_set


class DiagnosticTest(unittest.TestCase):
    def test_uniform_distribution_has_expected_diagnostics(self) -> None:
        result = posterior_diagnostics((0.25, 0.25, 0.25, 0.25))
        self.assertAlmostEqual(result.entropy_bits, 2.0)
        self.assertAlmostEqual(result.effective_sample_size, 4.0)
        self.assertEqual(result.support_size, 4)

    def test_zero_probability_is_excluded_from_support(self) -> None:
        result = posterior_diagnostics((1.0, 0.0))
        self.assertEqual(result.support_size, 1)
        self.assertEqual(result.entropy_bits, 0.0)

    def test_probabilities_must_be_normalized(self) -> None:
        with self.assertRaises(ValueError):
            posterior_diagnostics((0.2, 0.2))
        with self.assertRaises(ValueError):
            posterior_diagnostics((0.5, -0.5, 1.0))
        with self.assertRaises(TypeError):
            posterior_diagnostics((True, 0.0))

    def test_credible_set_is_smallest_probability_ordered_prefix(self) -> None:
        result = smallest_credible_set((("b", 0.3), ("a", 0.6), ("c", 0.1)), mass=0.8)
        self.assertEqual(tuple(label for label, _ in result), ("a", "b"))

    def test_credible_set_rejects_duplicate_labels(self) -> None:
        with self.assertRaises(ValueError):
            smallest_credible_set((("a", 0.5), ("a", 0.5)))

    def test_credible_mass_must_be_valid(self) -> None:
        with self.assertRaises(ValueError):
            smallest_credible_set((("a", 1.0),), mass=0)
        with self.assertRaises(TypeError):
            smallest_credible_set((("a", 1.0),), mass=True)


if __name__ == "__main__":
    unittest.main()
