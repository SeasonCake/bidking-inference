"""Current-state value of information for one more tool.

This is different from :mod:`bidking_lab.inference.roi`: the older ROI module
compares complete tool kits over fresh simulated sessions. This module starts
from the current partial observation, samples compatible truths, then asks:
"if we used this tool now, how much would the posterior width shrink?"
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np

from bidking_lab.extract.bid_map_table import BidMap
from bidking_lab.extract.drop_table import DropPool
from bidking_lab.extract.item_table import Item
from bidking_lab.inference.ground_truth import SessionTruth, prepare_session_sampler
from bidking_lab.inference.map_likelihood import truth_matches_obs
from bidking_lab.inference.observation import SessionObs, tool_price
from bidking_lab.inference.synth_readings import (
    SESSION_TOOL_SPECS,
    TOOL_SPECS,
    apply_tool,
)

DEFAULT_INFO_ROI_TOOLS: tuple[str, ...] = (
    "宝光四鉴",
    "随机抽检（2）",
    "四象窥视",
    "十方窥视",
    "普品扫描",
    "良品扫描",
    "优品扫描",
    "极品扫描",
    "珍品扫描",
    "优品均格",
    "优品估价",
    "极品估价",
    "总仓储空间",
    "全库透视",
    "明镜之眼",
)

_CUSTOM_TOOL_PRICE: dict[str, int] = {
    "四象窥视": 2_500,
    "十方窥视": 20_000,
    "随机抽检（2）": 2_500,
    "宝光四鉴": 2_500,
    "全库透视": 50_000,
    "明镜之眼": 50_000,
}


@dataclass(frozen=True)
class ToolInfoROI:
    """Expected interval compression from using one tool now."""

    tool_name: str
    silver_cost: int
    n_matched: int
    base_value_width: float
    expected_value_width: float
    value_width_gain: float
    base_cells_width: float
    expected_cells_width: float
    cells_width_gain: float
    roi_value: float
    note: str


def _width(values: Sequence[int]) -> float:
    if not values:
        return 0.0
    p10, p90 = np.percentile(np.asarray(values, dtype=np.float64), [10, 90])
    return float(p90 - p10)


def _expected_width(groups: Mapping[tuple[Any, ...], list[SessionTruth]], attr: str) -> float:
    total = sum(len(group) for group in groups.values())
    if total <= 0:
        return 0.0
    acc = 0.0
    for group in groups.values():
        if attr == "value":
            values = [truth.total_value() for truth in group]
        elif attr == "cells":
            values = [truth.warehouse_total_cells for truth in group]
        else:
            raise ValueError(f"unknown attr {attr!r}")
        acc += (len(group) / total) * _width(values)
    return acc


def _tool_cost(tool_name: str) -> int:
    if tool_name in TOOL_SPECS:
        spec = TOOL_SPECS[tool_name]
        return tool_price(tool_name, spec.rarity)
    if tool_name in SESSION_TOOL_SPECS:
        spec = SESSION_TOOL_SPECS[tool_name]
        return tool_price(tool_name, spec.rarity)
    return _CUSTOM_TOOL_PRICE[tool_name]


def _bucket_patch_signal(tool_name: str, truth: SessionTruth) -> tuple[Any, ...]:
    effect = apply_tool(truth, tool_name)
    pieces: list[Any] = []
    for key, value in sorted(effect.session_patch.items()):
        pieces.append(("session", key, value))
    for quality, patch in sorted(effect.bucket_patches.items()):
        for key, value in sorted(patch.items()):
            if hasattr(value, "raw"):
                value = getattr(value, "raw")
            pieces.append(("bucket", quality, key, value))
    return tuple(pieces)


def _sample_items_signal(
    truth: SessionTruth,
    *,
    n: int,
    rng: np.random.Generator,
    fields: tuple[str, ...],
) -> tuple[Any, ...]:
    items = [
        item
        for bucket in truth.buckets.values()
        for item in bucket.items
    ]
    if not items:
        return ()
    sample_n = min(n, len(items))
    idx = rng.choice(len(items), size=sample_n, replace=False)
    pieces: list[tuple[Any, ...]] = []
    for i in idx:
        item = items[int(i)]
        cells = item.shape_w * item.shape_h
        row: list[Any] = []
        for field in fields:
            if field == "item_id":
                row.append(item.item_id)
            elif field == "quality":
                row.append(item.quality)
            elif field == "cells":
                row.append(cells)
            elif field == "value":
                row.append(item.value)
            else:
                raise ValueError(f"unknown item signal field {field!r}")
        pieces.append(tuple(row))
    return tuple(sorted(pieces))


def _tool_signal(
    tool_name: str,
    truth: SessionTruth,
    *,
    rng: np.random.Generator,
) -> tuple[Any, ...]:
    if tool_name in TOOL_SPECS or tool_name in SESSION_TOOL_SPECS:
        return _bucket_patch_signal(tool_name, truth)
    if tool_name == "随机抽检（2）":
        return _sample_items_signal(
            truth,
            n=2,
            rng=rng,
            fields=("item_id", "quality", "cells", "value"),
        )
    if tool_name == "宝光四鉴":
        return _sample_items_signal(
            truth,
            n=4,
            rng=rng,
            fields=("quality",),
        )
    if tool_name == "四象窥视":
        return _sample_items_signal(
            truth,
            n=4,
            rng=rng,
            fields=("cells",),
        )
    if tool_name == "十方窥视":
        return _sample_items_signal(
            truth,
            n=10,
            rng=rng,
            fields=("cells",),
        )
    if tool_name == "全库透视":
        return tuple(
            sorted(
                item.shape_w * item.shape_h
                for bucket in truth.buckets.values()
                for item in bucket.items
            )
        )
    if tool_name == "明镜之眼":
        return tuple(
            sorted(
                item.quality
                for bucket in truth.buckets.values()
                for item in bucket.items
            )
        )
    raise KeyError(f"unsupported info ROI tool {tool_name!r}")


def _tool_note(tool_name: str) -> str:
    if tool_name == "宝光四鉴":
        return "当前先按品质信号估算；若样本确认还稳定给位置/轮廓，可继续提高它的仓储收益"
    if tool_name == "明镜之眼":
        return "按全库品质信号估算；伊森下一轮可与空间觉知轮廓合并为形状+品质"
    if tool_name == "随机抽检（2）":
        return "按完整 item_id/品质/格数/价值信号估算"
    if tool_name in ("四象窥视", "十方窥视", "全库透视"):
        return "按轮廓格数信号估算，不含屏幕位置堆叠信息"
    return "按现有扫描/估价/均格读数字段估算"


def _matching_truths(
    candidate_map_ids: Sequence[int],
    obs: SessionObs,
    *,
    maps: Mapping[int, BidMap],
    drops: Mapping[int, DropPool],
    items: Mapping[int, Item],
    n_trials: int,
    seed: int,
    cells_tol: int,
    count_tol: int,
    value_rel_tol: float,
) -> list[SessionTruth]:
    rng = np.random.default_rng(seed)
    truths: list[SessionTruth] = []
    for map_id in candidate_map_ids:
        if map_id not in maps:
            continue
        sampler = prepare_session_sampler(map_id, maps=maps, drops=drops, items=items)
        for _ in range(max(0, int(n_trials))):
            truth = sampler.sample(rng=rng)
            if truth_matches_obs(
                truth,
                obs,
                cells_tol=cells_tol,
                count_tol=count_tol,
                value_rel_tol=value_rel_tol,
            ):
                truths.append(truth)
    return truths


def estimate_tool_info_roi(
    candidate_map_ids: Sequence[int],
    obs: SessionObs,
    *,
    maps: Mapping[int, BidMap],
    drops: Mapping[int, DropPool],
    items: Mapping[int, Item],
    tools: Sequence[str] = DEFAULT_INFO_ROI_TOOLS,
    n_trials: int = 1000,
    seed: int = 0,
    cells_tol: int = 8,
    count_tol: int = 3,
    value_rel_tol: float = 0.10,
) -> list[ToolInfoROI]:
    """Estimate expected interval compression for each candidate tool."""

    truths = _matching_truths(
        candidate_map_ids,
        obs,
        maps=maps,
        drops=drops,
        items=items,
        n_trials=n_trials,
        seed=seed,
        cells_tol=cells_tol,
        count_tol=count_tol,
        value_rel_tol=value_rel_tol,
    )
    if not truths:
        return []

    base_value_width = _width([truth.total_value() for truth in truths])
    base_cells_width = _width([truth.warehouse_total_cells for truth in truths])

    out: list[ToolInfoROI] = []
    for i, tool_name in enumerate(tools):
        signal_rng = np.random.default_rng(seed + 10_000 + i)
        groups: dict[tuple[Any, ...], list[SessionTruth]] = defaultdict(list)
        try:
            cost = _tool_cost(tool_name)
            for truth in truths:
                groups[_tool_signal(tool_name, truth, rng=signal_rng)].append(truth)
        except KeyError:
            continue
        expected_value_width = _expected_width(groups, "value")
        expected_cells_width = _expected_width(groups, "cells")
        value_gain = max(0.0, base_value_width - expected_value_width)
        cells_gain = max(0.0, base_cells_width - expected_cells_width)
        out.append(
            ToolInfoROI(
                tool_name=tool_name,
                silver_cost=cost,
                n_matched=len(truths),
                base_value_width=base_value_width,
                expected_value_width=expected_value_width,
                value_width_gain=value_gain,
                base_cells_width=base_cells_width,
                expected_cells_width=expected_cells_width,
                cells_width_gain=cells_gain,
                roi_value=(value_gain / cost) if cost > 0 else 0.0,
                note=_tool_note(tool_name),
            )
        )
    return sorted(out, key=lambda row: (row.roi_value, row.cells_width_gain), reverse=True)


__all__ = (
    "DEFAULT_INFO_ROI_TOOLS",
    "ToolInfoROI",
    "estimate_tool_info_roi",
)
