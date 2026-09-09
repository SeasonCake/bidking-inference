import copy
from pathlib import Path
import tempfile
import unittest

from research.historical_data.generate_charts import (ROOT, aisha_rows, catalog_rows,
                                                      quality_rows, tables, write_csv)


class HistoricalDataTests(unittest.TestCase):
    def test_committed_csv_is_reproducible(self):
        with tempfile.TemporaryDirectory() as directory:
            for name, rows in tables().items():
                output = Path(directory) / (name + ".csv")
                write_csv(output, rows)
                self.assertEqual(output.read_bytes(), (ROOT / "docs/assets/charts" / output.name).read_bytes())

    def test_pinned_coverage_and_catalog_denominators(self):
        result = tables()
        self.assertEqual(len(result["snapshot_inventory"]), 7)
        self.assertEqual(sum(r["bytes"] for r in result["snapshot_inventory"]), 641335)
        rows = result["quality_weights"]
        self.assertEqual(len(rows), 25)
        self.assertEqual([r["session_count"] for r in rows if r["quality"] == "q1+q2"], [8, 222, 471, 143, 189])
        self.assertEqual({r["kind"] for r in rows}, {"historical_aggregate"})
        self.assertEqual([r["weight"] for r in rows if r["tier"] == "101" and r["quality"] == "q6"], [0.0])

    def test_invalid_weight_is_not_silently_normalized(self):
        base = {"by_tier": {"x": {"q1": .5, "q3": .2, "q4": .1, "q5": .1, "q6": .1}}, "tier_sessions": {"x": 3}}
        self.assertEqual(len(quality_rows(base)), 5)
        for value in (float("nan"), float("inf"), -.1, True, .6):
            wrong = copy.deepcopy(base)
            wrong["by_tier"]["x"]["q1"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                quality_rows(wrong)

    def test_catalog_not_drop_frequency(self):
        rows = catalog_rows([{"item_id": 1, "quality": 1}, {"item_id": 2, "quality": 6}])
        self.assertEqual([r["catalog_records"] for r in rows], [1, 0, 0, 0, 0, 1])
        with self.assertRaises(ValueError):
            catalog_rows([{"item_id": 1, "quality": 1}, {"item_id": 1, "quality": 6}])

    def test_missing_coverage_fails(self):
        with self.assertRaises(ValueError):
            quality_rows({"by_tier": {"x": {}}, "tier_sessions": {}})

    def test_historical_aisha_is_not_synthetic_or_new_experiment(self):
        rows = tables()["aisha_historical_metrics"]
        self.assertEqual([r["value_pct"] for r in rows], [-1.5, 23.2, 51.5, -.8, 23.4, 47.2])
        self.assertEqual({r["value_cases"] for r in rows}, {79})
        with self.assertRaises(ValueError):
            aisha_rows({"kind": "synthetic", "cases": 80, "value_cases": 79})
