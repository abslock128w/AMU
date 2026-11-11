"""C1: model-written probes for changed functions no test calls, kept only on execution evidence. The model is faked."""
import difflib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from amu.core import analyze, generate


class FakeAPI:
    """Stands in for anthropic.Anthropic(): returns the probes given, with a usage bill, and records each request."""

    def __init__(self, probes, input_tokens=4000, output_tokens=1500, stop_reason='end_turn'):
        self.calls = []
        self.reply = SimpleNamespace(stop_reason=stop_reason, usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
                                     content=[SimpleNamespace(type='text', text=json.dumps(dict(probes=probes)))])
        self.messages = SimpleNamespace(create=self.create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **params):
        self.calls.append(params)
        return self.reply


LIB0 = 'def tested(x):\n    return x\n\n\ndef scale(x):\n    return x * 2\n'
LIB1 = 'def tested(x):\n    return x + 0\n\n\ndef scale(x):\n    return x * 3 if x > 10 else x * 2\n'
TESTS = 'from lib import tested\n\n\ndef test_tested():\n    assert tested(1) == 1\n'


def run(tmp_path, probes, monkeypatch, dollars=0.10, on_disk=True, **fake):
    for name, text in {'lib.py': LIB1 if on_disk else LIB0, 'tests/test_lib.py': TESTS}.items():
        (tmp_path/name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path/name).write_text(text)
    api = FakeAPI(probes, **fake)
    monkeypatch.setattr(generate, 'client', lambda: api)
    diff = ''.join(difflib.unified_diff(LIB0.splitlines(True), LIB1.splitlines(True), 'a/lib.py', 'b/lib.py'))
    report = analyze(tmp_path, diff, full_evidence=True, index_dir=tmp_path/'.index', run=True, python=sys.executable,
                     generate=(dollars, 5, 'claude-opus-5-5'))
    return report, api


def test_only_probes_that_pass_every_filter_are_kept_and_their_outcomes_come_from_execution(tmp_path, monkeypatch):
    probes = [dict(function='lib.py#scale', setup='from lib import scale', call='scale(50)', why='reaches the new branch'),
              dict(function='lib.py#scale', setup='from lib import scale', call='scale(1)', why='old branch, same either way'),
              dict(function='lib.py#scale', setup='import nothing_like_this', call='1', why='setup fails'),
              dict(function='lib.py#scale', setup='from lib import scale', call='scale(', why='does not compile'),
              dict(function='lib.py#other', setup='', call='1', why='not a changed function: dropped')]
    report, api = run(tmp_path, probes, monkeypatch)
    made = report['generated']
    assert (made['proposed'], made['compiled'], made['ran'], made['stable'], made['reached'], made['accepted']) == (4, 3, 2, 2, 2, 1), made
    [row] = report['generated_differences']
    assert row['label'] == 'generated test shows a difference' and row['call'] == 'scale(50)'
    assert row['before'][::2] == ['return', '100'] and row['after'][::2] == ['return', '150'], row
    assert "assert repr(scale(50)) == '150'" in row['test']
    # The probe whose setup fails never reached its call: the report says so, with what the runner said.
    [gap] = [g for g in report['gaps'] if 'did not run' in g]
    assert gap.startswith('generated tests: 1 of 3 probes did not run on the head (') and 'nothing_like_this' in gap, gap
    # The request: every changed function, also one the tests call (they compare it only on the inputs they pass),
    # structured output, the default fallback.
    [params] = api.calls
    assert 'lib.py#scale' in params['messages'][0]['content']
    assert 'lib.py#tested' in params['messages'][0]['content']
    assert params['output_config']['format']['type'] == 'json_schema' and params['fallbacks'] == 'default'
    assert made['cost'] == round((4000 * 4 + 1500 * 20) / 1e6, 5)
    from amu.ci.render import render
    body = render(report, dict(head='a' * 40, merge_base='b' * 40))
    # A changed return value is not a new failure: it is listed, collapsed, under the heading that counts new failures.
    assert "### Generated tests: calls that worked before and now fail in the project's code (0)" in body, body
    assert 'Other generated tests whose outcome differs (1)' in body and '`scale(50)`' in body, body


@pytest.mark.parametrize('on_disk', [True, False], ids=['change-on-disk', 'code-before-on-disk'])
def test_an_outcome_that_depends_on_where_the_tree_lives_is_not_a_difference(tmp_path, monkeypatch, on_disk):
    # sympy #30701: update_args_with_paths returned absolute paths, so its outcome held each tree's own folder and
    # differed between the two copies with no change in behaviour.
    setup = 'import os\nimport lib\nfrom lib import scale'
    probes = [dict(function='lib.py#scale', setup=setup, call='(scale(50), lib.__file__)', why='a real difference'),
              dict(function='lib.py#scale', setup=setup, call='(scale(1), lib.__file__)', why='the tree as imported'),
              dict(function='lib.py#scale', setup=setup, call='(scale(1), os.path.abspath(__file__))', why='this file'),
              dict(function='lib.py#scale', setup=setup, call='(scale(1), os.getcwd())', why='the tree, resolved'),
              dict(function='lib.py#scale', setup=setup, call='(scale(1), os.path.basename(os.getcwd()))', why='its name')]
    report, _ = run(tmp_path, probes, monkeypatch, on_disk=on_disk)
    made, rows = report['generated'], report['generated_differences']
    assert [row['call'] for row in rows] == ['(scale(50), lib.__file__)'], rows
    # The folder's name is not a spelling of the tree: the run from a second folder is what drops that one.
    assert (made['ran'], made['stable'], made['reached'], made['accepted']) == (5, 4, 4, 1), made
    [row] = rows
    assert row['before'][2] == "(100, '<tree>/lib.py')" and row['after'][2] == "(150, '<tree>/lib.py')", row
    assert row['test'] == '', row['test']  # a committed test cannot know the tree's folder


def test_the_code_before_runs_at_the_path_the_changed_code_ran_from(tmp_path, monkeypatch):
    # Review: a folder's name, or the hash order of paths into it, is the same in both head folders and differed from
    # the folder the code before ran in, so such probes were kept with no change in behaviour.
    setup = 'import os\nfrom lib import scale'
    probes = [dict(function='lib.py#scale', setup=setup, call='(scale(1), os.getcwd().endswith("head"))', why='the name')]
    report, _ = run(tmp_path, probes, monkeypatch)
    assert report['generated']['reached'] == 1 and report['generated_differences'] == [], report['generated']

def test_the_tree_spellings_include_the_link_and_the_resolved_path(tmp_path):
    real = tmp_path/'real'
    real.mkdir()
    (tmp_path/'link').symlink_to(real, target_is_directory=True)
    names = generate._spellings(tmp_path/'link')
    assert str(tmp_path/'link') in names and os.path.realpath(real) in names
    assert names == sorted(names, key=len, reverse=True)
    if os.path.realpath('/var') == '/private/var':  # macOS: /var is a link into /private
        assert '/var/folders/x' in generate._spellings(Path('/private/var/folders/x'))


def test_no_request_crosses_the_dollar_cap_and_nothing_runs_without_a_client(tmp_path, monkeypatch):
    report, api = run(tmp_path, [], monkeypatch, dollars=0.01)
    assert api.calls == [] and report['generated']['cost'] == 0 and any('dollar cap' in g for g in report['gaps']), report['gaps']
    monkeypatch.setattr(generate, 'client', lambda: None)
    for name, text in {'lib.py': LIB1, 'tests/test_lib.py': TESTS}.items():
        (tmp_path/name).write_text(text)
    diff = ''.join(difflib.unified_diff(LIB0.splitlines(True), LIB1.splitlines(True), 'a/lib.py', 'b/lib.py'))
    report = analyze(tmp_path, diff, full_evidence=True, index_dir=tmp_path/'.index', run=True, python=sys.executable,
                     generate=(0.10, 5, 'claude-opus-5-5'))
    assert any('no Anthropic SDK or credentials' in g for g in report['gaps'])


def test_a_declined_request_is_a_gap_and_the_project_tests_never_see_the_key(tmp_path, monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-value-not-a-key')
    from amu.core import runners
    assert 'ANTHROPIC_API_KEY' not in runners.environment(tmp_path)
    report, _ = run(tmp_path, [], monkeypatch, stop_reason='refusal')
    assert any('declined' in g for g in report['gaps']) and report['generated']['accepted'] == 0


def test_test_file_functions_and_docstring_only_changes_get_no_probes_and_the_report_lists_them(tmp_path, monkeypatch):
    # Found in the retrospective (defect 8): the tier was asked for probes of an edited test method (Django #21820
    # test_invalid_view) and of functions whose only change is the docstring (sympy #30530, #30697, #30734).
    from amu.core import shadow
    lib0 = 'def scale(x):\n    return x * 2\n\n\ndef doc(x):\n    """Twice x."""\n    return x * 2\n'
    lib1 = 'def scale(x):\n    return x * 3 if x > 10 else x * 2\n\n\ndef doc(x):\n    """Twice ``x``; never a float."""\n    return x * 2\n'
    own0 = 'from lib import doc\n\n\ndef test_doc():\n    assert doc(1) == 2\n'
    own1 = own0.replace('doc(1) == 2', 'doc(2) == 4')
    other = 'from lib import doc\n\n\ndef test_other():\n    assert doc(3) == 6\n'
    before = {'lib.py': lib0, 'tests/test_lib.py': own0, 'tests/test_other.py': other}
    after = {'lib.py': lib1, 'tests/test_lib.py': own1, 'tests/test_other.py': other}
    diff = ''.join(''.join(difflib.unified_diff(before[n].splitlines(True), after[n].splitlines(True), 'a/' + n, 'b/' + n))
                   for n in after if before[n] != after[n])
    probes = [dict(function='lib.py#scale', setup='from lib import scale', call='scale(50)', why='reaches the new branch')]
    unchanged, ran_side = 'only its annotations, decorators, docstring or layout changed', []
    # With side-by-side execution (Python 3.12+) and without it (below 3.12, simulated here).
    for monitoring in (shadow._monitoring, lambda python: False):
        monkeypatch.setattr(shadow, '_monitoring', monitoring)
        for name, text in after.items():
            (tmp_path/name).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path/name).write_text(text)
        api = FakeAPI(probes)
        monkeypatch.setattr(generate, 'client', lambda: api)
        report = analyze(tmp_path, diff, full_evidence=True, index_dir=tmp_path/'.index', run=True, python=sys.executable,
                         generate=(0.10, 5, 'claude-opus-5-5'))
        [params] = api.calls
        asked = [line[3:] for line in params['messages'][0]['content'].splitlines() if line.startswith('## ')]
        assert asked == ['lib.py#scale'] and report['generated']['functions'] == 1, (asked, report['generated'])
        assert [row['call'] for row in report['generated_differences']] == ['scale(50)'], report['generated_differences']
        # What was left out of the probes, and why, is in the report with the tier.
        side = report.get('side_by_side')
        assert report['generated']['not_probed'] == {'lib.py#doc': unchanged, 'tests/test_lib.py#test_doc': 'in a test file'}
        ran_side.append(side is not None)
    assert ran_side == [sys.version_info >= (3, 12), False]


def test_targets_reach_nested_functions_and_generators_and_leave_out_what_runs_the_same():
    before = {'lib.py': 'def outer(x):\n    def inner(y):\n        return y\n    return inner(x)\n\n\n'
                        'def items(xs):\n    return [x for x in xs]\n\n\n'
                        'def typed(x):\n    return x\n\n\nasync def wait(x):\n    return x\n',
              'tests/test_lib.py': 'def test_a():\n    assert 1\n'}
    after = {'lib.py': 'def outer(x):\n    def inner(y):\n        return y + 1\n    return inner(x)\n\n\n'
                       'def items(xs):\n    for x in xs:\n        yield x\n\n\n'
                       'def typed(x: int) -> int:\n    """Typed."""\n    return x\n\n\nasync def wait(x):\n    return x + 1\n\n\n'
                       'def added():\n    return 1\n',
             'tests/test_lib.py': 'def test_a():\n    assert 2\n'}
    rows = [dict(file='lib.py', symbol=s) for s in ('outer.<locals>.inner', 'items', 'typed', 'wait', 'added')]
    rows.append(dict(file='tests/test_lib.py', symbol='test_a'))
    found, skipped = generate.targets(rows, before, after)
    # The nested function is probed through the function that holds it; a function that became a generator is a target.
    assert sorted(t['id'] for t in found) == ['lib.py#items', 'lib.py#outer'], found
    assert 'return y + 1' in next(t for t in found if t['id'] == 'lib.py#outer')['after']
    assert skipped == {'lib.py#typed': 'only its annotations, decorators, docstring or layout changed', 'lib.py#wait': 'coroutine',
                       'lib.py#added': 'not on both sides (new or removed)', 'tests/test_lib.py#test_a': 'in a test file'}


def test_targets_are_the_most_changed_functions_up_to_the_cap_and_the_rest_are_listed(monkeypatch):
    monkeypatch.setattr(generate, 'MAX_TARGETS', 2)
    before = {'lib.py': ''.join(f'def f{i}(x):\n    return x\n\n\n' for i in range(3))}
    after = {'lib.py': 'def f0(x):\n    return x + 1\n\n\ndef f1(x):\n    if x:\n        return x * 2\n    return [x, x]\n\n\n'
                       'def f2(x):\n    return x - (x + 1)\n\n\n'}
    found, skipped = generate.targets([dict(file='lib.py', symbol=f'f{i}') for i in range(3)], before, after)
    assert [t['id'] for t in found] == ['lib.py#f1', 'lib.py#f2'], [(t['id'], t['weight']) for t in found]
    assert skipped == {'lib.py#f0': 'more than 2 changed functions: the 2 most changed were probed'}
    # Fewer functions get more probes each.
    assert [generate.probes_per_function(n) for n in (1, 2, 3, 6, 7, 40)] == [8, 8, 5, 5, 3, 3]


def test_a_django_probe_is_hosted_in_the_test_app_it_imports_from(tmp_path):
    (tmp_path/'tests'/'queries').mkdir(parents=True)
    (tmp_path/'tests'/'queries'/'__init__.py').write_text('')
    probes = [(0, dict(setup='from queries.models import Number', call='Number.objects.none().union()')),
              (1, dict(setup='from django.utils.text import slugify', call="slugify('a b')")),
              (2, dict(setup='import queries.models as m', call='m.Number'))]
    files, ids = generate.test_file('django-runtests', probes, tmp_path)
    assert sorted(files) == ['tests/amu_generated/tests.py', 'tests/queries/test_amu_generated.py']
    assert ids == {0: 'tests/queries/test_amu_generated.py::AmuGeneratedTests::test_amu_probe_0',
                   1: 'tests/amu_generated/tests.py::AmuGeneratedTests::test_amu_probe_1',
                   2: 'tests/queries/test_amu_generated.py::AmuGeneratedTests::test_amu_probe_2'}
    for source in files.values():
        compile(source, 'generated', 'exec')
    # Other runners keep one file.
    files, ids = generate.test_file('pytest', probes, tmp_path)
    assert list(files) == ['test_amu_generated.py'] and len(ids) == 3


def test_rows_are_ordered_one_per_function_first_and_newly_raising_calls_before_others():
    def row(target, call, before, after):
        return dict(target=target, call=call, before=[before, 'int', '1'], after=[after, 'int', '2'])
    got = generate.ordered([row('a', 'a(1)', 'return', 'return'), row('a', 'a(2)', 'return', 'raise'),
                            row('a', 'a(3)', 'raise', 'return'), row('b', 'b(1)', 'raise', 'raise'), row('c', 'c(1)', 'return', 'raise')])
    assert [r['call'] for r in got] == ['a(2)', 'c(1)', 'b(1)', 'a(1)', 'a(3)']


def test_a_call_that_now_fails_at_a_statement_that_is_not_a_raise_is_a_crash_row(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], 'LIB1', 'def tested(x):\n    return x + 0\n\n\ndef scale(x):\n'
                        '    if x == 7:\n        raise ValueError("seven")\n    import warnings\n'
                        '    warnings.warn("old", DeprecationWarning)\n    return x.real * 2\n')
    probes = [dict(function='lib.py#scale', setup='from lib import scale', call="scale('ab')", why='a string'),
              dict(function='lib.py#scale', setup='from lib import scale', call="scale('cd')", why='another string'),
              dict(function='lib.py#scale', setup='from lib import scale', call='scale(7)', why='the refused value'),
              dict(function='lib.py#scale', setup='import warnings\nwarnings.simplefilter("error")\nfrom lib import scale',
                   call='scale(3)', why='warnings as errors')]
    report, _ = run(tmp_path, probes, monkeypatch)
    rows = {row['call']: row for row in report['generated_differences']}
    assert {call: (row.get('origin'), row.get('at')) for call, row in rows.items()} == {
        "scale('ab')": ('crash', ['lib.py', 10]), "scale('cd')": ('crash', ['lib.py', 10]), 'scale(7)': ('raised', ['lib.py', 7]),
        'scale(3)': ('raised', ['lib.py', 9])}, rows
    from amu.ci.render import render
    body = render(report, dict(head='a' * 40, merge_base='b' * 40))
    # Two calls that fail at the same statement are one row. The refusal the change wrote is not a crash, nor is a
    # warning the probe itself turned into an error.
    assert "now fail in the project's code (1)" in body and 'Other generated tests whose outcome differs (2)' in body, body
