import json, os, shutil, subprocess, sys, coverage
from pathlib import Path
repo, S, W = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
D = repo/'evaluation/before-tests'
rows = {r['case_id']: r for name in ('click-sealed-v2.json', 'holdout3.json') for r in json.loads((D/name).read_text())}
truth = {}
for i, case in enumerate(['click-v2-9f0bcdf0', 'click-v2-d0368817', 'click-v2-f67c2bb6', 'h3-click-bc32a92c', 'h3-click-8ee83ddb'], 1):
    base = D/'exec'/case/'base'
    truth[f'task{i}'] = dict(case=case, breaking=json.loads((D/'exec'/case/'result.json').read_text())['outcome_changed'])
    data = coverage.CoverageData(basename=str(base/'.coverage')); data.read()
    files = {}
    for name in data.measured_files():
        rel = Path(name).resolve().relative_to(base.resolve()).as_posix()
        files[rel] = {'contexts': {str(l): c for l, c in data.contexts_by_lineno(name).items()}}
    for arm in ('a', 'b'):
        work = S/'trial'/f'task{i}{arm}'
        shutil.copytree(rows[case]['tree'], work, ignore=shutil.ignore_patterns('.git', '.amu', '__pycache__', '.github', 'docs'))
        (work/'coverage.json').write_text(json.dumps({'meta': {'show_contexts': True}, 'files': files}))
        import time; later = time.time() + 2
        shutil.copy(rows[case]['patch'], work/'change.diff')
        with open(work/'change.diff', 'rb') as h:
            subprocess.run(['/usr/bin/patch', '-p1', '--batch', '-s'], cwd=work, stdin=h, check=True)
        for name in [l[6:].strip() for l in (work/'change.diff').read_text().splitlines() if l.startswith('+++ b/')]: os.utime(work/name, (later, later))
        for junk in work.rglob('*.orig'): junk.unlink()
        (work/'rt').write_text(f'''#!/bin/bash
# Run named test functions. Usage: ./rt tests/test_x.py::test_y [more ids]
cd "$(dirname "$0")"
for id in "$@"; do case "$id" in *::*) ;; *) echo "each argument must name a test function (file::function)"; exit 2;; esac; done
used=$(cat .rt-log 2>/dev/null | wc -l | tr -d ' ')
if [ $((used + $#)) -gt 15 ]; then echo "budget exceeded: $used of 15 test functions already run"; exit 3; fi
for id in "$@"; do echo "$id" >> .rt-log; done
env -i PATH=/usr/bin:/bin HOME="$PWD/.home" PYTHONPATH="$PWD/src" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 {W} -B -m pytest "$@" -q -p no:cacheprovider -o addopts= -W ignore 2>&1 | tail -40
''')
        os.chmod(work/'rt', 0o755)
        if arm == 'b':
            (work/'amu').write_text(f'''#!/bin/bash
# What the change reaches and which existing tests to run first. Add --full-evidence for the complete ranking.
cd "$(dirname "$0")"
PYTHONPATH={S}/trial-tools AMU_INDEX_DIR="$PWD/.amu-index" /opt/homebrew/bin/python3 -m amu.core --repo . --patch-file change.diff "$@"
''')
            os.chmod(work/'amu', 0o755)
(S/'trial-truth.json').write_text(json.dumps(truth, indent=1))
print(json.dumps({k: (v['case'], len(v['breaking'])) for k, v in truth.items()}))
