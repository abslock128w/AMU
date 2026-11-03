"""Record which tests run each line of the project, for the whole suite at the checked-out commit (core.suite).

Run by the scheduled workflow (ci/amu-record.yml), never by the pull request job:

    python -m amu.ci.record_suite --repo . --shard 0/4 --out shard-0.json.gz
    python -m amu.ci.record_suite --repo . --merge shard-*.json.gz --out .amu/suite/<sha>.json.gz

Prints one JSON line: commit, tests seen, planned, ran, seconds, complete, bytes written.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..core import suite


def partial(out):
    """Where a shard writes before its final name is known: beside it, one per output, so shards can share a folder."""
    return Path(out).with_name('.' + Path(out).name.replace('{commit}', 'commit') + '.partial')


def main(argv=None):
    p = argparse.ArgumentParser(prog='python -m amu.ci.record_suite')
    p.add_argument('--repo', default='.')
    p.add_argument('--out', required=True, help='file to write (gzipped JSON); {commit} is replaced by the commit')
    p.add_argument('--shard', help='i/n: run every n-th test starting at the i-th (0-based)')
    p.add_argument('--python', help='interpreter the tests run with (default: the project\'s)')
    p.add_argument('--runner', help='pytest, unittest, django-runtests or django-manage; default: detected')
    p.add_argument('--budget', type=float, help='seconds of test time at most (default: none)')
    p.add_argument('--merge', nargs='+', help='shard recordings of one commit to merge instead of running')
    a = p.parse_args(argv)
    if a.merge:
        data = suite.merge([suite.read(path) for path in sorted(a.merge)])
    else:
        shard = None
        if a.shard:
            number, count = (int(x) for x in a.shard.split('/'))
            shard = (number, count)
        python = str(Path(a.python).absolute()) if a.python and '/' in a.python else a.python  # the runner may change folder
        data = suite.record(a.repo, partial(a.out), python=python, runner=a.runner,
                            shard=shard, budget=a.budget)
    out = Path(a.out.replace('{commit}', data.get('commit') or 'unknown'))
    suite.write(data, out)
    partial(a.out).unlink(missing_ok=True)
    print(json.dumps(dict(commit=data.get('commit'), tests=len(data['tests']), planned=data.get('planned'),
                          ran=data.get('ran'), seconds=data.get('seconds'), complete=data.get('complete'),
                          bytes=out.stat().st_size, out=str(out))))
    return 0


if __name__ == '__main__':
    sys.exit(main())
