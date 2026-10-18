"""Whether a flip is believed: seeded order, a reach check and three reruns on the changed code.

A test that passed before the change and fails after it is reported as a failure only when:
- both sides ran in the same order (pytest-randomly with a fixed seed when the project has it, else the same explicit
  order on both sides, with a fixed hash seed);
- it executed at least one changed line on the changed side (the reach check); a flip that did not is labelled
  ``not caused by this change`` with the reason;
- it fails again in all three reruns on the changed side: in the same process right after itself, alone in a fresh
  process, and after the tests that ran before it in its batch.
A flip that fails in some of those orders and passes in others is order-dependent. When the code before the change
passes in every order it failed in on the changed side, the change brought the dependence (a state leak): that counts,
as ``fails: order-dependent, new with this change``. Otherwise it is ``unstable``, naming the rerun that passed.
No flip is dropped: each one carries one of the four labels.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from . import edges, runners

CONFIRMED, UNSTABLE, NOT_CAUSED = 'fails', 'unstable', 'not caused by this change'
ORDER_NEW = 'fails: order-dependent, new with this change'


def counted(label):
    """Whether a flip with this label is a failure in the count: it fails in every rerun, or it fails on the changed
    code in some order and passes on the code before the change in every order run (a state leak the change brings)."""
    return label in (CONFIRMED, ORDER_NEW)
SEED = 20261016
MAX_ALONE = 20  # flips rerun one per fresh process; past this they share one fresh process and reach is not measured
LEGS = ('in_process', 'fresh_process', 'after_set')
NAMES = dict(in_process='the rerun in the same process', fresh_process='the rerun alone in a fresh process',
             after_set='the rerun after the tests that ran before it')

# Imported by every Python process started with the support directory on PYTHONPATH, children included. It records
# which changed lines run (AMU_REACH_TARGETS: {file: [lines]}), one file per process in AMU_REACH_OUT, then hands
# over to any sitecustomize the environment already had.
SITECUSTOMIZE = r'''
import os as _os, sys as _sys
_targets = _os.environ.get("AMU_REACH_TARGETS")
if _targets:
    try:
        import atexit as _atexit, json as _json
        with open(_targets) as _handle:
            _want = {name: set(lines) for name, lines in _json.load(_handle).items()}
        _hits = set()
        _out = _os.environ.get("AMU_REACH_OUT", "")

        def _save():
            try:
                with open(_os.path.join(_out, "%d.json" % _os.getpid()), "w") as _h:
                    _json.dump(sorted(_hits), _h)
            except Exception:
                pass
        _atexit.register(_save)
        if _sys.version_info >= (3, 12):
            _m = _sys.monitoring
            _tool = next(i for i in (3, 4) if _m.get_tool(i) is None)
            _m.use_tool_id(_tool, "amu-reach")

            def _line(code, number):
                lines = _want.get(code.co_filename)
                if lines is not None and number in lines:
                    _hits.add("%s:%d" % (code.co_filename, number))
                return _m.DISABLE
            _m.register_callback(_tool, _m.events.LINE, _line)
            _m.set_events(_tool, _m.events.LINE)
        else:
            import threading as _threading

            def _local(frame, event, arg):
                if event == "line" and frame.f_lineno in _want.get(frame.f_code.co_filename, ()):
                    _hits.add("%s:%d" % (frame.f_code.co_filename, frame.f_lineno))
                return _local

            def _global(frame, event, arg):
                return _local if frame.f_code.co_filename in _want else None
            _sys.settrace(_global)
            _threading.settrace(_global)
    except Exception:
        pass
if _os.environ.get("AMU_EDGES_OUT"):
    try:
        import amu_edges_hook  # noqa: F401  call edges and exception paths of this rerun (edges module)
    except Exception:
        pass
_here = _os.path.dirname(_os.path.abspath(__file__))
_kept = list(_sys.path)
_self = _sys.modules.get("sitecustomize")
try:
    _sys.path[:] = [p for p in _sys.path if _os.path.abspath(p or ".") != _here]
    del _sys.modules["sitecustomize"]
    import sitecustomize  # noqa: F401  the environment's own, if any
except Exception:
    pass
finally:
    _sys.path[:] = _kept
    if "sitecustomize" not in _sys.modules and _self is not None:
        # The environment has none of its own: the import that loaded this file expects to find it here.
        _sys.modules["sitecustomize"] = _self
'''

# pytest plugin (-p amu_trust_rerun): with AMU_TRUST_REPEAT=N each test runs N times in a row in the same process,
# torn down in between, and every phase's outcome is written in order to AMU_TRUST_OUT.
PLUGIN = r'''
import json, os

_REPEAT = int(os.environ.get("AMU_TRUST_REPEAT", "1"))
_OUT = os.environ.get("AMU_TRUST_OUT")
_SEEN = []


def pytest_runtest_protocol(item, nextitem):
    if _REPEAT <= 1:
        return None
    from _pytest.runner import runtestprotocol
    item.ihook.pytest_runtest_logstart(nodeid=item.nodeid, location=item.location)
    for turn in range(_REPEAT):
        if turn and hasattr(item, "_initrequest"):
            item._initrequest()
        runtestprotocol(item, nextitem=nextitem if turn == _REPEAT - 1 else None, log=True)
    item.ihook.pytest_runtest_logfinish(nodeid=item.nodeid, location=item.location)
    return True


def pytest_runtest_logreport(report):
    _SEEN.append([report.nodeid, report.when, report.outcome])


def pytest_sessionfinish(session):
    if _OUT:
        with open(_OUT, "w") as handle:
            json.dump(_SEEN, handle)
'''


def support(scratch, changed):
    """Write the tracer and plugin into ``scratch``/trust; ``changed`` is {absolute file: lines}. Returns the folder."""
    folder = Path(scratch)/'trust'
    folder.mkdir(parents=True, exist_ok=True)
    (folder/'sitecustomize.py').write_text(SITECUSTOMIZE)
    (folder/'amu_trust_rerun.py').write_text(PLUGIN)
    (folder/'amu_edges_hook.py').write_text(edges.HOOK)
    (folder/'targets.json').write_text(json.dumps({name: sorted(lines) for name, lines in changed.items()}))
    return folder


def changed_lines(tree, edits, place):
    """{absolute path in ``tree``: changed lines on the changed side}, under the path as given and as resolved.
    ``place(path, edit)`` returns the lines for one file."""
    out = {}
    for path, edit in edits.items():
        lines = place(path, edit)
        if not lines:
            continue
        for name in {str(Path(tree)/path), str((Path(tree)/path).resolve())}:
            out[name] = set(lines)
    return out


def ordering(runner, python):
    """(extra pytest arguments, description) for running both sides in the same order."""
    if runner.name != 'pytest':
        return [], 'the same explicit order on both sides (fixed hash seed)'
    try:
        found = subprocess.run([python, '-c', 'import pytest_randomly'], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        found = False
    if found:
        return [f'--randomly-seed={SEED}'], f'pytest-randomly, seed {SEED}, on both sides'
    return [], 'the same explicit order on both sides (fixed hash seed; pytest-randomly not installed)'


def _env(folder, out=None, repeat=None, reach=None, calls=None, root=None):
    extra = dict(AMU_TRUST_PATH=str(folder))
    if calls and root:
        # Python 3.12+: the rerun's call edges and, with exception events on, each exception's path (edges module).
        calls.mkdir(parents=True, exist_ok=True)
        extra.update(AMU_EDGES_OUT=str(calls), AMU_EDGES_ROOT=str(root), AMU_EDGES_EXC='1')
    if reach:
        reach.mkdir(parents=True, exist_ok=True)
        extra.update(AMU_REACH_TARGETS=str(folder/'targets.json'), AMU_REACH_OUT=str(reach))
    if repeat:
        extra.update(AMU_TRUST_REPEAT=str(repeat), AMU_TRUST_OUT=str(out))
    return extra


def _in_given_order(runner):
    """Arguments that make a run keep the order of the ids given: pytest-randomly, when loaded, must not reorder."""
    return ['--randomly-dont-reorganize'] if any('randomly' in str(arg) for arg in runner.extra) else None


def _preceding(flipped, candidates, context):
    if not context:
        return [i for i in candidates if i not in set(flipped)]
    out, seen, flips = [], set(), set(flipped)
    for ident in flipped:
        batch = list(context.get(ident) or ())
        if ident not in batch:
            continue
        for other in batch[:batch.index(ident)]:
            if other not in flips and other not in seen:
                seen.add(other)
                out.append(other)
    return out


def _relative(tree, changed):
    """``changed`` ({absolute file: lines}) keyed by path relative to ``tree``."""
    out = {}
    for name, lines in changed.items():
        for base in {str(Path(tree)), str(Path(tree).resolve())}:
            if name.startswith(base + '/'):
                out[name[len(base) + 1:]] = set(lines)
    return out


def _path(calls, test_path, changed, test_name=None):
    """From the rerun's recorded calls: the call path from the test to changed code, and the path of an exception that
    passed through changed code. {} when there is neither or nothing was recorded (Python before 3.12)."""
    names, recorded, chains = edges.merge(calls)
    if not names:
        return {}
    found = {}
    pairs = set().union(*recorded.values()) if recorded else set()
    route = edges.path_to(names, pairs, test_path, changed, test_name)
    if route:
        found['path'] = route
    hit = edges.holding(names, changed)
    for kind, chain in chains:
        if hit & set(chain):
            found['raised'] = dict(exception=kind, through=list(dict.fromkeys(edges.record_id(names[i]) for i in chain))[:8])
            break
    return found


def _hits(folder):
    """Changed lines run by any process that wrote a record; None when none did."""
    found, any_file = set(), False
    for name in Path(folder).glob('*.json'):
        try:
            found.update(json.loads(name.read_text()))
            any_file = True
        except (OSError, ValueError):
            pass
    return found if any_file else None


def in_process(runner, python, tree, flipped, scratch, timeout):
    """Each flip run twice in a row in one process; the second outcome. {id: state}; empty when not available."""
    folder = Path(scratch)/'trust'
    if runner.name == 'pytest':
        out = Path(scratch)/'trust-in-process.json'
        runners.execute(runner, python, tree, flipped, Path(scratch)/'trust-in-process.xml', timeout,
                        extra_env=_env(folder, out=out, repeat=2), extra_args=['-p', 'amu_trust_rerun'])
        try:
            seen = json.loads(out.read_text())
        except (OSError, ValueError):
            return {}
        by_node = {}
        for nodeid, when, outcome in seen:
            by_node.setdefault((nodeid.split('[')[0], when), []).append(outcome)
        result = {}
        for ident in flipped:
            label = runner.label(ident)
            # The second run of the test: a failure in any of its phases counts.
            phases = [by_node.get((label, when), []) for when in ('setup', 'call', 'teardown')]
            second = [outcomes[len(outcomes) // 2:] for outcomes in phases if outcomes]
            if not second:
                continue
            flat = [o for part in second for o in part]
            result[ident] = 'failed' if 'failed' in flat else 'skipped' if 'skipped' in flat else 'passed'
        return result
    # unittest and Django: the label twice in one run; the runner reports each, in order.
    states = {}
    for ident in flipped:
        found = runners.sequence(runner, python, tree, [ident, ident], timeout)
        if len(found.get(ident, [])) >= 2:
            states[ident] = found[ident][-1]
    return states


def triad(runner, python, tree, flipped, candidates, scratch, left, changed, before_tree=None, context=None):
    """The three reruns and the reach check for ``flipped`` on the changed tree ``tree``.

    ``candidates`` are all ids run on the changed side, in the order they ran. ``changed`` is {file: lines}.
    Returns ({id: dict(label, reruns, reach, why)}, gaps)."""
    folder = support(scratch, changed)
    gaps = []
    began = time.perf_counter()

    def remaining():
        return max(left - (time.perf_counter() - began), 20)
    legs = {leg: {} for leg in LEGS}
    legs['in_process'] = in_process(runner, python, tree, flipped, scratch, remaining())
    reach, paths = {}, {}
    relative = _relative(tree, changed)
    alone = flipped[:MAX_ALONE]
    for number, ident in enumerate(alone):
        where, calls = Path(scratch)/f'trust-reach-{number}', Path(scratch)/f'trust-calls-{number}'
        found, _ = runners.execute(runner, python, tree, [ident], Path(scratch)/f'trust-alone-{number}.xml', remaining(),
                                   extra_env=_env(folder, reach=where, calls=calls, root=tree))
        legs['fresh_process'][ident] = found.get(ident)
        hits = _hits(where)
        reach[ident] = None if hits is None else len(hits)
        paths[ident] = _path(calls, ident.split('::')[0], relative, ident.split('::')[-1])
    rest = flipped[MAX_ALONE:]
    if rest:
        found, _ = runners.execute(runner, python, tree, rest, Path(scratch)/'trust-alone-rest.xml', remaining())
        legs['fresh_process'].update({i: found.get(i) for i in rest})
        gaps.append(f'{len(rest)} flip(s) past the first {MAX_ALONE} were rerun together in one fresh process; their reach was not measured')
    # The third rerun puts each flip back after the tests that ran before it in its own batch: the exact order it failed
    # in, without running the whole candidate set again. Without batch records, every candidate goes first.
    others = _preceding(flipped, candidates, context)
    found, problem = runners.execute(runner, python, tree, [*others, *flipped], Path(scratch)/'trust-after-set.xml', remaining(),
                                     extra_args=_in_given_order(runner))
    legs['after_set'] = {i: found.get(i) for i in flipped}
    if problem == 'timed out':
        gaps.append('the rerun of flips after the tests that ran before them timed out; that rerun is missing for some flips')
    # Failing in some orders and passing in others is order dependence. Whether the change brought it is known only by
    # running the same orders on the code before the change: alone (the flip's own confirmation), after the tests that
    # ran before it, and twice in one process.
    ordered = [i for i in flipped if {legs[leg].get(i) for leg in LEGS} & {'passed', 'skipped'}
               and {legs[leg].get(i) for leg in LEGS} & {'failed'}]
    base_order, base_repeat = {}, {}
    if ordered and before_tree is not None:
        if any(legs['after_set'].get(i) == 'failed' for i in ordered):
            base_order, _ = runners.execute(runner, python, before_tree, [*others, *ordered], Path(scratch)/'trust-after-set-base.xml',
                                            remaining(), extra_args=_in_given_order(runner))
        repeated = [i for i in ordered if legs['in_process'].get(i) == 'failed']
        if repeated:
            base_repeat = in_process(runner, python, before_tree, repeated, scratch, remaining())
    verdicts = {}
    for ident in flipped:
        reruns = {leg: legs[leg].get(ident) for leg in LEGS}
        passed = [leg for leg, state in reruns.items() if state in ('passed', 'skipped')]
        missing = [leg for leg, state in reruns.items() if state not in runners.ORDER]
        if passed:
            failed_in = [leg for leg in ('in_process', 'after_set') if reruns[leg] == 'failed']
            base = {'in_process': base_repeat.get(ident), 'after_set': base_order.get(ident)}
            if ident in ordered and failed_in and all(base[leg] == 'passed' for leg in failed_in):
                label, why = ORDER_NEW, ('fails ' + ' and '.join(NAMES[leg] for leg in failed_in).replace('the rerun ', '')
                                         + ', passes in ' + ' and '.join(NAMES[leg] for leg in passed)
                                         + '; the code before the change passes in the same orders: a state leak this change brings')
            else:
                label, why = UNSTABLE, 'passed in ' + ' and '.join(NAMES[leg] for leg in passed)
                if any(base[leg] == 'failed' for leg in failed_in):
                    why += '; fails in the same order on the code before the change too: an order dependence already there'
                elif failed_in:
                    why += '; the same order could not be run on the code before the change'
        elif reach.get(ident) == 0:
            label, why = NOT_CAUSED, 'it ran none of the changed lines on the changed code (reach tracer)'
        else:
            label, why = CONFIRMED, 'failed again in every rerun'
            if reach.get(ident):
                why += f'; ran {reach[ident]} changed line(s)'
        if missing and label != UNSTABLE:
            why += '; not rerun: ' + ', '.join(NAMES[leg] for leg in missing)
        if reach.get(ident) is None and label == CONFIRMED:
            why += '; reach not measured'
        verdicts[ident] = dict(label=label, reruns=reruns, reach=reach.get(ident), why=why, **(paths.get(ident) or {}))
    return verdicts, gaps
