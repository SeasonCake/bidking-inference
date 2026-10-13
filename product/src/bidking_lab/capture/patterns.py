"""Regex and keyword rules for the center-left game info panel."""

from __future__ import annotations

import re

# Lines we deliberately do not map to form fields (still logged as ignored).
IGNORE_SUBSTRINGS: tuple[str, ...] = (
    "轮廓",
    "品质最高", "占位最高", "站位最高", "占格最高",
    "随机显示", "随机选择", "件藏品的品质",
    "平均价值", "平均占位",
    "当前预估最低价格",
)

IGNORE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE) for p in (
        r"^第\s*\d+\s*轮",
        r"显示.{0,12}品质.{0,8}藏品.{0,8}(?:轮廓|各自|轮)",
        r"显示.{0,12}品质.{0,8}轮廓",
        r"随机显示\s*\d+\s*件",
        r"随机显示\s*\d+\s*件藏品的品质",
        r"随机选择.{0,24}件藏品",
        r"件藏品.{0,12}平均价值",
        r"随机.{0,16}平均价值",
        r"随机.{0,24}件",
        r"随机显示\s*\d+\s*个信息完全未知",
        r"(品质|占位|站位).{0,4}最高",
        r"总藏品.{0,8}平均占位",  # 用户认为无用
        r"随机抽检",
        r"^遗珍慧眼",
        r"^启迪之光",
        r"加布里埃",
        r"^宝光[四双]鉴",
        r"^[普良优极珍]品扫描[：:]\s*[普良优极珍]品扫描",
        r"^[普良优极珍]品估价[：:]\s*[普良优极珍]品估价",
        r"^[普良优珍]品均格\s*$",
    )
)

# Panel session hints (not per-quality scans).
SESSION_PANEL_RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"所有藏品总占用.{0,16}?(\d+)\s*格"),
        "warehouse_cells",
        "面板 → 仓库总占格",
    ),
    (
        re.compile(r"所有.{0,4}品总占用.{0,24}?(\d+)\s*格"),
        "warehouse_cells",
        "面板 → 仓库总占格（OCR 容错）",
    ),
    (
        re.compile(
            r"(?:本仓|本场|本局|仓库).{0,20}?共(?:有)?\s*(\d+)\s*件"
            r"(?:藏品|意品)(?!的)",
        ),
        "total_item_count",
        "本仓/本场共有 X 件藏品",
    ),
    (
        re.compile(
            r"共(?:有)?\s*(\d+)\s*件(?:藏品|意品)(?!的)(?!.*(?:紫|金|蓝|白|绿|品质|平均))",
        ),
        "total_item_count",
        "共有 X 件藏品（无品质色）",
    ),
    (
        re.compile(r"总仓储空间.{0,12}?(\d+)\s*格"),
        "warehouse_cells",
        "道具 → 仓库总占格",
    ),
)

# Tool scans (道具) — white/green/blue never come from map hints in practice.
# Descriptions without the tool name prefix (map / merged scan lines).
# ``所有{色}…总占…`` lines are most reliable; require 所有 + color to avoid
# matching a stray 「蓝品」 inside a garbled purple OCR line.
_QUALITY_ALL_CELLS = r"所有.{{0,4}}(?:{color}).{{0,24}}总占.{{0,16}}?(\d+)\s*格"

QUALITY_CELLS_RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(_QUALITY_ALL_CELLS.format(color="紫|紧")),
        "purple_cells",
        "紫品总格（所有紫…）",
    ),
    (
        re.compile(_QUALITY_ALL_CELLS.format(color="金")),
        "gold_cells",
        "金品总格（所有金…）",
    ),
    (
        re.compile(_QUALITY_ALL_CELLS.format(color="蓝")),
        "blue_cells",
        "蓝品总格（所有蓝…）",
    ),
    (
        re.compile(
            r"(?:白色|白).{0,24}(?:绿色|绿).{0,24}总占.{0,16}?(\d+)\s*格",
        ),
        "wg_cells",
        "白+绿总格",
    ),
    (
        re.compile(
            r"^(?!.*(?:蓝|紫|金)).*(?:白|绿).{0,24}总占.{0,16}?(\d+)\s*格",
        ),
        "wg_cells",
        "白+绿总格（OCR 断行）",
    ),
    (
        re.compile(r"^品总占.{0,16}?(\d+)\s*格"),
        "blue_cells",
        "蓝品总格（OCR 断行）",
    ),
    (
        re.compile(r"紫(?:色)?(?:品|色品质)?.{0,24}总占.{0,16}?(\d+)\s*格"),
        "purple_cells",
        "紫品总格",
    ),
    (
        re.compile(r"金(?:色)?(?:品|色品质)?.{0,24}总占.{0,16}?(\d+)\s*格"),
        "gold_cells",
        "金品总格",
    ),
)

TOOL_SCAN_RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"普.{0,4}品?.{0,4}扫描.{0,60}?(\d+)\s*格"),
        "wg_cells",
        "普品扫描 → 白+绿总格",
    ),
    (
        re.compile(r"良.{0,4}品?.{0,4}扫描.{0,60}?(\d+)\s*格"),
        "blue_cells",
        "良品扫描 → 蓝品总格",
    ),
    (
        re.compile(r"优.{0,4}品?.{0,4}扫描.{0,60}?(\d+)\s*格"),
        "purple_cells",
        "优品扫描 → 紫品总格",
    ),
    (
        re.compile(r"极.{0,4}品?.{0,4}扫描.{0,60}?(\d+)\s*格"),
        "gold_cells",
        "极品扫描 → 金品总格",
    ),
    (
        re.compile(r"珍品扫描.{0,40}?(\d+)\s*格"),
        "red_cells_total",
        "珍品扫描 → 红品总格",
    ),
)

AVG_CELLS_RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"优品均格.{0,30}?约?\s*([\d.]+)\s*格"),
        "purple_avg_raw",
        "优品均格",
    ),
    (
        re.compile(r"极品均格.{0,30}?约?\s*([\d.]+)\s*格"),
        "gold_avg_raw",
        "极品均格",
    ),
    (
        re.compile(r"珍品均格.{0,30}?约?\s*([\d.]+)\s*格"),
        "red_avg_raw",
        "珍品均格（仅记录，红品少用均格）",
    ),
)

# Map session hints (dynamic, not in BidMap static columns).
MAP_METRIC_RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"紫(?:色|品).{0,12}?均格.{0,20}?约?\s*([\d.]+)\s*格"),
        "purple_avg_raw",
        "地图 → 紫品均格",
    ),
    (
        re.compile(r"紫.{0,24}平均占位.{0,10}?约?\s*([\d.]+)\s*格"),
        "purple_avg_raw",
        "紫品平均占位（均格）",
    ),
    (
        re.compile(r"品.{0,4}平均占位约?\s*([\d.]+)\s*格"),
        "purple_avg_raw",
        "紫品均格（OCR 断行）",
    ),
    (
        re.compile(r"金(?:色|品).{0,12}?均格.{0,20}?约?\s*([\d.]+)\s*格"),
        "gold_avg_raw",
        "地图 → 金品均格",
    ),
    (
        re.compile(r"紫(?:色|品).{0,12}?均价.{0,20}?([\d,，.]+)"),
        "purple_avg_value",
        "地图 → 紫品均价",
    ),
    (
        re.compile(
            r"紫(?:色|品).{0,28}?平均价值.{0,16}?约?\s*([\d,，.]+)",
        ),
        "purple_avg_value",
        "地图 → 紫品平均价值（竞拍信息行）",
    ),
    (
        re.compile(r"金(?:色|品).{0,12}?均价.{0,20}?([\d,，.]+)"),
        "gold_avg_value",
        "地图 → 金品均价",
    ),
    (
        re.compile(
            r"金(?:色|品).{0,28}?平均价值.{0,16}?约?\s*([\d,，.]+)",
        ),
        "gold_avg_value",
        "地图 → 金品平均价值（竞拍信息行）",
    ),
    (
        re.compile(r"紫(?:色|品).{0,12}?总(?:价|估值).{0,20}?([\d,，]+)"),
        "purple_value",
        "地图 → 紫品总价",
    ),
    (
        re.compile(
            r"紫.{0,28}?总(?:价值|价|估值|价位).{0,10}?为?\s*(\d[\d,，]*)",
        ),
        "purple_value",
        "紫品总价（估价/总价值 OCR）",
    ),
    (
        re.compile(r"所有紫.{0,20}?总(?:价值|价).{0,10}?为?\s*(\d[\d,，]*)"),
        "purple_value",
        "所有紫品总价值",
    ),
    (
        re.compile(r"所有紫色品质藏品的总价值为\s*(\d[\d,，]*)"),
        "purple_value",
        "紫品总价值",
    ),
    (
        re.compile(
            r"显示.{0,4}紫.{0,24}?总(?:价值|价).{0,10}?为?\s*(\d[\d,，]*)",
        ),
        "purple_value",
        "显示紫品总价值（道具描述）",
    ),
    (
        re.compile(r"金(?:色|品).{0,12}?总(?:价|估值).{0,20}?([\d,，]+)"),
        "gold_value",
        "地图 → 金品总价",
    ),
    (
        re.compile(
            r"金.{0,28}?总(?:价值|价|估值|价位).{0,10}?为?\s*(\d[\d,，]*)",
        ),
        "gold_value",
        "金品总价（估价/总价值 OCR）",
    ),
    (
        re.compile(r"所有金.{0,20}?总(?:价值|价).{0,10}?为?\s*(\d[\d,，]*)"),
        "gold_value",
        "所有金品总价值",
    ),
    (
        re.compile(
            r"显示.{0,4}金.{0,24}?总(?:价值|价).{0,10}?为?\s*(\d[\d,，]*)",
        ),
        "gold_value",
        "显示金品总价值（道具描述）",
    ),
    (
        re.compile(r"紫(?:色|品).{0,12}?总格.{0,20}?(\d+)\s*格"),
        "purple_cells",
        "地图 → 紫品总格",
    ),
    (
        re.compile(r"金(?:色|品).{0,12}?总格.{0,20}?(\d+)\s*格"),
        "gold_cells",
        "地图 → 金品总格",
    ),
    (
        re.compile(
            r"(?:紫|紧).{0,32}?道具.{0,10}?(\d+)\s*件",
        ),
        "purple_count",
        "地图 → 紫品件数（品质道具 N 件）",
    ),
    (
        re.compile(r"金(?:色|品).{0,24}?道具.{0,10}?(\d+)\s*件"),
        "gold_count",
        "地图 → 金品件数（品质道具 N 件）",
    ),
    (
        re.compile(r"蓝.{0,32}?道具.{0,10}?(\d+)\s*件"),
        "blue_count",
        "地图 → 蓝品件数",
    ),
    (
        re.compile(r"紫(?:色|品).{0,16}?(\d+)\s*件"),
        "purple_count",
        "地图 → 紫品件数",
    ),
    (
        re.compile(r"金(?:色|品).{0,16}?(\d+)\s*件"),
        "gold_count",
        "地图 → 金品件数",
    ),
    (
        re.compile(
            r"(?:本仓|本场|本局|仓库|所有藏品).{0,20}?(\d+)\s*件(?:藏品|意品)(?!的)(?!.*平均)",
        ),
        "total_item_count",
        "地图 → 总藏品件数（需本仓/本场等语境）",
    ),
)

MAP_NAME_PATTERN = re.compile(
    r"^([^:：]{2,24})[:：]\s*(?:竞拍信息|完拍信息|地图信息|竞拍|信息)",
)
