"""Tests that would catch a raise, missed: for one change, the tests that fail when its changed lines raise, less the
tests AMU had as candidates on the head.

Usage: python raise_missed.py --head TREE --patch PATCH --report REPORT.json --python PY [--runner pytest|django-runtests]
       [--workers N]

Each hunk's first changed statement on the head gets ``raise RuntimeError('AMU-RAISE <path>:<line>')`` in front of it,
at its indentation; a statement that starts a definition gets it as the first line of its body instead, so it raises
when called. Statements at module or class level are not given one (a raise at import fails every test importing the
module, which says nothing about one line); they are counted under ``not_injected``. Then the whole suite runs once on
the head copy with every raise in place, and a test that fails with the marker in its failure text is a catcher. A raise
in one hunk can keep another from running in the same test, so the catchers are a lower bound per hunk; their union is
what is compared.

Prints JSON: catchers, candidates, missed (catchers not among the candidates), and per hunk where its raise went.
"""
import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

MARK = 'AMU-RAISE'


def hunks(patch):
    """{path: [first added line of each hunk]} for production .py files, from a unified diff."""
    found, path, new_line, first = {}, None, 0, None
    for line in patch.splitlines():
        if line.startswith('+++ '):
            raw = line[4:].split('\t')[0]
            path = raw[2:] if raw.startswith('b/') else None if raw == '/dev/null' else raw
            if path and (not path.endswith('.py') or re.search(r'(^|/)(tests?|testing)/|(^|/)test_[^/]*\.py$|_test\.py$|conftest\.py$', path)):
                path = None
        elif line.startswith('@@') and path:
            new_line = int(re.match(r'@@ -\d+(?:,\d+)? \+(\d+)', line)[1])
            first = None
        elif path and line.startswith('+') and not line.startswith('+++'):
            if first is None:
                first = new_line
                found.setdefault(path, []).append(new_line)
            new_line += 1
        elif path and line.startswith(' '):
            new_line += 1
    return found


def inject(text, lines, path):
    """``text`` with a raise before the first statement starting on or after each of ``lines`` within its hunk."""
    tree = ast.parse(text)
    statements = []
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
        if isinstance(node, ast.stmt):
            statements.append(node)
    rows, where = text.splitlines(True), []
    inserts = {}
    for line in lines:
        hit = sorted((s for s in statements if s.lineno >= line), key=lambda s: (s.lineno, -s.col_offset))
        if not hit or hit[0].lineno > line + 30:
            where.append(dict(line=line, at=None, why='no statement'))
            continue
        node = hit[0]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body[0]
            if isinstance(body, ast.Expr) and isinstance(getattr(body, 'value', None), ast.Constant) and len(node.body) > 1:
                body = node.body[1]
            node = body
        owner = parents.get(node)
        if isinstance(owner, (ast.Module, ast.ClassDef)):
            where.append(dict(line=line, at=None, why='module or class level'))
            continue
        indent = re.match(r'\s*', rows[node.lineno - 1])[0]
        inserts[node.lineno] = f"{indent}raise RuntimeError('{MARK} {path}:{line}')\n"
        where.append(dict(line=line, at=node.lineno))
    for at in sorted(inserts, reverse=True):
        rows.insert(at - 1, inserts[at])
    return ''.join(rows), where


def environment(tree):
    # The copy with the raises is what imports: its src/ and root first, ahead of any editable install of the original.
    return dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, [str(tree/'src'), str(tree), os.environ.get('PYTHONPATH')])),
                PYTHONDONTWRITEBYTECODE='1')


def catchers_pytest(python, tree, workers, report):
    command = [python, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-p', 'no:randomly', f'--junitxml={report}']
    if workers > 1:
        command += ['-n', str(workers)]
    subprocess.run(command, cwd=tree, capture_output=True, timeout=6 * 3600, env=environment(tree))
    found = set()
    for case in ET.parse(report).getroot().iter('testcase'):
        said = ''.join((c.get('message') or '') + (c.text or '') for c in case if c.tag in ('failure', 'error'))
        if MARK not in said:
            continue
        parts = case.get('classname', '').split('.')
        for cut in range(len(parts), 0, -1):
            if (tree/('/'.join(parts[:cut]) + '.py')).is_file():
                found.add('::'.join(['/'.join(parts[:cut]) + '.py', *parts[cut:], case.get('name', '').split('[')[0]]))
                break
    return found


def catchers_django(python, tree, workers):
    done = subprocess.run([python, 'runtests.py', '-v', '2', '--noinput', f'--parallel={workers}'], cwd=tree/'tests',
                          capture_output=True, text=True, timeout=6 * 3600, env=environment(tree))
    output = done.stdout + done.stderr
    found = set()
    for block in re.split(r'^={20,}$', output, flags=re.M)[1:]:
        match = re.search(r'^(?:ERROR|FAIL): (\w+) \(([\w.]+)\)', block, re.M)
        if not match or MARK not in block:
            continue
        label = match[2]
        parts = label.split('.')
        for cut in range(len(parts) - 1, 0, -1):
            if (tree/'tests'/('/'.join(parts[:cut]) + '.py')).is_file():
                found.add('::'.join(['tests/' + '/'.join(parts[:cut]) + '.py', *parts[cut:]]))
                break
    return found


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--head', required=True)
    p.add_argument('--patch', required=True)
    p.add_argument('--report', required=True)
    p.add_argument('--python', required=True)
    p.add_argument('--runner', default='pytest')
    p.add_argument('--workers', type=int, default=1)
    args = p.parse_args(argv)
    patch = Path(args.patch).read_text()
    report = json.loads(Path(args.report).read_text())
    candidates = set(report.get('candidate_sources') or {}) or {row['id'] for row in report.get('tests') or []}
    with tempfile.TemporaryDirectory(prefix='amu-raise-') as scratch:
        tree = Path(scratch)/'tree'
        shutil.copytree(args.head, tree, symlinks=True, ignore=shutil.ignore_patterns('.git', '.amu', '__pycache__'))
        placed = {}
        for path, lines in hunks(patch).items():
            text = (tree/path).read_text()
            changed, where = inject(text, lines, path)
            (tree/path).write_text(changed)
            placed[path] = where
        injected = sum(1 for rows in placed.values() for row in rows if row.get('at'))
        if args.runner == 'django-runtests':
            caught = catchers_django(args.python, tree, args.workers)
        else:
            caught = catchers_pytest(args.python, tree, args.workers, Path(scratch)/'raise.xml')
    missed = sorted(caught - candidates)
    print(json.dumps(dict(injected=injected, not_injected=sum(1 for rows in placed.values() for row in rows if not row.get('at')),
                          catchers=len(caught), candidates=len(candidates), missed=len(missed), missed_tests=missed[:50],
                          placed=placed), indent=1))


if __name__ == '__main__':
    sys.exit(main())
