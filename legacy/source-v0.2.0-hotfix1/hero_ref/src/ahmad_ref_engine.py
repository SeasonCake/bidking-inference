from __future__ import annotations

from dataclasses import dataclass, replace
from fractions import Fraction
from functools import lru_cache
import copy
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable, Iterable, Mapping

from layout_depth_policy import (
    LAYOUT_SPARSE_EARLY_SOFT_TARGET_NOTE_PREFIX,
    REF_QUOTE_SAFETY_TIER_NOTE,
    apply_aisha_geometry_grid_range,
    apply_draw_visible_anchor_grid_range,
    apply_count_implied_grid_floor,
    apply_count_implied_grid_range_floor,
    apply_deep_visibility_grid_range_anchor,
    apply_late_reveal_grid_residual_cap,
    apply_layout_band_widen_to_range,
    apply_layout_footroom_band_range_anchor,
    apply_layout_depth_hints,
    apply_grid_range_spread_cap,
    apply_shallow_visible_grid_low_floor,
    apply_shallow_flat_bottom_grid_range_tighten,
    ensure_aisha_floating_grid_minimum_spread,
    layout_depth_spec_for_hero,
    normalize_grid_cell_target,
    quote_safety_multipliers,
)
from draw_hero_policy import (  # side-hero count/value calibration (Phase-1 split)
    DRAW_HERO_COUNT_KEYS,
    DRAW_HERO_COUNT_SCALE,
    DRAW_HERO_COUNT_SCALE_BY_ROUND,
    DRAW_HERO_VALUE_SCALE,
    draw_hero_count_keys as _draw_hero_count_keys,
    draw_hero_count_scale as _draw_hero_count_scale,
    hero_value_scale as _policy_hero_value_scale,
)


def _project_root() -> Path:
    env_root = os.environ.get("BIDKING_LAB_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def _static_data_candidates() -> tuple[Path, ...]:
    env_static = os.environ.get("BIDKING_AHMAD_STATIC_DATA")
    candidates: list[Path] = []
    if env_static:
        candidates.append(Path(env_static).expanduser())
    relative = Path(
        "external_references",
        "AuctionAnalyzer4.13.3",
        "_decompiled",
        "MapBidCalculator",
        "MapBidCalculator",
        "Models",
        "StaticData.cs",
    )
    candidates.append(ROOT / relative)
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.append(Path(bundle_root) / relative)
        candidates.append(Path(bundle_root) / "StaticData.cs")
    candidates.append(Path.cwd() / relative)
    return tuple(candidates)


def _resolve_static_data() -> Path:
    for candidate in _static_data_candidates():
        try:
            if candidate.exists():
                return candidate.resolve()
        except OSError:
            continue
    return _static_data_candidates()[0]


ROOT = _project_root()
STATIC_DATA = _resolve_static_data()

QUALITY_KEYS = ("q1", "q3", "q4", "q5", "q6")
LOW_SPLIT_KEYS = ("white", "green")
LOW_QUALITY_NUMBER_TO_SPLIT = {"1": "white", "2": "green"}
LOW_SPLIT_ALIASES = {
    "1": "white",
    "q1": "white",
    "white": "white",
    "白": "white",
    "2": "green",
    "q2": "green",
    "green": "green",
    "绿": "green",
}
DEFAULT_ITEM_VALUES = {
    "q1": 400.0,
    "q3": 2500.0,
    "q4": 9045.5,
    "q5": 40000.0,
    "q6": 160000.0,
}
VALUE_UNCERTAINTY_CV = {
    "q1": 0.10,
    "q3": 0.18,
    "q4": 0.24,
    "q5": 0.32,
    "q6": 0.45,
}
VALUE_DISTRIBUTION_POINTS = (
    (-1.0, 0.125),
    (-0.5, 0.25),
    (0.0, 0.25),
    (0.5, 0.25),
    (1.0, 0.125),
)
DEFAULT_GRID_MEANS = {
    "q1": 2.2,
    "q3": 2.2,
    "q4": 2.4,
    "q5": 2.8,
    "q6": 3.2,
}
TOTAL_GRID_FROM_HIGH_TIER_CELLS_NOTE = "total_grid_target_from_known_high_tier_cells"
# Soft lower-bound variant (default ON; AISHA_DISABLE_SOFT_GRID_FLOOR_UNPINNED=1 rolls back): when a
# counted tier's footprint is unknown the total grid is NOT determined, so instead of a HARD pin we
# record the physical floor as a soft lower bound and let minimap/footroom estimate the range above
# it. Carries the floor value as a suffix, e.g. "total_grid_floor:96".
TOTAL_GRID_FLOOR_NOTE_PREFIX = "total_grid_floor:"
HIGH_TIER_CELL_KEYS = ("q3", "q4", "q5")
# Fallback cells/item when no tier avg_cells signal covers the residual items. Two regimes, because
# the residual (unknown) population differs by code path:
#   - HIGH-TIER GRID RAISE (count->grid, decision round, q3-q5 cells already known): the residual is
#     the few REMAINING unknowns, which skew large; empirically ~4.0 nails the truth on the batch-B
#     band samples (2501->140, 2401->113 exact). Keep 4.0 here.
#   - COUNT ANCHOR (grid->count, early/mid round, little fixed): the residual is the FULL drop mix,
#     dominated by small low tiers. Settled aisha warehouses (n=253, 10516 items) average
#     total_cells/total_count = 2.64 (per map-family 2.55-2.82; `scripts/audit_per_item_cells.py`).
#     4.0 over-counts cells/item by ~50% -> under-counts residual items -> the count systematically
#     under-shoots (live 2408 R2 grid 93 vs truth 119). Use 2.6 only on this path.
RESIDUAL_ITEM_CELL_ESTIMATE = 4.0
COUNT_ANCHOR_RESIDUAL_ITEM_CELL_ESTIMATE = 2.6
RESIDUAL_AVG_CELLS_NOTE = "total_grid_target_residual_avg_cells_estimate"
AISHA_LAYOUT_GRID_HINT_NOTE = "aisha_layout_grid_hint_shadow"
AISHA_LAYOUT_FOOTROOM_NOTE = "aisha_layout_grid_footroom_below_deepest"
AISHA_LAYOUT_FOOTROOM_MULT_NOTE = "aisha_layout_footroom_mult"
AISHA_LAYOUT_FOOTROOM_CAP_NOTE = "aisha_layout_footroom_capped"
AISHA_LAYOUT_FOOTROOM_SKIP_NOTE = "aisha_layout_footroom_skipped_not_undershoot"
AISHA_LAYOUT_FOOTROOM_SPARSE_NOTE = "aisha_layout_footroom_sparse_viewport"
AISHA_LAYOUT_BAND_WIDEN_DELTA_NOTE = "aisha_layout_band_widen_delta"
AISHA_LAYOUT_BAND_WIDEN_APPLIED_NOTE = "aisha_layout_band_widen_applied"
AISHA_LAYOUT_APPLICATION_MODE_NOTE = "aisha_layout_application_mode"
VALID_AISHA_LAYOUT_MODES = frozenset({"off", "target", "shadow", "band"})
DEFAULT_AISHA_LAYOUT_MODE = "off"
AISHA_LIVE_LAYOUT_MODE = "band"
AISHA_D1_SHADOW_Q6_DISCOUNT_NOTE = "aisha_d1_shadow_q6_discount"
AISHA_D1_APPLY_Q6_DISCOUNT_NOTE = "aisha_d1_apply_q6_discount"
AISHA_RED_PRIOR_CAP_NOTE = "aisha_red_prior_value_cap"
VALID_AISHA_D1_MODES = frozenset({"off", "shadow", "apply"})
DEFAULT_AISHA_D1_MODE = "off"
AISHA_LIVE_D1_MODE = "shadow"
SPARSE_LAYOUT_HERO_KEYS = frozenset({"raven", "sophie", "gabriela"})
LAYOUT_DEPTH_HERO_KEYS = frozenset({"aisha", *SPARSE_LAYOUT_HERO_KEYS})
PINNED_QUALITY_CELLS_SPARSE_PRIOR_NOTE = "pinned_quality_cells_sparse_prior"
AISHA_WAREHOUSE_ROWS = 18
AISHA_GRID_COLUMNS = 10
AISHA_LAYOUT_MIN_ROUND = 3
AISHA_LAYOUT_WHITE_ONLY_MAX_ROUND = 3
AISHA_EARLY_ROUND_MAX_COMBOS = 2500
AISHA_EARLY_VIEWPORT_GRID_HINT_NOTE = "aisha_early_viewport_grid_hint"
AISHA_EARLY_ROUND_LIGHTWEIGHT_NOTE = "aisha_early_round_lightweight"
AISHA_EARLY_ROUND_COUNT_PRIOR_WINDOW_NOTE = "aisha_early_round_count_prior_window"
AISHA_ENGINE_PASS_SKILL = "skill"
AISHA_ENGINE_PASS_ITEM = "item"
AISHA_LAYOUT_DEEPEST_ROW_THRESHOLD = 12
HARD_TOTAL_GRID_SOURCE_NOTES = frozenset(
    {
        "structured_ref_bridge_total_cells",
        "field_update_total_cells",
        "public_total_cells",
        "action_100103_total_cells",
        "settlement_review_total_grid",
        TOTAL_GRID_FROM_HIGH_TIER_CELLS_NOTE,
    }
)
DISPLAY_GRID_TOPK = 3
QUALITY_TO_INDEX = {"q1": (0, 1), "q3": (2,), "q4": (3,), "q5": (4,), "q6": (5,)}
QUALITY_TO_TIER_INDEX = {"q1": (0, 1), "q3": (2,), "q4": (3,), "q5": (4,), "q6": (5,)}
QUALITY_NUM_TO_KEY = {"1": "q1", "2": "q1", "3": "q3", "4": "q4", "5": "q5", "6": "q6"}
ACTION_AVG_CELLS = {
    "100110": "q1",
    "100111": "q3",
    "100112": "q4",
    "100113": "q5",
    "100114": "q6",
    "1002041": "q5",
    "1002042": "q4",
    "1002043": "q3",
}
ACTION_TOTAL_CELLS = {
    "100104": "q1",
    "100105": "q3",
    "100106": "q4",
    "100107": "q5",
    "100108": "q6",
}
ACTION_VALUE_SUM = {
    "100122": "q1",
    "100123": "q3",
    "100124": "q4",
    "100125": "q5",
    "100126": "q6",
}
ACTION_COUNTS = {
    "100116": "q1",
    "100117": "q3",
    "100118": "q4",
    "100119": "q5",
    "100120": "q6",
    "1002044": "q1",
}
ACTION_DIAGNOSTIC_ONLY = {
    "100121": "total_value",
    "100127": "all_items",
    "100134": "all_item_quality",
}
QUALITY_REVEAL_ACTION_IDS = frozenset(
    {
        "100127",
        "100134",
        "100135",
        "100136",
        "100137",
        "100138",
        "100139",
        "100140",
    }
)
ALL_ITEM_QUALITY_PUBLIC_INFO_IDS = frozenset({200004, 200030})
RAVEN_ALL_ITEM_QUALITY_SKILL_ID = 100301
RAVEN_HERO_ID = 301
MARIA_HERO_ID = 108
MARIA_SKILL_QUALITY_REVEAL_ID = 10010801
ETHAN_HERO_ID = 208
ETHAN_SKILL_R1_OUTLINE = 1002081
ETHAN_QUALITY_OUTLINE_SKILL_IDS = frozenset({1002082, 1002083, 1002084})
ETHAN_SKILL_FULL_OUTLINE = 1002085
MIRROR_EYE_ACTION_ID = 100134
MARIA_SKILL_VALUE_BY_ID = {
    "100108": "q1",
    "10010802": "q2",
    "10010803": "q3",
}
PUBLIC_AVG_CELLS = {
    "q4_avg_cells": "q4",
    "q5_avg_cells": "q5",
    "q6_avg_cells": "q6",
}
PUBLIC_COUNTS = {
    "q4_item_count": "q4",
    "q5_item_count": "q5",
    "q6_item_count": "q6",
}
PUBLIC_AVG_VALUES = {
    "q4_avg_value": "q4",
    "q5_avg_value": "q5",
    "q6_avg_value": "q6",
}
PUBLIC_BUCKET_OUTLINE_QUALITY = {
    200001: "q4",
    200002: "q5",
    200003: "q6",
}
PUBLIC_EXACT_QUALITY_CELLS_INFO = {
    200010: "q4",
    200011: "q5",
    200012: "q6",
}
PUBLIC_EXACT_QUALITY_COUNT_INFO = {
    200018: "q4",
    200019: "q5",
    200020: "q6",
}
HERO_BY_ID = {
    101: "fatima",
    102: "chenmei",
    103: "aisha",
    104: "gabriela",
    105: "tatiana",
    106: "naomi",
    107: "sophie",
    108: "maria",
    109: "helena",
    110: "isabella",
    201: "george",
    202: "carlos",
    203: "leonard",
    204: "ahmed",
    205: "ivan",
    206: "takeda",
    207: "wuqilin",
    208: "ethan",
    209: "victor",
    301: "raven",
}
HERO_ALIASES = {
    "fatima": "fatima",
    "法蒂玛": "fatima",
    "chenmei": "chenmei",
    "陈美": "chenmei",
    "aisha": "aisha",
    "艾莎": "aisha",
    "gabriela": "gabriela",
    "加布里埃拉": "gabriela",
    "tatiana": "tatiana",
    "塔蒂安娜": "tatiana",
    "naomi": "naomi",
    "娜奥米": "naomi",
    "sophie": "sophie",
    "索菲": "sophie",
    "maria": "maria",
    "玛丽亚": "maria",
    "helena": "helena",
    "海琳娜": "helena",
    "isabella": "isabella",
    "伊莎贝拉": "isabella",
    "伊萨贝拉": "isabella",
    "george": "george",
    "乔治": "george",
    "carlos": "carlos",
    "卡洛斯": "carlos",
    "leonard": "leonard",
    "莱昂纳德": "leonard",
    "ahmad": "ahmed",
    "ahmed": "ahmed",
    "ahamed": "ahmed",
    "艾哈": "ahmed",
    "艾哈迈德": "ahmed",
    "ivan": "ivan",
    "伊万": "ivan",
    "takeda": "takeda",
    "武田宏志": "takeda",
    "wuqilin": "wuqilin",
    "吴起灵": "wuqilin",
    "ethan": "ethan",
    "伊森": "ethan",
    "victor": "victor",
    "维克": "victor",
    "维克托": "victor",
    "raven": "raven",
    "拉文": "raven",
}
SUPPORTED_REF_HERO_KEYS = frozenset(HERO_BY_ID.values())
STRUCTURED_REF_HERO_KEYS = frozenset({"aisha", "ahmed", "victor"})
PUBLIC_MAX_QUALITY_INFO_ID = 200048
PUBLIC_MAX_QUALITY_SKILL_IDS = frozenset({"100110"})
# 至宝估价: reveals the value of one item from the session's highest quality tier.
TREASURE_HIGHEST_ITEM_VALUE_ACTION_IDS = frozenset({"100163"})
QUALITY_TIER_NUMBER = {"q1": 2, "q3": 3, "q4": 4, "q5": 5, "q6": 6}


# Aisha dynamic middle (balanced) tier. Red/gold are invisible to aisha, so per-vault red is
# unobservable -- but total_cells (well-estimated) correlates with red richness (audit
# audit_red_richness_signals.py: corr +0.41 w/ q6 count; rich tertile ~141 cells has 2x the reds of
# poor ~76). So the balanced bid percentile slides p50(poor)->p75(rich) by observed cell-richness:
# lean conservative on poor vaults (avoid phantom-red over-bid, e.g. 2501) and lean expected on rich
# vaults (fix the ~220k systematic under-bid). conservative=p25 / aggressive=p90 bracket the residual
# invisible-red uncertainty. AISHA-ONLY (isolation): other heroes keep balanced=p50, aggressive=p75.
# Env-overridable for the tuning sweep; defaults are the committed calibration.
# Calibrated by param sweep (audit_red_richness_signals + robust-expected, 2026-06-18, n=119):
# pct 0.55->0.85 over cells 70->125 lands the balanced central at ~-10k bias vs robust-expected
# (baseline p50 was -144k) with the lowest MAE. The D1 phantom-red cap still protects poor vaults.
AISHA_MIDDLE_PCT_LO = 0.55
# F2 (2026-06-19, workflow-validated): HI 0.85->0.75. The p85 slide was inflating cheap-but-cell-rich
# vaults (cells != value -> p85 of the value distribution picks a high-red combo). 0.75 is the knee:
# trims rich-vault over-bid while keeping the genuine under-bid rescue. Env AISHA_MIDDLE_PCT_HI=0.85
# restores the old slide.
AISHA_MIDDLE_PCT_HI = 0.75
AISHA_RICHNESS_CELLS_LO = 70.0
AISHA_RICHNESS_CELLS_HI = 125.0
# Over-bid fixes (2026-06-19, full-library + user-export A/B). All value-only, default ON, env-reversible.
# F1: pure-prior red (zero q6 evidence). On the ~13% where true red is also 0 it is 100% phantom and
#     +144% over (user's 2402/2409 = +170%). Damp two ways: (a) clamp the balanced percentile to LO at
#     the call site (don't slide to p85 on phantom red), (b) hard-trim the red VALUE tail to this keep.
#     keep=0.30 is the knee: kills the disasters yet preserves the median for the 56% of pure-prior
#     games that DO carry real medium/big red (under-bidding those is the user-accepted hidden-big-red).
AISHA_PURE_PRIOR_RED_KEEP = 0.30
# Per-family override of the keep above (asymmetric phantom-red damp). Format
# "24:0.05,25:0.30,45:0.30" (family=map_id//100); empty/unset = flat keep (no-op default).
# See _aisha_pure_prior_red_keep. The over-value lever is COUNT/phantom-red on LOW-red maps,
# NOT per-cell red price (locate: engine per-cell ~60k ~= truth) -> damp by map red base-rate.
AISHA_PURE_PRIOR_RED_KEEP_BY_FAMILY = "AISHA_PURE_PRIOR_RED_KEEP_BY_FAMILY"
AISHA_DISABLE_PURE_PRIOR_RED_DAMP = "AISHA_DISABLE_PURE_PRIOR_RED_DAMP"  # disables the percentile clamp
# Conservative(保守) tier = the user's SAFE floor. The phantom red COUNT center-regression shifts the
# WHOLE value distribution up, so even p25 carries unseen red -> on no-red vaults 保守 over-states truth
# every time (11/11 truth-red=0: 保守 > truth, up to 6.6x; the felt 把把过估). The keep lever (F1) only
# trims the upper red tail, not p25. Fix per tier semantics: the SAFE floor assumes the red you CANNOT
# see isn't there -> strip the conservative red component on pure-prior red (zero q6 evidence). 参考/激进
# keep their red optimism for the ~79% of pure-prior games that do carry real (acceptable-hidden) red.
# keep = fraction of the median red (rv50) LEFT in the conservative tier (strip = 1-keep). keep=0.25
# (strip 75% of rv50) is the library knee: on the 11 truth-red=0 samples it lands 保守 at median 0.94x
# truth (a true safe floor with margin), 0/11 over-stripped below half-truth, and cuts 保守>truth from
# 11/11 to 4/11 (the residual 4 are ALSO non-red/count over -- a separate count-prior fix, NOT red).
# keep=0.0 fully strips but over-shoots 3/11 below half-truth. Value-only, conservative-tier-only,
# pure-prior-only, aisha-only. Rollback: AISHA_DISABLE_CONS_PURE_PRIOR_RED_STRIP=1.
AISHA_CONS_PURE_PRIOR_RED_KEEP = 0.25
AISHA_DISABLE_CONS_PURE_PRIOR_RED_STRIP = "AISHA_DISABLE_CONS_PURE_PRIOR_RED_STRIP"
# Red-evidence ramp on the two pure-prior keeps above. Both keeps are tuned to kill a phantom
# SINGLE red on LOW-red vaults, but the SAME flat keep halves a GENUINE red on high-red vaults
# (eval R4: red-bucket 4+ 参考=0.50x truth, 保守=-922k, while 激进 is ~on truth; user: "阈值压太狠").
# The engine's own geometric q6 COUNT estimate tracks true red even though red is invisible to
# Aisha (eval red-bucket 0/1/2-3/4+ -> q6 count mid ~1/2/4/5), so ramp the keep UP toward a ceiling
# as that estimate rises: a low estimate keeps the phantom suppression (low-red over-est stays
# protected), a high estimate trusts the real red (high-red stops being halved). Rollback:
# AISHA_DISABLE_RED_KEEP_RAMP=1.
# Tuned on valid_aisha R4 + activity captures (122 samples; detailed by-bucket eval with MAE +
# over-est rate, 2026-06-21). LO=4 (not 3) is the better trade: it keeps the full high-red fix
# (red4+ 参/真 0.47->0.75, MAE 695k->481k) but fires far less on the MID buckets the ramp was
# over-correcting — red1 over-est 24%->14% (back to baseline), red2-3 35%->29%, red0 back to 1.52.
# Per 口径 (over-est is the enemy) the lower collateral over-est wins. q6-count estimate by truth
# bucket: red 0/1/2-3/4+ -> ~1/2/4/5, so LO=4 leaves 2-3 (~4) ~untouched and lifts only 4+ (~5).
# Env-overridable (AISHA_RED_KEEP_RAMP_LO/HI/TOP) for re-tuning without a code change.
AISHA_RED_KEEP_RAMP_LO = 4.0   # q6-count estimate at/below which keep stays at its flat base
AISHA_RED_KEEP_RAMP_HI = 5.0   # q6-count estimate at/above which keep reaches the ceiling
AISHA_RED_KEEP_RAMP_TOP = 0.90  # keep ceiling at a high red-count estimate
AISHA_DISABLE_RED_KEEP_RAMP = "AISHA_DISABLE_RED_KEEP_RAMP"
# F3: evidenced red (has q6 evidence) is still over-valued (catalog red price over-prices low-value
#     small red ~2.4x; flat ~62k/cell over-prices big sunk red 1.24-1.35x). Trim the red tail to this
#     keep, but EXEMPT true jackpots (rv50 > cap) = the acceptable hidden-big-red. cap is library-fit
#     (knowable-red n=10), keep env-tunable; recheck via eval_aisha_red_knowability after map changes.
AISHA_EVID_RED_KEEP = 0.75
AISHA_EVID_RED_JACKPOT_CAP = 800000.0
AISHA_DISABLE_EVID_RED_KEEP = "AISHA_DISABLE_AISHA_EVID_RED_KEEP"


def _aisha_middle_percentile(grid_mid: float | None) -> tuple[float, float]:
    """Return (middle_percentile, richness) from the (well-estimated) total-cell count."""
    lo_p = float(os.environ.get("AISHA_MIDDLE_PCT_LO") or AISHA_MIDDLE_PCT_LO)
    hi_p = float(os.environ.get("AISHA_MIDDLE_PCT_HI") or AISHA_MIDDLE_PCT_HI)
    c_lo = float(os.environ.get("AISHA_RICHNESS_CELLS_LO") or AISHA_RICHNESS_CELLS_LO)
    c_hi = float(os.environ.get("AISHA_RICHNESS_CELLS_HI") or AISHA_RICHNESS_CELLS_HI)
    if grid_mid is None or c_hi <= c_lo:
        return lo_p, 0.0
    richness = max(0.0, min(1.0, (float(grid_mid) - c_lo) / (c_hi - c_lo)))
    return lo_p + (hi_p - lo_p) * richness, richness


@dataclass(frozen=True)
class RefEvidence:
    hero: str
    map_id: int | None
    phase: str
    total_count: int | None
    fixed_counts: dict[str, int]
    min_counts: dict[str, int]
    count_sums: dict[str, int]
    avg_cells: dict[str, float]
    quality_cells: dict[str, float]
    quality_cell_floors: dict[str, float]
    avg_values: dict[str, float]
    quality_values: dict[str, float]
    quality_value_floors: dict[str, float]
    quality_value_floor_item_counts: dict[str, int]
    split_counts: dict[str, int]
    split_quality_cells: dict[str, float]
    split_avg_cells: dict[str, float]
    random_value_floors: tuple[tuple[int, float], ...]
    total_grid_target: float | None
    # Public q4/q5/q6 avg_cells before that tier's count is locked: soft prior only.
    soft_avg_cell_keys: frozenset[str]
    # Public q4/q5/q6 avg_value before that tier's count is locked: soft prior only.
    soft_avg_value_keys: frozenset[str]
    v3_conservative: str
    v3_balanced: str
    v3_aggressive: str
    source_notes: tuple[str, ...]


@dataclass(frozen=True)
class RefCombo:
    counts: dict[str, int]
    grids: dict[str, float]
    value: float
    weight: float
    total_grid: float


# P2 total-count confidence. Per-sample signals (range width, reveal completeness) do NOT predict
# total-count error (calibrate probe 2026-06-17: corr ~ -0.8..0); error is dominated by hero-class
# inherent variance. So confidence is a hero-class label + an empirical 1-sigma error band.
# Dual-track std source (real-primary + synthetic-fill): REAL = measured 1-sigma of
# (engine total - truth) on the real sample lib (scripts/audit_hero_total_count_bias.py); SYNTH =
# synthetic draw-class std (scripts/synthesize_draw_hero_samples.py, pooled sophie+gabriela = 13.7)
# used only for draw-heroes with no/too-few real samples (raven has 0). Draw-heroes are inherently
# low-confidence regardless of what they reveal; a locked total (e.g. ahmed public count) is high.
_TOTAL_COUNT_ERROR_STD = {
    "ahmed": 2.3,  # REAL (n=538)
    "victor": 4.8,  # REAL (n=7, thin; not draw-class so no synthetic fill available)
    "aisha": 7.5,  # REAL (n=253)
    "isabella": 8.4,  # REAL (n=6, thin; not draw-class so no synthetic fill available)
    "ethan": 8.6,  # REAL (n=161)
    "wuqilin": 8.0,  # REAL (n=20)
    "raven": 13.7,  # SYNTH draw-class (0 real samples)
    "gabriela": 14.2,  # REAL (n=50)
    "sophie": 14.1,  # REAL (n=28)
}
_DEFAULT_TOTAL_COUNT_ERROR_STD = 9.0


def _total_count_confidence_fields(
    evidence: "RefEvidence",
    quality_count_ranges: dict[str, tuple[int | None, int | None, int | None]],
) -> dict[str, Any]:
    hero = normalize_hero_key(evidence.hero)
    if evidence.total_count is not None:
        label, std = "high", 1.0  # total locked to a revealed/public exact count
    else:
        std = _TOTAL_COUNT_ERROR_STD.get(hero, _DEFAULT_TOTAL_COUNT_ERROR_STD)
        if hero in DRAW_HERO_COUNT_KEYS:
            label = "low"
        elif std <= 5.0:
            label = "high"
        else:
            label = "medium"
    total_mid = sum(
        int((quality_count_ranges.get(q) or (0, 0, 0))[1] or 0) for q in QUALITY_KEYS
    )
    band = [max(0, total_mid - round(std)), total_mid + round(std)] if total_mid > 0 else None
    return {
        "total_count_confidence": label,
        "total_count_estimate": total_mid or None,
        "total_count_error_band": band,
        "total_count_error_std": round(std, 1),
    }


@dataclass(frozen=True)
class RefResult:
    status: str
    source: str
    conservative: int | None
    balanced: int | None
    aggressive: int | None
    value_p25: int | None
    value_p50: int | None
    value_p75: int | None
    combo_count: int
    red_count_range: tuple[int | None, int | None, int | None]
    red_cells_range: tuple[int | None, int | None, int | None]
    red_value_range: tuple[int | None, int | None, int | None]
    quality_count_ranges: dict[str, tuple[int | None, int | None, int | None]]
    quality_cells_ranges: dict[str, tuple[int | None, int | None, int | None]]
    total_grid_range: tuple[int | None, int | None, int | None]
    notes: tuple[str, ...]
    evidence: RefEvidence

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "source": self.source,
            "conservative": self.conservative,
            "balanced": self.balanced,
            "aggressive": self.aggressive,
            "value_p25": self.value_p25,
            "value_p50": self.value_p50,
            "value_p75": self.value_p75,
            "combo_count": self.combo_count,
            "red_count_range": list(self.red_count_range),
            "red_cells_range": list(self.red_cells_range),
            "red_value_range": list(self.red_value_range),
            "quality_count_ranges": {
                key: list(value) for key, value in self.quality_count_ranges.items()
            },
            "quality_cells_ranges": {
                key: list(value) for key, value in self.quality_cells_ranges.items()
            },
            "total_grid_range": list(self.total_grid_range),
            "notes": list(self.notes),
            **_total_count_confidence_fields(self.evidence, self.quality_count_ranges),
            "evidence": {
                "hero": self.evidence.hero,
                "map_id": self.evidence.map_id,
                "phase": self.evidence.phase,
                "total_count": self.evidence.total_count,
                "fixed_counts": dict(self.evidence.fixed_counts),
                "min_counts": dict(self.evidence.min_counts),
                "count_sums": dict(self.evidence.count_sums),
                "avg_cells": dict(self.evidence.avg_cells),
                "quality_cells": dict(self.evidence.quality_cells),
                "quality_cell_floors": dict(self.evidence.quality_cell_floors),
                "avg_values": dict(self.evidence.avg_values),
                "quality_values": dict(self.evidence.quality_values),
                "quality_value_floors": dict(self.evidence.quality_value_floors),
                "quality_value_floor_item_counts": dict(
                    self.evidence.quality_value_floor_item_counts
                ),
                "split_counts": dict(self.evidence.split_counts),
                "split_quality_cells": dict(self.evidence.split_quality_cells),
                "split_avg_cells": dict(self.evidence.split_avg_cells),
                "random_value_floors": [
                    [sample_count, value_floor]
                    for sample_count, value_floor in self.evidence.random_value_floors
                ],
                "total_grid_target": self.evidence.total_grid_target,
                "soft_avg_cell_keys": sorted(self.evidence.soft_avg_cell_keys),
                "soft_avg_value_keys": sorted(self.evidence.soft_avg_value_keys),
                "source_notes": list(self.evidence.source_notes),
            },
        }


def _safe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def normalize_hero_key(hero: Any) -> str:
    text = str(hero or "").strip()
    if not text or text.lower() in {"?", "unknown", "none", "null"}:
        return ""
    return HERO_ALIASES.get(text.lower(), HERO_ALIASES.get(text, text.lower()))


def is_supported_ref_hero(hero: Any) -> bool:
    return normalize_hero_key(hero) in SUPPORTED_REF_HERO_KEYS


def _hero_from_context(
    hero: Any,
    *hero_id_candidates: Any,
) -> str:
    text = str(hero or "").strip()
    if text and text.lower() not in {"?", "unknown", "none", "null"}:
        return normalize_hero_key(text) or text
    for candidate in hero_id_candidates:
        hero_id = _safe_int(candidate)
        if hero_id in HERO_BY_ID:
            return HERO_BY_ID[hero_id]
    return text


def _is_unknown_hero(value: Any) -> bool:
    return str(value or "").strip().lower() in {"", "?", "unknown", "none", "null"}


def _dig(value: Any, *path: str, default: Any = None) -> Any:
    current = value
    for part in path:
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def _parse_quality_kv(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for part in str(text or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        parsed = _safe_int(value)
        if parsed is not None:
            out[key.strip()] = parsed
    return out


def _settlement_quality_truth(snapshot: dict[str, Any]) -> tuple[dict[str, int], dict[str, int]]:
    raw_counts = _parse_quality_kv(str(snapshot.get("final_quality_counts") or ""))
    if not raw_counts:
        return {}, {}
    counts = {
        "q1": (_safe_int(raw_counts.get("q1") or 0) or 0)
        + (_safe_int(raw_counts.get("q2") or 0) or 0),
        "q3": _safe_int(raw_counts.get("q3")) or 0,
        "q4": _safe_int(raw_counts.get("q4")) or 0,
        "q5": _safe_int(raw_counts.get("q5")) or 0,
        "q6": _safe_int(raw_counts.get("q6")) or 0,
    }
    raw_cells = _parse_quality_kv(str(snapshot.get("final_quality_cells") or ""))
    cells: dict[str, int] = {}
    if raw_cells:
        cells = {
            "q1": (_safe_int(raw_cells.get("q1") or 0) or 0)
            + (_safe_int(raw_cells.get("q2") or 0) or 0),
            "q3": _safe_int(raw_cells.get("q3")) or 0,
            "q4": _safe_int(raw_cells.get("q4")) or 0,
            "q5": _safe_int(raw_cells.get("q5")) or 0,
            "q6": _safe_int(raw_cells.get("q6")) or 0,
        }
    return counts, cells


def _apply_settlement_quality_truth(
    snapshot: dict[str, Any],
    *,
    fixed_counts: dict[str, int],
    min_counts: dict[str, int],
    avg_cells: dict[str, float],
    quality_cells: dict[str, float],
    source_notes: list[str],
) -> None:
    counts, cells = _settlement_quality_truth(snapshot)
    if not counts:
        return
    for key, value in counts.items():
        existing = fixed_counts.get(key)
        if existing is not None and int(existing) != int(value):
            source_notes.append("settlement_review_quality_counts_overrode_live")
        fixed_counts[key] = int(value)
        min_counts[key] = int(value)
    if cells:
        for key, value in cells.items():
            existing_cells = quality_cells.get(key)
            if existing_cells is not None and abs(float(existing_cells) - float(value)) > 0.0001:
                source_notes.append("settlement_review_quality_cells_overrode_live")
            quality_cells[key] = float(value)
            avg_cells.pop(key, None)
    source_notes.append("settlement_review_final_quality_truth")


def _append_source_note_once(source_notes: list[str], note: str) -> None:
    if note not in source_notes:
        source_notes.append(note)


def _hard_total_grid_target_from_notes(
    total_grid_target: float | None,
    source_notes: Iterable[str],
) -> int | None:
    if total_grid_target is None:
        return None
    notes = set(source_notes)
    # Sparse-early (draw heroes: sophie/gabriela/raven) soft target is a CAP to curb early-round
    # over-estimation from sparse reveals, NOT a settled total. Enforcing it as a hard grid equality
    # blanks deep vaults: when the visible deepest row is 13 no count combo's grid can equal a cap of
    # ~35 -> _grids_for_counts rejects every combo -> no_reachable_combo (sophie 2403: blank instead
    # of ~124). Leave the grid free so apply_draw_visible_anchor_grid_range can own the range from the
    # deepest VISIBLE row. Gated by the SAME flag as that anchor so DISABLE_DRAW_VISIBLE_ANCHOR=1 is a
    # single clean rollback to the old hard-capped behavior (without it, anchor-off would strip the cap
    # AND the anchor, leaving shallow draw vaults unconstrained).
    if (
        os.environ.get("DISABLE_DRAW_VISIBLE_ANCHOR") != "1"
        and any(n.startswith(LAYOUT_SPARSE_EARLY_SOFT_TARGET_NOTE_PREFIX) for n in notes)
    ):
        return None
    if TOTAL_GRID_FROM_HIGH_TIER_CELLS_NOTE in notes:
        rounded = int(round(float(total_grid_target)))
        if abs(float(total_grid_target) - rounded) > 0.25:
            return None
        return max(0, rounded)
    if "public_total_avg_cells_target" in notes:
        if not any(note in HARD_TOTAL_GRID_SOURCE_NOTES for note in notes):
            return None
    rounded = int(round(float(total_grid_target)))
    if abs(float(total_grid_target) - rounded) > 0.25:
        return None
    return max(0, rounded)


def _exact_integer_quality_cell(raw: Any) -> int | None:
    parsed = _safe_float(raw)
    if parsed is None:
        return None
    rounded = int(round(float(parsed)))
    if abs(float(parsed) - rounded) > 0.0001:
        return None
    return rounded


def _estimated_tier_grid_cells(
    key: str,
    count: int,
    *,
    avg_cells: dict[str, float],
    quality_cells: dict[str, float],
) -> int | None:
    if count <= 0:
        return 0
    exact = _exact_integer_quality_cell(quality_cells.get(key))
    if exact is not None:
        return exact
    avg = avg_cells.get(key)
    if avg is not None and avg > 0:
        options = _avg_grid_options(int(count), float(avg))
        if len(options) == 1:
            return int(options[0])
        if options:
            default = count * DEFAULT_GRID_MEANS[key]
            return int(min(options, key=lambda option: (abs(option - default), option)))
    options = _composable_grid_options(int(count))
    if options:
        default = count * DEFAULT_GRID_MEANS[key]
        return int(min(options, key=lambda option: (abs(option - default), option)))
    return int(count)


def _residual_per_item_cell_estimate(
    *,
    fixed_counts: dict[str, int],
    avg_cells: dict[str, float],
    source_notes: list[str],
    quality_probs: dict[str, float] | None = None,
    fallback: float = RESIDUAL_ITEM_CELL_ESTIMATE,
) -> float:
    """Expected cells/item of the still-unknown (residual) items.

    The residual items follow the FULL drop distribution restricted to the unfixed qualities. An
    UNWEIGHTED mean across those qualities over-counts cells/item, because the physically-large high
    tiers (q5 3.4, q6 3.8 cells) weigh as much as the frequent small low tiers (q1 1.9, q3 2.6) even
    though low tiers dominate the item mix (q1 27%, q3 29% of items vs q6 7%). Weighting each unfixed
    quality's avg_cells by its drop probability collapses the unweighted 2.88 to the item-weighted
    truth 2.64 when all tiers are unknown, yet correctly stays high (~3.5) when only q5/q6 remain
    unfixed -- there the residual genuinely IS high-tier (`scripts/audit_per_item_cells.py`).
    """
    unfixed_keys = [key for key in QUALITY_KEYS if fixed_counts.get(key) is None]
    signals = [
        (key, float(avg_cells[key]))
        for key in unfixed_keys
        if key in avg_cells and float(avg_cells[key]) > 0
    ]
    if not signals:
        return fallback
    if quality_probs:
        weight_sum = sum(max(0.0, float(quality_probs.get(key, 0.0))) for key, _ in signals)
        if weight_sum > 0:
            estimate = (
                sum(value * max(0.0, float(quality_probs.get(key, 0.0))) for key, value in signals)
                / weight_sum
            )
            _append_source_note_once(source_notes, RESIDUAL_AVG_CELLS_NOTE)
            return estimate
    estimate = sum(value for _, value in signals) / len(signals)
    _append_source_note_once(source_notes, RESIDUAL_AVG_CELLS_NOTE)
    return estimate


def _apply_total_grid_target_from_known_high_tier_cells(
    *,
    total_count: int | None,
    total_grid_target: float | None,
    fixed_counts: dict[str, int],
    quality_cells: dict[str, float],
    avg_cells: dict[str, float],
    source_notes: list[str],
) -> float | None:
    """Raise soft/missing total grid target using known q3–q5 cells + residual items."""
    if total_count is None or int(total_count) <= 0:
        return total_grid_target
    if any(note in HARD_TOTAL_GRID_SOURCE_NOTES for note in source_notes):
        return total_grid_target

    known_high = 0
    for key in HIGH_TIER_CELL_KEYS:
        if _exact_integer_quality_cell(quality_cells.get(key)) is not None:
            known_high += 1
    if known_high < 2:
        return total_grid_target

    known_cells_total = 0
    for key in QUALITY_KEYS:
        exact = _exact_integer_quality_cell(quality_cells.get(key))
        if exact is not None:
            known_cells_total += exact

    fixed_sum = sum(max(0, int(value)) for value in fixed_counts.values())
    residual_items = int(total_count) - fixed_sum
    if residual_items < 0:
        return total_grid_target

    if residual_items == 0:
        inferred = 0
        for key in QUALITY_KEYS:
            count = fixed_counts.get(key)
            if count is None:
                return total_grid_target
            tier_cells = _estimated_tier_grid_cells(
                key,
                int(count),
                avg_cells=avg_cells,
                quality_cells=quality_cells,
            )
            if tier_cells is None:
                return total_grid_target
            inferred += tier_cells
    else:
        # A quality whose CELLS are known but whose COUNT is unfixed is already fully represented in
        # known_cells_total; its items must be removed from the residual or they are counted twice --
        # once as their known cells and again via residual*per_item. (2406 R3: q4=26 cells known but
        # count unfixed -> 26 + ~9 items*4.0 double-counts purple, inflating the grid to 143 vs the
        # 105 truth.) Estimate those items from the per-quality cell mean and drop them from residual.
        known_cells_unfixed_items = 0
        for key in QUALITY_KEYS:
            exact = _exact_integer_quality_cell(quality_cells.get(key))
            if exact is not None and key not in fixed_counts:
                mean = DEFAULT_GRID_MEANS.get(key, 2.4)
                base = max(1, int(round(exact / mean)))
                # Tiny-gold/red: a q5/q6 tier whose KNOWN cells are small (exact < 2*mean) is many
                # 1-cell items, not a few mean-sized ones; round(exact/mean) then UNDER-counts them,
                # so too few items leave the residual and the leftover inflates it at ~4.0 cells/item
                # (2408 R3: gold 5 cells = 5 tiny items, counted as 2 -> residual 6 vs 3 -> hard total
                # 81 / value 974k vs truth grid 66 / value 555k, which locks the displayed 估总格).
                # Count one item per known cell so they fully leave the residual. NARROW: only fires on
                # an unfixed-count high tier with small known cells (2406/2501/2401 lack it -> untouched).
                # Rollback AISHA_DISABLE_TINY_HIGHTIER_RESIDUAL=1.
                if (
                    key in ("q5", "q6")
                    and float(exact) < 2.0 * mean
                    and os.environ.get("AISHA_DISABLE_TINY_HIGHTIER_RESIDUAL") != "1"
                ):
                    base = int(exact)
                known_cells_unfixed_items += base
        effective_residual = max(0, residual_items - known_cells_unfixed_items)
        per_item = _residual_per_item_cell_estimate(
            fixed_counts=fixed_counts,
            avg_cells=avg_cells,
            source_notes=source_notes,
        )
        inferred = known_cells_total + int(round(effective_residual * per_item))

    # Physical floor: every item occupies >=1 cell, so total cells >= Σknown_cells + (items NOT
    # already accounted for by a known-cell tier). The prior floor used `residual_items` (= total -
    # Σfixed), which WRONGLY drops a tier whose count is FIXED but whose cells are UNKNOWN (e.g. gold
    # locked by a count-only scan 200019): those items are neither in known_cells_total nor in
    # residual_items, so the derived HARD target could sit below the physical minimum and make every
    # combo infeasible → no_reachable_combo (2410 R4: gold 7 items dropped, target 92 < floor 96,
    # truth 103). Counting items by their known-cell tier instead closes that gap.
    items_with_known_cells = 0
    for key in QUALITY_KEYS:
        exact = _exact_integer_quality_cell(quality_cells.get(key))
        if exact is None:
            continue
        fixed = fixed_counts.get(key)
        if fixed is not None:
            items_with_known_cells += max(0, int(fixed))
        else:
            mean = DEFAULT_GRID_MEANS.get(key, 2.4)
            items_with_known_cells += max(1, int(round(exact / mean)))
    physical_floor = known_cells_total + max(0, int(total_count) - items_with_known_cells)

    max_plausible = int(total_count) * 18
    inferred = max(physical_floor, min(inferred, max_plausible))
    # NB: when this inferred target sits below the full-row geometric value (the warehouse is bigger
    # than the count model implies, e.g. 2501 R4 132 < 136), the grid is un-collapsed and floated up
    # toward it by the hidden-high-tier grid spread in run_reference_engine (not here), so the hard
    # target below stays correct for the genuinely-known-cell case.

    # Soft floor for an unpinned tail: when a counted tier's footprint is unknown (fixed count but
    # neither known cells nor a usable avg_cells — e.g. gold locked by the count-only 极品存量/200019
    # scan, which gives 件 not 格), the total grid is genuinely undetermined. Pinning a HARD target
    # then forces every unpinned item to its 1-cell floor and locks the total to that minimum (2410
    # R4: [96,96,96] vs truth 103 — falsely zero-width). Instead, record the physical floor as a SOFT
    # lower bound and let the minimap/footroom path estimate a range above it (2410 R4 -> [101,109,
    # 113], brackets 103). When the tier's cells/avg ARE known the total is real, so this stays hard.
    # Default ON; AISHA_DISABLE_SOFT_GRID_FLOOR_UNPINNED=1 rolls back to the hard pin.
    if os.environ.get("AISHA_DISABLE_SOFT_GRID_FLOOR_UNPINNED") != "1" and any(
        int(fixed_counts.get(key, 0) or 0) > 0
        and _exact_integer_quality_cell(quality_cells.get(key)) is None
        and not (avg_cells.get(key) is not None and float(avg_cells.get(key)) > 0)
        for key in fixed_counts
    ):
        _append_source_note_once(source_notes, f"{TOTAL_GRID_FLOOR_NOTE_PREFIX}{int(physical_floor)}")
        return total_grid_target

    if total_grid_target is not None and inferred <= float(total_grid_target) + 0.5:
        return total_grid_target

    if total_grid_target is not None:
        _append_source_note_once(
            source_notes,
            f"total_grid_target_raised:{int(round(float(total_grid_target)))}->{inferred}",
        )
    total_grid_target = float(inferred)
    _append_source_note_once(source_notes, TOTAL_GRID_FROM_HIGH_TIER_CELLS_NOTE)
    return total_grid_target


def _minimap_item_dedupe_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item.get("row"),
        item.get("col"),
        item.get("local_index"),
        item.get("quality"),
        item.get("width"),
        item.get("height"),
        item.get("cells"),
    )


def _minimap_items_from_snapshot(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    root_items = snapshot.get("minimap_grid_items")
    root: list[dict[str, Any]] = [
        row for row in root_items if isinstance(row, dict)
    ] if isinstance(root_items, list) else []
    contract: list[dict[str, Any]] = []
    ui_contract = snapshot.get("ui_contract")
    if isinstance(ui_contract, dict):
        minimap = ui_contract.get("minimap")
        if isinstance(minimap, dict):
            contract_items = minimap.get("items")
            if isinstance(contract_items, list):
                contract = [row for row in contract_items if isinstance(row, dict)]
    if not contract:
        return root
    if not root:
        return contract
    # Live monitor mirrors the same minimap into both paths; merge without double-counting.
    if len(root) == len(contract):
        return root
    merged: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for item in [*root, *contract]:
        key = _minimap_item_dedupe_key(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def _item_bottom_row(item: dict[str, Any], *, columns: int) -> int | None:
    row = _safe_int(item.get("row"))
    height = _safe_int(item.get("height")) or 1
    if row is None:
        local_index = _safe_int(item.get("local_index"))
        if local_index is not None and columns > 0:
            row = local_index // columns + 1
    if row is None:
        return None
    return int(row) + max(1, int(height)) - 1


def _deepest_minimap_bottom_row(
    items: list[dict[str, Any]],
    *,
    columns: int = AISHA_GRID_COLUMNS,
) -> int | None:
    deepest: int | None = None
    for item in items:
        bottom = _item_bottom_row(item, columns=columns)
        if bottom is None:
            continue
        deepest = bottom if deepest is None else max(deepest, bottom)
    return deepest


def _known_minimap_cells(items: list[dict[str, Any]]) -> int:
    total = 0
    for item in items:
        cells = _safe_int(item.get("cells"))
        if cells is not None and cells > 0:
            total += int(cells)
            continue
        width = _safe_int(item.get("width")) or 1
        height = _safe_int(item.get("height")) or 1
        total += max(1, int(width) * int(height))
    return total


def _deepest_full_row_grid_floor(
    items: list[dict[str, Any]],
    *,
    columns: int = AISHA_GRID_COLUMNS,
) -> int:
    """Geometric grid floor from a FULLY-occupied row. Items pack contiguously, so a row whose every
    column is occupied proves the warehouse is a full rectangle down to it -- the empty cells in the
    rows ABOVE are unrevealed high-tier (the user's negative space), not gaps outside the warehouse.
    So total grid >= deepest_full_row*columns + occupied cells below it. Returns 0 when no row is full
    (no such proof). A LOWER bound -- unrevealed big gold/red push the truth above (2501 R4: full row
    13 -> 130 + 6 below = 136 floor; the count-derived hard target sat at 132 < floor, truth 157)."""
    occ: set[tuple[int, int]] = set()
    for item in items:
        row = _safe_int(item.get("row"))
        col = _safe_int(item.get("col"))
        if row is None or col is None:
            li = _safe_int(item.get("local_index"))
            if li is not None and columns > 0:
                row = li // columns + 1
                col = li % columns + 1
        if row is None or col is None:
            continue
        width = max(1, _safe_int(item.get("width")) or 1)
        height = max(1, _safe_int(item.get("height")) or 1)
        for dr in range(height):
            for dc in range(width):
                cc = col + dc
                if 1 <= cc <= columns:
                    occ.add((row + dr, cc))
    if not occ:
        return 0
    cols_by_row: dict[int, set[int]] = {}
    for (r, c) in occ:
        cols_by_row.setdefault(r, set()).add(c)
    full_rows = [r for r, cs in cols_by_row.items() if len(cs) >= columns]
    if not full_rows:
        return 0
    deepest_full = max(full_rows)
    below = sum(1 for (r, _c) in occ if r > deepest_full)
    return deepest_full * columns + below


def _occupancy_adjusted_depth_grid(
    items: list[dict[str, Any]],
    raw_deepest: int,
    *,
    columns: int = AISHA_GRID_COLUMNS,
) -> int:
    """Re-anchor the rectangle depth grid when a lone deep item reaches the bottom unsupported.

    `raw_deepest * columns` assumes every cell down to the deepest revealed item is occupied. When
    only ONE revealed item reaches the bottom rows (a big item alone deep with no neighbours
    supporting the depth -- the scattered / lone-deep layout the user flagged on 2402), that
    over-states the grid by ~+10 cells at the decision round (`scripts/audit_lone_deep_overstate.py`:
    reaching==1 over_med +11 vs supported buckets ~0). Re-anchor to the SUPPORTED depth (deepest row
    reached by >=2 revealed items) plus the lone tail's own cells. Only ever DISCOUNTS
    (<= raw_deepest*columns). The CALLER must gate this to late rounds with adequate reveal, because
    early rounds reveal too few items and the "support" line collapses (2409 R1 would mis-discount
    120 -> 44); the lone-deep signal is only trustworthy once most of the vault is revealed.
    """
    rect = int(raw_deepest) * columns
    spans: list[tuple[int, int, int]] = []  # (top_row, bottom_row, width)
    for item in items:
        bottom = _item_bottom_row(item, columns=columns)
        if bottom is None:
            continue
        height = max(1, int(_safe_int(item.get("height")) or 1))
        width = max(1, int(_safe_int(item.get("width")) or 1))
        spans.append((bottom - height + 1, bottom, width))
    if not spans:
        return rect
    bottoms = [bottom for _top, bottom, _w in spans]
    support = 0
    for row in range(int(raw_deepest), 0, -1):
        if sum(1 for bottom in bottoms if bottom >= row) >= 2:
            support = row
            break
    if support <= 0 or support >= int(raw_deepest):
        return rect  # depth is supported by >=2 items -> no over-statement, keep the rectangle
    lone_tail = 0
    for top, bottom, width in spans:
        if bottom > support:
            overlap = bottom - max(top, support + 1) + 1
            if overlap > 0:
                lone_tail += overlap * width
    return min(rect, support * columns + lone_tail)


def _max_minimap_quality(items: list[dict[str, Any]]) -> int | None:
    qualities = [
        int(value)
        for item in items
        if (value := _safe_int(item.get("quality"))) is not None and int(value) > 0
    ]
    return max(qualities) if qualities else None


def _aisha_layout_footroom_multipliers(round_no: int) -> tuple[float, float, float]:
    """Conservative / balanced(P50) / aggressive multipliers on rows_below*cols base."""
    if int(round_no) <= 3:
        return (0.5, 0.75, 1.0)
    if int(round_no) == 4:
        return (0.75, 1.0, 1.35)
    return (1.0, 1.25, 1.5)


def _aisha_layout_footroom_raise_cap(round_no: int) -> int:
    if int(round_no) <= 3:
        return 15
    if int(round_no) == 4:
        return 22
    return 28


def _aisha_layout_viewport_fill_ratio(*, known_cells: int, deepest: int, columns: int) -> float:
    viewport_cells = max(1, int(deepest) * int(columns))
    return min(1.0, max(0.0, float(known_cells) / float(viewport_cells)))


def _aisha_layout_sparse_footroom_boost(fill_ratio: float) -> float:
    # Sparse early viewport (top-heavy scans) may leave more rows below; dense deep fill needs less.
    return max(0.2, min(0.85, 1.0 - float(fill_ratio)))


def _aisha_layout_target_looks_undershot(
    *,
    total_grid_target: float | None,
    known_cells: int,
    rows_below: int,
    columns: int,
    round_no: int,
) -> bool:
    if total_grid_target is None:
        return True
    baseline = float(total_grid_target)
    if baseline <= float(known_cells) + 0.5:
        return True
    # Early rounds: shallow visible depth with low target vs occupied viewport is a common undershoot.
    round_scale = 0.45 if int(round_no) <= 3 else (0.35 if int(round_no) == 4 else 0.28)
    implied_ceiling = float(known_cells) + float(rows_below) * float(columns) * round_scale
    return baseline + 0.5 < implied_ceiling


def _aisha_layout_mode_from_snapshot(snapshot: dict[str, Any]) -> str:
    raw = snapshot.get("audit_aisha_layout_mode")
    if isinstance(raw, str):
        mode = raw.strip().lower()
        if mode in VALID_AISHA_LAYOUT_MODES:
            return mode
    return DEFAULT_AISHA_LAYOUT_MODE


def _aisha_d1_mode_from_snapshot(snapshot: dict[str, Any]) -> str:
    raw = snapshot.get("audit_aisha_d1_mode")
    if isinstance(raw, str):
        mode = raw.strip().lower()
        if mode in VALID_AISHA_D1_MODES:
            return mode
    return DEFAULT_AISHA_D1_MODE


def _snapshot_round_no(snapshot: dict[str, Any]) -> int | None:
    ui_contract = snapshot.get("ui_contract")
    if isinstance(ui_contract, dict):
        context = ui_contract.get("context")
        if isinstance(context, dict):
            round_no = _safe_int(context.get("round"))
            if round_no is not None:
                return round_no
    return _safe_int(snapshot.get("round"))


def _snapshot_map_id(snapshot: dict[str, Any]) -> int | None:
    ui_contract = snapshot.get("ui_contract")
    if isinstance(ui_contract, dict):
        context = ui_contract.get("context")
        if isinstance(context, dict):
            mid = _safe_int(context.get("map_id"))
            if mid is not None:
                return mid
    return _safe_int(snapshot.get("map_id"))


def _aisha_pure_prior_red_keep(map_id: int | None) -> float:
    """Phantom-red value keep for pure-prior red, optionally per map FAMILY.

    Locate result (2026-06-19, full-library + user-export): the phantom-red over-value
    the user feels ("把把过估") is concentrated on LOW-red maps (villa 24xx) where a
    pure-prior red is almost always truly 0, while on high-red shipwreck (25/45xx) a
    pure-prior red is often a real hidden big red (keep must stay high). A single flat
    keep can't serve both -> allow a per-family override so the damp is asymmetric.

    Env `AISHA_PURE_PRIOR_RED_KEEP_BY_FAMILY` = "24:0.05,25:0.30,45:0.30" (family=map//100).
    Empty/unset -> flat `AISHA_PURE_PRIOR_RED_KEEP` (current behaviour, no-op default).
    """
    flat = float(os.environ.get("AISHA_PURE_PRIOR_RED_KEEP") or AISHA_PURE_PRIOR_RED_KEEP)
    spec = os.environ.get("AISHA_PURE_PRIOR_RED_KEEP_BY_FAMILY") or ""
    if not spec or map_id is None:
        return flat
    family = int(map_id) // 100
    for part in spec.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        fam_s, _, keep_s = part.partition(":")
        try:
            if int(fam_s.strip()) == family:
                return float(keep_s.strip())
        except ValueError:
            continue
    return flat


def _aisha_red_keep_evidence_ramp(q6_count_mid: float | None, base_keep: float) -> float:
    """Scale a pure-prior red keep UP toward AISHA_RED_KEEP_RAMP_TOP as the engine's own q6 COUNT
    estimate rises (see the constants block). Below LO -> flat base (phantom suppression intact);
    above HI -> the ceiling (genuine high red no longer halved); linear between."""
    if (
        base_keep >= 1.0
        or q6_count_mid is None
        or os.environ.get(AISHA_DISABLE_RED_KEEP_RAMP)
    ):
        return base_keep
    lo = float(os.environ.get("AISHA_RED_KEEP_RAMP_LO") or AISHA_RED_KEEP_RAMP_LO)
    hi = float(os.environ.get("AISHA_RED_KEEP_RAMP_HI") or AISHA_RED_KEEP_RAMP_HI)
    top = float(os.environ.get("AISHA_RED_KEEP_RAMP_TOP") or AISHA_RED_KEEP_RAMP_TOP)
    if hi <= lo:
        return base_keep
    fraction = max(0.0, min(1.0, (float(q6_count_mid) - lo) / (hi - lo)))
    ceiling = max(base_keep, top)
    return base_keep + (ceiling - base_keep) * fraction


def _aisha_d1_shadow_q6_discount(
    *,
    round_no: int,
    q6_count_range: tuple[int | None, int | None, int | None],
    total_grid_range: tuple[int | None, int | None, int | None],
    q6_count_locked: bool,
) -> float | None:
    """Shadow-only suggested down-weight on red value tail; live apply deferred to Phase 2."""
    if q6_count_locked:
        return None
    low, _mid, high = q6_count_range
    if low is None or high is None:
        return None
    span = max(0, int(high) - int(low))
    round_caps = {1: 0.55, 2: 0.65, 3: 0.75, 4: 0.85, 5: 0.90}
    discount = float(round_caps.get(min(max(int(round_no), 1), 5), 0.90))
    if span >= 2:
        discount *= max(0.45, 1.0 - 0.08 * min(span, 5))
    grid_low, _grid_mid, grid_high = total_grid_range
    if grid_low is not None and grid_high is not None and int(grid_high) - int(grid_low) >= 25:
        discount *= 0.92
    discount = round(max(0.35, min(1.0, discount)), 2)
    if discount >= 0.98:
        return None
    return discount


def _is_pure_prior_red(evidence: RefEvidence) -> bool:
    """True when red (q6) has zero evidence of any kind → its count comes purely
    from the map quality prior. Red is invisible to Aisha, so a pure-prior red can
    over-state value when the warehouse actually has 0 red (false-positive red)."""
    if int(evidence.fixed_counts.get("q6", 0) or 0) > 0:
        return False
    if int(evidence.min_counts.get("q6", 0) or 0) > 0:
        return False
    # any structured sum touching red constrains it → not pure prior
    for key in ("q6", "q5q6", "q4q5q6"):
        if evidence.count_sums.get(key) not in (None, ""):
            return False
    for table in (
        evidence.avg_cells,
        evidence.quality_cells,
        evidence.quality_cell_floors,
        evidence.avg_values,
        evidence.quality_values,
        evidence.quality_value_floors,
        evidence.split_counts,
        evidence.split_quality_cells,
        evidence.split_avg_cells,
    ):
        if table.get("q6") not in (None, "", 0, 0.0):
            return False
    return True


def _apply_aisha_d1_bid_adjustment(
    notes: list[str],
    *,
    snapshot: dict[str, Any],
    hero_key: str,
    quality_count_ranges: dict[str, tuple[int | None, int | None, int | None]],
    total_grid_range: tuple[int | None, int | None, int | None],
    evidence: RefEvidence,
    p50: float | None,
    rv50: float | None,
) -> float | None:
    """Red q6 value-tail adjustment.

    Two layered mechanisms over the *same* round-aware discount:
    - **pure-prior red value cap** (live default, env-reversible): when red has zero
      evidence (count comes purely from the map quality prior) the discount is
      *applied* to value p50, regardless of d1 audit mode. Mitigates an invisible
      false-positive red inflating the quote (2509/2507). Value-only; counts/grid/
      count_prior calibration untouched. Rollback: BIDKING_DISABLE_AISHA_RED_PRIOR_CAP=1.
    - **d1 audit mode** (off/shadow/apply): legacy experiment knob. shadow emits a
      note only; apply discounts value. Independent of the cap above.
    """
    if hero_key != "aisha":
        return None
    round_no = _snapshot_round_no(snapshot)
    if round_no is None:
        return None
    if evidence.fixed_counts.get("q6") not in (None, ""):
        return None
    q6_range = quality_count_ranges.get("q6")
    if not q6_range:
        return None
    discount = _aisha_d1_shadow_q6_discount(
        round_no=int(round_no),
        q6_count_range=q6_range,
        total_grid_range=total_grid_range,
        q6_count_locked=False,
    )
    # discount may be None (no shadow signal). It only gates the legacy pure-prior/d1 note + the
    # round-aware floor on F1's keep; F3 (evidenced red) does NOT depend on it.

    pure_prior_cap = (
        not os.environ.get("BIDKING_DISABLE_AISHA_RED_PRIOR_CAP")
        and _is_pure_prior_red(evidence)
    )
    d1_mode = _aisha_d1_mode_from_snapshot(snapshot)

    # Notes: cap and d1-mode are orthogonal observability signals; emit both.
    if discount is not None:
        if pure_prior_cap:
            _append_source_note_once(notes, f"{AISHA_RED_PRIOR_CAP_NOTE}={discount:g}@r{int(round_no)}")
        if d1_mode == "shadow":
            _append_source_note_once(notes, f"{AISHA_D1_SHADOW_Q6_DISCOUNT_NOTE}={discount:g}@r{int(round_no)}")
        elif d1_mode == "apply":
            _append_source_note_once(notes, f"{AISHA_D1_APPLY_Q6_DISCOUNT_NOTE}={discount:g}@r{int(round_no)}")

    if p50 is None or rv50 is None or rv50 <= 0:
        return None
    non_red = max(0.0, float(p50) - float(rv50))

    # F1: pure-prior red (zero q6 evidence) -> phantom-red disasters. Trim the red tail to
    # AISHA_PURE_PRIOR_RED_KEEP, never weaker than the round-aware shadow discount.
    if pure_prior_cap or d1_mode == "apply":
        if pure_prior_cap:
            keep = _aisha_pure_prior_red_keep(_snapshot_map_id(snapshot))
            if discount is not None:
                keep = min(keep, float(discount))
            # Ramp AFTER the round/span discount: a genuinely red-heavy vault (high q6 count
            # estimate) should lift the keep back up even though its wide count span pushed the
            # discount down -- otherwise the span penalty re-buries the real red we just released.
            q6_count_mid = q6_range[1] if q6_range and len(q6_range) > 1 else None
            keep = _aisha_red_keep_evidence_ramp(q6_count_mid, keep)
        else:  # legacy d1 apply mode
            keep = float(discount) if discount is not None else 1.0
        if keep < 1.0:
            _append_source_note_once(notes, f"aisha_pure_prior_red_keep={keep:g}@r{int(round_no)}")
            return non_red + float(rv50) * keep
        return None

    # F3: evidenced red (has q6 evidence, not pure-prior) is still over-valued. Trim the red tail,
    # but EXEMPT true jackpots (rv50 > cap = acceptable hidden-big-red).
    if not os.environ.get(AISHA_DISABLE_EVID_RED_KEEP) and int(evidence.min_counts.get("q6", 0) or 0) > 0:
        evid_keep = float(os.environ.get("AISHA_EVID_RED_KEEP") or AISHA_EVID_RED_KEEP)
        jackpot_cap = float(os.environ.get("AISHA_EVID_RED_JACKPOT_CAP") or AISHA_EVID_RED_JACKPOT_CAP)
        if evid_keep < 1.0 and float(rv50) <= jackpot_cap:
            _append_source_note_once(notes, f"aisha_evid_red_keep={evid_keep:g}@r{int(round_no)}")
            return non_red + float(rv50) * evid_keep
    return None


def prepare_reference_engine_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Apply hero-specific live defaults without overriding explicit audit modes."""
    payload = dict(snapshot)
    hero = payload.get("hero")
    ui_contract = payload.get("ui_contract")
    if isinstance(ui_contract, dict):
        context = ui_contract.get("context")
        if isinstance(context, dict) and context.get("hero") not in (None, ""):
            hero = context.get("hero")
    hero_key = normalize_hero_key(str(hero or ""))
    if hero_key in LAYOUT_DEPTH_HERO_KEYS:
        if payload.get("audit_aisha_layout_mode") in (None, ""):
            payload["audit_aisha_layout_mode"] = AISHA_LIVE_LAYOUT_MODE
    if hero_key == "aisha":
        if payload.get("audit_aisha_d1_mode") in (None, ""):
            payload["audit_aisha_d1_mode"] = AISHA_LIVE_D1_MODE
    return payload


def _apply_layout_depth_hints_from_snapshot(
    *,
    snapshot: dict[str, Any],
    hero: str,
    round_no: int | None,
    total_grid_target: float | None,
    source_notes: list[str],
    total_count: int | None = None,
) -> float | None:
    hero_key = normalize_hero_key(hero)
    if layout_depth_spec_for_hero(hero_key) is None:
        return total_grid_target
    items = _minimap_items_from_snapshot(snapshot)
    hard_total_locked = any(note in HARD_TOTAL_GRID_SOURCE_NOTES for note in source_notes)
    return apply_layout_depth_hints(
        hero_key=hero_key,
        round_no=round_no,
        total_grid_target=total_grid_target,
        source_notes=source_notes,
        items=items,
        layout_mode=_aisha_layout_mode_from_snapshot(snapshot),
        hard_total_locked=hard_total_locked,
        total_count=total_count,
    )


def _append_aisha_layout_footroom_notes(
    *,
    source_notes: list[str],
    round_no: int,
    raw_hinted: float,
    capped_hinted: float,
    conservative_mult: float,
    balanced_mult: float,
    aggressive_mult: float,
    sparsity_boost: float,
) -> None:
    if raw_hinted > capped_hinted + 0.5:
        _append_source_note_once(source_notes, AISHA_LAYOUT_FOOTROOM_CAP_NOTE)
    if sparsity_boost >= 0.55:
        _append_source_note_once(source_notes, AISHA_LAYOUT_FOOTROOM_SPARSE_NOTE)
    _append_source_note_once(source_notes, AISHA_LAYOUT_GRID_HINT_NOTE)
    _append_source_note_once(source_notes, AISHA_LAYOUT_FOOTROOM_NOTE)
    _append_source_note_once(
        source_notes,
        f"{AISHA_LAYOUT_FOOTROOM_MULT_NOTE}:"
        f"{conservative_mult:g}/{balanced_mult:g}/{aggressive_mult:g}@r{int(round_no)}",
    )


def _aisha_layout_band_widen_delta(source_notes: Iterable[str]) -> int | None:
    for note in source_notes:
        text = str(note)
        if not text.startswith(f"{AISHA_LAYOUT_BAND_WIDEN_DELTA_NOTE}:"):
            continue
        try:
            return max(0, int(text.split(":", 1)[1]))
        except ValueError:
            return None
    return None


def _apply_aisha_layout_band_widen_to_range(
    grid_range: tuple[int | None, int | None, int | None],
    source_notes: list[str],
) -> tuple[int | None, int | None, int | None]:
    return apply_layout_band_widen_to_range(grid_range, source_notes)


def _aisha_layout_effective_deepest_row(
    items: list[dict[str, Any]],
    *,
    round_no: int,
    columns: int = AISHA_GRID_COLUMNS,
) -> int | None:
    deepest = _deepest_minimap_bottom_row(items, columns=columns)
    if deepest is None:
        return None
    if int(round_no) >= 4:
        return deepest
    low_bottoms: list[int] = []
    high_bottoms: list[int] = []
    for item in items:
        quality = _safe_int(item.get("quality"))
        bottom = _item_bottom_row(item, columns=columns)
        if bottom is None or quality is None:
            continue
        if int(quality) <= 1:
            low_bottoms.append(int(bottom))
        elif int(quality) >= 3:
            high_bottoms.append(int(bottom))
    if not low_bottoms or not high_bottoms:
        return deepest
    low_ref = max(low_bottoms)
    high_ref = max(high_bottoms)
    blended = int(round(0.35 * low_ref + 0.65 * high_ref))
    return max(AISHA_LAYOUT_DEEPEST_ROW_THRESHOLD, min(deepest, blended))


def _apply_aisha_early_round_viewport_hint(
    *,
    snapshot: dict[str, Any],
    hero: str,
    round_no: int | None,
    total_grid_target: float | None,
    source_notes: list[str],
) -> float | None:
    """R1–R2 viewport cue from white/green minimap; soft floor only, no R3+ point target."""
    if normalize_hero_key(hero) != "aisha":
        return total_grid_target
    if round_no is None or int(round_no) >= AISHA_LAYOUT_MIN_ROUND:
        return total_grid_target
    if any(note in HARD_TOTAL_GRID_SOURCE_NOTES for note in source_notes):
        return total_grid_target

    items = _minimap_items_from_snapshot(snapshot)
    if not items:
        return total_grid_target
    known_cells = _known_minimap_cells(items)
    if known_cells <= 0:
        return total_grid_target
    deepest = _aisha_layout_effective_deepest_row(items, round_no=int(round_no))
    if deepest is None or deepest < AISHA_LAYOUT_DEEPEST_ROW_THRESHOLD:
        return total_grid_target

    rows_below = max(0, AISHA_WAREHOUSE_ROWS - int(deepest))
    fill_ratio = _aisha_layout_viewport_fill_ratio(
        known_cells=known_cells,
        deepest=int(deepest),
        columns=AISHA_GRID_COLUMNS,
    )
    sparsity_boost = _aisha_layout_sparse_footroom_boost(fill_ratio)
    # R1 white-only scans leave more unknown below; R2 green skill tightens slightly.
    footroom_mult = 1.45 if int(round_no) <= 1 else 1.20
    footroom = rows_below * AISHA_GRID_COLUMNS * footroom_mult * sparsity_boost
    hinted = float(known_cells + footroom)
    max_quality = _max_minimap_quality(items)
    _append_source_note_once(source_notes, AISHA_EARLY_VIEWPORT_GRID_HINT_NOTE)

    # Product: R1 avoids pinning a point estimate when only white is visible.
    if int(round_no) <= 1 and max_quality is not None and max_quality <= 1:
        _append_source_note_once(
            source_notes,
            f"aisha_early_viewport_band_low:{int(round(hinted))}",
        )
        return total_grid_target

    if total_grid_target is not None and hinted <= float(total_grid_target) + 0.5:
        return total_grid_target
    if total_grid_target is None:
        capped = min(hinted, float(known_cells) + 52.0)
        rounded = float(int(round(capped)))
        _append_source_note_once(
            source_notes,
            f"aisha_early_viewport_soft_target:{int(round(rounded))}",
        )
        return rounded
    return total_grid_target


def _apply_aisha_layout_grid_hint(
    *,
    snapshot: dict[str, Any],
    hero: str,
    round_no: int | None,
    total_grid_target: float | None,
    source_notes: list[str],
) -> float | None:
    """R3+ shadow: layout footroom hint with target / shadow / band application modes."""
    if normalize_hero_key(hero) != "aisha":
        return total_grid_target
    mode = _aisha_layout_mode_from_snapshot(snapshot)
    if mode == "off":
        return total_grid_target
    if round_no is None or int(round_no) < AISHA_LAYOUT_MIN_ROUND:
        return total_grid_target
    if any(note in HARD_TOTAL_GRID_SOURCE_NOTES for note in source_notes):
        return total_grid_target

    items = _minimap_items_from_snapshot(snapshot)
    if not items:
        return total_grid_target

    max_quality = _max_minimap_quality(items)
    if max_quality is not None and max_quality <= 1 and int(round_no) <= AISHA_LAYOUT_WHITE_ONLY_MAX_ROUND:
        return total_grid_target

    deepest = _aisha_layout_effective_deepest_row(items, round_no=int(round_no))
    if deepest is None or deepest < AISHA_LAYOUT_DEEPEST_ROW_THRESHOLD:
        return total_grid_target

    known_cells = _known_minimap_cells(items)
    rows_below = max(0, AISHA_WAREHOUSE_ROWS - int(deepest))
    if not _aisha_layout_target_looks_undershot(
        total_grid_target=total_grid_target,
        known_cells=known_cells,
        rows_below=rows_below,
        columns=AISHA_GRID_COLUMNS,
        round_no=int(round_no),
    ):
        _append_source_note_once(source_notes, AISHA_LAYOUT_FOOTROOM_SKIP_NOTE)
        return total_grid_target

    fill_ratio = _aisha_layout_viewport_fill_ratio(
        known_cells=known_cells,
        deepest=int(deepest),
        columns=AISHA_GRID_COLUMNS,
    )
    sparsity_boost = _aisha_layout_sparse_footroom_boost(fill_ratio)
    base_footroom = rows_below * AISHA_GRID_COLUMNS
    conservative_mult, balanced_mult, aggressive_mult = _aisha_layout_footroom_multipliers(
        int(round_no)
    )
    footroom = base_footroom * balanced_mult * sparsity_boost
    raw_hinted = float(known_cells + footroom)
    baseline = float(total_grid_target if total_grid_target is not None else known_cells)
    raise_cap = _aisha_layout_footroom_raise_cap(int(round_no))
    capped_hinted = min(raw_hinted, baseline + float(raise_cap))
    hinted = capped_hinted
    if hinted <= baseline + 0.5:
        return total_grid_target

    _append_aisha_layout_footroom_notes(
        source_notes=source_notes,
        round_no=int(round_no),
        raw_hinted=raw_hinted,
        capped_hinted=capped_hinted,
        conservative_mult=conservative_mult,
        balanced_mult=balanced_mult,
        aggressive_mult=aggressive_mult,
        sparsity_boost=sparsity_boost,
    )
    _append_source_note_once(
        source_notes,
        f"{AISHA_LAYOUT_APPLICATION_MODE_NOTE}:{mode}",
    )
    delta = int(round(hinted - baseline))
    if mode == "shadow":
        return total_grid_target
    if mode == "band":
        _append_source_note_once(
            source_notes,
            f"{AISHA_LAYOUT_BAND_WIDEN_DELTA_NOTE}:{delta}",
        )
        return total_grid_target
    if total_grid_target is not None:
        _append_source_note_once(
            source_notes,
            f"total_grid_target_raised:{int(round(float(total_grid_target)))}->{int(round(hinted))}",
        )
    return hinted


def _filter_avg_value_candidates_by_session(
    key: str,
    candidates: list[int],
    *,
    total_count: int,
    fixed_counts: dict[str, int],
    min_counts: dict[str, int],
    count_sums: dict[str, int],
) -> list[int]:
    if not candidates:
        return []
    feasible: list[int] = []
    for count in candidates:
        trial = {quality: int(fixed_counts.get(quality, 0) or 0) for quality in QUALITY_KEYS}
        trial[key] = int(count)
        q4q5 = count_sums.get("q4q5")
        if q4q5 is not None and trial["q4"] + trial["q5"] != int(q4q5):
            continue
        q4q5q6 = count_sums.get("q4q5q6")
        if q4q5q6 is not None:
            if key == "q5" and "q6" not in fixed_counts:
                trial["q6"] = int(q4q5q6) - trial["q4"] - trial["q5"]
            if trial["q4"] + trial["q5"] + trial["q6"] != int(q4q5q6):
                continue
            if trial["q6"] < max(0, int(min_counts.get("q6", 0) or 0)):
                continue
        other_fixed = sum(
            int(fixed_counts.get(quality, 0) or 0)
            for quality in QUALITY_KEYS
            if quality != key
        )
        other_min = sum(
            max(0, int(min_counts.get(quality, 0) or 0))
            for quality in QUALITY_KEYS
            if quality != key and quality not in fixed_counts
        )
        if int(count) + other_fixed > int(total_count):
            continue
        if int(count) + other_fixed + other_min > int(total_count):
            continue
        feasible.append(int(count))
    return feasible


def _apply_avg_value_only_q5_count_derivation(
    *,
    total_count: int | None,
    fixed_counts: dict[str, int],
    min_counts: dict[str, int],
    count_sums: dict[str, int],
    avg_values: dict[str, float],
    avg_cells: dict[str, float],
    quality_cells: dict[str, float],
    source_notes: list[str],
) -> None:
    """Derive q5 count from public gold avg price alone when it uniquely matches."""
    if total_count is None or int(total_count) <= 0:
        return
    if fixed_counts.get("q5") is not None:
        return
    if quality_cells.get("q5") not in (None, ""):
        return
    if avg_cells.get("q5") not in (None, ""):
        return
    avg = avg_values.get("q5")
    if not _avg_value_has_positive_signal(avg):
        return
    minimum = max(0, int(min_counts.get("q5", 0) or 0))
    candidates = [
        count
        for count in range(max(1, minimum), int(total_count) + 1)
        if _avg_value_count_matches(count, avg)
    ]
    candidates = _filter_avg_value_candidates_by_session(
        "q5",
        candidates,
        total_count=int(total_count),
        fixed_counts=fixed_counts,
        min_counts=min_counts,
        count_sums=count_sums,
    )
    if len(candidates) != 1:
        return
    fixed_counts["q5"] = candidates[0]
    min_counts["q5"] = max(min_counts.get("q5", 0), candidates[0])
    _append_source_note_once(source_notes, "avg_value_only_q5_count_derived")


def _apply_public_avg_value_integer_product_min_counts(
    *,
    soft_avg_value_keys: frozenset[str],
    avg_values: dict[str, float],
    min_counts: dict[str, int],
    source_notes: list[str],
) -> None:
    """One-decimal .5 display avg (e.g. 33895.5): gold totals are integer → count must be even."""
    for key in soft_avg_value_keys:
        avg = avg_values.get(key)
        if not _avg_value_has_positive_signal(avg):
            continue
        normalized = _ref_normalize_avg_value_wire(avg)
        if normalized is None:
            continue
        rounded_one = round(normalized, 1)
        if abs(normalized - rounded_one) > 1e-9:
            continue
        if abs((normalized * 10.0) - round(normalized * 10.0)) > 1e-9:
            continue
        if int(round(normalized * 10.0)) % 10 != 5:
            continue
        if 2 > int(min_counts.get(key, 0) or 0):
            min_counts[key] = 2
            _append_source_note_once(
                source_notes,
                f"public_{key}_avg_value_integer_product_min:2",
            )


def _apply_exact_count_residuals(
    *,
    total_count: int | None,
    fixed_counts: dict[str, int],
    min_counts: dict[str, int],
    count_sums: dict[str, int],
    avg_values: dict[str, float],
    quality_values: dict[str, float],
    quality_cells: dict[str, float],
    split_counts: dict[str, int],
    source_notes: list[str],
) -> None:
    def set_residual_count(
        key: str,
        residual: int,
        note: str,
        hard_conflict_note: str,
    ) -> bool:
        minimum = max(0, int(min_counts.get(key, 0)))
        if key == "q1":
            minimum = max(
                minimum,
                sum(
                    int(split_counts[split_key])
                    for split_key in LOW_SPLIT_KEYS
                    if split_counts.get(split_key) is not None
                ),
            )
        exact_cells_raw = quality_cells.get(key)
        exact_cells = None
        if exact_cells_raw is not None:
            exact_cells = int(round(float(exact_cells_raw)))
        if (
            residual < minimum
            or not _quality_count_matches_value_inputs(
                int(residual),
                None
                if _public_avg_value_soft_pending(
                    key,
                    source_notes=source_notes,
                    fixed_counts=fixed_counts,
                    quality_values=quality_values,
                    avg_values=avg_values,
                )
                else avg_values.get(key),
                _safe_float(quality_values.get(key)),
            )
            or (
                exact_cells is not None
                and not can_compose_grid_total(int(residual), exact_cells)
            )
        ):
            _append_source_note_once(source_notes, f"{note}_conflict")
            _append_source_note_once(source_notes, f"hard_conflict:{hard_conflict_note}")
            return False
        fixed_counts[key] = int(residual)
        min_counts[key] = max(min_counts.get(key, 0), int(residual))
        _append_source_note_once(source_notes, note)
        return True

    for _ in range(len(QUALITY_KEYS)):
        changed = False
        for group_key, group_keys in (
            ("q4q5", ("q4", "q5")),
            ("q4q5q6", ("q4", "q5", "q6")),
        ):
            group_total = count_sums.get(group_key)
            if group_total is None:
                continue
            missing = [key for key in group_keys if fixed_counts.get(key) is None]
            known_total = sum(int(fixed_counts[key]) for key in group_keys if key not in missing)
            if len(missing) == 0:
                if known_total != int(group_total):
                    _append_source_note_once(source_notes, f"count_sum_{group_key}_conflict")
                    _append_source_note_once(source_notes, f"hard_conflict:count_sum_{group_key}")
                continue
            if len(missing) != 1:
                continue
            missing_key = missing[0]
            residual = int(group_total) - known_total
            changed = (
                set_residual_count(
                    missing_key,
                    residual,
                    f"count_sum_{group_key}_{missing_key}_count_from_residual",
                    f"count_sum_{group_key}_{missing_key}_count_residual",
                )
                or changed
            )

        if total_count is not None:
            missing = [key for key in QUALITY_KEYS if fixed_counts.get(key) is None]
            known_total = sum(
                int(fixed_counts[key]) for key in QUALITY_KEYS if key not in missing
            )
            if len(missing) == 0:
                if known_total != int(total_count):
                    _append_source_note_once(source_notes, "quality_count_total_count_conflict")
                    _append_source_note_once(source_notes, "hard_conflict:quality_count_total_count")
            elif len(missing) == 1:
                missing_key = missing[0]
                residual = int(total_count) - known_total
                changed = (
                    set_residual_count(
                        missing_key,
                        residual,
                        f"quality_count_{missing_key}_from_total_count_residual",
                        f"quality_count_{missing_key}_total_count_residual",
                    )
                    or changed
                )
        if not changed:
            break


def _apply_quality_cells_total_grid_residual(
    *,
    total_grid_target: float | None,
    fixed_counts: dict[str, int],
    avg_cells: dict[str, float],
    quality_cells: dict[str, float],
    source_notes: list[str],
) -> None:
    target = _hard_total_grid_target_from_notes(total_grid_target, source_notes)
    if target is None:
        return
    missing: list[str] = []
    known_cells: dict[str, int] = {}
    for key in QUALITY_KEYS:
        raw = quality_cells.get(key)
        if raw is None:
            missing.append(key)
            continue
        rounded = int(round(float(raw)))
        if abs(float(raw) - rounded) > 0.0001:
            return
        known_cells[key] = rounded
    if not missing:
        if sum(known_cells.values()) != target:
            _append_source_note_once(source_notes, "quality_cells_total_grid_conflict")
            _append_source_note_once(source_notes, "hard_conflict:quality_cells_total_grid")
        return
    if len(missing) != 1:
        return
    missing_key = missing[0]
    residual = target - sum(known_cells.values())
    note = f"quality_cells_{missing_key}_from_total_grid_residual"
    hard_conflict_note = f"quality_cells_{missing_key}_total_grid_residual"
    count = fixed_counts.get(missing_key)
    if residual < 0:
        _append_source_note_once(source_notes, f"{note}_conflict")
        _append_source_note_once(source_notes, f"hard_conflict:{hard_conflict_note}")
        return
    if count is not None:
        count_int = int(count)
        if not can_compose_grid_total(count_int, residual):
            _append_source_note_once(source_notes, f"{note}_conflict")
            _append_source_note_once(source_notes, f"hard_conflict:{hard_conflict_note}")
            return
        avg = avg_cells.get(missing_key)
        if not _avg_matches_exact_grid(count_int, avg, residual):
            _append_source_note_once(source_notes, f"{note}_avg_conflict")
            _append_source_note_once(source_notes, f"hard_conflict:{hard_conflict_note}")
            return
    quality_cells[missing_key] = float(residual)
    _append_source_note_once(source_notes, note)


def _apply_avg_value_cells_exact_count_intersection(
    *,
    total_count: int | None,
    fixed_counts: dict[str, int],
    min_counts: dict[str, int],
    avg_cells: dict[str, float],
    avg_values: dict[str, float],
    quality_cells: dict[str, float],
    quality_values: dict[str, float],
    split_counts: dict[str, int],
    source_notes: list[str],
) -> None:
    if total_count is None or total_count <= 0:
        return
    for key in QUALITY_KEYS:
        if (
            fixed_counts.get(key) is not None
            or key in quality_cells
            or key in quality_values
            or key not in avg_cells
            or key not in avg_values
        ):
            continue
        avg_cell = avg_cells.get(key)
        avg_value = avg_values.get(key)
        if (
            avg_cell is None
            or avg_cell <= 0
            or not _avg_value_has_positive_signal(avg_value)
        ):
            continue
        minimum = max(1, int(min_counts.get(key, 0)))
        if key == "q1":
            minimum = max(
                minimum,
                sum(
                    int(split_counts[split_key])
                    for split_key in LOW_SPLIT_KEYS
                    if split_counts.get(split_key) is not None
                ),
            )
        candidates = [
            count
            for count in range(minimum, int(total_count) + 1)
            if _avg_value_count_matches(count, avg_value)
            and _avg_grid_options(count, avg_cell)
        ]
        if len(candidates) != 1:
            continue
        fixed_counts[key] = candidates[0]
        min_counts[key] = max(min_counts.get(key, 0), candidates[0])
        _append_source_note_once(
            source_notes,
            f"avg_value_cells_{key}_count_derived",
        )


def _parse_ranges(text: Any) -> tuple[float | None, float | None, float | None]:
    parts = [p.strip() for p in str(text or "").split("/") if p.strip()]
    parsed = [_safe_float(part) for part in parts[:3]]
    while len(parsed) < 3:
        parsed.append(None)
    return (parsed[0], parsed[1], parsed[2])


def _quality_key(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in QUALITY_KEYS:
        return text
    if text.startswith("q") and text[1:] in QUALITY_NUM_TO_KEY:
        return QUALITY_NUM_TO_KEY[text[1:]]
    if text in QUALITY_NUM_TO_KEY:
        return QUALITY_NUM_TO_KEY[text]
    match = re.search(r"(?:bucket|quality)[._/-]?q?(?P<q>[1-6])", text)
    if match:
        return QUALITY_NUM_TO_KEY.get(match.group("q"))
    return None


def _quality_number_to_key(value: Any) -> str | None:
    parsed = _safe_int(value)
    if parsed is None:
        return _quality_key(value)
    return QUALITY_NUM_TO_KEY.get(str(parsed))


def _low_split_key(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    return LOW_SPLIT_ALIASES.get(text)


def _iter_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        return
    if isinstance(value, (list, tuple)):
        for row in value:
            if isinstance(row, dict):
                yield row


def _merge_quality_values(
    out: dict[str, float],
    raw: Any,
    *,
    note_prefix: str,
    source_notes: list[str],
) -> None:
    if not isinstance(raw, dict):
        return
    for raw_key, raw_value in raw.items():
        key = _quality_key(raw_key)
        value = _safe_float(raw_value)
        if key is None or value is None:
            continue
        out[key] = value
        source_notes.append(f"{note_prefix}_{key}")


def _merge_quality_counts(
    out: dict[str, int],
    raw: Any,
    *,
    note_prefix: str,
    source_notes: list[str],
) -> None:
    if not isinstance(raw, dict):
        return
    for raw_key, raw_value in raw.items():
        key = _quality_key(raw_key)
        value = _safe_int(raw_value)
        if key is None or value is None:
            continue
        out[key] = value
        source_notes.append(f"{note_prefix}_{key}")


def _merge_split_values(
    out: dict[str, float],
    raw: Any,
    *,
    note_prefix: str,
    source_notes: list[str],
) -> None:
    if not isinstance(raw, dict):
        return
    for raw_key, raw_value in raw.items():
        key = _low_split_key(raw_key)
        value = _safe_float(raw_value)
        if key is None or value is None:
            continue
        out[key] = value
        source_notes.append(f"{note_prefix}_{key}")


def _merge_split_counts(
    out: dict[str, int],
    raw: Any,
    *,
    note_prefix: str,
    source_notes: list[str],
) -> None:
    if not isinstance(raw, dict):
        return
    for raw_key, raw_value in raw.items():
        key = _low_split_key(raw_key)
        value = _safe_int(raw_value)
        if key is None or value is None:
            continue
        out[key] = value
        source_notes.append(f"{note_prefix}_{key}")


def _merge_count_sums(
    out: dict[str, int],
    raw: Any,
    *,
    note_prefix: str,
    source_notes: list[str],
) -> None:
    if not isinstance(raw, dict):
        return
    for raw_key, raw_value in raw.items():
        key = str(raw_key or "").strip().lower().replace("+", "")
        value = _safe_int(raw_value)
        if value is None:
            continue
        if key in {"q4q5", "45"}:
            out["q4q5"] = value
            source_notes.append(f"{note_prefix}_q4q5")
        elif key in {"q4q5q6", "456"}:
            out["q4q5q6"] = value
            source_notes.append(f"{note_prefix}_q4q5q6")


def _has_shape_reveal(item: dict[str, Any]) -> bool:
    shape_code = item.get("shape_code")
    if shape_code not in (None, "", 0):
        return True
    shape_key = str(item.get("shape_key") or "").strip()
    return bool(shape_key)


def _is_coarse_quality_reveal_item(item: dict[str, Any]) -> bool:
    if _quality_number_to_key(item.get("quality")) is None:
        return False
    return not _has_shape_reveal(item)


def _quality_reveal_item_identity(item: dict[str, Any], key: str, *, fallback: str) -> tuple[str, str]:
    return (
        str(
            item.get("runtime_id")
            or item.get("local_index")
            or item.get("item_id")
            or fallback
        ),
        key,
    )


def _iter_skill_reveal_dict_rows(
    snapshot: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    seen: set[int] = set()
    for key in ("skill_reveal_rows", "skill_reveals"):
        for row in _iter_dicts(snapshot.get(key)):
            if not isinstance(row, dict):
                continue
            row_id = id(row)
            if row_id in seen:
                continue
            seen.add(row_id)
            yield row


def _is_maria_skill_quality_reveal_row(row: dict[str, Any]) -> bool:
    return (
        _safe_int(row.get("hero_id")) == MARIA_HERO_ID
        and _safe_int(row.get("skill_id")) == MARIA_SKILL_QUALITY_REVEAL_ID
    )


def _apply_maria_skill_evidence(
    snapshot: dict[str, Any],
    *,
    min_counts: dict[str, int],
    split_counts: dict[str, int],
    quality_value_floors: dict[str, float],
    source_notes: list[str],
) -> None:
    counts = {key: 0 for key in QUALITY_KEYS}
    split = {key: 0 for key in LOW_SPLIT_KEYS}
    seen: set[tuple[str, str]] = set()
    maria_sources: set[str] = set()

    for row in _iter_skill_reveal_dict_rows(snapshot):
        if not _is_maria_skill_quality_reveal_row(row):
            continue
        skill_id = _safe_int(row.get("skill_id"))
        prefix = f"maria_skill_{skill_id or 'quality'}"
        items = row.get("observed_items") or row.get("revealed_items_detail") or ()
        for item_idx, item in enumerate(_iter_dicts(items)):
            if not isinstance(item, dict):
                continue
            quality = _safe_int(item.get("quality"))
            if quality is None or quality > 3:
                continue
            key = _quality_number_to_key(quality)
            if key is None or not _is_coarse_quality_reveal_item(item):
                continue
            identity = _quality_reveal_item_identity(
                item,
                key,
                fallback=f"{prefix}-{item_idx}",
            )
            if identity in seen:
                continue
            seen.add(identity)
            counts[key] += 1
            maria_sources.add(prefix)
            split_key = LOW_QUALITY_NUMBER_TO_SPLIT.get(str(quality))
            if split_key is not None:
                split[split_key] += 1

    for row in _iter_skill_reveal_dict_rows(snapshot):
        if _safe_int(row.get("hero_id")) != MARIA_HERO_ID:
            continue
        if _is_maria_skill_quality_reveal_row(row):
            continue
        skill_key = str(row.get("skill_id") or "")
        quality_key = MARIA_SKILL_VALUE_BY_ID.get(skill_key)
        if quality_key is None:
            continue
        value = _safe_float(row.get("result"))
        if value is None or value <= 0:
            continue
        quality_value_floors[quality_key] = max(
            quality_value_floors.get(quality_key, 0.0),
            float(value),
        )
        source_notes.append(f"maria_skill_{quality_key}_value_floor")

    if any(counts.values()):
        for key, value in counts.items():
            min_counts[key] = max(min_counts.get(key, 0), value)
        source_notes.append("maria_skill_coarse_quality_min_counts")
        for prefix in sorted(maria_sources):
            source_notes.append(f"maria_skill_coarse_quality_source:{prefix}")
    if any(split.values()):
        for key, value in split.items():
            split_counts[key] = max(split_counts.get(key, 0), value)
        source_notes.append("maria_skill_coarse_quality_split_counts")


def _outline_item_cells(item: Mapping[str, Any]) -> int | None:
    cells = _safe_int(item.get("cells"))
    if cells is not None and cells > 0:
        return cells
    return _shape_cells(item.get("shape_code") or item.get("shape_key"))


def _outline_totals_from_items(items: Any) -> tuple[int, int] | None:
    cells_by_key: dict[int, int] = {}
    anonymous_index = 0
    for item in _iter_dicts(items):
        cells = _outline_item_cells(item)
        if cells is None:
            continue
        runtime_id = _safe_int(item.get("runtime_id"))
        key = runtime_id if runtime_id is not None else -anonymous_index - 1
        if runtime_id is None:
            anonymous_index += 1
        cells_by_key[key] = cells
    if not cells_by_key:
        return None
    return len(cells_by_key), sum(cells_by_key.values())


def _mirror_quality_runtime_ids(snapshot: dict[str, Any]) -> set[int]:
    runtime_ids: set[int] = set()
    row_groups = (
        snapshot.get("action_result_rows"),
        _dig(snapshot, "ui_contract", "actions", "results"),
    )
    seen_rows: set[int] = set()
    for rows in row_groups:
        for row in _iter_dicts(rows):
            if not isinstance(row, dict):
                continue
            row_id = id(row)
            if row_id in seen_rows:
                continue
            seen_rows.add(row_id)
            if str(row.get("action_id") or "") != str(MIRROR_EYE_ACTION_ID):
                continue
            for item in _iter_dicts(
                row.get("revealed_items_detail") or row.get("observed_items")
            ):
                runtime_id = _safe_int(item.get("runtime_id"))
                quality = _safe_int(item.get("quality"))
                if runtime_id is not None and quality is not None:
                    runtime_ids.add(runtime_id)
    return runtime_ids


def _apply_ethan_skill_evidence(
    snapshot: dict[str, Any],
    *,
    set_total_count: Callable[[Any, str], None],
    set_total_grid_target: Callable[[Any, str], None],
    source_notes: list[str],
) -> None:
    mirror_runtime_ids = _mirror_quality_runtime_ids(snapshot)
    full_outline_totals: tuple[int, int] | None = None

    for row in _iter_skill_reveal_dict_rows(snapshot):
        if _safe_int(row.get("hero_id")) != ETHAN_HERO_ID:
            continue
        skill_id = _safe_int(row.get("skill_id"))
        items = row.get("observed_items") or row.get("revealed_items_detail") or ()
        if skill_id == ETHAN_SKILL_R1_OUTLINE:
            totals = _outline_totals_from_items(items)
            if totals is not None:
                source_notes.append(
                    f"ethan_skill_r1_outline:{totals[0]}:{totals[1]}"
                )
            continue
        if skill_id == ETHAN_SKILL_FULL_OUTLINE:
            totals = _outline_totals_from_items(items)
            if totals is not None:
                full_outline_totals = totals
            continue
        if skill_id not in ETHAN_QUALITY_OUTLINE_SKILL_IDS or not mirror_runtime_ids:
            continue
        outline_runtime_ids = {
            runtime_id
            for item in _iter_dicts(items)
            if (runtime_id := _safe_int(item.get("runtime_id"))) is not None
            and _outline_item_cells(item) is not None
        }
        if outline_runtime_ids != mirror_runtime_ids:
            continue
        totals = _outline_totals_from_items(items)
        if totals is not None:
            full_outline_totals = totals

    if full_outline_totals is None:
        return
    count, cells = full_outline_totals
    set_total_count(count, "ethan_skill_full_outline_count")
    set_total_grid_target(cells, "ethan_skill_full_outline_cells")


def _iter_quality_reveal_item_rows(
    snapshot: dict[str, Any],
) -> Iterable[tuple[str, dict[str, Any]]]:
    for row in _iter_dicts(snapshot.get("public_info_rows")):
        info_id = _safe_int(row.get("info_id"))
        if info_id in PUBLIC_BUCKET_OUTLINE_QUALITY:
            continue
        prefix = f"public_info_{info_id}" if info_id is not None else "public_info"
        for item in _iter_dicts(row.get("revealed_items_detail")):
            if _quality_number_to_key(item.get("quality")) is not None:
                yield prefix, item

    row_groups = (
        ("action_result", snapshot.get("action_result_rows")),
        (
            "action_result",
            _dig(snapshot, "ui_contract", "actions", "results"),
        ),
    )
    seen_rows: set[int] = set()
    for prefix, rows in row_groups:
        for row_idx, row in enumerate(_iter_dicts(rows)):
            if not isinstance(row, dict):
                continue
            row_id = id(row)
            if row_id in seen_rows:
                continue
            seen_rows.add(row_id)
            action_id = str(row.get("action_id") or "")
            source_prefix = (
                f"{prefix}_{action_id}"
                if action_id
                else f"{prefix}_{row_idx}"
            )
            for item in _iter_dicts(row.get("revealed_items_detail")):
                if _quality_number_to_key(item.get("quality")) is not None:
                    yield source_prefix, item

    for reveal_idx, reveal in enumerate(_iter_skill_reveal_dict_rows(snapshot)):
        if _is_maria_skill_quality_reveal_row(reveal):
            continue
        skill_id = str(reveal.get("skill_id") or "")
        prefix = f"skill_{skill_id or reveal_idx}"
        for item in _iter_dicts(reveal.get("observed_items") or reveal.get("revealed_items_detail")):
            if isinstance(item, dict) and _quality_number_to_key(item.get("quality")) is not None:
                yield prefix, item


def _iter_coarse_quality_reveal_items(
    snapshot: dict[str, Any],
) -> Iterable[tuple[str, dict[str, Any]]]:
    for source_prefix, item in _iter_quality_reveal_item_rows(snapshot):
        if _is_coarse_quality_reveal_item(item):
            yield source_prefix, item


def _coarse_quality_reveal_floors(
    snapshot: dict[str, Any],
    *,
    source_notes: list[str],
) -> tuple[
    dict[str, int],
    dict[str, float],
    dict[str, float],
    dict[str, int],
    dict[str, int],
    dict[str, float],
]:
    counts = {key: 0 for key in QUALITY_KEYS}
    cell_floors: dict[str, float] = {}
    value_floors: dict[str, float] = {}
    value_floor_item_counts: dict[str, int] = {}
    split_counts = {key: 0 for key in LOW_SPLIT_KEYS}
    split_value_floors: dict[str, float] = {}
    seen: set[tuple[str, str]] = set()
    floor_seen: set[tuple[str, str]] = set()
    count_source_prefixes: set[str] = set()

    for source_prefix, item in _iter_quality_reveal_item_rows(snapshot):
        quality = _safe_int(item.get("quality"))
        if quality is None:
            continue
        key = _quality_number_to_key(quality)
        if key is None:
            continue

        if _is_coarse_quality_reveal_item(item):
            identity = _quality_reveal_item_identity(
                item,
                key,
                fallback=f"{source_prefix}-{id(item)}",
            )
            if identity not in seen:
                seen.add(identity)
                counts[key] += 1
                count_source_prefixes.add(source_prefix)
                split_key = LOW_QUALITY_NUMBER_TO_SPLIT.get(str(quality))
                if split_key is not None:
                    split_counts[split_key] += 1

        floor_identity = _quality_reveal_item_identity(
            item,
            key,
            fallback=f"floor-{source_prefix}-{id(item)}",
        )
        if floor_identity in floor_seen:
            continue
        floor_seen.add(floor_identity)

        item_cells = _safe_int(item.get("cells"))
        if item_cells is None or item_cells <= 0:
            item_cells = _shape_cells(item.get("shape_code") or item.get("shape_key"))
        if item_cells is not None and item_cells > 0:
            cell_floors[key] = cell_floors.get(key, 0.0) + float(item_cells)

        item_value = _safe_float(item.get("value"))
        if item_value is not None and item_value > 0:
            value_floors[key] = value_floors.get(key, 0.0) + float(item_value)
            value_floor_item_counts[key] = value_floor_item_counts.get(key, 0) + 1
            split_key = LOW_QUALITY_NUMBER_TO_SPLIT.get(str(quality))
            if split_key is not None and _is_coarse_quality_reveal_item(item):
                split_value_floors[split_key] = (
                    split_value_floors.get(split_key, 0.0) + float(item_value)
                )

    if any(counts.values()):
        source_notes.append("coarse_quality_reveal_min_counts")
        for prefix in sorted(count_source_prefixes):
            source_notes.append(f"coarse_quality_reveal_source:{prefix}")

    return (
        {key: value for key, value in counts.items() if value > 0},
        cell_floors,
        value_floors,
        {key: value for key, value in value_floor_item_counts.items() if value > 0},
        {key: value for key, value in split_counts.items() if value > 0},
        split_value_floors,
    )


def _iter_all_item_quality_scan_items(
    snapshot: dict[str, Any],
) -> Iterable[tuple[str, dict[str, Any]]]:
    for row in _iter_dicts(snapshot.get("public_info_rows")):
        info_id = _safe_int(row.get("info_id"))
        if info_id not in ALL_ITEM_QUALITY_PUBLIC_INFO_IDS:
            continue
        prefix = f"public_info_{info_id}" if info_id is not None else "public_info"
        for item in _iter_dicts(row.get("revealed_items_detail")):
            if isinstance(item, dict) and _quality_number_to_key(item.get("quality")) is not None:
                yield prefix, item

    for reveal in _iter_skill_reveal_dict_rows(snapshot):
        skill_id = _safe_int(reveal.get("skill_id"))
        if skill_id != RAVEN_ALL_ITEM_QUALITY_SKILL_ID:
            continue
        prefix = f"skill_{skill_id}"
        for item in _iter_dicts(reveal.get("observed_items") or reveal.get("revealed_items_detail")):
            if isinstance(item, dict) and _quality_number_to_key(item.get("quality")) is not None:
                yield prefix, item


def _count_all_item_quality_scan(snapshot: dict[str, Any]) -> tuple[dict[str, int], int, bool]:
    """Count coarse per-tier items from a full-warehouse quality scan only."""
    counts = {key: 0 for key in QUALITY_KEYS}
    seen: set[tuple[str, str]] = set()
    has_raven_r5_skill = False
    for source_prefix, item in _iter_all_item_quality_scan_items(snapshot):
        if source_prefix.startswith("skill_"):
            has_raven_r5_skill = True
        if not _is_coarse_quality_reveal_item(item):
            continue
        key = _quality_number_to_key(item.get("quality"))
        if key is None:
            continue
        identity = _quality_reveal_item_identity(
            item,
            key,
            fallback=f"{source_prefix}-{id(item)}",
        )
        if identity in seen:
            continue
        seen.add(identity)
        counts[key] += 1
    return counts, len(seen), has_raven_r5_skill


def _all_item_quality_scan_is_complete(
    snapshot: dict[str, Any],
    *,
    hero: str,
    total_count: int | None,
    scan_count: int,
    has_raven_r5_skill: bool,
) -> bool:
    if scan_count <= 0:
        return False
    if total_count is not None and int(total_count) == int(scan_count):
        return True
    if (
        normalize_hero_key(hero) == "raven"
        and has_raven_r5_skill
        and (_snapshot_round_no(snapshot) or 0) >= 5
    ):
        return True
    return False


def _apply_all_item_quality_exact_counts(
    snapshot: dict[str, Any],
    *,
    hero: str,
    total_count: int | None,
    fixed_counts: dict[str, int],
    min_counts: dict[str, int],
    quality_cells: dict[str, float],
    set_total_count,
    source_notes: list[str],
) -> int | None:
    """Lock exact per-tier counts when a full-warehouse quality scan is complete.

    Raven R5 (skill 100301) and matching public all-item-quality rows reveal every
    item's quality. Missing tiers (e.g. no red) must lock to zero, not stay on
    count_prior ranges.
    """
    scanned_counts, scan_count, has_raven_r5_skill = _count_all_item_quality_scan(snapshot)
    if not _all_item_quality_scan_is_complete(
        snapshot,
        hero=hero,
        total_count=total_count,
        scan_count=scan_count,
        has_raven_r5_skill=has_raven_r5_skill,
    ):
        return total_count

    if total_count is None and has_raven_r5_skill and normalize_hero_key(hero) == "raven":
        set_total_count(scan_count, "raven_skill_100301_all_item_quality_count")
        total_count = scan_count

    conflict = False
    for key in QUALITY_KEYS:
        exact = int(scanned_counts.get(key, 0))
        existing = fixed_counts.get(key)
        existing_min = int(min_counts.get(key, 0))
        if existing is not None and int(existing) != exact:
            source_notes.append(f"all_item_quality_{key}_count_conflict")
            source_notes.append(f"hard_conflict:all_item_quality_{key}_count")
            conflict = True
            break
        if existing is None and existing_min > exact:
            source_notes.append(f"all_item_quality_{key}_min_conflict")
            source_notes.append(f"hard_conflict:all_item_quality_{key}_min")
            conflict = True
            break
    if conflict:
        return total_count

    for key in QUALITY_KEYS:
        exact = int(scanned_counts.get(key, 0))
        fixed_counts[key] = exact
        min_counts[key] = exact
        if exact == 0:
            source_notes.append(f"all_item_quality_zero_{key}")
            if key not in quality_cells:
                quality_cells[key] = 0.0
        else:
            source_notes.append(f"all_item_quality_exact_{key}:{exact}")
    source_notes.append("all_item_quality_exact_counts")
    return total_count


def _public_quality_reveal_min_counts(snapshot: dict[str, Any]) -> dict[str, int]:
    counts, _, _, _, _, _ = _coarse_quality_reveal_floors(snapshot, source_notes=[])
    return counts


def _public_quality_reveal_floors(
    snapshot: dict[str, Any],
) -> tuple[dict[str, int], dict[str, float], dict[str, float]]:
    counts, cell_floors, value_floors, _, _, _ = _coarse_quality_reveal_floors(
        snapshot,
        source_notes=[],
    )
    return counts, cell_floors, value_floors


def _shape_cells(value: Any) -> int | None:
    parsed = _safe_int(value)
    if parsed is None:
        return None
    width = parsed // 10
    height = parsed % 10
    if width <= 0 or height <= 0:
        return None
    return width * height


def _apply_public_info_exact_numeric_rows(
    snapshot: dict[str, Any],
    *,
    fixed_counts: dict[str, int],
    min_counts: dict[str, int],
    quality_cells: dict[str, float],
    set_total_count,
    set_total_grid_target,
    source_notes: list[str],
) -> None:
    for row in _iter_dicts(snapshot.get("public_info_rows")):
        info_id = _safe_int(row.get("info_id"))
        value = _safe_float(row.get("value"))
        if info_id is None or value is None:
            continue
        if info_id == 200017:
            set_total_count(value, "public_info_total_item_count")
            continue
        if info_id == 200009:
            set_total_grid_target(value, "public_info_total_cells")
            continue
        quality_key = PUBLIC_EXACT_QUALITY_CELLS_INFO.get(info_id)
        if quality_key is not None:
            existing = quality_cells.get(quality_key)
            if existing is not None and abs(float(existing) - float(value)) > 0.0001:
                source_notes.append(f"public_info_{info_id}_{quality_key}_cells_conflict")
                continue
            quality_cells[quality_key] = float(value)
            source_notes.append(f"public_info_{info_id}_{quality_key}_cells")
            continue
        count_key = PUBLIC_EXACT_QUALITY_COUNT_INFO.get(info_id)
        if count_key is None:
            continue
        count_int = int(round(value))
        existing = fixed_counts.get(count_key)
        if existing is not None and int(existing) != count_int:
            source_notes.append(f"public_info_{info_id}_{count_key}_count_conflict")
            continue
        fixed_counts[count_key] = count_int
        min_counts[count_key] = max(min_counts.get(count_key, 0), count_int)
        source_notes.append(f"public_info_{info_id}_{count_key}_count")


def _runtime_quality_by_observed_id(snapshot: dict[str, Any]) -> dict[int, int]:
    qualities: dict[int, int] = {}

    def ingest(items: Any) -> None:
        for item in _iter_dicts(items):
            runtime_id = _safe_int(item.get("runtime_id"))
            quality = _safe_int(item.get("quality"))
            if runtime_id is None or quality is None or quality <= 0:
                continue
            qualities[runtime_id] = max(qualities.get(runtime_id, 0), quality)

    for row in _iter_skill_reveal_dict_rows(snapshot):
        ingest(row.get("observed_items") or row.get("revealed_items_detail"))
    for row in _iter_dicts(snapshot.get("action_result_rows")):
        ingest(row.get("revealed_items_detail") or row.get("observed_items"))
    for row in _iter_dicts(snapshot.get("public_info_rows")):
        ingest(row.get("revealed_items_detail"))
    uc = snapshot.get("ui_contract") if isinstance(snapshot.get("ui_contract"), dict) else {}
    actions = uc.get("actions") if isinstance(uc.get("actions"), dict) else {}
    for row in actions.get("results") or ():
        if not isinstance(row, dict):
            continue
        ingest(row.get("revealed_items_detail") or row.get("observed_items"))
    return qualities


def _extract_public_max_quality(snapshot: dict[str, Any]) -> int | None:
    max_quality: int | None = None
    runtime_qualities = _runtime_quality_by_observed_id(snapshot)

    def consider(quality: Any) -> None:
        nonlocal max_quality
        parsed = _safe_int(quality)
        if parsed is None or parsed <= 0:
            return
        max_quality = parsed if max_quality is None else min(max_quality, parsed)

    for row in _iter_dicts(snapshot.get("public_info_rows")):
        if _safe_int(row.get("info_id")) != PUBLIC_MAX_QUALITY_INFO_ID:
            continue
        for item in _iter_dicts(row.get("revealed_items_detail")):
            consider(item.get("quality"))

    for reveal in _iter_dicts(snapshot.get("skill_reveals")):
        if str(reveal.get("skill_id") or "") not in PUBLIC_MAX_QUALITY_SKILL_IDS:
            continue
        for item in _iter_dicts(reveal.get("observed_items")):
            consider(item.get("quality"))

    uc = snapshot.get("ui_contract") if isinstance(snapshot.get("ui_contract"), dict) else {}
    actions = uc.get("actions") if isinstance(uc.get("actions"), dict) else {}
    for row in actions.get("results") or ():
        if not isinstance(row, dict):
            continue
        action_id = str(row.get("action_id") or "")
        if action_id not in PUBLIC_MAX_QUALITY_SKILL_IDS:
            continue
        for item in _iter_dicts(
            row.get("revealed_items_detail") or row.get("observed_items")
        ):
            consider(item.get("quality"))

    for row in _iter_dicts(snapshot.get("action_result_rows")):
        action_id = str(row.get("action_id") or "")
        if action_id not in TREASURE_HIGHEST_ITEM_VALUE_ACTION_IDS:
            continue
        for item in _iter_dicts(
            row.get("revealed_items_detail") or row.get("observed_items")
        ):
            quality = _safe_int(item.get("quality"))
            runtime_id = _safe_int(item.get("runtime_id"))
            if quality is None and runtime_id is not None:
                quality = runtime_qualities.get(runtime_id)
            consider(quality)

    return max_quality


def _apply_public_max_quality_ceiling(
    max_quality: int | None,
    *,
    fixed_counts: dict[str, int],
    min_counts: dict[str, int],
    source_notes: list[str],
) -> None:
    if max_quality is None or max_quality >= 6:
        return
    source_notes.append(f"public_max_quality_ceiling:{max_quality}")
    for key, tier in QUALITY_TIER_NUMBER.items():
        if tier <= max_quality:
            continue
        existing = fixed_counts.get(key)
        existing_min = int(min_counts.get(key, 0))
        if (existing is not None and int(existing) > 0) or existing_min > 0:
            source_notes.append(f"hard_conflict:public_max_quality_zero_{key}")
            continue
        fixed_counts[key] = 0
        min_counts[key] = 0
        source_notes.append(f"public_max_quality_zero_{key}")


def _public_bucket_outline_totals(snapshot: dict[str, Any]) -> dict[str, tuple[int, int | None]]:
    totals: dict[str, tuple[int, int | None]] = {}
    seen: set[tuple[str, str]] = set()
    for row in _iter_dicts(snapshot.get("public_info_rows")):
        key = PUBLIC_BUCKET_OUTLINE_QUALITY.get(_safe_int(row.get("info_id")) or -1)
        if key is None:
            continue
        count = 0
        cells = 0
        missing_cells = False
        for idx, item in enumerate(_iter_dicts(row.get("revealed_items_detail"))):
            item_key = _quality_number_to_key(item.get("quality")) or key
            if item_key != key:
                continue
            identity = (
                str(
                    item.get("runtime_id")
                    or item.get("local_index")
                    or item.get("item_id")
                    or f"row-{id(row)}-{idx}"
                ),
                key,
            )
            if identity in seen:
                continue
            seen.add(identity)
            count += 1
            item_cells = _safe_int(item.get("cells"))
            if item_cells is None or item_cells <= 0:
                item_cells = _shape_cells(item.get("shape_code") or item.get("shape_key"))
            if item_cells is None or item_cells <= 0:
                missing_cells = True
            else:
                cells += item_cells
        if count > 0:
            prev_count, prev_cells = totals.get(key, (0, 0))
            next_cells: int | None
            if missing_cells or prev_cells is None:
                next_cells = None
            else:
                next_cells = prev_cells + cells
            totals[key] = (prev_count + count, next_cells)
    return totals


def _random_avg_sample_count(row: dict[str, Any]) -> int | None:
    sample_count = _safe_int(row.get("sample_count"))
    if sample_count is not None:
        return sample_count
    semantic = str(row.get("semantic") or row.get("kind") or "")
    match = re.search(r"random[_-](?P<count>\d+)[_-]avg[_-]value", semantic)
    if match:
        return _safe_int(match.group("count"))
    return None


def _quality_key_from_avg_value_row(row: dict[str, Any]) -> str | None:
    quality = _quality_number_to_key(row.get("quality"))
    if quality in {"q4", "q5", "q6"}:
        return quality
    semantic = str(row.get("semantic") or "")
    return PUBLIC_AVG_VALUES.get(semantic)


def _public_quality_avg_values(public_info: dict[str, Any]) -> dict[str, float]:
    rows: list[dict[str, Any]] = []
    rows.extend(_iter_dicts(public_info.get("public_avg_values")))
    rows.extend(_iter_dicts(public_info.get("public_numeric_facts")))
    values: dict[str, float] = {}
    for row in rows:
        kind = str(row.get("kind") or "")
        semantic = str(row.get("semantic") or "")
        if kind != "avg_value" and semantic not in PUBLIC_AVG_VALUES:
            continue
        key = _quality_key_from_avg_value_row(row)
        value = _safe_float(row.get("value"))
        if key is None or value is None:
            continue
        values[key] = value
    return values


# Random-N avg-value red anchor: a random N-item sample averaging >= this implies at least one
# item worth >= this (max >= avg), and empirically (scripts/survey_random_avg_value_anchor.py,
# 198 samples) a per-item random avg >= 50k means a red(q6) item is present in 100% of cases
# (15/15) vs an 83% baseline. We anchor at 100k (稳健, chosen 2026-06-16) to stay clear of the
# thin 50-100k tail. Above this we force q6 >= 1.
RANDOM_AVG_RED_ANCHOR_THRESHOLD = 100_000.0


def _public_random_value_floors(public_info: dict[str, Any]) -> tuple[tuple[int, float], ...]:
    rows: list[dict[str, Any]] = []
    rows.extend(_iter_dicts(public_info.get("public_random_avg_values")))
    rows.extend(_iter_dicts(public_info.get("public_numeric_facts")))
    floors: dict[int, float] = {}
    for row in rows:
        kind = str(row.get("kind") or "")
        semantic = str(row.get("semantic") or "")
        if kind != "random_avg_value" and not re.match(
            r"random[_-]\d+[_-]avg[_-]value",
            semantic,
        ):
            continue
        sample_count = _random_avg_sample_count(row)
        avg_value = _safe_float(row.get("value"))
        if sample_count is None or sample_count <= 0 or avg_value is None or avg_value <= 0:
            continue
        floors[sample_count] = max(floors.get(sample_count, 0.0), sample_count * avg_value)
    return tuple(sorted(floors.items()))


def _candidate_structured_inputs(snapshot: dict[str, Any], ui_contract: dict[str, Any], constraints: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for value in (
        snapshot.get("structured_ref_inputs"),
        ui_contract.get("structured_ref_inputs"),
        constraints.get("structured_ref_inputs"),
        snapshot.get("hero_ref_inputs"),
        ui_contract.get("hero_ref_inputs"),
        constraints.get("hero_ref_inputs"),
        snapshot.get("aisha_ref_inputs"),
        ui_contract.get("aisha_ref_inputs"),
        constraints.get("aisha_ref_inputs"),
        snapshot.get("ahmad_ref_inputs"),
        ui_contract.get("ahmad_ref_inputs"),
        constraints.get("ahmad_ref_inputs"),
        snapshot.get("victor_ref_inputs"),
        ui_contract.get("victor_ref_inputs"),
        constraints.get("victor_ref_inputs"),
    ):
        if isinstance(value, dict):
            candidates.append(value)
    return candidates


def _path_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(part for part in re.split(r"[./]", value) if part)
    if isinstance(value, (list, tuple)):
        return tuple(str(part) for part in value)
    return ()


def _bridge_from_field_updates(
    inputs: dict[str, Any],
    *,
    fixed_counts: dict[str, int],
    min_counts: dict[str, int],
    count_sums: dict[str, int],
    avg_cells: dict[str, float],
    quality_cells: dict[str, float],
    avg_values: dict[str, float],
    quality_values: dict[str, float],
    split_counts: dict[str, int],
    split_quality_cells: dict[str, float],
    split_avg_cells: dict[str, float],
    source_notes: list[str],
) -> tuple[int | None, float | None]:
    total_count: int | None = None
    total_grid_target: float | None = None
    for row in inputs.get("field_updates") or ():
        if not isinstance(row, dict):
            continue
        path = _path_tuple(row.get("path") or row.get("field"))
        value = row.get("value")
        if len(path) >= 2 and path[0] == "session":
            if path[1] in {"total_count", "total_item_count"}:
                parsed = _safe_int(value)
                if parsed is not None:
                    total_count = parsed
                    source_notes.append("field_update_total_count")
            elif path[1] in {"total_cells", "warehouse_total_cells"}:
                parsed_float = _safe_float(value)
                if parsed_float is not None:
                    total_grid_target = parsed_float
                    source_notes.append("field_update_total_cells")
        elif len(path) >= 3 and path[0] == "bucket_group":
            parsed_count = _safe_int(value)
            group_key = str(path[1] or "").strip().lower().replace("+", "")
            if parsed_count is not None and path[2] == "count":
                if group_key in {"q4q5", "45"}:
                    count_sums["q4q5"] = parsed_count
                    source_notes.append("field_update_q4q5_count_sum")
                elif group_key in {"q4q5q6", "456"}:
                    count_sums["q4q5q6"] = parsed_count
                    source_notes.append("field_update_q4q5q6_count_sum")
        elif len(path) >= 3 and path[0] == "bucket":
            key = _quality_number_to_key(path[1])
            if key is None:
                continue
            parsed = _safe_float(value)
            if parsed is None:
                continue
            if path[2] == "avg_cells":
                avg_cells[key] = parsed
                source_notes.append(f"field_update_{key}_avg_cells")
            elif path[2] == "count":
                fixed_counts[key] = int(round(parsed))
                min_counts[key] = int(round(parsed))
                source_notes.append(f"field_update_{key}_count")
            elif path[2] in {"cells", "total_cells"}:
                quality_cells[key] = parsed
                source_notes.append(f"field_update_{key}_cells")
            elif path[2] in {"avg_value", "average_value"}:
                avg_values[key] = parsed
                source_notes.append(f"field_update_{key}_avg_value")
            elif path[2] in {"value", "value_sum", "total_value"}:
                quality_values[key] = parsed
                source_notes.append(f"field_update_{key}_value_sum")
        elif len(path) >= 3 and path[0] == "bucket_split":
            key = _low_split_key(path[1])
            if key is None:
                continue
            parsed = _safe_float(value)
            if parsed is None:
                continue
            if path[2] == "avg_cells":
                split_avg_cells[key] = parsed
                source_notes.append(f"field_update_split_{key}_avg_cells")
            elif path[2] == "count":
                split_counts[key] = int(round(parsed))
                source_notes.append(f"field_update_split_{key}_count")
            elif path[2] in {"cells", "total_cells"}:
                split_quality_cells[key] = parsed
                source_notes.append(f"field_update_split_{key}_cells")
    return total_count, total_grid_target


def _extract_structured_bridge_inputs(
    snapshot: dict[str, Any],
    ui_contract: dict[str, Any],
    constraints: dict[str, Any],
    *,
    fixed_counts: dict[str, int],
    min_counts: dict[str, int],
    count_sums: dict[str, int],
    avg_cells: dict[str, float],
    quality_cells: dict[str, float],
    avg_values: dict[str, float],
    quality_values: dict[str, float],
    split_counts: dict[str, int],
    split_quality_cells: dict[str, float],
    split_avg_cells: dict[str, float],
    source_notes: list[str],
) -> tuple[int | None, float | None]:
    total_count: int | None = None
    total_grid_target: float | None = None
    for inputs in _candidate_structured_inputs(snapshot, ui_contract, constraints):
        parsed_total = _safe_int(
            inputs.get("total_count")
            or inputs.get("total_item_count")
            or inputs.get("session_total_count")
            or inputs.get("session_total_item_count")
        )
        if parsed_total is not None:
            total_count = parsed_total
            source_notes.append("structured_ref_bridge_total_count")
        parsed_cells = _safe_float(
            inputs.get("total_cells")
            or inputs.get("total_grid")
            or inputs.get("warehouse_total_cells")
            or inputs.get("session_total_cells")
        )
        if parsed_cells is not None:
            total_grid_target = parsed_cells
            source_notes.append("structured_ref_bridge_total_cells")
        _merge_quality_values(
            avg_cells,
            inputs.get("avg_cells") or inputs.get("average_cells"),
            note_prefix="structured_ref_bridge_avg_cells",
            source_notes=source_notes,
        )
        _merge_quality_values(
            quality_cells,
            inputs.get("quality_cells") or inputs.get("cells"),
            note_prefix="structured_ref_bridge_cells",
            source_notes=source_notes,
        )
        _merge_quality_values(
            avg_values,
            inputs.get("avg_values") or inputs.get("average_values"),
            note_prefix="structured_ref_bridge_avg_value",
            source_notes=source_notes,
        )
        _merge_quality_values(
            quality_values,
            inputs.get("quality_values")
            or inputs.get("value_sums")
            or inputs.get("values"),
            note_prefix="structured_ref_bridge_value_sum",
            source_notes=source_notes,
        )
        _merge_split_values(
            split_avg_cells,
            inputs.get("split_avg_cells") or inputs.get("low_quality_avg_cells"),
            note_prefix="structured_ref_bridge_split_avg_cells",
            source_notes=source_notes,
        )
        _merge_split_values(
            split_quality_cells,
            (
                inputs.get("split_quality_cells")
                or inputs.get("split_cells")
                or inputs.get("low_quality_cells")
            ),
            note_prefix="structured_ref_bridge_split_cells",
            source_notes=source_notes,
        )
        _merge_quality_counts(
            fixed_counts,
            inputs.get("fixed_counts") or inputs.get("counts"),
            note_prefix="structured_ref_bridge_count",
            source_notes=source_notes,
        )
        _merge_split_counts(
            split_counts,
            inputs.get("split_counts") or inputs.get("low_quality_counts"),
            note_prefix="structured_ref_bridge_split_count",
            source_notes=source_notes,
        )
        _merge_quality_counts(
            min_counts,
            inputs.get("min_counts"),
            note_prefix="structured_ref_bridge_min_count",
            source_notes=source_notes,
        )
        _merge_count_sums(
            count_sums,
            inputs.get("count_sums") or inputs.get("countSums"),
            note_prefix="structured_ref_bridge_count_sum",
            source_notes=source_notes,
        )
        field_total, field_cells = _bridge_from_field_updates(
            inputs,
            fixed_counts=fixed_counts,
            min_counts=min_counts,
            count_sums=count_sums,
            avg_cells=avg_cells,
            quality_cells=quality_cells,
            avg_values=avg_values,
            quality_values=quality_values,
            split_counts=split_counts,
            split_quality_cells=split_quality_cells,
            split_avg_cells=split_avg_cells,
            source_notes=source_notes,
        )
        if field_total is not None:
            total_count = field_total
        if field_cells is not None:
            total_grid_target = field_cells
    return total_count, total_grid_target


def _parse_static_arrays(text: str, name: str) -> dict[str, list[float]]:
    anchor = f"{name} = new Dictionary"
    start = text.find(anchor)
    if start < 0:
        return {}
    end = text.find("};", start)
    if end < 0:
        return {}
    block = text[start:end]
    pattern = re.compile(
        r'\{\s*"(?P<key>[^"]+)"\s*,\s*new double\[\d+\]\s*\{(?P<values>[^}]+)\}\s*\}',
        re.MULTILINE,
    )
    result: dict[str, list[float]] = {}
    for match in pattern.finditer(block):
        values = [
            float(item.strip())
            for item in match.group("values").split(",")
            if item.strip()
        ]
        result[match.group("key")] = values
    return result


def _parse_map_nests(text: str) -> dict[int, tuple[str, str]]:
    result: dict[int, tuple[str, str]] = {}
    current_map: str | None = None
    current_name: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        map_match = re.search(r'MapId\s*=\s*"(?P<id>\d+)"', line)
        if map_match:
            current_map = map_match.group("id")
            current_name = None
            continue
        name_match = re.search(r'MapName\s*=\s*"(?P<name>[^"]*)"', line)
        if name_match and current_map is not None:
            current_name = name_match.group("name")
            continue
        nest_match = re.search(r'NestId\s*=\s*"(?P<nest>[^"]+)"', line)
        if nest_match and current_map is not None:
            result[int(current_map)] = (nest_match.group("nest"), current_name or "")
            current_map = None
            current_name = None
    return result


@lru_cache(maxsize=4)
def load_reference_static_data(path: Path = STATIC_DATA) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return {"drop_weights": {}, "nest_prices": {}, "map_nests": {}}
    return {
        "drop_weights": _parse_static_arrays(text, "DropWeights"),
        "nest_prices": _parse_static_arrays(text, "NestWeightedPrices"),
        "map_nests": _parse_map_nests(text),
    }


def _map_tier(map_id: int | None) -> str:
    if map_id is None:
        return "104"
    family = int(map_id) // 100
    if family == 21:
        return "101"
    if family == 22:
        return "102"
    if family == 23:
        return "103"
    if family == 24:
        return "104"
    if family in {25, 45}:
        return "105"
    if family == 26:
        return "106"
    return "104"


def _activity_price_alias_map_id(
    map_id: int | None,
    static_data: dict[str, Any],
) -> tuple[int | None, str]:
    if map_id is None:
        return None, ""
    map_nests: dict[int, tuple[str, str]] = static_data.get("map_nests", {})
    nest_prices: dict[str, list[float]] = static_data.get("nest_prices", {})
    current_nest_id = map_nests.get(int(map_id), ("", ""))[0]
    if current_nest_id and nest_prices.get(current_nest_id):
        return map_id, ""

    candidates: list[tuple[str, int]] = []
    if 2521 <= int(map_id) <= 2540:
        # Only 2501-2510 carry configured nest prices (2511-2520 do NOT), so 2521-2530 alias via -20
        # and 2531-2540 via -30 -- both land in the configured 2501-2510 base. -10 kept as a fallback.
        candidates.append(("activity_shipwreck_minus20", int(map_id) - 20))
        candidates.append(("activity_shipwreck_minus30", int(map_id) - 30))
        candidates.append(("activity_shipwreck_minus10", int(map_id) - 10))
    elif 4501 <= int(map_id) <= 4540:
        # Deep-sea blind auction (暗拍) is tier105 like the 25xx sealed cabins and differs
        # only in hiding rival bids; the reference StaticData carries no 45xx nest at all,
        # so price via the SAME cabin index on the configured 2501-2510 base:
        #   4501/4521/4531 -> 2501, 4502/4532 -> 2502, ... 4510/4540 -> 2510.
        sealed_cabin = 2501 + ((int(map_id) - 1) % 10)
        candidates.append(("blind_auction_to_sealed_cabin", sealed_cabin))

    for mode, candidate in candidates:
        nest_id = map_nests.get(candidate, ("", ""))[0]
        prices = nest_prices.get(nest_id)
        if prices and len(prices) >= 6:
            return candidate, f"{mode}:{map_id}->{candidate}"
    return map_id, ""


def _quality_item_values(
    map_id: int | None,
    static_data: dict[str, Any],
) -> tuple[dict[str, float], str]:
    map_nests: dict[int, tuple[str, str]] = static_data.get("map_nests", {})
    nest_prices: dict[str, list[float]] = static_data.get("nest_prices", {})
    price_map_id, alias_note = _activity_price_alias_map_id(map_id, static_data)
    nest_id = map_nests.get(int(price_map_id or 0), ("", ""))[0]
    prices = nest_prices.get(nest_id)
    if not prices or len(prices) < 6:
        return dict(DEFAULT_ITEM_VALUES), "fallback_default_price"
    q1_indexes = QUALITY_TO_INDEX["q1"]
    values = {
        "q1": sum(prices[index] for index in q1_indexes) / len(q1_indexes),
        "q3": prices[2],
        "q4": prices[3],
        "q5": prices[4],
        "q6": prices[5],
    }
    note = f"nest_price:{nest_id}"
    if alias_note:
        note = f"{note};{alias_note}"
    return values, note


EXACT_CATALOG_ITEM_VALUE_LOCK_NOTE = "exact_catalog_item_value_lock"
EXACT_CATALOG_AVG_NO_ITEM_MATCH_NOTE = "exact_catalog_avg_no_item_match"
EXACT_CATALOG_VALUE_KEYS = ("q5", "q6")
QUALITY_KEY_TO_ITEM_TABLE_QUALITY = {"q5": 5, "q6": 6}


@lru_cache(maxsize=1)
def _exact_item_values_by_table_quality() -> dict[int, frozenset[int]]:
    """Lazy Item.txt lookup: quality -> set of exact single-item values."""
    try:
        from bidking_lab.extract.item_table import load_item_table

        path = ROOT / "data" / "raw" / "tables" / "Item.txt"
        if not path.is_file():
            return {}
        items = load_item_table(path)
        grouped: dict[int, set[int]] = {}
        for item in items.values():
            if int(item.value) > 0:
                grouped.setdefault(int(item.quality), set()).add(int(item.value))
        return {quality: frozenset(values) for quality, values in grouped.items()}
    except Exception:
        return {}


def _quality_key_item_table_quality(key: str) -> int | None:
    return QUALITY_KEY_TO_ITEM_TABLE_QUALITY.get(str(key or "").strip().lower())


def _exact_catalog_contains_value(key: str, value: float | int | None) -> bool:
    parsed = _safe_float(value)
    if parsed is None or parsed <= 0:
        return False
    table_quality = _quality_key_item_table_quality(key)
    if table_quality is None:
        return False
    values = _exact_item_values_by_table_quality().get(int(table_quality))
    if not values:
        return False
    return int(round(float(parsed))) in values


def _avg_is_exact_catalog_item_value(key: str, avg: float | None) -> bool:
    normalized = _ref_normalize_avg_value_wire(avg)
    if normalized is None or normalized <= 0:
        return False
    return _exact_catalog_contains_value(key, normalized)


def _apply_exact_catalog_value_locks(
    *,
    fixed_counts: dict[str, int],
    min_counts: dict[str, int],
    avg_values: dict[str, float],
    quality_values: dict[str, float],
    source_notes: list[str],
) -> None:
    """Lock q5/q6 count only when avg/total matches an exact Item.txt single-item price."""
    for key in EXACT_CATALOG_VALUE_KEYS:
        if fixed_counts.get(key) is not None:
            continue
        exact_total = quality_values.get(key)
        if exact_total is not None and _exact_catalog_contains_value(key, exact_total):
            derived = 1
            avg = avg_values.get(key)
            if avg is not None:
                parsed = _avg_value_count_from_total(avg, exact_total)
                if parsed is not None:
                    derived = int(parsed)
            fixed_counts[key] = int(derived)
            min_counts[key] = max(int(min_counts.get(key, 0) or 0), int(derived))
            _append_source_note_once(
                source_notes,
                f"{EXACT_CATALOG_ITEM_VALUE_LOCK_NOTE}:{key}:{int(round(float(exact_total)))}",
            )
            continue
        avg = avg_values.get(key)
        if avg is None or not _avg_value_has_positive_signal(avg):
            continue
        if not _avg_is_exact_catalog_item_value(key, avg):
            _append_source_note_once(
                source_notes,
                f"{EXACT_CATALOG_AVG_NO_ITEM_MATCH_NOTE}:{key}",
            )
            continue
        if not _avg_value_count_matches(1, avg):
            continue
        fixed_counts[key] = 1
        min_counts[key] = max(int(min_counts.get(key, 0) or 0), 1)
        _append_source_note_once(
            source_notes,
            f"{EXACT_CATALOG_ITEM_VALUE_LOCK_NOTE}:{key}:{int(round(float(avg)))}",
        )


def _quality_probabilities(
    map_id: int | None,
    static_data: dict[str, Any],
) -> tuple[dict[str, float], str]:
    drop_weights: dict[str, list[float]] = static_data.get("drop_weights", {})
    tier = _map_tier(map_id)
    weights = drop_weights.get(tier) or drop_weights.get("104") or []
    if len(weights) < 6:
        return {key: 1.0 / len(QUALITY_KEYS) for key in QUALITY_KEYS}, "fallback_uniform_prob"
    collapsed = {
        "q1": weights[0] + weights[1],
        "q3": weights[2],
        "q4": weights[3],
        "q5": weights[4],
        "q6": weights[5],
    }
    total = sum(collapsed.values()) or 1.0
    return {key: max(1e-9, value / total) for key, value in collapsed.items()}, f"tier_prob:{tier}"


def _grid_fixed_for_total_target(
    key: str,
    *,
    counts: dict[str, int],
    avg_cells: dict[str, float],
    fixed_grid_keys: set[str],
    soft_avg_cell_keys: frozenset[str],
) -> bool:
    if counts.get(key, 0) == 0:
        return True
    if key in fixed_grid_keys:
        return True
    if key in avg_cells and key not in soft_avg_cell_keys:
        return True
    return False


def _fit_grids_to_total_target(
    grids: dict[str, float],
    counts: dict[str, int],
    avg_cells: dict[str, float],
    target: float | None,
    fixed_grid_keys: set[str] | None = None,
    soft_avg_cell_keys: frozenset[str] | None = None,
) -> dict[str, float]:
    if target is None or target <= 0:
        return grids
    fixed_grid_keys = fixed_grid_keys or set()
    soft_avg_cell_keys = soft_avg_cell_keys or frozenset()
    fixed_values = [
        grids[key]
        for key in QUALITY_KEYS
        if _grid_fixed_for_total_target(
            key,
            counts=counts,
            avg_cells=avg_cells,
            fixed_grid_keys=fixed_grid_keys,
            soft_avg_cell_keys=soft_avg_cell_keys,
        )
    ]
    scalable_keys = [
        key
        for key in QUALITY_KEYS
        if not _grid_fixed_for_total_target(
            key,
            counts=counts,
            avg_cells=avg_cells,
            fixed_grid_keys=fixed_grid_keys,
            soft_avg_cell_keys=soft_avg_cell_keys,
        )
    ]
    target_int = int(round(target))
    if (
        scalable_keys
        and abs(float(target) - target_int) <= 0.25
        and all(abs(value - round(value)) <= 1e-6 for value in fixed_values)
    ):
        fixed_total_int = sum(int(round(value)) for value in fixed_values)
        remaining = target_int - fixed_total_int
        option_map = {
            key: tuple(
                option
                for option in _composable_grid_options(int(counts[key]))
                if option <= remaining
            )
            for key in scalable_keys
        }
        if remaining >= 0 and all(option_map.values()):
            states: dict[int, tuple[float, dict[str, int]]] = {0: (0.0, {})}
            for key in scalable_keys:
                default = grids[key]
                scale = max(1.0, abs(default))
                next_states: dict[int, tuple[float, dict[str, int]]] = {}
                for current_sum, (cost, assignment) in states.items():
                    for option in option_map[key]:
                        new_sum = current_sum + option
                        if new_sum > remaining:
                            continue
                        new_cost = cost + ((option - default) / scale) ** 2
                        existing = next_states.get(new_sum)
                        if existing is not None and existing[0] <= new_cost:
                            continue
                        next_assignment = dict(assignment)
                        next_assignment[key] = option
                        next_states[new_sum] = (new_cost, next_assignment)
                states = next_states
                if not states:
                    break
            exact = states.get(remaining)
            if exact is not None:
                fitted = dict(grids)
                for key, option in exact[1].items():
                    fitted[key] = float(option)
                return fitted

    fixed_total = sum(fixed_values)
    scalable_total = sum(grids[key] for key in scalable_keys)
    if not scalable_keys or scalable_total <= 0:
        return grids
    remaining = max(0.0, target - fixed_total)
    scale = remaining / scalable_total
    fitted = dict(grids)
    for key in scalable_keys:
        fitted[key] = max(float(counts[key]), grids[key] * scale)
    return fitted


def _apply_low_quality_split_evidence(
    *,
    total_count: int | None,
    split_counts: dict[str, int],
    split_quality_cells: dict[str, float],
    split_avg_cells: dict[str, float],
    fixed_counts: dict[str, int],
    min_counts: dict[str, int],
    avg_cells: dict[str, float],
    quality_cells: dict[str, float],
    source_notes: list[str],
) -> None:
    has_split_signal = bool(split_counts or split_quality_cells or split_avg_cells)
    if has_split_signal and fixed_counts.get("q1") is None and total_count is not None:
        other_keys = tuple(key for key in QUALITY_KEYS if key != "q1")
        if all(fixed_counts.get(key) is not None for key in other_keys):
            residual = int(total_count) - sum(int(fixed_counts[key]) for key in other_keys)
            known_split_count = sum(
                int(split_counts[key])
                for key in LOW_SPLIT_KEYS
                if split_counts.get(key) is not None
            )
            min_q1 = max(int(min_counts.get("q1", 0)), known_split_count)
            if residual < min_q1:
                source_notes.append("split_low_quality_q1_count_total_residual_conflict")
                source_notes.append("hard_conflict:split_low_quality_q1_count_total_residual")
            elif residual >= 0:
                fixed_counts["q1"] = residual
                min_counts["q1"] = max(min_counts.get("q1", 0), residual)
                source_notes.append("split_low_quality_q1_count_from_total_residual")

    if has_split_signal and fixed_counts.get("q1") is None and quality_cells.get("q1") is not None:
        derived_count = _avg_count_from_cells(avg_cells.get("q1"), quality_cells.get("q1"))
        if derived_count is not None:
            fixed_counts["q1"] = derived_count
            min_counts["q1"] = max(min_counts.get("q1", 0), derived_count)
            source_notes.append("split_low_quality_q1_count_from_avg_cells")

    if has_split_signal and quality_cells.get("q1") is None and fixed_counts.get("q1") is not None:
        options = _avg_grid_options(int(fixed_counts["q1"]), avg_cells.get("q1"))
        if len(options) == 1:
            quality_cells["q1"] = float(options[0])
            source_notes.append("split_low_quality_q1_cells_from_avg_count")

    if has_split_signal and fixed_counts.get("q1") is not None:
        missing_counts = [
            key for key in LOW_SPLIT_KEYS if split_counts.get(key) is None
        ]
        known_counts = [
            int(split_counts[key])
            for key in LOW_SPLIT_KEYS
            if split_counts.get(key) is not None
        ]
        if len(missing_counts) == 1 and len(known_counts) == 1:
            missing = int(fixed_counts["q1"]) - known_counts[0]
            if missing < 0:
                source_notes.append("split_low_quality_q1_count_complement_conflict")
                source_notes.append("hard_conflict:split_low_quality_q1_count_complement")
            else:
                split_counts[missing_counts[0]] = missing
                source_notes.append(f"split_low_quality_{missing_counts[0]}_count_from_q1_exact")

    if has_split_signal and quality_cells.get("q1") is not None:
        missing_cells = [
            key for key in LOW_SPLIT_KEYS if split_quality_cells.get(key) is None
        ]
        known_cells = [
            float(split_quality_cells[key])
            for key in LOW_SPLIT_KEYS
            if split_quality_cells.get(key) is not None
        ]
        if len(missing_cells) == 1 and len(known_cells) == 1:
            missing = float(quality_cells["q1"]) - known_cells[0]
            if missing < -0.0001:
                source_notes.append("split_low_quality_q1_cells_complement_conflict")
                source_notes.append("hard_conflict:split_low_quality_q1_cells_complement")
            else:
                split_quality_cells[missing_cells[0]] = max(0.0, missing)
                source_notes.append(f"split_low_quality_{missing_cells[0]}_cells_from_q1_exact")

    for split_key in LOW_SPLIT_KEYS:
        count = split_counts.get(split_key)
        cells = split_quality_cells.get(split_key)
        avg = split_avg_cells.get(split_key)
        if avg == 0 and count is None:
            split_counts[split_key] = 0
            count = 0
            source_notes.append(f"split_low_quality_{split_key}_zero_avg_count_zero")
        if count is None and avg is not None and cells is not None:
            derived_count = _avg_count_from_cells(avg, cells)
            if derived_count is None:
                source_notes.append(f"split_low_quality_{split_key}_avg_cells_conflict")
                source_notes.append(f"hard_conflict:split_low_quality_{split_key}_avg_cells")
            else:
                split_counts[split_key] = derived_count
                count = derived_count
                source_notes.append(f"split_low_quality_{split_key}_count_derived")
        if cells is None and count is not None and avg is not None:
            options = _avg_grid_options(int(count), avg)
            if len(options) == 1:
                split_quality_cells[split_key] = float(options[0])
                cells = float(options[0])
                source_notes.append(f"split_low_quality_{split_key}_cells_derived")
            elif not options:
                source_notes.append(f"split_low_quality_{split_key}_avg_count_conflict")
                source_notes.append(f"hard_conflict:split_low_quality_{split_key}_avg_count")
        if avg is None and count is not None and cells is not None:
            if not can_compose_grid_total(int(count), int(round(float(cells)))):
                source_notes.append(f"split_low_quality_{split_key}_count_cells_conflict")
                source_notes.append(f"hard_conflict:split_low_quality_{split_key}_count_cells")
                continue
            if int(count) == 0:
                if abs(float(cells)) > 0.0001:
                    source_notes.append(f"split_low_quality_{split_key}_count_cells_conflict")
                    source_notes.append(f"hard_conflict:split_low_quality_{split_key}_count_cells")
                else:
                    split_avg_cells[split_key] = 0.0
                    source_notes.append(f"split_low_quality_{split_key}_avg_derived")
            else:
                split_avg_cells[split_key] = float(cells) / float(count)
                source_notes.append(f"split_low_quality_{split_key}_avg_derived")
        elif avg is not None and count is not None and cells is not None:
            cells_int = int(round(float(cells)))
            if not can_compose_grid_total(int(count), cells_int) or not _avg_matches_exact_grid(
                int(count),
                avg,
                cells_int,
            ):
                source_notes.append(f"split_low_quality_{split_key}_avg_count_cells_conflict")
                source_notes.append(f"hard_conflict:split_low_quality_{split_key}_avg_count_cells")

    known_count_sum = sum(
        int(split_counts[key])
        for key in LOW_SPLIT_KEYS
        if split_counts.get(key) is not None
    )
    if known_count_sum > 0:
        min_counts["q1"] = max(min_counts.get("q1", 0), known_count_sum)
        source_notes.append("split_low_quality_q1_min_count")
    if _split_low_quality_q1_grid_extra_from_maps(split_counts, split_quality_cells) > 0:
        source_notes.append("split_low_quality_q1_grid_floor")

    have_all_counts = all(split_counts.get(key) is not None for key in LOW_SPLIT_KEYS)
    have_all_cells = all(split_quality_cells.get(key) is not None for key in LOW_SPLIT_KEYS)
    coarse_split_floor = (
        "coarse_quality_reveal_split_counts" in source_notes
        or "maria_skill_coarse_quality_split_counts" in source_notes
    )
    split_counts_exact = (not coarse_split_floor) or all(
        f"structured_ref_bridge_split_count_{key}" in source_notes
        or f"split_low_quality_{key}_count_derived" in source_notes
        or f"split_low_quality_{key}_count_from_q1_exact" in source_notes
        or f"split_low_quality_{key}_zero_avg_count_zero" in source_notes
        for key in LOW_SPLIT_KEYS
    )
    if have_all_counts:
        merged_count = sum(int(split_counts[key]) for key in LOW_SPLIT_KEYS)
        existing_count = fixed_counts.get("q1")
        if existing_count is None and not split_counts_exact:
            # White/green split counts seen so far came from random public/skill sample
            # reveals (e.g. "未知别墅" shows a random subset). Those are floors: there may
            # be more white/green items that were never sampled. Without an exact q1 to
            # anchor against, treat the sampled sum as a minimum for q1 instead of
            # locking q1 to the sampled count, which would badly under-count low items
            # and over-allocate the total to higher tiers.
            min_counts["q1"] = max(min_counts.get("q1", 0), merged_count)
            source_notes.append("split_low_quality_q1_coarse_floor_only")
        elif existing_count is not None and int(existing_count) != merged_count:
            if (
                int(existing_count) > merged_count
                and "coarse_quality_reveal_split_counts" in source_notes
            ):
                # Public coarse split counts come from random sample floors, not an
                # exact white/green warehouse breakdown. Exact q1 skill/count wins.
                min_counts["q1"] = max(min_counts.get("q1", 0), int(existing_count))
                source_notes.append("split_low_quality_q1_exact_overrides_coarse_split")
            else:
                source_notes.append("split_low_quality_q1_count_conflict")
                source_notes.append("hard_conflict:split_low_quality_q1_count")
        else:
            fixed_counts["q1"] = merged_count
            min_counts["q1"] = max(min_counts.get("q1", 0), merged_count)
            source_notes.append("split_low_quality_q1_count_merged")
    if have_all_cells:
        merged_cells = sum(float(split_quality_cells[key]) for key in LOW_SPLIT_KEYS)
        existing_cells = quality_cells.get("q1")
        if existing_cells is not None and abs(float(existing_cells) - merged_cells) > 0.0001:
            source_notes.append("split_low_quality_q1_cells_conflict")
            source_notes.append("hard_conflict:split_low_quality_q1_cells")
        else:
            quality_cells["q1"] = merged_cells
            source_notes.append("split_low_quality_q1_cells_merged")
    if fixed_counts.get("q1") is not None and quality_cells.get("q1") is not None and "q1" not in avg_cells:
        count = int(fixed_counts["q1"])
        cells = float(quality_cells["q1"])
        if count > 0:
            avg_cells["q1"] = cells / count
            source_notes.append("split_low_quality_q1_avg_derived")
        elif abs(cells) <= 0.0001:
            avg_cells["q1"] = 0.0
            source_notes.append("split_low_quality_q1_avg_derived")


def _layout_stage_warehouse_cells_p50(snapshot: dict[str, Any]) -> float | None:
    rows = snapshot.get("layout_stage_rows") or ()
    for row in reversed(tuple(rows)):
        if not isinstance(row, dict):
            continue
        estimate = str(row.get("布局估计") or "").strip()
        parts = [part.strip() for part in estimate.split("/") if part.strip()]
        if len(parts) < 2:
            continue
        parsed = _safe_float(parts[1])
        if parsed is not None and parsed > 0:
            return parsed
    return None


def _settlement_review_total_cells(snapshot: dict[str, Any], truth: Mapping[str, Any]) -> float | None:
    item_cells = _safe_float(truth.get("total_cells"))
    if item_cells is None:
        item_cells = _safe_float(snapshot.get("inventory_cells"))
    layout_cells = _layout_stage_warehouse_cells_p50(snapshot)
    if layout_cells is not None and (item_cells is None or float(layout_cells) > float(item_cells)):
        return float(layout_cells)
    return item_cells


def extract_evidence(snapshot: dict[str, Any]) -> RefEvidence:
    uc = snapshot.get("ui_contract") if isinstance(snapshot.get("ui_contract"), dict) else {}
    context = uc.get("context") if isinstance(uc.get("context"), dict) else {}
    baseline = uc.get("baseline") if isinstance(uc.get("baseline"), dict) else {}
    decision = baseline.get("decision") if isinstance(baseline.get("decision"), dict) else {}
    posterior = baseline.get("posterior") if isinstance(baseline.get("posterior"), dict) else {}
    constraints = uc.get("constraints") if isinstance(uc.get("constraints"), dict) else {}
    counts = constraints.get("counts") if isinstance(constraints.get("counts"), dict) else {}
    summary = constraints.get("summary") if isinstance(constraints.get("summary"), dict) else {}
    public_info = constraints.get("public_info") if isinstance(constraints.get("public_info"), dict) else {}
    actions = uc.get("actions") if isinstance(uc.get("actions"), dict) else {}
    truth = uc.get("truth") if isinstance(uc.get("truth"), dict) else {}
    source_notes: list[str] = []

    hero = _hero_from_context(
        context.get("hero") or snapshot.get("hero"),
        context.get("hero_id"),
        context.get("player_hero_id"),
        context.get("current_player_hero_id"),
        snapshot.get("hero_id"),
        snapshot.get("player_hero_id"),
        snapshot.get("current_player_hero_id"),
    )
    if _is_unknown_hero(hero):
        for candidate in _candidate_structured_inputs(snapshot, uc, constraints):
            structured_hero = _hero_from_context(candidate.get("hero"))
            if not _is_unknown_hero(structured_hero):
                hero = structured_hero
                source_notes.append("structured_hero")
                break
    map_id = _safe_int(context.get("map_id") or snapshot.get("map_id"))
    phase = str(context.get("phase") or snapshot.get("phase") or "")
    fixed_counts: dict[str, int] = {}
    min_counts: dict[str, int] = {}
    count_sums: dict[str, int] = {}
    avg_cells: dict[str, float] = {}
    quality_cells: dict[str, float] = {}
    quality_cell_floors: dict[str, float] = {}
    avg_values: dict[str, float] = {}
    quality_values: dict[str, float] = {}
    quality_value_floors: dict[str, float] = {}
    quality_value_floor_item_counts: dict[str, int] = {}
    split_counts: dict[str, int] = {}
    split_quality_cells: dict[str, float] = {}
    split_avg_cells: dict[str, float] = {}
    random_value_floors: tuple[tuple[int, float], ...] = ()

    bridge_total_count, bridge_total_cells = _extract_structured_bridge_inputs(
        snapshot,
        uc,
        constraints,
        fixed_counts=fixed_counts,
        min_counts=min_counts,
        count_sums=count_sums,
        avg_cells=avg_cells,
        quality_cells=quality_cells,
        avg_values=avg_values,
        quality_values=quality_values,
        split_counts=split_counts,
        split_quality_cells=split_quality_cells,
        split_avg_cells=split_avg_cells,
        source_notes=source_notes,
    )

    total_count = bridge_total_count
    if total_count is None:
        total_count = _safe_int(summary.get("input_total_item_count"))
    if total_count is None:
        total_count = _safe_int(counts.get("input_total_item_count"))

    def set_total_count(value: Any, note: str) -> None:
        nonlocal total_count
        parsed = _safe_int(value)
        if parsed is None:
            return
        if total_count is not None and int(total_count) != int(parsed):
            source_notes.append(f"{note}_conflicts_total_count:{total_count}->{parsed}")
        total_count = int(parsed)
        source_notes.append(note)

    settlement_total_count = _safe_int(truth.get("total_items"))
    if phase == "settled" and settlement_total_count is not None:
        if total_count is not None and int(total_count) != int(settlement_total_count):
            source_notes.append("settlement_review_total_count_overrode_bridge")
        total_count = settlement_total_count
        if "settlement_review_total_count" not in source_notes:
            source_notes.append("settlement_review_total_count")

    total_grid_target = bridge_total_cells
    if total_grid_target is None:
        total_grid_target = _safe_float(summary.get("input_warehouse_total_cells"))
    if total_grid_target is None:
        total_grid_target = _safe_float(counts.get("input_warehouse_total_cells"))

    def set_total_grid_target(value: Any, note: str) -> None:
        nonlocal total_grid_target
        parsed = _safe_float(value)
        if parsed is None:
            return
        if (
            total_grid_target is not None
            and abs(float(total_grid_target) - float(parsed)) > 0.0001
        ):
            source_notes.append(f"{note}_conflicts_total_grid:{total_grid_target}->{parsed}")
        total_grid_target = float(parsed)
        source_notes.append(note)

    if total_grid_target is None:
        public_constraints = public_info.get("input_constraints")
        if isinstance(public_constraints, dict):
            set_total_grid_target(
                public_constraints.get("total_cells")
                or public_constraints.get("warehouse_total_cells"),
                "public_total_cells",
            )
    settlement_total_cells = _settlement_review_total_cells(snapshot, truth)
    if phase == "settled" and settlement_total_cells is not None:
        layout_cells = _layout_stage_warehouse_cells_p50(snapshot)
        item_cells = _safe_float(truth.get("total_cells")) or _safe_float(snapshot.get("inventory_cells"))
        if (
            layout_cells is not None
            and item_cells is not None
            and float(layout_cells) > float(item_cells) + 0.5
        ):
            source_notes.append("settlement_review_total_grid_from_layout_replay")
        if (
            total_grid_target is not None
            and abs(float(total_grid_target) - float(settlement_total_cells)) > 0.0001
        ):
            source_notes.append("settlement_review_total_grid_overrode_bridge")
        total_grid_target = settlement_total_cells
        if "settlement_review_total_grid" not in source_notes:
            source_notes.append("settlement_review_total_grid")

    known_quality_counts: dict[str, int] = {}
    known_quality_source = ""
    if phase == "settled":
        known_quality_counts.update(_parse_quality_kv(str(snapshot.get("final_quality_counts") or "")))
        known_quality_source = "settlement"
    if not known_quality_counts:
        raw_known = counts.get("known_quality_counts")
        known_quality_source = "constraints"
        if isinstance(raw_known, dict):
            known_quality_counts.update(
                {str(k): int(v) for k, v in raw_known.items() if _safe_int(v) is not None}
            )
    q1_combined = (
        _safe_int(known_quality_counts.get("q1") or 0) or 0
    ) + (_safe_int(known_quality_counts.get("q2") or 0) or 0)
    quality_counts = {
        "q1": q1_combined,
        "q3": _safe_int(known_quality_counts.get("q3")) or 0,
        "q4": _safe_int(known_quality_counts.get("q4")) or 0,
        "q5": _safe_int(known_quality_counts.get("q5")) or 0,
        "q6": _safe_int(known_quality_counts.get("q6")) or 0,
    }
    if phase == "settled":
        known_quality_cells = _parse_quality_kv(str(snapshot.get("final_quality_cells") or ""))
        q1_cells_combined = (
            _safe_int(known_quality_cells.get("q1") or 0) or 0
        ) + (_safe_int(known_quality_cells.get("q2") or 0) or 0)
        for key, value in {
            "q1": q1_cells_combined,
            "q3": _safe_int(known_quality_cells.get("q3")) or 0,
            "q4": _safe_int(known_quality_cells.get("q4")) or 0,
            "q5": _safe_int(known_quality_cells.get("q5")) or 0,
            "q6": _safe_int(known_quality_cells.get("q6")) or 0,
        }.items():
            if value > 0:
                quality_cells[key] = float(value)
                avg_cells.pop(key, None)

    if known_quality_source == "settlement":
        for key in QUALITY_KEYS:
            fixed_counts.pop(key, None)
            min_counts.pop(key, None)

    for key, value in quality_counts.items():
        if value > 0:
            min_counts.setdefault(key, value)
    if total_count is not None and sum(quality_counts.values()) == total_count:
        fixed_counts.update(
            {
                key: value
                for key, value in quality_counts.items()
                if key not in fixed_counts
            }
        )
        if phase == "settled" or known_quality_source == "settlement":
            source_notes.append("settlement_review_known_quality_counts_sum_to_total")
        else:
            source_notes.append("known_quality_counts_sum_to_total")
    elif phase == "settled" and total_count is not None:
        fixed_counts.update(
            {
                key: value
                for key, value in quality_counts.items()
                if key not in fixed_counts
            }
        )
        source_notes.append("settlement_review_fixed_counts")

    if phase != "settled":
        (
            public_quality_counts,
            public_quality_cell_floors,
            public_quality_value_floors,
            public_quality_value_floor_item_counts,
            coarse_split_counts,
            _coarse_split_value_floors,
        ) = _coarse_quality_reveal_floors(snapshot, source_notes=source_notes)
        if public_quality_counts:
            for key, value in public_quality_counts.items():
                min_counts[key] = max(min_counts.get(key, 0), value)
            if "public_quality_reveal_min_counts" not in source_notes:
                source_notes.append("public_quality_reveal_min_counts")
        if coarse_split_counts:
            for key, value in coarse_split_counts.items():
                split_counts[key] = max(split_counts.get(key, 0), value)
            source_notes.append("coarse_quality_reveal_split_counts")
        if public_quality_cell_floors:
            for key, value in public_quality_cell_floors.items():
                quality_cell_floors[key] = max(
                    quality_cell_floors.get(key, 0.0),
                    float(value),
                )
                source_notes.append(f"public_quality_reveal_{key}_cell_floor")
        if public_quality_value_floors:
            for key, value in public_quality_value_floors.items():
                quality_value_floors[key] = max(
                    quality_value_floors.get(key, 0.0),
                    float(value),
                )
                source_notes.append(f"public_quality_reveal_{key}_value_floor")
        if public_quality_value_floor_item_counts:
            for key, value in public_quality_value_floor_item_counts.items():
                quality_value_floor_item_counts[key] = max(
                    quality_value_floor_item_counts.get(key, 0),
                    int(value),
                )
        _apply_maria_skill_evidence(
            snapshot,
            min_counts=min_counts,
            split_counts=split_counts,
            quality_value_floors=quality_value_floors,
            source_notes=source_notes,
        )
        _apply_ethan_skill_evidence(
            snapshot,
            set_total_count=set_total_count,
            set_total_grid_target=set_total_grid_target,
            source_notes=source_notes,
        )
        total_count = _apply_all_item_quality_exact_counts(
            snapshot,
            hero=hero,
            total_count=total_count,
            fixed_counts=fixed_counts,
            min_counts=min_counts,
            quality_cells=quality_cells,
            set_total_count=set_total_count,
            source_notes=source_notes,
        )
        public_outline_totals = _public_bucket_outline_totals(snapshot)
        for key, (count, cells) in public_outline_totals.items():
            existing_count = fixed_counts.get(key)
            if existing_count is not None and int(existing_count) != int(count):
                source_notes.append(f"public_bucket_outline_{key}_count_conflict")
                continue
            fixed_counts[key] = count
            min_counts[key] = max(min_counts.get(key, 0), count)
            source_notes.append(f"public_bucket_outline_{key}_count")
            if cells is None:
                continue
            existing_cells = quality_cells.get(key)
            if existing_cells is not None and abs(float(existing_cells) - float(cells)) > 0.0001:
                source_notes.append(f"public_bucket_outline_{key}_cells_conflict")
                continue
            quality_cells[key] = float(cells)
            source_notes.append(f"public_bucket_outline_{key}_cells")

    for row in actions.get("results") or ():
        if not isinstance(row, dict):
            continue
        action_id = str(row.get("action_id") or "")
        diagnostic_semantic = ACTION_DIAGNOSTIC_ONLY.get(action_id)
        if diagnostic_semantic is not None:
            source_notes.append(f"action_{action_id}_{diagnostic_semantic}_diagnostic_only")
            revealed_count = _safe_int(row.get("revealed_items"))
            if revealed_count is not None and revealed_count > 0:
                source_notes.append(f"action_{action_id}_revealed_items:{revealed_count}")
        value = _safe_float(row.get("result"))
        if value is None:
            continue
        note_suffix = "_inferred_zero" if row.get("inferred_zero") else ""
        quality_for_avg = ACTION_AVG_CELLS.get(action_id)
        quality_for_cells = ACTION_TOTAL_CELLS.get(action_id)
        quality_for_value = ACTION_VALUE_SUM.get(action_id)
        quality_for_count = ACTION_COUNTS.get(action_id)
        if quality_for_avg is not None:
            avg_cells[quality_for_avg] = value
            source_notes.append(f"action_{action_id}_{quality_for_avg}_avg_cells{note_suffix}")
        elif quality_for_cells is not None:
            quality_cells[quality_for_cells] = value
            source_notes.append(f"action_{action_id}_{quality_for_cells}_cells{note_suffix}")
        elif quality_for_value is not None:
            quality_values[quality_for_value] = value
            source_notes.append(f"action_{action_id}_{quality_for_value}_value_sum{note_suffix}")
        elif quality_for_count is not None:
            fixed_counts[quality_for_count] = int(round(value))
            min_counts[quality_for_count] = int(round(value))
            source_notes.append(f"action_{action_id}_{quality_for_count}_count{note_suffix}")
        elif action_id in {"100115", "100204"}:
            set_total_count(value, f"action_{action_id}_total_count")
        elif action_id == "100103":
            set_total_grid_target(value, "action_100103_total_cells")

    for row in public_info.get("public_numeric_facts") or ():
        if not isinstance(row, dict):
            continue
        semantic = str(row.get("semantic") or "")
        value = _safe_float(row.get("value"))
        if value is None:
            continue
        if semantic == "total_item_count":
            set_total_count(value, "public_total_item_count")
        elif semantic == "total_cells":
            set_total_grid_target(value, "public_total_cells")
        elif semantic == "total_avg_cells":
            # Store the all-items avg-cells under a non-quality key so it survives for the
            # grid<->count cross-check below. When count is locked it also pins the grid target
            # (avg * count); when count is UNKNOWN it instead lets us derive count from the minimap
            # grid (count ~= grid / avg) -- a strong, accurate public constraint we must not waste.
            if value and value > 0:
                avg_cells["total"] = float(value)
            if total_count is not None:
                implied = float(value) * float(total_count)
                # 件数 + 每件均格都是公开精确值 → 总格 = 件数×均格 是确定值，硬锁(而非软目标)。
                # 仅当乘积接近整数(|frac|<=0.25)才锁 —— 防均格是四舍五入近似时锁错(此时退回软目标)。
                # 用户(原作者)定: 给了总件数和均格就该锁死总格。
                if abs(implied - round(implied)) <= 0.25:
                    set_total_grid_target(implied, "public_total_cells")
                else:
                    set_total_grid_target(implied, "public_total_avg_cells_target")
            else:
                source_notes.append("public_total_avg_cells_count_constraint")
        elif semantic == "total_avg_value":
            source_notes.append("public_total_avg_value_diagnostic_only")
        elif semantic in PUBLIC_AVG_CELLS:
            avg_cells[PUBLIC_AVG_CELLS[semantic]] = value
            source_notes.append(f"public_{PUBLIC_AVG_CELLS[semantic]}_avg_cells")
        elif semantic in PUBLIC_COUNTS:
            key = PUBLIC_COUNTS[semantic]
            fixed_counts[key] = int(round(value))
            min_counts[key] = int(round(value))
            source_notes.append(f"public_{key}_count")

    for key, value in _public_quality_avg_values(public_info).items():
        normalized = _ref_normalize_avg_value_wire(value)
        if normalized is None:
            continue
        avg_values[key] = float(normalized)
        source_notes.append(f"public_{key}_avg_value")
        if abs(float(normalized) - float(value)) > 1e-9:
            source_notes.append(f"public_{key}_avg_value_wire_normalized")

    _apply_exact_catalog_value_locks(
        fixed_counts=fixed_counts,
        min_counts=min_counts,
        avg_values=avg_values,
        quality_values=quality_values,
        source_notes=source_notes,
    )

    if phase != "settled":
        random_value_floors = _public_random_value_floors(public_info)
        for sample_count, value_floor in random_value_floors:
            source_notes.append(
                f"public_random_avg_value_floor_{sample_count}:{int(round(value_floor))}"
            )
        # Red anchor. A high random per-item avg is caused, with near-certainty, by a red item in
        # the draw that carries ~97-99% of the draw value (Monte-Carlo, 956k draws). So we bind the
        # implied value to RED rather than flat-clamping the total: anchor >=1 red whose value
        # floor is ~the draw total (N*avg). The count prior then adds the warehouse's other reds
        # (3-4 typical) on top, so the estimate floats above the floor with spread instead of
        # hugging it. value_floor = N*avg, avg = value_floor / sample_count.
        max_random_avg = max(
            (value_floor / sample_count for sample_count, value_floor in random_value_floors if sample_count > 0),
            default=0.0,
        )
        # tightest implied draw total = the largest N*avg seen (this is the jackpot red's value).
        red_value_floor = max((value_floor for _sc, value_floor in random_value_floors), default=0.0)
        if (
            max_random_avg >= RANDOM_AVG_RED_ANCHOR_THRESHOLD
            and int(fixed_counts.get("q6", 1) or 0) >= 1
        ):
            if int(min_counts.get("q6", 0) or 0) < 1:
                min_counts["q6"] = 1
            # 0.95x of the draw total. The drawn red(s) carry ~0.97-0.99 of the draw value
            # (Monte-Carlo), so 0.95x is a safe lower bound on the drawn red value; the warehouse's
            # OTHER reds/items (3-4 reds total vs ~1 drawn) are computed on top, so the total floats
            # above the hard floor V (=N*avg <= truth) with spread instead of hugging it.
            # round() keeps it a whole value -- never surface a fractional figure to the UI.
            anchored_red_value = float(round(0.95 * red_value_floor))
            if anchored_red_value > quality_value_floors.get("q6", 0.0):
                quality_value_floors["q6"] = anchored_red_value
                quality_value_floor_item_counts["q6"] = max(
                    1, int(quality_value_floor_item_counts.get("q6", 0) or 0)
                )
                source_notes.append(
                    f"random_avg_red_anchor_q6_value_floor:{int(round(anchored_red_value))}"
                )

    if phase == "settled":
        _apply_settlement_quality_truth(
            snapshot,
            fixed_counts=fixed_counts,
            min_counts=min_counts,
            avg_cells=avg_cells,
            quality_cells=quality_cells,
            source_notes=source_notes,
        )

    _apply_avg_value_cells_exact_count_intersection(
        total_count=total_count,
        fixed_counts=fixed_counts,
        min_counts=min_counts,
        avg_cells=avg_cells,
        avg_values=avg_values,
        quality_cells=quality_cells,
        quality_values=quality_values,
        split_counts=split_counts,
        source_notes=source_notes,
    )
    _apply_avg_value_only_q5_count_derivation(
        total_count=total_count,
        fixed_counts=fixed_counts,
        min_counts=min_counts,
        count_sums=count_sums,
        avg_values=avg_values,
        avg_cells=avg_cells,
        quality_cells=quality_cells,
        source_notes=source_notes,
    )
    _apply_exact_count_residuals(
        total_count=total_count,
        fixed_counts=fixed_counts,
        min_counts=min_counts,
        count_sums=count_sums,
        avg_values=avg_values,
        quality_values=quality_values,
        quality_cells=quality_cells,
        split_counts=split_counts,
        source_notes=source_notes,
    )
    _apply_quality_cells_total_grid_residual(
        total_grid_target=total_grid_target,
        fixed_counts=fixed_counts,
        avg_cells=avg_cells,
        quality_cells=quality_cells,
        source_notes=source_notes,
    )

    _apply_low_quality_split_evidence(
        total_count=total_count,
        split_counts=split_counts,
        split_quality_cells=split_quality_cells,
        split_avg_cells=split_avg_cells,
        fixed_counts=fixed_counts,
        min_counts=min_counts,
        avg_cells=avg_cells,
        quality_cells=quality_cells,
        source_notes=source_notes,
    )

    _apply_public_info_exact_numeric_rows(
        snapshot,
        fixed_counts=fixed_counts,
        min_counts=min_counts,
        quality_cells=quality_cells,
        set_total_count=set_total_count,
        set_total_grid_target=set_total_grid_target,
        source_notes=source_notes,
    )

    for key, avg in tuple(avg_cells.items()):
        if avg == 0 and key not in fixed_counts:
            fixed_counts[key] = 0
            min_counts[key] = 0
            source_notes.append(f"zero_avg_cells_{key}_count_zero")

    for key, avg in tuple(avg_values.items()):
        avg_fraction = _avg_value_fraction(avg)
        if avg_fraction == 0 and key not in fixed_counts:
            fixed_counts[key] = 0
            min_counts[key] = 0
            source_notes.append(f"zero_avg_value_{key}_count_zero")

    for key, cells in tuple(quality_cells.items()):
        if key in fixed_counts:
            continue
        parsed_cells = _safe_float(cells)
        if parsed_cells is not None and abs(parsed_cells) <= 0.0001:
            fixed_counts[key] = 0
            min_counts[key] = 0
            source_notes.append(f"zero_quality_cells_{key}_count_zero")

    for key, count in fixed_counts.items():
        if int(count) == 0 and key not in quality_cells:
            quality_cells[key] = 0.0

    for key, value in tuple(quality_values.items()):
        if key in avg_values:
            continue
        count = fixed_counts.get(key)
        parsed_value = _safe_float(value)
        if count is None or parsed_value is None or parsed_value < 0:
            continue
        count_int = int(count)
        if count_int > 0:
            avg_values[key] = float(parsed_value) / float(count_int)
            source_notes.append(f"quality_value_{key}_avg_value_derived")
        elif abs(parsed_value) <= 0.0001:
            avg_values[key] = 0.0
            source_notes.append(f"quality_value_{key}_avg_value_derived")

    for key, avg in tuple(avg_values.items()):
        quality_value = quality_values.get(key)
        fixed_count = fixed_counts.get(key)
        if fixed_count is not None:
            if not _quality_count_matches_value_inputs(
                int(fixed_count),
                avg,
                _safe_float(quality_value),
            ):
                source_notes.append(f"quality_value_{key}_avg_count_conflict")
            continue
        if quality_value is None:
            continue
        derived_count = _avg_value_count_from_total(avg, quality_value)
        derived_via_tolerance = False
        if derived_count is None:
            derived_count = _avg_value_count_from_total_with_tolerance(
                avg,
                quality_value,
                total_count=total_count,
            )
            derived_via_tolerance = derived_count is not None
        if derived_count is None:
            source_notes.append(f"quality_value_{key}_avg_value_conflict")
            continue
        fixed_counts[key] = derived_count
        min_counts[key] = derived_count
        if derived_via_tolerance:
            source_notes.append(f"quality_value_{key}_count_derived_product_tolerance")
        else:
            source_notes.append(f"quality_value_{key}_count_derived")

    for key, cells in quality_cells.items():
        if key in avg_cells:
            continue
        count = fixed_counts.get(key)
        if count is not None and count > 0 and cells >= 0:
            avg_cells[key] = float(cells) / float(count)
            source_notes.append(f"quality_cells_{key}_avg_derived")

    for key, avg in tuple(avg_cells.items()):
        if key in fixed_counts or avg is None:
            continue
        cells = quality_cells.get(key)
        derived_count = _avg_count_from_cells(avg, cells)
        if derived_count is None:
            continue
        fixed_counts[key] = derived_count
        min_counts[key] = derived_count
        source_notes.append(f"quality_cells_{key}_count_derived")

    for key, avg in tuple(avg_cells.items()):
        cells = quality_cells.get(key)
        if cells is None:
            continue
        cells_int = int(round(float(cells)))
        fixed_count = fixed_counts.get(key)
        if fixed_count is not None:
            if not _avg_matches_exact_grid(int(fixed_count), avg, cells_int):
                source_notes.append(f"quality_cells_{key}_avg_count_conflict")
        elif _avg_count_from_cells(avg, cells) is None:
            source_notes.append(f"quality_cells_{key}_avg_cells_conflict")

    _apply_public_max_quality_ceiling(
        _extract_public_max_quality(snapshot),
        fixed_counts=fixed_counts,
        min_counts=min_counts,
        source_notes=source_notes,
    )

    # Scope the high-tier-cell grid target to Aisha. The estimate ignores
    # public-reveal cell floors (e.g. a single q6 with 15 cells), so for other
    # heroes it can land below the minimum reachable grid and break red-reveal
    # combos (no_reachable_combo / count_prior fallback). Generalizing it with
    # floor-awareness is a v0.2 follow-up.
    if normalize_hero_key(hero) == "aisha" and not snapshot.get("_skip_high_tier_grid_target"):
        total_grid_target = _apply_total_grid_target_from_known_high_tier_cells(
            total_count=total_count,
            total_grid_target=total_grid_target,
            fixed_counts=fixed_counts,
            quality_cells=quality_cells,
            avg_cells=avg_cells,
            source_notes=source_notes,
        )

    round_no = _safe_int(context.get("round") or snapshot.get("round"))
    total_grid_target = _apply_layout_depth_hints_from_snapshot(
        snapshot=snapshot,
        hero=hero,
        round_no=round_no,
        total_grid_target=total_grid_target,
        source_notes=source_notes,
        total_count=total_count,
    )

    soft_avg_cell_keys = _derive_soft_public_avg_cell_keys(
        source_notes=source_notes,
        fixed_counts=fixed_counts,
        quality_cells=quality_cells,
    )
    for key in sorted(soft_avg_cell_keys):
        _append_source_note_once(source_notes, f"public_{key}_avg_cells_soft_pending_count")

    soft_avg_value_keys = _derive_soft_public_avg_value_keys(
        source_notes=source_notes,
        fixed_counts=fixed_counts,
        quality_values=quality_values,
        avg_values=avg_values,
    )
    _apply_public_avg_value_integer_product_min_counts(
        soft_avg_value_keys=soft_avg_value_keys,
        avg_values=avg_values,
        min_counts=min_counts,
        source_notes=source_notes,
    )
    for key in sorted(soft_avg_value_keys):
        _append_source_note_once(source_notes, f"public_{key}_avg_value_soft_pending_count")

    total_grid_target = normalize_grid_cell_target(total_grid_target)

    return RefEvidence(
        hero=hero,
        map_id=map_id,
        phase=phase,
        total_count=total_count,
        fixed_counts=fixed_counts,
        min_counts=min_counts,
        count_sums=count_sums,
        avg_cells=avg_cells,
        quality_cells=quality_cells,
        quality_cell_floors=quality_cell_floors,
        avg_values=avg_values,
        quality_values=quality_values,
        quality_value_floors=quality_value_floors,
        quality_value_floor_item_counts=quality_value_floor_item_counts,
        split_counts=split_counts,
        split_quality_cells=split_quality_cells,
        split_avg_cells=split_avg_cells,
        random_value_floors=random_value_floors,
        total_grid_target=total_grid_target,
        soft_avg_cell_keys=soft_avg_cell_keys,
        soft_avg_value_keys=soft_avg_value_keys,
        v3_conservative=str(decision.get("defend_bid") or ""),
        v3_balanced=str(decision.get("attack_bid") or ""),
        v3_aggressive=str(decision.get("stop_price") or ""),
        source_notes=tuple(dict.fromkeys(source_notes)),
    )


def can_compose_grid_total(count: int, grid: int) -> bool:
    if count == 0:
        return grid == 0
    if count < 0 or grid < count or grid > 18 * count:
        return False
    valid_sizes = (1, 2, 3, 4, 5, 6, 8, 9, 10, 12, 15, 16, 18)
    reachable = {0}
    for _ in range(count):
        reachable = {
            current + size
            for current in reachable
            for size in valid_sizes
            if current + size <= grid
        }
        if not reachable:
            return False
    return grid in reachable


@lru_cache(maxsize=4096)
def _composable_grid_options(count: int) -> tuple[int, ...]:
    if count < 0:
        return ()
    if count == 0:
        return (0,)
    # 一次子集和 DP 求出「恰好 count 件」可组成的全部总格，而非对 range(count, 18*count+1)
    # 里每个候选格各跑一遍完整 DP（原写法 O(count^3) → 高总数时单次可达数十秒，是 live 低信息
    # 长推理的真正热点）。reachable 集合对所有候选格相同，算一次即可。结果与逐格调用
    # can_compose_grid_total 完全一致（已对 count 0..50 逐一核验），纯提速、零语义改动。
    valid_sizes = (1, 2, 3, 4, 5, 6, 8, 9, 10, 12, 15, 16, 18)
    cap = 18 * count
    reachable = {0}
    for _ in range(count):
        reachable = {
            current + size
            for current in reachable
            for size in valid_sizes
            if current + size <= cap
        }
    return tuple(sorted(grid for grid in reachable if grid >= count))


AISHA_HIDDEN_HT_GRID_SPREAD_DISABLE = "AISHA_DISABLE_HIDDEN_HIGHTIER_GRID_SPREAD"  # default ON
_AISHA_GEOM_CENTER_PULL = 0.8  # how far to float the grid center toward the full-row geometric value
# Unpinned gold/red footprints: instead of one mean-sized estimate, spread over a per-item SIZE range
# (small..big) with a right-skewed plausibility prior. The mean is most likely but big gold/red are
# real -- without this the grid band collapses (N hidden items * a constant mean = constant cells
# regardless of the count split; 2501 R4: 9 hidden items truly hold 61 cells = 6.8/item vs ~2.7).
# Size multipliers RELATIVE to the tier's own DEFAULT_GRID_MEANS, so the mid (×1.0) == the baseline
# mean estimate and the p50 (reference) does NOT shift -- only the thin tails widen p10/p90 for the
# small/big possibility. Symmetric-ish weight around ×1.0 keeps the median at the mean.
_HIDDEN_HT_CELL_SIZE_PRIOR = ((0.60, 0.42), (1.0, 1.00), (1.45, 0.42))


def _cell_unknown_hightier_keys(evidence: "RefEvidence") -> list[str]:
    """High-tier tiers (gold/red) whose footprint is genuinely unknown (no exact cells, no avg) --
    pure size-prior. These are the ones a single mean under-estimates when the item is big."""
    out: list[str] = []
    for key in ("q5", "q6"):
        if _quality_exact_cells(key, evidence) is not None:
            continue
        if evidence.avg_cells.get(key) is not None:
            continue
        out.append(key)
    return out


def _hidden_hightier_cell_options(
    key: str, count: int, evidence: "RefEvidence", center_cells: float | None = None
) -> list[tuple[float, float]]:
    """Representative (cells, prior_weight) for an unpinned high-tier tier's footprint: a symmetric
    SIZE range (small..big) around `center_cells` (default the count*mean), composable for `count`
    items and >= the cell floor. Centering on the combo's OWN fitted grid keeps the p50 == the combo
    total (no band shift) while still floating small/big; only the geometric center-shift raises the
    genuine under-floor cases. Dedup keeps the max weight for a shared composable cell total."""
    if count <= 0:
        return [(0.0, 1.0)]
    floor = _quality_cell_floor(key, evidence)
    composable = [option for option in _composable_grid_options(int(count)) if option >= floor]
    if not composable:
        return [(float(count), 1.0)]
    center = center_cells if center_cells and center_cells > 0 else count * DEFAULT_GRID_MEANS.get(key, 2.4)
    by_cells: dict[int, float] = {}
    for mult, weight in _HIDDEN_HT_CELL_SIZE_PRIOR:
        nearest = min(composable, key=lambda option: (abs(option - center * mult), option))
        by_cells[nearest] = max(by_cells.get(nearest, 0.0), weight)
    return [(float(cells), weight) for cells, weight in by_cells.items()]


def _ref_format_game_avg(cells: int, count: int, *, max_decimals: int = 2) -> str:
    if count <= 0 or cells < 0:
        return ""
    decimals = max(2, max_decimals)
    scale = 10**decimals
    floored_scaled = (int(cells) * scale) // int(count)
    int_part, frac_value = divmod(floored_scaled, scale)
    digits = str(frac_value).zfill(decimals)
    if int(cells) * scale == floored_scaled * int(count):
        digits = digits.rstrip("0")
    return f"{int_part}.{digits}" if digits else str(int_part)


def _ref_parse_display_avg(text: str) -> float | None:
    value = str(text or "").strip().replace(",", "")
    if not value or "e" in value.lower():
        return None
    if "." in value:
        int_part, frac_part = value.split(".", 1)
        if not int_part.isdigit() or not frac_part.isdigit():
            return None
    elif not value.isdigit():
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _ref_avg_display_decimals(display_text: str) -> int:
    value = str(display_text or "").strip()
    if "." not in value:
        return 0
    return len(value.split(".", 1)[1])


def _ref_avg_display_product_tolerance(display_text: str, count: int) -> float:
    decimals = _ref_avg_display_decimals(display_text)
    if decimals < 2:
        return 0.0001
    return max(0.0001, (0.5 * (10**-decimals) * max(1, int(count))) + 1e-9)


def _ref_avg_looks_like_display_reading(avg: float) -> bool:
    if not math.isfinite(avg):
        return False
    for decimals in (3, 2, 1, 0):
        rounded = round(avg, decimals)
        if abs(avg - rounded) <= 1e-9:
            return True
    return False


def _ref_normalize_avg_value_wire(avg: float | None) -> float | None:
    """Snap protocol float averages to UI decimals before fraction/count parsing.

    Client avg prices often arrive as binary floats (total/count). Feeding those
    directly into Fraction.limit_denominator(60) can pick a wrong rational on the
    full magnitude (e.g. 6659.21435546875 -> 93229/14).
    """
    if avg is None:
        return None
    value = float(avg)
    if not math.isfinite(value) or value < 0:
        return None
    if _ref_avg_looks_like_display_reading(value):
        for decimals in (3, 2, 1, 0):
            rounded = round(value, decimals)
            if abs(value - rounded) <= 1e-9:
                return rounded
    wire = Fraction(value).limit_denominator(65536)
    if abs(float(wire) - value) <= 1e-9:
        wired = float(wire)
        for decimals in (3, 2, 1, 0):
            rounded = round(wired, decimals)
            if abs(wired - rounded) <= (0.5 * 10**-decimals) + 1e-9:
                return rounded
    return value


def _ref_avg_value_decimal_text(avg: float) -> str:
    return format(float(avg), ".10f").rstrip("0").rstrip(".")


def _ref_avg_value_product_tolerance(count: int, avg: float) -> float:
    text = _ref_avg_value_decimal_text(avg)
    decimals = len(text.split(".", 1)[1]) if "." in text else 0
    return max(0.0001, (0.5 * 10**-decimals) * max(1, int(count)) + 1e-6)


def _ref_public_avg_value_wire_normalized(evidence: RefEvidence, key: str) -> bool:
    return f"public_{key}_avg_value_wire_normalized" in evidence.source_notes


def _filter_quality_counts_for_avg_value(
    key: str,
    candidates: Iterable[int],
    evidence: RefEvidence,
) -> list[int]:
    ordered = sorted({int(value) for value in candidates})
    avg = evidence.avg_values.get(key)
    if key in evidence.soft_avg_value_keys and _avg_value_has_positive_signal(avg):
        # A precise *fractional* tier-mean price pins the count: total value = avg*count must be an
        # integer, so count is a multiple of the reduced denominator (93229/14 -> multiple of 14;
        # 44925/8 -> multiple of 8). Apply this even when the mean isn't a single catalog item value.
        # Gate on denominator>1: an integer-valued avg (e.g. 31560) constrains nothing, so it must NOT
        # be used to spuriously collapse the count (esp. not to 1 -- see does_not_lock_one).
        normalized = _ref_normalize_avg_value_wire(avg)
        avg_fraction = _avg_value_fraction(normalized) if normalized is not None else None
        if avg_fraction is not None and avg_fraction.denominator > 1:
            filtered = [value for value in ordered if _avg_value_count_matches(value, avg)]
            if filtered:
                return filtered
        if not _avg_is_exact_catalog_item_value(key, avg):
            return [value for value in ordered if value != 1]
        filtered = [value for value in ordered if _avg_value_count_matches(value, avg)]
        if filtered:
            return filtered
        return []
    if (
        ordered
        and avg is not None
        and _ref_public_avg_value_wire_normalized(evidence, key)
    ):
        return ordered
    return [
        value
        for value in ordered
        if _quality_count_matches_value_evidence(key, value, evidence)
    ]


def _ref_avg_matches_game_display(count: int, avg: float | None, grid: int) -> bool:
    if avg is None:
        return True
    if count <= 0:
        return grid == 0 and abs(float(avg)) <= 0.0001
    if abs(float(avg) * count - grid) <= 0.0001:
        return True
    if not _ref_avg_looks_like_display_reading(float(avg)):
        return False
    display_text = _ref_format_game_avg(grid, count)
    display_value = _ref_parse_display_avg(display_text)
    if display_value is None:
        return False
    product_tolerance = _ref_avg_display_product_tolerance(display_text, count)
    if abs(float(avg) * count - grid) <= product_tolerance:
        return True
    return abs(display_value - float(avg)) <= 1e-9


def _avg_grid_options(count: int, avg: float | None) -> list[int]:
    if count < 0:
        return []
    if count == 0:
        return [0] if avg in (None, 0) else []
    low = count
    high = 18 * count
    if avg is None:
        return list(range(low, high + 1))
    target = avg * count
    tolerance = 0.0001
    candidates = {
        int(math.floor(target)),
        int(round(target)),
        int(math.ceil(target)),
    }
    options = [
        grid
        for grid in sorted(candidates)
        if low <= grid <= high
        and abs(grid - target) <= tolerance
        and can_compose_grid_total(count, grid)
    ]
    if options:
        return options
    exact_options = [
        grid
        for grid in range(low, high + 1)
        if abs(grid - target) <= tolerance
        and can_compose_grid_total(count, grid)
    ]
    if exact_options:
        return exact_options
    return [
        grid
        for grid in range(low, high + 1)
        if _ref_avg_matches_game_display(count, avg, grid)
        and can_compose_grid_total(count, grid)
    ]


def _avg_count_from_cells(avg: float | None, cells: Any) -> int | None:
    if avg is None or cells in (None, ""):
        return None
    try:
        cells_value = float(str(cells).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    if cells_value < 0:
        return None
    cells_int = int(round(cells_value))
    if abs(cells_value - cells_int) > 0.0001:
        return None
    if avg == 0:
        return 0 if cells_int == 0 else None
    count = int(round(cells_int / avg))
    if count > 0 and abs(avg * count - cells_int) <= 0.0001:
        if can_compose_grid_total(count, cells_int):
            return count
    display_candidates = [
        candidate
        for candidate in range(1, cells_int + 1)
        if _ref_avg_matches_game_display(candidate, avg, cells_int)
        and can_compose_grid_total(candidate, cells_int)
    ]
    if len(display_candidates) == 1:
        return display_candidates[0]
    return None


def _avg_matches_exact_grid(count: int, avg: float | None, grid: int) -> bool:
    return _ref_avg_matches_game_display(count, avg, grid)


def _avg_value_fraction(avg: float | None) -> Fraction | None:
    if avg is None:
        return None
    normalized = _ref_normalize_avg_value_wire(avg)
    if normalized is None:
        return None
    value = float(normalized)
    if not math.isfinite(value) or value < 0:
        return None
    if value == 0:
        return Fraction(0, 1)
    text = _ref_avg_value_decimal_text(value)
    if "." not in text:
        return Fraction(int(text), 1)
    whole, frac = text.split(".", 1)
    digits = len(frac)
    exact = Fraction(int(whole) * 10**digits + int(frac), 10**digits)
    return exact.limit_denominator(10_000)


def _avg_value_has_positive_signal(avg: float | None) -> bool:
    fraction = _avg_value_fraction(avg)
    return fraction is not None and fraction > 0


def _avg_value_count_matches(count: int, avg: float | None) -> bool:
    normalized = _ref_normalize_avg_value_wire(avg)
    if normalized is None:
        return True
    if normalized == 0:
        return count == 0
    if count <= 0:
        return False
    fraction = _avg_value_fraction(normalized)
    if fraction is not None and fraction > 0:
        if (fraction.numerator * int(count)) % fraction.denominator == 0:
            return True
    product = float(normalized) * int(count)
    tolerance = _ref_avg_value_product_tolerance(int(count), float(normalized))
    return abs(product - round(product)) <= tolerance


def _avg_value_total_from_count(avg: float, count: int) -> float | None:
    fraction = _avg_value_fraction(avg)
    if fraction is None:
        return None
    return float(fraction * int(count))


def _avg_value_count_from_total(avg: float | None, total_value: Any) -> int | None:
    fraction = _avg_value_fraction(avg)
    value = _safe_float(total_value)
    if fraction is None or value is None or value < 0:
        return None
    total_fraction = Fraction(float(value)).limit_denominator(100)
    if fraction == 0:
        return 0 if total_fraction == 0 else None
    if total_fraction <= 0:
        return None
    count_fraction = total_fraction / fraction
    if count_fraction.denominator != 1 or count_fraction < 0:
        return None
    return int(count_fraction)


def _quality_count_product_tolerance_match(
    count: int,
    avg: float | None,
    exact_value: float | None,
    *,
    tolerance: float = 0.5,
) -> bool:
    if avg is None or exact_value is None or count <= 0:
        return False
    derived_total = _avg_value_total_from_count(float(avg), count)
    if derived_total is None:
        derived_total = float(avg) * int(count)
    return abs(float(exact_value) - derived_total) <= tolerance


def _avg_value_count_from_total_with_tolerance(
    avg: float | None,
    total_value: Any,
    *,
    total_count: int | None = None,
) -> int | None:
    """Recover integer count when wire-normalized avg breaks strict rational derive."""
    value = _safe_float(total_value)
    if avg is None or value is None or value <= 1e-9 or abs(float(avg)) <= 1e-9:
        return None
    approximate = max(1, int(round(float(value) / float(avg))))
    upper = int(total_count) if total_count and total_count > 0 else approximate + 3
    upper = min(max(upper, approximate), 200)
    lower = max(1, approximate - 3)
    candidates = [
        count
        for count in range(lower, upper + 1)
        if _quality_count_product_tolerance_match(count, avg, value)
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _choose_avg_grid_option(
    count: int,
    avg: float | None,
    options: list[int],
) -> float | None:
    if not options:
        return None
    if count <= 0:
        return 0.0
    if avg is None:
        return float(options[len(options) // 2])
    target = avg * count
    return float(min(options, key=lambda grid: (abs(grid - target), grid)))


def _log_fact(n: int) -> float:
    return math.lgamma(n + 1)


def _weighted_quantile(rows: list[tuple[float, float]], q: float) -> float | None:
    if not rows:
        return None
    rows = sorted(rows)
    total = sum(weight for _, weight in rows)
    if total <= 0:
        return rows[len(rows) // 2][0]
    threshold = total * q
    cumulative = 0.0
    for value, weight in rows:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return rows[-1][0]


def _count_values(
    total_count: int,
    key: str,
    evidence: RefEvidence,
    reserve: int,
) -> list[int]:
    fixed = evidence.fixed_counts.get(key)
    if fixed is not None:
        fixed_int = int(fixed)
        if not _quality_count_matches_value_evidence(key, fixed_int, evidence):
            return []
        return [fixed_int]
    minimum = _effective_min_count(key, evidence)
    maximum = total_count - reserve
    if maximum < minimum:
        return []
    values = _filter_quality_counts_for_avg_value(
        key,
        range(minimum, maximum + 1),
        evidence,
    )
    avg = evidence.avg_cells.get(key)
    if avg is not None and avg > 0 and fixed is None:
        fast_path = (
            _should_use_exact_total_avg_cells_fast_path(evidence)
            and key not in evidence.soft_avg_cell_keys
        )
        # Wire-precision public avg_cells (e.g. 10/3 = 3.3333332, not a 2-decimal display round)
        # implies the tier's total cells = avg*count is an exact integer, so the count must be able
        # to compose an integer grid at that avg. Prune impossible counts even while the tier count
        # is still "soft pending" (live R1: q4 avg 10/3 -> count must be a multiple of 3; 4/7/10 are
        # mathematically out). Gated on "not a rounded display" so a true 2-decimal avg never over-prunes.
        wire_soft = (
            evidence.phase != "settled"
            and key in evidence.soft_avg_cell_keys
            and not _ref_avg_looks_like_display_reading(float(avg))
        )
        if fast_path or wire_soft:
            avg_valid = [value for value in values if _avg_grid_options(value, avg)]
            if avg_valid:
                return avg_valid
    return values


def _effective_min_count(key: str, evidence: RefEvidence) -> int:
    minimum = max(0, int(evidence.min_counts.get(key, 0)))
    if key == "q1":
        minimum = max(minimum, _split_low_quality_q1_count_floor(evidence))
    exact_cells = _quality_exact_cells(key, evidence)
    if exact_cells is not None and exact_cells > 0:
        minimum = max(minimum, int(math.ceil(exact_cells / 18.0)), 1)
    cell_floor = _quality_cell_floor(key, evidence)
    if cell_floor > 0:
        minimum = max(minimum, int(math.ceil(cell_floor / 18.0)), 1)
    exact_value = _quality_exact_value(key, evidence)
    if exact_value is not None and exact_value > 0:
        minimum = max(minimum, 1)
    if _quality_value_floor(key, evidence) > 0:
        minimum = max(minimum, 1)
    if (
        evidence.phase != "settled"
        and evidence.fixed_counts.get(key) is None
        and evidence.avg_cells.get(key) is not None
        and (evidence.avg_cells.get(key) or 0.0) > 0
    ):
        minimum = max(minimum, 1)
    if (
        evidence.phase != "settled"
        and evidence.fixed_counts.get(key) is None
        and evidence.avg_values.get(key) is not None
        and _avg_value_has_positive_signal(evidence.avg_values.get(key))
    ):
        minimum = max(minimum, 1)
    return minimum


def _quality_exact_cells(key: str, evidence: RefEvidence) -> int | None:
    value = evidence.quality_cells.get(key)
    if value is None:
        return None
    parsed = int(round(float(value)))
    return max(0, parsed)


def _quality_cell_floor(key: str, evidence: RefEvidence) -> int:
    value = evidence.quality_cell_floors.get(key)
    if value is None:
        return 0
    parsed = _safe_float(value)
    if parsed is None or parsed <= 0:
        return 0
    return max(0, int(math.ceil(parsed - 1e-6)))


def _quality_exact_value(key: str, evidence: RefEvidence) -> float | None:
    value = evidence.quality_values.get(key)
    if value is None:
        return None
    parsed = _safe_float(value)
    if parsed is None or parsed < 0:
        return None
    return parsed


def _quality_value_floor(key: str, evidence: RefEvidence) -> float:
    value = evidence.quality_value_floors.get(key)
    parsed = _safe_float(value)
    if parsed is None or parsed <= 0:
        return 0.0
    return float(parsed)


def _partial_known_quality_value_state(
    key: str,
    *,
    count: int,
    grid: float,
    item_values: dict[str, float],
    evidence: RefEvidence,
) -> tuple[float, int, float, float] | None:
    """Decompose partially revealed quality value into known sum + unknown parts.

    Unknown reds use per-item default value as the hard floor and remaining grid
    cells (total grid minus known cells) for the grid-conditioned center estimate.
    """
    known_sum = _quality_value_floor(key, evidence)
    if known_sum <= 0 or count <= 0:
        return None
    known_count = evidence.quality_value_floor_item_counts.get(key, 0)
    if known_count <= 0 or count <= known_count:
        return None
    known_cells = float(evidence.quality_cell_floors.get(key, 0.0))
    remaining_count = count - known_count
    remaining_grid = max(float(remaining_count), grid - known_cells)
    unknown_default = remaining_count * item_values[key]
    unknown_grid_value = _quality_value_for_grid(
        key,
        count=remaining_count,
        grid=remaining_grid,
        item_values=item_values,
    )
    return known_sum, remaining_count, unknown_default, unknown_grid_value


def _quality_value_floor_for_count(
    key: str,
    *,
    count: int,
    grid: float,
    item_values: dict[str, float],
    evidence: RefEvidence,
) -> float:
    partial = _partial_known_quality_value_state(
        key,
        count=count,
        grid=grid,
        item_values=item_values,
        evidence=evidence,
    )
    if partial is not None:
        known_sum, _remaining_count, unknown_default, _unknown_grid_value = partial
        return known_sum + unknown_default
    known_sum = _quality_value_floor(key, evidence)
    if count <= 0:
        return max(0.0, known_sum)
    return known_sum


def _quality_count_matches_value_inputs(
    count: int,
    avg: float | None,
    exact_value: float | None,
) -> bool:
    if not _avg_value_count_matches(count, avg):
        return False
    if exact_value is None:
        return True
    if count <= 0:
        return abs(float(exact_value)) <= 0.0001
    if avg is None:
        return exact_value > 0
    derived_total = _avg_value_total_from_count(float(avg), count)
    if derived_total is None:
        return False
    return abs(float(exact_value) - derived_total) <= 0.5


def _quality_count_matches_value_evidence(
    key: str,
    count: int,
    evidence: RefEvidence,
) -> bool:
    exact_value = _quality_exact_value(key, evidence)
    if (
        key in evidence.soft_avg_value_keys
        and evidence.fixed_counts.get(key) is None
        and exact_value is None
    ):
        avg = evidence.avg_values.get(key)
        if not _avg_is_exact_catalog_item_value(key, avg):
            return int(count) != 1
        return _quality_count_matches_value_inputs(
            count,
            avg,
            None,
        )
    return _quality_count_matches_value_inputs(
        count,
        evidence.avg_values.get(key),
        exact_value,
    )


def _hard_total_grid_target_int(evidence: RefEvidence) -> int | None:
    return _hard_total_grid_target_from_notes(
        evidence.total_grid_target,
        evidence.source_notes,
    )


def _soft_total_grid_floor_int(evidence: RefEvidence) -> int | None:
    """Soft lower bound on total grid cells from a `total_grid_floor:<N>` note (unpinned-tail
    experiment). Unlike the hard target, grids only have to sum to AT LEAST this — the range is
    free to expand above it via minimap/footroom."""
    for note in evidence.source_notes:
        if note.startswith(TOTAL_GRID_FLOOR_NOTE_PREFIX):
            try:
                return int(note[len(TOTAL_GRID_FLOOR_NOTE_PREFIX):])
            except ValueError:
                return None
    return None


def _split_low_quality_q1_floor_parts_from_maps(
    split_counts: dict[str, int],
    split_quality_cells: dict[str, float],
) -> tuple[int, int, int]:
    count_floor = 0
    exact_count_for_cells = 0
    exact_cells = 0
    for split_key in LOW_SPLIT_KEYS:
        raw_count = split_counts.get(split_key)
        raw_cells = split_quality_cells.get(split_key)
        count = max(0, int(raw_count)) if raw_count is not None else None
        cells = None
        if raw_cells is not None:
            cells = max(0, int(round(float(raw_cells))))

        if count is not None:
            count_floor += count
        elif cells is not None and cells > 0:
            count_floor += max(1, int(math.ceil(cells / 18.0)))

        if cells is None:
            continue
        if count is None:
            count_for_cells = 0 if cells == 0 else max(1, int(math.ceil(cells / 18.0)))
        else:
            count_for_cells = count
        exact_count_for_cells += count_for_cells
        exact_cells += cells
    return count_floor, exact_count_for_cells, exact_cells


def _split_low_quality_q1_grid_extra_from_maps(
    split_counts: dict[str, int],
    split_quality_cells: dict[str, float],
) -> int:
    _count_floor, exact_count_for_cells, exact_cells = _split_low_quality_q1_floor_parts_from_maps(
        split_counts,
        split_quality_cells,
    )
    return max(0, exact_cells - exact_count_for_cells)


def _split_low_quality_q1_count_floor(evidence: RefEvidence) -> int:
    count_floor, _exact_count_for_cells, _exact_cells = _split_low_quality_q1_floor_parts_from_maps(
        evidence.split_counts,
        evidence.split_quality_cells,
    )
    return count_floor


def _split_low_quality_q1_grid_floor(count: int, evidence: RefEvidence) -> int:
    if count <= 0:
        return 0
    return int(count) + _split_low_quality_q1_grid_extra_from_maps(
        evidence.split_counts,
        evidence.split_quality_cells,
    )


def _split_low_quality_q1_grid_matches(
    count: int,
    grid: float,
    evidence: RefEvidence,
) -> bool:
    floor = _split_low_quality_q1_grid_floor(count, evidence)
    return float(grid) + 1e-6 >= float(floor)


def _default_total_count(map_id: int | None) -> int:
    if map_id is None:
        return 28
    family = int(map_id) // 100
    if family in {21, 22}:
        return 24
    if family == 23:
        return 27
    if family in {25, 45}:
        return 33
    if family == 24:
        return 28
    if family == 26:
        return 33
    return 28


def _known_count_floor(evidence: RefEvidence) -> int:
    fixed_total = sum(max(0, int(value)) for value in evidence.fixed_counts.values())
    min_total = sum(_effective_min_count(key, evidence) for key in QUALITY_KEYS)
    for group_key, keys in (
        ("q4q5", ("q4", "q5")),
        ("q4q5q6", ("q4", "q5", "q6")),
    ):
        group_total = evidence.count_sums.get(group_key)
        if group_total is None:
            continue
        fixed_group = sum(max(0, int(evidence.fixed_counts.get(key, 0))) for key in keys)
        min_total = max(min_total, max(0, int(group_total)) + fixed_total - fixed_group)
    return max(fixed_total, min_total)


def _positive_quality_cell_keys(evidence: RefEvidence) -> list[str]:
    return [
        key
        for key, value in evidence.quality_cells.items()
        if (_safe_float(value) or 0.0) > 0.0001
    ]


def _quality_cells_fully_pinned(evidence: RefEvidence) -> bool:
    """True when every positive quality_cells tier also has a matching fixed count."""
    keys = _positive_quality_cell_keys(evidence)
    if len(keys) < 2:
        return False
    for key in keys:
        fixed = evidence.fixed_counts.get(key)
        cells = _quality_exact_cells(key, evidence)
        if fixed is None or cells is None:
            return False
        if not can_compose_grid_total(int(fixed), int(cells)):
            return False
    return True


def _quality_cells_blocks_sparse_exact_prior(evidence: RefEvidence) -> bool:
    if not evidence.quality_cells:
        return False
    positive_keys = [
        key
        for key, value in evidence.quality_cells.items()
        if (_safe_float(value) or 0.0) > 0.0001
    ]
    # One tier total-cells hint (e.g. public 200011 gold cells) still leaves the
    # count split underdetermined; the prior sampler can honor it via
    # _quality_exact_cells. Multiple tier totals need the full search path.
    return len(positive_keys) >= 2


def _should_use_sparse_exact_total_prior(evidence: RefEvidence) -> bool:
    if evidence.total_count is None or evidence.phase == "settled":
        return False
    pinned_quality_cells = _quality_cells_fully_pinned(evidence)
    if evidence.count_sums:
        return False
    if _quality_cells_blocks_sparse_exact_prior(evidence) and not pinned_quality_cells:
        return False
    nonzero_fixed_count = sum(1 for value in evidence.fixed_counts.values() if int(value) > 0)
    if nonzero_fixed_count > 1:
        if not pinned_quality_cells:
            return False
        # The pinned-cells + many-fixed sparse-prior route is an Aisha hidden-sample
        # perf path (avoids high-total enumeration blowup). Other heroes keep exact
        # enumeration so pinned red-reveal states resolve to status "ok" rather than
        # falling back to count_prior.
        if normalize_hero_key(evidence.hero) != "aisha":
            return False
        if evidence.total_count is None or int(evidence.total_count) < 40:
            return False
    if nonzero_fixed_count == 1 and len(evidence.avg_cells) > 2 and not pinned_quality_cells:
        return False
    # Exact total count alone still leaves the count split underdetermined. Use the
    # probability prior sampler instead of full nested enumeration so live/manual
    # sparse states stay responsive without relying on max_combos truncation order.
    return True


def _sparse_exact_high_total_tight_prior(evidence: RefEvidence) -> bool:
    if evidence.total_count is None or int(evidence.total_count) < 50:
        return False
    if not _should_use_sparse_exact_total_prior(evidence):
        return False
    fixed_nonzero = sum(1 for value in evidence.fixed_counts.values() if int(value) > 0)
    return fixed_nonzero <= 2


EXACT_TOTAL_COUNT_SOURCE_NOTES = (
    "public_info_total_item_count",
    "structured_ref_bridge_total_count",
    "field_update_total_count",
    "ethan_skill_full_outline_count",
    "settlement_review_total_count",
)


def _has_explicit_total_count_source(source_notes: tuple[str, ...]) -> bool:
    for note in source_notes:
        if note in EXACT_TOTAL_COUNT_SOURCE_NOTES:
            return True
        if any(note.startswith(f"{prefix}:") for prefix in EXACT_TOTAL_COUNT_SOURCE_NOTES):
            return True
    return False


def _non_total_count_evidence_strength(evidence: RefEvidence) -> int:
    score = 0
    if evidence.split_counts:
        score += 2
    if evidence.count_sums:
        score += 2
    score += sum(1 for value in evidence.fixed_counts.values() if int(value) > 0)
    score += sum(1 for value in evidence.min_counts.values() if int(value) > 0)
    if evidence.avg_cells:
        score += 1
    if evidence.avg_values or evidence.quality_values:
        score += 1
    return score


def _should_use_exact_total_avg_cells_fast_path(evidence: RefEvidence) -> bool:
    """Exact total + avg_cells live states eligible for §50-2 micro-optimizations."""
    if evidence.total_count is None or evidence.phase in {"settled", "manual"}:
        return False
    if _quality_cells_blocks_sparse_exact_prior(evidence):
        return False
    if not evidence.avg_cells:
        return False
    return any((avg or 0) > 0 for avg in evidence.avg_cells.values())


def _nearest_composable_default_grid(count: int, default: float, *, cell_floor: int) -> float | None:
    candidate = int(round(default))
    if candidate < cell_floor:
        candidate = cell_floor
    if can_compose_grid_total(count, candidate):
        return float(candidate)
    return None


def _count_prior_center_from_grid_quality_residual(
    evidence: RefEvidence,
    *,
    source_notes: list[str],
    baseline_center: int,
) -> int | None:
    """Estimate count-prior center from total grid minus pinned tier cells (grid+quality path)."""
    grid_target = evidence.total_grid_target
    if grid_target is None or float(grid_target) <= 0:
        return None
    if _non_total_count_evidence_strength(evidence) < 2:
        return None

    known_cells_total = 0
    for key in QUALITY_KEYS:
        exact = _exact_integer_quality_cell(evidence.quality_cells.get(key))
        if exact is not None:
            known_cells_total += exact

    fixed_sum = sum(max(0, int(value)) for value in evidence.fixed_counts.values())
    per_item = _residual_per_item_cell_estimate(
        fixed_counts=evidence.fixed_counts,
        avg_cells=evidence.avg_cells,
        source_notes=source_notes,
    )
    if per_item <= 0:
        return None
    residual_grid = max(0.0, float(grid_target) - float(known_cells_total))
    residual_items = int(round(residual_grid / per_item))
    estimated = max(fixed_sum + residual_items, fixed_sum, 1)
    _append_source_note_once(source_notes, "total_count_prior_center_from_grid_quality_residual")
    return max(int(baseline_center), int(estimated), 1)


def _should_defer_total_count_prior(evidence: RefEvidence) -> bool:
    """Live quotes always run count_prior; grid-only defer removed after cap/perf work.

    Mapping total grid (and avg cells) to item-count prior still needs audit (v0.2).
    """
    del evidence
    return False


# Draw/lottery-hero count + value calibration constants and scale logic live in draw_hero_policy
# (imported at the top). The engine keeps only the orchestration below, which needs RefEvidence /
# normalize_hero_key / _default_total_count. _draw_hero_count_keys / _draw_hero_count_scale are the
# policy functions (env A/B overrides included).


def _draw_hero_count_center(
    center: int, evidence: RefEvidence, notes: list[str], round_no: int | None = None
) -> int:
    if evidence.total_count is not None:  # total locked -> prior unused, do not touch
        return center
    if normalize_hero_key(evidence.hero) not in _draw_hero_count_keys():
        return center  # scope: only draw-heroes; aisha/ahmed/ethan/maria/etc. unchanged
    scale = _draw_hero_count_scale(round_no)
    boosted = max(int(center), int(round(_default_total_count(evidence.map_id) * scale)))
    if boosted != int(center):
        rtag = round_no if round_no is not None else "?"
        notes.append(f"draw_hero_count_center_boost:{int(center)}->{boosted}@r{rtag}({scale:g})")
    return boosted


def _hero_value_scale(hero: str | None) -> float:
    """Scoped value-percentile multiplier for a hero (1.0 = unchanged).

    Thin wrapper: normalise the hero key, then delegate to draw_hero_policy
    (which holds the DRAW_HERO_VALUE_SCALE table + the HERO_VALUE_SCALE_OVERRIDE env A/B).
    """
    return _policy_hero_value_scale(normalize_hero_key(str(hero or "")))


def _fixed_count_reachable_total_band(
    evidence: RefEvidence,
    probs: dict[str, float] | None,
    *,
    base_total: int,
) -> tuple[int, int] | None:
    """Reachable total-count band [lo, hi] implied by the FIXED (hard) per-quality counts.

    Fixed counts come from public info / skill reveals / scan props — absolute values that
    pin the corresponding qualities exactly. So total = Σfixed + Σ(free-quality counts), and
    each free quality can only contribute within its own prior count window. Totals OUTSIDE
    [lo, hi] therefore admit ZERO combos in the enumerator, regardless of the soft count prior.

    This lets the soft count center (e.g. grid/avg_cells hint, which over-states on a
    footroom-inflated grid) YIELD to the hard fixed counts instead of overriding them: when the
    soft window sits entirely above this band, the engine would enumerate only infeasible totals
    and return no_reachable_combo (observed live: 2405/2410/2501 decision rounds). Intersecting
    the soft window with this band is loss-free (it only drops totals that have no combo anyway).

    Returns None when nothing is fixed (no hard pin → no constraint) or probs is unavailable.
    The free windows are sized at `base_total` (the soft window's upper, generous). This keeps
    `hi` from under-stating the reachable max on DEEP near-full vaults — where the high soft
    center is legitimate and the free mid/high tiers genuinely large — so the band does not
    wrongly narrow them. The conflict cases are still caught because there the only free tier is
    red (q6), whose prob is tiny so its window max stays small even at a high base_total."""
    if probs is None or not evidence.fixed_counts:
        return None
    fixed_total = sum(max(0, int(value)) for value in evidence.fixed_counts.values())
    base_total = max(int(base_total), fixed_total + 8)
    lo = fixed_total
    hi = fixed_total
    for key in QUALITY_KEYS:
        if key in evidence.fixed_counts:
            continue
        free_min = _effective_min_count(key, evidence)
        lo += free_min
        window = _prior_count_values(base_total, key, evidence, probs)
        hi += max(window) if window else free_min
    if hi < lo:
        hi = lo
    return lo, hi


def _total_count_candidates(
    evidence: RefEvidence,
    notes: list[str],
    *,
    early_round_lightweight: bool = False,
    avg_cells_count_hint: int | None = None,
    avg_cells_count_hint_public: bool = False,
    round_no: int | None = None,
    probs: dict[str, float] | None = None,
) -> tuple[list[int], int | None]:
    if evidence.total_count is not None:
        total_count = int(evidence.total_count)
        fixed_total = sum(max(0, int(value)) for value in evidence.fixed_counts.values())
        if evidence.phase == "manual" and fixed_total != total_count:
            notes.append("manual_total_count_prior_enumeration")
            return [total_count], total_count
        if _should_use_sparse_exact_total_prior(evidence):
            if (
                _quality_cells_fully_pinned(evidence)
                and _quality_cells_blocks_sparse_exact_prior(evidence)
                and int(total_count) >= 40
            ):
                notes.append("pinned_quality_cells_sparse_prior")
            notes.append("sparse_exact_total_prior_enumeration")
            return [total_count], total_count
        return [total_count], None
    has_live_input = bool(
        evidence.source_notes
        or evidence.fixed_counts
        or evidence.min_counts
        or evidence.count_sums
        or evidence.avg_cells
        or evidence.avg_values
        or evidence.quality_values
        or evidence.total_grid_target
    )
    if not has_live_input:
        if evidence.phase in {"settled", "manual"}:
            return [], None
        center = max(_default_total_count(evidence.map_id), _known_count_floor(evidence))
        center = _draw_hero_count_center(center, evidence, notes, round_no=round_no)
        if early_round_lightweight:
            lower = max(_known_count_floor(evidence), center - 2, 1)
            upper = min(50, max(lower, center + 2))
            notes.append(AISHA_EARLY_ROUND_COUNT_PRIOR_WINDOW_NOTE)
        else:
            lower = max(_known_count_floor(evidence), center - 4, 1)
            upper = min(60, max(lower, center + 4))
        notes.append("total_count_from_map_default_prior")
        notes.append(f"total_count_prior_center:{center}")
        return list(range(lower, upper + 1)), center
    if _should_defer_total_count_prior(evidence):
        notes.append("waiting_total_count")
        notes.append("waiting_total_count:grid_only")
        return [], None
    center = max(_default_total_count(evidence.map_id), _known_count_floor(evidence))
    residual_center = _count_prior_center_from_grid_quality_residual(
        evidence,
        source_notes=notes,
        baseline_center=center,
    )
    if residual_center is not None:
        center = residual_center
    elif evidence.total_grid_target is not None and evidence.total_grid_target > 0:
        center = max(center, int(round(evidence.total_grid_target / 3.0)), 1)
    # Public all-items avg-cells cross-check: when the public total_avg_cells is known, the geometry
    # grid + avg pin count = grid / avg (avg_cells_count_hint). BIDIRECTIONAL (2026-06-20, user-designed,
    # eval_avg_conform: 件数 bias -4.4->-0.4, 符合度好, 总格不受损): make count CONFORM to the public
    # avg in BOTH directions — not just raise an under-estimate, also lower an over-estimate — so the
    # displayed count/grid satisfy grid = count×avg. (Old behaviour was one-directional > center only,
    # which left count un-conformed when grid/avg < the prior.) Rollback AISHA_DISABLE_AVG_COUNT_CONFORM=1.
    if avg_cells_count_hint is not None:
        # BIDIRECTIONAL conform 只在 hint 来自【公开均格】时(精确约束)生效; 来自残差 per-item 估计
        # (无公开均格, 不可靠)时保持旧【单向只上修】—— 否则会把真密深仓(2410)误压欠(残差 hint<先验)。
        if avg_cells_count_hint_public and not os.environ.get("AISHA_DISABLE_AVG_COUNT_CONFORM"):
            if int(avg_cells_count_hint) != int(center):
                center = int(avg_cells_count_hint)
                notes.append(f"total_count_from_grid_avg_cells:{center}")
        elif avg_cells_count_hint > int(center):  # 残差 hint 或 flag 关闭 → 单向只上修
            center = int(avg_cells_count_hint)
            notes.append(f"total_count_from_grid_avg_cells:{center}")
    center = _draw_hero_count_center(center, evidence, notes, round_no=round_no)
    if early_round_lightweight:
        lower = max(_known_count_floor(evidence), center - 2, 1)
        upper = min(50, max(lower, center + 2))
        notes.append(AISHA_EARLY_ROUND_COUNT_PRIOR_WINDOW_NOTE)
    else:
        lower = max(_known_count_floor(evidence), center - 4, 1)
        upper = max(lower, center + 4)
        upper = min(60, upper)
    # Hard fixed counts cap the soft prior: clamp the window to the reachable band implied by
    # the fixed per-quality counts (totals outside it have zero combos). Loss-free; when the soft
    # window sits entirely above the band (soft center over-states vs the fixed counts), re-anchor
    # to the band so the engine enumerates the feasible totals instead of returning
    # no_reachable_combo. Only bites when something is fixed (band is None otherwise).
    band = (
        None
        if os.environ.get("AISHA_DISABLE_FIXED_BAND_CLAMP") == "1"
        else _fixed_count_reachable_total_band(evidence, probs, base_total=upper)
    )
    if band is not None:
        band_lo, band_hi = band
        clamped_lo = max(lower, band_lo)
        clamped_hi = min(upper, band_hi)
        if clamped_lo > clamped_hi:
            notes.append(f"count_window_reanchored_to_fixed_band:{lower}-{upper}->{band_lo}-{band_hi}")
            lower, upper = band_lo, band_hi
            center = max(band_lo, min(center, band_hi))
        elif (clamped_lo, clamped_hi) != (lower, upper):
            notes.append(f"count_window_clamped_to_fixed_band:{lower}-{upper}->{clamped_lo}-{clamped_hi}")
            lower, upper = clamped_lo, clamped_hi
            center = max(lower, min(center, upper))
    notes.append("total_count_from_ref_count_prior")
    notes.append(f"total_count_prior_center:{center}")
    return list(range(lower, upper + 1)), center


def _prior_count_values(
    total: int,
    key: str,
    evidence: RefEvidence,
    probs: dict[str, float],
) -> list[int]:
    fixed = evidence.fixed_counts.get(key)
    exact_cells = _quality_exact_cells(key, evidence)
    if fixed is not None:
        fixed_int = max(0, int(fixed))
        if exact_cells is not None and not can_compose_grid_total(fixed_int, exact_cells):
            return []
        if not _quality_count_matches_value_evidence(key, fixed_int, evidence):
            return []
        return [fixed_int]
    minimum = _effective_min_count(key, evidence)
    maximum = total
    if exact_cells is not None:
        maximum = min(maximum, exact_cells)
        if exact_cells == 0:
            maximum = 0
            minimum = max(minimum, 0)
    p = max(0.0, min(1.0, float(probs.get(key, 0.0))))
    expected = total * p
    sigma = math.sqrt(max(0.75, total * p * max(0.0, 1.0 - p)))
    radius = max(1, min(5, int(math.ceil(1.6 * sigma))))
    if _sparse_exact_high_total_tight_prior(evidence):
        radius = max(1, min(radius, 2))
    lower = max(minimum, int(math.floor(expected - radius)))
    upper = min(maximum, int(math.ceil(expected + radius)))
    anchors = {
        minimum,
        int(round(expected)),
        int(math.floor(expected)),
        int(math.ceil(expected)),
    }
    values = {value for value in range(lower, upper + 1)}
    values.update(value for value in anchors if minimum <= value <= maximum)
    avg = evidence.avg_cells.get(key)
    if avg is not None and avg > 0:
        avg_valid = [
            value
            for value in range(minimum, maximum + 1)
            if _avg_grid_options(value, avg)
        ]
        if avg_valid:
            # Wire-precision avg_cells (e.g. 10/3 = 3.3333332, not a 2-decimal display) means the
            # tier total cells = avg*count is an exact integer, so the count must compose an integer
            # grid at that avg. Drop prior-window counts that can't (q4 avg 10/3 -> only multiples of
            # 3; 4/7/10 are impossible) by INTERSECTING, instead of just unioning the feasible set in.
            # Gated on "not a rounded display" so a true 2-decimal avg never over-prunes.
            if evidence.phase != "settled" and not _ref_avg_looks_like_display_reading(float(avg)):
                feasible = set(avg_valid)
                narrowed = {value for value in values if value in feasible}
                values = narrowed or feasible
            else:
                values.update(avg_valid)
    avg_value = evidence.avg_values.get(key)
    if (
        avg_value is not None
        and _avg_value_has_positive_signal(avg_value)
        and key not in evidence.soft_avg_value_keys
    ):
        avg_value_valid = [
            value
            for value in range(minimum, maximum + 1)
            if _quality_count_matches_value_evidence(key, value, evidence)
        ]
        if avg_value_valid:
            values.update(avg_value_valid)
    if exact_cells is not None:
        valid = _filter_quality_counts_for_avg_value(
            key,
            (
                value
                for value in sorted(values)
                if can_compose_grid_total(value, exact_cells)
            ),
            evidence,
        )
        if valid:
            return valid
        return _filter_quality_counts_for_avg_value(
            key,
            (
                value
                for value in range(minimum, maximum + 1)
                if can_compose_grid_total(value, exact_cells)
            ),
            evidence,
        )
    return _filter_quality_counts_for_avg_value(key, sorted(values), evidence)


def _evidence_grid_avg_cells_log_penalties(
    counts: dict[str, int],
    grids: dict[str, float],
    evidence: RefEvidence,
    *,
    include_grid_target: bool = True,
) -> float:
    logw = 0.0
    if include_grid_target and evidence.total_grid_target is not None:
        total_grid = sum(grids.values())
        diff = total_grid - evidence.total_grid_target
        logw -= min(30.0, (diff * diff) / (2 * 6.0 * 6.0))
    for key, avg in evidence.avg_cells.items():
        count = counts.get(key, 0)
        if count <= 0 or avg is None or avg <= 0:
            continue
        sigma = {
            "q1": 0.35,
            "q3": 0.35,
            "q4": 0.45,
            "q5": 0.55,
            "q6": 0.70,
        }.get(key, 0.55) / max(1.0, math.sqrt(count))
        diff = grids.get(key, 0.0) / count - avg
        logw -= min(30.0, 0.5 * (diff * diff) / (sigma * sigma))
    return logw


# Per-item cell size of a high tier GROWS with its known cell total: big gold/red hoards hold bigger
# items (scripts/audit_high_tier_cells_to_count.py, aisha library). A clamped linear divisor fit to
# the per-cell-bucket means; count center = known_cells / divisor. The combo multinomial alone centers
# the count on probs*total and ignores this, so a large known gold footprint under-counts (2406 R3:
# 28 gold cells -> engine 5 vs 6 truth; 28/4.6 ~= 6). (slope, intercept, lo, hi)
_HIGH_TIER_CELL_DIVISOR_FIT = {
    "q5": (0.0975, 1.87, 2.2, 4.8),  # gold: cells 4->2.3 ... 28->4.6
    "q6": (0.110, 2.10, 2.2, 5.6),  # red:  cells 4->2.5 ... 16->3.9 ... plateau
}
# Strength of the soft pull toward the cell-implied count center (Gaussian sigma; larger = weaker).
HIGH_TIER_KNOWN_CELLS_COUNT_BIAS_SIGMA = 2.0


def _high_tier_count_center_from_cells(key: str, cells: int) -> float | None:
    fit = _HIGH_TIER_CELL_DIVISOR_FIT.get(key)
    if fit is None or cells <= 0:
        return None
    slope, intercept, lo, hi = fit
    divisor = min(hi, max(lo, intercept + slope * cells))
    return cells / divisor


def _known_cells_count_bias_log(counts: dict[str, int], evidence: RefEvidence) -> float:
    """Aisha-only soft nudge of a high-tier combo count toward its cell-implied center.

    When q5/q6 CELLS are known (e.g. gold via 极品扫描) but the count is not locked, the empirical
    cells->count center (which rises with the hoard size) corrects the multinomial's probs*total
    under-count. A loose Gaussian keeps it a nudge, not an override -- combos still satisfy the hard
    cell composition; this only reweights which surviving count is most likely.
    """
    if normalize_hero_key(evidence.hero) != "aisha":
        return 0.0
    sigma = HIGH_TIER_KNOWN_CELLS_COUNT_BIAS_SIGMA
    try:
        sigma = float(os.environ.get("AISHA_HIGH_TIER_COUNT_BIAS_SIGMA", sigma))
    except (TypeError, ValueError):
        pass
    if sigma <= 0:
        return 0.0
    bias = 0.0
    for key in ("q5", "q6"):
        if key in evidence.fixed_counts:
            continue  # count already locked -> nothing to nudge
        exact = _exact_integer_quality_cell(evidence.quality_cells.get(key))
        if exact is None or exact <= 0:
            continue
        center = _high_tier_count_center_from_cells(key, int(exact))
        if center is None:
            continue
        bias -= 0.5 * ((float(counts[key]) - center) / sigma) ** 2
    return bias


# A revealed GIANT red (one q6 collectible occupying many cells) is the same evidence whether it
# arrives via 公开红 (public max-cell info) or 巨物抽样 (skill) — both land in q6 cell_floor via
# _coarse_quality_reveal_floors. The giant dominates the red value (its footprint is most of the red
# space), so the residual UNREVEALED red is near zero — but the multinomial count prior
# (probs['q6']*total) is blind to the reveal and piles ~p6*total phantom reds on top (4531 R4: q6
# count center ~6 vs truth 2; balanced 1.80M vs truth 854k). Nudge q6 toward the REVEALED count.
GIANT_RED_REVEAL_MIN_CELLS = 8.0          # a single revealed red >= this is "giant"
GIANT_RED_REVEAL_MIN_CELLS_PER_ITEM = 6.0  # avg revealed-red cells; below this it's normal red
AISHA_GIANT_RED_COUNT_PRIOR_SIGMA = 1.0


def _revealed_giant_red_count_bias_log(counts: dict[str, int], evidence: RefEvidence) -> float:
    """Aisha-only soft nudge of the q6 (red) combo count toward the REVEALED count when a GIANT red
    is revealed. A loose Gaussian keeps it a nudge, not a clamp — combos still satisfy the hard cell
    floor and the count can rise back under hard evidence. STRICTLY gated on a giant reveal so normal
    small revealed red (which the engine already UNDER-counts library-wide — Phase A) is never
    touched: the gate is a structural no-op whenever red is invisible (no q6 cell_floor). Rollback
    BIDKING_DISABLE_AISHA_GIANT_RED_COUNT=1."""
    if normalize_hero_key(evidence.hero) != "aisha":
        return 0.0
    if os.environ.get("BIDKING_DISABLE_AISHA_GIANT_RED_COUNT") == "1":
        return 0.0
    if "q6" in evidence.fixed_counts:
        return 0.0  # red count already locked -> nothing to nudge
    revealed = int(evidence.min_counts.get("q6", 0) or 0)
    if revealed <= 0:
        return 0.0  # no revealed red -> invisible/pure-prior, leave the under-count path alone
    cell_floor = _quality_cell_floor("q6", evidence)
    if cell_floor < GIANT_RED_REVEAL_MIN_CELLS:
        return 0.0  # revealed red is not giant
    if cell_floor / max(1, revealed) < GIANT_RED_REVEAL_MIN_CELLS_PER_ITEM:
        return 0.0  # many small revealed reds, not a single giant
    sigma = AISHA_GIANT_RED_COUNT_PRIOR_SIGMA
    try:
        sigma = float(os.environ.get("AISHA_GIANT_RED_COUNT_SIGMA", sigma))
    except (TypeError, ValueError):
        pass
    if sigma <= 0:
        return 0.0
    center = max(1, revealed)
    return -0.5 * ((float(counts.get("q6", 0)) - center) / sigma) ** 2


def _prior_log_weight(
    counts: dict[str, int],
    grids: dict[str, float],
    *,
    total: int,
    total_prior_center: int,
    evidence: RefEvidence,
    probs: dict[str, float],
    item_values: dict[str, float] | None = None,
) -> float:
    logw = _log_fact(total)
    for key in QUALITY_KEYS:
        count = counts[key]
        logw -= _log_fact(count)
        if count:
            logw += count * math.log(max(1e-9, probs[key]))
    logw -= ((total - total_prior_center) ** 2) / (2 * 4.0 * 4.0)
    logw += _known_cells_count_bias_log(counts, evidence)
    logw += _revealed_giant_red_count_bias_log(counts, evidence)
    logw += _evidence_grid_avg_cells_log_penalties(counts, grids, evidence)
    avg_value_penalty, _ = _soft_public_avg_value_log_weight(
        counts,
        grids,
        evidence,
        item_values=item_values,
    )
    logw += avg_value_penalty
    return logw


def _soft_public_avg_value_log_weight(
    counts: dict[str, int],
    grids: dict[str, float],
    evidence: RefEvidence,
    *,
    item_values: dict[str, float] | None = None,
) -> tuple[float, bool]:
    item_values = item_values or {}
    penalty = 0.0
    applied = False
    for key in evidence.soft_avg_value_keys:
        avg = evidence.avg_values.get(key)
        count = int(counts.get(key, 0))
        if avg is None or not _avg_value_has_positive_signal(avg) or count <= 0:
            continue
        grid = float(grids.get(key, count * DEFAULT_GRID_MEANS[key]))
        tier_total = _quality_value_for_evidence(
            key,
            count=count,
            grid=grid,
            item_values=item_values,
            evidence=evidence,
        )
        combo_avg = tier_total / count
        cv = VALUE_UNCERTAINTY_CV.get(key, 0.20)
        sigma = max(8_000.0, float(avg) * cv / math.sqrt(max(1, count)))
        diff = combo_avg - float(avg)
        penalty -= min(25.0, 0.5 * (diff / sigma) ** 2)
        applied = True
    return penalty, applied


def _count_sum_matches(counts: dict[str, int], evidence: RefEvidence) -> bool:
    q4q5 = evidence.count_sums.get("q4q5")
    if q4q5 is not None and counts.get("q4", 0) + counts.get("q5", 0) != int(q4q5):
        return False
    q4q5q6 = evidence.count_sums.get("q4q5q6")
    if (
        q4q5q6 is not None
        and counts.get("q4", 0) + counts.get("q5", 0) + counts.get("q6", 0)
        != int(q4q5q6)
    ):
        return False
    return True


def _random_value_floor(evidence: RefEvidence) -> float | None:
    floors = [
        float(value_floor)
        for _sample_count, value_floor in evidence.random_value_floors
        if value_floor > 0
    ]
    if not floors:
        return None
    return max(floors)


def _random_value_floor_log_weight(value: float, evidence: RefEvidence) -> float:
    floor = _random_value_floor(evidence)
    if floor is None or value >= floor:
        return 0.0
    scale = max(30_000.0, floor * 0.25)
    diff = (floor - value) / scale
    return -min(25.0, 0.5 * diff * diff)


def _quality_exact_value_log_weight(
    counts: dict[str, int],
    grids: dict[str, float],
    item_values: dict[str, float],
    evidence: RefEvidence,
) -> tuple[float, bool]:
    penalty = 0.0
    applied = False
    for key in QUALITY_KEYS:
        if key in evidence.avg_values:
            continue
        exact_value = _quality_exact_value(key, evidence)
        if exact_value is None or exact_value <= 0:
            continue
        count = int(counts.get(key, 0))
        if count <= 0:
            continue
        center = _quality_value_for_grid(
            key,
            count=count,
            grid=grids.get(key, count * DEFAULT_GRID_MEANS[key]),
            item_values=item_values,
        )
        cv = VALUE_UNCERTAINTY_CV.get(key, 0.20)
        scale = max(20_000.0, center * cv / math.sqrt(max(1, count)))
        diff = (float(exact_value) - center) / scale
        penalty -= min(25.0, 0.5 * diff * diff)
        applied = True
    return penalty, applied


def _enumerate_prior_count_combos(
    total: int,
    *,
    evidence: RefEvidence,
    probs: dict[str, float],
    total_prior_center: int,
    max_new: int,
    item_values: dict[str, float] | None = None,
) -> list[RefCombo]:
    if max_new <= 0:
        return []
    values = {
        key: _prior_count_values(total, key, evidence, probs)
        for key in QUALITY_KEYS
    }
    combos: list[RefCombo] = []
    q1_fixed = evidence.fixed_counts.get("q1")
    for q3 in values["q3"]:
        for q4 in values["q4"]:
            for q5 in values["q5"]:
                for q6 in values["q6"]:
                    q1 = total - q3 - q4 - q5 - q6
                    if q1_fixed is not None and q1 != int(q1_fixed):
                        continue
                    if q1 not in values["q1"]:
                        continue
                    counts = {"q1": q1, "q3": q3, "q4": q4, "q5": q5, "q6": q6}
                    if not _count_sum_matches(counts, evidence):
                        continue
                    if any(counts[key] < _effective_min_count(key, evidence) for key in QUALITY_KEYS):
                        continue
                    grids = _grids_for_counts(counts, evidence)
                    if grids is None:
                        continue
                    combos.append(
                        RefCombo(
                            counts=counts,
                            grids=grids,
                            value=0.0,
                            weight=_prior_log_weight(
                                counts,
                                grids,
                                total=total,
                                total_prior_center=total_prior_center,
                                evidence=evidence,
                                probs=probs,
                                item_values=item_values,
                            ),
                            total_grid=sum(grids.values()),
                        )
                    )
                    if len(combos) >= max_new:
                        return combos
    return combos


def _allocate_integer_counts(total: int, weights: dict[str, float]) -> dict[str, int]:
    keys = list(weights)
    weight_total = sum(max(0.0, weights[key]) for key in keys) or 1.0
    raw = {key: total * max(0.0, weights[key]) / weight_total for key in keys}
    out = {key: int(math.floor(value)) for key, value in raw.items()}
    remainder = total - sum(out.values())
    order = sorted(keys, key=lambda key: raw[key] - out[key], reverse=True)
    for key in order[: max(0, remainder)]:
        out[key] += 1
    return out


def _prior_counts_for_total(
    total: int,
    evidence: RefEvidence,
    probs: dict[str, float],
) -> dict[str, int] | None:
    counts = {key: 0 for key in QUALITY_KEYS}
    fixed_keys: set[str] = set()
    for key, value in evidence.min_counts.items():
        counts[key] = max(counts[key], max(0, int(value)))
    for key, value in evidence.fixed_counts.items():
        counts[key] = max(0, int(value))
        fixed_keys.add(key)

    for group_key, group_keys_tuple in (
        ("q4q5", ("q4", "q5")),
        ("q4q5q6", ("q4", "q5", "q6")),
    ):
        group_sum = evidence.count_sums.get(group_key)
        if group_sum is None:
            continue
        group_total = max(0, int(group_sum))
        fixed_group = sum(counts[key] for key in group_keys_tuple)
        if fixed_group > group_total:
            return None
        missing_group = group_total - fixed_group
        group_keys = [key for key in group_keys_tuple if key not in fixed_keys]
        if group_keys and missing_group:
            group_alloc = _allocate_integer_counts(
                missing_group,
                {key: probs[key] for key in group_keys},
            )
            for key, value in group_alloc.items():
                counts[key] += value
                fixed_keys.add(key)
        elif missing_group:
            return None

    used = sum(counts.values())
    if used > total:
        return None
    free_keys = [key for key in QUALITY_KEYS if key not in fixed_keys]
    if free_keys:
        alloc = _allocate_integer_counts(total - used, {key: probs[key] for key in free_keys})
        for key, value in alloc.items():
            counts[key] += value
    elif used != total:
        return None
    if any(counts[key] < _effective_min_count(key, evidence) for key in QUALITY_KEYS):
        return None
    if any(
        not _quality_count_matches_value_evidence(key, counts[key], evidence)
        for key in QUALITY_KEYS
    ):
        return None
    if not _count_sum_matches(counts, evidence):
        return None
    return counts


def _grids_for_counts(
    counts: dict[str, int],
    evidence: RefEvidence,
) -> dict[str, float] | None:
    grids: dict[str, float] = {}
    for key in QUALITY_KEYS:
        count = counts[key]
        exact_cells = _quality_exact_cells(key, evidence)
        cell_floor = _quality_cell_floor(key, evidence)
        avg = evidence.avg_cells.get(key)
        if exact_cells is not None:
            if exact_cells < cell_floor:
                return None
            if not can_compose_grid_total(count, exact_cells):
                return None
            if not _avg_matches_exact_grid(count, avg, exact_cells):
                return None
            grids[key] = float(exact_cells)
        elif count <= 0:
            if cell_floor > 0:
                return None
            grids[key] = 0.0
        elif avg is not None:
            if key in evidence.soft_avg_cell_keys:
                default = count * float(avg)
                options = [
                    option
                    for option in _composable_grid_options(int(count))
                    if option >= cell_floor
                ]
                if not options:
                    return None
                grids[key] = float(
                    min(options, key=lambda option: (abs(option - default), option))
                )
            else:
                options = _avg_grid_options(count, avg)
                if cell_floor > 0:
                    options = [option for option in options if option >= cell_floor]
                selected = _choose_avg_grid_option(count, avg, options)
                if selected is None:
                    return None
                grids[key] = selected
        else:
            default = count * DEFAULT_GRID_MEANS[key]
            if cell_floor > 0:
                if (
                    _should_use_exact_total_avg_cells_fast_path(evidence)
                    and evidence.total_grid_target is None
                ):
                    fast_grid = _nearest_composable_default_grid(
                        int(count),
                        default,
                        cell_floor=cell_floor,
                    )
                    if fast_grid is not None:
                        grids[key] = fast_grid
                    else:
                        options = [
                            option
                            for option in _composable_grid_options(int(count))
                            if option >= cell_floor
                        ]
                        if not options:
                            return None
                        grids[key] = float(
                            min(options, key=lambda option: (abs(option - default), option))
                        )
                else:
                    options = [
                        option
                        for option in _composable_grid_options(int(count))
                        if option >= cell_floor
                    ]
                    if not options:
                        return None
                    grids[key] = float(min(options, key=lambda option: (abs(option - default), option)))
            else:
                grids[key] = default
    fitted = _fit_grids_to_total_target(
        grids,
        counts,
        evidence.avg_cells,
        evidence.total_grid_target,
        fixed_grid_keys=set(evidence.quality_cells),
        soft_avg_cell_keys=evidence.soft_avg_cell_keys,
    )
    hard_total = _hard_total_grid_target_int(evidence)
    if hard_total is not None and abs(sum(fitted.values()) - hard_total) > 0.0001:
        return None
    soft_floor = _soft_total_grid_floor_int(evidence)
    if soft_floor is not None and sum(fitted.values()) + 1e-6 < soft_floor:
        return None
    for key in QUALITY_KEYS:
        if fitted.get(key, 0.0) + 1e-6 < _quality_cell_floor(key, evidence):
            return None
    if not _split_low_quality_q1_grid_matches(counts.get("q1", 0), fitted.get("q1", 0.0), evidence):
        return None
    return fitted


def _quality_value_for_grid(
    key: str,
    *,
    count: int,
    grid: float,
    item_values: dict[str, float],
) -> float:
    if count <= 0:
        return 0.0
    base_total = count * item_values[key]
    mean = max(1.0, DEFAULT_GRID_MEANS[key])
    avg = max(1.0, grid / count)
    impact_ratio = 0.03 if key == "q1" else 0.08
    adjustment = (avg - mean) * item_values[key] * impact_ratio * count
    cap = base_total * (0.20 if key == "q1" else 0.35)
    adjustment = max(-cap, min(cap, adjustment))
    return max(float(count), base_total + adjustment)


def _quality_value_for_evidence(
    key: str,
    *,
    count: int,
    grid: float,
    item_values: dict[str, float],
    evidence: RefEvidence,
) -> float:
    exact_value = _quality_exact_value(key, evidence)
    if exact_value is not None:
        return float(exact_value)
    partial = _partial_known_quality_value_state(
        key,
        count=count,
        grid=grid,
        item_values=item_values,
        evidence=evidence,
    )
    if partial is not None:
        known_sum, _remaining_count, unknown_default, unknown_grid_value = partial
        grid_center = known_sum + unknown_grid_value
        default_floor = known_sum + unknown_default
        avg_value = evidence.avg_values.get(key)
        if avg_value is not None:
            total = _avg_value_total_from_count(float(avg_value), int(count))
            if total is not None:
                return max(default_floor, grid_center, total)
        return max(default_floor, grid_center)
    value_floor = _quality_value_floor_for_count(
        key,
        count=count,
        grid=grid,
        item_values=item_values,
        evidence=evidence,
    )
    avg_value = evidence.avg_values.get(key)
    if avg_value is not None:
        total = _avg_value_total_from_count(float(avg_value), int(count))
        if total is not None:
            return max(value_floor, total)
    return max(
        value_floor,
        _quality_value_for_grid(
            key,
            count=count,
            grid=grid,
            item_values=item_values,
        ),
    )


def _value_distribution_points_with_floor(
    center: float,
    spread: float,
    floor: float,
) -> tuple[tuple[float, float], ...]:
    return tuple(
        (max(float(floor), value), weight)
        for value, weight in _value_distribution_points(center, spread)
    )


def _quality_value_distribution_points(
    key: str,
    *,
    center: float,
    spread: float,
    count: int,
    grid: float,
    item_values: dict[str, float],
    evidence: RefEvidence,
) -> tuple[tuple[float, float], ...]:
    return _value_distribution_points_with_floor(
        center,
        spread,
        _quality_value_floor_for_count(
            key,
            count=count,
            grid=grid,
            item_values=item_values,
            evidence=evidence,
        ),
    )


def _total_value_floor(
    evidence: RefEvidence,
    *,
    counts: dict[str, int] | None = None,
    grids: dict[str, float] | None = None,
    item_values: dict[str, float] | None = None,
) -> float:
    if counts is None or grids is None or item_values is None:
        return sum(_quality_value_floor(key, evidence) for key in QUALITY_KEYS)
    return sum(
        _quality_value_floor_for_count(
            key,
            count=counts[key],
            grid=grids.get(key, counts[key] * DEFAULT_GRID_MEANS[key]),
            item_values=item_values,
            evidence=evidence,
        )
        for key in QUALITY_KEYS
    )


def _combo_value_distribution_points(
    center: float,
    spread: float,
    evidence: RefEvidence,
    *,
    counts: dict[str, int],
    grids: dict[str, float],
    item_values: dict[str, float],
) -> tuple[tuple[float, float], ...]:
    return _value_distribution_points_with_floor(
        center,
        spread,
        _total_value_floor(
            evidence,
            counts=counts,
            grids=grids,
            item_values=item_values,
        ),
    )


def _combo_value(
    counts: dict[str, int],
    grids: dict[str, float],
    item_values: dict[str, float],
    evidence: RefEvidence,
) -> float:
    return sum(
        _quality_value_for_evidence(
            key,
            count=counts[key],
            grid=grids.get(key, counts[key] * DEFAULT_GRID_MEANS[key]),
            item_values=item_values,
            evidence=evidence,
        )
        for key in QUALITY_KEYS
    )


def _quality_value_uncertainty(
    key: str,
    *,
    count: int,
    grid: float,
    item_values: dict[str, float],
    evidence: RefEvidence,
) -> float:
    if count <= 0:
        return 0.0
    if _quality_exact_value(key, evidence) is not None:
        return 0.0
    known_count = evidence.quality_value_floor_item_counts.get(key, 0)
    known_sum = _quality_value_floor(key, evidence)
    if known_count > 0 and known_sum > 0 and count > known_count:
        known_cells = float(evidence.quality_cell_floors.get(key, 0.0))
        remaining_count = count - known_count
        remaining_grid = max(float(remaining_count), grid - known_cells)
        center = _quality_value_for_grid(
            key,
            count=remaining_count,
            grid=remaining_grid,
            item_values=item_values,
        )
        cv = VALUE_UNCERTAINTY_CV.get(key, 0.20)
        return max(0.0, center * cv / math.sqrt(max(1, remaining_count)))
    avg_value = evidence.avg_values.get(key)
    if (
        avg_value is not None
        and _avg_value_total_from_count(float(avg_value), int(count)) is not None
    ):
        return 0.0
    center = _quality_value_for_grid(
        key,
        count=count,
        grid=grid,
        item_values=item_values,
    )
    cv = VALUE_UNCERTAINTY_CV.get(key, 0.20)
    return max(0.0, center * cv / math.sqrt(max(1, int(count))))


def _combo_value_uncertainty(
    counts: dict[str, int],
    grids: dict[str, float],
    item_values: dict[str, float],
    evidence: RefEvidence,
) -> float:
    variance = 0.0
    for key in QUALITY_KEYS:
        spread = _quality_value_uncertainty(
            key,
            count=counts[key],
            grid=grids.get(key, counts[key] * DEFAULT_GRID_MEANS[key]),
            item_values=item_values,
            evidence=evidence,
        )
        variance += spread * spread
    return math.sqrt(variance)


def _value_distribution_points(
    center: float,
    spread: float,
) -> tuple[tuple[float, float], ...]:
    if spread < 1.0:
        return ((center, 1.0),)
    return tuple(
        (max(0.0, center + offset * spread), weight)
        for offset, weight in VALUE_DISTRIBUTION_POINTS
    )


def _grid_display_signature(
    evidence: RefEvidence,
) -> tuple[tuple[int | None, ...], tuple[float | None, ...], int | None]:
    exact = tuple(_quality_exact_cells(key, evidence) for key in QUALITY_KEYS)
    avg = tuple(
        round(float(evidence.avg_cells[key]), 6)
        if key in evidence.avg_cells
        else None
        for key in QUALITY_KEYS
    )
    target_int = None
    if evidence.total_grid_target is not None:
        rounded = int(round(float(evidence.total_grid_target)))
        if abs(float(evidence.total_grid_target) - rounded) <= 0.25:
            target_int = rounded
    return exact, avg, target_int


def _cached_grid_constraint_options(
    count: int,
    exact_cells: int | None,
    avg: float | None,
) -> tuple[int, ...] | None:
    if exact_cells is not None:
        if not can_compose_grid_total(count, exact_cells):
            return ()
        if not _avg_matches_exact_grid(count, avg, exact_cells):
            return ()
        return (exact_cells,)
    if count <= 0:
        return (0,)
    if avg is not None:
        return tuple(_avg_grid_options(count, avg))
    return None


@lru_cache(maxsize=20000)
def _display_grid_options_cached(
    counts_tuple: tuple[int, ...],
    key: str,
    exact_tuple: tuple[int | None, ...],
    avg_tuple: tuple[float | None, ...],
    target_int: int | None,
) -> tuple[int, ...]:
    key_index = QUALITY_KEYS.index(key)
    count = counts_tuple[key_index]
    exact_cells = exact_tuple[key_index]
    avg = avg_tuple[key_index]
    constrained = _cached_grid_constraint_options(count, exact_cells, avg)
    if constrained is not None:
        return constrained
    candidates = _composable_grid_options(count)
    if target_int is not None:
        residual = target_int
        all_other_exact = True
        for idx, other_key in enumerate(QUALITY_KEYS):
            if other_key == key:
                continue
            other_options = _cached_grid_constraint_options(
                counts_tuple[idx],
                exact_tuple[idx],
                avg_tuple[idx],
            )
            if other_options is None or len(other_options) != 1:
                all_other_exact = False
                break
            residual -= other_options[0]
        if all_other_exact:
            candidates = tuple(option for option in candidates if option == residual)
    default = count * DEFAULT_GRID_MEANS[key]
    ranked = sorted(candidates, key=lambda option: (abs(option - default), option))
    return tuple(ranked[:DISPLAY_GRID_TOPK])


def _display_grid_options_for_quality(
    counts: dict[str, int],
    evidence: RefEvidence,
    key: str,
) -> tuple[int, ...]:
    exact, avg, target_int = _grid_display_signature(evidence)
    options = _display_grid_options_cached(
        tuple(int(counts[item]) for item in QUALITY_KEYS),
        key,
        exact,
        avg,
        target_int,
    )
    count = int(counts.get(key, 0))
    cell_floor = _quality_cell_floor(key, evidence)
    if cell_floor > 0:
        filtered = tuple(option for option in options if option >= cell_floor)
        if filtered:
            options = filtered
        elif _quality_exact_cells(key, evidence) is not None or evidence.avg_cells.get(key) is not None:
            return ()
        else:
            candidates = tuple(
                option
                for option in _composable_grid_options(count)
                if option >= cell_floor
            )
            default = count * DEFAULT_GRID_MEANS[key]
            ranked = sorted(candidates, key=lambda option: (abs(option - default), option))
            options = tuple(ranked[:DISPLAY_GRID_TOPK])
    if key == "q1":
        floor = _split_low_quality_q1_grid_floor(count, evidence)
        if floor > 0:
            filtered = tuple(option for option in options if option >= floor)
            if filtered:
                return filtered
            if _quality_exact_cells(key, evidence) is not None or evidence.avg_cells.get(key) is not None:
                return ()
            candidates = tuple(option for option in _composable_grid_options(count) if option >= floor)
            default = count * DEFAULT_GRID_MEANS[key]
            ranked = sorted(candidates, key=lambda option: (abs(option - default), option))
            return tuple(ranked[:DISPLAY_GRID_TOPK])
    if options:
        return options
    if cell_floor > 0:
        return ()
    fallback = int(round(counts.get(key, 0) * DEFAULT_GRID_MEANS[key]))
    if can_compose_grid_total(int(counts.get(key, 0)), fallback):
        return (fallback,)
    return _composable_grid_options(int(counts.get(key, 0)))[:1]


def _public_quality_avg_value_notes(notes: Iterable[str]) -> list[str]:
    return [
        str(note)
        for note in notes
        if re.fullmatch(r"public_q[456]_avg_value", str(note))
    ]


def _without_public_quality_avg_values(snapshot: dict[str, Any]) -> dict[str, Any]:
    cloned = copy.deepcopy(snapshot)
    ui_contract = cloned.get("ui_contract")
    if not isinstance(ui_contract, dict):
        return cloned
    constraints = ui_contract.get("constraints")
    if not isinstance(constraints, dict):
        return cloned
    public_info = constraints.get("public_info")
    if not isinstance(public_info, dict):
        return cloned

    def keep(row: Any) -> bool:
        if not isinstance(row, dict):
            return True
        semantic = str(row.get("semantic") or "")
        return semantic not in PUBLIC_AVG_VALUES

    for key in ("public_numeric_facts", "public_avg_values"):
        rows = public_info.get(key)
        if isinstance(rows, list):
            public_info[key] = [row for row in rows if keep(row)]
    return cloned


def _derive_soft_public_avg_cell_keys(
    *,
    source_notes: Iterable[str],
    fixed_counts: dict[str, int],
    quality_cells: dict[str, float],
) -> frozenset[str]:
    soft: set[str] = set()
    for note in source_notes:
        match = re.fullmatch(r"public_(q[456])_avg_cells", str(note))
        if not match:
            continue
        key = match.group(1)
        if fixed_counts.get(key) is not None:
            continue
        if key in quality_cells:
            continue
        soft.add(key)
    return frozenset(soft)


def _derive_soft_public_avg_value_keys(
    *,
    source_notes: Iterable[str],
    fixed_counts: dict[str, int],
    quality_values: dict[str, float],
    avg_values: dict[str, float],
) -> frozenset[str]:
    soft: set[str] = set()
    for note in source_notes:
        match = re.fullmatch(r"public_(q[456])_avg_value", str(note))
        if not match:
            continue
        key = match.group(1)
        if fixed_counts.get(key) is not None:
            continue
        if key in quality_values:
            continue
        if key not in avg_values:
            continue
        soft.add(key)
    return frozenset(soft)


def _public_avg_value_soft_pending(
    key: str,
    *,
    source_notes: Iterable[str],
    fixed_counts: dict[str, int],
    quality_values: dict[str, float],
    avg_values: dict[str, float],
) -> bool:
    if not any(str(note) == f"public_{key}_avg_value" for note in source_notes):
        return False
    if fixed_counts.get(key) is not None:
        return False
    if key in quality_values:
        return False
    return key in avg_values


def _public_quality_avg_cells_notes(notes: Iterable[str]) -> list[str]:
    return [
        str(note)
        for note in notes
        if re.fullmatch(r"public_q[456]_avg_cells", str(note))
    ]


def _without_public_avg_cell_constraints(
    snapshot: dict[str, Any],
    avg_cell_notes: Iterable[str],
) -> dict[str, Any]:
    qualities: set[str] = set()
    for note in avg_cell_notes:
        match = re.fullmatch(r"public_(q[456])_avg_cells", str(note))
        if match:
            qualities.add(match.group(1))
    if not qualities:
        return snapshot
    cloned = copy.deepcopy(snapshot)
    for container_key in ("structured_ref_inputs",):
        container = cloned.get(container_key)
        if not isinstance(container, dict):
            continue
        avg_cells = container.get("avg_cells")
        if isinstance(avg_cells, dict):
            container["avg_cells"] = {
                key: value for key, value in avg_cells.items() if key not in qualities
            }
    ui_contract = cloned.get("ui_contract")
    if isinstance(ui_contract, dict):
        constraints = ui_contract.get("constraints")
        if isinstance(constraints, dict):
            public_info = constraints.get("public_info")
            if isinstance(public_info, dict):
                blocked = {f"{quality}_avg_cells" for quality in ("q4", "q5", "q6")}

                def keep(row: Any) -> bool:
                    if not isinstance(row, dict):
                        return True
                    return str(row.get("semantic") or "") not in blocked

                for key in ("public_numeric_facts", "public_avg_values"):
                    rows = public_info.get(key)
                    if isinstance(rows, list):
                        public_info[key] = [row for row in rows if keep(row)]
    return cloned


def _with_public_quality_avg_fallback_notes(
    result: RefResult,
    *,
    public_avg_value_notes: Iterable[str],
    public_avg_cell_notes: Iterable[str],
) -> RefResult:
    downgrade_notes = ["public_quality_avg_value_conflict_fallback"]
    downgrade_notes.extend(f"{note}_downgraded" for note in public_avg_value_notes)
    if public_avg_cell_notes:
        downgrade_notes.append("public_quality_avg_cells_conflict_fallback")
        downgrade_notes.extend(f"{note}_downgraded" for note in public_avg_cell_notes)
    notes = tuple(dict.fromkeys([*result.notes, *downgrade_notes]))
    evidence = replace(
        result.evidence,
        source_notes=tuple(dict.fromkeys([*result.evidence.source_notes, *downgrade_notes])),
    )
    return replace(result, notes=notes, evidence=evidence)


def _retry_without_public_quality_avg_values(
    snapshot: dict[str, Any],
    *,
    public_avg_notes: Iterable[str],
    static_data: dict[str, Any],
    safety_factor: float,
    max_combos: int,
) -> RefResult | None:
    notes = list(public_avg_notes)
    if not notes:
        return None
    result = run_reference_engine(
        _without_public_quality_avg_values(snapshot),
        static_data=static_data,
        safety_factor=safety_factor,
        max_combos=max_combos,
        _allow_public_avg_fallback=False,
    )
    if result.status not in {"ok", "count_prior"}:
        return None
    return _with_public_quality_avg_fallback_notes(
        result,
        public_avg_value_notes=notes,
        public_avg_cell_notes=(),
    )


def _retry_without_public_avg_constraints(
    snapshot: dict[str, Any],
    *,
    public_avg_value_notes: Iterable[str],
    public_avg_cell_notes: Iterable[str],
    static_data: dict[str, Any],
    safety_factor: float,
    max_combos: int,
) -> RefResult | None:
    value_notes = list(public_avg_value_notes)
    cell_notes = list(public_avg_cell_notes)
    if not value_notes and not cell_notes:
        return None
    working = snapshot
    if value_notes:
        working = _without_public_quality_avg_values(working)
    if cell_notes:
        working = _without_public_avg_cell_constraints(working, cell_notes)
    result = run_reference_engine(
        working,
        static_data=static_data,
        safety_factor=safety_factor,
        max_combos=max_combos,
        _allow_public_avg_fallback=False,
    )
    if result.status not in {"ok", "count_prior"}:
        return None
    return _with_public_quality_avg_fallback_notes(
        result,
        public_avg_value_notes=value_notes,
        public_avg_cell_notes=cell_notes,
    )


HIGH_TIER_GRID_RELAXED_NOTE = "high_tier_grid_target_relaxed_no_reachable"


def _retry_without_high_tier_grid_target(
    snapshot: dict[str, Any],
    *,
    static_data: dict[str, Any],
    safety_factor: float,
    max_combos: int,
) -> RefResult | None:
    """The high-tier-cell grid target is a DERIVED heuristic (raises the total grid from known
    q3–q5 cells + residual items). It ignores public-reveal cell floors (a single q6 red revealed
    at N cells) and a residual tier's known avg_cells, so it can pin the total grid BELOW the
    physical minimum and reject every combo — at the decision round the overlay then shows no quote
    at all (2537 R4: derived target 83 < physical min 87 → no_reachable_combo). The real warehouse
    is always feasible, so when the derived target is the blocker, re-run once without it and let the
    count_prior path produce an estimate. Only fires on an otherwise-empty combo set, so it cannot
    change any sample that already quotes."""
    working = dict(snapshot)
    working["_skip_high_tier_grid_target"] = True
    result = run_reference_engine(
        working,
        static_data=static_data,
        safety_factor=safety_factor,
        max_combos=max_combos,
        _allow_public_avg_fallback=False,
        _allow_high_tier_grid_fallback=False,
    )
    if result.status not in {"ok", "count_prior"}:
        return None
    notes = list(result.notes)
    _append_source_note_once(notes, HIGH_TIER_GRID_RELAXED_NOTE)
    return replace(result, notes=tuple(notes))


AISHA_RED_ROOM_REWEIGHT_NOTE_PREFIX = "aisha_red_room_reweight:"
_AISHA_RED_ROOM_MIN_ROUND = 3
_AISHA_RED_ROOM_MIN_ROOM = 3.0  # cap - non_red below this is geometry noise, not real red space (R6)
_AISHA_RED_ROOM_TIGHT_MAX = 10.0  # only tight vaults (low-red signature) carry false-positive red;
# large room = deep/rich vault where red genuinely fills space (high-red, accurate) -> leave alone
_AISHA_RED_ROOM_RED_VOL = 3.2  # avg red (q6) cells per item, for cells<->count of the room ceiling
_AISHA_RED_ROOM_TIER_SLACK = 2.0  # allow up to 2x the map-tier expected red before the room binds
_AISHA_RED_ROOM_PENALTY = 0.35  # log-weight penalty per red CELL over the ceiling (soft, not a hard cut)


def _aisha_red_room_reweighted_combos(
    combos: list[RefCombo],
    *,
    hero_key: str,
    round_no: int | None,
    evidence: RefEvidence,
    probs: dict[str, float],
    geom_capacity: float | None,
    notes: list[str],
) -> list[RefCombo]:
    """Aisha pure-prior red: soft-reweight combos by how well their red CELLS fit the geometric room.

    Red is invisible to Aisha, so its per-vault count comes from the flat map prior and over-states a
    tight low-red vault (eval: low-red +424k). The room = a RED-INDEPENDENT capacity (layout geometry
    total_grid_target, NOT the combo total_grid which already bakes in red -> circular) minus the
    estimated non-red cells. Combos whose red overshoots min(room, map-tier expected red x slack) are
    exponentially down-weighted, trimming the false-positive red without a hard cut. The map-tier
    factor keeps low-tier maps (P(q6) tiny -> empty space is NOT red) from reading空 as red room.

    Strictly isolated: aisha only, pure-prior red only (never touches a revealed/locked red -> keeps
    _is_pure_prior_red input unchanged, so the value cap stays armed), decision rounds only. NEVER
    writes evidence. Rollback: BIDKING_DISABLE_AISHA_RED_ROOM=1.
    """
    if str(hero_key or "").strip().lower() != "aisha":
        return combos
    if os.environ.get("BIDKING_DISABLE_AISHA_RED_ROOM") == "1":
        return combos
    if not combos or not _is_pure_prior_red(evidence):
        return combos
    if round_no is None or int(round_no) < _AISHA_RED_ROOM_MIN_ROUND:
        return combos
    if geom_capacity is None or float(geom_capacity) <= 0:
        return combos
    max_logw = max(combo.weight for combo in combos)
    weight_of = lambda combo: math.exp(max(-745.0, combo.weight - max_logw))  # noqa: E731
    non_red = _weighted_quantile(
        [
            (sum(float(combo.grids.get(key, 0.0)) for key in QUALITY_KEYS if key != "q6"), weight_of(combo))
            for combo in combos
        ],
        0.5,
    )
    if non_red is None:
        return combos
    room = float(geom_capacity) - float(non_red)
    if room < _AISHA_RED_ROOM_MIN_ROOM:
        return combos
    if room > _AISHA_RED_ROOM_TIGHT_MAX:
        # Large room = a deep/rich vault where the geometric signal is reliable and red genuinely
        # fills space (high-red bucket, accurate at baseline). Only tight vaults (low-red's signature)
        # carry the false-positive red worth trimming -> don't touch large-room vaults (no high-red hit).
        return combos
    total_med = _weighted_quantile([(float(sum(combo.counts.values())), weight_of(combo)) for combo in combos], 0.5) or 0.0
    p6 = max(1e-9, float(probs.get("q6", 0.0)))
    tier_red_cells = total_med * p6 * _AISHA_RED_ROOM_RED_VOL * _AISHA_RED_ROOM_TIER_SLACK
    red_cell_ceiling = min(room, tier_red_cells)
    changed = False
    out: list[RefCombo] = []
    for combo in combos:
        over = float(combo.grids.get("q6", 0.0)) - red_cell_ceiling
        if over > 0:
            changed = True
            out.append(replace(combo, weight=combo.weight - _AISHA_RED_ROOM_PENALTY * over))
        else:
            out.append(combo)
    if changed:
        _append_source_note_once(
            notes, f"{AISHA_RED_ROOM_REWEIGHT_NOTE_PREFIX}cap{int(round(red_cell_ceiling))}_room{int(round(room))}"
        )
    return out


def _high_tier_floor_extra(
    snapshot: dict[str, Any],
    evidence: "RefEvidence",
    notes: list[str],
) -> tuple[float, int]:
    """Residual count-anchor correction for a publicly-revealed LARGE high-tier item.

    A revealed big q5/q6 item (e.g. a 16-cell red car from 巨物抽样) contributes a public
    `cell_floor` that is NOT an exact pin. The residual count-anchor below only subtracts
    `_exact_integer_quality_cell` tiers from the grid before dividing by the per-item average, so
    the big item's cells stay in the residual and get counted as ~floor/per_item PHANTOM items (a
    16-cell red -> ~6 items) -> total count inflated -> q5/q6 over-packed -> combos collapse to 3
    -> band collapses + red value locked high (observed: 2405 R3, total 49 vs truth ~43).

    Fix: deduct that floor from the residual and refill the tier's `min_count` (the big item is ~1
    item, not floor/per_item). Returns (cells_to_deduct, min_items_to_refill).

    GATED to the bug trigger so R4 / normal samples stay byte-identical: fires only when q5 cells
    are UNKNOWN (not exact-pinned) -- once q5 cells are pinned the residual already absorbs the
    high-tier cells correctly and deducting again would over-shrink. Default ON; rollback
    AISHA_DISABLE_Q5FIX=1.
    """
    if os.environ.get("AISHA_DISABLE_Q5FIX") == "1":
        return 0.0, 0
    # Gate: q5 cells known (e.g. R4 pins q5=14) -> residual already correct, do not double-deduct.
    if _exact_integer_quality_cell(evidence.quality_cells.get("q5")) is not None:
        return 0.0, 0
    try:
        _counts, cell_floors, _value_floors = _public_quality_reveal_floors(snapshot)
    except Exception:
        return 0.0, 0
    extra_cells = 0.0
    extra_items = 0
    for tier in ("q5", "q6"):
        floor = cell_floors.get(tier)
        if not floor or float(floor) <= 0:
            continue
        if _exact_integer_quality_cell(evidence.quality_cells.get(tier)) is not None:
            continue  # this tier's cells are pinned -> already correctly subtracted
        min_items = int(evidence.min_counts.get(tier, 1) or 1)
        extra_cells += float(floor)
        extra_items += min_items
        notes.append(f"aisha_q5fix_hightier_floor:{tier}:{int(float(floor))}cells+{min_items}item")
    return extra_cells, extra_items


def run_reference_engine(
    snapshot: dict[str, Any],
    *,
    static_data: dict[str, Any] | None = None,
    safety_factor: float = 0.85,
    max_combos: int = 50000,
    _allow_public_avg_fallback: bool = True,
    _allow_high_tier_grid_fallback: bool = True,
) -> RefResult:
    static_data = static_data or load_reference_static_data()
    evidence = extract_evidence(snapshot)
    notes = list(evidence.source_notes)
    early_round_lightweight = bool(snapshot.get("audit_aisha_early_round"))
    engine_pass = str(snapshot.get("audit_aisha_engine_pass") or "").strip()
    if early_round_lightweight:
        max_combos = min(max_combos, AISHA_EARLY_ROUND_MAX_COMBOS)
        _append_source_note_once(notes, AISHA_EARLY_ROUND_LIGHTWEIGHT_NOTE)
    if engine_pass in {AISHA_ENGINE_PASS_SKILL, AISHA_ENGINE_PASS_ITEM}:
        _append_source_note_once(notes, f"aisha_engine_pass:{engine_pass}")
    if not is_supported_ref_hero(evidence.hero):
        return RefResult(
            status="not_structured_hero",
            source="ref_v0",
            conservative=None,
            balanced=None,
            aggressive=None,
            value_p25=None,
            value_p50=None,
            value_p75=None,
            combo_count=0,
            red_count_range=(None, None, None),
            red_cells_range=(None, None, None),
            red_value_range=(None, None, None),
            quality_count_ranges={},
            quality_cells_ranges={},
            total_grid_range=(None, None, None),
            notes=("current_hero_not_supported",),
            evidence=evidence,
        )
    hero_key = normalize_hero_key(evidence.hero)
    if hero_key and hero_key not in STRUCTURED_REF_HERO_KEYS:
        notes.append("generic_ref_hero")

    if any(note.startswith("hard_conflict:") for note in notes):
        return RefResult(
            status="no_reachable_combo",
            source="ref_v0",
            conservative=None,
            balanced=None,
            aggressive=None,
            value_p25=None,
            value_p50=None,
            value_p75=None,
            combo_count=0,
            red_count_range=(None, None, None),
            red_cells_range=(None, None, None),
            red_value_range=(None, None, None),
            quality_count_ranges={},
            quality_cells_ranges={},
            total_grid_range=(None, None, None),
            notes=tuple(dict.fromkeys(notes + ["constraints_conflict_or_too_strict"])),
            evidence=evidence,
        )

    item_values, price_note = _quality_item_values(evidence.map_id, static_data)
    probs, prob_note = _quality_probabilities(evidence.map_id, static_data)
    notes.extend([price_note, prob_note])
    random_floor = _random_value_floor(evidence)
    if random_floor is not None:
        notes.append(f"random_value_floor_soft_weight:{int(round(random_floor))}")
    # Minimap depth is independent of count, so compute it BEFORE the count candidates: combined
    # with the public all-items avg-cells it anchors count = grid / avg (the grid<->count cross-check).
    snapshot_round = _snapshot_round_no(snapshot)
    minimap_items = _minimap_items_from_snapshot(snapshot)
    raw_deepest = _deepest_minimap_bottom_row(minimap_items)
    known_minimap_cells = _known_minimap_cells(minimap_items)
    # Anchor the count to the OBSERVED minimap grid (count ~= grid / per-item avg-cells), not just
    # the weak map-default prior. The minimap depth is a strong count-independent signal; without it
    # the count systematically under-estimates (live 2409 R3: prior ~28 vs truth 46). Two flavours:
    #   - public total_avg_cells present -> count = grid / total_avg (most accurate cross-check), or
    #   - else use the per-quality residual per-item avg (generalised grid->count).
    # Only LIFTS toward the observed grid (max with the default center downstream); count-locked /
    # no-minimap heroes are untouched.
    avg_cells_count_hint: int | None = None
    avg_cells_count_hint_public = False  # True 仅当 hint = 公开均格(total_avg_cells)推得; 残差估计=False
    total_avg_cells = evidence.avg_cells.get("total")
    if (
        evidence.total_count is None
        and raw_deepest
        and raw_deepest > 0
        and _non_total_count_evidence_strength(evidence) >= 2
    ):
        rect_depth_grid = int(raw_deepest) * AISHA_GRID_COLUMNS
        # Lone-deep discount: a big item alone in a deep cell with no neighbours supporting that depth
        # makes raw_deepest*columns over-state the grid (~+10 cells; user-flagged on 2402/2410).
        # Re-anchor to the supported depth once the vault is mostly revealed. The reveal-completeness
        # gate (>=45% of the rectangle known) is the real guard against early sparse views; the round
        # floor only blocks R1. Small vaults reveal most by R2 (2410 R2 completeness 0.51) and need the
        # discount on the COUNT anchor, while normal early vaults (R2 ~0.31) fall below the gate.
        lone_deep_min_round = 2
        try:
            lone_deep_min_round = int(os.environ.get("AISHA_LONE_DEEP_MIN_ROUND", lone_deep_min_round))
        except (TypeError, ValueError):
            pass
        if (
            snapshot_round is not None
            and int(snapshot_round) >= lone_deep_min_round
            and rect_depth_grid > 0
            and (known_minimap_cells or 0) / rect_depth_grid >= 0.45
        ):
            adjusted = _occupancy_adjusted_depth_grid(minimap_items, int(raw_deepest))
            if adjusted < rect_depth_grid:
                notes.append(f"aisha_lone_deep_depth_discount:{rect_depth_grid}->{adjusted}")
                rect_depth_grid = adjusted
        depth_grid = max(int(known_minimap_cells or 0), rect_depth_grid)
        if total_avg_cells and total_avg_cells > 0:
            if depth_grid > 0:
                avg_cells_count_hint = int(round(depth_grid / float(total_avg_cells)))
                avg_cells_count_hint_public = True  # 来自公开均格 → 允许双向 conform
        else:
            per_item = _residual_per_item_cell_estimate(
                fixed_counts=evidence.fixed_counts,
                avg_cells=evidence.avg_cells,
                source_notes=notes,
                quality_probs=probs,
                fallback=COUNT_ANCHOR_RESIDUAL_ITEM_CELL_ESTIMATE,
            )
            if per_item > 0 and depth_grid > 0:
                known_q_cells = sum(
                    _exact_integer_quality_cell(evidence.quality_cells.get(k)) or 0
                    for k in QUALITY_KEYS
                )
                fixed_sum = sum(max(0, int(v)) for v in evidence.fixed_counts.values())
                # A publicly-revealed LARGE high-tier item (e.g. a 16-cell red car) leaves a
                # cell_floor that isn't exact-pinned, so its big cells would otherwise be divided by
                # the per-item average into ~floor/per_item phantom items. Deduct that floor and
                # refill it as ~1 item. Gated to q5-cells-unknown (the bug trigger); R4/normal
                # samples untouched. Rollback AISHA_DISABLE_Q5FIX=1. (2405 R3: hint 49 -> 44)
                _q5fix_cells, _q5fix_items = _high_tier_floor_extra(snapshot, evidence, notes)
                residual_items = int(
                    round(max(0.0, depth_grid - known_q_cells - _q5fix_cells) / per_item)
                )
                avg_cells_count_hint = fixed_sum + residual_items + _q5fix_items
        # Richness lift (experiment, env-toggleable AISHA_DISABLE_RICHNESS_LIFT=1): once a low/mid
        # tier is LOCKED (e.g. blue q3 at R3) and exceeds its prior expectation, the vault is rich
        # and the grid alone may under-state the total on a scattered layout. Lift toward the
        # richness-implied total = locked_low / P(low), blended 50% and capped at 1.25x so a
        # high-low-tier vault can't over-count. Helps the mid-round high-tier (purple/gold) estimate
        # which is otherwise total-limited (2402 R3: hint 34 -> 38 == truth).
        if avg_cells_count_hint and os.environ.get("AISHA_DISABLE_RICHNESS_LIFT") != "1":
            locked_low = sum(int(evidence.fixed_counts.get(k, 0) or 0) for k in ("q1", "q3"))
            p_low = float(probs.get("q1", 0.0)) + float(probs.get("q3", 0.0))
            if locked_low > 0 and p_low > 0.05:
                richness_total = locked_low / p_low
                base = int(avg_cells_count_hint)
                if richness_total > base:
                    blended = int(round(base + 0.5 * (richness_total - base)))
                    lifted = min(blended, int(round(base * 1.25)))
                    if lifted > base:
                        avg_cells_count_hint = lifted
                        notes.append(f"aisha_richness_total_lift:{lifted}")
    total_candidates, total_prior_center = _total_count_candidates(
        evidence,
        notes,
        early_round_lightweight=early_round_lightweight,
        avg_cells_count_hint=avg_cells_count_hint,
        avg_cells_count_hint_public=avg_cells_count_hint_public,
        round_no=snapshot_round,
        probs=probs,
    )
    # Count→grid floor helps early live rounds (e.g. R1 total grid too low) but shifts
    # R3+ fatbeans regression baselines; keep it on R1–R2 until v0.1.9 audit closes.
    count_for_grid_floor = evidence.total_count if evidence.total_count is not None else total_prior_center
    if (
        count_for_grid_floor is not None
        and snapshot_round is not None
        and int(snapshot_round) <= 2
    ):
        apply_count_implied_grid_floor(
            hero_key=hero_key,
            total_count=int(count_for_grid_floor),
            total_grid_target=evidence.total_grid_target,
            source_notes=notes,
            round_no=int(snapshot_round),
            raw_deepest=raw_deepest,
        )
    exact_total_avg_fast_path = _should_use_exact_total_avg_cells_fast_path(evidence)
    if not total_candidates:
        return RefResult(
            status="missing_total_count",
            source="ref_v0",
            conservative=None,
            balanced=None,
            aggressive=None,
            value_p25=None,
            value_p50=None,
            value_p75=None,
            combo_count=0,
            red_count_range=(None, None, None),
            red_cells_range=(None, None, None),
            red_value_range=(None, None, None),
            quality_count_ranges={},
            quality_cells_ranges={},
            total_grid_range=(None, None, None),
            notes=tuple(notes + ["waiting_total_count"]),
            evidence=evidence,
        )

    combos: list[RefCombo] = []
    quality_value_soft_weight_applied = False
    public_avg_value_soft_weight_applied = False
    q3_min = evidence.fixed_counts.get("q3", evidence.min_counts.get("q3", 0))
    q4_min = evidence.fixed_counts.get("q4", evidence.min_counts.get("q4", 0))
    q5_min = evidence.fixed_counts.get("q5", evidence.min_counts.get("q5", 0))
    q6_min = evidence.fixed_counts.get("q6", evidence.min_counts.get("q6", 0))

    if total_prior_center is not None:
        if _sparse_exact_high_total_tight_prior(evidence):
            notes.append("sparse_exact_high_total_tight_prior")
        for total in total_candidates:
            prior_combos = _enumerate_prior_count_combos(
                total,
                evidence=evidence,
                probs=probs,
                total_prior_center=total_prior_center,
                max_new=max_combos - len(combos),
                item_values=item_values,
            )
            for combo in prior_combos:
                value = _combo_value(combo.counts, combo.grids, item_values, evidence)
                quality_value_weight, quality_value_weight_applied = _quality_exact_value_log_weight(
                    combo.counts,
                    combo.grids,
                    item_values,
                    evidence,
                )
                public_avg_value_weight, public_avg_value_weight_applied = _soft_public_avg_value_log_weight(
                    combo.counts,
                    combo.grids,
                    evidence,
                    item_values=item_values,
                )
                quality_value_soft_weight_applied = (
                    quality_value_soft_weight_applied or quality_value_weight_applied
                )
                public_avg_value_soft_weight_applied = (
                    public_avg_value_soft_weight_applied or public_avg_value_weight_applied
                )
                combos.append(
                    RefCombo(
                        counts=combo.counts,
                        grids=combo.grids,
                        value=value,
                        weight=combo.weight
                        + quality_value_weight
                        + public_avg_value_weight
                        + _random_value_floor_log_weight(value, evidence),
                        total_grid=combo.total_grid,
                    )
                )
            if len(combos) >= max_combos:
                notes.append("combo_cap_hit")
                break
        if combos:
            notes.append("count_prior_enumerated")
            notes.append("grid_conditioned_value_v1")
    else:
        for total in total_candidates:
            for q1 in _count_values(total, "q1", evidence, q3_min + q4_min + q5_min + q6_min):
                remaining_after_q1 = total - q1
                for q3 in _count_values(remaining_after_q1, "q3", evidence, q4_min + q5_min + q6_min):
                    remaining_after_q3 = remaining_after_q1 - q3
                    for q4 in _count_values(remaining_after_q3, "q4", evidence, q5_min + q6_min):
                        remaining_after_q4 = remaining_after_q3 - q4
                        for q5 in _count_values(remaining_after_q4, "q5", evidence, q6_min):
                            q6 = remaining_after_q4 - q5
                            if (
                                "q4q5" in evidence.count_sums
                                and q4 + q5 != evidence.count_sums["q4q5"]
                            ):
                                continue
                            if (
                                "q4q5q6" in evidence.count_sums
                                and q4 + q5 + q6 != evidence.count_sums["q4q5q6"]
                            ):
                                continue
                            if q6 < _effective_min_count("q6", evidence):
                                continue
                            fixed_q6 = evidence.fixed_counts.get("q6")
                            if fixed_q6 is not None and q6 != fixed_q6:
                                continue
                            if not _quality_count_matches_value_evidence("q6", q6, evidence):
                                continue
                            counts = {"q1": q1, "q3": q3, "q4": q4, "q5": q5, "q6": q6}
                            grids = _grids_for_counts(counts, evidence)
                            if grids is None:
                                continue
                            total_grid = sum(grids.values())
                            logw = _log_fact(total)
                            for key in QUALITY_KEYS:
                                count = counts[key]
                                logw -= _log_fact(count)
                                if count:
                                    logw += count * math.log(probs[key])
                            logw += _known_cells_count_bias_log(counts, evidence)
                            logw += _evidence_grid_avg_cells_log_penalties(
                                counts,
                                grids,
                                evidence,
                            )
                            value = _combo_value(counts, grids, item_values, evidence)
                            quality_value_weight, quality_value_weight_applied = _quality_exact_value_log_weight(
                                counts,
                                grids,
                                item_values,
                                evidence,
                            )
                            public_avg_value_weight, public_avg_value_weight_applied = (
                                _soft_public_avg_value_log_weight(
                                    counts,
                                    grids,
                                    evidence,
                                    item_values=item_values,
                                )
                            )
                            quality_value_soft_weight_applied = (
                                quality_value_soft_weight_applied or quality_value_weight_applied
                            )
                            public_avg_value_soft_weight_applied = (
                                public_avg_value_soft_weight_applied
                                or public_avg_value_weight_applied
                            )
                            combos.append(
                                RefCombo(
                                    counts=counts,
                                    grids=grids,
                                    value=value,
                                    weight=logw
                                    + quality_value_weight
                                    + public_avg_value_weight
                                    + _random_value_floor_log_weight(value, evidence),
                                    total_grid=total_grid,
                                )
                            )
                            if len(combos) >= max_combos:
                                notes.append("combo_cap_hit")
                                break
                        if len(combos) >= max_combos:
                            break
                    if len(combos) >= max_combos:
                        break
                if len(combos) >= max_combos:
                    break
            if len(combos) >= max_combos:
                break
    if exact_total_avg_fast_path and combos:
        notes.append("exact_total_avg_cells_fast_path")
    if quality_value_soft_weight_applied:
        notes.append("quality_value_soft_weight_v0")
    if public_avg_value_soft_weight_applied:
        notes.append("public_avg_value_soft_weight_v0")

    if not combos:
        # The derived high-tier-cell grid target can sit below the physical minimum (it misses
        # public-reveal cell floors / residual avg_cells) and reject every combo, leaving the
        # decision round with no quote. Relax it first — it is a heuristic, not ground truth.
        if _allow_high_tier_grid_fallback and TOTAL_GRID_FROM_HIGH_TIER_CELLS_NOTE in notes:
            fallback = _retry_without_high_tier_grid_target(
                snapshot,
                static_data=static_data,
                safety_factor=safety_factor,
                max_combos=max_combos,
            )
            if fallback is not None:
                return fallback
        public_avg_value_notes = _public_quality_avg_value_notes(notes)
        public_avg_cell_notes = _public_quality_avg_cells_notes(notes)
        if _allow_public_avg_fallback and (public_avg_value_notes or public_avg_cell_notes):
            fallback = _retry_without_public_avg_constraints(
                snapshot,
                public_avg_value_notes=public_avg_value_notes,
                public_avg_cell_notes=public_avg_cell_notes,
                static_data=static_data,
                safety_factor=safety_factor,
                max_combos=max_combos,
            )
            if fallback is not None:
                return fallback
        return RefResult(
            status="no_reachable_combo",
            source="ref_v0",
            conservative=None,
            balanced=None,
            aggressive=None,
            value_p25=None,
            value_p50=None,
            value_p75=None,
            combo_count=0,
            red_count_range=(None, None, None),
            red_cells_range=(None, None, None),
            red_value_range=(None, None, None),
            quality_count_ranges={},
            quality_cells_ranges={},
            total_grid_range=(None, None, None),
            notes=tuple(notes + ["constraints_conflict_or_too_strict"]),
            evidence=evidence,
        )

    # Aisha pure-prior red: soft-reweight combos so an invisible red can't overshoot the geometric
    # room (red-independent capacity - non-red cells). Isolated + flag-gated; no-op for other heroes,
    # revealed/locked red, or early rounds. Capacity = layout geometry (NOT post-combo grid -> circular).
    combos = _aisha_red_room_reweighted_combos(
        combos,
        hero_key=hero_key,
        round_no=snapshot_round,
        evidence=evidence,
        probs=probs,
        geom_capacity=(
            float(evidence.total_grid_target)
            if evidence.total_grid_target
            else (float(_occupancy_adjusted_depth_grid(minimap_items, raw_deepest)) if raw_deepest else None)
        ),
        notes=notes,
    )

    max_logw = max(combo.weight for combo in combos)
    has_intra_quality_value_band = False
    weighted_values: list[tuple[float, float]] = []
    for combo in combos:
        combo_weight = math.exp(max(-745.0, combo.weight - max_logw))
        value_spread = _combo_value_uncertainty(
            combo.counts,
            combo.grids,
            item_values,
            evidence,
        )
        if value_spread >= 1.0:
            has_intra_quality_value_band = True
        for value, point_weight in _combo_value_distribution_points(
            combo.value,
            value_spread,
            evidence,
            counts=combo.counts,
            grids=combo.grids,
            item_values=item_values,
        ):
            weighted_values.append((value, combo_weight * point_weight))
    weighted_red = [
        (float(combo.counts["q6"]), math.exp(max(-745.0, combo.weight - max_logw)))
        for combo in combos
    ]
    weighted_red_cells: list[tuple[float, float]] = []
    weighted_red_value: list[tuple[float, float]] = []
    for combo in combos:
        combo_weight = math.exp(max(-745.0, combo.weight - max_logw))
        red_options = _display_grid_options_for_quality(combo.counts, evidence, "q6")
        option_weight = combo_weight / max(1, len(red_options))
        for red_grid in red_options:
            weighted_red_cells.append((float(red_grid), option_weight))
            red_value = _quality_value_for_evidence(
                "q6",
                count=combo.counts["q6"],
                grid=float(red_grid),
                item_values=item_values,
                evidence=evidence,
            )
            red_value_spread = _quality_value_uncertainty(
                "q6",
                count=combo.counts["q6"],
                grid=float(red_grid),
                item_values=item_values,
                evidence=evidence,
            )
            for value, point_weight in _quality_value_distribution_points(
                "q6",
                center=red_value,
                spread=red_value_spread,
                count=combo.counts["q6"],
                grid=float(red_grid),
                item_values=item_values,
                evidence=evidence,
            ):
                weighted_red_value.append((value, option_weight * point_weight))
    if has_intra_quality_value_band:
        notes.append("intra_quality_value_band_v0")
    # Grid range: by default the single combo.total_grid collapses when N hidden gold/red items each
    # take a constant mean footprint. Replace the unpinned gold/red footprint with a per-item SIZE
    # range (symmetric, so the p50/reference stays at the mean) so the band reflects that a hidden
    # gold/red could be small or big -- fixes the collapsed grid (2501 R4 [132,132,132]). Default ON;
    # rollback AISHA_DISABLE_HIDDEN_HIGHTIER_GRID_SPREAD=1. Only touches the grid range (not red/value).
    # Skip the spread when the total grid is GENUINELY known (public total_cells / settlement / field
    # update) -- then the grid is determined and must stay hard. Only spread when the total is merely
    # INFERRED from high-tier cells (the case the spread fixes: 2501).
    _genuine_hard_total = any(
        note in (HARD_TOTAL_GRID_SOURCE_NOTES - {TOTAL_GRID_FROM_HIGH_TIER_CELLS_NOTE})
        for note in evidence.source_notes
    )
    _hidden_ht_keys = (
        []
        if (os.environ.get(AISHA_HIDDEN_HT_GRID_SPREAD_DISABLE) == "1" or _genuine_hard_total)
        else _cell_unknown_hightier_keys(evidence)
    )
    if _hidden_ht_keys:
        weighted_grid = []
        for combo in combos:
            combo_w = math.exp(max(-745.0, combo.weight - max_logw))
            base = float(combo.total_grid) - sum(float(combo.grids.get(k, 0.0)) for k in _hidden_ht_keys)
            opt_lists = [
                _hidden_hightier_cell_options(k, int(combo.counts[k]), evidence, center_cells=float(combo.grids.get(k, 0.0)))
                for k in _hidden_ht_keys
            ]
            stack = [(0.0, 1.0)]
            for opts in opt_lists:
                stack = [(cells + o, weight * w) for (cells, weight) in stack for (o, w) in opts]
            for cells, prior_w in stack:
                weighted_grid.append((base + cells, combo_w * prior_w))
    else:
        weighted_grid = [
            (combo.total_grid, math.exp(max(-745.0, combo.weight - max_logw)))
            for combo in combos
        ]
    quality_count_ranges: dict[str, tuple[int | None, int | None, int | None]] = {}
    quality_cells_ranges: dict[str, tuple[int | None, int | None, int | None]] = {}
    for key in QUALITY_KEYS:
        weighted_count = [
            (float(combo.counts[key]), math.exp(max(-745.0, combo.weight - max_logw)))
            for combo in combos
        ]
        weighted_cells: list[tuple[float, float]] = []
        for combo in combos:
            combo_weight = math.exp(max(-745.0, combo.weight - max_logw))
            options = _display_grid_options_for_quality(combo.counts, evidence, key)
            option_weight = combo_weight / max(1, len(options))
            weighted_cells.extend((float(option), option_weight) for option in options)
        count_q = tuple(_weighted_quantile(weighted_count, q) for q in (0.10, 0.50, 0.90))
        cells_q = tuple(_weighted_quantile(weighted_cells, q) for q in (0.10, 0.50, 0.90))
        quality_count_ranges[key] = tuple(
            int(round(value)) if value is not None else None for value in count_q
        )  # type: ignore[assignment]
        quality_cells_ranges[key] = tuple(
            int(round(value)) if value is not None else None for value in cells_q
        )  # type: ignore[assignment]
    p25 = _weighted_quantile(weighted_values, 0.25)
    p50 = _weighted_quantile(weighted_values, 0.50)
    p75 = _weighted_quantile(weighted_values, 0.75)
    p90 = _weighted_quantile(weighted_values, 0.90)
    # Per-hero value re-centring (sophie over-values, see DRAW_HERO_VALUE_SCALE). Scales the
    # value percentiles that feed all three tiers + the displayed value. Aisha/ahmed = 1.0.
    _value_scale = _hero_value_scale(evidence.hero)
    if _value_scale != 1.0:
        p25 = p25 * _value_scale if p25 is not None else None
        p50 = p50 * _value_scale if p50 is not None else None
        p75 = p75 * _value_scale if p75 is not None else None
        p90 = p90 * _value_scale if p90 is not None else None
        _append_source_note_once(notes, f"hero_value_scale:{_value_scale:g}")
    # NOTE: the random-N value floor is NOT applied here as a flat clamp on the percentiles. A flat
    # clamp pins p25=p50=p75=floor and hugs it even when more reds are implied (the catalog
    # under-values jackpot reds). Instead the high signal is bound to RED value at evidence time
    # (see random_avg_red_anchor below): the floor manifests as an expensive red, and the count
    # prior adds the warehouse's other reds on top, so the value distribution floats above the
    # floor with spread. Monte-Carlo (scripts/simulate_random_avg_red_cause.py, 956k draws): a
    # high random-N avg is caused by a red in the draw (P~=1.0 at >=5w) carrying 97-99% of the
    # draw value, while the warehouse holds 3-4 reds total.
    r10 = _weighted_quantile(weighted_red, 0.10)
    r50 = _weighted_quantile(weighted_red, 0.50)
    r90 = _weighted_quantile(weighted_red, 0.90)
    rc10 = _weighted_quantile(weighted_red_cells, 0.10)
    rc50 = _weighted_quantile(weighted_red_cells, 0.50)
    rc90 = _weighted_quantile(weighted_red_cells, 0.90)
    rv10 = _weighted_quantile(weighted_red_value, 0.10)
    rv50 = _weighted_quantile(weighted_red_value, 0.50)
    rv90 = _weighted_quantile(weighted_red_value, 0.90)
    g10 = _weighted_quantile(weighted_grid, 0.10)
    g50 = _weighted_quantile(weighted_grid, 0.50)
    g90 = _weighted_quantile(weighted_grid, 0.90)
    # Soft center-shift toward the full-row geometric value: it's a reasonable estimate to FLOAT around,
    # not a hard floor (the enclosed empties above a full row may themselves be gaps, not items). When
    # the count grid sits below it, nudge the whole band up by a fraction of the gap (keeping the spread
    # for up/down float); when the count grid already meets/exceeds it, no shift -> over-estimate samples
    # are untouched. Only with the hidden-high-tier cell spread ON.
    if _hidden_ht_keys and g50 is not None:
        geom_center = _deepest_full_row_grid_floor(minimap_items)
        if geom_center > g50:
            offset = _AISHA_GEOM_CENTER_PULL * (geom_center - g50)
            # Pull the center (mid) and the aggressive (high) up toward the geometric value, but keep
            # the conservative (low) at its natural spread value so the band can still FLOAT DOWN and
            # bracket a lower truth (the geom value is a soft center, not a floor -- 2501 vs a sample
            # whose truth sits below the full-row value when the enclosed empties are real gaps).
            g50 += offset
            g90 = (g90 if g90 is not None else g50) + offset
    total_grid_range = _apply_aisha_layout_band_widen_to_range(
        (
            int(round(g10)) if g10 is not None else None,
            int(round(g50)) if g50 is not None else None,
            int(round(g90)) if g90 is not None else None,
        ),
        notes,
    )
    total_grid_range = apply_layout_footroom_band_range_anchor(
        total_grid_range,
        hero_key=hero_key,
        round_no=snapshot_round,
        known_cells=known_minimap_cells,
        raw_deepest=raw_deepest,
        source_notes=notes,
    )
    total_grid_range = apply_count_implied_grid_range_floor(
        total_grid_range,
        notes,
        raw_deepest=raw_deepest,
        round_no=snapshot_round,
        known_cells=known_minimap_cells,
    )
    total_grid_range = apply_shallow_visible_grid_low_floor(
        total_grid_range,
        hero_key=hero_key,
        round_no=snapshot_round,
        raw_deepest=raw_deepest,
        known_cells=known_minimap_cells,
        source_notes=notes,
    )
    total_grid_range = apply_grid_range_spread_cap(
        total_grid_range,
        hero_key=hero_key,
        items=minimap_items,
        raw_deepest=raw_deepest,
        source_notes=notes,
    )
    total_grid_range = apply_deep_visibility_grid_range_anchor(
        total_grid_range,
        notes,
        hero_key=hero_key,
        round_no=snapshot_round,
        raw_deepest=raw_deepest,
        known_cells=known_minimap_cells,
        items=minimap_items,
    )
    total_grid_range = ensure_aisha_floating_grid_minimum_spread(
        total_grid_range,
        notes,
        hero_key=hero_key,
        round_no=snapshot_round,
        raw_deepest=raw_deepest,
    )
    total_grid_range = apply_shallow_flat_bottom_grid_range_tighten(
        total_grid_range,
        notes,
        hero_key=hero_key,
        round_no=snapshot_round,
        raw_deepest=raw_deepest,
        known_cells=known_minimap_cells,
        items=minimap_items,
    )

    # Geometry-first grid pass runs last and only on the reported grid range (value p50 above
    # is computed from the pre-geometry grid, so item count/value path is untouched).
    total_grid_range = apply_aisha_geometry_grid_range(
        total_grid_range,
        notes,
        hero_key=hero_key,
        round_no=snapshot_round,
        raw_deepest=raw_deepest,
        known_cells=known_minimap_cells,
        items=minimap_items,
    )

    # Draw heroes (sophie/gabriela/raven): anchor grid to the VISIBLE deepest row each round so the
    # per-round estimate tracks what the player sees (games may end before R4; early-round refs matter).
    total_grid_range = apply_draw_visible_anchor_grid_range(
        total_grid_range,
        notes,
        hero_key=hero_key,
        raw_deepest=raw_deepest,
        known_cells=known_minimap_cells,
    )

    # Final upper bound: once most of the grid is already visible (near-complete shallow reveal),
    # the grid can't exceed seen cells + cells of items still hidden. Trims the footroom over-lift
    # on a partially-filled deepest row (live R4) while leaving deep/mid-game warehouses untouched.
    total_grid_range = apply_late_reveal_grid_residual_cap(
        total_grid_range,
        notes,
        hero_key=hero_key,
        round_no=snapshot_round,
        raw_deepest=raw_deepest,
        known_cells=known_minimap_cells,
        count_estimate=count_for_grid_floor,
        items=minimap_items,
    )

    # Grid-primary anchor (UP-ONLY): the occupancy-adjusted geometric grid (occ-adj) is more accurate &
    # unbiased than the count-derived grid; the count loop SWINGS R3-over / R4-under (2409 t106: R3 113
    # over, R4 99 under). LIFT the displayed grid toward occ-adj when occ-adj is HIGHER (fixes the R4
    # under-estimate / price drop, e.g. 2409 R4 99->~104 toward truth 106). We do NOT pull DOWN toward
    # occ-adj lift: DEFAULT-OFF since 2026-06-20. Originally added (dada661) to fix an apparent "R4
    # under-estimate" (2409 99->104), but that under-estimate was an artifact of LONG-TAIL RED inflating
    # the settlement truth (e.g. 富春山居图 1.24M vs 古剑 0.14M) + mixed bad-truth samples. After cleaning
    # the sample library (valid-only) and normalizing long-tail reds to same-shape expected value, the
    # "under" disappears and this lift only OVER-states grid on the 2 samples it still touches (net -12k,
    # both worsened). Its design premise was a polluted-baseline mirage. Code kept; re-enable only with a
    # re-validated premise via AISHA_ENABLE_GRID_OCC_ANCHOR=1. See memory long-tail-red-contaminates-truth.
    if (
        hero_key == "aisha"
        and raw_deepest
        and os.environ.get("AISHA_ENABLE_GRID_OCC_ANCHOR")
    ):
        _occ = _occupancy_adjusted_depth_grid(minimap_items, int(raw_deepest))
        _frac = float(os.environ.get("AISHA_GRID_OCC_ANCHOR_FRAC") or 0.5)
        _g = total_grid_range
        # Gate to HIGH reveal (known/mid >= 0.75): the under-estimate this fixes is a near-full-reveal
        # phenomenon (R4 late-reveal cap too tight); at low reveal (R3 early) the grid leans OVER, so
        # lifting there would worsen the over. So only lift when most of the vault is already seen.
        _reveal = (float(known_minimap_cells or 0) / float(_g[1])) if (_g and _g[1]) else 0.0
        if _occ > 0 and _frac > 0 and _g and _g[1] is not None and _occ > int(_g[1]) and _reveal >= 0.75:
            _mid0 = int(_g[1])
            _mid1 = int(round(_mid0 + _frac * (float(_occ) - _mid0)))
            if _mid1 != _mid0:
                _shift = _mid1 - _mid0
                total_grid_range = (
                    max(0, int(_g[0] or _mid0) + _shift) if _g[0] is not None else None,
                    _mid1,
                    max(_mid1, int(_g[2] or _mid0) + _shift),
                )
                notes.append(f"aisha_grid_occ_anchor:{_mid0}->{_mid1}(occ={_occ})")

    # Hard floor: the total grid can never be below the cells already KNOWN to be locked -- including
    # premium-scan (极品扫描) gold/red cells that lock quality_cells but are NOT in the minimap. User R4
    # (2401): the scan locked 14 gold cells (q5) yet known_minimap had only 1, so the late-reveal cap
    # floored grid at the 65-based 68, BELOW the 78 locked cells (truth 79). Floor every tier at the
    # locked quality_cells sum so the displayed grid never drops below the visible/locked footprint.
    # Locked cells are revealed-exact (<= truth), so this is always a safe lower bound. Reversible:
    # AISHA_DISABLE_LOCKED_CELLS_GRID_FLOOR=1.
    if hero_key == "aisha" and not os.environ.get("AISHA_DISABLE_LOCKED_CELLS_GRID_FLOOR"):
        _locked_cells = int(sum(
            float(v) for v in evidence.quality_cells.values()
            if isinstance(v, (int, float)) and float(v) > 0
        ))
        _g = total_grid_range
        if _locked_cells > 0 and _g and _g[1] is not None and int(_g[1]) < _locked_cells:
            total_grid_range = (
                max(int(_g[0] or 0), _locked_cells),
                max(int(_g[1]), _locked_cells),
                max(int(_g[2] or 0), _locked_cells),
            )
            notes.append(f"aisha_locked_cells_grid_floor:{_locked_cells}")

    conservative_mul, balanced_mul, aggressive_mul = quote_safety_multipliers(safety_factor)
    _append_source_note_once(notes, REF_QUOTE_SAFETY_TIER_NOTE)

    # Tier sources. AISHA-ONLY: balanced slides p50->p75 by observed cell-richness (reds invisible,
    # but total_cells tracks red richness), aggressive=p90 to bracket a hidden red; the red value
    # tail discount (D1 phantom-red cap) applies to the balanced central. Other heroes are untouched
    # (balanced=p50, aggressive=p75) so ahmed/signalled behaviour is isolated.
    if hero_key == "aisha":
        mid_pct, _richness = _aisha_middle_percentile((total_grid_range or [None, None, None])[1])
        # F1: pure-prior red (zero q6 evidence) -> the value distribution's upper percentiles are
        # phantom red. Don't slide the central up to p85 on cell-richness; clamp to the floor pct so a
        # no-evidence red isn't bet as present (fixes the +170% phantom-red over-bid; evidenced/real
        # red keeps the slide). The red value tail is additionally trimmed in _apply_aisha_d1_bid_adjustment.
        if _is_pure_prior_red(evidence) and not os.environ.get(AISHA_DISABLE_PURE_PRIOR_RED_DAMP):
            mid_pct = min(mid_pct, AISHA_MIDDLE_PCT_LO)
        balanced_central = _weighted_quantile(weighted_values, mid_pct)
        _append_source_note_once(notes, f"aisha_dynamic_balanced_pct:{mid_pct:.2f}")
        adjusted = _apply_aisha_d1_bid_adjustment(
            notes,
            snapshot=snapshot,
            hero_key=hero_key,
            quality_count_ranges=quality_count_ranges,
            total_grid_range=total_grid_range,
            evidence=evidence,
            p50=balanced_central,
            rv50=rv50,
        )
        if adjusted is not None:
            balanced_central = adjusted
        aggressive_central = p90
    else:
        balanced_central = p50
        aggressive_central = p75

    # Display contract: 保守 <= 参考 <= 激进. The safety multipliers DESCEND (0.90/0.85/0.80), so on a
    # NARROW value band (p25 within ~5.9% of balanced) conservative=p25*0.90 mechanically overtakes
    # balanced=center*0.85 and the ladder inverts (user-observed: 2401 R3 保守 98.8w > 参考 96.0w).
    # Clamp to the balanced central so the three tiers never cross; collapses to equality on a tight
    # band (honest) instead of showing 保守 above 参考.
    _cons_p25 = float(p25 or 0)
    # Pure-prior red: strip the conservative tier's unseen-red component so 保守 is a true safe floor
    # (assume the red you can't see isn't there). Subtract the red value at the SAME (p25) quantile so
    # the non-red base remains aligned; 参考/激进 untouched. See AISHA_CONS_PURE_PRIOR_RED_KEEP.
    if (
        hero_key == "aisha"
        and p25 is not None
        and _is_pure_prior_red(evidence)
        and not os.environ.get(AISHA_DISABLE_CONS_PURE_PRIOR_RED_STRIP)
    ):
        # Subtract the MEDIAN red (rv50), not rv25: the p25-of-total scenario still carries the
        # phantom red the count center-regression bakes in (rv25 under-strips), and a SAFE floor should
        # exclude the full expected unseen red. Floor at the non-red value; going below truth is fine for
        # a floor (margin), the monotonic clamp keeps 保守 <= 参考.
        rv_strip = (_weighted_quantile(weighted_red_value, 0.50) or 0.0) * _value_scale
        cons_red_keep = float(
            os.environ.get("AISHA_CONS_PURE_PRIOR_RED_KEEP") or AISHA_CONS_PURE_PRIOR_RED_KEEP
        )
        # Same red-evidence ramp as the value cap: don't strip 75% of the median red off the safe
        # floor when the engine's own count says the vault is genuinely red-heavy (eval: 保守 4+ red
        # was -922k vs truth). r50 = red-count median for this combo set.
        cons_red_keep = _aisha_red_keep_evidence_ramp(r50, cons_red_keep)
        stripped = max(0.0, _cons_p25 - float(rv_strip) * (1.0 - cons_red_keep))
        if stripped < _cons_p25:
            notes.append(f"aisha_cons_pure_prior_red_strip:{int(round(_cons_p25))}->{int(round(stripped))}")
            _cons_p25 = stripped
    _cons_value = _cons_p25 * conservative_mul
    _bal_value = (balanced_central or 0) * balanced_mul
    _agg_value = (aggressive_central or 0) * aggressive_mul
    _cons_value = min(_cons_value, _bal_value)
    _agg_value = max(_agg_value, _bal_value)
    return RefResult(
        status="count_prior" if total_prior_center is not None else "ok",
        source="ref_v0",
        conservative=int(round(_cons_value)),
        balanced=int(round(_bal_value)),
        aggressive=int(round(_agg_value)),
        value_p25=int(round(p25 or 0)),
        value_p50=int(round(p50 or 0)),
        value_p75=int(round(p75 or 0)),
        combo_count=len(combos),
        red_count_range=(
            int(round(r10)) if r10 is not None else None,
            int(round(r50)) if r50 is not None else None,
            int(round(r90)) if r90 is not None else None,
        ),
        red_cells_range=(
            int(round(rc10)) if rc10 is not None else None,
            int(round(rc50)) if rc50 is not None else None,
            int(round(rc90)) if rc90 is not None else None,
        ),
        red_value_range=(
            int(round(rv10)) if rv10 is not None else None,
            int(round(rv50)) if rv50 is not None else None,
            int(round(rv90)) if rv90 is not None else None,
        ),
        quality_count_ranges=quality_count_ranges,
        quality_cells_ranges=quality_cells_ranges,
        total_grid_range=total_grid_range,
        notes=tuple(dict.fromkeys(notes)),
        evidence=evidence,
    )


def run_reference_engine_from_path(path: Path) -> RefResult:
    return run_reference_engine(json.loads(path.read_text(encoding="utf-8-sig")))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run isolated Ahmed reference v0 on a snapshot.")
    parser.add_argument(
        "snapshot",
        nargs="?",
        default=str(ROOT / "data" / "logs" / "live" / "latest_snapshot.json"),
    )
    args = parser.parse_args()
    result = run_reference_engine_from_path(Path(args.snapshot))
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
