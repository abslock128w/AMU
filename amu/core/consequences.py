"""Located downstream consequences of a change: exception chains, shared state, used results.

A row is emitted only when both ends have a source location. Anything else is a gap.
"""
from __future__ import annotations

from .graph import APPROXIMATE, LINE, SPECIFICITY

CATCH_ALL = {'*', 'Exception', 'BaseException'}
MAX_HOPS = 4


def inside(record, lines):
    return any(start <= line <= end for start, end in record.get('spans', []) for line in lines)


class Finder:
    def __init__(self, graph, after, new_lines, renumbered):
        self.graph = graph
        self.after = after            # identity -> record as of the candidate source
        self.new_lines = new_lines    # path -> candidate line numbers the patch wrote
        self.renumbered = renumbered  # paths whose parent line numbers differ from the candidate
        self.parents = {}
        for record in graph.records.values():
            if record['kind'] == 'class':
                for base in record['bases']:
                    name = base[-1] if base and base[0] in ('name', 'attr') else None
                    if name:
                        self.parents.setdefault(record['qual'].rsplit('.', 1)[-1], set()).add(name)

    def record(self, ident):
        return self.after.get(ident) or self.graph.records.get(ident)

    def ancestors(self, name):
        seen, pending = set(), [name]
        while pending:
            for parent in self.parents.get(pending.pop(), ()):
                if parent not in seen:
                    seen.add(parent)
                    pending.append(parent)
        return seen

    def handler(self, caller, call_line, exception):
        accepted = {exception, *self.ancestors(exception)}
        for start, end, caught, line, last in caller.get('handlers', []):
            if not (set(caught) & (accepted | CATCH_ALL)):
                continue
            # Parent call lines cannot be compared with a candidate file's numbering.
            if caller['path'] in self.renumbered or start <= call_line <= end:
                raised = [name for name, at in caller.get('raises', []) if line <= at <= last and not (name or '').startswith('warn:')]
                return line, last, raised
        return None

    def exceptions(self):
        rows = []
        for ident, record in self.after.items():
            if record['kind'] != 'function':
                continue
            lines = self.new_lines.get(record['path'], set())
            for name, line in record.get('raises', []):
                if name and not name.startswith('warn:') and line in lines:
                    found = self.chain(ident, name, 0, {ident})
                    if found:
                        steps, end, guessed = found
                        rows.append(dict(
                            **{'from': f"{record['qual']} raises {name}"}, to=end,
                            path=[f"{record['path']}:{line} raise {name}", *steps],
                            evidence='static; a receiver was guessed' if guessed else 'static source path'))
        # A chain that is the tail of a longer one adds nothing.
        return [row for row in rows
                if not any(other is not row and len(other['path']) > len(row['path'])
                           and [s.split(' ', 1)[0] for s in other['path'][-len(row['path']) + 1:]]
                           == [s.split(' ', 1)[0] for s in row['path'][1:]] for other in rows)]

    def handlers(self, ident, exception, hops, seen):
        """Located handlers for an exception leaving ``ident``, looking through callers that let it pass."""
        found = []
        if hops >= MAX_HOPS:
            return found
        for caller_id, edge in sorted(self.graph.reverse.get(ident, {}).items()):
            caller = self.record(caller_id)
            if caller_id in seen or caller is None or caller['kind'] != 'function' or self.graph.is_test(caller):
                continue
            handler = self.handler(caller, edge[LINE], exception)
            if handler is None:
                found.extend((c, line, raised, guessed or edge[APPROXIMATE])
                             for c, line, raised, guessed in self.handlers(caller_id, exception, hops + 1, seen | {caller_id}))
            else:
                found.append((caller_id, handler[0], handler[2], edge[APPROXIMATE]))
        return found

    def chain(self, ident, exception, hops, seen):
        """Follow one exception while the path is unambiguous. Returns (steps, end, guessed) or None."""
        found = {(c, line): (c, line, raised, guessed) for c, line, raised, guessed in self.handlers(ident, exception, hops, seen)}
        if not found:
            return None
        caller_id, line, raised, guessed = sorted(found.values(), key=lambda h: (not h[0].startswith('src/'), h[0]))[0]
        caller = self.record(caller_id)
        onward = list(dict.fromkeys(name for name in raised if name))
        action = ('raises ' + ' | '.join(onward)) if onward else 're-raises it' if raised else 'handles it'
        step = f"{caller['path']}:{line} {caller['qual']} catches {exception}, {action}"
        if len(found) > 1:
            return [step], f"{len(found)} handlers catch {exception}; first is {caller['qual']}", guessed
        if len(onward) == 1:
            deeper = self.chain(caller_id, onward[0], hops + 1, seen | {caller_id})
            if deeper:
                return [step, *deeper[0]], deeper[1], guessed or deeper[2]
        return [step], f"{caller['qual']} {action}", guessed

    def state(self):
        rows = []
        for ident, record in self.after.items():
            owner = self.record(record.get('owner'))
            if record['kind'] != 'function' or not owner or owner['kind'] != 'class':
                continue
            lines = self.new_lines.get(record['path'], set())
            for attribute, line in dict.fromkeys(map(tuple, record.get('writes', []))):
                if line not in lines:
                    continue
                readers = []
                for other in self.graph.records.values():
                    if other.get('owner') != owner['id'] or other['id'] == ident or other['kind'] != 'function':
                        continue
                    other = self.record(other['id'])
                    reads = [at for expr, _, at in other.get('refs', []) if expr == ['attr', ['name', 'self'], attribute]]
                    written = {at for name, at in other.get('writes', []) if name == attribute}
                    reads = [at for at in reads if at not in written]
                    if reads:
                        readers.append(f"{other['path']}:{reads[0]} {other['qual']} reads self.{attribute}")
                if readers:
                    rows.append(dict(**{'from': f"{record['qual']} writes self.{attribute}"},
                                     to=f"{len(readers)} reader(s) in {owner['qual']}",
                                     path=[f"{record['path']}:{line} self.{attribute} assigned", *sorted(readers)[:3]],
                                     evidence='static source path'))
        return rows

    def results(self):
        rows = []
        for ident, record in self.after.items():
            if record['kind'] != 'function':
                continue
            lines = self.new_lines.get(record['path'], set())
            changed = [line for line in record.get('return_lines', []) if line in lines]
            if not changed:
                continue
            users = []
            for caller_id, edge in sorted(self.graph.reverse.get(ident, {}).items()):
                caller = self.graph.records[caller_id]
                if (edge[APPROXIMATE] or edge[SPECIFICITY] != 0 or caller['kind'] != 'function'
                        or self.graph.is_test(caller) or edge[LINE] in caller.get('unused', [])):
                    continue
                users.append(f"{caller['path']}:{edge[LINE]} {caller['qual']} uses the result")
            if users:
                rows.append(dict(**{'from': f"{record['qual']} return changed"}, to=f"{len(users)} caller(s) use the result",
                                 path=[f"{record['path']}:{changed[0]} return", *users[:3]], evidence='static source path'))
        return rows


def find(graph, after, new_lines, renumbered):
    finder = Finder(graph, after, new_lines, renumbered)
    rows = []
    for group in (finder.exceptions(), finder.state(), finder.results()):
        unique = {}
        for row in group:
            unique.setdefault((row['from'], row['to']), row)
        # Longer located chains explain more than a single hop.
        rows.extend(sorted(unique.values(), key=lambda r: (-len(r['path']), r['from'], r['to'])))
    return rows
