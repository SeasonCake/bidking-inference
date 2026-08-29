from __future__ import annotations

import unittest

from auction_inference import (
    ApproximateObservation,
    Candidate,
    CategoricalObservation,
    EvidenceTerm,
    IntervalObservation,
    infer_weighted_posterior,
    score_candidate,
)


class ScoringTest(unittest.TestCase):
    def setUp(self) -> None:
        self.terms = (
            EvidenceTerm("count", IntervalObservation(9, 13)),
            EvidenceTerm("value", ApproximateObservation(1200, 200)),
            EvidenceTerm("region", CategoricalObservation({"north": 1.0, "south": 0.5})),
        )

    def test_hard_and_soft_evidence_produce_normalized_rows(self) -> None:
        summary = infer_weighted_posterior(
            (
                Candidate("north", {"count": 11, "value": 1100, "region": "north"}, 0.6),
                Candidate("south", {"count": 12, "value": 1300, "region": "south"}, 0.4),
            ),
            self.terms,
        )
        self.assertAlmostEqual(sum(row.probability for row in summary.rows), 1.0)
        self.assertGreater(summary.rows[0].probability, summary.rows[1].probability)

    def test_hard_rejection_preserves_reason(self) -> None:
        score = score_candidate(
            Candidate("small", {"count": 4, "value": 1100, "region": "north"}, 1.0),
            self.terms,
        )
        self.assertFalse(score.accepted)
        self.assertIn("count:outside_interval", score.reasons)

    def test_missing_field_fails_closed(self) -> None:
        score = score_candidate(Candidate("missing", {"count": 10}, 1.0), self.terms)
        self.assertIn("missing_field:value", score.reasons)
        self.assertIn("missing_field:region", score.reasons)

    def test_zero_prior_is_rejected(self) -> None:
        score = score_candidate(
            Candidate("zero", {"count": 10, "value": 1200, "region": "north"}, 0),
            self.terms,
        )
        self.assertEqual(score.reasons, ("zero_prior",))

    def test_log_space_handles_tiny_priors(self) -> None:
        summary = infer_weighted_posterior(
            (
                Candidate("a", {"x": 1}, 1e-300),
                Candidate("b", {"x": 1}, 2e-300),
            ),
            (EvidenceTerm("x", IntervalObservation(1, 1)),),
        )
        self.assertAlmostEqual(summary.rows[0].probability, 1 / 3)
        self.assertAlmostEqual(summary.rows[1].probability, 2 / 3)

    def test_mapping_parsers_reject_wrong_shapes(self) -> None:
        candidate = Candidate.from_mapping(
            {"label": "a", "attributes": {"x": 1}, "prior": 1}
        )
        term = EvidenceTerm.from_mapping(
            {"field": "x", "observation": {"kind": "interval", "minimum": 0, "maximum": 2}}
        )
        self.assertEqual(candidate.label, "a")
        self.assertEqual(term.field, "x")
        with self.assertRaises(ValueError):
            Candidate.from_mapping({"label": "a", "attributes": {"x": 1}})

    def test_empty_or_fully_rejected_inputs_fail(self) -> None:
        with self.assertRaises(ValueError):
            infer_weighted_posterior((), self.terms)
        with self.assertRaises(ValueError):
            infer_weighted_posterior((Candidate("a", {"x": 1}, 1),), ())
        with self.assertRaises(ValueError):
            infer_weighted_posterior(
                (Candidate("a", {"x": 1}, 1),),
                (EvidenceTerm("x", IntervalObservation(2, 3)),),
            )

    def test_duplicate_candidate_labels_fail(self) -> None:
        with self.assertRaises(ValueError):
            infer_weighted_posterior(
                (Candidate("a", {"x": 1}, 1), Candidate("a", {"x": 2}, 1)),
                (EvidenceTerm("x", IntervalObservation(1, 2)),),
            )

    def test_custom_observation_must_return_valid_match(self) -> None:
        class InvalidObservation:
            def evaluate(self, value: object) -> str:
                return "invalid"

        with self.assertRaises(TypeError):
            score_candidate(
                Candidate("a", {"x": 1}, 1),
                (EvidenceTerm("x", InvalidObservation()),),
            )


if __name__ == "__main__":
    unittest.main()
