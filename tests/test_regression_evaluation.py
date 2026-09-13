import unittest
from examples.regression_evaluation import Sample, example, fit_offset, metrics


class EvaluationTests(unittest.TestCase):
    def test_signed_aggregate_cancellation_does_not_mean_small_errors(self):
        result = example()["holdout"]
        self.assertEqual(result["aggregate_signed_relative_error"], 0)
        self.assertEqual(result["bias"], 0)
        self.assertEqual(result["mae"], 40)
        self.assertEqual(result["rmse"], 40)
        self.assertAlmostEqual(result["mape"], 0.4)

    def test_holdout_changes_cannot_change_fitted_offset(self):
        train = Sample("a", "train", 100, 90)
        self.assertEqual(fit_offset([train, Sample("b", "holdout", 100, -900)]), 10)
        self.assertEqual(fit_offset([train, Sample("b", "holdout", 100, 9999)]), 10)

    def test_reconstruction_cannot_be_used_as_training_prediction(self):
        with self.assertRaises(ValueError):
            fit_offset([Sample("a", "train", 100, 100, "reconstruction")])
        with self.assertRaises(ValueError):
            metrics([Sample("a", "train", 100, 100), Sample("b", "holdout", 100, 100)])
        with self.assertRaises(ValueError):
            metrics(
                [
                    Sample("a", "train", 100, 100),
                    Sample("b", "train", 100, 100, "reconstruction"),
                ]
            )

    def test_zero_actual_has_explicit_mape_coverage(self):
        result = metrics([Sample("a", "holdout", 0, 2), Sample("b", "holdout", 10, 12)])
        self.assertEqual(result["mape_n"], 1)
        self.assertEqual(result["zero_actual_excluded_from_mape"], 1)
        self.assertEqual(result["mae"], 2)
        result = metrics([Sample("a", "holdout", 0, 2)])
        self.assertIsNone(result["mape"])
        self.assertIsNone(result["aggregate_signed_relative_error"])
