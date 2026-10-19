import json
import subprocess
import sys
from pathlib import Path

import pytest

from amu.ci import run as ci_run
from amu.ci.render import render

ROOT = Path(__file__).resolve().parents[2]


def git(repo, *args):
    return subprocess.run(['git', '-c', 'user.email=t@t', '-c', 'user.name=t', *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def pull_request(tmp_path, monkeypatch):
    """A repository with main and a pull request branch that turns one test red."""
    monkeypatch.setenv('PYTHONPATH', str(ROOT))
    monkeypatch.delenv('GITHUB_OUTPUT', raising=False)
    repo = tmp_path/'repo'
    (repo/'tests').mkdir(parents=True)
    (repo/'calc.py').write_text('def add(a, b):\n    return a + b\n')
    (repo/'tests'/'test_calc.py').write_text('from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n\n'
                                             'def test_other():\n    assert True\n')
    git(repo, 'init', '-q', '-b', 'main')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'base')
    base = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'checkout', '-qb', 'feature')
    (repo/'calc.py').write_text('def add(a, b):\n    return a - b\n')
    git(repo, 'commit', '-qam', 'flip')
    return repo, base, git(repo, 'rev-parse', 'HEAD')


def test_flip_against_merge_base_is_saved(pull_request, tmp_path):
    repo, base, head = pull_request
    # A commit that lands on main after the branch point stays out of the diff.
    git(repo, 'checkout', '-q', 'main')
    (repo/'other.py').write_text('x = 1\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'later on main')
    later = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'checkout', '-q', 'feature')
    report, meta = ci_run.run(repo, later, head, tmp_path/'out', python=sys.executable, pr=7)
    assert report['flips'] == 1
    assert [f['id'] if isinstance(f, dict) else f for f in report['predicted_failures']] == ['tests/test_calc.py::test_add']
    assert meta['merge_base'] == base and meta['status'] == 'ran' and meta['pr'] == 7
    assert 'other.py' not in (tmp_path/'out'/'patch.diff').read_text()
    saved = json.loads((tmp_path/'out'/'report.json').read_text())
    assert saved['flips'] == 1


def test_a_pull_request_that_only_adds_a_blank_line_to_a_test_file_still_runs(pull_request, tmp_path):
    """sympy #30589: a blank line was the pull request's only edit to a test file; its other side could not be built and
    no test ran."""
    repo, base, _ = pull_request
    tests = repo/'tests'/'test_calc.py'
    tests.write_text(tests.read_text().replace('\ndef test_other', '\n\ndef test_other'))
    git(repo, 'commit', '-qam', 'spacing')
    report, meta = ci_run.run(repo, base, git(repo, 'rev-parse', 'HEAD'), tmp_path/'out', python=sys.executable)
    assert report.get('flips') == 1, (report.get('summary'), report.get('gaps'))
    assert not [gap for gap in report['gaps'] if 'could not be built' in gap], report['gaps']
    # A file the patch still cannot be undone in is taken from the merge base.
    command = ci_run.amu_command(tmp_path/'out'/'patch.diff', sys.executable, None, None, None, meta['merge_base'])
    assert command[command.index('--merge-base') + 1] == base


def test_checkout_not_at_head_runs_nothing(pull_request, tmp_path):
    repo, base, head = pull_request
    git(repo, 'checkout', '-q', 'main')
    report, meta = ci_run.run(repo, base, head, tmp_path/'out', python=sys.executable)
    assert 'not the pull request head' in report['error'] and meta['status'] == 'error'


def test_missing_base_is_an_error_not_a_crash(pull_request, tmp_path):
    repo, _, head = pull_request
    report, _ = ci_run.run(repo, 'f' * 40, head, tmp_path/'out', python=sys.executable)
    assert 'no merge base' in report['error']


def test_main_always_exits_zero_and_writes_outputs(pull_request, tmp_path, monkeypatch):
    repo, base, head = pull_request
    outputs = tmp_path/'github-output'
    monkeypatch.setenv('GITHUB_OUTPUT', str(outputs))
    monkeypatch.chdir(repo)
    assert ci_run.main(['--base', base, '--head', head, '--out', str(tmp_path/'out'), '--python', sys.executable]) == 0
    assert f'merge-base={base}' in outputs.read_text() and 'flips=1' in outputs.read_text()
    assert ci_run.main(['--base', 'nope', '--out', str(tmp_path/'bad')]) == 0
    assert 'error' in json.loads((tmp_path/'bad'/'report.json').read_text())


def test_wall_cap_stops_a_hanging_suite_and_says_so(pull_request, tmp_path, monkeypatch):
    repo, base, head = pull_request
    monkeypatch.setattr(ci_run, 'RESERVE', 0)
    (repo/'tests'/'conftest.py').write_text('import time\ntime.sleep(60)\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'slow suite')
    head = git(repo, 'rev-parse', 'HEAD')
    report, meta = ci_run.run(repo, base, head, tmp_path/'out', python=sys.executable, wall_cap=8)
    assert meta['status'] == 'ran' and meta['seconds'] < 30
    assert report.get('flips') is None and report['gaps'][0].startswith('not done before the wall cap: 2 of 2 candidate tests'), report
    assert render(report, meta).splitlines()[0].endswith('no candidate test ran; stopped at the wall cap of 8 s before it finished')


@pytest.fixture
def hangs_after_the_flip(pull_request, tmp_path):
    """The pull request on a merge base whose suite hangs from the first run after test_add failed on the head and
    passed on the merge base: every later step waits on it, as a slow suite outlasts what the wall cap leaves."""
    repo, _, _ = pull_request
    git(repo, 'checkout', '-q', 'main')
    (repo/'tests'/'conftest.py').write_text(
        f"import time\nfrom pathlib import Path\n\nMARKS = Path({str(tmp_path/'marks')!r})\n"
        "if (MARKS/'flipped').exists():\n    time.sleep(120)\n\n\n"
        "def pytest_runtest_logreport(report):\n"
        "    if report.when == 'call' and report.nodeid.endswith('::test_add'):\n"
        "        MARKS.mkdir(exist_ok=True)\n"
        "        if report.failed:\n            (MARKS/'failed').touch()\n"
        "        elif (MARKS/'failed').exists():\n            (MARKS/'flipped').touch()\n")
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'a suite that hangs once test_add has flipped')
    base = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'checkout', '-q', 'feature')
    git(repo, 'merge', '-q', '--no-edit', 'main')
    return repo, base, git(repo, 'rev-parse', 'HEAD')


def test_a_run_stopped_at_the_wall_cap_still_reports_the_flip_it_found(hangs_after_the_flip, tmp_path, monkeypatch):
    repo, base, head = hangs_after_the_flip
    monkeypatch.setattr(ci_run, 'RESERVE', 0)
    report, meta = ci_run.run(repo, base, head, tmp_path/'out', python=sys.executable, wall_cap=20)
    assert (tmp_path/'marks'/'flipped').exists(), 'the suite never hung; this test no longer shows the defect'
    assert report.get('flips') == 1, report
    assert [row['id'] for row in report['predicted_failures']] == ['tests/test_calc.py::test_add']
    assert meta['status'] == 'ran' and meta['seconds'] < 20
    # The reruns of the flip waited on the hang until amu's deadline; what came after them was not started.
    assert report['gaps'][0].startswith('not done before the wall cap: reruns of flips') and 'side-by-side' in report['gaps'][0]
    body = render(report, meta)
    assert body.splitlines()[0] == ('**AMU: 1 flip** at ' + f'`{head[:7]}`' + ', among 2 tests run; stopped at the wall cap '
                                    'of 20 s before it finished')
    assert '### Flips (1)' in body and '`tests/test_calc.py::test_add`' in body
    assert '`not done before the wall cap: reruns of flips' in body


def test_second_run_reuses_the_index_and_records_on_the_merge_base(pull_request, tmp_path):
    repo, base, head = pull_request
    _, first = ci_run.run(repo, base, head, tmp_path/'one', python=sys.executable)
    assert first['cached_index'] is False and first['recording'] is False
    report, second = ci_run.run(repo, base, head, tmp_path/'two', python=sys.executable, record=True, budget=60)
    assert second['cached_index'] is True and second['recording'] is True
    assert report['flips'] == 1 and not [gap for gap in report['gaps'] if 'recording' in gap]
    assert git(repo, 'worktree', 'list').count('\n') == 0  # the merge-base worktree is gone


def test_a_checkout_with_its_own_amu_package_does_not_shadow_the_installed_one(pull_request, tmp_path):
    repo, base, head = pull_request
    (repo/'amu'/'core').mkdir(parents=True)
    (repo/'amu'/'__init__.py').write_text('')
    (repo/'amu'/'core'/'__init__.py').write_text('')
    (repo/'amu'/'core'/'__main__.py').write_text('print("the checkout\'s amu")\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'an amu package of its own')
    head = git(repo, 'rev-parse', 'HEAD')
    report, _ = ci_run.run(repo, base, head, tmp_path/'out', python=sys.executable)
    assert report.get('flips') == 1, report


def test_amu_stopped_at_the_wall_cap_after_its_deadline_says_what_was_lost(pull_request, tmp_path, monkeypatch):
    repo, base, head = pull_request
    monkeypatch.setattr(ci_run, 'RESERVE', 0)
    # An amu that does not stop at its deadline: only the kill at the wall cap stops it.
    monkeypatch.setattr(ci_run, 'amu_command', lambda *args: [sys.executable, '-c', 'import time; time.sleep(60)'])
    report, meta = ci_run.run(repo, base, head, tmp_path/'out', python=sys.executable, wall_cap=8)
    assert report['error'] == 'stopped at the wall cap of 8 s; amu reported nothing' and meta['seconds'] < 30
    gaps = report.get('gaps', [])
    assert len(gaps) == 1 and gaps[0].endswith(' s and was stopped: what it found before the wall cap is lost'), report
    assert '### Gaps (1)' in render(report, meta)
    assert 0 < meta['deadline'] < 8 and f" s of its deadline of {meta['deadline']} s " in gaps[0]


def purelib(python=sys.executable):
    return Path(subprocess.check_output([python, '-c', 'import sysconfig; print(sysconfig.get_paths()["purelib"])'], text=True).strip())


def hook_files():
    from amu.core import edges, observe
    return [purelib()/name for name in (*edges.HOOK_FILES, *observe.HOOK_FILES) if (purelib()/name).exists()]


@pytest.fixture
def slow_to_record(pull_request):
    """The pull request on a merge base whose suite hangs under a recorder only, as sympy's whole suite outlasts any
    share the wall cap can give the tracer (#30476 in the retrospective)."""
    repo, _, _ = pull_request
    git(repo, 'checkout', '-q', 'main')
    (repo/'tests'/'conftest.py').write_text("import os, time\n\nif os.environ.get('AMU_EDGES_OUT') or os.environ.get('AMU_OBSERVE_RC'):\n"
                                            "    time.sleep(120)\n")
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'a suite too slow to record')
    base = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'checkout', '-q', 'feature')
    git(repo, 'merge', '-q', '--no-edit', 'main')
    return repo, base, git(repo, 'rev-parse', 'HEAD')


@pytest.fixture
def small_shares(monkeypatch):
    monkeypatch.setattr(ci_run, 'RESERVE', 0)
    monkeypatch.setattr(ci_run, 'RECORDING_MIN', 5)
    monkeypatch.setattr(ci_run, 'RECORDING_STORE', 4)
    monkeypatch.setattr(ci_run, 'amu_share', lambda budget: 30)


def test_a_recording_that_does_not_fit_its_share_leaves_amu_its_own(slow_to_record, small_shares, tmp_path):
    repo, base, head = slow_to_record
    report, meta = ci_run.run(repo, base, head, tmp_path/'one', python=sys.executable, wall_cap=45, record=True, budget=20)
    assert report.get('flips') == 1, report
    assert meta['status'] == 'ran' and meta['recording'] is False and meta['seconds'] < 45
    share = [int(gap.split('its share of ')[1].split()[0]) for gap in report['gaps'] if 'did not finish in its share' in gap]
    assert share and 10 <= share[0] <= 15  # 45 s less amu's 30, less what reaching the recording took
    assert hook_files() == []
    # The next run, with no more time for it, does not try again.
    report, meta = ci_run.run(repo, base, head, tmp_path/'two', python=sys.executable, wall_cap=45, record=True, budget=20)
    assert report.get('flips') == 1 and meta['seconds'] < 45
    assert any(f'earlier one did not finish in {share[0]} s' in gap for gap in report['gaps'])


def test_a_recording_killed_at_its_share_leaves_no_hook_behind(slow_to_record, small_shares, monkeypatch, tmp_path):
    repo, base, head = slow_to_record
    # The suite's own limit lies past the share, so the kill at the share is what stops it, before the recorder's
    # cleanup can run.
    monkeypatch.setattr(ci_run, 'RECORDING_STORE', -60)
    sweep = ci_run.remove_hooks
    monkeypatch.setattr(ci_run, 'remove_hooks', lambda python: None)
    try:
        ci_run.observe(repo.resolve(), base, sys.executable, None, 8)
        assert hook_files(), 'the kill did not leave the hook behind; this test no longer shows the defect'
    finally:
        sweep(sys.executable)
    monkeypatch.setattr(ci_run, 'remove_hooks', sweep)
    (ci_run.cache_dir(repo.resolve())/ci_run.ATTEMPT).unlink()  # or the second recording would not be tried
    note = ci_run.observe(repo.resolve(), base, sys.executable, None, 8)
    assert hook_files() == []
    assert note == 'recording on the merge base did not finish in its share of 8 s; static ranking only'


def test_a_hook_left_by_a_killed_run_is_removed_before_anything_and_reported(slow_to_record, monkeypatch, tmp_path):
    repo, base, head = slow_to_record
    # A run killed while its recording ran (SIGKILL: the job's own limit) never reaches its backstop.
    monkeypatch.setattr(ci_run, 'RECORDING_STORE', -60)
    sweep = ci_run.remove_hooks
    monkeypatch.setattr(ci_run, 'remove_hooks', lambda python: None)
    try:
        ci_run.observe(repo.resolve(), base, sys.executable, None, 8)
        left = [path.name for path in hook_files()]
        assert left, 'the kill did not leave the hook behind; this test no longer shows the defect'
        report, meta = ci_run.run(repo, base, head, tmp_path/'out', python=sys.executable, budget=20)
        assert hook_files() == []
        assert (f"removed a stale recording hook ({', '.join(left)}) from {purelib()}: an earlier recording was stopped "
                f"before it could remove it") in report['gaps'], report['gaps']
        assert report['flips'] == 1 and meta['status'] == 'ran'
    finally:
        sweep(sys.executable)


def test_no_recording_when_the_wall_cap_leaves_it_no_share(pull_request, tmp_path):
    repo, base, head = pull_request
    report, meta = ci_run.run(repo, base, head, tmp_path/'out', python=sys.executable, wall_cap=600, record=True, budget=300)
    assert report.get('flips') == 1 and meta['recording'] is False
    assert "recording skipped: the wall cap leaves 0 s for it after amu's share" in report['gaps']
