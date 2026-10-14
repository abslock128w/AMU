"""The blind-trial hook, end to end on a small pytest project: hook.py drives bare amu as a subprocess."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BLIND = ROOT/'evaluation/before-tests/blind'


def git(work, *args):
    subprocess.run(['git', '-C', str(work), '-c', 'user.email=t@example.invalid', '-c', 'user.name=t', *args], check=True, capture_output=True)


def setup(tmp_path, hook='hook.py'):
    work, run = tmp_path/'ws', tmp_path/'run'
    (work/'tests').mkdir(parents=True)
    run.mkdir()
    (work/'lib.py').write_text('def work():\n    return 1\n')
    (work/'tests/test_lib.py').write_text('from lib import work\n\n\ndef test_work():\n    assert work() == 1\n')
    git(work, 'init', '-q')
    git(work, 'add', '-A')
    git(work, 'commit', '-qm', 'start')
    config = dict(workspace=str(work), run=str(run), tools=str(BLIND), python=sys.executable, budget=900, arm='amu', amu=str(ROOT),
                  amu_args=['--full-evidence'])
    (run/'config.json').write_text(json.dumps(config))
    return work, run


def shell(run, uid, change, hook='hook.py'):
    """A Bash tool call that changes files: PreToolUse, the change, PostToolUse. Returns what the hook injected."""
    def event(phase):
        payload = dict(hook_event_name=phase, tool_name='Bash', tool_use_id=uid, tool_input=dict(command='python3 - <<EOF'))
        done = subprocess.run([sys.executable, str(BLIND/hook), str(run/'config.json')], input=json.dumps(payload), capture_output=True,
                              text=True, check=True)
        return json.loads(done.stdout)['hookSpecificOutput']['additionalContext'] if done.stdout.strip() else None
    event('PreToolUse')
    change()
    return event('PostToolUse')


def test_a_flagged_test_renamed_away_stays_listed(tmp_path):
    work, run = setup(tmp_path)
    first = shell(run, 'a', lambda: (work/'lib.py').write_text('def work():\n    return 2\n'))
    assert '1 existing test(s)' in first and 'test_lib.test_work' in first.replace('tests.', ''), first
    # The agent renames the failing test and asserts the new value: the old test no longer exists to rerun. The engine
    # runs the test as it was against the changed code; it still fails there.
    second = shell(run, 'b', lambda: (work/'tests/test_lib.py').write_text(
        'from lib import work\n\n\ndef test_work_now_two():\n    assert work() == 2\n'))
    assert 'test_work (test edited, not fixed: as edited it is gone)' in second and 'aborts' not in second, second
    log = [json.loads(line) for line in (run/'hook.jsonl').read_text().splitlines()]
    assert log[-1]['only'] == 1 and log[-1]['flips'] == 1, log[-1]


def test_the_recheck_keeps_a_flagged_test_whose_file_hash_changed(tmp_path, monkeypatch):
    """When the check could not run a flagged test in its original form, the file hash decides."""
    import importlib.util
    import types

    work, run = setup(tmp_path)
    (work/'lib.py').write_text('def work():\n    return 2\n')
    ident = 'tests/test_lib.py::test_work'
    monkeypatch.setattr(sys, 'argv', ['hook.py', str(run/'config.json')])
    spec = importlib.util.spec_from_file_location('blind_hook_under_test', BLIND/'hook.py')
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    production, tests_then, _ = hook.keys(work)
    flagged = dict(file=hook.file_hash(work, ident), tests=tests_then)
    (run/'last-check.json').write_text(json.dumps(dict(key='earlier', production=production, tests='earlier', body='', flips=1,
                                                       failing=['test_lib.test_work'], ids=[ident], hashes={ident: flagged})))
    (work/'tests/test_lib.py').write_text('from lib import work\n\n\ndef test_work():\n    assert work() == 2\n')
    calls, real_run = [], subprocess.run

    def fake_run(command, **kwargs):
        if 'amu.core' not in command:
            return real_run(command, **kwargs)
        calls.append(command)
        packet = dict(flips=0, predicted_failures=[], gaps=[], summary='EXECUTED 1 of 1 candidate tests after the change with pytest in 0.1 s')
        return types.SimpleNamespace(stdout=json.dumps(packet), stderr='', returncode=0)
    monkeypatch.setattr(hook.subprocess, 'run', fake_run)
    row = {}
    text = hook.check(run, work.resolve(), 'Automatic test check', row)
    assert '--only' in calls[0] and ident in calls[0], calls
    assert row['kept_by_hash'] == [ident] and 'test_lib.test_work (test edited, not fixed: as edited it passes)' in text.replace('tests.', ''), text
    # The same file, unchanged since it was flagged, and not failing: it drops off.
    _, tests_now, _ = hook.keys(work)
    (run/'last-check.json').write_text(json.dumps(dict(key='earlier', production=production, tests='earlier', body='', flips=1,
                                                       failing=['test_lib.test_work'], ids=[ident],
                                                       hashes={ident: dict(file=hook.file_hash(work, ident), tests=tests_now)})))
    row = {}
    hook.check(run, work.resolve(), 'Automatic test check', row)
    assert 'kept_by_hash' not in row and row['flips'] == 0, row


def test_a_flagged_test_fixed_in_the_code_drops_off(tmp_path):
    work, run = setup(tmp_path)
    shell(run, 'a', lambda: (work/'lib.py').write_text('def work():\n    return 2\n'))
    fixed = shell(run, 'b', lambda: (work/'lib.py').write_text('def work():\n    return 3 - 2\n'))
    assert 'none that passed before now fails' in fixed, fixed


def test_the_dumb_hook_runs_the_modules_for_touched_files(tmp_path):
    work, run = setup(tmp_path)
    (work/'helpers.py').write_text('def double(x):\n    return 2 * x\n')
    (work/'tests/test_misc.py').write_text('from helpers import double\n\n\ndef test_double():\n    assert double(2) == 4\n')
    git(work, 'add', '-A')
    git(work, 'commit', '-qm', 'more')
    named = shell(run, 'a', lambda: (work/'lib.py').write_text('def work():\n    return 2\n'), hook='dumb_hook.py')
    assert 'in 1 test module(s) for the files you changed (tests/test_lib.py)' in named and '1 failed' in named, named
    assert 'test_lib.test_work' in named, named
    imported = shell(run, 'b', lambda: (work/'helpers.py').write_text('def double(x):\n    return x + x + 1\n'), hook='dumb_hook.py')
    assert 'tests/test_misc.py' in imported and '2 failed' in imported, imported
    log = [json.loads(line) for line in (run/'hook.jsonl').read_text().splitlines()]
    assert log[-1]['why'] == {'tests/test_lib.py': 'named after lib.py', 'tests/test_misc.py': 'imports helpers'}, log[-1]
    assert 'amu' not in json.dumps(log).lower().replace('amu_', ''), 'the dumb hook must not run AMU'


def test_a_recheck_that_runs_nothing_keeps_the_flag(tmp_path):
    work, run = setup(tmp_path)
    shell(run, 'a', lambda: (work/'lib.py').write_text('def work():\n    return 2\n'))
    broken = shell(run, 'b', lambda: (work/'tests/conftest.py').write_text('import nope\n'))
    assert 'test_work' not in broken or 'FAILED' in broken or 'could not' in broken, broken
    fixed = shell(run, 'c', lambda: (work/'tests/conftest.py').write_text('# fixed\n'))
    assert 'test_lib.test_work' in fixed.replace('tests.', '') and 'now fail' in fixed, fixed


def test_a_full_check_after_a_rename_still_lists_the_flagged_test(tmp_path):
    work, run = setup(tmp_path)
    shell(run, 'a', lambda: (work/'lib.py').write_text('def work():\n    return 2\n'))
    shell(run, 'b', lambda: (work/'tests/test_lib.py').write_text('from lib import work\n\n\ndef test_work_now_two():\n    assert work() == 2\n'))
    third = shell(run, 'c', lambda: (work/'lib.py').write_text('def work():\n    # still two\n    return 2\n'))
    assert 'test_work (test edited, not fixed' in third, third


def test_is_test_keeps_a_package_named_test_as_production():
    import importlib.util
    spec = importlib.util.spec_from_file_location('hook_is_test', BLIND/'hook.py')
    source = (BLIND/'hook.py').read_text()
    namespace = {}
    exec(compile(source[source.index('def is_test'):source.index('def keys')], 'hook.py', 'exec'), namespace)
    is_test = namespace['is_test']
    assert not is_test('django/test/testcases.py') and not is_test('django/contrib/admin/tests.py')
    assert is_test('tests/mail/tests.py') and is_test('sympy/core/tests/test_basic.py') and is_test('pkg/conftest.py')


def test_the_dumb_hook_counts_parametrized_failures_and_survives_a_broken_module(tmp_path):
    work, run = setup(tmp_path)
    (work/'pkg').mkdir()
    (work/'pkg/__init__.py').write_text('def value():\n    return 1\n\n\ndef helper():\n    return 0\n')
    (work/'tests/test_pkg.py').write_text('import pytest\nfrom pkg import value\n\n\n@pytest.mark.parametrize("x", [1, 2, 3])\n'
                                          'def test_value(x):\n    assert value() == 1 or x != 1\n')
    (work/'tests/test_helper.py').write_text('from pkg import helper\n\n\ndef test_helper():\n    assert helper() == 0\n')
    git(work, 'add', '-A')
    git(work, 'commit', '-qm', 'pkg')
    text = shell(run, 'a', lambda: (work/'pkg/__init__.py').write_text('def value():\n    return 2\n'), hook='dumb_hook.py')
    assert 'tests/test_pkg.py' in text and 'tests/test_helper.py' in text, text
    assert 'test_pkg.test_value' in text and 'test_helper' in text and '2 failed' in text, text


def test_the_dumb_hook_runs_the_modules_under_a_touched_conftest(tmp_path):
    work, run = setup(tmp_path)
    text = shell(run, 'a', lambda: (work/'tests/conftest.py').write_text('import pytest\n'), hook='dumb_hook.py')
    assert 'tests/test_lib.py' in text and '1 passed' in text, text
