# -*- coding: utf-8 -*-
"""服务端欠计兜底判据的设计门(collector `_declared_gt_captured` + 一次性补跑脚本)。

背景: 客户端那道结算完整性门【被收紧过】—— `e04e259`(2026-06-23)初版是 **0.8 比例门**
(`inventory_count < declared * 0.8` 才算残缺), 后来才收紧成"缺一件就算"。群里大量客户端仍在
跑老包, 于是"少抓 1~11 件"的欠计样本带着 `settlement_incomplete=False` 混进了主库
(实测 121/26,813, 比例全部落在 0.800~0.984 = 恰好老门放行的区间)。服务端补这道网。

⭐三条铁律(错一条就会污染主库或误伤好样本):
  1. 判据必须用 `inventory_count`(实抓件数), **绝不能**用 `sum(final_quality_counts)` ——
     后者只统计【有品质】的藏品; 品质解析不出的件不在里面 => 会把好样本误判成欠计。
     (这个坑当天咬了发布安全窗口两次: 真值少算 48,300, 欠计多报 28 条。)
  2. 缺字段 => **不隔离**(判不了就别动), 绝不靠估算回退。
  3. 实抓【多于】声明(count-definition jitter, 实测 22 局)=> **不是欠计**, 不许隔离。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import rescreen_collected_samples as rs  # noqa: E402
import sample_collector_server as scs  # noqa: E402


def _rec(**kw):
    r = {"map_id": 2501, "hero": "ahmed", "final_quality_counts": "q1=2;q5=1;q6=2",
         "known_value_sum": 500000}
    r.update(kw)
    return r


# --------------------------------------------------------------------------- #
# 判据本身
# --------------------------------------------------------------------------- #
def test_declared_gt_captured_flags_undercount():
    """血色 2501 那类: 声明 61 / 实抓 56 -> 欠计。"""
    assert rs.declared_gt_captured(_rec(total_item_count=61, inventory_count=56)) is True


def test_old_ratio_gate_band_is_caught():
    """⭐老的 0.8 比例门放行的那一段(0.8~1.0)正是漏网区 —— 新判据必须抓住它。"""
    for declared, captured in ((50, 46), (60, 55), (40, 39), (55, 44)):   # 比例 0.92/0.917/0.975/0.80
        assert rs.declared_gt_captured(
            _rec(total_item_count=declared, inventory_count=captured)
        ) is True, f"declared={declared} captured={captured} 没被抓到"


def test_complete_settlement_not_flagged():
    assert rs.declared_gt_captured(_rec(total_item_count=61, inventory_count=61)) is False


def test_captured_more_than_declared_is_not_undercount():
    """⭐实测 22 局 实抓 > 声明(count-definition jitter)。这【不是】欠计, 不许隔离。"""
    assert rs.declared_gt_captured(_rec(total_item_count=57, inventory_count=66)) is False


def test_missing_fields_never_quarantine():
    """⭐判不了就别动 —— 缺任一字段一律不隔离(绝不估算回退)。"""
    assert rs.declared_gt_captured(_rec(inventory_count=56)) is False              # 无声明
    assert rs.declared_gt_captured(_rec(total_item_count=61)) is False             # 无实抓
    assert rs.declared_gt_captured(_rec()) is False                                # 都没有
    assert rs.declared_gt_captured(_rec(total_item_count=0, inventory_count=0)) is False
    assert rs.declared_gt_captured(_rec(total_item_count="61", inventory_count=56)) is False  # 类型异常


def test_never_use_final_quality_counts_as_captured():
    """🔴 铁律 1: 品质解析不出的件不在 final_quality_counts 里 —— 用它当实抓件数会误伤好样本。

    真实案例(血色 2501 的乌木椅 1065006): 61 件全抓到了, 但其中 1 件品质解析不出 =>
    sum(final_quality_counts) = 60 < 声明 61 => 若用它判, 会把一个【完整】的结算误隔离。
    """
    good = _rec(total_item_count=61, inventory_count=61,
                final_quality_counts="q1=7;q2=10;q3=12;q4=15;q5=10;q6=6")  # 合计 60, 少的那件品质未解
    assert sum(int(v) for v in (s.split("=")[1] for s in good["final_quality_counts"].split(";"))) == 60
    assert rs.declared_gt_captured(good) is False, "用 final_quality_counts 判 => 误伤完整结算"


# --------------------------------------------------------------------------- #
# collector 接线: 判据必须真的挂在 _is_abnormal 上(否则新样本照样漏进主库)
# --------------------------------------------------------------------------- #
def test_collector_quarantines_undercount():
    assert scs._is_abnormal(_rec(total_item_count=61, inventory_count=56)) is True


def test_collector_still_accepts_healthy():
    assert scs._is_abnormal(_rec(total_item_count=61, inventory_count=61)) is False


def test_collector_keeps_existing_gates():
    """原有两道门不能被弄丢。"""
    assert scs._is_abnormal(_rec(settlement_incomplete=True)) is True
    assert scs._is_abnormal(_rec(known_value_sum=0)) is True
    assert scs._is_abnormal(_rec(known_value_sum=None)) is True


def test_collector_and_rescreen_share_one_criterion():
    """⭐两边判据必须一致 —— 分叉了就会出现"服务端收下、补跑又隔离"的抖动。"""
    for d, c in ((61, 56), (61, 61), (57, 66), (50, 40)):
        r = _rec(total_item_count=d, inventory_count=c)
        assert scs._is_abnormal(r) == rs.declared_gt_captured(r) or scs._is_abnormal(r) is True


# --------------------------------------------------------------------------- #
# 补跑脚本: 动生产数据, 必须默认 dry-run + 备份 + 幂等
# --------------------------------------------------------------------------- #
def _write(p: Path, recs):
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n", encoding="utf-8")


def _run(main_path, *extra):
    import contextlib
    import io
    buf = io.StringIO()
    argv = ["rescreen", str(main_path), *extra]
    old = sys.argv
    sys.argv = argv
    try:
        with contextlib.redirect_stdout(buf):
            rc = rs.main()
    finally:
        sys.argv = old
    return rc, buf.getvalue()


def test_dry_run_does_not_touch_files(tmp_path):
    """⭐默认 dry-run: 一个字节都不许改。"""
    main = tmp_path / "samples.jsonl"
    _write(main, [_rec(total_item_count=61, inventory_count=56), _rec(total_item_count=61, inventory_count=61)])
    before = main.read_bytes()
    rc, out = _run(main)
    assert rc == 0
    assert main.read_bytes() == before, "dry-run 改了文件!"
    assert not (tmp_path / "samples_quarantine.jsonl").exists()
    assert "1" in out and "dry-run" in out


def test_apply_moves_only_undercount_and_backs_up(tmp_path):
    main = tmp_path / "samples.jsonl"
    bad = _rec(total_item_count=61, inventory_count=56, map_id=9001)
    ok1 = _rec(total_item_count=61, inventory_count=61, map_id=9002)
    ok2 = _rec(total_item_count=57, inventory_count=66, map_id=9003)   # 实抓>声明: 不是欠计
    ok3 = _rec(map_id=9004)                                            # 无声明: 判不了
    _write(main, [bad, ok1, ok2, ok3])

    rc, out = _run(main, "--apply")
    assert rc == 0

    kept = [json.loads(x) for x in main.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert [r["map_id"] for r in kept] == [9002, 9003, 9004], "隔离了不该隔离的行"

    q = [json.loads(x) for x in (tmp_path / "samples_quarantine.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(q) == 1 and q[0]["map_id"] == 9001
    assert q[0]["_quarantine_reason"] == "declared_gt_captured_rescreen"   # 留痕, 可回滚

    assert list(tmp_path.glob("samples.jsonl.bak_rescreen_*")), "没有备份就动了生产数据!"


def test_apply_is_idempotent(tmp_path):
    """⭐再跑一次不许重复隔离(否则每跑一次主库就掉一批)。"""
    main = tmp_path / "samples.jsonl"
    _write(main, [_rec(total_item_count=61, inventory_count=56), _rec(total_item_count=61, inventory_count=61)])
    _run(main, "--apply")
    after_first = main.read_text(encoding="utf-8")
    q_first = (tmp_path / "samples_quarantine.jsonl").read_text(encoding="utf-8")

    _run(main, "--apply")
    assert main.read_text(encoding="utf-8") == after_first
    assert (tmp_path / "samples_quarantine.jsonl").read_text(encoding="utf-8") == q_first


def test_unparseable_lines_are_never_dropped(tmp_path):
    """解析不了的行必须原样留在主库 —— 绝不能因为读不懂就丢掉数据。"""
    main = tmp_path / "samples.jsonl"
    main.write_text(
        json.dumps(_rec(total_item_count=61, inventory_count=61), ensure_ascii=False) + "\n"
        + "{这不是合法 json\n",
        encoding="utf-8",
    )
    rc, out = _run(main, "--apply")
    assert rc == 0
    assert "{这不是合法 json" in main.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# ❌ 已证伪存档: settlement_parse_residual 作为完整性判据 = 100% 假阳性。别再实现。
# --------------------------------------------------------------------------- #
def test_parse_residual_is_NOT_a_completeness_gate():
    """🔴 `walk 断了` != `件数丢了` —— 残段里装的是别的结构, 不是藏品。

    发布安全窗口 2026-07-12 一度把 `settlement_parse_residual > 0` 当成【英雄无关】的完整性判据
    (350bcb1), 因为它不依赖"精确总件数"声明(那个基本只有 ahmed 有: ahmed 97.5% / aisha 36.6%
    / gabriela 3.7% / raven 5.7%)。审计窗口当天用【已知阴性金标准】证伪:

        离线 fatbeans valid_ahmed 400 局(34ea6f1 的 body-union 一件都没捞回 ⇒ 结算确实完整):
            判据报阳 10 局 —— 真欠计 0, 【假阳性 10 = 100%】(residual 6 ~ 2,462 B)

    ⚠️阈值救不了: 血色那帧的【真】欠计 residual = 139 B, 假阳性却高达 2,462 B —— 真阳性比假阳性还小。
    ⚠️格外危险: 它要补的正是【非 ahmed 的盲区】, 那些英雄没有任何交叉验证手段 ⇒ 误伤健康样本
      【没有任何人会发现】。这是把"盲区"升级成"主动丢数据", 比现状更糟。

    ⭐方法论教训(比 bug 本身值钱): 发布安全窗口只在【阳性侧】验了(离群机 11/17 报阳), 拿
      "6/17 能报 0" 当成了特异性证明。**"能报 clean" != "报阳的都是真的"** ——
      特异性必须在【已知阴性金标准】上验。

    本测试锁死: collector 【不得】拿 residual 当隔离判据。
    """
    assert not hasattr(scs, "_parse_residual_broken"), "判据已被证伪, 不许重新实现"
    # 一个 residual 很大但结算完整的样本(离线金标准里真实存在) -> 绝不能被隔离
    healthy_but_broken_walk = _rec(hero="gabriela", settlement_parse_residual=2462,
                                   total_item_count=44, inventory_count=44)
    assert scs._is_abnormal(healthy_but_broken_walk) is False, "误伤了健康样本!"
