"""Historical count-window arithmetic plus a strict, synthetic teaching wrapper.

Source: docs/research/PROVENANCE.json, entry aisha-early-window.
Research example only; this is not the product engine or a stable package API.
"""

from __future__ import annotations


AISHA_EARLY_ROUND_COUNT_WINDOW_CAP = 50
AISHA_EARLY_ROUND_COUNT_WINDOW_EXPANDED_NOTE = "aisha_early_round_count_window_expanded"
_TOTAL_COUNT_PRIOR_WINDOW_CAP = 100


def _aisha_early_round_count_window_bounds(
    *,
    center: int,
    count_floor: int,
    notes: list[str],
) -> tuple[int, int]:
    """±2 count prior window for audit_aisha_early_round (R1–R2 live cap path).

    May return lower>upper when center exceeds AISHA_EARLY_ROUND_COUNT_WINDOW_CAP; caller
    should run fixed-band clamp first, then _maybe_expand_aisha_early_inverted_count_window.
    """
    del notes  # expand note is emitted only after band clamp fails to restore a range
    lower = max(count_floor, center - 2, 1)
    upper = min(
        AISHA_EARLY_ROUND_COUNT_WINDOW_CAP,
        max(lower, center + 2),
    )
    return lower, upper


def _maybe_expand_aisha_early_inverted_count_window(
    *,
    lower: int,
    upper: int,
    center: int,
    count_floor: int,
    notes: list[str],
    rollback: bool = False,
) -> tuple[int, int]:
    """Historical arithmetic; explicit rollback replaces the original env lookup.

    On the grid-target branch this runs AFTER fixed-band clamp/reanchor. The
    no-grid-target branch did not have that clamp and called it immediately.
    """
    if lower <= upper:
        return lower, upper
    if rollback:
        return lower, upper
    lower = max(count_floor, center - 2, 1)
    upper = min(_TOTAL_COUNT_PRIOR_WINDOW_CAP, max(lower, center + 2))
    notes.append(
        f"{AISHA_EARLY_ROUND_COUNT_WINDOW_EXPANDED_NOTE}:{lower}-{upper}@center{center}"
    )
    return lower, upper


def early_window_example(
    center: int,
    count_floor: int = 1,
    *,
    fixed_band: tuple[int, int] | None = None,
    branch: str = "with_grid_target",
    rollback: bool = False,
) -> dict:
    """Validate toy inputs, then trace the selected historical branch order.

    The caller supplies a reachable band; its derivation from item composition
    is deliberately absent. Strict input checks below are NEW teaching code.
    A valid input can still yield no candidates when the 100 cap is exceeded.
    """
    if type(center) is not int or center < 1:
        raise ValueError("center must be a positive integer count")
    if type(count_floor) is not int or count_floor < 0:
        raise ValueError("count_floor must be a non-negative integer count")
    if type(rollback) is not bool:
        raise ValueError("rollback must be a bool")
    if branch not in ("with_grid_target", "without_grid_target"):
        raise ValueError("unknown historical branch")
    if fixed_band is not None:
        if branch != "with_grid_target":
            raise ValueError("the no-grid-target branch has no fixed-band step")
        if (
            not isinstance(fixed_band, tuple)
            or len(fixed_band) != 2
            or any(type(x) is not int for x in fixed_band)
            or not 1 <= fixed_band[0] <= fixed_band[1] <= _TOTAL_COUNT_PRIOR_WINDOW_CAP
            or fixed_band[0] < count_floor
        ):
            raise ValueError("fixed_band must be a reachable count interval within 1..100")

    notes: list[str] = []
    lower, upper = _aisha_early_round_count_window_bounds(
        center=center, count_floor=count_floor, notes=notes
    )
    steps = [{"stage": "legacy_cap", "bounds": [lower, upper], "center": center}]
    if branch == "with_grid_target":
        if fixed_band is not None:
            band_lo, band_hi = fixed_band
            clamped_lo = max(lower, band_lo)
            clamped_hi = min(upper, band_hi)
            if clamped_lo > clamped_hi:
                notes.append(
                    f"count_window_reanchored_to_fixed_band:{lower}-{upper}->{band_lo}-{band_hi}"
                )
                lower, upper = band_lo, band_hi
                center = max(band_lo, min(center, band_hi))
            elif (clamped_lo, clamped_hi) != (lower, upper):
                notes.append(
                    f"count_window_clamped_to_fixed_band:{lower}-{upper}->{clamped_lo}-{clamped_hi}"
                )
                lower, upper = clamped_lo, clamped_hi
                center = max(lower, min(center, upper))
        steps.append({"stage": "fixed_band", "bounds": [lower, upper], "center": center})

    lower, upper = _maybe_expand_aisha_early_inverted_count_window(
        lower=lower, upper=upper, center=center, count_floor=count_floor,
        notes=notes, rollback=rollback,
    )
    if branch == "with_grid_target":
        # This assignment also appears on the original grid-target branch.
        center = max(lower, min(center, upper))
    steps.append({"stage": "expand_if_inverted", "bounds": [lower, upper], "center": center})
    return {
        "synthetic": True,
        "kind": "count_window_example",
        "unit": "items",
        "branch": branch,
        "rollback": rollback,
        "steps": steps,
        "candidates": list(range(lower, upper + 1)),
        "notes": notes,
    }
