"""Replay actual frozen AMU inspections and a captured agent patch locally."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from amu import mcp_manifest, mcp_verification

HERE = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--entire', type=Path, required=True)
parser.add_argument('--graph-plugin', type=Path, required=True)
parser.add_argument('--patch', type=Path, default=HERE / 'value-validation/treatment.patch')
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
for path in [args.entire, args.graph_plugin, args.patch]:
    if not path.is_file(): parser.error(f'Required local file missing: {path}')
args.output = args.output.resolve()
if args.output.exists(): parser.error('Refuse to overwrite an existing receipt; choose a new output path')
args.output.parent.mkdir(parents=True, exist_ok=True)
spec = importlib.util.spec_from_file_location('accepted_wire', HERE / 'tools/raw_transport.py')
wire = importlib.util.module_from_spec(spec); spec.loader.exec_module(wire)
receipt = {
    'scope': 'Actual local replay of one captured agent patch, not a fresh comparison or authenticated ChatGPT test',
    'started_at_utc': datetime.now(timezone.utc).isoformat(),
    'candidate': 'EHpaIc', 'status': 'running', 'calls': [], 'sections': {},
    'commands': [], 'live_configuration_changed': False,
    'models_used_by_amu': False,
    'provider_binaries': {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in [args.entire, args.graph_plugin]},
    'captured_patch_sha256': hashlib.sha256(args.patch.read_bytes()).hexdigest(),
}


def save():
    args.output.write_text(json.dumps(receipt, indent=2) + '\n')


def command(label, arguments, cwd, env):
    started = time.perf_counter()
    run = subprocess.run(arguments, cwd=cwd, env=env, capture_output=True, text=True, timeout=30)
    receipt['commands'].append({'label': label, 'arguments': arguments,
        'exit_code': run.returncode, 'elapsed_seconds': time.perf_counter() - started,
        'stdout': run.stdout, 'stderr': run.stderr})
    save()
    return run


save()
try:
    with tempfile.TemporaryDirectory(prefix='amu-demo.', dir=args.output.parent) as temporary:
        work = Path(temporary); root = work / 'checkout'
        shutil.copytree(HERE / 'fixtures/checkout', root)
        (work / 'cache').mkdir(); (work / 'home').mkdir(); (work / 'control').mkdir()
        denied = work / 'ungranted'; denied.mkdir()
        environment = {'HOME': str(work / 'home'), 'PATH': '/usr/bin:/bin',
                       'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
                       'PYTHONDONTWRITEBYTECODE': '1', 'LANG': 'en_US.UTF-8'}
        for git_root in [root, denied]:
            run = command('initialize own disposable Git fixture', ['git', 'init', '--quiet', str(git_root)], work, environment)
            assert run.returncode == 0
        reproduce = [sys.executable, '-I', '-B', '-c',
                     f'import sys;sys.path.insert(0,{str(root)!r});from shop.pricing import discounted_total_cents;print(discounted_total_cents(5,50))']
        before = command('reproduce reported bug', reproduce, root, environment)
        assert before.returncode == 0 and before.stdout.strip() == '2'
        receipt['original_observed_total_cents'] = 2
        original = command('original available tests', [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-v'], root, environment)
        assert original.returncode == 0
        print('1. Original behavior: 5 cents at 50% => 2 cents; requested 3.', flush=True)
        print('   The original eight tests pass despite this bug.', flush=True)

        selected_path = os.pathsep.join([str(args.entire.resolve().parent),
                        str(args.graph_plugin.resolve().parent), '/usr/bin', '/bin'])
        launcher = f'''import os
os.environ.update(PATH={selected_path!r}, AMU_DISABLE_MODELS="1", ENTIRE_PLUGIN_DATA_DIR={str(work / 'cache')!r})
from amu import mcp_server
mcp_server.main(["--allow-repo", {str(root)!r}])
'''
        with wire.RawSession(sys.executable, work / 'control', code=launcher, timeout=45) as session:
            receipt['identity'] = mcp_verification._initialize_identity(session.initialize())
            assert receipt['identity']['build_sha256'] == '880131726cdfd53d8d572a4ebb2ccfcba278179a7ceee5136a2c33c19d415f0e'
            frame, listed = session.call('demo-tools', 'tools/list', {})
            wire.assert_bounded(frame)
            receipt['manifest'] = mcp_manifest.compare_tools(listed['result']['tools'])
            assert receipt['manifest']['match']
            print('2. Frozen build verified; four inspection tools advertised.', flush=True)

            def call(name, **arguments):
                arguments = {'repo': str(root), **arguments}
                begin = time.perf_counter()
                frame, packet = session.call('demo-' + str(len(receipt['calls'])), 'tools/call',
                                             {'name': name, 'arguments': arguments})
                wire.assert_bounded(frame)
                assert 'error' not in packet and not packet['result'].get('isError'), packet
                result = packet['result']
                text = next(x['text'] for x in result['content'] if x.get('type') == 'text')
                assert result['structuredContent'] == {'result': text}
                payload = json.loads(text)
                receipt['calls'].append({'tool': name, 'arguments': arguments,
                    'elapsed_seconds': time.perf_counter() - begin,
                    'wire_bytes': len(frame), 'wire_sha256': hashlib.sha256(frame).hexdigest(),
                    'original_wire_frame': frame.decode(), 'response': packet, 'payload': payload})
                receipt['frames'] = session.frames; save()
                return payload

            search = call('amu_search', query='order discount rounding integer cents')
            assert search.get('hits') and not search.get('error')
            graph = call('amu_graph', target='shop/pricing.py#discounted_total_cents', depth=2, relation='CALLS')
            assert graph['status'] == 'ok' and graph.get('edges')
            names = sorted({n.get('name', n.get('symbol', '')) for n in graph['nodes']})
            print('3. AMU graph: ' + ', '.join(x for x in names if x in ['quote', 'checkout', 'receipt_text']), flush=True)
            print(f'   Actual graph: {len(graph["nodes"])} nodes, {len(graph["edges"])} edges; evidence retained.', flush=True)

            base = {'file': 'shop/pricing.py', 'line': 7,
                    'proposed_change': 'Use exact integer nearest-cent discount rounding with half cents upward while preserving validation',
                    'depth': 2, 'page_size': 5}
            initial = call('amu_impact', **base)
            assert initial.get('report_hash') and not initial.get('error')
            assert initial['assessment']['safe_to_change'] is None
            report_hash = initial['report_hash']; stale_cursor = initial['page']['next_cursor']
            for section, metadata in initial['retrieval'].items():
                rows = []; cursor = None; seen = set(); pages = 0
                while True:
                    kw = {**base, 'section': section}
                    if cursor is not None: kw['cursor'] = cursor
                    payload = call('amu_impact', **kw)
                    assert payload['report_hash'] == report_hash
                    assert payload['page']['section'] == section and payload['page']['offset'] == len(rows)
                    assert payload['page']['total'] == metadata['entries']
                    items = payload['impact']['rows'] if section == 'impact' else payload['details']['items']
                    assert len(items) == payload['page']['returned']
                    rows.extend(items); pages += 1; cursor = payload['page']['next_cursor']
                    if cursor is None: break
                    assert cursor not in seen; seen.add(cursor)
                assert len(rows) == metadata['entries'] and metadata['fragmented_items'] == 0
                receipt['sections'][section] = {'complete': True, 'entries': len(rows), 'pages': pages,
                    'entries_sha256': hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}
                save()
            receipt['report_hash'] = report_hash
            print('4. Intent impact: all six evidence sections recovered; safety remains unknown.', flush=True)
            print('   ' + ', '.join(f'{k}: {v["entries"]}' for k, v in receipt['sections'].items()), flush=True)
            reviews = call('amu_docs_reviews')
            assert reviews == {'reviews': []}, reviews
            refusal = call('amu_docs_reviews', repo=str(denied))
            assert refusal['category'] == 'REPOSITORY_NOT_AUTHORIZED', refusal
            print('   Review queue is genuinely empty; ungranted fixture is denied.', flush=True)

            applied = command('replay captured treatment patch', ['git', '-C', str(root), 'apply', str(args.patch.resolve())], root, environment)
            assert applied.returncode == 0, applied.stderr
            after = command('reproduce corrected behavior', reproduce, root, environment)
            assert after.returncode == 0 and after.stdout.strip() == '3'
            receipt['fixed_observed_total_cents'] = 3
            if stale_cursor:
                stale = call('amu_impact', **{**base, 'cursor': stale_cursor})
                assert 'stale' in stale.get('error', '').lower(), stale
                receipt['old_cursor_refused_after_source_change'] = True
            else:
                receipt['old_cursor_refused_after_source_change'] = 'not exercised; initial section fits in one page'
            print('5. Captured agent patch replayed in this disposable copy: total now 3 cents.', flush=True)
            tests = command('captured final regression suite replay', [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-v'], root, environment)
            assert tests.returncode == 0, tests.stderr
            evaluated = command('same frozen independent evaluator replay',
                [sys.executable, '-I', '-B', str(HERE / 'value-validation/frozen-evaluator.py'), '--repo', str(root)], root, environment)
            assert evaluated.returncode == 0, evaluated.stdout + evaluated.stderr
            receipt['independent_evaluation'] = json.loads(evaluated.stdout)
            assert receipt['independent_evaluation']['passed'] == 10
            receipt['maximum_frame_bytes'] = max(row['bytes'] for row in session.frames)
            print('6. Regression suite passes; all ten independent check groups pass.', flush=True)
            print(f'   {len(session.frames)} actual wire frames; largest {receipt["maximum_frame_bytes"]:,} / 131,072 bytes.', flush=True)
            print('   This is a replay of recorded evidence, not a live ChatGPT acceptance.', flush=True)
        receipt['status'] = 'passed'
    receipt['temporary_fixture_removed'] = True
except Exception as exc:
    receipt.update(status='failed', error_type=type(exc).__name__, error=str(exc)[:2000])
    raise
finally:
    receipt['finished_at_utc'] = datetime.now(timezone.utc).isoformat(); save()
print(f'Receipt: {args.output}', flush=True)
