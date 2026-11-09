"""The suite is listed by the runner's own collection (runners.collect), so test methods a class inherits from its
direct parent or gets at import (setattr in a loop) are part of it. Synthetic projects only."""
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from amu.core import runners, suite, suite_ids

GENERATED = textwrap.dedent('''\
    import unittest

    from lib import double


    class BaseCase(unittest.TestCase):
        def test_base(self):
            self.assertEqual(double(1), 2)

        def test_parent_own(self):
            self.assertEqual(double(2), 4)


    class ChildCase(BaseCase):
        pass


    class GeneratedCase(unittest.TestCase):
        pass


    def _make(n):
        def test(self):
            self.assertEqual(double(n), 2 * n)
        return test


    for _n in range(3):
        setattr(GeneratedCase, "test_gen_%d" % _n, _make(_n))
    ''')
LIB = 'def double(x):\n    return 2 * x\n'
WANT = {'tests/test_gen.py::ChildCase::test_base', 'tests/test_gen.py::ChildCase::test_parent_own',
        'tests/test_gen.py::GeneratedCase::test_gen_0', 'tests/test_gen.py::GeneratedCase::test_gen_2'}


def write(root, files):
    for name, text in files.items():
        (root/name).parent.mkdir(parents=True, exist_ok=True)
        (root/name).write_text(text)
    subprocess.run(['git', 'init', '-q'], cwd=root, check=True)
    subprocess.run(['git', 'add', '-A'], cwd=root, check=True)
    subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 'base'], cwd=root, check=True)


def test_pytest_suite_includes_inherited_and_generated(tmp_path):
    write(tmp_path, {'lib.py': LIB, 'tests/__init__.py': '', 'tests/test_gen.py': GENERATED,
                     'pytest.ini': '[pytest]\npython_classes = *Case\n'})
    ids = set(suite_ids(tmp_path, runner='pytest', python=sys.executable))
    assert WANT <= ids


def test_unittest_suite_includes_inherited_and_generated(tmp_path):
    write(tmp_path, {'lib.py': LIB, 'tests/__init__.py': '', 'tests/test_gen.py': GENERATED})
    ids, why = runners.collect(runners.get(tmp_path, 'unittest'), sys.executable, tmp_path)
    assert why is None
    assert WANT <= set(ids)


# A stand-in for Django's runner: its suite is built by DiscoverRunner, and databases are set up before the run.
FAKE_DJANGO = {
    'django/__init__.py': '',
    'django/test/__init__.py': '',
    'django/test/runner.py': textwrap.dedent('''\
        import unittest


        class DiscoverRunner:
            def setup_databases(self, **kwargs):
                raise SystemExit("databases set up during collection")

            def teardown_databases(self, old_config, **kwargs):
                pass

            def run_checks(self, databases):
                pass

            def run_tests(self, labels):
                suite = unittest.defaultTestLoader.discover(".", pattern="test*.py", top_level_dir=".")
                self.run_checks(None)
                old = self.setup_databases()
                result = unittest.TextTestRunner(verbosity=2).run(suite)
                self.teardown_databases(old)
                return len(result.failures)
        '''),
    'tests/runtests.py': textwrap.dedent('''\
        import os
        import sys
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from django.test.runner import DiscoverRunner
        sys.exit(DiscoverRunner().run_tests(sys.argv[1:]))
        '''),
}


def test_django_runner_lists_its_suite_without_databases(tmp_path):
    gen = GENERATED.replace('from lib import double', 'import os, sys\nsys.path.insert(0, os.path.dirname(os.getcwd()))\n'
                                                        'from lib import double')
    write(tmp_path, {'lib.py': LIB, **FAKE_DJANGO, 'tests/gen/__init__.py': '', 'tests/gen/test_gen.py': gen})
    chosen = runners.get(tmp_path, 'django-runtests')
    ids, why = runners.collect(chosen, sys.executable, tmp_path)
    assert why is None
    assert {'tests/gen/test_gen.py::ChildCase::test_base', 'tests/gen/test_gen.py::GeneratedCase::test_gen_1'} <= set(ids)


def test_listing_failure_falls_back_and_says_so(tmp_path):
    write(tmp_path, {'lib.py': LIB, 'tests/__init__.py': '', 'tests/test_gen.py': GENERATED,
                     'pytest.ini': '[pytest]\npython_classes = *Case\n'})
    ids, why = runners.collect(runners.get(tmp_path, 'pytest'), str(tmp_path/'no-python'), tmp_path)
    assert ids is None and why


@pytest.mark.skipif(sys.version_info < (3, 12), reason='sys.monitoring needs Python 3.12')
def test_recording_holds_generated_tests(tmp_path):
    write(tmp_path, {'lib.py': LIB, 'tests/__init__.py': '', 'tests/test_gen.py': GENERATED,
                     'pytest.ini': '[pytest]\npython_classes = *Case\n'})
    data = suite.record(tmp_path, tmp_path/'rec.json.gz', python=sys.executable, runner='pytest')
    recording = suite.Recording(tmp_path, data['commit'], data, data['commit'])
    ran = recording.tests('lib.py', (tmp_path/'lib.py').read_text(), [2])
    assert WANT <= ran


# Flask's conftest.py has a fixture named test_apps and jinja's test modules one named test_env: test-named functions
# the runner never collects. They are not tests, so they are never candidates and never "gave no outcome".
FIXTURES = {
    'lib.py': LIB,
    'tests/__init__.py': '',
    'tests/conftest.py': 'import pytest\n\n\n@pytest.fixture\ndef test_apps():\n    return 2\n',
    'tests/test_lib.py': ('import pytest\n\nfrom lib import double\n\n\n@pytest.fixture\ndef test_env():\n    return 3\n\n\n'
                          'def test_double(test_apps, test_env):\n    assert double(test_apps) == 4 and double(test_env) == 6\n'),
}


def test_a_fixture_named_test_something_is_not_a_candidate(tmp_path):
    import difflib

    from amu.core import analyze
    write(tmp_path, FIXTURES)
    changed = LIB.replace('2 * x', 'x + x')
    (tmp_path/'lib.py').write_text(changed)
    diff = ''.join(difflib.unified_diff(LIB.splitlines(True), changed.splitlines(True), 'a/lib.py', 'b/lib.py'))
    report = analyze(tmp_path, diff, full_evidence=True, index_dir=tmp_path/'.index', run=True, python=sys.executable,
                     runner='pytest')
    executed = report['executed']
    assert executed['ran'] == executed['candidates'] == 1, executed
    assert not executed.get('unrunnable') and not executed.get('unrunnable_tests'), executed
    assert not any('could not be run' in gap for gap in report['gaps']), report['gaps']
