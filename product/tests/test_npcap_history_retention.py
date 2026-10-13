from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _module():
    path = ROOT / "scripts" / "run_npcap_live_monitor.py"
    spec = importlib.util.spec_from_file_location("run_npcap_live_monitor_retention", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_session_history_retention_keeps_all_rounds_for_latest_sessions(tmp_path: Path) -> None:
    module = _module()
    path = tmp_path / "sessions.jsonl"
    rows: list[dict] = []
    for session_no in range(305):
        session_id = f"session-{session_no:03d}"
        rows.extend(
            [
                {"session_id": session_id, "phase": "reading", "round": 1},
                {"session_id": session_id, "phase": "bidding", "round": 1},
                {"session_id": session_id, "phase": "settled", "round": 2},
            ]
        )
    _write_rows(path, rows)

    module._prune_session_history(path, keep_sessions=300)

    retained = _read_rows(path)
    retained_ids = list(dict.fromkeys(row["session_id"] for row in retained))
    assert retained_ids == [f"session-{session_no:03d}" for session_no in range(5, 305)]
    assert len(retained) == 900
    assert [row["phase"] for row in retained[:3]] == ["reading", "bidding", "settled"]


def test_session_history_retention_leaves_small_or_invalid_file_unchanged(
    tmp_path: Path,
) -> None:
    module = _module()
    small = tmp_path / "small.jsonl"
    _write_rows(small, [{"session_id": "a"}, {"session_id": "b"}])
    small_before = small.read_bytes()
    module._prune_session_history(small, keep_sessions=300)
    assert small.read_bytes() == small_before

    invalid = tmp_path / "invalid.jsonl"
    invalid.write_text('{"session_id":"a"}\nnot-json\n', encoding="utf-8")
    invalid_before = invalid.read_bytes()
    module._prune_session_history(invalid, keep_sessions=1)
    assert invalid.read_bytes() == invalid_before
