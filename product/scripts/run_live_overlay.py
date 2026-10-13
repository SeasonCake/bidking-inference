"""Tiny always-on-top live overlay for ``data/logs/live/latest_snapshot.json``.

The overlay has no game integration by itself. It displays the latest monitor
snapshot, so it works with the directory/stdin monitor today and can keep
working when the monitor source becomes a true realtime feed.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import logging
import math
import os
from pathlib import Path
import signal
import time
import tkinter as tk
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

COMPACT_WIDTH = 480
COMPACT_HEIGHT = 420
COMPACT_MIN_WIDTH = 360
COMPACT_MIN_HEIGHT = 260
DETAIL_MIN_WIDTH = 820
DETAIL_MIN_HEIGHT = 620
DETAIL_MAX_WIDTH = 980
DETAIL_MAX_HEIGHT = 860
HOVER_OFFSET = 18
HOVER_MARGIN = 12
HOVER_MOVE_DEADZONE = 8
SLOW_PROCESSING_SECONDS = 15.0
STALE_SNAPSHOT_SECONDS = 120
SETTLED_RESULT_RETAIN_SECONDS = 60
DEFAULT_SNAPSHOT_PATH = ROOT / "data" / "logs" / "live" / "latest_snapshot.json"
ROUND_WAREHOUSE_REFERENCE_MULTIPLIERS = {
    1: 2.0,
    2: 1.6,
    3: 1.3,
    4: 1.1,
    5: 1.0,
}

BG = "#09111f"
PANEL = "#111827"
PANEL_SOFT = "#162033"
BORDER = "#263244"
TEXT = "#e5e7eb"
MUTED = "#94a3b8"
GOOD = "#86efac"
WARN = "#fbbf24"
BAD = "#fb7185"
ACCENT = "#60a5fa"
PURPLE = "#c084fc"

TAG_COLORS = {
    "header": TEXT,
    "normal": TEXT,
    "good": GOOD,
    "warn": WARN,
    "bad": BAD,
    "dim": MUTED,
}


def _load_snapshot(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _capture_status_path(snapshot_path: Path) -> Path:
    return snapshot_path.parent / "capture_source_status.json"


def _load_live_snapshot(snapshot_path: Path) -> dict:
    snapshot = _load_snapshot(snapshot_path)
    capture = _load_snapshot(_capture_status_path(snapshot_path))
    if not snapshot and capture:
        snapshot = {"_no_artifact": True}
    if capture:
        snapshot = dict(snapshot)
        snapshot["_capture_source_status"] = capture
    return snapshot


def _snapshot_file_signature(path: Path) -> tuple[str, int, int] | tuple[str]:
    try:
        stat = path.stat()
    except OSError:
        return ("missing",)
    return ("file", stat.st_mtime_ns, stat.st_size)


def _capture_status_signature(capture: dict[str, Any] | None) -> tuple[Any, ...]:
    if not isinstance(capture, dict) or not capture:
        return ("missing",)
    accepted_frames = (
        capture.get("accepted_frames")
        if capture.get("accepted_frames") is not None
        else capture.get("accepted_packets")
    )
    return (
        "capture",
        capture.get("source"),
        capture.get("process_name"),
        capture.get("active_flows"),
        bool(capture.get("raw_packets")),
        bool(accepted_frames),
        capture.get("active_session_id"),
    )


def _terminate_pid(pid: int) -> bool:
    if pid <= 0 or pid == os.getpid():
        return False
    try:
        import psutil

        process = psutil.Process(pid)
        process.terminate()
        try:
            process.wait(timeout=3.0)
        except psutil.TimeoutExpired:
            process.kill()
            process.wait(timeout=3.0)
        return True
    except ImportError:
        try:
            os.kill(pid, signal.SIGTERM)
            return True
        except OSError:
            return False
    except Exception:
        return False


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        import psutil

        return psutil.pid_exists(pid) and psutil.Process(pid).is_running()
    except ImportError:
        pass
    except Exception:
        return False
    if os.name == "nt":
        try:
            import ctypes

            process_query_limited_information = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                process_query_limited_information,
                False,
                int(pid),
            )
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _watched_pid_exited(
    pids: list[int] | tuple[int, ...],
    *,
    running_fn: Any = _pid_running,
) -> bool:
    return any(not running_fn(int(pid)) for pid in pids if int(pid) > 0)


def _cleanup_exit_targets(
    pids: list[int] | tuple[int, ...],
    lock_paths: list[Path] | tuple[Path, ...],
    *,
    terminate_fn: Any = _terminate_pid,
) -> None:
    seen: set[int] = set()
    for pid in pids:
        if pid in seen:
            continue
        seen.add(pid)
        terminate_fn(int(pid))
    for lock_path in lock_paths:
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass


def _default_window_geometry(screen_width: int, screen_height: int) -> str:
    width = max(COMPACT_MIN_WIDTH, min(COMPACT_WIDTH, screen_width - 80))
    height = max(COMPACT_MIN_HEIGHT, min(COMPACT_HEIGHT, screen_height - 120))
    return f"{width}x{height}+40+80"


def _detail_window_size(
    screen_width: int,
    screen_height: int,
    *,
    requested_width: int = 0,
    requested_height: int = 0,
) -> tuple[int, int]:
    max_width = max(DETAIL_MIN_WIDTH, min(DETAIL_MAX_WIDTH, screen_width - 80))
    max_height = max(DETAIL_MIN_HEIGHT, min(DETAIL_MAX_HEIGHT, screen_height - 120))
    width = max(DETAIL_MIN_WIDTH, min(max_width, requested_width + 40))
    height = max(DETAIL_MIN_HEIGHT, min(max_height, requested_height + 60))
    return width, height


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _price_int(value: Any) -> int | None:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _processing_seconds(
    snapshot: dict[str, Any],
    ui_contract: dict[str, Any] | None = None,
) -> float | None:
    contract = ui_contract if isinstance(ui_contract, dict) else {}
    source = _as_mapping(contract.get("source"))
    diagnostics = _as_mapping(contract.get("diagnostics"))
    sampling = _as_mapping(diagnostics.get("sampling"))
    for value in (
        source.get("processing_seconds"),
        sampling.get("processing_seconds"),
        snapshot.get("processing_seconds"),
    ):
        seconds = _float_or_none(value)
        if seconds is not None:
            return seconds
    return None


def _fmt_int(value: Any) -> str:
    if value is None or value == "":
        return "?"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def _range_parts(value: Any) -> tuple[str, ...]:
    text = str(value or "").strip()
    if not text:
        return ()
    return tuple(part.strip() for part in text.split("/") if part.strip())


def _range_p50(value: Any) -> str:
    parts = _range_parts(value)
    if len(parts) >= 3:
        return parts[1]
    if len(parts) == 1:
        return parts[0]
    return ""


def _flag(value: Any) -> bool:
    return value is True or str(value).lower() == "true"


def _first(rows: Any) -> dict[str, Any]:
    if isinstance(rows, list | tuple) and rows:
        row = rows[0]
        if isinstance(row, dict):
            return row
    return {}


def _short(value: Any, limit: int = 92) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _join_parts(parts: tuple[Any, ...] | list[Any], *, sep: str = " / ") -> str:
    return sep.join(str(part) for part in parts if str(part or "").strip())


def _ui_contract_constraints_summary(contract: dict[str, Any]) -> dict[str, Any]:
    constraints = _as_mapping(contract.get("constraints"))
    return _as_mapping(constraints.get("summary"))


def _known_red_item_count(contract: dict[str, Any]) -> int | None:
    count = _int_or_none(
        _ui_contract_constraints_summary(contract).get("known_red_item_count")
    )
    if count is not None and count > 0:
        return count
    return None


def _q6_count_range(contract: dict[str, Any], v2: dict[str, Any] | None = None) -> str:
    baseline = _as_mapping(contract.get("baseline"))
    posterior = _as_mapping(baseline.get("posterior"))
    text = str(posterior.get("q6_count_range") or "").strip()
    if not text and v2:
        text = str(v2.get("q6件数 P10/P50/P90") or "").strip()
    return text


def _q6_count_display(
    contract: dict[str, Any],
    v2: dict[str, Any] | None = None,
    *,
    known_prefix: str = "红品",
) -> str:
    red_count = _known_red_item_count(contract)
    if red_count is not None:
        return f"{known_prefix} {red_count}件"
    count_range = _q6_count_range(contract, v2)
    if count_range:
        return f"件数 {count_range}"
    return ""


def _v3_confidence_label(value: Any) -> str:
    text = str(value or "").strip()
    return {
        "high": "高",
        "medium": "中",
        "low": "低",
        "low_medium": "中低",
        "medium_low": "中低",
    }.get(text.lower(), text)


def _clamp_scroll_fraction(value: Any) -> float:
    try:
        fraction = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, fraction))


def _severity_for_bid(text: str) -> str:
    if "停止" in text or "过热" in text or "不追" in text:
        return "bad"
    if "防守" in text or "可守" in text or "谨慎" in text or "风险" in text:
        return "warn"
    return "good"


def _severity_color(severity: str) -> str:
    return TAG_COLORS.get(severity, TEXT)


def _section_style(
    title: Any,
    value: Any = "",
    detail: Any = "",
) -> dict[str, str]:
    title_text = str(title or "")
    text = f"{title_text} {value or ''} {detail or ''}"
    if "v3 实战" in title_text or "V3 实战" in title_text:
        tag = "warn" if any(token in text for token in ("低估", "加价", "风险")) else "dim"
        return {"badge": "V3", "tag": tag, "color": WARN if tag == "warn" else PURPLE}
    if any(token in text for token in ("后验无匹配", "过热", "停止追价", "冲突")):
        return {"badge": "风险", "tag": "bad", "color": BAD}
    if any(token in text for token in ("风险", "q6", "红货", "漏", "低置信")):
        return {"badge": "红货", "tag": "warn", "color": WARN}
    if "Fallback" in title_text:
        return {"badge": "兜底", "tag": "warn", "color": WARN}
    if "Shadow" in title_text:
        return {"badge": "SHADOW", "tag": "dim", "color": PURPLE}
    if "MiniMap" in title_text:
        return {"badge": "地图", "tag": "normal", "color": ACCENT}
    if "输入约束" in title_text:
        return {"badge": "约束", "tag": "normal", "color": PURPLE}
    if "布局" in title_text:
        return {"badge": "布局", "tag": "normal", "color": ACCENT}
    if "道具" in title_text:
        return {"badge": "道具", "tag": "good", "color": GOOD}
    if "正式出价" in title_text:
        tag = _severity_for_bid(text)
        return {"badge": "出价", "tag": tag, "color": _severity_color(tag)}
    if "诊断" in title_text:
        return {"badge": "诊断", "tag": "dim", "color": PURPLE}
    if "结算" in title_text or "Truth" in title_text:
        return {"badge": "TRUTH", "tag": "dim", "color": MUTED}
    return {"badge": "提示", "tag": "normal", "color": ACCENT}


def _chip_style(text: Any, fallback_tag: str = "normal") -> dict[str, str]:
    value = str(text or "")
    if any(token in value for token in ("过热", "停止", "不追", "后验无匹配")):
        return {"fg": BAD, "bg": "#2b1720"}
    if any(token in value for token in ("最高", "当前", "抢仓")):
        return {"fg": WARN, "bg": "#2a2114"}
    if any(token in value for token in ("防守", "可守", "谨慎")):
        return {"fg": WARN, "bg": "#2a2114"}
    if "进攻" in value:
        return {"fg": GOOD, "bg": "#13261b"}
    return {"fg": _severity_color(fallback_tag), "bg": PANEL}


def _is_price_metric(title: Any) -> bool:
    title_text = str(title or "")
    return any(
        token in title_text
        for token in ("价值", "估值", "P50", "价", "红货", "抢仓", "停止", "最高")
    )


def _quality_color(quality: Any) -> str:
    return _quality_style(quality)["fill"]


def _quality_style(quality: Any) -> dict[str, str]:
    try:
        q = int(quality)
    except (TypeError, ValueError):
        return {
            "fill": "",
            "outline": "#94a3b8",
            "stipple": "",
            "hatch": "///",
            "unknown": "1",
        }
    if q >= 6:
        return {"fill": BAD, "outline": "#fecdd3", "stipple": "", "hatch": "", "unknown": ""}
    if q == 5:
        return {"fill": WARN, "outline": "#fde68a", "stipple": "", "hatch": "", "unknown": ""}
    if q == 4:
        return {"fill": PURPLE, "outline": "#e9d5ff", "stipple": "", "hatch": "", "unknown": ""}
    if q == 3:
        return {"fill": ACCENT, "outline": "#bfdbfe", "stipple": "", "hatch": "", "unknown": ""}
    if q == 2:
        return {"fill": GOOD, "outline": "#bbf7d0", "stipple": "", "hatch": "", "unknown": ""}
    return {"fill": "#f8fafc", "outline": "#cbd5e1", "stipple": "", "hatch": "", "unknown": ""}


def _bounded_popup_position(
    *,
    pointer_x: int,
    pointer_y: int,
    popup_width: int,
    popup_height: int,
    screen_width: int,
    screen_height: int,
    offset: int = HOVER_OFFSET,
    margin: int = HOVER_MARGIN,
) -> tuple[int, int]:
    x = pointer_x + offset
    y = pointer_y + offset
    if x + popup_width + margin > screen_width:
        x = pointer_x - popup_width - offset
    if y + popup_height + margin > screen_height:
        y = pointer_y - popup_height - offset
    max_x = max(margin, screen_width - popup_width - margin)
    max_y = max(margin, screen_height - popup_height - margin)
    return max(margin, min(x, max_x)), max(margin, min(y, max_y))


def _age(snapshot: dict[str, Any]) -> tuple[str, bool]:
    seconds = _snapshot_age_seconds(snapshot)
    if seconds is None:
        return "", False
    seconds_old = max(0, int(seconds))
    return f"{seconds_old}s前", seconds_old > STALE_SNAPSHOT_SECONDS


def _snapshot_age_seconds(snapshot: dict[str, Any]) -> float | None:
    created_at = snapshot.get("created_at")
    if not isinstance(created_at, int | float):
        return None
    return max(0.0, time.time() - float(created_at))


def _capture_status_age_seconds(capture: dict[str, Any]) -> float | None:
    ts = capture.get("ts")
    if not isinstance(ts, int | float):
        return None
    return max(0.0, time.time() - float(ts))


def _capture_has_fresh_session(capture: dict[str, Any] | None) -> bool:
    if not isinstance(capture, dict) or not capture.get("active_session_id"):
        return False
    age_seconds = _capture_status_age_seconds(capture)
    return age_seconds is not None and age_seconds <= 10.0


def _session_map_id(session: Any) -> int | None:
    text = str(session or "")
    prefix = text.split(":", 1)[0]
    try:
        return int(prefix)
    except (TypeError, ValueError):
        return None


def _capture_waiting_copy(
    capture: dict[str, Any] | None,
    *,
    fallback_subtitle: str,
    fallback_detail: str,
) -> tuple[str, str, list[tuple[str, str]]]:
    if not isinstance(capture, dict) or not capture:
        return fallback_subtitle, fallback_detail, []
    age_seconds = _capture_status_age_seconds(capture)
    fresh = age_seconds is not None and age_seconds <= 10.0
    active_flows = capture.get("active_flows")
    sniffed_packets = capture.get("sniffed_packets")
    raw_packets = capture.get("raw_packets")
    accepted_frames = (
        capture.get("accepted_frames")
        if capture.get("accepted_frames") is not None
        else capture.get("accepted_packets")
    )
    try:
        active_flow_count = int(active_flows or 0)
    except (TypeError, ValueError):
        active_flow_count = 0
    try:
        raw_count = int(raw_packets or 0)
    except (TypeError, ValueError):
        raw_count = 0
    try:
        accepted_count = int(accepted_frames or 0)
    except (TypeError, ValueError):
        accepted_count = 0
    session = capture.get("active_session_id")
    map_id = _session_map_id(session)
    if not fresh:
        return fallback_subtitle, fallback_detail, []
    if session:
        if map_id is not None:
            subtitle = f"监听中，已抓到新局 map {map_id}"
            detail = "等待首个状态帧/推理更新"
        else:
            subtitle = "监听中，已有对局会话"
            detail = "等待下一次实时推理更新"
    elif active_flow_count > 0 and raw_count > 0 and accepted_count == 0:
        subtitle = "监听中，识别对局数据"
        detail = "已抓到网络包，等待可解析状态"
    elif active_flow_count > 0:
        subtitle = "监听中，等待对局数据"
        detail = "已连接游戏服务器，等待下一条对局状态包"
    else:
        subtitle = "监听中，等待游戏连接"
        detail = "等待 BidKing.exe 建立对局连接"
    notes = [
        (
            "抓包状态",
            f"flows {active_flows if active_flows is not None else '?'} / "
            f"sniffed {sniffed_packets if sniffed_packets is not None else '?'} / "
            f"raw {raw_packets if raw_packets is not None else '?'} / "
            f"accepted {accepted_frames if accepted_frames is not None else '?'}",
        )
    ]
    if session:
        notes.append(("当前会话", str(session)))
    if map_id is not None:
        notes.append(("当前地图", str(map_id)))
    return subtitle, detail, notes


def _overlay_standby_from_capture(
    capture_status: dict[str, Any] | None,
    *,
    fallback_subtitle: str,
    fallback_detail: str,
    decision_text: str | None = None,
) -> dict[str, Any]:
    subtitle, detail, notes = _capture_waiting_copy(
        capture_status,
        fallback_subtitle=fallback_subtitle,
        fallback_detail=fallback_detail,
    )
    if decision_text is None:
        if "识别对局数据" in subtitle or "等待可解析状态" in detail:
            decision_text = "解析协议帧中"
        elif "已有对局会话" in subtitle:
            decision_text = "等待推理更新"
        elif _capture_has_fresh_session(capture_status):
            decision_text = "新局监听中"
        else:
            decision_text = "等待对局开始"
    return _standby_model(
        subtitle=subtitle,
        detail=detail,
        notes=notes,
        decision_text=decision_text,
    )


def _demo_snapshot() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "created_at": time.time(),
        "file": "demo_overlay_snapshot.json",
        "hero": "ethan",
        "map_id": 2506,
        "round": 3,
        "known_value_sum": 1_292_866,
        "panel": {
            "summary_rows": [
                {
                    "topic": "当前最高价是否可追",
                    "conclusion": "可守不抢",
                    "detail": "对手 642,000 / 防守价 598,000 / 停止价 712,000",
                },
                {
                    "topic": "当前价值区间",
                    "conclusion": "402,000 / 741,000 / 1,180,000",
                    "detail": "v2 decision_value；raw 402,000 / 812,000 / 1,620,000",
                },
                {
                    "topic": "当前仓储区间",
                    "conclusion": "142 / 168 / 204",
                    "detail": "中；布局已知格较多，但仍有底部空间不确定性",
                },
                {
                    "topic": "下一次优先使用道具",
                    "conclusion": "随机抽检（2），ROI 318.4",
                    "detail": "优先确认 q6 是否为 3x4/4x4 大件",
                },
            ],
            "layout_stages": [
                {
                    "stage": "R3 / sort 66",
                    "known_cells": "128",
                    "coverage": "76%",
                    "deepest_row": "19",
                    "estimate": "142/168/204",
                    "confidence": "中",
                    "risk": "中：底部空间仍可能容纳红货",
                },
            ],
        },
        "v2_posterior_rows": [
            {
                "范围": "2506 探险家座舰资料库",
                "匹配": "244/500",
                "价值口径": "decision_value",
                "决策价值 P10/P50/P90": "402,000 / 741,000 / 1,180,000",
                "原始价值 P10/P50/P90": "402,000 / 812,000 / 1,620,000",
                "q6价值 P10/P50/P90": "120,000 / 452,800 / 932,715",
                "q6决策价值 P10/P50/P90": "90,000 / 386,200 / 712,400",
                "q6件数 P10/P50/P90": "0 / 1 / 1",
                "q6格数 P10/P50/P90": "0 / 4 / 6",
                "q6样本率": "68.0%",
                "q6先验缺口": "件数P90低1.10；格数P90低3.5",
                "q6先验风险参考": "486,510",
                "q6先验风险": "是",
                "q6实战门控": "shipwreck_positive_net",
                "q6实战参考P90": "486,510",
                "诊断": "footprint_overlap_cells:1",
            },
        ],
        "bid_rows": [
            {
                "证据": "v2 decision_value",
                "轮次": "R3/5",
                "信息强度": "中",
                "仓储": "后验 142/168/204 (中)",
                "当前最高": "对手A 642,000",
                "风险带": "防守区",
                "探价(P10)": "401,999",
                "防守价": "598,000",
                "抢仓上限": "666,900",
                "停止价": "712,000",
                "建议": "可守不抢",
            },
        ],
        "model_eval": {
            "q6_p90_misses_truth": True,
            "layout_conflict": True,
            "decision_value_p50_error": -188_000,
            "warehouse_p50_error": 9,
            "stop_minus_final_value": -122_000,
        },
        "category_grid_items": [
            {
                "category": 108,
                "category_label": "能源",
                "quality": 6,
                "local_index": 14,
                "cells": 16,
                "shape_key": "44",
                "row": 2,
                "col": 4,
            },
            {
                "category": 107,
                "category_label": "数码",
                "quality": 4,
                "local_index": 26,
                "cells": 4,
                "shape_key": "22",
                "row": 3,
                "col": 6,
            },
        ],
    }


def _summary_entries(snapshot: dict) -> list[tuple[str, str]]:
    panel = snapshot.get("panel") or {}
    rows = panel.get("summary_rows") or []
    entries: list[tuple[str, str]] = []
    if not snapshot:
        return [("等待 latest_snapshot.json ...", "dim")]
    age, stale = _age(snapshot)
    hero = str(snapshot.get("hero") or "?")
    header = (
        f"{hero.upper()}  |  map {snapshot.get('map_id') or '?'}  "
        f"|  R{snapshot.get('round') or '?'}  "
        f"|  结算 {_fmt_int(snapshot.get('known_value_sum'))}"
        f"{f'  {age}' if age else ''}"
    )
    entries.append((header, "header"))
    if stale:
        entries.append(("状态: snapshot 超过 120 秒未更新，检查 Fatbeans 导出或 monitor 进程", "warn"))
    processing_seconds = _processing_seconds(
        snapshot,
        snapshot.get("ui_contract") if isinstance(snapshot.get("ui_contract"), dict) else {},
    )
    if (
        processing_seconds is not None
        and processing_seconds > SLOW_PROCESSING_SECONDS
    ):
        entries.append(
            (
                f"状态: 本次推理耗时 {processing_seconds:.1f}s，观察是否连续变慢",
                "warn",
            )
        )

    summary_by_topic = {
        str(row.get("topic") or ""): row
        for row in rows
        if isinstance(row, dict)
    }
    bid = _first(snapshot.get("bid_rows"))
    if bid:
        action = str(bid.get("建议") or "?")
        current = str(bid.get("当前最高") or "?")
        risk = str(bid.get("风险带") or "?")
        stop = str(bid.get("停止价") or "?")
        entries.append(
            (
                f"决策: {action}  |  最高 {current}  |  停止 {stop}  |  {risk}",
                _severity_for_bid(f"{action} {risk}"),
            )
        )
    elif decision_row := summary_by_topic.get("当前最高价是否可追"):
        line = (
            f"决策: {decision_row.get('conclusion', '')}  |  "
            f"{_short(decision_row.get('detail'), 90)}"
        )
        entries.append((line, _severity_for_bid(line)))

    value_row = summary_by_topic.get("当前价值区间")
    if value_row:
        entries.append(
            (
                "价值: "
                f"{value_row.get('conclusion', '')}  |  "
                f"{_short(value_row.get('detail'), 74)}",
                "normal",
            )
        )
    warehouse_row = summary_by_topic.get("当前仓储区间")
    if warehouse_row:
        entries.append(
            (
                "仓储: "
                f"{warehouse_row.get('conclusion', '')}  |  "
                f"{_short(warehouse_row.get('detail'), 70)}",
                "normal",
            )
        )

    v2_rows = snapshot.get("v2_posterior_rows") or []
    if v2_rows:
        row = v2_rows[0]
        q6_rate = row.get("q6样本率") or ""
        q6_prior = row.get("q6掉落先验") or ""
        q6_count = row.get("q6件数 P10/P50/P90") or ""
        q6_value = row.get("q6价值 P10/P50/P90") or ""
        q6_decision_value = row.get("q6决策价值 P10/P50/P90") or ""
        q6_prior_gap = str(row.get("q6先验缺口") or "")
        q6_prior_floor = str(row.get("q6先验风险参考") or "")
        q6_practical_p90 = str(row.get("q6实战参考P90") or "")
        if q6_rate or q6_value:
            tag = "warn"
            try:
                tag = "bad" if float(str(q6_rate).strip("%")) < 10 else "normal"
            except ValueError:
                tag = "normal"
            if q6_prior_gap:
                tag = "warn"
            prior_text = f" / 先验 {q6_prior}" if q6_prior else ""
            count_text = f"  |  件数 {q6_count}" if q6_count else ""
            value_text = q6_decision_value or q6_value
            gap_text = (
                f"  |  先验缺口 {q6_prior_gap}"
                + (
                    f" / 实战参考P90 {q6_practical_p90}"
                    if q6_practical_p90
                    else f" / 参考上界 {q6_prior_floor}"
                    if q6_prior_floor
                    else ""
                )
                if q6_prior_gap
                else ""
            )
            entries.append(
                (
                    f"红货: q6样本率 {q6_rate or '?'}{prior_text}{count_text}  |  {value_text}{gap_text}",
                    tag,
                )
            )
        diagnostics = str(row.get("诊断") or "")
        if diagnostics:
            tag = "warn"
            if "q6_unconstrained_low_sample_rate" in diagnostics:
                tag = "bad"
            entries.append((f"后验: {_short(diagnostics, 108)}", tag))
    layout = panel.get("layout_stages") or []
    if layout:
        stage = layout[0]
        risk = stage.get("risk", "")
        tag = "warn" if risk and risk not in ("低", "低风险") else "normal"
        entries.append(
            (
                f"布局: {stage.get('stage', '')}  |  "
                f"已知 {stage.get('known_cells', '')}格  "
                f"估计 {stage.get('estimate', '')}  "
                f"{stage.get('confidence', '')}  |  {_short(risk, 52)}",
                tag,
            )
        )
    tool_row = summary_by_topic.get("下一次优先使用道具")
    if tool_row:
        entries.append(
            (
                "道具: "
                f"{tool_row.get('conclusion', '')}"
                + (
                    f"  |  {_short(tool_row.get('detail'), 62)}"
                    if tool_row.get("detail")
                    else ""
                ),
                "good",
            )
        )
    eval_row = snapshot.get("model_eval") or {}
    if eval_row:
        if _flag(eval_row.get("q6_false_low_risk")):
            entries.append(("回测警告: 真实有红货，但后验 q6 样本率过低", "bad"))
        elif _flag(eval_row.get("q6_p90_misses_truth")):
            entries.append(("回测警告: q6 P90 低于结算 q6 价值", "warn"))
        if _flag(eval_row.get("layout_conflict")):
            root = str(eval_row.get("layout_conflict_root") or "footprint 存在重叠或越界")
            entries.append((f"布局诊断: {root}", "warn"))
        if _flag(eval_row.get("relaxed_exact_used")):
            entries.append(("约束诊断: exact 桶约束已放宽", "warn"))
        parts = []
        if eval_row.get("decision_value_p50_error") is not None:
            parts.append(f"决策P50误差 {_fmt_int(eval_row['decision_value_p50_error'])}")
        if eval_row.get("warehouse_p50_error") is not None:
            parts.append(f"仓储P50误差 {eval_row['warehouse_p50_error']}")
        if eval_row.get("stop_minus_final_value") is not None:
            parts.append(f"停止价-结算 {_fmt_int(eval_row['stop_minus_final_value'])}")
        if parts:
            entries.append(("评估: " + " / ".join(parts), "dim"))
    return entries


def _summary_lines(snapshot: dict) -> list[str]:
    return [line for line, _tag in _summary_entries(snapshot)]


def _summary_by_topic(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    panel = snapshot.get("panel") or {}
    rows = panel.get("summary_rows") or []
    return {
        str(row.get("topic") or ""): row
        for row in rows
        if isinstance(row, dict)
    }


def _category_focus_text(items: list[dict[str, Any]]) -> tuple[str, str]:
    counts = Counter(str(item.get("category_label") or item.get("category") or "?") for item in items)
    summary = " / ".join(f"{label}×{count}" for label, count in counts.most_common(4))
    details: list[str] = []
    for item in items[:5]:
        label = item.get("category_label") or item.get("category") or "?"
        quality = item.get("quality")
        q_text = f"Q{quality}" if quality is not None else "Q?"
        loc = item.get("local_index")
        pos = f"#{loc}" if loc is not None else ""
        shape = item.get("shape_key") or ""
        details.append(" ".join(str(part) for part in (label, q_text, shape, pos) if part))
    return summary, "；".join(details)


def _ui_contract_shadow_section(
    contract: dict[str, Any],
) -> tuple[str, str, str] | None:
    shadows = [
        shadow
        for shadow in contract.get("shadows", ()) or ()
        if isinstance(shadow, dict)
    ]
    if not shadows:
        return None
    active = [shadow for shadow in shadows if _flag(shadow.get("active"))]
    if active:
        selected = [
            shadow
            for shadow in active
            if shadow.get("display_mode") != "debug_only"
        ] or active
        details = []
        for shadow in selected[:3]:
            q6_p90 = shadow.get("q6_decision_value_p90")
            delta = shadow.get("q6_p90_delta")
            trials = shadow.get("trials")
            parts = [
                str(shadow.get("label") or "shadow"),
                f"q6P90 {_fmt_int(q6_p90)}" if q6_p90 is not None else "",
                f"Δ{_fmt_int(delta)}" if delta is not None else "",
                f"trials {trials}" if trials is not None else "",
            ]
            details.append(" ".join(part for part in parts if part))
        return ("Shadow 风险参考", "；".join(details), "只读参考，不改变正式出价")
    inactive_labels = [
        str(shadow.get("label") or "shadow")
        for shadow in shadows
        if not _flag(shadow.get("active"))
    ]
    return (
        "Shadow 状态",
        "未激活",
        " / ".join(inactive_labels[:3]),
    )


def _quality_count_text(counts: Any) -> str:
    mapping = _as_mapping(counts)
    if not mapping:
        return ""

    def sort_key(item: tuple[Any, Any]) -> tuple[int, str]:
        label = str(item[0])
        digits = "".join(ch for ch in label if ch.isdigit())
        return (int(digits) if digits else 99, label)

    return " / ".join(
        f"{str(label).upper()}×{count}"
        for label, count in sorted(mapping.items(), key=sort_key)
        if count
    )


def _ui_contract_minimap_section(
    contract: dict[str, Any],
) -> tuple[str, str, str] | None:
    minimap = contract.get("minimap") or {}
    if not isinstance(minimap, dict) or minimap.get("status") != "available":
        return None
    known_items = minimap.get("known_items") or 0
    drawable_items = minimap.get("drawable_items") or 0
    final_total_items = minimap.get("final_total_items")
    quality_counts = minimap.get("quality_counts") or {}
    category_counts = minimap.get("category_counts") or {}
    layout_complete = _flag(minimap.get("layout_complete"))
    settlement_layout = minimap.get("layout_source") == "settlement_inventory"
    source_label = (
        "结算全布局"
        if layout_complete
        else "结算布局"
        if settlement_layout
        else "赛中已知"
    )
    q_text = " / ".join(
        f"{label.upper()}×{count}" for label, count in list(quality_counts.items())[-3:]
    )
    c_text = " / ".join(
        f"{label}×{count}"
        for label, count in list(category_counts.items())[:4]
    )
    reveal_text = _quality_count_text(minimap.get("quality_reveal_counts"))
    unplaced_text = _quality_count_text(
        minimap.get("quality_reveal_unplaced_counts")
    )
    grid_note = _minimap_capacity_text(minimap, contract=contract)
    if minimap.get("scrollable"):
        grid_note += " / 需滚动"
    if settlement_layout and final_total_items:
        headline = f"{source_label} {drawable_items}/{final_total_items} 件"
    else:
        headline = f"{source_label} {known_items} 件"
    return (
        "MiniMap",
        headline + (f"  |  {q_text}" if q_text else ""),
        _join_parts(
            (
                c_text,
                f"公共品质 {reveal_text}" if reveal_text else "",
                f"未定位 {unplaced_text}" if unplaced_text else "",
                grid_note,
            )
        ),
    )


def _minimap_capacity_text(
    minimap: dict[str, Any],
    *,
    contract: dict[str, Any] | None = None,
) -> str:
    contract = contract or {}
    context = _as_mapping(contract.get("context"))
    baseline = _as_mapping(contract.get("baseline"))
    layout = _as_mapping(baseline.get("layout"))
    constraints = _as_mapping(contract.get("constraints"))
    summary = _as_mapping(constraints.get("summary"))
    truth = _as_mapping(contract.get("truth"))
    if minimap.get("layout_source") == "settlement_inventory":
        cells = context.get("inventory_cells") or summary.get(
            "input_warehouse_total_cells"
        ) or truth.get("total_cells")
        if cells is not None:
            return f"当前{cells}格"
    if summary.get("input_warehouse_total_cells") is not None:
        return f"当前{summary.get('input_warehouse_total_cells')}格"
    if layout.get("estimate"):
        return f"估格 {layout.get('estimate')}"
    if summary.get("input_warehouse_total_cells_approx") is not None:
        return f"估格 {summary.get('input_warehouse_total_cells_approx')}"
    return f"默认{minimap.get('default_cells', 130)}格"


def _ui_contract_fallback_section(
    contract: dict[str, Any],
) -> tuple[str, str, str] | None:
    fallback = contract.get("fallback") or {}
    if not isinstance(fallback, dict) or not _flag(fallback.get("active")):
        return None
    decision = fallback.get("decision") or {}
    if not isinstance(decision, dict):
        return None
    thresholds = " / ".join(
        part
        for part in (
            f"倍率 {decision.get('warehouse_multiplier')}"
            if decision.get("warehouse_multiplier")
            else "",
            f"探价 {decision.get('probe_bid')}" if decision.get("probe_bid") else "",
            f"防守 {decision.get('defend_bid')}" if decision.get("defend_bid") else "",
            f"可追(P90) {decision.get('attack_bid')}"
            if decision.get("attack_bid")
            else "",
            f"停止 {decision.get('stop_price')}" if decision.get("stop_price") else "",
        )
        if part
    )
    player_risks = "；".join(
        " ".join(
            part
            for part in (
                str(row.get("current_bid") or ""),
                str(row.get("risk_band") or ""),
            )
            if part
        )
        for row in decision.get("player_risks", ()) or ()
        if isinstance(row, dict)
    )
    detail = "；".join(
        part
        for part in (
            f"对手：{player_risks}" if player_risks else "",
            f"补信息：{decision.get('next_info_hint')}"
            if decision.get("next_info_hint")
            else "",
            str(decision.get("rationale") or ""),
        )
        if part
    )
    return (
        "Fallback 出价参考",
        thresholds or "已生成 map-prior 低置信参考",
        detail,
    )


def _ui_contract_constraints_section(
    contract: dict[str, Any],
) -> tuple[str, str, str] | None:
    constraints = contract.get("constraints") or {}
    if not isinstance(constraints, dict):
        return None
    summary = _ui_contract_constraints_summary(contract)
    if not summary:
        return None
    headline_parts: list[str] = []
    if summary.get("input_total_item_count") is not None:
        headline_parts.append(f"总件 {summary.get('input_total_item_count')}")
    if summary.get("input_warehouse_total_cells") is not None:
        headline_parts.append(f"总格 {summary.get('input_warehouse_total_cells')}")
    elif summary.get("input_warehouse_total_cells_approx") is not None:
        headline_parts.append(
            f"估格 {summary.get('input_warehouse_total_cells_approx')}"
        )
    if summary.get("known_grid_items"):
        headline_parts.append(f"已知 {summary.get('known_grid_items')} 件")

    quality_parts = []
    for key, label in (
        ("known_purple_item_count", "紫"),
        ("known_gold_item_count", "金"),
        ("known_red_item_count", "红"),
    ):
        count = summary.get(key)
        if count:
            quality_parts.append(f"{label}×{count}")

    detail_parts = []
    for key, label in (
        ("anchor_count", "锚点"),
        ("shape_target_count", "形状"),
        ("category_target_count", "分类"),
        ("category_exclusion_count", "反排"),
    ):
        count = summary.get(key)
        if count:
            detail_parts.append(f"{label}{count}")
    if summary.get("public_constraint_key"):
        detail_parts.append(str(summary.get("public_constraint_key")))
    if summary.get("map_alias_label"):
        detail_parts.append(str(summary.get("map_alias_label")))
    public_info = _as_mapping(constraints.get("public_info"))
    public_numeric_summary = str(
        public_info.get("public_numeric_summary") or ""
    ).strip()
    if public_numeric_summary:
        detail_parts.append(_short(public_numeric_summary, 96))

    if not headline_parts and not quality_parts and not detail_parts:
        return None
    headline = " / ".join(headline_parts or quality_parts)
    detail = " / ".join([*quality_parts, *detail_parts])
    return ("输入约束", headline, detail)


def _ui_contract_decision_section(
    contract: dict[str, Any],
) -> tuple[str, str, str] | None:
    baseline = _as_mapping(contract.get("baseline"))
    decision = _as_mapping(baseline.get("decision"))
    if not decision:
        return None
    headline = str(decision.get("action") or "暂无正式建议")
    detail = _join_parts(
        (
            f"最高 {decision.get('current_highest')}"
            if decision.get("current_highest")
            else "",
            f"风险 {decision.get('risk_band')}" if decision.get("risk_band") else "",
            f"倍率 {decision.get('warehouse_multiplier')}"
            if decision.get("warehouse_multiplier")
            else "",
            f"探价 {decision.get('probe_bid')}" if decision.get("probe_bid") else "",
            f"防守 {decision.get('defend_bid')}" if decision.get("defend_bid") else "",
            f"可追(P90) {decision.get('attack_bid')}"
            if decision.get("attack_bid")
            else "",
            f"停止 {decision.get('stop_price')}" if decision.get("stop_price") else "",
            (
                "来源 v3 practical"
                if baseline.get("source") == "v3_practical"
                else ""
            ),
            str(decision.get("evidence") or ""),
        )
    )
    title = "正式出价"
    if baseline.get("source") == "v3_practical":
        title = "正式出价 v3"
    return (title, headline, detail)


def _ui_contract_v2_reference_section(
    contract: dict[str, Any],
) -> tuple[str, str, str] | None:
    reference = _as_mapping(contract.get("v2_reference"))
    if not _flag(reference.get("available")):
        return None
    decision = _as_mapping(reference.get("decision"))
    if not decision:
        return None
    headline = str(decision.get("action") or "暂无 v2 建议")
    detail = _join_parts(
        (
            f"最高 {decision.get('current_highest')}"
            if decision.get("current_highest")
            else "",
            f"风险 {decision.get('risk_band')}" if decision.get("risk_band") else "",
            f"防守 {decision.get('defend_bid')}" if decision.get("defend_bid") else "",
            f"停止 {decision.get('stop_price')}" if decision.get("stop_price") else "",
            str(decision.get("evidence") or "v2 reference"),
            (
                "当前不影响正式出价"
                if reference.get("affects_bid") is False
                else "当前为正式出价来源"
            ),
        )
    )
    return ("v2 对照", headline, detail)


def _ui_contract_round_reference_section(
    contract: dict[str, Any],
) -> tuple[str, str, str] | None:
    context = _as_mapping(contract.get("context"))
    baseline = _as_mapping(contract.get("baseline"))
    posterior = _as_mapping(baseline.get("posterior"))
    round_no = _int_or_none(context.get("action_round") or context.get("round"))
    if round_no is None:
        return None
    multiplier = ROUND_WAREHOUSE_REFERENCE_MULTIPLIERS.get(round_no)
    if multiplier is None:
        return None
    p50 = _price_int(_range_p50(posterior.get("decision_value_range")))
    if p50 is None:
        return None
    reference = int(math.ceil(p50 / multiplier))
    return (
        "轮次仓位参考",
        f"R{round_no} 参考 {_fmt_int(reference)}",
        f"P50 {_fmt_int(p50)} ÷ {multiplier:g}；只作仓位/防守提示，不改变正式出价",
    )


def _ui_contract_posterior_section(
    contract: dict[str, Any],
) -> tuple[str, str, str] | None:
    baseline = _as_mapping(contract.get("baseline"))
    posterior = _as_mapping(baseline.get("posterior"))
    if not posterior:
        return None
    headline = _join_parts(
        (
            f"匹配 {posterior.get('match_text')}" if posterior.get("match_text") else "",
            str(posterior.get("status") or ""),
            f"q6样本 {posterior.get('q6_sample_rate')}"
            if posterior.get("q6_sample_rate")
            else "",
        )
    )
    detail = _join_parts(
        (
            f"决策 {posterior.get('decision_value_range')}"
            if posterior.get("decision_value_range")
            else "",
            f"raw {posterior.get('raw_value_range')}"
            if posterior.get("raw_value_range")
            else "",
            f"总格 {posterior.get('total_cells_range')}"
            if posterior.get("total_cells_range")
            else "",
            f"q6价值 {posterior.get('q6_decision_value_range')}"
            if posterior.get("q6_decision_value_range")
            else "",
            _q6_count_display(contract, known_prefix="已知红品")
            or (
                f"q6件数 {posterior.get('q6_count_range')}"
                if posterior.get("q6_count_range")
                else ""
            ),
            f"q6格数 {posterior.get('q6_cells_range')}"
            if posterior.get("q6_cells_range")
            else "",
            f"剩余空间 {posterior.get('remaining_cells_after_layout_range')}"
            if posterior.get("remaining_cells_after_layout_range")
            else "",
            f"空间压力 {posterior.get('q6_space_pressure_range')}"
            if posterior.get("q6_space_pressure_range")
            else "",
            f"溢出 {posterior.get('q6_space_overflow_rate')}"
            if posterior.get("q6_space_overflow_rate")
            else "",
        )
    )
    return ("后验概览", headline or "暂无匹配信息", detail)


def _ui_contract_layout_section(
    contract: dict[str, Any],
) -> tuple[str, str, str] | None:
    baseline = _as_mapping(contract.get("baseline"))
    layout = _as_mapping(baseline.get("layout"))
    if not layout:
        return None
    headline = _join_parts(
        (
            f"估计 {layout.get('estimate')}" if layout.get("estimate") else "",
            f"已知 {layout.get('known_cells')}格" if layout.get("known_cells") else "",
            str(layout.get("confidence") or ""),
        )
    )
    detail = _join_parts(
        (
            str(layout.get("stage") or ""),
            str(layout.get("risk") or ""),
        )
    )
    return ("布局概览", headline or "暂无布局估计", detail)


def _ui_contract_q6_risk_section(
    contract: dict[str, Any],
) -> tuple[str, str, str] | None:
    q6_risk = _as_mapping(contract.get("q6_risk_reference"))
    if not q6_risk:
        return None
    has_signal = any(
        q6_risk.get(key)
        for key in (
            "risk",
            "prior_gap",
            "prior_reference_p90",
            "practical_gate",
            "practical_reference_p90",
        )
    )
    if not has_signal:
        return None
    reference = (
        q6_risk.get("practical_reference_p90")
        or q6_risk.get("prior_reference_p90")
        or ""
    )
    headline = _join_parts(
        (
            "已触发" if _flag(q6_risk.get("risk")) else "未触发",
            str(q6_risk.get("practical_gate") or ""),
            f"参考P90 {reference}" if reference else "",
        )
    )
    detail = _join_parts(
        (
            str(q6_risk.get("prior_gap") or ""),
            f"display {q6_risk.get('display_mode')}"
            if q6_risk.get("display_mode")
            else "",
            "不影响正式出价"
            if q6_risk.get("affects_bid") is False
            else "",
        )
    )
    return ("q6 风险参考", headline, detail)


def _ui_contract_v3_practical_section(
    contract: dict[str, Any],
    *,
    detail: bool = False,
) -> tuple[str, str, str] | None:
    diagnostics = _as_mapping(contract.get("diagnostics"))
    practical = _as_mapping(diagnostics.get("v3_practical"))
    if not practical or not _flag(practical.get("available")):
        return None

    recommendation = str(practical.get("recommendation") or "")
    status = str(practical.get("status") or "")
    mode = str(practical.get("mode") or "")
    confidence = _v3_confidence_label(practical.get("confidence"))
    p50 = practical.get("formal_decision_value_p50")
    p90 = practical.get("formal_decision_value_p90")
    baseline_p50 = practical.get("baseline_formal_decision_value_p50")
    baseline_p90 = practical.get("baseline_formal_decision_value_p90")
    delta_p50 = practical.get("delta_formal_decision_value_p50")
    delta_p90 = practical.get("delta_formal_decision_value_p90")
    q6_p50 = practical.get("q6_formal_decision_value_p50")
    q6_p90 = practical.get("q6_formal_decision_value_p90")
    baseline_q6_p90 = practical.get("baseline_q6_formal_decision_value_p90")
    delta_q6 = practical.get("delta_q6_formal_decision_value_p50")
    delta_q6_p90 = practical.get("delta_q6_formal_decision_value_p90")
    total_p90 = practical.get("total_value_p90")
    q6_value_p90 = practical.get("q6_value_p90")
    raw_total_gap_p90 = _price_int(practical.get("raw_total_gap_to_formal_p90"))
    q6_raw_gap_p90 = _price_int(practical.get("q6_raw_gap_to_formal_p90"))
    read_only = (
        "只读参考，不影响正式出价"
        if not _flag(practical.get("affects_bid")) and not _flag(practical.get("active"))
        else "警告：v3 practical 不应进入正式出价"
    )

    if recommendation == "raise_watch":
        headline = "低估风险：参考上沿/谨慎加价"
    elif recommendation == "ceiling_watch":
        headline = "参考上沿：防低估但不建议直接加价"
    elif recommendation == "risk_watch":
        headline = "风险提示：证据/容量异常，谨慎参考"
    elif _flag(practical.get("candidate")):
        headline = "候选观察：未进入正式出价"
    else:
        headline = "未触发：baseline passthrough"
    if baseline_p90 is not None and p90 is not None:
        p90_text = f"正式P90 {_fmt_int(baseline_p90)} -> v3P90 {_fmt_int(p90)}"
    else:
        p90_text = f"v3P90 {_fmt_int(p90)}" if p90 is not None else ""
    value_parts = [
        f"v3P50 {_fmt_int(p50)}" if p50 is not None else "",
        p90_text,
        (
            f"正式P50 {_fmt_int(baseline_p50)}"
            if baseline_p50 is not None and detail
            else ""
        ),
        f"ΔP50 {_fmt_int(delta_p50)}" if delta_p50 is not None else "",
        f"ΔP90 {_fmt_int(delta_p90)}" if delta_p90 is not None else "",
    ]
    q6_parts = [
        f"q6P50 {_fmt_int(q6_p50)}" if q6_p50 is not None else "",
        f"q6P90 {_fmt_int(q6_p90)}" if q6_p90 is not None else "",
        (
            f"正式q6P90 {_fmt_int(baseline_q6_p90)}"
            if baseline_q6_p90 is not None and detail
            else ""
        ),
        f"Δq6 {_fmt_int(delta_q6)}" if delta_q6 is not None else "",
        f"Δq6P90 {_fmt_int(delta_q6_p90)}" if delta_q6_p90 is not None else "",
    ]
    upper_parts = [
        (
            f"仓库上限ΔP90 {_fmt_int(raw_total_gap_p90)}"
            if raw_total_gap_p90 is not None and raw_total_gap_p90 > 0
            else ""
        ),
        (
            f"q6上限ΔP90 {_fmt_int(q6_raw_gap_p90)}"
            if q6_raw_gap_p90 is not None and q6_raw_gap_p90 > 0
            else ""
        ),
        (
            f"仓库上限P90 {_fmt_int(total_p90)}"
            if detail and total_p90 is not None
            else ""
        ),
        (
            f"q6上限P90 {_fmt_int(q6_value_p90)}"
            if detail and q6_value_p90 is not None
            else ""
        ),
    ]
    value_text = _join_parts([*value_parts, *q6_parts, *upper_parts])
    if value_text:
        headline = f"{headline} | {value_text}"

    source_parts = [
        f"证据 {practical.get('source_lanes')}" if practical.get("source_lanes") else "",
        f"风险 {practical.get('risk_flags')}" if practical.get("risk_flags") else "",
        f"模式 {mode}" if mode else "",
        f"状态 {status}" if status and detail else "",
        f"置信 {confidence}" if confidence else "",
        read_only,
    ]
    if detail and practical.get("reason"):
        source_parts.append(str(practical.get("reason") or ""))
    return ("v3 实战参考", headline, _join_parts(source_parts))


def _ui_contract_action_section(
    contract: dict[str, Any],
) -> tuple[str, str, str] | None:
    actions = _as_mapping(contract.get("actions"))
    results = [
        row
        for row in actions.get("results", ()) or ()
        if isinstance(row, dict)
    ]
    sent = [
        row
        for row in actions.get("sent", ()) or ()
        if isinstance(row, dict)
    ]
    if not results and not sent:
        return None
    latest = results[0] if results else sent[0]
    tool = str(latest.get("tool") or latest.get("action_id") or "道具")
    result = str(latest.get("result") or "")
    revealed = str(latest.get("revealed_items") or "")
    revealed_summary = str(latest.get("revealed_summary") or "")
    if result:
        headline = f"{tool}: {result}"
    elif revealed_summary:
        headline = f"{tool}: {revealed_summary}"
    elif revealed and revealed != "0":
        headline = f"{tool}: 揭示 {revealed} 件"
    else:
        headline = f"{tool}: 已发送"
    details = []
    for row in results[:4]:
        row_tool = str(row.get("tool") or row.get("action_id") or "道具")
        row_result = str(row.get("result") or "")
        row_revealed = str(row.get("revealed_items") or "")
        row_summary = str(row.get("revealed_summary") or "")
        if row_result:
            details.append(f"{row_tool}={row_result}")
        elif row_summary:
            details.append(f"{row_tool}={row_summary}")
        elif row_revealed and row_revealed != "0":
            details.append(f"{row_tool}=揭示{row_revealed}件")
        else:
            details.append(f"{row_tool}=已返回")
    if not details and sent:
        details = [
            str(row.get("tool") or row.get("action_id") or "道具")
            for row in sent[:4]
        ]
    return ("最近道具", headline, "；".join(details))


def _ui_contract_truth_section(
    contract: dict[str, Any],
) -> tuple[str, str, str] | None:
    truth = _as_mapping(contract.get("truth"))
    if not truth:
        return None
    if not _flag(truth.get("available")):
        return ("结算/Truth", "实时结算前不可用", str(truth.get("source") or ""))
    q6 = _as_mapping(truth.get("q6"))
    top = _as_mapping(truth.get("top_item"))
    catalog_value_partial = truth.get("catalog_value_complete") is False
    value_label = "已知价值" if catalog_value_partial else "总值"
    headline = _join_parts(
        (
            f"{value_label} {_fmt_int(truth.get('total_value'))}"
            if truth.get("total_value") is not None
            else "",
            f"总件 {truth.get('total_items')}"
            if truth.get("total_items") is not None
            else "",
            f"总格 {truth.get('total_cells')}"
            if truth.get("total_cells") is not None
            else "",
        )
    )
    top_label = _join_parts(
        (
            str(top.get("name") or ""),
            f"Q{top.get('quality')}" if top.get("quality") is not None else "",
            f"{top.get('cells')}格" if top.get("cells") is not None else "",
            _fmt_int(top.get("value")) if top.get("value") is not None else "",
        ),
        sep=" ",
    )
    detail = _join_parts(
        (
            f"q6 {q6.get('count')}件/{q6.get('cells')}格/{_fmt_int(q6.get('value'))}"
            if any(q6.get(key) is not None for key in ("count", "cells", "value"))
            else "",
            f"最高 {top_label}" if top_label else "",
            "藏品尚未全部识别，当前显示已知部分"
            if catalog_value_partial
            else "",
            "" if catalog_value_partial else str(truth.get("source") or ""),
        )
    )
    return ("结算/Truth", headline or "已记录结算", detail)


def _ui_contract_size_bucket_section(
    contract: dict[str, Any],
) -> tuple[str, str, str] | None:
    diagnostics = _as_mapping(contract.get("diagnostics"))
    size_bucket = _as_mapping(diagnostics.get("size_bucket"))
    if not size_bucket:
        return None
    if not _flag(size_bucket.get("active")) and not _flag(
        size_bucket.get("reading_active")
    ):
        return None
    headline = _join_parts(
        (
            str(size_bucket.get("latest_reading_label") or ""),
            str(size_bucket.get("latest_target_label") or ""),
        )
    )
    details: list[str] = []
    for label in size_bucket.get("reading_labels", ()) or ():
        if label:
            details.append(f"读数 {label}")
    for label in size_bucket.get("target_labels", ()) or ():
        if label:
            details.append(f"推理 {label}")
    if _flag(size_bucket.get("inference_matches_reading")):
        details.append("读数与后验占格证据一致")
    elif _flag(size_bucket.get("active")) and _flag(size_bucket.get("reading_active")):
        details.append("读数与后验占格证据不一致（检查轮廓/件数）")
    if not headline and not details:
        return None
    return ("N格均价", headline or "占格均价证据", "；".join(details[:6]))


def _ui_contract_diagnostics_section(
    contract: dict[str, Any],
) -> tuple[str, str, str] | None:
    diagnostics = _as_mapping(contract.get("diagnostics"))
    if not diagnostics:
        return None
    layout = _as_mapping(diagnostics.get("layout"))
    q6 = _as_mapping(diagnostics.get("q6"))
    sampling = _as_mapping(diagnostics.get("sampling"))
    size_bucket = _as_mapping(diagnostics.get("size_bucket"))
    headline = _join_parts(
        (
            _short(diagnostics.get("posterior"), 74),
            "layout_conflict" if _flag(layout.get("conflict")) else "",
            "bottom_row_risk" if _flag(layout.get("bottom_row_risk")) else "",
            "q6_below_prior" if _flag(q6.get("below_drop_prior")) else "",
            "size_bucket" if _flag(size_bucket.get("active")) else "",
        )
    )
    detail = _join_parts(
        (
            f"bottom_row {layout.get('bottom_row')}"
            if layout.get("bottom_row") is not None
            else "",
            f"阈值 {layout.get('bottom_row_risk_threshold')}"
            if layout.get("bottom_row_risk_threshold") is not None
            else "",
            "q6 P90 漏真值" if _flag(q6.get("p90_misses_truth")) else "",
            f"top_size {q6.get('top_size_band')}" if q6.get("top_size_band") else "",
            "exact放宽" if _flag(sampling.get("relaxed_exact_used")) else "",
            f"n_trials {sampling.get('n_trials')}"
            if sampling.get("n_trials") is not None
            else "",
            f"shadow {sampling.get('shadow_trials')}"
            if sampling.get("shadow_trials") is not None
            else "",
            f"{sampling.get('processing_seconds')}s"
            if sampling.get("processing_seconds") is not None
            else "",
        )
    )
    if not headline and not detail:
        return None
    return ("诊断明细", headline or "无高亮诊断", detail)


def _ui_contract_shadow_detail_section(
    contract: dict[str, Any],
) -> tuple[str, str, str] | None:
    shadows = [
        shadow
        for shadow in contract.get("shadows", ()) or ()
        if isinstance(shadow, dict)
    ]
    if not shadows:
        return None
    active_count = sum(1 for shadow in shadows if _flag(shadow.get("active")))
    details: list[str] = []
    for shadow in shadows[:5]:
        details.append(
            _join_parts(
                (
                    str(shadow.get("label") or "shadow"),
                    str(shadow.get("status") or ""),
                    str(shadow.get("display_mode") or ""),
                    f"profile {shadow.get('evidence_profile')}"
                    if shadow.get("evidence_profile")
                    else "",
                    f"q6P90 {_fmt_int(shadow.get('q6_decision_value_p90'))}"
                    if shadow.get("q6_decision_value_p90") is not None
                    else "",
                    f"件{shadow.get('q6_count_p90')}"
                    if shadow.get("q6_count_p90") is not None
                    else "",
                    f"格{shadow.get('q6_cells_p90')}"
                    if shadow.get("q6_cells_p90") is not None
                    else "",
                    f"Δ{_fmt_int(shadow.get('q6_p90_delta'))}"
                    if shadow.get("q6_p90_delta") is not None
                    else "",
                ),
                sep=" ",
            )
        )
    return (
        "Shadow 明细",
        f"active {active_count}/{len(shadows)}，全部不影响正式出价",
        "；".join(detail for detail in details if detail),
    )


def _ui_contract_minimap_detail_section(
    contract: dict[str, Any],
) -> tuple[str, str, str] | None:
    minimap = _as_mapping(contract.get("minimap"))
    if minimap.get("status") != "available":
        return None
    base = _ui_contract_minimap_section(contract)
    if base is None:
        return None
    geometry_note = _join_parts(
        (
            f"{minimap.get('columns') or 10}列",
            f"rows_hint {minimap.get('rows_hint')}"
            if minimap.get("rows_hint") is not None
            else "",
            f"viewport {minimap.get('viewport_rows')}"
            if minimap.get("viewport_rows") is not None
            else "",
            "滚动" if minimap.get("scrollable") else "不滚动",
            "颜色块显示品质，不显示短名",
        )
    )
    detail = _join_parts((base[2], geometry_note))
    return (base[0], base[1], detail)


def _ui_contract_hover_sections(contract: dict[str, Any]) -> list[tuple[str, str, str]]:
    sections: list[tuple[str, str, str]] = []
    for section in (
        _ui_contract_decision_section(contract),
        _ui_contract_v2_reference_section(contract),
        _ui_contract_posterior_section(contract),
        _ui_contract_v3_practical_section(contract),
        _ui_contract_truth_section(contract),
        _ui_contract_minimap_section(contract),
        _ui_contract_round_reference_section(contract),
        _ui_contract_action_section(contract),
        _ui_contract_q6_risk_section(contract),
        _ui_contract_constraints_section(contract),
        _ui_contract_size_bucket_section(contract),
        _ui_contract_layout_section(contract),
        _ui_contract_fallback_section(contract),
    ):
        if section is not None:
            sections.append(section)
    return sections


def _ui_contract_detail_sections(contract: dict[str, Any]) -> list[tuple[str, str, str]]:
    sections: list[tuple[str, str, str]] = []
    for section in (
        _ui_contract_truth_section(contract),
        _ui_contract_decision_section(contract),
        _ui_contract_v2_reference_section(contract),
        _ui_contract_posterior_section(contract),
        _ui_contract_v3_practical_section(contract, detail=True),
        _ui_contract_minimap_detail_section(contract),
        _ui_contract_layout_section(contract),
        _ui_contract_constraints_section(contract),
        _ui_contract_action_section(contract),
        _ui_contract_size_bucket_section(contract),
        _ui_contract_q6_risk_section(contract),
        _ui_contract_fallback_section(contract),
        _ui_contract_round_reference_section(contract),
        _ui_contract_shadow_detail_section(contract),
        _ui_contract_diagnostics_section(contract),
    ):
        if section is not None:
            sections.append(section)
    return sections


def _interaction_layers(
    contract: dict[str, Any],
    *,
    metrics: list[tuple[str, str, str, str]],
    sections: list[tuple[str, str, str]],
    alerts: list[tuple[str, str]],
) -> dict[str, Any]:
    raw = _as_mapping(contract.get("interaction"))
    compact = _as_mapping(raw.get("compact"))
    hover = _as_mapping(raw.get("hover"))
    detail = _as_mapping(raw.get("detail"))
    hover_sections = _ui_contract_hover_sections(contract) if contract else []
    detail_sections = _ui_contract_detail_sections(contract) if contract else []

    if not hover_sections and sections:
        hover_sections = sections[:5]
    if not detail_sections and (sections or alerts):
        detail_sections = [*sections]
        if alerts:
            detail_sections.append(
                (
                    "风险与回测",
                    " / ".join(text for text, _tag in alerts[:5]),
                    "",
                )
            )

    return {
        "mini": {
            "purpose": compact.get("purpose") or "always_on_top_core_tips",
            "fields": tuple(compact.get("fields") or ()),
            "metrics": metrics[:4],
            "sections": sections[:4],
        },
        "hover": {
            "purpose": hover.get("purpose") or "expanded_quick_context",
            "fields": tuple(hover.get("fields") or ()),
            "enabled": bool(hover_sections),
            "sections": hover_sections[:8],
        },
        "detail": {
            "purpose": detail.get("purpose") or "click_to_open_full_reasoning",
            "fields": tuple(detail.get("fields") or ()),
            "enabled": bool(detail_sections),
            "collapsible": detail.get("collapsible", True) is not False,
            "renderers": tuple(detail.get("renderers") or ()),
            "sections": detail_sections[:14],
        },
    }


def _minimap_canvas_geometry(minimap: dict[str, Any]) -> dict[str, int]:
    columns = max(1, int(minimap.get("columns") or 10))
    viewport_rows = max(1, int(minimap.get("viewport_rows") or 13))
    max_rows = max(viewport_rows, int(minimap.get("max_rows") or 25))
    rows_hint = int(minimap.get("rows_hint") or viewport_rows)
    rows = max(viewport_rows, min(rows_hint, max_rows))
    visible_rows = min(viewport_rows, rows)
    cell = max(6, min(14, 196 // columns))
    return {
        "columns": columns,
        "rows": rows,
        "visible_rows": visible_rows,
        "cell": cell,
        "width": columns * cell + 1,
        "height": rows * cell + 1,
        "visible_height": visible_rows * cell + 1,
    }


def _ui_contract_alerts(contract: dict[str, Any]) -> list[tuple[str, str]]:
    alerts: list[tuple[str, str]] = []
    baseline = contract.get("baseline") or {}
    posterior = baseline.get("posterior") or {}
    if posterior.get("status") == "zero_match":
        alerts.append(
            (
                f"baseline 后验无匹配：{posterior.get('match_text') or '0/N'}，"
                "需复核约束或样本解析",
                "bad",
            )
        )
    fallback = contract.get("fallback") or {}
    if _flag(fallback.get("active")):
        alerts.append(
            (
                "v1 fallback 已生成低置信参考，不替代 baseline v2",
                "warn",
            )
        )
    q6_risk = contract.get("q6_risk_reference") or {}
    if _flag(q6_risk.get("risk")):
        reference = (
            q6_risk.get("practical_reference_p90")
            or q6_risk.get("prior_reference_p90")
            or "?"
        )
        gap = q6_risk.get("prior_gap") or "q6 prior gap"
        alerts.append((f"UI契约 q6 风险参考：{gap}，参考P90 {reference}", "warn"))
    if _flag(q6_risk.get("affects_bid")) or _flag(q6_risk.get("bid_floor_applied")):
        alerts.append(
            (
                "q6 风险参考不应影响正式出价：检查 affects_bid/bid_floor_applied",
                "bad",
            )
        )
    practical = _as_mapping(_as_mapping(contract.get("diagnostics")).get("v3_practical"))
    if practical:
        if _flag(practical.get("affects_bid")) or _flag(practical.get("active")):
            alerts.append(
                (
                    "v3 practical 应保持 shadow-only：检查 active/affects_bid",
                    "bad",
                )
            )
        elif (
            _flag(practical.get("available"))
            and practical.get("recommendation")
            in {"raise_watch", "ceiling_watch", "risk_watch"}
        ):
            recommendation_label = {
                "raise_watch": "提示低估风险",
                "ceiling_watch": "给出参考上沿",
                "risk_watch": "提示证据/容量风险",
            }.get(str(practical.get("recommendation")), "提示风险")
            baseline_p90 = practical.get("baseline_formal_decision_value_p90")
            practical_p90 = practical.get("formal_decision_value_p90")
            p90_text = (
                f"正式P90 {_fmt_int(baseline_p90)} -> v3P90 {_fmt_int(practical_p90)}"
                if baseline_p90 is not None and practical_p90 is not None
                else f"P90 {_fmt_int(practical_p90)}"
            )
            alerts.append(
                (
                    f"v3 实战参考{recommendation_label}："
                    f"{p90_text}，"
                    f"来源 {practical.get('source_lanes') or practical.get('source') or '?'}，"
                    "不改正式出价",
                    "warn",
                )
            )
    for shadow in contract.get("shadows", ()) or ():
        if not isinstance(shadow, dict) or not _flag(shadow.get("active")):
            continue
        if shadow.get("display_mode") != "risk_reference_candidate":
            continue
        alerts.append(
            (
                f"{shadow.get('label')} tail-risk shadow 已激活，q6P90 "
                f"{_fmt_int(shadow.get('q6_decision_value_p90'))}，不改正式出价",
                "warn",
            )
        )
    return alerts


def _standby_model(
    *,
    subtitle: str,
    detail: str = "",
    notes: list[tuple[str, str]] | None = None,
    decision_text: str = "等待对局开始",
) -> dict[str, Any]:
    decision_detail = detail or "等待实时数据"
    metrics = [
        ("P50估值", "--", "等待新状态", "dim"),
        ("防守价", "--", "等待新状态", "dim"),
        ("当前最高", "--", "等待新状态", "dim"),
        ("q6风险", "--", "等待新状态", "dim"),
    ]
    sections = [
        ("监听状态", decision_detail, "捕获到新对局状态后自动切换到推荐面板"),
        ("显示说明", "未显示旧局建议", "当前只展示默认等待面板"),
    ]
    for title, value in notes or []:
        sections.append((title, value, ""))
    return {
        "empty": False,
        "title": "BidKing Live",
        "subtitle": subtitle,
        "round": "?",
        "status": ("待机", "dim"),
        "decision": (decision_text, decision_detail, "dim"),
        "metrics": metrics,
        "sections": sections,
        "alerts": [],
        "interaction": {
            "mini": {
                "purpose": "standby_core_tips",
                "fields": (),
                "metrics": metrics,
                "sections": sections,
            },
            "hover": {
                "purpose": "disabled_until_live_data",
                "fields": (),
                "enabled": False,
                "sections": [],
            },
            "detail": {
                "purpose": "disabled_until_live_data",
                "fields": (),
                "enabled": False,
                "collapsible": True,
                "renderers": (),
                "sections": [],
            },
        },
        "minimap": {},
        "footer": "",
    }


def _snapshot_session_id(snapshot: dict[str, Any]) -> str | None:
    ui_contract = _as_mapping(snapshot.get("ui_contract"))
    context = _as_mapping(ui_contract.get("context"))
    session_id = context.get("session_id") or snapshot.get("session_id")
    return str(session_id) if session_id else None


def _capture_session_ahead_of_snapshot(
    capture_status: dict[str, Any] | None,
    snapshot: dict[str, Any],
) -> bool:
    if not isinstance(capture_status, dict):
        return False
    capture_session = capture_status.get("active_session_id")
    if not capture_session:
        return False
    snapshot_session = _snapshot_session_id(snapshot)
    if not snapshot_session:
        return True
    return str(capture_session) != snapshot_session


def _overlay_model(
    snapshot: dict[str, Any],
    *,
    review_snapshot: bool = False,
) -> dict[str, Any]:
    capture_status = _as_mapping(snapshot.get("_capture_source_status"))
    if not snapshot or snapshot.get("_no_artifact"):
        subtitle, detail, notes = _capture_waiting_copy(
            capture_status,
            fallback_subtitle="等待实时对局状态",
            fallback_detail="等待实时数据",
        )
        model = _standby_model(
            subtitle=subtitle,
            detail=detail,
            notes=notes,
        )
        model["empty"] = True
        return model

    age, stale = _age(snapshot)
    summary = _summary_by_topic(snapshot)
    bid = _first(snapshot.get("bid_rows"))
    v2 = _first(snapshot.get("v2_posterior_rows"))
    model_eval = snapshot.get("model_eval") or {}
    panel = snapshot.get("panel") or {}
    ui_contract = snapshot.get("ui_contract") or {}
    if not isinstance(ui_contract, dict):
        ui_contract = {}
    contract_context = ui_contract.get("context") or {}
    contract_source = ui_contract.get("source") or {}
    contract_baseline = ui_contract.get("baseline") or {}
    contract_decision = contract_baseline.get("decision") or {}
    contract_posterior = contract_baseline.get("posterior") or {}
    contract_layout = contract_baseline.get("layout") or {}
    contract_fallback = ui_contract.get("fallback") or {}
    fallback_decision = contract_fallback.get("decision") or {}
    fallback_posterior = contract_fallback.get("posterior") or {}
    contract_truth = _as_mapping(ui_contract.get("truth"))
    truth_q6 = _as_mapping(contract_truth.get("q6"))
    truth_top = _as_mapping(contract_truth.get("top_item"))
    phase = str(contract_context.get("phase") or snapshot.get("phase") or "")
    is_settled = phase == "settled" and _flag(contract_truth.get("available"))
    age_seconds = _snapshot_age_seconds(snapshot)
    if (
        not review_snapshot
        and _capture_session_ahead_of_snapshot(capture_status, snapshot)
        and _capture_has_fresh_session(capture_status)
    ):
        return _overlay_standby_from_capture(
            capture_status,
            fallback_subtitle="新局监听中",
            fallback_detail="抓包会话已更新，等待首个状态帧/推理快照",
            decision_text="新局监听中",
        )
    if (
        not review_snapshot
        and is_settled
        and _capture_session_ahead_of_snapshot(capture_status, snapshot)
        and _capture_has_fresh_session(capture_status)
    ):
        return _overlay_standby_from_capture(
            capture_status,
            fallback_subtitle="新局监听中",
            fallback_detail="抓包会话已更新，等待本局推理快照",
            decision_text="新局监听中",
        )
    if (
        not review_snapshot
        and is_settled
        and age_seconds is not None
        and age_seconds > SETTLED_RESULT_RETAIN_SECONDS
    ):
        if _capture_session_ahead_of_snapshot(capture_status, snapshot):
            return _overlay_standby_from_capture(
                capture_status,
                fallback_subtitle="新局监听中",
                fallback_detail="抓包会话已更新，等待本局推理快照",
                decision_text="新局监听中",
            )
        return _overlay_standby_from_capture(
            capture_status,
            fallback_subtitle="等待下一局开始",
            fallback_detail="上一局结算已结束",
        )
    if (
        not review_snapshot
        and not is_settled
        and age_seconds is not None
        and age_seconds > STALE_SNAPSHOT_SECONDS
    ):
        return _overlay_standby_from_capture(
            capture_status,
            fallback_subtitle="等待新的实时对局状态",
            fallback_detail="不显示过期旧局出价",
        )
    category_items = snapshot.get("category_grid_items") or []
    layout = _first(panel.get("layout_stages"))
    hero = str(contract_context.get("hero") or snapshot.get("hero") or "?").upper()
    map_id = contract_context.get("map_id") or snapshot.get("map_id") or "?"
    round_no = (
        contract_context.get("action_round")
        or contract_context.get("round")
        or snapshot.get("action_round")
        or snapshot.get("round")
        or "?"
    )
    title = f"{hero}  ·  map {map_id}  ·  R{round_no}"
    subtitle_parts = [
        f"文件 {contract_source.get('file') or snapshot.get('file') or '?'}",
        f"结算 {_fmt_int(contract_context.get('known_value_sum') or snapshot.get('known_value_sum'))}",
    ]
    if age:
        subtitle_parts.append(age)
    processing_seconds = _processing_seconds(snapshot, ui_contract)
    if processing_seconds is not None:
        subtitle_parts.append(f"推理 {processing_seconds:.1f}s")
    slow_processing = (
        processing_seconds is not None
        and processing_seconds > SLOW_PROCESSING_SECONDS
    )
    if stale:
        status = ("过期", "bad")
    elif slow_processing:
        status = ("慢", "warn")
    else:
        status = ("实时", "good")

    decision_text = "等待建议"
    decision_detail = ""
    decision_severity = "dim"
    decision_current = str(contract_decision.get("current_highest") or "")
    decision_risk = str(contract_decision.get("risk_band") or "")
    decision_probe = str(contract_decision.get("probe_bid") or "")
    decision_defend = str(contract_decision.get("defend_bid") or "")
    decision_stop = str(contract_decision.get("stop_price") or "")
    if bid:
        decision_current = decision_current or str(bid.get("当前最高") or "")
        decision_risk = decision_risk or str(bid.get("风险带") or "")
        decision_probe = decision_probe or str(bid.get("探价(P10)") or "")
        decision_defend = decision_defend or str(bid.get("防守价") or "")
        decision_stop = decision_stop or str(bid.get("停止价") or "")
    if is_settled:
        total_value = contract_truth.get("total_value")
        total_items = contract_truth.get("total_items")
        total_cells = contract_truth.get("total_cells")
        decision_text = f"结算 {_fmt_int(total_value)}"
        decision_detail = _join_parts(
            (
                f"总件 {total_items}" if total_items is not None else "",
                f"总格 {total_cells}" if total_cells is not None else "",
                (
                    f"红货 {truth_q6.get('count')}件/"
                    f"{truth_q6.get('cells')}格/"
                    f"{_fmt_int(truth_q6.get('value'))}"
                )
                if any(
                    truth_q6.get(key) is not None
                    for key in ("count", "cells", "value")
                )
                else "",
            ),
            sep="  |  ",
        )
        decision_severity = "good"
    elif contract_decision.get("action"):
        action = str(contract_decision.get("action") or "?")
        current = decision_current or "?"
        risk = decision_risk or "?"
        decision_text = action
        decision_detail = _join_parts(
            (
                f"最高 {current}",
                f"防守 {decision_defend}" if decision_defend else "",
                f"停止 {decision_stop}" if decision_stop else "",
                risk,
            ),
            sep="  |  ",
        )
        decision_severity = _severity_for_bid(f"{action} {risk}")
    elif (
        contract_posterior.get("status") == "zero_match"
        and _flag(contract_fallback.get("active"))
        and fallback_decision.get("action")
    ):
        action = str(fallback_decision.get("action") or "?")
        current = str(fallback_decision.get("current_highest") or "?")
        risk = str(fallback_decision.get("risk_band") or "?")
        stop = str(fallback_decision.get("stop_price") or "?")
        match_text = str(contract_posterior.get("match_text") or "0/N")
        decision_text = f"低置信参考：{action}"
        decision_detail = (
            f"v2匹配 {match_text}  |  最高 {current}  |  停止 {stop}  |  {risk}"
        )
        decision_severity = _severity_for_bid(f"{action} {risk}")
    elif contract_posterior.get("status") == "zero_match":
        match_text = str(contract_posterior.get("match_text") or "0/N")
        decision_text = "后验无匹配"
        decision_detail = f"匹配 {match_text}  |  复核公开约束/布局解析"
        decision_severity = "bad"
    elif bid:
        action = str(bid.get("建议") or "?")
        current = str(bid.get("当前最高") or "?")
        risk = str(bid.get("风险带") or "?")
        stop = str(bid.get("停止价") or "?")
        decision_text = action
        decision_detail = f"最高 {current}  |  停止 {stop}  |  {risk}"
        decision_severity = _severity_for_bid(f"{action} {risk}")
    elif contract_posterior.get("decision_value_range"):
        decision_text = "开局估值"
        decision_detail = _join_parts(
            (
                f"P50 {_range_p50(contract_posterior.get('decision_value_range')) or '?'}",
                f"匹配 {contract_posterior.get('match_text') or '?'}",
                "等待首个当前最高价后生成防守/停止价",
            ),
            sep="  |  ",
        )
        decision_severity = "normal"
    elif decision_row := summary.get("当前最高价是否可追"):
        decision_text = str(decision_row.get("conclusion") or "?")
        decision_detail = _short(decision_row.get("detail"), 96)
        decision_severity = _severity_for_bid(f"{decision_text} {decision_detail}")

    metrics: list[tuple[str, str, str, str]] = []
    if is_settled:
        metrics.append(
            (
                "结算总值",
                _fmt_int(contract_truth.get("total_value")),
                "最终准确值",
                "good",
            )
        )
        metrics.append(
            (
                "总件/总格",
                _join_parts(
                    (
                        str(contract_truth.get("total_items") or "?"),
                        str(contract_truth.get("total_cells") or "?"),
                    ),
                    sep=" / ",
                ),
                "结算 inventory",
                "normal",
            )
        )
        if any(truth_q6.get(key) is not None for key in ("count", "cells", "value")):
            metrics.append(
                (
                    "红货 q6",
                    _fmt_int(truth_q6.get("value")),
                    _join_parts(
                        (
                            f"{truth_q6.get('count')}件"
                            if truth_q6.get("count") is not None
                            else "",
                            f"{truth_q6.get('cells')}格"
                            if truth_q6.get("cells") is not None
                            else "",
                        ),
                        sep=" / ",
                    ),
                    "warn" if (truth_q6.get("value") or 0) else "normal",
                )
            )
        top_label = _join_parts(
            (
                str(truth_top.get("name") or ""),
                f"Q{truth_top.get('quality')}"
                if truth_top.get("quality") is not None
                else "",
            ),
            sep=" ",
        )
        if top_label or truth_top.get("value") is not None:
            metrics.append(
                (
                    "最高货",
                    _fmt_int(truth_top.get("value")),
                    top_label,
                    "warn" if truth_top.get("quality") == 6 else "normal",
                )
            )
    elif contract_posterior.get("decision_value_range"):
        metrics.append(
            (
                "P50估值",
                _range_p50(contract_posterior.get("decision_value_range")) or "?",
                _short(
                    f"P10/P50/P90 {contract_posterior.get('decision_value_range') or '?'}",
                    46,
                ),
                "normal",
            )
        )
    elif _flag(contract_fallback.get("active")) and fallback_posterior.get(
        "raw_value_range"
    ):
        metrics.append(
            (
                "fallback P50",
                _range_p50(fallback_posterior.get("raw_value_range")) or "?",
                _short(
                    f"v1低置信 / {fallback_posterior.get('match_text') or '?'}",
                    46,
                ),
                "warn",
            )
        )
    elif value_row := summary.get("当前价值区间"):
        metrics.append(
            (
                "P50估值",
                _range_p50(value_row.get("conclusion"))
                or str(value_row.get("conclusion") or "?"),
                _short(value_row.get("conclusion"), 46),
                "normal",
            )
        )
    if not is_settled and decision_defend:
        metrics.append(
            (
                "防守价",
                decision_defend,
                _short(
                    _join_parts(
                        (
                            f"探价 {decision_probe}" if decision_probe else "",
                            f"停止 {decision_stop}" if decision_stop else "",
                        )
                    ),
                    46,
                ),
                "warn" if decision_severity == "warn" else "normal",
            )
        )
    if not is_settled and decision_current:
        metrics.append(
            (
                "当前最高",
                decision_current,
                decision_risk,
                decision_severity,
            )
        )
    if not is_settled and (contract_posterior.get("q6_sample_rate") or v2):
        q6_rate = str(
            contract_posterior.get("q6_sample_rate")
            or v2.get("q6样本率")
            or "?"
        )
        q6_count_text = _q6_count_display(ui_contract, v2)
        q6_value_text = str(
            contract_posterior.get("q6_decision_value_range")
            or v2.get("q6决策价值 P10/P50/P90")
            or v2.get("q6价值 P10/P50/P90")
            or ""
        )
        q6_detail = _join_parts(
            (
                f"样本 {q6_rate}" if q6_count_text else "",
                q6_value_text,
            ),
            sep="  |  ",
        )
        q6_tag = "normal"
        try:
            q6_tag = "bad" if float(q6_rate.strip("%")) < 10 else "normal"
        except ValueError:
            pass
        if str(v2.get("q6先验缺口") or ""):
            q6_tag = "warn"
        metrics.append(
            (
                "红品数量",
                q6_count_text or f"样本 {q6_rate}",
                q6_detail,
                q6_tag,
            )
        )
    if (
        not is_settled
        and len(metrics) < 4
        and _flag(contract_fallback.get("active"))
        and fallback_posterior.get("total_cells_range")
    ):
        metrics.append(
            (
                "fallback仓储",
                str(fallback_posterior.get("total_cells_range") or "?"),
                _short(
                    f"{fallback_posterior.get('confidence') or '低置信'} / "
                    f"{fallback_posterior.get('match_text') or '?'}",
                    46,
                ),
                "warn",
            )
        )
    elif not is_settled and len(metrics) < 4 and (
        warehouse_row := summary.get("当前仓储区间")
    ):
        metrics.append(
            (
                "仓储",
                str(warehouse_row.get("conclusion") or "?"),
                _short(warehouse_row.get("detail"), 46),
                "normal",
            )
        )
    if not is_settled and len(metrics) < 4 and (contract_layout.get("estimate") or layout):
        layout_estimate = contract_layout.get("estimate") or layout.get("estimate")
        layout_known = contract_layout.get("known_cells") or layout.get("known_cells")
        layout_confidence = contract_layout.get("confidence") or layout.get("confidence")
        layout_risk = contract_layout.get("risk") or layout.get("risk")
        metrics.append(
            (
                "布局",
                str(layout_estimate or "?"),
                f"已知 {layout_known or '?'}格 · {layout_confidence or '?'}",
                "warn" if layout_risk not in ("", "低", "低风险") else "normal",
            )
        )
    sections: list[tuple[str, str, str]] = []
    truth_section = _ui_contract_truth_section(ui_contract)
    if is_settled and truth_section is not None:
        sections.append(truth_section)
    if not is_settled and (tool_row := summary.get("下一次优先使用道具")):
        sections.append(
            (
                "下一步道具",
                str(tool_row.get("conclusion") or ""),
                _short(tool_row.get("detail"), 110),
            )
        )
    if category_items:
        category_summary, category_detail = _category_focus_text(category_items)
        sections.append(("鉴影命中", category_summary, _short(category_detail, 118)))
    action_section = _ui_contract_action_section(ui_contract)
    if action_section is not None:
        sections.append(action_section)
    fallback_section = _ui_contract_fallback_section(ui_contract)
    if fallback_section is not None:
        sections.append(
            (
                fallback_section[0],
                _short(fallback_section[1], 118),
                _short(fallback_section[2], 168),
            )
        )
    minimap_section = _ui_contract_minimap_section(ui_contract)
    if minimap_section is not None:
        sections.append(minimap_section)
    constraints_section = _ui_contract_constraints_section(ui_contract)
    if constraints_section is not None:
        sections.append(constraints_section)
    practical_section = _ui_contract_v3_practical_section(ui_contract)
    if practical_section is not None and "低估风险" in practical_section[1]:
        sections.append(
            (
                practical_section[0],
                _short(practical_section[1], 118),
                _short(practical_section[2], 168),
            )
        )
    shadow_section = _ui_contract_shadow_section(ui_contract)
    if shadow_section is not None:
        sections.append(shadow_section)
    diagnostics = str(v2.get("诊断") or "")
    if diagnostics:
        sections.append(("后验诊断", _short(diagnostics, 118), ""))
    if layout and layout.get("risk"):
        sections.append(("布局风险", _short(layout.get("risk"), 118), ""))

    alerts: list[tuple[str, str]] = []
    if stale:
        alerts.append(("snapshot 超过 120 秒未更新，检查 Fatbeans 导出或 monitor 进程", "bad"))
    if slow_processing:
        alerts.append(
            (
                f"本次推理耗时 {processing_seconds:.1f}s；若连续出现，优先考虑降低 "
                "n_trials 或 baseline-first / shadow 后台补齐",
                "warn",
            )
        )
    if _flag(model_eval.get("q6_false_low_risk")):
        alerts.append(("真实有红货，但后验 q6 样本率过低", "bad"))
    elif _flag(model_eval.get("q6_p90_misses_truth")):
        alerts.append(("q6 P90 低于结算 q6 价值", "warn"))
    if not ui_contract and v2 and str(v2.get("q6先验缺口") or ""):
        gap = str(v2.get("q6先验缺口") or "")
        floor = str(v2.get("q6先验风险参考") or "")
        practical = str(v2.get("q6实战参考P90") or "")
        suffix = (
            f"，实战参考P90 {practical}"
            if practical
            else f"，参考上界 {floor}"
            if floor
            else ""
        )
        alerts.append((f"q6 件数/格数低于先验：{gap}{suffix}", "warn"))
    if _flag(model_eval.get("layout_conflict")):
        root = str(model_eval.get("layout_conflict_root") or "footprint 存在重叠或越界")
        alerts.append((root, "warn"))
    if _flag(model_eval.get("relaxed_exact_used")):
        alerts.append(("exact 桶约束已放宽", "warn"))
    alerts.extend(_ui_contract_alerts(ui_contract))

    eval_parts = []
    if model_eval.get("decision_value_p50_error") is not None:
        eval_parts.append(f"决策P50误差 {_fmt_int(model_eval['decision_value_p50_error'])}")
    if model_eval.get("warehouse_p50_error") is not None:
        eval_parts.append(f"仓储P50误差 {model_eval['warehouse_p50_error']}")
    if model_eval.get("stop_minus_final_value") is not None:
        eval_parts.append(f"停止价-结算 {_fmt_int(model_eval['stop_minus_final_value'])}")

    interaction = _interaction_layers(
        ui_contract,
        metrics=metrics,
        sections=sections,
        alerts=alerts,
    )
    raw_minimap = ui_contract.get("minimap")
    if isinstance(raw_minimap, dict) and raw_minimap.get("status") == "available":
        minimap_model = dict(raw_minimap)
        minimap_model["capacity_text"] = _minimap_capacity_text(
            minimap_model,
            contract=ui_contract,
        )
    else:
        minimap_model = {}

    return {
        "empty": False,
        "title": title,
        "subtitle": "  ·  ".join(subtitle_parts),
        "round": round_no,
        "status": status,
        "decision": (decision_text, decision_detail, decision_severity),
        "metrics": metrics,
        "sections": sections,
        "alerts": alerts,
        "interaction": interaction,
        "minimap": minimap_model,
        "footer": " / ".join(eval_parts),
    }


class Overlay:
    def __init__(
        self,
        root: tk.Tk,
        snapshot_path: Path,
        interval_ms: int,
        *,
        demo: bool = False,
        review_snapshot: bool = False,
        exit_when_pids: tuple[int, ...] = (),
    ) -> None:
        self.root = root
        self.snapshot_path = snapshot_path
        self.interval_ms = interval_ms
        self.demo = demo
        self.review_snapshot = review_snapshot
        self._error_log_path = snapshot_path.parent / "overlay.errors.log"
        self._logger = logging.getLogger("bidking.overlay")
        if not self._logger.handlers:
            self._logger.setLevel(logging.INFO)
            handler = logging.FileHandler(
                self._error_log_path,
                encoding="utf-8",
            )
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(message)s")
            )
            self._logger.addHandler(handler)
        self.exit_when_pids = tuple(pid for pid in exit_when_pids if pid > 0)
        self._last_snapshot_signature: tuple[Any, ...] | None = None
        self._last_snapshot: dict[str, Any] | None = None
        self._last_stale_state: bool | None = None
        self._demo_snapshot = _demo_snapshot() if demo else None
        self._current_model: dict[str, Any] | None = None
        self._detail_open = False
        self._hover_window: tk.Toplevel | None = None
        self._last_hover_position: tuple[int, int] | None = None
        self._last_click_time: int | None = None
        self.user_closed = False
        self._compact_geometry = _default_window_geometry(
            root.winfo_screenwidth(),
            root.winfo_screenheight(),
        )
        root.title("BidKing Live")
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.96)
        root.geometry(self._compact_geometry)
        root.minsize(COMPACT_MIN_WIDTH, COMPACT_MIN_HEIGHT)
        root.configure(bg=BG)
        self.canvas = tk.Canvas(root, bg=BG, highlightthickness=0, bd=0)
        self.scrollbar = tk.Scrollbar(
            root,
            orient="vertical",
            command=self.canvas.yview,
        )
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.frame = tk.Frame(self.canvas, bg=BG, padx=8, pady=8)
        self._frame_window = self.canvas.create_window(
            (0, 0),
            window=self.frame,
            anchor="nw",
        )
        self.frame.bind(
            "<Configure>",
            lambda _event: self.canvas.configure(
                scrollregion=self.canvas.bbox("all")
            ),
        )
        self.canvas.bind(
            "<Configure>",
            lambda event: self.canvas.itemconfigure(
                self._frame_window,
                width=event.width,
            ),
        )
        self.canvas.bind_all(
            "<MouseWheel>",
            lambda event: self._scroll_canvas(self.canvas, event),
        )
        root.protocol("WM_DELETE_WINDOW", self._on_user_close)
        self.refresh()

    def _on_user_close(self) -> None:
        self.user_closed = True
        self.root.destroy()

    def _window_origin(self) -> tuple[int, int]:
        try:
            return max(0, self.root.winfo_x()), max(0, self.root.winfo_y())
        except tk.TclError:
            return 40, 80

    def _resize_for_mode(self) -> None:
        def resize() -> None:
            if self._detail_open:
                self.root.update_idletasks()
                width, height = _detail_window_size(
                    self.root.winfo_screenwidth(),
                    self.root.winfo_screenheight(),
                    requested_width=self.frame.winfo_reqwidth(),
                    requested_height=self.frame.winfo_reqheight(),
                )
                x, y = self._window_origin()
                self.root.geometry(f"{width}x{height}+{x}+{y}")
                return
            x, y = self._window_origin()
            width_height = self._compact_geometry.split("+", 1)[0]
            self.root.geometry(f"{width_height}+{x}+{y}")

        self.root.after_idle(resize)

    def _scroll_canvas(self, canvas: tk.Canvas, event: tk.Event) -> str:
        canvas.yview_scroll(
            -int(event.delta / 120),
            "units",
        )
        return "break"

    def _clear(self) -> None:
        for child in self.frame.winfo_children():
            child.destroy()

    def _label(
        self,
        parent: tk.Widget,
        text: str,
        *,
        fg: str = TEXT,
        bg: str = PANEL,
        font: tuple[str, int, str] | tuple[str, int] = ("Microsoft YaHei UI", 10),
        anchor: str = "w",
        wraplength: int = 0,
    ) -> tk.Label:
        return tk.Label(
            parent,
            text=text,
            fg=fg,
            bg=bg,
            font=font,
            anchor=anchor,
            justify="left",
            wraplength=wraplength,
        )

    def _card(self, parent: tk.Widget, *, bg: str = PANEL) -> tk.Frame:
        frame = tk.Frame(parent, bg=bg, bd=1, relief="solid", highlightthickness=1)
        frame.configure(highlightbackground=BORDER, highlightcolor=BORDER)
        return frame

    def _canvas_scroll_fraction(self) -> float:
        try:
            return _clamp_scroll_fraction(self.canvas.yview()[0])
        except tk.TclError:
            return 0.0

    def _restore_canvas_scroll(self, fraction: float) -> None:
        fraction = _clamp_scroll_fraction(fraction)

        def restore() -> None:
            try:
                self.canvas.yview_moveto(fraction)
            except tk.TclError:
                pass

        self.root.after_idle(restore)

    def _render_section_rows(
        self,
        parent: tk.Widget,
        sections: list[tuple[str, str, str]],
        *,
        bg: str,
        limit: int,
        wraplength: int,
        compact: bool = False,
    ) -> None:
        for title, value, detail in sections[:limit]:
            style = _section_style(title, value, detail)
            row = tk.Frame(parent, bg=bg)
            row.pack(fill="x", pady=(5 if compact else 8, 0))
            tk.Frame(row, width=3, bg=style["color"]).pack(side="left", fill="y")
            body = tk.Frame(row, bg=bg, padx=7, pady=(1 if compact else 2))
            body.pack(side="left", fill="x", expand=True)
            header = tk.Frame(body, bg=bg)
            header.pack(fill="x")
            tk.Label(
                header,
                text=style["badge"],
                fg=BG,
                bg=style["color"],
                font=("Microsoft YaHei UI", 7, "bold"),
                padx=5,
                pady=1,
            ).pack(side="left")
            self._label(
                header,
                str(title),
                fg=style["color"],
                bg=bg,
                font=("Microsoft YaHei UI", 8 if compact else 9, "bold"),
            ).pack(side="left", padx=(6, 0))
            if value:
                self._label(
                    body,
                    str(value),
                    fg=TEXT,
                    bg=bg,
                    font=("Microsoft YaHei UI", 8 if compact else 9, "bold"),
                    wraplength=wraplength,
                ).pack(anchor="w", pady=(2, 0))
            if detail:
                self._label(
                    body,
                    str(detail),
                    fg=MUTED,
                    bg=bg,
                    font=("Microsoft YaHei UI", 8),
                    wraplength=wraplength,
                ).pack(anchor="w")

    def _render_decision_chips(
        self,
        parent: tk.Widget,
        detail: str,
        *,
        severity: str,
        bg: str,
        wraplength: int,
    ) -> None:
        chips = [part.strip() for part in str(detail or "").split("  |  ") if part.strip()]
        if not chips:
            return
        row = tk.Frame(parent, bg=bg)
        row.pack(fill="x", pady=(5, 0))
        for chip_text in chips[:4]:
            style = _chip_style(chip_text, severity)
            tk.Label(
                row,
                text=_short(chip_text, 42),
                fg=style["fg"],
                bg=style["bg"],
                font=("Microsoft YaHei UI", 9, "bold"),
                padx=7,
                pady=3,
                wraplength=wraplength,
                justify="left",
            ).pack(side="left", padx=(0, 5), pady=(0, 4))

    def _render_hover_minimap(
        self,
        parent: tk.Widget,
        minimap: dict[str, Any],
    ) -> None:
        if minimap.get("status") != "available":
            return
        card = tk.Frame(parent, bg=PANEL_SOFT)
        card.pack(anchor="w", fill="x", pady=(6, 2))
        layout_complete = _flag(minimap.get("layout_complete"))
        settlement_layout = minimap.get("layout_source") == "settlement_inventory"
        source_label = (
            "结算全布局"
            if layout_complete
            else "结算布局"
            if settlement_layout
            else "赛中已知"
        )
        if settlement_layout and minimap.get("final_total_items"):
            count_text = (
                f"{minimap.get('drawable_items', 0)}/"
                f"{minimap.get('final_total_items')} 件"
            )
        else:
            count_text = f"{minimap.get('known_items', 0)} 件"
        head = _join_parts(
            (
                f"MiniMap {source_label} {count_text}",
                str(minimap.get("capacity_text") or _minimap_capacity_text(minimap)),
            )
        )
        self._label(
            card,
            head,
            fg=ACCENT,
            bg=PANEL_SOFT,
            font=("Microsoft YaHei UI", 8, "bold"),
        ).pack(anchor="w")
        canvas_frame = tk.Frame(card, bg=PANEL_SOFT)
        canvas_frame.pack(anchor="w", pady=(4, 0))
        canvas = tk.Canvas(
            canvas_frame,
            bg=PANEL,
            highlightthickness=0,
            bd=0,
        )
        geometry = _minimap_canvas_geometry(minimap)
        scrollable = minimap.get("scrollable") or (
            geometry["height"] > geometry["visible_height"]
        )
        if scrollable:
            scrollbar = tk.Scrollbar(
                canvas_frame,
                orient="vertical",
                command=canvas.yview,
            )
            canvas.configure(yscrollcommand=scrollbar.set)
            canvas.pack(side="left")
            scrollbar.pack(side="right", fill="y")
            canvas.bind(
                "<MouseWheel>",
                lambda event: self._scroll_canvas(canvas, event),
            )
        else:
            canvas.pack(side="left")
        self._draw_minimap(canvas, minimap)
        quality_counts = _as_mapping(minimap.get("quality_counts"))
        q_text = _join_parts(
            (
                f"紫×{quality_counts.get('q4', 0)}",
                f"金×{quality_counts.get('q5', 0)}",
                f"红×{quality_counts.get('q6', 0)}",
            ),
            sep="  ",
        )
        if q_text:
            self._label(
                card,
                q_text,
                fg=MUTED,
                bg=PANEL_SOFT,
                font=("Microsoft YaHei UI", 8),
            ).pack(anchor="w", pady=(3, 0))

    def _position_hover(self, event: tk.Event) -> None:
        if self._hover_window is None:
            return
        try:
            self._hover_window.update_idletasks()
            width = max(1, self._hover_window.winfo_reqwidth())
            height = max(1, self._hover_window.winfo_reqheight())
            x, y = _bounded_popup_position(
                pointer_x=int(event.x_root),
                pointer_y=int(event.y_root),
                popup_width=width,
                popup_height=height,
                screen_width=self.root.winfo_screenwidth(),
                screen_height=self.root.winfo_screenheight(),
            )
            if self._last_hover_position is not None:
                last_x, last_y = self._last_hover_position
                if (
                    abs(x - last_x) < HOVER_MOVE_DEADZONE
                    and abs(y - last_y) < HOVER_MOVE_DEADZONE
                ):
                    return
            self._hover_window.geometry(f"+{x}+{y}")
            self._last_hover_position = (x, y)
        except tk.TclError:
            self._hover_window = None
            self._last_hover_position = None

    def _show_hover(self, event: tk.Event) -> None:
        if self._detail_open or self._current_model is None:
            self._hide_hover()
            return
        hover = _as_mapping(
            _as_mapping(self._current_model.get("interaction")).get("hover")
        )
        sections = hover.get("sections") or []
        if not hover.get("enabled") or not sections:
            return
        minimap = _as_mapping(self._current_model.get("minimap"))
        text_sections = [
            section
            for section in sections
            if not (minimap and section[0] == "MiniMap")
        ]
        if not text_sections and not minimap:
            return
        try:
            exists = self._hover_window is not None and self._hover_window.winfo_exists()
        except tk.TclError:
            exists = False
            self._hover_window = None
        if not exists:
            window = tk.Toplevel(self.root)
            window.withdraw()
            window.overrideredirect(True)
            window.attributes("-topmost", True)
            window.configure(bg=BORDER)
            body = tk.Frame(window, bg=PANEL_SOFT, padx=10, pady=8)
            body.pack(fill="both", expand=True, padx=1, pady=1)
            self._label(
                body,
                "悬浮局面",
                fg=ACCENT,
                bg=PANEL_SOFT,
                font=("Microsoft YaHei UI", 9, "bold"),
            ).pack(anchor="w")
            if minimap and text_sections:
                content = tk.Frame(body, bg=PANEL_SOFT)
                content.pack(fill="both", expand=True)
                text_col = tk.Frame(content, bg=PANEL_SOFT)
                text_col.pack(side="left", fill="both", expand=True)
                minimap_col = tk.Frame(content, bg=PANEL_SOFT)
                minimap_col.pack(side="right", anchor="ne", padx=(10, 0))
                self._render_section_rows(
                    text_col,
                    bg=PANEL_SOFT,
                    sections=text_sections,
                    limit=6,
                    wraplength=360,
                    compact=True,
                )
                self._render_hover_minimap(minimap_col, minimap)
            else:
                if text_sections:
                    self._render_section_rows(
                        body,
                        bg=PANEL_SOFT,
                        sections=text_sections,
                        limit=6,
                        wraplength=460,
                        compact=True,
                    )
                if minimap:
                    self._render_hover_minimap(body, minimap)
            self._hover_window = window
            self._last_hover_position = None
            window.bind("<Leave>", self._schedule_hide_hover, add="+")
        self._position_hover(event)
        if self._hover_window is not None:
            self._hover_window.deiconify()

    def _hide_hover(self) -> None:
        if self._hover_window is None:
            return
        try:
            self._hover_window.destroy()
        except tk.TclError:
            pass
        self._hover_window = None
        self._last_hover_position = None

    def _is_inside_root(self, widget: tk.Widget | None) -> bool:
        while widget is not None:
            if widget == self.root:
                return True
            widget = widget.master
        return False

    def _is_inside_hover(self, widget: tk.Widget | None) -> bool:
        if self._hover_window is None:
            return False
        while widget is not None:
            if widget == self._hover_window:
                return True
            widget = widget.master
        return False

    def _hide_hover_if_pointer_outside(self) -> None:
        try:
            widget = self.root.winfo_containing(
                self.root.winfo_pointerx(),
                self.root.winfo_pointery(),
            )
        except tk.TclError:
            widget = None
        if not self._is_inside_root(widget) and not self._is_inside_hover(widget):
            self._hide_hover()

    def _schedule_hide_hover(self, _event: tk.Event) -> None:
        self.root.after(120, self._hide_hover_if_pointer_outside)

    def _toggle_detail(self, event: tk.Event) -> None:
        if self._current_model is None:
            return
        detail = _as_mapping(
            _as_mapping(self._current_model.get("interaction")).get("detail")
        )
        if not detail.get("enabled"):
            return
        event_time = int(getattr(event, "time", 0) or 0)
        if event_time and event_time == self._last_click_time:
            return
        self._last_click_time = event_time
        self._detail_open = not self._detail_open
        self._hide_hover()
        self._render(self._current_model)

    def _bind_layer_events(self, widget: tk.Widget) -> None:
        if not isinstance(widget, tk.Scrollbar):
            widget.bind("<Enter>", self._show_hover, add="+")
            widget.bind("<Motion>", self._position_hover, add="+")
            widget.bind("<Leave>", self._schedule_hide_hover, add="+")
            widget.bind("<Button-1>", self._toggle_detail, add="+")
        for child in widget.winfo_children():
            self._bind_layer_events(child)

    def _render_detail_panel(
        self,
        parent: tk.Widget,
        model: dict[str, Any],
    ) -> None:
        detail = _as_mapping(_as_mapping(model.get("interaction")).get("detail"))
        sections = detail.get("sections") or []
        if not detail.get("enabled") or not sections:
            return
        card = self._card(parent, bg=PANEL_SOFT)
        card.pack(fill="x", pady=(10, 0))
        body = tk.Frame(card, bg=PANEL_SOFT, padx=12, pady=10)
        body.pack(fill="x")
        header = tk.Frame(body, bg=PANEL_SOFT)
        header.pack(fill="x")
        self._label(
            header,
            "点击全量详情（再次点击收起）",
            fg=ACCENT,
            bg=PANEL_SOFT,
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(side="left", anchor="w")
        renderer_note = ""
        renderers = detail.get("renderers") or ()
        if renderers:
            names = [
                str(renderer.get("name"))
                for renderer in renderers
                if isinstance(renderer, dict) and renderer.get("name")
            ]
            renderer_note = " / ".join(names)
        if renderer_note:
            self._label(
                header,
                renderer_note,
                fg=MUTED,
                bg=PANEL_SOFT,
                font=("Microsoft YaHei UI", 8),
            ).pack(side="right", anchor="e")
        self._render_section_rows(
            body,
            sections,
            bg=PANEL_SOFT,
            limit=len(sections),
            wraplength=800,
        )

    def _draw_unknown_quality_fill(
        self,
        canvas: tk.Canvas,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        *,
        color: str,
    ) -> None:
        width = max(1, x1 - x0)
        height = max(1, y1 - y0)
        step = max(4, min(width, height) // 3)
        for start_x in range(x0 - height, x1, step):
            clipped_start_x = max(x0, start_x)
            clipped_end_x = min(x1, start_x + height)
            start_y = y1 - (clipped_start_x - start_x)
            end_y = y1 - (clipped_end_x - start_x)
            if clipped_start_x < clipped_end_x and y0 <= end_y <= start_y <= y1:
                canvas.create_line(
                    clipped_start_x,
                    start_y,
                    clipped_end_x,
                    end_y,
                    fill=color,
                    width=1,
                )

    def _draw_minimap(self, canvas: tk.Canvas, minimap: dict[str, Any]) -> None:
        geometry = _minimap_canvas_geometry(minimap)
        columns = geometry["columns"]
        rows = geometry["rows"]
        cell = geometry["cell"]
        width = geometry["width"]
        height = geometry["height"]
        canvas.configure(
            width=width,
            height=geometry["visible_height"],
            scrollregion=(0, 0, width, height),
        )
        for col in range(columns + 1):
            x = col * cell
            canvas.create_line(x, 0, x, height, fill=BORDER)
        for row in range(rows + 1):
            y = row * cell
            canvas.create_line(0, y, width, y, fill=BORDER)
        for item in minimap.get("items", ()) or ():
            if not isinstance(item, dict):
                continue
            row = item.get("row")
            col = item.get("col")
            if row is None or col is None:
                continue
            try:
                row_i = int(row)
                col_i = int(col)
                item_w = max(1, int(item.get("width") or 1))
                item_h = max(1, int(item.get("height") or 1))
            except (TypeError, ValueError):
                continue
            if row_i < 1 or col_i < 1 or row_i > rows:
                continue
            x0 = (col_i - 1) * cell + 1
            y0 = (row_i - 1) * cell + 1
            x1 = min(columns * cell, (col_i - 1 + item_w) * cell) - 1
            y1 = min(rows * cell, (row_i - 1 + item_h) * cell) - 1
            style = _quality_style(item.get("quality"))
            render_mode = str(item.get("render_mode") or "footprint")
            if render_mode == "marker":
                pad = max(2, cell // 4)
                mx0 = min(x1 - 1, x0 + pad)
                my0 = min(y1 - 1, y0 + pad)
                mx1 = max(mx0 + 1, x1 - pad)
                my1 = max(my0 + 1, y1 - pad)
                options = {
                    "fill": style["fill"],
                    "outline": style["outline"],
                    "width": 1,
                }
                canvas.create_oval(
                    mx0,
                    my0,
                    mx1,
                    my1,
                    **options,
                )
            else:
                options = {
                    "fill": style["fill"],
                    "outline": style["outline"],
                }
                if style.get("stipple"):
                    options["stipple"] = style["stipple"]
                canvas.create_rectangle(
                    x0,
                    y0,
                    x1,
                    y1,
                    **options,
                )
                if style.get("unknown"):
                    self._draw_unknown_quality_fill(
                        canvas,
                        x0,
                        y0,
                        x1,
                        y1,
                        color=style["outline"],
                    )

    def _render(self, model: dict[str, Any]) -> None:
        previous_scroll = (
            self._canvas_scroll_fraction()
            if self._current_model is not None
            else 0.0
        )
        self._hide_hover()
        self._current_model = model
        self._clear()
        interaction = _as_mapping(model.get("interaction"))
        mini = _as_mapping(interaction.get("mini"))
        hover = _as_mapping(interaction.get("hover"))
        detail = _as_mapping(interaction.get("detail"))
        header = tk.Frame(self.frame, bg=BG)
        header.pack(fill="x")
        title_box = tk.Frame(header, bg=BG)
        title_box.pack(side="left", fill="x", expand=True)
        self._label(
            title_box,
            model["title"],
            bg=BG,
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(anchor="w")
        self._label(
            title_box,
            model["subtitle"],
            fg=MUTED,
            bg=BG,
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", pady=(2, 0))
        if hover.get("enabled") or detail.get("enabled"):
            detail_state = "收起全量" if self._detail_open else "展开全量"
            self._label(
                title_box,
                f"mini · hover 详情 · 点击{detail_state}",
                fg=ACCENT if self._detail_open else MUTED,
                bg=BG,
                font=("Microsoft YaHei UI", 8),
            ).pack(anchor="w", pady=(2, 0))
        status_text, status_tag = model["status"]
        status = tk.Label(
            header,
            text=status_text,
            fg=BG,
            bg=_severity_color(status_tag),
            font=("Microsoft YaHei UI", 10, "bold"),
            padx=12,
            pady=5,
        )
        status.pack(side="right", padx=(10, 0))

        decision_text, decision_detail, decision_tag = model["decision"]
        decision = self._card(self.frame, bg=PANEL_SOFT)
        decision.pack(fill="x", pady=(8, 8), ipady=5)
        tk.Frame(decision, width=5, bg=_severity_color(decision_tag)).pack(
            side="left",
            fill="y",
        )
        decision_body = tk.Frame(decision, bg=PANEL_SOFT, padx=12, pady=8)
        decision_body.pack(fill="x", expand=True)
        self._label(
            decision_body,
            decision_text,
            fg=_severity_color(decision_tag),
            bg=PANEL_SOFT,
            font=("Microsoft YaHei UI", 15, "bold"),
        ).pack(anchor="w")
        if decision_detail:
            self._render_decision_chips(
                decision_body,
                decision_detail,
                severity=decision_tag,
                bg=PANEL_SOFT,
                wraplength=420,
            )

        metrics = tk.Frame(self.frame, bg=BG)
        metrics.pack(fill="x")
        mini_metrics = mini.get("metrics") or model["metrics"][:4]
        for index, (title, value, detail, tag) in enumerate(mini_metrics[:4]):
            card = self._card(metrics)
            row_no = index // 2
            col_no = index % 2
            card.grid(
                row=row_no,
                column=col_no,
                padx=(0 if col_no == 0 else 6, 0),
                pady=(0 if row_no == 0 else 6, 0),
                sticky="nsew",
            )
            metrics.grid_columnconfigure(col_no, weight=1, uniform="metric")
            metric_color = _severity_color(tag) if tag != "normal" else ACCENT
            tk.Frame(card, width=3, bg=metric_color).pack(
                side="left",
                fill="y",
            )
            body = tk.Frame(card, bg=PANEL, padx=8, pady=6)
            body.pack(side="left", fill="both", expand=True)
            self._label(
                body,
                title,
                fg=metric_color if tag != "normal" else MUTED,
                font=("Microsoft YaHei UI", 9, "bold"),
            ).pack(anchor="w")
            self._label(
                body,
                value,
                fg=_severity_color(tag),
                font=("Microsoft YaHei UI", 12 if _is_price_metric(title) else 10, "bold"),
                wraplength=185,
            ).pack(anchor="w", pady=(3, 0))
            if detail:
                self._label(
                    body,
                    _short(detail, 46),
                    fg=MUTED,
                    font=("Microsoft YaHei UI", 8),
                    wraplength=185,
                ).pack(anchor="w", pady=(3, 0))

        lower = tk.Frame(self.frame, bg=BG)
        lower.pack(fill="both", expand=True, pady=(8, 0))
        left = tk.Frame(lower, bg=BG)
        left.pack(side="left", fill="both", expand=True)
        show_side_panel = self._detail_open
        right: tk.Frame | None = None
        if show_side_panel:
            right = tk.Frame(lower, bg=BG, width=210)
            right.pack(side="right", fill="y", padx=(8, 0))

        mini_sections = mini.get("sections") or model["sections"][:4]
        for title, value, detail in mini_sections[:4]:
            style = _section_style(title, value, detail)
            row = self._card(left)
            row.pack(fill="x", pady=(0, 8))
            tk.Frame(row, width=4, bg=style["color"]).pack(side="left", fill="y")
            body = tk.Frame(row, bg=PANEL, padx=10, pady=7)
            body.pack(side="left", fill="x", expand=True)
            header = tk.Frame(body, bg=PANEL)
            header.pack(fill="x")
            tk.Label(
                header,
                text=style["badge"],
                fg=BG,
                bg=style["color"],
                font=("Microsoft YaHei UI", 7, "bold"),
                padx=5,
                pady=1,
            ).pack(side="left")
            self._label(
                header,
                title,
                fg=style["color"],
                font=("Microsoft YaHei UI", 9, "bold"),
            ).pack(side="left", padx=(6, 0))
            self._label(body, value, wraplength=420).pack(anchor="w", pady=(3, 0))
            if detail:
                self._label(body, detail, fg=MUTED, wraplength=420, font=("Microsoft YaHei UI", 8)).pack(anchor="w")

        if not show_side_panel and model["alerts"]:
            alert_row = self._card(left, bg=PANEL_SOFT)
            alert_row.pack(fill="x", pady=(0, 8))
            alert_body = tk.Frame(alert_row, bg=PANEL_SOFT, padx=10, pady=7)
            alert_body.pack(fill="x")
            self._label(
                alert_body,
                "风险提示",
                fg=PURPLE,
                bg=PANEL_SOFT,
                font=("Microsoft YaHei UI", 9, "bold"),
            ).pack(anchor="w")
            for text, tag in model["alerts"][:2]:
                self._label(
                    alert_body,
                    "• " + _short(text, 86),
                    fg=_severity_color(tag),
                    bg=PANEL_SOFT,
                    wraplength=420,
                    font=("Microsoft YaHei UI", 8),
                ).pack(anchor="w", pady=(3, 0))

        if show_side_panel and right is not None and model.get("minimap"):
            minimap = model["minimap"]
            layout_complete = _flag(minimap.get("layout_complete"))
            settlement_layout = minimap.get("layout_source") == "settlement_inventory"
            source_label = (
                "结算全布局"
                if layout_complete
                else "结算布局"
                if settlement_layout
                else "赛中已知"
            )
            if settlement_layout and minimap.get("final_total_items"):
                count_text = (
                    f"{minimap.get('drawable_items', 0)}/"
                    f"{minimap.get('final_total_items')} 件"
                )
            else:
                count_text = f"{minimap.get('known_items', 0)} 件"
            minimap_card = self._card(right)
            minimap_card.pack(fill="x", pady=(0, 8))
            minimap_body = tk.Frame(minimap_card, bg=PANEL, padx=10, pady=8)
            minimap_body.pack(fill="x")
            self._label(
                minimap_body,
                f"MiniMap · {source_label}",
                fg=ACCENT,
                font=("Microsoft YaHei UI", 10, "bold"),
            ).pack(anchor="w")
            canvas_frame = tk.Frame(minimap_body, bg=PANEL)
            canvas_frame.pack(anchor="w", pady=(6, 0))
            canvas = tk.Canvas(
                canvas_frame,
                bg=PANEL_SOFT,
                highlightthickness=0,
                bd=0,
            )
            minimap_geometry = _minimap_canvas_geometry(minimap)
            scrollable = (
                minimap.get("scrollable")
                or minimap_geometry["height"] > minimap_geometry["visible_height"]
            )
            if scrollable:
                scrollbar = tk.Scrollbar(
                    canvas_frame,
                    orient="vertical",
                    command=canvas.yview,
                )
                canvas.configure(yscrollcommand=scrollbar.set)
                canvas.pack(side="left")
                scrollbar.pack(side="right", fill="y")
                canvas.bind(
                    "<MouseWheel>",
                    lambda event: self._scroll_canvas(canvas, event),
                )
            else:
                canvas.pack(side="left")
            self._draw_minimap(canvas, minimap)
            self._label(
                minimap_body,
                f"{count_text} · "
                f"{minimap.get('capacity_text') or _minimap_capacity_text(minimap)}",
                fg=MUTED,
                font=("Microsoft YaHei UI", 8),
            ).pack(anchor="w", pady=(5, 0))

        if show_side_panel and right is not None:
            alert_card = self._card(right)
            alert_card.pack(fill="both", expand=True)
            alert_body = tk.Frame(alert_card, bg=PANEL, padx=10, pady=8)
            alert_body.pack(fill="both", expand=True)
            self._label(
                alert_body,
                "风险与回测",
                fg=PURPLE,
                font=("Microsoft YaHei UI", 10, "bold"),
            ).pack(anchor="w")
            if model["alerts"]:
                for text, tag in model["alerts"][:5]:
                    self._label(
                        alert_body,
                        "• " + text,
                        fg=_severity_color(tag),
                        wraplength=190,
                        font=("Microsoft YaHei UI", 9),
                    ).pack(anchor="w", pady=(6, 0))
            else:
                self._label(
                    alert_body,
                    "暂无高亮风险",
                    fg=GOOD,
                    font=("Microsoft YaHei UI", 9),
                ).pack(anchor="w", pady=(6, 0))
            if model["footer"]:
                self._label(
                    alert_body,
                    model["footer"],
                    fg=MUTED,
                    wraplength=190,
                    font=("Microsoft YaHei UI", 8),
                ).pack(anchor="w", side="bottom", pady=(10, 0))
        if self._detail_open:
            self._render_detail_panel(self.frame, model)
        self._bind_layer_events(self.frame)
        self._resize_for_mode()
        self._restore_canvas_scroll(previous_scroll)

    def _render_overlay_error(self, exc: BaseException) -> None:
        self._logger.exception("overlay render failed")
        model = _standby_model(
            subtitle="悬浮窗渲染异常",
            detail=str(exc)[:240],
            notes=[
                ("说明", "窗口未关闭，将继续重试读取 snapshot"),
                ("日志", str(self._error_log_path)),
            ],
            decision_text="UI 异常",
        )
        model["status"] = ("异常", "bad")
        model["alerts"] = [("渲染失败，请查看 overlay.errors.log", "bad")]
        self._render(model)

    def refresh(self) -> None:
        try:
            self._refresh_once()
        except Exception as exc:  # noqa: BLE001 - keep Tk loop alive
            try:
                self._render_overlay_error(exc)
            except Exception:  # noqa: BLE001
                self._logger.exception("overlay error panel failed")
        self.root.after(self.interval_ms, self.refresh)

    def _refresh_once(self) -> None:
        if self.exit_when_pids and _watched_pid_exited(self.exit_when_pids):
            # Keep the window open with a clear error instead of vanishing when
            # the background monitor dies (common cause: WinDivert needs admin).
            self.exit_when_pids = ()
            model = _standby_model(
                subtitle="监听进程已退出",
                detail=(
                    "WinDivert 需要管理员 PowerShell。"
                    "请关闭本窗口后，右键 PowerShell「以管理员身份运行」，"
                    "再执行 .\\scripts\\start_live_windivert_overlay.ps1 -Restart"
                ),
                notes=[
                    ("常见原因", "非管理员启动导致 monitor 立即退出"),
                    (
                        "日志",
                        "查看 data/logs/live/monitor.stderr.log 是否含 "
                        "elevated PowerShell/admin",
                    ),
                ],
            )
            model["status"] = ("监听已停", "bad")
            model["alerts"] = [
                ("后台 monitor 已退出，悬浮窗保持打开仅用于提示", "bad"),
            ]
            self._render(model)
            self._last_snapshot_signature = ("monitor-exit",)
            self.root.after(self.interval_ms, self.refresh)
            return
        signature: tuple[Any, ...]
        if self.demo:
            signature = ("demo",)
            snapshot = self._demo_snapshot or {}
        else:
            snapshot = _load_live_snapshot(self.snapshot_path)
            signature = (
                "live",
                _snapshot_file_signature(self.snapshot_path),
                _capture_status_signature(
                    _as_mapping(snapshot.get("_capture_source_status"))
                ),
            )
        should_render = signature != self._last_snapshot_signature
        if snapshot is not None:
            _age_text, stale = _age(snapshot)
            if stale != self._last_stale_state:
                should_render = True
        if should_render:
            snapshot = snapshot or {}
            self._render(
                _overlay_model(snapshot, review_snapshot=self.review_snapshot)
            )
            self._last_snapshot_signature = signature
            self._last_snapshot = snapshot
            self._last_stale_state = _age(snapshot)[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Show BidKing live overlay.")
    parser.add_argument(
        "--snapshot",
        default=str(DEFAULT_SNAPSHOT_PATH),
        help="Path to latest_snapshot.json",
    )
    parser.add_argument("--interval-ms", type=int, default=1000)
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Show a built-in demo snapshot instead of reading latest_snapshot.json.",
    )
    parser.add_argument(
        "--review-snapshot",
        action="store_true",
        help="Do not hide stale snapshots; useful for archived UI review files.",
    )
    parser.add_argument(
        "--stop-pid-on-exit",
        type=int,
        action="append",
        default=[],
        help="Terminate this monitor PID when the overlay exits.",
    )
    parser.add_argument(
        "--cleanup-lock-on-exit",
        type=Path,
        action="append",
        default=[],
        help="Remove this monitor lock file after exit cleanup.",
    )
    parser.add_argument(
        "--exit-when-pid-exits",
        type=int,
        action="append",
        default=[],
        help="Close the overlay when this monitor PID exits.",
    )
    args = parser.parse_args()
    snapshot_path = Path(args.snapshot)
    try:
        explicit_review_snapshot = (
            snapshot_path.resolve() != DEFAULT_SNAPSHOT_PATH.resolve()
        )
    except OSError:
        explicit_review_snapshot = snapshot_path != DEFAULT_SNAPSHOT_PATH
    review_snapshot = args.review_snapshot or explicit_review_snapshot

    root = tk.Tk()
    overlay = Overlay(
        root,
        snapshot_path,
        max(250, args.interval_ms),
        demo=args.demo,
        review_snapshot=review_snapshot,
        exit_when_pids=tuple(args.exit_when_pid_exits),
    )
    try:
        root.mainloop()
    finally:
        _cleanup_exit_targets(
            tuple(args.stop_pid_on_exit),
            tuple(args.cleanup_lock_on_exit),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
