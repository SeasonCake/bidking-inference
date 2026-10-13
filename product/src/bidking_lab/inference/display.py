"""Game-display model for the ``X品均格`` (average-cells-by-quality) tool.

Why this module exists
----------------------
When the player uses a ``X品均格`` tool, the game shows them an average
``(总格数 / 件数)`` for that quality tier — a small decimal like ``2.9``,
``2.90`` or ``2.345``. The player can *read decimals back* to constrain
the unknown integers ``(total_cells, count)``.

Game display rule (CONFIRMED by 2026-05-15 screenshots)
-------------------------------------------------------
The game shows the ratio **truncated (floored)** to **at most 2 decimals**,
and **trims trailing zeros if and only if the truncated value equals the
exact ratio**:

* Floor first: ``floor(value × 100) / 100``
* If that equals the exact ratio → trim trailing zeros
* Otherwise display all 2 decimals (with the trailing 0 preserved when
  the second decimal happens to be zero)

Examples calibrated against playtest screenshots:

* ``17 / 7 = 2.4285…`` → floor at d=2 = ``2.42`` ≠ exact → ``"2.42"``
  (mansion screenshot, 优品均格 says "约2.42格" with map-provided 17 cells)
* ``35 / 14 = 2.5`` exactly → floor at d=2 = ``2.50`` == exact → trim
  → ``"2.5"`` (shipwreck screenshot, 优品均格 says "约2.5格")
* ``32 / 11 = 2.909…`` → floor at d=2 = ``2.90`` ≠ exact → ``"2.90"``
  (the user's original 2.90 ↔ 32/11 mapping)
* ``29 / 10 = 2.9`` exactly → floor at d=2 = ``2.90`` == exact → trim
  → ``"2.9"``
* ``55 / 16 = 3.4375`` → floor = ``3.43`` ≠ exact → ``"3.43"``
  (shipwreck screenshot, one of the candidates for "3.43" reading)

The game always says "约X.XX格" (approximately X.XX cells) which is
consistent with truncation: it's reporting "at least 2.42" rather than
"closest to 2.43".

The user confirmed:

1. The game **occasionally shows 3 decimals** (so d ∈ {0, 1, 2, 3}).
   The exact rule for choosing d is not fully reverse-engineered yet;
   parsing is therefore done by *display length* rather than assuming
   a fixed d.
2. **Integer-looking readings (e.g., ``"3"``)** carry almost no
   information — they admit far too many (m, n) candidates. This is
   captured by :func:`reading_info_bits`.

Public API
----------
* :class:`Reading` — a parsed game-screen reading
* :func:`parse_reading` — string → :class:`Reading`
* :func:`format_value` — exact ratio → game-display string (best-effort
  simulator; useful for unit tests once screenshots confirm the rule)
* :func:`is_compatible` — does ``(total_cells, count)`` produce this
  reading?
* :func:`enumerate_candidates` — list all ``(total_cells, count)``
  consistent with a reading, optionally bounded by warehouse-size
  priors
* :func:`reading_info_bits` — coarse information-content metric
  (log2 of plausible-candidate count)

All numeric work uses :class:`fractions.Fraction` to avoid float drift
on equality checks like ``29/10 == 2.9``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable

_READING_RE = re.compile(r"^\s*(\d+)(?:\.(\d+))?\s*$")


@dataclass(frozen=True)
class Reading:
    """A parsed ``X品均格`` reading from the game screen.

    Attributes
    ----------
    raw:
        Original string the player typed in.
    value:
        Exact rational equal to the digits shown (e.g., ``"2.90"`` →
        ``Fraction(29, 10)``, **not** ``Fraction(290, 100)`` — those are
        equal as :class:`Fraction`).
    n_decimals:
        Number of digits to the right of the decimal point in ``raw``.
        ``0`` for bare integers.
    trailing_zero:
        ``True`` iff ``n_decimals >= 1`` and the last digit shown is
        ``0``. Implies that the displayed value is **rounded**, not
        exact (because the game trims trailing zeros for exact values).
    is_integer:
        ``True`` iff no decimal point appeared in ``raw``.
    """

    raw: str
    value: Fraction
    n_decimals: int
    trailing_zero: bool
    is_integer: bool


def parse_reading(text: str) -> Reading:
    """Parse a game-screen string like ``"2.9"`` or ``"2.90"`` or ``"3"``.

    Raises
    ------
    ValueError
        If ``text`` does not look like a non-negative decimal number.
    """
    match = _READING_RE.match(text)
    if not match:
        raise ValueError(f"unrecognised reading: {text!r}")
    int_part = match.group(1)
    frac_part = match.group(2) or ""
    n_decimals = len(frac_part)
    numerator = int(int_part + frac_part) if frac_part else int(int_part)
    denominator = 10**n_decimals if n_decimals else 1
    value = Fraction(numerator, denominator)
    trailing_zero = n_decimals >= 1 and frac_part[-1] == "0"
    return Reading(
        raw=text.strip(),
        value=value,
        n_decimals=n_decimals,
        trailing_zero=trailing_zero,
        is_integer=(n_decimals == 0),
    )


def format_value(m: int, n: int, *, max_decimals: int = 2) -> str:
    """Simulate the game's display for the exact ratio ``m / n``.

    Reproduces the truncation rule confirmed by playtest screenshots
    (2026-05-15):

    * Take ``floor(m / n × 10^max_decimals) / 10^max_decimals`` (truncation).
    * If that equals the exact ratio (decimal terminates within
      ``max_decimals``), trim trailing zeros (``35/14 → "2.5"``,
      ``29/10 → "2.9"``, ``3/1 → "3"``).
    * Otherwise keep all ``max_decimals`` digits with trailing zeros
      preserved (``32/11 → "2.90"``, ``17/7 → "2.42"``, ``55/16 → "3.43"``).

    ``max_decimals`` defaults to 2 because the game uses at most
    2 decimals per the user.
    """
    if n <= 0:
        raise ValueError(f"denominator must be positive, got {n}")
    if m < 0:
        raise ValueError(f"numerator must be non-negative, got {m}")
    exact = Fraction(m, n)
    scale = 10**max_decimals
    floored_scaled = (m * scale) // n  # math.floor for non-negative
    floored = Fraction(floored_scaled, scale)
    int_part, frac_part = divmod(floored_scaled, scale)
    digits = str(frac_part).zfill(max_decimals)
    is_exact_at_precision = floored == exact
    if is_exact_at_precision:
        digits = digits.rstrip("0")
    if digits:
        return f"{int_part}.{digits}"
    return str(int_part)


def is_compatible(reading: Reading, total_cells: int, count: int) -> bool:
    """Does ``(total_cells, count)`` produce ``reading`` under the game's display rule?

    Algorithm: simulate the game's display via :func:`format_value` and
    compare to ``reading.raw``. This single-step check handles all four
    cases naturally:

    * Integer reading like ``"3"``: requires exact ratio (else game would
      have shown decimals).
    * One-decimal reading like ``"2.5"``: requires exact ratio (else
      trim would not have happened).
    * Two-decimal trailing-zero reading like ``"2.90"``: requires ratio
      to floor to that 2-dp value but **not** equal it (else trim).
    * Two-decimal non-trailing-zero like ``"2.42"``: requires the ratio
      to be in the half-open interval ``[reading.value, reading.value +
      0.01)``; the ratio may or may not be exact.
    """
    if count <= 0 or total_cells < 0:
        return False
    return format_value(total_cells, count) == reading.raw


def enumerate_candidates(
    reading: Reading,
    *,
    max_count: int = 50,
    max_total_cells: int = 252,
) -> list[tuple[int, int]]:
    """All ``(total_cells, count)`` integer pairs consistent with ``reading``.

    Bounds default to game limits:

    * ``max_count = 50``: per-quality item counts are well below this in
      observed sessions (typical session = 20–35 items total across all
      qualities).
    * ``max_total_cells = 252``: the largest possible quality contribution
      to the 6×7=42 cabinet grid times a generous max of six rounds.
      Real warehouses cap below 150.

    Returned list is sorted by ``count`` ascending then ``total_cells``
    ascending. For integer readings (``"3"``) this list can be very long
    — the caller should filter by warehouse-size prior (see
    :mod:`bidking_lab.inference.observation`).
    """
    out: list[tuple[int, int]] = []
    for count in range(1, max_count + 1):
        for total_cells in range(0, max_total_cells + 1):
            if is_compatible(reading, total_cells, count):
                out.append((total_cells, count))
    return out


def reading_info_bits(
    reading: Reading,
    *,
    max_count: int = 50,
    max_total_cells: int = 252,
) -> float:
    """Coarse information measure: ``log2(plausible-candidate count)``.

    Lower bits ≈ stronger constraint. Integer readings ("3") typically
    score 7–9 bits ("white noise"), while a 3-decimal reading ("2.345")
    usually scores 0–2 bits ("near-pinpoint"). This is the metric to
    use when comparing "should I spend money on this average-cells tool?"
    """
    candidates = enumerate_candidates(
        reading, max_count=max_count, max_total_cells=max_total_cells
    )
    if not candidates:
        return math.inf
    return math.log2(len(candidates))


def avg_value_shows_fractional_cents(avg_value: float) -> bool:
    """True when the map/UI shows a non-integer per-item silver average.

    Game silver totals are integers, so a reading like ``39539.17`` leaks
    that ``avg_value × count`` should land near a whole silver amount.
    Integer displays (``32507``) do not carry this signal.
    """
    if avg_value <= 0:
        return False
    return abs(avg_value - round(avg_value)) > 0.009


def integer_total_leak_distance(avg_value: float, count: int) -> float:
    """``|avg_value × count − round(avg_value × count)|`` — lower is better.

    Uses cent-rounded ``avg_value`` (game shows at most 2 dp) to avoid
    float blow-ups at large ``count``.
    """
    if count <= 0 or avg_value <= 0:
        return float("inf")
    cents = round(avg_value * 100)
    total_silver = (cents * count) / 100.0
    return abs(total_silver - round(total_silver))


def best_count_for_avg_value_integer_leak(
    avg_value: float,
    *,
    max_count: int = 35,
    max_distance: float = 0.05,
) -> int | None:
    """Smallest item count whose ``avg × count`` is near-integer silver.

    Map hints like ``39539.17`` leak denominator information through the
    fractional cents (``×6 → 237235.02``). Among counts that pass
    ``max_distance``, return the **minimum** count (Occam).
    """
    if not avg_value_shows_fractional_cents(avg_value):
        return None
    matches = [
        count
        for count in range(1, max_count + 1)
        if integer_total_leak_distance(avg_value, count) <= max_distance
    ]
    return min(matches) if matches else None


def filter_by_warehouse_size(
    candidates: Iterable[tuple[int, int]],
    *,
    warehouse_size: int,
    shape_known_cells: int = 0,
) -> list[tuple[int, int]]:
    """Drop candidates whose ``total_cells`` exceeds the warehouse minus
    the cells already claimed by identified items.

    ``shape_known_cells`` is the per-quality cells already attributed to
    items the player has identified (e.g. seeing a 5x4 outline pins
    20 cells to the 蓝品 bucket — when evaluating ``良品均格``, subtract
    those 20 first).

    This is the "use the warehouse-size prior to prune" step the user
    described.
    """
    budget = warehouse_size - shape_known_cells
    return [(tc, c) for tc, c in candidates if tc <= budget]


__all__ = (
    "Reading",
    "parse_reading",
    "format_value",
    "is_compatible",
    "enumerate_candidates",
    "reading_info_bits",
    "avg_value_shows_fractional_cents",
    "integer_total_leak_distance",
    "best_count_for_avg_value_integer_leak",
    "filter_by_warehouse_size",
)
