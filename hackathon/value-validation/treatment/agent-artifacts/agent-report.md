# Discount-rounding fix

## Discovered cause

The original `shop/pricing.py:7` used `round(subtotal_cents * (100 - discount_percent) / 100)`. Python's `round` uses ties-to-even, so 2.5 cents became 2 rather than the requested 3. The division also converted the integer calculation to floating point, losing cents above its exact-integer range and overflowing for sufficiently large amounts.

The recorded reproduction returned 2 for `(5, 50)`, 9007199254740992 for the undiscounted subtotal 9007199254740993, and 4503599627370496 for that subtotal at 50%. The original eight tests passed despite these defects.

## Dependencies actually identified

Source inspection and the corrected AMU search/graph/impact calls established these relationships:

- `shop/cart.py:11` computes the order subtotal; `shop/cart.py:14` validates item prices and quantities, and `shop/cart.py:16` aggregates integer prices and quantities.
- `shop/quote.py:6` obtains that subtotal, and `shop/quote.py:7` calls the pricing function before returning the total at line 8.
- `shop/checkout.py:6` calls pricing, then `shop/checkout.py:7` passes the resulting integer cents to `gateway.charge`. Validation therefore happens before this external interface is invoked.
- `shop/receipts.py:6` obtains the total through `quote`, then line 7 formats the receipt. This is an indirect pricing consumer.
- `shop/pricing.py:3` and `shop/pricing.py:5` contain the existing negative-subtotal and percentage-range validation.

AMU identified quote and checkout as direct callers, receipt text as an indirect caller, and the existing pricing/integration tests as consumers. An initial graph query used an unsupported depth of 3 and an incorrect colon target separator; the impact query also rejected depth 3. These were corrected to depth 2 and `shop/pricing.py#discounted_total_cents`. The corrected graph reported status `ok`; the corrected impact report was `inspectable` and still explicitly did not assess behavioral safety. Its coverage section was inspected after adding the regressions. No graph result was treated as a substitute for executing tests. AMU's suggested pytest/build commands were not selected; the supplied unittest suite is directly runnable and executes behavior.

## Patch decisions

Changed only the pricing expression and its docstring in production code. `shop/pricing.py:7` now computes `(subtotal_cents * (100 - discount_percent) + 50) // 100`. For the documented nonnegative integer domain, adding 50 before dividing by 100 gives nearest-cent rounding with exact halves upward, using arbitrary-precision integer arithmetic throughout. Zero and 100% discounts remain exact. Both existing validation branches remain unchanged.

No caller modifications, dependencies, new features, deployment changes, or changes to the original AMU project were made.

## Selected tests and actual results

Added seven regression methods to `tests/test_orders.py`:

- Line 26: below-half, exact-half, and above-half rounding, including both formerly correct odd ties and formerly incorrect even ties.
- Line 40: exact totals for `2**53 + 1` and `10**400 + 5` at 0%, 50%, and 100% discount, covering precision loss and float overflow.
- Line 48: zero subtotals at 0%, 50%, and 100% discount.
- Line 69: multiple cart items and quantities are aggregated before order-level rounding.
- Line 79: checkout returns and actually charges 3 cents for a 5-cent order at 50%.
- Line 85: invalid cart/discount inputs raise before the gateway is charged.
- Line 98: the receipt displays `$0.03` for that half-cent case.

All test commands used the assigned Python 3.14 interpreter, `-B -m unittest discover -s tests -v`, through the command recorder:

1. `baseline-tests`: 8 tests passed, exit 0 (`command-1791615701940472000.json`).
2. `regression-tests-before-fix`: 15 tests ran, with 6 assertion failures and 1 overflow error, exit 1 (`command-1791615748062456000.json`). The failures demonstrated the rounding defect in pricing, quote, checkout, and receipts, plus large-integer precision loss.
3. `final-tests`: all 15 tests passed, exit 0 (`command-1791615764745372000.json`).
4. `final-whitespace-check`: `git diff --check` passed, exit 0 (`command-1791615764722491000.json`). The final diff was also reviewed.

The initial source-reading command attempted to pass multiple files to macOS `nl`, which rejected that usage; the following recorded `rg -n` read succeeded. This did not affect source or test execution.

## Limits and protocol notes

The test outcomes above are this agent's actual local observations; no independent evaluator was read or run. Integer precision is verified for the pricing result. The existing receipt formatter still uses floating-point dollar formatting; changing arbitrary-size receipt display is outside this order-discount calculation fix. The gateway in tests is the existing in-memory test double, with no network activity.

The session-listed systematic-debugging skill was read because the higher-priority session instructions required it. That instructional read was outside the assigned fixture and appears in the recorded command trace. No other condition roots, experiment/evaluator reports, unrelated project source, AMU package internals, or setup/preflight files were inspected. Every shell command used the assigned recorder. Token/model usage was not available; elapsed time is captured by the recorder's start/finish entries.
