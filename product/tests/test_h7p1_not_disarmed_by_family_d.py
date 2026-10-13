"""族D 不得缴械 H7p1 —— 而且 notes 里的声称必须与实际输出一致.

H7p1 (red_cells_tail_hedge_hi) 的闸门【意图】是 "总格是不是一个已知事实":
    · 是 (手填 / live / 公开面板 / 总格工具) -> 不 hedge, 因为那是硬事实, 撑宽它会侵蚀确定性
    · 否 (总格是 count×先验 推出来的)       -> hedge, 给看不见的大红留出尾部空间
但它【实际问的】是 `evidence.total_grid_target is None` —— 一个"字段空不空"的问题.

族D 的对账 pass 用 replace(evidence, total_grid_target=<引擎自己估的总格>) 重入引擎.
那是【引擎自己的猜测】, 不是任何读数. 裸的 is-None 测试分不出来, 于是:
    pass1: hedge 触发, 往 notes 里写 "red_cells_tail_hedge_hi:10->24"
    pass2: 族D 注入软目标 -> hedge 被缴械 -> 红格 hi 停在 9
    族D 用 pass2 的结果【覆盖】red_cells_range, 而 pass1 的注记【留在 notes 里】
    => 面板/下游读到一条描述着"不存在的输出"的注记.  实测: 32/32 局全部矛盾.

代价是【真实的】且被聚合数掩盖了: ahmed 1418 局里 9 局的红格从"罩住真值"变成"切掉真值",
而且切掉的全是【大红】(真值 14/15/17 格; hi 24->9 · 32->11 · 32->12). 聚合覆盖数只从 802 动到 801,
一个"净 −1"把"拿大红换小红"整个盖住了.

本测试钉两条不变量:
  1. 结构: H7p1 的闸门必须放行【带族D 注记】的软目标 (否则族D 一开就把它关掉);
  2. 契约: 只要 notes 里有 hedge 的声称, 最终 red_cells_range[2] 就必须【等于】它声称的值.
     注记不许撒谎 —— 一条描述着不存在输出的注记, 比没有注记更坏.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in ("hero_ref/src", "hero_ref/tools", "src", "scripts", "."):
    _full = str(ROOT / _p)
    if _full not in sys.path:
        sys.path.insert(0, _full)

from ahmad_ref_engine import (  # noqa: E402
    GRID_IDENTITY_RECONCILE_NOTE_PREFIX,
    RED_CELLS_PER_ITEM_P90_BY_FAMILY,
    RED_CELLS_PER_ITEM_P90_DEFAULT,
    extract_evidence,
    run_reference_engine,
)


def _hedge_claim(notes) -> int | None:
    """the hi that a red_cells_tail_hedge_hi note CLAIMS it raised the band to."""
    import re

    for note in notes or ():
        text = str(note)
        if not text.startswith("red_cells_tail_hedge_hi:"):
            continue
        match = re.search(r"->(\d+)", text)
        if match:
            return int(match.group(1))
    return None


@pytest.mark.slow
def test_hedge_note_never_lies_about_the_final_red_cells_hi() -> None:
    """The contract: a note that says it raised the hi to N must be describing the band that shipped.

    This is the invariant the bug broke. It is checked against the real pool rather than a synthetic
    snapshot because the failure only appears when 族D's reconcile pass actually re-enters the engine —
    a hand-built evidence object would not exercise the re-entry that caused it.
    """
    try:
        from audit_aisha_gap import (
            _build_snapshot,
            _events_through_sort,
            _map_id_from_events,
            _prefix_sort_id,
            _settlement_breakdown,
        )
        from bidking_lab.live.fatbeans import (
            live_batches_from_fatbeans_events,
            parse_fatbeans_capture,
        )
        from build_clean_eval_set import clean_eval_files
        from hero_ref_live_schedule import hero_max_combos_for_round
    except Exception:  # pragma: no cover - offline pool absent
        pytest.skip("clean eval pool not available")

    files = list(clean_eval_files("ahmed"))[:220]
    if not files:
        pytest.skip("clean eval pool empty")

    checked = 0
    liars: list[str] = []
    for path in files:
        try:
            events = parse_fatbeans_capture(Path(path))
            if not _settlement_breakdown(events):
                continue
            pre = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
            prefix = None
            for cut in range(len(pre), 0, -1):
                if not all(b.phase == "reading" for b in pre[:cut]):
                    prefix = pre[:cut]
                    break
            if not prefix:
                continue
            sort_id = _prefix_sort_id(prefix)
            sliced = _events_through_sort(events, sort_id) if sort_id else events
            snapshot = _build_snapshot(
                hero="ahmed",
                events=sliced,
                prefix_batches=prefix,
                map_id=_map_id_from_events(events),
            )
            rnd = int(((snapshot.get("ui_contract") or {}).get("context") or {}).get("round") or 5)
            result = run_reference_engine(
                snapshot, max_combos=min(20000, hero_max_combos_for_round("ahmed", min(rnd, 5)))
            ).as_dict()
        except Exception:
            continue

        claim = _hedge_claim(result.get("notes"))
        if claim is None:
            continue
        checked += 1
        hi = (result.get("red_cells_range") or [None, None, None])[2]
        if hi is not None and int(hi) != claim:
            liars.append(
                f"map={result.get('map_id')} note claims hi={claim} but shipped "
                f"red_cells_range={result.get('red_cells_range')}"
            )

    if not checked:
        pytest.skip("no hedged game in the sampled slice")
    assert not liars, (
        f"{len(liars)}/{checked} hedged games ship a red_cells_range that contradicts their own "
        f"hedge note (族D disarmed H7p1 and then overwrote the band):\n  " + "\n  ".join(liars[:6])
    )


def test_family_d_soft_target_does_not_disarm_the_hedge() -> None:
    """Structural: the gate must let a 族D-reconciled (INFERRED) total through.

    族D only fires when total_grid_target was None, so its note is a sufficient marker that the target
    is the engine's own guess. If the gate ever goes back to a bare `total_grid_target is None`, 族D's
    pass 2 silently turns the hedge off again — and the aggregate coverage count will NOT tell you
    (it moved 802 -> 801 while 9 big-red games lost the truth).
    """
    import inspect

    import ahmad_ref_engine as engine

    target = getattr(engine, "_run_reference_engine_impl", engine.run_reference_engine)
    source = inspect.getsource(target)
    start = source.find("BIDKING_DISABLE_RED_TAIL_DISPLAY_HEDGE")
    assert start > 0, "H7p1 gate not found — did the hedge get renamed?"
    # the gate spans from the kill-env check down to the rc90 bound; take a generous window
    gate = source[start : start + 2600]
    # the source names the CONSTANT, not its value — assert on what is actually written there
    assert "GRID_IDENTITY_RECONCILE_NOTE_PREFIX" in gate, (
        "H7p1's gate no longer recognises 族D's inferred total. A bare `total_grid_target is None` "
        "test cannot tell a game/player READING from the engine's OWN guess, and 族D injects the latter."
    )
    assert GRID_IDENTITY_RECONCILE_NOTE_PREFIX == "grid_identity_reconciled:"


def test_family_p90_table_is_the_hedge_ceiling() -> None:
    """Guard the constant the hedge hedges TO (a silent table edit would move every red hi)."""
    assert RED_CELLS_PER_ITEM_P90_BY_FAMILY["villa"] == 8.0
    assert RED_CELLS_PER_ITEM_P90_BY_FAMILY["shipwreck"] == 7.75
    assert RED_CELLS_PER_ITEM_P90_DEFAULT > 0
