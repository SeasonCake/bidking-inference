from __future__ import annotations

import argparse
import copy
import colorsys
import ctypes
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import importlib
import json
import math
import ntpath
import os
from pathlib import Path
import queue
import random
import re
import signal
import stat
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import Any, Callable, Mapping
import uuid
import webbrowser
import zipfile


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
LAB_ROOT = TOOLS_DIR.parents[0]
LAB_SRC = LAB_ROOT / "src"
if str(LAB_SRC) not in sys.path:
    sys.path.insert(0, str(LAB_SRC))

from bidking_lab._atomic_json import atomic_write_json as _atomic_write_json  # noqa: E402
from bidking_lab.live.live_refresh_signatures import (  # noqa: E402
    bid_signature_from_snapshot,
    inference_signature_from_snapshot,
    ref_input_signature_from_snapshot,
)
from bidking_lab.live import quote_preferences as quote_prefs  # noqa: E402
from bidking_lab.live.fact_receipts import (  # noqa: E402
    current_receipt_bundle_bytes_valid,
    current_receipt_bundle_paths,
)
from bidking_lab.live.startup_version_notice import (  # noqa: E402
    build_update_notice,
    read_recent_startup_version_status,
)
from bidking_lab.secure.diagnostic_redaction import (  # noqa: E402
    collect_player_aliases,
    collect_player_aliases_from_document,
    redact_document,
    redact_payload,
)
from ahmad_live_panel_server import (  # noqa: E402
    CURRENT_JOINT_EXACT_CONTINUITY_DISABLE_ENV,
    QUALITY_DISPLAY_ORDER,
    SETTLED_STALE_SECONDS,
    STALE_SNAPSHOT_SECONDS,
    capture_session_ahead_of_snapshot,
    _aisha_d1_flag_detail,
    _aisha_defense_multiplier_hint,
    _candidate_summary,
    _compact_grid_cells,
    _low_quality_count_probability_hover,
    _mask_bidder_display_text,
    _next_info_hint,
    _parse_int,
    _purple_gold_quality_summary,
    _quality_dimension_probability_hover,
    _quality_probability_hover,
    _quality_probability_summary,
    _quality_uncertainty_summary,
    _ref_adaptation_pending_text,
    activity_2511_provisional_quote_revoked,
    _red_display_ranges,
    _red_display_value_range,
    _red_range_text,
    summarize_snapshot,
)


_STARTUP_TIMING_EVENTS = frozenset({"tk_map", "tk_after_idle"})
_STARTUP_TIMING_RESULTS = frozenset({"begin", "ok", "failed", "skipped", "unavailable"})
_STARTUP_TIMING_TOPOLOGIES = frozenset(
    {
        "unknown",
        "cold_start",
        "current_current",
        "current_absent",
        "current_stale",
        "current_unsafe",
        "rapid_reentry",
        "diagnostics",
        "repair",
    }
)


def _write_startup_timing_event(event: str, result: str) -> bool:
    """Write one fixed, privacy-safe event; never affect UI startup."""

    temporary: Path | None = None
    try:
        if event not in _STARTUP_TIMING_EVENTS or result not in _STARTUP_TIMING_RESULTS:
            return False
        run_id = os.environ.get("BIDKING_STARTUP_TIMING_RUN_ID", "")
        if re.fullmatch(r"[0-9a-f]{32}", run_id) is None:
            return False
        directory = Path(os.environ.get("BIDKING_STARTUP_TIMING_DIR", ""))
        if not directory.is_absolute():
            return False
        base_tick = int(os.environ.get("BIDKING_STARTUP_TIMING_BASE_TICK_MS", ""))
        if os.name != "nt":
            return False
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetTickCount64.argtypes = []
        kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        current_tick = int(kernel32.GetTickCount64())
        elapsed_ms = current_tick - base_tick
        if elapsed_ms < 0 or elapsed_ms > 604_800_000:
            return False
        topology = os.environ.get("BIDKING_STARTUP_TIMING_TOPOLOGY", "unknown")
        if topology not in _STARTUP_TIMING_TOPOLOGIES:
            topology = "unknown"
        payload = {
            "schema": "bidking_startup_timing_v1",
            "schema_version": 1,
            "run_id": run_id,
            "event": event,
            "result": result,
            "topology": topology,
            "elapsed_ms": elapsed_ms,
        }
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if len(encoded) > 1024:
            return False
        directory.mkdir(parents=True, exist_ok=True)
        final = directory / f"{run_id}--{event}--{result}.json"
        temporary = directory / f".{run_id}--{event}--{result}--{uuid.uuid4().hex}.tmp"
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
        temporary.replace(final)
        return True
    except (OSError, ValueError, TypeError):
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return False

try:
    from hero_ref_version import resolve_hero_ref_version
    from product_profile import (
        NETWORK_DIAGNOSTIC_BADGE,
        NETWORK_DIAGNOSTIC_DIALOG_LINES,
        NETWORK_DIAGNOSTIC_TOOLTIP,
        PRODUCT_PROFILE_NETWORK_DIAGNOSTIC,
        STANDARD_SUBSCRIPTION_PRICE_ROWS,
        normalize_product_profile,
        resolve_product_profile,
        token_entry_action_text,
    )
except Exception:  # noqa: BLE001 - keep overlay usable if version helper is missing
    def resolve_hero_ref_version() -> str:
        return "dev"

    NETWORK_DIAGNOSTIC_BADGE = "网络诊断内测"
    NETWORK_DIAGNOSTIC_DIALOG_LINES = (
        "网络诊断内测包",
        "保留已有 token 激活与计算功能",
        "购买/再次购买与猫猫新权益暂不在本包启用",
        "90 天猫猫等新权益以线上正式服务为准",
    )
    NETWORK_DIAGNOSTIC_TOOLTIP = (
        "仅供网络问题群定向测试\n"
        "保留已有 token 激活与计算\n"
        "购买/再次购买与猫猫新权益不在本包启用"
    )
    PRODUCT_PROFILE_NETWORK_DIAGNOSTIC = "network-diagnostic"
    STANDARD_SUBSCRIPTION_PRICE_ROWS = (
        ("绑定设备：", ()),
        ("可换绑：", ()),
    )

    def normalize_product_profile(value: object) -> str:
        return (
            PRODUCT_PROFILE_NETWORK_DIAGNOSTIC
            if str(value or "").strip().lower()
            == PRODUCT_PROFILE_NETWORK_DIAGNOSTIC
            else "standard"
        )

    def resolve_product_profile() -> str:
        return "standard"

    def token_entry_action_text(product_profile: object) -> str:
        if (
            normalize_product_profile(product_profile)
            == PRODUCT_PROFILE_NETWORK_DIAGNOSTIC
        ):
            return "输入已有 token"
        return "".join(("点此输入/", "购买 token"))

from pet_companion import (  # noqa: E402
    PET_ACTION_BUBBLES,
    PET_COLOR_LABELS,
    PET_COLOR_ORDER,
    PET_INTRO_TEXT,
    PET_WINDOW_SIZE,
    PetCompanionOverlay,
    normalize_pet_color,
    normalize_pet_position,
    normalize_pet_touch_state,
    pet_actions_for_color,
    pet_assets_available,
)
from pet_access import (  # noqa: E402
    PET_ACCESS_PREVIEWS,
    PetAccessState,
    format_pet_access_clock,
    normalize_pet_trial_seconds,
    pet_access_contract_text,
    pet_access_from_activation,
    pet_access_tier_label,
)
from network_connection_status import (  # noqa: E402
    NETWORK_CONNECTION_STATUS_FILENAME,
    validate_network_connection_status,
)
from detail_composition_view import (  # noqa: E402
    composition_candidate_is_physical,
    DetailCompositionView,
    QUALITY_COMPOSITION_SCHEMA_VERSION,
    QualityComposition,
    detail_composition_view,
    format_compact_quality_footprint,
    format_probability,
    format_quality_count_candidates,
    format_quality_dimension_state_badge,
    format_quality_probability,
    quality_dimension_is_exact,
    quality_dimension_triplet,
    quality_display_count_triplet,
    quality_is_strict_exact,
    quality_paired_candidate_triplet,
)

try:
    from hero_ref_live_schedule import REF_FULL_MAX_COMBOS, hero_max_combos_for_round  # noqa: E402
except Exception:  # noqa: BLE001 - manual mode falls back when schedule helper missing
    REF_FULL_MAX_COMBOS = 20_000

    def hero_max_combos_for_round(hero_key: str, round_no: int | None) -> int:  # noqa: ARG001
        return REF_FULL_MAX_COMBOS

try:
    from ahmad_ref_engine import (  # noqa: E402
        HERO_ALIASES,
        SUPPORTED_REF_HERO_KEYS,
        can_compose_grid_total,
        is_supported_ref_hero,
        load_reference_static_data,
        normalize_hero_key,
        prepare_reference_engine_snapshot,
        run_reference_engine,
    )
except Exception:  # noqa: BLE001 - keep overlay usable if ref core is unavailable
    HERO_ALIASES = {
        "aisha": "aisha",
        "艾莎": "aisha",
        "ahmad": "ahmed",
        "ahmed": "ahmed",
        "ahamed": "ahmed",
        "艾哈": "ahmed",
        "艾哈迈德": "ahmed",
        "victor": "victor",
        "维克": "victor",
        "维克托": "victor",
    }
    SUPPORTED_REF_HERO_KEYS = frozenset({"aisha", "ahmed", "victor"})

    def normalize_hero_key(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        return HERO_ALIASES.get(text.lower(), HERO_ALIASES.get(text, text.lower()))

    def is_supported_ref_hero(value: Any) -> bool:
        return normalize_hero_key(value) in SUPPORTED_REF_HERO_KEYS

    can_compose_grid_total = None  # type: ignore[assignment]
    load_reference_static_data = None  # type: ignore[assignment]
    run_reference_engine = None  # type: ignore[assignment]

    def prepare_reference_engine_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
        return dict(snapshot)

try:
    from draw_hero_policy import draw_hero_prop_kit_text
except Exception:  # noqa: BLE001 - draw-hero prop kit text is optional polish

    def draw_hero_prop_kit_text(normalized_hero: Any) -> str:  # type: ignore[misc]
        return ""


# 手填重算在 UI 线程同步跑, 深仓高预算(8000/REF_FULL=20000)会让悬浮窗卡死 16-30s+。
# 实测 2000≈8000 估值不变(参考 215 vs 217k, 红件/件数全同), 故封顶 2500 把卡顿压到 ~2.5s。
# 只影响手填重算; live 自动估算(monitor 独立进程, 不卡 UI)不变。锁定多的轮次本就到不了上限。
_MANUAL_MAX_COMBOS = 2500
# The live tables currently top out far below this value (44 rows in the shipped
# BidMap table, with larger community/player warehouses still remaining well under
# three digits).  Keep a generous ceiling so malformed scientific-notation counts
# cannot drive the exact-grid DP into an unbounded allocation from a Tk trace.
_MANUAL_MAX_ITEM_COUNT = 512
# A valid item may occupy up to 18 cells.  Cell totals therefore need a separate
# bound: reusing the item-count ceiling rejects valid high-cell inputs such as
# 44 items at 18 cells each, while leaving them unbounded can make trace-time
# arithmetic and count-from-cells enumeration pathological.
_MANUAL_MAX_GRID_CELLS = 18 * _MANUAL_MAX_ITEM_COUNT


def _manual_engine_max_combos(snapshot: dict[str, Any]) -> int:
    """Manual panel runs synchronously on the UI thread, so cap combos hard to
    keep deep-warehouse recompute snappy (see _MANUAL_MAX_COMBOS)."""
    hero_key = normalize_hero_key(snapshot.get("hero") or "")
    round_no = _parse_int(snapshot.get("round"))
    if round_no is None:
        uc = snapshot.get("ui_contract") if isinstance(snapshot.get("ui_contract"), dict) else {}
        context = uc.get("context") if isinstance(uc.get("context"), dict) else {}
        round_no = _parse_int(context.get("round"))
    if hero_key and round_no is not None:
        return min(hero_max_combos_for_round(hero_key, round_no), _MANUAL_MAX_COMBOS)
    return _MANUAL_MAX_COMBOS


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SNAPSHOT = ROOT / "data" / "logs" / "live" / "latest_snapshot.json"
HERO_REF_DISPLAY_VERSION = resolve_hero_ref_version()
CREDIT_TEXT = "作者: 加菲_Barista · 协作: lemyes"
CREDIT_BILIBILI_URL = "https://space.bilibili.com/88048665?spm_id_from=333.1007.0.0"
BILIBILI_TIP_TEXT = "访问加菲_Barista 的 Bilibili 空间"
PUBLIC_BIG_DATA_URL = "https://api.bidking.example.invalid/public"
CLOUD_BIG_DATA_TEXT = "☁ 大数据"
CLOUD_BIG_DATA_TIP_TEXT = "打开现有公共大数据平台（需要联网）"
CLOUD_BIG_DATA_OPEN_COOLDOWN_MS = 800
DETAIL_TIP_TEXT = "展开 / 收起详情、小地图和手动填写区"
COMPATIBILITY_GUIDANCE_STEPS = (
    "完整解压程序包，不要在 ZIP 内直接运行。",
    "将整个文件夹（解压后的 Hero Ref/BidKingLive）加入火绒或 360 的信任区/白名单。",
    "首次启动或能进游戏但不计算时，完整退出火绒、360、游戏加速器；"
    "仅关闭主窗口可能仍有后台防护或服务。",
    "先启动 Hero Ref，等待悬浮窗出现，再启动游戏。",
    "仍不计算时按同一顺序重启，再收集脱敏诊断私发；不要反复更换 token。",
    "不要卸载或永久关闭安全软件；测试后可恢复防护。"
    "若恢复后再次失效，请保持安全设置并等待兼容更新。",
)
COMPATIBILITY_GUIDANCE_FOOTER = (
    "Hero Ref 不会自动修改安全软件、Windows 安全功能、代理、路由或 DNS。"
)
MANUAL_TIP_TEXT = "手动填写；断网或识别缺项时补总件、均格、件数、均价/总价；编辑时自动抓取不会覆盖当前输入"
MANUAL_RETURN_TIP_TEXT = "返回实时；手填内容保留，live 继续监测并可补齐空字段"
MANUAL_CLEAR_SETTLEMENT_TIP = "结算页手填时清掉自动带入的结算数字，保留英雄/地图；不删除 live 日志"
MANUAL_CLEAR_SETTLEMENT_INACTIVE_TIP = "只在当前停留结算页并进入手填时启用"
THEME_TIP_PREFIX = "切换配色"
PET_TIP_OFF = "左键状态岛：唤醒桌面猫"
PET_TIP_ON = "左键状态岛：收回桌面猫"
PET_COLOR_TIP = "换色：右键右下角猫猫状态岛（桌面猫上也可右键）"
PET_ACCESS_TRIAL_TEXT = "免费试用可使用桌面猫猫 10 分钟"
PET_ACCESS_FULL_TEXT = (
    "90 天及以上在订阅有效期内不限累计时长 · 历史永久用户持续可用"
)
PET_LAUNCHER_PROGRESS_MIN_WIDTH = 66
SETTLEMENT_HIDE_TIP = "预设隐藏结算金额，想自己看结算时用；结算出现后生效，只影响界面，不影响计算"
SETTLEMENT_SHOW_TIP = "显示结算金额；关闭预设隐藏，只影响界面，不影响计算"
SETTLEMENT_INACTIVE_TIP = SETTLEMENT_HIDE_TIP
CLOSE_TIP_TEXT = "关闭 Hero Ref；游戏未运行时后台读取会在短暂宽限后结束"
CLOSE_KEEP_MONITOR_TIP_TEXT = (
    "关闭悬浮窗；后台读取按工程保留设置继续运行"
)
CLOSE_PROTECT_MONITOR_TIP_TEXT = (
    "关闭悬浮窗；游戏运行期间后台读取继续，游戏退出后自动结束；重新打开可恢复界面"
)
RESIZE_TIP_TEXT = "拖动 ↘ 缩放窗口；左右调宽度，上下调字号，高度自动贴合内容"
FOOTER_RESIZE_TIP_TEXT = "拖动 ◢ 缩放窗口；左右调宽度，上下调字号，高度自动贴合内容"
SUMMARY_DIAGNOSTIC_LOG = "hero_ref_ui_summary.jsonl"
UI_HEALTH_LOG = "hero_ref_ui_health.jsonl"
SAFE_FRAME_TELEMETRY_LOG = "hero_ref_ui_frame_safe.jsonl"
SAFE_FRAME_TELEMETRY_LIMIT = 64
DISPLAY_QUALITY_KEYS = frozenset({"q1", "q3", "q4", "q5", "q6"})
UI_RUNTIME_STATUS = "hero_ref_ui_runtime_status.json"
COMPUTE_STATUS_FILENAME = "monitor_compute_status.json"
COMPUTE_STATUS_SCHEMA_VERSION = 1
_SNAPSHOT_NOT_LOADED = object()
UI_PREFS_FILENAME = "hero_ref_ui_prefs.json"
UI_PREFS_SCHEMA_VERSION = 7
UI_PREFS_SAVE_DEBOUNCE_MS = 400
RESIZE_FRAME_INTERVAL_MS = 16  # 合并高频鼠标事件，每个显示帧最多提交一次最新尺寸
UI_SCALE_LIVE_INTERVAL_MS = 48  # 真实排版限频提交；持续拖动也不得把它无限延后到松手
UI_SCALE_LIVE_DELTA = 0.01      # 只跳过视觉不可分辨的微小变化，不把最后一档留到松手
UI_STALL_SECONDS = 5.0
UI_STALL_LOG_INTERVAL_SECONDS = 5.0
# 诊断导出只带【近窗】的 UI 健康日志。hero_ref_ui_health.jsonl 是【只记异常、跨会话累积】的 append
# 日志,导出打整份 → 读包人看末尾常把【上上周的旧 stall】误当【本局】(真实事故: 07-04 导出的末尾
# stall 是 06-24 的, 差 10 天, 被当成当局铁证 → 据此推错根因)。故导出时按 logged_at 裁到近 6h(足够
# 覆盖一次连续使用, 又必然排除数天前的旧尾), 并在 manifest 记全量跨度+裁剪计数, 读包人一眼知道裁过。
DIAGNOSTIC_HEALTH_WINDOW_SECONDS = 6 * 3600.0
UI_RUNTIME_STATUS_REPEAT_INTERVAL_SECONDS = 10.0
# Only a PROCESS worker can be abandoned and replaced safely. An in-thread engine call cannot be
# cancelled: treating a legitimate deep-vault call as stuck starts a second CPU-bound call while the
# first one is still running. A real Gabriela 2407 R4 takes ~35s on the 2026-07-15 source baseline, so
# the old universal 25s watchdog amplified one slow quote into two competing computations. Packaged
# builds now prove their native child once at startup before using the persistent process; a failed
# proof only permits one bounded process attempt for each new input, never an implicit CPU thread.
SUMMARY_PROCESS_WORKER_TIMEOUT_SECONDS = 90.0
SUMMARY_FALLBACK_PROCESS_TIMEOUT_SECONDS = 5.0
REF_ENGINE_LIVENESS_TIMEOUT_SECONDS = 4.0
REF_ENGINE_LIVENESS_RETRY_INITIAL_SECONDS = 1.0
REF_ENGINE_LIVENESS_RETRY_MAX_SECONDS = 8.0
REF_ENGINE_CHILD_LIVENESS_IDENTITY = ("bidking-ref-engine-child", 1, 3)
_REF_ENGINE_WARMUP_PENDING = object()
# Compatibility name for diagnostics/tests that imported the old constant. Recovery code below uses
# the explicit process-only name so its cancellation boundary stays obvious.
SUMMARY_WORKER_TIMEOUT_SECONDS = SUMMARY_PROCESS_WORKER_TIMEOUT_SECONDS


def _is_packaged_build() -> bool:
    """Return whether runtime identity comes from a built client bundle.

    This is only an identity signal.  Packaged callers still have to prove the native
    child import path at runtime before a process worker becomes eligible.
    """
    try:
        here = Path(__file__).resolve()
        for parent in here.parents[:6]:
            if (parent / "BUILD_MANIFEST.txt").is_file():
                return True
    except OSError:
        pass
    return bool(getattr(sys, "frozen", False))


def _packaged_ref_engine_requires_native() -> bool:
    """Only the exact portable manifest may use the checked-in Python child chain."""
    try:
        root = _validated_package_root()
        manifest = _require_plain_descendant(
            root, root / "BUILD_MANIFEST.txt", label="BUILD_MANIFEST", file=True
        )
        fields: dict[str, str] = {}
        for line in manifest.read_text(encoding="utf-8-sig").splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            if key in {"ProductProfile", "Encrypted", "NativeBinaryDistribution"}:
                if value.startswith(" "):
                    value = value[1:]
                if key in fields or value != value.strip():
                    return True
                fields[key] = value.strip()
        return not (
            fields == {
                "ProductProfile": "standard",
                "Encrypted": "False",
                "NativeBinaryDistribution": "NotRequired",
            }
        )
    except (OSError, RuntimeError, ValueError, PackageRootAuthorityError):
        return True


def _ref_engine_child_liveness_probe(require_native: bool) -> tuple[str, int, int]:
    """Prove that the child imported the real engine/panel/overlay implementation.

    The result is deliberately fixed and path-free.  Packaged callers require all three
    high-value modules to be native; source tests exercise the same import/callable contract
    without pretending a ``.py`` checkout is the encrypted candidate.
    """
    if type(require_native) is not bool:
        raise TypeError("require_native must be exact bool")
    panel = importlib.import_module("ahmad_live_panel_server")
    engine = importlib.import_module("ahmad_ref_engine")
    overlay = sys.modules.get(__name__)
    modules = (overlay, panel, engine)
    if overlay is None:
        raise RuntimeError("overlay module unavailable")
    if not callable(getattr(panel, "summarize_snapshot", None)):
        raise RuntimeError("summary entry unavailable")
    if not callable(getattr(engine, "run_reference_engine", None)):
        raise RuntimeError("engine entry unavailable")
    if require_native:
        for module in modules:
            module_file = getattr(module, "__file__", None)
            if type(module_file) is not str or Path(module_file).suffix.lower() != ".pyd":
                raise RuntimeError("native module unavailable")
    elif _is_packaged_build() and _packaged_ref_engine_requires_native():
        raise RuntimeError("portable profile unavailable")
    return REF_ENGINE_CHILD_LIVENESS_IDENTITY


def _shutdown_ref_engine_executor(executor: Any, *, hard: bool, owner: Any = None) -> bool:
    """Bounded shutdown for a single-worker process pool.

    ``Future.cancel``/``shutdown(cancel_futures=True)`` do not stop a task which is already
    executing.  Capture the pool's worker process and terminate it first when a liveness or
    summary deadline expires, otherwise a supposedly abandoned calculation keeps consuming
    CPU and can starve Tk together with its replacement.
    """
    if executor is None:
        return True
    retiring = None
    if owner is not None:
        retiring = owner.__dict__.setdefault("_ref_engine_retiring_executors", {})
        retiring[id(executor)] = executor
    # CPython clears executor._processes on shutdown(wait=False). Retain both
    # exact Process objects and the owned mapping before that mutation. A
    # later call must never interpret the cleared attribute as proof of exit.
    state = executor.__dict__.setdefault("_bidking_shutdown_state", {
        "lock": threading.RLock(), "processes": {}, "maps": [], "known": False,
    })
    with state["lock"]:
        processes_obj = getattr(executor, "_processes", None)
        threads = getattr(executor, "_threads", None)
        if isinstance(threads, (set, list, tuple)):
            state["known"] = True
        if isinstance(processes_obj, dict):
            state["known"] = True
            if all(processes_obj is not item for item in state["maps"]):
                state["maps"].append(processes_obj)
        for mapping in state["maps"]:
            for process in tuple(mapping.values()):
                state["processes"][id(process)] = process
        if hard:
            for process in tuple(state["processes"].values()):
                try:
                    process.terminate()
                except Exception:
                    pass
        try:
            executor.shutdown(wait=False, cancel_futures=True)
            shutdown_ok = True
        except Exception:
            shutdown_ok = False
        # Include a submit already inside the executor's own shutdown lock.
        for mapping in state["maps"]:
            for process in tuple(mapping.values()):
                state["processes"][id(process)] = process
        reaped = bool(state["known"]) and shutdown_ok
        for process in tuple(state["processes"].values()):
            try:
                process.join(timeout=0.2 if hard else 0.0)
                if process.is_alive() and hard:
                    process.kill()
                    process.join(timeout=0.2)
                if process.is_alive() is not False:
                    reaped = False
            except Exception:
                reaped = False
        reaped = reaped and all(thread.is_alive() is False for thread in (threads or ()))
        if reaped and retiring is not None:
            retiring.pop(id(executor), None)
        return reaped


DIAGNOSTIC_PROFILES = ("engineering", "portable", "public-safe")
DIAGNOSTIC_PROFILE_ALIASES = {
    "engineering": "engineering",
    "portable": "portable",
    "stable": "portable",
    "public-safe": "public-safe",
    "public_safe": "public-safe",
}
DEFAULT_DIAGNOSTIC_PROFILE = "engineering"
TOPMOST_ON_TIP = "置顶中；点击切换为自由窗口"
TOPMOST_OFF_TIP = "自由窗口；点击恢复置顶"
REFRESH_TIP = "刷新当前界面显示；不会重启后台读取，也不会影响正在进行的对局。"
RESTART_TIP = (
    "重启前请先完全退出游戏；等悬浮窗重新出现后，再从 Steam 启动游戏。"
    "游戏运行中点击重启会被安全拦截。"
)
RESTART_GAME_RUNNING_TEXT = "请先完全退出 BidKing，再重启计算器"
RESTART_GAME_STATE_UNKNOWN_TEXT = (
    "未能确认游戏是否已退出；请先完全退出 BidKing，再重启计算器"
)
GAME_PROCESS_NAME = "BidKing.exe"
STEAM_GAME_APP_ID = "4128580"
STEAM_GAME_URI = f"steam://rungameid/{STEAM_GAME_APP_ID}"
GAME_LAUNCH_STATUS_MAX_AGE_SECONDS = 15.0
GAME_LAUNCH_PENDING_SECONDS = 12.0
GAME_LAUNCH_ERROR_SECONDS = 8.0
GAME_LAUNCH_READY_TEXT = "启动游戏 ▸"
GAME_LAUNCH_PENDING_TEXT = "正在启动…"
GAME_LAUNCH_ERROR_TEXT = "请从 Steam 启动"
GAME_LAUNCH_READY_TIP = (
    "Hero Ref 本地读取已就绪。点击后通过 Steam 启动竞拍之王；"
    "保持 Hero Ref 开启，游戏启动后此入口会自动收起。"
)
GAME_LAUNCH_ERROR_TIP = (
    "未能通过 Steam 协议启动游戏。请确认 Steam 已安装并登录，"
    "然后从 Steam 手动启动竞拍之王；Hero Ref 会继续等待。"
)
MINIMAP_PINNED_FREE_SUFFIX = " · 自由"
MINIMAP_PINNED_DRAG_TIP = "拖动标题栏可跨屏自由移动；默认跟随主面板"
EXPORT_DIAGNOSTIC_TIP_TEMPLATE = (
    "异常/卡住/结算不对时点这里；导出当前可用的对局状态和近期日志到 {path}。"
    "可能含设备与软件环境，请仅私发给支持人员，不要发群或公开上传"
)
EXPORT_DIAGNOSTIC_UNAVAILABLE_TIP = (
    "诊断包保存到系统“文档”中的 BidKingLive\\exports。"
    "当前无法访问系统文档目录，导出会失败；请恢复该目录访问后重试"
)
REQUIRED_RAW_TABLES = ("BidMap.txt", "Drop.txt", "Item.txt")
PACKAGE_ROOT_ENV = "BIDKING_PACKAGE_ROOT"
DOCUMENTS_KNOWN_FOLDER_ID = uuid.UUID("fdd39ad0-238f-46af-adb4-6c85480369c7")
LOCAL_APPDATA_KNOWN_FOLDER_ID = uuid.UUID("f1b32785-6fba-4fcf-9d55-7b8e7f157091")

FONT_UI = "Microsoft YaHei UI"
FONT_NUMERIC = "Segoe UI Semibold"
SUMMARY_VALUE_FONT_SIZE = 8
RED_COUNT_INLINE_FONT_SIZE = 8
SUMMARY_COMPACT_FONT_SIZE = 7
SUMMARY_COMPACT_MIN_FONT_SIZE = 6
UI_SCALE_MIN = 0.85
UI_SCALE_MAX = 1.6
MINI_BASE_WIDTH = 440
MINI_BASE_HEIGHT = 397
# Keep the legacy scale baseline above: changing it would shrink the type at the
# familiar 510px width.  This separate cold-start fallback gives a new user enough
# height for both six-row cards before the first mapped content measurement settles.
MINI_INITIAL_HEIGHT = 501
MINI_WINDOW_CHROME_HEIGHT = 18
DETAILS_BASE_WIDTH = 700
DETAILS_BASE_HEIGHT = 700
HERO_BUTTON_PADDING_BASE = (8, 4)
HERO_BUTTON_FONT_SIZE = 8
MINIMAP_CELL_HINT_BASE = 13.0
MINIMAP_POPUP_CELL_HINT_BASE = 24.0
MINIMAP_DEFAULT_COLUMNS = 10
MINIMAP_DEFAULT_ROWS = 13


def canvas_draw_size(
    canvas: tk.Canvas,
    *,
    min_width: int,
    min_height: int,
) -> tuple[int, int]:
    """Use the canvas widget's laid-out size, not stale creation defaults."""
    try:
        canvas.update_idletasks()
    except tk.TclError:
        pass
    width = int(canvas.winfo_width() or 0)
    height = int(canvas.winfo_height() or 0)
    if width <= 1:
        width = int(min_width)
    if height <= 1:
        height = int(min_height)
    return max(int(min_width), width), max(int(min_height), height)

MANUAL_BASE_FIELDS = (
    ("hero", "英雄", ""),
    ("map_id", "地图", ""),
    ("total_count", "总件", ""),
    ("total_cells", "总格", ""),
    ("total_avg", "全均格", ""),
)
MANUAL_QUALITY_ROWS = (
    ("白", "white_avg", "white_count", "white_cells"),
    ("绿", "green_avg", "green_count", "green_cells"),
    ("白绿", "q1_avg", "q1_count", "q1_cells"),
    ("蓝", "q3_avg", "q3_count", "q3_cells"),
    ("紫", "q4_avg", "q4_count", "q4_cells"),
    ("金", "q5_avg", "q5_count", "q5_cells"),
    ("红", "q6_avg", "q6_count", "q6_cells"),
)
MANUAL_VALUE_ROWS = (
    ("白", "white_avg_value", "white_value_sum"),
    ("绿", "green_avg_value", "green_value_sum"),
    ("白绿", "q1_avg_value", "q1_value_sum"),
    ("蓝", "q3_avg_value", "q3_value_sum"),
    ("紫", "q4_avg_value", "q4_value_sum"),
    ("金", "q5_avg_value", "q5_value_sum"),
    ("红", "q6_avg_value", "q6_value_sum"),
)
MANUAL_EXTRA_FIELDS = (
    ("q4q5_count", "紫金红件", ""),
)
QUALITY_LABELS = {
    "q1": "白绿",
    "q3": "蓝",
    "q4": "紫",
    "q5": "金",
    "q6": "红",
}
QUALITY_INPUT_KEYS = ("q1", "q3", "q4", "q5", "q6")
SPLIT_QUALITY_LABELS = {
    "white": "白",
    "green": "绿",
}
SPLIT_QUALITY_INPUT_KEYS = ("white", "green")
def compute_ui_scale(
    width: int,
    height: int,
    *,
    base_width: int,
    base_height: int,
    min_scale: float = UI_SCALE_MIN,
    max_scale: float = UI_SCALE_MAX,
) -> float:
    """Scale mini UI typography from window size relative to a mode baseline."""
    safe_w = max(1, int(base_width))
    safe_h = max(1, int(base_height))
    ratio = min(int(width) / safe_w, int(height) / safe_h)
    return max(float(min_scale), min(float(max_scale), float(ratio)))


def _clamp_rect_into_workarea(
    x: int, y: int, w: int, h: int, left: int, top: int, right: int, bottom: int
) -> tuple[int, int]:
    """把窗口 (x,y,w,h) 的左上角夹进工作区 [left,right]x[top,bottom], 返回夹后的 (x,y)。
    详情展开后窗口变高, 若原本靠屏幕下沿则底部会到屏外 → 下半信息看不到(群友小分辨率反馈);
    窗口高度本就 clamp 到 work_h-72(垂直塞得下), 这里只把位置拉回屏内。窗口比工作区还大时贴左上
    (保证顶部可见)。"""
    avail_w = right - left
    avail_h = bottom - top
    nx = left if w >= avail_w else max(left, min(int(x), right - w))
    ny = top if h >= avail_h else max(top, min(int(y), bottom - h))
    return nx, ny


def _monitor_work_area_for_point(
    widget: tk.Misc,
    x: int,
    y: int,
) -> tuple[int, int, int, int]:
    """Return the work area of the monitor nearest a desktop point.

    Tk's ``winfo_screenwidth/height`` only describes the primary display on Windows.  Minimap
    popups use desktop coordinates, so clamping them to that rectangle silently pulls every
    secondary-display position back onto the primary display.  ``MonitorFromPoint`` preserves
    negative/offset monitor origins and ``rcWork`` excludes that monitor's taskbar.
    """
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class MONITORINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD),
                ]

            user32 = ctypes.windll.user32
            monitor_from_point = user32.MonitorFromPoint
            monitor_from_point.argtypes = [wintypes.POINT, wintypes.DWORD]
            monitor_from_point.restype = wintypes.HANDLE
            get_monitor_info = user32.GetMonitorInfoW
            get_monitor_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
            get_monitor_info.restype = wintypes.BOOL
            monitor = monitor_from_point(wintypes.POINT(int(x), int(y)), 2)
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            if monitor and get_monitor_info(monitor, ctypes.byref(info)):
                rect = info.rcWork
                if rect.right > rect.left and rect.bottom > rect.top:
                    return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
        except Exception:
            pass

    try:
        left = int(widget.winfo_vrootx())
        top = int(widget.winfo_vrooty())
        right = left + max(1, int(widget.winfo_vrootwidth()))
        bottom = top + max(1, int(widget.winfo_vrootheight()))
        return left, top, right, bottom
    except (AttributeError, tk.TclError, TypeError, ValueError):
        try:
            screen_w = max(1, int(widget.winfo_screenwidth()))
            screen_h = max(1, int(widget.winfo_screenheight()))
        except (AttributeError, tk.TclError, TypeError, ValueError):
            # Headless/layout-test doubles may intentionally expose no Tk screen methods.  A real
            # Tk widget always takes one of the paths above; this keeps those pure layout callers
            # compatible with the historical 1920x1080 fallback.
            screen_w, screen_h = 1920, 1080
        return 0, 0, screen_w, screen_h


def _window_outer_rect(widget: tk.Misc) -> tuple[int, int, int, int]:
    """Return the mapped top-level's physical outer rectangle when Windows exposes it."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            get_ancestor = user32.GetAncestor
            get_ancestor.argtypes = [wintypes.HWND, wintypes.UINT]
            get_ancestor.restype = wintypes.HWND
            hwnd = wintypes.HWND(int(widget.winfo_id()))
            outer_hwnd = get_ancestor(hwnd, 2) or hwnd  # GA_ROOT includes title bar and border.
            rect = wintypes.RECT()
            if user32.GetWindowRect(outer_hwnd, ctypes.byref(rect)):
                if rect.right > rect.left and rect.bottom > rect.top:
                    return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
        except Exception:
            pass
    left = int(widget.winfo_rootx())
    top = int(widget.winfo_rooty())
    return (
        left,
        top,
        left + max(1, int(widget.winfo_width())),
        top + max(1, int(widget.winfo_height())),
    )


def scaled_font(
    family: str,
    size: int,
    *styles: str,
    scale: float = 1.0,
) -> tuple[Any, ...]:
    scaled_size = max(6, int(round(int(size) * float(scale))))
    if styles:
        return (family, scaled_size, *styles)
    return (family, scaled_size)


def font_spec_from_widget(widget: tk.Widget) -> tuple[Any, ...] | None:
    try:
        actual = tkfont.Font(font=widget.cget("font")).actual()
    except tk.TclError:
        return None
    family = str(actual.get("family") or FONT_UI)
    size = int(float(actual.get("size") or 0))
    if size <= 0:
        return None
    styles: list[str] = []
    if str(actual.get("weight") or "") == "bold":
        styles.append("bold")
    if str(actual.get("slant") or "") == "italic":
        styles.append("italic")
    if str(actual.get("underline") or "") == "1":
        styles.append("underline")
    return (family, size, *styles)


def compute_fitted_mini_height(
    shell_reqheight: int,
    *,
    chrome: int = MINI_WINDOW_CHROME_HEIGHT,
    min_height: int = 320,
    max_height: int = 1500,
) -> int:
    return max(min_height, min(max_height, int(shell_reqheight) + int(chrome)))


def compute_mini_ui_scale(
    width: int,
    *,
    base_width: int = MINI_BASE_WIDTH,
    min_scale: float = UI_SCALE_MIN,
    max_scale: float = UI_SCALE_MAX,
) -> float:
    """Mini overlay scale follows width; height is auto-fitted to content."""
    ratio = int(width) / max(1, int(base_width))
    return max(float(min_scale), min(float(max_scale), float(ratio)))


def compute_mini_resize_target(
    width0: int,
    height0: int,
    scale0: float,
    dx: int,
    dy: int,
    *,
    min_width: int = 430,
    max_width: int = 1200,
    min_scale: float = UI_SCALE_MIN,
    max_scale: float = UI_SCALE_MAX,
) -> tuple[int, float]:
    """Map mini-mode grip motion to independent width and typography targets.

    Horizontal motion changes the panel width and scales the layout proportionally. Vertical
    motion changes typography without forcing the panel wider; the real window height is still
    measured from the resulting content so the border never leaves blank filler below the cards.
    When both axes move, the larger relative gesture controls typography.
    """
    safe_width = max(1, int(width0))
    safe_height = max(1, int(height0))
    base_scale = max(float(min_scale), min(float(max_scale), float(scale0)))
    width = max(int(min_width), min(int(max_width), safe_width + int(dx)))
    horizontal_scale = base_scale * (float(width) / float(safe_width))
    vertical_height = max(1, safe_height + int(dy))
    vertical_scale = base_scale * (float(vertical_height) / float(safe_height))
    horizontal_delta = abs((float(width) / float(safe_width)) - 1.0)
    vertical_delta = abs((float(vertical_height) / float(safe_height)) - 1.0)
    target_scale = vertical_scale if vertical_delta > horizontal_delta else horizontal_scale
    return width, max(float(min_scale), min(float(max_scale), target_scale))


_PET_SETTLEMENT_ENTRY_FEE_BY_PREFIX = {
    "21": 0,
    "31": 0,
    "22": 2_000,
    "32": 2_000,
    "23": 5_000,
    "33": 5_000,
    "24": 10_000,
    "34": 10_000,
    "44": 10_000,
    "25": 25_000,
    "35": 25_000,
    "45": 25_000,
    "26": 100_000,
    "55": 15_000,
    "56": 15_000,
}
PET_SETTLEMENT_TIER_THRESHOLD = 200_000.0


def _pet_nonnegative_finite_amount(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        amount = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if not math.isfinite(amount) or amount < 0:
        return None
    return amount


def _pet_authoritative_entry_fee_silver(map_id: Any) -> float | None:
    if isinstance(map_id, bool):
        return None
    if isinstance(map_id, int):
        map_number = map_id
    elif isinstance(map_id, str) and map_id.strip().isdigit():
        try:
            map_number = int(map_id.strip())
        except (OverflowError, TypeError, ValueError):
            return None
    else:
        return None
    if map_number < 0:
        return None
    fee = _PET_SETTLEMENT_ENTRY_FEE_BY_PREFIX.get(str(map_number)[:2])
    return float(fee) if fee is not None else None


def pet_own_settlement_net_result(
    snapshot: Any,
) -> tuple[str, float] | None:
    """Return ``(session_id, signed_net)`` for an authoritative own-win result.

    This is an animation-only consumer of settlement truth. Missing identity,
    incomplete truth, unknown maps and malformed amounts all fail closed so a
    Pet reaction can never imply a result the calculator did not prove.
    """
    if not isinstance(snapshot, dict):
        return None
    ui_contract = (
        snapshot.get("ui_contract")
        if isinstance(snapshot.get("ui_contract"), dict)
        else {}
    )
    context = (
        ui_contract.get("context")
        if isinstance(ui_contract.get("context"), dict)
        else {}
    )
    phase = str(snapshot.get("phase") or context.get("phase") or "").strip().lower()
    if phase != "settled" or snapshot.get("settlement_incomplete") is not False:
        return None
    if snapshot.get("winner_is_self") is not True:
        return None

    known_value = _pet_nonnegative_finite_amount(snapshot.get("known_value_sum"))
    self_bid = _pet_nonnegative_finite_amount(snapshot.get("self_bid"))
    if known_value is None or self_bid is None:
        return None

    # Match "我的记录": known map families always use the authoritative
    # family fee. Snapshot/table entry_fee_silver may be stale (notably 56xx)
    # and unknown maps must not gain celebration authority from an explicit fee.
    map_id = snapshot.get("map_id") or context.get("map_id")
    entry_fee = _pet_authoritative_entry_fee_silver(map_id)
    if entry_fee is None:
        return None

    session_id = str(
        snapshot.get("session_id") or context.get("session_id") or ""
    ).strip()
    if not session_id:
        return None
    return session_id, known_value - self_bid - entry_fee


def pet_profitable_own_settlement(
    snapshot: Any,
) -> tuple[str, float] | None:
    """Compatibility view for consumers that only need a proven positive result."""

    outcome = pet_own_settlement_net_result(snapshot)
    if outcome is None:
        return None
    session_id, net_result = outcome
    return (session_id, net_result) if net_result > 0 else None


def pet_settlement_reaction(
    snapshot: Any,
) -> tuple[str, float, str, str] | None:
    """Map one proven own settlement to its settlement-only Pet reaction."""

    outcome = pet_own_settlement_net_result(snapshot)
    if outcome is None:
        return None
    session_id, net_result = outcome
    if net_result == 0:
        return None
    if net_result > 0:
        action = (
            "big_win"
            if net_result >= PET_SETTLEMENT_TIER_THRESHOLD
            else "perk"
        )
    else:
        action = (
            "loss_droop"
            if abs(net_result) >= PET_SETTLEMENT_TIER_THRESHOLD
            else "loss_peek"
        )
    bubble = PET_ACTION_BUBBLES[action][0]
    return session_id, net_result, action, bubble


def split_purple_gold_display_summary(value: Any) -> tuple[str, str]:
    """Turn the legacy combined purple/gold summary into two readable Tk row values.

    The panel/API contract remains ``quality_count_summary`` so HTML, diagnostics and older
    consumers keep seeing the same text.  Tk only changes presentation: each tier gets one row,
    while count/cell units and approximation markers remain visible.
    """
    text = _text(value, "").strip()
    if not text or text == "-":
        return "-", "-"

    values = {"紫": "-", "金": "-"}
    for raw_part in text.split(" · "):
        part = raw_part.strip()
        label = next((candidate for candidate in ("紫", "金") if part.startswith(candidate)), None)
        if label is None:
            continue
        content = part[len(label):].strip()
        if content.startswith("品"):
            content = content[1:].lstrip("：: ")
        if content.startswith("件 "):
            count_text, separator, cells_text = content[2:].partition(" ")
            content = f"{count_text}件"
            if separator and cells_text:
                content += f" · {cells_text}"
        else:
            match = re.fullmatch(r"(.+?件)/(.*格)", content)
            if match is not None:
                content = f"{match.group(1)} · {match.group(2)}"
        values[label] = content or "-"
    return values["紫"], values["金"]


def _legacy_quality_fallback_is_provably_physical(value: Any) -> bool:
    """Accept only legacy rows whose complete count/cell relation is provable.

    A cached canonical composition is preferred by ``render``.  This parser is
    only a settlement/continuity last resort for legacy summaries.  If both
    dimensions vary, the marginal text cannot prove which count belongs to
    which cell footprint and therefore fails closed.
    """
    if type(value) is not str:  # noqa: E721 - JSON/UI trust boundary
        return False
    match = re.fullmatch(r"\s*(.+?)件\s*·\s*(.+?)格\s*", value)
    if match is None:
        return False

    def _axis(raw: str) -> tuple[tuple[int, int], ...] | None:
        parts: list[tuple[int, int]] = []
        for token in raw.split("/"):
            normalized = token.strip().lstrip("约≈~ ")
            exact = re.fullmatch(r"\d+", normalized)
            if exact is not None:
                number = int(normalized)
                parts.append((number, number))
                continue
            interval = re.fullmatch(r"(\d+)\s*[-–—]\s*(\d+)", normalized)
            if interval is None:
                return None
            low, high = (int(interval.group(1)), int(interval.group(2)))
            if low > high:
                return None
            parts.append((low, high))
        return tuple(parts) if parts else None

    counts = _axis(match.group(1))
    cells = _axis(match.group(2))
    if counts is None or cells is None:
        return False
    count_varies = any(low != high for low, high in counts) or len(set(counts)) > 1
    cells_vary = any(low != high for low, high in cells) or len(set(cells)) > 1
    if count_varies and cells_vary:
        return False
    if len(counts) == 1:
        counts = counts * len(cells)
    elif len(cells) == 1:
        cells = cells * len(counts)
    elif len(counts) != len(cells):
        return False
    for (count_low, count_high), (cells_low, cells_high) in zip(counts, cells):
        count_zero = count_low == count_high == 0
        cells_zero = cells_low == cells_high == 0
        if count_zero != cells_zero:
            return False
        if not count_zero and (count_low == 0 or cells_low == 0):
            return False
    return True


def format_quality_row_with_verified_fallback(
    quality: QualityComposition | None,
    fallback: Any,
    *,
    allow_fallback: bool,
) -> str:
    """Prefer current paired candidates; admit legacy text only from verified continuity.

    Ordinary loading/unavailable frames must never turn a marginal or placeholder
    summary into ``待定件``.  Settlement truth and the explicit same-session
    continuity path may retain their last verified compact count/cell text while
    the structured projection is unavailable.
    """
    current = format_compact_quality_footprint(quality)
    if current != "—件·—格" or not allow_fallback:
        return current
    text = _text(fallback, "").strip()
    if (
        not text
        or "待定" in text
        or "未锁" in text
        or not _legacy_quality_fallback_is_provably_physical(text)
    ):
        return "—件·—格"
    return text


def split_candidate_dimension_summary(value: Any) -> tuple[str, str]:
    """Project the legacy compact candidate line into aligned count/grid rows.

    This is display-only: the server remains authoritative for exact-vs-estimated
    wording.  Reject non-built-in strings before any protocol call, and preserve the
    complete original line in the caller's hover rather than guessing missing data.
    """

    if type(value) is not str:  # noqa: E721 - UI trust boundary
        return "-", "-"
    count_text = "-"
    grid_text = "-"
    for raw_part in str.split(value, " · "):
        part = str.strip(raw_part)
        if part.startswith("总件 "):
            body = part[len("总件 ") :].strip()
            count_text = f"{body}{'' if body.endswith('件') else '件'}" if body else "-"
        elif part.startswith("估总件 "):
            body = part[len("估总件 ") :].strip()
            count_text = f"估 {body}{'' if body.endswith('件') else '件'}" if body else "-"
        elif part.startswith("总格 "):
            body = part[len("总格 ") :].strip()
            grid_text = f"{body}{'' if body.endswith('格') else '格'}" if body else "-"
        elif part.startswith("估总格 "):
            body = part[len("估总格 ") :].strip()
            grid_text = f"估 {body}{'' if body.endswith('格') else '格'}" if body else "-"
    return count_text, grid_text


def merge_action_next_step_display(action: Any, next_step: Any) -> str:
    """Combine the two recommendation concepts without dropping either one.

    The compact card has one row for the current action and the next useful evidence.  Empty,
    placeholder and duplicate values collapse cleanly so waiting/settlement states stay concise.
    """
    action_text = _text(action, "").strip()
    next_text = _text(next_step, "").strip()
    parts: list[str] = []
    for text in (action_text, next_text):
        if text and text != "-" and text not in parts:
            parts.append(text)
    return " · ".join(parts) if parts else "-"


def action_next_step_hover(action: Any, next_step: Any, extra: Any = None) -> str:
    """Build a structured hover so a merged recommendation row remains lossless."""
    action_text = _text(action, "").strip()
    next_text = _text(next_step, "").strip()
    extra_text = _text(extra, "").strip()
    blocks = []
    if action_text and action_text != "-":
        blocks.append(f"当前动作\n  {action_text}")
    if next_text and next_text != "-" and next_text != action_text:
        blocks.append(f"下一步\n  {next_text}")
    if extra_text:
        blocks.append(f"道具/技能说明\n  {extra_text}")
    return "\n\n".join(blocks)


def ui_prefs_path_for_snapshot(snapshot_path: Path) -> Path:
    """UI 偏好(配色主题/窗口位置大小/展开态)存放路径。
    优先【包外每用户目录】%LOCALAPPDATA%\\bidking(与激活缓存 activation_cache.bin 同处)——版本更新
    频繁, 换新包/更新版本【不丢配色】。取不到(非 Windows / bidking_lab 导入失败)时回退快照同级目录
    (旧行为, 保证 dev/测试兜底可用)。"""
    try:
        from bidking_lab.secure.client_activate import _app_dir  # noqa: PLC0415
        return _app_dir() / UI_PREFS_FILENAME
    except Exception:  # noqa: BLE001
        return Path(snapshot_path).parent / UI_PREFS_FILENAME


def _clamp_ui_pref_size(width: int, height: int) -> tuple[int, int]:
    return (
        max(430, min(1200, int(width))),
        max(320, min(1500, int(height))),
    )


def _normalize_pet_preferences(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    settlement_feedback_enabled = raw.get("settlement_feedback_enabled", True)
    normalized: dict[str, Any] = {
        "enabled": bool(raw.get("enabled")),
        "color": normalize_pet_color(raw.get("color")),
        "settlement_feedback_enabled": (
            settlement_feedback_enabled
            if isinstance(settlement_feedback_enabled, bool)
            else True
        ),
    }
    position = normalize_pet_position(raw.get("position"))
    if position is not None:
        normalized["position"] = [int(position[0]), int(position[1])]
    return normalized


def normalize_ui_prefs_payload(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    theme_name = raw.get("theme_name")
    if theme_name is not None and not isinstance(theme_name, str):
        return None
    pet_touch_date, pet_touch_today, pet_touch_total = normalize_pet_touch_state(
        raw.get("pet_touch_date"),
        raw.get("pet_touch_today"),
        raw.get("pet_touch_total"),
    )
    pet_trial_seconds_remaining = normalize_pet_trial_seconds(
        raw.get("pet_trial_seconds_remaining"),
        legacy_started_at=raw.get("pet_trial_started_at"),
        legacy_expires_at=raw.get("pet_trial_expires_at"),
    )
    normalized: dict[str, Any] = {
        "schema_version": UI_PREFS_SCHEMA_VERSION,
        "theme_name": str(theme_name or "warm"),
        "details_expanded": bool(raw.get("details_expanded")),
        "ui_scale": float(raw.get("ui_scale") or 1.0),
        "pet_intro_seen": bool(raw.get("pet_intro_seen")),
        "pet_discovery_seen": bool(raw.get("pet_discovery_seen")),
        "pet_touch_date": pet_touch_date,
        "pet_touch_today": pet_touch_today,
        "pet_touch_total": pet_touch_total,
        "pet_trial_seconds_remaining": pet_trial_seconds_remaining,
    }
    if "mini_ui_scale" in raw:
        try:
            mini_ui_scale = float(raw.get("mini_ui_scale"))
            if math.isfinite(mini_ui_scale):
                normalized["mini_ui_scale"] = max(
                    UI_SCALE_MIN,
                    min(UI_SCALE_MAX, mini_ui_scale),
                )
        except (TypeError, ValueError):
            pass
    pet_preferences_raw = raw.get("pet_preferences")
    if not isinstance(pet_preferences_raw, dict) and any(
        key in raw for key in ("pet_enabled", "pet_color", "pet_position")
    ):
        # Schema <= 5 stored these fields for every user. Keep them as a pending
        # migration candidate; the overlay applies/writes the block only after an
        # explicit 365-day or permanent entitlement is confirmed.
        pet_preferences_raw = {
            "enabled": raw.get("pet_enabled"),
            "color": raw.get("pet_color"),
            "position": raw.get("pet_position"),
        }
    pet_preferences = _normalize_pet_preferences(pet_preferences_raw)
    if pet_preferences is not None:
        normalized["pet_preferences"] = pet_preferences
    position = raw.get("window_position")
    if isinstance(position, (list, tuple)) and len(position) == 2:
        try:
            normalized["window_position"] = [
                int(position[0]),
                int(position[1]),
            ]
        except (TypeError, ValueError):
            pass
    for key in ("custom_mini_size", "custom_details_size"):
        value = raw.get(key)
        if isinstance(value, (list, tuple)) and len(value) == 2:
            try:
                normalized[key] = list(_clamp_ui_pref_size(int(value[0]), int(value[1])))
            except (TypeError, ValueError):
                continue
        elif value is None:
            normalized[key] = None
    return normalized


def read_ui_prefs(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return normalize_ui_prefs_payload(payload)


def write_ui_prefs(path: Path, payload: dict[str, Any]) -> None:
    normalized = normalize_ui_prefs_payload(payload)
    if normalized is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


MANUAL_GENERIC_MAP_ALIASES = {
    "快递": 2101,
    "仓库": 2201,
    "集装箱": 2301,
    "箱子": 2301,
    "别墅": 2401,
    "残骸": 2501,
    "沉船": 2501,
    "船": 2501,
    "隐秘拍卖": 2601,
    "隐秘拍卖会": 2601,
    "拍卖会": 2601,
}
AUTO_REPLACE_MANUAL_FIELDS = {
    "hero",
    "map_id",
    "total_count",
    "total_cells",
    "total_avg",
    "q4q5_count",
}
MAINLINE_QUALITY_STYLE = {
    "q1": {"fill": "#cbd5e1", "outline": "#94a3b8", "unknown": False},
    "q2": {"fill": "#86efac", "outline": "#bbf7d0", "unknown": False},
    "q3": {"fill": "#60a5fa", "outline": "#bfdbfe", "unknown": False},
    "q4": {"fill": "#c084fc", "outline": "#e9d5ff", "unknown": False},
    "q5": {"fill": "#fbbf24", "outline": "#d97706", "unknown": False},
    "q6": {"fill": "#fb7185", "outline": "#e11d48", "unknown": False},
    "treasure": {"fill": "#fbbf24", "outline": "#d97706", "unknown": False},
    "unknown": {"fill": "#4a5d78", "outline": "#94a3b8", "unknown": True},
}

# Rounded-corner radius (px) for the borderless overlay's outer window.
WINDOW_CORNER_RADIUS = 14

# Support-author Alipay QR shown on hover over the 投喂罐头 mascot. Bundled asset
# (the author's donation code — meant to be public/scanned). Override via env
# BIDKING_SUPPORT_QR. PIL (Pillow) loads it; missing/unloadable -> QR popup silently
# skipped (the cat + can still show).
SUPPORT_QR_IMAGE_PATH = os.environ.get(
    "BIDKING_SUPPORT_QR",
    str(Path(__file__).resolve().parent.parent / "assets" / "support_qr.png"),
)
SUPPORT_FEED_TEXT = "喵~ 猫猫饿了\n投喂一罐?"
SHOP_URL = "https://www.kufaka.com/shop/GGTO42L9"
SHOP_QR_IMAGE_PATH = Path(__file__).resolve().parent.parent / "assets" / "shop_qr.png"
# Rounded-corner radius (px) for inner content cards (canvas-drawn).
CARD_CORNER_RADIUS = 9


def _round_rect_points(x1: float, y1: float, x2: float, y2: float, r: float) -> list[float]:
    """Control points for a smooth (spline) rounded rectangle canvas polygon.

    Corner points are duplicated so create_polygon(smooth=True) rounds only at the
    four corners and keeps the edges straight.
    """
    r = max(0.0, min(r, (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]

THEME_ORDER = ("warm", "blue", "dark", "light", "random")
THEMES: dict[str, dict[str, Any]] = {
    "blue": {
        "label": "蓝色",
        "BG": "#243b63",
        "PANEL": "#2d456d",
        "PANEL_SOFT": "#3d6090",
        "PANEL_MUTED": "#34446c",
        "BORDER": "#6f91c2",
        "TEXT": "#f7fbff",
        "MUTED": "#cbd9ee",
        "DIM": "#93a8c5",
        "GOOD": "#67e6bc",
        "WARN": "#ffd166",
        "BAD": "#ff6f91",
        "ACCENT": "#7ad7ff",
        "WARM": "#ffb86b",
        "MINIMAP_BG": "#1f3152",
        "MINIMAP_GRID": "#496b9a",
        "MINIMAP_TEXT": "#bdd1e8",
        "QUALITY_COLORS": {
            "q1": "#8d94a7",
            "q2": "#d8dce4",
            "q3": "#5bbff0",
            "q4": "#b894f6",
            "q5": "#ffd45a",
            "q6": "#ff747c",
            "unknown": "#455a78",
        },
        "HERO_BUTTON_ACTIVE": "#4e73a6",
        "SCROLL_LINE": "#5878a8",
        "TOOLTIP_BG": "#203659",
        "MANUAL_BG": "#30456d",
        "MANUAL_INPUT_BG": "#203659",
        "MANUAL_STATUS_BG": "#4b3a2a",
        "MANUAL_LABEL_FG": "#dfe9f8",
        "MINIMAP_ITEM_SHADOW": "#172641",
        "MINIMAP_ITEM_OUTLINE": "#172641",
        "BUTTON_DARK_FG": "#251f2b",
    },
    "dark": {
        "label": "暗色",
        "BG": "#141923",
        "PANEL": "#202838",
        "PANEL_SOFT": "#2b3548",
        "PANEL_MUTED": "#28283a",
        "BORDER": "#46536a",
        "TEXT": "#f4f7fb",
        "MUTED": "#bbc6d5",
        "DIM": "#8794aa",
        "GOOD": "#5fd39d",
        "WARN": "#f1c85f",
        "BAD": "#ef718f",
        "ACCENT": "#6bbde8",
        "WARM": "#f4b66a",
        "MINIMAP_BG": "#121927",
        "MINIMAP_GRID": "#2b3446",
        "MINIMAP_TEXT": "#aab6c9",
        "QUALITY_COLORS": {
            key: value["fill"] for key, value in MAINLINE_QUALITY_STYLE.items()
        },
        "HERO_BUTTON_ACTIVE": "#36445d",
        "SCROLL_LINE": "#3a465c",
        "TOOLTIP_BG": "#171d2a",
        "MANUAL_BG": "#28253d",
        "MANUAL_INPUT_BG": "#192132",
        "MANUAL_STATUS_BG": "#403326",
        "MANUAL_LABEL_FG": "#dce5f1",
        "MINIMAP_ITEM_SHADOW": "#0f1623",
        "MINIMAP_ITEM_OUTLINE": "#0f1623",
        "BUTTON_DARK_FG": "#251f2b",
    },
    "light": {
        "label": "亮色",
        "BG": "#eef5fb",
        "PANEL": "#ffffff",
        "PANEL_SOFT": "#dcebf9",
        "PANEL_MUTED": "#f6f1fb",
        "BORDER": "#95accb",
        "TEXT": "#17233a",
        "MUTED": "#425472",
        "DIM": "#7182a0",
        "GOOD": "#0f9f75",
        "WARN": "#b97900",
        "BAD": "#d83b66",
        "ACCENT": "#1677b7",
        "WARM": "#f2a642",
        "MINIMAP_BG": "#e7f0fa",
        "MINIMAP_GRID": "#bfd2e7",
        "MINIMAP_TEXT": "#586d89",
        "QUALITY_COLORS": {
            "q1": "#8f98a6",
            "q2": "#d4dae2",
            "q3": "#42aee2",
            "q4": "#a97fed",
            "q5": "#efbb35",
            "q6": "#ef5f6c",
            "unknown": "#bcc9d8",
        },
        "HERO_BUTTON_ACTIVE": "#c7def5",
        "SCROLL_LINE": "#aac2dd",
        "TOOLTIP_BG": "#ffffff",
        "MANUAL_BG": "#fff4e4",
        "MANUAL_INPUT_BG": "#edf4fb",
        "MANUAL_STATUS_BG": "#ffe5b8",
        "MANUAL_LABEL_FG": "#324460",
        "MINIMAP_ITEM_SHADOW": "#c5d1de",
        "MINIMAP_ITEM_OUTLINE": "#edf4fb",
        "BUTTON_DARK_FG": "#251f2b",
    },
    # Anthropic-inspired warm theme: cream "paper" ground, clay/terracotta accents.
    # Retro and clean — quality colours stay semantic (q5 gold, q6 red) but are
    # muted to sit on the warm paper instead of a dark/blue panel.
    "warm": {
        "label": "暖橙",
        "BG": "#f0eee6",          # Anthropic "Cloud" paper
        "PANEL": "#faf9f5",       # ivory card
        "PANEL_SOFT": "#ece6d8",  # warm soft panel
        "PANEL_MUTED": "#f3ecdf", # warm peachy muted panel
        "BORDER": "#d6ccba",      # tan hairline border
        "TEXT": "#1f1e1d",        # warm ink (near-black)
        "MUTED": "#6b6557",       # warm gray
        "DIM": "#938b7b",         # light warm gray
        "GOOD": "#3f7d5a",        # muted retro green
        "WARN": "#b6781f",        # ochre amber
        "BAD": "#c0492f",         # terracotta red
        "ACCENT": "#cc785c",      # Anthropic "Clay" — signature orange
        "WARM": "#d97757",        # Claude coral
        "MINIMAP_BG": "#e9e2d2",  # parchment
        "MINIMAP_GRID": "#cbbfa6",# tan grid
        "MINIMAP_TEXT": "#7a7263",
        "QUALITY_COLORS": {
            "q1": "#9a9183",
            "q2": "#d8cfbe",
            "q3": "#4f97c4",
            "q4": "#a07ae0",
            "q5": "#e0a020",
            "q6": "#d6473a",
            "unknown": "#cabfa8",
        },
        "HERO_BUTTON_ACTIVE": "#e6d6c2",
        "SCROLL_LINE": "#cdbfa6",
        "TOOLTIP_BG": "#fbfaf6",
        "MANUAL_BG": "#f6ebd8",
        "MANUAL_INPUT_BG": "#fbf6ec",
        "MANUAL_STATUS_BG": "#f0dcbe",
        "MANUAL_LABEL_FG": "#4a4334",
        "MINIMAP_ITEM_SHADOW": "#d6cbb4",
        "MINIMAP_ITEM_OUTLINE": "#efe8d9",
        "BUTTON_DARK_FG": "#251f2b",
    },
}


def _hsl_color(hue: float, saturation: float, lightness: float) -> str:
    red, green, blue = colorsys.hls_to_rgb(hue % 1.0, lightness, saturation)
    return f"#{int(red * 255):02x}{int(green * 255):02x}{int(blue * 255):02x}"


def _random_theme() -> dict[str, Any]:
    hue = random.random()
    return {
        "label": "随机",
        "BG": _hsl_color(hue, 0.38, 0.25),
        "PANEL": _hsl_color(hue, 0.34, 0.33),
        "PANEL_SOFT": _hsl_color(hue, 0.38, 0.43),
        "PANEL_MUTED": _hsl_color(hue + 0.06, 0.30, 0.34),
        "BORDER": _hsl_color(hue, 0.28, 0.61),
        "TEXT": "#fbf8f2",
        "MUTED": _hsl_color(hue, 0.30, 0.80),
        "DIM": _hsl_color(hue, 0.25, 0.66),
        "GOOD": "#67e6bc",
        "WARN": "#ffd166",
        "BAD": "#ff6f91",
        "ACCENT": _hsl_color(hue + 0.43, 0.75, 0.70),
        "WARM": _hsl_color(hue + 0.11, 0.85, 0.70),
        "MINIMAP_BG": _hsl_color(hue, 0.42, 0.20),
        "MINIMAP_GRID": _hsl_color(hue, 0.32, 0.43),
        "MINIMAP_TEXT": _hsl_color(hue, 0.28, 0.78),
        "QUALITY_COLORS": THEMES["blue"]["QUALITY_COLORS"],
        "HERO_BUTTON_ACTIVE": _hsl_color(hue, 0.40, 0.48),
        "SCROLL_LINE": _hsl_color(hue, 0.30, 0.50),
        "TOOLTIP_BG": _hsl_color(hue, 0.42, 0.21),
        "MANUAL_BG": _hsl_color(hue + 0.08, 0.32, 0.32),
        "MANUAL_INPUT_BG": _hsl_color(hue, 0.40, 0.22),
        "MANUAL_STATUS_BG": "#4b3a2a",
        "MANUAL_LABEL_FG": _hsl_color(hue, 0.28, 0.84),
        "MINIMAP_ITEM_SHADOW": _hsl_color(hue, 0.45, 0.16),
        "MINIMAP_ITEM_OUTLINE": _hsl_color(hue, 0.45, 0.16),
        "BUTTON_DARK_FG": "#251f2b",
    }


def _theme_by_name(name: str) -> dict[str, Any]:
    if name == "random":
        return _random_theme()
    return THEMES.get(name, THEMES["dark"])


def _apply_theme_globals(theme: dict[str, Any]) -> None:
    globals_to_update = {
        key: value
        for key, value in theme.items()
        if key.isupper() and key != "QUALITY_COLORS"
    }
    globals().update(globals_to_update)
    globals()["QUALITY_COLORS"] = dict(theme["QUALITY_COLORS"])


_apply_theme_globals(THEMES["warm"])


def _flat_theme_colors(theme: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in theme.items():
        if isinstance(value, str) and value.startswith("#"):
            out[key] = value.lower()
    quality = theme.get("QUALITY_COLORS")
    if isinstance(quality, dict):
        for key, value in quality.items():
            if isinstance(value, str) and value.startswith("#"):
                out[f"QUALITY_{key}"] = value.lower()
    return out


def _theme_replacements(old_theme: dict[str, Any], new_theme: dict[str, Any]) -> dict[str, str]:
    old_flat = _flat_theme_colors(old_theme)
    new_flat = _flat_theme_colors(new_theme)
    replacements: dict[str, str] = {}
    for key, old_value in old_flat.items():
        new_value = new_flat.get(key)
        if new_value and new_value != old_value:
            replacements[old_value] = new_value
    return replacements


def _read_json(path: Path) -> dict[str, Any]:
    # 跨进程读写竞争: monitor 用 os.replace 原子换名写 latest_snapshot.json(249KB), 换名瞬间本进程
    # open() 在 Windows 会撞 sharing-violation(PermissionError)/瞬时读失败。竞价期帧多→高频换文件→
    # 撞车频繁; 若单次失败就 return {}, refresh() 会当"无快照"把整个 UI blank 成占位("等 latest_snapshot")
    # ——整局不显示、结算后手动点 tab 才刷出来(实测 67/116 tick missing, 集中在竞价段)。故: 文件确实
    # 不存在(FileNotFound)= 真无快照立即返回; 其余瞬时失败短重试几次(换名 sub-ms, 20ms 后必已稳定)。
    for attempt in range(4):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return {}
        except Exception:
            if attempt >= 3:
                return {}
            time.sleep(0.02 * (attempt + 1))
            continue
        return payload if isinstance(payload, dict) else {}
    return {}


def _text(value: Any, fallback: str = "-") -> str:
    if value is None or value == "":
        return fallback
    return str(value)


def _normalize_diagnostic_profile(value: Any) -> str:
    profile = str(value or DEFAULT_DIAGNOSTIC_PROFILE).strip().lower()
    normalized = DIAGNOSTIC_PROFILE_ALIASES.get(profile)
    if normalized not in DIAGNOSTIC_PROFILES:
        return DEFAULT_DIAGNOSTIC_PROFILE
    return normalized


def _parse_diagnostic_profile(value: str) -> str:
    profile = str(value or "").strip().lower()
    if profile not in DIAGNOSTIC_PROFILE_ALIASES:
        raise argparse.ArgumentTypeError(
            "expected one of: engineering, portable, public-safe"
        )
    return _normalize_diagnostic_profile(profile)


def _apply_windows_toolwindow(widget: tk.Misc, *, enabled: bool) -> None:
    if not enabled or os.name != "nt":
        return
    try:
        widget.attributes("-toolwindow", True)
    except tk.TclError:
        pass


def _appwindow_hwnd(widget: tk.Misc) -> int:
    """The shell-tracked top-level HWND for a Tk window. Tk wraps the toplevel in a frame, so the
    window the taskbar tracks is GetParent(winfo_id) (falls back to the GA_ROOT ancestor / the id)."""
    import ctypes

    wid = int(widget.winfo_toplevel().winfo_id())
    u = ctypes.windll.user32
    return int(u.GetParent(wid) or u.GetAncestor(wid, 2) or wid)


def _set_windows_redraw(widget: tk.Misc, *, enabled: bool) -> bool:
    """Suspend/resume painting for one synchronous layout transaction on Windows.

    Tk may expose every ``pack -> measure -> geometry -> remeasure`` intermediate state while a
    details/manual transition is still running.  ``WM_SETREDRAW`` keeps the last complete frame on
    screen until the final layout is ready; enabling performs one synchronous full-tree repaint.
    Returns whether a native redraw state was actually changed (off-Windows/test doubles are no-op).
    """
    if os.name != "nt":
        return False
    try:
        import ctypes

        top = widget.winfo_toplevel()
        # Freeze Tk's client HWND, not its outer shell/taskbar wrapper.  WM_SETREDRAW on the wrapper
        # while geometry changes makes Tk observe the toplevel as unmapped, which breaks the real
        # details/manual mapping contract even after native drawing is re-enabled.
        hwnd = int(top.winfo_id())
        if not hwnd:
            return False
        user32 = ctypes.windll.user32
        WM_SETREDRAW = 0x000B
        user32.SendMessageW(hwnd, WM_SETREDRAW, 1 if enabled else 0, 0)
        if enabled:
            RDW_INVALIDATE = 0x0001
            RDW_ERASE = 0x0004
            RDW_FRAME = 0x0400
            RDW_ALLCHILDREN = 0x0080
            RDW_UPDATENOW = 0x0100
            user32.RedrawWindow(
                hwnd,
                None,
                None,
                RDW_INVALIDATE
                | RDW_ERASE
                | RDW_FRAME
                | RDW_ALLCHILDREN
                | RDW_UPDATENOW,
            )
        return True
    except Exception:
        return False


def _apply_windows_appwindow(widget: tk.Misc, *, enabled: bool) -> None:
    """Give a BORDERLESS (overrideredirect) Tk window a Windows taskbar + Alt-Tab entry WITHOUT a
    native frame and WITHOUT a second window. overrideredirect windows are normally excluded from the
    taskbar; setting WS_EX_APPWINDOW (and clearing WS_EX_TOOLWINDOW) forces this same window in. The
    shell only re-reads the ex-style across a hide/show cycle, so we withdraw + re-deiconify. MUST run
    AFTER the window is mapped (see _apply_taskbar_mode's deferral), else the taskbar ignores it.
    No-op off Windows."""
    if not enabled or os.name != "nt":
        return
    try:
        import ctypes

        u = ctypes.windll.user32
        top = widget.winfo_toplevel()
        top.update_idletasks()
        hwnd = _appwindow_hwnd(top)
        if not hwnd:
            return
        GWL_EXSTYLE = -20
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_APPWINDOW = 0x00040000
        style = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
        desired = (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
        if style == desired:
            return  # 样式已就位(taskbar 已注册)→ 跳过隐显，避免第二次注册时的多余闪烁(只闪一次)
        u.SetWindowLongW(hwnd, GWL_EXSTYLE, desired)
        # Re-register with the shell so the taskbar button appears. Hide/show the OS window via Win32
        # ShowWindow (NOT Tk withdraw/deiconify, which un-maps the toplevel and left the borderless
        # window blank). Win32 hide/show triggers the same shell re-read but keeps Tk mapped; SW_SHOWNA
        # re-shows without stealing focus. After re-show, child display-boxes can come back blank until
        # their next WM_PAINT — so force a SYNCHRONOUS full-tree repaint with RedrawWindow
        # (RDW_ALLCHILDREN|RDW_UPDATENOW), which is what fixes "some 显示框 blank out" on taskbar apply.
        SW_HIDE, SW_SHOWNA = 0, 8
        u.ShowWindow(hwnd, SW_HIDE)
        u.ShowWindow(hwnd, SW_SHOWNA)
        RDW_INVALIDATE, RDW_ERASE, RDW_ALLCHILDREN, RDW_UPDATENOW = 0x0001, 0x0004, 0x0080, 0x0100
        u.RedrawWindow(hwnd, None, None,
                       RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_UPDATENOW)
        # Tk canvas 内容由 Tk 自己重绘; update_idletasks 只跑 idle 任务、不处理 expose 事件，所以
        # 子画布会留空 —— 用 top.update() 处理 hide/show 产生的 expose，让 Tk 真正重画(空白bug关键)。
        top.update()
    except Exception:
        pass


def _apply_taskbar_mode(root: tk.Tk, *, show_taskbar: bool) -> None:
    """Keep the overlay BORDERLESS in both modes (the overlay's whole design). When show_taskbar,
    give that same borderless window a taskbar/Alt-Tab entry via WS_EX_APPWINDOW (no frame, no second
    window) -- useful for stream window-capture and Alt-Tab. The old behavior dropped overrideredirect
    to earn a taskbar entry, which re-added a redundant native-bordered window."""
    root.overrideredirect(True)
    if not show_taskbar:
        _apply_windows_toolwindow(root, enabled=True)


def _reveal_root_when_ready(
    root: tk.Tk,
    *,
    show_taskbar: bool,
    restore_alpha: float | None = None,
) -> None:
    """Reveal a fully-laid-out overlay once, with its taskbar identity already established.

    Production startup maps the Tk root at alpha=0 while constructing the widget tree.  Mapping is
    intentional: withdrawn Tk windows report different geometry and used to shrink the 510px mini
    layout to 440px.  Taskbar mode still needs a mapped HWND before Windows accepts
    ``WS_EX_APPWINDOW``; perform the shell re-registration/repaint while transparent, then restore
    opacity so the user sees one complete frame rather than a visible hide/show cycle.
    """
    root.update_idletasks()
    if restore_alpha is None:
        try:
            restore_alpha = float(root.attributes("-alpha"))
        except (tk.TclError, TypeError, ValueError):
            restore_alpha = None
    try:
        if show_taskbar and os.name == "nt":
            try:
                root.attributes("-alpha", 0.0)
            except tk.TclError:
                pass
        root.deiconify()
        root.update_idletasks()
        if show_taskbar and os.name == "nt":
            _apply_windows_appwindow(root, enabled=True)
            root.update_idletasks()
    finally:
        if restore_alpha is not None:
            try:
                root.attributes("-alpha", restore_alpha)
            except tk.TclError:
                pass


def _apply_rounded_window_region(widget: tk.Misc, *, radius: int = 14) -> None:
    """Clip a borderless Tk window to rounded corners on Windows (SetWindowRgn).

    Borderless overlays (overrideredirect) carry no native non-client frame, so
    Windows 11 DWM corner rounding never applies; we install a round-rect region
    sized to the current window instead. The region is pixel-fixed, so this must
    be re-applied whenever the window resizes. No-op off Windows / before layout.
    """
    if os.name != "nt":
        return
    try:
        import ctypes

        widget.update_idletasks()
        width = int(widget.winfo_width())
        height = int(widget.winfo_height())
        if width <= 1 or height <= 1:
            return
        hwnd = int(widget.winfo_id())
        root_hwnd = ctypes.windll.user32.GetAncestor(hwnd, 2)  # GA_ROOT
        if root_hwnd:
            hwnd = root_hwnd
        diameter = max(2, int(radius) * 2)
        # CreateRoundRectRgn is right/bottom-exclusive; +1 keeps the last row/col.
        region = ctypes.windll.gdi32.CreateRoundRectRgn(
            0, 0, width + 1, height + 1, diameter, diameter
        )
        # SetWindowRgn takes ownership of the region handle (no manual delete).
        ctypes.windll.user32.SetWindowRgn(hwnd, region, True)
    except Exception:
        pass


def _hex_to_rgb(color: str) -> tuple[int, int, int] | None:
    text = str(color or "").strip()
    if not text.startswith("#") or len(text) != 7:
        return None
    try:
        red = int(text[1:3], 16)
        green = int(text[3:5], 16)
        blue = int(text[5:7], 16)
    except ValueError:
        return None
    return red, green, blue


def _blend_hex(color: str, other: str, ratio: float) -> str:
    left = _hex_to_rgb(color)
    right = _hex_to_rgb(other)
    if left is None or right is None:
        return color
    ratio = max(0.0, min(1.0, ratio))
    mixed = tuple(
        int(round(a * (1.0 - ratio) + b * ratio))
        for a, b in zip(left, right)
    )
    return f"#{mixed[0]:02x}{mixed[1]:02x}{mixed[2]:02x}"


def _short(value: Any, limit: int = 36) -> str:
    text = _text(value, "")
    if len(text) <= limit:
        return text or "-"
    return f"{text[: limit - 1]}..."


def _hover_candidate_block(value: Any) -> str:
    """Lay out a ``标题：候选 · 候选`` sentence as a scan-friendly block."""
    text = _text(value, "").strip()
    if not text:
        return ""
    heading, separator, detail = text.partition("：")
    candidates = [part.strip() for part in detail.split(" · ") if part.strip()]
    if not separator or not candidates:
        return text
    return "\n".join((heading.strip(), *(f"  {candidate}" for candidate in candidates)))


def _structured_quality_candidate_block(
    quality: QualityComposition | None,
    *,
    dimension: str,
    heading: str,
    unit: str,
) -> str:
    """Keep a supplied marginal separate from the omitted joint probability."""
    if quality is None:
        return ""
    joint_fallback = False
    if dimension == "count" and quality.count_candidates:
        rows = [
            (candidate.value, candidate.probability)
            for candidate in quality.count_candidates
        ]
    elif dimension == "cells" and quality.cells_candidates:
        rows = [
            (candidate.value, candidate.probability)
            for candidate in quality.cells_candidates
        ]
    else:
        if not quality.candidates:
            return quality.reason
        joint_fallback = True
        probability_by_value: dict[int, float] = {}
        for candidate in quality.candidates:
            raw_value = getattr(candidate, dimension)
            probability_by_value[raw_value] = (
                probability_by_value.get(raw_value, 0.0) + candidate.probability
            )
        rows = sorted(
            probability_by_value.items(),
            key=lambda row: (-row[1], row[0]),
        )
    entries = [
        f"{value}{unit} {format_quality_probability(quality, probability)}"
        for value, probability in rows
    ]
    if quality.lock_reason == "sampled_weighted_posterior":
        # Full sampled marginals can contain hundreds of values.  Keep every
        # entry in its source order while using the existing tooltip width.
        lines = [heading, *("  " + " · ".join(entries[i:i + 4]) for i in range(0, len(entries), 4))]
    else:
        lines = [heading, *(f"  {entry}" for entry in entries)]
    if (
        quality.other_probability is not None
        and quality.other_probability > 0.0
        and (
            joint_fallback
            or quality.lock_reason != "sampled_weighted_posterior"
            or abs(sum(probability for _, probability in rows) - 1.0) > 1e-12
        )
    ):
        lines.append(f"  其他 {format_probability(quality.other_probability)}")
    return "\n".join(lines)


def _hover_probability_blocks(value: Any) -> str:
    """Split per-quality probability prose into one compact block per quality."""
    text = _text(value, "").strip()
    if not text:
        return ""
    blocks = [_hover_candidate_block(section) for section in text.split("；")]
    return "\n\n".join(block for block in blocks if block)


def _red_priority_probability_variants(value: Any) -> tuple[str, ...]:
    """Fallbacks for the one-line 金/红概率 value, always preserving the complete red segment."""
    text = _text(value, "") or "-"
    if "·" not in text:
        return (text,)
    gold, red = text.rsplit("·", 1)
    if not red.startswith("红"):
        return (text,)
    variants = (text, f"{gold[:1]}…·{red}", f"…·{red}", red)
    return tuple(dict.fromkeys(variants))


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _confidence_flag(ref_result: Any) -> dict[str, str] | None:
    """Build a total-count-confidence badge from a ref_v0 result dict (P2).

    Draw-heroes (sophie/gabriela) read 'low' with a wide band; aisha is usually 'medium'; a locked
    total or ahmed reads 'high'. Surfaces the inherent-variance signal so a low-confidence quote is
    visible during live eyeball testing.
    """
    if not isinstance(ref_result, dict):
        return None
    conf = ref_result.get("total_count_confidence")
    if not conf:
        return None
    band = ref_result.get("total_count_error_band")
    band_txt = ""
    if isinstance(band, (list, tuple)) and len(band) == 2 and None not in band:
        band_txt = f" ±{int((band[1] - band[0]) / 2)}"
    label_zh = {"low": "低置信", "medium": "中置信", "high": "高置信"}.get(str(conf), str(conf))
    level = {"low": "risk", "medium": "watch", "high": "neutral"}.get(str(conf), "neutral")
    return {
        "label": f"{label_zh}{band_txt}",
        "level": level,
        "detail": f"总件数置信 {conf} band={band}（红不可见→单仓方差固有）",
    }


def _compact_flags(flags: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not isinstance(flags, list):
        return out
    for flag in flags[:8]:
        if not isinstance(flag, dict):
            continue
        out.append(
            {
                "label": _text(flag.get("label"), ""),
                "level": _text(flag.get("level"), ""),
                "detail": _text(flag.get("detail"), ""),
            }
        )
    return out


def _bounded_exact_grid_cells(value: Any) -> int | None:
    """Return a finite, integral grid-cell value without coercing foreign types."""
    if type(value) is int:
        return value if 0 <= value <= _MANUAL_MAX_GRID_CELLS else None
    if type(value) is not float:
        return None
    if not math.isfinite(value) or not 0 <= value <= _MANUAL_MAX_GRID_CELLS:
        return None
    rounded = round(value)
    return int(rounded) if abs(value - rounded) <= 1e-6 else None


def _remaining_cells_range(ref_result: Any) -> tuple[int | None, int | None, int | None] | None:
    """Return red-plus-empty room, bounded below by exact pure-red cells."""
    if not isinstance(ref_result, dict):
        return None
    total = ref_result.get("total_grid_range")
    if not (isinstance(total, (list, tuple)) and len(total) == 3):
        return None
    parsed_total = tuple(_bounded_exact_grid_cells(tier) for tier in total)
    if all(tier is None for tier in parsed_total):
        return None
    cells = ref_result.get("quality_cells_ranges")
    if not isinstance(cells, dict):
        return None
    visible = 0
    for key in ("q1", "q3", "q4", "q5"):
        rng = cells.get(key)
        if isinstance(rng, (list, tuple)) and len(rng) == 3 and rng[1] is not None:
            parsed_visible = _bounded_exact_grid_cells(rng[1])
            if parsed_visible is not None:
                visible += parsed_visible
    evidence = ref_result.get("evidence")
    quality_cells = evidence.get("quality_cells") if isinstance(evidence, dict) else None
    raw_red_cells = quality_cells.get("q6") if isinstance(quality_cells, dict) else None
    exact_red_cells = _bounded_exact_grid_cells(raw_red_cells)
    maximum_total = max(tier for tier in parsed_total if tier is not None)
    if exact_red_cells is not None and exact_red_cells > maximum_total:
        exact_red_cells = None
    out: list[int | None] = []
    for tier in parsed_total:
        if tier is None:
            out.append(None)
            continue
        remaining = max(0, tier - visible)
        out.append(
            max(remaining, exact_red_cells)
            if exact_red_cells is not None
            else remaining
        )
    if all(v is None for v in out):
        return None
    return (out[0], out[1], out[2])


def _summary_diagnostic_row(
    data: dict[str, Any],
    *,
    snapshot_path: Path,
    render_mode: str,
    manual_active: bool,
    settlement_values_hidden: bool,
) -> dict[str, Any]:
    context = _mapping(data.get("context"))
    reference = _mapping(data.get("reference"))
    red = _mapping(data.get("red"))
    evidence = _mapping(data.get("evidence"))
    ahmed_ref = _mapping(data.get("ahmed_ref"))
    ref_evidence = _mapping(ahmed_ref.get("evidence"))
    diagnostics = _mapping(data.get("diagnostics"))
    rare_signals = _mapping(diagnostics.get("rare_signals"))
    performance = _mapping(diagnostics.get("performance"))
    minimap = _mapping(data.get("minimap"))
    truth = _mapping(data.get("truth"))
    truth_q6 = _mapping(truth.get("q6"))
    stale = _mapping(data.get("stale"))
    latest_result = evidence.get("latest_result")
    if not isinstance(latest_result, dict):
        latest_result = evidence.get("latest_sent")
    if not isinstance(latest_result, dict):
        latest_result = {}
    return {
        "logged_at": time.time(),
        "snapshot_path": str(snapshot_path),
        "status": _text(data.get("status"), "ok"),
        "render_mode": render_mode,
        "manual_active": bool(manual_active),
        "settlement_values_hidden": bool(settlement_values_hidden),
        "updated_at_text": _text(data.get("updated_at_text"), ""),
        "stale_reason": _text(stale.get("reason"), ""),
        "snapshot_age_seconds": stale.get("age_seconds"),
        "context": {
            "hero": context.get("hero"),
            "map_id": context.get("map_id"),
            "round": context.get("round"),
            "phase": context.get("phase"),
            "session_id": context.get("session_id"),
        },
        "reference": {
            "source": reference.get("source"),
            "readiness": reference.get("readiness"),
            "action": reference.get("action"),
            "current_highest": reference.get("current_highest"),
            "conservative": reference.get("conservative"),
            "balanced": reference.get("balanced"),
            "aggressive": reference.get("aggressive"),
            "decision_range": reference.get("decision_range"),
            "total_grid_range": reference.get("total_grid_range"),
            "total_value_range": reference.get("total_value_range"),
            **(
                {"total_value_bound": dict(reference["total_value_bound"])}
                if isinstance(reference.get("total_value_bound"), dict)
                else {}
            ),
            "v3_balanced": reference.get("v3_balanced"),
            "ref_minus_v3_balanced": reference.get("ref_minus_v3_balanced"),
            "note": reference.get("note"),
        },
        "red": {
            "count_range": red.get("count_range"),
            "count_display": red.get("count_display"),
            "cells_range": red.get("cells_range"),
            "value_range": red.get("value_range"),
            "quality_count_summary": red.get("quality_count_summary"),
            "quality_probability_summary": red.get("quality_probability_summary"),
            "count_probability_hover": red.get("count_probability_hover"),
            "cells_probability_hover": red.get("cells_probability_hover"),
            "purple_gold_probability_hover": red.get("purple_gold_probability_hover"),
            "quality_probability_hover": red.get("quality_probability_hover"),
            "low_quality_count_probability_hover": red.get(
                "low_quality_count_probability_hover"
            ),
            "uncertainty_summary": red.get("uncertainty_summary"),
            "risk_reference": red.get("risk_reference"),
        },
        "evidence": {
            "match_text": evidence.get("match_text"),
            "information_density": evidence.get("information_density"),
            "ref_input_summary": evidence.get("ref_input_summary"),
            "candidate_summary": evidence.get("candidate_summary"),
            "next_info_hint": evidence.get("next_info_hint"),
            "public_numeric_summary": evidence.get("public_numeric_summary"),
            "minimap_quality_summary": evidence.get("minimap_quality_summary"),
            "ref_combo_count": evidence.get("ref_combo_count"),
            "diagnostics": evidence.get("diagnostics"),
            "ref_notes": evidence.get("ref_notes"),
            "latest_result": latest_result,
            "manual_overlay": bool(evidence.get("manual_overlay")),
        },
        "ref_v0": {
            "status": ahmed_ref.get("status"),
            "source": ahmed_ref.get("source"),
            "notes": ahmed_ref.get("notes"),
            "combo_count": ahmed_ref.get("combo_count"),
            "quality_count_ranges": ahmed_ref.get("quality_count_ranges"),
            "total_grid_range": ahmed_ref.get("total_grid_range"),
            "value_range": {
                "p25": ahmed_ref.get("value_p25"),
                "p50": ahmed_ref.get("value_p50"),
                "p75": ahmed_ref.get("value_p75"),
            },
            "total_count_confidence": ahmed_ref.get("total_count_confidence"),
            "total_count_error_band": ahmed_ref.get("total_count_error_band"),
            "total_count_estimate": ahmed_ref.get("total_count_estimate"),
            "evidence": {
                "source_notes": ref_evidence.get("source_notes"),
                "total_count": ref_evidence.get("total_count"),
                "total_grid_target": ref_evidence.get("total_grid_target"),
                "fixed_counts": ref_evidence.get("fixed_counts"),
                "min_counts": ref_evidence.get("min_counts"),
                "avg_cells": ref_evidence.get("avg_cells"),
                "quality_cells": ref_evidence.get("quality_cells"),
                "avg_values": ref_evidence.get("avg_values"),
                "quality_values": ref_evidence.get("quality_values"),
                "quality_count_cells_options": ref_evidence.get(
                    "quality_count_cells_options"
                ),
                "quality_count_cells_value_options": ref_evidence.get(
                    "quality_count_cells_value_options"
                ),
                "quality_avg_value_intervals": ref_evidence.get(
                    "quality_avg_value_intervals"
                ),
                "q6_full_avg_support": ref_evidence.get("q6_full_avg_support"),
                "q4_known_item_facts": ref_evidence.get("q4_known_item_facts"),
                "q5_known_item_facts": ref_evidence.get("q5_known_item_facts"),
                "q6_known_item_facts": ref_evidence.get("q6_known_item_facts"),
                **(
                    {
                        "physical_candidate_domains": ref_evidence.get(
                            "physical_candidate_domains"
                        )
                    }
                    if isinstance(
                        ref_evidence.get("physical_candidate_domains"),
                        list,
                    )
                    and ref_evidence.get("physical_candidate_domains")
                    else {}
                ),
            },
        },
        "minimap": {
            "summary_text": minimap.get("summary_text"),
            "quality_counts": minimap.get("quality_counts"),
            "items_count": len(minimap.get("items") or ()),
            "source": minimap.get("source"),
        },
        "truth": {
            "available": bool(truth.get("available")),
            "total_value": truth.get("total_value"),
            "total_items": truth.get("total_items"),
            "total_cells": truth.get("total_cells"),
            "q6": {
                "count": truth_q6.get("count"),
                "cells": truth_q6.get("cells"),
                "value": truth_q6.get("value"),
            },
        },
        "diagnostics": {
            "rare_signals": {
                "present": bool(rare_signals.get("present")),
                "summary": rare_signals.get("summary"),
                "role_counts": rare_signals.get("role_counts"),
                "actions": rare_signals.get("actions"),
                "public_info": rare_signals.get("public_info"),
            },
            "performance": {
                "summary_total_ms": performance.get("summary_total_ms"),
                "ref_engine_ms": performance.get("ref_engine_ms"),
                "settlement_ref_engine_ms": performance.get("settlement_ref_engine_ms"),
                "refresh_total_ms": performance.get("refresh_total_ms"),
                "export_ms": performance.get("export_ms"),
            },
        },
        "flags": _compact_flags(data.get("flags"))
        + [cf for cf in (_confidence_flag(ahmed_ref),) if cf],
    }


def _summary_diagnostic_signature(row: dict[str, Any]) -> str:
    stable = {
        key: value
        for key, value in row.items()
        if key not in {"logged_at", "snapshot_age_seconds", "updated_at_text"}
    }
    diagnostics = stable.get("diagnostics")
    if isinstance(diagnostics, dict):
        diagnostics = dict(diagnostics)
        diagnostics.pop("performance", None)
        stable["diagnostics"] = diagnostics
    return json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class DiagnosticExportPathError(RuntimeError):
    """The Windows Documents authority is unavailable or unsafe."""


class PackageRootAuthorityError(RuntimeError):
    """The package root does not match the executing overlay module."""


def _is_reparse_point(path: Path) -> bool:
    try:
        attrs = int(getattr(path.lstat(), "st_file_attributes", 0))
    except OSError:
        return False
    return bool(attrs & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)))


def _require_plain_directory(path: Path, *, label: str) -> Path:
    if not path.is_absolute():
        raise ValueError(f"{label} 不是绝对路径")
    try:
        info = path.lstat()
    except OSError as exc:
        raise ValueError(f"{label} 不可用") from exc
    if _is_reparse_point(path) or not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"{label} 不是普通目录")
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{label} 不可解析") from exc


def _require_plain_file(path: Path, *, label: str) -> Path:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ValueError(f"{label} 不可用") from exc
    if (
        _is_reparse_point(path)
        or not stat.S_ISREG(info.st_mode)
        or int(getattr(info, "st_nlink", 1)) != 1
    ):
        raise ValueError(f"{label} 不是单链接普通文件")
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{label} 不可解析") from exc


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((str(path), str(root))) == os.path.commonpath(
            (str(root), str(root))
        )
    except (OSError, ValueError):
        return False


def _require_plain_descendant(
    root: Path,
    path: Path,
    *,
    label: str,
    file: bool = False,
) -> Path:
    resolved_root = _require_plain_directory(root, label="包根目录")
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} 不在包根目录内") from exc
    current = root
    for part in relative.parts[:-1] if file else relative.parts:
        current = current / part
        _require_plain_directory(current, label=label)
    resolved = (
        _require_plain_file(path, label=label)
        if file
        else _require_plain_directory(path, label=label)
    )
    if not _path_is_within(resolved, resolved_root):
        raise ValueError(f"{label} 解析到包外")
    return resolved


def _windows_known_folder(folder_id_value: uuid.UUID, *, label: str) -> Path:
    if os.name != "nt":
        raise DiagnosticExportPathError(f"{label}仅支持 Windows")
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = (
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            )

        folder_id = GUID.from_buffer_copy(folder_id_value.bytes_le)
        raw_path = ctypes.c_void_p()
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        getter = shell32.SHGetKnownFolderPath
        getter.argtypes = (
            ctypes.POINTER(GUID),
            wintypes.DWORD,
            wintypes.HANDLE,
            ctypes.POINTER(ctypes.c_void_p),
        )
        getter.restype = ctypes.c_long
        result = int(getter(ctypes.byref(folder_id), 0, None, ctypes.byref(raw_path)))
        if result != 0 or not raw_path.value:
            raise OSError(result, f"SHGetKnownFolderPath({label}) failed")
        try:
            value = ctypes.wstring_at(raw_path.value)
        finally:
            ole32.CoTaskMemFree.argtypes = (ctypes.c_void_p,)
            ole32.CoTaskMemFree(raw_path)
    except Exception as exc:  # noqa: BLE001 - translate native failures to one safe UI state
        raise DiagnosticExportPathError(f"无法读取{label}") from exc
    if not value or "\x00" in value:
        raise DiagnosticExportPathError(f"{label}无效")
    return Path(value)


def _windows_documents_known_folder() -> Path:
    """Read FOLDERID_Documents directly; never infer it from env/home/cwd."""

    return _windows_known_folder(DOCUMENTS_KNOWN_FOLDER_ID, label="系统文档目录")


def _windows_local_appdata_known_folder() -> Path:
    """Read FOLDERID_LocalAppData directly; never infer it from env/home/cwd."""

    return _windows_known_folder(LOCAL_APPDATA_KNOWN_FOLDER_ID, label="本地应用数据目录")


def _validated_external_live_root(
    snapshot_path: Path,
    *,
    _live_root_for_test: Path | None = None,
) -> Path:
    """Bind diagnostic inputs to the exact external LocalAppData live terminal."""

    try:
        snapshot_parent = _require_plain_directory(
            Path(snapshot_path).parent, label="快照父目录"
        )
        if _live_root_for_test is None:
            current = _require_plain_directory(
                _windows_local_appdata_known_folder(),
                label="本地应用数据目录",
            )
            for name in ("BidKingLive", "logs", "live"):
                current = _require_plain_directory(
                    current / name,
                    label="活动日志目录",
                )
            if os.path.samefile(snapshot_parent, current):
                live_root = current
            else:
                package_root = _validated_package_root()
                package_live_root = _require_plain_descendant(
                    package_root,
                    package_root / "data" / "logs" / "live",
                    label="包内活动日志目录",
                )
                if not os.path.samefile(snapshot_parent, package_live_root):
                    raise ValueError("快照不属于允许的活动日志终点")
                # Bind the portable in-package terminal to the same strict profile
                # authority; all unknown/mixed profile text remains fail-closed.
                if _packaged_ref_engine_requires_native():
                    raise ValueError("包内活动日志目录 profile 无效")
                live_root = package_live_root
        else:
            live_root = _require_plain_directory(
                Path(_live_root_for_test),
                label="测试活动日志目录",
            )
        if not os.path.samefile(snapshot_parent, live_root):
            raise ValueError("快照不属于活动日志终点")
        return live_root
    except (OSError, ValueError) as exc:
        raise DiagnosticExportPathError("活动日志目录身份无效") from exc


def _diagnostic_export_dir(
    snapshot_path: Path,
    *,
    _documents_root_for_test: Path | None = None,
) -> Path:
    # snapshot_path intentionally remains an input for call compatibility only.  Runtime logs
    # stay beside the external snapshot; manual exports have a separate Known Folder authority.
    del snapshot_path
    documents = (
        Path(_documents_root_for_test)
        if _documents_root_for_test is not None
        else _windows_documents_known_folder()
    )
    try:
        documents = _require_plain_directory(documents, label="系统文档目录")
        current = documents
        for name in ("BidKingLive", "exports"):
            candidate = current / name
            candidate.mkdir(exist_ok=True)
            current = _require_plain_directory(candidate, label="诊断导出目录")
            if not _path_is_within(current, documents):
                raise ValueError("诊断导出目录越出系统文档目录")
        return current
    except (OSError, ValueError) as exc:
        raise DiagnosticExportPathError("系统文档目录不可写或不安全") from exc


def _diagnostic_export_tip(snapshot_path: Path) -> str:
    try:
        path = _diagnostic_export_dir(snapshot_path)
    except DiagnosticExportPathError:
        return EXPORT_DIAGNOSTIC_UNAVAILABLE_TIP
    return EXPORT_DIAGNOSTIC_TIP_TEMPLATE.format(path=str(path))


def _show_export_in_explorer(export_path: Path, *, _popen: Any = None) -> bool:
    if os.name != "nt":
        return False
    try:
        safe_path = _require_plain_file(export_path, label="诊断导出文件")
        popen = subprocess.Popen if _popen is None else _popen
        popen(
            ["explorer.exe", f'/select,"{safe_path}"'],
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)),
        )
    except (OSError, ValueError):
        return False
    return True


_PACKAGE_ROOT_VALUE_UNSET = object()


def _validated_package_root(
    *,
    package_root_value: object = _PACKAGE_ROOT_VALUE_UNSET,
    module_file: Path | None = None,
) -> Path:
    """Bind package resources to BIDKING_PACKAGE_ROOT and this executing module.

    A missing environment value uses the module identity (source/dev).  A present but empty,
    malformed, foreign or aliased-to-a-reparse value is rejected rather than falling back.
    ``module_file`` and ``package_root_value`` are narrow test seams; production callers pass none.
    """

    raw_module = Path(__file__) if module_file is None else Path(module_file)
    try:
        module_path = _require_plain_file(raw_module, label="执行模块")
        if (
            raw_module.parent.name.casefold() != "tools"
            or raw_module.parent.parent.name.casefold() != "hero_ref"
        ):
            raise ValueError("执行模块不在 hero_ref/tools")
        module_root_raw = raw_module.parent.parent.parent
        module_root = _require_plain_directory(module_root_raw, label="执行模块包根")
        _require_plain_descendant(
            module_root_raw,
            raw_module,
            label="执行模块",
            file=True,
        )
    except (OSError, ValueError) as exc:
        raise PackageRootAuthorityError("无法确认执行模块包根") from exc

    value = os.environ.get(PACKAGE_ROOT_ENV) if package_root_value is _PACKAGE_ROOT_VALUE_UNSET else package_root_value
    if value is None:
        return module_root
    if not isinstance(value, (str, os.PathLike)):
        raise PackageRootAuthorityError("BIDKING_PACKAGE_ROOT 类型无效")
    text = os.fspath(value)
    if not text.strip() or "\x00" in text:
        raise PackageRootAuthorityError("BIDKING_PACKAGE_ROOT 为空或无效")
    candidate_raw = Path(text)
    try:
        candidate = _require_plain_directory(candidate_raw, label="BIDKING_PACKAGE_ROOT")
        if not os.path.samefile(candidate, module_root):
            raise ValueError("BIDKING_PACKAGE_ROOT 与执行模块不一致")
        # candidate may be a valid extended-path or case alias.  samefile above binds its
        # identity; use the canonical module_root spelling for the lexical containment check.
        if not _path_is_within(module_path, module_root):
            raise ValueError("执行模块位于包根之外")
    except (OSError, ValueError) as exc:
        raise PackageRootAuthorityError("BIDKING_PACKAGE_ROOT 身份校验失败") from exc
    return module_root


def _candidate_package_roots(snapshot_path: Path) -> list[Path]:
    del snapshot_path
    try:
        return [_validated_package_root()]
    except PackageRootAuthorityError:
        return []


def _raw_tables_summary(snapshot_path: Path) -> dict[str, Any]:
    roots = _candidate_package_roots(snapshot_path)
    if not roots:
        return {
            "package_root": None,
            "path": None,
            "present": False,
            "required_present": False,
            "files": [],
            "missing_required": list(REQUIRED_RAW_TABLES),
            "authority_status": "package_root_unavailable",
        }
    root = roots[0]
    raw_dir = root / "data" / "raw" / "tables"
    try:
        resolved_raw_dir = _require_plain_descendant(root, raw_dir, label="raw tables")
    except ValueError:
        return {
            "package_root": str(root),
            "path": str(raw_dir),
            "present": False,
            "required_present": False,
            "files": [],
            "missing_required": list(REQUIRED_RAW_TABLES),
            "authority_status": "raw_tables_unavailable",
        }
    files: list[dict[str, Any]] = []
    missing: list[str] = []
    for name in REQUIRED_RAW_TABLES:
        path = raw_dir / name
        try:
            safe_file = _require_plain_descendant(root, path, label=f"raw table {name}", file=True)
        except ValueError:
            missing.append(name)
            continue
        files.append(_diagnostic_file_summary(safe_file))
    return {
        "package_root": str(root),
        "path": str(resolved_raw_dir),
        "present": True,
        "required_present": not missing,
        "files": files,
        "missing_required": missing,
        "authority_status": "ok" if not missing else "required_files_missing",
    }


def _safe_export_name(path: Path, *, base_dir: Path) -> str:
    try:
        rel = path.resolve().relative_to(base_dir.resolve())
        return rel.as_posix()
    except (OSError, ValueError):
        return path.name


def _validate_live_input_directory(live_root: Path, path: Path) -> Path:
    if not path.is_absolute():
        raise DiagnosticExportPathError("诊断输入目录不是绝对路径")
    try:
        relative = path.relative_to(live_root)
    except ValueError as exc:
        raise DiagnosticExportPathError("诊断输入目录不属于活动日志目录") from exc
    if any(":" in part for part in relative.parts):
        raise DiagnosticExportPathError("诊断输入目录名称无效")
    current = live_root
    try:
        current = _require_plain_directory(current, label="活动日志目录")
        for part in relative.parts:
            current = _require_plain_directory(current / part, label="诊断输入目录")
        if not _path_is_within(current, live_root):
            raise ValueError("resolved outside live root")
        return current
    except (OSError, ValueError) as exc:
        raise DiagnosticExportPathError("诊断输入目录身份无效") from exc


def _plain_live_json_files(live_root: Path, root: Path) -> list[tuple[Path, float]]:
    """Enumerate JSON leaves without following reparse directories."""

    pending = [_validate_live_input_directory(live_root, root)]
    rows: list[tuple[Path, float]] = []
    while pending:
        current = pending.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as exc:
            raise DiagnosticExportPathError("诊断输入目录遍历失败") from exc
        for entry in entries:
            path = Path(entry.path)
            try:
                info = path.lstat()
            except OSError as exc:
                raise DiagnosticExportPathError("诊断输入目录成员状态不可读") from exc
            if _is_reparse_point(path):
                raise DiagnosticExportPathError("诊断输入目录包含重解析成员")
            if stat.S_ISDIR(info.st_mode):
                pending.append(_validate_live_input_directory(live_root, path))
            elif path.suffix.casefold() == ".json":
                rows.append((path, _validate_live_input_path(live_root, path)[1].st_mtime))
    return rows


def _verified_diagnostic_session_id(
    snapshot: object,
    current_summary: object,
) -> str | None:
    """Bind receipt export to one exact snapshot and rendered summary session."""

    if type(snapshot) is not dict or type(current_summary) is not dict:  # noqa: E721
        return None
    root_session = dict.get(snapshot, "session_id")
    ui_contract = dict.get(snapshot, "ui_contract")
    if type(ui_contract) is not dict:  # noqa: E721
        return None
    context = dict.get(ui_contract, "context")
    if type(context) is not dict:  # noqa: E721
        return None
    context_session = dict.get(context, "session_id")
    summary_session = _strict_quote_summary_session_id(current_summary)
    if (
        type(root_session) is not str  # noqa: E721
        or type(context_session) is not str  # noqa: E721
        or root_session != context_session
        or root_session != summary_session
    ):
        return None
    return summary_session


def _candidate_diagnostic_paths(
    snapshot: dict[str, Any],
    snapshot_path: Path,
    *,
    live_root: Path,
    expected_receipt_session_id: object = None,
    diagnostic_profile: str = DEFAULT_DIAGNOSTIC_PROFILE,
    raw_forensics: bool = False,
) -> list[Path]:
    diagnostic_profile = _normalize_diagnostic_profile(diagnostic_profile)
    base_dir = snapshot_path.parent
    candidates: list[Path] = [
        snapshot_path,
        base_dir / "capture_source_status.json",
        base_dir / "wss_helper_status.json",  # 固定字段 sidecar：helper 退出码及 redirector/WinDivert 分类
        base_dir / NETWORK_CONNECTION_STATUS_FILENAME,
        base_dir / UI_HEALTH_LOG,
        base_dir / SAFE_FRAME_TELEMETRY_LOG,
        base_dir / UI_RUNTIME_STATUS,
        base_dir / COMPUTE_STATUS_FILENAME,
        base_dir / "monitor.stdout.log",
        base_dir / "monitor.stderr.log",
        base_dir / "overlay.stderr.log",  # overlay 崩溃/闪退 traceback(此前漏带→11/11诊断包缺·闪退无从归因)
        base_dir / "startup_status.log",  # WMI/CIM 不可用时的 PID fallback 与 stale-state 回收证据
        base_dir / "startup_session.json",  # 本次启动 ID/时间，用于排除历史 monitor 日志误归因
        base_dir / "monitor_log_session.json",  # stdout/stderr 与 startup launch_id 的归因 sidecar
        base_dir / "restart.log",  # 重启器动作/失败(A2 群友"重启=关了没重开"必看; 此前漏带导致无从诊断)
        # 自动上传只把 HTTP/config 结果写进这两份状态文件，不进 monitor stdout。诊断包漏带时，
        # 401 bad auth、InvalidTag 与根本未派发三种情况无法区分；文件不含上传/主密钥，可安全随包。
        base_dir / "upload_status.txt",
        base_dir / "upload_status.jsonl",
    ]
    # Always include only the bounded current-session fact bundle.  It is
    # source-side, pseudonymous and independent of the engineering UI timeline.
    candidates.extend(
        current_receipt_bundle_paths(
            base_dir,
            expected_session_id=expected_receipt_session_id,
        )
    )
    if diagnostic_profile == "engineering":
        candidates.extend(
            [
                base_dir / SUMMARY_DIAGNOSTIC_LOG,
                base_dir / "model_eval.jsonl",
                base_dir / "monitor_errors.jsonl",
            ]
        )
    if raw_forensics:
        candidates.extend(
            [
                base_dir / "local_player_cache.json",
                base_dir / "monitor.lock",
                base_dir / "fatbeans_webhook_live.json",
                base_dir / "raw" / "fatbeans_webhook_live.jsonl",
            ]
        )
        archive_dir = base_dir / "raw" / "archive"
        try:
            archive_dir.lstat()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise DiagnosticExportPathError("诊断归档目录状态不可读") from exc
        else:
            safe_archive_dir = _validate_live_input_directory(live_root, archive_dir)
            try:
                reset_rows = _plain_live_json_files(live_root, safe_archive_dir)
                reset_rows.sort(key=lambda row: row[1], reverse=True)
                resets = [row[0] for row in reset_rows]
                now = time.time()
                recent = [p for p, modified in reset_rows if (now - modified) <= 30 * 60.0]
                candidates.extend((recent or resets[:2])[:12])
            except OSError as exc:
                raise DiagnosticExportPathError("诊断归档目录遍历失败") from exc
        round_dir = base_dir / "round_snapshots"
        try:
            round_dir.lstat()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise DiagnosticExportPathError("逐轮快照目录状态不可读") from exc
        else:
            safe_round_dir = _validate_live_input_directory(live_root, round_dir)
            candidates.extend(sorted(safe_round_dir.glob("*.json")))
        for key in ("raw_capture", "raw_capture_jsonl"):
            raw_value = snapshot.get(key)
            if not raw_value:
                continue
            path = Path(str(raw_value))
            if not path.is_absolute():
                path = base_dir / path
            candidates.append(path)
    seen: set[Path] = set()
    out: list[Path] = []
    for path in candidates:
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise DiagnosticExportPathError("诊断输入状态不可读") from exc
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in seen:
            continue
        if not _path_is_within(resolved, live_root):
            raise DiagnosticExportPathError("诊断输入越出活动日志目录")
        seen.add(resolved)
        out.append(path)
    return out


def _stable_input_identity(info: os.stat_result) -> tuple[int, int, int]:
    return (int(info.st_dev), int(info.st_ino), int(info.st_size))


def _stable_input_state(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        *_stable_input_identity(info),
        int(getattr(info, "st_nlink", 1)),
        int(getattr(info, "st_mtime_ns", int(info.st_mtime * 1_000_000_000))),
        int(getattr(info, "st_ctime_ns", int(info.st_ctime * 1_000_000_000))),
    )


def _open_stable_diagnostic_input(path: Path) -> int:
    flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0)) | int(getattr(os, "O_NOINHERIT", 0))
    if os.name != "nt":
        return os.open(path, flags)

    import ctypes
    import msvcrt
    from ctypes import wintypes

    generic_read = 0x80000000
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    file_share_delete = 0x00000004
    open_existing = 3
    file_flag_open_reparse_point = 0x00200000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path),
        generic_read,
        file_share_read | file_share_write | file_share_delete,
        None,
        open_existing,
        file_flag_open_reparse_point,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if int(handle) == int(invalid_handle):
        error = ctypes.get_last_error()
        raise OSError(error, "CreateFileW failed")
    try:
        return msvcrt.open_osfhandle(int(handle), flags)
    except Exception:
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle(handle)
        raise


def _validate_live_input_path(live_root: Path, path: Path) -> tuple[Path, os.stat_result]:
    if not path.is_absolute():
        raise DiagnosticExportPathError("诊断输入不是绝对路径")
    try:
        relative = path.relative_to(live_root)
    except ValueError as exc:
        raise DiagnosticExportPathError("诊断输入不属于活动日志目录") from exc
    if not relative.parts or any(":" in part for part in relative.parts):
        raise DiagnosticExportPathError("诊断输入名称无效")
    current = live_root
    try:
        _require_plain_directory(live_root, label="活动日志目录")
        for part in relative.parts[:-1]:
            current = _require_plain_directory(current / part, label="诊断输入父目录")
        info = path.lstat()
        if (
            _is_reparse_point(path)
            or not stat.S_ISREG(info.st_mode)
            or int(getattr(info, "st_nlink", 1)) != 1
        ):
            raise ValueError("not a single-link ordinary file")
        resolved = path.resolve(strict=True)
        if not _path_is_within(resolved, live_root):
            raise ValueError("resolved outside live root")
        return resolved, info
    except (OSError, ValueError) as exc:
        raise DiagnosticExportPathError("诊断输入文件身份无效") from exc


def _read_stable_live_input(
    live_root: Path,
    path: Path,
    *,
    _after_first_read_for_test: Callable[[Path], None] | None = None,
) -> tuple[bytes, tuple[int, int]]:
    """Read one live input through a held Windows handle and reject identity/content drift."""

    resolved_path, before = _validate_live_input_path(live_root, path)
    fd = -1
    try:
        fd = _open_stable_diagnostic_input(path)
        with os.fdopen(fd, "rb", closefd=True) as stream:
            fd = -1
            opened = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or int(getattr(opened, "st_nlink", 1)) != 1
                or _stable_input_identity(opened) != _stable_input_identity(before)
                or not os.path.samefile(path, stream.fileno())
            ):
                raise DiagnosticExportPathError("诊断输入打开后身份变化")
            first = stream.read()
            if _after_first_read_for_test is not None:
                _after_first_read_for_test(path)
            stream.seek(0)
            second = stream.read()
            after_handle = os.fstat(stream.fileno())
            if first != second or _stable_input_state(after_handle) != _stable_input_state(opened):
                raise DiagnosticExportPathError("诊断输入读取期间发生变化")
            after_path_resolved, after_path = _validate_live_input_path(live_root, path)
            if (
                after_path_resolved != resolved_path
                or _stable_input_state(after_path) != _stable_input_state(before)
                or _stable_input_identity(after_path) != _stable_input_identity(after_handle)
                or int(getattr(after_path, "st_nlink", 1))
                != int(getattr(after_handle, "st_nlink", 1))
                or not os.path.samefile(path, stream.fileno())
            ):
                raise DiagnosticExportPathError("诊断输入读取后身份变化")
            return first, (int(opened.st_dev), int(opened.st_ino))
    except DiagnosticExportPathError:
        raise
    except Exception as exc:
        raise DiagnosticExportPathError("诊断输入读取失败或不稳定") from exc
    finally:
        if fd >= 0:
            os.close(fd)


def _diagnostic_file_summary(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
    except OSError:
        return {"path": str(path), "exists": False}
    return {
        "path": str(path),
        "exists": True,
        "size_bytes": stat.st_size,
        "modified_at": stat.st_mtime,
    }


def _scoped_health_log(
    path: Path,
    *,
    now_ts: float,
    window_seconds: float = DIAGNOSTIC_HEALTH_WINDOW_SECONDS,
) -> tuple[str | None, dict[str, Any]]:
    """Read hero_ref_ui_health.jsonl and return only the rows within ``window_seconds``
    of ``now_ts`` (the export moment), plus stats describing what was kept/dropped.

    Why: the health log is append-only and cumulative across every session, so its tail
    is often days-old anomalies. Bundling the whole file makes a reader mistake a stale
    stall for the current game (see DIAGNOSTIC_HEALTH_WINDOW_SECONDS). Scoping to a recent
    window keeps the current sitting and provably excludes multi-day-old entries.

    Returns (scoped_text, stats). scoped_text is None on read failure -> caller bundles the
    raw file unchanged (never break the export over a filtering nicety). A line whose
    logged_at can't be parsed is KEPT (don't silently drop unknown signal).
    """
    stats: dict[str, Any] = {"filtered": False}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None, stats
    return _scoped_health_text(raw, now_ts=now_ts, window_seconds=window_seconds)


def _scoped_health_text(
    raw: str,
    *,
    now_ts: float,
    window_seconds: float = DIAGNOSTIC_HEALTH_WINDOW_SECONDS,
) -> tuple[str, dict[str, Any]]:
    stats: dict[str, Any] = {"filtered": False}
    cutoff = now_ts - window_seconds
    kept: list[str] = []
    total = 0
    first_ts: float | None = None
    last_ts: float | None = None
    for line in raw.splitlines():
        if not line.strip():
            continue
        total += 1
        ts: float | None = None
        try:
            row = json.loads(line)
            if isinstance(row, dict) and isinstance(row.get("logged_at"), (int, float)):
                ts = float(row["logged_at"])
        except (ValueError, TypeError):
            ts = None
        if ts is not None:
            first_ts = ts if first_ts is None else min(first_ts, ts)
            last_ts = ts if last_ts is None else max(last_ts, ts)
        if ts is None or ts >= cutoff:
            kept.append(line)
    stats.update(
        {
            "filtered": True,
            "window_seconds": window_seconds,
            "full_lines": total,
            "kept_lines": len(kept),
            "dropped_stale_lines": total - len(kept),
            "full_span": [first_ts, last_ts],
            "window_start": cutoff,
        }
    )
    text = "\n".join(kept)
    if kept:
        text += "\n"
    return text, stats


def _capture_health_summary_from_inputs(inputs: Mapping[str, bytes]) -> dict[str, Any]:
    """Build the manifest health projection only from already pinned live input bytes."""

    summary: dict[str, Any] = {}
    status_bytes = inputs.get("capture_source_status.json")
    if status_bytes is not None:
        try:
            payload = json.loads(status_bytes.decode("utf-8-sig"))
            if isinstance(payload, dict):
                summary["capture_source_status"] = {
                    "ts": payload.get("ts"),
                    "source": payload.get("source"),
                    "active_session_id": payload.get("active_session_id"),
                    "accepted_frames": payload.get("accepted_frames"),
                    "ignored_frames": payload.get("ignored_frames"),
                    "dropped_bytes": payload.get("dropped_bytes"),
                    "dropped_bytes_delta": payload.get("dropped_bytes_delta"),
                    "kernel_drop": payload.get("kernel_drop"),
                    "kernel_recv": payload.get("kernel_recv"),
                    "queue_full_drops": payload.get("queue_full_drops"),
                    "error_code": payload.get("error_code"),
                    "error_message": payload.get("error_message"),
                    "error_hint": payload.get("error_hint"),
                    "capture_devices": payload.get("capture_devices"),
                    "ignored_reasons": payload.get("ignored_reasons"),
                }
        except Exception:  # noqa: BLE001 - summary projection never weakens input validation
            summary["capture_source_status_error"] = "read/parse failed"

    log_bytes = inputs.get("monitor.stdout.log")
    if log_bytes is not None:
        try:
            tail = log_bytes[-32768:].decode("utf-8", errors="replace").splitlines()
            settle_lines: list[str] = []
            last_status_line: str | None = None
            for line in tail:
                if "[settle]" in line:
                    settle_lines.append(line)
                if "[npcap]" in line or "[status]" in line:
                    last_status_line = line
            summary["recent_settlement_frames"] = settle_lines[-12:]
            summary["last_status_line"] = last_status_line
        except Exception:  # noqa: BLE001 - bytes are pinned; only projection may fail
            summary["log_read_error"] = "monitor.stdout.log tail failed"
    return summary


@contextmanager
def _new_diagnostic_export_file(
    export_dir: Path,
    *,
    timestamp: str,
    safe_session: str,
) -> Any:
    """Create one new ordinary ZIP without overwriting an existing export."""

    export_dir = _require_plain_directory(export_dir, label="诊断导出目录")
    flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
    flags |= int(getattr(os, "O_BINARY", 0)) | int(getattr(os, "O_NOINHERIT", 0))
    export_path: Path | None = None
    fd: int | None = None
    for suffix in range(1, 1000):
        tail = "" if suffix == 1 else f"-{suffix}"
        candidate = export_dir / f"HeroRefDiag-{timestamp}-{safe_session}{tail}.zip"
        try:
            fd = os.open(candidate, flags, 0o600)
            export_path = candidate
            break
        except FileExistsError:
            continue
    if fd is None or export_path is None:
        raise DiagnosticExportPathError("无法分配新的诊断文件名")
    stream = os.fdopen(fd, "w+b", closefd=True)
    try:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or int(getattr(opened, "st_nlink", 1)) != 1:
            raise DiagnosticExportPathError("新诊断文件身份无效")
        yield export_path, stream
        stream.flush()
        os.fsync(stream.fileno())
        final = export_path.lstat()
        if (
            _is_reparse_point(export_path)
            or not stat.S_ISREG(final.st_mode)
            or int(getattr(final, "st_nlink", 1)) != 1
        ):
            raise DiagnosticExportPathError("诊断文件完成后身份无效")
        if not os.path.samefile(export_path, stream.fileno()):
            raise DiagnosticExportPathError("诊断文件在写入期间被替换")
        _require_plain_directory(export_dir, label="诊断导出目录")
    except Exception:
        stream.close()
        try:
            export_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    else:
        stream.close()


def _write_diagnostic_export(
    *,
    snapshot: dict[str, Any],
    snapshot_path: Path,
    current_summary: dict[str, Any] | None = None,
    diagnostic_profile: str = DEFAULT_DIAGNOSTIC_PROFILE,
    show_taskbar: bool = False,
    raw_forensics: bool = False,
    _documents_root_for_test: Path | None = None,
    _live_root_for_test: Path | None = None,
    _after_input_read_for_test: Callable[[Path], None] | None = None,
) -> Path:
    export_started = time.perf_counter()
    diagnostic_profile = _normalize_diagnostic_profile(diagnostic_profile)
    live_root = _validated_external_live_root(
        snapshot_path,
        _live_root_for_test=_live_root_for_test,
    )
    export_dir = _diagnostic_export_dir(
        snapshot_path,
        _documents_root_for_test=_documents_root_for_test,
    )
    context = _mapping(current_summary.get("context") if current_summary else {})
    session_text = str(context.get("session_id") or snapshot.get("session_id") or "session")
    safe_session = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in session_text)[:48]
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    base_dir = live_root
    created_at = time.time()
    expected_receipt_session_id = _verified_diagnostic_session_id(
        snapshot,
        current_summary,
    )
    candidate_paths = _candidate_diagnostic_paths(
        snapshot,
        snapshot_path,
        live_root=live_root,
        expected_receipt_session_id=expected_receipt_session_id,
        diagnostic_profile=diagnostic_profile,
        raw_forensics=raw_forensics,
    )
    prepared_inputs: list[tuple[Path, str, bytes]] = []
    input_identities: set[tuple[int, int]] = set()
    for path in candidate_paths:
        source_bytes, identity = _read_stable_live_input(
            live_root,
            path,
            _after_first_read_for_test=_after_input_read_for_test,
        )
        if identity in input_identities:
            raise DiagnosticExportPathError("诊断输入身份重复")
        input_identities.add(identity)
        prepared_inputs.append(
            (path, _safe_export_name(path, base_dir=base_dir), source_bytes)
        )
    receipt_root = snapshot_path.parent / "fact_receipts"
    receipt_pinned: dict[str, bytes] = {}
    for path, _arcname, source_bytes in prepared_inputs:
        try:
            relative = path.relative_to(receipt_root).as_posix()
        except ValueError:
            continue
        receipt_pinned[relative] = source_bytes
    if receipt_pinned and not current_receipt_bundle_bytes_valid(
        expected_receipt_session_id,
        receipt_pinned,
    ):
        prepared_inputs = [
            row
            for row in prepared_inputs
            if not _path_is_within(row[0], receipt_root)
        ]
    pinned_inputs = {arcname: source_bytes for _path, arcname, source_bytes in prepared_inputs}
    current_reference = _mapping(current_summary.get("reference") if current_summary else {})
    current_evidence = _mapping(current_summary.get("evidence") if current_summary else {})
    current_diagnostics = _mapping(current_summary.get("diagnostics") if current_summary else {})
    snapshot_ready = bool(
        _text(context.get("session_id") or snapshot.get("session_id"), "").strip()
        and _text(context.get("hero") or snapshot.get("hero"), "").strip() not in {"", "?", "unknown"}
    )
    raw_tables = _raw_tables_summary(snapshot_path)
    package_is_public_safe = not bool(raw_tables.get("required_present"))
    manifest = {
        "created_at": created_at,
        "snapshot_path": str(snapshot_path),
        "session_id": session_text,
        "context": context,
        "version": {
            "schema_version": snapshot.get("schema_version"),
            "ui_contract_schema_version": _mapping(snapshot.get("ui_contract")).get("schema_version"),
            "source_file": snapshot.get("file"),
            "created_at": snapshot.get("created_at"),
        },
        "parameters": {
            "n_trials": snapshot.get("n_trials"),
            "roi_trials": snapshot.get("roi_trials"),
            "shadow_trials": snapshot.get("shadow_trials"),
            "formal_mode_requested": snapshot.get("formal_mode_requested"),
            "formal_mode": snapshot.get("formal_mode"),
            "formal_baseline_source": snapshot.get("formal_baseline_source"),
            "map_id": snapshot.get("map_id"),
            "model_map_id": snapshot.get("model_map_id"),
            "map_alias_mode": snapshot.get("map_alias_mode"),
            "hero": snapshot.get("hero"),
            "round": snapshot.get("round"),
            "phase": snapshot.get("phase"),
        },
        "startup": {
            "launch_mode": "taskbar" if show_taskbar else "floating",
            "show_taskbar": bool(show_taskbar),
            "diagnostic_profile": diagnostic_profile,
        },
        "package": {
            "is_public_safe": package_is_public_safe,
            "privacy_mode": "raw-forensics" if raw_forensics else "support-safe",
            "contains_unredacted_raw": bool(raw_forensics),
            "includes_raw_tables": bool(raw_tables.get("required_present")),
            "raw_tables_dir": raw_tables.get("path"),
            "raw_tables_root": raw_tables.get("package_root"),
            "raw_tables_missing_required": raw_tables.get("missing_required"),
        },
        "current_summary": {
            "status": current_summary.get("status") if current_summary else None,
            "reference_source": current_reference.get("source"),
            "readiness": current_reference.get("readiness"),
            "candidate_summary": current_evidence.get("candidate_summary"),
            "next_info_hint": current_evidence.get("next_info_hint"),
            "rare_signal_summary": _mapping(current_diagnostics.get("rare_signals")).get("summary"),
            "snapshot_ready": snapshot_ready,
            "snapshot_ready_warning": (
                None
                if snapshot_ready
                else "导出时 session/hero 尚未就绪；请等标题出现英雄名与轮次后再导出"
            ),
        },
        "source_files": {
            "file": snapshot.get("file"),
            "raw_capture": snapshot.get("raw_capture"),
            "raw_capture_jsonl": snapshot.get("raw_capture_jsonl"),
        },
        "log_summary": {
            "log_dir": str(base_dir.resolve()),
            "diagnostic_profile": diagnostic_profile,
            "continuous_ui_summary": diagnostic_profile == "engineering",
            "export_includes_raw": bool(raw_forensics),
            "export_includes_ui_summary": diagnostic_profile == "engineering",
            "latest_snapshot": _diagnostic_file_summary(snapshot_path),
            "ui_summary": _diagnostic_file_summary(base_dir / SUMMARY_DIAGNOSTIC_LOG),
            "ui_health": _diagnostic_file_summary(base_dir / UI_HEALTH_LOG),
            "ui_runtime_status": _diagnostic_file_summary(base_dir / UI_RUNTIME_STATUS),
            "compute_status": _diagnostic_file_summary(base_dir / COMPUTE_STATUS_FILENAME),
            "fact_receipts": _diagnostic_file_summary(
                base_dir / "fact_receipts" / "current_manifest.json"
            ),
            "monitor_stdout": _diagnostic_file_summary(base_dir / "monitor.stdout.log"),
            "monitor_stderr": _diagnostic_file_summary(base_dir / "monitor.stderr.log"),
            "monitor_log_session": _diagnostic_file_summary(
                base_dir / "monitor_log_session.json"
            ),
            "upload_status": _diagnostic_file_summary(base_dir / "upload_status.txt"),
            "upload_status_history": _diagnostic_file_summary(base_dir / "upload_status.jsonl"),
            "model_eval": _diagnostic_file_summary(base_dir / "model_eval.jsonl"),
            "monitor_errors": _diagnostic_file_summary(base_dir / "monitor_errors.jsonl"),
            "raw_tables": raw_tables,
        },
        "capture_health": _capture_health_summary_from_inputs(pinned_inputs),
        "included": [],
        "missing_optional": [],
        "note": (
            "RAW FORENSICS: contains unredacted source files; private handling required."
            if raw_forensics
            else "Support-safe diagnostic package; text and structured fields are redacted; no screenshots are included."
        ),
    }
    prepared_support_files: list[tuple[str, str, str]] = []
    player_aliases: dict[str, str] = {}
    if not raw_forensics:
        # Build one package-wide alias table before writing any member.  Player names can be
        # dynamic keys in one JSON file and plain text embedded in another log; one-pass,
        # per-file redaction misses that cross-member relationship.
        collect_player_aliases(snapshot, current_summary or {}, manifest, aliases=player_aliases)
        for path, arcname, source_bytes in prepared_inputs:
            source_text = source_bytes.decode("utf-8-sig", errors="replace")
            # UI 健康日志按近窗裁剪再打包；只处理已由 held handle 固定的内容。
            if path.name == UI_HEALTH_LOG:
                scoped, health_stats = _scoped_health_text(
                    source_text,
                    now_ts=manifest["created_at"],
                )
                manifest["log_summary"]["ui_health_scope"] = health_stats
                source_text = scoped
            prepared_support_files.append((arcname, path.suffix, source_text))
            collect_player_aliases_from_document(
                source_text,
                suffix=path.suffix,
                aliases=player_aliases,
            )

    with _new_diagnostic_export_file(
        export_dir,
        timestamp=timestamp,
        safe_session=safe_session,
    ) as (export_path, export_stream):
        with zipfile.ZipFile(export_stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            if raw_forensics:
                for _path, arcname, source_bytes in prepared_inputs:
                    archive.writestr(arcname, source_bytes)
                    manifest["included"].append(arcname)
            else:
                for arcname, suffix, source_text in prepared_support_files:
                    archive.writestr(
                        arcname,
                        redact_document(
                            source_text,
                            suffix=suffix,
                            player_aliases=player_aliases,
                        ),
                    )
                    manifest["included"].append(arcname)
            manifest["log_summary"]["included_count"] = len(manifest["included"])
            if current_summary:
                archive.writestr(
                    "hero_ref_current_summary.json",
                    json.dumps(
                        redact_payload(current_summary, player_aliases=player_aliases),
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
                manifest["included"].append("hero_ref_current_summary.json")
            manifest["performance"] = {
                "export_ms": round((time.perf_counter() - export_started) * 1000.0, 2),
            }
            archive.writestr(
                "BUILD_EXPORT_MANIFEST.json",
                json.dumps(
                    redact_payload(manifest, player_aliases=player_aliases),
                    ensure_ascii=False,
                    indent=2,
                ),
            )
    # 附加: 逐轮估计 vs 最终真值对照报告(实战复盘, <export>.replay.md)。引擎 replay 较重(每轮
    # 重算),放**后台 daemon 线程**生成，导出/UI 立即返回不卡顿。纯附加、读取式，失败不影响 zip。
    def _gen_replay(_zip: Path = export_path) -> None:
        try:
            import replay_vs_truth as _mod

            _zip.with_suffix(".replay.md").write_text(_mod.build_report(_zip), encoding="utf-8")
        except Exception:
            pass

    if raw_forensics:
        threading.Thread(target=_gen_replay, daemon=True, name="replay-vs-truth").start()
    return export_path


def _status_int(payload: dict[str, Any], key: str) -> int:
    try:
        return int(payload.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _capture_status_signature(status: dict[str, Any]) -> tuple[Any, ...]:
    if not status:
        return ()
    ignored = status.get("ignored_reasons")
    ignored_items: tuple[tuple[str, int], ...] = ()
    if isinstance(ignored, dict):
        ignored_items = tuple(
            sorted((str(key), _to_int(value, 0)) for key, value in ignored.items())
        )
    capture_devices = status.get("capture_devices")
    device_items: tuple[tuple[Any, ...], ...] = ()
    if isinstance(capture_devices, list):
        device_items = tuple(
            sorted(
                (
                    _text(device.get("description"), ""),
                    _text(device.get("state"), ""),
                    _text(device.get("capture_mode"), ""),
                    _text(device.get("error_code"), ""),
                    bool(device.get("non_promisc_fallback_attempted")),
                )
                for device in capture_devices
                if isinstance(device, dict)
            )
        )
    network_status = status.get("_network_connection_status")
    network = (
        network_status
        if isinstance(network_status, dict)
        else {}
    )
    helper_status = status.get("_wss_helper_status")
    helper = helper_status if isinstance(helper_status, dict) else {}
    compute_status = status.get("_monitor_compute_status")
    compute = compute_status if isinstance(compute_status, dict) else {}
    return (
        _text(status.get("source"), ""),
        _text(status.get("process_name"), ""),
        _text(status.get("filter"), ""),
        _status_int(status, "active_flows"),
        _status_int(status, "sniffed_packets"),
        _status_int(status, "raw_packets"),
        _status_int(status, "accepted_frames"),
        _status_int(status, "ignored_frames"),
        _status_int(status, "dropped_bytes"),
        _text(status.get("active_session_id"), ""),
        _text(status.get("error_code"), ""),
        _text(status.get("error_message"), ""),
        _text(status.get("error_hint"), ""),
        _text(network.get("game_connection_state"), ""),
        _text(network.get("wss_intercept_state"), ""),
        _text(network.get("capture_decode_state"), ""),
        _text(helper.get("phase"), ""),
        _text(helper.get("error_code"), ""),
        _text(helper.get("windivert_preload_state"), ""),
        _text(helper.get("windivert_preload_phase"), ""),
        _text(helper.get("monitor_elevation_state"), ""),
        _text(compute.get("event"), ""),
        _text(compute.get("terminal_reason"), ""),
        _status_int(compute, "trace_seq"),
        ignored_items,
        device_items,
    )


def _top_ignored_reason(status: dict[str, Any]) -> str:
    ignored = status.get("ignored_reasons")
    if not isinstance(ignored, dict) or not ignored:
        return "-"
    reason, count = max(
        ignored.items(),
        key=lambda item: _to_int(item[1], 0),
    )
    return f"{reason} x{_to_int(count, 0)}"


def _capture_wait_diagnostics(status: dict[str, Any]) -> dict[str, Any]:
    if not status:
        return {
            "subtitle": "等待 monitor 状态",
            "flags": [{"label": "无实时数据", "level": "watch", "detail": ""}],
            "action": "等待启动",
            "state": "no_capture_status",
            "recent": "-",
            "source": "-",
            "detail": "未看到 capture_source_status.json",
            "note": "确认已用管理员权限启动 Hero Ref",
        }
    active = _status_int(status, "active_flows")
    raw = _status_int(status, "raw_packets")
    accepted = _status_int(status, "accepted_frames")
    sniffed = _status_int(status, "sniffed_packets")
    session = _text(status.get("active_session_id"), "")
    source = _text(status.get("source"), "windivert")
    top_reason = _top_ignored_reason(status)
    detail = f"flow {active} / raw {raw} / accepted {accepted}"
    error_code = _text(status.get("error_code"), "")
    error_message = _text(status.get("error_message"), "")
    error_hint = _text(status.get("error_hint"), "")
    network_status = status.get("_network_connection_status")
    network = validate_network_connection_status(
        network_status,
        launch_id=_text(status.get("_current_launch_id"), ""),
    )
    game_connection_state = _text(network.get("game_connection_state"), "")
    wss_intercept_state = _text(network.get("wss_intercept_state"), "")
    capture_decode_state = _text(network.get("capture_decode_state"), "")
    if wss_intercept_state == "attached" and error_code.startswith("wss_"):
        # The three-layer model has current per-game WSS health. Do not let an
        # older capture/helper error code override that recovered state below.
        error_code = ""
    network_evidence = (
        network.get("evidence")
        if isinstance(network.get("evidence"), dict)
        else {}
    )
    helper_status = status.get("_wss_helper_status")
    helper = helper_status if isinstance(helper_status, dict) else {}
    compute_status = status.get("_monitor_compute_status")
    compute = compute_status if isinstance(compute_status, dict) else {}
    compute_terminal_reason = _text(compute.get("terminal_reason"), "")
    inference_failed = bool(
        compute.get("event") == "error"
        and compute_terminal_reason
        and accepted > 0
        and capture_decode_state == "recognized"
        and wss_intercept_state == "attached"
    )
    current_launch_id = _text(status.get("_current_launch_id"), "")
    helper_launch_id = _text(helper.get("launch_id"), "")
    helper_is_current = bool(
        current_launch_id
        and helper_launch_id
        and current_launch_id == helper_launch_id
    )
    preload_access_denied = bool(
        helper_is_current
        and _text(helper.get("windivert_preload_state"), "")
        == "failed_access_denied"
    )
    preload_phase = _text(helper.get("windivert_preload_phase"), "") or "unknown"
    preload_same_path_mixed_generation = bool(
        helper_is_current
        and _text(helper.get("windivert_preload_state"), "")
        == "failed_same_path_mixed_generation"
        and preload_phase == "verify_product"
        and wss_intercept_state != "attached"
        and accepted <= 0
        and not session
    )
    preload_service_conflict = bool(
        helper_is_current
        and _text(helper.get("windivert_preload_state"), "")
        == "failed_service_conflict"
        and preload_phase == "verify_product"
        and wss_intercept_state != "attached"
        and accepted <= 0
        and not session
    )
    preload_repaired_restart_required = bool(
        helper_is_current
        and _text(helper.get("windivert_preload_state"), "")
        == "repaired_restart_required"
        and wss_intercept_state != "attached"
        and accepted <= 0
        and not session
    )
    preload_elevation = _text(helper.get("monitor_elevation_state"), "")
    if preload_elevation == "elevated":
        preload_access_denied_note = (
            f"已提升的本地兼容预加载在 {preload_phase} 阶段被 SCM 或系统策略拒绝；"
            "不锁定具体安全软件，请打开兼容步骤后按顺序复验"
        )
    elif preload_elevation == "not_elevated":
        preload_access_denied_note = (
            f"本地监控未取得管理员权限，在 {preload_phase} 阶段被拒绝；"
            "请重新以管理员身份启动并打开兼容步骤"
        )
    else:
        preload_access_denied_note = (
            f"本地兼容预加载在 {preload_phase} 阶段被拒绝，提升状态待确认；"
            "不锁定具体安全软件，请打开兼容步骤后按顺序复验"
        )
    if inference_failed:
        state = compute_terminal_reason
        action = "请导出诊断"
        top_reason = compute_terminal_reason
        if compute_terminal_reason == "drop_pool_incomplete":
            subtitle = "已读取对局，掉落资料暂不完整"
            note = "有效对局帧已收到，但同代掉落资料无法完整解析；请导出诊断给作者"
            label = "掉落资料异常"
        elif compute_terminal_reason == "inference_input_error":
            subtitle = "已读取对局，推理输入异常"
            note = "有效对局帧已收到，推理输入校验未通过；请导出诊断给作者"
            label = "推理输入异常"
        else:
            subtitle = "已读取对局，报价计算失败"
            note = "有效对局帧已收到，但报价计算未完成；请导出诊断给作者"
            label = "报价计算失败"
        flags = [
            {
                "label": label,
                "level": "risk",
                "detail": compute_terminal_reason,
            }
        ]
    elif preload_same_path_mixed_generation:
        subtitle = "新旧包可能在同一目录混装"
        action = "完整解压到新的最终目录"
        state = "wss_same_path_mixed_generation"
        note = (
            "产品服务指向当前包 driver 的同一绝对目录，但当前代身份无法完整证明。"
            "为避免误删，本次不会停止、删除或重配任何服务。请完全退出 Hero Ref 和游戏，"
            "把官方 ZIP 完整解压到一个新的最终目录；不要先删除旧目录，也不要在首次启动后"
            "移动或改名新目录。然后从新目录启动并按提示完成精确修复"
        )
        flags = [
            {
                "label": "同目录代际不明",
                "level": "risk",
                "detail": "verify_product",
            }
        ]
    elif preload_repaired_restart_required:
        subtitle = "旧版兼容服务清理已提交，需要重启"
        action = "重启电脑后重新启动"
        state = "wss_stale_product_service_repaired_restart_required"
        note = (
            "当前包只对已严格验证的 BidKing 旧版兼容服务提交了安全清理；"
            "这不代表旧驱动或句柄已卸载，请重启电脑，"
            "把当前包重新完整解压到全新纯英文目录，不要覆盖旧目录；"
            "先启动 Hero Ref、浮窗出现后再启动游戏。不要处理名为 "
            "WinDivert 的其他服务"
        )
        flags = [
            {
                "label": "兼容服务清理已提交",
                "level": "watch",
                "detail": "restart_required",
            }
        ]
    elif preload_service_conflict:
        subtitle = "检测到旧版兼容服务残留"
        action = "请联系作者修复"
        state = "wss_stale_product_service_conflict"
        note = (
            "当前包已安全拒绝复用身份不一致的旧版产品服务。请完全退出 Hero Ref "
            "和游戏后联系作者使用当前包的受控修复入口，完成后先启动 Hero Ref、"
            "再启动游戏；不要更换 token，也不要处理其他 WinDivert 服务"
        )
        flags = [
            {
                "label": "旧版服务残留",
                "level": "risk",
                "detail": "verify_product",
            }
        ]
    elif game_connection_state == "failed":
        subtitle = "游戏连接服务器失败"
        action = "切换网络后重试"
        state = "game_connection_failed"
        note = (
            "检测到本次游戏启动后的 WebSocket 连接错误；"
            "这是游戏连接阶段，不是报价或解析故障"
        )
        flags = [
            {
                "label": "游戏连接失败",
                "level": "risk",
                "detail": "本次启动 WebSocket 错误",
            }
        ]
        if wss_intercept_state == "failed":
            flags.append(
                {
                    "label": "本地读取也异常",
                    "level": "risk",
                    "detail": _text(network_evidence.get("helper_error_code"), ""),
                }
            )
    elif wss_intercept_state == "failed":
        helper_error_code = (
            _text(network_evidence.get("helper_error_code"), "") or error_code
        )
        runtime_compatibility_failed = (
            helper_error_code == "wss_runtime_compatibility_failed"
        )
        redirector_missing_stable = (
            helper_error_code == "wss_redirector_missing_stable"
        )
        subtitle = (
            "本地兼容组件被系统拒绝"
            if preload_access_denied
            else (
                "本地兼容组件启动失败"
                if runtime_compatibility_failed
                else (
                    "本地兼容组件未接管"
                    if redirector_missing_stable
                    else "本地读取组件启动失败"
                )
            )
        )
        action = (
            "查看兼容步骤"
            if preload_access_denied or redirector_missing_stable
            else "请导出诊断"
        )
        state = (
            "wss_preload_access_denied"
            if preload_access_denied
            else (
                "wss_runtime_compatibility_failed"
                if runtime_compatibility_failed
                else (
                    "wss_local_intercept_missing"
                    if redirector_missing_stable
                    else "wss_intercept_failed"
                )
            )
        )
        note = (
            preload_access_denied_note
            if preload_access_denied
            else (
                "当前固定运行时未通过本机安全策略；请保持系统安全功能开启。"
                "这不是游戏网络、报价或解析故障"
                if runtime_compatibility_failed
                else (
                    "helper 仍在运行，但 redirector 在启动宽限后持续未接管；"
                    "请保持系统安全设置不变，打开兼容步骤后按顺序复验"
                    if redirector_missing_stable
                    else (
                        "helper / redirector / WinDivert 未正常接管；"
                        "该状态与游戏服务器连接分开判断"
                    )
                )
            )
        )
        flags = [
            {
                "label": (
                    "预加载被拒绝"
                    if preload_access_denied
                    else (
                        "运行时兼容失败"
                        if runtime_compatibility_failed
                        else (
                            "本地未接管"
                            if redirector_missing_stable
                            else "本地接管失败"
                        )
                    )
                ),
                "level": "risk",
                "detail": (
                    preload_phase
                    if preload_access_denied
                    else helper_error_code
                ),
            }
        ]
    elif capture_decode_state == "unrecognized":
        subtitle = "已收到游戏数据，当前版本尚未识别"
        action = "等待版本适配"
        state = "wss_data_unrecognized"
        note = "本地读取已收到数据；accepted_frames=0，不属于网络连接失败"
        flags = [{"label": "数据未识别", "level": "watch", "detail": detail}]
    elif (
        source == "wss_local"
        and game_connection_state == "not_running"
        and wss_intercept_state in {"ready", "not_started", "unknown"}
    ):
        subtitle = "读取已就绪，等待启动游戏"
        action = "启动 BidKing"
        state = "game_not_running"
        note = "保持 Hero Ref 开启，再启动游戏；当前未判定为连接失败"
        flags = [{"label": "游戏未运行", "level": "neutral", "detail": detail}]
    elif (
        source == "wss_local"
        and game_connection_state in {"connecting", "unknown"}
        and active <= 0
        and raw <= 0
    ):
        subtitle = (
            "游戏正在连接服务器"
            if game_connection_state == "connecting"
            else "等待游戏连接状态"
        )
        action = "等待连接"
        state = (
            "game_connecting"
            if game_connection_state == "connecting"
            else "game_connection_unknown"
        )
        note = "尚无新鲜失败证据，也未收到本地 WSS 数据；继续等待或导出诊断"
        flags = [{"label": "连接状态待确认", "level": "neutral", "detail": detail}]
    elif error_code == "npcap_device_open_failed":
        subtitle = "网卡抓包初始化失败"
        action = "请导出诊断"
        state = error_code
        note = error_hint or "抓包网卡无法打开；请导出诊断后复验"
        flags = [
            {
                "label": "网卡初始化失败",
                "level": "risk",
                "detail": error_message or error_code,
            }
        ]
    elif error_code:
        subtitle = "monitor 底层抓包启动失败"
        action = "检查防火墙/安全软件"
        state = error_code
        note = error_hint or "重新解压 full 包，信任整个文件夹后用管理员入口启动"
        flags = [
            {
                "label": "抓包失败",
                "level": "risk",
                "detail": error_message or error_code,
            }
        ]
    elif source == "wss_local":
        # WSS local capture receives sanitized application frames, not NIC packets.
        # Before the first in-game state event, an open flow with zero frames is a
        # normal wait state. Reusing the Npcap/WinDivert "no NIC traffic" diagnosis
        # here falsely reports a failure even though the same session can calculate
        # normally as soon as the first auction frame arrives.
        if accepted > 0 or session:
            subtitle = "读取链路正常，等待新局状态"
            action = "等待状态更新"
            state = "wss_session_waiting_snapshot"
            note = "已收到过有效对局数据；进入新局后会自动刷新"
            flags = [
                {
                    "label": "读取正常",
                    "level": "neutral",
                    "detail": session or detail,
                }
            ]
        elif raw > 0 or sniffed > 0:
            subtitle = "已收到游戏数据，正在识别对局状态"
            action = "等待状态帧"
            state = "wss_data_waiting_frame"
            note = "本地读取正常；首个可识别状态事件到达后会自动刷新"
            flags = [{"label": "已收到数据", "level": "neutral", "detail": detail}]
        elif active > 0:
            subtitle = "已连接游戏，等待对局数据"
            action = "等待对局数据"
            state = "wss_flow_waiting_data"
            note = "当前连接已建立；进入对局后会在首个状态事件到达时自动刷新"
            flags = [{"label": "游戏已连接", "level": "neutral", "detail": detail}]
        else:
            subtitle = "读取已就绪，等待游戏连接"
            action = "等待游戏连接"
            state = "wss_waiting_connection"
            note = "保持 Hero Ref 开启，再启动或进入 BidKing 对局"
            flags = [{"label": "读取已就绪", "level": "neutral", "detail": detail}]
    elif active <= 0:
        subtitle = "monitor 已启动，等待 BidKing.exe 网络流"
        action = "等待游戏连接"
        state = "no_active_flow"
        note = "先进入对局；仍无连接时请导出诊断"
        flags = [{"label": "等待连接", "level": "neutral", "detail": detail}]
    elif sniffed <= 0:
        # active_flows>0 仅证明系统 TCP 表存在游戏连接，不能单凭 sniffed=0
        # 将原因锁定为加速器。网卡选择、驱动、虚拟网卡或网络软件改道都可能导致。
        subtitle = "检测到游戏连接，但未收到游戏网卡流量"
        action = "检查抓包网卡"
        state = "flow_no_sniff"
        note = "网卡选择、驱动、虚拟网卡或网络软件改道都可能导致；请导出诊断后再判断"
        flags = [{"label": "未收到网卡流量", "level": "watch", "detail": detail}]
    elif raw <= 0:
        subtitle = "已连接游戏流，等待对局包"
        action = "等待对局包"
        state = "no_raw_packets"
        note = "保持 UI 开启后进入新局或使用道具"
        flags = [{"label": "已连接", "level": "neutral", "detail": detail}]
    elif accepted <= 0:
        subtitle = "已抓到流量，但未解析到对局状态帧"
        action = "等待状态帧"
        state = "raw_no_game_frame"
        # raw>0 = 包【已经抓到】,只是没解析成对局帧(rev_not_game_frame)——采集没被挡,不是加速器拦截。
        # 真根因多为:①版本过旧,入站帧带 802.1Q VLAN 标签、旧版在内核 BPF/parse 前就丢了(0.2.6 起已修);
        # ②多网卡/虚拟网卡把同一条流重复喂进切帧器致长度前缀错位。别再让用户卸软件(帮倒忙,见 VLAN 根因)。
        note = "抓到了流量但没解析成对局帧(包已收到、未识别为对局帧)→ 多为①版本过旧(入站帧的 VLAN 标签旧版会丢, 0.2.6 起已修)请先【更新到最新版本】; ②多网卡/虚拟网卡重组错位。请先更新; 最新版仍持续再用 BroadSniff/IncludeLoopback 备用启动或跑诊断发作者"
        flags = [{"label": "抓包未成帧", "level": "watch", "detail": top_reason}]
    else:
        subtitle = "已识别会话，等待估价状态帧"
        action = "等待估价帧"
        state = "session_waiting_snapshot"
        note = "开局状态可能已错过；下一次状态/道具结果会刷新"
        flags = [{"label": "会话已识别", "level": "neutral", "detail": session or detail}]
    # A concise, state-specific "what to do next" so the 下一步 row does not just
    # echo the 动作 row (both used to fall back to `action` → identical text).
    next_hint = {
        "no_active_flow": "进入对局即可",
        "npcap_device_open_failed": "导出诊断后复验",
        "flow_no_sniff": "导出诊断或检查网卡",
        "no_raw_packets": "进入新局或用道具",
        "raw_no_game_frame": "等下一个状态帧",
        "session_waiting_snapshot": "等估价帧刷新",
        "wss_waiting_connection": "启动游戏或进入对局",
        "wss_flow_waiting_data": "进入对局即可",
        "wss_data_waiting_frame": "等待状态事件",
        "wss_session_waiting_snapshot": "进入新局即可",
        "game_connection_failed": "切换网络后重启游戏",
        "wss_intercept_failed": "导出诊断后复验",
        "wss_preload_access_denied": "按兼容顺序重启",
        "wss_same_path_mixed_generation": "完全退出后迁移新目录",
        "wss_stale_product_service_conflict": "完全退出后修复并重启",
        "wss_stale_product_service_repaired_restart_required": (
            "重启后先开 Hero Ref"
        ),
        "wss_local_intercept_missing": "按兼容顺序重启",
        "wss_data_unrecognized": "等待版本适配",
        "drop_pool_incomplete": "导出诊断给作者",
        "inference_input_error": "导出诊断给作者",
        "inference_error": "导出诊断给作者",
        "game_not_running": "启动游戏即可",
        "game_connecting": "等待进入大厅",
        "game_connection_unknown": "继续等待或导出诊断",
    }.get(state, "按提示排查抓包" if error_code else "等待刷新")
    return {
        "subtitle": subtitle,
        "flags": flags,
        "action": action,
        "state": state,
        "next_hint": next_hint,
        "recent": top_reason,
        "source": source,
        "detail": f"{detail} / sniffed {sniffed}",
        "note": note,
        "session": session,
    }


def _compact_capture_runtime_status(status: dict[str, Any]) -> dict[str, Any]:
    diagnostics = _capture_wait_diagnostics(status)
    network = validate_network_connection_status(
        status.get("_network_connection_status"),
        launch_id=_text(status.get("_current_launch_id"), ""),
    )
    return {
        "present": bool(status),
        "source": _text(status.get("source"), "") if status else "",
        "process_name": _text(status.get("process_name"), "") if status else "",
        "filter": _text(status.get("filter"), "") if status else "",
        "active_flows": _status_int(status, "active_flows"),
        "sniffed_packets": _status_int(status, "sniffed_packets"),
        "raw_packets": _status_int(status, "raw_packets"),
        "accepted_frames": _status_int(status, "accepted_frames"),
        "ignored_frames": _status_int(status, "ignored_frames"),
        "dropped_bytes": _status_int(status, "dropped_bytes"),
        "active_session_id": _text(status.get("active_session_id"), "") if status else "",
        "top_ignored_reason": _top_ignored_reason(status) if status else "-",
        "wait_state": diagnostics.get("state"),
        "wait_action": diagnostics.get("action"),
        "wait_note": diagnostics.get("note"),
        "error_code": _text(status.get("error_code"), "") if status else "",
        "error_message": _text(status.get("error_message"), "") if status else "",
        "error_hint": _text(status.get("error_hint"), "") if status else "",
        "game_connection_state": _text(
            network.get("game_connection_state"), ""
        ),
        "wss_intercept_state": _text(
            network.get("wss_intercept_state"), ""
        ),
        "capture_decode_state": _text(
            network.get("capture_decode_state"), ""
        ),
    }


def _to_int(value: Any, default: int) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


def _to_optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError, OverflowError):
        return None


def _to_manual_count(
    value: Any,
    label: str,
    *,
    maximum: int = _MANUAL_MAX_ITEM_COUNT,
) -> tuple[int | None, str]:
    if value in (None, ""):
        return None, ""
    text = str(value).replace(",", "").strip()
    if not text:
        return None, ""
    parsed = _to_optional_float(text)
    if parsed is None:
        return None, f"{label}需要整数"
    if not math.isfinite(parsed):
        return None, f"{label}需要有限数字"
    if not parsed.is_integer():
        return None, f"{label}需要整数；{text} 像均格，请填到均格/总格"
    parsed_int = int(parsed)
    if parsed_int < 0:
        return None, f"{label}不能为负数"
    if parsed_int > maximum:
        return None, f"{label or '输入值'}超出可计算范围"
    return parsed_int, ""


def _to_manual_cells(value: Any, label: str) -> tuple[int | None, str]:
    return _to_manual_count(value, label, maximum=_MANUAL_MAX_GRID_CELLS)


def _to_manual_nonnegative_float(value: Any, label: str) -> tuple[float | None, str]:
    if value in (None, ""):
        return None, ""
    text = str(value).replace(",", "").strip()
    if not text:
        return None, ""
    parsed = _to_optional_float(text)
    if parsed is None:
        return None, f"{label}需要数字"
    if not math.isfinite(parsed):
        return None, f"{label}需要有限数字"
    if parsed < 0:
        return None, f"{label}不能为负数"
    return parsed, ""


def _manual_inline_count(value: Any) -> int | None:
    parsed, error = _to_manual_count(value, "")
    return None if error else parsed


def _manual_inline_cells(value: Any) -> int | None:
    parsed, error = _to_manual_cells(value, "")
    return None if error else parsed


def _manual_inline_nonnegative_float(value: Any) -> float | None:
    parsed, error = _to_manual_nonnegative_float(value, "")
    return None if error else parsed


def _to_manual_value_sum(value: Any, label: str) -> tuple[int | None, str]:
    parsed, error = _to_manual_nonnegative_float(value, label)
    if error or parsed is None:
        return None, error
    if not parsed.is_integer():
        return None, f"{label}需要整数"
    return int(parsed), ""


def _manual_value_sum_matches_avg(avg: float, *, count: int, value_sum: float) -> bool:
    if count <= 0:
        return abs(float(value_sum)) <= 0.0001 and abs(float(avg)) <= 0.0001
    return abs(float(avg) * count - float(value_sum)) <= 0.5


def _normalize_manual_hero(value: Any) -> str:
    return normalize_hero_key(value)


def _supported_manual_hero_display(*values: Any) -> Any:
    for value in values:
        if is_supported_ref_hero(value):
            return value
    return None


def _normalize_manual_map_text(value: Any) -> str:
    text = str(value or "").replace("\u200b", "").strip().lower()
    if not text:
        return ""
    text = re.sub(r"[\s_\-:：#()\[\]（）【】]+", "", text)
    for prefix in ("地图", "mapid", "map"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text


def _processed_maps_candidates() -> tuple[Path, ...]:
    candidates = [
        ROOT / "data" / "processed" / "maps.json",
        Path.cwd() / "data" / "processed" / "maps.json",
    ]
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.append(Path(bundle_root) / "data" / "processed" / "maps.json")
    return tuple(candidates)


def _moat_json_or_plain(path: Path) -> Any:
    """Read a moat JSON that may be BKENC1-encrypted in the packaged build.
    加密包: 走内核 K 透明解密(data_crypt.load_json); 源码: 明文直读。data_crypt 不可用时回退明文。
    ⚠️ 直接 path.read_text() 读加密 moat 会抛 UnicodeDecodeError(0xNN) —— 见 maps.json 冻结事故。"""
    try:
        from bidking_lab.secure.data_crypt import load_json as _load_json  # noqa: PLC0415
        return _load_json(path)
    except ImportError:
        return json.loads(path.read_text(encoding="utf-8"))


# 排位图 56xx 的正确中文名。权威 = build_match_records._RANK_MAP_NAMES(保持同步)。
# maps.json 改版(06-18 YooAsset)后给 56xx 抄了【错配的沉船名】(如 5605→"殖民商船宝库"=沉船35号名),
# 故手填面板等显示层必须覆盖, 否则排位图显示成沉船名(群友反馈)。见 map-family-taxonomy / dashboard续5。
_RANK_MAP_NAME_OVERRIDES: dict[int, str] = {
    5601: "百物杂拍", 5602: "居家陈设", 5603: "秘药器械", 5604: "奢品风尚",
    5605: "军械私拍", 5606: "珍宝原石", 5607: "古物珍玩", 5608: "尖端数码",
    5609: "载具机械", 5610: "珍馐食材", 5611: "翰墨典藏",
}
QUALITY_SUMMARY_ROW_ORDER = (
    "红件",
    "红格",
    "金品",
    "紫品",
    "蓝品",
    "白绿",
)

# 2026-07-16 红箱活动图尚未进入当前 maps.json。手填/详情不能因此只显示数字；
# 名称顺序与 scripts/build_match_records._SUNKEN_BASE_NAMES 保持一致。
_RED_CRATE_SHIPWRECK_BASE_NAMES = (
    "未知残骸", "远洋客轮舱房", "军用舰艇保险库", "冷链货船隔离舱", "殖民商船宝库",
    "探险家座舰资料库", "皇家御用货舱", "生物实验室样本库", "私掠船军火舱", "现代货轮娱乐库",
)
_RED_CRATE_SHIPWRECK_MAP_NAMES = {
    base + 41 + offset: f"{name}(红箱活动)"
    for base in (2500, 4500)
    for offset, name in enumerate(_RED_CRATE_SHIPWRECK_BASE_NAMES)
}


@lru_cache(maxsize=1)
def _processed_map_name_rows() -> tuple[tuple[int, str], ...]:
    for path in _processed_maps_candidates():
        try:
            raw = _moat_json_or_plain(path)
        except Exception:  # noqa: BLE001  缺K/错K/篡改(InvalidTag,RuntimeError)/损坏都跳过, 不崩显示路径(见 maps.json 冻结事故)
            continue
        if not isinstance(raw, list):
            continue
        rows: list[tuple[int, str]] = []
        seen_ids: set[int] = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            map_id = _to_optional_int(item.get("map_id"))
            name = _text(item.get("name"), "").replace("\u200b", "").strip()
            if map_id is None:
                continue
            name = _RANK_MAP_NAME_OVERRIDES.get(map_id, name)  # 56xx \u6392\u4f4d\u540d\u8986\u76d6 maps.json \u9519\u914d\u7684\u6c89\u8239\u540d
            if not name:
                continue
            rows.append((map_id, name))
            seen_ids.add(map_id)
        # \u8865 maps.json \u7f3a\u5931\u7684\u6392\u4f4d\u56fe(\u5982 5611), \u4e0e build_match_records._load_map_meta \u4e00\u81f4
        for mid, nm in _RANK_MAP_NAME_OVERRIDES.items():
            if mid not in seen_ids:
                rows.append((mid, nm))
        for mid, nm in _RED_CRATE_SHIPWRECK_MAP_NAMES.items():
            if mid not in seen_ids:
                rows.append((mid, nm))
        if rows:
            return tuple(rows)
    return ()


def _add_manual_map_alias(
    lookup: dict[str, tuple[int, str]],
    alias: Any,
    map_id: int,
    name: str,
) -> None:
    key = _normalize_manual_map_text(alias)
    if key:
        lookup.setdefault(key, (map_id, name))


def _manual_map_lookup() -> dict[str, tuple[int, str]]:
    lookup: dict[str, tuple[int, str]] = {}
    for map_id, name in _processed_map_name_rows():
        for alias in {str(map_id), name, f"{map_id}{name}", f"{name}{map_id}"}:
            _add_manual_map_alias(lookup, alias, map_id, name)

    data: dict[str, Any] = {}
    if callable(load_reference_static_data):
        try:
            loaded = load_reference_static_data()
            if isinstance(loaded, dict):
                data = loaded
        except Exception:  # noqa: BLE001 - manual fallback should not depend on static parsing
            data = {}
    map_nests = data.get("map_nests") if isinstance(data.get("map_nests"), dict) else {}
    for raw_map_id, raw_row in map_nests.items():
        map_id = _to_optional_int(raw_map_id)
        if map_id is None:
            continue
        name = ""
        if isinstance(raw_row, (list, tuple)) and len(raw_row) >= 2:
            name = _text(raw_row[1], "")
        for alias in {str(map_id), name, f"{map_id}{name}", f"{name}{map_id}"}:
            _add_manual_map_alias(lookup, alias, map_id, name)
    for alias, map_id in MANUAL_GENERIC_MAP_ALIASES.items():
        _add_manual_map_alias(lookup, alias, map_id, _manual_map_name(map_id, lookup=lookup))
    return lookup


def _manual_map_name(map_id: Any, *, lookup: dict[str, tuple[int, str]] | None = None) -> str:
    parsed = _to_optional_int(map_id)
    if parsed is None:
        return ""
    entries = lookup if lookup is not None else _manual_map_lookup()
    row = entries.get(str(parsed))
    return row[1] if row else ""


def _manual_map_display_value(value: Any) -> str:
    map_id = _to_optional_int(value)
    if map_id is None:
        return _text(value, "")
    name = _manual_map_name(map_id)
    return f"{map_id} {name}".strip() if name else str(map_id)


def _manual_map_id_from_text(value: Any) -> tuple[int | None, str]:
    text = str(value or "").strip()
    if not text:
        return None, ""
    parsed = _to_optional_int(text)
    if parsed is not None:
        return parsed, ""
    match = re.search(r"(?<!\d)(\d{4})(?!\d)", text.replace(",", ""))
    if match:
        return int(match.group(1)), ""
    lookup = _manual_map_lookup()
    row = lookup.get(_normalize_manual_map_text(text))
    if row is not None:
        return row[0], ""
    return None, f"地图无法识别：{text}"


def _minimap_quality_key(value: Any) -> str:
    if value in (None, ""):
        return "unknown"
    text = str(value).strip().lower()
    if text.startswith("q") and text[1:].isdigit():
        return text
    parsed = _to_optional_int(value)
    if parsed is not None:
        return f"q{parsed}"
    return text or "unknown"


def _to_optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _format_manual_number(value: Any) -> str:
    parsed = _to_optional_float(value)
    if parsed is None:
        return "" if value in (None, "") else str(value)
    if parsed.is_integer():
        return str(int(parsed))
    return f"{parsed:.4f}".rstrip("0").rstrip(".")


def _manual_quality_label(key: str) -> str:
    return SPLIT_QUALITY_LABELS.get(key, QUALITY_LABELS.get(key, key))


def _manual_avg_grid_options(count: int, avg: float) -> list[int]:
    if can_compose_grid_total is None:
        return []
    checked_count, count_error = _to_manual_count(count, "")
    if count_error or checked_count is None or not math.isfinite(float(avg)):
        return []
    count = checked_count
    if count == 0:
        return [0] if avg == 0 else []
    low = count
    high = 18 * count
    target = _manual_finite_product(avg, count)
    if target is None:
        return []
    tolerance = 0.0001
    candidates = {
        int(math.floor(target)),
        int(round(target)),
        int(math.ceil(target)),
    }
    options = [
        grid
        for grid in sorted(candidates)
        if low <= grid <= high
        and abs(grid - target) <= tolerance
        and can_compose_grid_total(count, grid)
    ]
    if options:
        return options
    return [
        grid
        for grid in range(low, high + 1)
        if abs(grid - target) <= tolerance
        and can_compose_grid_total(count, grid)
    ]


def _decimal_places(text: str) -> int:
    value = str(text or "").strip().replace(",", "")
    if not value:
        return 0
    if "e" in value.lower():
        return 6
    if "." not in value:
        return 0
    return max(0, len(value.split(".", 1)[1]))


def _manual_decimal_parts(text: str) -> tuple[str, str] | None:
    value = str(text or "").strip().replace(",", "")
    if not value or "e" in value.lower():
        return None
    if "." in value:
        int_part, frac_part = value.split(".", 1)
        if not frac_part:
            return None
    else:
        int_part, frac_part = value, ""
    if not int_part or not int_part.isdigit() or (frac_part and not frac_part.isdigit()):
        return None
    return str(int(int_part)), frac_part


def _manual_avg_uses_display_rule(avg_text: str) -> bool:
    parts = _manual_decimal_parts(avg_text)
    if parts is None:
        return False
    _int_part, frac_part = parts
    return len(frac_part) <= 3


def _format_manual_game_avg(total_cells: int, count: int, *, max_decimals: int) -> str:
    if count <= 0 or total_cells < 0:
        return ""
    decimals = max(2, max_decimals)
    scale = 10**decimals
    floored_scaled = (int(total_cells) * scale) // int(count)
    int_part, frac_value = divmod(floored_scaled, scale)
    digits = str(frac_value).zfill(decimals)
    if int(total_cells) * scale == floored_scaled * int(count):
        digits = digits.rstrip("0")
    return f"{int_part}.{digits}" if digits else str(int_part)


def _manual_avg_text_matches_grid(avg_text: str, *, count: int, cells: int) -> bool:
    parts = _manual_decimal_parts(avg_text)
    if parts is None or count <= 0:
        return False
    int_part, frac_part = parts
    normalized = f"{int_part}.{frac_part}" if frac_part else int_part
    max_decimals = max(2, len(frac_part))
    formatted = _format_manual_game_avg(cells, count, max_decimals=max_decimals)
    if formatted == normalized:
        return True
    # Exact integer ratio: _format_manual_game_avg strips an all-zero fraction and
    # returns the bare integer ("2" for 36/18), so a user-typed "2.0"/"3.0" (whole
    # number, all-zero fraction) never matched and was wrongly rejected as 无法对应
    # 整数总格. Trailing zeros normally carry truncation precision (so "1.80" must
    # NOT match the exact 1.8 grid) — that contract is preserved because this only
    # fires for an all-zero fraction against a bare-integer format (i.e. an exact ratio).
    if frac_part and set(frac_part) <= {"0"} and formatted == int_part:
        return True
    return False


def _manual_avg_product_tolerance(avg_text: str, count: int) -> float:
    decimals = _decimal_places(avg_text)
    if decimals < 2:
        return 0.0001
    return max(0.0001, (0.5 * (10 ** -decimals) * max(1, int(count))) + 1e-9)


def _manual_finite_product(left: float | int, right: float | int) -> float | None:
    try:
        product = float(left) * int(right)
    except (OverflowError, TypeError, ValueError):
        return None
    return product if math.isfinite(product) else None


def _manual_avg_matches_cells(
    avg: float | None,
    *,
    avg_text: str,
    count: int,
    cells: int,
) -> bool:
    if avg is None:
        return True
    if int(count) == 0:
        return float(avg) == 0 and int(cells) == 0
    if _manual_avg_uses_display_rule(avg_text):
        return _manual_avg_text_matches_grid(avg_text, count=count, cells=cells)
    return abs(float(avg) * int(count) - int(cells)) <= _manual_avg_product_tolerance(
        avg_text,
        count,
    )


def _manual_avg_grid_options_from_text(count: int, avg: float, avg_text: str) -> list[int]:
    if can_compose_grid_total is None:
        return []
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or count < 0
        or count > _MANUAL_MAX_ITEM_COUNT
        or not math.isfinite(float(avg))
    ):
        return []
    if count == 0:
        return [0] if avg == 0 else []
    target = _manual_finite_product(avg, count)
    if target is None:
        return []
    low = count
    high = 18 * count
    if _manual_avg_uses_display_rule(avg_text):
        return [
            grid
            for grid in range(low, high + 1)
            if _manual_avg_text_matches_grid(avg_text, count=count, cells=grid)
            and can_compose_grid_total(count, grid)
        ]
    tolerance = _manual_avg_product_tolerance(avg_text, count)
    if not math.isfinite(tolerance):
        return []
    candidates = {
        int(math.floor(target)),
        int(round(target)),
        int(math.ceil(target)),
    }
    options = [
        grid
        for grid in sorted(candidates)
        if low <= grid <= high
        and abs(grid - target) <= tolerance
        and can_compose_grid_total(count, grid)
    ]
    if options:
        return options
    return [
        grid
        for grid in range(low, high + 1)
        if abs(grid - target) <= tolerance
        and can_compose_grid_total(count, grid)
    ]


def _manual_avg_count_from_cells(avg: float | None, cells: Any) -> int | None:
    if can_compose_grid_total is None or avg is None or cells in (None, ""):
        return None
    try:
        cells_value = float(str(cells).replace(",", "").strip())
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(cells_value) or not math.isfinite(float(avg)) or cells_value < 0:
        return None
    try:
        cells_int = int(round(cells_value))
    except (OverflowError, ValueError):
        return None
    if abs(cells_value - cells_int) > 0.0001:
        return None
    if avg == 0:
        return 0 if cells_int == 0 else None
    try:
        count = int(round(cells_int / avg))
    except (OverflowError, ValueError, ZeroDivisionError):
        return None
    if count <= 0 or abs(avg * count - cells_int) > 0.0001:
        return None
    checked_count, count_error = _to_manual_count(count, "")
    if count_error or checked_count is None:
        return None
    if not can_compose_grid_total(checked_count, cells_int):
        return None
    return checked_count


def _manual_avg_count_from_cells_text(
    avg: float | None,
    cells: Any,
    avg_text: str,
    *,
    count_label: str,
) -> tuple[int | None, str]:
    if can_compose_grid_total is None or avg is None or cells in (None, ""):
        return None, ""
    try:
        cells_value = float(str(cells).replace(",", "").strip())
    except (TypeError, ValueError, OverflowError):
        return None, ""
    if not math.isfinite(cells_value) or not math.isfinite(float(avg)) or cells_value < 0:
        return None, ""
    try:
        cells_int = int(round(cells_value))
    except (OverflowError, ValueError):
        return None, ""
    if abs(cells_value - cells_int) > 0.0001:
        return None, ""
    if avg == 0:
        return (0, "") if cells_int == 0 else (None, "")
    if _manual_avg_uses_display_rule(avg_text):
        # Only enumerate counts that are themselves legal manual inputs.  Grid cells
        # have a wider bound (9216), so using cells_int as the loop ceiling could
        # spend seconds in composability DP before later discovering count > 512.
        candidates = [
            count
            for count in range(1, min(cells_int, _MANUAL_MAX_ITEM_COUNT) + 1)
            if _manual_avg_text_matches_grid(avg_text, count=count, cells=cells_int)
            and can_compose_grid_total(count, cells_int)
        ]
        if len(candidates) == 1:
            return _to_manual_count(candidates[0], count_label)
        if candidates:
            return None, ""
        # A dirty-empty count field is intentionally not autofilled, but its derived
        # value still has to hit the same count gate before snapshot/apply.  Check the
        # direct ratio without invoking a high-count DP so 513/9216 fail immediately.
        try:
            direct_count = int(round(cells_int / avg))
        except (OverflowError, ValueError, ZeroDivisionError):
            return None, ""
        if (
            direct_count > 0
            and cells_int >= direct_count
            and cells_int <= 18 * direct_count
            and _manual_avg_text_matches_grid(
                avg_text,
                count=direct_count,
                cells=cells_int,
            )
        ):
            _checked_count, count_error = _to_manual_count(
                direct_count,
                count_label,
            )
            if count_error:
                return None, count_error
        return None, ""
    try:
        count = int(round(cells_int / avg))
    except (OverflowError, ValueError, ZeroDivisionError):
        return None, ""
    if count <= 0:
        return None, ""
    if not _manual_avg_matches_cells(avg, avg_text=avg_text, count=count, cells=cells_int):
        return None, ""
    checked_count, count_error = _to_manual_count(count, count_label)
    if count_error or checked_count is None:
        return None, count_error
    if not can_compose_grid_total(checked_count, cells_int):
        return None, ""
    return checked_count, ""


def _money(value: Any, fallback: str = "-") -> str:
    if value in (None, ""):
        return fallback
    try:
        return f"{int(round(float(value))):,}"
    except (TypeError, ValueError):
        return str(value)


def _parse_money(value: Any) -> float | None:
    """Parse a money string ('639,978') or number to float; None if not numeric."""
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace(",", "").replace("+", "").strip())
    except (TypeError, ValueError):
        return None


_QUOTE_RECEIPT_REQUIRED_KEYS = frozenset(
    {
        "schema",
        "source",
        "round",
        "action_round",
        "observed_round",
        "phase",
        "formal_mode",
        "input_fingerprint_scope",
        "input_fingerprint",
        "result_identity",
        "result_status",
        "result_combo_count",
        "balanced",
        "decision_range",
        "value_range",
    }
)
_QUOTE_IDENTITY_KEYS = frozenset(
    {
        "schema",
        "source",
        "input_fingerprint_scope",
        "input_fingerprint",
        "result_identity",
    }
)


def _strict_quote_summary_session_id(summary: Any) -> str | None:
    """Return the cache authority session without coercion or root fallback."""

    if type(summary) is not dict:  # noqa: E721 - exact producer boundary
        return None
    context = summary.get("context")
    if type(context) is not dict:  # noqa: E721
        return None
    session_id = context.get("session_id")
    if type(session_id) is not str:  # noqa: E721
        return None
    if not session_id or session_id.strip() != session_id:
        return None
    return session_id


def _strict_pre_settlement_display_quote(
    summary: Any,
) -> tuple[str, float, str, str] | None:
    """Return one product-shaped quote that was actually eligible to render.

    Some valid legacy/ref-prior summaries cannot emit the stronger reproducible
    quote receipt.  They are still the estimate the user saw.  Keep this
    process-local projection separate from receipt authority so settlement can
    compare against the displayed quote without inventing a receipt.
    """

    session_id = _strict_quote_summary_session_id(summary)
    if session_id is None or type(summary) is not dict:  # noqa: E721
        return None
    if summary.get("status") != "ok":
        return None
    context = summary.get("context")
    reference = summary.get("reference")
    truth = summary.get("truth")
    if type(context) is not dict or type(reference) is not dict:  # noqa: E721
        return None
    if truth is not None and type(truth) is not dict:  # noqa: E721
        return None
    if type(truth) is dict:  # noqa: E721
        truth_available = truth.get("available")
        if truth_available is not None and truth_available is not False:
            return None
    raw_receipt = summary.get("quote_receipt")
    raw_identity = summary.get("quote_identity")
    if (
        type(raw_receipt) is not dict  # noqa: E721
        or raw_receipt
        or type(raw_identity) is not dict  # noqa: E721
        or raw_identity
    ):
        return None
    if context.get("phase") not in {"reading", "bidding"}:
        return None
    hero = context.get("hero")
    map_id = context.get("map_id")
    formal_mode = context.get("formal_mode")
    if (
        type(hero) is not str  # noqa: E721
        or not hero
        or type(map_id) is not int  # noqa: E721
        or map_id <= 0
        or type(formal_mode) is not str  # noqa: E721
        or formal_mode in {"", "?", "unknown"}
        or context.get("is_supported_ref_hero") is not True
    ):
        return None
    for key in ("round", "action_round", "observed_round"):
        if type(context.get(key)) is not int or context[key] < 0:  # noqa: E721
            return None
    source = reference.get("source")
    if (
        type(source) is not str  # noqa: E721
        or not source
        or source in {"settlement", "settlement_partial"}
    ):
        return None
    balanced = _parse_money(reference.get("balanced"))
    if balanced is None or not math.isfinite(balanced) or balanced < 0:
        return None
    decision_range = reference.get("decision_range")
    value_range = reference.get("raw_value_range")
    if (
        type(decision_range) is not str  # noqa: E721
        or decision_range in {"", "-"}
        or type(value_range) is not str  # noqa: E721
        or value_range in {"", "-"}
        or reference.get("total_value_range") != value_range
    ):
        return None
    decision_values = tuple(_parse_money(part) for part in decision_range.split(" / "))
    value_values = tuple(_parse_money(part) for part in value_range.split(" / "))
    if (
        len(decision_values) != 3
        or len(value_values) != 3
        or any(
            value is None or not math.isfinite(value) or value < 0
            for value in (*decision_values, *value_values)
        )
        or not (
            decision_values[0] <= decision_values[1] <= decision_values[2]
            and value_values[0] <= value_values[1] <= value_values[2]
        )
        or decision_values[1] != balanced
    ):
        return None
    return session_id, balanced, decision_range, value_range


def _valid_quote_receipt(value: Any, *, summary: Any = None) -> bool:
    """Validate a complete producer receipt and, when supplied, its live summary.

    The two SHA fields are privacy-safe identities, not authorization by themselves.
    A cacheable receipt must also agree with every observable quote field in the same
    Panel summary so settlement cannot combine a current estimate with stale ranges.
    """

    if type(value) is not dict:  # noqa: E721 - exact JSON boundary
        return False
    if set(value) != _QUOTE_RECEIPT_REQUIRED_KEYS:
        return False
    if value.get("schema") != "hero_ref_quote_receipt_v1":
        return False
    if value.get("source") not in {"live_ref_result", "settlement_clone"}:
        return False
    if value.get("phase") not in {"reading", "bidding"}:
        return False
    if value.get("input_fingerprint_scope") != "ref_inputs_without_session_v1":
        return False
    if value.get("result_status") not in {
        "ok",
        "count_prior",
        "provisional_estimated",
    }:
        return False
    for key in ("round", "action_round", "observed_round"):
        if type(value.get(key)) is not int or value[key] < 0:  # noqa: E721
            return False
    if type(value.get("result_combo_count")) is not int or value["result_combo_count"] < 1:  # noqa: E721
        return False
    if type(value.get("balanced")) is not int or value["balanced"] < 0:  # noqa: E721
        return False
    formal_mode = value.get("formal_mode")
    if type(formal_mode) is not str or formal_mode in {"", "?", "unknown"}:  # noqa: E721
        return False
    for key in ("input_fingerprint", "result_identity"):
        digest = value.get(key)
        if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:  # noqa: E721
            return False
    for key in ("decision_range", "value_range"):
        text = value.get(key)
        if type(text) is not str or text in {"", "-"}:  # noqa: E721
            return False
    if summary is None:
        return True
    if type(summary) is not dict:  # noqa: E721
        return False

    context = summary.get("context")
    reference = summary.get("reference")
    evidence = summary.get("evidence")
    identity = summary.get("quote_identity")
    if not all(type(item) is dict for item in (context, reference, evidence, identity)):  # noqa: E721
        return False
    if _strict_quote_summary_session_id(summary) is None:
        return False
    if set(identity) != _QUOTE_IDENTITY_KEYS:
        return False
    if identity.get("schema") != "hero_ref_quote_identity_v1":
        return False
    if any(
        identity.get(key) != value.get(key)
        for key in (
            "source",
            "input_fingerprint_scope",
            "input_fingerprint",
            "result_identity",
        )
    ):
        return False
    if value["source"] != "live_ref_result":
        return False
    if value["phase"] != context.get("phase"):
        return False
    for receipt_key, context_key in (
        ("round", "round"),
        ("action_round", "action_round"),
        ("observed_round", "observed_round"),
    ):
        if value[receipt_key] != _parse_int(context.get(context_key)):
            return False
    if value["formal_mode"] != context.get("formal_mode"):
        return False
    balanced = _parse_money(reference.get("balanced"))
    if balanced is None or not math.isfinite(balanced) or balanced != value["balanced"]:
        return False
    if reference.get("decision_range") != value["decision_range"]:
        return False
    if reference.get("raw_value_range") != value["value_range"]:
        return False
    if reference.get("total_value_range") != value["value_range"]:
        return False
    if evidence.get("ref_status") != value["result_status"]:
        return False
    if _parse_int(evidence.get("ref_combo_count")) != value["result_combo_count"]:
        return False
    return True


def _current_summary_fresh_paired_quality_keys(summary: Any) -> frozenset[str]:
    """Return quality keys whose paired candidates belong to the visible round."""
    if type(summary) is not dict:  # noqa: E721
        return frozenset()
    context = summary.get("context")
    truth = summary.get("truth")
    if type(context) is not dict:  # noqa: E721
        return frozenset()
    session_id = context.get("session_id")
    round_no = context.get("round")
    if (
        type(session_id) is not str  # noqa: E721
        or not session_id
        or session_id.strip() != session_id
        or type(round_no) is not int  # noqa: E721
        or round_no < 0
    ):
        return frozenset()
    view = detail_composition_view(summary)
    ready_keys = frozenset(
        quality.key
        for quality in view.qualities
        if quality.status in {"ready", "exact"} and bool(quality.candidates)
    )
    if context.get("phase") == "settled":
        return (
            ready_keys
            if type(truth) is dict and truth.get("available") is True  # noqa: E721
            else frozenset()
        )
    continuity = summary.get("continuity")
    if type(continuity) is dict:  # noqa: E721
        source_round = continuity.get("source_round")
        if _parse_int(source_round) != round_no:
            current = continuity.get("current_joint_exact_qualities")
            if (
                type(current) is not list  # noqa: E721
                or any(
                    type(key) is not str or key not in DISPLAY_QUALITY_KEYS  # noqa: E721
                    for key in current
                )
                or len(current) > len(DISPLAY_QUALITY_KEYS)
                or len(set(current)) != len(current)
            ):
                return frozenset()
            return ready_keys & frozenset(current)
    receipt = summary.get("quote_receipt")
    if type(receipt) is dict and receipt and not _valid_quote_receipt(  # noqa: E721
        receipt,
        summary=summary,
    ):
        return frozenset()
    return ready_keys


def _current_summary_has_fresh_paired_quality(summary: Any) -> bool:
    return bool(_current_summary_fresh_paired_quality_keys(summary))


def _flag_color(level: str) -> str:
    if level == "risk":
        return BAD
    if level == "watch":
        return WARN
    if level == "neutral":
        return ACCENT
    return MUTED


def _terminate_pid(pid: int) -> bool:
    if pid <= 0 or pid == os.getpid():
        return False
    try:
        import psutil

        process = psutil.Process(pid)
        process.terminate()
        try:
            process.wait(timeout=1.2)
        except psutil.TimeoutExpired:
            process.kill()
            process.wait(timeout=1.0)
        return True
    except ImportError:
        try:
            os.kill(pid, signal.SIGTERM)
            return True
        except OSError:
            return False
    except Exception:
        return False


_PID_ALIVE = "alive"
_PID_DEAD = "dead"
_PID_UNKNOWN = "unknown"
_PID_DEAD_CONFIRMATIONS = 2
_AUTO_PSUTIL = object()


class _WindowsProcessHandle:
    """An identity-bound, limited process handle used only for liveness checks."""

    def __init__(self, pid: int, handle: int, kernel32: Any) -> None:
        self.pid = int(pid)
        self._handle = int(handle)
        self._kernel32 = kernel32

    def poll(self) -> str:
        if not self._handle:
            return _PID_UNKNOWN
        try:
            wait_result = int(self._kernel32.WaitForSingleObject(self._handle, 0))
        except Exception:
            return _PID_UNKNOWN
        if wait_result == 0x00000102:  # WAIT_TIMEOUT: the bound process is still alive.
            return _PID_ALIVE
        if wait_result == 0x00000000:  # WAIT_OBJECT_0: the bound process exited.
            return _PID_DEAD
        return _PID_UNKNOWN

    def close(self) -> None:
        handle, self._handle = self._handle, 0
        if not handle:
            return
        try:
            self._kernel32.CloseHandle(handle)
        except Exception:
            pass


def _open_windows_process_handle(
    pid: int,
    *,
    kernel32: Any = None,
) -> _WindowsProcessHandle | None:
    if pid <= 0 or (os.name != "nt" and kernel32 is None):
        return None
    try:
        if kernel32 is None:
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [
                ctypes.c_ulong,
                ctypes.c_int,
                ctypes.c_ulong,
            ]
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            kernel32.WaitForSingleObject.restype = ctypes.c_ulong
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_int
        # SYNCHRONIZE binds the handle to this exact process identity even if its PID is
        # later reused. QUERY_LIMITED_INFORMATION is intentionally the least privileged
        # inspection right used by the overlay.
        raw_handle = kernel32.OpenProcess(0x00100000 | 0x00001000, False, int(pid))
        handle = int(getattr(raw_handle, "value", raw_handle) or 0)
        if not handle:
            return None
        return _WindowsProcessHandle(pid, handle, kernel32)
    except Exception:
        return None


def _psutil_pid_liveness(
    pid: int,
    *,
    expected_create_time: float | None = None,
    psutil_module: Any = _AUTO_PSUTIL,
) -> tuple[str, float | None]:
    if pid <= 0:
        return _PID_DEAD, None
    if pid == os.getpid():
        return _PID_ALIVE, None
    if psutil_module is _AUTO_PSUTIL:
        try:
            import psutil as psutil_module
        except Exception:
            return _PID_UNKNOWN, None
    try:
        if not bool(psutil_module.pid_exists(pid)):
            return _PID_DEAD, None
        process = psutil_module.Process(pid)
        if not bool(process.is_running()):
            return _PID_DEAD, None
        try:
            observed_create_time = float(process.create_time())
        except Exception:
            observed_create_time = None
    except Exception as exc:
        no_such_process = getattr(psutil_module, "NoSuchProcess", None)
        if isinstance(no_such_process, type) and isinstance(exc, no_such_process):
            return _PID_DEAD, None
        # AccessDenied and transient process-query failures are not proof of exit.
        return _PID_UNKNOWN, None
    if (
        expected_create_time is not None
        and observed_create_time is not None
        and abs(observed_create_time - expected_create_time) > 0.001
    ):
        return _PID_DEAD, observed_create_time
    if observed_create_time is None and expected_create_time is None:
        # Without a persistent handle or create time, a reused PID cannot be tied to
        # the monitor instance the launcher asked us to watch.
        return _PID_UNKNOWN, None
    return _PID_ALIVE, observed_create_time


class _WatchedPidProbe:
    def __init__(
        self,
        pid: int,
        *,
        handle_factory: Callable[[int], _WindowsProcessHandle | None] = (
            _open_windows_process_handle
        ),
        psutil_module: Any = _AUTO_PSUTIL,
    ) -> None:
        self.pid = int(pid)
        self._handle_factory = handle_factory
        self._psutil_module = psutil_module
        self._handle = handle_factory(self.pid)
        self._expected_create_time: float | None = None

    def poll(self) -> str:
        if self._handle is None:
            # A transient access/query failure may recover. Bind a persistent handle as
            # soon as Windows permits it; never infer death merely because open failed.
            self._handle = self._handle_factory(self.pid)
        if self._handle is not None:
            return self._handle.poll()
        state, observed_create_time = _psutil_pid_liveness(
            self.pid,
            expected_create_time=self._expected_create_time,
            psutil_module=self._psutil_module,
        )
        if (
            state == _PID_ALIVE
            and self._expected_create_time is None
            and observed_create_time is not None
        ):
            self._expected_create_time = observed_create_time
        return state

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


class _WatchedPidGroup:
    def __init__(
        self,
        pids: tuple[int, ...],
        *,
        probe_factory: Callable[[int], Any] = _WatchedPidProbe,
        dead_confirmations: int = _PID_DEAD_CONFIRMATIONS,
    ) -> None:
        self._probes = {
            int(pid): probe_factory(int(pid))
            for pid in pids
            if int(pid) > 0
        }
        self._dead_streaks = {pid: 0 for pid in self._probes}
        self._dead_confirmations = max(2, int(dead_confirmations))

    def poll(self) -> str:
        aggregate = _PID_ALIVE
        for pid, probe in self._probes.items():
            state = str(probe.poll())
            if state == _PID_DEAD:
                self._dead_streaks[pid] += 1
                if self._dead_streaks[pid] >= self._dead_confirmations:
                    return _PID_DEAD
                aggregate = _PID_UNKNOWN
            else:
                self._dead_streaks[pid] = 0
                if state != _PID_ALIVE:
                    aggregate = _PID_UNKNOWN
        return aggregate

    def exited(self, _pids: tuple[int, ...] = ()) -> bool:
        return self.poll() == _PID_DEAD

    def close(self) -> None:
        for probe in self._probes.values():
            try:
                probe.close()
            except Exception:
                pass


def _game_exit_group_request(base_dir: Path, launch_id: str,
                             monitor_identities: dict[int, int]) -> dict[str, Any] | None:
    """Accept only this UI's captured launch and exact monitor incarnation."""
    if type(launch_id) is not str or not launch_id or not monitor_identities:
        return None
    startup = _read_json(base_dir / "startup_session.json")
    status = _read_json(base_dir / "lifecycle_status.json")
    if not isinstance(startup, dict) or not isinstance(status, dict):
        return None
    pid = status.get("monitor_pid")
    started = status.get("monitor_started_at_unix_ms")
    if (startup.get("launch_id") != launch_id or status.get("launch_id") != launch_id
            or status.get("owner_kind") != "wss_monitor_game_exit_group"
            or type(status.get("group_exit_schema_version")) is not int
            or status.get("group_exit_schema_version") != 1
            or type(pid) is not int or type(started) is not int or started <= 0
            or monitor_identities.get(pid) != started
            or status.get("phase") not in {"stop_requested", "cleanup_partial", "cleanup_complete"}
            or status.get("stop_reason") not in {"bound_game_exited", "bound_game_replaced"}):
        return None
    return status


def _watched_pid_exited(pids: tuple[int, ...]) -> bool:
    """Compatibility helper for one-shot callers; production uses a persistent group."""

    return any(
        _psutil_pid_liveness(int(pid))[0] == _PID_DEAD
        for pid in pids
        if int(pid) > 0
    )


def _named_process_running_state(
    process_name: str,
    *,
    process_iter: Any = None,
) -> bool | None:
    """Return True/False for a complete process scan, or None when it is unsafe to decide."""

    if process_iter is None:
        try:
            import psutil
        except Exception:
            return None
        process_iter = psutil.process_iter

    wanted = str(process_name or "").strip().casefold()
    if not wanted:
        return None
    scan_incomplete = False
    try:
        for process in process_iter(["name"]):
            try:
                name = str(process.info.get("name") or "").strip().casefold()
            except Exception:
                scan_incomplete = True
                continue
            if name == wanted:
                return True
    except Exception:
        return None
    return None if scan_incomplete else False


def _game_launch_candidate_ready(
    capture_status: Any,
    *,
    process_running_state: bool | None,
    now: float | None = None,
) -> bool:
    """Fail closed unless the current WSS launch is ready and BidKing is absent."""

    if not isinstance(capture_status, dict):
        return False
    if _text(capture_status.get("source"), "") != "wss_local":
        return False
    current_launch_id = _text(capture_status.get("_current_launch_id"), "")
    if not current_launch_id:
        return False
    network = validate_network_connection_status(
        capture_status.get("_network_connection_status"),
        launch_id=current_launch_id,
    )
    if _text(network.get("launch_id"), "") != current_launch_id:
        return False
    if (
        _text(network.get("game_connection_state"), "") != "not_running"
        or _text(network.get("wss_intercept_state"), "") != "ready"
        or _text(network.get("capture_decode_state"), "") != "idle"
    ):
        return False
    updated_at = network.get("updated_at")
    if isinstance(updated_at, bool) or not isinstance(updated_at, (int, float)):
        return False
    checked_at = time.time() if now is None else float(now)
    age_seconds = checked_at - float(updated_at)
    if age_seconds < -2.0 or age_seconds > GAME_LAUNCH_STATUS_MAX_AGE_SECONDS:
        return False
    evidence = network.get("evidence")
    if not isinstance(evidence, dict):
        return False
    if (
        evidence.get("game_running") is not False
        or _text(evidence.get("helper_phase"), "") != "helper_running"
        or _text(evidence.get("helper_error_code"), "")
    ):
        return False
    helper = capture_status.get("_wss_helper_status")
    if not isinstance(helper, dict):
        return False
    if (
        _text(helper.get("launch_id"), "") != current_launch_id
        or _text(helper.get("phase"), "") != "helper_running"
        or _text(helper.get("error_code"), "")
    ):
        return False
    return process_running_state is False


def _launch_fixed_steam_game_uri() -> None:
    """Ask the registered Steam protocol handler to launch the fixed BidKing app."""

    startfile = getattr(os, "startfile", None)
    if not callable(startfile):
        raise OSError("Steam protocol launcher is unavailable")
    startfile(STEAM_GAME_URI)


class SlimScrollbar(tk.Canvas):
    def __init__(self, parent: tk.Widget, *, command: Any, hide_when_full: bool = True) -> None:
        super().__init__(
            parent,
            width=8,
            height=1,
            bg=PANEL_MUTED,
            highlightthickness=0,
            borderwidth=0,
            relief="flat",
        )
        self.command = command
        self.hide_when_full = hide_when_full
        self._pack_kwargs: dict[str, Any] | None = None
        self._grid_kwargs: dict[str, Any] | None = None
        self._hidden = False
        self._first = 0.0
        self._last = 1.0
        self._drag_anchor = 0
        self._dragging = False
        self._hover = False
        self.bind("<Configure>", self._redraw, add="+")
        self.bind("<ButtonPress-1>", self._begin_drag, add="+")
        self.bind("<B1-Motion>", self._drag, add="+")
        self.bind("<ButtonRelease-1>", self._end_drag, add="+")
        self.bind("<Enter>", self._enter, add="+")
        self.bind("<Leave>", self._leave, add="+")

    def pack(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        self._pack_kwargs = kwargs.copy()
        self._grid_kwargs = None
        self._hidden = False
        super().pack(*args, **kwargs)

    def grid(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        self._grid_kwargs = kwargs.copy()
        self._pack_kwargs = None
        self._hidden = False
        super().grid(*args, **kwargs)

    def set(self, first: Any, last: Any) -> None:
        try:
            self._first = max(0.0, min(1.0, float(first)))
            self._last = max(self._first, min(1.0, float(last)))
        except (TypeError, ValueError):
            return
        full = self._first <= 0.001 and self._last >= 0.999
        if full and self.hide_when_full:
            if not self._hidden:
                if self._grid_kwargs is not None:
                    self.grid_remove()
                elif self._pack_kwargs is not None:
                    self.pack_forget()
                self._hidden = True
            return
        if self._hidden:
            self._restore()
        self._redraw()

    def _restore(self) -> None:
        if self._grid_kwargs is not None:
            super().grid(**self._grid_kwargs)
        elif self._pack_kwargs is not None:
            super().pack(**self._pack_kwargs)
        self._hidden = False

    def _thumb_geometry(self) -> tuple[int, int, int]:
        height = max(1, self.winfo_height())
        visible = max(0.02, min(1.0, self._last - self._first))
        thumb_height = max(22, int(height * visible))
        thumb_height = min(height, thumb_height)
        max_top = max(0, height - thumb_height)
        max_first = max(0.001, 1.0 - visible)
        top = int((self._first / max_first) * max_top) if max_top else 0
        top = max(0, min(max_top, top))
        return top, top + thumb_height, thumb_height

    def _redraw(self, _event: tk.Event[Any] | None = None) -> None:
        if self._hidden:
            return
        self.delete("all")
        width = max(8, self.winfo_width())
        height = max(1, self.winfo_height())
        self.create_rectangle(width // 2 - 1, 0, width // 2 + 1, height, fill=SCROLL_LINE, outline="")
        top, bottom, _thumb_height = self._thumb_geometry()
        thumb_color = WARM if self._dragging else (ACCENT if self._hover else MUTED)
        self.create_rectangle(
            1,
            top + 1,
            width - 2,
            max(top + 22, bottom - 1),
            fill=thumb_color,
            outline="",
        )

    def _move_to_thumb_top(self, top: int) -> None:
        _old_top, _old_bottom, thumb_height = self._thumb_geometry()
        height = max(1, self.winfo_height())
        max_top = max(0, height - thumb_height)
        if max_top <= 0:
            return
        visible = max(0.02, min(1.0, self._last - self._first))
        max_first = max(0.001, 1.0 - visible)
        new_first = max(0.0, min(max_top, float(top))) / max_top * max_first
        self.command("moveto", new_first)

    def _begin_drag(self, event: tk.Event[Any]) -> str:
        top, bottom, thumb_height = self._thumb_geometry()
        if event.y < top or event.y > bottom:
            top = int(event.y - thumb_height / 2)
            self._move_to_thumb_top(top)
            top, _bottom, _thumb_height = self._thumb_geometry()
        self._drag_anchor = event.y - top
        self._dragging = True
        self._redraw()
        return "break"

    def _drag(self, event: tk.Event[Any]) -> str:
        if self._dragging:
            self._move_to_thumb_top(event.y - self._drag_anchor)
        return "break"

    def _end_drag(self, _event: tk.Event[Any]) -> str:
        self._dragging = False
        self._redraw()
        return "break"

    def _enter(self, _event: tk.Event[Any]) -> None:
        self._hover = True
        self._redraw()

    def _leave(self, _event: tk.Event[Any]) -> None:
        self._hover = False
        self._redraw()


def _slim_scrollbar(parent: tk.Widget, *, command: Any, hide_when_full: bool = True) -> SlimScrollbar:
    return SlimScrollbar(parent, command=command, hide_when_full=hide_when_full)


def _monitor_lock_path(snapshot_path: Path) -> Path:
    return snapshot_path.parent / "monitor.lock"


def _pid_from_monitor_lock(lock_path: Path) -> int | None:
    payload = _read_json(lock_path)
    try:
        pid = int(payload.get("pid"))
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _cleanup_exit_targets(
    pids: tuple[int, ...] | list[int],
    lock_paths: tuple[Path, ...] | list[Path],
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


def _package_root_with_monitor(snapshot_path: Any) -> Path | None:
    # External snapshots live under LocalAppData and are intentionally unrelated to package
    # placement.  Package cleanup must never infer a root by walking snapshot ancestors.
    del snapshot_path
    try:
        root = _validated_package_root()
        _require_plain_descendant(
            root,
            root / "BidKingHeroMonitor",
            label="BidKingHeroMonitor",
        )
    except (PackageRootAuthorityError, ValueError):
        return None
    return root


def _find_calculator_launcher(snapshot_path: Any) -> tuple[str, Path] | None:
    """Find the launcher only under the module-bound package root.

    ``snapshot_path`` is deliberately ignored: public runtime snapshots are external state, not
    package identity.  Prefer the shell EXE, then the source/development PowerShell launcher.
    """
    del snapshot_path
    try:
        root = _validated_package_root()
    except PackageRootAuthorityError:
        return None
    for kind, relative in (
        ("exe", Path("BidKingLive.exe")),
        ("ps1", Path("scripts") / "start_live_npcap_overlay.ps1"),
    ):
        try:
            path = _require_plain_descendant(
                root,
                root / relative,
                label=f"{kind} 启动器",
                file=True,
            )
        except ValueError:
            continue
        return kind, path
    return None


def _spawn_restart_relauncher(
    launcher: tuple[str, Path],
    wait_pids: "list[int]",
    *,
    diagnostic_profile: str = DEFAULT_DIAGNOSTIC_PROFILE,
    restart_log_path: Path,
    _popen: Any = None,
) -> None:
    """派生一个【分离且脱离父 job】的 PowerShell 重启器，本 overlay 退出后仍存活，重新拉起计算器。

    ▸ 首选路径 = 包内启动脚本 `start_live_npcap_overlay.ps1 -Restart` + 内嵌 Python：脚本的 `-Restart`
      分支【自己强杀旧 monitor/overlay + 清锁】再起新实例，不依赖旧进程精确退出时机，也不会撞"已在
      运行就跳过"的守卫；找不到脚本才回退直接起壳 `BidKingLive.exe`。
    ▸ **脱离 job**：壳 exe(bat2exe 类)常把整串子进程放进 kill-on-close 的 Job，父 overlay 一退，
      重启器还没拉起新实例就被一起清掉 = 群友反馈的"UI 关了但不重开"。CREATE_BREAKAWAY_FROM_JOB 让
      重启器逃出该 Job；若 Job 不允许 breakaway 则 CreateProcess 失败，回退到不带该 flag。
    ▸ **可观测**：把动作/失败写进 external live log 的 `restart.log`，避免"静默一闪不重开"无从排查。
    """
    if os.name != "nt":
        raise RuntimeError("自动重启仅支持 Windows")
    kind, path = launcher
    if kind not in {"exe", "ps1"}:
        raise PackageRootAuthorityError("启动器类型无效")
    root = _validated_package_root()
    expected_relative = (
        Path("BidKingLive.exe")
        if kind == "exe"
        else Path("scripts") / "start_live_npcap_overlay.ps1"
    )
    expected_path = _require_plain_descendant(
        root,
        root / expected_relative,
        label="重启启动器",
        file=True,
    )
    try:
        same_launcher = os.path.samefile(path, expected_path)
    except OSError:
        same_launcher = False
    if not same_launcher:
        raise PackageRootAuthorityError("重启启动器不属于当前包根")
    path = expected_path

    def _optional_package_file(relative: Path, label: str) -> Path | None:
        try:
            return _require_plain_descendant(
                root,
                root / relative,
                label=label,
                file=True,
            )
        except ValueError:
            return None

    ps1 = _optional_package_file(
        Path("scripts") / "start_live_npcap_overlay.ps1",
        "PowerShell 重启器",
    )
    embedded_py = _optional_package_file(
        Path("runtime") / "python" / "python.exe",
        "内嵌 Python",
    )
    restart_log = Path(restart_log_path)
    if restart_log.name.casefold() != "restart.log":
        raise ValueError("restart log 文件名无效")
    _require_plain_directory(restart_log.parent, label="活动日志目录")
    diagnostic_profile = _normalize_diagnostic_profile(diagnostic_profile)

    def _q(text: Any) -> str:  # PowerShell 单引号字符串转义（' -> ''）
        return "'" + str(text).replace("'", "''") + "'"

    if ps1 is not None:
        # 脚本 + 内嵌 python(打包版) + -Restart(脚本自清旧实例)。dev/源码无内嵌 python 时省略该参数。
        ps_args = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1), "-Restart"]
        ps_args += ["-DiagnosticProfile", diagnostic_profile]
        if embedded_py is not None:
            ps_args += ["-PythonPath", str(embedded_py)]
        arglist = ",".join(_q(a) for a in ps_args)
        # -WindowStyle Hidden: 已提权时脚本就在这个 Start-Process 开的窗口里跑(+4s 守卫/Npcap 检查/起
        # monitor 会停留 ~10s),不隐藏就会看到一个空白 PowerShell 窗口(作者实测)。与脚本自提权分支
        # (-Verb RunAs -WindowStyle Hidden)一致隐藏。
        launch_cmd = (
            f"Start-Process -FilePath 'powershell.exe' -WindowStyle Hidden -WorkingDirectory {_q(root)} "
            f"-ArgumentList {arglist}"
        )
    else:  # 回退：直接起壳 exe(脚本缺失的异常情形)
        launch_cmd = f"Start-Process -FilePath {_q(path)} -WorkingDirectory {_q(root)}"

    ids = ",".join(str(int(p)) for p in wait_pids if int(p) > 0)
    wait_block = ""
    if ids:
        wait_block = (
            f"$ids=@({ids});"
            "$deadline=(Get-Date).AddSeconds(12);"
            "while((Get-Date) -lt $deadline){"
            "$alive=@($ids|Where-Object{Get-Process -Id $_ -ErrorAction SilentlyContinue});"
            "if($alive.Count -eq 0){break};Start-Sleep -Milliseconds 300};"
        )
    lg = _q(restart_log)
    script = (
        wait_block
        + f"try{{Add-Content -Path {lg} -Value ((Get-Date -Format o)+'  restart begin') -Encoding utf8;"
        + launch_cmd + ";"
        + f"Add-Content -Path {lg} -Value ((Get-Date -Format o)+'  restart launched') -Encoding utf8}}"
        + f"catch{{Add-Content -Path {lg} -Value ((Get-Date -Format o)+'  ERROR '+$_.Exception.Message) -Encoding utf8}}"
    )
    popen = _popen if _popen is not None else subprocess.Popen
    # ⚠️ 用 CREATE_NO_WINDOW,【绝不用 DETACHED_PROCESS】: powershell.exe 是控制台程序,
    # DETACHED_PROCESS 会让它【没有有效控制台】→ 脚本静默不执行(重启器啥都不干、连 restart.log 都
    # 写不出 = 群友/作者实测"UI 关了但不重开"的真根因;此 bug 从重启按钮加入起一直存在)。
    # CREATE_NO_WINDOW 给它一个隐藏控制台,脚本正常跑;子进程本就在父 overlay 退出后存活(孤儿不被杀)。
    CREATE_NO_WINDOW = 0x08000000
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_BREAKAWAY_FROM_JOB = 0x01000000
    argv = ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script]
    base_flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    try:
        popen(argv, creationflags=base_flags | CREATE_BREAKAWAY_FROM_JOB)
    except OSError:
        # Job 不允许 breakaway -> 退回不带该 flag(至少 NO_WINDOW,尽力而为)。
        popen(argv, creationflags=base_flags)


def _unload_windivert_driver_under(
    root: Path | None,
    *,
    runner: Any = None,
) -> bool:
    """Stop and delete the WinDivert kernel driver when it was loaded from
    inside ``root``.

    pydivert registers ``WinDivert64.sys`` as a demand-start kernel service the
    first time the monitor opens a handle. When the monitor is force-terminated
    on exit, pydivert never closes the handle, so the driver stays loaded and
    keeps the ``.sys`` file inside the package locked. That blocks deleting the
    whole package folder. Removing the service unloads the driver and releases
    the file. Scoped to our package path so an unrelated WinDivert install is
    never touched.
    """
    if os.name != "nt" or root is None:
        return False
    try:
        root_prefix = ntpath.normcase(ntpath.normpath(str(root.resolve())))
    except OSError:
        return False
    run = runner if runner is not None else _run_sc_command
    try:
        query = run(["sc", "qc", "WinDivert"])
    except (OSError, subprocess.SubprocessError):
        return False
    if query is None or query.returncode != 0:
        return False
    binary_path = ""
    for line in str(query.stdout or "").splitlines():
        if "BINARY_PATH_NAME" in line and ":" in line:
            binary_path = line.split(":", 1)[1].strip()
            break
    if not binary_path:
        return False
    if binary_path.startswith('"'):
        closing = binary_path.find('"', 1)
        binary_path = binary_path[1:closing] if closing > 1 else ""
    else:
        match = re.match(r"(?i)^(.+?\.sys)(?:\s|$)", binary_path)
        binary_path = match.group(1) if match else ""
    for prefix in ("\\??\\", "\\\\?\\"):
        if binary_path.startswith(prefix):
            binary_path = binary_path[len(prefix) :]
            break
    binary_normalized = ntpath.normcase(ntpath.normpath(binary_path)) if binary_path else ""
    if not binary_normalized or not binary_normalized.startswith(
        root_prefix.rstrip("\\/") + ntpath.sep
    ):
        return False
    for command in (["sc", "stop", "WinDivert"], ["sc", "delete", "WinDivert"]):
        try:
            run(command)
        except (OSError, subprocess.SubprocessError):
            pass
    return True


def _run_sc_command(command: list[str]) -> Any:
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=10,
        creationflags=no_window,
    )


class HoverTip:
    def __init__(self, widget: tk.Widget, text: str = "") -> None:
        self.widget = widget
        self.text = text
        self.tip: tk.Toplevel | None = None
        self._label: tk.Label | None = None
        self.fixed_position = False
        widget.bind("<Enter>", self._enter, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<Motion>", self._motion, add="+")

    def set_text(self, text: Any) -> None:
        self.text = _text(text, "")
        if self._label is not None:
            try:
                self._label.configure(text=self.text)
            except tk.TclError:
                pass

    def show_at(self, event: tk.Event[Any], text: Any | None = None) -> None:
        self.show_at_coords(event.x_root, event.y_root, text)

    def show_at_coords(self, x_root: int, y_root: int, text: Any | None = None) -> None:
        if text is not None:
            self.set_text(text)
        if not self.text:
            return
        if self.tip is None:
            self.tip = tk.Toplevel(self.widget)
            self.tip.wm_overrideredirect(True)
            self.tip.attributes("-topmost", True)
            shell = tk.Frame(
                self.tip,
                bg=TOOLTIP_BG,
                highlightthickness=1,
                highlightbackground=BORDER,
            )
            shell.pack()
            tk.Frame(shell, bg=ACCENT, width=3).pack(side="left", fill="y")
            self._label = tk.Label(
                shell,
                text=self.text,
                bg=TOOLTIP_BG,
                fg=TEXT,
                justify="left",
                anchor="nw",
                wraplength=460,
                padx=12,
                pady=9,
                relief="flat",
                borderwidth=0,
                font=(FONT_UI, 9),
            )
            self._label.pack(side="left")
        elif self._label is not None:
            self._label.configure(text=self.text)
        self.tip.geometry(f"+{int(x_root) + 12}+{int(y_root) + 10}")

    def hide(self, _event: tk.Event[Any] | None = None) -> None:
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None
            self._label = None

    def _enter(self, event: tk.Event[Any]) -> None:
        self.show_at(event)

    def _motion(self, event: tk.Event[Any]) -> None:
        if self.tip is not None and not self.fixed_position:
            self.tip.geometry(f"+{event.x_root + 12}+{event.y_root + 10}")


class PixelCat(tk.Canvas):
    """A tiny self-drawn pixel cat that animates while the engine is "thinking".

    No image assets — each frame is a small pixel grid drawn as scaled rects and
    cycled with after(). Shown during inference (推理中) to signal work + add a
    little life (retro pixel vibe to match the warm theme).
    """

    _PALETTE = {
        "O": "#e2893f",  # cat orange
        "D": "#3b2b22",  # dark outline / eyes / feet
        "P": "#e89aa0",  # pink nose
        "W": "#fbf7ef",  # eye / muzzle highlight
    }
    # 11-wide x 10-tall front-facing sitting cat (cols 0-10); tail lives in cols 10-11.
    _BODY = (
        ".D.......D.",
        ".DO.....OD.",
        ".DOOOOOOOD.",
        ".OOWOOOWOO.",
        ".OODOOODOO.",
        ".OOOOPOOOO.",
        ".OOOPPPOOO.",
        ".DOOOOOOOD.",
        "..DOOOOOD..",
        "..D.D.D.D..",
    )
    _BLINK = (
        ".D.......D.",
        ".DO.....OD.",
        ".DOOOOOOOD.",
        ".OOOOOOOOO.",
        ".OODOOODOO.",  # eyes closed -> dark line
        ".OOOOPOOOO.",
        ".OOOPPPOOO.",
        ".DOOOOOOOD.",
        "..DOOOOOD..",
        "..D.D.D.D..",
    )
    # Tail pixels (col, row), orange; wags up -> mid -> down across frames.
    _TAILS = (
        ((10, 5), (11, 5), (11, 4), (11, 3)),  # up
        ((10, 6), (11, 6), (11, 5), (11, 4)),  # mid
        ((10, 7), (11, 7), (11, 6), (11, 5)),  # down
        ((10, 6), (11, 6), (11, 5), (11, 4)),  # mid (paired with blink)
    )
    _PX = 2
    _COLS = 12
    _ROWS = 10

    def __init__(self, parent: tk.Misc, *, bg: str) -> None:
        super().__init__(
            parent,
            width=self._COLS * self._PX,
            height=self._ROWS * self._PX,
            bg=bg,
            highlightthickness=0,
            bd=0,
        )
        self._frame = 0
        self._after_id: str | None = None
        self._running = False

    def _render_frame(self) -> None:
        self.delete("all")
        px = self._PX
        body = self._BLINK if self._frame == 3 else self._BODY
        for r, row in enumerate(body):
            for c, ch in enumerate(row):
                color = self._PALETTE.get(ch)
                if color:
                    self.create_rectangle(c * px, r * px, c * px + px, r * px + px, fill=color, width=0)
        for c, r in self._TAILS[self._frame]:
            self.create_rectangle(c * px, r * px, c * px + px, r * px + px, fill=self._PALETTE["O"], width=0)

    def _tick(self) -> None:
        self._render_frame()
        self._frame = (self._frame + 1) % len(self._TAILS)
        self._after_id = self.after(230, self._tick)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._tick()

    def stop(self) -> None:
        self._running = False
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def set_background(self, bg: str) -> None:
        try:
            self.configure(bg=bg)
        except tk.TclError:
            pass


class BorderGlow:
    """A bright 'comet' that travels around the window border during inference.

    Four thin edge canvases are place()'d over the window perimeter only while
    inferring; a head + fading tail of dots advances clockwise around them. The
    rounded window region clips the corners, so the light reads as following the
    rounded edge. Colours are read live from the theme globals (BG / ACCENT).
    """

    _THICK = 3
    _SEGMENTS = 4  # evenly-spaced solid light bars circling together
    _SEG_LEN = 0.14  # length of each bar as a fraction of the perimeter
    _SPEED = 0.011  # perimeter fraction advanced per frame
    # Gradient along each bar, tail(0)->head(1): cream -> clay -> coral -> warm gold.
    _GRAD_STOPS = ((0.0, "BG"), (0.28, "ACCENT"), (0.62, "WARM"), (1.0, "#e8b24a"))

    def __init__(self, parent: tk.Misc) -> None:
        self.parent = parent
        self._pos = 0.0
        self._after_id: str | None = None
        self._running = False
        self._w = 0  # cached window size (avoids per-frame update_idletasks = flicker)
        self._h = 0
        t = self._THICK
        mk = lambda **kw: tk.Canvas(parent, bg=BG, highlightthickness=0, bd=0, **kw)
        self._top = mk(height=t)
        self._bottom = mk(height=t)
        self._left = mk(width=t)
        self._right = mk(width=t)
        self._edges = (self._top, self._bottom, self._left, self._right)
        parent.bind("<Configure>", self._on_parent_resize, add="+")

    def _on_parent_resize(self, event: tk.Event[Any]) -> None:
        if event.widget is self.parent:
            self._w, self._h = int(event.width), int(event.height)

    def _place(self) -> None:
        t = self._THICK
        self._top.place(relx=0.0, rely=0.0, relwidth=1.0, height=t)
        self._bottom.place(relx=0.0, rely=1.0, anchor="sw", relwidth=1.0, height=t)
        self._left.place(relx=0.0, rely=0.0, relheight=1.0, width=t)
        self._right.place(relx=1.0, rely=0.0, anchor="ne", relheight=1.0, width=t)
        # Raise the edge WIDGETs above content. Canvas.lift/tkraise are overridden to
        # mean canvas-ITEM raise, so call the underlying Tk window 'raise' directly.
        for c in self._edges:
            try:
                c.tk.call("raise", c._w)
            except tk.TclError:
                pass

    def _point(self, s: float, w: int, h: int) -> tuple[tk.Canvas, float, float]:
        # s in [0,1) -> (edge canvas, x, y) clockwise from the top-left corner.
        half = self._THICK / 2.0
        d = (s % 1.0) * 2.0 * (w + h)
        if d < w:
            return self._top, d, half
        d -= w
        if d < h:
            return self._right, half, d
        d -= h
        if d < w:
            return self._bottom, w - d, half
        d -= w
        return self._left, half, h - d

    @staticmethod
    def _resolve(token: str) -> str:
        # Resolve a gradient stop to a hex colour (theme globals by name, else literal).
        if token in ("BG", "ACCENT", "WARM", "GOOD", "BAD"):
            return globals().get(token, "#cc785c")
        return token

    def _grad(self, t: float) -> str:
        t = max(0.0, min(1.0, t))
        stops = self._GRAD_STOPS
        for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
            if t <= t1:
                f = 0.0 if t1 <= t0 else (t - t0) / (t1 - t0)
                return _blend_hex(self._resolve(c0), self._resolve(c1), f)
        return self._resolve(stops[-1][1])

    def _draw_arc(self, start: float, w: int, h: int) -> None:
        per = 2.0 * (w + h)
        samples = max(6, int(self._SEG_LEN * per / 3.0))  # ~3px per sample -> solid line
        prev: tuple[tk.Canvas, float, float] | None = None
        for i in range(samples + 1):
            t = i / samples  # 0 = tail, 1 = head
            canvas, x, y = self._point(start + t * self._SEG_LEN, w, h)
            if prev is not None and prev[0] is canvas:
                canvas.create_line(
                    prev[1], prev[2], x, y, fill=self._grad(t),
                    width=self._THICK, capstyle="round", tags="glow",
                )
            prev = (canvas, x, y)

    def _draw(self) -> None:
        w, h = self._w, self._h
        if w <= 1 or h <= 1:  # cache not warm yet -> read once (not every frame)
            w = self._w = max(1, int(self.parent.winfo_width()))
            h = self._h = max(1, int(self.parent.winfo_height()))
        for c in self._edges:
            c.delete("glow")
        for k in range(self._SEGMENTS):  # 4 solid bars, evenly spaced, circling together
            start = (self._pos + k / self._SEGMENTS - self._SEG_LEN) % 1.0
            self._draw_arc(start, w, h)

    def _tick(self) -> None:
        self._pos = (self._pos + self._SPEED) % 1.0
        self._draw()
        self._after_id = self.parent.after(45, self._tick)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._place()
        self._tick()

    def stop(self) -> None:
        self._running = False
        if self._after_id is not None:
            try:
                self.parent.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        for c in self._edges:
            try:
                c.place_forget()
            except tk.TclError:
                pass

    def refresh_background(self) -> None:
        for c in self._edges:
            try:
                c.configure(bg=BG)
            except tk.TclError:
                pass


class LyingCat(tk.Canvas):
    """A self-drawn lying 'loaf' cat mascot for the support widget.

    Always idles gently (slow tail sway + occasional blink) so it never looks
    frozen while the overlay sits in standby; perk() plays a brief lively wag
    (e.g. at settlement). Pure canvas sprite, no image assets.
    """

    _PALETTE = {
        "O": "#e2893f",  # cat orange
        "D": "#3b2b22",  # dark outline / eyes / paws
        "P": "#e89aa0",  # pink nose
        "W": "#fbf7ef",  # eye highlight
    }
    # 14-wide x 9-tall front-facing loaf; tail drawn separately (cols 10-13).
    _BODY = (
        "..D.....D.....",
        ".DOD...DOD....",
        ".OOOOOOOOO....",
        ".OWOOOWOOO....",
        ".ODOOODOOO....",
        ".OOOPOOOOO....",
        ".OOOOOOOOOO...",
        "..DDOOOODD....",
        "..............",
    )
    _BLINK = (
        "..D.....D.....",
        ".DOD...DOD....",
        ".OOOOOOOOO....",
        ".OOOOOOOOO....",
        ".ODDDDDOOO....",  # eyes closed -> dark line
        ".OOOPOOOOO....",
        ".OOOOOOOOOO...",
        "..DDOOOODD....",
        "..............",
    )
    _TAILS = (
        ((10, 5), (11, 4), (12, 3), (12, 2)),  # mid
        ((10, 5), (11, 4), (12, 3), (13, 2)),  # up
        ((10, 6), (11, 5), (12, 4), (12, 3)),  # down
    )
    # Mouth open -> meowing (a dark open mouth below the nose).
    _MEOW = (
        "..D.....D.....",
        ".DOD...DOD....",
        ".OOOOOOOOO....",
        ".OWOOOWOOO....",
        ".ODOOODOOO....",
        ".OOOPOOOOO....",
        ".OODDDOOOO....",  # open mouth
        "..DDOOOODD....",
        "..............",
    )
    # Stretch: body extends a row, paws spread to the corners (a luxurious 伸懒腰).
    _STRETCH = (
        "..D.....D.....",
        ".DOD...DOD....",
        ".OOOOOOOOO....",
        ".OWOOOWOOO....",
        ".ODOOODOOO....",
        ".OOOPOOOOO....",
        ".OOOOOOOOOO...",
        ".OOOOOOOOOO...",
        "DD.OOOOOO.DD..",
    )
    # Lick paw: tongue out (extra pink) + a front paw lifted to the face (舔爪).
    _LICK = (
        "..D.....D.....",
        ".DOD...DOD....",
        ".OOOOOOOOO....",
        ".OWOOOWOOO....",
        ".ODOOODOOO....",
        ".OOPPOOOOO....",
        ".ODOOOOOOOO...",
        "...OOOOODD....",
        "..............",
    )
    # Yawn: squinty eyes + a big open mouth (打哈欠).
    _YAWN = (
        "..D.....D.....",
        ".DOD...DOD....",
        ".OOOOOOOOO....",
        ".OOOOOOOOO....",
        ".ODDODDOOO....",
        ".OODDDOOOO....",
        ".OODDDDOOO....",
        "..DDOOOODD....",
        "..............",
    )
    # Head tilt: head + ears shift one column over the body (歪头).
    _TILT = (
        "...D.....D....",
        "..DOD...DOD...",
        "..OOOOOOOOO...",
        "..OWOOOWOOO...",
        "..ODOOODOOO...",
        "..OOOPOOOOO...",
        ".OOOOOOOOOO...",
        "..DDOOOODD....",
        "..............",
    )
    _FRAMES = {
        "body": _BODY, "blink": _BLINK, "meow": _MEOW, "stretch": _STRETCH,
        "lick": _LICK, "yawn": _YAWN, "tilt": _TILT,
    }
    # A small "z" glyph (cols 10-12, rows 0-2, clear of the down-tail) shown while sleeping.
    _ZZZ = ((10, 0), (11, 0), (12, 0), (11, 1), (10, 2), (11, 2), (12, 2))
    # Sequences: (frame_key, tail_idx, show_z). idle=slow sway+blink; perk=quick wag; meow=mouth
    # open/close+wag; sleep=eyes closed+pulsing z; stretch=arch out and back; lick=paw to face;
    # eat=fast chomps + wag (fed from the can).
    _IDLE_SEQ = (("body", 0, 0), ("body", 1, 0), ("body", 0, 0), ("body", 2, 0), ("blink", 0, 0))
    _PERK_SEQ = (("body", 1, 0), ("body", 2, 0), ("body", 1, 0), ("body", 0, 0))
    _MEOW_SEQ = (("meow", 1, 0), ("body", 2, 0), ("meow", 1, 0), ("body", 0, 0), ("meow", 2, 0), ("body", 1, 0))
    _SLEEP_SEQ = (("blink", 2, 1), ("blink", 2, 1), ("blink", 2, 0), ("blink", 2, 1))
    _STRETCH_SEQ = (("body", 0, 0), ("stretch", 1, 0), ("stretch", 1, 0), ("stretch", 0, 0), ("body", 0, 0))
    _LICK_SEQ = (("lick", 0, 0), ("body", 1, 0), ("lick", 0, 0), ("body", 0, 0))
    _EAT_SEQ = (("meow", 1, 0), ("body", 1, 0), ("meow", 2, 0), ("body", 2, 0))
    _YAWN_SEQ = (("body", 0, 0), ("yawn", 0, 0), ("yawn", 0, 0), ("yawn", 0, 0), ("body", 0, 0))
    _TILT_SEQ = (("tilt", 1, 0), ("tilt", 1, 0), ("body", 0, 0), ("tilt", 2, 0), ("body", 0, 0))
    _CHASE_SEQ = (("body", 0, 0), ("body", 1, 0), ("body", 2, 0), ("body", 1, 0), ("body", 0, 0), ("body", 2, 0))
    _IDLE_MS, _PERK_MS, _MEOW_MS, _SLEEP_MS = 620, 130, 150, 470
    _STRETCH_MS, _LICK_MS, _EAT_MS = 210, 175, 110
    _YAWN_MS, _TILT_MS, _CHASE_MS = 190, 200, 85
    _PX = 2
    _COLS = 14
    _ROWS = 9

    def __init__(self, parent: tk.Misc, *, bg: str) -> None:
        super().__init__(
            parent,
            width=self._COLS * self._PX,
            height=self._ROWS * self._PX,
            bg=bg,
            highlightthickness=0,
            bd=0,
        )
        self._i = 0
        self._after_id: str | None = None
        self._mode = "idle"
        self._mode_left = 0
        self._running = False

    def _render(self, frame_key: str, tail_idx: int, show_z: int) -> None:
        self.delete("all")
        px = self._PX
        for r, row in enumerate(self._FRAMES[frame_key]):
            for c, ch in enumerate(row):
                color = self._PALETTE.get(ch)
                if color:
                    self.create_rectangle(c * px, r * px, c * px + px, r * px + px, fill=color, width=0)
        for c, r in self._TAILS[tail_idx]:
            self.create_rectangle(c * px, r * px, c * px + px, r * px + px, fill=self._PALETTE["O"], width=0)
        if show_z:
            for c, r in self._ZZZ:
                self.create_rectangle(c * px, r * px, c * px + px, r * px + px, fill=self._PALETTE["D"], width=0)

    def _tick(self) -> None:
        seq, ms = {
            "perk": (self._PERK_SEQ, self._PERK_MS),
            "meow": (self._MEOW_SEQ, self._MEOW_MS),
            "sleep": (self._SLEEP_SEQ, self._SLEEP_MS),
            "stretch": (self._STRETCH_SEQ, self._STRETCH_MS),
            "lick": (self._LICK_SEQ, self._LICK_MS),
            "eat": (self._EAT_SEQ, self._EAT_MS),
            "yawn": (self._YAWN_SEQ, self._YAWN_MS),
            "tilt": (self._TILT_SEQ, self._TILT_MS),
            "chase": (self._CHASE_SEQ, self._CHASE_MS),
        }.get(self._mode, (self._IDLE_SEQ, self._IDLE_MS))
        frame_key, tail_idx, show_z = seq[self._i % len(seq)]
        self._render(frame_key, tail_idx, show_z)
        self._i += 1
        if self._mode != "idle":
            self._mode_left -= 1
            if self._mode_left <= 0:
                self._mode = "idle"
                self._i = 0
        self._after_id = self.after(ms, self._tick)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._tick()

    def stop(self) -> None:
        self._running = False
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def _enter(self, mode: str, ticks: int) -> None:
        self._mode = mode
        self._mode_left = ticks
        self._i = 0
        # Render the new action immediately instead of waiting out the current frame's delay
        # (sleep ticks are 470ms -> a click during a nap would otherwise look like it did nothing).
        if self._running and self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = self.after(1, self._tick)

    def perk(self) -> None:
        self._enter("perk", 16)  # brief lively wag (~2s)

    def meow(self) -> None:
        self._enter("meow", 12)  # mouth opens/closes a few times

    def sleep(self) -> None:
        self._enter("sleep", 9)  # eyes closed + pulsing z (~4s)

    def stretch(self) -> None:
        self._enter("stretch", 10)  # arches out and settles back (伸懒腰)

    def lick(self) -> None:
        self._enter("lick", 12)  # licks a front paw (舔爪)

    def eat(self) -> None:
        self._enter("eat", 14)  # fast chomps + wag (fed from the can)

    def yawn(self) -> None:
        self._enter("yawn", 8)  # squint + a big open mouth (打哈欠)

    def tilt(self) -> None:
        self._enter("tilt", 8)  # tilts its head and back (歪头)

    def chase(self) -> None:
        self._enter("chase", 16)  # whips its tail back and forth (追尾巴)

    def set_background(self, bg: str) -> None:
        try:
            self.configure(bg=bg)
        except tk.TclError:
            pass


class AhmadTkOverlay:
    def __init__(
        self,
        root: tk.Tk,
        *,
        snapshot_path: Path,
        interval_ms: int,
        exit_when_pids: tuple[int, ...] = (),
        stop_pids_on_exit: tuple[int, ...] = (),
        cleanup_lock_paths: tuple[Path, ...] = (),
        keep_monitor_on_close: bool = False,
        protect_monitor_while_game_running: bool = False,
        load_existing_snapshot: bool = False,
        diagnostic_profile: str = DEFAULT_DIAGNOSTIC_PROFILE,
        show_taskbar: bool = False,
        ui_prefs_enabled: bool = False,
        product_profile: str | None = None,
        pet_access_preview: str | None = None,
        pet_usage_event_handler: (
            Callable[[str, PetAccessState], bool | None] | None
        ) = None,
    ) -> None:
        self.root = root
        self.snapshot_path = snapshot_path
        self.interval_ms = max(300, int(interval_ms))
        self.diagnostic_profile = _normalize_diagnostic_profile(diagnostic_profile)
        self.product_profile = normalize_product_profile(
            resolve_product_profile() if product_profile is None else product_profile
        )
        self.network_diagnostic_cohort = (
            self.product_profile == PRODUCT_PROFILE_NETWORK_DIAGNOSTIC
        )
        self.show_taskbar = bool(show_taskbar)
        self.exit_when_pids = exit_when_pids
        self._watched_pid_group = _WatchedPidGroup(exit_when_pids)
        self._watched_pid_exited_fn = self._watched_pid_group.exited
        startup = _read_json(snapshot_path.parent / "startup_session.json")
        self._group_exit_launch_id = startup.get("launch_id", "") if isinstance(startup, dict) else ""
        self._group_exit_monitor_identities = {}
        for pid in exit_when_pids:
            state, created = _psutil_pid_liveness(pid)
            if state == _PID_ALIVE and created is not None:
                self._group_exit_monitor_identities[pid] = int(created * 1000.0)
        if self._group_exit_launch_id and self._group_exit_monitor_identities:
            self.root.after(100, self._poll_game_exit_group)
        self._stop_pids_on_exit = tuple(pid for pid in stop_pids_on_exit if pid > 0)
        self._cleanup_lock_paths = tuple(cleanup_lock_paths)
        self.keep_monitor_on_close = bool(keep_monitor_on_close)
        self.protect_monitor_while_game_running = bool(
            protect_monitor_while_game_running
        )
        self._exit_cleanup_done = False
        self._last_ui_heartbeat_at = time.monotonic()
        self._last_ui_stall_bucket = 0
        self._last_signature: tuple[Any, ...] | None = None
        self._last_inference_signature: tuple[Any, ...] | None = None
        self._last_bid_signature: tuple[Any, ...] = ()
        self._last_capture_status_signature: tuple[Any, ...] | None = None
        self._last_summary: dict[str, Any] = {}
        self._last_live_snapshot: dict[str, Any] = {}
        self._last_live_summary: dict[str, Any] = {}
        self._last_verified_quality_summary: dict[str, Any] = {}
        self._last_verified_quality_session_id = ""
        self._quality_unavailable_continuity_identity: (
            tuple[str, int, int] | None
        ) = None
        self._ref_problem_retry_signature: tuple[Any, ...] | None = None
        self._consumed_pet_settlement_reaction_sessions: set[str] = set()
        self._last_summary_log_signature: str | None = None
        # Version discovery reuses the monitor's one-shot startup check. The UI only polls
        # the local result for a short startup window; it never creates a second server heartbeat.
        self._startup_update_notice_applied = False
        self._startup_update_notice_deadline = time.monotonic() + 15.0
        self._startup_update_url = ""
        # Hero Ref result memoize: skip recomputation when ref engine inputs are
        # semantically identical to the last run (monitor's inference_signature
        # changes on every new frame's facts, but ref output is often unchanged —
        # observed ~11 identical-output re-runs in a 3-game aisha session).
        # Keyed by ref_input_signature; invalidated on session/round change.
        self._ref_cache_signature: tuple[Any, ...] | None = None
        self._ref_cache_summary: dict[str, Any] | None = None
        self._ref_cache_snapshot: dict[str, Any] | None = None
        # One atomically validated live quote: session, balanced, receipt and its
        # producer identity. The settlement review may replay this coherent unit,
        # never a current balanced value with stale receipt/ranges.
        self._last_pre_settlement_estimate: (
            tuple[str, float, dict[str, Any], dict[str, Any]] | None
        ) = None
        # Display-only fallback for valid live summaries that cannot issue the
        # stronger quote receipt.  It never becomes receipt/identity authority.
        self._last_pre_settlement_display_quote: (
            tuple[str, float, str, str] | None
        ) = None
        self._summary_result_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._summary_worker_running = False
        self._summary_worker_seq = 0
        self._summary_worker_signature: tuple[Any, ...] | None = None
        self._summary_worker_pending: dict[str, Any] | None = None
        self._summary_worker_mode: str | None = None
        self._compute_pending = False
        self._compute_pending_trace_seq = 0
        self._compute_terminal_trace_seq = -1
        self._compute_monitor_generation = 0
        self._compute_status_event = "idle"
        self._last_compute_status_signature: tuple[Any, ...] | None = None
        self._compute_latency_timestamps: dict[str, float] = {}
        self._compute_indicator_visible_seq = 0
        self._compute_indicator_idle_scheduled_seq = 0
        self._summary_worker_executor: Any = None
        self._ref_engine_executor: Any = None
        self._ref_engine_transient_executor: Any = None
        self._ref_engine_executor_mode: str | None = None
        self._ref_engine_liveness_state = "not_started"
        self._ref_engine_liveness_results: queue.Queue[dict[str, Any]] = queue.Queue()
        self._ref_engine_liveness_lock = threading.Lock()
        self._ref_engine_liveness_generation = 0
        self._ref_engine_liveness_token: object | None = None
        self._ref_engine_liveness_retry_at = 0.0
        self._ref_engine_liveness_retry_count = 0
        self._ref_engine_fallback_attempted_signature: tuple[Any, ...] | None = None
        self._deferred_live_apply: dict[str, Any] | None = None
        self._drag_active = False
        self._pending_minimap_data: dict[str, Any] = {}
        self._minimap_render_after_id: str | None = None
        self._pinned_sync_after_id: str | None = None
        self._pending_pinned_sync: tuple[int, int] | None = None
        self._manual_result_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._manual_worker_running = False
        self._manual_worker_seq = 0
        self._manual_worker_authority_denied = False
        self._manual_input_revision = 0
        self._manual_snapshot: dict[str, Any] = {}
        self._manual_summary: dict[str, Any] = {}
        self._manual_active = False
        self._manual_edit_enabled = False
        self._manual_live_session_id = ""
        self._manual_programmatic_update = False
        self._manual_dirty_fields: set[str] = set()
        self._manual_autofill_values: dict[str, str] = {}
        self._manual_settlement_edit_unlocked = False
        self._manual_auto_expanded_details = False
        self._game_launch_candidate_context = False
        self._game_launch_candidate_mode: str | None = None
        self._game_launch_candidate_bindings: dict[str, str] = {}
        self._game_launch_pending_until = 0.0
        self._game_launch_error_until = 0.0
        self._game_process_state_probe = _named_process_running_state
        self._game_uri_launcher = _launch_fixed_steam_game_uri
        self.details_composition_view_enabled = (
            os.environ.get("BIDKING_DISABLE_DETAILS_COMPOSITION_VIEW") != "1"
        )
        self._detail_composition_filter = "all"
        self._detail_composition_collapsed = False
        self._detail_composition_view = detail_composition_view({})
        self.settlement_values_hidden = False
        self.topmost_enabled = True
        self.last_diagnostic_export_path: Path | None = None
        self._minimap_data: dict[str, Any] = {}
        self._minimap_canvas_signatures: dict[int, tuple[Any, ...]] = {}
        self._canvas_tip: HoverTip | None = None
        self._popup_canvas_tip: HoverTip | None = None
        self._minimap_popup: tk.Toplevel | None = None
        self._popup_canvas: tk.Canvas | None = None
        self._popup_title: tk.Label | None = None
        self._popup_counts: tk.Label | None = None
        self._pinned_minimap_popup: tk.Toplevel | None = None
        self._pinned_canvas: tk.Canvas | None = None
        self._pinned_title: tk.Label | None = None
        self._pinned_counts: tk.Label | None = None
        self._pinned_canvas_tip: HoverTip | None = None
        self._pinned_offset: tuple[int, int] | None = None
        self._pinned_minimap_follow = True
        self._pinned_minimap_drag_offset: tuple[int, int] | None = None
        self._pinned_configure_after_id: str | None = None
        self._hide_minimap_after_id: str | None = None
        self.details_expanded = False
        self._load_existing_snapshot = load_existing_snapshot
        self._drag_offset: tuple[int, int] | None = None
        self._resize_anchor: tuple[int, int, int, int] | None = None
        self._resize_scale_anchor: float | None = None
        self._custom_details_size: tuple[int, int] | None = None
        self._custom_mini_size: tuple[int, int] | None = None
        self._custom_mini_scale: float | None = None
        self._mini_layout_snapshot: tuple[
            tuple[int, int] | None,
            tuple[int, int],
            float,
        ] | None = None
        self._ui_prefs_enabled = bool(ui_prefs_enabled)
        self._ui_prefs_path = ui_prefs_path_for_snapshot(snapshot_path)
        self._quote_preferences_path = self._ui_prefs_path.parent / "quote_preferences.json"
        self._quote_preferences, self._quote_preferences_error = (
            quote_prefs.load_preferences(self._quote_preferences_path)
            if self._ui_prefs_enabled else (quote_prefs.new_preferences(), None)
        )
        self._quote_policy = self._quote_preferences["schemes"][self._quote_preferences["active_name"]]
        self._quote_writer_id = uuid.uuid4().hex
        self._quote_application_revision = 0
        self._quote_applied_signature = None
        self._quote_record_status = quote_prefs.unknown("missing")
        self._ui_prefs_save_after_id: str | None = None
        preview_key = str(pet_access_preview or "").strip().lower() or None
        if preview_key is not None and preview_key not in PET_ACCESS_PREVIEWS:
            raise ValueError(f"unknown pet access preview: {pet_access_preview!r}")
        self._pet_access_preview = preview_key
        self._pet_usage_event_handler = pet_usage_event_handler
        self._pet_usage_request_queue: queue.Queue[
            tuple[str, dict[str, Any]]
        ] = queue.Queue()
        self._pet_usage_worker_running = False
        self._pet_usage_identity_marker: str | None = None
        self._pet_usage_authority_snapshot: (
            tuple[bytes, bytes, int, int, int] | None
        ) = None
        self._pet_usage_session_id = uuid.uuid4().hex
        self._pet_usage_next_seq = 0
        self._pet_access_dialog: tk.Toplevel | None = None
        self._pet_access_after_id: str | None = None
        self._last_pet_access_status: str | None = None
        self._last_pet_access_state: PetAccessState | None = None
        self._pet_trial_seconds_remaining: int | None = None
        self._pet_preview_seconds_remaining: int | None = None
        self._pet_preview_tick_monotonic: float | None = None
        self._pet_usage_start_pending = False
        self._pet_usage_visible_reported = False
        self._pet_usage_hidden_reported = False
        self._pending_window_position: tuple[int, int] | None = None
        self._pet_companion: PetCompanionOverlay | None = None
        self._pet_enabled = False
        self._pet_visibility_generation = 0
        self._pet_visibility_pending = False
        self._pet_visibility_restoring = False
        self._pet_color = "ragdoll"
        self._pet_position: tuple[int, int] | None = None
        self._pet_settlement_feedback_enabled = True
        self._pet_intro_seen = False
        self._pet_discovery_seen = False
        self._pet_discovery_after_id: str | None = None
        self._pet_discovery_pulse_step = 0
        self._pet_discovery_pulse_on = False
        (
            self._pet_touch_date,
            self._pet_touch_today,
            self._pet_touch_total,
        ) = normalize_pet_touch_state(None, 0, 0)
        self._pet_restore_pending = False
        self._pending_pet_preferences: dict[str, Any] | None = None
        self._last_companion_action: str | None = None
        self._pet_action_deck: list[str] = []
        self._resize_active = False
        self._resize_pending_pointer: tuple[int, int] | None = None
        self._resize_frame_after_id: str | None = None
        self._resize_toolbar_compact = False
        self._resize_shell_chrome_width = 30
        self._active_resize_tip: HoverTip | None = None
        self._live_scale_pending: float | None = None
        self._live_scale_after_id: str | None = None
        self._atomic_layout_depth = 0
        self._atomic_layout_redraw_suspended = False
        self._activation_refit_pending = False
        self._activation_refit_after_id: str | None = None
        self._ui_scale = 1.0
        self._scaled_layout_specs: list[tuple[tk.Widget, dict[str, int]]] = []
        self.theme_name = "warm"
        self.theme_values = _theme_by_name(self.theme_name)

        root.title("Hero Ref")
        root.configure(bg=BG)
        root.attributes("-topmost", True)
        _apply_taskbar_mode(root, show_taskbar=self.show_taskbar)  # borderless; +taskbar via APPWINDOW
        # self._set_cat_window_icon(root)  # mascot cat icon -- DISABLED (suspected of UI lag, revisit later)
        root.resizable(True, True)
        root.minsize(430, 320)
        root.geometry(f"{self._mini_geometry()}+20+0")
        # Re-clip rounded corners whenever the borderless window's size changes
        # (geometry paths re-round directly; this catches first-show + external resize).
        self._last_rounding_size: tuple[int, int] = (0, 0)
        root.bind("<Configure>", self._on_root_configure_round, add="+")
        root.bind("<Map>", self._on_root_map_activation_refit, add="+")

        style = ttk.Style(root)
        self.style = style
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Hero.TButton",
            background=PANEL_SOFT,
            foreground=TEXT,
            bordercolor=BORDER,
            lightcolor=PANEL_SOFT,
            darkcolor=PANEL_SOFT,
            padding=(8, 4),
            borderwidth=0,
            relief="flat",
        )
        style.map("Hero.TButton", background=[("active", HERO_BUTTON_ACTIVE)])

        self.outer = tk.Frame(root, bg=BG)
        self.outer.pack(fill="both", expand=True)
        # Traveling border light — animated only while inferring (see _show_pixel_cat).
        self.border_glow = BorderGlow(self.outer)

        tk.Frame(self.outer, bg=WARM, width=2).pack(side="left", fill="y")
        self.shell = tk.Frame(self.outer, bg=BG, padx=9, pady=9)
        self.shell.pack(side="left", fill="both", expand=True)
        stripe = tk.Frame(self.shell, bg=BG)
        stripe.pack(fill="x", pady=(0, 7))
        tk.Frame(stripe, bg=ACCENT, height=1).pack(side="left", fill="x", expand=True)
        tk.Frame(stripe, bg=WARM, height=1, width=82).pack(side="right", padx=(6, 0))

        header = tk.Frame(self.shell, bg=BG)
        header.pack(fill="x")
        top_row = tk.Frame(header, bg=BG)
        top_row.pack(fill="x")
        title_box = tk.Frame(top_row, bg=BG)
        self._header_title_box = title_box
        title_box.pack(side="left", fill="x", expand=True)
        title_header = tk.Frame(title_box, bg=BG)
        title_header.pack(fill="x")
        # Keep product identity in the main header line, but reserve its natural
        # width before the title gets the remaining parcel.  The outer four
        # header actions already reserve their own right-side parcel on top_row.
        header_identity = tk.Frame(title_header, bg=BG)
        self._header_identity = header_identity
        header_identity.pack(side="right", anchor="e")
        self.title = tk.Label(
            title_header,
            text="Hero Ref",
            bg=BG,
            fg=TEXT,
            font=(FONT_UI, 12, "bold"),
            anchor="w",
        )
        self.title.pack(side="left", fill="x", expand=True)
        title_box.bind("<Configure>", self._refit_header_title_wrap, add="+")
        self.version_label = tk.Label(
            header_identity,
            text=f"v{HERO_REF_DISPLAY_VERSION}",
            bg=BG,
            fg=DIM,
            font=(FONT_UI, 7),
            anchor="e",
        )
        self.version_label.pack(side="right")
        self.version_tip = HoverTip(
            self.version_label,
            f"Hero Ref v{HERO_REF_DISPLAY_VERSION}\n正在检查更新…",
        )
        # A compact semantic badge is immediately left of the version.  It only
        # consumes title space while visible; version and the four actions retain
        # their natural width at every DPI.
        self.update_notice = tk.Button(
            header_identity,
            text="有新版",
            command=self._open_startup_update_url,
            bg=BG,
            fg=WARM,
            activebackground=BG,
            activeforeground=WARM,
            relief="flat",
            bd=0,
            padx=0,
            pady=0,
            font=(FONT_UI, 7, "bold"),
            cursor="hand2",
            takefocus=True,
        )
        self.update_notice.pack(side="right", padx=(0, 6))
        self.update_notice.pack_forget()
        self.update_notice_tip = HoverTip(self.update_notice, "")
        subtitle_row = tk.Frame(header, bg=BG)
        self._header_subtitle_row = subtitle_row
        subtitle_row.pack(fill="x")
        self.subtitle = tk.Label(
            subtitle_row,
            text="等待实时包",
            bg=BG,
            fg=MUTED,
            font=(FONT_UI, 7),
            anchor="w",
        )
        # 175% DPI 下标题、版本和四个顶栏按钮会挤满首行；诊断身份必须始终可见，
        # 因此只在该 profile 下增加一条紧凑独立行，标准包不增加任何高度。
        self.product_profile_badge = tk.Label(
            title_box,
            text=NETWORK_DIAGNOSTIC_BADGE,
            bg=PANEL_SOFT,  # noqa: F821 - theme globals are rebound by apply_theme
            fg=WARM,  # noqa: F821 - theme globals are rebound by apply_theme
            font=(FONT_UI, 6, "bold"),
            padx=4,
            pady=1,
            anchor="w",
        )
        self.product_profile_badge_tip = HoverTip(
            self.product_profile_badge,
            NETWORK_DIAGNOSTIC_TOOLTIP,
        )
        if self.network_diagnostic_cohort:
            self.product_profile_badge.pack(anchor="w", pady=(2, 0))
        # 激活/试用状态: 单行左侧显示剩余时间，右侧保留 token 管理入口。进入最后 90 天的
        # 有效付费用户必须能换绑/更换 token；此前只显示会员文字却解绑点击，导致临近到期用户
        # 没有任何恢复入口。长期及历史无期限会员仍隐藏整行，保持紧凑。它挂在完整 header 宽度上。
        self.activation_row = tk.Frame(header, bg=BG)
        self.activation_label = tk.Label(
            self.activation_row,
            text="免费额度 · 检测中…",
            bg=BG,
            fg=MUTED,
            font=(FONT_UI, 8, "bold"),
            anchor="w",
            cursor="hand2",
        )
        self.activation_action = tk.Button(
            self.activation_row,
            text=token_entry_action_text(self.product_profile),
            command=self._open_token_dialog,
            bg=BG,
            fg=GOOD,
            activebackground=BG,
            activeforeground=GOOD,
            relief="flat",
            bd=0,
            padx=2,
            pady=0,
            font=(FONT_UI, 7, "bold"),
            cursor="hand2",
            takefocus=True,
        )
        self.activation_action.pack(side="right")
        self.activation_label.pack(side="left", fill="x", expand=True)
        self.activation_label.bind("<Button-1>", self._open_token_dialog)
        self.activation_row.pack(fill="x")
        self.activation_tip = HoverTip(self.activation_label, "点击此处输入激活 token")
        self.activation_action_tip = HoverTip(
            self.activation_action,
            (
                "打开已有 token 输入框"
                if self.network_diagnostic_cohort
                else "打开 token 输入框与购买说明"
            ),
        )
        # 激活状态(本进程, 与 monitor 共享设备绑定缓存): _act_ready 前保持放行避免启动闪降级。
        self._act_state: dict[str, Any] = {
            "run_full": True, "needs_token": False, "warn": False,
            "text": "检测中…", "mode": "checking",
        }
        self._act_ready = False
        self._expired_latched = False   # 到期即锁；仅服务端重新确认有效试用/会员后解锁
        self._act_diag: dict[str, Any] = {}
        self._token_dialog: tk.Toplevel | None = None
        self._compatibility_dialog: tk.Toplevel | None = None
        self._cloud_big_data_opening = False
        self._cloud_big_data_reset_after_id: str | None = None
        self.root.after(300, self._start_activation_worker)
        self.root.after(350, self._poll_startup_update_notice)
        self.credit_top = tk.Label(
            title_box,
            text=CREDIT_TEXT,
            bg=BG,
            fg=DIM,
            font=(FONT_UI, 6),
            anchor="w",
        )
        self.title_tip = HoverTip(self.title, CREDIT_TEXT)
        header_actions = tk.Frame(subtitle_row, bg=BG)
        header_actions.pack(side="right", padx=(6, 0))
        self.subtitle.pack(side="left", fill="x", expand=True)
        # 三个头部按钮统一成同款圆角药丸 + 同 width/padx/pady, 只颜色不同(红叉/橙置顶/绿重启),
        # 避免"⟳ 最大、× 最小"的不均衡(各字形宽度不一, 固定 width 后盒子等宽)。
        _HDR_BTN = dict(padx=5, pady=2, width=2)
        self.close_button = self._rounded_button(
            header_actions,
            text="×",
            bg=BAD,
            fg="#ffffff",
            outline=BAD,
            **_HDR_BTN,
        )
        self.close_button.pack(side="right", padx=(4, 0))
        self.close_button.bind("<Button-1>", lambda _e: self._on_user_close(), add="+")
        self.close_tip = HoverTip(
            self.close_button,
            (
                CLOSE_KEEP_MONITOR_TIP_TEXT
                if self.keep_monitor_on_close
                else (
                    CLOSE_PROTECT_MONITOR_TIP_TEXT
                    if self.protect_monitor_while_game_running
                    else CLOSE_TIP_TEXT
                )
            ),
        )
        self.topmost_button = self._rounded_button(
            header_actions,
            text="T",
            bg=PANEL_SOFT,
            fg=WARM,
            outline=WARM,
            **_HDR_BTN,
        )
        self.topmost_button.pack(side="right", padx=(4, 0))
        self.topmost_button.bind("<Button-1>", self.toggle_topmost, add="+")
        self.topmost_tip = HoverTip(self.topmost_button, TOPMOST_ON_TIP)
        self.restart_button = self._rounded_button(
            header_actions,
            text="⟳",
            bg=PANEL_SOFT,
            fg=GOOD,
            outline=GOOD,
            **_HDR_BTN,
        )
        self.restart_button.pack(side="right", padx=(4, 0))
        self.restart_button.bind("<Button-1>", self.refresh_interface, add="+")
        self.refresh_tip = HoverTip(self.restart_button, REFRESH_TIP)
        # Compatibility alias for the restricted, non-header restart method and
        # its existing regression tests. The visible header action is refresh-only.
        self.restart_tip = self.refresh_tip
        self.top_resize_grip = self._rounded_button(
            header_actions,
            text="↘",
            bg=PANEL_SOFT,
            fg=WARM,
            outline=WARM,
            **_HDR_BTN,
        )
        try:
            self.top_resize_grip.configure(cursor="sizing")
        except tk.TclError:
            pass
        self.top_resize_grip.pack(side="right", padx=(0, 2))
        self.top_resize_grip.bind("<ButtonPress-1>", self._begin_resize, add="+")
        self.top_resize_grip.bind("<B1-Motion>", self._resize_window, add="+")
        self.top_resize_grip.bind("<ButtonRelease-1>", self._end_resize, add="+")
        self.top_resize_tip = HoverTip(self.top_resize_grip, RESIZE_TIP_TEXT)
        control_row = tk.Frame(header, bg=BG)
        control_row.pack(fill="x", pady=(4, 0))
        self.status = tk.Label(
            control_row,
            text="--:--",
            bg=PANEL_SOFT,
            fg=MUTED,
            padx=7,
            pady=4,
            font=(FONT_UI, 8),
        )
        self.status.pack(side="left")
        # Pixel cat "thinking" indicator — packed/animated only while inferring.
        self.pixel_cat = PixelCat(control_row, bg=BG)
        header_right = tk.Frame(control_row, bg=BG)
        header_right.pack(side="right")
        self.mode_button = ttk.Button(
            header_right,
            text="迷你", 
            style="Hero.TButton",
            command=self.toggle_details, 
            width=4,
        )
        self.mode_button.pack(side="right", padx=(0, 5))
        self.mode_tip = HoverTip(self.mode_button, DETAIL_TIP_TEXT)
        self.manual_button = self._rounded_button(
            header_right,
            text="手填",
            bg=WARM,
            fg=BUTTON_DARK_FG,
            outline=WARM,
            padx=8,
            pady=4,
        )
        self.manual_button.pack(side="right", padx=(0, 5))
        self.manual_button.bind("<Button-1>", self.toggle_manual_mode, add="+")
        self.manual_tip = HoverTip(self.manual_button, MANUAL_TIP_TEXT)
        self.theme_button = self._rounded_button(
            header_right,
            text="配色",
            bg=PANEL_SOFT,
            fg=ACCENT,
            padx=8,
            pady=4,
        )
        self.theme_button.pack(side="right", padx=(0, 5))
        self.theme_button.bind("<Button-1>", self._show_theme_menu, add="+")
        self.theme_tip = HoverTip(self.theme_button, f"{THEME_TIP_PREFIX}：暗色")
        self.map_button = self._rounded_button(
            header_right,
            text="地图",
            bg=PANEL_SOFT,
            fg=GOOD,
            padx=8,
            pady=4,
        )
        self.map_button.pack(side="right", padx=(0, 5))
        self.map_button.bind("<Enter>", self._show_minimap_popup, add="+")
        self.map_button.bind("<Leave>", self._schedule_hide_minimap_popup, add="+")
        self.map_button.bind("<Button-1>", self.toggle_pinned_minimap, add="+")
        self.export_diag_button = self._rounded_button(
            header_right,
            text="导出",
            bg=PANEL_SOFT,
            fg=WARN,
            padx=8,
            pady=4,
        )
        self.export_diag_button.pack(side="right", padx=(0, 5))
        self.export_diag_button.bind("<Button-1>", self.export_diagnostic_package, add="+")
        self.export_diag_tip = HoverTip(self.export_diag_button, _diagnostic_export_tip(self.snapshot_path))
        self.settlement_button = self._rounded_button(
            header_right,
            text="藏价",
            bg=PANEL_SOFT,
            fg=DIM,
            padx=8,
            pady=4,
        )
        self.settlement_button.pack(side="right", padx=(0, 5))
        self.settlement_button.bind("<Button-1>", self.toggle_settlement_values, add="+")
        self.settlement_tip = HoverTip(self.settlement_button, SETTLEMENT_HIDE_TIP)
        self._bind_drag(header, top_row, title_box, self.title, self.subtitle, control_row)

        # Keep the entry outside the chip container: _render_flags rebuilds its children.
        self.quote_status_row = tk.Frame(self.shell, bg=BG)
        self.quote_status_row.pack(fill="x", pady=(5, 4))
        self.quote_button = self._rounded_button(
            self.quote_status_row, text="报价", bg=PANEL_SOFT, fg=ACCENT,
            font=(FONT_UI, 7, "bold"), padx=6, pady=0, radius=3,
        )
        self.quote_button.pack(side="right", padx=(6, 0))
        self.quote_button.bind("<Button-1>", self._open_quote_preferences, add="+")
        self.quote_tip = HoverTip(self.quote_button, "报价设置：三档比例与出价上限")
        self.flags = tk.Frame(self.quote_status_row, bg=BG)
        self.flags.pack(side="left", fill="x", expand=True)

        prices = tk.Frame(self.shell, bg=BG)
        prices.pack(fill="x")
        self.price_labels: dict[str, tk.Label] = {}
        self.price_title_labels: dict[str, tk.Label] = {}
        self.default_price_titles = {
            "conservative": "保守",
            "balanced": "参考",
            "aggressive": "激进",
        }
        for idx, (key, label, color) in enumerate(
            (
                ("conservative", "保守", GOOD),
                ("balanced", "参考", WARN),
                ("aggressive", "激进", BAD),
            )
        ):
            card = self._card(prices, bg=PANEL, padx=7, pady=6)
            card.pack(side="left", fill="x", expand=True, padx=(0, 0 if idx == 2 else 4))
            tk.Frame(card, bg=color, height=2).pack(fill="x", pady=(0, 4))
            title_label = tk.Label(
                card,
                text=label,
                bg=PANEL,
                fg=MUTED,
                font=(FONT_UI, 7),
                anchor="w",
            )
            title_label.pack(fill="x")
            self.price_title_labels[key] = title_label
            price = tk.Label(
                card,
                text="-",
                bg=PANEL,
                fg=color,
                font=(FONT_NUMERIC, 15),
                anchor="w",
            )
            price.pack(fill="x", pady=(2, 0))
            self.price_labels[key] = price

        mid = tk.Frame(self.shell, bg=BG)
        mid.pack(fill="x", pady=(5, 0))
        self.quality_state_badges: dict[str, tk.Label] = {}
        self.red_count_triplet_frame: tk.Frame | None = None
        self.red_count_triplet_labels: dict[str, tk.Label] = {}
        self.red_rows = self._row_card(
            mid,
            "品质与概率",
            QUALITY_SUMMARY_ROW_ORDER,
            value_font_pt=SUMMARY_COMPACT_FONT_SIZE,
            state_badge_keys=frozenset(
                {"红件", "红格", "紫品", "金品", "白绿", "蓝品"}
            ),
        )
        self.action_rows = self._row_card(
            mid,
            "当前建议",
            (
                ("动作", "建议"),
                "最近",
                ("金红概率", "金/红率"),
                ("红/空格", "红+空"),
                ("候选", "总件"),
                "总格",
            ),
            value_font_pt=SUMMARY_COMPACT_FONT_SIZE,
        )
        self.red_rows["_card"].pack(side="left", fill="both", expand=True, padx=(0, 4))
        self.action_rows["_card"].pack(side="left", fill="both", expand=True)

        self.details_card = self._card(self.shell, bg=PANEL_MUTED, padx=6, pady=6)
        details_viewport = tk.Frame(self.details_card, bg=PANEL_MUTED)
        details_viewport.pack(fill="both", expand=True)
        details_viewport.columnconfigure(0, weight=1)
        details_viewport.rowconfigure(0, weight=1)
        self.details_scroll_canvas = tk.Canvas(
            details_viewport,
            height=360,
            bg=PANEL_MUTED,
            highlightthickness=0,
            borderwidth=0,
            relief="flat",
            yscrollincrement=18,
        )
        self.details_scrollbar = _slim_scrollbar(
            details_viewport,
            command=self.details_scroll_canvas.yview,
        )
        self.details_scroll_canvas.configure(
            yscrollcommand=self.details_scrollbar.set,
        )
        self.details_scroll_canvas.grid(row=0, column=0, sticky="nsew")
        self.details_scrollbar.grid(row=0, column=1, sticky="ns")
        details_grid = tk.Frame(self.details_scroll_canvas, bg=PANEL_MUTED)
        self.details_grid = details_grid
        self._details_scroll_window = self.details_scroll_canvas.create_window(
            (0, 0),
            window=details_grid,
            anchor="nw",
        )
        details_grid.bind(
            "<Configure>",
            self._sync_details_scroll_region,
            add="+",
        )
        self.details_scroll_canvas.bind(
            "<Configure>",
            self._sync_details_scroll_region,
            add="+",
        )
        self.root.bind_all(
            "<MouseWheel>",
            self._scroll_details_viewport,
            add="+",
        )
        self._build_manual_panel(details_grid)
        details_top = tk.Frame(details_grid, bg=PANEL_MUTED)
        self.details_top = details_top
        details_top.pack(fill="x", pady=(8, 0))
        if getattr(self, "details_composition_view_enabled", False):
            # “详情”优先承载估值与品质组合；手填是顶部“手填”按钮进入的独立工作流。
            # 保留原控件与状态机，只在普通详情态隐藏这张大表，避免它挤掉真正的详情信息。
            self.manual_card.pack_forget()
            self._build_details_composition_view(details_top)
        else:
            details_top.columnconfigure(0, weight=1, uniform="detail_cards")
            details_top.columnconfigure(1, weight=1, uniform="detail_cards")
            details_top.rowconfigure(0, weight=1)
            self.evidence_rows = self._row_card(
                details_top,
                "证据",
                (
                    "匹配",
                    "密度",
                    "输入",
                    "组合",
                    "最近",
                    "诊断",
                ),
            )
            self.detail_rows = self._row_card(
                details_top,
                "参考",
                (
                    "外援",
                    "决策",
                    "总格",
                    "总值",
                    "红值",
                    "结算",
                    "备注",
                ),
            )
            self.evidence_rows["_card"].grid(row=0, column=0, sticky="nsew", padx=(0, 4))
            self.detail_rows["_card"].grid(row=0, column=1, sticky="nsew")

        self.minimap_card = self._card(self.shell, bg=PANEL, padx=6, pady=6)
        minimap_header = tk.Frame(self.minimap_card, bg=PANEL)
        minimap_header.pack(fill="x", pady=(0, 5))
        self.minimap_title = tk.Label(
            minimap_header,
            text="小地图",
            bg=PANEL,
            fg=MUTED,
            font=(FONT_UI, 8, "bold"),
            anchor="w",
        )
        self.minimap_title.pack(side="left", fill="x", expand=True)
        self.minimap_counts = tk.Label(
            minimap_header,
            text="-",
            bg=PANEL,
            fg=MUTED,
            font=(FONT_UI, 7),
            anchor="e",
        )
        self.minimap_counts.pack(side="right")
        minimap_body = tk.Frame(self.minimap_card, bg=PANEL)
        minimap_body.pack(fill="x")
        minimap_canvas_frame = tk.Frame(minimap_body, bg=PANEL)
        minimap_canvas_frame.pack(side="left")
        self.minimap_canvas = tk.Canvas(
            minimap_canvas_frame,
            width=300,
            height=160,
            bg=MINIMAP_BG,
            highlightthickness=1,
            highlightbackground=BORDER,
        )
        self.minimap_scrollbar = _slim_scrollbar(
            minimap_canvas_frame,
            command=self.minimap_canvas.yview,
            hide_when_full=False,
        )
        self.minimap_canvas.configure(yscrollcommand=self.minimap_scrollbar.set)
        self.minimap_canvas.grid(row=0, column=0, sticky="nsew")
        self.minimap_scrollbar.grid(row=0, column=1, sticky="ns")
        minimap_canvas_frame.columnconfigure(0, weight=1)
        minimap_canvas_frame.rowconfigure(0, weight=1)
        self.minimap_canvas.bind("<MouseWheel>", self._scroll_detail_minimap, add="+")
        self._canvas_tip = HoverTip(self.minimap_canvas)
        minimap_meta = tk.Frame(minimap_body, bg=PANEL, padx=8)
        minimap_meta.pack(side="left", fill="both", expand=True)
        self.minimap_meta_rows = {
            "件数": self._meta_row(minimap_meta, "件数"),
            "来源": self._meta_row(minimap_meta, "来源"),
            "品质": self._meta_row(minimap_meta, "品质"),
            "范围": self._meta_row(minimap_meta, "范围"),
        }
        self.details_card.pack(fill="x", pady=(5, 0))
        self.footer_row = tk.Frame(self.shell, bg=BG)
        self.footer_row.pack(fill="x", pady=(1, 0))
        self.footer_meta_row = tk.Frame(self.footer_row, bg=BG)
        self.footer_actions_row = tk.Frame(self.footer_row, bg=BG)
        self.footer = tk.Label(
            self.footer_meta_row,
            text=f"{CREDIT_TEXT} · ",
            bg=BG,
            fg=DIM,
            font=(FONT_UI, 6),
            anchor="w",
        )
        self.footer.pack(side="left")
        # Keep the two external destinations in one explicit footer group.  The
        # group expands, while its children stay right-aligned and ordered, so
        # state refreshes and long header text cannot push either link into the
        # six-control header or reorder Bilibili / cloud data.
        self.footer_links = tk.Frame(self.footer_meta_row, bg=BG)
        self.footer_bilibili = tk.Label(
            self.footer_links,
            text="Bilibili",
            bg=BG,
            fg=ACCENT,
            activeforeground=WARM,
            font=(FONT_UI, 6, "underline"),
            anchor="w",
            padx=0,
        )
        try:
            self.footer_bilibili.configure(cursor="hand2")
        except tk.TclError:
            pass
        self.footer_bilibili.bind("<Button-1>", self._open_credit_bilibili, add="+")
        self.footer_bilibili_tip = HoverTip(
            self.footer_bilibili,
            BILIBILI_TIP_TEXT,
        )
        self.footer_cloud_big_data = tk.Label(
            self.footer_links,
            text=CLOUD_BIG_DATA_TEXT,
            bg=BG,
            fg=DIM,
            activebackground=PANEL_SOFT,
            activeforeground=ACCENT,
            disabledforeground=DIM,
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=BG,
            highlightcolor=ACCENT,
            font=(FONT_UI, 6),
            anchor="center",
            padx=1,
            pady=0,
            takefocus=True,
        )
        try:
            self.footer_cloud_big_data.configure(cursor="hand2")
        except tk.TclError:
            pass
        # Instance bindings deliberately stay separate from the Bilibili label
        # and use add="+" so HoverTip/class/external bindings remain intact.
        self.footer_cloud_big_data.bind(
            "<ButtonRelease-1>",
            self._open_cloud_big_data,
            add="+",
        )
        self.footer_cloud_big_data.bind(
            "<Enter>", self._activate_cloud_big_data_hover, add="+"
        )
        self.footer_cloud_big_data.bind(
            "<Leave>", self._deactivate_cloud_big_data_hover, add="+"
        )
        self.footer_cloud_big_data.bind("<Return>", self._open_cloud_big_data, add="+")
        self.footer_cloud_big_data.bind("<space>", self._open_cloud_big_data, add="+")
        self.footer_cloud_big_data_tip = HoverTip(
            self.footer_cloud_big_data,
            CLOUD_BIG_DATA_TIP_TEXT,
        )
        # Allocate from the original left edge in visual order.  Bilibili gets
        # its natural parcel first, so even an unexpectedly long cloud label
        # can only consume the remaining width instead of hiding Bilibili.
        self.footer_bilibili.pack(side="left")
        self.footer_cloud_big_data.pack(side="left", padx=(4, 0))
        self.footer_links.pack(side="left", fill="x", expand=True)
        self._stabilize_footer_link_height()
        self.resize_grip = tk.Label(
            self.footer_actions_row,
            text="◢",
            bg=BG,
            fg=BORDER,
            font=(FONT_UI, 8, "bold"),
            padx=2,
            highlightthickness=0,
        )
        try:
            self.resize_grip.configure(cursor="sizing")
        except tk.TclError:
            pass
        self.resize_grip.pack(side="right")
        self.resize_grip.bind("<ButtonPress-1>", self._begin_resize, add="+")
        self.resize_grip.bind("<B1-Motion>", self._resize_window, add="+")
        self.resize_grip.bind("<ButtonRelease-1>", self._end_resize, add="+")
        self.resize_grip_tip = HoverTip(
            self.resize_grip,
            FOOTER_RESIZE_TIP_TEXT,
        )

        # Support-author "feed the cat" mascot (bottom-right). A lying cat idles
        # gently and perks up at settlement. Hover the CAT for the 文案 (text), hover
        # the CAN for the Alipay QR — keeps the corner clean (just cat + can) by default.
        # Click the CAN to feed -> both mascots react.  The footer CAT is the stable,
        # low-risk launcher for the independent desktop companion; its richer random
        # interactions move to that companion so the main mini UI never needs to hide.
        self.support_frame = tk.Frame(self.footer_actions_row, bg=BG)
        self.pet_launcher = self._card(
            self.support_frame,
            bg=PANEL_SOFT,
            outline="#e4b067",
            radius=5,
            padx=3,
            pady=1,
            fit_width=True,
        )
        self.pet_launcher_row = tk.Frame(self.pet_launcher, bg=PANEL_SOFT)
        self.pet_launcher_row.pack(anchor="center")
        self.support_cat = LyingCat(self.pet_launcher_row, bg=PANEL_SOFT)
        self.support_cat.pack(side="left")
        self.pet_launcher_timer = tk.Label(
            self.pet_launcher_row,
            text="",
            bg=PANEL_SOFT,
            fg="#9a5a1b",
            font=(FONT_NUMERIC, 7, "bold"),
            anchor="center",
            padx=3,
        )
        self.pet_launcher_timer.pack(side="left")
        self.pet_launcher_dot = tk.Canvas(
            self.pet_launcher_row,
            width=7,
            height=7,
            bg=PANEL_SOFT,
            highlightthickness=0,
            bd=0,
        )
        self.pet_launcher_dot.pack(side="left", padx=(1, 0))
        self.pet_launcher_progress = tk.Canvas(
            self.pet_launcher,
            width=PET_LAUNCHER_PROGRESS_MIN_WIDTH,
            height=2,
            bg=PANEL_SOFT,
            highlightthickness=0,
            bd=0,
        )
        self._pet_launcher_progress_ratio = 0.0
        self._pet_launcher_progress_color = WARM
        self.pet_launcher_progress.bind(
            "<Configure>",
            self._redraw_pet_launcher_progress,
            add="+",
        )
        self.pet_launcher_progress.pack(fill="x", pady=(1, 0))
        self.pet_launcher.master._round_card["fixed_height"] = 30
        self.pet_launcher.pack(side="left", padx=(0, 4))
        self.support_can = self._make_feed_can(self.support_frame)
        if not self.network_diagnostic_cohort:
            self.support_can.pack(side="left")
        self.support_frame.pack(side="right", padx=(6, 4))
        self._footer_groups_wrapped = False
        self.footer_actions_row.pack(side="right")
        self.footer_meta_row.pack(side="left", fill="x", expand=True)
        self.footer_row.bind("<Configure>", self._layout_footer_groups, add="+")
        self.pet_launcher_tip = HoverTip(self.pet_launcher, PET_TIP_OFF)
        self.support_feed_tip = self.pet_launcher_tip
        self.support_can.bind("<Enter>", self._show_support_qr, add="+")
        self.support_can.bind("<Leave>", self._schedule_hide_support_qr, add="+")
        self.support_can.bind("<Button-1>", self._feed_cat, add="+")  # click to feed (fun)
        self._pet_launcher_targets = (
            self.pet_launcher,
            self.pet_launcher_row,
            self.support_cat,
            self.pet_launcher_timer,
            self.pet_launcher_dot,
            self.pet_launcher_progress,
        )
        for _w in self._pet_launcher_targets:
            _w.bind("<Button-1>", self.toggle_pet_companion, add="+")
            _w.bind("<Button-3>", self._show_pet_menu, add="+")
            if _w is not self.pet_launcher:
                _w.bind("<Enter>", self.pet_launcher_tip._enter, add="+")
                _w.bind("<Leave>", self.pet_launcher_tip.hide, add="+")
                _w.bind("<Motion>", self.pet_launcher_tip._motion, add="+")
        for _w in (*self._pet_launcher_targets, self.support_can):
            try:
                _w.configure(cursor="hand2")
            except tk.TclError:
                pass
        self.support_cat.start()
        self._start_cat_idle()          # 低频自发闲置(摸摸/饿等·作者0709)

        self._bind_drag_recursive(
            self.outer,
            exclude={
                self.close_button,
                self.topmost_button,
                self.restart_button,
                self.mode_button,
                self.manual_button,
                self.theme_button,
                self.map_button,
                self.settlement_button,
                self.export_diag_button,
                self.top_resize_grip,
                self.resize_grip,
                self.footer_bilibili,
                self.footer_cloud_big_data,
                self.pet_launcher,
            },
        )
        self._set_topmost_button_state()
        self._scaled_layout_specs = [
            (self.shell, {"padx": 9, "pady": 9}),
        ]
        self._capture_ui_font_bases(self.outer)
        if self._ui_prefs_enabled and self._load_ui_prefs():
            pass
        else:
            self._set_details_mode(False)
        self._apply_pending_window_position()
        if self._pet_restore_pending:
            self.root.after_idle(self._restore_pet_companion)
        root.protocol("WM_DELETE_WINDOW", self._on_user_close)
        self._start_ui_health_watchdog()
        self._start_ref_engine_liveness_warmup()
        if self._load_existing_snapshot:
            self.refresh()
        else:
            self._last_signature = self._snapshot_signature()
            self.render_missing("等待新局实时包")
            self.root.after(self.interval_ms, self.refresh)
        self.root.after_idle(self._settle_initial_layout)
        if self._ui_prefs_enabled and not self._pet_discovery_seen:
            self._pet_discovery_after_id = self.root.after(
                1200,
                self._show_pet_discovery_tip,
            )
        # Paint the entitlement state synchronously so a warm start cannot briefly
        # expose the trial timer for permanent users before the first delayed tick.
        self._refresh_pet_access_ui()

    def _details_geometry(self) -> str:
        if self._custom_details_size is not None:
            width, height = self._custom_details_size
            return f"{width}x{height}"
        width, height = self._details_content_size()
        return f"{width}x{height}"

    def _details_content_size(self, *, width: int | None = None) -> tuple[int, int]:
        work_w, work_h = self._screen_size()
        self._sync_details_viewport_height()
        self.root.update_idletasks()
        if width is None:
            width, _ = self._current_window_size()
        requested_w = int(self.outer.winfo_reqwidth()) if hasattr(self, "outer") else int(width)
        width = min(760, max(430, int(width), requested_w + 12, int(work_w * 0.44)))
        width = min(width, max(430, work_w - 40))
        height = self._measure_window_height_for_width(width)
        height = min(height, max(320, work_h - 72))
        return width, height

    def _fit_details_window_to_content(self, *, width: int | None = None) -> None:
        if not hasattr(self, "root"):
            return
        if width is None:
            width, _ = self._current_window_size()
        width = max(430, min(1200, int(width)))
        # Apply details typography before measuring so height matches final scale.
        self._ui_scale = compute_ui_scale(
            width,
            DETAILS_BASE_HEIGHT,
            base_width=DETAILS_BASE_WIDTH,
            base_height=DETAILS_BASE_HEIGHT,
        )
        self._apply_ui_scale()
        self.root.update_idletasks()
        self._custom_details_size = None
        fitted_width, fitted_height = self._details_content_size(width=width)
        self._apply_window_geometry(f"{fitted_width}x{fitted_height}", flush=False)
        self._sync_ui_scale_from_window(force=True)
        self.root.update_idletasks()
        refined_width, refined_height = self._details_content_size(width=fitted_width)
        if abs(refined_height - fitted_height) > 4:
            self._apply_window_geometry(f"{refined_width}x{refined_height}")

    def _load_ui_prefs(self) -> bool:
        prefs = read_ui_prefs(self._ui_prefs_path)
        if prefs is None:
            return False
        self._apply_ui_prefs_payload(prefs)
        return True

    def _resolve_pending_pet_preferences(
        self,
        state: PetAccessState | None = None,
    ) -> str:
        pending = getattr(self, "_pending_pet_preferences", None)
        if not isinstance(pending, dict):
            return "none"
        access = state or self._current_pet_access()
        if access.can_persist_preferences:
            self._pet_color = normalize_pet_color(pending.get("color"))
            self._pet_position = normalize_pet_position(pending.get("position"))
            saved_settlement_feedback = pending.get(
                "settlement_feedback_enabled",
                True,
            )
            self._pet_settlement_feedback_enabled = (
                saved_settlement_feedback
                if isinstance(saved_settlement_feedback, bool)
                else True
            )
            self._pet_enabled = (
                bool(pending.get("enabled"))
                and pet_assets_available()
            )
            self._pet_restore_pending = self._pet_enabled
            self._pending_pet_preferences = None
            return "applied"
        if access.status == "checking" or (
            access.reason == "entitlement_pending"
            and access.plan_kind is None
        ):
            return "waiting"
        self._pending_pet_preferences = None
        return "cleared"

    def _apply_ui_prefs_payload(self, prefs: dict[str, Any]) -> None:
        mini_size = prefs.get("custom_mini_size")
        if isinstance(mini_size, (list, tuple)) and len(mini_size) == 2:
            self._custom_mini_size = (int(mini_size[0]), int(mini_size[1]))
        mini_scale = prefs.get("mini_ui_scale")
        if isinstance(mini_scale, (int, float)) and math.isfinite(float(mini_scale)):
            self._custom_mini_scale = max(
                UI_SCALE_MIN,
                min(UI_SCALE_MAX, float(mini_scale)),
            )
        details_size = prefs.get("custom_details_size")
        if isinstance(details_size, (list, tuple)) and len(details_size) == 2:
            self._custom_details_size = (int(details_size[0]), int(details_size[1]))
        position = prefs.get("window_position")
        if isinstance(position, (list, tuple)) and len(position) == 2:
            try:
                self._pending_window_position = (int(position[0]), int(position[1]))
            except (TypeError, ValueError):
                self._pending_window_position = None
        self._pet_intro_seen = bool(prefs.get("pet_intro_seen"))
        self._pet_discovery_seen = bool(prefs.get("pet_discovery_seen"))
        (
            self._pet_touch_date,
            self._pet_touch_today,
            self._pet_touch_total,
        ) = normalize_pet_touch_state(
            prefs.get("pet_touch_date"),
            prefs.get("pet_touch_today"),
            prefs.get("pet_touch_total"),
        )
        self._pet_trial_seconds_remaining = normalize_pet_trial_seconds(
            prefs.get("pet_trial_seconds_remaining"),
            legacy_started_at=prefs.get("pet_trial_started_at"),
            legacy_expires_at=prefs.get("pet_trial_expires_at"),
        )
        pending = prefs.get("pet_preferences")
        self._pending_pet_preferences = (
            dict(pending)
            if isinstance(pending, dict)
            else None
        )
        self._resolve_pending_pet_preferences()
        theme_name = str(prefs.get("theme_name") or "warm")
        if theme_name and theme_name != self.theme_name:
            self.apply_theme(theme_name)
        expanded = bool(prefs.get("details_expanded"))
        self._set_details_mode(expanded)

    def _apply_pending_window_position(self) -> None:
        pending = self._pending_window_position
        if pending is None:
            return
        width, height = self._current_window_size()
        pending_x, pending_y = int(pending[0]), int(pending[1])
        # A per-user prefs file survives package upgrades and monitor changes. Preserve valid
        # negative coordinates when a left-hand display still exists, but ask Windows for the
        # nearest *current* monitor when the saved display was disconnected and clamp the whole
        # window into that work area. Without this startup gate Tk happily maps a responsive,
        # taskbar-visible window entirely off-screen (real repro: x=-2035 after removing a monitor).
        left, top, right, bottom = _monitor_work_area_for_point(
            self.root,
            pending_x + width // 2,
            pending_y + height // 2,
        )
        final_x, final_y = _clamp_rect_into_workarea(
            pending_x,
            pending_y,
            width,
            height,
            left,
            top,
            right,
            bottom,
        )
        self.root.geometry(f"{width}x{height}+{final_x}+{final_y}")
        self.root.update_idletasks()
        self._pending_window_position = None

    def _settle_initial_layout(self) -> None:
        """Re-fit the window after the first content render.

        UI prefs restore details/mini geometry during __init__ before refresh()
        has populated the cards, so the layout is measured while empty. Once the
        real content is rendered the stored size no longer matches and the bottom
        cards can overlap the panel above until the user manually resizes. This
        one-shot pass runs after the first render and once the window is mapped,
        re-fitting (growing height to fit content when needed) and reflowing the
        layout without moving the window.
        """
        if not hasattr(self, "root"):
            return
        try:
            if not self.root.winfo_exists():
                return
            self.root.update_idletasks()
        except tk.TclError:
            return
        if self.details_expanded:
            self._sync_ui_scale_from_window(force=True)
            self.root.update_idletasks()
            width, height = self._current_window_size()
            needed_h = self._measure_window_height_for_width(width, probe_height=height)
            _, work_h = self._screen_size()
            final_h = min(max(height, needed_h), max(360, work_h - 72))
            self._apply_window_geometry(f"{width}x{final_h}")
            if self._custom_details_size is not None:
                self._custom_details_size = (width, final_h)
            self.root.update_idletasks()
        else:
            width, _ = self._current_window_size()
            self._apply_mini_resize_layout(width, finalize=True)

    def _collect_ui_prefs_payload(self) -> dict[str, Any]:
        width, height = self._current_window_size()
        geo = str(self.root.geometry() or "")
        window_position: list[int] | None = None
        if "+" in geo:
            parts = geo.split("+")
            if len(parts) >= 3:
                try:
                    window_position = [int(parts[1]), int(parts[2])]
                except ValueError:
                    window_position = None
        pet_position = self._pet_position
        companion = getattr(self, "_pet_companion", None)
        if companion is not None:
            try:
                pet_position = companion.position()
            except (AttributeError, tk.TclError):
                pass
        (
            self._pet_touch_date,
            self._pet_touch_today,
            self._pet_touch_total,
        ) = normalize_pet_touch_state(
            self._pet_touch_date,
            self._pet_touch_today,
            self._pet_touch_total,
        )
        access = self._current_pet_access()
        pet_preferences: dict[str, Any] | None = None
        if access.can_persist_preferences:
            pet_preferences = {
                "enabled": bool(self._pet_enabled),
                "color": normalize_pet_color(self._pet_color),
                "settlement_feedback_enabled": bool(
                    getattr(self, "_pet_settlement_feedback_enabled", True)
                ),
                "position": (
                    list(pet_position)
                    if pet_position is not None
                    else None
                ),
            }
        elif (
            access.status == "checking"
            or (
                access.reason == "entitlement_pending"
                and access.plan_kind is None
            )
        ) and isinstance(self._pending_pet_preferences, dict):
            # Do not erase an existing premium preference while the signed
            # entitlement is still unresolved. Never create this block for a
            # free/short-plan user.
            pet_preferences = dict(self._pending_pet_preferences)
        payload = {
            "schema_version": UI_PREFS_SCHEMA_VERSION,
            "theme_name": str(getattr(self, "theme_name", "warm") or "warm"),
            "details_expanded": bool(self.details_expanded),
            "ui_scale": round(float(self._ui_scale), 4),
            "pet_intro_seen": bool(self._pet_intro_seen),
            "pet_discovery_seen": bool(self._pet_discovery_seen),
            "pet_touch_date": self._pet_touch_date,
            "pet_touch_today": int(self._pet_touch_today),
            "pet_touch_total": int(self._pet_touch_total),
            "pet_trial_seconds_remaining": self._pet_trial_seconds_remaining,
            "mini_ui_scale": (
                round(float(self._custom_mini_scale), 4)
                if self._custom_mini_scale is not None
                else None
            ),
            "window_position": window_position,
            "custom_mini_size": list(self._custom_mini_size) if self._custom_mini_size else None,
            "custom_details_size": list(self._custom_details_size) if self._custom_details_size else None,
            "window_size": [width, height],
        }
        if pet_preferences is not None:
            payload["pet_preferences"] = pet_preferences
        return payload

    def _save_ui_prefs_if_enabled(self) -> None:
        if not getattr(self, "_ui_prefs_enabled", False):
            return
        self._ui_prefs_save_after_id = None
        root = getattr(self, "root", None)
        if root is None:
            return
        try:
            if not root.winfo_exists():
                return
        except tk.TclError:
            return
        try:
            write_ui_prefs(self._ui_prefs_path, self._collect_ui_prefs_payload())
        except (OSError, tk.TclError):
            pass

    def _schedule_ui_prefs_save(self) -> None:
        if not getattr(self, "_ui_prefs_enabled", False) or not hasattr(self, "root"):
            return
        if self._ui_prefs_save_after_id is not None:
            try:
                self.root.after_cancel(self._ui_prefs_save_after_id)
            except tk.TclError:
                pass
        self._ui_prefs_save_after_id = self.root.after(
            UI_PREFS_SAVE_DEBOUNCE_MS,
            self._save_ui_prefs_if_enabled,
        )

    def _estimate_mini_content_height(self, *, flush: bool = True) -> int:
        if flush:
            self.root.update_idletasks()
        return compute_fitted_mini_height(int(self.shell.winfo_reqheight()))

    def _geometry_position_suffix(self) -> str:
        geo = str(self.root.geometry() or "")
        if "+" not in geo:
            return ""
        return "+" + geo.split("+", 1)[1]

    @contextmanager
    def _atomic_layout_update(self):
        """Expose only the final frame of a synchronous multi-step layout change.

        The context is nest-safe because opening/closing manual mode can call the details toggle.
        Test doubles and non-Windows platforms keep the same layout semantics without native calls.
        """
        depth = int(getattr(self, "_atomic_layout_depth", 0))
        if depth == 0:
            root = getattr(self, "root", None)
            self._atomic_layout_redraw_suspended = bool(
                root is not None and _set_windows_redraw(root, enabled=False)
            )
        self._atomic_layout_depth = depth + 1
        try:
            yield
        finally:
            remaining = max(0, int(getattr(self, "_atomic_layout_depth", 1)) - 1)
            self._atomic_layout_depth = remaining
            if remaining == 0:
                suspended = bool(getattr(self, "_atomic_layout_redraw_suspended", False))
                self._atomic_layout_redraw_suspended = False
                if suspended:
                    root = getattr(self, "root", None)
                    if root is not None:
                        try:
                            # Finish Tk geometry propagation while native painting is still frozen.
                            root.update_idletasks()
                        except (AttributeError, tk.TclError):
                            pass
                        _set_windows_redraw(root, enabled=True)

    def _apply_window_geometry(self, size: str, *, flush: bool = True) -> None:
        self.root.geometry(f"{size}{self._geometry_position_suffix()}")
        if flush:
            self.root.update_idletasks()
            self._refresh_window_rounding()

    def _set_cat_window_icon(self, root: tk.Tk) -> None:
        """Use the mascot cat as the window / taskbar / Alt-Tab icon, built from the LyingCat pixel
        sprite on a transparent background (no image asset). Kept as an attribute so the PhotoImage
        isn't garbage-collected (Tk only holds a weak ref)."""
        try:
            rows = LyingCat._BODY
            pal = LyingCat._PALETTE
            scale = 4
            side = max(len(rows), max(len(r) for r in rows))  # square canvas, cat centered
            pad_x = (side - max(len(r) for r in rows)) // 2
            pad_y = (side - len(rows)) // 2
            img = tk.PhotoImage(width=side * scale, height=side * scale)
            for r, row in enumerate(rows):
                for c, ch in enumerate(row):
                    color = pal.get(ch)
                    if not color:
                        continue
                    x0 = (c + pad_x) * scale
                    y0 = (r + pad_y) * scale
                    img.put(color, to=(x0, y0, x0 + scale, y0 + scale))
            self._cat_window_icon = img  # keep a strong ref alive
            root.iconphoto(True, img)
        except Exception:
            pass

    def _refresh_window_rounding(self) -> None:
        """Re-clip the borderless overlay to rounded corners after a resize. The overlay is now
        borderless in BOTH floating and taskbar modes (taskbar presence comes from WS_EX_APPWINDOW,
        not from dropping the border), so rounding always applies."""
        _apply_rounded_window_region(self.root, radius=WINDOW_CORNER_RADIUS)

    def _on_root_configure_round(self, event: tk.Event[Any]) -> None:
        # Only the root toplevel's own resize matters; ignore child <Configure> bubbles.
        if event.widget is not self.root:
            return
        size = (int(event.width), int(event.height))
        if size == self._last_rounding_size:
            return
        self._last_rounding_size = size
        if getattr(self, "_resize_active", False):
            # SetWindowRgn forces a synchronous redraw. Re-clipping every pointer event makes the
            # grip trail the cursor; release restores the exact rounded region once.
            return
        self._refresh_window_rounding()

    def _refit_header_title_wrap(self, _event: tk.Event[Any] | None = None) -> None:
        """Wrap a long title inside the parcel left by identity and actions."""
        try:
            available = int(self._header_title_box.winfo_width())
            identity = getattr(self, "_header_identity", None)
            if identity is not None and identity.winfo_ismapped():
                available -= int(identity.winfo_reqwidth()) + 6
            if available > 0:
                self.title.configure(wraplength=available)
        except tk.TclError:
            pass

    def _compute_status_path(self) -> Path:
        return self.snapshot_path.parent / COMPUTE_STATUS_FILENAME

    @staticmethod
    def _safe_compute_timestamps(values: Any) -> dict[str, float]:
        if not isinstance(values, dict):
            return {}
        safe: dict[str, float] = {}
        for key in (
            "wss_frame_received",
            "parser_recognized",
            "monitor_state_committed",
            "semantic_recognized",
            "monitor_inference_started",
            "monitor_inference_completed",
            "snapshot_published",
            "overlay_detected",
            "indicator_visible",
            "worker_submitted",
            "summary_completed",
            "render_applied",
        ):
            value = values.get(key)
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                safe[key] = round(float(value), 6)
        return safe

    def _compute_latency_diagnostics(self) -> dict[str, Any]:
        timestamps = self._safe_compute_timestamps(
            getattr(self, "_compute_latency_timestamps", {})
        )
        pairs = {
            "capture_to_semantic": ("wss_frame_received", "semantic_recognized"),
            "capture_to_parser": ("wss_frame_received", "parser_recognized"),
            "parser_to_state_commit": ("parser_recognized", "monitor_state_committed"),
            "state_commit_to_semantic": ("monitor_state_committed", "semantic_recognized"),
            "semantic_to_snapshot_publish": ("semantic_recognized", "snapshot_published"),
            "publish_to_ui_detect": ("snapshot_published", "overlay_detected"),
            "detect_to_indicator": ("overlay_detected", "indicator_visible"),
            "detect_to_worker_submit": ("overlay_detected", "worker_submitted"),
            "compute": ("worker_submitted", "summary_completed"),
            "result_to_render": ("summary_completed", "render_applied"),
        }
        segments_ms: dict[str, float] = {}
        for name, (start_key, end_key) in pairs.items():
            start = timestamps.get(start_key)
            end = timestamps.get(end_key)
            if start is not None and end is not None and end >= start:
                segments_ms[name] = round((end - start) * 1000.0, 3)
        return {
            "event": str(getattr(self, "_compute_status_event", "idle")),
            "pending": bool(getattr(self, "_compute_pending", False)),
            "trace_seq": max(0, int(getattr(self, "_compute_pending_trace_seq", 0))),
            "timestamps_monotonic": timestamps,
            "segments_ms": segments_ms,
        }

    def _mark_compute_timestamp(self, key: str, value: float | None = None) -> None:
        if key not in {
            "overlay_detected",
            "indicator_visible",
            "worker_submitted",
            "summary_completed",
            "render_applied",
        }:
            return
        timestamps = getattr(self, "_compute_latency_timestamps", None)
        if not isinstance(timestamps, dict):
            timestamps = {}
            self._compute_latency_timestamps = timestamps
        timestamp = time.monotonic() if value is None else float(value)
        if math.isfinite(timestamp):
            timestamps[key] = timestamp

    def _set_compute_pending(
        self,
        event: str,
        *,
        trace_seq: int | None = None,
        timestamps: Any = None,
    ) -> None:
        current_seq = max(0, int(getattr(self, "_compute_pending_trace_seq", 0)))
        terminal_seq = max(-1, int(getattr(self, "_compute_terminal_trace_seq", -1)))
        if type(trace_seq) is not int or trace_seq < 0:  # noqa: E721 - reject bool sequence
            trace_seq = current_seq if bool(getattr(self, "_compute_pending", False)) else current_seq + 1
        # A rendered/terminal trace is monotonic: delayed sidecar progress for
        # that trace must not re-light an already completed indicator.
        if trace_seq < current_seq or trace_seq <= terminal_seq:
            return
        incoming = self._safe_compute_timestamps(timestamps)
        if trace_seq > current_seq:
            self._compute_latency_timestamps = incoming
            self._compute_indicator_visible_seq = 0
            self._compute_indicator_idle_scheduled_seq = 0
        else:
            current = getattr(self, "_compute_latency_timestamps", None)
            if not isinstance(current, dict):
                current = {}
                self._compute_latency_timestamps = current
            current.update(incoming)
        self._compute_pending_trace_seq = trace_seq
        self._compute_pending = True
        self._compute_status_event = str(event)
        current_timestamps = getattr(self, "_compute_latency_timestamps", {})
        if "overlay_detected" not in current_timestamps:
            self._mark_compute_timestamp("overlay_detected")
        self._sync_compute_indicator()

    def _clear_compute_pending(self, event: str, *, rendered: bool = False) -> None:
        if rendered:
            self._mark_compute_timestamp("render_applied")
        current_seq = max(0, int(getattr(self, "_compute_pending_trace_seq", 0)))
        terminal_seq = max(-1, int(getattr(self, "_compute_terminal_trace_seq", -1)))
        self._compute_terminal_trace_seq = max(terminal_seq, current_seq)
        self._compute_pending = False
        self._compute_status_event = str(event)
        self._sync_compute_indicator()

    def _poll_compute_status(self) -> None:
        status_path = self._compute_status_path()
        try:
            if not status_path.is_file():
                return
        except OSError:
            return
        payload = _read_json(status_path)
        if not isinstance(payload, dict) or payload.get("schema_version") != COMPUTE_STATUS_SCHEMA_VERSION:
            return
        event = payload.get("event")
        trace_seq = payload.get("trace_seq")
        counters = payload.get("counters")
        snapshot_seq = counters.get("snapshot_seq") if isinstance(counters, dict) else None
        if type(trace_seq) is not int or trace_seq < 0 or not isinstance(event, str):  # noqa: E721 - reject bool sequence
            return
        signature = (
            trace_seq,
            event,
            snapshot_seq if type(snapshot_seq) is int else None,  # noqa: E721 - reject bool
            bool(payload.get("compute_pending")),
        )
        if signature == getattr(self, "_last_compute_status_signature", None):
            return
        self._last_compute_status_signature = signature
        if payload.get("compute_pending") is True and event in {
            "semantic_pending",
            "snapshot_published",
        }:
            self._set_compute_pending(
                event,
                trace_seq=trace_seq,
                timestamps=payload.get("timestamps_monotonic"),
            )
            self._record_ui_runtime_status(f"compute_{event}")
            return
        if event in {"reset", "stopped"}:
            if event == "reset":
                # A restarted monitor begins a new trace generation at zero.
                self._compute_monitor_generation = max(
                    0,
                    int(getattr(self, "_compute_monitor_generation", 0)),
                ) + 1
                # A queued snapshot from the previous generation must not be
                # launched after its in-flight worker finishes.
                self._summary_worker_pending = None
                self._compute_terminal_trace_seq = -1
            self._compute_pending_trace_seq = trace_seq
            self._compute_latency_timestamps = self._safe_compute_timestamps(
                payload.get("timestamps_monotonic")
            )
            self._clear_compute_pending(f"compute_{event}")
            self._record_ui_runtime_status(f"compute_{event}")
            return
        if event in {
            "partial",
            "error",
            "degraded",
            "skipped_bootstrap",
        } and trace_seq >= int(getattr(self, "_compute_pending_trace_seq", 0)):
            self._compute_pending_trace_seq = trace_seq
            self._clear_compute_pending(f"compute_{event}")
            self._record_ui_runtime_status(f"compute_{event}")

    def _mark_compute_indicator_visible(self, trace_seq: int) -> None:
        if (
            not bool(getattr(self, "_compute_pending", False))
            or trace_seq != int(getattr(self, "_compute_pending_trace_seq", 0))
            or trace_seq == int(getattr(self, "_compute_indicator_visible_seq", 0))
        ):
            return
        cat = getattr(self, "pixel_cat", None)
        try:
            if cat is None or not cat.winfo_ismapped():
                return
        except (tk.TclError, AttributeError):
            return
        self._compute_indicator_visible_seq = trace_seq
        self._mark_compute_timestamp("indicator_visible")
        self._record_ui_runtime_status("compute_indicator_visible")

    def _sync_compute_indicator(self) -> None:
        # A worker is an implementation detail, not proof that the latest
        # monitor trace still expects a render. Every legitimate dispatch path
        # sets compute_pending first; terminal/error/reset events can therefore
        # stop the indicator even while a superseded worker winds down.
        active = bool(getattr(self, "_compute_pending", False))
        if active:
            if hasattr(self, "status"):
                try:
                    self.status.configure(text=self._inference_status_text())
                except (tk.TclError, AttributeError):
                    pass
            self._show_pixel_cat()
        else:
            self._hide_pixel_cat()

    def _show_pixel_cat(self) -> None:
        cat = getattr(self, "pixel_cat", None)
        if cat is None:
            return
        try:
            cat.set_background(self.status.cget("bg"))
            if not cat.winfo_ismapped():
                cat.pack(side="left", padx=(5, 0), after=self.status)
            cat.start()
            trace_seq = int(getattr(self, "_compute_pending_trace_seq", 0))
            if (
                trace_seq > 0
                and trace_seq != int(getattr(self, "_compute_indicator_idle_scheduled_seq", 0))
            ):
                self._compute_indicator_idle_scheduled_seq = trace_seq
                try:
                    self.root.after_idle(
                        lambda seq=trace_seq: self._mark_compute_indicator_visible(seq)
                    )
                except (tk.TclError, AttributeError):
                    pass
        except tk.TclError:
            pass
        glow = getattr(self, "border_glow", None)
        if glow is not None:
            try:
                glow.refresh_background()
                glow.start()
            except tk.TclError:
                pass

    def _hide_pixel_cat(self) -> None:
        cat = getattr(self, "pixel_cat", None)
        if cat is not None:
            try:
                cat.stop()
                if cat.winfo_ismapped():
                    cat.pack_forget()
            except tk.TclError:
                pass
        glow = getattr(self, "border_glow", None)
        if glow is not None:
            try:
                glow.stop()
            except tk.TclError:
                pass

    def _make_feed_can(self, parent: tk.Widget) -> tk.Canvas:
        """A tiny drawn cat-food can (the hover target for the support QR)."""
        px, w, h = 2, 7, 9
        cv = tk.Canvas(parent, width=w * px, height=h * px, bg=BG, highlightthickness=0, bd=0)
        lid = _blend_hex(WARM, "#ffffff", 0.45)
        cv.create_rectangle(1 * px, 0, (w - 1) * px, 2 * px, fill=lid, width=0)          # lid
        cv.create_rectangle(1 * px, 1 * px, (w - 1) * px, (h - 1) * px, fill=WARM, width=0)  # body
        cv.create_rectangle(1 * px, 4 * px, (w - 1) * px, 6 * px, fill=PANEL, width=0)    # label band
        cv.create_rectangle(2 * px, 4 * px + 1, 4 * px, 5 * px + 1, fill=WARM, width=0)   # paw mark
        return cv

    def _support_qr_image(self) -> Any:
        if getattr(self, "_support_qr_photo", None) is not None:
            return self._support_qr_photo
        if getattr(self, "_support_qr_failed", False):
            return None
        try:
            from PIL import Image, ImageTk

            im = Image.open(SUPPORT_QR_IMAGE_PATH)
            im.thumbnail((160, 160))
            self._support_qr_photo = ImageTk.PhotoImage(im)
            return self._support_qr_photo
        except Exception:
            pass
        # Fallback without Pillow: Tk 8.6+ reads PNG natively. The packaged portable
        # build does NOT bundle Pillow, so without this the QR popup silently no-ops
        # for players (only a local dev with PIL ever saw it). Integer subsample keeps
        # the QR modules crisp (no smooth resize needed).
        try:
            photo = tk.PhotoImage(file=SUPPORT_QR_IMAGE_PATH)
            factor = max(1, max(photo.width(), photo.height()) // 200)
            if factor > 1:
                photo = photo.subsample(factor, factor)
            self._support_qr_photo = photo
            return self._support_qr_photo
        except Exception:
            self._support_qr_failed = True
            return None

    def _show_support_qr(self, event: tk.Event[Any] | None = None) -> None:
        self._cancel_hide_support_qr()
        photo = self._support_qr_image()
        if photo is None:
            return
        pop = getattr(self, "_support_qr_popup", None)
        if pop is None:
            pop = tk.Toplevel(self.root)
            pop.overrideredirect(True)
            pop.attributes("-topmost", True)  # else it hides behind the topmost overlay
            _apply_windows_toolwindow(pop, enabled=not self.show_taskbar)
            pop.configure(bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
            self._support_qr_label = tk.Label(pop, image=photo, bg=PANEL, bd=0)
            self._support_qr_label.pack(padx=6, pady=(6, 2))
            tk.Label(pop, text="扫码投喂 · 谢谢老板~", bg=PANEL, fg=MUTED, font=(FONT_UI, 8)).pack(pady=(0, 6))
            self._support_qr_popup = pop
        else:
            self._support_qr_label.configure(image=photo)
            pop.deiconify()
        pop.update_idletasks()
        pw, ph = pop.winfo_reqwidth(), pop.winfo_reqheight()
        try:
            anchor = self.support_frame
            x = anchor.winfo_rootx() + anchor.winfo_width() - pw
            y = anchor.winfo_rooty() - ph - 6
        except tk.TclError:
            x, y = 200, 200
        pop.geometry(f"+{max(0, int(x))}+{max(0, int(y))}")
        pop.lift()

    def _cancel_hide_support_qr(self) -> None:
        aid = getattr(self, "_support_qr_hide_after", None)
        if aid is not None:
            try:
                self.root.after_cancel(aid)
            except tk.TclError:
                pass
            self._support_qr_hide_after = None

    def _schedule_hide_support_qr(self, event: tk.Event[Any] | None = None) -> None:
        self._cancel_hide_support_qr()
        try:
            self._support_qr_hide_after = self.root.after(200, self._hide_support_qr)
        except tk.TclError:
            pass

    def _hide_support_qr(self) -> None:
        pop = getattr(self, "_support_qr_popup", None)
        if pop is not None:
            try:
                pop.withdraw()
            except tk.TclError:
                pass

    def _pet_work_area_for_point(self, x: int, y: int) -> tuple[int, int, int, int]:
        return _monitor_work_area_for_point(self.root, int(x), int(y))

    def _default_pet_position(self) -> tuple[int, int]:
        try:
            self.root.update_idletasks()
            root_x = int(self.root.winfo_x())
            root_y = int(self.root.winfo_y())
            root_w = max(1, int(self.root.winfo_width()))
            root_h = max(1, int(self.root.winfo_height()))
        except (AttributeError, tk.TclError, TypeError, ValueError):
            root_x, root_y, root_w, root_h = 20, 20, 510, 450
        work_area = self._pet_work_area_for_point(
            root_x + root_w // 2,
            root_y + root_h // 2,
        )
        left, top, right, bottom = work_area
        gap = 12
        right_candidate = (root_x + root_w + gap, root_y + max(0, root_h - PET_WINDOW_SIZE))
        left_candidate = (root_x - PET_WINDOW_SIZE - gap, right_candidate[1])
        if right_candidate[0] + PET_WINDOW_SIZE <= right:
            candidate = right_candidate
        elif left_candidate[0] >= left:
            candidate = left_candidate
        else:
            candidate = (right - PET_WINDOW_SIZE - 20, bottom - PET_WINDOW_SIZE - 20)
        return _clamp_rect_into_workarea(
            candidate[0],
            candidate[1],
            PET_WINDOW_SIZE,
            PET_WINDOW_SIZE,
            left,
            top,
            right,
            bottom,
        )

    def _create_pet_companion(self) -> PetCompanionOverlay | None:
        if self._pet_companion is not None:
            return self._pet_companion
        if not pet_assets_available():
            self._update_pet_tooltip(unavailable=True)
            return None
        position = self._pet_position or self._default_pet_position()
        try:
            self._pet_companion = PetCompanionOverlay(
                self.root,
                color=self._pet_color,
                position=position,
                topmost=bool(getattr(self, "topmost_enabled", True)),
                work_area_for_point=self._pet_work_area_for_point,
                on_click=self._interact_with_pet_companion,
                on_context_menu=lambda x, y: self._show_pet_menu(x_root=x, y_root=y),
                on_position_changed=self._on_pet_position_changed,
                on_hide_requested=lambda: self._set_pet_companion_enabled(False),
                apply_toolwindow=lambda window: _apply_windows_toolwindow(window, enabled=True),
                on_visibility_changed=self._on_pet_companion_visibility_changed,
            )
        except (OSError, RuntimeError, tk.TclError):
            self._pet_companion = None
        return self._pet_companion

    def _restore_pet_companion(self) -> None:
        if not self._pet_enabled:
            self._pet_restore_pending = False
            return
        access = self._current_pet_access()
        if access.status == "checking":
            self._pet_restore_pending = True
            try:
                self.root.after(500, self._restore_pet_companion)
            except tk.TclError:
                pass
            return
        self._pet_restore_pending = False
        self._set_pet_companion_enabled(True, restoring=True)

    def _current_pet_access(self, *, now: int | None = None) -> PetAccessState:
        return pet_access_from_activation(
            getattr(self, "_act_state", {}),
            trial_seconds_remaining=getattr(
                self,
                "_pet_trial_seconds_remaining",
                None,
            ),
            now=now,
            preview=getattr(self, "_pet_access_preview", None),
            preview_seconds_remaining=getattr(
                self,
                "_pet_preview_seconds_remaining",
                None,
            ),
        )

    def _set_controlled_pet_preview_phase(self, phase: str) -> None:
        if getattr(self, "_pet_access_preview", None) is None:
            return
        state = self._current_pet_access()
        if not state.is_metered:
            return
        normalized_phase = str(phase or "").strip().lower()
        if int(state.seconds_left or 0) <= 0:
            normalized_phase = "exhausted"
        if state.usage_kind == "quota" and state.duration_days == 30:
            key = {
                "available": "quota30",
                "active": "quota30_active",
                "paused": "quota30_paused",
                "exhausted": "quota30_exhausted",
            }.get(normalized_phase)
        elif state.usage_kind == "quota":
            key = {
                "available": "quota60",
                "active": "quota60_active",
                "paused": "quota60_paused",
                "exhausted": "quota60_exhausted",
            }.get(normalized_phase)
        else:
            key = {
                "available": "free",
                "active": "trial",
                "paused": "trial_paused",
                "exhausted": "expired",
            }.get(normalized_phase)
        if key is not None:
            self._pet_access_preview = key

    def _consume_controlled_pet_preview_time(self) -> None:
        if getattr(self, "_pet_access_preview", None) is None:
            return
        tick = getattr(self, "_pet_preview_tick_monotonic", None)
        if (
            tick is None
            or not bool(getattr(self, "_pet_enabled", False))
            or not self._is_pet_companion_visible()
        ):
            return
        state = self._current_pet_access()
        if state.status not in {"trial_active", "quota_active"}:
            self._pet_preview_tick_monotonic = None
            return
        current = time.monotonic()
        elapsed = max(0, int(current - float(tick)))
        if elapsed <= 0:
            return
        remaining = max(0, int(state.seconds_left or 0) - elapsed)
        self._pet_preview_seconds_remaining = remaining
        self._pet_preview_tick_monotonic = float(tick) + elapsed
        if remaining <= 0:
            self._pet_preview_tick_monotonic = None
            self._set_controlled_pet_preview_phase("exhausted")

    def _notify_pet_usage_event(
        self,
        event: str,
        state: PetAccessState,
    ) -> bool:
        handler = getattr(self, "_pet_usage_event_handler", None)
        if handler is None:
            return False
        try:
            return bool(handler(str(event), state))
        except Exception:  # noqa: BLE001 - Pet metering must never break the calculator
            return False

    @staticmethod
    def _pet_usage_authority_marker(state: Any) -> str | None:
        if type(state) is not dict:
            return None
        usage = dict.get(state, "feature_usage")
        if type(usage) is not dict:
            return None
        pet = dict.get(usage, "pet_companion")
        if type(pet) is not dict:
            return None
        marker = dict.get(pet, "entitlement_id")
        if (
            type(marker) is not str
            or len(marker) != 64
            or any(
                not ("0" <= char <= "9" or "a" <= char <= "f")
                for char in marker
            )
        ):
            return None
        return marker

    @staticmethod
    def _pet_usage_event_acknowledged(result: Any, event: dict[str, Any]) -> bool:
        usage = getattr(result, "feature_usage", None)
        if type(usage) is not dict:
            return False
        pet = dict.get(usage, "pet_companion")
        if type(pet) is not dict:
            return False
        status = dict.get(pet, "status")
        return (
            type(status) is str
            and status in {"active", "paused", "exhausted"}
            and type(dict.get(pet, "last_seq")) is int
            and dict.get(pet, "last_seq") == dict.get(event, "seq")
            and type(dict.get(pet, "visible")) is bool
            and dict.get(pet, "visible") is dict.get(event, "visible")
        )

    def _queue_product_pet_usage_event(
        self,
        event: str,
        state: PetAccessState,
    ) -> bool:
        """Queue one A1 visibility event without doing network I/O on Tk."""
        if (
            type(event) is not str
            or event not in {"start", "visible", "hidden"}
            or type(state) is not PetAccessState
            or not state.is_metered
        ):
            return False
        marker = self._pet_usage_authority_marker(
            getattr(self, "_act_state", None)
        )
        if marker is None:
            return False
        try:
            from bidking_lab.secure import enforce

            authority = enforce.pet_usage_request_authority_snapshot()
        except Exception:  # noqa: BLE001 - local authority failure is Pet-only
            return False
        if authority is None:
            return False
        previous_authority = getattr(
            self,
            "_pet_usage_authority_snapshot",
            None,
        )
        identity_changed = previous_authority != authority
        if marker != self._pet_usage_identity_marker or identity_changed:
            self._pet_usage_identity_marker = marker
            self._pet_usage_session_id = uuid.uuid4().hex
            self._pet_usage_next_seq = 0
        self._pet_usage_authority_snapshot = authority
        seq = self._pet_usage_next_seq
        self._pet_usage_next_seq += 1
        request = {
            "session_id": self._pet_usage_session_id,
            "seq": seq,
            "visible": event != "hidden",
        }
        self._pet_usage_request_queue.put((marker, authority, request))
        if not self._pet_usage_worker_running:
            self._pet_usage_worker_running = True
            threading.Thread(
                target=self._run_product_pet_usage_worker,
                daemon=True,
                name="hero-ref-pet-usage",
            ).start()
        return True

    def _run_product_pet_usage_worker(self) -> None:
        """Serialize Pet usage requests; retry only transport failures."""
        try:
            from bidking_lab.secure import enforce
        except Exception:  # noqa: BLE001 - Pet failure must not break the calculator
            self._pet_usage_worker_running = False
            return
        while True:
            _marker, authority, event = self._pet_usage_request_queue.get()
            transient_failures = 0
            try:
                while True:
                    current_authority = (
                        enforce.pet_usage_request_authority_snapshot()
                    )
                    if current_authority != authority:
                        break
                    result = enforce.refresh_pet_usage(
                        event,
                        expected_authority=authority,
                    )
                    if self._pet_usage_event_acknowledged(result, event):
                        break
                    if not enforce.is_transient_activation_failure(result):
                        break
                    transient_failures += 1
                    time.sleep(
                        enforce.activation_refresh_delay(
                            result,
                            transient_failures,
                        )
                    )
                    current_authority = (
                        enforce.pet_usage_request_authority_snapshot()
                    )
                    if current_authority != authority:
                        break
            except Exception:  # noqa: BLE001 - keep Pet isolated from calculator
                pass
            finally:
                self._pet_usage_request_queue.task_done()

    def _is_pet_companion_visible(self) -> bool:
        companion = getattr(self, "_pet_companion", None)
        window = getattr(companion, "window", None)
        if window is None:
            return False
        try:
            generation = companion.visibility_generation
            if not companion.visibility_commit_is_current(generation):
                return False
            window.update_idletasks()
            return bool(
                companion is getattr(self, "_pet_companion", None)
                and companion.visibility_generation == generation
                and companion.visibility_commit_is_current(generation)
            )
        except (AttributeError, tk.TclError):
            return False

    def _set_pet_usage_visibility(
        self,
        visible: bool,
        state: PetAccessState,
    ) -> bool:
        if not state.is_metered:
            self._pet_usage_visible_reported = False
            self._pet_usage_hidden_reported = False
            return True
        if visible:
            if getattr(self, "_pet_access_preview", None) is not None:
                self._set_controlled_pet_preview_phase("active")
                self._pet_preview_tick_monotonic = time.monotonic()
                return True
            if self._pet_usage_visible_reported:
                return True
            accepted = self._notify_pet_usage_event("visible", state)
            self._pet_usage_visible_reported = accepted
            if accepted:
                self._pet_usage_hidden_reported = False
            return accepted
        self._consume_controlled_pet_preview_time()
        self._pet_preview_tick_monotonic = None
        if getattr(self, "_pet_access_preview", None) is not None:
            if int(self._current_pet_access().seconds_left or 0) > 0:
                self._set_controlled_pet_preview_phase("paused")
            return True
        should_report_hidden = self._pet_usage_visible_reported
        if should_report_hidden:
            self._pet_usage_hidden_reported = self._notify_pet_usage_event(
                "hidden",
                state,
            )
        self._pet_usage_visible_reported = False
        return True

    def _close_pet_access_dialog(self) -> None:
        dialog = getattr(self, "_pet_access_dialog", None)
        self._pet_access_dialog = None
        if dialog is not None:
            try:
                dialog.destroy()
            except tk.TclError:
                pass

    def _position_pet_access_dialog(self, dialog: tk.Toplevel) -> None:
        dialog.update_idletasks()
        width = max(350, int(dialog.winfo_reqwidth()))
        height = max(210, int(dialog.winfo_reqheight()))
        root_x = int(self.root.winfo_x())
        root_y = int(self.root.winfo_y())
        root_w = max(1, int(self.root.winfo_width()))
        root_h = max(1, int(self.root.winfo_height()))
        left, top, right, bottom = _monitor_work_area_for_point(
            self.root,
            root_x + root_w // 2,
            root_y + root_h // 2,
        )
        x = root_x + root_w + 12
        if x + width > right:
            x = root_x - width - 12
        x, y = _clamp_rect_into_workarea(
            x,
            root_y + max(0, root_h - height),
            width,
            height,
            left,
            top,
            right,
            bottom,
        )
        dialog.geometry(f"{width}x{height}+{x}+{y}")

    def _open_pet_plan_dialog(self) -> None:
        self._close_pet_access_dialog()
        self._open_token_dialog()

    def _start_pet_usage_from_dialog(self) -> None:
        state = self._current_pet_access()
        if not state.can_start_usage or self._pet_usage_start_pending:
            return
        if getattr(self, "_pet_access_preview", None) is not None:
            self._pet_preview_seconds_remaining = int(state.seconds_left or 0)
            self._set_controlled_pet_preview_phase("active")
            self._close_pet_access_dialog()
            self._set_pet_companion_enabled(True)
            self._refresh_pet_access_ui(schedule_next=False)
            return
        usage_event = "visible" if state.status.endswith("_paused") else "start"
        if self._notify_pet_usage_event(usage_event, state):
            self._pet_usage_start_pending = True
            self._close_pet_access_dialog()
            self._update_pet_tooltip()
            return
        self._show_pet_access_dialog(
            PetAccessState(
                "entitlement_pending",
                False,
                None,
                "usage_start_pending",
                state.plan_kind,
                state.usage_kind,
                state.total_seconds,
            )
        )

    def _show_pet_access_dialog(self, state: PetAccessState | None = None) -> None:
        state = state or self._current_pet_access()
        self._close_pet_access_dialog()
        dialog = tk.Toplevel(self.root)
        dialog.title("桌面猫猫权限")
        dialog.withdraw()
        dialog.overrideredirect(True)
        dialog.configure(bg=WARM)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.attributes("-topmost", bool(self.topmost_enabled))
        dialog.protocol("WM_DELETE_WINDOW", self._close_pet_access_dialog)
        dialog.bind("<Escape>", lambda _event: self._close_pet_access_dialog(), add="+")
        self._pet_access_dialog = dialog
        shell = tk.Frame(dialog, bg=PANEL, padx=16, pady=13)
        shell.pack(fill="both", expand=True, padx=1, pady=1)

        balance_text = ""
        if state.status == "trial_available":
            title = "体验桌面猫猫"
            detail = (
                "免费试用可获得一次 10 分钟猫猫体验。\n"
                "只有桌面猫真实显示时计量；收回猫猫或退出软件后暂停。"
            )
            status_text = "尚未开始 · 需要确认后才会启用"
            status_color = GOOD
            balance_text = format_pet_access_clock(state)
        elif state.status == "quota_available":
            title = pet_access_tier_label(state)
            detail = (
                f"{pet_access_contract_text(state)}。\n"
                "只有桌面猫真实显示时计量；余额以服务端签名结果为准。"
            )
            status_text = "额度可用 · 需要确认后才会启用"
            status_color = GOOD
            balance_text = format_pet_access_clock(state)
        elif state.status in {"trial_active", "trial_paused"}:
            title = "10 分钟猫猫体验"
            detail = (
                f"{pet_access_contract_text(state)}；桌面猫显示时计量。\n"
                "收回猫猫或退出软件后暂停；"
                "当前余额以服务端签名结果为准。"
            )
            status_text = "正在计量" if state.status == "trial_active" else "已暂停"
            status_color = GOOD if state.status == "trial_active" else WARN
            balance_text = format_pet_access_clock(state)
        elif state.status in {"quota_active", "quota_paused"}:
            title = pet_access_tier_label(state)
            detail = (
                f"{pet_access_contract_text(state)}；桌面猫显示时计量。\n"
                "收回猫猫或退出软件后暂停；"
                "当前余额以服务端签名结果为准。"
            )
            status_text = "正在计量" if state.status == "quota_active" else "已暂停"
            status_color = GOOD if state.status == "quota_active" else WARN
            balance_text = format_pet_access_clock(state)
        elif state.status in {"trial_exhausted", "quota_exhausted"}:
            title = (
                f"{pet_access_tier_label(state)}已用完"
                if state.status == "quota_exhausted"
                else "猫猫体验已结束"
            )
            detail = (
                "本份服务端猫猫计时额度已经耗尽。"
                if state.status == "quota_exhausted"
                else "一次性 10 分钟体验已经结束。"
            )
            status_text = "已耗尽 · 核心报价功能不受影响"
            status_color = WARM
            balance_text = format_pet_access_clock(state)
        elif state.status == "full":
            title = pet_access_tier_label(state)
            detail = (
                f"{pet_access_contract_text(state)}。\n"
                "猫猫状态随服务端签名主权益到期或续期同步。"
            )
            status_text = "权益有效 · 不扣减猫猫计时额度"
            status_color = GOOD
        elif state.status == "checking":
            title = "正在确认猫猫权限"
            detail = "正在读取当前授权状态，请稍候再试。"
            status_text = "检测期间不会计量或错误开放权限"
            status_color = MUTED
        elif state.status == "entitlement_pending":
            title = "猫猫权益待同步"
            detail = (
                "正在等待服务端返回签名的套餐与猫猫余额。\n"
                "客户端不会根据套餐名、剩余天数或本地偏好猜测权限。"
            )
            status_text = "权益同步中 · 不等于已耗尽"
            status_color = WARN
        elif state.status == "state_error":
            title = "猫猫权益暂不可用"
            detail = (
                "猫猫权益数据缺失、格式异常或暂时无法验证。\n"
                "本次保持关闭；报价、抓包和主界面不受影响。"
            )
            status_text = "状态异常 · 已安全关闭猫猫"
            status_color = BAD
        else:
            title = "此套餐暂未包含猫猫"
            detail = (
                "当前签名权益没有可用的猫猫体验、配额或完整权限。\n"
                "可查看订阅方案；核心报价功能继续正常使用。"
            )
            status_text = "核心报价功能不受影响"
            status_color = MUTED

        topbar = tk.Frame(shell, bg=PANEL)
        topbar.pack(fill="x")
        tk.Label(
            topbar,
            text="桌面猫猫  ·  PET",
            bg=PANEL,
            fg=WARM,
            font=(FONT_UI, 7, "bold"),
            anchor="w",
        ).pack(side="left")
        close = tk.Label(
            topbar,
            text="×",
            bg=PANEL,
            fg=MUTED,
            activeforeground=BAD,
            font=(FONT_UI, 11, "bold"),
            padx=4,
            cursor="hand2",
        )
        close.pack(side="right")
        close.bind("<Button-1>", lambda _event: self._close_pet_access_dialog(), add="+")
        tk.Frame(shell, bg=WARM, height=2).pack(fill="x", pady=(7, 11))
        tk.Label(
            shell,
            text=title,
            bg=PANEL,
            fg=TEXT,
            font=(FONT_UI, 12, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            shell,
            text=detail,
            bg=PANEL,
            fg=MUTED,
            font=(FONT_UI, 8),
            justify="left",
            anchor="w",
            wraplength=330,
        ).pack(fill="x", pady=(5, 0))

        status_card = tk.Frame(
            shell,
            bg=PANEL_SOFT,
            padx=10,
            pady=7,
            highlightthickness=1,
            highlightbackground=BORDER,
        )
        status_card.pack(fill="x", pady=(11, 0))
        tk.Label(
            status_card,
            text=status_text,
            bg=PANEL_SOFT,
            fg=status_color,
            font=(FONT_UI, 8, "bold"),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        if balance_text:
            tk.Label(
                status_card,
                text=balance_text,
                bg=PANEL_SOFT,
                fg=WARM if state.is_metered else TEXT,
                font=(FONT_NUMERIC, 10, "bold"),
                anchor="e",
            ).pack(side="right", padx=(10, 0))
        tk.Label(
            shell,
            text=PET_ACCESS_FULL_TEXT,
            bg=PANEL,
            fg=GOOD,
            font=(FONT_UI, 8, "bold"),
            anchor="w",
        ).pack(fill="x", pady=(8, 0))

        actions = tk.Frame(shell, bg=PANEL)
        actions.pack(fill="x", pady=(12, 0))
        dismiss = self._rounded_button(
            actions,
            text="暂不",
            bg=PANEL_SOFT,
            fg=TEXT,
            outline=BORDER,
            padx=12,
            pady=5,
            font=(FONT_UI, 8),
            radius=8,
        )
        dismiss.pack(side="right")
        dismiss.configure(cursor="hand2")
        dismiss.bind("<Button-1>", lambda _event: self._close_pet_access_dialog(), add="+")
        if state.can_start_usage:
            primary = self._rounded_button(
                actions,
                text=(
                    "继续显示猫猫"
                    if state.status.endswith("_paused")
                    else (
                        f"启用{pet_access_tier_label(state)}"
                        if state.usage_kind == "quota"
                        else "开始 10 分钟体验"
                    )
                ),
                bg=WARM,
                fg=BUTTON_DARK_FG,
                outline=WARM,
                padx=12,
                pady=5,
                font=(FONT_UI, 8, "bold"),
                radius=8,
            )
            primary.pack(side="right", padx=(0, 8))
            primary.configure(cursor="hand2")
            primary.bind(
                "<Button-1>",
                lambda _event: self._start_pet_usage_from_dialog(),
                add="+",
            )
        elif state.status not in {
            "checking",
            "entitlement_pending",
            "trial_active",
            "trial_paused",
            "quota_active",
            "quota_paused",
            "full",
        }:
            primary = self._rounded_button(
                actions,
                text="查看订阅方案",
                bg=GOOD,
                fg=BUTTON_DARK_FG,
                outline=GOOD,
                padx=12,
                pady=5,
                font=(FONT_UI, 8, "bold"),
                radius=8,
            )
            primary.pack(side="right", padx=(0, 8))
            primary.configure(cursor="hand2")
            primary.bind(
                "<Button-1>",
                lambda _event: self._open_pet_plan_dialog(),
                add="+",
            )
        self._position_pet_access_dialog(dialog)
        dialog.deiconify()
        dialog.lift()

    def _refresh_pet_access_ui(self, *, schedule_next: bool = True) -> None:
        self._pet_access_after_id = None
        self._consume_controlled_pet_preview_time()
        previous_state = getattr(self, "_last_pet_access_state", None)
        state = self._current_pet_access()
        previous_status = getattr(self, "_last_pet_access_status", None)
        opening_after_start = False
        if (
            isinstance(previous_state, PetAccessState)
            and previous_state.is_metered
            and not state.is_metered
            and (
                self._pet_usage_visible_reported
                or self._pet_usage_hidden_reported
            )
        ):
            self._set_pet_usage_visibility(False, previous_state)
            self._pet_usage_visible_reported = False
            self._pet_usage_hidden_reported = False
        self._last_pet_access_state = state
        self._last_pet_access_status = state.status
        preference_resolution = self._resolve_pending_pet_preferences(state)
        if preference_resolution == "applied" and self._pet_restore_pending:
            try:
                self.root.after_idle(self._restore_pet_companion)
            except tk.TclError:
                self._pet_restore_pending = False
        elif preference_resolution == "cleared":
            self._schedule_ui_prefs_save()
        if self._pet_usage_start_pending and state.status in {
            "trial_active",
            "trial_paused",
            "quota_active",
            "quota_paused",
            "full",
            "state_error",
            "locked",
            "trial_exhausted",
            "quota_exhausted",
        }:
            self._pet_usage_start_pending = False
            if state.status in {
                "trial_active",
                "trial_paused",
                "quota_active",
                "quota_paused",
            }:
                opening_after_start = True
                try:
                    self.root.after_idle(self._set_pet_companion_enabled, True)
                except tk.TclError:
                    pass
        if not state.allowed and (
            bool(getattr(self, "_pet_enabled", False))
            or bool(getattr(self, "_pet_visibility_pending", False))
        ):
            self._set_pet_companion_enabled(False)
        elif state.is_metered and not bool(
            getattr(self, "_pet_visibility_pending", False)
        ) and not bool(
            getattr(getattr(self, "_pet_companion", None), "visibility_pending", False)
        ):
            if self._pet_enabled and self._is_pet_companion_visible():
                self._set_pet_usage_visibility(True, state)
            elif (
                state.status in {"trial_active", "quota_active"}
                and not opening_after_start
            ):
                self._set_pet_usage_visibility(False, state)
        if (
            previous_status in {
                "trial_active",
                "trial_paused",
                "quota_active",
                "quota_paused",
            }
            and state.status in {"trial_exhausted", "quota_exhausted"}
        ):
            self._show_pet_access_dialog(state)
        self._update_pet_tooltip()
        if schedule_next:
            try:
                self._pet_access_after_id = self.root.after(
                    1000,
                    self._refresh_pet_access_ui,
                )
            except tk.TclError:
                self._pet_access_after_id = None

    def _on_pet_companion_visibility_changed(
        self,
        ready: bool,
        request_token: Any,
        visibility_generation: Any,
    ) -> bool:
        companion = getattr(self, "_pet_companion", None)
        if companion is None:
            return False
        pending = bool(getattr(self, "_pet_visibility_pending", False))
        expected = int(getattr(self, "_pet_visibility_generation", 0))
        if type(visibility_generation) is not int:
            return False
        if request_token is not None and (
            type(request_token) is not int or request_token != expected or not pending
        ):
            return False
        try:
            if companion.visibility_generation != visibility_generation:
                return False
        except (AttributeError, tk.TclError):
            return False
        if not ready:
            if request_token is not None:
                self._pet_visibility_pending = False
            if bool(getattr(self, "_pet_enabled", False)):
                self._set_pet_usage_visibility(False, self._current_pet_access())
                self._pet_enabled = False
            elif request_token is not None and getattr(
                self,
                "_pet_access_preview",
                None,
            ) is not None:
                self._set_pet_usage_visibility(False, self._current_pet_access())
            self._update_pet_tooltip(unavailable=True)
            self._schedule_ui_prefs_save()
            return True

        try:
            if not companion.visibility_commit_is_current(visibility_generation):
                return False
            companion.window.update_idletasks()
            if (
                companion is not getattr(self, "_pet_companion", None)
                or companion.visibility_generation != visibility_generation
                or not companion.visibility_commit_is_current(visibility_generation)
            ):
                return False
        except (AttributeError, tk.TclError):
            return False

        if request_token is None:
            if not self._pet_enabled:
                return False
            self._set_pet_usage_visibility(True, self._current_pet_access())
            return True

        restoring = bool(getattr(self, "_pet_visibility_restoring", False))
        access = self._current_pet_access()
        if not access.allowed:
            self._pet_enabled = False
            self._pet_visibility_pending = False
            if getattr(self, "_pet_access_preview", None) is not None:
                self._set_pet_usage_visibility(False, access)
            try:
                companion.hide()
            except (AttributeError, tk.TclError):
                pass
            self._update_pet_tooltip(unavailable=not access.allowed)
            self._schedule_ui_prefs_save()
            return False
        self._pet_enabled = True
        if not self._set_pet_usage_visibility(True, access):
            self._pet_enabled = False
            self._pet_visibility_pending = False
            try:
                companion.hide()
            except (AttributeError, tk.TclError):
                pass
            if not restoring:
                self._show_pet_access_dialog(
                    PetAccessState(
                        "entitlement_pending",
                        False,
                        None,
                        "usage_visibility_pending",
                        access.plan_kind,
                        access.usage_kind,
                        access.total_seconds,
                    )
                )
            self._update_pet_tooltip()
            self._schedule_ui_prefs_save()
            return False
        if (
            companion is not getattr(self, "_pet_companion", None)
            or expected != getattr(self, "_pet_visibility_generation", -1)
            or companion.visibility_generation != visibility_generation
            or not companion.visibility_commit_is_current(visibility_generation)
        ):
            self._pet_enabled = False
            return False
        self._pet_visibility_pending = False
        if not restoring:
            (
                self._pet_touch_date,
                self._pet_touch_today,
                self._pet_touch_total,
            ) = normalize_pet_touch_state(
                self._pet_touch_date,
                self._pet_touch_today,
                self._pet_touch_total,
            )
            first_intro = not self._pet_intro_seen
            self._pet_intro_seen = True
            summary = (
                f"今日撸猫 {self._pet_touch_today} 次 · "
                f"累计 {self._pet_touch_total} 次"
            )
            bubble_text = f"{PET_INTRO_TEXT}\n{summary}" if first_intro else summary

            def _show_open_summary(
                target: PetCompanionOverlay = companion,
                text: str = bubble_text,
                generation: int = expected,
            ) -> None:
                if (
                    generation == getattr(self, "_pet_visibility_generation", -1)
                    and target is getattr(self, "_pet_companion", None)
                    and self._pet_enabled
                    and self._is_pet_companion_visible()
                ):
                    target.show_bubble(text, duration_ms=2800)

            try:
                self.root.after(180, _show_open_summary)
            except tk.TclError:
                pass
        self._update_pet_tooltip()
        self._schedule_ui_prefs_save()
        return True

    def _set_pet_companion_enabled(self, enabled: bool, *, restoring: bool = False) -> None:
        requested = bool(enabled)
        if requested and not restoring:
            self._mark_pet_discovery_seen()
        if requested:
            access = self._current_pet_access()
            if not access.allowed:
                if not restoring:
                    self._show_pet_access_dialog(access)
                else:
                    self._pet_enabled = False
                self._update_pet_tooltip()
                self._schedule_ui_prefs_save()
                return
            if bool(getattr(self, "_pet_visibility_pending", False)):
                return
            if self._pet_enabled and self._is_pet_companion_visible():
                self._set_pet_usage_visibility(True, access)
                self._update_pet_tooltip()
                return
        companion = self._pet_companion
        if requested:
            companion = self._create_pet_companion()
            if companion is None:
                self._pet_enabled = False
                self._update_pet_tooltip(unavailable=True)
                self._schedule_ui_prefs_save()
                return
            companion.set_color(self._pet_color)
            if self._pet_position is not None:
                companion.set_position(*self._pet_position)
            self._pet_visibility_generation += 1
            self._pet_visibility_pending = True
            self._pet_visibility_restoring = bool(restoring)
            self._pet_enabled = False
            companion.show(
                visibility_token=self._pet_visibility_generation,
            )
        else:
            self._pet_visibility_generation += 1
            self._pet_visibility_pending = False
            access = self._current_pet_access()
            self._set_pet_usage_visibility(False, access)
            self._pet_enabled = False
            if companion is not None:
                self._pet_position = companion.position()
                companion.hide()
        self._update_pet_tooltip()
        self._schedule_ui_prefs_save()

    def toggle_pet_companion(self, _event: tk.Event[Any] | None = None) -> str:
        self._mark_pet_discovery_seen()
        self._set_pet_companion_enabled(
            not (
                bool(self._pet_enabled)
                or bool(getattr(self, "_pet_visibility_pending", False))
            )
        )
        return "break"

    def _hide_pet_discovery_tip(self) -> None:
        self._pet_discovery_after_id = None
        self._pet_discovery_pulse_on = False
        self._update_pet_launcher_state()

    def _mark_pet_discovery_seen(self) -> None:
        after_id = getattr(self, "_pet_discovery_after_id", None)
        if after_id is not None:
            try:
                self.root.after_cancel(after_id)
            except (AttributeError, tk.TclError):
                pass
            self._pet_discovery_after_id = None
        self._pet_discovery_seen = True
        self._hide_pet_discovery_tip()
        self._schedule_ui_prefs_save()

    def _advance_pet_discovery_pulse(self) -> None:
        if bool(getattr(self, "_pet_discovery_seen", False)):
            self._hide_pet_discovery_tip()
            return
        step = max(0, int(getattr(self, "_pet_discovery_pulse_step", 0)))
        if step >= 4:
            self._pet_discovery_seen = True
            self._hide_pet_discovery_tip()
            self._schedule_ui_prefs_save()
            return
        self._pet_discovery_pulse_on = step % 2 == 0
        self._pet_discovery_pulse_step = step + 1
        self._update_pet_launcher_state()
        try:
            self._pet_discovery_after_id = self.root.after(
                650,
                self._advance_pet_discovery_pulse,
            )
        except tk.TclError:
            self._pet_discovery_after_id = None

    def _show_pet_discovery_tip(self) -> None:
        self._pet_discovery_after_id = None
        if bool(getattr(self, "_pet_discovery_seen", False)):
            return
        if getattr(self, "pet_launcher", None) is None:
            return
        self._pet_discovery_pulse_step = 0
        self._advance_pet_discovery_pulse()

    def _redraw_pet_launcher_progress(
        self,
        _event: tk.Event[Any] | None = None,
    ) -> None:
        progress = getattr(self, "pet_launcher_progress", None)
        if progress is None:
            return
        ratio = max(
            0.0,
            min(1.0, float(getattr(self, "_pet_launcher_progress_ratio", 0.0))),
        )
        color = str(
            getattr(
                self,
                "_pet_launcher_progress_color",
                WARM,  # noqa: F821 - theme globals are rebound by apply_theme
            )
        )
        try:
            width = max(1, int(progress.winfo_width()))
            if width <= 1:
                width = max(1, int(progress.cget("width")))
            progress.delete("all")
            progress.create_rectangle(
                0,
                0,
                width,
                2,
                fill=BORDER,  # noqa: F821 - theme globals are rebound by apply_theme
                width=0,
            )
            filled = max(0, min(width, int(round(width * ratio))))
            if filled > 0:
                progress.create_rectangle(
                    0,
                    0,
                    filled,
                    2,
                    fill=color,
                    width=0,
                )
        except (TypeError, ValueError, tk.TclError):
            pass

    def _sync_pet_launcher_card(self, holder: tk.Canvas) -> None:
        try:
            holder.update_idletasks()
        except tk.TclError:
            return
        self._sync_rounded_card(holder)

    @staticmethod
    def _pet_launcher_timer_pack_padding(
        timer: tk.Label,
        timer_text: str,
    ) -> tuple[int, int]:
        if not timer_text or re.fullmatch(r"\d{2}(?::\d{2}){1,2}", timer_text) is None:
            return (0, 0)
        try:
            font = tkfont.Font(root=timer, font=timer.cget("font"))
            widest_digit = max("0123456789", key=font.measure)
            template = ":".join(
                widest_digit * 2 for _part in timer_text.split(":")
            )
            extra = max(0, int(font.measure(template)) - int(font.measure(timer_text)))
        except (TypeError, ValueError, tk.TclError):
            return (0, 0)
        return (extra // 2, extra - (extra // 2))

    def _update_pet_launcher_state(self, *, unavailable: bool = False) -> None:
        launcher = getattr(self, "pet_launcher", None)
        timer = getattr(self, "pet_launcher_timer", None)
        dot = getattr(self, "pet_launcher_dot", None)
        progress = getattr(self, "pet_launcher_progress", None)
        cat = getattr(self, "support_cat", None)
        if launcher is None or timer is None or dot is None or progress is None:
            return
        access = self._current_pet_access()
        timer_text = ""
        ratio = 0.0
        dot_color = MUTED
        total_seconds = int(access.total_seconds or 0)
        if access.is_metered:
            timer_text = format_pet_access_clock(access)
            if total_seconds > 0:
                ratio = max(
                    0.0,
                    min(1.0, float(access.seconds_left or 0) / total_seconds),
                )
        if unavailable:
            bg = PANEL_SOFT
            fg = DIM
            border = BORDER
            progress_color = DIM
        elif self._pet_enabled:
            bg = _blend_hex(PANEL, WARM, 0.16)
            fg = TEXT
            border = WARM
            progress_color = GOOD if access.status == "full" else WARM
            dot_color = (
                GOOD
                if access.status in {"full", "trial_active", "quota_active"}
                else WARN
            )
            if access.status == "full":
                ratio = 1.0
        else:
            bg = PANEL_SOFT
            fg = TEXT
            border = BORDER
            progress_color = WARM
            if access.status == "checking":
                timer_text = "…"
                fg = MUTED
                progress_color = MUTED
            elif access.status == "entitlement_pending":
                timer_text = "…"
                fg = WARN
                progress_color = WARN
                dot_color = WARN
            elif access.status == "state_error":
                timer_text = "!"
                fg = BAD
                progress_color = BAD
                dot_color = BAD
            elif access.status == "locked":
                fg = DIM
                progress_color = DIM
                dot_color = DIM
            elif access.status == "full":
                ratio = 1.0
                progress_color = GOOD
                dot_color = GOOD
            elif access.status in {"trial_active", "quota_active"}:
                fg = GOOD
                progress_color = GOOD
                dot_color = GOOD
            elif access.status in {"trial_paused", "quota_paused"}:
                fg = WARN
                progress_color = WARN
                dot_color = WARN
            elif access.status in {"trial_available", "quota_available"}:
                fg = WARM
                progress_color = WARM
                dot_color = WARM
            elif access.status in {"trial_exhausted", "quota_exhausted"}:
                fg = DIM
                progress_color = DIM
                dot_color = BAD
        if bool(getattr(self, "_pet_discovery_pulse_on", False)) and not unavailable:
            bg = _blend_hex(PANEL, WARM, 0.12)
            border = WARM
        try:
            launcher.configure(bg=bg)
            row = getattr(self, "pet_launcher_row", None)
            if row is not None:
                row.configure(bg=bg)
            timer.configure(text=timer_text, bg=bg, fg=fg)
            if timer_text:
                timer_pad = self._pet_launcher_timer_pack_padding(timer, timer_text)
                if timer.winfo_manager() != "pack":
                    timer.pack(side="left", padx=timer_pad)
                else:
                    timer.pack_configure(padx=timer_pad)
            elif timer.winfo_manager() == "pack":
                timer.pack_forget()
            if cat is not None:
                cat.configure(bg=bg)
            dot.configure(bg=bg)
            dot.delete("all")
            dot.create_oval(1, 1, 6, 6, fill=dot_color, outline="")
            progress.configure(bg=bg)
            self._pet_launcher_progress_ratio = ratio
            self._pet_launcher_progress_color = progress_color
            self._redraw_pet_launcher_progress()
            holder = getattr(launcher, "master", None)
            if holder is not None and hasattr(holder, "_round_card"):
                holder._round_card["bg"] = bg
                holder._round_card["outline"] = border
                self._sync_rounded_card(holder)
                try:
                    self.root.after_idle(
                        lambda target=holder: self._sync_pet_launcher_card(target)
                    )
                except (AttributeError, tk.TclError):
                    pass
        except tk.TclError:
            pass

    def _update_pet_tooltip(self, *, unavailable: bool = False) -> None:
        if unavailable:
            text = "桌面猫猫资源不可用；主界面和报价不受影响"
        else:
            access = self._current_pet_access()
            if access.status == "checking":
                text = "正在确认猫猫权限；检测完成前不会错误开放"
                action_text = "左键状态岛：查看权限状态"
            elif access.status == "trial_available":
                text = f"{PET_ACCESS_TRIAL_TEXT} · 当前 {format_pet_access_clock(access)}"
                action_text = "左键状态岛：查看规则并确认开始"
            elif access.status in {"trial_active", "trial_paused"}:
                phase = "计量中" if access.status == "trial_active" else "已暂停"
                text = (
                    f"10 分钟猫猫体验 · {phase} · "
                    f"剩余 {format_pet_access_clock(access)}"
                )
                action_text = PET_TIP_ON if self._pet_enabled else PET_TIP_OFF
            elif access.status == "quota_available":
                text = (
                    f"{pet_access_tier_label(access)} · "
                    f"{pet_access_contract_text(access)} · "
                    f"当前 {format_pet_access_clock(access)}"
                )
                action_text = "左键状态岛：查看规则并确认启用"
            elif access.status in {"quota_active", "quota_paused"}:
                phase = "计量中" if access.status == "quota_active" else "已暂停"
                text = (
                    f"{pet_access_tier_label(access)} · {phase} · "
                    f"剩余 {format_pet_access_clock(access)}"
                )
                action_text = PET_TIP_ON if self._pet_enabled else PET_TIP_OFF
            elif access.status == "trial_exhausted":
                text = "一次性 10 分钟猫猫体验已用完"
                action_text = "左键状态岛：查看体验说明"
            elif access.status == "quota_exhausted":
                text = f"{pet_access_tier_label(access)}已用完"
                action_text = "左键状态岛：查看配额说明"
            elif access.status == "entitlement_pending":
                text = "猫猫权益同步中；不会按套餐名、本地偏好或剩余天数猜测"
                action_text = "左键状态岛：查看权限说明"
            elif access.status == "state_error":
                text = "猫猫权益暂时无法验证，已安全关闭；主功能不受影响"
                action_text = "左键状态岛：查看状态说明"
            elif access.status == "locked":
                text = "当前签名权益未包含可用的猫猫体验、配额或完整权限"
                action_text = "左键状态岛：查看权限说明"
            else:
                text = pet_access_contract_text(access)
                action_text = PET_TIP_ON if self._pet_enabled else PET_TIP_OFF
            color_label = PET_COLOR_LABELS[normalize_pet_color(self._pet_color)]
            text = (
                f"{text}\n"
                f"{action_text} · 只本地陪伴，不影响报价\n"
                f"{PET_COLOR_TIP} · 当前：{color_label}"
            )
        for name in ("pet_launcher_tip",):
            tip = getattr(self, name, None)
            if tip is not None:
                tip.set_text(text)
        self._update_pet_launcher_state(unavailable=unavailable)

    def _on_pet_position_changed(self, position: tuple[int, int]) -> None:
        self._pet_position = (int(position[0]), int(position[1]))
        self._schedule_ui_prefs_save()

    def _set_pet_color(self, color: Any) -> None:
        self._pet_color = normalize_pet_color(color)
        self._pet_action_deck = []
        companion = self._pet_companion
        if companion is not None:
            try:
                companion.set_color(self._pet_color)
            except (OSError, RuntimeError, tk.TclError):
                pass
        self._update_pet_tooltip()
        self._schedule_ui_prefs_save()

    def _set_pet_settlement_feedback_enabled(self, enabled: Any) -> None:
        self._pet_settlement_feedback_enabled = bool(enabled)
        self._schedule_ui_prefs_save()

    def _show_pet_menu(
        self,
        event: tk.Event[Any] | None = None,
        *,
        x_root: int | None = None,
        y_root: int | None = None,
    ) -> str:
        menu = tk.Menu(
            self.root,
            tearoff=0,
            bg=PANEL,
            fg=TEXT,
            activebackground=PANEL_SOFT,
            activeforeground=TEXT,
            borderwidth=0,
            activeborderwidth=0,
        )
        menu.add_command(
            label="收回桌面猫猫" if self._pet_enabled else "唤醒桌面猫猫",
            command=lambda: self._set_pet_companion_enabled(not self._pet_enabled),
        )
        access = self._current_pet_access()
        if access.status == "full":
            access_label = f"使用权限：{pet_access_contract_text(access)}"
        elif access.status == "checking":
            access_label = "使用权限：检测中…"
        elif access.status == "entitlement_pending":
            access_label = "使用权限：签名余额同步中…"
        elif access.status == "trial_available":
            access_label = "10 分钟体验：尚未开始"
        elif access.status in {"trial_active", "trial_paused"}:
            access_label = f"10 分钟体验：剩余 {format_pet_access_clock(access)}"
        elif access.status == "trial_exhausted":
            access_label = "10 分钟体验：已用完"
        elif access.status == "quota_available":
            access_label = (
                f"{pet_access_tier_label(access)}："
                f"{format_pet_access_clock(access)}"
            )
        elif access.status in {"quota_active", "quota_paused"}:
            access_label = (
                f"{pet_access_tier_label(access)}："
                f"剩余 {format_pet_access_clock(access)}"
            )
        elif access.status == "quota_exhausted":
            access_label = f"{pet_access_tier_label(access)}：已用完"
        elif access.status == "state_error":
            access_label = "使用权限：暂时无法验证"
        else:
            access_label = "使用权限：当前权益未包含"
        menu.add_command(label=access_label, state="disabled")
        if access.status in {
            "locked",
            "state_error",
            "trial_exhausted",
            "quota_exhausted",
        }:
            menu.add_command(label="查看订阅方案", command=self._open_pet_plan_dialog)
        menu.add_separator()
        menu.add_command(label="猫猫颜色", state="disabled")
        variable = tk.StringVar(master=self.root, value=normalize_pet_color(self._pet_color))
        self._pet_menu_color_var = variable
        for color in PET_COLOR_ORDER:
            menu.add_radiobutton(
                label=f"  {PET_COLOR_LABELS[color]}",
                value=color,
                variable=variable,
                command=lambda choice=color: self._set_pet_color(choice),
            )
        menu.add_separator()
        feedback_variable = tk.BooleanVar(
            master=self.root,
            value=bool(getattr(self, "_pet_settlement_feedback_enabled", True)),
        )
        self._pet_menu_settlement_feedback_var = feedback_variable
        menu.add_checkbutton(
            label="结算反馈动画（测试）",
            variable=feedback_variable,
            command=lambda: self._set_pet_settlement_feedback_enabled(
                feedback_variable.get()
            ),
        )
        menu.add_command(
            label="  测试功能 · 后续可能调整为增值权益",
            state="disabled",
        )
        if event is not None:
            x_root = int(event.x_root)
            y_root = int(event.y_root)
        if x_root is None or y_root is None:
            try:
                x_root = int(self.support_cat.winfo_rootx())
                y_root = int(self.support_cat.winfo_rooty() + self.support_cat.winfo_height())
            except (AttributeError, tk.TclError):
                x_root, y_root = 200, 200
        try:
            menu.tk_popup(int(x_root), int(y_root))
        finally:
            menu.grab_release()
        return "break"

    def _next_pet_companion_action(self) -> str:
        available_actions = pet_actions_for_color(self._pet_color)
        self._pet_action_deck = [
            action
            for action in self._pet_action_deck
            if action in available_actions
        ]
        if not self._pet_action_deck:
            self._pet_action_deck = list(available_actions)
            random.shuffle(self._pet_action_deck)
            if (
                len(self._pet_action_deck) > 1
                and self._pet_action_deck[-1] == self._last_companion_action
            ):
                self._pet_action_deck[0], self._pet_action_deck[-1] = (
                    self._pet_action_deck[-1],
                    self._pet_action_deck[0],
                )
        action = self._pet_action_deck.pop()
        self._last_companion_action = action
        return action

    def _interact_with_pet_companion(self) -> None:
        companion = self._pet_companion
        if companion is None:
            return
        self._record_pet_touch()
        action = self._next_pet_companion_action()
        action_text = random.choice(PET_ACTION_BUBBLES.get(action, ("摸摸～",)))
        # Make every accepted click visible immediately.  Animation rendering is
        # asynchronous and deliberately fail-soft, so it must not be the only
        # acknowledgement that an interaction was recorded.
        try:
            companion.show_touch_feedback(
                f"互动 · {action_text}  +1",
                duration_ms=1050,
            )
        except (AttributeError, tk.TclError):
            pass
        try:
            getattr(companion, action)()
        except (AttributeError, tk.TclError):
            return

    def _record_pet_touch(self) -> tuple[int, int]:
        (
            self._pet_touch_date,
            self._pet_touch_today,
            self._pet_touch_total,
        ) = normalize_pet_touch_state(
            getattr(self, "_pet_touch_date", None),
            getattr(self, "_pet_touch_today", 0),
            getattr(self, "_pet_touch_total", 0),
        )
        self._pet_touch_today += 1
        self._pet_touch_total += 1
        self._schedule_ui_prefs_save()
        return int(self._pet_touch_today), int(self._pet_touch_total)

    def _maybe_play_pet_settlement_reaction(
        self,
        snapshot: dict[str, Any],
    ) -> bool:
        reaction = pet_settlement_reaction(snapshot)
        if reaction is None:
            return False
        session_id, _net_result, action, bubble = reaction
        consumed_sessions = getattr(
            self,
            "_consumed_pet_settlement_reaction_sessions",
            None,
        )
        if not isinstance(consumed_sessions, set):
            consumed_sessions = set()
            self._consumed_pet_settlement_reaction_sessions = consumed_sessions
        if session_id in consumed_sessions:
            return False
        # Consume the live settlement once even while Pet is hidden. Reopening
        # Pet must not replay a stale result as if it had just happened.
        consumed_sessions.add(session_id)
        if not bool(getattr(self, "_pet_settlement_feedback_enabled", True)):
            return False
        companion = getattr(self, "_pet_companion", None)
        if (
            companion is None
            or not bool(getattr(self, "_pet_enabled", False))
            or not self._is_pet_companion_visible()
        ):
            return False
        try:
            getattr(companion, action)()
            companion.show_bubble(
                bubble,
                duration_ms=1800,
            )
        except (AttributeError, tk.TclError):
            return False
        # The automatic result reaction is not a user touch and must not enter
        # the +1 ledger. Removing its action from the current daily deck is a
        # no-op for settlement-only poses and avoids an immediate repeated perk.
        self._last_companion_action = action
        self._pet_action_deck = [
            queued_action
            for queued_action in getattr(self, "_pet_action_deck", [])
            if queued_action != action
        ]
        return True

    def _maybe_celebrate_profitable_pet_settlement(
        self,
        snapshot: dict[str, Any],
    ) -> bool:
        """Backward-compatible entry point retained for older UI callers/tests."""

        return self._maybe_play_pet_settlement_reaction(snapshot)

    def _destroy_pet_companion(self) -> None:
        state = self._current_pet_access()
        self._set_pet_usage_visibility(False, state)
        self._pet_visibility_generation += 1
        self._pet_visibility_pending = False
        self._pet_enabled = False
        companion = getattr(self, "_pet_companion", None)
        self._pet_companion = None
        if companion is not None:
            try:
                companion.destroy()
            except (AttributeError, tk.TclError):
                pass

    def _feed_cat(self, event: tk.Event[Any] | None = None) -> None:
        """Click the can to feed the cat: the cat EATS (fast chomps + wag) + a floating heart -- the
        can and cat are linked so feeding visibly makes the cat dig in."""
        cat = getattr(self, "support_cat", None)
        if cat is not None:
            try:
                cat.eat()  # the cat reacting to being fed (can <-> cat interaction)
            except (tk.TclError, AttributeError):
                pass
        companion = getattr(self, "_pet_companion", None)
        if companion is not None and self._pet_enabled:
            try:
                companion.eat()
                companion.show_bubble(random.choice(PET_ACTION_BUBBLES["eat"]))
            except (tk.TclError, AttributeError):
                pass
        self._pop_feed_heart()

    _HEART = ((1, 0), (2, 0), (4, 0), (5, 0)) + tuple(
        (c, r) for r in (1, 2) for c in range(7)
    ) + ((1, 3), (2, 3), (3, 3), (4, 3), (5, 3), (2, 4), (3, 4), (4, 4), (3, 5))

    def _pop_feed_heart(self, big: bool = False) -> None:
        cat = getattr(self, "support_cat", None)
        if cat is None:
            return
        try:
            cat.update_idletasks()
            cx = cat.winfo_rootx() + cat.winfo_width() // 2
            cy = cat.winfo_rooty()
        except tk.TclError:
            return
        key = "#010203"  # transparent-colour key (Windows) so only the heart shows
        try:
            pop = tk.Toplevel(self.root)
            pop.overrideredirect(True)
            pop.attributes("-topmost", True)
            try:
                pop.attributes("-transparentcolor", key)
            except tk.TclError:
                key = BG  # fallback: blend into the footer ground
            px = 4 if big else 3
            size = 7 * px
            cv = tk.Canvas(pop, width=size, height=size + px, bg=key, highlightthickness=0, bd=0)
            cv.pack()
            for c, r in self._HEART:
                cv.create_rectangle(c * px, r * px, c * px + px, r * px + px, fill=WARM, width=0)
        except tk.TclError:
            return
        steps = 15

        def _rise(i: int) -> None:
            if i > steps:
                try:
                    pop.destroy()
                except tk.TclError:
                    pass
                return
            try:
                pop.geometry(f"+{cx - size // 2}+{int(cy - 4 - i * 1.7)}")
                pop.attributes("-alpha", max(0.0, 1.0 - i / steps))
            except tk.TclError:
                return
            self.root.after(45, lambda: _rise(i + 1))

        _rise(0)

    _CAT_MEOWS = ("喵~", "喵喵~", "喵呜~", "咕噜噜~")
    # Click reactions; "meow" is weighted heavier. Method name == action name (dispatched via getattr).
    _CAT_ACTIONS = ("meow", "meow", "stretch", "lick", "sleep", "yawn", "tilt", "chase")

    def _pet_cat(self, event: tk.Event[Any] | None = None) -> None:
        """Click the cat to interact: the cat itself ANIMATES — meow / stretch / lick a paw / nap /
        yawn / head-tilt / chase its tail. Consecutive clicks avoid repeating the same action so it
        visibly switches. The meow adds a small sound bubble. Pure fun."""
        cat = getattr(self, "support_cat", None)
        if cat is None:
            return
        action = random.choice(self._CAT_ACTIONS)
        last = getattr(self, "_last_cat_action", None)
        for _ in range(4):  # re-roll a few times to dodge an immediate repeat
            if action != last:
                break
            action = random.choice(self._CAT_ACTIONS)
        self._last_cat_action = action
        try:
            getattr(cat, action)()
        except (tk.TclError, AttributeError):
            return
        if action == "meow":
            self._pop_cat_text(random.choice(self._CAT_MEOWS))

    # 低频【自发】闲置动作(作者0709: 很低频率蹦一下要摸摸/饿了, 增添生气)。不打扰: 只在窗口可见时,
    # 且每次 tick 再打个折才真触发; 复用点击那套动作+气泡。间隔 60~180s 随机。
    _CAT_IDLE_BUBBLES = ("摸摸我嘛~", "蹭蹭…", "喵~ 有点饿了", "呼噜噜~", "看看我?", "困困…", "喂饭饭~")

    def _start_cat_idle(self) -> None:
        self._schedule_cat_idle()

    def _schedule_cat_idle(self) -> None:
        try:
            self.root.after(random.randint(60_000, 180_000), self._cat_idle_tick)
        except tk.TclError:
            pass

    def _cat_idle_tick(self) -> None:
        cat = getattr(self, "support_cat", None)
        try:
            visible = bool(cat) and self.root.winfo_viewable()
        except tk.TclError:
            visible = False
        if visible and random.random() < 0.6:          # 低频再打折 → 更稀疏, 不烦人
            action = random.choice(("stretch", "yawn", "tilt", "lick", "meow"))
            try:
                getattr(cat, action)()
            except (tk.TclError, AttributeError):
                pass
            else:
                self._pop_cat_text(random.choice(self._CAT_IDLE_BUBBLES))
        self._schedule_cat_idle()                       # 循环下一次

    def _pop_cat_text(self, text: str) -> None:
        """A little speech bubble that floats up and fades above the cat."""
        cat = getattr(self, "support_cat", None)
        if cat is None:
            return
        try:
            cat.update_idletasks()
            cx = cat.winfo_rootx() + cat.winfo_width() // 2
            cy = cat.winfo_rooty()
        except tk.TclError:
            return
        try:
            pop = tk.Toplevel(self.root)
            pop.overrideredirect(True)
            pop.attributes("-topmost", True)
            _apply_windows_toolwindow(pop, enabled=not self.show_taskbar)
            pop.configure(bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
            tk.Label(
                pop, text=text, bg=PANEL, fg=WARM, font=(FONT_UI, 9, "bold"), bd=0, padx=5, pady=1
            ).pack()
        except tk.TclError:
            return
        steps = 16

        def _rise(i: int) -> None:
            if i > steps:
                try:
                    pop.destroy()
                except tk.TclError:
                    pass
                return
            try:
                pop.update_idletasks()
                pw = pop.winfo_reqwidth()
                pop.geometry(f"+{cx - pw // 2}+{int(cy - 8 - i * 1.6)}")
                pop.attributes("-alpha", max(0.0, 1.0 - i / steps))
            except tk.TclError:
                return
            self.root.after(45, lambda: _rise(i + 1))

        _rise(0)

    def _measure_window_height_for_width(self, width: int, *, probe_height: int | None = None) -> int:
        width = max(430, min(1200, int(width)))
        suffix = self._geometry_position_suffix()
        _, current_height = self._current_window_size()
        if probe_height is None:
            # 用【足够大的 probe】测"内容不被窗口压缩的自然高度"。实测(BIDKING_OVERLAY_FIT_DEBUG):
            # probe=475→reqheight=452, probe=452→reqheight=475 —— reqheight 反向依赖 probe(窗口越矮
            # 某 fill/换行内容 reqheight 反而越大)→ 若拿"当前高"当 probe 会 452↔475 双向震荡, 停在偏高
            # 475 → 底部留空。给一个大 probe(屏幕工作高)则内容不被压缩, 稳定测得自然高度(452)= 正确贴合。
            _, work_h = self._screen_size()
            probe_height = min(1500, max(320, work_h - 72))
        else:
            probe_height = max(320, min(1500, int(probe_height)))
        self.root.geometry(f"{width}x{probe_height}{suffix}")
        self.root.update_idletasks()
        measured = max(320, int(self.root.winfo_reqheight()))
        if os.environ.get("BIDKING_OVERLAY_FIT_DEBUG") == "1":
            try:
                print(
                    f"[measure] w={width} probe={probe_height} root_req={measured} "
                    f"shell_req={self.shell.winfo_reqheight() if hasattr(self, 'shell') else '?'}",
                    file=sys.stderr,
                    flush=True,
                )
            except Exception:  # noqa: BLE001
                pass
        return min(1500, measured)

    def _default_mini_size(self) -> tuple[int, int]:
        if hasattr(self, "outer"):
            self.root.update_idletasks()
            # Wider default (floor 510, was 430) — user prefers a roomier landscape
            # mini window; width drives the UI scale (width/MINI_BASE_WIDTH) so text grows too.
            width = max(510, min(760, int(self.outer.winfo_reqwidth()) + 12))
            height = self._measure_window_height_for_width(width)
            return width, height
        return MINI_BASE_WIDTH, MINI_INITIAL_HEIGHT

    def _fit_mini_window_to_content(self) -> None:
        if self.details_expanded or not hasattr(self, "shell"):
            return
        width, _ = self._current_window_size()
        self._apply_mini_resize_layout(width)

    def _apply_mini_resize_layout(
        self,
        width: int,
        *,
        scale: float | None = None,
        finalize: bool = False,
    ) -> int | None:
        if self.details_expanded or not hasattr(self, "shell"):
            return None
        width = max(430, min(1200, int(width)))
        if scale is not None:
            next_scale = max(UI_SCALE_MIN, min(UI_SCALE_MAX, float(scale)))
            self._custom_mini_scale = next_scale
        elif getattr(self, "_custom_mini_scale", None) is not None:
            next_scale = max(
                UI_SCALE_MIN,
                min(UI_SCALE_MAX, float(self._custom_mini_scale)),
            )
        else:
            next_scale = compute_mini_ui_scale(width)
        self._queue_ui_scale(next_scale, force=finalize)
        if finalize:
            # 不喂"当前高"当 probe(会造成 452↔475 双向震荡→停偏高留空, 见 _measure_window_height_for_width):
            # 用大 probe 默认测自然高度, 稳定贴合。
            fitted_height = self._measure_window_height_for_width(width)
        elif getattr(self, "_resize_active", False):
            # Geometry frames between two throttled real-layout commits must preserve the last
            # rendered height. Re-estimating from shell.reqheight adds the 18px chrome allowance to
            # an already complete request and makes the border jump again on the next pointer frame.
            _, fitted_height = self._current_window_size()
        else:
            fitted_height = self._estimate_mini_content_height(flush=True)
        if os.environ.get("BIDKING_OVERLAY_FIT_DEBUG") == "1":
            try:  # 底部留空诊断: 对比 窗口现高 vs shell内容高+chrome vs root请求高, 定位多余高度来源。
                # ⚠️必须打 stderr: overlay 由 pythonw 启动, 只有 stderr 重定向进 overlay.stderr.log。
                _, _cur_h = self._current_window_size()
                print(
                    f"[fit] finalize={finalize} w={width} fitted={fitted_height} cur_h={_cur_h} "
                    f"shell_req={self.shell.winfo_reqheight()} root_req={self.root.winfo_reqheight()} "
                    f"est_shell+chrome={self._estimate_mini_content_height()}",
                    file=sys.stderr,
                    flush=True,
                )
                # 逐控件高度: 揪出等待态多要 ~23px 的那个控件(capture 状态变化时长高但没填满)。
                for _k in self.shell.winfo_children():
                    print(
                        f"  [child] {_k.winfo_class():8} req_h={_k.winfo_reqheight():4} "
                        f"h={_k.winfo_height():4} y={_k.winfo_y():4} name=...{str(_k)[-22:]}",
                        file=sys.stderr,
                        flush=True,
                    )
            except Exception:  # noqa: BLE001
                pass
        self._custom_mini_size = (width, fitted_height)
        self._apply_window_geometry(
            f"{width}x{fitted_height}",
            flush=finalize or not getattr(self, "_resize_active", False),
        )
        if finalize or not getattr(self, "_resize_active", False):
            self._schedule_autofit_refresh()
        return fitted_height

    def _remember_mini_layout_snapshot(self) -> None:
        width, height = self._current_window_size()
        self._mini_layout_snapshot = (
            self._custom_mini_size,
            (width, height),
            float(self._ui_scale),
        )

    def _reset_ui_scale_baseline(self) -> None:
        self._ui_scale = 1.0
        self._apply_ui_scale()

    def _restore_mini_layout_after_details(self) -> None:
        snapshot = self._mini_layout_snapshot
        self._custom_details_size = None
        if snapshot is not None:
            custom_size, saved_size, saved_scale = snapshot
            self._custom_mini_size = custom_size
            self._custom_mini_scale = max(
                UI_SCALE_MIN,
                min(UI_SCALE_MAX, float(saved_scale)),
            )
        else:
            custom_size = None
            saved_size = (MINI_BASE_WIDTH, MINI_INITIAL_HEIGHT)

        self.mode_button.configure(text="详情")
        self.root.minsize(430, 395)
        self.details_card.pack_forget()
        self.minimap_card.pack_forget()

        if custom_size is not None:
            width = int(custom_size[0])
        else:
            width = int(saved_size[0])
        self._apply_mini_resize_layout(width, finalize=True)

    def _mini_geometry(self) -> str:
        if self._custom_mini_size is not None:
            width, height = self._custom_mini_size
        else:
            width, height = self._default_mini_size()
        return f"{width}x{height}"

    def _screen_size(self) -> tuple[int, int]:
        left, top, right, bottom = self._work_area_rect()
        return max(1, right - left), max(1, bottom - top)

    def _work_area_rect(self) -> tuple[int, int, int, int]:
        """当前窗口所在显示器的工作区 ``(left, top, right, bottom)``（排除任务栏）。"""
        try:
            root_x = int(self.root.winfo_x())
            root_y = int(self.root.winfo_y())
            root_w = max(1, int(self.root.winfo_width()))
            root_h = max(1, int(self.root.winfo_height()))
        except (AttributeError, tk.TclError, TypeError, ValueError):
            root_x = root_y = 0
            root_w = root_h = 1
        return _monitor_work_area_for_point(
            self.root,
            root_x + root_w // 2,
            root_y + root_h // 2,
        )

    def _nudge_window_onscreen(self) -> None:
        """详情展开后, 若整窗超出屏幕工作区(尤其窗口原本靠屏幕下沿, 展开变高后底部到屏外), 把窗口
        位置拉回屏内, 保证整窗可见 —— 修"点详情后下面的信息也都不显示"(群友小分辨率/低位窗)。
        best-effort, 任何异常都不影响 UI。"""
        if not hasattr(self, "root"):
            return
        try:
            self.root.update_idletasks()
            w = int(self.root.winfo_width())
            h = int(self.root.winfo_height())
            x = int(self.root.winfo_x())
            y = int(self.root.winfo_y())
            left, top, right, bottom = self._work_area_rect()
            nx, ny = _clamp_rect_into_workarea(x, y, w, h, left, top, right, bottom)
            if nx != x or ny != y:
                self.root.geometry(f"{w}x{h}+{nx}+{ny}")
                self.root.update_idletasks()
                self._refresh_window_rounding()
        except Exception:  # noqa: BLE001 - 重定位纯兜底, 失败不影响展开
            pass

    def _bind_drag(self, *widgets: tk.Widget) -> None:
        for widget in widgets:
            widget.bind("<ButtonPress-1>", self._begin_drag, add="+")
            widget.bind("<B1-Motion>", self._drag_window, add="+")
            widget.bind("<ButtonRelease-1>", self._end_drag, add="+")

    def _bind_drag_recursive(self, widget: tk.Widget, *, exclude: set[tk.Widget]) -> None:
        if widget in exclude:
            return
        interactive = (tk.Button, ttk.Button, tk.Entry, tk.Canvas, tk.Scale, tk.Scrollbar)
        if not isinstance(widget, interactive):
            self._bind_drag(widget)
        for child in widget.winfo_children():
            self._bind_drag_recursive(child, exclude=exclude)

    def _begin_drag(self, event: tk.Event[Any]) -> None:
        self._drag_active = True
        self._drag_offset = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _drag_window(self, event: tk.Event[Any]) -> None:
        if self._drag_offset is None:
            return
        dx, dy = self._drag_offset
        next_x = event.x_root - dx
        next_y = event.y_root - dy
        self.root.geometry(f"+{next_x}+{next_y}")
        self._schedule_pinned_minimap_sync(root_x=next_x, root_y=next_y)

    def _schedule_pinned_minimap_sync(self, *, root_x: int, root_y: int) -> None:
        self._pending_pinned_sync = (int(root_x), int(root_y))
        if self._pinned_sync_after_id is not None:
            return
        self._pinned_sync_after_id = self.root.after(16, self._flush_pinned_minimap_sync)

    def _flush_pinned_minimap_sync(self) -> None:
        self._pinned_sync_after_id = None
        pending = self._pending_pinned_sync
        if pending is None:
            return
        self._sync_pinned_minimap_position(root_x=pending[0], root_y=pending[1])
        self._pending_pinned_sync = None

    def _end_drag(self, _event: tk.Event[Any]) -> None:
        self._drag_offset = None
        self._drag_active = False
        pending = self._pending_pinned_sync
        if pending is not None:
            self._sync_pinned_minimap_position(root_x=pending[0], root_y=pending[1])
            self._pending_pinned_sync = None
        if self._pinned_sync_after_id is not None:
            try:
                self.root.after_cancel(self._pinned_sync_after_id)
            except tk.TclError:
                pass
            self._pinned_sync_after_id = None
        self._flush_deferred_live_summary()
        self._schedule_minimap_canvas_render()
        self._schedule_ui_prefs_save()

    def _begin_resize(self, event: tk.Event[Any]) -> str:
        self._drag_active = False
        self._resize_active = True
        self._resize_pending_pointer = None
        self._resize_toolbar_compact = False
        stale_after_id = getattr(self, "_resize_frame_after_id", None)
        if stale_after_id is not None:
            try:
                self.root.after_cancel(stale_after_id)
            except tk.TclError:
                pass
        self._resize_frame_after_id = None
        width0, height0 = self._current_window_size()
        try:
            self._resize_shell_chrome_width = max(
                18,
                int(width0) - max(1, int(self.shell.winfo_width())),
            )
        except (AttributeError, tk.TclError, TypeError, ValueError):
            self._resize_shell_chrome_width = 30
        self._resize_anchor = (
            event.x_root,
            event.y_root,
            width0,
            height0,
        )
        self._resize_scale_anchor = float(getattr(self, "_ui_scale", 1.0))
        event_widget = getattr(event, "widget", None)
        if event_widget is getattr(self, "resize_grip", None):
            self._active_resize_tip = getattr(self, "resize_grip_tip", None)
        else:
            self._active_resize_tip = getattr(self, "top_resize_tip", None)
        if self._active_resize_tip is not None:
            self._active_resize_tip.fixed_position = True
        self._show_resize_preview(
            width0,
            height0,
            float(getattr(self, "_ui_scale", 1.0)),
            x_root=int(event.x_root),
            y_root=int(event.y_root),
        )
        return "break"

    def _resize_window(self, event: tk.Event[Any]) -> str:
        if self._resize_anchor is None:
            return "break"
        # Windows can deliver mouse-motion events much faster than Tk can remeasure hundreds of
        # widgets.  Keep only the newest pointer and commit at most once per display frame so the
        # queue cannot build up behind the user's hand.  This remains live resizing; release only
        # performs the final exact commit.
        self._resize_pending_pointer = (int(event.x_root), int(event.y_root))
        if getattr(self, "_resize_frame_after_id", None) is None:
            self._resize_frame_after_id = self.root.after(
                RESIZE_FRAME_INTERVAL_MS,
                self._flush_resize_frame,
            )
        return "break"

    def _flush_resize_frame(self) -> None:
        self._resize_frame_after_id = None
        pending = getattr(self, "_resize_pending_pointer", None)
        self._resize_pending_pointer = None
        if pending is None or getattr(self, "_resize_anchor", None) is None:
            return
        # A motion frame now contains only one geometry commit; typography/autofit/rounding are
        # deferred below, so a native redraw transaction here would add a synchronous full-tree
        # repaint without hiding any intermediate layout. Let Tk/DWM present this frame normally.
        self._apply_resize_pointer(*pending)

    def _apply_resize_pointer(self, x_root: int, y_root: int) -> None:
        if self._resize_anchor is None:
            return
        x0, y0, width0, height0 = self._resize_anchor
        dx = int(x_root - x0)
        dy = int(y_root - y0)
        if self.details_expanded:
            width = max(430, min(1200, width0 + dx))
            height = max(320, min(1500, height0 + dy))
            self._update_resize_toolbar_layout(width)
            self._custom_details_size = (width, height)
            self._apply_window_geometry(f"{width}x{height}", flush=False)
            next_scale = compute_ui_scale(
                width,
                height,
                base_width=DETAILS_BASE_WIDTH,
                base_height=DETAILS_BASE_HEIGHT,
            )
            self._queue_ui_scale(next_scale, force=False)
            self._show_resize_preview(
                width,
                height,
                next_scale,
                x_root=x_root,
                y_root=y_root,
            )
        else:
            scale0 = float(
                getattr(self, "_resize_scale_anchor", None)
                or getattr(self, "_ui_scale", 1.0)
            )
            width, next_scale = compute_mini_resize_target(
                width0,
                height0,
                scale0,
                dx,
                dy,
            )
            self._update_resize_toolbar_layout(width)
            fitted_height = self._apply_mini_resize_layout(width, scale=next_scale)
            self._show_resize_preview(
                width,
                int(fitted_height or height0),
                next_scale,
                x_root=x_root,
                y_root=y_root,
            )

    def _resize_toolbar_controls(self) -> tuple[tk.Widget, ...]:
        return tuple(
            widget
            for widget in (
                getattr(self, "settlement_button", None),
                getattr(self, "export_diag_button", None),
                getattr(self, "map_button", None),
                getattr(self, "theme_button", None),
                getattr(self, "manual_button", None),
                getattr(self, "mode_button", None),
            )
            if widget is not None
        )

    def _set_resize_toolbar_compact(self, compact: bool) -> None:
        compact = bool(compact)
        if compact == bool(getattr(self, "_resize_toolbar_compact", False)):
            return
        controls = self._resize_toolbar_controls()
        if len(controls) != 6:
            return
        try:
            if compact:
                for widget in controls:
                    widget.pack_forget()
                for index, widget in enumerate(controls):
                    widget.grid(
                        row=index // 3,
                        column=index % 3,
                        padx=(0, 5),
                        pady=(0, 3) if index < 3 else 0,
                        sticky="e",
                    )
            else:
                for widget in controls:
                    widget.grid_forget()
                # Pack right in reverse visual order to restore the original single row:
                # 藏价 → 导出 → 地图 → 配色 → 手填 → 详情.
                for widget in reversed(controls):
                    widget.pack(side="right", padx=(0, 5))
            self._resize_toolbar_compact = compact
        except (AttributeError, tk.TclError):
            return

    def _update_resize_toolbar_layout(self, target_width: int) -> None:
        controls = self._resize_toolbar_controls()
        if len(controls) != 6 or not hasattr(self, "status"):
            return
        normal_toolbar_width = 0
        for widget in controls:
            holder = getattr(widget, "master", widget)
            if widget is getattr(self, "mode_button", None):
                holder = widget
            try:
                normal_toolbar_width += max(1, int(holder.winfo_reqwidth())) + 5
            except (AttributeError, tk.TclError, TypeError, ValueError):
                return
        try:
            status_width = max(1, int(self.status.winfo_reqwidth()))
        except (AttributeError, tk.TclError, TypeError, ValueError):
            return
        available = max(
            1,
            int(target_width) - int(getattr(self, "_resize_shell_chrome_width", 30)),
        )
        required = status_width + normal_toolbar_width + 8
        if getattr(self, "_resize_toolbar_compact", False):
            compact = available < required + 16
        else:
            compact = available < required
        self._set_resize_toolbar_compact(compact)

    def _show_resize_preview(
        self,
        width: int,
        height: int,
        scale: float,
        *,
        x_root: int,
        y_root: int,
    ) -> None:
        tip = getattr(self, "_active_resize_tip", None)
        if tip is None:
            return
        percent = int(round(float(scale) * 100.0))
        if self.details_expanded:
            text = (
                f"缩放中 · {int(width)} × {int(height)} · 目标排版 {percent}%\n"
                "内容持续跟随 · 松手精确确认"
            )
        else:
            text = (
                f"缩放中 · 宽度 {int(width)} · 目标排版 {percent}%\n"
                "左右调宽 · 上下调字号 · 高度自动贴合"
            )
        try:
            first_show = tip.tip is None
            tip.show_at_coords(x_root, y_root, text)
            popup = tip.tip
            if popup is None:
                return
            if first_show:
                # Only the first map needs a synchronous size. Calling this every frame also drains
                # the main overlay's pending layout and turned the live grip back into 80ms+ stalls.
                popup.update_idletasks()
            popup_w = max(1, int(popup.winfo_reqwidth()))
            popup_h = max(1, int(popup.winfo_reqheight()))
            root_x = int(self.root.winfo_x())
            root_y = int(self.root.winfo_y())
            # winfo_width can still be the previous frame immediately after geometry(...).
            # Anchor the live readout to the requested size so it follows the grip without lag.
            root_w = max(1, int(width))
            root_h = max(1, int(height))
            left, top, right, bottom = _monitor_work_area_for_point(
                self.root,
                root_x + root_w // 2,
                root_y + root_h // 2,
            )
            popup_x = root_x + root_w + 10
            if popup_x + popup_w > right:
                popup_x = root_x - popup_w - 10
            popup_x, popup_y = _clamp_rect_into_workarea(
                popup_x,
                root_y + 8,
                popup_w,
                popup_h,
                left,
                top,
                right,
                bottom,
            )
            popup.geometry(f"+{popup_x}+{popup_y}")
        except (AttributeError, tk.TclError):
            pass

    def _cancel_live_ui_scale_timer(self) -> None:
        after_id = getattr(self, "_live_scale_after_id", None)
        if after_id is not None:
            try:
                self.root.after_cancel(after_id)
            except tk.TclError:
                pass
            self._live_scale_after_id = None

    def _apply_pending_ui_scale(self, *, force: bool = False) -> bool:
        self._live_scale_after_id = None
        pending = getattr(self, "_live_scale_pending", None)
        if pending is None and not force:
            return False
        next_scale = float(pending if pending is not None else self._ui_scale)
        self._live_scale_pending = None
        if force or abs(next_scale - float(self._ui_scale)) >= UI_SCALE_LIVE_DELTA:
            self._ui_scale = next_scale
            self._apply_ui_scale()
            return True
        return False

    def _refit_mini_during_live_scale(self) -> None:
        """Commit the mini window's real content height in the same frame as live typography.

        Width geometry can follow the pointer cheaply, but its requested height is based on the last
        rendered fonts.  Once a throttled font/layout pass lands, remeasure immediately so continuous
        horizontal *or* vertical grip motion exposes the actual target size before release.
        """
        if getattr(self, "details_expanded", False) or not getattr(self, "_resize_active", False):
            return
        if not hasattr(self, "shell"):
            return
        try:
            self.root.update_idletasks()
            width, _ = self._current_window_size()
            # After the live font pass, root.reqheight is already the complete borderless window
            # request. Adding MINI_WINDOW_CHROME_HEIGHT here double-counts 18px and creates a final
            # release snap (observed 591 -> 573 at width 810).
            fitted_height = max(320, min(1500, int(self.root.winfo_reqheight())))
            self._custom_mini_size = (int(width), int(fitted_height))
            self._apply_window_geometry(f"{int(width)}x{int(fitted_height)}", flush=False)
        except (AttributeError, tk.TclError, TypeError, ValueError):
            return

    def _apply_pending_ui_scale_after(self) -> None:
        with self._atomic_layout_update():
            applied = self._apply_pending_ui_scale(force=False)
            if applied:
                self._refit_mini_during_live_scale()

    def _queue_ui_scale(self, next_scale: float, *, force: bool = False) -> None:
        next_scale = float(next_scale)
        if force or not getattr(self, "_resize_active", False):
            self._live_scale_pending = None
            self._cancel_live_ui_scale_timer()
            if force or abs(next_scale - float(self._ui_scale)) >= UI_SCALE_LIVE_DELTA:
                self._ui_scale = next_scale
                self._apply_ui_scale()
            return
        # Font/layout traversal is much more expensive than one geometry frame. Keep the newest
        # target, but never cancel an already scheduled pass: a debounce that restarts on every
        # pointer event leaves typography and mini height frozen for the entire continuous drag.
        self._live_scale_pending = next_scale
        if getattr(self, "_live_scale_after_id", None) is None:
            self._live_scale_after_id = self.root.after(
                UI_SCALE_LIVE_INTERVAL_MS,
                self._apply_pending_ui_scale_after,
            )

    def _end_resize(self, _event: tk.Event[Any]) -> str:
        with self._atomic_layout_update():
            return self._end_resize_now(_event)

    def _end_resize_now(self, event: tk.Event[Any]) -> str:
        pending = getattr(self, "_resize_pending_pointer", None)
        if (
            getattr(self, "_resize_anchor", None) is not None
            and hasattr(event, "x_root")
            and hasattr(event, "y_root")
        ):
            pending = (int(event.x_root), int(event.y_root))
        frame_after_id = getattr(self, "_resize_frame_after_id", None)
        if frame_after_id is not None:
            try:
                self.root.after_cancel(frame_after_id)
            except tk.TclError:
                pass
        self._resize_frame_after_id = None
        self._resize_pending_pointer = pending
        self._flush_resize_frame()
        # Compact wrapping only protects the deliberately stale typography during live motion.
        # Release always settles back to the product's normal one-row toolbar contract.
        self._set_resize_toolbar_compact(False)
        self._resize_anchor = None
        self._resize_scale_anchor = None
        self._resize_active = False
        self._cancel_live_ui_scale_timer()
        self._live_scale_pending = None
        self.root.update_idletasks()
        self._flush_deferred_live_summary()
        self._schedule_minimap_canvas_render()
        if self.details_expanded:
            self._sync_ui_scale_from_window(force=True)
        else:
            width, _ = self._current_window_size()
            self._apply_mini_resize_layout(width, finalize=True)
        self._refresh_window_rounding()
        self._schedule_autofit_refresh()
        self._schedule_ui_prefs_save()
        active_tip = getattr(self, "_active_resize_tip", None)
        if active_tip is not None:
            active_tip.fixed_position = False
            idle_tip_text = (
                FOOTER_RESIZE_TIP_TEXT
                if active_tip is getattr(self, "resize_grip_tip", None)
                else RESIZE_TIP_TEXT
            )
            active_tip.set_text(idle_tip_text)
            active_tip.hide()
        self._active_resize_tip = None
        return "break"

    def _ui_scale_base_geometry(self) -> tuple[int, int]:
        if self.details_expanded:
            return DETAILS_BASE_WIDTH, DETAILS_BASE_HEIGHT
        return MINI_BASE_WIDTH, MINI_BASE_HEIGHT

    def _scaled_padding(self, value: int) -> int:
        return max(1, int(round(int(value) * float(self._ui_scale))))

    def _capture_ui_font_bases(self, widget: tk.Widget) -> None:
        font_widgets = (
            tk.Label,
            tk.Button,
            tk.Entry,
            tk.Text,
            tk.Checkbutton,
            tk.Radiobutton,
        )
        if isinstance(widget, font_widgets):
            if not hasattr(widget, "_ahmad_font_base"):
                spec = font_spec_from_widget(widget)
                if spec is not None:
                    widget._ahmad_font_base = spec  # type: ignore[attr-defined]
        for child in widget.winfo_children():
            self._capture_ui_font_bases(child)

    def _apply_font_scale(self, widget: tk.Widget) -> None:
        font_widgets = (
            tk.Label,
            tk.Button,
            tk.Entry,
            tk.Text,
            tk.Checkbutton,
            tk.Radiobutton,
        )
        if isinstance(widget, font_widgets):
            base = getattr(widget, "_ahmad_font_base", None)
            if base is None and abs(float(self._ui_scale) - 1.0) < 0.05:
                base = font_spec_from_widget(widget)
                if base is not None:
                    widget._ahmad_font_base = base  # type: ignore[attr-defined]
            base = getattr(widget, "_ahmad_font_base", None)
            if base is not None:
                family, size, *styles = base
                widget.configure(font=scaled_font(family, int(size), *styles, scale=self._ui_scale))
        for child in widget.winfo_children():
            if not self.details_expanded and hasattr(self, "details_card"):
                if child in (self.details_card, self.minimap_card):
                    continue
            self._apply_font_scale(child)

    def _apply_layout_scale(self) -> None:
        for widget, options in self._scaled_layout_specs:
            if not widget.winfo_exists():
                continue
            widget.configure(
                **{
                    key: self._scaled_padding(value)
                    for key, value in options.items()
                }
            )

    def _apply_button_style_scale(self) -> None:
        pad_x, pad_y = HERO_BUTTON_PADDING_BASE
        self.style.configure(
            "Hero.TButton",
            font=scaled_font(FONT_UI, HERO_BUTTON_FONT_SIZE, scale=self._ui_scale),
            padding=(self._scaled_padding(pad_x), self._scaled_padding(pad_y)),
        )

    def _apply_ui_scale(self) -> None:
        self._apply_layout_scale()
        self._apply_button_style_scale()
        self._apply_font_scale(self.outer)
        for control in (
            self.top_resize_grip,
            self.restart_button,
            self.topmost_button,
            self.close_button,
        ):
            holder = getattr(control, "_rounded_holder", None)
            if holder is not None:
                self._sync_rounded_card(holder)
        # The scaled layout can shrink the red row after its prior fit cache was
        # recorded.  Settle Tk's final parcel before restoring that cache.
        self.root.update_idletasks()
        self._restore_red_count_triplet_cached_font_after_global_scale()
        self._stabilize_footer_link_height()
        self._layout_footer_groups(force=True)
        # 小地图重渲染是缩放里最重的一步; 拖动缩放过程中跳过它(_end_resize 会再完整渲染一次)，
        # 让拖动更丝滑、不被每帧的小地图重绘拖慢。
        if self.details_expanded and self._minimap_data and not getattr(self, "_resize_active", False):
            self._render_minimap(self._minimap_data)
        if getattr(self, "_resize_active", False):
            try:
                self.root.after_idle(
                    lambda: self._update_resize_toolbar_layout(self._current_window_size()[0])
                )
            except (AttributeError, tk.TclError):
                pass
        # Scaling restores each label's base font; re-run density-aware fits once layout has settled.
        if not getattr(self, "_resize_active", False):
            self._schedule_autofit_refresh()

    def _current_window_size(self) -> tuple[int, int]:
        geo = str(self.root.geometry() or "")
        size_part = geo.split("+", 1)[0]
        if "x" in size_part:
            try:
                parsed_w, parsed_h = size_part.split("x", 1)
                return max(1, int(parsed_w)), max(1, int(parsed_h))
            except ValueError:
                pass
        width = max(1, int(self.root.winfo_width() or 0))
        height = max(1, int(self.root.winfo_height() or 0))
        if width >= 200 and height >= 200:
            return width, height
        if self.details_expanded:
            return DETAILS_BASE_WIDTH, DETAILS_BASE_HEIGHT
        return self._default_mini_size()

    def _sync_ui_scale_from_window(self, *, force: bool = False) -> None:
        if not hasattr(self, "root"):
            return
        width, height = self._current_window_size()
        if self.details_expanded:
            base_w, base_h = self._ui_scale_base_geometry()
            next_scale = compute_ui_scale(
                width,
                height,
                base_width=base_w,
                base_height=base_h,
            )
        else:
            custom_mini_scale = getattr(self, "_custom_mini_scale", None)
            next_scale = (
                float(custom_mini_scale)
                if custom_mini_scale is not None
                else compute_mini_ui_scale(width)
            )
        scale_changed = abs(next_scale - self._ui_scale) >= 0.02
        if force or scale_changed:
            self._queue_ui_scale(next_scale, force=force)

    def _configure_theme_style(self) -> None:
        self.style.configure(
            "Hero.TButton",
            background=PANEL_SOFT,
            foreground=TEXT,
            bordercolor=BORDER,
            lightcolor=PANEL_SOFT,
            darkcolor=PANEL_SOFT,
        )
        self.style.map("Hero.TButton", background=[("active", HERO_BUTTON_ACTIVE)])
        self._apply_button_style_scale()

    def _replace_theme_colors(self, widget: tk.Widget, replacements: dict[str, str]) -> None:
        for option in (
            "bg",
            "background",
            "fg",
            "foreground",
            "activebackground",
            "activeforeground",
            "highlightbackground",
            "highlightcolor",
            "insertbackground",
            "selectbackground",
            "selectforeground",
        ):
            try:
                current = str(widget.cget(option)).lower()
            except tk.TclError:
                continue
            replacement = replacements.get(current)
            if replacement:
                try:
                    widget.configure(**{option: replacement})
                except tk.TclError:
                    pass
        # Some interactive labels temporarily replace their foreground and retain the
        # normal colour for the next state transition.  Theme switching must remap the
        # retained semantic colour too, otherwise leaving that temporary state restores
        # warm-theme ink onto a dark card (the suggestion row becomes nearly invisible).
        for attribute in ("_action_normal_fg",):
            current = getattr(widget, attribute, None)
            replacement = replacements.get(str(current).lower()) if current else None
            if replacement:
                setattr(widget, attribute, replacement)
        action_state = getattr(widget, "_action_normal_visual_state", None)
        if isinstance(action_state, dict):
            for option in (
                "fg",
                "highlightbackground",
                "highlightcolor",
            ):
                current = action_state.get(option)
                replacement = (
                    replacements.get(str(current).lower()) if current else None
                )
                if replacement:
                    action_state[option] = replacement
        for child in widget.winfo_children():
            self._replace_theme_colors(child, replacements)

    def _show_theme_menu(self, event: tk.Event[Any] | None = None) -> str:
        menu = tk.Menu(
            self.root,
            tearoff=0,
            bg=PANEL,
            fg=TEXT,
            activebackground=PANEL_SOFT,
            activeforeground=TEXT,
            borderwidth=0,
            activeborderwidth=0,
        )
        for name in THEME_ORDER:
            label = "随机抽色" if name == "random" else str(THEMES[name]["label"])
            menu.add_command(label=label, command=lambda choice=name: self.apply_theme(choice))
        menu.add_separator()
        menu.add_command(
            label="收回桌面猫猫" if self._pet_enabled else "唤醒桌面猫猫",
            command=lambda: self._set_pet_companion_enabled(not self._pet_enabled),
        )
        menu.add_command(label="猫猫颜色", state="disabled")
        pet_color_var = tk.StringVar(
            master=self.root,
            value=normalize_pet_color(self._pet_color),
        )
        self._theme_menu_pet_color_var = pet_color_var
        for color in PET_COLOR_ORDER:
            menu.add_radiobutton(
                label=f"  {PET_COLOR_LABELS[color]}",
                value=color,
                variable=pet_color_var,
                command=lambda choice=color: self._set_pet_color(choice),
            )
        x = self.theme_button.winfo_rootx()
        y = self.theme_button.winfo_rooty() + self.theme_button.winfo_height() + 2
        if event is not None:
            x = event.x_root
            y = event.y_root + 8
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()
        return "break"

    def apply_theme(self, name: str) -> None:
        old_theme = self.theme_values
        new_theme = _theme_by_name(name)
        self.theme_name = name
        self.theme_values = new_theme
        _apply_theme_globals(new_theme)
        replacements = _theme_replacements(old_theme, new_theme)
        self._configure_theme_style()
        self._replace_theme_colors(self.root, replacements)
        if self._minimap_popup is not None:
            self._replace_theme_colors(self._minimap_popup, replacements)
        if self._pinned_minimap_popup is not None:
            self._replace_theme_colors(self._pinned_minimap_popup, replacements)
        # Rounded cards paint their fill as a canvas item, which the widget-attr recolour
        # above does not touch -- remap each card's stored fill and redraw it.
        for holder in list(getattr(self, "_rounded_cards", [])):
            try:
                data = holder._round_card  # type: ignore[attr-defined]
                data["bg"] = replacements.get(str(data["bg"]).lower(), data["bg"])
                self._draw_rounded_card(holder)
            except (AttributeError, tk.TclError):
                continue
        label = "随机" if name == "random" else str(new_theme.get("label") or name)
        self.theme_tip.set_text(f"{THEME_TIP_PREFIX}：{label}")
        self._set_manual_button_state()
        self._set_topmost_button_state()
        self._update_pet_tooltip()
        data = self._last_summary or self._last_live_summary
        if data:
            if data.get("status") == "stale_snapshot":
                self.render_standby(data)
            else:
                self.render(data)
        else:
            self.render_missing("等待 latest_snapshot.json")
        self._redraw_minimap_popup()
        self._redraw_pinned_minimap()
        self._schedule_ui_prefs_save()

    def _on_user_close(self) -> None:
        self._hide_minimap_popup()
        self._hide_pinned_minimap()
        # WSS is inline with the game's current connection.  Preserve it while the
        # game is active (or process detection is unavailable); once the game has
        # exited, an ordinary close can safely reclaim the background monitor.
        if bool(getattr(self, "protect_monitor_while_game_running", False)):
            game_state = _named_process_running_state(GAME_PROCESS_NAME)
            if game_state is not False:
                self.keep_monitor_on_close = True
        # 先把窗口隐掉 = 秒关视觉; 安全时停 monitor 的工作在窗口消失后进行，
        # 避免「点关闭→窗口冻结几秒等 monitor 退出」的卡顿。
        try:
            self.root.withdraw()
            self.root.update_idletasks()
        except Exception:
            pass
        self._save_ui_prefs_if_enabled()
        self._destroy_pet_companion()
        self._finish_ui_exit()

    def refresh_interface(self, _event: tk.Event[Any] | None = None) -> str:
        """Repaint the current UI state without touching capture lifecycle."""

        self._hide_minimap_popup()
        self._hide_pinned_minimap()
        self._update_pet_tooltip()
        data = self._last_summary or self._last_live_summary
        if data:
            if data.get("status") == "stale_snapshot":
                self.render_standby(data)
            else:
                self.render(data)
        else:
            self.render_missing("等待 latest_snapshot.json")
        self._redraw_minimap_popup()
        self._redraw_pinned_minimap()
        if hasattr(self, "refresh_tip"):
            self.refresh_tip.set_text(REFRESH_TIP)
        self._record_ui_runtime_status("interface_refreshed")
        return "break"

    def restart_calculator(self, _event: tk.Event[Any] | None = None) -> str:
        """一键重启计算器。

        新版连接只能在 BidKing.exe 完全退出后重新启动，因此公版 tooltip 和使用说明
        明确禁止在游戏运行中点击。运行态或检测未知时 fail-safe 拒绝，不关闭 UI、
        monitor，也不派生重启器。确认游戏已退出后，重启仍走包内 BidKingLive.exe
        （与首次启动同一条路径），并派生一个分离重启器等待旧 monitor+overlay 退出后再拉新实例。
        """
        game_state = _named_process_running_state(GAME_PROCESS_NAME)
        if game_state is not False:
            message = (
                RESTART_GAME_RUNNING_TEXT
                if game_state is True
                else RESTART_GAME_STATE_UNKNOWN_TEXT
            )
            if hasattr(self, "status"):
                self.status.configure(text=message)
            if hasattr(self, "restart_tip"):
                self.restart_tip.set_text(message)
            return "break"
        launcher = _find_calculator_launcher(self.snapshot_path)
        if launcher is None:
            if hasattr(self, "status"):
                self.status.configure(text="重启不可用")
            if hasattr(self, "restart_tip"):
                self.restart_tip.set_text("找不到启动器（BidKingLive.exe / 启动脚本），请手动关掉再打开")
            return "break"
        wait_pids = [int(p) for p in getattr(self, "_stop_pids_on_exit", ()) if int(p) > 0]
        self_pid = os.getpid()
        if self_pid not in wait_pids:
            wait_pids.append(self_pid)
        try:
            _spawn_restart_relauncher(
                launcher,
                wait_pids,
                diagnostic_profile=getattr(
                    self, "diagnostic_profile", DEFAULT_DIAGNOSTIC_PROFILE
                ),
                restart_log_path=self.snapshot_path.parent / "restart.log",
            )
        except Exception as exc:  # noqa: BLE001 - keep UI usable on failure
            if hasattr(self, "status"):
                self.status.configure(text="重启失败")
            if hasattr(self, "restart_tip"):
                self.restart_tip.set_text(f"重启失败: {exc}")
            return "break"
        if hasattr(self, "status"):
            self.status.configure(text="重启中…")
        # 关掉自己并【强制】停 monitor：keep_monitor_on_close 对重启不适用——不杀旧 monitor 的话，
        # 新启动脚本会复用旧的卡死 monitor。窗口先隐藏 = 秒关视觉。
        try:
            self.root.withdraw()
            self.root.update_idletasks()
        except Exception:  # noqa: BLE001
            pass
        self._save_ui_prefs_if_enabled()
        self._destroy_pet_companion()
        self.keep_monitor_on_close = False
        self._finish_ui_exit()
        return "break"

    def _apply_topmost_state_to_windows(self) -> None:
        enabled = bool(getattr(self, "topmost_enabled", True))
        for window in (self.root, self._minimap_popup, self._pinned_minimap_popup):
            if window is None:
                continue
            try:
                window.attributes("-topmost", enabled)
            except tk.TclError:
                pass
        companion = getattr(self, "_pet_companion", None)
        if companion is not None:
            try:
                companion.set_topmost(enabled)
            except (AttributeError, tk.TclError):
                pass

    def _set_topmost_button_state(self) -> None:
        if not hasattr(self, "topmost_button"):
            return
        enabled = bool(getattr(self, "topmost_enabled", True))
        self.topmost_button.configure(
            text="T",
            fg=WARM if enabled else DIM,
            bg=PANEL_MUTED if enabled else PANEL_SOFT,
            highlightbackground=WARM if enabled else BORDER,
        )
        if hasattr(self, "topmost_tip"):
            self.topmost_tip.set_text(TOPMOST_ON_TIP if enabled else TOPMOST_OFF_TIP)

    def toggle_topmost(self, _event: tk.Event[Any] | None = None) -> str:
        self.topmost_enabled = not bool(getattr(self, "topmost_enabled", True))
        self._apply_topmost_state_to_windows()
        self._set_topmost_button_state()
        return "break"

    def _open_credit_bilibili(self, _event: tk.Event[Any] | None = None) -> None:
        webbrowser.open(CREDIT_BILIBILI_URL, new=2, autoraise=True)

    def _activate_cloud_big_data_hover(
        self,
        _event: tk.Event[Any] | None = None,
    ) -> None:
        if bool(getattr(self, "_cloud_big_data_opening", False)):
            return
        try:
            self.footer_cloud_big_data.configure(state="active")
        except (AttributeError, tk.TclError):
            pass

    def _deactivate_cloud_big_data_hover(
        self,
        _event: tk.Event[Any] | None = None,
    ) -> None:
        if bool(getattr(self, "_cloud_big_data_opening", False)):
            return
        try:
            self.footer_cloud_big_data.configure(state="normal")
        except (AttributeError, tk.TclError):
            pass

    def _stabilize_footer_link_height(self) -> None:
        links = getattr(self, "footer_links", None)
        bili = getattr(self, "footer_bilibili", None)
        cloud = getattr(self, "footer_cloud_big_data", None)
        if links is None or bili is None or cloud is None:
            return
        try:
            links.update_idletasks()
            links.configure(height=max(bili.winfo_reqheight(), cloud.winfo_reqheight()))
            links.pack_propagate(False)
        except tk.TclError:
            pass

    def _layout_footer_groups(
        self,
        event: tk.Event[Any] | None = None,
        *,
        force: bool = False,
    ) -> None:
        row = getattr(self, "footer_row", None)
        meta = getattr(self, "footer_meta_row", None)
        actions = getattr(self, "footer_actions_row", None)
        if row is None or meta is None or actions is None:
            return
        try:
            width = int(getattr(event, "width", 0) or row.winfo_width())
            if width <= 1:
                return
            links_width = (
                self.footer_bilibili.winfo_reqwidth()
                + self.footer_cloud_big_data.winfo_reqwidth()
                + 4
            )
            meta_width = self.footer.winfo_reqwidth() + links_width
            actions_width = (
                self.support_frame.winfo_reqwidth()
                + self.resize_grip.winfo_reqwidth()
                + 10
            )
            wrapped = meta_width + actions_width > width
            if (
                not force
                and wrapped == bool(getattr(self, "_footer_groups_wrapped", False))
            ):
                return
            meta.pack_forget()
            actions.pack_forget()
            if wrapped:
                meta.pack(side="top", fill="x")
                actions.pack(side="top", fill="x")
            else:
                actions.pack(side="right")
                meta.pack(side="left", fill="x", expand=True)
            self._footer_groups_wrapped = wrapped
        except (AttributeError, tk.TclError):
            return

    def _finish_cloud_big_data_open(self, *, opened: bool) -> None:
        self._cloud_big_data_opening = False
        self._cloud_big_data_reset_after_id = None
        button = getattr(self, "footer_cloud_big_data", None)
        if button is not None:
            try:
                button.configure(
                    state="normal",
                    fg=DIM,
                    activeforeground=ACCENT,
                    disabledforeground=DIM,
                    cursor="hand2",
                )
            except tk.TclError:
                pass
        tip = getattr(self, "footer_cloud_big_data_tip", None)
        if tip is not None:
            tip.set_text(
                CLOUD_BIG_DATA_TIP_TEXT
                if opened
                else "大数据打开失败；请检查默认浏览器后重试"
            )

    def _open_cloud_big_data(
        self,
        _event: tk.Event[Any] | None = None,
    ) -> str:
        """Open the existing public-data destination with a bounded click burst."""
        if bool(getattr(self, "_cloud_big_data_opening", False)):
            return "break"
        self._cloud_big_data_opening = True
        button = getattr(self, "footer_cloud_big_data", None)
        if button is not None:
            try:
                button.configure(
                    state="disabled",
                    disabledforeground=DIM,
                    cursor="arrow",
                )
            except tk.TclError:
                pass
        tip = getattr(self, "footer_cloud_big_data_tip", None)
        if tip is not None:
            tip.set_text("正在打开大数据…")
        try:
            opened = bool(
                webbrowser.open(PUBLIC_BIG_DATA_URL, new=2, autoraise=True)
            )
        except Exception:  # noqa: BLE001 - browser integration must fail soft
            opened = False
        if not opened:
            if button is not None:
                try:
                    button.configure(disabledforeground=BAD)
                except tk.TclError:
                    pass
            if tip is not None:
                tip.set_text("大数据打开失败；请检查默认浏览器后重试")
        try:
            self._cloud_big_data_reset_after_id = self.root.after(
                CLOUD_BIG_DATA_OPEN_COOLDOWN_MS,
                lambda: self._finish_cloud_big_data_open(opened=opened),
            )
        except (AttributeError, tk.TclError):
            self._finish_cloud_big_data_open(opened=opened)
        return "break"

    def export_diagnostic_package(self, _event: tk.Event[Any] | None = None) -> str:
        export_started = time.perf_counter()
        snapshot = self._last_live_snapshot or _read_json(self.snapshot_path)
        if not snapshot:
            snapshot = {
                "schema_version": None,
                "created_at": time.time(),
                "session_id": "no_snapshot",
                "phase": "missing_snapshot",
                "file": None,
                "ui_contract": {
                    "context": {
                        "phase": "missing_snapshot",
                        "session_id": "no_snapshot",
                    },
                },
            }
        current_summary = self._last_summary or self._last_live_summary or {}
        try:
            export_path = _write_diagnostic_export(
                snapshot=snapshot,
                snapshot_path=self.snapshot_path,
                current_summary=current_summary,
                diagnostic_profile=getattr(
                    self,
                    "diagnostic_profile",
                    DEFAULT_DIAGNOSTIC_PROFILE,
                ),
                show_taskbar=bool(getattr(self, "show_taskbar", False)),
            )
            if isinstance(current_summary, dict):
                self._mark_summary_performance(
                    current_summary,
                    "export_ms",
                    export_started,
                )
        except Exception as exc:  # noqa: BLE001 - keep UI usable
            if hasattr(self, "export_diag_tip"):
                self.export_diag_tip.set_text(f"导出失败: {exc}")
            if hasattr(self, "status"):
                self.status.configure(text="导出失败")
            return "break"
        self.last_diagnostic_export_path = export_path
        if hasattr(self, "export_diag_tip"):
            self.export_diag_tip.set_text(
                f"已导出诊断：{export_path.resolve()}"
            )
        if hasattr(self, "status"):
            self.status.configure(text="已导出诊断")
        if hasattr(self, "manual_status"):
            self.manual_status.configure(text=f"诊断包：{export_path.name}", fg=ACCENT)
        _show_export_in_explorer(export_path)
        return "break"

    def _run_exit_cleanup(self) -> None:
        liveness_lock = self._ensure_ref_engine_liveness_lock()
        executors: list[Any] = []
        with liveness_lock:
            if getattr(self, "_exit_cleanup_complete", False):
                return
            # Cancellation publication is linearized with registration/submit.
            self._exit_cleanup_done = True
            generation = getattr(self, "_ref_engine_liveness_generation", 0)
            if type(generation) is not int or generation < 0:
                generation = 0
            self._ref_engine_liveness_generation = generation + 1
            self._ref_engine_liveness_token = None
            self._ref_engine_liveness_state = "closed"
            self._ref_engine_executor_mode = None
            results = getattr(self, "_ref_engine_liveness_results", None)
            if results is not None:
                while True:
                    try:
                        row = results.get_nowait()
                    except queue.Empty:
                        break
                    executor = row.get("executor") if isinstance(row, dict) else None
                    if executor is not None and all(
                        executor is not item for item in executors
                    ):
                        executors.append(executor)
            for name in (
                "_summary_worker_executor",
                "_ref_engine_executor",
                "_ref_engine_transient_executor",
                "_ref_engine_liveness_starting_executor",
            ):
                executor = getattr(self, name, None)
                if executor is not None and all(executor is not item for item in executors):
                    executors.append(executor)
            for executor in tuple(getattr(self, "_ref_engine_retiring_executors", {}).values()):
                if all(executor is not item for item in executors):
                    executors.append(executor)
        self._clear_compute_pending("exit_cleanup")
        watched_pid_group = getattr(self, "_watched_pid_group", None)
        if watched_pid_group is not None:
            watched_pid_group.close()
        pool_results = [_shutdown_ref_engine_executor(executor, hard=True, owner=self) for executor in executors]
        with liveness_lock:
            for executor, reaped in zip(executors, pool_results):
                if not reaped:
                    continue
                for name in ("_summary_worker_executor", "_ref_engine_executor",
                             "_ref_engine_transient_executor", "_ref_engine_liveness_starting_executor"):
                    if getattr(self, name, None) is executor:
                        setattr(self, name, None)
            compute_threads = getattr(self, "_ref_engine_compute_threads", {})
            for identity, thread in tuple(compute_threads.items()):
                try:
                    # These are the actual live/manual raw Thread producers.
                    # Never kill or detach them; the original Tk callback waits
                    # for their calculation and result-queue publication.
                    thread.join(timeout=0.0)
                    if thread.is_alive() is False:
                        compute_threads.pop(identity, None)
                except Exception:
                    pass
            self._group_exit_pools_reaped = (
                all(result is True for result in pool_results)
                and not getattr(self, "_ref_engine_retiring_executors", {})
                and not getattr(self, "_ref_engine_liveness_inflight", set())
                and not compute_threads
            )
            self._exit_cleanup_complete = self._group_exit_pools_reaped
        if getattr(self, "keep_monitor_on_close", False):
            return
        if getattr(self, "_exit_monitor_cleanup_requested", False):
            return
        self._exit_monitor_cleanup_requested = True
        pids = list(getattr(self, "_stop_pids_on_exit", ()))
        lock_paths = list(getattr(self, "_cleanup_lock_paths", ()))
        monitor_lock = _monitor_lock_path(self.snapshot_path)
        fallback_pid = _pid_from_monitor_lock(monitor_lock)
        if fallback_pid and fallback_pid not in pids:
            pids.append(fallback_pid)
        if monitor_lock not in lock_paths:
            lock_paths.append(monitor_lock)
        _cleanup_exit_targets(tuple(pids), tuple(lock_paths))
        _unload_windivert_driver_under(_package_root_with_monitor(self.snapshot_path))

    def _scroll_detail_minimap(self, event: tk.Event[Any]) -> str:
        delta = -1 if event.delta > 0 else 1
        self.minimap_canvas.yview_scroll(delta, "units")
        return "break"

    def _scroll_pinned_minimap(self, event: tk.Event[Any]) -> str:
        if self._pinned_canvas is None:
            return "break"
        delta = -1 if event.delta > 0 else 1
        self._pinned_canvas.yview_scroll(delta, "units")
        return "break"

    def _set_manual_button_state(self) -> None:
        if not hasattr(self, "manual_button"):
            return
        manual_mode = bool(self._manual_active or getattr(self, "_manual_edit_enabled", False))
        if manual_mode:
            self.manual_button.configure(
                text="实时",
                bg="#ffe1a8",
                fg=BUTTON_DARK_FG,
                highlightbackground="#fff0c4",
            )
            if hasattr(self, "manual_tip"):
                self.manual_tip.set_text(MANUAL_RETURN_TIP_TEXT)
        else:
            self.manual_button.configure(
                text="手填",
                bg=WARM,
                fg=BUTTON_DARK_FG,
                highlightbackground=WARM,
            )
            if hasattr(self, "manual_tip"):
                self.manual_tip.set_text(MANUAL_TIP_TEXT)

    def _set_manual_edit_enabled(self, enabled: bool) -> None:
        self._manual_edit_enabled = bool(enabled)
        if hasattr(self, "manual_entries"):
            state = "normal" if enabled else "disabled"
            for entry in self.manual_entries.values():
                try:
                    entry.configure(
                        state=state,
                        disabledbackground=PANEL_SOFT,
                        disabledforeground=DIM,
                    )
                except tk.TclError:
                    pass
                except AttributeError:
                    pass
        if hasattr(self, "manual_buttons"):
            for label, button in self.manual_buttons.items():
                target_state = "normal"
                if label in {"应用并启用", "填入当前"} and not enabled:
                    target_state = "disabled"
                if label == "清结算":
                    target_state = "disabled"
                try:
                    button.configure(state=target_state)
                except tk.TclError:
                    pass
                except AttributeError:
                    pass
        self._set_manual_settlement_button_state()
        self._set_manual_button_state()

    def _set_manual_settlement_button_state(self) -> None:
        if not hasattr(self, "manual_buttons"):
            return
        button = self.manual_buttons.get("清结算")
        if button is None:
            return
        data = self._last_summary or self._last_live_summary or {}
        active = self._is_settlement_summary(data)
        enabled = bool(active and getattr(self, "_manual_edit_enabled", False))
        try:
            button.configure(
                state="normal" if enabled else "disabled",
                fg=ACCENT if enabled else DIM,
                bg=MANUAL_BG,
                highlightbackground=ACCENT if enabled else BORDER,
            )
        except tk.TclError:
            pass
        except AttributeError:
            pass
        if hasattr(self, "manual_clear_settlement_tip"):
            self.manual_clear_settlement_tip.set_text(
                MANUAL_CLEAR_SETTLEMENT_TIP
                if active
                else MANUAL_CLEAR_SETTLEMENT_INACTIVE_TIP
            )

    def _is_settlement_summary(self, data: dict[str, Any]) -> bool:
        context = data.get("context") if isinstance(data.get("context"), dict) else {}
        truth = data.get("truth") if isinstance(data.get("truth"), dict) else {}
        return _text(context.get("phase"), "") == "settled" and bool(truth.get("available"))

    def _settlement_values_are_hidden(self, data: dict[str, Any]) -> bool:
        return bool(getattr(self, "settlement_values_hidden", False) and self._is_settlement_summary(data))

    def _settlement_display_value(self, data: dict[str, Any], value: Any) -> str:
        return "已隐藏" if self._settlement_values_are_hidden(data) else _text(value)

    def _set_settlement_button_state(self, data: dict[str, Any] | None = None) -> None:
        if not hasattr(self, "settlement_button"):
            return
        hidden = bool(getattr(self, "settlement_values_hidden", False))
        self.settlement_button.configure(
            text="显价" if hidden else "藏价",
            fg=WARM if hidden else ACCENT,
            bg=PANEL_MUTED if hidden else PANEL_SOFT,
            highlightbackground=WARM if hidden else BORDER,
        )
        if hasattr(self, "settlement_tip"):
            self.settlement_tip.set_text(SETTLEMENT_SHOW_TIP if hidden else SETTLEMENT_HIDE_TIP)

    def toggle_settlement_values(self, _event: tk.Event[Any] | None = None) -> str:
        data = self._last_summary or self._last_live_summary or {}
        self.settlement_values_hidden = not bool(getattr(self, "settlement_values_hidden", False))
        self._set_settlement_button_state(data)
        if self._is_settlement_summary(data):
            self.render(data)
        return "break"

    def toggle_manual_mode(self, _event: tk.Event[Any] | None = None) -> str:
        if self._manual_active or getattr(self, "_manual_edit_enabled", False):
            return self.return_to_live_mode()
        return self.open_manual_panel()

    def open_manual_panel(self, _event: tk.Event[Any] | None = None) -> str:
        with self._atomic_layout_update():
            return self._open_manual_panel_now(_event)

    def _open_manual_panel_now(self, _event: tk.Event[Any] | None = None) -> str:
        # 展开判定以 self.details_expanded(模式标志)为唯一真值来源，【不要】拿 winfo_ismapped() 反推。
        #
        # 作者 2026-07-11 实测的死锁：第一次点「手填」能展开，之后永久打不开，按钮却照样翻成「实时」。
        # 根因(审计窗口真 Tk A/B 定位)：details_card 是 _card() 造的圆角卡片，返回的是内层 content
        # Frame —— pack/pack_forget 被代理到外层 holder Canvas，但 winfo_ismapped() 当时【没被代理】。
        # 而 content 的 mapped 标志【只在走过手填之后】会与 holder 脱节并残留为 1(见 _card() 里的
        # 三路实测：走详情收起 content=0 正常，走"手填→实时"收起 content=1 残留)，于是旧代码把
        # details_expanded 反推成 True -> auto_expanded 恒为 False -> toggle_details() 永不被调用
        # -> 面板再也打不开。这解释了为什么故障总是【用过一次手填之后】才出现。
        # (_card() 现已把 winfo_ismapped 一并代理到 holder，那条才是真值；但这里仍只认模式标志，
        #  免得再被任何布局实现细节带偏。)
        #
        # 仅当标志说「已展开」而卡片其实没被 pack(真矛盾)时，才纠偏成 False。
        if hasattr(self, "details_card") and self.details_expanded:
            try:
                if not self.details_card.winfo_ismapped():
                    self.details_expanded = False
            except tk.TclError:
                pass
        # Remember whether entering manual auto-expanded the panel so that
        # returning to live can symmetrically collapse back to mini. If the user
        # had already expanded details on their own, leave that state untouched.
        auto_expanded = not self.details_expanded
        if auto_expanded:
            self.toggle_details()
        self._manual_auto_expanded_details = auto_expanded
        data = self._last_summary or self._last_live_summary or {}
        should_prefill_empty = (
            hasattr(self, "manual_entries")
            and not self._has_manual_inputs()
            and not bool(getattr(self, "_manual_dirty_fields", set()))
            and not bool(getattr(self, "_manual_autofill_values", {}))
            and any(
                value not in (None, "")
                for value in self._manual_values_from_summary(data).values()
            )
        )
        settlement_edit = self._is_settlement_summary(data)
        if settlement_edit:
            self._manual_settlement_edit_unlocked = True
            if not _text(getattr(self, "_manual_live_session_id", ""), "").strip():
                self._manual_live_session_id = self._summary_session_id(data)
        self._set_manual_edit_enabled(True)
        if should_prefill_empty:
            self.prefill_manual_inputs()
        # 已有值有两种来源：刚 prefill，或之前锁定态的只读回填(_auto_sync)。两者都说明
        # 进入手填时字段非空，应提示「可覆盖」而非「待填写」。prefill 自带「已填入当前，
        # 待应用」状态，故仅在 prefill 未跑而字段已被回填时补一条覆盖提示。
        prefilled = self._has_manual_inputs()
        if hasattr(self, "manual_status"):
            if prefilled and not should_prefill_empty:
                self.manual_status.configure(text="可编辑，覆盖实时值", fg=WARM)
            elif not prefilled:
                self.manual_status.configure(
                    text="结算页手填，待填写" if settlement_edit else "手动模式，待填写",
                    fg=WARM,
                )
        if hasattr(self, "manual_card"):
            if getattr(self, "details_composition_view_enabled", False):
                self.manual_card.pack(
                    fill="x",
                    pady=(5, 0),
                    before=self.details_top,
                )
                self.details_scroll_canvas.yview_moveto(0.0)
            self.manual_card.configure(highlightbackground=WARM, highlightthickness=2)
        self._set_manual_settlement_button_state()
        # The expand pass above fitted the window height while the manual panel was
        # still in its shorter read-only state; enabling edit mode (entries/buttons,
        # status row) grows it. Grow the window now so the mid-window panel isn't
        # clipped below the fold (the other half of the "手填 shows nothing" report).
        self._grow_window_for_manual()
        return "break"

    def _grow_window_for_manual(self) -> None:
        """Grow the window height (never shrink) to fit the manual panel once it is
        visible and edit-enabled. Mirrors the measure/clamp in _settle_initial_layout."""
        if not hasattr(self, "root") or not self.details_expanded:
            return
        try:
            self.root.update_idletasks()
        except tk.TclError:
            return
        width, height = self._current_window_size()
        needed_h = self._measure_window_height_for_width(width, probe_height=height)
        _, work_h = self._screen_size()
        final_h = min(max(height, needed_h), max(360, work_h - 72))
        if abs(final_h - height) > 2:
            self._apply_window_geometry(f"{width}x{final_h}")
            if self._custom_details_size is not None:
                self._custom_details_size = (width, final_h)

    def return_to_live_mode(self, _event: tk.Event[Any] | None = None) -> str:
        with self._atomic_layout_update():
            return self._return_to_live_mode_now(_event)

    def _return_to_live_mode_now(self, _event: tk.Event[Any] | None = None) -> str:
        self._manual_active = False
        self._manual_settlement_edit_unlocked = False
        self._manual_snapshot = {}
        self._manual_summary = {}
        self._set_manual_edit_enabled(False)
        if (
            getattr(self, "details_composition_view_enabled", False)
            and hasattr(self, "manual_card")
        ):
            self.manual_card.pack_forget()
            self.details_scroll_canvas.yview_moveto(0.0)
        if self._last_live_summary:
            self._auto_sync_manual_inputs(self._last_live_summary)
        if hasattr(self, "manual_status"):
            self.manual_status.configure(text="实时模式，手填保留", fg=DIM)
        self._restore_manual_card_border()
        # Collapse back to mini only if entering manual auto-expanded the panel
        # (mirrors open_manual_panel, which expands via toggle_details).
        if getattr(self, "_manual_auto_expanded_details", False):
            self._manual_auto_expanded_details = False
            if self.details_expanded:
                self.toggle_details()
        elif (
            getattr(self, "details_expanded", False)
            and getattr(self, "details_composition_view_enabled", False)
        ):
            self._fit_details_window_to_content()
            self._nudge_window_onscreen()
        if self._last_live_summary:
            self._last_summary = self._last_live_summary
            if self._last_live_summary.get("status") == "stale_snapshot":
                self.render_standby(self._last_live_summary)
            else:
                self.render(self._last_live_summary)
        else:
            self._last_summary = {}
            self.render_missing("等待 latest_snapshot.json")
        return "break"

    def _restore_manual_card_border(self) -> None:
        if hasattr(self, "manual_card"):
            self.manual_card.configure(highlightbackground=BORDER, highlightthickness=1)

    def _meta_row(self, parent: tk.Widget, label: str) -> tk.Label:
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", pady=(0, 6))
        tk.Label(
            row,
            text=label,
            bg=PANEL,
            fg=DIM,
            font=(FONT_UI, 7),
            anchor="w",
            width=4,
        ).pack(side="left")
        value = tk.Label(
            row,
            text="-",
            bg=PANEL,
            fg=TEXT,
            font=(FONT_UI, 8, "bold"),
            anchor="w",
            justify="left",
            wraplength=230,
        )
        value.pack(side="left", fill="x", expand=True)
        return value

    def _build_manual_panel(self, parent: tk.Widget) -> None:
        manual_bg = MANUAL_BG
        manual_input_bg = MANUAL_INPUT_BG
        card = self._card(parent, bg=manual_bg, padx=7, pady=7)
        self.manual_card = card
        card.pack(fill="x", pady=(5, 0))
        tk.Frame(card, bg=WARM, height=2).pack(fill="x", pady=(0, 4))
        header = tk.Frame(card, bg=manual_bg)
        header.pack(fill="x", pady=(0, 4))
        title_box = tk.Frame(header, bg=manual_bg)
        title_box.pack(side="left", fill="x", expand=True)
        tk.Label(
            title_box,
            text="手动填写 / 断网备用",
            bg=manual_bg,
            fg=WARM,
            font=(FONT_UI, 8, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            title_box,
            text="可只填总件/总格；补均格/件数/均价/总价会进一步锁定",
            bg=manual_bg,
            fg=MUTED,
            font=(FONT_UI, 6),
            anchor="w",
        ).pack(fill="x", pady=(1, 0))
        self.manual_status = tk.Label(
            header,
            text="未启用",
            bg=MANUAL_STATUS_BG,
            fg=WARM,
            padx=7,
            pady=2,
            font=(FONT_UI, 7),
            anchor="e",
        )
        self.manual_status.pack(side="right")

        self.manual_entries: dict[str, tk.Entry] = {}
        self.manual_vars: dict[str, tk.StringVar] = {}
        self.manual_buttons: dict[str, tk.Button] = {}
        buttons = tk.Frame(card, bg=manual_bg)
        buttons.pack(fill="x", pady=(0, 5))
        for text, command, color in (
            ("应用并启用", self.apply_manual_inputs, WARM),
            ("填入当前", self.prefill_manual_inputs, ACCENT),
            ("清空手动", self.clear_manual_inputs, DIM),
            ("清结算", self.clear_settlement_manual_values, DIM),
        ):
            is_primary = text == "应用并启用"
            button = tk.Button(
                buttons,
                text=text,
                command=command,
                bg=WARM if is_primary else manual_bg,
                fg=BUTTON_DARK_FG if is_primary else color,
                activebackground="#ffd08d" if is_primary else HERO_BUTTON_ACTIVE,
                activeforeground=BUTTON_DARK_FG if is_primary else TEXT,
                relief="flat",
                borderwidth=0,
                padx=9 if is_primary else 7,
                pady=3 if is_primary else 2,
                font=(FONT_UI, 8, "bold"),
                highlightthickness=1,
                highlightbackground=WARM if is_primary else BORDER,
            )
            button.pack(side="left", padx=(0, 6))
            self.manual_buttons[text] = button
            if text == "清结算":
                self.manual_clear_settlement_tip = HoverTip(
                    button,
                    MANUAL_CLEAR_SETTLEMENT_INACTIVE_TIP,
                )

        form = tk.Frame(card, bg=manual_bg)
        form.pack(fill="x")

        def add_manual_entry(parent: tk.Widget, key: str, default: str = "") -> tk.Entry:
            var = tk.StringVar(value=default)
            entry = tk.Entry(
                parent,
                width=6,
                textvariable=var,
                bg=manual_input_bg,
                fg=TEXT,
                insertbackground=TEXT,
                relief="flat",
                borderwidth=0,
                font=(FONT_UI, 8),
                justify="center",
            )
            entry.pack(fill="x", expand=True)
            entry.bind(
                "<KeyRelease>",
                lambda event, field=key: self._mark_manual_pending(event, field=field),
                add="+",
            )
            entry.bind(
                "<FocusIn>",
                lambda event, field=key: self._focus_manual_entry(event, field=field),
                add="+",
            )
            entry.bind("<Return>", self._apply_manual_from_event, add="+")
            var.trace_add(
                "write",
                lambda *_args, overlay=self, field=key: overlay._on_manual_var_write(field),
            )
            self.manual_vars[key] = var
            self.manual_entries[key] = entry
            return entry

        base_grid = tk.Frame(form, bg=manual_bg)
        base_grid.pack(fill="x", pady=(0, 5))
        for col in range(len(MANUAL_BASE_FIELDS)):
            base_grid.columnconfigure(col, weight=1, uniform="manual_base")
        for col, (key, label, default) in enumerate(MANUAL_BASE_FIELDS):
            cell = tk.Frame(base_grid, bg=manual_bg)
            cell.grid(
                row=0,
                column=col,
                sticky="ew",
                padx=(0, 5 if col < len(MANUAL_BASE_FIELDS) - 1 else 0),
            )
            tk.Label(
                cell,
                text=label,
                bg=manual_bg,
                fg=MANUAL_LABEL_FG,
                font=(FONT_UI, 7, "bold" if key in {"total_count", "total_cells"} else "normal"),
                anchor="w",
            ).pack(fill="x", pady=(0, 1))
            add_manual_entry(cell, key, default)

        quality_table = tk.Frame(form, bg=manual_bg)
        quality_table.pack(fill="x")
        column_specs = (
            ("品质", 0, 54),
            ("均格", 1, 1),
            ("件", 1, 1),
            ("格", 1, 1),
            ("均价", 1, 1),
            ("总价", 1, 1),
        )
        for col, (_label, weight, minsize) in enumerate(column_specs):
            quality_table.columnconfigure(col, weight=weight, minsize=minsize)
        for col, (label, _weight, _minsize) in enumerate(column_specs):
            tk.Label(
                quality_table,
                text=label,
                bg=manual_bg,
                fg=WARM if col == 0 else DIM,
                font=(FONT_UI, 7, "bold"),
                anchor="center" if col else "w",
            ).grid(
                row=0,
                column=col,
                sticky="ew",
                padx=(0, 5 if col < len(column_specs) - 1 else 0),
                pady=(0, 2),
            )

        value_keys_by_label = {
            label: (avg_value_key, value_sum_key)
            for label, avg_value_key, value_sum_key in MANUAL_VALUE_ROWS
        }
        for row, (label, avg_key, count_key, cells_key) in enumerate(MANUAL_QUALITY_ROWS, start=1):
            tk.Label(
                quality_table,
                text=label,
                bg=manual_bg,
                fg=MANUAL_LABEL_FG,
                font=(FONT_UI, 7, "bold"),
                anchor="w",
            ).grid(row=row, column=0, sticky="ew", padx=(0, 5), pady=(0, 3))
            for col, key in enumerate((avg_key, count_key, cells_key), start=1):
                cell = tk.Frame(quality_table, bg=manual_bg)
                cell.grid(row=row, column=col, sticky="ew", padx=(0, 5), pady=(0, 3))
                add_manual_entry(cell, key)
            value_keys = value_keys_by_label.get(label)
            for col, key in enumerate(value_keys or ("", ""), start=4):
                cell = tk.Frame(quality_table, bg=manual_bg)
                cell.grid(
                    row=row,
                    column=col,
                    sticky="ew",
                    padx=(0, 5 if col < len(column_specs) - 1 else 0),
                    pady=(0, 3),
                )
                if key:
                    add_manual_entry(cell, key)

        extra_row = tk.Frame(form, bg=manual_bg)
        extra_row.pack(fill="x", pady=(2, 0))
        tk.Label(
            extra_row,
            text="合计",
            bg=manual_bg,
            fg=WARM,
            font=(FONT_UI, 7, "bold"),
            anchor="w",
            width=6,
        ).pack(side="left")
        for key, label, default in MANUAL_EXTRA_FIELDS:
            tk.Label(
                extra_row,
                text=label,
                bg=manual_bg,
                fg=MANUAL_LABEL_FG,
                font=(FONT_UI, 7),
                anchor="w",
            ).pack(side="left", padx=(0, 4))
            cell = tk.Frame(extra_row, bg=manual_bg)
            cell.pack(side="left", fill="x", expand=True)
            add_manual_entry(cell, key, default)
        self._set_manual_edit_enabled(False)

    def _has_manual_inputs(self) -> bool:
        for key, entry in self.manual_entries.items():
            value = entry.get().strip()
            if key == "hero" and value.lower() == "ahmed":
                continue
            if value:
                return True
        return False

    def _on_manual_var_write(self, field: str) -> None:
        if self._manual_programmatic_update:
            return
        self._manual_input_revision = int(getattr(self, "_manual_input_revision", 0)) + 1
        self._manual_dirty_fields.add(field)
        try:
            validation_error = self._sync_manual_derived_fields(trigger_field=field)
        except (ArithmeticError, TypeError, ValueError):
            validation_error = "输入无效：派生计算失败"
        if validation_error:
            if hasattr(self, "manual_status"):
                self.manual_status.configure(text=validation_error, fg=BAD)
            return
        self._mark_manual_pending()

    def _can_autofill_manual_field(self, key: str, *, allow_dirty_empty: bool = False) -> bool:
        current = self._manual_entry_text(key)
        last_auto = self._manual_autofill_values.get(key)
        if not current:
            if allow_dirty_empty:
                return True
            return key not in self._manual_dirty_fields
        return last_auto is not None and current == last_auto

    def _focus_manual_entry(self, event: tk.Event[Any], *, field: str) -> str | None:
        if self._manual_programmatic_update:
            return None
        widget = getattr(event, "widget", None)
        if not isinstance(widget, tk.Entry):
            return None
        try:
            if str(widget.cget("state")) != "normal":
                return None
        except (tk.TclError, AttributeError):
            return None
        if not widget.get():
            return None
        widget.after_idle(lambda w=widget: self._select_manual_entry_all(w))
        return None

    def _select_manual_entry_all(self, widget: tk.Entry) -> None:
        try:
            widget.selection_range(0, "end")
            widget.icursor("end")
        except (tk.TclError, AttributeError):
            return

    def _set_manual_derived_entry(
        self,
        key: str,
        value: Any,
        *,
        allow_dirty_empty: bool = False,
    ) -> None:
        if value in (None, "") or not self._can_autofill_manual_field(
            key,
            allow_dirty_empty=allow_dirty_empty,
        ):
            return
        self._set_manual_entry(key, _format_manual_number(value), track_auto=True)

    def _set_empty_manual_entry_auto(self, key: str, value: Any) -> None:
        if self._manual_entry_text(key):
            return
        entry = self.manual_entries.get(key)
        if entry is None:
            return
        var = getattr(self, "manual_vars", {}).get(key)
        if var is None and not (hasattr(entry, "delete") and hasattr(entry, "insert")):
            return
        self._set_manual_entry(key, _format_manual_number(value), track_auto=True)

    def _manual_quality_zero_values(self, key: str) -> dict[str, float | int | None]:
        return {
            "avg": _to_optional_float(self._manual_entry_text(f"{key}_avg")),
            "count": _to_optional_int(self._manual_entry_text(f"{key}_count")),
            "cells": _to_optional_int(self._manual_entry_text(f"{key}_cells")),
            "avg_value": _to_optional_float(self._manual_entry_text(f"{key}_avg_value")),
            "value_sum": _to_optional_float(self._manual_entry_text(f"{key}_value_sum")),
        }

    def _sync_zero_quality_row(self, key: str) -> None:
        values = self._manual_quality_zero_values(key)
        has_zero = any(value == 0 for value in values.values())
        has_nonzero = any(value not in (None, 0) for value in values.values())
        if not has_zero or has_nonzero:
            return
        for suffix in ("avg", "count", "cells", "avg_value", "value_sum"):
            self._set_empty_manual_entry_auto(f"{key}_{suffix}", 0)

    def _sync_manual_derived_fields(
        self,
        *,
        trigger_field: str | None = None,
    ) -> str | None:
        if not hasattr(self, "manual_entries") or self._manual_programmatic_update:
            return None
        all_quality_keys = (*SPLIT_QUALITY_INPUT_KEYS, *QUALITY_INPUT_KEYS)
        full_sync = trigger_field is None
        sync_total = full_sync or trigger_field in {
            "total_count",
            "total_cells",
            "total_avg",
        }
        quality_keys = all_quality_keys if full_sync else tuple(
            key
            for key in all_quality_keys
            if isinstance(trigger_field, str) and trigger_field.startswith(f"{key}_")
        )
        if sync_total:
            total_count_text = self._manual_entry_text("total_count")
            total_count, total_count_error = _to_manual_count(
                total_count_text,
                "总件",
            )
            if total_count_error and total_count_text:
                return f"输入无效：{total_count_error}"
            total_cells_text = self._manual_entry_text("total_cells")
            total_cells, total_cells_error = _to_manual_nonnegative_float(
                total_cells_text,
                "总格",
            )
            if total_cells_error and total_cells_text:
                return f"输入无效：{total_cells_error}"
            if total_cells is not None and total_cells > _MANUAL_MAX_GRID_CELLS:
                return "输入无效：总格超出可计算范围"
            total_avg_text = self._manual_entry_text("total_avg")
            total_avg, total_avg_error = _to_manual_nonnegative_float(
                total_avg_text,
                "全均格",
            )
            if total_avg_error and total_avg_text:
                return f"输入无效：{total_avg_error}"
            if total_count is not None and total_count > 0:
                if total_avg is not None:
                    if _manual_finite_product(total_avg, total_count) is None:
                        return "输入无效：全均格超出可计算范围"
                    grid_options = _manual_avg_grid_options_from_text(
                        total_count,
                        total_avg,
                        total_avg_text,
                    )
                    if len(grid_options) == 1:
                        self._set_manual_derived_entry(
                            "total_cells",
                            grid_options[0],
                            allow_dirty_empty=trigger_field
                            in {"total_count", "total_avg"},
                        )
                        total_cells = _manual_inline_nonnegative_float(
                            self._manual_entry_text("total_cells")
                        )
                if total_cells is not None:
                    self._set_manual_derived_entry(
                        "total_avg",
                        total_cells / total_count,
                    )

        for key in quality_keys:
            label = _manual_quality_label(key)
            self._sync_zero_quality_row(key)
            count_text = self._manual_entry_text(f"{key}_count")
            count, count_error = _to_manual_count(count_text, f"{label}件")
            if count_error and count_text:
                return f"输入无效：{count_error}"
            cells_text = self._manual_entry_text(f"{key}_cells")
            cells, cells_error = _to_manual_cells(cells_text, f"{label}格")
            if cells_error and cells_text:
                return f"输入无效：{cells_error}"
            avg_text = self._manual_entry_text(f"{key}_avg")
            avg, avg_error = _to_manual_nonnegative_float(
                avg_text,
                f"{label}均格",
            )
            if avg_error and avg_text:
                return f"输入无效：{avg_error}"
            if avg == 0 and count in (None, 0) and cells in (None, 0):
                allow_dirty_empty = trigger_field == f"{key}_avg"
                if count is None:
                    self._set_manual_derived_entry(
                        f"{key}_count",
                        0,
                        allow_dirty_empty=allow_dirty_empty,
                    )
                    count = _manual_inline_count(
                        self._manual_entry_text(f"{key}_count")
                    )
                if cells is None:
                    self._set_manual_derived_entry(
                        f"{key}_cells",
                        0,
                        allow_dirty_empty=allow_dirty_empty,
                    )
                    cells = _manual_inline_cells(
                        self._manual_entry_text(f"{key}_cells")
                    )
            if count is not None and cells is not None:
                if count == 0:
                    if cells == 0:
                        self._set_manual_derived_entry(f"{key}_avg", 0)
                else:
                    self._set_manual_derived_entry(f"{key}_avg", cells / count)
            if count is not None and avg is not None:
                if _manual_finite_product(avg, count) is None:
                    return f"输入无效：{label}均格超出可计算范围"
                grid_options = _manual_avg_grid_options_from_text(count, avg, avg_text)
                if len(grid_options) == 1:
                    self._set_manual_derived_entry(f"{key}_cells", grid_options[0])
            if count is None and avg is not None and cells is not None:
                derived_count, derived_count_error = _manual_avg_count_from_cells_text(
                    avg,
                    cells,
                    avg_text,
                    count_label=f"{label}件",
                )
                if derived_count_error:
                    return f"输入无效：{derived_count_error}"
                if derived_count is not None:
                    self._set_manual_derived_entry(f"{key}_count", derived_count)
        for key in quality_keys:
            label = _manual_quality_label(key)
            self._sync_zero_quality_row(key)
            count_text = self._manual_entry_text(f"{key}_count")
            count, count_error = _to_manual_count(count_text, f"{label}件")
            if count_error and count_text:
                return f"输入无效：{count_error}"
            avg_value_text = self._manual_entry_text(f"{key}_avg_value")
            avg_value, avg_value_error = _to_manual_nonnegative_float(
                avg_value_text,
                f"{label}均价",
            )
            if avg_value_error and avg_value_text:
                return f"输入无效：{avg_value_error}"
            value_sum = _manual_inline_nonnegative_float(
                self._manual_entry_text(f"{key}_value_sum")
            )
            if count is not None:
                if count > 0:
                    if value_sum is not None and avg_value is None:
                        self._set_manual_derived_entry(f"{key}_avg_value", value_sum / count)
                    if avg_value is not None and value_sum is None:
                        derived_value_sum = _manual_finite_product(avg_value, count)
                        if derived_value_sum is None:
                            return f"输入无效：{label}均价超出可计算范围"
                        self._set_manual_derived_entry(
                            f"{key}_value_sum",
                            derived_value_sum,
                        )
                elif count == 0:
                    if value_sum == 0 and avg_value is None:
                        self._set_manual_derived_entry(f"{key}_avg_value", 0)
                    if avg_value == 0 and value_sum is None:
                        self._set_manual_derived_entry(f"{key}_value_sum", 0)
        sync_split_merge = full_sync or any(
            key in SPLIT_QUALITY_INPUT_KEYS for key in quality_keys
        )
        if not sync_split_merge:
            return None
        q1_has_user_value = False
        for field in ("q1_avg", "q1_count", "q1_cells"):
            value = self._manual_entry_text(field)
            if value and self._manual_autofill_values.get(field) != value:
                q1_has_user_value = True
                break
        if not q1_has_user_value:
            white_count = _manual_inline_count(
                self._manual_entry_text("white_count")
            )
            green_count = _manual_inline_count(
                self._manual_entry_text("green_count")
            )
            white_cells = _manual_inline_cells(
                self._manual_entry_text("white_cells")
            )
            green_cells = _manual_inline_cells(
                self._manual_entry_text("green_cells")
            )
            merged_count = None
            merged_cells = None
            if white_count is not None and green_count is not None:
                merged_count = white_count + green_count
                merged_count, merged_count_error = _to_manual_count(
                    merged_count,
                    "白绿件",
                )
                if merged_count_error:
                    return f"输入无效：{merged_count_error}"
                self._set_manual_derived_entry("q1_count", merged_count)
            if white_cells is not None and green_cells is not None:
                merged_cells = white_cells + green_cells
                self._set_manual_derived_entry("q1_cells", merged_cells)
            if merged_count is not None and merged_count > 0 and merged_cells is not None:
                self._set_manual_derived_entry("q1_avg", merged_cells / merged_count)
            elif merged_count == 0 and merged_cells == 0:
                self._set_manual_derived_entry("q1_avg", 0)
        return None

    def _mark_manual_pending(
        self,
        _event: tk.Event[Any] | None = None,
        *,
        field: str | None = None,
    ) -> None:
        if not hasattr(self, "manual_status"):
            return
        if field is not None and not self._manual_programmatic_update:
            self._manual_dirty_fields.add(field)
        if self._manual_active:
            self.manual_status.configure(text="已改动，待应用", fg=WARM)
        elif self._has_manual_inputs():
            self.manual_status.configure(text="待应用", fg=WARM)
        else:
            self.manual_status.configure(text="未启用", fg=WARM)

    def _apply_manual_from_event(self, _event: tk.Event[Any]) -> str:
        self.apply_manual_inputs()
        return "break"

    def _card(
        self,
        parent: tk.Widget,
        *,
        bg: str,
        padx: int = 9,
        pady: int = 8,
        radius: int | None = None,
        outline: str | None = None,
        fit_width: bool = False,
    ) -> tk.Frame:
        # Rounded card: a Canvas paints the rounded-rect background + border; a content
        # Frame holds the children. The content's geometry methods are proxied to the
        # holder so existing callers keep using `card.pack(...)` / `card.grid(...)` and
        # `tk.X(card, ...)` unchanged. Corners show the parent's ground colour.
        try:
            ground = parent.cget("bg")
        except tk.TclError:
            ground = BG
        radius = CARD_CORNER_RADIUS if radius is None else max(2, int(radius))
        inset = max(1, radius - 2)
        holder = tk.Canvas(
            parent, bg=ground, highlightthickness=0, bd=0, width=1, height=1
        )
        content = tk.Frame(holder, bg=bg, padx=padx, pady=pady)
        holder._round_card = {
            "content": content, "bg": bg, "inset": inset, "radius": radius,
            "outline": outline or BORDER, "fit_width": bool(fit_width),
        }
        holder.create_window(inset, inset, anchor="nw", window=content, tags="rc_content")
        for _method in (
            "pack", "pack_configure", "pack_forget",
            "grid", "grid_configure", "grid_forget",
            "place", "place_configure", "place_forget",
        ):
            setattr(content, _method, getattr(holder, _method))
        # winfo_ismapped 必须一起代理，否则它会对调用方【说谎】。
        # 真正被 pack/unpack 的是 holder；content 只是 holder 这块 Canvas 里的嵌入窗口。
        # 通常 holder 被 unpack 时 content 的 mapped 标志会跟着变 0(裸 Canvas+Frame 实测确实如此)，
        # 但【本 overlay 里并非总是】—— 真 Tk 三路实测(Windows Tk 8.6，审计窗口 2026-07-11)：
        #     走「详情」按钮收起：     holder=0  content=0   ← 正常
        #     走「手填 → 实时」收起：  holder=0  content=1   ← ★ content 的标志【残留】
        # 也就是说：只要用过一次手填，content.winfo_ismapped() 就与 holder 脱节并恒为 1，
        # 于是调用方常见的
        #     if not card.winfo_ismapped(): card.pack(...)
        # 守卫永久失效 —— 卡片一旦收起就再也 pack 不回来。这正是作者 2026-07-11 的原话：
        # 「第一次手填切换完，完全切不回去了，包括详情按钮」。
        # 代理到 holder 后，ismapped 恒等于"真正被 pack 的那个控件"的状态，与走哪条路径无关。
        content.winfo_ismapped = holder.winfo_ismapped  # type: ignore[method-assign]
        content.bind("<Configure>", lambda _e, h=holder: self._sync_rounded_card(h))
        holder.bind("<Configure>", lambda _e, h=holder: self._draw_rounded_card(h))
        if not hasattr(self, "_rounded_cards"):
            self._rounded_cards = []
        self._rounded_cards.append(holder)
        # The holder starts 1px tall, so the create_window content sits outside the
        # visible area and never maps -> its <Configure> never fires. Break that deadlock
        # by sizing the holder to the content's *requested* height (valid while unmapped)
        # once the caller has finished adding children (after_idle).
        try:
            self.root.after_idle(lambda h=holder: self._sync_rounded_card(h))
        except (AttributeError, tk.TclError):
            pass
        return content  # type: ignore[return-value]

    def _sync_rounded_card(self, holder: tk.Canvas) -> None:
        data = getattr(holder, "_round_card", None)
        if not data:
            return
        content = data["content"]
        try:
            inset = data["inset"]
            wanted_h = int(
                data.get("fixed_height")
                or (int(content.winfo_reqheight()) + 2 * inset)
            )
            if int(holder.cget("height")) != wanted_h:
                holder.configure(height=wanted_h)  # requested height; grid sticky may grow it
            fixed_width = data.get("fixed_width")
            if fixed_width is not None:
                wanted_w = int(fixed_width)
                if int(holder.cget("width")) != wanted_w:
                    holder.configure(width=wanted_w)
            elif data.get("fit_width"):
                # Buttons size to their text (no fill); cards keep width=1 and stretch via pack.
                wanted_w = int(content.winfo_reqwidth()) + 2 * inset
                if int(holder.cget("width")) != wanted_w:
                    holder.configure(width=wanted_w)
        except tk.TclError:
            return
        self._draw_rounded_card(holder)

    def _draw_rounded_card(self, holder: tk.Canvas) -> None:
        data = getattr(holder, "_round_card", None)
        if not data:
            return
        width = int(holder.winfo_width())
        height = int(holder.winfo_height())
        if width <= 1 or height <= 1:
            return
        inset = data["inset"]
        holder.itemconfigure("rc_content", width=max(1, width - 2 * inset))
        holder.delete("rc_bg")
        holder.create_polygon(
            _round_rect_points(1, 1, width - 1, height - 1, data["radius"]),
            smooth=True,
            splinesteps=16,
            fill=data["bg"],
            outline=data.get("outline", BORDER),
            tags="rc_bg",
        )
        holder.tag_lower("rc_bg")

    def _rounded_button(
        self,
        parent: tk.Widget,
        *,
        text: str,
        bg: str,
        fg: str,
        outline: str | None = None,
        font: tuple[Any, ...] | None = None,
        padx: int = 8,
        pady: int = 4,
        radius: int = 8,
        width: int | None = None,
    ) -> tk.Label:
        """A label styled as a rounded pill button (same canvas machinery as _card).

        Returns the inner Label so callers keep `.bind("<Button-1>", ...)` working;
        geometry calls (`.pack`/`.grid`) are proxied to the rounded holder canvas.

        ``width`` (in text chars) fixes the box size so glyphs of different visual widths
        (e.g. ⟳ vs T vs ×) render as equal-size buttons instead of hugging each glyph.
        """
        try:
            ground = parent.cget("bg")
        except tk.TclError:
            ground = BG
        inset = max(1, radius - 2)
        holder = tk.Canvas(parent, bg=ground, highlightthickness=0, bd=0, width=1, height=1)
        _label_kw = {"width": width} if width is not None else {}
        label = tk.Label(holder, text=text, bg=bg, fg=fg, padx=padx, pady=pady,
                         font=font or (FONT_UI, 8, "bold"), **_label_kw)
        holder._round_card = {
            "content": label, "bg": bg, "inset": inset, "radius": radius,
            "outline": outline or BORDER, "fit_width": True,
        }
        label._rounded_holder = holder  # type: ignore[attr-defined]
        holder.create_window(inset, inset, anchor="nw", window=label, tags="rc_content")
        for _method in (
            "pack", "pack_configure", "pack_forget",
            "grid", "grid_configure", "grid_forget",
            "place", "place_configure", "place_forget",
        ):
            setattr(label, _method, getattr(holder, _method))
        # State changes (active/pinned) and theme recolour reconfigure the label's bg;
        # mirror that onto the rounded pill fill so the two never diverge.
        _orig_configure = label.configure

        def _configure(cnf: Any = None, **kw: Any) -> Any:
            result = _orig_configure(cnf, **kw) if cnf is not None else _orig_configure(**kw)
            new_bg = kw.get("bg", kw.get("background"))
            if new_bg:
                holder._round_card["bg"] = new_bg
                self._draw_rounded_card(holder)
            return result

        label.configure = _configure  # type: ignore[method-assign]
        label.config = _configure  # type: ignore[method-assign]
        label.bind("<Configure>", lambda _e, h=holder: self._sync_rounded_card(h))
        holder.bind("<Configure>", lambda _e, h=holder: self._draw_rounded_card(h))
        if not hasattr(self, "_rounded_cards"):
            self._rounded_cards = []
        self._rounded_cards.append(holder)
        try:
            self.root.after_idle(lambda h=holder: self._sync_rounded_card(h))
        except (AttributeError, tk.TclError):
            pass
        return label

    def _detail_info_row(
        self,
        parent: tk.Widget,
        label: str,
        *,
        bg: str = PANEL,
    ) -> tk.Label:
        row = tk.Frame(parent, bg=bg)
        row.pack(fill="x", pady=1)
        tk.Label(
            row,
            text=label,
            bg=bg,
            fg=DIM,
            font=(FONT_UI, 7),
            anchor="w",
            width=9,
        ).pack(side="left")
        value = tk.Label(
            row,
            text="-",
            bg=bg,
            fg=TEXT,
            font=(FONT_UI, 8),
            anchor="w",
            justify="left",
        )
        value.pack(side="left", fill="x", expand=True)
        return value

    def _build_details_composition_view(self, parent: tk.Widget) -> None:
        self.detail_rows = self._row_card(
            parent,
            "本轮估值",
            (
                "总格",
                "总值",
                "红值",
                "轮廓值",
                "结算",
            ),
        )
        self.detail_rows["_card"].pack(fill="x")

        card = self._card(parent, bg=PANEL, padx=7, pady=6)
        self.detail_composition_card = card
        card.pack(fill="x", pady=(5, 0))
        tk.Frame(card, bg=ACCENT, height=1).pack(fill="x", pady=(0, 4))
        header = tk.Frame(card, bg=PANEL)
        header.pack(fill="x")
        tk.Label(
            header,
            text="品质组合",
            bg=PANEL,
            fg=MUTED,
            font=(FONT_UI, 8, "bold"),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        self.detail_composition_toggle = tk.Label(
            header,
            text="收起",
            bg=PANEL,
            fg=ACCENT,
            font=(FONT_UI, 7, "bold"),
            padx=4,
            cursor="hand2",
        )
        self.detail_composition_toggle.pack(side="right")
        self.detail_composition_toggle.bind(
            "<Button-1>", self._toggle_detail_composition, add="+"
        )

        content = tk.Frame(card, bg=PANEL)
        content.pack(fill="x", pady=(4, 0))
        self.detail_composition_content = content
        filter_row = tk.Frame(content, bg=PANEL)
        filter_row.pack(fill="x", pady=(0, 4))
        self.detail_composition_filter_buttons: dict[str, tk.Label] = {}
        for key, label in (
            ("all", "全部"),
            ("q1", "白绿"),
            ("q3", "蓝"),
            ("q4", "紫"),
            ("q5", "金"),
            ("q6", "红"),
        ):
            button = tk.Label(
                filter_row,
                text=label,
                bg=PANEL_SOFT,
                fg=MUTED,
                font=(FONT_UI, 7, "bold"),
                padx=6,
                pady=2,
                highlightthickness=1,
                highlightbackground=BORDER,
                cursor="hand2",
            )
            button.pack(side="left", padx=(0, 4))
            button.bind(
                "<Button-1>",
                lambda _event, choice=key: self._select_detail_composition_filter(
                    choice
                ),
                add="+",
            )
            self.detail_composition_filter_buttons[key] = button

        table = tk.Frame(content, bg=PANEL)
        table.pack(fill="x")
        for column, weight in enumerate((2, 2, 2, 2, 3)):
            table.columnconfigure(column, weight=weight, uniform="composition")
        for column, title in enumerate(("品质", "件数", "格数", "概率", "状态")):
            tk.Label(
                table,
                text=title,
                bg=PANEL_SOFT,
                fg=DIM,
                font=(FONT_UI, 7, "bold"),
                anchor="w" if column in {0, 4} else "e",
                padx=4,
                pady=2,
            ).grid(row=0, column=column, sticky="ew")

        self.detail_composition_rows: list[dict[str, tk.Widget]] = []
        for row_index in range(8):
            row_bg = PANEL if row_index % 2 == 0 else PANEL_MUTED
            row_widgets: dict[str, tk.Widget] = {}
            for column, key in enumerate(
                ("quality", "count", "cells", "probability", "state")
            ):
                label = tk.Label(
                    table,
                    text="-",
                    bg=row_bg,
                    fg=TEXT if key != "state" else MUTED,
                    font=(FONT_NUMERIC if key in {"count", "cells", "probability"} else FONT_UI, 8),
                    anchor="w" if key in {"quality", "state"} else "e",
                    padx=4,
                    pady=2,
                )
                label.grid(
                    row=row_index + 1,
                    column=column,
                    sticky="ew",
                )
                row_widgets[key] = label
            self.detail_composition_rows.append(row_widgets)

        self.detail_composition_footer = tk.Label(
            content,
            text="等待引擎提供结构化组合",
            bg=PANEL,
            fg=DIM,
            font=(FONT_UI, 7),
            anchor="w",
            justify="left",
            padx=2,
            pady=3,
        )
        self.detail_composition_footer.pack(fill="x", pady=(3, 0))

        diagnostics = self._card(parent, bg=PANEL, padx=7, pady=6)
        self.detail_diagnostics_card = diagnostics
        diagnostics.pack(fill="x", pady=(5, 0))
        tk.Frame(diagnostics, bg=BORDER, height=1).pack(fill="x", pady=(0, 4))
        diagnostic_header = tk.Frame(diagnostics, bg=PANEL)
        diagnostic_header.pack(fill="x")
        tk.Label(
            diagnostic_header,
            text="证据与诊断",
            bg=PANEL,
            fg=MUTED,
            font=(FONT_UI, 8, "bold"),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        self.detail_diagnostics_toggle = tk.Label(
            diagnostic_header,
            text="展开",
            bg=PANEL,
            fg=ACCENT,
            font=(FONT_UI, 7, "bold"),
            padx=4,
            cursor="hand2",
        )
        self.detail_diagnostics_toggle.pack(side="right")
        self.detail_diagnostics_toggle.bind(
            "<Button-1>", self._toggle_detail_diagnostics, add="+"
        )
        self.detail_diagnostics_content = tk.Frame(diagnostics, bg=PANEL)
        self.evidence_rows = {
            "_card": diagnostics,
            "状态与来源": self._detail_info_row(
                self.detail_diagnostics_content, "状态与来源"
            ),
            "输入": self._detail_info_row(self.detail_diagnostics_content, "当前输入"),
            "最近": self._detail_info_row(self.detail_diagnostics_content, "最近事件"),
            "仓库轮廓": self._detail_info_row(
                self.detail_diagnostics_content, "仓库轮廓"
            ),
            "诊断": self._detail_info_row(self.detail_diagnostics_content, "诊断说明"),
        }
        self._detail_diagnostics_collapsed = True
        self._update_detail_composition_filter_styles()
        self._render_detail_composition_rows()

    def _sync_details_viewport_height(self) -> int:
        """Reserve less fixed viewport height as Tk typography grows at high DPI."""
        canvas = getattr(self, "details_scroll_canvas", None)
        if canvas is None:
            return 360
        try:
            tk_scaling = float(self.root.tk.call("tk", "scaling"))
        except (AttributeError, tk.TclError, TypeError, ValueError):
            tk_scaling = 96.0 / 72.0
        dpi_factor = max(1.0, tk_scaling / (96.0 / 72.0))
        height = max(210, min(360, int(round(360 - 200 * (dpi_factor - 1.0)))))
        try:
            canvas.configure(height=height)
        except tk.TclError:
            pass
        return height

    def _sync_details_scroll_region(
        self, _event: tk.Event[Any] | None = None
    ) -> None:
        canvas = getattr(self, "details_scroll_canvas", None)
        window = getattr(self, "_details_scroll_window", None)
        if canvas is None or window is None:
            return
        try:
            canvas.itemconfigure(
                window,
                width=max(1, int(canvas.winfo_width())),
            )
            canvas.configure(scrollregion=canvas.bbox("all"))
        except tk.TclError:
            return

    def _scroll_details_viewport(
        self, event: tk.Event[Any]
    ) -> str | None:
        canvas = getattr(self, "details_scroll_canvas", None)
        if (
            canvas is None
            or not getattr(self, "details_expanded", False)
        ):
            return None
        try:
            left = int(canvas.winfo_rootx())
            top = int(canvas.winfo_rooty())
            right = left + int(canvas.winfo_width())
            bottom = top + int(canvas.winfo_height())
            if not (
                left <= int(event.x_root) < right
                and top <= int(event.y_root) < bottom
            ):
                return None
            first, last = canvas.yview()
            if first <= 0.001 and last >= 0.999:
                return None
            delta = int(getattr(event, "delta", 0))
            if delta == 0:
                return None
            canvas.yview_scroll(-3 if delta > 0 else 3, "units")
            return "break"
        except (AttributeError, tk.TclError, TypeError, ValueError):
            return None

    def _schedule_details_refit(self) -> None:
        if (
            not getattr(self, "details_expanded", False)
            or getattr(self, "_custom_details_size", None) is not None
        ):
            return

        def _refit() -> None:
            if (
                not getattr(self, "details_expanded", False)
                or getattr(self, "_custom_details_size", None) is not None
            ):
                return
            try:
                current_width, _ = self._current_window_size()
                self._fit_details_window_to_content(width=current_width)
                self._nudge_window_onscreen()
            except tk.TclError:
                return

        try:
            self.root.after_idle(_refit)
        except (AttributeError, tk.TclError):
            pass

    def _toggle_detail_composition(
        self, _event: tk.Event[Any] | None = None
    ) -> str:
        self._detail_composition_collapsed = not self._detail_composition_collapsed
        if self._detail_composition_collapsed:
            self.detail_composition_content.pack_forget()
            self.detail_composition_toggle.configure(text="展开")
        else:
            self.detail_composition_content.pack(fill="x", pady=(4, 0))
            self.detail_composition_toggle.configure(text="收起")
        self._schedule_details_refit()
        return "break"

    def _toggle_detail_diagnostics(
        self, _event: tk.Event[Any] | None = None
    ) -> str:
        self._detail_diagnostics_collapsed = not self._detail_diagnostics_collapsed
        if self._detail_diagnostics_collapsed:
            self.detail_diagnostics_content.pack_forget()
            self.detail_diagnostics_toggle.configure(text="展开")
        else:
            self.detail_diagnostics_content.pack(fill="x", pady=(4, 0))
            self.detail_diagnostics_toggle.configure(text="收起")
        self._schedule_details_refit()
        return "break"

    def _select_detail_composition_filter(self, key: str) -> str:
        if key not in {"all", "q1", "q3", "q4", "q5", "q6"}:
            return "break"
        if self._detail_composition_filter == key:
            return "break"
        self._detail_composition_filter = key
        self._update_detail_composition_filter_styles()
        self._render_detail_composition_rows()
        self._schedule_details_refit()
        return "break"

    def _update_detail_composition_filter_styles(self) -> None:
        for key, widget in getattr(
            self, "detail_composition_filter_buttons", {}
        ).items():
            selected = key == self._detail_composition_filter
            widget.configure(
                bg=PANEL if selected else PANEL_SOFT,
                fg=ACCENT if selected else MUTED,
                highlightbackground=ACCENT if selected else BORDER,
            )

    @staticmethod
    def _detail_quality_state_text(
        quality: QualityComposition,
        *,
        candidate_index: int | None = None,
    ) -> str:
        if quality_is_strict_exact(quality):
            return "锁定"
        if quality.status == "loading":
            return "计算中"
        if quality.status == "error":
            return "暂不可用"
        if quality.status == "conflict":
            return "冲突"
        if quality.status == "no_reachable":
            return "无可达"
        if quality.status == "stale":
            return "旧结果"
        if quality.status == "unavailable":
            return "未提供"
        if quality.lock_strength == "lower_bound":
            return "组合下界"
        if candidate_index == 0:
            return "最可能"
        return "候选"

    @staticmethod
    def _detail_quality_hover(quality: QualityComposition) -> str:
        lines = []
        if quality.source:
            lines.append(f"来源：{quality.source}")
        if quality.lock_reason:
            lines.append(f"依据：{quality.lock_reason}")
        if quality.reason:
            lines.append(quality.reason)
        if quality.display_reconciled:
            lines.append("显示值经过单独校准；候选概率仍来自报价 posterior")
        if quality.count_correlation_note:
            lines.append(quality.count_correlation_note)
        return "\n".join(lines)

    def _detail_composition_rows_for_view(
        self, view: DetailCompositionView
    ) -> list[tuple[QualityComposition, Any | None, int | None]]:
        if self._detail_composition_filter == "all":
            return [
                (
                    quality,
                    quality.candidates[0] if quality.candidates else None,
                    0 if quality.candidates else None,
                )
                for quality in view.qualities
            ]
        quality = view.quality(self._detail_composition_filter)
        if not quality.candidates:
            return [(quality, None, None)]
        return [
            (quality, candidate, index)
            for index, candidate in enumerate(quality.candidates[:8])
        ]

    def _detail_composition_footer_text(
        self,
        view: DetailCompositionView,
        rows: list[tuple[QualityComposition, Any | None, int | None]],
    ) -> str:
        if self._detail_composition_filter == "all":
            if view.status in {
                "loading",
                "error",
                "conflict",
                "no_reachable",
                "stale",
                "unavailable",
            } and view.reason:
                return view.reason
            truncated = any(
                quality.truncated or quality.cap_hit for quality in view.qualities
            )
            suffix = " · 部分品质有其他概率" if truncated else ""
            return f"选择品质查看完整候选 · 概率不重新归一化{suffix}"

        quality = rows[0][0]
        if not quality.candidates:
            return quality.reason or self._detail_quality_state_text(quality)
        parts = []
        if quality.shown_probability is not None:
            parts.append(
                f"覆盖 {format_quality_probability(quality, quality.shown_probability)}"
            )
        else:
            parts.append("覆盖率待结构化接口补充")
        if quality.other_probability is not None:
            parts.append(f"其他 {format_probability(quality.other_probability)}")
        if quality.truncated:
            parts.append("已截断")
        if quality.cap_hit:
            parts.append("达到候选上限")
        if quality.lock_reason == "sampled_weighted_posterior":
            # Keep the model's mass accounting visible in the existing footer;
            # full marginal lists remain in the existing probability hovers.
            visible_mass = math.fsum(candidate.probability for _quality, candidate, _index in rows if candidate is not None)
            return "候选" + " · ".join(parts + [f"本页{len(rows)}项 {format_probability(visible_mass)}"])
        if quality.display_reconciled:
            parts.append("显示值另有校准")
        if quality.count_correlation_note:
            parts.append(quality.count_correlation_note)
        if quality.key == "q6" and quality.count_candidates:
            marginal_text = format_quality_count_candidates(quality)
            if marginal_text:
                parts.append(f"红件边际 {marginal_text}")
        return " · ".join(parts)

    def _render_detail_composition_rows(self) -> None:
        rows = self._detail_composition_rows_for_view(
            self._detail_composition_view
        )
        visible_count = min(len(rows), len(self.detail_composition_rows))
        previous_count = getattr(self, "_detail_composition_visible_rows", None)
        for row_index, widgets in enumerate(self.detail_composition_rows):
            if row_index >= visible_count:
                for widget in widgets.values():
                    widget.grid_remove()
                continue

            quality, candidate, candidate_index = rows[row_index]
            values = {
                "quality": quality.label,
                "count": "-" if candidate is None else str(candidate.count),
                "cells": "-" if candidate is None else str(candidate.cells),
                "probability": (
                    (
                        "-"
                        if candidate is None
                        else format_quality_probability(
                            quality,
                            candidate.probability,
                        )
                    )
                ),
                "state": self._detail_quality_state_text(
                    quality, candidate_index=candidate_index
                ),
            }
            hover = self._detail_quality_hover(quality)
            for widget in widgets.values():
                widget.grid()
            for key, value in values.items():
                label = widgets[key]
                label.configure(
                    text=value,
                    fg=(
                        ACCENT
                        if key == "state"
                        and value in {"锁定", "最可能", "组合下界"}
                        else TEXT if key != "state" else MUTED
                    ),
                )
                if key in {"quality", "state"}:
                    self._set_label(
                        label, value, limit=18, hover=hover or None  # type: ignore[arg-type]
                    )

        self._detail_composition_visible_rows = visible_count
        self.detail_composition_footer.configure(
            text=self._detail_composition_footer_text(
                self._detail_composition_view, rows
            )
        )
        if previous_count is not None and previous_count != visible_count:
            self._schedule_details_refit()

    def _render_detail_composition(self, data: dict[str, Any]) -> None:
        if not self.details_composition_view_enabled:
            return
        self._detail_composition_view = detail_composition_view(data)
        self._render_detail_composition_rows()

    def _row_card(
        self,
        parent: tk.Widget,
        title: str,
        labels: tuple[str | tuple[str, str], ...],
        *,
        value_font_pt: int = SUMMARY_VALUE_FONT_SIZE,
        state_badge_keys: frozenset[str] = frozenset(),
    ) -> dict[str, tk.Label]:
        card = self._card(parent, bg=PANEL, padx=7, pady=6)
        tk.Frame(card, bg=ACCENT if title in {"当前建议", "证据", "本轮估值"} else BAD, height=1).pack(
            fill="x",
            pady=(0, 4),
        )
        tk.Label(
            card,
            text=title,
            bg=PANEL,
            fg=MUTED,
            font=(FONT_UI, 8, "bold"),
            anchor="w",
        ).pack(fill="x", pady=(0, 3))
        rows: dict[str, tk.Label] = {"_card": card}  # type: ignore[dict-item]
        for idx, spec in enumerate(labels):
            # spec 可为 "键"(键=显示名) 或 ("键","显示名")——键稳定(供 self.red_rows[键] 引用),
            # 显示名可与稳定键不同，便于压缩常驻面板文案而不影响取值路径。
            key, disp = spec if isinstance(spec, tuple) else (spec, spec)
            row = tk.Frame(card, bg=PANEL)
            row.pack(fill="x")
            row_label = tk.Label(
                row,
                text=disp,
                bg=PANEL,
                fg=DIM,
                font=(FONT_UI, 7),
                anchor="w",
                # The quality card uses only two-character tier names.  Reserving the
                # action card's wider label column here used to steal the exact space
                # needed for count+cell ranges at 150/175% DPI.
                width=(
                    3
                    if title == "品质与概率"
                    else 6
                    if title == "当前建议"
                    else 8
                ),
            )
            row_label.pack(side="left")
            if title == "当前建议" and key == "候选":
                self._candidate_row_label = row_label
            if key in state_badge_keys:
                badge = tk.Label(
                    row,
                    text="",
                    bg=PANEL,
                    fg=DIM,
                    font=(FONT_UI, 6, "bold"),
                    anchor="center",
                    padx=2,
                    pady=0,
                    bd=0,
                    relief="flat",
                )
                self.quality_state_badges[key] = badge
            row_value_font_pt = (
                SUMMARY_VALUE_FONT_SIZE
                if title == "品质与概率" and key in {"红件", "红格"}
                else value_font_pt
            )
            value = tk.Label(
                row,
                text="-",
                bg=PANEL,
                fg=TEXT,
                font=(
                    FONT_UI,
                    row_value_font_pt,
                    "bold" if key == "动作" else "normal",
                ),
                anchor="e",
                justify="right" if key == "金红概率" else "left",
                wraplength=0,
            )
            value.pack(side="right", fill="x", expand=True)
            rows[key] = value
            if key == "红件":
                triplet = tk.Frame(row, bg=PANEL)
                segments = (
                    ("low", TEXT, "normal"),
                    ("sep_left", DIM, "normal"),
                    ("middle", BAD, "bold"),
                    ("sep_right", DIM, "normal"),
                    ("high", TEXT, "normal"),
                )
                for name, color, weight in segments:
                    segment = tk.Label(
                        triplet,
                        text="",
                        bg=PANEL,
                        fg=color,
                        font=(FONT_UI, RED_COUNT_INLINE_FONT_SIZE, weight),
                        anchor="e",
                        padx=0,
                        pady=0,
                    )
                    segment.pack(side="left")
                    self.red_count_triplet_labels[name] = segment
                triplet.bind("<Configure>", self._on_red_count_triplet_configure, add="+")
                self.red_count_triplet_frame = triplet
        return rows

    def _set_quality_state_badge(self, key: str, text: str) -> None:
        badge = getattr(self, "quality_state_badges", {}).get(key)
        if badge is None:
            return
        value = getattr(self, "red_rows", {}).get(key)
        triplet = (
            getattr(self, "red_count_triplet_frame", None)
            if key == "红件"
            else None
        )
        active_value = (
            triplet
            if triplet is not None and triplet.winfo_manager()
            else value
        )
        if text and not badge.winfo_manager():
            # Repack in deterministic right-to-left order so the fixed-width
            # badge owns the far-right slot and the currently visible value
            # refits the remainder.  Red count uses a five-label triplet frame;
            # repacking only its hidden fallback label left the live badge on
            # the wrong side after standby -> pending transitions.
            if active_value is not None:
                active_value.pack_forget()
            badge.pack(side="right", padx=((3, 0) if key in {"红件", "红格"} else (4, 0)))
            if active_value is triplet:
                active_value.pack(side="right", anchor="e")
            elif value is not None:
                value.pack(side="right", fill="x", expand=True)
        elif not text and badge.winfo_manager():
            badge.pack_forget()
        if text in {"锁", "锁定", "件锁", "格锁"}:
            badge.configure(
                text=text,
                bg=PANEL_SOFT,
                fg=GOOD,
            )
        elif text in {"未锁", "待", "估"}:
            badge.configure(
                text=text,
                bg=PANEL_SOFT,
                fg=WARN,
            )
        elif text == "冲突":
            badge.configure(
                text=text,
                bg=PANEL_SOFT,
                fg=BAD,
            )
        else:
            badge.configure(
                text="",
                bg=PANEL,
                fg=DIM,
            )

    def _set_red_count_display(
        self,
        quality: QualityComposition | None,
        *,
        fallback: Any,
        hover: Any,
    ) -> None:
        """Render the familiar three-tier red count with an emphasized midpoint."""
        value = self.red_rows["红件"]
        frame = getattr(self, "red_count_triplet_frame", None)
        paired_triplet = quality_paired_candidate_triplet(quality)
        authority_ready = (
            quality is not None
            and quality.status in {"ready", "exact"}
            and paired_triplet is not None
        )
        raw_triplet: tuple[int | str, int | str, int | str] = (
            tuple(
                candidate.count if candidate is not None else "—"
                for candidate in paired_triplet
            )
            if authority_ready and paired_triplet is not None
            else ("—", "—", "—")
        )

        def readable_token(item: int | str) -> str | None:
            if item == "—":
                return "—"
            if type(item) is int and 0 <= item <= 99:  # noqa: E721
                return str(item)
            return None

        readable = tuple(readable_token(item) for item in raw_triplet)
        triplet = (
            tuple(str(item) for item in readable)
            if all(item is not None for item in readable)
            else ("—", "—", "—")
        )
        if frame is not None:
            value.pack_forget()
            if not frame.winfo_manager():
                # The triplet is a value, so keep its natural width and align it
                # with the right edge used by every other quality row.  Expanding
                # this frame made its left-packed segments appear beside the row
                # caption while the status badge remained on the far right.
                frame.pack(side="right", anchor="e")
            low, middle, high = triplet
            texts = {
                "low": f"保{low}",
                "sep_left": "/",
                "middle": f"均{middle}",
                "sep_right": "/",
                "high": f"激{high}",
            }
            hover_text = _text(hover).strip()
            for key, segment in self.red_count_triplet_labels.items():
                segment.configure(text=texts[key])
                tip = getattr(segment, "_hover_tip", None)
                if hover_text:
                    if tip is None:
                        segment._hover_tip = HoverTip(segment, hover_text)  # type: ignore[attr-defined]
                    else:
                        tip.set_text(hover_text)
                elif tip is not None:
                    tip.set_text("")
            self._enforce_red_count_triplet_readable_font()
            return

        if not value.winfo_manager():
            value.pack(side="right", fill="x", expand=True)
        self._set_label(value, "保—/均—/激—", limit=24, hover=hover)

    def _on_red_count_triplet_configure(self, _event: tk.Event[Any] | None = None) -> None:
        self._enforce_red_count_triplet_readable_font()

    def _apply_red_count_triplet_readable_font(
        self,
        labels: Mapping[str, tk.Label],
        point_size: int,
    ) -> int:
        """Apply one readable size and return Tk's complete requested width."""
        for key, segment in labels.items():
            segment.configure(
                font=(
                    FONT_UI,
                    point_size,
                    "bold" if key == "middle" else "normal",
                )
            )
        # Nested Configure callbacks return under the active guard.
        self.root.update_idletasks()
        frame = self.red_count_triplet_frame
        return max(
            int(frame.winfo_reqwidth()) if frame is not None else 0,
            sum(int(segment.winfo_reqwidth()) for segment in labels.values()),
        )

    def _enforce_red_count_triplet_readable_font(self, *, force: bool = False) -> None:
        """Keep the structural triplet readable and inside its final Tk parcel."""
        if getattr(self, "_red_count_triplet_refit_active", False):
            return
        frame = getattr(self, "red_count_triplet_frame", None)
        labels = getattr(self, "red_count_triplet_labels", {})
        if frame is None or not labels or not frame.winfo_manager():
            return
        signature = tuple(labels[key].cget("text") for key in labels)
        if (
            not force
            and signature == getattr(self, "_red_count_triplet_fit_signature", None)
            and int(frame.winfo_width()) >= int(frame.winfo_reqwidth())
        ):
            return
        self._red_count_triplet_refit_active = True
        try:
            target_point_size = max(
                SUMMARY_COMPACT_MIN_FONT_SIZE,
                int(
                    round(
                        RED_COUNT_INLINE_FONT_SIZE
                        * float(getattr(self, "_ui_scale", 1.0))
                    )
                ),
            )
            required = self._apply_red_count_triplet_readable_font(
                labels,
                target_point_size,
            )
            pending_tokens = {
                "low": ("保—", "保–"),
                "middle": ("均—", "均–"),
                "high": ("激—", "激–"),
            }
            if all(
                labels[key].cget("text") in choices
                for key, choices in pending_tokens.items()
            ):
                # Keep the base inline em-dash copy whenever it fits. At the
                # narrow 175%-DPI parcel only, use the typographically equivalent
                # en dash; this preserves the original five packed segments and
                # readable font instead of clipping or shrinking to 4/7pt.
                for key, choices in pending_tokens.items():
                    labels[key].configure(text=choices[0])
                self.root.update_idletasks()
                available = int(frame.winfo_width())
                if available >= 24 and int(frame.winfo_reqwidth()) > available:
                    for key, choices in pending_tokens.items():
                        labels[key].configure(text=choices[1])
                    self.root.update_idletasks()
                    required = max(
                        int(frame.winfo_reqwidth()),
                        sum(
                            int(segment.winfo_reqwidth())
                            for segment in labels.values()
                        ),
                    )
            available = int(frame.winfo_width())
            chosen = target_point_size
            if available >= 24 and required > available:
                for point_size in range(
                    target_point_size - 1,
                    SUMMARY_COMPACT_MIN_FONT_SIZE - 1,
                    -1,
                ):
                    required = self._apply_red_count_triplet_readable_font(
                        labels,
                        point_size,
                    )
                    chosen = point_size
                    if required <= available:
                        break
            self._red_count_triplet_fit_signature = tuple(
                labels[key].cget("text") for key in labels
            )
            self._red_count_triplet_fit_font = chosen
        finally:
            self._red_count_triplet_refit_active = False

    def _restore_red_count_triplet_cached_font_after_global_scale(self) -> None:
        """Restore the readable red-count token after global font scaling."""
        if getattr(self, "_red_count_triplet_refit_active", False):
            return
        frame = getattr(self, "red_count_triplet_frame", None)
        labels = getattr(self, "red_count_triplet_labels", {})
        if (
            frame is None
            or not labels
            or not frame.winfo_manager()
        ):
            return
        self._enforce_red_count_triplet_readable_font(force=True)

    @staticmethod
    def _red_cells_display(quality: QualityComposition | None) -> str:
        """Render cells from the exact same three joint rows as red counts."""
        paired_triplet = quality_paired_candidate_triplet(quality)
        if (
            quality is None
            or quality.status not in {"ready", "exact"}
            or paired_triplet is None
        ):
            return "—格"
        cells = tuple(
            str(candidate.cells) if candidate is not None else "—"
            for candidate in paired_triplet
        )
        if cells[0] == cells[1] == cells[2]:
            return f"{cells[1]}格"
        return f"{cells[0]}/{cells[1]}/{cells[2]}格"

    @staticmethod
    def _red_dimension_badge(
        quality: QualityComposition | None, dimension: str
    ) -> str:
        """Red rows use only the two visible lock states from their own dimension."""
        if quality is not None and quality.status == "conflict":
            return "冲突"
        if (quality is not None and quality.status == "ready"
            and quality.lock_reason == "sampled_weighted_posterior"
            and not quality_dimension_is_exact(quality, dimension)):
            return "估"
        return "锁" if quality_dimension_is_exact(quality, dimension) else "待"

    def _render_flags(self, flags: list[dict[str, str]]) -> None:
        for child in self.flags.winfo_children():
            child.destroy()
        if not flags:
            flags = [{"label": "正常监测", "level": "neutral", "detail": ""}]
        for flag in flags[:4]:
            level = str(flag.get("level") or "")
            label = str(flag.get("label") or "watch")
            detail = str(flag.get("detail") or "")
            chip = tk.Label(
                self.flags,
                text=label,
                bg=PANEL_SOFT,
                fg=_flag_color(level),
                padx=5,
                pady=1,
                font=(FONT_UI, 7),
                highlightthickness=1,
                highlightbackground=BORDER,
            )
            chip.pack(side="left", padx=(0, 4), pady=1)
            if detail:
                HoverTip(chip, detail)
        self._apply_font_scale(self.flags)

    def _set_label(self, widget: tk.Label, value: Any, *, limit: int = 30, hover: Any = None) -> None:
        text = _text(value)
        display = _short(text, limit)
        widget.configure(text=display, height=1)
        tip = getattr(widget, "_hover_tip", None)
        # hover 显式给了详情 → 【始终挂】(即使值没截断/已锁定, 也能悬停看候选/范围/依据);
        # 否则回退旧行为: 仅当显示被截断时挂全文。
        hover_text = _text(hover).strip() if hover is not None else ""
        full = hover_text or (text if text and text != display else "")
        if full:
            if tip is None:
                widget._hover_tip = HoverTip(widget, full)  # type: ignore[attr-defined]
            else:
                tip.set_text(full)
        elif tip is not None:
            tip.set_text("")

    def _resolve_match_records_scripts_dir(self) -> Path | None:
        """Locate packaged/repo scripts/ containing match_records_html.py."""
        here = Path(__file__).resolve()
        live_dir = self.snapshot_path.parent if hasattr(self, "snapshot_path") else None
        candidates: list[Path] = []
        if live_dir is not None:
            # .../data/logs/live -> package/repo root is parents[2]
            candidates.append(live_dir.parents[2] / "scripts")
        candidates.extend(
            here.parents[i] / "scripts"
            for i in range(1, min(5, len(here.parents)))
        )
        seen: set[Path] = set()
        for cand in candidates:
            try:
                resolved = cand.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            if (resolved / "match_records_html.py").exists():
                return resolved
        return None

    def _open_match_records(self, event: Any = None) -> str:
        """结算「查看对局记录」点击: 后台线程把本地 sessions.jsonl 生成自包含离线 HTML 并用
        默认浏览器打开(数据来自本地文件、不经客户端进程; 后台跑不卡悬浮窗)。"""
        base = self.snapshot_path.parent
        summary = getattr(self, "_last_summary", None)
        target_session_id = (
            self._summary_session_id(summary) if isinstance(summary, dict) else ""
        )

        def _work() -> None:
            try:
                scripts_dir = self._resolve_match_records_scripts_dir()
                if scripts_dir is None:
                    raise FileNotFoundError(
                        "match_records_html.py not found under scripts/ "
                        "(portable build must include match_records_html.py + build_match_records.py)"
                    )
                if str(scripts_dir) not in sys.path:
                    sys.path.insert(0, str(scripts_dir))
                from match_records_html import build_and_open

                build_and_open(
                    base / "sessions.jsonl",
                    base / "match_records.html",
                    open_browser=True,
                    target_session_id=target_session_id or None,
                )
            except Exception as exc:  # noqa: BLE001 - 锦上添花, 失败不影响悬浮窗
                traceback.print_exc()
                try:
                    import tkinter.messagebox as messagebox

                    messagebox.showerror(
                        "对局记录",
                        f"无法打开对局记录 HTML:\n{exc}\n\n"
                        f"详情见 data/logs/live/overlay.stderr.log",
                    )
                except Exception:  # noqa: BLE001
                    pass

        threading.Thread(target=_work, daemon=True, name="match-records-html").start()
        return "break"  # 阻止冒泡到窗口拖拽(<ButtonPress-1>)

    @staticmethod
    def _replace_action_cell_click_binding(
        widget: tk.Widget,
        callback: Callable[..., Any],
    ) -> None:
        """Temporarily replace click/keyboard activation without losing prior scripts."""
        sequences = ("<Button-1>", "<Return>", "<space>")
        if not getattr(widget, "_action_binding_snapshot_saved", False):
            previous_bindings: dict[str, str] = {}
            for sequence in sequences:
                try:
                    previous = widget.bind(sequence)
                except (AttributeError, tk.TclError, TypeError):
                    previous = ""
                previous_bindings[sequence] = (
                    previous if isinstance(previous, str) else ""
                )
            widget._action_previous_bindings = previous_bindings  # type: ignore[attr-defined]
            widget._action_binding_snapshot_saved = True  # type: ignore[attr-defined]

        owned_bindings = getattr(widget, "_action_owned_bindings", {})
        new_owned_bindings: dict[str, str | None] = {}
        for sequence in sequences:
            owned_id = (
                owned_bindings.get(sequence)
                if isinstance(owned_bindings, dict)
                else None
            )
            try:
                if isinstance(owned_id, str) and owned_id:
                    widget.unbind(sequence, owned_id)
                else:
                    widget.unbind(sequence)
            except (AttributeError, tk.TclError, TypeError):
                try:
                    widget.unbind(sequence)
                except (AttributeError, tk.TclError, TypeError):
                    pass
            try:
                func_id = widget.bind(sequence, callback)
            except (AttributeError, tk.TclError, TypeError):
                func_id = None
            new_owned_bindings[sequence] = (
                func_id if isinstance(func_id, str) else None
            )
        widget._action_owned_bindings = new_owned_bindings  # type: ignore[attr-defined]

    @staticmethod
    def _restore_action_cell_click_binding(widget: tk.Widget) -> None:
        """Remove only the temporary action binding and restore the exact prior script."""
        if not getattr(widget, "_action_binding_snapshot_saved", False):
            return
        sequences = ("<Button-1>", "<Return>", "<space>")
        owned_bindings = getattr(widget, "_action_owned_bindings", {})
        previous_bindings = getattr(widget, "_action_previous_bindings", {})
        for sequence in sequences:
            owned_id = (
                owned_bindings.get(sequence)
                if isinstance(owned_bindings, dict)
                else None
            )
            try:
                if isinstance(owned_id, str) and owned_id:
                    widget.unbind(sequence, owned_id)
                else:
                    widget.unbind(sequence)
            except (AttributeError, tk.TclError, TypeError):
                try:
                    widget.unbind(sequence)
                except (AttributeError, tk.TclError, TypeError):
                    pass
            previous = (
                previous_bindings.get(sequence, "")
                if isinstance(previous_bindings, dict)
                else ""
            )
            if isinstance(previous, str) and previous:
                try:
                    widget.bind(sequence, previous)
                except (AttributeError, tk.TclError, TypeError):
                    pass
        widget._action_previous_bindings = {}  # type: ignore[attr-defined]
        widget._action_owned_bindings = {}  # type: ignore[attr-defined]
        widget._action_binding_snapshot_saved = False  # type: ignore[attr-defined]

    @staticmethod
    def _capture_action_cell_visual_state(widget: tk.Widget) -> None:
        if getattr(widget, "_action_normal_visual_state", None) is not None:
            return
        state: dict[str, Any] = {}
        for option in (
            "fg",
            "font",
            "cursor",
            "padx",
            "pady",
            "takefocus",
            "highlightthickness",
            "highlightbackground",
            "highlightcolor",
        ):
            try:
                current = widget.cget(option)
            except (AttributeError, KeyError, tk.TclError, TypeError):
                continue
            if current is None:
                if option == "cursor":
                    current = ""
                else:
                    continue
            state[option] = current
        widget._action_normal_visual_state = state  # type: ignore[attr-defined]

    @staticmethod
    def _restore_action_cell_visual_state(widget: tk.Widget) -> None:
        state = getattr(widget, "_action_normal_visual_state", None)
        if not isinstance(state, dict):
            return
        try:
            widget.configure(**state)
        except (AttributeError, tk.TclError, TypeError):
            pass
        widget._action_normal_visual_state = None  # type: ignore[attr-defined]

    @staticmethod
    def _apply_action_cell_normal_visual_state(widget: tk.Widget) -> None:
        """Apply the cached ordinary style while preserving it for later restore."""
        state = getattr(widget, "_action_normal_visual_state", None)
        if not isinstance(state, dict):
            return
        try:
            widget.configure(**state)
        except (AttributeError, tk.TclError, TypeError):
            pass

    def _apply_action_cell(
        self,
        value: Any,
        *,
        next_step: Any = None,
        extra_hover: Any = None,
        limit: int = 36,
        compatibility_guidance: bool = False,
    ) -> None:
        """Render the merged suggestion row while preserving settlement click behavior.

        The compact surface now shares one row between current action and next evidence.  Full
        values remain available in a structured hover; settlement still becomes the clickable
        local match-record action and every other state clears that click binding.
        """
        widget = self.action_rows.get("动作") if hasattr(self, "action_rows") else None
        if widget is None:
            return
        hover = action_next_step_hover(value, next_step, extra_hover)
        if compatibility_guidance:
            self._capture_action_cell_visual_state(widget)
            self._apply_action_cell_normal_visual_state(widget)
            widget._action_normal_fg = widget.cget("fg")  # type: ignore[attr-defined]
            widget._action_normal_font = widget.cget("font")  # type: ignore[attr-defined]
            self._set_label(
                widget,
                "查看兼容步骤 ▸",
                limit=18,
                hover=hover,
            )
            widget.configure(
                fg=WARM,
                cursor="hand2",
                font=widget._action_normal_font,  # type: ignore[attr-defined]
                takefocus=True,
                highlightthickness=1,
                highlightbackground=BORDER,  # noqa: F821 - rebound by apply_theme
                highlightcolor=WARM,  # noqa: F821 - rebound by apply_theme
            )
            self._replace_action_cell_click_binding(
                widget,
                self._open_compatibility_guidance,
            )
            widget._action_clickable = True  # type: ignore[attr-defined]
        elif _text(value, "").strip() == "结算完成":
            self._capture_action_cell_visual_state(widget)
            self._apply_action_cell_normal_visual_state(widget)
            widget._action_normal_fg = widget.cget("fg")  # type: ignore[attr-defined]
            widget._action_normal_font = widget.cget("font")  # type: ignore[attr-defined]
            self._set_label(widget, "查看对局记录", limit=18, hover=hover)
            widget.configure(
                fg=ACCENT,
                cursor="hand2",
                # Accent/cursor carry the action affordance.  Reuse the exact
                # ordinary action-cell font so settlement does not jump two
                # points and one visual hierarchy above its sibling rows.
                font=(
                    getattr(widget, "_action_normal_font", None)
                    or (FONT_UI, SUMMARY_COMPACT_FONT_SIZE, "bold")
                ),
                takefocus=True,
                # Keep the record entry text-like, not button-like.  Keyboard
                # focus/activation remains available without a persistent box.
                highlightthickness=0,
            )
            self._replace_action_cell_click_binding(
                widget,
                self._open_match_records,
            )
            widget._action_clickable = True  # type: ignore[attr-defined]
        else:
            if getattr(widget, "_action_clickable", False):
                self._restore_action_cell_click_binding(widget)
                self._restore_action_cell_visual_state(widget)
                widget._action_clickable = False  # type: ignore[attr-defined]
            display = merge_action_next_step_display(value, next_step)
            self._set_autofit_label(
                widget,
                display,
                fallback_limit=limit,
                hover=hover,
                base_font_pt=SUMMARY_COMPACT_FONT_SIZE,
                min_font_pt=SUMMARY_COMPACT_MIN_FONT_SIZE,
            )

    def _schedule_autofit_refresh(self) -> None:
        """Debounce density-aware label fitting after UI scale or width changes."""
        if not hasattr(self, "root"):
            return
        after_id = getattr(self, "_autofit_refresh_after_id", None)
        if after_id is not None:
            try:
                self.root.after_cancel(after_id)
            except (AttributeError, tk.TclError):
                pass
        try:
            self._autofit_refresh_after_id = self.root.after(
                36, self._refresh_autofit_labels
            )
        except (AttributeError, tk.TclError):
            self._autofit_refresh_after_id = None

    def _refresh_autofit_labels(self) -> None:
        self._autofit_refresh_after_id = None
        for widget in tuple(getattr(self, "_autofit_widgets", ())):
            try:
                if not widget.winfo_exists():
                    continue
                callback = getattr(widget, "_ahmad_autofit_callback", None)
                if callable(callback):
                    callback()
            except tk.TclError:
                continue

    def _set_candidate_autofit(
        self,
        value: Any,
        *,
        preserve_game_launch: bool = False,
        hover: Any = None,
    ) -> None:
        """候选行: 显示全文，按文本长度/控件宽度自动缩字号防溢出(艾莎局总件/总格是范围、文本更长)。"""
        widget = self.action_rows.get("候选") if hasattr(self, "action_rows") else None
        if not preserve_game_launch:
            self._clear_game_launch_candidate_binding()
            row_label = getattr(self, "_candidate_row_label", None)
            if row_label is not None:
                row_label.configure(text="总件")
        self._set_autofit_label(
            widget,
            value,
            retry=lambda: self._set_candidate_autofit(
                value,
                preserve_game_launch=preserve_game_launch,
                hover=hover,
            ),
            hover=hover,
            base_font_pt=SUMMARY_COMPACT_FONT_SIZE,
            min_font_pt=SUMMARY_COMPACT_MIN_FONT_SIZE,
        )

    def _set_candidate_dimension_rows(self, data: dict[str, Any]) -> None:
        summary = self._mini_candidate_text(data)
        count_text, grid_text = split_candidate_dimension_summary(summary)
        hover = summary if summary and summary != "-" else None
        self._set_candidate_autofit(count_text, hover=hover)
        self._set_autofit_label(
            self.action_rows.get("总格"),
            grid_text,
            hover=hover,
            base_font_pt=SUMMARY_COMPACT_FONT_SIZE,
            min_font_pt=SUMMARY_COMPACT_MIN_FONT_SIZE,
        )

    def _clear_game_launch_candidate_binding(self) -> None:
        widget = self.action_rows.get("候选") if hasattr(self, "action_rows") else None
        bindings = dict(getattr(self, "_game_launch_candidate_bindings", {}))
        if widget is not None:
            for sequence, func_id in bindings.items():
                try:
                    widget.unbind(sequence, func_id)
                except (tk.TclError, TypeError):
                    pass
            try:
                widget.configure(fg=TEXT, cursor="", takefocus=False)
            except tk.TclError:
                pass
            tip = getattr(widget, "_hover_tip", None)
            if tip is not None:
                tip.set_text("")
        self._game_launch_candidate_bindings = {}
        self._game_launch_candidate_mode = None

    def _set_game_launch_candidate_display(
        self,
        *,
        mode: str,
        text: str,
        color: str,
        hover: str,
        enabled: bool,
    ) -> None:
        widget = self.action_rows.get("候选") if hasattr(self, "action_rows") else None
        if widget is None:
            return
        self._clear_game_launch_candidate_binding()
        row_label = getattr(self, "_candidate_row_label", None)
        if row_label is not None:
            row_label.configure(text="启动")
        self._set_label(self.action_rows.get("总格"), "-")
        self._set_candidate_autofit(
            text,
            preserve_game_launch=True,
            hover=hover,
        )
        widget.configure(
            fg=color,
            cursor="hand2" if enabled else "",
            takefocus=bool(enabled),
        )
        if enabled:
            func_id = widget.bind(
                "<Button-1>",
                self._launch_game_from_candidate,
                add="+",
            )
            if func_id:
                self._game_launch_candidate_bindings["<Button-1>"] = func_id
        self._game_launch_candidate_mode = mode

    def _game_process_running_state(self) -> bool | None:
        try:
            return self._game_process_state_probe(GAME_PROCESS_NAME)
        except Exception:
            return None

    def _sync_game_launch_candidate(
        self,
        capture_status: dict[str, Any] | None = None,
    ) -> None:
        if (
            not bool(getattr(self, "_game_launch_candidate_context", False))
            or bool(getattr(self, "_manual_active", False))
            or bool(getattr(self, "_manual_edit_enabled", False))
        ):
            if getattr(self, "_game_launch_candidate_mode", None) is not None:
                self._set_candidate_autofit("-")
            return
        status = capture_status if isinstance(capture_status, dict) else self._capture_status()
        ready = _game_launch_candidate_ready(
            status,
            process_running_state=self._game_process_running_state(),
        )
        if not ready:
            if getattr(self, "_game_launch_candidate_mode", None) is not None:
                self._set_candidate_autofit("-")
            return
        now = time.monotonic()
        if now < float(getattr(self, "_game_launch_pending_until", 0.0)):
            self._set_game_launch_candidate_display(
                mode="pending",
                text=GAME_LAUNCH_PENDING_TEXT,
                color=WARM,
                hover="已向 Steam 发送启动请求；正在等待 BidKing.exe 出现。",
                enabled=False,
            )
            return
        if now < float(getattr(self, "_game_launch_error_until", 0.0)):
            self._set_game_launch_candidate_display(
                mode="error",
                text=GAME_LAUNCH_ERROR_TEXT,
                color=WARM,
                hover=GAME_LAUNCH_ERROR_TIP,
                enabled=False,
            )
            return
        self._set_game_launch_candidate_display(
            mode="ready",
            text=GAME_LAUNCH_READY_TEXT,
            color=GOOD,
            hover=GAME_LAUNCH_READY_TIP,
            enabled=True,
        )

    def _notify_game_launch_failure(self) -> None:
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return
        try:
            import tkinter.messagebox as messagebox

            messagebox.showwarning(
                "启动游戏",
                "未能通过 Steam 启动竞拍之王。\n\n"
                "请确认 Steam 已安装并登录，然后从 Steam 手动启动游戏；"
                "Hero Ref 会继续等待。",
                parent=self.root,
            )
        except Exception:
            pass

    def _launch_game_from_candidate(
        self,
        _event: tk.Event[Any] | None = None,
    ) -> str:
        if time.monotonic() < float(getattr(self, "_game_launch_pending_until", 0.0)):
            return "break"
        capture_status = self._capture_status()
        if not _game_launch_candidate_ready(
            capture_status,
            process_running_state=self._game_process_running_state(),
        ):
            self._sync_game_launch_candidate(capture_status)
            return "break"
        try:
            self._game_uri_launcher()
        except Exception:
            self._game_launch_pending_until = 0.0
            self._game_launch_error_until = time.monotonic() + GAME_LAUNCH_ERROR_SECONDS
            self._record_ui_runtime_status(
                "game_launch_failed",
                capture_status=capture_status,
            )
            self._sync_game_launch_candidate(capture_status)
            self._notify_game_launch_failure()
            return "break"
        self._game_launch_error_until = 0.0
        self._game_launch_pending_until = time.monotonic() + GAME_LAUNCH_PENDING_SECONDS
        self._record_ui_runtime_status(
            "game_launch_requested",
            capture_status=capture_status,
        )
        self._sync_game_launch_candidate(capture_status)
        return "break"

    def _set_autofit_label(
        self,
        widget,
        value,
        *,
        retry=None,
        fallback_limit: int = 30,
        hover: Any = None,
        min_font_pt: int | None = None,
        base_font_pt: int = 8,
        font_family: str = FONT_UI,
        preserve_red_segment: bool = False,
        wrap_at_separator: bool = False,
        stabilize_font: bool = False,
    ) -> None:
        """Show full text, shrinking the font (base*UI-scale down to a floor) to avoid overflow; if it
        still overflows at the floor, truncate + hover the full text. Recomputes from base each call
        (no cumulative shrink). Reused by 候选 and 低品件 (both carry长文本 like aisha ranges/risk text)."""
        if widget is None:
            return
        text = _text(value) or "-"
        widgets = getattr(self, "_autofit_widgets", None)
        if widgets is None:
            widgets = []
            self._autofit_widgets = widgets
        if all(existing is not widget for existing in widgets):
            widgets.append(widget)
        widget._ahmad_autofit_callback = lambda: self._set_autofit_label(  # type: ignore[attr-defined]
            widget,
            value,
            retry=retry,
            fallback_limit=fallback_limit,
            hover=hover,
            min_font_pt=min_font_pt,
            base_font_pt=base_font_pt,
            font_family=font_family,
            preserve_red_segment=preserve_red_segment,
            wrap_at_separator=wrap_at_separator,
            stabilize_font=stabilize_font,
        )
        try:
            import tkinter.font as tkfont

            widget.configure(text=text, height=1, wraplength=0)
            avail = int(widget.winfo_width())
            if avail <= 1:  # 尚未布局完成，下一帧再试
                cb = retry or (
                    lambda: self._set_autofit_label(
                        widget,
                        value,
                        fallback_limit=fallback_limit,
                        hover=hover,
                        min_font_pt=min_font_pt,
                        base_font_pt=base_font_pt,
                        font_family=font_family,
                        preserve_red_segment=preserve_red_segment,
                        wrap_at_separator=wrap_at_separator,
                        stabilize_font=stabilize_font,
                    )
                )
                widget.after(24, cb)
                return
            base_pt = max(
                7,
                int(round(int(base_font_pt) * float(getattr(self, "_ui_scale", 1.0)))),
            )
            min_pt = (
                max(4, int(min_font_pt))
                if min_font_pt is not None
                else max(6, base_pt - 3)
            )
            f = tkfont.Font(family=font_family, size=base_pt)
            pt = base_pt
            wrapped_parts = tuple(
                part.strip() for part in text.split(" · ") if part.strip()
            )
            use_wrapped_lines = (
                bool(wrap_at_separator)
                and len(wrapped_parts) > 1
                and f.measure(text) > avail - 3
            )
            measure_text = (
                lambda: max(f.measure(part) for part in wrapped_parts)
                if use_wrapped_lines
                else f.measure(text)
            )
            while pt > min_pt and measure_text() > avail - 3:
                pt -= 1
                f.configure(size=pt)
            if stabilize_font:
                previous_width = getattr(widget, "_ahmad_autofit_stable_width", None)
                previous_scale = getattr(widget, "_ahmad_autofit_stable_scale", None)
                previous_pt = getattr(widget, "_ahmad_autofit_stable_pt", None)
                ui_scale = float(getattr(self, "_ui_scale", 1.0))
                if (
                    type(previous_width) is int
                    and abs(previous_width - avail) <= 8
                    and type(previous_scale) in {int, float}
                    and abs(float(previous_scale) - ui_scale) < 0.01
                    and type(previous_pt) is int
                ):
                    pt = min(pt, previous_pt)
                    f.configure(size=pt)
            # hover 显式给了详情 → 【始终挂】(锁定/短值也能悬停看候选/范围/依据); 否则回退旧行为:
            # 缩到下限仍溢出才截断+hover 全文, 未溢出则清 hover(全文已可见)。
            hover_text = _text(hover).strip() if hover is not None else ""
            tip = getattr(widget, "_hover_tip", None)
            overflow = measure_text() > avail - 3
            display_text = "\n".join(wrapped_parts) if use_wrapped_lines else text
            if overflow:
                if preserve_red_segment:
                    variants = _red_priority_probability_variants(text)
                    for candidate in variants[1:]:
                        if f.measure(candidate) <= avail - 3:
                            display_text = candidate
                            break
                    else:
                        # The final variant is the complete red segment. It may shrink further, but
                        # it is never substring-truncated; gold is the intentionally expendable part.
                        display_text = variants[-1]
                        stable_floor = min_pt if stabilize_font else 4
                        while pt > stable_floor and f.measure(display_text) > avail - 3:
                            pt -= 1
                            f.configure(size=pt)
                else:
                    max_chars = max(8, int((avail - 3) / max(1, f.measure("0"))))
                    display_text = _short(text, max_chars)
            widget.configure(
                text=display_text,
                font=(font_family, pt, "normal"),
                height=2 if use_wrapped_lines else 1,
                justify=(
                    "right"
                    if use_wrapped_lines
                    else ("left" if wrap_at_separator else widget.cget("justify"))
                ),
            )
            # Font.measure can differ slightly from the themed label's actual
            # request (notably numeric fonts under high-DPI Tk).  The primary
            # price cards must fit their real widget request, not just the
            # predictor; keep full one-line text and reduce only as needed.
            while (
                display_text == text
                and pt > min_pt
                and widget.winfo_reqwidth() > avail
            ):
                pt -= 1
                widget.configure(font=(font_family, pt, "normal"))
            overflow = widget.winfo_reqwidth() > avail
            if stabilize_font:
                widget._ahmad_autofit_stable_width = avail  # type: ignore[attr-defined]
                widget._ahmad_autofit_stable_scale = float(  # type: ignore[attr-defined]
                    getattr(self, "_ui_scale", 1.0)
                )
                widget._ahmad_autofit_stable_pt = pt  # type: ignore[attr-defined]
            full = hover_text or (text if overflow else "")
            if full:
                if tip is None:
                    widget._hover_tip = HoverTip(widget, full)  # type: ignore[attr-defined]
                else:
                    tip.set_text(full)
            elif tip is not None:
                tip.set_text("")
        except Exception:  # noqa: BLE001
            self._set_label(widget, value, limit=fallback_limit, hover=hover)

    @staticmethod
    def _joint_quality_is_exact(quality: QualityComposition | None) -> bool:
        """Only the structured joint contract may authorize compact "unique" copy."""
        return quality_is_strict_exact(quality)

    @classmethod
    def _lock_triple(
        cls,
        range_str: Any,
        prefix: str,
        unit: str = "",
        *,
        quality: QualityComposition | None = None,
        dimension: str = "",
    ) -> str:
        """Translate a compact percentile triple without inferring locks from its shape.

        Equal P10/P50/P90 values are still only an estimate when the structured joint
        posterior has multiple arms or tail mass.  This keeps compact hover semantics
        aligned with the detail composition view.
        """
        s = _text(range_str).strip()
        if not s or s == "-":
            return ""
        parts = [p.strip() for p in s.split("/") if p.strip()]
        collapsed = len(parts) >= 3 and parts[0] == parts[-1]
        if collapsed and cls._joint_quality_is_exact(quality):
            one = parts[len(parts) // 2]
            return f"{prefix}\n  {one}{unit}\n  唯一锁定（联合候选 100%）"
        if len(parts) >= 3 and not collapsed:
            return (
                f"{prefix}\n"
                f"  保守 {parts[0]} / 中位 {parts[1]} / 激进 {parts[-1]}{unit}\n"
                f"  概率估计：按中位展示，实际范围 {parts[0]}–{parts[-1]}{unit}"
            )
        one = parts[len(parts) // 2] if parts else s
        detail = "仍有尾部联合候选"
        if quality is not None and len(quality.candidates) > 1:
            values = {
                getattr(candidate, dimension)
                for candidate in quality.candidates
                if dimension in {"count", "cells"}
            }
            if dimension == "count" and len(values) == 1:
                detail = "件数候选集中，格数仍有多个联合候选"
            elif dimension == "cells" and len(values) == 1:
                detail = "格数候选集中，件数仍有多个联合候选"
        elif quality is None or quality.status == "unavailable":
            detail = "缺少结构化联合锁证据"
        return f"{prefix}\n  最可能 {one}{unit}\n  概率估计：{detail}"

    def _red_hover(
        self,
        label: str,
        red: dict,
        reference: dict,
        *,
        quality: QualityComposition | None = None,
    ) -> str:
        """红品各行悬停详情：主行联合代表、结构化锁与低概率候选。"""
        def visible_red_header(dimension: str, unit: str) -> str:
            if quality is None or quality.lock_reason != "sampled_weighted_posterior":
                return self._lock_triple(
                    red.get(f"{dimension}_range"), label, unit,
                    quality=quality, dimension=dimension,
                )
            paired = quality_paired_candidate_triplet(quality)
            if quality is not None:
                if quality.status not in {"ready", "exact"} or paired is None:
                    return f"{label}\n  等待可用的联合候选"
                values = [
                    str(getattr(candidate, dimension)) if candidate is not None else "—"
                    for candidate in paired
                ]
                if self._joint_quality_is_exact(quality):
                    return self._lock_triple(
                        "/".join(values), label, unit, quality=quality, dimension=dimension
                    )
                if quality_dimension_is_exact(quality, dimension):
                    return f"{label}\n  {values[1]}{unit}\n  该维度已锁定；另一维按联合候选展示"
                return (
                    f"{label}\n"
                    f"  保守 {values[0]} / 中位 {values[1]} / 激进 {values[2]}{unit}\n"
                    "  主行联合代表：对应保 / 均 / 激，不表示完整可能范围"
                )

        basis = []
        pr, sr = red.get("prior_rate"), red.get("sample_rate")
        if pr:
            basis.append(f"先验红率 {_text(pr)}")
        if sr:
            basis.append(f"抽检红率 {_text(sr)}")
        if label == "红件":
            candidate_block = (
                _structured_quality_candidate_block(
                    quality,
                    dimension="count",
                    heading="红件候选",
                    unit="件",
                )
                if quality is not None
                else _hover_candidate_block(red.get("count_probability_hover"))
            )
            blocks = [
                visible_red_header("count", "件"),
                candidate_block,
            ]
            correlation_note = (
                quality.count_correlation_note.strip()
                if quality is not None
                else ""
            )
            if correlation_note:
                blocks.append("联动\n  " + correlation_note)
            if basis:
                blocks.append("依据\n" + "\n".join(f"  {item}" for item in basis))
            order_note = _text(red.get("count_order_note")).strip()
            if order_note:
                blocks.append("显示\n  " + order_note)
            return "\n\n".join(block for block in blocks if block)
        if label == "红格":
            candidate_block = (
                _structured_quality_candidate_block(
                    quality,
                    dimension="cells",
                    heading="红格候选",
                    unit="格",
                )
                if quality is not None
                else _hover_candidate_block(red.get("cells_probability_hover"))
            )
            blocks = [
                visible_red_header("cells", "格"),
                candidate_block,
            ]
            return "\n\n".join(block for block in blocks if block)
        if label == "金红概率":
            return _hover_probability_blocks(red.get("quality_probability_hover"))
        if label in {"紫金件", "紫品", "金品"}:
            s = _text(red.get("quality_count_summary")).strip()
            detail = _text(red.get("purple_gold_probability_hover")).strip()
            blocks = []
            if s and s != "-":
                summary_lines = [part.strip() for part in s.split(" · ") if part.strip()]
                blocks.append("紫金件/格\n" + "\n".join(f"  {part}" for part in summary_lines))
            if detail:
                blocks.append(_hover_probability_blocks(detail))
            if blocks:
                blocks.append("每档顺序：保守 / 中位 / 激进；件数与格数并列")
            return "\n\n".join(blocks)
        if label in {"白绿", "蓝品"} and quality is not None:
            return "\n\n".join(
                block
                for block in (
                    _structured_quality_candidate_block(
                        quality,
                        dimension="count",
                        heading=f"{label}件候选",
                        unit="件",
                    ),
                    _structured_quality_candidate_block(
                        quality,
                        dimension="cells",
                        heading=f"{label}格候选",
                        unit="格",
                    ),
                )
                if block
            )
        if label == "低品件":
            parts = [x for x in (_text(red.get("uncertainty_summary")).strip(),
                                  _text(red.get("risk_reference")).strip()) if x and x != "-"]
            blocks = [f"低品件/格\n  {parts[0]}"] if parts else []
            probability_detail = _hover_probability_blocks(
                red.get("low_quality_count_probability_hover")
            )
            if probability_detail:
                blocks.append(probability_detail)
            if len(parts) > 1:
                blocks.append("依据\n" + "\n".join(f"  {part}" for part in parts[1:]))
            return "\n\n".join(blocks)
        return ""

    def _set_summary_label(
        self,
        widget: tk.Label,
        value: Any,
        *,
        line_limit: int = 38,
        max_lines: int = 2,
    ) -> None:
        text = _text(value)
        if text in {"", "-"}:
            widget.configure(text="-", height=1)
            return
        parts = text.split(" · ")
        if len(parts) <= 1:
            widget.configure(text=_short(text, line_limit * max_lines), height=1)
            return
        lines: list[str] = []
        current = ""
        index = 0
        while index < len(parts):
            part = parts[index]
            candidate = part if not current else f"{current} · {part}"
            if len(candidate) <= line_limit or not current:
                current = candidate
                index += 1
                continue
            lines.append(current)
            current = ""
            if len(lines) >= max_lines - 1:
                rest = " · ".join(parts[index:])
                lines.append(_short(rest, line_limit))
                break
        else:
            if current:
                lines.append(current)
        if not lines:
            lines = [_short(text, line_limit)]
        lines = lines[:max_lines]
        widget.configure(text="\n".join(lines), height=len(lines), justify="right")

    def _manual_entry_text(self, key: str) -> str:
        entry = self.manual_entries.get(key)
        if entry is None:
            return ""
        return entry.get().strip()

    def _set_manual_entry(
        self,
        key: str,
        value: Any,
        *,
        track_auto: bool = False,
    ) -> None:
        entry = self.manual_entries.get(key)
        if entry is None:
            return
        var = getattr(self, "manual_vars", {}).get(key)
        text = "" if value in (None, "") else str(value)
        self._manual_programmatic_update = True
        previous_state: str | None = None
        try:
            try:
                previous_state = str(entry.cget("state"))
                if previous_state == "disabled":
                    entry.configure(state="normal")
            except (tk.TclError, AttributeError):
                previous_state = None
            if var is not None:
                var.set(text)
            else:
                entry.delete(0, "end")
                if text:
                    entry.insert(0, text)
        finally:
            if previous_state == "disabled":
                try:
                    entry.configure(state=previous_state)
                except (tk.TclError, AttributeError):
                    pass
            self._manual_programmatic_update = False
        if track_auto:
            if not hasattr(self, "_manual_autofill_values"):
                self._manual_autofill_values = {}
            if not hasattr(self, "_manual_dirty_fields"):
                self._manual_dirty_fields = set()
            self._manual_autofill_values[key] = text
            self._manual_dirty_fields.discard(key)

    def _summary_phase(self, data: dict[str, Any]) -> str:
        context = data.get("context") if isinstance(data.get("context"), dict) else {}
        return _text(context.get("phase") or data.get("phase"), "")

    def _summary_session_id(self, data: dict[str, Any]) -> str:
        context = data.get("context") if isinstance(data.get("context"), dict) else {}
        return _text(context.get("session_id") or data.get("session_id"), "").strip()

    def _summary_with_ethan_r5_outline_continuity(
        self,
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        """Keep one Ethan quote visible during the R5 full-outline frame gap.

        The monitor can advance action round before skill ``1002085`` reaches the
        snapshot.  This is deliberately narrower than a generic stale-cache rule:
        one exact session and map, R5 only, and non-decreasing round.  Any terminal,
        engine error, new session/map, or actual skill result clears/replaces it.
        """
        if type(summary) is not dict:  # noqa: E721 - internal JSON boundary
            return summary
        raw_context = dict.get(summary, "context")
        context = raw_context if type(raw_context) is dict else {}  # noqa: E721
        raw_hero = dict.get(context, "hero")
        hero_key = (
            normalize_hero_key(raw_hero)
            if type(raw_hero) is str  # noqa: E721
            else ""
        )
        ref_result = dict.get(summary, "ahmed_ref")
        ref = ref_result if type(ref_result) is dict else {}  # noqa: E721
        reference_result = dict.get(summary, "reference")
        reference = (
            reference_result
            if type(reference_result) is dict  # noqa: E721
            else {}
        )
        raw_phase = dict.get(context, "phase")
        phase = raw_phase if type(raw_phase) is str else ""  # noqa: E721
        raw_summary_status = dict.get(summary, "status")
        summary_status = (
            raw_summary_status if type(raw_summary_status) is str else ""  # noqa: E721
        )
        session_id = _strict_quote_summary_session_id(summary)
        raw_map_id = dict.get(context, "map_id")
        if type(raw_map_id) is int and raw_map_id > 0:  # noqa: E721
            map_identity: tuple[type, int | str] | None = (int, raw_map_id)
        elif (
            type(raw_map_id) is str  # noqa: E721
            and 1 <= len(raw_map_id) <= 12
            and raw_map_id.isascii()
            and raw_map_id.isdecimal()
            and raw_map_id == str(int(raw_map_id))
            and int(raw_map_id) > 0
        ):
            map_identity = (str, raw_map_id)
        else:
            map_identity = None
        raw_round = dict.get(context, "round")
        if type(raw_round) is int and 0 <= raw_round <= 5:  # noqa: E721
            round_no = raw_round
        elif (
            type(raw_round) is str  # noqa: E721
            and 1 <= len(raw_round) <= 2
            and raw_round.isascii()
            and raw_round.isdecimal()
            and raw_round == str(int(raw_round))
            and 0 <= int(raw_round) <= 5
        ):
            round_no = int(raw_round)
        else:
            round_no = None
        cached = getattr(self, "_last_ethan_r5_outline_quote", None)

        raw_notes = dict.get(ref, "notes")
        strict_notes = (
            tuple(raw_notes)
            if type(raw_notes) in {list, tuple}  # noqa: E721
            and all(
                type(note) is str  # noqa: E721
                and bool(note)
                and note.strip() == note
                for note in raw_notes
            )
            else None
        )
        marker_texts = tuple(note.casefold() for note in strict_notes or ())
        performance = dict.get(summary, "performance")
        has_nonlive_marker = (
            "stale" in summary
            or "continuity" in summary
            or any(
                marker in note
                for note in marker_texts
                for marker in ("stale", "continuity", "cached")
            )
            or (
                type(performance) is dict  # noqa: E721
                and (
                    dict.get(performance, "ref_cache_hit") is True
                    or dict.get(performance, "pending_ref_cache_hit") is True
                )
            )
        )

        cached_session = (
            dict.get(cached, "session_id")
            if type(cached) is dict  # noqa: E721
            else None
        )
        cached_map_identity = (
            dict.get(cached, "map_identity")
            if type(cached) is dict  # noqa: E721
            else None
        )
        cached_round = (
            dict.get(cached, "round")
            if type(cached) is dict  # noqa: E721
            else None
        )
        cached_is_valid = (
            type(cached) is dict  # noqa: E721
            and type(cached_session) is str  # noqa: E721
            and type(cached_map_identity) is tuple  # noqa: E721
            and len(cached_map_identity) == 2
            and (
                cached_map_identity[0] is int
                or cached_map_identity[0] is str
            )
            and type(cached_map_identity[1]) is cached_map_identity[0]  # noqa: E721
            and type(cached_round) is int  # noqa: E721
            and 0 <= cached_round <= 5
        )
        same_authority = (
            cached_is_valid
            and session_id is not None
            and map_identity is not None
            and cached_session == session_id
            and cached_map_identity == map_identity
        )
        if (
            same_authority
            and round_no is not None
            and round_no < cached_round
        ):
            # A delayed lower-round frame invalidates the continuity authority.  Keeping
            # the older quote would make a later R5 wait indistinguishable from a stale
            # out-of-order cache replay.
            self._last_ethan_r5_outline_quote = None
            return summary

        raw_ref_status = dict.get(ref, "status")
        ref_status = raw_ref_status if type(raw_ref_status) is str else ""  # noqa: E721

        def _exact_money_value(value: Any) -> Decimal | None:
            max_finite_float = Decimal("1.7976931348623157E+308")
            if type(value) is int:  # noqa: E721 - reject bool and subclasses
                if value.bit_length() > 1024:
                    return None
                parsed = Decimal(value)
            elif type(value) is float:  # noqa: E721 - reject float subclasses
                if not math.isfinite(value):
                    return None
                parsed = Decimal(str(value))
            elif (
                type(value) is str  # noqa: E721 - reject coercible objects
                and 1 <= len(value) <= 64
                and value.strip() == value
            ):
                if re.fullmatch(
                    r"[+-]?(?:0|[1-9][0-9]*|[1-9][0-9]{0,2}(?:,[0-9]{3})+)(?:\.[0-9]+)?",
                    value,
                ) is None:
                    return None
                try:
                    parsed = Decimal(value.replace(",", ""))
                except InvalidOperation:
                    return None
            else:
                return None
            if not parsed.is_finite() or parsed.copy_abs() > max_finite_float:
                return None
            return parsed

        ref_balanced = _exact_money_value(dict.get(ref, "balanced"))
        reference_balanced = _exact_money_value(dict.get(reference, "balanced"))
        quote_shape = (
            summary_status == "ok"
            and hero_key == "ethan"
            and phase in {"reading", "bidding"}
            and session_id is not None
            and map_identity is not None
            and round_no is not None
            and type(reference_result) is dict  # noqa: E721
            and ref_status in {"ok", "count_prior"}
            and ref_balanced is not None
            and reference_balanced == ref_balanced
        )
        valid_quote = (
            quote_shape
            and strict_notes is not None
            and not has_nonlive_marker
        )
        if valid_quote:
            r5_skill_authority = round_no != 5 or "skill:1002085" in strict_notes
            if not r5_skill_authority:
                if cached_is_valid and not same_authority:
                    self._last_ethan_r5_outline_quote = None
                return summary
            self._last_ethan_r5_outline_quote = {
                "summary": summary,
                "session_id": session_id,
                "map_identity": map_identity,
                "round": round_no,
            }
            return summary

        notes = list(strict_notes) if strict_notes is not None else []
        is_outline_wait = (
            summary_status == "ok"
            and hero_key == "ethan"
            and phase in {"reading", "bidding"}
            and session_id is not None
            and map_identity is not None
            and round_no == 5
            and ref_status == "missing_total_count"
            and "hero_ref_waiting" in notes
            and "hero_ref_wait:全仓轮廓技能帧" in notes
        )
        if (
            summary_status == "ok"
            and hero_key == "ethan"
            and phase in {"reading", "bidding"}
            and same_authority
            and round_no == 5
            and ref_status != "error"
            and not is_outline_wait
        ):
            # A malformed or incomplete same-authority R5 row is a frame candidate,
            # not a cache reset and not a replacement.  Preserve the last monotonic
            # authority until either a valid skill result, an explicit wait, or an
            # explicit error/terminal state arrives.
            return summary
        if not is_outline_wait:
            # This includes terminal, process/error and any different semantic state.
            # It makes a previous quote ineligible before the next render rather than
            # accidentally carrying it into an unrelated capture.
            self._last_ethan_r5_outline_quote = None
            return summary

        if type(cached) is not dict:  # noqa: E721 - cache boundary
            return summary
        source_summary = dict.get(cached, "summary")
        source_round = dict.get(cached, "round")
        if (
            not cached_is_valid
            or type(source_summary) is not dict  # noqa: E721
            or dict.get(cached, "session_id") != session_id
            or dict.get(cached, "map_identity") != map_identity
            or type(source_round) is not int  # noqa: E721
            or source_round > round_no
        ):
            self._last_ethan_r5_outline_quote = None
            return summary

        source_reference = dict.get(source_summary, "reference")
        current_reference = dict.get(summary, "reference")
        if type(source_reference) is not dict:  # noqa: E721
            self._last_ethan_r5_outline_quote = None
            return summary
        display = dict(summary)
        reference = dict(source_reference)
        if type(current_reference) is dict:  # noqa: E721
            # These values come from the newest snapshot/UI contract, not the retained quote.
            for key in (
                "v3_conservative",
                "v3_balanced",
                "v3_aggressive",
                "risk_band",
                "current_highest",
            ):
                if key in current_reference:
                    reference[key] = current_reference[key]
        reference["readiness"] = "ethan_r5_outline_updating"
        display["reference"] = reference
        current_composition = dict.get(summary, "quality_composition")
        preserve_current_composition_conflict = (
            type(current_composition) is dict  # noqa: E721
            and type(dict.get(current_composition, "schema_version")) is int  # noqa: E721
            and dict.get(current_composition, "schema_version")
            == QUALITY_COMPOSITION_SCHEMA_VERSION
            and type(dict.get(current_composition, "status")) is str  # noqa: E721
            and dict.get(current_composition, "status") == "conflict"
        )
        for key in (
            "red",
            "quality_composition",
            "red_outline_value_summary",
            "q5_total_cells_value_summary",
        ):
            if key == "quality_composition" and preserve_current_composition_conflict:
                continue
            if key in source_summary:
                display[key] = source_summary[key]
        retained_ref = dict(
            source_summary.get("ahmed_ref")
            if type(source_summary.get("ahmed_ref")) is dict
            else {}
        )
        retained_raw_notes = retained_ref.get("notes")
        retained_notes = (
            [str(note) for note in retained_raw_notes if str(note).strip()]
            if isinstance(retained_raw_notes, (list, tuple))
            else []
        )
        if "ethan_r5_outline_transition" not in retained_notes:
            retained_notes.append("ethan_r5_outline_transition")
        retained_ref["notes"] = retained_notes
        display["ahmed_ref"] = retained_ref
        current_evidence = summary.get("evidence")
        evidence = dict(current_evidence) if type(current_evidence) is dict else {}
        evidence["ref_status"] = "ethan_r5_outline_updating"
        evidence["ref_readiness"] = "ethan_r5_outline_updating"
        evidence["ref_notes"] = ";".join(retained_notes)
        display["evidence"] = evidence
        current_flags = summary.get("flags")
        flags: list[dict[str, str]] = [
            {
                "label": "R5全仓轮廓更新中",
                "level": "watch",
                "detail": f"沿用本局同图R{source_round}有效报价，等待全仓轮廓技能帧",
            }
        ]
        if type(current_flags) is list:  # noqa: E721
            flags.extend(flag for flag in current_flags if type(flag) is dict)
        display["flags"] = flags
        display["continuity"] = {
            "reason": "ethan_r5_outline_updating",
            "source_round": source_round,
        }
        return display

    def _current_joint_exact_quality_payloads(
        self,
        summary: dict[str, Any],
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        """Validate current per-quality hard facts for continuity rendering.

        The panel has already checked the engine joint, both marginals, ranges,
        lock metadata and evidence.  This second JSON boundary is intentionally
        strict: a malformed quality is ignored independently and can never turn
        cached candidates or ranges into a new lock.
        """
        if "stale" in summary:
            return {}, {}
        red = dict.get(summary, "red")
        if type(red) is not dict or any(  # noqa: E721 - exact JSON object required
            type(raw_key) is not str for raw_key in red
        ):
            return {}, {}
        raw_facts = dict.get(red, "quality_joint_exact_facts")
        if type(raw_facts) is not dict or any(  # noqa: E721
            type(raw_key) is not str for raw_key in raw_facts
        ):
            return {}, {}

        payloads: dict[str, dict[str, Any]] = {}
        accepted_facts: dict[str, dict[str, Any]] = {}
        for key in QUALITY_DISPLAY_ORDER:
            raw_fact = dict.get(raw_facts, key)
            if type(raw_fact) is not dict:  # noqa: E721
                continue
            if any(type(raw_key) is not str for raw_key in raw_fact):
                continue
            if set(raw_fact) != {"count", "cells", "source"}:
                continue
            count = dict.get(raw_fact, "count")
            cells = dict.get(raw_fact, "cells")
            source = dict.get(raw_fact, "source")
            if (
                type(count) is not int
                or count < 0
                or type(cells) is not int
                or cells < 0
                or type(source) is not str
                or source != "joint_exact"
            ):
                continue
            payloads[key] = {
                "status": "exact",
                "source": "current_joint_exact_fact",
                "lock_strength": "exact",
                "lock_reason": "exact_lock",
                "candidates": [
                    {
                        "count": count,
                        "cells": cells,
                        "probability": 1.0,
                    }
                ],
                "count_candidates": [{"value": count, "probability": 1.0}],
                "cells_candidates": [{"value": cells, "probability": 1.0}],
                "shown_probability": 1.0,
                "other_probability": 0.0,
                "truncated": False,
                "cap_hit": False,
                "pricing_posterior": True,
                "display_reconciled": False,
                "count_lock_value": count,
                "cells_lock_value": cells,
                "dimension_state": "锁定",
                "reason": "",
            }
            accepted_facts[key] = {
                "count": count,
                "cells": cells,
                "source": source,
            }
        return payloads, accepted_facts

    def _summary_with_quality_continuity(
        self,
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        """Keep the last verified same-session view through one bad composition update.

        A malformed root composition cannot authorize a fallback reconstruction from
        legacy count/cell ranges.  It also should not blank an auction that is still
        running.  The only safe continuity source is a previously rendered canonical
        ready/exact payload from the same session, shown with an explicit visible marker.
        """
        if type(summary) is not dict:  # noqa: E721 - internal JSON boundary
            return summary
        # Continuity and quote receipts share one cache authority: only the
        # exact, non-empty builtin string from ``context.session_id``.  Root
        # fallback remains available to unrelated manual-UI helpers, but must
        # never authorize caching or consuming current exact quality facts.
        session_id = _strict_quote_summary_session_id(summary)
        phase = self._summary_phase(summary)
        cached_session = _text(
            getattr(self, "_last_verified_quality_session_id", ""),
            "",
        ).strip()
        summary_is_stale = "stale" in summary
        if session_id and cached_session and session_id != cached_session:
            self._last_verified_quality_summary = {}
            self._last_verified_quality_session_id = ""
            self._quality_unavailable_continuity_identity = None
            cached_session = ""

        view = detail_composition_view(summary)
        q6_view = view.quality("q6")
        q6_verified = (
            q6_view.status in {"ready", "exact"}
            and bool(q6_view.candidates)
        )
        q6_transient_unavailable = (
            view.status in {"ready", "exact", "unavailable"}
            and q6_view.status == "unavailable"
            and not q6_view.candidates
        )
        ref_result = summary.get("ahmed_ref")
        ref_error = (
            type(ref_result) is dict
            and dict.get(ref_result, "status") == "error"
        )
        if (
            summary.get("status") == "ok"
            and phase in {"reading", "bidding"}
            and view.status in {"ready", "exact"}
            and q6_verified
            and not ref_error
            and not summary_is_stale
            and session_id is not None
        ):
            self._last_verified_quality_summary = summary
            self._last_verified_quality_session_id = session_id
            self._quality_unavailable_continuity_identity = None
            return summary

        if phase == "settled":
            self._last_verified_quality_summary = {}
            self._last_verified_quality_session_id = ""
            self._quality_unavailable_continuity_identity = None
            return summary
        if q6_transient_unavailable and (summary_is_stale or ref_error):
            return summary
        if (
            summary.get("status") != "ok"
            or phase not in {"reading", "bidding"}
            or (
                view.status != "conflict"
                and not ref_error
                and not q6_transient_unavailable
            )
            or not session_id
            or session_id != cached_session
        ):
            return summary

        cached = getattr(self, "_last_verified_quality_summary", None)
        if type(cached) is not dict or not cached:  # noqa: E721
            return summary
        cached_view = detail_composition_view(cached)
        cached_q6 = cached_view.quality("q6")
        if (
            cached_view.status not in {"ready", "exact"}
            or cached_q6.status not in {"ready", "exact"}
            or not cached_q6.candidates
        ):
            return summary

        cached_context = cached.get("context")
        current_context = summary.get("context")
        q6_pending_identity: tuple[str, int, int] | None = None
        if q6_transient_unavailable:
            if (
                type(cached_context) is not dict  # noqa: E721
                or type(current_context) is not dict  # noqa: E721
            ):
                return summary
            cached_map = dict.get(cached_context, "map_id")
            current_map = dict.get(current_context, "map_id")
            cached_round = dict.get(cached_context, "round")
            current_round = dict.get(current_context, "round")
            if (
                type(cached_map) is not int  # noqa: E721
                or cached_map <= 0
                or type(current_map) is not int  # noqa: E721
                or current_map != cached_map
                or type(cached_round) is not int  # noqa: E721
                or cached_round < 0
                or type(current_round) is not int  # noqa: E721
                or current_round != cached_round + 1
            ):
                return summary
            q6_pending_identity = (session_id, current_map, current_round)
            if (
                getattr(
                    self,
                    "_quality_unavailable_continuity_identity",
                    None,
                )
                == q6_pending_identity
            ):
                return summary
            self._quality_unavailable_continuity_identity = q6_pending_identity

        display = dict(cached)
        merged_context = (
            dict(cached_context) if type(cached_context) is dict else {}
        )
        if type(current_context) is dict:  # noqa: E721
            for key in ("phase", "round", "session_id", "hero", "map_id"):
                value = dict.get(current_context, key)
                if type(value) in {str, int, float} and type(value) is not bool:
                    merged_context[key] = value
        display["context"] = merged_context

        source_round = _text(
            cached_context.get("round")
            if type(cached_context) is dict
            else "",
            "?",
        )
        continuity_reason = (
            "ref_engine_transient_error"
            if ref_error
            else "quality_q6_next_round_pending"
            if q6_transient_unavailable
            else "quality_composition_conflict"
        )
        current_joint_payloads: dict[str, dict[str, Any]] = {}
        current_joint_facts: dict[str, dict[str, Any]] = {}
        if (
            continuity_reason == "quality_composition_conflict"
            and os.environ.get(CURRENT_JOINT_EXACT_CONTINUITY_DISABLE_ENV) != "1"
        ):
            current_joint_payloads, current_joint_facts = (
                self._current_joint_exact_quality_payloads(summary)
            )
            cached_composition = dict.get(cached, "quality_composition")
            cached_qualities = (
                dict.get(cached_composition, "qualities")
                if type(cached_composition) is dict
                else None
            )
            cached_red = dict.get(cached, "red")
            if (
                type(cached_composition) is not dict
                or dict.get(cached_composition, "status") not in {"ready", "exact"}
                or type(cached_qualities) is not dict
                or any(type(raw_key) is not str for raw_key in cached_qualities)
                or type(cached_red) is not dict
                or any(type(raw_key) is not str for raw_key in cached_red)
            ):
                current_joint_payloads = {}
                current_joint_facts = {}
            elif current_joint_payloads:
                merged_composition = dict(cached_composition)
                merged_qualities = dict(cached_qualities)
                for key, payload in current_joint_payloads.items():
                    merged_qualities[key] = payload
                merged_composition["qualities"] = merged_qualities
                display["quality_composition"] = merged_composition

                merged_red = dict(cached_red)
                merged_red["quality_joint_exact_facts"] = current_joint_facts
                display["red"] = merged_red
        continuity = {
            "reason": continuity_reason,
            "source_round": source_round,
        }
        if current_joint_payloads:
            continuity["current_joint_exact_qualities"] = sorted(
                current_joint_payloads
            )
        display["continuity"] = continuity
        current_flags = summary.get("flags")
        flags: list[dict[str, str]] = [
            {
                "label": f"沿用R{source_round}",
                "level": "watch",
                "detail": (
                    "当前计算异常，保留本局上一份已验证结果并重试一次"
                    if ref_error
                    else (
                        f"R{merged_context.get('round')} 红品联合候选更新中；"
                        "仅本帧沿用上一轮已验证结果"
                    )
                    if q6_pending_identity is not None
                    else (
                        "当前组合更新冲突；沿用上一轮报价，当前轮独立硬事实已更新"
                        if current_joint_payloads
                        else "当前组合更新冲突；显示本局上一份已验证结果"
                    )
                ),
            }
        ]
        if type(current_flags) is list:  # noqa: E721
            for raw_flag in current_flags[:3]:
                if type(raw_flag) is not dict:  # noqa: E721
                    continue
                flag: dict[str, str] = {}
                for key in ("label", "level", "detail"):
                    value = dict.get(raw_flag, key)
                    if type(value) is str:
                        flag[key] = value
                if flag:
                    flags.append(flag)
        display["flags"] = flags
        return display

    def _summary_with_quality_retry_pending(
        self,
        summary: dict[str, Any],
        *,
        reason: str,
    ) -> dict[str, Any]:
        """Render the first transient composition failure as a bounded retry.

        A live snapshot can briefly expose an incomplete joint while related skill
        fields are still arriving.  Showing every quality as ``冲突`` for that single
        pass is noisy and was observed to recover on the next refresh.  Do not invent
        values or hide a persistent conflict: the first exact problem signature gets
        one clean pending presentation, while a repeated result is rendered normally.
        """
        display = dict(summary)
        reference = summary.get("reference")
        pending_reference = dict(reference) if type(reference) is dict else {}
        pending_reference.update(
            {
                "conservative": "-",
                "balanced": "-",
                "aggressive": "-",
                "action": "重新校验本轮输入",
            }
        )
        display["reference"] = pending_reference
        display["red"] = {}
        display["ahmed_ref"] = {"status": "loading"}
        display["quality_composition"] = {
            "status": "loading",
            "reason": "retrying_current_round_inputs",
        }
        display["continuity"] = {
            "reason": "quality_composition_retrying",
            "problem": reason,
        }
        display["flags"] = [
            {
                "label": "组合校验中",
                "level": "watch",
                "detail": "本轮输入正在合并，自动重试一次",
            }
        ]
        return display

    def _should_reset_manual_for_summary(self, data: dict[str, Any]) -> bool:
        if not hasattr(self, "manual_entries"):
            return False
        has_manual_state = (
            self._manual_active
            or getattr(self, "_manual_edit_enabled", False)
            or self._has_manual_inputs()
            or bool(_text(getattr(self, "_manual_live_session_id", ""), "").strip())
            or bool(getattr(self, "_manual_snapshot", {}))
        )
        if not has_manual_state:
            return False
        stale = data.get("stale") if isinstance(data.get("stale"), dict) else {}
        reason = _text(stale.get("reason"), "")
        if reason in {"session_ahead", "settled_stale"}:
            return True
        truth = data.get("truth") if isinstance(data.get("truth"), dict) else {}
        if self._summary_phase(data) == "settled" and bool(truth.get("available")):
            if bool(getattr(self, "_manual_settlement_edit_unlocked", False)):
                current_session_id = self._summary_session_id(data)
                previous_session_id = _text(
                    getattr(self, "_manual_live_session_id", ""),
                    "",
                ).strip()
                if not previous_session_id or current_session_id == previous_session_id:
                    return False
            return True
        current_session_id = self._summary_session_id(data)
        previous_session_id = _text(getattr(self, "_manual_live_session_id", ""), "").strip()
        return bool(current_session_id and previous_session_id and current_session_id != previous_session_id)

    def _reset_manual_state(self, status_text: str = "已清空，回到实时", *, status_fg: str = DIM) -> None:
        self._manual_active = False
        self._manual_edit_enabled = False
        self._manual_settlement_edit_unlocked = False
        self._manual_snapshot = {}
        self._manual_summary = {}
        for key in tuple(getattr(self, "manual_entries", {})):
            self._set_manual_entry(key, "", track_auto=False)
        self._manual_dirty_fields.clear()
        self._manual_autofill_values.clear()
        self._manual_live_session_id = ""
        if hasattr(self, "manual_status"):
            self.manual_status.configure(text=status_text, fg=status_fg)
        if hasattr(self, "manual_buttons"):
            self._set_manual_edit_enabled(False)
            self._set_manual_button_state()

    def _manual_values_from_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        context = data.get("context") if isinstance(data.get("context"), dict) else {}
        ahmed_ref = data.get("ahmed_ref") if isinstance(data.get("ahmed_ref"), dict) else {}
        evidence = ahmed_ref.get("evidence") if isinstance(ahmed_ref.get("evidence"), dict) else {}
        values: dict[str, Any] = {}
        hero = _supported_manual_hero_display(context.get("hero"), evidence.get("hero"))
        if hero not in (None, ""):
            values["hero"] = hero
        map_id = context.get("map_id") or evidence.get("map_id")
        if map_id not in (None, ""):
            values["map_id"] = map_id
        if evidence.get("total_count") not in (None, ""):
            values["total_count"] = evidence.get("total_count")
        if evidence.get("total_grid_target") not in (None, ""):
            # 总格=整格：用 _compact_grid_cells 取整，避免把 footroom 浮点(如 61.44)显示给用户。
            values["total_cells"] = _compact_grid_cells(evidence.get("total_grid_target"))
            total_count = _to_optional_float(evidence.get("total_count"))
            total_cells = _to_optional_float(evidence.get("total_grid_target"))
            if total_count is not None and total_count > 0 and total_cells is not None:
                values["total_avg"] = _format_manual_number(total_cells / total_count)
        avg_cells = evidence.get("avg_cells") if isinstance(evidence.get("avg_cells"), dict) else {}
        split_avg_cells = (
            evidence.get("split_avg_cells")
            if isinstance(evidence.get("split_avg_cells"), dict)
            else {}
        )
        quality_cells = (
            evidence.get("quality_cells")
            if isinstance(evidence.get("quality_cells"), dict)
            else {}
        )
        avg_values = evidence.get("avg_values") if isinstance(evidence.get("avg_values"), dict) else {}
        quality_values = (
            evidence.get("quality_values")
            if isinstance(evidence.get("quality_values"), dict)
            else {}
        )
        split_quality_cells = (
            evidence.get("split_quality_cells")
            if isinstance(evidence.get("split_quality_cells"), dict)
            else {}
        )
        quality_cell_ranges = (
            ahmed_ref.get("quality_cells_ranges")
            if isinstance(ahmed_ref.get("quality_cells_ranges"), dict)
            else {}
        )
        quality_count_ranges = (
            ahmed_ref.get("quality_count_ranges")
            if isinstance(ahmed_ref.get("quality_count_ranges"), dict)
            else {}
        )
        fixed_counts = evidence.get("fixed_counts") if isinstance(evidence.get("fixed_counts"), dict) else {}
        split_counts = evidence.get("split_counts") if isinstance(evidence.get("split_counts"), dict) else {}
        count_sums = evidence.get("count_sums") if isinstance(evidence.get("count_sums"), dict) else {}
        for split_key in SPLIT_QUALITY_INPUT_KEYS:
            avg_value = _to_optional_float(split_avg_cells.get(split_key))
            if avg_value not in (None, ""):
                values[f"{split_key}_avg"] = _format_manual_number(avg_value)
            cell_value = split_quality_cells.get(split_key)
            count_value = _to_optional_int(split_counts.get(split_key))
            if count_value is None and cell_value not in (None, "") and avg_value is not None:
                count_value = _manual_avg_count_from_cells(avg_value, cell_value)
            if count_value is not None:
                values[f"{split_key}_count"] = count_value
            if cell_value in (None, "") and count_value is not None and avg_value is not None:
                grid_options = _manual_avg_grid_options(count_value, avg_value)
                if len(grid_options) == 1:
                    cell_value = grid_options[0]
            if cell_value not in (None, ""):
                values[f"{split_key}_cells"] = _format_manual_number(cell_value)
        for quality in QUALITY_INPUT_KEYS:
            avg_value = _to_optional_float(avg_cells.get(quality))
            if avg_value not in (None, ""):
                values[f"{quality}_avg"] = _format_manual_number(avg_value)
            cell_value = quality_cells.get(quality)
            if cell_value in (None, ""):
                raw_range = quality_cell_ranges.get(quality)
                if isinstance(raw_range, (list, tuple)) and len(raw_range) >= 3:
                    first, middle, last = raw_range[0], raw_range[1], raw_range[2]
                    if first not in (None, "") and first == middle == last:
                        cell_value = middle
            count_value = _to_optional_int(fixed_counts.get(quality))
            if count_value is None and cell_value not in (None, "") and avg_value is not None:
                count_value = _manual_avg_count_from_cells(avg_value, cell_value)
            if count_value is None:
                raw_count_range = quality_count_ranges.get(quality)
                if isinstance(raw_count_range, (list, tuple)) and len(raw_count_range) >= 3:
                    first, middle, last = raw_count_range[0], raw_count_range[1], raw_count_range[2]
                    if first not in (None, "") and first == middle == last:
                        count_value = _to_optional_int(middle)
            if count_value is not None:
                values[f"{quality}_count"] = count_value
            if cell_value in (None, "") and count_value is not None and avg_value is not None:
                grid_options = _manual_avg_grid_options(count_value, avg_value)
                if len(grid_options) == 1:
                    cell_value = grid_options[0]
            if cell_value not in (None, ""):
                values[f"{quality}_cells"] = _format_manual_number(cell_value)
            if avg_values.get(quality) not in (None, ""):
                values[f"{quality}_avg_value"] = _format_manual_number(avg_values[quality])
            if quality_values.get(quality) not in (None, ""):
                values[f"{quality}_value_sum"] = _format_manual_number(quality_values[quality])
        count_sum_value = count_sums.get("q4q5q6")
        if count_sum_value in (None, ""):
            count_sum_value = count_sums.get("q4q5")
        if count_sum_value not in (None, ""):
            values["q4q5_count"] = count_sum_value
        return values

    def _manual_context_fallback_values(self) -> dict[str, Any]:
        for data in (
            getattr(self, "_last_live_summary", {}),
            getattr(self, "_last_summary", {}),
        ):
            if not isinstance(data, dict) or not data:
                continue
            if data.get("status") == "stale_snapshot":
                continue
            if self._summary_phase(data) == "manual":
                continue
            values = self._manual_values_from_summary(data)
            fallback: dict[str, Any] = {}
            hero = _normalize_manual_hero(values.get("hero"))
            if is_supported_ref_hero(hero):
                fallback["hero"] = hero
            map_id = _to_optional_int(values.get("map_id"))
            if map_id is not None:
                fallback["map_id"] = map_id
            if fallback:
                return fallback
        return {}

    def _auto_sync_manual_inputs(self, data: dict[str, Any]) -> None:
        if not hasattr(self, "manual_entries") or self._manual_active or getattr(self, "_manual_edit_enabled", False):
            return
        if data.get("status") == "stale_snapshot" or self._summary_phase(data) == "settled":
            return
        # Locked-state read-only backfill: mirror the live evidence into the (disabled)
        # detail fields even before the user ever opens 手填, so the panel shows real
        # values instead of blanks. This runs ONLY while editing is disabled (guarded
        # above), so the user cannot have diverged a field yet; entering 手填 unlocks
        # override and the per-field guards below then protect any edited value. Seeding
        # the form on first live frame is intentional — it is the read-only mirror, not
        # user-entered manual state (which only the 手填/应用 path produces).
        values = self._manual_values_from_summary(data)
        for key, value in values.items():
            current = self._manual_entry_text(key)
            last_auto = self._manual_autofill_values.get(key)
            if key in self._manual_dirty_fields:
                continue
            if current and last_auto is not None and current != last_auto:
                self._manual_dirty_fields.add(key)
                continue
            if current and last_auto is None and key not in AUTO_REPLACE_MANUAL_FIELDS:
                continue
            display_value = _manual_map_display_value(value) if key == "map_id" else value
            self._set_manual_entry(key, display_value, track_auto=True)
        if self._has_manual_inputs() and not self._manual_active:
            self._manual_live_session_id = self._summary_session_id(data)
            # Fields are still locked (read-only mirror); say so. 手填 unlocks override.
            self.manual_status.configure(text="实时数据（只读），手填可改", fg=DIM)

    def _manual_inputs_snapshot(self) -> tuple[dict[str, Any] | None, str]:
        hero_text = self._manual_entry_text("hero")
        map_text = self._manual_entry_text("map_id")
        fallback_context = self._manual_context_fallback_values()
        fallback_sources: dict[str, str] = {}
        hero = _normalize_manual_hero(hero_text)
        if not hero and fallback_context.get("hero"):
            hero = _normalize_manual_hero(fallback_context.get("hero"))
            fallback_sources["hero"] = "live_context"
        if map_text:
            map_id, map_error = _manual_map_id_from_text(map_text)
        else:
            map_id = _to_optional_int(fallback_context.get("map_id"))
            map_error = ""
            if map_id is not None:
                fallback_sources["map_id"] = "live_context"
        if map_error:
            return None, map_error
        map_name = _manual_map_name(map_id)
        total_count, error = _to_manual_count(self._manual_entry_text("total_count"), "总件")
        if error:
            return None, error
        total_cells, error = _to_manual_nonnegative_float(
            self._manual_entry_text("total_cells"),
            "总格",
        )
        if error:
            return None, error
        if total_cells is not None and total_cells > _MANUAL_MAX_GRID_CELLS:
            return None, "总格超出可计算范围"
        total_avg_text = self._manual_entry_text("total_avg")
        total_avg, error = _to_manual_nonnegative_float(total_avg_text, "全均格")
        if error:
            return None, error
        # 总件不再强制(断网备用: 群友常只能从画面数出总格, 数不出总件)。只填总格→引擎锁总格,
        # overlay 推红格(总格−已知品质格)。至少一项数值输入的兜底在下方 ref_inputs 组装前。
        if not hero:
            return None, "需要填写英雄，或先点填入当前"
        if not is_supported_ref_hero(hero):
            unsupported = hero_text.strip() if isinstance(hero_text, str) else str(hero)
            return None, f"暂不支持英雄：{unsupported or hero}"
        if map_id is None:
            return None, "需要填写地图，或先点填入当前"
        if total_count == 0:
            return None, "总件需要大于0"
        if total_cells == 0:
            return None, "总格需要大于0"
        if total_cells is None and total_avg is not None:
            if total_count is None:
                # 全均格 = 总格 / 总件, 无总件无法定总格。断网请直接填总格。
                return None, "只填全均格无法定总格；请改填总格，或补总件"
            if _manual_finite_product(total_avg, total_count) is None:
                return None, "全均格超出可计算范围"
            grid_options = _manual_avg_grid_options_from_text(
                total_count,
                total_avg,
                total_avg_text,
            )
            if len(grid_options) != 1:
                return None, "全均格对不上整数总格，改填总格或只留总件"
            total_cells = grid_options[0]
        elif total_cells is not None:
            total_cells_int = int(round(total_cells))
            if abs(float(total_cells) - total_cells_int) > 0.0001:
                return None, "总格需要整数"
            # 全均格匹配校验只在有总件时做(全均格=总格/总件需要总件)。
            if (
                total_avg is not None
                and total_count is not None
                and not _manual_avg_matches_cells(
                    total_avg,
                    avg_text=total_avg_text,
                    count=total_count,
                    cells=total_cells_int,
                )
            ):
                return None, "全均格与总格/总件不一致"
            total_cells = total_cells_int
        if (
            total_count is not None
            and total_cells is not None
            and can_compose_grid_total is not None
            and not can_compose_grid_total(total_count, total_cells)
        ):
            return None, f"总件{total_count}件无法组成{total_cells}格"
        avg_cells: dict[str, float] = {}
        quality_cells: dict[str, int] = {}
        avg_values: dict[str, float] = {}
        quality_values: dict[str, int] = {}
        fixed_counts: dict[str, int] = {}
        split_avg_cells: dict[str, float] = {}
        split_quality_cells: dict[str, int] = {}
        split_counts: dict[str, int] = {}
        split_avg_values: dict[str, float] = {}
        split_quality_values: dict[str, int] = {}
        count_sums: dict[str, int] = {}
        for key in (*SPLIT_QUALITY_INPUT_KEYS, *QUALITY_INPUT_KEYS):
            label = _manual_quality_label(key)
            avg_text = self._manual_entry_text(f"{key}_avg")
            avg, error = _to_manual_nonnegative_float(avg_text, f"{label}均格")
            if error:
                return None, error
            count, error = _to_manual_count(
                self._manual_entry_text(f"{key}_count"),
                f"{label}件",
            )
            if error:
                return None, error
            cells, error = _to_manual_cells(
                self._manual_entry_text(f"{key}_cells"),
                f"{label}格",
            )
            if error:
                return None, error
            avg_value, error = _to_manual_nonnegative_float(
                self._manual_entry_text(f"{key}_avg_value"),
                f"{label}均价",
            )
            if error:
                return None, error
            value_sum, error = _to_manual_value_sum(
                self._manual_entry_text(f"{key}_value_sum"),
                f"{label}总价",
            )
            if error:
                return None, error
            zero_fields: list[str] = []
            nonzero_fields: list[str] = []
            for value, field_label in (
                (avg, f"{label}均格"),
                (count, f"{label}件"),
                (cells, f"{label}格"),
                (avg_value, f"{label}均价"),
                (value_sum, f"{label}总价"),
            ):
                if value == 0:
                    zero_fields.append(field_label)
                elif value is not None:
                    nonzero_fields.append(field_label)
            if zero_fields:
                if nonzero_fields:
                    return None, (
                        f"{'/'.join(zero_fields)}为0时，"
                        f"{'和'.join(nonzero_fields)}也必须为0"
                    )
                if avg is None:
                    avg = 0.0
                    self._set_empty_manual_entry_auto(f"{key}_avg", 0)
                if count is None:
                    count = 0
                    self._set_empty_manual_entry_auto(f"{key}_count", 0)
                if cells is None:
                    cells = 0
                    self._set_empty_manual_entry_auto(f"{key}_cells", 0)
                if avg_value is None:
                    avg_value = 0.0
                    self._set_empty_manual_entry_auto(f"{key}_avg_value", 0)
                if value_sum is None:
                    value_sum = 0
                    self._set_empty_manual_entry_auto(f"{key}_value_sum", 0)
            if count is None and avg is not None and cells is not None:
                derived_count, derived_count_error = _manual_avg_count_from_cells_text(
                    avg,
                    cells,
                    avg_text,
                    count_label=f"{label}件",
                )
                if derived_count_error:
                    return None, derived_count_error
                if derived_count is None:
                    if avg == 0:
                        return None, (
                            f"{label}均格为0时，{label}件和{label}格都必须为0"
                        )
                    return None, (
                        f"{label}均格与{label}格"
                        "无法对应到整数件数"
                    )
                count = derived_count
            if avg == 0:
                zero_parts: list[str] = []
                if count not in (None, 0):
                    zero_parts.append(f"{label}件")
                if cells not in (None, 0):
                    zero_parts.append(f"{label}格")
                if zero_parts:
                    return None, f"{label}均格为0时，{'和'.join(zero_parts)}也必须为0"
                if count is None:
                    count = 0
                    self._set_empty_manual_entry_auto(f"{key}_count", 0)
                if cells is None:
                    cells = 0
                    self._set_empty_manual_entry_auto(f"{key}_cells", 0)
            if count is not None:
                count, count_error = _to_manual_count(count, f"{label}件")
                if count_error:
                    return None, count_error
                if key in SPLIT_QUALITY_INPUT_KEYS:
                    split_counts[key] = count
                else:
                    fixed_counts[key] = count
                if avg is not None and cells is None:
                    if _manual_finite_product(avg, count) is None:
                        return None, f"{label}均格超出可计算范围"
                    grid_options = _manual_avg_grid_options_from_text(count, avg, avg_text)
                    if not grid_options:
                        if avg == 0:
                            return None, f"{label}均格为0时，{label}件也必须为0"
                        return None, (
                            f"{label}均格与{label}件"
                            "无法对应到整数格数"
                        )
            if cells is not None:
                if key in SPLIT_QUALITY_INPUT_KEYS:
                    split_quality_cells[key] = cells
                else:
                    quality_cells[key] = cells
                if count is not None:
                    if (
                        can_compose_grid_total is not None
                        and not can_compose_grid_total(count, cells)
                    ):
                        return None, f"{label}{count}件无法组成{cells}格"
                    if count <= 0:
                        if cells != 0:
                            return None, f"{label}件为0时格数也必须为0"
                        derived_avg = 0.0
                    else:
                        derived_avg = cells / count
                    if avg is None:
                        avg = derived_avg
                    elif _manual_avg_matches_cells(
                        avg,
                        avg_text=avg_text,
                        count=count,
                        cells=cells,
                    ):
                        avg = derived_avg
                    else:
                        return None, (
                            f"{label}均格与{label}格/"
                            f"{label}件不一致"
                        )
            if avg is not None:
                if key in SPLIT_QUALITY_INPUT_KEYS:
                    split_avg_cells[key] = avg
                else:
                    avg_cells[key] = avg
            if count == 0:
                if avg_value not in (None, 0):
                    return None, f"{label}件为0时{label}均价也必须为0"
                if value_sum not in (None, 0):
                    return None, f"{label}件为0时{label}总价也必须为0"
            elif count is not None and avg_value is not None and value_sum is not None:
                if not _manual_value_sum_matches_avg(
                    avg_value,
                    count=count,
                    value_sum=value_sum,
                ):
                    return None, (
                        f"{label}均价与{label}总价/"
                        f"{label}件不一致"
                    )
            if key in SPLIT_QUALITY_INPUT_KEYS:
                if avg_value is not None:
                    split_avg_values[key] = avg_value
                if value_sum is not None:
                    split_quality_values[key] = value_sum
            else:
                if avg_value is not None:
                    avg_values[key] = avg_value
                if value_sum is not None:
                    quality_values[key] = value_sum
        if all(key in split_counts for key in SPLIT_QUALITY_INPUT_KEYS):
            split_count_total = sum(split_counts[key] for key in SPLIT_QUALITY_INPUT_KEYS)
            split_count_total, split_count_error = _to_manual_count(
                split_count_total,
                "白绿件",
            )
            if split_count_error:
                return None, split_count_error
            if (
                fixed_counts.get("q1") is not None
                and fixed_counts["q1"] != split_count_total
            ):
                return None, (
                    f"白{split_counts['white']}件+绿{split_counts['green']}件"
                    f"与白绿{fixed_counts['q1']}件不一致"
                )
        elif fixed_counts.get("q1") is not None:
            known_split_count = sum(split_counts.values())
            if known_split_count > fixed_counts["q1"]:
                return None, "已填白/绿件数超过白绿件数"
        if all(key in split_quality_cells for key in SPLIT_QUALITY_INPUT_KEYS):
            split_cells_total = sum(
                split_quality_cells[key] for key in SPLIT_QUALITY_INPUT_KEYS
            )
            if (
                quality_cells.get("q1") is not None
                and quality_cells["q1"] != split_cells_total
            ):
                return None, (
                    f"白{split_quality_cells['white']}格+"
                    f"绿{split_quality_cells['green']}格"
                    f"与白绿{quality_cells['q1']}格不一致"
                )
        elif quality_cells.get("q1") is not None:
            known_split_cells = sum(split_quality_cells.values())
            if known_split_cells > quality_cells["q1"]:
                return None, "已填白/绿格数超过白绿格数"

        low_quality_count = fixed_counts.get("q1")
        if low_quality_count is None:
            low_quality_count = sum(split_counts.values())
        known_quality_count = low_quality_count + sum(
            fixed_counts.get(key, 0) for key in ("q3", "q4", "q5", "q6")
        )
        known_quality_count, known_quality_count_error = _to_manual_count(
            known_quality_count,
            "已填品质件数合计",
        )
        if known_quality_count_error:
            return None, known_quality_count_error
        if total_count is not None and known_quality_count > total_count:
            return None, (
                f"已填品质件数合计{known_quality_count}超过总件{total_count}"
            )
        low_quality_cells = quality_cells.get("q1")
        if low_quality_cells is None:
            low_quality_cells = sum(split_quality_cells.values())
        known_quality_cells = low_quality_cells + sum(
            quality_cells.get(key, 0) for key in ("q3", "q4", "q5", "q6")
        )
        if total_cells is not None and known_quality_cells > total_cells:
            return None, (
                f"已填品质格数合计{known_quality_cells}超过总格{total_cells}"
            )
        split_value_inputs = {
            key
            for key, value in split_avg_values.items()
            if value not in (None, 0)
        } | {
            key
            for key, value in split_quality_values.items()
            if value not in (None, 0)
        }
        if split_value_inputs:
            missing = [
                SPLIT_QUALITY_LABELS[key]
                for key in SPLIT_QUALITY_INPUT_KEYS
                if key not in split_value_inputs and split_counts.get(key) != 0
            ]
            if missing:
                return None, f"白/绿价值需同时填写；缺少{'、'.join(missing)}，或改填白绿行"
            split_value_sums: dict[str, int] = {}
            for split_key in SPLIT_QUALITY_INPUT_KEYS:
                value_sum = split_quality_values.get(split_key)
                if value_sum is None and split_counts.get(split_key) == 0:
                    value_sum = 0
                if value_sum is None:
                    split_count = split_counts.get(split_key)
                    split_avg_value = split_avg_values.get(split_key)
                    if split_count is None or split_avg_value is None:
                        return None, (
                            f"{SPLIT_QUALITY_LABELS[split_key]}均价需要配合"
                            f"{SPLIT_QUALITY_LABELS[split_key]}件或总价"
                        )
                    value_sum = int(round(split_avg_value * split_count))
                split_value_sums[split_key] = value_sum
            q1_value_sum = sum(split_value_sums.values())
            existing_q1_value_sum = quality_values.get("q1")
            if (
                existing_q1_value_sum is not None
                and abs(float(existing_q1_value_sum) - float(q1_value_sum)) > 0.5
            ):
                return None, "白/绿总价合计与白绿总价不一致"
            quality_values["q1"] = q1_value_sum
            q1_count_for_value = fixed_counts.get("q1")
            if q1_count_for_value is None and all(key in split_counts for key in SPLIT_QUALITY_INPUT_KEYS):
                q1_count_for_value = sum(split_counts[key] for key in SPLIT_QUALITY_INPUT_KEYS)
            if q1_count_for_value is not None:
                if q1_count_for_value <= 0:
                    derived_q1_avg_value = 0.0 if q1_value_sum == 0 else None
                else:
                    derived_q1_avg_value = q1_value_sum / q1_count_for_value
                if derived_q1_avg_value is not None:
                    existing_q1_avg_value = avg_values.get("q1")
                    if (
                        existing_q1_avg_value is not None
                        and not _manual_value_sum_matches_avg(
                            existing_q1_avg_value,
                            count=q1_count_for_value,
                            value_sum=q1_value_sum,
                        )
                    ):
                        return None, "白/绿价值合计与白绿均价不一致"
                    avg_values["q1"] = derived_q1_avg_value
        q4q5_count, error = _to_manual_count(self._manual_entry_text("q4q5_count"), "紫金红件")
        if error:
            return None, error
        if q4q5_count is not None:
            known_high_count = sum(
                fixed_counts.get(key, 0) for key in ("q4", "q5", "q6")
            )
            if all(key in fixed_counts for key in ("q4", "q5", "q6")):
                if q4q5_count != known_high_count:
                    return None, (
                        f"紫金红件{q4q5_count}与已填紫/金/红"
                        f"合计{known_high_count}不一致"
                    )
            elif known_high_count > q4q5_count:
                return None, "已填紫/金/红件数超过紫金红件"
            if total_count is not None and q4q5_count > total_count:
                return None, f"紫金红件{q4q5_count}超过总件{total_count}"
            count_sums["q4q5q6"] = q4q5_count
        # 总件强制放开后的兜底: 至少要有一项实质数值输入, 否则(只有英雄/地图)无从计算。
        if not (
            total_count is not None
            or total_cells is not None
            or fixed_counts or split_counts
            or quality_cells or split_quality_cells
            or avg_cells or split_avg_cells
            or quality_values or avg_values
            or count_sums
        ):
            return None, "至少填总件或总格（或某档件/格/价）"
        ref_inputs: dict[str, Any] = {
            "total_count": total_count,
            "avg_cells": avg_cells,
            "fixed_counts": fixed_counts,
        }
        if quality_cells:
            ref_inputs["quality_cells"] = quality_cells
        if avg_values:
            ref_inputs["avg_values"] = avg_values
        if quality_values:
            ref_inputs["quality_values"] = quality_values
        if split_avg_cells:
            ref_inputs["split_avg_cells"] = split_avg_cells
        if split_quality_cells:
            ref_inputs["split_quality_cells"] = split_quality_cells
        if split_counts:
            ref_inputs["split_counts"] = split_counts
        if count_sums:
            ref_inputs["count_sums"] = count_sums
        if total_cells is not None:
            ref_inputs["total_cells"] = total_cells
        now = time.time()
        snapshot = {
            "created_at": now,
            "hero": hero,
            "map_id": map_id,
            "map_name": map_name or None,
            "phase": "manual",
            "structured_ref_inputs": ref_inputs,
            "ui_contract": {
                "context": {
                    "hero": hero,
                    "map_id": map_id,
                    "map_name": map_name or None,
                    "round": "手动",
                    "phase": "manual",
                    "session_id": "manual",
                },
                "constraints": {
                    "structured_ref_inputs": ref_inputs,
                },
                "baseline": {
                    "decision": {},
                    "posterior": {},
                },
                "source": {
                    "created_at": now,
                    "source_mode": "manual",
                    "manual_map_input": map_text,
                    "manual_context_fallback": fallback_sources,
                },
                "truth": {"available": False},
            },
        }
        return snapshot, ""

    def _manual_input_summary(self, evidence: dict[str, Any]) -> str:
        parts: list[str] = []
        total_parts: list[str] = []
        avg_parts: list[str] = []
        if evidence.get("total_count") not in (None, ""):
            total_parts.append(f"总件 {evidence['total_count']}")
        if evidence.get("total_grid_target") not in (None, ""):
            total_parts.append(f"总格 {_compact_grid_cells(evidence['total_grid_target'])}")
            total_count = _to_optional_float(evidence.get("total_count"))
            total_cells = _to_optional_float(evidence.get("total_grid_target"))
            if total_count is not None and total_count > 0 and total_cells is not None:
                avg_parts.append(f"全均格 {_format_manual_number(total_cells / total_count)}")
        else:
            estimated_total_grid = self._range_text(evidence.get("total_grid_range"), suffix="格")
            if estimated_total_grid != "-":
                total_parts.append(f"估总格 {estimated_total_grid}")
        avg_cells = evidence.get("avg_cells")
        avg_values = evidence.get("avg_values")
        quality_values = evidence.get("quality_values")
        quality_cells = evidence.get("quality_cells")
        split_avg_cells = evidence.get("split_avg_cells")
        split_quality_cells = evidence.get("split_quality_cells")
        split_counts = evidence.get("split_counts")
        if isinstance(split_avg_cells, dict):
            for key in SPLIT_QUALITY_INPUT_KEYS:
                if split_avg_cells.get(key) not in (None, ""):
                    avg_parts.append(
                        f"{SPLIT_QUALITY_LABELS[key]}均格 "
                        f"{_format_manual_number(split_avg_cells[key])}"
                    )
        if isinstance(avg_cells, dict):
            for key in QUALITY_INPUT_KEYS:
                if avg_cells.get(key) not in (None, ""):
                    avg_parts.append(f"{QUALITY_LABELS[key]}均格 {_format_manual_number(avg_cells[key])}")
        fixed_counts = evidence.get("fixed_counts")
        if isinstance(split_counts, dict):
            split_count_parts = [
                f"{SPLIT_QUALITY_LABELS[key]}件 {split_counts[key]}"
                for key in SPLIT_QUALITY_INPUT_KEYS
                if split_counts.get(key) not in (None, "")
            ]
            if split_count_parts:
                parts.append("分件 " + "，".join(split_count_parts))
        if isinstance(fixed_counts, dict):
            count_parts = [
                f"{QUALITY_LABELS[key]}件 {fixed_counts[key]}"
                for key in QUALITY_INPUT_KEYS
                if fixed_counts.get(key) not in (None, "")
            ]
            if count_parts:
                parts.append("件数 " + "，".join(count_parts))
        if isinstance(split_quality_cells, dict):
            split_cell_parts = [
                f"{SPLIT_QUALITY_LABELS[key]}格 {_format_manual_number(split_quality_cells[key])}"
                for key in SPLIT_QUALITY_INPUT_KEYS
                if split_quality_cells.get(key) not in (None, "")
            ]
            if split_cell_parts:
                parts.append("分格 " + "，".join(split_cell_parts))
        if isinstance(quality_cells, dict):
            cell_parts = [
                f"{QUALITY_LABELS[key]}格 {_format_manual_number(quality_cells[key])}"
                for key in QUALITY_INPUT_KEYS
                if quality_cells.get(key) not in (None, "")
            ]
            if cell_parts:
                parts.append("格数 " + "，".join(cell_parts))
        if isinstance(avg_values, dict):
            avg_value_parts = [
                f"{QUALITY_LABELS[key]}均价 {_format_manual_number(avg_values[key])}"
                for key in QUALITY_INPUT_KEYS
                if avg_values.get(key) not in (None, "")
            ]
            if avg_value_parts:
                parts.append("均价 " + "，".join(avg_value_parts))
        if isinstance(quality_values, dict):
            value_sum_parts = [
                f"{QUALITY_LABELS[key]}总价 {_format_manual_number(quality_values[key])}"
                for key in QUALITY_INPUT_KEYS
                if quality_values.get(key) not in (None, "")
            ]
            if value_sum_parts:
                parts.append("总价 " + "，".join(value_sum_parts))
        min_counts = evidence.get("min_counts")
        if isinstance(min_counts, dict):
            floor_parts = []
            for key in QUALITY_INPUT_KEYS:
                value = _to_optional_int(min_counts.get(key))
                fixed_value = (
                    _to_optional_int(fixed_counts.get(key))
                    if isinstance(fixed_counts, dict)
                    else None
                )
                if value is None or value <= 0 or fixed_value is not None and fixed_value >= value:
                    continue
                floor_parts.append(f"{QUALITY_LABELS[key]}≥{value}")
            if floor_parts:
                parts.append("下界 " + "，".join(floor_parts))
        count_sums = evidence.get("count_sums")
        if isinstance(count_sums, dict):
            if count_sums.get("q4q5q6") not in (None, ""):
                parts.append(f"紫金红件 {count_sums['q4q5q6']}")
            elif count_sums.get("q5q6_min") not in (None, ""):
                parts.append(
                    f"时尚+数码金红件 {count_sums['q5q6_min']}（类内精确，全仓下界）"
                )
            elif count_sums.get("q4q5") not in (None, ""):
                parts.append(f"紫金件 {count_sums['q4q5']}")
        ordered_parts = total_parts + parts + avg_parts
        return " · ".join(ordered_parts) if ordered_parts else "-"

    def _range_text(self, values: Any, *, money: bool = False, suffix: str = "") -> str:
        if not isinstance(values, (list, tuple)) or not values:
            return "-"
        parts: list[str]
        if money:
            parts = [_money(value, "?") for value in values]
        else:
            parts = [
                "?" if value is None else _compact_grid_cells(value)
                for value in values
            ]
        text = " / ".join(parts)
        return f"{text}{suffix}" if suffix and text != "-" else text

    def _manual_live_round_no(self, snapshot: dict[str, Any]) -> int | None:
        round_no = _parse_int(snapshot.get("round"))
        if round_no is not None:
            return round_no
        uc = snapshot.get("ui_contract") if isinstance(snapshot.get("ui_contract"), dict) else {}
        context = uc.get("context") if isinstance(uc.get("context"), dict) else {}
        round_no = _parse_int(context.get("round"))
        if round_no is not None:
            return round_no
        live = getattr(self, "_last_live_summary", None)
        if isinstance(live, dict):
            live_context = live.get("context") if isinstance(live.get("context"), dict) else {}
            return _parse_int(live_context.get("round"))
        return None

    def _quote_display_values(self, data: dict[str, Any]) -> dict[str, str]:
        """Render-only projection: never mutate summary, receipt or model results."""
        self._quote_display_data = data
        self._quote_pending_application = None
        tip = getattr(self, "quote_tip", None)
        if tip is not None:
            tip.set_text("报价设置：当前无可调整的出价三档，未应用比例或上限")
        basis = data.get("quote_basis")
        if type(basis) is not dict or basis.get("schema") != "quote_basis_v1" or basis.get("status") != "available":
            return {}
        phase = (data.get("context") or {}).get("phase")
        if phase not in {"reading", "bidding", "manual"}:
            return {}
        try:
            policy = quote_prefs.validate_policy(getattr(self, "_quote_policy", quote_prefs.default_policy()))
            raw = quote_prefs.validate_raw(basis.get("raw"))
            adjusted = quote_prefs.apply_policy(raw, policy)
        except ValueError:
            return {}
        self._quote_pending_application = (raw, policy, adjusted)
        if tip is not None:
            crossed = adjusted["conservative"] > adjusted["balanced"] or adjusted["balanced"] > adjusted["aggressive"]
            tip.set_text("报价设置：" + ("默认比例" if quote_prefs.is_default(policy) else "自定义比例/上限")
                         + ("；调整后三档交叉，标签顺序保持" if crossed else ""))
        if quote_prefs.is_default(policy):
            return {}  # Existing formatting is part of default identity.
        return {key: _money(adjusted[key]) for key in quote_prefs.TIERS}

    def _record_applied_quote(self, data: dict[str, Any]) -> None:
        pending = getattr(self, "_quote_pending_application", None)
        if pending is None or AhmadTkOverlay._activation_product_denied(self):
            return
        raw, policy, adjusted = pending
        context = data.get("context") or {}
        basis = data.get("quote_basis") or {}
        if basis.get("source_mode") == "manual":
            self._quote_record_status = quote_prefs.unknown("manual")
            return  # Independent manual advice stays in local UI memory.
        session_hash = quote_prefs.hash_session(context.get("session_id"))
        live = getattr(self, "_last_live_summary", None)
        live_session = (live.get("context") or {}).get("session_id") if type(live) is dict else None
        if live_session and live_session != context.get("session_id"):
            self._quote_record_status = quote_prefs.unknown("different_session")
            return
        if not session_hash or context.get("phase") not in {"reading", "bidding"}:
            self._quote_record_status = quote_prefs.unknown("unavailable")
            return
        receipt = data.get("quote_receipt") or {}
        if receipt.get("source") == "settlement_clone":
            return
        now = time.time()
        rounds = {key: _parse_int(context.get(key)) for key in ("round", "action_round", "observed_round")}
        snapshot_at = data.get("updated_at")
        if any(value is None for value in rounds.values()) or type(snapshot_at) not in (int, float):
            self._quote_record_status = quote_prefs.unknown("unavailable")
            return
        identity = data.get("quote_identity") or {}
        signature = quote_prefs.fingerprint({"session_hash": session_hash, "raw": raw, "policy": policy,
                                             "source_mode": basis.get("source_mode")})
        if signature == getattr(self, "_quote_applied_signature", None):
            return
        revision = getattr(self, "_quote_application_revision", 0) + 1
        record = {
            "schema": quote_prefs.RECORD_SCHEMA, "status": "recorded", "coverage": quote_prefs.COVERAGE,
            "session_hash": session_hash, "writer_id": getattr(self, "_quote_writer_id", ""),
            "revision": revision, "source_mode": basis.get("source_mode"),
            "input_fingerprint": identity.get("input_fingerprint"),
            "result_identity": identity.get("result_identity"), "generation_hash": basis.get("generation_hash"),
            **rounds, "phase": context["phase"], "raw": raw, "policy": policy,
            "policy_fingerprint": quote_prefs.fingerprint(policy), "is_default": quote_prefs.is_default(policy),
            "adjusted": adjusted, "applied_at": now, "snapshot_at": snapshot_at,
        }
        self._quote_record_status = quote_prefs.write_applied(
            Path(self.snapshot_path).parent / "quote_last_applied.json", record,
        )
        if self._quote_record_status.get("status") == "recorded":
            self._quote_application_revision = revision
            self._quote_applied_signature = signature

    def _save_quote_draft(self, name: str, policy: dict, revision: int) -> None:
        """Publish effective state only after persistence succeeds; UI drafts are separate."""
        if getattr(self, "_ui_prefs_enabled", False):
            updated = quote_prefs.save_preferences(
                self._quote_preferences_path, expected_revision=revision, name=name, policy=policy,
            )
        else:
            updated = copy.deepcopy(self._quote_preferences)
            if updated["revision"] != revision:
                raise ValueError("报价设置已改变，请重新打开")
            updated["schemes"][name] = quote_prefs.validate_policy(policy)
            updated["active_name"] = name
            updated["revision"] += 1
            updated = quote_prefs.validate_preferences(updated)
        self._quote_preferences = updated
        self._quote_policy = updated["schemes"][updated["active_name"]]
        self._quote_preferences_error = None

    def _open_quote_preferences(self, _event=None) -> None:
        existing = getattr(self, "_quote_dialog", None)
        if existing is not None:
            try:
                existing.lift()
                return
            except tk.TclError:
                self._quote_dialog = None
        # Refresh the save revision without silently applying another window's settings.
        persisted, error = (quote_prefs.load_preferences(self._quote_preferences_path)
                            if self._ui_prefs_enabled else (self._quote_preferences, None))
        revision = persisted["revision"]
        draft_policy = persisted["schemes"][persisted["active_name"]]
        dlg = tk.Toplevel(self.root)
        dlg.title("报价设置")
        dlg.transient(self.root)
        dlg.attributes("-topmost", True)
        dlg.configure(bg=PANEL, padx=14, pady=12)
        dlg.resizable(False, False)
        self._quote_dialog = dlg
        tk.Label(dlg, text="报价设置", bg=PANEL, fg=TEXT, font=(FONT_UI, 11, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
        tk.Label(dlg, text="只调整出价参考，不改模型估值；跨回合、英雄沿用", bg=PANEL, fg=MUTED,
                 font=(FONT_UI, 8)).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 10))
        name = tk.StringVar(dlg, value=persisted["active_name"])
        tk.Label(dlg, text="方案名称", bg=PANEL, fg=TEXT).grid(row=2, column=0, sticky="w")
        selector = ttk.Combobox(dlg, textvariable=name, values=list(persisted["schemes"]), width=25)
        selector.grid(row=2, column=1, columnspan=2, sticky="ew", pady=4)
        entries, previews = {}, {}
        for index, (key, title) in enumerate(zip(quote_prefs.TIERS, ("保守", "参考", "激进")), 3):
            tk.Label(dlg, text=title + "比例 (%)", bg=PANEL, fg=TEXT).grid(row=index, column=0, sticky="w", pady=6)
            entries[key] = tk.StringVar(dlg, value=str(Decimal(draft_policy["ratios_bps"][key]) / 100))
            tk.Entry(dlg, textvariable=entries[key], width=10, bg=BG, fg=TEXT, insertbackground=TEXT).grid(row=index, column=1, padx=10)
            previews[key] = tk.Label(dlg, text="— → —", bg=PANEL, fg=MUTED, font=(FONT_UI, 9))
            previews[key].grid(row=index, column=2, sticky="w")
        cap = tk.StringVar(dlg, value="" if draft_policy["cap"] is None else str(draft_policy["cap"]))
        tk.Label(dlg, text="统一上限（游戏金额）", bg=PANEL, fg=TEXT).grid(row=6, column=0, sticky="w", pady=6)
        tk.Entry(dlg, textvariable=cap, width=14, bg=BG, fg=TEXT, insertbackground=TEXT).grid(row=6, column=1, padx=10)
        tk.Label(dlg, text="留空不设上限", bg=PANEL, fg=MUTED, font=(FONT_UI, 8)).grid(row=6, column=2, sticky="w")
        status = tk.Label(dlg, text=error or "", bg=PANEL, fg=MUTED, font=(FONT_UI, 8), justify="left", wraplength=460)
        status.grid(row=7, column=0, columnspan=3, sticky="w", pady=(8, 4))

        def draft():
            return quote_prefs.policy_from_text({key: var.get() for key, var in entries.items()}, cap.get())

        def preview(*_args):
            try:
                policy = draft()
                basis = (getattr(self, "_quote_display_data", {}).get("quote_basis") or {})
                raw = basis.get("raw") if basis.get("status") == "available" else None
                crossed = False
                for key in quote_prefs.TIERS:
                    previews[key].configure(text="— → —")
                if raw is not None:
                    values = quote_prefs.apply_policy(raw, policy)
                    crossed = values["conservative"] > values["balanced"] or values["balanced"] > values["aggressive"]
                    for key in quote_prefs.TIERS:
                        previews[key].configure(text=f"{_money(raw[key])} → {_money(values[key])}")
                state = "默认" if quote_prefs.is_default(policy) else "自定义"
                changed = policy != self._quote_policy or name.get() != self._quote_preferences["active_name"]
                message = f"{state} · {'未保存，预览不生效' if changed else '当前已生效'}"
                if raw is None:
                    message += "；当前没有可调整的出价三档"
                if crossed:
                    message += "；调整后三档交叉，标签顺序保持不变"
                status.configure(text=error or message, fg=WARM if changed or crossed else MUTED)
            except ValueError as exc:
                status.configure(text=str(exc), fg=BAD)

        def select(_event=None):
            selected = persisted["schemes"].get(name.get())
            if selected is not None:
                for key in quote_prefs.TIERS:
                    entries[key].set(str(Decimal(selected["ratios_bps"][key]) / 100))
                cap.set("" if selected["cap"] is None else str(selected["cap"]))
            preview()

        def close():
            self._quote_preview_refresh = None
            self._quote_dialog = None
            dlg.destroy()

        def save():
            try:
                self._save_quote_draft(name.get().strip(), draft(), revision)
            except (OSError, ValueError) as exc:
                status.configure(text=f"保存失败，原方案仍生效：{exc}", fg=BAD)
                return
            close()
            current = getattr(self, "_last_summary", None)
            if isinstance(current, dict) and not AhmadTkOverlay._activation_product_denied(self):
                if current.get("status") == "stale_snapshot":
                    self.render_standby(current)
                else:
                    self.render(current)  # Cached raw basis; never restart the engine.

        def reset():
            name.set("默认")
            select()

        controls = tk.Frame(dlg, bg=PANEL)
        controls.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        for title, callback, color in (("恢复默认", reset, MUTED), ("取消", close, MUTED), ("保存并应用", save, GOOD)):
            tk.Button(controls, text=title, command=callback, bg=BG, fg=color, relief="flat",
                      font=(FONT_UI, 9), padx=10, pady=5).pack(side="left", padx=(0, 6))
        selector.bind("<<ComboboxSelected>>", select)
        for var in (*entries.values(), cap, name):
            var.trace_add("write", preview)
        self._quote_preview_refresh = preview
        dlg.protocol("WM_DELETE_WINDOW", close)
        preview()

    def _add_shop_entry(self, dialog) -> None:
        """Standard activation dialog only; independent from support-can QR bindings."""
        button = self._rounded_button(dialog, text="打开店铺", bg=PANEL_SOFT, fg=ACCENT, padx=8, pady=4)
        button.pack(anchor="e", pady=(0, 6))
        button.bind("<Button-1>", lambda _event: webbrowser.open(SHOP_URL), add="+")
        popup = {"window": None}

        def hide(_event=None):
            window = popup["window"]
            popup["window"] = None
            if window is not None:
                try:
                    window.destroy()
                except tk.TclError:
                    pass

        def show(_event=None):
            hide()
            try:
                photo = tk.PhotoImage(master=dialog, file=str(SHOP_QR_IMAGE_PATH))
                if photo.width() != 225 or photo.height() != 225:
                    return
                window = tk.Toplevel(dialog)
                popup["window"] = window
                window.transient(dialog)
                window.overrideredirect(True)
                window.attributes("-topmost", True)
                window.configure(bg="white", padx=20, pady=20)
                label = tk.Label(window, image=photo, bg="white", borderwidth=0)
                label.image = photo
                label.pack()
                window.geometry(f"+{button.winfo_rootx()}+{button.winfo_rooty() + button.winfo_height() + 3}")
                window.lift(dialog)
            except (OSError, tk.TclError):
                hide()  # Missing QR never removes the clickable shop link.

        button.bind("<Enter>", show, add="+")
        button.bind("<Leave>", hide, add="+")
        dialog.bind("<Destroy>", lambda event: hide() if event.widget is dialog else None, add="+")

    def _manual_result_summary(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        hero_key = normalize_hero_key(snapshot.get("hero") or "")
        round_no = self._manual_live_round_no(snapshot)
        if run_reference_engine is None:
            raise RuntimeError("ref engine unavailable")
        result = run_reference_engine(
            prepare_reference_engine_snapshot(snapshot),
            max_combos=_manual_engine_max_combos(snapshot),
        ).as_dict()
        evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
        display_evidence = dict(evidence)
        display_evidence["total_grid_range"] = result.get("total_grid_range")
        candidate_summary = _candidate_summary(result)
        hero_key = normalize_hero_key(snapshot.get("hero") or "")
        next_info_hint = _next_info_hint(
            result,
            hero_key=hero_key,
            round_no=_parse_int(snapshot.get("round")),
        )
        notes = tuple(str(item) for item in (result.get("notes") or ()))
        fixed_counts = evidence.get("fixed_counts") if isinstance(evidence.get("fixed_counts"), dict) else {}
        avg_cells = evidence.get("avg_cells") if isinstance(evidence.get("avg_cells"), dict) else {}
        status = _text(result.get("status"), "unknown")
        display_ready = result.get("balanced") not in (None, "")
        if "manual_total_count_prior_enumeration" in notes:
            status = "manual_prior"
        elif "combo_cap_hit" in notes and "q6" not in fixed_counts and "q6" not in avg_cells:
            status = "manual_constraints_wide"
        ok = display_ready
        adaptation_pending = _ref_adaptation_pending_text(result) if not ok else ""
        flags = [{"label": "手动输入", "level": "neutral", "detail": "manual reference mode"}]
        if status == "manual_prior":
            flags.append({"label": "手动先验", "level": "watch", "detail": ";".join(notes)})
        elif status == "manual_constraints_wide":
            flags.append({"label": "约束较宽", "level": "watch", "detail": ";".join(notes)})
        elif not ok:
            flags.append({"label": adaptation_pending or "约束不足", "level": "risk", "detail": ";".join(notes)})
        layout_notes = [note for note in notes if "aisha_layout" in note]
        if hero_key == "aisha" and layout_notes:
            flags.append({"label": "布局余量", "level": "neutral", "detail": "; ".join(layout_notes[:4])})
        defense_hint = _aisha_defense_multiplier_hint(_parse_int(snapshot.get("round")))
        if hero_key == "aisha" and defense_hint:
            flags.append(
                {"label": defense_hint, "level": "neutral", "detail": "产品参考倍数，不进引擎"},
            )
        d1_detail = _aisha_d1_flag_detail(list(notes)) if hero_key == "aisha" else ""
        if d1_detail:
            flags.append(
                {"label": "红品权重参考", "level": "watch", "detail": d1_detail},
            )
        conf_flag = _confidence_flag(result)
        if conf_flag:
            flags.append(conf_flag)
        red_count_range, red_cells_range = _red_display_ranges(result)
        red_value_range = _red_display_value_range(result, red_count_range)
        return {
            "status": "ok",
            "quote_basis": quote_prefs.quote_basis(
                result, eligible=(ok and result.get("status") in {"ok", "count_prior", "provisional_estimated"}
                                  and (result.get("total_value_bound") or {}).get("mode") != "lower_bound_only"),
                source_mode="manual",
            ),
            "snapshot_path": "manual",
            "updated_at_text": time.strftime("%H:%M:%S"),
            "context": {
                "hero": snapshot.get("hero") or "?",
                "is_supported_ref_hero": True,
                "map_id": snapshot.get("map_id"),
                "round": "手动",
                "phase": "manual",
                "session_id": "manual",
                "file": None,
            },
            "reference": {
                "label": "Hero Ref",
                "source": "manual_ref",
                "readiness": status,
                "note": "manual inputs",
                "conservative": _money(result.get("conservative")) if ok else "-",
                "balanced": _money(result.get("balanced")) if ok else "-",
                "aggressive": _money(result.get("aggressive")) if ok else "-",
                "raw_value_range": self._range_text(
                    (result.get("value_p25"), result.get("value_p50"), result.get("value_p75")),
                    money=True,
                ),
                "v3_conservative": "-",
                "v3_balanced": "-",
                "v3_aggressive": "-",
                "ref_minus_v3_balanced": "-",
                "ref_minus_v3_balanced_raw": None,
                "action": "手动参考" if ok else adaptation_pending or "约束不足",
                "risk_band": "manual",
                "current_highest": "-",
                "decision_range": self._range_text(
                    (result.get("conservative"), result.get("balanced"), result.get("aggressive")),
                    money=True,
                ),
                "total_grid_range": self._range_text(result.get("total_grid_range"), suffix="格"),
                "total_value_range": self._range_text(
                    (result.get("value_p25"), result.get("value_p50"), result.get("value_p75")),
                    money=True,
                ),
            },
            "red": {
                "count_range": _red_range_text(red_count_range),
                "cells_range": _red_range_text(red_cells_range),
                "value_range": self._range_text(red_value_range, money=True),
                "quality_count_summary": self._quality_count_summary(result),
                "quality_probability_summary": _quality_probability_summary(result),
                "count_probability_hover": _quality_dimension_probability_hover(
                    result, "q6", "count"
                ),
                "cells_probability_hover": _quality_dimension_probability_hover(
                    result, "q6", "cells"
                ),
                "purple_gold_probability_hover": _quality_probability_hover(
                    result, ("q4", "q5")
                ),
                "quality_probability_hover": _quality_probability_hover(
                    result, ("q5", "q6")
                ),
                "low_quality_count_probability_hover": (
                    _low_quality_count_probability_hover(result)
                ),
                "uncertainty_summary": _quality_uncertainty_summary(result),
                "prior_rate": "-",
                "sample_rate": "-",
                "risk_reference": "",
            },
            "evidence": {
                "match_text": "manual",
                "information_density": "手动",
                "diagnostics": ";".join(result.get("notes") or ()),
                "latest_sent": {},
                "latest_result": {},
                "source_mode": "manual_ref",
                "ref_status": status,
                "ref_readiness": status,
                "ref_combo_count": _text(result.get("combo_count"), ""),
                "ref_input_summary": self._manual_input_summary(display_evidence),
                "candidate_summary": candidate_summary,
                "next_info_hint": next_info_hint,
                "ref_notes": ";".join(result.get("notes") or ()),
            },
            "truth": {"available": False, "q6": {}, "top_item": {}},
            "ahmed_ref": result,
            "minimap": {
                "status": "unavailable",
                "summary_text": "手动模式无小地图",
                "layout_source": "manual",
                "columns": 10,
                "viewport_rows": 13,
                "known_items": 0,
                "drawable_items": 0,
                "final_total_items": None,
                "quality_counts": {},
                "items": [],
            },
            "flags": flags,
        }

    def _structured_input_candidates(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        uc = snapshot.get("ui_contract") if isinstance(snapshot.get("ui_contract"), dict) else {}
        constraints = uc.get("constraints") if isinstance(uc.get("constraints"), dict) else {}
        candidates: list[dict[str, Any]] = []
        for value in (
            snapshot.get("structured_ref_inputs"),
            uc.get("structured_ref_inputs"),
            constraints.get("structured_ref_inputs"),
            snapshot.get("hero_ref_inputs"),
            uc.get("hero_ref_inputs"),
            constraints.get("hero_ref_inputs"),
            snapshot.get("aisha_ref_inputs"),
            uc.get("aisha_ref_inputs"),
            constraints.get("aisha_ref_inputs"),
            snapshot.get("ahmad_ref_inputs"),
            uc.get("ahmad_ref_inputs"),
            constraints.get("ahmad_ref_inputs"),
            snapshot.get("victor_ref_inputs"),
            uc.get("victor_ref_inputs"),
            constraints.get("victor_ref_inputs"),
        ):
            if isinstance(value, dict):
                candidates.append(value)
        return candidates

    def _merge_ref_inputs(
        self,
        base: dict[str, Any],
        overlay: dict[str, Any],
    ) -> dict[str, Any]:
        merged = copy.deepcopy(base)
        for key, value in overlay.items():
            if value in (None, ""):
                continue
            if isinstance(value, dict):
                if not value:
                    continue
                current = merged.get(key)
                if isinstance(current, dict):
                    nested = copy.deepcopy(current)
                    nested.update(value)
                    merged[key] = nested
                else:
                    merged[key] = copy.deepcopy(value)
            elif isinstance(value, list):
                if value:
                    merged[key] = copy.deepcopy(value)
            else:
                merged[key] = value
        return merged

    def _merged_live_ref_inputs(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for candidate in self._structured_input_candidates(snapshot):
            merged = self._merge_ref_inputs(merged, candidate)
        return merged

    def _manual_ref_inputs(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        value = snapshot.get("structured_ref_inputs")
        return copy.deepcopy(value) if isinstance(value, dict) else {}

    def _snapshot_with_manual_overlay(
        self,
        live_snapshot: dict[str, Any],
        manual_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        overlaid = copy.deepcopy(live_snapshot)
        manual_inputs = self._manual_ref_inputs(manual_snapshot)
        merged_inputs = self._merge_ref_inputs(
            self._merged_live_ref_inputs(live_snapshot),
            manual_inputs,
        )
        overlaid["structured_ref_inputs"] = merged_inputs
        uc = overlaid.setdefault("ui_contract", {})
        if not isinstance(uc, dict):
            uc = {}
            overlaid["ui_contract"] = uc
        constraints = uc.setdefault("constraints", {})
        if not isinstance(constraints, dict):
            constraints = {}
            uc["constraints"] = constraints
        source = uc.setdefault("source", {})
        if not isinstance(source, dict):
            source = {}
            uc["source"] = source
        uc["structured_ref_inputs"] = merged_inputs
        constraints["structured_ref_inputs"] = merged_inputs
        source["manual_overlay"] = True
        source["manual_overlay_updated_at"] = time.time()
        return overlaid

    def _manual_overlay_summary(
        self,
        live_snapshot: dict[str, Any],
        manual_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        summary = summarize_snapshot(
            self._snapshot_with_manual_overlay(live_snapshot, manual_snapshot),
            snapshot_path=self.snapshot_path,
        )
        flags = summary.setdefault("flags", [])
        if isinstance(flags, list):
            flags.append(
                {
                    "label": "手动叠加",
                    "level": "neutral",
                    "detail": "manual structured inputs are merged into the latest live snapshot",
                }
            )
        evidence = summary.get("evidence")
        if isinstance(evidence, dict):
            evidence["manual_overlay"] = True
        if isinstance(summary.get("quote_basis"), dict):
            summary["quote_basis"]["source_mode"] = "manual_overlay"
        reference = summary.get("reference")
        if isinstance(reference, dict):
            note = _text(reference.get("note"), "")
            if "manual overlay" not in note:
                reference["note"] = f"{note} manual overlay.".strip()
        return summary

    def apply_manual_inputs(self) -> None:
        # 激活强制(单一 UI 入口闸): 降级态(未激活/试用到期)不校验、不计算、不渲染。手填是唯一同步
        # 计算路径, 若不在此拦, _start_manual_summary_worker 降级返回 False 会落到下面 inline
        # _manual_result_summary 跑引擎并 render, 绕过 refresh 的降级 gate = 到期后门。worker 路径
        # (_start_manual_summary_worker)与 live-apply(_apply_live_summary)已各自加同一闸, 这里补齐。
        if AhmadTkOverlay._activation_product_denied(self):
            if hasattr(self, "manual_status"):
                self.manual_status.configure(
                    text=AhmadTkOverlay._activation_denied_text(self),
                    fg=BAD,
                )
            return
        try:
            snapshot, error = self._manual_inputs_snapshot()
        except Exception:  # noqa: BLE001 - malformed UI input must not escape Tk callback
            self.manual_status.configure(
                text="输入检查失败，请检查数字格式",
                fg=BAD,
            )
            return
        if snapshot is None:
            self.manual_status.configure(text=error, fg=BAD)
            return
        try:
            live_summary = self._last_live_summary if isinstance(self._last_live_summary, dict) else {}
            settlement_manual = bool(
                getattr(self, "_manual_settlement_edit_unlocked", False)
                and self._is_settlement_summary(live_summary)
            )
            live_session_id = self._summary_session_id(live_summary)
            revision = int(getattr(self, "_manual_input_revision", 0))
            if self._start_manual_summary_worker(
                snapshot=snapshot,
                settlement_manual=settlement_manual,
                live_session_id=live_session_id,
                revision=revision,
            ):
                return
            summary = self._manual_result_summary(snapshot)
        except Exception as exc:  # noqa: BLE001 - show calculation failure in UI
            self.manual_status.configure(text=f"计算失败: {exc}", fg=BAD)
            return
        self._manual_active = True
        self._set_manual_edit_enabled(True)
        self._manual_snapshot = snapshot
        self._manual_summary = summary
        self._last_summary = summary
        self._manual_live_session_id = live_session_id
        self.manual_status.configure(
            text="手动计算，结算页已脱离" if settlement_manual else "手动计算，实时后台",
            fg=WARM,
        )
        self._set_manual_button_state()
        if summary.get("status") == "stale_snapshot":
            self.render_standby(summary)
        else:
            self.render(summary)

    def _quality_count_summary(self, result: dict[str, Any]) -> str:
        return _purple_gold_quality_summary(result)

    def _settlement_cleared_manual_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        context = data.get("context") if isinstance(data.get("context"), dict) else {}
        values = self._manual_values_from_summary(data)
        hero = _normalize_manual_hero(values.get("hero") or context.get("hero")) or "?"
        map_id = _to_optional_int(values.get("map_id") or context.get("map_id"))
        map_name = _manual_map_name(map_id)
        session_id = self._summary_session_id(data) or "manual"
        input_summary_parts = []
        if hero != "?":
            input_summary_parts.append(f"英雄 {hero}")
        if map_id is not None:
            input_summary_parts.append(f"地图 {_manual_map_display_value(map_id)}")
        input_summary = " · ".join(input_summary_parts) if input_summary_parts else "结算已清，待填写"
        return {
            "status": "ok",
            "snapshot_path": "manual",
            "updated_at_text": time.strftime("%H:%M:%S"),
            "context": {
                "hero": hero,
                "is_supported_ref_hero": is_supported_ref_hero(hero),
                "map_id": map_id,
                "map_name": map_name or None,
                "round": "手动",
                "phase": "manual",
                "session_id": session_id,
                "file": None,
            },
            "reference": {
                "label": "Hero Ref",
                "source": "manual_ref",
                "readiness": "settlement_cleared",
                "note": "settlement cleared; waiting for manual inputs",
                "conservative": "-",
                "balanced": "-",
                "aggressive": "-",
                "raw_value_range": "-",
                "v3_conservative": "-",
                "v3_balanced": "-",
                "v3_aggressive": "-",
                "ref_minus_v3_balanced": "-",
                "ref_minus_v3_balanced_raw": None,
                "action": "待手填",
                "risk_band": "manual",
                "current_highest": "-",
                "decision_range": "-",
                "total_grid_range": "-",
                "total_value_range": "-",
            },
            "red": {
                "count_range": "-",
                "cells_range": "-",
                "value_range": "-",
                "quality_count_summary": "-",
                "quality_probability_summary": "-",
                "count_probability_hover": "",
                "cells_probability_hover": "",
                "purple_gold_probability_hover": "",
                "quality_probability_hover": "",
                "low_quality_count_probability_hover": "",
                "uncertainty_summary": "结算已清，待输入",
                "prior_rate": "-",
                "sample_rate": "-",
                "risk_reference": "",
            },
            "evidence": {
                "match_text": "manual",
                "information_density": "结算已清",
                "diagnostics": "settlement_cleared",
                "latest_sent": {},
                "latest_result": {},
                "source_mode": "manual_ref",
                "ref_status": "settlement_cleared",
                "ref_readiness": "settlement_cleared",
                "ref_combo_count": "",
                "ref_input_summary": input_summary,
                "candidate_summary": "-",
                "next_info_hint": "补总件/总格或品质信息",
                "ref_notes": "settlement_cleared",
            },
            "truth": {"available": False, "q6": {}, "top_item": {}},
            "ahmed_ref": {
                "status": "settlement_cleared",
                "evidence": {},
                "notes": ("settlement_cleared",),
            },
            "minimap": {
                "status": "unavailable",
                "summary_text": "手动模式无小地图",
                "layout_source": "manual",
                "columns": 10,
                "viewport_rows": 13,
                "known_items": 0,
                "drawable_items": 0,
                "final_total_items": None,
                "quality_counts": {},
                "items": [],
            },
            "flags": [
                {
                    "label": "结算已清",
                    "level": "watch",
                    "detail": "settlement truth detached from manual edit view",
                }
            ],
        }

    def clear_settlement_manual_values(self) -> str:
        data = self._last_summary or self._last_live_summary or {}
        if not self._is_settlement_summary(data):
            self._set_manual_settlement_button_state()
            return "break"
        self._manual_settlement_edit_unlocked = True
        self._set_manual_edit_enabled(True)
        values = self._manual_values_from_summary(data)
        keep = {
            "hero": values.get("hero") or "",
            "map_id": (
                _manual_map_display_value(values.get("map_id"))
                if values.get("map_id") not in (None, "")
                else ""
            ),
        }
        for key in tuple(getattr(self, "manual_entries", {})):
            self._set_manual_entry(key, keep.get(key, ""), track_auto=key in keep)
        summary = self._settlement_cleared_manual_summary(data)
        self._manual_active = False
        self._manual_snapshot = {}
        self._manual_summary = summary
        self._last_summary = summary
        self._manual_dirty_fields.clear()
        self._manual_autofill_values = {key: value for key, value in keep.items() if value}
        self._manual_live_session_id = self._summary_session_id(data)
        if hasattr(self, "manual_status"):
            self.manual_status.configure(text="已清结算，可手动填写", fg=WARM)
        if hasattr(self, "manual_card"):
            self.manual_card.configure(highlightbackground=WARM, highlightthickness=2)
        self.render(summary)
        self._set_manual_settlement_button_state()
        return "break"

    def clear_manual_inputs(self) -> str:
        if not hasattr(self, "manual_entries"):
            return "break"
        self._manual_active = False
        self._manual_snapshot = {}
        self._manual_summary = {}
        self._manual_dirty_fields.clear()
        self._manual_autofill_values.clear()
        self._manual_live_session_id = ""
        for key in tuple(getattr(self, "manual_entries", {})):
            self._set_manual_entry(key, "", track_auto=False)
        if hasattr(self, "manual_status"):
            self.manual_status.configure(text="已清空，待填写", fg=DIM)
        self._set_manual_button_state()
        self._set_manual_settlement_button_state()
        return "break"

    def prefill_manual_inputs(self) -> None:
        data = self._last_live_summary or self._last_summary or {}
        values = self._manual_values_from_summary(data)
        self._set_manual_entry("hero", values.get("hero") or "", track_auto=True)
        self._set_manual_entry(
            "map_id",
            (
                _manual_map_display_value(values.get("map_id"))
                if values.get("map_id") is not None
                else ""
            ),
            track_auto=True,
        )
        self._set_manual_entry("total_count", values.get("total_count") if values.get("total_count") is not None else "", track_auto=True)
        self._set_manual_entry("total_cells", values.get("total_cells") if values.get("total_cells") is not None else "", track_auto=True)
        self._set_manual_entry("total_avg", values.get("total_avg") if values.get("total_avg") is not None else "", track_auto=True)
        for key in (*SPLIT_QUALITY_INPUT_KEYS, *QUALITY_INPUT_KEYS):
            self._set_manual_entry(
                f"{key}_avg",
                values.get(f"{key}_avg") if values.get(f"{key}_avg") is not None else "",
                track_auto=True,
            )
            self._set_manual_entry(
                f"{key}_count",
                values.get(f"{key}_count") if values.get(f"{key}_count") is not None else "",
                track_auto=True,
            )
            self._set_manual_entry(
                f"{key}_cells",
                values.get(f"{key}_cells") if values.get(f"{key}_cells") is not None else "",
                track_auto=True,
            )
            self._set_manual_entry(
                f"{key}_avg_value",
                values.get(f"{key}_avg_value") if values.get(f"{key}_avg_value") is not None else "",
                track_auto=True,
            )
            self._set_manual_entry(
                f"{key}_value_sum",
                values.get(f"{key}_value_sum") if values.get(f"{key}_value_sum") is not None else "",
                track_auto=True,
            )
        self._set_manual_entry(
            "q4q5_count",
            values.get("q4q5_count") if values.get("q4q5_count") is not None else "",
            track_auto=True,
        )
        self._manual_live_session_id = self._summary_session_id(data)
        self.manual_status.configure(text="已填入当前，待应用", fg=ACCENT)

    def _clear_values(self) -> None:
        self._clear_game_launch_candidate_binding()
        self._set_price_titles({})
        for widget in self.price_labels.values():
            widget.configure(text="-")
        for row_group in (
            self.red_rows,
            self.action_rows,
            self.evidence_rows,
            self.detail_rows,
        ):
            for key, widget in row_group.items():
                if key == "_card":
                    continue
                self._set_label(widget, "-")
        triplet_frame = getattr(self, "red_count_triplet_frame", None)
        if triplet_frame is not None and triplet_frame.winfo_manager():
            triplet_frame.pack_forget()
        red_count_value = getattr(self, "red_rows", {}).get("红件")
        if red_count_value is not None and not red_count_value.winfo_manager():
            red_count_value.pack(side="right", fill="x", expand=True)
        for segment in getattr(self, "red_count_triplet_labels", {}).values():
            segment.configure(text="")
            tip = getattr(segment, "_hover_tip", None)
            if tip is not None:
                tip.set_text("")
        for key in tuple(getattr(self, "quality_state_badges", {})):
            self._set_quality_state_badge(key, "")

    def _set_price_titles(self, titles: dict[str, Any]) -> None:
        for key, default in self.default_price_titles.items():
            widget = self.price_title_labels.get(key)
            if widget is not None:
                widget.configure(text=_text(titles.get(key), default))

    def _minimap_count_text(self, minimap: dict[str, Any]) -> str:
        quality_counts = minimap.get("quality_counts")
        if not isinstance(quality_counts, dict) or not quality_counts:
            return "-"
        return "  ".join(
            f"{key.upper()}:{quality_counts[key]}"
            for key in ("q6", "q5", "q4", "q3")
            if key in quality_counts
        ) or "-"

    def _detail_minimap_evidence_text(self, minimap: dict[str, Any]) -> str:
        """Keep the detail-only minimap metadata after removing its duplicate canvas."""
        if not isinstance(minimap, dict) or not minimap:
            return "-"
        known = minimap.get("known_items")
        if known in (None, ""):
            known = minimap.get("drawable_items")
        if known in (None, ""):
            known = "-"
        total = minimap.get("final_total_items")
        item_text = (
            f"{known}/{total}件"
            if total not in (None, "", known)
            else f"{_text(known)}件"
        )
        source = _text(minimap.get("layout_source"), "").strip()
        quality = self._minimap_count_text(minimap)
        columns = _to_int(minimap.get("columns"), MINIMAP_DEFAULT_COLUMNS)
        viewport_rows = _to_int(minimap.get("viewport_rows"), MINIMAP_DEFAULT_ROWS)
        parts = [item_text]
        if source and source != "-":
            parts.append(source)
        if quality != "-":
            parts.append(quality)
        parts.append(f"{columns}x{viewport_rows} · {columns * viewport_rows}格")
        return " · ".join(parts)

    def _set_minimap_meta(self, minimap: dict[str, Any]) -> None:
        rows = getattr(self, "minimap_meta_rows", {})
        if not rows:
            return
        known = minimap.get("known_items") or minimap.get("drawable_items") or "-"
        total = minimap.get("final_total_items")
        item_text = f"{known}/{total}" if total not in (None, "", known) else _text(known)
        columns = _to_int(minimap.get("columns"), MINIMAP_DEFAULT_COLUMNS)
        viewport_rows = _to_int(minimap.get("viewport_rows"), MINIMAP_DEFAULT_ROWS)
        values = {
            "件数": item_text,
            "来源": minimap.get("layout_source") or "-",
            "品质": self._minimap_count_text(minimap),
            "范围": f"{columns}列 x {viewport_rows}行 · {columns * viewport_rows}格",
        }
        for key, value in values.items():
            if key in rows:
                rows[key].configure(text=_short(value, 32))

    def _render_minimap_header(
        self,
        *,
        title: tk.Label,
        counts: tk.Label,
        minimap: dict[str, Any],
    ) -> None:
        status = str(minimap.get("status") or "")
        summary = _text(minimap.get("summary_text"), "等待公开轮廓/小地图")
        title.configure(text=f"小地图 · {_short(summary, 22)}")
        counts.configure(text=self._minimap_count_text(minimap))
        available = status == "available" and isinstance(minimap.get("items"), list) and bool(minimap.get("items"))
        self.map_button.configure(text="地图", fg=GOOD if available else DIM)

    def _render_minimap(self, minimap: dict[str, Any]) -> None:
        self._minimap_data = minimap
        self._pending_minimap_data = minimap
        self._render_minimap_header(
            title=self.minimap_title,
            counts=self.minimap_counts,
            minimap=minimap,
        )
        self._schedule_minimap_canvas_render()

    def _round_rect(
        self,
        canvas: tk.Canvas,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        radius: float,
        **kwargs: Any,
    ) -> int:
        radius = max(1.0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
        points = (
            x1 + radius, y1,
            x2 - radius, y1,
            x2, y1,
            x2, y1 + radius,
            x2, y2 - radius,
            x2, y2,
            x2 - radius, y2,
            x1 + radius, y2,
            x1, y2,
            x1, y2 - radius,
            x1, y1 + radius,
            x1, y1,
        )
        return int(canvas.create_polygon(points, smooth=True, splinesteps=8, **kwargs))

    def _minimap_quality_style(self, quality: str) -> dict[str, Any]:
        key = quality if quality in MAINLINE_QUALITY_STYLE else "unknown"
        style = dict(MAINLINE_QUALITY_STYLE[key])
        if key == "unknown":
            style["unknown"] = True
        return style

    def _draw_unknown_quality_fill(
        self,
        canvas: tk.Canvas,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        *,
        color: str,
        tag: str,
        base_fill: str | None = None,
    ) -> None:
        width = max(1, x1 - x0)
        height = max(1, y1 - y0)
        if base_fill:
            canvas.create_rectangle(
                x0,
                y0,
                x1,
                y1,
                fill=base_fill,
                outline="",
                width=0,
                tags=(tag,),
            )
        step = max(3, min(width, height) // 4)
        line_width = 2 if min(width, height) >= 14 else 1
        for start_x in range(x0 - height, x1 + height, step):
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
                    width=line_width,
                    tags=(tag,),
                )

    def _draw_minimap(
        self,
        canvas: tk.Canvas,
        minimap: dict[str, Any],
        tip: HoverTip | None,
        *,
        min_width: int = 300,
        min_height: int = 104,
        allow_scroll: bool = False,
        cell_hint: float = 20.0,
    ) -> None:
        visible_width, visible_height = canvas_draw_size(
            canvas,
            min_width=min_width,
            min_height=min_height,
        )
        # Skip the clear+redraw when nothing that affects this canvas changed.
        # Live snapshots refresh ~1/s but the minimap content is usually identical;
        # redrawing unconditionally made the canvas flicker every cycle.
        cache = getattr(self, "_minimap_canvas_signatures", None)
        if cache is not None:
            signature = (
                int(visible_width),
                int(visible_height),
                round(float(cell_hint), 2),
                bool(allow_scroll),
                json.dumps(minimap, sort_keys=True, default=str),
            )
            if cache.get(id(canvas)) == signature:
                return
            cache[id(canvas)] = signature
        canvas.delete("all")
        width = visible_width
        height = visible_height
        status = str(minimap.get("status") or "")
        if status != "available" or not isinstance(minimap.get("items"), list) or not minimap.get("items"):
            canvas.configure(scrollregion=(0, 0, width, height))
            columns = MINIMAP_DEFAULT_COLUMNS
            rows = MINIMAP_DEFAULT_ROWS
            pad_x = 10
            pad_y = 8
            cell = max(4, int(min((width - pad_x * 2) / columns, (height - pad_y * 2) / rows)))
            grid_w = cell * columns
            grid_h = cell * rows
            x0 = int(max(pad_x, round((width - grid_w) / 2)))
            y0 = int(max(pad_y, round((height - grid_h) / 2)))
            canvas.create_rectangle(x0, y0, x0 + grid_w, y0 + grid_h, outline=BORDER, fill=MINIMAP_BG)
            grid_color = _blend_hex(MINIMAP_GRID, MINIMAP_BG, 0.18)
            for col in range(columns + 1):
                x = x0 + col * cell + 0.5
                canvas.create_line(x, y0, x, y0 + grid_h, fill=grid_color)
            for row in range(rows + 1):
                y = y0 + row * cell + 0.5
                canvas.create_line(x0, y, x0 + grid_w, y, fill=grid_color)
            canvas.create_text(
                width / 2,
                height / 2,
                text="等待公开轮廓/小地图",
                fill=MINIMAP_TEXT,
                font=(FONT_UI, 9),
            )
            return

        items = [item for item in minimap.get("items", []) if isinstance(item, dict)]
        columns = _to_int(minimap.get("columns"), MINIMAP_DEFAULT_COLUMNS)
        viewport_rows = _to_int(minimap.get("viewport_rows"), MINIMAP_DEFAULT_ROWS)
        max_row = 0
        max_col = 0
        for item in items:
            row = _to_int(item.get("row"), 1)
            col = _to_int(item.get("col"), 1)
            item_w = max(1, _to_int(item.get("width"), 1))
            item_h = max(1, _to_int(item.get("height"), 1))
            max_row = max(max_row, row + item_h - 1)
            max_col = max(max_col, col + item_w - 1)
        columns = max(MINIMAP_DEFAULT_COLUMNS, min(20, max(columns, max_col)))
        rows = max(MINIMAP_DEFAULT_ROWS, min(25, max(max_row, viewport_rows)))
        pad_x = 10
        pad_y = 8
        if allow_scroll:
            width = visible_width
            height = max(visible_height, int(rows * cell_hint + pad_y * 2))
        cell = max(4, int(min((width - pad_x * 2) / columns, (height - pad_y * 2) / rows)))
        grid_w = cell * columns
        grid_h = cell * rows
        # Anchor to the TOP-LEFT, matching the game warehouse origin (row 1 / col 1 at the
        # top-left). Centering pushed the layout down/right so a shallow warehouse looked like
        # it had drifted ~30-40% lower than it should and sat off to the right.
        x0 = pad_x
        y0 = pad_y
        canvas.configure(scrollregion=(0, 0, width, height))

        grid_color = _blend_hex(MINIMAP_GRID, MINIMAP_BG, 0.18)
        canvas.create_rectangle(x0, y0, x0 + grid_w, y0 + grid_h, outline=BORDER, fill=MINIMAP_BG)
        for col in range(columns + 1):
            x = x0 + col * cell + 0.5
            canvas.create_line(x, y0, x, y0 + grid_h, fill=grid_color)
        for row in range(rows + 1):
            y = y0 + row * cell + 0.5
            canvas.create_line(x0, y, x0 + grid_w, y, fill=grid_color)

        for idx, item in enumerate(items):
            row = _to_int(item.get("row"), 1)
            col = _to_int(item.get("col"), 1)
            item_w = max(1, _to_int(item.get("width"), 1))
            item_h = max(1, _to_int(item.get("height"), 1))
            if row < 1 or col < 1 or row > rows or col > columns:
                continue
            quality = _minimap_quality_key(item.get("quality"))
            item_value = _to_int(item.get("value"), 0)
            style = self._minimap_quality_style(quality)
            fill = str(style["fill"])
            if style.get("unknown"):
                outline = str(style["outline"])
            else:
                outline = _blend_hex(fill, MINIMAP_BG, 0.36)
            gap = 1
            x1 = x0 + (col - 1) * cell + gap
            y1 = y0 + (row - 1) * cell + gap
            x2 = min(x0 + grid_w - gap, x0 + (col - 1 + item_w) * cell - gap)
            y2 = min(y0 + grid_h - gap, y0 + (row - 1 + item_h) * cell - gap)
            tag = f"item_{idx}"
            source_text = str(item.get("source") or "").lower()
            render_mode = str(item.get("render_mode") or "").lower()
            item_layout_source = str(item.get("layout_source") or "").lower()
            shape_key = str(item.get("shape_key") or "").strip()
            item_id = item.get("item_id")
            has_item_identity = item_id not in (None, "", 0, "0")
            cells_value = _to_int(item.get("cells"), 0)
            explicit_marker = render_mode == "marker"
            has_hard_footprint = (
                render_mode == "footprint"
                or (
                    not explicit_marker
                    and (
                        bool(shape_key)
                        or has_item_identity
                        or source_text in {"packet", "settlement_inventory", "settlement"}
                    )
                )
            )
            marker_only = explicit_marker or (
                not has_hard_footprint
                and (
                    source_text in {
                        "quality_only",
                        "quality_reveal",
                        "public_quality",
                        "quality_marker",
                    }
                    or item_layout_source in {
                        "quality_only",
                        "quality_reveal",
                        "public_quality",
                    }
                    or cells_value <= 0
                )
            )
            if marker_only:
                marker_size = max(6, min(11, int(round(cell * 0.55))))
                if item_value > 0:
                    marker_size = max(marker_size, 8)
                dot_x = (x1 + x2) / 2
                dot_y = (y1 + y2) / 2
                marker_x1 = int(round(dot_x - marker_size / 2))
                marker_y1 = int(round(dot_y - marker_size / 2))
                marker_x2 = marker_x1 + marker_size
                marker_y2 = marker_y1 + marker_size
                marker_fill = (
                    _blend_hex(MINIMAP_BG, outline, 0.42)
                    if style.get("unknown")
                    else fill
                )
                canvas.create_oval(
                    marker_x1,
                    marker_y1,
                    marker_x2,
                    marker_y2,
                    fill=marker_fill,
                    outline=outline,
                    width=1,
                    tags=(tag,),
                )
            else:
                unknown_item = bool(style.get("unknown"))
                if unknown_item:
                    base_fill = _blend_hex(MINIMAP_BG, outline, 0.42)
                    canvas.create_rectangle(
                        x1,
                        y1,
                        x2,
                        y2,
                        fill=base_fill,
                        outline=outline,
                        width=1,
                        tags=(tag,),
                    )
                    self._draw_unknown_quality_fill(
                        canvas,
                        x1,
                        y1,
                        x2,
                        y2,
                        color=outline,
                        tag=tag,
                    )
                else:
                    canvas.create_rectangle(
                        x1,
                        y1,
                        x2,
                        y2,
                        fill=fill,
                        outline=outline,
                        width=1,
                        tags=(tag,),
                    )
            tooltip = _text(item.get("tooltip") or item.get("label") or quality, "")
            if tip is not None:
                canvas.tag_bind(tag, "<Enter>", lambda e, text=tooltip, hover=tip: hover.show_at(e, text))
                canvas.tag_bind(tag, "<Motion>", lambda e, text=tooltip, hover=tip: hover.show_at(e, text))
                canvas.tag_bind(tag, "<Leave>", lambda _e, hover=tip: hover.hide())

    def _cancel_hide_minimap_popup(self, _event: tk.Event[Any] | None = None) -> None:
        if self._hide_minimap_after_id is None:
            return
        try:
            self.root.after_cancel(self._hide_minimap_after_id)
        except tk.TclError:
            pass
        self._hide_minimap_after_id = None

    def _set_header_subtitle(self, text: Any) -> None:
        """Keep the compact secondary header line single-line and measurable at DPI."""
        self._set_autofit_label(
            self.subtitle,
            text,
            base_font_pt=7,
            min_font_pt=SUMMARY_COMPACT_FONT_SIZE,
            hover=text,
            stabilize_font=True,
        )

    def _schedule_hide_minimap_popup(self, _event: tk.Event[Any] | None = None) -> None:
        self._cancel_hide_minimap_popup()
        self._hide_minimap_after_id = self.root.after(160, self._hide_minimap_popup)

    def _hide_minimap_popup(self) -> None:
        self._hide_minimap_after_id = None
        if self._popup_canvas is not None:
            self._minimap_canvas_signatures.pop(id(self._popup_canvas), None)
        if self._minimap_popup is not None:
            try:
                self._minimap_popup.destroy()
            except tk.TclError:
                pass
        self._minimap_popup = None
        self._popup_canvas = None
        self._popup_title = None
        self._popup_counts = None
        self._popup_canvas_tip = None

    def _show_minimap_popup(self, event: tk.Event[Any] | None = None) -> None:
        if self._pinned_minimap_popup is not None:
            return
        self._cancel_hide_minimap_popup()
        if self._minimap_popup is None:
            popup = tk.Toplevel(self.root)
            popup.withdraw()
            popup.overrideredirect(True)
            popup.attributes("-topmost", bool(getattr(self, "topmost_enabled", True)))
            _apply_windows_toolwindow(popup, enabled=not self.show_taskbar)
            popup.configure(bg=BG)
            shell = self._card(popup, bg=PANEL, padx=7, pady=7)
            shell.pack(fill="both", expand=True)
            header = tk.Frame(shell, bg=PANEL)
            header.pack(fill="x", pady=(0, 5))
            self._popup_title = tk.Label(
                header,
                text="小地图",
                bg=PANEL,
                fg=MUTED,
                font=(FONT_UI, 8, "bold"),
                anchor="w",
            )
            self._popup_title.pack(side="left", fill="x", expand=True)
            self._popup_counts = tk.Label(
                header,
                text="-",
                bg=PANEL,
                fg=MUTED,
                font=(FONT_UI, 7),
                anchor="e",
            )
            self._popup_counts.pack(side="right")
            self._popup_canvas = tk.Canvas(
                shell,
                width=206,
                height=248,
                bg=MINIMAP_BG,
                highlightthickness=1,
                highlightbackground=BORDER,
            )
            self._popup_canvas.pack(fill="both", expand=True)
            self._popup_canvas_tip = HoverTip(self._popup_canvas)
            popup.bind("<Enter>", self._cancel_hide_minimap_popup, add="+")
            popup.bind("<Leave>", self._schedule_hide_minimap_popup, add="+")
            self._popup_canvas.bind("<Enter>", self._cancel_hide_minimap_popup, add="+")
            self._popup_canvas.bind("<Leave>", self._schedule_hide_minimap_popup, add="+")
            self._minimap_popup = popup
        popup_w = 222
        popup_h = 292
        popup_x, popup_y = self._minimap_popup_position_near_root(
            popup_w=popup_w,
            popup_h=popup_h,
            right_gap=16,
        )
        self._minimap_popup.geometry(f"{popup_w}x{popup_h}+{popup_x}+{popup_y}")
        self._redraw_minimap_popup()
        self._minimap_popup.deiconify()

    def _redraw_minimap_popup(self) -> None:
        if (
            self._minimap_popup is None
            or self._popup_canvas is None
            or self._popup_title is None
            or self._popup_counts is None
        ):
            return
        self._render_minimap_header(
            title=self._popup_title,
            counts=self._popup_counts,
            minimap=self._minimap_data,
        )
        self._draw_minimap(
            self._popup_canvas,
            self._minimap_data,
            self._popup_canvas_tip,
            min_width=206,
            min_height=248,
        )

    def _hide_pinned_minimap(self) -> None:
        if self._pinned_configure_after_id is not None:
            try:
                self.root.after_cancel(self._pinned_configure_after_id)
            except tk.TclError:
                pass
            self._pinned_configure_after_id = None
        if self._pinned_canvas is not None:
            self._minimap_canvas_signatures.pop(id(self._pinned_canvas), None)
        if self._pinned_minimap_popup is not None:
            try:
                self._pinned_minimap_popup.destroy()
            except tk.TclError:
                pass
        self._pinned_minimap_popup = None
        self._pinned_canvas = None
        self._pinned_title = None
        self._pinned_counts = None
        self._pinned_canvas_tip = None
        self._pinned_offset = None
        self._pinned_minimap_follow = True
        self._pinned_minimap_drag_offset = None
        if hasattr(self, "map_button"):
            self.map_button.configure(bg=PANEL_SOFT, fg=GOOD)

    def _popup_geometry_bounds(
        self,
        *,
        popup_w: int,
        popup_h: int,
        root_x: int,
        root_y: int,
        anchor_x: int | None = None,
        anchor_y: int | None = None,
    ) -> tuple[int, int]:
        if anchor_x is None:
            anchor_x = root_x + popup_w // 2
        if anchor_y is None:
            anchor_y = root_y + popup_h // 2
        left, top, right, bottom = _monitor_work_area_for_point(
            self.root,
            anchor_x,
            anchor_y,
        )
        return _clamp_rect_into_workarea(
            root_x,
            root_y,
            popup_w,
            popup_h,
            left,
            top,
            right,
            bottom,
        )

    def _minimap_popup_position_near_root(
        self,
        *,
        popup_w: int,
        popup_h: int,
        right_gap: int,
    ) -> tuple[int, int]:
        root_x = int(self.root.winfo_x())
        root_y = int(self.root.winfo_y())
        root_w = max(1, int(self.root.winfo_width()))
        root_h = max(1, int(self.root.winfo_height()))
        left, top, right, bottom = _monitor_work_area_for_point(
            self.root,
            root_x + root_w // 2,
            root_y + root_h // 2,
        )
        popup_x = root_x + root_w + int(right_gap)
        popup_y = root_y + 8
        if popup_x + popup_w > right:
            # Keep the map beside the panel on the SAME monitor when possible.  Work-area origins
            # can be negative or offset on a secondary display, so compare against left/top rather
            # than the primary display's zero origin.
            left_x = root_x - popup_w - 8
            if left_x >= left:
                popup_x = left_x
            else:
                popup_x = root_x
                popup_y = root_y + root_h + 8
        if popup_y + popup_h > bottom:
            popup_y = root_y - popup_h - 8
        return _clamp_rect_into_workarea(
            popup_x,
            popup_y,
            popup_w,
            popup_h,
            left,
            top,
            right,
            bottom,
        )

    def _sync_pinned_minimap_position(
        self,
        *,
        root_x: int | None = None,
        root_y: int | None = None,
    ) -> None:
        if (
            self._pinned_minimap_popup is None
            or self._pinned_offset is None
            or not getattr(self, "_pinned_minimap_follow", True)
        ):
            return
        if root_x is None:
            root_x = self.root.winfo_x()
        if root_y is None:
            root_y = self.root.winfo_y()
        offset_x, offset_y = self._pinned_offset
        popup_w = max(1, self._pinned_minimap_popup.winfo_width() or 292)
        popup_h = max(1, self._pinned_minimap_popup.winfo_height() or 382)
        popup_x, popup_y = self._popup_geometry_bounds(
            popup_w=popup_w,
            popup_h=popup_h,
            root_x=root_x + offset_x,
            root_y=root_y + offset_y,
        )
        self._pinned_minimap_popup.geometry(f"+{popup_x}+{popup_y}")

    def _bind_pinned_minimap_drag(self, *widgets: tk.Widget) -> None:
        for widget in widgets:
            widget.bind("<ButtonPress-1>", self._begin_pinned_minimap_drag, add="+")
            widget.bind("<B1-Motion>", self._drag_pinned_minimap, add="+")
            widget.bind("<ButtonRelease-1>", self._end_pinned_minimap_drag, add="+")

    def _begin_pinned_minimap_drag(self, event: tk.Event[Any]) -> str:
        if self._pinned_minimap_popup is None:
            return ""
        self._pinned_minimap_drag_offset = (
            event.x_root - self._pinned_minimap_popup.winfo_x(),
            event.y_root - self._pinned_minimap_popup.winfo_y(),
        )
        return "break"

    def _drag_pinned_minimap(self, event: tk.Event[Any]) -> str:
        if self._pinned_minimap_popup is None or self._pinned_minimap_drag_offset is None:
            return ""
        dx, dy = self._pinned_minimap_drag_offset
        popup_w = max(1, self._pinned_minimap_popup.winfo_width() or 292)
        popup_h = max(1, self._pinned_minimap_popup.winfo_height() or 382)
        popup_x, popup_y = self._popup_geometry_bounds(
            popup_w=popup_w,
            popup_h=popup_h,
            root_x=event.x_root - dx,
            root_y=event.y_root - dy,
            anchor_x=event.x_root,
            anchor_y=event.y_root,
        )
        self._pinned_minimap_popup.geometry(f"+{popup_x}+{popup_y}")
        if getattr(self, "_pinned_minimap_follow", True):
            self._pinned_minimap_follow = False
            self._pinned_offset = None
            self._mark_pinned_minimap_detached()
        return "break"

    def _end_pinned_minimap_drag(self, _event: tk.Event[Any]) -> None:
        self._pinned_minimap_drag_offset = None

    def _mark_pinned_minimap_detached(self) -> None:
        if self._pinned_title is None:
            return
        current = str(self._pinned_title.cget("text") or "")
        if not current.endswith(MINIMAP_PINNED_FREE_SUFFIX):
            self._pinned_title.configure(text=f"{current}{MINIMAP_PINNED_FREE_SUFFIX}")

    def _on_pinned_minimap_configure(self, event: tk.Event[Any]) -> None:
        if self._pinned_canvas is None or event.widget is not self._pinned_canvas:
            return
        if self._pinned_configure_after_id is not None:
            try:
                self.root.after_cancel(self._pinned_configure_after_id)
            except tk.TclError:
                pass
        self._pinned_configure_after_id = self.root.after(80, self._redraw_pinned_minimap)

    def toggle_pinned_minimap(self, event: tk.Event[Any] | None = None) -> str:
        self._hide_minimap_popup()
        if self._pinned_minimap_popup is not None:
            self._hide_pinned_minimap()
            return "break"

        popup = tk.Toplevel(self.root)
        popup.withdraw()
        popup.overrideredirect(True)
        popup.attributes("-topmost", bool(getattr(self, "topmost_enabled", True)))
        _apply_windows_toolwindow(popup, enabled=not self.show_taskbar)
        popup.configure(bg=BG)
        shell = self._card(popup, bg=PANEL, padx=7, pady=7)
        shell.pack(fill="both", expand=True)
        header = tk.Frame(shell, bg=PANEL)
        header.pack(fill="x", pady=(0, 5))
        self._pinned_title = tk.Label(
            header,
            text="常驻地图",
            bg=PANEL,
            fg=TEXT,
            font=(FONT_UI, 8, "bold"),
            anchor="w",
        )
        self._pinned_title.pack(side="left", fill="x", expand=True)
        self._pinned_counts = tk.Label(
            header,
            text="-",
            bg=PANEL,
            fg=MUTED,
            font=(FONT_UI, 7),
            anchor="e",
        )
        self._pinned_counts.pack(side="left", padx=(6, 8))
        close = tk.Button(
            header,
            text="×",
            command=self._hide_pinned_minimap,
            bg=BAD,
            fg="#ffffff",
            activebackground="#ff8aa0",
            activeforeground="#ffffff",
            relief="flat",
            borderwidth=0,
            padx=5,
            pady=0,
            font=(FONT_UI, 8, "bold"),
        )
        close.pack(side="right")
        self._bind_pinned_minimap_drag(header, shell, self._pinned_title, self._pinned_counts)
        HoverTip(header, MINIMAP_PINNED_DRAG_TIP)

        canvas_frame = tk.Frame(shell, bg=PANEL)
        canvas_frame.pack(fill="both", expand=True)
        self._pinned_canvas = tk.Canvas(
            canvas_frame,
            width=260,
            height=320,
            bg=MINIMAP_BG,
            highlightthickness=1,
            highlightbackground=BORDER,
        )
        vbar = _slim_scrollbar(canvas_frame, command=self._pinned_canvas.yview)
        self._pinned_canvas.configure(yscrollcommand=vbar.set)
        self._pinned_canvas.grid(row=0, column=0, sticky="nsew")
        vbar.grid(row=0, column=1, sticky="ns")
        canvas_frame.columnconfigure(0, weight=1)
        canvas_frame.rowconfigure(0, weight=1)
        self._pinned_canvas_tip = HoverTip(self._pinned_canvas)
        self._pinned_canvas.bind("<MouseWheel>", self._scroll_pinned_minimap, add="+")
        self._pinned_canvas.bind("<Configure>", self._on_pinned_minimap_configure, add="+")

        popup_w = 292
        popup_h = 382
        root_x = self.root.winfo_x()
        root_y = self.root.winfo_y()
        popup_x, popup_y = self._minimap_popup_position_near_root(
            popup_w=popup_w,
            popup_h=popup_h,
            right_gap=8,
        )
        popup.geometry(f"{popup_w}x{popup_h}+{popup_x}+{popup_y}")
        self._pinned_offset = (popup_x - root_x, popup_y - root_y)
        self._pinned_minimap_follow = True
        self._pinned_minimap_drag_offset = None
        self._pinned_minimap_popup = popup
        self.map_button.configure(bg=PANEL_MUTED, fg=WARM)
        popup.deiconify()
        popup.update_idletasks()
        self._redraw_pinned_minimap()
        return "break"

    def _redraw_pinned_minimap(self) -> None:
        self._pinned_configure_after_id = None
        if (
            self._pinned_minimap_popup is None
            or self._pinned_canvas is None
            or self._pinned_title is None
            or self._pinned_counts is None
        ):
            return
        self._render_minimap_header(
            title=self._pinned_title,
            counts=self._pinned_counts,
            minimap=self._minimap_data,
        )
        if not getattr(self, "_pinned_minimap_follow", True):
            self._mark_pinned_minimap_detached()
        self._draw_minimap(
            self._pinned_canvas,
            self._minimap_data,
            self._pinned_canvas_tip,
            min_width=260,
            min_height=320,
            allow_scroll=True,
            cell_hint=self._minimap_cell_hint(MINIMAP_POPUP_CELL_HINT_BASE),
        )

    def _snapshot_signature(
        self,
        snapshot: Any = _SNAPSHOT_NOT_LOADED,
    ) -> tuple[Any, ...]:
        if snapshot is _SNAPSHOT_NOT_LOADED:
            snapshot = _read_json(self.snapshot_path)
        return self._live_inference_signature(snapshot if isinstance(snapshot, dict) else None)

    def _live_inference_signature(self, snapshot: dict[str, Any] | None) -> tuple[Any, ...]:
        if isinstance(snapshot, dict):
            sig = inference_signature_from_snapshot(snapshot)
            if sig:
                try:
                    ref_sig = ref_input_signature_from_snapshot(snapshot)
                except Exception:  # noqa: BLE001 - refresh keeps the legacy signature
                    ref_sig = ()
                if ref_sig:
                    return ("__monitor_and_ref_inputs__", sig, ref_sig)
                return sig
        try:
            stat = self.snapshot_path.stat()
        except OSError:
            return (0, 0)
        return ("__legacy_mtime__", stat.st_mtime_ns, stat.st_size)

    def _live_bid_signature(self, snapshot: dict[str, Any]) -> tuple[Any, ...]:
        return bid_signature_from_snapshot(snapshot)

    def _snapshot_file_age_seconds(self) -> float | None:
        try:
            return max(0.0, time.time() - self.snapshot_path.stat().st_mtime)
        except OSError:
            return None

    def _capture_status(self) -> dict[str, Any]:
        base_dir = self.snapshot_path.parent
        status = _read_json(base_dir / "capture_source_status.json")
        if not isinstance(status, dict):
            status = {}
        network_status = _read_json(
            base_dir / NETWORK_CONNECTION_STATUS_FILENAME
        )
        if network_status:
            status["_network_connection_status"] = network_status
        helper_status = _read_json(base_dir / "wss_helper_status.json")
        if helper_status:
            status["_wss_helper_status"] = helper_status
        compute_status = _read_json(base_dir / COMPUTE_STATUS_FILENAME)
        if (
            isinstance(compute_status, dict)
            and compute_status.get("schema_version") == COMPUTE_STATUS_SCHEMA_VERSION
        ):
            status["_monitor_compute_status"] = compute_status
        startup_session = _read_json(base_dir / "startup_session.json")
        current_launch_id = _text(startup_session.get("launch_id"), "")
        if current_launch_id:
            status["_current_launch_id"] = current_launch_id
        return status

    def _record_summary_diagnostic(
        self,
        data: dict[str, Any],
        *,
        render_mode: str,
    ) -> None:
        if self._ui_interaction_busy():
            return
        if (
            _normalize_diagnostic_profile(
                getattr(self, "diagnostic_profile", DEFAULT_DIAGNOSTIC_PROFILE)
            )
            != "engineering"
        ):
            return
        try:
            row = _summary_diagnostic_row(
                data,
                snapshot_path=self.snapshot_path,
                render_mode=render_mode,
                manual_active=bool(self._manual_active),
                settlement_values_hidden=bool(self.settlement_values_hidden),
            )
            raw_source_snapshot = getattr(self, "_last_live_snapshot", {})
            source_snapshot = raw_source_snapshot if isinstance(raw_source_snapshot, dict) else {}
            row["source_files"] = {
                "file": source_snapshot.get("file"),
                "raw_capture": source_snapshot.get("raw_capture"),
                "raw_capture_jsonl": source_snapshot.get("raw_capture_jsonl"),
            }
            signature = _summary_diagnostic_signature(row)
            if signature == self._last_summary_log_signature:
                return
            self._last_summary_log_signature = signature
            log_path = self.snapshot_path.parent / SUMMARY_DIAGNOSTIC_LOG
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
                fh.write("\n")
        except Exception:
            return

    def _record_ui_health(self, row: dict[str, Any]) -> None:
        try:
            log_path = self.snapshot_path.parent / UI_HEALTH_LOG
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
                fh.write("\n")
        except Exception:
            return

    def _record_safe_frame_telemetry(
        self,
        data: dict[str, Any],
        *,
        paired_presence: bool,
    ) -> None:
        """Keep a bounded, value-free frame trail for support diagnostics."""
        try:
            raw_context = data.get("context")
            context = raw_context if type(raw_context) is dict else {}  # noqa: E721
            session_id = context.get("session_id")
            previous = getattr(self, "_safe_frame_session_id", None)
            if type(session_id) is str and session_id != previous:  # noqa: E721
                self._safe_frame_session_id = session_id
                self._safe_frame_session_seq = int(
                    getattr(self, "_safe_frame_session_seq", 0)
                ) + 1
            raw_result = data.get("ahmed_ref")
            result = raw_result if type(raw_result) is dict else {}  # noqa: E721
            notes = tuple(str(item) for item in (result.get("notes") or ()))
            composition = detail_composition_view(data)
            red_quality = composition.quality("q6")
            red_triplet = quality_paired_candidate_triplet(red_quality)
            red_candidates_physical = bool(red_quality.candidates) and all(
                composition_candidate_is_physical(candidate)
                for candidate in red_quality.candidates
            )
            red_triplet_complete = bool(
                red_triplet is not None
                and all(candidate is not None for candidate in red_triplet)
            )
            if red_quality.status == "conflict":
                red_reason_code = "conflict"
            elif red_quality.status not in {"ready", "exact"}:
                red_reason_code = "unavailable"
            elif not red_quality.candidates:
                red_reason_code = "no_candidates"
            elif not red_candidates_physical:
                red_reason_code = "nonphysical"
            elif red_quality.count_correlation_state == "unknown":
                red_reason_code = "unknown_correlation"
            else:
                red_reason_code = "ready"
            raw_evidence = result.get("evidence")
            evidence = raw_evidence if type(raw_evidence) is dict else {}  # noqa: E721
            total_count = evidence.get("total_count")
            total_count_sources = tuple(
                code
                for note, code in (
                    ("action_100115_total_count", "inventory_census"),
                    ("action_100204_total_count", "ahmed_skill"),
                    ("public_info_total_item_count", "public_info"),
                    ("structured_ref_bridge_total_count", "structured_bridge"),
                    ("field_update_total_count", "field_update"),
                    ("settlement_review_total_count", "settlement"),
                )
                if note in notes
            )
            source_snapshot = getattr(self, "_last_live_snapshot", {})
            action_rows = (
                source_snapshot.get("action_result_rows")
                if type(source_snapshot) is dict  # noqa: E721
                else ()
            )
            round_no = _parse_int(context.get("round"))
            current_direct_action = bool(
                type(action_rows) is list  # noqa: E721
                and any(
                    type(item) is dict  # noqa: E721
                    and dict.get(item, "source_kind") == "direct_action_response"
                    and _parse_int(dict.get(item, "round")) == round_no
                    for item in action_rows
                )
            )
            labels = getattr(self, "red_count_triplet_labels", {})
            sizes = [
                int(tkfont.Font(root=self.root, font=label.cget("font")).cget("size"))
                for label in labels.values()
            ]
            row = {
                "schema": 3,
                "session_seq": int(getattr(self, "_safe_frame_session_seq", 0)),
                "round": round_no,
                "applied_seq": int(getattr(self, "_summary_worker_seq", 0)),
                "paired_presence": bool(paired_presence),
                "red_paired_physical": red_triplet_complete,
                "q6_status": red_quality.status,
                "q6_correlation_state": red_quality.count_correlation_state,
                "q6_reason_code": red_reason_code,
                "q6_candidate_count": len(red_quality.candidates),
                "q6_candidates_physical": red_candidates_physical,
                "q6_triplet_complete": red_triplet_complete,
                "total_count_present": type(total_count) is int and total_count >= 0,  # noqa: E721
                "total_count_sources": total_count_sources,
                "current_direct_action": current_direct_action,
                "item_signal": current_direct_action
                or any("item" in note for note in notes),
                "item_pass_applied": "aisha_quote_pass:item" in notes,
                "hard_joint_quote_floor_applied": any(
                    note.startswith("hard_joint_catalog_quote_floor:")
                    for note in notes
                ),
                "font_size_min": min(sizes) if sizes else 0,
            }
            log_path = self.snapshot_path.parent / SAFE_FRAME_TELEMETRY_LOG
            log_path.parent.mkdir(parents=True, exist_ok=True)
            prior: list[str] = []
            if log_path.is_file():
                prior = [
                    line
                    for line in log_path.read_text(encoding="utf-8").splitlines()
                    if line
                ]
            lines = (prior + [json.dumps(row, sort_keys=True, separators=(",", ":"))])[
                -SAFE_FRAME_TELEMETRY_LIMIT:
            ]
            tmp = log_path.with_suffix(log_path.suffix + ".tmp")
            tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            os.replace(tmp, log_path)
        except Exception:
            return

    def _record_ui_runtime_status(
        self,
        event: str,
        *,
        snapshot_signature: tuple[int, int] | None = None,
        capture_status: dict[str, Any] | None = None,
        error: Any = None,
    ) -> None:
        if self._ui_interaction_busy():
            return
        try:
            status_path = self.snapshot_path.parent / UI_RUNTIME_STATUS
            status_path.parent.mkdir(parents=True, exist_ok=True)
            signature = snapshot_signature
            if signature is None:
                signature = self._snapshot_signature()
            last_signature = getattr(self, "_last_signature", None)
            summary = getattr(self, "_last_summary", {})
            if not isinstance(summary, dict):
                summary = {}
            context = summary.get("context") if isinstance(summary.get("context"), dict) else {}
            reference = summary.get("reference") if isinstance(summary.get("reference"), dict) else {}
            age = self._snapshot_file_age_seconds()
            summary_worker_running = bool(getattr(self, "_summary_worker_running", False))
            summary_worker_mode = getattr(self, "_summary_worker_mode", None)
            summary_worker_elapsed = None
            summary_worker_started = getattr(self, "_summary_worker_started_wall", None)
            if summary_worker_running and isinstance(summary_worker_started, (int, float)):
                summary_worker_elapsed = max(0.0, time.monotonic() - float(summary_worker_started))
            manual_worker_running = bool(getattr(self, "_manual_worker_running", False))
            manual_active = bool(getattr(self, "_manual_active", False))
            manual_edit_enabled = bool(getattr(self, "_manual_edit_enabled", False))
            compute_latency = self._compute_latency_diagnostics()
            activation_diag = getattr(self, "_act_diag", {})
            if not isinstance(activation_diag, dict):
                activation_diag = {}
            activation_status_key = tuple(
                activation_diag.get(key)
                for key in (
                    "activated", "mode", "reason", "source",
                    "next_retry_seconds", "consecutive_transient_failures",
                )
            )
            status_key = (
                event,
                signature,
                _capture_status_signature(capture_status or {}),
                summary.get("status"),
                context.get("session_id"),
                context.get("phase"),
                reference.get("source"),
                summary_worker_running,
                summary_worker_mode,
                manual_worker_running,
                manual_active,
                manual_edit_enabled,
                compute_latency.get("event"),
                compute_latency.get("pending"),
                compute_latency.get("trace_seq"),
                activation_status_key,
                _text(error, ""),
            )
            now_monotonic = time.monotonic()
            last_status_key = getattr(self, "_last_ui_runtime_status_key", None)
            last_status_at = float(getattr(self, "_last_ui_runtime_status_at", 0.0))
            if (
                status_key == last_status_key
                and now_monotonic - last_status_at < UI_RUNTIME_STATUS_REPEAT_INTERVAL_SECONDS
            ):
                return
            payload: dict[str, Any] = {
                "logged_at": time.time(),
                "event": event,
                "snapshot_path": str(self.snapshot_path),
                "snapshot_exists": signature != (0, 0),
                "snapshot_signature": list(signature),
                "snapshot_age_seconds": None if age is None else round(age, 3),
                "last_applied_signature": list(last_signature)
                if isinstance(last_signature, tuple)
                else None,
                "diagnostic_profile": getattr(
                    self,
                    "diagnostic_profile",
                    DEFAULT_DIAGNOSTIC_PROFILE,
                ),
                "summary_worker_running": summary_worker_running,
                "summary_worker_mode": summary_worker_mode,
                "summary_worker_elapsed_seconds": (
                    None if summary_worker_elapsed is None else round(summary_worker_elapsed, 3)
                ),
                "manual_worker_running": manual_worker_running,
                "manual_active": manual_active,
                "manual_edit_enabled": manual_edit_enabled,
                "compute_latency": compute_latency,
                "activation": dict(activation_diag),
                "last_summary": {
                    "status": summary.get("status"),
                    "hero": context.get("hero"),
                    "map_id": context.get("map_id"),
                    "round": context.get("round"),
                    "phase": context.get("phase"),
                    "session_id": context.get("session_id"),
                    "reference_source": reference.get("source"),
                },
            }
            if capture_status is not None:
                payload["capture"] = _compact_capture_runtime_status(capture_status)
            if error is not None:
                payload["error"] = _text(error, "")
            tmp = status_path.with_name(
                f"{status_path.name}.{threading.get_ident()}.tmp"
            )
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(status_path)
            self._last_ui_runtime_status_key = status_key
            self._last_ui_runtime_status_at = now_monotonic
        except Exception:
            return

    def _start_ui_health_watchdog(self) -> None:
        def _watch() -> None:
            while not bool(getattr(self, "_exit_cleanup_done", True)):
                time.sleep(1.0)
                now = time.monotonic()
                last = float(getattr(self, "_last_ui_heartbeat_at", now))
                gap_seconds = max(0.0, now - last)
                if gap_seconds < UI_STALL_SECONDS:
                    continue
                bucket = int(gap_seconds // UI_STALL_LOG_INTERVAL_SECONDS)
                if bucket == int(getattr(self, "_last_ui_stall_bucket", 0)):
                    continue
                self._last_ui_stall_bucket = bucket
                self._record_ui_health(
                    {
                        "logged_at": time.time(),
                        "event": "ui_event_loop_stall_suspected",
                        "gap_seconds": round(gap_seconds, 3),
                        "snapshot_path": str(self.snapshot_path),
                        "diagnostic_profile": getattr(
                            self,
                            "diagnostic_profile",
                            DEFAULT_DIAGNOSTIC_PROFILE,
                        ),
                    }
                )

        self._ui_health_thread = threading.Thread(
            target=_watch,
            name="HeroRefUIHealthWatchdog",
            daemon=True,
        )
        self._ui_health_thread.start()

    def _snapshot_session_id_from_file(self, snapshot: dict[str, Any]) -> str:
        context = snapshot.get("ui_contract", {}).get("context")
        if isinstance(context, dict):
            session_id = context.get("session_id")
            if session_id:
                return _text(session_id, "").strip()
        return _text(snapshot.get("session_id"), "").strip()

    def _capture_session_ahead_of_snapshot_file(self) -> bool:
        snapshot = _read_json(self.snapshot_path)
        if not isinstance(snapshot, dict):
            return False
        capture_status = self._capture_status()
        if not capture_status:
            return False
        return capture_session_ahead_of_snapshot(
            capture_status,
            snapshot_session_id=self._snapshot_session_id_from_file(snapshot),
        )

    def _needs_stale_refresh(self) -> bool:
        summary = self._last_live_summary if self._manual_active else self._last_summary
        if not summary or summary.get("status") == "stale_snapshot":
            return False
        identity = self._stale_refresh_state_identity()
        return identity is not None and identity not in (
            getattr(self, "_stale_refresh_requested_identity", None),
            getattr(self, "_stale_refresh_completed_identity", None),
        )

    def _stale_refresh_state_identity(
        self, snapshot: dict[str, Any] | None = None, *, include_fresh_age: bool = False,
    ) -> tuple[Any, ...] | None:
        """Identify a stale transition, not the continuously increasing age.

        File publication and semantic identity are separate: a rewritten but
        equivalent input must be allowed to cross its own age threshold later.
        Capture heartbeats do not change identity; a different active session does.
        """
        if snapshot is None:
            snapshot = _read_json(self.snapshot_path)
        if not isinstance(snapshot, dict) or not snapshot:
            return None
        try:
            stat = self.snapshot_path.stat()
        except OSError:
            return None
        input_identity = (
            max(0, int(getattr(self, "_compute_monitor_generation", 0))),
            self._live_inference_signature(snapshot), stat.st_mtime_ns, stat.st_size,
        )
        if self._capture_session_ahead_of_snapshot_file():
            return ("session_ahead", _text(self._capture_status().get("active_session_id"), "").strip(),
                    input_identity)
        summary = getattr(
            self, "_last_live_summary" if getattr(self, "_manual_active", False) else "_last_summary", {}
        )
        summary = summary if isinstance(summary, dict) else {}
        context = summary.get("context") if isinstance(summary.get("context"), dict) else {}
        phase = self._summary_phase(snapshot) or _text(context.get("phase"), "")
        if phase == "settled":
            return None
        age = max(0.0, time.time() - stat.st_mtime)
        return ("age", input_identity) if include_fresh_age or age >= STALE_SNAPSHOT_SECONDS else None

    def _record_completed_stale_refresh(
        self, summary: dict[str, Any], identity: Any,
    ) -> None:
        # Only an accepted real worker/synchronous result can close a transition.
        # Cache/bid-only applies and superseded/invalid/error results never call here.
        if not isinstance(identity, tuple) or AhmadTkOverlay._activation_product_denied(self):
            return
        stale = summary.get("stale")
        reference = summary.get("ahmed_ref")
        view = detail_composition_view(summary)
        if (
            summary.get("status") == "ok"
            and isinstance(stale, dict)
            and stale.get("reason") == "stale_snapshot"
            and stale.get("keep_last_quote") is True
            and not (type(reference) is dict and reference.get("status") == "error")
            and view.status in {"ready", "exact"}
            and view.quality("q6").status in {"ready", "exact"}
            and bool(view.quality("q6").candidates)
        ):
            self._stale_refresh_completed_identity = identity

    def _mark_summary_performance(
        self,
        summary: dict[str, Any],
        key: str,
        started_at: float,
    ) -> None:
        diagnostics = summary.setdefault("diagnostics", {})
        if not isinstance(diagnostics, dict):
            diagnostics = {}
            summary["diagnostics"] = diagnostics
        performance = diagnostics.setdefault("performance", {})
        if not isinstance(performance, dict):
            performance = {}
            diagnostics["performance"] = performance
        performance[key] = round((time.perf_counter() - started_at) * 1000.0, 2)

    def _ui_interaction_busy(self) -> bool:
        return bool(
            getattr(self, "_resize_active", False)
            or getattr(self, "_drag_active", False)
        )

    def _cancel_minimap_render_timer(self) -> None:
        after_id = getattr(self, "_minimap_render_after_id", None)
        if after_id is None:
            return
        try:
            self.root.after_cancel(after_id)
        except tk.TclError:
            pass
        self._minimap_render_after_id = None

    def _schedule_minimap_canvas_render(self) -> None:
        if self._ui_interaction_busy():
            return
        self._cancel_minimap_render_timer()
        self._minimap_render_after_id = self.root.after(1, self._flush_scheduled_minimap_canvas_render)

    def _flush_scheduled_minimap_canvas_render(self) -> None:
        self._minimap_render_after_id = None
        if self._ui_interaction_busy():
            return
        minimap = getattr(self, "_pending_minimap_data", None)
        if not isinstance(minimap, dict):
            return
        if self.details_expanded:
            self._draw_minimap(
                self.minimap_canvas,
                minimap,
                self._canvas_tip,
                min_width=300,
                min_height=160,
                allow_scroll=True,
                cell_hint=self._minimap_cell_hint(MINIMAP_CELL_HINT_BASE),
            )
            self._set_minimap_meta(minimap)
        if self._minimap_popup is not None and self._popup_canvas is not None:
            self._redraw_minimap_popup()
        if self._pinned_minimap_popup is not None and self._pinned_canvas is not None:
            self._redraw_pinned_minimap()

    def _flush_deferred_live_summary(self) -> None:
        pending = getattr(self, "_deferred_live_apply", None)
        if not isinstance(pending, dict):
            return
        self._deferred_live_apply = None
        snapshot = pending.get("snapshot")
        summary = pending.get("summary")
        if isinstance(snapshot, dict) and isinstance(summary, dict):
            self._apply_live_summary(snapshot, summary, force=True)

    def _apply_live_summary(
        self,
        snapshot: dict[str, Any],
        summary: dict[str, Any],
        *,
        force: bool = False,
    ) -> None:
        # 激活强制(单一汇聚点): 降级态(未激活/试用到期)丢弃任何已算结果, 不渲染。堵住"降级前启动的
        # 在途 worker / 迟到快照"在 refresh gate 之前经 _drain 回填渲染的漏洞(否则降级后偶蹦一轮结果)。
        if AhmadTkOverlay._activation_product_denied(self):
            self._summary_worker_running = False
            self._summary_worker_mode = None
            self._clear_compute_pending("activation_denied")
            self._render_degraded()
            return
        if not force and self._ui_interaction_busy():
            self._deferred_live_apply = {"snapshot": snapshot, "summary": summary}
            return
        if activity_2511_provisional_quote_revoked(snapshot, summary):
            summary = summarize_snapshot(snapshot, snapshot_path=self.snapshot_path)
        incoming_summary = summary
        incoming_view = detail_composition_view(incoming_summary)
        incoming_ref = incoming_summary.get("ahmed_ref")
        phase = self._summary_phase(incoming_summary)
        ref_error = (
            type(incoming_ref) is dict
            and dict.get(incoming_ref, "status") == "error"
        )
        quality_conflict = incoming_view.status == "conflict"
        retryable_problem = phase in {"reading", "bidding"} and (
            ref_error or quality_conflict
        )
        first_retry = False
        problem_reason = "ref_engine_error" if ref_error else "quality_composition_conflict"
        if retryable_problem:
            problem_signature = (
                problem_reason,
                self._summary_session_id(incoming_summary),
                self._live_inference_signature(snapshot),
            )
            if problem_signature != getattr(self, "_ref_problem_retry_signature", None):
                self._ref_problem_retry_signature = problem_signature
                self._last_inference_signature = None
                first_retry = True
        else:
            self._ref_problem_retry_signature = None
        summary = self._summary_with_ethan_r5_outline_continuity(incoming_summary)
        summary = self._summary_with_quality_continuity(summary)
        if first_retry and summary is incoming_summary:
            summary = self._summary_with_quality_retry_pending(
                incoming_summary,
                reason=problem_reason,
            )
        self._last_live_snapshot = snapshot
        self._last_live_summary = summary
        if self._should_reset_manual_for_summary(incoming_summary):
            self._reset_manual_state("已自动清空，等待新局", status_fg=DIM)
        if self._manual_active:
            if not self._manual_live_session_id:
                self._manual_live_session_id = self._summary_session_id(summary)
            self.manual_status.configure(text="手动结果，实时后台", fg=WARM)
        elif getattr(self, "_manual_edit_enabled", False):
            self._set_manual_settlement_button_state()
        else:
            self._auto_sync_manual_inputs(summary)
            self._last_summary = summary
            if summary.get("status") == "stale_snapshot":
                self.render_standby(summary)
            else:
                self.render(summary)
                try:
                    self._maybe_play_pet_settlement_reaction(snapshot)
                except Exception:  # noqa: BLE001 - Pet feedback must never block render
                    pass
                # 首个【真实内容】渲染后再贴合一次窗口高度。init 的 _settle_initial_layout(after_idle)
                # 跑在真实内容渲染【前】(占位 render_missing / 未 settle 的字体缩放), 测出偏高 → mini 窗口
                # 底部留空(群友"刚打开就有空位"); 手填往返能修好, 正是因为它在 settle 后重跑了同一贴合路径。
                # 这里在内容 settle 后按同一路径(_apply_mini_resize_layout finalize=True, 允许缩矮)重贴合一次。
                # 仅一次(getattr 标志)、仅 mini 态, 不干扰后续渲染/用户手动 resize。
                if (
                    not getattr(self, "details_expanded", False)
                    and not getattr(self, "_did_settle_after_content", False)
                ):
                    self._did_settle_after_content = True
                    root = getattr(self, "root", None)
                    if root is not None:
                        try:
                            root.after_idle(self._settle_initial_layout)
                        except (tk.TclError, AttributeError):
                            pass
        self._cache_live_summary_result(snapshot, incoming_summary)

    def _cache_live_summary_result(
        self,
        snapshot: dict[str, Any],
        incoming_summary: dict[str, Any],
    ) -> None:
        """Memoize a valid result without requiring it to be rendered.

        A completed worker can already be superseded by a newer semantic trace.
        Its result is still useful for the existing ref-input cache, but rendering
        it would briefly replace the latest view and could clear the newer
        compute-pending acknowledgement.
        """
        incoming_view = detail_composition_view(incoming_summary)
        incoming_ref = incoming_summary.get("ahmed_ref")
        ref_error = (
            type(incoming_ref) is dict  # noqa: E721 - strict JSON object boundary
            and dict.get(incoming_ref, "status") == "error"
        )
        if (
            incoming_summary.get("status") == "ok"
            and not ref_error
            and incoming_view.status in {"ready", "exact"}
            and incoming_view.quality("q6").status in {"ready", "exact"}
            and bool(incoming_view.quality("q6").candidates)
        ):
            phase_now = self._summary_phase(snapshot) or self._summary_phase(incoming_summary)
            if phase_now in ("reading", "bidding"):
                try:
                    ref_sig = ref_input_signature_from_snapshot(snapshot)
                except Exception:  # noqa: BLE001 - memoize must never block render
                    ref_sig = None
                if ref_sig is not None:
                    self._ref_cache_signature = ref_sig
                    self._ref_cache_summary = incoming_summary
                    self._ref_cache_snapshot = snapshot
            elif phase_now == "settled":
                self._ref_cache_signature = None
                self._ref_cache_summary = None
                self._ref_cache_snapshot = None

    def _summary_result_owner_is_closed(
        self,
        result: Mapping[str, Any],
    ) -> bool:
        result_generation = result.get("compute_monitor_generation")
        current_generation = max(
            0,
            int(getattr(self, "_compute_monitor_generation", 0)),
        )
        if (
            type(result_generation) is int  # noqa: E721 - reject bool generation
            and result_generation != current_generation
        ):
            return True
        result_trace_seq = result.get("compute_trace_seq")
        terminal_trace_seq = max(
            -1,
            int(getattr(self, "_compute_terminal_trace_seq", -1)),
        )
        return (
            type(result_trace_seq) is int  # noqa: E721 - reject bool trace sequence
            and result_trace_seq <= terminal_trace_seq
        )

    def _summary_result_superseded_by_compute_trace(
        self,
        result: Mapping[str, Any],
    ) -> bool:
        if self._summary_result_owner_is_closed(result):
            return True
        result_trace_seq = result.get("compute_trace_seq")
        return (
            bool(getattr(self, "_compute_pending", False))
            and type(result_trace_seq) is int  # noqa: E721 - reject bool sequence
            and result_trace_seq
            < int(getattr(self, "_compute_pending_trace_seq", 0))
        )

    def _ensure_ref_engine_liveness_lock(self) -> Any:
        lock = getattr(self, "_ref_engine_liveness_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._ref_engine_liveness_lock = lock
        if not hasattr(self, "_ref_engine_liveness_generation"):
            self._ref_engine_liveness_generation = 0
        if not hasattr(self, "_ref_engine_liveness_token"):
            self._ref_engine_liveness_token = None
        return lock

    def _schedule_ref_engine_liveness_retry(self) -> float:
        """Record a bounded retry deadline after a proven child/process failure."""
        retry_count = getattr(self, "_ref_engine_liveness_retry_count", 0)
        if type(retry_count) is not int or retry_count < 0:
            retry_count = 0
        retry_count += 1
        retry_delay = min(
            REF_ENGINE_LIVENESS_RETRY_INITIAL_SECONDS * (2 ** (retry_count - 1)),
            REF_ENGINE_LIVENESS_RETRY_MAX_SECONDS,
        )
        self._ref_engine_liveness_retry_count = retry_count
        self._ref_engine_liveness_retry_at = time.monotonic() + retry_delay
        return retry_delay

    def _create_ref_engine_executor(self) -> Any:
        from concurrent.futures import ProcessPoolExecutor

        return ProcessPoolExecutor(max_workers=1)

    def _ref_engine_executor_snapshot_locked(self) -> tuple[Any, Any, Any]:
        return (
            getattr(self, "_ref_engine_liveness_generation", None),
            getattr(self, "_ref_engine_liveness_token", None),
            getattr(self, "_ref_engine_liveness_state", None),
        )

    def _ref_engine_executor_snapshot_is_current_locked(
        self,
        snapshot: tuple[Any, Any, Any],
    ) -> bool:
        return (
            not bool(getattr(self, "_exit_cleanup_done", False))
            and getattr(self, "_ref_engine_liveness_state", None) != "closed"
            and getattr(self, "_ref_engine_liveness_generation", None) == snapshot[0]
            and getattr(self, "_ref_engine_liveness_token", None) is snapshot[1]
            and getattr(self, "_ref_engine_liveness_state", None) == snapshot[2]
        )

    def _start_ref_engine_liveness_warmup(self) -> None:
        """Start one packaged native-child proof without blocking Tk.

        Source/dev keeps the existing lazy process pool.  The explicit rollback flag is the
        only route back to the historical in-thread engine; an automatic failure never takes
        that route because a CPU-bound Python thread cannot be stopped safely.
        """
        if not hasattr(self, "_ref_engine_liveness_results"):
            self._ref_engine_liveness_results = queue.Queue()
        liveness_lock = self._ensure_ref_engine_liveness_lock()
        with liveness_lock:
            if bool(getattr(self, "_exit_cleanup_done", False)) or getattr(
                self, "_ref_engine_liveness_state", None
            ) == "closed":
                return
            if os.environ.get("HERO_REF_DISABLE_PROCESS_ENGINE") == "1":
                self._ref_engine_liveness_state = "rollback"
                self._ref_engine_executor_mode = "thread_rollback"
                return
            if not _is_packaged_build():
                self._ref_engine_liveness_state = "source_ready"
                return
            state = getattr(self, "_ref_engine_liveness_state", "not_started")
            now = time.monotonic()
            retry_at = getattr(self, "_ref_engine_liveness_retry_at", 0.0)
            if not isinstance(retry_at, (int, float)):
                retry_at = 0.0
            if state == "failed" and now < float(retry_at):
                return
            if state not in {"not_started", "failed"}:
                return
            generation = getattr(self, "_ref_engine_liveness_generation", 0)
            if type(generation) is not int or generation < 0:
                generation = 0
            generation += 1
            token = object()
            self._ref_engine_liveness_generation = generation
            self._ref_engine_liveness_token = token
            self._ref_engine_liveness_state = "pending"
            self.__dict__.setdefault("_ref_engine_liveness_inflight", set()).add(token)
        result_queue = self._ref_engine_liveness_results

        def _probe_owned() -> None:
            executor = None
            reason = "probe_failed"
            try:
                executor = self._create_ref_engine_executor()
                with liveness_lock:
                    if (bool(getattr(self, "_exit_cleanup_done", False))
                            or getattr(self, "_ref_engine_liveness_token", None) is not token):
                        _shutdown_ref_engine_executor(executor, hard=True, owner=self)
                        return
                    self._ref_engine_liveness_starting_executor = executor
                    self.__dict__.setdefault("_ref_engine_retiring_executors", {})[id(executor)] = executor
                    future = executor.submit(
                        _ref_engine_child_liveness_probe,
                        _packaged_ref_engine_requires_native(),
                    )
                identity = future.result(timeout=REF_ENGINE_LIVENESS_TIMEOUT_SECONDS)
                if identity != REF_ENGINE_CHILD_LIVENESS_IDENTITY:
                    raise RuntimeError("unexpected child identity")
            except TimeoutError:
                reason = "probe_timeout"
                _shutdown_ref_engine_executor(executor, hard=True, owner=self)
                executor = None
            except Exception:  # noqa: BLE001 - fixed reason only; child errors may expose paths
                _shutdown_ref_engine_executor(executor, hard=True, owner=self)
                executor = None
            published = False
            with liveness_lock:
                if getattr(self, "_ref_engine_liveness_starting_executor", None) is executor:
                    self._ref_engine_liveness_starting_executor = None
                if (
                    not bool(getattr(self, "_exit_cleanup_done", False))
                    and getattr(self, "_ref_engine_liveness_state", None) == "pending"
                    and getattr(self, "_ref_engine_liveness_generation", None) == generation
                    and getattr(self, "_ref_engine_liveness_token", None) is token
                ):
                    result_queue.put(
                        {
                            "status": "ready" if executor is not None else "failed",
                            "reason": reason,
                            "executor": executor,
                            "generation": generation,
                            "token": token,
                        }
                    )
                    published = True
            if not published:
                _shutdown_ref_engine_executor(executor, hard=True, owner=self)

        def _probe() -> None:
            try:
                _probe_owned()
            finally:
                with liveness_lock:
                    self._ref_engine_liveness_inflight.discard(token)

        threading.Thread(
            target=_probe,
            daemon=True,
            name="hero-ref-engine-liveness",
        ).start()

    def _drain_ref_engine_liveness_results(self) -> None:
        results = getattr(self, "_ref_engine_liveness_results", None)
        if results is None:
            return
        liveness_lock = self._ensure_ref_engine_liveness_lock()
        latest: dict[str, Any] | None = None
        discarded_executors: list[Any] = []
        outcome: str | None = None
        resume_pending = False
        with liveness_lock:
            generation = getattr(self, "_ref_engine_liveness_generation", None)
            token = getattr(self, "_ref_engine_liveness_token", None)
            accepting = (
                not bool(getattr(self, "_exit_cleanup_done", False))
                and getattr(self, "_ref_engine_liveness_state", None) == "pending"
            )
            while True:
                try:
                    row = results.get_nowait()
                except queue.Empty:
                    break
                executor = row.get("executor") if isinstance(row, dict) else None
                valid = (
                    accepting
                    and isinstance(row, dict)
                    and type(row.get("generation")) is int
                    and row.get("generation") == generation
                    and row.get("token") is token
                    and token is not None
                )
                if not valid:
                    if executor is not None and all(
                        executor is not item for item in discarded_executors
                    ):
                        discarded_executors.append(executor)
                    continue
                if latest is not None:
                    previous = latest.get("executor")
                    if previous is not None and all(
                        previous is not item for item in discarded_executors
                    ):
                        discarded_executors.append(previous)
                latest = row
            if latest is not None:
                executor = latest.get("executor")
                if latest.get("status") == "ready" and executor is not None:
                    self._ref_engine_executor = executor
                    self._ref_engine_executor_mode = "process"
                    self._ref_engine_liveness_state = "ready"
                    self._ref_engine_pool_disabled = False
                    self._ref_engine_liveness_retry_at = 0.0
                    self._ref_engine_liveness_retry_count = 0
                    outcome = "ready"
                else:
                    if executor is not None and all(
                        executor is not item for item in discarded_executors
                    ):
                        discarded_executors.append(executor)
                    self._ref_engine_executor = None
                    self._ref_engine_executor_mode = None
                    self._ref_engine_liveness_state = "failed"
                    self._ref_engine_pool_disabled = True
                    self._schedule_ref_engine_liveness_retry()
                    outcome = "failed"
                if getattr(self, "_summary_worker_mode", None) == "liveness":
                    self._summary_worker_running = False
                    self._summary_worker_signature = None
                    self._summary_worker_mode = None
                    resume_pending = True
        for executor in discarded_executors:
            _shutdown_ref_engine_executor(executor, hard=True, owner=self)
        if latest is None:
            return
        with liveness_lock:
            still_current = (
                not bool(getattr(self, "_exit_cleanup_done", False))
                and getattr(self, "_ref_engine_liveness_generation", None)
                == latest.get("generation")
                and getattr(self, "_ref_engine_liveness_token", None)
                is latest.get("token")
                and getattr(self, "_ref_engine_liveness_state", None) == outcome
            )
        if not still_current:
            return
        if outcome == "ready":
            self._record_ui_runtime_status("ref_engine_child_ready")
        else:
            self._record_ui_health(
                {
                    "logged_at": time.time(),
                    "event": "ref_engine_child_unavailable",
                    "reason": str(latest.get("reason") or "probe_failed"),
                    "state": "process_failure",
                    "retry_after_seconds": round(
                        max(
                            0.0,
                            float(getattr(self, "_ref_engine_liveness_retry_at", 0.0))
                            - time.monotonic(),
                        ),
                        3,
                    ),
                }
            )
            self._record_ui_runtime_status("ref_engine_child_unavailable")
        if resume_pending:
            self._maybe_start_pending_summary_worker()

    def _ensure_ref_engine_executor(self) -> Any:
        """Return a process executor, a warm-up sentinel, or explicit rollback ``None``."""
        liveness_lock = self._ensure_ref_engine_liveness_lock()
        with liveness_lock:
            if bool(getattr(self, "_exit_cleanup_done", False)) or getattr(
                self, "_ref_engine_liveness_state", None
            ) == "closed":
                self._ref_engine_executor_mode = None
                return None
            snapshot = self._ref_engine_executor_snapshot_locked()
        rollback_requested = os.environ.get("HERO_REF_DISABLE_PROCESS_ENGINE") == "1"
        packaged_build = _is_packaged_build()
        with liveness_lock:
            if not self._ref_engine_executor_snapshot_is_current_locked(snapshot):
                self._ref_engine_executor_mode = None
                return None
            if rollback_requested:
                self._ref_engine_executor_mode = "thread_rollback"
                return None
            if not packaged_build:
                executor = getattr(self, "_ref_engine_executor", None)
                if executor is not None:
                    self._ref_engine_executor_mode = "process"
                    return executor
        if not packaged_build:
            try:
                created_executor = self._create_ref_engine_executor()
            except Exception:  # noqa: BLE001 - fixed failure state, never implicit thread
                with liveness_lock:
                    if self._ref_engine_executor_snapshot_is_current_locked(snapshot):
                        self._ref_engine_liveness_state = "failed"
                        self._ref_engine_executor_mode = None
                return None
            executor = None
            discard = None
            with liveness_lock:
                if not self._ref_engine_executor_snapshot_is_current_locked(snapshot):
                    discard = created_executor
                    self._ref_engine_executor_mode = None
                else:
                    executor = getattr(self, "_ref_engine_executor", None)
                    if executor is None:
                        executor = created_executor
                        self._ref_engine_executor = executor
                    else:
                        discard = created_executor
                    self._ref_engine_executor_mode = "process"
            if discard is not None:
                _shutdown_ref_engine_executor(discard, hard=True, owner=self)
            return executor
        with liveness_lock:
            if not self._ref_engine_executor_snapshot_is_current_locked(snapshot):
                self._ref_engine_executor_mode = None
                return None
            state = getattr(self, "_ref_engine_liveness_state", "not_started")
        if state == "not_started":
            self._start_ref_engine_liveness_warmup()
            with liveness_lock:
                if bool(getattr(self, "_exit_cleanup_done", False)) or getattr(
                    self, "_ref_engine_liveness_state", None
                ) == "closed":
                    self._ref_engine_executor_mode = None
                    return None
                state = getattr(self, "_ref_engine_liveness_state", "pending")
                snapshot = self._ref_engine_executor_snapshot_locked()
        with liveness_lock:
            if not self._ref_engine_executor_snapshot_is_current_locked(snapshot):
                self._ref_engine_executor_mode = None
                return None
            if state == "pending":
                self._ref_engine_executor_mode = "liveness"
                return _REF_ENGINE_WARMUP_PENDING
            if state == "ready":
                executor = getattr(self, "_ref_engine_executor", None)
                if executor is not None:
                    self._ref_engine_executor_mode = "process"
                    return executor
                self._ref_engine_liveness_state = "failed"
                snapshot = self._ref_engine_executor_snapshot_locked()
        # A failed child is distinct from not-yet-ready: both keep the latest input pending,
        # but only the former carries a bounded retry deadline.  Crucially, creation, import,
        # warm-up and probe remain on the liveness thread; a new snapshot must never create a
        # fallback ProcessPoolExecutor on Tk's refresh path.
        self._start_ref_engine_liveness_warmup()
        with liveness_lock:
            snapshot = self._ref_engine_executor_snapshot_locked()
            if not self._ref_engine_executor_snapshot_is_current_locked(snapshot):
                self._ref_engine_executor_mode = None
                return None
            state = getattr(self, "_ref_engine_liveness_state", "failed")
            if state in {"pending", "failed"}:
                self._ref_engine_executor_mode = "liveness"
                return _REF_ENGINE_WARMUP_PENDING
            self._ref_engine_executor_mode = None
            return None

    def _note_pool_fallback(self, reason: str, detail: str = "") -> None:
        """Retain the historical one-shot diagnostic event for compatibility tests.

        Automatic packaged failure paths no longer call this helper or enter a CPU
        summary thread.  The only live thread path is the explicit operator rollback.
        """
        if getattr(self, "_pool_fallback_logged", False):
            return
        self._pool_fallback_logged = True
        try:
            self._record_ui_health(
                {
                    "logged_at": time.time(),
                    "event": "ref_engine_pool_fallback_in_thread",
                    "reason": reason,
                    "detail": detail[:200],
                    "snapshot_path": str(self.snapshot_path),
                }
            )
        except Exception:  # noqa: BLE001 - observability must never break inference
            pass

    def _merge_cached_summary_with_fresh_snapshot(
        self,
        summary: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a shallow copy of ``summary`` with display-only bid/age fields
        refreshed from ``snapshot``. Used when a ref-cache hit skips recomputation:
        the cached recommendation stays, but the current-highest bid and other
        live display fields reflect the latest snapshot so the UI does not look
        frozen on a stale bid.
        """
        if activity_2511_provisional_quote_revoked(snapshot, summary):
            return summarize_snapshot(snapshot, snapshot_path=self.snapshot_path)
        merged = dict(summary)
        merged["reference"] = dict(summary.get("reference") or {})
        uc = snapshot.get("ui_contract") if isinstance(snapshot.get("ui_contract"), dict) else {}
        baseline = uc.get("baseline") if isinstance(uc.get("baseline"), dict) else {}
        decision = baseline.get("decision") if isinstance(baseline.get("decision"), dict) else {}
        new_highest = _mask_bidder_display_text(decision.get("current_highest"))
        merged["reference"]["current_highest"] = new_highest
        # Surface that this render came from the cache (UI health/diagnostics).
        merged.setdefault("diagnostics", {})
        return merged

    def _apply_bid_only_live_update(self, snapshot: dict[str, Any]) -> bool:
        """Refresh displayed current-highest bid without rerunning Hero Ref / formal inference."""
        summary = self._last_live_summary
        if not isinstance(summary, dict) or summary.get("status") != "ok":
            return False
        if activity_2511_provisional_quote_revoked(snapshot, summary):
            self._apply_live_summary(snapshot,
                summarize_snapshot(snapshot, snapshot_path=self.snapshot_path), force=True)
            return True
        uc = snapshot.get("ui_contract") if isinstance(snapshot.get("ui_contract"), dict) else {}
        baseline = uc.get("baseline") if isinstance(uc.get("baseline"), dict) else {}
        decision = baseline.get("decision") if isinstance(baseline.get("decision"), dict) else {}
        new_highest = _mask_bidder_display_text(decision.get("current_highest"))
        reference = summary.get("reference") if isinstance(summary.get("reference"), dict) else {}
        if _text(reference.get("current_highest"), "") == _text(new_highest, ""):
            self._last_live_snapshot = snapshot
            return True
        updated = copy.deepcopy(summary)
        updated.setdefault("reference", {})["current_highest"] = new_highest
        self._apply_live_summary(snapshot, updated, force=True)
        return True

    def _start_live_summary_worker(
        self,
        *,
        snapshot: dict[str, Any],
        signature: tuple[Any, ...],
        started_at: float,
    ) -> bool:
        if not hasattr(self, "_summary_result_queue"):
            return False
        liveness_lock = self._ensure_ref_engine_liveness_lock()
        with liveness_lock:
            if bool(getattr(self, "_exit_cleanup_done", False)):
                return True
        self._set_compute_pending("snapshot_detected")
        compute_trace_seq = int(getattr(self, "_compute_pending_trace_seq", 0))
        compute_monitor_generation = max(
            0,
            int(getattr(self, "_compute_monitor_generation", 0)),
        )
        # 激活强制: 降级态不启任何 live 推理 worker(连结算内联计算也跳), 直接显示降级。
        if AhmadTkOverlay._activation_product_denied(self):
            self._summary_worker_running = False
            self._clear_compute_pending("activation_denied")
            self._render_degraded()
            return False
        # Settlement preempts, but only the truth-first summary runs inline.  The historical
        # pre-settlement clone can enumerate thousands of combinations and must never run on
        # Tk's thread.  A coherent same-session quote receipt fills the estimate during render;
        # otherwise the truth stays visible with ``更新中`` while the normal process worker
        # computes the optional fallback below.
        if self._summary_phase(snapshot) == "settled":
            if getattr(self, "_summary_worker_mode", None) == "process_fallback":
                _shutdown_ref_engine_executor(
                    getattr(self, "_summary_worker_executor", None),
                    hard=True,
                    owner=self,
                )
                self._summary_worker_executor = None
            self._summary_worker_seq = int(getattr(self, "_summary_worker_seq", 0)) + 1
            self._summary_worker_running = False
            self._summary_worker_pending = None
            self._summary_worker_signature = None
            self._summary_worker_mode = None
            try:
                summary = summarize_snapshot(
                    snapshot,
                    snapshot_path=self.snapshot_path,
                    defer_settlement_estimate=True,
                )
            except Exception:  # noqa: BLE001 - degrade to the async worker below
                pass
            else:
                self._mark_summary_performance(summary, "refresh_total_ms", started_at)
                self._last_signature = signature
                self._apply_live_summary(snapshot, summary)
                performance = (
                    (summary.get("diagnostics") or {}).get("performance")
                    if isinstance(summary.get("diagnostics"), dict)
                    else {}
                )
                if not isinstance(performance, dict) or performance.get(
                    "settlement_estimate_state"
                ) != "pending":
                    # A pending truth-first paint is intermediate, not terminal for this trace.
                    # Closing it here makes the same-trace async estimate look stale and drops it.
                    self._clear_compute_pending("settled_applied", rendered=True)
                    return True
        if bool(getattr(self, "_summary_worker_running", False)):
            if getattr(self, "_summary_worker_signature", None) == signature:
                return True
            self._summary_worker_pending = {
                "snapshot": snapshot,
                "signature": signature,
                "started_at": started_at,
            }
            return True
        self._summary_worker_running = True
        self._summary_worker_started_wall = time.monotonic()  # for slow-compute status observability
        self._summary_worker_signature = signature
        self._summary_worker_seq = int(getattr(self, "_summary_worker_seq", 0)) + 1
        seq = self._summary_worker_seq
        snapshot_path = self.snapshot_path
        result_queue = self._summary_result_queue
        stale_refresh_identity = self._stale_refresh_state_identity(snapshot)
        self._summary_worker_stale_refresh_identity = stale_refresh_identity
        if stale_refresh_identity is None:
            # The child may begin after the threshold. Bind its eventual stale
            # result to the submitted file, without consuming a fresh transition.
            stale_refresh_identity = self._stale_refresh_state_identity(snapshot, include_fresh_age=True)

        # Preferred path: compute in a separate PROCESS (pool) so the CPU-bound engine does not starve
        # the Tk UI thread via the GIL. add_done_callback fires on the pool's mgmt thread and puts the
        # SAME result shape the in-thread worker does, so _drain_live_summary_results is unchanged.
        # Any submit failure drops the primary pool and gets at most one bounded fresh-process
        # attempt for this signature.  It never falls through to an automatic CPU thread.
        executor = self._ensure_ref_engine_executor()
        executor_mode = getattr(self, "_ref_engine_executor_mode", None)
        if executor is _REF_ENGINE_WARMUP_PENDING:
            self._summary_worker_mode = "liveness"
            self._summary_worker_pending = {
                "snapshot": snapshot,
                "signature": signature,
                "started_at": started_at,
            }
            return True
        if executor is not None:
            if executor_mode == "process_fallback":
                if getattr(self, "_ref_engine_fallback_attempted_signature", None) == signature:
                    liveness_lock = self._ensure_ref_engine_liveness_lock()
                    with liveness_lock:
                        if getattr(self, "_ref_engine_transient_executor", None) is executor:
                            self._ref_engine_transient_executor = None
                    _shutdown_ref_engine_executor(executor, hard=True, owner=self)
                    result_queue.put(
                        {
                            "seq": seq,
                            "compute_trace_seq": compute_trace_seq,
                            "compute_monitor_generation": compute_monitor_generation,
                            "signature": signature,
                            "snapshot": snapshot,
                            "error": "ref_engine_process_unavailable",
                            "mode": "process_fallback",
                        }
                    )
                    self._summary_worker_mode = "process_unavailable"
                    return True
                self._ref_engine_fallback_attempted_signature = signature
            proc_started = time.perf_counter()
            try:
                liveness_lock = self._ensure_ref_engine_liveness_lock()
                with liveness_lock:
                    if bool(getattr(self, "_exit_cleanup_done", False)):
                        raise RuntimeError("ref_engine_exit_requested")
                    self.__dict__.setdefault("_ref_engine_retiring_executors", {})[id(executor)] = executor
                    future = executor.submit(summarize_snapshot, snapshot, snapshot_path=snapshot_path)
            except Exception:  # noqa: BLE001 - fixed state only; exception may expose paths
                liveness_lock = self._ensure_ref_engine_liveness_lock()
                with liveness_lock:
                    if getattr(self, "_ref_engine_transient_executor", None) is executor:
                        self._ref_engine_transient_executor = None
                    primary_is_current = (
                        executor_mode == "process"
                        and not bool(getattr(self, "_exit_cleanup_done", False))
                        and getattr(self, "_ref_engine_liveness_state", None) != "closed"
                        and getattr(self, "_ref_engine_executor", None) is executor
                    )
                    if primary_is_current:
                        self._ref_engine_executor = None
                        self._ref_engine_liveness_state = "failed"
                        self._ref_engine_pool_disabled = True
                        self._schedule_ref_engine_liveness_retry()
                _shutdown_ref_engine_executor(executor, hard=True, owner=self)
                if primary_is_current:
                    executor = self._ensure_ref_engine_executor()
                    executor_mode = getattr(self, "_ref_engine_executor_mode", None)
                    if executor is not None and executor is not _REF_ENGINE_WARMUP_PENDING:
                        self._ref_engine_fallback_attempted_signature = signature
                        try:
                            with liveness_lock:
                                if bool(getattr(self, "_exit_cleanup_done", False)):
                                    raise RuntimeError("ref_engine_exit_requested")
                                self.__dict__.setdefault("_ref_engine_retiring_executors", {})[id(executor)] = executor
                                future = executor.submit(
                                    summarize_snapshot,
                                    snapshot,
                                    snapshot_path=snapshot_path,
                                )
                        except Exception:  # noqa: BLE001 - bounded fallback also unavailable
                            with liveness_lock:
                                if (
                                    getattr(self, "_ref_engine_transient_executor", None)
                                    is executor
                                ):
                                    self._ref_engine_transient_executor = None
                            _shutdown_ref_engine_executor(executor, hard=True, owner=self)
                            executor = None
                else:
                    executor = None
                if executor is None:
                    result_queue.put(
                        {
                            "seq": seq,
                            "compute_trace_seq": compute_trace_seq,
                            "compute_monitor_generation": compute_monitor_generation,
                            "signature": signature,
                            "snapshot": snapshot,
                            "error": "ref_engine_process_unavailable",
                            "mode": "process_unavailable",
                        }
                    )
                    self._summary_worker_mode = "process_unavailable"
                    return True
            else:
                pass
            self._mark_compute_timestamp("worker_submitted")

            def _on_process_done(fut: Any) -> None:
                completed_monotonic = time.monotonic()
                try:
                    summary = fut.result()
                    self._mark_summary_performance(summary, "refresh_worker_ms", proc_started)
                    self._mark_summary_performance(summary, "refresh_total_ms", started_at)
                    result_queue.put(
                        {
                            "seq": seq,
                            "compute_trace_seq": compute_trace_seq,
                            "compute_monitor_generation": compute_monitor_generation,
                            "signature": signature,
                            "snapshot": snapshot,
                            "summary": summary,
                            "mode": executor_mode,
                            "stale_refresh_identity": stale_refresh_identity,
                            "executor": executor,
                            "summary_completed_monotonic": completed_monotonic,
                        }
                    )
                except Exception:  # noqa: BLE001 - fixed error; exception may expose private paths
                    result_queue.put(
                        {
                            "seq": seq,
                            "compute_trace_seq": compute_trace_seq,
                            "compute_monitor_generation": compute_monitor_generation,
                            "signature": signature,
                            "snapshot": snapshot,
                            "error": "ref_engine_process_failed",
                            "mode": executor_mode,
                            "executor": executor,
                            "summary_completed_monotonic": completed_monotonic,
                        }
                    )

            future.add_done_callback(_on_process_done)
            liveness_lock = self._ensure_ref_engine_liveness_lock()
            with liveness_lock:
                if getattr(self, "_ref_engine_transient_executor", None) is executor:
                    self._ref_engine_transient_executor = None
                executor_is_live = (
                    not bool(getattr(self, "_exit_cleanup_done", False))
                    and getattr(self, "_ref_engine_liveness_state", None) != "closed"
                )
                if executor_is_live:
                    self._summary_worker_executor = executor
                    self._summary_worker_mode = str(executor_mode or "process")
            if not executor_is_live:
                _shutdown_ref_engine_executor(executor, hard=True, owner=self)
            return True

        if executor_mode != "thread_rollback":
            result_queue.put(
                {
                    "seq": seq,
                    "compute_trace_seq": compute_trace_seq,
                    "compute_monitor_generation": compute_monitor_generation,
                    "signature": signature,
                    "snapshot": snapshot,
                    "error": "ref_engine_process_unavailable",
                    "mode": "process_unavailable",
                }
            )
            self._summary_worker_mode = "process_unavailable"
            return True

        def _worker() -> None:
            worker_started = time.perf_counter()
            try:
                summary = summarize_snapshot(snapshot, snapshot_path=snapshot_path)
                completed_monotonic = time.monotonic()
                self._mark_summary_performance(summary, "refresh_worker_ms", worker_started)
                self._mark_summary_performance(summary, "refresh_total_ms", started_at)
                result_queue.put(
                    {
                        "seq": seq,
                        "compute_trace_seq": compute_trace_seq,
                        "compute_monitor_generation": compute_monitor_generation,
                        "signature": signature,
                        "snapshot": snapshot,
                        "summary": summary,
                        "summary_completed_monotonic": completed_monotonic,
                        "stale_refresh_identity": stale_refresh_identity,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - surface worker error in UI health
                result_queue.put(
                    {
                        "seq": seq,
                        "compute_trace_seq": compute_trace_seq,
                        "compute_monitor_generation": compute_monitor_generation,
                        "signature": signature,
                        "snapshot": snapshot,
                        "error": repr(exc),
                        "summary_completed_monotonic": time.monotonic(),
                    }
                )

        self._summary_worker_mode = "thread_rollback"
        with liveness_lock:
            if bool(getattr(self, "_exit_cleanup_done", False)):
                self._summary_worker_running = False
                return True
            compute_threads = self.__dict__.setdefault("_ref_engine_compute_threads", {})
            for identity, prior in tuple(compute_threads.items()):
                if prior.is_alive() is False:
                    compute_threads.pop(identity, None)
            thread = None
            try:
                thread = threading.Thread(target=_worker, daemon=False, name="hero-ref-summary")
                compute_threads[id(thread)] = thread
                thread.start()
            except Exception:
                if thread is not None and thread.ident is not None:
                    # A wrapper can raise after native start; the actual worker
                    # still owns this request and must publish its normal result.
                    return True
                compute_threads.pop(id(thread), None)
                self._summary_worker_running = False
                result_queue.put({
                    "seq": seq, "compute_trace_seq": compute_trace_seq,
                    "compute_monitor_generation": compute_monitor_generation,
                    "signature": signature, "snapshot": snapshot,
                    "error": "ref_engine_thread_start_failed",
                    "summary_completed_monotonic": time.monotonic(),
                })
                return True
            self._mark_compute_timestamp("worker_submitted")
        return True

    def _maybe_start_pending_summary_worker(self) -> None:
        pending = getattr(self, "_summary_worker_pending", None)
        if not isinstance(pending, dict):
            return
        self._summary_worker_pending = None
        snapshot = pending.get("snapshot")
        signature = pending.get("signature")
        started_at = pending.get("started_at")
        if not isinstance(snapshot, dict) or not isinstance(signature, tuple):
            return
        if not isinstance(started_at, (int, float)):
            started_at = time.perf_counter()
        # A snapshot that arrived while the worker was running used to bypass the normal refresh cache
        # and launch a second full engine pass unconditionally. Once the finished result has populated
        # the ref cache, reuse it when the pending snapshot has identical semantic engine inputs; merge
        # only the fresh display fields. This is byte-equivalent for the quote and avoids a second 35s
        # Gabriela pass on mtime/bid-only churn.
        phase_now = self._summary_phase(snapshot)
        pending_ref_sig: tuple[Any, ...] | None = None
        if phase_now != "settled":
            try:
                pending_ref_sig = ref_input_signature_from_snapshot(snapshot)
            except Exception:  # noqa: BLE001 - cache probing must never block a real recompute
                pending_ref_sig = None
        if (
            pending_ref_sig is not None
            and pending_ref_sig == getattr(self, "_ref_cache_signature", None)
            and isinstance(getattr(self, "_ref_cache_summary", None), dict)
        ):
            cached_summary = self._merge_cached_summary_with_fresh_snapshot(
                dict(self._ref_cache_summary), snapshot
            )
            self._mark_summary_performance(cached_summary, "refresh_total_ms", float(started_at))
            cached_summary.setdefault("diagnostics", {})
            cached_summary.setdefault("performance", {})
            cached_summary["performance"]["ref_cache_hit"] = True
            cached_summary["performance"]["pending_ref_cache_hit"] = True
            self._last_signature = signature
            self._apply_live_summary(snapshot, cached_summary)
            self._clear_compute_pending("pending_cache_hit", rendered=True)
            self._record_ui_runtime_status(
                "summary_pending_cache_hit",
                snapshot_signature=signature if isinstance(signature, tuple) else None,
            )
            return
        self._start_live_summary_worker(
            snapshot=snapshot,
            signature=signature,
            started_at=float(started_at),
        )

    def _drain_live_summary_results(self) -> None:
        if not hasattr(self, "_summary_result_queue"):
            return
        latest: dict[str, Any] | None = None
        while True:
            try:
                item = self._summary_result_queue.get_nowait()
            except queue.Empty:
                break
            if latest is None or int(item.get("seq") or 0) >= int(latest.get("seq") or 0):
                latest = item
        if latest is None:
            # No result landed. Only a process-pool future is safely replaceable. A Python thread cannot
            # be cancelled; restarting it would leave the old engine pass consuming CPU and create the
            # exact duplicate-work spiral this watchdog is meant to avoid.
            if bool(getattr(self, "_summary_worker_running", False)):
                started = getattr(self, "_summary_worker_started_wall", None)
                if isinstance(started, (int, float)):
                    elapsed = time.monotonic() - float(started)
                    worker_mode = getattr(self, "_summary_worker_mode", None)
                    timeout_seconds = (
                        SUMMARY_FALLBACK_PROCESS_TIMEOUT_SECONDS
                        if worker_mode == "process_fallback"
                        else SUMMARY_PROCESS_WORKER_TIMEOUT_SECONDS
                    )
                    if worker_mode in {"process", "process_fallback"} and elapsed >= timeout_seconds:
                        stuck_sig = getattr(self, "_summary_worker_signature", None)
                        # Bump seq so the abandoned worker's result (if it ever lands) is ignored.
                        self._summary_worker_seq = int(getattr(self, "_summary_worker_seq", 0)) + 1
                        self._summary_worker_running = False
                        self._summary_worker_signature = None
                        self._summary_worker_mode = None
                        self._ref_engine_pool_disabled = True
                        ex = getattr(self, "_summary_worker_executor", None) or getattr(
                            self, "_ref_engine_executor", None
                        )
                        _shutdown_ref_engine_executor(ex, hard=True, owner=self)
                        self._summary_worker_executor = None
                        self._ref_engine_liveness_state = "failed"
                        self._ref_engine_executor = None
                        retry_delay = self._schedule_ref_engine_liveness_retry()
                        self._record_ui_health(
                            {
                                "logged_at": time.time(),
                                "event": "summary_worker_timeout_recovered",
                                "state": "process_timeout",
                                "elapsed_seconds": round(elapsed, 3),
                                "retry_after_seconds": round(retry_delay, 3),
                                "snapshot_path": str(self.snapshot_path),
                            }
                        )
                        self._record_ui_runtime_status(
                            "summary_worker_timeout_recovered",
                            snapshot_signature=stuck_sig if isinstance(stuck_sig, tuple) else None,
                        )
                        # A failed primary gets one bounded fresh-process attempt. A failed bounded
                        # fallback stays failed for this exact signature, otherwise the refresh loop
                        # would continuously spawn replacements for an input already proved unhealthy.
                        if getattr(self, "_summary_worker_pending", None) is not None:
                            self._maybe_start_pending_summary_worker()
                        elif worker_mode == "process":
                            self._last_inference_signature = None
                            self._last_signature = None
                        if (
                            not bool(getattr(self, "_summary_worker_running", False))
                            and getattr(self, "_summary_worker_pending", None) is None
                        ):
                            self._clear_compute_pending("summary_timeout")
            return
        seq = latest.get("seq")
        if seq != getattr(self, "_summary_worker_seq", None):
            if latest.get("mode") == "process_fallback":
                _shutdown_ref_engine_executor(latest.get("executor"), hard=False, owner=self)
            return
        if self._summary_result_owner_is_closed(latest):
            # Trace numbers restart at reset, so cross-generation work cannot
            # be ordered numerically.  A terminal trace is likewise closed.
            # Retire either without rendering, caching, reporting an obsolete
            # error, or clearing a newer pending trace.
            self._summary_worker_running = False
            completed_mode = latest.get("mode") or getattr(
                self, "_summary_worker_mode", None
            )
            completed_executor = latest.get("executor") or getattr(
                self, "_summary_worker_executor", None
            )
            self._summary_worker_mode = None
            self._summary_worker_executor = None
            self._summary_worker_signature = None
            if completed_mode == "process_fallback":
                _shutdown_ref_engine_executor(completed_executor, hard=False, owner=self)
            self._maybe_start_pending_summary_worker()
            return
        self._summary_worker_running = False
        completed_monotonic = latest.get("summary_completed_monotonic")
        if (
            isinstance(completed_monotonic, (int, float))
            and latest.get("compute_trace_seq")
            == int(getattr(self, "_compute_pending_trace_seq", 0))
        ):
            self._mark_compute_timestamp("summary_completed", float(completed_monotonic))
        signature = latest.get("signature")
        if signature != getattr(self, "_summary_worker_signature", None):
            if latest.get("mode") == "process_fallback":
                _shutdown_ref_engine_executor(latest.get("executor"), hard=False, owner=self)
            return
        completed_mode = latest.get("mode") or getattr(self, "_summary_worker_mode", None)
        completed_executor = latest.get("executor") or getattr(
            self, "_summary_worker_executor", None
        )
        self._summary_worker_mode = None
        self._summary_worker_executor = None
        if completed_mode == "process_fallback":
            _shutdown_ref_engine_executor(completed_executor, hard=False, owner=self)
        if latest.get("error"):
            superseded = self._summary_result_superseded_by_compute_trace(latest)
            if completed_mode == "process":
                _shutdown_ref_engine_executor(completed_executor, hard=True, owner=self)
                self._ref_engine_executor = None
                self._ref_engine_liveness_state = "failed"
                self._ref_engine_pool_disabled = True
                self._schedule_ref_engine_liveness_retry()
                self._last_inference_signature = None
            self._record_ui_health(
                {
                    "logged_at": time.time(),
                    "event": "summary_worker_error",
                    "state": (
                        "process_failure"
                        if completed_mode == "process"
                        else "summary_failure"
                    ),
                    "error": str(latest.get("error") or "ref_engine_process_failed"),
                    "snapshot_path": str(self.snapshot_path),
                }
            )
            self._record_ui_runtime_status(
                "summary_worker_error",
                snapshot_signature=signature if isinstance(signature, tuple) else None,
                error=latest.get("error"),
            )
            self._summary_worker_signature = None
            self._maybe_start_pending_summary_worker()
            if (
                not bool(getattr(self, "_summary_worker_running", False))
                and getattr(self, "_summary_worker_pending", None) is None
                and not superseded
            ):
                self._clear_compute_pending("summary_error")
            return
        summary = latest.get("summary")
        snapshot = latest.get("snapshot")
        if not isinstance(summary, dict) or not isinstance(snapshot, dict):
            superseded = self._summary_result_superseded_by_compute_trace(latest)
            self._summary_worker_signature = None
            self._maybe_start_pending_summary_worker()
            if (
                not bool(getattr(self, "_summary_worker_running", False))
                and getattr(self, "_summary_worker_pending", None) is None
                and not superseded
            ):
                self._clear_compute_pending("summary_invalid_result")
            return
        if (
            getattr(self, "_summary_worker_pending", None) is not None
            or self._summary_result_superseded_by_compute_trace(latest)
        ):
            # Preserve memoization, but never paint a result older than a queued
            # snapshot or a monitor-confirmed semantic trace. The pending path
            # either reuses this cache against the newest snapshot or starts one
            # latest-wins worker; it does not launch duplicate CPU work.
            self._cache_live_summary_result(snapshot, summary)
            self._summary_worker_signature = None
            self._maybe_start_pending_summary_worker()
            return
        self._last_signature = signature
        self._summary_worker_signature = None
        self._record_completed_stale_refresh(summary, latest.get("stale_refresh_identity"))
        self._apply_live_summary(snapshot, summary)
        self._clear_compute_pending("summary_applied", rendered=True)
        self._record_ui_runtime_status(
            "summary_applied",
            snapshot_signature=signature if isinstance(signature, tuple) else None,
        )
        self._maybe_start_pending_summary_worker()

    def _start_manual_summary_worker(
        self,
        *,
        snapshot: dict[str, Any],
        settlement_manual: bool,
        live_session_id: str,
        revision: int,
    ) -> bool:
        if not hasattr(self, "_manual_result_queue"):
            return False
        liveness_lock = self._ensure_ref_engine_liveness_lock()
        with liveness_lock:
            if bool(getattr(self, "_exit_cleanup_done", False)):
                # Handled cancellation, not permission to use the synchronous fallback.
                return True
        # 激活强制: 降级态(未激活/试用到期)手填也不计算(满足"降级后不为任何英雄计算")。
        if AhmadTkOverlay._activation_product_denied(self):
            if hasattr(self, "manual_status"):
                self.manual_status.configure(
                    text=AhmadTkOverlay._activation_denied_text(self),
                    fg=BAD,
                )
            return False
        if bool(getattr(self, "_manual_worker_running", False)):
            if hasattr(self, "manual_status"):
                self.manual_status.configure(text="手动计算中，请稍候", fg=WARM)
            return True
        try:
            from bidking_lab.secure import enforce

            authority_fence = enforce.product_authority_fence_snapshot()
        except Exception:  # noqa: BLE001 - an unreadable authority must fail closed
            authority_fence = None
        if authority_fence is None:
            # Returning True prevents apply_manual_inputs() from falling back
            # to its synchronous calculation path after this authority failure.
            if hasattr(self, "manual_status"):
                self.manual_status.configure(
                    text="激活状态正在更新，请稍后重新应用",
                    fg=WARM,
                )
            return True
        self._manual_worker_running = True
        self._manual_worker_seq = int(getattr(self, "_manual_worker_seq", 0)) + 1
        seq = self._manual_worker_seq
        result_queue = self._manual_result_queue

        def _worker() -> None:
            try:
                summary = self._manual_result_summary(snapshot)
                result_queue.put(
                    {
                        "seq": seq,
                        "snapshot": snapshot,
                        "summary": summary,
                        "settlement_manual": settlement_manual,
                        "live_session_id": live_session_id,
                        "revision": revision,
                        "authority_fence": authority_fence,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - show calculation failure in UI
                result_queue.put(
                    {
                        "seq": seq,
                        "error": repr(exc),
                        "revision": revision,
                        "authority_fence": authority_fence,
                    }
                )

        with liveness_lock:
            if bool(getattr(self, "_exit_cleanup_done", False)):
                self._manual_worker_running = False
                return True
            compute_threads = self.__dict__.setdefault("_ref_engine_compute_threads", {})
            for identity, prior in tuple(compute_threads.items()):
                if prior.is_alive() is False:
                    compute_threads.pop(identity, None)
            thread = None
            try:
                thread = threading.Thread(target=_worker, daemon=False, name="hero-ref-manual")
                compute_threads[id(thread)] = thread
                thread.start()
            except Exception:
                if thread is not None and thread.ident is not None:
                    return True
                compute_threads.pop(id(thread), None)
                self._manual_worker_running = False
                result_queue.put({
                    "seq": seq, "revision": revision, "authority_fence": authority_fence,
                    "error": "manual_thread_start_failed",
                })
                return True
        if hasattr(self, "manual_status"):
            self.manual_status.configure(text="手动计算中...", fg=WARM)
        return True

    def _drain_manual_summary_results(self) -> None:
        if not hasattr(self, "_manual_result_queue"):
            return
        if AhmadTkOverlay._activation_product_denied(self):
            # A manual worker may have started under an allowed grant and finish after an
            # authoritative stop lands.  Invalidate its sequence before touching any
            # queued result so a later re-activation cannot resurrect that old result.
            if not bool(getattr(self, "_manual_worker_authority_denied", False)):
                self._manual_worker_seq = int(getattr(self, "_manual_worker_seq", 0)) + 1
                self._manual_worker_authority_denied = True
            self._manual_worker_running = False
            while True:
                try:
                    self._manual_result_queue.get_nowait()
                except queue.Empty:
                    break
            if hasattr(self, "manual_status"):
                self.manual_status.configure(
                    text=AhmadTkOverlay._activation_denied_text(self),
                    fg=BAD,
                )
            if hasattr(self, "activation_label"):
                self._update_activation_ui()
            self._render_degraded()
            return
        self._manual_worker_authority_denied = False
        latest: dict[str, Any] | None = None
        while True:
            try:
                latest = self._manual_result_queue.get_nowait()
            except queue.Empty:
                break
        if latest is None:
            return
        self._manual_worker_running = False
        if latest.get("seq") != getattr(self, "_manual_worker_seq", None):
            return
        try:
            from bidking_lab.secure import enforce

            authority_is_current = enforce.product_authority_fence_is_current(
                latest.get("authority_fence")
            )
        except Exception:  # noqa: BLE001 - queued work must fail closed
            authority_is_current = False
        if not authority_is_current:
            if hasattr(self, "manual_status"):
                self.manual_status.configure(
                    text="激活状态已更新，请重新应用",
                    fg=WARM,
                )
            return
        if latest.get("revision") != getattr(self, "_manual_input_revision", 0):
            if hasattr(self, "manual_status"):
                self.manual_status.configure(text="输入已改动，请重新应用", fg=WARM)
            return
        if latest.get("error"):
            if hasattr(self, "manual_status"):
                self.manual_status.configure(text=f"计算失败: {latest['error']}", fg=BAD)
            return
        summary = latest.get("summary")
        snapshot = latest.get("snapshot")
        if not isinstance(summary, dict) or not isinstance(snapshot, dict):
            return
        settlement_manual = bool(latest.get("settlement_manual"))
        self._manual_active = True
        self._set_manual_edit_enabled(True)
        self._manual_snapshot = snapshot
        self._manual_summary = summary
        self._last_summary = summary
        self._manual_live_session_id = _text(latest.get("live_session_id"), "")
        if hasattr(self, "manual_status"):
            self.manual_status.configure(
                text="手动计算，结算页已脱离" if settlement_manual else "手动计算，实时后台",
                fg=WARM,
            )
        self._set_manual_button_state()
        if summary.get("status") == "stale_snapshot":
            self.render_standby(summary)
        else:
            self.render(summary)

    def _inference_status_text(self) -> str:
        """Keep the busy status compact so it cannot crowd the adjacent toolbar buttons."""
        return "推理中"

    # ----------------------------------------------------------------- #
    # 激活 / 试用强制(0.2.2 付费版)
    # ----------------------------------------------------------------- #
    def _authoritative_activation_stop_reason(self) -> str:
        """Return only the two states that suppress unrelated recovery actions."""
        state = getattr(self, "_act_state", None)
        if isinstance(state, dict):
            reason = state.get("reason")
            if reason in {"revoked", "device_revoked"}:
                return str(reason)
        try:
            from bidking_lab.secure import enforce

            reason = enforce.activation_state().get("reason")
            if reason in {"revoked", "device_revoked"}:
                return str(reason)
        except Exception:  # noqa: BLE001 - UI suppression must remain no-throw
            pass
        return ""

    def _activation_product_denied(self) -> bool:
        """Give a persisted deny priority over cached UI readiness."""
        state = getattr(self, "_act_state", None)
        try:
            from bidking_lab.secure import enforce

            persisted = enforce.persistent_authoritative_deny_state()
        except Exception:  # noqa: BLE001 - normal checking remains the fallback
            persisted = None
        if isinstance(persisted, dict):
            state = persisted
            self._act_state = persisted
        return isinstance(state, dict) and not bool(state.get("run_full", True))

    def _activation_denied_text(self) -> str:
        state = getattr(self, "_act_state", None)
        if isinstance(state, dict):
            text = state.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
        return "激活暂不可用"

    def _suppress_update_notice_for_authoritative_stop(self) -> bool:
        reason = AhmadTkOverlay._authoritative_activation_stop_reason(self)
        if not reason:
            return False
        self._startup_update_url = ""
        self._startup_update_notice_suppressed = True
        try:
            self.update_notice.pack_forget()
        except (tk.TclError, AttributeError):
            pass
        self._refit_header_title_wrap()
        return True

    def _open_startup_update_url(self) -> str:
        """Open only a transport-valid download URL supplied by the version service."""
        if AhmadTkOverlay._suppress_update_notice_for_authoritative_stop(self):
            return "break"
        url = str(getattr(self, "_startup_update_url", "") or "").strip()
        if not url:
            return "break"
        try:
            from bidking_lab.live.version_check import validate_service_url

            checked = validate_service_url(url, purpose="update download")
            webbrowser.open(checked, new=2, autoraise=True)
        except Exception:  # noqa: BLE001 - an invalid/browser-less URL must not break the UI
            tip = getattr(self, "update_notice_tip", None)
            if tip is not None:
                tip.set_text("发现新版本，但下载地址不可用；请联系作者获取最新版。")
        return "break"

    def _apply_startup_update_notice(self, status: dict[str, Any] | None) -> None:
        self._startup_update_notice_status = status
        self._startup_update_notice_applied = True
        if AhmadTkOverlay._suppress_update_notice_for_authoritative_stop(self):
            return
        self._startup_update_notice_suppressed = False
        view = build_update_notice(status, app_version=HERO_REF_DISPLAY_VERSION)
        self._startup_update_url = str(view.get("url") or "")
        tooltip = str(view.get("tooltip") or f"Hero Ref v{HERO_REF_DISPLAY_VERSION}")
        self.version_tip.set_text(tooltip)
        self.update_notice_tip.set_text(tooltip)
        if not view.get("visible"):
            try:
                self.update_notice.pack_forget()
            except tk.TclError:
                pass
            self._refit_header_title_wrap()
            return
        required = bool(view.get("required"))
        color = BAD if required else WARM
        self.update_notice.configure(
            text=str(view.get("label") or "有新版"),
            fg=color,
            activeforeground=color,
            cursor="hand2" if self._startup_update_url else "arrow",
        )
        try:
            if self.update_notice.winfo_manager() != "pack":
                self.update_notice.pack(side="right", padx=(0, 6))
        except tk.TclError:
            pass
        self._refit_header_title_wrap()

    def _poll_startup_update_notice(self) -> None:
        """Poll a local hand-off briefly; no network call is made by the overlay."""
        if getattr(self, "_startup_update_notice_applied", False):
            return
        status = read_recent_startup_version_status(
            self.snapshot_path.parent,
            app_version=HERO_REF_DISPLAY_VERSION,
            writer_pids=tuple(getattr(self, "exit_when_pids", ())),
        )
        if status is not None:
            self._apply_startup_update_notice(status)
            return
        if time.monotonic() >= float(getattr(self, "_startup_update_notice_deadline", 0.0)):
            self._apply_startup_update_notice(None)
            return
        try:
            self.root.after(300, self._poll_startup_update_notice)
        except tk.TclError:
            pass

    def _start_activation_worker(self) -> None:
        """后台线程: 首次激活(连服务器 /activate, 缓存优先) + 自适应重激活(捡付费/撤销变更)。
        结果存进本进程 enforce._LAST;UI 线程每 tick 用 enforce.activation_state() 重算倒计时。永不阻塞 UI。"""
        def _worker() -> None:
            try:
                from bidking_lab.secure import enforce
            except Exception:  # noqa: BLE001  导不进 secure -> 不强制(放行), 不崩 UI
                return
            first = True
            consecutive_transient_failures = 0
            while True:
                started = time.monotonic()
                try:
                    result = enforce.startup_activate() if first else enforce.refresh_activation()
                except Exception:  # noqa: BLE001 - enforce 本身已降级兜底；这里再防导入态异常
                    result = None
                elapsed_ms = round((time.monotonic() - started) * 1000.0, 1)
                transient = enforce.is_transient_activation_failure(result)
                consecutive_transient_failures = (
                    consecutive_transient_failures + 1 if transient else 0
                )
                delay = enforce.activation_refresh_delay(result, consecutive_transient_failures)
                self._act_diag = {
                    "attempted_at": time.time(),
                    "elapsed_ms": elapsed_ms,
                    "activated": bool(getattr(result, "activated", False)),
                    "mode": str(getattr(result, "mode", "none")),
                    "reason": str(getattr(result, "reason", "error")),
                    "source": str(getattr(result, "source", "unknown")),
                    "next_retry_seconds": delay,
                    "consecutive_transient_failures": consecutive_transient_failures,
                }
                self._act_ready = True
                first = False
                time.sleep(delay)

        threading.Thread(target=_worker, daemon=True, name="hero-ref-activation").start()

    def _hide_activation_label(self) -> None:
        try:
            row = getattr(self, "activation_row", self.activation_label)
            if row.winfo_manager() == "pack":
                row.pack_forget()
                self._refit_mini_after_activation_toggle()
        except tk.TclError:
            pass

    def _show_activation_label(self) -> None:
        try:
            row = getattr(self, "activation_row", self.activation_label)
            if row.winfo_manager() != "pack":
                row.pack(fill="x", after=self.subtitle)
                self._refit_mini_after_activation_toggle()
        except tk.TclError:
            pass

    def _set_activation_entry_available(self, available: bool) -> None:
        """切换 token 管理入口；长期付费隐藏，临近到期及未付费状态可用。"""
        action = getattr(self, "activation_action", None)
        label = getattr(self, "activation_label", None)
        state = getattr(self, "_act_state", {})
        active_paid = (
            isinstance(state, dict)
            and bool(state.get("run_full"))
            and state.get("mode") == "paid"
        )
        if active_paid:
            action_text = "更换 token"
            from bidking_lab.secure.membership_ui import renewal_available
            if renewal_available(state, diagnostic=bool(getattr(self, "network_diagnostic_cohort", False))):
                action_text = "续费 / 更换"
        elif getattr(self, "network_diagnostic_cohort", False):
            action_text = "输入已有 token"
        else:
            action_text = token_entry_action_text(
                getattr(self, "product_profile", None)
            )
        label_tip_text = "点击此处更换 token" if active_paid else "点击此处输入激活 token"
        if action_text == "续费 / 更换":
            label_tip_text = "同类型新卡可叠加会员时长，也可选择更换 token"
        if getattr(self, "network_diagnostic_cohort", False):
            action_tip_text = (
                "打开已有 token 更换入口"
                if active_paid
                else "打开已有 token 输入框"
            )
        else:
            action_tip_text = (
                "打开 token 更换与购买说明"
                if active_paid
                else "打开 token 输入框与购买说明"
            )
        if action is not None:
            try:
                managed = action.winfo_manager() == "pack"
                if available and not managed:
                    pack_options: dict[str, Any] = {"side": "right"}
                    if label is not None:
                        pack_options["before"] = label
                    action.pack(**pack_options)
                elif not available and managed:
                    action.pack_forget()
                if available:
                    action.configure(text=action_text)
            except (tk.TclError, AttributeError):
                pass
        if label is not None:
            try:
                label.configure(cursor="hand2" if available else "arrow")
            except (tk.TclError, AttributeError):
                pass
            try:
                if available:
                    label.bind("<Button-1>", self._open_token_dialog)
                else:
                    label.unbind("<Button-1>")
            except (tk.TclError, AttributeError):
                pass
        tip = getattr(self, "activation_tip", None)
        if tip is not None:
            try:
                tip.hide()
                tip.set_text(label_tip_text if available else "")
            except (tk.TclError, AttributeError):
                pass
        action_tip = getattr(self, "activation_action_tip", None)
        if action_tip is not None:
            try:
                if available:
                    action_tip.set_text(action_tip_text)
                else:
                    action_tip.hide()
            except (tk.TclError, AttributeError):
                pass

    def _refit_mini_after_activation_toggle(self) -> None:
        """激活栏出现/消失会改变内容高度。等待态(无 game 内容)下 `_did_settle_after_content` 不触发,
        没有任何路径重贴窗口高度 → "免费额度 · 检测中…"敲定成试用/隐藏后, mini 窗口底部留一块空(作者实测:
        长期授权隐藏激活栏、或激活栏在首次 settle 之后才出现时最明显)。故在激活栏 pack 管理态真正翻转时补一次
        贴合(仅 mini 态；已映射时 after_idle，withdraw/iconify 时记一次 pending 并在 root Map 后执行；
        复用 finalize 路径按自然高缩/涨)。
        仅在【pack 管理态翻转】时调用(_show/_hide 已各自 winfo_manager 守卫),不会每 tick 抖动。
        不能用 winfo_ismapped 判断：窗口 withdraw/iconify 时，子控件仍由 pack 管理但 mapped=False。"""
        if getattr(self, "details_expanded", False):
            self._activation_refit_pending = False
            return
        root = getattr(self, "root", None)
        if root is None:
            return
        try:
            if not root.winfo_exists():
                return
            if not root.winfo_ismapped():
                # pack 状态已经翻转，不能丢掉这次自然高度重贴。窗口下一次真正 Map 时只执行一次。
                self._activation_refit_pending = True
                return
            self._activation_refit_pending = False
            if getattr(self, "_activation_refit_after_id", None) is None:
                self._activation_refit_after_id = root.after_idle(
                    self._do_refit_mini_for_activation
                )
        except (tk.TclError, AttributeError):
            pass

    def _on_root_map_activation_refit(self, event: tk.Event[Any]) -> None:
        """恢复 withdraw/iconify 后消费一次激活栏高度重贴；忽略子控件 Map 与重复事件。"""
        root = getattr(self, "root", None)
        if (
            root is None
            or event.widget is not root
            or not getattr(self, "_activation_refit_pending", False)
        ):
            return
        self._refit_mini_after_activation_toggle()

    def _do_refit_mini_for_activation(self) -> None:
        self._activation_refit_after_id = None
        if getattr(self, "details_expanded", False) or not hasattr(self, "shell"):
            self._activation_refit_pending = False
            return
        try:
            if not self.root.winfo_exists():
                return
            if not self.root.winfo_ismapped():
                self._activation_refit_pending = True
                return
            self._activation_refit_pending = False
            self.root.update_idletasks()
            width, _ = self._current_window_size()
            self._apply_mini_resize_layout(width, finalize=True)
        except (tk.TclError, AttributeError):
            pass

    def _update_activation_ui(self) -> bool:
        """每 tick 调: 刷新激活标签(倒计时/红色)。返回 True=应降级(拦计算)。永不抛。"""
        try:
            from bidking_lab.secure import enforce
        except Exception:  # noqa: BLE001
            return False
        try:
            st = enforce.persistent_authoritative_deny_state()
            if st is None:
                if not self._act_ready:
                    self.activation_label.configure(text="免费额度 · 检测中…", fg=MUTED)
                    self._set_activation_entry_available(True)
                    self._show_activation_label()
                    return False                      # 无持久deny:保留正常启动检测态
                st = enforce.activation_state()       # 用 _LAST + 当前时间重算
            # 到期态锁存避免瞬时心跳抖动误放行；但服务端可能调整试用时长或修复状态，
            # 因此重新确认有效的 trial/paid 都必须解锁，不能让旧锁存覆盖服务端权威结果。
            valid_entitlement = st.get("run_full") and st.get("mode") in ("trial", "paid")
            state_reason = str(st.get("reason", "") or "")
            token_entry_cannot_resolve = state_reason in {
                "revoked",
                "device_revoked",
                "upgrade_required",
            }
            if state_reason in {"revoked", "device_revoked"}:
                st = dict(st)
                st["needs_token"] = False
            non_entitlement_state = (
                bool(st.get("transport_grace"))
                or token_entry_cannot_resolve
                or state_reason.startswith("error")
                or state_reason in {
                    "offline",
                    "no_server",
                    "state_error",
                    "rate_limited",
                    "server_error",
                }
            )
            if non_entitlement_state:
                # 线路/服务波动不是付费或试用到期。旧 latch 必须旁路，否则一次历史
                # expired 会把 needs_token=False 的网络态重新改成“需激活 token”。
                self._expired_latched = False
            elif valid_entitlement:
                self._expired_latched = False
            elif (not st.get("run_full")) and st.get("mode") in ("trial", "paid"):
                self._expired_latched = True
            if self._expired_latched and not valid_entitlement:
                st = dict(st)
                st["run_full"] = False
                st["needs_token"] = True
                st["warn"] = True
                if st.get("mode") != "paid":
                    st["text"] = "试用已结束 · 需激活 token"
            self._act_state = st
            roaming_refresh = getattr(self, "_activation_roaming_view_refresh", None)
            if callable(roaming_refresh):
                roaming_refresh(st)
            if state_reason in {"revoked", "device_revoked"}:
                AhmadTkOverlay._suppress_update_notice_for_authoritative_stop(self)
            elif getattr(self, "_startup_update_notice_suppressed", False):
                self._apply_startup_update_notice(
                    getattr(self, "_startup_update_notice_status", None)
                )
            run_full = bool(st.get("run_full", True))
            active_paid = run_full and st.get("mode") == "paid"
            # 支持自助续费的会员始终保留同一入口，不必等到最后90天。
            # 不支持续费的旧长期/永久授权保持原紧凑显示规则。
            paid_left = st.get("seconds_left")
            from bidking_lab.secure.membership_ui import renewal_available
            hide_long_paid = active_paid and not renewal_available(
                st, diagnostic=bool(getattr(self, "network_diagnostic_cohort", False))
            ) and (
                paid_left is None
                or (isinstance(paid_left, (int, float)) and paid_left > 90 * 86400)
            )
            token_entry_available = (
                not hide_long_paid
                and not bool(st.get("transport_grace"))
                and (run_full or bool(st.get("needs_token", True)))
            )
            self._set_activation_entry_available(token_entry_available)
            if hide_long_paid:
                self._hide_activation_label()
                return False
            self._show_activation_label()
            warn = bool(st.get("warn", False))
            if not run_full:
                color, prefix = BAD, "🔒 "
            elif st.get("transport_grace"):
                color, prefix = WARN, ""
            elif warn:
                color, prefix = BAD, ""               # 试用/会员将尽: 红
            elif st.get("mode") == "paid":
                color, prefix = GOOD, ""
            else:
                color, prefix = MUTED, ""
            status_text = str(st.get("text", "")).strip()
            # enforce 的 pending 兼容文案曾自带“激活:”前缀；overlay 统一负责可见层级，
            # 避免出现“免费额度 · 激活: …”。付费倒计时本身已明确写“会员”，不再误标为免费额度。
            for legacy_prefix in ("激活: ", "激活："):
                if status_text.startswith(legacy_prefix):
                    status_text = status_text[len(legacy_prefix):].lstrip()
                    break
            if (
                st.get("mode") != "paid"
                and not bool(st.get("credential_present"))
            ):
                status_text = "免费额度 · " + status_text
            self.activation_label.configure(text=prefix + status_text, fg=color)
            return not run_full
        except Exception:  # noqa: BLE001  激活 UI 绝不拖垮刷新
            return False

    def _render_degraded(self) -> None:
        """降级显示: 清空所有英雄提示, 标题提示需激活, 停掉"推理中"转圈猫(否则降级后一直转)。"""
        try:
            self._summary_worker_running = False
            self._summary_worker_mode = None
            self._compute_pending = False
            self._compute_status_event = "degraded"
            self._hide_pixel_cat()   # 降级 = 不再推理, 停转圈
            needs_token = bool(self._act_state.get("needs_token", True))
            self.title.configure(
                text="🔒 需激活 token" if needs_token else "🔒 激活暂不可用"
            )
            detail = str(
                self._act_state.get("text", "试用已结束 · 需激活 token")
            )
            if needs_token:
                detail += " · 点右侧 token 入口"
            self._set_header_subtitle(detail)
            self.status.configure(text=time.strftime("%H:%M:%S"))
            self._clear_values()
        except Exception:  # noqa: BLE001
            pass

    def _open_compatibility_guidance(
        self,
        _event: tk.Event[Any] | None = None,
    ) -> str:
        """Show the recovery sequence only when the user asks from a failed state."""
        existing = getattr(self, "_compatibility_dialog", None)
        if existing is not None:
            try:
                existing.deiconify()
                existing.lift()
                return "break"
            except tk.TclError:
                self._compatibility_dialog = None
        try:
            dialog = tk.Toplevel(self.root)
        except tk.TclError:
            return "break"
        self._compatibility_dialog = dialog
        dialog.title("Hero Ref · 兼容设置与恢复")
        dialog.attributes("-topmost", bool(getattr(self, "topmost_enabled", True)))
        dialog.configure(bg=PANEL, padx=16, pady=14)
        dialog.resizable(False, False)
        try:
            dialog.transient(self.root)
        except tk.TclError:
            pass

        tk.Label(
            dialog,
            text="能进游戏但不计算？按这个顺序恢复",
            bg=PANEL,
            fg=TEXT,
            font=(FONT_UI, 11, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            dialog,
            text="适用于首次使用、预加载被拒绝或本地兼容组件未接管。",
            bg=PANEL,
            fg=MUTED,
            font=(FONT_UI, 8),
            anchor="w",
        ).pack(fill="x", pady=(3, 10))

        guide = tk.Frame(dialog, bg=BG, padx=10, pady=8)
        guide.pack(fill="both", expand=True)
        for index, step in enumerate(COMPATIBILITY_GUIDANCE_STEPS, start=1):
            tk.Label(
                guide,
                text=f"{index}. {step}",
                bg=BG,
                fg=TEXT,
                font=(FONT_UI, 8),
                anchor="nw",
                justify="left",
                wraplength=500,
            ).pack(fill="x", anchor="w", pady=(0, 5))
        tk.Label(
            guide,
            text=COMPATIBILITY_GUIDANCE_FOOTER,
            bg=BG,
            fg=GOOD,
            font=(FONT_UI, 8, "bold"),
            anchor="nw",
            justify="left",
            wraplength=500,
        ).pack(fill="x", anchor="w", pady=(3, 0))

        def _close() -> None:
            try:
                dialog.destroy()
            except tk.TclError:
                pass
            self._compatibility_dialog = None

        tk.Button(
            dialog,
            text="知道了",
            command=_close,
            bg=GOOD,
            fg="#0c1320",
            activebackground=GOOD,
            activeforeground="#0c1320",
            relief="flat",
            font=(FONT_UI, 9, "bold"),
            width=9,
        ).pack(anchor="e", pady=(10, 0))
        dialog.protocol("WM_DELETE_WINDOW", _close)
        dialog.update_idletasks()
        requested_width = min(
            max(460, dialog.winfo_reqwidth()),
            max(460, dialog.winfo_screenwidth() - 32),
        )
        requested_height = min(
            dialog.winfo_reqheight(),
            max(360, dialog.winfo_screenheight() - 48),
        )
        x = max(0, (dialog.winfo_screenwidth() - requested_width) // 2)
        y = max(0, (dialog.winfo_screenheight() - requested_height) // 2)
        dialog.geometry(f"{requested_width}x{requested_height}+{x}+{y}")
        return "break"

    def _open_token_dialog(self, _event: tk.Event[Any] | None = None) -> None:
        """弹出激活 token 输入框。提交 -> 后台 activate(ui_token) -> 成功即解锁。"""
        from bidking_lab.secure.membership_ui import renewal_rule_text, renewal_available
        AhmadTkOverlay._activation_product_denied(self)
        state = getattr(self, "_act_state", None)
        if (
            isinstance(state, dict)
            and state.get("reason") in {
                "revoked",
                "device_revoked",
                "upgrade_required",
            }
        ):
            return
        existing = getattr(self, "_token_dialog", None)
        if existing is not None:
            try:
                existing.deiconify(); existing.lift(); return
            except tk.TclError:
                self._token_dialog = None
        try:
            dlg = tk.Toplevel(self.root)
        except tk.TclError:
            return
        dlg.title(
            "激活 BidKing · 网络诊断内测"
            if self.network_diagnostic_cohort
            else "激活 BidKing"
        )
        dlg.attributes("-topmost", True)
        dlg.configure(bg=PANEL, padx=14, pady=12)
        dlg.resizable(False, False)
        self._token_dialog = dlg
        if not self.network_diagnostic_cohort:
            self._add_shop_entry(dlg)
        can_renew = renewal_available(getattr(self, "_act_state", None), diagnostic=self.network_diagnostic_cohort)
        tk.Label(dlg, text="粘贴新卡，为当前会员续费" if can_renew else "粘贴你的激活 token", bg=PANEL, fg=TEXT,
                 font=(FONT_UI, 10, "bold")).pack(anchor="w")
        tk.Label(dlg, text=(
                     "已有 token 可继续在此激活；本包不提供购买或新权益开通。"
                     if self.network_diagnostic_cohort
                     else "剩余时间保留；要换卡使用，请选择下方“更换使用”。" if can_renew
                     else "试用到期 / 未激活时,在此粘贴购买的 token 解锁。"
                 ),
                 bg=PANEL, fg=MUTED, font=(FONT_UI, 8)).pack(anchor="w", pady=(2, 6))
        # 标准包显示价格与购买引导；网络诊断内测包只显示准确的 cohort 边界。
        try:
            from bidking_lab.secure.client_activate import CONTACT as _contact
        except Exception:  # noqa: BLE001
            _contact = "购买/激活请加 GitHub Issues 或project discussion"
        price_box = tk.Frame(dlg, bg=BG, padx=8, pady=6)
        price_box.pack(fill="x", pady=(0, 8))
        for column in (1, 3, 5):
            price_box.grid_columnconfigure(column, weight=1)

        def _price_row(
            row: int,
            label_text: str,
            values: tuple[str, str, str],
        ) -> None:
            """Keep each duration and separator on stable columns across all price rows."""
            tk.Label(price_box, text=label_text, bg=BG, fg=TEXT,
                     font=(FONT_UI, 8), anchor="w").grid(
                         row=row, column=0, sticky="w", pady=(2, 0), padx=(0, 4)
                     )
            for value_column, value_text in zip((1, 3, 5), values):
                tk.Label(price_box, text=value_text, bg=BG, fg=TEXT,
                         font=(FONT_UI, 8), anchor="w").grid(
                             row=row, column=value_column, sticky="w", pady=(2, 0)
                         )
            for separator_column in (2, 4):
                tk.Label(price_box, text="｜", bg=BG, fg=TEXT,
                         font=(FONT_UI, 8), anchor="center").grid(
                             row=row, column=separator_column, sticky="w", pady=(2, 0)
                         )

        if self.network_diagnostic_cohort:
            for row, line in enumerate(NETWORK_DIAGNOSTIC_DIALOG_LINES):
                tk.Label(
                    price_box,
                    text=line,
                    bg=BG,  # noqa: F821 - theme globals are rebound by apply_theme
                    fg=(
                        GOOD  # noqa: F821 - theme globals are rebound by apply_theme
                        if row < 2
                        else (WARM if row == 2 else MUTED)  # noqa: F821
                    ),
                    font=(
                        FONT_UI,
                        9 if row == 0 else 8,
                        "bold" if row in (0, 2) else "normal",
                    ),
                    anchor="w",
                ).grid(
                    row=row,
                    column=0,
                    columnspan=6,
                    sticky="ew",
                    pady=((2, 0) if row else (0, 0)),
                )
            contact_row = len(NETWORK_DIAGNOSTIC_DIALOG_LINES)
            contact_prefix = "内测支持"
        else:
            tk.Label(price_box, text="所有档位均可正常使用计算器", bg=BG, fg=GOOD,
                     font=(FONT_UI, 9, "bold"), anchor="w").grid(
                         row=0, column=0, columnspan=6, sticky="ew"
                     )
            for row_index, (label_text, values) in enumerate(
                STANDARD_SUBSCRIPTION_PRICE_ROWS,
                start=1,
            ):
                _price_row(row_index, label_text, values)
            tk.Label(price_box, text="一次性购买，不自动扣款", bg=BG, fg=MUTED,
                     font=(FONT_UI, 8), anchor="w").grid(
                         row=3, column=0, columnspan=6, sticky="ew", pady=(3, 0)
                     )
            tk.Label(price_box, text=renewal_rule_text(getattr(self, "_act_state", None)), bg=BG, fg=MUTED,
                     font=(FONT_UI, 8), anchor="w", justify="left", wraplength=560).grid(
                         row=4, column=0, columnspan=6, sticky="ew", pady=(2, 0)
                     )
            tk.Label(price_box, text="猫猫：免费10分钟 ｜ 30天12小时 ｜ 31–89天24小时 ｜ 90天及以上订阅期内不限时",
                     bg=BG, fg=GOOD,
                     font=(FONT_UI, 8, "bold"), anchor="w").grid(
                         row=5, column=0, columnspan=6, sticky="ew", pady=(2, 0)
                     )
            tk.Label(price_box, text="90/365天可用：我的记录 ｜ 365天另含：完整私有大数据与高级个性化",
                     bg=BG, fg=GOOD,
                     font=(FONT_UI, 8, "bold"), anchor="w").grid(
                         row=6, column=0, columnspan=6, sticky="ew", pady=(2, 0)
                     )
            tk.Label(price_box, text="音效、动画与互动成长将逐步扩充", bg=BG, fg=MUTED,
                     font=(FONT_UI, 8), anchor="w").grid(
                         row=7, column=0, columnspan=6, sticky="ew", pady=(2, 0)
                     )
            tk.Label(price_box, text="漫游卡可换绑8次（首次绑定不计）｜历史永久权益不变", bg=BG, fg=MUTED,
                     font=(FONT_UI, 8), anchor="w").grid(
                         row=8, column=0, columnspan=6, sticky="ew", pady=(2, 0)
                     )
            contact_row = 9
            contact_prefix = "购买/激活"
        # 联系方式用只读输入框, 方便选中复制 QQ / 群号
        contact_value = _contact
        for prefix in ("购买/激活", "购买/激活请"):
            if contact_value.startswith(prefix):
                contact_value = contact_value[len(prefix):].lstrip()
                break
        tk.Label(price_box, text=contact_prefix, bg=BG, fg=MUTED,
                 font=(FONT_UI, 8), anchor="w").grid(
                     row=contact_row, column=0, sticky="w", pady=(2, 0), padx=(0, 4)
                 )
        contact_entry = tk.Entry(price_box, bg=BG, fg=MUTED, relief="flat",
                                 font=(FONT_UI, 8), readonlybackground=BG, bd=0,
                                 highlightthickness=0)
        contact_entry.insert(0, contact_value)
        contact_entry.configure(state="readonly")
        contact_entry.grid(
            row=contact_row, column=1, columnspan=5, sticky="ew", pady=(2, 0)
        )
        entry = tk.Entry(dlg, width=42, bg=BG, fg=TEXT, insertbackground=TEXT,
                         relief="flat", font=(FONT_UI, 9))
        from bidking_lab.secure.membership_ui import (
            renewal_available, renewal_feedback, SWITCH_CONFIRMATION,
        )
        renewal_mode = renewal_available(
            getattr(self, "_act_state", None),
            diagnostic=bool(getattr(self, "network_diagnostic_cohort", False)),
        )
        entry.pack(fill="x")
        entry.focus_set()
        status = tk.Label(dlg, text="", bg=PANEL, fg=MUTED, font=(FONT_UI, 8), anchor="w",
                          justify="left", wraplength=560)
        status.pack(fill="x", pady=(6, 0))
        btns = tk.Frame(dlg, bg=PANEL)
        btns.pack(fill="x", pady=(10, 0))
        operation = {
            "generation": 0, "busy": False, "clearing": False,
            "feedback": False, "roaming_view": ("", MUTED),
        }
        recovery_message = "本机清除未完成，请关闭软件并重启；仍异常请导出诊断联系支持"

        def _close() -> None:
            operation["generation"] += 1
            if getattr(self, "_activation_clear_view_refresh", None) is _refresh_clear_result:
                self._activation_clear_view_refresh = None
            if getattr(self, "_activation_roaming_view_refresh", None) is _refresh_roaming_result:
                self._activation_roaming_view_refresh = None
            try:
                dlg.destroy()
            except tk.TclError:
                pass
            if getattr(self, "_token_dialog", None) is dlg:
                self._token_dialog = None

        ok_btn = tk.Button(btns, text="续费" if renewal_mode else "激活", bg=GOOD, fg="#0c1320", relief="flat",
                           font=(FONT_UI, 9, "bold"), width=8)
        ok_btn.pack(side="right")
        switch_btn = tk.Button(btns, text="更换使用", bg=BG, fg=TEXT, relief="flat",
                               font=(FONT_UI, 9), width=9)
        if renewal_mode:
            switch_btn.pack(side="right", padx=(0, 8))
        tk.Button(btns, text="关闭" if renewal_mode else "取消", command=_close, bg=BG, fg=MUTED, relief="flat",
                  font=(FONT_UI, 9), width=6).pack(side="right", padx=(0, 8))
        # 换绑/清除: 清本机已存 token + 缓存(+ 本进程 env token), 回干净态后可粘贴新 token。
        # 修"贴了错 token 后一直绑定失败"(旧 token 残留在文件/env 盖过新贴的)。
        clear_btn = tk.Button(btns, text="换绑/清除", bg=BG, fg=MUTED, relief="flat",
                              font=(FONT_UI, 9), width=9)
        clear_btn.pack(side="left")

        def _can_submit() -> bool:
            shared_clear = getattr(self, "_activation_clear_operation", None)
            if shared_clear is not None and shared_clear["state"] != "complete":
                return False
            if shared_clear is None and getattr(self, "_activation_clear_requires_restart", False):
                return False
            try:
                from bidking_lab.secure.client_activate import activation_submission_preflight
                return activation_submission_preflight(entry.get())
            except Exception:  # noqa: BLE001 - eligibility must fail closed
                return False

        def _sync_activation_button(_event=None) -> bool:
            eligible = _can_submit()
            shared_clear = getattr(self, "_activation_clear_operation", None)
            if shared_clear is not None:
                self._activation_clear_requires_restart = shared_clear["state"] != "complete"
            ok_btn.configure(state="normal" if eligible and not operation["busy"] else "disabled")
            switch_btn.configure(state="normal" if eligible and not operation["busy"] else "disabled")
            if not eligible and not operation["busy"]:
                pending = shared_clear is not None and shared_clear["state"] == "pending"
                status.configure(text="清除中…" if pending else recovery_message, fg=MUTED if pending else BAD)
            return eligible

        def _refresh_roaming_result(current_state=None) -> None:
            # A state tick owns only an idle current view. Local submission/clear
            # feedback keeps its slot until the user closes or performs an action.
            if (
                getattr(self, "_token_dialog", None) is not dlg
                or operation["busy"] or operation["clearing"] or operation["feedback"]
                or not _can_submit()
            ):
                return
            current_state = current_state if current_state is not None else getattr(self, "_act_state", None)
            if type(current_state) is not dict:
                return
            meta = current_state.get("roaming_rebinds")
            text, color = "", MUTED
            if current_state.get("roaming_rebind_limit_reached") is True:
                text, color = "换绑次数已用完，请在当前绑定的电脑使用", BAD
            elif current_state.get("run_full") and current_state.get("mode") == "paid" and type(meta) is dict:
                text = f"上次验证：换绑剩余 {meta['remaining']}/8 次"
            if operation["roaming_view"] != (text, color):
                status.configure(text=text, fg=color)
                operation["roaming_view"] = (text, color)

        def _refresh_clear_result(shared_clear) -> None:
            if shared_clear is not getattr(self, "_activation_clear_operation", None):
                return
            if shared_clear["state"] == "pending":
                _sync_activation_button()
                return
            # A reopened view may consume completion; a later local submission
            # keeps its own status. A clear worker never owns a closed view.
            if not operation["clearing"] and operation["generation"] != 0:
                _sync_activation_button()
                return
            operation["busy"] = False
            operation["clearing"] = False
            operation["feedback"] = True
            try:
                complete = shared_clear["state"] == "complete"
                if complete:
                    msg = "已清除本机 token,可粘贴新 token 激活"
                    if shared_clear["env_was_set"]:
                        msg += "(检测到环境变量 token,已清本进程;若仍失败请重启)"
                    status.configure(text=msg, fg=GOOD)
                    if operation.get("clear_operation") is shared_clear:
                        entry.delete(0, "end")
                        entry.focus_set()
                else:
                    status.configure(text=recovery_message, fg=BAD)
                clear_btn.configure(state="normal")
                _sync_activation_button()
            except tk.TclError:
                pass

        def _submit(action: str | None = None) -> None:
            if operation["busy"] or not _sync_activation_button():
                return
            operation["feedback"] = True
            token = entry.get()
            if not token:
                status.configure(text="请先粘贴 token", fg=BAD); return
            try:
                from bidking_lab.secure.client_activate import (
                    is_single_activation_token_input,
                )
            except Exception:  # noqa: BLE001 - local validation must fail closed
                status.configure(  # noqa: F821 - theme global rebound by apply_theme
                    text="激活组件暂不可用，请重启后重试",
                    fg=BAD,  # noqa: F821 - theme global rebound by apply_theme
                )
                return
            if not is_single_activation_token_input(token):
                status.configure(
                    text="粘贴内容不是单一token，请重新复制纯token",
                    fg=BAD,  # noqa: F821 - theme global rebound by apply_theme
                )
                return
            action = action or ("renew" if renewal_mode else "activate")
            if action == "switch":
                from tkinter import messagebox
                if not messagebox.askyesno("更换本机 token", SWITCH_CONFIRMATION, parent=dlg):
                    return
            operation["generation"] += 1
            generation = operation["generation"]
            operation["busy"] = True
            ok_btn.configure(state="disabled")
            switch_btn.configure(state="disabled")
            if action == "renew":
                clear_btn.configure(state="disabled")
                entry.configure(state="disabled")
            status.configure(text="续费确认中…" if action == "renew" else "激活中…", fg=MUTED)

            def _do() -> None:
                ok = False
                msg = "激活失败"
                pending_confirmation = False
                try:
                    from bidking_lab.secure import enforce
                    res = (enforce.redeem_membership_card(card_token=token)
                           if action == "renew" else enforce.refresh_activation(ui_token=token))
                    self._act_ready = True
                    if action == "renew":
                        feedback = renewal_feedback(res)
                        ok, msg = feedback.succeeded, feedback.text
                        pending_confirmation = feedback.confirmation_pending
                    else:
                        ok = bool(getattr(res, "activated", False)) and getattr(res, "mode", "") == "paid"
                        state = enforce.activation_state(res)
                        msg = "激活成功!已解锁" if ok else str(
                            state.get("text", "激活未通过 · 请稍后重试或联系支持")
                        )
                        if ok and type(state.get("roaming_rebinds")) is dict:
                            msg = f"激活成功 · 换绑剩余 {state['roaming_rebinds']['remaining']}/8 次"
                except Exception:  # noqa: BLE001
                    # 对话框经常被截图发给支持；异常类型和正文都可能泄露本机环境。
                    if action == "renew":
                        pending_confirmation = True
                        msg = "续费结果尚未确认，请用同一张卡重试；不要重复购买。"
                    else:
                        msg = "激活异常 · 请稍后重试或运行激活网络诊断"

                def _after() -> None:
                    if generation != operation["generation"]:
                        return
                    operation["busy"] = False
                    try:
                        if action == "renew":
                            entry.configure(state="normal")
                        if not _sync_activation_button():
                            return
                        clear_btn.configure(state="normal")
                        status.configure(text=msg, fg=(GOOD if ok else WARN if pending_confirmation else BAD))
                        if ok and action == "renew" and entry.get() == token:
                            entry.delete(0, "end")
                        if ok and action != "renew":
                            self.root.after(
                                900,
                                lambda: _close() if (
                                    generation == operation["generation"] and _can_submit()
                                ) else None,
                            )
                    except tk.TclError:
                        pass
                try:
                    self.root.after(0, _after)
                except tk.TclError:
                    pass

            threading.Thread(target=_do, daemon=True, name="hero-ref-token").start()

        def _do_clear() -> None:
            if operation["clearing"]:
                return
            operation["generation"] += 1
            operation["busy"] = True
            operation["clearing"] = True
            operation["feedback"] = True
            shared_clear = {"state": "pending", "env_was_set": False}
            operation["clear_operation"] = shared_clear
            self._activation_clear_operation = shared_clear
            self._activation_clear_requires_restart = True
            clear_btn.configure(state="disabled")
            ok_btn.configure(state="disabled")
            status.configure(text="清除中…", fg=MUTED)

            def _work() -> None:
                info = {"cleared": False, "env_was_set": False}
                try:
                    from bidking_lab.secure import enforce
                    info = enforce.clear_activation()
                    self._act_ready = True
                except Exception:  # noqa: BLE001
                    pass
                complete = (
                    type(info) is dict
                    and info.get("cleared") is True
                    and info.get("can_activate") is True
                )
                if complete:
                    try:
                        from bidking_lab.secure.client_activate import activation_submission_preflight
                        complete = activation_submission_preflight("")
                    except Exception:  # noqa: BLE001 - missing eligibility stays closed
                        complete = False
                # Complete only this operation's record, independently of any
                # dialog generation. A newer clear owns a different record.
                shared_clear["env_was_set"] = type(info) is dict and info.get("env_was_set") is True
                shared_clear["state"] = "complete" if complete else "failed"

                def _after() -> None:
                    current_clear = getattr(self, "_activation_clear_operation", None)
                    refresh = getattr(self, "_activation_clear_view_refresh", None)
                    if current_clear is not None and refresh is not None:
                        # Even an older worker can change backend eligibility;
                        # repaint only the current record, never its old result.
                        refresh(current_clear)
                try:
                    self.root.after(0, _after)
                except tk.TclError:
                    pass

            threading.Thread(target=_work, daemon=True, name="hero-ref-token-clear").start()

        ok_btn.configure(command=_submit)
        switch_btn.configure(command=lambda: _submit("switch"))
        clear_btn.configure(command=_do_clear)
        entry.bind("<Return>", lambda _e: _submit())
        entry.bind("<KeyRelease>", _sync_activation_button)
        self._activation_clear_view_refresh = _refresh_clear_result
        self._activation_roaming_view_refresh = _refresh_roaming_result
        shared_clear = getattr(self, "_activation_clear_operation", None)
        if shared_clear is not None and shared_clear["state"] != "pending":
            _refresh_clear_result(shared_clear)
        _sync_activation_button()
        _refresh_roaming_result()
        dlg.protocol("WM_DELETE_WINDOW", _close)
        try:
            dlg.update_idletasks()
            x = self.root.winfo_rootx() + 30
            y = self.root.winfo_rooty() + 60
            width = max(1, int(dlg.winfo_reqwidth()))
            height = max(1, int(dlg.winfo_reqheight()))
            left, top, right, bottom = _monitor_work_area_for_point(self.root, x, y)
            x, y = _clamp_rect_into_workarea(
                x, y, width, height, left, top, right, bottom
            )
            dlg.geometry(f"+{x}+{y}")
            dlg.update_idletasks()
            outer_left, outer_top, outer_right, outer_bottom = _window_outer_rect(dlg)
            outer_width = outer_right - outer_left
            outer_height = outer_bottom - outer_top
            left, top, right, bottom = _monitor_work_area_for_point(
                self.root, outer_left, outer_top
            )
            clamped_left, clamped_top = _clamp_rect_into_workarea(
                outer_left,
                outer_top,
                outer_width,
                outer_height,
                left,
                top,
                right,
                bottom,
            )
            if clamped_left != outer_left or clamped_top != outer_top:
                dlg.geometry(
                    f"+{int(dlg.winfo_x()) + clamped_left - outer_left}"
                    f"+{int(dlg.winfo_y()) + clamped_top - outer_top}"
                )
        except tk.TclError:
            pass

    def _notify_watched_monitor_exit(self) -> None:
        """The overlay auto-closes when its capture monitor exits (--exit-when-pid-exits). Without a
        word, that reads as a mysterious black-window flash (group-reported: '开了就关 + 任务栏图标黑 +
        stderr 空'). Pop a BLOCKING native box BEFORE teardown so the user learns it was the monitor
        that quit (not a UI crash) and where to look — no matter WHEN the monitor died (the ps1 launch
        guard only checks once at +4s). One-shot; opt-out via HERO_REF_NO_EXIT_DIALOG=1; skipped under
        pytest so headless tests never block on a modal dialog. Never raises during teardown."""
        if getattr(self, "_monitor_exit_notified", False):
            return
        self._monitor_exit_notified = True
        if os.environ.get("HERO_REF_NO_EXIT_DIALOG") == "1" or os.environ.get("PYTEST_CURRENT_TEST"):
            return
        if sys.platform != "win32":
            return
        try:
            msg = (
                "后台对局数据读取已停止，计算器将自动关闭。\n\n"
                "请完全退出游戏，把程序文件夹加入杀毒软件白名单，再重新启动计算器；"
                "等悬浮窗出现后再启动游戏。\n\n"
                "仍然失败时，请运行「诊断工具\\收集诊断.bat」并把结果私发作者。"
            )
            import ctypes

            # MB_OK | MB_ICONWARNING | MB_SETFOREGROUND | MB_TOPMOST
            ctypes.windll.user32.MessageBoxW(0, msg, "BidKing Live · 数据读取已停止", 0x00040030)
        except Exception:  # noqa: BLE001 - a diagnostic popup must never block/raise during teardown
            pass

    def _poll_game_exit_group(self) -> None:
        if bool(getattr(self, "_exit_cleanup_done", False)):
            return
        request = _game_exit_group_request(
            self.snapshot_path.parent, getattr(self, "_group_exit_launch_id", ""),
            getattr(self, "_group_exit_monitor_identities", {}),
        )
        if request is None:
            self.root.after(100, self._poll_game_exit_group)
            return
        # The monitor retains its original non-daemon settlement finalizer.
        # Normal game exit must not enter the unexpected-monitor modal path.
        self.keep_monitor_on_close = True
        self._group_exit_cleanup_errors = []
        for name, action in (
            ("dispatch", lambda: self._clear_compute_pending("bound_game_exited")),
            ("preferences", self._save_ui_prefs_if_enabled),
            ("pet", self._destroy_pet_companion),
        ):
            try:
                action()
            except Exception:
                self._group_exit_cleanup_errors.append(name)
                if name == "pools":
                    self._group_exit_pools_reaped = False
        self._finish_ui_exit()

    def _finish_ui_exit(self) -> None:
        """Retry unfinished pools on the original Tk owner, never a finalizer thread."""
        try:
            self._run_exit_cleanup()
        except Exception:
            self._group_exit_pools_reaped = False
        self._write_game_exit_ack()
        if getattr(self, "_group_exit_pools_reaped", False) is True:
            self.root.destroy()
        else:
            self.root.after(200, self._finish_ui_exit)

    def _write_game_exit_ack(self) -> None:
        try:
            identities = getattr(self, "_group_exit_monitor_identities", {})
            launch_id = getattr(self, "_group_exit_launch_id", "")
            if len(identities) != 1 or not launch_id:
                return
            monitor_pid, monitor_started = next(iter(identities.items()))
            import psutil
            acknowledgment = {
                "group_exit_schema_version": 1, "launch_id": launch_id,
                "monitor_pid": monitor_pid,
                "monitor_started_at_unix_ms": monitor_started,
                "ui_pid": os.getpid(), "ui_started_at": psutil.Process().create_time(),
                "pools_reaped": getattr(self, "_group_exit_pools_reaped", False) is True,
                "cleanup_errors": getattr(self, "_group_exit_cleanup_errors", []),
            }
            path = self.snapshot_path.parent / "lifecycle_ui_exit.json"
            # Windows readers can briefly deny replacement. Use the existing
            # bounded retry writer; never publish or accept a partial ack.
            _atomic_write_json(path, acknowledgment)
        except Exception as exc:
            # Keep shutdown non-modal, but retain a fixed-shape diagnostic if
            # publication still fails. Exception text may contain private paths.
            category = "permission" if isinstance(exc, PermissionError) else (
                "io" if isinstance(exc, OSError) else "other"
            )
            errno = getattr(exc, "errno", 0)
            winerror = getattr(exc, "winerror", 0)
            errno = errno if type(errno) is int else 0
            winerror = winerror if type(winerror) is int else 0
            try:
                print(
                    f"[lifecycle] ui_exit_ack_write_failed category={category} "
                    f"errno={errno} winerror={winerror}",
                    file=sys.stderr, flush=True,
                )
            except Exception:
                pass

    def refresh(self) -> None:
        refresh_started = time.perf_counter()
        heartbeat_now = time.monotonic()
        heartbeat_gap = heartbeat_now - float(
            getattr(self, "_last_ui_heartbeat_at", heartbeat_now)
        )
        self._last_ui_heartbeat_at = heartbeat_now
        self._last_ui_stall_bucket = 0
        self._poll_compute_status()
        if self._ui_interaction_busy():
            try:
                self._sync_compute_indicator()
            finally:
                self._last_ui_heartbeat_at = time.monotonic()
                if not bool(getattr(self, "_exit_cleanup_done", False)):
                    self.root.after(self.interval_ms, self.refresh)
            return
        self._drain_ref_engine_liveness_results()
        # A child failure has a retry deadline; revisit it from the regular Tk tick, while
        # `_start_ref_engine_liveness_warmup` itself keeps pool work off this thread.
        if getattr(self, "_ref_engine_liveness_state", None) == "failed":
            self._start_ref_engine_liveness_warmup()
        self._drain_live_summary_results()
        self._drain_manual_summary_results()
        if heartbeat_gap >= UI_STALL_SECONDS:
            self._record_ui_health(
                {
                    "logged_at": time.time(),
                    "event": "ui_event_loop_recovered",
                    "gap_seconds": round(heartbeat_gap, 3),
                    "snapshot_path": str(self.snapshot_path),
                    "diagnostic_profile": getattr(
                        self,
                        "diagnostic_profile",
                        DEFAULT_DIAGNOSTIC_PROFILE,
                    ),
                }
            )
        should_reschedule = True
        try:
            watched_pid_exited_fn = getattr(
                self,
                "_watched_pid_exited_fn",
                _watched_pid_exited,
            )
            if self.exit_when_pids and watched_pid_exited_fn(self.exit_when_pids):
                if _game_exit_group_request(
                    self.snapshot_path.parent, getattr(self, "_group_exit_launch_id", ""),
                    getattr(self, "_group_exit_monitor_identities", {}),
                ) is not None:
                    should_reschedule = False
                    self._poll_game_exit_group()
                    return
                should_reschedule = False
                self._clear_compute_pending("watched_monitor_exit")
                self._record_ui_runtime_status("watched_monitor_exit")
                # 抓包 monitor 退出 → overlay 随之自关(--exit-when-pid-exits)。不提示的话群友只看到
                # "黑窗一闪就没"(窗口建了还没绘就 destroy), 无从排查。关窗前弹一个说明框讲清"是 monitor
                # 退了、多半被杀软拦截、去查 monitor.stderr.log", 无论 monitor 何时被杀都有解释(ps1 存活
                # 守护只在 +4s 查一次, 错过就没提示)。
                self._notify_watched_monitor_exit()
                self._save_ui_prefs_if_enabled()
                self._destroy_pet_companion()
                self._finish_ui_exit()
                return
            # 激活强制(0.2.2 付费版): 每 tick 刷新倒计时标签;未激活/试用到期 → 清空所有英雄提示 +
            # 显示"需激活 token", 并跳过本次计算渲染(monitor 侧也已 gate 推理, 双保险不计算/不泄露)。
            if self._update_activation_ui():
                self._clear_compute_pending("activation_denied")
                self._render_degraded()
                self._record_ui_runtime_status("gated_needs_activation")
                return
            capture_status = self._capture_status()
            capture_signature: tuple[Any, ...] = ()
            snapshot = _read_json(self.snapshot_path)
            if not isinstance(snapshot, dict):
                snapshot = None
            # _read_json returns {} when the file is missing or unparseable; treat
            # an empty payload as "no snapshot" so the refresh falls through to
            # render_missing instead of starting a worker on an empty dict.
            if snapshot is not None and not snapshot:
                snapshot = None
            try:
                signature = self._snapshot_signature(snapshot)
            except TypeError:
                # Compatibility for bounded tests/integrations that replace the
                # historical zero-argument signature probe. Production uses the
                # loaded snapshot above and therefore avoids a second JSON read.
                signature = self._snapshot_signature()
            if signature == (0, 0):
                capture_signature = _capture_status_signature(capture_status)
            capture_changed = (
                signature == (0, 0)
                and capture_signature != self._last_capture_status_signature
            )
            inference_changed = (
                snapshot is not None
                and (
                    signature != self._last_inference_signature
                    or self._needs_stale_refresh()
                )
            )
            if snapshot is not None and not inference_changed:
                bid_signature = self._live_bid_signature(snapshot)
                if bid_signature != self._last_bid_signature:
                    self._last_bid_signature = bid_signature
                    if self._apply_bid_only_live_update(snapshot):
                        self._last_signature = signature
                        self._record_ui_runtime_status(
                            "bid_only_refresh",
                            snapshot_signature=signature,
                            capture_status=capture_status,
                        )
                    else:
                        inference_changed = True
            elif snapshot is None and (capture_changed or signature == (0, 0)):
                inference_changed = False
            if inference_changed and snapshot is not None:
                self._set_compute_pending("snapshot_detected")
                self._last_inference_signature = signature
                self._last_bid_signature = self._live_bid_signature(snapshot)
                self._last_capture_status_signature = None
                # Ref-input memoize: if the Hero Ref engine's actual inputs
                # (public/skill/action/inventory facts + minimap + structured
                # inputs + context) match the last run, reuse its summary instead
                # of re-running a ~0.1-1s+ computation. The monitor-side
                # inference_signature changes per frame, but ref output is often
                # unchanged across consecutive frames in the same round — observed
                # ~11 identical-output re-runs in a 3-game aisha session. Skip
                # the cache when stale refresh is forced or phase is settled
                # (settled is authoritative truth, must recompute inline).
                stale_refresh_identity = self._stale_refresh_state_identity(snapshot)
                # Consuming the invalidation stops age-only ticks, not existing
                # bounded failure recovery. A retry must still bypass old ref cache.
                force_refresh = self._needs_stale_refresh() or stale_refresh_identity is not None
                phase_now = self._summary_phase(snapshot)
                ref_sig: tuple[Any, ...] | None = None
                if not force_refresh and phase_now != "settled":
                    try:
                        ref_sig = ref_input_signature_from_snapshot(snapshot)
                    except Exception:  # noqa: BLE001 - memoize must never block ref
                        ref_sig = None
                if (
                    ref_sig is not None
                    and ref_sig == self._ref_cache_signature
                    and isinstance(self._ref_cache_summary, dict)
                    and isinstance(self._ref_cache_snapshot, dict)
                ):
                    cached_summary = dict(self._ref_cache_summary)
                    # Refresh display-only fields (current highest bid, age) from
                    # the fresh snapshot so the UI stays live without recomputing.
                    cached_summary = self._merge_cached_summary_with_fresh_snapshot(
                        cached_summary, snapshot
                    )
                    self._mark_summary_performance(cached_summary, "refresh_total_ms", refresh_started)
                    cached_summary.setdefault("diagnostics", {})
                    cached_summary.setdefault("performance", {})
                    cached_summary["performance"]["ref_cache_hit"] = True
                    self._last_signature = signature
                    self._apply_live_summary(snapshot, cached_summary)
                    self._clear_compute_pending("summary_cache_hit", rendered=True)
                    self._record_ui_runtime_status(
                        "summary_cache_hit",
                        snapshot_signature=signature,
                        capture_status=capture_status,
                    )
                elif self._start_live_summary_worker(
                    snapshot=snapshot,
                    signature=signature,
                    started_at=refresh_started,
                ):
                    # Do not consume a transition merely because the same fresh
                    # input is still in flight. Its result may predate the age gate.
                    if stale_refresh_identity is not None and (
                        getattr(self, "_summary_worker_stale_refresh_identity", None)
                        == stale_refresh_identity
                    ):
                        self._stale_refresh_requested_identity = stale_refresh_identity
                    self._record_ui_runtime_status(
                        "summary_worker_started",
                        snapshot_signature=signature,
                        capture_status=capture_status,
                    )
                else:
                    summary = summarize_snapshot(snapshot, snapshot_path=self.snapshot_path)
                    self._stale_refresh_requested_identity = stale_refresh_identity
                    self._record_completed_stale_refresh(summary, stale_refresh_identity)
                    self._mark_summary_performance(summary, "refresh_total_ms", refresh_started)
                    self._last_signature = signature
                    self._apply_live_summary(snapshot, summary)
                    self._clear_compute_pending("summary_applied", rendered=True)
                    self._record_ui_runtime_status(
                        "summary_applied",
                        snapshot_signature=signature,
                        capture_status=capture_status,
                    )
            elif snapshot is None:
                self._last_capture_status_signature = capture_signature
                if not self._manual_active and not getattr(self, "_manual_edit_enabled", False):
                    self.render_missing("等待 latest_snapshot.json")
                self._record_ui_runtime_status(
                    "waiting_for_snapshot",
                    snapshot_signature=signature,
                    capture_status=capture_status,
                )
            else:
                self._record_ui_runtime_status(
                    "idle_no_change",
                    snapshot_signature=signature,
                    capture_status=capture_status,
                )
            self._sync_game_launch_candidate(capture_status)
            self._sync_compute_indicator()
        finally:
            self._last_ui_heartbeat_at = time.monotonic()
            if should_reschedule and not bool(getattr(self, "_exit_cleanup_done", False)):
                self.root.after(self.interval_ms, self.refresh)

    def render_missing(self, message: str) -> None:
        capture_status = self._capture_status()
        diagnostics = _capture_wait_diagnostics(capture_status)
        self._game_launch_candidate_context = True
        self.title.configure(text="Hero Ref")
        self._set_header_subtitle(diagnostics.get("subtitle") or message)
        self.status.configure(text=time.strftime("%H:%M:%S"))
        self._render_flags(diagnostics.get("flags") or [{"label": "无实时数据", "level": "watch", "detail": ""}])
        self._set_settlement_button_state({})
        self._clear_values()
        next_hint = diagnostics.get("next_hint") or diagnostics.get("state") or diagnostics.get("source")
        self._apply_action_cell(
            diagnostics.get("action"),
            next_step=next_hint,
            limit=36,
            compatibility_guidance=diagnostics.get("state")
            in {
                "wss_preload_access_denied",
                "wss_local_intercept_missing",
            },
        )
        self._set_label(self.action_rows["最近"], diagnostics.get("recent"), limit=20)
        self._set_candidate_autofit("-")
        self._sync_game_launch_candidate(capture_status)
        if getattr(self, "details_composition_view_enabled", False):
            self._set_label(
                self.evidence_rows["状态与来源"],
                diagnostics.get("state"),
                limit=42,
            )
            self._set_label(
                self.evidence_rows["输入"],
                diagnostics.get("detail"),
                limit=52,
            )
            self._set_label(
                self.evidence_rows["诊断"],
                diagnostics.get("note") or diagnostics.get("recent"),
                limit=64,
            )
            self._set_label(self.detail_rows["轮廓值"], "未满足完整轮廓条件", limit=34)
            self._set_label(self.detail_rows["结算"], "未结算", limit=38)
            self._render_detail_composition(
                {
                    "quality_composition": {
                        "status": "unavailable",
                        "reason": "等待实时估值",
                    }
                }
            )
        else:
            self._set_label(self.evidence_rows["匹配"], diagnostics.get("state"), limit=28)
            self._set_label(self.evidence_rows["密度"], diagnostics.get("detail"), limit=34)
            self._set_label(self.evidence_rows["诊断"], diagnostics.get("recent"), limit=28)
            self._set_label(self.detail_rows["外援"], "waiting", limit=18)
            self._set_label(self.detail_rows["备注"], diagnostics.get("note"), limit=42)
        self._render_minimap({})
        self._record_summary_diagnostic(
            {
                "status": "missing_snapshot",
                "evidence": {
                    "diagnostics": diagnostics.get("recent"),
                    "ref_notes": diagnostics.get("note"),
                },
                "flags": diagnostics.get("flags") or [],
                "stale": {"reason": "missing_snapshot"},
            },
            render_mode="missing",
        )
        # 占位/等待态(还没进对局)也贴合一次窗口高度: init 的 _settle_initial_layout(after_idle)
        # 测在渲染/字体缩放 settle 之前 → 底部留空(群友"等待游戏连接"时就有空位, 非仅真实对局态)。
        # 首次 render_missing settle 后按同一路径重贴合一次(=手填往返那次)。一次性、仅 mini、全防御。
        if (
            not getattr(self, "details_expanded", False)
            and not getattr(self, "_did_settle_missing", False)
        ):
            self._did_settle_missing = True
            root = getattr(self, "root", None)
            if root is not None:
                try:
                    root.after_idle(self._settle_initial_layout)
                except (tk.TclError, AttributeError):
                    pass

    def render_standby(self, data: dict[str, Any]) -> None:
        self._quote_display_data = data
        self._quote_pending_application = None
        refresh_quote_preview = getattr(self, "_quote_preview_refresh", None)
        if callable(refresh_quote_preview):
            refresh_quote_preview()
        capture_status = self._capture_status()
        self._game_launch_candidate_context = True
        stale = data.get("stale") if isinstance(data.get("stale"), dict) else {}
        age = stale.get("age_seconds")
        age_text = f"{int(age)}s" if isinstance(age, (int, float)) else "unknown"
        reason = _text(stale.get("reason"), "stale_snapshot")
        title = "Hero Ref · 新局等待" if reason == "session_ahead" else "Hero Ref · 待机"
        self.title.configure(text=title)
        self._set_header_subtitle(f"snapshot age={age_text}; 等待首个实时估价")
        if self._is_settlement_summary(data):
            self.status.configure(text="已结算")
        elif getattr(self, "_ref_engine_liveness_state", None) == "failed":
            self.status.configure(text="估价暂不可用")
        else:
            self.status.configure(text=_text(data.get("updated_at_text"), "--:--"))
        self._render_flags(data.get("flags") or [{"label": "待机", "level": "neutral", "detail": ""}])
        self._set_settlement_button_state(data)
        self._clear_values()
        self._apply_action_cell("等待新局", next_step="standby", limit=36)
        self._set_label(self.action_rows["最近"], "-", limit=18)
        self._set_candidate_autofit("-")
        self._sync_game_launch_candidate(capture_status)
        self._update_detail_rows(data)
        self._render_minimap(data.get("minimap") or {})
        self._record_summary_diagnostic(data, render_mode="standby")

    def _apply_settlement_estimate_override(self, data: dict[str, Any]) -> None:
        """Make the settled 估价 card show the last on-screen pre-settlement 参考, not a re-run.

        At settlement the snapshot's minimap/constraints are back-filled with the full truth, so the
        panel server can only re-run the engine on a clone that STRIPS them -- which loses the live
        evidence and diverges low (toward the conservative tier; user-flagged 2403/2407). This caches
        the 参考 (balanced) of each pre-settlement round and, at settlement, rewrites the 估价
        (conservative) and 差值 (aggressive = 结算 - 估价) to that real recommendation.
        """
        session_id = _strict_quote_summary_session_id(data)
        if session_id is None:
            self._last_pre_settlement_estimate = None
            self._last_pre_settlement_display_quote = None
            return
        is_settlement = self._is_settlement_summary(data)
        ref = data.get("reference")
        if not is_settlement:
            display_quote = _strict_pre_settlement_display_quote(data)
            cached_display = getattr(
                self,
                "_last_pre_settlement_display_quote",
                None,
            )
            if display_quote is not None:
                self._last_pre_settlement_display_quote = display_quote
            elif cached_display:
                self._last_pre_settlement_display_quote = None
            balanced = _parse_money(ref.get("balanced")) if isinstance(ref, dict) else None
            raw_receipt = data.get("quote_receipt")
            raw_identity = data.get("quote_identity")
            if (
                session_id
                and balanced is not None
                and math.isfinite(balanced)
                and _valid_quote_receipt(raw_receipt, summary=data)
            ):
                self._last_pre_settlement_estimate = (
                    session_id,
                    balanced,
                    copy.deepcopy(raw_receipt),
                    copy.deepcopy(raw_identity),
                )
            elif (
                self._last_pre_settlement_estimate
                and self._last_pre_settlement_estimate[0] == session_id
            ):
                self._last_pre_settlement_estimate = None
            return
        if not isinstance(ref, dict):
            return
        cached = self._last_pre_settlement_estimate
        cached_receipt: dict[str, Any] = {}
        cached_identity: dict[str, Any] = {}
        receipt_cache_valid = False
        if cached and cached[0] == session_id:
            cached_receipt = cached[2] if len(cached) >= 3 else {}
            cached_identity = cached[3] if len(cached) >= 4 else {}
            receipt_cache_valid = bool(
                _valid_quote_receipt(cached_receipt)
                and type(cached_identity) is dict  # noqa: E721
                and set(cached_identity) == _QUOTE_IDENTITY_KEYS
                and cached_identity.get("schema") == "hero_ref_quote_identity_v1"
                and not any(
                    cached_identity.get(key) != cached_receipt.get(key)
                    for key in (
                        "source",
                        "input_fingerprint_scope",
                        "input_fingerprint",
                        "result_identity",
                    )
                )
                and _parse_money(cached_receipt.get("balanced")) == cached[1]
            )
            if not receipt_cache_valid:
                self._last_pre_settlement_estimate = None

        if receipt_cache_valid:
            estimate = cached[1]
            decision_range = cached_receipt.get("decision_range")
            value_range = cached_receipt.get("value_range")
            receipt = copy.deepcopy(cached_receipt)
            receipt["settlement_review_applied"] = True
            data["quote_receipt"] = receipt
            data["quote_identity"] = copy.deepcopy(cached_identity)
            estimate_state = "cached_receipt"
        else:
            raw_receipt = data.get("quote_receipt")
            raw_identity = data.get("quote_identity")
            if (
                type(raw_receipt) is not dict  # noqa: E721
                or raw_receipt
                or type(raw_identity) is not dict  # noqa: E721
                or raw_identity
            ):
                return
            display_cached = getattr(
                self,
                "_last_pre_settlement_display_quote",
                None,
            )
            if not display_cached or display_cached[0] != session_id:
                return
            estimate = display_cached[1]
            decision_range = display_cached[2]
            value_range = display_cached[3]
            estimate_state = "cached_display"

        ref["conservative"] = _money(estimate)
        settled_total = _parse_money(ref.get("balanced"))
        if settled_total is not None:
            delta = settled_total - estimate
            ref["aggressive"] = f"{'+' if delta >= 0 else '-'}{_money(abs(delta))}"
        diagnostics = data.get("diagnostics")
        performance = (
            diagnostics.get("performance")
            if isinstance(diagnostics, dict)
            else None
        )
        if isinstance(performance, dict):
            performance["settlement_estimate_state"] = estimate_state
        if isinstance(decision_range, str) and decision_range:
            ref["decision_range"] = decision_range
        if isinstance(value_range, str) and value_range:
            ref["raw_value_range"] = value_range
            ref["total_value_range"] = value_range
        note = ref.get("note")
        if isinstance(note, str):
            _, separator, suffix = note.partition(";")
            ref["note"] = (
                "Settlement review: top cards show the last on-screen Hero Ref "
                "estimate, final total, and final-minus-estimate delta."
                + (f";{suffix}" if separator else "")
            )

    def render(self, data: dict[str, Any]) -> None:
        self._game_launch_candidate_context = False
        self._clear_game_launch_candidate_binding()
        if not bool(getattr(self, "_compute_pending", False)) and not bool(
            getattr(self, "_summary_worker_running", False)
        ):
            self._hide_pixel_cat()
        self._apply_settlement_estimate_override(data)
        context = data.get("context") or {}
        reference = data.get("reference") or {}
        red = data.get("red") or {}
        evidence = data.get("evidence") or {}
        phase = _text(context.get("phase"), "?")
        is_settled = phase == "settled"
        continuity = (
            data.get("continuity")
            if type(data.get("continuity")) is dict
            else {}
        )
        verified_continuity = continuity.get("reason") in {
            "quality_composition_conflict",
            "quality_q6_next_round_pending",
            "ref_engine_transient_error",
        }
        paired_quality_keys = _current_summary_fresh_paired_quality_keys(data)
        compact_composition = detail_composition_view(data)
        pending_composition = detail_composition_view({})

        def visible_quality(key: str):
            canonical = compact_composition.quality(key)
            owner = (
                compact_composition
                if compact_composition.status == "conflict"
                or key in paired_quality_keys
                or (
                    verified_continuity
                    and canonical.status in {"ready", "exact"}
                    and bool(canonical.candidates)
                )
                else pending_composition
            )
            return owner.quality(key)

        compact_q6 = visible_quality("q6")
        count_facts = (
            red.get("quality_count_facts")
            if type(red.get("quality_count_facts")) is dict
            else {}
        )

        def _trusted_count_fact(key: str) -> int | None:
            raw_fact = dict.get(count_facts, key)
            if type(raw_fact) is not dict:  # noqa: E721 - JSON display boundary
                return None
            count = dict.get(raw_fact, "count")
            source_name = dict.get(raw_fact, "source")
            if (
                type(count) is int
                and count >= 0
                and type(source_name) is str
                and source_name
                in {"count_marginal_exact", "maria_skill_count_exact"}
            ):
                return count
            return None

        q1_count_fact = _trusted_count_fact("q1")
        q3_count_fact = _trusted_count_fact("q3")
        badge_texts = {
            "红件": self._red_dimension_badge(compact_q6, "count"),
            "红格": self._red_dimension_badge(compact_q6, "cells"),
            "紫品": format_quality_dimension_state_badge(
                visible_quality("q4")
            ),
            "金品": format_quality_dimension_state_badge(
                visible_quality("q5")
            ),
            "白绿": (
                "件锁"
                if q1_count_fact is not None
                and visible_quality("q1").status
                not in {"ready", "exact"}
                else format_quality_dimension_state_badge(
                    visible_quality("q1")
                )
            ),
            "蓝品": (
                "件锁"
                if q3_count_fact is not None
                and visible_quality("q3").status
                not in {"ready", "exact"}
                else format_quality_dimension_state_badge(
                    visible_quality("q3")
                )
            ),
        }
        for key, badge_text in badge_texts.items():
            self._set_quality_state_badge(key, "" if is_settled else badge_text)
        hero = _text(context.get("hero"), "?")
        map_id = _text(context.get("map_id"), "?")
        round_text = _text(context.get("round"), "?")
        # Settlement perk: the support cat does a happy wag once when results land.
        if phase == "settled" and getattr(self, "_last_render_phase", None) != "settled":
            cat = getattr(self, "support_cat", None)
            if cat is not None:
                try:
                    cat.perk()
                except tk.TclError:
                    pass
        self._last_render_phase = phase
        source = _text(reference.get("source"), "-")
        self.title.configure(text=f"{hero} · {map_id} · R{round_text}")
        stale = data.get("stale") if isinstance(data.get("stale"), dict) else {}
        if continuity.get("reason") in {
            "quality_composition_conflict",
            "ref_engine_transient_error",
        }:
            source_round = _text(continuity.get("source_round"), "?")
            current_joint_exact = continuity.get("current_joint_exact_qualities")
            continuity_text = (
                f"沿用R{source_round}报价 · 本轮硬事实已更新"
                if type(current_joint_exact) is list and current_joint_exact
                else f"沿用R{source_round}已验证结果"
            )
            self._set_header_subtitle(
                f"{phase} · {source} · {continuity_text} · "
                f"{context.get('session_id') or '-'}"
            )
        elif continuity.get("reason") == "quality_composition_retrying":
            self._set_header_subtitle(
                f"{phase} · {source} · 本轮组合校验中 · "
                f"{context.get('session_id') or '-'}"
            )
        elif stale.get("keep_last_quote"):
            age = stale.get("age_seconds")
            age_text = f"{int(age)}s" if isinstance(age, (int, float)) else "?"
            self._set_header_subtitle(
                f"{phase} · {source} · snapshot age={age_text} · "
                f"{context.get('session_id') or '-'}"
            )
        else:
            self._set_header_subtitle(f"{phase} · {source} · {context.get('session_id') or '-'}")
        self.status.configure(text=_text(data.get("updated_at_text"), "--:--"))
        self._render_flags(data.get("flags") or [])
        self._set_settlement_button_state(data)
        price_titles = reference.get("price_titles") if isinstance(reference.get("price_titles"), dict) else {}
        self._set_price_titles(price_titles)

        quote_display = self._quote_display_values(data)
        for key in ("conservative", "balanced", "aggressive"):
            text = self._settlement_display_value(data, quote_display.get(key, reference.get(key)))
            self._set_autofit_label(
                self.price_labels[key],
                text,
                fallback_limit=24,
                base_font_pt=15,
                min_font_pt=10,
                font_family=FONT_NUMERIC,
            )

        try:
            cards_configured = all(
                str(self.price_labels[key].cget("text")) == str(quote_display.get(key, reference.get(key)))
                for key in quote_prefs.TIERS
            )
        except (AttributeError, tk.TclError):
            cards_configured = False
        if cards_configured:
            self._record_applied_quote(data)
        refresh_quote_preview = getattr(self, "_quote_preview_refresh", None)
        if callable(refresh_quote_preview):
            refresh_quote_preview()

        # Compact hover consumes the same structured joint lock as the details view.
        # A visually collapsed percentile triple is not evidence of a unique state.
        self._set_red_count_display(
            compact_q6,
            fallback=red.get("count_range") if "q6" in paired_quality_keys else None,
            hover=self._red_hover(
                "红件", red, reference, quality=compact_q6
            ),
        )
        self._set_autofit_label(
            self.red_rows["红格"],
            self._red_cells_display(compact_q6),
            fallback_limit=18,
            hover=self._red_hover("红格", red, reference, quality=compact_q6),
            base_font_pt=SUMMARY_VALUE_FONT_SIZE,
            min_font_pt=SUMMARY_VALUE_FONT_SIZE,
        )
        # 紫金是报价判断的高优先级信息。保留 panel/API 的组合字段与完整 hover，只在 Tk
        # 常驻卡片拆成两个固定单行（紫/金各一行）。长范围优先缩到既定字号下限，仍放不下
        # 才截断并由 hover 保留全文；不能在「件 · 格」处分成两行并顶高整张卡片。
        compact_q4 = visible_quality("q4")
        compact_q5 = visible_quality("q5")
        compact_q1 = visible_quality("q1")
        compact_q3 = visible_quality("q3")
        purple_summary, gold_summary = split_purple_gold_display_summary(
            red.get("quality_count_summary")
        )
        allow_verified_summary_fallback = is_settled or continuity.get("reason") in {
            "quality_composition_conflict",
            "ref_engine_transient_error",
        }
        purple_gold_hover = self._red_hover("紫金件", red, reference)
        self._set_autofit_label(
            self.red_rows["紫品"],
            format_quality_row_with_verified_fallback(
                compact_q4,
                purple_summary,
                allow_fallback=allow_verified_summary_fallback,
            ),
            fallback_limit=30,
            hover=purple_gold_hover,
            base_font_pt=SUMMARY_COMPACT_FONT_SIZE,
            min_font_pt=SUMMARY_COMPACT_MIN_FONT_SIZE,
        )
        self._set_autofit_label(
            self.red_rows["金品"],
            format_quality_row_with_verified_fallback(
                compact_q5,
                gold_summary,
                allow_fallback=allow_verified_summary_fallback,
            ),
            fallback_limit=30,
            hover=purple_gold_hover,
            base_font_pt=SUMMARY_COMPACT_FONT_SIZE,
            min_font_pt=SUMMARY_COMPACT_MIN_FONT_SIZE,
        )
        self._set_autofit_label(
            self.red_rows["白绿"],
            (
                f"{q1_count_fact}件·—格"
                if q1_count_fact is not None
                and compact_q1.status not in {"ready", "exact"}
                else format_compact_quality_footprint(
                    compact_q1,
                    compact_range=True,
                )
            ),
            fallback_limit=24,
            hover=self._red_hover("白绿", red, reference, quality=compact_q1),
            base_font_pt=SUMMARY_COMPACT_FONT_SIZE,
            min_font_pt=5,
        )
        self._set_autofit_label(
            self.red_rows["蓝品"],
            (
                f"{q3_count_fact}件·—格"
                if q3_count_fact is not None
                and compact_q3.status not in {"ready", "exact"}
                else format_compact_quality_footprint(
                    compact_q3,
                    compact_range=True,
                )
            ),
            fallback_limit=24,
            hover=self._red_hover("蓝品", red, reference, quality=compact_q3),
            base_font_pt=SUMMARY_COMPACT_FONT_SIZE,
            min_font_pt=5,
        )
        next_info = self._mini_next_info_text(data)
        self._apply_action_cell(
            "结算完成" if self._is_settlement_summary(data) else reference.get("action"),
            next_step=next_info,
            extra_hover=self._draw_prop_kit_tip_text(data),
            limit=36,
        )  # 结算时变「查看对局记录」可点
        # 红+空 = 推理总格 − 已知白绿蓝紫金格，并以精确红格为下限。
        # 它不是左侧纯红格；值走自动缩字号 fit，
        # 大仓范围文本更长(如"120–180格")时按控件宽自动缩字号,不溢出.
        self._set_autofit_label(
            self.action_rows["红/空格"],
            self._range_text(_remaining_cells_range(data.get("ahmed_ref")), suffix="格"),
            fallback_limit=20,
            base_font_pt=SUMMARY_COMPACT_FONT_SIZE,
            min_font_pt=SUMMARY_COMPACT_MIN_FONT_SIZE,
        )
        self._set_label(self.action_rows["最近"], self._latest_result_text(evidence), limit=20)
        self._set_candidate_dimension_rows(data)
        self._set_autofit_label(
            self.action_rows["金红概率"],
            red.get("quality_probability_summary"),
            fallback_limit=32,
            hover=self._red_hover("金红概率", red, reference),
            base_font_pt=SUMMARY_COMPACT_FONT_SIZE,
            min_font_pt=SUMMARY_COMPACT_MIN_FONT_SIZE,
            preserve_red_segment=True,
            stabilize_font=True,
        )
        self._set_remaining_cells_tip(data)
        self._update_detail_rows(data)
        self._render_minimap(data.get("minimap") or {})
        mode = "manual_overlay" if self._manual_active else "live"
        if self._is_settlement_summary(data):
            mode = f"{mode}_settled"
        self._record_safe_frame_telemetry(
            data,
            paired_presence=bool(paired_quality_keys),
        )
        self._record_summary_diagnostic(data, render_mode=mode)

    def _draw_prop_kit_tip_text(self, data: dict[str, Any]) -> str:
        """Return a draw hero's static kit text for the merged suggestion-row hover."""
        context = data.get("context") if isinstance(data.get("context"), dict) else {}
        return draw_hero_prop_kit_text(normalize_hero_key(context.get("hero") or ""))

    def _set_remaining_cells_tip(self, data: dict[str, Any]) -> None:
        """Clarify that red-plus-empty room differs from pure-red cells."""
        widget = self.action_rows.get("红/空格")
        if widget is None:
            return
        tip_text = (
            "红+空 = 估总格 − 已知(白绿/蓝/紫/金)格\n"
            "= 尚未分配给这些品质的「红 + 空位」区\n"
            "已知红格是它的下限；它不等于左侧纯红格"
        )
        tip = getattr(widget, "_hover_tip", None)
        if tip is None:
            widget._hover_tip = HoverTip(widget, tip_text)  # type: ignore[attr-defined]
        else:
            tip.set_text(tip_text)

    def _latest_result_text(self, evidence: dict[str, Any]) -> str:
        latest = evidence.get("latest_result")
        if not isinstance(latest, dict) or not latest:
            latest = evidence.get("latest_sent")
        if not isinstance(latest, dict) or not latest:
            return "-"
        tool = latest.get("tool") or latest.get("action_id") or latest.get("name") or ""
        result = latest.get("result")
        if result in (None, ""):
            return _text(tool, "-")
        result_text = _format_manual_number(result)
        return f"{tool}={result_text}" if tool else _text(result_text)

    def _mini_input_text(self, evidence: dict[str, Any]) -> str:
        summary = _text(evidence.get("ref_input_summary"), "")
        compact_parts: list[str] = []
        if summary and summary != "-":
            for part in summary.split(" · "):
                if part.startswith(("总件 ", "总格 ", "估总格 ")):
                    compact_parts.append(part)
                if len(compact_parts) >= 2:
                    break
        if compact_parts:
            return " · ".join(compact_parts)
        return _text(evidence.get("ref_readiness") or evidence.get("ref_status"), "-")

    def _mini_candidate_text(self, data: dict[str, Any]) -> str:
        evidence = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}
        candidate = _text(evidence.get("candidate_summary"), "").strip()
        if candidate and candidate != "-":
            return candidate
        return self._mini_input_text(evidence)

    def _mini_next_info_text(self, data: dict[str, Any]) -> str:
        evidence = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}
        hint = _text(evidence.get("next_info_hint"), "").strip()
        if hint and hint != "-":
            return hint
        ref_result = data.get("ahmed_ref") if isinstance(data.get("ahmed_ref"), dict) else {}
        context = data.get("context") if isinstance(data.get("context"), dict) else {}
        hero_key = normalize_hero_key(context.get("hero") or "")
        hint = _next_info_hint(
            ref_result,
            hero_key=hero_key,
            round_no=_parse_int(context.get("round")),
        )
        if hint and hint != "-":
            return hint
        return _text(evidence.get("ref_readiness") or evidence.get("ref_status"), "-")

    def _input_evidence_text(self, evidence: dict[str, Any]) -> str:
        parts = []
        for key in ("ref_input_summary", "public_numeric_summary", "minimap_quality_summary"):
            text = _text(evidence.get(key), "").strip()
            if text and text != "-" and text not in parts:
                parts.append(text)
        return " · ".join(parts) if parts else "-"

    def _truth_text(self, data: dict[str, Any]) -> str:
        truth = data.get("truth")
        if not isinstance(truth, dict) or not truth.get("available"):
            return "未结算"
        total_items = _text(truth.get("total_items"), "?")
        total_cells = _text(truth.get("total_cells"), "?")
        q6 = truth.get("q6") if isinstance(truth.get("q6"), dict) else {}
        q6_count = _text(q6.get("count"), "?")
        q6_cells = _text(q6.get("cells"), "?")
        if self._settlement_values_are_hidden(data):
            return f"价值隐藏 · {total_items}件/{total_cells}格 · 红{q6_count}件/{q6_cells}格"
        total = _text(truth.get("total_value"), "?")
        return f"{total} · {total_items}件/{total_cells}格 · 红{q6_count}件/{q6_cells}格"

    def _outline_value_summary_text(self, data: dict[str, Any]) -> str:
        raw = data.get("red_outline_value_summary")
        if not isinstance(raw, dict):
            return "未满足完整轮廓条件"
        if (
            raw.get("status") != "outline_complete"
            or raw.get("eligibility_complete") is not True
        ):
            return "未满足完整轮廓条件"
        values = []
        for key, label in (
            ("floor_value", "底价"),
            ("p50_value", "P50"),
            ("p90_value", "P90"),
        ):
            parsed = _parse_money(raw.get(key))
            if parsed is None or not math.isfinite(parsed) or parsed < 0:
                return "轮廓摘要字段不完整"
            values.append(f"{label} {_money(parsed)}")
        return " · ".join(values)

    def _update_detail_rows(self, data: dict[str, Any]) -> None:
        reference = data.get("reference") if isinstance(data.get("reference"), dict) else {}
        red = data.get("red") if isinstance(data.get("red"), dict) else {}
        evidence = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}
        ahmed_ref = data.get("ahmed_ref") if isinstance(data.get("ahmed_ref"), dict) else {}

        if self.details_composition_view_enabled:
            status_parts = []
            for value in (
                ahmed_ref.get("status") or reference.get("source"),
                evidence.get("match_text"),
                evidence.get("information_density"),
            ):
                text = _text(value, "").strip()
                if text and text != "-" and text not in status_parts:
                    status_parts.append(text)
            self._set_label(
                self.evidence_rows["状态与来源"],
                " · ".join(status_parts) or "-",
                limit=58,
            )
            self._set_summary_label(
                self.evidence_rows["输入"],
                self._input_evidence_text(evidence),
                line_limit=54,
            )
            self._set_label(
                self.evidence_rows["最近"],
                self._latest_result_text(evidence),
                limit=46,
            )
            self._set_label(
                self.evidence_rows["仓库轮廓"],
                self._detail_minimap_evidence_text(
                    data.get("minimap") if isinstance(data.get("minimap"), dict) else {}
                ),
                limit=70,
            )
            self._set_label(
                self.evidence_rows["诊断"],
                reference.get("note")
                or evidence.get("diagnostics")
                or evidence.get("ref_notes"),
                limit=70,
            )

            total_value = reference.get("total_value_range") or red.get("value_range")
            self._set_label(
                self.detail_rows["总格"],
                reference.get("total_grid_range"),
                limit=40,
            )
            self._set_label(
                self.detail_rows["总值"],
                self._settlement_display_value(data, total_value),
                limit=46,
            )
            self._set_label(
                self.detail_rows["红值"],
                self._settlement_display_value(data, red.get("value_range")),
                limit=46,
            )
            self._set_label(
                self.detail_rows["轮廓值"],
                self._outline_value_summary_text(data),
                limit=52,
            )
            self._set_label(
                self.detail_rows["结算"],
                self._truth_text(data),
                limit=60,
            )
            self._render_detail_composition(data)
            return

        self._set_label(self.evidence_rows["匹配"], evidence.get("match_text"), limit=26)
        self._set_label(self.evidence_rows["密度"], evidence.get("information_density"), limit=22)
        self._set_summary_label(self.evidence_rows["输入"], self._input_evidence_text(evidence))
        self._set_label(self.evidence_rows["组合"], evidence.get("ref_combo_count"), limit=22)
        self._set_label(self.evidence_rows["最近"], self._latest_result_text(evidence), limit=28)
        self._set_label(
            self.evidence_rows["诊断"],
            evidence.get("diagnostics") or evidence.get("ref_notes"),
            limit=34,
        )

        self._set_label(self.detail_rows["外援"], ahmed_ref.get("status") or reference.get("source"), limit=24)
        decision_range = reference.get("decision_range")
        total_value = reference.get("total_value_range") or red.get("value_range")
        self._set_label(self.detail_rows["决策"], decision_range, limit=34)
        self._set_label(self.detail_rows["总格"], reference.get("total_grid_range"), limit=28)
        self._set_label(
            self.detail_rows["总值"],
            self._settlement_display_value(data, total_value),
            limit=34,
        )
        self._set_label(self.detail_rows["红值"], self._settlement_display_value(data, red.get("value_range")), limit=34)
        self._set_label(self.detail_rows["结算"], self._truth_text(data), limit=38)
        self._set_label(self.detail_rows["备注"], reference.get("note") or evidence.get("ref_notes"), limit=38)

    def _set_details_mode(self, expanded: bool) -> None:
        with self._atomic_layout_update():
            self._set_details_mode_now(expanded)

    def _set_details_mode_now(self, expanded: bool) -> None:
        was_expanded = self.details_expanded
        if expanded and not was_expanded:
            self._fit_mini_window_to_content()
            self._remember_mini_layout_snapshot()
        self.details_expanded = expanded
        if expanded:
            self.mode_button.configure(text="迷你")
            self.root.minsize(430, 360)
            # 无条件 pack：pack() 对已 pack 的控件是幂等的，而且会重新压实 before=footer_row 的次序。
            # 这里【不能】再用 winfo_ismapped 做守卫 —— 它曾在此永久失效(见 _card() 的代理说明)，
            # 导致收起后再展开时 pack 被跳过，详情卡片再也回不来。
            self.details_card.pack(fill="x", pady=(5, 0), before=self.footer_row)
            # 详情底部的小地图与 mini 的“地图”入口重复，又让 510px/高 DPI 窗口过长。
            # 保留其 widget/data 供现有弹窗与诊断链复用，但详情可见层不再挂载该卡片；
            # 独有的件数/来源/品质/范围已并入折叠的“仓库轮廓”诊断行。
            self.minimap_card.pack_forget()
            self.details_scroll_canvas.yview_moveto(0.0)
            self.root.update_idletasks()
            if self._custom_details_size is not None:
                self._apply_window_geometry(self._details_geometry(), flush=False)
                self._sync_ui_scale_from_window(force=True)
            else:
                current_w, _ = self._current_window_size()
                self._fit_details_window_to_content(width=current_w)
            self.root.update_idletasks()
            if self._custom_details_size is None:
                # 再量一次: 首次展开时圆角卡片的 holder 还没按内容撑开(高度仍是 1px 占位), 上面那遍
                # fit 量到的 shell 偏矮 —— 实测首展 win_h=1143, 而内容需要 shell_req=1344(屏幕上限
                # 1320) -> 详情/小地图底部被裁掉 ~177px, 且【只在第一次展开】出现, 第二次起才对。
                # 卡片尺寸要到上一行 update_idletasks 之后才落定, 所以按落定后的尺寸重量一遍。
                current_w, _ = self._current_window_size()
                self._fit_details_window_to_content(width=current_w)
                self.root.update_idletasks()
            # 展开变高后, 若窗口靠屏底导致底部到屏外, 把整窗拉回屏内(修"点详情后下面信息不显示")。
            self._nudge_window_onscreen()
        elif was_expanded:
            self._restore_mini_layout_after_details()
        else:
            self.mode_button.configure(text="详情")
            self.root.minsize(430, 395)
            self.details_card.pack_forget()
            self.minimap_card.pack_forget()
            self._reset_ui_scale_baseline()
            self._apply_window_geometry(self._mini_geometry())
            width, _ = self._current_window_size()
            self._apply_mini_resize_layout(width, finalize=True)
        self._schedule_ui_prefs_save()

    def toggle_details(self) -> None:
        with self._atomic_layout_update():
            self._set_details_mode(not self.details_expanded)
            self._update_detail_rows(self._last_summary or {})
            if self.details_expanded:
                self._render_minimap(self._minimap_data)

    def _minimap_cell_hint(self, base: float) -> float:
        return max(8.0, float(base) * float(self._ui_scale))

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show isolated Ahmed reference Tk overlay.")
    parser.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT))
    # 300ms is the existing constructor floor. At the historical 1000ms default,
    # an atomically published snapshot could not meet the 300-500ms visible-
    # acknowledgement target even when monitor and engine work were instantaneous.
    parser.add_argument("--interval-ms", type=int, default=300)
    parser.add_argument(
        "--load-existing",
        action="store_true",
        help="Render the current latest_snapshot.json immediately instead of starting in standby.",
    )
    parser.add_argument(
        "--show-taskbar",
        action="store_true",
        help="Give the borderless overlay a taskbar/Alt-Tab entry (WS_EX_APPWINDOW) for stream "
        "window-capture and Alt-Tab, without adding a frame or a second window.",
    )
    parser.add_argument(
        "--diagnostic-profile",
        type=_parse_diagnostic_profile,
        metavar="{engineering,portable,public-safe}",
        default=_normalize_diagnostic_profile(
            os.environ.get(
                "BIDKING_HERO_REF_DIAGNOSTIC_PROFILE",
                DEFAULT_DIAGNOSTIC_PROFILE,
            )
        ),
        help=(
            "engineering writes full continuous UI diagnostics; portable skips "
            "continuous UI summary; public-safe also minimizes support files. "
            "All ordinary UI exports are support-safe and redacted; unredacted raw "
            "forensics require an explicit internal raw_forensics request."
        ),
    )
    parser.add_argument(
        "--stop-pid-on-exit",
        type=int,
        action="append",
        default=[],
        help="Terminate this monitor PID when the Hero Ref overlay exits.",
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
        help="Close Hero Ref when this monitor PID exits.",
    )
    parser.add_argument(
        "--keep-monitor-on-close",
        action="store_true",
        help="Leave the live monitor running when Hero Ref exits.",
    )
    parser.add_argument(
        "--protect-monitor-while-game-running",
        action="store_true",
        help=(
            "Leave the live monitor running only while BidKing.exe may still be active; "
            "used by the packaged WSS lifecycle."
        ),
    )
    parser.add_argument(
        "--ui-prefs",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Load/save hero_ref_ui_prefs.json beside the snapshot log dir. "
            "Packaged Start-HeroRef enables this; dev scripts leave it off."
        ),
    )
    args = parser.parse_args(argv)

    _write_startup_timing_event("tk_map", "begin")
    root = tk.Tk()
    timing_ready = {"map": False, "idle": False}

    def _record_tk_after_idle() -> None:
        if timing_ready["idle"]:
            return
        timing_ready["idle"] = True
        _write_startup_timing_event("tk_after_idle", "ok")

    def _record_root_map(event: tk.Event[tk.Misc]) -> None:
        if event.widget is not root or timing_ready["map"]:
            return
        timing_ready["map"] = True
        _write_startup_timing_event("tk_map", "ok")
        _write_startup_timing_event("tk_after_idle", "begin")
        root.after_idle(_record_tk_after_idle)

    root.bind("<Map>", _record_root_map, add="+")
    # Build and settle the complete Tk tree while transparent.  Keep it mapped so Tk measures the
    # same 510px mini layout as before; constructor-time update_idletasks() can no longer expose a
    # half-built border/card hierarchy filling outside-in.
    startup_alpha: float | None = None
    try:
        startup_alpha = float(root.attributes("-alpha"))
        root.attributes("-alpha", 0.0)
    except (tk.TclError, TypeError, ValueError):
        root.withdraw()
    # Taskbar 身份 + 小猫图标(替换 python.exe 默认图标)。AppUserModelID 让 Windows 用窗口自带图标
    # 并与 python 进程分组区分; iconbitmap(default=) 设 WM_SETICON 给本进程所有 toplevel。失败不影响启动。
    try:
        if os.name == "nt":
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("BidKing.Overlay.Catfan")
        _cat_ico = Path(__file__).resolve().parent.parent / "assets" / "bidking_cat.ico"
        if _cat_ico.is_file():
            root.iconbitmap(default=str(_cat_ico))
    except Exception:
        pass
    overlay_ref: dict[str, AhmadTkOverlay] = {}

    def _handle_pet_usage_event(event: str, state: PetAccessState) -> bool:
        overlay_instance = overlay_ref.get("overlay")
        return (
            overlay_instance._queue_product_pet_usage_event(event, state)
            if overlay_instance is not None
            else False
        )

    overlay = AhmadTkOverlay(
        root,
        snapshot_path=Path(args.snapshot),
        interval_ms=args.interval_ms,
        exit_when_pids=tuple(args.exit_when_pid_exits),
        stop_pids_on_exit=tuple(args.stop_pid_on_exit),
        cleanup_lock_paths=tuple(args.cleanup_lock_on_exit),
        keep_monitor_on_close=bool(args.keep_monitor_on_close),
        protect_monitor_while_game_running=bool(
            args.protect_monitor_while_game_running
        ),
        load_existing_snapshot=bool(args.load_existing),
        diagnostic_profile=args.diagnostic_profile,
        show_taskbar=bool(args.show_taskbar),
        ui_prefs_enabled=bool(args.ui_prefs),
        pet_usage_event_handler=_handle_pet_usage_event,
    )
    overlay_ref["overlay"] = overlay
    _reveal_root_when_ready(
        root,
        show_taskbar=bool(args.show_taskbar),
        restore_alpha=startup_alpha,
    )
    try:
        root.mainloop()
    finally:
        overlay._save_ui_prefs_if_enabled()
        overlay._run_exit_cleanup()
    return 0


if __name__ == "__main__":
    # MUST be first: the live-inference ProcessPoolExecutor spawns worker processes that re-run this
    # exe (frozen build) / re-import this module; freeze_support intercepts the child bootstrap BEFORE
    # main() so a worker never launches a second overlay window. No-op in the parent / unfrozen run.
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
