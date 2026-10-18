"""B2: changed functions executed side by side on the inputs the tests call them with."""
import difflib
import sys

import pytest

from amu.core import analyze

NEW = sys.version_info >= (3, 12)
pytestmark = pytest.mark.skipif(not NEW, reason='sys.monitoring needs Python 3.12')


def change(tmp_path, files_before, files_after):
    for name, text in files_after.items():
        (tmp_path/name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path/name).write_text(text)
    diff = ''.join(''.join(difflib.unified_diff(files_before.get(n, '').splitlines(True), files_after[n].splitlines(True),
                                                'a/' + n if n in files_before else '/dev/null', 'b/' + n))
                   for n in files_after if files_before.get(n) != files_after[n])
    return analyze(tmp_path, diff, full_evidence=True, index_dir=tmp_path/'.index', run=True, python=sys.executable)


def test_a_behaviour_change_no_test_catches_is_reported_with_its_input(tmp_path):
    lib0 = 'def clamp(v, top=10):\n    return min(v, top)\n'
    lib1 = 'def clamp(v, top=10):\n    return min(v, top - 1)\n'
    tests = 'from lib import clamp\n\n\ndef test_small():\n    assert clamp(3) == 3\n'
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1, 'tests/test_lib.py': tests})
    assert report['flips'] == 0, report['summary']
    assert report['side_by_side']['compared_calls'] == 1 and report['side_by_side']['targets'] == 1, report['side_by_side']
    assert report['behaviour_differences'] == [], report['behaviour_differences']  # clamp(3) is 3 either way
    tests2 = tests + '\n\ndef test_big():\n    assert clamp(50) > 0\n'
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests2}, {'lib.py': lib1, 'tests/test_lib.py': tests2})
    [row] = report['behaviour_differences']
    assert row['target'] == 'lib.py#clamp' and row['differs'] == ['result'] and '50' in row['input'], row
    assert row['before']['result'] == ['return', '10'] and row['after']['result'] == ['return', '9'], row
    from amu.ci.render import render
    body = render(report, dict(head='a' * 40, merge_base='b' * 40))
    assert '### Behaviour differs on captured input (1)' in body and '`lib.py#clamp`' in body, body


def test_mutation_printing_and_exceptions_count_and_noise_does_not(tmp_path):
    lib0 = ('import random\n\n\ndef add(items, x):\n    items.append(x)\n    return len(items)\n\n\n'
            'def shout(x):\n    print(x)\n\n\ndef parse(x):\n    return int(x)\n\n\ndef noisy(x):\n    return random.random() + x\n')
    lib1 = ('import random\n\n\ndef add(items, x):\n    items.append(x)\n    items.append(x)\n    return len(items) - 1\n\n\n'
            'def shout(x):\n    print(x.upper())\n\n\ndef parse(x):\n    return int(x, 16)\n\n\ndef noisy(x):\n    return random.random() + x + 0\n')
    tests = ('from lib import add, shout, parse, noisy\n\n\ndef test_all():\n    add([], 1)\n    shout("hi")\n'
             '    parse("10")\n    noisy(1)\n')
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1, 'tests/test_lib.py': tests})
    found = {row['target'].split('#')[1]: row['differs'] for row in report['behaviour_differences']}
    assert found == {'add': ['args_after'], 'shout': ['stdout'], 'parse': ['result']}, report['behaviour_differences']
    assert report['side_by_side']['unstable'] == 1  # noisy: neither version repeats itself, so nothing is said


def test_a_method_using_super_is_compared_with_its_class(tmp_path):
    lib0 = ('class Base:\n    def size(self):\n        return 1\n\n\nclass Box(Base):\n    def __init__(self, n):\n        self.n = n\n\n'
            '    def size(self):\n        return super().size() + self.n\n')
    lib1 = lib0.replace('return super().size() + self.n', 'return super().size() * self.n')
    tests = 'from lib import Box\n\n\ndef test_box():\n    assert Box(2).size()\n'
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1, 'tests/test_lib.py': tests})
    [row] = report['behaviour_differences']
    assert row['target'] == 'lib.py#Box.size' and row['before']['result'] == ['return', '3'] and row['after']['result'] == ['return', '2'], row


def test_changed_functions_that_call_each_other_run_as_changed_together(tmp_path):
    # Found on Django 7b8504eaab: the changed caller needs state the changed callee now sets. Mixing versions raised.
    lib0 = ('class Detector:\n    def prepare(self):\n        self.keys = [1]\n\n    def detect(self):\n        self.prepare()\n'
            '        return len(self.keys)\n')
    lib1 = ('class Detector:\n    def prepare(self):\n        self.keys = [1]\n        self.extra = [2]\n\n    def detect(self):\n'
            '        self.prepare()\n        return len(self.keys) + len(self.extra)\n')
    tests = 'from lib import Detector\n\n\ndef test_detect():\n    assert Detector().detect()\n'
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1, 'tests/test_lib.py': tests})
    found = {row['target']: row for row in report['behaviour_differences']}
    assert found['lib.py#Detector.detect']['after']['result'] == ['return', '2'], found
    assert found['lib.py#Detector.detect']['before']['result'] == ['return', '1']
    assert found['lib.py#Detector.prepare']['differs'] == ['args_after']


def test_a_new_helper_and_uncopyable_arguments_are_handled(tmp_path):
    # Found on Django 27137e65: the changed function calls a helper the change adds to its module (NameError before),
    # and many arguments do not pickle (a lambda here); a deep copy carries them.
    lib0 = 'def check(lang, finder):\n    return finder(lang)\n'
    lib1 = ('def exists(lang):\n    return lang.startswith("e")\n\n\n'
            'def check(lang, finder):\n    return finder(lang) and exists(lang)\n')
    tests = 'from lib import check\n\n\ndef test_check():\n    assert check("fr", lambda lang: True)\n'
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1, 'tests/test_lib.py': tests})
    [row] = report['behaviour_differences']
    assert row['before']['result'] == ['return', 'True'] and row['after']['result'] == ['return', 'False'], row


def test_arguments_that_cannot_be_copied_are_used_live_only_when_no_run_changes_them(tmp_path):
    lib0 = ('import threading\n\n\nclass Site:\n    def __init__(self):\n        self.lock = threading.RLock()\n        self.n = 1\n\n'
            '    def size(self):\n        return self.n\n\n    def bump(self):\n        self.n += 1\n        return self.n\n')
    lib1 = lib0.replace('return self.n\n\n    def bump', 'return self.n + 1\n\n    def bump').replace('self.n += 1', 'self.n += 2')
    tests = 'from lib import Site\n\n\ndef test_site():\n    s = Site()\n    s.size()\n    s.bump()\n'
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1, 'tests/test_lib.py': tests})
    found = {row['target']: row for row in report['behaviour_differences']}
    assert set(found) == {'lib.py#Site.size'}, found
    assert report['side_by_side']['not_compared'] == {'lib.py#Site.bump': 'changes arguments that cannot be copied; not compared on them'}


def test_the_as_changed_run_sees_new_class_members_and_names_added_to_other_modules(tmp_path):
    # Found on Django 4ea38d54 (a method added to the class) and 27137e65 (a function added to another module).
    util0 = 'def base():\n    return 1\n'
    util1 = util0 + '\n\ndef extra():\n    return 10\n'
    lib0 = ('import util\n\n\nclass Site:\n    def urls(self):\n        return [util.base()]\n')
    lib1 = ('import util\n\n\nclass Site:\n    def shortcut(self):\n        return util.extra()\n\n'
            '    @property\n    def flag(self):\n        return True\n\n    def urls(self):\n        return [util.base(), self.shortcut(), self.flag]\n')
    tests = 'from lib import Site\n\n\ndef test_urls():\n    assert Site().urls()\n'
    report = change(tmp_path, {'util.py': util0, 'lib.py': lib0, 'tests/test_lib.py': tests},
                    {'util.py': util1, 'lib.py': lib1, 'tests/test_lib.py': tests})
    [row] = report['behaviour_differences']
    assert row['after']['result'] == ['return', ['list', ['1', '10', 'True']]], row
    assert row['before']['result'] == ['return', ['list', ['1']]]


def test_an_import_of_a_helper_added_elsewhere_and_objects_built_by_the_old_code(tmp_path):
    # Found on Django 0398417c (a changed import of a helper added to another module) and d992705f (populate() on an
    # object the old __init__ built).
    util0 = 'def base():\n    return 1\n'
    util1 = util0 + '\n\ndef qualname(x):\n    return "q:" + x\n'
    lib0 = ('from util import base\n\n\nclass Pop:\n    def __init__(self, v):\n        self.v = v\n\n'
            '    def run(self):\n        return self.v\n\n\ndef label(x):\n    return x\n')
    lib1 = ('from util import base, qualname\n\n\nclass Pop:\n    def __init__(self, v):\n        self.v = v\n        self.w = v * 2\n\n'
            '    def run(self):\n        return self.w\n\n\ndef label(x):\n    return qualname(x)\n')
    tests = 'from lib import Pop, label\n\n\ndef test_pop():\n    assert Pop(2).run()\n    assert label("a")\n'
    report = change(tmp_path, {'util.py': util0, 'lib.py': lib0, 'tests/test_lib.py': tests},
                    {'util.py': util1, 'lib.py': lib1, 'tests/test_lib.py': tests})
    found = {row['target'].split('#')[1]: row for row in report['behaviour_differences']}
    assert found['label']['after']['result'] == ['return', "'q:a'"], found
    assert found['Pop.__init__']['differs'] == ['args_after'] and 'Pop.run' not in found, found
    assert report['side_by_side']['input_built_before_the_change'] == 1


def test_comparisons_made_before_the_time_limit_are_kept(tmp_path, monkeypatch):
    # Found in pass 2 on Django (2936a0a9): results were written at exit, so a run stopped at its limit kept nothing.
    from amu.core import shadow
    monkeypatch.setattr(shadow, 'SIDE_BY_SIDE', 4)
    monkeypatch.setattr(shadow, 'CONFIRM', 4)
    lib0 = 'def clamp(v):\n    return min(v, 10)\n'
    lib1 = 'def clamp(v):\n    return min(v, 9)\n'
    tests = ('import time\nfrom lib import clamp\n\n\ndef test_a():\n    assert clamp(50)\n\n\n'
             'def test_slow():\n    time.sleep(8)\n')
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1, 'tests/test_lib.py': tests})
    assert [row['target'] for row in report['behaviour_differences']] == ['lib.py#clamp'], report.get('side_by_side')


def test_a_changed_signature_and_names_imported_by_value(tmp_path):
    # Found on Django 65a9f141: cached_import(module_path, class_name) became cached_import(module_path); old call sites'
    # inputs do not fit it, and a module that did `from module_loading import import_string` kept the old caller.
    loading0 = 'def cached(path, name):\n    return path + "." + name\n\n\ndef load(dotted):\n    path, name = dotted.rsplit(".", 1)\n    return cached(path, name)\n'
    loading1 = 'def cached(dotted):\n    return dotted\n\n\ndef load(dotted):\n    return cached(dotted).upper()\n'
    user = 'from loading import load\n\n\ndef validators(names):\n    return [load(n) for n in names]\n'
    tests = 'from user import validators\n\n\ndef test_v():\n    assert validators(["a.b"])\n'
    report = change(tmp_path, {'loading.py': loading0, 'user.py': user, 'tests/test_v.py': tests},
                    {'loading.py': loading1, 'user.py': user, 'tests/test_v.py': tests})
    found = {row['target'].split('#')[1]: row for row in report['behaviour_differences']}
    assert 'cached' not in found and report['side_by_side']['calls_not_fitting_a_changed_signature'] >= 1, report['side_by_side']
    assert found['load']['after']['result'] == ['return', "'A.B'"], found


def test_the_hooks_load_where_the_interpreter_has_no_sitecustomize_of_its_own(tmp_path):
    # Found on GitHub's setup-python (PR #11): handing over to the environment's sitecustomize left none in sys.modules
    # when there was none, and the import that loaded the hook raised KeyError. Homebrew's Python ships one, which hid it.
    import subprocess

    from amu.core import sidebyside, trust
    for name, text in (('trust', trust.SITECUSTOMIZE), ('side', sidebyside.HOOK)):
        folder = tmp_path/name
        folder.mkdir()
        (folder/'sitecustomize.py').write_text(text)
        code = (f'import sys; sys.path[:] = [{str(folder)!r}]; import sitecustomize; '
                'assert sys.modules["sitecustomize"] is sitecustomize; print("loaded")')
        # -S: no site at start-up, so no sitecustomize is already imported and none other is on the path.
        done = subprocess.run([sys.executable, '-S', '-c', code], capture_output=True, text=True)
        assert done.returncode == 0 and 'loaded' in done.stdout, (name, done.stderr[-500:])


def test_the_as_changed_run_can_import_a_module_the_change_adds(tmp_path):
    # Found on AMU's own PR #13: shadow.run as changed called a new module (amu/core/isolate.py) the base did not have.
    (tmp_path/'pkg').mkdir()
    init = ''
    lib0 = 'def work(x):\n    return x\n'
    lib1 = 'from pkg import helper\n\n\ndef work(x):\n    return helper.double(x)\n'
    helper = 'def double(x):\n    return x * 2\n'
    tests = 'from pkg.lib import work\n\n\ndef test_work():\n    assert work(3)\n'
    report = change(tmp_path, {'pkg/__init__.py': init, 'pkg/lib.py': lib0, 'tests/test_lib.py': tests},
                    {'pkg/__init__.py': init, 'pkg/lib.py': lib1, 'pkg/helper.py': helper, 'tests/test_lib.py': tests})
    [row] = report['behaviour_differences']
    assert row['before']['result'] == ['return', '3'] and row['after']['result'] == ['return', '6'], row
