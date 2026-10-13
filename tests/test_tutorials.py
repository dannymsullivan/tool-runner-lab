"""Self-paced artifact acceptance tests: edits power real SDK tool execution."""
import contextlib
import io
import json
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

import lab
import tutorial_runtime as runtime
import tutorial_series as series


def namespace(module, reference=True):
    result = dict(vars(runtime))
    exec(runtime.RUNNER_SOLUTION, result)
    exec(runtime.SOLUTIONS[module] if reference else runtime.STARTERS[module], result)
    return result


def execute_notebook(notebook, *, skip_roles=()):
    result = {"__name__": "__main__"}
    for cell in notebook["cells"]:
        if cell["cell_type"] != "code" or cell["metadata"]["lab_role"] in skip_roles:
            continue
        source = "\n".join(line for line in "".join(cell["source"]).splitlines() if not line.startswith("%pip"))
        exec(compile(source, "<standalone-colab-cell>", "exec"), result)
    return result


class TutorialTests(unittest.TestCase):
    def test_each_starter_fails_with_actionable_feedback_and_reference_passes_without_network(self):
        with patch("socket.socket.connect", side_effect=AssertionError("Practice must not use network")):
            for module in runtime.EXERCISES:
                with self.subTest(module=module):
                    starter = runtime.check_exercise(module, namespace(module, False), quiet=True)
                    self.assertFalse(starter["passed"], starter)
                    self.assertIn("Edit and rerun", starter["message"])
                    reference = runtime.check_exercise(module, namespace(module), quiet=True)
                    self.assertTrue(reference["passed"], reference)
                    self.assertGreater(len(reference["checks"]), 2)

    def test_learner_inventory_tool_is_dispatched_on_each_fresh_run(self):
        ns = namespace("build-tools")
        calls = []
        def spy(session):
            @runtime.beta_tool
            def check_inventory(product: str) -> str:
                """Read the actual store while recording SDK dispatch."""
                calls.append(session.store)
                return json.dumps(session.store.check_inventory(product))
            return check_inventory
        ns["make_inventory_tool"] = spy
        for stock in (0, 3):
            result = runtime.run_bot(ns, "build-tools", stock=stock)
            self.assertEqual(len(result["saved"]), 1 if stock == 0 else 0)
        self.assertEqual(len(calls), 2)
        self.assertIsNot(calls[0], calls[1])

    def test_changed_student_adapter_changes_bot_action_without_rebuilding_interface(self):
        ns = namespace("make-it-yours")
        calls = []
        def available_stock(store, product, reserved):
            calls.append((product, reserved))
            return {"product": "Blue hoodie (M)", "count": 2}
        ns["available_stock"] = available_stock
        first = runtime.run_bot(ns, "make-it-yours", stock=0)
        self.assertEqual(first["saved"], [])
        def revised(store, product, reserved):
            calls.append((product, reserved))
            return {"product": "Blue hoodie (M)", "count": 0}
        ns["available_stock"] = revised
        second = runtime.run_bot(ns, "make-it-yours", stock=3)
        self.assertEqual([r["order_id"] for r in second["saved"]], ["A100"])
        self.assertEqual(len(calls), 2)

    def test_completed_reply_does_not_pass_missing_write_or_wrong_order(self):
        ns = namespace("verify-actions")
        broken = runtime.run_bot(ns, "verify-actions", fault="missing-write")
        self.assertEqual(broken["stop_reason"], "end_turn")
        self.assertIn("R-A100", broken["reply"])
        self.assertFalse(broken["verified"])
        self.assertEqual(broken["saved"], [])
        outage = runtime.run_bot(ns, "verify-actions", fault="outage")
        self.assertEqual(outage["claims"], [])
        self.assertTrue(any(event["is_error"] for event in outage["events"]))
        self.assertFalse(ns["verify_return"]({"return_id":"R-A100","order_id":"A100"},
                                             [{"return_id":"R-A100","order_id":"A101"}]))

    def test_reference_loading_is_explicit_and_provenance_clears_when_user_redefines(self):
        ns = namespace("why-tool-runner", False)
        original = ns["build_runner"]
        runtime.check_exercise("why-tool-runner", ns, quiet=True)
        self.assertIs(ns["build_runner"], original)
        with contextlib.redirect_stdout(io.StringIO()):
            runtime.load_reference("why-tool-runner", ns)
        self.assertIn("loaded reference", runtime.origin("why-tool-runner", ns))
        exec(runtime.RUNNER_SOLUTION, ns)
        self.assertIn("current notebook code", runtime.origin("why-tool-runner", ns))

    def test_feedback_survives_missing_noncallable_and_callable_object_definitions(self):
        class UnfinishedCallable:
            def __call__(self, *args, **kwargs):
                raise NotImplementedError("Finish the exercise before checking it")
        for module, name in runtime.EXERCISES.items():
            for replacement in (None, 42, UnfinishedCallable()):
                with self.subTest(module=module, replacement=type(replacement).__name__):
                    ns = namespace(module)
                    if replacement is None:
                        del ns[name]
                    else:
                        ns[name] = replacement
                    report = runtime.check_exercise(module, ns, quiet=True)
                    self.assertFalse(report["passed"])
                    self.assertIn("Edit and rerun", report["message"])
                    self.assertIn(name, report["implementation"])

    def test_cleanup_occurs_on_success_and_learner_failure(self):
        captured = []
        original = runtime.SupportSession
        def session(*args, **kwargs):
            value = original(*args, **kwargs)
            captured.append(value)
            return value
        with patch.object(runtime, "SupportSession", session):
            runtime.run_bot(namespace("why-tool-runner"), "why-tool-runner")
            with self.assertRaises(NotImplementedError):
                runtime.run_bot(namespace("why-tool-runner", False), "why-tool-runner")
        for item in captured:
            self.assertTrue(item.http.is_closed)
            with self.assertRaises(sqlite3.ProgrammingError):
                item.store.snapshot()

    def test_all_saved_notebooks_are_current_valid_standalone_and_reference_cells_execute(self):
        import nbformat
        root = Path(series.__file__).parent
        with patch("socket.socket.connect", side_effect=AssertionError("No model calls in practice")):
            for module in series.MODULES:
                for reference in (False, True):
                    with self.subTest(module=module["id"], reference=reference):
                        saved = json.loads((root / module["reference" if reference else "notebook"].lstrip("/")).read_text())
                        self.assertEqual(saved, series.build_notebook(module["id"], reference=reference))
                        nbformat.validate(saved)
                        self.assertTrue(all(not cell.get("outputs") for cell in saved["cells"]))
                        with contextlib.redirect_stdout(io.StringIO()):
                            ns = execute_notebook(saved, skip_roles={"interface"})
                        self.assertIs(ns["checkpoint"]["passed"], reference)
                        self.assertNotIn("api_key", ns)
                        if module["id"] == "make-it-yours":
                            self.assertFalse(ns["RUN_LIVE"])

    def test_widget_callback_uses_latest_definition_and_renders_actual_result(self):
        ns = namespace("make-it-yours")
        with patch("IPython.display.display"), contextlib.redirect_stdout(io.StringIO()):
            button = runtime.show_bot(ns, "make-it-yours")
        with patch.object(runtime, "render_result") as render, contextlib.redirect_stdout(io.StringIO()):
            button.click()
            self.assertEqual(render.call_args.args[0]["saved"], [])
            ns["available_stock"] = lambda store, product, reserved: {"product": product, "count": 0}
            button.click()
            self.assertEqual(len(render.call_args.args[0]["saved"]), 1)
        self.assertFalse(button.disabled)
        button.close()

    def test_live_key_is_opt_in_hidden_and_errors_do_not_echo_secret(self):
        ns = namespace("make-it-yours")
        with patch("getpass.getpass", return_value=""), patch.object(runtime.anthropic, "Anthropic") as client:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertIsNone(runtime.run_live(ns, model="chosen-model"))
            client.assert_not_called()
        with patch("getpass.getpass", return_value="private-key"), patch.object(runtime.anthropic, "Anthropic", side_effect=RuntimeError("private-key")):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                runtime.run_live(ns, model="chosen-model")
            self.assertNotIn("private-key", output.getvalue())
        self.assertEqual(ns["MODEL"], runtime.MODEL)


if __name__ == "__main__":
    unittest.main()
