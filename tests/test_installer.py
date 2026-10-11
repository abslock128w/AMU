"""Exercise the real shell installer with isolated, offline command doubles."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMMIT = 'a' * 40


@pytest.fixture
def installer(tmp_path):
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    log = tmp_path / 'calls'
    stub = '''#!/bin/sh
name=${0##*/}
printf '%s' "$name" >> "$CALL_LOG"
for arg in "$@"; do printf '|%s' "$arg" >> "$CALL_LOG"; done
printf '\\n' >> "$CALL_LOG"
case "$name:$1:$2" in
  pipx:environment:--value) printf '%s\\n' "$STUB_BIN";;
  pipx:install:*) printf 'constraints|%s\\n' "$PIP_CONSTRAINT" >> "$CALL_LOG"; exit "${INSTALL_EXIT:-0}";;
  pipx:runpip:*) exit "${DEPS_EXIT:-0}";;
  entire:amu:doctor) exit "${DOCTOR_EXIT:-0}";;
  entire:graph:version) exit "${GRAPH_EXIT:-0}";;
  entire:plugin:install) exit "${PLUGIN_EXIT:-0}";;
  brew:*) exit "${BREW_EXIT:-0}";;
  amu:--version:) printf 'amu 0.2.0\\n'; exit "${VERSION_EXIT:-0}";;
esac
'''
    for name in ('entire', 'pipx', 'amu', 'brew'):
        path = bin_dir / name
        path.write_text(stub)
        path.chmod(0o755)

    def run(*args, **settings):
        for name in settings.pop('MISSING', '').split():
            (bin_dir / name).unlink()
        env = {'PATH': str(bin_dir), 'HOME': str(tmp_path), 'CALL_LOG': str(log),
               'STUB_BIN': str(bin_dir), **settings}
        result = subprocess.run(['/bin/sh', str(ROOT / 'scripts/install.sh'), *args],
                                env=env, cwd=tmp_path, capture_output=True, text=True, timeout=10)
        return result, log.read_text() if log.exists() else ''

    return run


def test_explicit_commit_is_used(installer):
    result, calls = installer('--commit', COMMIT)
    assert result.returncode == 0, result.stderr
    assert f'TWC-Thugs@{COMMIT}' in calls
    assert f'/{COMMIT}/constraints.txt' in calls
    assert 'pipx|runpip|amu|check' in calls
    assert 'installed:' in result.stdout
    assert 'unpinned' in result.stdout and 'not production verified' in result.stdout


@pytest.mark.parametrize('args', [(), ('--commit', 'main'), ('--commit', 'v0.2.0'),
                                  ('--commit',), ('--oops',), ('--local', '/missing')])
def test_invalid_selection_has_no_side_effects(installer, args):
    result, calls = installer(*args)
    assert result.returncode != 0
    assert calls == ''


@pytest.mark.parametrize('setting', ['INSTALL_EXIT', 'DEPS_EXIT', 'DOCTOR_EXIT', 'VERSION_EXIT'])
def test_failures_never_report_success(installer, setting):
    result, calls = installer('--commit', COMMIT, **{setting: '7'})
    assert result.returncode != 0
    assert 'installed:' not in result.stdout
    assert result.stderr.strip()
    if setting == 'INSTALL_EXIT':
        assert 'doctor' not in calls


def test_repeat_install_uses_requested_commit(installer):
    installer('--commit', COMMIT)
    result, calls = installer('--commit', 'b' * 40)
    assert result.returncode == 0
    installs = [line for line in calls.splitlines() if line.startswith('pipx|install|')]
    assert len(installs) == 2
    assert '--force' in installs[1] and '@' + 'b' * 40 in installs[1]


def test_local_wheel_with_spaces(installer, tmp_path):
    wheel = tmp_path / 'my package.whl'
    wheel.touch()
    (tmp_path / 'constraints.txt').write_text('rich==15.0.0\n')
    result, calls = installer('--local', str(wheel))
    assert result.returncode == 0, result.stderr
    assert f'pipx|install|--force|--backend|pip|--pip-args=--force-reinstall|{wheel}\n' in calls
    assert f'constraints|{tmp_path}/constraints.txt\n' in calls


def test_public_installer_matches():
    assert (ROOT / 'scripts/install.sh').read_bytes() == (ROOT / 'web/public/install.sh').read_bytes()


@pytest.mark.parametrize('settings', [
    {'MISSING': 'pipx'}, {'MISSING': 'entire brew'},
    {'MISSING': 'entire', 'BREW_EXIT': '7'},
    {'GRAPH_EXIT': '7', 'PLUGIN_EXIT': '7'}, {'GRAPH_EXIT': '7'},
])
def test_prerequisite_failure_stops_before_package_install(installer, settings):
    result, calls = installer('--commit', COMMIT, **settings)
    assert result.returncode != 0 and result.stderr.strip()
    assert 'pipx|install|' not in calls
    assert 'installed:' not in result.stdout


def test_failed_upgrade_is_not_reported_as_ready(installer):
    assert installer('--commit', COMMIT)[0].returncode == 0
    result, _ = installer('--commit', 'b' * 40, INSTALL_EXIT='7')
    assert result.returncode != 0
    assert 'installed:' not in result.stdout
    assert 'inspect pipx state' in result.stderr


def test_local_project(installer, tmp_path):
    (tmp_path / 'pyproject.toml').write_text('[project]\nname="amu"\n')
    (tmp_path / 'constraints.txt').write_text('rich==15.0.0\n')
    result, calls = installer('--local', str(tmp_path))
    assert result.returncode == 0
    assert f'pipx|install|--force|--backend|pip|--pip-args=--force-reinstall|{tmp_path}\n' in calls


def test_help_has_no_side_effects(installer):
    result, calls = installer('--help')
    assert result.returncode == 0 and 'Usage:' in result.stdout
    assert calls == ''


def test_missing_constraints_refuses_unlocked_install(installer, tmp_path):
    wheel = tmp_path / 'amu.whl'
    wheel.touch()
    result, calls = installer('--local', str(wheel))
    assert result.returncode != 0 and 'constraints.txt' in result.stderr
    assert calls == ''


@pytest.mark.parametrize('args', [
    ('--commit', COMMIT, '--entire-bin', '/missing'),
    ('--commit', COMMIT, '--graph-sha256', 'a' * 64),
    ('--commit', COMMIT, '--local', '/missing'),
    ('--commit', COMMIT, '--entire-bin', '/one', '--entire-bin', '/two'),
])
def test_partial_or_duplicate_engine_and_source_selection_has_no_side_effects(installer, args):
    result, calls = installer(*args)
    assert result.returncode != 0
    assert calls == ''
