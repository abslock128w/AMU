"""Protected fixture checks; class state deliberately persists between checks."""

import unittest

from cache_case import TypedCache


class TestTypedCache(unittest.TestCase):
    def test_repeat_integer_identity(self):
        first = TypedCache.lookup(1)
        second = TypedCache.lookup(1)
        self.observed = {"same_identity": first is second, "kind": first.kind, "value": first.value}
        self.assertIs(first, second)
        self.assertEqual(first.kind, "int")
        self.assertIs(type(first.value), int)
        self.assertEqual(first.value, 1)

    def test_repeat_string_identity(self):
        first = TypedCache.lookup("1")
        second = TypedCache.lookup("1")
        self.observed = {"same_identity": first is second, "kind": first.kind, "value": first.value}
        self.assertIs(first, second)
        self.assertEqual(first.kind, "str")
        self.assertIs(type(first.value), str)
        self.assertEqual(first.value, "1")

    def test_distinct_integer_and_string(self):
        integer = TypedCache.lookup(1)
        string = TypedCache.lookup("1")
        self.observed = {
            "kinds": [integer.kind, string.kind],
            "values": [integer.value, string.value],
            "same_identity": integer is string,
        }
        self.assertIsNot(integer, string)
        self.assertEqual(integer.kind, "int")
        self.assertIs(type(integer.value), int)
        self.assertEqual(integer.value, 1)
        self.assertEqual(string.kind, "str")
        self.assertIs(type(string.value), str)
        self.assertEqual(string.value, "1")

    def test_none_is_rejected(self):
        with self.assertRaises(ValueError):
            TypedCache.lookup(None)
        self.observed = {"raised": "ValueError"}
