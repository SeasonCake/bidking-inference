"""Print Hero Ref UI rows for purple/gold count+cells display samples."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "data" / "samples" / "hero_ref" / "fixtures" / "purple_gold_cells_display"
OUTPUT_PATH = FIXTURE_DIR / "preview_expected.txt"


def _load_server_module():
    path = ROOT / "hero_ref" / "tools" / "ahmad_live_panel_server.py"
    spec = importlib.util.spec_from_file_location("ahmad_live_panel_server", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load panel server from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _with_fresh_timestamps(payload: dict) -> dict:
    """Avoid stale_snapshot when loading static fixture files in UI."""
    out = copy.deepcopy(payload)
    now = time.time()
    out["created_at"] = now
    ui_contract = out.setdefault("ui_contract", {})
    source = ui_contract.setdefault("source", {})
    if isinstance(source, dict):
        source["created_at"] = now
    return out


def _preview(module, snapshot_path: Path, *, ui_ready_name: str | None = None) -> str:
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    fresh = _with_fresh_timestamps(payload)
    if ui_ready_name:
        ui_path = FIXTURE_DIR / ui_ready_name
        ui_path.write_text(json.dumps(fresh, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result = module.summarize_snapshot(
        fresh,
        snapshot_path=ROOT / "data" / "logs" / "live" / "latest_snapshot.json",
    )
    red = result.get("red") if isinstance(result.get("red"), dict) else {}
    context = result.get("context") if isinstance(result.get("context"), dict) else {}
    lines = [
        f"file: {snapshot_path.name}",
        f"status: {result.get('status')}",
        f"context: {context.get('hero')} · map {context.get('map_id')} · R{context.get('round')} · {context.get('phase')}",
        f"【红品与价值 → 紫金件】: {red.get('quality_count_summary')}",
        f"【红品与价值 → 红件 / 红格】: {red.get('count_range')} | {red.get('cells_range')}",
        f"【红品与价值 → 低品件】: {red.get('uncertainty_summary') or red.get('risk_reference')}",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    module = _load_server_module()
    parts = [
        "Hero Ref 紫金件数+格子 显示预览（v0.1.9 dev）",
        "UI 看「红品与价值」卡片里的「紫金件」行。",
        "",
    ]
    for source_name, ui_name in (
        ("locked_both_snapshot.json", "locked_both_ui.json"),
        ("zero_gold_snapshot.json", "zero_gold_ui.json"),
        ("settled_snapshot.json", "settled_ui.json"),
    ):
        path = FIXTURE_DIR / source_name
        parts.append(_preview(module, path, ui_ready_name=ui_name))
    text = "\n".join(parts)
    OUTPUT_PATH.write_text(text, encoding="utf-8")
    print(text)
    print(f"written: {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
