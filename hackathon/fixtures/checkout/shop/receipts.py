from .cart import Item
from .quote import quote


def receipt_text(items: list[Item], discount_percent: int = 0) -> str:
    total = quote(items, discount_percent)["total_cents"]
    return f"Total: ${total / 100:.2f}"
