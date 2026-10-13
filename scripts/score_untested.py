"""Score untested[] rows from history, with no hand labels. Static only: nothing upstream is executed.

For every screened Django commit that broke no existing test and changes production code, AMU analyses the
production-only diff on the parent tree. Each changed function is either an untested row or a covered one.

(a) Author agreement: the same commit added or modified a test that reaches the function in the commit's tree.
(b) Later bug: a later commit whose message says fix, bug, regression or revert changed the same function.
    Django prefixes most subjects with "Fixed #NNNN --"; that prefix is stripped before matching.
"""
import argparse
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'scripts'))
from before_tests_harness import functions, git  # noqa: E402

from amu.core import analyze  # noqa: E402
from amu.core import index as core_index  # noqa: E402

DATA = ROOT/'evaluation/before-tests/phase0'
BUG = re.compile(r'\b(fix(ed|es|ing)?|bug|regression|revert(ed|s)?)\b', re.I)
PREFIX = re.compile(r'^\s*(\[[^\]]+\]\s*)?((Fixed|Refs)\s+#\d+(,\s*#\d+)*\s*(--|:)?\s*)+', re.I)
HUNK = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+', re.M)


def checkout(repo, work, commit):
    env = dict(os.environ, GIT_INDEX_FILE=str(work.parent/'worktree.index'))
    subprocess.run(['git', '--git-dir=' + str(repo), '--work-tree=' + str(work), 'read-tree', '--reset', '-u', commit], check=True, env=env)


def touched_functions(repo, commit):
    """{(file, function qualname)} whose parent-side lines a commit changed."""
    found = set()
    try:
        parent = git(repo, 'rev-parse', commit + '^').decode().strip()
        names = [n for n in git(repo, 'diff', '--name-only', parent, commit).decode().splitlines() if n.startswith('django/') and n.endswith('.py')]
    except subprocess.CalledProcessError:
        return found
    for name in names:
        try:
            tree = ast.parse(git(repo, 'show', parent + ':' + name))
            diff = git(repo, 'diff', '-U0', parent, commit, '--', name).decode(errors='replace')
        except (subprocess.CalledProcessError, SyntaxError):
            continue
        lines = set()
        for start, count in HUNK.findall(diff):
            lines.update(range(int(start), int(start) + max(int(count or 1), 1)))
        def visit(body, prefix=''):
            for node in body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    qual = prefix + node.name
                    if not isinstance(node, ast.ClassDef) and any(node.lineno <= line <= node.end_lineno for line in lines):
                        found.add((name, qual))
                    visit(node.body, qual + '.')
        visit(tree.body)
    return found


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--repo', required=True)
    p.add_argument('--work', required=True, help='scratch directory outside this repository')
    a = p.parse_args()
    repo, work = Path(a.repo), Path(a.work)
    tree, index_dir = work/'tree', work/'index'
    tree.mkdir(parents=True, exist_ok=True)
    core_index.STATE_KEEP = 3
    protocol = json.loads((DATA/'protocol.json').read_text())
    history = git(repo, 'rev-list', '--no-merges', protocol['head']).decode().splitlines()
    position = {commit: i for i, commit in enumerate(history)}
    screened = [next(c for c in history if c.startswith(short)) for short, reason in protocol['screened']
                if 'breaking' not in reason.replace('no existing test breaks', '') and reason != 'kept']
    rows, log = [], []
    for step, commit in enumerate(reversed(screened)):
        parent = git(repo, 'rev-parse', commit + '^').decode().strip()
        paths = git(repo, 'diff', '--name-only', parent, commit).decode().splitlines()
        prod = [n for n in paths if n.startswith('django/') and n.endswith('.py')]
        if not prod:
            log.append([commit[:10], 'no production change'])
            continue
        if step % 25 == 0:
            for old in index_dir.glob('*/state-*.json'):
                old.unlink()  # bound the drift of repeated relinking with a periodic full rebuild
        patch = git(repo, 'diff', parent, commit, '--', *prod).decode(errors='replace')
        checkout(repo, tree, parent)
        report = analyze(tree, patch, full_evidence=True, coverage_evidence='none', index_dir=index_dir)
        untested = {(row['file'], row['symbol']): row['basis'] for row in report['untested']}
        changed_tests = set()
        for name in paths:
            if name.startswith('tests/') and name.endswith('.py'):
                try:
                    after = functions(git(repo, 'show', commit + ':' + name), name)
                except (subprocess.CalledProcessError, SyntaxError):
                    continue
                try:
                    before = functions(git(repo, 'show', parent + ':' + name), name)
                except (subprocess.CalledProcessError, SyntaxError):
                    before = {}
                changed_tests.update(k.replace('::', '#', 1).replace('::', '.') for k, v in after.items() if before.get(k) != v)
        checkout(repo, tree, commit)
        graph = core_index.state(tree, index_dir)[4]
        for row in report['changed_functions']:
            ident = row['file'] + '#' + row['symbol']
            reach = graph.reach([ident]) if ident in graph.records else {}
            rows.append(dict(commit=commit, symbol=row['symbol'], file=row['file'], lines=row['lines'],
                             untested=(row['file'], row['symbol']) in untested, basis=untested.get((row['file'], row['symbol'])),
                             tests_reaching_in_parent=report['caveats'] and None, author_test=bool(changed_tests & set(reach)),
                             commit_changed_tests=len(changed_tests), followers=position[commit]))
        log.append([commit[:10], f"{len(report['changed_functions'])} changed functions, {len(report['untested'])} untested"])
        print(*log[-1], flush=True)
    fixes = {}
    for commit in history:
        message = git(repo, 'log', '-1', '--format=%s%n%b', commit).decode(errors='replace')
        if BUG.search(PREFIX.sub('', message, count=1)):
            fixes[commit] = touched_functions(repo, commit)
    for row in rows:
        row['later_fix'] = [c[:10] for c, touched in fixes.items()
                            if position[c] < position[row['commit']] and (row['file'], row['symbol']) in touched]
    (DATA/'untested-rows.json').write_text(json.dumps(dict(rows=rows, log=log, bugfix_commits=len(fixes), history=len(history)), indent=1) + '\n')
    for label, group in (('untested', [r for r in rows if r['untested']]), ('covered', [r for r in rows if not r['untested']])):
        n = len(group) or 1
        print(f"{label}: rows {len(group)} | author added or changed a reaching test {sum(r['author_test'] for r in group)} ({sum(r['author_test'] for r in group)/n:.0%})"
              f" | later bug-fix touched it {sum(bool(r['later_fix']) for r in group)} ({sum(bool(r['later_fix']) for r in group)/n:.0%})")
