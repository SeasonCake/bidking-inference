import unittest
from examples.posterior_projection import (
    Candidate,
    count_distribution,
    example,
    marginal,
    projection,
    views,
)


class ProjectionTests(unittest.TestCase):
    def test_full_and_truncated_tail_come_from_their_own_distribution(self):
        result = example()
        self.assertAlmostEqual(result["joint_top_two"]["other"], 0.4)
        self.assertEqual(result["full_marginal"]["other"], 0)
        self.assertAlmostEqual(result["truncated_marginal"]["other"], 0.1)
        self.assertEqual(result["full_marginal"]["main"], "0")
        self.assertAlmostEqual(result["full_marginal"]["details"][0][1], 0.6)

    def test_double_counting_joint_tail_is_rejected(self):
        with self.assertRaises(ValueError):
            projection({"0": 0.6, "1": 0.3, "2": 0.1, "other": 0.4})
        rows = (Candidate("one", {"n": 0}, 1),)
        with self.assertRaises(ValueError):
            marginal(rows, (0.6,), "n")

    def test_observed_zero_and_unknown_are_not_fused(self):
        candidates = (
            Candidate("none", {"count": 0}, 0.1),
            Candidate("some", {"count": 5}, 0.9),
        )
        self.assertEqual(count_distribution(candidates, 0), {"0": 1.0})
        self.assertEqual(count_distribution(candidates, None), {"0": 0.1, "5": 0.9})
        with self.assertRaises(ValueError):
            count_distribution(candidates, 2)
        self.assertEqual(views(projection({"0": 1}))["main"], "0")
        self.assertEqual(projection({"0": 1}).other, 0)
        with self.assertRaises(ValueError):
            marginal((Candidate("x", {"other": 1}, 1),), (1.0,), "n")

    def test_numeric_and_text_labels_cannot_silently_merge(self):
        candidates = (
            Candidate("number", {"n": 1}, 0.5),
            Candidate("text", {"n": "1"}, 0.5),
        )
        with self.assertRaises(ValueError):
            marginal(candidates, (0.5, 0.5), "n")

    def test_ordering_ties_and_zero_probability(self):
        value = projection({"b": 0.5, "a": 0.5, "c": 0}, 1)
        self.assertEqual(views(value)["main"], "a")
        self.assertEqual(value.other, 0.5)
        self.assertEqual(projection({"zero": 0, "one": 1}, 5).other, 0)

    def test_invalid_probability_and_limit(self):
        for values in [{}, {"x": -1, "y": 2}, {"x": True}, {"x": float("nan")}]:
            with self.assertRaises(ValueError):
                projection(values)
        with self.assertRaises(ValueError):
            projection({"x": 1}, True)
