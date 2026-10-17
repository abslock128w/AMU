import json
import subprocess
import sys

from amu.ci import counters, post, verify
from amu.ci.render import render

H1, H2, MB = 'a' * 40, 'b' * 40, 'c' * 40


def report(flips, ran=10, ids=()):
    return dict(flips=flips, predicted_failures=[dict(id=i, before='passed', after='failed') for i in ids], gaps=['g'],
                executed=dict(ran=ran, candidates=ran, runner='pytest'))


def test_record_keeps_counts_and_shas_only():
    nasty = report(2, ids=['tests/test_secret.py::test_password'])
    nasty['error'] = 'source line: password = "hunter2"'
    row = counters.record(nasty, dict(head=H1, merge_base='not a sha', seconds='12', pr=3), dict(checked=2, held='x', false=1))
    assert row == dict(head=H1, merge_base=None, flips=2, ran=10, candidates=10, gaps=1, checked=2, held=None, false=1,
                       aborted=False, error=True, seconds=None, evidence=None, replayed=None, unstable=None, not_caused=None,
                       generated=None, generated_kept=None, generated_cost=None, behaviour=None)
    text = counters.block([row])
    assert 'secret' not in text and 'hunter2' not in text and '-->' not in text[:-3]


def test_history_survives_a_round_trip_and_a_rerun_replaces_its_push():
    first = counters.record(report(1), dict(head=H1))
    body = render(report(1), dict(head=H1), extra=counters.block([first]))
    second = counters.record(report(0), dict(head=H2))
    pushes = counters.extend(body, second)
    assert [p['head'] for p in pushes] == [H1, H2]
    again = counters.extend(counters.block(pushes), counters.record(report(0, ran=5), dict(head=H2)))
    assert [(p['head'], p['ran']) for p in again] == [(H1, 10), (H2, 5)]


def test_summary_measures_both_bars():
    flagged_then_cleared = [counters.record(report(2), dict(head=H1), dict(checked=2, held=2, false=0, other=0)),
                            counters.record(report(0), dict(head=H2))]
    flagged_not_cleared = [counters.record(report(1), dict(head=H1), dict(checked=1, held=0, false=1, other=0))]
    clear_throughout = [counters.record(report(0), dict(head=H1))]
    cleared_but_closed = flagged_then_cleared
    out = counters.summarize([('o/r', 1, True, flagged_then_cleared), ('o/r', 2, True, flagged_not_cleared),
                              ('o/r', 3, True, clear_throughout), ('o/r', 4, False, cleared_but_closed)])
    assert out == dict(pull_requests=4, with_a_flip=3, cleared_before_merge=1, cleared_share=0.333,
                       flips_rerun=5, false_flips=1, false_flip_rate=0.2, flips_labelled_unstable=0,
                       flips_labelled_not_caused=0, change_evidence_rows=0, generated_probes=0, generated_kept=0,
                       generated_acceptance=None, generated_prs=0, generated_cost_per_pr=None,
                       acted_on=out['acted_on'])
    assert out['acted_on']['flips'] == dict(pull_requests=3, cleared_before_merge=1, share=0.333)
    assert counters.summarize([('o/r', n, True, clear_throughout) for n in range(80)])['pull_requests'] == 50


def test_poster_adds_counters_only_when_opted_in(tmp_path, monkeypatch):
    (tmp_path/'report.json').write_text(json.dumps(report(1, ids=['tests/t.py::test_a'])))
    (tmp_path/'verify.json').write_text(json.dumps(dict(checked=1, held=1, false=0, other=0, states={})))
    bodies = []
    monkeypatch.setenv('GITHUB_TOKEN', 't')
    monkeypatch.setattr(post, 'upsert', lambda github, repo, pr, body, author: bodies.append(body(None)) or ('created', 1))
    monkeypatch.setattr(post, 'check', lambda *a: None)
    for opted in (False, True):
        (tmp_path/'meta.json').write_text(json.dumps(dict(head=H1, counters=opted)))
        post.main(['--dir', str(tmp_path), '--repo', 'o/r', '--pr', '4', '--verify-dir', str(tmp_path)])
    assert 'amu-ci-counters' not in bodies[0] and 'amu-ci-counters' in bodies[1]
    assert counters.history(bodies[1])[0]['held'] == 1
    assert 'Rerun on the merge base in a fresh job: 1 passed again, 0 failed there too' in bodies[0]


def test_verify_reruns_flips_on_the_merge_base(tmp_path, monkeypatch):
    repo = tmp_path/'repo'
    (repo/'tests').mkdir(parents=True)
    (repo/'tests'/'test_x.py').write_text('def test_held():\n    assert True\n\ndef test_false():\n    assert False\n')
    subprocess.run(['git', 'init', '-q'], cwd=repo, check=True)
    flagged = report(3, ids=['tests/test_x.py::test_held', 'tests/test_x.py::test_false', 'tests/test_x.py::test_gone'])
    result = verify.verify(repo, flagged, sys.executable, timeout=120)
    assert (result['checked'], result['held'], result['false'], result['other']) == (3, 1, 1, 1)
    assert result['states']['tests/test_x.py::test_false'] == 'failed'
    (tmp_path/'in').mkdir()
    (tmp_path/'in'/'report.json').write_text(json.dumps(report(0)))
    monkeypatch.chdir(repo)
    assert verify.main(['--dir', str(tmp_path/'in'), '--out', str(tmp_path/'out'), '--python', sys.executable]) == 0
    assert json.loads((tmp_path/'out'/'verify.json').read_text())['checked'] == 0
