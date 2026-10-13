import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "summarize_v3_prior_archive_calibration.py"
    )
    spec = importlib.util.spec_from_file_location(
        "summarize_v3_prior_archive_calibration",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_summarize_calibration_from_values_reports_prior_shift() -> None:
    module = _load_module()

    rows = module.summarize_calibration_from_values(
        {
            2506: (100.0, 200.0, 300.0, 400.0, 500.0),
            2507: (100.0, 110.0),
        },
        {
            2506: (50.0, 100.0, 150.0, 200.0, 250.0),
            2507: (100.0, 100.0),
        },
        map_names={2506: "test"},
        min_sessions=3,
    )

    assert rows == [
        {
            "map_id": 2506,
            "map_name": "test",
            "archive_sessions": 5,
            "prior_trials": 5,
            "actual_raw_p50": 300.0,
            "actual_raw_p90": 460.0,
            "prior_raw_p50": 150.0,
            "prior_raw_p90": 230.0,
            "median_ratio": 2.0,
            "p90_ratio": 2.0,
        }
    ]
