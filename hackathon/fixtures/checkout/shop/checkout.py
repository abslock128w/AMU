from .cart import Item, subtotal_cents
from .pricing import discounted_total_cents


def checkout(items: list[Item], gateway, discount_percent: int = 0) -> dict:
    total = discounted_total_cents(subtotal_cents(items), discount_percent)
    payment_id = gateway.charge(total)
    return {"charged_cents": total, "payment_id": payment_id}
