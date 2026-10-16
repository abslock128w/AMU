"""Render amu's pull request report as one Markdown comment.

Usage: python -m amu.ci.render DIR    (DIR holds report.json and meta.json; prints the comment)

The first line is the flip count and the head SHA. Then flips, then a runner abort, then gaps, then what ran. The
comment says only what ran: a count of 0 is a count among the tests that ran, never a pass for the suite.

Everything in the report can come from the pull request's own code (test names, error text), so every such string
goes into an inline code span with backticks and line breaks removed and its length cut. That leaves no Markdown,
HTML or @mention in it. The whole comment is cut to fit GitHub's limit.
"""
from __future__ import annotations

import json
import re
import sys
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


def render(report, meta, limit=LIMIT, listed=LISTED, extra='', verify=None):
    head, base = meta.get('head'), meta.get('merge_base') or meta.get('base')
    executed = report.get('executed') or {}
    ran = executed.get('ran') or 0
    flips = report.get('flips')
    failures = [row for row in report.get('predicted_failures') or [] if failure_id(row)]
    aborted = report.get('failure')
    if report.get('error'):
        first = f"**AMU: no flip count** at {short(head)}: {code(report['error'])}"
    elif aborted:
        first = f'**AMU: no flip count** at {short(head)}: the test runner aborts on the head before any test'
    elif flips is None or not ran:
        first = f'**AMU: no flip count** at {short(head)}: no candidate test ran'
    else:
        first = f"**AMU: {plural(flips, 'flip')}** at {short(head)}, among {plural(ran, 'test')} run"
    evidence = [row for row in report.get('change_evidence') or [] if isinstance(row, dict) and row.get('id')]
    if evidence and not report.get('error'):
        first += f"; {plural(len(evidence), 'test')} of the pull request's own fail on the merge base and pass on the head"
    out = [first, '']
    if failures and not report.get('error'):
        out += [f'### Flips ({len(failures)})', '',
                f'Pass on the merge base {short(base)}, fail on the head. Each was rerun on the merge base to confirm '
                'it passed there.', '']

        def flip(row):
            label = row.get('label') if isinstance(row, dict) else None
            return code(failure_id(row)) + (' (fails as the test was before it was edited)' if label == 'test edited, not fixed' else '')
        out += rows(failures, flip, listed) + ['']
        if verify and verify.get('checked'):
            out += [f"Rerun on the merge base in a fresh job: {verify.get('held', 0)} passed again, "
                    f"{verify.get('false', 0)} failed there too, so not the change, "
                    f"{verify.get('other', 0)} could not be rerun.", '']
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
        quiet = sum(row.get('verdict') == 'none' for row in replayed)
        if quiet:
            out += [f"{plural(quiet, 'test')} pass on the merge base too, so they show nothing about the change.", '']
    unstable = report.get('unstable') or []
    if unstable:
        out += [f'Flipped once and not on a rerun, so not counted: {len(unstable)}.', '']
        out += rows(unstable, lambda row: code(failure_id(row)), listed) + ['']
    if aborted:
        out += ['### Runner abort', '',
                f"The test runner ({code(aborted.get('runner') or executed.get('runner') or '?')}) starts on the merge base "
                f"and aborts on the head before running any test: {code(aborted.get('error', ''))}. On the merge base it "
                f"ran {aborted.get('ran_before', '?')} of the same {aborted.get('probed', '?')} candidates.", '']
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
                       'Failures were rerun on the merge base. Tests outside the candidates were not run.')
        else:
            out.append('No test ran, so nothing here says whether the change breaks one.')
        out.append('')
    footer = (f"<sub>AMU {code(meta.get('amu_version') or '?', 40)} from {code(meta.get('amu_ref') or '?', 60)}, "
              f"report {code(report.get('report_hash') or '-', 20)}. Full JSON is in the workflow run's artifact.</sub>")
    body = '\n'.join(out + [footer, extra, MARKER]).replace('\n\n\n', '\n\n')
    if len(body) > limit and listed > 1:
        return render(report, meta, limit, listed // 2, extra, verify)
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
