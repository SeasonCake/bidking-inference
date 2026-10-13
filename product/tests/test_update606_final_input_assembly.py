from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load(relative: str, name: str) -> ModuleType:
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _builder() -> ModuleType:
    return _load(
        "scripts/calibration/assemble_update606_final_inputs.py",
        "update606_final_input_assembly",
    )


def _checker() -> ModuleType:
    return _load(
        "scripts/check_update606_final_inputs.py",
        "update606_final_input_checker",
    )


def _distribution_fixture() -> tuple[dict[str, bytes], bytes, set[int]]:
    droppable = [{"item_id": item_id} for item_id in range(1, 681)]
    weights = {
        str(map_id): {"items": {"1": [1.0, 1, 1]}}
        for map_id in range(2101, 2202)
    }
    priors = {str(map_id): {} for map_id in range(2101, 2173)}
    presolve = {
        "maps": {str(map_id): {} for map_id in range(2101, 2224)},
    }
    outputs = {
        "drop_weights_all_maps_0627.json": json.dumps(weights).encode(),
        "drop_table_priors_0627.json": json.dumps(priors).encode(),
        "quality_combo_presolve_q456.json": json.dumps(presolve).encode(),
    }
    return outputs, json.dumps(droppable).encode(), set(range(2101, 2224))


def test_independent_distribution_authorities_keep_distinct_map_sets() -> None:
    module = _builder()
    outputs, droppable, allowlist = _distribution_fixture()

    receipt = module._validate_distribution(  # noqa: SLF001
        outputs,
        droppable,
        allowed_map_ids=allowlist,
    )

    assert receipt["approved_droppable"] == {
        "rows": 680,
        "ids_sha256": receipt["approved_droppable"]["ids_sha256"],
        "regenerated_from_raw_drop": False,
    }
    assert {
        name: value["maps"]
        for name, value in receipt["independent_map_authorities"].items()
    } == {"presolve": 123, "priors": 72, "weights": 101}


def test_distribution_rejects_item_outside_approved_680() -> None:
    module = _builder()
    outputs, droppable, allowlist = _distribution_fixture()
    weights = json.loads(outputs["drop_weights_all_maps_0627.json"])
    weights["2101"]["items"]["681"] = [1.0, 1, 1]
    outputs["drop_weights_all_maps_0627.json"] = json.dumps(weights).encode()

    with pytest.raises(module.FinalInputAssemblyError, match="outside approved 680"):
        module._validate_distribution(  # noqa: SLF001
            outputs,
            droppable,
            allowed_map_ids=allowlist,
        )


@pytest.mark.parametrize("denied", [5506, 6001])
def test_distribution_rejects_hard_excluded_map(denied: int) -> None:
    module = _builder()
    outputs, droppable, allowlist = _distribution_fixture()
    priors = json.loads(outputs["drop_table_priors_0627.json"])
    priors.pop(next(iter(priors)))
    priors[str(denied)] = {}
    outputs["drop_table_priors_0627.json"] = json.dumps(priors).encode()

    with pytest.raises(module.FinalInputAssemblyError, match="stable-route authority"):
        module._validate_distribution(  # noqa: SLF001
            outputs,
            droppable,
            allowed_map_ids=allowlist | {denied},
        )


def test_checker_rejects_writable_input_manifest(tmp_path: Path) -> None:
    module = _checker()
    (tmp_path / "input_manifest.json").write_text("{}", encoding="utf-8")

    with pytest.raises(module.FinalInputCheckError, match="read-only regular"):
        module.verify(tmp_path)


def test_checker_success_receipt_reuses_validated_workflow_count() -> None:
    source = (ROOT / "scripts/check_update606_final_inputs.py").read_text(
        encoding="utf-8"
    )

    assert '"count": workflow_contract["count"]' in source
    assert '"count": 11' not in source
