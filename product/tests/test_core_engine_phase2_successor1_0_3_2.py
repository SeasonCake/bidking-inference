from __future__ import annotations

import copy
import hashlib
import io
from pathlib import Path
import subprocess
import tarfile

import pytest

from ahmad_ref_engine import (
    extract_evidence,
    prepare_reference_engine_snapshot,
    run_reference_engine,
)
from test_ahmad_ref_engine_avg_value_wire_samples import (
    FATBEANS_SAMPLE_DIR,
    _snapshot_with_public_avg,
)
from test_ahmad_ref_engine_public_info import _aisha_fatbeans_snapshot


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = FATBEANS_SAMPLE_DIR / (
    "fatbeans_valid_aisha_2402_3rounds_2402_1367586310602652_0052.json"
)
SAMPLE_SHA256 = "bca21609bf77db644542b131d873c04df088452eae85d13c745245fb42de2990"
STATIC_DATA = ROOT / (
    "external_references/AuctionAnalyzer4.13.3/_decompiled/MapBidCalculator/"
    "MapBidCalculator/Models/StaticData.cs"
)
STATIC_DATA_SHA256 = "7543b4aa16addc73dfaaaf3c6df9bc0f6662bd83458de110a16d04ff9f2f9676"
LOADER = Path("src/bidking_lab/data/item_category_authority.py")
LOADER_BLOB = "fe7c4799e6155f3b5283d5dfbce0b56569047a38"


def _run(*args: str, cwd: Path, text: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=text,
        encoding="utf-8" if text else None,
        errors="strict" if text else None,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def _wire_snapshot() -> dict:
    assert STATIC_DATA.is_file(), (
        "Aisha 0052 A/B requires the frozen StaticData input; an archive fallback "
        "must fail instead of being reported as product evidence"
    )
    assert hashlib.sha256(STATIC_DATA.read_bytes()).hexdigest() == STATIC_DATA_SHA256
    payload = SAMPLE.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == SAMPLE_SHA256
    return _snapshot_with_public_avg(
        _aisha_fatbeans_snapshot(SAMPLE, round_count=3),
        tier="q4",
        raw_value=6659.21435546875,
    )


def test_aisha_0052_gate_rejects_missing_static_data(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setitem(_wire_snapshot.__globals__, "STATIC_DATA", tmp_path / "missing.cs")
    with pytest.raises(AssertionError, match="requires the frozen StaticData input"):
        _wire_snapshot()


@pytest.mark.parametrize("prepared", [False, True], ids=["direct", "prepared"])
def test_aisha_0052_q4_avg_preserves_split_bridge_in_all_engine_entries(
    prepared: bool,
) -> None:
    snapshot = _wire_snapshot()
    if prepared:
        snapshot = prepare_reference_engine_snapshot(copy.deepcopy(snapshot))

    evidence = extract_evidence(copy.deepcopy(snapshot))
    assert evidence.avg_values == {"q4": 6659.214}
    assert evidence.soft_avg_value_keys == frozenset({"q4"})

    result = run_reference_engine(snapshot, max_combos=50_000).as_dict()
    assert result["status"] == "count_prior"
    assert result["combo_count"] > 0
    assert result["quality_count_ranges"]["q4"] == [4, 6, 9]
    assert "public_q4_avg_value_wire_normalized" in result["notes"]
    assert "public_quality_avg_value_conflict_fallback" not in result["notes"]


def test_authority_loader_materializes_exact_lf_under_archive_and_autocrlf(
    tmp_path: Path,
) -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert f"{LOADER.as_posix()} text eol=lf" in attributes

    loader = _run(
        "git",
        "cat-file",
        "blob",
        f"HEAD:{LOADER.as_posix()}",
        cwd=ROOT,
        text=False,
    ).stdout
    assert b"\r\n" not in loader
    assert hashlib.sha1(f"blob {len(loader)}\0".encode() + loader).hexdigest() == LOADER_BLOB

    fixture = tmp_path / "fixture"
    (fixture / LOADER.parent).mkdir(parents=True)
    (fixture / ".gitattributes").write_text(
        f"{LOADER.as_posix()} text eol=lf\n",
        encoding="utf-8",
        newline="\n",
    )
    (fixture / LOADER).write_bytes(loader)
    _run("git", "init", "-q", cwd=fixture)
    _run("git", "add", ".gitattributes", LOADER.as_posix(), cwd=fixture)
    _run(
        "git",
        "-c",
        "user.name=Phase2 Test",
        "-c",
        "user.email=phase2@example.invalid",
        "commit",
        "-qm",
        "fixture",
        cwd=fixture,
    )

    archive = _run("git", "archive", "--format=tar", "HEAD", cwd=fixture, text=False).stdout
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
        archived = bundle.extractfile(LOADER.as_posix())
        assert archived is not None
        assert archived.read() == loader

    for autocrlf in ("true", "false"):
        checkout = tmp_path / f"checkout-{autocrlf}"
        _run("git", "clone", "-qn", str(fixture), str(checkout), cwd=tmp_path)
        _run("git", "config", "core.autocrlf", autocrlf, cwd=checkout)
        _run("git", "checkout", "-q", "HEAD", cwd=checkout)
        materialized = (checkout / LOADER).read_bytes()
        assert materialized == loader
        assert b"\r\n" not in materialized
        blob = _run("git", "hash-object", "--no-filters", LOADER.as_posix(), cwd=checkout)
        assert blob.stdout.strip() == LOADER_BLOB
