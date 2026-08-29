from __future__ import annotations

import unittest

from auction_inference import (
    CategoricalObservation,
    DiscreteVariable,
    EvidenceTerm,
    VariableState,
    enumerate_joint_candidates,
    infer_joint_posterior,
)


class JointTest(unittest.TestCase):
    def setUp(self) -> None:
        self.variables = (
            DiscreteVariable("a", (VariableState("x", 0.6), VariableState("y", 0.4))),
            DiscreteVariable("b", (VariableState("m", 0.7), VariableState("n", 0.3))),
        )

    def test_enumerates_cartesian_candidates_and_priors(self) -> None:
        candidates = enumerate_joint_candidates(self.variables)
        self.assertEqual(len(candidates), 4)
        self.assertAlmostEqual(sum(candidate.prior for candidate in candidates), 1.0)
        self.assertEqual(candidates[0].attributes, {"a": "x", "b": "m"})

    def test_limit_stops_combinatorial_expansion(self) -> None:
        with self.assertRaisesRegex(ValueError, "exceeds"):
            enumerate_joint_candidates(self.variables, max_combinations=3)

    def test_duplicate_variable_or_state_names_fail(self) -> None:
        with self.assertRaises(ValueError):
            enumerate_joint_candidates((self.variables[0], self.variables[0]))
        with self.assertRaises(ValueError):
            DiscreteVariable("a", (VariableState("x", 1), VariableState("x", 1)))

    def test_joint_posterior_consumes_categorical_evidence(self) -> None:
        summary = infer_joint_posterior(
            self.variables,
            (EvidenceTerm("a", CategoricalObservation({"x": 1.0, "y": 0.1})),),
        )
        x_mass = sum(row.probability for row in summary.rows if row.attributes["a"] == "x")
        self.assertGreater(x_mass, 0.8)

    def test_invalid_limit_type_fails_closed(self) -> None:
        with self.assertRaises(TypeError):
            enumerate_joint_candidates(self.variables, max_combinations=True)


if __name__ == "__main__":
    unittest.main()
