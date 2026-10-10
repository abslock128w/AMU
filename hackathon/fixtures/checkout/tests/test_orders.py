import unittest

from shop.cart import Item
from shop.checkout import checkout
from shop.pricing import discounted_total_cents
from shop.quote import quote
from shop.receipts import receipt_text


class Gateway:
    def __init__(self):
        self.charges = []

    def charge(self, amount):
        self.charges.append(amount)
        return "payment-1"


class OrderTests(unittest.TestCase):
    def test_default_price(self):
        self.assertEqual(discounted_total_cents(2000), 2000)

    def test_exact_discount(self):
        self.assertEqual(discounted_total_cents(2000, 10), 1800)

    def test_free_order(self):
        self.assertEqual(discounted_total_cents(2000, 100), 0)

    def test_invalid_subtotal(self):
        with self.assertRaises(ValueError):
            discounted_total_cents(-1, 10)

    def test_invalid_discount(self):
        for percent in [-1, 101]:
            with self.assertRaises(ValueError):
                discounted_total_cents(100, percent)

    def test_quote(self):
        self.assertEqual(quote([Item("book", 1000, 2)], 10),
                         {"subtotal_cents": 2000, "total_cents": 1800})

    def test_checkout(self):
        gateway = Gateway()
        self.assertEqual(checkout([Item("book", 1000, 2)], gateway, 10),
                         {"charged_cents": 1800, "payment_id": "payment-1"})
        self.assertEqual(gateway.charges, [1800])

    def test_receipt(self):
        self.assertEqual(receipt_text([Item("book", 1000, 2)], 10), "Total: $18.00")


if __name__ == "__main__":
    unittest.main()
