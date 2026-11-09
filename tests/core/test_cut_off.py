"""After a run that did not give every candidate test an outcome, a hunk no run test reached is not recorded, never
"ran in no test" (amu.core.incomplete, states.classify). Synthetic sources only."""
from amu.core import incomplete, states
from amu.core.hunks import split
from amu.core.patch import changes

BEFORE = 'def f(x):\n    return x + 1\n\n\ndef g(x):\n    return x * 2\n'
AFTER = 'def f(x):\n    return x + 2\n\n\ndef g(x):\n    return x * 3\n'
EDIT = ('--- a/lib.py\n+++ b/lib.py\n@@ -1,6 +1,6 @@\n def f(x):\n-    return x + 1\n+    return x + 2\n \n \n'
        ' def g(x):\n-    return x * 2\n+    return x * 3\n')


def rows(tmp_path):
    (tmp_path/'lib.py').write_text(AFTER)
    edits, _ = changes(tmp_path, EDIT)
    found = split('lib.py', edits['lib.py'], BEFORE, AFTER)
    recorded = [dict(h, head_tests=(['tests/test_lib.py::test_g'] if 'g' in (h.get('new_function') or '') else []),
                     base_tests=[], head_ran_lines=[6] if 'g' in (h.get('new_function') or '') else [])
                for h in found]
    return recorded


def test_a_cut_off_run_leaves_unreached_hunks_not_recorded(tmp_path):
    why = incomplete(dict(ran=640, candidates=13207, unrunnable=0), [])
    assert why == 'the run stopped after 640 of 13207 candidate tests'
    out = states.classify(tmp_path, changes(tmp_path, EDIT)[0], {'lib.py': BEFORE}, {'lib.py': AFTER}, rows(tmp_path), incomplete=why)
    by = {row['function']: row for row in out}
    assert by['f']['state'] == 'not-recorded' and by['f']['why'] == why, out
    assert by['g']['state'] != 'not-recorded', out


def test_a_complete_run_still_says_ran_in_no_test(tmp_path):
    assert incomplete(dict(ran=13207, candidates=13207), []) is None
    assert incomplete(dict(ran=99, candidates=100, unrunnable=1), []) is None  # a test the runner cannot collect
    out = states.classify(tmp_path, changes(tmp_path, EDIT)[0], {'lib.py': BEFORE}, {'lib.py': AFTER}, rows(tmp_path))
    assert {row['function']: row['state'] for row in out}['f'] == 'executed-none', out


def test_a_lost_chunk_is_incomplete():
    gaps = ["shadow run (after the change, pytest): no outcomes read: AttributeError: x"]
    assert incomplete(dict(ran=279, candidates=679, unrunnable=400), gaps) == '400 of 679 candidate tests gave no outcome'
