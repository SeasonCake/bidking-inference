from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bounded_readonly as tool


class BoundedReadonlyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "subject"
        self.root.mkdir()
        (self.root / "one.txt").write_text("hello\n第五轮 1003035\nhello", encoding="utf-8")

    def search(self, files=None, needle="1003035", **kw):
        return tool.search(self.root, files or ["one.txt"], needle,
            max_bytes=kw.get("max_bytes", 1000), max_file_bytes=kw.get("max_file_bytes", 1000),
            max_hits=kw.get("max_hits", 10))

    def test_literal_unicode_match_and_valid_negative(self):
        positive = self.search()
        self.assertEqual(positive["matches"], [{"path": "one.txt", "lines": [2]}])
        self.assertTrue(positive["complete"])
        self.assertFalse(positive["absence_supported"])
        negative = self.search(needle="no-such-needle")
        self.assertTrue(negative["absence_supported"])
        self.assertEqual(negative["scope"], "only_the_explicit_files")

    def test_does_not_read_unselected_sibling(self):
        (self.root / "huge.txt").write_bytes(b"x" * 10000)
        self.assertEqual(self.search()["files_read"], 1)

    def test_multiline_queries_are_refused_not_false_negatives(self):
        for separator in "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029":
            with self.subTest(separator=repr(separator)):
                with self.assertRaisesRegex(tool.Refused, "single_line_literal"):
                    self.search(needle="hello" + separator + "第五轮")
        proc = subprocess.run([sys.executable, tool.__file__, "search", "--root",
            str(self.root), "--file", "one.txt", "--contains", "hello\n第五轮"],
            capture_output=True, timeout=15)
        self.assertEqual(proc.returncode, 3)
        self.assertFalse(json.loads(proc.stdout)["absence_supported"])

    def test_preflights_all_files_before_content_read(self):
        (self.root / "big.txt").write_bytes(b"x" * 10000)
        with mock.patch.object(Path, "open", side_effect=AssertionError("content opened")):
            with self.assertRaisesRegex(tool.Refused, "per_file_budget"):
                self.search(files=["one.txt", "big.txt"])

    def test_budget_overflow_not_zero_match(self):
        with self.assertRaisesRegex(tool.Refused, "total_content_budget"):
            self.search(max_bytes=1)
        with self.assertRaisesRegex(tool.Refused, "per_file_budget"):
            self.search(max_file_bytes=1)

    def test_directory_parent_glob_and_duplicate_refused(self):
        (self.root / "subdir").mkdir()
        for files in (["subdir"], ["../other.txt"], ["*.txt"], ["one.txt", "one.txt"],
                      [str(self.root / "one.txt")]):
            with self.subTest(files=files), self.assertRaises(tool.Refused):
                self.search(files=files)

    def test_binary_and_bad_encoding_refused(self):
        (self.root / "binary").write_bytes(b"hello\0data")
        with self.assertRaises(tool.Refused):
            self.search(files=["binary"])
        (self.root / "invalid").write_bytes(b"\xff")
        with self.assertRaises(UnicodeError):
            self.search(files=["invalid"])

    def test_long_line_stops_reading_before_entire_file(self):
        import io
        stream = io.BytesIO(b"x" * 200000)
        with self.assertRaisesRegex(tool.Refused, "line_byte_budget"):
            tool.read_text_bytes(stream, 200000, 4096)
        self.assertLess(stream.tell(), 200000)

    def test_output_bytes_cap_cli(self):
        for n in range(20):
            (self.root / ("file" + str(n) + ".txt")).write_text("needle")
        args = []
        for n in range(20):
            args += ["--file", "file" + str(n) + ".txt"]
        proc = subprocess.run([sys.executable, tool.__file__, "search", "--root",
            str(self.root), "--contains", "needle", "--max-output-bytes", "1024", *args],
            capture_output=True, timeout=15)
        self.assertEqual(proc.returncode, 2)
        self.assertLessEqual(len(proc.stdout), 1024)
        self.assertFalse(json.loads(proc.stdout)["complete"])

    def test_output_limit_is_partial(self):
        got = self.search(needle="hello", max_hits=1)
        self.assertEqual(got["status"], "limit")
        self.assertFalse(got["complete"])
        self.assertFalse(got["absence_supported"])

    def test_broad_roots_refused(self):
        for name in ("scratch", "tmp", ".codex", "2026", "bidking-worktrees"):
            root = Path(self.temp.name) / name
            root.mkdir()
            with self.subTest(name=name), self.assertRaisesRegex(tool.Refused, "broad_root"):
                tool.checked_root(root)

    def test_symlink_is_rejected_before_content(self):
        link = self.root / "link.txt"
        try:
            link.symlink_to(self.root / "one.txt")
        except OSError as e:
            self.skipTest(f"symlink creation unavailable: {e}")
        with self.assertRaisesRegex(tool.Refused, "reparse"):
            self.search(files=["link.txt"])
        got = tool.inventory(self.root, max_entries=20, max_depth=2)
        self.assertEqual(got["reparse_count"], 1)
        self.assertFalse(got["complete"])

    def test_reparse_attribute_control_without_symlink_privilege(self):
        obj = mock.Mock(st_mode=stat_mode(), st_file_attributes=0x400)
        self.assertTrue(tool.reparse(obj))

    @unittest.skipUnless(os.name == "nt", "Windows junction control")
    def test_real_windows_junction_is_not_followed(self):
        target = self.root / "target"
        target.mkdir()
        (target / "leaf.txt").write_text("needle")
        link = self.root / "junction"
        command = [
            "powershell.exe", "-NoProfile", "-Command",
            "New-Item -ItemType Junction -Path '" + str(link)
            + "' -Target '" + str(target) + "' | Out-Null",
        ]
        made = subprocess.run(command, capture_output=True, timeout=10)
        self.assertEqual(made.returncode, 0, made.stderr)
        try:
            with self.assertRaisesRegex(tool.Refused, "reparse"):
                self.search(files=["junction/leaf.txt"])
            got = tool.inventory(self.root, max_entries=20, max_depth=2)
            self.assertEqual(got["reparse_count"], 1)
            self.assertFalse(got["complete"])
            self.assertEqual(got["file_count"], 2)  # one.txt + the real target leaf
        finally:
            os.rmdir(link)  # Only the test's exact junction, never its target.
        self.assertEqual((target / "leaf.txt").read_text(), "needle")

    def test_inventory_is_metadata_only_and_not_delete_authority(self):
        with mock.patch.object(Path, "open", side_effect=AssertionError("opened content")):
            got = tool.inventory(self.root, max_entries=20, max_depth=2)
        self.assertEqual(got["file_count"], 1)
        self.assertEqual(got["bytes_read"], 0)
        self.assertFalse(got["cleanup_eligible"])

    def test_entry_and_depth_limits(self):
        child = self.root / "child"
        child.mkdir()
        (child / "leaf").write_text("data")
        capped = tool.inventory(self.root, max_entries=1, max_depth=3)
        self.assertFalse(capped["complete"])
        self.assertEqual(capped["entries_seen"], 1)
        deep = tool.inventory(self.root, max_entries=20, max_depth=0)
        self.assertFalse(deep["complete"])
        self.assertEqual(deep["reason"], "depth_limit_reached")

    def test_real_hard_timeout_kills_owned_worker(self):
        start = time.monotonic()
        got = tool.run_bounded([sys.executable, "-c", "import time; time.sleep(30)"], .15)
        self.assertEqual(got["status"], "timeout")
        self.assertTrue(got["owned_worker_reaped"])
        self.assertFalse(got["absence_supported"])
        self.assertLess(time.monotonic() - start, 5)

    def test_cli_search_and_inventory(self):
        for args in (["search", "--file", "one.txt", "--contains", "1003035"], ["inventory"]):
            proc = subprocess.run([sys.executable, tool.__file__, *args, "--root", str(self.root)],
                                  capture_output=True, timeout=15)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(json.loads(proc.stdout)["complete"])

    def test_invalid_limits_and_missing_file_cli_fail_closed(self):
        for args in (["--max-bytes", "999999999"], ["--timeout", "nan"],
                     ["--file", "missing.txt"]):
            proc = subprocess.run([sys.executable, tool.__file__, "search", "--root",
                str(self.root), "--file", "one.txt", "--contains", "needle", *args],
                capture_output=True, timeout=15)
            self.assertEqual(proc.returncode, 3)
            self.assertFalse(json.loads(proc.stdout)["absence_supported"])


def stat_mode():
    import stat
    return stat.S_IFREG | 0o600


if __name__ == "__main__":
    unittest.main()
