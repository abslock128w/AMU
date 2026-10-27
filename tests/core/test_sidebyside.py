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
    assert '<summary>Behaviour differs on captured input (1)</summary>' in body and '`lib.py#clamp`' in body, body
    assert body.splitlines()[0].endswith('; 1 behaviour difference on captured input, collapsed at the end'), body


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


def test_a_function_reaching_its_own_name_finds_the_module_function(tmp_path):
    # Found on sympy #30715: the copy's own name (to_NNF recursing) was a cell of the wrapper it is compiled in, filled
    # with the class owning it, None for a module function. Callers of the changed to_NNF raised "'NoneType' object is not
    # callable" as changed only, and to_NNF itself raised on both sides. A method named like the module function it calls
    # reached its class.
    lib0 = ('def flatten(items):\n    out = []\n    for item in items:\n        out += flatten(item) if isinstance(item, list) else [item]\n'
            '    return out\n\n\ndef size(items):\n    return len(flatten(items))\n\n\n'
            'class Bag:\n    def __init__(self, items):\n        self.items = items\n\n    def size(self):\n        return size(self.items)\n')
    lib1 = (lib0.replace('else [item]\n', 'else [item] if item is not None else []\n')
            .replace('return len(flatten(items))', 'return len(flatten(items if isinstance(items, list) else [items]))')
            .replace('return size(self.items)', 'return size(self.items) * 1'))
    tests = 'from lib import Bag, size\n\n\ndef test_size():\n    assert size([1, [2, None]]) >= 2\n    assert Bag([1, [2, None]]).size() >= 2\n'
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1, 'tests/test_lib.py': tests})
    found = {(row['target'].split('#')[1], row['input']): row for row in report['behaviour_differences']}
    outer = found.get(('flatten', '([[1, [2, None]]], {})'))
    assert outer and outer['before']['result'] == ['return', ['list', ['1', '2', 'None']]], found
    assert outer['after']['result'] == ['return', ['list', ['1', '2']]], outer
    rows = {name: row for (name, _), row in found.items() if name in ('size', 'Bag.size')}
    assert set(rows) == {'size', 'Bag.size'}, found
    assert all(row['before']['result'] == ['return', '3'] and row['after']['result'] == ['return', '2'] for row in rows.values()), rows


def test_a_decorated_function_reaching_its_own_name_reaches_itself_undecorated(tmp_path):
    # Found on sympy's _inverse_mellin_transform: @_noconds_ takes a keyword the function does not, and it recurses
    # passing it. Through the module's wrapper before the change and the bare copy swapped in as changed, a neutral edit
    # raised as changed only; through an lru_cache the as-changed copy recursed into the original.
    lib0 = ('import functools\n\n\ndef noconds(func):\n    @functools.wraps(func)\n    def wrapper(*args, noconds=True, **kwargs):\n'
            '        res = func(*args, **kwargs)\n        return res[0] if noconds else res\n    return wrapper\n\n\n'
            '@noconds\ndef total(xs):\n    if not isinstance(xs, list):\n        return xs, True\n'
            '    return sum(total(x, noconds=False)[0] for x in xs), True\n\n\n'
            '@functools.lru_cache(maxsize=None)\ndef depth(t):\n    if not isinstance(t, tuple):\n        return 0\n'
            '    return 1 + max([depth(x) for x in t] or [0])\n')
    lib1 = lib0.replace('        return xs, True\n', '        # a leaf\n        return xs, True\n').replace('return 1 + max(', 'return 2 + max(')
    tests = 'from lib import depth, total\n\n\ndef test_lib():\n    assert total([1, [2, 3]]) == 6\n    assert depth(((1, (2,)), 3)) > 0\n'
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1, 'tests/test_lib.py': tests})
    found = {(row['target'].split('#')[1], row['input']): row for row in report['behaviour_differences']}
    assert {name for name, _ in found} == {'depth'}, found
    outer = found.get(('depth', '([((1, (2,)), 3)], {})'))
    assert outer and outer['before']['result'] == ['return', '3'] and outer['after']['result'] == ['return', '6'], found


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


# Found on Django #22039: DebugLexer._tag_re_split moved to Lexer. The as-changed run kept the subclass's deleted method,
# so the new tokenize called the old splitter. Here a method and a class attribute move from the subclass to the base.
MOVED0 = ('class Lexer:\n    def __init__(self, text):\n        self.text = text\n\n    def tokenize(self):\n'
          '        return [part.strip() for part in self.text.split(",")]\n\n\n'
          'class DebugLexer(Lexer):\n    sep = ","\n\n    def pieces(self):\n        return self.text.split(self.sep)\n\n'
          '    def tokenize(self):\n        return [(part.strip(), len(part.strip())) for part in self.pieces()]\n')
MOVED1 = ('import re\n\n\nclass Lexer:\n    sep = re.compile(r"\\s*,\\s*")\n\n    def __init__(self, text):\n        self.text = text\n\n'
          '    def pieces(self):\n        return self.sep.split(self.text.strip())\n\n    def tokenize(self):\n'
          '        return self.pieces()\n\n\nclass DebugLexer(Lexer):\n    def tokenize(self):\n'
          '        return [(part, len(part)) for part in self.pieces()]\n')
MOVED_TESTS = ('from lib import DebugLexer, Lexer\n\n\ndef test_lex():\n    assert Lexer("A , b").tokenize()\n'
               '    assert DebugLexer("A , b").tokenize()\n')


def test_members_the_change_moves_from_a_subclass_to_its_base_are_gone_from_the_subclass_as_changed(tmp_path):
    report = change(tmp_path, {'lib.py': MOVED0, 'tests/test_lib.py': MOVED_TESTS}, {'lib.py': MOVED1, 'tests/test_lib.py': MOVED_TESTS})
    assert set(report['side_by_side']['called']) == {'lib.py#Lexer.tokenize', 'lib.py#DebugLexer.tokenize'}, report['side_by_side']
    assert report['behaviour_differences'] == [], report['behaviour_differences']


def test_a_behaviour_change_in_a_member_moved_to_the_base_still_shows(tmp_path):
    moved1 = MOVED1.replace('return self.sep.split(self.text.strip())', 'return self.sep.split(self.text.strip().lower())')
    report = change(tmp_path, {'lib.py': MOVED0, 'tests/test_lib.py': MOVED_TESTS}, {'lib.py': moved1, 'tests/test_lib.py': MOVED_TESTS})
    found = {row['target'].split('#')[1]: row for row in report['behaviour_differences']}
    assert set(found) == {'Lexer.tokenize', 'DebugLexer.tokenize'}, found
    pairs = ['list', [['tuple', ["'A'", '1']], ['tuple', ["'b'", '1']]]]
    assert found['DebugLexer.tokenize']['before']['result'] == ['return', pairs], found
    assert found['DebugLexer.tokenize']['after']['result'] == ['return', ['list', [['tuple', ["'a'", '1']], ['tuple', ["'b'", '1']]]]], found


# Found in review: a method moved into a new mixin the class now inherits (its bases change), and a mutable class
# attribute moved into a new __init__. Deleting the member as changed exposed the old bases, or broke objects built
# before the change: false rows where the head behaves as before.
MIX0 = ('class Base:\n    def name(self):\n        return "base"\n\n\n'
        'class A(Base):\n    def helper(self):\n        return "helper"\n\n    def run(self):\n        return self.helper()\n')
MIX1 = ('class Mixin:\n    def helper(self):\n        return "helper"\n\n\nclass Base:\n    def name(self):\n        return "base"\n\n\n'
        'class A(Mixin, Base):\n    def run(self):\n        result = self.helper()\n        return result\n')
REG0 = 'class Registry:\n    entries = {"a": 1}\n\n    def find(self, key):\n        return self.entries.get(key)\n'
REG1 = ('class Registry:\n    def __init__(self):\n        self.entries = {"a": 1}\n\n'
        '    def find(self, key):\n        return self.entries.get(key, None)\n')


def test_a_member_moved_to_a_new_base_or_onto_the_instance_is_not_deleted_as_changed(tmp_path):
    for name, before, after, test in (
            ('mixin', MIX0, MIX1, 'from lib import A\n\n\ndef test_a():\n    assert A().run() == "helper"\n'),
            ('registry', REG0, REG1, 'from lib import Registry\n\n\ndef test_r():\n    assert Registry().find("a") == 1\n')):
        root = tmp_path/name
        report = change(root, {'lib.py': before, 'tests/test_lib.py': test}, {'lib.py': after, 'tests/test_lib.py': test})
        assert report['side_by_side']['called'], (name, report['side_by_side'])
        assert report['behaviour_differences'] == [], (name, report['behaviour_differences'])

def test_additions_name_the_members_a_change_removes_from_a_class():
    from amu.core.sidebyside import additions
    before = ('class A:\n    x = 1\n    y = 2\n\n    def f(self):\n        return 1\n\n    class Inner:\n        z = 3\n\n\n'
              'class B(A):\n    def g(self):\n        return 2\n\n    def h(self):\n        return 3\n\n\ndef top():\n    return 4\n\n\n'
              '@total_ordering\nclass C:\n    def __lt__(self, other):\n        return True\n\n    def __le__(self, other):\n'
              '        return True\n\n    def keep(self):\n        return 5\n')
    after = ('class A:\n    if True:\n        y = 2\n\n    def f(self):\n        return 1\n\n    class Inner:\n        pass\n\n\n'
             'class B(A):\n    def h(self):\n        return 3\n\n\n@total_ordering\nclass C:\n    def __lt__(self, other):\n'
             '        return True\n')
    removed = {row['owner']: row['removed'] for row in additions(before, after, 'lib.py') if 'removed' in row}
    # top() is a module's name, not a member; y is still bound, in an if block; total_ordering makes C.__le__ again.
    assert removed == {'A': ['x'], 'A.Inner': ['z'], 'B': ['g'], 'C': ['keep']}, removed


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


def test_a_generator_from_an_as_changed_helper_differs_only_in_what_it_yields(tmp_path):
    # Found on sympy #30715: dpll_satisfiable returns _all_models(...), a generator from a helper the change also edits.
    # The as-changed helper was built inside side-by-side's wrapper, so the two results differed only in the generator's
    # name, "<generator object __amu_outer__.<locals>._all_models at 0x?>" against "_all_models".
    lib0 = ('def models(cnf):\n    for clause in cnf:\n        yield clause\n\n\n'
            'def satisfiable(cnf, all_models=False):\n    if all_models:\n        return models(cnf)\n    return bool(cnf)\n\n\n'
            'class Tree:\n    def __init__(self, items):\n        self.items = items\n\n'
            '    def nodes(self):\n        for item in self.items:\n            yield item\n\n'
            '    def walk(self):\n        return self.nodes()\n')
    lib1 = (lib0.replace('    for clause in cnf:\n        yield clause\n', '    yield from cnf\n')
            .replace('    if all_models:\n        return models(cnf)\n    return bool(cnf)\n',
                     '    if not all_models:\n        return bool(cnf)\n    return models(cnf)\n')
            .replace('        for item in self.items:\n            yield item\n', '        yield from self.items\n')
            .replace('        return self.nodes()\n', '        found = self.nodes()\n        return found\n'))
    tests = ('from lib import Tree, satisfiable\n\n\n'
             'def test_models():\n    assert list(satisfiable([1, 2], True)) == [1, 2]\n    assert list(Tree([3]).walk()) == [3]\n')
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1, 'tests/test_lib.py': tests})
    assert report['side_by_side']['compared_calls'] == 4, report['side_by_side']
    assert report['behaviour_differences'] == [], report['behaviour_differences']
    # A change in what the helper yields still shows, on the helper; the generator its caller returns is the same one.
    lib2 = lib1.replace('    yield from cnf\n', '    for clause in cnf:\n        yield clause * 2\n')
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib2, 'tests/test_lib.py': tests})
    [row] = report['behaviour_differences']
    assert row['target'] == 'lib.py#models' and row['differs'] == ['result'], row
    assert row['before']['result'] == ['return', ['list', ['1', '2']]], row
    assert row['after']['result'] == ['return', ['list', ['2', '4']]], row


def test_what_the_copies_define_is_named_as_in_the_source(tmp_path):
    # A class, function or generator defined in a compared copy, or in a member the change adds to a class, is shown
    # with the name it has where the source lives, not side-by-side's own wrappers.
    lib0 = ('def box(v):\n    class Box:\n        pass\n    made = Box()\n    made.v = v\n    return made\n\n\n'
            'class Tree:\n    def __init__(self, items):\n        self.items = items\n\n'
            '    def walk(self):\n        return iter(self.items)\n')
    lib1 = (lib0.replace('made.v = v\n', 'made.v = v + 1\n')
            .replace('    def walk(self):\n        return iter(self.items)\n',
                     '    def leaves(self):\n        yield from self.items\n\n    def walk(self):\n        return self.leaves()\n'))
    tests = 'from lib import Tree, box\n\n\ndef test_all():\n    assert box(1).v\n    assert list(Tree([3]).walk()) == [3]\n'
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1, 'tests/test_lib.py': tests})
    found = {row['target'].split('#')[1]: row for row in report['behaviour_differences']}
    assert found['box']['before']['result'] == ['return', ['lib.box.<locals>.Box', ['dict', [["'v'", '1']]]]], found['box']
    assert found['box']['after']['result'] == ['return', ['lib.box.<locals>.Box', ['dict', [["'v'", '2']]]]], found['box']
    assert found['Tree.walk']['after']['result'] == ['return', 'generator:<generator object Tree.leaves at 0x?>'], found


def test_a_copy_that_outlives_its_comparison_is_not_taken_for_the_function_it_copies(tmp_path):
    # The as-changed copy carries the changed function's own name. Here an unchanged helper keeps whatever the module
    # holds under that name, the copy during the as-changed runs, and the test calls it later: those calls run the copy,
    # not the code before the change, and are not captured as calls of it.
    lib0 = 'seen = []\n\n\ndef keep():\n    seen.append(note)\n\n\ndef note(x):\n    keep()\n    return x\n'
    lib1 = lib0.replace('return x\n', 'return x + 1\n')
    tests = 'import lib\n\n\ndef test_note():\n    lib.note(1)\n    for fn in list(lib.seen):\n        fn(2)\n'
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1, 'tests/test_lib.py': tests})
    # seen: note three times (its two runs before the change, the test's call), each captured again; the copy twice
    assert report['side_by_side']['compared_calls'] == 4, report['side_by_side']


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


def test_tests_the_change_adds_are_not_run_on_the_code_before_it(tmp_path, monkeypatch):
    # Found on PR #20: the side-by-side batch named a test the pull request adds; on the code before the change pytest
    # rejected the whole batch, and isolating the label took longer than the comparison.
    from amu.core import runners, sidebyside
    sent = []
    real = runners.execute

    def recording(runner, python, tree, ids, *args, **kwargs):
        if 'AMU_SBS_SPEC' in (kwargs.get('extra_env') or {}):
            sent.extend(ids)
        return real(runner, python, tree, ids, *args, **kwargs)
    monkeypatch.setattr(sidebyside.runners, 'execute', recording)
    lib0 = 'def clamp(v):\n    return min(v, 10)\n'
    lib1 = 'def clamp(v):\n    return min(v, 9)\n'
    old = 'from lib import clamp\n\n\ndef test_a():\n    assert clamp(50)\n'
    new = 'from lib import clamp\n\n\ndef test_new():\n    assert clamp(50) == 9\n'
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': old},
                    {'lib.py': lib1, 'tests/test_lib.py': old, 'tests/test_new.py': new})
    assert sent == ['tests/test_lib.py::test_a'], sent
    assert [row['target'] for row in report['behaviour_differences']] == ['lib.py#clamp'], report['behaviour_differences']


def test_functions_in_test_files_and_docstring_only_changes_are_not_compared_and_are_listed(tmp_path):
    # Found in the retrospective (defect 8): 167 of 447 comparable changed functions were in test files (Django #21820
    # test_invalid_view, #21861 ModelTest.test_eq), and docstring-only changes were compared (sympy #30530, #30697).
    lib0 = 'def clamp(v):\n    return min(v, 10)\n\n\ndef describe(v):\n    """The value."""\n    return str(v)\n'
    lib1 = lib0.replace('min(v, 10)', 'min(v, 9)').replace('"""The value."""', '"""The value, as text."""')
    helpers0 = 'def big():\n    return 50\n'
    helpers1 = 'def big():\n    return 49 + 1\n'
    tests = ('from helpers import big\nfrom lib import clamp, describe\n\n\n'
             'def test_clamp():\n    assert clamp(big()) and describe(1)\n')
    report = change(tmp_path, {'lib.py': lib0, 'tests/helpers.py': helpers0, 'tests/test_lib.py': tests},
                    {'lib.py': lib1, 'tests/helpers.py': helpers1, 'tests/test_lib.py': tests})
    side = report['side_by_side']
    assert (side['targets'], side['called']) == (1, ['lib.py#clamp']), side
    assert side['not_compared'] == {'lib.py#describe': 'only its docstring, comments or layout changed',
                                    'tests/helpers.py#big': 'in a test file'}, side['not_compared']
    assert [row['target'] for row in report['behaviour_differences']] == ['lib.py#clamp'], report['behaviour_differences']



def test_a_private_name_in_a_member_the_change_adds_is_mangled_as_in_its_class(tmp_path):
    # The change moves Counter's private-attribute logic into a member it adds; the behaviour is the same. Compiled inside
    # side-by-side's own class, the added member's self.__n was mangled with that class's name and missed the attribute.
    lib0 = ('class Counter:\n    def __init__(self):\n        self.__n = 0\n\n    def bump(self):\n        self.__n += 1\n'
            '        return self.__n\n\n    def tick(self):\n        return self.bump()\n')
    lib1 = lib0.replace('    def tick(self):\n        return self.bump()\n',
                        '    def bump2(self):\n        self.__n += 1\n        return self.__n\n\n    def tick(self):\n        return self.bump2()\n')
    tests = 'from lib import Counter\n\n\ndef test_c():\n    assert Counter().tick() == 1\n'
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1, 'tests/test_lib.py': tests})
    assert report['side_by_side']['compared_calls'] == 1, report['side_by_side']
    assert report['behaviour_differences'] == [], report['behaviour_differences']


def test_calls_are_sampled_by_argument_shape_so_a_rare_edge_case_is_compared(tmp_path):
    # Live case (a) of the comment-usefulness test: the tests call short() 46 times, 3 with None; the first five calls
    # captured were all strings, so the change to the None case showed no row.
    lib0 = "def short(sha):\n    return (sha or 'unknown')[:7]\n"
    lib1 = "def short(sha):\n    return (sha or '(none)')[:7]\n"
    tests = ('from lib import short\n\n\ndef test_many():\n    for i in range(20):\n        assert short("abcdef%07d" % i)\n\n\n'
             'def test_missing():\n    short(None)\n')
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1, 'tests/test_lib.py': tests})
    [row] = report['behaviour_differences']
    assert row['input'] == '([None], {})' and row['before']['result'] == ['return', "'unknown'"], row
    assert row['after']['result'] == ['return', "'(none)'"], row
    captured = report['side_by_side']['captured']
    shapes = sorted({c['shape'] for c in captured})
    assert shapes == ['None', 'str:11-100'], shapes
    assert sum(c['shape'] == 'str:11-100' for c in captured) == 5  # at most five of one shape
    assert all(c['target'] == 'lib.py#short' and c['test'] for c in captured), captured
