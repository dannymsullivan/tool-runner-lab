"""Incomplete model output must never authorize execution or report completion."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lab


class StopConditionsTests(unittest.TestCase):
    def response(self, reason, content):
        return {
            "id": "msg_incomplete", "type": "message", "role": "assistant",
            "model": lab.DEFAULT_MODEL, "content": content,
            "stop_reason": reason, "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }

    def test_truncated_tool_request_cannot_write(self):
        response = self.response("max_tokens", [{
            "type": "tool_use", "id": "toolu_incomplete", "name": "start_return",
            "input": {"order_id": "A100", "reason": "Incomplete generation"},
        }])
        with patch.object(lab, "_fixture_response", return_value=response):
            result = lab.run_lab({"mode": "fixture", "scenario": "normal"})
        self.assertFalse(result["completed"])
        self.assertEqual(0, result["metrics"]["tool_calls"])
        self.assertEqual([], result["state"]["returns"])

    def test_empty_final_message_is_not_a_completed_task(self):
        response = self.response("end_turn", [{"type": "text", "text": " "}])
        with patch.object(lab, "_fixture_response", return_value=response):
            result = lab.run_lab({"mode": "fixture", "scenario": "normal"})
        self.assertFalse(result["completed"])

    def test_iteration_limit_preserves_evidence_without_claiming_completion(self):
        result = lab.run_lab({"mode": "fixture", "scenario": "normal", "max_iterations": 2})
        self.assertFalse(result["completed"])
        self.assertEqual("iteration_limit", result["stop_reason"])
        self.assertEqual(1, len(result["state"]["returns"]))
        self.assertTrue(any(not check["passed"] for check in result["checks"]))


if __name__ == "__main__":
    unittest.main()
