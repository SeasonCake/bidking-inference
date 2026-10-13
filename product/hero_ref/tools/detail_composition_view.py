from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any


QUALITY_ORDER = ("q6", "q5", "q4", "q3", "q1")
QUALITY_LABELS = {
    "q1": "白绿",
    "q3": "蓝",
    "q4": "紫",
    "q5": "金",
    "q6": "红",
}
QUALITY_STATUSES = {
    "ready",
    "exact",
    "unavailable",
    "no_reachable",
    "stale",
    "loading",
    "error",
    "conflict",
}
LOCK_STRENGTHS = {
    "exact",
    "probabilistic",
    "lower_bound",
    "unavailable",
}
COUNT_CORRELATION_STATES = {
    "complete",
    "independent",
    "partial",
    "unknown",
}
QUALITY_COMPOSITION_SCHEMA_VERSION = 1
_COUNT_CORRELATION_FIELDS = frozenset(
    {
        "compact_count_candidates",
        "compact_count_triplet",
        "count_correlation_state",
        "count_correlation_note",
    }
)


@dataclass(frozen=True, slots=True)
class CompositionCandidate:
    count: int
    cells: int
    probability: float


@dataclass(frozen=True, slots=True)
class MarginalCandidate:
    value: int
    probability: float


@dataclass(frozen=True, slots=True)
class QualityComposition:
    key: str
    label: str
    status: str
    source: str
    lock_strength: str
    lock_reason: str
    candidates: tuple[CompositionCandidate, ...]
    count_candidates: tuple[MarginalCandidate, ...]
    cells_candidates: tuple[MarginalCandidate, ...]
    shown_probability: float | None
    other_probability: float | None
    truncated: bool
    cap_hit: bool
    completion_flags_complete: bool
    pricing_posterior: bool
    display_reconciled: bool
    reason: str
    count_lock_value: int | None = None
    cells_lock_value: int | None = None
    dimension_state: str = ""
    discrete_count_display: bool = False
    compact_count_candidates: tuple[MarginalCandidate, ...] = ()
    compact_count_triplet: tuple[int, ...] = ()
    count_correlation_state: str = ""
    count_correlation_note: str = ""


@dataclass(frozen=True, slots=True)
class DetailCompositionView:
    status: str
    reason: str
    qualities: tuple[QualityComposition, ...]

    def quality(self, key: str) -> QualityComposition:
        for quality in self.qualities:
            if quality.key == key:
                return quality
        raise KeyError(key)


def composition_candidate_is_physical(
    candidate: CompositionCandidate | None,
) -> bool:
    """Reject zero/non-zero count-cell hybrids at the visible trust boundary."""
    if not _has_exact_type(candidate, CompositionCandidate):
        return False
    return (candidate.count == 0) == (candidate.cells == 0)


def _visible_compact_count_triplet(
    quality: QualityComposition,
) -> tuple[int, int, int] | None:
    """Normalize cross-quality arm order into the visible low/middle/high scale.

    Complete total-count arms are stored in cross-tier tuple order.  q6 can
    therefore run in the opposite direction when an earlier quality increases.
    That arm order belongs in structured details; the persistent red 保/均/激
    row is a numeric scale and must stay small-to-large.  Other qualities keep
    their arm positions so callers that inspect complete cross-tier arms cannot
    accidentally splice mutually exclusive states.
    """
    if len(quality.compact_count_triplet) != 3:
        return None
    first, second, third = quality.compact_count_triplet
    if quality.key != "q6":
        return first, second, third
    low, middle, high = sorted((first, second, third))
    return low, middle, high


def quality_paired_candidate_triplet(
    quality: QualityComposition | None,
) -> tuple[
    CompositionCandidate | None,
    CompositionCandidate | None,
    CompositionCandidate | None,
] | None:
    """Select three visible columns while preserving each joint count/cell row.

    ``count_correlation_state=unknown`` means the displayed qualities cannot be
    combined into complete cross-tier total-count arms.  It does not invalidate
    the count/cell pairing inside one quality's canonical joint candidates.  Keep
    those physical q6 pairs visible while the badge/hover continues to describe
    the cross-tier linkage as pending.
    """
    if (
        not _has_exact_type(quality, QualityComposition)
        or quality.status not in {"ready", "exact"}
        or not quality.candidates
    ):
        return None
    candidates = tuple(quality.candidates)
    selected: tuple[CompositionCandidate, CompositionCandidate, CompositionCandidate]
    visible_count_triplet = _visible_compact_count_triplet(quality)
    if visible_count_triplet is not None:
        by_count: list[CompositionCandidate] = []
        for index, count in enumerate(visible_count_triplet):
            matches = tuple(row for row in candidates if row.count == count)
            if not matches:
                return None
            if index == 0:
                chosen = min(matches, key=lambda row: (row.cells, -row.probability))
            elif index == 1:
                chosen = max(matches, key=lambda row: (row.probability, -row.cells))
            else:
                chosen = max(matches, key=lambda row: (row.cells, row.probability))
            by_count.append(chosen)
        selected = (by_count[0], by_count[1], by_count[2])
    else:
        selected = (
            min(candidates, key=lambda row: (row.count, row.cells)),
            max(candidates, key=lambda row: (row.probability, -row.count, -row.cells)),
            max(candidates, key=lambda row: (row.count, row.cells)),
        )
    return tuple(
        row if composition_candidate_is_physical(row) else None
        for row in selected
    )  # type: ignore[return-value]


def quality_compact_paired_candidates(
    quality: QualityComposition | None,
) -> tuple[CompositionCandidate, ...]:
    """Choose a compact low/centre/high envelope without splitting joint rows.

    The persistent quality rows have room for at most three representative
    states.  Every displayed count/cell index must therefore come from one real
    ``CompositionCandidate``.  For one to three states we keep every state; for
    a longer posterior we keep the physical low/high endpoints and one useful
    centre state.  Hover/details retain the full posterior.
    """
    if (
        not _has_exact_type(quality, QualityComposition)
        or quality.status not in {"ready", "exact"}
        or not quality.candidates
        or any(
            not composition_candidate_is_physical(candidate)
            for candidate in quality.candidates
        )
    ):
        return ()
    ordered = tuple(
        sorted(
            quality.candidates,
            key=lambda row: (row.count, row.cells, -row.probability),
        )
    )
    if len(ordered) <= 3:
        return ordered

    low = ordered[0]
    high = ordered[-1]
    centre = max(
        ordered,
        key=lambda row: (row.probability, -row.count, -row.cells),
    )
    if (centre.count, centre.cells) in {
        (low.count, low.cells),
        (high.count, high.cells),
    }:
        centre = ordered[len(ordered) // 2]
    selected: list[CompositionCandidate] = []
    seen: set[tuple[int, int]] = set()
    for candidate in (low, centre, high):
        identity = (candidate.count, candidate.cells)
        if identity not in seen:
            selected.append(candidate)
            seen.add(identity)
    return tuple(selected)


def _has_exact_type(value: Any, expected: type[Any]) -> bool:
    """Check a built-in/container type without accepting protocol-bearing subclasses."""
    return type(value) is expected  # noqa: E721 - exact type is the safety contract


def _strict_exact_fields(
    *,
    status: str,
    lock_strength: str,
    candidates: list[CompositionCandidate] | tuple[CompositionCandidate, ...],
    shown_probability: float | None,
    other_probability: float | None,
    truncated: bool | None,
    cap_hit: bool | None,
    completion_flags_complete: bool,
) -> bool:
    if (
        not _has_exact_type(status, str)
        or not _has_exact_type(lock_strength, str)
        or type(candidates) not in (list, tuple)
        or not _has_exact_type(shown_probability, float)
        or not _has_exact_type(other_probability, float)
        or not _has_exact_type(truncated, bool)
        or not _has_exact_type(cap_hit, bool)
        or not _has_exact_type(completion_flags_complete, bool)
    ):
        return False
    if len(candidates) != 1 or not _has_exact_type(
        candidates[0],
        CompositionCandidate,
    ):
        return False
    probability = candidates[0].probability
    if not _has_exact_type(probability, float):
        return False
    return (
        status == "exact"
        and lock_strength == "exact"
        and probability == 1.0
        and shown_probability == 1.0
        and other_probability == 0.0
        and completion_flags_complete
        and truncated is False
        and cap_hit is False
    )


def quality_is_strict_exact(quality: QualityComposition | None) -> bool:
    """Return whether every joint field authorizes an exact UI lock."""
    if quality is None:
        return False
    if not _strict_exact_fields(
        status=quality.status,
        lock_strength=quality.lock_strength,
        candidates=quality.candidates,
        shown_probability=quality.shown_probability,
        other_probability=quality.other_probability,
        truncated=quality.truncated,
        cap_hit=quality.cap_hit,
        completion_flags_complete=quality.completion_flags_complete,
    ):
        return False
    joint = quality.candidates[0]
    for marginal, expected in (
        (quality.count_candidates, joint.count),
        (quality.cells_candidates, joint.cells),
    ):
        if marginal and not (
            len(marginal) == 1
            and _has_exact_type(marginal[0], MarginalCandidate)
            and marginal[0].value == expected
            and marginal[0].probability == 1.0
        ):
            return False
    return True


def format_quality_state_badge(
    *qualities: QualityComposition | None,
) -> str:
    """Return the compact, always-visible joint-state label.

    The caller may combine real structured tiers (the compact low-quality row is
    q1=白绿 plus q3=蓝).  A mixed row remains probabilistic whenever any tier has
    another reachable joint candidate.  Missing/non-ready tiers never authorize
    a lock, and percentile/range text is deliberately not accepted here.
    """
    if not qualities:
        return ""
    states: list[str] = []
    for quality in qualities:
        if not _has_exact_type(quality, QualityComposition):
            states.append("unavailable")
            continue
        if quality_is_strict_exact(quality):
            states.append("locked")
            continue
        if quality.status in {"ready", "exact"} and bool(quality.candidates):
            states.append("probabilistic")
            continue
        states.append("unavailable")
    if any(
        _has_exact_type(quality, QualityComposition)
        and quality.status == "conflict"
        for quality in qualities
    ):
        return "冲突"
    if states and all(state == "locked" for state in states):
        return "锁定"
    if any(state in {"locked", "probabilistic"} for state in states):
        return "未锁"
    return ""


def _stable_dict(value: Any) -> dict[str, Any] | None:
    """Accept only plain JSON-object dictionaries without touching key protocols."""
    if not _has_exact_type(value, dict):
        return None
    if any(not _has_exact_type(key, str) for key in value.keys()):
        return None
    return value


def _non_negative_int(value: Any) -> int | None:
    if not _has_exact_type(value, int) or value < 0:
        return None
    return value


def _probability(value: Any) -> float | None:
    if _has_exact_type(value, int):
        return float(value) if value in (0, 1) else None
    if not _has_exact_type(value, float):
        return None
    if not math.isfinite(value) or value < 0.0 or value > 1.0:
        return None
    return value


def _boolean(value: Any, *, default: bool | None = False) -> bool | None:
    if value is None:
        return default
    return value if _has_exact_type(value, bool) else None


def _text_projection(value: Any, default: str = "") -> tuple[str, bool]:
    if value is None:
        return default, True
    if not _has_exact_type(value, str):
        return default, False
    text = value.strip()
    return (text or default), True


def _empty_quality(
    key: str,
    *,
    status: str = "unavailable",
    reason: str = "等待结构化组合数据",
) -> QualityComposition:
    return QualityComposition(
        key=key,
        label=QUALITY_LABELS[key],
        status=status,
        source="",
        lock_strength="unavailable",
        lock_reason="",
        candidates=(),
        count_candidates=(),
        cells_candidates=(),
        shown_probability=None,
        other_probability=None,
        truncated=False,
        cap_hit=False,
        completion_flags_complete=False,
        pricing_posterior=False,
        display_reconciled=False,
        reason=reason,
        count_lock_value=None,
        cells_lock_value=None,
        dimension_state="",
        discrete_count_display=False,
        compact_count_candidates=(),
        compact_count_triplet=(),
        count_correlation_state="",
        count_correlation_note="",
    )


def _quality_view(key: str, raw_value: Any) -> QualityComposition:
    raw = _stable_dict(raw_value)
    if raw is None:
        return _empty_quality(
            key,
            status="conflict",
            reason="品质组合字段类型冲突",
        )
    if not raw:
        return _empty_quality(key)

    status, status_valid = _text_projection(
        raw.get("status"),
        "unavailable",
    )
    status = status.lower()
    status_known = status in QUALITY_STATUSES
    if not status_known:
        status = "conflict"

    lock_strength, lock_strength_valid = _text_projection(
        raw.get("lock_strength"),
        "unavailable",
    )
    lock_strength = lock_strength.lower()
    lock_strength_known = lock_strength in LOCK_STRENGTHS
    if lock_strength not in LOCK_STRENGTHS:
        lock_strength = "unavailable"
    source, source_valid = _text_projection(raw.get("source"))
    lock_reason, lock_reason_valid = _text_projection(raw.get("lock_reason"))
    reason, reason_valid = _text_projection(raw.get("reason"))

    candidates: list[CompositionCandidate] = []
    seen: set[tuple[int, int]] = set()
    invalid_candidate = False
    raw_candidates = raw.get("candidates")
    if type(raw_candidates) in (list, tuple):
        for raw_candidate in raw_candidates:
            candidate = _stable_dict(raw_candidate)
            if candidate is None:
                invalid_candidate = True
                continue
            count = _non_negative_int(candidate.get("count"))
            cells = _non_negative_int(candidate.get("cells"))
            probability = _probability(candidate.get("probability"))
            identity = (count, cells)
            if (
                count is None
                or cells is None
                or probability is None
                or identity in seen
            ):
                invalid_candidate = True
                continue
            seen.add(identity)
            candidates.append(
                CompositionCandidate(
                    count=count,
                    cells=cells,
                    probability=probability,
                )
            )
    elif raw_candidates not in (None, ()):
        invalid_candidate = True

    candidates.sort(key=lambda item: (-item.probability, item.count, item.cells))

    def marginal_candidates(field: str) -> tuple[list[MarginalCandidate], bool]:
        if field not in raw:
            return [], False
        raw_rows = raw.get(field)
        if type(raw_rows) not in (list, tuple):
            return [], True
        rows: list[MarginalCandidate] = []
        identities: set[int] = set()
        total = 0.0
        for raw_row in raw_rows:
            row = _stable_dict(raw_row)
            if row is None:
                return [], True
            value = _non_negative_int(row.get("value"))
            probability = _probability(row.get("probability"))
            if (
                value is None
                or probability is None
                or probability <= 0.0
                or value in identities
            ):
                return [], True
            identities.add(value)
            total += probability
            rows.append(MarginalCandidate(value=value, probability=probability))
        if total > 1.000001:
            return [], True
        rows.sort(key=lambda item: (-item.probability, item.value))
        return rows, False

    count_candidates, invalid_count_candidates = marginal_candidates(
        "count_candidates"
    )
    cells_candidates, invalid_cells_candidates = marginal_candidates(
        "cells_candidates"
    )
    compact_count_candidates, invalid_compact_count_candidates = marginal_candidates(
        "compact_count_candidates"
    )
    raw_compact_count_triplet = raw.get("compact_count_triplet")
    explicit_compact_count_triplet = "compact_count_triplet" in raw
    compact_count_triplet: list[int] = []
    invalid_compact_count_triplet = False
    if explicit_compact_count_triplet:
        if type(raw_compact_count_triplet) not in (list, tuple):
            invalid_compact_count_triplet = True
        else:
            compact_count_triplet = list(raw_compact_count_triplet)
            invalid_compact_count_triplet = (
                len(compact_count_triplet) not in {0, 3}
                or any(
                    not _has_exact_type(value, int) or value < 0
                    for value in compact_count_triplet
                )
            )
    count_correlation_state, count_correlation_state_valid = _text_projection(
        raw.get("count_correlation_state")
    )
    count_correlation_state = count_correlation_state.lower()
    count_correlation_note, count_correlation_note_valid = _text_projection(
        raw.get("count_correlation_note")
    )
    explicit_count_correlation = any(
        field in raw
        for field in (
            "compact_count_candidates",
            "compact_count_triplet",
            "count_correlation_state",
            "count_correlation_note",
        )
    )
    candidate_count_values = {candidate.count for candidate in candidates}
    invalid_count_correlation = explicit_count_correlation and (
        not count_correlation_state_valid
        or not count_correlation_note_valid
        or count_correlation_state not in COUNT_CORRELATION_STATES
        or (
            count_correlation_state in {"independent", "unknown"}
            and (
                bool(compact_count_candidates)
                or bool(compact_count_triplet)
            )
        )
        or (
            count_correlation_state in {"complete", "partial"}
            and (
                not compact_count_candidates
                or (
                    explicit_compact_count_triplet
                    and len(compact_count_triplet) != 3
                )
            )
        )
        or any(
            row.value not in candidate_count_values
            for row in compact_count_candidates
        )
        or any(
            value not in candidate_count_values
            for value in compact_count_triplet
        )
    )

    def marginal_covers_joint(
        marginal: list[MarginalCandidate],
        *,
        dimension: str,
    ) -> bool:
        if not marginal:
            return True
        marginal_by_value = {row.value: row.probability for row in marginal}
        visible_by_value: dict[int, float] = {}
        for candidate in candidates:
            value = getattr(candidate, dimension)
            visible_by_value[value] = (
                visible_by_value.get(value, 0.0) + candidate.probability
            )
        return all(
            value in marginal_by_value
            and marginal_by_value[value] + 0.000001 >= probability
            for value, probability in visible_by_value.items()
        )

    inconsistent_marginals = not marginal_covers_joint(
        count_candidates,
        dimension="count",
    ) or not marginal_covers_joint(
        cells_candidates,
        dimension="cells",
    )
    visible_probability = sum(item.probability for item in candidates)
    shown_value = raw.get("shown_probability")
    other_value = raw.get("other_probability")
    shown_probability = _probability(shown_value)
    other_probability = _probability(other_value)
    invalid_probability_field = (
        ("shown_probability" in raw and shown_probability is None)
        or ("other_probability" in raw and other_probability is None)
    )
    completion_flags_complete = (
        "truncated" in raw
        and _has_exact_type(raw.get("truncated"), bool)
        and "cap_hit" in raw
        and _has_exact_type(raw.get("cap_hit"), bool)
    )
    # Completion flags are required evidence only for a claimed exact lock.
    # Missing/None/wrong-type must remain unknown in that state so a singleton
    # cannot silently inherit False and appear as a 100% unique lock. Non-exact
    # legacy states keep the existing absent-as-False normalization.
    exact_flag_default = None if status == "exact" else False
    truncated = _boolean(raw.get("truncated"), default=exact_flag_default)
    cap_hit = _boolean(raw.get("cap_hit"), default=exact_flag_default)
    pricing_posterior = _boolean(raw.get("pricing_posterior"))
    display_reconciled = _boolean(raw.get("display_reconciled"))
    discrete_count_display = _boolean(raw.get("discrete_count_display"))

    count_lock_present = "count_lock_value" in raw
    cells_lock_present = "cells_lock_value" in raw
    count_lock_value = (
        _non_negative_int(raw.get("count_lock_value"))
        if raw.get("count_lock_value") is not None
        else None
    )
    cells_lock_value = (
        _non_negative_int(raw.get("cells_lock_value"))
        if raw.get("cells_lock_value") is not None
        else None
    )
    dimension_state, dimension_state_valid = _text_projection(
        raw.get("dimension_state")
    )
    expected_dimension_state = (
        "锁定"
        if count_lock_value is not None and cells_lock_value is not None
        else "件锁"
        if count_lock_value is not None
        else "格锁"
        if cells_lock_value is not None
        else "未锁"
    )
    explicit_dimension_state = (
        count_lock_present or cells_lock_present or "dimension_state" in raw
    )
    locked_value_conflict = (
        count_lock_value is not None
        and (
            any(candidate.count != count_lock_value for candidate in candidates)
            or any(
                candidate.value != count_lock_value
                for candidate in count_candidates
            )
        )
    ) or (
        cells_lock_value is not None
        and (
            any(candidate.cells != cells_lock_value for candidate in candidates)
            or any(
                candidate.value != cells_lock_value
                for candidate in cells_candidates
            )
        )
    )
    invalid_dimension_state = (
        (
            count_lock_present
            and raw.get("count_lock_value") is not None
            and count_lock_value is None
        )
        or (
            cells_lock_present
            and raw.get("cells_lock_value") is not None
            and cells_lock_value is None
        )
        or not dimension_state_valid
        or locked_value_conflict
        or (
            explicit_dimension_state
            and dimension_state != expected_dimension_state
        )
    )

    inconsistent_probability = visible_probability > 1.000001
    if shown_probability is not None:
        inconsistent_probability = inconsistent_probability or (
            abs(shown_probability - visible_probability) > 0.000001
        )
    if shown_probability is not None and other_probability is not None:
        inconsistent_probability = inconsistent_probability or (
            abs(shown_probability + other_probability - 1.0) > 0.000001
        )

    exact_marginals_valid = True
    if len(candidates) == 1:
        joint = candidates[0]
        exact_marginals_valid = all(
            not marginal
            or (
                len(marginal) == 1
                and marginal[0].value == expected
                and marginal[0].probability == 1.0
            )
            for marginal, expected in (
                (count_candidates, joint.count),
                (cells_candidates, joint.cells),
            )
        )
    exact_invalid = status == "exact" and (
        not _strict_exact_fields(
            status=status,
            lock_strength=lock_strength,
            candidates=candidates,
            shown_probability=shown_probability,
            other_probability=other_probability,
            truncated=truncated,
            cap_hit=cap_hit,
            completion_flags_complete=completion_flags_complete,
        )
        or not exact_marginals_valid
    )
    ready_without_candidates = status in {"ready", "exact"} and not candidates
    invalid_structure = (
        invalid_candidate
        or invalid_count_candidates
        or invalid_cells_candidates
        or invalid_compact_count_candidates
        or invalid_compact_count_triplet
        or invalid_count_correlation
        or inconsistent_marginals
        or invalid_probability_field
        or inconsistent_probability
        or exact_invalid
        or ready_without_candidates
        or truncated is None
        or cap_hit is None
        or pricing_posterior is None
        or display_reconciled is None
        or discrete_count_display is None
        or not status_valid
        or not status_known
        or not lock_strength_valid
        or not lock_strength_known
        or not source_valid
        or not lock_reason_valid
        or not reason_valid
        or invalid_dimension_state
    )
    if invalid_structure:
        status = "conflict"
        lock_strength = "unavailable"
        candidates = []
        count_candidates = []
        cells_candidates = []
        shown_probability = None
        other_probability = None
        count_lock_value = None
        cells_lock_value = None
        dimension_state = ""
        discrete_count_display = False
        compact_count_candidates = []
        compact_count_triplet = []
        count_correlation_state = ""
        count_correlation_note = ""

    if status == "conflict" and not reason:
        reason = "结构化组合字段冲突"

    return QualityComposition(
        key=key,
        label=QUALITY_LABELS[key],
        status=status,
        source=source,
        lock_strength=lock_strength,
        lock_reason=lock_reason,
        candidates=tuple(candidates),
        count_candidates=tuple(count_candidates),
        cells_candidates=tuple(cells_candidates),
        shown_probability=shown_probability,
        other_probability=other_probability,
        truncated=bool(truncated) if truncated is not None else False,
        cap_hit=bool(cap_hit) if cap_hit is not None else False,
        completion_flags_complete=completion_flags_complete,
        pricing_posterior=(
            bool(pricing_posterior)
            if pricing_posterior is not None
            else False
        ),
        display_reconciled=(
            bool(display_reconciled)
            if display_reconciled is not None
            else False
        ),
        reason=reason,
        count_lock_value=count_lock_value,
        cells_lock_value=cells_lock_value,
        dimension_state=(
            dimension_state if explicit_dimension_state else ""
        ),
        discrete_count_display=(
            bool(discrete_count_display)
            if discrete_count_display is not None
            else False
        ),
        compact_count_candidates=tuple(compact_count_candidates),
        compact_count_triplet=tuple(compact_count_triplet),
        count_correlation_state=(
            count_correlation_state if explicit_count_correlation else ""
        ),
        count_correlation_note=(
            count_correlation_note if explicit_count_correlation else ""
        ),
    )


def _root_status_from_qualities(
    declared_status: str,
    qualities: tuple[QualityComposition, ...],
) -> str:
    statuses = {quality.status for quality in qualities}
    if "conflict" in statuses:
        return "conflict"

    for stronger_status in (
        "error",
        "stale",
        "loading",
        "no_reachable",
    ):
        if stronger_status in statuses and declared_status in {
            "",
            "ready",
            "exact",
        }:
            return stronger_status

    if declared_status in QUALITY_STATUSES:
        if declared_status in {"ready", "exact"} and statuses <= {"unavailable"}:
            return "unavailable"
        return declared_status

    if "conflict" in statuses:
        return "conflict"
    if "error" in statuses:
        return "error"
    if "stale" in statuses:
        return "stale"
    if "loading" in statuses:
        return "loading"
    if "no_reachable" in statuses:
        return "no_reachable"
    if statuses <= {"unavailable"}:
        return "unavailable"
    return "ready"


def _root_reason(status: str) -> str:
    return {
        "loading": "组合计算中",
        "error": "组合暂不可用",
        "conflict": "组合数据存在冲突",
        "no_reachable": "没有可达组合",
        "stale": "显示的是旧组合结果",
        "unavailable": "等待引擎提供结构化组合",
    }.get(status, "")


def _conflict_view(
    reason: str,
    *,
    quality_reason: str | None = None,
) -> DetailCompositionView:
    return DetailCompositionView(
        status="conflict",
        reason=reason,
        qualities=tuple(
            _empty_quality(
                key,
                status="conflict",
                reason=quality_reason or reason,
            )
            for key in QUALITY_ORDER
        ),
    )


def _compact_count_arms_valid(
    raw_block: dict[str, Any],
    raw_qualities: dict[str, Any],
    qualities: tuple[QualityComposition, ...],
) -> bool:
    """Validate the root arm matrix before any per-quality triplet is rendered."""
    for quality in qualities:
        if quality.status not in {"ready", "exact"}:
            continue
        raw_quality = _stable_dict(raw_qualities.get(quality.key))
        if raw_quality is None or not _COUNT_CORRELATION_FIELDS.issubset(raw_quality):
            return False

    has_total = "count_correlation_total" in raw_block
    has_authority = "authoritative_count_arms" in raw_block
    has_arms = "compact_count_arms" in raw_block
    if not has_total and not has_authority and not has_arms:
        return all(
            quality.status not in {"ready", "exact"}
            or (
                quality.count_correlation_state in {"independent", "unknown"}
                and not quality.compact_count_candidates
                and not quality.compact_count_triplet
            )
            for quality in qualities
        )
    if not has_total or not has_authority or not has_arms:
        return False
    total = raw_block.get("count_correlation_total")
    raw_authoritative_arms = raw_block.get("authoritative_count_arms")
    raw_arms = raw_block.get("compact_count_arms")
    if (
        not _has_exact_type(total, int)
        or total < 0
        or type(raw_authoritative_arms) is not list  # noqa: E721
        or len(raw_authoritative_arms) > 4_096
        or type(raw_arms) is not list  # noqa: E721
        or len(raw_arms) not in {0, 3}
    ):
        return False

    def parse_arm(raw_arm: Any) -> tuple[int, ...] | None:
        if (
            type(raw_arm) is not dict  # noqa: E721
            or any(type(raw_key) is not str for raw_key in raw_arm)  # noqa: E721
            or set(raw_arm) != set(QUALITY_ORDER)
        ):
            return None
        arm = tuple(dict.get(raw_arm, key) for key in QUALITY_ORDER)
        if (
            any(not _has_exact_type(value, int) or value < 0 for value in arm)
            or sum(arm) != total
        ):
            return None
        return arm

    authoritative_arms: set[tuple[int, ...]] = set()
    for raw_arm in raw_authoritative_arms:
        arm = parse_arm(raw_arm)
        if arm is None or arm in authoritative_arms:
            return False
        authoritative_arms.add(arm)
    if not raw_arms:
        return all(
            quality.count_correlation_state in {"", "unknown"}
            and not quality.compact_count_triplet
            for quality in qualities
        )
    if not authoritative_arms:
        return False

    arms: list[tuple[int, ...]] = []
    for raw_arm in raw_arms:
        arm = parse_arm(raw_arm)
        if arm is None or arm not in authoritative_arms:
            return False
        arms.append(arm)

    for index, quality in enumerate(qualities):
        candidate_counts = {candidate.count for candidate in quality.candidates}
        expected_triplet = tuple(arm[index] for arm in arms)
        if any(value not in candidate_counts for value in expected_triplet):
            return False
        if quality.count_correlation_state in {"independent", "unknown"}:
            if quality.compact_count_triplet:
                return False
        elif quality.count_correlation_state in {"complete", "partial"}:
            if quality.compact_count_triplet != expected_triplet:
                return False
        else:
            return False
    return True


def detail_composition_view(summary: Any) -> DetailCompositionView:
    """Normalize the additive details contract without deriving posterior states.

    The UI intentionally consumes only ``quality_composition``.  Legacy marginal
    count/cell candidates, notes and display strings are not inspected here, so the
    frontend cannot manufacture a joint state that the pricing posterior never emitted.
    """

    if summary is None:
        summary_map: dict[str, Any] = {}
    else:
        stable_summary = _stable_dict(summary)
        if stable_summary is None:
            return _conflict_view(
                "组合数据存在冲突",
                quality_reason="组合数据容器类型冲突",
            )
        summary_map = stable_summary
    if "quality_composition" not in summary_map:
        reason, reason_valid = _text_projection(
            summary_map.get("quality_composition_reason"),
            "等待引擎提供结构化组合",
        )
        if not reason_valid:
            return _conflict_view("组合数据原因字段类型冲突")
        return DetailCompositionView(
            status="unavailable",
            reason=reason,
            qualities=tuple(
                _empty_quality(key, reason=reason) for key in QUALITY_ORDER
            ),
        )

    raw_block_value = summary_map.get("quality_composition")
    raw_block = _stable_dict(raw_block_value)
    if raw_block is None:
        return _conflict_view(
            "组合数据存在冲突",
            quality_reason="组合数据容器类型冲突",
        )
    if not raw_block:
        reason, reason_valid = _text_projection(
            summary_map.get("quality_composition_reason"),
            "等待引擎提供结构化组合",
        )
        if not reason_valid:
            return _conflict_view("组合数据原因字段类型冲突")
        return DetailCompositionView(
            status="unavailable",
            reason=reason,
            qualities=tuple(
                _empty_quality(key, reason=reason) for key in QUALITY_ORDER
            ),
        )

    # Every ready producer in repository history has used the nested ``qualities``
    # envelope.  The former flat fallback was consumer-only and carried no
    # independently verifiable schema/generation, so accepting it would let a
    # current payload delete its marker and joint authority to regain marginal
    # rendering.  Keep only the proven non-ready placeholder compatibility below.
    if "qualities" not in raw_block:
        raw_status = dict.get(raw_block, "status")
        legacy_nonready = (
            "schema_version" not in raw_block
            and type(raw_status) is str  # noqa: E721 - exact scalar trust boundary
            and raw_status.lower()
            in {"unavailable", "loading", "error", "stale", "no_reachable"}
            and not any(key in raw_block for key in QUALITY_ORDER)
        )
        if not legacy_nonready:
            return _conflict_view("品质组合 schema 代际冲突")
        raw_qualities: dict[str, Any] = {}
    else:
        schema_version = dict.get(raw_block, "schema_version")
        if (
            type(schema_version) is not int  # noqa: E721 - reject bool/subclasses
            or schema_version != QUALITY_COMPOSITION_SCHEMA_VERSION
        ):
            return _conflict_view("品质组合 schema 代际冲突")
        stable_qualities = _stable_dict(dict.get(raw_block, "qualities"))
        if stable_qualities is None:
            return _conflict_view(
                "组合数据存在冲突",
                quality_reason="qualities 字段类型冲突",
            )
        raw_qualities = stable_qualities

    qualities = tuple(
        (
            _quality_view(key, raw_qualities[key])
            if key in raw_qualities
            else _empty_quality(key)
        )
        for key in QUALITY_ORDER
    )

    if not _compact_count_arms_valid(raw_block, raw_qualities, qualities):
        return _conflict_view("品质件数联合臂字段冲突")

    declared_status, declared_status_valid = _text_projection(
        raw_block.get("status")
    )
    declared_status = declared_status.lower()
    root_status = _root_status_from_qualities(declared_status, qualities)
    reason, root_reason_valid = _text_projection(raw_block.get("reason"))
    if not declared_status_valid or not root_reason_valid:
        root_status = "conflict"
    if root_status != declared_status or not reason:
        reason = _root_reason(root_status)
    if root_status == "conflict":
        return _conflict_view(reason or _root_reason("conflict"))

    return DetailCompositionView(
        status=root_status,
        reason=reason,
        qualities=qualities,
    )


def format_probability(value: float | None) -> str:
    if value is None:
        return "-"
    if not _has_exact_type(value, float):
        return "-"
    percent = value * 100.0
    if percent == 0.0:
        return "0%"
    if percent < 0.1:
        return "<0.1%"
    if value == 1.0:
        return "100%"
    if percent >= 99.95:
        return "<100%"
    return f"{percent:.1f}%"


def format_quality_probability(
    quality: QualityComposition | None,
    value: float | None,
) -> str:
    """Reserve literal 100% for a complete, strict joint lock."""
    if not _has_exact_type(value, float):
        return "-"
    if value == 1.0 and not quality_is_strict_exact(quality):
        return "<100%"
    return format_probability(value)


def _quality_count_distribution(
    quality: QualityComposition | None,
) -> tuple[MarginalCandidate, ...]:
    if quality is None or quality.status == "conflict":
        return ()
    if quality.count_candidates:
        return quality.count_candidates
    probabilities: dict[int, float] = {}
    for candidate in quality.candidates:
        probabilities[candidate.count] = (
            probabilities.get(candidate.count, 0.0) + candidate.probability
        )
    return tuple(
        MarginalCandidate(value=value, probability=probability)
        for value, probability in sorted(
            probabilities.items(),
            key=lambda item: (-item[1], item[0]),
        )
    )


def _quality_cells_distribution(
    quality: QualityComposition | None,
) -> tuple[MarginalCandidate, ...]:
    if quality is None or quality.status == "conflict":
        return ()
    if quality.cells_candidates:
        return quality.cells_candidates
    probabilities: dict[int, float] = {}
    for candidate in quality.candidates:
        probabilities[candidate.cells] = (
            probabilities.get(candidate.cells, 0.0) + candidate.probability
        )
    return tuple(
        MarginalCandidate(value=value, probability=probability)
        for value, probability in sorted(
            probabilities.items(),
            key=lambda item: (-item[1], item[0]),
        )
    )


def _quality_dimension_distribution(
    quality: QualityComposition | None,
    dimension: str,
) -> tuple[MarginalCandidate, ...]:
    if dimension == "count":
        return _quality_count_distribution(quality)
    if dimension == "cells":
        return _quality_cells_distribution(quality)
    return ()


def _quality_display_dimension_distribution(
    quality: QualityComposition | None,
    dimension: str,
) -> tuple[MarginalCandidate, ...]:
    """Use the total-constrained count projection for persistent UI readouts."""
    if (
        dimension == "count"
        and quality is not None
        and quality.count_correlation_state
    ):
        if quality.count_correlation_state == "unknown":
            return ()
        if quality.count_correlation_state in {"complete", "partial"}:
            return quality.compact_count_candidates
    return _quality_dimension_distribution(quality, dimension)


def quality_dimension_is_exact(
    quality: QualityComposition | None,
    dimension: str,
) -> bool:
    """Return whether one displayed dimension is complete and uniquely known."""
    if quality is None or quality.status not in {"ready", "exact"}:
        return False
    # New panel payloads carry an explicit per-dimension authority projection.
    # Once present, a null lock value is an intentional fail-closed result, not
    # permission for the client to reconstruct a lock from visible marginals.
    if quality.dimension_state:
        if dimension == "count":
            return quality.count_lock_value is not None
        if dimension == "cells":
            return quality.cells_lock_value is not None
        return False
    if dimension == "count" and quality.count_lock_value is not None:
        return True
    if dimension == "cells" and quality.cells_lock_value is not None:
        return True
    if (
        quality.truncated
        or quality.cap_hit
        or not quality.completion_flags_complete
        or quality.other_probability != 0.0
    ):
        return False
    rows = _quality_dimension_distribution(quality, dimension)
    return (
        len(rows) == 1
        and _has_exact_type(rows[0], MarginalCandidate)
        and rows[0].probability == 1.0
    )


def quality_dimension_triplet(
    quality: QualityComposition | None,
    dimension: str,
) -> tuple[int, int, int] | None:
    if (
        dimension == "count"
        and quality is not None
        and quality.count_correlation_state in {"complete", "partial", "unknown"}
    ):
        return _visible_compact_count_triplet(quality)
    rows = _quality_display_dimension_distribution(quality, dimension)
    if not rows:
        return None
    values = tuple(row.value for row in rows)
    return min(values), rows[0].value, max(values)


def quality_count_triplet(
    quality: QualityComposition | None,
) -> tuple[int, int, int] | None:
    """Return conservative / most-likely / aggressive reachable counts.

    The middle value follows the canonical marginal ordering (probability first,
    value second); the outer values preserve the full reachable count span.  This
    deliberately does not infer an exact lock from a visually collapsed triplet.
    """
    return quality_dimension_triplet(quality, "count")


def quality_display_count_triplet(
    quality: QualityComposition | None,
    *,
    fallback: Any = None,
) -> tuple[int, int, int] | None:
    """Keep the familiar three-tier count line while structured state guards lock copy."""
    if quality is not None and quality.status == "conflict":
        return None
    structured = quality_count_triplet(quality)
    if quality_is_strict_exact(quality):
        return structured
    if (
        quality is not None
        and quality.count_correlation_state in {"complete", "partial", "unknown"}
    ):
        # New payloads explicitly distinguish a total-constrained visible arm
        # from missing joint evidence.  Never revive the legacy independent
        # marginal/range fallback once that state is present.
        return structured
    fallback_text = fallback.strip() if _has_exact_type(fallback, str) else ""
    fallback_parts = tuple(
        part for part in fallback_text.replace(" ", "").split("/") if part
    )
    if len(fallback_parts) == 3 and all(part.isdecimal() for part in fallback_parts):
        fallback_triplet = tuple(int(part) for part in fallback_parts)
        if structured is not None:
            # Preserve the familiar conservative/aggressive envelope while the
            # emphasized centre comes from the canonical highest-probability count.
            # Negligible tails therefore stay in hover/details instead of stretching
            # the main card.
            low, _legacy_middle, high = fallback_triplet
            most_likely = structured[1]
            if low <= most_likely <= high:
                return low, most_likely, high
        return fallback_triplet
    return structured


def format_quality_dimension_state_badge(
    quality: QualityComposition | None,
) -> str:
    """Describe count/cell certainty without hiding a locked dimension."""
    if not _has_exact_type(quality, QualityComposition):
        return ""
    if quality.status == "conflict":
        return "冲突"
    count_exact = quality_dimension_is_exact(quality, "count")
    cells_exact = quality_dimension_is_exact(quality, "cells")
    if count_exact and cells_exact:
        return "锁定"
    if count_exact:
        return "件锁"
    if cells_exact:
        return "格锁"
    if quality.status in {"ready", "exact"} and quality.candidates:
        return "未锁"
    return ""


def format_compact_quality_footprint(
    quality: QualityComposition | None,
    *,
    compact_range: bool = False,
) -> str:
    """Format one quality without inventing values between discrete candidates.

    A complete dense integer count domain may use a compact envelope.  Sparse
    counts created by fractional averages instead show the physical low /
    most-likely / high joint rows.  Repeating an endpoint (for example
    ``8/16/16``) is intentional: it preserves the three visible arms without
    implying that the impossible integers between 8 and 16 exist.  Cell bounds
    keep the established compact envelope; hover/details retain the full joint
    posterior.
    """
    if quality is not None and quality.status == "conflict":
        return "—件·—格"
    if quality is not None and quality.candidates and any(
        not composition_candidate_is_physical(row) for row in quality.candidates
    ):
        return "—件·—格"
    paired = quality_compact_paired_candidates(quality)
    if not paired:
        return "—件·—格"
    if len(paired) == 1 and not quality_is_strict_exact(quality):
        candidate = paired[0]
        return f"约{candidate.count}件·约{candidate.cells}格"

    all_candidates = tuple(quality.candidates) if quality is not None else paired
    low = min(all_candidates, key=lambda row: (row.count, row.cells, -row.probability))
    middle = max(
        all_candidates,
        key=lambda row: (row.probability, -row.count, -row.cells),
    )
    high = max(all_candidates, key=lambda row: (row.count, row.cells, row.probability))
    visible_triplet = (low, middle, high)

    def _dimension_text(field: str) -> str:
        values = sorted({getattr(row, field) for row in all_candidates})
        if len(values) == 1:
            return str(values[0])
        if (
            field != "count"
            or not quality.discrete_count_display
            or values[-1] - values[0] + 1 == len(values)
        ):
            return f"{values[0]}–{values[-1]}"
        return "/".join(str(getattr(row, field)) for row in visible_triplet)

    return f"{_dimension_text('count')}件·{_dimension_text('cells')}格"


def format_quality_count_candidates(
    quality: QualityComposition | None,
) -> str:
    rows = _quality_count_distribution(quality)
    return " · ".join(
        f"{row.value}件 {format_quality_probability(quality, row.probability)}"
        for row in rows
    )


def format_compact_quality_count(
    quality: QualityComposition | None,
    *,
    fallback: Any = "-",
) -> str:
    """Format the persistent count readout without inferring an exact lock.

    The percentile triplet is only a backward-compatible estimate.  Exact/100%
    wording is authorized exclusively by the complete structured joint contract.
    """
    if quality is not None and quality.status == "conflict":
        return "组合冲突"
    if (
        quality is not None
        and quality.count_correlation_state == "unknown"
    ):
        return "联动待定"
    triplet = quality_display_count_triplet(quality, fallback=fallback)
    if quality_dimension_is_exact(quality, "count") and triplet:
        return f"{triplet[1]}件"
    if triplet:
        low, middle, high = triplet
        if (
            quality is not None
            and quality.key == "q6"
            and quality.count_lock_value is None
            and quality.count_correlation_state == "complete"
        ):
            # Complete joint arms are useful reference evidence, but they are
            # not a residual count lock while the arms disagree on q6.
            return f"待定 · {low}/{middle}/{high}件"
        return f"保 {low} / 均 {middle} / 激 {high}件"
    return "-"
