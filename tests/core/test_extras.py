"""Features still behind a flag (build queue part 4): refactor labels, exploration order, mutation, equivalence."""
import difflib
import sys

from amu.ci.render import render
from amu.core import analyze, equivalence, mutate, refactor, select


def diff(path, before, after):
    return ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), 'a/' + path, 'b/' + path))


def write(root, files):
    for name, text in files.items():
        (root/name).parent.mkdir(parents=True, exist_ok=True)
        (root/name).write_text(text)
    return root


def test_renames_and_moves_are_labelled_and_a_changed_body_is_not():
    before = ('def total(xs):\n    """Sum."""\n    return sum(xs)\n\n\ndef keep(x):\n    return x\n\n\n'
              'class Box:\n    def size(self):\n        return 1\n\n    def more(self):\n        return 2\n')
    after_lib = 'def keep(x):\n    return x + 0\n\n\ndef grand_total(xs):\n    """Add them up."""\n    return sum(xs)\n'
    after_box = 'class Box:\n    def size(self):\n        return 1\n\n    def more(self):\n        return 2\n'
    rows = refactor.label({'lib.py': (before, after_lib), 'box.py': (None, after_box)})
    assert {(r['kind'], r['from'], r['to']) for r in rows} == {
        ('rename', 'lib.py#total', 'lib.py#grand_total'), ('move', 'lib.py#Box', 'box.py#Box')}, rows
    assert all(r['label'] == 'refactor, behaviour preserved if tests pass' for r in rows)
    # Two equal candidates: no way to tell which it became, so no label.
    twin = 'def a(x):\n    return x\n\n\ndef b(x):\n    return x\n'
    assert refactor.label({'m.py': ('def old(x):\n    return x\n', twin)}) == []


def test_never_run_tests_get_a_tenth_of_the_order_when_exploring():
    measured = [f't{i}' for i in range(30)]
    fresh = ['n0', 'n1', 'n2']
    durations = {t: 1.0 for t in measured}
    tiebreak = {t: i for i, t in enumerate(measured + fresh)}
    plain = select.order(measured + fresh, durations=durations, tiebreak=tiebreak)
    assert plain[-3:] == fresh
    explored = select.order(measured + fresh, durations=durations, tiebreak=tiebreak, explore=0.1)
    assert [explored.index(n) for n in fresh] == [9, 19, 29] and sorted(explored) == sorted(plain)
    assert select.order(measured + fresh, touched=['n2'], durations=durations, tiebreak=tiebreak, explore=0.1)[0] == 'n2'


def test_mutants_change_one_operator_and_leave_logging_and_configuration_alone():
    source = ('import logging\nLIMIT = 10\n\n\ndef check(x, y):\n    logging.info("x %s", x == y)\n'
              '    ok = x < LIMIT and y > 0\n    return x + y if ok else 0\n')
    made = mutate.mutants(source, {2, 6, 7, 8})
    lines = {line for line, _, _ in made}
    assert lines == {7, 8}, made
    kinds = {what.split(':')[0] for _, what, _ in made}
    assert {'comparison', 'boolean', 'arithmetic', 'return value'} <= kinds, kinds
    for _, _, text in made:
        compile(text, 'm.py', 'exec')
        assert text.count('\n') == source.count('\n')


LIB0 = 'def clamp(v, top):\n    return v\n'
LIB1 = 'def clamp(v, top):\n    if v > top:\n        return top\n    return v\n'


def test_a_mutant_the_tests_do_not_notice_is_listed_and_one_they_catch_is_not(tmp_path):
    lib0, lib1 = 'def shift(x):\n    return x\n', 'def shift(x):\n    y = x + 2\n    return y\n'
    weak = 'from lib import shift\n\n\ndef test_shift():\n    assert shift(0) >= -5\n'
    strong = 'from lib import shift\n\n\ndef test_shift():\n    assert shift(0) == 2\n'
    for name, tests, survives in (('weak', weak, True), ('strong', strong, False)):
        root = write(tmp_path/name, {'lib.py': lib1, 'tests/test_lib.py': tests})
        report = analyze(root, diff('lib.py', lib0, lib1), full_evidence=True, index_dir=tmp_path/name/'.index', run=True,
                         python=sys.executable, runner='pytest', extras=('mutation',))
        found = report['mutation']
        assert found['mutants'] >= 2 and found['covered_lines'] == 2, found
        # x + 2 -> x - 2 and 2 -> 3 still pass >= -5; return y -> return None does not compare.
        expected = ['arithmetic: + -> -', 'constant: 2 -> 3'] if survives else []
        assert sorted(s['mutant'] for s in found['survivors']) == expected, (name, found)
        body = render(report, dict(head='a' * 40))
        assert ('Tests that would not notice (2)' in body) is survives, body
    assert (tmp_path/'weak'/'lib.py').read_text() == lib1  # the tree is left as it was


def test_only_annotated_pure_functions_are_compared_for_equivalence():
    assert equivalence.eligible('def f(x: int) -> int:\n    return x + 1\n', 'f')
    assert not equivalence.eligible('def f(x) -> int:\n    return x + 1\n', 'f')
    assert not equivalence.eligible('def f(x: int) -> int:\n    print(x)\n    return x\n', 'f')
    assert not equivalence.eligible('def f(x: list) -> None:\n    x[0] = 1\n', 'f')
    assert not equivalence.eligible('def f(x: int) -> int:\n    global y\n    return x\n', 'f')


def test_time_to_the_first_flip_and_labels_are_reported_only_when_asked(tmp_path):
    root = write(tmp_path, {'lib.py': LIB1, 'tests/test_lib.py': 'from lib import clamp\n\n\ndef test_high():\n    assert clamp(9, 5) == 9\n'})
    report = analyze(root, diff('lib.py', LIB0, LIB1), full_evidence=True, index_dir=tmp_path/'.index', run=True, python=sys.executable,
                     runner='pytest', extras=('refactor', 'explore'))
    assert report['flips'] == 1 and report['first_flip_seconds'] >= 0, report.get('first_flip_seconds')
    assert report['refactors'] == [] and 'mutation' not in report and 'equivalence' not in report
    plain = analyze(root, diff('lib.py', LIB0, LIB1), full_evidence=True, index_dir=tmp_path/'.index', run=True, python=sys.executable,
                    runner='pytest')
    assert 'refactors' not in plain and 'mutation' not in plain


def test_the_comment_shows_renames_and_differing_inputs():
    report = dict(flips=0, executed=dict(ran=3, candidates=3, runner='pytest'), head='a' * 40,
                  refactors=[{'kind': 'rename', 'from': 'lib.py#total', 'to': 'lib.py#grand_total', 'label': refactor.LABEL}],
                  equivalence=dict(rows=[dict(target='lib.py#f', tool='crosshair', input='x=3', before='returns 1', after='returns 2')]))
    body = render(report, dict(head='a' * 40), quiet=True)
    assert 'Renamed or moved, nothing else (1)' in body and 'grand_total' in body
    assert 'An input on which the two versions differ (1)' in body and 'x=3' in body
