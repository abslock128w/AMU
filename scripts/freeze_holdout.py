"""Phase 0 holdout: commits whose change breaks an existing test that lexical search would not surface.

Executes the upstream repository's tests (owner approved Django on 2026-10-13). For each commit, walking
backwards from the frozen HEAD, the test modules the commit itself touched are run on the parent tree and
again with only the production part of the commit applied. A test that passed and now fails is breaking.
A breaking test outside the 40 tests most lexically similar to the diff is indirect. The first commits with
an indirect breaking test are kept. AMU's reachability ranking is never consulted during selection.

--project sympy (owner approved sympy on 2026-10-15) screens sympy instead: production code is sympy/**.py outside
tests/ directories, test modules are sympy/**/tests/test_*.py, and they run under pytest with sympy's own configuration
(its addopts deselect the slow and tooslow marks, as its default run does), one function per id, parameters merged.
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
import xml.etree.ElementTree as ET
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
BLIND = False  # --blind: tasks an agent must implement from the commit message; see the argument's help
BLIND_MAX_LINES = 200
LINE = re.compile(r'^(\w+) \(([\w.]+)\)')
STATE = re.compile(r'\.\.\. (ok|FAIL|ERROR|skipped|expected failure|unexpected success)')
NOISE = re.compile(r'^(Merge|Apply (batched )?suggestions?|Revert|Update \.?mailmap|Add .* to \.mailmap)|mailmap|^(doc|DOC|docs)\b|typo', re.I)
PROJECTS = {
    'django': dict(repository='https://github.com/django/django',
                   production=lambda p: p.startswith('django/') and p.endswith('.py'),
                   test=lambda p: p.startswith('tests/') and p.endswith('.py'),
                   environment=dict(database='sqlite, default test settings',
                                    dependencies='asgiref, sqlparse only; tests needing optional packages skip')),
    'sympy': dict(repository='https://github.com/sympy/sympy',
                  production=lambda p: p.startswith('sympy/') and p.endswith('.py') and '/tests/' not in p and '/benchmarks/' not in p,
                  test=lambda p: p.startswith('sympy/') and '/tests/test_' in p and p.endswith('.py'),
                  environment=dict(runner="pytest with sympy's pyproject configuration (addopts -m 'not slow and not tooslow')",
                                   dependencies='mpmath, pytest, pytest-xdist, pytest-timeout, hypothesis; tests needing optional packages skip')),
}
PROJECT = 'django'


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


def run_pytest(python, tree, paths, timeout=900):
    """{path::Class::test: state} from pytest's junit report, parameters merged (a function fails if any case does);
    '__unloadable__' lists test files that could not be collected."""
    report = tree.parent/'junit.xml'
    report.unlink(missing_ok=True)
    env = dict(PATH='/usr/bin:/bin', HOME=str(tree.parent/'home'), PYTHONPATH=str(tree), PYTHONDONTWRITEBYTECODE='1',
               LANG='en_US.UTF-8', HYPOTHESIS_STORAGE_DIRECTORY=str(tree.parent/'hypothesis'))
    (tree.parent/'home').mkdir(exist_ok=True)
    subprocess.run([python, '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '--continue-on-collection-errors',
                    f'--junitxml={report}', *paths], cwd=tree, env=env, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    found, unloadable, order = {}, set(), {'failed': 3, 'passed': 2, 'skipped': 1}
    try:
        cases = list(ET.parse(report).getroot().iter('testcase'))
    except (OSError, ET.ParseError):
        cases = []
    for case in cases:
        parts = case.get('classname', '').split('.')
        state = next((child.tag for child in case if child.tag in ('failure', 'error', 'skipped')), 'passed')
        for cut in range(len(parts), 0, -1):
            if (tree/('/'.join(parts[:cut]) + '.py')).is_file():
                break
        else:
            # A collection error: the classname is empty and the name is the module's dotted path.
            module = case.get('name', '').replace('.', '/') + '.py'
            if state == 'error' and (tree/module).is_file():
                unloadable.add(module)
            continue
        ident = '::'.join(['/'.join(parts[:cut]) + '.py', *parts[cut:], case.get('name', '').split('[')[0]])
        state = 'failed' if state in ('failure', 'error') else state
        if order[state] > order.get(found.get(ident), 0):
            found[ident] = state
    found['__unloadable__'] = sorted(unloadable)
    return found


def lexical_top(tree, patch, limit=40):
    """The tests whose names and bodies share most of the diff's vocabulary: what a text search finds."""
    facts, *_ = build(tree, tree.parent/'index')
    tests = {ident: r for f in facts.values() for ident, r in f['records'].items()
             if r['kind'] == 'function' and r['qual'].rsplit('.', 1)[-1].startswith('test_') and PROJECTS[PROJECT]['test'](r['path'])}
    content = rank.Content(tests)
    edits, _ = changes(tree, patch)
    query = content.query(' '.join(text for edit in edits.values() for _, text in [*edit['added'], *edit['removed']]))
    scored = sorted(((content.score(ident, query), ident) for ident in tests), reverse=True)
    return [tests[ident]['path'] + '::' + tests[ident]['qual'].replace('.', '::') for score, ident in scored[:limit] if score > 0]


def screen(repo, commit, python, cases):
    parent = git(repo, 'rev-parse', commit + '^').decode().strip()
    paths = git(repo, 'diff', '--name-only', parent, commit).decode().splitlines()
    prod = [p for p in paths if PROJECTS[PROJECT]['production'](p)]
    touched = [p for p in paths if PROJECTS[PROJECT]['test'](p)]
    if not prod or not touched:
        return None, 'no production or no test change'
    if BLIND:
        subject = git(repo, 'log', '-1', '--format=%s', commit).decode().strip()
        if PROJECT == 'django' and not subject.startswith('Fixed #'):
            return None, 'blind: subject does not start with "Fixed #"'
        if PROJECT == 'sympy' and (NOISE.search(subject) or len(subject) < 30):
            return None, 'blind: subject is review, merge, docs or mailmap housekeeping, or under 30 characters'
        size = sum(line[:1] in '+-' and line[:3] not in ('+++', '---') for line in git(repo, 'diff', parent, commit, '--', *prod).decode(errors='replace').splitlines())
        if size > BLIND_MAX_LINES:
            return None, f'blind: production change is {size} lines, over {BLIND_MAX_LINES}'
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
    case = cases/(('dj-' if PROJECT == 'django' else PROJECT[:2] + '-') + commit[:8])
    shutil.rmtree(case, ignore_errors=True)
    tree = case/'parent'
    tree.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(git(repo, 'archive', parent))) as archive:
        archive.extractall(tree, filter='data')
    patch = git(repo, 'diff', parent, commit, '--', *prod)
    (case/'production.patch').write_bytes(patch)
    if PROJECT == 'django':
        labels = sorted({p[len('tests/'):-3].replace('/', '.') for p in touched if (tree/p).is_file()})
        runner = run_tests
    else:
        labels = sorted(p for p in touched if (tree/p).is_file())
        runner = run_pytest
    if not labels:
        shutil.rmtree(case, ignore_errors=True)
        return None, 'no touched test module existed before'
    try:
        before = runner(python, tree, labels)
        with open(case/'production.patch', 'rb') as handle:
            applied = subprocess.run(['/usr/bin/patch', '-p1', '--batch', '-s'], cwd=tree, stdin=handle, capture_output=True)
        after = runner(python, tree, labels) if applied.returncode == 0 else {}
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
        if test.split('::')[0] in gone:
            return True
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
                breaking=breaking, indirect=indirect, lexical_top40=top, production_files=prod,
                message=git(repo, 'log', '-1', '--format=%B', commit).decode(errors='replace').strip()), 'kept' if indirect else 'lexical'


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--repo', required=True)
    p.add_argument('--python', required=True)
    p.add_argument('--cases', required=True, help='directory for extracted trees, outside this repository')
    p.add_argument('--want', type=int, default=6, help='indirect cases to keep')
    p.add_argument('--max-screened', type=int, default=250)
    p.add_argument('--skip', type=int, default=0, help='newest commits to pass over, for a set disjoint from an earlier one')
    p.add_argument('--name', default='django', help='manifest name; "django" is the Phase 0 set')
    p.add_argument('--project', choices=sorted(PROJECTS), default='django')
    p.add_argument('--head', help='commit to screen back from; default: the Phase 0 head for Django sets, else the clone\'s HEAD')
    p.add_argument('--data', default=str(DATA), help='where the manifest and protocol are written')
    p.add_argument('--with-lexical', action='store_true', help='also keep as many breaking-but-lexical cases as indirect ones')
    p.add_argument('--blind', action='store_true', help='keep the first --want commits that break an existing test, indirect or not, '
                   f'whose subject starts with "Fixed #" and whose production change is at most {BLIND_MAX_LINES} added plus removed lines')
    a = p.parse_args()
    KEEP_LEXICAL = a.with_lexical or a.blind
    BLIND = a.blind
    PROJECT = a.project
    DATA = Path(a.data)
    DATA.mkdir(parents=True, exist_ok=True)
    record = DATA/('protocol.json' if a.name == 'django' else a.name + '-protocol.json')
    if record.exists():
        raise SystemExit('Already frozen; do not overwrite')
    repo, cases = Path(a.repo), Path(a.cases)
    if a.head:
        head = git(repo, 'rev-parse', a.head).decode().strip()
    elif a.project == 'django' and a.name != 'django':
        head = json.loads((DATA/'protocol.json').read_text())['head']
    else:
        head = git(repo, 'rev-parse', 'HEAD').decode().strip()
    indirect, lexical, log = [], [], []
    for commit in git(repo, 'rev-list', '--no-merges', head).decode().splitlines()[a.skip:a.skip + a.max_screened]:
        try:
            row, reason = screen(repo, commit, a.python, cases)
        except subprocess.CalledProcessError:
            row, reason = None, 'history boundary'
        if row and a.blind and len(indirect) < a.want:
            indirect.append(row)  # order of screening, either kind
        elif row and reason == 'kept' and len(indirect) < a.want:
            indirect.append(row)
        elif row and reason == 'lexical' and len(lexical) < a.want:
            lexical.append(row)
        elif row:
            shutil.rmtree(cases/row['case_id'], ignore_errors=True)
            reason += ' (not needed)'
        log.append([commit[:10], reason])
        print(commit[:10], reason, flush=True)
        if len(indirect) == a.want and (a.blind or not a.with_lexical or len(lexical) == a.want):
            break
    rows = indirect if a.blind else indirect + lexical[:len(indirect)]
    for extra in lexical[len(indirect):]:
        shutil.rmtree(cases/extra['case_id'], ignore_errors=True)
    (DATA/(a.name + '.json')).write_text(json.dumps(rows, indent=1) + '\n')
    record.write_text(json.dumps(dict(
        rule=__doc__.strip(), project=a.project, repository=PROJECTS[a.project]['repository'], head=head, skipped_newest=a.skip, screened=log,
        kept=dict(blind=[r['case_id'] for r in rows]) if a.blind else dict(indirect=[r['case_id'] for r in indirect], lexical=[r['case_id'] for r in lexical[:len(indirect)]]),
        environment=dict(python=subprocess.check_output([a.python, '-V']).decode().strip(), **PROJECTS[a.project]['environment']),
        hashes={a.name + '.json': hashlib.sha256((DATA/(a.name + '.json')).read_bytes()).hexdigest()}), indent=1) + '\n')
    print(f'Frozen {len(indirect)} indirect and {min(len(lexical), len(indirect))} lexical cases after screening {len(log)} commits.')
