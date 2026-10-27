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


class _Renamed(ast.NodeTransformer):
    """The before side with each renamed or moved name spelled as the change spells it."""

    def __init__(self, names, modules):
        self.names, self.modules = names, modules

    def _name(self, value):
        return self.names.get(value, value)

    def visit_Name(self, node):
        node.id = self._name(node.id)
        return node

    def visit_Attribute(self, node):
        self.generic_visit(node)
        node.attr = self._name(node.attr)
        return node

    def visit_FunctionDef(self, node):
        self.generic_visit(node)
        node.name = self._name(node.name)
        return node

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self.generic_visit(node)
        node.name = self._name(node.name)
        return node

    def visit_alias(self, node):
        node.name = '.'.join(self._name(part) for part in node.name.split('.'))
        if node.asname:
            node.asname = self._name(node.asname)
        return node

    def visit_ImportFrom(self, node):
        self.generic_visit(node)
        moved = [self.modules.get((node.module, alias.name)) for alias in node.names]
        if node.module and moved and all(moved) and len(set(moved)) == 1:
            node.module = moved[0]
        return node



def _module(path):
    module = path[:-3].replace('/', '.')
    return module[:-len('.__init__')] if module.endswith('.__init__') else module


def _without(tree, quals):
    """``tree`` with the definitions named in ``quals`` (Class.method or function) left out."""
    def strip(body, owner):
        kept = []
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and owner + node.name in quals:
                continue
            if isinstance(node, ast.ClassDef):
                node.body = strip(node.body, owner + node.name + '.') or [ast.Pass()]
            kept.append(node)
        return kept
    tree.body = strip(tree.body, '')
    return tree


def only(sources):
    """The rename and move rows when every change in ``sources`` {path: (before text or None, after text or None)} is
    one of them, read as Python: the before side, with each renamed or moved name spelled the new way, has the same
    syntax tree as the after side once the moved definitions are set aside. Otherwise None. A change to any other
    file, a comment-only edit aside, is not a rename."""
    if not sources or any(not path.endswith('.py') for path in sources):
        return None
    rows = label(sources)
    if not rows:
        return None
    names, modules, gone, came = {}, {}, {}, {}
    for row in rows:
        old_path, old_qual = row['from'].split('#', 1)
        new_path, new_qual = row['to'].split('#', 1)
        old_name, new_name = old_qual.rsplit('.', 1)[-1], new_qual.rsplit('.', 1)[-1]
        if old_name != new_name:
            if names.get(old_name, new_name) != new_name:
                return None  # one name renamed two ways: not read as a plain rename
            names[old_name] = new_name
        if row['kind'] == 'move':
            gone.setdefault(old_path, set()).add(old_qual)
            came.setdefault(new_path, set()).add(new_qual)
            if old_path != new_path:
                modules[(_module(old_path), new_name)] = _module(new_path)
    for path, (old, new) in sources.items():
        try:
            before = ast.parse(old or '')
            after = ast.parse(new or '')
        except (SyntaxError, ValueError):
            return None
        if any(isinstance(node, ast.Constant) and node.value in names for node in ast.walk(before)):
            return None  # the old name in a string may be looked up by it (getattr, a registry): not only a rename
        before = _Renamed(names, modules).visit(_without(before, gone.get(path, set())))
        after = _without(after, came.get(path, set()))
        if ast.dump(before, include_attributes=False) != ast.dump(after, include_attributes=False):
            return None
    return rows


def says(text, names, modules=None):
    """Whether the source ``text`` of a file the change leaves alone uses one of ``names`` (old names a change renames):
    imports it, reads it as an attribute, or uses it bare without defining it itself. A string holding the old name
    counts only in a file that imports one of ``modules`` (where the renamed definitions were), or names one in a
    string (importlib): only there can getattr or a registry look it up on that module. With ``modules`` None, every
    such string counts. A file that does not parse is taken to use it."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return True
    own = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            own.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            own.add(node.id)
        elif isinstance(node, ast.arg):
            own.add(node.arg)
    strings = modules is None or _imports(tree, set(modules))
    for node in ast.walk(tree):
        if isinstance(node, ast.alias) and node.name.split('.')[-1] in names:
            return True
        if isinstance(node, ast.Attribute) and node.attr in names:
            return True
        if strings and isinstance(node, ast.Constant) and node.value in names:
            return True
        if isinstance(node, ast.Name) and node.id in names and node.id not in own:
            return True
    return False


def _imports(tree, modules):
    """Whether ``tree`` imports one of ``modules`` (dotted): ``import m`` or a package above it, ``from m import x``,
    ``from package import m``, or ``m`` as a string (importlib.import_module)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(m == alias.name or m.startswith(alias.name + '.') for alias in node.names for m in modules):
                return True
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            if node.module in modules or any(f'{node.module}.{alias.name}' in modules for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            # A relative import: its target is not resolved here, so it may be the module.
            if any(alias.name == m.rsplit('.', 1)[-1] for alias in node.names for m in modules) or \
                    (node.module and any(m.endswith('.' + node.module) or m == node.module for m in modules)):
                return True
        elif isinstance(node, ast.Constant) and node.value in modules:
            return True
    return False


def adds_only(sources):
    """Whether the change only adds code: in every Python file outside the tests, the code before is the code after once
    the definitions it adds are set aside (new functions, classes and methods; a new file counts whole). Test files may
    change freely; other files are not read. False when no Python file outside the tests changed."""
    from .replay import is_test_file

    seen = False
    for path, (old, new) in sources.items():
        if not path.endswith('.py') or is_test_file(path):
            continue
        seen = True
        if not old:
            continue
        try:
            before, after = ast.parse(old), ast.parse(new or '')
        except (SyntaxError, ValueError):
            return False
        kept = set(_definitions(old, path))
        added = {qual for (_, qual) in _definitions(new, path) if (path, qual) not in kept}
        if ast.dump(before, include_attributes=False) != ast.dump(_without(after, added), include_attributes=False):
            return False
    return seen
