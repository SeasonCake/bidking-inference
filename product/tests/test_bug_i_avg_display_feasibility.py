"""BUG-I gate (族A 第一刀): 均格的整数积约束必须贯穿到件数范围 —— 显示四舍五入 ≠ 放弃约束.

两个群友案(0711):
  #4  aisha 5605 R4: 金均格 3.5 -> 件数必须使 avg*count 有整数解;引擎却给 [4,5,5](5 件需 17.5 格,不可能),
      真值 6 件/21 格被排除在外。
  #13 aisha 5606 R3: 紫均格 2.25 -> 只有 4 的倍数可行;引擎却给 [7,9,12],真值 8 件/18 格。

两个根因(各自独立,都要修):
  ① `_prior_count_values` 里的豁免:均格看起来像"整洁显示值"(3.5 / 2.25)时**跳过**与可行集的交集,
     只做并集 -> 算术上不可能的件数被放行。其实 `_avg_grid_options` **本来就处理了显示四舍五入**
     (它接受任何"游戏会显示成该均格"的格数),所以交集是安全的;交集为空时回落,永不清零。
  ② `_derive_soft_public_avg_cell_keys`:某档的均格若**同时**来自道具(action_<id>_<tier>_avg_cells)和
     公开信息,会被标成 soft(公开软先验)-> 硬约束整个被跳过。道具给的均格是硬证据,不能被降级。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "hero_ref" / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "hero_ref" / "src"))

from ahmad_ref_engine import (  # noqa: E402
    _avg_grid_options,
    _derive_soft_public_avg_cell_keys,
    _ref_avg_looks_like_display_reading,
)


def test_display_rounded_avg_still_constrains_counts() -> None:
    # the helper was always right — 3.5 admits only even counts, 2.25 only multiples of 4
    assert _ref_avg_looks_like_display_reading(3.5) and _ref_avg_looks_like_display_reading(2.25)
    assert _avg_grid_options(5, 3.5) == []       # 5 golds would need 17.5 cells
    assert _avg_grid_options(4, 3.5) == [14]
    assert _avg_grid_options(6, 3.5) == [21]     # the 5605 truth
    assert _avg_grid_options(7, 2.25) == []
    assert _avg_grid_options(9, 2.25) == []
    assert _avg_grid_options(8, 2.25) == [18]    # the 5606 truth


def test_tool_avg_is_hard_even_when_public_repeats_it() -> None:
    # public-only -> soft prior (unchanged behaviour)
    assert _derive_soft_public_avg_cell_keys(
        source_notes=["public_q4_avg_cells"], fixed_counts={}, quality_cells={}
    ) == frozenset({"q4"})
    # tool (优品均格 100112) + public repeat -> HARD (the 5606 defect)
    assert _derive_soft_public_avg_cell_keys(
        source_notes=["public_q4_avg_cells", "action_100112_q4_avg_cells"],
        fixed_counts={},
        quality_cells={},
    ) == frozenset()
    # 金色均格 100113 likewise
    assert _derive_soft_public_avg_cell_keys(
        source_notes=["public_q5_avg_cells", "action_100113_q5_avg_cells"],
        fixed_counts={},
        quality_cells={},
    ) == frozenset()
    # a locked count or known cells already bypass the soft flag (unchanged)
    assert _derive_soft_public_avg_cell_keys(
        source_notes=["public_q4_avg_cells"], fixed_counts={"q4": 8}, quality_cells={}
    ) == frozenset()


@pytest.mark.parametrize(
    "fixture_dir,round_file,tier,avg,truth_count,truth_cells,forbidden",
    [
        # #4: gold avg 3.5 -> odd counts impossible; truth 6/21
        ("recordings_inbox_20260711_desktop_diag5605_aisha", "r04_bidding.json", "q5", 3.5, 6, 21, (5, 7)),
        # #13: purple avg 2.25 -> only multiples of 4; truth 8/18
        ("recordings_inbox_20260711_desktop_diag5606b_aisha", "r03_bidding.json", "q4", 2.25, 8, 18, (7, 9)),
    ],
)
def test_live_replay_excludes_impossible_counts(
    fixture_dir, round_file, tier, avg, truth_count, truth_cells, forbidden
) -> None:
    import json

    from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine

    path = ROOT / "data" / "samples" / fixture_dir / round_file
    if not path.exists():
        pytest.skip(f"{fixture_dir} snapshot absent (gitignored) — local-only gate")
    res = run_reference_engine(
        prepare_reference_engine_snapshot(json.loads(path.read_text(encoding="utf-8"))),
        max_combos=8000,
    ).as_dict()
    counts = res["quality_count_ranges"][tier]
    cells = res["quality_cells_ranges"][tier]

    # every displayed count must be arithmetically possible at this avg
    for value in counts:
        assert _avg_grid_options(int(value), avg), f"{tier} count {value} impossible at avg {avg}"
    for bad in forbidden:
        assert bad not in counts
    # and the truth must be inside the range
    assert counts[0] <= truth_count <= counts[2]
    assert cells[0] <= truth_cells <= cells[2]
