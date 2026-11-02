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


def _functions(text, with_headers=False):
    """[(first body line, last line, qualified name)] of every function in ``text``; [] when it does not parse."""
    try:
        tree = ast.parse(text or '')
    except (SyntaxError, ValueError):
        return ([], []) if with_headers else []
    found, headers = [], []

    def walk(nodes, prefix):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = prefix + node.name
                if node.body:
                    found.append((node.body[0].lineno, node.end_lineno, qual))
                    headers.append((min([node.lineno, *(d.lineno for d in node.decorator_list)]), node.body[0].lineno - 1, qual))
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
    return (found, headers) if with_headers else found


def code_lines(text):
    """Lines of ``text`` that have bytecode: the only lines a line event can fire on. None when it does not compile."""
    try:
        top = compile(text or '', '<hunk>', 'exec', dont_inherit=True)
    except (SyntaxError, ValueError):
        return None
    found, pending = set(), [top]
    while pending:
        code = pending.pop()
        if code.co_name == '__annotate__':
            continue  # annotations evaluated lazily (3.14): only when someone asks for them, never by running the code
        found.update(line for _, _, line in code.co_lines() if line)
        pending.extend(c for c in code.co_consts if hasattr(c, 'co_lines'))
    return found


def executable(text, lines):
    """``lines`` as a line event can see them: a line without bytecode (an element of a literal spread over lines, a
    name in a parenthesised import) stands for the lines of the innermost statement holding it that have some."""
    have = code_lines(text)
    if have is None:
        return set(lines)
    try:
        statements = [n for n in ast.walk(ast.parse(text or '')) if isinstance(n, ast.stmt)]
    except (SyntaxError, ValueError):
        return set(lines)
    out = set()
    for line in lines:
        if line in have:
            out.add(line)
            continue
        holding = [s for s in statements if s.lineno <= line <= (s.end_lineno or s.lineno)]
        if holding:
            inner = min(holding, key=lambda s: (s.end_lineno or s.lineno) - s.lineno)
            out.update(n for n in range(inner.lineno, (inner.end_lineno or inner.lineno) + 1) if n in have)
        else:
            out.add(line)
    return out


def defaults(text, qual):
    """{parameter: its default expression dumped} of the function ``qual`` (dotted, <locals> for nested) in ``text``."""
    try:
        tree = ast.parse(text or '')
    except (SyntaxError, ValueError):
        return {}
    parts = qual.split('.')

    def find(nodes, rest):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == rest[0]:
                if len(rest) == 1:
                    return node if not isinstance(node, ast.ClassDef) else None
                tail = rest[1:]
                if tail and tail[0] == '<locals>':
                    tail = tail[1:]
                return find(node.body, tail) if tail else None
        return None
    node = find(tree.body, parts)
    if node is None:
        return {}
    args = node.args
    positional = [*args.posonlyargs, *args.args]
    found = {a.arg: d for a, d in zip(positional[len(positional) - len(args.defaults):], args.defaults)}
    found.update({a.arg: d for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is not None})
    return found


def changed_defaults(before, after, qual):
    """{parameter: (default before, after)} where the change alters a default, each as record.literal of a plain value
    or None when it is not one."""
    import json
    old, new = defaults(before, qual), defaults(after, qual)
    out = {}
    for name in set(old) | set(new):
        a, b = old.get(name), new.get(name)
        if a is not None and b is not None and ast.dump(a) == ast.dump(b):
            continue

        def form(node):
            try:
                value = ast.literal_eval(node) if node is not None else None
            except (ValueError, SyntaxError, TypeError):
                return None
            return json.dumps(repr(value)) if isinstance(value, (int, float, str, bytes, bool, type(None))) else None
        out[name] = (form(a), form(b))
    return out


def innermost(functions, lines):
    """The narrowest function whose body holds every line in ``lines``, as (first, last, qual); None if none does."""
    lines = sorted(lines)
    if not lines:
        return None
    holding = [f for f in functions if f[0] <= lines[0] and lines[-1] <= f[1]]
    return min(holding, key=lambda f: f[1] - f[0]) if holding else None


def touched(functions, lines):
    """Qualified names of the innermost functions whose bodies hold any of ``lines``, in order."""
    found = []
    for line in sorted(lines):
        holding = [f for f in functions if f[0] <= line <= f[1]]
        if holding:
            qual = min(holding, key=lambda f: f[1] - f[0])[2]
            if qual not in found:
                found.append(qual)
    return found


CONTEXT = 3  # context lines kept around a block


def blocks(edit):
    """Each run of removed and added lines of a file's diff, between context lines: a diff hunk can hold several (two
    changes a few lines apart share one). Each block keeps its rows, where it starts on each side, and up to CONTEXT
    context lines before and after it on each side."""
    found = []
    for old_start, body, new_start in edit.get('hunks', []):
        old_line, new_line, context, current = old_start, new_start, [], None
        for row in body:
            sign = row[:1]
            if sign in '-+':
                if current is None:
                    current = dict(rows=[], removed=[], added=[], old_start=old_line, new_start=new_line,
                                   old_context=[c[0] for c in context[-CONTEXT:]], new_context=[c[1] for c in context[-CONTEXT:]], after=0)
                    found.append(current)
                current['rows'].append(row)
                if sign == '-':
                    current['removed'].append(old_line)
                    old_line += 1
                else:
                    current['added'].append(new_line)
                    new_line += 1
            else:
                if current is not None:
                    if current['after'] < CONTEXT:
                        current['old_context'].append(old_line)
                        current['new_context'].append(new_line)
                        current['after'] += 1
                    if current['after'] >= CONTEXT:
                        current = None
                context.append((old_line, new_line))
                old_line += 1
                new_line += 1
    for block in found:
        block.pop('after', None)
    return found


def split(path, edit, before, after):
    """The hunks of one file's change, each a dict: path, index, removed and added line numbers, context lines on each
    side, the function holding it on each side, and the lines to watch on each side (``base``, ``head``)."""
    (old_functions, old_headers), (new_functions, new_headers) = _functions(before, True), _functions(after, True)
    found = []
    for index, block in enumerate(blocks(edit)):
        removed, added, old_context, new_context = block['removed'], block['added'], block['old_context'], block['new_context']
        old_start, new_start = block['old_start'], block['new_start']
        # Where the block sits on each side: what it removes or adds, else the line just before the gap.
        old_here = removed or [max(1, old_start - 1)]
        new_here = added or [max(1, new_start - 1)]
        old_function = innermost(old_functions, removed or old_here)
        new_function = innermost(new_functions, added or new_here)
        # A change to a signature (a default, a parameter) or a decorator changes the function too.
        old_touched = touched(old_functions + old_headers, removed or old_here)
        new_touched = touched(new_functions + new_headers, added or new_here)
        if old_function:
            base = set(range(old_function[0], old_function[1] + 1))
        elif removed:
            # A hunk across several definitions: the body of each function it changes, and its lines outside them.
            base = set(removed) | set(old_context)
            for first, last, qual in old_functions:
                if qual in old_touched:
                    base |= set(range(first, last + 1))
        elif new_function is None:
            base = set(old_context)  # module or class level: the lines around the insertion run when it runs
        else:
            base = set()  # inside a function the code before the change does not have
        head = executable(after, set(added) if added else set(new_context))
        base = executable(before, base) if not old_function else base
        found.append(dict(path=path, index=index, rows=block['rows'], removed=removed, added=added, old_start=old_start, new_start=new_start,
                          old_function=old_function[2] if old_function else None,
                          new_function=new_function[2] if new_function else None,
                          old_touched=old_touched, new_touched=new_touched,
                          base=sorted(base), head=sorted(head)))
    return found


def name(hunk):
    """``path:line`` where the hunk starts on the code after the change."""
    line = (hunk['added'] or [hunk['new_start']])[0]
    return f"{hunk['path']}:{line}"
