"""Run with the clean environment's Python -I; never import the checkout."""

import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    import amu
    from amu import mcp_manifest, mcp_server, mcp_verification, process, repository_diagnostics
    from amu.services import diagnostics, documentation, graph_sync, impact, impact_report, mapping

    package = Path(amu.__file__).resolve()
    assert package.is_relative_to(Path(sys.prefix).resolve()), package
    assert amu.__version__ == importlib.metadata.version('amu'), 'package version mismatch'
    for module in (mcp_manifest, mcp_verification, repository_diagnostics, diagnostics, impact, impact_report):
        assert Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), module.__file__
    identity = mcp_manifest.runtime_identity()
    assert identity['build']['complete'] and len(identity['build_sha256']) == 64, identity
    assert len(mcp_manifest.inspection_manifest()['tools']) == 4
    # Exercise the installed transport implementation, not the source checkout.
    bounded = process.run([sys.executable, '-c', "print('x' * 2048)"], timeout=5, stdout_limit=64, stderr_limit=64)
    assert bounded.error == 'output_limit' and bounded.stdout == bounded.stderr == b'', bounded
    with tempfile.TemporaryDirectory(prefix='amu-installed-') as directory:
        root = Path(directory)
        home = root / 'home'
        home.mkdir()
        bin_dir = root / 'bin'
        bin_dir.mkdir()
        git = shutil.which('git')
        assert git, 'git is required for the smoke test'
        (bin_dir / 'git').symlink_to(git)
        # No inherited credentials, provider endpoints, Python paths, or graph binary.
        env = {'PATH': str(bin_dir), 'HOME': str(home), 'AMU_HOME': str(home / '.amu'),
               'NO_COLOR': '1', 'TERM': 'dumb', 'LANG': 'en_US.UTF-8'}
        os.environ.clear()
        os.environ.update(env)

        def run(args, code=0, input=None, terminal=False):
            result = subprocess.run(args, cwd=root, env=env, text=True, input=input,
                                    capture_output=True, timeout=30)
            assert result.returncode == code, (args, result.returncode, result.stdout, result.stderr)
            return result.stdout + result.stderr if terminal else result.stdout

        run([git, 'init', '-q', str(root)])
        cli = str(Path(sys.executable).parent / 'amu')
        alias = str(Path(sys.executable).parent / 'entire-amu')
        for entry in (cli, alias):
            assert 'TWC Thugs' in run([entry, '--help'])
            assert amu.__version__ in run([entry, '--version'])
        doctor = json.loads(run([cli, 'doctor', '--json'], code=3))
        assert doctor['install']
        run([cli, 'map'], code=2)
        skills = json.loads(run([cli, 'skills', 'list', '--json']))
        assert len(skills) >= 5, skills
        for skill in skills:
            assert Path(skill['path']).read_text().strip()
        # The installed wheel must ship the versioned fixture resources and
        # expose both new deterministic commands without a graph/model provider.
        score = json.loads(run([cli, 'benchmark', '--json']))
        assert score['counts']['cases'] > 0 and score['fixture_version'], score
        assert score['tool_version'] == amu.__version__, score
        for name in ('public-api-v1.json', 'relations-v1.json'):
            assert (package.parent / 'benchmark_data' / name).is_file(), name
        (root / 'api.py').write_text('def public(value):\n    return value\n')
        run([git, 'add', '--', 'api.py'])
        run([git, '-c', 'user.name=Installed smoke', '-c', 'user.email=smoke@example.invalid',
             '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'baseline'])
        (root / 'api.py').write_text('def public(value, strict=False):\n    return value\n')
        manifest = json.loads(run([cli, 'api-manifest', '--file', 'api.py', '--json']))
        assert any(row['id'] == 'api.py#public' and row['change'] == 'signature'
                   for row in manifest['changes']), manifest
        assert Path(documentation.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
        queue = documentation.review_queue(str(root))
        assert queue == json.loads(run([cli, 'docs-reviews', '--json']))
        doc_graph = documentation.graph(str(root))
        console_graph = json.loads(run([cli, 'docs-graph', '--json']))
        assert doc_graph['graph']['summary'] == console_graph['graph']['summary']
        assert doc_graph['graph']['graph_hash'] == console_graph['graph']['graph_hash']
        try:
            documentation.execute(str(root), documentation.DocsRequest(mode='flag'))
        except documentation.DocumentationError as exc:
            assert exc.exit_code == 3
            direct_docs_error = exc.payload
        else:
            raise AssertionError('missing graph provider must not become an empty docs listing')
        assert direct_docs_error == json.loads(run([cli, 'docs', '--mode', 'flag', '--json'], code=3))
        assert direct_docs_error == json.loads(mcp_server.amu_docs(repo=str(root)))
        for module in (mapping, graph_sync):
            assert Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
        try:
            mapping.execute(str(root), mapping.MapRequest(symbol='api.py#public', depth=True))
        except mapping.MapInputError as exc:
            assert exc.exit_code == 2
        else:
            raise AssertionError('the core must reject a boolean traversal depth')
        failed_sync = graph_sync.execute(str(root))
        console_sync = json.loads(run([cli, 'sync', '--json'], code=3))
        for field in ('error', 'index_ref', 'snapshot_ref', 'snapshot_hash', 'code_cache_reused'):
            assert failed_sync[field] == console_sync[field], (field, failed_sync, console_sync)
        assert failed_sync['readiness']['ready'] is False and failed_sync['snapshot_hash'] is None
        (root / 'raw.json').write_text(json.dumps({'edges': [], 'completeness': True}))
        (root / 'validated.json').write_text('[]')
        relations = json.loads(run([cli, 'benchmark', '--relations', 'raw.json',
                                    '--validated', 'validated.json', '--json']))
        assert relations['reports']['raw_provider']['counts']['tn'] > 0, relations
        assert relations['reports']['amu_source_validated']['counts']['tn'] == 0, relations
        transcript = run([cli], input='/help\n/docs --bad-option\n/help\n/quit\n', terminal=True)
        assert transcript.count('AMU command guide') == 2, transcript
        assert 'No such option' in transcript and 'Traceback' not in transcript, transcript
        # Real wrapper-to-CLI subprocess, not a patched adapter.
        result = json.loads(mcp_server.amu_map_target('example.py#missing', repo=str(root)))
        direct = json.loads(run([cli, 'map', '--symbol', 'example.py#missing', '--json'], code=3))
        assert result == direct, (result, direct)
    print('Installed-package smoke passed: versions, entry points, resources, API manifest, benchmarks, documentation/mapping/sync services, REPL, missing-engine guards, MCP wrapper.')


if __name__ == '__main__':
    main()
