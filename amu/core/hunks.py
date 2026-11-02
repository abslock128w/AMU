"""A change as hunks, and the lines that tell whether a test ran each one.

Each hunk of a changed Python file gets two sets of lines to watch while the tests run:

- on the code before the change (``base``): the body of the innermost function holding the hunk there, so any test that
  entered that function counts; for a hunk outside any function, its removed lines and the context lines around it;
- on the code after it (``head``): the lines the hunk adds, or, for a hunk that only removes, the lines around the gap.

A hunk with no function around it on the base side and nothing it removed there (code the change adds, such as a new
function) has no base lines: no run of the code before the change can say which tests reach it.
"""
from __future__ import annotations

import ast


def _functions(text):
    """[(first body line, last line, qualified name)] of every function in ``text``; [] when it does not parse."""
    try:
        tree = ast.parse(text or '')
    except (SyntaxError, ValueError):
        return []
    found = []

    def walk(nodes, prefix):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = prefix + node.name
                if node.body:
                    found.append((node.body[0].lineno, node.end_lineno, qual))
                walk(node.body, qual + '.<locals>.')
            elif isinstance(node, ast.ClassDef):
                walk(node.body, prefix + node.name + '.')
            elif hasattr(node, 'body') and isinstance(getattr(node, 'body'), list):
                walk(node.body, prefix)
                walk(getattr(node, 'orelse', []) or [], prefix)
                walk(getattr(node, 'finalbody', []) or [], prefix)
                for handler in getattr(node, 'handlers', []) or []:
                    walk(handler.body, prefix)
    walk(tree.body, '')
    return found


def innermost(functions, lines):
    """The narrowest function whose body holds every line in ``lines``, as (first, last, qual); None if none does."""
    lines = sorted(lines)
    if not lines:
        return None
    holding = [f for f in functions if f[0] <= lines[0] and lines[-1] <= f[1]]
    return min(holding, key=lambda f: f[1] - f[0]) if holding else None


def split(path, edit, before, after):
    """The hunks of one file's change, each a dict: path, index, removed and added line numbers, context lines on each
    side, the function holding it on each side, and the lines to watch on each side (``base``, ``head``)."""
    old_functions, new_functions = _functions(before), _functions(after)
    found = []
    for index, (old_start, body, new_start) in enumerate(edit.get('hunks', [])):
        old_line, new_line = old_start, new_start
        removed, added, old_context, new_context = [], [], [], []
        for row in body:
            sign = row[:1]
            if sign == '-':
                removed.append(old_line)
                old_line += 1
            elif sign == '+':
                added.append(new_line)
                new_line += 1
            else:
                old_context.append(old_line)
                new_context.append(new_line)
                old_line += 1
                new_line += 1
        # Where the hunk sits on each side: what it removes or adds, else the gap between its context lines.
        old_here = removed or [n for n in old_context if n < (old_context[-1] if old_context else 0)][-1:] or old_context[:1]
        new_here = added or [n for n in new_context if n < (new_context[-1] if new_context else 0)][-1:] or new_context[:1]
        old_function = innermost(old_functions, removed or old_here)
        new_function = innermost(new_functions, added or new_here)
        if old_function:
            base = set(range(old_function[0], old_function[1] + 1))
        elif removed:
            base = set(removed) | set(old_context)
        elif new_function is None:
            base = set(old_context)  # module or class level: the lines around the insertion run when it runs
        else:
            base = set()  # inside a function the code before the change does not have
        head = set(added) if added else set(new_context)
        found.append(dict(path=path, index=index, removed=removed, added=added, old_start=old_start, new_start=new_start,
                          old_function=old_function[2] if old_function else None,
                          new_function=new_function[2] if new_function else None,
                          base=sorted(base), head=sorted(head)))
    return found


def name(hunk):
    """``path:line`` where the hunk starts on the code after the change."""
    line = (hunk['added'] or [hunk['new_start']])[0]
    return f"{hunk['path']}:{line}"
