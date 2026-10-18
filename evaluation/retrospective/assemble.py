"""Write the per-pull-request deliverables (brief 5, piece 2) into this folder from the scratch root's results.

Usage: python -P evaluation/retrospective/assemble.py --root ROOT

For each frozen pull request:
- ``<repo>/<n>.md``: the comment AMU would have posted (rendered as the posting job renders it). When this session's
  probes ran, the comment is rendered again with them in the report, so they appear under the automated tier's own
  heading ("Generated tests showing a difference").
- ``<repo>/<n>.json``: report, meta, verify, timing, the probe targets and every probe attempt with its filter
  counts. The report's ``impacted`` list (static reach, up to 2 MB per pull request) is replaced by its length; the
  untouched reports are in ``raw-reports.tar.xz``.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from freeze import check  # noqa: E402
from probe import append_to_comment  # noqa: E402


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--root', required=True)
    a = p.parse_args(argv)
    root = Path(a.root)
    for name, prs in check()['prs'].items():
        (HERE/name).mkdir(exist_ok=True)
        for pr in prs:
            out = root/'out'/name/str(pr['number'])
            if (out/'probes.json').exists():
                comment = append_to_comment(root, name, pr['number'])
            else:
                comment = (out/'comment.md').read_text()
            (HERE/name/f"{pr['number']}.md").write_text(comment)
            report = read(out/'report.json', {})
            if isinstance(report.get('impacted'), list):
                report['impacted'] = dict(omitted='static reach list; the full report is in raw-reports.tar.xz',
                                          count=len(report['impacted']))
            doc = dict(pr=dict(repo=name, **{k: pr[k] for k in ('number', 'title', 'merged_at', 'base_sha', 'head_sha', 'merge_base')}),
                       report=report, meta=read(out/'meta.json'), verify=read(out/'verify'/'verify.json'),
                       timing=read(out/'timing.json'), probe_targets=read(out/'probe-targets.json'),
                       probes=read(out/'probes.json'), probe_minutes=read(out/'probe-minutes.json'))
            (HERE/name/f"{pr['number']}.json").write_text(json.dumps(doc, indent=1, sort_keys=True) + '\n')
    shutil.copy(root/'raw-reports.tar.xz', HERE/'raw-reports.tar.xz')


if __name__ == '__main__':
    main()
