from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import zipfile

import pytest

from bidking_lab.live import version_check as vc
from bidking_lab.secure import client_activate as ca


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "activation_service_endpoints.json"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config_bytes(*, enabled: bool = False) -> bytes:
    endpoints = [
        {
            "endpoint_id": "endpoint_1",
            "base": "https://one.example",
            "telemetry": "https://one.example/telemetry",
        },
        {
            "endpoint_id": "endpoint_2",
            "base": "https://two.example",
            "telemetry": "https://two.example/telemetry",
        },
    ]
    if enabled:
        endpoints.append(
            {
                "endpoint_id": "endpoint_3",
                "base": "https://third.example.cn",
                "telemetry": None,
            }
        )
    return json.dumps(
        {
            "schema_version": 1,
            "generation": 2 if enabled else 1,
            "endpoint_3_enabled": enabled,
            "endpoints": endpoints,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def test_tracked_default_is_real_two_endpoint_generation_with_endpoint3_off():
    config = vc.load_activation_service_endpoint_config(CONFIG_PATH)
    assert config.generation == 1
    assert config.endpoint_3_enabled is False
    assert tuple(endpoint.endpoint_id for endpoint in config.endpoints) == (
        "endpoint_1",
        "endpoint_2",
    )
    assert tuple(endpoint.base for endpoint in config.endpoints) == (
        "https://api.bidking.example.invalid",
        "https://backup.bidking.example.invalid",
    )
    assert all("placeholder" not in endpoint.base for endpoint in config.endpoints)
    assert ca._activate_urls(None) == [
        "https://api.bidking.example.invalid/activate",
        "https://backup.bidking.example.invalid/activate",
    ]


def test_package_layout_resolves_endpoint_file_from_stage_root(monkeypatch, tmp_path):
    stage = tmp_path / "Stage"
    module_path = stage / "src" / "bidking_lab" / "live" / "version_check.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text("# package layout marker\n", encoding="ascii")
    config_path = stage / "activation_service_endpoints.json"
    config_path.write_bytes(_config_bytes())

    monkeypatch.setattr(vc, "__file__", str(module_path))
    assert vc.activation_service_endpoints_path() == config_path
    assert vc.load_activation_service_endpoint_config().generation == 1


def test_loader_rejects_identity_swap_between_lstat_and_open(monkeypatch, tmp_path):
    config_path = tmp_path / "activation_service_endpoints.json"
    replacement = tmp_path / "replacement.json"
    config_path.write_bytes(_config_bytes())
    replacement.write_bytes(_config_bytes(enabled=True))
    original_open = vc.os.open

    def swapping_open(path, flags):
        replacement.replace(config_path)
        return original_open(path, flags)

    monkeypatch.setattr(vc.os, "open", swapping_open)
    with pytest.raises(ValueError, match="identity changed"):
        vc.load_activation_service_endpoint_config(config_path)


def test_loader_uses_no_follow_flag_when_platform_exposes_it(monkeypatch, tmp_path):
    config_path = tmp_path / "activation_service_endpoints.json"
    config_path.write_bytes(_config_bytes())
    observed: list[int] = []

    def recording_open(path, flags):
        del path
        observed.append(flags)
        raise OSError("synthetic no-follow probe")

    monkeypatch.setattr(vc.os, "O_NOFOLLOW", 0x20000000, raising=False)
    monkeypatch.setattr(vc.os, "open", recording_open)
    with pytest.raises(ValueError, match="cannot be opened safely"):
        vc.load_activation_service_endpoint_config(config_path)
    assert observed and observed[0] & vc.os.O_NOFOLLOW


def test_shared_immutable_parse_failure_disables_activation_and_version(monkeypatch):
    monkeypatch.setattr(
        vc,
        "_CONTROLLED_ACTIVATION_SERVICE_ENDPOINT_CONFIG",
        None,
    )
    assert vc.iter_service_endpoints() == []
    assert ca._activate_urls(None) == []
    status = vc.check_from_local_config(timeout=0.01)
    assert status["ok"] is False
    assert status["reachable"] is False
    assert status["configured"] is False


def test_synthetic_endpoint3_is_explicit_and_has_no_telemetry(monkeypatch):
    config = vc.parse_activation_service_endpoint_config_bytes(
        _config_bytes(enabled=True)
    )
    assert config.endpoint_3_enabled is True
    assert config.endpoints[2] == vc.ActivationServiceEndpoint(
        endpoint_id="endpoint_3",
        base="https://third.example.cn",
        telemetry=None,
    )
    monkeypatch.setattr(vc, "iter_service_endpoints", lambda: list(config.endpoints))
    candidates = ca._activation_endpoint_candidates(None)
    assert [(row.endpoint_id, row.base, row.telemetry_url) for row in candidates] == [
        ("endpoint_1", "https://one.example", "https://one.example/telemetry"),
        ("endpoint_2", "https://two.example", "https://two.example/telemetry"),
        ("endpoint_3", "https://third.example.cn", None),
    ]
    assert (
        ca._telemetry_url_from_activation_url(
            "https://third.example.cn/activate"
        )
        is None
    )


@pytest.mark.parametrize(
    ("enabled", "generation"),
    ((False, 1), (True, 2)),
)
def test_schema1_accepts_only_the_frozen_generation_for_endpoint3_state(
    enabled,
    generation,
):
    value = json.loads(_config_bytes(enabled=enabled).decode("ascii"))
    value["generation"] = generation
    config = vc.parse_activation_service_endpoint_config_bytes(
        json.dumps(value, separators=(",", ":")).encode("ascii")
    )
    assert config.generation == generation
    assert config.endpoint_3_enabled is enabled


@pytest.mark.parametrize(
    "case",
    (
        "generation1_enabled_three_endpoints",
        "generation1_enabled_two_endpoints",
        "generation1_enabled_malicious_extra",
        "generation2_disabled_two_endpoints",
        "generation2_disabled_three_endpoints",
    ),
)
def test_schema1_rejects_generation_endpoint3_state_mismatch(case):
    if case.startswith("generation1"):
        value = json.loads(_config_bytes(enabled=True).decode("ascii"))
        value["generation"] = 1
        if case.endswith("two_endpoints"):
            value["endpoints"].pop()
        elif case.endswith("malicious_extra"):
            value["generation_override"] = 2
    else:
        value = json.loads(_config_bytes(enabled=False).decode("ascii"))
        value["generation"] = 2
        if case.endswith("three_endpoints"):
            third = json.loads(_config_bytes(enabled=True).decode("ascii"))[
                "endpoints"
            ][2]
            value["endpoints"].append(third)
    raw = json.dumps(value, separators=(",", ":")).encode("ascii")
    with pytest.raises(ValueError):
        vc.parse_activation_service_endpoint_config_bytes(raw)


@pytest.mark.parametrize("generation", (0, 3, True, 2_147_483_648))
def test_schema1_rejects_unassigned_or_non_exact_generation(generation):
    value = json.loads(_config_bytes().decode("ascii"))
    value["generation"] = generation
    raw = json.dumps(value, separators=(",", ":")).encode("ascii")
    with pytest.raises(ValueError):
        vc.parse_activation_service_endpoint_config_bytes(raw)


def test_schema1_rejects_generation_subclass_without_magic(monkeypatch):
    class BombInt(int):
        calls = 0

        def explode(self, *_args, **_kwargs):
            type(self).calls += 1
            raise AssertionError("generation subclass protocol must not run")

        __eq__ = explode
        __le__ = explode
        __ge__ = explode
        __bool__ = explode
        __hash__ = explode
        __repr__ = explode

    value = json.loads(_config_bytes().decode("ascii"))
    value["generation"] = BombInt(1)
    monkeypatch.setattr(vc.json, "loads", lambda *_args, **_kwargs: value)
    with pytest.raises(ValueError):
        vc.parse_activation_service_endpoint_config_bytes(_config_bytes())
    assert BombInt.calls == 0


def test_synthetic_endpoint3_success_installs_no_telemetry_route(
    monkeypatch,
    tmp_path,
):
    config = vc.parse_activation_service_endpoint_config_bytes(
        _config_bytes(enabled=True)
    )
    monkeypatch.setattr(vc, "iter_service_endpoints", lambda: list(config.endpoints))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(ca, "device_fingerprint", lambda: "DEVICE-ENDPOINT-3")
    monkeypatch.setattr(ca, "_package_dir", lambda: tmp_path / "package")
    tried: list[str] = []
    installed: list[object] = []

    def fake_post(url, device, token, timeout):
        del token, timeout
        tried.append(url)
        if len(tried) < 3:
            return ca._ActivationPostFailure(
                "connect_timeout",
                retry_fallback=True,
                stage="tcp",
            )
        exp = 10_000
        lease = json.dumps(
            {"uid": f"trial:{device}", "device": device, "exp": exp},
            separators=(",", ":"),
        )
        return 200, {
            "ok": True,
            "mode": "trial",
            "lease": lease + ".signature",
            "trial_exp": exp,
            "master_key": "a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
        }

    def fake_install(*_args, telemetry_url=ca._MISSING, **_kwargs):
        installed.append(telemetry_url)
        return True

    monkeypatch.setattr(ca, "_post_activate", fake_post)
    monkeypatch.setattr(ca, "_save_cache", lambda **_kwargs: True)
    monkeypatch.setattr(ca, "_install_provider", fake_install)
    result = ca.activate(ui_token="", now=1_000, force_server=True)

    assert result.activated is True
    assert tried == [
        "https://one.example/activate",
        "https://two.example/activate",
        "https://third.example.cn/activate",
    ]
    assert installed == [None]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(extra=True),
        lambda value: value["endpoints"][0].update(extra=True),
        lambda value: value["endpoints"][0].update(
            base="https://user:maintainer@example.invalid"
        ),
        lambda value: value["endpoints"][0].update(base="https://one.example:443"),
        lambda value: value["endpoints"][0].update(base="https://one.example/api"),
        lambda value: value["endpoints"][0].update(base="https://one.example?q=1"),
        lambda value: value["endpoints"][0].update(base="https://one.example#frag"),
        lambda value: value["endpoints"][1].update(base="https://one.example"),
        lambda value: value.update(endpoint_3_enabled=True),
    ],
)
def test_strict_parser_rejects_ambiguous_or_inconsistent_config(mutate):
    value = json.loads(_config_bytes().decode("ascii"))
    mutate(value)
    raw = json.dumps(value, separators=(",", ":")).encode("ascii")
    with pytest.raises(ValueError):
        vc.parse_activation_service_endpoint_config_bytes(raw)


def test_strict_parser_rejects_duplicate_keys_and_malicious_subclasses():
    duplicate = _config_bytes().replace(
        b'"generation":1',
        b'"generation":1,"generation":2',
    )
    with pytest.raises(ValueError):
        vc.parse_activation_service_endpoint_config_bytes(duplicate)

    class EvilBytes(bytes):
        pass

    class EvilStr(str):
        pass

    with pytest.raises(ValueError):
        vc.parse_activation_service_endpoint_config_bytes(EvilBytes(_config_bytes()))
    with pytest.raises(ValueError):
        vc._strict_endpoint_base(EvilStr("https://evil.example"))


def test_diagnostic_projects_synthetic_three_endpoints_to_six_fixed_probes():
    diag = _load_module(
        "activation_transport_diag_endpoint_hotfix2",
        ROOT / "scripts" / "activation_transport_diagnostic.py",
    )
    config = vc.parse_activation_service_endpoint_config_bytes(
        _config_bytes(enabled=True)
    )
    bases, probes = diag._diagnostic_endpoint_rows(config)
    assert tuple(name for name, _base in bases) == (
        "endpoint_1",
        "endpoint_2",
        "endpoint_3",
    )
    assert len(probes) == 6
    assert {name for name, _endpoint, _url in probes} == {
        "endpoint_1",
        "endpoint_2",
        "endpoint_3",
    }


def test_build_and_artifact_identity_are_bound_to_raw_config_bytes():
    build = (ROOT / "scripts" / "build_live_portable.ps1").read_text(
        encoding="utf-8"
    )
    contract = json.loads(
        (ROOT / "scripts" / "release_zip_contract.json").read_text(
            encoding="utf-8"
        )
    )
    assert '"LICENSE", "activation_service_endpoints.json"' in build
    assert "ActivationServiceEndpointsSHA256:" in build
    assert "ActivationServiceEndpointsGeneration:" in build
    assert "ActivationServiceEndpoint3Enabled:" in build
    assert contract["contract_version"] == 14
    assert "activation_service_endpoints.json" in contract["required_members"][
        "common"
    ]
    assert contract["fixed_manifest_values"][
        "ActivationServiceEndpoint3Enabled"
    ] == "False"

    audit = _load_module(
        "audit_release_zip_endpoint_hotfix2",
        ROOT / "scripts" / "audit_release_zip.py",
    )
    raw = CONFIG_PATH.read_bytes()
    config = vc.parse_activation_service_endpoint_config_bytes(raw)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("activation_service_endpoints.json", raw)
    buffer.seek(0)
    with zipfile.ZipFile(buffer) as archive:
        members = {
            "activation_service_endpoints.json": [
                archive.getinfo("activation_service_endpoints.json")
            ]
        }
        fields = {
            "activationserviceendpointssha256": config.raw_sha256,
            "activationserviceendpointsgeneration": str(config.generation),
            "activationserviceendpoint3enabled": "False",
        }
        assert audit._activation_service_endpoint_identity_issues(
            archive, members, fields
        ) == []
        fields["activationserviceendpoint3enabled"] = "True"
        assert audit._activation_service_endpoint_identity_issues(
            archive, members, fields
        ) == ["release_activation_endpoint_config_enabled_mismatch"]
