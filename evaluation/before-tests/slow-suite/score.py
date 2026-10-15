"""Score the slow-suite trial against the hidden upstream tests. Run only after every agent has finished.

Usage: score.py ROOT SYMPY_GIT [--only ID ...]
Executes sympy's default suite (its own pytest configuration; slow and tooslow tests are not run): per task on the
parent tree and on the upstream commit, per run on the agent's tree with every sympy/**/tests/ directory replaced by
upstream's at the task's commit (the hidden tests), and the touched test modules on the agent's tree as shipped.
Definitions are in TRIAL-RECORD.md and are those of the two Django trials; this file implements them and nothing
else. Scoring runs use pytest-xdist (rules.json score_workers); every suspect is then run again by itself, serially.
"""
import argparse
import ast
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import symrun  # noqa: E402

# The Django trials' transcript reader (blind/score.py; this file has the same name, so it is loaded by path).
_spec = importlib.util.spec_from_file_location('blind_score', HERE.parent/'blind'/'score.py')
_blind = importlib.util.module_from_spec(_spec)
sys.path.insert(1, str(HERE.parent/'blind'))
_spec.loader.exec_module(_blind)
session = _blind.session

RULES = json.loads((HERE/'rules.json').read_text())
OUTSIDE = r'sympy-cases|sympy/repo|/runs/|side-session-notes|scratchpad/slow'
EDITS = r'sed -i|python3? - <<|cat >|> *sympy/|perl -pi'


def is_test_path(path):
    return path.startswith('sympy/') and '/tests/' in path


def cached(path, make):
    path = Path(path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(make()))
    return json.loads(path.read_text())


def suite(python, tree, home, labels=(), workers=None):
    workers = RULES['score_workers'] if workers is None and not labels else (workers or 0)
    result = symrun.run(python, tree, labels, timeout=3 * 3600, home=home, workers=workers)
    return dict(states=result['states'], problems=result['problems'], seconds=result['seconds'])


def extract(repo, commit, target, paths=()):
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    data = subprocess.run(['git', '-C', str(repo), 'archive', commit, *paths], check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        archive.extractall(target, filter='data')


def test_source(tree, ident):
    """Source text of the test function (path::Class::test), or the whole file when it is inherited or generated."""
    path, *names = ident.split('::')
    file = Path(tree)/path
    if not file.is_file():
        return None
    source = file.read_text(errors='replace')
    body, node = ast.parse(source).body, None
    for name in names:
        node = next((n for n in body if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name), None)
        if node is None:
            return 'file:' + source
        body = getattr(node, 'body', [])
    return ast.get_source_segment(source, node)


def ran_by(test, entry):
    """Whether a completed ./rt run covered ``test``. A -k run counts when its expression occurs in the test's id."""
    labels = entry.get('labels') or ['full suite']
    k = (entry.get('k') or [None])[0]
    hit = labels == ['full suite'] or any(symrun.covers(label, test) for label in labels)
    return hit and (k is None or k.strip() in test)


def score_run(root, repo, ident, slot, row, parent, upstream, python):
    work, run, scratch = root/'ws'/ident, root/'runs'/ident, root/'score'/ident
    result = json.loads((run/'result.json').read_text())

    def hidden():
        tree = scratch/'hidden'
        shutil.rmtree(scratch, ignore_errors=True)
        shutil.copytree(work, tree, ignore=shutil.ignore_patterns('.git', '__pycache__', 'rt', 'TASK.md', 'PROMPT.md', '.claude', '.hypothesis'))
        for tests in sorted(tree.glob('sympy/**/tests'), key=lambda p: len(p.parts), reverse=True):
            shutil.rmtree(tests, ignore_errors=True)
        names = subprocess.run(['git', '-C', str(repo), 'ls-tree', '-r', '--name-only', row['commit'], 'sympy'], check=True,
                               capture_output=True, text=True).stdout.split()
        extract(repo, row['commit'], scratch/'up', sorted({n.split('/tests/')[0] + '/tests' for n in names if is_test_path(n)}))
        for tests in sorted((scratch/'up').glob('sympy/**/tests')):
            target = tree/tests.relative_to(scratch/'up')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(tests), str(target))
        first = suite(python, tree, scratch/'home')
        suspects = sorted(t for t, state in upstream['states'].items() if state == 'passed' and first['states'].get(t) not in ('passed', 'skipped'))
        # Every suspect is run again by itself before it counts; one that passes alone is recorded as unstable.
        again = suite(python, tree, scratch/'home', suspects, workers=0)['states'] if 0 < len(suspects) <= 400 else {}
        return dict(first=first, suspects=suspects, again=again)
    hid = cached(root/'score'/f'{ident}-hidden.json', hidden)
    failing = [t for t in hid['suspects'] if hid['again'].get(t) != 'passed']
    regressions = [t for t in failing if parent['states'].get(t) == 'passed']
    new_failing = [t for t in failing if t not in parent['states']]
    parent_tree = Path(row['tree'])
    kept = [t for t in regressions if test_source(parent_tree, t) == test_source(scratch/'hidden', t)]

    def own():
        changed = subprocess.run(['git', '-C', str(work), 'diff', '--cached', '--name-only', 'HEAD'], check=True, capture_output=True, text=True).stdout.split()
        labels = sorted({*row['labels'], *(p for p in changed if is_test_path(p) and Path(p).name.startswith('test_') and (work/p).is_file())})
        return dict(labels=labels, **suite(python, work, scratch/'home-own', labels, workers=0))
    mine = cached(root/'score'/f'{ident}-own.json', own)
    red = sorted(t for t, state in mine['states'].items() if state == 'failed' and parent['states'].get(t) == 'passed')
    ledger = json.loads((run/'ledger.json').read_text()) if (run/'ledger.json').exists() else []
    hooks = [json.loads(line) for line in (run/'hook.jsonl').read_text().splitlines()] if (run/'hook.jsonl').exists() else []
    breaking = list(row['breaking'])
    agent_ran = [t for t in breaking if any(r['tool'] == 'rt' and ran_by(t, r) and not r.get('problems') for r in ledger)]
    hook_flagged = [t for t in breaking if any(t in (h.get('failing') or []) for h in hooks)]
    talk = session(run/'session.jsonl', OUTSIDE, EDITS) if (run/'session.jsonl').exists() else {}
    return dict(id=ident, **slot, wall_seconds=result['wall_seconds'], stopped_at_hard_cap=result['stopped_at_hard_cap'], **talk,
                regressions=regressions, regressions_in_tests_upstream_left_alone=kept, upstream_new_tests_failing=new_failing,
                unstable=[t for t in hid['suspects'] if hid['again'].get(t) == 'passed'], hidden_problems=hid['first']['problems'],
                red_in_own_tree=red, own_problems=mine['problems'], breaking=breaking, breaking_run_by_agent=agent_ran, breaking_flagged_by_hook=hook_flagged,
                test_seconds=round(sum(r['seconds'] for r in ledger), 1), rt_runs=sum(r['tool'] == 'rt' for r in ledger),
                hook_runs=sum(r['tool'] == 'hook' for r in ledger), hook_seconds=round(sum(r['seconds'] for r in ledger if r['tool'] == 'hook'), 1),
                hook_injections=sum(bool(h.get('injected')) for h in hooks), hook_skipped_for_budget=sum(h.get('skipped') == 'budget' for h in hooks),
                diff_lines=len((run/'agent.diff').read_text(errors='replace').splitlines()) if (run/'agent.diff').exists() else None)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('root')
    p.add_argument('repo')
    p.add_argument('--only', nargs='*')
    a = p.parse_args()
    root, repo, python = Path(a.root), Path(a.repo), str(Path(a.root)/'env/bin/python')
    manifest = {row['case_id']: row for row in json.loads((HERE/'sympy.json').read_text())}
    mapping = json.loads((root/'runs'/'map.json').read_text())
    rows = []
    for ident, slot in sorted(mapping.items(), key=lambda item: (item[1]['task'], item[1]['trial'], item[1]['arm'])):
        if (a.only and ident not in a.only) or not (root/'runs'/ident/'result.json').exists():
            continue
        row = manifest[slot['case']]
        parent = cached(root/'score'/f"{slot['task']}-parent.json", lambda: suite(python, row['tree'], root/'score'/'home-parent'))

        def reference():
            extract(repo, row['commit'], root/'score'/'trees'/slot['task'])
            return suite(python, root/'score'/'trees'/slot['task'], root/'score'/'home-upstream')
        upstream = cached(root/'score'/f"{slot['task']}-upstream.json", reference)
        rows.append(score_run(root, repo, ident, slot, row, parent, upstream, python))
        print(ident, slot['task'], slot['arm'], slot['trial'], 'regressions', len(rows[-1]['regressions']), 'red in own tree',
              len(rows[-1]['red_in_own_tree']), flush=True)
        (root/'score'/'scores.json').write_text(json.dumps(rows, indent=1))
