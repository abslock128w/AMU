"""Per-hunk evidence states: what is known about each hunk of a change, from what ran and from its syntax.

Each hunk of a changed production file gets one state:

- ``not-code``: the hunk changes docstrings, comments or layout only; the code is the same.
- ``annotations-only``: the hunk changes type annotations only (and docstrings, comments, layout); what runs is the
  same unless something reads the annotations.
- ``new-code``: the hunk adds definitions the code before the change does not have, no existing code names them, and
  no existing test ran them.
- ``executed-both-sides``: tests ran the hunk on the code after the change, and ran on the code before it too.
- ``executed-head-only``: only tests the change adds ran it.
- ``executed-none``: no test ran it, and nothing says why. A hunk whose changed lines inside function bodies no test
  entered is in this state even when its ``def`` or ``class`` lines ran at import.
- ``not-executable-here``: a changed line runs only under a condition false in this job: a Python version, a
  platform, or a module that is not installed. Read from ``if`` statements and conditional expressions around the line,
  around every call of the function holding it, and around every use of a class the change adds. A hunk that ran is in
  this state when one of its changed lines is so guarded (the row names that line).
- ``not-recorded``: nothing recorded what ran (no sys.monitoring, or the static selection alone).

``WORST`` orders them for the first line of a report: the first in it that any hunk has is the one reported.
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path

from . import hunks as hunk_split
from . import replay

WORST = ('executed-none', 'not-executable-here', 'not-recorded', 'executed-head-only', 'new-code', 'executed-both-sides',
         'annotations-only', 'not-code')


def interpreter(python):
    """What a guard can test about the job's interpreter: version, platform, os name, implementation."""
    code = ('import json, os, platform, sys; print(json.dumps(dict(version=list(sys.version_info[:3]), platform=sys.platform, '
            'os=os.name, system=platform.system(), implementation=sys.implementation.name)))')
    try:
        done = subprocess.run([python, '-c', code], capture_output=True, text=True, timeout=30)
        return json.loads(done.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def importable(python, module, cache):
    if module not in cache:
        try:
            done = subprocess.run([python, '-c', f'import importlib.util, sys; sys.exit(0 if importlib.util.find_spec({module!r}) else 3)'],
                                  capture_output=True, timeout=30)
            cache[module] = done.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            cache[module] = None
    return cache[module]


def _strip(tree):
    """``tree`` without docstrings: the first string-only statement of each module, class and function body."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], 'value', None), ast.Constant) \
                    and isinstance(body[0].value.value, str):
                node.body = body[1:] or [ast.Pass()]
    return tree


class _Unannotated(ast.NodeTransformer):
    """A tree without its annotations: parameters, returns, and annotated assignments (kept as plain assignments)."""

    def visit_arg(self, node):
        node.annotation = None
        return node

    def visit_FunctionDef(self, node):
        node.returns = None
        self.generic_visit(node)
        return node

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_AnnAssign(self, node):
        if node.value is None:
            # A bare annotation declares a name (a dataclass field, for instance): which names, not their types.
            node.annotation = ast.Constant(None)
            return node
        return ast.copy_location(ast.Assign(targets=[node.target], value=node.value), node)


def _same_without_annotations(before, after):
    try:
        one, two = (_Unannotated().visit(_strip(ast.parse(text))) for text in (before, after))
        return ast.dump(one) == ast.dump(two)
    except (SyntaxError, ValueError):
        return False


def _same_code(before, after):
    try:
        return ast.dump(_strip(ast.parse(before))) == ast.dump(_strip(ast.parse(after)))
    except (SyntaxError, ValueError):
        return False


def _typing_only(before, after, hunk):
    """Whether every changed line of the hunk sits in the body of an ``if TYPE_CHECKING:`` (added lines on the new side,
    removed lines on the old): code a type checker reads and no run executes, like an annotation."""
    def guarded(text, numbers):
        lines = text.splitlines()
        numbers = [n for n in numbers if n <= len(lines) and lines[n - 1].strip()]
        if not numbers:
            return None
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            return False
        spans = [(node.body[0].lineno, node.body[-1].end_lineno) for node in ast.walk(tree) if isinstance(node, ast.If) and node.body
                 and (node.test.id if isinstance(node.test, ast.Name) else node.test.attr if isinstance(node.test, ast.Attribute) else '') == 'TYPE_CHECKING'
                 and node.body[0].lineno > (node.test.end_lineno or node.lineno)]
        return all(any(a <= n <= b for a, b in spans) for n in numbers)
    sides = [guarded(after, hunk.get('added') or []), guarded(before, hunk.get('removed') or [])]
    return False not in sides and True in sides


def _one_hunk(before, hunk_rows, old_start):
    """``before`` with one block of a hunk applied: its rows (removed, added, and the unchanged lines between them, when
    the block holds several changes) from ``old_start`` on."""
    lines = before.splitlines(True)
    old = [row[1:] for row in hunk_rows if row[:1] in '- ']
    new = [row[1:] for row in hunk_rows if row[:1] in '+ ']
    start = max(old_start - 1, 0)
    return ''.join(lines[:start] + [n if n.endswith('\n') else n + '\n' for n in new] + lines[start + len(old):])


def _definitions(text):
    """{qualified name: (first line, last line)} of every function and class in ``text``."""
    try:
        tree = ast.parse(text or '')
    except (SyntaxError, ValueError):
        return {}
    found = {}

    def walk(nodes, prefix):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qual = prefix + node.name
                first = min([node.lineno, *(d.lineno for d in node.decorator_list)])
                found[qual] = (first, node.end_lineno)
                walk(node.body, qual + '.')
    walk(tree.body, '')
    return found


MAX_DEPTH = 12  # how deep a guard's names and operands are followed before it is unknown


class Guards:
    """Whether code runs in this job, from the conditions of ``if`` statements: a three-valued evaluation (True, False,
    None for unknown) of version, platform and import-availability tests, following module-level names through their
    assignments and imports."""

    def __init__(self, root, python, sources):
        self.root, self.python, self.sources = Path(root), python, sources
        self.facts = interpreter(python) if python else None
        self.imports, self.texts, self.trees = {}, {}, {}

    def text(self, path):
        if path not in self.texts:
            if path in self.sources:
                self.texts[path] = self.sources[path]
            else:
                try:
                    self.texts[path] = (self.root/path).read_text(errors='replace')
                except OSError:
                    self.texts[path] = None
        return self.texts[path]

    def tree(self, path):
        if path not in self.trees:
            try:
                self.trees[path] = ast.parse(self.text(path) or '')
            except (SyntaxError, ValueError):
                self.trees[path] = None
        return self.trees[path]

    def module_file(self, path, module, level):
        parts = Path(path).parent.parts
        if level:
            base = list(parts[:len(parts) - (level - 1)] if level > 1 else parts)
        else:
            base = []
            candidates = [Path(*module.split('.')), Path('src', *module.split('.'))]
            for c in candidates:
                for form in (c.with_suffix('.py'), c/'__init__.py'):
                    if (self.root/form).is_file() or form.as_posix() in self.sources:
                        return form.as_posix()
            return None
        target = Path(*base, *(module.split('.') if module else []))
        for form in (target.with_suffix('.py'), target/'__init__.py'):
            if (self.root/form).is_file() or form.as_posix() in self.sources:
                return form.as_posix()
        return None

    def name(self, path, name, depth):
        """The value of a module-level ``name`` in ``path``: True/False/None, or a tuple/str when it is one."""
        tree = self.tree(path)
        if tree is None or depth > 4:
            return None
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                return self.value(path, node.value, depth + 1)
            if isinstance(node, ast.ImportFrom) and any((a.asname or a.name) == name for a in node.names):
                alias = next(a for a in node.names if (a.asname or a.name) == name)
                other = self.module_file(path, node.module or '', node.level)
                return self.name(other, alias.name, depth + 1) if other else None
            if isinstance(node, ast.Try):
                found = self.tried(path, node, name)
                if found is not None:
                    return found
        return None

    def tried(self, path, node, name):
        """``name`` bound in both a try body that imports a module and its ImportError handler: whether that module can
        be imported here decides which binding holds."""
        modules = [a.name for n in node.body if isinstance(n, ast.Import) for a in n.names] + \
                  [n.module for n in node.body if isinstance(n, ast.ImportFrom) and n.module and not n.level]
        binds = [n for n in node.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
        handled = [n for h in node.handlers for n in h.body
                   if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
        if not (modules and binds and handled):
            return None
        present = [importable(self.python, m, self.imports) for m in modules]
        if None in present:
            return None
        chosen = binds[-1] if all(present) else handled[-1]
        value = self.value(path, chosen.value, 1)
        if value is not None and not all(present):
            missing = next(m for m, ok in zip(modules, present) if not ok)
            return ('missing', missing, value)
        return value

    def value(self, path, node, depth=0):
        facts = self.facts or {}
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Tuple):
            items = [self.value(path, e, depth) for e in node.elts]
            return None if any(i is None for i in items) else tuple(items)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            key = (node.value.id, node.attr)
            if key == ('sys', 'version_info') and facts:
                return ('version', tuple(facts['version']))
            if key == ('sys', 'platform') and facts:
                return ('platform', facts['platform'])
            if key == ('os', 'name') and facts:
                return ('platform', facts['os'])
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute) and isinstance(node.value.value, ast.Name) \
                and (node.value.value.id, node.value.attr, node.attr) == ('sys', 'implementation', 'name') and facts:
            return ('platform', facts['implementation'])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) \
                and node.func.value.id == 'platform' and not node.args and facts:
            if node.func.attr == 'system':
                return ('platform', facts['system'])
            if node.func.attr == 'python_implementation':
                return ('platform', {'cpython': 'CPython', 'pypy': 'PyPy'}.get(facts['implementation'], facts['implementation']))
        if isinstance(node, ast.Subscript):
            inner = self.value(path, node.value, depth)
            if isinstance(inner, tuple) and inner and inner[0] == 'version':
                try:
                    expression = ast.fix_missing_locations(ast.Expression(ast.Subscript(ast.Name('v', ast.Load()), node.slice, ast.Load())))
                    picked = eval(compile(expression, '', 'eval'), {'__builtins__': {}}, {'v': inner[1]})
                except Exception:
                    return None
                return ('version', picked) if isinstance(picked, tuple) else ('version-item', picked)
            return None
        if isinstance(node, ast.Name):
            return self.name(path, node.id, depth + 1)
        if isinstance(node, (ast.Compare, ast.BoolOp)) or (isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not)):
            return self.truth(path, node, depth + 1)
        return None

    def truth(self, path, node, depth=0):
        """(kind, bool) for a condition this job decides, kind one of 'version', 'platform', ('missing', module);
        True/False for a plain constant; None when it cannot be told."""
        if depth > MAX_DEPTH:
            return None
        if isinstance(node, ast.Compare) and len(node.ops) > 1:
            # a < b < c is a < b and b < c, each operand evaluated once.
            operands = [node.left, *node.comparators]
            pairs = [ast.Compare(left, [op], [right]) for left, op, right in zip(operands, node.ops, operands[1:])]
            return self.truth(path, ast.BoolOp(ast.And(), pairs), depth + 1)
        if isinstance(node, ast.BoolOp):
            parts = [self.truth(path, v, depth) for v in node.values]
            known = [p for p in parts if p is not None]
            values = [p[1] if isinstance(p, tuple) else p for p in known]
            kinds = [p[0] for p in known if isinstance(p, tuple)]
            if isinstance(node.op, ast.And) and False in values:
                index = values.index(False)
                return (kinds[0] if kinds else None, False) if not isinstance(known[index], tuple) else known[index]
            if isinstance(node.op, ast.Or) and True in values:
                return next(p for p in known if (p[1] if isinstance(p, tuple) else p) is True)
            if len(known) == len(parts):
                return (kinds[0] if kinds else None, isinstance(node.op, ast.And))
            return None
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            inner = self.truth(path, node.operand, depth)
            if inner is None:
                return None
            return (inner[0], not inner[1]) if isinstance(inner, tuple) else (not inner)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ('startswith', 'endswith') \
                and len(node.args) == 1 and not node.keywords:
            owner, given = self.value(path, node.func.value, depth), self.value(path, node.args[0], depth)
            if isinstance(owner, tuple) and owner and owner[0] == 'platform' and isinstance(owner[1], str) \
                    and isinstance(given, (str, tuple)) and all(isinstance(g, str) for g in (given if isinstance(given, tuple) else (given,))):
                return ('platform', bool(getattr(owner[1], node.func.attr)(given)))
            return None
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left, right = self.value(path, node.left, depth), self.value(path, node.comparators[0], depth)
            kind = next((v[0] for v in (left, right) if isinstance(v, tuple) and v and v[0] in ('version', 'version-item', 'platform')), None)
            if kind is None:
                return None
            unwrap = [v[1] if isinstance(v, tuple) and v and v[0] in ('version', 'version-item', 'platform') else v for v in (left, right)]
            if any(u is None for u in unwrap):
                return None
            op = node.ops[0]
            ops = {ast.Lt: lambda a, b: a < b, ast.LtE: lambda a, b: a <= b, ast.Gt: lambda a, b: a > b,
                   ast.GtE: lambda a, b: a >= b, ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b,
                   ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b}
            try:
                result = ops[type(op)](*unwrap)
            except (KeyError, TypeError):
                return None
            return ('version' if kind.startswith('version') else 'platform', bool(result))
        value = self.value(path, node, depth)
        if isinstance(value, tuple) and value and value[0] == 'missing':
            inner = value[2]
            return (('missing', value[1]), bool(inner)) if isinstance(inner, bool) else None
        if isinstance(value, tuple) and len(value) == 2 and value[0] in ('version', 'platform', None) and isinstance(value[1], bool):
            return value
        if isinstance(value, bool):
            return value
        return None

    def blocking(self, path, line, col=None):
        """The first enclosing ``if`` (or else-branch) condition that keeps ``line`` of ``path`` (or, with ``col``, the
        expression starting there) from running in this job, as a kind; None when none does or it cannot be told."""
        tree = self.tree(path)
        if tree is None:
            return None
        found = self.condition(path, line, col)
        return found[0] if found else None

    def condition(self, path, line, col=None):
        """See _condition. Unknown (None) when the evaluation fails in any way: unknown never gates."""
        try:
            return self._condition(path, line, col)
        except Exception:  # noqa: BLE001 - a guard this job cannot read is unknown, not a crash
            return None

    def _condition(self, path, line, col=None):
        """(kind, the condition as written, whether the line needs it true) for the first ``if`` or conditional expression
        that keeps ``line`` from running in this job; None when none does. A conditional expression on one line counts
        when the branch it does not take holds code (not an empty literal)."""
        tree = self.tree(path)
        if tree is None:
            return None
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                # A branch on the condition's own line (``if x: y``) shares a line that runs: never gated.
                tested = node.test.end_lineno or node.lineno
                in_body = node.body and tested < node.body[0].lineno <= line <= node.body[-1].end_lineno
                in_else = node.orelse and tested < node.orelse[0].lineno <= line <= node.orelse[-1].end_lineno
                if not (in_body or in_else):
                    continue
                found = self.truth(path, node.test)
                if isinstance(found, tuple) and found[0] and found[1] == (not in_body):
                    return found[0], ast.unparse(node.test), bool(in_body)
            elif isinstance(node, ast.IfExp):
                if not (node.lineno <= line <= (node.end_lineno or node.lineno)):
                    continue
                found = self.truth(path, node.test)
                if not (isinstance(found, tuple) and found[0]):
                    continue
                skipped = node.body if found[1] is False else node.orelse
                if _empty(skipped):
                    continue
                if col is not None:
                    # One expression (a use, a call): gated when it sits inside the skipped branch.
                    inside = (skipped.lineno, skipped.col_offset) <= (line, col) <= (skipped.end_lineno, skipped.end_col_offset)
                else:
                    # A whole line: gated only when it holds nothing but the skipped branch; a line that also holds the
                    # test or the branch taken runs.
                    taken = node.orelse if skipped is node.body else node.body
                    shares = any(part.lineno <= line <= (part.end_lineno or part.lineno) for part in (node.test, taken))
                    inside = not shares and skipped.lineno <= line <= (skipped.end_lineno or skipped.lineno)
                if inside:
                    return found[0], ast.unparse(node.test), found[1] is False
        return None


def _empty(node):
    return (isinstance(node, ast.Constant) and node.value in (None, '', 0, False)) or (
        isinstance(node, (ast.Dict, ast.List, ast.Tuple, ast.Set)) and not getattr(node, 'keys', getattr(node, 'elts', None)))


def _reason(kind, facts):
    if kind == 'version':
        return f"Python {'.'.join(str(v) for v in (facts or {}).get('version', ['?'])[:2])}"
    if kind == 'platform':
        return (facts or {}).get('platform', 'this platform')
    if isinstance(kind, tuple) and kind[0] == 'missing':
        return f'{kind[1]} not installed'
    return 'this job'


def _bodies(text):
    """[(first, last)] of every function body in ``text`` (after its signature)."""
    return [(a, b) for a, b, _ in hunk_split._functions(text)]


def referenced(tree, names, skip=()):
    """Whether code in ``tree`` uses any of ``names`` (a name or an attribute), outside the line spans in ``skip``.
    Imports and string literals (``__all__``) are not uses."""
    for node in ast.walk(tree):
        used = (node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else None)
        if used in names and not any(a <= node.lineno <= b for a, b in skip):
            return True
    return False


def _uses(guards, files, name, skip_path=None, skip=()):
    """[(path, line)] of every use of ``name`` (a name or an attribute) in ``files``, outside ``skip`` spans of
    ``skip_path``."""
    found = []
    pattern = re.compile(r'\b' + re.escape(name) + r'\b')
    for path in files:
        text = guards.text(path)
        if not text or not pattern.search(text):
            continue
        tree = guards.tree(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            used = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else None
            if used == name and not (path == skip_path and any(a <= node.lineno <= b for a, b in skip)):
                found.append((path, node.lineno, node.col_offset))
    return found


def gate(guards, hunk, files, new_classes=None):
    """The condition that keeps a hunk from running here: around the hunk, around every call of the function holding
    it (found by name in the production files), or around every use of a class the change adds that holds it. None
    when nothing shows one."""
    line = (hunk['added'] or hunk['removed'] or [hunk['new_start']])[0]
    kind = guards.blocking(hunk['path'], line)
    if kind:
        return kind
    # A class the change adds, holding the hunk: it runs only where it is used.
    for top, (first, last) in (new_classes or {}).items():
        if any(first <= n <= last for n in hunk['added']):
            uses = _uses(guards, files, top, hunk['path'], [(first, last)])
            kinds = [guards.blocking(path, at, col) for path, at, col in uses]
            if uses and all(kinds):
                return kinds[0]
    holder = hunk.get('new_function')
    if not holder or '<locals>' in holder:
        return None
    name = holder.rsplit('.', 1)[-1]
    calls, gated = 0, []
    pattern = re.compile(r'\b' + re.escape(name) + r'\s*\(')
    for path in files:
        text = guards.text(path)
        if not text or not pattern.search(text):
            continue
        tree = guards.tree(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and ((isinstance(node.func, ast.Name) and node.func.id == name)
                                               or (isinstance(node.func, ast.Attribute) and node.func.attr == name)):
                calls += 1
                found = guards.blocking(path, node.lineno, node.col_offset)
                if not found:
                    return None
                gated.append(found)
    return gated[0] if calls and gated else None


OUTSIDE = ('scripts', 'examples')  # top-level folders that hold tools and samples, not the code a project ships


def _outside(guards, path, production):
    """Whether ``path`` sits in a top-level scripts/ or examples/ folder that no production module imports."""
    top = path.split('/', 1)[0]
    if top not in OUTSIDE or '/' not in path:
        return False
    pattern = re.compile(r'^\s*(?:from|import)\s+' + re.escape(top) + r'\b', re.M)
    return not any(pattern.search(guards.text(p) or '') for p in production if p.split('/', 1)[0] not in OUTSIDE)


def classify(root, edits, before_src, after_src, recorded, ran_before=(), python=None, production=None, own=(), incomplete=None):
    """Every hunk of the changed production files with its state. ``recorded``: the hunks evidence returned (with the
    tests that ran each on each side), or None when nothing was recorded. ``ran_before``: tests with an outcome on the
    code before the change. ``production``: the production .py files of the tree, for finding names and callers."""
    rows = recorded if recorded is not None else [dict(h, base_tests=None, head_tests=None)
                                                  for path, edit in edits.items() if path.endswith('.py') and not replay.is_test_file(path)
                                                  for h in hunk_split.split(path, edit, before_src.get(path) or '', after_src.get(path) or '')]
    production = list(production or [])
    guards = Guards(root, python, {p: t for p, t in after_src.items() if t is not None})
    ran_before, own = set(ran_before), set(own)
    out = []
    for h in rows:
        path = h['path']
        before, after = before_src.get(path) or '', after_src.get(path) or ''
        body = h.get('body') or h.get('rows') or []
        state, why, tests = None, '', []
        head = [t for t in (h.get('head_tests') or [])]
        existing = [t for t in head if t not in own]
        applied = _one_hunk(before, body, h['old_start']) if before else ''
        if _outside(guards, path, production):
            state, why = 'not-production', f"in {path.split('/', 1)[0]}/, which the package does not import"
        elif before and _same_code(before, applied):
            state = 'not-code'
        elif before and _same_without_annotations(before, applied):
            state, why = 'annotations-only', 'type annotations only'
        elif _typing_only(before, after, h):
            state, why = 'annotations-only', 'under `if TYPE_CHECKING:`, read by type checkers only'
        elif before and len(h.get('runs') or []) > 1:
            # A block that mixes a docstring or comment edit with a code edit: the quiet part is not code, and the code
            # part gets its own state below.
            runs = h['runs']
            quiet = [r for r in runs if _same_code(before, _one_hunk(before, r['rows'], r['old_start']))]
            if quiet and len(quiet) < len(runs):
                out.append(dict(path=path, line=(quiet[0]['added'] or [quiet[0]['new_start']])[0], state='not-code', why='',
                                tests=0, sample=[], base_tests=None, function=h.get('new_function') or h.get('old_function')))
                loud = [r for r in runs if r not in quiet]
                h = dict(h, added=[n for r in loud for n in r['added']], removed=[n for r in loud for n in r['removed']],
                         new_start=loud[0]['new_start'])
        if state is None and not [n for n in h['removed'] if (before.splitlines()[n - 1].strip() if n <= len(before.splitlines()) else '')]:
            old_defs, new_defs = _definitions(before), _definitions(after)
            added = [n for n in h['added'] if n <= len(after.splitlines()) and after.splitlines()[n - 1].strip()]
            fresh = {q: span for q, span in new_defs.items() if q not in old_defs}
            inside = added and all(any(a <= n <= b for a, b in fresh.values()) for n in added)
            if inside and not existing:
                names = {q.rsplit('.', 1)[-1] for q, (a, b) in fresh.items() if any(a <= n <= b for n in added)}
                pattern = re.compile(r'\b(' + '|'.join(re.escape(n) for n in sorted(names)) + r')\b')
                named = any(pattern.search(guards.text(p) or '') and guards.tree(p) is not None and referenced(guards.tree(p), names)
                            for p in production if p != path)
                own_file = guards.tree(path) is not None and referenced(guards.tree(path), names, list(fresh.values()))
                if not named and not own_file:
                    state, tests = 'new-code', head
        if state is None and h.get('head_tests') is None:
            state = 'not-recorded'
        if state is None and (head or h.get('head_at_import')):
            both = [t for t in head if t in ran_before]
            # Lines a test ran, when no test with an outcome before the change is among them, ran in the change's own
            # tests: the def line running at import on the way does not make that "at import".
            called = bool(h.get('head_ran_lines')) and bool(head)
            if both or (h.get('head_at_import') and not called):
                state, tests = 'executed-both-sides', both
                why = 'at import' if h.get('head_at_import') and not both else ''
            else:
                state, tests = 'executed-head-only', head
        line = (h['added'] or [h['new_start']])[0]
        new_classes = {q: span for q, span in _definitions(after).items() if '.' not in q and q not in _definitions(before)}
        if state in ('executed-both-sides', 'executed-head-only') and h.get('head_ran_lines') is not None:
            # Changed lines inside function bodies run only when the function is called: a hunk whose body lines no
            # test entered did not run, whatever its def or class lines did at import.
            bodies = [b for b in _bodies(after) if any(b[0] <= n <= b[1] for n in h.get('head') or [])]
            inside = [n for n in h.get('head') or [] if any(a <= n <= b for a, b in bodies)]
            if inside and not set(inside) & set(h['head_ran_lines']):
                state, why, tests = None, '', []
        if state is None:
            kind = gate(guards, h, production, new_classes)
            if kind:
                state, why = 'not-executable-here', f'not executable on {_reason(kind, guards.facts)} here'
            elif incomplete:
                # Some candidate tests gave no outcome: one of them may run this hunk. Not seen is not "ran in no test".
                state, why = 'not-recorded', incomplete
            else:
                state = 'executed-none'
        if state in ('executed-both-sides', 'executed-head-only', 'executed-none', 'new-code'):
            # A changed line under a condition false here did not run, though others of the hunk may have: named.
            ran_lines = set(h.get('head_ran_lines') or ())
            for n in h['added']:
                if n in ran_lines:
                    continue  # it ran: whatever a guard on it says, it is executable here
                found = guards.condition(path, n)
                if found:
                    state, line = 'not-executable-here', n
                    why = f"not executable on {_reason(found[0], guards.facts)} here ({'needs' if found[2] else 'needs not'} {found[1]})"
                    break
        out.append(dict(path=path, line=line, state=state, why=why, tests=len(tests),
                        sample=tests[:5], base_tests=h.get('base_test_count', len(h.get('base_tests') or []) if h.get('base_tests') is not None else None),
                        function=h.get('new_function') or h.get('old_function')))
    return out
