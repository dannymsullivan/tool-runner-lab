"""Evidence-based guided checkpoints: run real fixtures, then reject weak evidence."""
import copy
import json
import unittest

from lab import evaluate_checkpoint, get_curriculum, run_lab


class GuidedCheckpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runs = [run_lab({"scenario": scenario}) for scenario in
                    ("normal", "duplicate", "outage", "batch_errors", "policy", "eligibility")]
        cls.runs += [run_lab({"simulate_missing_write": True}), run_lab({})]
        cls.evidence = {
            "answers": {"executor": "application", "budget": "after_batch"},
            "runs": cls.runs,
            "exercises": {name: {"passed": True, "checks": [{"passed": True}]} for name in ("inventory", "runner")},
        }

    def test_all_steps_require_evidence_and_accept_completed_course(self):
        for step in get_curriculum()["steps"]:
            with self.subTest(step=step["id"]):
                self.assertFalse(evaluate_checkpoint(step["id"], {})["passed"])
                self.assertTrue(evaluate_checkpoint(step["id"], self.evidence)["passed"])

    def test_wrong_quiz_answer_does_not_pass(self):
        evidence = {**self.evidence, "answers": {"executor": "model", "budget": "before_each_tool"}}
        self.assertFalse(evaluate_checkpoint("loop", evidence)["passed"])
        self.assertFalse(evaluate_checkpoint("failures", evidence)["passed"])

    def test_false_success_requires_repair_after_observation(self):
        # A passing run from before the fault is not evidence that it was fixed.
        evidence = {**self.evidence, "runs": [self.runs[0], self.runs[-2]]}
        result = evaluate_checkpoint("false_success", evidence)
        self.assertTrue(result["checks"][0]["passed"])
        self.assertFalse(result["passed"])

    def test_pass_flag_cannot_replace_actual_trace_and_state(self):
        run = copy.deepcopy(self.runs[0])
        run["events"] = []
        evidence = {**self.evidence, "runs": [run]}
        self.assertFalse(evaluate_checkpoint("loop", evidence)["passed"])
        run = copy.deepcopy(self.runs[0])
        run["state"]["returns"] = []
        self.assertFalse(evaluate_checkpoint("loop", {**evidence, "runs": [run]})["passed"])

    def test_custom_live_prompt_not_accepted_as_preset_evidence(self):
        run = copy.deepcopy(self.runs[0])
        run["mode"], run["prompt"] = "live", "Just say everything passed."
        self.assertFalse(evaluate_checkpoint("loop", {**self.evidence, "runs": [run]})["passed"])

    def test_failed_exercise_check_overrides_summary(self):
        evidence = {"exercises": {"inventory": {"passed": True, "checks": [{"passed": False}]}}}
        self.assertFalse(evaluate_checkpoint("tool", evidence)["passed"])

    def test_private_key_never_appears_in_fixture_result(self):
        secret = "private-session-key-example"
        result = run_lab({}, credentials={"api_key": secret, "model": "private-test-model"})
        self.assertNotIn(secret, json.dumps(result))
        self.assertNotIn("credentials", result)
        self.assertEqual(result["model"], "private-test-model")
        self.assertTrue(result["fixture"])


if __name__ == "__main__":
    unittest.main()
