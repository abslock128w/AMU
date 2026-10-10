# Mini checkout

A small synthetic order-pricing service. Amounts are nonnegative integer cents,
and discount percentages are integers from 0 through 100. The service produces
quotes, charges a gateway, and formats a receipt.

Run available tests from this directory:

```sh
python3 -B -m unittest discover -s tests -v
```

No third-party packages or network services are required.
