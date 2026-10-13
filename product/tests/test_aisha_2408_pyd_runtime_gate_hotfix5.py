"""Aisha R4 must survive the encrypted Cython engine boundary.

The source-Python engine accepted this state while hotfix4's compiled ``.pyd``
raised before producing a quote.  The release build runs the same frozen fixture
again after compiling the engine; this source test protects its product meaning.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ahmad_live_panel_server as panel  # noqa: E402
from ahmad_ref_engine import run_reference_engine  # noqa: E402


FIXTURE = ROOT / "tests" / "fixtures" / "live" / "aisha_2408_multicount_q5_pyd_gate.json"
BUILD_SCRIPT = ROOT / "scripts" / "build_live_portable.ps1"


def test_aisha_2408_multicount_q5_runtime_gate_produces_quote() -> None:
    snapshot = json.loads(FIXTURE.read_text(encoding="utf-8"))
    prepared = panel._aisha_prepare_pass_snapshot(  # noqa: SLF001
        snapshot,
        pass_kind=panel.AISHA_ENGINE_PASS_ITEM,
        round_no=4,
    )

    result = run_reference_engine(
        prepared,
        max_combos=panel._aisha_pass_max_combos(4, prepared),  # noqa: SLF001
    )

    assert result.status in {"ok", "count_prior"}
    assert result.combo_count > 0
    assert 0 < result.conservative <= result.balanced <= result.aggressive


def test_release_build_executes_aisha_2408_fixture_with_stage_pyd() -> None:
    build = BUILD_SCRIPT.read_text(encoding="utf-8-sig")
    gate_start = build.index("$aishaPydGateFixture =")

    assert "aisha_2408_multicount_q5_pyd_gate.json" in build[gate_start:]
    assert "$aishaPydGateOut = (& $verifyPy -s -c $aishaPydGateCode" in build[gate_start:]
    assert "AISHA_PYD_GATE_OK" in build[gate_start:]
    assert gate_start < build.index("# --- 6d.")
