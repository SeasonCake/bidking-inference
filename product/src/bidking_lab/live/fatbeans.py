"""Fatbeans JSON capture adapter.

This module is deliberately read-only. It parses FatbeansCreater JSON exports
into normalized game events, then exposes a narrow conversion for fields that
already have stable ``LiveObservationBatch`` semantics.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import secrets
import struct
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from bidking_lab.live.types import (
    FieldUpdate,
    GridItemObservation,
    LiveObservationBatch,
)

_CATEGORY_OUTLINE_ACTIONS: dict[int, int] = {
    100151: 101,  # 家具物品
    100152: 102,  # 医疗药品
    100153: 103,  # 时尚潮流
    100154: 104,  # 兵装军火
    100155: 105,  # 珠宝矿藏
    100156: 106,  # 文物古董
    100157: 107,  # 数码娱乐
    100158: 108,  # 能源交通
    100159: 109,  # 食饮珍馐
    100160: 110,  # 书画古籍
}
_HERO_MODE_BY_ID: dict[int, str] = {
    101: "fatima",
    102: "chenmei",
    103: "aisha",
    104: "gabriela",
    105: "tatiana",
    106: "naomi",
    107: "sophie",
    108: "maria",
    109: "helena",
    110: "isabella",
    201: "george",
    202: "carlos",
    203: "leonard",
    204: "ahmed",
    205: "ivan",
    206: "takeda",
    207: "wuqilin",
    208: "ethan",
    209: "victor",
    301: "raven",
    302: "liss",
    303: "indiana",
}
_SKILL_REVEAL_CATEGORIES: dict[int, int] = {
    100101: 106,  # 法蒂玛: 文物古董轮廓/品质
    1001011: 106,
    1001012: 106,
    1001013: 106,
    1001014: 106,
    100105: 103,  # 塔蒂安娜: 时尚潮流轮廓/品质
    100109: 102,  # 海琳娜: 医疗药品品质/轮廓
    1001091: 102,
    1001092: 102,
    1001093: 102,
    1001094: 102,
    1001101: 105,  # 伊莎贝拉: 珠宝矿藏轮廓
    100201: 104,  # 乔治: 兵装军火轮廓/品质
    100206: 110,  # 武田宏志: 书画古籍轮廓/品质
    1002062: 110,
    1002063: 110,
    1002064: 110,
    1002065: 110,
    10002071: 106,  # 吴起灵: 文物古董轮廓
    10002072: 106,  # 吴起灵: 文物古董品质
    10002073: 106,  # 吴起灵: 文物古董完整信息
}
_POSTUPDATE_SKILL_REVEAL_CATEGORIES: dict[int, int] = {
    # Only actions whose official text has a single category belong here. The
    # opening union reveals for Chenmei (jewelry+fashion), Naomi
    # (fashion+digital), Carlos (furniture+digital), and Ivan (weapon+energy)
    # deliberately remain uncategorized rather than being coerced into one side.
    1001021: 105,  # 陈美: 珠宝矿藏完整信息
    1001022: 105,
    1001023: 105,
    1001024: 105,
    1001051: 103,  # 塔蒂安娜: 时尚潮流完整信息
    1001052: 103,
    1001053: 103,
    1001054: 103,
    1001062: 107,  # 娜奥米: 数码娱乐完整信息
    1001063: 107,
    1001064: 107,
    10010921: 102,  # 海琳娜: 医疗药品完整信息（嵌套 action）
    10010931: 102,
    10010941: 102,
    1002011: 104,  # 乔治: 兵装军火完整信息
    1002012: 104,
    1002013: 104,
    1002014: 104,
    10020221: 101,  # 卡洛斯: 家具物品完整信息（嵌套 action）
    10020231: 101,
    10020241: 101,
    100203: 109,  # 莱昂纳德: 食饮珍馐品质
    1002031: 106,  # 开局额外文物古董品质
    1002032: 109,  # 后续食饮珍馐完整信息
    1002033: 109,
    1002034: 109,
    1002061: 110,  # 武田宏志: 书画古籍递进帧
    10020631: 110,
    10020641: 110,
    10020651: 110,
}
_ACTION_SESSION_FIELDS: dict[int, tuple[str, ...]] = {
    100103: ("session", "warehouse_total_cells"),  # 总仓储空间
    100115: ("session", "total_item_count"),  # 库存清点
}
_ACTION_TOTAL_CELLS: dict[int, int] = {
    100104: 1,  # 普品扫描: white+green merged bucket
    100105: 3,  # 良品扫描
    100106: 4,  # 优品扫描
    100107: 5,  # 极品扫描
    100108: 6,  # 珍品扫描
}
_ACTION_AVG_CELLS: dict[int, int] = {
    100110: 1,  # 普品均格: white+green merged bucket
    100111: 3,  # 良品均格
    100112: 4,  # 优品均格
    100113: 5,  # 极品均格
    100114: 6,  # 珍品均格
}
_ACTION_VALUE_SUM: dict[int, int] = {
    100123: 3,  # 良品估价
    100124: 4,  # 优品估价
    100125: 5,  # 极品估价
    100126: 6,  # 珍品估价
}
_ACTION_COUNT: dict[int, int] = {
    100117: 3,  # 良品存量
    100118: 4,  # 优品存量
    100119: 5,  # 极品存量
    100120: 6,  # 珍品存量
}
_ACTION_GREEN_SPLIT_FIELDS: dict[int, tuple[str, ...]] = {
    100116: ("bucket_split", "green", "count"),  # 普品存量: green only
    100122: ("bucket_split", "green", "value_sum"),  # 普品估价: green only
}
_AHMAD_SKILL_SESSION_FIELDS: dict[int, tuple[str, ...]] = {
    100204: ("session", "total_item_count"),  # R1: total item count
}
_AHMAD_SKILL_AVG_CELLS: dict[int, int] = {
    1002041: 5,  # R2: gold/orange avg cells
    1002042: 4,  # R3: purple avg cells
    1002043: 3,  # R4: blue avg cells
}
_AHMAD_SKILL_COUNT: dict[int, int] = {
    1002044: 1,  # R5: green+white merged item count
}
_VICTOR_SKILL_COUNT_SUM: dict[int, tuple[str, ...]] = {
    100209: ("bucket_group", "q4q5q6", "count"),  # R1: q4 + q5 + q6 item count
}
POSTUPDATE_NINE_HERO_SKILL_COMPAT_DISABLE = (
    "BIDKING_DISABLE_POSTUPDATE_NINE_HERO_SKILL_COMPAT"
)
_NAOMI_SCOPED_SKILL_COUNT_SUM: dict[int, tuple[str, ...]] = {
    # opening: exact gold+red count inside the Fashion + Digital category scope
    100106: ("category_quality_group", "103+107", "q5q6", "count"),
}
# Structured-ref heroes: skill ids that uniquely identify the local player's hero when
# player_id is not wired yet (common at R1 on ship maps with 4 bid slots visible).
_STRUCTURED_REF_HERO_SKILL_IDS: dict[str, frozenset[int]] = {
    "ahmed": frozenset(_AHMAD_SKILL_SESSION_FIELDS) | frozenset(_AHMAD_SKILL_AVG_CELLS) | frozenset(_AHMAD_SKILL_COUNT),
    "aisha": (
        frozenset(_ACTION_SESSION_FIELDS)
        | frozenset(_ACTION_COUNT)
        | frozenset(_ACTION_AVG_CELLS)
        | frozenset(_ACTION_GREEN_SPLIT_FIELDS)
    ),
    "victor": frozenset(_VICTOR_SKILL_COUNT_SUM),
    # Current wire evidence identifies Indiana's opening outline plus one
    # quality-only reveal at R3/R4/R5.  These are identity anchors only: unlike
    # the category skills above, they neither name category 111 nor prove a
    # complete warehouse count.  The child rows enrich only their exact
    # runtime item; their local_index is not a stable identity.
    "indiana": frozenset({1003031, 1003033, 1003034, 1003035}),
}
MARIA_HERO_ID = 108
ETHAN_HERO_ID = 208
ETHAN_SKILL_R1_OUTLINE = 1002081
ETHAN_QUALITY_OUTLINE_SKILLS = frozenset({1002082, 1002083, 1002084})
ETHAN_SKILL_FULL_OUTLINE = 1002085
_MARIA_SKILL_QUALITY_REVEAL_ID = 10010801
# 100108「玛丽亚·总价」= 低品三档(白+绿+蓝)合计价值, 非白品单档 —— 2026-07-10 回放坐实(2407 样本
# result=49227 = 结算 q1+q2+q3 值之和)。旧结论"100108=white-tier value"(06-12)是错的, 把它写成
# bucket/1/value_sum 会把白品价值抬 ~47x。玛丽亚估价证据在引擎侧走 skill_reveal_rows 直接路径
# (_apply_maria_skill_evidence 读 result), structured_ref 无「低品合计」字段, 故此处不再产生单档
# value_sum。品质分布(10010801)仍作位置 marker 单独处理。空 dict 保留供未来逐档总价技能接入。
_MARIA_SKILL_VALUE_SUM: dict[int, int] = {}
_ACTION_SIZE_AVG_VALUE: dict[int, int] = {
    100169: 1,  # 单格均价
    100170: 2,  # 两格均价
    100171: 3,  # 三格均价
    100172: 4,  # 四格均价
    100173: 6,  # 六格均价
}
_PUBLIC_INFO_INT_VALUE_FIELDS: dict[int, int] = {
    200009: 14,  # 所有藏品总占用格数
    200010: 14,  # 紫色品质藏品总占用格数
    200011: 14,  # 金色品质藏品总占用格数
    200012: 14,  # 红色品质藏品总占用格数
    200017: 7,  # 所有藏品件数
    200018: 7,  # 紫色品质藏品件数
    200019: 7,  # 金色品质藏品件数
    200020: 7,  # 红色品质藏品件数
    200044: 12,  # 本场拍卖类型藏品总价值
}
_PUBLIC_INFO_AVG_CELLS_IDS: frozenset[int] = frozenset({200013, 200014, 200015, 200016})
_PUBLIC_INFO_AVG_VALUE_IDS: frozenset[int] = frozenset({200035, 200036, 200037, 200038})
# "显示所有<品质>藏品的轮廓" — a COMPLETE per-quality outline reveal (200001=紫/200002=金/200003=红).
# Zero observed items means zero of that quality (a real exact-count signal, e.g. villa 4405 R2 紫=0),
# so keep the row (value=count=0) instead of dropping it like an empty/no-value public info.
_PUBLIC_INFO_BUCKET_OUTLINE_IDS: frozenset[int] = frozenset({200001, 200002, 200003})
_PUBLIC_INFO_EXACT_COUNT_IDS: frozenset[int] = frozenset({200017, 200018, 200019, 200020})
_PUBLIC_INFO_EXACT_UPDATE_PATHS: dict[int, tuple[str, ...]] = {
    200009: ("session", "warehouse_total_cells"),
    200010: ("bucket", "4", "total_cells"),
    200011: ("bucket", "5", "total_cells"),
    200012: ("bucket", "6", "total_cells"),
    200017: ("session", "total_item_count"),
    200018: ("bucket", "4", "count"),
    200019: ("bucket", "5", "count"),
    200020: ("bucket", "6", "count"),
}
_PUBLIC_INFO_AVG_CELLS_UPDATE_PATHS: dict[int, tuple[str, ...]] = {
    200013: ("bucket", "4", "avg_cells"),
    200014: ("session", "total_avg_cells"),
    200015: ("bucket", "5", "avg_cells"),
    200016: ("bucket", "6", "avg_cells"),
}
_PUBLIC_OUTLINE_QUALITY_BY_INFO_ID: dict[int, int] = {
    200001: 4,
    200002: 5,
    200003: 6,
}
_MAX_FRAME_BYTES = 4 * 1024 * 1024


Direction = Literal["SEND", "REV"]


@dataclass(frozen=True)
class FatbeansPacket:
    sort_id: int
    direction: Direction
    src: str
    dst: str
    capture_time: str
    data: bytes


@dataclass(frozen=True)
class FatbeansFrame:
    index: int
    direction: Direction
    sort_id: int
    capture_time: str
    raw: bytes

    @property
    def header_len(self) -> int:
        return 12 if self.direction == "SEND" else 16

    @property
    def body(self) -> bytes:
        return self.raw[self.header_len :]

    @property
    def packet_tag(self) -> int:
        if self.direction == "SEND":
            return int.from_bytes(self.raw[4:8], "big")
        return int.from_bytes(self.raw[8:12], "big")

    @property
    def message_id(self) -> int:
        if self.direction == "SEND":
            return int.from_bytes(self.raw[8:12], "big")
        return int.from_bytes(self.raw[12:16], "big")


@dataclass(frozen=True)
class FatbeansSendEvent:
    sort_id: int
    capture_time: str
    message_id: int
    session_id: str | None
    value: int | None

    @property
    def kind(self) -> str:
        if self.message_id == 0x0022:
            return "bid"
        if self.message_id == 0x0026:
            return "action"
        return "send"


@dataclass(frozen=True)
class FatbeansPlayerBid:
    player_id: int
    name: str
    hero_id: int | None
    values: tuple[int, ...]
    # Ranked-mode (56xx) pre-match rank snapshot, broadcast on the session-start
    # player block. Read-only: NOT consumed by any value/count/grid/bid inference.
    # Non-ranked maps omit these protobuf fields -> default None (zero old-map impact).
    rank_points: float | None = None  # f16 (float32): 赛前排位分
    rank_tier: int | None = None  # f17 (varint): 段位
    rank_group: int | None = None  # f18 (varint): 大段/分组

    @property
    def current_value(self) -> int | None:
        return self.values[-1] if self.values else None


@dataclass(frozen=True)
class FatbeansObservedItem:
    local_index: int | None
    runtime_id: int | None
    item_id: int | None
    quality: int | None
    value: int | None
    shape_code: int | None
    cells: int | None


@dataclass(frozen=True)
class FatbeansActionResult:
    action_id: int
    result: int | float | None
    result_field: int | None
    observed_items: tuple[FatbeansObservedItem, ...] = ()
    result_wire_type: str | None = None
    result_raw_hex: str | None = None


@dataclass(frozen=True)
class FatbeansSkillReveal:
    skill_id: int
    hero_id: int | None
    round_index: int | None
    result: int | float | None = None
    result_field: int | None = None
    observed_items: tuple[FatbeansObservedItem, ...] = ()


@dataclass(frozen=True)
class FatbeansInventoryItem:
    runtime_id: int
    item_id: int
    quality: int | None
    cells: int
    local_index: int | None = None


@dataclass(frozen=True)
class FatbeansPublicInfo:
    info_id: int
    map_id: int | None
    value: float | int
    value_field: int
    observed_items: tuple[FatbeansObservedItem, ...] = ()
    value_wire_type: str | None = None
    value_raw_hex: str | None = None
    value_endian: str | None = None
    value_conflicts: tuple[str, ...] = ()


@dataclass(frozen=True)
class FatbeansStatusEvent:
    sort_id: int
    capture_time: str
    player_id: int | None
    session_id: str | None


@dataclass(frozen=True)
class FatbeansRankScoreChange:
    """Ranked-mode (56xx) per-match rank-point change from the 0x2d top-level f7/f8.

    Read-only: NOT consumed by any value/count/grid/bid inference. ``slot`` records
    which top-level field carried the block (7 or 8) since alignment to the local
    player is left to consumers (cross-reference with the player's 0x21 ``rank_points``
    per the field-map report), not hard-coded here.
    """

    slot: int  # 7 or 8: which 0x2d top-level field carried this block
    before: float  # sub f1: 赛前排位分
    delta: float  # sub f2: 本局增减
    after: float  # sub f3: 赛后排位分


@dataclass(frozen=True)
class SettlementAuctionOwnerEvidence:
    """Privacy-safe owner metadata carried by a settlement ``S2C_45`` payload.

    The raw UID, display name and cosmetic identifiers are deliberately not retained.
    Aliases are process-scoped HMACs: stable enough to compare equal values during one
    parse/support run, but intentionally not correlatable across separate runs.
    """

    extension_present: bool
    validation_status: Literal["absent", "valid", "malformed_conflict"]
    fields_present: tuple[str, ...] = ()
    owner_identity_alias: str | None = None
    owner_display_alias: str | None = None
    metadata_shape: str | None = None
    conflicts: tuple[str, ...] = ()


_ABSENT_SETTLEMENT_AUCTION_OWNER_EVIDENCE = SettlementAuctionOwnerEvidence(
    extension_present=False,
    validation_status="absent",
)


@dataclass(frozen=True)
class FatbeansStateEvent:
    sort_id: int
    capture_time: str
    message_id: int
    session_id: str | None
    map_id: int | None
    round_index: int | None
    player_id: int | None = None
    bids: tuple[FatbeansPlayerBid, ...] = ()
    action_results: tuple[FatbeansActionResult, ...] = ()
    skill_reveals: tuple[FatbeansSkillReveal, ...] = ()
    public_infos: tuple[FatbeansPublicInfo, ...] = ()
    inventory_items: tuple[FatbeansInventoryItem, ...] = ()
    # ⚠️ 名字有误导: 这其实是游戏「本场福利」(亏损分成), 不是"亏损单位数"。来自结算帧 0x2d
    # 顶层 f5。机制: 别人拍下且赢家亏损时, 每个未拍下玩家收 赢家亏损×10% = 本场福利(加进资产)。
    # 仅"本机未拍下且赢家亏损"时存在, 否则 None(赢家/赢家盈利都没有)。实测2401赢家亏113204→f5=11320。
    # 保留旧名避免改动下游契约; monitor 透出 artifact 时改叫 settlement_welfare(本场福利)。
    settlement_loss_units: int | None = None
    # Ranked-mode (56xx) per-match rank-point changes from the settlement frame
    # (0x2d top-level f7/f8). Read-only; () on non-ranked maps and non-settlement
    # frames. NOT consumed by any value/count/grid/bid inference.
    rank_score_changes: tuple[FatbeansRankScoreChange, ...] = ()
    # 0x2D 结算帧顶层 protobuf walk 未消费的尾部字节数 (= len(body) - top_stop)。
    # 它只描述 parser 的字节级状态：非零不等于结算缺件，零也不保证结算完整。
    # 本字段不参与 runtime、engine 或 UI 的 completeness 判定。完整性由
    # settlement_incomplete、inventory/captured 件数与格数、存在时的 optional declared
    # 件数与格数，以及最终 quality count/cells closure 共同决定。
    # 字段继续保留在本地 artifact 和诊断中；None 表示非 0x2D 帧或未计算。
    settlement_parse_residual: int | None = None
    # S2C_45 / REV 0x2D 内层 f15..f19 的独立、脱敏 owner evidence。
    # PW1 只解析，不接 warehouse source、报价、上传、API 或 UI。
    settlement_auction_owner_evidence: SettlementAuctionOwnerEvidence = (
        _ABSENT_SETTLEMENT_AUCTION_OWNER_EVIDENCE
    )

    @property
    def round_no(self) -> int | None:
        """One-based in-game round number from the state payload."""
        return self.round_index


@dataclass(frozen=True)
class FatbeansCaptureEvents:
    packets: tuple[FatbeansPacket, ...]
    frames: tuple[FatbeansFrame, ...]
    sends: tuple[FatbeansSendEvent, ...]
    states: tuple[FatbeansStateEvent, ...]
    statuses: tuple[FatbeansStatusEvent, ...]


ProtoField = tuple[int, int, Any]

_SETTLEMENT_AUCTION_OWNER_FIELDS: dict[int, tuple[str, int]] = {
    15: ("send_auction_user_uid", 0),
    16: ("send_auction_user_name", 2),
    17: ("send_auction_user_head", 0),
    18: ("send_auction_head_box", 0),
    19: ("send_auction_user_title", 0),
}
_SETTLEMENT_AUCTION_OWNER_ALIAS_KEY = secrets.token_bytes(32)


def _direction(raw: Any) -> Direction:
    text = str(raw or "").upper()
    if text == "RECV":
        text = "REV"
    if text not in ("SEND", "REV"):
        raise ValueError(f"unsupported Fatbeans direction: {raw!r}")
    return text  # type: ignore[return-value]


def load_fatbeans_packets(path: str | Path) -> list[FatbeansPacket]:
    rows = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return load_fatbeans_packets_from_rows(rows)


def load_fatbeans_packets_from_payload(raw: str | bytes) -> list[FatbeansPacket]:
    """Load packets from a Fatbeans JSON export already held in memory."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8-sig")
    rows = json.loads(raw)
    return load_fatbeans_packets_from_rows(rows)


def load_fatbeans_packets_from_rows(
    rows: Sequence[Mapping[str, Any]],
) -> list[FatbeansPacket]:
    """Load packets from decoded Fatbeans export rows."""
    if not isinstance(rows, list):
        raise ValueError("Fatbeans export must be a JSON array")

    packets: list[FatbeansPacket] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        protocol = str(row.get("Protocol") or "").lower()
        if protocol and protocol != "tcp":
            continue
        payload = base64.b64decode(row.get("Data") or "")
        expected_len = int(row.get("DataLength") or 0)
        if len(payload) != expected_len:
            raise ValueError(
                f"DataLength mismatch at SortID={row.get('SortID')}: "
                f"decoded={len(payload)} expected={expected_len}"
            )
        packets.append(
            FatbeansPacket(
                sort_id=int(row["SortID"]),
                direction=_direction(row.get("Direct")),
                src=f"{row.get('SrcIP')}:{row.get('SrcPort')}",
                dst=f"{row.get('DstIP')}:{row.get('DstPort')}",
                capture_time=str(row.get("CaptureTime") or ""),
                data=payload,
            )
        )
    return sorted(packets, key=lambda packet: packet.sort_id)


def reconstruct_fatbeans_frames(
    packets: Sequence[FatbeansPacket],
    direction: Direction,
) -> list[FatbeansFrame]:
    frames: list[FatbeansFrame] = []
    packets_by_stream: dict[tuple[str, str], list[FatbeansPacket]] = {}
    for packet in packets:
        if packet.direction != direction:
            continue
        packets_by_stream.setdefault((packet.src, packet.dst), []).append(packet)

    min_frame_len = 12 if direction == "SEND" else 16
    stream_groups = sorted(
        packets_by_stream.values(),
        key=lambda group: min(packet.sort_id for packet in group),
    )
    for selected in stream_groups:
        selected.sort(key=lambda packet: packet.sort_id)
        stream = bytearray()
        segments: list[tuple[int, int, FatbeansPacket]] = []
        for packet in selected:
            start = len(stream)
            stream.extend(packet.data)
            segments.append((start, len(stream), packet))

        offset = 0
        segment_index = 0
        while offset < len(stream):
            if offset + 4 > len(stream):
                break
            frame_len = int.from_bytes(stream[offset : offset + 4], "big")
            if (
                frame_len < min_frame_len
                or frame_len > _MAX_FRAME_BYTES
                or offset + frame_len > len(stream)
            ):
                offset += 1
                continue
            while (
                segment_index + 1 < len(segments)
                and segments[segment_index][1] <= offset
            ):
                segment_index += 1
            packet = segments[segment_index][2]
            frames.append(
                FatbeansFrame(
                    index=len(frames),
                    direction=direction,
                    sort_id=packet.sort_id,
                    capture_time=packet.capture_time,
                    raw=bytes(stream[offset : offset + frame_len]),
                )
            )
            offset += frame_len
    return frames


def _read_varint(data: bytes, offset: int) -> tuple[int, int] | None:
    value = 0
    shift = 0
    pos = offset
    while pos < len(data) and shift <= 63:
        byte = data[pos]
        value |= (byte & 0x7F) << shift
        pos += 1
        if not byte & 0x80:
            return value, pos
        shift += 7
    return None


def _parse_fields(data: bytes, *, limit: int = 1024) -> list[ProtoField]:
    return _parse_fields_stop(data, limit=limit)[0]


def _parse_fields_stop(data: bytes, *, limit: int = 1024) -> tuple[list[ProtoField], int]:
    """Like _parse_fields, but also returns the offset where the walk STOPPED.

    The walk breaks silently on the first malformed byte; everything after is dropped. The stop
    offset lets settlement recovery slide ONLY that unconsumed remainder (2401 body-union fix) —
    fully-parsed regions are never re-scanned, so item-shaped records inside OTHER structures
    (2508 fixture: 4 phantom wrappers in healthy regions) cannot be swallowed.
    """
    fields: list[ProtoField] = []
    offset = 0
    good_end = 0  # end of the last fully-parsed field (stop offset for remainder recovery)
    while offset < len(data) and len(fields) < limit:
        key = _read_varint(data, offset)
        if key is None:
            break
        key_value, offset = key
        field_no = key_value >> 3
        wire_type = key_value & 0x07
        if field_no <= 0:
            break

        if wire_type == 0:
            parsed = _read_varint(data, offset)
            if parsed is None:
                break
            value, offset = parsed
            fields.append((field_no, wire_type, value))
        elif wire_type == 1:
            if offset + 8 > len(data):
                break
            fields.append((field_no, wire_type, data[offset : offset + 8]))
            offset += 8
        elif wire_type == 2:
            parsed_len = _read_varint(data, offset)
            if parsed_len is None:
                break
            size, offset = parsed_len
            if offset + size > len(data):
                break
            fields.append((field_no, wire_type, data[offset : offset + size]))
            offset += size
        elif wire_type == 5:
            if offset + 4 > len(data):
                break
            fields.append((field_no, wire_type, data[offset : offset + 4]))
            offset += 4
        else:
            break
        good_end = offset
    return fields, good_end


def _first(fields: Sequence[ProtoField], field_no: int) -> Any:
    for fn, _wt, value in fields:
        if fn == field_no:
            return value
    return None


def _all(fields: Sequence[ProtoField], field_no: int) -> list[Any]:
    return [value for fn, _wt, value in fields if fn == field_no]


def _text(value: Any) -> str | None:
    if not isinstance(value, bytes):
        return None
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if text and all(ord(char) >= 32 for char in text):
        return text
    return None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _settlement_owner_alias(kind: str, value: bytes) -> str:
    digest = hmac.new(
        _SETTLEMENT_AUCTION_OWNER_ALIAS_KEY,
        b"bidking-settlement-owner-pw1-v1\0"
        + kind.encode("ascii")
        + b"\0"
        + value,
        hashlib.sha256,
    ).hexdigest()
    return f"pw1-{kind}-{digest[:24]}"


def _parse_settlement_auction_owner_evidence(
    fields: Sequence[ProtoField],
) -> SettlementAuctionOwnerEvidence:
    """Parse the complete f15..f19 owner block without retaining identity plaintext."""

    values_by_field: dict[int, list[tuple[int, Any]]] = {
        field_no: [] for field_no in _SETTLEMENT_AUCTION_OWNER_FIELDS
    }
    for field_no, wire_type, value in fields:
        if field_no in values_by_field:
            values_by_field[field_no].append((wire_type, value))

    present = tuple(
        field_name
        for field_no, (field_name, _wire_type) in _SETTLEMENT_AUCTION_OWNER_FIELDS.items()
        if values_by_field[field_no]
    )
    if not present:
        return _ABSENT_SETTLEMENT_AUCTION_OWNER_EVIDENCE

    conflicts: list[str] = []
    if len(present) != len(_SETTLEMENT_AUCTION_OWNER_FIELDS):
        conflicts.append("partial_extension")

    canonical: dict[int, Any] = {}
    for field_no, (_field_name, expected_wire_type) in (
        _SETTLEMENT_AUCTION_OWNER_FIELDS.items()
    ):
        entries = values_by_field[field_no]
        if not entries:
            continue
        if any(wire_type != expected_wire_type for wire_type, _value in entries):
            conflicts.append("wrong_wire_type")
        correctly_wired = [
            value for wire_type, value in entries if wire_type == expected_wire_type
        ]
        if not correctly_wired:
            continue
        try:
            distinct_values = set(correctly_wired)
        except TypeError:
            conflicts.append("invalid_value")
            continue
        if len(distinct_values) != 1:
            conflicts.append("duplicate_conflict")
            continue
        value = correctly_wired[0]
        if field_no == 16:
            text = _text(value)
            if text is None or not text.strip():
                conflicts.append("invalid_text")
                continue
        elif not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            conflicts.append("invalid_value")
            continue
        canonical[field_no] = value

    if len(canonical) != len(_SETTLEMENT_AUCTION_OWNER_FIELDS):
        conflicts.append("invalid_or_missing_value")
    if conflicts:
        return SettlementAuctionOwnerEvidence(
            extension_present=True,
            validation_status="malformed_conflict",
            fields_present=present,
            conflicts=tuple(dict.fromkeys(conflicts)),
        )

    owner_name = canonical[16]
    assert isinstance(owner_name, bytes)
    return SettlementAuctionOwnerEvidence(
        extension_present=True,
        validation_status="valid",
        fields_present=present,
        owner_identity_alias=_settlement_owner_alias(
            "uid", str(canonical[15]).encode("ascii")
        ),
        owner_display_alias=_settlement_owner_alias("name", owner_name),
        metadata_shape="uid_name_head_headbox_title",
    )


def _bind_settlement_owner_evidence_to_current_session(
    evidence: SettlementAuctionOwnerEvidence,
    *,
    evidence_session_id: str | None,
    current_session_id: str | None,
    binding_conflict: str | None = None,
) -> SettlementAuctionOwnerEvidence:
    """Fail closed unless settlement and active parser context name the same session."""

    if not evidence.extension_present:
        return evidence
    session_conflict: str | None = None
    if binding_conflict is not None:
        session_conflict = binding_conflict
    elif (
        not isinstance(evidence_session_id, str)
        or not evidence_session_id
        or not isinstance(current_session_id, str)
        or not current_session_id
    ):
        session_conflict = "session_unverified"
    elif evidence_session_id != current_session_id:
        session_conflict = "session_mismatch"
    if session_conflict is None:
        return evidence
    return replace(
        evidence,
        validation_status="malformed_conflict",
        owner_identity_alias=None,
        owner_display_alias=None,
        metadata_shape=None,
        conflicts=tuple(dict.fromkeys((*evidence.conflicts, session_conflict))),
    )


def _fixed32_float(value: bytes) -> float:
    return struct.unpack("<f", value)[0]


def _parse_send_event(frame: FatbeansFrame) -> FatbeansSendEvent | None:
    if frame.direction != "SEND" or frame.message_id not in (0x0022, 0x0026):
        return None
    fields = _parse_fields(frame.body)
    return FatbeansSendEvent(
        sort_id=frame.sort_id,
        capture_time=frame.capture_time,
        message_id=frame.message_id,
        session_id=_text(_first(fields, 2)),
        value=_int(_first(fields, 3)),
    )


def _parse_status_event(frame: FatbeansFrame) -> FatbeansStatusEvent | None:
    if frame.direction != "REV" or frame.packet_tag != 0 or frame.message_id != 0x0077:
        return None
    fields = _parse_fields(frame.body)
    return FatbeansStatusEvent(
        sort_id=frame.sort_id,
        capture_time=frame.capture_time,
        player_id=_int(_first(fields, 1)),
        session_id=_text(_first(fields, 2)),
    )


def _parse_player_bid(block: bytes) -> FatbeansPlayerBid | None:
    fields = _parse_fields(block)
    player_id = _int(_first(fields, 1))
    name = _text(_first(fields, 2))
    if player_id is None or name is None:
        return None
    values: list[int] = []
    for raw_bid in _all(fields, 5):
        if not isinstance(raw_bid, bytes):
            continue
        bid_fields = _parse_fields(raw_bid)
        bid = _int(_first(bid_fields, 2))
        if bid is not None:
            values.append(bid)
    # Ranked-mode (56xx) pre-match rank snapshot (read-only; absent on old maps).
    raw_rank_points = _first(fields, 16)
    rank_points = (
        _fixed32_float(raw_rank_points)
        if isinstance(raw_rank_points, bytes) and len(raw_rank_points) == 4
        else None
    )
    return FatbeansPlayerBid(
        player_id=player_id,
        name=name,
        hero_id=_int(_first(fields, 3)),
        values=tuple(values),
        rank_points=rank_points,
        rank_tier=_int(_first(fields, 17)),
        rank_group=_int(_first(fields, 18)),
    )


def _parse_action_result(block: bytes) -> FatbeansActionResult | None:
    fields = _parse_fields(block)
    action_id = _int(_first(fields, 4))
    if action_id is None:
        return None
    observed_items = tuple(
        item
        for raw in _all(fields, 8)
        if isinstance(raw, bytes)
        for item in (_parse_observed_item(raw),)
        if item is not None
    )
    for result_field in (14, 12, 7):
        raw_result = _first(fields, result_field)
        result = _int(raw_result)
        if result is not None:
            return FatbeansActionResult(
                action_id=action_id,
                result=result,
                result_field=result_field,
                observed_items=observed_items,
                result_wire_type="varint",
            )
    for result_field in (11, 9, 10):
        raw_result = _first(fields, result_field)
        if isinstance(raw_result, bytes) and len(raw_result) == 4:
            return FatbeansActionResult(
                action_id=action_id,
                result=_fixed32_float(raw_result),
                result_field=result_field,
                observed_items=observed_items,
                result_wire_type="float32",
                result_raw_hex=raw_result.hex(),
            )
        if isinstance(raw_result, int):
            return FatbeansActionResult(
                action_id=action_id,
                result=float(raw_result),
                result_field=result_field,
                observed_items=observed_items,
                result_wire_type="varint_as_float",
            )
    if observed_items:
        return FatbeansActionResult(
            action_id=action_id,
            result=None,
            result_field=None,
            observed_items=observed_items,
        )
    return None


def _parse_direct_action_response(
    frame: FatbeansFrame,
    *,
    allow_correlated_tag: bool = False,
) -> FatbeansActionResult | None:
    """Parse ``REV msg=0x0027`` direct tool/action responses.

    These frames carry only the action result payload. They do not repeat the
    session/map/round fields, so callers must attach the current auction
    context before converting them into state events.
    """
    if (
        frame.direction != "REV"
        or (
            frame.packet_tag != 0
            and not (
                allow_correlated_tag
                and 0 < frame.packet_tag <= 0xFFFFFFFF
            )
        )
        or frame.message_id != 0x0027
    ):
        return None
    fields = _parse_fields(frame.body)
    payload = _first(fields, 2)
    if not isinstance(payload, bytes):
        return None
    result = _parse_action_result(payload)
    if result is not None:
        return result
    if os.environ.get("BIDKING_DISABLE_EMPTY_ACTION_ACK_ZERO_RECOVERY") == "1":
        return None
    # A zero-valued numeric tool response keeps the action id but omits every result
    # field. Preserve that structurally valid empty ACK as a result-less row. The
    # monitor only turns it into zero when the same session has a matching outbound
    # numeric action, so malformed/unsolicited 0x0027 frames still cannot invent
    # evidence. This also makes the ACK itself the later server state instead of
    # delaying zero recovery until the next auction update.
    payload_fields = _parse_fields(payload)
    action_id = _int(_first(payload_fields, 4))
    if action_id is None:
        return None
    return FatbeansActionResult(
        action_id=action_id,
        result=None,
        result_field=None,
        observed_items=(),
    )


def _parse_observed_item(block: bytes) -> FatbeansObservedItem | None:
    fields = _parse_fields(block)
    local_index = _int(_first(fields, 1))
    runtime_id = _int(_first(fields, 2))
    quality = _int(_first(fields, 6))
    if runtime_id is None and quality is None:
        return None
    return FatbeansObservedItem(
        local_index=local_index,
        runtime_id=runtime_id,
        item_id=_int(_first(fields, 3)),
        quality=quality,
        value=_int(_first(fields, 7)),
        shape_code=_int(_first(fields, 4)),
        cells=_int(_first(fields, 8)),
    )


def _shape_cells(shape_code: int | None) -> int | None:
    if shape_code is None:
        return None
    width = shape_code // 10
    height = shape_code % 10
    if width <= 0 or height <= 0:
        return None
    return width * height


def _observed_item_cells(item: FatbeansObservedItem) -> int | None:
    return item.cells or _shape_cells(item.shape_code)


def _parse_skill_reveal(block: bytes) -> FatbeansSkillReveal | None:
    fields = _parse_fields(block)
    skill_id = _int(_first(fields, 1))
    if skill_id is None:
        return None
    observed_items = tuple(
        item
        for raw in _all(fields, 8)
        if isinstance(raw, bytes)
        for item in (_parse_observed_item(raw),)
        if item is not None
    )
    result: int | float | None = None
    result_field: int | None = None
    for candidate_field in (14, 12, 7):
        parsed = _int(_first(fields, candidate_field))
        if parsed is not None:
            result = parsed
            result_field = candidate_field
            break
    if result is None:
        for candidate_field in (11, 9, 10):
            raw_result = _first(fields, candidate_field)
            if isinstance(raw_result, bytes) and len(raw_result) == 4:
                result = _fixed32_float(raw_result)
                result_field = candidate_field
                break
            if isinstance(raw_result, int):
                result = float(raw_result)
                result_field = candidate_field
                break
    if not observed_items and result is None:
        if skill_id in _AHMAD_SKILL_AVG_CELLS or skill_id in _AHMAD_SKILL_COUNT:
            result = 0.0
            result_field = 0
        else:
            return None
    return FatbeansSkillReveal(
        skill_id=skill_id,
        hero_id=_int(_first(fields, 2)),
        round_index=_int(_first(fields, 6)),
        result=result,
        result_field=result_field,
        observed_items=observed_items,
    )


def _parse_public_info(block: bytes) -> FatbeansPublicInfo | None:
    fields = _parse_fields(block)
    info_id = _int(_first(fields, 1))
    if info_id is None:
        return None
    observed_items = tuple(
        item
        for raw in _all(fields, 8)
        if isinstance(raw, bytes)
        for item in (_parse_observed_item(raw),)
        if item is not None
    )
    int_value_field = _PUBLIC_INFO_INT_VALUE_FIELDS.get(info_id)
    if int_value_field is not None:
        raw = _first(fields, int_value_field)
        if isinstance(raw, int):
            return FatbeansPublicInfo(
                info_id=info_id,
                map_id=_int(_first(fields, 3)),
                value=raw,
                value_field=int_value_field,
                observed_items=observed_items,
            )
        # Zero exact int metrics are implied when the value field is absent.
        return FatbeansPublicInfo(
            info_id=info_id,
            map_id=_int(_first(fields, 3)),
            value=0,
            value_field=int_value_field,
            observed_items=observed_items,
        )
    value_entries = [
        (field_no, wire_type, raw)
        for field_no, wire_type, raw in fields
        if field_no in {11, 9}
    ]
    if value_entries:
        value_field = 11 if any(row[0] == 11 for row in value_entries) else 9
        selected = [row for row in value_entries if row[0] == value_field]
        conflicts: list[str] = []
        if any(row[0] != value_field for row in value_entries):
            conflicts.append("multiple_value_fields")

        def fingerprint(row: tuple[int, int, Any]) -> tuple[int, bytes | int | str]:
            _field_no, wire_type, raw = row
            if isinstance(raw, bytes):
                return wire_type, raw
            if isinstance(raw, int):
                return wire_type, raw
            return wire_type, type(raw).__name__

        if len({fingerprint(row) for row in selected}) > 1:
            conflicts.append("duplicate_value_conflict")
        _field_no, wire_type, raw = selected[0]
        if wire_type == 5 and isinstance(raw, bytes) and len(raw) == 4:
            value: float | int = _fixed32_float(raw)
            value_wire_type = "float32"
            value_raw_hex = raw.hex()
            value_endian = "little"
        elif wire_type == 0 and isinstance(raw, int):
            value = raw
            value_wire_type = "varint"
            value_raw_hex = None
            value_endian = None
        else:
            value = 0.0
            value_wire_type = (
                "float64"
                if wire_type == 1 and isinstance(raw, bytes) and len(raw) == 8
                else "unknown"
            )
            value_raw_hex = raw.hex() if isinstance(raw, bytes) else None
            value_endian = "little" if isinstance(raw, bytes) else None
            conflicts.append("invalid_value_wire")
        return FatbeansPublicInfo(
            info_id=info_id,
            map_id=_int(_first(fields, 3)),
            value=value,
            value_field=value_field,
            observed_items=observed_items,
            value_wire_type=value_wire_type,
            value_raw_hex=value_raw_hex,
            value_endian=value_endian,
            value_conflicts=tuple(dict.fromkeys(conflicts)),
        )
    if observed_items:
        return FatbeansPublicInfo(
            info_id=info_id,
            map_id=_int(_first(fields, 3)),
            value=len(observed_items),
            value_field=8,
            observed_items=observed_items,
        )
    if info_id in _PUBLIC_INFO_BUCKET_OUTLINE_IDS:
        # Complete "show all <quality> outlines" reveal with zero items -> 0 of that quality.
        # Keep it (value=0) so the engine locks the count to 0 instead of dropping the signal.
        return FatbeansPublicInfo(
            info_id=info_id,
            map_id=_int(_first(fields, 3)),
            value=0,
            value_field=8,
            observed_items=(),
        )
    if info_id in _PUBLIC_INFO_AVG_CELLS_IDS:
        # Zero avg cells is implied when the float/int value field is absent.
        return FatbeansPublicInfo(
            info_id=info_id,
            map_id=_int(_first(fields, 3)),
            value=0.0,
            value_field=0,
            observed_items=observed_items,
        )
    if info_id in _PUBLIC_INFO_AVG_VALUE_IDS:
        # Zero avg value is implied when the float/int value field is absent.
        return FatbeansPublicInfo(
            info_id=info_id,
            map_id=_int(_first(fields, 3)),
            value=0.0,
            value_field=0,
            observed_items=observed_items,
        )
    return None


# New-map (villa 24xx / shipwreck 25xx, game ≈2026-06-28) 0x2D settlement bodies
# desync mid-frame: _parse_fields hits an invalid wire type partway through the
# inner inventory block (f2.f4) and truncates, so every item wrapper after the
# desync point is dropped. On 2508 (repro 2026-07-03) that lost the 6 tail items
# — incl. the two most valuable reds (跑车 293k / 黄唇鱼鱼胶 193k) — settlement
# read 424,916 vs the game's 952,712. Tail recovery slides the raw block for the
# length-delimited item wrappers the structured walk missed and unions them
# (see _recover_truncated_tail_items). Additive by construction; 0-regression on
# classic/56xx frames (they parse fully, so recovery finds nothing new).
_SETTLEMENT_TAIL_RECOVERY_DISABLE_ENV = "BIDKING_DISABLE_SETTLEMENT_TAIL_RECOVERY"
# Item wrapper byte bounds: a full item needs runtime_id+item_id (~15B floor);
# the largest observed collectible (跑车, 15 cells) packs into 111B. 256B keeps
# margin to ~30 cells while refusing to swallow a multi-item region as one match.
_MIN_ITEM_WRAPPER_BYTES = 6
_MAX_ITEM_WRAPPER_BYTES = 256


def _settlement_tail_recovery_enabled() -> bool:
    return os.environ.get(_SETTLEMENT_TAIL_RECOVERY_DISABLE_ENV) != "1"


# 2026-07-11 (2401 结算 366,842 vs 565,606): settlement body union — read ALL payload f4 blocks (not
# just the first) and slide the WHOLE body for stranded item wrappers (variant-B tail). Disable to
# restore the first-f4-only + in-block-recovery behavior.
_SETTLEMENT_BODY_UNION_DISABLE_ENV = "BIDKING_DISABLE_SETTLEMENT_BODY_UNION"


def _settlement_body_union_enabled() -> bool:
    return os.environ.get(_SETTLEMENT_BODY_UNION_DISABLE_ENV) != "1"


def _parse_inventory_items(
    block: Any, *, recover: bool = True
) -> tuple[FatbeansInventoryItem, ...]:
    if not isinstance(block, bytes):
        return ()
    items: list[FatbeansInventoryItem] = []
    seen: set[tuple[int, int]] = set()

    def is_inventory_item(ints: dict[int, list[int]]) -> bool:
        runtime_id = ints.get(1, [None])[0]
        item_id = ints.get(2, [None])[0]
        return (
            isinstance(runtime_id, int)
            and isinstance(item_id, int)
            and 1_000_000 <= item_id < 2_000_000
        )

    def direct_item_child_count(
        byte_fields: dict[int, list[bytes]],
    ) -> int:
        count = 0
        for values in byte_fields.values():
            for value in values:
                child_ints: dict[int, list[int]] = {}
                for field_no, _wire_type, child_value in _parse_fields(value):
                    if isinstance(child_value, int):
                        child_ints.setdefault(field_no, []).append(child_value)
                if is_inventory_item(child_ints):
                    count += 1
        return count

    def walk(
        data: bytes,
        depth: int,
        inherited_local_index: int | None,
    ) -> None:
        if depth > 8:
            return
        fields = _parse_fields(data)
        ints: dict[int, list[int]] = {}
        byte_fields: dict[int, list[bytes]] = {}
        for field_no, _wire_type, value in fields:
            if isinstance(value, int):
                ints.setdefault(field_no, []).append(value)
            elif isinstance(value, bytes):
                byte_fields.setdefault(field_no, []).append(value)

        runtime_id = ints.get(1, [None])[0]
        item_id = ints.get(2, [None])[0]
        if is_inventory_item(ints):
            key = (runtime_id, item_id)
            if key not in seen:
                seen.add(key)
                items.append(
                    FatbeansInventoryItem(
                        runtime_id=runtime_id,
                        item_id=item_id,
                        quality=ints.get(9, [None])[0],
                        cells=len(byte_fields.get(4, ())),
                        local_index=inherited_local_index,
                    )
                )
            return

        local_index_for_children = inherited_local_index
        if direct_item_child_count(byte_fields) == 1:
            raw_local_index = ints.get(1, [None])[0]
            local_index_for_children = (
                raw_local_index
                if isinstance(raw_local_index, int) and 0 <= raw_local_index < 1000
                else 0
            )

        for values in byte_fields.values():
            for value in values:
                walk(value, depth + 1, local_index_for_children)

    walk(block, 0, None)
    if recover and _settlement_tail_recovery_enabled():
        _recover_truncated_tail_items(block, seen, items)
    return tuple(items)


def _recover_truncated_tail_items(
    block: bytes,
    seen: set[tuple[int, int]],
    items: list[FatbeansInventoryItem],
) -> None:
    """Union item wrappers the structured walk missed after a mid-frame desync.

    Slides the raw block; at each length-delimited field whose body parses as a
    real item (runtime_id + item_id in [1e6, 2e6)) with a wired quality, unions
    it if unseen. The clean prefix is re-scanned harmlessly (already in `seen`).
    quality∈[1,6] guards against partial/false wrappers in the desynced region;
    recover=False on the inner parse bounds recursion to one level.
    """
    offset = 0
    length = len(block)
    while offset < length:
        parsed_key = _read_varint(block, offset)
        if parsed_key is None:
            offset += 1
            continue
        key_value, cursor = parsed_key
        wire_type = key_value & 0x07
        field_no = key_value >> 3
        if wire_type == 2 and 0 < field_no < 32:
            parsed_len = _read_varint(block, cursor)
            if parsed_len is not None:
                size, body_start = parsed_len
                if (
                    0 < size <= length - body_start
                    and _MIN_ITEM_WRAPPER_BYTES <= size <= _MAX_ITEM_WRAPPER_BYTES
                ):
                    recovered = _parse_inventory_items(
                        block[body_start : body_start + size], recover=False
                    )
                    if recovered:
                        for item in recovered:
                            key = (item.runtime_id, item.item_id)
                            if (
                                key not in seen
                                and isinstance(item.quality, int)
                                and 1 <= item.quality <= 6
                            ):
                                seen.add(key)
                                items.append(item)
                        offset = body_start + size
                        continue
        offset += 1


def _state_payload(frame: FatbeansFrame) -> tuple[bytes | None, int | None]:
    fields = _parse_fields(frame.body)
    if frame.direction != "REV" or frame.packet_tag != 0:
        return None, None
    if frame.message_id in (0x0021, 0x0025):
        payload = _first(fields, 1)
        return (payload if isinstance(payload, bytes) else None), None
    if frame.message_id == 0x002D:
        payload = _first(fields, 2)  # f2 = 结算 inventory payload
        # f5 = 游戏「本场福利」(亏损分成, 非"亏损单位"; 见 FatbeansStateEvent.settlement_loss_units
        # 注释)。别人拍下且赢家亏损时 = 赢家亏损×10%, 加进资产; 否则该字段缺省(None)。
        return (
            payload if isinstance(payload, bytes) else None,
            _int(_first(fields, 5)),
        )
    return None, None


def _parse_rank_score_changes(
    frame: FatbeansFrame,
) -> tuple[FatbeansRankScoreChange, ...]:
    """Read ranked-mode per-match rank-point blocks from the 0x2d body top-level.

    These f7/f8 blocks (each a message with 3 float32 subfields: before/delta/after)
    only appear on 56xx ranked settlement frames; old maps omit them -> empty tuple.
    Read-only, additive: not consumed by any inference path.
    """
    if frame.direction != "REV" or frame.packet_tag != 0 or frame.message_id != 0x002D:
        return ()
    fields = _parse_fields(frame.body)
    changes: list[FatbeansRankScoreChange] = []
    for slot in (7, 8):
        block = _first(fields, slot)
        if not isinstance(block, bytes):
            continue
        sub = _parse_fields(block)
        raw_before = _first(sub, 1)
        raw_delta = _first(sub, 2)
        raw_after = _first(sub, 3)
        # before/after are always wired; delta (sub f2) is the protobuf default and
        # is omitted entirely on a zero-change match (observed: 5601 no-change frames
        # carry only f1/f3), so treat a missing f2 as 0.0 rather than dropping the block.
        if not (
            isinstance(raw_before, bytes) and len(raw_before) == 4
            and isinstance(raw_after, bytes) and len(raw_after) == 4
        ):
            continue
        delta = (
            _fixed32_float(raw_delta)
            if isinstance(raw_delta, bytes) and len(raw_delta) == 4
            else 0.0
        )
        changes.append(
            FatbeansRankScoreChange(
                slot=slot,
                before=_fixed32_float(raw_before),
                delta=delta,
                after=_fixed32_float(raw_after),
            )
        )
    return tuple(changes)


def _merge_settlement_frame_inventory(
    frame: FatbeansFrame,
    primary: tuple[FatbeansInventoryItem, ...],
) -> tuple[FatbeansInventoryItem, ...]:
    """Merge warehouse rows scattered across a ranked 0x002D frame body.

    Classic maps pack the full settlement list in the inner payload field f4
    (reachable via top-level f2). Ranked 56xx captures (5604 live repro
    2026-06-28) also attach one item each in per-player top-level f3 blocks on
    the same frame; reading only f2.f4 under-counts (22/33) and drops ~300k of
    catalog value. Union by (runtime_id, item_id); leave primary-only paths
    unchanged when no extra rows exist.
    """
    if frame.message_id != 0x002D:
        return primary
    merged: dict[tuple[int, int], FatbeansInventoryItem] = {
        (item.runtime_id, item.item_id): item for item in primary
    }
    before = len(merged)
    top_fields, top_stop = _parse_fields_stop(frame.body)
    for field_no, _wire_type, block in top_fields:
        if field_no != 3 or not isinstance(block, bytes):
            continue
        for item in _parse_inventory_items(block):
            merged[(item.runtime_id, item.item_id)] = item
    # Variant-B desync (the 2403 observation of 2026-07-02, reproduced byte-level on 2401 2026-07-11):
    # the TOP-LEVEL walk itself can break mid-body (2401: BAD tag at offset 5826 of 8118), stranding
    # item wrappers in the UNCONSUMED remainder — the payload region lost after a mid-f2 desync plus
    # everything after the break (per-player blocks the f3 merge above never reaches). 2401: complete
    # 8118B frame, ahmed declared 48, structured parse 28 items / 366,842; remainder slide recovers the
    # other 20 -> 48 items whose catalog sum equals the game's 战利品价格 565,606 byte-exact. Sliding
    # is restricted to the REMAINDERS the walks gave up on (top-level tail + each f2 payload's own
    # unconsumed tail): fully-parsed regions are never re-scanned, so item-shaped records inside other
    # healthy structures are not swallowed (2508 variant-A fixture carries 4 such phantoms — a
    # whole-body slide wrongly picked them up; the remainder slide leaves them alone, verified 47/47).
    # 12 healthy same-session frames: remainders empty -> no-op. 3a63e60 fixed variant A (desync
    # INSIDE the first f4 block) only; this closes variant B. Kill: BIDKING_DISABLE_SETTLEMENT_BODY_UNION=1.
    if _settlement_body_union_enabled() and _settlement_tail_recovery_enabled():
        remainders: list[bytes] = []
        if 0 < top_stop < len(frame.body):
            remainders.append(frame.body[top_stop:])
        for field_no, _wire_type, block in top_fields:
            if field_no == 2 and isinstance(block, bytes):
                _sub_fields, sub_stop = _parse_fields_stop(block)
                if 0 < sub_stop < len(block):
                    remainders.append(block[sub_stop:])
        if remainders:
            seen = set(merged.keys())
            extra: list[FatbeansInventoryItem] = []
            for chunk in remainders:
                _recover_truncated_tail_items(chunk, seen, extra)
            for item in extra:
                merged.setdefault((item.runtime_id, item.item_id), item)
    if len(merged) == before:
        return primary
    return tuple(merged.values())


def _reconcile_declared_settlement_inventory(
    frame: FatbeansFrame,
    current: tuple[FatbeansInventoryItem, ...],
    declared_count: int | None,
) -> tuple[FatbeansInventoryItem, ...]:
    """Use an independent exact count to admit whole-body recovery candidates.

    Some 56xx frames contain a complete item wrapper that crosses the declared
    boundary of the nested settlement payload. Remainder-only recovery cannot
    see the full wrapper, while a blind whole-body scan is unsafe: the 2508
    fixture contains four item-shaped non-settlement records. Only accept the
    broader scan when the earlier ``库存清点`` result supplies an exact count,
    the scan lands on that count exactly, and it is a strict superset of the
    conservative parse.
    """
    if (
        frame.message_id != 0x002D
        or not _settlement_body_union_enabled()
        or not _settlement_tail_recovery_enabled()
        or not isinstance(declared_count, int)
        or isinstance(declared_count, bool)
        or declared_count <= len(current)
    ):
        return current

    candidates = _parse_inventory_items(frame.body)
    candidate_by_key = {
        (item.runtime_id, item.item_id): item for item in candidates
    }
    current_keys = {(item.runtime_id, item.item_id) for item in current}
    if len(candidate_by_key) != declared_count or not current_keys.issubset(
        candidate_by_key
    ):
        return current

    extras = [
        item
        for key, item in candidate_by_key.items()
        if key not in current_keys
    ]
    if not extras or any(
        not isinstance(item.quality, int)
        or not 1 <= item.quality <= 6
        or item.cells <= 0
        for item in extras
    ):
        return current
    return (*current, *extras)


LOG_SETTLEMENT_FRAMES = False  # live monitor enables safe per-0x2D numeric diagnostics
LOG_RAW_SETTLEMENT_FRAMES = False
RAW_SETTLEMENT_FORENSICS_ENV = "BIDKING_ENABLE_RAW_SETTLEMENT_FORENSICS"


def configure_live_settlement_diagnostics() -> bool:
    """Enable safe live statistics and explicitly gate raw settlement forensics.

    Raw frame bodies can contain player or application data.  They stay disabled
    unless an operator deliberately opts in for local reverse engineering; normal
    portable startup and support diagnostics only receive numeric statistics.
    """

    global LOG_SETTLEMENT_FRAMES, LOG_RAW_SETTLEMENT_FRAMES
    LOG_SETTLEMENT_FRAMES = True
    LOG_RAW_SETTLEMENT_FRAMES = os.environ.get(RAW_SETTLEMENT_FORENSICS_ENV) == "1"
    return LOG_RAW_SETTLEMENT_FRAMES


def _log_settlement_frame(
    frame: FatbeansFrame, payload: bytes | None, f4_items: int, merged_items: int
) -> None:
    """One structured line per 0x002D settlement frame to stdout (-> monitor.stdout.log).

    Surfaces under-counts in real time: a settlement frame with body=8009B/items=16
    vs a healthy body=~9000B/items=33 is instantly visible as a capture/truncation
    symptom instead of a silent value gap (the 5606 class). Gated by
    LOG_SETTLEMENT_FRAMES so batch/eval/CI (which import this engine module) stay
    quiet; only the live monitor opts in.
    """
    if not LOG_SETTLEMENT_FRAMES:
        return
    try:
        print(
            f"[settle] sort={frame.sort_id} body={len(frame.body)}B "
            f"payload={len(payload or b'')}B f4_items={f4_items} "
            f"merged_items={merged_items} f3_extra={max(0, merged_items - f4_items)}",
            flush=True,
        )
    except Exception:  # noqa: BLE001 — diag logging must never break parse
        pass
    if not LOG_RAW_SETTLEMENT_FRAMES:
        return
    # Explicit local raw-forensics only.  This line contains the complete application
    # body and is never enabled by normal portable startup or admitted to support reports.
    try:
        _body = frame.body or b""
        _top = [
            (fn, wt, (len(x) if isinstance(x, (bytes, bytearray)) else x))
            for fn, wt, x in _parse_fields(_body)
        ]
        print(
            f"[settle-raw] sort={frame.sort_id} mid=0x{frame.message_id:04x} "
            f"body={len(_body)}B top_fields={_top} "
            f"bodyb64={base64.b64encode(_body).decode('ascii')}",
            flush=True,
        )
    except Exception:  # noqa: BLE001 — 诊断绝不能打断解析
        pass


def _parse_state_event(frame: FatbeansFrame) -> FatbeansStateEvent | None:
    payload, loss_units = _state_payload(frame)
    if payload is None:
        return None
    fields = _parse_fields(payload)
    owner_evidence = (
        _parse_settlement_auction_owner_evidence(fields)
        if frame.message_id == 0x002D
        else _ABSENT_SETTLEMENT_AUCTION_OWNER_EVIDENCE
    )
    bids = tuple(
        parsed
        for raw in _all(fields, 5)
        if isinstance(raw, bytes)
        for parsed in (_parse_player_bid(raw),)
        if parsed is not None
    )
    actions = tuple(
        parsed
        for raw in _all(fields, 8)
        if isinstance(raw, bytes)
        for parsed in (_parse_action_result(raw),)
        if parsed is not None
    )
    skill_reveals = tuple(
        parsed
        for raw in _all(fields, 6)
        if isinstance(raw, bytes)
        for parsed in (_parse_skill_reveal(raw),)
        if parsed is not None
    )
    public_infos = tuple(
        parsed
        for raw in _all(fields, 7)
        if isinstance(raw, bytes)
        for parsed in (_parse_public_info(raw),)
        if parsed is not None
    )
    primary_inventory = _parse_inventory_items(_first(fields, 4))
    inventory_items = _merge_settlement_frame_inventory(frame, primary_inventory)
    # 顶层 walk 未消费的尾部字节诊断；非零值不代表结算缺件，也不参与完整性判定。
    # 见 FatbeansStateEvent.settlement_parse_residual。
    residual: int | None = None
    if frame.message_id == 0x002D:
        _body = frame.body
        _, _stop = _parse_fields_stop(_body)
        residual = max(0, len(_body) - int(_stop))
    if LOG_SETTLEMENT_FRAMES and frame.message_id == 0x002D:
        _log_settlement_frame(frame, payload, len(primary_inventory), len(inventory_items))
    return FatbeansStateEvent(
        sort_id=frame.sort_id,
        capture_time=frame.capture_time,
        message_id=frame.message_id,
        session_id=_text(_first(fields, 1)),
        map_id=_int(_first(fields, 2)),
        round_index=_int(_first(fields, 3)),
        bids=bids,
        action_results=actions,
        skill_reveals=skill_reveals,
        public_infos=public_infos,
        inventory_items=inventory_items,
        settlement_loss_units=loss_units,
        rank_score_changes=_parse_rank_score_changes(frame),
        settlement_parse_residual=residual,
        settlement_auction_owner_evidence=owner_evidence,
    )


def parse_fatbeans_capture(
    path: str | Path,
    *,
    local_player_id_hint: int | None = None,
) -> FatbeansCaptureEvents:
    packets = load_fatbeans_packets(path)
    return parse_fatbeans_packets(
        packets,
        local_player_id_hint=local_player_id_hint,
    )


def parse_fatbeans_capture_payload(
    raw: str | bytes,
    *,
    local_player_id_hint: int | None = None,
) -> FatbeansCaptureEvents:
    """Parse a Fatbeans JSON export already held in memory."""
    packets = load_fatbeans_packets_from_payload(raw)
    return parse_fatbeans_packets(
        packets,
        local_player_id_hint=local_player_id_hint,
    )


def parse_fatbeans_packets(
    packets: Sequence[FatbeansPacket],
    *,
    local_player_id_hint: int | None = None,
) -> FatbeansCaptureEvents:
    """Parse loaded Fatbeans packets into normalized capture events."""
    frames = [
        *reconstruct_fatbeans_frames(packets, "SEND"),
        *reconstruct_fatbeans_frames(packets, "REV"),
    ]
    frames.sort(key=lambda frame: (frame.sort_id, frame.index))
    sends = tuple(
        event
        for frame in frames
        for event in (_parse_send_event(frame),)
        if event is not None
    )
    statuses = tuple(
        event
        for frame in frames
        for event in (_parse_status_event(frame),)
        if event is not None
    )
    states_list: list[FatbeansStateEvent] = []
    current_session_id: str | None = None
    confirmed_owner_evidence_session_id: str | None = None
    candidate_owner_evidence_session_id: str | None = None
    owner_evidence_candidate_conflicted = False
    current_map_id: int | None = None
    current_round_index: int | None = None
    local_player_by_session: dict[str, int] = {}
    declared_item_count_by_session: dict[str, int] = {}
    hinted_local_player_id = _int(local_player_id_hint)
    pending_bid_values_by_session: dict[str, list[int]] = {}
    pending_action_sends_by_session: dict[
        str,
        list[tuple[int, int | None]],
    ] = {}
    for frame in frames:
        status = _parse_status_event(frame)
        if status is not None:
            if status.session_id is not None:
                current_session_id = status.session_id
                confirmed_owner_evidence_session_id = status.session_id
                candidate_owner_evidence_session_id = None
                owner_evidence_candidate_conflicted = False
            continue
        send = _parse_send_event(frame)
        if send is not None and send.session_id:
            # A SEND is only a bootstrap candidate when no current state/status has
            # confirmed a session.  Late traffic from an old session must never
            # replace the state machine's current owner-evidence identity.
            if confirmed_owner_evidence_session_id is None:
                if owner_evidence_candidate_conflicted:
                    pass
                elif candidate_owner_evidence_session_id is None:
                    candidate_owner_evidence_session_id = send.session_id
                elif candidate_owner_evidence_session_id != send.session_id:
                    candidate_owner_evidence_session_id = None
                    owner_evidence_candidate_conflicted = True
        if send is not None and send.kind == "bid" and send.session_id and send.value:
            pending_bid_values_by_session.setdefault(send.session_id, []).append(
                int(send.value)
            )
        if (
            send is not None
            and send.kind == "action"
            and send.session_id
            and isinstance(send.value, int)
            and not isinstance(send.value, bool)
            and send.value > 0
        ):
            pending = pending_action_sends_by_session.setdefault(send.session_id, [])
            pending.append((int(send.value), current_round_index))
            # The live UI only needs a tiny in-flight action window. Keep this
            # parser-side correlation bounded for imported captures as well.
            del pending[:-64]
        state = _parse_state_event(frame)
        if state is not None:
            if state.message_id == 0x002D:
                state = replace(
                    state,
                    settlement_auction_owner_evidence=(
                        _bind_settlement_owner_evidence_to_current_session(
                            state.settlement_auction_owner_evidence,
                            evidence_session_id=state.session_id,
                            current_session_id=(
                                confirmed_owner_evidence_session_id
                                or candidate_owner_evidence_session_id
                            ),
                            binding_conflict=(
                                "session_candidate_conflict"
                                if confirmed_owner_evidence_session_id is None
                                and owner_evidence_candidate_conflicted
                                else None
                            ),
                        )
                    ),
                )
            state_session_id = state.session_id or current_session_id
            if state_session_id is not None:
                for action in state.action_results:
                    if (
                        action.action_id == 100115
                        and isinstance(action.result, int)
                        and not isinstance(action.result, bool)
                        and action.result > 0
                    ):
                        declared_item_count_by_session[state_session_id] = (
                            action.result
                        )
                if state.message_id == 0x002D:
                    reconciled_inventory = _reconcile_declared_settlement_inventory(
                        frame,
                        state.inventory_items,
                        declared_item_count_by_session.get(state_session_id),
                    )
                    if reconciled_inventory != state.inventory_items:
                        state = replace(
                            state,
                            inventory_items=reconciled_inventory,
                        )
            if state.session_id is not None:
                local_player_id = local_player_by_session.get(state.session_id)
                if (
                    local_player_id is not None
                    and state.bids
                    and not any(bid.player_id == local_player_id for bid in state.bids)
                ):
                    local_player_by_session.pop(state.session_id, None)
                    local_player_id = None
                if (
                    local_player_id is None
                    and hinted_local_player_id is not None
                    and state.bids
                    and any(
                        bid.player_id == hinted_local_player_id
                        for bid in state.bids
                    )
                ):
                    local_player_id = hinted_local_player_id
                    local_player_by_session[state.session_id] = local_player_id
                if local_player_id is None:
                    local_player_id = _infer_local_player_id_from_bid_values(
                        state.bids,
                        pending_bid_values_by_session.get(state.session_id, ()),
                    )
                    if local_player_id is not None:
                        local_player_by_session[state.session_id] = local_player_id
                if local_player_id is not None:
                    state = replace(state, player_id=local_player_id)
            states_list.append(state)
            if state.session_id is not None:
                current_session_id = state.session_id
                if state.message_id != 0x002D:
                    confirmed_owner_evidence_session_id = state.session_id
                    candidate_owner_evidence_session_id = None
                    owner_evidence_candidate_conflicted = False
            if state.map_id is not None:
                current_map_id = state.map_id
            if state.round_index is not None:
                current_round_index = state.round_index
            continue

        direct_action = _parse_direct_action_response(
            frame,
            allow_correlated_tag=True,
        )
        if direct_action is None or current_session_id is None:
            continue
        response_round = current_round_index
        if frame.packet_tag != 0:
            pending = pending_action_sends_by_session.get(current_session_id, [])
            match_index = next(
                (
                    index
                    for index, (action_id, _round_index) in enumerate(pending)
                    if action_id == direct_action.action_id
                ),
                None,
            )
            if match_index is None:
                # Non-zero 0x0027 tags are request-correlated replies in current
                # live traffic. They are authoritative only when a same-session
                # outbound action with the same id preceded them.
                continue
            _action_id, response_round = pending.pop(match_index)
        if (
            direct_action.action_id == 100115
            and isinstance(direct_action.result, int)
            and not isinstance(direct_action.result, bool)
            and direct_action.result > 0
        ):
            declared_item_count_by_session[current_session_id] = direct_action.result
        states_list.append(
            FatbeansStateEvent(
                sort_id=frame.sort_id,
                capture_time=frame.capture_time,
                message_id=frame.message_id,
                session_id=current_session_id,
                player_id=local_player_by_session.get(current_session_id),
                map_id=current_map_id,
                # Attribute a correlated reply to the round in which its action
                # was sent. A late reply must never leak into a future round.
                round_index=response_round,
                action_results=(direct_action,),
            )
        )
    if local_player_by_session:
        states_list = [
            replace(state, player_id=local_player_id)
            if (
                state.player_id is None
                and state.session_id is not None
                and (local_player_id := local_player_by_session.get(state.session_id))
                is not None
                and (
                    not state.bids
                    or any(bid.player_id == local_player_id for bid in state.bids)
                )
            )
            else state
            for state in states_list
        ]
    return FatbeansCaptureEvents(
        packets=tuple(packets),
        frames=tuple(frames),
        sends=sends,
        states=tuple(states_list),
        statuses=statuses,
    )


def _infer_local_player_id_from_bid_values(
    bids: Sequence[FatbeansPlayerBid],
    pending_bid_values: Sequence[int],
) -> int | None:
    if not bids or not pending_bid_values:
        return None
    matched: set[int] = set()
    for value in pending_bid_values:
        candidates = [
            bid.player_id
            for bid in bids
            if value in bid.values
        ]
        if len(candidates) == 1:
            matched.add(candidates[0])
    return next(iter(matched)) if len(matched) == 1 else None


def _event_kind_for_state(state: FatbeansStateEvent) -> str:
    if state.message_id == 0x0021:
        return "session_started"
    if state.message_id == 0x002D or state.inventory_items:
        return "session_settled"
    if state.action_results or state.skill_reveals:
        return "tool_revealed"
    if state.public_infos:
        return "public_info_changed"
    return "round_changed"


def _state_updates(state: FatbeansStateEvent) -> list[FieldUpdate]:
    updates: list[FieldUpdate] = []
    if state.map_id is not None:
        updates.append(
            FieldUpdate(
                path=("session", "map_id"),
                value=state.map_id,
                source="packet",
                confidence="exact",
                sequence=state.sort_id,
            )
        )
    if state.round_index is not None:
        updates.append(
            FieldUpdate(
                path=("session", "round"),
                value=state.round_index,
                source="packet",
                confidence="exact",
                sequence=state.sort_id,
            )
        )
    hero = _hero_mode_from_state(state)
    if hero is not None:
        updates.append(
            FieldUpdate(
                path=("session", "hero"),
                value=hero,
                source="packet",
                confidence="exact",
                sequence=state.sort_id,
            )
        )
    for result in state.action_results:
        if result.result is None:
            continue
        if result.action_id in _ACTION_SESSION_FIELDS:
            updates.append(
                FieldUpdate(
                    path=_ACTION_SESSION_FIELDS[result.action_id],
                    value=result.result,
                    source="packet",
                    confidence="exact",
                    sequence=state.sort_id,
                )
            )
        elif result.action_id in _ACTION_GREEN_SPLIT_FIELDS:
            updates.append(
                FieldUpdate(
                    path=_ACTION_GREEN_SPLIT_FIELDS[result.action_id],
                    value=result.result,
                    source="packet",
                    confidence="exact",
                    sequence=state.sort_id,
                )
            )
        elif result.action_id in _ACTION_TOTAL_CELLS:
            quality = _ACTION_TOTAL_CELLS[result.action_id]
            updates.append(
                FieldUpdate(
                    path=("bucket", str(quality), "total_cells"),
                    value=result.result,
                    source="packet",
                    confidence="exact",
                    sequence=state.sort_id,
                )
            )
        elif result.action_id in _ACTION_VALUE_SUM:
            quality = _ACTION_VALUE_SUM[result.action_id]
            updates.append(
                FieldUpdate(
                    path=("bucket", str(quality), "value_sum"),
                    value=result.result,
                    source="packet",
                    confidence="exact",
                    sequence=state.sort_id,
                )
            )
        elif result.action_id in _ACTION_COUNT:
            quality = _ACTION_COUNT[result.action_id]
            updates.append(
                FieldUpdate(
                    path=("bucket", str(quality), "count"),
                    value=result.result,
                    source="packet",
                    confidence="exact",
                    sequence=state.sort_id,
                )
            )
        elif result.action_id in _ACTION_AVG_CELLS:
            quality = _ACTION_AVG_CELLS[result.action_id]
            updates.append(
                FieldUpdate(
                    path=("bucket", str(quality), "avg_cells"),
                    value=result.result,
                    source="packet",
                    confidence="exact",
                    sequence=state.sort_id,
                )
            )
    return updates


def _ahmad_skill_updates(state: FatbeansStateEvent) -> list[FieldUpdate]:
    updates: list[FieldUpdate] = []
    for reveal in state.skill_reveals:
        if reveal.result is None:
            continue
        if reveal.hero_id is not None and reveal.hero_id != 204:
            continue
        if reveal.skill_id in _AHMAD_SKILL_SESSION_FIELDS:
            updates.append(
                FieldUpdate(
                    path=_AHMAD_SKILL_SESSION_FIELDS[reveal.skill_id],
                    value=reveal.result,
                    source="packet",
                    confidence="exact",
                    sequence=state.sort_id,
                )
            )
        elif reveal.skill_id in _AHMAD_SKILL_AVG_CELLS:
            quality = _AHMAD_SKILL_AVG_CELLS[reveal.skill_id]
            updates.append(
                FieldUpdate(
                    path=("bucket", str(quality), "avg_cells"),
                    value=reveal.result,
                    source="packet",
                    confidence="exact",
                    sequence=state.sort_id,
                )
            )
        elif reveal.skill_id in _AHMAD_SKILL_COUNT:
            quality = _AHMAD_SKILL_COUNT[reveal.skill_id]
            updates.append(
                FieldUpdate(
                    path=("bucket", str(quality), "count"),
                    value=reveal.result,
                    source="packet",
                    confidence="exact",
                    sequence=state.sort_id,
                )
            )
    return updates


def _victor_skill_updates(state: FatbeansStateEvent) -> list[FieldUpdate]:
    updates: list[FieldUpdate] = []
    for reveal in state.skill_reveals:
        if reveal.result is None:
            continue
        if reveal.hero_id is not None and reveal.hero_id != 209:
            continue
        if reveal.skill_id in _VICTOR_SKILL_COUNT_SUM:
            updates.append(
                FieldUpdate(
                    path=_VICTOR_SKILL_COUNT_SUM[reveal.skill_id],
                    value=reveal.result,
                    source="packet",
                    confidence="exact",
                    sequence=state.sort_id,
                )
            )
    return updates


def _naomi_skill_updates(state: FatbeansStateEvent) -> list[FieldUpdate]:
    if os.environ.get(POSTUPDATE_NINE_HERO_SKILL_COMPAT_DISABLE) == "1":
        return []
    updates: list[FieldUpdate] = []
    for reveal in state.skill_reveals:
        if reveal.hero_id != 106 or reveal.result is None:
            continue
        path = _NAOMI_SCOPED_SKILL_COUNT_SUM.get(reveal.skill_id)
        if path is None:
            continue
        updates.append(
            FieldUpdate(
                path=path,
                value=reveal.result,
                source="packet",
                confidence="exact",
                sequence=state.sort_id,
            )
        )
    return updates


def _maria_skill_updates(state: FatbeansStateEvent) -> list[FieldUpdate]:
    updates: list[FieldUpdate] = []
    for reveal in state.skill_reveals:
        if reveal.hero_id != MARIA_HERO_ID:
            continue
        quality = _MARIA_SKILL_VALUE_SUM.get(reveal.skill_id)
        if quality is None or reveal.result is None:
            continue
        updates.append(
            FieldUpdate(
                path=("bucket", str(quality), "value_sum"),
                value=float(reveal.result),
                source="packet",
                confidence="exact",
                sequence=state.sort_id,
            )
        )
    return updates


def _hero_mode_from_state(state: FatbeansStateEvent) -> str | None:
    player_id = getattr(state, "player_id", None)
    bids = tuple(getattr(state, "bids", ()) or ())
    skill_reveals = tuple(getattr(state, "skill_reveals", ()) or ())
    if player_id is not None:
        for bid in bids:
            if bid.player_id != player_id or bid.hero_id is None:
                continue
            hero = _HERO_MODE_BY_ID.get(bid.hero_id)
            if hero is not None:
                return hero
        return None

    reveal_heroes = {
        hero
        for reveal in skill_reveals
        if reveal.hero_id is not None
        for hero in (_HERO_MODE_BY_ID.get(reveal.hero_id),)
        if hero is not None
    }
    bid_heroes = {
        hero
        for bid in bids
        if bid.hero_id is not None
        for hero in (_HERO_MODE_BY_ID.get(bid.hero_id),)
        if hero is not None
    }
    if len(reveal_heroes) == 1:
        return next(iter(reveal_heroes))
    if len(bid_heroes) == 1:
        return next(iter(bid_heroes))
    # R1 on ship maps: four seat heroes are visible in bids before local player_id attaches,
    # but the structured-hero skill reveal (e.g. Ahmed 100204 total count) still names hero_id.
    for reveal in skill_reveals:
        hero_id = reveal.hero_id
        skill_id = reveal.skill_id
        if hero_id is None or skill_id is None:
            continue
        hero = _HERO_MODE_BY_ID.get(hero_id)
        if hero is None:
            continue
        skill_ids = _STRUCTURED_REF_HERO_SKILL_IDS.get(hero)
        if skill_ids is not None and int(skill_id) in skill_ids:
            return hero
    return None


def hero_mode_from_state(state: FatbeansStateEvent) -> str | None:
    return _hero_mode_from_state(state)


def _skill_reveal_category(skill_id: int | None) -> int | None:
    if skill_id is None:
        return None
    normalized = int(skill_id)
    if os.environ.get(POSTUPDATE_NINE_HERO_SKILL_COMPAT_DISABLE) != "1":
        category = _POSTUPDATE_SKILL_REVEAL_CATEGORIES.get(normalized)
        if category is not None:
            return category
    return _SKILL_REVEAL_CATEGORIES.get(normalized)


def _inventory_updates(state: FatbeansStateEvent) -> list[FieldUpdate]:
    if not state.inventory_items:
        return []

    updates: list[FieldUpdate] = [
        FieldUpdate(
            path=("session", "warehouse_total_cells"),
            value=sum(item.cells for item in state.inventory_items),
            source="packet",
            confidence="exact",
            sequence=state.sort_id,
        ),
        FieldUpdate(
            path=("session", "total_item_count"),
            value=len(state.inventory_items),
            source="packet",
            confidence="exact",
            sequence=state.sort_id,
        ),
    ]
    for quality in (1, 2, 3, 4, 5, 6):
        quality_items = [
            item for item in state.inventory_items
            if item.quality == quality
        ]
        if not quality_items:
            continue
        updates.extend(
            (
                FieldUpdate(
                    path=("bucket", str(quality), "total_cells"),
                    value=sum(item.cells for item in quality_items),
                    source="packet",
                    confidence="exact",
                    sequence=state.sort_id,
                ),
                FieldUpdate(
                    path=("bucket", str(quality), "count"),
                    value=len(quality_items),
                    source="packet",
                    confidence="exact",
                    sequence=state.sort_id,
                ),
            )
        )
    return updates


def _aisha_skill_quality(skill_id: int) -> int | None:
    # Aisha exposes cumulative per-quality outlines for q=1..4.
    return {
        1001034: 1,
        1001033: 2,
        1001032: 3,
        1001031: 4,
    }.get(skill_id)


def _complete_quality_reveal_scope_for_skill(
    hero_id: int | None,
    skill_id: int | None,
) -> tuple[int, ...]:
    """Return qualities whose item list is complete for one skill frame.

    This is deliberately narrower than general quality-bearing skill rows.
    Only Aisha's per-tier warehouse outlines and Maria's complete low-tier
    location list are safe absence evidence for a physical runtime.
    """

    if hero_id == 103 and skill_id is not None:
        quality = _aisha_skill_quality(int(skill_id))
        return (quality,) if quality is not None else ()
    if hero_id == MARIA_HERO_ID and skill_id == _MARIA_SKILL_QUALITY_REVEAL_ID:
        return (1, 2, 3)
    return ()


def _complete_quality_reveal_scope_for_public_info(
    info_id: int | None,
) -> tuple[int, ...]:
    """Return the supported absence scope for a complete public outline."""

    if info_id is None:
        return ()
    quality = _PUBLIC_OUTLINE_QUALITY_BY_INFO_ID.get(int(info_id))
    # Purple/gold public outline rows are complete per-quality location lists.
    # Their misses can therefore exclude that quality for the same physical
    # runtime.  Red itself is not useful as negative proof for red hardening.
    return (quality,) if quality is not None and 1 <= quality <= 5 else ()


def _aisha_skill_updates(state: FatbeansStateEvent) -> list[FieldUpdate]:
    updates: list[FieldUpdate] = []
    for reveal in state.skill_reveals:
        if reveal.hero_id != 103:
            continue
        quality = _aisha_skill_quality(reveal.skill_id)
        if quality is None:
            continue
        cells_by_runtime: dict[int | None, int] = {}
        anonymous_index = 0
        for item in reveal.observed_items:
            cells = _observed_item_cells(item)
            if cells is None:
                continue
            key = item.runtime_id
            if key is None:
                key = -anonymous_index - 1
                anonymous_index += 1
            cells_by_runtime[key] = cells
        if not cells_by_runtime:
            continue
        updates.extend(
            (
                FieldUpdate(
                    path=("bucket", str(quality), "total_cells"),
                    value=sum(cells_by_runtime.values()),
                    source="packet",
                    confidence="exact",
                    sequence=state.sort_id,
                ),
                FieldUpdate(
                    path=("bucket", str(quality), "count"),
                    value=len(cells_by_runtime),
                    source="packet",
                    confidence="exact",
                    sequence=state.sort_id,
                ),
                *(
                    (
                        FieldUpdate(
                            path=("bucket_split", "white" if quality == 1 else "green", "total_cells"),
                            value=sum(cells_by_runtime.values()),
                            source="packet",
                            confidence="exact",
                            sequence=state.sort_id,
                        ),
                        FieldUpdate(
                            path=("bucket_split", "white" if quality == 1 else "green", "count"),
                            value=len(cells_by_runtime),
                            source="packet",
                            confidence="exact",
                            sequence=state.sort_id,
                        ),
                    )
                    if quality in {1, 2}
                    else ()
                ),
            )
        )
    return updates


def _public_outline_updates(state: FatbeansStateEvent) -> list[FieldUpdate]:
    updates: list[FieldUpdate] = []
    for info in state.public_infos:
        quality = _PUBLIC_OUTLINE_QUALITY_BY_INFO_ID.get(info.info_id)
        if quality is None or not info.observed_items:
            continue
        observed_qualities = {
            item.quality for item in info.observed_items
            if item.quality is not None
        }
        if observed_qualities and observed_qualities != {quality}:
            continue
        cells_by_runtime: dict[int | None, int] = {}
        anonymous_index = 0
        for item in info.observed_items:
            cells = _observed_item_cells(item)
            if cells is None:
                continue
            key = item.runtime_id
            if key is None:
                key = -anonymous_index - 1
                anonymous_index += 1
            cells_by_runtime[key] = cells
        if not cells_by_runtime:
            continue
        updates.extend(
            (
                FieldUpdate(
                    path=("bucket", str(quality), "total_cells"),
                    value=sum(cells_by_runtime.values()),
                    source="packet",
                    confidence="exact",
                    sequence=state.sort_id,
                ),
                FieldUpdate(
                    path=("bucket", str(quality), "count"),
                    value=len(cells_by_runtime),
                    source="packet",
                    confidence="exact",
                    sequence=state.sort_id,
                ),
            )
        )
    return updates


def _public_numeric_updates(state: FatbeansStateEvent) -> list[FieldUpdate]:
    updates: list[FieldUpdate] = []
    for info in state.public_infos:
        path = _PUBLIC_INFO_EXACT_UPDATE_PATHS.get(info.info_id)
        if path is not None:
            if type(info.value) is int:  # noqa: E721 - bool/subclasses excluded
                value: int | float = info.value
            elif (  # preserve the historical integral-float packet bridge
                type(info.value) is float  # noqa: E721
                and math.isfinite(info.value)
                and info.value.is_integer()
            ):
                value = int(info.value)
            else:
                continue
            if value < 0:
                continue
        else:
            path = _PUBLIC_INFO_AVG_CELLS_UPDATE_PATHS.get(info.info_id)
            if path is None or type(info.value) not in (int, float):  # noqa: E721
                continue
            value = float(info.value)
            if not math.isfinite(value) or value < 0.0 or value > 250.0:
                continue
        updates.append(
            FieldUpdate(
                path=path,
                value=value,
                source="packet",
                confidence="exact",
                sequence=state.sort_id,
            )
        )
    return updates


def _validated_outline_identity_cells(
    items: Sequence[FatbeansObservedItem],
) -> tuple[tuple[int, int], ...] | None:
    return _validated_outline_identity_cells_for_join(
        items,
        require_runtime_ids=False,
    )


def _validated_outline_identity_cells_for_join(
    items: Sequence[FatbeansObservedItem],
    *,
    require_runtime_ids: bool,
) -> tuple[tuple[int, int], ...] | None:
    cells_by_runtime: dict[int, int] = {}
    anonymous_cells: list[int] = []
    if not items:
        return None
    for item in items:
        cells = _observed_item_cells(item)
        if type(cells) is not int or cells <= 0:
            return None
        runtime_id = item.runtime_id
        if type(runtime_id) is not int or runtime_id <= 0:
            if require_runtime_ids:
                return None
            anonymous_cells.append(cells)
            continue
        if runtime_id in cells_by_runtime:
            return None
        cells_by_runtime[runtime_id] = cells

    identity = sorted(cells_by_runtime.items())
    identity.extend(
        (-index, cells)
        for index, cells in enumerate(sorted(anonymous_cells), start=1)
    )
    return tuple(identity) if identity else None


def _exact_outline_totals(
    items: Sequence[FatbeansObservedItem],
) -> tuple[int, int] | None:
    identity = _validated_outline_identity_cells(items)
    if identity is None:
        return None
    return len(identity), sum(cells for _runtime_id, cells in identity)


def _mirror_quality_runtime_ids(state: FatbeansStateEvent) -> frozenset[int] | None:
    identity_sets: set[frozenset[int]] = set()
    for result in state.action_results:
        if result.action_id != 100134:
            continue
        runtime_ids: set[int] = set()
        if not result.observed_items:
            return None
        for item in result.observed_items:
            runtime_id = item.runtime_id
            quality = item.quality
            if (
                type(runtime_id) is not int
                or runtime_id <= 0
                or type(quality) is not int
                or quality <= 0
                or runtime_id in runtime_ids
            ):
                return None
            runtime_ids.add(runtime_id)
        identity_sets.add(frozenset(runtime_ids))
    if len(identity_sets) != 1:
        return None
    return next(iter(identity_sets))


def _full_mirror_join_outline_items(
    state: FatbeansStateEvent,
) -> Sequence[FatbeansObservedItem]:
    quality_runtime_ids = _mirror_quality_runtime_ids(state)
    if not quality_runtime_ids:
        return ()

    matching_items: list[Sequence[FatbeansObservedItem]] = []
    matching_identities: set[tuple[tuple[int, int], ...]] = set()
    for reveal in state.skill_reveals:
        if reveal.hero_id != ETHAN_HERO_ID or reveal.skill_id not in ETHAN_QUALITY_OUTLINE_SKILLS:
            continue
        identity = _validated_outline_identity_cells_for_join(
            reveal.observed_items,
            require_runtime_ids=True,
        )
        if identity is None:
            continue
        outline_runtime_ids = frozenset(runtime_id for runtime_id, _cells in identity)
        if outline_runtime_ids == quality_runtime_ids:
            matching_items.append(reveal.observed_items)
            matching_identities.add(identity)
    if len(matching_identities) != 1:
        return ()
    return matching_items[0]


def _full_outline_updates(state: FatbeansStateEvent) -> list[FieldUpdate]:
    if state.inventory_items:
        return []

    full_identities: set[tuple[tuple[int, int], ...]] = set()
    invalid_semantic_full_outline = False
    for reveal in state.skill_reveals:
        if reveal.hero_id == ETHAN_HERO_ID and reveal.skill_id == ETHAN_SKILL_FULL_OUTLINE:
            identity = _validated_outline_identity_cells(reveal.observed_items)
            if identity is None:
                invalid_semantic_full_outline = True
            else:
                full_identities.add(identity)
    for result in state.action_results:
        if result.action_id == 100100:
            identity = _validated_outline_identity_cells(result.observed_items)
            if identity is None:
                invalid_semantic_full_outline = True
            else:
                full_identities.add(identity)
    mirror_join_items = _full_mirror_join_outline_items(state)
    if mirror_join_items:
        mirror_identity = _validated_outline_identity_cells_for_join(
            mirror_join_items,
            require_runtime_ids=True,
        )
        if mirror_identity is not None:
            full_identities.add(mirror_identity)

    if invalid_semantic_full_outline or len(full_identities) != 1:
        return []
    full_identity = next(iter(full_identities))
    count = len(full_identity)
    cells = sum(item_cells for _runtime_id, item_cells in full_identity)
    return [
        FieldUpdate(
            path=("session", "warehouse_total_cells"),
            value=cells,
            source="packet",
            confidence="exact",
            sequence=state.sort_id,
        ),
        FieldUpdate(
            path=("session", "total_item_count"),
            value=count,
            source="packet",
            confidence="exact",
            sequence=state.sort_id,
        ),
    ]


def _state_grid_items(
    state: FatbeansStateEvent,
    *,
    current_generation: str | None = None,
    current_session_id: str | None = None,
) -> tuple[GridItemObservation, ...]:
    if state.inventory_items:
        return tuple(
            GridItemObservation(
                cells=item.cells,
                source="packet",
                confidence="exact",
                runtime_id=item.runtime_id,
                item_id=item.item_id,
                quality=item.quality,
                local_index=item.local_index,
            )
            for item in state.inventory_items
        )

    revealed_items: list[GridItemObservation] = []
    seen_runtime_ids: set[int] = set()
    index_by_runtime: dict[int, int] = {}
    metadata_by_runtime: dict[int, FatbeansObservedItem] = {}

    def remember_item_metadata(item: FatbeansObservedItem) -> None:
        if item.runtime_id is None:
            return
        if item.quality is None and item.item_id is None and item.value is None:
            return
        current = metadata_by_runtime.get(item.runtime_id)
        if current is None:
            metadata_by_runtime[item.runtime_id] = item
            return
        metadata_by_runtime[item.runtime_id] = FatbeansObservedItem(
            local_index=(
                current.local_index
                if current.local_index is not None
                else item.local_index
            ),
            runtime_id=item.runtime_id,
            item_id=current.item_id or item.item_id,
            quality=current.quality or item.quality,
            value=current.value or item.value,
            shape_code=current.shape_code or item.shape_code,
            cells=current.cells or item.cells,
        )

    for info in state.public_infos:
        for item in info.observed_items:
            remember_item_metadata(item)
    for result in state.action_results:
        for item in result.observed_items:
            remember_item_metadata(item)

    def append_observed_item(
        item: FatbeansObservedItem,
        *,
        category: int | None = None,
        marker_cells: int | None = None,
    ) -> None:
        shape_cells = _shape_cells(item.shape_code)
        if (
            shape_cells is not None
            and item.cells is not None
            and item.cells != shape_cells
        ):
            # A shaped row is physical evidence, not a display marker.  Never
            # publish a self-contradictory footprint; another exact row for the
            # same runtime may still supply a valid shape/cell pair.
            return
        cells = _observed_item_cells(item)
        if cells is None:
            if marker_cells is not None and marker_cells > 0:
                cells = marker_cells
            elif item.local_index is not None and (
                item.quality is not None
                or item.value is not None
                or item.shape_code is not None
            ):
                cells = _observed_item_cells(item) or 1
            else:
                return
        if item.runtime_id is not None:
            if item.runtime_id in seen_runtime_ids:
                index = index_by_runtime.get(item.runtime_id)
                if index is not None:
                    current = revealed_items[index]
                    updated = current
                    if category is not None:
                        categories = tuple(
                            sorted(
                                {
                                    value
                                    for value in (
                                        *current.categories,
                                        current.category,
                                        category,
                                    )
                                    if value is not None
                                }
                            )
                        )
                        updated = replace(
                            updated,
                            category=categories[0],
                            categories=categories,
                        )
                    if item.value is not None:
                        updated = replace(updated, value=item.value)
                    if item.quality is not None and current.quality is None:
                        updated = replace(updated, quality=item.quality)
                    if item.item_id is not None and current.item_id is None:
                        updated = replace(updated, item_id=item.item_id)
                    # A shaped/identified observation (item_id + shape_code) is the
                    # authoritative footprint: adopt its cell count AND top-left anchor
                    # over a bare marker reveal. A bare reveal carries a placeholder
                    # cells=1 at a *scan* cell (interior/edge), not the item's anchor, so
                    # without this the minimap keeps a 1-cell deep marker for a multi-cell
                    # item (4403: 黄金权杖 1x4 stayed cells=1 at a row-10 scan cell instead
                    # of 4 cells at its row-8 anchor), making the engine infer phantom
                    # high-tier in the empty deep rows and over-count red.
                    if item.shape_code is not None:
                        shaped_cells = _observed_item_cells(item)
                        incoming_shape = str(item.shape_code)
                        current_footprint = current.footprint()
                        incoming_footprint = GridItemObservation(
                            cells=shaped_cells or 0,
                            source="packet",
                            confidence="exact",
                            shape_key=incoming_shape,
                            local_index=item.local_index,
                        ).footprint()
                        if (
                            current_footprint is not None
                            and incoming_footprint is not None
                            and current_footprint != incoming_footprint
                        ):
                            updated = replace(updated, confidence="low")
                        else:
                            updated = replace(
                                updated,
                                shape_key=incoming_shape,
                                cells=shaped_cells or current.cells,
                                local_index=(
                                    item.local_index
                                    if item.local_index is not None
                                    else current.local_index
                                ),
                            )
                    elif item.local_index is not None and current.local_index is None:
                        updated = replace(updated, local_index=item.local_index)
                    if updated != current:
                        revealed_items[index] = updated
                return
            seen_runtime_ids.add(item.runtime_id)
        metadata = (
            metadata_by_runtime.get(item.runtime_id)
            if item.runtime_id is not None
            else None
        )
        anchor_local_index = item.local_index
        if (
            item.shape_code is None
            and metadata is not None
            and metadata.shape_code is not None
            and metadata.local_index is not None
        ):
            # A bare quality/skill reveal (e.g. raven full-quality scan) reports a
            # scanned cell, not the footprint's top-left anchor, so for multi-cell
            # items its local_index lands on an interior/edge cell. A shaped
            # observation (action result / public info) carries the true anchor;
            # use it to position the footprint instead of the reveal's scan cell.
            anchor_local_index = metadata.local_index
        revealed_items.append(
            GridItemObservation(
                cells=cells,
                source="packet",
                confidence="exact",
                runtime_id=item.runtime_id,
                item_id=item.item_id or (metadata.item_id if metadata else None),
                quality=item.quality or (metadata.quality if metadata else None),
                shape_key=str(
                    item.shape_code
                    or (metadata.shape_code if metadata else None)
                    or ("11" if item.value is not None and item.item_id is None else None)
                    or ""
                )
                or None,
                value=item.value or (metadata.value if metadata else None),
                local_index=anchor_local_index,
                category=category,
                categories=(category,) if category is not None else (),
            )
        )
        if item.runtime_id is not None:
            index_by_runtime[item.runtime_id] = len(revealed_items) - 1

    def ethan_item_quality_known(item: FatbeansObservedItem) -> bool:
        if item.quality is not None:
            return True
        if item.runtime_id is None:
            return False
        metadata = metadata_by_runtime.get(item.runtime_id)
        return metadata is not None and metadata.quality is not None

    for reveal in state.skill_reveals:
        category = _skill_reveal_category(reveal.skill_id)
        if (
            type(current_generation) is str
            and current_generation in {"b049c3-2026-09-10-597", "76b7ca-2026-10-08-723"}
            and type(current_session_id) is str
            and current_session_id
            and state.session_id == current_session_id
            and type(state.map_id) is int and state.map_id == 5215
            and type(state.round_no) is int and state.round_no == 4
            and type(reveal.hero_id) is int and reveal.hero_id == 207
            and type(reveal.skill_id) is int and reveal.skill_id == 4022
            and type(reveal.round_index) is int and reveal.round_index == 4
            and all(row.hero_id in (None, 207) for row in state.skill_reveals)
            and state.message_id != 0x002D
        ):
            # Observed items only: the rule's "all antiques" wording does not
            # establish class completeness or invent an item/count/value.
            category = 106
        marker_only = (
            reveal.hero_id == MARIA_HERO_ID
            and reveal.skill_id == _MARIA_SKILL_QUALITY_REVEAL_ID
        )
        if (
            reveal.hero_id == ETHAN_HERO_ID
            and reveal.skill_id in ETHAN_QUALITY_OUTLINE_SKILLS
        ):
            for item in reveal.observed_items:
                if ethan_item_quality_known(item):
                    append_observed_item(
                        item,
                        category=category,
                        marker_cells=1 if marker_only else None,
                    )
            continue
        for item in reveal.observed_items:
            append_observed_item(
                item,
                category=category,
                marker_cells=1 if marker_only else None,
            )
    for info in state.public_infos:
        for item in info.observed_items:
            append_observed_item(item)
    for result in state.action_results:
        category = _CATEGORY_OUTLINE_ACTIONS.get(result.action_id)
        for item in result.observed_items:
            append_observed_item(item, category=category)
    return tuple(revealed_items)


def live_batches_from_fatbeans_capture(path: str | Path) -> tuple[LiveObservationBatch, ...]:
    """Return live batches for Fatbeans facts with stable current semantics.

    Cross-quality facts, opponent bids, and raw public info remain in
    ``parse_fatbeans_capture`` until the inference schema has explicit fields
    for them.
    """

    events = parse_fatbeans_capture(path)
    return live_batches_from_fatbeans_events(events)


def live_batches_from_fatbeans_capture_payload(
    raw: str | bytes,
) -> tuple[LiveObservationBatch, ...]:
    """Return live batches from an in-memory Fatbeans JSON export."""
    events = parse_fatbeans_capture_payload(raw)
    return live_batches_from_fatbeans_events(events)


def live_batches_from_fatbeans_events(
    events: FatbeansCaptureEvents,
    *,
    current_generation: str | None = None,
    current_session_id: str | None = None,
) -> tuple[LiveObservationBatch, ...]:
    """Return live batches from already parsed Fatbeans events."""
    batches: list[LiveObservationBatch] = []
    for state in events.states:
        updates = [
            *_state_updates(state),
            *_ahmad_skill_updates(state),
            *_victor_skill_updates(state),
            *_naomi_skill_updates(state),
            *_maria_skill_updates(state),
            *_public_numeric_updates(state),
            *_public_outline_updates(state),
            *_aisha_skill_updates(state),
            *_full_outline_updates(state),
            *_inventory_updates(state),
        ]
        grid_items = _state_grid_items(
            state,
            current_generation=current_generation,
            current_session_id=current_session_id,
        )
        if not updates and not grid_items:
            continue
        batches.append(
            LiveObservationBatch(
                source="packet",
                event_kind=_event_kind_for_state(state),  # type: ignore[arg-type]
                phase=(
                    "settled"
                    if state.message_id == 0x002D or state.inventory_items
                    else "reading" if state.message_id == 0x0021 else "bidding"
                ),
                field_updates=tuple(updates),
                grid_items=grid_items,
                sequence=state.sort_id,
                session_id=state.session_id,
            )
        )
    return tuple(batches)


def latest_player_bids(states: Iterable[FatbeansStateEvent]) -> dict[str, int]:
    out: dict[str, int] = {}
    for state in states:
        for bid in state.bids:
            if bid.current_value is not None:
                out[bid.name] = bid.current_value
    return out


def local_player_bid_summary(
    states: Iterable[FatbeansStateEvent],
) -> dict[str, Any]:
    """Resolve "我的出价/是否我拍下" from parsed states.

    Unlike ``latest_player_bids`` (name-keyed, drops uid), this keeps each bid's
    ``player_id`` so the local player's own bid survives. ``state.player_id`` is the
    local player's id that ``parse_fatbeans_packets`` already infers/stamps per
    session; the winner is whoever holds the highest current bid (auction = highest
    bidder wins at settlement). All fields are ``None``/``False`` when the local
    player could not be identified (e.g. no hint and inference failed) — callers
    treat that as "unknown", not "lost".
    """
    local_id: int | None = None
    by_id: dict[int, tuple[str, int]] = {}  # player_id -> (name, latest value)
    for state in states:
        if state.player_id is not None:
            local_id = state.player_id
        for bid in state.bids:
            if bid.current_value is not None:
                by_id[bid.player_id] = (bid.name, bid.current_value)
    winner_id: int | None = None
    winner_val: int | None = None
    for pid, (_name, val) in by_id.items():
        if winner_val is None or val > winner_val:
            winner_id, winner_val = pid, val
    self_entry = by_id.get(local_id) if local_id is not None else None
    return {
        "local_player_id": local_id,
        "self_name": self_entry[0] if self_entry is not None else None,
        "self_bid": self_entry[1] if self_entry is not None else None,
        "winner_player_id": winner_id,
        "winner_is_self": (
            local_id is not None and winner_id is not None and winner_id == local_id
        ),
    }


__all__ = (
    "FatbeansActionResult",
    "FatbeansCaptureEvents",
    "FatbeansFrame",
    "FatbeansInventoryItem",
    "FatbeansObservedItem",
    "FatbeansPacket",
    "FatbeansPlayerBid",
    "FatbeansPublicInfo",
    "FatbeansRankScoreChange",
    "FatbeansSendEvent",
    "FatbeansSkillReveal",
    "FatbeansStateEvent",
    "FatbeansStatusEvent",
    "hero_mode_from_state",
    "latest_player_bids",
    "local_player_bid_summary",
    "live_batches_from_fatbeans_capture",
    "live_batches_from_fatbeans_capture_payload",
    "live_batches_from_fatbeans_events",
    "load_fatbeans_packets",
    "load_fatbeans_packets_from_payload",
    "load_fatbeans_packets_from_rows",
    "parse_fatbeans_capture",
    "parse_fatbeans_capture_payload",
    "parse_fatbeans_packets",
    "reconstruct_fatbeans_frames",
)
