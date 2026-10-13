"""Streamlit tab for session-level joint candidate filtering.

The joint engine is most useful when the player has partial readings
such as avg-cells / avg-value / value-sum but not exact bucket cells.
It combines the per-bucket candidates under one warehouse capacity
constraint, so locally good but globally over-budget combinations get
demoted.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

import streamlit as st

from bidking_lab.inference.joint import JointHypothesis, joint_top_k_for_session
from bidking_lab.inference.observation import (
    BucketCandidate,
    QualityBucketObs,
    SessionObs,
    candidates_for_bucket,
)
from bidking_lab.inference.quality_priors import PER_CELL_VALUE_DEFAULT

QUALITY_LABEL = {1: "白品", 2: "绿品", 3: "蓝品", 4: "紫品", 5: "金品", 6: "红品"}
_JOINT_CONTEXT_CACHE_KEY = "_joint_context_cache"


def _reading_raw(bucket: QualityBucketObs) -> str:
    if bucket.avg_cells is None:
        return ""
    return str(getattr(bucket.avg_cells, "raw", bucket.avg_cells))


def _bucket_constraints(bucket: QualityBucketObs) -> str:
    parts: list[str] = []
    if bucket.total_cells is not None:
        parts.append(f"格数={bucket.total_cells}")
    if bucket.count is not None:
        parts.append(f"件数={bucket.count}")
    if bucket.avg_cells is not None:
        parts.append(f"均格={_reading_raw(bucket)}")
    if bucket.value_sum is not None and bucket.value_sum > 0:
        parts.append(f"总估价={bucket.value_sum:,}")
    if bucket.avg_value is not None and bucket.avg_value > 0:
        parts.append(f"均价={bucket.avg_value:,.2f}/件")
    if bucket.value_range is not None:
        lo, hi = bucket.value_range
        parts.append(f"估价区间={lo:,}-{hi:,}")
    if bucket.huge_band != "none":
        huge = f"巨物={bucket.huge_band}"
        if bucket.huge_cells_override:
            huge += f"({bucket.huge_cells_override}格)"
        parts.append(huge)
    return "；".join(parts) if parts else "未提供约束"


def _session_fingerprint(session: SessionObs, per_bucket_top: int) -> tuple[Any, ...]:
    bucket_parts: list[tuple[Any, ...]] = []
    for q in sorted(session.buckets):
        b = session.buckets[q]
        bucket_parts.append(
            (
                q,
                _reading_raw(b),
                b.total_cells,
                b.total_cells_approx,
                b.count,
                b.value_sum,
                b.avg_value,
                b.value_range,
                b.huge_band,
                b.huge_cells_override,
            )
        )
    return (
        session.map_id,
        session.hero,
        session.warehouse_total_cells,
        session.warehouse_total_cells_approx,
        session.warehouse_total_cells_tolerance,
        session.total_item_count,
        per_bucket_top,
        tuple(bucket_parts),
    )


def _local_top1_by_quality(session: SessionObs) -> dict[int, BucketCandidate]:
    capacity = session.warehouse_capacity()
    out: dict[int, BucketCandidate] = {}
    for q, bucket in session.buckets.items():
        cands = candidates_for_bucket(bucket, warehouse_capacity=capacity)
        if cands:
            out[q] = cands[0]
    return out


def _joint_context(
    session: SessionObs,
    *,
    per_bucket_top: int,
    k: int,
) -> tuple[list[JointHypothesis], dict[int, BucketCandidate]]:
    hyps = joint_top_k_for_session(
        session,
        k=k,
        per_bucket_top=per_bucket_top,
        warehouse_slack=5,
    )
    return hyps, _local_top1_by_quality(session)


def _joint_context_cached(
    session: SessionObs,
    *,
    per_bucket_top: int,
    k: int,
    cache: MutableMapping[str, Any],
    force_refresh: bool = False,
) -> tuple[list[JointHypothesis], dict[int, BucketCandidate]]:
    """Cache joint DFS results across tab switches for the same readings."""
    fingerprint = _session_fingerprint(session, per_bucket_top)
    key = (fingerprint, k)
    cache_bucket = cache.setdefault(_JOINT_CONTEXT_CACHE_KEY, {})
    if force_refresh or key not in cache_bucket:
        hyps, local_top1 = _joint_context(
            session,
            per_bucket_top=per_bucket_top,
            k=k,
        )
        cache_bucket[key] = (hyps, local_top1)
        if len(cache_bucket) > 8:
            oldest = next(iter(cache_bucket))
            if oldest != key:
                cache_bucket.pop(oldest, None)
    return cache_bucket[key]


def _result_badge(capacity: int, total_cells: int) -> tuple[str, str]:
    gap = capacity - total_cells
    if gap > 0:
        return "未占满", f"剩余 {gap} 格可由未填品质或误差解释"
    if gap == 0:
        return "刚好占满", "已观测 bucket 刚好覆盖仓库"
    return "过仓", f"超出 {-gap} 格，依赖 slack/软罚保留"


def _render_summary(
    *,
    session: SessionObs,
    hyps: list[JointHypothesis],
    local_top1: dict[int, BucketCandidate],
) -> None:
    top = hyps[0]
    capacity = session.warehouse_capacity()
    local_total = sum(c.total_cells for c in local_top1.values())
    gap = capacity - top.total_cells
    local_gap = capacity - local_total

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("联合 top-1 格数", f"{top.total_cells}", f"{gap:+d} vs 仓库")
    c2.metric("独立 top-1 合计", f"{local_total}", f"{local_gap:+d} vs 仓库")
    c3.metric("组合评分", f"{top.composite:.3f}")
    c4.metric("过仓罚分", f"{top.warehouse_penalty:.2f}")

    demoted = []
    for q, joint_cand in top.per_bucket.items():
        local_cand = local_top1.get(q)
        if local_cand and (
            local_cand.total_cells != joint_cand.total_cells
            or local_cand.count != joint_cand.count
        ):
            demoted.append(
                f"{QUALITY_LABEL.get(q, f'q{q}')} {local_cand.total_cells}格/{local_cand.count}件"
                f" → {joint_cand.total_cells}格/{joint_cand.count}件"
            )
    if demoted:
        st.info("联合筛选为了满足仓库总格约束，调整了：" + "；".join(demoted))
    else:
        st.caption("联合 top-1 与各 bucket 独立 top-1 一致；当前读数之间没有明显容量冲突。")


def _render_hypothesis(
    *,
    rank: int,
    hyp: JointHypothesis,
    session: SessionObs,
    local_top1: dict[int, BucketCandidate],
) -> None:
    capacity = session.warehouse_capacity()
    status, status_text = _result_badge(capacity, hyp.total_cells)
    title = (
        f"#{rank}  {status} · {hyp.total_cells}/{capacity} 格 · "
        f"score {hyp.composite:.3f}"
    )
    with st.expander(title, expanded=(rank == 1)):
        st.caption(
            f"{status_text}。bucket评分={hyp.bucket_composite:.3f}，"
            f"仓库罚分={hyp.warehouse_penalty:.2f}。分数越低越符合当前读数。"
        )
        rows: list[dict[str, Any]] = []
        for q in (1, 2, 3, 4, 5, 6):
            bucket = session.buckets.get(q)
            cand = hyp.per_bucket.get(q)
            local = local_top1.get(q)
            if cand is None:
                rows.append(
                    {
                        "品质": QUALITY_LABEL[q],
                        "输入约束": _bucket_constraints(bucket) if bucket else "未观察",
                        "联合结果": "—",
                        "独立top1": "—",
                        "评分拆解": "—",
                        "说明": "该品质未进入本次联合枚举",
                    }
                )
                continue
            avg = cand.total_cells / max(1, cand.count)
            per_cell = PER_CELL_VALUE_DEFAULT.get(q, 0)
            local_text = "—"
            note = "采用独立top1"
            if local is not None:
                local_text = f"{local.total_cells}格/{local.count}件"
                if local.total_cells != cand.total_cells or local.count != cand.count:
                    note = "被仓库/其它bucket约束调整"
            if cand.is_db_matched:
                note += "；命中单件物品库"
            rows.append(
                {
                    "品质": QUALITY_LABEL[q],
                    "输入约束": _bucket_constraints(bucket) if bucket else "未观察",
                    "联合结果": f"{cand.total_cells}格/{cand.count}件 · 均格{avg:.2f}",
                    "独立top1": local_text,
                    "评分拆解": (
                        f"value {cand.value_score:.3f} / "
                        f"cells {cand.cells_score:.3f} / "
                        f"local {cand.composite:.3f}"
                    ),
                    "说明": f"{note}；先验约 {per_cell:,}/格",
                }
            )
        st.table(rows)


def render_joint_reasoning_summary(
    *,
    session: SessionObs,
    per_bucket_top: int,
    expanded: bool = False,
) -> None:
    """Render a compact joint reasoning card for the bidding hint tab."""
    if not session.buckets:
        return

    hyps, local_top1 = _joint_context_cached(
        session,
        per_bucket_top=per_bucket_top,
        k=3,
        cache=st.session_state,
    )
    if not hyps:
        st.warning(
            "🔎 联合筛选没有找到可行组合；当前读数可能互相冲突，"
            "请到「联合筛选」tab 查看每个 bucket 的候选约束。"
        )
        return

    top = hyps[0]
    capacity = session.warehouse_capacity()
    local_total = sum(c.total_cells for c in local_top1.values())
    gap = capacity - top.total_cells
    local_gap = capacity - local_total

    with st.expander("🔎 推理依据：联合筛选摘要", expanded=expanded):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("联合 top-1", f"{top.total_cells} 格", f"{gap:+d} vs 仓库")
        c2.metric("独立 top-1", f"{local_total} 格", f"{local_gap:+d} vs 仓库")
        c3.metric("组合评分", f"{top.composite:.3f}")
        c4.metric("过仓罚分", f"{top.warehouse_penalty:.2f}")

        demoted = []
        for q, joint_cand in top.per_bucket.items():
            local_cand = local_top1.get(q)
            if local_cand and (
                local_cand.total_cells != joint_cand.total_cells
                or local_cand.count != joint_cand.count
            ):
                demoted.append(
                    f"{QUALITY_LABEL.get(q, f'q{q}')} "
                    f"{local_cand.total_cells}格/{local_cand.count}件"
                    f" → {joint_cand.total_cells}格/{joint_cand.count}件"
                )
        if demoted:
            st.info("为满足仓库总格约束，joint 调整了：" + "；".join(demoted))
        else:
            st.caption("joint top-1 与各 bucket 独立 top-1 一致；当前读数之间没有明显容量冲突。")

        rows: list[dict[str, Any]] = []
        for q in sorted(top.per_bucket.keys(), reverse=True):
            cand = top.per_bucket[q]
            bucket = session.buckets.get(q)
            local = local_top1.get(q)
            local_text = "—" if local is None else f"{local.total_cells}格/{local.count}件"
            note = "采用独立top1"
            if local is not None and (
                local.total_cells != cand.total_cells or local.count != cand.count
            ):
                note = "被联合约束修正"
            rows.append(
                {
                    "品质": QUALITY_LABEL.get(q, f"q{q}"),
                    "输入约束": _bucket_constraints(bucket) if bucket else "未观察",
                    "joint结果": f"{cand.total_cells}格/{cand.count}件",
                    "独立top1": local_text,
                    "依据": (
                        f"value {cand.value_score:.3f} / "
                        f"cells {cand.cells_score:.3f} / "
                        f"local {cand.composite:.3f}"
                    ),
                    "说明": note,
                }
            )
        st.table(rows)
        st.caption("完整 top-5 组合、未观察品质和每个 hypothesis 的展开解释见「联合筛选」tab。")


def render_joint_inference_tab(*, session_builder, state, per_bucket_top: int) -> None:
    """Render the joint candidate filtering tab."""
    del state  # kept for the caller contract; session_builder is the source of truth.

    st.subheader("联合筛选 — 仓库组成候选")
    st.caption(
        "把紫/金/红等 bucket 的均格、均价、总价、巨物和仓库总格放进同一个组合搜索里。"
        "这里看的不是 MC 分布，而是“哪些格数×件数组合能同时解释当前读数”。"
    )

    try:
        session = session_builder()
    except ValueError as exc:
        st.info(f"先在侧栏选择地图，并填写仓库总格数。当前无法构建会话：{exc}")
        return
    observed = len(session.buckets)
    if observed == 0:
        st.info("先在「读数输入」里填至少一个品质 bucket。")
        return

    st.info(
        "使用方式：当某些品质没有总格数、只填了均格/均价/估价时，看这里的 top 组合。"
        "如果“独立top1合计”超过仓库，而“联合top1”没有超过，说明联合筛选正在修正局部最优。"
    )

    fingerprint = _session_fingerprint(session, per_bucket_top)
    refresh = st.button("刷新联合筛选", key="run_joint", type="primary")
    if refresh:
        with st.spinner("联合枚举中..."):
            hyps, local_top1 = _joint_context_cached(
                session,
                per_bucket_top=per_bucket_top,
                k=5,
                cache=st.session_state,
                force_refresh=True,
            )
    else:
        cache_bucket = st.session_state.get(_JOINT_CONTEXT_CACHE_KEY, {})
        if (fingerprint, 5) in cache_bucket:
            hyps, local_top1 = _joint_context_cached(
                session,
                per_bucket_top=per_bucket_top,
                k=5,
                cache=st.session_state,
            )
        else:
            with st.spinner("联合枚举中..."):
                hyps, local_top1 = _joint_context_cached(
                    session,
                    per_bucket_top=per_bucket_top,
                    k=5,
                    cache=st.session_state,
                )
    if not hyps:
        st.warning(
            "未产生联合候选。通常是某个 bucket 的均格/件数/估价互相矛盾，"
            "或多个 bucket 的最小格数已经超过仓库容量。先回到读数页检查黄色提示。"
        )
        return

    _render_summary(session=session, hyps=hyps, local_top1=local_top1)
    for rank, hyp in enumerate(hyps, start=1):
        _render_hypothesis(
            rank=rank,
            hyp=hyp,
            session=session,
            local_top1=local_top1,
        )
