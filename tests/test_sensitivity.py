from __future__ import annotations

import unittest

from auction_inference import (
    ApproximateObservation,
    Candidate,
    EvidenceTerm,
    compare_distributions,
    rank_evidence_influence,
)


class SensitivityTest(unittest.TestCase):
    def test_distribution_shift_accepts_different_supports(self) -> None:
        shift = compare_distributions(
            {"a": 0.7, "b": 0.3},
            {"a": 0.4, "b": 0.5, "c": 0.1},
        )
        self.assertAlmostEqual(shift.total_variation, 0.3)
        self.assertGreater(shift.jensen_shannon_bits, 0.0)
        self.assertTrue(shift.top_changed)
        self.assertEqual(shift.deltas[0].label, "a")

    def test_leave_one_out_ranks_stronger_term_first(self) -> None:
        candidates = (
            Candidate("left", {"x": 0, "y": 0}, 0.5),
            Candidate("right", {"x": 1, "y": 1}, 0.5),
        )
        terms = (
            EvidenceTerm("x", ApproximateObservation(0, 0.5), weight=2.0),
            EvidenceTerm("y", ApproximateObservation(1, 0.5), weight=1.0),
        )
        influences = rank_evidence_influence(candidates, terms)
        self.assertEqual(influences[0].field, "x")
        self.assertGreater(
            influences[0].shift.total_variation,
            influences[1].shift.total_variation,
        )
        self.assertTrue(influences[0].shift.top_changed)

    def test_invalid_distributions_and_short_term_sets_fail(self) -> None:
        with self.assertRaises(ValueError):
            compare_distributions({"a": 0.8}, {"a": 1.0})
        with self.assertRaises(TypeError):
            compare_distributions({"a": True}, {"a": 1.0})
        with self.assertRaises(ValueError):
            rank_evidence_influence(
                (Candidate("a", {"x": 1}, 1.0),),
                (EvidenceTerm("x", ApproximateObservation(1, 1)),),
            )


if __name__ == "__main__":
    unittest.main()
