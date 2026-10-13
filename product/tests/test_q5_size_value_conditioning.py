"""Q5 size-value conditioning gate (审计窗口 2026-07-11 · 血色 5611 案).

金件数+格/件【精确已知】时, q5 单价按 Q5_SIZE_VALUE_POINTS(29,131 结算点全局分箱中位·
corr 0.88-0.91)条件化, 替代扁平经验均值 —— 修"件数锁全对但大金(9.5格/件)按均价估→报价欠 22%"。
默认 ON(300行 ahmed clean_eval sweep: 隔离0违规·cpi≥6.5 med −44.4→−32.9%·cons破66→59);
cpi<2 排除(sweep 中位变差)。kill=AHMAD_ENABLE_Q5_SIZE_VALUE=0。

含本地门: 血色 5611 真快照重放(gitignored fixture 缺失则 skip)。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))

from ahmad_ref_engine import (  # noqa: E402
    Q5_SIZE_VALUE_NOTE_PREFIX,
    _apply_q5_size_conditioned_value,
    _q5_size_unit_value,
    run_reference_engine,
)

ENV = "AHMAD_ENABLE_Q5_SIZE_VALUE"
FIXTURE_DIR = ROOT / "data" / "samples" / "recordings_inbox_20260711_desktop_diag5611_xuese"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    monkeypatch.delenv("AHMAD_Q5_SIZE_VALUE_MIN_CPI", raising=False)


def test_unit_value_interpolation_and_clamp() -> None:
    assert _q5_size_unit_value(9.5) == pytest.approx(64890.0)      # 5611 案: bin9 中心精确命中
    assert _q5_size_unit_value(0.5) == pytest.approx(16200.0)      # 下端截断
    assert _q5_size_unit_value(20.0) == pytest.approx(85800.0)     # 上端截断
    mid = _q5_size_unit_value(2.0)                                  # 1.5/2.5 中点线性
    assert mid == pytest.approx((16200.0 + 24793.0) / 2.0)


def _evidence(avg=None, soft=(), cells=None, cnt=None):
    return SimpleNamespace(
        avg_cells={} if avg is None else {"q5": avg},
        soft_avg_cell_keys=frozenset(soft),
        quality_cells={} if cells is None else {"q5": cells},
        fixed_counts={} if cnt is None else {"q5": cnt},
    )


def test_apply_gates() -> None:
    base = {"q5": 30000.0, "q6": 100000.0}
    # regime on + 精确均格 -> 换表值
    out, note = _apply_q5_size_conditioned_value(base, _evidence(avg=9.5), value_on=True)
    assert out["q5"] == pytest.approx(64890.0) and note and note.startswith(Q5_SIZE_VALUE_NOTE_PREFIX)
    assert out["q6"] == base["q6"]                       # 只动 q5
    # 公开软均格(count 未锁)不触发
    out, note = _apply_q5_size_conditioned_value(base, _evidence(avg=9.5, soft=("q5",)), value_on=True)
    assert out is base and note is None
    # 软均格但 cells+fixed count 双知 -> 走派生分支仍触发
    out, note = _apply_q5_size_conditioned_value(
        base, _evidence(avg=9.5, soft=("q5",), cells=57, cnt=6), value_on=True)
    assert out["q5"] == pytest.approx(64890.0) and note
    # cpi<2 小金域排除(sweep 中位变差)
    out, note = _apply_q5_size_conditioned_value(base, _evidence(avg=1.5), value_on=True)
    assert out is base and note is None
    # regime 门: value_on=False(非 ahmed)不触发
    out, note = _apply_q5_size_conditioned_value(base, _evidence(avg=9.5), value_on=False)
    assert out is base and note is None
    # kill switch
    os.environ[ENV] = "0"
    try:
        out, note = _apply_q5_size_conditioned_value(base, _evidence(avg=9.5), value_on=True)
        assert out is base and note is None
    finally:
        os.environ.pop(ENV, None)


def _snapshot_5611_like() -> dict:
    # ahmed 决策轮合成: 总件40 + 金件6(道具100119)+ 金均格9.5(精确) —— 血色 5611 的证据形状。
    return {
        "ui_contract": {
            "context": {"hero": "ahmed", "map_id": 5611, "phase": "bidding", "round": 4},
            "constraints": {"public_info": {}},
        },
        "structured_ref_inputs": {
            "total_count": 40,
            "counts": {"q1": 12, "q3": 11, "q4": 9, "q5": 6},
            "avg_cells": {"q1": 2.0, "q3": 31.0 / 11.0, "q4": 34.0 / 9.0, "q5": 9.5},
            "quality_cells": {"q1": 24.0, "q4": 34.0},
            "split_counts": {"white": 4, "green": 8},
        },
        "public_info_rows": [],
    }


def _run(snap: dict, env: str | None, max_combos: int = 8000) -> dict:
    if env is None:
        os.environ.pop(ENV, None)
    else:
        os.environ[ENV] = env
    try:
        return run_reference_engine(snap, max_combos=max_combos).as_dict()
    finally:
        os.environ.pop(ENV, None)


def _notes(res: dict) -> list[str]:
    return [str(n) for n in (res.get("notes") or [])]


def test_synthetic_ahmed_gold_cpi_fires_and_kill_reverts() -> None:
    snap = _snapshot_5611_like()
    off = _run(snap, "0")
    default = _run(snap, None)
    on = _run(snap, "1")
    assert not any(n.startswith(Q5_SIZE_VALUE_NOTE_PREFIX) for n in _notes(off))
    for res in (default, on):   # 默认 ON = 强开同效
        fired = [n for n in _notes(res) if n.startswith(Q5_SIZE_VALUE_NOTE_PREFIX)]
        assert fired and "cpi=9.5" in fired[0]
    assert default["balanced"] == on["balanced"]
    # 9.5格/件大金: 表值(64,890)≈2×扁平均值 -> 报价必须显著抬升
    assert default["balanced"] > off["balanced"]


def test_aisha_not_in_regime_untouched() -> None:
    snap = _snapshot_5611_like()
    snap["ui_contract"]["context"]["hero"] = "aisha"
    res = _run(snap, None)
    assert not any(n.startswith(Q5_SIZE_VALUE_NOTE_PREFIX) for n in _notes(res))


def test_xuese_5611_replay_gate() -> None:
    f = FIXTURE_DIR / "r_final_bidding.json"
    if not f.exists():
        pytest.skip("faithful 5611 snapshot absent (gitignored) — local-only gate")
    snap = json.loads(f.read_text(encoding="utf-8"))
    truth = json.loads((FIXTURE_DIR / "settled_truth.json").read_text(encoding="utf-8"))["total_value"]
    assert truth == 816208
    off = _run(snap, "0")
    on = _run(snap, None)   # 默认 ON
    # OFF = 复现实战欠估(与 live 0.2.6-hotfix1 逐字节一致的回放基线)
    assert off["balanced"] == 634368
    # ON: 触发 note + 误差 |−22.3%| -> |+9.9%|(离真值更近), 红值不动(隔离)
    fired = [n for n in _notes(on) if n.startswith(Q5_SIZE_VALUE_NOTE_PREFIX)]
    assert fired and "cpi=9.5" in fired[0]
    assert abs(on["balanced"] - truth) < abs(off["balanced"] - truth)
    assert on["red_value_range"] == off["red_value_range"]
