def discounted_total_cents(subtotal_cents: int, discount_percent: int = 0) -> int:
    """Return the total after applying an order-level percentage discount."""
    if subtotal_cents < 0:
        raise ValueError("subtotal must be nonnegative")
    if not 0 <= discount_percent <= 100:
        raise ValueError("discount must be between 0 and 100")
    return round(subtotal_cents * (100 - discount_percent) / 100)
