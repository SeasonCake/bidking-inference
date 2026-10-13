"""Minimal public-package ABI for the native lifecycle helper."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


_LIFECYCLE_ABI = 2
_LIFECYCLE_GENERATION = "__BIDKING_NATIVE_LIFECYCLE_GENERATION__"
_LIFECYCLE_CALLER = "_client_launcher"
_SAFE_FAILURE = "客户端启动保护组件不可用。请重新完整解压当前版本后重试。"
_SAFE_REASON_ALLOWLIST = frozenset(
    {
        "ok",
        "unknown",
        "native_module_missing",
        "native_module_load_failed",
        "native_symbol_missing",
        "native_access_denied",
        "native_exception",
        "native_status_invalid",
        "native_status_nonzero",
        "client_lifecycle_unavailable",
        "client_lifecycle_request_rejected",
        "client_lifecycle_busy",
        "client_lifecycle_payload_invalid",
        "client_lifecycle_start_failed",
    }
)
_NATIVE_RUNTIME_REASONS = frozenset(
    reason for reason in _SAFE_REASON_ALLOWLIST if reason.startswith("client_lifecycle_")
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--python-path", default=sys.executable)
    parser.add_argument("--diagnostic-profile", default="portable")
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--no-taskbar", action="store_true")
    return parser


def _repair_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _fixed_reason(value: object) -> str:
    if type(value) is str and len(value) <= 64 and value in _SAFE_REASON_ALLOWLIST:
        return value
    return "unknown"


def _exception_reason(error: BaseException) -> str:
    try:
        if type(error) is PermissionError:
            return "native_access_denied"
        if type(error) is not RuntimeError:
            return "native_exception"
        arguments = error.args
        if type(arguments) is not tuple or len(arguments) != 1:
            return "native_exception"
        value = arguments[0]
        if (
            type(value) is str
            and len(value) <= 64
            and value in _NATIVE_RUNTIME_REASONS
        ):
            return value
    except BaseException:
        return "native_exception"
    return "native_exception"


def _native_helper_receipt() -> tuple[object | None, str]:
    try:
        import bidking_kernel  # type: ignore
    except ModuleNotFoundError as error:
        reason = (
            "native_module_missing"
            if type(error) is ModuleNotFoundError and error.name == "bidking_kernel"
            else "native_module_load_failed"
        )
        return None, reason
    except PermissionError:
        return None, "native_access_denied"
    except (ImportError, OSError):
        return None, "native_module_load_failed"
    except Exception:
        return None, "native_exception"

    contract_function = getattr(bidking_kernel, "client_lifecycle_contract", None)
    runner = getattr(bidking_kernel, "run_client_lifecycle", None)
    if not callable(contract_function) or not callable(runner):
        return None, "native_symbol_missing"
    try:
        contract = contract_function()
    except Exception as error:
        return None, _exception_reason(error)

    expected = (_LIFECYCLE_ABI, _LIFECYCLE_GENERATION, True)
    try:
        contract_matches = tuple(contract) == expected
    except Exception:
        contract_matches = False
    if not contract_matches:
        return None, "client_lifecycle_request_rejected"
    return bidking_kernel, "ok"


def _run_native_receipt(mode: str, **kwargs: object) -> tuple[int, str]:
    helper, reason = _native_helper_receipt()
    if helper is None:
        return 1, _fixed_reason(reason)
    try:
        status = helper.run_client_lifecycle(
            abi=_LIFECYCLE_ABI,
            generation=_LIFECYCLE_GENERATION,
            caller=_LIFECYCLE_CALLER,
            mode=mode,
            **kwargs,
        )
    except Exception as error:
        return 1, _fixed_reason(_exception_reason(error))
    if type(status) is not int:
        return 1, "native_status_invalid"
    if status == 0:
        return 0, "ok"
    return status, "native_status_nonzero"


def _run_native(mode: str, **kwargs: object) -> int:
    status, reason = _run_native_receipt(mode, **kwargs)
    if mode == "launch" and reason == "client_lifecycle_busy":
        return 0
    if status != 0 and reason != "native_status_nonzero":
        print(_SAFE_FAILURE, file=sys.stderr)
    return status


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    package_root = Path(__file__).resolve().parent.parent
    return _run_native(
        "launch",
        package_root=str(package_root),
        python_path=str(Path(args.python_path).resolve()),
        diagnostic_profile=str(args.diagnostic_profile),
        restart=bool(args.restart),
        no_taskbar=bool(args.no_taskbar),
        repair_dry_run=False,
    )


def diagnostics_main() -> int:
    package_root = Path(__file__).resolve().parent.parent
    return _run_native(
        "diagnostics",
        package_root=str(package_root),
        python_path="",
        diagnostic_profile="portable",
        restart=False,
        no_taskbar=False,
        repair_dry_run=False,
    )


def diagnostics_receipt_main() -> tuple[int, str]:
    """Return only an exact status and fixed safe reason for local build diagnostics."""
    package_root = Path(__file__).resolve().parent.parent
    status, reason = _run_native_receipt(
        "diagnostics",
        package_root=str(package_root),
        python_path="",
        diagnostic_profile="portable",
        restart=False,
        no_taskbar=False,
        repair_dry_run=False,
    )
    return status, _fixed_reason(reason)


def repair_main(argv: list[str] | None = None) -> int:
    args = _repair_parser().parse_args(argv)
    package_root = Path(__file__).resolve().parent.parent
    return _run_native(
        "repair",
        package_root=str(package_root),
        python_path=str(Path(sys.executable).resolve()),
        diagnostic_profile="portable",
        restart=False,
        no_taskbar=False,
        repair_dry_run=bool(args.dry_run),
    )


if __name__ == "__main__":
    raise SystemExit(main())
