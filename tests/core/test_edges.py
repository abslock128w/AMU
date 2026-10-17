"""B1: the first-hit edge tracer on sys.monitoring."""
import subprocess
import sys

import pytest

from amu.core import edges

NEW = sys.version_info >= (3, 12)


def project(tmp_path):
    (tmp_path/'pkg').mkdir()
    (tmp_path/'pkg/__init__.py').write_text('')
    (tmp_path/'pkg/core.py').write_text(
        'class Box:\n    def __init__(self, v):\n        self.v = v\n\n    @property\n    def double(self):\n        return twice(self.v)\n\n\n'
        'def twice(v):\n    return v * 2\n\n\nREGISTRY = {"t": twice}\n\n\ndef dispatch(name, v):\n    return REGISTRY[name](v)\n')
    (tmp_path/'tests').mkdir()
    (tmp_path/'tests/test_core.py').write_text(
        'import subprocess, sys\nfrom pkg.core import Box, dispatch\n\n\n'
        'def test_property():\n    assert Box(2).double == 4\n\n\n'
        'def test_registry():\n    assert dispatch("t", 3) == 6\n')
    # Reaches the package only through a child process: nothing in this file imports it.
    (tmp_path/'tests/test_cli.py').write_text(
        'import subprocess, sys\n\n\ndef test_child():\n    code = "from pkg.core import twice; twice(1)"\n'
        '    subprocess.run([sys.executable, "-c", code], check=True)\n')
    for args in (['init', '-q'], ['add', '-A'], ['-c', 'user.email=a@b', '-c', 'user.name=a', 'commit', '-qm', 'p']):
        subprocess.run(['git', *args], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


@pytest.mark.skipif(not NEW, reason='sys.monitoring needs Python 3.12')
def test_edges_reach_through_properties_registries_and_child_processes(tmp_path):
    root = project(tmp_path)
    info = edges.record(root, sys.executable, 'pytest', index_dir=tmp_path/'.index')
    assert info.get('tests') == 3 and not info['gaps'], info
    from amu.core.index import cache_dir
    data = edges.load(cache_dir(root, tmp_path/'.index'))
    found = edges.entering(data, {'pkg/core.py': {11}})
    names = {test.rsplit('.', 1)[-1]: ids for test, ids in found.items()}
    assert set(names) == {'test_property', 'test_registry', 'test_child'}, found
    assert all('pkg/core.py#twice' in ids for ids in names.values())
    property_test = data['tests'].index(next(t for t in data['tests'] if t.endswith('test_property')))
    path = edges.path_to(data['names'], data['edges'][property_test], 'tests/test_core.py', {'pkg/core.py': {11}})
    assert path == ['tests/test_core.py#test_property', 'pkg/core.py#Box.double', 'pkg/core.py#twice'], path


def test_record_ids_drop_nested_scope_markers():
    assert edges.record_id(['a.py', 'f.<locals>.g', 1, 2]) == 'a.py#f.g'
    assert edges.record_id(['a.py', '<module>', 1, 2]) == 'a.py'
    assert edges.record_id(['a.py', 'f.<locals>.<lambda>', 1, 2]) == 'a.py#f'


@pytest.mark.skipif(not NEW, reason='sys.monitoring needs Python 3.12')
def test_a_flips_rerun_records_its_path_to_the_change_and_the_exception_path(tmp_path):
    import difflib

    from amu.ci.render import render
    from amu.core import analyze
    lib0 = 'def check(v):\n    return v\n\n\ndef work(v):\n    return check(v) + 1\n'
    lib1 = 'def check(v):\n    if v > 1:\n        raise ValueError(v)\n    return v\n\n\ndef work(v):\n    return check(v) + 1\n'
    tests = 'from lib import work\n\n\ndef helper():\n    return work(2)\n\n\ndef test_work():\n    assert helper() == 3\n'
    (tmp_path/'tests').mkdir()
    (tmp_path/'lib.py').write_text(lib1)
    (tmp_path/'tests/test_lib.py').write_text(tests)
    diff = ''.join(difflib.unified_diff(lib0.splitlines(True), lib1.splitlines(True), 'a/lib.py', 'b/lib.py'))
    result = analyze(tmp_path, diff, full_evidence=True, index_dir=tmp_path/'.index', run=True, python=sys.executable)
    [row] = result['flip_trust']
    assert row['label'] == 'fails' and row['path'] == ['tests/test_lib.py#test_work', 'tests/test_lib.py#helper', 'lib.py#work', 'lib.py#check'], row
    assert row['raised']['exception'] == 'ValueError' and row['raised']['through'][0] == 'lib.py#check', row
    body = render(result, dict(head='a' * 40, merge_base='b' * 40))
    assert 'path `test_work > helper > work > check`' in body and '`ValueError` raised through `check > work > helper > test_work`' in body, body


@pytest.mark.skipif(not NEW, reason='sys.monitoring needs Python 3.12')
def test_a_call_recording_makes_dynamically_reached_tests_candidates(tmp_path, monkeypatch):
    import difflib

    from amu.core import analyze, shadow
    from amu.core.index import cache_dir
    root = project(tmp_path)
    seen = []
    monkeypatch.setattr(shadow, 'run', lambda r, e, ids, *a, **k: (seen.extend(ids), ([], dict(ran=0, seconds=0.0, runner='x'), []))[1])
    source = (root/'pkg/core.py').read_text()
    diff = ''.join(difflib.unified_diff(source.splitlines(True), source.replace('v * 2', 'v * 3').splitlines(True), 'a/pkg/core.py', 'b/pkg/core.py'))
    analyze(root, diff, full_evidence=True, index_dir=tmp_path/'.index', run=True, python=sys.executable)
    # test_child reaches twice() only through `python -c`: no import or call path a static analysis can see.
    assert 'tests/test_cli.py::test_child' not in seen, seen
    edges.record(root, sys.executable, 'pytest', index_dir=tmp_path/'.index')
    assert edges.load(cache_dir(root, tmp_path/'.index'))
    seen.clear()
    report = analyze(root, diff, full_evidence=True, index_dir=tmp_path/'.index', run=True, python=sys.executable)
    assert 'tests/test_cli.py::test_child' in seen, seen
    assert report['call_recording']['tests'] == 3


@pytest.mark.skipif(not NEW, reason='sys.monitoring needs Python 3.12')
def test_the_tracer_never_changes_what_a_test_sees(tmp_path):
    # Found on Django: getattr(mock.call(...), '__code__') is another, unhashable, call object; the callback raised into
    # the test. Mocks, lazy objects and proxies must pass through untouched.
    (tmp_path/'pkg').mkdir()
    (tmp_path/'pkg/__init__.py').write_text('def f(x):\n    return x\n')
    (tmp_path/'tests').mkdir()
    (tmp_path/'tests/test_mocks.py').write_text(
        'from unittest import mock\nimport pkg\n\n\nclass Lazy:\n    touched = 0\n\n    def __getattr__(self, name):\n'
        '        Lazy.touched += 1\n        return lambda *a: None\n\n    def __call__(self, *a):\n        return 1\n\n\n'
        'def test_mock_calls():\n    m = mock.Mock()\n    pkg.f(m)(1)\n    assert m.mock_calls == [mock.call(1)]\n\n\n'
        'def test_lazy_not_touched():\n    Lazy()(pkg.f(2))\n    assert Lazy.touched == 0\n')
    subprocess.run(['git', 'init', '-q'], cwd=tmp_path, check=True)
    info = edges.record(tmp_path, sys.executable, 'pytest', index_dir=tmp_path/'.index')
    assert info['exit'] == 0 and info.get('tests') == 2, info
