"""Four standalone self-paced notebooks with editable code and observable outcomes."""
from __future__ import annotations

import json
from pathlib import Path
import re
from urllib.parse import quote

import notebook_lab
import tutorial_runtime as runtime

MODULES = [
    {"id": "why-tool-runner", "title": "Why Tool Runner?", "minutes": 15,
     "outcome": "Replace manual conversation bookkeeping with the SDK and prove the same return was saved.",
     "notebook": "/tutorials/01-why-tool-runner.ipynb", "reference": "/tutorials/01-why-tool-runner.reference.ipynb", "exercise": "build_runner"},
    {"id": "build-tools", "title": "Build the tool your bot uses", "minutes": 20,
     "outcome": "Write a typed inventory tool, run it inside the bot, and see its data change the decision.",
     "notebook": "/tutorials/02-build-tools.ipynb", "reference": "/tutorials/02-build-tools.reference.ipynb", "exercise": "make_inventory_tool"},
    {"id": "verify-actions", "title": "Prove the action happened", "minutes": 20,
     "outcome": "Catch a convincing success claim that has no matching database record, and distinguish an outage from zero stock.",
     "notebook": "/tutorials/03-verify-actions.ipynb", "reference": "/tutorials/03-verify-actions.reference.ipynb", "exercise": "verify_return"},
    {"id": "make-it-yours", "title": "Make the bot follow your data", "minutes": 20,
     "outcome": "Adapt physical stock into available stock, test the changed behavior, and optionally try the same code with live Claude.",
     "notebook": "/tutorials/04-make-it-yours.ipynb", "reference": "/tutorials/04-make-it-yours.reference.ipynb", "exercise": "available_stock"},
]

NOTICE = "Four independent Colab labs. Times are planning estimates, not learner-tested timings. Scripted practice needs no API key."
PUBLICATION_PATH = Path(__file__).with_name("publication.json")


def _publication_target(path):
    """Read only a GitHub repository and single-segment ref; fail closed locally."""
    try:
        publication = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return None
    if not isinstance(publication, dict) or set(publication) != {"repository", "ref"}:
        return None
    repository, ref = publication["repository"], publication["ref"]
    if not isinstance(repository, str) or not isinstance(ref, str):
        return None
    parts = repository.split("/")
    if len(parts) != 2:
        return None
    owner, name = parts
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", owner):
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", name) or name in {".", ".."}:
        return None
    # Main, a tag, or a commit SHA is sufficient here. Reject separators and URL
    # syntax rather than allowing publication settings to change the URL host.
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", ref) or ".." in ref:
        return None
    return "/".join(quote(part, safe="") for part in (owner, name, "blob", ref))


def manifest(*, publication_path=None):
    modules = [dict(module) for module in MODULES]
    target = _publication_target(PUBLICATION_PATH if publication_path is None else publication_path)
    if target:
        for module in modules:
            for field, link_field in (("notebook", "colab_url"), ("reference", "reference_colab_url")):
                # Download paths remain unchanged. Only known notebook paths get
                # hosted links, and the generator still reads the original MODULES.
                path = module[field]
                if re.fullmatch(r"/tutorials/[a-z0-9.-]+\.ipynb", path):
                    module[link_field] = "https://colab.research.google.com/github/" + target + path
    return {"modules": modules, "notice": NOTICE}


LESSONS = {
    "why-tool-runner": {
        "opening": "The bot already works. Now inspect the repeated work that makes it work: ask the model, run its requested tools, attach matching results, and ask again. Your goal is to replace that bookkeeping while preserving the outcome.",
        "prediction": "The customer wants a return only when replacement stock is zero. Will using Tool Runner change the number of model turns or the saved return?",
        "instruction": "Complete `build_runner`. Return the SDK runner, rather than a finished message. Use the supplied client, tools, and request. The interface will iterate the runner and show its actual tool results.",
        "hint": "Use `client.beta.messages.tool_runner(...)`. A user message is `{'role': 'user', 'content': request}`. `tools` is already the list of decorated tools. A turn cap bounds work; it does not undo actions.",
        "experiment": "Set stock to 0 and run your bot. Then set it to 3. Compare the tool results and saved returns with the manual loop. Both versions call the same tools; the SDK takes over coordination.",
        "takeaway": "Tool Runner exists to remove repetitive tool-dispatch and conversation-management code. It does not implement your business logic or prove your tools kept their promises. The manual loop is useful when you need behavior that does not fit the runner's controls.",
        "transfer": "Explain which lines in the manual loop correspond to tool dispatch, call-ID matching, and conversation continuation. Which responsibilities remain in your application?",
    },
    "build-tools": {
        "opening": "The model cannot know your inventory from its own knowledge. You supply a Python tool that reads the store, and the SDK describes that tool to the model. Your code will power the same support bot.",
        "prediction": "If the store contains zero, should the tool return zero or report an error? What should happen if the inventory service cannot be reached?",
        "instruction": "Complete the single lookup line inside `check_inventory`. Read this run's `session.store`, then return JSON. Keep the type annotation and docstring; they describe the tool to the model.",
        "hint": "`session.store.check_inventory(product)` returns a dictionary containing product and count. `json.dumps(...)` converts it into the string returned by the tool. Do not catch lookup errors and convert them to zero.",
        "experiment": "Run the bot with stock=0, then stock=3. Your decorated tool is installed into each fresh run. Inspect the check_inventory result and the saved return below the reply.",
        "takeaway": "The SDK derives the schema from your function, dispatches the model's request, and sends the returned data back. Your Python code owns the data source and error semantics. Known zero and unknown stock are different facts.",
        "transfer": "Change the docstring to explain a limitation of your inventory source. Run the schema cell again and find your description. Then restore the accurate description before saving.",
    },
    "verify-actions": {
        "opening": "A final answer can look successful even when a tool has a bug. This store can deliberately claim that it saved a return while skipping the write. Your check must catch that discrepancy.",
        "prediction": "Can a runner finish with end_turn while the database has no return? If inventory is unavailable, is a missing return itself a failure?",
        "instruction": "Complete `verify_return`. A verified return needs both the claimed return ID and order ID in the same saved row. No claim or no matching record must return False.",
        "hint": "Use `bool(claim) and any(...)`. Inside the generator, compare `row['return_id']` and `row['order_id']` with the claim. Checking only that saved records exist accepts the wrong action.",
        "experiment": "Run the bot with stock=0 and Working store. Repeat with Skip database write: the reply still claims success, but your verification should be False. Finally choose Inventory outage: inspect the error and why the bot makes no claim.",
        "takeaway": "end_turn describes conversation completion. Your outcome predicate checks a real application fact. Tool Runner surfaces tool errors; your tool and model policy decide what to do about them. No claim during an outage is different from a false success claim.",
        "transfer": "Add a separate test using a correct return ID paired with the wrong order. Explain why checking the number of rows would miss this bug. Keep the working and missing-write cases as regression tests.",
    },
    "make-it-yours": {
        "opening": "Your warehouse has three hoodies, but some are reserved for other customers. Physical stock is no longer the right answer to 'Can I get a replacement?' Adapt the existing tool to return available stock. The changed value must alter the bot's action.",
        "prediction": "With three physical units and three reservations, should this customer's bot create a return? What if only one unit is reserved?",
        "instruction": "Implement `available_stock` with less scaffolding. Read the store, subtract reserved units, clamp the result at zero, and preserve the product field. Let unknown stock remain an error. The tool wrapper below calls this function during every bot run.",
        "hint": "Copy the returned dictionary with `{**record, 'count': ...}`. `max(0, record['count'] - reserved)` avoids a negative availability. The reservation count is supplied by the interface in this small exercise.",
        "experiment": "Keep physical stock at 3. Run with 1 reserved, then 3 reserved. You should see 2 available and no return, then 0 available and one saved return. Change your function, rerun its cell, and click the same bot button: it uses the new definition.",
        "takeaway": "You changed an application's interpretation of its data without rewriting the conversation loop. In a real integration, replace the practice data access with a warehouse API and retain the tool contract and checks. New arbitrary tools or prompts need a matching fixture or a live model.",
        "transfer": "Create one more local assertion for reservations greater than physical stock. Then identify where your real data source would provide reservations. Keep that source separate from Tool Runner's conversation coordination.",
    },
}


def _bootstrap():
    original = next(cell for cell in notebook_lab.build_notebook()["cells"] if cell["cell_type"] == "code")
    source = "".join(original["source"])
    source = source.replace("%pip -q install anthropic==1.12.1 httpx2==2.13.1", "%pip -q install anthropic==1.12.1 httpx2==2.13.1 ipywidgets==8.1.8")
    source = source.replace("print('Ready. Run Part 1, then try the experiments in Part 2.')", "")
    runtime_source = Path(runtime.__file__).read_text().replace(
        "from notebook_lab import SupportSession, MODEL, CUSTOMER_REQUEST, MANUAL_CODE\n", "")
    return source + f"\nMANUAL_CODE = {notebook_lab.MANUAL_CODE!r}\nexec({runtime_source!r}, globals())\n" + (
        "try:\n"
        "    from google.colab import output as colab_output\n"
        "    colab_output.enable_custom_widget_manager()\n"
        "except ImportError:\n"
        "    pass\n"
        "print('Ready. The practice model is scripted; your Python and SDK calls are real.')\n"
    )


def build_notebook(module_id, *, reference=False):
    module = next(m for m in MODULES if m["id"] == module_id)
    lesson = LESSONS[module_id]
    cells = []
    def md(text):
        cells.append({"cell_type": "markdown", "metadata": {}, "source": text.splitlines(keepends=True)})
    def code(text, role, *, collapsed=False):
        metadata = {"lab_role": role, "lab_module": module_id}
        if collapsed:
            metadata.update(cellView="form", jupyter={"source_hidden": True})
        cells.append({"cell_type": "code", "execution_count": None, "metadata": metadata,
                      "outputs": [], "source": text.splitlines(keepends=True)})
    suffix = " — complete reference" if reference else ""
    md(f"# {module['title']}{suffix}\n\n{lesson['opening']}\n\n"
       f"**You will:** {module['outcome']}\n\n"
       f"**About {module['minutes']} minutes** is a planning estimate. Work at your own pace; stop after any checkpoint. "
       "You need basic Python functions and dictionaries. Each notebook runs independently.\n\n"
       "**Practice mode:** scripted model replies, real Anthropic SDK, real Python tools, and a fresh SQLite store for each run. "
       "No API key or model charges. Setup downloads Python packages; practice does not contact Claude.\n\n"
       "In Colab choose **File → Save a copy in Drive**, then run one code cell at a time. "
       "Edit and rerun the function cell before testing changes. If you get stuck, use the hint, the explicit recovery cell, or this module's complete reference notebook. "
       "Avoid Run all on the learner version: the exercise starts unfinished on purpose.")
    md("## 1. Prepare and check the environment\n\nRun once. The collapsed setup supplies the practice store and display helpers. "
       "The Tool Runner call and the function you will change remain visible below. If setup fails, rerun it; if imports still fail, restart the Colab session and rerun setup.")
    code(_bootstrap(), "setup", collapsed=True)
    code("probe = SupportSession(stock=0)\ntry:\n    assert probe.store.check_inventory('Blue hoodie (M)')['count'] == 0\n    print('Preflight passed: SDK imported and practice store returns real data.')\nfinally:\n    probe.close()", "preflight")
    md(f"## 2. Predict\n\n{lesson['prediction']}\n\nWrite a one-sentence prediction before running the next experiment.")
    if module_id == "why-tool-runner":
        md("### Run the manual loop once\n\nThis minimal example makes the coordination visible. Notice the assistant message, tool result IDs, and repeated request. "
           "It is a teaching example, not a complete production orchestration framework. The final line checks the saved outcome.")
        code(notebook_lab.MANUAL_CODE, "manual")
    else:
        md("### The Tool Runner call you are building around\n\nThis working function is supplied so you can focus on this module's exercise. "
           "The bot calls it with your tools. `max_iterations` limits model turns, and `run_tools_eagerly=False` lets the display inspect tool results explicitly.")
        code(runtime.RUNNER_SOLUTION, "supplied-runner")
    md(f"## 3. Build one piece\n\n{lesson['instruction']}\n\n"
       "Run the function definition after editing it. Defining a function alone does not execute the customer's request.")
    code(runtime.SOLUTIONS[module_id] if reference else runtime.STARTERS[module_id], "exercise")
    md(f"<details><summary>Hint</summary>\n\n{lesson['hint']}\n\n</details>")
    if module_id == "build-tools":
        code("schema_session = SupportSession(stock=0)\ntry:\n    print(json.dumps(make_inventory_tool(schema_session).to_dict(), indent=2))\nfinally:\n    schema_session.close()", "inspect-schema")
    if module_id == "make-it-yours":
        md("The supplied inventory tool wrapper uses your function like this on each run:\n\n"
           "```python\n@beta_tool\ndef check_inventory(product: str) -> str:\n"
           '    """Report replacement stock after subtracting reservations."""\n'
           "    return json.dumps(available_stock(session.store, product, reserved))\n```\n\n"
           "The runtime creates a new store per click, installs that wrapper in the runner's tool list, and calls your latest executed definition.")
    md("## 4. Run your bot\n\n" + lesson["experiment"])
    code(f"bot_button = show_bot(globals(), {module_id!r})", "interface")
    md("If the widget does not appear, use the ordinary Python cell below. It runs the same implementation and prints the same evidence. "
       "Change its inputs to repeat your experiment. No separate app or server is required.")
    params = ", stock=3, reserved=3" if module_id == "make-it-yours" else ", stock=0"
    code("try:\n" + f"    result = run_bot(globals(), {module_id!r}{params})\n"
         "    render_result(result)\n"
         "except Exception as error:\n"
         "    print('Not ready yet. Edit and rerun your function cell, then run this cell again.')\n"
         "    print(type(error).__name__ + ': ' + str(error))", "bot-run")
    md("## 5. Check the behavior\n\nThe check calls your actual function with multiple cases, including a failure or boundary condition. "
       "A passing check is evidence for those cases, not a guarantee about every possible model response. "
       "If it fails, read the first unmet condition, edit and rerun your function, and rerun this cell.")
    code(f"checkpoint = check_exercise({module_id!r}, globals())", "checkpoint")
    md("### Recover without losing your place\n\nThe reference is available below. Reading it does not change your code. "
       "To load it into the running notebook, change `USE_REFERENCE` to `True` and run the recovery cell. "
       "This explicitly replaces only this exercise's function in memory. Your editable cell remains intact. "
       "Rerun the check and bot afterward; their output identifies a loaded reference. Rerun your own function cell to switch back.\n\n"
       f"<details><summary>Read the reference solution</summary>\n\n```python\n{runtime.SOLUTIONS[module_id]}\n```\n\n</details>")
    code("USE_REFERENCE = False\nif USE_REFERENCE:\n" + f"    load_reference({module_id!r}, globals())\n"
         "else:\n    print('Your implementation is still active. Set USE_REFERENCE=True only if you want to replace it.')", "recovery")
    md(f"## What changed—and why it matters\n\n{lesson['takeaway']}\n\n"
       f"**Try independently:** {lesson['transfer']}\n\n"
       "**Keep your work:** save this notebook with your implementation and observed results. "
       "After a disconnected runtime, rerun setup, the supplied runner (if present), your edited function, and the check. "
       "Each run creates its own store, so previous bot output is a record of that run rather than persistent customer data.")
    if module_id == "make-it-yours":
        md("## Optional: use live Claude\n\nThe same edited tool and runner can call a live model. This is optional and may incur API charges. "
           "First pass the practice checkpoint. Select a model available to your account, set `RUN_LIVE=True`, and run the cell. "
           "It prompts privately for your key with `getpass`; never paste a key into a code cell. "
           "The key is used for this call and is not printed or stored in the notebook. "
           "The model sees the sample customer request and practice tool results. The database remains local practice data. "
           "Judge the tool calls and saved records; live wording and choices can vary.")
        code("RUN_LIVE = False\nLIVE_MODEL = MODEL  # Choose a model your Anthropic account can access.\n"
             "if RUN_LIVE:\n    if check_exercise('make-it-yours', globals(), quiet=True)['passed']:\n"
             "        live_result = run_live(globals(), model=LIVE_MODEL)\n"
             "    else:\n        print('Pass the practice checkpoint before making a live call.')\n"
             "else:\n    print('Practice only. No API key requested and no live call made.')", "live-opt-in")
    md("## Stop here or continue\n\nYou can return to any module independently. Next modules deepen the same application instead of adding unrelated screens.\n\n"
       "1. Why Tool Runner?\n2. Build the tool your bot uses\n3. Prove the action happened\n4. Make the bot follow your data\n\n"
       "[Anthropic Tool Runner guide](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-runner) · "
       "[Colab widget example](https://colab.research.google.com/github/jupyter-widgets/ipywidgets/blob/master/docs/source/examples/Widget%20Basics.ipynb)\n\n"
       "This lab pins its SDK version because Tool Runner's beta interface can change. The supplied reference implementation and checks target that pinned version.")
    code("# @title Close practice resources when finished\nresources.close()\nprint('Resources closed. Save your notebook; rerun setup to start again.')", "cleanup", collapsed=True)
    for index, cell in enumerate(cells):
        cell["id"] = f"{module_id}-{index:02d}"
    filename = Path(module["reference" if reference else "notebook"]).name
    return {"nbformat": 4, "nbformat_minor": 5, "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.12"},
        "colab": {"name": filename, "provenance": [], "toc_visible": False},
        "lab": {"module": module_id, "reference": reference},
    }, "cells": cells}


def write_notebooks():
    root = Path(__file__).parent
    for module in MODULES:
        for reference in (False, True):
            target = root / module["reference" if reference else "notebook"].lstrip("/")
            target.parent.mkdir(exist_ok=True)
            target.write_text(json.dumps(build_notebook(module["id"], reference=reference), indent=2, ensure_ascii=False) + "\n")
    return [module[key] for module in MODULES for key in ("notebook", "reference")]


if __name__ == "__main__":
    for path in write_notebooks():
        print(path)
