"""Source-external container probes for phase-1 composition successor1."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "hero_ref" / "src", ROOT / "hero_ref" / "tools", ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ahmad_ref_engine as engine  # noqa: E402
from test_core_engine_phase1_composition_0_3_2 import (  # noqa: E402
    _both_authorities_snapshot,
)


def _explode(*_args: object, **_kwargs: object) -> object:
    raise AssertionError("hostile aggregate container protocol executed")


class _HostileRows(list):
    __contains__ = _explode
    __getitem__ = _explode
    __iter__ = _explode
    __len__ = _explode


def _snapshot_with_container(group: str, hostile: bool) -> dict:
    snapshot = _both_authorities_snapshot()
    public_info = snapshot["ui_contract"]["constraints"]["public_info"]
    if hostile:
        public_info[group] = _HostileRows(public_info[group])
    else:
        public_info[group] = list(public_info[group])
    return snapshot


def _assert_evidence(evidence: engine.RefEvidence, group: str, hostile: bool) -> None:
    if not hostile:
        assert evidence.hard_public_avg_values == {"q5": 50_000.0}
        assert evidence.total_grid_options == (9,)
    elif group == "public_avg_values":
        assert evidence.hard_public_avg_values == {}
        assert evidence.total_grid_options == (9,)
    else:
        assert evidence.hard_public_avg_values == {"q5": 50_000.0}
        assert evidence.total_grid_options == ()


def _assert_result(result: engine.RefResult, group: str, hostile: bool) -> None:
    if not hostile:
        assert result.balanced == 200_000
        assert (
            f"{engine.HARD_QUALITY_AVG_VALUE_QUOTE_FLOOR_NOTE}:q5=200000"
            in result.notes
        )
        assert f"{engine.TOTAL_AVG_CELLS_GRID_OPTIONS_NOTE}:unique:9" in result.notes
    elif group == "public_avg_values":
        assert result.balanced == 184_000
        assert not any(
            note.startswith(engine.HARD_QUALITY_AVG_VALUE_QUOTE_FLOOR_NOTE)
            for note in result.notes
        )
        assert f"{engine.TOTAL_AVG_CELLS_GRID_OPTIONS_NOTE}:unique:9" in result.notes
    else:
        assert result.balanced == 200_000
        assert (
            f"{engine.HARD_QUALITY_AVG_VALUE_QUOTE_FLOOR_NOTE}:q5=200000"
            in result.notes
        )
        assert not any(
            note.startswith(engine.TOTAL_AVG_CELLS_GRID_OPTIONS_NOTE)
            for note in result.notes
        )


@pytest.mark.parametrize("consumer", ("extract", "prepare", "run"))
@pytest.mark.parametrize("group", ("public_avg_values", "public_numeric_facts"))
def test_exact_builtin_row_container_is_the_known_good_control(
    group: str,
    consumer: str,
) -> None:
    snapshot = _snapshot_with_container(group, hostile=False)

    if consumer == "extract":
        _assert_evidence(engine.extract_evidence(snapshot), group, hostile=False)
    elif consumer == "prepare":
        prepared = engine.prepare_reference_engine_snapshot(snapshot)
        _assert_evidence(engine.extract_evidence(prepared), group, hostile=False)
        _assert_result(engine.run_reference_engine(prepared), group, hostile=False)
    else:
        _assert_result(engine.run_reference_engine(snapshot), group, hostile=False)


@pytest.mark.parametrize("consumer", ("extract", "prepare", "run"))
@pytest.mark.parametrize("group", ("public_avg_values", "public_numeric_facts"))
def test_hostile_row_container_is_fail_closed_without_running_protocols(
    group: str,
    consumer: str,
) -> None:
    snapshot = _snapshot_with_container(group, hostile=True)

    if consumer == "extract":
        _assert_evidence(engine.extract_evidence(snapshot), group, hostile=True)
    elif consumer == "prepare":
        prepared = engine.prepare_reference_engine_snapshot(snapshot)
        _assert_evidence(engine.extract_evidence(prepared), group, hostile=True)
        _assert_result(engine.run_reference_engine(prepared), group, hostile=True)
    else:
        _assert_result(engine.run_reference_engine(snapshot), group, hostile=True)


@pytest.mark.parametrize("consumer", ("extract", "prepare", "run"))
def test_hostile_raw_row_container_is_stable_and_fail_closed(
    consumer: str,
) -> None:
    snapshot = _both_authorities_snapshot()
    snapshot["public_info_rows"] = _HostileRows(snapshot["public_info_rows"])

    def assert_evidence(evidence: engine.RefEvidence) -> None:
        assert evidence.hard_public_avg_values == {}
        assert evidence.total_grid_options == ()

    def assert_result(result: engine.RefResult) -> None:
        assert result.balanced == 184_000
        assert not any(
            note.startswith(engine.HARD_QUALITY_AVG_VALUE_QUOTE_FLOOR_NOTE)
            for note in result.notes
        )
        assert not any(
            note.startswith(engine.TOTAL_AVG_CELLS_GRID_OPTIONS_NOTE)
            for note in result.notes
        )

    if consumer == "extract":
        assert_evidence(engine.extract_evidence(snapshot))
    elif consumer == "prepare":
        prepared = engine.prepare_reference_engine_snapshot(snapshot)
        assert_evidence(engine.extract_evidence(prepared))
        assert_result(engine.run_reference_engine(prepared))
    else:
        assert_result(engine.run_reference_engine(snapshot))
