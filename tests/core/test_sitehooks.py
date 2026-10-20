"""The recorders' hook files never outlive their recording: a signal takes them out, and after a kill the next run
does and says so; a recording that still runs keeps its own (sitehooks)."""
import difflib
import importlib.util
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from amu.core import analyze, edges, observe

ROOT = Path(__file__).resolve().parents[2]
RECORDERS = {  # how each recorder is started, and the hook files it installs
    'edges': ('edges.record(sys.argv[1], sys.executable, "pytest")', edges.HOOK_FILES),
    'observe': ('observe.record(sys.argv[1], sys.executable, "pytest")', observe.HOOK_FILES),
    'plain': ('edges.record(sys.argv[1], sys.executable, "pytest", plain=True)', edges.HOOK_FILES[:1]),
}
KINDS = [pytest.param('edges', marks=pytest.mark.skipif(sys.version_info < (3, 12), reason='sys.monitoring needs Python 3.12')),
         pytest.param('observe', marks=pytest.mark.skipif(importlib.util.find_spec('coverage') is None, reason='needs coverage')),
         'plain']


def purelib():
    return Path(subprocess.check_output([sys.executable, '-c', 'import sysconfig; print(sysconfig.get_paths()["purelib"])'], text=True).strip())


def hook_files():
    """What the recorders left in this interpreter's site-packages, owner markers included."""
    return sorted(path.name for path in purelib().iterdir() if path.name.startswith(('amu_edges_hook', 'zzz_amu_edges',
                                                                                    'amu_observe_hook', 'zzz_amu_observe')))


def stale(files):
    return (f"removed a stale recording hook ({', '.join(files)}) from {purelib()}: an earlier recording was stopped before "
            f"it could remove it")


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A project whose suite, started by a recorder these tests start, waits there to be stopped."""
    monkeypatch.setenv('PYTHONPATH', str(ROOT))
    root = tmp_path/'repo'
    (root/'tests').mkdir(parents=True)
    (root/'calc.py').write_text('def add(a, b):\n    return a + b\n')
    (root/'tests'/'test_calc.py').write_text('from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n')
    (root/'tests'/'conftest.py').write_text("import os, pathlib, time\n\nif os.environ.get('HOLD_SUITE'):\n"
                                            "    pathlib.Path(os.environ['HOLD_SUITE']).touch()\n    time.sleep(120)\n")
    assert hook_files() == []
    return root


@pytest.fixture
def recorders(project, tmp_path):
    """Starts a recorder process on the project and returns it once the suite runs, with the hook in place. Whatever
    the test leaves is stopped and taken out afterwards."""
    started = []

    def start(kind):
        held = tmp_path/f'held-{len(started)}'
        process = subprocess.Popen([sys.executable, '-c', 'import sys\nfrom amu.core import edges, observe\n' + RECORDERS[kind][0],
                                    str(project)], env=dict(os.environ, HOLD_SUITE=str(held)), stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True)
        started.append(process)
        deadline = time.monotonic() + 60
        while not held.exists():
            if process.poll() is not None or time.monotonic() > deadline:
                os.killpg(process.pid, signal.SIGKILL)
                pytest.fail(f'the suite never started under the {kind} recorder: {process.communicate()}')
            time.sleep(0.05)
        return process

    yield start
    for process in started:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
        process.communicate()
    folder = purelib()
    for name in hook_files():
        (folder/name).unlink()


def change(root):
    source = (root/'calc.py').read_text()
    changed = source.replace('a + b', 'a - b')
    (root/'calc.py').write_text(changed)
    return ''.join(difflib.unified_diff(source.splitlines(True), changed.splitlines(True), 'a/calc.py', 'b/calc.py'))


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('name', ['SIGTERM', 'SIGINT', 'SIGHUP'])
def test_a_recording_stopped_by_a_signal_mid_suite_takes_its_hook_out(recorders, kind, name):
    number = getattr(signal, name)
    if signal.getsignal(number) is signal.SIG_IGN:
        pytest.skip(f'{name} is ignored here, and a recorder leaves an ignored signal ignored')
    process = recorders(kind)
    assert set(RECORDERS[kind][1]) <= set(hook_files())
    os.kill(process.pid, number)
    process.communicate(timeout=60)
    assert hook_files() == []


@pytest.mark.parametrize('kind', KINDS)
def test_after_a_recording_is_killed_the_next_run_removes_its_hook_and_says_so(project, recorders, kind, tmp_path):
    process = recorders(kind)
    os.killpg(process.pid, signal.SIGKILL)  # as amu.ci.run stops a recording at its share: nothing can be caught
    process.communicate()
    files = RECORDERS[kind][1]
    assert set(files) <= set(hook_files()), 'the kill did not leave the hook behind; this test no longer shows the defect'
    report = analyze(project, change(project), full_evidence=True, index_dir=tmp_path/'.index', run=True, python=sys.executable)
    assert hook_files() == []
    assert stale(files) in report['gaps'], report['gaps']
    assert report['flips'] == 1, report


@pytest.mark.parametrize('kind', KINDS)
def test_a_hook_whose_recording_still_runs_is_left_alone(project, recorders, kind, tmp_path):
    process = recorders(kind)
    own = set(RECORDERS[kind][1])
    # Beside it, the other recorder's hook, left by a kill.
    other, hook = (edges.HOOK_FILES, edges.HOOK) if kind == 'observe' else (observe.HOOK_FILES, observe.HOOK)
    for name, text in zip(other, (hook, f'import {Path(other[0]).stem}\n')):
        (purelib()/name).write_text(text)
    report = analyze(project, change(project), full_evidence=True, index_dir=tmp_path/'.index', run=True, python=sys.executable)
    assert [gap for gap in report['gaps'] if 'stale recording hook' in gap] == [stale(other)], report['gaps']
    assert own <= set(hook_files()) and not set(other) & set(hook_files())
    marker = RECORDERS[kind][1][0].replace('.py', '.owner')
    assert (purelib()/marker).read_text().strip() == str(process.pid)
    process.terminate()
    process.communicate(timeout=60)
    assert hook_files() == []


def test_a_second_recording_never_takes_over_the_hook_of_one_that_runs(project, recorders):
    recorders('plain')
    info = edges.record(project, sys.executable, 'pytest', plain=True)
    assert info['gaps'] == ['could not install the edge hook in the interpreter; nothing recorded'], info
    assert 'amu_edges_hook.py' in hook_files()
