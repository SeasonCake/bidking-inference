"""Draw/lottery-hero (sophie / gabriela / raven / wuqilin) calibration policy.

Phase-1 extraction of the side-hero tuning out of ``ahmad_ref_engine`` so the
draw-class count/value calibration lives in one place and the core engine just
calls in (mirrors ``layout_depth_policy``). This module is intentionally PURE —
it holds the calibration constants + the scale/lookup logic and takes already
-normalised hero keys + primitive args, so the dependency stays one-way
(``ahmad_ref_engine`` -> ``draw_hero_policy``) with no import cycle.

The engine keeps the orchestration that needs its own internals (``RefEvidence``,
``normalize_hero_key``, ``_default_total_count``); it normalises the hero key and
hands it here.

Draw/lottery heroes never receive a total-count reveal and have only a sparse
minimap, so their total_count falls back to the map default — systematically
below settled truth. The draw mechanism is the COMMON case; the exceptions are
the signalled heroes (aisha=minimap-anchored, ahmed/ethan/maria=skill/public
signals) that lock the total and bypass the default. So the correction is scoped
by an EXPLICIT allowlist (not "everything non-signalled") because the
classification is evidence-based, not mechanical: victor is draw-mechanism yet
already estimates accurately (-1%) and would over-shoot if boosted.

Count calibration (scripts/audit_hero_total_count_bias.py, 2026-06-16): at scale
1.45 the included heroes land near 0 total-count bias — gabriela -0.2 (n=38),
wuqilin -0.1 (n=20), sophie -1.5 (n=27) — while the signalled controls are
untouched (aisha -10%, ahmed +1% identical across scales). isabella is EXCLUDED
(over-shoots at every scale, +15%..+28%, n=6). raven has no samples yet but is
kept by mechanism (its last-round skill, if it reveals the total, locks it and
the boost auto-skips). Only applies pre-lock (no total_count) and only lifts.
"""

from __future__ import annotations

import os

DRAW_HERO_COUNT_KEYS = frozenset({"sophie", "gabriela", "raven", "wuqilin"})
DRAW_HERO_COUNT_SCALE = 1.45
# Round-ramped boost. A flat +45% over-shoots SMALL warehouses in the EARLY rounds, where the
# draw-hero reveals are too sparse to justify the full lift (live sophie 2406 R1: truth 25 items /
# 70 cells, but the boosted prior 28->41 drove grid 94). The boost's value is at the DECISION round
# (R4+), where it zeroes the count bias on large warehouses; there it stays well-calibrated.
# Sweep (2026-06-18, sophie+gabriela n=78): at R1 the boost ONLY adds small-warehouse over-shoot
# (cntBias +4.2@1.45 -> +1.4@1.25; gridBias +4.2 -> ~0@1.35) while large warehouses are
# under-estimated at EVERY scale (reveals too sparse to reach them); at the decision round 1.45 is
# best (gridBias -0.2). A/B confirmed the ramp gives decision-round zero-regression + lower R1 MAE.
# Aisha/ahmed/ethan/maria are unaffected — the engine's scope guard early-returns for them before
# any scale is read (verified: their count/grid are invariant to this value).
DRAW_HERO_COUNT_SCALE_BY_ROUND = {1: 1.20, 2: 1.30, 3: 1.40}

# Per-hero value calibration (decision-round value_p50 vs settled truth). The value distribution is
# built from a hero-agnostic shape-value prior dominated by aisha-rich warehouses, so it does not
# realise correctly for every hero. A scoped multiplier on the value percentiles re-centres ALL
# three bid tiers (they read p25/p50/p75) plus the displayed value, without touching count/grid or
# any other hero.
#
# Calibration (scripts/fit_draw_hero_value_scale.py + audit_aisha_error_map.py, 2026-06-17, decision
# round, measured on the CORRECT hero path -- the draw-hero 1.45x count boost is what inflates value,
# so both draw-heroes over-value once their real path is exercised):
#   sophie  n=25-28: raw p50 bias +116k..+190k (subset/long-tail sensitive), robust bias +133k
#                 (stable). cons tier exceeds settled truth in ~60% of samples (aisha baseline 35%).
#                 fit mean-ratio 0.67; scale 0.72 brings cons>truth ~40% and errs slightly toward
#                 the SAFE (under) side rather than over-correcting on a small, noisy sample.
#   gabriela: also over (raw +83k) but its cons tier is already ~aisha-level (38%), so the
#                 over-estimate sits in the upper tiers, not the floor. Left at 1.0 (follow-up ~0.88).
# Heroes absent from the table use 1.0 (no change) -- default-safe, opt-in per hero.
DRAW_HERO_VALUE_SCALE: dict[str, float] = {"sophie": 0.72}


# --- Prop-usage ("道具") hints --------------------------------------------------
# Draw heroes get almost no structured reveal from the engine; the player fills the
# warehouse picture by SPENDING battle items (道具). So their "下一步" should name the
# actual prop a draw-hero player would reach for to close the current info gap, rather
# than the generic "等待总件数/总格" copy (which does not match how these heroes play).
#
# Tendency + skill from docs/draw_hero_reveal_mechanics.zh-CN.md §2 (用户口述 + 真实样本
# 双重确认): every round the player spends 0-1 props, usually 1. They are the CHEAP
# reveals (宝光四鉴/双鉴, 随机抽检 ~2500 each) — 宝光四鉴 highest freq, then 宝光双鉴,
# 随机抽检 2/1 — plus an occasional targeted reveal (至宝寻踪 / 巨物抽样).
#
# COST GATE (user, 2026): total-count/total-cells reveals (库存清点 / 总仓储空间 / 全库
# 透视) are HIGH-COST (数万 each) and are only spent ONCE at the endgame to confirm
# warehouse value for the final bid — never early. So the per-round hint recommends only
# the cheap reveals; the expensive all-in-one (全库透视: outlines of ALL items -> count +
# cells + shapes in one) is surfaced ONLY in the decision window. Names match
# data/processed/battle_items.json exactly.
DRAW_HERO_PROP_HINT_KEYS = frozenset({"sophie", "gabriela", "raven", "wuqilin"})

# Draw heroes don't lock a total; the count prior peaks at the DECISION round (R4+, see
# DRAW_HERO_COUNT_SCALE_BY_ROUND), which is also when the expensive total-confirm prop is
# worth spending. No reliable total-rounds signal exists, so R4+ approximates "endgame".
DRAW_HERO_DECISION_ROUND_MIN = 4

# Info gap kind -> ordered prop names (most-likely-used first), names per battle_items.json.
# Only CHEAP per-round reveals here + the single endgame total-confirm prop.
DRAW_HERO_PROP_BY_GAP: dict[str, tuple[str, ...]] = {
    "quality": ("宝光四鉴", "宝光双鉴"),             # show_quality x4 / x2 (top freq, ~2500)
    "value": ("随机抽检（2）", "随机抽检（1）"),       # reveal_items incl. value (~2500)
    "deepest": ("至宝寻踪", "巨物抽样"),             # low-freq targeted (highest-quality / largest)
    "endgame_total": ("全库透视",),                 # 数万贵道具：终局一次确认 件+格+轮廓
}

# Static per-hero kit line (skill auto-reveal + typical prop kit), for a 道具表述 display.
# Per-round = cheap reveals (~2500); endgame = one high-cost total-confirm (数万).
DRAW_HERO_PROP_KIT: dict[str, str] = {
    "sophie": "技能逐轮揭品质；每轮宝光四鉴/双鉴、抽检1/2(便宜)；终局全库透视确认总价",
    "gabriela": "技能逐轮揭品质+轮廓；每轮宝光四鉴/双鉴、抽检1/2；终局全库透视确认总价",
    "raven": "抽奖信息稀疏；每轮宝光四鉴/双鉴、抽检补品质；终局全库透视确认总价",
    "wuqilin": "抽奖信息稀疏；每轮宝光四鉴/双鉴、抽检补品质；终局全库透视确认总价",
}


def draw_hero_uses_prop_hint(normalized_hero: str | None) -> bool:
    """Whether this (already-normalised) hero should get prop-name hints instead of
    generic '补总件/总格' copy. Env override for A/B / future roster additions."""
    raw = os.environ.get("DRAW_HERO_PROP_KEYS_OVERRIDE")
    keys = frozenset(k.strip() for k in raw.split(",") if k.strip()) if raw else DRAW_HERO_PROP_HINT_KEYS
    return str(normalized_hero or "") in keys


def draw_hero_prop_for_gap(gap: str, *, limit: int = 2) -> str:
    """Prop name(s) a draw hero would use to close `gap` (joined, top-`limit`)."""
    props = DRAW_HERO_PROP_BY_GAP.get(gap, ())
    return "/".join(props[:limit])


def draw_hero_prop_kit_text(normalized_hero: str | None) -> str:
    """Static '道具使用表述' line for a draw hero (empty for non-draw heroes)."""
    return DRAW_HERO_PROP_KIT.get(str(normalized_hero or ""), "")


def draw_hero_count_keys() -> frozenset[str]:
    # Env override for calibration A/B only; default is the committed allowlist.
    raw = os.environ.get("DRAW_HERO_KEYS_OVERRIDE")
    return frozenset(k.strip() for k in raw.split(",") if k.strip()) if raw else DRAW_HERO_COUNT_KEYS


def draw_hero_count_scale(round_no: int | None = None) -> float:
    """Boost multiplier for the count prior, ramped by round (None -> full scale)."""
    raw = os.environ.get("DRAW_HERO_SCALE_OVERRIDE")  # flat override for A/B (takes precedence)
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    if round_no is not None:
        return DRAW_HERO_COUNT_SCALE_BY_ROUND.get(int(round_no), DRAW_HERO_COUNT_SCALE)
    return DRAW_HERO_COUNT_SCALE


def hero_value_scale(normalized_hero: str | None) -> float:
    """Scoped value-percentile multiplier for an ALREADY-NORMALISED hero key (1.0 = unchanged).

    Env HERO_VALUE_SCALE_OVERRIDE="sophie=0.78,gabriela=1.13" for calibration A/B.
    """
    table = dict(DRAW_HERO_VALUE_SCALE)
    raw = os.environ.get("HERO_VALUE_SCALE_OVERRIDE")
    if raw:
        for pair in raw.split(","):
            key, sep, value = pair.partition("=")
            if sep:
                try:
                    table[key.strip().lower()] = float(value)
                except ValueError:
                    pass
    return table.get(str(normalized_hero or ""), 1.0)
