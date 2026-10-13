"""Re-verify that settled truth-first rendering never runs the heavy clone inline.

背景 (2026-07-01, 打包/加密窗口收口):
  群友反馈 "沉船/大仓 60+件 计算器无响应需重开"。watchdog 抓到 stall 时
  ``summary_worker_running=False`` -> 重活在 Tk 主线程, 不在推理 worker。
  根因class = settled 结算态在 Tk 主线程内联跑 pre-settlement clone。
  0.3.4 将结算拆成 truth-first 与 estimate-fallback：主线程只运行
  ``summarize_snapshot(..., defer_settlement_estimate=True)``；同局可信 quote
  receipt 可立即填充估价，否则显示“更新中”，高成本 clone 只进进程 worker。

本脚本只计时真实 UI 会同步执行的 truth-first 路径，并验证真实结算值仍可见、
``settlement_ref_engine_ms`` 未运行、estimate 状态明确为 pending。

跑法:
  python scripts/repro_settled_inline_stall.py --diagnostic-zip <HeroRefDiag.zip>
  python scripts/repro_settled_inline_stall.py --snapshot <latest_snapshot.json>
判据: 每份 truth-first < 200ms 且 estimate_state=pending。无可用输入返回 2，
不再把“0 样本”误报为绿色。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]

# (tag, 相对路径). 已知 settled 样本; 深仓优先。draw 深仓 settled 缺失 -> 见 docstring 补验。
SNAPSHOTS = [
    (
        "aisha_2531_R4_80item_10M",  # 就是 2026-06-21 那局 11s stall
        "data/samples/hero_ref/archive/exports/2026-06-21/"
        "HeroRefDiag-20260621-014137-2531_1469222597150160/latest_snapshot.json",
    ),
    ("ahmed_0610_38item", "data/logs/live_2026.06.10_ahmed/latest_snapshot.json"),
]

STALL_BUDGET_MS = 200.0  # 超过即视为主线程可感卡顿, 需回看是否有未封顶路径


def _load_engine():
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "hero_ref/tools"))
    sys.path.insert(0, str(ROOT / "hero_ref/src"))
    os.environ.setdefault("BIDKING_PROJECT_ROOT", str(ROOT))
    import ahmad_live_panel_server as srv  # noqa: E402

    if srv.run_reference_engine is None:
        raise SystemExit("run_reference_engine 未加载 — 引擎不可用, 计时无意义")
    return srv


def _load_inputs(
    snapshots: list[Path],
    diagnostic_zips: list[Path],
) -> list[tuple[str, dict[str, object], Path]]:
    inputs: list[tuple[str, dict[str, object], Path]] = []
    requested = snapshots or diagnostic_zips
    if not requested:
        snapshots = [ROOT / relative for _tag, relative in SNAPSHOTS]
    for path in snapshots:
        if not path.is_file():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if type(value) is dict:  # noqa: E721
            inputs.append((path.stem, value, path))
    for path in diagnostic_zips:
        if not path.is_file():
            continue
        try:
            with zipfile.ZipFile(path) as archive:
                raw = archive.read("latest_snapshot.json")
            value = json.loads(raw)
        except (
            OSError,
            KeyError,
            UnicodeError,
            json.JSONDecodeError,
            zipfile.BadZipFile,
        ):
            continue
        if type(value) is dict:  # noqa: E721
            inputs.append(
                (path.stem.split("_")[0], value, Path("latest_snapshot.json"))
            )
    return inputs


def _measure(srv, inputs: list[tuple[str, dict[str, object], Path]]) -> float:
    worst_total_ms = 0.0
    for tag, snap, snapshot_path in inputs:
        uc = snap.get("ui_contract", {}) or {}
        ctx = uc.get("context", {}) or {}
        hero = ctx.get("hero") or snap.get("hero") or "?"
        rnd = ctx.get("round") or snap.get("round")
        phase = ctx.get("phase") or snap.get("phase")
        if phase != "settled":
            raise RuntimeError(f"{tag}: phase={phase} is not settled")
        started = time.perf_counter()
        summary = srv.summarize_snapshot(
            snap,
            snapshot_path=snapshot_path,
            defer_settlement_estimate=True,
        )
        total = (time.perf_counter() - started) * 1000.0
        truth = summary.get("truth") if isinstance(summary.get("truth"), dict) else {}
        performance = (
            summary.get("diagnostics", {}).get("performance", {})
            if isinstance(summary.get("diagnostics"), dict)
            else {}
        )
        state = performance.get("settlement_estimate_state")
        if (
            truth.get("available") is not True
            or performance.get("settlement_ref_engine_ms") is not None
            or state != "pending"
        ):
            raise RuntimeError(f"{tag}: truth-first contract mismatch")
        worst_total_ms = max(worst_total_ms, total)
        verdict = "OK 无stall" if total < STALL_BUDGET_MS else "超预算"
        print(
            f"[{tag}] hero={hero} R{rnd} truth_first={total:.1f}ms "
            f"estimate_state={state} -> {verdict}"
        )
    return worst_total_ms


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 GBK 会把中文输出乱码; 强制 UTF-8 便于后续重验读结果。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - 非 tty / 旧 python 无 reconfigure, 忽略
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", action="append", type=Path, default=[])
    parser.add_argument("--diagnostic-zip", action="append", type=Path, default=[])
    args = parser.parse_args(argv)
    inputs = _load_inputs(args.snapshot, args.diagnostic_zip)
    if not inputs:
        print("[input] no settled snapshot input", file=sys.stderr)
        return 2
    srv = _load_engine()
    try:
        worst = _measure(srv, inputs)
    except RuntimeError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    print(f"最坏 truth-first = {worst:.1f}ms (预算 {STALL_BUDGET_MS:.0f}ms)")
    return 0 if worst < STALL_BUDGET_MS else 1


if __name__ == "__main__":
    raise SystemExit(main())
