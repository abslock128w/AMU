"""Score the blind trial against the hidden upstream tests. Run only after every agent has finished.

Usage: score.py ROOT DJANGO_GIT [--only ID ...]
Executes Django's suite: per task on the parent tree and on the upstream commit, per run on the agent's tree
with upstream's tests/ in place of the agent's (the hidden tests), and the touched modules on the agent's tree
as shipped. Definitions are in TRIAL-RECORD.md; this file implements them and nothing else.
"""
import argparse
import ast
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import djrun  # noqa: E402

RULES = json.loads((HERE/'rules.json').read_text())
NETWORK = re.compile(r'\b(curl|wget|pip3? install|git (fetch|pull|clone|remote)|gh |ssh |nc )')


def dotted(ident):
    path, *rest = ident.split('::')
    return '.'.join([path[len('tests/'):-3].replace('/', '.'), *rest])


def cached(path, make):
    path = Path(path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(make()))
    return json.loads(path.read_text())


def suite(python, tree, home, labels=()):
    result = djrun.run(python, tree, labels, shards=RULES['score_shards'], timeout=3 * 3600, home=home)
    return dict(states=result['states'], problems=result['problems'], seconds=result['seconds'])


def extract(repo, commit, target, paths=()):
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    data = subprocess.run(['git', '-C', str(repo), 'archive', commit, *paths], check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        archive.extractall(target, filter='data')


def method_source(tree, ident):
    """Source text of the test method named by a dotted id, or None when it is not defined in that file."""
    parts = ident.split('.')
    for cut in range(len(parts), 0, -1):
        path = Path(tree)/'tests'/('/'.join(parts[:cut]) + '.py')
        if path.is_file():
            source, node = path.read_text(errors='replace'), None
            body = ast.parse(source).body
            for name in parts[cut:]:
                node = next((n for n in body if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name), None)
                if node is None:
                    return 'file:' + source  # inherited or generated: compare the whole file
                body = getattr(node, 'body', [])
            return ast.get_source_segment(source, node)
    return None


def session(path):
    """The same measures from a desktop-app session transcript (one JSON record per line, usage per message)."""
    from datetime import datetime
    out = dict(tokens=0, output_tokens=0, cost=None, turns=0, declared=False, network=[], outside=[], final='', bash_edits=0, edit_tool_calls=0)
    seen, stamps, last_text = set(), [], ''
    for line in Path(path).read_text(errors='replace').splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get('timestamp'):
            stamps.append(datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00')).timestamp())
        if event.get('type') != 'assistant':
            continue
        message = event.get('message') or {}
        if message.get('id') not in seen:
            seen.add(message.get('id'))
            usage = message.get('usage') or {}
            out['tokens'] += sum(usage.get(k) or 0 for k in ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens', 'output_tokens'))
            out['output_tokens'] += usage.get('output_tokens') or 0
            out['turns'] += 1
        for block in message.get('content') or []:
            if block.get('type') == 'text':
                last_text = block.get('text') or ''
            if block.get('type') != 'tool_use':
                continue
            if block.get('name') in ('Edit', 'Write', 'MultiEdit'):
                out['edit_tool_calls'] += 1
            if block.get('name') == 'Bash':
                command = str((block.get('input') or {}).get('command', ''))
                if NETWORK.search(command):
                    out['network'].append(command[:200])
                if re.search(r'phase0-django|django\.git|blind-cases|/runs/|side-session-notes|scratchpad/trial/', command):
                    out['outside'].append(command[:200])
                if re.search(r'sed -i|python3? - <<|cat >|> *(django|tests)/|perl -pi', command):
                    out['bash_edits'] += 1
    out['final'] = last_text[-400:]
    out['declared'] = '"done"' in last_text
    out['session_wall_seconds'] = round(stamps[-1] - stamps[0], 1) if stamps else None
    return out


def transcript(path):
    """Tokens, turns, test commands and anything that looks like network use, from a stream-json transcript."""
    out = dict(tokens=None, cost=None, turns=None, declared=False, network=[], outside=[], final='')
    for line in Path(path).read_text(errors='replace').splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get('type') == 'assistant':
            for block in event.get('message', {}).get('content', []):
                if block.get('type') == 'tool_use' and block.get('name') == 'Bash':
                    command = str(block.get('input', {}).get('command', ''))
                    if NETWORK.search(command):
                        out['network'].append(command[:200])
                    if re.search(r'phase0-django|django\.git|blind-cases|/runs/', command):
                        out['outside'].append(command[:200])
        if event.get('type') == 'result':
            usage = event.get('usage') or {}
            out.update(tokens=sum(usage.get(k) or 0 for k in ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens', 'output_tokens')),
                       output_tokens=usage.get('output_tokens'), cost=event.get('total_cost_usd'), turns=event.get('num_turns'),
                       final=str(event.get('result') or '')[-400:])
            out['declared'] = event.get('subtype') == 'success' and '"done"' in out['final']
    return out


def score_run(root, repo, ident, slot, row, parent, upstream, python):
    work, run, scratch = root/'ws'/ident, root/'runs'/ident, root/'score'/ident
    result = json.loads((run/'result.json').read_text())
    # Hidden tests: the agent's tree with upstream's tests/ in place of its own.
    def hidden():
        tree = scratch/'hidden'
        shutil.rmtree(scratch, ignore_errors=True)
        shutil.copytree(work, tree, ignore=shutil.ignore_patterns('.git', '__pycache__', 'tests', 'rt', 'TASK.md', 'PROMPT.md', '.claude'))
        extract(repo, row['commit'], scratch/'up', ['tests'])
        shutil.move(str(scratch/'up'/'tests'), str(tree/'tests'))
        assert (tree/'django').is_dir() and (tree/'tests'/'runtests.py').is_file()
        first = suite(python, tree, scratch/'home')
        suspects = sorted(t for t, state in upstream['states'].items() if state == 'passed' and first['states'].get(t) != 'passed'
                          and first['states'].get(t) != 'skipped')
        # Every suspect is run again by itself before it counts; one that passes alone is recorded as unstable.
        again = suite(python, tree, scratch/'home', suspects)['states'] if 0 < len(suspects) <= 400 else {}  # more than 400: counted as they stand
        return dict(first=first, suspects=suspects, again=again)
    hid = cached(root/'score'/f'{ident}-hidden.json', hidden)
    failing = [t for t in hid['suspects'] if hid['again'].get(t) != 'passed']
    regressions = [t for t in failing if parent['states'].get(t) == 'passed']
    new_failing = [t for t in failing if t not in parent['states']]
    parent_tree = Path(row['tree'])
    kept = [t for t in regressions if method_source(parent_tree, t) == method_source(scratch/'hidden', t)]
    # Own tree as shipped: the modules upstream touched, the modules the agent touched, and the frozen breaking tests' modules.
    def own():
        changed = subprocess.run(['git', '-C', str(work), 'diff', '--cached', '--name-only', 'HEAD'], check=True, capture_output=True, text=True).stdout.split()
        labels = sorted({*row['labels'], *(p[len('tests/'):-3].replace('/', '.') for p in changed if p.startswith('tests/') and p.endswith('.py') and (work/p).is_file())})
        labels = [label for label in labels if not label.endswith('__init__')]
        return dict(labels=labels, **suite(python, work, scratch/'home-own', labels))
    mine = cached(root/'score'/f'{ident}-own.json', own)
    red = sorted(t for t, state in mine['states'].items() if state == 'failed' and parent['states'].get(t) == 'passed')
    ledger = json.loads((run/'ledger.json').read_text()) if (run/'ledger.json').exists() else []
    hooks = [json.loads(line) for line in (run/'hook.jsonl').read_text().splitlines()] if (run/'hook.jsonl').exists() else []
    breaking = [dotted(t) for t in row['breaking']]
    def covered(test, labels):
        return not labels or any(test == label or test.startswith(label + '.') for label in labels)
    agent_ran = [t for t in breaking if any(r['tool'] == 'rt' and covered(t, r.get('labels') or []) and not r.get('problems') for r in ledger)]
    hook_flagged = [t for t in breaking if any(t in (h.get('failing') or []) for h in hooks)]
    talk = session(run/'session.jsonl') if (run/'session.jsonl').exists() else transcript(run/'transcript.jsonl')
    return dict(id=ident, **slot, wall_seconds=result['wall_seconds'], stopped_at_hard_cap=result['stopped_at_hard_cap'], **talk,
                regressions=regressions, regressions_in_tests_upstream_left_alone=kept, upstream_new_tests_failing=new_failing,
                unstable=[t for t in hid['suspects'] if hid['again'].get(t) == 'passed'], hidden_problems=hid['first']['problems'],
                red_in_own_tree=red, own_problems=mine['problems'], breaking=breaking, breaking_run_by_agent=agent_ran, breaking_flagged_by_hook=hook_flagged,
                test_seconds=round(sum(r['seconds'] for r in ledger), 1), rt_runs=sum(r['tool'] == 'rt' for r in ledger),
                hook_runs=sum(r['tool'] == 'hook' for r in ledger), hook_seconds=round(sum(r['seconds'] for r in ledger if r['tool'] == 'hook'), 1),
                hook_injections=sum(bool(h.get('injected')) for h in hooks), hook_skipped_for_budget=sum(h.get('skipped') == 'budget' for h in hooks),
                diff_lines=len((run/'agent.diff').read_text(errors='replace').splitlines()))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('root')
    p.add_argument('repo')
    p.add_argument('--only', nargs='*')
    a = p.parse_args()
    root, repo, python = Path(a.root), Path(a.repo), str(Path(a.root)/'env/bin/python')
    manifest = {row['case_id']: row for row in json.loads((HERE.parent/'phase0/blind.json').read_text())}
    mapping = json.loads((root/'runs'/'map.json').read_text())
    rows = []
    for ident, slot in sorted(mapping.items(), key=lambda item: (item[1]['task'], item[1]['trial'], item[1]['arm'])):
        if (a.only and ident not in a.only) or not (root/'runs'/ident/'result.json').exists():
            continue
        row = manifest[slot['case']]
        parent = cached(root/'score'/f"{slot['task']}-parent.json", lambda: suite(python, row['tree'], root/'score'/'home-parent'))
        def reference():
            extract(repo, row['commit'], root/'score'/'trees'/slot['task'])
            return suite(python, root/'score'/'trees'/slot['task'], root/'score'/'home-upstream')
        upstream = cached(root/'score'/f"{slot['task']}-upstream.json", reference)
        rows.append(score_run(root, repo, ident, slot, row, parent, upstream, python))
        print(ident, slot['task'], slot['arm'], slot['trial'], 'regressions', len(rows[-1]['regressions']), 'red in own tree', len(rows[-1]['red_in_own_tree']), flush=True)
        (root/'score'/'scores.json').write_text(json.dumps(rows, indent=1))
