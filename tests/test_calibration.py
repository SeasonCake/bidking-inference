from __future__ import annotations

import math
import unittest

from auction_inference import BinaryForecast, assess_binary_calibration


class CalibrationTest(unittest.TestCase):
    def test_reliability_and_scores_are_weighted(self) -> None:
        report = assess_binary_calibration(
            (
                BinaryForecast(0.1, False),
                BinaryForecast(0.2, False),
                BinaryForecast(0.8, True),
                BinaryForecast(0.9, True),
            ),
            bins=2,
        )
        self.assertEqual(report.forecast_count, 4)
        self.assertAlmostEqual(report.brier_score, 0.025)
        self.assertAlmostEqual(report.expected_calibration_error, 0.15)
        self.assertAlmostEqual(report.maximum_calibration_error, 0.15)
        self.assertEqual(len(report.bins), 2)

    def test_exact_predictions_keep_log_loss_finite(self) -> None:
        report = assess_binary_calibration(
            (BinaryForecast(0.0, False), BinaryForecast(1.0, True))
        )
        self.assertTrue(math.isfinite(report.log_loss))
        self.assertAlmostEqual(report.brier_score, 0.0)

    def test_sample_weights_change_observed_rate(self) -> None:
        report = assess_binary_calibration(
            (BinaryForecast(0.5, False, 1), BinaryForecast(0.5, True, 3)), bins=1
        )
        self.assertAlmostEqual(report.bins[0].observed_rate, 0.75)
        self.assertAlmostEqual(report.bins[0].total_weight, 4.0)

    def test_invalid_forecasts_fail_closed(self) -> None:
        with self.assertRaises(TypeError):
            BinaryForecast(True, False)
        with self.assertRaises(TypeError):
            BinaryForecast(0.5, 1)
        with self.assertRaises(ValueError):
            BinaryForecast(1.1, True)
        with self.assertRaises(ValueError):
            assess_binary_calibration(())
        with self.assertRaises(TypeError):
            assess_binary_calibration((BinaryForecast(0.5, True),), bins=True)


if __name__ == "__main__":
    unittest.main()
