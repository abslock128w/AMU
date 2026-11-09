"""When this job's run of the code before the change sees a test run a changed line that the suite recording says the
test did not run, coverage depends on test order in this suite, and the report says so (evidence.order_dependent)."""
import types

from amu.core.evidence import order_dependent


def recording(lines, changed=()):
    tests = sorted({t for ts in lines.values() for t in ts} | {'tests/test_a.py::test_other'})
    return types.SimpleNamespace(data=dict(tests=tests), changed=set(changed),
                                 tests=lambda path, text, wanted: {t for n in wanted for t in lines.get(n, ())})


HUNKS = [dict(path='lib.py', index=0, base=[10, 11])]


def test_a_test_running_a_line_the_recording_says_it_did_not_is_order_dependent():
    whole = recording({10: {'tests/test_a.py::test_one'}})
    coverage = {'tests/test_a.py::test_one': {'lib.py': {10}}, 'tests/test_a.py::test_other': {'lib.py': {10, 11}}}
    assert order_dependent(whole, HUNKS, {'lib.py': ''}, coverage) == ['tests/test_a.py::test_other']


def test_agreement_tests_the_recording_does_not_know_and_changed_test_files_are_not():
    whole = recording({10: {'tests/test_a.py::test_one'}, 99: {'tests/test_b.py::test_changed'}}, changed=['tests/test_b.py'])
    coverage = {'tests/test_a.py::test_one': {'lib.py': {10}}, 'tests/test_new.py::test_new': {'lib.py': {10}},
                'tests/test_b.py::test_changed': {'lib.py': {11}}}
    assert order_dependent(whole, HUNKS, {'lib.py': ''}, coverage) == []
    assert order_dependent(None, HUNKS, {'lib.py': ''}, coverage) == []
