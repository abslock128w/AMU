"""AMU's compact, static before-tests contract."""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

from . import consequences, coverage, rank
from .git import working_diff
from .index import build, parse_or_none
from .index import graph as build_graph
from .patch import applied, candidate, changes, renumber

LIMIT = 2000
MAX_TESTS = 40


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
    return ' > '.join(names)


def after_facts(root, facts, edits, gaps):
    """Records of changed files as of the candidate source, plus the lines the patch wrote."""
    after, new_lines, renumbered = {}, {}, set()
    for path, edit in edits.items():
        source = candidate(root, path, edit)
        parsed = parse_or_none(path, source) if source is not None else None
        if parsed is None:
            gaps.append(f'{path}: patch did not apply cleanly; consequences not derived')
            continue
        after.update(parsed['records'])
        new_lines[path] = edit['new']
        if not applied(root, path, edit):
            renumbered.add(path)
    return after, new_lines, renumbered


def analyze(root, patch=None, *, full_evidence=False, coverage_evidence=None, index_dir=None):
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
    for path, edit in edits.items():
        # A recording made before this edit is in parent line numbers; the tree is not.
        if observed and path in observed['files'] and path in observed['stale'] and applied(root, path, edit):
            place, moved = renumber(edit), {}
            for line, contexts in observed['files'][path].items():
                moved.setdefault(place(line), set()).update(contexts)
            observed['files'][path] = moved
    scores, best, impacted, unexecuted = {}, {}, {}, []
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
        if seen and next(iter(seen.values()))[1] is None and record['kind'] == 'function':
            unexecuted.append(record['qual'])
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
    rows = []
    for ident, gain in rank.diverse(scores, MAX_TESTS):
        _, position, path, shared, broad, exactness = best[ident]
        r = tests[ident]
        exact = not position[2] and not broad and position[1] == 0
        if exactness is not None:
            exactness, on_line = exactness
            why = ('ran the changed lines of ' if on_line else 'ran ') + label(graph, path) + (
                '' if exactness == 1 else ' (recorded before an edit to this file)')
            confidence = 'observed'
        else:
            why = ('calls ' if position[0] == 1 and exact else 'via ') + label(graph, path)
            confidence = 'heuristic' if not exact else 'direct' if position[0] == 1 else 'transitive'
        if shared:
            why += ' (name: ' + ','.join(shared) + ')'
        rows.append(dict(id=r['path'] + '::' + r['qual'].replace('.', '::'), why=why, confidence=confidence,
                         source='coverage' if exactness is not None else 'static', line=r['line'], score=round(gain, 3)))
    after, new_lines, renumbered = after_facts(root, facts, edits, gaps)
    found = consequences.find(graph, after, new_lines, renumbered)
    unresolved = graph.unresolved_count + graph.ambiguous
    if unresolved:
        gaps.append(f'{unresolved} call sites unresolved repo-wide; dynamic dispatch may reach more')
    if unexecuted:
        gaps.append('No recorded test ran the changed lines of ' + ', '.join(sorted(set(unexecuted))[:3]))
    if not rows:
        gaps.append('No existing test reaches the change within five source hops')
    listed = [dict(symbol=ident, depth=position[0], via=label(graph, path))
              for ident, (position, path) in sorted(impacted.items(), key=lambda item: (item[1][0], item[0]))]
    report = dict(changed_symbols=narrow, impacted=listed, tests=rows, consequences=found, gaps=sorted(set(gaps)),
                  caveats=f'Static candidates, not proof. {len(scores)} tests reach the change; run these first, not only.')
    if coverage_status:
        report['coverage'] = coverage_status
    report['report_hash'] = hashlib.sha256(encoded([hashes, patch, report])).hexdigest()[:12]
    if full_evidence:
        report['index'] = {'parsed_files': parsed, 'files': len(facts)}
        return report
    return compact(report)


def compact(report):
    """Fit the agent packet under LIMIT bytes. Runnable test ids are the last thing to go."""
    out = dict(report)
    out['tests'] = [{k: row[k] for k in ('id', 'why', 'confidence', 'source')} for row in report['tests'][:10]]
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
