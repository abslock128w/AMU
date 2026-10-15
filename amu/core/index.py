"""Content-addressed Python facts. Source is parsed, never imported."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import time
from pathlib import Path

from .rank import tokens

VERSION = '8'
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
            # First line of the body: lines before it run when the module is imported, not when a test calls in.
            record['body'] = min(record.get('body', node.body[0].lineno), node.body[0].lineno) if previous is not None and previous['kind'] == kind else node.body[0].lineno
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


RELINK_LIMIT = 25
STATE_KEEP = 2
PARALLEL_FROM = 300
SLIM_DROP = ('refs', 'bindings', 'imports', 'returns', 'args', 'required_args', 'values')


def _sources(root):
    """Every Python file of the tree as an absolute path with its relative name, in a stable order."""
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP and not d.startswith('.') and not Path(directory, d).is_symlink())
        for filename in sorted(files):
            if filename.endswith(('.py', '.pyi')):
                source = os.path.join(directory, filename)
                if not os.path.islink(source):
                    yield os.path.relpath(source, root).replace(os.sep, '/'), source


def _parse_job(job):
    path, source = job
    try:
        with open(source, 'rb') as handle:
            return path, parse(path, handle.read()), None
    except (SyntaxError, UnicodeError, RecursionError, ValueError, OSError) as exc:
        return path, None, type(exc).__name__


def _fact_file(cache, path, digest):
    return cache/(hashlib.sha256((path+'\0'+digest).encode()).hexdigest()+'.json')


def _facts(cache, sources, gaps):
    facts, missing = {}, []
    for path, source, digest in sources:
        dest = _fact_file(cache, path, digest)
        try:
            fact = json.loads(dest.read_text())
            if fact.get('path') != path:
                raise ValueError('cache identity mismatch')
            facts[path] = fact
        except (OSError, ValueError):
            missing.append((path, source, dest))
    jobs = [(path, source) for path, source, _ in missing]
    results = None
    if len(jobs) >= PARALLEL_FROM and (os.cpu_count() or 1) > 1:
        # A cold index of a large tree is parsing-bound; parsing is independent per file.
        try:
            from concurrent.futures import ProcessPoolExecutor
            with ProcessPoolExecutor(max_workers=min(8, os.cpu_count() or 1)) as pool:
                results = list(pool.map(_parse_job, jobs, chunksize=64))
        except (OSError, RuntimeError, ImportError):
            results = None
    if results is None:
        results = [_parse_job(job) for job in jobs]
    writable = True
    for (path, _, dest), (_, fact, problem) in zip(missing, results):
        if fact is None:
            gaps.append(f'{path}: {problem}')
            continue
        facts[path] = fact
        if writable:
            try:
                cache.mkdir(parents=True, exist_ok=True)
                temporary = dest.with_suffix('.'+str(os.getpid())+'.tmp')
                temporary.write_text(json.dumps(fact, separators=(',', ':')))
                temporary.replace(dest)
            except OSError:
                writable = False
                gaps.append('Index cache unavailable; analysis remains in memory')
    return {path: facts[path] for path, _, _ in sources if path in facts}, len(missing)


def cache_dir(root, index_dir=None):
    return Path(index_dir or os.environ.get('AMU_INDEX_DIR') or Path(root).resolve()/'.amu/index')/VERSION


def _scan(root, index_dir):
    """Hash every Python file. A file whose size and modification time match the last scan is not read again."""
    root = Path(root).resolve()
    cache = cache_dir(root, index_dir)
    ledger = cache/('stat-' + hashlib.sha256(str(root).encode()).hexdigest()[:16] + '.json')
    try:
        known = json.loads(ledger.read_text())
    except (OSError, ValueError):
        known = {}
    sources, hashes, gaps, seen, dirty = [], {}, [], {}, False
    # As git does: a file modified within two seconds of the last scan may have changed again unseen.
    settled = known.pop('__scanned__', 0) - 2_000_000_000
    for path, source in _sources(root):
        try:
            status = os.stat(source)
            stamp = [status.st_mtime_ns, status.st_size]
            if known.get(path, [None])[:2] == stamp and status.st_mtime_ns < settled:
                digest = known[path][2]
            else:
                with open(source, 'rb') as handle:
                    digest = hashlib.sha256(handle.read()).hexdigest()
                dirty = True
        except OSError:
            gaps.append(f'{path}: OSError')
            continue
        seen[path] = [*stamp, digest]
        hashes[path] = digest
        sources.append((path, source, digest))
    if dirty or len(seen) != len(known):
        try:
            cache.mkdir(parents=True, exist_ok=True)
            temporary = ledger.with_suffix('.' + str(os.getpid()) + '.tmp')
            temporary.write_text(json.dumps(dict(seen, __scanned__=time.time_ns()), separators=(',', ':')))
            temporary.replace(ledger)
        except OSError:
            pass
    return cache, sources, hashes, gaps


def build(root, index_dir=None):
    """Per-file facts of the tree: (facts, hashes, gaps, files parsed this call, cache directory)."""
    cache, sources, hashes, gaps = _scan(root, index_dir)
    facts, parsed = _facts(cache, sources, gaps)
    return facts, hashes, sorted(set(gaps)), parsed, cache


def state(root, index_dir=None):
    """Facts and reference graph of the tree: (facts, hashes, gaps, files parsed, graph).

    An unchanged tree is answered from one snapshot file holding the graph and only the record
    fields analysis reads afterwards; the per-file facts are needed again only to rebuild a graph.
    """
    from .graph import Graph
    cache, sources, hashes, gaps = _scan(root, index_dir)
    key = hashlib.sha256(json.dumps(sorted(hashes.items())).encode()).hexdigest()
    dest = cache/('state-'+key+'.json')
    try:
        saved = json.loads(dest.read_text())
        return saved['facts'], hashes, sorted(set(gaps) | set(saved['gaps'])), 0, Graph(saved['facts'], saved['graph'])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    built, facts, parsed = None, None, 0
    for earlier in sorted(cache.glob('state-*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:1]:
        # An edited tree: reuse the latest snapshot's graph and facts, and relink only the files that differ.
        try:
            saved = json.loads(earlier.read_text())
            differs = {path for path in set(hashes) | set(saved['hashes']) if hashes.get(path) != saved['hashes'].get(path)}
            if len(differs) <= RELINK_LIMIT:
                fresh, parsed = _facts(cache, [row for row in sources if row[0] in differs], gaps)
                facts = {path: fresh.get(path) or saved['facts'].get(path) for path, _, _ in sources}
                facts = {path: fact for path, fact in facts.items() if fact}

                def complete(path):
                    # Untouched files stay in their slim form until the changed code refers into them.
                    try:
                        return json.loads(_fact_file(cache, path, hashes[path]).read_text())
                    except (OSError, ValueError, KeyError):
                        return None
                built = Graph(facts, saved['graph'], relink=differs, loader=complete)
                gaps.append(f'graph reused from the previous state; {len(differs)} changed file(s) relinked')
        except (OSError, ValueError, KeyError, TypeError):
            built = None
    if built is None:
        facts, parsed = _facts(cache, sources, gaps)
        built = Graph(facts)
    slim = {path: dict(fact, records={ident: {k: v for k, v in record.items() if k not in SLIM_DROP}
                                      for ident, record in fact['records'].items()}) for path, fact in facts.items()}
    try:
        cache.mkdir(parents=True, exist_ok=True)
        for old in sorted(cache.glob('state-*.json'), key=lambda p: p.stat().st_mtime)[:-STATE_KEEP]:
            old.unlink(missing_ok=True)
        temporary = dest.with_suffix('.'+str(os.getpid())+'.tmp')
        temporary.write_text(json.dumps(dict(facts=slim, hashes=hashes, gaps=sorted(g for g in set(gaps) if 'relinked' not in g),
                                             graph=built.snapshot()), separators=(',', ':')))
        temporary.replace(dest)
    except OSError:
        pass
    return facts, hashes, sorted(set(gaps)), parsed, built


def graph(facts, hashes, cache):
    """Build the reference graph, reusing the snapshot of an identical tree state."""
    from .graph import Graph
    key = hashlib.sha256(json.dumps(sorted(hashes.items())).encode()).hexdigest()
    dest = cache/('graph2-'+key+'.json')
    try:
        return Graph(facts, json.loads(dest.read_text()))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    built = Graph(facts)
    try:
        cache.mkdir(parents=True, exist_ok=True)
        stale = sorted(cache.glob('graph*.json'), key=lambda p: p.stat().st_mtime)[:-2]
        for old in stale:
            old.unlink(missing_ok=True)
        temporary = dest.with_suffix('.'+str(os.getpid())+'.tmp')
        temporary.write_text(json.dumps(built.snapshot(), separators=(',', ':')))
        temporary.replace(dest)
    except OSError:
        pass
    return built
