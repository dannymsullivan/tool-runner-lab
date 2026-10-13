"""A correct final record must not hide a write made before its observations."""
import unittest
from unittest.mock import patch

import lab


class VerificationOrderTests(unittest.TestCase):
    def run_script(self, batches):
        def response(scenario, turn, body, model):
            calls = batches[turn] if turn < len(batches) else []
            content = [{"type": "tool_use", "id": f"toolu_order_{turn}_{index}", "name": name, "input": args}
                       for index, (name, args) in enumerate(calls)]
            if not content:
                content = [{"type": "text", "text": "The return is recorded."}]
            return {"id": f"msg_order_{turn}", "type": "message", "role": "assistant", "model": model,
                    "content": content, "stop_reason": "tool_use" if calls else "end_turn", "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0}}
        with patch.object(lab, "_fixture_response", side_effect=response):
            return lab.run_lab({"scenario": "normal"})

    def test_late_evidence_does_not_justify_an_earlier_write(self):
        result = self.run_script([
            [("start_return", {"order_id": "A100", "reason": "Delivery too late"})],
            [("lookup_order", {"order_id": "A100"}), ("check_inventory", {"product": "Blue hoodie (M)"})],
        ])
        self.assertTrue(result["completed"])
        self.assertEqual(["A100"], [row["order_id"] for row in result["state"]["returns"]])
        check = next(check for check in result["checks"] if check["name"] == "Order and stock inspected")
        self.assertFalse(check["passed"])

    def test_same_batch_lookup_is_not_a_model_observation_before_write(self):
        result = self.run_script([[
            ("lookup_order", {"order_id": "A100"}),
            ("check_inventory", {"product": "Blue hoodie (M)"}),
            ("start_return", {"order_id": "A100", "reason": "Delivery too late"}),
        ]])
        self.assertTrue(result["completed"])
        check = next(check for check in result["checks"] if check["name"] == "Order and stock inspected")
        self.assertFalse(check["passed"])


if __name__ == "__main__":
    unittest.main()
