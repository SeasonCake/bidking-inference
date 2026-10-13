"""Fast Aisha regression via pre-built eval slices (see scripts/build_aisha_eval_slices.py)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "data" / "fixtures" / "aisha_eval_slices"
MANIFEST = FIXTURE_DIR / "manifest.json"

AHMAD_SRC = ROOT / "hero_ref" / "src"
if str(AHMAD_SRC) not in sys.path:
    sys.path.insert(0, str(AHMAD_SRC))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ahmad_ref_engine import run_reference_engine  # noqa: E402


def _manifest_slices() -> list[dict]:
    if not MANIFEST.is_file():
        return []
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return list(payload.get("slices") or [])


@pytest.fixture(scope="module")
def aisha_eval_slices() -> list[dict]:
    return _manifest_slices()


@pytest.mark.skipif(not MANIFEST.is_file(), reason="run scripts/build_aisha_eval_slices.py first")
def test_aisha_eval_slices_manifest_non_empty(aisha_eval_slices: list[dict]) -> None:
    assert len(aisha_eval_slices) >= 10


@pytest.mark.skipif(not MANIFEST.is_file(), reason="run scripts/build_aisha_eval_slices.py first")
@pytest.mark.parametrize(
    "entry",
    _manifest_slices()[:8] or [{"path": "__missing__"}],
    ids=lambda e: e.get("slice_id", "missing"),
)
def test_aisha_eval_slice_quotes_at_live_cap(entry: dict) -> None:
    slice_path = ROOT / entry["path"]
    payload = json.loads(slice_path.read_text(encoding="utf-8"))
    result = run_reference_engine(payload["snapshot"], max_combos=8_000).as_dict()
    assert result["combo_count"] > 0
    assert result.get("balanced") is not None or result["status"] in {"count_prior", "exact"}
