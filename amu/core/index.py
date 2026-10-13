"""Content-addressed Python facts. Source is parsed, never imported."""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path

from .rank import tokens

VERSION = '6'
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
SKIP = {'.git', '.amu', '.entire', '.venv', 'venv', 'node_modules', '__pycache__', 'build', 'dist', 'site-packages'}


def expression(node):
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return ['name', node.id]
    if isinstance(node, ast.Attribute):
        return ['attr', expression(node.value), node.attr]
    if isinstance(node, ast.Call):
        return ['call', expression(node.func)]
    if isinstance(node, ast.Subscript):
        return ['item', expression(node.value)]
    if isinstance(node, ast.Constant):
        return ['literal', type(node.value).__name__]
    return ['union', *[expression(n) for n in ast.iter_child_nodes(node) if isinstance(n, ast.expr)]]


def terminal(node):
    """Last identifier of a raised or caught exception expression."""
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def module_name(path):
    parts = Path(path).with_suffix('').parts
    if parts[0] == 'src':
        parts = parts[1:]
    if parts[-1] == '__init__':
        parts = parts[:-1]
    return '.'.join(parts)


def parse(path, data):
    tree = ast.parse(data, filename=path)
    module = module_name(path)
    package = module if Path(path).stem == '__init__' else module.rpartition('.')[0]
    records = {}

    def scope(node, qual, owner=None):
        ident = path + ('#'+qual if qual else '')
        kind = 'class' if isinstance(node, ast.ClassDef) else 'function' if isinstance(node, FUNCTIONS) else 'module'
        # A declaration starts at its first decorator.
        start = min([getattr(node, 'lineno', 1), *[d.lineno for d in getattr(node, 'decorator_list', [])]])
        end = getattr(node, 'end_lineno', None) or len(data.splitlines()) + 1
        previous = records.get(ident)
        if previous is not None and previous['kind'] == kind:
            # Property setters, overloads and conditional definitions share one identity.
            record = previous
            record['spans'].append([start, end])
        else:
            record = dict(id=ident, path=path, module=module, qual=qual, kind=kind, owner=owner, line=start, end=end,
                          spans=[[start, end]], bindings={}, imports={}, refs=[], returns=[], bases=[], args=[],
                          fixture=False, autouse=False, raises=[], handlers=[], return_lines=[], writes=[], unused=[])
            records[ident] = record
        if isinstance(node, FUNCTIONS):
            record['args'] = [a.arg for a in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]]
            record['required_args'] = [a.arg for a in node.args.args[:len(node.args.args)-len(node.args.defaults)]]
            record['required_args'] += [a.arg for a, default in zip(node.args.kwonlyargs, node.args.kw_defaults) if default is None]
            for d in node.decorator_list:
                name = ast.unparse(d.func if isinstance(d, ast.Call) else d)
                if name.rsplit('.', 1)[-1] == 'fixture':
                    record['fixture'] = True
                    for keyword in d.keywords if isinstance(d, ast.Call) else []:
                        if keyword.arg == 'autouse' and isinstance(keyword.value, ast.Constant):
                            record['autouse'] = bool(keyword.value.value)
                        if keyword.arg == 'name' and isinstance(keyword.value, ast.Constant):
                            record['fixture_name'] = keyword.value.value
            if node.name.startswith('test'):
                # Vocabulary of a test: what it names and the literals it feeds in.
                text = ' '.join(n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else n.value
                                for n in ast.walk(node)
                                if isinstance(n, (ast.Name, ast.Attribute)) or isinstance(n, ast.Constant) and isinstance(n.value, str))
                record['words'] = sorted(tokens(text[:20000]))[:400]
                # Raw identifiers and string literals: what the test names and what it asserts on.
                record['names'] = sorted({n.id if isinstance(n, ast.Name) else n.attr for n in ast.walk(node)
                                          if isinstance(n, (ast.Name, ast.Attribute))})[:600]
                record['literals'] = sorted({' '.join(n.value.split())[:200] for n in ast.walk(node)
                                             if isinstance(n, ast.Constant) and isinstance(n.value, str) and len(n.value) >= 8})[:200]
        if isinstance(node, ast.ClassDef):
            record['bases'] = [expression(b) for b in node.bases]

        def bind(target, value):
            if isinstance(target, (ast.Tuple, ast.List)):
                for item in target.elts:
                    bind(item, value)
            else:
                key = ast.unparse(target)
                record['bindings'].setdefault(key, []).append(expression(value))

        def variable(name, statement, value):
            varid = path + '#' + (qual + '.' if qual else '') + name
            if varid in records and records[varid]['kind'] != 'variable':
                bind(ast.Name(id=name), value)
                return
            first = varid not in records
            var = records.setdefault(varid, dict(
                id=varid, path=path, module=module, qual=(qual + '.' if qual else '') + name, kind='variable',
                owner=ident, line=statement.lineno, end=statement.end_lineno,
                spans=[[statement.lineno, statement.end_lineno]], bindings={}, imports={}, refs=[], returns=[],
                bases=[], args=[], fixture=False, autouse=False, values=[], raises=[], handlers=[],
                return_lines=[], writes=[], unused=[]))
            var['values'].append(expression(value))
            for sub in ast.walk(value) if value is not None else []:
                if isinstance(sub, ast.Call):
                    var['refs'].append([expression(sub.func), 'call', sub.lineno])
                elif isinstance(sub, (ast.Name, ast.Attribute)) and isinstance(sub.ctx, ast.Load):
                    var['refs'].append([expression(sub), 'reference', sub.lineno])
            if first:
                record['bindings'].setdefault(name, []).append(['var', varid])

        def visit(n):
            if isinstance(n, (*FUNCTIONS, ast.ClassDef)):
                childqual = (qual+'.' if qual else '')+n.name
                record['bindings'].setdefault(n.name, []).append(['symbol', path+'#'+childqual])
                scope(n, childqual, ident)
                return
            if isinstance(n, ast.Import):
                for a in n.names:
                    record['imports'][a.asname or a.name.split('.')[0]] = a.name if a.asname else a.name.split('.')[0]
            elif isinstance(n, ast.ImportFrom):
                if n.level:
                    base = package.split('.')
                    base = base[:len(base)-n.level+1]
                    prefix = '.'.join([*base, *([n.module] if n.module else [])])
                else:
                    prefix = n.module or ''
                for a in n.names:
                    record['imports'][a.asname or a.name] = (prefix+'.'+a.name).strip('.')
            if isinstance(n, (ast.Assign, ast.AnnAssign)):
                targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                value = n.value if n.value is not None else getattr(n, 'annotation', None)
                names = [t.id for t in targets if isinstance(t, ast.Name)]
                if kind != 'function' and names and len(names) == len(targets) and value is not None:
                    # Module and class state is a symbol of its own: a registry or constant
                    # can change, and can be the only link between a consumer and a class.
                    for name in names:
                        variable(name, n, value)
                    return
                for target in targets:
                    bind(target, value)
            elif isinstance(n, (ast.Return, ast.Yield, ast.YieldFrom)):
                record['returns'].append(expression(n.value))
                record['return_lines'].append(n.lineno)
            elif isinstance(n, ast.Raise):
                record['raises'].append([terminal(n.exc), n.lineno])
            elif isinstance(n, ast.Try) and n.body:
                for handler in n.handlers:
                    caught = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type] if handler.type else []
                    record['handlers'].append([n.body[0].lineno, n.body[-1].end_lineno,
                                               [terminal(c) for c in caught] or ['*'], handler.lineno, handler.end_lineno])
            elif isinstance(n, ast.Expr) and isinstance(n.value, ast.Call):
                record['unused'].append(n.lineno)
            if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                for target in n.targets if isinstance(n, ast.Assign) else [n.target]:
                    for item in target.elts if isinstance(target, (ast.Tuple, ast.List)) else [target]:
                        if isinstance(item, ast.Attribute) and isinstance(item.value, ast.Name) and item.value.id == 'self':
                            record['writes'].append([item.attr, n.lineno])
            if isinstance(n, ast.Call):
                record['refs'].append([expression(n.func), 'call', n.lineno])
                if terminal(n.func) == 'warn':
                    category = next((k.value for k in n.keywords if k.arg == 'category'), n.args[1] if len(n.args) > 1 else None)
                    record['raises'].append(['warn:' + (terminal(category) or 'UserWarning'), n.lineno])
            elif isinstance(n, (ast.Name, ast.Attribute)) and isinstance(n.ctx, ast.Load):
                record['refs'].append([expression(n), 'reference', n.lineno])
            for child in ast.iter_child_nodes(n):
                visit(child)

        # Decorators are processed after the visitor exists.
        for statement in node.body:
            visit(statement)
        if isinstance(node, FUNCTIONS):
            for d in node.decorator_list:
                visit(d)
        return ident

    # Scope visitor needs to exist before decorator traversal.
    scope(tree, '')
    return dict(path=path, module=module, records=records)


def parse_or_none(path, data):
    try:
        return parse(path, data)
    except (SyntaxError, UnicodeError, RecursionError, ValueError):
        return None


def build(root, index_dir=None):
    root = Path(root).resolve()
    cache = Path(index_dir or os.environ.get('AMU_INDEX_DIR') or root/'.amu/index')/VERSION
    facts, hashes, gaps = {}, {}, []
    parsed = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP and not d.startswith('.') and not Path(directory, d).is_symlink())
        for filename in sorted(files):
            source = Path(directory, filename)
            if source.suffix not in {'.py', '.pyi'} or source.is_symlink():
                continue
            path = source.relative_to(root).as_posix()
            try:
                data = source.read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                hashes[path] = digest
                key = hashlib.sha256((path+'\0'+digest).encode()).hexdigest()
                dest = cache/(key+'.json')
                try:
                    fact = json.loads(dest.read_text())
                    if fact.get('path') != path:
                        raise ValueError('cache identity mismatch')
                except (OSError, ValueError):
                    fact = parse(path, data)
                    parsed += 1
                    try:
                        cache.mkdir(parents=True, exist_ok=True)
                        temporary = dest.with_suffix('.'+str(os.getpid())+'.tmp')
                        temporary.write_text(json.dumps(fact, separators=(',', ':')))
                        temporary.replace(dest)
                    except OSError:
                        gaps.append('Index cache unavailable; analysis remains in memory')
                facts[path] = fact
            except (SyntaxError, UnicodeError, OSError, RecursionError) as exc:
                gaps.append(f'{path}: {type(exc).__name__}')
    return facts, hashes, sorted(set(gaps)), parsed, cache


def graph(facts, hashes, cache):
    """Build the reference graph, reusing the snapshot of an identical tree state."""
    from .graph import Graph
    key = hashlib.sha256(json.dumps(sorted(hashes.items())).encode()).hexdigest()
    dest = cache/('graph-'+key+'.json')
    try:
        return Graph(facts, json.loads(dest.read_text()))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    built = Graph(facts)
    try:
        cache.mkdir(parents=True, exist_ok=True)
        stale = sorted(cache.glob('graph-*.json'), key=lambda p: p.stat().st_mtime)[:-2]
        for old in stale:
            old.unlink(missing_ok=True)
        temporary = dest.with_suffix('.'+str(os.getpid())+'.tmp')
        temporary.write_text(json.dumps(built.snapshot(), separators=(',', ':')))
        temporary.replace(dest)
    except OSError:
        pass
    return built
