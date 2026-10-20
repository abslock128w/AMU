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
    # The request: the changed functions the tests never called (on Python 3.12+, where side-by-side execution says which
    # were called; below that, every changed function), structured output, the default fallback.
    [params] = api.calls
    assert 'lib.py#scale' in params['messages'][0]['content']
    assert ('lib.py#tested' in params['messages'][0]['content']) == (sys.version_info < (3, 12))
    assert params['output_config']['format']['type'] == 'json_schema' and params['fallbacks'] == 'default'
    assert made['cost'] == round((4000 * 4 + 1500 * 20) / 1e6, 5)
    from amu.ci.render import render
    body = render(report, dict(head='a' * 40, merge_base='b' * 40))
    assert '### Generated tests showing a difference (1)' in body and '`scale(50)`' in body and '<details>' in body, body


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
    unchanged, ran_side = 'only its docstring, comments or layout changed', []
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
        # What was left out, and why, is in the report once: with side-by-side's list when it ran, else with the tier.
        side = report.get('side_by_side')
        left_out = side['not_compared'] if side is not None else report['generated']['not_compared']
        assert left_out == {'lib.py#doc': unchanged, 'tests/test_lib.py#test_doc': 'in a test file'}, left_out
        assert ('not_compared' in report['generated']) == (side is None), report['generated']
        ran_side.append(side is not None)
    assert ran_side == [sys.version_info >= (3, 12), False]
