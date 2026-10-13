from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import sys
from types import SimpleNamespace

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import pytest


ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT / "src", ROOT / "hero_ref" / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from bidking_lab.extract.current_rankmap_package_contract import (  # noqa: E402
    CURRENT_PACKAGE_GENERATION,
    CURRENT_RANKMAP_AUTHORITY_MODE,
    CURRENT_RANKMAP_MAP_IDS,
)
from bidking_lab.live import current_rankmap_runtime as runtime  # noqa: E402
from bidking_lab.secure import data_crypt  # noqa: E402
from bidking_lab.secure.data_crypt import (  # noqa: E402
    DataKeyUnavailable,
    bkenc2_member_aad,
    encrypt_compressed_bytes,
)
from bidking_lab.simulation.basic_mc import (  # noqa: E402
    RankMapAlgorithmPriorBoundDropPools,
    flatten_pool,
)


_KEY = bytes(range(32))
_WRONG_KEY = bytes(reversed(range(32)))
_ENCRYPTED_MEMBERS = (
    "data/raw/tables/RankMap.txt",
    "data/processed/rankmap_drop_weights_current.json",
    "data/processed/rankmap_drop_priors_current.json",
    "data/processed/rankmap_distribution_authority_current.json",
)


def _package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, dict[str, bytes], dict[int, SimpleNamespace], dict[int, SimpleNamespace], dict[int, object]]:
    package = tmp_path / "package"
    weights = {
        str(map_id): {
            "count": 16.0,
            "n_items": 1,
            "items": {"101": [1.0, 100, 100]},
        }
        for map_id in CURRENT_RANKMAP_MAP_IDS
    }
    priors = {str(map_id): {} for map_id in CURRENT_RANKMAP_MAP_IDS}
    authority = {
        "schema": "bidking_current_rankmap_distribution_authority_v2",
        "package_generation": CURRENT_PACKAGE_GENERATION,
        "authority_mode": CURRENT_RANKMAP_AUTHORITY_MODE,
        "drop_or_dropmap_textasset": False,
        "drop_or_dropmap_absence_is_not_a_blocker": True,
        "claims_item_level_drop_graph": False,
        "modeled_item_probabilities": True,
        "official_distribution_authority": False,
        "model_distribution_authority": True,
        "runtime_open_claim": True,
        "runtime_open_scope": "s2_ranked_5601_through_5611",
        "ranked_5606": {
            "map_id": 5606,
            "status": "modeled_empirically_calibrated",
            "runtime_open_claim": True,
        },
        "recognized_unmodeled_scopes": {
            "large_auction_6001": {
                "map_id": 6001,
                "static_table_present": True,
                "lifecycle": "dormant_history_no_current_entry",
                "distribution_authority": False,
                "quote_available": False,
            }
        },
        "chenmei": {"map_id": 5506, "status": "pending_isolated"},
        "empirical_calibration": {
            "sessions": 191,
            "settled_items": 7_126,
            "decision": "retain_previous_quality_prior",
        },
        "raven_chaotic_space": dict(runtime.RAVEN_CHAOTIC_SPACE_AUTHORITY_RECEIPT),
    }
    payloads = {
        "data/raw/tables/Item.txt": b"item-current",
        "data/raw/tables/BidMap.txt": b"bidmap-current",
        "data/raw/tables/RankMap.txt": b"rankmap-current",
        "data/processed/rankmap_drop_weights_current.json": json.dumps(
            weights,
            separators=(",", ":"),
        ).encode(),
        "data/processed/rankmap_drop_priors_current.json": json.dumps(
            priors,
            separators=(",", ":"),
        ).encode(),
        "data/processed/rankmap_distribution_authority_current.json": json.dumps(
            authority,
            separators=(",", ":"),
        ).encode(),
    }
    specs = {
        relative: {
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        for relative, payload in payloads.items()
    }
    monkeypatch.setattr(runtime, "PACKAGE_BASELINE_ID", "test-rankmap-bkenc2")
    monkeypatch.setattr(runtime, "_PACKAGE_FILES", specs)
    for relative, payload in payloads.items():
        path = package / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    fingerprint = package / "scripts" / "package_data_fingerprints.json"
    fingerprint.parent.mkdir(parents=True)
    fingerprint.write_text(
        json.dumps(
            {
                "baseline_id": "test-rankmap-bkenc2",
                "files": {
                    relative: {
                        "size": spec["bytes"],
                        "sha256": spec["sha256"],
                    }
                    for relative, spec in specs.items()
                },
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    maps = {
        map_id: SimpleNamespace(drop_pool_id=900_000 + map_id)
        for map_id in CURRENT_RANKMAP_MAP_IDS
    }
    items = {101: SimpleNamespace(value=100)}
    drops = {77: object()}
    return package, payloads, maps, items, drops


def _encrypt_members(
    package: Path,
    payloads: dict[str, bytes],
    *,
    key: bytes = _KEY,
) -> None:
    for relative in _ENCRYPTED_MEMBERS:
        (package / relative).write_bytes(
            encrypt_compressed_bytes(
                payloads[relative],
                key,
                member_path=relative,
            )
        )


def _bind(
    package: Path,
    maps: dict[int, SimpleNamespace],
    items: dict[int, SimpleNamespace],
    drops: dict[int, object],
):
    return runtime.bind_current_rankmap_algorithm_prior(
        package_root=package,
        maps=maps,
        drops=drops,
        items=items,
    )


def _assert_fail_closed(
    result: tuple[object, str, dict[str, object]],
    drops: dict[int, object],
    expected_status: str,
) -> None:
    returned, status, receipt = result
    assert returned is drops
    assert status == expected_status
    assert receipt == {}


def test_plaintext_and_correct_path_bkenc2_keep_all_18_original_pools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package, payloads, maps, items, drops = _package(tmp_path, monkeypatch)

    for encrypted in (False, True):
        if encrypted:
            _encrypt_members(package, payloads)
            monkeypatch.setattr(data_crypt, "resolve_key", lambda: _KEY)
        bound, status, receipt = _bind(package, maps, items, drops)
        assert status == "ok"
        assert isinstance(bound, RankMapAlgorithmPriorBoundDropPools)
        assert bound.bound_pool_ids == {
            maps[map_id].drop_pool_id for map_id in CURRENT_RANKMAP_MAP_IDS
        }
        assert len(bound.bound_pool_ids) == 18
        assert receipt["scope_map_count"] == 18
        assert receipt["ranked_5606"] == "modeled_empirically_calibrated"
        assert receipt["runtime_open_claim"] is True
        assert receipt["recognized_unmodeled_scopes"]["large_auction_6001"] == {
            "map_id": 6001,
            "static_table_present": True,
            "lifecycle": "dormant_history_no_current_entry",
            "distribution_authority": False,
            "quote_available": False,
        }
        assert receipt["chenmei_5506"] == "pending_isolated"
        assert receipt["drop_or_dropmap_textasset"] is False
        assert receipt["claims_item_level_drop_graph"] is False
        for map_id in CURRENT_RANKMAP_MAP_IDS:
            flattened = flatten_pool(
                maps[map_id].drop_pool_id,
                bound,
                items,
            )
            assert flattened.item_ids == [101]
            assert flattened.probabilities == [1.0]


@pytest.mark.parametrize("arm", ["wrong_member", "wrong_key", "cipher_bitflip"])
def test_authenticated_decryption_failures_return_unchanged_strict_drops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    arm: str,
) -> None:
    package, payloads, maps, items, drops = _package(tmp_path, monkeypatch)
    _encrypt_members(package, payloads)
    monkeypatch.setattr(data_crypt, "resolve_key", lambda: _KEY)
    if arm == "wrong_member":
        left = package / _ENCRYPTED_MEMBERS[0]
        right = package / _ENCRYPTED_MEMBERS[1]
        left_raw, right_raw = left.read_bytes(), right.read_bytes()
        left.write_bytes(right_raw)
        right.write_bytes(left_raw)
    elif arm == "wrong_key":
        monkeypatch.setattr(data_crypt, "resolve_key", lambda: _WRONG_KEY)
    else:
        path = package / _ENCRYPTED_MEMBERS[0]
        blob = bytearray(path.read_bytes())
        blob[-1] ^= 1
        path.write_bytes(blob)

    _assert_fail_closed(
        _bind(package, maps, items, drops),
        drops,
        "authority_input_authentication_mismatch",
    )


def test_missing_key_is_unavailable_without_swallowing_other_runtime_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package, payloads, maps, items, drops = _package(tmp_path, monkeypatch)
    _encrypt_members(package, payloads)

    def missing_key() -> bytes:
        raise DataKeyUnavailable("test-only missing key")

    monkeypatch.setattr(data_crypt, "resolve_key", missing_key)
    _assert_fail_closed(
        _bind(package, maps, items, drops),
        drops,
        "authority_input_unavailable",
    )

    monkeypatch.setattr(
        runtime,
        "_plain_bytes",
        lambda _path: (_ for _ in ()).throw(RuntimeError("unrelated runtime error")),
    )
    with pytest.raises(RuntimeError, match="unrelated runtime error"):
        _bind(package, maps, items, drops)


def test_authenticated_but_malformed_compressed_payload_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package, payloads, maps, items, drops = _package(tmp_path, monkeypatch)
    _encrypt_members(package, payloads)
    monkeypatch.setattr(data_crypt, "resolve_key", lambda: _KEY)
    relative = _ENCRYPTED_MEMBERS[0]
    nonce = bytes(range(12))
    malformed = struct.pack(">Q", 5) + b"not-gzip"
    (package / relative).write_bytes(
        b"BKENC2\n"
        + nonce
        + AESGCM(_KEY).encrypt(
            nonce,
            malformed,
            bkenc2_member_aad(relative),
        )
    )

    _assert_fail_closed(
        _bind(package, maps, items, drops),
        drops,
        "authority_input_unavailable",
    )


@pytest.mark.parametrize("error", [ValueError("bad input"), OSError("read failed")])
def test_existing_non_auth_input_failures_remain_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    package, _payloads, maps, items, drops = _package(tmp_path, monkeypatch)
    monkeypatch.setattr(
        runtime,
        "_plain_bytes",
        lambda _path: (_ for _ in ()).throw(error),
    )
    _assert_fail_closed(
        _bind(package, maps, items, drops),
        drops,
        "authority_input_unavailable",
    )


def test_authenticated_failure_types_are_explicit_not_legacy_catch_aliases() -> None:
    assert not issubclass(InvalidTag, (ValueError, RuntimeError))
    assert issubclass(DataKeyUnavailable, RuntimeError)
    assert not issubclass(DataKeyUnavailable, ValueError)
