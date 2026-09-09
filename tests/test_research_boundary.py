"""Known-good and deliberately invalid controls for the research boundary checker."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.verify import (BINARY_SUFFIXES, TEXT_SUFFIXES, local_document_link_errors,
                            research_manifest_errors, text_boundary_errors)


class ResearchBoundaryTests(unittest.TestCase):
    def test_local_links_known_good_and_missing_control(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "target with spaces.md").write_text("# Target", encoding="utf-8")
            page = root / "README.md"
            page.write_text("[local](target%20with%20spaces.md) [anchor](#hello) [web](https://example.org)", encoding="utf-8")
            self.assertEqual(local_document_link_errors(root), [])
            page.write_text('[bad](missing.md) <img src="missing.png">', encoding="utf-8")
            self.assertEqual(len(local_document_link_errors(root)), 2)

    def test_native_sources_and_symbols_are_in_scan_scope(self):
        self.assertTrue({".cs", ".ps1", ".cpp", ".h", ".tsv", ".csv"} <= TEXT_SUFFIXES)
        self.assertIn(".pdb", BINARY_SUFFIXES)

    def test_metadata_path_exception_is_not_a_code_exception(self):
        private_name = "bidking" + "_lab"
        source = "src/" + private_name + "/live/helper.py"
        text = json.dumps({"entries": [{"sources": [{"path": source}]}]})
        self.assertEqual(text_boundary_errors("docs/research/PROVENANCE.json", text), [])
        self.assertTrue(text_boundary_errors("research/unsafe.py", "import " + private_name))
        self.assertTrue(text_boundary_errors("docs/research/other.md", source))
        bad = json.dumps({"entries": [], "note": "import " + private_name})
        self.assertTrue(text_boundary_errors("docs/research/PROVENANCE.json", bad))

    def test_only_exact_public_legacy_locator_is_allowed(self):
        suffix = "data/" + "processed"
        allowed = "legacy/data-v0.2.7-hotfix3/" + suffix
        self.assertEqual(text_boundary_errors("research/reader.py", allowed), [])
        self.assertTrue(text_boundary_errors("research/reader.py", suffix))

    def test_fixture_identity_and_unlisted_file_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "research/example/fixtures/history.json"
            path.parent.mkdir(parents=True)
            payload = b'{"kind":"historical_aggregate","n":8}\n'
            path.write_bytes(payload)
            relative = path.relative_to(root).as_posix()
            manifest = {"schema_version": 1, "entries": [{"id": "example", "kind": "historical_aggregate",
                "adaptation": "Approved summary", "files": [relative],
                "sources": [{"commit": "a" * 40, "git_blob": "b" * 40, "path": "docs/history.md"}]}],
                "fixtures": [{"path": relative, "kind": "historical_aggregate", "sha256": hashlib.sha256(payload).hexdigest()}]}
            self.assertEqual(research_manifest_errors(manifest, root), [])
            bad = copy.deepcopy(manifest)
            bad["fixtures"][0]["sha256"] = "0" * 64
            self.assertTrue(any("bytes changed" in e for e in research_manifest_errors(bad, root)))
            bad = copy.deepcopy(manifest)
            bad["fixtures"][0]["kind"] = "unreviewed"
            self.assertTrue(any("unclassified" in e for e in research_manifest_errors(bad, root)))
            bad["fixtures"][0]["kind"] = "synthetic"
            self.assertTrue(any("contradicts" in e for e in research_manifest_errors(bad, root)))
            (path.parent / "extra.txt").write_text("synthetic control", encoding="utf-8")
            errors = research_manifest_errors(manifest, root)
            self.assertTrue(any("no provenance" in e for e in errors))
            self.assertTrue(any("not reviewed" in e for e in errors))

    def test_absolute_and_parent_source_paths_rejected(self):
        for source in ("/tmp/source.py", "../source.py", "C:/private/source.py"):
            manifest = {"schema_version": 1, "entries": [{"id": "x", "kind": "historical_adaptation",
                "adaptation": "control", "files": ["research/missing.py"],
                "sources": [{"commit": "a" * 40, "git_blob": "b" * 40, "path": source}]}], "fixtures": []}
            self.assertTrue(any("source identity" in e for e in research_manifest_errors(manifest, Path("."))))
