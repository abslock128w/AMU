"""Which hunk of the change makes a flipped test fail: delta debugging over independently applied hunks (brief 4, C2).

The change is split into its hunks in production files; test files keep every hunk. A trial applies a subset of the
production hunks to a copy of the code before the change and runs the flipped test once. ``ddmin`` (Zeller) narrows
the set to a minimal subset that still fails, in a logarithmic number of trials when one hunk is the cause. A subset
that does not run (a hunk that needs another to import, say) counts as unresolved, not as passing. A change with a
single production hunk names it without running anything. Trials are bounded in number and time; a search cut short
says so and reports the smallest failing set found.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from . import runners
from .replay import is_test_file

TRIALS = 16


def hunks(edits):
    """[(path, index)] for every hunk of every edited production file, in patch order."""
    return [(path, n) for path, edit in edits.items() if path.endswith('.py') and not is_test_file(path)
            for n in range(len(edit.get('hunks') or ()))]


def apply(before, edit, chosen):
    """``before`` with the hunks whose indexes are in ``chosen`` applied; None when the text does not match."""
    lines, out, position = before.splitlines(keepends=True), [], 0
    for n, (start, body, *_) in enumerate(edit['hunks']):
        start = max(start - 1, 0)
        if start < position or start > len(lines):
            return None
        out.extend(lines[position:start])
        position = start
        for row in body:
            sign, text = row[:1], row[1:]
            if sign == '+':
                if n in chosen:
                    out.append(text)
                continue
            if position >= len(lines) or lines[position].rstrip('\r\n') != text.rstrip('\r\n'):
                return None
            if sign == ' ' or n not in chosen:
                out.append(lines[position])
            position += 1
    out.extend(lines[position:])
    return ''.join(out)


def head_lines(edit, n):
    """The lines of hunk ``n`` on the changed side: its added lines, or for a pure removal the line it left."""
    start, body, *rest = edit['hunks'][n]
    line, added = (rest[0] if rest else start), []
    for row in body:
        if row[:1] == '+':
            added.append(line)
            line += 1
        elif row[:1] == ' ':
            line += 1
    return (added[0], added[-1]) if added else (max(1, line - 1), max(1, line - 1))


def ddmin(items, test, budget):
    """Zeller's ddmin. ``test(subset)`` returns 'fail', 'pass' or 'unresolved'; ``budget()`` says whether another trial
    fits. Returns (smallest failing subset found, finished)."""
    current, n = list(items), 2
    while len(current) >= 2:
        chunks = [current[i * len(current) // n:(i + 1) * len(current) // n] for i in range(n)]
        reduced = False
        for chunk in chunks:
            if not budget():
                return current, False
            if test(chunk) == 'fail':
                current, n, reduced = chunk, 2, True
                break
        if not reduced:
            for chunk in chunks:
                rest = [x for x in current if x not in chunk]
                if not rest:
                    continue
                if not budget():
                    return current, False
                if test(rest) == 'fail':
                    current, n, reduced = rest, max(n - 1, 2), True
                    break
        if not reduced:
            if n >= len(current):
                break
            n = min(len(current), 2 * n)
    return current, True


def run(runner, python, before_tree, edits, before_src, flip_id, scratch, seconds):
    """The minimal set of production hunks that makes ``flip_id`` fail. Returns dict(hunks=[...], trials, finished)."""
    items = hunks(edits)
    if not items:
        return None
    if len(items) == 1:
        return dict(hunks=[_where(edits, *items[0])], trials=0, finished=True)
    started, trials = time.monotonic(), [0]
    copy = Path(scratch)/'c2-tree'
    if not copy.exists():
        shutil.copytree(before_tree, copy, symlinks=True, ignore=shutil.ignore_patterns('.git', '.amu', '__pycache__'))
    tests = [path for path in edits if is_test_file(path)]
    from .patch import candidate
    for path in tests:
        text = candidate(before_tree, path, edits[path])
        if text is not None:
            (copy/path).parent.mkdir(parents=True, exist_ok=True)
            (copy/path).write_text(text)

    def test(subset):
        trials[0] += 1
        chosen = {}
        for path, n in subset:
            chosen.setdefault(path, set()).add(n)
        for path in {p for p, _ in items}:
            text = apply(before_src.get(path) or '', edits[path], chosen.get(path, set()))
            if text is None:
                return 'unresolved'
            (copy/path).write_text(text)
        found, _ = runners.execute(runner, python, copy, [flip_id], Path(scratch)/'c2-trial.xml',
                                   max(10, seconds - (time.monotonic() - started)))
        state = found.get(flip_id)
        return 'fail' if state == 'failed' else 'pass' if state in ('passed', 'skipped') else 'unresolved'

    def budget():
        return trials[0] < TRIALS and time.monotonic() - started < seconds
    smallest, finished = ddmin(items, test, budget)
    for path in {p for p, _ in items}:  # leave the copy as the code before the change
        (copy/path).write_text(before_src.get(path) or '')
    return dict(hunks=[_where(edits, path, n) for path, n in smallest], trials=trials[0], finished=finished)


def _where(edits, path, n):
    first, last = head_lines(edits[path], n)
    return dict(file=path, hunk=n + 1, of=len(edits[path]['hunks']), start=first, end=last)
