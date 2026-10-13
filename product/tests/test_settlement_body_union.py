"""Settlement body-union gate (2026-07-11 群友 2401: overlay 结算 366,842 vs 游戏 565,606).

The 0x2d settlement frame was COMPLETE (8118B, length-valid) and held all 48 declared items, but the
top-level field walk desyncs at offset ~5826 — everything after (per-player f4 blocks in the lost f2
region + top-level tail wrappers) was dropped: 28/48 items, and the f3 merge never ran past the break.
Fix = body union: read ALL payload f4 blocks + slide the whole body for stranded item wrappers
(additive, 3-layer guards, dedup). Validated on 13 matches of the same live session: 12 healthy
frames byte-invariant (union == settled truth), the broken frame recovers exactly 48 whose catalog
sum equals the game's 战利品价格 565,606. Kill: BIDKING_DISABLE_SETTLEMENT_BODY_UNION=1.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from bidking_lab.live.fatbeans import (  # noqa: E402
    load_fatbeans_packets_from_rows,
    parse_fatbeans_packets,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "live"
BROKEN_2401 = FIXTURES / "settlement_2401_body_union_npcap_live.jsonl"
HEALTHY_2408 = FIXTURES / "settlement_2408_multi_f4_npcap_live.jsonl"
KILL = "BIDKING_DISABLE_SETTLEMENT_BODY_UNION"


def _settled_items(path: Path):
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    events = parse_fatbeans_packets(load_fatbeans_packets_from_rows(rows))
    settled = next(s for s in events.states if s.message_id == 0x002D)
    return settled.inventory_items


def test_2401_desynced_frame_recovers_all_declared_items() -> None:
    items = _settled_items(BROKEN_2401)
    assert len(items) == 48  # ahmed declared 48; game loot value 565,606
    dist = Counter(i.quality for i in items)
    # loot screen dist 2白/8绿/14蓝/16紫/8金/0红; one q5 arrives with wire quality unset (catalog
    # recovery at the summary layer restores it — BUG-C path), hence 7 wired + 1 None here.
    assert dist[1] == 2 and dist[2] == 8 and dist[3] == 14 and dist[4] == 16
    assert dist[5] + dist[None] == 8 and dist[6] == 0
    # the recovered set must include the 9-cell gold that dominated the missing value
    assert any(i.item_id == 1065006 and i.cells == 9 for i in items)


def test_2401_kill_env_restores_old_undercount(monkeypatch) -> None:
    monkeypatch.setenv(KILL, "1")
    items = _settled_items(BROKEN_2401)
    assert len(items) == 28


def test_healthy_multi_f4_frame_is_invariant(monkeypatch) -> None:
    # 2408 same-session healthy frame: complete before (f3 merge) and after — union adds nothing.
    items_on = _settled_items(HEALTHY_2408)
    assert len(items_on) == 59  # == that match's settled truth (ui_summary)
    monkeypatch.setenv(KILL, "1")
    items_off = _settled_items(HEALTHY_2408)
    assert len(items_off) == 59
    assert {(i.runtime_id, i.item_id) for i in items_on} == {
        (i.runtime_id, i.item_id) for i in items_off
    }
