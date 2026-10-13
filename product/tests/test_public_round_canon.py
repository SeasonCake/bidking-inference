"""_public_round canon gate (作者 2026-07-11 口径 + 40局/族 census 实证).

沉船/排位56xx/hidden: 单一公开轮 = R1(旧规则把沉船推到 R3 —— 合成的 sophie/gabriela 在沉船图上
晚两轮才看到公开信息; 56xx/hidden 旧代码走 villa 分支只是碰巧正确)。villa: 首波 R1 / 次波 R3,
按 src_round 相对序(wire 戳滞后玩家可见轮一轮, [1,2] = 可见 R1+R3, 艾莎逐档揭示锚定)。
census: scripts/_tmp_public_round_census.py (沉船40/40·56xx40/40·hidden39/40 首现 wire R1)。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from synthesize_draw_hero_samples import _public_round  # noqa: E402


def test_shipwreck_single_public_round_is_r1() -> None:
    for map_id in (2501, 2508, 3502, 4501):
        for src in (1, 2, 3):
            assert _public_round(src, map_id) == 1


def test_ranked_and_hidden_single_public_round_is_r1() -> None:
    # 56xx 排位与 26xx hidden 同为 theme6; 即使个别样本因开局盲窗首现晚记(hidden census 1/40 在
    # wire R2), 口径仍是单一公开轮 R1。
    for map_id in (5602, 5611, 2601):
        for src in (1, 2, 3):
            assert _public_round(src, map_id) == 1


def test_villa_first_wave_r1_refresh_r3() -> None:
    for map_id in (2401, 2408, 3401, 4402):
        assert _public_round(1, map_id) == 1
        assert _public_round(2, map_id) == 3  # wire 2 = 玩家可见 R3(戳滞后一轮)
        assert _public_round(3, map_id) == 3


def test_uncalibrated_old_families_keep_two_wave_rule() -> None:
    assert _public_round(1, 2301) == 1
    assert _public_round(2, 2301) == 3
