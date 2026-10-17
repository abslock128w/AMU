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
    p.add_argument('--run', action='store_true', help='execute candidate tests before and after the change even for a supplied patch')
    p.add_argument('--static', action='store_true', help='never execute tests; static packet only')
    p.add_argument('--runner', choices=['pytest', 'unittest', 'django-runtests', 'django-manage'], help='test runner; default: detected from the tree')
    p.add_argument('--python', help='interpreter with pytest for --run; default: this one')
    p.add_argument('--budget', type=int, default=60, help='seconds allowed per side for --run')
    p.add_argument('--only', nargs='+', metavar='TEST', help='run exactly these tests (path::Class::test) instead of the ranked candidates')
    p.add_argument('--also', nargs='+', metavar='TEST', help='run these tests (path::Class::test) as well as the ranked candidates, first')
    p.add_argument('--observe', action='store_true', help='run the whole suite once under coverage and store a per-test recording beside the index')
    p.add_argument('--generate-dollars', type=float, default=0.0,
                   help='dollars allowed for model-written probes of changed functions no test calls (0: none)')
    p.add_argument('--generate-minutes', type=float, default=20.0, help='minutes allowed for generating and checking probes')
    p.add_argument('--generate-model', default='claude-opus-5-5', help='Claude model that writes the probes')
    p.add_argument('--observe-calls', action='store_true',
                   help='run the whole suite once with the sys.monitoring call tracer (Python 3.12+) and store per-test call edges')
    a = p.parse_args(argv)
    if a.observe_calls:
        from . import edges
        sys.stdout.write(encoded(edges.record(a.repo, a.python, a.runner)).decode() + '\n')
        return
    if a.observe:
        from . import observe
        sys.stdout.write(encoded(observe.record(a.repo, a.python, a.runner)).decode() + '\n')
        return
    patch = None if a.patch_file is None else sys.stdin.read() if a.patch_file == '-' else Path(a.patch_file).read_text(errors='replace')
    sys.stdout.write(encoded(analyze(a.repo, patch, full_evidence=a.full_evidence, coverage_evidence=a.coverage, run=False if a.static else True if a.run else None, python=a.python, budget=a.budget, include_tests=a.tests, runner=a.runner, only=a.only, also=a.also, generate=(a.generate_dollars, a.generate_minutes, a.generate_model) if a.generate_dollars > 0 else None)).decode() + '\n')


if __name__ == '__main__':
    main()
