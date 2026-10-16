"""Build the slow-suite trial's workspaces: one git repository per task, arm and trial, holding the parent tree.

Usage: build_workspaces.py ROOT RECORDING_OBSERVED_DIR [task ...]
ROOT gets ws/<id>/ (what the agent sees: the tree, ./rt, TASK.md) and runs/<id>/ (ledger, hook configuration, hook
log, AMU index; never shown to the agent). Both arms have a hook with the same triggers: the dumb hook
(`dumb_hook.py`, no AMU) in the control arm, bare amu (`hook.py`) in the AMU arm. Workspace ids are shuffled so a path
does not name the arm. The numbers in rules.json are the trial's rules; this script only reads them.
"""
import json
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BLIND = HERE.parent/'blind'
RULES = json.loads((HERE/'rules.json').read_text())


def tree_of(row):
    """The task's parent tree: under rules.json cases_dir when set (regenerated from the frozen commits after the
    scratch area was lost in a restart; each production patch matched its frozen sha256), else the manifest's path."""
    return Path(RULES['cases_dir'])/row['case_id']/'parent' if RULES.get('cases_dir') else Path(row['tree'])

RT = r'''#!/opt/homebrew/bin/python3
"""Run sympy's tests in this workspace. Usage: ./rt [-k EXPRESSION] [PATH_OR_TEST ...]
  ./rt                                              the full suite (about %(suite_minutes)s minutes)
  ./rt sympy/core                                   one package's tests
  ./rt sympy/core/tests/test_basic.py               one test module
  ./rt sympy/core/tests/test_basic.py::test_equality    one test
  ./rt sympy/core/tests/test_basic.py -k equality   tests matching an expression
sympy's own configuration applies: tests marked slow or tooslow are not run. Tests run one after another.
Every run is charged its running time against this task's test-time budget of %(budget)d seconds."""
import sys
sys.path.insert(0, %(tools)r)
import djrun, symrun
HERE = %(workspace)r
ledger = djrun.Ledger(%(ledger)r, %(budget)d)
if __name__ == "__main__":
    args = sys.argv[1:]
    if any(a in ("-h", "--help") for a in args):
        sys.exit(__doc__)
    labels, extra = [], []
    while args:
        arg = args.pop(0)
        if arg == "-k" and args:
            extra += ["-k", args.pop(0)]
        elif arg.startswith("-"):
            sys.exit(f"./rt takes test paths, test ids and -k EXPRESSION only, not {arg}; see ./rt --help")
        else:
            labels.append(arg)
    left = ledger.left()
    if left <= 1:
        sys.exit(f"test-time budget exhausted: {ledger.spent():.0f} of {ledger.budget} s used; no more tests can be run")
    result = symrun.run(%(python)r, HERE, labels, timeout=left, home=%(home)r, extra=extra)
    states = result["states"]
    failed = sorted(k for k, v in states.items() if v == "failed")
    ledger.charge("rt", result["seconds"], labels=labels or ["full suite"], k=extra[1:] or None, problems=result["problems"],
                  ran=len(states), failed=failed)
    print("\n".join(symrun.failures(result["output"])))
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

HOOKS = {'dumb': 'dumb_hook.py', 'amu': 'hook.py'}


def build(root, ident, row, text, arm, recording):
    work, run = root/'ws'/ident, root/'runs'/ident
    for path in (work, run):
        shutil.rmtree(path, ignore_errors=True)
    run.mkdir(parents=True)
    shutil.copytree(tree_of(row), work, ignore=shutil.ignore_patterns('__pycache__', '.github', '.amu', '.hypothesis', '.pytest_cache'))
    (work/'TASK.md').write_text(text)
    (work/'rt').write_text(RT % dict(tools=str(root/'tools'), workspace=str(work), ledger=str(run/'ledger.json'), budget=RULES['budget_seconds'],
                                     python=str(root/'env/bin/python'), home=str(run/'home'), suite_minutes=RULES['suite_minutes_told']))
    os.chmod(work/'rt', 0o755)
    git = ['git', '-C', str(work), '-c', 'user.email=trial@example.invalid', '-c', 'user.name=trial']
    subprocess.run([*git, 'init', '-q'], check=True)
    with open(work/'.git/info/exclude', 'a') as handle:
        handle.write('rt\nTASK.md\n.hypothesis/\n')
    for args in (['add', '-A'], ['commit', '-qm', 'sympy, before the task']):
        subprocess.run([*git, *args], check=True, stdout=subprocess.DEVNULL)
    config = dict(workspace=str(work), run=str(run), tools=str(root/'tools'), python=str(root/'env/bin/python'), budget=RULES['budget_seconds'],
                  arm=arm, amu=str(HERE.parents[2]), amu_args=RULES['amu_args'], label_format='pytest')
    (run/'config.json').write_text(json.dumps(config, indent=1))
    command = f"/opt/homebrew/bin/python3 {root/'tools'/HOOKS[arm]} {run/'config.json'}"
    hook = [dict(type='command', command=command, timeout=RULES['budget_seconds'] + 60)]
    # The same triggers in both arms: after file edits and after any shell command (fires when a .py file changed);
    # before any shell command (records the tree, and fires when the command runs the tests).
    settings = dict(hooks=dict(PostToolUse=[dict(matcher='Edit|Write|MultiEdit|Bash', hooks=hook)], PreToolUse=[dict(matcher='Bash', hooks=hook)]))
    if arm == 'amu':
        # Pre-placed, uncharged, before the session: the coverage recording (a CI artefact) and the index of this
        # workspace's tree (in real use it persists per repository), built where the hook's checks look for it.
        version = Path(recording).parent.name
        (run/'amu-index'/version).mkdir(parents=True)
        os.symlink(recording, run/'amu-index'/version/'observed')
        started = time.time()
        subprocess.run(['/opt/homebrew/bin/python3', str(HERE/'prebuild_index.py'), str(work), str(run/'amu-index')],
                       env=dict(os.environ, PYTHONPATH=str(HERE.parents[2])), check=True, capture_output=True)
        (run/'index-build.json').write_text(json.dumps(dict(seconds=round(time.time() - started, 1), charged=False)))
    (run/'settings.json').write_text(json.dumps(settings, indent=1))


if __name__ == '__main__':
    root, recording, only = Path(sys.argv[1]), sys.argv[2], sys.argv[3:]
    manifest = {row['case_id']: row for row in json.loads((HERE/'sympy.json').read_text())}
    tasks = json.loads((HERE/'tasks.json').read_text())
    (root/'tools').mkdir(parents=True, exist_ok=True)
    for source in (BLIND/'djrun.py', BLIND/'hook.py', BLIND/'dumb_hook.py', HERE/'symrun.py'):
        shutil.copy(source, root/'tools'/source.name)
    slots = [(task, arm, trial) for task in tasks for arm in ('dumb', 'amu') for trial in (1, 2)]
    names = [f'w{n:02d}' for n in range(1, len(slots) + 1)]
    random.Random(RULES['seed']).shuffle(names)
    mapping = {}
    for ident, (task, arm, trial) in zip(names, slots):
        mapping[ident] = dict(task=task, arm=arm, trial=trial, case=tasks[task]['case'])
        if not only or task in only:
            build(root, ident, manifest[tasks[task]['case']], tasks[task]['text'], arm, recording)
    (root/'runs'/'map.json').write_text(json.dumps(mapping, indent=1))
    print(json.dumps(mapping))
