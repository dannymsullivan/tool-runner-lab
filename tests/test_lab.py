"""Outcome checks using the real SDK runner with offline model fixtures."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import lab


class SupportAgentLabTests(unittest.TestCase):
    def run_fixture(self, scenario="normal", **overrides):
        events = []
        payload = {"mode": "fixture", "scenario": scenario, **overrides}
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}), patch(
            "socket.socket.connect",
            side_effect=AssertionError("Fixture mode must not open a network connection."),
        ):
            result = lab.run_lab(payload, events.append)
        self.assertTrue(events, "A run should expose its actual execution events.")
        self.assertTrue(result["checks"], "A run should evaluate its outcome.")
        return result

    def assert_checks_pass(self, result):
        failures = [check for check in result["checks"] if not check["passed"]]
        self.assertEqual([], failures)

    def test_configuration_offers_scenarios_and_tools_without_a_key(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            config = lab.get_config()
        self.assertTrue(config["scenarios"])
        self.assertGreaterEqual(len(config["tools"]), 3)
        self.assertFalse(config["live_available"])
        self.assertEqual([], config["initial_state"]["returns"])

    def test_success_persists_one_return(self):
        result = self.run_fixture()
        self.assertTrue(result["completed"])
        self.assertEqual(1, len(result["state"]["returns"]))
        self.assertGreaterEqual(result["metrics"]["tool_calls"], 3)
        self.assertGreaterEqual(result["metrics"]["assistant_messages"], 2)
        self.assert_checks_pass(result)

    def test_duplicate_request_does_not_create_duplicate_returns(self):
        result = self.run_fixture("duplicate")
        self.assertTrue(result["completed"])
        self.assertEqual(1, len(result["state"]["returns"]))
        self.assert_checks_pass(result)

    def test_read_only_and_refused_requests_do_not_mutate_orders(self):
        for scenario in ("policy", "unknown", "eligibility"):
            with self.subTest(scenario=scenario):
                result = self.run_fixture(scenario)
                self.assertEqual([], result["state"]["returns"])
                self.assertEqual(result["initial_state"]["orders"], result["state"]["orders"])
                self.assert_checks_pass(result)

    def test_success_message_without_database_write_fails_the_check(self):
        result = self.run_fixture(simulate_missing_write=True)
        self.assertEqual([], result["state"]["returns"])
        self.assertTrue(
            any(not check["passed"] for check in result["checks"]),
            "A claimed return is insufficient when the database has no return record.",
        )

    def test_available_replacement_stock_does_not_trigger_conditional_return(self):
        result = self.run_fixture(inventory_count=5)
        self.assertTrue(result["completed"])
        self.assertEqual([], result["state"]["returns"])
        self.assert_checks_pass(result)

    def test_inventory_outage_returns_unknown_not_zero_and_stops_retries(self):
        result = self.run_fixture("outage", inventory_count=5, error_threshold=2)
        self.assertFalse(result["completed"])
        self.assertEqual("error_threshold", result["stop_reason"])
        self.assertEqual(2, result["metrics"]["errors"])
        self.assertEqual([], result["state"]["returns"])
        inventory_results = [
            event["data"] for event in result["events"]
            if event["type"] == "tool_result" and event["data"]["name"] == "check_inventory"
        ]
        self.assertEqual(2, len(inventory_results))
        for record in inventory_results:
            self.assertTrue(record["is_error"])
            self.assertEqual("inventory_unavailable", record["result"]["error"])
            self.assertNotIn("count", record["result"])
        self.assertEqual(result["initial_state"]["inventory"], result["state"]["inventory"])
        self.assert_checks_pass(result)

    def test_error_budget_stops_after_the_current_tool_batch(self):
        result = self.run_fixture("batch_errors", error_threshold=2)
        self.assertFalse(result["completed"])
        self.assertEqual("error_threshold", result["stop_reason"])
        self.assertEqual(3, result["metrics"]["errors"])
        self.assertEqual(1, result["metrics"]["assistant_messages"])
        self.assertEqual([], result["state"]["returns"])
        self.assert_checks_pass(result)

    def test_repeated_runs_have_isolated_database_state(self):
        first = self.run_fixture()
        second = self.run_fixture()
        self.assertEqual([], first["initial_state"]["returns"])
        self.assertEqual([], second["initial_state"]["returns"])
        self.assertEqual(1, len(first["state"]["returns"]))
        self.assertEqual(1, len(second["state"]["returns"]))
        self.assert_checks_pass(second)


if __name__ == "__main__":
    unittest.main()
