"""Focused teaching-contract tests; no game, product engine or real captures."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from research.aisha import (
    early_window_example, evaluate_grid_example, live_teaching_snapshot,
    offline_teaching_snapshot, public_visible_round, simulate_visibility, toy_warehouse,
)
from research.aisha.__main__ import demo
from research.aisha.replay_contract import (
    _apply_total_avg_cells_grid_feasibility, normalize_public_facts,
)
from research.aisha.windows import _aisha_early_round_count_window_bounds


class WindowTests(unittest.TestCase):
    def test_original_bounds_reproduce_empty_range(self):
        notes = []
        bounds = _aisha_early_round_count_window_bounds(center=58, count_floor=1, notes=notes)
        self.assertEqual(bounds, (56, 50))
        self.assertEqual(list(range(bounds[0], bounds[1] + 1)), [])
        self.assertEqual(notes, [])

    def test_normal_window_unchanged(self):
        result = early_window_example(42)
        self.assertEqual(result["candidates"], [40, 41, 42, 43, 44])
        self.assertEqual(result["notes"], [])

    def test_inverted_window_rescued_but_rollback_is_empty(self):
        self.assertEqual(early_window_example(58)["candidates"], [56, 57, 58, 59, 60])
        self.assertEqual(early_window_example(58, rollback=True)["candidates"], [])

    def test_fixed_band_reanchor_precedes_expand_and_preserves_price_input(self):
        result = early_window_example(58, fixed_band=(45, 48))
        rollback = early_window_example(58, fixed_band=(45, 48), rollback=True)
        self.assertEqual(result["candidates"], [45, 46, 47, 48])
        self.assertEqual(result["candidates"], rollback["candidates"])
        self.assertEqual(result["steps"][1]["bounds"], [45, 48])
        self.assertTrue(result["notes"][0].startswith("count_window_reanchored"))
        self.assertFalse(any("expanded" in n for n in result["notes"]))

    def test_fixed_band_intersection(self):
        self.assertEqual(early_window_example(42, fixed_band=(41, 43))["candidates"], [41, 42, 43])

    def test_no_grid_target_branch_has_no_fixed_band_step(self):
        result = early_window_example(58, branch="without_grid_target")
        self.assertEqual([x["stage"] for x in result["steps"]], ["legacy_cap", "expand_if_inverted"])
        self.assertEqual(result["candidates"], [56, 57, 58, 59, 60])
        with self.assertRaises(ValueError):
            early_window_example(58, branch="without_grid_target", fixed_band=(45, 48))

    def test_expansion_has_a_real_ceiling(self):
        self.assertEqual(early_window_example(100)["candidates"], [98, 99, 100])
        result = early_window_example(105)
        self.assertEqual(result["steps"][-1]["bounds"], [103, 100])
        self.assertEqual(result["candidates"], [])

    def test_strict_wrapper_is_separate_from_historical_arithmetic(self):
        for value in (True, 0, -1, "58", 58.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                early_window_example(value)
        with self.assertRaises(ValueError):
            early_window_example(58, fixed_band=(45, 101))
        with self.assertRaises(ValueError):
            early_window_example(58, rollback=1)


class ReplayContractTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{"semantic": "total_avg_cells", "value": 4.0, "unit": "cells_per_item"}]
        self.counts = {"all_items": (20, 25, 30)}

    def test_legacy_missing_channel_and_fixed_parity(self):
        live = live_teaching_snapshot(self.rows)
        fixed = offline_teaching_snapshot(self.rows)
        legacy = offline_teaching_snapshot(self.rows, legacy_omission=True)
        self.assertEqual(live, fixed)
        self.assertNotEqual(live, legacy)
        old_result = evaluate_grid_example(legacy, (60, 100, 140), self.counts)
        new_result = evaluate_grid_example(fixed, (60, 100, 140), self.counts)
        self.assertEqual(old_result["status"], "missing")
        self.assertEqual(old_result["after"], [60, 100, 140])
        self.assertEqual(new_result["after"], [80, 100, 120])
        self.assertTrue(new_result["changed"])

    def test_feasible_grid_does_not_move_and_quote_is_not_measured(self):
        for before in ((90, 100, 110), (60, 100, 140)):
            result = evaluate_grid_example(live_teaching_snapshot(self.rows), before, self.counts)
            self.assertFalse(result["quote_evaluated"])
            self.assertNotIn("quote_improvement", result)
        result = evaluate_grid_example(live_teaching_snapshot(self.rows), (90, 100, 110), self.counts)
        self.assertFalse(result["changed"])
        self.assertEqual(result["notes"], [])

    def test_absent_fact_and_wrong_semantic_are_missing(self):
        for rows in ([], [{"semantic": "avg_value", "value": 4.0, "unit": "money_per_item"}]):
            result = evaluate_grid_example(live_teaching_snapshot(rows), (60, 100, 140), self.counts)
            self.assertEqual(result["status"], "missing")
            self.assertFalse(result["changed"])

    def test_invalid_values_do_not_become_zero_or_valid_facts(self):
        for value in (None, True, "4.0", 0, -1, float("nan"), float("inf"), 10**400):
            with self.subTest(value=repr(value)[:30]):
                snap = live_teaching_snapshot([{"semantic": "total_avg_cells", "value": value}])
                result = evaluate_grid_example(snap, (60, 100, 140), self.counts)
                self.assertEqual(result["status"], "invalid")
                self.assertFalse(result["changed"])

    def test_units_conflicts_and_equal_duplicates(self):
        wrong = [{"semantic": "total_avg_cells", "value": 4.0, "unit": "money_per_cell"}]
        self.assertEqual(normalize_public_facts(wrong)["status"], "invalid")
        conflict = self.rows + [{"semantic": "total_avg_cells", "value": 5.0}]
        self.assertEqual(normalize_public_facts(conflict)["status"], "invalid")
        self.assertEqual(normalize_public_facts(list(reversed(conflict)))["status"], "invalid")
        same = normalize_public_facts(self.rows + self.rows)
        self.assertEqual(same["status"], "present")
        self.assertEqual(len(same["public_numeric_facts"]), 1)

    def test_invalid_count_ranges_and_overflow_do_not_reach_helper(self):
        for counts in ({}, {"all": (0, 0, 0)}, {"all": (30, 20, 10)}, {"all": (None, 20, 30)}, {"all": (True, 2, 3)}):
            result = evaluate_grid_example(live_teaching_snapshot(self.rows), (60, 100, 140), counts)
            self.assertEqual(result["status"], "invalid_count_ranges")
            self.assertFalse(result["changed"])
        result = evaluate_grid_example(
            live_teaching_snapshot([{"semantic": "total_avg_cells", "value": 1e308}]),
            (60, 100, 140), self.counts,
        )
        self.assertEqual(result["status"], "invalid_product")

    def test_original_helper_retains_rounding_and_partial_grid(self):
        self.assertEqual(
            _apply_total_avg_cells_grid_feasibility(
                (0, 5, 12), [], total_avg_cells=2.5, quality_count_ranges={"all": (1, 2, 3)}
            ),
            (2, 5, 8),  # Python round: 2.5->2 and 7.5->8; not floor/ceil.
        )
        self.assertEqual(
            _apply_total_avg_cells_grid_feasibility(
                (None, 100, 140), [], total_avg_cells=4.0, quality_count_ranges=self.counts
            ),
            (None, 100, 120),
        )
        with self.assertRaises(ValueError):
            evaluate_grid_example(live_teaching_snapshot(self.rows), (None, 100, 140), self.counts)


class VisibilityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = toy_warehouse()

    def test_fixture_is_explicitly_fictional_and_has_ten_items(self):
        self.assertIs(self.fixture["synthetic"], True)
        self.assertEqual(len(self.fixture["items"]), 10)
        self.assertEqual(sum(it["cells"] for it in self.fixture["items"]), 27)

    def test_white_green_split_is_not_a_white_only_merged_lock(self):
        first = simulate_visibility(self.fixture, 1, tool_schedule={})
        second = simulate_visibility(self.fixture, 2, tool_schedule={})
        self.assertEqual(first["fixed_counts"], {"split_white": 2})
        self.assertEqual(first["merged_q1_count"], {"certainty": "lower_bound", "value": 2, "unit": "items"})
        self.assertEqual(second["fixed_counts"]["split_green"], 2)
        self.assertEqual(second["fixed_counts"]["bucket_q1_white_plus_green"], 4)
        self.assertEqual(second["fixed_cells"]["bucket_q1_white_plus_green"], 6)
        self.assertEqual(second["merged_q1_count"]["certainty"], "exact")

    def test_skill_rounds_never_reveal_gold_red_and_r5_has_no_new_skill(self):
        fourth = simulate_visibility(self.fixture, 4, tool_schedule={})
        fifth = simulate_visibility(self.fixture, 5, tool_schedule={})
        self.assertEqual({x["quality"] for x in fourth["items_visible"]}, {1, 2, 3, 4})
        self.assertEqual(len(fourth["items_visible"]), 8)
        self.assertEqual(fourth["items_visible"], fifth["items_visible"])
        self.assertEqual(fourth["fixed_counts"], fifth["fixed_counts"])
        self.assertEqual(fourth["fixed_cells"]["q3"], 5)
        self.assertEqual(fourth["fixed_cells"]["q4"], 6)

    def test_truth_values_cannot_leak_through_quality_or_outline(self):
        before = simulate_visibility(self.fixture, 1, seed=0)
        modified = copy.deepcopy(self.fixture)
        modified["private_teaching_marker"] = "must_not_be_copied"
        for item in modified["items"]:
            item["value"] += 999999
            item["private_teaching_marker"] = "must_not_be_copied"
        after = simulate_visibility(modified, 1, seed=0)
        self.assertEqual(before, after)
        self.assertNotIn("total_value", before)
        self.assertNotIn("truth", before)
        for item in before["items_visible"]:
            self.assertNotIn("value", item)
            if item["granularity"] == "quality":
                for key in ("row", "col", "width", "height", "cells"):
                    self.assertNotIn(key, item)

    def test_inspection_can_reveal_value_but_only_for_its_hits(self):
        result = simulate_visibility(self.fixture, 1, tool_schedule={1: "inspect2"})
        hits = set(result["tool_events"][0]["entities"])
        for item in result["items_visible"]:
            self.assertEqual("value" in item, item["entity"] in hits)
        self.assertEqual(len(hits), 2)

    def test_cross_source_duplicates_merge_but_tool_internal_draws_are_unique(self):
        result = simulate_visibility(self.fixture, 1, seed=0)
        event = result["tool_events"][0]
        self.assertEqual(len(event["entities"]), 4)
        self.assertEqual(len(set(event["entities"])), 4)
        self.assertIn("toy-01", event["already_visible"])
        item = next(x for x in result["items_visible"] if x["entity"] == "toy-01")
        self.assertEqual(item["granularity"], "outline")
        self.assertEqual(item["sources"], ["R1:skill", "R1:baoguang4"])
        self.assertEqual(len({x["entity"] for x in result["items_visible"]}), len(result["items_visible"]))

    def test_gold_scan_is_aggregate_only_and_zero_differs_from_missing(self):
        fourth = simulate_visibility(self.fixture, 4, tool_schedule={4: "goldscan"})
        self.assertEqual(fourth["q5_scan"], {"status": "known", "cells": 4})
        self.assertNotIn("q5", fourth["fixed_counts"])
        self.assertFalse(any(it["quality"] == 5 for it in fourth["items_visible"]))
        zero = copy.deepcopy(self.fixture)
        zero["items"] = [it for it in zero["items"] if it["quality"] != 5]
        zero["public_events"] = []
        missing = simulate_visibility(zero, 3, tool_schedule={4: "goldscan"})
        known_zero = simulate_visibility(zero, 4, tool_schedule={4: "goldscan"})
        self.assertEqual(missing["q5_scan"], {"status": "missing"})
        self.assertNotIn("q5", missing["fixed_cells"])
        self.assertEqual(known_zero["q5_scan"], {"status": "known", "cells": 0})
        self.assertEqual(known_zero["fixed_cells"]["q5"], 0)

    def test_public_wire_round_is_not_player_round(self):
        self.assertEqual(public_visible_round("villa", 2), 3)
        for family in ("shipwreck", "ranked", "hidden"):
            self.assertEqual(public_visible_round(family, 2), 1)
        second = simulate_visibility(self.fixture, 2, tool_schedule={})
        third = simulate_visibility(self.fixture, 3, tool_schedule={})
        self.assertEqual([x["semantic"] for x in second["public_facts"]], ["total_avg_cells"])
        self.assertEqual([x["semantic"] for x in third["public_facts"]], ["total_avg_cells", "q4_count"])
        with self.assertRaises(ValueError):
            public_visible_round("uncalibrated", 2)

    def test_integer_seed_is_repeatable_and_does_not_modify_input(self):
        original = copy.deepcopy(self.fixture)
        self.assertEqual(simulate_visibility(self.fixture, 5, seed=0), simulate_visibility(self.fixture, 5, seed=0))
        self.assertEqual(original, self.fixture)
        self.assertNotEqual(
            simulate_visibility(self.fixture, 1, seed=0)["tool_events"],
            simulate_visibility(self.fixture, 1, seed=1)["tool_events"],
        )
        with self.assertRaises(ValueError):
            simulate_visibility(self.fixture, 1, seed="0")

    def test_fixture_opt_in_and_geometry_are_checked(self):
        invalid = copy.deepcopy(self.fixture)
        invalid["synthetic"] = False
        with self.assertRaises(ValueError):
            simulate_visibility(invalid, 1)
        invalid = copy.deepcopy(self.fixture)
        invalid["items"][1]["col"] = 1
        with self.assertRaises(ValueError):
            simulate_visibility(invalid, 1)


class DemoTests(unittest.TestCase):
    def test_historical_aggregate_is_not_mislabeled_synthetic(self):
        result = demo()
        historical = result["historical_aggregate"]
        self.assertEqual(historical["kind"], "historical_aggregate")
        self.assertNotIn("synthetic", historical)
        self.assertEqual((historical["cases"], historical["value_cases"]), (80, 79))
        self.assertEqual(historical["paired"]["median_abs_quote_delta_pct"], 19.6)
        self.assertEqual(historical["paired"]["mean_abs_quote_delta_pct"], 31.4)
        self.assertIs(result["compensation_illustration"]["synthetic"], True)
        self.assertEqual(result["compensation_illustration"]["old_total_with_compensation"], 500)
        self.assertEqual(result["compensation_illustration"]["corrected_count_with_old_compensation"], 625)

    def test_json_entry_point_is_repeatable_across_hash_seeds(self):
        root = Path(__file__).resolve().parents[1]
        outputs = []
        for hash_seed in ("1", "98765"):
            env = {**os.environ, "PYTHONHASHSEED": hash_seed, "PYTHONIOENCODING": "utf-8"}
            completed = subprocess.run(
                [sys.executable, "-B", "-m", "research.aisha"], cwd=root,
                env=env, capture_output=True, check=True, timeout=10,
            )
            outputs.append(completed.stdout)
            self.assertEqual(completed.stderr, b"")
            self.assertEqual(json.loads(completed.stdout)["kind"], "research_demo")
        self.assertEqual(outputs[0], outputs[1])


if __name__ == "__main__":
    unittest.main()
