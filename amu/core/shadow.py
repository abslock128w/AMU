"""Shadow execution: run candidate tests against the code before and after the change, and compare.

Only runs when the caller asks for it. It executes the repository's own tests with the
interpreter the caller names, so it belongs to the developer working on their own tree.
"""
from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import os
import re
import shutil
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import basecache, equivalence, generate, isolate, mutate, replay, runners, sidebyside, trust
from .git import _git, file_at
from .patch import applied, candidate, original, renumber

CHUNK = 40
SIDE_BY_SIDE = 120  # seconds at most for the side-by-side run on the code before the change
EXTRAS_SECONDS = 120  # for each of mutation and equivalence, when turned on (build queue part 4)
ISOLATED = 3  # flips whose hunk is isolated
ISOLATE_SECONDS = 120  # per flip
CONFIRM = 20  # seconds always allowed for confirming a failure on the unchanged code
PARTS = max(1, min(4, (os.cpu_count() or 2) // 2))  # processes side by side, for runners that allow it
RATE = 15.0  # tests per second per process, until a run in this tree has measured it
RATES = 9  # measured rates kept; the estimate is their median, so one slow run does not set it
FILL = 0.8  # share of the budget the candidates are planned to fill; the rest is kept for the checks after them
IGNORED = ('.git', '.amu', '.amu-index', '.venv', 'venv', 'node_modules', '__pycache__', '.tox', '.nox',
           '.mypy_cache', '.pytest_cache', '.ruff_cache', 'dist', 'build', '*.egg-info', '.coverage', 'coverage.json')
# A process that stays (amu watch) sets a folder here: the copy of the tree is kept there between runs and brought up to
# date file by file, instead of being made afresh (2.2 s on Django, more than the rest of a warm run).
MIRROR = None
MIRROR_CHANGED = None  # paths, relative to the tree, that changed since the last run (amu watch's events); None: all
_mirror_state = dict(synced=False, written=set())


def _ignored(name):
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in IGNORED)


def _refresh(root, mirror, paths):
    """Copy each of ``paths`` from ``root`` into ``mirror`` again, or take it away when ``root`` no longer has it."""
    for path in paths:
        source, target = Path(root)/path, Path(mirror)/path
        if any(_ignored(part) for part in Path(path).parts):
            continue
        target.unlink(missing_ok=True)
        if source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def _sync(root, mirror):
    """Make ``mirror`` a copy of ``root`` again, copying only files whose size or modification time differ."""
    root, mirror, seen = Path(root), Path(mirror), set()
    for folder, names, files in os.walk(root):
        names[:] = [n for n in names if not _ignored(n)]
        relative = Path(folder).relative_to(root)
        (mirror/relative).mkdir(parents=True, exist_ok=True)
        for name in files:
            if _ignored(name):
                continue
            source, target = Path(folder)/name, mirror/relative/name
            seen.add(str(relative/name))
            try:
                have, want = target.lstat(), source.lstat()
                if (have.st_size, have.st_mtime_ns) == (want.st_size, want.st_mtime_ns):
                    continue
                target.unlink()
            except FileNotFoundError:
                pass
            if source.is_symlink():
                target.symlink_to(os.readlink(source))
            else:
                shutil.copy2(source, target)
    for folder, names, files in os.walk(mirror):
        relative = Path(folder).relative_to(mirror)
        for name in files:
            if str(relative/name) not in seen:
                (Path(folder)/name).unlink(missing_ok=True)


def parent_key(root):
    """Identity of the code before the change: the commit, plus any uncommitted change outside .py files,
    which the before side shares with the after side. None outside a git repository."""
    try:
        head = _git(root, 'rev-parse', 'HEAD').strip()
        other = [line for line in _git(root, 'status', '--porcelain').splitlines() if not line.rstrip().endswith(('.py', '.pyi'))]
    except OSError:
        return None
    return hashlib.sha256('\n'.join([head, *sorted(other)]).encode()).hexdigest()[:16]


def load_memo(memo, key):
    """The memo for the code before the change ``key``. Outcomes before the change hold only for the same key; the
    measured rates, per-test durations and when each test last flipped carry over to any key. ``rate`` is the median of
    the last measured rates."""
    try:
        data = json.loads(Path(memo).read_text())
    except (OSError, ValueError, TypeError):
        return dict(key=key, before={}, rate=None, rates=[], durations={}, flips={})
    # A memo written before rates were kept holds one rate.
    rates = data.get('rates') if isinstance(data.get('rates'), list) and data.get('rates') else [data.get('rate')]
    rates = [rate for rate in rates if isinstance(rate, (int, float)) and rate > 0][-RATES:]
    kept = dict(rate=_median(rates), rates=rates, durations=data.get('durations') or {}, flips=data.get('flips') or {})
    return dict(data, **kept) if data.get('key') == key else dict(key=key, before={}, **kept)


def _median(rates):
    return round(statistics.median(rates), 2) if rates else None


def history(memo):
    """({id: when it last flipped}, {id: seconds}) from earlier runs in this tree; empty when there were none."""
    try:
        data = json.loads(Path(memo).read_text())
    except (OSError, ValueError, TypeError):
        return {}, {}
    return data.get('flips') or {}, data.get('durations') or {}


def execute(runner, python, tree, ids, scratch, name, timeout, parts):
    """runners.execute over ``parts`` processes side by side. Returns ({id: state}, [problems])."""
    slices = [ids[i::parts] for i in range(parts)] if parts > 1 else [ids]
    slices = [part for part in slices if part]
    with ThreadPoolExecutor(len(slices)) as pool:
        results = list(pool.map(lambda pair: runners.execute(runner, python, tree, pair[1], Path(scratch)/f'{name}-{pair[0]}.xml', timeout),
                                enumerate(slices)))
    found, problems = {}, []
    for got, problem in results:
        found.update(got)
        if problem and problem not in problems:
            problems.append(problem)
    return found, problems


def within(seconds, deadline):
    """``seconds``, cut to what is left before ``deadline`` (a time.monotonic() value); unchanged without one."""
    return seconds if deadline is None else min(seconds, deadline - time.monotonic())


def late(deadline):
    """Whether ``deadline`` (a time.monotonic() value) has passed."""
    return deadline is not None and time.monotonic() >= deadline


def run(root, edits, ids, python=None, budget=60, runner=None, memo=None, own=(), base_cache=None, functions=(), generate_with=None,
        merge_base=None, deadline=None, extras=(), prefer=()):
    """Outcomes of ``ids`` before and after the change. Returns (rows, info, gaps). ``prefer``: tests seen running a
    changed line, run first by side-by-side.

    ``own`` are the tests the change adds or edits (replay.touched); they are also run on the code before the change
    with the tests as the change leaves them, and their verdicts go to ``info['replayed']``.

    ``functions`` are the changed functions (analyze's changed_functions): with Python 3.12 or later, the tests that ran
    run once more on the code before the change while each changed function is executed side by side on the inputs
    they call it with (sidebyside); stable differences go to ``info['behaviour']``.

    ``generate_with`` (dollars, minutes, model[, cli]): changed functions the tests never called get model-written probes, kept
    only on execution evidence (generate); results go to ``info['generated']``.

    ``base_cache`` (basecache.BaseCache) answers passing outcomes on the code before the change from earlier runs;
    only what it cannot answer runs there.

    ``memo`` is a file kept between runs on the same tree: outcomes already seen on the code before the change
    (valid while that code is the same) and the measured test rates, whose median plans how many candidates are tried
    first. Time the plan leaves over goes to the next candidates.

    ``merge_base`` is the commit before the change when the tree is its head (a pull request): a file the patch cannot
    be undone in is taken as that commit holds it. A file neither the patch nor the merge base gives stays as the tree
    holds it on both sides, and a gap names it; the rest of the change is still compared.

    ``deadline`` (a time.monotonic() value: CI's wall cap, less time to report) cuts every run at it, and no step starts
    past it. The steps run in order of what they find: flips, the change's own tests, then isolation, side-by-side and
    generated probes. What the deadline left undone or cut short goes to ``info['cut']``."""
    python, gaps, started, cut = python or sys.executable, [], time.perf_counter(), []
    runner = runners.get(root, runner)
    if not ids:
        return [], dict(ran=0, seconds=0.0, runner=runner.name), gaps
    if late(deadline):
        # Not even the copy of the tree: on a large project it takes seconds the report needs.
        cut.append(f'{len(ids)} of {len(ids)} candidate tests')
        return [], dict(ran=0, seconds=0.0, runner=runner.name, candidates=len(ids), cut=cut), gaps
    parts = PARTS if runner.parallel else 1
    seeded, order = trust.ordering(runner, python)
    runner.extra = (*runners.plugin_args(runner, python), *seeded)
    key = parent_key(root) if memo else None
    remembered = load_memo(memo, key) if key else dict(before={}, rate=None, rates=[], durations={}, flips={})
    rate = remembered.get('rate') or RATE
    pool, ids = ids, ids[:max(CHUNK, int(rate * parts * budget * FILL))]
    with tempfile.TemporaryDirectory(prefix='amu-shadow-') as scratch:
        if MIRROR is not None:
            other = Path(MIRROR)
            if _mirror_state['synced'] and MIRROR_CHANGED is not None:
                # Only what changed since, and what the last run wrote into the copy, needs copying again.
                _refresh(root, other, set(MIRROR_CHANGED) | _mirror_state['written'] | set(edits))
            else:
                _sync(root, other)
            _mirror_state.update(synced=True, written=set(edits))
        else:
            other = Path(scratch)/'tree'
            shutil.copytree(root, other, ignore=shutil.ignore_patterns(*IGNORED), symlinks=True)
        on_disk, before_src, after_src, unbuilt = {}, {}, {}, []
        for path, edit in edits.items():
            on_disk[path] = applied(root, path, edit)
            # With the merge base the tree is the change's head: a file not taken for the head was misjudged, and the
            # patch is not applied forward onto it.
            source = original(root, path, edit) if on_disk[path] else None if merge_base else candidate(root, path, edit)
            if source is None and merge_base:
                # The tree is the change's head: the file before the change is the one the merge base holds.
                source, on_disk[path] = file_at(root, merge_base, path), True
            if source is None:
                unbuilt.append(path)
                del on_disk[path]
                continue
            (other/path).parent.mkdir(parents=True, exist_ok=True)
            (other/path).write_text(source)
            live = _read(root/path)
            before_src[path], after_src[path] = (source, live) if on_disk[path] else (live, source)
        if unbuilt:
            # The copy keeps such a file as the tree holds it, so both sides run the same file: its change is left out
            # of the comparison, and the rest of the change is not.
            gaps += [f'{path}: other side of the change could not be built; both sides run it as the tree holds it, so its '
                     'change is not compared' for path in unbuilt]
            edits = {path: edit for path, edit in edits.items() if path not in unbuilt}
            key = None  # the copy is not the code before the change: what runs on it is not remembered for that code
            functions = [row for row in functions if row['file'] not in unbuilt]
            if not edits:
                return [], dict(ran=0, seconds=0.0, runner=runner.name), [*gaps, 'no changed file could be built on both sides; nothing executed']
        # Whichever side the working tree holds, the copy holds the other one.
        live_is_after = any(on_disk.values())
        trees = dict(after=root if live_is_after else other, before=other if live_is_after else root)
        states = dict(after={}, before={})
        ready = round(time.perf_counter() - started, 2)  # the copy is made: what comes next runs tests
        context, from_memo = {}, set()
        # Most promising candidates first, in chunks. Each chunk runs on the changed code; only the tests
        # that fail there are run on the code as it was, to confirm they passed before. A test that passes
        # after the change cannot be a new failure, so the unchanged side is never paid for it.
        left, done, aborted, measured, measured_seconds, pace = float(budget), 0, None, 0, 0.0, rate
        first_flip = None  # seconds into the run when the first flip was confirmed (time to first difference)
        stopped, struck = None, {}
        while left > 1:
            if done == len(ids):
                # The planned candidates finished with time to spare: the next ones run a chunk at a time, as many as the
                # last chunk's pace fits in what is left above the share kept for the checks after them. Candidates come
                # shortest first (select.order), so the pace of the whole run would fit too many.
                rate = measured / measured_seconds / parts if measured and measured_seconds else rate
                ids = pool[:done + min(CHUNK * parts, max(0, int(pace * parts * (left - budget * (1 - FILL)))))]
                if done == len(ids):
                    break
            start = done
            chunk = ids[start:start + CHUNK * parts]
            if late(deadline):
                break
            began = time.perf_counter()
            found, problems = execute(runner, python, trees['after'], chunk, scratch, 'after', within(max(left, 1), deadline), parts)
            took = time.perf_counter() - began
            left -= took
            states['after'].update(found)
            for part in _slices(chunk, parts):
                context.update({ident: part for ident in part})
            problem = next((p for p in problems if p != 'timed out'), problems[0] if problems else None)
            # A chunk the runner aborts on does not end the run: the next chunk holds other candidates.
            if problem and problem != 'timed out':
                # Candidates the change removed (a test renamed or deleted in an edited file) cannot run after it; that
                # is not the runner aborting. Only candidates that may still exist on the changed side count. Labels of
                # a group the runner refuses as a whole (runners.refusal) are not lost either: they have their own gap.
                present = [i for i in chunk if not _removed(i, edits, before_src, trees['after'])]
                refused = runners.refusals(runner, trees['after'])
                lost = [i for i in present if found.get(i) in (None, 'unrunnable') and runner.group(i) not in refused]
                if lost:
                    for text in problems:
                        gap = f'shadow run (after the change, {runner.name}): {text}'
                        if text != 'timed out' and text.removeprefix('no outcomes read: ') not in refused.values() and gap not in gaps:
                            gaps.append(gap)
                if present and not any(state != 'unrunnable' for state in states['after'].values()):
                    if lost and problem in struck:
                        # Nothing has run on either side, and another chunk fails as the first did: the runner runs
                        # nothing here, whatever the candidates. Restarting it for every chunk would only use the budget.
                        stopped = (f'shadow run stopped after {start + len(chunk)} of {len(ids)} candidates: {runner.name} ran none of them, '
                                   f'nor the first {struck[problem]} on the code before the change ({problem})')
                        break
                    # Nothing ran on the changed code. If the same candidates run on the code before the change,
                    # the change is what stops the runner: that is a failure of the change, not a gap.
                    began = time.perf_counter()
                    probe, again = runners.execute(runner, python, trees['before'], present, Path(scratch)/'probe.xml',
                                                   within(max(left, CONFIRM), deadline))
                    left -= time.perf_counter() - began
                    ran_before = sum(state != 'unrunnable' for state in probe.values())
                    if ran_before:
                        aborted = dict(error=problem.removeprefix('no outcomes read: '), ran_before=ran_before, probed=len(present))
                        break
                    if lost:
                        struck[problem] = len(present)
            if 'timed out' in problems:
                # A slice cut short ends the run, whatever another slice of the chunk met.
                break
            # The rate counts only chunks that finished: a chunk stopped at the budget took its time and left few
            # outcomes or none.
            if 'timed out' not in problems:
                counted = sum(found.get(ident) in OUTCOMES for ident in chunk)
                measured, measured_seconds = measured + counted, measured_seconds + took
                pace = counted / took / parts if counted and took > 0 else pace
            failing = [ident for ident in chunk if found.get(ident) == 'failed']
            # The code before the change is the same on every run until the commit moves: what it did then, it does now.
            remembered_now = {ident: remembered['before'][ident] for ident in failing if ident in remembered['before']}
            states['before'].update(remembered_now)
            from_memo.update(remembered_now)
            unknown = [ident for ident in failing if ident not in remembered['before']]
            if unknown:
                began = time.perf_counter()
                confirmed, problem = _base(base_cache, runner, python, trees['before'], unknown, Path(scratch)/'before.xml',
                                           within(max(left, CONFIRM), deadline))
                left -= time.perf_counter() - began
                states['before'].update(confirmed)
                remembered['before'].update({ident: state for ident, state in confirmed.items() if state != 'unrunnable'})
                if problem:
                    gaps.append(f'shadow run (before the change, {runner.name}): {problem}')
                if late(deadline) and any(ident not in confirmed for ident in unknown):
                    cut.append(f'confirming {sum(ident not in confirmed for ident in unknown)} failure(s) on the code before the change')
            if first_flip is None and any(_flip(states['before'].get(i), states['after'].get(i)) for i in failing):
                first_flip = round(time.perf_counter() - started, 1)
            done = start + len(chunk)
        if len(ids) < len(pool):
            gaps.insert(0, f'{len(pool) - len(ids)} lower-ranked candidate(s) not run: about {rate:.0f} tests/s per process fit {len(ids)} in the {budget} s allowed')
        if stopped:
            gaps.append(stopped)
        elif done < len(ids) and not aborted and late(deadline):
            cut.append(f'{len(ids) - done} of {len(ids)} candidate tests')
        elif done < len(ids) and not aborted:
            gaps.append(f'shadow run stopped at its {budget} s budget: {done} of {len(ids)} candidates run')
        # A test that an edited test file no longer defines (renamed or deleted) did not fail because of the change: it
        # is gone. Its original form is checked below like any other edited test.
        removed = [i for i in ids if i in states['after'] and _removed(i, edits, before_src, trees['after'])]
        for ident in removed:
            states['after'][ident] = 'removed'
        unstable, edited_flips, originals, replayed, verdicts, edited_verdicts, trust_seconds = [], [], {}, [], {}, {}, 0.0
        side, behaviour, made, generated, mutation, equivalent = None, [], None, [], None, None
        if not aborted:
            # A flip is believed only after the trust checks (trust module): the reach check and three reruns on the
            # changed code. A "before" outcome taken from the memo is confirmed afresh, so a test that is flaky on the
            # unchanged code is not a flip. Nothing is dropped: every flip leaves with a label.
            flipped = [i for i in ids if _flip(states['before'].get(i), states['after'].get(i)) and states['after'][i] == 'failed']
            if flipped:
                ran = [i for i in ids if states['after'].get(i) in OUTCOMES]
                changed = trust.changed_lines(trees['after'], edits, _after_lines)
                began = time.perf_counter()
                verdicts, more = trust.triad(runner, python, trees['after'], flipped, ran, scratch, max(left, CONFIRM), changed,
                                             before_tree=trees['before'], context=context, deadline=deadline)
                # Verifying a flip is not charged to the budget for finding flips: otherwise the reruns could leave no time
                # for the edited-test check below, and flips it would find would go unreported.
                trust_seconds += time.perf_counter() - began
                gaps += more
                if late(deadline) and any(None in verdict['reruns'].values() for verdict in verdicts.values()):
                    cut.append('reruns of flips')
                remembered_flips = [i for i in flipped if i in from_memo and verdicts[i]['label'] != trust.UNSTABLE]
                if remembered_flips:
                    began = time.perf_counter()
                    fresh, _ = _base(base_cache, runner, python, trees['before'], remembered_flips, Path(scratch)/'before-fresh.xml',
                                     within(max(left, CONFIRM), deadline))
                    left -= time.perf_counter() - began
                    for i in remembered_flips:
                        if fresh.get(i) in OUTCOMES and fresh[i] != states['before'][i]:
                            verdicts[i].update(label=trust.UNSTABLE, why='passed and failed on the code before the change')
                unstable += [i for i in flipped if verdicts[i]['label'] == trust.UNSTABLE]
            # A test file the change edited: its tests as they were are also run against the changed code. A test that
            # passed before and fails in its original form on the changed code, but passes, is skipped or is gone as
            # edited, was dealt with by changing the test, not the code: "test edited, not fixed". Only tests the file
            # defined before the edit can run in their original form.
            edited = {path for path in edits if before_src.get(path) and any(_path(i) == path for i in ids)}
            checked = [i for i in ids if _path(i) in edited and states['after'].get(i) in ('passed', 'skipped', 'unrunnable', 'removed')
                       and _existed(i, before_src, trees['after'])]
            # These are tests the change touches, and every test the change touches runs (select): the check gets at least
            # the confirmation allowance even when the candidates used up the budget.
            if checked and late(deadline):
                cut.append('tests of edited files as they were before the edit')
            elif checked:
                # The copy holds one side of every changed file; overwrite it into "changed code, tests as they were".
                for path in edits:
                    _write(other/path, before_src[path] if path in edited else after_src[path])
                failing = []
                try:
                    began = time.perf_counter()
                    originals, problems = execute(runner, python, other, checked, scratch, 'original',
                                                  within(max(left, CONFIRM), deadline), parts)
                    left -= time.perf_counter() - began
                    if 'timed out' in problems and late(deadline):
                        cut.append('tests of edited files as they were before the edit, cut short')
                    failing = [i for i in checked if originals.get(i) == 'failed']
                    if failing:
                        # The same trust checks as any flip, on the changed code with the tests as they were. Reach
                        # counts production lines only: the edited test files are in their earlier form here.
                        changed = trust.changed_lines(other, {p: e for p, e in edits.items() if p not in edited}, _after_lines)
                        began = time.perf_counter()
                        edited_verdicts, more = trust.triad(runner, python, other, failing, checked, scratch, max(left, CONFIRM), changed,
                                                            deadline=deadline)
                        trust_seconds += time.perf_counter() - began
                        gaps += more
                        if late(deadline) and any(None in verdict['reruns'].values() for verdict in edited_verdicts.values()):
                            cut.append('reruns of flips')
                    for problem in problems:
                        gaps.append(f'shadow run (original tests on the changed code, {runner.name}): {problem}')
                finally:
                    for path in edits:
                        _write(other/path, before_src[path] if live_is_after else after_src[path])
                # The before side is run afresh here, not taken from the memo: these become failures on its word alone.
                known = {}
                unknown = list(failing)
                if unknown:
                    began = time.perf_counter()
                    confirmed, problem = _base(base_cache, runner, python, trees['before'], unknown, Path(scratch)/'before-original.xml',
                                               within(max(left, CONFIRM), deadline))
                    left -= time.perf_counter() - began
                    known.update(confirmed)
                    remembered['before'].update({i: state for i, state in confirmed.items() if state != 'unrunnable'})
                    if problem:
                        gaps.append(f'shadow run (before the change, original tests, {runner.name}): {problem}')
                    if late(deadline) and any(i not in confirmed for i in unknown):
                        cut.append(f'confirming {sum(i not in confirmed for i in unknown)} failure(s) of edited tests as they were '
                                   'on the code before the change')
                edited_flips = [i for i in failing if known.get(i) == 'passed']
                unstable += [i for i in edited_flips if edited_verdicts.get(i, {}).get('label') == trust.UNSTABLE]
        # The change's own tests go before the steps that only add to what was found, so a deadline cuts those first.
        if own and not aborted and late(deadline):
            cut.append("the change's own tests on the code before it")
        elif own and not aborted:
            replayed, more, unfinished = _replay(runner, python, trees['after'], other, edits, before_src, after_src, live_is_after,
                                                 own, states['after'], scratch, left, base_cache, deadline)
            gaps += more
            if unfinished:
                cut.append(f"the change's own tests on the code before it, {len(unfinished)} of {len(set(own))} left without a result")
        if not aborted:
            # Which hunk makes each counted flip fail (isolate): delta debugging on a copy of the code before the change.
            counted = [i for i in ids if i in verdicts and trust.counted(verdicts[i]['label'])][:ISOLATED]
            for ident in counted:
                if late(deadline):
                    cut.append('flip isolation')
                    break
                began = time.perf_counter()
                found = isolate.run(runner, python, trees['before'], edits, before_src, ident, scratch,
                                    within(ISOLATE_SECONDS, deadline))
                trust_seconds += time.perf_counter() - began
                if found:
                    verdicts[ident]['isolated'] = found
        if functions and not aborted and _monitoring(python) and late(deadline):
            if any(states['after'].get(i) in OUTCOMES for i in ids if i not in set(own)):
                cut.append('side-by-side')
        elif functions and not aborted and _monitoring(python):
            spec_targets, skipped = sidebyside.targets(functions, before_src, after_src)
            # Not the change's own tests: this run uses the tests as they were, where an added test does not exist and
            # an unknown label makes the runner reject the whole batch.
            mine = set(own)
            ran_ids = [i for i in ids if states['after'].get(i) in OUTCOMES and i not in mine]
            began = time.perf_counter()
            added = [a for path in edits if path.endswith('.py') and not replay.is_test_file(path)
                     for a in sidebyside.additions(before_src.get(path) or '', after_src.get(path) or '', path)]
            # Production modules the change adds as new files (nothing on the code before the change).
            from .index import module_name
            modules = [dict(name=module_name(path), file=path, source=after_src[path]) for path in edits
                       if path.endswith('.py') and not replay.is_test_file(path) and not before_src.get(path) and after_src.get(path)]
            side, behaviour, more = sidebyside.run(runner, python, trees['before'], ran_ids, spec_targets, skipped, scratch,
                                                   within(max(min(budget, SIDE_BY_SIDE), CONFIRM), deadline), added, modules,
                                                   prefer=prefer)
            side['seconds'] = round(time.perf_counter() - began, 1)
            gaps += more
            if late(deadline):
                cut.append('side-by-side, cut short')
        wanted = generate_with and (generate_with[0] > 0 or generate_with[3:4] == (True,))
        if functions and not aborted and wanted and late(deadline):
            cut.append('generated probes')
        elif functions and not aborted and wanted:
            spec_targets, left_out = sidebyside.targets(functions, before_src, after_src)
            called = set((side or {}).get('called') or ())
            uncalled = [t for t in spec_targets if t['id'] not in called]
            example = next((_read(Path(trees['after'])/_path(i)) for i in ids if _path(i).endswith('.py')), '')
            changed = {path: _after_lines(path, edit) for path, edit in edits.items()
                       if path.endswith('.py') and not replay.is_test_file(path)}
            dollars, minutes, model, *cli = generate_with
            # The code before the change is the working tree itself when the change is not on disk; it is copied then,
            # never written to.
            made, generated, more = generate.run(runner, python, trees['after'], trees['before'], uncalled, changed, example,
                                                 scratch, dollars, within(minutes * 60, deadline) / 60, model,
                                                 copy_before=not live_is_after, cli=bool(cli and cli[0]))
            if side is None:
                # Side-by-side did not run (Python before 3.12), so its list of the changed functions left out, and why,
                # is not in the report: it goes with the generated tests, which got the same targets.
                made['not_compared'] = left_out
            gaps += more
        flagged = [extra for extra in ('mutation', 'equivalence') if extra in extras]
        if functions and not aborted and flagged and late(deadline):
            cut.append(' and '.join(flagged))
        elif not aborted and flagged:
            production = [path for path in edits if path.endswith('.py') and not replay.is_test_file(path) and after_src.get(path)]

            def put(path, text):
                if text is None:
                    (other/path).unlink(missing_ok=True)
                else:
                    _write(other/path, text)
            # The copy becomes the changed code for these runs, and is put back after.
            for path in edits:
                put(path, after_src.get(path))
            try:
                if 'mutation' in flagged:
                    passing = [i for i in ids if states['after'].get(i) == 'passed' and i not in set(own)]
                    mutation, more = mutate.run(runner, python, other, {p: after_src[p] for p in production},
                                                {p: _after_lines(p, edits[p]) for p in production}, passing, scratch,
                                                within(EXTRAS_SECONDS, deadline), put)
                    gaps += more
                if 'equivalence' in flagged:
                    spec_targets, _ = sidebyside.targets(functions, before_src, after_src)
                    began_equivalence = time.monotonic()
                    equivalent, equivalent_rows, more = equivalence.run(
                        python, other, spec_targets, before_src, after_src, scratch,
                        left=lambda: within(EXTRAS_SECONDS, deadline) - (time.monotonic() - began_equivalence))
                    equivalent['rows'] = equivalent_rows
                    gaps += more
            finally:
                for path in edits:
                    put(path, before_src.get(path) if live_is_after else after_src.get(path))
    ran_after = sum(state in OUTCOMES for state in states['after'].values())
    remembered.setdefault('durations', {}).update(runner.durations or {})
    now = round(time.time())
    remembered.setdefault('flips', {}).update({i: now for i, v in verdicts.items() if trust.counted(v['label'])})
    if key and memo:
        if measured >= CHUNK and measured_seconds > 0:
            remembered['rates'] = [*remembered.get('rates', []), round(measured / measured_seconds / parts, 2)][-RATES:]
            remembered['rate'] = _median(remembered['rates'])
        try:
            Path(memo).parent.mkdir(parents=True, exist_ok=True)
            Path(memo).write_text(json.dumps(remembered))
        except OSError:
            pass
    rows, unstable = [], sorted(set(unstable))
    for ident in ids:
        before, after = states['before'].get(ident), states['after'].get(ident)
        if ident in verdicts:
            rows.append(dict(id=ident, before=before, after=after, label='executed', trust=verdicts[ident]['label'],
                             why=verdicts[ident]['why'], reruns=verdicts[ident]['reruns'], reach=verdicts[ident]['reach'],
                             **{k: verdicts[ident][k] for k in ('path', 'raised', 'isolated') if k in verdicts[ident]}))
        elif ident in edited_flips:
            verdict = edited_verdicts.get(ident) or dict(label=trust.CONFIRMED, why='', reruns=None, reach=None)
            rows.append(dict(id=ident, before='passed', after='failed', label=EDITED, edited_form=after, trust=verdict['label'],
                             why=('as the test was before the edit: ' + verdict['why']).rstrip(': '), reruns=verdict['reruns'],
                             reach=verdict['reach']))
        elif ident in unstable:
            continue
        elif before in OUTCOMES and after in OUTCOMES and before != after:
            rows.append(dict(id=ident, before=before, after=after, label='executed'))
    if unstable:
        gaps.append(f'{len(unstable)} test(s) flipped and did not fail again in every rerun; reported as unstable, not as failures: ' + ', '.join(unstable[:3])
                    + (f' and {len(unstable) - 3} more' if len(unstable) > 3 else ''))
    labels = {**{i: v['label'] for i, v in verdicts.items()}, **{i: edited_verdicts[i]['label'] for i in edited_flips if i in edited_verdicts}}
    unreached = [i for i in ids if labels.get(i) == trust.NOT_CAUSED]
    if removed:
        gaps.append(f'{len(removed)} candidate(s) no longer defined after the change (renamed or deleted in an edited test file)')
    unrunnable = sorted(ident for ident, state in states['after'].items() if state == 'unrunnable')
    # A label group the runner refuses as a whole (runners.refusal): dropped, and named with what the runner said.
    refused, groups = runners.refusals(runner, trees['after']), {}
    for ident in unrunnable:
        groups.setdefault(runner.group(ident), []).append(ident)
    for group in sorted(set(groups) & set(refused)):
        gaps.append(f'{len(groups[group])} candidate(s) in {group} not run: {runner.name} refuses them before any test runs: {refused[group]}')
    other = [ident for ident in unrunnable if runner.group(ident) not in refused]
    if other:
        gaps.append(f'{len(other)} candidate(s) could not be run by {runner.name} and are not covered by this result')
    info = dict(seconds_before_running=ready, ran=ran_after, candidates=len(ids), unrunnable=len(unrunnable), outcomes=states['after'], seconds=round(time.perf_counter() - started, 1), runner=runner.name)
    if aborted:
        info['aborted'] = aborted
    if unstable:
        info['unstable'] = unstable
    if unreached:
        info['not_caused'] = unreached
    info['order'] = order
    if not aborted and trust_seconds:
        info['trust_seconds'] = round(trust_seconds, 1)
    if base_cache is not None:
        info['base_cache'] = dict(answered=base_cache.hits, ran=base_cache.misses, stored=base_cache.stored)
    if replayed:
        info['replayed'] = replayed
    if not aborted and side is not None:
        info['side_by_side'], info['behaviour'] = side, behaviour
    if not aborted and made is not None:
        info['generated'], info['generated_differences'] = made, generated
    if originals:
        info['original_tests'] = {i: state for i, state in originals.items() if state != 'unrunnable'}
        info['ran_original'] = len(info['original_tests'])
    if cut:
        info['cut'] = list(dict.fromkeys(cut))
    info['first_flip_seconds'] = first_flip
    if mutation is not None:
        info['mutation'] = mutation
    if equivalent is not None:
        info['equivalence'] = equivalent
    return rows, info, gaps


EDITED = 'test edited, not fixed'


def _monitoring(python):
    from .edges import supported
    return supported(python)


def _base(cache, runner, python, tree, ids, report, timeout, texts=None):
    """runners.execute on the code before the change, through the cache of passing outcomes when there is one.
    ``texts`` gets what the runner printed about each test that did not pass (runners.execute)."""
    return basecache.execute(cache, lambda t, i, *rest: runners.execute(runner, python, t, i, *rest, texts=texts), tree, ids,
                             report, timeout)


def _after_lines(path, edit):
    """Changed lines of one file, numbered as on the changed side: added lines, and where removed lines' replacements
    landed."""
    place = renumber(edit)
    return set(edit['new']) | {place(line) for line in edit['old']}


def _replay(runner, python, after_tree, other, edits, before_src, after_src, live_is_after, own, after_states, scratch, left, base_cache=None,
            deadline=None):
    """The change's own tests on the code before it, with the tests as the change leaves them (replay module).
    Returns ([{id, head, base, verdict}], gaps, [ids the deadline left without a verdict]). The copy ``other`` is put
    back as it was."""
    gaps, unfinished = [], []
    own = list(dict.fromkeys(own))
    missing = [i for i in own if after_states.get(i) not in OUTCOMES]
    if missing:
        found, problem = runners.execute(runner, python, after_tree, missing, Path(scratch)/'own-head.xml',
                                         within(max(left, CONFIRM), deadline))
        after_states.update({i: s for i, s in found.items() if i in missing})
        if problem:
            gaps.append(f"replaying the change's own tests (after the change, {runner.name}): {problem}")
        unfinished = [i for i in missing if i not in found] if late(deadline) else []
    runnable = [i for i in own if after_states.get(i) in OUTCOMES]
    if not runnable:
        return [], gaps, unfinished
    for path in edits:
        _write(other/path, after_src[path] if replay.is_test_file(path) else before_src[path])
    try:
        said = {}
        base, problem = _base(base_cache, runner, python, other, runnable, Path(scratch)/'own-base.xml',
                              within(max(left, CONFIRM), deadline), texts=said)
        # A failure on the code before the change is the evidence; it must repeat in a fresh process to count.
        failing = [i for i in runnable if base.get(i) in ('failed', 'unrunnable') and after_states[i] == 'passed']
        again = runners.execute(runner, python, other, failing, Path(scratch)/'own-base-again.xml',
                                within(max(left, CONFIRM), deadline))[0] if failing else {}
    finally:
        for path in edits:
            _write(other/path, before_src[path] if live_is_after else after_src[path])
    if problem and not any(base.get(i) in OUTCOMES for i in runnable):
        gaps.append(f"replaying the change's own tests (before the change, {runner.name}): {problem}")
    # The deadline can stop the run on the code before the change part way (the cache answers the rest), or the rerun a
    # failure there needs to be evidence: those tests have no verdict, and are named rather than dropped.
    if late(deadline):
        unfinished += [i for i in runnable if i not in base or (i in failing and i not in again)]
    rows = []
    # What the change adds outside its tests: a test that fails before the change only because that is not there yet
    # (ImportError, AttributeError or NameError on it) shows the new code exists, not what it does.
    added = replay.added_names({path: (before_src.get(path), after_src.get(path)) for path in edits})
    for ident in runnable:
        if ident in unfinished:
            continue
        head, was = after_states[ident], base.get(ident)
        if was == 'unrunnable' and head == 'passed':
            # Runs after the change and does not even load before it (an import of what the change adds).
            was = 'unloadable'
        if ident in again and again[ident] == 'passed':
            rows.append(dict(id=ident, head=head, base=was, verdict='unstable', why='failed on the code before the change once, passed on a rerun'))
            continue
        verdict = replay.classify(head, was)
        if verdict is None:
            continue
        row = dict(id=ident, head=head, base=was, verdict=verdict)
        if verdict == 'evidence':
            text = said.get(ident) or {}
            missing = replay.new_code_error(text.get('text'), added)
            if missing and not text.get('own') and not (replay.mentions(text.get('text'), _path(ident))
                                                        and replay.references(after_src.get(_path(ident)), missing[1])):
                missing = None  # the run's output names it, but not for this test's file
            if missing:
                row.update(verdict='new_code', why=f'{missing[0]} on {missing[1]}')
            elif was == 'unloadable':
                row.update(verdict='unloadable', why='does not load on the code before the change; the cause was not read')
        rows.append(row)
    return rows, gaps, unfinished
OUTCOMES = ('passed', 'failed', 'skipped')


def _path(ident):
    return ident.split('::')[0]


def _slices(ids, parts):
    """The batches execute() gives each process, so a test can be rerun among the tests it first ran with."""
    return [part for part in ([ids[i::parts] for i in range(parts)] if parts > 1 else [ids]) if part]


def _removed(ident, edits, before_src, after_tree):
    """A test the change took away: its class (or module) defined it directly before the edit and does not now, or its
    class is gone. A test the class only inherits cannot be taken away by editing this file."""
    path = _path(ident)
    if path not in edits:
        return False
    if not (_direct(before_src.get(path, ''), ident) is True and _direct(_read(Path(after_tree)/path), ident) is False):
        return False
    # Moved up into a base class in another edited file: still inherited, not removed. Only a method (an indented def)
    # can be inherited; a module-level test is not.
    if len(ident.split('::')) < 3:
        return True
    name = ident.split('::')[-1]
    pattern = re.compile(r'^[ \t]+(async\s+)?def\s+' + re.escape(name) + r'\b', re.M)
    return not any(pattern.search(_read(Path(after_tree)/other)) for other in edits if other != path and other.endswith('.py'))


def _existed(ident, before_src, after_tree):
    """Whether a test from an edited file can run in the file's form before the edit: defined there directly, or
    inherited both before and after (a method the edit added to a class with bases did not exist before)."""
    before = _direct(before_src.get(_path(ident), ''), ident)
    if before is True:
        return True
    return before is False and _direct(_read(Path(after_tree)/_path(ident)), ident) is False and _defined(before_src.get(_path(ident), ''), ident, True) is True


def _direct(text, ident):
    """Whether ``text`` defines the test directly in its class (or module): True, False, or None when it does not parse."""
    if not text:
        return False
    try:
        body = ast.parse(text).body
    except SyntaxError:
        return None
    *classes, name = ident.split('::')[1:] or ['']
    for cls in classes:
        owner = next((node for node in body if isinstance(node, ast.ClassDef) and node.name == cls), None)
        if owner is None:
            return False
        body = owner.body
    return any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name for node in body)


def _defined(text, ident, edited):
    """Whether the test ``ident`` (path::Class::test) is defined in ``text``: True, False, or None when unknown.

    A file the change did not edit cannot have lost a test. A class that still exists and has base classes may inherit
    the method from elsewhere, so it counts as defined. A file that does not parse is unknown."""
    if not edited:
        return True
    if not text:
        return False
    try:
        body = ast.parse(text).body
    except SyntaxError:
        return None
    *classes, name = ident.split('::')[1:] or ['']
    owner = None
    for cls in classes:
        owner = next((node for node in body if isinstance(node, ast.ClassDef) and node.name == cls), None)
        if owner is None:
            return False
        body = owner.body
    if any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name for node in body):
        return True
    return bool(owner is not None and owner.bases)


def _flip(before, after):
    return before in OUTCOMES and after in OUTCOMES and before != after


def _read(path):
    try:
        return Path(path).read_text()
    except (OSError, UnicodeError):
        return ''


def _write(path, text):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text)
