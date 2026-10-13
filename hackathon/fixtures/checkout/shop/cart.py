from dataclasses import dataclass


@dataclass(frozen=True)
class Item:
    name: str
    unit_cents: int
    quantity: int = 1


def subtotal_cents(items: list[Item]) -> int:
    total = 0
    for item in items:
        if item.unit_cents < 0 or item.quantity < 1:
            raise ValueError("invalid cart item")
        total += item.unit_cents * item.quantity
    return total
