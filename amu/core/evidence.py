"""Execution-first selection: which tests run on each side of a change, chosen from what the tests were seen to run.

The order of evidence:

1. **The whole suite.** When the suite's last measured time on the code before the change fits the budget, or no
   time was measured yet, every test runs on that side, recorded (record module). If that run finishes inside the
   planned share of the budget, every test also runs on the code after the change: no selection.
2. **Coverage of the changed code.** Otherwise the candidates are the tests the recording saw entering the code each
   hunk changes (hunks module): this run's recording, or one kept for the same commit (``Coverage``).
3. **The static graph**, only for a hunk no recording can speak for: code the change adds where nothing ran before, a
   hunk whose file was not recorded, a module-level hunk that runs at import, or a recording that did not finish.

Every candidate keeps where it came from (``sources``), and each hunk the tests that ran it on each side.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from . import hunks as hunk_split
from . import record, replay

FILL = 0.8  # share of the budget a whole-suite run may take and still be run on the other side too
WHOLE = 400  # tests per process in one chunk of a whole-suite run: fewer runner starts; a chunk the budget stops is lost
ALL, COVERAGE, STATIC, OWN = 'all of the suite', 'coverage', 'static graph', "the change's own test"
RECORDED, CHANGED = 'suite recording', 'test file changed since the recording'
CUT = 'seen before the recording was cut off'


def _sha(text):
    return hashlib.sha256((text or '').encode()).hexdigest()[:16]


class Coverage:
    """Per-test coverage of changed files on one commit, kept between runs (the CI cache is keyed by the merge base).
    For each file: the hash of its text, the lines that were watched, and {line: [test]}. Only a recording of the whole
    suite is kept, so a watched line nobody ran was run by no test."""

    def __init__(self, folder, key):
        self.path = Path(folder)/'coverage'/f'{key}.json' if folder and key else None
        try:
            self.data = json.loads(self.path.read_text()) if self.path else {}
        except (OSError, ValueError):
            self.data = {}

    def tests(self, path, text, lines):
        """Tests that ran any of ``lines``, or None when this commit's recording did not watch all of them."""
        entry = (self.data.get('files') or {}).get(path)
        if not entry or entry.get('sha') != _sha(text) or not set(lines) <= set(entry.get('watched', [])):
            return None
        hits = entry.get('hits', {})
        return {test for line in lines for test in hits.get(str(line), [])}

    def store(self, before_src, watched, coverage, tests_run):
        if not self.path:
            return
        files = dict(self.data.get('files') or {})
        for path, lines in watched.items():
            hits = {}
            for test, seen in coverage.items():
                for line in seen.get(path, ()):
                    hits.setdefault(str(line), []).append(test)
            files[path] = dict(sha=_sha(before_src.get(path)), watched=sorted(lines), hits={k: sorted(v) for k, v in hits.items()})
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(dict(files=files, tests=tests_run)))
        except OSError:
            pass


def _chunks(execute, runner, python, tree, ids, scratch, name, budget, deadline, parts, env, chunk):
    """Run ``ids`` in order, a chunk at a time, until they are done or ``budget`` seconds are used. Returns
    ({id: state}, [problems], seconds, {id: the slice it ran in}); ``problems`` holds 'wall cap' when the deadline
    stopped it."""
    found, problems, context, began = {}, [], {}, time.perf_counter()
    for start in range(0, len(ids), chunk * parts):
        left = budget - (time.perf_counter() - began)
        if deadline is not None:
            if deadline - time.monotonic() <= 1:
                problems.append('wall cap')
                break
            left = min(left, deadline - time.monotonic())
        if left <= 1:
            break
        part = ids[start:start + chunk * parts]
        got, said = execute(runner, python, tree, part, scratch, f'{name}-{start}', left, parts, env)
        found.update(got)
        for slice_ in [part[i::parts] for i in range(parts)] if parts > 1 else [part]:
            context.update({ident: slice_ for ident in slice_})
        problems += [p for p in said if p not in problems]
        if 'timed out' in said:
            if deadline is not None and deadline - time.monotonic() <= 1:
                problems.append('wall cap')
            break
    return found, problems, round(time.perf_counter() - began, 1), context


RERUN = 60  # seconds at most for each run again of the tests whose calls differed
SECOND_SEED = '7919'  # the hash seed of the second run of the code before the change (the others use 0)
AGAIN = 200  # tests at most run again: those with a differing result first, then those whose inputs differed


def compare(execute, runner, python, trees, plan, scratch, deadline, parts, chunk, calls_b, calls_a, both, base_watch,
            head_watch, moved=None, second=None):
    """Calls of each changed function present on both sides, paired by test and call index (record.pair).

    The tests behind a difference (a result that differs on an equal input, or inputs that differ) run three times more,
    each call recorded field by field: on the code before the change, in its own tree and in a second tree made the way
    the tree after the change was made (``second``: a callable giving its path, or None to use the same tree), and on
    the code after the change. The second run before the change also has another hash seed. A field that differs between the two runs of the code before the change belongs to the
    process or the checkout, not to what the code does: it is left out of both sides' inputs and results for that
    function, and the pairs are compared again. Only these runs give rows; a difference whose test did not run again
    is counted (``not_rerun``), not shown."""
    differ = set()
    rows, counts = record.pair([c for c in calls_b if c['function'] in both], [c for c in calls_a if c['function'] in both], moved,
                               differ)
    tests = sorted({row['test'] for row in rows})
    tests += [t for t in sorted(differ) if t not in set(tests)]
    tests = tests[:AGAIN]
    kept, extra = rows, dict(unstable=0, excluded_fields=0, not_rerun=0, rerun_tests=0)
    if tests:
        runs = {}
        where = None
        if second is not None:
            try:
                where = second()
            except Exception:
                where = None
        for name, tree, watch, targets in (('before', trees['before'], base_watch, plan['targets_before']),
                                           ('before-2', where or trees['before'], base_watch, plan['targets_before']),
                                           ('after', trees['after'], head_watch, plan['targets_after'])):
            folder = Path(scratch)/f'record-{name}-again'
            env = record.prepare(folder, tree, watch, targets, fields=True)
            if name == 'before-2':
                env['PYTHONHASHSEED'] = SECOND_SEED  # what the process fixes arbitrarily differs too
            _chunks(execute, runner, python, tree, tests, scratch, f'{name}-again', RERUN, deadline, parts, env, chunk)
            runs[name] = [c for c in record.read(folder, tree, runner.cwd(tree))[1] if c['function'] in both]
        noisy = record.unstable_fields(runs['before'], runs['before-2'])
        again, again_counts = record.pair(record.without(runs['before'], noisy), record.without(runs['after'], noisy), moved)
        ran = {c['test'] for c in runs['before']} & {c['test'] for c in runs['after']}
        kept = again
        extra = dict(unstable=sum(1 for row in rows if row['test'] in ran and not any(
                         r['function'] == row['function'] and r['test'] == row['test'] for r in again)),
                     excluded_fields=sum(len(v['in'] | v['out']) for v in noisy.values()),
                     excluded={f: sorted(v['in'] | v['out']) for f, v in noisy.items()},
                     not_rerun=sum(1 for row in rows if row['test'] not in ran), rerun_tests=len(tests),
                     recovered=again_counts['compared'])
    called = sorted({c['function'] for c in calls_b if c['function'] in both} | {c['function'] for c in calls_a if c['function'] in both})
    return dict(rows=kept, counts=dict(counts, **extra), called=called, compared_functions=len(both))


def plan_hunks(edits, before_src, after_src):
    """Every hunk of the changed production files (tests are the change's own tests, replayed apart)."""
    return [h for path, edit in edits.items() if path.endswith('.py') and not replay.is_test_file(path)
            for h in hunk_split.split(path, edit, before_src.get(path) or '', after_src.get(path) or '')]


def run(execute, runner, python, trees, edits, before_src, after_src, plan, scratch, budget, deadline, parts, chunk,
        remembered, cache_folder, second=None):
    """Both sides of the change, chosen and run as the module says. Returns a dict: ids (the candidates run on the code
    after the change, in order), before/after ({id: state}), context, sources ({id: where it came from}), mode, hunks
    (each with the tests that ran it on each side), recorded coverage and calls per side, seconds, problems."""
    hunks = plan_hunks(edits, before_src, after_src)
    base_watch, head_watch = {}, {}
    for h in hunks:
        base_watch.setdefault(h['path'], set()).update(h['base'])
        head_watch.setdefault(h['path'], set()).update(h['head'])
    # Every function a hunk changes, on each side where it exists, is recorded in that side's own run (record).
    moved = {}
    for h in hunks:
        for qual in set(h['old_touched']) & set(h['new_touched']):
            found = hunk_split.changed_defaults(before_src.get(h['path']) or '', after_src.get(h['path']) or '', qual)
            if found:
                moved[f"{h['path']}#{qual}"] = found
    targets_before = sorted({(h['path'], qual, tuple(sorted(moved.get(f"{h['path']}#{qual}", {}))))
                             for h in hunks for qual in h['old_touched']})
    targets_after = sorted({(h['path'], qual, tuple(sorted(moved.get(f"{h['path']}#{qual}", {}))))
                            for h in hunks for qual in h['new_touched']})
    plan = dict(plan, targets_before=targets_before, targets_after=targets_after)
    own = list(dict.fromkeys(plan.get('own') or []))
    mine = set(own)
    static = [i for i in plan.get('static') or [] if i not in mine]
    everything = [i for i in plan.get('everything') or [] if i not in mine]
    durations = remembered.get('durations') or {}
    kept = Coverage(cache_folder, plan.get('key'))
    whole = plan.get('suite')  # suite.Recording of the nearest ancestor of the merge base, made outside this job
    suite = remembered.get('suite_seconds')
    if suite is None and whole is not None and whole.complete and whole.seconds is not None:
        suite = whole.seconds
    fits = suite is None or suite <= budget * FILL
    # What a kept recording of this commit already says about each hunk; None where it cannot speak.
    known = {(h['path'], h['index']): kept.tests(h['path'], before_src.get(h['path']), h['base']) if h['base'] else None
             for h in hunks}
    from_whole = set()
    if whole is not None:
        for h in hunks:
            key = (h['path'], h['index'])
            if h['base'] and known[key] is None:
                known[key] = whole.tests(h['path'], before_src.get(h['path']), h['base'])
                if known[key] is not None:
                    from_whole.add(key)
    # The whole suite runs here, recorded, when it fits. Otherwise a suite recording, when there is one, chooses; this
    # job never records the suite to choose. Without one, the run below records what it can in the budget, unless this
    # commit's kept recording already speaks for every hunk with code before the change.
    exploring = fits or (whole is None and any(h['base'] and known[h['path'], h['index']] is None for h in hunks))
    if exploring:
        # Static candidates first, so a run the budget stops still has their outcomes; then the rest, quickest first.
        first = set(static)
        rest = sorted((i for i in everything if i not in first), key=lambda i: (durations.get(i, 0.5), i))
        base_ids = list(dict.fromkeys([*static, *rest]))
    else:
        base_ids = None  # chosen from the kept recording below, then run
    sources, gaps = {}, []
    before_dir = Path(scratch)/'record-before'
    env = record.prepare(before_dir, trees['before'], base_watch, plan.get('targets_before') or [])
    coverage_b, calls_b = {}, []
    if base_ids is not None:
        found_b, problems_b, seconds_b, tried = _chunks(execute, runner, python, trees['before'], base_ids, scratch, 'before',
                                                       budget, deadline, parts, env, WHOLE if fits else chunk)
        coverage_b, calls_b, skipped_b = record.read(before_dir, trees['before'], runner.cwd(trees['before']))
        # Complete: every test was given to the runner and no budget stopped it. A test the runner never reports (one
        # that cannot load here) does not make a run incomplete.
        complete = bool(base_ids) and all(i in tried for i in base_ids) and not {'timed out', 'wall cap'} & set(problems_b) \
            and any(state in ('passed', 'failed', 'skipped') for state in found_b.values())
    else:
        found_b, problems_b, seconds_b, complete, skipped_b = {}, [], 0.0, False, {}
    mode = ALL if exploring and complete and seconds_b <= budget * FILL else COVERAGE
    if exploring and complete:
        remembered['suite_seconds'] = seconds_b
        kept.store(before_src, {p: v for p, v in base_watch.items() if v}, coverage_b, len(base_ids))
    elif exploring and not complete and fits:
        # The whole suite did not fit: next time the kept recording, or the static graph, chooses.
        remembered['suite_seconds'] = max(seconds_b, budget)
    for h in hunks:
        key = (h['path'], h['index'])
        h['seen_before_cut'] = []
        if complete or not exploring:
            ran = record.tests_on(coverage_b, h['path'], h['base']) if exploring else known.get(key)
        elif whole is not None and known.get(key) is not None:
            ran = known[key]  # this run was cut off; the suite recording speaks for the hunk
            from_whole.add(key)
        else:
            ran = None
            # A recording cut off by the budget still saw some tests run this hunk: they are candidates too.
            h['seen_before_cut'] = sorted(t for t in record.tests_on(coverage_b, h['path'], h['base']) if t != record.IMPORT)
        h['base_tests'] = sorted(t for t in (ran or ()) if t != record.IMPORT) if ran is not None else None
        h['at_import'] = bool(ran and record.IMPORT in ran)
    used_static = False
    if mode == ALL:
        head_ids = [*own, *base_ids]
        sources.update({i: ALL for i in base_ids})
    else:
        chosen, used_static = [], False
        for h in hunks:
            label = RECORDED if (h['path'], h['index']) in from_whole else COVERAGE
            if h['base_tests'] is not None and not (h['at_import'] and not h['base_tests']):
                chosen += [t for t in h['base_tests'] if t not in mine]
                sources.update({t: label for t in h['base_tests'] if t not in mine and t not in sources})
            chosen += [t for t in h['seen_before_cut'] if t not in mine]
            sources.update({t: CUT for t in h['seen_before_cut'] if t not in mine and t not in sources})
        if whole is not None:
            changed = [t for t in whole.changed_tests(everything) if t not in mine]
            chosen += changed
            sources.update({t: CHANGED for t in changed if t not in sources})
        if any(h['base_tests'] is None or (h['at_import'] and not h['base_tests']) for h in hunks) or not hunks:
            chosen += static
            used_static = bool(static)
            for t in static:
                sources.setdefault(t, STATIC)
        head_ids = list(dict.fromkeys([*own, *chosen]))
    sources.update({i: OWN for i in own})
    after_dir = Path(scratch)/'record-after'
    env = record.prepare(after_dir, trees['after'], head_watch, plan.get('targets_after') or [])
    found_a, problems_a, seconds_a, context = _chunks(execute, runner, python, trees['after'], head_ids, scratch, 'after',
                                                      budget, deadline, parts, env, WHOLE if mode == ALL else chunk)
    coverage_a, calls_a, skipped_a = record.read(after_dir, trees['after'], runner.cwd(trees['after']))
    # Candidates the code before the change has not run yet (chosen from a kept recording, or from the static graph):
    # run there too, so each outcome on the changed code has its partner.
    missing = [i for i in head_ids if i not in mine and i in found_a and i not in found_b]
    if missing:
        env = record.prepare(Path(scratch)/'record-before-2', trees['before'], base_watch, plan.get('targets_before') or [])
        more, said, took, _ = _chunks(execute, runner, python, trees['before'], missing, scratch, 'before-2',
                                      max(budget - seconds_b, budget * (1 - FILL)), deadline, parts, env, chunk)
        found_b.update(more)
        problems_b += [p for p in said if p not in problems_b]
        seconds_b += took
        cov2, calls2, _ = record.read(Path(scratch)/'record-before-2', trees['before'], runner.cwd(trees['before']))
        for test, files in cov2.items():
            for path, lines in files.items():
                coverage_b.setdefault(test, {}).setdefault(path, set()).update(lines)
        calls_b += calls2
    for h in hunks:
        h['head_tests'] = sorted(t for t in record.tests_on(coverage_a, h['path'], h['head']) if t != record.IMPORT)
        h['head_at_import'] = record.IMPORT in record.tests_on(coverage_a, h['path'], h['head'])
    paired = compare(execute, runner, python, trees, plan, scratch, deadline, parts, chunk, calls_b, calls_a,
                     {f'{t[0]}#{t[1]}' for t in targets_before} & {f'{t[0]}#{t[1]}' for t in targets_after}, base_watch,
                     head_watch, moved, second)
    cut = []
    if 'wall cap' in problems_a or ('wall cap' in problems_b and not found_a):
        cut.append(f'{sum(i not in found_a for i in head_ids)} of {len(head_ids)} candidate tests')
    if 'wall cap' in problems_b and base_ids is not None and not complete:
        cut.append(f'the code before the change: {sum(i not in found_b for i in base_ids)} of {len(base_ids)} tests')
    problems_a = [p for p in problems_a if p != 'wall cap']
    problems_b = [p for p in problems_b if p != 'wall cap']
    if exploring and not complete and not cut:
        gaps.append(f"the code before the change ran {len(found_b)} of {len(base_ids)} tests in its {budget} s: not the "
                    f"whole suite, so the static graph adds candidates for every hunk")
    static_fallback = mode != ALL and whole is None and used_static
    return dict(ids=head_ids, before=found_b, after=found_a, context=context, sources=sources, mode=mode, hunks=hunks,
                coverage=dict(before=coverage_b, after=coverage_a), calls=dict(before=calls_b, after=calls_a),
                skipped={**skipped_b, **skipped_a}, seconds=dict(before=seconds_b, after=seconds_a),
                problems=dict(before=problems_b, after=problems_a), base_complete=complete,
                base_planned=len(base_ids) if base_ids is not None else 0, gaps=gaps, cut=cut, paired=paired,
                targets=dict(before=targets_before, after=targets_after), static_fallback=static_fallback,
                suite=dict(commit=whole.sha, seconds=whole.seconds, complete=whole.complete,
                           changed_since=len(whole.changed or ())) if whole is not None else None)
