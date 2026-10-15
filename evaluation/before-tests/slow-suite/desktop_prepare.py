"""Prepare the 32 workspaces for desktop-app sessions.

Writes ws/<id>/.claude/settings.json (the arm's hook: the dumb hook or the AMU hook, with the same triggers, plus the
same permission rules in both arms so that ./rt and read-only shell commands run without a prompt) and ws/<id>/PROMPT.md
(the prompt from TRIAL-RECORD.md). Both are excluded from git so `git status` stays clean.
Usage: desktop_prepare.py ROOT
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent/'blind'))
from desktop_prepare import ALLOW, DENY  # noqa: E402  (the permission rules of the earlier trials, unchanged)

RULES = json.loads((HERE/'rules.json').read_text())

PROMPT = """You are working in {workspace}. It is a git repository holding the SymPy library: source in sympy/, each package's tests in its tests/ directory (for example sympy/core/tests/), documentation in doc/. HEAD is the state before your work.

Your task is in TASK.md: implement that change in this repository.

Make sure your change does not break anything. Behaviour the task does not ask to change must keep working, and the existing tests must pass when you are done. Where the task changes behaviour on purpose, update the tests that describe the old behaviour and add tests for the new behaviour.

Rules:
- Run tests only with ./rt (see ./rt --help). ./rt with no arguments runs the full suite, which takes about {suite} minutes. Do not call bin/test, pytest or python yourself.
- You have a budget of {budget} seconds of test-execution time. Every test run in this workspace is charged its running time to that budget, whether you start it or an automatic check does, and the amount used is printed each time. When it is used up no more tests can be run. Time you spend reading, thinking and editing is not limited.
- Work only inside this directory. Do not read anything outside it, do not use the network, and do not use git to fetch, pull or clone.
- Leave your work uncommitted in the working tree.

When you are finished, end your reply with a single JSON object on the last line:
{{"done": true, "tests_run": ["the paths or test ids you ran, or 'full suite'"], "notes": "one sentence"}}"""

if __name__ == '__main__':
    root = Path(sys.argv[1])
    for ident, slot in json.loads((root/'runs'/'map.json').read_text()).items():
        work, run = root/'ws'/ident, root/'runs'/ident
        if not (run/'settings.json').exists():
            continue
        hooks = json.loads((run/'settings.json').read_text()).get('hooks', {})
        (work/'.claude').mkdir(exist_ok=True)
        (work/'.claude'/'settings.json').write_text(json.dumps(dict(hooks=hooks, permissions=dict(allow=ALLOW, deny=DENY)), indent=1))
        prompt = PROMPT.format(workspace=work, suite=RULES['suite_minutes_told'], budget=RULES['budget_seconds'])
        (work/'PROMPT.md').write_text(prompt + '\n')
        (run/'prompt.txt').write_text(prompt)
        exclude = work/'.git/info/exclude'
        if '.claude/' not in exclude.read_text():
            exclude.write_text(exclude.read_text() + '.claude/\nPROMPT.md\n')
    print('prepared', len(list((root/'ws').iterdir())))
