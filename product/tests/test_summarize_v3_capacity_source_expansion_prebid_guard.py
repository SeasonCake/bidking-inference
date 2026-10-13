import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_capacity_source_expansion_prebid_guard.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_capacity_source_expansion_prebid_guard",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _row(
    file: str,
    *,
    round_no: int,
    candidate: bool = True,
    pressure: bool = False,
    target_delta: float | None = None,
    p95_delta: float | None = None,
) -> dict[str, object]:
    return {
        "file": f"{file}#prebid_r{round_no}",
        "status": "ready",
        "round": round_no,
        "v3_cse_ready": True,
        "v3_cse_candidate": candidate,
        "v3_cse_pressure_candidate": pressure,
        "v3_cse_target_prior_max_delta": target_delta,
        "v3_cse_target_to_unique_non_temp_p95_delta": p95_delta,
        "v3_cse_target_count_source": "floor",
    }


def test_prebid_guard_summarizes_row_and_session_precision() -> None:
    module = _load_module()
    rows = [
        _row("a.json", round_no=1, target_delta=-10, p95_delta=-12),
        _row("a.json", round_no=2, pressure=True, target_delta=2, p95_delta=-4),
        _row("b.json", round_no=2, pressure=True, target_delta=3, p95_delta=-3),
        _row("c.json", round_no=1, candidate=False),
        _row("c.json", round_no=4, target_delta=-2, p95_delta=-4),
    ]

    result = module.summarize_prebid_guard(
        rows=rows,
        truth_by_file={"a.json": True, "b.json": False, "c.json": True},
        guards=("cse_candidate", "pressure_candidate", "target_near_source_p95_5"),
    )

    assert result["ready_rows"] == 5
    assert result["truth_rows"] == 4
    assert result["truth_sessions"] == 2

    by_guard = {row["guard"]: row for row in result["guards"]}
    assert by_guard["cse_candidate"]["selected_rows"] == 4
    assert by_guard["cse_candidate"]["row_recall"] == 0.75
    assert by_guard["cse_candidate"]["row_precision"] == 0.75
    assert by_guard["cse_candidate"]["session_recall"] == 1.0
    assert by_guard["cse_candidate"]["session_precision"] == 0.666667

    assert by_guard["pressure_candidate"]["selected_rows"] == 2
    assert by_guard["pressure_candidate"]["covered_truth_rows"] == 1
    assert by_guard["pressure_candidate"]["row_precision"] == 0.5
    assert by_guard["pressure_candidate"]["session_recall"] == 0.5

    assert by_guard["target_near_source_p95_5"]["selected_rows"] == 3
    assert by_guard["target_near_source_p95_5"]["covered_truth_sessions"] == 2
