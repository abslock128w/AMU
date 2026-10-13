"""AMU's compact, static before-tests contract."""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

from . import consequences, coverage, lexical, rank, shadow
from .git import working_diff
from .index import build, parse_or_none
from .index import graph as build_graph
from .patch import applied, candidate, changes, original, renumber

LIMIT = 2000
MAX_TESTS = 40
RUN_CANDIDATES = 150  # executing is cheap next to guessing; --run looks well past the ranked list


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


def analyze(root, patch=None, *, full_evidence=False, coverage_evidence=None, index_dir=None, run=False, python=None, budget=60):
    """What a change reaches, which existing tests to run first, and what could not be seen.

    ``patch`` is unified-diff text, applied or not yet applied to ``root``. Without one,
    the uncommitted change of the working tree is used. Nothing is imported or executed.
    """
    root = Path(root).resolve()
    early = []
    if patch is None:
        patch, early = working_diff(root)
    facts, hashes, gaps, parsed, cache = build(root, index_dir)
    edits, patch_gaps = changes(root, patch)
    gaps.extend([*early, *patch_gaps])
    graph = build_graph(facts, hashes, cache)
    symbols, where = changed_symbols(root, facts, edits, gaps)
    order = {'function': 0, 'variable': 0, 'class': 1, 'module': 2}
    narrow = sorted(symbols, key=lambda s: (order[graph.records[s]['kind']], s))
    tests = {ident: r for ident, r in graph.records.items() if graph.is_test(r)}
    affinity = rank.Affinity(tests)
    content = rank.Content(tests)
    # What the patch says, per file: the words of its added and removed lines.
    said = {path: content.query(' '.join(text for _, text in [*edit['added'], *edit['removed']])) for path, edit in edits.items()}
    observed, coverage_status = coverage.load(root, coverage_evidence, hashes, gaps)
    recorded = {}
    for path, edit in edits.items():
        # A recording made before this edit is in parent line numbers; the tree is not.
        if observed and path in observed['files'] and path in observed['stale'] and applied(root, path, edit):
            place, moved = renumber(edit), {}
            for line, contexts in observed['files'][path].items():
                moved.setdefault(place(line), set()).update(contexts)
            observed['files'][path] = moved
            # Lines the patch added did not exist when the recording was made; it says nothing about them.
            recorded[path] = {place(line) for line in edit['old'] | edit['anchor']}
    scores, best, impacted, untested = {}, {}, {}, []
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
        seen = coverage.tests_for(observed, record, tests, where.get(symbol, ()))
        if record['kind'] == 'function' and symbol in where:
            lines = sorted(where[symbol])
            span = f'{lines[0]}-{lines[-1]}' if len(lines) > 1 else str(lines[0])
            judged = [line for line in lines if record['path'] not in recorded or line in recorded[record['path']]]
            if observed and record['path'] in observed['files'] and judged:
                rows_here = observed['files'][record['path']]
                if not any(coverage._test_identity(c) in tests for line in judged for c in rows_here.get(line, ())):
                    untested.append(dict(symbol=record['qual'], file=record['path'], lines=span, basis='coverage'))
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
    after, before, new_lines, renumbered = after_facts(root, facts, edits, gaps)
    gone_phrases = set().union(*(lexical.removed_phrases(edit) for edit in edits.values())) if edits else set()
    # Only files whose patched form was established can say a definition is gone.
    settled = {r['path'] for r in after.values()}
    elsewhere = {r['qual'].rsplit('.', 1)[-1] for r in graph.records.values()
                 if r['path'] not in settled and r['kind'] in ('function', 'class', 'variable') and not graph.is_test(r)}
    likely = lexical.matches(tests, gone_phrases, lexical.removed_names(
        {k: r for k, r in before.items() if r['path'] in settled}, after, elsewhere))
    for symbol, categories in lexical.removed_signals(before, after).items():
        near = [ident for ident, (position, _, _) in graph.reach([symbol], max_depth=2).items() if not position[2]]
        for ident, reason in lexical.expecting(tests, near, categories).items():
            likely.setdefault(ident, reason)
    for ident in likely:
        # A test found only by what it says still belongs in the list.
        scores.setdefault(ident, {}).setdefault('', 0.5)
        best.setdefault(ident, (0.5, (1, 2, True), [ident], [], True, None))
    # Likely failures lead; each group keeps its own relevance order.
    first = rank.diverse({t: v for t, v in scores.items() if t in likely}, MAX_TESTS)
    order = first + rank.diverse({t: v for t, v in scores.items() if t not in likely}, RUN_CANDIDATES if run else MAX_TESTS)
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
    found = consequences.find(graph, after, new_lines, renumbered)
    executed = None
    if run:
        # Exact beats ranked: run the candidates on both sides of the change and report what flips.
        wide = [tests[ident]['path'] + '::' + tests[ident]['qual'].replace('.', '::') for ident, _ in order[:RUN_CANDIDATES]]
        flips, executed, run_gaps = shadow.run(root, edits, wide, python, budget)
        gaps.extend(run_gaps)
        state = {flip['id']: flip for flip in flips}
        for row in rows:
            if row['id'] in state:
                row['expect'] = 'fails' if state[row['id']]['after'] == 'failed' else 'now_' + state[row['id']]['after']
            elif executed['ran'] and not run_gaps:
                row['expect'] = 'unchanged'
        rows.sort(key=lambda row: row['expect'] != 'fails')
        executed['predicted_failures'] = [flip for flip in flips if flip['after'] == 'failed']
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
    summary = f"{fails} likely_fail, {len(untested)} untested, {len(scores)} tests reach. Judge rows by expect and why; not a run-all list."
    if executed is not None:
        summary = (f"EXECUTED {executed['ran']} candidate tests on both sides in {executed['seconds']} s: "
                   f"{len(executed['predicted_failures'])} now fail. " + summary.split(', ', 1)[1].split('. ')[0] + '.')
    report = dict(summary=summary, changed_symbols=narrow, untested=untested, tests=rows, consequences=found,
                  impacted=listed, gaps=sorted(set(gaps)),
                  caveats='Static unless a row says observed. Not proof.')
    if coverage_status:
        report['coverage'] = coverage_status
    if executed is not None:
        report['predicted_failures'] = executed['predicted_failures']
    report['report_hash'] = hashlib.sha256(encoded([hashes, patch, report])).hexdigest()[:12]
    if full_evidence:
        report['index'] = {'parsed_files': parsed, 'files': len(facts)}
        return report
    return compact(report)


def compact(report):
    """Fit the agent packet under LIMIT bytes. Runnable test ids are the last thing to go."""
    out = dict(report)
    out['tests'] = [{k: row[k] for k in ('id', 'expect', 'why', 'confidence')} for row in report['tests'][:10]]
    out['untested'] = [dict(row) for row in report['untested'][:3]]
    if 'predicted_failures' in report:
        out['predicted_failures'] = [row['id'] for row in report['predicted_failures'][:12]]
        out['tests'] = [row for row in out['tests'] if row['expect'] != 'fails'][:6]
    out['changed_symbols'] = report['changed_symbols'][:5]
    out['impacted'] = [dict(row) for row in report['impacted'][:3]]
    # Steps keep the last two path components here; --full-evidence has full paths.
    out['consequences'] = [dict(row, path=[re.sub(r'^\S*/(?=[^/ ]+/[^/ ]+:\d)', '', step) for step in row['path']])
                           for row in report['consequences'][:2]]
    out['gaps'] = report['gaps'][:3]
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

    steps = [lambda: narrow(28),
             lambda: out['impacted'] and out['impacted'].pop(),
             lambda: len(out['gaps']) > 1 and out['gaps'].pop(),
             lambda: len(out['untested']) > 2 and out['untested'].pop(),
             lambda: len(out['changed_symbols']) > 3 and out['changed_symbols'].pop(),
             lambda: len(out['consequences']) > 1 and out['consequences'].pop(),
             lambda: len(out['changed_symbols']) > 1 and out['changed_symbols'].pop(),
             lambda: len(out['tests']) > 8 and out['consequences'] and out['tests'].pop(),
             lambda: narrow(12),
             lambda: out['consequences'] and out['consequences'].pop(),
             lambda: len(out['untested']) > 1 and out['untested'].pop(),
             lambda: len(out['tests']) > 1 and out['tests'].pop()]
    while len(encoded(out)) >= LIMIT:
        if not any(step() for step in steps):
            break
    return out
