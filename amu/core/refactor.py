"""Atomic-change labels: a function or class the change only renames or moves (build queue part 4, behind a flag).

PyRef, the refactoring detector the queue names, is not on PyPI (the ``pyref`` there is an X-ray reflectivity package),
so this is AMU's own AST match, for renames and moves only. A definition that leaves one place and arrives at another
with the same body (name and docstring aside) is labelled ``refactor, behaviour preserved if tests pass``:

- rename: the same file and owner, a new name;
- move: another file or owner, the same name (or a new one: moved and renamed).

Anything else about the change still counts; the label only says this part of it needs no other reading.
"""
from __future__ import annotations

import ast

LABEL = 'refactor, behaviour preserved if tests pass'


def _definitions(source, path):
    """{(path, qual): (kind, shape)} for functions and classes at module or class level; shape is the AST with the
    definition's own name and docstring left out."""
    try:
        tree = ast.parse(source or '')
    except (SyntaxError, ValueError):
        return {}
    found = {}

    def walk(body, owner):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qual = owner + node.name
                inner = list(node.body)
                if inner and isinstance(inner[0], ast.Expr) and isinstance(getattr(inner[0], 'value', None), ast.Constant) \
                        and isinstance(inner[0].value.value, str):
                    inner = inner[1:]
                kind = 'class' if isinstance(node, ast.ClassDef) else 'function'
                parts = [type(node).__name__, *(ast.dump(d) for d in node.decorator_list), *(ast.dump(s) for s in inner)]
                if kind == 'function':
                    parts.insert(1, ast.dump(node.args))
                    parts.insert(2, ast.dump(node.returns) if node.returns else '')
                else:
                    parts.insert(1, ast.dump(ast.Tuple(elts=node.bases + [k.value for k in node.keywords], ctx=ast.Load())))
                found[(path, qual)] = (kind, '\n'.join(parts))
                if kind == 'class':
                    walk(node.body, qual + '.')
    walk(tree.body, '')
    return found


def label(sources):
    """Rows for ``sources`` {path: (before text or None, after text or None)}: each definition the change renames or
    moves without changing it, as {kind: rename|move, from, to, label}. Only Python files are read."""
    before, after = {}, {}
    for path, (old, new) in sources.items():
        if not path.endswith('.py'):
            continue
        before.update(_definitions(old, path))
        after.update(_definitions(new, path))
    gone = {key: value for key, value in before.items() if key not in after}
    came = {key: value for key, value in after.items() if key not in before}
    rows, taken = [], set()
    for old_key, (kind, shape) in sorted(gone.items()):
        matches = [key for key, value in sorted(came.items()) if value == (kind, shape) and key not in taken]
        if len(matches) != 1:
            continue  # nothing, or more than one place it could have gone: no label
        new_key = matches[0]
        taken.add(new_key)
        (old_path, old_qual), (new_path, new_qual) = old_key, new_key
        owner = lambda qual: qual.rpartition('.')[0]  # noqa: E731
        moved = old_path != new_path or owner(old_qual) != owner(new_qual)
        rows.append(dict(kind='move' if moved else 'rename', what=kind, label=LABEL,
                         **{'from': f'{old_path}#{old_qual}', 'to': f'{new_path}#{new_qual}'}))
    # The methods of a class that moved as a whole moved with it: one row, the class's.
    outer = {row['from'] for row in rows if row['what'] == 'class'}
    return [row for row in rows if not any(row['from'].startswith(o + '.') for o in outer)]
