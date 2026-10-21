"""Diff-scoped mutation as a blindness proof (build queue part 4, behind a flag).

Mutants go only on changed lines of production files that the tests run (the reach tracer, on the passing candidates),
at most PER_FILE per file, and never on logging or configuration lines. Each mutant is one small edit of one line, by
AMU's own operators (mutmut's covered-lines mode needs its own runner and configuration, so it does not fit a run that
must drive the project's own runner): a comparison, a boolean or arithmetic operator turned the other way, a constant
changed, a condition negated, a return value replaced by None. The tests that ran the changed lines are run against each
mutant; a mutant they all pass is a survivor, listed as a test the change could break without any test noticing.
"""
from __future__ import annotations

import ast
import re
import time
from pathlib import Path

from . import runners, trust

PER_FILE = 7
TESTS = 40  # passing candidates run against each mutant, the best-ranked first
COMPARE = {ast.Eq: '!=', ast.NotEq: '==', ast.Lt: '>=', ast.GtE: '<', ast.Gt: '<=', ast.LtE: '>', ast.Is: 'is not',
           ast.IsNot: 'is', ast.In: 'not in', ast.NotIn: 'in'}
COMPARE_TEXT = {ast.Eq: '==', ast.NotEq: '!=', ast.Lt: '<', ast.GtE: '>=', ast.Gt: '>', ast.LtE: '<=', ast.Is: 'is',
                ast.IsNot: 'is not', ast.In: 'in', ast.NotIn: 'not in'}
BINARY = {ast.Add: ('+', '-'), ast.Sub: ('-', '+'), ast.Mult: ('*', '/'), ast.Div: ('/', '*')}
LOGGING = re.compile(r'\b(log|logger|logging|warnings\.warn|warn|print|debug|info|warning|error|exception|critical)\s*\(')
CONFIG = re.compile(r'^[A-Z][A-Z0-9_]*\s*[:=]')


def _offsets(line):
    """Map a UTF-8 byte column (ast's col_offset) to a character index of ``line``."""
    table, count = {}, 0
    for index, char in enumerate(line):
        table[count] = index
        count += len(char.encode())
    table[count] = len(line)
    return table


def mutants(source, lines):
    """[(line, description, new source)] for the changed ``lines`` of ``source``: one candidate per operator found on a
    single-line statement there, in line order."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    text = source.splitlines(keepends=True)
    found = []
    # A one-line statement, or the condition of an if or while whose condition sits on one changed line.
    statements = [n for n in ast.walk(tree) if isinstance(n, ast.stmt) and n.lineno in lines and (
        n.lineno == n.end_lineno and not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom))
        or isinstance(n, (ast.If, ast.While)) and n.test.lineno == n.test.end_lineno == n.lineno)]
    for statement in sorted(statements, key=lambda n: n.lineno):
        number = statement.lineno
        line = text[number - 1]
        if LOGGING.search(line) or (statement.col_offset == 0 and CONFIG.match(line.lstrip())):
            continue
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
            continue  # a docstring
        where = _offsets(line)

        def swap(start, end, new, what):
            begin, stop = where.get(start), where.get(end)
            if begin is None or stop is None:
                return
            changed = line[:begin] + new + line[stop:]
            if changed != line:
                found.append((number, f'{what}: {line[begin:stop].strip()} -> {new.strip()}',
                              ''.join(text[:number - 1]) + changed + ''.join(text[number:])))
        for node in ast.walk(statement.test if isinstance(statement, (ast.If, ast.While)) else statement):
            if getattr(node, 'lineno', number) != number or getattr(node, 'end_lineno', number) != number:
                continue
            if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in COMPARE:
                start, end = node.left.end_col_offset, node.comparators[0].col_offset
                gap = line.encode()[start:end].decode(errors='replace')
                old = COMPARE_TEXT[type(node.ops[0])]
                if old in gap:
                    swap(start, end, gap.replace(old, COMPARE[type(node.ops[0])], 1), 'comparison')
            elif isinstance(node, ast.BinOp) and type(node.op) in BINARY:
                start, end = node.left.end_col_offset, node.right.col_offset
                gap = line.encode()[start:end].decode(errors='replace')
                old, new = BINARY[type(node.op)]
                if gap.count(old) == 1:
                    swap(start, end, gap.replace(old, new), 'arithmetic')
            elif isinstance(node, ast.BoolOp) and len(node.values) == 2:
                start, end = node.values[0].end_col_offset, node.values[1].col_offset
                gap = line.encode()[start:end].decode(errors='replace')
                old, new = ('and', 'or') if isinstance(node.op, ast.And) else ('or', 'and')
                if re.search(rf'\b{old}\b', gap):
                    swap(start, end, re.sub(rf'\b{old}\b', new, gap, count=1), 'boolean')
            elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
                swap(node.col_offset, node.end_col_offset, str(not node.value), 'constant')
            elif isinstance(node, ast.Constant) and type(node.value) is int:
                swap(node.col_offset, node.end_col_offset, str(node.value + 1), 'constant')
        if isinstance(statement, ast.Return) and statement.value is not None and not (
                isinstance(statement.value, ast.Constant) and statement.value.value is None):
            swap(statement.value.col_offset, statement.value.end_col_offset, 'None', 'return value')
        if isinstance(statement, (ast.If, ast.While)):
            test = statement.test
            begin, stop = where.get(test.col_offset), where.get(test.end_col_offset)
            if begin is not None and stop is not None:
                swap(test.col_offset, test.end_col_offset, 'not (' + line[begin:stop] + ')', 'condition negated')
    # One per operator kind and line first, so a file's cap spreads over its lines.
    seen, unique = set(), []
    for row in found:
        if (row[0], row[2]) not in seen:
            seen.add((row[0], row[2]))
            unique.append(row)
    return unique


def _pick(candidates, cap):
    """Up to ``cap`` of [(line, ...)], one per line in turn, so the cap spreads over the covered lines."""
    by_line = {}
    for row in candidates:
        by_line.setdefault(row[0], []).append(row)
    picked = []
    while len(picked) < cap and any(by_line.values()):
        for line in sorted(by_line):
            if by_line[line] and len(picked) < cap:
                picked.append(by_line[line].pop(0))
    return picked


def run(runner, python, tree, after_src, changed, passing, scratch, seconds, write):
    """Mutants on the covered changed lines of ``after_src`` ({path: text}, production files), run by ``passing`` (ids
    that passed after the change, best first) in ``tree``, which holds the changed code. ``changed`` is {path: lines}.
    ``write(path, text)`` puts a file into ``tree``; the changed version is written back after each mutant. Returns
    (info, gaps)."""
    started, gaps = time.monotonic(), []
    info = dict(covered_lines=0, mutants=0, killed=0, survived=0, unfinished=0, survivors=[], tests=0)
    tests = list(passing)[:TESTS]
    if not tests or not changed:
        info['seconds'] = 0.0
        return info, gaps

    def left():
        return seconds - (time.monotonic() - started)
    folder = trust.support(scratch, trust.changed_lines(tree, {p: {} for p in changed}, lambda path, edit: changed[path]))
    where = Path(scratch)/'mutation-reach'
    runners.execute(runner, python, tree, tests, Path(scratch)/'mutation-reach.xml', max(10, min(left(), 120)),
                    extra_env=trust._env(folder, reach=where))
    hits = trust._hits(where) or set()
    covered = {}
    for hit in hits:
        name, _, number = hit.rpartition(':')
        for path in changed:
            if name in (str(Path(tree)/path), str((Path(tree)/path).resolve())):
                covered.setdefault(path, set()).add(int(number))
    info.update(covered_lines=sum(len(v) for v in covered.values()), tests=len(tests))
    for path in sorted(covered):
        for number, what, text in _pick(mutants(after_src[path], covered[path]), PER_FILE):
            if left() < 10:
                info['unfinished'] += 1
                continue
            info['mutants'] += 1
            write(path, text)
            try:
                found, problem = runners.execute(runner, python, tree, tests, Path(scratch)/'mutant.xml', max(10, min(left(), 120)))
            finally:
                write(path, after_src[path])
            states = set(found.values())
            if problem == 'timed out' or not states or states <= {'unrunnable'}:
                info['unfinished'] += 1
                info['mutants'] -= 1
            elif 'failed' in states:
                info['killed'] += 1
            else:
                info['survived'] += 1
                info['survivors'].append(dict(file=path, line=number, mutant=what, tests=len(tests)))
    if info['unfinished']:
        gaps.append(f"mutation: {info['unfinished']} mutant(s) not run to the end in the {round(seconds)} s allowed")
    info['seconds'] = round(time.monotonic() - started, 1)
    return info, gaps
