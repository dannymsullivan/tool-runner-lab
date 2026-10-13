"""Reference answer. Edit exercises/inventory_tool.py for the exercise."""

import json

from anthropic import beta_tool

INVENTORY = {"blue-hoodie": 0, "trail-shoes": 12}


@beta_tool
def check_inventory(product: str) -> str:
    """Look up replacement inventory. Return JSON with count and in_stock.

    An unknown product returns an error; a known product may have zero stock.

    Args:
        product: Product name or slug. Surrounding whitespace and case are ignored;
            spaces between words are converted to hyphens, such as Blue Hoodie.
    """
    normalized = "-".join(product.strip().lower().split())
    if normalized not in INVENTORY:
        return json.dumps({"error": "unknown_product", "product": normalized})
    count = INVENTORY[normalized]
    return json.dumps({"product": normalized, "count": count, "in_stock": count > 0})
