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

import hashlib
import os
from typing import Iterable

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

# Occupancy-cap divisor for the count boost (cap default ON; rollback DRAW_HERO_DISABLE_OCC_CAP=1). The flat boost
# floors the count to map_prior*scale REGARDLESS of vault size -> est pins ~map-constant (truth 24
# and 55 both estimate ~38-40), over-shooting small vaults (+6~8) and under-shooting large (-8~11).
# The OBSERVED (de-exploded) grid is a size-varying signal: empirically grid_mid/2.5 ~= truth across
# both small and large brackets (real gabriela/sophie last-round). Capping the boost floor at
# de_exploded_grid/divisor lets small vaults (small grid -> low cap) stop over-shooting while large
# vaults (large grid -> cap >= map_prior*scale -> no-op) keep the full boost they need. Sweep
# 2.3~2.8 via env DRAW_HERO_OCC_CAP_DIVISOR_OVERRIDE to calibrate the small/large bracket trade-off.
# Calibrated 2026-06-22 on 40 gabriela + 14 sophie real last-round samples: 2.3 dominates 2.5/2.7 —
# small-vault bias +6.2/+8.1 -> +0.0/+1.3 (MAE 6.9/8.4 -> 3.7/5.0, over% halved) while large-vault
# degradation stays minimal (gabriela -8.2 -> -9.4, sophie unchanged). Lower divisors push small back
# toward over-estimate; higher ones over-fold large vaults (sparse draw bottoms mis-read as lone-deep).
DRAW_HERO_OCC_CAP_DIVISOR = 2.3


def draw_hero_occ_cap_divisor() -> float:
    raw = os.environ.get("DRAW_HERO_OCC_CAP_DIVISOR_OVERRIDE")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return DRAW_HERO_OCC_CAP_DIVISOR

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
# 双重确认): R1 starts with 宝光四鉴 + 随机抽检（2）. After either is spent, its slot is
# filled by one stable 50/50 choice from 宝光双鉴 / 随机抽检（1）; the other fills the
# next spent slot. Gabriela/Sophie move to a 至宝 tool at R4, while Raven uses 全库透视.
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

# Progressive cheap-prop queue for the heroes whose live recommendation follows the
# round-by-round player workflow instead of the current model gap. The backfill order is
# chosen once per session, so a fast-refreshing panel never flips between two suggestions.
DRAW_HERO_PROGRESSIVE_HINT_KEYS = frozenset({"sophie", "gabriela", "raven"})
DRAW_HERO_OPENING_PROPS: tuple[tuple[str, str], ...] = (
    ("100136", "宝光四鉴"),
    ("100129", "随机抽检（2）"),
)
DRAW_HERO_BACKFILL_PROPS: tuple[tuple[str, str], ...] = (
    ("100135", "宝光双鉴"),
    ("100128", "随机抽检（1）"),
)

# Static per-hero kit line (skill auto-reveal + typical prop kit), for a 道具表述 display.
# Per-round = cheap reveals (~2500); endgame = one high-cost total-confirm (数万).
DRAW_HERO_PROP_KIT: dict[str, str] = {
    "sophie": "R1宝光四鉴+抽检2；逐次补双鉴/抽检1；R4使用至宝道具",
    "gabriela": "R1宝光四鉴+抽检2；逐次补双鉴/抽检1；R4使用至宝道具",
    "raven": "R1宝光四鉴+抽检2；逐次补双鉴/抽检1；R4全库透视",
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


def draw_hero_progressive_prop_hint(
    normalized_hero: str | None,
    *,
    round_no: int | None,
    session_id: str = "",
    used_action_ids: Iterable[str | int] = (),
) -> str:
    """Stable per-session next-prop queue for Gabriela/Sophie/Raven.

    `sent` actions count as used immediately; callers should union them with results so
    packet loss or a result-less action cannot make the panel recommend a consumed prop.
    """
    hero_key = str(normalized_hero or "")
    if hero_key not in DRAW_HERO_PROGRESSIVE_HINT_KEYS:
        return ""
    if round_no is not None and int(round_no) >= DRAW_HERO_DECISION_ROUND_MIN:
        return "全库透视" if hero_key == "raven" else "使用至宝道具"

    used = {str(action_id).strip() for action_id in used_action_ids if str(action_id).strip()}
    backfill = list(DRAW_HERO_BACKFILL_PROPS)
    # sha256 gives an approximately even, reproducible split without Python's process-randomised
    # hash() or a redraw-time RNG that would make the visible recommendation flicker.
    if hashlib.sha256(str(session_id or "").encode("utf-8")).digest()[0] & 1:
        backfill.reverse()
    queue = (*DRAW_HERO_OPENING_PROPS, *backfill)
    remaining = [name for action_id, name in queue if action_id not in used]
    return "/".join(remaining[:2]) or "常用道具已用，观察出价"


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


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def sophie_value_scale(drawn_red: int, round_no: int | None) -> float:
    """Sophie's value multiplier as a function of the number of reds SHE DREW.

    Sophie has NO outline (mechanics doc granularity ①: 品质+粗略位置), so her red/vault-value
    expectation is DRAW-COUNT driven: drawing K reds early raises the expectation with K (even if
    it is the low-prob event of drawing the only few reds). The old flat 0.72 (DRAW_HERO_VALUE_SCALE)
    over-damps the red-rich case — a red0 vault and a red3+ vault both got ×0.72, but red-rich is
    already badly UNDER before the damp (faithful eval: sophie red3+ ≈ −44%). This ramps the
    multiplier UP with `drawn_red` (= coarse evidence.min_counts['q6'], the only red signal sophie
    has — no footprint): 0 drawn → LO (keep the no-red damp, red0 unchanged), ≥K1 drawn → HI (lift
    the red-rich tail OUT of the damp). Monotone in drawn_red ⇒ can NEVER lift a draw-count-0 vault.
    SOPHIE_VALUE_SCALE_RAMP_DISABLE=1 ⇒ flat LO (byte-identical to the old constant 0.72).
    """
    lo = _env_float("SOPHIE_VALUE_SCALE_LO", DRAW_HERO_VALUE_SCALE.get("sophie", 0.72))
    if os.environ.get("SOPHIE_VALUE_SCALE_RAMP_DISABLE") == "1":
        return lo
    hi = _env_float("SOPHIE_VALUE_SCALE_HI", 1.05)       # cap; A/B in {1.0, 1.05, 1.10}
    k0 = _env_float("SOPHIE_VALUE_RAMP_K0", 1.0)          # reds at which the lift begins
    k1 = _env_float("SOPHIE_VALUE_RAMP_K1", 3.0)          # reds at full lift (= red3+ bucket boundary)
    f = max(0.0, min(1.0, (float(drawn_red) - k0) / max(1e-9, k1 - k0)))
    if round_no is not None and int(round_no) < 2:
        f *= 0.5  # R1 draws are sparse/noisy — a single early lucky red shouldn't fully un-damp
    return lo + f * (hi - lo)


def gabriela_value_scale(revealed_red: int, round_no: int | None) -> float:
    """Gabriela's value multiplier as a function of the reds she has REVEALED (outline ②).

    Gabriela sees outlines, so her red value rides on the engine's revealed-red anchor; but on
    user-visible `balanced` the red-rich vaults still under-estimate (faithful eval: gabriela red3+
    ≈ −25%). When she HAS revealed red evidence the price should be in a credible range (author:
    "看到/抽到几个红还给低价会降可信度"). Lift modestly with `revealed_red` (= evidence.min_counts['q6'],
    her coarse revealed reds). LO=1.0 (no change) at revealed=0 ⇒ the mild red0 +7% is NOT touched
    (monotone, 0 reds → 1.0); ramp up to HI by K1 reds. Conservative HI — gabriela already
    extrapolates red count (some red-rich cases over-shoot), so this is a gentle nudge, not a full
    correction; the no-evidence (info-gap) vaults stay conservatively low by design (she can't see
    those reds). GABRIELA_VALUE_SCALE_RAMP_DISABLE=1 ⇒ flat 1.0 (byte-identical to today).
    """
    if os.environ.get("GABRIELA_VALUE_SCALE_RAMP_DISABLE") == "1":
        return 1.0
    lo = _env_float("GABRIELA_VALUE_SCALE_LO", 1.0)
    hi = _env_float("GABRIELA_VALUE_SCALE_HI", 1.15)     # gentle cap; A/B in {1.10, 1.15, 1.20}
    k0 = _env_float("GABRIELA_VALUE_RAMP_K0", 0.0)        # lift begins at the first revealed red
    k1 = _env_float("GABRIELA_VALUE_RAMP_K1", 3.0)        # full lift at red3+ boundary
    f = max(0.0, min(1.0, (float(revealed_red) - k0) / max(1e-9, k1 - k0)))
    if round_no is not None and int(round_no) < 2:
        f *= 0.5  # R1 reveals sparse/noisy — don't fully lift on one early outlined red
    return lo + f * (hi - lo)
