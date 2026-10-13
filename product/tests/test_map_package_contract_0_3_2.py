from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "scripts" / "package_data_manifest.json"
FINGERPRINTS = ROOT / "scripts" / "package_data_fingerprints.json"
RELEASE_CONTRACT = ROOT / "scripts" / "release_zip_contract.json"
SCHEDULED_CONSUMERS = ROOT / "scripts" / "package_data_scheduled_consumers.json"
CHECKER = ROOT / "scripts" / "check_package_data_integrity.py"
CLIENT_PROTECTION = ROOT / "scripts" / "client_protection_manifest.json"
ENGINE = ROOT / "hero_ref" / "src" / "ahmad_ref_engine.py"

ITEMS = "data/processed/items.json"
AUTHORITY = "data/processed/item_category_authority_0_3_2.json"
DROP_WEIGHTS = "data/processed/drop_weights_all_maps_0627.json"
AUCTION_HALL_6001_SHADOW = (
    "data/processed/auction_hall_6001_shadow_distribution.json"
)
LEGACY_MEMORY = "data/processed/item_category_mem_0625.json"
CALIBRATION = "data/processed/map_category_calibration_0_3_2.json"
NONPACKAGE_DATA = (LEGACY_MEMORY, CALIBRATION)
PHYSICAL_CATEGORY_IDS = frozenset(range(101, 111))
RAW_BYTE_FROZEN_DATA = (
    "data/processed/battle_items.json",
    "data/processed/heroes.json",
    "data/processed/maps.json",
    "data/processed/map_fragment_fixes.json",
)

EXPECTED_SHIPPED = (
    "data/raw/tables/Item.txt",
    "data/raw/tables/BidMap.txt",
    "data/raw/tables/RankMap.txt",
    "data/raw/tables/Drop.txt",
    "data/raw/tables/Hero.txt",
    "data/processed/red_longtail_normalized_0627.json",
    "data/processed/drop_table_priors_0627.json",
    "data/processed/quality_combo_presolve_q456.json",
    ITEMS,
    "data/processed/items_droppable.json",
    AUTHORITY,
    DROP_WEIGHTS,
    "data/processed/rankmap_drop_weights_current.json",
    "data/processed/rankmap_drop_priors_current.json",
    "data/processed/rankmap_distribution_authority_current.json",
    AUCTION_HALL_6001_SHADOW,
    "data/processed/battle_items.json",
    "data/processed/heroes.json",
    "data/processed/maps.json",
    "data/processed/map_fragment_fixes.json",
)
CURRENT_RANKMAP_AUTHORITY = (
    "data/processed/rankmap_distribution_authority_current.json"
)
CURRENT_RANKMAP_FIXED_INPUTS = (
    "data/raw/tables/Item.txt",
    "data/raw/tables/BidMap.txt",
    "data/raw/tables/RankMap.txt",
    "data/processed/rankmap_drop_weights_current.json",
    "data/processed/rankmap_drop_priors_current.json",
    CURRENT_RANKMAP_AUTHORITY,
)

CANONICAL_DATA = {
    ITEMS: (
        694_889,
        "49f4103b3e77987b801ca7b611eafe24f4d7b67c1e96bd833e7a5b694c8bbe1c",
    ),
    AUTHORITY: (
        611_847,
        "79d0dcfeec06e922fd91f75f6f1ea7b23d270c237b9d67914395b07d6d85b048",
    ),
    DROP_WEIGHTS: (
        2_664_405,
        "ce0db9680b53e393bc7a0db9e89e668ff3674701bdd524ed3b000f822bc8df11",
    ),
    CALIBRATION: (
        3_654_213,
        "37eb8f50d9be38ec3d1c85909ea3478800ecc574bda70446f05ca807983018cc",
    ),
}


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _load_value(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _package_fingerprint(files: dict[str, dict[str, Any]]) -> str:
    payload = [
        {
            "path": rel,
            "bytes": record["size"],
            "sha256": record["sha256"],
        }
        for rel, record in sorted(files.items())
    ]
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _run_checker(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", "scripts/check_package_data_integrity.py", "--verify"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def _synthetic_checker_repo(tmp_path: Path, *, initial_count: int) -> tuple[Path, dict[str, bytes]]:
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(CHECKER, scripts / CHECKER.name)
    shutil.copy2(MANIFEST, scripts / MANIFEST.name)
    payloads = {
        rel: f"approved-package-input:{index}:{rel}\n".encode()
        for index, rel in enumerate(EXPECTED_SHIPPED)
    }
    rankmap_generation = "1" * 64
    payloads[CURRENT_RANKMAP_AUTHORITY] = (
        json.dumps(
            {"package_generation": rankmap_generation},
            separators=(",", ":"),
        ).encode()
        + b"\n"
    )
    for rel in EXPECTED_SHIPPED[:initial_count]:
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payloads[rel])
    manifest_hash = hashlib.sha256((scripts / MANIFEST.name).read_bytes()).hexdigest()
    baseline = {
        "schema": "bidking_package_data_fingerprints_v1",
        "baseline_id": "test-map-package-contract",
        "manifest": "scripts/package_data_manifest.json",
        "manifest_sha256": manifest_hash,
        "files": {
            rel: {
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
            }
            for rel, payload in payloads.items()
        },
    }
    baseline["source_validation"] = {
        "package_fingerprint": _package_fingerprint(baseline["files"]),
    }
    (scripts / FINGERPRINTS.name).write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    engine = repo / "hero_ref" / "src" / ENGINE.name
    engine.parent.mkdir(parents=True)
    engine.write_text(
        'AISHA_Q5_TOTAL_CELLS_PACKAGE_BASELINE_ID = "test-map-package-contract"\n'
        f'CURRENT_RANKMAP_PACKAGE_GENERATION = "{rankmap_generation}"\n'
        f'AUCTION_HALL_6001_SHADOW_RELATIVE = "{AUCTION_HALL_6001_SHADOW}"\n'
        f"AUCTION_HALL_6001_SHADOW_BYTES = {len(payloads[AUCTION_HALL_6001_SHADOW])}\n"
        "AUCTION_HALL_6001_SHADOW_SHA256 = "
        f'"{hashlib.sha256(payloads[AUCTION_HALL_6001_SHADOW]).hexdigest()}"\n'
        f"_CURRENT_RANKMAP_GENERATION_FILES = {repr({rel: (len(payloads[rel]), hashlib.sha256(payloads[rel]).hexdigest()) for rel in CURRENT_RANKMAP_FIXED_INPUTS})}\n",
        encoding="utf-8",
    )
    return repo, payloads


def _write_payloads(repo: Path, payloads: dict[str, bytes], paths: tuple[str, ...]) -> None:
    for rel in paths:
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payloads[rel])


def _scheduled_lanea_catalog_projection(
    category_memory: dict[str, Any],
) -> dict[str, Any]:
    """Project the audited Lane A catalog identity over the frozen data.

    This data-only check does not activate Lane A in the package-only source.
    Final integration must rerun the exact product functions named by the
    scheduled-consumer contract.
    """

    processed = _load_value(ROOT / "data/processed/items_droppable.json")
    authority = _load(ROOT / AUTHORITY)
    map_weights = _load(ROOT / DROP_WEIGHTS)
    assert isinstance(processed, list)
    assert isinstance(category_memory, dict)

    document_rows = {record["item_id"]: record for record in authority["records"]}
    rows: dict[int, tuple[Any, ...]] = {}
    for raw_row in processed:
        item_id = raw_row["item_id"]
        record = document_rows[item_id]
        assert record["current_random_drop_eligible"] is True
        assert all(
            record[field] == raw_row[field]
            for field in ("quality", "value", "shape_w", "shape_h")
        )
        authoritative = frozenset(record["category_ids"]) & PHYSICAL_CATEGORY_IDS
        positive_hints = {
            tag
            for tag in raw_row.get("tags", ())
            if tag.__class__ is int and tag in PHYSICAL_CATEGORY_IDS
        }
        memory_hint = category_memory.get(str(item_id))
        if memory_hint is not None:
            assert memory_hint.__class__ is int
            if memory_hint in PHYSICAL_CATEGORY_IDS:
                positive_hints.add(memory_hint)
        assert positive_hints.issubset(authoritative)
        rows[item_id] = (
            raw_row["quality"],
            raw_row["value"],
            raw_row["shape_w"],
            raw_row["shape_h"],
            tuple(sorted(authoritative)),
            tuple(sorted(PHYSICAL_CATEGORY_IDS - authoritative)),
            (),
        )

    reachable_by_map: dict[int, tuple[int, ...]] = {}
    expected_ids = frozenset(rows)
    for raw_map_id, raw_entry in map_weights.items():
        raw_items = raw_entry["items"]
        assert raw_entry["n_items"] == len(raw_items)
        assert frozenset(int(item_id) for item_id in raw_items).issubset(expected_ids)
        reachable_by_map[int(raw_map_id)] = tuple(
            sorted(
                int(item_id)
                for item_id, weight_row in raw_items.items()
                if float(weight_row[0]) > 0
            )
        )

    signature_rows = tuple((item_id, *rows[item_id]) for item_id in sorted(rows))
    signature_maps = tuple(
        (map_id, reachable_by_map[map_id]) for map_id in sorted(reachable_by_map)
    )
    authority_identity = authority["authority_identity"]
    digest = hashlib.sha256(
        json.dumps(
            (signature_rows, signature_maps, "ok", authority_identity),
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()
    return {
        "catalog": rows,
        "domains": reachable_by_map,
        "status": "ok",
        "generation": f"physical-category-v1:{digest}",
    }


def test_package_manifest_freezes_exact_20_inputs() -> None:
    manifest = _load(MANIFEST)
    fingerprints = _load(FINGERPRINTS)

    assert tuple(manifest["files"]) == EXPECTED_SHIPPED
    assert len(EXPECTED_SHIPPED) == 20
    assert set(fingerprints["files"]) == set(EXPECTED_SHIPPED)
    assert fingerprints["baseline_id"] == (
        "package-data-2026-09-03-0.3.4-hotfix1-68171c"
    )
    assert fingerprints["manifest_sha256"] == hashlib.sha256(
        MANIFEST.read_bytes()
    ).hexdigest()
    for rel in (ITEMS, AUTHORITY, DROP_WEIGHTS):
        assert fingerprints["files"][rel] == {
            "sha256": CANONICAL_DATA[rel][1],
            "size": CANONICAL_DATA[rel][0],
        }
    assert set(NONPACKAGE_DATA).isdisjoint(manifest["files"])
    assert set(NONPACKAGE_DATA).isdisjoint(fingerprints["files"])


def test_map_data_files_have_exact_canonical_lf_bytes() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    for rel, (size, digest) in CANONICAL_DATA.items():
        payload = (ROOT / rel).read_bytes()
        assert len(payload) == size
        assert hashlib.sha256(payload).hexdigest() == digest
        assert b"\r\n" not in payload
        if rel not in (ITEMS, DROP_WEIGHTS):
            assert payload.endswith(b"\n")
        assert f"{rel} text eol=lf" in attributes


def test_raw_fingerprinted_package_json_is_binary_stable() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    fingerprints = _load(FINGERPRINTS)["files"]
    for rel in RAW_BYTE_FROZEN_DATA:
        payload = (ROOT / rel).read_bytes()
        expected = fingerprints[rel]
        assert len(payload) == expected["size"]
        assert hashlib.sha256(payload).hexdigest() == expected["sha256"]
        assert payload.count(b"\n") > 0
        assert payload.count(b"\r\n") in {0, payload.count(b"\n")}
        assert f"{rel} binary" in attributes


def test_scheduled_lanea_consumer_uses_audited_bkenc2_loader() -> None:
    scheduled = _load(SCHEDULED_CONSUMERS)
    lane_a = scheduled["lane_a"]
    loader = lane_a["bkenc2_loader_successor"]
    assert scheduled["activation_stage"] == "final_release_integration"
    assert scheduled["package_only_runtime_consumer"] is True
    assert lane_a["source_commit"] == (
        "6329d24e9cac2e88d4ace4dc4d7ebfef5e5cd3a8"
    )
    assert lane_a["audit_commit"] == (
        "c7d471ab09bc1c715938b20d9136b26d71e6333f"
    )
    assert lane_a["audit_document"] == {
        "path": (
            "docs/handoffs/AUDIT_core_engine_0.3.2_"
            "jianying_category_authority_single_snapshot_successor3_pass_"
            "2026-08-09.zh-CN.md"
        ),
        "blob": "39869492e25d4c9c2e17f463c5a0363e00f4152d",
        "verdict": "PASS",
    }
    assert lane_a["strict_code"] == {
        "src/bidking_lab/data/item_category_authority.py": (
            "17afedf960aa715de51a882f29fbef428ed7d886"
        ),
        "src/bidking_lab/inference/v2.py": (
            "66ae1b7bfe0bef6bdb363510fb0c7375e3ed3a0b"
        ),
    }
    assert tuple(lane_a["required_package_inputs"]) == (
        "data/processed/items_droppable.json",
        AUTHORITY,
        DROP_WEIGHTS,
    )
    assert "79D0DCFEEC06E922" in lane_a["packaged_loader_requirement"]
    assert loader["status"] == (
        "historical_audit_plus_eb1_and_68171c_42_column_extensions_pending_audit"
    )
    assert loader["source_commit"] == (
        "53ffce24933d3e825aa3b69cdcf1fa5f1088942e"
    )
    assert loader["handoff_commit"] == (
        "9f3f43b20a52a0019a1de3bc896bb7b4c03ac563"
    )
    assert loader["audit_commit"] == (
        "62ffd904160228b35a952c20fcebf8b14b9c84de"
    )

    current_v2 = (ROOT / "src/bidking_lab/inference/v2.py").read_text(
        encoding="utf-8-sig"
    )
    assert all(symbol in current_v2 for symbol in lane_a["required_symbols"])
    assert lane_a["current_strict_code"] == {
        "src/bidking_lab/data/item_category_authority.py": (
            "5ff9463d6fc2361f57a544649953b301ce339039"
        ),
        "src/bidking_lab/inference/v2.py": (
            "5d9a275bbaf81608fb6c736da969643b386f139d"
        ),
    }

    rankmap = scheduled["current_rankmap_distribution"]
    assert rankmap["status"] == (
        "68171c_source_owner_inputs_frozen_pending_different_owner_audit"
    )
    assert rankmap["package_generation"] == "68171c-2026-09-03-907"
    assert rankmap["authority_mode"] == "rankmap_drop_model_algorithm_prior"
    assert tuple(rankmap["scopes"]["route_stable_210x"]) == tuple(range(2101, 2108))
    assert tuple(rankmap["scopes"]["changed_route_ranked_560x"]) == tuple(
        range(5601, 5612)
    )
    assert rankmap["claims"] == {
        "drop_or_dropmap_textasset": False,
        "item_level_drop_graph": False,
        "drop_or_dropmap_absence_is_not_a_blocker": True,
        "distribution_input_contract": (
            "since_2026-06-25_current_support_rankmap_quality_value_settlement_model"
        ),
        "model_distribution_authority": True,
        "official_distribution_authority": False,
        "ranked_5606": "modeled_empirically_calibrated",
        "runtime_open_claim": True,
        "chenmei_5506": "pending_isolated",
        "raven_510x": "recognized_unavailable_not_distribution_authority",
    }
    assert rankmap["current_runtime_consumer_code"] == {
        "hero_ref/src/ahmad_ref_engine.py": (
            "4857bbfbace895269f0611368136b48bb46c00c3"
        )
    }
    raven = rankmap["recognized_unmodeled_scopes"][
        "raven_chaotic_space_510x"
    ]
    assert tuple(raven["map_ids"]) == tuple(range(5101, 5111))
    assert tuple(raven["pool_ids_not_model_ids"]) == tuple(range(5001, 5011))
    assert raven["quote_available"] is False
    large_auction = rankmap["recognized_unmodeled_scopes"]["large_auction_6001"]
    assert large_auction == {
        "map_id": 6001,
        "static_table_present": True,
        "runtime_observed": False,
        "lifecycle": "dormant_history_no_current_entry",
        "distribution_authority": False,
        "official_quote_authority": False,
        "provisional_distribution_available": False,
        "provisional_quote_available": False,
        "historical_sidecar": AUCTION_HALL_6001_SHADOW,
    }

    current_loader = (ROOT / "src/bidking_lab/data/item_category_authority.py").read_bytes()
    assert b"load_moat_text" in current_loader
    assert b"data_crypt" in current_loader
    assert b".read_text(" not in current_loader
    current_loader_blob = hashlib.sha1(
        f"blob {len(current_loader)}\0".encode("ascii") + current_loader
    ).hexdigest()
    assert current_loader_blob == loader["current_strict_code"][
        "src/bidking_lab/data/item_category_authority.py"
    ]

    source_commit = lane_a["source_commit"]
    audit_commit = lane_a["audit_commit"]
    audit_document = lane_a["audit_document"]
    assert _git("cat-file", "-t", source_commit) == "commit"
    assert _git("cat-file", "-t", audit_commit) == "commit"
    assert _git("show", "-s", "--format=%P", audit_commit) == source_commit
    assert _git("diff-tree", "--no-commit-id", "--name-only", "-r", audit_commit) == (
        audit_document["path"]
    )
    assert _git("rev-parse", f"{audit_commit}:{audit_document['path']}") == (
        audit_document["blob"]
    )
    for path, blob in lane_a["strict_code"].items():
        assert _git("rev-parse", f"{source_commit}:{path}") == blob

    audit_text = _git("show", f"{audit_commit}:{audit_document['path']}")
    assert audit_text.count("> lifecycle: `source-scoped-pass`") == 1
    assert audit_text.count("> verdict: **PASS**") == 1
    assert "> verdict: **BLOCK**" not in audit_text
    assert f"exact source：`{source_commit}`" in audit_text

    loader_source = loader["source_commit"]
    loader_handoff = loader["handoff_commit"]
    loader_audit = loader["audit_commit"]
    loader_document = loader["audit_document"]
    assert _git("show", "-s", "--format=%P", loader_handoff) == loader_source
    assert _git("show", "-s", "--format=%P", loader_audit) == loader_handoff
    assert _git("diff-tree", "--no-commit-id", "--name-only", "-r", loader_audit) == (
        loader_document["path"]
    )
    assert _git("rev-parse", f"{loader_audit}:{loader_document['path']}") == (
        loader_document["blob"]
    )
    for path, blob in loader["strict_code"].items():
        assert _git("rev-parse", f"{loader_source}:{path}") == blob


def test_optional_memory_is_object_equivalent_for_catalog_domains_and_generation() -> None:
    category_memory = _load(ROOT / LEGACY_MEMORY)
    without_memory = _scheduled_lanea_catalog_projection({})
    with_memory = _scheduled_lanea_catalog_projection(category_memory)

    assert with_memory == without_memory
    assert with_memory["status"] == "ok"
    assert len(with_memory["catalog"]) == 717
    assert len(with_memory["domains"]) == 101
    assert with_memory["generation"] == (
            "physical-category-v1:"
            "d87bc25e5dd7f485f806086b7d6c64b9ef3d767bb009eb6af1cb0d12a19de8a2"
        )


def test_calibration_stays_offline_and_audited_sav2_is_protected() -> None:
    scheduled = _load(SCHEDULED_CONSUMERS)
    protection = _load(CLIENT_PROTECTION)
    protection_text = json.dumps(protection, ensure_ascii=False)

    assert scheduled["offline_exclusions"] == [CALIBRATION]
    assert scheduled["sav2"] == {
        "status": "source_scoped_pass",
        "source_commit": "11e7dd6edfb44325d21ae4d8ff83e6bc5802836c",
        "handoff_commit": "7b5c104c20bbeb8d0f62a8132baad09c1c0ef2b6",
        "protected_module": "src/bidking_lab/inference/mean_multiset_support.py",
        "exact_blob": "2efcda19548a004573810f570a9062cc1b908273",
        "activation_requirement": (
            "Preserve the exact SAV2 blob in client protection and keep SAV2 "
            "shadow-only in the final independently audited composition."
        ),
    }
    sav2 = scheduled["sav2"]
    runtime_rel = sav2["protected_module"].removeprefix("src/")
    current_blob = _git("hash-object", sav2["protected_module"])
    assert runtime_rel in protection_text
    assert current_blob == sav2["exact_blob"]
    assert _git("show", "-s", "--format=%P", sav2["handoff_commit"]) == (
        sav2["source_commit"]
    )
    assert _git("rev-parse", f"{sav2['source_commit']}:{sav2['protected_module']}") == (
        sav2["exact_blob"]
    )


def test_release_and_build_contract_exclude_nonpackage_data() -> None:
    contract = _load(RELEASE_CONTRACT)
    protection = contract["member_protection"]
    build = (ROOT / "scripts/build_live_portable.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert contract["contract_version"] == 25
    for rel in (ITEMS, AUTHORITY, DROP_WEIGHTS, AUCTION_HALL_6001_SHADOW):
        assert protection[rel] == {"mode": "bkenc2-gzip", "required": True}
    assert set(NONPACKAGE_DATA).isdisjoint(protection)
    assert "--list-json" in build
    assert 'Copy-Tree "data\\processed"' not in build
    assert all(rel not in build for rel in NONPACKAGE_DATA)


def test_checker_rejects_8_of_20_then_accepts_exact_20_inputs(tmp_path: Path) -> None:
    repo, payloads = _synthetic_checker_repo(tmp_path, initial_count=8)

    partial = _run_checker(repo)
    assert partial.returncode == 1
    for rel in EXPECTED_SHIPPED[8:]:
        assert rel in partial.stdout

    _write_payloads(repo, payloads, EXPECTED_SHIPPED[8:])
    complete = _run_checker(repo)
    assert complete.returncode == 0, complete.stdout + complete.stderr


def test_checker_rejects_missing_lanea_inputs_until_both_exist(tmp_path: Path) -> None:
    repo, payloads = _synthetic_checker_repo(tmp_path, initial_count=0)
    without_lanea = tuple(
        rel for rel in EXPECTED_SHIPPED if rel not in (AUTHORITY, DROP_WEIGHTS)
    )
    _write_payloads(repo, payloads, without_lanea)

    missing_both = _run_checker(repo)
    assert missing_both.returncode == 1
    assert AUTHORITY in missing_both.stdout
    assert DROP_WEIGHTS in missing_both.stdout

    _write_payloads(repo, payloads, (AUTHORITY,))
    missing_weights = _run_checker(repo)
    assert missing_weights.returncode == 1
    assert DROP_WEIGHTS in missing_weights.stdout

    _write_payloads(repo, payloads, (DROP_WEIGHTS,))
    complete = _run_checker(repo)
    assert complete.returncode == 0, complete.stdout + complete.stderr


def test_checker_rejects_new_inputs_wrong_byte_and_crlf(tmp_path: Path) -> None:
    repo, payloads = _synthetic_checker_repo(
        tmp_path, initial_count=len(EXPECTED_SHIPPED)
    )

    for rel in (ITEMS, AUTHORITY, DROP_WEIGHTS):
        target = repo / rel
        original = payloads[rel]
        target.write_bytes(b"!" + original[1:])
        wrong_byte = _run_checker(repo)
        assert wrong_byte.returncode == 1
        assert rel in wrong_byte.stdout

        target.write_bytes(original.replace(b"\n", b"\r\n"))
        wrong_eol = _run_checker(repo)
        assert wrong_eol.returncode == 1
        assert rel in wrong_eol.stdout
        target.write_bytes(original)


def test_bkenc2_zip_hydration_preserves_all_new_plaintext(tmp_path: Path) -> None:
    from bidking_lab.secure.data_crypt import decrypt_bytes, encrypt_compressed_bytes

    contract = _load(RELEASE_CONTRACT)
    key = bytes(range(32))
    archive_path = tmp_path / "map-package-contract.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for rel in (ITEMS, AUTHORITY, DROP_WEIGHTS):
            assert contract["member_protection"][rel] == {
                "mode": "bkenc2-gzip",
                "required": True,
            }
            archive.writestr(
                rel,
                encrypt_compressed_bytes(
                    (ROOT / rel).read_bytes(),
                    key,
                    member_path=rel,
                ),
            )

    with zipfile.ZipFile(archive_path) as archive:
        for rel in (ITEMS, AUTHORITY, DROP_WEIGHTS):
            hydrated = decrypt_bytes(archive.read(rel), key, member_path=rel)
            assert hydrated == (ROOT / rel).read_bytes()
            assert hashlib.sha256(hydrated).hexdigest() == CANONICAL_DATA[rel][1]
            assert b"\r\n" not in hydrated


def test_contract_bytes_are_canonical_utf8_lf() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    for path in (FINGERPRINTS, SCHEDULED_CONSUMERS):
        payload = path.read_bytes()
        assert payload.startswith(b"{")
        assert payload.endswith(b"\n")
        assert b"\r\n" not in payload
        assert base64.b64encode(payload[:1]) == b"ew=="
        assert f"{path.relative_to(ROOT).as_posix()} text eol=lf" in attributes
