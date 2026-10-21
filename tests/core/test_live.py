"""``amu watch``: bare amu again on every save, with the index and the copy of the tree kept between runs."""
import subprocess
import sys
import threading
import time

from amu.core import analyze, index, live, shadow


def git(repo, *args):
    return subprocess.run(['git', '-c', 'user.email=t@t', '-c', 'user.name=t', *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout


def repository(tmp_path):
    repo = tmp_path/'repo'
    (repo/'tests').mkdir(parents=True)
    (repo/'calc.py').write_text('def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n')
    (repo/'tests'/'test_calc.py').write_text('from calc import add, sub\n\n\ndef test_add():\n    assert add(1, 2) == 3\n\n\n'
                                             'def test_sub():\n    assert sub(3, 2) == 1\n')
    (repo/'pytest.ini').write_text('[pytest]\n')
    git(repo, 'init', '-q', '-b', 'main')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'base')
    return repo


def outcome(report):
    return report['flips'], report.get('predicted_failures'), report['executed']['ran']


def test_kept_in_memory_the_index_and_the_copy_give_what_a_fresh_run_gives(tmp_path):
    repo = repository(tmp_path)
    edits = ['def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a - b\n',
             'def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return b - a\n',
             'def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b + 0\n']
    fresh, kept = [], []
    for text in edits:
        (repo/'calc.py').write_text(text)
        fresh.append(outcome(analyze(repo, python=sys.executable, runner='pytest', full_evidence=True)))
    index.MEMORY, shadow.MIRROR = True, tmp_path/'mirror'
    try:
        for n, text in enumerate(edits):
            (repo/'calc.py').write_text(text)
            (repo/f'extra{n}.txt').write_text('a file the copy must follow')
            shadow.MIRROR_CHANGED = None if n == 0 else {'calc.py', f'extra{n}.txt', f'extra{n - 1}.txt'}
            (repo/f'extra{n - 1}.txt').unlink(missing_ok=True)
            report = analyze(repo, python=sys.executable, runner='pytest', full_evidence=True)
            kept.append(outcome(report))
            assert report['seconds_before_running'] >= 0
        assert sorted(p.name for p in (tmp_path/'mirror').iterdir() if p.name.startswith('extra')) == ['extra2.txt']
        assert (tmp_path/'mirror'/'calc.py').read_text() != edits[2]  # it holds the code before the change
    finally:
        index.MEMORY, shadow.MIRROR, shadow.MIRROR_CHANGED = False, None, None
        shadow._mirror_state.update(synced=False, written=set())
        index._held.clear()
        index._complete.clear()
    assert kept == fresh and [f[0] for f in fresh] == [1, 1, 0], (kept, fresh)


def test_watch_runs_on_each_save_and_prints_the_summary_and_the_flips(tmp_path):
    repo = repository(tmp_path)
    (repo/'calc.py').write_text('def add(a, b):\n    return a + b + 0\n\n\ndef sub(a, b):\n    return a - b\n')
    seen = []

    def save():
        while not seen:
            time.sleep(0.1)
        (repo/'calc.py').write_text('def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a - b\n')
    threading.Thread(target=save, daemon=True).start()

    def show(out):
        seen.append(out)
    stop, started = threading.Event(), time.monotonic()
    watcher = threading.Thread(target=live.watch, args=(repo, show), kwargs=dict(python=sys.executable, runner='pytest', stop=stop))
    watcher.start()
    while len(seen) < 2 and time.monotonic() - started < 60:
        time.sleep(0.2)
    stop.set()
    watcher.join(30)
    assert not watcher.is_alive() and not index.MEMORY and shadow.MIRROR is None
    assert len(seen) >= 2, seen
    assert seen[0][0].startswith('EXECUTED') and len(seen[0]) == 1, seen[0]
    assert 'from the save to the first test run' in seen[1][0] and seen[1][1:] == ['  flip: tests/test_calc.py::test_add'], seen[1]
