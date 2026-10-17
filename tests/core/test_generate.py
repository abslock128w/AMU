"""C1: model-written probes for changed functions no test calls, kept only on execution evidence. The model is faked."""
import difflib
import json
import sys
from types import SimpleNamespace

from amu.core import analyze, generate


class FakeAPI:
    """Stands in for anthropic.Anthropic(): returns the probes given, with a usage bill, and records each request."""

    def __init__(self, probes, input_tokens=4000, output_tokens=1500, stop_reason='end_turn'):
        self.calls = []
        self.reply = SimpleNamespace(stop_reason=stop_reason, usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
                                     content=[SimpleNamespace(type='text', text=json.dumps(dict(probes=probes)))])
        self.messages = SimpleNamespace(create=self.create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **params):
        self.calls.append(params)
        return self.reply


LIB0 = 'def tested(x):\n    return x\n\n\ndef scale(x):\n    return x * 2\n'
LIB1 = 'def tested(x):\n    return x + 0\n\n\ndef scale(x):\n    return x * 3 if x > 10 else x * 2\n'
TESTS = 'from lib import tested\n\n\ndef test_tested():\n    assert tested(1) == 1\n'


def run(tmp_path, probes, monkeypatch, dollars=0.10, **fake):
    for name, text in {'lib.py': LIB1, 'tests/test_lib.py': TESTS}.items():
        (tmp_path/name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path/name).write_text(text)
    api = FakeAPI(probes, **fake)
    monkeypatch.setattr(generate, 'client', lambda: api)
    diff = ''.join(difflib.unified_diff(LIB0.splitlines(True), LIB1.splitlines(True), 'a/lib.py', 'b/lib.py'))
    report = analyze(tmp_path, diff, full_evidence=True, index_dir=tmp_path/'.index', run=True, python=sys.executable,
                     generate=(dollars, 5, 'claude-opus-5-5'))
    return report, api


def test_only_probes_that_pass_every_filter_are_kept_and_their_outcomes_come_from_execution(tmp_path, monkeypatch):
    probes = [dict(function='lib.py#scale', setup='from lib import scale', call='scale(50)', why='reaches the new branch'),
              dict(function='lib.py#scale', setup='from lib import scale', call='scale(1)', why='old branch, same either way'),
              dict(function='lib.py#scale', setup='import nothing_like_this', call='1', why='setup fails'),
              dict(function='lib.py#scale', setup='from lib import scale', call='scale(', why='does not compile'),
              dict(function='lib.py#other', setup='', call='1', why='not a changed function: dropped')]
    report, api = run(tmp_path, probes, monkeypatch)
    made = report['generated']
    assert (made['proposed'], made['compiled'], made['ran'], made['stable'], made['reached'], made['accepted']) == (4, 3, 2, 2, 2, 1), made
    [row] = report['generated_differences']
    assert row['label'] == 'generated test shows a difference' and row['call'] == 'scale(50)'
    assert row['before'][::2] == ['return', '100'] and row['after'][::2] == ['return', '150'], row
    assert "assert repr(scale(50)) == '150'" in row['test']
    # The request: only the changed function the tests never called, structured output, the default fallback.
    [params] = api.calls
    assert 'lib.py#scale' in params['messages'][0]['content'] and 'lib.py#tested' not in params['messages'][0]['content']
    assert params['output_config']['format']['type'] == 'json_schema' and params['fallbacks'] == 'default'
    assert made['cost'] == round((4000 * 4 + 1500 * 20) / 1e6, 5)
    from amu.ci.render import render
    body = render(report, dict(head='a' * 40, merge_base='b' * 40))
    assert '### Generated tests showing a difference (1)' in body and '`scale(50)`' in body and '<details>' in body, body


def test_no_request_crosses_the_dollar_cap_and_nothing_runs_without_a_client(tmp_path, monkeypatch):
    report, api = run(tmp_path, [], monkeypatch, dollars=0.01)
    assert api.calls == [] and report['generated']['cost'] == 0 and any('dollar cap' in g for g in report['gaps']), report['gaps']
    monkeypatch.setattr(generate, 'client', lambda: None)
    for name, text in {'lib.py': LIB1, 'tests/test_lib.py': TESTS}.items():
        (tmp_path/name).write_text(text)
    diff = ''.join(difflib.unified_diff(LIB0.splitlines(True), LIB1.splitlines(True), 'a/lib.py', 'b/lib.py'))
    report = analyze(tmp_path, diff, full_evidence=True, index_dir=tmp_path/'.index', run=True, python=sys.executable,
                     generate=(0.10, 5, 'claude-opus-5-5'))
    assert any('no Anthropic SDK or credentials' in g for g in report['gaps'])


def test_a_declined_request_is_a_gap_and_the_project_tests_never_see_the_key(tmp_path, monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-value-not-a-key')
    from amu.core import runners
    assert 'ANTHROPIC_API_KEY' not in runners.environment(tmp_path)
    report, _ = run(tmp_path, [], monkeypatch, stop_reason='refusal')
    assert any('declined' in g for g in report['gaps']) and report['generated']['accepted'] == 0
