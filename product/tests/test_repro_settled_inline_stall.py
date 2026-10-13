from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "repro_settled_inline_stall.py"


def _module():
    spec = importlib.util.spec_from_file_location("repro_settled_inline_stall", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _settled_snapshot() -> dict[str, object]:
    return {
        "phase": "settled",
        "ui_contract": {
            "context": {"phase": "settled", "hero": "gabriela", "round": 5}
        },
    }


def test_load_inputs_reads_snapshot_and_diagnostic_zip(tmp_path: Path) -> None:
    module = _module()
    snapshot = tmp_path / "latest_snapshot.json"
    snapshot.write_text(json.dumps(_settled_snapshot()), encoding="utf-8")
    diagnostic = tmp_path / "HeroRefDiag-safe.zip"
    with zipfile.ZipFile(diagnostic, "w") as archive:
        archive.writestr("latest_snapshot.json", json.dumps(_settled_snapshot()))

    loaded = module._load_inputs([snapshot], [diagnostic])

    assert len(loaded) == 2
    assert all(row[1]["phase"] == "settled" for row in loaded)


def test_measure_requires_truth_first_pending_contract(tmp_path: Path) -> None:
    module = _module()
    calls: list[bool] = []

    class Server:
        @staticmethod
        def summarize_snapshot(
            snapshot: dict[str, object],
            *,
            snapshot_path: Path,
            defer_settlement_estimate: bool,
        ) -> dict[str, object]:
            calls.append(defer_settlement_estimate)
            return {
                "truth": {"available": True, "total_value": 123},
                "diagnostics": {
                    "performance": {
                        "settlement_ref_engine_ms": None,
                        "settlement_estimate_state": "pending",
                    }
                },
            }

    worst = module._measure(
        Server(),
        [("safe", _settled_snapshot(), tmp_path / "latest_snapshot.json")],
    )

    assert calls == [True]
    assert 0 <= worst < module.STALL_BUDGET_MS


def test_measure_rejects_false_truth_first_claim(tmp_path: Path) -> None:
    module = _module()

    class Server:
        @staticmethod
        def summarize_snapshot(*_args: object, **_kwargs: object) -> dict[str, object]:
            return {
                "truth": {"available": False},
                "diagnostics": {
                    "performance": {
                        "settlement_ref_engine_ms": 1.0,
                        "settlement_estimate_state": "computed",
                    }
                },
            }

    with pytest.raises(RuntimeError, match="truth-first contract mismatch"):
        module._measure(
            Server(),
            [("bad", _settled_snapshot(), tmp_path / "latest_snapshot.json")],
        )
