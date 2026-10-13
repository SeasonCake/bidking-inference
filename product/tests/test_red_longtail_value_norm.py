"""Gate the revealed-red long-tail value normalization (leak audit fix L1/L2, 2026-06-27).

When a RED (q6) is revealed with a known item_id, the engine used to price it at its RAW catalog value
-> a revealed 世界冠军 (7.2M) / 非洲之心 (13M) / 新赛季大红 / mecha (30M) inflated the live headline bid
and the q6 value floor. The fix routes revealed-red values through the q6 long-tail normalized map
(red_longtail_normalized_0627.json, single source = aisha_calib). Non-long-tail reds are unchanged;
gold/below are never normalized (q6-only). env AHMAD_DISABLE_RED_LONGTAIL_NORM=1 reverts.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))

import ahmad_ref_engine as E  # noqa: E402

LT = E._load_red_longtail_normalized()
needs_map = pytest.mark.skipif(not LT, reason="red_longtail_normalized map not present")


@needs_map
def test_revealed_longtail_red_priced_at_normalized() -> None:
    """A revealed long-tail red (世界冠军 1036006, raw 7.2M) is summed at its normalized value, not raw."""
    iid = 1036006
    raw = 7_202_000
    norm = LT[iid]
    assert norm < raw and norm < 1_000_000  # normalized well below the raw long-tail
    items = [{"quality": 6, "item_id": iid, "value": raw, "cells": 6}]
    count, total = E._revealed_red_minimap_value(items)
    assert count == 1
    assert total == norm


@needs_map
def test_non_longtail_red_unchanged() -> None:
    """A normal red (not in the long-tail map) keeps its raw value (4403 麒麟芯片 54,600 class)."""
    items = [{"quality": 6, "item_id": 999999999, "value": 54_600, "cells": 1}]
    count, total = E._revealed_red_minimap_value(items)
    assert (count, total) == (1, 54_600)


@needs_map
def test_disable_env_reverts_to_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AHMAD_DISABLE_RED_LONGTAIL_NORM", "1")
    items = [{"quality": 6, "item_id": 1036006, "value": 7_202_000, "cells": 6}]
    _, total = E._revealed_red_minimap_value(items)
    assert total == 7_202_000


@needs_map
def test_fully_disclosed_longtail_red_uses_catalog_on_minimap() -> None:
    """Shape+item_id public reveal prices at catalog, not long-tail EV (200048 / 巨物 class)."""
    iid = 1086004
    raw = 1_688_400
    norm = LT[iid]
    assert norm < raw
    items = [
        {
            "quality": 6,
            "item_id": iid,
            "value": raw,
            "shape_code": 22,
            "cells": 4,
        }
    ]
    count, total = E._revealed_red_minimap_value(items)
    assert count == 1
    assert total == raw


@needs_map
def test_anchor_only_longtail_red_still_normalized_on_minimap() -> None:
    """Position-only id rows without shape keep long-tail normalization (L1/L2 guard)."""
    iid = 1036006
    raw = 7_202_000
    norm = LT[iid]
    items = [{"quality": 6, "item_id": iid, "value": raw, "cells": 6}]
    count, total = E._revealed_red_minimap_value(items)
    assert count == 1
    assert total == norm


def test_normalized_red_value_helper_handles_missing_fields() -> None:
    assert E._normalized_red_value({"item_id": None, "value": 100}) == 100
    assert E._normalized_red_value({"item_id": 1, "value": None}) is None


@needs_map
def test_longtail_map_matches_aisha_calib_live() -> None:
    """陈旧性 gate(防两源 desync): red_longtail_normalized_0627.json 必须 == aisha_calib 当场算的
    q6 长尾集 {item_id: normalized_item_value}。引擎读 JSON、评测(audit_aisha_gap)用 live aisha_calib
    —— 两源【只有刚跑过 regenerate_drop_weights 时才一致】。若改了 aisha_calib 长尾判据/值却没重生 JSON,
    引擎会用旧值过估而评测因不同源抓不到。本测试在 JSON 陈旧时变红, 强制重生(`scripts/calibration/
    regenerate_drop_weights.py`)。map 缺失时跳过(那是打包前置检查的职责, 见 build_live_portable.ps1 步骤0)。"""
    calib_dir = ROOT / "scripts" / "calibration"
    if str(calib_dir) not in sys.path:
        sys.path.insert(0, str(calib_dir))
    try:
        import aisha_calib as ac  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"aisha_calib 不可用: {exc}")
    ac._catalog()
    expected = {iid: int(ac.normalized_item_value(iid))
                for iid, (_v, _shp, q, _nm) in ac._CATALOG.items()
                if q == 6 and ac.is_longtail(iid)}
    loaded = E._load_red_longtail_normalized()  # {int: int}
    only_json = set(loaded) - set(expected)
    only_calib = set(expected) - set(loaded)
    val_diff = {k: (loaded.get(k), expected.get(k))
                for k in set(loaded) & set(expected) if loaded.get(k) != expected.get(k)}
    assert loaded == expected, (
        "red_longtail_normalized_0627.json 与 aisha_calib 不一致(陈旧?)-> 重跑 "
        "scripts/calibration/regenerate_drop_weights.py。"
        f"  仅JSON有={sorted(only_json)}  仅calib有={sorted(only_calib)}  值差={val_diff}"
    )
