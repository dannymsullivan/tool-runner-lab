"""Check locally edited exercises with the real SDK and offline model responses.

This is a teaching checker, not a sandbox for untrusted Python code.
"""

import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import patch

import anthropic
import httpx2 as httpx
from anthropic.lib.tools import BetaToolRunner

HERE = Path(__file__).resolve().parent


def load_exercise(exercise, reference=False):
    filename = "inventory_tool.py" if exercise == "inventory" else "runner_exercise.py"
    path = HERE / "solutions" / filename if reference else HERE / filename
    spec = importlib.util.spec_from_file_location("learner_" + exercise, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_inventory(module, add):
    tool = module.check_inventory
    schema = tool.to_dict()
    inputs = schema.get("input_schema", {})
    product = inputs.get("properties", {}).get("product", {})
    add("Typed tool schema", schema.get("name") == "check_inventory" and product.get("type") == "string"
        and "product" in inputs.get("required", []),
        "Keep @beta_tool and the product: str annotation so the SDK generates a required string argument.")
    descriptions = (schema.get("description", ""), product.get("description", ""))
    add("Useful tool descriptions", all(text.strip() and "TODO" not in text for text in descriptions),
        "Replace both TODO descriptions: explain the return value and the accepted product names.")
    cases = [
        ("Zero is valid stock", "blue-hoodie", {"product": "blue-hoodie", "count": 0, "in_stock": False},
         "Use membership in INVENTORY, not a truthiness check: zero is a known stock count."),
        ("Normalize product names", "  BLUE   Hoodie  ", {"product": "blue-hoodie", "count": 0, "in_stock": False},
         "Strip whitespace, lowercase the name, then join its words with hyphens."),
        ("Available stock", "trail-shoes", {"product": "trail-shoes", "count": 12, "in_stock": True},
         "Return JSON with product, count, and a boolean in_stock computed from count > 0."),
        ("Unknown stays unknown", "missing-product", {"error": "unknown_product", "product": "missing-product"},
         "Return an unknown_product JSON error instead of treating a missing product as zero stock."),
    ]
    for name, value, expected, hint in cases:
        try:
            output = tool.call({"product": value})
            actual = json.loads(output) if isinstance(output, str) else None
            passed = actual == expected
            if passed and "count" in expected:
                passed = type(actual["count"]) is int and type(actual["in_stock"]) is bool
        except Exception:
            passed = False
        add(name, passed, hint)
    return schema


def check_runner(module, add):
    calls, requests = [], []

    @anthropic.beta_tool
    def reference_inventory(product: str) -> str:
        """Read stock from the checker's tiny fixture.

        Args:
            product: Product slug to look up.
        """
        calls.append(product)
        return json.dumps({"product": product, "count": 0})

    def respond(request):
        requests.append(json.loads(request.content))
        first = len(requests) == 1
        content = ([{"type": "tool_use", "id": "toolu_exercise_1", "name": "reference_inventory",
                     "input": {"product": "blue-hoodie"}}] if first else
                   [{"type": "text", "text": "The blue hoodie has zero replacement stock."}])
        return httpx.Response(200, json={
            "id": "msg_exercise_" + str(len(requests)), "type": "message", "role": "assistant",
            "model": "fixture-model", "content": content,
            "stop_reason": "tool_use" if first else "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        })

    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        with anthropic.Anthropic(api_key="fixture-only", base_url="https://fixture.invalid",
                                 http_client=transport, max_retries=0) as client:
            tools = [reference_inventory]
            question = "Check stock for blue-hoodie."
            with patch.object(client.beta.messages, "tool_runner", wraps=client.beta.messages.tool_runner) as spy:
                runner = module.build_runner(client, "fixture-model", tools, question)
            valid = isinstance(runner, BetaToolRunner)
            add("Actual SDK runner", valid, "Return client.beta.messages.tool_runner(...), not a list or a final response.")
            kwargs = spy.call_args.kwargs if spy.call_args else {}
            add("Bounded runner configuration", kwargs.get("max_tokens") == 512 and kwargs.get("max_iterations") == 4,
                "Set max_tokens=512 and max_iterations=4 when constructing the runner.")
            add("Forward the task and tools", kwargs.get("model") == "fixture-model" and kwargs.get("tools") is tools
                and kwargs.get("messages") == [{"role": "user", "content": question}],
                "Pass the supplied model and tools, plus one user message containing question.")
            messages = list(runner) if valid else []
    add("Tool executes exactly once", calls == ["blue-hoodie"],
        "Let the SDK runner dispatch the supplied tool; do not execute it separately.")
    results = [block for request in requests[1:] for message in request.get("messages", [])
               if isinstance(message.get("content"), list) for block in message["content"]
               if block.get("type") == "tool_result"]
    add("Matching tool result reaches next turn", len(requests) == 2 and len(results) == 1
        and results[0].get("tool_use_id") == "toolu_exercise_1" and not results[0].get("is_error", False),
        "The runner must feed the executed tool result back with the original tool-use ID.")
    add("Complete final response", len(messages) == 2 and messages[-1].stop_reason == "end_turn"
        and any(block.type == "text" and block.text.strip() for block in messages[-1].content),
        "Return a runner the caller can iterate until a complete final response arrives.")


def run_checks(exercise, reference=False):
    checks = []
    result = {"passed": False, "checks": checks, "feedback": ""}

    def add(name, passed, detail):
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    # Suppress incidental learner prints; stdout is a clean JSON response for the UI.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), patch(
        "socket.socket.connect", side_effect=RuntimeError("Network disabled for exercise checks")
    ):
        try:
            module = load_exercise(exercise, reference)
            if exercise == "inventory":
                result["schema"] = check_inventory(module, add)
            else:
                check_runner(module, add)
        except NotImplementedError:
            add("Complete the starter", False, "Replace the TODO and NotImplementedError in exercises/" +
                ("inventory_tool.py" if exercise == "inventory" else "runner_exercise.py") + ", save, then check again.")
        except Exception as exc:
            add("Exercise runs", False, f"The exercise raised {type(exc).__name__}. Check its syntax, imports, and required function signature.")
    result["passed"] = bool(checks) and all(check["passed"] for check in checks)
    result["feedback"] = ("All checks passed. Explain the tool/result sequence, then continue to the next lesson."
                          if result["passed"] else next(check["detail"] for check in checks if not check["passed"]))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exercise", choices=("inventory", "runner"), required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--reference", action="store_true", help="Check the reference answer; does not change your starter.")
    args = parser.parse_args()
    result = run_checks(args.exercise, args.reference)
    if args.json:
        print(json.dumps(result))
    else:
        for check in result["checks"]:
            print(("PASS" if check["passed"] else "TRY AGAIN") + ": " + check["name"])
        print(result["feedback"])
    # A failed learning check is a valid response, not a crashed subprocess.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
