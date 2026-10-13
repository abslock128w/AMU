"""Static, commit-derived evaluation. Never imports/collects upstream code."""
import argparse
import ast
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'evaluation/before-tests'
EXTERNAL = ROOT.parent / 'external-amu-evaluation-2026-10-12'


def git(repo, *args):
    return subprocess.check_output(['git', '--git-dir='+str(repo), *args])


def functions(source, path):
    result = {}
    tree = ast.parse(source)
    bindings = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for name in ast.walk(target):
                    if isinstance(name, ast.Name):
                        bindings[name.id] = node
    def parameter_data(node):
        pending = [n.id for d in node.decorator_list for n in ast.walk(d) if isinstance(n, ast.Name)]
        seen = set()
        while pending:
            name = pending.pop()
            if name in seen or name not in bindings:
                continue
            seen.add(name)
            pending.extend(n.id for n in ast.walk(bindings[name]) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load))
        return [(name, ast.dump(bindings[name], include_attributes=False)) for name in sorted(seen)]
    def visit(body, prefix=''):
        for node in body:
            if isinstance(node, ast.ClassDef):
                visit(node.body, prefix+node.name+'::')
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('test_'):
                result[path+'::'+prefix+node.name] = repr((ast.dump(node, include_attributes=False), parameter_data(node)))
    visit(tree.body)
    return result


def prepare(repo, commit, case_id, base_tree=None, allow_empty=False):
    parent = git(repo, 'rev-parse', commit+'^').decode().strip()
    paths = git(repo, 'diff', '--name-only', parent, commit).decode().splitlines()
    prod = [p for p in paths if p.startswith('src/') and p.endswith('.py')]
    tests = [p for p in paths if p.startswith('tests/') and p.endswith('.py')]
    if not prod or (not tests and not allow_empty):
        return None
    truth, existing = [], []
    for path in tests:
        try:
            after = functions(git(repo, 'show', commit+':'+path), path)
        except subprocess.CalledProcessError:
            continue
        try:
            before = functions(git(repo, 'show', parent+':'+path), path)
        except subprocess.CalledProcessError:
            before = {}
        truth.extend(k for k, v in after.items() if before.get(k) != v)
        existing.extend(k for k in after if k in before and before[k] != after[k])
    if not truth and not allow_empty:
        return None
    case = DATA / 'cases' / case_id
    case.mkdir(parents=True, exist_ok=True)
    patch = git(repo, 'diff', '--binary', parent, commit, '--', *prod)
    (case/'production.patch').write_bytes(patch)
    if base_tree is None:
        base_tree = case/'parent'
        base_tree.mkdir(exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(git(repo, 'archive', parent))) as archive:
            archive.extractall(base_tree, filter='data')
    return dict(case_id=case_id, commit=commit, parent=parent, repo=str(repo), tree=str(base_tree),
                patch=str(case/'production.patch'), patch_sha256=hashlib.sha256(patch).hexdigest(),
                truth=sorted(truth), existing_truth=sorted(existing), files=sorted(tests))


def freeze():
    DATA.mkdir(parents=True, exist_ok=True)
    protocol = DATA/'protocol-v2.json'
    if protocol.exists():
        raise SystemExit('Already frozen; do not overwrite')
    repos = {'werkzeug': EXTERNAL/'werkzeug.git', 'click': ROOT.parent/'static-usefulness-2026-10-12/click.git'}
    heads = {name: git(repo, 'rev-parse', 'HEAD').decode().strip() for name, repo in repos.items()}
    rows = []
    for case in sorted((EXTERNAL/'cases').iterdir()):
        manifest = json.loads((case/'manifest.json').read_text())
        rows.append(prepare(repos['werkzeug'], manifest['commit'], case.name, case/'baseline', allow_empty=True))
    original = list(rows)
    for commit in git(repos['werkzeug'], 'rev-list', '--no-merges', heads['werkzeug']).decode().splitlines():
        if commit in {r['commit'] for r in rows}:
            continue
        if sum(bool(r['truth']) for r in rows) >= 16:
            break
        row = prepare(repos['werkzeug'], commit, 'extra-'+commit[:8])
        if row:
            rows.append(row)
    assert sum(bool(r['truth']) for r in rows) == 16
    selected = []
    for commit in git(repos['click'], 'rev-list', '--no-merges', heads['click']).decode().splitlines():
        row = prepare(repos['click'], commit, 'click-v2-'+commit[:8])
        if row:
            selected.append(row)
            if len(selected) == 10:
                break
    assert len(selected) == 10
    manifests = {'werkzeug-v2.json': rows, 'click-sealed-v2.json': selected}
    hashes = {}
    for name, values in manifests.items():
        path=DATA/name
        path.write_text(json.dumps(values,indent=2)+'\n')
        hashes[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    protocol.write_text(json.dumps(dict(
        rule='Walk git rev-list --no-merges backwards from frozen HEAD; first commits with production src .py and added/modified test function AST, including transitively referenced decorator parameter data. Werkzeug skips original 16. No AMU execution before selection.',
        heads=heads, hashes=hashes, commits={name:[r['commit'] for r in values] for name,values in manifests.items()},
        original_cases=[r['case_id'] for r in original],
        thresholds={'file_case_hit_rate':0.75,'function_case_hit_rate':0.5,'max_recommendations':40},
        scoring='Per-case recall fractions and aggregate nonzero-hit proportions; empty ground truth N/A. New tests absent in parent count as misses. Noise means non-ground-truth recommendations, not proof of irrelevance.',
        supersedes='freeze.json; original baseline retained unchanged; Click v1 never executed'
    ),indent=2)+'\n')
    print('Frozen original 16 plus 7 Werkzeug cases; Click 10 resealed. No upstream execution.')


def freeze_holdout2():
    """Second holdout, frozen after the Click run was read and before any fix for its misses.

    Same backwards walk. Every commit must also modify a test function that already exists in
    its parent, so that an existing-test recommender can score on every case.
    """
    protocol = DATA/'protocol-v3.json'
    if protocol.exists():
        raise SystemExit('Already frozen; do not overwrite')
    repos = {'requests': ROOT.parent/'static-usefulness-2026-10-12/requests.git',
             'itsdangerous': ROOT.parent/'core-pipeline-evaluation-2026-10-12/itsdangerous.git'}
    rows, heads = [], {}
    for name, repo in repos.items():
        heads[name] = git(repo, 'rev-parse', 'HEAD').decode().strip()
        chosen = 0
        for commit in git(repo, 'rev-list', '--no-merges', heads[name]).decode().splitlines():
            try:
                row = prepare(repo, commit, f'h2-{name}-{commit[:8]}')
            except (subprocess.CalledProcessError, SyntaxError):
                continue  # root or shallow-boundary commit, or unparsable test source
            if row and row['existing_truth']:
                rows.append(row)
                chosen += 1
                if chosen == 5:
                    break
            elif row:
                shutil.rmtree(DATA/'cases'/row['case_id'])
    path = DATA/'holdout2.json'
    path.write_text(json.dumps(rows, indent=2)+'\n')
    protocol.write_text(json.dumps(dict(
        rule='Walk git rev-list --no-merges backwards from each frozen HEAD; first 5 commits per repository that change production src .py and modify at least one test function already present in the parent. No analyzer run before selection.',
        reason='The Click holdout was read on 2026-10-13 (core: 1/10 function hits, 9/10 file hits). Fixes for its misses are judged here, once.',
        heads=heads, hashes={'holdout2.json': hashlib.sha256(path.read_bytes()).hexdigest()},
        commits=[r['commit'] for r in rows],
        thresholds={'function_case_hit_rate': 0.6, 'file_case_hit_rate': 0.75, 'max_recommendations': 40}), indent=2)+'\n')
    print(f'Frozen {len(rows)} holdout-2 cases. No upstream execution.')


def freeze_holdout3():
    """Unseen Click commits for the coverage-assisted ranking, frozen before any run on them.

    Continues the same backwards walk past every commit already used, keeping the first 8 that
    change production src .py and modify a test function already present in the parent.
    """
    protocol = DATA/'protocol-v4.json'
    if protocol.exists():
        raise SystemExit('Already frozen; do not overwrite')
    repo = ROOT.parent/'static-usefulness-2026-10-12/click.git'
    used = {r['commit'] for r in json.loads((DATA/'click-sealed-v2.json').read_text())}
    head = json.loads((DATA/'protocol-v2.json').read_text())['heads']['click']
    rows = []
    for commit in git(repo, 'rev-list', '--no-merges', head).decode().splitlines():
        if commit in used:
            continue
        try:
            row = prepare(repo, commit, 'h3-click-'+commit[:8])
        except (subprocess.CalledProcessError, SyntaxError):
            continue
        if row and row['existing_truth']:
            rows.append(row)
            if len(rows) == 8:
                break
        elif row:
            import shutil
            shutil.rmtree(DATA/'cases'/row['case_id'])
    path = DATA/'holdout3.json'
    path.write_text(json.dumps(rows, indent=2)+'\n')
    protocol.write_text(json.dumps(dict(
        rule='Continue git rev-list --no-merges backwards from the frozen Click HEAD, skipping the 10 commits of click-sealed-v2; first 8 that change production src .py and modify a test function already present in the parent. No analyzer or test run before selection.',
        reason='Judges line-aware coverage ranking on commits never analysed. Same repository as a development set, so independence is by commit, not by project.',
        head=head, hashes={'holdout3.json': hashlib.sha256(path.read_bytes()).hexdigest()},
        commits=[r['commit'] for r in rows]), indent=2)+'\n')
    print(f'Frozen {len(rows)} holdout-3 cases. Nothing executed.')


def worker(args):
    sys.path.insert(0, args.build)
    row = json.loads(Path(args.case).read_text())
    patch = Path(row['patch']).read_text()
    start = time.perf_counter()
    if args.engine == 'legacy':
        from amu.services.impact import PatchRequest, execute_patch
        packet = execute_patch(row['tree'], PatchRequest(patch=patch, base='worktree'))
        elapsed = time.perf_counter()-start
        output = json.dumps(packet, separators=(',', ':')).encode()
        entries, cursor = [], None
        while True:
            page = execute_patch(row['tree'], PatchRequest(patch=patch, base='worktree', section='recommended_checks', page_size=100, cursor=cursor))
            entries.extend(page['details']['items'])
            cursor = page['page']['next_cursor']
            if cursor is None:
                break
        import base64
        fragments = {}
        tests = []
        for item in entries:
            if 'evidence_fragment' in item:
                f = item['evidence_fragment']
                fragments.setdefault(f['item_sha256'], []).append(f)
            else:
                tests.append(item)
        for parts in fragments.values():
            tests.append(json.loads(b''.join(base64.b64decode(p['data']) for p in sorted(parts,key=lambda p:p['fragment_index']))))
        ids = [t['test_id'] for t in tests if 'test_id' in t]
    else:
        from amu.core import analyze
        # Keep the index outside the frozen case trees.
        packet = analyze(Path(row['tree']), patch, index_dir=Path(args.output).parent/'index'/row['case_id'], include_tests=True)
        output = json.dumps(packet, separators=(',', ':')).encode()
        elapsed = time.perf_counter()-start
        ids = [t['id'] for t in packet['tests']]
    Path(args.output+'.packet.json').write_bytes(output)
    top = ids[:10]
    found = set(top) & set(row['truth'])
    found_files = {t.split('::')[0] for t in top} & set(row['files'])
    result = dict(case_id=row['case_id'], function_recall10=len(found)/len(row['truth']) if row['truth'] else None, file_recall10=len(found_files)/len(row['files']) if row['files'] else None,
                  function_hits=sorted(found), missing_from_parent=sorted(set(row['truth'])-set(row['existing_truth'])),
                  recommended=len(ids), top10=top, seconds=elapsed, bytes=len(output), noise10=len(set(top)-set(row['truth'])))
    Path(args.output).write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result), flush=True)


def run(args):
    freeze = json.loads((DATA/{'holdout2': 'protocol-v3.json', 'holdout3': 'protocol-v4.json'}.get(args.suite, 'protocol-v2.json')).read_text())
    manifest = DATA/{'werkzeug': 'werkzeug-v2.json', 'click': 'click-sealed-v2.json', 'holdout2': 'holdout2.json', 'holdout3': 'holdout3.json'}[args.suite]
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == freeze['hashes'][manifest.name]
    out = DATA/args.label
    out.mkdir(exist_ok=False)
    rows = []
    for case in json.loads(manifest.read_text()):
        assert hashlib.sha256(Path(case['patch']).read_bytes()).hexdigest() == case['patch_sha256']
        casefile = out/(case['case_id']+'.input.json')
        casefile.write_text(json.dumps(case))
        output = out/(case['case_id']+'.json')
        start = time.perf_counter()
        env = dict(os.environ, AMU_HOME=str(out/'home'), AMU_DISABLE_MODELS='1')
        call = subprocess.run([sys.executable, __file__, 'worker', '--case', str(casefile), '--build', args.build, '--engine',args.engine,'--output',str(output)], env=env, capture_output=True, timeout=180)
        (out/(case['case_id']+'.stderr')).write_bytes(call.stderr)
        if call.returncode:
            result = dict(case_id=case['case_id'], error=call.stderr.decode()[-1000:], seconds=time.perf_counter()-start)
            output.write_text(json.dumps(result,indent=2))
        else:
            result=json.loads(output.read_text())
            result['wall_seconds']=time.perf_counter()-start
        rows.append(result)
        (out/'results.json').write_text(json.dumps(rows,indent=2)+'\n')
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=['freeze','freeze-holdout2','freeze-holdout3','run','worker'])
    p.add_argument('--suite',default='werkzeug',choices=['werkzeug','click','holdout2','holdout3'])
    p.add_argument('--label')
    p.add_argument('--build',default=str(ROOT))
    p.add_argument('--engine',default='legacy')
    p.add_argument('--case')
    p.add_argument('--output')
    a=p.parse_args()
    {'freeze':lambda _:freeze(),'freeze-holdout2':lambda _:freeze_holdout2(),'freeze-holdout3':lambda _:freeze_holdout3(),'run':run,'worker':worker}[a.action](a)
