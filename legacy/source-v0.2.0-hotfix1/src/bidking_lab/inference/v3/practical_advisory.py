"""Practical v3 advisory synthesis.

This layer is intentionally shadow-only. It converts existing v3 lanes into a
single practical advisory surface for archive/live review, without changing the
formal bid path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bidking_lab.inference.map_likelihood import QuantileSummary
from bidking_lab.inference.v3.capacity_source_expansion import (
    V3CapacitySourceExpansionReport,
)
from bidking_lab.inference.v3.formal_value_sampler import (
    V3FormalValueSamplerReport,
)
from bidking_lab.inference.v3.posterior import (
    V3PosteriorReport,
    empty_posterior_flat_dict,
)
from bidking_lab.inference.v3.settlement_count_prior import (
    V3SettlementCountPriorReport,
)
from bidking_lab.inference.v3.tail_value_review import V3TailValueReviewReport
from bidking_lab.inference.v3.underestimate_repair import (
    V3UnderestimateRepairReport,
)

_Q6_PRIOR_FLOOR_MIN_VALUE_GAP = 100_000.0
_Q6_PRIOR_TAIL_CEILING_MIN_PRESENT_RATE = 0.90
_Q6_PRIOR_TAIL_CEILING_MULTIPLIER = 2.5
_Q6_PRIOR_TAIL_CEILING_MIN_P90_GAP = 100_000.0
_Q6_PRIOR_TAIL_CEILING_MAX_DELTA = 750_000.0
_TAIL_REPLACEMENT_MIN_P90_GAP = 50_000.0
_RANDOM_AVG_VALUE_SIGNAL_FLOOR = 20_000.0
_RANDOM_AVG_VALUE_MIN_P50_GAP = 100_000.0
_RANDOM_AVG_VALUE_MIN_P90_GAP = 50_000.0
_RANDOM_AVG_HIGH_SIGNAL_AVG_FLOOR = 80_000.0
_RANDOM_AVG_HIGH_SIGNAL_MULTIPLIER = 2.5
_RANDOM_AVG_HIGH_SIGNAL_MIN_P90_GAP = 100_000.0
_RANDOM_AVG_HIGH_SIGNAL_MAX_DELTA = 400_000.0
_Q6_VALUE_CEILING_MIN_P50_GAP = 100_000.0
_Q6_VALUE_CEILING_MIN_P90_GAP = 100_000.0
_Q6_VALUE_RAISE_MIN_P50_GAP = 200_000.0
_Q6_VALUE_RAISE_MIN_P90_GAP = 200_000.0
_Q6_VALUE_CEILING_MAX_DELTA = 400_000.0
_Q6_RAW_TAIL_LOW_SUPPORT_MAX_MATCHED = 2
_Q6_RAW_TAIL_LOW_SUPPORT_MIN_P90_GAP = 200_000.0
_Q6_RAW_TAIL_LOW_SUPPORT_MAX_DELTA = 600_000.0
_Q6_RAW_TAIL_VALUE_STRESS_MIN_P90_GAP = 300_000.0
_Q6_RAW_TAIL_VALUE_STRESS_MAX_DELTA = 300_000.0
_SOURCE_PROFILE_Q6_TAIL_CEILING_RULES = {
    ("ethan", 2501, "public:random_avg+shape"): {
        "min_q6_present_rate": 0.85,
        "min_raw_total_p90_gap": 100_000.0,
        "p90_delta": 400_000.0,
    },
    ("ethan", 2506, "shape"): {
        "min_q6_present_rate": 0.85,
        "min_raw_total_p90_gap": 100_000.0,
        "p90_delta": 500_000.0,
    },
    ("ethan", 2401, "item+shape"): {
        "min_q6_present_rate": 0.0,
        "min_raw_total_p90_gap": 0.0,
        "min_shape_anchors": 33,
        "p90_delta": 1_000_000.0,
    },
    ("aisha", 2506, "item+shape"): {
        "min_q6_present_rate": 0.0,
        "min_raw_total_p90_gap": 0.0,
        "min_shape_anchors": 28,
        "min_item_anchors": 4,
        "p90_delta": 500_000.0,
    },
}


@dataclass(frozen=True)
class V3PracticalAdvisoryReport:
    baseline: V3PosteriorReport | None
    advisory: V3PosteriorReport | None
    source: str
    mode: str
    status: str
    recommendation: str
    confidence: str
    source_lanes: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()
    reason: str = ""

    @property
    def available(self) -> bool:
        return True

    @property
    def ready(self) -> bool:
        return self.baseline is not None and self.baseline.ready

    @property
    def active(self) -> bool:
        return False

    @property
    def affects_bid(self) -> bool:
        return False

    @property
    def candidate(self) -> bool:
        return self.ready and self.status not in {
            "not_ready",
            "baseline_passthrough",
        }

    def to_flat_dict(self, *, prefix: str = "v3_practical_") -> dict[str, Any]:
        out = (
            empty_posterior_flat_dict(prefix=prefix)
            if self.advisory is None
            else self.advisory.to_flat_dict(prefix=prefix)
        )
        baseline_formal = _formal(self.baseline)
        advisory_formal = _formal(self.advisory)
        baseline_total = _total_value(self.baseline)
        advisory_total = _total_value(self.advisory)
        baseline_q6_value = _q6_value(self.baseline)
        advisory_q6_value = _q6_value(self.advisory)
        baseline_q6_formal = _q6_formal(self.baseline)
        advisory_q6_formal = _q6_formal(self.advisory)
        out.update(
            {
                f"{prefix}available": self.available,
                f"{prefix}ready": self.ready,
                f"{prefix}affects_bid": self.affects_bid,
                f"{prefix}active": self.active,
                f"{prefix}candidate": self.candidate,
                f"{prefix}source": self.source,
                f"{prefix}mode": self.mode,
                f"{prefix}status": self.status,
                f"{prefix}recommendation": self.recommendation,
                f"{prefix}confidence": self.confidence,
                f"{prefix}source_lanes": "+".join(self.source_lanes),
                f"{prefix}risk_flags": "+".join(self.risk_flags),
                f"{prefix}reason": self.reason,
                f"{prefix}baseline_formal_decision_value_p50": _q(
                    baseline_formal,
                    "p50",
                ),
                f"{prefix}baseline_formal_decision_value_p90": _q(
                    baseline_formal,
                    "p90",
                ),
                f"{prefix}delta_formal_decision_value_p50": _delta(
                    advisory_formal,
                    baseline_formal,
                    "p50",
                ),
                f"{prefix}delta_formal_decision_value_p90": _delta(
                    advisory_formal,
                    baseline_formal,
                    "p90",
                ),
                f"{prefix}baseline_total_value_p90": _q(
                    baseline_total,
                    "p90",
                ),
                f"{prefix}delta_total_value_p90": _delta(
                    advisory_total,
                    baseline_total,
                    "p90",
                ),
                f"{prefix}raw_total_gap_to_formal_p90": _delta(
                    advisory_total,
                    advisory_formal,
                    "p90",
                ),
                f"{prefix}baseline_raw_total_gap_to_formal_p90": _delta(
                    baseline_total,
                    baseline_formal,
                    "p90",
                ),
                f"{prefix}baseline_q6_formal_decision_value_p50": _q(
                    baseline_q6_formal,
                    "p50",
                ),
                f"{prefix}baseline_q6_formal_decision_value_p90": _q(
                    baseline_q6_formal,
                    "p90",
                ),
                f"{prefix}delta_q6_formal_decision_value_p50": _delta(
                    advisory_q6_formal,
                    baseline_q6_formal,
                    "p50",
                ),
                f"{prefix}delta_q6_formal_decision_value_p90": _delta(
                    advisory_q6_formal,
                    baseline_q6_formal,
                    "p90",
                ),
                f"{prefix}baseline_q6_value_p90": _q(
                    baseline_q6_value,
                    "p90",
                ),
                f"{prefix}delta_q6_value_p90": _delta(
                    advisory_q6_value,
                    baseline_q6_value,
                    "p90",
                ),
                f"{prefix}q6_raw_gap_to_formal_p90": _delta(
                    advisory_q6_value,
                    advisory_q6_formal,
                    "p90",
                ),
                f"{prefix}baseline_q6_raw_gap_to_formal_p90": _delta(
                    baseline_q6_value,
                    baseline_q6_formal,
                    "p90",
                ),
            }
        )
        return out


def advise_practical_report(
    posterior: V3PosteriorReport | None,
    *,
    ccv_posterior: V3PosteriorReport | None = None,
    ccv_component_posterior: V3PosteriorReport | None = None,
    residual_posterior: V3PosteriorReport | None = None,
    formal_value: V3FormalValueSamplerReport | None = None,
    underestimate: V3UnderestimateRepairReport | None = None,
    tail_review: V3TailValueReviewReport | None = None,
    settlement_count_prior: V3SettlementCountPriorReport | None = None,
    capacity_source_expansion: V3CapacitySourceExpansionReport | None = None,
    evidence_events: tuple[Any, ...] = (),
    hero: str | None = None,
    evidence_profile_key: str | None = None,
    item_anchor_count: int | None = None,
    shape_anchor_count: int | None = None,
) -> V3PracticalAdvisoryReport:
    if posterior is None or not posterior.ready:
        return V3PracticalAdvisoryReport(
            baseline=posterior,
            advisory=posterior,
            source="none",
            mode="not_ready",
            status="not_ready",
            recommendation="no_advice",
            confidence="none",
            reason="baseline_not_ready",
        )

    lanes: list[str] = []
    risks: list[str] = []
    reasons: list[str] = []
    source_profile = _normalized_evidence_profile_key(
        evidence_profile_key,
        evidence_events=evidence_events,
    )

    def apply_source_profile_ceiling(
        advisory: V3PosteriorReport,
    ) -> tuple[V3PosteriorReport, bool]:
        combined = _source_profile_q6_tail_ceiling_watch(
            advisory,
            hero=hero,
            evidence_profile_key=source_profile,
            item_anchor_count=item_anchor_count,
            shape_anchor_count=shape_anchor_count,
        )
        if combined is None:
            return advisory, False
        next_advisory, source_profile_reason = combined
        lanes.append("source_profile")
        risks.append("source_profile_q6_tail_ceiling")
        _replace_reason_prefix(
            reasons,
            "source_profile_q6_tail_ceiling_shadow_only:",
            source_profile_reason,
        )
        return next_advisory, True

    if formal_value is not None and formal_value.candidate:
        lanes.append("formal_value")
        risks.append("value_floor_candidate")
        reasons.append(formal_value.gate_reason)
    elif formal_value is not None and formal_value.mixed_value_floor_watch:
        lanes.append("formal_value")
        risks.append("mixed_value_floor_guard")
        reasons.append(formal_value.gate_reason)
    elif (
        formal_value is not None
        and "capacity_cells_drift" in formal_value.stress_classes
    ):
        lanes.append("formal_value")
        risks.append("capacity_cells_drift")
        reasons.append(formal_value.gate_reason)

    q6_prior_floor = _q6_prior_floor_watch(
        posterior,
        formal_value,
    )
    if q6_prior_floor is not None:
        lanes.append("formal_value")
        lanes.append("prior_q6_floor")
        risks.append("q6_prior_floor_watch")
        reasons.append(q6_prior_floor[1])

    tail_replacement_watch = _tail_replacement_p90_watch(posterior)
    if tail_replacement_watch is not None:
        lanes.append("tail_replacement")
        risks.append("tail_replacement_p90_watch")
        reasons.append(tail_replacement_watch[1])

    random_avg_watch = _random_avg_value_floor_watch(
        posterior,
        evidence_events=evidence_events,
    )
    if random_avg_watch is not None:
        lanes.append("random_avg_value")
        risks.append("random_avg_value_floor_watch")
        reasons.append(random_avg_watch[1])

    random_avg_high_signal_watch = _random_avg_high_signal_ceiling_watch(
        posterior,
        evidence_events=evidence_events,
    )
    if random_avg_high_signal_watch is not None:
        lanes.append("random_avg_value")
        risks.append("random_avg_high_signal_ceiling")
        reasons.append(random_avg_high_signal_watch[1])

    q6_value_ceiling_watch = _q6_value_ceiling_watch(
        posterior,
        ccv_posterior=ccv_posterior,
        ccv_component_posterior=ccv_component_posterior,
        residual_posterior=residual_posterior,
    )
    if q6_value_ceiling_watch is not None:
        lanes.append(q6_value_ceiling_watch[3])
        risks.append("q6_value_ceiling_watch")
        reasons.append(q6_value_ceiling_watch[1])

    q6_raw_tail_ceiling_watch = _q6_raw_tail_low_support_ceiling_watch(
        posterior,
        formal_value=formal_value,
    )
    if q6_raw_tail_ceiling_watch is not None:
        lanes.append("q6_raw_tail_value")
        risks.append("q6_raw_tail_low_support_ceiling")
        reasons.append(q6_raw_tail_ceiling_watch[1])

    q6_raw_tail_value_stress_watch = _q6_raw_tail_value_stress_ceiling_watch(
        posterior,
        formal_value=formal_value,
    )
    if q6_raw_tail_value_stress_watch is not None:
        lanes.append("q6_raw_tail_value")
        risks.append("q6_raw_tail_value_stress_ceiling")
        reasons.append(q6_raw_tail_value_stress_watch[1])

    if underestimate is not None and underestimate.candidate:
        lanes.append("underestimate")
        risks.append("underestimate_repair_candidate")
        if underestimate.entry is not None:
            reasons.append(underestimate.entry.gate_reason)

    if tail_review is not None and tail_review.candidate:
        lanes.append("tail_review")
        risks.append("tail_value_candidate")
        if tail_review.entry is not None:
            reasons.append(tail_review.entry.gate_reason)
    if tail_review is not None and tail_review.hurt_guard:
        lanes.append("tail_review")
        risks.append("tail_value_hurt_guard")
        if tail_review.entry is not None:
            reasons.append(tail_review.entry.gate_reason)

    if settlement_count_prior is not None and settlement_count_prior.candidate:
        lanes.append("settlement_count_prior")
        risks.append("settlement_count_prior_candidate")
        reasons.append(settlement_count_prior.gate_reason)
    if settlement_count_prior is not None and settlement_count_prior.missing_table:
        lanes.append("settlement_count_prior")
        risks.append("missing_settlement_count_table")
        reasons.append(settlement_count_prior.gate_reason)

    cse_fields = (
        capacity_source_expansion.to_flat_dict()
        if capacity_source_expansion is not None
        else {}
    )
    if bool(cse_fields.get("v3_cse_pressure_candidate")):
        lanes.append("capacity_source_expansion")
        risks.append("capacity_source_pressure")
        if capacity_source_expansion is not None:
            reasons.append(capacity_source_expansion.gate_reason)
    elif capacity_source_expansion is not None and capacity_source_expansion.candidate:
        lanes.append("capacity_source_expansion")
        risks.append("capacity_source_candidate")
        reasons.append(capacity_source_expansion.gate_reason)

    if formal_value is not None and formal_value.candidate:
        advisory = formal_value.posterior
        if advisory is not None:
            combined_q6_raw_tail = _q6_raw_tail_low_support_ceiling_watch(
                advisory,
                formal_value=formal_value,
            )
            if combined_q6_raw_tail is not None:
                advisory, q6_raw_tail_reason = combined_q6_raw_tail
                _replace_reason_prefix(
                    reasons,
                    "q6_raw_tail_low_support_ceiling_shadow_only:",
                    q6_raw_tail_reason,
                )
            combined_q6_value_stress = _q6_raw_tail_value_stress_ceiling_watch(
                advisory,
                formal_value=formal_value,
            )
            if combined_q6_value_stress is not None:
                advisory, q6_value_stress_reason = combined_q6_value_stress
                _replace_reason_prefix(
                    reasons,
                    "q6_raw_tail_value_stress_ceiling_shadow_only:",
                    q6_value_stress_reason,
                )
            advisory, _source_profile_applied = apply_source_profile_ceiling(advisory)
        return V3PracticalAdvisoryReport(
            baseline=posterior,
            advisory=advisory,
            source="formal_value",
            mode="value_floor_watch",
            status="watch_raise_candidate",
            recommendation="raise_watch",
            confidence="medium",
            source_lanes=_dedupe(lanes),
            risk_flags=_dedupe(risks),
            reason=_join_reasons(reasons),
        )
    if underestimate is not None and underestimate.candidate:
        advisory = underestimate.posterior
        source_profile_applied = False
        if advisory is not None:
            advisory, source_profile_applied = apply_source_profile_ceiling(advisory)
        return V3PracticalAdvisoryReport(
            baseline=posterior,
            advisory=advisory,
            source="underestimate",
            mode="bounded_underestimate_repair",
            status="watch_raise_candidate",
            recommendation="raise_watch" if source_profile_applied else "ceiling_watch",
            confidence="medium_low" if source_profile_applied else "low_medium",
            source_lanes=_dedupe(lanes),
            risk_flags=_dedupe(risks),
            reason=_join_reasons(reasons),
        )
    if q6_prior_floor is not None:
        advisory, _reason = q6_prior_floor
        combined_q6_prior_tail = _q6_prior_tail_ceiling_watch(
            advisory,
            formal_value=formal_value,
        )
        if combined_q6_prior_tail is not None:
            advisory, q6_prior_tail_reason = combined_q6_prior_tail
            lanes.append("prior_q6_tail")
            risks.append("q6_prior_tail_ceiling")
            _replace_reason_prefix(
                reasons,
                "q6_prior_tail_ceiling_shadow_only:",
                q6_prior_tail_reason,
            )
        combined_random_avg = _random_avg_value_floor_watch(
            advisory,
            evidence_events=evidence_events,
        )
        if combined_random_avg is not None:
            advisory, random_reason, _random_raise_watch = combined_random_avg
            if random_reason not in reasons:
                reasons.append(random_reason)
        combined_random_avg_high_signal = _random_avg_high_signal_ceiling_watch(
            advisory,
            evidence_events=evidence_events,
        )
        if combined_random_avg_high_signal is not None:
            advisory, random_high_reason = combined_random_avg_high_signal
            _replace_reason_prefix(
                reasons,
                "random_avg_high_signal_ceiling_shadow_only:",
                random_high_reason,
            )
        combined_q6_value = _q6_value_ceiling_watch(
            advisory,
            ccv_posterior=ccv_posterior,
            ccv_component_posterior=ccv_component_posterior,
            residual_posterior=residual_posterior,
        )
        if combined_q6_value is not None:
            advisory, q6_value_reason, _q6_value_raise_watch, _source = (
                combined_q6_value
            )
            if q6_value_reason not in reasons:
                reasons.append(q6_value_reason)
        combined_q6_raw_tail = _q6_raw_tail_low_support_ceiling_watch(
            advisory,
            formal_value=formal_value,
        )
        if combined_q6_raw_tail is not None:
            advisory, q6_raw_tail_reason = combined_q6_raw_tail
            _replace_reason_prefix(
                reasons,
                "q6_raw_tail_low_support_ceiling_shadow_only:",
                q6_raw_tail_reason,
            )
        combined_q6_value_stress = _q6_raw_tail_value_stress_ceiling_watch(
            advisory,
            formal_value=formal_value,
        )
        if combined_q6_value_stress is not None:
            advisory, q6_value_stress_reason = combined_q6_value_stress
            _replace_reason_prefix(
                reasons,
                "q6_raw_tail_value_stress_ceiling_shadow_only:",
                q6_value_stress_reason,
            )
        advisory, _source_profile_applied = apply_source_profile_ceiling(advisory)
        return V3PracticalAdvisoryReport(
            baseline=posterior,
            advisory=advisory,
            source="q6_prior_floor",
            mode="q6_prior_floor_watch",
            status="watch_q6_prior_floor",
            recommendation="raise_watch",
            confidence="low_medium",
            source_lanes=_dedupe(lanes),
            risk_flags=_dedupe(risks),
            reason=_join_reasons(reasons),
        )
    if random_avg_watch is not None:
        advisory, _reason, random_raise_watch = random_avg_watch
        combined_q6_value = _q6_value_ceiling_watch(
            advisory,
            ccv_posterior=ccv_posterior,
            ccv_component_posterior=ccv_component_posterior,
            residual_posterior=residual_posterior,
        )
        if combined_q6_value is not None:
            advisory, q6_value_reason, q6_value_raise_watch, _source = (
                combined_q6_value
            )
            random_raise_watch = random_raise_watch or q6_value_raise_watch
            if q6_value_reason not in reasons:
                reasons.append(q6_value_reason)
        combined_random_avg_high_signal = _random_avg_high_signal_ceiling_watch(
            advisory,
            evidence_events=evidence_events,
        )
        if combined_random_avg_high_signal is not None:
            advisory, random_high_reason = combined_random_avg_high_signal
            _replace_reason_prefix(
                reasons,
                "random_avg_high_signal_ceiling_shadow_only:",
                random_high_reason,
            )
        combined_q6_raw_tail = _q6_raw_tail_low_support_ceiling_watch(
            advisory,
            formal_value=formal_value,
        )
        if combined_q6_raw_tail is not None:
            advisory, q6_raw_tail_reason = combined_q6_raw_tail
            _replace_reason_prefix(
                reasons,
                "q6_raw_tail_low_support_ceiling_shadow_only:",
                q6_raw_tail_reason,
            )
        combined_q6_value_stress = _q6_raw_tail_value_stress_ceiling_watch(
            advisory,
            formal_value=formal_value,
        )
        if combined_q6_value_stress is not None:
            advisory, q6_value_stress_reason = combined_q6_value_stress
            _replace_reason_prefix(
                reasons,
                "q6_raw_tail_value_stress_ceiling_shadow_only:",
                q6_value_stress_reason,
            )
        advisory, _source_profile_applied = apply_source_profile_ceiling(advisory)
        return V3PracticalAdvisoryReport(
            baseline=posterior,
            advisory=advisory,
            source="random_avg_value",
            mode="random_avg_value_floor_watch",
            status=(
                "watch_random_avg_value_floor"
                if random_raise_watch
                else "watch_random_avg_value_p50_floor"
            ),
            recommendation="raise_watch" if random_raise_watch else "ceiling_watch",
            confidence="medium_low",
            source_lanes=_dedupe(lanes),
            risk_flags=_dedupe(risks),
            reason=_join_reasons(reasons),
        )
    if q6_value_ceiling_watch is not None:
        advisory, _reason, q6_value_raise_watch, q6_value_source = (
            q6_value_ceiling_watch
        )
        combined_random_avg_high_signal = _random_avg_high_signal_ceiling_watch(
            advisory,
            evidence_events=evidence_events,
        )
        if combined_random_avg_high_signal is not None:
            advisory, random_high_reason = combined_random_avg_high_signal
            _replace_reason_prefix(
                reasons,
                "random_avg_high_signal_ceiling_shadow_only:",
                random_high_reason,
            )
        combined_q6_raw_tail = _q6_raw_tail_low_support_ceiling_watch(
            advisory,
            formal_value=formal_value,
        )
        if combined_q6_raw_tail is not None:
            advisory, q6_raw_tail_reason = combined_q6_raw_tail
            _replace_reason_prefix(
                reasons,
                "q6_raw_tail_low_support_ceiling_shadow_only:",
                q6_raw_tail_reason,
            )
        combined_q6_value_stress = _q6_raw_tail_value_stress_ceiling_watch(
            advisory,
            formal_value=formal_value,
        )
        if combined_q6_value_stress is not None:
            advisory, q6_value_stress_reason = combined_q6_value_stress
            _replace_reason_prefix(
                reasons,
                "q6_raw_tail_value_stress_ceiling_shadow_only:",
                q6_value_stress_reason,
            )
        advisory, _source_profile_applied = apply_source_profile_ceiling(advisory)
        return V3PracticalAdvisoryReport(
            baseline=posterior,
            advisory=advisory,
            source=q6_value_source,
            mode="q6_value_ceiling_watch",
            status=(
                "watch_q6_value_raise"
                if q6_value_raise_watch
                else "watch_q6_value_ceiling"
            ),
            recommendation="raise_watch" if q6_value_raise_watch else "ceiling_watch",
            confidence="medium_low" if q6_value_raise_watch else "low_medium",
            source_lanes=_dedupe(lanes),
            risk_flags=_dedupe(risks),
            reason=_join_reasons(reasons),
        )
    if q6_raw_tail_ceiling_watch is not None:
        advisory, _reason = q6_raw_tail_ceiling_watch
        combined_random_avg_high_signal = _random_avg_high_signal_ceiling_watch(
            advisory,
            evidence_events=evidence_events,
        )
        if combined_random_avg_high_signal is not None:
            advisory, random_high_reason = combined_random_avg_high_signal
            _replace_reason_prefix(
                reasons,
                "random_avg_high_signal_ceiling_shadow_only:",
                random_high_reason,
            )
        combined_q6_value_stress = _q6_raw_tail_value_stress_ceiling_watch(
            advisory,
            formal_value=formal_value,
        )
        if combined_q6_value_stress is not None:
            advisory, q6_value_stress_reason = combined_q6_value_stress
            _replace_reason_prefix(
                reasons,
                "q6_raw_tail_value_stress_ceiling_shadow_only:",
                q6_value_stress_reason,
            )
        advisory, _source_profile_applied = apply_source_profile_ceiling(advisory)
        return V3PracticalAdvisoryReport(
            baseline=posterior,
            advisory=advisory,
            source="q6_raw_tail_value",
            mode="q6_raw_tail_low_support_ceiling_watch",
            status="watch_q6_raw_tail_low_support_ceiling",
            recommendation="ceiling_watch",
            confidence="low_medium",
            source_lanes=_dedupe(lanes),
            risk_flags=_dedupe(risks),
            reason=_join_reasons(reasons),
        )
    if q6_raw_tail_value_stress_watch is not None:
        advisory, _reason = q6_raw_tail_value_stress_watch
        combined_random_avg_high_signal = _random_avg_high_signal_ceiling_watch(
            advisory,
            evidence_events=evidence_events,
        )
        if combined_random_avg_high_signal is not None:
            advisory, random_high_reason = combined_random_avg_high_signal
            _replace_reason_prefix(
                reasons,
                "random_avg_high_signal_ceiling_shadow_only:",
                random_high_reason,
            )
        advisory, _source_profile_applied = apply_source_profile_ceiling(advisory)
        return V3PracticalAdvisoryReport(
            baseline=posterior,
            advisory=advisory,
            source="q6_raw_tail_value",
            mode="q6_raw_tail_value_stress_ceiling_watch",
            status="watch_q6_raw_tail_value_stress_ceiling",
            recommendation="ceiling_watch",
            confidence="low_medium",
            source_lanes=_dedupe(lanes),
            risk_flags=_dedupe(risks),
            reason=_join_reasons(reasons),
        )
    source_profile_watch = _source_profile_q6_tail_ceiling_watch(
        posterior,
        hero=hero,
        evidence_profile_key=source_profile,
        item_anchor_count=item_anchor_count,
        shape_anchor_count=shape_anchor_count,
    )
    if source_profile_watch is not None:
        advisory, source_profile_reason = source_profile_watch
        lanes.append("source_profile")
        risks.append("source_profile_q6_tail_ceiling")
        reasons.append(source_profile_reason)
        return V3PracticalAdvisoryReport(
            baseline=posterior,
            advisory=advisory,
            source="source_profile",
            mode="source_profile_q6_tail_ceiling_watch",
            status="watch_source_profile_q6_tail_ceiling",
            recommendation="raise_watch",
            confidence="low_medium",
            source_lanes=_dedupe(lanes),
            risk_flags=_dedupe(risks),
            reason=_join_reasons(reasons),
        )
    if random_avg_high_signal_watch is not None:
        advisory, _reason = random_avg_high_signal_watch
        return V3PracticalAdvisoryReport(
            baseline=posterior,
            advisory=advisory,
            source="random_avg_value",
            mode="random_avg_high_signal_ceiling_watch",
            status="watch_random_avg_high_signal_ceiling",
            recommendation="ceiling_watch",
            confidence="medium_low",
            source_lanes=_dedupe(lanes),
            risk_flags=_dedupe(risks),
            reason=_join_reasons(reasons),
        )
    if tail_replacement_watch is not None:
        advisory, _reason = tail_replacement_watch
        return V3PracticalAdvisoryReport(
            baseline=posterior,
            advisory=advisory,
            source="tail_replacement",
            mode="tail_replacement_p90_watch",
            status="watch_tail_replacement_p90",
            recommendation="ceiling_watch",
            confidence="low",
            source_lanes=_dedupe(lanes),
            risk_flags=_dedupe(risks),
            reason=_join_reasons(reasons),
        )
    if any(
        flag in risks
        for flag in (
            "capacity_source_pressure",
            "capacity_cells_drift",
            "mixed_value_floor_guard",
            "missing_settlement_count_table",
        )
    ):
        return V3PracticalAdvisoryReport(
            baseline=posterior,
            advisory=posterior,
            source="risk_watch",
            mode="capacity_or_value_guard",
            status="watch_risk_no_numeric_shift",
            recommendation="risk_watch",
            confidence="low",
            source_lanes=_dedupe(lanes),
            risk_flags=_dedupe(risks),
            reason=_join_reasons(reasons),
        )
    if "tail_value_candidate" in risks:
        return V3PracticalAdvisoryReport(
            baseline=posterior,
            advisory=posterior,
            source="risk_watch",
            mode="tail_or_source_watch",
            status="watch_no_numeric_shift",
            recommendation="ceiling_watch",
            confidence="low",
            source_lanes=_dedupe(lanes),
            risk_flags=_dedupe(risks),
            reason=_join_reasons(reasons),
        )
    return V3PracticalAdvisoryReport(
        baseline=posterior,
        advisory=posterior,
        source="baseline",
        mode="baseline",
        status="baseline_passthrough",
        recommendation="baseline_reference",
        confidence="baseline",
        source_lanes=_dedupe(lanes),
        risk_flags=_dedupe(risks),
        reason=(
            "broad_risk_recorded_no_action"
            if risks
            else "no_practical_v3_risk"
        ),
    )


def empty_practical_advisory_flat_dict(
    *,
    prefix: str = "v3_practical_",
) -> dict[str, Any]:
    return V3PracticalAdvisoryReport(
        baseline=None,
        advisory=None,
        source="none",
        mode="not_ready",
        status="not_ready",
        recommendation="no_advice",
        confidence="none",
        reason="missing_baseline",
    ).to_flat_dict(prefix=prefix)


def _formal(report: V3PosteriorReport | None) -> QuantileSummary | None:
    return report.formal_decision_value if report is not None else None


def _total_value(report: V3PosteriorReport | None) -> QuantileSummary | None:
    return report.total_value if report is not None else None


def _q6_value(report: V3PosteriorReport | None) -> QuantileSummary | None:
    return report.q6_value if report is not None else None


def _q6_formal(report: V3PosteriorReport | None) -> QuantileSummary | None:
    return report.q6_formal_decision_value if report is not None else None


def _q(summary: QuantileSummary | None, attr: str) -> float | None:
    if summary is None:
        return None
    return round(float(getattr(summary, attr)), 6)


def _delta(
    advisory: QuantileSummary | None,
    baseline: QuantileSummary | None,
    attr: str,
) -> float | None:
    if advisory is None or baseline is None:
        return None
    return round(float(getattr(advisory, attr)) - float(getattr(baseline, attr)), 6)


def _float_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed


def _map_family_from_id(map_id: int) -> str:
    prefix = int(map_id) // 100
    if prefix in {24, 34, 44}:
        return "villa"
    if prefix in {25, 35, 45}:
        return "shipwreck"
    if prefix in {26, 36, 46}:
        return "hidden"
    return "other"


def _normal_hero(value: Any) -> str:
    hero = str(value or "").strip().lower()
    return hero or "unknown"


def _event_targets(event: Any) -> set[str]:
    return {str(target) for target in (getattr(event, "targets", ()) or ())}


def _evidence_profile_key_from_events(evidence_events: tuple[Any, ...]) -> str:
    parts: list[str] = []
    public_flags: set[str] = set()
    has_public_random_avg = False
    has_category = False
    has_item = False
    has_shape = False
    has_layout = False
    for event in evidence_events:
        targets = _event_targets(event)
        semantic = str(getattr(event, "semantic", "") or "")
        source_kind = str(getattr(event, "source_kind", "") or "")
        if source_kind == "public_info":
            if "session.total_count" in targets or "session.total_cells" in targets:
                public_flags.add("total")
            if "quality_ceiling" in targets:
                public_flags.add("max_quality")
            if "max_item_cells" in targets:
                public_flags.add("max_item_cells")
            if "random_avg_value" in targets or (
                semantic.startswith("random_") and "avg" in semantic
            ):
                has_public_random_avg = True
        if "category_anchors" in targets or semantic.startswith("category_"):
            has_category = True
        if "item_anchors" in targets:
            has_item = True
        if "shape_anchors" in targets:
            has_shape = True
        if semantic in {"all_outlines", "full_outline_session_total", "ethan_full_outline"}:
            has_layout = True
        if "shape_anchors" in targets and (
            "session.total_cells" in targets or "session.total_count" in targets
        ):
            has_layout = True
    public_parts = [
        label
        for label in ("total", "max_quality", "max_item_cells")
        if label in public_flags
    ]
    if public_parts:
        parts.append("public:" + "+".join(public_parts))
    if has_public_random_avg:
        parts.append("public:random_avg")
    if has_category:
        parts.append("tool:category")
    if has_item:
        parts.append("item")
    if has_shape:
        parts.append("shape")
    if has_layout:
        parts.append("layout")
    return "+".join(parts) if parts else "basic"


def _normalized_evidence_profile_key(
    evidence_profile_key: str | None,
    *,
    evidence_events: tuple[Any, ...],
) -> str:
    profile = str(evidence_profile_key or "").strip()
    if profile:
        return profile
    return _evidence_profile_key_from_events(evidence_events)


def _raise_p90_by_delta(
    summary: QuantileSummary | None,
    delta: float,
) -> QuantileSummary | None:
    if summary is None or delta <= 0.0:
        return summary
    return QuantileSummary(
        p10=float(summary.p10),
        p50=float(summary.p50),
        p90=float(summary.p90) + float(delta),
    )


def _floor_p90(
    summary: QuantileSummary | None,
    floor: float,
) -> QuantileSummary | None:
    if summary is None or floor <= 0.0:
        return summary
    return QuantileSummary(
        p10=float(summary.p10),
        p50=float(summary.p50),
        p90=max(float(summary.p90), float(floor)),
    )


def _floor_p50_p90(
    summary: QuantileSummary | None,
    floor: float,
) -> QuantileSummary | None:
    if summary is None or floor <= 0.0:
        return summary
    floor_value = float(floor)
    return QuantileSummary(
        p10=float(summary.p10),
        p50=max(float(summary.p50), floor_value),
        p90=max(float(summary.p90), floor_value),
    )


def _raise_p50_p90_by_delta(
    summary: QuantileSummary | None,
    *,
    p50_delta: float,
    p90_delta: float,
) -> QuantileSummary | None:
    if summary is None:
        return None
    p50_delta = max(0.0, float(p50_delta))
    p90_delta = max(0.0, float(p90_delta))
    if p50_delta <= 0.0 and p90_delta <= 0.0:
        return summary
    return QuantileSummary(
        p10=float(summary.p10),
        p50=float(summary.p50) + p50_delta,
        p90=float(summary.p90) + p90_delta,
    )


def _q6_prior_floor_watch(
    posterior: V3PosteriorReport,
    formal_value: V3FormalValueSamplerReport | None,
) -> tuple[V3PosteriorReport, str] | None:
    prior_fields = formal_value.prior_fields if formal_value is not None else None
    if not prior_fields:
        return None
    prior_q6_value = _float_or_none(prior_fields.get("v3_prior_q6_expected_value"))
    q6_formal = posterior.q6_formal_decision_value
    formal = posterior.formal_decision_value
    if prior_q6_value is None or q6_formal is None or formal is None:
        return None
    q6_gap = max(0.0, float(prior_q6_value) - float(q6_formal.p90))
    if q6_gap < _Q6_PRIOR_FLOOR_MIN_VALUE_GAP:
        return None
    diagnostics = tuple(posterior.diagnostics) + (
        "practical_q6_prior_floor_watch",
        f"practical_q6_prior_expected_value={prior_q6_value:.6f}",
        f"practical_q6_prior_p90_gap={q6_gap:.6f}",
    )
    advisory = V3PosteriorReport(
        map_id=posterior.map_id,
        map_name=posterior.map_name,
        n_total=posterior.n_total,
        n_matched=posterior.n_matched,
        n_strict_matched=posterior.n_strict_matched,
        match_scope=posterior.match_scope,
        q6_present_rate=posterior.q6_present_rate,
        total_cells=posterior.total_cells,
        total_value=_raise_p90_by_delta(posterior.total_value, q6_gap),
        formal_decision_value=_raise_p90_by_delta(formal, q6_gap),
        tail_replacement_decision_value=_raise_p90_by_delta(
            posterior.tail_replacement_decision_value,
            q6_gap,
        ),
        q6_count=posterior.q6_count,
        q6_cells=posterior.q6_cells,
        q6_value=_floor_p90(posterior.q6_value, prior_q6_value),
        q6_formal_decision_value=_floor_p90(q6_formal, prior_q6_value),
        q6_tail_replacement_decision_value=_floor_p90(
            posterior.q6_tail_replacement_decision_value,
            prior_q6_value,
        ),
        diagnostics=diagnostics,
    )
    reason = (
        "q6_prior_floor_shadow_only:"
        f"expected={prior_q6_value:.0f}:p90_gap={q6_gap:.0f}"
    )
    return advisory, reason


def _q6_prior_tail_ceiling_watch(
    posterior: V3PosteriorReport,
    formal_value: V3FormalValueSamplerReport | None,
) -> tuple[V3PosteriorReport, str] | None:
    prior_fields = formal_value.prior_fields if formal_value is not None else None
    if not prior_fields:
        return None
    if _map_family_from_id(posterior.map_id) not in {"shipwreck", "villa"}:
        return None
    present_rate = _float_or_none(posterior.q6_present_rate)
    if (
        present_rate is None
        or present_rate < _Q6_PRIOR_TAIL_CEILING_MIN_PRESENT_RATE
    ):
        return None
    prior_q6_value = _float_or_none(prior_fields.get("v3_prior_q6_expected_value"))
    formal = posterior.formal_decision_value
    q6_formal = posterior.q6_formal_decision_value
    if prior_q6_value is None or formal is None or q6_formal is None:
        return None
    tail_target = prior_q6_value * _Q6_PRIOR_TAIL_CEILING_MULTIPLIER
    raw_gap = max(0.0, tail_target - float(q6_formal.p90))
    if raw_gap < _Q6_PRIOR_TAIL_CEILING_MIN_P90_GAP:
        return None
    p90_delta = min(raw_gap, _Q6_PRIOR_TAIL_CEILING_MAX_DELTA)
    diagnostics = tuple(posterior.diagnostics) + (
        "practical_q6_prior_tail_ceiling_watch",
        f"practical_q6_prior_tail_present_rate={present_rate:.6f}",
        f"practical_q6_prior_tail_expected_value={prior_q6_value:.6f}",
        f"practical_q6_prior_tail_target={tail_target:.6f}",
        f"practical_q6_prior_tail_p90_gap={raw_gap:.6f}",
        f"practical_q6_prior_tail_p90_delta={p90_delta:.6f}",
    )
    advisory = V3PosteriorReport(
        map_id=posterior.map_id,
        map_name=posterior.map_name,
        n_total=posterior.n_total,
        n_matched=posterior.n_matched,
        n_strict_matched=posterior.n_strict_matched,
        match_scope=posterior.match_scope,
        q6_present_rate=posterior.q6_present_rate,
        total_cells=posterior.total_cells,
        total_value=_raise_p90_by_delta(posterior.total_value, p90_delta),
        formal_decision_value=_raise_p90_by_delta(formal, p90_delta),
        tail_replacement_decision_value=_raise_p90_by_delta(
            posterior.tail_replacement_decision_value,
            p90_delta,
        ),
        q6_count=posterior.q6_count,
        q6_cells=posterior.q6_cells,
        q6_value=posterior.q6_value,
        q6_formal_decision_value=_raise_p90_by_delta(q6_formal, p90_delta),
        q6_tail_replacement_decision_value=_raise_p90_by_delta(
            posterior.q6_tail_replacement_decision_value,
            p90_delta,
        ),
        diagnostics=diagnostics,
    )
    reason = (
        "q6_prior_tail_ceiling_shadow_only:"
        f"present_rate={present_rate:.3f}:expected={prior_q6_value:.0f}:"
        f"target={tail_target:.0f}:raw_gap={raw_gap:.0f}:"
        f"p90_delta={p90_delta:.0f}"
    )
    return advisory, reason


def _source_profile_q6_tail_ceiling_watch(
    posterior: V3PosteriorReport,
    *,
    hero: str | None,
    evidence_profile_key: str,
    item_anchor_count: int | None,
    shape_anchor_count: int | None,
) -> tuple[V3PosteriorReport, str] | None:
    rule = _SOURCE_PROFILE_Q6_TAIL_CEILING_RULES.get(
        (_normal_hero(hero), int(posterior.map_id), evidence_profile_key)
    )
    if rule is None:
        return None
    formal = posterior.formal_decision_value
    total = posterior.total_value
    q6_formal = posterior.q6_formal_decision_value
    if formal is None or total is None or q6_formal is None:
        return None
    present_rate = _float_or_none(posterior.q6_present_rate)
    min_present_rate = float(rule["min_q6_present_rate"])
    if present_rate is None or present_rate < min_present_rate:
        return None
    min_shape_anchors = rule.get("min_shape_anchors")
    if min_shape_anchors is not None:
        if shape_anchor_count is None or int(shape_anchor_count) < int(min_shape_anchors):
            return None
    min_item_anchors = rule.get("min_item_anchors")
    if min_item_anchors is not None:
        if item_anchor_count is None or int(item_anchor_count) < int(min_item_anchors):
            return None
    raw_total_gap = max(0.0, float(total.p90) - float(formal.p90))
    min_raw_total_gap = float(rule["min_raw_total_p90_gap"])
    if raw_total_gap < min_raw_total_gap:
        return None
    p90_delta = float(rule["p90_delta"])
    diagnostics = tuple(posterior.diagnostics) + (
        "practical_source_profile_q6_tail_ceiling_watch",
        f"practical_source_profile_hero={_normal_hero(hero)}",
        f"practical_source_profile_key={evidence_profile_key}",
        f"practical_source_profile_q6_present_rate={present_rate:.6f}",
        f"practical_source_profile_item_anchors={item_anchor_count}",
        f"practical_source_profile_shape_anchors={shape_anchor_count}",
        f"practical_source_profile_raw_total_gap={raw_total_gap:.6f}",
        f"practical_source_profile_p90_delta={p90_delta:.6f}",
    )
    advisory = V3PosteriorReport(
        map_id=posterior.map_id,
        map_name=posterior.map_name,
        n_total=posterior.n_total,
        n_matched=posterior.n_matched,
        n_strict_matched=posterior.n_strict_matched,
        match_scope=posterior.match_scope,
        q6_present_rate=posterior.q6_present_rate,
        total_cells=posterior.total_cells,
        total_value=_raise_p90_by_delta(posterior.total_value, p90_delta),
        formal_decision_value=_raise_p90_by_delta(formal, p90_delta),
        tail_replacement_decision_value=_raise_p90_by_delta(
            posterior.tail_replacement_decision_value,
            p90_delta,
        ),
        q6_count=posterior.q6_count,
        q6_cells=posterior.q6_cells,
        q6_value=posterior.q6_value,
        q6_formal_decision_value=_raise_p90_by_delta(q6_formal, p90_delta),
        q6_tail_replacement_decision_value=_raise_p90_by_delta(
            posterior.q6_tail_replacement_decision_value,
            p90_delta,
        ),
        diagnostics=diagnostics,
    )
    reason = (
        "source_profile_q6_tail_ceiling_shadow_only:"
        f"hero={_normal_hero(hero)}:map_id={posterior.map_id}:"
        f"profile={evidence_profile_key}:present_rate={present_rate:.3f}:"
        f"item_anchors={item_anchor_count}:"
        f"shape_anchors={shape_anchor_count}:"
        f"raw_total_gap={raw_total_gap:.0f}:p90_delta={p90_delta:.0f}"
    )
    return advisory, reason


def _random_avg_value_observations(
    evidence_events: tuple[Any, ...],
) -> tuple[tuple[int, float], ...]:
    observations: list[tuple[int, float]] = []
    for event in evidence_events:
        targets = set(getattr(event, "targets", ()) or ())
        semantic = str(getattr(event, "semantic", "") or "")
        if "random_avg_value" not in targets and not (
            semantic.startswith("random_") and semantic.endswith("_avg_value")
        ):
            continue
        raw_count = semantic.removeprefix("random_").split("_", 1)[0]
        try:
            sample_count = int(raw_count)
        except (TypeError, ValueError):
            continue
        payload = getattr(event, "payload", None) or {}
        value = _float_or_none(payload.get("value", payload.get("result")))
        if value is None or value < _RANDOM_AVG_VALUE_SIGNAL_FLOOR:
            continue
        observations.append((sample_count, float(value)))
    return tuple(observations)


def _random_avg_value_floor_watch(
    posterior: V3PosteriorReport,
    *,
    evidence_events: tuple[Any, ...],
) -> tuple[V3PosteriorReport, str, bool] | None:
    observations = _random_avg_value_observations(evidence_events)
    if not observations:
        return None
    formal = posterior.formal_decision_value
    if formal is None:
        return None
    floor = max(float(count) * value for count, value in observations)
    p50_gap = max(0.0, floor - float(formal.p50))
    p90_gap = max(0.0, floor - float(formal.p90))
    if (
        p50_gap < _RANDOM_AVG_VALUE_MIN_P50_GAP
        and p90_gap < _RANDOM_AVG_VALUE_MIN_P90_GAP
    ):
        return None
    raise_watch = p90_gap >= _RANDOM_AVG_VALUE_MIN_P90_GAP
    diagnostics = tuple(posterior.diagnostics) + (
        "practical_random_avg_value_floor_watch",
        f"practical_random_avg_value_floor={floor:.6f}",
        f"practical_random_avg_value_p50_gap={p50_gap:.6f}",
        f"practical_random_avg_value_p90_gap={p90_gap:.6f}",
    )
    advisory = V3PosteriorReport(
        map_id=posterior.map_id,
        map_name=posterior.map_name,
        n_total=posterior.n_total,
        n_matched=posterior.n_matched,
        n_strict_matched=posterior.n_strict_matched,
        match_scope=posterior.match_scope,
        q6_present_rate=posterior.q6_present_rate,
        total_cells=posterior.total_cells,
        total_value=_floor_p50_p90(posterior.total_value, floor),
        formal_decision_value=_floor_p50_p90(formal, floor),
        tail_replacement_decision_value=_floor_p50_p90(
            posterior.tail_replacement_decision_value,
            floor,
        ),
        q6_count=posterior.q6_count,
        q6_cells=posterior.q6_cells,
        q6_value=posterior.q6_value,
        q6_formal_decision_value=posterior.q6_formal_decision_value,
        q6_tail_replacement_decision_value=(
            posterior.q6_tail_replacement_decision_value
        ),
        diagnostics=diagnostics,
    )
    labels = ",".join(f"n={count}:avg={value:.0f}" for count, value in observations)
    reason = (
        "random_avg_value_floor_shadow_only:"
        f"floor={floor:.0f}:p50_gap={p50_gap:.0f}:"
        f"p90_gap={p90_gap:.0f}:samples={labels}"
    )
    return advisory, reason, raise_watch


def _random_avg_high_signal_ceiling_watch(
    posterior: V3PosteriorReport,
    *,
    evidence_events: tuple[Any, ...],
) -> tuple[V3PosteriorReport, str] | None:
    observations = _random_avg_value_observations(evidence_events)
    if not observations:
        return None
    formal = posterior.formal_decision_value
    if formal is None:
        return None
    candidates: list[tuple[int, float, float]] = []
    for sample_count, average_value in observations:
        if average_value < _RANDOM_AVG_HIGH_SIGNAL_AVG_FLOOR:
            continue
        target = (
            float(sample_count)
            * average_value
            * _RANDOM_AVG_HIGH_SIGNAL_MULTIPLIER
        )
        candidates.append((sample_count, average_value, target))
    if not candidates:
        return None
    sample_count, average_value, target = max(
        candidates,
        key=lambda item: (item[2], item[1], item[0]),
    )
    p90_gap = max(0.0, target - float(formal.p90))
    if p90_gap < _RANDOM_AVG_HIGH_SIGNAL_MIN_P90_GAP:
        return None
    p90_delta = min(p90_gap, _RANDOM_AVG_HIGH_SIGNAL_MAX_DELTA)
    diagnostics = tuple(posterior.diagnostics) + (
        "practical_random_avg_high_signal_ceiling_watch",
        f"practical_random_avg_high_signal_count={sample_count}",
        f"practical_random_avg_high_signal_avg={average_value:.6f}",
        f"practical_random_avg_high_signal_target={target:.6f}",
        f"practical_random_avg_high_signal_p90_gap={p90_gap:.6f}",
        f"practical_random_avg_high_signal_p90_delta={p90_delta:.6f}",
    )
    advisory = V3PosteriorReport(
        map_id=posterior.map_id,
        map_name=posterior.map_name,
        n_total=posterior.n_total,
        n_matched=posterior.n_matched,
        n_strict_matched=posterior.n_strict_matched,
        match_scope=posterior.match_scope,
        q6_present_rate=posterior.q6_present_rate,
        total_cells=posterior.total_cells,
        total_value=_raise_p90_by_delta(posterior.total_value, p90_delta),
        formal_decision_value=_raise_p90_by_delta(formal, p90_delta),
        tail_replacement_decision_value=_raise_p90_by_delta(
            posterior.tail_replacement_decision_value,
            p90_delta,
        ),
        q6_count=posterior.q6_count,
        q6_cells=posterior.q6_cells,
        q6_value=posterior.q6_value,
        q6_formal_decision_value=posterior.q6_formal_decision_value,
        q6_tail_replacement_decision_value=(
            posterior.q6_tail_replacement_decision_value
        ),
        diagnostics=diagnostics,
    )
    reason = (
        "random_avg_high_signal_ceiling_shadow_only:"
        f"n={sample_count}:avg={average_value:.0f}:"
        f"target={target:.0f}:p90_gap={p90_gap:.0f}:"
        f"p90_delta={p90_delta:.0f}"
    )
    return advisory, reason


def _q6_value_ceiling_watch(
    posterior: V3PosteriorReport,
    *,
    ccv_posterior: V3PosteriorReport | None = None,
    ccv_component_posterior: V3PosteriorReport | None = None,
    residual_posterior: V3PosteriorReport | None = None,
) -> tuple[V3PosteriorReport, str, bool, str] | None:
    del ccv_posterior
    formal = posterior.formal_decision_value
    q6_value = posterior.q6_value
    if formal is None or q6_value is None:
        return None
    candidates: list[tuple[str, V3PosteriorReport, float, float]] = []
    for source, report, scope in (
        (
            "q6_value_component",
            ccv_component_posterior,
            "ccv_component_likelihood",
        ),
        ("q6_value_residual", residual_posterior, "residual_likelihood"),
    ):
        if report is None or not report.ready or report.match_scope != scope:
            continue
        report_q6_value = report.q6_value
        if report_q6_value is None:
            continue
        p50_gap = max(0.0, float(report_q6_value.p50) - float(q6_value.p50))
        p90_gap = max(0.0, float(report_q6_value.p90) - float(q6_value.p90))
        if (
            p50_gap < _Q6_VALUE_CEILING_MIN_P50_GAP
            or p90_gap < _Q6_VALUE_CEILING_MIN_P90_GAP
        ):
            continue
        candidates.append((source, report, p50_gap, p90_gap))
    if not candidates:
        return None
    source, report, raw_p50_gap, raw_p90_gap = max(
        candidates,
        key=lambda item: (item[2] + item[3], item[2], item[3]),
    )
    p50_delta = min(raw_p50_gap, _Q6_VALUE_CEILING_MAX_DELTA)
    p90_delta = min(raw_p90_gap, _Q6_VALUE_CEILING_MAX_DELTA)
    raise_watch = (
        raw_p50_gap >= _Q6_VALUE_RAISE_MIN_P50_GAP
        and raw_p90_gap >= _Q6_VALUE_RAISE_MIN_P90_GAP
    )
    diagnostics = tuple(posterior.diagnostics) + (
        "practical_q6_value_ceiling_watch",
        f"practical_q6_value_ceiling_source={source}",
        f"practical_q6_value_p50_gap={raw_p50_gap:.6f}",
        f"practical_q6_value_p90_gap={raw_p90_gap:.6f}",
        f"practical_q6_value_p50_delta={p50_delta:.6f}",
        f"practical_q6_value_p90_delta={p90_delta:.6f}",
    )
    advisory = V3PosteriorReport(
        map_id=posterior.map_id,
        map_name=posterior.map_name,
        n_total=posterior.n_total,
        n_matched=posterior.n_matched,
        n_strict_matched=posterior.n_strict_matched,
        match_scope=posterior.match_scope,
        q6_present_rate=posterior.q6_present_rate,
        total_cells=posterior.total_cells,
        total_value=_raise_p50_p90_by_delta(
            posterior.total_value,
            p50_delta=p50_delta,
            p90_delta=p90_delta,
        ),
        formal_decision_value=_raise_p50_p90_by_delta(
            formal,
            p50_delta=p50_delta,
            p90_delta=p90_delta,
        ),
        tail_replacement_decision_value=_raise_p50_p90_by_delta(
            posterior.tail_replacement_decision_value,
            p50_delta=p50_delta,
            p90_delta=p90_delta,
        ),
        q6_count=posterior.q6_count,
        q6_cells=posterior.q6_cells,
        q6_value=_raise_p50_p90_by_delta(
            q6_value,
            p50_delta=p50_delta,
            p90_delta=p90_delta,
        ),
        q6_formal_decision_value=_raise_p50_p90_by_delta(
            posterior.q6_formal_decision_value,
            p50_delta=p50_delta,
            p90_delta=p90_delta,
        ),
        q6_tail_replacement_decision_value=_raise_p50_p90_by_delta(
            posterior.q6_tail_replacement_decision_value,
            p50_delta=p50_delta,
            p90_delta=p90_delta,
        ),
        diagnostics=diagnostics,
    )
    reason = (
        "q6_value_ceiling_shadow_only:"
        f"source={source}:p50_gap={raw_p50_gap:.0f}:"
        f"p90_gap={raw_p90_gap:.0f}:p50_delta={p50_delta:.0f}:"
        f"p90_delta={p90_delta:.0f}:source_matched={report.n_matched}"
    )
    return advisory, reason, raise_watch, source


def _q6_raw_tail_low_support_ceiling_watch(
    posterior: V3PosteriorReport,
    *,
    formal_value: V3FormalValueSamplerReport | None,
) -> tuple[V3PosteriorReport, str] | None:
    if posterior.match_scope != "strict":
        return None
    if int(posterior.n_matched) > _Q6_RAW_TAIL_LOW_SUPPORT_MAX_MATCHED:
        return None
    formal = posterior.formal_decision_value
    q6_value = posterior.q6_value
    q6_formal = posterior.q6_formal_decision_value
    if formal is None or q6_value is None or q6_formal is None:
        return None
    if not _has_tail_value_support(posterior, formal_value):
        return None
    raw_gap = max(0.0, float(q6_value.p90) - float(q6_formal.p90))
    if raw_gap < _Q6_RAW_TAIL_LOW_SUPPORT_MIN_P90_GAP:
        return None
    p90_delta = min(raw_gap, _Q6_RAW_TAIL_LOW_SUPPORT_MAX_DELTA)
    diagnostics = tuple(posterior.diagnostics) + (
        "practical_q6_raw_tail_low_support_ceiling_watch",
        f"practical_q6_raw_tail_low_support_n_matched={posterior.n_matched}",
        f"practical_q6_raw_tail_p90_gap={raw_gap:.6f}",
        f"practical_q6_raw_tail_p90_delta={p90_delta:.6f}",
    )
    advisory = V3PosteriorReport(
        map_id=posterior.map_id,
        map_name=posterior.map_name,
        n_total=posterior.n_total,
        n_matched=posterior.n_matched,
        n_strict_matched=posterior.n_strict_matched,
        match_scope=posterior.match_scope,
        q6_present_rate=posterior.q6_present_rate,
        total_cells=posterior.total_cells,
        total_value=_raise_p90_by_delta(posterior.total_value, p90_delta),
        formal_decision_value=_raise_p90_by_delta(formal, p90_delta),
        tail_replacement_decision_value=_raise_p90_by_delta(
            posterior.tail_replacement_decision_value,
            p90_delta,
        ),
        q6_count=posterior.q6_count,
        q6_cells=posterior.q6_cells,
        q6_value=posterior.q6_value,
        q6_formal_decision_value=_raise_p90_by_delta(
            posterior.q6_formal_decision_value,
            p90_delta,
        ),
        q6_tail_replacement_decision_value=_raise_p90_by_delta(
            posterior.q6_tail_replacement_decision_value,
            p90_delta,
        ),
        diagnostics=diagnostics,
    )
    reason = (
        "q6_raw_tail_low_support_ceiling_shadow_only:"
        f"n_matched={posterior.n_matched}:"
        f"raw_gap={raw_gap:.0f}:p90_delta={p90_delta:.0f}"
    )
    return advisory, reason


def _q6_raw_tail_value_stress_ceiling_watch(
    posterior: V3PosteriorReport,
    *,
    formal_value: V3FormalValueSamplerReport | None,
) -> tuple[V3PosteriorReport, str] | None:
    if formal_value is None or "value_floor_stress" not in formal_value.stress_classes:
        return None
    formal = posterior.formal_decision_value
    q6_value = posterior.q6_value
    q6_formal = posterior.q6_formal_decision_value
    if formal is None or q6_value is None or q6_formal is None:
        return None
    raw_gap = max(0.0, float(q6_value.p90) - float(q6_formal.p90))
    if raw_gap < _Q6_RAW_TAIL_VALUE_STRESS_MIN_P90_GAP:
        return None
    p90_delta = min(raw_gap, _Q6_RAW_TAIL_VALUE_STRESS_MAX_DELTA)
    diagnostics = tuple(posterior.diagnostics) + (
        "practical_q6_raw_tail_value_stress_ceiling_watch",
        f"practical_q6_raw_tail_value_stress_p90_gap={raw_gap:.6f}",
        f"practical_q6_raw_tail_value_stress_p90_delta={p90_delta:.6f}",
    )
    advisory = V3PosteriorReport(
        map_id=posterior.map_id,
        map_name=posterior.map_name,
        n_total=posterior.n_total,
        n_matched=posterior.n_matched,
        n_strict_matched=posterior.n_strict_matched,
        match_scope=posterior.match_scope,
        q6_present_rate=posterior.q6_present_rate,
        total_cells=posterior.total_cells,
        total_value=_raise_p90_by_delta(posterior.total_value, p90_delta),
        formal_decision_value=_raise_p90_by_delta(formal, p90_delta),
        tail_replacement_decision_value=_raise_p90_by_delta(
            posterior.tail_replacement_decision_value,
            p90_delta,
        ),
        q6_count=posterior.q6_count,
        q6_cells=posterior.q6_cells,
        q6_value=posterior.q6_value,
        q6_formal_decision_value=_raise_p90_by_delta(
            posterior.q6_formal_decision_value,
            p90_delta,
        ),
        q6_tail_replacement_decision_value=_raise_p90_by_delta(
            posterior.q6_tail_replacement_decision_value,
            p90_delta,
        ),
        diagnostics=diagnostics,
    )
    reason = (
        "q6_raw_tail_value_stress_ceiling_shadow_only:"
        f"raw_gap={raw_gap:.0f}:p90_delta={p90_delta:.0f}"
    )
    return advisory, reason


def _has_tail_value_support(
    posterior: V3PosteriorReport,
    formal_value: V3FormalValueSamplerReport | None,
) -> bool:
    formal = posterior.formal_decision_value
    replacement = posterior.tail_replacement_decision_value
    if formal is not None and replacement is not None:
        replacement_gap = max(0.0, float(replacement.p90) - float(formal.p90))
        if replacement_gap >= _TAIL_REPLACEMENT_MIN_P90_GAP:
            return True
    if formal_value is None:
        return False
    return "value_floor_stress" in formal_value.stress_classes


def _tail_replacement_p90_watch(
    posterior: V3PosteriorReport,
) -> tuple[V3PosteriorReport, str] | None:
    formal = posterior.formal_decision_value
    replacement = posterior.tail_replacement_decision_value
    if formal is None or replacement is None:
        return None
    gap = max(0.0, float(replacement.p90) - float(formal.p90))
    if gap < _TAIL_REPLACEMENT_MIN_P90_GAP:
        return None
    diagnostics = tuple(posterior.diagnostics) + (
        "practical_tail_replacement_p90_watch",
        f"practical_tail_replacement_p90_gap={gap:.6f}",
    )
    advisory = V3PosteriorReport(
        map_id=posterior.map_id,
        map_name=posterior.map_name,
        n_total=posterior.n_total,
        n_matched=posterior.n_matched,
        n_strict_matched=posterior.n_strict_matched,
        match_scope=posterior.match_scope,
        q6_present_rate=posterior.q6_present_rate,
        total_cells=posterior.total_cells,
        total_value=posterior.total_value,
        formal_decision_value=_floor_p90(formal, float(replacement.p90)),
        tail_replacement_decision_value=replacement,
        q6_count=posterior.q6_count,
        q6_cells=posterior.q6_cells,
        q6_value=posterior.q6_value,
        q6_formal_decision_value=posterior.q6_formal_decision_value,
        q6_tail_replacement_decision_value=(
            posterior.q6_tail_replacement_decision_value
        ),
        diagnostics=diagnostics,
    )
    reason = f"tail_replacement_p90_shadow_only:p90_gap={gap:.0f}"
    return advisory, reason


def _dedupe(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return tuple(out)


def _join_reasons(reasons: list[str]) -> str:
    return ";".join(_dedupe([reason for reason in reasons if reason]))


def _replace_reason_prefix(reasons: list[str], prefix: str, replacement: str) -> None:
    reasons[:] = [reason for reason in reasons if not reason.startswith(prefix)]
    reasons.append(replacement)


__all__ = (
    "V3PracticalAdvisoryReport",
    "advise_practical_report",
    "empty_practical_advisory_flat_dict",
)
