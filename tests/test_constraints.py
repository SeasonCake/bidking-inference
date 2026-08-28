from __future__ import annotations

import unittest

from auction_inference.constraints import filter_hypotheses
from auction_inference.models import Evidence, Hypothesis, Interval


class ConstraintTest(unittest.TestCase):
    def test_separates_positive_and_negative_hypotheses(self) -> None:
        evidence = Evidence(Interval(10, 14), Interval(900, 1600))
        accepted, rejected = filter_hypotheses(
            (
                Hypothesis("inside", 12, 1200, 0.7),
                Hypothesis("outside", 9, 800, 0.3),
            ),
            evidence,
        )
        self.assertEqual([result.hypothesis.label for result in accepted], ["inside"])
        self.assertEqual(
            rejected[0].reasons,
            ("total_count_outside_interval", "total_value_outside_interval"),
        )

    def test_bool_is_not_accepted_as_integer(self) -> None:
        with self.assertRaises(TypeError):
            Interval(True, 2)


if __name__ == "__main__":
    unittest.main()
