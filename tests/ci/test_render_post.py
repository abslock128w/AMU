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


def test_the_check_is_informational_and_annotates_only_changed_files():
    from amu.ci import post

    class Checks:
        def __init__(self):
            self.created = []

        def pages(self, path):
            assert path.endswith('/pulls/7/files')
            return [dict(filename='lib.py')]

        def request(self, method, path, body=None):
            self.created.append(body)
            return dict(id=99)
    hunk = dict(file='lib.py', hunk=2, of=3, start=12, end=12)
    forged = dict(file='.github/workflows/x.yml', hunk=1, of=1, start=1, end=1)
    report = dict(flips=1, predicted_failures=[dict(id='tests/t.py::test_a')], gaps=[], executed=dict(ran=4, candidates=4),
                  flip_trust=[dict(id='tests/t.py::test_a', label='fails', isolated=dict(hunks=[hunk, forged], finished=True))])
    github = Checks()
    assert post.check(github, 'o/r', 7, 'h' * 40, report, dict(head='h' * 40)) == 99
    [body] = github.created
    assert body['conclusion'] == 'neutral' and body['head_sha'] == 'h' * 40
    assert [(n['path'], n['start_line']) for n in body['output']['annotations']] == [('lib.py', 12)]
    quiet = dict(flips=0, predicted_failures=[], gaps=['g'], executed=dict(ran=4, candidates=4))
    github = Checks()
    post.check(github, 'o/r', 7, 'h' * 40, quiet, dict(head='h' * 40))
    assert github.created[0]['conclusion'] == 'success'


def test_quiet_mode_folds_a_report_with_nothing_found_into_one_line():
    from amu.ci.render import render
    quiet = dict(flips=0, predicted_failures=[], gaps=['20 call sites unresolved'], executed=dict(ran=4, candidates=4, runner='pytest'))
    body = render(quiet, META, quiet=True)
    first, rest = body.split('\n', 1)
    assert first.startswith('**AMU: 0 flips**') and rest.lstrip().startswith('<details>') and '### What ran' in rest
    loud = dict(quiet, flips=1, predicted_failures=[dict(id='t.py::test_a')])
    assert '<details><summary>What ran' not in render(loud, META, quiet=True)


def test_behaviour_row_shows_where_the_two_outcomes_diverge_past_a_long_shared_prefix():
    shared = '<html><body>' + 'same header text ' * 40
    before = dict(result=['return', repr(shared + 'old footer wording')], stdout='', calls=[], args_after='')
    after = dict(result=['return', repr(shared + 'new footer wording')], stdout='', calls=[], args_after='')
    report = dict(flips=0, predicted_failures=[], executed=EXECUTED,
                  side_by_side=dict(compared_calls=1, functions_called=1, targets=1),
                  behaviour_differences=[dict(target='amu.ci.render.render', input='(1,)', differs=['result'],
                                              before=before, after=after)])
    row = next(line for line in render(report, META).splitlines() if line.startswith('- `amu.ci.render.render`'))
    assert 'old footer' in row and 'new footer' in row
    assert row.index('old footer') < row.index('as changed') < row.index('new footer')
    assert '…' in row and len(row) < 700


def test_behaviour_rows_are_collapsed_at_the_end_with_their_count_on_the_first_line():
    # Side-by-side precision was 61% on the brief 5 retrospective's hand-labelled rows, under the 70% bar
    outcome = dict(result=['return', '1'], stdout='', calls=[], args_after='')
    rows = [dict(target=f'lib.py#f{i}', input='(1,)', differs=['result'], before=outcome, after=dict(outcome, result=['return', '2']))
            for i in range(3)]
    report = dict(flips=1, predicted_failures=[flip('tests/t.py::test_a')], gaps=['g'], executed=EXECUTED,
                  side_by_side=dict(compared_calls=3, functions_called=3, targets=3), behaviour_differences=rows)
    body = render(report, META)
    first, rest = body.split('\n', 1)
    assert first == ('**AMU: 1 flip** at `abcdef1`, among 40 tests run; '
                     '3 behaviour differences on captured input, collapsed at the end'), first
    assert '### Behaviour differs' not in body
    opened = rest.index('<details><summary>Behaviour differs on captured input (3)</summary>')
    assert rest.index('### Flips (1)') < rest.index('### Gaps (1)') < rest.index('### What ran') < opened
    assert opened < rest.index('- `lib.py#f0`') < rest.index('- `lib.py#f2`') < rest.index('</details>') < rest.index('<sub>AMU')
    assert render(report, META, quiet=True) == body
    none = dict(report, behaviour_differences=[])
    body = render(none, META)
    assert 'behaviour difference' not in body.splitlines()[0] and '<details>' not in body
    assert body.index('### Behaviour differs on captured input (0)') < body.index('### What ran')
    assert 'behaviour difference' not in render(dict(report, error='no merge base'), META)
