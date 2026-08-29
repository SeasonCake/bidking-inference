"""Phase 1A inference engine: turn tool readings + hero outlines into
posterior distributions over (quality, value) for each placeholder item.

See docstring of :mod:`bidking_lab.inference.display` for the decimal-
display model that underpins the small-decimal-leakage inference for the
``X品均格`` family.

Top-level surfaces:

* :func:`bidking_lab.inference.observation.top_k_for_session` — *greedy*
  per-bucket top-K, fast and good enough when the player gives at least
  one strong reading per quality.
* :func:`bidking_lab.inference.joint.joint_top_k_for_session` — *joint*
  posterior across buckets, uses the observed warehouse total as a
  soft global constraint. Use when greedy top-1 looks inconsistent
  across buckets, or when warehouse_total_cells is known exactly.
"""

from bidking_lab.inference.ground_truth import (
    BucketTruth,
    SessionTruth,
    is_huge_item,
    sample_session_truth,
)
from bidking_lab.inference.joint import (
    JointHypothesis,
    joint_top_k_for_session,
)
from bidking_lab.inference.map_likelihood import (
    MapLikelihoodResult,
    QuantileSummary,
    category_observation_soft_score,
    estimate_map_likelihood,
    summarize_map_truths,
    truth_matches_obs,
)
from bidking_lab.inference.bid_strategy import (
    BidStrategyReport,
    BidThresholds,
    PlayerBidRisk,
    recommend_bid_strategy,
)
from bidking_lab.inference.warehouse_estimator import (
    WarehouseEstimate,
    WarehouseMapContribution,
    estimate_warehouse_cells,
)
from bidking_lab.inference.tool_info_roi import (
    DEFAULT_INFO_ROI_TOOLS,
    ToolInfoROI,
    estimate_tool_info_roi,
)
from bidking_lab.inference.v2 import (
    ConditionalSampler,
    EvidenceFact,
    EvidenceStore,
    EvidenceStoreBuilder,
    KnownItemAnchor,
    KnownFootprint,
    LayoutFeasibility,
    PosteriorReport,
    QualityDropPrior,
    ResidualProblem,
    RuntimeEvidence,
    ShapeTarget,
    build_residual_problem,
    decision_value_for_truth,
    estimate_posterior_v2,
    evidence_store_from_fatbeans_events,
    is_tail_supported_by_evidence,
    known_footprints,
    known_item_anchors,
    layout_feasibility_from_store,
    layout_feasibility_score,
    q6_decision_value_for_truth,
    shape_targets_from_store,
    value_evidence_score,
)
from bidking_lab.inference.quality_combo_presolve import (
    QualityComboPresolve,
    is_quality_combo_reachable,
    load_quality_combo_presolve,
    quality_combo_presolve_for_map,
    quality_combo_presolve_payload,
)
from bidking_lab.inference.observation import (
    AISHA_DEFAULT_LOADOUT,
    AHMED_DEFAULT_LOADOUT,
    ETHAN_ALT_LOADOUT,
    ETHAN_DEFAULT_LOADOUT,
    FREQUENCY_PRIOR_LOADOUT,
    GABRIELA_DEFAULT_LOADOUT,
    GENERIC_DEFAULT_LOADOUT,
    MARIA_DEFAULT_LOADOUT,
    QUALITY_ONLY_DEFAULT_LOADOUT,
    RAVEN_DEFAULT_LOADOUT,
    STANDARD_LOADOUTS,
    VICTOR_DEFAULT_LOADOUT,
    BucketCandidate,
    CategoryItemObservation,
    QualityBucketObs,
    SessionObs,
    candidate_cache_info,
    candidates_for_bucket,
    clear_candidate_cache,
    top_k_for_session,
)
from bidking_lab.inference.roi import (
    ToolROI,
    compute_tool_roi,
)
from bidking_lab.inference.snipe import (
    PassRecommendation,
    SnipeRecommendation,
    compute_pass_recommendation,
    compute_snipe_recommendation,
)
from bidking_lab.inference.synth_readings import (
    SESSION_TOOL_SPECS,
    TOOL_SPECS,
    ToolEffect,
    ToolSpec,
    apply_tool,
    build_session_obs,
)

__all__ = (
    "BucketCandidate",
    "CategoryItemObservation",
    "QualityBucketObs",
    "SessionObs",
    "candidate_cache_info",
    "candidates_for_bucket",
    "clear_candidate_cache",
    "top_k_for_session",
    "AISHA_DEFAULT_LOADOUT",
    "AHMED_DEFAULT_LOADOUT",
    "ETHAN_DEFAULT_LOADOUT",
    "ETHAN_ALT_LOADOUT",
    "FREQUENCY_PRIOR_LOADOUT",
    "GABRIELA_DEFAULT_LOADOUT",
    "GENERIC_DEFAULT_LOADOUT",
    "MARIA_DEFAULT_LOADOUT",
    "QUALITY_ONLY_DEFAULT_LOADOUT",
    "RAVEN_DEFAULT_LOADOUT",
    "STANDARD_LOADOUTS",
    "VICTOR_DEFAULT_LOADOUT",
    "JointHypothesis",
    "joint_top_k_for_session",
    "MapLikelihoodResult",
    "QuantileSummary",
    "category_observation_soft_score",
    "estimate_map_likelihood",
    "summarize_map_truths",
    "truth_matches_obs",
    "BidStrategyReport",
    "BidThresholds",
    "PlayerBidRisk",
    "recommend_bid_strategy",
    "WarehouseEstimate",
    "WarehouseMapContribution",
    "estimate_warehouse_cells",
    "DEFAULT_INFO_ROI_TOOLS",
    "ToolInfoROI",
    "estimate_tool_info_roi",
    "ConditionalSampler",
    "EvidenceFact",
    "EvidenceStore",
    "EvidenceStoreBuilder",
    "KnownItemAnchor",
    "KnownFootprint",
    "LayoutFeasibility",
    "PosteriorReport",
    "QualityDropPrior",
    "ResidualProblem",
    "RuntimeEvidence",
    "ShapeTarget",
    "build_residual_problem",
    "decision_value_for_truth",
    "estimate_posterior_v2",
    "evidence_store_from_fatbeans_events",
    "is_tail_supported_by_evidence",
    "known_footprints",
    "known_item_anchors",
    "layout_feasibility_from_store",
    "layout_feasibility_score",
    "q6_decision_value_for_truth",
    "shape_targets_from_store",
    "value_evidence_score",
    "QualityComboPresolve",
    "is_quality_combo_reachable",
    "load_quality_combo_presolve",
    "quality_combo_presolve_for_map",
    "quality_combo_presolve_payload",
    "BucketTruth",
    "SessionTruth",
    "is_huge_item",
    "sample_session_truth",
    "ToolSpec",
    "ToolEffect",
    "TOOL_SPECS",
    "SESSION_TOOL_SPECS",
    "apply_tool",
    "build_session_obs",
    "ToolROI",
    "compute_tool_roi",
    "SnipeRecommendation",
    "compute_snipe_recommendation",
    "PassRecommendation",
    "compute_pass_recommendation",
)
