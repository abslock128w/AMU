"""Run the blind trial's agents: headless Claude Code, one process per workspace.

Usage: run_trial.py ROOT [--only ID ...] [--pairs N]
Both arms of a task and trial run at the same time, so they share machine load. The prompt is the same for
both arms; the only difference is runs/<id>/settings.json, which holds the PostToolUse hook for the AMU arm
and is empty for the control arm. Nothing from the user's own Claude settings, memory or MCP servers is loaded.
"""
import argparse
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
RULES = json.loads((HERE/'rules.json').read_text())

PROMPT = """You are working in {workspace}. It is a git repository holding the Django web framework: source in django/, tests in tests/, documentation in docs/. HEAD is the state before your work.

Your task is in TASK.md: implement that change in this repository.

Make sure your change does not break anything. Behaviour the task does not ask to change must keep working, and the existing tests must pass when you are done. Where the task changes behaviour on purpose, update the tests that describe the old behaviour and add tests for the new behaviour.

Rules:
- Run tests only with ./rt (see ./rt --help). ./rt with no arguments runs the full suite, which takes about {suite} minutes. Do not call runtests.py, pytest or python yourself.
- You have a budget of {budget} seconds of test-execution time. Every test run in this workspace is charged its running time to that budget, whether you start it or an automatic check does, and the amount used is printed each time. When it is used up no more tests can be run. Time you spend reading, thinking and editing is not limited.
- Work only inside this directory. Do not read anything outside it, do not use the network, and do not use git to fetch, pull or clone.
- Leave your work uncommitted in the working tree.

When you are finished, end your reply with a single JSON object on the last line:
{{"done": true, "tests_run": ["the labels you ran, or 'full suite'"], "notes": "one sentence"}}"""

ALLOWED = ['Read', 'Edit', 'Write', 'Grep', 'Glob', 'Bash']
DENIED = ['WebFetch', 'WebSearch', 'Agent', 'Task', 'Workflow', 'Skill', 'NotebookEdit', 'ToolSearch', 'Artifact', 'CronCreate', 'ScheduleWakeup', 'SendMessage', 'Monitor', 'EnterWorktree']


def launch(root, ident):
    work, run = root/'ws'/ident, root/'runs'/ident
    if (run/'result.json').exists():
        return ident, 'already run'
    prompt = PROMPT.format(workspace=work, suite=RULES['suite_minutes_told'], budget=RULES['budget_seconds'])
    (run/'prompt.txt').write_text(prompt)
    # The desktop app's own variables point the CLI at this session; the agents use the CLI's own login.
    env = {k: v for k, v in os.environ.items() if not k.startswith(('CLAUDE', 'ANTHROPIC'))}
    command = ['claude', '-p', prompt, '--model', RULES['model'], '--setting-sources', '', '--settings', str(run/'settings.json'),
               '--strict-mcp-config', '--allowedTools', *ALLOWED, '--disallowedTools', *DENIED, '--output-format', 'stream-json', '--verbose']
    started, killed = time.time(), False
    with open(run/'transcript.jsonl', 'w') as out, open(run/'stderr.txt', 'w') as err:
        proc = subprocess.Popen(command, cwd=work, env=env, stdout=out, stderr=err, stdin=subprocess.DEVNULL)
        try:
            proc.wait(timeout=RULES['hard_cap_seconds'])
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            killed = True
    ended = time.time()
    git = ['git', '-C', str(work)]
    subprocess.run([*git, 'add', '-A'], check=True)
    diff = subprocess.run([*git, 'diff', '--cached', 'HEAD', '--binary'], check=True, capture_output=True).stdout
    (run/'agent.diff').write_bytes(diff)
    (run/'result.json').write_text(json.dumps(dict(started=started, ended=ended, wall_seconds=round(ended - started, 1), exit=proc.returncode,
                                                   stopped_at_hard_cap=killed), indent=1))
    return ident, f'{ended - started:.0f} s, exit {proc.returncode}'


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('root')
    p.add_argument('--only', nargs='*', help='workspace ids; default all, trial 1 of every task first')
    p.add_argument('--pairs', type=int, default=RULES['pairs_at_once'])
    a = p.parse_args()
    root = Path(a.root)
    mapping = json.loads((root/'runs'/'map.json').read_text())
    pairs = {}
    for ident, slot in mapping.items():
        if not a.only or ident in a.only:
            pairs.setdefault((slot['trial'], slot['task']), []).append(ident)

    def both(key):
        with ThreadPoolExecutor(2) as pool:
            return key, list(pool.map(lambda ident: launch(root, ident), pairs[key]))

    with ThreadPoolExecutor(a.pairs) as pool:
        for key, done in pool.map(both, sorted(pairs)):
            print(key, done, flush=True)
