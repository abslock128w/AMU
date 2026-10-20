"""AMU's compact, static before-tests contract."""
from __future__ import annotations

import fnmatch
import hashlib
import json
import math
import re
from pathlib import Path

from . import basecache, consequences, coverage, edges, lexical, rank, replay, runners, select, shadow, trust, vanished
from .git import working_diff
from .index import cache_dir, parse_or_none
from .index import state as load_state
from .patch import align, applied, candidate, changes, original, renumber
from .sitehooks import sweep

LIMIT = 2000
MAX_TESTS = 40
MIXIN_COPIES = 6  # concrete subclasses run for a test method that lives on a mixin
PLAIN_BASES = {'object', 'ABC', 'Generic', 'Protocol'}  # bases that add no test a runner collects
RUN_CANDIDATES = 150  # inheriting copies of failing base-class tests run at most


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()


def changed_symbols(root, facts, edits, gaps):
    """Map changed lines to symbols.

    Returns {identity: fallback}: the narrowest declaration holding each changed
    line, with the enclosing class as a fallback for unresolved receivers.
    Enclosing functions hold the line too and are changed in their own right.
    """
    changed, where = {}, {}
    for path, edit in edits.items():
        records = facts.get(path, {}).get('records', {})
        if not records:
            gaps.append(f'{path}: not in the tree; new code has no existing callers')
            continue
        # An agent may ask before or after writing its edit; use whichever side the tree holds.
        if applied(root, path, edit):
            # Removed lines left no line of their own; they count where their replacement or neighbour now sits.
            place = renumber(edit)
            lines = edit['new'] | {place(line) for line in edit['old']}
        else:
            lines = edit['old'] | edit['anchor']
        for line in sorted(lines):
            containing = sorted((r for r in records.values() if any(a <= line <= b for a, b in r['spans'])),
                                key=lambda r: (r['kind'] == 'module', min(b - a for a, b in r['spans'])))
            if not containing:
                continue
            narrow = containing[0]
            classes = [r['id'] for r in containing[1:] if r['kind'] == 'class']
            changed.setdefault(narrow['id'], classes[0] if classes and narrow['kind'] != 'class' else None)
            where.setdefault(narrow['id'], set()).add(line)
            for outer in containing[1:]:
                if outer['kind'] == 'function':
                    changed.setdefault(outer['id'], classes[0] if classes else None)
                    where.setdefault(outer['id'], set()).add(line)
    if edits and not changed:
        gaps.append('Changed lines could not be mapped to any declaration')
    return changed, where


def _read(root, path):
    try:
        return (Path(root)/path).read_text(errors='replace')
    except OSError:
        return None


def label(graph, path):
    names = [graph.records[x]['qual'] or graph.records[x]['module'] for x in path[1:]]
    return ' > '.join(names) or 'no resolved path'


def after_facts(root, facts, edits, gaps):
    """Records of changed files as of the candidate source, plus the lines the patch wrote."""
    after, before, new_lines, renumbered = {}, {}, {}, set()
    for path, edit in edits.items():
        earlier = original(root, path, edit)
        parsed = parse_or_none(path, earlier) if earlier is not None else None
        if parsed:
            before.update(parsed['records'])
        source = candidate(root, path, edit)
        parsed = parse_or_none(path, source) if source is not None else None
        if parsed is None:
            gaps.append(f'{path}: patch did not apply cleanly; consequences not derived')
            continue
        after.update(parsed['records'])
        new_lines[path] = edit['new']
        if not applied(root, path, edit):
            renumbered.add(path)
    return after, before, new_lines, renumbered


def analyze(root, patch=None, *, full_evidence=False, coverage_evidence=None, index_dir=None, run=None, python=None,
            budget=60, include_tests=False, runner=None, only=None, also=None, generate=None, merge_base=None):
    """What a change reaches, which existing tests to run first, and what could not be seen.

    ``patch`` is unified-diff text, applied or not yet applied to ``root``. Without one,
    the uncommitted change of the working tree is used. Nothing is imported or executed.
    ``merge_base``, with ``root`` at the change's head, is where execution takes a file the patch cannot be undone in.
    """
    root = Path(root).resolve()
    early = []
    own_tree = patch is None
    if patch is None:
        patch, early = working_diff(root)
    if run is None:
        # The developer's own uncommitted change, in a tree with tests to run: execute, do not guess.
        # A supplied patch may describe someone else's tree, so that stays static unless asked.
        run = own_tree and bool(patch.strip()) and runners.evident(root)
    python = python or (runners.interpreter(root) if run else None)
    # A recording killed before it could take its hook out of this interpreter would have every test run load it.
    swept = sweep(python) if run and python else []
    facts, hashes, gaps, parsed, graph = load_state(root, index_dir)
    edits, patch_gaps = changes(root, patch)
    gaps.extend([*early, *patch_gaps, *swept])
    symbols, where = changed_symbols(root, facts, edits, gaps)
    order = {'function': 0, 'variable': 0, 'class': 1, 'module': 2}
    narrow = sorted(symbols, key=lambda s: (order[graph.records[s]['kind']], s))
    tests = {ident: r for ident, r in graph.records.items() if graph.is_test(r)}
    chosen = runners.get(root, runner)
    affinity = rank.Affinity(tests)
    content = rank.Content(tests)
    # What the patch says, per file: the words of its added and removed lines.
    said = {path: content.query(' '.join(text for _, text in [*edit['added'], *edit['removed']])) for path, edit in edits.items()}
    observed, coverage_status = coverage.load(root, coverage_evidence, hashes, gaps, cache_dir(root, index_dir))
    identify = coverage.identifier(tests, graph)
    recorded = {}
    for path, edit in edits.items():
        # A recording is in the line numbers of the file as recorded; the tree may have moved on, through
        # this edit, through earlier ones, or both. Carry the recorded lines forward through each.
        if not observed or path not in observed['files'] or path not in observed['stale']:
            continue
        on_disk = applied(root, path, edit)
        before_edit = original(root, path, edit) if on_disk else None
        then = observed['text'](path) if observed.get('text') else None
        steps = []
        if then is not None:
            now = before_edit if on_disk else _read(root, path)
            if now is None:
                continue
            table = align(then, now)
            steps.append(table.get)
        elif not (on_disk and before_edit is not None and (
                'recorded' not in observed or hashlib.sha256(before_edit.encode()).hexdigest() == observed['recorded'].get(path))):
            continue  # no recorded text and other edits intervene: judged by function name instead
        if on_disk:
            steps.append(renumber(edit))
        moved = {}
        for line, contexts in observed['files'][path].items():
            for step in steps:
                line = step(line) if line is not None else None
            if line is not None:
                moved.setdefault(line, set()).update(contexts)
        observed['files'][path] = moved
        observed.setdefault('translated', set()).add(path)
        if on_disk:
            # Lines the patch added did not exist when the recording was made; it says nothing about them.
            place = renumber(edit)
            recorded[path] = {place(line) for line in edit['old'] | edit['anchor']}
    after, before, new_lines, renumbered = after_facts(root, facts, edits, gaps)
    # With the change on disk the index is the edited tree: a definition it renamed or removed is gone from the graph,
    # and so is every path to its tests. Find them by the old name instead.
    # Only files whose edited form parsed, with the edit on disk, can say a definition is gone; so can a deleted file.
    settled = {r['path'] for r in after.values()}
    taken = {k: r for k, r in before.items() if r['path'] in settled and applied(root, r['path'], edits[r['path']])}
    for path, source in vanished.deleted(patch).items():
        parsed_old = None if (root/path).exists() else parse_or_none(path, source)
        if parsed_old:
            taken.update(parsed_old['records'])
    removed = vanished.gone(taken, after)
    scores, best, impacted, untested, functions, observed_tests = {}, {}, {}, [], [], set()
    for symbol, fallback in symbols.items():
        record = graph.records[symbol]
        names = rank.symbol_tokens(record)
        weight = 0.3 if record['kind'] == 'module' else 1.0
        reached = {ident: (position, path, False) for ident, (position, path, _) in graph.reach([symbol]).items()}
        broad_weight = 0.0
        if fallback:
            # A reference to the owning class may reach the member through a receiver we could not bind.
            extra = {ident: found for ident, found in graph.reach([fallback]).items() if ident not in reached}
            for ident, (position, path, _) in extra.items():
                reached[ident] = ((position[0], max(1, position[1]), True), path, True)
            # The more tests merely mention the class, the less each mention says about this member.
            broad_weight = 0.5 / math.log2(2 + sum(ident in tests for ident in extra))
        seen = coverage.tests_for(observed, record, tests, where.get(symbol, ()), identify)
        observed_tests.update(seen)
        if record['kind'] == 'function' and symbol in where:
            lines = sorted(where[symbol])
            span = f'{lines[0]}-{lines[-1]}' if len(lines) > 1 else str(lines[0])
            functions.append(dict(symbol=record['qual'], file=record['path'], lines=span))
            judged = [line for line in lines if record['path'] not in recorded or line in recorded[record['path']]]
            by_name = (observed and observed.get('by_function') and record['path'] in observed['stale']
                       and record['path'] not in observed.get('translated', ()))
            if by_name:
                # The file changed since the recording in ways this patch does not explain: judge the
                # function by name. A function the recording never saw is left unjudged.
                ran = (observed['by_function'](record['path']) or {}).get(record['qual'])
                if ran is not None and not any(identify(c) in tests for c in ran):
                    untested.append(dict(symbol=record['qual'], file=record['path'], lines=span, basis='coverage'))
            elif observed and record['path'] in observed['files'] and judged:
                rows_here = observed['files'][record['path']]
                if not any(identify(c) in tests for line in judged for c in rows_here.get(line, ())):
                    untested.append(dict(symbol=record['qual'], file=record['path'], lines=span, basis='coverage'))
            elif observed and record['path'] in observed['files']:
                pass
            elif not any(ident in tests for ident in reached):
                untested.append(dict(symbol=record['qual'], file=record['path'], lines=span, basis='static_only'))
        for ident, (position, path, broad) in reached.items():
            if ident not in tests:
                if not broad and (ident not in impacted or position < impacted[ident][0]):
                    impacted[ident] = (position, path)
                continue
            match, shared = affinity.score(ident, names)
            about = content.score(ident, said.get(record['path'], (set(), 0)))
            value = weight * rank.structural(position) * (broad_weight if broad else 1.0) * (1 + 2 * match + 2 * about)
            scores.setdefault(ident, {})[symbol] = value
            if ident not in best or value > best[ident][0]:
                best[ident] = (value, position, path, shared, broad, None)
        for ident, (exactness, on_line) in seen.items():
            # Recorded execution outranks any static guess, and adds tests static analysis missed.
            # Running the changed lines themselves counts for more than entering the declaration.
            match, shared = affinity.score(ident, names)
            about = content.score(ident, said.get(record['path'], (set(), 0)))
            strength = 0.5 if on_line is None else 0.15 + 0.85 * on_line
            # What ran outweighs what merely sounds related: names only nudge recorded evidence.
            value = weight * rank.OBSERVED * exactness * strength * (1 + 0.5 * match + 0.5 * about) + scores.get(ident, {}).get(symbol, 0)
            scores.setdefault(ident, {})[symbol] = value
            if ident not in best or best[ident][5] is None or value > best[ident][0]:
                best[ident] = (value, (1, 0, False), [ident, symbol], shared, False, (exactness, on_line))
    calls = edges.load(cache_dir(root, index_dir))
    if calls:
        # A per-test call recording (edges module): tests seen entering a changed function are candidates with evidence,
        # however they reached it. Matched by function name, so it holds across edits made since.
        entered = {identify(label) for label in edges.tests_entering(calls, set(symbols))} - {None}
        observed_tests.update(ident for ident in entered if ident in tests)
    named = {}
    if removed:
        extra_scores, extra_best = vanished.candidates(root, graph, tests, removed, affinity, content, taken)
        for ident, per in extra_scores.items():
            scores.setdefault(ident, {}).update(per)
        for ident, (value, position, path, shared, why) in extra_best.items():
            if ident not in best or value > best[ident][0]:
                # Found by the old name, not through a resolved call: a heuristic row that says what it matched.
                best[ident] = (value, (position[0], position[1], True), path, shared, True, None)
                named[ident] = why
    gone_phrases = set().union(*(lexical.removed_phrases(edit) for edit in edits.values())) if edits else set()
    elsewhere = {r['qual'].rsplit('.', 1)[-1] for r in graph.records.values()
                 if r['path'] not in settled and r['kind'] in ('function', 'class', 'variable') and not graph.is_test(r)}
    likely = lexical.matches(tests, gone_phrases, lexical.removed_names(
        {k: r for k, r in before.items() if r['path'] in settled}, after, elsewhere))
    for symbol, categories in lexical.removed_signals(before, after).items():
        near = [ident for ident, (position, _, _) in graph.reach([symbol], max_depth=2).items() if not position[2]]
        for ident, reason in lexical.expecting(tests, near, categories).items():
            likely.setdefault(ident, reason)
    collects, heirs_of, supers = {}, {}, {}
    class_patterns = runners.pytest_classes(root) if chosen.name == 'pytest' else ()

    def pytest_collects(name):
        """Whether pytest collects a class by its name: the project's ``python_classes``, prefixes or globs, else Test."""
        return any(fnmatch.fnmatchcase(name, p) if any(c in p for c in '*?[') else name.startswith(p) for p in class_patterns)

    for parent, subs in graph.subclasses.items():
        for sub in subs:
            supers.setdefault(sub, set()).add(parent)

    def declared(ident):
        """A declaration as the change leaves it: from the changed file when that parsed, else from the graph."""
        return after.get(ident) if ident.split('#')[0] in settled else graph.records.get(ident)

    def parents(base, rec):
        """The classes a base expression names. The graph's class links, made when it was built, answer by name too: a
        graph loaded from its snapshot has slim records and resolves no name. A bare name the same changed file declares
        is read from that file: with the change not yet written, the graph has not seen it."""
        name = base[-1] if base and base[0] in ('name', 'attr') and isinstance(base[-1], str) else ''
        if base and base[0] == 'name' and rec['path'] in settled and (declared(rec['path'] + '#' + name) or {}).get('kind') == 'class':
            return {rec['path'] + '#' + name}
        linked = {parent for parent in supers.get(rec['id'], ()) if (declared(parent) or {}).get('qual', '').rsplit('.', 1)[-1] == name}
        return linked | {parent for parent in graph.resolve(base, rec['owner']) if (declared(parent) or {}).get('kind') == 'class'}

    def collected(cls, seen=()):
        """Whether a runner collects tests from this class: True for a TestCase descendant (or Test* under pytest), False
        for a mixin (every base followed and none leads to one), None when a base could not be followed."""
        if cls in collects:
            return collects[cls]
        rec = declared(cls) or {}
        if rec.get('kind') != 'class':
            return None
        runs, unknown = chosen.name == 'pytest' and pytest_collects(rec['qual'].rsplit('.', 1)[-1]), False
        for base in rec['bases']:
            name = base[-1] if base and base[0] in ('name', 'attr') and isinstance(base[-1], str) else ''
            found = parents(base, rec) - {*seen, cls}
            states = [collected(parent, (*seen, cls)) for parent in found]
            runs = runs or name.endswith('TestCase') or True in states
            # A base named outside the tree may be a TestCase, and so may one that names the class itself (a redefinition
            # that extends the first); object and a *Mixin add no test a runner collects.
            unknown = unknown or None in states or not found and name not in PLAIN_BASES and not name.endswith('Mixin')
        collects[cls] = True if runs else None if unknown else False
        return collects[cls]

    def heirs(cls):
        """Subclasses of ``cls``: the graph's, and those a changed file declares as the change leaves it."""
        if cls not in heirs_of:
            found, pending = set(), [cls]
            while pending:
                parent = pending.pop()
                direct = {sub for sub in graph.subclasses.get(parent, ()) if declared(sub)} | {
                    ident for ident, r in after.items() if r['kind'] == 'class' and any(parent in parents(b, r) for b in r['bases'])}
                pending += sorted(direct - found)
                found |= direct
            heirs_of[cls] = found
        return heirs_of[cls]

    def inheritors(test):
        """Ids of the same test method (path::Class::test) in subclasses that inherit it and that a runner may collect: it
        runs, and fails, there too. Those seen to be collected come first."""
        path, *quals = test.split('::')
        if len(quals) < 2:
            return []
        name = quals[-1]
        return [sub.split('#')[0] + '::' + declared(sub)['qual'].replace('.', '::') + '::' + name
                for sub in sorted(heirs(path + '#' + '.'.join(quals[:-1])), key=lambda sub: (collected(sub) is not True, sub))
                if declared(sub + '.' + name) is None and collected(sub) is not False and sub.split('#')[0].split('/')[0] == path.split('/')[0]]

    def run_ids(test):
        """The ids a runner can execute for a test (path::Class::test), whatever named it: itself, or, for a method on a
        class no runner collects (a mixin), the subclasses that inherit it, up to MIXIN_COPIES. Nothing for a mixin's
        method that no subclass inherits: its own label would stop the runner, and no class runs it."""
        path, *quals = test.split('::')
        state = collected(path + '#' + '.'.join(quals[:-1])) if len(quals) > 1 else True
        if state:
            return [test]
        return inheritors(test)[:MIXIN_COPIES] or ([test] if state is None else [])

    def label_of(ident):
        return tests[ident]['path'] + '::' + tests[ident]['qual'].replace('.', '::')

    inherited = {ident: copies for ident in likely if (copies := inheritors(label_of(ident)))}
    for ident in likely:
        # A test found only by what it says still belongs in the list.
        scores.setdefault(ident, {}).setdefault('', 0.5)
        best.setdefault(ident, (0.5, (1, 2, True), [ident], [], True, None))
    # Likely failures lead; each group keeps its own relevance order.
    first = rank.diverse({t: v for t, v in scores.items() if t in likely}, MAX_TESTS)
    # The diverse pick orders what is shown; what runs is chosen by subtraction (select), with scores as a tiebreaker.
    order = first + rank.diverse({t: v for t, v in scores.items() if t not in likely}, MAX_TESTS)
    rows = []
    for ident, gain in order[:MAX_TESTS]:
        _, position, path, shared, broad, exactness = best[ident]
        r = tests[ident]
        exact = not position[2] and not broad and position[1] == 0
        if exactness is not None:
            _, on_line = exactness
            exactness = exactness[0], on_line
            exact_value = exactness[0]
            why = ('ran the changed lines of ' if on_line else 'ran ') + label(graph, path) + (
                '' if exact_value == 1 else ' (recorded before an edit to this file)')
            confidence = 'observed'
        else:
            why = ('calls ' if position[0] == 1 and exact else 'via ') + label(graph, path)
            confidence = 'heuristic' if not exact else 'direct' if position[0] == 1 else 'transitive'
            if ident in named:
                why = (why + ', ' if len(path) > 1 else '') + named[ident]
        if shared:
            why += ' (name: ' + ','.join(shared) + ')'
        expect = 'reaches'
        if exactness is not None and exactness[1]:
            expect = 'ran_changed_lines'
        if ident in likely:
            expect, why = 'likely_fail', likely[ident] + ('; ' + why if len(path) > 1 else '')
            confidence = confidence if len(path) > 1 else 'lexical'
        rows.append(dict(id=r['path'] + '::' + r['qual'].replace('.', '::'), expect=expect, why=why, confidence=confidence,
                         line=r['line'], score=round(gain, 3)))
        for copy in inherited.get(ident, [])[:8]:
            rows.append(dict(id=copy, expect=expect, why='inherits ' + r['qual'] + '; ' + likely[ident], confidence=confidence,
                             line=r['line'], score=round(gain, 3)))
    found = consequences.find(graph, after, new_lines, renumbered, gaps)
    executed = None
    if run:
        # Exact beats ranked: run the candidates on both sides of the change and report what flips.
        # With a recording, the candidates are the tests seen executing the change; run more of them.
        # Selection by subtraction (select module): the import closure of the changed files, hubs passed only on
        # recorded evidence, plus what static reach found; ordered by recent flips and measured durations, the
        # ranking only breaking ties. The time the budget allows caps it, not a count.
        picked, selection = select.candidates(graph, facts, list(edits), tests, observed_tests, scores)
        position = {ident: n for n, (ident, _) in enumerate(order)}
        tail = sorted((i for i in picked if i not in position), key=lambda i: (-max(scores.get(i, {'': 0}).values() or [0]), i))
        position.update({ident: len(position) + n for n, ident in enumerate(tail)})
        memo = cache_dir(root, index_dir)/'shadow-memo.json'
        flipped_at, durations = shadow.history(memo)
        expanded = list(dict.fromkeys(run_id for ident in sorted(picked, key=position.get) for run_id in run_ids(label_of(ident))))
        tiebreak = {run_id: n for n, run_id in enumerate(expanded)}
        wide = select.order(expanded, flips=flipped_at, durations=durations, tiebreak=tiebreak)
        if only:
            # A caller that already knows which tests matter (a hook re-checking earlier failures) names them.
            wide = list(dict.fromkeys(run_id for test in only for run_id in run_ids(test)))
        elif also:
            # Tests a caller wants run in any case (earlier failures), ahead of the ranked candidates.
            wide = list(dict.fromkeys([*(run_id for test in also for run_id in run_ids(test)), *wide]))
        # Every test the change adds or edits runs, first, and is replayed on the code before the change. One on a mixin
        # runs, and is replayed, in the subclasses that inherit it.
        touched = [] if only else replay.touched(root, edits, chosen.name)
        own = list(dict.fromkeys(run_id for test in touched for run_id in run_ids(test)))
        wide = list(dict.fromkeys([*own, *wide]))
        # A ranked mixin method no class inherits is dropped quietly, as before: the project's own suite never runs it, and
        # most are test-named methods of helper classes. One the change or the caller names is said.
        unrun = sorted({test for test in [*touched, *(only or also or ())] if not run_ids(test)})
        if unrun:
            gaps.append(f'{len(unrun)} test(s) defined on a mixin, a class {chosen.name} does not collect, that no test class inherits: '
                        'not run: ' + ', '.join(unrun[:3]) + (f' and {len(unrun) - 3} more' if len(unrun) > 3 else ''))
        # Passing outcomes on the code before the change, by content: a second run against the same base runs nothing
        # there that the first saw pass.
        forward = {path: sorted({t for name in select.deps(fact) if (t := select.resolve(name, graph.modules)) and t != path})
                   for path, fact in facts.items()}
        cache = basecache.BaseCache(cache_dir(root, index_dir)/'base-passes', forward, root, python,
                                    [chosen.name, *runners.plugin_args(runners.get(root, runner), python), trust.SEED])
        flips, executed, run_gaps = shadow.run(root, edits, wide, python, budget, runner, memo=memo, own=own, base_cache=cache,
                                               functions=functions, generate_with=generate, merge_base=merge_base)
        executed['selection'] = dict(selection, candidates=len(wide))
        gaps.extend(run_gaps)
        # A failing test defined on a base class is run again in every subclass that inherits it.
        ranked = {label_of(ident) for ident in picked}
        copies = sorted({copy for flip in flips if flip['after'] == 'failed' and flip['id'] in ranked
                         and trust.counted(flip.get('trust', trust.CONFIRMED))
                         for copy in inheritors(flip['id'])} - set(wide))[:RUN_CANDIDATES]
        if copies:
            more, again, more_gaps = shadow.run(root, edits, copies, python, budget, runner, memo=memo, base_cache=cache,
                                                merge_base=merge_base)
            flips.extend(more)
            gaps.extend(more_gaps)
            executed.update(ran=executed['ran'] + again['ran'], seconds=round(executed['seconds'] + again['seconds'], 1),
                            candidates=executed.get('candidates', 0) + again.get('candidates', 0),
                            outcomes={**executed.get('outcomes', {}), **again.get('outcomes', {})},
                            unstable=sorted({*executed.get('unstable', []), *again.get('unstable', [])}),
                            not_caused=sorted({*executed.get('not_caused', []), *again.get('not_caused', [])}),
                            base_cache=again.get('base_cache', executed.get('base_cache')),
                            ran_original=executed.get('ran_original', 0) + again.get('ran_original', 0),
                            original_tests={**executed.get('original_tests', {}), **again.get('original_tests', {})})
        # Only a flip that passed the trust checks is a failure; the others stay listed with their label.
        labelled = [flip for flip in flips if flip.get('trust') and flip['after'] == 'failed']
        flips = [flip for flip in flips if trust.counted(flip.get('trust', trust.CONFIRMED))]
        state = {flip['id']: flip for flip in flips}
        for row in rows:
            if row['id'] in state:
                row['expect'] = 'fails' if state[row['id']]['after'] == 'failed' else 'now_' + state[row['id']]['after']
            elif executed.get('outcomes', {}).get(row['id']) in ('passed', 'skipped'):
                row['expect'] = 'unchanged'
        rows.sort(key=lambda row: row['expect'] != 'fails')
        executed['predicted_failures'] = [flip for flip in flips if flip['after'] == 'failed']
        executed['flip_trust'] = [dict(id=f['id'], label=f['trust'], why=f.get('why', ''), reruns=f.get('reruns'), reach=f.get('reach'),
                                       **{k: f[k] for k in ('path', 'raised', 'isolated') if k in f})
                                  for f in labelled]
        replayed = executed.get('replayed') or []
        executed['change_evidence'] = [dict(id=row['id'], before=row['base'], after=row['head'], label=replay.EVIDENCE)
                                       for row in replayed if row['verdict'] == 'evidence']
        both = [row['id'] for row in replayed if row['verdict'] == 'fails_both']
        if both:
            gaps.append(f"{len(both)} test(s) the change adds or edits fail before and after it: " + ', '.join(both[:3])
                        + (f' and {len(both) - 3} more' if len(both) > 3 else ''))
        head_only = [row['id'] for row in replayed if row['verdict'] == 'fails_head']
        if head_only:
            gaps.append(f"{len(head_only)} test(s) the change adds or edits pass on the code before it and fail after it: "
                        + ', '.join(head_only[:3]) + (f' and {len(head_only) - 3} more' if len(head_only) > 3 else ''))
        shown = {row['id'] for row in rows}
        for flip in executed['predicted_failures']:
            if flip['id'] not in shown:
                rows.insert(0, dict(id=flip['id'], expect='fails', why='failed when executed after the change',
                                    confidence='executed', line=0, score=0.0))
    unresolved = graph.unresolved_count + graph.ambiguous
    if unresolved:
        gaps.append(f'{unresolved} call sites unresolved repo-wide; dynamic dispatch may reach more')
    if not rows:
        gaps.append('No existing test reaches the change within five source hops')
    listed = [dict(symbol=ident, depth=position[0], via=label(graph, path))
              for ident, (position, path) in sorted(impacted.items(), key=lambda item: (item[1][0], item[0]))]
    fails = sum(row['expect'] == 'likely_fail' for row in rows)
    summary = f"{fails} likely_fail, {len(scores)} tests reach the change."
    if executed is not None and (executed['ran'] or executed.get('ran_original')):
        summary = (f"EXECUTED {executed['ran']} of {executed.get('candidates', executed['ran'])} candidate tests after the change with {executed['runner']} in {executed['seconds']} s, failures confirmed before it: "
                   f"{len(executed['predicted_failures'])} now fail. " + summary.split(', ', 1)[1])
        if executed.get('ran_original'):
            summary += f" {executed['ran_original']} test(s) from edited test files also run as they were before the edit."
        edited = sum(flip.get('label') == shadow.EDITED for flip in executed['predicted_failures'])
        if edited:
            summary += (f" {edited} of the failures pass, are skipped or are gone only because the test was edited; as it was, "
                        f"it still fails on the changed code: {shadow.EDITED}.")
        if executed.get('unstable'):
            summary += f" {len(executed['unstable'])} flipped and did not fail again in every rerun: unstable, not counted."
        if executed.get('not_caused'):
            summary += f" {len(executed['not_caused'])} flipped without running any changed line: {trust.NOT_CAUSED}, not counted."
        if executed.get('replayed'):
            summary += (f" {len(executed['replayed'])} test(s) the change adds or edits replayed on the code before it: "
                        f"{len(executed['change_evidence'])} fail there and pass after ({replay.EVIDENCE}).")
    elif executed is not None and executed.get('aborted'):
        # The runner starts on the code before the change and not after it: the change broke it.
        aborted = executed['aborted']
        summary = (f"FAILED: the test runner ({executed['runner']}) aborts on the changed code before running any test: {aborted['error']}. "
                   f"On the code before the change it ran {aborted['ran_before']} of the same {aborted['probed']} candidates.")
    elif executed is not None:
        # Saying "0 now fail" when nothing ran would be a false all-clear.
        summary = 'NOT EXECUTED: no candidate test could be run; static analysis only. ' + summary
    report = dict(summary=summary, changed_symbols=narrow + [f"{r['path']}#{r['qual']} (renamed or removed)" for r in removed.values()],
                  untested=untested, tests=rows, consequences=found,
                  impacted=listed, gaps=sorted(set(gaps)),
                  caveats='Static unless a row says observed. Not proof.')
    # Every listed test carries the exact command for the project's own runner.
    report['runner'] = chosen.name
    for row in rows:
        row['run'] = chosen.paste(row['id']) if not chosen.needs_class or row['id'].count('::') >= 2 else None
    if coverage_status:
        report['coverage'] = coverage_status
    if calls:
        report['call_recording'] = dict(tests=len(calls['tests']), recorded_at=calls.get('recorded_at'),
                                        seconds=calls.get('seconds'))
    if executed is not None:
        report['predicted_failures'] = executed['predicted_failures']
        # What flipped comes first, then what could not be seen.
        report = dict(flips=len(executed['predicted_failures']) if executed['ran'] or executed.get('ran_original') else None,
                      predicted_failures=report['predicted_failures'],
                      gaps=report['gaps'], **{k: v for k, v in report.items() if k not in ('predicted_failures', 'gaps')})
        if executed.get('unstable'):
            report['unstable'] = executed['unstable']
        if executed.get('flip_trust'):
            # Every flip with its label (fails, unstable, not caused by this change), why, and each rerun's outcome.
            report['flip_trust'] = executed['flip_trust']
            report['order'] = executed.get('order')
        if executed.get('side_by_side') is not None:
            # Changed functions run side by side on the inputs the tests called them with (sidebyside).
            report['behaviour_differences'] = [{k: row[k] for k in ('target', 'input', 'differs', 'before', 'after', 'test', 'label')}
                                               for row in executed.get('behaviour', [])]
            report['side_by_side'] = {k: v for k, v in executed['side_by_side'].items() if k != 'skipped'}
            report['side_by_side']['not_compared'] = executed['side_by_side'].get('skipped', {})
        if executed.get('generated') is not None:
            # Model-written probes for changed functions no test called, kept only on execution evidence (generate).
            report['generated'] = executed['generated']
            report['generated_differences'] = executed.get('generated_differences', [])
        if executed.get('replayed'):
            report['change_evidence'] = executed['change_evidence']
            report['replayed'] = executed['replayed']
        if executed.get('original_tests'):
            # Which tests of an edited test file were also run as they were before the edit, and how they did.
            report['original_tests'] = executed['original_tests']
        if executed.get('aborted'):
            report = dict(failure=dict(executed['aborted'], runner=executed['runner']), flips=None, **{k: v for k, v in report.items() if k != 'flips'})
    report['report_hash'] = hashlib.sha256(encoded([hashes, patch, report])).hexdigest()[:12]
    if full_evidence:
        report['index'] = {'parsed_files': parsed, 'files': len(facts)}
        if executed is not None:
            # How much actually ran, for readers that must not claim more (the pull request comment).
            report['executed'] = dict({k: executed.get(k) for k in ('ran', 'candidates', 'unrunnable', 'seconds', 'runner')},
                                      budget=budget, replayed=len(executed.get('replayed') or []), selection=executed.get('selection'),
                                      base_cache=executed.get('base_cache'))
        report['changed_functions'] = functions
        return report
    return compact(report, include_tests)


def compact(report, include_tests=False):
    """Fit the agent packet under LIMIT bytes.

    By default only tests expected to fail are listed. In two agent trials a list of tests that
    merely reach the change did not help, and cost test runs; ``include_tests`` brings it back.
    """
    out = dict(report)
    listed = report['tests'] if include_tests else [row for row in report['tests'] if row['expect'] in ('likely_fail', 'fails')]
    native = report.get('runner') == 'pytest'
    out['tests'] = [{k: row[k] for k in ('id', 'expect', 'why', 'confidence') + (() if native else ('run',))} for row in listed[:10]]
    if not include_tests:
        out['caveats'] = report['caveats'] + ' Tests that only reach the change are omitted; --tests lists them.'
        report = dict(report, caveats=out['caveats'])
    # Untested rows are evidence for a reader, not a signal: scored on Django history, neither the static
    # rows nor the recording-backed ones were more likely to be fixed later than covered functions.
    out.pop('untested', None)
    if 'predicted_failures' in report:
        commands = {row['id']: row.get('run') for row in report['tests']}
        out['predicted_failures'] = [(row['id'] if native else commands.get(row['id']) or row['id']) for row in report['predicted_failures'][:12]]
        edited = [row['id'] for row in report['predicted_failures'] if row.get('label') == shadow.EDITED]
        if edited:
            # Kept apart from the pasteable commands: these fail only in the form the test had before it was edited.
            out['test_edited_not_fixed'] = edited[:12]
        if report.get('unstable'):
            out['unstable'] = report['unstable'][:5] + ([f"and {len(report['unstable']) - 5} more"] if len(report['unstable']) > 5 else [])
        out.pop('original_tests', None)
        out['tests'] = [row for row in out['tests'] if row['expect'] != 'fails'][:6]
    out['changed_symbols'] = report['changed_symbols'][:5]
    out['impacted'] = [dict(row) for row in report['impacted'][:3]]
    # Steps keep the last two path components here; --full-evidence has full paths.
    out['consequences'] = [dict(row, path=[re.sub(r'^\S*/(?=[^/ ]+/[^/ ]+:\d)', '', step) for step in row['path']])
                           for row in report['consequences'][:2]]
    # Shadow gaps can repeat long test ids; they are cut, the full text is in --full-evidence.
    out['gaps'] = [gap if len(gap) <= 240 or 'predicted_failures' not in report else gap[:237] + '...' for gap in report['gaps'][:3]]
    hidden = len(report['changed_symbols']) - len(out['changed_symbols'])
    more = len(report['consequences']) - len(out['consequences'])
    out['caveats'] = (report['caveats'] + (f' +{hidden} changed symbols.' if hidden else '')
                      + (f' +{more} consequences.' if more else '') + ' More: --full-evidence.')
    width = [72]

    def narrow(floor):
        if width[0] <= floor:
            return False
        width[0] -= 4
        for row in out['tests']:
            if len(row['why']) > width[0]:
                row['why'] = row['why'][:width[0] - 1] + '~'
        return True

    def fewer_unstable():
        shown = [i for i in out.get('unstable', []) if not i.startswith('and ')]
        if len(shown) <= 1:
            return False
        out['unstable'] = shown[:-1] + [f"and {len(report['unstable']) - len(shown) + 1} more"]
        return True

    steps = [lambda: narrow(28),
             lambda: out['impacted'] and out['impacted'].pop(),
             lambda: len(out['gaps']) > 1 and out['gaps'].pop(),
             fewer_unstable,
             lambda: len(out['changed_symbols']) > 3 and out['changed_symbols'].pop(),
             lambda: len(out['consequences']) > 1 and out['consequences'].pop(),
             lambda: len(out['changed_symbols']) > 1 and out['changed_symbols'].pop(),
             lambda: len(out['tests']) > 8 and out['consequences'] and out['tests'].pop(),
             lambda: narrow(12),
             lambda: out['consequences'] and out['consequences'].pop(),
             lambda: len(out['tests']) > 1 and out['tests'].pop()]
    while len(encoded(out)) >= LIMIT:
        if not any(step() for step in steps):
            break
    return out
