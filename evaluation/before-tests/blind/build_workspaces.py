"""Build the blind-trial workspaces: one git repository per task, arm and trial, holding the parent tree.

Usage: build_workspaces.py ROOT RECORDING_OBSERVED_DIR [task ...]
ROOT gets ws/<id>/ (what the agent sees: the tree, ./rt, TASK.md) and runs/<id>/ (ledger, hook configuration,
hook log, AMU index; never shown to the agent). Workspace ids are shuffled so a path does not name the arm.
The numbers in rules.json are the trial's rules; this script only reads them.
"""
import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RULES = json.loads((HERE/'rules.json').read_text())

RT = r'''#!/opt/homebrew/bin/python3
"""Run Django's tests in this workspace. Usage: ./rt [LABEL ...]
  ./rt                      the full suite (about %(suite_minutes)s minutes)
  ./rt mail                 one test package
  ./rt mail.tests.MailTests.test_ascii    a module, class or method
Every run is charged its running time against this task's test-time budget of %(budget)d seconds."""
import sys, time
sys.path.insert(0, %(tools)r)
import djrun
HERE = %(workspace)r
ledger = djrun.Ledger(%(ledger)r, %(budget)d)
if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        sys.exit(__doc__)
    labels = []
    for arg in sys.argv[1:]:
        if "::" in arg or arg.endswith(".py"):
            path, *rest = arg.split("::")
            path = path[len("tests/"):] if path.startswith("tests/") else path
            arg = ".".join([path[:-3].replace("/", ".") if path.endswith(".py") else path, *rest])
        labels.append(arg)
    left = ledger.left()
    if left <= 1:
        sys.exit(f"test-time budget exhausted: {ledger.spent():.0f} of {ledger.budget} s used; no more tests can be run")
    result = djrun.run(%(python)r, HERE, labels, shards=%(shards)d, timeout=left, home=%(home)r)
    ledger.charge("rt", result["seconds"], labels=labels, problems=result["problems"],
                  ran=len(result["states"]), failed=sorted(k for k, v in result["states"].items() if v == "failed"))
    states = result["states"]
    failed = sorted(k for k, v in states.items() if v == "failed")
    print("\n".join(djrun.failures(result["output"])))
    for problem in result["problems"]:
        print("PROBLEM:", problem)
    if not states and not result["problems"]:
        print("\n".join(result["output"].splitlines()[-30:]))
    print(f"ran {len(states)} tests in {result['seconds']:.0f} s: {sum(v == 'passed' for v in states.values())} passed, "
          f"{len(failed)} failed, {sum(v == 'skipped' for v in states.values())} skipped")
    for name in failed[:60]:
        print("FAILED", name)
    print(f"[test time: {ledger.spent():.0f} of {ledger.budget} s used]")
'''


def build(root, ident, row, text, arm, recording):
    work, run = root/'ws'/ident, root/'runs'/ident
    for path in (work, run):
        shutil.rmtree(path, ignore_errors=True)
    run.mkdir(parents=True)
    shutil.copytree(row['tree'], work, ignore=shutil.ignore_patterns('__pycache__', '.github', '.amu'))
    (work/'TASK.md').write_text(text)
    (work/'rt').write_text(RT % dict(tools=str(root/'tools'), workspace=str(work), ledger=str(run/'ledger.json'), budget=RULES['budget_seconds'],
                                     python=str(root/'env/bin/python'), shards=RULES['rt_shards'], home=str(run/'home'),
                                     suite_minutes=RULES['suite_minutes_told']))
    os.chmod(work/'rt', 0o755)
    git = ['git', '-C', str(work), '-c', 'user.email=trial@example.invalid', '-c', 'user.name=trial']
    subprocess.run([*git, 'init', '-q'], check=True)
    with open(work/'.git/info/exclude', 'a') as handle:
        handle.write('rt\nTASK.md\n')
    for args in (['add', '-A'], ['commit', '-qm', 'Django, before the task']):
        subprocess.run([*git, *args], check=True, stdout=subprocess.DEVNULL)
    config = dict(workspace=str(work), run=str(run), tools=str(root/'tools'), python=str(root/'env/bin/python'), budget=RULES['budget_seconds'],
                  arm=arm, amu=str(HERE.parents[2]), amu_args=RULES['amu_args'])
    (run/'config.json').write_text(json.dumps(config, indent=1))
    settings = {}
    if arm == 'amu':
        command = f"/opt/homebrew/bin/python3 {root/'tools'/'hook.py'} {run/'config.json'}"
        hook = [dict(type='command', command=command, timeout=RULES['budget_seconds'] + 60)]
        # After file edits and after any shell command (fires when a .py file changed); before any shell command
        # (records the tree, and fires when the command runs the tests).
        settings = dict(hooks=dict(PostToolUse=[dict(matcher='Edit|Write|MultiEdit|Bash', hooks=hook)], PreToolUse=[dict(matcher='Bash', hooks=hook)]))
        # The recording is the only thing pre-placed in the index; facts and graph are built on the clock.
        version = Path(recording).parent.name
        (run/'amu-index'/version).mkdir(parents=True)
        os.symlink(recording, run/'amu-index'/version/'observed')
    (run/'settings.json').write_text(json.dumps(settings, indent=1))


if __name__ == '__main__':
    root, recording, only = Path(sys.argv[1]), sys.argv[2], sys.argv[3:]
    manifest = {row['case_id']: row for row in json.loads((HERE.parent/'phase0/blind.json').read_text())}
    tasks = json.loads((HERE/'tasks.json').read_text())
    (root/'tools').mkdir(parents=True, exist_ok=True)
    for name in ('djrun.py', 'hook.py'):
        shutil.copy(HERE/name, root/'tools'/name)
    slots = [(task, arm, trial) for task in tasks for arm in ('control', 'amu') for trial in (1, 2)]
    names = [f'w{n:02d}' for n in range(1, len(slots) + 1)]
    random.Random(RULES['seed']).shuffle(names)
    mapping = {}
    for ident, (task, arm, trial) in zip(names, slots):
        mapping[ident] = dict(task=task, arm=arm, trial=trial, case=tasks[task]['case'])
        if not only or task in only:
            build(root, ident, manifest[tasks[task]['case']], tasks[task]['text'], arm, recording)
    (root/'runs'/'map.json').write_text(json.dumps(mapping, indent=1))
    print(json.dumps(mapping))
