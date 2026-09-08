from __future__ import annotations

from dataclasses import asdict, replace
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from examples.failure_workflows import (  # noqa: E402
    RefreshGate,
    SaveOutcome,
    SnapshotWriteError,
    save_in_stages,
    write_json_snapshot,
)


class SnapshotTests(unittest.TestCase):
    def test_success_is_utf8_json_and_leaves_no_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "state.json"
            target.write_text("old", encoding="utf-8")
            value = {"title": "合成文档 🌱", "ready": True}
            write_json_snapshot(target, value)
            self.assertEqual(json.loads(target.read_bytes()), value)
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_encoding_failure_never_touches_target_or_sibling(self) -> None:
        cyclic: list[object] = []
        cyclic.append(cyclic)
        for value in ({"bad": object()}, {"bad": float("nan")}, cyclic, "\ud800"):
            with (
                self.subTest(value_type=type(value).__name__),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                target, sibling = root / "state.json", root / "keep.txt"
                target.write_bytes(b"old")
                sibling.write_bytes(b"keep")
                replacement = mock.Mock()
                with self.assertRaises((TypeError, ValueError, UnicodeError)):
                    write_json_snapshot(target, value, replace=replacement)
                replacement.assert_not_called()
                self.assertEqual(target.read_bytes(), b"old")
                self.assertEqual(sibling.read_bytes(), b"keep")
                self.assertEqual(
                    {p.name for p in root.iterdir()}, {"state.json", "keep.txt"}
                )

    def test_replace_failure_preserves_old_files_and_cleans_only_own_temp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, sibling = root / "state.json", root / "keep.tmp"
            target.write_bytes(b"old")
            sibling.write_bytes(b"keep")
            error = PermissionError("synthetic lock")
            with self.assertRaises(SnapshotWriteError) as caught:
                write_json_snapshot(
                    target, {"revision": 2}, replace=mock.Mock(side_effect=error)
                )
            self.assertIs(caught.exception.operation_error, error)
            self.assertIsNone(caught.exception.cleanup_error)
            self.assertEqual(target.read_bytes(), b"old")
            self.assertEqual(sibling.read_bytes(), b"keep")
            self.assertEqual(
                {p.name for p in root.iterdir()}, {"state.json", "keep.tmp"}
            )

    def test_cleanup_failure_does_not_hide_the_original_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "state.json"
            target.write_bytes(b"old")
            first, second = OSError("replace failed"), PermissionError("cleanup failed")
            with mock.patch.object(Path, "unlink", side_effect=second):
                with self.assertRaises(SnapshotWriteError) as caught:
                    write_json_snapshot(
                        target, {}, replace=mock.Mock(side_effect=first)
                    )
            self.assertIs(caught.exception.operation_error, first)
            self.assertIs(caught.exception.cleanup_error, second)
            self.assertEqual(target.read_bytes(), b"old")
            self.assertEqual(len(list(Path(directory).glob(".state.json.*.tmp"))), 1)

    def test_old_target_invariant_catches_a_deliberately_broken_writer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "state.json"
            target.write_bytes(b"old")
            # Positive control: direct truncation is the failure class, not shipped behavior.
            target.write_bytes(b"")
            with self.assertRaises(AssertionError):
                self.assertEqual(target.read_bytes(), b"old")


class RefreshTests(unittest.TestCase):
    def ticket(self, gate: RefreshGate, revision: str = "a", stale: bool = False):
        ticket = gate.request(revision, stale=stale)
        self.assertIsNotNone(ticket)
        return ticket

    def test_pending_request_suppresses_repeated_ticks(self) -> None:
        gate = RefreshGate()
        self.ticket(gate)
        self.assertTrue(all(gate.request("a", stale=False) is None for _ in range(10)))
        self.assertEqual(gate.submitted, 1)

    def test_fresh_to_stale_refreshes_once_after_accepted_completion(self) -> None:
        gate = RefreshGate()
        self.assertTrue(gate.accept(self.ticket(gate)))
        self.assertTrue(gate.accept(self.ticket(gate, stale=True)))
        self.assertTrue(all(gate.request("a", stale=True) is None for _ in range(10)))
        self.assertEqual(gate.submitted, 2)

    def test_failure_allows_a_new_request_without_marking_success(self) -> None:
        gate = RefreshGate()
        first = self.ticket(gate)
        self.assertTrue(gate.fail(first))
        second = self.ticket(gate)
        self.assertFalse(gate.accept(first))
        self.assertTrue(gate.accept(second))
        self.assertEqual(gate.submitted, 2)

    def test_a_b_a_sequence_rejects_both_old_completions(self) -> None:
        gate = RefreshGate()
        first, middle, current = (
            self.ticket(gate, "a"),
            self.ticket(gate, "b"),
            self.ticket(gate, "a"),
        )
        self.assertFalse(gate.accept(first))
        self.assertFalse(gate.accept(middle))
        self.assertTrue(gate.accept(current))

    def test_old_failure_cannot_clear_a_new_pending_request(self) -> None:
        gate = RefreshGate()
        old = self.ticket(gate, "a")
        new = self.ticket(gate, "b")
        self.assertFalse(gate.fail(old))
        self.assertIsNone(gate.request("b", stale=False))
        self.assertTrue(gate.accept(new))

    def test_foreign_or_copied_tickets_are_not_current_handles(self) -> None:
        gate, other = RefreshGate(), RefreshGate()
        mine, foreign = self.ticket(gate), self.ticket(other)
        self.assertFalse(gate.accept(foreign))
        self.assertFalse(gate.accept(replace(mine)))
        self.assertFalse(gate.accept(None))
        self.assertTrue(gate.accept(mine))

    def test_invalid_revision_or_stale_flag_does_not_advance_state(self) -> None:
        gate = RefreshGate()
        for revision in ("", None, 1, True):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                gate.request(revision, stale=False)
        for stale in ("true", 1, None):
            with self.subTest(stale=stale), self.assertRaises(TypeError):
                gate.request("a", stale=stale)
        self.assertEqual(gate.submitted, 0)

    def test_tick_count_invariant_detects_an_age_only_broken_policy(self) -> None:
        submitted = sum(age >= 5 for age in range(5, 15))
        with self.assertRaises(AssertionError):
            self.assertEqual(submitted, 1)


class StagedSaveTests(unittest.TestCase):
    def test_normal_save_does_not_attempt_recovery(self) -> None:
        remote, local, restore = (
            mock.Mock(return_value=True),
            mock.Mock(return_value=None),
            mock.Mock(),
        )
        self.assertEqual(
            save_in_stages(remote, local, restore),
            SaveOutcome("accepted", "committed", "not_needed"),
        )
        remote.assert_called_once_with()
        local.assert_called_once_with()
        restore.assert_not_called()

    def test_remote_rejection_does_not_write_or_restore_local_state(self) -> None:
        local, restore = mock.Mock(), mock.Mock()
        self.assertEqual(
            save_in_stages(lambda: False, local, restore),
            SaveOutcome("rejected", "not_attempted", "not_needed"),
        )
        local.assert_not_called()
        restore.assert_not_called()

    def test_uncertain_remote_result_is_not_automatically_retried(self) -> None:
        remote = mock.Mock(
            side_effect=ConnectionError("synthetic private-message-canary")
        )
        local, restore = mock.Mock(), mock.Mock()
        result = save_in_stages(remote, local, restore)
        self.assertEqual(result.remote, "unknown")
        self.assertNotIn("private-message-canary", json.dumps(asdict(result)))
        remote.assert_called_once_with()
        local.assert_not_called()
        restore.assert_not_called()

    def test_remote_acknowledgement_must_be_a_real_boolean(self) -> None:
        for value in (1, 0, "true", {}, None):
            with self.subTest(value=value):
                local = mock.Mock()
                result = save_in_stages(lambda: value, local, mock.Mock())
                self.assertEqual(
                    (result.remote, result.error_type), ("unknown", "TypeError")
                )
                local.assert_not_called()

    def test_local_failure_keeps_remote_acceptance_and_reports_restoration(
        self,
    ) -> None:
        remote, restore = mock.Mock(return_value=True), mock.Mock(return_value=None)
        result = save_in_stages(
            remote, mock.Mock(side_effect=OSError("save failed")), restore
        )
        self.assertEqual(
            result, SaveOutcome("accepted", "failed", "restored", "OSError")
        )
        remote.assert_called_once_with()
        restore.assert_called_once_with()

    def test_failed_recovery_is_not_reported_as_restored(self) -> None:
        result = save_in_stages(
            lambda: True,
            mock.Mock(side_effect=OSError()),
            mock.Mock(side_effect=PermissionError()),
        )
        self.assertEqual(
            result,
            SaveOutcome("accepted", "failed", "failed", "OSError", "PermissionError"),
        )

    def test_false_local_return_is_not_a_commit_acknowledgement(self) -> None:
        result = save_in_stages(lambda: True, lambda: False, lambda: None)
        self.assertEqual(
            result, SaveOutcome("accepted", "failed", "restored", "TypeError")
        )

    def test_false_recovery_return_is_not_success(self) -> None:
        result = save_in_stages(
            lambda: True, mock.Mock(side_effect=OSError()), lambda: False
        )
        self.assertEqual(result.recovery, "failed")
        self.assertEqual(result.recovery_error_type, "TypeError")

    def test_independent_axes_catch_a_deliberately_flattened_success(self) -> None:
        expected = SaveOutcome("accepted", "failed", "restored", "OSError")
        broken = SaveOutcome("accepted", "committed", "not_needed")
        with self.assertRaises(AssertionError):
            self.assertEqual(broken, expected)


class WorkflowCommandTests(unittest.TestCase):
    def test_all_examples_are_deterministic_and_explicitly_synthetic(self) -> None:
        command = [sys.executable, str(ROOT / "examples/failure_workflows.py")]
        first = subprocess.run(command, check=True, capture_output=True, timeout=10)
        second = subprocess.run(command, check=True, capture_output=True, timeout=10)
        self.assertEqual(first.stdout, second.stdout)
        result = json.loads(first.stdout)
        self.assertTrue(result["synthetic"])
        self.assertEqual(len(result["cases"]), 9)
        self.assertEqual(
            result["cases"]["refresh-once"]["extra_requests_after_stale_success"], 0
        )
        self.assertEqual(
            result["cases"]["staged-local-error"]["outcome"]["local"], "failed"
        )

    def test_unknown_scenario_is_an_input_error(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "examples/failure_workflows.py"),
                "--scenario",
                "not-a-case",
            ],
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
