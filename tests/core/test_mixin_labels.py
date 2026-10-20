"""A test method defined on a mixin (a class no runner collects) runs as the concrete test classes that inherit it.

Django #21805 and #21983 (retrospective, defect 2): the change's own tests included methods of a mixin, and the label
``SharedEmailBackendTests.test_unknown_kwargs_ignored`` stopped Django's runner with "missing 1 required positional
argument: 'self'"."""
import difflib
import sys

from amu.core import analyze

# Django's tests/runtests.py in miniature: dotted labels through unittest's loader, which calls a function it is named.
RUNTESTS = '''import sys
import unittest

args, labels = sys.argv[1:], []
while args:
    arg = args.pop(0)
    if arg in ('-v', '--parallel'):
        args.pop(0)
    elif not arg.startswith('-'):
        labels.append(arg)
unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(labels))
'''
LIB0, LIB1 = 'def key(v):\n    return "k:" + v\n', 'def key(v):\n    return "key:" + v\n'
BASE = 'class MailTestsMixin:\n    def message(self):\n        return "m"\n'
SHARED = ('import unittest\n\nimport lib\n\nfrom .base import MailTestsMixin\n\n\n'
          'class SharedEmailBackendTests(MailTestsMixin):\n    def test_key(self):\n        self.assertEqual(lib.key("a"), "k:a")\n\n'
          '    def test_message(self):\n        self.assertEqual(self.message(), "m")\n\n\n'
          'class DummyBackendTests(SharedEmailBackendTests, unittest.TestCase):\n    pass\n\n\n'
          'class LocmemBackendTests(SharedEmailBackendTests, unittest.TestCase):\n    pass\n')
CRASH = "missing 1 required positional argument: 'self'"


def write(root, files):
    for name, text in files.items():
        (root/name).parent.mkdir(parents=True, exist_ok=True)
        (root/name).write_text(text)
    return root


def diff(path, before, after):
    return ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), 'a/' + path, 'b/' + path))


def django_tree(tmp_path, tests):
    return write(tmp_path, {'lib.py': LIB1, 'tests/runtests.py': RUNTESTS, 'tests/mail/__init__.py': '', 'tests/mail/base.py': BASE,
                            'tests/mail/test_backends.py': tests})


def test_a_mixin_test_the_change_edits_is_run_and_replayed_in_its_concrete_subclasses_by_django_runtests(tmp_path):
    edited = SHARED.replace('"k:a"', '"key:a"')
    root = django_tree(tmp_path, edited)
    patch = diff('lib.py', LIB0, LIB1) + diff('tests/mail/test_backends.py', SHARED, edited)
    result = analyze(root, patch, full_evidence=True, index_dir=tmp_path/'.index', run=True, python=sys.executable)
    assert result['runner'] == 'django-runtests'
    assert not any(CRASH in gap or 'could not be run' in gap for gap in result['gaps']), result['gaps']
    # The PR's own test, as the subclasses run it: fails on the code before the change, passes after.
    assert [(row['id'], row['verdict']) for row in result.get('replayed') or []] == [
        ('tests/mail/test_backends.py::DummyBackendTests::test_key', 'evidence'),
        ('tests/mail/test_backends.py::LocmemBackendTests::test_key', 'evidence')], result
    assert result['executed']['unrunnable'] == 0 and result['executed']['candidates'] == 4, result['executed']


def test_a_test_the_change_adds_to_a_mixin_runs_in_its_test_classes_under_pytest(tmp_path):
    mixin = ('import lib\n\n\nclass KeyChecks:\n    def test_prefix(self):\n        assert lib.key("a").startswith("k")\n\n\n'
             'class TestLocal(KeyChecks):\n    pass\n\n\nclass TestRemote(KeyChecks):\n    pass\n')
    added = mixin.replace('\n\n\nclass TestLocal', '\n\n    def test_long_prefix(self):\n        assert lib.key("a") == "key:a"\n\n\nclass TestLocal')
    root = write(tmp_path, {'lib.py': LIB1, 'tests/test_keys.py': added})
    patch = diff('lib.py', LIB0, LIB1) + diff('tests/test_keys.py', mixin, added)
    result = analyze(root, patch, full_evidence=True, index_dir=tmp_path/'.index', run=True, python=sys.executable, runner='pytest')
    assert not any('KeyChecks::' in gap for gap in result['gaps']), result['gaps']
    assert [(row['id'], row['verdict']) for row in result.get('replayed') or []] == [
        ('tests/test_keys.py::TestLocal::test_long_prefix', 'evidence'),
        ('tests/test_keys.py::TestRemote::test_long_prefix', 'evidence')], result


def test_a_mixin_test_no_test_class_inherits_is_not_run_and_is_named_in_the_gaps(tmp_path):
    lonely = ('import lib\n\nfrom .base import MailTestsMixin\n\n\n'
              'class SharedEmailBackendTests(MailTestsMixin):\n    def test_key(self):\n        self.assertEqual(lib.key("a"), "k:a")\n')
    edited = lonely.replace('"k:a"', '"key:a"')
    root = django_tree(tmp_path, edited)
    patch = diff('lib.py', LIB0, LIB1) + diff('tests/mail/test_backends.py', lonely, edited)
    result = analyze(root, patch, full_evidence=True, index_dir=tmp_path/'.index', run=True, python=sys.executable)
    assert not any(CRASH in gap or 'could not be run' in gap for gap in result['gaps']), result['gaps']
    assert ('1 test(s) defined on a mixin, a class django-runtests does not collect, that no test class inherits: not run: '
            'tests/mail/test_backends.py::SharedEmailBackendTests::test_key') in result['gaps'], result['gaps']
    assert result['executed']['ran'] == 0 and not result['executed']['candidates'], result['executed']


def test_a_mixin_test_named_by_the_caller_runs_in_its_concrete_subclasses(tmp_path):
    root = django_tree(tmp_path, SHARED)
    result = analyze(root, diff('lib.py', LIB0, LIB1), full_evidence=True, index_dir=tmp_path/'.index', run=True, python=sys.executable,
                     only=['tests/mail/test_backends.py::SharedEmailBackendTests::test_message'])
    assert not any(CRASH in gap or 'could not be run' in gap for gap in result['gaps']), result['gaps']
    assert result['executed']['ran'] == 2 and result['executed']['unrunnable'] == 0, result['executed']


def test_a_mixin_the_change_adds_before_it_is_written_runs_in_the_subclasses_it_adds(tmp_path):
    """The patch describes a change not yet on disk: the graph has not seen the new classes; the changed file has."""
    added = SHARED.replace('class DummyBackendTests', 'class SmtpChecks:\n    def test_smtp(self):\n        self.assertTrue(lib.key("s"))\n\n\n'
                           'class SmtpBackendTests(SmtpChecks, unittest.TestCase):\n    pass\n\n\nclass DummyBackendTests')
    root = django_tree(tmp_path, SHARED)
    (root/'lib.py').write_text(LIB0)
    result = analyze(root, diff('tests/mail/test_backends.py', SHARED, added), full_evidence=True, index_dir=tmp_path/'.index', run=True,
                     python=sys.executable)
    assert not any(CRASH in gap or 'could not be run' in gap for gap in result['gaps']), result['gaps']
    assert [(row['id'], row['head']) for row in result.get('replayed') or []] == [('tests/mail/test_backends.py::SmtpBackendTests::test_smtp', 'passed')], result


def test_a_second_run_from_the_graph_snapshot_finds_the_same_mixins_and_test_classes(tmp_path):
    """A graph loaded from its snapshot has slim records and resolves no base by name; its class links still hold. Before,
    the second run took every class whose base was not named *TestCase for a mixin and ran nothing."""
    support = 'import unittest\n\n\nclass MailTestsSupport:\n    pass\n\n\nclass BackendTestBase(unittest.TestCase):\n    pass\n'
    tests = ('import lib\n\nfrom .base import BackendTestBase, MailTestsSupport\n\n\n'
             'class SharedEmailBackendTests(MailTestsSupport):\n    def test_key(self):\n        self.assertEqual(lib.key("a"), "k:a")\n\n\n'
             'class DummyBackendTests(SharedEmailBackendTests, BackendTestBase):\n    pass\n\n\n'
             'class OrphanChecks(MailTestsSupport):\n    def test_orphan(self):\n        self.assertTrue(lib.key("o"))\n\n\n'
             'class PlainBackendTests(BackendTestBase):\n    def test_plain(self):\n        self.assertTrue(lib.key("p"))\n')
    root = django_tree(tmp_path, tests)
    (root/'tests/mail/base.py').write_text(support)
    runs = [analyze(root, diff('lib.py', LIB0, LIB1), full_evidence=True, index_dir=tmp_path/'.index', run=True, python=sys.executable)
            for _ in range(2)]
    for result in runs:
        assert not any(CRASH in gap or 'could not be run' in gap for gap in result['gaps']), result['gaps']
        assert result['executed']['ran'] == 2 and result['flips'] == 1, (result['summary'], result['executed'])
        assert [row['id'] for row in result['predicted_failures']] == ['tests/mail/test_backends.py::DummyBackendTests::test_key']


def test_a_mixin_in_a_test_file_at_the_repository_root_runs_in_its_test_classes(tmp_path):
    """A test file at the root: the subclass filter compared a class ident with a path, and dropped every subclass."""
    mixin = ('import lib\n\n\nclass KeyChecks:\n    def test_prefix(self):\n        assert lib.key("a").startswith("k")\n\n\n'
             'class TestLocal(KeyChecks):\n    pass\n\n\nclass TestRemote(KeyChecks):\n    pass\n')
    added = mixin.replace('\n\n\nclass TestLocal', '\n\n    def test_long_prefix(self):\n        assert lib.key("a") == "key:a"\n\n\nclass TestLocal')
    root = write(tmp_path, {'lib.py': LIB1, 'test_keys.py': added})
    patch = diff('lib.py', LIB0, LIB1) + diff('test_keys.py', mixin, added)
    result = analyze(root, patch, full_evidence=True, index_dir=tmp_path/'.index', run=True, python=sys.executable, runner='pytest')
    assert not any('inherits' in gap for gap in result['gaps']), result['gaps']
    assert [(row['id'], row['verdict']) for row in result.get('replayed') or []] == [
        ('test_keys.py::TestLocal::test_long_prefix', 'evidence'), ('test_keys.py::TestRemote::test_long_prefix', 'evidence')], result


def test_a_class_the_projects_python_classes_names_keeps_its_own_label_under_pytest(tmp_path):
    """pytest collects ``KeyTests`` when the project sets ``python_classes = *Tests``: its test is run as named."""
    tests = 'import lib\n\n\nclass KeyTests:\n    def test_prefix(self):\n        assert lib.key("a").startswith("k:")\n'
    edited = tests.replace('"k:"', '"key:"')
    root = write(tmp_path, {'lib.py': LIB1, 'tests/test_keys.py': edited, 'pytest.ini': '[pytest]\npython_classes = *Tests\n'})
    patch = diff('lib.py', LIB0, LIB1) + diff('tests/test_keys.py', tests, edited)
    result = analyze(root, patch, full_evidence=True, index_dir=tmp_path/'.index', run=True, python=sys.executable, runner='pytest')
    assert not any('does not collect' in gap for gap in result['gaps']), result['gaps']
    assert [(row['id'], row['verdict']) for row in result.get('replayed') or []] == [
        ('tests/test_keys.py::KeyTests::test_prefix', 'evidence')], result


def test_python_classes_is_read_from_the_projects_pytest_configuration(tmp_path):
    from amu.core.runners import pytest_classes
    assert pytest_classes(tmp_path) == ('Test',)
    (tmp_path/'setup.cfg').write_text('[metadata]\nname = x\n')
    assert pytest_classes(tmp_path) == ('Test',)
    (tmp_path/'pyproject.toml').write_text('[tool.pytest.ini_options]\npython_classes = ["Check*", "Suite"]\n')
    assert pytest_classes(tmp_path) == ('Check*', 'Suite')
    (tmp_path/'pytest.ini').write_text('[pytest]\npython_classes =\n    Test\n    *Tests\n')
    assert pytest_classes(tmp_path) == ('Test', '*Tests')
