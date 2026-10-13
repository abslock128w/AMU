"""Phase 0 holdout: commits whose change breaks an existing test that lexical search would not surface.

Executes the upstream repository's tests (owner approved Django on 2026-10-13). For each commit, walking
backwards from the frozen HEAD, the test modules the commit itself touched are run on the parent tree and
again with only the production part of the commit applied. A test that passed and now fails is breaking.
A breaking test outside the 40 tests most lexically similar to the diff is indirect. The first commits with
an indirect breaking test are kept. AMU's reachability ranking is never consulted during selection.
"""
import argparse
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'scripts'))
from before_tests_harness import functions, git  # noqa: E402

from amu.core import rank  # noqa: E402
from amu.core.index import build  # noqa: E402
from amu.core.patch import changes  # noqa: E402

DATA = ROOT/'evaluation/before-tests/phase0'
KEEP_LEXICAL = False
LINE = re.compile(r'^(\w+) \(([\w.]+)\)')
STATE = re.compile(r'\.\.\. (ok|FAIL|ERROR|skipped|expected failure|unexpected success)')


def run_tests(python, tree, labels, timeout=900):
    """{test id: state} from Django's runner in verbose mode."""
    env = dict(PATH='/usr/bin:/bin', HOME=str(tree.parent/'home'), PYTHONPATH=str(tree), PYTHONDONTWRITEBYTECODE='1',
               LANG='en_US.UTF-8')
    (tree.parent/'home').mkdir(exist_ok=True)
    done = subprocess.run([python, '-B', 'runtests.py', *labels, '-v', '2', '--parallel', '1', '--noinput'],
                          cwd=tree/'tests', env=env, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    found, pending, unloadable = {}, None, set()
    for line in (done.stderr + done.stdout).decode(errors='replace').splitlines():
        match = LINE.match(line)
        if match and '_FailedTest' in match[2]:
            # A module that cannot be imported: every test in it fails. Remembered by module name.
            unloadable.add(match[1])
            pending = None
            continue
        if match:
            parts = match[2].split('.')
            for cut in range(len(parts), 0, -1):
                if (tree/'tests'/('/'.join(parts[:cut]) + '.py')).is_file():
                    pending = '::'.join(['tests/' + '/'.join(parts[:cut]) + '.py', *parts[cut:]])
                    break
            else:
                pending = None
        state = STATE.search(line)
        if state and pending:
            found[pending] = 'passed' if state[1] == 'ok' else 'failed' if state[1] in ('FAIL', 'ERROR', 'unexpected success') else 'skipped'
            pending = None
    found['__unloadable__'] = sorted(unloadable)
    return found


def lexical_top(tree, patch, limit=40):
    """The tests whose names and bodies share most of the diff's vocabulary: what a text search finds."""
    facts, *_ = build(tree, tree.parent/'index')
    tests = {ident: r for f in facts.values() for ident, r in f['records'].items()
             if r['kind'] == 'function' and r['qual'].rsplit('.', 1)[-1].startswith('test_') and r['path'].startswith('tests/')}
    content = rank.Content(tests)
    edits, _ = changes(tree, patch)
    query = content.query(' '.join(text for edit in edits.values() for _, text in [*edit['added'], *edit['removed']]))
    scored = sorted(((content.score(ident, query), ident) for ident in tests), reverse=True)
    return [tests[ident]['path'] + '::' + tests[ident]['qual'].replace('.', '::') for score, ident in scored[:limit] if score > 0]


def screen(repo, commit, python, cases):
    parent = git(repo, 'rev-parse', commit + '^').decode().strip()
    paths = git(repo, 'diff', '--name-only', parent, commit).decode().splitlines()
    prod = [p for p in paths if p.startswith('django/') and p.endswith('.py')]
    touched = [p for p in paths if p.startswith('tests/') and p.endswith('.py')]
    if not prod or not touched:
        return None, 'no production or no test change'
    modified = []
    for path in touched:
        try:
            before = functions(git(repo, 'show', parent + ':' + path), path)
            after = functions(git(repo, 'show', commit + ':' + path), path)
        except (subprocess.CalledProcessError, SyntaxError):
            continue
        modified.extend(k for k in before if before[k] != after.get(k))
    if not modified:
        return None, 'no existing test modified or removed'
    case = cases/('dj-' + commit[:8])
    shutil.rmtree(case, ignore_errors=True)
    tree = case/'parent'
    tree.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(git(repo, 'archive', parent))) as archive:
        archive.extractall(tree, filter='data')
    patch = git(repo, 'diff', parent, commit, '--', *prod)
    (case/'production.patch').write_bytes(patch)
    labels = sorted({p[len('tests/'):-3].replace('/', '.') for p in touched if (tree/p).is_file()})
    try:
        before = run_tests(python, tree, labels)
        with open(case/'production.patch', 'rb') as handle:
            applied = subprocess.run(['/usr/bin/patch', '-p1', '--batch', '-s'], cwd=tree, stdin=handle, capture_output=True)
        after = run_tests(python, tree, labels) if applied.returncode == 0 else {}
        with open(case/'production.patch', 'rb') as handle:
            subprocess.run(['/usr/bin/patch', '-p1', '-R', '--batch', '-s'], cwd=tree, stdin=handle, capture_output=True)
        for junk in [*tree.rglob('*.orig'), *tree.rglob('*.rej')]:
            junk.unlink()
    except subprocess.TimeoutExpired:
        shutil.rmtree(case, ignore_errors=True)
        return None, 'test run timed out'
    gone = set(after.pop('__unloadable__', ()))
    before.pop('__unloadable__', None)
    def module_failed(test):
        parts = test.split('::')[0][len('tests/'):-3].split('/')
        return any(name in gone for name in (parts[-1], parts[0], '.'.join(parts)))
    breaking = sorted(t for t, state in before.items() if state == 'passed' and (after.get(t) == 'failed' or (t not in after and module_failed(t))))
    if not breaking:
        shutil.rmtree(case, ignore_errors=True)
        return None, f'no existing test breaks ({len(before)} run)'
    top = lexical_top(tree, patch.decode(errors='replace'))
    indirect = sorted(set(breaking) - set(top))
    shutil.rmtree(case/'index', ignore_errors=True)
    if not indirect and not KEEP_LEXICAL:
        shutil.rmtree(case, ignore_errors=True)
        return None, f'{len(breaking)} breaking, all within the lexical top 40'
    return dict(case_id=case.name, commit=commit, parent=parent, tree=str(tree), patch=str(case/'production.patch'),
                patch_sha256=hashlib.sha256(patch).hexdigest(), labels=labels, tests_run=len(before),
                breaking=breaking, indirect=indirect, lexical_top40=top, production_files=prod), 'kept' if indirect else 'lexical'


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--repo', required=True)
    p.add_argument('--python', required=True)
    p.add_argument('--cases', required=True, help='directory for extracted trees, outside this repository')
    p.add_argument('--want', type=int, default=6, help='indirect cases to keep')
    p.add_argument('--max-screened', type=int, default=250)
    p.add_argument('--skip', type=int, default=0, help='newest commits to pass over, for a set disjoint from an earlier one')
    p.add_argument('--name', default='django', help='manifest name; "django" is the Phase 0 set')
    p.add_argument('--with-lexical', action='store_true', help='also keep as many breaking-but-lexical cases as indirect ones')
    a = p.parse_args()
    KEEP_LEXICAL = a.with_lexical
    DATA.mkdir(parents=True, exist_ok=True)
    record = DATA/('protocol.json' if a.name == 'django' else a.name + '-protocol.json')
    if record.exists():
        raise SystemExit('Already frozen; do not overwrite')
    repo, cases = Path(a.repo), Path(a.cases)
    head = json.loads((DATA/'protocol.json').read_text())['head'] if a.name != 'django' else git(repo, 'rev-parse', 'HEAD').decode().strip()
    indirect, lexical, log = [], [], []
    for commit in git(repo, 'rev-list', '--no-merges', head).decode().splitlines()[a.skip:a.skip + a.max_screened]:
        try:
            row, reason = screen(repo, commit, a.python, cases)
        except subprocess.CalledProcessError:
            row, reason = None, 'history boundary'
        if row and reason == 'kept' and len(indirect) < a.want:
            indirect.append(row)
        elif row and reason == 'lexical' and len(lexical) < a.want:
            lexical.append(row)
        elif row:
            shutil.rmtree(cases/row['case_id'], ignore_errors=True)
            reason += ' (not needed)'
        log.append([commit[:10], reason])
        print(commit[:10], reason, flush=True)
        if len(indirect) == a.want and (not a.with_lexical or len(lexical) == a.want):
            break
    rows = indirect + lexical[:len(indirect)]
    for extra in lexical[len(indirect):]:
        shutil.rmtree(cases/extra['case_id'], ignore_errors=True)
    (DATA/(a.name + '.json')).write_text(json.dumps(rows, indent=1) + '\n')
    record.write_text(json.dumps(dict(
        rule=__doc__.strip(), repository='https://github.com/django/django', head=head, skipped_newest=a.skip, screened=log,
        kept=dict(indirect=[r['case_id'] for r in indirect], lexical=[r['case_id'] for r in lexical[:len(indirect)]]),
        environment=dict(python=subprocess.check_output([a.python, '-V']).decode().strip(), database='sqlite, default test settings',
                         dependencies='asgiref, sqlparse only; tests needing optional packages skip'),
        hashes={a.name + '.json': hashlib.sha256((DATA/(a.name + '.json')).read_bytes()).hexdigest()}), indent=1) + '\n')
    print(f'Frozen {len(indirect)} indirect and {min(len(lexical), len(indirect))} lexical cases after screening {len(log)} commits.')
