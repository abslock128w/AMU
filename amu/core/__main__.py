"""python -m amu.core --repo . [--patch-file diff|-] [--coverage FILE] [--full-evidence]"""
import argparse
import sys
from pathlib import Path

from . import analyze, encoded


def main(argv=None):
    p = argparse.ArgumentParser(prog='amu before-tests', description=__doc__)
    p.add_argument('--repo', default='.')
    p.add_argument('--patch-file', help="unified diff, or - for stdin; default: uncommitted changes")
    p.add_argument('--coverage', help='per-test coverage recording; default: auto-detect')
    p.add_argument('--full-evidence', action='store_true')
    p.add_argument('--tests', action='store_true', help='also list tests that only reach the change')
    p.add_argument('--run', action='store_true', help='execute the candidate tests before and after the change and report which flip')
    p.add_argument('--runner', choices=['pytest', 'unittest', 'django-runtests', 'django-manage'], help='test runner; default: detected from the tree')
    p.add_argument('--python', help='interpreter with pytest for --run; default: this one')
    p.add_argument('--budget', type=int, default=60, help='seconds allowed per side for --run')
    a = p.parse_args(argv)
    patch = None if a.patch_file is None else sys.stdin.read() if a.patch_file == '-' else Path(a.patch_file).read_text(errors='replace')
    sys.stdout.write(encoded(analyze(a.repo, patch, full_evidence=a.full_evidence, coverage_evidence=a.coverage, run=a.run, python=a.python, budget=a.budget, include_tests=a.tests, runner=a.runner)).decode() + '\n')


if __name__ == '__main__':
    main()
