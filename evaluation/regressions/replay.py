"""Replay confirmed regression pull requests through a frozen AMU engine, as the Action runs them, and say whether AMU
caught the regression and, if not, why.

Usage: python replay.py --engine DIR --tag TAG --verified verified.jsonl --candidates candidates.json [--only ID ...]
                        [--suite-dir DIR]
Per pull request, under out-reg-TAG/<id>/: report.json, meta.json, comment.md, run.log, existing.json, verdict.json.

Caught: a counted flip, or a behaviour row on a regressed function. Otherwise one cause, checked in this order:
  trust     a test flipped in AMU's run but the trust layer did not count it
  budget    a test that flips exists in the suite at the head, and AMU's run was cut before it ran
  selection a test that flips exists in the suite at the head, and AMU did not select it
  ran       a test that flips exists, AMU ran it, and it did not flip there (traced by hand)
  suite     no test in the suite at the head flips: only the later test shows the regression
"existing.json" is that check: the test files the fix touched, as they are at the head (no overlay), run on the merge
base and the head; a test passing on the base and failing on the head is a test that flips.
"""
import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import verify as V

W = Path(__file__).resolve().parent.parent
AMU_PY = str(W/'amuvenv'/'bin'/'python')


def run_amu(engine, tag, c, key, out, suite_dir):
    clone, python = V.CLONES[c['repo']], V.PYTHONS[c['repo']]/'bin'/'python'
    head = W/'regwork'/f'amu-{tag}'/key.replace(':', '_')
    V.git(clone, 'worktree', 'remove', '--force', str(head))
    shutil.rmtree(head, ignore_errors=True)
    head.parent.mkdir(parents=True, exist_ok=True)
    V.git(clone, 'worktree', 'add', '--detach', '-q', str(head), c['head'], check=True)
    env = dict({k: v for k, v in os.environ.items() if not k.startswith('ANTHROPIC_')}, PYTHONPATH=str(engine))
    if suite_dir:
        env['AMU_SUITE_DIR'] = suite_dir
    command = [AMU_PY, '-P', '-m', 'amu.ci.run', '--base', c['base'], '--head', c['head'], '--out', str(out),
               '--python', str(python), '--pr', str(c.get('culprit_pr') or 0), '--install-ok', 'true', '--amu-ref', tag,
               '--budget', '300', '--wall-cap', '1200', '--generate-dollars', '0', '--extras', '']
    began = time.monotonic()
    done = subprocess.run(command, cwd=head, capture_output=True, text=True, env=env)
    seconds = round(time.monotonic() - began, 1)
    (out/'run.log').write_text(done.stdout[-20000:] + done.stderr[-20000:])
    comment = subprocess.run([AMU_PY, '-P', '-m', 'amu.ci.render', str(out)], capture_output=True, text=True, env=env).stdout
    (out/'comment.md').write_text(comment)
    V.git(clone, 'worktree', 'remove', '--force', str(head))
    return seconds, comment


def existing_flips(c, key):
    """Tests in the fix's test files, as they stand at the head, that pass on the base and fail on the head."""
    clone = V.CLONES[c['repo']]
    files = [f for f in V.test_files(clone, c['fix_sha']) if V.git(clone, 'cat-file', '-t', f"{c['head']}:{f}") == 'blob']
    if not files:
        return dict(files=[], flips=[], note='the fix only adds new test files')
    probe = dict(c, tests=[f.replace('/', '.')[:-3].replace('tests.', '', 1) if c['repo'] == 'django' else f for f in files])
    sides = {}
    for side in ('base', 'head'):
        sides[side] = run_plain(probe, f"{c['repo']}-exist-{side}", c[side])
    flips = sorted(t for t, r in sides['head'].items() if r == 'fail' and sides['base'].get(t) == 'pass')
    return dict(files=files, flips=flips, counts={s: len(v) for s, v in sides.items()})


def run_plain(c, label, commit):
    """Run c['tests'] at commit with no overlay; per-test outcomes."""
    clone, python = V.CLONES[c['repo']], V.PYTHONS[c['repo']]/'bin'/'python'
    tree = W/'regwork'/'verify'/label
    V.git(clone, 'worktree', 'remove', '--force', str(tree))
    shutil.rmtree(tree, ignore_errors=True)
    V.git(clone, 'worktree', 'add', '--detach', '-q', str(tree), commit, check=True)
    env = dict({k: v for k, v in os.environ.items() if not k.startswith('ANTHROPIC_')},
               PYTHONPATH=os.pathsep.join([str(tree/'src'), str(tree)]), PYTHONDONTWRITEBYTECODE='1')
    cmd = V.command(c['repo'], tree, python, c['tests'])
    if c['repo'] == 'django':
        cmd[1] = 'runtests.py'
    try:
        done = subprocess.run(cmd, cwd=tree/'tests' if c['repo'] == 'django' else tree, capture_output=True, text=True,
                              env=env, timeout=1800)
        result = V.per_test(c['repo'], done.stdout + done.stderr)
    except subprocess.TimeoutExpired:
        result = {}
    V.git(clone, 'worktree', 'remove', '--force', str(tree))
    return result


def same_test(a, b):
    """A pytest node id or a Django dotted id, matched without parameters and module prefixes."""
    def norm(t):
        return t.split('[')[0].replace('::', '.').replace('/', '.').replace('.py.', '.').removeprefix('tests.')
    return norm(a) == norm(b) or norm(a).endswith('.' + norm(b)) or norm(b).endswith('.' + norm(a))


def classify(c, report, exist):
    counted = [r for r in report.get('predicted_failures') or [] if isinstance(r, dict) and r.get('label') != 'test edited, not fixed']
    # A row counts only on the regressed function itself: the same method name on another class is another function.
    names = {r.split('#', 1)[-1] for r in c.get('regressed') or []}
    rows = [r for r in report.get('behaviour_differences') or []
            if str(r.get('target', '')).split('#')[-1] in names]
    if counted or rows:
        return dict(caught=True, by='flip' if counted else 'behaviour row',
                    flips=[r.get('id') for r in counted], rows=len(rows))
    trust = [r for r in report.get('flip_trust') or [] if isinstance(r, dict)
             and r.get('label') not in ('fails', 'fails: order-dependent, new with this change')]
    if not exist['flips']:
        return dict(caught=False, cause='suite', detail=exist.get('note') or 'no existing test flips')
    # Trust is the cause only when a test that really flips was set aside as unstable.
    trust = [r for r in trust if any(same_test(str(r.get('id')), f) for f in exist['flips'])]
    if trust:
        return dict(caught=False, cause='trust', detail=trust)
    executed = report.get('executed') or {}
    planned = [t.get('id') for t in report.get('tests') or [] if isinstance(t, dict)]
    picked = [f for f in exist['flips'] if any(same_test(f, p) for p in planned)]
    if not picked:
        cut = (executed.get('candidates') or 0) > (executed.get('ran') or 0) + (executed.get('unrunnable') or 0)
        return dict(caught=False, cause='budget' if cut else 'selection', detail=dict(flips=exist['flips'][:10],
                    ran=executed.get('ran'), candidates=executed.get('candidates')))
    return dict(caught=False, cause='ran', detail=dict(selected=picked[:10]))


def main():
    a = argparse.ArgumentParser()
    a.add_argument('--engine', required=True)
    a.add_argument('--tag', required=True)
    a.add_argument('--verified', required=True)
    a.add_argument('--candidates', required=True)
    a.add_argument('--only', nargs='*')
    a.add_argument('--suite-dir')
    args = a.parse_args()
    cands = {V.cid(c): c for c in json.loads(Path(args.candidates).read_text())}
    for line in Path(args.verified).read_text().splitlines():
        row = json.loads(line)
        if row['verdict'] != 'confirmed' or (args.only and row['id'] not in args.only):
            continue
        c, key = cands[row['id']], row['id']
        out = W/f'out-reg-{args.tag}'/key.replace(':', '_')
        if (out/'verdict.json').exists():
            continue
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True)
        seconds, comment = run_amu(Path(args.engine), args.tag, c, key, out, args.suite_dir)
        report = json.loads((out/'report.json').read_text()) if (out/'report.json').exists() else {}
        exist = existing_flips(c, key)
        (out/'existing.json').write_text(json.dumps(exist, indent=1))
        verdict = dict(id=key, repo=c['repo'], seconds=seconds, first=(comment.splitlines() or [''])[0],
                       regressed_tests=row['regressed_tests'], regressed=c.get('regressed'),
                       **(classify(c, report, exist) if report else dict(caught=False, cause='no report')))
        (out/'verdict.json').write_text(json.dumps(verdict, indent=1))
        print(json.dumps({k: verdict[k] for k in ('id', 'caught') if k in verdict} | dict(by=verdict.get('by'), cause=verdict.get('cause'))), flush=True)


if __name__ == '__main__':
    main()
