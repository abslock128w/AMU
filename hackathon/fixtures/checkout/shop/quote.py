from .cart import Item, subtotal_cents
from .pricing import discounted_total_cents


def quote(items: list[Item], discount_percent: int = 0) -> dict:
    subtotal = subtotal_cents(items)
    total = discounted_total_cents(subtotal, discount_percent)
    return {"subtotal_cents": subtotal, "total_cents": total}
