"""Render amu's pull request report as one Markdown comment.

Usage: python -m amu.ci.render DIR    (DIR holds report.json and meta.json; prints the comment)

The first line is the flip count and the head SHA. Then flips, then a runner abort, then gaps, then what ran. The
comment says only what ran: a count of 0 is a count among the tests that ran, never a pass for the suite. A run the
wall cap stopped says so on the first line, and its first gap lists what was not done.

Side-by-side's behaviour differences come last, collapsed, with their count on the first line. On the 18 rows
hand-labelled in the brief 5 retrospective 11 were real (61%, under its 70% bar), so they stay collapsed until the
false rows are fixed.

Everything in the report can come from the pull request's own code (test names, error text), so every such string
goes into an inline code span with backticks and line breaks removed and its length cut. That leaves no Markdown,
HTML or @mention in it. The whole comment is cut to fit GitHub's limit.
"""
from __future__ import annotations

import json
import re
import sys
import textwrap
from pathlib import Path

MARKER = '<!-- amu-ci-comment v1 -->'
LIMIT = 60_000      # GitHub rejects comment bodies over 65,536 characters
FIELD = 300         # characters kept from any one untrusted string
LISTED = 50         # rows shown per list before "and N more"


def code(text, limit=FIELD):
    text = re.sub(r'\s+', ' ', str(text)).replace('`', "'").strip()
    if len(text) > limit:
        text = text[:limit - 1] + '…'
    return f'`{text}`' if text else '`(empty)`'


def short(sha):
    return code((sha or 'unknown')[:7])


def failure_id(row):
    return row.get('id', '?') if isinstance(row, dict) else row


def plural(count, word):
    return f"{count} {word}{'' if count == 1 else 's'}"


def rows(items, render, limit=LISTED):
    lines = [f'- {render(item)}' for item in items[:limit]]
    if len(items) > limit:
        lines.append(f'- and {len(items) - limit} more in the artifact')
    return lines


EDITED = 'test edited, not fixed'
CUT = 'side-by-side run stopped at its time limit'


def is_test_file(path):
    """The rule amu.core.replay uses: under a tests directory, test_*.py, *_test.py, tests.py or conftest.py."""
    parts = str(path).split('/')
    name = parts[-1]
    return name.endswith('.py') and ('tests' in parts[:-1] or name.startswith('test_') or name.endswith('_test.py')
                                     or name in ('tests.py', 'conftest.py'))


def split_flips(report):
    """(flips, tests the pull request updated): a test the pull request edited, failing only as it was before the edit,
    is not a flip. Reports from before 0.4.0 keep those among the flips, labelled; newer ones list them apart."""
    failures = [row for row in report.get('predicted_failures') or [] if failure_id(row)]
    updated = [row for row in failures if isinstance(row, dict) and row.get('label') == EDITED]
    updated += [row for row in report.get('updated_tests') or [] if isinstance(row, dict) and row.get('id')]
    return [row for row in failures if not (isinstance(row, dict) and row.get('label') == EDITED)], updated


def reach(report):
    """Whether something ran the changed code: True, False (side-by-side watched every comparable changed function
    and no test called one), or None (not seen). With False, the production files those functions are in."""
    counted, updated = split_flips(report)
    side = report.get('side_by_side') if isinstance(report.get('side_by_side'), dict) else None
    if counted or updated or report.get('change_evidence') or (side and side.get('functions_called')):
        return True, []
    if any(isinstance(row, dict) and row.get('expect') == 'ran_changed_lines' for row in report.get('tests') or []):
        return True, []
    files = sorted({row.get('file') for row in report.get('changed_functions') or []
                    if isinstance(row, dict) and isinstance(row.get('file'), str) and not is_test_file(row['file'])})
    if side and side.get('targets') and not side.get('functions_called') and files:
        return False, files
    return None, []


def stopped(report):
    side = report.get('side_by_side') if isinstance(report.get('side_by_side'), dict) else {}
    return bool(side.get('stopped')) or any(str(gap).startswith(CUT) for gap in report.get('gaps') or [])


def own_tests(report):
    """Tests the pull request adds or edits that ran on the head: replayed and not skipped there, or, with nothing
    replayed, changed functions in test files."""
    replayed = [row for row in report.get('replayed') or [] if isinstance(row, dict)]
    if replayed:
        return sum(row.get('head') != 'skipped' for row in replayed)
    return sum(isinstance(row, dict) and is_test_file(str(row.get('file', ''))) for row in report.get('changed_functions') or [])


def quiet_body(report, meta, first, extra):
    """Nothing found: the first line, and what ran and what was not seen, collapsed."""
    full = render(report, meta, extra='', quiet=False).split('\n', 1)[1].rsplit(MARKER, 1)[0].strip()
    return '\n'.join([first, '', '<details><summary>What ran and what was not seen</summary>', '', full, '', '</details>',
                      extra, MARKER]).replace('\n\n\n', '\n\n')


def extras(report, listed=LISTED):
    """Sections for features still behind a flag: renames and moves, and inputs on which two versions differ."""
    out = []
    moved = [row for row in report.get('refactors') or [] if isinstance(row, dict)]
    if moved and not report.get('error'):
        out += [f'### Renamed or moved, nothing else ({len(moved)})', '',
                'The same code under a new name or in a new place: refactor, behaviour preserved if tests pass.', '']
        out += rows(moved, lambda row: f"{code(row.get('from', '?'), 120)} -> {code(row.get('to', '?'), 120)} ({row.get('kind', '?')})",
                    listed) + ['']
    differ = [row for row in (report.get('equivalence') or {}).get('rows') or [] if isinstance(row, dict)]
    if differ and not report.get('error'):
        out += [f'### An input on which the two versions differ ({len(differ)})', '',
                'Found by a search over the inputs of annotated, pure changed functions (CrossHair, Hypothesis):', '']
        out += rows(differ, lambda row: f"{code(row.get('target', '?'), 120)}: {code(row.get('input', ''), 200)} gives "
                                        f"{code(row.get('before', ''), 160)} and {code(row.get('after', ''), 160)} "
                                        f"({row.get('tool', '?')})", listed) + ['']
    return out


def nothing_found(report):
    executed = report.get('executed') or {}
    counted, _ = split_flips(report)
    return (not report.get('error') and not report.get('failure') and report.get('flips') is not None and not counted
            and executed.get('ran') and reach(report)[0] is not False
            and not any(report.get(k) for k in ('change_evidence', 'behaviour_differences', 'generated_differences', 'unstable', 'cut',
                                                'refactors'))
            and not (report.get('equivalence') or {}).get('rows') and not (report.get('mutation') or {}).get('survivors')
            and not any(isinstance(r, dict) and not str(r.get('label', '')).startswith('fails') for r in report.get('flip_trust') or []))


def render(report, meta, limit=LIMIT, listed=LISTED, extra='', verify=None, quiet=False):
    if quiet and nothing_found(report):
        body = render(report, meta, limit, listed, '', verify)
        return quiet_body(report, meta, body.splitlines()[0], extra)
    head, base = meta.get('head'), meta.get('merge_base') or meta.get('base')
    executed = report.get('executed') or {}
    ran = executed.get('ran') or 0
    flips = report.get('flips')
    failures, updated = split_flips(report)
    aborted = report.get('failure')
    if report.get('error'):
        first = f"**AMU: no flip count** at {short(head)}: {code(report['error'])}"
    elif report.get('refactor_only'):
        moved = [row for row in report.get('refactors') or [] if isinstance(row, dict)]
        first = (f"**AMU: refactor only** at {short(head)}: every change renames or moves "
                 f"{plural(len(moved), 'definition')} without changing it; no tests run")
    elif aborted:
        first = f'**AMU: no flip count** at {short(head)}: the test runner aborts on the head before any test'
    elif flips is None or not ran:
        first = f'**AMU: no flip count** at {short(head)}: no candidate test ran'
    elif failures:
        first = f"**AMU: {plural(len(failures), 'flip')}** at {short(head)}, among {plural(ran, 'test')} run"
    else:
        reached, files = reach(report)
        if reached is False:
            # Never folded away: a reviewer reading only this line must not take it for "nothing changed".
            mine = own_tests(report)
            where = ', '.join(code(f, 120) for f in files[:3]) + (f' and {len(files) - 3} more' if len(files) > 3 else '')
            besides = f", apart from the {plural(mine, 'test')} this pull request adds or edits" if mine else ''
            timed = ' before the side-by-side time limit' if stopped(report) else ''
            first = f"**AMU: no test ran the changed code in {where}** at {short(head)}{besides}; {plural(ran, 'test')} run{timed}"
        elif reached:
            first = f"**AMU: 0 flips** at {short(head)}, among {plural(ran, 'test')} run, some of them on the changed code"
        else:
            first = (f"**AMU: no flip** at {short(head)} among {plural(ran, 'test')} run; whether any ran the changed code "
                     'was not seen')
    if report.get('cut') and not report.get('error'):
        cap = meta.get('wall_cap')
        cap = f' of {cap} s' if isinstance(cap, int) and not isinstance(cap, bool) and 0 < cap < 10**7 else ''
        first += f'; stopped at the wall cap{cap} before it finished'
    evidence = [row for row in report.get('change_evidence') or [] if isinstance(row, dict) and row.get('id')]
    if evidence and not report.get('error'):
        first += f"; {plural(len(evidence), 'test')} of the pull request's own fail on the merge base and pass on the head"
    behaviour = [row for row in report.get('behaviour_differences') or [] if isinstance(row, dict)]
    side = report.get('side_by_side') if isinstance(report.get('side_by_side'), dict) else None
    if behaviour and side is not None and not report.get('error'):
        first += f"; {plural(len(behaviour), 'behaviour difference')} on captured input, collapsed at the end"
    out, collapsed = [first, ''], []
    if failures and not report.get('error'):
        out += [f'### Flips ({len(failures)})', '',
                f'Pass on the merge base {short(base)}, fail on the head. Each was rerun on the merge base to confirm '
                'it passed there, and failed again on the head in every rerun made: in the same process, alone in a fresh '
                'process, and after the tests that ran before it, unless its row says it is order-dependent. A rerun that was '
                'not made is named on its row.', '']
        missing = {row.get('id'): [leg for leg, state in (row.get('reruns') or {}).items() if state is None]
                   for row in report.get('flip_trust') or [] if isinstance(row, dict)}
        leaks = {row.get('id'): row.get('why', '') for row in report.get('flip_trust') or [] if isinstance(row, dict)
                 and row.get('label') == 'fails: order-dependent, new with this change'}
        routes = {row.get('id'): row for row in report.get('flip_trust') or [] if isinstance(row, dict)}

        def short_id(ident):
            path, _, qual = str(ident).partition('#')
            return (qual or path.rsplit('/', 1)[-1]) if qual else path.rsplit('/', 1)[-1]

        def route(ident):
            row = routes.get(ident) or {}
            parts = []
            if isinstance(row.get('path'), list) and row['path']:
                parts.append('path ' + code(' > '.join(short_id(step) for step in row['path'][-6:]), 200))
            raised = row.get('raised') if isinstance(row.get('raised'), dict) else None
            if raised and isinstance(raised.get('through'), list):
                parts.append(code(raised.get('exception', '?'), 60) + ' raised through '
                             + code(' > '.join(short_id(step) for step in raised['through'][:6]), 200))
            found = row.get('isolated') if isinstance(row.get('isolated'), dict) else None
            if found and isinstance(found.get('hunks'), list) and found['hunks']:
                places = ', '.join(f"hunk {h.get('hunk', '?')} of {h.get('of', '?')} in {code(h.get('file', '?'), 120)} "
                                   f"at line {h.get('start', '?')}" for h in found['hunks'][:3] if isinstance(h, dict))
                parts.append(('caused by ' if found.get('finished') else 'narrowed to, search cut short: ') + places)
            return ('; ' + '; '.join(parts)) if parts else ''
        names = dict(in_process='in the same process', fresh_process='alone in a fresh process', after_set='after the tests before it')

        def flip(row):
            label = row.get('label') if isinstance(row, dict) else None
            gone = [names.get(leg, str(leg)) for leg in missing.get(failure_id(row), [])]
            return (code(failure_id(row)) + (' (fails as the test was before it was edited)' if label == EDITED else '')
                    + (f" (order-dependent, new with this change: {code(leaks[failure_id(row)], 200)})" if failure_id(row) in leaks else '')
                    + route(failure_id(row))
                    + (f" (not rerun {', '.join(gone)})" if gone else ''))
        out += rows(failures, flip, listed) + ['']
        if verify and verify.get('checked'):
            out += [f"Rerun on the merge base in a fresh job: {verify.get('held', 0)} passed again, "
                    f"{verify.get('false', 0)} failed there too, so not the change, "
                    f"{verify.get('other', 0)} could not be rerun.", '']
    if updated and not report.get('error'):
        out += [f'### Tests this pull request updated ({len(updated)})', '',
                'Pass on the merge base and fail on the head as they were before this pull request edited them; as edited, '
                'they pass. The edit is in the diff, so they are not flips.', '']
        out += rows(updated, lambda row: code(failure_id(row)), listed) + ['']
    if side is not None and not report.get('error'):
        section = [f"Each changed function the tests called on the merge base ran there twice as it was and twice as changed, "
                   f"on copies of the same arguments: {side.get('compared_calls', 0)} call(s) of "
                   f"{side.get('functions_called', 0)} of {side.get('targets', 0)} comparable changed function(s). Only "
                   'differences both versions repeat are listed.', '']

        def flat(side_result, field):
            value = side_result.get(field) if isinstance(side_result, dict) else None
            text = '?' if value is None else value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            return re.sub(r'\s+', ' ', text).replace('`', "'").strip()

        def difference(row):
            differs = [str(f) for f in row.get('differs') or []]
            # Show the first field that differs, cut to a window around where the two versions part: outcomes that
            # share a long prefix would otherwise both be cut before the part that differs.
            field = next((f for f in differs if f in ('result', 'args_after', 'stdout', 'calls')), 'result')
            was, now = flat(row.get('before'), field), flat(row.get('after'), field)
            at = next((i for i, (a, b) in enumerate(zip(was, now)) if a != b), min(len(was), len(now)))
            start = max(0, at - 40)

            def window(text):
                return ('…' if start else '') + text[start:start + 160] + ('…' if len(text) > start + 160 else '')
            where = f", from character {at + 1}" if start else ''
            return (f"{code(row.get('target', '?'), 120)} on {code(row.get('input', ''), 160)}: as it was {code(window(was), 170)}, "
                    f"as changed {code(window(now), 170)} (differs in: {code(', '.join(differs), 60)}; shown: {field}{where})")
        section += rows(behaviour, difference, listed) + ['']
        if behaviour:
            collapsed = [f'<details><summary>Behaviour differs on captured input ({len(behaviour)})</summary>', '',
                         "Collapsed until side-by-side's false rows are fixed: of 18 such rows hand-labelled on Django and "
                         'sympy pull requests, 11 were real differences and 7 came from how side-by-side runs the changed code.',
                         '', *section, '</details>', '']
        else:
            out += ['### Behaviour differs on captured input (0)', '', *section]
    made = report.get('generated') if isinstance(report.get('generated'), dict) else None
    produced = [row for row in report.get('generated_differences') or [] if isinstance(row, dict)]
    if made is not None and not report.get('error') and made.get('proposed'):
        out += [f"### Generated tests showing a difference ({len(produced)})", '']
        spent = (f"through {code(str(made.get('backend')), 20)}, at a cost of Claude plan usage, within "
                 f"{float(made.get('minutes_cap') or 0):g} min" if made.get('cost_basis') == 'plan usage' else
                 f"at ${float(made.get('cost') or 0):.3f} of a ${float(made.get('dollars_cap') or 0):.2f} cap")
        out += [f"For changed functions no test called, {code(made.get('model', '?'), 40)} proposed "
                f"{made.get('proposed', 0)} probe(s) for {made.get('functions', 0)} function(s), {spent}. Each was kept only if it "
                f"compiled ({made.get('compiled', 0)}), ran on the head ({made.get('ran', 0)}), gave the same outcome five times "
                f"there ({made.get('stable', 0)}), ran a changed line ({made.get('reached', 0)}), and gave one stable, different "
                f"outcome on the merge base ({made.get('accepted', 0)}). The outcomes come from running the code, not from the model.", '']

        def shown(row):
            before, after = row.get('before') or ['?', '?', '?'], row.get('after') or ['?', '?', '?']
            text = (f"{code(row.get('target', '?'), 120)}: {code(row.get('call', ''), 160)} gives "
                    f"{code(' '.join(str(x) for x in before[:1] + before[2:3]), 160)} on the merge base and "
                    f"{code(' '.join(str(x) for x in after[:1] + after[2:3]), 160)} on the head")
            body = str(row.get('test', ''))[:1500].replace('`', "'")
            return text + ('\n  <details><summary>generated test</summary>\n\n  ```python\n'
                           + textwrap.indent(body, '  ') + '  ```\n  </details>' if body else '')
        out += rows(produced, shown, listed) + ['']
    replayed = [row for row in report.get('replayed') or [] if isinstance(row, dict)]
    if replayed and not report.get('error'):
        out += [f"### Change evidence: the pull request's own tests ({len(evidence)})", '',
                f"The {plural(len(replayed), 'test')} this pull request adds or edits ran on the head and on the merge base "
                f"{short(base)} with the tests as the pull request leaves them.", '']
        if evidence:
            out += ['Fail on the merge base and pass on the head, so the change does what they check. Each failure on the '
                    'merge base repeated in a fresh process.', '']

            def shown(row):
                return code(row['id']) + (' (does not load on the merge base)' if row.get('before') == 'unloadable' else '')
            out += rows(evidence, shown, listed) + ['']
        made_for = [row for row in replayed if row.get('verdict') == 'new_code' and row.get('id')]
        if made_for:
            out += [f'### New tests for new code ({len(made_for)})', '',
                    'Fail on the merge base only because what they use is not there yet (an ImportError, AttributeError or '
                    'NameError on a name this pull request adds), and pass on the head. They show the new code exists, not '
                    'what it does, so they are not counted as change evidence.', '']
            out += rows(made_for, lambda row: code(row['id']) + (f" ({code(row['why'], 120)})" if row.get('why') else ''),
                        listed) + ['']
        unread = sum(row.get('verdict') == 'unloadable' for row in replayed)
        if unread:
            out += [f"{plural(unread, 'test')} do not load on the merge base for a reason AMU did not read, and pass on the "
                    'head: not counted as change evidence.', '']
        # Verdict 'none' covers a test that passed on both sides and one that was skipped on either: only the first passed.
        quiet = [row for row in replayed if row.get('verdict') == 'none']
        skipped_both = sum(row.get('head') == 'skipped' and row.get('base') == 'skipped' for row in quiet)
        skipped_one = sum((row.get('head') == 'skipped') != (row.get('base') == 'skipped') for row in quiet)
        passed = len(quiet) - skipped_both - skipped_one
        if passed:
            out += [f"{plural(passed, 'test')} pass on the merge base too, so they show nothing about the change.", '']
        if skipped_both:
            out += [f"{plural(skipped_both, 'test')} skipped on both sides, so they show nothing about the change: "
                    'they did not run here.', '']
        if skipped_one:
            out += [f"{plural(skipped_one, 'test')} skipped on one side only, so they show nothing about the change.", '']
    labelled = [row for row in report.get('flip_trust') or [] if isinstance(row, dict) and row.get('id')
                and not str(row.get('label', '')).startswith('fails')]
    unstable = report.get('unstable') or []
    if labelled and not report.get('error'):
        out += [f'### Flips not counted ({len(labelled)})', '',
                'Pass on the merge base and fail on the head, but did not pass the checks a flip needs to count:', '']
        out += rows(labelled, lambda row: f"{code(row['id'])}: {code(row.get('label', '?'), 40)}, {code(row.get('why', ''), 200)}", listed) + ['']
    elif unstable:
        out += [f'Flipped once and not on a rerun, so not counted: {len(unstable)}.', '']
        out += rows(unstable, lambda row: code(failure_id(row)), listed) + ['']
    if aborted:
        out += ['### Runner abort', '',
                f"The test runner ({code(aborted.get('runner') or executed.get('runner') or '?')}) starts on the merge base "
                f"and aborts on the head before running any test: {code(aborted.get('error', ''))}. On the merge base it "
                f"ran {aborted.get('ran_before', '?')} of the same {aborted.get('probed', '?')} candidates.", '']
    out += extras(report, listed)
    if report.get('refactor_only') and not report.get('error'):
        out += ['Read from the syntax of the change: with the old names spelled the new way, the code before and after is '
                'the same. Callers outside this repository, and names looked up from strings, are not checked.', '']
    found = report.get('mutation') or {}
    survivors = [row for row in found.get('survivors') or [] if isinstance(row, dict)]
    if survivors and not report.get('error'):
        collapsed += [f'<details><summary>Tests that would not notice ({len(survivors)})</summary>', '',
                      f"Mutants on changed lines the tests run, {found.get('mutants', 0)} in all, {found.get('killed', 0)} caught: "
                      f"each of these was run by the {found.get('tests', '?')} best-ranked passing candidates, and every one "
                      'passed. A change there could go unnoticed. (Behind a flag; an equivalent mutant also lands here.)', '',
                      *rows(survivors, lambda row: f"{code(str(row.get('file')) + ':' + str(row.get('line')), 120)}: "
                                                   f"{code(row.get('mutant', ''), 160)}", listed), '', '</details>', '']
    gaps = list(report.get('gaps') or [])
    if meta.get('install_ok') is False:
        gaps.insert(0, "the project's install command failed; see install.log in the artifact")
    if gaps:
        out += [f'### Gaps ({len(gaps)})', '', 'Not run or not seen:', '']
        out += rows(gaps, code, listed) + ['']
    if not report.get('error'):
        out += ['### What ran', '']
        if ran:
            out.append(f"{executed.get('ran')} of {executed.get('candidates', '?')} candidate tests on the head, "
                       f"{executed.get('seconds', '?')} s with {code(executed.get('runner') or report.get('runner') or '?')}"
                       f"{', budget ' + str(executed['budget']) + ' s per side' if executed.get('budget') else ''}. "
                       'Failures were rerun on the merge base. Tests outside the candidates were not run.'
                       + (f" Order: {code(report['order'], 120)}." if report.get('order') else ''))
            cached = executed.get('base_cache') or {}
            if cached.get('answered') and not cached.get('ran'):
                out.append(f"The merge base side was not run: all {cached['answered']} outcomes it needed are passes "
                           'cached from an earlier run, keyed on the content of each test file and what it imports, the '
                           'interpreter, the lock files and the runner arguments.')
            elif cached.get('answered'):
                out.append(f"On the merge base, {cached['answered']} outcomes came from the cache of earlier passes and "
                           f"{cached['ran']} ran.")
        elif report.get('refactor_only'):
            out.append('No test ran: the change only renames or moves code.')
        else:
            out.append('No test ran, so nothing here says whether the change breaks one.')
        out.append('')
    out += collapsed
    footer =(f"<sub>AMU {code(meta.get('amu_version') or '?', 40)} from {code(meta.get('amu_ref') or '?', 60)}, "
              f"report {code(report.get('report_hash') or '-', 20)}. Full JSON is in the workflow run's artifact.</sub>")
    body = '\n'.join(out + [footer, extra, MARKER]).replace('\n\n\n', '\n\n')
    if len(body) > limit and listed > 1:
        return render(report, meta, limit, listed // 2, extra, verify, quiet)
    if len(body) > limit:
        body = first + '\n\nThe report is too long for a comment; see the artifact.\n\n' + MARKER
    return body


def load(folder):
    folder = Path(folder)

    def read(name):
        try:
            value = json.loads((folder/name).read_text())
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}
    report = read('report.json') or dict(error='no report.json in the artifact')
    return report, read('meta.json')


def load_verify(folder):
    """The fresh-job rerun's counts, as integers only; None when there are none."""
    try:
        value = json.loads((Path(folder)/'verify.json').read_text())
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(value, dict):
        return None
    counts = {key: value.get(key) for key in ('checked', 'held', 'false', 'other')}
    if not all(isinstance(n, int) and not isinstance(n, bool) and 0 <= n < 10**7 for n in counts.values()):
        return None
    return counts


if __name__ == '__main__':
    print(render(*load(sys.argv[1])))
