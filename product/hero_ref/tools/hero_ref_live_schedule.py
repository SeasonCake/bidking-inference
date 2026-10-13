"""Live ref inference schedule: hero skill-round categories and compute triggers.

Each hero is assigned a category that defines when ref_v0 runs (skill frame vs
public/prop/layout) and how many combos to enumerate per round. Overlay/panel
code imports this module; engine semantics stay in ahmad_ref_engine.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from enum import Enum
from numbers import Real
from typing import Any, Callable

# Combo caps — global defaults; per-category overrides in hero_max_combos_for_round.
REF_SPARSE_MAX_COMBOS = 1500
REF_EARLY_MAX_COMBOS = 2500
REF_MID_MAX_COMBOS = 8000
REF_LATE_MAX_COMBOS = 12000
REF_FULL_MAX_COMBOS = 20000

EARLY_ROUND_ENGINE_FLAG = "audit_aisha_early_round"
SCHEDULED_SKILL_SPARSE_FAIL_OPEN_DISABLE = (
    "BIDKING_DISABLE_SCHEDULED_SKILL_SPARSE_FAIL_OPEN"
)
POSTUPDATE_NINE_HERO_SKILL_COMPAT_DISABLE = (
    "BIDKING_DISABLE_POSTUPDATE_NINE_HERO_SKILL_COMPAT"
)


class HeroInferenceCategory(str, Enum):
    """Skill-round pattern buckets for live ref scheduling."""

    MULTI_ROUND_SKILL = "multi_round_skill"
    PROGRESSIVE_COUNT = "progressive_count"
    EARLY_SINGLE_SKILL = "early_single_skill"
    LATE_SINGLE_SKILL = "late_single_skill"
    EARLY_LATE_SKILL = "early_late_skill"
    R1_BURST = "r1_burst"
    R1_STATIC = "r1_static"
    STAGED_ROUNDS = "staged_rounds"
    SPARSE_PUBLIC_PROP = "sparse_public_prop"


CATEGORY_LABELS_ZH: dict[HeroInferenceCategory, str] = {
    HeroInferenceCategory.MULTI_ROUND_SKILL: "多轮技能帧（每轮触发）",
    HeroInferenceCategory.PROGRESSIVE_COUNT: "渐进件数/均格（每轮技能）",
    HeroInferenceCategory.EARLY_SINGLE_SKILL: "首轮单技能（R1 触发，其后公开/道具）",
    HeroInferenceCategory.LATE_SINGLE_SKILL: "末期单技能（前期公开/布局）",
    HeroInferenceCategory.EARLY_LATE_SKILL: "首末双技能",
    HeroInferenceCategory.R1_BURST: "首轮/跨轮技能爆发",
    HeroInferenceCategory.R1_STATIC: "首轮固定信息",
    HeroInferenceCategory.STAGED_ROUNDS: "分阶段递进（多轮不同技能）",
    HeroInferenceCategory.SPARSE_PUBLIC_PROP: "公开/道具/手填驱动",
}

# Live ref tuning priority — engine/overlay schedule changes focus here first.
PRIORITY_MANAGED_HERO_KEYS = frozenset(
    {"aisha", "ahmed", "raven", "wuqilin", "sophie", "gabriela"}
)

# Public numeric semantics consumed by ``ahmad_ref_engine.extract_evidence``.
# A label/summary is deliberately not a fact: scheduler readiness must follow
# the same typed evidence that the engine can actually consume.
_PUBLIC_NUMERIC_SEMANTICS = frozenset(
    {
        "total_item_count",
        "total_cells",
        "total_avg_cells",
        "total_avg_value",
        "q4_item_count",
        "q5_item_count",
        "q6_item_count",
        "q4_avg_cells",
        "q5_avg_cells",
        "q6_avg_cells",
        "q4_avg_value",
        "q5_avg_value",
        "q6_avg_value",
        "random_3_avg_value",
        "random_6_avg_value",
        "random_9_avg_value",
        "random_12_avg_value",
    }
)
_PUBLIC_NUMERIC_INFO_IDS = frozenset(
    {
        200009,
        200010,
        200011,
        200012,
        200013,
        200014,
        200015,
        200016,
        200017,
        200018,
        200019,
        200020,
        200031,
        200032,
        200033,
        200034,
        200035,
        200036,
        200037,
        200038,
    }
)
_PUBLIC_PRESENCE_INFO_IDS = frozenset(
    {
        200001,
        200002,
        200003,
        200004,
        200028,
        200030,
        200048,
    }
)
_QUALITY_KEYS = frozenset({"q1", "q2", "q3", "q4", "q5", "q6"})
_EXACT_PRESENCE_WORDS = frozenset(
    {"exact", "observed", "packet", "public_info", "settlement"}
)
_UNQUALIFIED_WORDS = frozenset(
    {"estimated", "estimate", "approx", "approximate", "placeholder", "pending"}
)


@dataclass(frozen=True)
class HeroRefSchedule:
    hero_key: str
    hero_id: int
    category: HeroInferenceCategory
    skill_rounds: dict[int, int]
    skill_round_labels: dict[int, str]
    dual_pass_max_round: int | None = None
    early_round_boundary: int = 3
    alt_total_action_ids: frozenset[str] = frozenset({"100115", "100204"})
    focus_managed: bool = False
    scheduled_skill_sparse_fail_open: bool = False


def _parse_int(value: Any) -> int | None:
    """Accept only parser-contract integer values, never coercible lookalikes."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _parse_shape_code(value: Any) -> int | None:
    """Accept the live shape representations without weakening identity fields."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text.isdecimal():
        return None
    return int(text)


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip() or default


def _finite_nonnegative_number(value: Any) -> float | None:
    """Permissive manual-input parser retained for existing UI text fields."""
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _finite_nonnegative_wire_number(value: Any) -> float | None:
    """Validate parser numeric values without coercing strings or booleans."""
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    try:
        parsed = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _mapping_is_estimated_or_placeholder(row: dict[str, Any]) -> bool:
    for key in ("estimated", "is_estimated", "placeholder", "is_placeholder"):
        if row.get(key) is True:
            return True
    for key in ("confidence", "provenance", "source", "status", "render_mode"):
        word = _text(row.get(key)).lower()
        if any(marker in word for marker in _UNQUALIFIED_WORDS):
            return True
    return False


def _shape_has_positive_area(row: dict[str, Any]) -> bool:
    cells = _parse_int(row.get("cells"))
    if cells is not None and cells > 0:
        return True
    raw_shape = row.get("shape_code")
    if raw_shape is None:
        raw_shape = row.get("shape_key")
    shape = _parse_shape_code(raw_shape)
    if shape is not None and shape // 10 > 0 and shape % 10 > 0:
        return True
    width = _parse_int(row.get("width"))
    height = _parse_int(row.get("height"))
    return bool(width and height)


def _observed_item_has_signal(row: dict[str, Any], *, require_layout: bool) -> bool:
    if not row or _mapping_is_estimated_or_placeholder(row):
        return False
    runtime_id = _parse_int(row.get("runtime_id"))
    local_index = _parse_int(row.get("local_index"))
    item_id = _parse_int(row.get("item_id"))
    row_no = _parse_int(row.get("row"))
    col_no = _parse_int(row.get("col"))
    has_identity = (
        runtime_id is not None
        or local_index is not None
        or item_id is not None
        or (row_no is not None and col_no is not None)
    )
    has_layout = _shape_has_positive_area(row)
    if require_layout:
        return has_identity and has_layout
    quality = _parse_int(row.get("quality"))
    value = _parse_int(row.get("value"))
    return has_identity and (
        has_layout
        or quality in {1, 2, 3, 4, 5, 6}
        or value is not None
    )


def _rows_have_observed_item_signal(
    rows: Any,
    *,
    require_layout: bool,
) -> bool:
    return isinstance(rows, list) and any(
        isinstance(row, dict)
        and _observed_item_has_signal(row, require_layout=require_layout)
        for row in rows
    )


def _mapping_has_exact_presence(row: dict[str, Any], *, prefix: str = "") -> bool:
    keys = (
        f"{prefix}_exact" if prefix else "exact",
        f"{prefix}_present" if prefix else "present",
        f"{prefix}_observed" if prefix else "observed",
    )
    if any(row.get(key) is True for key in keys):
        return True
    source_keys = (
        f"{prefix}_confidence" if prefix else "confidence",
        f"{prefix}_provenance" if prefix else "provenance",
        f"{prefix}_source" if prefix else "source",
    )
    return any(
        _text(row.get(key)).lower() in _EXACT_PRESENCE_WORDS
        for key in source_keys
    )


def _public_numeric_fact_has_signal(row: dict[str, Any]) -> bool:
    if not row or _mapping_is_estimated_or_placeholder(row):
        return False
    semantic = _text(row.get("semantic")).lower()
    if semantic not in _PUBLIC_NUMERIC_SEMANTICS or "value" not in row:
        return False
    value = _finite_nonnegative_wire_number(row.get("value"))
    if value is None:
        return False
    if semantic.startswith("random_") and value == 0:
        return False
    return True


def _quality_counts_have_signal(block: dict[str, Any]) -> bool:
    quality_count_meta = {
        key.removeprefix("quality_counts_"): value
        for key, value in block.items()
        if key.startswith("quality_counts_")
    }
    if (
        _mapping_is_estimated_or_placeholder(block)
        or _mapping_is_estimated_or_placeholder(quality_count_meta)
    ):
        return False
    counts = block.get("quality_counts")
    if not isinstance(counts, dict) or not counts:
        return False
    for raw_key, raw_value in counts.items():
        key = _text(raw_key).lower()
        if key not in _QUALITY_KEYS:
            continue
        value_row = raw_value if isinstance(raw_value, dict) else None
        value = (
            _parse_int(value_row.get("value"))
            if value_row is not None
            else _parse_int(raw_value)
        )
        if value is None:
            continue
        if value > 0:
            if value_row is None or not _mapping_is_estimated_or_placeholder(value_row):
                return True
            continue
        if (
            value_row is not None
            and not _mapping_is_estimated_or_placeholder(value_row)
            and _mapping_has_exact_presence(value_row)
        ):
            return True
        if _mapping_has_exact_presence(block, prefix="quality_counts"):
            return True
    return False


def _iter_snapshot_skill_reveal_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for key in ("skill_reveal_rows", "skill_reveals"):
        payload = snapshot.get(key)
        if not isinstance(payload, list):
            continue
        for row in payload:
            if not isinstance(row, dict):
                continue
            row_id = id(row)
            if row_id in seen:
                continue
            seen.add(row_id)
            rows.append(row)
    return rows


def _snapshot_action_result_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    top = snapshot.get("action_result_rows")
    if isinstance(top, list):
        rows.extend(row for row in top if isinstance(row, dict))
    uc = snapshot.get("ui_contract") if isinstance(snapshot.get("ui_contract"), dict) else {}
    actions = uc.get("actions") if isinstance(uc.get("actions"), dict) else {}
    for row in actions.get("results") or ():
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _skill_reveal_row_has_signal(row: dict[str, Any]) -> bool:
    revealed = _parse_int(row.get("revealed_items"))
    if revealed is not None and revealed > 0:
        return True
    result_val = row.get("result")
    parsed_result = _finite_nonnegative_wire_number(result_val)
    if parsed_result is not None:
        if parsed_result != 0.0:
            return True
        # A wired numeric field carrying zero is an exact skill result, not
        # a missing frame. Naomi's scoped Fashion+Digital gold/red count
        # can legitimately be zero.
        if _parse_int(row.get("result_field")) is not None:
            return True
    items = row.get("observed_items") or row.get("revealed_items_detail") or ()
    return _rows_have_observed_item_signal(items, require_layout=False)


def _round_skill_frame_ready(
    snapshot: dict[str, Any],
    *,
    hero_id: int,
    skill_id: int,
) -> bool:
    for row in _iter_snapshot_skill_reveal_rows(snapshot):
        if _parse_int(row.get("hero_id")) != hero_id:
            continue
        if _parse_int(row.get("skill_id")) != skill_id:
            continue
        if _skill_reveal_row_has_signal(row):
            return True
    return False


def _public_info_row_has_signal(row: dict[str, Any]) -> bool:
    if not row or _mapping_is_estimated_or_placeholder(row):
        return False
    info_id = _parse_int(row.get("info_id"))
    if info_id in _PUBLIC_NUMERIC_INFO_IDS:
        return (
            "value" in row
            and _finite_nonnegative_wire_number(row.get("value")) is not None
        )
    if info_id not in _PUBLIC_PRESENCE_INFO_IDS:
        return False
    items = row.get("revealed_items_detail") or row.get("observed_items") or ()
    if _rows_have_observed_item_signal(items, require_layout=False):
        return True
    if (
        "value" in row
        and _finite_nonnegative_wire_number(row.get("value")) is not None
    ):
        return True
    return _mapping_has_exact_presence(row)


def _snapshot_has_public_info(snapshot: dict[str, Any]) -> bool:
    rows = snapshot.get("public_info_rows")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and _public_info_row_has_signal(row):
                return True
    uc = snapshot.get("ui_contract") if isinstance(snapshot.get("ui_contract"), dict) else {}
    constraints = uc.get("constraints") if isinstance(uc.get("constraints"), dict) else {}
    public_info = constraints.get("public_info") if isinstance(constraints.get("public_info"), dict) else {}
    for key in (
        "public_numeric_facts",
        "public_avg_cells",
        "public_avg_values",
        "public_random_avg_values",
    ):
        rows = public_info.get(key)
        if isinstance(rows, list) and any(
            isinstance(row, dict) and _public_numeric_fact_has_signal(row)
            for row in rows
        ):
            return True
    input_constraints = public_info.get("input_constraints")
    if not isinstance(input_constraints, dict):
        return False
    for key in (
        "warehouse_total_cells",
        "warehouse_total_cells_approx",
        "total_item_count",
    ):
        row = input_constraints.get(key)
        if not isinstance(row, dict) or _mapping_is_estimated_or_placeholder(row):
            continue
        if (
            "value" in row
            and _finite_nonnegative_wire_number(row.get("value")) is not None
            and _text(row.get("confidence")).lower() in {"high", "exact"}
        ):
            return True
    return False


def _prop_action_row_has_signal(row: dict[str, Any]) -> bool:
    action_id = _text(row.get("action_id"))
    if not action_id:
        return False
    if row.get("inferred_zero"):
        return True
    detail = row.get("revealed_items_detail") or row.get("observed_items") or ()
    if _rows_have_observed_item_signal(detail, require_layout=False):
        return True
    result_val = row.get("result")
    parsed_result = _finite_nonnegative_wire_number(result_val)
    return parsed_result is not None and parsed_result != 0.0


def _snapshot_has_prop_signal(snapshot: dict[str, Any]) -> bool:
    for row in _snapshot_action_result_rows(snapshot):
        if _prop_action_row_has_signal(row):
            return True
    return False


def _snapshot_has_alt_total_signal(snapshot: dict[str, Any], action_ids: frozenset[str]) -> bool:
    for row in _snapshot_action_result_rows(snapshot):
        action_id = _text(row.get("action_id"))
        if action_id not in action_ids:
            continue
        if _prop_action_row_has_signal(row) or _parse_int(row.get("result")) not in (None, 0):
            return True
    for row in _iter_snapshot_skill_reveal_rows(snapshot):
        skill_id = _text(row.get("skill_id"))
        if skill_id in action_ids and _skill_reveal_row_has_signal(row):
            return True
    return False


def _snapshot_has_manual_structured_inputs(snapshot: dict[str, Any]) -> bool:
    structured = snapshot.get("structured_ref_inputs")
    if not isinstance(structured, dict):
        return False
    for key in (
        "total_count",
        "total_item_count",
        "total_cells",
        "warehouse_total_cells",
    ):
        value = structured.get(key)
        if _finite_nonnegative_number(value) is not None:
            return True
    for key in (
        "count_sums",
        "fixed_counts",
        "min_counts",
        "avg_cells",
        "quality_cells",
        "avg_values",
        "quality_values",
        "quality_value_floors",
        "split_counts",
        "split_quality_cells",
        "split_avg_cells",
    ):
        value = structured.get(key)
        if not isinstance(value, dict):
            continue
        if any(_finite_nonnegative_number(item) is not None for item in value.values()):
            return True
    manual = snapshot.get("manual_ref_inputs")
    if isinstance(manual, dict):
        for value in manual.values():
            if _finite_nonnegative_number(value) is not None:
                return True
    return False


def _snapshot_has_layout_signal(snapshot: dict[str, Any]) -> bool:
    for key in ("minimap",):
        block = snapshot.get(key)
        if not isinstance(block, dict):
            continue
        items = block.get("items")
        if _rows_have_observed_item_signal(items, require_layout=True):
            return True
        if _quality_counts_have_signal(block):
            return True
    uc = snapshot.get("ui_contract") if isinstance(snapshot.get("ui_contract"), dict) else {}
    mm = uc.get("minimap")
    if isinstance(mm, dict):
        items = mm.get("items")
        if _rows_have_observed_item_signal(items, require_layout=True):
            return True
        if _quality_counts_have_signal(mm):
            return True
    return False


def _round_skill_sort(snapshot: dict[str, Any], *, hero_id: int, skill_id: int) -> int | None:
    anchor: int | None = None
    for row in _iter_snapshot_skill_reveal_rows(snapshot):
        if _parse_int(row.get("hero_id")) != hero_id:
            continue
        if _parse_int(row.get("skill_id")) != skill_id:
            continue
        row_sort = _parse_int(row.get("sort"))
        if row_sort is None:
            continue
        if anchor is None or row_sort > anchor:
            anchor = row_sort
    return anchor


def _round_public_or_prop_ready(
    snapshot: dict[str, Any],
    *,
    round_no: int,
    hero_id: int,
    skill_id: int | None,
) -> bool:
    floor = _round_skill_sort(snapshot, hero_id=hero_id, skill_id=skill_id) if skill_id else None
    rows = snapshot.get("public_info_rows")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict) or not _public_info_row_has_signal(row):
                continue
            row_sort = _parse_int(row.get("sort"))
            if floor is not None and (row_sort is None or row_sort < floor):
                continue
            return True
    for row in _snapshot_action_result_rows(snapshot):
        if not _prop_action_row_has_signal(row):
            continue
        row_sort = _parse_int(row.get("sort"))
        if floor is not None and (row_sort is None or row_sort < floor):
            continue
        return True
    return False


def _sparse_early_ready(snapshot: dict[str, Any]) -> bool:
    return (
        _snapshot_has_public_info(snapshot)
        or _snapshot_has_prop_signal(snapshot)
        or _snapshot_has_manual_structured_inputs(snapshot)
        or _snapshot_has_layout_signal(snapshot)
    )


HERO_REF_SCHEDULES: dict[str, HeroRefSchedule] = {
    "aisha": HeroRefSchedule(
        hero_key="aisha",
        hero_id=103,
        category=HeroInferenceCategory.MULTI_ROUND_SKILL,
        skill_rounds={1: 1001034, 2: 1001033, 3: 1001032, 4: 1001031},
        skill_round_labels={
            1: "白品技能帧",
            2: "绿品技能帧",
            3: "蓝品技能帧",
            4: "紫品技能帧",
        },
        dual_pass_max_round=5,
        early_round_boundary=3,
        focus_managed=True,
    ),
    "ahmed": HeroRefSchedule(
        hero_key="ahmed",
        hero_id=204,
        category=HeroInferenceCategory.PROGRESSIVE_COUNT,
        skill_rounds={
            1: 100204,
            2: 1002041,
            3: 1002042,
            4: 1002043,
            5: 1002044,
        },
        skill_round_labels={
            1: "总件技能帧",
            2: "金均格技能帧",
            3: "紫均格技能帧",
            4: "蓝均格技能帧",
            5: "白绿件技能帧",
        },
        early_round_boundary=2,
        focus_managed=True,
    ),
    "victor": HeroRefSchedule(
        hero_key="victor",
        hero_id=209,
        category=HeroInferenceCategory.EARLY_SINGLE_SKILL,
        skill_rounds={1: 100209},
        skill_round_labels={1: "紫金红件技能帧"},
        early_round_boundary=2,
    ),
    "raven": HeroRefSchedule(
        hero_key="raven",
        hero_id=301,
        category=HeroInferenceCategory.LATE_SINGLE_SKILL,
        skill_rounds={5: 100301},
        skill_round_labels={5: "全品质技能帧"},
        early_round_boundary=5,
        focus_managed=True,
    ),
    "ethan": HeroRefSchedule(
        hero_key="ethan",
        hero_id=208,
        category=HeroInferenceCategory.EARLY_LATE_SKILL,
        skill_rounds={1: 1002081, 5: 1002085},
        skill_round_labels={1: "五类轮廓技能帧", 5: "全仓轮廓技能帧"},
        early_round_boundary=3,
    ),
    "fatima": HeroRefSchedule(
        hero_key="fatima",
        hero_id=101,
        category=HeroInferenceCategory.R1_BURST,
        skill_rounds={1: 1001011},
        skill_round_labels={1: "R1品质技能帧"},
    ),
    "gabriela": HeroRefSchedule(
        hero_key="gabriela",
        hero_id=104,
        category=HeroInferenceCategory.R1_BURST,
        skill_rounds={1: 1001041},
        skill_round_labels={1: "R1起跨轮品质(2件/轮)"},
        focus_managed=True,
    ),
    "sophie": HeroRefSchedule(
        hero_key="sophie",
        hero_id=107,
        category=HeroInferenceCategory.R1_BURST,
        skill_rounds={1: 1001071, 2: 1001072},
        skill_round_labels={1: "R1五件品质", 2: "R2起跨轮品质(2件/轮)"},
        focus_managed=True,
    ),
    "helena": HeroRefSchedule(
        hero_key="helena",
        hero_id=109,
        category=HeroInferenceCategory.STAGED_ROUNDS,
        skill_rounds={
            1: 100109,
            2: 1001091,
            3: 1001092,
            4: 1001093,
            5: 1001094,
        },
        skill_round_labels={
            1: "R1医疗品质",
            2: "R2医疗轮廓",
            3: "R3医疗轮廓/完整信息",
            4: "R4医疗轮廓/完整信息",
            5: "R5医疗轮廓/完整信息",
        },
        scheduled_skill_sparse_fail_open=True,
    ),
    "carlos": HeroRefSchedule(
        hero_key="carlos",
        hero_id=202,
        category=HeroInferenceCategory.STAGED_ROUNDS,
        skill_rounds={
            1: 100202,
            2: 1002021,
            3: 1002022,
            4: 1002023,
            5: 1002024,
        },
        skill_round_labels={
            1: "R1家具+数码轮廓",
            2: "R2家具+数码品质",
            3: "R3品质/家具完整信息",
            4: "R4品质/家具完整信息",
            5: "R5品质/家具完整信息",
        },
        scheduled_skill_sparse_fail_open=True,
    ),
    "takeda": HeroRefSchedule(
        hero_key="takeda",
        hero_id=206,
        category=HeroInferenceCategory.STAGED_ROUNDS,
        skill_rounds={
            1: 100206,
            2: 1002062,
            3: 10020631,
            4: 1002064,
            5: 1002065,
        },
        skill_round_labels={
            1: "R1书画古籍轮廓",
            2: "R2书画古籍品质",
            3: "R3书画古籍完整信息",
            4: "R4书画古籍递进",
            5: "R5书画古籍递进",
        },
        scheduled_skill_sparse_fail_open=True,
    ),
    "wuqilin": HeroRefSchedule(
        hero_key="wuqilin",
        hero_id=207,
        category=HeroInferenceCategory.STAGED_ROUNDS,
        skill_rounds={1: 1002071, 2: 1002072, 3: 1002073, 4: 1002074},
        skill_round_labels={
            1: "R1古董件数",
            2: "R2古董轮廓",
            3: "R3古董品质",
            4: "R4古董1/3完整信息",
        },
        focus_managed=True,
    ),
    "chenmei": HeroRefSchedule(
        hero_key="chenmei",
        hero_id=102,
        category=HeroInferenceCategory.STAGED_ROUNDS,
        skill_rounds={1: 100102, 3: 1001021, 4: 1001022, 5: 1001023},
        skill_round_labels={
            1: "R1珠宝+时尚轮廓",
            3: "R3珠宝完整信息",
            4: "R4珠宝完整信息",
            5: "R5珠宝完整信息",
        },
        scheduled_skill_sparse_fail_open=True,
    ),
    "tatiana": HeroRefSchedule(
        hero_key="tatiana",
        hero_id=105,
        category=HeroInferenceCategory.STAGED_ROUNDS,
        skill_rounds={1: 100105, 3: 1001051, 4: 1001052, 5: 1001053},
        skill_round_labels={
            1: "R1时尚品质+轮廓",
            3: "R3时尚完整信息",
            4: "R4时尚完整信息",
            5: "R5时尚完整信息",
        },
        scheduled_skill_sparse_fail_open=True,
    ),
    "naomi": HeroRefSchedule(
        hero_key="naomi",
        hero_id=106,
        category=HeroInferenceCategory.STAGED_ROUNDS,
        skill_rounds={1: 100106, 3: 1001062, 4: 1001063, 5: 1001064},
        skill_round_labels={
            1: "R1时尚+数码轮廓/类内金红件数",
            3: "R3数码完整信息",
            4: "R4数码完整信息",
            5: "R5数码完整信息",
        },
        scheduled_skill_sparse_fail_open=True,
    ),
    "maria": HeroRefSchedule(
        hero_key="maria",
        hero_id=108,
        category=HeroInferenceCategory.EARLY_SINGLE_SKILL,
        skill_rounds={1: 1001081},
        skill_round_labels={1: "R1白绿蓝价值/品质"},
        early_round_boundary=2,
    ),
    "isabella": HeroRefSchedule(
        hero_key="isabella",
        hero_id=110,
        category=HeroInferenceCategory.R1_STATIC,
        skill_rounds={1: 1001101},
        skill_round_labels={1: "R1轮廓"},
    ),
    "george": HeroRefSchedule(
        hero_key="george",
        hero_id=201,
        category=HeroInferenceCategory.STAGED_ROUNDS,
        skill_rounds={
            1: 100201,
            2: 1002011,
            3: 1002012,
            4: 1002013,
            5: 1002014,
        },
        skill_round_labels={
            1: "R1武器品质+轮廓",
            2: "R2武器完整信息",
            3: "R3武器完整信息",
            4: "R4武器完整信息",
            5: "R5武器完整信息",
        },
        scheduled_skill_sparse_fail_open=True,
    ),
    "leonard": HeroRefSchedule(
        hero_key="leonard",
        hero_id=203,
        category=HeroInferenceCategory.STAGED_ROUNDS,
        skill_rounds={1: 100203, 3: 1002032, 4: 1002033, 5: 1002034},
        skill_round_labels={
            1: "R1食品+古董品质",
            3: "R3食品完整信息",
            4: "R4食品完整信息",
            5: "R5食品完整信息",
        },
        scheduled_skill_sparse_fail_open=True,
    ),
    "ivan": HeroRefSchedule(
        hero_key="ivan",
        hero_id=205,
        category=HeroInferenceCategory.STAGED_ROUNDS,
        skill_rounds={1: 100205, 2: 1002051, 3: 1002052},
        skill_round_labels={
            1: "R1武器+能源轮廓",
            2: "R2最大占格藏品品质",
            3: "R3最大占格藏品品质",
        },
        scheduled_skill_sparse_fail_open=True,
    ),
    "indiana": HeroRefSchedule(
        hero_key="indiana",
        hero_id=303,
        category=HeroInferenceCategory.MULTI_ROUND_SKILL,
        skill_rounds={1: 1003031, 3: 1003033, 4: 1003034, 5: 1003035},
        skill_round_labels={
            1: "R1未知箱轮廓技能帧",
            3: "R3随机品质揭示技能帧",
            4: "R4随机品质揭示技能帧",
            5: "R5随机品质揭示技能帧",
        },
        early_round_boundary=2,
        focus_managed=True,
    ),
}


_LEGACY_POSTUPDATE_SCHEDULE_OVERRIDES: dict[str, HeroRefSchedule] = {
    "helena": HeroRefSchedule(
        hero_key="helena",
        hero_id=109,
        category=HeroInferenceCategory.R1_BURST,
        skill_rounds={1: 1001091},
        skill_round_labels={1: "R1医疗品质/轮廓"},
    ),
    "carlos": HeroRefSchedule(
        hero_key="carlos",
        hero_id=202,
        category=HeroInferenceCategory.R1_BURST,
        skill_rounds={1: 1002021, 2: 1002022},
        skill_round_labels={1: "R1轮廓", 2: "R2跨轮品质"},
        scheduled_skill_sparse_fail_open=True,
    ),
    "takeda": HeroRefSchedule(
        hero_key="takeda",
        hero_id=206,
        category=HeroInferenceCategory.R1_BURST,
        skill_rounds={1: 1002061, 2: 1002062},
        skill_round_labels={1: "R1书籍轮廓", 2: "R2跨轮品质"},
        scheduled_skill_sparse_fail_open=True,
    ),
    "chenmei": HeroRefSchedule(
        hero_key="chenmei",
        hero_id=102,
        category=HeroInferenceCategory.EARLY_SINGLE_SKILL,
        skill_rounds={1: 1001021},
        skill_round_labels={1: "R1珠宝时尚轮廓"},
        early_round_boundary=2,
    ),
    "tatiana": HeroRefSchedule(
        hero_key="tatiana",
        hero_id=105,
        category=HeroInferenceCategory.EARLY_SINGLE_SKILL,
        skill_rounds={1: 1001051},
        skill_round_labels={1: "R1时尚品质/轮廓"},
        early_round_boundary=2,
    ),
    "naomi": HeroRefSchedule(
        hero_key="naomi",
        hero_id=106,
        category=HeroInferenceCategory.R1_STATIC,
        skill_rounds={1: 1001061},
        skill_round_labels={1: "R1轮廓+红金件数"},
    ),
    "george": HeroRefSchedule(
        hero_key="george",
        hero_id=201,
        category=HeroInferenceCategory.EARLY_SINGLE_SKILL,
        skill_rounds={1: 1002011},
        skill_round_labels={1: "R1武器品质/轮廓"},
        early_round_boundary=2,
    ),
    "leonard": HeroRefSchedule(
        hero_key="leonard",
        hero_id=203,
        category=HeroInferenceCategory.R1_STATIC,
        skill_rounds={1: 1002031},
        skill_round_labels={1: "R1食品/古董品质"},
    ),
    "ivan": HeroRefSchedule(
        hero_key="ivan",
        hero_id=205,
        category=HeroInferenceCategory.EARLY_SINGLE_SKILL,
        skill_rounds={1: 1002051},
        skill_round_labels={1: "R1武器能源轮廓"},
        early_round_boundary=2,
    ),
}


def get_hero_schedule(hero_key: str) -> HeroRefSchedule | None:
    key = _text(hero_key).lower()
    if os.environ.get(POSTUPDATE_NINE_HERO_SKILL_COMPAT_DISABLE) == "1":
        legacy = _LEGACY_POSTUPDATE_SCHEDULE_OVERRIDES.get(key)
        if legacy is not None:
            return legacy
    return HERO_REF_SCHEDULES.get(key)


def hero_inference_category(hero_key: str) -> HeroInferenceCategory | None:
    spec = get_hero_schedule(hero_key)
    return spec.category if spec else None


def hero_category_label_zh(hero_key: str) -> str:
    spec = get_hero_schedule(hero_key)
    if spec is None:
        return CATEGORY_LABELS_ZH[HeroInferenceCategory.SPARSE_PUBLIC_PROP]
    return CATEGORY_LABELS_ZH[spec.category]


def hero_should_run_scheduled_inference(
    hero_key: str,
    round_no: int | None,
    phase: str,
) -> bool:
    if phase in ("settled", "manual"):
        return False
    if round_no is None or not (1 <= int(round_no) <= 5):
        return False
    return get_hero_schedule(hero_key) is not None


def hero_uses_dual_pass(hero_key: str, round_no: int | None, phase: str) -> bool:
    spec = get_hero_schedule(hero_key)
    if spec is None or spec.dual_pass_max_round is None:
        return False
    if phase in ("settled", "manual"):
        return False
    if round_no is None:
        return False
    return 1 <= int(round_no) <= int(spec.dual_pass_max_round)


def hero_max_combos_for_round(hero_key: str, round_no: int | None) -> int:
    spec = get_hero_schedule(hero_key)
    r = int(round_no) if round_no is not None else 1
    if spec is None:
        return REF_MID_MAX_COMBOS

    cat = spec.category
    if cat is HeroInferenceCategory.MULTI_ROUND_SKILL:
        if r < 3:
            return REF_EARLY_MAX_COMBOS
        if r < 5:
            return REF_MID_MAX_COMBOS
        return REF_LATE_MAX_COMBOS

    if cat is HeroInferenceCategory.PROGRESSIVE_COUNT:
        if r == 1:
            return REF_EARLY_MAX_COMBOS
        if r <= 3:
            return REF_MID_MAX_COMBOS
        return REF_LATE_MAX_COMBOS

    if cat is HeroInferenceCategory.EARLY_SINGLE_SKILL:
        return REF_EARLY_MAX_COMBOS if r == 1 else REF_MID_MAX_COMBOS

    if cat is HeroInferenceCategory.LATE_SINGLE_SKILL:
        return REF_SPARSE_MAX_COMBOS if r < 5 else REF_LATE_MAX_COMBOS

    if cat is HeroInferenceCategory.EARLY_LATE_SKILL:
        if r == 1:
            return REF_EARLY_MAX_COMBOS
        if r < 5:
            return REF_SPARSE_MAX_COMBOS
        return REF_LATE_MAX_COMBOS

    if cat in {
        HeroInferenceCategory.R1_BURST,
        HeroInferenceCategory.R1_STATIC,
        HeroInferenceCategory.STAGED_ROUNDS,
    }:
        if r == 1:
            return REF_EARLY_MAX_COMBOS
        if r <= 3:
            return REF_MID_MAX_COMBOS
        return REF_LATE_MAX_COMBOS

    return REF_SPARSE_MAX_COMBOS


def _quote_ready_multi_round(
    snapshot: dict[str, Any],
    spec: HeroRefSchedule,
    round_no: int,
) -> tuple[bool, str]:
    skill_id = spec.skill_rounds.get(round_no)
    if skill_id is None:
        return True, ""
    if _round_skill_frame_ready(snapshot, hero_id=spec.hero_id, skill_id=skill_id):
        return True, ""
    label = spec.skill_round_labels.get(round_no, "技能帧")
    return False, f"等待{label}"


def _quote_ready_progressive_count(
    snapshot: dict[str, Any],
    spec: HeroRefSchedule,
    round_no: int,
) -> tuple[bool, str]:
    skill_id = spec.skill_rounds.get(round_no)
    if skill_id is not None and _round_skill_frame_ready(
        snapshot, hero_id=spec.hero_id, skill_id=skill_id
    ):
        return True, ""
    if round_no == 1 and (
        _snapshot_has_alt_total_signal(snapshot, spec.alt_total_action_ids)
        or _snapshot_has_public_info(snapshot)
        or _snapshot_has_manual_structured_inputs(snapshot)
    ):
        return True, ""
    if skill_id is not None and _round_public_or_prop_ready(
        snapshot,
        round_no=round_no,
        hero_id=spec.hero_id,
        skill_id=skill_id,
    ):
        return True, ""
    if round_no > 1 and _sparse_early_ready(snapshot):
        return True, ""
    label = spec.skill_round_labels.get(round_no, "技能帧")
    return False, f"等待{label}或公开/道具"


def _quote_ready_early_single(
    snapshot: dict[str, Any],
    spec: HeroRefSchedule,
    round_no: int,
) -> tuple[bool, str]:
    if round_no == 1:
        skill_id = spec.skill_rounds.get(1)
        if skill_id and _round_skill_frame_ready(snapshot, hero_id=spec.hero_id, skill_id=skill_id):
            return True, ""
        if _snapshot_has_manual_structured_inputs(snapshot) or _snapshot_has_public_info(snapshot):
            return True, ""
        return False, spec.skill_round_labels.get(1, "等待首轮技能帧")
    if _sparse_early_ready(snapshot):
        return True, ""
    return False, "等待公开/道具/手填"


def _quote_ready_late_single(
    snapshot: dict[str, Any],
    spec: HeroRefSchedule,
    round_no: int,
) -> tuple[bool, str]:
    if round_no >= 5:
        skill_id = spec.skill_rounds.get(5)
        if skill_id and _round_skill_frame_ready(snapshot, hero_id=spec.hero_id, skill_id=skill_id):
            return True, ""
        if _sparse_early_ready(snapshot):
            return True, ""
        return False, spec.skill_round_labels.get(5, "等待R5技能帧")
    if _sparse_early_ready(snapshot):
        return True, ""
    return False, "等待公开/道具/布局"


def _quote_ready_early_late(
    snapshot: dict[str, Any],
    spec: HeroRefSchedule,
    round_no: int,
) -> tuple[bool, str]:
    skill_id = spec.skill_rounds.get(round_no)
    if skill_id and _round_skill_frame_ready(snapshot, hero_id=spec.hero_id, skill_id=skill_id):
        return True, ""
    if round_no in spec.skill_rounds:
        return False, spec.skill_round_labels.get(round_no, "等待技能帧")
    if _sparse_early_ready(snapshot):
        return True, ""
    return False, "等待公开/道具/布局"


def _quote_ready_round_skill_or_sparse(
    snapshot: dict[str, Any],
    spec: HeroRefSchedule,
    round_no: int,
) -> tuple[bool, str]:
    skill_id = spec.skill_rounds.get(round_no)
    if skill_id and _round_skill_frame_ready(snapshot, hero_id=spec.hero_id, skill_id=skill_id):
        return True, ""
    if round_no in spec.skill_rounds:
        if (
            _sparse_early_ready(snapshot)
            and (
                round_no == 1
                or (
                    spec.scheduled_skill_sparse_fail_open
                    and os.environ.get(SCHEDULED_SKILL_SPARSE_FAIL_OPEN_DISABLE) != "1"
                )
            )
        ):
            return True, ""
        label = spec.skill_round_labels.get(round_no, "技能帧")
        return False, f"等待{label}"
    if _sparse_early_ready(snapshot):
        return True, ""
    return False, "等待公开/道具/手填"


_QUOTE_READY_HANDLERS: dict[
    HeroInferenceCategory,
    Callable[[dict[str, Any], HeroRefSchedule, int], tuple[bool, str]],
] = {
    HeroInferenceCategory.MULTI_ROUND_SKILL: _quote_ready_multi_round,
    HeroInferenceCategory.PROGRESSIVE_COUNT: _quote_ready_progressive_count,
    HeroInferenceCategory.EARLY_SINGLE_SKILL: _quote_ready_early_single,
    HeroInferenceCategory.LATE_SINGLE_SKILL: _quote_ready_late_single,
    HeroInferenceCategory.EARLY_LATE_SKILL: _quote_ready_early_late,
    HeroInferenceCategory.R1_BURST: _quote_ready_round_skill_or_sparse,
    HeroInferenceCategory.R1_STATIC: _quote_ready_round_skill_or_sparse,
    HeroInferenceCategory.STAGED_ROUNDS: _quote_ready_round_skill_or_sparse,
    HeroInferenceCategory.SPARSE_PUBLIC_PROP: lambda s, _spec, _r: (
        (True, "") if _sparse_early_ready(s) else (False, "等待公开/道具/手填")
    ),
}


def hero_quote_ready(
    snapshot: dict[str, Any],
    hero_key: str,
    round_no: int,
    public_info: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    del public_info
    spec = get_hero_schedule(hero_key)
    if spec is None:
        return (_sparse_early_ready(snapshot), "等待公开/道具/手填")
    if hero_key == "wuqilin" and _wuqilin_5215_current_scope(snapshot):
        # The selected model can price before the next reveal. The engine owns
        # source/session/evidence qualification and distinguishes conflicts.
        return True, ""
    handler = _QUOTE_READY_HANDLERS[spec.category]
    return handler(snapshot, spec, int(round_no))


def _wuqilin_5215_current_scope(snapshot: dict[str, Any]) -> bool:
    if type(snapshot) is not dict:
        return False
    ui = snapshot.get("ui_contract", {})
    if type(ui) is not dict:
        return False
    context = ui.get("context", {})
    if type(context) is not dict:
        return False
    owners = (snapshot, context)
    maps = [owner["map_id"] for owner in owners if "map_id" in owner]
    sessions = [owner["session_id"] for owner in owners if "session_id" in owner]
    if not maps or any(type(value) not in (int, str) or value not in (5215, "5215") for value in maps):
        return False
    if not sessions or any(type(value) is not str or not value or value != sessions[0] for value in sessions):
        return False
    sources = [owner["warehouse_source"] for owner in owners if "warehouse_source" in owner]
    generation = "b049c3-2026-09-10-597"
    return bool(sources) and all(
        type(source) is dict
        and all(source.get(key) == generation for key in ("source_generation", "map_profile_generation", "game_version"))
        and source.get("map_id") == 5215
        and source.get("source_conflict") is False
        and source.get("quarantine_required") is False
        for source in sources
    )


def hero_ref_waiting_result(
    *,
    hero_key: str,
    wait_hint: str,
    category: HeroInferenceCategory | None = None,
) -> dict[str, Any]:
    cat = category or hero_inference_category(hero_key)
    cat_value = cat.value if cat else HeroInferenceCategory.SPARSE_PUBLIC_PROP.value
    return {
        "status": "missing_total_count",
        "source": "ref_v0",
        "conservative": None,
        "balanced": None,
        "aggressive": None,
        "value_p25": None,
        "value_p50": None,
        "value_p75": None,
        "combo_count": 0,
        "red_count_range": [None, None, None],
        "red_cells_range": [None, None, None],
        "red_value_range": [None, None, None],
        "quality_count_ranges": {},
        "quality_cells_ranges": {},
        "total_grid_range": [None, None, None],
        "notes": [
            "hero_ref_waiting",
            f"hero_ref_wait:{wait_hint}",
            f"hero_ref_category:{cat_value}",
        ],
        "evidence": {"hero": hero_key},
    }


def hero_ref_wait_hint(ref_notes: list[str]) -> str:
    for note in ref_notes:
        text = str(note)
        if text.startswith("hero_ref_wait:"):
            return text.split(":", 1)[1]
        if text.startswith("aisha_early_wait:"):
            return text.split(":", 1)[1]
    return ""


def hero_classification_table() -> list[dict[str, Any]]:
    """Exportable schedule summary for tuning."""
    rows: list[dict[str, Any]] = []
    for key in sorted(HERO_REF_SCHEDULES):
        spec = get_hero_schedule(key)
        if spec is None:
            continue
        rows.append(
            {
                "hero_key": spec.hero_key,
                "hero_id": spec.hero_id,
                "category": spec.category.value,
                "category_label_zh": CATEGORY_LABELS_ZH[spec.category],
                "skill_rounds": dict(spec.skill_rounds),
                "skill_round_labels": dict(spec.skill_round_labels),
                "dual_pass": spec.dual_pass_max_round is not None,
                "focus_managed": spec.focus_managed,
                "max_combos_r1": hero_max_combos_for_round(key, 1),
                "max_combos_r3": hero_max_combos_for_round(key, 3),
                "max_combos_r5": hero_max_combos_for_round(key, 5),
            }
        )
    return rows
