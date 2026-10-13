from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import struct
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import pytest


ROOT = Path(__file__).resolve().parents[1]
for _path in (
    ROOT / "src",
    ROOT / "hero_ref" / "src",
    ROOT / "hero_ref" / "tools",
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from ahmad_live_panel_server import (  # noqa: E402
    ServerConfig,
    make_handler,
    summarize_snapshot,
)
from ahmad_ref_engine import (  # noqa: E402
    BIDKING_DISABLE_PUBLIC_CATEGORY_TOTAL_VALUE_FLOOR,
    extract_evidence,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)
from ahmad_tk_overlay import _summary_diagnostic_row  # noqa: E402
from bidking_lab.live.fatbeans import (  # noqa: E402
    FatbeansCaptureEvents,
    FatbeansStateEvent,
    _parse_public_info,
)
from bidking_lab.live.monitor import _public_info_rows  # noqa: E402
from bidking_lab.runtime.snapshot import (  # noqa: E402
    _ui_public_numeric_contract,
    ui_contract_from_artifact,
)


_ABSENT = object()
_CATEGORY_INFO_ID = 200044
_CATEGORY_SEMANTIC = "auction_category_total_value"
_CATEGORY_LABEL = "竞拍类型总价"
_OBSERVED_CATEGORY_TOTAL = 868_607
_THRESHOLD_VALUES = (868_607, 999_999, 1_000_000, 1_000_001)
_NO_OP_FIELDS = (
    "combo_count",
    "value_p25",
    "value_p50",
    "value_p75",
    "quality_count_ranges",
    "quality_cells_ranges",
    "quality_count_candidates",
    "quality_cells_candidates",
    "quality_count_cells_candidates",
    "quality_lock",
)
_PACKAGE_FINGERPRINT_SHA256 = (
    "96d0cfea329038edf2a81bff9ed4e903f53253e7ed9720604d7d564bde4d6dd1"
)
_RELEASE_CONTRACT_CANONICAL_LF_SHA256 = (
    "7ea4aaa42c6215476b4137af881380dde41a957d9c82848ce0cf078e52440084"
)
_STATIC_DATA_REL = (
    "external_references/AuctionAnalyzer4.13.3/_decompiled/MapBidCalculator/"
    "MapBidCalculator/Models/StaticData.cs"
)


def _assert_frozen_file(
    relative: str,
    *,
    size: int,
    sha256: str,
) -> None:
    path = ROOT / relative
    assert path.is_file(), f"missing frozen package input: {relative}"
    payload = path.read_bytes()
    assert len(payload) == size, f"wrong frozen package input size: {relative}"
    assert hashlib.sha256(payload).hexdigest() == sha256, (
        f"wrong frozen package input sha256: {relative}"
    )


@pytest.fixture(scope="module", autouse=True)
def _frozen_package_inputs() -> None:
    fingerprints_path = ROOT / "scripts" / "package_data_fingerprints.json"
    contract_path = ROOT / "scripts" / "release_zip_contract.json"
    assert hashlib.sha256(fingerprints_path.read_bytes()).hexdigest() == (
        _PACKAGE_FINGERPRINT_SHA256
    )
    contract_bytes = contract_path.read_bytes()
    canonical_contract = contract_bytes.replace(b"\r\n", b"\n")
    assert b"\r" not in canonical_contract
    assert hashlib.sha256(canonical_contract).hexdigest() == (
        _RELEASE_CONTRACT_CANONICAL_LF_SHA256
    )
    contract = json.loads(canonical_contract.decode("utf-8"))
    fingerprints = json.loads(fingerprints_path.read_text("utf-8"))
    for relative, spec in fingerprints["files"].items():
        _assert_frozen_file(
            relative,
            size=int(spec["size"]),
            sha256=str(spec["sha256"]),
        )
    _assert_frozen_file(
        _STATIC_DATA_REL,
        size=10_408,
        sha256=contract["fixed_manifest_values"]["StaticDataSourceSHA256"],
    )


def _warehouse_source() -> dict[str, object]:
    return {
        "qualification": "official_random_warehouse",
        "official_distribution_eligible": True,
        "quote_policy": "advisory_only_no_clamp",
        "quarantine_required": False,
    }


def _artifact(
    category_total: object = _ABSENT,
    *,
    row: dict[str, object] | None = None,
    phase: str = "bidding",
) -> dict[str, object]:
    """Reduced Ahmed 5603 R3 fixture with all settlement truth omitted."""
    source = _warehouse_source()
    rows: list[dict[str, object]] = []
    if row is not None:
        rows.append(row)
    elif category_total is not _ABSENT:
        rows.append(
            {
                "info_id": _CATEGORY_INFO_ID,
                "map_id": 5603,
                "value": category_total,
                "value_field": 12,
            }
        )
    return {
        "hero": "ahmed",
        "map_id": 5603,
        "phase": phase,
        "round": 3,
        "action_round": 3,
        "session_id": "5603:truth-free-public-category-regression",
        "created_at": time.time(),
        "warehouse_source_qualification": "official_random_warehouse",
        "official_distribution_eligible": True,
        "warehouse_source": copy.deepcopy(source),
        # This reduced tactical context is deliberately pre-settlement.  It
        # keeps the existing posterior above the 1m boundary so the test can
        # prove that 200044 changes terminal quotes, not posterior structure.
        "structured_ref_inputs": {
            "hero": "ahmed",
            "total_count": 36,
            "total_cells": 104,
            "counts": {"q3": 7, "q4": 9},
            "quality_cells": {"q3": 21, "q4": 23},
            "avg_cells": {"q5": 3.25},
            "quality_values": {"q5": 133_860},
            "split_counts": {"white": 5, "green": 4},
            "split_quality_cells": {"white": 8, "green": 11},
        },
        "public_info_rows": rows,
    }


def _snapshot(
    category_total: object = _ABSENT,
    *,
    row: dict[str, object] | None = None,
    phase: str = "bidding",
) -> dict[str, object]:
    snapshot = _artifact(category_total, row=row, phase=phase)
    snapshot["ui_contract"] = ui_contract_from_artifact(snapshot)
    return snapshot


def _run(snapshot: dict[str, object], *, max_combos: int = 8_000) -> dict:
    prepared = prepare_reference_engine_snapshot(copy.deepcopy(snapshot))
    return run_reference_engine(prepared, max_combos=max_combos).as_dict()


def _quotes(result: dict) -> list[int]:
    return [result[key] for key in ("conservative", "balanced", "aggressive")]


def _assert_no_op_fields(left: dict, right: dict) -> None:
    for field in _NO_OP_FIELDS:
        assert left[field] == right[field], field


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _field_varint(field_no: int, value: int) -> bytes:
    return _varint((field_no << 3) | 0) + _varint(value)


def _field_bytes(field_no: int, value: bytes) -> bytes:
    return _varint((field_no << 3) | 2) + _varint(len(value)) + value


def _field_fixed32(field_no: int, value: float) -> bytes:
    return _varint((field_no << 3) | 5) + struct.pack("<f", value)


def _wire_artifact(block: bytes) -> tuple[object, dict[str, object]]:
    info = _parse_public_info(block)
    state = FatbeansStateEvent(
        sort_id=1,
        capture_time="truth-free",
        message_id=0x21,
        session_id="5603:truth-free-public-category-regression",
        map_id=5603,
        round_index=3,
        public_infos=() if info is None else (info,),
    )
    events = FatbeansCaptureEvents(
        packets=(),
        frames=(),
        sends=(),
        states=(state,),
        statuses=(),
    )
    artifact = _artifact()
    artifact["public_info_rows"] = _public_info_rows(
        events,
        {},
        session_id=state.session_id,
    )
    artifact["ui_contract"] = ui_contract_from_artifact(artifact)
    return info, artifact


def test_valid_wire_200044_reaches_runtime_and_engine_as_exact_value() -> None:
    block = (
        _field_varint(1, _CATEGORY_INFO_ID)
        + _field_varint(3, 5603)
        + _field_varint(12, _OBSERVED_CATEGORY_TOTAL)
    )

    info, snapshot = _wire_artifact(block)

    assert info is not None
    assert type(info.info_id) is int  # noqa: E721 - exact wire scalar contract
    assert type(info.value) is int  # noqa: E721 - exact wire scalar contract
    assert info.info_id == _CATEGORY_INFO_ID
    assert info.value == _OBSERVED_CATEGORY_TOTAL
    public_info = snapshot["ui_contract"]["constraints"]["public_info"]
    assert public_info["public_exact_subset_values"] == [
        {
            "info_id": _CATEGORY_INFO_ID,
            "semantic": _CATEGORY_SEMANTIC,
            "kind": "exact_subset_value",
            "label": _CATEGORY_LABEL,
            "value": float(_OBSERVED_CATEGORY_TOTAL),
            "display_value": "868,607",
            "text": "竞拍类型总价 868,607",
        }
    ]
    assert extract_evidence(snapshot).public_category_total_value_floor == float(
        _OBSERVED_CATEGORY_TOTAL
    )


@pytest.mark.parametrize(
    ("block", "parsed_value"),
    (
        (
            _field_fixed32(1, 200_044.9)
            + _field_varint(3, 5603)
            + _field_varint(12, _OBSERVED_CATEGORY_TOTAL),
            None,
        ),
        (
            _field_varint(1, _CATEGORY_INFO_ID)
            + _field_varint(3, 5603)
            + _field_bytes(12, b"868607"),
            0,
        ),
        (
            _field_varint(1, _CATEGORY_INFO_ID)
            + _field_varint(3, 5603)
            + _field_fixed32(12, math.inf),
            0,
        ),
    ),
    ids=("float-info-id", "bytes-value", "infinite-fixed32-value"),
)
def test_wire_wrong_types_cannot_become_category_floor(
    block: bytes,
    parsed_value: int | None,
) -> None:
    info, snapshot = _wire_artifact(block)

    if parsed_value is None:
        assert info is None
    else:
        assert info is not None
        assert type(info.value) is int  # noqa: E721 - exact wire scalar contract
        assert info.value == parsed_value
    public_info = snapshot["ui_contract"]["constraints"]["public_info"]
    assert public_info["public_exact_subset_values"] == []
    assert extract_evidence(snapshot).public_category_total_value_floor == 0


@pytest.mark.parametrize(
    "row",
    (
        {"info_id": 200_044.9, "value": _OBSERVED_CATEGORY_TOTAL},
        {"info_id": _CATEGORY_INFO_ID, "value": "868607"},
        {"info_id": _CATEGORY_INFO_ID, "value": math.inf},
    ),
    ids=("float-info-id", "string-value", "infinite-value"),
)
def test_json_snapshot_invalid_present_values_fail_closed_without_projection(
    row: dict[str, object],
) -> None:
    artifact = _artifact(row=row)

    direct_projection = _ui_public_numeric_contract(artifact["public_info_rows"])
    contract = ui_contract_from_artifact(artifact)
    artifact["ui_contract"] = contract

    assert direct_projection["public_exact_subset_values"] == []
    assert direct_projection["public_numeric_summary"] == ""
    assert contract["constraints"]["public_info"][
        "public_exact_subset_values"
    ] == []
    assert contract["diagnostics"]["rare_signals"]["public_info"] == []
    assert extract_evidence(artifact).public_category_total_value_floor == 0


class _ExplodingScalar:
    calls = 0

    @classmethod
    def _explode(cls) -> None:
        cls.calls += 1
        raise AssertionError("scalar protocol must not run")

    def __eq__(self, other: object) -> bool:
        self._explode()

    def __float__(self) -> float:
        self._explode()

    def __int__(self) -> int:
        self._explode()

    def __str__(self) -> str:
        self._explode()


@pytest.mark.parametrize(
    "row",
    (
        {
            "info_id": _ExplodingScalar(),
            "semantic": _CATEGORY_SEMANTIC,
            "value": _OBSERVED_CATEGORY_TOTAL,
        },
        {"semantic": _ExplodingScalar(), "value": _OBSERVED_CATEGORY_TOTAL},
        {"info_id": _CATEGORY_INFO_ID, "value": _ExplodingScalar()},
    ),
    ids=("info-id", "semantic", "value"),
)
def test_invalid_protocol_scalars_are_zero_magic_and_noop(
    row: dict[str, object],
) -> None:
    _ExplodingScalar.calls = 0
    artifact = _artifact(row=row)

    projection = _ui_public_numeric_contract(artifact["public_info_rows"])
    contract = ui_contract_from_artifact(artifact)
    artifact["ui_contract"] = contract
    evidence = extract_evidence(artifact)

    assert projection["public_exact_subset_values"] == []
    assert contract["constraints"]["public_info"][
        "public_exact_subset_values"
    ] == []
    assert evidence.public_category_total_value_floor == 0
    assert _ExplodingScalar.calls == 0


class _IntSubclass(int):
    pass


class _FloatSubclass(float):
    pass


@pytest.mark.parametrize(
    "row",
    (
        {"info_id": True, "value": _OBSERVED_CATEGORY_TOTAL},
        {"info_id": "200044", "value": _OBSERVED_CATEGORY_TOTAL},
        {"info_id": _IntSubclass(_CATEGORY_INFO_ID), "value": 868_607},
        {"info_id": _CATEGORY_INFO_ID, "value": True},
        {"info_id": _CATEGORY_INFO_ID, "value": math.nan},
        {"info_id": _CATEGORY_INFO_ID, "value": -1},
        {"info_id": _CATEGORY_INFO_ID, "value": _IntSubclass(868_607)},
        {"info_id": _CATEGORY_INFO_ID, "value": _FloatSubclass(868_607.0)},
        {"info_id": _CATEGORY_INFO_ID, "value": 10**1000},
        {
            "info_id": 299_999,
            "semantic": _CATEGORY_SEMANTIC,
            "value": _OBSERVED_CATEGORY_TOTAL,
        },
        {
            "info_id": 200_044.9,
            "semantic": _CATEGORY_SEMANTIC,
            "value": _OBSERVED_CATEGORY_TOTAL,
        },
        {"semantic": str(_CATEGORY_SEMANTIC), "value": 0},
    ),
    ids=(
        "bool-info-id",
        "string-info-id",
        "info-id-subclass",
        "bool-value",
        "nan-value",
        "negative-value",
        "int-value-subclass",
        "float-value-subclass",
        "overflowing-int-value",
        "conflicting-info-id",
        "invalid-present-does-not-fallback",
        "semantic-zero-value",
    ),
)
def test_other_invalid_category_rows_are_noop(row: dict[str, object]) -> None:
    artifact = _artifact(row=row)
    artifact["ui_contract"] = ui_contract_from_artifact(artifact)

    assert artifact["ui_contract"]["constraints"]["public_info"][
        "public_exact_subset_values"
    ] == []
    assert extract_evidence(artifact).public_category_total_value_floor == 0


def test_absent_id_accepts_only_exact_canonical_semantic() -> None:
    valid = _snapshot(
        row={"semantic": _CATEGORY_SEMANTIC, "value": _OBSERVED_CATEGORY_TOTAL}
    )
    invalid = _snapshot(
        row={"semantic": f" {_CATEGORY_SEMANTIC}", "value": _OBSERVED_CATEGORY_TOTAL}
    )

    assert extract_evidence(valid).public_category_total_value_floor == float(
        _OBSERVED_CATEGORY_TOTAL
    )
    assert extract_evidence(invalid).public_category_total_value_floor == 0


def test_threshold_matrix_is_posterior_noop_and_quote_monotonic() -> None:
    baseline = _run(_snapshot())
    results = {value: _run(_snapshot(value)) for value in _THRESHOLD_VALUES}

    assert _quotes(baseline) == [1_180_266, 1_270_962, 1_361_658]
    assert {
        value: _quotes(result) for value, result in results.items()
    } == {
        868_607: [1_239_046, 1_329_742, 1_420_437],
        999_999: [1_249_558, 1_340_253, 1_430_949],
        1_000_000: [1_249_558, 1_340_253, 1_430_949],
        1_000_001: [1_249_558, 1_340_253, 1_430_949],
    }
    for value, result in results.items():
        _assert_no_op_fields(result, baseline)
        assert result["evidence"]["public_category_total_value_floor"] == float(
            value
        )
        assert _quotes(result) == sorted(_quotes(result))
    for lower, upper in zip(
        (baseline, *(results[value] for value in _THRESHOLD_VALUES[:-1])),
        (results[value] for value in _THRESHOLD_VALUES),
    ):
        assert all(
            lower_quote <= upper_quote
            for lower_quote, upper_quote in zip(_quotes(lower), _quotes(upper))
        )
    for lower_value, upper_value in ((999_999, 1_000_000), (1_000_000, 1_000_001)):
        assert all(
            0 <= upper - lower <= 1
            for lower, upper in zip(
                _quotes(results[lower_value]),
                _quotes(results[upper_value]),
            )
        )


def test_named_rollback_exactly_restores_absent_200044(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    absent = _run(_snapshot())
    monkeypatch.setenv(BIDKING_DISABLE_PUBLIC_CATEGORY_TOTAL_VALUE_FLOOR, "1")
    rollback = _run(_snapshot(_OBSERVED_CATEGORY_TOTAL))

    assert rollback == absent


def test_settlement_keeps_public_fact_as_label_without_truth_injection() -> None:
    snapshot = _snapshot(_OBSERVED_CATEGORY_TOTAL, phase="settled")
    public_info = snapshot["ui_contract"]["constraints"]["public_info"]

    assert public_info["public_numeric_summary"] == "竞拍类型总价 868,607"
    assert public_info["public_exact_subset_values"][0]["kind"] == (
        "exact_subset_value"
    )
    assert snapshot["ui_contract"]["truth"]["available"] is False
    assert "truth" not in snapshot
    assert "final_value" not in snapshot
    assert "settlement_value" not in snapshot


def test_summary_api_and_tk_publish_the_same_floor_quotes_and_source(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(_OBSERVED_CATEGORY_TOTAL)
    original = copy.deepcopy(snapshot)
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(
        json.dumps(snapshot, ensure_ascii=False),
        encoding="utf-8",
    )

    summary = summarize_snapshot(snapshot, snapshot_path=snapshot_path)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(
            ServerConfig(project_root=tmp_path, snapshot=Path("snapshot.json"))
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(  # noqa: S310 - self-created loopback product contract
            f"http://127.0.0.1:{server.server_port}/api/latest",
            timeout=30,
        ) as response:
            assert response.status == 200
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)

    tk_row = _summary_diagnostic_row(
        payload,
        snapshot_path=snapshot_path,
        render_mode="live",
        manual_active=False,
        settlement_values_hidden=False,
    )
    expected_quotes = _quotes(summary["ahmed_ref"])

    assert snapshot == original
    assert summary["ahmed_ref"]["evidence"][
        "public_category_total_value_floor"
    ] == float(_OBSERVED_CATEGORY_TOTAL)
    assert payload["ahmed_ref"]["evidence"][
        "public_category_total_value_floor"
    ] == float(_OBSERVED_CATEGORY_TOTAL)
    floor_note = "public_info_200044_category_total_value_floor:868607"
    assert floor_note in summary["ahmed_ref"]["evidence"]["source_notes"]
    assert floor_note in payload["ahmed_ref"]["evidence"]["source_notes"]
    assert floor_note in tk_row["ref_v0"]["evidence"]["source_notes"]
    assert _quotes(payload["ahmed_ref"]) == expected_quotes
    assert [
        tk_row["reference"][key]
        for key in ("conservative", "balanced", "aggressive")
    ] == [f"{value:,}" for value in expected_quotes]
    assert summary["context"]["warehouse_source_qualification"] == (
        "official_random_warehouse"
    )
    assert summary["context"]["warehouse_source"] == _warehouse_source()
    assert summary["context"]["warehouse_source"][
        "official_distribution_eligible"
    ] is True
    assert summary["context"]["warehouse_source"]["quote_policy"] == (
        "advisory_only_no_clamp"
    )


_FRESH_PROCESS_RUNNER = r"""
import copy
import json
import sys
from pathlib import Path

root = Path.cwd()
sys.path.insert(0, str(root / "hero_ref" / "src"))
from ahmad_ref_engine import prepare_reference_engine_snapshot, run_reference_engine

payload = json.load(sys.stdin)
max_combos = int(sys.argv[1])

def project(snapshot):
    result = run_reference_engine(
        prepare_reference_engine_snapshot(copy.deepcopy(snapshot)),
        max_combos=max_combos,
    ).as_dict()
    return {
        "quotes": [
            result[key]
            for key in ("conservative", "balanced", "aggressive")
        ],
        "floor": result["evidence"]["public_category_total_value_floor"],
        "stable": {
            key: result[key]
            for key in (
                "combo_count",
                "value_p25",
                "value_p50",
                "value_p75",
                "quality_count_ranges",
                "quality_cells_ranges",
                "quality_count_candidates",
                "quality_cells_candidates",
                "quality_count_cells_candidates",
                "quality_lock",
            )
        },
    }

print(json.dumps({
    "with_floor": project(payload["with_floor"]),
    "without_floor": project(payload["without_floor"]),
}, sort_keys=True))
"""


def _fresh(
    *,
    max_combos: int,
    rollback: bool,
) -> dict:
    env = os.environ.copy()
    if rollback:
        env[BIDKING_DISABLE_PUBLIC_CATEGORY_TOTAL_VALUE_FLOOR] = "1"
    else:
        env.pop(BIDKING_DISABLE_PUBLIC_CATEGORY_TOTAL_VALUE_FLOOR, None)
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and local source
        [sys.executable, "-B", "-c", _FRESH_PROCESS_RUNNER, str(max_combos)],
        cwd=ROOT,
        env=env,
        input=json.dumps(
            {
                "with_floor": _snapshot(_OBSERVED_CATEGORY_TOTAL),
                "without_floor": _snapshot(),
            }
        ),
        text=True,
        capture_output=True,
        check=True,
        timeout=240,
    )
    assert completed.stderr == ""
    return json.loads(completed.stdout)


@pytest.mark.parametrize("max_combos", (8_000, 50_000))
def test_fresh_process_8k_50k_preserves_posterior_and_exact_rollback(
    max_combos: int,
) -> None:
    current = _fresh(max_combos=max_combos, rollback=False)
    rollback = _fresh(max_combos=max_combos, rollback=True)

    assert current["with_floor"]["stable"] == current["without_floor"]["stable"]
    assert current["with_floor"]["stable"] == rollback["with_floor"]["stable"]
    assert rollback["with_floor"] == rollback["without_floor"]
    assert current["without_floor"] == rollback["without_floor"]
    assert current["with_floor"]["floor"] == float(_OBSERVED_CATEGORY_TOTAL)
    assert current["without_floor"]["floor"] == 0
    assert min(current["with_floor"]["quotes"]) >= _OBSERVED_CATEGORY_TOTAL
    assert current["with_floor"]["quotes"] == sorted(
        current["with_floor"]["quotes"]
    )
