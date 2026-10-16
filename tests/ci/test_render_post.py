from amu.ci import post
from amu.ci.render import LIMIT, MARKER, render

META = dict(head='abcdef1234567890', merge_base='1234567aaaa', amu_version='0.1.0', amu_ref='v0.1.0', install_ok=True)
EXECUTED = dict(ran=40, candidates=40, unrunnable=0, seconds=3.1, runner='pytest', budget=300)


def flip(ident, label='executed'):
    return dict(id=ident, before='passed', after='failed', label=label)


def test_first_line_is_flip_count_and_head_then_sections_in_order():
    report = dict(flips=2, predicted_failures=[flip('tests/test_a.py::test_one'), flip('tests/test_a.py::test_two')],
                  gaps=['3 lower-ranked candidate(s) not run'], executed=EXECUTED, report_hash='h1')
    body = render(report, META)
    assert body.splitlines()[0] == '**AMU: 2 flips** at `abcdef1`, among 40 tests run'
    assert body.index('### Flips (2)') < body.index('### Gaps (1)') < body.index('### What ran')
    assert '`tests/test_a.py::test_one`' in body and body.rstrip().endswith(MARKER)


def test_zero_flips_is_a_count_among_what_ran_not_a_pass():
    body = render(dict(flips=0, predicted_failures=[], gaps=[], executed=EXECUTED), META)
    assert body.startswith('**AMU: 0 flips** at `abcdef1`, among 40 tests run')
    assert 'Tests outside the candidates were not run.' in body
    for claim in ('safe', 'passes', 'no regression', 'all clear'):
        assert claim not in body.lower()


def test_nothing_ran_gives_no_count():
    body = render(dict(flips=None, predicted_failures=[], gaps=['shadow run stopped'], executed=dict(EXECUTED, ran=0)), META)
    assert body.startswith('**AMU: no flip count** at `abcdef1`: no candidate test ran')
    assert 'No test ran' in body


def test_runner_abort_comes_after_flips_and_before_gaps():
    report = dict(flips=None, failure=dict(error='ImportError: cannot import name x', ran_before=12, probed=12, runner='pytest'),
                  gaps=['g'], executed=EXECUTED)
    body = render(report, META)
    assert body.startswith('**AMU: no flip count** at `abcdef1`: the test runner aborts')
    assert body.index('### Runner abort') < body.index('### Gaps') and 'ran 12 of the same 12' in body


def test_error_and_failed_install():
    body = render(dict(error='no merge base'), dict(META, install_ok=False))
    assert body.startswith('**AMU: no flip count** at `abcdef1`: `no merge base`')
    assert "install command failed" in body and '### What ran' not in body


def test_untrusted_text_cannot_escape_its_code_span():
    nasty = 'tests/t.py::test_x`\n\n# Heading <img src=x> @owner ' + '`' * 3 + MARKER
    body = render(dict(flips=1, predicted_failures=[flip(nasty)], gaps=[nasty], executed=EXECUTED), META)
    assert '\n# Heading' not in body and '`' * 3 not in body
    for line in body.splitlines():
        if 'Heading' in line:
            assert line.startswith('- `') and line.count('`') == 2


def test_size_cap_lists_fewer_rows_and_says_how_many_more():
    many = [flip(f'tests/test_{i}.py::test_' + 'x' * 250) for i in range(2000)]
    body = render(dict(flips=2000, predicted_failures=many, gaps=[], executed=EXECUTED), META)
    assert len(body) <= LIMIT and 'more in the artifact' in body and body.startswith('**AMU: 2000 flips**')


class FakeGitHub:
    def __init__(self, comments):
        self.comments, self.calls = comments, []

    def pages(self, path):
        return iter(self.comments)

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        return dict(id=99)


def test_upsert_edits_only_our_marked_comment():
    spoof = dict(id=1, body='copied ' + MARKER, user=dict(login='someone'))
    mine = dict(id=2, body='old ' + MARKER, user=dict(login=post.AUTHOR))
    github = FakeGitHub([spoof, mine])
    assert post.upsert(github, 'o/r', 5, 'new') == ('edited', 2)
    assert github.calls == [('PATCH', '/repos/o/r/issues/comments/2', dict(body='new'))]


def test_upsert_creates_when_none():
    github = FakeGitHub([dict(id=1, body='hello', user=dict(login=post.AUTHOR))])
    assert post.upsert(github, 'o/r', 5, 'new') == ('created', 99)
    assert github.calls[0][:2] == ('POST', '/repos/o/r/issues/5/comments')


def test_flips_that_fail_the_trust_checks_are_listed_with_their_label_not_counted():
    from amu.ci.render import render
    report = dict(flips=1, predicted_failures=[dict(id='tests/t.py::test_a', before='passed', after='failed')],
                  flip_trust=[dict(id='tests/t.py::test_a', label='fails', why='failed again in every rerun'),
                              dict(id='tests/t.py::test_b', label='unstable', why='passed in the rerun alone in a fresh process'),
                              dict(id='tests/t.py::test_c', label='not caused by this change', why='it ran none of the changed lines')],
                  change_evidence=[dict(id='tests/t.py::test_new', before='failed', after='passed')],
                  replayed=[dict(id='tests/t.py::test_new', verdict='evidence'), dict(id='tests/t.py::test_old', verdict='none')],
                  order='pytest-randomly, seed 1, on both sides', gaps=[], executed=dict(ran=9, candidates=9, runner='pytest'))
    body = render(report, dict(head='a' * 40, merge_base='b' * 40))
    first = body.splitlines()[0]
    assert first.startswith('**AMU: 1 flip**') and "1 test of the pull request's own fail on the merge base" in first, first
    assert '### Flips not counted (2)' in body and '`unstable`' in body and '`not caused by this change`' in body
    assert "### Change evidence: the pull request's own tests (1)" in body and '`tests/t.py::test_new`' in body
    assert '1 test pass on the merge base too' in body and 'pytest-randomly' in body


def test_a_rerun_the_runner_could_not_make_is_named_on_the_flip():
    from amu.ci.render import render
    report = dict(flips=1, predicted_failures=[dict(id='tests/t.py::T::test_a', before='passed', after='failed')],
                  flip_trust=[dict(id='tests/t.py::T::test_a', label='fails', why='',
                                   reruns=dict(in_process=None, fresh_process='failed', after_set='failed'))],
                  gaps=[], executed=dict(ran=3, candidates=3, runner='django-runtests'))
    body = render(report, dict(head='a' * 40, merge_base='b' * 40))
    assert '`tests/t.py::T::test_a` (not rerun in the same process)' in body, body
