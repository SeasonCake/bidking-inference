"""Synthetic positive/negative controls for codecs, classifications and input bounds."""
import base64
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research.tables import assert_uniform_columns, decode_table_text, decode_table_text_strict, iter_table_rows
from research.tables.__main__ import main
from research.tables.diff import InputError, Limits, compare_directories


FIXTURES = Path(__file__).resolve().parents[1] / "research" / "tables" / "fixtures"


class CodecTests(unittest.TestCase):
    def test_legacy_whitespace_and_permissive_punctuation(self):
        self.assertEqual(decode_table_text(" YQ==! \n"), "a")
        with self.assertRaises(ValueError):
            decode_table_text_strict("YQ==!")

    def test_strict_base64_is_not_decryption(self):
        self.assertEqual(decode_table_text_strict(" YQ==\n"), "a")
        for bad in ("@not-base64", "Y", base64.b64encode(b"\xff").decode()):
            with self.subTest(bad=bad), self.assertRaises((ValueError, UnicodeError)):
                decode_table_text_strict(bad)

    def test_rows_and_empty_contract(self):
        self.assertEqual(list(iter_table_rows("a\tb\r\nc\td\n")), [["a", "b"], ["c", "d"]])
        self.assertEqual(list(iter_table_rows("a\tb\n\n")), [["a", "b"], [""]])
        self.assertEqual(assert_uniform_columns([]), 0)
        self.assertEqual(assert_uniform_columns([["a", "b"]]), 2)
        with self.assertRaisesRegex(ValueError, r"row\[1\].*1 cols, expected 2"):
            assert_uniform_columns([["a", "b"], ["c"]])

    def test_legacy_does_not_remove_bom(self):
        raw = base64.b64encode("\ufeffid\tname\n".encode()).decode()
        self.assertTrue(decode_table_text(raw).startswith("\ufeff"))


class DirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.before, self.after = self.root / "before", self.root / "after"
        self.before.mkdir()
        self.after.mkdir()
        self.schema = self.root / "schema.json"
        self.spec = {"version": 1, "tables": {"toy.tsv": {"encoding": "tsv", "columns": ["id", "name"], "id_column": "id"}}}
        self.write_schema()

    def write_schema(self):
        self.schema.write_text(json.dumps(self.spec), encoding="utf-8")

    def pair(self, left=b"id\tname\n1\tA\n", right=None):
        (self.before / "toy.tsv").write_bytes(left)
        (self.after / "toy.tsv").write_bytes(left if right is None else right)

    def compare(self, **kwargs):
        return compare_directories(self.before, self.after, self.schema, **kwargs)

    def test_demo_all_classifications_and_hand_checked_positive_controls(self):
        result = compare_directories(FIXTURES / "before", FIXTURES / "after", FIXTURES / "schema.json")
        self.assertEqual(result["counts"], {"added": 1, "removed": 1, "changed": 2, "unchanged": 1})
        entries = {x["name"]: x for x in result["details"]}
        self.assertFalse(entries["labels.tsv"]["canonical_changed"])
        self.assertTrue(entries["items.tsv"]["structure_changed"])
        self.assertEqual(entries["items.tsv"]["after"]["duplicate_id_rows"], 1)
        self.assertIn("unknown_columns", entries["items.tsv"]["after"]["issues"])
        self.assertIsNone(entries["notes.txt"]["structure_changed"])
        self.assertEqual(result["semantic_compatibility"], "not_assessed")

    def test_bom_line_endings_only_raw_change(self):
        self.pair(right=b"\xef\xbb\xbfid\tname\r\n1\tA\r\n")
        item = self.compare()["details"][0]
        self.assertEqual(item["classification"], "changed")
        self.assertFalse(item["canonical_changed"])
        self.assertFalse(item["structure_changed"])

    def test_cell_change_does_not_invent_structure_change(self):
        self.pair(right=b"id\tname\n1\tB\n")
        item = self.compare()["details"][0]
        self.assertTrue(item["canonical_changed"])
        self.assertFalse(item["structure_changed"])
        self.assertNotIn('"B"', json.dumps(item))

    def test_non_structured_and_non_rectangular(self):
        for data, issue in ((b"plain prose\n", "non_structured_text"), (b"id\tname\n1\n", "non_rectangular")):
            self.pair(right=data)
            item = self.compare()["details"][0]
            self.assertIn(issue, item["after"]["issues"])
            self.assertIsNone(item["structure_changed"])

    def test_invalid_utf8_and_base64_are_reported_not_missing(self):
        self.pair(right=b"\xff")
        self.assertEqual(self.compare()["details"][0]["after"]["issues"], ["invalid_encoding"])
        self.spec["tables"]["toy.tsv"]["encoding"] = "base64-tsv"
        self.write_schema()
        self.pair(base64.b64encode(b"id\tname\n1\tA\n"), b"@invalid")
        item = self.compare()["details"][0]
        self.assertEqual(item["classification"], "changed")
        self.assertIsNone(item["canonical_changed"])

    def test_missing_columns_duplicate_header_empty_ids(self):
        self.pair(right=b"id\tid\n\t1\n")
        issues = self.compare()["details"][0]["after"]["issues"]
        self.assertEqual(set(issues), {"invalid_header", "missing_columns", "empty_ids"})

    def test_reordered_columns_reported(self):
        self.pair(right=b"name\tid\nA\t1\n")
        self.assertIn("column_order_changed", self.compare()["details"][0]["after"]["issues"])

    def test_unknown_table_and_absent_schema_are_explicit(self):
        (self.before / "other.txt").write_text("Artificial prose", encoding="utf-8")
        result = self.compare()
        self.assertEqual(result["issue_counts"], {"unknown_table": 1})
        self.assertEqual(result["schema_entries_absent_from_both"], 1)
        self.assertIsNone(result["details"][0]["before"]["structure"])

    def test_identical_known_good_has_no_issues(self):
        self.pair()
        result = self.compare()
        self.assertEqual(result["counts"]["unchanged"], 1)
        self.assertEqual(result["issue_counts"], {})

    def test_file_total_and_count_budgets(self):
        self.pair()
        for limits in (Limits(max_file_bytes=4), Limits(max_total_bytes=self.schema.stat().st_size + 1)):
            with self.subTest(limits=limits), self.assertRaisesRegex(InputError, "byte budget"):
                self.compare(limits=limits)
        (self.before / "extra.txt").write_bytes(b"x")
        with self.assertRaisesRegex(InputError, "entry budget"):
            self.compare(limits=Limits(max_files=1))

    def test_bad_limits_and_schema(self):
        for value in (0, -1, 1.5, True):
            with self.assertRaises(InputError):
                Limits(max_files=value)
        for raw in ('{}', '{"version":1,"version":1,"tables":{}}', '{"version":1,"tables":{"../toy.tsv":{}}}'):
            self.schema.write_text(raw, encoding="utf-8")
            with self.assertRaises(InputError):
                self.compare()

    def test_missing_directory_is_error_not_empty_collection(self):
        with self.assertRaisesRegex(InputError, "input inaccessible"):
            compare_directories(self.root / "missing", self.after, self.schema)

    def test_child_directory_rejected_without_recursion(self):
        (self.before / "nested").mkdir()
        with self.assertRaisesRegex(InputError, "regular file"):
            self.compare()

    def test_reparse_attribute_positive_control(self):
        original = Path.lstat
        target = self.before
        def fake_lstat(path):
            info = original(path)
            if path == target:
                class Reparse:
                    st_mode = info.st_mode
                    st_file_attributes = 0x400
                return Reparse()
            return info
        with patch.object(Path, "lstat", fake_lstat), self.assertRaisesRegex(InputError, "reparse"):
            self.compare()

    def test_real_symlink_rejected_when_host_allows_creation(self):
        target = self.root / "artificial-target.txt"
        target.write_text("Synthetic target", encoding="utf-8")
        try:
            (self.before / "linked.txt").symlink_to(target)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("host does not permit temporary symlinks: " + type(exc).__name__)
        with self.assertRaisesRegex(InputError, "links.*reparse"):
            self.compare()

    def test_cli_requires_explicit_choice(self):
        for args in ([], ["--demo", "--before", str(self.before)]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                main(args)
            self.assertEqual(raised.exception.code, 2)

    def test_cli_summary_and_details(self):
        for args, detailed in ((["--demo"], False), (["--demo", "--details"], True)):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(args), 0)
            result = json.loads(out.getvalue())
            self.assertEqual("details" in result, detailed)
            self.assertNotIn("Toy Alpha", out.getvalue())

    def test_cli_wrong_input_returns_two_without_traceback(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--before", str(self.before), "--after", str(self.after),
                         "--schema", str(self.schema), "--max-files", "0"])
        self.assertEqual(code, 2)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("input error:", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())


if __name__ == "__main__":
    unittest.main()
