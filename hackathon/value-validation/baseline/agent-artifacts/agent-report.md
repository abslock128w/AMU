# Baseline agent report

## Cause and fix

The original calculation at `repo/shop/pricing.py:7` divided by 100 using floating-point arithmetic and then called Python's `round`, which rounds exact ties to the even integer. Consequently, a five-cent subtotal with a 50% discount became 2 cents instead of 3. The floating-point conversion also lost integer precision at 9007199254740993 cents and raised `OverflowError` for a subtotal of `10 ** 400 + 5` with a 50% discount.

The replacement at `repo/shop/pricing.py:7` is `(subtotal_cents * (100 - discount_percent) + 50) // 100`. Multiplication keeps the exact numerator in hundredths of a cent; adding 50 before integer division rounds to the nearest cent with ties upward. The documented domain is nonnegative integer cents and integer percentages from 0 through 100 (`repo/README.md:3`). The existing validation at `repo/shop/pricing.py:3` and `repo/shop/pricing.py:5` is unchanged. The docstring at `repo/shop/pricing.py:2` now states the rounding rule.

Only the shared pricing computation and its regression tests were changed. No dependencies or features were added.

## Dependencies identified from local source

- `repo/shop/cart.py:11` defines `subtotal_cents`; its validation at line 14 precedes integer multiplication and accumulation at line 16.
- `repo/shop/quote.py:2` imports the pricing function. `quote` computes the cart subtotal at line 6, applies the order-level discount at line 7, and returns both amounts at line 8.
- `repo/shop/checkout.py:2` imports the same pricing function. Line 6 prices the summed cart, line 7 passes that result to `gateway.charge`, and line 8 returns the charged amount.
- `repo/shop/receipts.py:2` imports `quote`. Line 6 reads the quoted total, and line 7 formats the amount for display.
- `repo/tests/test_orders.py:10` supplies a local gateway double whose `charge` method records the amount at line 15. The checkout regression verifies both the returned charge and the amount passed to the gateway.

These dependencies were found by inspecting ordinary local source files. No graph-product tools or evaluator reports were used.

## Tests selected and actual outcomes

All test runs used the assigned Python executable and the command recorder, with `-B -m unittest discover -s tests -v`.

1. `baseline-tests`: all 8 original tests passed, exit 0.
2. `regressions-before-fix`: 15 tests ran; 7 assertion failures and 1 error were reported, exit 1. This reproduced half-cent rounding failures directly and through quotes, gateway charges, and receipts, plus precision loss and overflow on large integer subtotals.
3. `tests-after-fix`: all 15 tests passed, exit 0. The seven new test methods cover ties upward (`repo/tests/test_orders.py:26`), values just below and above half a cent (line 31), exact large-integer pricing and a huge free order (line 37), zero subtotals (line 46), aggregate order-level rounding in quotes (line 67), the actual charged amount (line 79), and displayed receipt totals (line 88). Original undiscounted, exact-discount, free-order, and invalid-input checks also passed.
4. `inspect-final-diff`: `git diff --check` passed, exit 0, with no output.
5. `review-final-patch`: inspected the final source/test diff, exit 0.

## Limits and transcript

Tests used the existing local gateway double and did not contact external services. Large integer arithmetic was verified directly at the pricing function; receipt formatting for enormous amounts was not tested. The receipt's existing floating-point currency display at `repo/shop/receipts.py:7` was not changed because this request concerns order-level discount arithmetic.

The recorder's command JSON files in this artifacts directory contain the actual arguments, outputs, exit codes, and durations. Source changes were made with `apply_patch`; the final patch is captured separately by the comparison harness. No independent evaluator was read or run, and no evaluator result is claimed. Token/model usage was not measured by this run.
