"""Hosted Colab links are optional metadata; downloads always remain available."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

import tutorial_series


class ColabLinkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "publication.json"
        self.original = copy.deepcopy(tutorial_series.MODULES)

    def manifest(self, config):
        self.path.write_text(json.dumps(config))
        return tutorial_series.manifest(publication_path=self.path)

    def assert_download_only(self, result):
        self.assertEqual(result["modules"], self.original)
        self.assertEqual(result["notice"], tutorial_series.NOTICE)

    def test_all_eight_colab_links_and_unchanged_downloads(self):
        result = self.manifest({"repository": "dannymsullivan/tool-runner-lab", "ref": "main"})
        links = []
        for original, module in zip(self.original, result["modules"]):
            for field, link in (("notebook", "colab_url"), ("reference", "reference_colab_url")):
                self.assertEqual(module[field], original[field])
                expected = "https://colab.research.google.com/github/dannymsullivan/tool-runner-lab/blob/main" + original[field]
                self.assertEqual(module[link], expected)
                links.append(module[link])
        self.assertEqual(len(set(links)), 8)
        self.assertEqual(tutorial_series.MODULES, self.original)

    def test_missing_or_unreadable_config_keeps_downloads(self):
        self.assert_download_only(tutorial_series.manifest(publication_path=self.path))
        self.path.mkdir()
        self.assert_download_only(tutorial_series.manifest(publication_path=self.path))

    def test_malformed_config_keeps_downloads(self):
        for raw in (b"{bad json", b"", b"\xff\xfe", b"null", b"[]", b"true"):
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                self.assert_download_only(tutorial_series.manifest(publication_path=self.path))

    def test_unsafe_or_incomplete_config_keeps_downloads(self):
        bad_repositories = [None, [], "", "owner", "https://github.com/owner/repo",
                            "token@github.com/owner/repo", "owner/repo?token=secret", "owner/repo#main",
                            "owner/../repo", "owner/..", "../repo", "owner//repo", "owner/repo%2Fextra",
                            "-owner/repo", "owner-/repo", "owner/repo\n", "owner\\repo"]
        bad_refs = [None, [], "", "../main", "main/extra", "main?token=secret", "main#fragment",
                    "main%2Fextra", "main\\extra", "main\n", ".", "..", "feature..main"]
        configs = [{}, {"repository": "owner/repo"}, {"ref": "main"},
                   {"repository": "owner/repo", "ref": "main", "token": "secret"}]
        configs += [{"repository": repository, "ref": "main"} for repository in bad_repositories]
        configs += [{"repository": "owner/repo", "ref": ref} for ref in bad_refs]
        for config in configs:
            with self.subTest(config=config):
                self.assert_download_only(self.manifest(config))

    def test_safe_tags_and_commit_refs_are_supported(self):
        for ref in ("main", "v1.0.0", "release-2026_10", "012345abcdef"):
            with self.subTest(ref=ref):
                result = self.manifest({"repository": "valid-owner/tool_runner.lab", "ref": ref})
                self.assertIn("/github/valid-owner/tool_runner.lab/blob/" + ref + "/tutorials/",
                              result["modules"][0]["colab_url"])


if __name__ == "__main__":
    unittest.main()
