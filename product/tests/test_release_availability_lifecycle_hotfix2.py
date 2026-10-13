from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
from typing import Any

import pytest

from bidking_lab.live.fact_receipts import FactReceiptWriter


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check_hotfix3_real_drop_pool_quote_runtime.py"
BUILD = ROOT / "scripts" / "build_live_portable.ps1"
MONITOR = ROOT / "scripts" / "run_fatbeans_webhook_monitor.py"
PUBLISHER = ROOT / "scripts" / "publish_release_availability_failure_receipt.py"
PYTHON = Path(r"C:\Python313\python.exe")


def _load_checker():
    name = "release_availability_lifecycle_hotfix2_checker"
    spec = importlib.util.spec_from_file_location(name, CHECKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_path(name: str, path: Path):
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _monitor_config(module: Any, root: Path) -> Any:
    return module.WebhookMonitorConfig(
        log_dir=root / "logs",
        raw_dir=root / "raw",
        process_name="BidKing.exe",
        server_ports=(10000,),
        n_trials=20,
        roi_trials=0,
        shadow_trials=20,
        full_shadow_trials=20,
        run_debug_shadows=False,
        seed=1,
    )


class _HoldingWriter:
    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "fact-receipt.json.tmp"
        self.handle = self.path.open("wb")
        self.stop_event = threading.Event()
        self._worker = threading.Thread(target=self.stop_event.wait, daemon=True)
        self._worker.start()

    def close(self) -> None:
        self.stop_event.set()
        self._worker.join(timeout=2)
        self.handle.close()


class _FailingWriter:
    def __init__(self) -> None:
        self._worker = threading.Thread()

    def close(self) -> None:
        raise PermissionError("sensitive-cleanup-detail")


class _ClosingThenFailingWriter(_HoldingWriter):
    def close(self) -> None:
        super().close()
        raise PermissionError("sensitive-cleanup-detail")


class _ConstructorBaseFailure(BaseException):
    pass


class _FakeMonitor:
    created: list["_FakeMonitor"] = []
    writer_factory: Any = _HoldingWriter

    def __init__(self, *, config: Any, tables: Any) -> None:
        del tables
        self.config = config
        self._writer_root = config.log_dir
        self._fact_receipts = self.writer_factory(config.log_dir)
        self._worker = threading.Thread()
        self._last_requested_count = 0
        self.created.append(self)

    def accept_row(
        self, row: dict[str, object], *, schedule_process: bool
    ) -> dict[str, object]:
        assert row and schedule_process
        self._last_requested_count += 1
        return {"accepted": True}

    def _process_snapshot(self, *, force: bool) -> None:
        assert force
        _FakeMonitorScript.write_monitor_logs(
            {
                "map_id": 2409,
                "model_map_id": 2409,
                "phase": 4,
                "round": 1,
                "drop_pool_support": {
                    "status": "legacy_incomplete_conditional_known_support",
                    "quote_quality": "degraded",
                    "unresolved_probability_ppm": 50,
                },
            }
        )


class _FakeMonitorScript:
    WebhookMonitorConfig = SimpleNamespace
    FatbeansWebhookMonitor = _FakeMonitor

    @staticmethod
    def write_monitor_logs(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("unpatched_writer")


def _panel(*, failure: BaseException | None = None) -> Any:
    def summarize_snapshot(*_args: object, **_kwargs: object) -> dict[str, object]:
        if failure is not None:
            raise failure
        return {
            "status": "ok",
            "ahmed_ref": {
                "status": "ok",
                "conservative": 1,
                "balanced": 2,
                "aggressive": 3,
            },
            "flags": [{"label": "掉落资料降级"}],
        }

    return SimpleNamespace(summarize_snapshot=summarize_snapshot)


def _run_capture(checker: Any, *, panel: Any) -> None:
    _FakeMonitor.created.clear()

    def monitor_factory(*, config: Any, tables: Any) -> _FakeMonitor:
        return _FakeMonitor(config=config, tables=tables)

    _FakeMonitorScript.FatbeansWebhookMonitor = monitor_factory
    checker._check_monitor_capture(  # noqa: SLF001
        rows=[{"SortID": 1}],
        contract={
            "prefix_rows": 1,
            "map_id": 2409,
            "model_map_id": 2409,
            "phase": 4,
            "round": 1,
            "unresolved_probability_ppm": 50,
        },
        tables=object(),
        monitor_script=_FakeMonitorScript,
        panel=panel,
    )


def test_monitor_capture_closes_writer_thread_before_temporary_directory_exit() -> None:
    checker = _load_checker()
    _FakeMonitor.writer_factory = _HoldingWriter

    _run_capture(checker, panel=_panel())

    assert len(_FakeMonitor.created) == 1
    monitor = _FakeMonitor.created[0]
    assert not monitor._fact_receipts._worker.is_alive()
    assert not monitor._writer_root.parent.exists()


def test_real_fact_receipt_writer_and_partial_monitor_are_safe_to_close(
    tmp_path: Path,
) -> None:
    checker = _load_checker()
    writer = FactReceiptWriter(tmp_path)
    monitor = SimpleNamespace(_worker=threading.Thread(), _fact_receipts=writer)
    assert writer._worker.is_alive()  # noqa: SLF001

    checker._close_monitor_capture(monitor)  # noqa: SLF001
    checker._close_monitor_capture(None)  # noqa: SLF001
    checker._close_monitor_capture(SimpleNamespace())  # noqa: SLF001

    assert not writer._worker.is_alive()  # noqa: SLF001
    assert list(tmp_path.rglob("*.tmp")) == []


def test_product_assertion_precedes_cleanup_failure() -> None:
    checker = _load_checker()
    _FakeMonitor.writer_factory = staticmethod(lambda _root: _FailingWriter())

    with pytest.raises(AssertionError, match="product-assertion"):
        _run_capture(checker, panel=_panel(failure=AssertionError("product-assertion")))


def test_cleanup_failure_without_product_error_is_fixed_and_safe() -> None:
    checker = _load_checker()
    _FakeMonitor.writer_factory = staticmethod(lambda _root: _FailingWriter())

    with pytest.raises(
        RuntimeError, match="^monitor_capture_cleanup_failed$"
    ) as caught:
        _run_capture(checker, panel=_panel())

    assert "sensitive-cleanup-detail" not in str(caught.value)


@pytest.mark.parametrize(
    "failure",
    (
        AssertionError("constructor-primary"),
        SystemExit("constructor-primary"),
        KeyboardInterrupt("constructor-primary"),
        _ConstructorBaseFailure("constructor-primary"),
    ),
    ids=("assertion", "system-exit", "keyboard-interrupt", "base-exception"),
)
@pytest.mark.parametrize(
    "failure_point",
    ("cache-path", "cache-load"),
)
def test_actual_monitor_constructor_closes_started_writer_before_reraising(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
    failure_point: str,
) -> None:
    module = _load_path(
        f"release_availability_monitor_{failure_point}_{type(failure).__name__}",
        MONITOR,
    )
    writers: list[FactReceiptWriter] = []

    def writer_factory(root: Path) -> FactReceiptWriter:
        writer = FactReceiptWriter(root)
        writers.append(writer)
        return writer

    def fail(*_args: object, **_kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(module, "FactReceiptWriter", writer_factory)
    monkeypatch.setattr(
        module,
        "_local_player_cache_path"
        if failure_point == "cache-path"
        else "_load_cached_local_player_id",
        fail,
    )

    with tempfile.TemporaryDirectory(dir=tmp_path) as temporary:
        root = Path(temporary)
        with pytest.raises(type(failure)) as caught:
            module.FatbeansWebhookMonitor(
                config=_monitor_config(module, root),
                tables=object(),
            )
        assert caught.value is failure
        assert len(writers) == 1
        assert not writers[0]._worker.is_alive()  # noqa: SLF001
        assert list(root.rglob("*.tmp")) == []


def test_actual_monitor_constructor_preserves_primary_when_close_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_path("release_availability_monitor_close_failure", MONITOR)
    writers: list[_ClosingThenFailingWriter] = []
    primary = AssertionError("constructor-primary")

    def writer_factory(root: Path) -> _ClosingThenFailingWriter:
        writer = _ClosingThenFailingWriter(root)
        writers.append(writer)
        return writer

    def fail(_path: Path) -> None:
        raise primary

    monkeypatch.setattr(module, "FactReceiptWriter", writer_factory)
    monkeypatch.setattr(module, "_load_cached_local_player_id", fail)

    with pytest.raises(AssertionError) as caught:
        module.FatbeansWebhookMonitor(
            config=_monitor_config(module, tmp_path),
            tables=object(),
        )

    assert caught.value is primary
    assert len(writers) == 1
    assert not writers[0]._worker.is_alive()
    assert "sensitive-cleanup-detail" not in str(caught.value)


def test_actual_monitor_normal_constructor_semantics_remain_available(
    tmp_path: Path,
) -> None:
    module = _load_path("release_availability_monitor_normal", MONITOR)
    monitor = module.FatbeansWebhookMonitor(
        config=_monitor_config(module, tmp_path),
        tables=object(),
    )
    try:
        assert monitor._worker.name == "fatbeans-webhook-monitor"  # noqa: SLF001
        assert not monitor._worker.is_alive()  # noqa: SLF001
        assert monitor._received == 0  # noqa: SLF001
    finally:
        monitor._fact_receipts.close()  # noqa: SLF001


def _ps_quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _load_publisher():
    return _load_path("release_availability_failure_receipt_publisher", PUBLISHER)


def _canonical_failure(error_type: str = "PermissionError") -> bytes:
    return (
        '{"schema":"bidking_release_availability_receipt_v1",'
        f'"status":"fail","error":"{error_type}"}}\n'
    ).encode("ascii")


def test_failure_receipt_publisher_binds_exact_identity_and_content(
    tmp_path: Path,
) -> None:
    publisher = _load_publisher()
    output = tmp_path / "safe-failure.json"

    receipt = publisher.publish_failure_receipt(output, "PermissionError")

    assert receipt["status"] == "published"
    assert receipt["schema"] == publisher.SCHEMA
    assert receipt["number_of_links"] == 1
    assert receipt["bytes"] == len(_canonical_failure())
    assert receipt["sha256"] == hashlib.sha256(_canonical_failure()).hexdigest()
    assert output.read_bytes() == _canonical_failure()
    assert output.stat().st_ino == receipt["file_index"]


def test_failure_receipt_publisher_rejects_same_content_new_file_identity(
    tmp_path: Path,
) -> None:
    publisher = _load_publisher()
    output = tmp_path / "safe-failure.json"
    retained = tmp_path / "bound-original.json"

    def replace_after_move(path: Path, _baseline: dict[str, object]) -> None:
        raw = path.read_bytes()
        path.replace(retained)
        path.write_bytes(raw)

    with pytest.raises(publisher.PublicationError, match="target_identity_changed"):
        publisher.publish_failure_receipt(
            output,
            "PermissionError",
            after_move=replace_after_move,
        )

    assert output.read_bytes() == _canonical_failure()
    assert retained.read_bytes() == _canonical_failure()
    assert output.stat().st_ino != retained.stat().st_ino


@pytest.mark.parametrize(
    "mutation",
    (
        "missing",
        "delete-pending",
        "directory",
        "junction",
        "hardlink",
        "timestamp",
        "attributes",
        "replacement-size",
        "replacement-hash",
    ),
)
def test_failure_receipt_publisher_fails_closed_on_identity_or_metadata_race(
    tmp_path: Path,
    mutation: str,
) -> None:
    publisher = _load_publisher()
    output = tmp_path / "safe-failure.json"
    retained = tmp_path / f"retained-{mutation}.json"
    linked = tmp_path / "extra-link.json"
    junction_target = tmp_path / "junction-target"

    def mutate(path: Path, _baseline: dict[str, object]) -> None:
        if mutation == "missing":
            path.replace(retained)
        elif mutation == "delete-pending":
            path.unlink()
        elif mutation == "directory":
            path.replace(retained)
            path.mkdir()
        elif mutation == "junction":
            path.replace(retained)
            junction_target.mkdir()
            created = subprocess.run(
                [
                    "cmd.exe",
                    "/d",
                    "/c",
                    "mklink",
                    "/J",
                    str(path),
                    str(junction_target),
                ],
                capture_output=True,
                check=False,
                text=True,
                timeout=5,
            )
            assert created.returncode == 0
        elif mutation == "hardlink":
            os.link(path, linked)
        elif mutation == "timestamp":
            os.utime(path, ns=(1_700_000_000_000_000_000,) * 2)
        elif mutation == "attributes":
            os.chmod(path, 0o444)
        elif mutation == "replacement-size":
            path.replace(retained)
            path.write_bytes(b"FOREIGN")
        elif mutation == "replacement-hash":
            raw = bytearray(
                retained.read_bytes() if retained.exists() else path.read_bytes()
            )
            path.replace(retained)
            raw[-2] ^= 1
            path.write_bytes(raw)
        else:  # pragma: no cover - frozen parametrization
            raise AssertionError("unknown mutation")

    if mutation in {"timestamp", "attributes", "delete-pending"}:
        with pytest.raises((publisher.PublicationError, PermissionError)):
            publisher.publish_failure_receipt(
                output,
                "PermissionError",
                after_move=mutate,
            )
    else:
        with pytest.raises(publisher.PublicationError):
            publisher.publish_failure_receipt(
                output,
                "PermissionError",
                after_move=mutate,
            )

    if mutation in {
        "missing",
        "directory",
        "junction",
        "replacement-size",
        "replacement-hash",
    }:
        assert retained.read_bytes() == _canonical_failure()
    if mutation == "directory":
        assert output.is_dir()
    if mutation == "junction":
        assert output.is_dir() and output.is_symlink() is False
    if mutation == "hardlink":
        assert output.exists() and linked.exists()
    if mutation == "delete-pending":
        if output.exists():
            assert output.read_bytes() == _canonical_failure()


def test_failure_receipt_publisher_rejects_target_open_failure_without_deleting(
    tmp_path: Path,
) -> None:
    publisher = _load_publisher()
    output = tmp_path / "safe-failure.json"

    def fail_open(_api: object, _path: Path) -> int:
        raise publisher.PublicationError("open_failed")

    with pytest.raises(publisher.PublicationError, match="open_failed"):
        publisher.publish_failure_receipt(
            output,
            "PermissionError",
            open_target=fail_open,
        )

    assert output.read_bytes() == _canonical_failure()


@pytest.mark.parametrize("replacement", ("same-content", "different-content"))
def test_failure_receipt_publisher_rejects_post_target_open_path_replacement(
    tmp_path: Path,
    replacement: str,
) -> None:
    publisher = _load_publisher()
    output = tmp_path / "safe-failure.json"
    retained = tmp_path / "held-original.json"
    mutation_happened = False

    def permissive_open_then_replace(api: object, path: Path) -> int:
        nonlocal mutation_happened
        handle = publisher._open(
            api,
            path,
            access=publisher._GENERIC_READ,
            share=(
                publisher._FILE_SHARE_READ
                | publisher._FILE_SHARE_WRITE
                | publisher._FILE_SHARE_DELETE
            ),
            disposition=publisher._OPEN_EXISTING,
            flags=publisher._FILE_FLAG_OPEN_REPARSE_POINT,
        )
        path.replace(retained)
        path.write_bytes(
            _canonical_failure() if replacement == "same-content" else b"FOREIGN"
        )
        mutation_happened = True
        return handle

    with pytest.raises(publisher.PublicationError, match="target_identity_changed"):
        publisher.publish_failure_receipt(
            output,
            "PermissionError",
            open_target=permissive_open_then_replace,
        )

    assert mutation_happened
    assert retained.read_bytes() == _canonical_failure()
    assert output.stat().st_ino != retained.stat().st_ino
    if replacement == "same-content":
        assert output.read_bytes() == _canonical_failure()
    else:
        assert output.read_bytes() == b"FOREIGN"


@pytest.mark.parametrize("phase", ("target", "final"))
@pytest.mark.parametrize("mutation", ("rename", "delete"))
def test_failure_receipt_publisher_held_path_handles_block_late_namespace_mutation(
    tmp_path: Path,
    phase: str,
    mutation: str,
) -> None:
    publisher = _load_publisher()
    output = tmp_path / "safe-failure.json"
    retained = tmp_path / "held-original.json"
    attempted = False
    blocked = False
    mutation_happened = False

    def mutate(path: Path, _snapshot: dict[str, object]) -> None:
        nonlocal attempted, blocked, mutation_happened
        attempted = True
        try:
            if mutation == "rename":
                path.replace(retained)
            else:
                path.unlink()
            mutation_happened = True
        except OSError as error:
            assert getattr(error, "winerror", None) in {5, 32}
            blocked = True

    kwargs = {
        "after_target_bound" if phase == "target" else "after_final_bound": mutate
    }
    receipt = publisher.publish_failure_receipt(
        output,
        "PermissionError",
        **kwargs,
    )

    assert attempted and blocked and not mutation_happened
    assert not retained.exists()
    assert output.read_bytes() == _canonical_failure()
    assert output.stat().st_ino == receipt["file_index"]


def test_failure_receipt_publisher_detects_hardlink_growth_after_target_open(
    tmp_path: Path,
) -> None:
    publisher = _load_publisher()
    output = tmp_path / "safe-failure.json"
    linked = tmp_path / "late-hardlink.json"
    mutation_happened = False

    def add_hardlink(path: Path, _snapshot: dict[str, object]) -> None:
        nonlocal mutation_happened
        os.link(path, linked)
        mutation_happened = True

    with pytest.raises(publisher.PublicationError, match="identity_invalid"):
        publisher.publish_failure_receipt(
            output,
            "PermissionError",
            after_target_bound=add_hardlink,
        )

    assert mutation_happened
    assert output.exists() and linked.exists()
    assert output.stat().st_ino == linked.stat().st_ino


def test_failure_receipt_publisher_allows_concurrent_read_during_final_lock(
    tmp_path: Path,
) -> None:
    publisher = _load_publisher()
    output = tmp_path / "safe-failure.json"
    observed: list[bytes] = []

    def read_current(path: Path, _snapshot: dict[str, object]) -> None:
        observed.append(path.read_bytes())

    receipt = publisher.publish_failure_receipt(
        output,
        "PermissionError",
        after_final_bound=read_current,
    )

    assert observed == [_canonical_failure()]
    assert output.stat().st_ino == receipt["file_index"]


def test_failure_receipt_publisher_blocks_or_rejects_parent_retarget_after_target_open(
    tmp_path: Path,
) -> None:
    publisher = _load_publisher()
    publication_root = tmp_path / "publication-root"
    publication_root.mkdir()
    retained_root = tmp_path / "bound-publication-root"
    output = publication_root / "safe-failure.json"
    attempted = False
    blocked = False
    mutation_happened = False

    def retarget_parent(path: Path, _snapshot: dict[str, object]) -> None:
        nonlocal attempted, blocked, mutation_happened
        attempted = True
        try:
            path.parent.replace(retained_root)
            created = subprocess.run(
                [
                    "cmd.exe",
                    "/d",
                    "/c",
                    "mklink",
                    "/J",
                    str(path.parent),
                    str(retained_root),
                ],
                capture_output=True,
                check=False,
                text=True,
                timeout=5,
            )
            assert created.returncode == 0
            mutation_happened = True
        except OSError as error:
            assert getattr(error, "winerror", None) in {5, 32}
            blocked = True

    outcome = "pass"
    receipt: dict[str, object] | None = None
    try:
        receipt = publisher.publish_failure_receipt(
            output,
            "PermissionError",
            after_target_bound=retarget_parent,
        )
    except publisher.PublicationError as error:
        outcome = str(error)

    assert attempted
    if mutation_happened:
        assert outcome in {"parent_identity_invalid", "parent_identity_changed"}
        assert receipt is None
        assert (retained_root / output.name).read_bytes() == _canonical_failure()
    else:
        assert blocked and outcome == "pass"
        assert receipt is not None
        assert publication_root.is_dir() and not retained_root.exists()
        assert output.stat().st_ino == receipt["file_index"]


def test_failure_receipt_publisher_blocks_delete_access_opened_before_target_lock(
    tmp_path: Path,
) -> None:
    publisher = _load_publisher()
    output = tmp_path / "safe-failure.json"
    delete_handle: int | None = None
    handle_opened = False

    def hold_delete_access(path: Path, _snapshot: dict[str, object]) -> None:
        nonlocal delete_handle, handle_opened
        api = publisher._WindowsFunctions()
        delete_handle = publisher._open(
            api,
            path,
            access=0x00010000,
            share=(
                publisher._FILE_SHARE_READ
                | publisher._FILE_SHARE_WRITE
                | publisher._FILE_SHARE_DELETE
            ),
            disposition=publisher._OPEN_EXISTING,
            flags=publisher._FILE_FLAG_OPEN_REPARSE_POINT,
        )
        handle_opened = True

    try:
        with pytest.raises(publisher.PublicationError, match="open_failed"):
            publisher.publish_failure_receipt(
                output,
                "PermissionError",
                after_move=hold_delete_access,
            )
    finally:
        if delete_handle is not None:
            publisher._WindowsFunctions().close_handle(delete_handle)

    assert handle_opened
    assert output.read_bytes() == _canonical_failure()


def test_failure_receipt_publisher_blocks_or_rejects_parent_canonical_path_retarget(
    tmp_path: Path,
) -> None:
    publisher = _load_publisher()
    publication_root = tmp_path / "publication-root"
    publication_root.mkdir()
    retained_root = tmp_path / "bound-publication-root"
    output = publication_root / "safe-failure.json"

    def retarget_parent(path: Path, _baseline: dict[str, object]) -> None:
        path.parent.replace(retained_root)
        created = subprocess.run(
            [
                "cmd.exe",
                "/d",
                "/c",
                "mklink",
                "/J",
                str(path.parent),
                str(retained_root),
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
        assert created.returncode == 0

    with pytest.raises((publisher.PublicationError, PermissionError)) as caught:
        publisher.publish_failure_receipt(
            output,
            "PermissionError",
            after_move=retarget_parent,
        )

    if type(caught.value) is publisher.PublicationError:  # noqa: E721
        assert str(caught.value) in {
            "parent_identity_invalid",
            "parent_identity_changed",
        }
        assert output.read_bytes() == _canonical_failure()
        assert (retained_root / output.name).read_bytes() == _canonical_failure()
    else:
        assert type(caught.value) is PermissionError  # noqa: E721
        assert output.read_bytes() == _canonical_failure()
        assert publication_root.is_dir() and not retained_root.exists()


def test_failure_receipt_publisher_never_overwrites_preexisting_leaf(
    tmp_path: Path,
) -> None:
    publisher = _load_publisher()
    output = tmp_path / "safe-failure.json"
    output.write_bytes(b"FOREIGN-SENTINEL")

    with pytest.raises(publisher.PublicationError, match="output_invalid"):
        publisher.publish_failure_receipt(output, "PermissionError")

    assert output.read_bytes() == b"FOREIGN-SENTINEL"


def _failure_receipt_function() -> str:
    source = BUILD.read_text(encoding="utf-8-sig")
    start = source.index("function Save-ReleaseAvailabilityFailureReceipt")
    end = source.index("function Invoke-ReleaseAvailabilityGate", start)
    return source[start:end]


def _invoke_failure_receipt(
    tmp_path: Path,
    *,
    gate_output: str,
    gate_exit: str = "1",
    preexisting: bytes | None = None,
    publisher_output: str | None = None,
    publisher_exit: int = 0,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    output = tmp_path / "safe-failure.json"
    if preexisting is not None:
        output.write_bytes(preexisting)
    publisher = PUBLISHER
    if publisher_output is not None:
        publisher = tmp_path / "fake-publisher.py"
        publisher.write_text(
            "import sys\n"
            + f"sys.stdout.write({publisher_output!r})\n"
            + f"raise SystemExit({publisher_exit})\n",
            encoding="utf-8",
        )
    script = tmp_path / "invoke.ps1"
    publisher_sha = hashlib.sha256(publisher.read_bytes()).hexdigest()
    script.write_text(
        _failure_receipt_function()
        + "\n[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)\n"
        + "$ErrorActionPreference='Stop'\n"
        + f"$BuildPython={_ps_quote(PYTHON)}\n"
        + f"$ReleaseAvailabilityFailureReceiptPublisherPath={_ps_quote(publisher)}\n"
        + "$TrustedFileHashCommand={ param([string]$LiteralPath,[string]$Algorithm) "
        + "if($Algorithm -cne 'SHA256'){throw 'algorithm'}; "
        + "$s=[IO.File]::OpenRead($LiteralPath); $h=[Security.Cryptography.SHA256]::Create(); "
        + "try{$d=[BitConverter]::ToString($h.ComputeHash($s)).Replace('-','')}"
        + "finally{$h.Dispose();$s.Dispose()}; [pscustomobject]@{Hash=$d} }\n"
        + "$ReleaseAvailabilityFailureReceiptPublisherSHA256="
        + f"{_ps_quote(publisher_sha)}\n"
        + "try {\n"
        + "  $r=Save-ReleaseAvailabilityFailureReceipt "
        + f"-GateExit ({gate_exit}) -GateOutput {_ps_quote(gate_output)} "
        + f"-Label 'Stage' -OutputPath {_ps_quote(output)}\n"
        + "  Write-Output ('STATUS=pass;TYPE=' + $r.ErrorType)\n"
        + "  exit 0\n"
        + "} catch {\n"
        + "  Write-Output ('STATUS=fail;TYPE=' + $_.Exception.Message)\n"
        + "  exit 1\n"
        + "}\n",
        encoding="utf-8-sig",
    )
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        capture_output=True,
        check=False,
        encoding="utf-8",
        text=True,
        timeout=15,
    )
    return completed, output


def test_builder_atomically_saves_exact_safe_checker_failure_receipt(
    tmp_path: Path,
) -> None:
    canonical = (
        '{"schema":"bidking_release_availability_receipt_v1",'
        '"status":"fail","error":"PermissionError"}\n'
    )

    completed, output = _invoke_failure_receipt(tmp_path, gate_output=canonical)

    assert completed.returncode == 0
    assert completed.stdout.strip() == "STATUS=pass;TYPE=PermissionError"
    assert completed.stderr == ""
    assert output.read_bytes() == canonical.encode("ascii")
    assert list(tmp_path.glob("safe-failure.json.tmp-*")) == []


@pytest.mark.parametrize(
    ("gate_output", "gate_exit"),
    (
        ("", "1"),
        ("not-json\n", "1"),
        (
            '{"schema":"bidking_release_availability_receipt_v1",'
            '"status":"fail","error":"PermissionError","extra":true}\n',
            "1",
        ),
        (
            '{"schema":"bidking_release_availability_receipt_v1",'
            '"status":"fail","error":true}\n',
            "1",
        ),
        (
            'FORGED\n{"schema":"bidking_release_availability_receipt_v1",'
            '"status":"fail","error":"PermissionError"}\n',
            "1",
        ),
        (
            '{"schema":"bidking_release_availability_receipt_v1",'
            '"status":"fail","error":"ForgedError"}\n',
            "1",
        ),
        (
            '{"schema":"bidking_release_availability_receipt_v1",'
            '"status":"fail","error":"PermissionError"}\n',
            "$true",
        ),
    ),
    ids=(
        "missing",
        "bad",
        "extra",
        "bool",
        "forged-stdout",
        "unknown-type",
        "bool-exit",
    ),
)
def test_builder_failure_receipt_rejects_noncanonical_or_untrusted_output(
    tmp_path: Path,
    gate_output: str,
    gate_exit: str,
) -> None:
    completed, output = _invoke_failure_receipt(
        tmp_path,
        gate_output=gate_output,
        gate_exit=gate_exit,
    )

    assert completed.returncode == 1
    assert completed.stdout.startswith("STATUS=fail;TYPE=release availability checker")
    assert completed.stderr == ""
    assert not output.exists()
    assert list(tmp_path.glob("safe-failure.json.tmp-*")) == []


def test_builder_failure_receipt_never_overwrites_preexisting_leaf(
    tmp_path: Path,
) -> None:
    canonical = (
        '{"schema":"bidking_release_availability_receipt_v1",'
        '"status":"fail","error":"AssertionError"}\n'
    )
    completed, output = _invoke_failure_receipt(
        tmp_path,
        gate_output=canonical,
        preexisting=b"FOREIGN-SENTINEL",
    )

    assert completed.returncode == 1
    assert output.read_bytes() == b"FOREIGN-SENTINEL"
    assert list(tmp_path.glob("safe-failure.json.tmp-*")) == []


@pytest.mark.parametrize(
    ("publisher_output", "publisher_exit"),
    (
        ("", 0),
        ("FORGED\n{}\n", 0),
        (
            '{"schema":"bidking_release_availability_failure_publication_v1",'
            '"status":"published","bytes":true}\n',
            0,
        ),
        (
            '{"schema":"bidking_release_availability_failure_publication_v1",'
            '"status":"published","extra":1}\n',
            0,
        ),
        ('{"schema":"unknown","status":"published"}\n', 0),
        (
            '{"schema":"bidking_release_availability_failure_publication_v1",'
            '"status":"publication_failed"}\n',
            1,
        ),
    ),
    ids=("missing", "forged-prefix", "bool", "extra", "unknown", "nonzero"),
)
def test_builder_rejects_untrusted_publisher_output_without_sidecar(
    tmp_path: Path,
    publisher_output: str,
    publisher_exit: int,
) -> None:
    completed, output = _invoke_failure_receipt(
        tmp_path,
        gate_output=_canonical_failure().decode("ascii"),
        publisher_output=publisher_output,
        publisher_exit=publisher_exit,
    )

    assert completed.returncode == 1
    assert completed.stdout.startswith("STATUS=fail;TYPE=release availability checker")
    assert completed.stderr == ""
    assert not output.exists()


def test_builder_success_path_remains_after_nonzero_only_receipt_branch() -> None:
    source = BUILD.read_text(encoding="utf-8-sig")
    failure_writer = _failure_receipt_function()
    gate_start = source.index("function Invoke-ReleaseAvailabilityGate")
    gate_end = source.index("$ReleaseAvailabilityStageReceipt", gate_start)
    gate = source[gate_start:gate_end]

    failure = gate.index("if ($gateExit -ne 0)")
    success_parse = gate.index("$gateReceipt = $gateOutput | ConvertFrom-Json")
    assert failure < success_parse
    assert "Save-ReleaseAvailabilityFailureReceipt" in gate[failure:success_parse]
    assert "Save-ReleaseAvailabilityFailureReceipt" not in gate[success_parse:]
    assert '$gateReceipt.status -cne "pass"' in gate[success_parse:]
    assert "$ReleaseAvailabilityFailureReceiptPublisherPath" in failure_writer
    assert "& $BuildPython -I -B" in failure_writer
    assert "[System.IO.File]::Move" not in failure_writer
