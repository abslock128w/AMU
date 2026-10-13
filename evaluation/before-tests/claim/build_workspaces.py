"""Build the claim-run workspaces: one git repository per task and arm, with the change uncommitted.

Both arms get ./rt, a test runner that charges wall-clock time to a 180 s ledger and stops at the limit.
The AMU arm also gets ./amu, whose whole running time (index, tree copy, shadow execution) is charged to
the same ledger. Usage: build_workspaces.py MANIFEST OUT PYTHON TOOLS RECORDING [task prefix]
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

BUDGET = 180

RT = r'''#!/usr/bin/env python3
"""Run Django tests by label, charging wall-clock time to this task's budget.
Usage: ./rt LABEL [LABEL ...]   label = app, app.module, app.module.Class or app.module.Class.test_method
       (a path id like tests/app/module.py::Class::test_method is accepted too)"""
import json, os, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, ".budget.json")
BUDGET = %(budget)d
def load():
    try:
        return json.load(open(LEDGER))
    except (OSError, ValueError):
        return []
def spent():
    return sum(row["seconds"] for row in load())
def charge(tool, seconds, detail):
    rows = load(); rows.append({"tool": tool, "seconds": round(seconds, 2), "detail": detail})
    json.dump(rows, open(LEDGER, "w"))
if __name__ == "__main__":
    labels = []
    for arg in sys.argv[1:]:
        if "::" in arg:
            path, *rest = arg.split("::")
            path = path[len("tests/"):] if path.startswith("tests/") else path
            arg = ".".join([path[:-3].replace("/", "."), *rest])
        labels.append(arg)
    if not labels:
        sys.exit(__doc__)
    left = BUDGET - spent()
    if left <= 1:
        sys.exit(f"budget exhausted: {BUDGET} s of tool time used")
    env = {"PATH": "/usr/bin:/bin", "HOME": os.path.join(HERE, ".home"), "PYTHONPATH": HERE, "PYTHONDONTWRITEBYTECODE": "1", "LANG": "en_US.UTF-8"}
    os.makedirs(env["HOME"], exist_ok=True)
    start = time.time()
    try:
        done = subprocess.run(["%(python)s", "-B", "runtests.py", *labels, "-v", "2", "--parallel", "1", "--noinput"],
                              cwd=os.path.join(HERE, "tests"), env=env, capture_output=True, text=True, timeout=left, stdin=subprocess.DEVNULL)
        output = done.stderr + done.stdout
    except subprocess.TimeoutExpired as exc:
        output = "STOPPED: the budget ran out during this run.\n" + ((exc.stderr or b"").decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or ""))
    charge("rt", time.time() - start, " ".join(labels)[:300])
    lines = [l for l in output.splitlines() if not l.startswith(("Creating", "Destroying", "Cloning", "  ", "Operations to perform", "Running migrations", "Synchroniz", "Importing application"))]
    keep = [l for l in lines if " ... " in l and not l.rstrip().endswith("... ok")] + lines[-25:]
    print("\n".join(keep[-70:]))
    print(f"[budget: {spent():.0f} of {BUDGET} s of tool time used]")
'''

AMU = r'''#!/usr/bin/env python3
"""AMU: for the uncommitted change in this repository, run candidate tests before and after it and print
which existing tests now fail. Its running time is charged to this task's budget. Usage: ./amu"""
import json, os, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from rt import BUDGET, charge, spent
left = BUDGET - spent()
if left <= 30:
    sys.exit(f"budget exhausted or too little left for AMU: {left:.0f} s")
# AMU's own overhead (index from cold, tree copy) is about 30 s on Django; the rest goes to running tests.
allowance = int(max(10, min(110, left - 45)))
env = dict(os.environ, PYTHONPATH="%(tools)s", AMU_INDEX_DIR=os.path.join(HERE, ".amu-index"))
start = time.time()
done = subprocess.run(["/opt/homebrew/bin/python3", "-m", "amu.core", "--repo", HERE, "--python", "%(python)s", "--budget", str(allowance), *sys.argv[1:]],
                      env=env, capture_output=True, text=True)
charge("amu", time.time() - start, " ".join(sys.argv[1:]))
print(done.stdout.strip() or done.stderr.strip()[-600:])
print(f"[budget: {spent():.0f} of {BUDGET} s of tool time used]")
'''


def build(row, out, name, arm, python, tools, recording):
    work = out/f'{name}{arm}'
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(row['tree'], work, ignore=shutil.ignore_patterns('docs', 'js_tests', '.github', '__pycache__', 'extras', 'scripts', '.amu'))
    git = ['git', '-C', str(work), '-c', 'user.email=trial@example.invalid', '-c', 'user.name=trial']
    for args in (['init', '-q'], ['add', '-A'], ['commit', '-qm', 'parent state']):
        subprocess.run([*git, *args], check=True, stdout=subprocess.DEVNULL)
    shutil.copy(row['patch'], work/'change.diff')
    with open(work/'change.diff', 'rb') as handle:
        subprocess.run(['/usr/bin/patch', '-p1', '--batch', '-s'], cwd=work, stdin=handle, check=True)
    for junk in work.rglob('*.orig'):
        junk.unlink()
    (work/'rt.py').write_text(RT % dict(budget=BUDGET, python=python))
    (work/'rt').write_text('#!/bin/sh\nexec /opt/homebrew/bin/python3 "$(dirname "$0")/rt.py" "$@"\n')
    os.chmod(work/'rt', 0o755)
    if arm == 'b':
        (work/'amu').write_text(AMU % dict(tools=tools, python=python))
        os.chmod(work/'amu', 0o755)
        # The stored recording is the only thing pre-placed in the index: facts and graph are built cold, on the clock.
        version = Path(recording).parent.name
        (work/'.amu-index'/version).mkdir(parents=True)
        os.symlink(recording, work/'.amu-index'/version/'observed')
    with open(work/'.git/info/exclude', 'a') as handle:
        handle.write('rt\nrt.py\namu\nchange.diff\n.budget.json\n.amu-index/\n.home/\n')
    return work


if __name__ == '__main__':
    manifest, out, python, tools, recording = sys.argv[1:6]
    prefix = sys.argv[6] if len(sys.argv) > 6 else 'task'
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    truth = {}
    for number, row in enumerate(json.loads(Path(manifest).read_text()), 1):
        name = f'{prefix}{number}'
        truth[name] = dict(case=row['case_id'], breaking=row['breaking'], indirect=row['indirect'])
        for arm in 'ab':
            build(row, out, name, arm, python, tools, recording)
    (out/'truth.json').write_text(json.dumps(truth, indent=1))
    print({k: (v['case'], len(v['breaking']), len(v['indirect'])) for k, v in truth.items()})
