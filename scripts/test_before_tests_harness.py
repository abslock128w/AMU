"""Validate scoring mechanics, never product accuracy."""
import unittest
from before_tests_harness import functions


class GroundTruthTests(unittest.TestCase):
    def test_shared_parameter_data_is_part_of_test(self):
        before = 'values = [1]\n@pytest.mark.parametrize("x", values)\ndef test_value(x):\n    assert x\n'
        after = before.replace('[1]', '[1, 2]')
        self.assertNotEqual(functions(before, 'tests/test_x.py'), functions(after, 'tests/test_x.py'))

    def test_line_shifts_do_not_modify_tests(self):
        before = 'def test_value():\n    assert True\n'
        self.assertEqual(functions(before, 'tests/test_x.py'), functions('\n'+before, 'tests/test_x.py'))

    def test_nested_class_nodeid(self):
        source = 'class TestThing:\n    def test_value(self):\n        pass\n'
        self.assertEqual(list(functions(source, 'tests/test_x.py')), ['tests/test_x.py::TestThing::test_value'])


if __name__ == '__main__':
    unittest.main()
