"""Evaluator-only behavior checks, frozen before either agent runs."""
import argparse
from decimal import Decimal, ROUND_HALF_UP, localcontext
import importlib
import json
from pathlib import Path
import sys
import traceback

parser = argparse.ArgumentParser()
parser.add_argument('--repo', required=True)
args = parser.parse_args()
root = Path(args.repo).resolve()
sys.path.insert(0, str(root))
from shop.cart import Item, subtotal_cents
from shop.checkout import checkout
from shop.pricing import discounted_total_cents
from shop.quote import quote
from shop.receipts import receipt_text

checks = []


def run(name, category, operation):
    try:
        operation()
        checks.append({'name': name, 'category': category, 'passed': True})
    except Exception as exc:
        checks.append({'name': name, 'category': category, 'passed': False,
                       'error': f'{type(exc).__name__}: {exc}'})


def equal(actual, expected):
    assert actual == expected, f'expected {expected!r}, observed {actual!r}'


class Gateway:
    def __init__(self):
        self.charges = []

    def charge(self, amount):
        self.charges.append(amount)
        return 'evaluation-payment'


def quote_boundary():
    equal(quote([Item('tiny order', 5)], 50),
          {'subtotal_cents': 5, 'total_cents': 3})


def payment_boundary():
    gateway = Gateway()
    result = checkout([Item('tiny order', 5)], gateway, 50)
    equal(result, {'charged_cents': 3, 'payment_id': 'evaluation-payment'})
    equal(gateway.charges, [3])


def precise_large_orders():
    with localcontext() as context:
        context.prec = 80
        for subtotal in [10**18 + 5, 10**24 + 39, 2**62 + 1]:
            for discount in [0, 1, 25, 50, 99, 100]:
                expected = int((Decimal(subtotal) * Decimal(100 - discount) /
                                Decimal(100)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
                observed = discounted_total_cents(subtotal, discount)
                equal(observed, expected)
                assert type(observed) is int


def boundary_matrix():
    expected_values = [(1, 50, 1), (5, 50, 3), (25, 50, 13),
                       (3, 25, 2), (7, 10, 6), (199, 50, 100),
                       (0, 50, 0), (9, 0, 9), (9, 100, 0)]
    for subtotal, discount, expected in expected_values:
        equal(discounted_total_cents(subtotal, discount), expected)


def order_level_rounding():
    items = [Item('first', 1), Item('second', 1)]
    equal(quote(items, 50)['total_cents'], 1)
    gateway = Gateway()
    equal(checkout(items, gateway, 50)['charged_cents'], 1)
    equal(gateway.charges, [1])


def validation():
    for subtotal, discount in [(-1, 0), (100, -1), (100, 101)]:
        try:
            discounted_total_cents(subtotal, discount)
        except ValueError:
            continue
        raise AssertionError('invalid price input no longer rejected')
    gateway = Gateway()
    try:
        checkout([Item('invalid', 10, 0)], gateway)
    except ValueError:
        equal(gateway.charges, [])
    else:
        raise AssertionError('invalid cart no longer rejected before charging')


def original_normal_order():
    items = [Item('book', 1000, 2)]
    equal(subtotal_cents(items), 2000)
    equal(quote(items, 10), {'subtotal_cents': 2000, 'total_cents': 1800})
    gateway = Gateway()
    equal(checkout(items, gateway, 10),
          {'charged_cents': 1800, 'payment_id': 'evaluation-payment'})
    equal(gateway.charges, [1800])
    equal(receipt_text(items, 10), 'Total: $18.00')


def default_and_free():
    for subtotal in [0, 1, 99, 2000]:
        equal(discounted_total_cents(subtotal), subtotal)
        equal(discounted_total_cents(subtotal, 100), 0)
        assert type(discounted_total_cents(subtotal)) is int


run('reported half-cent bug', 'requested', lambda: equal(discounted_total_cents(5, 50), 3))
run('rounding boundary matrix', 'requested', boundary_matrix)
run('large integer precision', 'requested', precise_large_orders)
run('quote consumer', 'requested', quote_boundary)
run('payment consumer and single charge', 'requested', payment_boundary)
run('receipt consumer', 'requested', lambda: equal(receipt_text([Item('tiny order', 5)], 50), 'Total: $0.03'))
run('order-level rounding', 'protected', order_level_rounding)
run('input validation and no invalid charge', 'protected', validation)
run('existing normal quote/payment/receipt', 'protected', original_normal_order)
run('default, zero and free order behavior', 'protected', default_and_free)
result = {
    'repository': str(root),
    'requested_bug_fixed': all(row['passed'] for row in checks if row['category'] == 'requested'),
    'protected_behavior_passed': all(row['passed'] for row in checks if row['category'] == 'protected'),
    'checks': checks,
    'passed': sum(row['passed'] for row in checks),
    'failed': sum(not row['passed'] for row in checks),
}
print(json.dumps(result, indent=2))
raise SystemExit(0 if not result['failed'] else 1)
