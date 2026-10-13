import json, os, shutil, subprocess, sys
from pathlib import Path
repo, S, py = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
rows = json.loads((repo/'evaluation/before-tests/phase0/django.json').read_text())
truth = {}
for i, row in enumerate(rows, 1):
    truth[f'case{i}'] = dict(case=row['case_id'], breaking=row['breaking'], indirect=row['indirect'])
    for arm in 'ab':
        work = S/'p0'/f'case{i}{arm}'
        shutil.copytree(row['tree'], work, ignore=shutil.ignore_patterns('docs', 'js_tests', '.github', '__pycache__', 'extras', 'scripts'))
        shutil.copy(row['patch'], work/'change.diff')
        with open(work/'change.diff', 'rb') as h:
            subprocess.run(['/usr/bin/patch', '-p1', '--batch', '-s'], cwd=work, stdin=h, check=True)
        for junk in work.rglob('*.orig'): junk.unlink()
        (work/'rt').write_text(f'''#!/bin/bash
# Run named test methods. Usage: ./rt tests/app/test_file.py::ClassName::test_method [more ids]
cd "$(dirname "$0")"
labels=()
for id in "$@"; do
  case "$id" in tests/*.py::*::test*) ;; *) echo "each argument must be tests/<path>.py::ClassName::test_method"; exit 2;; esac
  path="${{id%%::*}}"; rest="${{id#*::}}"; path="${{path#tests/}}"; path="${{path%.py}}"
  labels+=("${{path//\\//.}}.${{rest//::/.}}")
done
used=$(cat .rt-log 2>/dev/null | wc -l | tr -d ' ')
if [ $((used + $#)) -gt 15 ]; then echo "budget exceeded: $used of 15 test methods already run"; exit 3; fi
for id in "$@"; do echo "$id" >> .rt-log; done
mkdir -p .home
cd tests && env -i PATH=/usr/bin:/bin HOME="$PWD/../.home" PYTHONPATH="$PWD/.." PYTHONDONTWRITEBYTECODE=1 LANG=en_US.UTF-8 {py} -B runtests.py "${{labels[@]}}" -v 2 --parallel 1 --noinput 2>&1 | grep -v "^Creating\\|^Destroying\\|^Cloning\\|^  \\(Applying\\|Creating\\|Running\\|Synchroniz\\)\\|^Operations to perform\\|^Running migrations\\|^    Creating table" | tail -45
''')
        os.chmod(work/'rt', 0o755)
        if arm == 'b':
            (work/'amu').write_text(f'''#!/bin/bash
# What the change reaches, what it likely breaks, and which existing tests to consider. --full-evidence for the complete ranking.
cd "$(dirname "$0")"
PYTHONPATH={S}/p0-tools AMU_INDEX_DIR="$PWD/.amu-index" /opt/homebrew/bin/python3 -m amu.core --repo . --patch-file change.diff --coverage none "$@"
''')
            os.chmod(work/'amu', 0o755)
(S/'p0-truth.json').write_text(json.dumps(truth, indent=1))
print({k: (v['case'], len(v['breaking']), len(v['indirect'])) for k, v in truth.items()})
