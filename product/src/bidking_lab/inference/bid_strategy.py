"""Round-aware bid strategy heuristics.

This module converts a value posterior and current player bids into a small
set of explainable price bands. It deliberately avoids opponent prediction for
now; repeated human price patterns need more real match logs before they are
stable enough to model.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from bidking_lab.inference.observation import SessionObs


ROUND_WAREHOUSE_MULTIPLIERS: Mapping[int, float] = {
    1: 2.0,
    2: 1.6,
    3: 1.3,
    4: 1.1,
    5: 1.0,
}
DEFEND_UPSIDE_BLEND = 0.15
DEFEND_MAX_VALUE_PREMIUM = 1.15


@dataclass(frozen=True)
class BidThresholds:
    """Computed bid thresholds for the current information state."""

    probe_bid: int
    defend_bid: int
    attack_bid: int
    stop_bid: int
    warehouse_multiplier: float = 1.0


@dataclass(frozen=True)
class PlayerBidRisk:
    """Risk label for one player's latest known bid."""

    name: str
    bid: int
    risk_band: str


@dataclass(frozen=True)
class BidStrategyReport:
    """One bid recommendation with per-player risk rows."""

    evidence_label: str
    round_label: str
    info_strength: str
    warehouse_status: str
    fair_value: int
    upside_value: int
    leader: str
    highest_bid: int
    thresholds: BidThresholds
    risk_band: str
    action: str
    rationale: str
    next_info_hint: str
    player_risks: tuple[PlayerBidRisk, ...]


def _value_attr(value_summary: Any, name: str) -> float | None:
    value = getattr(value_summary, name, None)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round_label(round_no: int | None, total_rounds: int | None) -> str:
    if round_no is None:
        return "未知轮次"
    if total_rounds and total_rounds > 0:
        return f"R{round_no}/{total_rounds}"
    return f"R{round_no}"


def _estimate_quantile_summary(warehouse_estimate: Any) -> Any | None:
    if warehouse_estimate is None:
        return None
    return getattr(warehouse_estimate, "total_cells", None)


def _estimate_confidence(warehouse_estimate: Any) -> str:
    if warehouse_estimate is None:
        return ""
    return str(getattr(warehouse_estimate, "confidence", "") or "")


def _warehouse_status(session: SessionObs | None, warehouse_estimate: Any = None) -> str:
    if session is not None and session.warehouse_total_cells is not None:
        return f"精确 {session.warehouse_total_cells}"
    if session is not None and session.warehouse_total_cells_approx is not None:
        tol = session.warehouse_total_cells_tolerance or 0
        return f"估计 {session.warehouse_total_cells_approx}±{tol}"
    estimate = _estimate_quantile_summary(warehouse_estimate)
    if estimate is not None:
        confidence = _estimate_confidence(warehouse_estimate)
        label = f"后验 {estimate.p10:.0f}/{estimate.p50:.0f}/{estimate.p90:.0f}"
        if confidence:
            label = f"{label} ({confidence})"
        return label
    return "未知"


def _known_bucket_count(session: SessionObs | None) -> int:
    if session is None:
        return 0
    return sum(
        1 for bucket in session.buckets.values()
        if (
            bucket.avg_cells is not None
            or bucket.total_cells is not None
            or bucket.total_cells_approx is not None
            or bucket.count is not None
            or bucket.value_sum is not None
            or bucket.avg_value is not None
            or bucket.value_range is not None
            or bucket.huge_band != "none"
        )
    )


def _info_strength(
    *,
    session: SessionObs | None,
    round_no: int | None,
    total_rounds: int | None,
    posterior_samples: int,
    warehouse_estimate: Any = None,
) -> str:
    score = 0
    known_buckets = _known_bucket_count(session)
    if known_buckets >= 2:
        score += 1
    if known_buckets >= 4:
        score += 1
    if session is not None and session.warehouse_total_cells is not None:
        score += 2
    elif session is not None and session.warehouse_total_cells_approx is not None:
        score += 1
    elif _estimate_quantile_summary(warehouse_estimate) is not None:
        confidence = _estimate_confidence(warehouse_estimate)
        estimate = _estimate_quantile_summary(warehouse_estimate)
        width = estimate.p90 - estimate.p10
        if confidence == "高" and width <= 25:
            score += 2
        elif confidence in ("中", "高"):
            score += 1
    if session is not None and session.total_item_count is not None:
        score += 1
    if posterior_samples >= 100:
        score += 1
    if posterior_samples >= 500:
        score += 1
    if round_no is not None and total_rounds and total_rounds > 0:
        progress = round_no / total_rounds
        if progress >= 0.5:
            score += 1
        if progress >= 0.75:
            score += 1

    if score <= 2:
        return "低"
    if score <= 5:
        return "中"
    return "高"


def _thresholds_for_strength(
    *,
    p10: float,
    p50: float,
    p90: float,
    info_strength: str,
    round_no: int | None,
) -> BidThresholds:
    multiplier = _warehouse_multiplier(round_no)
    probe_bid = _ceil_price(p10 / multiplier)
    defend_value = _defend_value_with_premium(p50=p50, p90=p90)
    defend_bid = _ceil_price(defend_value / multiplier)
    attack_bid = _ceil_price(p90 / multiplier)
    stop_bid = attack_bid
    if info_strength == "高":
        stop_bid = _ceil_price((p90 / multiplier) * 1.03)
    return BidThresholds(
        probe_bid=probe_bid,
        defend_bid=defend_bid,
        attack_bid=attack_bid,
        stop_bid=stop_bid,
        warehouse_multiplier=multiplier,
    )


def _warehouse_multiplier(round_no: int | None) -> float:
    if round_no is None:
        return 1.0
    return ROUND_WAREHOUSE_MULTIPLIERS.get(round_no, 1.0)


def _ceil_price(value: float) -> int:
    return max(0, int(math.ceil(value)))


def _defend_value_with_premium(*, p50: float, p90: float) -> float:
    """Use a mild P55-like premium so defend bids can actually win ties."""
    if p90 <= p50:
        return max(0.0, p50)
    blended = p50 + (p90 - p50) * DEFEND_UPSIDE_BLEND
    capped = min(blended, p50 * DEFEND_MAX_VALUE_PREMIUM)
    return max(0.0, min(capped, p90))


def _risk_band(bid: int, thresholds: BidThresholds) -> str:
    if bid <= thresholds.probe_bid:
        return "很低价"
    if bid <= thresholds.defend_bid:
        return "防守区"
    if bid <= thresholds.attack_bid:
        return "进攻区"
    if bid <= thresholds.stop_bid:
        return "高风险抢仓"
    return "过热区"


def _action_for_highest(
    *,
    highest_bid: int,
    thresholds: BidThresholds,
    info_strength: str,
    warehouse_status: str,
) -> str:
    if highest_bid > thresholds.stop_bid:
        return "停止追价"
    if info_strength == "低":
        return "保守探价，不主动追高"
    if highest_bid > thresholds.attack_bid:
        return "只在必须抢仓时追"
    if highest_bid > thresholds.defend_bid:
        return "小幅进攻，等新信息确认"
    if warehouse_status == "未知":
        return "可防守，但优先补仓储信息"
    return "仍在可防守区"


def _rationale(
    *,
    info_strength: str,
    warehouse_status: str,
    posterior_samples: int,
    round_no: int | None,
    warehouse_multiplier: float,
) -> str:
    parts: list[str] = []
    if round_no == 1 or info_strength == "低":
        parts.append("早期/低信息阶段不主动高追")
    elif info_strength == "中":
        parts.append("已有部分桶或仓储证据，按当前后验分位给出攻防线")
    else:
        parts.append("信息较完整，抢仓上限可接近 P90 对应价")
    if warehouse_multiplier > 1.0:
        parts.append(
            f"本轮秒仓倍率 {warehouse_multiplier:g}x，出价阈值按估值÷倍率反推；"
            "防守价含轻微成交溢价"
        )
    else:
        parts.append("本轮最高价直接决定归属，防守价含轻微成交溢价")
    if warehouse_status == "未知":
        parts.append("仓储未知会放大总价值不确定性")
    elif warehouse_status.startswith("精确"):
        parts.append("仓储已精确锁定，后续主要不确定性来自品质与价值")
    elif warehouse_status.startswith("估计"):
        parts.append("仓储为估计值，仍需保留容差")
    elif warehouse_status.startswith("后验"):
        parts.append("仓储来自 MC 后验估计，区间宽度会影响追价保守度")
    if posterior_samples and posterior_samples < 100:
        parts.append("有效样本偏少，建议只作方向参考")
    parts.append("暂未建模玩家整数价位习惯")
    return "；".join(parts)


def _next_info_hint(
    *,
    info_strength: str,
    warehouse_status: str,
    round_no: int | None,
) -> str:
    if info_strength == "低":
        if round_no == 1:
            return "优先使用低成本补信息道具：宝光四鉴、抽检二、普品/良品扫描；总仓储/高品质扫描只在高价值局或当前配置已携带时考虑"
        return "优先用低成本道具补轮廓或具体物品；普品/良品扫描适合补基础桶，高品质扫描需看当前价差和局面价值"
    if warehouse_status == "未知":
        return "仓储仍未知：宝光四鉴若抽到底部/大轮廓可间接收紧仓储，抽检二可补具体物品价值"
    if warehouse_status.startswith("后验"):
        return "仓储已有后验区间：若区间仍宽，优先使用轮廓/抽检；扫描类按缺失品质选择，总仓储只在当前配置已携带且收益明确时使用"
    if warehouse_status.startswith("精确"):
        return "仓储已锁定：不再使用总仓储，优先补会改变出价阈值的信息，如抽检二、宝光四鉴、缺失高价值品质扫描；估价只在该品质主导且临近追价阈值时使用"
    return "信息已较完整：道具只在能明显改变抢仓/停止阈值时使用"


def recommend_bid_strategy(
    *,
    latest_bids: Mapping[str, int],
    value_summary: Any,
    evidence_label: str,
    session: SessionObs | None = None,
    round_no: int | None = None,
    total_rounds: int | None = None,
    posterior_samples: int = 0,
    warehouse_estimate: Any = None,
) -> BidStrategyReport | None:
    """Return a round-aware bid recommendation.

    ``value_summary`` must expose ``p10``, ``p50`` and ``p90`` attributes.
    """

    if not latest_bids:
        return None
    p10 = _value_attr(value_summary, "p10")
    p50 = _value_attr(value_summary, "p50")
    p90 = _value_attr(value_summary, "p90")
    if p10 is None or p50 is None or p90 is None:
        return None

    clean_bids: dict[str, int] = {}
    for name, bid in latest_bids.items():
        try:
            clean_bids[str(name)] = int(bid)
        except (TypeError, ValueError):
            continue
    if not clean_bids:
        return None

    strength = _info_strength(
        session=session,
        round_no=round_no,
        total_rounds=total_rounds,
        posterior_samples=posterior_samples,
        warehouse_estimate=warehouse_estimate,
    )
    wh_status = _warehouse_status(session, warehouse_estimate)
    thresholds = _thresholds_for_strength(
        p10=p10,
        p50=p50,
        p90=p90,
        info_strength=strength,
        round_no=round_no,
    )
    leader, highest_bid = max(clean_bids.items(), key=lambda item: item[1])
    player_risks = tuple(
        PlayerBidRisk(name=name, bid=bid, risk_band=_risk_band(bid, thresholds))
        for name, bid in sorted(clean_bids.items(), key=lambda item: item[1], reverse=True)
    )
    return BidStrategyReport(
        evidence_label=evidence_label,
        round_label=_round_label(round_no, total_rounds),
        info_strength=strength,
        warehouse_status=wh_status,
        fair_value=int(p50),
        upside_value=int(p90),
        leader=leader,
        highest_bid=highest_bid,
        thresholds=thresholds,
        risk_band=_risk_band(highest_bid, thresholds),
        action=_action_for_highest(
            highest_bid=highest_bid,
            thresholds=thresholds,
            info_strength=strength,
            warehouse_status=wh_status,
        ),
        rationale=_rationale(
            info_strength=strength,
            warehouse_status=wh_status,
            posterior_samples=posterior_samples,
            round_no=round_no,
            warehouse_multiplier=thresholds.warehouse_multiplier,
        ),
        next_info_hint=_next_info_hint(
            info_strength=strength,
            warehouse_status=wh_status,
            round_no=round_no,
        ),
        player_risks=player_risks,
    )


__all__ = (
    "BidStrategyReport",
    "BidThresholds",
    "DEFEND_MAX_VALUE_PREMIUM",
    "DEFEND_UPSIDE_BLEND",
    "PlayerBidRisk",
    "ROUND_WAREHOUSE_MULTIPLIERS",
    "recommend_bid_strategy",
)
