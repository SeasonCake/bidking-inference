from __future__ import annotations

import math
import unittest

from auction_inference import (
    ApproximateObservation,
    CategoricalObservation,
    ExactObservation,
    IntervalObservation,
    observation_from_mapping,
)


class ObservationTest(unittest.TestCase):
    def test_exact_observation_matches_numeric_and_string_values(self) -> None:
        self.assertTrue(ExactObservation(3).evaluate(3.0).accepted)
        self.assertTrue(ExactObservation("north").evaluate("north").accepted)

    def test_exact_observation_rejects_cross_type_and_bool(self) -> None:
        self.assertEqual(ExactObservation("3").evaluate(3).reason, "exact_mismatch")
        self.assertEqual(ExactObservation(1).evaluate(True).reason, "type_mismatch")

    def test_interval_is_inclusive_and_rejects_non_numeric(self) -> None:
        observation = IntervalObservation(2, 4)
        self.assertTrue(observation.evaluate(2).accepted)
        self.assertTrue(observation.evaluate(4.0).accepted)
        self.assertEqual(observation.evaluate("3").reason, "numeric_type_mismatch")

    def test_approximate_observation_scores_distance(self) -> None:
        observation = ApproximateObservation(100, 10)
        self.assertEqual(observation.evaluate(100).log_likelihood, 0.0)
        self.assertAlmostEqual(observation.evaluate(110).log_likelihood, -0.5)

    def test_approximate_cutoff_rejects_outlier(self) -> None:
        result = ApproximateObservation(100, 10, cutoff=2).evaluate(121)
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "outside_cutoff")

    def test_categorical_observation_uses_relative_likelihood(self) -> None:
        observation = CategoricalObservation({"north": 1.0, "south": 0.25})
        self.assertEqual(observation.evaluate("north").log_likelihood, 0.0)
        self.assertAlmostEqual(observation.evaluate("south").log_likelihood, math.log(0.25))
        self.assertEqual(observation.evaluate("west").reason, "category_not_supported")

    def test_invalid_observation_parameters_fail(self) -> None:
        with self.assertRaises(ValueError):
            IntervalObservation(5, 4)
        with self.assertRaises(ValueError):
            ApproximateObservation(0, 0)
        with self.assertRaises(ValueError):
            CategoricalObservation({"x": 0})

    def test_mapping_parser_requires_exact_shape(self) -> None:
        observation = observation_from_mapping(
            {"kind": "approximate", "center": 10, "scale": 2, "cutoff": 3}
        )
        self.assertIsInstance(observation, ApproximateObservation)
        with self.assertRaises(ValueError):
            observation_from_mapping({"kind": "exact", "expected": 1, "extra": 2})
        with self.assertRaises(TypeError):
            observation_from_mapping(["exact", 1])


if __name__ == "__main__":
    unittest.main()
