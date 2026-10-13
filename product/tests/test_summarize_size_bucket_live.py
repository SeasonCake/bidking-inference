from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_script_module(name: str):
    # scripts/ 不是包（无 __init__.py），且 pyproject pythonpath 只含 src/，
    # 所以 `from scripts.x import` 在 pytest 下无法解析。按文件路径加载，与
    # test_fatbeans_webhook_monitor.py 一致；脚本自身的 sys.path 注入仍会执行。
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_module = _load_script_module("summarize_size_bucket_live")
_dedupe_latest_by_file = _module._dedupe_latest_by_file
summarize = _module.summarize


def test_summarize_size_bucket_live_groups(tmp_path: Path) -> None:
    log = tmp_path / "model_eval.jsonl"
    rows = [
        {
            "file": "a.json",
            "ts": 1.0,
            "final_value": 1_000_000,
            "decision_value_p50": 900_000,
            "decision_value_p50_error": -100_000,
            "action_100172_used": True,
            "action_size_avg_tool_count": 1,
            "size_bucket_active": True,
        },
        {
            "file": "b.json",
            "ts": 1.0,
            "final_value": 800_000,
            "decision_value_p50": 700_000,
            "decision_value_p50_error": -100_000,
            "action_100172_used": False,
            "action_size_avg_tool_count": 0,
            "size_bucket_active": False,
        },
    ]
    log.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
        encoding="utf-8",
    )
    loaded = _dedupe_latest_by_file(rows)
    summary = summarize(loaded)
    assert summary["settled_rows"] == 2
    groups = {g["label"]: g for g in summary["groups"]}
    assert groups["action_100172_used"]["rows"] == 1
    assert groups["action_100172_not_used"]["rows"] == 1
    assert groups["action_100172_used"]["decision_p50_mae"] == 100_000.0


def test_summarize_size_bucket_live_prefers_replacement_decision_truth() -> None:
    summary = summarize(
        [
            {
                "file": "tail.json",
                "ts": 1.0,
                "final_value": 1_000_000,
                "final_decision_value": 600_000,
                "final_decision_value_with_tail_replacement": 650_000,
                "decision_value_p50": 550_000,
                "decision_value_p50_error": -450_000,
            },
        ]
    )

    assert summary["groups"][0]["decision_p50_mae"] == 100_000.0


def test_summarize_size_bucket_live_falls_back_to_raw_truth() -> None:
    summary = summarize(
        [
            {
                "file": "raw.json",
                "ts": 1.0,
                "final_value": 1_000_000,
                "decision_value_p50": 700_000,
                "decision_value_p50_error": 0,
            },
        ]
    )

    assert summary["groups"][0]["decision_p50_mae"] == 300_000.0
