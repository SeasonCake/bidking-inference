from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from examples.evidence_lifecycle import Snapshot, infer_snapshot  # noqa: E402


class EvidenceLifecycleTest(unittest.TestCase):
    def test_local_examples_win_over_an_unrelated_installed_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            shadow = Path(directory)
            package = shadow / "examples"
            package.mkdir()
            (package / "__init__.py").write_text(
                "raise RuntimeError('unrelated examples package was imported')\n",
                encoding="utf-8",
            )
            script = (
                "import sys; sys.path[:0] = " + repr([str(ROOT), str(shadow)])
                + "; import examples.evidence_lifecycle as target; print(target.__file__)"
            )
            completed = subprocess.run(
                [sys.executable, "-c", script], cwd=ROOT, capture_output=True,
                text=True, timeout=10,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(Path(completed.stdout.strip()).resolve(),
                             (ROOT / "examples" / "evidence_lifecycle.py").resolve())

    def infer(self, complete: bool, seen: int | None) -> dict[str, object]:
        return infer_snapshot(Snapshot("run-a", "revision-b", complete, seen),
                              current_session="run-a", current_revision="revision-b")

    def test_complete_count_constrains_the_first_result(self) -> None:
        self.assertEqual(self.infer(True, 2)["posterior"], {"observed": 1.0})

    def test_partial_count_is_a_lower_bound_not_an_exact_count(self) -> None:
        posterior = self.infer(False, 2)["posterior"]
        self.assertEqual(set(posterior), {"observed", "more"})
        self.assertAlmostEqual(posterior["observed"], 2 / 3)
        self.assertAlmostEqual(posterior["more"], 1 / 3)

    def test_missing_is_not_zero_and_zero_is_a_valid_lower_bound(self) -> None:
        expected = {"few": 0.25, "observed": 0.5, "more": 0.25}
        self.assertEqual(self.infer(False, None)["posterior"], expected)
        self.assertEqual(self.infer(False, 0)["posterior"], expected)
        with self.assertRaises(ValueError):
            self.infer(True, 0)  # Complete zero contradicts every candidate in this toy pool.

    def test_revision_change_replaces_the_ready_view(self) -> None:
        snapshot = Snapshot("run-a", "revision-b", True, 2)
        view = infer_snapshot(snapshot, current_session="run-a", current_revision="revision-b")
        self.assertEqual(view["status"], "ready")
        view = infer_snapshot(snapshot, current_session="run-a", current_revision="revision-c")
        self.assertEqual(view, {"status": "withheld", "reason": "revision_mismatch", "posterior": {}})

    def test_unknown_revision_and_wrong_session_are_not_promoted(self) -> None:
        for snapshot, current_revision, reason in (
            (Snapshot("run-a", None, True, 2), "revision-b", "revision_unknown"),
            (Snapshot("run-a", "revision-b", True, 2), None, "revision_unknown"),
            (Snapshot("run-old", "revision-b", True, 2), "revision-b", "session_mismatch"),
        ):
            with self.subTest(reason=reason):
                result = infer_snapshot(snapshot, current_session="run-a", current_revision=current_revision)
                self.assertEqual(result["reason"], reason)
                self.assertEqual(result["posterior"], {})

    def test_invalid_shape_is_rejected(self) -> None:
        for count in (True, -1, 7, 2.0):
            with self.subTest(count=count), self.assertRaises(ValueError):
                Snapshot("run-a", "revision-b", False, count)
        with self.assertRaises(TypeError):
            Snapshot("run-a", "revision-b", "false", 2)
        with self.assertRaises(ValueError):
            Snapshot("run-a", "revision-b", True, None)
        with self.assertRaises(ValueError):
            Snapshot("", "revision-b", False, 2)

    def test_a_new_matching_snapshot_can_resume(self) -> None:
        result = infer_snapshot(Snapshot("run-b", "revision-c", True, 3),
                                current_session="run-b", current_revision="revision-c")
        self.assertEqual(result["posterior"], {"more": 1.0})

    def test_example_is_deterministic_and_explicitly_synthetic(self) -> None:
        command = [sys.executable, str(ROOT / "examples" / "evidence_lifecycle.py")]
        first = subprocess.run(command, check=True, capture_output=True, timeout=10)
        second = subprocess.run(command, check=True, capture_output=True, timeout=10)
        self.assertEqual(first.stdout, second.stdout)
        result = json.loads(first.stdout)
        self.assertTrue(result["synthetic"])
        self.assertEqual(result["cases"]["stale_revision"]["posterior"], {})


if __name__ == "__main__":
    unittest.main()
