from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _module():
    path = ROOT / "scripts" / "run_fatbeans_live_monitor.py"
    spec = importlib.util.spec_from_file_location("run_fatbeans_live_monitor", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_watch_mode_records_failed_file_fingerprint(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _module()
    watch_dir = tmp_path / "watch"
    log_dir = tmp_path / "logs"
    watch_dir.mkdir()
    sample = watch_dir / "bad.json"
    sample.write_text("{}", encoding="utf-8")
    calls: list[Path] = []

    def fail_process(path: Path, **_kwargs) -> None:
        calls.append(path)
        raise ValueError("invalid frame length 123")

    monkeypatch.setattr(module, "load_monitor_tables", lambda tables_dir=None: object())
    monkeypatch.setattr(module, "_is_stable_file", lambda path, stable_seconds: True)
    monkeypatch.setattr(module, "_process_file", fail_process)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "run_fatbeans_live_monitor.py",
            "--watch-dir",
            str(watch_dir),
            "--log-dir",
            str(log_dir),
            "--once",
            "--no-lock",
        ],
    )

    assert module.main() == 0
    assert calls == [sample]

    manifest = json.loads(
        (log_dir / "processed_files.json").read_text(encoding="utf-8")
    )
    entry = manifest[str(sample.resolve())]
    assert entry["status"] == "error"
    assert entry["error_type"] == "ValueError"
    assert "invalid frame length" in entry["error"]

    error_lines = (log_dir / "monitor_errors.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(error_lines) == 1
    error_row = json.loads(error_lines[0])
    assert error_row["name"] == "bad.json"
    assert error_row["error_type"] == "ValueError"

    calls.clear()
    assert module.main() == 0
    assert calls == []
