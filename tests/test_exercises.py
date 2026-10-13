"""Starter failures are intentional; the reference answers must pass offline."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ExerciseTests(unittest.TestCase):
    def check_exercise(self, exercise, reference=False):
        command = [sys.executable, str(ROOT / "exercises/check.py"), "--exercise", exercise, "--json"]
        if reference:
            command.append("--reference")
        env = {name: value for name, value in os.environ.items()
               if name in {"PATH", "SYSTEMROOT", "TMPDIR", "LANG"}}
        run = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=10, check=True)
        self.assertEqual("", run.stderr)
        return json.loads(run.stdout)

    def test_current_work_is_complete_or_explains_the_unfinished_starter(self):
        for exercise in ("inventory", "runner"):
            with self.subTest(exercise=exercise):
                result = self.check_exercise(exercise)
                self.assertTrue(result["feedback"])
                filename = "inventory_tool.py" if exercise == "inventory" else "runner_exercise.py"
                unfinished = "raise NotImplementedError(" in (ROOT / "exercises" / filename).read_text()
                if unfinished:
                    self.assertFalse(result["passed"])
                    self.assertTrue(any(not check["passed"] for check in result["checks"]))
                else:
                    self.assertTrue(result["passed"], result["checks"])

    def test_reference_answers_pass_without_changing_starters(self):
        paths = [ROOT / "exercises/inventory_tool.py", ROOT / "exercises/runner_exercise.py"]
        before = [path.read_bytes() for path in paths]
        for exercise in ("inventory", "runner"):
            with self.subTest(exercise=exercise):
                result = self.check_exercise(exercise, reference=True)
                self.assertTrue(result["passed"], result["checks"])
        self.assertEqual(before, [path.read_bytes() for path in paths])


if __name__ == "__main__":
    unittest.main()
