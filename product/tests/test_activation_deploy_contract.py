from __future__ import annotations

import ast
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
from types import ModuleType, SimpleNamespace
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest


def test_history_deployment_exact_groups_include_helper():
    assert "sample_history_index.py" in RUNTIME_FILES
    shell = SERVER_UPDATE.read_text(encoding="utf-8")
    for group in ("FILES", "ACTIVATION_GROUP"):
        body = shell.split(group + "=(", 1)[1].split(")", 1)[0]
        assert body.count("sample_history_index.py") == 1


def test_history_deployment_missing_only_helper_rejected_before_load(tmp_path, monkeypatch):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    for name in RUNTIME_FILES:
        if name != "sample_history_index.py":
            (candidate / name).write_text("# synthetic unused candidate\n", encoding="utf-8")
    monkeypatch.setattr(gate, "_load_module", lambda *_args: pytest.fail("must refuse before loading"))
    with pytest.raises(CandidateError, match="missing runtime files: sample_history_index.py"):
        validate_candidate_dir(candidate)


def test_history_deployment_candidate_namespace_restored(tmp_path, monkeypatch):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    for name in RUNTIME_FILES:
        (candidate / name).write_text("# synthetic unused candidate\n", encoding="utf-8")
    (candidate / "sample_history_index.py").write_text("MARKER='candidate-only'\n", encoding="utf-8")
    previous = ModuleType("sample_history_index")
    previous.MARKER = "host-must-not-be-used"
    monkeypatch.setitem(sys.modules, "sample_history_index", previous)
    original = gate._load_module
    seen = []
    def load(name, path):
        seen.append(name)
        if name == "upload_lease":
            assert sys.modules["sample_history_index"].MARKER == "candidate-only"
            assert Path(sys.modules["sample_history_index"].__file__).resolve() == candidate / "sample_history_index.py"
            raise CandidateError("synthetic stop before consumer or database")
        return original(name, path)
    monkeypatch.setattr(gate, "_load_module", load)
    with pytest.raises(CandidateError, match="synthetic stop"):
        validate_candidate_dir(candidate)
    assert seen[:3] == ["activation_core", "membership_card_store", "sample_history_index"]
    assert sys.modules["sample_history_index"] is previous


def test_history_deployment_real_cli_only_flag_omission_refused(tmp_path, monkeypatch):
    candidate = _copy_candidate(tmp_path / "candidate")
    original = gate.subprocess.Popen
    observed = []
    def launch(command, *args, **kwargs):
        command = list(command)
        if "--history-index-db-file" in command:
            index = command.index("--history-index-db-file")
            del command[index:index + 2]
            observed.append(True)
        return original(command, *args, **kwargs)
    monkeypatch.setattr(gate.subprocess, "Popen", launch)
    with pytest.raises(CandidateError, match="did not configure history index"):
        gate._real_collector_main_smoke(candidate, server, collector,
            secret=b"synthetic-history-cli", master_key="S0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0s=")
    assert observed == [True]


def test_history_deployment_full_shell_group_only_helper_omission_refused(tmp_path):
    requested = [name for name in (*RUNTIME, "activation_deploy_gate.py")
                 if name != "sample_history_index.py"]
    completed = _run_preflight(tmp_path, requested)
    assert completed.returncode != 0
    assert "sample_history_index.py" in completed.stdout + completed.stderr
    assert not (tmp_path / "home" / "bidking" / ".script_candidates").exists()


@pytest.mark.parametrize("fault", [None, "missing", "wrong_database"])
def test_history_deployment_actual_derived_database_checker(tmp_path, fault):
    import sample_history_index
    state = tmp_path / "public"
    state.mkdir()
    path = gate._candidate_history_fixture(state)
    if fault is None:
        index = sample_history_index.HistoryIndex(path, state / "samples.jsonl",
            state / "samples_quarantine.jsonl", projector=lambda *_: sample_history_index.Projection(),
            parser_version="synthetic-deploy-history-test")
        try:
            index.refresh()
            assert index.ready and index.summary()["main_unique"] == 1
            gate._verify_candidate_history_database(path)
        finally:
            index.close()
    elif fault == "missing":
        with pytest.raises(CandidateError, match="did not configure history index"):
            gate._verify_candidate_history_database(path)
    else:
        import sqlite3
        with sqlite3.connect(path) as connection:
            connection.execute("PRAGMA application_id=7")
        with pytest.raises(CandidateError, match="source/dedup contract failed"):
            gate._verify_candidate_history_database(path)


@pytest.mark.parametrize("route,code,payload,retryable", [
    ("healthz", 503, {"ok": False, "reason": "history_index_unavailable", "state": "loading"}, True),
    ("healthz", 503, {"ok": False, "reason": "history_index_unavailable", "state": "failed"}, False),
    ("healthz", 503, {"ok": False, "reason": "history_index_unavailable", "state": "unknown"}, False),
    ("activation-readiness", 503, {"ok": False, "reason": "history_index_unavailable", "state": "loading"}, False),
    ("healthz", 500, {"ok": False, "reason": "history_index_unavailable", "state": "loading"}, False),
    ("healthz", 503, {"ok": False, "reason": "state_error", "state": "loading"}, False),
    ("healthz", 503, {"ok": 0, "reason": "history_index_unavailable", "state": "loading"}, False),
    ("healthz", 503, {"ok": False, "reason": "history_index_unavailable", "state": "loading", "extra": 1}, False),
    ("healthz", 503, {"ok": False, "reason": "history_index_unavailable"}, False),
])
def test_history_loading_probe_exact_retryable_boundary(monkeypatch, route, code, payload, retryable):
    def fail(url, *, timeout):
        assert timeout == 2
        raise urllib.error.HTTPError(url, code, "synthetic", {}, io.BytesIO(json.dumps(payload).encode()))
    monkeypatch.setattr(gate.urllib.request, "build_opener", lambda *_: SimpleNamespace(open=fail))
    with pytest.raises(CandidateError) as caught:
        gate._read_loopback_json("http://127.0.0.1:1/" + route)
    assert isinstance(caught.value, gate.CandidateLoading) is retryable


@pytest.mark.parametrize("case", ["loading_then_ready", "perpetual_loading", "terminal_failure"])
def test_history_loading_original_poll_loop_bounded_and_failclosed(case):
    # Execute the exact producer's polling AST, without file init, HTTP or sleeps.
    tree = ast.parse((SCRIPTS / "activation_deploy_gate.py").read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "_real_collector_main_smoke")
    loop = next(node for node in ast.walk(function) if isinstance(node, ast.For)
                and isinstance(node.target, ast.Name) and node.target.id == "_attempt")
    calls, sleeps = [], []
    def read(url):
        calls.append(url)
        if url.endswith("/healthz"):
            if case == "terminal_failure":
                raise CandidateError("candidate runtime probe returned HTTP 503 (invalid)")
            if case == "perpetual_loading" or len(calls) == 1:
                raise gate.CandidateLoading("synthetic history loading")
            return {"ok": True}
        return {"ok": True, "service": "activation"}
    namespace = {"process": SimpleNamespace(poll=lambda: None), "port": 1,
                 "_read_loopback_json": read, "CandidateLoading": gate.CandidateLoading,
                 "CandidateError": CandidateError, "time": SimpleNamespace(sleep=sleeps.append),
                 "urllib": urllib, "last_type": "not_ready"}
    program = compile(ast.fix_missing_locations(ast.Module(body=[loop], type_ignores=[])),
                      "<exact-candidate-poll-loop>", "exec")
    if case == "loading_then_ready":
        exec(program, namespace)
        assert len(calls) == 3 and sleeps == [0.2]
    elif case == "perpetual_loading":
        with pytest.raises(CandidateError, match="history_loading"):
            exec(program, namespace)
        assert len(calls) == 30 and sleeps == [0.2] * 30
    else:
        with pytest.raises(CandidateError, match="HTTP 503"):
            exec(program, namespace)
        assert len(calls) == 1 and sleeps == []


def test_history_loading_transport_timeout_is_not_reclassified(monkeypatch):
    def fail(_url, *, timeout):
        assert timeout == 2
        raise TimeoutError("synthetic transport timeout")
    monkeypatch.setattr(gate.urllib.request, "build_opener", lambda *_: SimpleNamespace(open=fail))
    with pytest.raises(TimeoutError):
        gate._read_loopback_json("http://127.0.0.1:1/healthz")


def _break_dashboard_health_only(candidate):
    dashboard = candidate / "sample_dashboard.py"
    dashboard.write_text(dashboard.read_text(encoding="utf-8")
        + "\n\ndef build_dashboard_html(*args, **kwargs):\n    raise RuntimeError('synthetic mismatch')\n",
        encoding="utf-8")


def test_dashboard_health_fixture_preserves_actual_history_projection(tmp_path):
    candidate = _copy_candidate(tmp_path / "candidate")
    _break_dashboard_health_only(candidate)
    name = "_synthetic_candidate_dashboard_health"
    try:
        module = gate._load_module(name, candidate / "sample_dashboard.py")
        assert callable(module._dashboard_sample_allowed)
        assert type(module._dashboard_sample_allowed({})) is bool
        with pytest.raises(RuntimeError, match="synthetic mismatch"):
            module.build_dashboard_html([])
    finally:
        sys.modules.pop(name, None)


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SERVER_UPDATE = SCRIPTS / "server_update.sh"
RUNTIME = (
    "sample_dashboard.py",
    "build_match_records.py",
    "public_stats.py",
    "activation_core.py",
    "membership_card_store.py",
    "sample_history_index.py",
    "upload_lease.py",
    "activation_server.py",
    "activation_http_e2ee.py",
    "quote_preferences.py",
    "upload_identity_store.py",
    "upload_identity_db_admin.py",
    "activation_device_admin.py",
    "pet_usage_db_admin.py",
    "my_records.py",
    "sample_collector_server.py",
)

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import activation_core as core  # noqa: E402
import activation_server as server  # noqa: E402
import sample_collector_server as collector  # noqa: E402
import activation_deploy_gate as gate  # noqa: E402
from activation_deploy_gate import (  # noqa: E402
    CandidateError,
    RUNTIME_FILES,
    _write_private_fixture,
    validate_candidate_dir,
)
from upload_identity_store import initialize_upload_identity_database  # noqa: E402


def _runtime_source(name: str) -> Path:
    if name == "activation_http_e2ee.py":
        return ROOT / "src" / "bidking_lab" / "secure" / name
    if name == "quote_preferences.py":
        return ROOT / "src" / "bidking_lab" / "live" / name
    return SCRIPTS / name


def _copy_candidate(target: Path) -> Path:
    target.mkdir(parents=True)
    for name in RUNTIME:
        shutil.copy2(_runtime_source(name), target / name)
    return target


def _as_bash_path(bash: str, path: Path) -> str:
    if os.name != "nt":
        return path.as_posix()
    completed = subprocess.run(
        [bash, "-lc", 'cygpath -u "$1"', "bidking-path", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=5,
        check=True,
    )
    return completed.stdout.strip()


def _service(tmp_path: Path, *, membership: bool = False):
    pet_usage_db = tmp_path / "pet_usage.sqlite3"
    server.initialize_pet_usage_database(
        pet_usage_db,
        secret=b"readiness-test",
        legacy_paths=(
            tmp_path / "pet_usage.json",
            tmp_path / "pet_usage.anchor.jsonl",
        ),
    )
    membership_db = tmp_path / "membership.sqlite3"
    if membership:
        gate._initialize_candidate_membership(server, membership_db)
    return server.ActivationService(
        secret=b"readiness-test",
        master_key_b64="S0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0s=",
        bindings_path=tmp_path / "bindings.json",
        revoked_path=tmp_path / "revoked.txt",
        telemetry_path=tmp_path / "telemetry.jsonl",
        trials_path=tmp_path / "trials.json",
        paid_starts_path=tmp_path / "paid_starts.json",
        pet_usage_db_path=pet_usage_db,
        pet_usage_path=tmp_path / "pet_usage.json",
        membership_store_path=membership_db if membership else None,
    )


def test_candidate_private_fixture_is_no_clobber_and_posix_private(
    tmp_path: Path,
) -> None:
    path = tmp_path / "activation_http_e2ee_key.txt"
    payload = b"synthetic-private-fixture\n"

    _write_private_fixture(path, payload)

    assert path.read_bytes() == payload
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(CandidateError, match="cannot create private"):
        _write_private_fixture(path, b"must-not-clobber")
    assert path.read_bytes() == payload


def test_exact_candidate_group_passes_compile_paid_trial_roaming_and_readiness(tmp_path: Path):
    report = validate_candidate_dir(_copy_candidate(tmp_path / "candidate"))

    assert report == {
        "ok": True,
        "core_api": core.ACTIVATION_CORE_API_VERSION,
        "checks": [
            "compile",
            "collector_wiring",
            "collector_main_subprocess",
            "configured_membership_renewal",
            "configured_history_index",
            "unconfigured_legacy",
            "readiness_route",
            "activation_http_e2ee",
            "readiness",
            "bad_token",
            "trial",
            "roaming",
            "device_revoke",
            "device_block_key_separation",
            "pet_entitlement",
            "pet_usage",
        ],
    }


def test_candidate_gate_rejects_healthz_dashboard_runtime_mismatch(
    tmp_path: Path,
) -> None:
    candidate = _copy_candidate(tmp_path / "candidate")
    _break_dashboard_health_only(candidate)

    with pytest.raises(CandidateError, match=r"HTTP 500"):
        validate_candidate_dir(candidate)


def test_activation_candidate_group_includes_healthz_direct_dependencies() -> None:
    for name in ("sample_dashboard.py", "build_match_records.py", "public_stats.py"):
        assert name in RUNTIME_FILES
    update = SERVER_UPDATE.read_text(encoding="utf-8")
    group = update.split("ACTIVATION_GROUP=(", 1)[1].split(")", 1)[0]
    for name in ("sample_dashboard.py", "build_match_records.py", "public_stats.py"):
        assert name in group


def test_current_candidate_requires_membership_store_in_exact_groups() -> None:
    assert "membership_card_store.py" in RUNTIME_FILES
    update = SERVER_UPDATE.read_text(encoding="utf-8")
    for marker in ("FILES=(", "ACTIVATION_GROUP=("):
        assert "membership_card_store.py" in update.split(marker, 1)[1].split(")", 1)[0]


def test_candidate_missing_store_rejects_before_import_or_socket(tmp_path, monkeypatch):
    candidate = _copy_candidate(tmp_path / "candidate")
    (candidate / "membership_card_store.py").unlink()
    monkeypatch.setattr(gate, "_load_module", lambda *_: pytest.fail("must reject before imports"))
    with pytest.raises(CandidateError, match="missing runtime files: membership_card_store.py"):
        validate_candidate_dir(candidate)


@pytest.mark.parametrize("corrupt", [False, True])
def test_configured_candidate_store_missing_or_bad_refuses_not_legacy(tmp_path, corrupt):
    path = tmp_path / "membership.sqlite3"
    if corrupt:
        path.write_bytes(b"synthetic-not-sqlite")
    with pytest.raises(server.membership_card_store.MembershipStateError):
        server.ActivationService(
            secret=b"synthetic-candidate-missing-store",
            master_key_b64="S0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0s=",
            bindings_path=tmp_path / "bindings.json", revoked_path=tmp_path / "revoked.txt",
            telemetry_path=tmp_path / "telemetry.jsonl", trials_path=tmp_path / "trials.json",
            membership_store_path=path,
        )
    assert not (tmp_path / "bindings.json").exists()


def test_candidate_store_load_is_exact_and_exception_restores_host(tmp_path, monkeypatch):
    candidate = _copy_candidate(tmp_path / "candidate")
    host = ModuleType("membership_card_store")
    host.marker = "host-must-not-be-used"
    monkeypatch.setitem(sys.modules, "membership_card_store", host)
    real_load = gate._load_module
    observed = []

    def load(name, path):
        module = real_load(name, path)
        if name == "membership_card_store":
            observed.append(module)
        if name == "activation_server":
            assert module.membership_card_store is observed[0]
            assert Path(module.membership_card_store.__file__).resolve() == (
                candidate / "membership_card_store.py"
            ).resolve()
            raise RuntimeError("synthetic-stop-before-state-or-socket")
        return module

    monkeypatch.setattr(gate, "_load_module", load)
    with pytest.raises(RuntimeError, match="synthetic-stop-before-state-or-socket"):
        validate_candidate_dir(candidate)
    assert len(observed) == 1 and observed[0] is not host
    assert sys.modules["membership_card_store"] is host


def test_candidate_representative_renewal_checker_uses_actual_service(tmp_path):
    service = _service(tmp_path, membership=True)
    gate._verify_candidate_renewal(service.handle_activate, core, secret=service.secret)
    snapshot = service.membership_store.read("deploy-gate-renew-A", now=int(server.time.time()))
    assert snapshot.revision == 2
    assert len(service.membership_store.claims_for_account("deploy-gate-renew-A")) == 2


@pytest.mark.parametrize("fault", ["capability", "canonical", "feature", "full_pet", "replay"])
def test_candidate_renewal_checker_rejects_wrong_current_claims(tmp_path, fault):
    service = _service(tmp_path, membership=True)

    def request(body):
        code, response = service.handle_activate(body)
        response = dict(response)
        if fault == "capability":
            response.pop("membership_card_capability", None)
        elif response.get("card_redemption", {}).get("status") == "redeemed":
            lease = gate._signed_lease_payload(response)
            if fault == "canonical":
                lease["membership"]["canonical_uid"] = "synthetic-wrong-account"
            elif fault == "feature":
                lease["membership"]["private_dashboard_exp"] = lease["paid_exp"]
            elif fault == "full_pet":
                lease["feature_usage"]["pet_companion"]["entitlement_exp"] -= 86400
            response["lease"] = core.sign_lease(lease, service.secret)
        elif fault == "replay" and response.get("card_redemption", {}).get("status") == "replayed":
            response["card_redemption"] = {**response["card_redemption"],
                                            "membership_exp": response["card_redemption"]["membership_exp"] + 86400}
        return code, response

    with pytest.raises(CandidateError, match="candidate .* contract failed|candidate configured membership first use failed"):
        gate._verify_candidate_renewal(request, core, secret=service.secret)


def test_candidate_unconfigured_legacy_positive(tmp_path):
    service = _service(tmp_path)
    now = int(server.time.time())
    token = core.mint_token("synthetic-unconfigured", service.secret, iat=now, exp=now + 30 * 86400)
    code, body = service.handle_activate({"device": "synthetic-device", "token": token})
    assert code == 200 and body["ok"] is True
    assert service.membership_store is None
    assert "membership" not in gate._signed_lease_payload(body)
    assert "membership_card_capability" not in body
    assert not (tmp_path / "membership.sqlite3").exists()


def test_candidate_bad_store_import_rejects_and_restores_host(tmp_path, monkeypatch):
    candidate = _copy_candidate(tmp_path / "candidate")
    (candidate / "membership_card_store.py").write_text(
        "raise RuntimeError('synthetic-bad-candidate-store')\n", encoding="utf-8",
    )
    host = ModuleType("membership_card_store")
    monkeypatch.setitem(sys.modules, "membership_card_store", host)
    with pytest.raises(RuntimeError, match="synthetic-bad-candidate-store"):
        validate_candidate_dir(candidate)
    assert sys.modules["membership_card_store"] is host


def test_candidate_server_foreign_store_module_refused_before_state(tmp_path, monkeypatch):
    candidate = _copy_candidate(tmp_path / "candidate")
    host = ModuleType("membership_card_store")
    monkeypatch.setitem(sys.modules, "membership_card_store", host)
    real_load = gate._load_module

    def load(name, path):
        module = real_load(name, path)
        if name == "activation_server":
            module.membership_card_store = host
        return module

    monkeypatch.setattr(gate, "_load_module", load)
    with pytest.raises(CandidateError, match="exact candidate membership module"):
        validate_candidate_dir(candidate)
    assert sys.modules["membership_card_store"] is host


def test_real_candidate_popen_explicitly_uses_utf8_stdio_environment():
    tree = ast.parse((SCRIPTS / "activation_deploy_gate.py").read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "_real_collector_main_smoke")
    calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == "Popen"]
    assert len(calls) == 1
    env = next((keyword.value for keyword in calls[0].keywords if keyword.arg == "env"), None)
    assert isinstance(env, ast.Call) and isinstance(env.func, ast.Name)
    assert env.func.id == "_candidate_child_stdio_environment"


def test_candidate_utf8_stdio_environment_overrides_cp1252_without_parent_mutation(monkeypatch):
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    monkeypatch.setenv("PYTHONUTF8", "0")
    monkeypatch.setenv("BIDKING_SYNTHETIC_ENV_SENTINEL", "keep")
    env = gate._candidate_child_stdio_environment()
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONUTF8"] == "0" and env["BIDKING_SYNTHETIC_ENV_SENTINEL"] == "keep"
    assert os.environ["PYTHONIOENCODING"] == "cp1252"


def test_actual_collector_chinese_log_statements_cp1252_red_utf8_green(monkeypatch):
    # Execute the two actual startup print nodes implicated by the captured
    # failure, with synthetic values; no service, socket, state or credentials.
    source = (SCRIPTS / "sample_collector_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    prints = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name) and node.value.func.id == "print"):
            continue
        statement = ast.get_source_segment(source, node)
        for marker in ("历史索引状态=", "历史索引已就绪 main="):
            if marker in statement:
                assert marker not in prints
                prints[marker] = ast.unparse(node)
    assert len(prints) == 2
    program = (
        "from types import SimpleNamespace; import sys, time\n"
        "args=SimpleNamespace(host='127.0.0.1', port=0); _OUT='synthetic-owned-output'\n"
        "ing=auth=exp=ver=act='synthetic'; _RATE_MAX=1; _RATE_WINDOW=1\n"
        "_history_index_state=lambda: 'ready'; _SEEN=set(); _QUAR_SEEN=set(); started=time.monotonic()\n"
        "counts={'main_unique':0, 'quarantine_unique':0}\n"
        + "\n".join(prints.values())
        + "\nprint('标准错误', file=sys.stderr, flush=True)\n"
    )
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    monkeypatch.setenv("PYTHONUTF8", "0")
    command = [sys.executable, "-B", "-c", program]
    red = subprocess.run(command, env=os.environ.copy(), capture_output=True, timeout=5)
    assert red.returncode != 0 and b"UnicodeEncodeError" in red.stderr
    green = subprocess.run(command, env=gate._candidate_child_stdio_environment(),
                           capture_output=True, timeout=5)
    assert green.returncode == 0
    output = green.stdout.decode("utf-8", errors="strict")
    assert "历史索引状态=" in output and "历史索引已就绪 main=" in output
    assert green.stderr.decode("utf-8", errors="strict").strip() == "标准错误"


def test_candidate_shared_device_block_secret_is_rejected_by_preflight(
    tmp_path: Path,
) -> None:
    candidate = _copy_candidate(tmp_path / "candidate")
    server_path = candidate / "activation_server.py"
    text = server_path.read_text(encoding="utf-8").replace(
        "if hmac.compare_digest(secret_digest, block_digest):",
        "if False and hmac.compare_digest(secret_digest, block_digest):",
        1,
    )
    assert "if False and hmac.compare_digest" in text
    server_path.write_text(text, encoding="utf-8")

    with pytest.raises(CandidateError, match="device block key separation"):
        validate_candidate_dir(candidate)


def test_candidate_blank_key_presence_cannot_be_collapsed_to_missing(
    tmp_path: Path,
) -> None:
    candidate = _copy_candidate(tmp_path / "candidate")
    collector_path = candidate / "sample_collector_server.py"
    text = collector_path.read_text(encoding="utf-8").replace(
        "device_block_key=_device_block_key,",
        "device_block_key=(_device_block_key or None),",
        1,
    )
    assert "device_block_key=(_device_block_key or None)," in text
    collector_path.write_text(text, encoding="utf-8")

    with pytest.raises(CandidateError, match="zero-byte classification"):
        validate_candidate_dir(candidate)


def test_candidate_missing_strict_timestamp_fails_before_deploy(tmp_path: Path):
    candidate = _copy_candidate(tmp_path / "candidate")
    core_path = candidate / "activation_core.py"
    text = core_path.read_text(encoding="utf-8").replace(
        "def _strict_state_timestamp(",
        "def _removed_strict_state_timestamp(",
        1,
    )
    core_path.write_text(text, encoding="utf-8")

    with pytest.raises(RuntimeError, match="activation_core_symbol_missing"):
        validate_candidate_dir(candidate)


def test_syntax_valid_broken_collector_service_wiring_fails_before_deploy(tmp_path: Path):
    candidate = _copy_candidate(tmp_path / "candidate")
    collector_path = candidate / "sample_collector_server.py"
    text = collector_path.read_text(encoding="utf-8").replace(
        "min_client_version=args.min_client_version,",
        "missing_collector_parameter=args.min_client_version,",
        1,
    )
    assert "missing_collector_parameter=" in text
    collector_path.write_text(text, encoding="utf-8")

    with pytest.raises(TypeError, match="missing_collector_parameter"):
        validate_candidate_dir(candidate)


def test_candidate_missing_activation_readiness_route_fails_before_deploy(tmp_path: Path):
    candidate = _copy_candidate(tmp_path / "candidate")
    collector_path = candidate / "sample_collector_server.py"
    text = collector_path.read_text(encoding="utf-8").replace(
        'if self.path == "/activation-readiness":',
        'if self.path == "/activation-readiness-disabled":',
        1,
    )
    assert "/activation-readiness-disabled" in text
    collector_path.write_text(text, encoding="utf-8")

    with pytest.raises(CandidateError, match="readiness route failed"):
        validate_candidate_dir(candidate)


def test_readiness_is_non_writing_and_missing_dependency_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    service = _service(tmp_path)
    before = sorted(path.name for path in tmp_path.iterdir())

    assert server.activation_readiness(service) == (
        200,
        {"ok": True, "service": "activation"},
    )
    assert sorted(path.name for path in tmp_path.iterdir()) == before

    monkeypatch.delattr(core, "_strict_state_timestamp")
    code, body = server.activation_readiness(service)
    assert code == 503
    assert body == {
        "ok": False,
        "service": "activation",
        "reason": "activation_core_symbol_missing",
    }
    assert sorted(path.name for path in tmp_path.iterdir()) == before


def test_public_readiness_endpoint_uses_constructed_service_without_activate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    service = _service(tmp_path)
    monkeypatch.setattr(collector, "_ACT", service)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), collector.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{httpd.server_port}/activation-readiness"
        with urllib.request.urlopen(url, timeout=3) as response:
            assert response.status == 200
            assert json.loads(response.read()) == {"ok": True, "service": "activation"}

        monkeypatch.delattr(core, "_strict_state_timestamp")
        with pytest.raises(urllib.error.HTTPError) as captured:
            urllib.request.urlopen(url, timeout=3)
        assert captured.value.code == 503
        assert json.loads(captured.value.read()) == {
            "ok": False,
            "service": "activation",
            "reason": "activation_core_symbol_missing",
        }
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)


def _run_preflight(
    tmp_path: Path,
    requested: list[str],
    *,
    break_core: bool = False,
    state_directory_declared: bool = True,
    database_present: bool = True,
    head_present: bool = True,
    legacy_present: bool = False,
    upload_database_valid: bool = True,
):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is unavailable")
    home = tmp_path / "home"
    deployed = home / "bidking" / "scripts"
    home.mkdir(parents=True)
    deployed.mkdir(parents=True)
    pet_state = tmp_path / "pet-state"
    pet_state.mkdir()
    upload_state = tmp_path / "upload-state"
    upload_state.mkdir()
    if upload_database_valid:
        initialize_upload_identity_database(upload_state / "upload_identity.sqlite3")
    else:
        (upload_state / "upload_identity.sqlite3").write_bytes(b"not-sqlite")
    if database_present:
        (pet_state / "pet_usage.sqlite3").write_bytes(b"deploy-placeholder")
    if head_present:
        (pet_state / "pet_usage.head.jsonl").write_bytes(b"deploy-placeholder\n")
    if legacy_present:
        (home / "bidking" / "pet_usage.json").write_text(
            "{}",
            encoding="utf-8",
        )
    for name in RUNTIME + ("activation_deploy_gate.py",):
        shutil.copy2(_runtime_source(name), home / name)
        shutil.copy2(_runtime_source(name), deployed / name)
    if break_core:
        path = home / "activation_core.py"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "def _strict_state_timestamp(",
                "def _removed_strict_state_timestamp(",
                1,
            ),
            encoding="utf-8",
        )
    shutil.copy2(SCRIPTS / "web_ui.py", home / "web_ui.py")
    test_bin = tmp_path / "test-bin"
    test_bin.mkdir()
    python3 = test_bin / "python3"
    _write_executable(
        python3,
        f'#!/usr/bin/env bash\nexec "{Path(sys.executable).as_posix()}" "$@"\n',
    )
    _write_executable(
        test_bin / "systemctl",
        """#!/usr/bin/env bash
if [ "$1" = "show" ]; then
  printf '%s\n' "${BIDKING_TEST_STATE_DIRECTORY_DECLARATION:-}"
  exit 0
fi
exit 0
""",
    )
    test_bin_bash = _as_bash_path(bash, test_bin)
    bash_env = tmp_path / "bash_env"
    bash_env.write_text(
        f'export PATH="{test_bin_bash}:$PATH"\n',
        encoding="utf-8",
        newline="\n",
    )
    env = os.environ.copy()
    env.update(
        HOME=home.as_posix(),
        BIDKING_SERVER_UPDATE_PREFLIGHT_ONLY="1",
        BIDKING_SERVER_UPDATE_TEST_MODE="1",
        BIDKING_TEST_PET_STATE_DIRECTORY=pet_state.as_posix(),
        BIDKING_TEST_UPLOAD_IDENTITY_STATE_DIRECTORY=upload_state.as_posix(),
        BIDKING_TEST_STATE_DIRECTORY_DECLARATION=(
            "bidking-activation-anchor bidking-upload-identity"
            if state_directory_declared else ""
        ),
        BASH_ENV=_as_bash_path(bash, bash_env),
    )
    return subprocess.run(
        [bash, str(SERVER_UPDATE), *requested],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
        env=env,
    )


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _run_full_deploy(
    tmp_path: Path,
    *,
    readiness_mode: str,
    first_restart_fails: bool = False,
    restart_fail_calls: str = "",
    restore_mode: str = "",
    install_fail_file: str = "",
):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is unavailable")
    home = tmp_path / "home"
    deployed = home / "bidking" / "scripts"
    home.mkdir(parents=True)
    deployed.mkdir(parents=True)
    pet_state = tmp_path / "pet-state"
    pet_state.mkdir()
    upload_state = tmp_path / "upload-state"
    upload_state.mkdir()
    initialize_upload_identity_database(upload_state / "upload_identity.sqlite3")
    (pet_state / "pet_usage.sqlite3").write_bytes(b"deploy-placeholder")
    (pet_state / "pet_usage.head.jsonl").write_bytes(b"deploy-placeholder\n")
    deploy_files = (*RUNTIME, "activation_deploy_gate.py")
    originals: dict[str, bytes] = {}
    for name in deploy_files:
        source = _runtime_source(name)
        shutil.copy2(source, home / name)
        shutil.copy2(source, deployed / name)
        originals[name] = (deployed / name).read_bytes()
    collector_candidate = home / "sample_collector_server.py"
    collector_candidate.write_text(
        collector_candidate.read_text(encoding="utf-8")
        + "\n# BIDKING_TEST_CANDIDATE\n",
        encoding="utf-8",
    )

    test_bin = tmp_path / "test-bin"
    test_bin.mkdir()
    _write_executable(
        test_bin / "python3",
        f'#!/usr/bin/env bash\nexec "{Path(sys.executable).as_posix()}" "$@"\n',
    )
    _write_executable(
        test_bin / "sudo",
        '#!/usr/bin/env bash\nexec "$@"\n',
    )
    _write_executable(
        test_bin / "sleep",
        "#!/usr/bin/env bash\nexit 0\n",
    )
    _write_executable(
        test_bin / "systemctl",
        """#!/usr/bin/env bash
if [ "$1" = "show" ]; then
  printf '%s\n' 'bidking-activation-anchor bidking-upload-identity'
  exit 0
fi
count_file="$HOME/systemctl_calls"
count=0
[ -f "$count_file" ] && count="$(cat "$count_file")"
count=$((count + 1))
printf '%s\n' "$count" > "$count_file"
if [ "${BIDKING_TEST_RESTORE_MODE:-}" = "missing-backup" ] && [ "$count" -eq 1 ]; then
  backup_file="$(find "$HOME/bidking/.script_bak" -type f \
    -name sample_collector_server.py | head -n 1)"
  [ -n "$backup_file" ] && rm -f "$backup_file"
fi
case ",${BIDKING_TEST_RESTART_FAIL_CALLS:-}," in
  *",$count,"*)
    exit 1
    ;;
esac
if [ "${BIDKING_TEST_FIRST_RESTART_FAILS:-0}" = "1" ] && [ "$count" -eq 1 ]; then
  exit 1
fi
exit 0
""",
    )
    _write_executable(
        test_bin / "cp",
        """#!/usr/bin/env bash
if [ "${BIDKING_TEST_RESTORE_MODE:-}" = "copy-fail" ]; then
  source_path="${@: -2:1}"
  target_path="${!#}"
  if [[ "$source_path" == *"/.script_bak/"*"/sample_collector_server.py" ]] \
    && [[ "$target_path" == *"/bidking/scripts/sample_collector_server.py" ]]; then
    echo "forced restore copy failure" >&2
    exit 1
  fi
fi
exec /usr/bin/cp "$@"
""",
    )
    _write_executable(
        test_bin / "install",
        """#!/usr/bin/env bash
target="${!#}"
if [ -n "${BIDKING_TEST_INSTALL_FAIL_FILE:-}" ]; then
  case "$target" in
    *"/${BIDKING_TEST_INSTALL_FAIL_FILE}")
      echo "forced install failure" >&2
      exit 1
      ;;
  esac
fi
exec /usr/bin/install "$@"
""",
    )
    _write_executable(
        test_bin / "curl",
        """#!/usr/bin/env bash
url="${!#}"
printf '%s\n' "$url" >> "$HOME/curl_calls"
case "$url" in
  *"/healthz")
    printf '%s' '{"ok":true}'
    exit 0
    ;;
  *"/activation-readiness")
    if [ "${BIDKING_TEST_READINESS_MODE:-}" = "always-fail" ]; then
      echo "forced readiness failure" >&2
      exit 22
    fi
    if grep -q "BIDKING_TEST_CANDIDATE" \
      "$HOME/bidking/scripts/sample_collector_server.py"; then
      echo "candidate readiness failure" >&2
      exit 22
    fi
    printf '%s' '{"ok":true,"service":"activation"}'
    exit 0
    ;;
esac
echo "unexpected curl URL: $url" >&2
exit 2
""",
    )
    test_bin_bash = _as_bash_path(bash, test_bin)
    bash_env = tmp_path / "bash_env"
    bash_env.write_text(
        f'export PATH="{test_bin_bash}:$PATH"\n',
        encoding="utf-8",
        newline="\n",
    )
    env = os.environ.copy()
    env.update(
        HOME=home.as_posix(),
        BASH_ENV=_as_bash_path(bash, bash_env),
        BIDKING_TEST_READINESS_MODE=readiness_mode,
        BIDKING_TEST_FIRST_RESTART_FAILS="1" if first_restart_fails else "0",
        BIDKING_TEST_RESTART_FAIL_CALLS=restart_fail_calls,
        BIDKING_TEST_RESTORE_MODE=restore_mode,
        BIDKING_TEST_INSTALL_FAIL_FILE=install_fail_file,
        BIDKING_SERVER_UPDATE_TEST_MODE="1",
        BIDKING_TEST_PET_STATE_DIRECTORY=pet_state.as_posix(),
        BIDKING_TEST_UPLOAD_IDENTITY_STATE_DIRECTORY=upload_state.as_posix(),
    )
    completed = subprocess.run(
        [bash, str(SERVER_UPDATE), *deploy_files],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=40,
        check=False,
        env=env,
    )
    return completed, home, deployed, originals


def test_server_update_rejects_partial_activation_group(tmp_path: Path):
    completed = _run_preflight(tmp_path, ["activation_core.py"])

    assert completed.returncode != 0
    assert "不可拆分部署组" in completed.stdout + completed.stderr


def test_server_update_checks_exact_group_and_ignores_known_home_leftovers(tmp_path: Path):
    requested = [*RUNTIME, "activation_deploy_gate.py"]
    completed = _run_preflight(tmp_path, requested)

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "候选组合预检通过" in completed.stdout
    assert "PREFLIGHT ONLY" in completed.stdout
    assert "将保留且不部署的 HOME 候选: web_ui.py" in completed.stdout


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        (
            {"state_directory_declared": False},
            "systemd unit 未声明 StateDirectory=bidking-activation-anchor",
        ),
        (
            {"database_present": False},
            "Pet SQLite authority 未离线初始化",
        ),
        (
            {"head_present": False},
            "Pet external head 未离线初始化",
        ),
        (
            {"legacy_present": True},
            "发现旧Pet JSON状态",
        ),
        (
            {"upload_database_valid": False},
            "上传身份数据库 schema/quick_check 未通过",
        ),
    ],
)
def test_server_update_refuses_unprepared_pet_authority(
    tmp_path: Path,
    kwargs: dict,
    expected: str,
):
    completed = _run_preflight(
        tmp_path,
        [*RUNTIME, "activation_deploy_gate.py"],
        **kwargs,
    )

    assert completed.returncode != 0
    assert expected in completed.stdout + completed.stderr


def test_server_update_rejects_mixed_activation_candidate(tmp_path: Path):
    completed = _run_preflight(
        tmp_path,
        [*RUNTIME, "activation_deploy_gate.py"],
        break_core=True,
    )

    assert completed.returncode != 0
    assert "activation_core_symbol_missing" in completed.stdout + completed.stderr


def test_server_update_requires_exact_readiness_payload():
    text = SERVER_UPDATE.read_text(encoding="utf-8")

    assert 'payload.get("ok") is True' in text
    assert 'payload.get("service") == "activation"' in text
    assert "HTTP 200 但 activation readiness 载荷不合格" in text


def test_install_readiness_failure_rolls_back_and_verifies_both_health_gates(
    tmp_path: Path,
):
    completed, home, deployed, originals = _run_full_deploy(
        tmp_path,
        readiness_mode="candidate-fails",
    )

    combined = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "candidate_health_or_readiness_failed" in combined
    assert "通过普通健康与适用的激活 readiness 验收" in combined
    assert "回滚验收完成" in combined
    assert (home / "systemctl_calls").read_text(encoding="utf-8").strip() == "2"
    calls = (home / "curl_calls").read_text(encoding="utf-8").splitlines()
    assert sum(url.endswith("/healthz") for url in calls) >= 2
    assert sum(url.endswith("/activation-readiness") for url in calls) >= 6
    for name, expected in originals.items():
        assert (deployed / name).read_bytes() == expected


def test_install_and_rollback_readiness_failure_is_never_reported_as_recovered(
    tmp_path: Path,
):
    completed, home, deployed, originals = _run_full_deploy(
        tmp_path,
        readiness_mode="always-fail",
    )

    combined = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "回滚后未通过普通健康/激活 readiness 双门" in combined
    assert "回滚后仍未恢复" in combined
    assert "回滚验收完成" not in combined
    calls = (home / "curl_calls").read_text(encoding="utf-8").splitlines()
    assert sum(url.endswith("/activation-readiness") for url in calls) >= 10
    for name, expected in originals.items():
        assert (deployed / name).read_bytes() == expected


def test_candidate_restart_failure_rolls_back_then_verifies_both_health_gates(
    tmp_path: Path,
):
    completed, home, deployed, originals = _run_full_deploy(
        tmp_path,
        readiness_mode="candidate-fails",
        first_restart_fails=True,
    )

    combined = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "candidate_restart_failed" in combined
    assert "通过普通健康与适用的激活 readiness 验收" in combined
    assert (home / "systemctl_calls").read_text(encoding="utf-8").strip() == "2"
    calls = (home / "curl_calls").read_text(encoding="utf-8").splitlines()
    assert sum(url.endswith("/healthz") for url in calls) >= 1
    assert sum(url.endswith("/activation-readiness") for url in calls) >= 1
    for name, expected in originals.items():
        assert (deployed / name).read_bytes() == expected


def test_restore_copy_failure_stops_before_restart_and_never_reports_success(
    tmp_path: Path,
):
    completed, home, deployed, _originals = _run_full_deploy(
        tmp_path,
        readiness_mode="candidate-fails",
        restore_mode="copy-fail",
    )

    combined = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "恢复文件失败: sample_collector_server.py" in combined
    assert "禁止重启并禁止报告恢复成功" in combined
    assert "回滚验收完成" not in combined
    assert (home / "systemctl_calls").read_text(encoding="utf-8").strip() == "1"
    assert b"BIDKING_TEST_CANDIDATE" in (
        deployed / "sample_collector_server.py"
    ).read_bytes()


def test_missing_backup_metadata_stops_before_restart_and_never_reports_success(
    tmp_path: Path,
):
    completed, home, deployed, _originals = _run_full_deploy(
        tmp_path,
        readiness_mode="candidate-fails",
        restore_mode="missing-backup",
    )

    combined = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "回滚元数据不完整或冲突: sample_collector_server.py" in combined
    assert "禁止重启并禁止报告恢复成功" in combined
    assert "回滚验收完成" not in combined
    assert (home / "systemctl_calls").read_text(encoding="utf-8").strip() == "1"
    assert b"BIDKING_TEST_CANDIDATE" in (
        deployed / "sample_collector_server.py"
    ).read_bytes()


def test_second_restart_failure_after_restore_is_explicit_and_not_recovered(
    tmp_path: Path,
):
    completed, home, deployed, originals = _run_full_deploy(
        tmp_path,
        readiness_mode="candidate-fails",
        restart_fail_calls="2",
    )

    combined = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "回滚文件已恢复，但服务重启失败" in combined
    assert "自动恢复未通过" not in combined
    assert "回滚验收完成" not in combined
    assert (home / "systemctl_calls").read_text(encoding="utf-8").strip() == "2"
    for name, expected in originals.items():
        assert (deployed / name).read_bytes() == expected


def test_install_interruption_restores_bytes_then_verifies_both_health_gates(
    tmp_path: Path,
):
    completed, home, deployed, originals = _run_full_deploy(
        tmp_path,
        readiness_mode="candidate-fails",
        install_fail_file="sample_collector_server.py",
    )

    combined = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "candidate_install_failed" in combined
    assert "通过普通健康与适用的激活 readiness 验收" in combined
    assert (home / "systemctl_calls").read_text(encoding="utf-8").strip() == "1"
    calls = (home / "curl_calls").read_text(encoding="utf-8").splitlines()
    assert sum(url.endswith("/healthz") for url in calls) >= 1
    assert sum(url.endswith("/activation-readiness") for url in calls) >= 1
    for name, expected in originals.items():
        assert (deployed / name).read_bytes() == expected
