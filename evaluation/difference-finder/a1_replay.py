"""Check A1 of brief 4: replay each frozen case's own tests on the code before the change.

Usage: a1_replay.py MANIFEST [MANIFEST ...] --python PY --scratch DIR --out OUT.json [--only-files GLOB ...] [--cases ID ...]

For each case: the parent tree from the frozen commit's repository, the commit's whole diff (production and tests)
applied uncommitted, then exactly the tests the commit adds or edits (``replay.touched``) run on the head and on the
code before the change with the tests as the commit leaves them. Nothing else runs. ``--only-files`` keeps a case
only when every test file it touches matches, for suites approved one file at a time.
"""
from __future__ import annotations

import argparse
import fnmatch
import io
import json
import subprocess
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from amu.core import replay, shadow  # noqa: E402
from amu.core.patch import changes  # noqa: E402


def git(*args, cwd=None):
    return subprocess.run(['git', *args], cwd=cwd, capture_output=True, check=True).stdout


def build(row, work):
    tree = work/'tree'
    tree.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(git('--git-dir=' + row['repo'], 'archive', row['parent']))) as archive:
        archive.extractall(tree, filter='data')
    for args in (['init', '-q'], ['add', '-A'], ['-c', 'user.email=a@b', '-c', 'user.name=amu', 'commit', '-qm', 'base']):
        git(*args, cwd=tree)
    full = git('--git-dir=' + row['repo'], 'diff', '--binary', row['parent'], row['commit'])
    subprocess.run(['git', 'apply', '--whitespace=nowarn', '-'], cwd=tree, input=full, check=True, capture_output=True)
    git('add', '-A', cwd=tree)
    return tree, git('diff', '--cached', '--no-color', 'HEAD', cwd=tree).decode(errors='replace')


def case(row, python, scratch, budget):
    work = Path(scratch)/row['case_id']
    if work.exists():
        subprocess.run(['rm', '-rf', str(work)], check=True)
    tree, patch = build(row, work)
    edits, _ = changes(tree, patch)
    own = replay.touched(tree, edits, 'pytest')
    started = time.perf_counter()
    rows, info, gaps = shadow.run(tree, edits, own, python, budget, 'pytest', own=own) if own else ([], {}, [])
    replayed = info.get('replayed', [])
    truth = set(row['truth'])
    new_only = bool(row['truth']) and not row['existing_truth']
    evidence = [r['id'] for r in replayed if r['verdict'] == 'evidence']
    return dict(case_id=row['case_id'], truth=sorted(truth), new_tests_only=new_only, touched=own,
                replayed=replayed, evidence=evidence, evidence_in_truth=sorted(set(evidence) & truth),
                head_states={i: info.get('outcomes', {}).get(i) for i in own}, flips=rows, gaps=gaps,
                seconds=round(time.perf_counter() - started, 1))


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('manifests', nargs='+')
    p.add_argument('--python', required=True)
    p.add_argument('--scratch', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--budget', type=int, default=300)
    p.add_argument('--only-files', nargs='*', default=None)
    p.add_argument('--cases', nargs='*', default=None)
    a = p.parse_args(argv)
    out = Path(a.out)
    results = json.loads(out.read_text()) if out.exists() else []
    done = {r['case_id'] for r in results}
    for manifest in a.manifests:
        for row in json.loads(Path(manifest).read_text()):
            if row['case_id'] in done or (a.cases and row['case_id'] not in a.cases) or not row['files']:
                continue
            if a.only_files is not None and not all(any(fnmatch.fnmatch(f, g) for g in a.only_files) for f in row['files']):
                continue
            try:
                result = case(row, a.python, a.scratch, a.budget)
            except Exception as exc:  # recorded, not hidden
                result = dict(case_id=row['case_id'], error=f'{type(exc).__name__}: {exc}'[:500])
            result['manifest'] = Path(manifest).name
            results.append(result)
            out.write_text(json.dumps(results, indent=1) + '\n')
            print(json.dumps({k: result.get(k) for k in ('case_id', 'new_tests_only', 'touched', 'evidence', 'seconds', 'error')}), flush=True)


if __name__ == '__main__':
    main()
