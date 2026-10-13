"""Notebook helpers. Learner functions remain visible, editable, and in the run path."""
from __future__ import annotations

import contextlib
import hashlib
import html
import inspect
import json

import anthropic
from anthropic import beta_tool
from anthropic.lib.tools import ToolError

from notebook_lab import SupportSession, MODEL, CUSTOMER_REQUEST, MANUAL_CODE

EXERCISES = {
    "why-tool-runner": "build_runner",
    "build-tools": "make_inventory_tool",
    "verify-actions": "verify_return",
    "make-it-yours": "available_stock",
}

RUNNER_SOLUTION = '''def build_runner(client, tools, request):
    """Let the SDK dispatch tools and continue the conversation."""
    return client.beta.messages.tool_runner(
        model=MODEL, max_tokens=512, max_iterations=6,
        run_tools_eagerly=False,
        tools=tools,
        messages=[{"role": "user", "content": request}],
    )'''
INVENTORY_SOLUTION = '''def make_inventory_tool(session):
    @beta_tool
    def check_inventory(product: str) -> str:
        """Check replacement stock. Zero is valid; lookup failures are errors."""
        return json.dumps(session.store.check_inventory(product))
    return check_inventory'''
VERIFY_SOLUTION = '''def verify_return(claim, saved):
    """Require the claimed return ID AND order ID to exist in saved records."""
    return bool(claim) and any(
        row["return_id"] == claim["return_id"]
        and row["order_id"] == claim["order_id"]
        for row in saved
    )'''
AVAILABLE_SOLUTION = '''def available_stock(store, product, reserved):
    """Subtract reserved units from physical stock, with a minimum of zero."""
    record = store.check_inventory(product)
    return {**record, "count": max(0, record["count"] - reserved)}'''
SOLUTIONS = dict(zip(EXERCISES, [RUNNER_SOLUTION, INVENTORY_SOLUTION, VERIFY_SOLUTION, AVAILABLE_SOLUTION]))
STARTERS = {
    "why-tool-runner": '''def build_runner(client, tools, request):
    """Return client.beta.messages.tool_runner(...) with the supplied tools/request."""
    # Include model=MODEL, max_tokens=512, max_iterations=6,
    # run_tools_eagerly=False, tools=tools, and a user message.
    raise NotImplementedError("Connect this function to the SDK Tool Runner.")''',
    "build-tools": '''def make_inventory_tool(session):
    @beta_tool
    def check_inventory(product: str) -> str:
        """Check replacement stock. Zero is valid; lookup failures are errors."""
        # Read session.store.check_inventory(product), then serialize it to JSON.
        raise ToolError("Exercise unfinished: read the store and return its JSON result.")
    return check_inventory''',
    "verify-actions": '''def verify_return(claim, saved):
    """True only when both claimed IDs match the same saved return record."""
    # claim may be None. saved may be empty. Compare return_id AND order_id.
    raise NotImplementedError("Verify the claim against the saved records.")''',
    "make-it-yours": '''def available_stock(store, product, reserved):
    """Return a product/count record after subtracting reserved units."""
    # Preserve other fields, clamp count at zero, and let lookup errors propagate.
    raise NotImplementedError("Turn physical stock into available replacement stock.")''',
}


def load_reference(module, namespace):
    """Explicit recovery action; never invoked by a check or bot callback."""
    exec(SOLUTIONS[module], namespace)
    function = namespace[EXERCISES[module]]
    namespace.setdefault("_loaded_reference_code", {})[module] = function.__code__
    print(f"Reference loaded for {function.__name__}. Your previous function was replaced in memory.")
    print("The editable cell is unchanged. Rerun your edited cell to use your implementation again.")


def origin(module, namespace):
    name = EXERCISES[module]
    function = namespace.get(name)
    if function is None:
        return f"{name}: no exercise function loaded; rerun its definition cell"
    if not callable(function):
        return f"{name}: expected a function, found {type(function).__name__}; rerun its definition cell"
    code = getattr(function, "__code__", None)
    if code is None:
        return f"{name}: current callable ({type(function).__name__}); no Python function fingerprint"
    reference = namespace.get("_loaded_reference_code", {}).get(module)
    source = "loaded reference" if code is reference else "current notebook code"
    digest = hashlib.sha256(code.co_code + repr(code.co_consts).encode()).hexdigest()[:8]
    return f"{name}: {source} ({digest})"


def run_bot(namespace, module, *, stock=0, reserved=0, fault="none", request=CUSTOMER_REQUEST,
            live_client=None):
    """Fresh store per run; exercise function looked up at run time, never cached."""
    session = SupportSession(stock=stock, inventory_outage=fault == "outage",
                             simulate_missing_write=fault == "missing-write")
    invoked = []
    try:
        if module == "build-tools":
            tool = namespace["make_inventory_tool"](session)
            if tool.to_dict()["name"] != "check_inventory":
                raise ValueError("Name the decorated function check_inventory so the model can request it.")
            session.tools["check_inventory"] = tool
            invoked.append("make_inventory_tool")
        if module == "make-it-yours":
            @beta_tool
            def check_inventory(product: str) -> str:
                """Report replacement stock after subtracting reserved units; errors mean unknown stock."""
                invoked.append("available_stock")
                try:
                    return json.dumps(namespace["available_stock"](session.store, product, reserved))
                except NotImplementedError as error:
                    raise ToolError("Exercise unfinished: " + str(error)) from error
            session.tools["check_inventory"] = check_inventory
        client = live_client if live_client is not None else session.client
        # In module 1 this is the learner's function. Other modules retain the
        # visible reference function from their notebook's setup cell.
        runner = namespace["build_runner"](client, list(session.tools.values()), request)
        invoked.append("build_runner")
        events, claims, final = [], [], None
        for message in runner:
            final = message
            names = {block.id: block.name for block in message.content if block.type == "tool_use"}
            response = runner.generate_tool_call_response()
            if response:
                for result in response["content"]:
                    value = lab_decode(result.get("content"))
                    events.append({"tool": names[result["tool_use_id"]], "result": value,
                                   "is_error": bool(result.get("is_error", False))})
                    if isinstance(value, dict) and "return_id" in value:
                        claims.append(value)
        if final is None:
            raise ValueError("The runner produced no model messages.")
        saved = session.store.snapshot()["returns"]
        verification = None
        if module == "verify-actions":
            verification = namespace["verify_return"](claims[-1] if claims else None, saved)
            invoked.append("verify_return")
        return {"reply": "\n".join(block.text for block in final.content if block.type == "text"),
                "stop_reason": final.stop_reason, "saved": saved, "claims": claims,
                "events": events, "turns": len(session.requests) if live_client is None else None,
                "verified": verification, "invoked": invoked,
                "implementation": origin(module, namespace),
                "mode": "Live Claude" if live_client is not None else "Scripted model · real SDK/tools/database"}
    finally:
        session.close()


def lab_decode(content):
    if isinstance(content, list):
        content = "\n".join(item.get("text", "") for item in content if isinstance(item, dict))
    try:
        return json.loads(content)
    except (ValueError, TypeError):
        return content


def check_exercise(module, namespace, *, quiet=False):
    """Behavioral checks on learner code. Return evidence, not just a green badge."""
    checks = []
    def require(condition, message):
        if not condition:
            raise AssertionError(message)
        checks.append(message)
    try:
        name = EXERCISES[module]
        if not callable(namespace.get(name)):
            raise TypeError(f"{name} must be a function; rerun its definition cell")
        if module == "why-tool-runner":
            result = run_bot(namespace, module)
            require(result["stop_reason"] == "end_turn", "The runner reaches a final model answer.")
            require([row["order_id"] for row in result["saved"]] == ["A100"], "The tools saved the A100 return.")
            require(result["turns"] == 3, "The same request takes three model turns.")
            stocked = run_bot(namespace, module, stock=3)
            require(stocked["saved"] == [], "Available replacement stock prevents a return.")
        elif module == "build-tools":
            for stock in (0, 3):
                session = SupportSession(stock)
                try:
                    tool = namespace["make_inventory_tool"](session)
                    schema = tool.to_dict()["input_schema"]
                    require(schema["properties"]["product"]["type"] == "string" and "product" in schema["required"],
                            "The generated schema requires a product string.")
                    value = json.loads(tool.call({"product": " blue HOODIE (m) "}))
                    require(value["count"] == stock, f"The tool reads the actual store count: {stock}.")
                finally:
                    session.close()
                result = run_bot(namespace, module, stock=stock)
                inventory = next(event for event in result["events"] if event["tool"] == "check_inventory")
                require(not inventory["is_error"] and inventory["result"]["count"] == stock,
                        f"Tool Runner dispatched your inventory tool with stock={stock}.")
                require(len(result["saved"]) == (1 if stock == 0 else 0), "The bot outcome follows your tool result.")
            outage = run_bot(namespace, module, fault="outage")
            require(any(e["is_error"] for e in outage["events"]), "An outage stays an error, not a made-up zero.")
            require(not outage["saved"], "An inventory outage does not start a return.")
        elif module == "verify-actions":
            verify = namespace["verify_return"]
            claim = {"return_id": "R-A100", "order_id": "A100"}
            require(verify(claim, [claim]) is True, "A matching saved return passes.")
            require(verify(claim, []) is False, "A missing database write fails verification.")
            require(verify(None, []) is False, "No claim is not a verified return.")
            require(verify(claim, [{"return_id": "R-A100", "order_id": "A101"}]) is False,
                    "A matching return ID on the wrong order fails.")
            require(verify(claim, [{"return_id": "R-other", "order_id": "A100"}]) is False,
                    "A matching order with the wrong return ID fails.")
            for fault, expected in [("none", True), ("missing-write", False), ("outage", False)]:
                result = run_bot(namespace, module, fault=fault)
                require(result["verified"] is expected, f"Real bot/database check with {fault}: verified={expected}.")
        else:
            for stock, reserved, expected in [(3, 1, 2), (3, 3, 0), (3, 5, 0), (0, 0, 0)]:
                result = run_bot(namespace, module, stock=stock, reserved=reserved)
                event = next(e for e in result["events"] if e["tool"] == "check_inventory")
                require(not event["is_error"] and event["result"]["count"] == expected,
                        f"Physical {stock} − reserved {reserved} → available {expected}.")
                require(len(result["saved"]) == (1 if expected == 0 else 0), "The bot follows the available count.")
                require("available_stock" in result["invoked"], "Tool Runner called your stock adapter.")
            outage = run_bot(namespace, module, fault="outage")
            require(any(e["is_error"] for e in outage["events"]) and not outage["saved"],
                    "Unknown stock stays an error and no return is saved.")
        report = {"passed": True, "checks": checks, "message": "Checkpoint passed. Your code powers the bot.",
                  "implementation": origin(module, namespace)}
    except Exception as error:
        message = str(error) if isinstance(error, (NotImplementedError, AssertionError, ValueError, KeyError, TypeError, AttributeError, ToolError)) else type(error).__name__
        report = {"passed": False, "checks": checks,
                  "message": f"Not ready yet: {message}. Edit and rerun your function cell, then rerun this check.",
                  "implementation": origin(module, namespace)}
    if not quiet:
        print(report["message"])
        print(report["implementation"])
        for check in checks:
            print("✓", check)
    return report


def show_bot(namespace, module):
    """An inline view of the same learner code. No external server or UI build."""
    import ipywidgets as widgets
    from IPython.display import display, HTML, clear_output
    stock = widgets.IntSlider(value=0 if module != "make-it-yours" else 3, min=0, max=5, description="In stock:")
    reserved = widgets.IntSlider(value=0, min=0, max=5, description="Reserved:")
    fault = widgets.Dropdown(options=[("Working store", "none"), ("Skip database write", "missing-write"),
                                      ("Inventory outage", "outage")], description="Condition:")
    button = widgets.Button(description="Run my bot", button_style="primary")
    output = widgets.Output()
    controls = [stock]
    if module == "make-it-yours":
        controls.append(reserved)
    if module == "verify-actions":
        controls.append(fault)
    def clicked(_):
        button.disabled = True
        with output:
            clear_output(wait=True)
            try:
                result = run_bot(namespace, module, stock=stock.value, reserved=reserved.value, fault=fault.value)
                render_result(result)
            except Exception as error:
                print("Not ready yet. Edit and rerun your function cell, then click Run my bot again.")
                print(type(error).__name__ + ": " + str(error))
        button.disabled = False
    button.on_click(clicked)
    display(HTML("<p><b>Customer:</b> " + html.escape(CUSTOMER_REQUEST) + "</p>"))
    display(widgets.VBox(controls + [button, output]))
    print("Uses your latest executed function definition. Rerun an edited function cell; this button picks it up automatically.")
    print("Each click starts a fresh store. Replies are scripted; the actual SDK executes your tools.")
    return button


def render_result(result):
    print(result["mode"])
    print(result["implementation"])
    print("\nBot:", result["reply"] or f"Stopped with {result['stop_reason']} before a final reply.")
    for event in result["events"]:
        print("\n" + event["tool"] + (" [tool error]" if event["is_error"] else ""), "→", event["result"])
    print("\nSaved returns:", json.dumps(result["saved"], indent=2))
    if result["verified"] is not None:
        print("Your verification:", result["verified"])
    print("Actual exercise path:", " → ".join(result["invoked"]))


def run_live(namespace, *, model):
    """Explicit optional call. Key is prompted privately, never embedded or printed."""
    from getpass import getpass
    key = getpass("Anthropic API key (hidden; used for this call only): ").strip()
    if not key:
        print("No key supplied. No API call made.")
        return None
    previous_model = namespace.get("MODEL", MODEL)
    try:
        namespace["MODEL"] = model
        with anthropic.Anthropic(api_key=key, max_retries=0) as client:
            result = run_bot(namespace, "make-it-yours", stock=3, reserved=3, live_client=client)
        render_result(result)
        print("Check the tool events and database, not exact wording. Live model choices can differ.")
        return result
    except Exception as error:
        # Do not surface request headers or exception bodies that may include secrets.
        print(f"Live call could not complete ({type(error).__name__}). Check your key, model access, and account.")
        return None
    finally:
        namespace["MODEL"] = previous_model
        key = None
