"""StreamSeqDiag 设计门 —— 这个检测器要用来给"楔死根因"定案, 所以它自己必须先被证明。

⭐核心要求(错一条, 取证结论就是假的):
  1. 【干净流必须报 0】 一个完好无损的流上报出 gap = 假阳性 = 会把无辜的抓包链路定罪。
  2. 【真空洞必须报出来, 且字节数要准】 这是唯一能证明"字节根本没抓到"的证据。
  3. 【重传/乱序 ≠ 空洞】 重传是无害的(朴素 extend 照样能拼满长度); 把它算成 gap 就会
     把"乱序假说"错误地"证实"一遍 —— 恰恰是本次要证伪的那个假说。
  4. 【乱序之后不能连环假报】 一个迟到段回填后, 后续正常包不许再被报成 gap。
"""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bidking_lab.live.stream_diag import _SEQ_MOD, StreamSeqDiag  # noqa: E402

A = ("1.1.1.1", 10000, "2.2.2.2", 5555)
B = ("1.1.1.1", 10000, "2.2.2.2", 6666)


# --------------------------------------------------------------------------- #
# ⭐要求 1: 干净流必须报 0(否则一切阳性结论都不可信)
# --------------------------------------------------------------------------- #
def test_clean_stream_reports_no_gap():
    d = StreamSeqDiag()
    seq = 1000
    for _ in range(50):
        assert d.observe(A, seq, 1460) in ("first", "inorder")
        seq += 1460
    assert d.gaps == 0 and d.gap_bytes == 0 and d.overlaps == 0
    assert d.packets == 50


def test_two_streams_do_not_contaminate_each_other():
    d = StreamSeqDiag()
    d.observe(A, 1000, 100)
    d.observe(B, 77000, 100)          # 完全不同的 seq 空间, 不是 A 的空洞
    d.observe(A, 1100, 100)
    d.observe(B, 77100, 100)
    assert d.gaps == 0


# --------------------------------------------------------------------------- #
# ⭐要求 2: 真空洞必须报出来, 且字节数准确
# --------------------------------------------------------------------------- #
def test_gap_detected_with_exact_byte_count():
    d = StreamSeqDiag()
    d.observe(A, 1000, 1460)                       # 期望下一个 = 2460
    assert d.observe(A, 2460 + 4380, 1460) == "gap"  # 跳过了 3 个段
    assert d.gaps == 1
    assert d.gap_bytes == 4380
    assert d.max_gap_bytes == 4380
    assert d.unresolved_gaps == 1
    assert d.unresolved_gap_bytes == 4380


def test_gap_matches_the_real_wedge_shape():
    """真实楔死: 帧头声明 20093B, 缓冲只有 10103B -> 少 9990B。检测器要能报出这个数。"""
    d = StreamSeqDiag()
    seq = 5000
    d.observe(A, seq, 10103)          # 帧头 + 前 10103 字节到了
    d.observe(A, seq + 10103 + 9990, 500)   # 中间 9990 字节从没出现
    assert d.gaps == 1 and d.gap_bytes == 9990


# --------------------------------------------------------------------------- #
# ⭐要求 3: 重传/乱序【不是】空洞 —— 这条决定"乱序假说"能不能被证伪
# --------------------------------------------------------------------------- #
def test_retransmit_is_overlap_not_gap():
    d = StreamSeqDiag()
    d.observe(A, 1000, 1460)
    d.observe(A, 2460, 1460)
    assert d.observe(A, 1000, 1460) == "overlap"   # 重传老段
    assert d.gaps == 0, "重传被误报成空洞 -> 会把无辜的网络定罪"
    assert d.overlaps == 1


def test_reorder_then_resume_does_not_cascade_false_gaps():
    """⭐迟到段回填后, 后续正常包不许再被连环报成 gap(否则一个乱序会伪造出几十个空洞)。"""
    d = StreamSeqDiag()
    d.observe(A, 1000, 1460)          # [1000,2460)
    d.observe(A, 3920, 1460)          # [3920,5380) 先到 -> 报 gap(1460)
    assert d.gaps == 1 and d.gap_bytes == 1460
    assert d.unresolved_gap_bytes == 1460
    assert d.observe(A, 2460, 1460) == "overlap"   # 迟到的 [2460,3920) 补上 -> overlap
    assert d.unresolved_gap_bytes == 0
    assert d.unresolved_gaps == 0
    assert d.filled_gap_bytes == 1460
    assert d.observe(A, 5380, 1460) == "inorder"   # 后续继续正常
    assert d.gaps == 1, "乱序回填后又报了新空洞 -> 连环假阳性"


# --------------------------------------------------------------------------- #
# 边界: 32 位回绕 / 无 seq / 流数上限
# --------------------------------------------------------------------------- #
def test_seq_wraparound_is_not_a_4gb_gap():
    d = StreamSeqDiag()
    d.observe(A, _SEQ_MOD - 100, 100)   # 到达 seq 空间末尾, 下一个期望 = 0(回绕)
    assert d.observe(A, 0, 200) == "inorder"
    assert d.gaps == 0, "seq 回绕被误报成 4GB 空洞"


def test_missing_seq_is_ignored_not_treated_as_wrap():
    """WinDivert 路径不提供 seq(=0)。绝不能把它当成回绕到 0。"""
    d = StreamSeqDiag()
    d.observe(A, 0, 1460)
    d.observe(A, 0, 1460)
    assert d.packets == 0 and d.gaps == 0


def test_zero_length_payload_ignored():
    d = StreamSeqDiag()
    d.observe(A, 1000, 0)             # 纯 ACK
    assert d.packets == 0


def test_stream_table_is_bounded():
    d = StreamSeqDiag()
    for port in range(200):
        d.observe(("1.1.1.1", 10000, "2.2.2.2", port), 1000, 10)
    assert len(d._next_seq) <= 64


def test_as_dict_shape():
    d = StreamSeqDiag(label="wire")
    d.observe(A, 1000, 100)
    d.observe(A, 1300, 100)           # gap 200
    assert d.as_dict() == {
        "packets": 2, "gaps": 1, "gap_bytes": 200, "max_gap_bytes": 200, "overlaps": 0,
        "unresolved_gaps": 1, "unresolved_gap_bytes": 200, "filled_gap_bytes": 0,
    }


def test_partial_late_fill_leaves_only_the_still_missing_bytes():
    d = StreamSeqDiag()
    d.observe(A, 1000, 100)            # [1000,1100)
    d.observe(A, 1400, 100)            # jump over [1100,1400)
    d.observe(A, 1200, 100)            # fill only [1200,1300)
    assert d.unresolved_gaps == 2
    assert d.unresolved_gap_bytes == 200
    assert d.filled_gap_bytes == 100


def test_overlap_extending_past_high_water_advances_expected():
    d = StreamSeqDiag()
    d.observe(A, 1000, 200)            # expected = 1200
    assert d.observe(A, 1100, 200) == "overlap"  # retransmit extends through 1300
    assert d.observe(A, 1300, 100) == "inorder"
    assert d.gaps == 0
    assert d.unresolved_gap_bytes == 0


def test_late_fill_across_sequence_wrap_closes_the_hole():
    d = StreamSeqDiag()
    d.observe(A, _SEQ_MOD - 200, 100)  # expected = mod-100
    d.observe(A, 100, 100)             # jump 200B across wrap
    assert d.unresolved_gap_bytes == 200
    d.observe(A, _SEQ_MOD - 100, 200)  # late segment covers both sides of wrap
    assert d.unresolved_gap_bytes == 0
    assert d.filled_gap_bytes == 200
