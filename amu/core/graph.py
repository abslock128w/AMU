"""Best-effort lexical bindings and bounded reachability over shared source facts."""
from collections import defaultdict, deque

AMBIGUITY_LIMIT = 8


class Graph:
    def __init__(self, facts, snapshot=None):
        self.records = {k: r for f in facts.values() for k, r in f['records'].items()}
        self.modules = {f['module']: f['path'] for f in facts.values()}
        self.members = defaultdict(list)
        self.methods = defaultdict(list)
        self.fixtures = defaultdict(list)
        self.edges = defaultdict(dict)
        self.reverse = defaultdict(dict)
        self.unresolved = set()
        self.ambiguous = 0
        self.below = {}
        self.memo = {}
        for ident, r in self.records.items():
            if r['qual']:
                self.members[(r['owner'], r['qual'].rsplit('.', 1)[-1])].append(ident)
                if self.records.get(r['owner'], {}).get('kind') == 'class':
                    self.methods[r['qual'].rsplit('.', 1)[-1]].append(ident)
            if r.get('fixture'):
                self.fixtures[r.get('fixture_name', r['qual'].rsplit('.', 1)[-1])].append(ident)
        self.subclasses = defaultdict(set)
        self.state = defaultdict(list)
        if snapshot is not None:
            # Edges of an unchanged tree are reused; only the reverse map is rebuilt.
            self.unresolved_count, self.ambiguous = snapshot['unresolved'], snapshot['ambiguous']
            for caller, targets in snapshot['edges'].items():
                for target, (line, specificity, approximate) in targets.items():
                    row = dict(line=line, specificity=specificity, approximate=bool(approximate))
                    self.edges[caller][target] = row
                    self.reverse[target][caller] = row
            return
        for ident, r in self.records.items():
            if r['kind'] == 'class':
                for base in r['bases']:
                    for parent in self.resolve(base, r['owner']):
                        if self.records.get(parent, {}).get('kind') == 'class':
                            self.subclasses[parent].add(ident)
            elif r['kind'] == 'function' and self.records.get(r['owner'], {}).get('kind') == 'class':
                # Instance state may be assigned by any method, not only the constructor.
                for key, values in r['bindings'].items():
                    if key.startswith('self.') and key.count('.') == 1:
                        self.state[(r['owner'], key[5:])].extend((ident, value) for value in values)
        self.memo.clear()
        autouse = {name for name, found in self.fixtures.items() if any(self.records[f].get('autouse') for f in found)}
        for ident, r in self.records.items():
            if r['kind'] not in ('function', 'variable'):
                continue
            scope = ident if r['kind'] == 'function' else r['owner']
            for expr, kind, line in r['refs']:
                targets = self.resolve(expr, scope)
                approximate = False
                if not targets and expr and expr[0] == 'attr':
                    # Unknown receivers keep same-named source methods as candidates, unless the
                    # name is so common that the guess carries no information.
                    candidates = self.methods.get(expr[2], [])
                    if len(candidates) <= AMBIGUITY_LIMIT:
                        targets = set(candidates)
                        approximate = bool(targets)
                    elif kind == 'call':
                        self.ambiguous += 1
                if not targets and kind == 'call':
                    self.unresolved.add((ident, line))
                for target in sorted(targets):
                    if target not in self.records or target == ident:
                        continue
                    dest = self.records[target]
                    specificity = 0 if kind == 'call' and dest['kind'] == 'function' else 1 if dest['kind'] in ('class', 'variable', 'function') else 2
                    self.add(ident, target, line, specificity, approximate)
                    if kind == 'call' and dest['kind'] == 'class':
                        for constructor in self.attribute({target}, '__init__', ident, set(), 0):
                            self.add(ident, constructor, line, 0, approximate)
                    if dest['kind'] == 'function' and self.records[dest['owner']]['kind'] == 'class':
                        # A call bound to a base method may dispatch to an override.
                        name = dest['qual'].rsplit('.', 1)[-1]
                        for sub in self.descendants(dest['owner']):
                            for override in self.members.get((sub, name), []):
                                if override != ident:
                                    self.add(ident, override, line, 0, True)
            if self.is_test(r) or r.get('fixture'):
                for name in {*r.get('required_args', []), *autouse}:
                    candidates = [f for f in self.fixtures.get(name, ()) if self.visible_fixture(r, self.records[f])]
                    # The nearest conftest wins, as in pytest.
                    candidates.sort(key=lambda f: len(self.records[f]['path']), reverse=True)
                    if candidates and (name in r.get('required_args', []) or self.records[candidates[0]].get('autouse')):
                        self.add(ident, candidates[0], r['line'], 0, False)
        for caller, targets in self.edges.items():
            for target, edge in targets.items():
                self.reverse[target][caller] = edge
        self.unresolved_count = len(self.unresolved)

    def snapshot(self):
        return dict(unresolved=self.unresolved_count, ambiguous=self.ambiguous,
                    edges={caller: {target: [e['line'], e['specificity'], int(e['approximate'])] for target, e in targets.items()}
                           for caller, targets in self.edges.items()})

    def descendants(self, cls):
        if cls not in self.subclasses:
            return ()
        if cls in self.below:
            return self.below[cls]
        seen, pending = set(), [cls]
        while pending:
            for sub in self.subclasses.get(pending.pop(), ()):
                if sub not in seen:
                    seen.add(sub)
                    pending.append(sub)
        self.below[cls] = seen
        return seen

    @staticmethod
    def is_test(r):
        name = r['qual'].rsplit('.', 1)[-1]
        path = r['path'].split('/')
        return r['kind'] == 'function' and name.startswith('test_') and ('tests' in path or path[-1].startswith('test_') or path[-1].endswith('_test.py'))

    @staticmethod
    def visible_fixture(test, fixture):
        path = fixture['path']
        return path == test['path'] or path.endswith('conftest.py') and test['path'].startswith(path.rsplit('/', 1)[0]+'/')

    def add(self, caller, target, line, specificity, approximate):
        row = dict(line=line, specificity=specificity, approximate=approximate)
        old = self.edges[caller].get(target)
        if old is None or (approximate, specificity, line) < (old['approximate'], old['specificity'], old['line']):
            self.edges[caller][target] = row

    def imported(self, name, seen, depth):
        if depth > 12 or name in seen:
            return set()
        seen = seen | {name}
        if name in self.modules:
            return {self.modules[name]}
        prefix, _, tail = name.rpartition('.')
        if prefix in self.modules:
            module = self.modules[prefix]
            r = self.records[module]
            result = set(self.members.get((module, tail), []))
            if tail in r['imports']:
                result |= self.imported(r['imports'][tail], seen, depth+1)
            for expr in r['bindings'].get(tail, []):
                result |= self.resolve(expr, module, seen, depth+1)
            if '*' in r['imports']:
                result |= self.imported(r['imports']['*'][:-1]+tail, seen, depth+1)
            return result
        if prefix:
            return self.attribute(self.imported(prefix, seen, depth+1), tail, '', seen, depth+1)
        return set()

    def attribute(self, bases, name, scope, seen, depth):
        result = set()
        if depth > 12:
            return result
        for base in bases:
            r = self.records.get(base)
            if not r:
                continue
            result.update(self.members.get((base, name), []))
            if r['kind'] == 'module':
                result |= self.imported(r['module']+'.'+name, seen, depth+1)
            for expr in r['bindings'].get(name, []):
                result |= self.resolve(expr, base, seen, depth+1)
            if r['kind'] == 'class':
                for parent in r['bases']:
                    result |= self.attribute(self.resolve(parent, base, seen, depth+1), name, scope, seen, depth+1)
                for method, expr in self.state.get((base, name), ()):
                    result |= self.resolve(expr, method, seen, depth+1)
        return result

    def resolve(self, expr, scope, seen=None, depth=0):
        if not expr or depth > 12:
            return set()
        top = seen is None
        seen = seen or set()
        key = (scope, repr(expr))
        if top and key in self.memo:
            return self.memo[key]
        if key in seen:
            return set()
        seen = seen | {key}
        kind = expr[0]
        result = set()
        r = self.records.get(scope, {})
        if kind == 'symbol':
            result.add(expr[1])
        elif kind == 'var':
            result.add(expr[1])
            var = self.records.get(expr[1], {})
            for value in var.get('values', []):
                result |= self.resolve(value, var.get('owner'), seen, depth+1)
        elif kind == 'name':
            name = expr[1]
            if name in {'self', 'cls'} and self.records.get(r.get('owner'), {}).get('kind') == 'class':
                result.add(r['owner'])
            for value in r.get('bindings', {}).get(name, []):
                result |= self.resolve(value, scope, seen, depth+1)
            if name in r.get('imports', {}):
                result |= self.imported(r['imports'][name], seen, depth+1)
            if not result and r.get('owner'):
                result |= self.resolve(expr, r['owner'], seen, depth+1)
            if not result and '*' in r.get('imports', {}):
                result |= self.imported(r['imports']['*'][:-1]+name, seen, depth+1)
            if not result:
                for fixture in self.fixtures.get(name, []):
                    if self.visible_fixture(r, self.records[fixture]):
                        for value in self.records[fixture]['returns']:
                            result |= self.resolve(value, fixture, seen, depth+1)
        elif kind == 'attr':
            result |= self.attribute(self.resolve(expr[1], scope, seen, depth+1), expr[2], scope, seen, depth+1)
        elif kind == 'call':
            for target in self.resolve(expr[1], scope, seen, depth+1):
                dest = self.records.get(target, {})
                if dest.get('kind') == 'class':
                    result.add(target)
                else:
                    for value in dest.get('returns', []):
                        result |= self.resolve(value, target, seen, depth+1)
        elif kind == 'item':
            result |= self.resolve(expr[1], scope, seen, depth+1)
        elif kind == 'union':
            for child in expr[1:]:
                result |= self.resolve(child, scope, seen, depth+1)
        if top:
            self.memo[key] = result
        return result

    def reach(self, changed, max_depth=5):
        """Best (rank, path, evidence) to each dependant of any identity in ``changed``.

        Depth counts calls and references. Stepping from a nested definition to the function
        that lexically contains it costs nothing: the outer function is what a caller invokes.
        """
        found, visited = {}, {}
        queue = deque((ident, [ident], [], 0, 0) for ident in changed if ident in self.records)
        while queue:
            target, path, evidence, specificity, depth = queue.popleft()
            rank = (depth, specificity, any(e['approximate'] for e in evidence))
            if target in visited and visited[target] <= rank:
                continue
            visited[target] = rank
            if len(path) > 1:
                found[target] = (rank, path, evidence)
            for caller, edge in self.reverse.get(target, {}).items():
                if caller in path:
                    continue
                contained = self.records[target].get('owner') == caller and self.records[caller]['kind'] == 'function'
                step = (caller, [caller, *path], [edge, *evidence], max(specificity, 0 if contained else edge['specificity']),
                        depth + (0 if contained else 1))
                if contained:
                    queue.appendleft(step)
                elif depth < max_depth:
                    queue.append(step)
        return found
