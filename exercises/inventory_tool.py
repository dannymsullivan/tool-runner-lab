"""Exercise 1: turn a Python function into a reliable inventory tool.

Run: python exercises/check.py --exercise inventory
Edit this file, then rerun the check. Do not edit the checker.
"""

import json

from anthropic import beta_tool

INVENTORY = {"blue-hoodie": 0, "trail-shoes": 12}


@beta_tool
def check_inventory(product: str) -> str:
    """TODO: Explain what this tool returns, including unknown products.

    Args:
        product: TODO: Explain the product name and normalization rules.
    """
    # TODO 1: Strip surrounding whitespace, lowercase, and join words with '-'.
    # TODO 2: Check membership in INVENTORY. Zero stock is a valid result.
    # TODO 3: Unknown -> JSON {"error": "unknown_product", "product": normalized}.
    # TODO 4: Known -> JSON {"product": normalized, "count": n, "in_stock": n > 0}.
    raise NotImplementedError("Complete the inventory tool, then check your work.")
