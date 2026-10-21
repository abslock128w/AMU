"""Search for an input on which a changed function's two versions differ (build queue part 4, behind a flag).

For changed module-level functions that are annotated (every parameter and the return) and look pure (no global or
nonlocal, no yield, no attribute or item stores, no print, open, input, exec or eval), the two versions are compared
by CrossHair's ``diffbehavior`` and by a Hypothesis ``equivalent()`` test from its ghostwriter, at most SECONDS each
per function, in the project's interpreter. A row is reported only when one of them shows a concrete input on which the
two versions give different results. Both tools must be installed there; without them this says so in a gap.
"""
from __future__ import annotations

import ast
import re
import subprocess
import time
from pathlib import Path

from . import runners
from .index import module_name

SECONDS = 30
IMPURE = {'print', 'open', 'input', 'exec', 'eval', 'setattr', 'delattr', 'compile', '__import__'}


def eligible(source, name):
    """Whether the module-level function ``name`` in ``source`` is annotated and looks pure."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return False
    node = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name), None)
    if node is None or node.returns is None:
        return False
    args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    if node.args.vararg or node.args.kwarg or any(a.annotation is None for a in args):
        return False
    for inner in ast.walk(node):
        if isinstance(inner, (ast.Global, ast.Nonlocal, ast.Yield, ast.YieldFrom, ast.Await)):
            return False
        if isinstance(inner, (ast.Attribute, ast.Subscript)) and isinstance(inner.ctx, (ast.Store, ast.Del)):
            return False
        if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) and inner.func.id in IMPURE:
            return False
    return True


def _crosshair(python, tree, before, after, seconds):
    try:
        done = subprocess.run([python, '-m', 'crosshair', 'diffbehavior', before, after, '--per_path_timeout', '2'],
                              cwd=tree, capture_output=True, text=True, timeout=seconds, stdin=subprocess.DEVNULL,
                              env=runners.environment(tree))
    except subprocess.TimeoutExpired:
        return None, 'no difference found in the time allowed'
    output = (done.stdout + done.stderr).strip()
    if 'No module named crosshair' in output:
        return None, 'crosshair is not installed in the interpreter'
    found = re.search(r'Given: \((.*?)\),\s*\n\s*(.+?)\s*\n\s*(.+?)(?:\n|$)', output, re.S)
    return (dict(tool='crosshair', input=found.group(1)[:300], before=found.group(2)[:300], after=found.group(3)[:300])
            if found else None), None


def _hypothesis(python, tree, before, after, scratch, seconds):
    try:
        written = subprocess.run([python, '-m', 'hypothesis', 'write', '--equivalent', before, after], cwd=tree,
                                 capture_output=True, text=True, timeout=seconds, stdin=subprocess.DEVNULL,
                                 env=runners.environment(tree))
    except subprocess.TimeoutExpired:
        return None, 'the Hypothesis ghostwriter did not finish'
    if written.returncode or 'def test_' not in written.stdout:
        tail = (written.stderr or written.stdout).strip().splitlines()[-1:] or ['no output']
        return None, f'the Hypothesis ghostwriter wrote no test: {tail[0][:160]}'
    test = Path(scratch)/'test_amu_equivalent.py'
    test.write_text(written.stdout)
    try:
        done = subprocess.run([python, '-m', 'pytest', '-q', '-x', '-p', 'no:cacheprovider', str(test)], cwd=tree,
                              capture_output=True, text=True, timeout=seconds, stdin=subprocess.DEVNULL,
                              env=runners.environment(tree))
    except subprocess.TimeoutExpired:
        return None, 'no difference found in the time allowed'
    found = re.search(r'Falsifying example: (.+?)(?:\n\n|\nE |$)', done.stdout, re.S)
    differs = re.search(r'AssertionError: (.+)', done.stdout)
    return (dict(tool='hypothesis', input=found.group(1).strip()[:300], before='', after=(differs.group(1) if differs else '')[:300])
            if done.returncode and found else None), None


def run(python, tree, targets, before_src, after_src, scratch, seconds_each=SECONDS, left=lambda: float('inf')):
    """Rows for ``targets`` (sidebyside.targets entries) whose two versions differ on a concrete input. ``tree`` holds
    the changed code; the two versions are written next to their module as private modules and taken away after.
    Returns (info, rows, gaps)."""
    started, rows, gaps, problems = time.monotonic(), [], [], set()
    info = dict(eligible=0, compared=0, differ=0)
    for target in targets:
        path, qual = target['id'].split('#', 1)
        if '.' in qual or path not in before_src or path not in after_src:
            continue
        if not (eligible(before_src[path], qual) and eligible(after_src[path], qual)):
            continue
        info['eligible'] += 1
        if left() < 2 * seconds_each:
            gaps.append('equivalence: the time allowed ended before every eligible function was compared')
            break
        module = module_name(path)
        package, _, stem = module.rpartition('.')
        names = {side: f"_amu_eq_{side}_{stem}" for side in ('before', 'after')}
        files = {side: Path(tree)/Path(path).parent/(names[side] + '.py') for side in names}
        dotted = {side: (package + '.' if package else '') + names[side] + '.' + qual for side in names}
        try:
            files['before'].write_text(before_src[path])
            files['after'].write_text(after_src[path])
            info['compared'] += 1
            for found, problem in (_crosshair(python, tree, dotted['before'], dotted['after'], seconds_each),
                                   _hypothesis(python, tree, dotted['before'], dotted['after'], scratch, seconds_each)):
                if problem and 'installed' in problem or problem and 'wrote no test' in problem:
                    problems.add(problem)
                if found:
                    rows.append(dict(target=target['id'], **found))
                    break
        finally:
            for file in files.values():
                file.unlink(missing_ok=True)
    info['differ'] = len(rows)
    gaps += [f'equivalence: {problem}' for problem in sorted(problems)]
    info['seconds'] = round(time.monotonic() - started, 1)
    return info, rows, gaps
