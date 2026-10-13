"""HTTP/session integration tests. All runs are fixtures; connection calls are mocked."""
import http.cookiejar
import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

import app
import lab


class ServerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.environment = patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "ANTHROPIC_MODEL": ""})
        cls.environment.start()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join(timeout=3)
        cls.environment.stop()

    def setUp(self):
        self.cookies = http.cookiejar.CookieJar()
        self.browser = build_opener(ProxyHandler({}), HTTPCookieProcessor(self.cookies))

    def request(self, path, payload=None, *, browser=None, origin=None, content_type="application/json"):
        headers = {}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            headers = {"Content-Type": content_type, "Origin": origin or self.base}
        request = Request(self.base + path, data=data, headers=headers)
        try:
            response = (browser or self.browser).open(request, timeout=20)
        except HTTPError as exc:
            response = exc
        with response:
            raw = response.read().decode()
            if "ndjson" in response.headers.get("Content-Type", ""):
                body = [json.loads(line) for line in raw.splitlines() if line.strip()]
            else:
                body = json.loads(raw)
            return response.status, body, response.headers, raw

    def run_fixture(self, scenario="normal", **options):
        code, body, _, _ = self.request("/api/run", {"scenario": scenario, "mode": "fixture", **options})
        self.assertEqual(code, 200)
        self.assertTrue(any(item.get("kind") == "event" for item in body))
        final = [item["result"] for item in body if item.get("kind") == "result"]
        self.assertEqual(len(final), 1, body)
        self.assertTrue(final[0]["fixture"])
        return final[0]

    def test_config_curriculum_and_cookie_attributes(self):
        code, config, headers, raw = self.request("/api/config")
        self.assertEqual(code, 200)
        self.assertFalse(config["connected"])
        self.assertFalse(config["live_available"])
        for name in ("source_url", "source_label", "editor_url"):
            self.assertIn(name, config)
        self.assertNotIn('"api_key"', raw)
        self.assertNotIn('"credentials"', raw)
        cookie = headers.get("Set-Cookie", "").lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=", cookie)
        self.assertTrue(list(self.cookies))
        code, course, _, _ = self.request("/api/curriculum")
        self.assertEqual(code, 200)
        self.assertEqual([step["id"] for step in course["steps"]],
                         ["loop", "tool", "runner", "writes", "failures", "false_success", "capstone"])

    def test_fixture_run_is_saved_only_in_its_browser_session(self):
        result = self.run_fixture()
        code, report, _, _ = self.request("/api/report")
        self.assertEqual(code, 200)
        for field in ("completed_steps", "checkpoints", "exercise_results", "runs"):
            self.assertIn(field, report)
        self.assertEqual(len(report["runs"]), 1)
        self.assertEqual(report["runs"][0]["run_id"], result["run_id"])
        outsider = build_opener(ProxyHandler({}), HTTPCookieProcessor(http.cookiejar.CookieJar()))
        _, other_report, _, _ = self.request("/api/report", browser=outsider)
        self.assertEqual(other_report["runs"], [])
        self.assertEqual(other_report["completed_steps"], [])

    def test_checkpoint_uses_saved_evidence_not_browser_supplied_passes(self):
        fabricated_run = lab.run_lab({"scenario": "normal"})
        code, checkpoint, _, _ = self.request("/api/checkpoint", {
            "step_id": "loop", "answers": {"executor": "application"},
            "runs": [fabricated_run], "exercises": {"inventory": {"passed": True}},
        })
        self.assertEqual(code, 200)
        self.assertFalse(checkpoint.get("checkpoint", checkpoint)["passed"])
        _, report, _, _ = self.request("/api/report")
        self.assertNotIn("loop", report["completed_steps"])
        self.run_fixture()
        _, checkpoint, _, _ = self.request("/api/checkpoint", {"step_id": "loop", "answers": {"executor": "application"}})
        self.assertTrue(checkpoint.get("checkpoint", checkpoint)["passed"])
        _, report, _, _ = self.request("/api/report")
        self.assertIn("loop", report["completed_steps"])

    def test_exercises_are_fixed_paths_and_run_real_checks(self):
        code, exercise, _, _ = self.request("/api/exercise?exercise=inventory")
        self.assertEqual(code, 200)
        self.assertIsInstance(exercise, dict)
        code, result, _, _ = self.request("/api/exercise", {"exercise": "inventory"})
        self.assertEqual(code, 200)
        evidence = result.get("result", result)
        self.assertIn("passed", evidence)
        self.assertTrue(evidence["checks"])
        for path, payload in (("/api/exercise?exercise=../../lab", None),
                              ("/api/exercise", {"exercise": "../../lab"})):
            code, _, _, _ = self.request(path, payload)
            self.assertIn(code, (400, 404))

    def test_cross_origin_writes_and_wrong_media_type_are_rejected(self):
        code, _, _, _ = self.request("/api/run", {"mode": "fixture"}, origin="https://unrelated.example")
        self.assertEqual(code, 403)
        code, _, _, _ = self.request("/api/run", {"mode": "fixture"}, content_type="text/plain")
        self.assertEqual(code, 415)
        _, report, _, _ = self.request("/api/report")
        self.assertEqual(report["runs"], [])

    def test_connection_checks_models_without_generating_and_disconnects(self):
        secret = "sk-ant-api03-fixture-test-secret-never-a-real-key"
        client = MagicMock()
        client.__enter__.return_value = client
        client.models.list.return_value = SimpleNamespace(data=[
            SimpleNamespace(id="claude-fixture-a", display_name="Fixture A"),
            SimpleNamespace(id="claude-fixture-b", display_name="Fixture B"),
        ])
        with patch.object(app.anthropic, "Anthropic", return_value=client) as constructor:
            code, connection, _, raw = self.request("/api/connect", {"api_key": secret, "model": "claude-fixture-a"})
        self.assertEqual(code, 200, raw)
        self.assertTrue(connection["connected"])
        self.assertEqual(connection["model"], "claude-fixture-a")
        self.assertTrue(connection["models"])
        self.assertNotIn(secret, raw)
        self.assertEqual(constructor.call_args.kwargs["api_key"], secret)
        client.models.list.assert_called_once()
        client.messages.create.assert_not_called()
        client.beta.messages.tool_runner.assert_not_called()
        _, config, _, raw = self.request("/api/config")
        self.assertTrue(config["connected"])
        self.assertTrue(config["live_available"])
        self.assertNotIn(secret, raw)
        _, _, _, raw = self.request("/api/report")
        self.assertNotIn(secret, raw)
        code, _, _, raw = self.request("/api/disconnect", {})
        self.assertEqual(code, 200)
        self.assertNotIn(secret, raw)
        _, config, _, _ = self.request("/api/config")
        self.assertFalse(config["connected"])
        self.assertFalse(config["live_available"])

    def test_unknown_requested_model_does_not_establish_connection(self):
        secret = "sk-ant-api03-fixture-rejected-model-secret"
        client = MagicMock()
        client.__enter__.return_value = client
        client.models.list.return_value = SimpleNamespace(data=[SimpleNamespace(id="claude-fixture-a", display_name="Fixture A")])
        with patch.object(app.anthropic, "Anthropic", return_value=client):
            code, _, _, raw = self.request("/api/connect", {"api_key": secret, "model": "claude-not-accessible"})
        self.assertIn(code, (400, 422))
        self.assertNotIn(secret, raw)
        _, config, _, _ = self.request("/api/config")
        self.assertFalse(config["connected"])


if __name__ == "__main__":
    unittest.main()
