"""Opt-in research examples, outside auction_inference's stable API."""

from .replay_contract import evaluate_grid_example, live_teaching_snapshot, offline_teaching_snapshot
from .visibility import public_visible_round, simulate_visibility, toy_warehouse
from .windows import early_window_example

__all__ = [
    "early_window_example", "evaluate_grid_example", "live_teaching_snapshot",
    "offline_teaching_snapshot", "public_visible_round", "simulate_visibility", "toy_warehouse",
]
