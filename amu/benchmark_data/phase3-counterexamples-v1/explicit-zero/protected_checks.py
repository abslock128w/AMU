"""Literal checks for omitted, zero, positive, and invalid limit inputs."""

import unittest

from case import effective_limit


def probe():
    return {"value": effective_limit(0, 5)}


class ProtectedChecks(unittest.TestCase):
    def test_omitted_uses_default(self):
        self.observed = {"value": effective_limit(None, 5)}
        self.assertEqual(self.observed, {"value": 5})

    def test_explicit_zero_is_kept(self):
        self.observed = {"value": effective_limit(0, 5)}
        self.assertEqual(self.observed, {"value": 0})

    def test_positive_is_kept(self):
        self.observed = {"value": effective_limit(3, 5)}
        self.assertEqual(self.observed, {"value": 3})

    def test_boolean_is_rejected(self):
        try:
            effective_limit(False, 5)
        except ValueError:
            self.observed = {"raised": "ValueError"}
        else:
            self.observed = {"raised": None}
        self.assertEqual(self.observed, {"raised": "ValueError"})
