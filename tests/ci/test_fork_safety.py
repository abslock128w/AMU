import json
from pathlib import Path

from amu.ci import post
from amu.ci.post import resolve

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = sorted((ROOT/'ci').glob('*.yml'))


def test_no_template_or_action_uses_pull_request_target():
    for path in [*TEMPLATES, ROOT/'action.yml']:
        assert 'pull_request_target' not in path.read_text().replace('Never use pull_request_target', '')


def test_untrusted_job_has_a_read_only_token_and_does_not_post():
    text = (ROOT/'ci'/'amu-pr.yml').read_text()
    assert 'pull_request:' in text and 'contents: read' in text and 'write' not in text
    assert 'persist-credentials: false' in text and 'amu.ci.post' not in text
    assert 'amu.ci.post' not in (ROOT/'action.yml').read_text()


def test_posting_job_never_checks_out_or_runs_pull_request_code():
    text = (ROOT/'ci'/'amu-pr-comment.yml').read_text()
    assert 'workflow_run:' in text and 'actions/checkout' not in text
    assert 'permissions: {}' in text and 'pull-requests: write' in text
    assert '--no-deps' in text and '@${AMU_REF}' in text and '--event "$GITHUB_EVENT_PATH"' in text


class FakeGitHub:
    def __init__(self, pulls, listed=()):
        self.pulls, self.listed, self.calls = pulls, listed, []

    def request(self, method, path, body=None):
        self.calls.append(path)
        if '/pulls?' in path:
            return [dict(number=n) for n in self.listed]
        return self.pulls[int(path.rsplit('/', 1)[1])]


def event(**run):
    return dict(workflow_run=dict(dict(event='pull_request', conclusion='success', head_sha='h2', pull_requests=[]), **run))


def test_fork_pull_request_found_by_head_owner_and_branch():
    github = FakeGitHub({4: dict(state='open', head=dict(sha='h2'))}, listed=[4])
    run = event(head_repository=dict(owner=dict(login='forker')), head_branch='fix')
    assert resolve(github, 'o/r', run) == (4, 'h2')
    assert any('head=forker%3Afix' in call for call in github.calls)


def test_stale_cancelled_and_non_pr_runs_post_nothing():
    github = FakeGitHub({4: dict(state='open', head=dict(sha='h3'))})
    assert resolve(github, 'o/r', event(pull_requests=[dict(number=4)]))[0] is None
    assert resolve(github, 'o/r', event(conclusion='cancelled'))[0] is None
    assert resolve(github, 'o/r', event(event='push'))[0] is None


def test_artifact_cannot_redirect_the_comment(tmp_path, monkeypatch):
    (tmp_path/'report.json').write_text(json.dumps(dict(flips=0, predicted_failures=[], gaps=[],
                                                        executed=dict(ran=3, candidates=3))))
    (tmp_path/'meta.json').write_text(json.dumps(dict(pr=1, head='forged')))
    (tmp_path/'event.json').write_text(json.dumps(event(pull_requests=[dict(number=4)])))
    posted = []
    monkeypatch.setenv('GITHUB_TOKEN', 't')
    monkeypatch.setattr(post.GitHub, 'request', lambda self, m, p, b=None: dict(state='open', head=dict(sha='h2')))
    monkeypatch.setattr(post, 'upsert', lambda github, repo, pr, body, author: posted.append((pr, body(None))) or ('created', 1))
    assert post.main(['--dir', str(tmp_path), '--repo', 'o/r', '--event', str(tmp_path/'event.json')]) == 0
    assert posted[0][0] == 4 and '`h2`' in posted[0][1].splitlines()[0] and 'forged' not in posted[0][1]


def test_cache_holds_only_the_index_folder():
    # A repository may track its own files under .amu (AMU's does); restoring a cache over them would change the tree.
    text = (ROOT/'action.yml').read_text()
    assert 'path: .amu/index\n' in text and 'path: .amu\n' not in text


def test_every_action_step_is_one_step():
    # GitHub rejects a step with two 'if' keys; a lenient YAML parser silently keeps the last one.
    text = (ROOT/'action.yml').read_text()
    steps = text.split('\n  steps:\n', 1)[1].split('\n    - ')[1:]
    assert 'uses: actions/cache@v4' in text
    for step in steps:
        keys = [line.strip().split(':', 1)[0] for line in ('  ' + step).splitlines()
                if line.startswith(('  ', '      ')) and not line.startswith('        ') and ':' in line and not line.strip().startswith('#')]
        assert len(keys) == len(set(keys)), step
        assert ('uses' in keys) != ('run' in keys), step
