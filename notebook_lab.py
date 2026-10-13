"""Small, executable notebook lessons shared by the browser and Google Colab.

Only this module's fixed cells are executable through HTTP. Model messages are
scripted locally; the SDK dispatches the real Python tools against real SQLite.
"""
from __future__ import annotations

import ast
import contextlib
import inspect
import io
import json
from pathlib import Path

import anthropic
import httpx2 as httpx
from anthropic import beta_tool
from anthropic.lib.tools import ToolError

import lab

MODEL = lab.DEFAULT_MODEL
CUSTOMER_REQUEST = lab.SCENARIO_BY_ID["normal"]["prompt"]
NOTICE = "Scripted model replies. Real Tool Runner and Python tools. No API key or model charge."

CELLS = [
    {
        "id": "inventory",
        "title": "Read one fact",
        "instruction": "The setup supplies the same practice store as the bot. Run its inventory lookup directly. What does a count of zero tell you?",
        "code": 'baseline = support_session(stock=0)\nbaseline.store.check_inventory("Blue hoodie (M)")',
        "takeaway": "Python reads the store. Zero is a real stock count, not a guess from the model.",
    },
    {
        "id": "schema",
        "title": "Make it a tool",
        "instruction": "Wrap the lookup in a typed function. Run it to see the input description the SDK generates for the model.",
        "code": '@beta_tool\ndef check_inventory(product: str) -> str:\n    """Check replacement stock for a product."""\n    return json.dumps(baseline.store.check_inventory(product))\n\nbaseline.tools["check_inventory"] = check_inventory\ncheck_inventory.to_dict()["input_schema"]',
        "takeaway": "@beta_tool describes the Python function so the model can request it. The product argument becomes a required string.",
    },
    {
        "id": "runner",
        "title": "Let Tool Runner handle the loop",
        "instruction": "Run the customer's request with the tools attached. until_done() continues through tool calls until the model finishes.",
        "code": 'runner = baseline.client.beta.messages.tool_runner(\n    model=MODEL, max_tokens=512,\n    tools=list(baseline.tools.values()),\n    messages=[{"role": "user", "content": CUSTOMER_REQUEST}],\n)\nanswer = runner.until_done()\nprint(answer.content[0].text)',
        "takeaway": "Tool Runner dispatches requested tools, sends their results back, and continues the conversation. You supplied the tools; the SDK handled the loop.",
    },
    {
        "id": "compare",
        "title": "Change one fact",
        "instruction": "Predict: will a return still be created with three replacements available? Run the same request and tools against a fresh store with stock=3.",
        "code": 'comparison = support_session(stock=3)\nrunner = comparison.client.beta.messages.tool_runner(\n    model=MODEL, max_tokens=512,\n    tools=list(comparison.tools.values()),\n    messages=[{"role": "user", "content": CUSTOMER_REQUEST}],\n)\nprint(runner.until_done().content[0].text)',
        "takeaway": "The request is unchanged. A different tool result changes the scripted model's next action: available stock means no return.",
    },
    {
        "id": "verify",
        "title": "Check what actually changed",
        "instruction": "A reply is not proof that an action happened. Run these assertions against both databases to check the result.",
        "code": 'before = baseline.store.snapshot()["returns"]\nafter = comparison.store.snapshot()["returns"]\nassert [row["order_id"] for row in before] == ["A100"]\nassert after == []\nprint("Checks passed: 0 in stock → 1 saved return; 3 in stock → 0 returns.")',
        "takeaway": "Both database checks passed. Tool Runner coordinates the loop; your Python tools and checks own the real application behavior.",
    },
]


def _runner_code(*, limit=None):
    """Keep the real SDK call visible in each standalone experiment."""
    limit_line = f"    max_iterations={limit},\n" if limit is not None else ""
    return ('runner = session.client.beta.messages.tool_runner(\n'
            '    model=MODEL, max_tokens=512, run_tools_eagerly=False,\n'
            + limit_line +
            '    tools=list(session.tools.values()),\n'
            '    messages=[{"role": "user", "content": CUSTOMER_REQUEST}],\n'
            ')\n')


MANUAL_CODE = '''session = support_session(stock=0)
messages = [{"role": "user", "content": CUSTOMER_REQUEST}]
for turn in range(3):
    # Ask the model, carrying the conversation so far.
    message = session.client.beta.messages.create(
        model=MODEL, max_tokens=512, messages=messages,
        tools=[tool.to_dict() for tool in session.tools.values()],
    )
    messages.append({"role": "assistant", "content": [
        block.model_dump(exclude_none=True) for block in message.content]})
    if message.stop_reason == "end_turn":
        break
    if message.stop_reason != "tool_use":
        raise RuntimeError("The model stopped without completing the task.")
    # Dispatch every requested tool yourself.
    results = []
    for block in message.content:
        if block.type != "tool_use":
            continue
        result = {"type": "tool_result", "tool_use_id": block.id}
        try:
            result["content"] = session.tools[block.name].call(block.input)
        except ToolError as error:
            result.update(content=str(error), is_error=True)
        results.append(result)
    # Send results with matching call IDs; the next turn uses them.
    messages.append({"role": "user", "content": results})
else:
    raise RuntimeError("Reached the turn limit before a final answer.")
saved = session.store.snapshot()["returns"]
assert [row["order_id"] for row in saved] == ["A100"]
print(message.content[0].text)
print("Model turns:", len(session.requests), "| Saved returns:", len(saved))'''

RUNNER_COMPARISON_CODE = ('session = support_session(stock=0)\n' + _runner_code(limit=3) +
                          'message = runner.until_done()\n'
                          'saved = session.store.snapshot()["returns"]\n'
                          'assert message.stop_reason == "end_turn"\n'
                          'assert [row["order_id"] for row in saved] == ["A100"]\n'
                          'print(message.content[0].text)\n'
                          'print("Model turns:", len(session.requests), "| Saved returns:", len(saved))')


def _trace_code(stock):
    return (f'session = support_session(stock={stock})\n' + _runner_code() +
            'for turn, message in enumerate(runner, 1):\n'
            '    print(f"Turn {turn}: {message.stop_reason}")\n'
            '    names = {block.id: block.name for block in message.content\n'
            '             if block.type == "tool_use"}\n'
            '    response = runner.generate_tool_call_response()\n'
            '    if response:\n'
            '        for result in response["content"]:\n'
            '            print(" ", names[result["tool_use_id"]], "→", result["content"])\n'
            '    else:\n'
            '        print(message.content[0].text)\n'
            'saved = session.store.snapshot()["returns"]\n'
            'print(f"Saved returns: {len(saved)}")\n'
            f'assert len(saved) == {1 if stock == 0 else 0}')


def _limit_code(limit):
    return ('session = support_session(stock=0)\n' + _runner_code(limit=limit) +
            'last_message = runner.until_done()\n'
            'saved = session.store.snapshot()["returns"]\n'
            'print(f"Last stop reason: {last_message.stop_reason}")\n'
            'print(f"Saved returns: {len(saved)}")\n'
            'if last_message.stop_reason == "end_turn":\n'
            '    print(last_message.content[0].text)\n'
            'else:\n'
            '    print("Stopped before a final answer. Check the saved returns above.")\n'
            f'assert last_message.stop_reason == {"tool_use" if limit == 2 else "end_turn"!r}\n'
            'assert len(saved) == 1')


def _error_code(outage):
    return (f'session = support_session(stock=0, inventory_outage={outage})\n'
            '# During an outage, check_inventory raises ToolError.\n' + _runner_code() +
            'inventory_result = None\n'
            'for message in runner:\n'
            '    names = {block.id: block.name for block in message.content\n'
            '             if block.type == "tool_use"}\n'
            '    response = runner.generate_tool_call_response()\n'
            '    if response:\n'
            '        for result in response["content"]:\n'
            '            if names[result["tool_use_id"]] == "check_inventory":\n'
            '                inventory_result = result\n'
            'print("is_error:", inventory_result.get("is_error", False))\n'
            'print(inventory_result["content"])\n'
            'print(message.content[0].text)\n'
            'saved = session.store.snapshot()["returns"]\n'
            'print(f"Saved returns: {len(saved)}")\n'
            f'assert inventory_result.get("is_error", False) is {outage}\n'
            f'assert len(saved) == {0 if outage else 1}')


def _persistence_code(missing):
    return (f'session = support_session(stock=0, simulate_missing_write={missing})\n'
            + _runner_code() +
            'for message in runner:\n'
            '    response = runner.generate_tool_call_response()\n'
            '    if response:\n'
            '        for result in response["content"]:\n'
            '            value = json.loads(result["content"])\n'
            '            if "return_id" in value:\n'
            '                claimed_id = value["return_id"]\n'
            'saved = session.store.snapshot()["returns"]\n'
            'verified = any(row["return_id"] == claimed_id for row in saved)\n'
            'print(message.content[0].text)\n'
            'print(f"Tool claimed: {claimed_id} | Saved returns: {len(saved)}")\n'
            'print("Verified: the claimed return is saved." if verified else\n'
            '      f"Bug caught: reply claims {claimed_id}, database has 0 returns.")\n'
            f'assert verified is {not missing}')


DEEP_CELLS = [
    {
        "id": "why", "title": "Why use Tool Runner?",
        "instruction": "A model can request a tool, but your program must execute it and send the result back. Run a minimal manual loop, then let Tool Runner do the same conversation bookkeeping. Both use the same model replies, Python tools, and store.",
        "control_label": "Run the same task with",
        "challenge": "Predict: which bookkeeping does the SDK take over?",
        "variants": [
            {"id": "manual", "label": "Your own loop", "code": MANUAL_CODE,
             "takeaway": "You wrote the conversation bookkeeping: request messages, execute tools, match result IDs, and repeat. This minimal teaching loop handles ToolError and a turn cap; a production loop needs further handling for your application's requirements."},
            {"id": "runner", "label": "Tool Runner", "code": RUNNER_COMPARISON_CODE,
             "takeaway": "The SDK replaces that loop for standard tool use, with the same tools and outcome. You still own the business logic and checks. Write a manual loop when your application's needs do not fit the runner's controls."},
        ],
    },
    {
        "id": "trace", "title": "Watch each turn",
        "instruction": "Run the same request with two stock counts. Each model turn is followed by real tool results. Calling generate_tool_call_response() here lets you inspect the results; the SDK caches them so they are not executed twice.",
        "control_label": "Replacement stock",
        "challenge": "Predict: which tool call disappears when replacements are available?",
        "variants": [
            {"id": "empty", "label": "0 in stock", "code": _trace_code(0),
             "takeaway": "Three model turns: lookup and inventory, then create the return, then answer. Two tools can run within one turn."},
            {"id": "available", "label": "3 in stock", "code": _trace_code(3),
             "takeaway": "The stock result removes the need for start_return. The scripted model finishes after two turns; the database stays unchanged."},
        ],
    },
    {
        "id": "limits", "title": "Stop before the answer",
        "instruction": "Change max_iterations from two to three. The limit counts model turns, not individual tool calls. Inspect both the last stop reason and the database.",
        "control_label": "Maximum model turns",
        "challenge": "Predict: with only two model turns, will the return already exist?",
        "variants": [
            {"id": "capped", "label": "2 turns", "code": _limit_code(2),
             "takeaway": "The return was saved, but the cap stopped the loop before the final answer. until_done() returns the last message, even when its stop reason is still tool_use."},
            {"id": "complete", "label": "3 turns", "code": _limit_code(3),
             "takeaway": "The third model turn produces the answer. A turn limit bounds work; it does not roll back actions already taken or prove the task completed."},
        ],
    },
    {
        "id": "errors", "title": "Handle a tool failure",
        "instruction": "The inventory tool raises ToolError during an outage. Inspect how the SDK turns that exception into a tool result, then restore the service and rerun the same request.",
        "control_label": "Inventory service",
        "challenge": "Predict: does an inventory failure mean there are zero replacements?",
        "variants": [
            {"id": "unavailable", "label": "Unavailable", "code": _error_code(True),
             "takeaway": "is_error=True means stock is unknown. The SDK reports the error to the model; our scripted model chooses to stop without a return. Tool Runner does not decide that business policy."},
            {"id": "restored", "label": "Restored", "code": _error_code(False),
             "takeaway": "The lookup now returns a valid count of zero. The customer's condition is met, and the return is saved. A known zero and an unknown value require different decisions."},
        ],
    },
    {
        "id": "persistence", "title": "Catch a convincing mistake",
        "instruction": "Inject a tool bug that reports success but skips the database write. Compare the claimed return ID with saved records, then fix the write and repeat.",
        "control_label": "Database write",
        "challenge": "Predict: can Tool Runner finish normally even though the return was never saved?",
        "variants": [
            {"id": "missing", "label": "Skip the write", "code": _persistence_code(True),
             "takeaway": "Bug caught: the tool and reply claim success, but there is no saved return. A completed loop cannot verify that your tool implemented its promise."},
            {"id": "saved", "label": "Save the return", "code": _persistence_code(False),
             "takeaway": "The claimed ID now matches a real record. Tool Runner owns orchestration; your tool implementation and outcome checks own correctness."},
        ],
    },
]


class SupportSession:
    """One isolated store, its tools, and an SDK client with an offline transport."""

    def __init__(self, stock=0, inventory_outage=False, simulate_missing_write=False):
        self.store = lab.Store(inventory_count=stock, inventory_outage=inventory_outage,
                               simulate_missing_write=simulate_missing_write)
        self.calls = []
        self.requests = []
        self.tools = {tool.to_dict()["name"]: tool
                      for tool in lab._build_tools(self.store, [])
                      if tool.to_dict()["name"] != "check_return_eligibility"}

        def respond(request):
            body = json.loads(request.content)
            self.requests.append(body)
            # Derive the turn from this conversation, so a learner may rerun a
            # cell without the fixture's turn counter leaking across runners.
            turn = sum(message["role"] == "assistant" for message in body["messages"])
            reply = lab._fixture_response("normal", turn, body, MODEL)
            self.calls.extend(block["name"] for block in reply["content"] if block["type"] == "tool_use")
            return httpx.Response(200, json=reply)

        self.http = httpx.Client(transport=httpx.MockTransport(respond))
        try:
            self.client = anthropic.Anthropic(
                api_key="fixture-key-not-a-real-credential", base_url="https://fixture.invalid",
                http_client=self.http, max_retries=0,
            )
        except Exception:
            self.http.close()
            self.store.close()
            raise

    def close(self):
        try:
            self.client.close()
        finally:
            self.store.close()


def curriculum():
    return {"cells": [dict(cell) for cell in CELLS],
            "deep_cells": [dict(cell) for cell in DEEP_CELLS], "notice": NOTICE}


def execute_source(code, namespace):
    """Execute trusted source and display its final expression like a notebook.

    A namespace-local print captures output without redirecting process stdout:
    several browser sessions may run cells in different server threads.
    """
    output = io.StringIO()
    namespace["print"] = lambda *args, **kwargs: print(*args, **{**kwargs, "file": output})
    tree = ast.parse(code, filename="<lab-cell>", mode="exec")
    final = tree.body.pop() if tree.body and isinstance(tree.body[-1], ast.Expr) else None
    exec(compile(tree, "<lab-cell>", "exec"), namespace)
    if final is not None:
        value = eval(compile(ast.Expression(final.value), "<lab-cell>", "eval"), namespace)
        if value is not None:
            print(json.dumps(value, indent=2, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value), file=output)
    return output.getvalue().strip()


def run_cell(cell_id, variant=None):
    """Recreate prerequisites per request; never execute browser-supplied code."""
    ids = [cell["id"] for cell in CELLS]
    deep_ids = [cell["id"] for cell in DEEP_CELLS]
    if not isinstance(cell_id, str) or cell_id not in ids + deep_ids:
        raise ValueError("Choose a known notebook cell.")
    if cell_id in ids and variant is not None:
        raise ValueError("Intro cells do not accept variants.")
    selected = next(cell for cell in CELLS + DEEP_CELLS if cell["id"] == cell_id)
    if cell_id in deep_ids:
        if not isinstance(variant, str) or variant not in [item["id"] for item in selected["variants"]]:
            raise ValueError("Choose one of this cell's two variants.")
        selected_variant = next(item for item in selected["variants"] if item["id"] == variant)
    with contextlib.ExitStack() as resources:
        def support_session(stock=0, **options):
            session = SupportSession(stock, **options)
            resources.callback(session.close)
            return session

        namespace = {"support_session": support_session, "beta_tool": beta_tool, "ToolError": ToolError,
                     "json": json, "MODEL": MODEL, "CUSTOMER_REQUEST": CUSTOMER_REQUEST}
        if cell_id in deep_ids:
            output = execute_source(selected_variant["code"], namespace)
            return {"cell_id": cell_id, "variant": variant, "output": output,
                    "takeaway": selected_variant["takeaway"], "passed": True}
        for cell in CELLS[:ids.index(cell_id) + 1]:
            output = execute_source(cell["code"], namespace)
        return {"cell_id": cell_id, "output": output,
                "takeaway": selected["takeaway"], "passed": True}


def build_notebook():
    """Build a standalone notebook: no repository, server, or API key needed."""
    def markdown(text):
        return {"cell_type": "markdown", "metadata": {}, "source": text.splitlines(keepends=True)}

    def code(source, *, setup=False, cell_id=None, variant=None):
        metadata = {"colab": {"base_uri": ""}}
        if setup:
            metadata.update(cellView="form", jupyter={"source_hidden": True})
        if cell_id:
            metadata["lab_cell_id"] = cell_id
        if variant:
            metadata["lab_variant"] = variant
        return {"cell_type": "code", "execution_count": None, "metadata": metadata,
                "outputs": [], "source": source.splitlines(keepends=True)}

    # Embed only the real store, tool definitions, and fixture machinery needed
    # here, rather than relying on a GitHub repository or a downloaded module.
    dependencies = [lab._error, lab._order_id, lab._product_key, lab.Store,
                    lab._build_tools, lab._decode_content, lab._fixture_response]
    lab_source = "from __future__ import annotations\nimport json\nimport sqlite3\nfrom typing import Any\nimport anthropic\nfrom anthropic.lib.tools import ToolError\n\n"
    lab_source += "\n\n".join(inspect.getsource(obj) for obj in dependencies)
    bootstrap = (
        "# @title Prepare the practice store and offline SDK client\n"
        "# This setup runs once. Expand it to inspect the store and scripted replies.\n"
        "%pip -q install anthropic==1.12.1 httpx2==2.13.1\n\n"
        "import atexit\nimport contextlib\nimport json\nimport types\n"
        "import anthropic\nimport httpx2 as httpx\nfrom anthropic import beta_tool\nfrom anthropic.lib.tools import ToolError\n\n"
        f"MODEL = {MODEL!r}\nCUSTOMER_REQUEST = {CUSTOMER_REQUEST!r}\n"
        "lab = types.ModuleType('practice_store')\n"
        f"exec({lab_source!r}, lab.__dict__)\n\n"
        + inspect.getsource(SupportSession) + "\n\n"
        "if 'resources' in globals():\n    resources.close()\n"
        "resources = contextlib.ExitStack()\natexit.register(resources.close)\n\n"
        "def support_session(stock=0, **options):\n"
        "    session = SupportSession(stock, **options)\n"
        "    resources.callback(session.close)\n"
        "    return session\n\n"
        f"print({NOTICE!r})\nprint('Ready. Run Part 1, then try the experiments in Part 2.')"
    )
    cells = [
        markdown("# Understand Tool Runner, one cell at a time\n\n"
                 "You are helping a customer whose hoodie is late. They want a return **only if no replacement is available**. "
                 "Start with one lookup, expose it as a tool, then let Tool Runner carry the request through to an answer.\n\n"
                 "**Practice mode:** the model replies are scripted; Anthropic's actual SDK runs real Python tools and saves real SQLite records. "
                 "No Claude API key is needed and there are no model charges. This notebook does not call Claude.\n\n"
                 "Run the setup once, then run Part 1's five cells in order. Part 2 has five deeper experiments, each with two versions to compare. All lesson cells are identical to the lab UI. "
                 "In Colab, you can edit them and experiment. If you change an earlier cell, rerun the cells below it.\n\n"
                 "`support_session(stock=...)` supplies a fresh practice store, its order/stock/return tools, and an offline SDK client. "
                 "It does not run the Tool Runner for you. Expand setup to inspect those definitions."),
        code(bootstrap, setup=True),
        markdown(f"**Customer request**\n\n> {CUSTOMER_REQUEST}"),
    ]
    for index, cell in enumerate(CELLS, 1):
        cells.append(markdown(f"## {index}. {cell['title']}\n\n{cell['instruction']}"))
        cells.append(code(cell["code"], cell_id=cell["id"]))
        cells.append(markdown(f"**What to notice:** {cell['takeaway']}"))
    cells.append(markdown("## You have completed the loop\n\n"
                          "Python owns the facts and actions. `@beta_tool` describes what the model may request. "
                          "Tool Runner dispatches those requests and returns tool results until the conversation finishes. "
                          "Your assertions check the saved outcome.\n\n"
                          "Try a new stock count in cell 4. Before running it, predict whether a return should exist. "
                          "The fixed model replies respond to stock changes; arbitrary new prompts need a live model.\n\n"
                          "[Read the Tool Runner documentation](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-runner)."))
    cells.append(markdown("# Part 2: Why Tool Runner, and how to control it\n\n"
                          "Each experiment changes one condition. **Predict → run → change → check.** "
                          "Run both versions before moving on. Each version creates its own fresh store, so these cells can be rerun independently.\n\n"
                          "The model is still scripted. These experiments show what the installed SDK does, while making our model behavior and application policy visible. "
                          "Tool Runner is a beta SDK helper; this notebook pins the version used by the lab."))
    for index, cell in enumerate(DEEP_CELLS, 1):
        cells.append(markdown(f"## Experiment {index}: {cell['title']}\n\n{cell['instruction']}\n\n**Predict:** {cell['challenge'].removeprefix('Predict: ')}"))
        for variant_index, variant in enumerate(cell["variants"]):
            action = "Run" if variant_index == 0 else "Change and rerun"
            cells.append(markdown(f"### {action}: {variant['label']}"))
            cells.append(code(variant["code"], cell_id=cell["id"], variant=variant["id"]))
            cells.append(markdown(f"**Check:** {variant['takeaway']}"))
    cells.append(markdown("## What you now know\n\n"
                          "You can inspect each model turn, bound the loop, distinguish tool failures from valid data, and verify a reported action against saved state. "
                          "Tool Runner handles tool dispatch and conversation continuation. Your application supplies policy, reliable tools, and outcome checks.\n\n"
                          "**Try your own test:** change the cap to one turn. Predict which tools will run, the last stop reason, and whether a return will exist. "
                          "Before running, update the expected assertions to test your prediction. Then run it and inspect the saved state.\n\n"
                          "[Tool Runner reference](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-runner)"))
    cells.append(code("# @title Close the practice stores when you are finished\nresources.close()\nprint('Practice stores closed. Rerun setup to start again.')", setup=True))
    for index, cell in enumerate(cells):
        cell["id"] = f"tool-runner-{index:02d}"
    return {"nbformat": 4, "nbformat_minor": 5, "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.12"},
        "colab": {"name": "tool-runner-lab.ipynb", "provenance": [], "toc_visible": False},
    }, "cells": cells}


if __name__ == "__main__":
    target = Path(__file__).with_name("tool-runner-lab.ipynb")
    target.write_text(json.dumps(build_notebook(), indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {target.name}")
