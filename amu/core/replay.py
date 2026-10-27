"""The pull request's own tests, replayed against the code before the change.

A test the change adds or edits is run twice: on the changed tree, and on the code before the change with the tests
as the change leaves them. That is the fail-to-pass oracle SWT-bench scores:
- fails on the code before, passes after: the change does what this test checks (change evidence, the PR's own test);
- passes on both: the test says nothing about the change;
- fails on both: the test does not pass on the change it came with (a gap).
"""
from __future__ import annotations

import ast
import re
from pathlib import PurePosixPath

from .patch import candidate, original

EVIDENCE = "change evidence: the PR's own test"
NEW_CODE = 'new test for new code'
# What a test that only uses what the change adds raises on the code before it.
MISSING = (re.compile(r"\b(ImportError|ModuleNotFoundError): cannot import name '([\w.]+)'"),
           re.compile(r"\b(ModuleNotFoundError|ImportError): No module named '([\w.]+)'"),
           re.compile(r"\b(AttributeError): .*?has no attribute '(\w+)'"),
           re.compile(r"\b(NameError): name '(\w+)' is not defined"))


def added_names(sources):
    """Names the change adds outside test files, from ``sources`` {path: (before text or None, after text or None)}:
    functions, classes and assigned names at module or class level that the file did not define before, and the dotted
    module of a file that did not exist before."""
    def defined(text):
        try:
            body = ast.parse(text or '').body
        except (SyntaxError, ValueError):
            return set()
        names = set()

        def visit(nodes):
            for node in nodes:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.add(node.name)
                    if isinstance(node, ast.ClassDef):
                        visit(node.body)
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                        names.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name))
        visit(body)
        return names
    added = set()
    for path, (before, after) in sources.items():
        if not path.endswith('.py') or is_test_file(path) or after is None:
            continue
        added |= defined(after) - defined(before)
        if not before:
            module = path[:-3].replace('/', '.')
            added.add(module[:-len('.__init__')] if module.endswith('.__init__') else module)
    return added


def new_code_error(text, names):
    """(exception, name) when ``text`` shows an ImportError, ModuleNotFoundError, AttributeError or NameError on one of
    ``names`` (what the change adds), else None."""
    modules = {name for name in names if '.' in name}
    for pattern in MISSING:
        for exception, name in pattern.findall(text or ''):
            if name in names or name.rsplit('.', 1)[-1] in names or any(m == name or m.startswith(name + '.') or
                                                                        name.startswith(m + '.') for m in modules):
                return exception, name
    return None


def mentions(output, path):
    """Whether a runner's ``output`` names the test file ``path``, as a path or as a dotted module."""
    module = path[:-3].replace('/', '.') if path.endswith('.py') else path
    return path in (output or '') or bool(re.search(r'\b' + re.escape(module) + r'\b', output or ''))


def references(text, name):
    """Whether the source ``text`` mentions ``name`` (its last dotted part) as a word."""
    return bool(re.search(r'\b' + re.escape(name.rsplit('.', 1)[-1]) + r'\b', text or ''))


def is_test_file(path):
    """A file the change's tests live in: under a tests directory, test_*.py, *_test.py, tests.py or conftest.py.
    A ``test`` directory is not enough: ``django/test/`` is production code."""
    parts = PurePosixPath(path).parts
    name = parts[-1] if parts else ''
    return name.endswith('.py') and ('tests' in parts[:-1] or name.startswith('test_')
                                     or name.endswith('_test.py') or name in ('tests.py', 'conftest.py'))


def definitions(text, module_level=True):
    """{'Class::test': fingerprint} for the tests a runner collects from ``text``: test* methods of classes, and test*
    functions at module level when the runner collects those (pytest). None when the text does not parse.

    The fingerprint is the test's own syntax plus every module-level assignment it reads, followed through the names
    those read, so a test whose parametrize table or expected value changed counts as edited."""
    try:
        body = ast.parse(text or '').body
    except SyntaxError:
        return None
    bindings = {}
    for node in body:
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                for name in ast.walk(target):
                    if isinstance(name, ast.Name):
                        bindings.setdefault(name.id, []).append(node)

    def data(node):
        pending, seen = [n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)], set()
        while pending:
            name = pending.pop()
            if name in seen or name not in bindings:
                continue
            seen.add(name)
            for bound in bindings[name]:
                pending.extend(n.id for n in ast.walk(bound.value or bound) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load))
        return [(name, [ast.dump(b, include_attributes=False) for b in bindings[name]]) for name in sorted(seen)]
    found = {}

    def visit(nodes, prefix):
        for node in nodes:
            if isinstance(node, ast.ClassDef):
                visit(node.body, prefix + node.name + '::')
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('test') and (prefix or module_level):
                found[prefix + node.name] = repr((ast.dump(node, include_attributes=False), data(node)))
    visit(body, '')
    return found


def touched(root, edits, runner_name='pytest'):
    """Ids (path::Class::test) of the tests the change adds or whose definition it edits, in patch order."""
    ids = []
    for path, edit in edits.items():
        if not is_test_file(path) or PurePosixPath(path).name == 'conftest.py':
            continue
        after = definitions(candidate(root, path, edit), runner_name == 'pytest')
        before = definitions(original(root, path, edit) or '', runner_name == 'pytest') or {}
        if not after:
            continue
        ids += [f'{path}::{name}' for name, dump in after.items() if before.get(name) != dump]
    return ids


def classify(head, base):
    """One replayed test's verdict from its state after the change (head) and on the code before it (base).
    Returns 'evidence', 'none', 'fails_both', 'fails_head' or None when either side has no outcome."""
    if head == 'passed' and base in ('failed', 'unloadable'):
        return 'evidence'
    if head == 'passed' and base == 'passed':
        return 'none'
    if head == 'failed' and base in ('failed', 'unloadable'):
        return 'fails_both'
    if head == 'failed' and base == 'passed':
        return 'fails_head'
    if head == 'skipped' or base == 'skipped':
        return 'none'
    return None
