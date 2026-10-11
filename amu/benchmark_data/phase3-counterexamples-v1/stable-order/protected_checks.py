"""Literal checks for first-seen order and duplicate removal."""

import unittest

from case import unique_in_order


def probe():
    return {"values": unique_in_order([3, 1, 3, 2])}


class ProtectedChecks(unittest.TestCase):
    def test_first_seen_numeric_order(self):
        self.observed = {"values": unique_in_order([3, 1, 3, 2])}
        self.assertEqual(self.observed, {"values": [3, 1, 2]})

    def test_first_seen_text_order(self):
        self.observed = {"values": unique_in_order(["b", "a", "b"])}
        self.assertEqual(self.observed, {"values": ["b", "a"]})

    def test_empty_input(self):
        self.observed = {"values": unique_in_order([])}
        self.assertEqual(self.observed, {"values": []})

    def test_repeat_single_value(self):
        self.observed = {"values": unique_in_order([4, 4, 4])}
        self.assertEqual(self.observed, {"values": [4]})
