"""HTTP publishing checks for the self-paced notebook series."""
import copy
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import app


MODULE_IDS = ["why-tool-runner", "build-tools", "verify-actions", "make-it-yours"]


class TutorialSeriesServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.actual_root = app.ROOT
        cls.manifest_provider = staticmethod(app.tutorial_manifest)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join(timeout=3)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "tutorials").mkdir()
        self.manifest = {"modules": [], "notice": "Practice notebooks; no key required."}
        self.files = {}
        for index, module_id in enumerate(MODULE_IDS, 1):
            stem = f"{index:02d}-{module_id}"
            module = {"id": module_id, "title": module_id, "minutes": 10,
                      "outcome": "Practice one skill", "exercise": "Change one thing",
                      "notebook": f"/tutorials/{stem}.ipynb",
                      "reference": f"/tutorials/{stem}.reference.ipynb"}
            self.manifest["modules"].append(module)
            for field in ("notebook", "reference"):
                raw = json.dumps({"nbformat": 4, "nbformat_minor": 5, "cells": [],
                                  "metadata": {"test_module": module_id, "test_kind": field}}).encode()
                self.files[module[field]] = raw
                (self.root / module[field].lstrip("/")).write_bytes(raw)
        (self.root / "tool-runner-lab.ipynb").write_text('{"nbformat": 4, "cells": []}')
        root_patch = patch.object(app, "ROOT", self.root)
        manifest_patch = patch.object(app, "tutorial_manifest", return_value=self.manifest)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        manifest_patch.start()
        self.addCleanup(manifest_patch.stop)

    def get(self, path):
        request = Request(self.base + path)
        try:
            response = build_opener(ProxyHandler({})).open(request, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.read(), response.headers

    def test_manifest_and_all_eight_declared_downloads(self):
        status, raw, headers = self.get("/api/tutorials")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw), self.manifest)
        self.assertIn("application/json", headers["Content-Type"])
        self.assertEqual(len(self.files), 8)
        for path, expected in self.files.items():
            with self.subTest(path=path):
                status, raw, headers = self.get(path)
                self.assertEqual(status, 200)
                self.assertEqual(raw, expected)
                self.assertIn("application/x-ipynb+json", headers["Content-Type"])
                self.assertEqual(headers["Content-Disposition"], f'attachment; filename="{path.rsplit("/", 1)[1]}"')
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(int(headers["Content-Length"]), len(raw))

    def test_undeclared_files_and_path_escapes_are_not_served(self):
        (self.root / "tutorials" / "private.ipynb").write_text("PRIVATE NOTEBOOK")
        (self.root / "private.ipynb").write_text("PRIVATE PARENT")
        for path in ("/tutorials/", "/tutorials", "/tutorials/private.ipynb",
                     "/tutorials/../private.ipynb", "/tutorials/%2e%2e/private.ipynb",
                     "/tutorials/%2E%2E%2Fprivate.ipynb", "/tutorials/nested/private.ipynb",
                     "/tutorials/01-why-tool-runner.ipynb/", "/tutorials/01-why-tool-runner.py",
                     "/tutorials/01-why-tool-runner.ipynb%00", "/app.py"):
            with self.subTest(path=path):
                status, raw, _ = self.get(path)
                self.assertEqual(status, 404)
                self.assertNotIn(b"PRIVATE", raw)

    def test_missing_declared_file_is_a_safe_error(self):
        path = self.manifest["modules"][0]["notebook"]
        (self.root / path.lstrip("/")).unlink()
        with self.assertLogs(level="ERROR"):
            status, raw, _ = self.get(path)
        self.assertEqual(status, 500)
        self.assertEqual(json.loads(raw), {"error": "This tutorial is unavailable. Try again later."})
        self.assertNotIn(str(self.root).encode(), raw)

    def test_unavailable_manifest_does_not_break_existing_routes(self):
        with patch.object(app, "tutorial_manifest", side_effect=RuntimeError("private-path-secret")):
            with self.assertLogs(level="ERROR"):
                status, raw, _ = self.get("/api/tutorials")
            self.assertEqual(status, 500)
            self.assertNotIn(b"private-path-secret", raw)
            for path in ("/api/health", "/api/notebook", "/tool-runner-lab.ipynb"):
                with self.subTest(path=path):
                    status, raw, _ = self.get(path)
                    self.assertEqual(status, 200)
                    self.assertIsInstance(json.loads(raw), dict)

    def test_invalid_manifest_path_and_symlink_cannot_escape_tutorials(self):
        invalid = copy.deepcopy(self.manifest)
        invalid["modules"][0]["notebook"] = "/tutorials/../private.ipynb"
        with patch.object(app, "tutorial_manifest", return_value=invalid), self.assertLogs(level="ERROR"):
            status, raw, _ = self.get("/api/tutorials")
        self.assertEqual(status, 500)
        self.assertNotIn(b"private.ipynb", raw)
        path = self.manifest["modules"][0]["notebook"]
        target = self.root / path.lstrip("/")
        target.unlink()
        outside = self.root / "private.ipynb"
        outside.write_text("PRIVATE DATA")
        target.symlink_to(outside)
        with self.assertLogs(level="ERROR"):
            status, raw, _ = self.get(path)
        self.assertEqual(status, 500)
        self.assertNotIn(b"PRIVATE DATA", raw)

    def test_published_series_contains_eight_downloadable_notebooks(self):
        with patch.object(app, "ROOT", self.actual_root), patch.object(app, "tutorial_manifest", self.manifest_provider):
            status, raw, _ = self.get("/api/tutorials")
            self.assertEqual(status, 200)
            manifest = json.loads(raw)
            self.assertEqual([module["id"] for module in manifest["modules"]], MODULE_IDS)
            paths = [module[field] for module in manifest["modules"] for field in ("notebook", "reference")]
            self.assertEqual(len(set(paths)), 8)
            for path in paths:
                with self.subTest(path=path):
                    status, raw, _ = self.get(path)
                    self.assertEqual(status, 200)
                    self.assertEqual(json.loads(raw)["nbformat"], 4)
                    self.assertEqual(raw, (self.actual_root / path.lstrip("/")).read_bytes())


if __name__ == "__main__":
    unittest.main()
