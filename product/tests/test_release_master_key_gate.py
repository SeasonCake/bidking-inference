from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import verify_release_master_key as gate  # noqa: E402
from bidking_lab.secure import client_activate  # noqa: E402


def _install_cache(monkeypatch, key: bytes, *, saved_at: float = 9_900, exp: float = 20_000):
    monkeypatch.setattr(client_activate, "device_fingerprint", lambda: "device-1")
    monkeypatch.setattr(client_activate, "resolve_token", lambda value: "token-1")
    monkeypatch.setattr(
        client_activate,
        "_load_cache",
        lambda **kwargs: {
            "saved_at": saved_at,
            "mk": base64.b64encode(key).decode("ascii"),
            "lease": {"device": "device-1", "exp": exp},
        },
    )


def _key_file(tmp_path: Path, key: bytes) -> Path:
    path = tmp_path / "master_key_v2.txt"
    path.write_text(base64.b64encode(key).decode("ascii"), encoding="ascii")
    return path


def test_release_key_must_match_recent_activation_cache(tmp_path: Path, monkeypatch) -> None:
    key = bytes(range(32))
    _install_cache(monkeypatch, key)
    age = gate.verify_release_key(_key_file(tmp_path, key), now=10_000)
    assert age == pytest.approx(100 / 3600)


def test_release_key_rejects_wrong_file_without_printing_secret(tmp_path: Path, monkeypatch) -> None:
    delivered = bytes(range(32))
    selected = b"x" * 32
    _install_cache(monkeypatch, delivered)
    with pytest.raises(gate.ReleaseKeyVerificationError, match="不一致") as exc:
        gate.verify_release_key(_key_file(tmp_path, selected), now=10_000)
    assert base64.b64encode(selected).decode("ascii") not in str(exc.value)


def test_release_key_rejects_stale_activation_cache(tmp_path: Path, monkeypatch) -> None:
    key = bytes(range(32))
    _install_cache(monkeypatch, key, saved_at=1_000)
    with pytest.raises(gate.ReleaseKeyVerificationError, match="已超过 6 小时"):
        gate.verify_release_key(_key_file(tmp_path, key), now=30_000)
