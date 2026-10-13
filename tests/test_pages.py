"""Static publishing checks: real offline execution, portable links, no secrets."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_pages
import lab


class PagesExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.output = cls.root / "site"
        cls.writes = []
        original = lab.Store.start_return

        def record_write(store, order_id, reason):
            cls.writes.append(order_id)
            return original(store, order_id, reason)

        with patch("socket.socket.connect", side_effect=AssertionError("Pages export must remain offline")), patch.dict(
            os.environ, {"ANTHROPIC_API_KEY": "PRIVATE-BUILD-KEY", "ANTHROPIC_MODEL": "PRIVATE-BUILD-MODEL"}
        ), patch.object(lab.Store, "start_return", record_write):
            build_pages.export_pages("dannymsullivan/tool-runner-lab", output=cls.output)
        cls.raw = (cls.output / "static-data.js").read_text()
        cls.payload = json.loads(cls.raw.removeprefix("window.LAB_STATIC = ").removesuffix(";\n"))

    def test_real_runs_and_all_fifteen_cell_results_are_available(self):
        self.assertTrue(self.writes, "The build must execute actual Python database tools.")
        self.assertEqual(set(self.payload), {"config", "notebook", "tutorials", "runs", "cells"})
        self.assertEqual(set(self.payload["runs"]), {"0", "3"})
        empty, stocked = (self.payload["runs"][key] for key in ("0", "3"))
        self.assertEqual([row["order_id"] for row in empty["state"]["returns"]], ["A100"])
        self.assertEqual(stocked["state"]["returns"], [])
        for result in self.payload["runs"].values():
            self.assertTrue(result["fixture"])
            self.assertTrue(result["completed"])
            self.assertTrue(all(check["passed"] for check in result["checks"]))
            self.assertEqual(result["metrics"]["duration_ms"], 0)
            self.assertTrue(all(event["elapsed_ms"] == 0 for event in result["events"]))
        cells = self.payload["cells"]
        expected = {"inventory", "schema", "runner", "compare", "verify"}
        for cell in self.payload["notebook"]["deep_cells"]:
            expected.update(f"{cell['id']}:{variant['id']}" for variant in cell["variants"])
        self.assertEqual(set(cells), expected)
        self.assertEqual(len(cells), 15)
        self.assertEqual(cells["why:manual"]["output"], cells["why:runner"]["output"])
        self.assertIn("1 saved return", cells["verify"]["output"])
        for key, result in cells.items():
            self.assertTrue(result["passed"], key)
            expected_fields = {"cell_id", "output", "takeaway", "passed"} | ({"variant"} if ":" in key else set())
            self.assertEqual(set(result), expected_fields)

    def test_only_clean_portable_public_artifacts_are_exported(self):
        expected = build_pages.TOP_LEVEL | {path.lstrip("/") for path in build_pages.NOTEBOOK_PATHS}
        actual = {path.relative_to(self.output).as_posix() for path in self.output.rglob("*") if path.is_file()}
        self.assertEqual(actual, expected)
        html = (self.output / "index.html").read_text()
        self.assertIn('href="./styles.css"', html)
        self.assertIn('href="./"', html)
        self.assertLess(html.index('src="./static-data.js"'), html.index('src="./app.js"'))
        self.assertNotRegex(html, r'(?:src|href)="/(?!/)')
        for module in self.payload["tutorials"]["modules"]:
            for field, colab in (("notebook", "colab_url"), ("reference", "reference_colab_url")):
                path = module[field]
                self.assertEqual((self.output / path.lstrip("/")).read_bytes(), (build_pages.ROOT / path.lstrip("/")).read_bytes())
                self.assertEqual(module[colab], f"https://colab.research.google.com/github/dannymsullivan/tool-runner-lab/blob/main{path}")
        config = self.payload["config"]
        self.assertFalse(config["live_available"])
        self.assertFalse(config["connected"])
        self.assertTrue(config["static_preview"])
        self.assertEqual(config["source_url"], "https://github.com/dannymsullivan/tool-runner-lab")
        for secret in ("PRIVATE-BUILD-KEY", "PRIVATE-BUILD-MODEL", str(build_pages.ROOT), "/Users/", "/private/var/"):
            self.assertNotIn(secret, self.raw)

    def test_export_is_reproducible_and_independent(self):
        other = self.root / "second-site"
        with patch("socket.socket.connect", side_effect=AssertionError("No network")):
            build_pages.export_pages("dannymsullivan/tool-runner-lab", output=other)
        for source in self.output.rglob("*"):
            if source.is_file():
                self.assertEqual(source.read_bytes(), (other / source.relative_to(self.output)).read_bytes(), source.name)
        self.assertFalse((build_pages.ROOT / "static-data.js").exists())

    def test_unsafe_targets_and_foreign_files_fail_before_execution(self):
        for repository, ref in (("../private", "main"), ("owner/repo/extra", "main"),
                                ("owner/repo", "../../secrets"), ("owner/repo", "main?key=value"),
                                ("owner/repo", "main#secret"), ("https://github.com/a/b", "main")):
            with self.subTest(repository=repository, ref=ref), patch.object(build_pages.lab, "run_lab") as run:
                with self.assertRaises(ValueError):
                    build_pages.export_pages(repository, ref, self.root / "invalid")
                run.assert_not_called()
        foreign = self.root / "foreign"
        foreign.mkdir()
        (foreign / "personal.txt").write_text("keep this")
        with patch.object(build_pages.lab, "run_lab") as run, self.assertRaises(ValueError):
            build_pages.export_pages("owner/repo", output=foreign)
        run.assert_not_called()
        self.assertEqual((foreign / "personal.txt").read_text(), "keep this")
        for destination in (build_pages.ROOT, build_pages.ROOT / "web", build_pages.ROOT / "tutorials"):
            with self.assertRaises(ValueError):
                build_pages.export_pages("owner/repo", output=destination)

    def test_invalid_manifest_or_execution_never_publishes_partial_results(self):
        catalog = copy.deepcopy(build_pages.tutorial_series.manifest())
        catalog["modules"][0]["notebook"] = "/tutorials/../../private.ipynb"
        with patch.object(build_pages.tutorial_series, "manifest", return_value=catalog), self.assertRaises(ValueError):
            build_pages.export_pages("owner/repo", output=self.root / "invalid-manifest")
        self.assertFalse((self.root / "invalid-manifest").exists())
        with patch.object(build_pages.lab, "run_lab", return_value={"completed": False}), self.assertRaises(ValueError):
            build_pages.export_pages("owner/repo", output=self.root / "failed-build")
        self.assertFalse((self.root / "failed-build").exists())


if __name__ == "__main__":
    unittest.main()
