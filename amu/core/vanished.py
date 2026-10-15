"""Candidates for a definition the change renamed or removed.

With the change on disk, the index is built from the edited tree: a function the change renamed or removed is not in
it, and references to the old name no longer resolve, so nothing reaches it. Its tests are found here instead, by the
old name: tests whose source still names it, tests in files that import or use it at module level, tests that reach
code that still names it (its callers), tests whose names share its words, and tests in files that have changed
together with its file. Nothing is imported or executed.

A name is only evidence where it can mean the old definition. A method's name counts in files that also name its
class; a name still defined in another module counts in files that also name the old module.
"""
from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path, PurePosixPath

from . import rank
from .git import _git

COMMITS = 200       # history read for co-change
WIDE = 100          # a commit touching more files than this is not evidence of coupling
NAME_ONLY = 0.5     # a test only named like the old definition is weaker evidence than a reference
CO_CHANGE = 0.4     # a test in a file that changes with the old definition's file
MODULE_LEVEL = 0.8  # a test file that imports or uses the old name at module level: every test in it fails to load


def deleted(patch):
    """{path: source before the change} for Python files the patch deletes, rebuilt from the removed lines."""
    found, path, lines = {}, None, None
    for line in patch.splitlines(keepends=True):
        if line.startswith('--- '):
            raw = line[4:].strip().split('\t')[0]
            path, lines = (raw[2:] if raw.startswith('a/') else raw), None
        elif line.startswith('+++ '):
            raw = line[4:].strip().split('\t')[0]
            pure = PurePosixPath(path or '')
            if raw == '/dev/null' and pure.suffix in ('.py', '.pyi') and not pure.is_absolute() and '..' not in pure.parts:
                lines = found.setdefault(path, [])
        elif lines is not None and line.startswith('-'):
            lines.append(line[1:])
    return {path: ''.join(lines) for path, lines in found.items()}


def gone(before, after):
    """Records the change took away: top-level functions, classes and module constants, and methods of classes that
    remain, present before and absent after under the same qualified name in the same file. Local closures are not
    importable and are covered by their enclosing function; the methods of a class that is gone are covered by the
    class. A name the module still binds or imports (a re-export) is not gone; one served only through a module
    ``__getattr__`` is, since such a shim usually warns, and tests that turn warnings into errors fail on it."""
    kept = {(r['path'], r['qual']) for r in after.values()}
    modules = {r['path']: r for r in after.values() if r['kind'] == 'module'}
    found = {}
    for ident, r in before.items():
        leaf = r['qual'].rsplit('.', 1)[-1]
        owner = before.get(r.get('owner')) or {}
        if (r['kind'] not in ('function', 'class', 'variable') or (r['path'], r['qual']) in kept
                or len(leaf) < 3 or leaf.startswith('__')):
            continue
        if owner.get('kind') == 'function' or (owner.get('kind') == 'class' and (r['kind'] == 'variable'
                                                                              or (owner['path'], owner['qual']) not in kept)):
            continue
        module = modules.get(r['path'], {})
        if owner.get('kind') != 'class' and (leaf in module.get('imports', {}) or leaf in module.get('bindings', {})):
            continue
        found[ident] = r
    return found


def _text(root, path, cache):
    if path not in cache:
        try:
            cache[path] = (Path(root)/path).read_text(errors='replace').splitlines()
        except OSError:
            cache[path] = []
    return cache[path]


def _mentions(record, pattern, root, cache):
    lines = _text(root, record['path'], cache)
    return any(pattern.search(line) for start, end in record.get('spans', ()) for line in lines[max(0, start - 1):end])


def _module_level(root, path, pattern, cache):
    """Whether the module's own statements (imports, assignments, decorators' arguments outside functions) name it."""
    lines = _text(root, path, cache)
    try:
        body = ast.parse('\n'.join(lines)).body
    except (SyntaxError, ValueError):
        return False
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            parts = [*node.decorator_list, *getattr(node, 'bases', [])]
            spans = [(part.lineno, part.end_lineno) for part in parts]
        else:
            spans = [(node.lineno, node.end_lineno)]
        if any(pattern.search(line) for start, end in spans for line in lines[start - 1:end]):
            return True
    return False


def co_changed(root, path):
    """{file: share of the commits touching ``path`` that also touched it}, over recent history. A commit without a
    parent (the root, or a shallow clone's boundary, which lists the whole tree) and a commit touching more than
    WIDE files (a reformat, a vendored update) say nothing about which files belong together; they are not counted.
    Git failing or timing out gives no co-change evidence, not an error."""
    try:
        rows = [line.split() for line in _git(root, 'log', f'-n{COMMITS}', '--format=%H %P', '--', path).splitlines()]
        shas = [row[0] for row in rows if len(row) > 1]
        if not shas:
            return {}
        listing = _git(root, 'show', '--name-only', '--format=@%H', *shas)
    except (OSError, subprocess.SubprocessError):
        return {}
    counts, total = {}, 0
    for block in listing.split('@')[1:]:
        names = {name for name in block.splitlines()[1:] if name}
        if len(names) > WIDE:
            continue
        total += 1
        for name in names - {path}:
            counts[name] = counts.get(name, 0) + 1
    return {name: count / total for name, count in counts.items()} if total else {}


def _word(name):
    return re.compile(r'\b' + re.escape(name) + r'\b')


def _module_name(path):
    pure = PurePosixPath(path)
    return pure.parent.name if pure.stem == '__init__' else pure.stem


def candidates(root, graph, tests, removed, affinity, content, before=None):
    """{test id: {key: score}} and {test id: (value, position, path, shared, why)} for the removed definitions."""
    scores, best, cache, co = {}, {}, {}, {}
    before = before or {}
    files = {r['path'] for r in tests.values()}
    defined = {}
    for r in graph.records.values():
        if r['kind'] in ('function', 'class', 'variable') and not graph.is_test(r):
            defined.setdefault(r['qual'].rsplit('.', 1)[-1], set()).add(r['path'])
    for ident, record in removed.items():
        leaf = record['qual'].rsplit('.', 1)[-1]
        old = f"{record['path']}#{record['qual']}"
        key = f'{old} (renamed or removed)'
        names = rank.symbol_tokens(record)
        query = content.query(leaf.replace('_', ' '))
        pattern = _word(leaf)
        owner = before.get(record.get('owner')) or {}
        if owner.get('kind') == 'class':
            scope = _word(owner['qual'].rsplit('.', 1)[-1])
        elif defined.get(leaf, set()) - {record['path']}:
            scope = _word(_module_name(record['path']))
        else:
            scope = None

        def add(test, value, position, path, shared, why):
            scores.setdefault(test, {})[key] = max(value, scores.get(test, {}).get(key, 0.0))
            if test not in best or value > best[test][0]:
                best[test] = (value, position, path, shared, why)
        holders = set()
        for path in files | {r['path'] for r in graph.records.values()}:
            lines = _text(root, path, cache)
            if not lines:
                continue
            text = '\n'.join(lines)
            if leaf in text and pattern.search(text) and (scope is None or path == record['path'] or scope.search(text)):
                holders.add(path)
        # Tests whose own source still names the old definition.
        for test, r in tests.items():
            if r['path'] in holders and _mentions(r, pattern, root, cache):
                match, shared = affinity.score(test, names)
                add(test, rank.structural((1, 0, False)) * (1 + 2 * match + 2 * content.score(test, query)), (1, 0, False), [test], shared,
                    f'names {old}, renamed or removed')
        # Test files that import or use it at module level: none of their tests loads.
        for path in sorted(holders & files):
            if _module_level(root, path, pattern, cache):
                for test, r in tests.items():
                    if r['path'] == path:
                        match, shared = affinity.score(test, names)
                        add(test, MODULE_LEVEL * rank.structural((1, 0, False)) * (1 + 2 * match), (1, 1, False), [test], shared,
                            f'its module imports or uses {old}, renamed or removed')
        # Code that still names it is its callers; the tests that reach them reach what it was.
        callers = [other for other, r in graph.records.items()
                   if r['path'] in holders and not graph.is_test(r) and r['kind'] == 'function' and _mentions(r, pattern, root, cache)]
        for test, (position, path, _) in graph.reach(callers).items():
            if test in tests:
                depth, specificity, approximate = position
                moved = (depth + 1, specificity, approximate)
                match, shared = affinity.score(test, names)
                add(test, rank.structural(moved) * (1 + 2 * match), moved, path, shared, f'which names {old}, renamed or removed')
        # Tests named like it, and tests in files that change with its file.
        if record['path'] not in co:
            co[record['path']] = co_changed(root, record['path'])
        for test, r in tests.items():
            match, shared = affinity.score(test, names)
            share = co[record['path']].get(r['path'], 0.0)
            if match >= 0.5:
                add(test, NAME_ONLY * match, (2, 1, True), [test], shared, f'named like {old}, renamed or removed')
            if share and match > 0:
                add(test, CO_CHANGE * share * (1 + 2 * match), (2, 2, True), [test], shared,
                    f"its file changes with {record['path']}; {old} renamed or removed")
    return scores, best
