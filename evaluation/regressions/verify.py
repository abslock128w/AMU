"""Verify regression candidates: the later test must pass on the culprit PR's merge base, fail on its head, and pass on
the fix commit. The test files the fix commit adds or changes are laid over each tree (a test that does not exist yet
at base or head is taken from the fix).

Usage: python verify.py --candidates candidates.json --out verified.jsonl [--only ID ...] [--timeout 600]
Each output row: id, repo, outcomes {base, head, fix} as pass/fail/error/missing, verdict confirmed/rejected/unrunnable,
and the tail of each run's output. Rows already in --out are skipped.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

W = Path(__file__).resolve().parent.parent
CLONES = dict(django=W/'repos'/'django', sympy=W/'repos'/'sympy', flask=W/'repos'/'flask', attrs=W/'attrs',
              httpx=W/'httpx', jinja=W/'jinja', requests=W/'requests')
PYTHONS = dict(django=W/'env'/'django', sympy=W/'env'/'sympy', flask=W/'env'/'flask', attrs=W/'attrsvenv',
               httpx=W/'httpxvenv', jinja=W/'jinjavenv', requests=W/'requestsvenv')


def git(repo, *args, check=False):
    done = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True)
    if check and done.returncode:
        raise RuntimeError(done.stderr)
    return done.stdout.strip()


def cid(c):
    return f"{c['repo']}:{(c.get('culprit_pr') or c['culprit_sha'][:10])}:{c['fix_sha'][:10]}"


def test_files(clone, fix):
    """Test files the fix commit adds or modifies."""
    names = git(clone, 'diff-tree', '--no-commit-id', '--name-status', '-r', f'{fix}^', fix).splitlines()
    return [line.split('\t')[-1] for line in names if line[:1] in 'AM' and re.search(r'(^|/)tests?(/|_)|_test\.py$|/test_', line)]


def command(repo, tree, python, tests):
    if repo == 'django':
        labels = [t.replace('tests/', '', 1) if t.startswith('tests/') else t for t in tests]
        return [str(python), 'tests/runtests.py', '--parallel', '1', '--noinput', '-v', '2', *labels]
    return [str(python), '-m', 'pytest', '-q', '-rA', '-p', 'no:cacheprovider', '-o', 'addopts=', *tests]


def per_test(repo, text):
    """Each test's own outcome, read from the runner's output: pass or fail (errors count as fail)."""
    out = {}
    if repo == 'django':
        for name, dotted, result in re.findall(r'^(\w+) \(([\w.]+)\)[^\n]*? \.\.\. (ok|FAIL|ERROR|skipped|expected failure)', text, re.M):
            out[dotted if dotted.endswith(name) else f'{dotted}.{name}'] = dict(ok='pass', FAIL='fail', ERROR='fail').get(result, 'skip')
    else:
        for result, node in re.findall(r'^(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS) (\S+)', text, re.M):
            out[node] = dict(PASSED='pass', FAILED='fail', ERROR='fail').get(result, 'skip')
    return out


def outcome(repo, done):
    text = done.stdout + done.stderr
    if done.returncode == 0:
        return 'pass'
    if re.search(r'(ERROR: (file or directory )?not found|no tests ran|ImportError|ModuleNotFoundError|SyntaxError|'
                 r'errors? during collection|AttributeError: module .* has no attribute)', text) and not re.search(
                 r'\bFAILED\b|^FAIL:', text, re.M):
        return 'error'
    return 'fail'


def run_at(c, label, commit, files, timeout):
    clone, python = CLONES[c['repo']], PYTHONS[c['repo']]/'bin'/'python'
    tree = W/'regwork'/'verify'/label
    git(clone, 'worktree', 'remove', '--force', str(tree))
    shutil.rmtree(tree, ignore_errors=True)
    tree.parent.mkdir(parents=True, exist_ok=True)
    git(clone, 'worktree', 'add', '--detach', '-q', str(tree), commit, check=True)
    # The fix's own change to its test files, applied 3-way; a file it will not apply to is copied whole from the fix.
    method = 'none'
    if files:
        patch = subprocess.run(['git', '-C', str(clone), 'diff', f"{c['fix_sha']}^", c['fix_sha'], '--', *files],
                               capture_output=True).stdout
        applied = subprocess.run(['git', '-C', str(tree), 'apply', '--3way', '-'], input=patch, capture_output=True)
        method = 'diff'
        if applied.returncode or git(tree, 'diff', '--name-only', '--diff-filter=U'):
            git(tree, 'checkout', '-f', 'HEAD', '--', '.')
            git(tree, 'clean', '-fdq')
            method = 'whole-file'
            for f in files:
                (tree/f).parent.mkdir(parents=True, exist_ok=True)
                (tree/f).write_bytes(subprocess.run(['git', '-C', str(clone), 'show', f"{c['fix_sha']}:{f}"], capture_output=True).stdout)
    env = dict({k: v for k, v in os.environ.items() if not k.startswith('ANTHROPIC_')},
               PYTHONPATH=os.pathsep.join([str(tree/'src'), str(tree)]), PYTHONDONTWRITEBYTECODE='1')
    cwd = tree/'tests' if c['repo'] == 'django' else tree
    cmd = command(c['repo'], tree, python, c['tests'])
    if c['repo'] == 'django':
        cmd[1] = 'runtests.py'
    try:
        done = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env, timeout=timeout)
        result = outcome(c['repo'], done), f'[{method}] ' + (done.stdout + done.stderr)[-1500:], per_test(c['repo'], done.stdout + done.stderr)
    except subprocess.TimeoutExpired:
        result = 'error', 'timeout', {}
    git(clone, 'worktree', 'remove', '--force', str(tree))
    return result


def main():
    a = argparse.ArgumentParser()
    a.add_argument('--candidates', required=True)
    a.add_argument('--out', required=True)
    a.add_argument('--only', nargs='*')
    a.add_argument('--timeout', type=int, default=600)
    a.add_argument('--stop-file', help='stop before the next candidate once this file exists')
    args = a.parse_args()
    out = Path(args.out)
    done = {json.loads(line)['id'] for line in out.read_text().splitlines()} if out.exists() else set()
    for c in json.loads(Path(args.candidates).read_text()):
        key = cid(c)
        if args.stop_file and Path(args.stop_file).exists():
            break
        if key in done or (args.only and key not in args.only) or not c.get('tests'):
            continue
        clone = CLONES[c['repo']]
        for sha in (c['base'], c['head'], c['fix_sha']):
            if git(clone, 'cat-file', '-t', sha) != 'commit':
                git(clone, 'fetch', '-q', 'origin', sha)
        files = test_files(clone, c['fix_sha'])
        outcomes, tails, tests = {}, {}, {}
        for side, commit in (('base', c['base']), ('head', c['head']), ('fix', c['fix_sha'])):
            outcomes[side], tails[side], tests[side] = run_at(c, f"{c['repo']}-{side}", commit, files, args.timeout)
        regressed = sorted(t for t in tests['head'] if tests['head'][t] == 'fail' and tests['base'].get(t) == 'pass'
                           and tests['fix'].get(t) == 'pass')
        if regressed:
            verdict = 'confirmed'
        elif 'error' in (outcomes['head'], outcomes['fix'], outcomes['base']):
            verdict = 'unrunnable'
        else:
            verdict = 'rejected'
        row = dict(id=key, repo=c['repo'], culprit_pr=c.get('culprit_pr'), base=c['base'], head=c['head'], fix=c['fix_sha'],
                   tests=c['tests'], overlaid=files, outcomes=outcomes, regressed_tests=regressed, per_test=tests, verdict=verdict, tails=tails)
        with out.open('a') as handle:
            handle.write(json.dumps(row) + '\n')
        print(json.dumps(dict(id=key, outcomes=outcomes, regressed=regressed, verdict=verdict)), flush=True)


if __name__ == '__main__':
    main()
