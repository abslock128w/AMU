"""Verified installer mode checks the provider the dispatcher will really execute."""

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def engine_install(tmp_path):
    bin_dir = tmp_path / 'selected tools'
    bin_dir.mkdir()
    managed = tmp_path / 'managed'
    managed.mkdir()
    log = tmp_path / 'calls'
    (bin_dir / 'python3').symlink_to(sys.executable)
    dispatcher = bin_dir / 'entire'
    dispatcher.write_text('''#!/bin/sh
printf 'entire|%s|%s|%s\\n' "$1" "$2" "$3" >> "$CALL_LOG"
case "$1:$2" in
  --help:)
    printf 'Usage: entire [command]\\n'
    [ "${BUILTIN_GRAPH:-0}" = 0 ] || printf '  graph  Built-in graph command\\n';;
  plugin:--help)
    [ "${UNKNOWN_HELP:-0}" = 0 ] || { printf 'unknown dispatcher\\n'; exit 0; }
    printf "Plugins are external executables named 'entire-<name>'.\\nThe managed directory is auto-prepended to PATH at startup.\\n";;
  plugin:list)
    [ "${UNKNOWN_LIST:-0}" = 0 ] || { printf 'unknown plugin directory\\n'; exit 0; }
    printf 'Managed plugin directory: %s\\n' "$STUB_MANAGED";;
  graph:*)
    shift
    PATH="$STUB_MANAGED:$PATH"
    export PATH
    exec entire-graph "$@";;
  amu:doctor)
    if [ "${REPLACE_GRAPH_IN_DOCTOR:-0}" = 1 ]; then printf '#!/bin/sh\\nexit 0\\n' > "$STUB_GRAPH"; fi
    exit "${DOCTOR_EXIT:-0}";;
  *) exit 9;;
esac
''')
    graph = tmp_path / 'trusted graph build'
    graph.write_text('''#!/bin/sh
printf 'graph|%s|%s\\n' "$1" "$2" >> "$CALL_LOG"
case "$1" in
  version) printf '{"provider":"entire-graph","version":"dev-with-explicit-pin"}\\n';;
  capabilities)
    [ "${CAPABILITIES_EXIT:-0}" = 0 ] || exit "$CAPABILITIES_EXIT"
    printf '{"provider":"entire-graph","supported_languages":["Python"]}\\n';;
  *) exit 9;;
esac
''')
    for path in (dispatcher, graph):
        path.chmod(0o755)
    (managed / 'entire-graph').symlink_to(graph)
    for name in ('pipx', 'amu', 'brew'):
        path = bin_dir / name
        path.write_text('''#!/bin/sh
name=${0##*/}
printf '%s|%s|%s|%s\\n' "$name" "$1" "$2" "$3" >> "$CALL_LOG"
case "$name:$1" in
  pipx:environment) printf '%s\\n' "$STUB_BIN";;
  pipx:install)
    if [ "${REPLACE_GRAPH_IN_INSTALL:-0}" = 1 ]; then printf '#!/bin/sh\\nexit 0\\n' > "$STUB_GRAPH"; fi;;
  amu:--version) printf 'amu 0.2.0\\n';;
  brew:*) exit 99;;
esac
''')
        path.chmod(0o755)
    project = tmp_path / 'source'
    project.mkdir()
    (project / 'pyproject.toml').write_text('[project]\nname="amu"\n')
    (project / 'constraints.txt').write_text('rich==15.0.0\n')

    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def run(*extra, entire_path=None, graph_path=None, entire_hash=None, graph_hash=None, **settings):
        selected_entire = dispatcher if entire_path is None else entire_path
        selected_graph = graph if graph_path is None else graph_path
        args = [
            '--local', str(project), '--entire-bin', str(selected_entire),
            '--entire-sha256', entire_hash or digest(dispatcher),
            '--graph-bin', str(selected_graph), '--graph-sha256', graph_hash or digest(graph),
            *extra,
        ]
        env = {'PATH': str(bin_dir), 'HOME': str(tmp_path), 'CALL_LOG': str(log),
               'STUB_BIN': str(bin_dir), 'STUB_MANAGED': str(managed), 'STUB_GRAPH': str(graph), **settings}
        result = subprocess.run(['/bin/sh', str(ROOT / 'scripts/install.sh'), *args],
                                env=env, cwd=tmp_path, capture_output=True, text=True, timeout=15)
        return result, log.read_text() if log.exists() else ''

    return run, dispatcher, graph, managed, bin_dir


def test_verified_install_uses_actual_managed_symlink_and_never_fetches_engine(engine_install):
    run, _, _, _, _ = engine_install
    result, calls = run()
    assert result.returncode == 0, result.stderr
    assert 'Engine identity verified against supplied SHA256 pins:' in result.stdout
    assert 'installed:' in result.stdout
    assert 'pipx|install|' in calls
    assert calls.index('graph|capabilities|--json') < calls.index('pipx|install|')
    assert 'brew|' not in calls and 'plugin|install' not in calls


@pytest.mark.parametrize('label', ['entire', 'graph'])
def test_digest_mismatch_does_not_execute_either_engine(engine_install, label):
    run, _, _, _, _ = engine_install
    result, calls = run(**{label + '_hash': '0' * 64})
    assert result.returncode != 0 and 'SHA256 mismatch' in result.stderr
    assert calls == ''


@pytest.mark.parametrize('kind', ['missing', 'relative', 'directory', 'not-executable'])
def test_invalid_selected_binary_is_rejected_before_install(engine_install, tmp_path, kind):
    run, _, graph, _, _ = engine_install
    selected = tmp_path / 'missing'
    if kind == 'relative':
        selected = Path('relative-engine')
    elif kind == 'directory':
        selected = tmp_path
    elif kind == 'not-executable':
        graph.chmod(0o644)
        selected = graph
    result, calls = run(graph_path=selected)
    assert result.returncode != 0
    assert calls == ''
    assert 'installed:' not in result.stdout


def test_managed_provider_wins_over_matching_path_provider(engine_install):
    run, _, graph, managed, bin_dir = engine_install
    (bin_dir / 'entire-graph').symlink_to(graph)
    (managed / 'entire-graph').unlink()
    wrong = managed / 'entire-graph'
    wrong.write_text('#!/bin/sh\nexit 0\n')
    wrong.chmod(0o755)
    result, calls = run()
    assert result.returncode != 0 and 'graph dispatch mismatch' in result.stderr
    assert 'graph|' not in calls
    assert 'pipx|install|' not in calls
    assert 'brew|' not in calls and 'plugin|install' not in calls


def test_matching_path_provider_is_accepted_when_managed_entry_is_absent(engine_install):
    run, _, graph, managed, bin_dir = engine_install
    (managed / 'entire-graph').unlink()
    (bin_dir / 'entire-graph').symlink_to(graph)
    result, _ = run()
    assert result.returncode == 0, result.stderr


def test_dispatcher_symlink_is_verified_by_its_target_bytes(engine_install, tmp_path):
    run, dispatcher, _, _, _ = engine_install
    real_dispatcher = tmp_path / 'trusted dispatcher build'
    dispatcher.rename(real_dispatcher)
    dispatcher.symlink_to(real_dispatcher)
    result, _ = run()
    assert result.returncode == 0, result.stderr
    assert str(real_dispatcher) in result.stdout


def test_missing_effective_provider_refuses_to_install_one(engine_install):
    run, _, _, managed, _ = engine_install
    (managed / 'entire-graph').unlink()
    result, calls = run()
    assert result.returncode != 0 and 'graph dispatch mismatch' in result.stderr
    assert 'pipx|install|' not in calls and 'plugin|install' not in calls


@pytest.mark.parametrize('settings', [{'UNKNOWN_HELP': '1'}, {'UNKNOWN_LIST': '1'}, {'BUILTIN_GRAPH': '1'},
                                      {'CAPABILITIES_EXIT': '7'}])
def test_unknown_dispatch_or_preflight_failure_has_no_package_side_effects(engine_install, settings):
    run, _, _, _, _ = engine_install
    result, calls = run(**settings)
    assert result.returncode != 0
    assert 'pipx|install|' not in calls
    assert 'installed:' not in result.stdout


def test_verified_health_failure_never_reports_install_ready(engine_install):
    run, _, _, _, _ = engine_install
    result, calls = run(DOCTOR_EXIT='7')
    assert result.returncode != 0 and 'health check failed' in result.stderr
    assert 'pipx|install|' in calls
    assert 'installed:' not in result.stdout


@pytest.mark.parametrize('setting', ['REPLACE_GRAPH_IN_INSTALL', 'REPLACE_GRAPH_IN_DOCTOR'])
def test_engine_replacement_during_install_or_health_check_is_not_ready(engine_install, setting):
    run, _, _, _, _ = engine_install
    result, calls = run(**{setting: '1'})
    assert result.returncode != 0 and 'SHA256 mismatch' in result.stderr
    assert 'pipx|install|' in calls
    assert 'installed:' not in result.stdout
    if setting == 'REPLACE_GRAPH_IN_INSTALL':
        assert 'entire|amu|doctor|' not in calls


def test_relative_path_entry_is_rejected_in_verified_mode(engine_install):
    run, _, _, _, bin_dir = engine_install
    result, calls = run(PATH=str(bin_dir) + ':')
    assert result.returncode != 0 and 'PATH entries' in result.stderr
    assert calls == ''


@pytest.mark.parametrize('digest', ['abc', 'z' * 64])
def test_invalid_digest_is_rejected_without_execution(engine_install, digest):
    run, _, _, _, _ = engine_install
    result, calls = run(graph_hash=digest)
    assert result.returncode != 0 and '64 hexadecimal' in result.stderr
    assert calls == ''
