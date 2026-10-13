"""Build UI-ready snapshots + preview text for masked highest-bidder display."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "data" / "samples" / "hero_ref" / "fixtures" / "masked_highest_bidder"
OUTPUT_PATH = FIXTURE_DIR / "preview_expected.txt"

SAMPLES = (
    ("sample1_playera_snapshot.json", "sample1_playera_ui.json", "样本1 · 玩家A"),
    ("sample2_four_char_snapshot.json", "sample2_four_char_ui.json", "样本2 · 欧阳娜娜"),
)


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
    out = copy.deepcopy(payload)
    now = time.time()
    out["created_at"] = now
    ui_contract = out.setdefault("ui_contract", {})
    source = ui_contract.setdefault("source", {})
    if isinstance(source, dict):
        source["created_at"] = now
    return out


def _preview(module, source_name: str, ui_name: str, label: str) -> str:
    source_path = FIXTURE_DIR / source_name
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    fresh = _with_fresh_timestamps(payload)
    ui_path = FIXTURE_DIR / ui_name
    ui_path.write_text(json.dumps(fresh, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result = module.summarize_snapshot(
        fresh,
        snapshot_path=ROOT / "data" / "logs" / "live" / "latest_snapshot.json",
    )
    reference = result.get("reference") if isinstance(result.get("reference"), dict) else {}
    context = result.get("context") if isinstance(result.get("context"), dict) else {}
    raw = (
        fresh.get("ui_contract", {})
        .get("baseline", {})
        .get("decision", {})
        .get("current_highest")
    )
    return "\n".join(
        [
            f"{label}",
            f"  源文件: {source_name}",
            f"  UI加载: {ui_name}",
            f"  原始最高: {raw}",
            f"  UI显示最高: {reference.get('current_highest')}",
            f"  场景: {context.get('hero')} · map {context.get('map_id')} · R{context.get('round')}",
            "",
        ]
    )


def main() -> int:
    module = _load_server_module()
    parts = [
        "Hero Ref 最高出价者名字掩码预览（v0.1.9 dev）",
        "看「当前建议 → 最高」行。",
        "",
    ]
    for source_name, ui_name, label in SAMPLES:
        parts.append(_preview(module, source_name, ui_name, label))
    text = "\n".join(parts)
    OUTPUT_PATH.write_text(text, encoding="utf-8")
    print(text)
    print(f"written: {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
