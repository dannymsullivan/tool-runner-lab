"""Executable notebook and HTTP checks. All SDK calls use offline fixtures."""
import json
import sqlite3
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import app
import lab
import notebook_lab


class NotebookTests(unittest.TestCase):
    def test_cells_use_real_sdk_without_network_and_verify_saved_outcomes(self):
        calls = []
        original = lab.Store.start_return

        def record_write(store, order_id, reason):
            calls.append(order_id)
            return original(store, order_id, reason)

        with patch("socket.socket.connect", side_effect=AssertionError("No network in a practice cell")), patch.object(lab.Store, "start_return", record_write):
            result = notebook_lab.run_cell("verify")
        self.assertTrue(result["passed"])
        self.assertIn("1 saved return", result["output"])
        self.assertEqual(["A100"], calls)

    def test_first_two_cells_show_actual_data_and_generated_schema(self):
        inventory = json.loads(notebook_lab.run_cell("inventory")["output"])
        schema = json.loads(notebook_lab.run_cell("schema")["output"])
        self.assertEqual(inventory["count"], 0)
        self.assertEqual(schema["required"], ["product"])
        self.assertEqual(schema["properties"]["product"]["type"], "string")

    def test_compare_and_rerun_are_isolated(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(notebook_lab.run_cell, ["verify", "inventory", "compare", "verify"]))
        self.assertEqual(results[0]["output"], results[3]["output"])
        self.assertEqual(json.loads(results[1]["output"])["count"], 0)
        self.assertIn("3 replacement hoodies", results[2]["output"])

    def test_resources_close_on_success_and_failure(self):
        sessions = []
        original = notebook_lab.SupportSession

        def capture(stock=0):
            session = original(stock)
            sessions.append(session)
            return session

        with patch.object(notebook_lab, "SupportSession", capture):
            notebook_lab.run_cell("verify")
            with patch.object(lab.Store, "check_inventory", side_effect=RuntimeError("test failure")):
                with self.assertRaises(RuntimeError):
                    notebook_lab.run_cell("inventory")
        self.assertEqual(len(sessions), 3)
        for session in sessions:
            self.assertTrue(session.http.is_closed)
            with self.assertRaises(sqlite3.ProgrammingError):
                session.store.snapshot()

    def test_unknown_ids_are_rejected_before_execution(self):
        for cell_id in (None, [], "__import__('os')", "../../lab.py"):
            with self.subTest(cell_id=cell_id), self.assertRaises(ValueError):
                notebook_lab.run_cell(cell_id)

    def test_trace_dispatches_real_tools_once_and_stock_changes_path(self):
        calls = []
        patches = []
        for name in ("lookup_order", "check_inventory", "start_return"):
            original = getattr(lab.Store, name)

            def record(store, *args, name=name, original=original, **kwargs):
                calls.append(name)
                return original(store, *args, **kwargs)

            patches.append(patch.object(lab.Store, name, record))
        with patches[0], patches[1], patches[2], patch("socket.socket.connect", side_effect=AssertionError("Offline experiment")):
            empty = notebook_lab.run_cell("trace", "empty")
            # start_return also looks up its order while enforcing store policy.
            self.assertEqual(calls, ["lookup_order", "check_inventory", "start_return", "lookup_order"])
            calls.clear()
            available = notebook_lab.run_cell("trace", "available")
            self.assertEqual(calls, ["lookup_order", "check_inventory"])
        self.assertIn("Turn 3: end_turn", empty["output"])
        self.assertIn("Saved returns: 1", empty["output"])
        self.assertIn("Turn 2: end_turn", available["output"])
        self.assertNotIn("start_return →", available["output"])
        self.assertIn("Saved returns: 0", available["output"])

    def test_manual_and_runner_use_same_tools_and_matching_result_history(self):
        sessions = []
        original = notebook_lab.SupportSession

        def capture(*args, **kwargs):
            session = original(*args, **kwargs)
            sessions.append(session)
            return session

        with patch.object(notebook_lab, "SupportSession", capture), patch("socket.socket.connect", side_effect=AssertionError("Offline comparison")):
            manual = notebook_lab.run_cell("why", "manual")
            runner = notebook_lab.run_cell("why", "runner")
        self.assertEqual(manual["output"], runner["output"])
        self.assertIn("Model turns: 3 | Saved returns: 1", manual["output"])
        for session in sessions:
            self.assertEqual(session.calls, ["lookup_order", "check_inventory", "start_return"])
            self.assertEqual(len(session.requests), 3)
            for request, names in zip(session.requests[1:], (["lookup_order", "check_inventory"], ["start_return"])):
                assistant, results = request["messages"][-2:]
                self.assertEqual((assistant["role"], results["role"]), ("assistant", "user"))
                self.assertEqual([block["name"] for block in assistant["content"]], names)
                self.assertEqual([block["id"] for block in assistant["content"]],
                                 [block["tool_use_id"] for block in results["content"]])
                self.assertTrue(all(block["type"] == "tool_result" and not block.get("is_error")
                                    for block in results["content"]))
            self.assertTrue(session.http.is_closed)

    def test_manual_loop_reports_real_tool_error_instead_of_success(self):
        session = notebook_lab.SupportSession(inventory_outage=True)
        namespace = {"support_session": lambda **options: session, "ToolError": lab.ToolError,
                     "MODEL": notebook_lab.MODEL, "CUSTOMER_REQUEST": notebook_lab.CUSTOMER_REQUEST}
        source = notebook_lab.MANUAL_CODE.replace('== ["A100"]', '== []')
        try:
            output = notebook_lab.execute_source(source, namespace)
            results = session.requests[1]["messages"][-1]["content"]
            order, inventory = results
            self.assertNotIn("is_error", order)
            self.assertTrue(inventory["is_error"])
            self.assertEqual(json.loads(inventory["content"])["error"], "inventory_unavailable")
            self.assertNotIn("count", json.loads(inventory["content"]))
            self.assertIn("Saved returns: 0", output)
            self.assertEqual(session.calls, ["lookup_order", "check_inventory"])
        finally:
            session.close()

    def test_manual_loop_does_not_claim_nonfinal_stop_is_complete(self):
        original = lab._fixture_response

        def truncated(*args, **kwargs):
            reply = original(*args, **kwargs)
            reply["stop_reason"] = "max_tokens"
            return reply

        with patch.object(lab, "_fixture_response", truncated), self.assertRaisesRegex(RuntimeError, "stopped without completing"):
            notebook_lab.run_cell("why", "manual")

    def test_iteration_cap_does_not_undo_completed_tool_writes(self):
        for variant, stop_reason in (("capped", "tool_use"), ("complete", "end_turn")):
            with self.subTest(variant=variant):
                result = notebook_lab.run_cell("limits", variant)
                self.assertTrue(result["passed"])
                self.assertIn(f"Last stop reason: {stop_reason}", result["output"])
                self.assertIn("Saved returns: 1", result["output"])
        self.assertIn("Stopped before a final answer", notebook_lab.run_cell("limits", "capped")["output"])

    def test_tool_error_is_a_real_sdk_error_result_and_not_a_zero(self):
        failures = []
        original = lab.Store.check_inventory

        def lookup(store, product):
            try:
                return original(store, product)
            except lab.ToolError as error:
                failures.append(error)
                raise

        with patch.object(lab.Store, "check_inventory", lookup):
            unavailable = notebook_lab.run_cell("errors", "unavailable")
            restored = notebook_lab.run_cell("errors", "restored")
        self.assertEqual(len(failures), 1)
        self.assertIn("is_error: True", unavailable["output"])
        self.assertIn("Stock is unknown", unavailable["output"])
        self.assertIn("Saved returns: 0", unavailable["output"])
        self.assertNotIn('"count": 0', unavailable["output"])
        self.assertIn("is_error: False", restored["output"])
        self.assertIn('"count": 0', restored["output"])
        self.assertIn("Saved returns: 1", restored["output"])

    def test_missing_write_is_detected_then_fixed_against_actual_database(self):
        missing = notebook_lab.run_cell("persistence", "missing")
        saved = notebook_lab.run_cell("persistence", "saved")
        self.assertIn("Bug caught: reply claims R-A100, database has 0 returns.", missing["output"])
        self.assertIn("Verified: the claimed return is saved.", saved["output"])
        self.assertIn("Saved returns: 1", saved["output"])
        self.assertTrue(missing["passed"])  # The experiment detected the deliberately injected bug.

    def test_variant_whitelist_prevents_execution_and_cross_cell_choices(self):
        for cell_id, variant in (("trace", None), ("trace", []), ("trace", "saved"),
                                 ("errors", "__import__('os')"), ("inventory", "empty")):
            with self.subTest(cell_id=cell_id, variant=variant), self.assertRaises(ValueError), patch.object(notebook_lab, "execute_source", side_effect=AssertionError("Must not execute")):
                notebook_lab.run_cell(cell_id, variant)

    def test_notebook_is_standalone_and_executes_same_code(self):
        saved = json.loads(Path(notebook_lab.__file__).with_name("tool-runner-lab.ipynb").read_text())
        self.assertEqual(saved, notebook_lab.build_notebook())
        lessons = [cell for cell in saved["cells"] if cell["metadata"].get("lab_cell_id") and not cell["metadata"].get("lab_variant")]
        self.assertEqual(["".join(cell["source"]) for cell in lessons], [cell["code"] for cell in notebook_lab.CELLS])
        experiments = [cell for cell in saved["cells"] if cell["metadata"].get("lab_variant")]
        self.assertEqual(["".join(cell["source"]) for cell in experiments],
                         [variant["code"] for cell in notebook_lab.DEEP_CELLS for variant in cell["variants"]])
        namespace = {}
        with patch("socket.socket.connect", side_effect=AssertionError("No network after dependency installation")):
            try:
                for cell in saved["cells"]:
                    if cell["cell_type"] != "code":
                        continue
                    source = "".join(cell["source"])
                    # Dependencies are already installed in the test runtime.
                    source = "\n".join(line for line in source.splitlines() if not line.startswith("%pip "))
                    output = notebook_lab.execute_source(source, namespace)
                    if cell["metadata"].get("lab_cell_id") == "verify":
                        self.assertIn("Checks passed", output)
                        self.assertEqual(namespace["baseline"].calls, ["lookup_order", "check_inventory", "start_return"])
                        self.assertEqual(namespace["comparison"].calls, ["lookup_order", "check_inventory"])
            finally:
                if "resources" in namespace:
                    namespace["resources"].close()


class NotebookServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join(timeout=3)

    def request(self, path, payload=None, origin=None):
        headers = {"Content-Type": "application/json", "Origin": origin or self.base}
        request = Request(self.base + path, headers=headers,
                          data=json.dumps(payload).encode() if payload is not None else None)
        try:
            response = build_opener(ProxyHandler({})).open(request, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read()), response.headers

    def test_metadata_execution_and_download(self):
        status, metadata, _ = self.request("/api/notebook")
        self.assertEqual(status, 200)
        self.assertEqual(metadata, notebook_lab.curriculum())
        status, result, _ = self.request("/api/notebook-cell", {"cell_id": "verify"})
        self.assertEqual(status, 200)
        self.assertTrue(result["passed"])
        status, notebook, headers = self.request("/tool-runner-lab.ipynb")
        self.assertEqual(status, 200)
        self.assertEqual(notebook["nbformat"], 4)
        self.assertIn("application/x-ipynb+json", headers["Content-Type"])
        self.assertIn('attachment; filename="tool-runner-lab.ipynb"', headers["Content-Disposition"])

    def test_extra_code_and_cross_origin_requests_are_rejected(self):
        for payload in ({"cell_id": "verify", "code": "print('not allowed')"}, {"cell_id": "bad"}, {},
                        {"cell_id": "trace"}, {"cell_id": "trace", "variant": None},
                        {"cell_id": "trace", "variant": []}, {"cell_id": "trace", "variant": "saved"},
                        {"cell_id": "trace", "variant": "empty", "code": "print('no')"},
                        {"cell_id": "inventory", "variant": None}, {"cell_id": {}}):
            status, _, _ = self.request("/api/notebook-cell", payload)
            self.assertEqual(status, 400)
        status, _, _ = self.request("/api/notebook-cell", {"cell_id": "inventory"}, origin="https://unrelated.example")
        self.assertEqual(status, 403)

    def test_deep_metadata_and_each_experiment_variant(self):
        status, metadata, _ = self.request("/api/notebook")
        self.assertEqual(status, 200)
        self.assertEqual(len(metadata["deep_cells"]), 5)
        self.assertEqual(metadata["deep_cells"][0]["id"], "why")
        for cell in metadata["deep_cells"]:
            self.assertEqual(len(cell["variants"]), 2)
            for variant in cell["variants"]:
                with self.subTest(cell=cell["id"], variant=variant["id"]):
                    status, result, _ = self.request("/api/notebook-cell", {"cell_id": cell["id"], "variant": variant["id"]})
                    self.assertEqual(status, 200)
                    self.assertEqual(set(result), {"cell_id", "variant", "output", "takeaway", "passed"})
                    self.assertEqual(result["variant"], variant["id"])
                    self.assertTrue(result["passed"])
                    self.assertTrue(result["output"])


if __name__ == "__main__":
    unittest.main()
