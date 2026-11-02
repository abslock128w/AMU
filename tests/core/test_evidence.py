"""Execution-first selection: the candidates come from what the tests were seen to run, not from name resolution."""
import difflib
import json
import subprocess
import sys
import textwrap

import pytest

from amu.core import analyze
from amu.core.index import cache_dir

pytestmark = pytest.mark.skipif(sys.version_info < (3, 12), reason='sys.monitoring needs Python 3.12')

IMPL = textwrap.dedent('''\
    def shape(value):
        if isinstance(value, list):
            return [shape(v) for v in value]
        return type(value).__name__
    ''')
FILES = {
    # A package that re-exports, beside a stub of the same module: resolving `from pkg import shape` by name finds the
    # stub's declaration, not the code.
    'src/pkg/__init__.py': 'from ._impl import shape as shape\n',
    'src/pkg/__init__.pyi': 'def shape(value: object) -> object: ...\n',
    'src/pkg/_impl.py': IMPL,
    'tests/test_shape.py': textwrap.dedent('''\
        import pkg


        def test_nested():
            assert pkg.shape([1, [2]]) == ["int", ["int"]]


        def test_flat():
            assert pkg.shape(1) == "int"
        '''),
    'tests/test_other.py': 'def test_unrelated():\n    assert 1 + 1 == 2\n',
}


def change(tmp_path, after_impl, **kwargs):
    for name, text in FILES.items():
        (tmp_path/name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path/name).write_text(text)
    if not (tmp_path/'.git').exists():
        # A commit to key the recording on: the code before the change.
        for args in (['init', '-q'], ['add', '-A'], ['-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'base']):
            subprocess.run(['git', *args], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path/'src/pkg/_impl.py').write_text(after_impl)
    diff = ''.join(difflib.unified_diff(IMPL.splitlines(True), after_impl.splitlines(True), 'a/src/pkg/_impl.py', 'b/src/pkg/_impl.py'))
    return analyze(tmp_path, diff, full_evidence=True, index_dir=tmp_path/'.index', run=True, python=sys.executable, **kwargs)


BROKEN = IMPL.replace('return [shape(v) for v in value]', 'return [shape(v) for v in value][::-1]')


def test_a_suite_that_fits_runs_whole_on_both_sides(tmp_path):
    report = change(tmp_path, BROKEN)
    assert report['selection_evidence']['mode'] == 'all of the suite', report['selection_evidence']
    assert report['executed']['ran'] == 3, report['executed']
    assert [row['id'] for row in report['predicted_failures']] == ['tests/test_shape.py::test_nested'], report['summary']
    [hunk] = report['hunks']
    assert hunk['state'] == 'executed-both-sides' and hunk['sample'] == ['tests/test_shape.py::test_nested'], hunk  # runs the line


def test_past_the_budget_the_candidates_are_the_tests_seen_in_the_changed_function(tmp_path):
    # A first run records the suite; a later one, with the suite known not to fit, chooses from that recording.
    change(tmp_path, BROKEN)
    memo = cache_dir(tmp_path, tmp_path/'.index')/'shadow-memo.json'
    data = json.loads(memo.read_text())
    data['suite_seconds'] = 10_000
    memo.write_text(json.dumps(data))
    report = change(tmp_path, BROKEN)
    assert report['selection_evidence']['mode'] == 'coverage', report['selection_evidence']
    sources = report['candidate_sources']
    assert sources == {'tests/test_shape.py::test_nested': 'coverage', 'tests/test_shape.py::test_flat': 'coverage'}, sources
    assert [row['id'] for row in report['predicted_failures']] == ['tests/test_shape.py::test_nested'], report['summary']


def test_static_selection_alone_misses_them_which_is_why_it_is_not_primary(tmp_path):
    report = change(tmp_path, BROKEN, extras=('static-select',))
    assert 'selection_evidence' not in report
    assert report['predicted_failures'] == [], report['summary']  # the failing test was never a candidate


@pytest.mark.parametrize('config', ['[tool.pytest]\ntestpaths = ["tests"]\n', '[tool.pytest.ini_options]\ntestpaths = "tests"\n'])
def test_the_whole_suite_is_the_one_the_project_configures(tmp_path, config):
    # A directory of benchmarks outside ``testpaths`` needs a plugin the environment lacks: it is not part of the suite.
    (tmp_path/'pyproject.toml').write_text(config)
    (tmp_path/'bench').mkdir()
    (tmp_path/'bench/test_speed.py').write_text('import not_installed_plugin\n\n\ndef test_speed():\n    pass\n')
    report = change(tmp_path, BROKEN)
    assert report['selection_evidence']['mode'] == 'all of the suite', report['selection_evidence']
    assert not any(i.startswith('bench/') for i in report['candidate_sources']), report['candidate_sources']
    assert report['executed']['unrunnable'] == 0, report['executed']


def test_candidates_that_cannot_run_are_named(tmp_path):
    (tmp_path/'tests').mkdir(parents=True)
    (tmp_path/'tests/test_broken.py').write_text('import not_installed_plugin\n\n\ndef test_never():\n    pass\n')
    report = change(tmp_path, BROKEN)
    assert report['executed']['unrunnable_tests'] == ['tests/test_broken.py::test_never'], report['executed']


def test_a_directory_whose_conftest_cannot_load_does_not_take_the_other_tests_with_it(tmp_path):
    # Two parts split from the same failing part are not disjoint: only two parts with no test in common, both failing
    # alike, say the runner itself aborts.
    from amu.core import runners
    (tmp_path/'examples/tests').mkdir(parents=True)
    (tmp_path/'examples/tests/conftest.py').write_text('import not_installed_example\n')
    (tmp_path/'examples/tests/test_example.py').write_text('def test_one():\n    pass\n\n\ndef test_two():\n    pass\n')
    (tmp_path/'tests').mkdir()
    (tmp_path/'tests/test_good.py').write_text(''.join(f'def test_{n}():\n    pass\n\n\n' for n in range(6)))
    ids = ['examples/tests/test_example.py::test_one', 'examples/tests/test_example.py::test_two',
           *[f'tests/test_good.py::test_{n}' for n in range(6)]]
    found, problem = runners.execute(runners.get(tmp_path, 'pytest'), sys.executable, tmp_path, ids, tmp_path/'r.xml', 120)
    assert all(found[f'tests/test_good.py::test_{n}'] == 'passed' for n in range(6)), found
    assert found['examples/tests/test_example.py::test_one'] == 'unrunnable', found
