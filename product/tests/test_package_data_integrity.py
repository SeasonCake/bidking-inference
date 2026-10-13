from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_package_data_integrity.py"
MANIFEST = ROOT / "scripts" / "package_data_manifest.json"
FINGERPRINTS = ROOT / "scripts" / "package_data_fingerprints.json"
ENGINE = ROOT / "hero_ref" / "src" / "ahmad_ref_engine.py"
PARENT_VERIFIER_COMMIT = "76499d5a298747fac877ac0fd932259b086764b5"
S1_PARENT_VERIFIER_COMMIT = "ef8ef4fbf20887168d8d39da45960b92a7ef93c0"
S2_PARENT_VERIFIER_COMMIT = "712036a2a60c65835a430500cf3b6541ca0fed38"
PYTHON313 = Path("C:/Python313/python.exe")

_SPEC = importlib.util.spec_from_file_location("check_package_data_integrity", SCRIPT)
assert _SPEC and _SPEC.loader
_INTEGRITY = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_INTEGRITY)


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    script = repo / "scripts" / SCRIPT.name
    script.parent.mkdir(parents=True)
    shutil.copy2(SCRIPT, script)
    shutil.copy2(MANIFEST, repo / "scripts" / MANIFEST.name)
    engine = repo / "hero_ref" / "src" / ENGINE.name
    engine.parent.mkdir(parents=True)

    files: dict[str, dict[str, object]] = {}
    for index, rel in enumerate(_INTEGRITY.SHIPPED):
        payload = f"known-good-{index}:{rel}\n".encode()
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        files[rel] = {
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        }

    baseline_id = "test-approved-baseline"
    rankmap_generation = "test-current-rankmap-generation"
    authority_rel = "data/processed/rankmap_distribution_authority_current.json"
    authority_payload = json.dumps(
        {"package_generation": rankmap_generation},
        separators=(",", ":"),
    ).encode()
    (repo / authority_rel).write_bytes(authority_payload)
    files[authority_rel] = {
        "sha256": hashlib.sha256(authority_payload).hexdigest(),
        "size": len(authority_payload),
    }
    _, _, current_rankmap_files = _INTEGRITY._load_engine_package_contract(ENGINE)
    assert set(current_rankmap_files) < set(files)
    sidecar_rel = "data/processed/auction_hall_6001_shadow_distribution.json"
    sidecar = files[sidecar_rel]
    activity_rel = "data/processed/shipwreck_calibrated_b049.json"
    activity = files[activity_rel]
    engine.write_text(
        'AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID = (\n'
        f'    "{baseline_id}"\n'
        ")\n"
        f'CURRENT_RANKMAP_PACKAGE_GENERATION = "{rankmap_generation}"\n'
        f'AUCTION_HALL_6001_SHADOW_RELATIVE = "{sidecar_rel}"\n'
        f"AUCTION_HALL_6001_SHADOW_BYTES = {sidecar['size']}\n"
        f'AUCTION_HALL_6001_SHADOW_SHA256 = "{sidecar["sha256"]}"\n'
        f'SHIPWRECK_CALIBRATED_RELATIVE = "{activity_rel}"\n'
        f'SHIPWRECK_CALIBRATED_BYTES = {activity["size"]}\n'
        f'SHIPWRECK_CALIBRATED_SHA256 = "{activity["sha256"]}"\n'
        f"_CURRENT_RANKMAP_GENERATION_FILES = {repr({rel: (files[rel]['size'], files[rel]['sha256']) for rel in current_rankmap_files})}\n",
        encoding="utf-8",
    )

    manifest_hash = hashlib.sha256(
        (repo / "scripts" / MANIFEST.name).read_bytes()
    ).hexdigest()
    (repo / "scripts" / FINGERPRINTS.name).write_text(
        json.dumps(
            {
                "schema": "bidking_package_data_fingerprints_v1",
                "baseline_id": baseline_id,
                "manifest": "scripts/package_data_manifest.json",
                "manifest_sha256": manifest_hash,
                "source_validation": {
                    "package_fingerprint": _INTEGRITY._fingerprint_records(files),
                },
                "files": files,
            }
        ),
        encoding="utf-8",
    )
    return repo


def _verify(
    repo: Path,
    *,
    encoding: str = "utf-8",
    executable: str | Path = sys.executable,
) -> subprocess.CompletedProcess[bytes]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = encoding
    return subprocess.run(
        [str(executable), str(repo / "scripts" / SCRIPT.name), "--verify"],
        cwd=repo,
        env=env,
        capture_output=True,
        check=False,
    )


def _list_json(repo: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, str(repo / "scripts" / SCRIPT.name), "--list-json"],
        cwd=repo,
        capture_output=True,
        check=False,
    )


def _run_parent_verifier(
    repo: Path,
    *,
    commit: str = PARENT_VERIFIER_COMMIT,
    executable: str | Path = sys.executable,
) -> subprocess.CompletedProcess[bytes]:
    """Run the audited parent checker over the same synthetic repository."""
    parent = subprocess.run(
        [
            "git",
            "show",
            f"{commit}:scripts/check_package_data_integrity.py",
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    assert parent.returncode == 0, parent.stdout + parent.stderr
    checker = repo / "scripts" / SCRIPT.name
    successor = checker.read_bytes()
    try:
        checker.write_bytes(parent.stdout)
        return _verify(repo, executable=executable)
    finally:
        checker.write_bytes(successor)


def _engine_path(repo: Path) -> Path:
    return repo / "hero_ref" / "src" / ENGINE.name


def _replace_engine_text(repo: Path, old: str, new: str) -> None:
    engine = _engine_path(repo)
    source = engine.read_text(encoding="utf-8")
    assert source.count(old) == 1
    engine.write_text(source.replace(old, new, 1), encoding="utf-8")


def _set_item_member_size_reference(repo: Path, definition: str) -> None:
    engine = _engine_path(repo)
    _baseline_id, _generation, members = _INTEGRITY._load_engine_package_contract(engine)
    size, digest = members["data/raw/tables/Item.txt"]
    source = engine.read_text(encoding="utf-8")
    definition = definition.format(size=size)
    source = source.replace(
        "_CURRENT_RANKMAP_GENERATION_FILES =",
        definition + "\n_CURRENT_RANKMAP_GENERATION_FILES =",
        1,
    )
    record = repr((size, digest))
    assert source.count(record) == 1
    source = source.replace(
        record,
        f"(AISHA_Q5_TOTAL_CELLS_ITEM_BYTES, {digest!r})",
        1,
    )
    engine.write_text(source, encoding="utf-8")


def test_known_good_baseline_passes(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    result = _verify(repo)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("field", ("relative", "size", "sha256"))
def test_auction_hall_6001_engine_identity_drift_fails_closed(
    tmp_path: Path,
    field: str,
) -> None:
    repo = _make_repo(tmp_path)
    engine = _engine_path(repo)
    relative, size, digest = _INTEGRITY._load_engine_auction_hall_6001_contract(
        engine
    )
    if field == "relative":
        _replace_engine_text(repo, relative, relative + ".wrong")
    elif field == "size":
        _replace_engine_text(
            repo,
            f"AUCTION_HALL_6001_SHADOW_BYTES = {size}",
            f"AUCTION_HALL_6001_SHADOW_BYTES = {size + 1}",
        )
    else:
        _replace_engine_text(repo, digest, "0" * 64)

    result = _verify(repo)

    assert result.returncode == 2
    assert "6001" in result.stdout.decode("utf-8")


@pytest.mark.parametrize("field", ("relative", "size", "sha256"))
def test_shipwreck_calibrated_engine_identity_drift_fails_closed(tmp_path: Path, field: str) -> None:
    repo = _make_repo(tmp_path)
    relative, size, digest = _INTEGRITY._load_engine_shipwreck_calibrated_contract(_engine_path(repo))
    if field == "relative":
        _replace_engine_text(repo, relative, relative + ".wrong")
    elif field == "size":
        _replace_engine_text(repo, f"SHIPWRECK_CALIBRATED_BYTES = {size}",
                             f"SHIPWRECK_CALIBRATED_BYTES = {size + 1}")
    else:
        _replace_engine_text(repo, digest, "0" * 64)
    result = _verify(repo)
    assert result.returncode == 2
    assert "沉船校准" in result.stdout.decode("utf-8")


def test_recorded_package_fingerprint_drift_fails_closed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    baseline = repo / "scripts" / FINGERPRINTS.name
    document = json.loads(baseline.read_text(encoding="utf-8"))
    document["source_validation"]["package_fingerprint"] = "0" * 64
    baseline.write_text(json.dumps(document), encoding="utf-8")

    result = _verify(repo)

    assert result.returncode == 2
    assert "package_fingerprint" in result.stdout.decode("utf-8")


def test_changing_any_shipped_file_fails_closed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)

    for rel in _INTEGRITY.SHIPPED:
        target = repo / rel
        original = target.read_bytes()
        target.write_bytes(original + b"changed")
        try:
            result = _verify(repo)
        finally:
            target.write_bytes(original)
        assert result.returncode == 1, (rel, result.stdout + result.stderr)


def test_machine_readable_copy_list_matches_frozen_manifest(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)

    result = _list_json(repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == list(_INTEGRITY.SHIPPED)


def test_baseline_and_manifest_path_sets_must_match_exactly(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    baseline = repo / "scripts" / FINGERPRINTS.name
    value = json.loads(baseline.read_text(encoding="utf-8"))

    _, _, current_rankmap_files = _INTEGRITY._load_engine_package_contract(
        repo / "hero_ref" / "src" / ENGINE.name
    )
    removed_rel = next(rel for rel in value["files"] if rel not in current_rankmap_files)
    removed_value = value["files"].pop(removed_rel)
    value["source_validation"]["package_fingerprint"] = (
        _INTEGRITY._fingerprint_records(value["files"])
    )
    baseline.write_text(json.dumps(value), encoding="utf-8")
    missing_result = _verify(repo)
    assert missing_result.returncode == 1, missing_result.stdout + missing_result.stderr

    value["files"][removed_rel] = removed_value
    value["files"]["data/processed/historical_extra.json"] = {
        "sha256": "0" * 64,
        "size": 1,
    }
    value["source_validation"]["package_fingerprint"] = (
        _INTEGRITY._fingerprint_records(value["files"])
    )
    baseline.write_text(json.dumps(value), encoding="utf-8")
    extra_result = _verify(repo)
    assert extra_result.returncode == 1, extra_result.stdout + extra_result.stderr


def test_missing_required_shipped_file_fails_closed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / _INTEGRITY.SHIPPED[-1]).unlink()

    result = _verify(repo)

    assert result.returncode == 1, result.stdout + result.stderr


@pytest.mark.parametrize("magic", [b"BKENC1", b"BKENC2"])
def test_bkenc_source_pollution_fails_without_a_baseline_change(
    tmp_path: Path,
    magic: bytes,
) -> None:
    repo = _make_repo(tmp_path)
    (repo / _INTEGRITY.SHIPPED[0]).write_bytes(magic + b"encrypted-package-data")

    result = _verify(repo)

    assert result.returncode == 1, result.stdout + result.stderr


def test_missing_baseline_fails_loud_instead_of_snapshotting(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    baseline = repo / "scripts" / FINGERPRINTS.name
    baseline.unlink()

    result = _verify(repo)

    assert result.returncode == 2, result.stdout + result.stderr
    assert not baseline.exists()


def test_snapshot_creates_review_candidate_without_overwriting_approved_baseline(
    tmp_path: Path,
) -> None:
    repo = _make_repo(tmp_path)
    baseline = repo / "scripts" / FINGERPRINTS.name
    before = baseline.read_bytes()

    result = subprocess.run(
        [sys.executable, str(repo / "scripts" / SCRIPT.name), "--snapshot"],
        cwd=repo,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert baseline.read_bytes() == before
    candidate = repo / ".package_data_fingerprints.candidate.json"
    assert candidate.is_file()
    value = json.loads(candidate.read_text(encoding="utf-8"))
    assert value["schema"] == "bidking_package_data_fingerprints_candidate_v1"
    assert value["approved_baseline"] == "scripts/package_data_fingerprints.json"
    assert value["files"]


def test_manifest_hash_change_requires_separately_reviewed_baseline(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    manifest = repo / "scripts" / MANIFEST.name
    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["review_probe"] = True
    manifest.write_text(json.dumps(value), encoding="utf-8")

    result = _verify(repo)

    assert result.returncode == 2, result.stdout + result.stderr


def test_stale_engine_or_manifest_baseline_fails_before_member_verification(
    tmp_path: Path,
) -> None:
    repo = _make_repo(tmp_path)
    baseline = repo / "scripts" / FINGERPRINTS.name
    value = json.loads(baseline.read_text(encoding="utf-8"))
    value["baseline_id"] = "stale-baseline-control"
    baseline.write_text(json.dumps(value), encoding="utf-8")

    result = _verify(repo)

    assert result.returncode == 2, result.stdout + result.stderr


@pytest.mark.parametrize("mode", ["missing", "malformed", "dynamic", "duplicate"])
def test_engine_baseline_constant_must_be_unique_static_and_readable(
    tmp_path: Path,
    mode: str,
) -> None:
    repo = _make_repo(tmp_path)
    engine = repo / "hero_ref" / "src" / ENGINE.name
    baseline_id = _INTEGRITY._load_engine_package_baseline_id(engine)
    source = engine.read_text(encoding="utf-8")
    assignment = (
        'AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID = (\n'
        f'    "{baseline_id}"\n'
        ")"
    )
    assert source.count(assignment) == 1
    if mode == "missing":
        engine.unlink()
    elif mode == "malformed":
        engine.write_text("if (\n", encoding="utf-8")
    elif mode == "dynamic":
        engine.write_text(
            source.replace(
                assignment,
                "AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID = str(object())",
                1,
            ),
            encoding="utf-8",
        )
    else:
        engine.write_text(source + "\n" + assignment, encoding="utf-8")

    result = _verify(repo)

    assert result.returncode == 2, result.stdout + result.stderr


def test_parent_accepts_rebound_baseline_but_successor_rejects_exact_repro(
    tmp_path: Path,
) -> None:
    repo = _make_repo(tmp_path)
    baseline_id = _INTEGRITY._load_engine_package_baseline_id(_engine_path(repo))
    assignment = (
        'AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID = (\n'
        f'    "{baseline_id}"\n'
        ")"
    )
    _replace_engine_text(
        repo,
        assignment,
        'BASE = "test-approved-baseline"\n'
        'BASE = str("runtime-spoof")\n'
        "AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID = BASE",
    )

    parent = _run_parent_verifier(repo, commit=S1_PARENT_VERIFIER_COMMIT)
    successor = _verify(repo)

    assert parent.returncode == 0, parent.stdout + parent.stderr
    assert successor.returncode == 2, successor.stdout + successor.stderr


def test_parent_accepts_rebound_member_size_but_successor_rejects_exact_repro(
    tmp_path: Path,
) -> None:
    repo = _make_repo(tmp_path)
    _set_item_member_size_reference(
        repo,
        "AISHA_Q5_TOTAL_CELLS_ITEM_BYTES = {size}\n"
        "AISHA_Q5_TOTAL_CELLS_ITEM_BYTES = bool(1)",
    )

    parent = _run_parent_verifier(repo, commit=S1_PARENT_VERIFIER_COMMIT)
    successor = _verify(repo)

    assert parent.returncode == 0, parent.stdout + parent.stderr
    assert successor.returncode == 2, successor.stdout + successor.stderr


def test_parent_accepts_module_if_baseline_rebind_but_successor_rejects(
    tmp_path: Path,
) -> None:
    repo = _make_repo(tmp_path)
    engine = _engine_path(repo)
    engine.write_text(
        engine.read_text(encoding="utf-8")
        + "\nif True:\n"
        + '    AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID = str("runtime-spoof")\n',
        encoding="utf-8",
    )

    parent = _run_parent_verifier(repo)
    successor = _verify(repo)

    assert parent.returncode == 0, parent.stdout + parent.stderr
    assert successor.returncode == 2, successor.stdout + successor.stderr


def test_parent_accepts_module_if_member_bool_rebind_but_successor_rejects(
    tmp_path: Path,
) -> None:
    repo = _make_repo(tmp_path)
    _set_item_member_size_reference(
        repo,
        "AISHA_Q5_TOTAL_CELLS_ITEM_BYTES = {size}",
    )
    engine = _engine_path(repo)
    engine.write_text(
        engine.read_text(encoding="utf-8")
        + "\nif True:\n    AISHA_Q5_TOTAL_CELLS_ITEM_BYTES = True\n",
        encoding="utf-8",
    )

    parent = _run_parent_verifier(repo)
    successor = _verify(repo)

    assert parent.returncode == 0, parent.stdout + parent.stderr
    assert successor.returncode == 2, successor.stdout + successor.stderr


def test_parent_accepts_module_definition_member_rebind_but_successor_rejects(
    tmp_path: Path,
) -> None:
    repo = _make_repo(tmp_path)
    _set_item_member_size_reference(
        repo,
        "AISHA_Q5_TOTAL_CELLS_ITEM_BYTES = {size}",
    )
    engine = _engine_path(repo)
    engine.write_text(
        engine.read_text(encoding="utf-8")
        + "\ndef AISHA_Q5_TOTAL_CELLS_ITEM_BYTES():\n    return 0\n",
        encoding="utf-8",
    )

    parent = _run_parent_verifier(repo)
    successor = _verify(repo)

    assert parent.returncode == 0, parent.stdout + parent.stderr
    assert successor.returncode == 2, successor.stdout + successor.stderr


def test_real_engine_item_member_uses_allowed_single_hop_literal_constants() -> None:
    _baseline_id, _generation, members = _INTEGRITY._load_engine_package_contract()

    assert members["data/raw/tables/Item.txt"] == (
        692_184,
        "cb15d92ba307831d3da42875a6c542b5675baa48e74d37290cfbcd1a86594949",
    )


@pytest.mark.parametrize(
    "definition",
    [
        "AISHA_Q5_TOTAL_CELLS_ITEM_BYTES = str(29)",
        "ALIAS_BYTES = 29\nAISHA_Q5_TOTAL_CELLS_ITEM_BYTES = ALIAS_BYTES",
        "AISHA_Q5_TOTAL_CELLS_ITEM_BYTES = True",
        "AISHA_Q5_TOTAL_CELLS_ITEM_BYTES = 29\nAISHA_Q5_TOTAL_CELLS_ITEM_BYTES = 29",
    ],
    ids=("dynamic", "alias-chain", "bool", "multi-assign"),
)
def test_member_name_must_be_single_hop_unique_direct_literal(
    tmp_path: Path, definition: str
) -> None:
    repo = _make_repo(tmp_path)
    _set_item_member_size_reference(repo, definition)

    result = _verify(repo)

    assert result.returncode == 2, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            'AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID = (\n'
            '    "test-approved-baseline"\n'
            ")",
            "BASE = \"test-approved-baseline\"\n"
            "AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID = BASE",
        ),
        (
            'CURRENT_RANKMAP_PACKAGE_GENERATION = "test-current-rankmap-generation"',
            'CURRENT_RANKMAP_PACKAGE_GENERATION = f"test-current-rankmap-generation"',
        ),
        (
            "'data/raw/tables/Item.txt'",
            "ITEM_LOGICAL_ID = \"data/raw/tables/Item.txt\"\n"
            "ITEM_LOGICAL_ID",
        ),
    ],
    ids=("baseline-alias", "generation-f-string", "logical-id-alias"),
)
def test_trusted_values_must_not_use_aliases_or_expressions(
    tmp_path: Path, old: str, new: str
) -> None:
    repo = _make_repo(tmp_path)
    _replace_engine_text(repo, old, new)

    result = _verify(repo)

    assert result.returncode == 2, result.stdout + result.stderr


def test_undefined_member_literal_name_fails_closed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _set_item_member_size_reference(repo, "")

    result = _verify(repo)

    assert result.returncode == 2, result.stdout + result.stderr


@pytest.mark.parametrize(
    "expression",
    [
        'str("test-approved-baseline")',
        '" test-approved-baseline ".strip()',
        '("test-approved-baseline",)[0]',
        '"test-approved-" + "baseline"',
        'f"test-approved-baseline"',
    ],
    ids=("call", "attribute", "subscript", "binop", "f-string"),
)
def test_trusted_baseline_requires_direct_literal_not_expression(
    tmp_path: Path, expression: str
) -> None:
    repo = _make_repo(tmp_path)
    _replace_engine_text(
        repo,
        'AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID = (\n'
        '    "test-approved-baseline"\n'
        ")",
        f"AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID = {expression}",
    )

    result = _verify(repo)

    assert result.returncode == 2, result.stdout + result.stderr


def test_duplicate_direct_member_logical_id_fails_closed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    engine = _engine_path(repo)
    _baseline_id, _generation, members = _INTEGRITY._load_engine_package_contract(engine)
    assignment = f"_CURRENT_RANKMAP_GENERATION_FILES = {members!r}"
    duplicate = (
        assignment[:-1]
        + f", 'data/raw/tables/Item.txt': {members['data/raw/tables/Item.txt']!r}}}"
    )
    _replace_engine_text(repo, assignment, duplicate)

    result = _verify(repo)

    assert result.returncode == 2, result.stdout + result.stderr


@pytest.mark.parametrize(
    "statement",
    [
        'AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID: str = "test-approved-baseline"',
        "AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID += \"-spoof\"",
        'AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID, other = ("x", "y")',
        "import os as AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID",
        "del AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID",
        "global AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID",
        "nonlocal AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID",
    ],
    ids=("annassign", "augassign", "destructure", "import", "delete", "global", "nonlocal"),
)
def test_trusted_top_level_name_rebinding_forms_fail_closed(
    tmp_path: Path, statement: str
) -> None:
    repo = _make_repo(tmp_path)
    engine = _engine_path(repo)
    engine.write_text(
        engine.read_text(encoding="utf-8") + "\n" + statement + "\n",
        encoding="utf-8",
    )

    result = _verify(repo)

    assert result.returncode == 2, result.stdout + result.stderr


@pytest.mark.parametrize(
    "statement",
    [
        "if True:\n    AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID = \"spoof\"",
        "for AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID in ():\n    pass",
        "while False:\n    AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID = \"spoof\"",
        "try:\n    AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID = \"spoof\"\nexcept Exception:\n    pass",
        "with object() as AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID:\n    pass",
        "try:\n    pass\nexcept Exception as AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID:\n    pass",
        "match 1:\n    case AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID:\n        pass",
        "if (AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID := \"spoof\"):\n    pass",
        "[(AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID := \"spoof\") for _ in ()]",
        "def AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID():\n    return \"spoof\"",
        "class AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID:\n    pass",
        "import os as AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID",
        "del AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID",
    ],
    ids=(
        "if",
        "for",
        "while",
        "try-body",
        "with",
        "except",
        "match",
        "walrus",
        "comprehension-walrus",
        "function",
        "class",
        "import",
        "delete",
    ),
)
def test_module_execution_scope_binders_fail_closed(
    tmp_path: Path, statement: str
) -> None:
    repo = _make_repo(tmp_path)
    engine = _engine_path(repo)
    engine.write_text(
        engine.read_text(encoding="utf-8") + "\n" + statement + "\n",
        encoding="utf-8",
    )

    result = _verify(repo)

    assert result.returncode == 2, result.stdout + result.stderr


def test_function_and_class_body_bindings_are_not_module_rebindings(
    tmp_path: Path,
) -> None:
    repo = _make_repo(tmp_path)
    engine = _engine_path(repo)
    engine.write_text(
        engine.read_text(encoding="utf-8")
        + "\ndef helper():\n"
        + '    AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID = "local"\n'
        + "\nclass Helper:\n"
        + '    AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID = "class-local"\n',
        encoding="utf-8",
    )

    result = _verify(repo)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("source_suffix", "executable"),
    [
        (
            "\nclass GlobalSpoof:\n"
            "    global AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID\n"
            '    AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID = "spoof"\n',
            sys.executable,
        ),
        (
            "\ntype AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID = int\n",
            PYTHON313,
        ),
        ("\nfrom spoof import *\n", sys.executable),
    ],
    ids=("class-global", "type-alias", "import-star"),
)
def test_s2_parent_accepts_s3_controls_but_successor_rejects(
    tmp_path: Path, source_suffix: str, executable: str | Path
) -> None:
    if Path(executable) == PYTHON313 and not PYTHON313.is_file():
        pytest.skip("CPython 3.13 is required for the TypeAlias control")
    repo = _make_repo(tmp_path)
    engine = _engine_path(repo)
    engine.write_text(
        engine.read_text(encoding="utf-8") + source_suffix,
        encoding="utf-8",
    )

    parent = _run_parent_verifier(
        repo,
        commit=S2_PARENT_VERIFIER_COMMIT,
        executable=executable,
    )
    successor = _verify(repo, executable=executable)

    assert parent.returncode == 0, parent.stdout + parent.stderr
    assert successor.returncode == 2, successor.stdout + successor.stderr


def test_async_function_lambda_and_comprehension_locals_are_not_module_binders(
    tmp_path: Path,
) -> None:
    repo = _make_repo(tmp_path)
    engine = _engine_path(repo)
    engine.write_text(
        engine.read_text(encoding="utf-8")
        + "\nasync def helper():\n"
        + '    AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID = "async-local"\n'
        + "\ndef scoped():\n"
        + "    global AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID\n"
        + '    AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID = "function-global"\n'
        + "\n_lambda = lambda: (AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID := \"lambda-local\")\n"
        + "_local_rows = [AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID for AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID in ()]\n",
        encoding="utf-8",
    )

    result = _verify(repo)

    assert result.returncode == 0, result.stdout + result.stderr


def test_stale_engine_member_authority_fails_before_data_copy(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    baseline = repo / "scripts" / FINGERPRINTS.name
    value = json.loads(baseline.read_text(encoding="utf-8"))
    rel = next(iter(value["files"]))
    value["files"][rel]["size"] += 1
    baseline.write_text(json.dumps(value), encoding="utf-8")

    result = _verify(repo)

    assert result.returncode == 2, result.stdout + result.stderr


@pytest.mark.parametrize("mode", ["missing", "extra"])
def test_engine_rankmap_authority_member_set_must_be_exact(
    tmp_path: Path,
    mode: str,
) -> None:
    repo = _make_repo(tmp_path)
    engine = repo / "hero_ref" / "src" / ENGINE.name
    _baseline_id, _generation, members = _INTEGRITY._load_engine_package_contract(
        engine
    )
    assignment = f"_CURRENT_RANKMAP_GENERATION_FILES = {members!r}"
    source = engine.read_text(encoding="utf-8")
    assert source.count(assignment) == 1
    changed = dict(members)
    if mode == "missing":
        changed.pop(next(iter(changed)))
    else:
        changed["data/processed/unapproved_rankmap_authority.json"] = (
            1,
            "0" * 64,
        )
    engine.write_text(
        source.replace(assignment, f"_CURRENT_RANKMAP_GENERATION_FILES = {changed!r}"),
        encoding="utf-8",
    )

    result = _verify(repo)

    assert result.returncode == 2, result.stdout + result.stderr


def test_stale_current_rankmap_generation_fails_before_data_copy(
    tmp_path: Path,
) -> None:
    repo = _make_repo(tmp_path)
    authority = (
        repo / "data" / "processed" / "rankmap_distribution_authority_current.json"
    )
    value = json.loads(authority.read_text(encoding="utf-8"))
    value["package_generation"] = "stale-generation-control"
    payload = json.dumps(value).encode()
    authority.write_bytes(payload)
    baseline = repo / "scripts" / FINGERPRINTS.name
    fingerprints = json.loads(baseline.read_text(encoding="utf-8"))
    rel = authority.relative_to(repo).as_posix()
    old_record = fingerprints["files"][rel]
    new_record = {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
    }
    fingerprints["files"][rel] = new_record
    baseline.write_text(json.dumps(fingerprints), encoding="utf-8")
    engine = repo / "hero_ref" / "src" / ENGINE.name
    source = engine.read_text(encoding="utf-8")
    old_tuple = repr((old_record["size"], old_record["sha256"]))
    new_tuple = repr((new_record["size"], new_record["sha256"]))
    assert source.count(old_tuple) == 1
    engine.write_text(source.replace(old_tuple, new_tuple, 1), encoding="utf-8")

    result = _verify(repo)

    assert result.returncode == 2, result.stdout + result.stderr


def test_verify_does_not_crash_in_gbk_console(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)

    result = _verify(repo, encoding="gbk")

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout
    assert b"UnicodeEncodeError" not in result.stderr


def test_failed_verdict_remains_visible_in_gbk_console(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    target = repo / _INTEGRITY.SHIPPED[0]
    target.write_bytes(target.read_bytes() + b"changed")

    result = _verify(repo, encoding="gbk")

    assert result.returncode == 1
    assert result.stdout
    assert b"UnicodeEncodeError" not in result.stderr


def test_historical_processed_files_are_not_in_copy_manifest(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    historical = (
        "data/processed/activity_drop_shadow_prior.json",
        "data/processed/reconstructed_map_prior_2401.json",
        "data/processed/reconstructed_map_prior_2403.json",
        "data/processed/reconstructed_map_prior_2406.json",
        "data/processed/reconstructed_map_prior_2409.json",
        "data/processed/reconstructed_map_prior_2410.json",
        "data/processed/reconstructed_map_prior_2531.json",
        "data/processed/reconstructed_map_prior_4532.json",
    )
    for rel in historical:
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("historical", encoding="utf-8")

    result = _list_json(repo)
    listed = set(json.loads(result.stdout))

    assert result.returncode == 0
    assert listed == set(_INTEGRITY.SHIPPED)
    assert listed.isdisjoint(historical)
    assert "data/raw/tables/Hero.txt" in listed
    assert "data/raw/tables/Skill.txt" not in listed
    assert "data/processed/upload_config.enc" not in listed


def test_portable_build_runs_verify_before_stage_or_encryption() -> None:
    build = (ROOT / "scripts" / "build_live_portable.ps1").read_text(
        encoding="utf-8-sig"
    )

    integrity_at = build.index(
        '$integrityChecker = Join-Path $Repo "scripts\\check_package_data_integrity.py"'
    )
    stage_at = build.index(
        "New-Item -ItemType Directory -Force -Path $Stage, $Cache, $RuntimePy"
    )
    upload_encrypt_at = build.index(
        '$encHelper = Join-Path $Repo "scripts\\encrypt_upload_config.py"'
    )
    data_copy_at = build.index(
        "Copy-Item -LiteralPath $src -Destination $dst -Force"
    )
    moat_encrypt_at = build.index(
        '$encMoat = Join-Path $Repo "scripts\\encrypt_moat_files.py"'
    )

    assert "--verify" in build[integrity_at:stage_at]
    assert "--list-json" in build[integrity_at:stage_at]
    assert "$packageDataParsed = $packageDataJson | ConvertFrom-Json" in build
    assert "$packageDataFiles = @($packageDataParsed)" in build
    assert "@($packageDataJson | ConvertFrom-Json)" not in build
    assert "& $BuildPython $integrityChecker --snapshot" not in build
    assert "$integrityExit -ne 0" in build[integrity_at:stage_at]
    assert '$env:PYTHONIOENCODING = "utf-8"' in build[integrity_at:stage_at]
    assert '"scripts/check_package_data_integrity.py"' in build[:integrity_at]
    assert '"scripts/package_data_manifest.json"' in build[:integrity_at]
    assert '"scripts/package_data_fingerprints.json"' in build[:integrity_at]
    assert '"PackageDataManifestSHA256: $PackageDataManifestSHA256"' in build
    assert '"PackageDataFingerprintSHA256: $PackageDataFingerprintSHA256"' in build
    assert '"PackageDataBaselineID: $PackageDataBaselineID"' in build
    assert 'Copy-Tree "data\\processed"' not in build
    assert '"Skill.txt"' not in build
    assert "$packageDataDiff.Count -gt 0" in build[data_copy_at:moat_encrypt_at]
    assert "--release-contract $ReleaseZipContractPath" in build[moat_encrypt_at:]
    assert integrity_at < stage_at < upload_encrypt_at < data_copy_at < moat_encrypt_at
    assert integrity_at < build.index("if ($InspectOnly)")


def test_repository_approved_fingerprint_file_matches_current_manifest() -> None:
    value = json.loads(FINGERPRINTS.read_text(encoding="utf-8"))
    baseline_id, rankmap_generation, rankmap_files = (
        _INTEGRITY._load_engine_package_contract()
    )
    rankmap_authority = json.loads(
        (
            ROOT
            / "data"
            / "processed"
            / "rankmap_distribution_authority_current.json"
        ).read_text(encoding="utf-8")
    )

    assert value["schema"] == "bidking_package_data_fingerprints_v1"
    assert value["baseline_id"] == baseline_id
    assert value["manifest"] == "scripts/package_data_manifest.json"
    assert value["manifest_sha256"] == hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
    assert set(value["files"]) == set(_INTEGRITY.SHIPPED)
    assert {
        rel: (value["files"][rel]["size"], value["files"][rel]["sha256"])
        for rel in rankmap_files
    } == rankmap_files
    assert rankmap_authority["package_generation"] == rankmap_generation


def test_package_copy_list_expands_under_windows_powershell_51() -> None:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("Windows PowerShell 5.1 is not installed")

    command = r'''
$raw = '["first","second"]'
$parsed = $raw | ConvertFrom-Json
$files = @($parsed)
if ($files.Count -ne 2) { exit 3 }
foreach ($item in $files) {
    if ($item -isnot [string] -or -not $item) { exit 4 }
}
Write-Output ($files -join ",")
'''
    result = subprocess.run(
        [powershell, "-NoProfile", "-Command", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "first,second" in result.stdout
