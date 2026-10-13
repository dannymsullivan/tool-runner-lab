"""Inspectable support lab: real SDK runner, local SQLite tools, optional live model.

Fixture mode supplies scripted API responses through the SDK's HTTP transport.
It never contacts Anthropic and never represents fixture behavior as model reasoning.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from typing import Any, Callable

import anthropic
import httpx2 as httpx
from anthropic.lib.tools import ToolError

DEFAULT_MODEL = "claude-haiku-5-5"
SCENARIOS = [
    {"id": "normal", "title": "Rescue an order", "description": "Look up the hoodie, inspect stock, and create a return only when no replacement is available.", "prompt": "What's the status of order A100? Check replacement stock for Blue hoodie (M). If none is available, please start a return because the delivery is too late."},
    {"id": "unknown", "title": "Unknown order", "description": "Handle a missing record without inventing an order or creating a return.", "prompt": "Please look up order A999 and start a return for it."},
    {"id": "policy", "title": "Policy boundary", "description": "The gift card is non-returnable. The Python tool must enforce that rule.", "prompt": "Please return my gift card, order A102. I understand it says non-returnable, but I would like you to try."},
    {"id": "outage", "title": "Inventory outage", "description": "Inventory lookups fail. Stop after the configured number of completed tool errors.", "prompt": "Check the stock for Blue hoodie (M). If the service is unavailable, retry until you can check it."},
    {"id": "duplicate", "title": "Repeated return request", "description": "Two calls for the same order must return one database record within the run.", "prompt": "Start a return for my Trail shoes, order A101, because they do not fit. I may have submitted this already; check that there is only one return."},
    {"id": "eligibility", "title": "Eligibility is read-only", "description": "Answer whether the shoes can be returned without creating a return the customer did not request.", "prompt": "Can I return order A101? Please check eligibility only; do not start a return."},
    {"id": "batch_errors", "title": "Error budget in a batch", "description": "Three failing calls in one assistant message show why a threshold can be exceeded by a completed batch.", "prompt": "Check inventory for Blue hoodie (M), Trail shoes (10), and Gift card in one batch."},
]
SCENARIO_BY_ID = {item["id"]: item for item in SCENARIOS}

def _error(code: str, message: str) -> ToolError:
    return ToolError(json.dumps({"error": code, "message": message}))


def _order_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 100:
        raise _error("invalid_order_id", "Provide a non-empty order ID shorter than 100 characters.")
    return value.strip().upper()


def _product_key(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise _error("invalid_product", "Provide a non-empty product name shorter than 200 characters.")
    return " ".join(value.strip().casefold().split())


class Store:
    """Each run owns a fresh in-memory SQLite database, never shared with others."""

    def __init__(self, inventory_count: int = 0, inventory_outage: bool = False, simulate_missing_write: bool = False):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.inventory_outage = inventory_outage
        self.simulate_missing_write = simulate_missing_write
        self.db.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE orders (order_id TEXT PRIMARY KEY, product TEXT NOT NULL,
                status TEXT NOT NULL, eta TEXT NOT NULL, returnable INTEGER NOT NULL);
            CREATE TABLE inventory (product TEXT PRIMARY KEY, product_key TEXT UNIQUE NOT NULL,
                count INTEGER NOT NULL CHECK(count >= 0));
            CREATE TABLE returns (return_id TEXT PRIMARY KEY, order_id TEXT UNIQUE NOT NULL
                REFERENCES orders(order_id), reason TEXT NOT NULL);
        """)
        self.db.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?)", [
            ("A100", "Blue hoodie (M)", "shipped", "Friday", 1),
            ("A101", "Trail shoes (10)", "delivered", "Monday", 1),
            ("A102", "Gift card", "delivered", "Monday", 0),
        ])
        self.db.executemany("INSERT INTO inventory VALUES (?, ?, ?)", [
            (product, _product_key(product), count) for product, count in [
                ("Blue hoodie (M)", inventory_count), ("Trail shoes (10)", 12), ("Gift card", 999)
            ]
        ])
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def snapshot(self) -> dict:
        orders = [dict(row) for row in self.db.execute("SELECT * FROM orders ORDER BY order_id")]
        for row in orders:
            row["returnable"] = bool(row["returnable"])
        return {"orders": orders,
                "inventory": [dict(row) for row in self.db.execute("SELECT product, count FROM inventory ORDER BY product")],
                "returns": [dict(row) for row in self.db.execute("SELECT * FROM returns ORDER BY order_id")]}

    def lookup_order(self, order_id: str) -> dict:
        """Get an existing order by its normalized identifier."""
        normalized = _order_id(order_id)
        row = self.db.execute("SELECT * FROM orders WHERE order_id = ?", (normalized,)).fetchone()
        if row is None:
            raise _error("order_not_found", "No order matches the supplied identifier.")
        result = dict(row)
        result["returnable"] = bool(result["returnable"])
        return result

    def check_inventory(self, product: str) -> dict:
        """Look up replacement stock, preserving a valid zero count."""
        normalized = _product_key(product)
        if self.inventory_outage:
            raise _error("inventory_unavailable", "Inventory service is temporarily unavailable. Stock is unknown.")
        row = self.db.execute("SELECT product, count FROM inventory WHERE product_key = ?", (normalized,)).fetchone()
        if row is None:
            raise _error("product_not_found", "No inventory record matches the supplied product.")
        return dict(row)

    def check_return_eligibility(self, order_id: str) -> dict:
        """Inspect policy without creating a return."""
        order = self.lookup_order(order_id)
        return {"order_id": order["order_id"], "eligible": order["returnable"],
                "reason": "This order is returnable." if order["returnable"] else "Gift cards are non-returnable."}

    def start_return(self, order_id: str, reason: str) -> dict:
        """Enforce policy and persist an idempotent return record."""
        order = self.lookup_order(order_id)
        if not order["returnable"]:
            raise _error("return_not_allowed", "This order is non-returnable. No return was created.")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
            raise _error("invalid_reason", "Provide a non-empty return reason of at most 1000 characters.")
        order_id = order["order_id"]
        previous = self.db.execute("SELECT * FROM returns WHERE order_id = ?", (order_id,)).fetchone()
        if previous is not None:
            return {**dict(previous), "already_exists": True}
        record = {"return_id": "R-" + order_id, "order_id": order_id, "reason": reason.strip()}
        if not self.simulate_missing_write:
            # The UNIQUE constraint remains authoritative even if callers retry.
            self.db.execute("INSERT INTO returns (return_id, order_id, reason) VALUES (?, ?, ?) ON CONFLICT(order_id) DO NOTHING",
                            (record["return_id"], order_id, record["reason"]))
            self.db.commit()
        return {**record, "already_exists": False}


def _build_tools(store: Store, snapshots: list[dict]) -> list:
    """The SDK generates JSON schemas from these type hints and docstrings."""
    def invoke(name: str, **kwargs) -> str:
        try:
            return json.dumps(getattr(store, name)(**kwargs))
        finally:
            snapshots.append({"name": name, "state": store.snapshot()})

    @anthropic.beta_tool
    def lookup_order(order_id: str) -> str:
        """Look up a customer order. Returns product, delivery status, ETA, and returnability.
        Use this to obtain facts before answering an order question. Unknown IDs produce an error.

        Args:
            order_id: Order identifier, for example A100. Whitespace and case are normalized.
        """
        return invoke("lookup_order", order_id=order_id)

    @anthropic.beta_tool
    def check_inventory(product: str) -> str:
        """Check current replacement stock. Returns product and count, including zero.
        Unknown products and service outages produce errors. Never treat an error as stock availability.

        Args:
            product: Product name from an order, such as Blue hoodie (M), Trail shoes (10), or Gift card.
        """
        return invoke("check_inventory", product=product)

    @anthropic.beta_tool
    def start_return(order_id: str, reason: str) -> str:
        """Create a return for an existing returnable order when the customer requests it.
        Enforces policy in Python. Repeated requests reuse the same return record. Returns
        return_id, order_id, reason, and already_exists. Eligibility questions alone do not authorize a return.

        Args:
            order_id: Existing order ID to return.
            reason: A short reason supplied by the customer.
        """
        return invoke("start_return", order_id=order_id, reason=reason)

    @anthropic.beta_tool
    def check_return_eligibility(order_id: str) -> str:
        """Check whether an existing order can be returned without changing any application state.
        Returns eligible and an explanation. Use for questions about whether an order can be returned;
        such a question does not authorize starting a return. Unknown orders produce an error.

        Args:
            order_id: Order ID to inspect.
        """
        return invoke("check_return_eligibility", order_id=order_id)

    return [lookup_order, check_inventory, start_return, check_return_eligibility]


def get_config() -> dict:
    store = Store()
    try:
        initial_state = store.snapshot()
        tool_specs = [tool.to_dict() for tool in _build_tools(store, [])]
    finally:
        store.close()
    return {"live_available": bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()),
            "default_model": os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
            "model_access_verified": False, "sdk_version": anthropic.__version__,
            "scenarios": SCENARIOS, "tools": tool_specs, "initial_state": initial_state,
            "defaults": {"scenario": "normal", "mode": "fixture", "inventory_count": 0,
                         "inventory_outage": False, "simulate_missing_write": False,
                         "max_iterations": 6, "error_threshold": 2},
            "fixture_notice": "Scripted model replies run through the real Anthropic SDK Tool Runner and real Python/SQLite tools. No model call or API charge.",
            "live_notice": "Live mode calls Anthropic with the server's API key. API usage may incur charges. Model access has not been verified."}


def _decode_content(content: Any) -> Any:
    if isinstance(content, list):
        content = "\n".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
    if isinstance(content, str):
        try:
            return json.loads(content)
        except (ValueError, TypeError):
            return content[:4000]
    return content


def _fixture_response(scenario: str, turn: int, body: dict, model: str) -> dict:
    """Script only model messages. All tool results originate in the actual runner."""
    previous = [block for message in body.get("messages", []) if message.get("role") == "user"
                for block in (message.get("content") if isinstance(message.get("content"), list) else [])
                if block.get("type") == "tool_result"]
    values = [_decode_content(block.get("content")) for block in previous]
    successful_returns = [value for value in values if isinstance(value, dict) and "return_id" in value]
    calls: list[tuple[str, dict]] = []
    answer = ""
    if scenario == "normal":
        if turn == 0:
            calls = [("lookup_order", {"order_id": " a100 "}), ("check_inventory", {"product": " blue HOODIE (m) "})]
        elif turn == 1:
            stock = next((value for value in reversed(values) if isinstance(value, dict) and "count" in value), None)
            if stock is None:
                answer = "Order A100 is shipped, with an ETA of Friday. Inventory could not be checked, so I have not assumed stock or started a return."
            elif stock["count"] == 0:
                calls = [("start_return", {"order_id": "A100", "reason": "Delivery is too late"})]
            else:
                answer = f"Order A100 is shipped, with an ETA of Friday. There are {stock['count']} replacement hoodies available. I have not started a return because your condition was that replacements be out of stock."
        else:
            answer = "Order A100 is shipped and due Friday. No replacement hoodie is in stock. The return tool reports return R-A100 created for your order." if successful_returns else "The return could not be started. Please review the tool result."
    elif scenario == "unknown":
        if turn == 0:
            calls = [("lookup_order", {"order_id": "A999"})]
        else:
            answer = "I could not find order A999. No return was created. Please check the order ID."
    elif scenario == "policy":
        if turn == 0:
            calls = [("lookup_order", {"order_id": "A102"})]
        elif turn == 1:
            calls = [("start_return", {"order_id": "A102", "reason": "Customer requested an exception"})]
        else:
            answer = "Order A102 is a non-returnable gift card. The return tool rejected the request, and no return was created."
    elif scenario in ("outage", "batch_errors"):
        names = ["Blue hoodie (M)", "Trail shoes (10)", "Gift card"] if scenario == "batch_errors" else ["Blue hoodie (M)"]
        calls = [("check_inventory", {"product": name}) for name in names]
    elif scenario == "duplicate":
        if turn == 0:
            calls = [("start_return", {"order_id": order_id, "reason": "Shoes do not fit"}) for order_id in ("A101", " a101 ")]
        else:
            answer = "Both calls returned R-A101. The second call found the existing return, so there is one return for order A101." if any(isinstance(value, dict) and value.get("already_exists") for value in values) else "The return tool reported a return, but the second response did not report an existing record. Review the state verification."
    elif scenario == "eligibility":
        if turn == 0:
            calls = [("check_return_eligibility", {"order_id": "A101"})]
        else:
            eligible = next((value.get("eligible") for value in reversed(values) if isinstance(value, dict) and "eligible" in value), False)
            answer = "Order A101 is eligible for a return. I only checked eligibility; no return has been started." if eligible else "The order is not eligible for a return. No return was started."
    content = ([{"type": "tool_use", "id": f"toolu_fixture_{turn}_{index}", "name": name, "input": args}
                for index, (name, args) in enumerate(calls)] if calls else [{"type": "text", "text": answer}])
    return {"id": f"msg_fixture_{turn}", "type": "message", "role": "assistant", "model": model,
            "content": content, "stop_reason": "tool_use" if calls else "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}}


def _int_option(payload: dict, name: str, default: int, low: int, high: int) -> int:
    value = payload.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{name} must be an integer from {low} to {high}.")
    return value


def _bool_option(payload: dict, name: str) -> bool:
    value = payload.get(name, False)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be true or false.")
    return value


def _safe_failure(exc: Exception) -> str:
    # Never return exception bodies, request headers, URLs, or provider credentials.
    if isinstance(exc, anthropic.AuthenticationError):
        return "Anthropic authentication failed. Check the server's API key."
    if isinstance(exc, anthropic.RateLimitError):
        return "Anthropic rate limit reached. Try again later."
    if isinstance(exc, anthropic.APIConnectionError):
        return "Could not connect to Anthropic. Check network access and retry."
    if isinstance(exc, anthropic.APIStatusError):
        return f"Anthropic returned HTTP {exc.status_code}. Check model access and API configuration."
    return "The run stopped because an unexpected backend error occurred. No provider details were exposed."


def _verification(scenario: str, state: dict, records: list[dict], stop_reason: str,
                  completed: bool, inventory_count: int, outage: bool, threshold: int,
                  custom_prompt: bool) -> list[dict]:
    checks: list[dict] = []
    def add(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})
    returns = state["returns"]
    by_order = {row["order_id"]: row for row in returns}
    orders = {row["order_id"]: row for row in state["orders"]}
    add("One return per order", len(by_order) == len(returns), f"Database contains {len(returns)} return record(s) for {len(by_order)} distinct order(s).")
    add("Return policy enforced", all(row["order_id"] in orders and orders[row["order_id"]]["returnable"] for row in returns), "Every stored return must reference an existing returnable order.")
    reported = [record for record in records if record["name"] == "start_return" and not record["is_error"]]
    persisted = all(isinstance(record["result"], dict) and record["result"].get("order_id") in by_order
                    and by_order[record["result"]["order_id"]]["return_id"] == record["result"].get("return_id") for record in reported)
    add("Reported returns really exist", persisted, f"Checked {len(reported)} successful return tool result(s) against SQLite; {len(returns)} record(s) stored.")
    errors = sum(record["is_error"] for record in records)
    add("Error budget respected after batch", errors < threshold or stop_reason == "error_threshold", f"{errors} error(s), threshold {threshold}. The budget is checked after all calls in the current batch.")
    add("Finished or stopped at error budget", completed or stop_reason == "error_threshold", f"Run stop reason: {stop_reason}.")
    if custom_prompt:
        add("Custom prompt: invariant checks only", True, "Scenario-specific expectations are skipped for an edited live prompt; the checks above still inspect real state and trace.")
        return checks
    expected_orders = ({"A100"} if scenario == "normal" and not outage and inventory_count == 0
                       else {"A101"} if scenario == "duplicate" else set())
    add("Only requested return records", set(by_order) == expected_orders,
        f"Expected returned orders: {', '.join(sorted(expected_orders)) or 'none'}; actual: {', '.join(sorted(by_order)) or 'none'}.")
    if scenario == "normal":
        first_write = next((index for index, record in enumerate(records) if record["name"] == "start_return"), None)
        observed = records
        if first_write is not None:
            write_turn = records[first_write]["assistant_turn"]
            # Tools in the same assistant batch can run sequentially, but the
            # model has not yet received their observations when it requests a
            # write in that batch. Require evidence from an earlier turn.
            observed = [record for record in records[:first_write] if record["assistant_turn"] < write_turn]
        inventory_results = [record for record in observed if record["name"] == "check_inventory" and not record["is_error"] and isinstance(record["result"], dict) and record["result"].get("product") == "Blue hoodie (M)"]
        inventory_error = any(record["name"] == "check_inventory" and record["is_error"] and isinstance(record["input"].get("product"), str) and _product_key(record["input"]["product"]) == "blue hoodie (m)" for record in observed)
        correct_order = any(record["name"] == "lookup_order" and not record["is_error"] and isinstance(record["result"], dict) and record["result"].get("order_id") == "A100" for record in observed)
        add("Order and stock inspected", correct_order and bool(inventory_results or (outage and inventory_error)), "A100's status and Blue hoodie (M) inventory must be observed in an earlier assistant turn before requesting a conditional return.")
        expected = not outage and inventory_count == 0
        add("Conditional return outcome", ("A100" in by_order) == expected, "A100 should have a return only when inventory was available to inspect and the stock count was zero.")
    elif scenario == "unknown":
        add("Unknown order handled", not returns and any(record["is_error"] and record["name"] in ("lookup_order", "start_return") for record in records), "The missing order must produce an observed tool error and no stored return.")
    elif scenario == "policy":
        add("Gift card not returned", "A102" not in by_order and any(record["name"] in ("lookup_order", "check_return_eligibility", "start_return") for record in records), "The agent inspected or attempted the gift-card order and no prohibited return is stored.")
    elif scenario == "outage":
        observed_outage = any(record["name"] == "check_inventory" and record["is_error"] and isinstance(record["result"], dict) and record["result"].get("error") == "inventory_unavailable" for record in records)
        add("Outage stopped safely", observed_outage and not returns and (completed or (stop_reason == "error_threshold" and errors >= threshold)), "An observed inventory failure may end with a final answer or the completed-batch error threshold; no return should be created.")
    elif scenario == "batch_errors":
        add("Outage stopped safely", stop_reason == "error_threshold" and errors >= threshold and not returns, "Inventory failures reached the completed-batch threshold without creating returns.")
    elif scenario == "duplicate":
        duplicate_results = [record for record in reported if isinstance(record["result"], dict) and record["result"].get("order_id") == "A101"]
        add("Repeated calls reuse one record", len(duplicate_results) >= 2 and len(returns) == 1 and "A101" in by_order and any(record["result"].get("already_exists") for record in duplicate_results), "At least two successful requests must point to one stored return, with a later result marked already existing.")
    elif scenario == "eligibility":
        inspected = any(record["name"] == "check_return_eligibility" and not record["is_error"] and isinstance(record["result"], dict) and record["result"].get("order_id") == "A101" for record in records)
        add("Eligibility stays read-only", inspected and not returns and not any(record["name"] == "start_return" for record in records), "Checking whether A101 can be returned must not initiate a return.")
    return checks


def run_lab(payload: dict, emit: Callable[[dict], None] | None = None, *, credentials: dict | None = None) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("The request must be a JSON object.")
    mode, scenario = payload.get("mode", "fixture"), payload.get("scenario", "normal")
    if mode not in ("fixture", "live"):
        raise ValueError("mode must be fixture or live.")
    if scenario not in SCENARIO_BY_ID:
        raise ValueError("Unknown scenario.")
    count = _int_option(payload, "inventory_count", 0, 0, 9999)
    maximum = _int_option(payload, "max_iterations", 6, 1, 12)
    threshold = _int_option(payload, "error_threshold", 2, 1, 8)
    outage = _bool_option(payload, "inventory_outage") or scenario in ("outage", "batch_errors")
    missing_write = _bool_option(payload, "simulate_missing_write")
    default_prompt = SCENARIO_BY_ID[scenario]["prompt"]
    supplied_prompt = payload.get("prompt", default_prompt)
    if not isinstance(supplied_prompt, str) or not supplied_prompt.strip() or len(supplied_prompt) > 8000:
        raise ValueError("prompt must contain between 1 and 8000 characters.")
    # Fixed fixtures cannot honestly respond to an arbitrary edited prompt.
    prompt = default_prompt if mode == "fixture" else supplied_prompt.strip()
    custom_prompt = mode == "live" and prompt != default_prompt
    # This keyword-only argument is supplied by the server's private session.
    # Never read credentials from the public payload or include them in results.
    private = credentials if credentials is not None else {}
    if not isinstance(private, dict):
        raise ValueError("Invalid private connection configuration.")
    model_value = private.get("model") or os.environ.get("ANTHROPIC_MODEL") or DEFAULT_MODEL
    key_value = private.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not isinstance(model_value, str) or not isinstance(key_value, str):
        raise ValueError("Invalid private connection configuration.")
    model = model_value.strip() or DEFAULT_MODEL
    api_key = key_value.strip()
    if mode == "live" and not api_key:
        raise ValueError("Live mode requires ANTHROPIC_API_KEY in the server environment.")
    start = time.perf_counter()
    events: list[dict] = []
    records: list[dict] = []
    run_id = uuid.uuid4().hex
    metrics = {"assistant_messages": 0, "tool_calls": 0, "errors": 0, "duration_ms": 0,
               "input_tokens": 0, "output_tokens": 0}
    store = Store(count, outage, missing_write)
    initial_state = store.snapshot()
    snapshots: list[dict] = []
    def event(kind: str, title: str, detail: str, data: dict | None = None) -> None:
        item = {"id": len(events) + 1, "type": kind, "title": title, "detail": detail,
                "data": data or {}, "elapsed_ms": round((time.perf_counter() - start) * 1000, 2)}
        events.append(item)
        if emit:
            try:
                emit(item)
            except (BrokenPipeError, ConnectionResetError):
                # The browser is gone. Close this run's database immediately;
                # the runner's finally block still closes its HTTP client.
                store.close()
                raise

    tools = _build_tools(store, snapshots)
    http_client = None
    client = None
    completed, stop_reason, answer = False, "iteration_limit", ""
    try:
        if mode == "fixture":
            turn = 0
            def respond(request):
                nonlocal turn
                body = json.loads(request.content)
                reply = _fixture_response(scenario, turn, body, model)
                turn += 1
                return httpx.Response(200, json=reply, headers={"content-type": "application/json", "request-id": f"fixture-{turn}"})
            http_client = httpx.Client(transport=httpx.MockTransport(respond))
            client = anthropic.Anthropic(api_key="fixture-key-not-a-real-credential", base_url="https://fixture.invalid", http_client=http_client, max_retries=0)
        else:
            client = anthropic.Anthropic(api_key=api_key, max_retries=0, timeout=45)
        runner = client.beta.messages.tool_runner(
            model=model, max_tokens=1600, max_iterations=maximum, run_tools_eagerly=False,
            tools=tools, messages=[{"role": "user", "content": prompt}],
            system="You are a support agent in a fictional local training store. Use tools for order facts and mutations. Respect return policy. Never invent stock or a return. Only create returns requested by the customer. For a repeated return request, call start_return again to check idempotent behavior. Tool errors are observations, not successful actions. Give a concise answer grounded in tool results.",
        )
        for message in runner:
            metrics["assistant_messages"] += 1
            metrics["input_tokens"] += int(getattr(message.usage, "input_tokens", 0) or 0)
            metrics["output_tokens"] += int(getattr(message.usage, "output_tokens", 0) or 0)
            blocks = message.content
            calls = [block for block in blocks if block.type == "tool_use"]
            text = "\n".join(block.text for block in blocks if block.type == "text")
            event("model_request", f"Assistant turn {metrics['assistant_messages']}",
                  f"{'Scripted fixture' if mode == 'fixture' else 'Live model'} returned {message.stop_reason}.",
                  {"stop_reason": message.stop_reason, "text": text, "tool_count": len(calls), "model": model,
                   "fixture": mode == "fixture", "usage": {"input_tokens": getattr(message.usage, "input_tokens", 0), "output_tokens": getattr(message.usage, "output_tokens", 0)}})
            # Never dispatch tool blocks from an incomplete/refused response.
            if message.stop_reason != "tool_use":
                stop_reason = message.stop_reason or "incomplete"
                answer = text
                completed = stop_reason == "end_turn" and not calls and bool(text.strip())
                if calls:
                    completed, stop_reason = False, "incomplete"
                break
            if not calls:
                stop_reason = "incomplete"
                break
            for call in calls:
                event("tool_call", call.name, "The SDK is about to dispatch this requested tool call.",
                      {"name": call.name, "input": call.input, "tool_use_id": call.id})
            metrics["tool_calls"] += len(calls)
            snapshots.clear()
            # Exactly one explicit generation per batch. The SDK caches it and
            # reuses it on the next iteration rather than executing tools twice.
            result_message = runner.generate_tool_call_response()
            batch = (result_message or {}).get("content", [])
            results_by_id = {result["tool_use_id"]: result for result in batch if result.get("type") == "tool_result"}
            snapshot_index = 0
            for call in calls:
                raw = results_by_id.get(call.id, {"content": "No matching tool result.", "is_error": True})
                result = _decode_content(raw.get("content"))
                is_error = bool(raw.get("is_error", False))
                metrics["errors"] += int(is_error)
                snapshot = store.snapshot()
                if snapshot_index < len(snapshots) and snapshots[snapshot_index]["name"] == call.name:
                    snapshot = snapshots[snapshot_index]["state"]
                    snapshot_index += 1
                record = {"name": call.name, "input": call.input, "result": result,
                          "is_error": is_error, "tool_use_id": call.id, "state": snapshot,
                          "assistant_turn": metrics["assistant_messages"]}
                records.append(record)
                event("tool_result", f"{call.name}: {'error' if is_error else 'result'}",
                      "Tool failed; the error is returned to the runner." if is_error else "Python function completed; inspect its result and the database snapshot.", record)
            if metrics["errors"] >= threshold:
                stop_reason = "error_threshold"
                answer = f"Stopped after {metrics['errors']} tool errors. The threshold is checked after each completed batch."
                break
        else:
            stop_reason = "iteration_limit"
            answer = "The iteration limit was reached before a final answer. Review the executed actions before retrying."
    except (BrokenPipeError, ConnectionResetError):
        # Do not emit a second event to the same disconnected stream.
        raise
    except Exception as exc:
        stop_reason, completed = "error", False
        answer = _safe_failure(exc)
        event("error", "Run stopped", answer, {"error_type": type(exc).__name__})
    finally:
        if client is not None:
            client.close()
        elif http_client is not None:
            http_client.close()
    try:
        state = store.snapshot()
        checks = _verification(scenario, state, records, stop_reason, completed, count, outage, threshold, custom_prompt)
        for check in checks:
            event("verification", check["name"], check["detail"], check)
        metrics["duration_ms"] = round((time.perf_counter() - start) * 1000, 2)
        event("complete", "Run complete" if completed else "Run stopped", answer,
              {"completed": completed, "stop_reason": stop_reason, "all_checks_passed": all(check["passed"] for check in checks)})
        return {"run_id": run_id, "mode": mode, "scenario": scenario, "prompt": prompt,
                "answer": answer, "completed": completed, "stop_reason": stop_reason,
                "events": events, "initial_state": initial_state, "state": state, "checks": checks,
                "metrics": metrics, "model": model, "fixture": mode == "fixture",
                "fixture_prompt_ignored": mode == "fixture" and supplied_prompt != default_prompt,
                "options": {"inventory_count": count, "inventory_outage": outage, "simulate_missing_write": missing_write,
                            "max_iterations": maximum, "error_threshold": threshold},
                "token_usage_note": "No model tokens used; fixture usage is zero." if mode == "fixture" else "Reported by the Anthropic API."}
    finally:
        store.close()


def run_checks() -> list[dict]:
    """Fast deterministic checks exercise the same Python functions as the SDK."""
    results = []
    store = Store()
    try:
        normalized = store.lookup_order(" a100 ")["order_id"] == "A100"
        zero = store.check_inventory(" BLUE HOODIE (m) ")["count"] == 0
        results.append({"name": "Normalized IDs and zero stock", "passed": normalized and zero,
                        "detail": "Whitespace/case normalization preserves an existing order and valid zero inventory."})
        first = store.start_return("A101", "Does not fit")
        second = store.start_return(" a101 ", "Duplicate request")
        results.append({"name": "Idempotent return persistence", "passed": first["return_id"] == second["return_id"] and second["already_exists"] and len(store.snapshot()["returns"]) == 1,
                        "detail": "Repeated calls return the same record and SQLite stores one return."})
        rejected = False
        try:
            store.start_return("A102", "Please make an exception")
        except ToolError:
            rejected = True
        results.append({"name": "Policy enforced by Python", "passed": rejected and len(store.snapshot()["returns"]) == 1,
                        "detail": "Non-returnable gift cards cannot create a return even through a direct function call."})
    finally:
        store.close()
    return results


def get_curriculum() -> dict:
    """Teaching content for the guided Tool Runner course, independent of UI state."""
    return {
        "title": "Learn the Claude Tool Runner",
        "description": "Follow one support request from a tool schema to a verified database change. Fix two pieces of Python, inspect failures, and prove the result.",
        "fixture_note": "Scripted Claude replies pass through the real SDK, which dispatches real Python tools. No API key is needed. This teaches and tests the Tool Runner mechanism, not the quality of a live model's decisions.",
        "steps": [
            {"id": "loop", "title": "1. Follow the conversation", "objective": "Identify who chooses a tool and who executes it.",
             "why": "Claude returns a request to use a tool. Your application runs the function and sends its result back. That observation lets Claude decide what to do next.",
             "instruction": "Run Rescue an order with zero hoodie stock. Find the lookup and inventory calls, the returned observations, then the return call. Answer who executes the Python function.",
             "action": {"type": "run", "scenario": "normal", "scenarios": ["normal"], "label": "Run the first conversation"},
             "quiz": {"key": "executor", "question": "Who executes the Python tool function?", "options": [
                 {"value": "model", "label": "Claude executes it on Anthropic's servers"},
                 {"value": "application", "label": "The SDK calls it inside our application"},
                 {"value": "browser", "label": "The browser interprets the tool schema"}]}},
            {"id": "tool", "title": "2. Make a dependable tool", "objective": "Write a function that distinguishes zero stock from a missing product.",
             "why": "The SDK derives the tool's name, inputs, and description from your typed function and docstring. It does not decide what your inventory data means. A valid zero count must survive your Python logic.",
             "instruction": "Open the inventory exercise. Complete its function, normalize the product name, preserve a zero stock count, and run the exercise checks. Inspect the generated tool schema.",
             "action": {"type": "exercise", "exercise": "inventory", "label": "Complete the inventory tool"}},
            {"id": "runner", "title": "3. Let the runner manage turns", "objective": "Construct a bounded Tool Runner and verify its message plumbing.",
             "why": "Tool Runner coordinates API calls, dispatches tool requests, and appends matching tool results to the conversation. Your code supplies the functions and limits. You return the runner; iterating it advances the conversation without manually executing the tools a second time.",
             "instruction": "Open the runner exercise. Pass the supplied model, tools, and user question to the SDK Tool Runner; set max_tokens to 512 and max_iterations to 4. Return the runner and run the checks, which inspect the executed function and the next API request.",
             "action": {"type": "exercise", "exercise": "runner", "label": "Complete the runner loop"}},
            {"id": "writes", "title": "4. Verify the write", "objective": "Confirm a successful tool call changed state, and retries do not create duplicates.",
             "why": "A friendly response is not evidence of a database write. The application owns persistence and return policy. A unique order constraint makes a repeated return request reuse one record within this run.",
             "instruction": "Run Rescue an order with zero stock, then Repeated return request. Inspect the SQLite state in each run: A100 should have one return in the first, and two requests for A101 should produce one return in the second.",
             "action": {"type": "run", "scenario": "duplicate", "scenarios": ["normal", "duplicate"], "label": "Test repeated requests"}},
            {"id": "failures", "title": "5. Understand the error budget", "objective": "Explain why a completed batch can exceed the error threshold.",
             "why": "An assistant message may request several tools. The runner executes that batch before your error counter is checked. With a threshold of two, three failing tools in one batch can produce three errors before the loop stops.",
             "instruction": "Run Inventory outage, then Error budget in a batch with threshold two. Compare the trace. The second fixture places three errors in a single batch so you can inspect the threshold overshoot.",
             "action": {"type": "run", "scenario": "batch_errors", "scenarios": ["outage", "batch_errors"], "label": "Observe a failing batch"},
             "quiz": {"key": "budget", "question": "When does this lab check its tool-error threshold?", "options": [
                 {"value": "before_each_tool", "label": "Before every individual tool in a batch"},
                 {"value": "after_batch", "label": "After all tool results in the current batch"},
                 {"value": "after_answer", "label": "Only after the final answer"}]}},
            {"id": "false_success", "title": "6. Catch a false success", "objective": "Detect a tool that reports success without recording the return, then repair the run.",
             "why": "The SDK can deliver a result accurately even when your function is wrong. A verifier must compare the claimed return ID against actual state; checking only the final answer would miss this defect.",
             "instruction": "Turn on the missing-write fault and run Rescue an order with zero stock. Locate the failed persistence check. Turn the fault off and rerun the same scenario until the stored return and tool result agree.",
             "action": {"type": "fault", "scenario": "normal", "scenarios": ["normal"], "fault": "simulate_missing_write", "label": "Expose the false success"}},
            {"id": "capstone", "title": "7. Test the boundaries", "objective": "Verify product policy and detect writes during a read-only request.",
             "why": "Python rejects returns for non-returnable products. The eligibility verifier detects an unintended write after execution; it is not an authorization gate. Tool availability and a system prompt do not establish customer authorization. Production applications should restrict available write tools or require approval before executing a write.",
             "instruction": "Run Policy boundary and Eligibility is read-only. Confirm both end with zero return records, and eligibility never calls start_return. Explain which guarantees came from Python and which actions came from the scripted assistant messages.",
             "action": {"type": "run", "scenario": "policy", "scenarios": ["policy", "eligibility"], "label": "Run the boundary checks"}},
        ],
        "api_key_required": False,
        "live_optional": True,
        "optional_live": {"title": "Try real model decisions", "description": "The full course works without a key. Optionally connect your Anthropic API key to send the same tools to live Claude, change the prompt, and compare its chosen calls with the repeatable fixtures. Live API usage may incur charges; it is not required for any checkpoint."},
    }


def evaluate_checkpoint(step_id: str, payload: dict) -> dict:
    """Evaluate server-owned session evidence, never client-supplied pass flags.

    The HTTP layer must build payload from its saved runs and exercise results.
    This function verifies their trace/state relationships, not browser checkboxes.
    """
    step_ids = {step["id"] for step in get_curriculum()["steps"]}
    if step_id not in step_ids:
        raise ValueError("Unknown curriculum checkpoint.")
    if not isinstance(payload, dict):
        raise ValueError("Checkpoint evidence must be an object.")
    runs = [run for run in payload.get("runs", []) if isinstance(run, dict)]
    answers = payload.get("answers", {})
    exercises = payload.get("exercises", {})
    answers = answers if isinstance(answers, dict) else {}
    exercises = exercises if isinstance(exercises, dict) else {}
    checks: list[dict] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    def results(run: dict, name: str | None = None) -> list[dict]:
        return [event["data"] for event in run.get("events", [])
                if isinstance(event, dict) and event.get("type") == "tool_result"
                and isinstance(event.get("data"), dict)
                and (name is None or event["data"].get("name") == name)]

    def preset(run: dict, scenario: str) -> bool:
        return (run.get("scenario") == scenario and run.get("mode") in ("fixture", "live")
                and run.get("prompt") == SCENARIO_BY_ID[scenario]["prompt"])

    def ended(run: dict) -> bool:
        return (run.get("completed") is True and run.get("stop_reason") == "end_turn"
                and isinstance(run.get("answer"), str) and bool(run["answer"].strip()))

    def all_checks_pass(run: dict) -> bool:
        evidence = run.get("checks", [])
        return bool(evidence) and all(isinstance(check, dict) and check.get("passed") is True for check in evidence)

    def returned_orders(run: dict) -> list[str]:
        return [row.get("order_id") for row in run.get("state", {}).get("returns", [])]

    def clean_options(run: dict) -> bool:
        options = run.get("options", {})
        return options.get("simulate_missing_write") is False and options.get("inventory_outage") is False

    def normal_success(run: dict) -> bool:
        if not (preset(run, "normal") and ended(run) and clean_options(run) and all_checks_pass(run)
                and run.get("options", {}).get("inventory_count") == 0 and returned_orders(run) == ["A100"]):
            return False
        order = any(not item.get("is_error") and isinstance(item.get("result"), dict)
                    and item["result"].get("order_id") == "A100" for item in results(run, "lookup_order"))
        stock = any(not item.get("is_error") and isinstance(item.get("result"), dict)
                    and item["result"].get("product") == "Blue hoodie (M)" and item["result"].get("count") == 0
                    for item in results(run, "check_inventory"))
        row = run["state"]["returns"][0]
        write = any(not item.get("is_error") and isinstance(item.get("result"), dict)
                    and item["result"].get("order_id") == "A100" and item["result"].get("return_id") == row.get("return_id")
                    for item in results(run, "start_return"))
        return order and stock and write

    def exercise_passed(name: str) -> bool:
        value = exercises.get(name, {})
        if not isinstance(value, dict) or value.get("passed") is not True:
            return False
        # The server executes the exercise and owns this result. If individual
        # check results are supplied, each must also pass.
        detail = value.get("checks", [])
        return isinstance(detail, list) and all(isinstance(check, dict) and check.get("passed") is True for check in detail)

    if step_id == "loop":
        add("Application executes tools", answers.get("executor") == "application", "Claude chooses a call; the SDK invokes the Python function inside your application.")
        add("Observed a complete loop", any(normal_success(run) for run in runs), "Complete Rescue an order with zero stock, the fault off, and one verified A100 return.")
    elif step_id in ("tool", "runner"):
        name = "inventory" if step_id == "tool" else "runner"
        add("Exercise checks pass", exercise_passed(name), f"Save the {name} exercise and run its server-side checks.")
    elif step_id == "writes":
        add("Verified the first write", any(normal_success(run) for run in runs), "A successful normal run must match its return result to an actual A100 database record.")
        duplicate = False
        for run in runs:
            if not (preset(run, "duplicate") and ended(run) and all_checks_pass(run) and clean_options(run) and returned_orders(run) == ["A101"]):
                continue
            calls = [item for item in results(run, "start_return") if not item.get("is_error") and isinstance(item.get("result"), dict) and item["result"].get("order_id") == "A101"]
            row = run["state"]["returns"][0]
            duplicate |= (len(calls) >= 2 and all(item["result"].get("return_id") == row.get("return_id") for item in calls)
                          and any(item["result"].get("already_exists") is True for item in calls))
        add("Retries reuse the stored return", duplicate, "Two successful A101 requests must reference one stored record, with a later result reporting it already exists.")
    elif step_id == "failures":
        safe_outage, batch_overshoot = False, False
        for run in runs:
            scenario = run.get("scenario")
            if scenario not in SCENARIO_BY_ID or not preset(run, scenario):
                continue
            errors = [item for item in results(run) if item.get("is_error")]
            if run.get("scenario") == "outage":
                observed = any(isinstance(item.get("result"), dict) and item["result"].get("error") == "inventory_unavailable" for item in errors)
                safe_outage |= observed and not returned_orders(run) and all_checks_pass(run) and (ended(run) or run.get("stop_reason") == "error_threshold")
            if run.get("scenario") == "batch_errors":
                threshold = run.get("options", {}).get("error_threshold", 2)
                # Count tool-result events between assistant messages to prove
                # that one completed batch, rather than total retries, overshot.
                batches: list[int] = []
                for event in run.get("events", []):
                    if event.get("type") == "model_request":
                        batches.append(0)
                    elif event.get("type") == "tool_result" and event.get("data", {}).get("is_error") and batches:
                        batches[-1] += 1
                batch_overshoot |= (run.get("stop_reason") == "error_threshold" and not returned_orders(run)
                                    and all_checks_pass(run) and isinstance(threshold, int) and any(count > threshold for count in batches))
        add("Observed a safe outage", safe_outage, "Run Inventory outage and inspect its actual inventory error without a return being created.")
        add("Observed a batch overshoot", batch_overshoot, "Run Error budget in a batch with a threshold below the three failing tool calls, then inspect the completed batch.")
        add("Budget checked after the batch", answers.get("budget") == "after_batch", "The threshold is checked after this batch's tools finish, so it is not a hard per-tool cap.")
    elif step_id == "false_success":
        faulty_indices = []
        for index, run in enumerate(runs):
            if not (preset(run, "normal") and run.get("options", {}).get("simulate_missing_write") is True and ended(run) and not returned_orders(run)):
                continue
            claimed_write = any(not item.get("is_error") and isinstance(item.get("result"), dict) and item["result"].get("return_id") for item in results(run, "start_return"))
            detected = any(check.get("name") == "Reported returns really exist" and check.get("passed") is False for check in run.get("checks", []))
            if claimed_write and detected:
                faulty_indices.append(index)
        repaired = any(normal_success(run) and any(fault_index < index for fault_index in faulty_indices) for index, run in enumerate(runs))
        add("Detected an unrecorded return", bool(faulty_indices), "With the missing-write fault on, a successful return result must disagree with empty database state and fail the persistence check.")
        add("Repaired it in a later run", repaired, "After observing that failure, turn off the fault and complete the normal zero-stock run with a verified return.")
    elif step_id == "capstone":
        policy, eligibility = False, False
        for run in runs:
            if not (ended(run) and all_checks_pass(run) and not returned_orders(run)):
                continue
            if preset(run, "policy"):
                evidence = [item for item in results(run) if isinstance(item.get("result"), dict)]
                policy |= any((item["result"].get("order_id") == "A102" and (item["result"].get("returnable") is False or item["result"].get("eligible") is False))
                              or (item.get("name") == "start_return" and item.get("is_error") and item["result"].get("error") == "return_not_allowed") for item in evidence)
            if preset(run, "eligibility"):
                eligibility |= (not results(run, "start_return") and any(not item.get("is_error") and isinstance(item.get("result"), dict) and item["result"].get("order_id") == "A101" and item["result"].get("eligible") is True for item in results(run, "check_return_eligibility")))
        add("Policy holds", policy, "Inspect the non-returnable gift-card policy and finish without creating any return.")
        add("Eligibility remains read-only", eligibility, "Inspect A101's eligibility without calling start_return or creating a record.")
    passed = bool(checks) and all(check["passed"] for check in checks)
    pending = [check["detail"] for check in checks if not check["passed"]]
    return {"step_id": step_id, "passed": passed, "checks": checks,
            "feedback": "Checkpoint complete. The saved trace and state support this result." if passed else " ".join(pending)}
