"""The subscription backend for generated tests: Claude Code's ``claude -p``, with a fake ``claude`` executable."""
import difflib
import json
import sys

import pytest

from amu.ci.render import render
from amu.core import analyze, generate, runners

TARGETS = [dict(id='lib.py#scale', before='def scale(x):\n    return x * 2\n', after='def scale(x):\n    return x * 3\n')]
PROBE = dict(function='lib.py#scale', setup='from lib import scale', call='scale(50)', why='the new branch')


def fake(tmp_path, body):
    """A ``claude`` that records its arguments, stdin, folder and environment, then runs ``body`` (Python)."""
    path = tmp_path/'bin'/'claude'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'#!{sys.executable}\nimport json, os, sys, time\n'
                    f'record = dict(argv=sys.argv[1:], stdin=sys.stdin.read(), folder=os.listdir(), env=sorted(os.environ))\n'
                    f'open({str(tmp_path/"call.json")!r}, "w").write(json.dumps(record))\n' + body)
    path.chmod(0o755)
    return generate.ClaudeCLI(str(path))


def reply(**fields):
    return f'print(json.dumps({dict(type="result", subtype="success", is_error=False, **fields)!r}))\n'


def test_a_well_formed_reply_gives_its_probes_and_costs_nothing_per_token(tmp_path, monkeypatch):
    monkeypatch.setenv('ANTHROPIC_SECRET_THING', 'x')
    monkeypatch.setenv('PROJECT_SETTING', 'y')
    cli = fake(tmp_path, reply(structured_output=dict(probes=[PROBE, dict(PROBE, function='lib.py#other')])))
    probes, spent, problem = generate.ask_cli(cli, 'claude-opus-5-5', TARGETS, '', 30)
    assert (probes, spent, problem) == ([PROBE], 0.0, None)
    call = json.loads((tmp_path/'call.json').read_text())
    # Same prompt as the API backend, no tools, an empty folder, and none of the job's environment but what it needs.
    assert call['stdin'] == generate.prompt(TARGETS, '') and 'lib.py#scale' in call['stdin']
    assert call['argv'][:3] == ['-p', '--output-format', 'json'] and call['argv'][call['argv'].index('--tools') + 1] == ''
    assert json.loads(call['argv'][call['argv'].index('--json-schema') + 1]) == generate.SCHEMA
    assert call['folder'] == [] and 'ANTHROPIC_SECRET_THING' not in call['env'] and 'PROJECT_SETTING' not in call['env']


def test_a_reply_with_the_json_in_its_text_is_read_too(tmp_path):
    cli = fake(tmp_path, reply(result='```json\n' + json.dumps(dict(probes=[PROBE])) + '\n```'))
    assert generate.ask_cli(cli, 'claude-opus-5-5', TARGETS, '', 30) == ([PROBE], 0.0, None)


@pytest.mark.parametrize('body, problem', [
    ('print("not json at all")\n', 'claude -p exited with status 0 and no readable reply: not json at all'),
    (reply(result='no probes here'), 'the model returned no readable probes'),
    (reply(structured_output=dict(something='else')), 'the model returned no readable probes'),
    ('print(json.dumps(dict(type="result", is_error=True, result="Not logged in")))\nsys.exit(1)\n', 'claude -p failed: Not logged in'),
    ('print("boom", file=sys.stderr)\nsys.exit(3)\n', 'claude -p exited with status 3 and no readable reply: boom'),
], ids=['malformed', 'text-without-json', 'wrong-shape', 'error-reply', 'non-zero-exit'])
def test_a_reply_that_cannot_be_read_gives_no_probes_and_says_why(tmp_path, body, problem):
    assert generate.ask_cli(fake(tmp_path, body), 'claude-opus-5-5', TARGETS, '', 30) == ([], 0.0, problem)


def test_a_reply_that_does_not_come_in_time_is_stopped(tmp_path):
    cli = fake(tmp_path, 'time.sleep(60)\n')
    assert generate.ask_cli(cli, 'claude-opus-5-5', TARGETS, '', 2) == ([], 0.0, 'claude -p did not answer in 2 s')


def test_claude_is_chosen_only_when_asked_for_with_no_api_key_and_a_way_to_sign_in(tmp_path, monkeypatch):
    cli = fake(tmp_path, '')
    monkeypatch.setattr(generate, 'client', lambda: None)
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.delenv('CLAUDE_CODE_OAUTH_TOKEN', raising=False)
    monkeypatch.setenv('PATH', str(tmp_path/'nowhere'))
    assert generate.backend(cli=True) is None
    monkeypatch.setenv('CLAUDE_CODE_OAUTH_TOKEN', 't')
    assert isinstance(generate.backend(cli=True), generate.ClaudeCLI) and generate.backend(cli=False) is None
    monkeypatch.delenv('CLAUDE_CODE_OAUTH_TOKEN')
    monkeypatch.setenv('PATH', str(tmp_path/'bin'))
    assert generate.backend(cli=True).command == cli.command
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'k')
    assert generate.backend(cli=True) is None  # the API backend (client() here: none)


def test_the_token_never_reaches_the_projects_tests(monkeypatch, tmp_path):
    monkeypatch.setenv('CLAUDE_CODE_OAUTH_TOKEN', 't')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'k')
    env = runners.environment(tmp_path)
    assert 'CLAUDE_CODE_OAUTH_TOKEN' not in env and 'ANTHROPIC_API_KEY' not in env


LIB0 = 'def tested(x):\n    return x\n\n\ndef scale(x):\n    return x * 2\n'
LIB1 = 'def tested(x):\n    return x + 0\n\n\ndef scale(x):\n    return x * 3 if x > 10 else x * 2\n'


def test_the_same_filter_keeps_claudes_probes_and_the_comment_names_the_backend(tmp_path, monkeypatch):
    root = tmp_path/'repo'
    for name, text in {'lib.py': LIB1, 'tests/test_lib.py': 'from lib import tested\n\n\ndef test_tested():\n    assert tested(1) == 1\n'}.items():
        (root/name).parent.mkdir(parents=True, exist_ok=True)
        (root/name).write_text(text)
    cli = fake(tmp_path, reply(structured_output=dict(probes=[PROBE, dict(PROBE, call='scale(1)', why='old branch')])))
    monkeypatch.setattr(generate, 'backend', lambda cli_allowed=False: cli if cli_allowed else None)
    diff = ''.join(difflib.unified_diff(LIB0.splitlines(True), LIB1.splitlines(True), 'a/lib.py', 'b/lib.py'))
    report = analyze(root, diff, full_evidence=True, index_dir=tmp_path/'.index', run=True, python=sys.executable,
                     generate=(0.0, 5, 'claude-opus-5-5', True))
    made = report['generated']
    assert (made['backend'], made['cost_basis'], made['cost'], made['accepted']) == ('claude -p', 'plan usage', 0.0, 1), made
    assert [row['call'] for row in report['generated_differences']] == ['scale(50)']
    body = render(report, dict(head='a' * 40, wall_cap=1200))
    assert 'through `claude -p`, at a cost of Claude plan usage, within 5 min' in body, body
    assert '$0.000 of a' not in body
