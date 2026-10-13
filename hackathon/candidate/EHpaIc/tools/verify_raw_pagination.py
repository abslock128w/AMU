"""Independent installed-package raw-pipe verification; disposable fixtures only."""

import argparse
import ast
import hashlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    from amu import mcp_manifest, mcp_verification

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    assert sys.flags.isolated
    identity = mcp_manifest.runtime_identity()
    assert Path(identity['module']).resolve().is_relative_to(Path(sys.prefix).resolve())
    scripts = args.source / 'scripts'
    fixture = load('independent_fixture', scripts / 'smoke_pilot_installed.py')
    wire = load('independent_wire', scripts / 'smoke_mcp_wire.py')
    native = load('independent_native', scripts / 'smoke_mcp_inspection.py')
    sections = {}
    calls = []
    with tempfile.TemporaryDirectory(prefix='amu-independent-raw-paging-') as directory:
        drill = fixture.Drill(Path(directory), fixture._binary('/opt/homebrew/Caskroom/entire/0.10.5/entire'),
                              fixture._binary('/Users/Abslock128/go/bin/entire-graph'), 60, 300)
        drill.env['AMU_DISABLE_MODELS'] = '1'
        drill.setup()
        baseline = native._capture(drill.repo)
        code = ('import os,sys\nos.environ.clear()\nos.environ.update(' + repr(drill.env)
                + ')\nfrom amu import mcp_server\nmcp_server.main(["--allow-repo",sys.argv[1]])\n')
        with wire.RawSession(sys.executable, drill.repo, code=code, timeout=30) as session:
            hello = session.initialize()
            assert mcp_verification._initialize_identity(hello)['build_sha256'] == identity['build_sha256']
            frame, listing = session.call('independent-manifest', 'tools/list', {})
            wire.assert_bounded(frame)
            manifest = mcp_manifest.compare_tools(listing['result']['tools'])
            assert manifest['match']

            def call(name, **arguments):
                identifier = 'independent-' + str(len(calls))
                frame, packet = session.call(identifier, 'tools/call', {
                    'name': name, 'arguments': {'repo': str(drill.repo), **arguments}})
                wire.assert_bounded(frame)
                assert packet['id'] == identifier and 'result' in packet
                result = packet['result']
                assert not result.get('isError')
                texts = [item['text'] for item in result['content'] if item.get('type') == 'text']
                assert len(texts) == 1 and result['structuredContent'] == {'result': texts[0]}
                calls.append({'tool': name, 'response_bytes': len(frame), 'section': arguments.get('section')})
                return json.loads(texts[0])

            assert call('amu_search', query='target')['hits']
            assert call('amu_graph', target=fixture.TARGET, depth=1, relation='CALLS')['nodes']
            assert not call('amu_docs_reviews').get('error')
            source = drill.repo / 'pilotpkg/api.py'
            line = next(node.lineno for node in ast.parse(source.read_text()).body
                        if isinstance(node, ast.FunctionDef) and node.name == 'target')
            arguments = {'file': 'pilotpkg/api.py', 'line': line,
                         'proposed_change': 'add an optional offset parameter to target', 'depth': 1}
            first = call('amu_impact', **arguments, page_size=100)
            assert first['assessment']['safe_to_change'] is None
            report_hash = first['report_hash']
            assert set(first['retrieval']) == {'impact', 'source_contradictions', 'source_uncertainties',
                                               'dynamic_boundaries', 'coverage', 'reasons'}
            old_cursor = first['retrieval']['impact']['cursor']

            def items(packet, section):
                return packet['impact']['rows'] if section == 'impact' else packet['details']['items']

            for section, metadata in first['retrieval'].items():
                reference = call('amu_impact', **arguments, section=section, page_size=100)
                assert reference['report_hash'] == report_hash and reference['page']['next_cursor'] is None
                expected = items(reference, section)
                received = []
                cursor = None
                seen = set()
                pages = 0
                while True:
                    request = {**arguments, 'section': section, 'page_size': 1}
                    if cursor is not None:
                        assert cursor not in seen
                        seen.add(cursor)
                        request['cursor'] = cursor
                    packet = call('amu_impact', **request)
                    assert packet['report_hash'] == report_hash
                    assert packet['page']['section'] == section and packet['page']['offset'] == len(received)
                    assert packet['page']['total'] == metadata['entries']
                    rows = items(packet, section)
                    assert len(rows) == packet['page']['returned']
                    received.extend(rows)
                    pages += 1
                    cursor = packet['page']['next_cursor']
                    if cursor is None:
                        assert packet['page']['remaining'] == 0
                        break
                    assert rows and pages <= metadata['entries']
                assert received == expected and len(received) == metadata['entries']
                sections[section] = {'pages': pages, 'entries': len(received),
                                     'logical_items': metadata['logical_items'],
                                     'fragmented_items': metadata['fragmented_items'],
                                     'complete': True,
                                     'entries_sha256': hashlib.sha256(json.dumps(received, sort_keys=True).encode()).hexdigest()}

            outside = drill.root / 'ungranted'
            outside.mkdir()
            denial = call('amu_docs_reviews', repo=str(outside))
            assert denial['error_kind'] == 'repository_not_allowed' and not (outside / '.amu').exists()
            assert native._capture(drill.repo) == baseline
            source.write_text(source.read_text() + '\n# Explicit disposable-fixture operator edit for stale-cursor verification.\n')
            stale = call('amu_impact', **arguments, cursor=old_cursor)
            assert stale['exit'] == 2 and 'cursor' in stale['error'].lower()
            frames = session.frames
        assert session.process.returncode == 0 and not session.trailing_stdout()
        report = {'status': 'passed', 'scope': 'new independent raw-pipe native-provider execution; no live client or NumPy',
                  'identity': identity, 'manifest': manifest, 'engine': drill.report['engine'],
                  'sections': sections, 'calls': calls, 'frames': frames, 'report_hash': report_hash,
                  'maximum_frame_bytes': max(frame['bytes'] for frame in frames),
                  'wire_allowance_including_newline': 131072,
                  'exact_root_denial': denial, 'stale_cursor_error': stale,
                  'source_docs_state_preserved_before_explicit_fixture_edit': True,
                  'models_used': False, 'live_configuration_changed': False,
                  'fragment_reconstruction_scope': 'native sections have no fragmented rows; oversized row hash reconstruction is separately covered by executed service regressions'}
    report['temporary_fixture_removed'] = True
    print(json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    main()
