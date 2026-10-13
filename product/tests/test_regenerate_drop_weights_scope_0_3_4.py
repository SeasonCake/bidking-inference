from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "calibration" / "regenerate_drop_weights.py"


def _module():
    spec = importlib.util.spec_from_file_location("regenerate_drop_weights", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_static_6001_never_enters_generated_distribution_scope() -> None:
    module = _module()

    assert module._generation_map_ids(  # noqa: SLF001
        ["2101", "5606", "6001"],
        ["2101", "6001"],
    ) == ["2101", "5606"]
    assert module.RECOGNIZED_UNMODELED_RANKMAP_IDS == frozenset({6001})
