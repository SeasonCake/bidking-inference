"""Evidence registry for the v3 inference rebuild.

This module is intentionally free of sampler logic. Its job is to make the
meaning and modeling status of observed Fatbeans ids explicit, so parser or
UI paths cannot silently ignore meaningful evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EvidenceStrength = Literal[
    "hard",
    "soft",
    "partial",
    "diagnostic",
    "pending",
    "unknown",
    "ignored",
]


@dataclass(frozen=True)
class EvidenceSpec:
    source_kind: str
    source_id: str
    semantic: str
    model_use: str
    constraint: str
    reference: str = "known"
    strength: EvidenceStrength = "soft"
    targets: tuple[str, ...] = ()
    affects_formal: bool = True
    notes: str = ""

    @property
    def is_pending(self) -> bool:
        return self.constraint == "pending" or self.strength == "pending"

    @property
    def is_unknown(self) -> bool:
        return self.constraint == "unknown" or self.strength == "unknown"

    def legacy_public_semantic(self) -> dict[str, str]:
        return {
            "semantic": self.semantic,
            "model_use": self.model_use,
            "constraint": self.constraint,
            "reference": self.reference,
        }


def _strength_from_constraint(constraint: str) -> EvidenceStrength:
    if constraint in {"hard", "hard_item", "hard_global", "hard_numeric"}:
        return "hard"
    if constraint == "soft":
        return "soft"
    if constraint == "partial":
        return "partial"
    if constraint == "diagnostic":
        return "diagnostic"
    if constraint == "pending":
        return "pending"
    if constraint == "unknown":
        return "unknown"
    if constraint == "ignored":
        return "ignored"
    return "soft"


def _spec(
    source_kind: str,
    source_id: int | str,
    semantic: str,
    model_use: str,
    constraint: str,
    *,
    targets: tuple[str, ...] = (),
    affects_formal: bool | None = None,
    reference: str = "known",
    notes: str = "",
) -> EvidenceSpec:
    strength = _strength_from_constraint(constraint)
    if affects_formal is None:
        affects_formal = strength in {"hard", "soft", "partial"}
    return EvidenceSpec(
        source_kind=source_kind,
        source_id=str(source_id),
        semantic=semantic,
        model_use=model_use,
        constraint=constraint,
        reference=reference,
        strength=strength,
        targets=targets,
        affects_formal=affects_formal,
        notes=notes,
    )


def _unknown(source_kind: str, source_id: int | str) -> EvidenceSpec:
    return _spec(
        source_kind,
        source_id,
        "unknown",
        "unknown_pending_reference",
        "unknown",
        reference="missing",
        affects_formal=False,
    )


PUBLIC_INFO_SPECS: dict[int, EvidenceSpec] = {
    200001: _spec("public_info", 200001, "q4_all_outlines", "modeled_bucket_outline", "hard", targets=("bucket.q4.count", "bucket.q4.cells", "shape_anchors")),
    200002: _spec("public_info", 200002, "q5_all_outlines", "modeled_bucket_outline", "hard", targets=("bucket.q5.count", "bucket.q5.cells", "shape_anchors")),
    200003: _spec("public_info", 200003, "q6_all_outlines", "modeled_bucket_outline", "hard", targets=("bucket.q6.count", "bucket.q6.cells", "shape_anchors")),
    200004: _spec("public_info", 200004, "all_item_quality", "generic_item_evidence", "partial", targets=("quality_floors",)),
    200009: _spec("public_info", 200009, "total_cells", "modeled_numeric_exact", "hard", targets=("session.total_cells",)),
    200010: _spec("public_info", 200010, "q4_total_cells", "modeled_numeric_exact", "hard", targets=("bucket.q4.cells",)),
    200011: _spec("public_info", 200011, "q5_total_cells", "modeled_numeric_exact", "hard", targets=("bucket.q5.cells",)),
    200012: _spec("public_info", 200012, "q6_total_cells", "modeled_numeric_exact", "hard", targets=("bucket.q6.cells",)),
    200013: _spec("public_info", 200013, "q4_avg_cells", "modeled_soft_avg_cells", "soft", targets=("bucket.q4.cells", "bucket.q4.count")),
    200014: _spec("public_info", 200014, "total_avg_cells", "modeled_soft_total_avg_cells", "soft", targets=("session.total_cells", "session.total_count")),
    200015: _spec("public_info", 200015, "q5_avg_cells", "modeled_soft_avg_cells", "soft", targets=("bucket.q5.cells", "bucket.q5.count")),
    200016: _spec("public_info", 200016, "q6_avg_cells", "modeled_soft_avg_cells", "soft", targets=("bucket.q6.cells", "bucket.q6.count")),
    200017: _spec("public_info", 200017, "total_item_count", "modeled_numeric_exact", "hard", targets=("session.total_count",)),
    200018: _spec("public_info", 200018, "q4_item_count", "modeled_numeric_exact", "hard", targets=("bucket.q4.count",)),
    200019: _spec("public_info", 200019, "q5_item_count", "modeled_numeric_exact", "hard", targets=("bucket.q5.count",)),
    200020: _spec("public_info", 200020, "q6_item_count", "modeled_numeric_exact", "hard", targets=("bucket.q6.count",)),
    200021: _spec("public_info", 200021, "random_2_item_reveal", "modeled_item_anchor_shape_layout", "hard_item", targets=("item_anchors", "shape_anchors")),
    200022: _spec("public_info", 200022, "random_4_item_reveal", "modeled_item_anchor_shape_layout", "hard_item", targets=("item_anchors", "shape_anchors")),
    200023: _spec("public_info", 200023, "random_6_item_reveal", "modeled_item_anchor_shape_layout", "hard_item", targets=("item_anchors", "shape_anchors")),
    200024: _spec("public_info", 200024, "random_8_item_reveal", "modeled_item_anchor_shape_layout", "hard_item", targets=("item_anchors", "shape_anchors")),
    200025: _spec("public_info", 200025, "random_12_item_reveal", "modeled_item_anchor_shape_layout", "hard_item", targets=("item_anchors", "shape_anchors")),
    200026: _spec("public_info", 200026, "random_3_quality_reveal", "modeled_quality_floor_if_keyed", "partial", targets=("quality_floors",)),
    200027: _spec("public_info", 200027, "random_6_quality_reveal", "modeled_quality_floor_if_keyed", "partial", targets=("quality_floors",)),
    200028: _spec("public_info", 200028, "random_9_quality_reveal", "modeled_quality_floor_if_keyed", "partial", targets=("quality_floors",)),
    200029: _spec("public_info", 200029, "random_12_quality_reveal", "modeled_quality_floor_if_keyed", "partial", targets=("quality_floors",)),
    200030: _spec("public_info", 200030, "all_item_quality", "generic_item_evidence", "partial", targets=("quality_floors",)),
    200031: _spec("public_info", 200031, "random_3_avg_value", "diagnostic_random_avg_signal", "diagnostic", targets=("random_avg_value",), affects_formal=False),
    200032: _spec("public_info", 200032, "random_6_avg_value", "diagnostic_random_avg_signal", "diagnostic", targets=("random_avg_value",), affects_formal=False),
    200033: _spec("public_info", 200033, "random_9_avg_value", "diagnostic_random_avg_signal", "diagnostic", targets=("random_avg_value",), affects_formal=False),
    200034: _spec("public_info", 200034, "random_12_avg_value", "diagnostic_random_avg_signal", "diagnostic", targets=("random_avg_value",), affects_formal=False),
    200035: _spec("public_info", 200035, "total_avg_value", "pending_global_avg_value", "pending", targets=("session.avg_value",), affects_formal=False),
    200036: _spec("public_info", 200036, "q4_avg_value", "modeled_soft_avg_value", "soft", targets=("bucket.q4.value",)),
    200037: _spec("public_info", 200037, "q5_avg_value", "modeled_soft_avg_value", "soft", targets=("bucket.q5.value",)),
    200038: _spec("public_info", 200038, "q6_avg_value", "modeled_soft_avg_value", "soft", targets=("bucket.q6.value",)),
    200039: _spec("public_info", 200039, "all_outlines", "modeled_layout_if_shapes_present", "partial", targets=("shape_anchors", "session.total_cells")),
    200048: _spec("public_info", 200048, "highest_quality_item", "modeled_global_max_quality", "hard_global", targets=("quality_ceiling", "item_anchors")),
    200049: _spec("public_info", 200049, "highest_value_item", "modeled_item_anchor_shape_layout", "hard_item", targets=("item_anchors", "value_ceiling")),
    200050: _spec("public_info", 200050, "largest_cell_item", "modeled_global_max_item_cells", "hard_global", targets=("max_item_cells", "item_anchors")),
    200052: _spec("public_info", 200052, "highest_quality_value", "pending_numeric_exact", "pending", targets=("quality_value_ceiling",), affects_formal=False),
}


ACTION_RESULT_SPECS: dict[int, EvidenceSpec] = {
    100100: _spec("action_result", 100100, "full_outline_session_total", "modeled_full_outline", "hard", targets=("session.total_cells", "session.total_count", "shape_anchors")),
    100103: _spec("action_result", 100103, "total_cells", "modeled_numeric_exact", "hard", targets=("session.total_cells",)),
    100104: _spec("action_result", 100104, "q1_total_cells", "modeled_numeric_exact", "hard", targets=("bucket.q1.cells",)),
    100105: _spec("action_result", 100105, "q3_total_cells", "modeled_numeric_exact", "hard", targets=("bucket.q3.cells",)),
    100106: _spec("action_result", 100106, "q4_total_cells", "modeled_numeric_exact", "hard", targets=("bucket.q4.cells",)),
    100107: _spec("action_result", 100107, "q5_total_cells", "modeled_numeric_exact", "hard", targets=("bucket.q5.cells",)),
    100108: _spec("action_result", 100108, "q6_total_cells", "modeled_numeric_exact", "hard", targets=("bucket.q6.cells",)),
    100110: _spec("action_result", 100110, "q1_avg_cells", "modeled_avg_cells", "soft", targets=("bucket.q1.cells", "bucket.q1.count")),
    100111: _spec("action_result", 100111, "q3_avg_cells", "modeled_avg_cells", "soft", targets=("bucket.q3.cells", "bucket.q3.count")),
    100112: _spec("action_result", 100112, "q4_avg_cells", "modeled_avg_cells", "soft", targets=("bucket.q4.cells", "bucket.q4.count")),
    100113: _spec("action_result", 100113, "q5_avg_cells", "modeled_avg_cells", "soft", targets=("bucket.q5.cells", "bucket.q5.count")),
    100114: _spec("action_result", 100114, "q6_avg_cells", "modeled_avg_cells", "soft", targets=("bucket.q6.cells", "bucket.q6.count")),
    100115: _spec("action_result", 100115, "total_item_count", "modeled_numeric_exact", "hard", targets=("session.total_count",)),
    100116: _spec("action_result", 100116, "q1_item_count", "modeled_numeric_exact", "hard", targets=("bucket.q1.count",)),
    100117: _spec("action_result", 100117, "q3_item_count", "modeled_numeric_exact", "hard", targets=("bucket.q3.count",)),
    100118: _spec("action_result", 100118, "q4_item_count", "modeled_numeric_exact", "hard", targets=("bucket.q4.count",)),
    100119: _spec("action_result", 100119, "q5_item_count", "modeled_numeric_exact", "hard", targets=("bucket.q5.count",)),
    100120: _spec("action_result", 100120, "q6_item_count", "modeled_numeric_exact", "hard", targets=("bucket.q6.count",)),
    100122: _spec("action_result", 100122, "q1_value_sum", "modeled_numeric_exact", "hard", targets=("bucket.q1.value",)),
    100123: _spec("action_result", 100123, "q3_value_sum", "modeled_numeric_exact", "hard", targets=("bucket.q3.value",)),
    100124: _spec("action_result", 100124, "q4_value_sum", "modeled_numeric_exact", "hard", targets=("bucket.q4.value",)),
    100125: _spec("action_result", 100125, "q5_value_sum", "modeled_numeric_exact", "hard", targets=("bucket.q5.value",)),
    100126: _spec("action_result", 100126, "q6_value_sum", "modeled_numeric_exact", "hard", targets=("bucket.q6.value",)),
    100128: _spec("action_result", 100128, "single_item_reveal", "modeled_item_anchor_shape_layout", "hard_item", targets=("item_anchors", "shape_anchors")),
    100129: _spec("action_result", 100129, "random_2_item_reveal", "modeled_item_anchor_shape_layout", "hard_item", targets=("item_anchors", "shape_anchors")),
    100130: _spec("action_result", 100130, "random_4_item_reveal", "modeled_item_anchor_shape_layout", "hard_item", targets=("item_anchors", "shape_anchors")),
    100131: _spec("action_result", 100131, "random_6_item_reveal", "modeled_item_anchor_shape_layout", "hard_item", targets=("item_anchors", "shape_anchors")),
    100134: _spec("action_result", 100134, "mirror_quality_join", "modeled_quality_floor_if_keyed", "partial", targets=("quality_floors", "runtime_join")),
    100135: _spec("action_result", 100135, "random_2_quality_reveal", "modeled_quality_floor_if_keyed", "partial", targets=("quality_floors",)),
    100136: _spec("action_result", 100136, "random_4_quality_reveal", "modeled_quality_floor_if_keyed", "partial", targets=("quality_floors",)),
    100137: _spec("action_result", 100137, "random_6_quality_reveal", "modeled_quality_floor_if_keyed", "partial", targets=("quality_floors",)),
    100138: _spec("action_result", 100138, "random_8_quality_reveal", "modeled_quality_floor_if_keyed", "partial", targets=("quality_floors",)),
    100139: _spec("action_result", 100139, "random_10_quality_reveal", "modeled_quality_floor_if_keyed", "partial", targets=("quality_floors",)),
    100140: _spec("action_result", 100140, "random_12_quality_reveal", "modeled_quality_floor_if_keyed", "partial", targets=("quality_floors",)),
    100151: _spec("action_result", 100151, "category_outline_101", "modeled_category_item_evidence", "hard_item", targets=("category_anchors", "shape_anchors")),
    100152: _spec("action_result", 100152, "category_outline_102", "modeled_category_item_evidence", "hard_item", targets=("category_anchors", "shape_anchors")),
    100153: _spec("action_result", 100153, "category_outline_103", "modeled_category_item_evidence", "hard_item", targets=("category_anchors", "shape_anchors")),
    100154: _spec("action_result", 100154, "category_outline_104", "modeled_category_item_evidence", "hard_item", targets=("category_anchors", "shape_anchors")),
    100155: _spec("action_result", 100155, "category_outline_105", "modeled_category_item_evidence", "hard_item", targets=("category_anchors", "shape_anchors")),
    100156: _spec("action_result", 100156, "category_outline_106", "modeled_category_item_evidence", "hard_item", targets=("category_anchors", "shape_anchors")),
    100157: _spec("action_result", 100157, "category_outline_107", "modeled_category_item_evidence", "hard_item", targets=("category_anchors", "shape_anchors")),
    100158: _spec("action_result", 100158, "category_outline_108", "modeled_category_item_evidence", "hard_item", targets=("category_anchors", "shape_anchors")),
    100159: _spec("action_result", 100159, "category_outline_109", "modeled_category_item_evidence", "hard_item", targets=("category_anchors", "shape_anchors")),
    100160: _spec("action_result", 100160, "category_outline_110", "modeled_category_item_evidence", "hard_item", targets=("category_anchors", "shape_anchors")),
    100168: _spec("action_result", 100168, "large_item_reveal", "modeled_item_anchor_shape_layout", "hard_item", targets=("item_anchors", "max_item_cells")),
    100169: _spec("action_result", 100169, "size_1_avg_value", "modeled_soft_size_avg_value", "soft", targets=("size_bucket_value",)),
    100170: _spec("action_result", 100170, "size_2_avg_value", "modeled_soft_size_avg_value", "soft", targets=("size_bucket_value",)),
    100171: _spec("action_result", 100171, "size_3_avg_value", "modeled_soft_size_avg_value", "soft", targets=("size_bucket_value",)),
    100172: _spec("action_result", 100172, "size_4_avg_value", "modeled_soft_size_avg_value", "soft", targets=("size_bucket_value",)),
    100173: _spec("action_result", 100173, "size_6_avg_value", "modeled_soft_size_avg_value", "soft", targets=("size_bucket_value",)),
}


SKILL_REVEAL_SPECS: dict[int, EvidenceSpec] = {
    100101: _spec("skill_reveal", 100101, "category_106_reveal", "modeled_category_item_evidence", "hard_item", targets=("category_anchors", "shape_anchors")),
    1001011: _spec("skill_reveal", 1001011, "category_106_reveal", "modeled_category_item_evidence", "hard_item", targets=("category_anchors", "shape_anchors")),
    1001012: _spec("skill_reveal", 1001012, "category_106_reveal", "modeled_category_item_evidence", "hard_item", targets=("category_anchors", "shape_anchors")),
    1001013: _spec("skill_reveal", 1001013, "category_106_reveal", "modeled_category_item_evidence", "hard_item", targets=("category_anchors", "shape_anchors")),
    1001014: _spec("skill_reveal", 1001014, "category_106_reveal", "modeled_category_item_evidence", "hard_item", targets=("category_anchors", "shape_anchors")),
    1001031: _spec("skill_reveal", 1001031, "aisha_q4_outline", "modeled_aisha_bucket_outline", "hard", targets=("bucket.q4.count", "bucket.q4.cells", "shape_anchors")),
    1001032: _spec("skill_reveal", 1001032, "aisha_q3_outline", "modeled_aisha_bucket_outline", "hard", targets=("bucket.q3.count", "bucket.q3.cells", "shape_anchors")),
    1001033: _spec("skill_reveal", 1001033, "aisha_q2_outline", "modeled_aisha_bucket_outline", "hard", targets=("bucket.q2.count", "bucket.q2.cells", "shape_anchors")),
    1001034: _spec("skill_reveal", 1001034, "aisha_q1_outline", "modeled_aisha_bucket_outline", "hard", targets=("bucket.q1.count", "bucket.q1.cells", "shape_anchors")),
    1001041: _spec("skill_reveal", 1001041, "hero_skill_item_reveal", "modeled_item_anchor_shape_layout", "hard_item", targets=("item_anchors", "shape_anchors")),
    1001042: _spec("skill_reveal", 1001042, "hero_skill_item_reveal", "modeled_item_anchor_shape_layout", "hard_item", targets=("item_anchors", "shape_anchors")),
    1001043: _spec("skill_reveal", 1001043, "hero_skill_item_reveal", "modeled_item_anchor_shape_layout", "hard_item", targets=("item_anchors", "shape_anchors")),
    1001044: _spec("skill_reveal", 1001044, "hero_skill_item_reveal", "modeled_item_anchor_shape_layout", "hard_item", targets=("item_anchors", "shape_anchors")),
    1001045: _spec("skill_reveal", 1001045, "hero_skill_item_reveal", "modeled_item_anchor_shape_layout", "hard_item", targets=("item_anchors", "shape_anchors")),
    100105: _spec("skill_reveal", 100105, "category_103_reveal", "modeled_category_item_evidence", "hard_item", targets=("category_anchors", "shape_anchors")),
    100107: _spec("skill_reveal", 100107, "hero_skill_item_reveal", "modeled_item_anchor_shape_layout", "hard_item", targets=("item_anchors", "shape_anchors")),
    1001071: _spec("skill_reveal", 1001071, "hero_skill_item_reveal", "modeled_item_anchor_shape_layout", "hard_item", targets=("item_anchors", "shape_anchors")),
    1001072: _spec("skill_reveal", 1001072, "hero_skill_item_reveal", "modeled_item_anchor_shape_layout", "hard_item", targets=("item_anchors", "shape_anchors")),
    1001073: _spec("skill_reveal", 1001073, "hero_skill_item_reveal", "modeled_item_anchor_shape_layout", "hard_item", targets=("item_anchors", "shape_anchors")),
    1001074: _spec("skill_reveal", 1001074, "hero_skill_item_reveal", "modeled_item_anchor_shape_layout", "hard_item", targets=("item_anchors", "shape_anchors")),
    100109: _spec("skill_reveal", 100109, "category_102_reveal", "modeled_category_item_evidence", "hard_item", targets=("category_anchors", "shape_anchors")),
    1001091: _spec("skill_reveal", 1001091, "category_102_reveal", "modeled_category_item_evidence", "hard_item", targets=("category_anchors", "shape_anchors")),
    1001092: _spec("skill_reveal", 1001092, "category_102_reveal", "modeled_category_item_evidence", "hard_item", targets=("category_anchors", "shape_anchors")),
    1001093: _spec("skill_reveal", 1001093, "category_102_reveal", "modeled_category_item_evidence", "hard_item", targets=("category_anchors", "shape_anchors")),
    1001094: _spec("skill_reveal", 1001094, "category_102_reveal", "modeled_category_item_evidence", "hard_item", targets=("category_anchors", "shape_anchors")),
    100110: _spec("skill_reveal", 100110, "hero_skill_item_reveal", "modeled_item_anchor_shape_layout", "hard_item", targets=("item_anchors", "shape_anchors")),
    1001101: _spec("skill_reveal", 1001101, "category_105_reveal", "modeled_category_item_evidence", "hard_item", targets=("category_anchors", "shape_anchors")),
    100201: _spec("skill_reveal", 100201, "category_104_reveal", "modeled_category_item_evidence", "hard_item", targets=("category_anchors", "shape_anchors")),
    100204: _spec("skill_reveal", 100204, "ahmed_total_item_count", "modeled_numeric_exact", "hard", targets=("session.total_count",), notes="Ahmad R1: total item count from HitItemIndex-style numeric result."),
    1002041: _spec("skill_reveal", 1002041, "ahmed_q5_avg_cells", "modeled_soft_avg_cells", "soft", targets=("bucket.q5.cells", "bucket.q5.count"), notes="Ahmad R2: gold/orange average cells."),
    1002042: _spec("skill_reveal", 1002042, "ahmed_q4_avg_cells", "modeled_soft_avg_cells", "soft", targets=("bucket.q4.cells", "bucket.q4.count"), notes="Ahmad R3: purple average cells."),
    1002043: _spec("skill_reveal", 1002043, "ahmed_q3_avg_cells", "modeled_soft_avg_cells", "soft", targets=("bucket.q3.cells", "bucket.q3.count"), notes="Ahmad R4: blue average cells."),
    1002044: _spec("skill_reveal", 1002044, "ahmed_q1_item_count", "modeled_numeric_exact", "hard", targets=("bucket.q1.count",), notes="Ahmad R5: green+white merged count, aligned with existing q1 merged normal-quality bucket."),
    100206: _spec("skill_reveal", 100206, "category_110_reveal", "modeled_category_item_evidence", "hard_item", targets=("category_anchors", "shape_anchors")),
    1002062: _spec("skill_reveal", 1002062, "category_110_reveal", "modeled_category_item_evidence", "hard_item", targets=("category_anchors", "shape_anchors")),
    1002063: _spec("skill_reveal", 1002063, "category_110_reveal", "modeled_category_item_evidence", "hard_item", targets=("category_anchors", "shape_anchors")),
    1002064: _spec("skill_reveal", 1002064, "category_110_reveal", "modeled_category_item_evidence", "hard_item", targets=("category_anchors", "shape_anchors")),
    1002065: _spec("skill_reveal", 1002065, "category_110_reveal", "modeled_category_item_evidence", "hard_item", targets=("category_anchors", "shape_anchors")),
    1002081: _spec("skill_reveal", 1002081, "ethan_outline", "modeled_ethan_outline", "hard_item", targets=("shape_anchors",)),
    1002082: _spec("skill_reveal", 1002082, "ethan_outline", "modeled_ethan_outline", "hard_item", targets=("shape_anchors",)),
    1002083: _spec("skill_reveal", 1002083, "ethan_outline", "modeled_ethan_outline", "hard_item", targets=("shape_anchors",)),
    1002084: _spec("skill_reveal", 1002084, "ethan_outline", "modeled_ethan_outline", "hard_item", targets=("shape_anchors",)),
    1002085: _spec("skill_reveal", 1002085, "ethan_full_outline", "modeled_full_outline", "hard", targets=("session.total_cells", "session.total_count", "shape_anchors")),
    10002071: _spec("skill_reveal", 10002071, "category_106_reveal", "modeled_category_item_evidence", "hard_item", targets=("category_anchors", "shape_anchors")),
    10002072: _spec("skill_reveal", 10002072, "category_106_quality_reveal", "modeled_quality_floor_if_keyed", "partial", targets=("quality_floors",)),
    10002073: _spec("skill_reveal", 10002073, "category_106_full_reveal", "modeled_item_anchor_shape_layout", "hard_item", targets=("item_anchors", "shape_anchors")),
}


def public_info_spec(info_id: int) -> EvidenceSpec:
    return PUBLIC_INFO_SPECS.get(int(info_id), _unknown("public_info", info_id))


def action_result_spec(action_id: int) -> EvidenceSpec:
    return ACTION_RESULT_SPECS.get(int(action_id), _unknown("action_result", action_id))


def skill_reveal_spec(skill_id: int) -> EvidenceSpec:
    return SKILL_REVEAL_SPECS.get(int(skill_id), _unknown("skill_reveal", skill_id))


def public_info_semantic(info_id: int) -> dict[str, str]:
    return public_info_spec(info_id).legacy_public_semantic()


def public_info_semantics_dict() -> dict[int, dict[str, str]]:
    return {
        info_id: spec.legacy_public_semantic()
        for info_id, spec in sorted(PUBLIC_INFO_SPECS.items())
    }


__all__ = (
    "ACTION_RESULT_SPECS",
    "PUBLIC_INFO_SPECS",
    "SKILL_REVEAL_SPECS",
    "EvidenceSpec",
    "EvidenceStrength",
    "action_result_spec",
    "public_info_semantic",
    "public_info_semantics_dict",
    "public_info_spec",
    "skill_reveal_spec",
)
